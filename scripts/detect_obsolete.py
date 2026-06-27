#!/usr/bin/env python3
"""Detect obsolete OCMUI Jira tickets by cross-referencing against the codebase.

Extracts UI element references (routes, component names, file paths) from ticket
descriptions and checks whether they still exist in the codebase.

Score breakdown:
- +40: ticket references a route or file that no longer exists in the repo
- +20: ticket references a component name not found in src/
- +15: ticket is 18+ months old
- +15: no activity in 12+ months
- +10: reporter no longer on team (combined with age)
"""

import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
TICKETS_FILE = BASE_DIR / "tickets.json"
OUTPUT_FILE = BASE_DIR / "obsolete_candidates.json"
MEMBERS_FILE = Path("/Users/dtaylor/repos/work/ocmui-team-dashboard/data/members.json")
REPO_ROOT = Path("/Users/dtaylor/repos/uhc-portal")

# Thresholds
AGE_MONTHS_THRESHOLD = 18
INACTIVITY_MONTHS_THRESHOLD = 12


def load_tickets():
    with open(TICKETS_FILE) as f:
        data = json.load(f)
    return data["tickets"]


def load_team_members():
    with open(MEMBERS_FILE) as f:
        return json.load(f)


def get_codebase_files():
    """Get all tracked files in the repo via git ls-files."""
    result = subprocess.run(
        ["git", "ls-files"],
        capture_output=True, text=True, cwd=REPO_ROOT
    )
    return set(result.stdout.strip().split("\n"))


def get_src_components():
    """Get component directories and file basenames from src/."""
    result = subprocess.run(
        ["git", "ls-files", "src/"],
        capture_output=True, text=True, cwd=REPO_ROOT
    )
    files = result.stdout.strip().split("\n")

    components = set()
    directories = set()
    for f in files:
        parts = Path(f).parts
        basename = Path(f).stem
        components.add(basename.lower())
        for part in parts:
            directories.add(part.lower())

    return components, directories, set(files)


def get_routes():
    """Extract route definitions from the codebase."""
    routes = set()

    # Pattern 1: JSX path/to attributes (path="/create", to="/details/:id")
    result = subprocess.run(
        ["rg", "--no-heading", "-oP", r"(?:path|to)=['\"]([^'\"]+)['\"]", "src/"],
        capture_output=True, text=True, cwd=REPO_ROOT
    )
    for line in result.stdout.strip().split("\n"):
        match = re.search(r"['\"]([/][^'\"]+)['\"]", line)
        if match:
            routes.add(match.group(1))

    # Pattern 2: Route path constants (export const FOO_PATH = '/foo')
    result2 = subprocess.run(
        ["rg", "--no-heading", "-oP", r"(?:PATH|_PATH|_ROUTE)\s*=\s*['\"]([^'\"]+)['\"]", "src/"],
        capture_output=True, text=True, cwd=REPO_ROOT
    )
    for line in result2.stdout.strip().split("\n"):
        match = re.search(r"['\"]([/][^'\"]+)['\"]", line)
        if match:
            routes.add(match.group(1))

    return routes


def normalize_file_reference(filepath):
    """Strip GitHub blob URL prefixes to get the repo-relative path.
    
    Handles patterns like:
      /blob/master/src/components/Foo.tsx -> src/components/Foo.tsx
      portal/blob/<sha>/src/components/Foo.tsx -> src/components/Foo.tsx
      chrome/blob/master/src/... -> src/...  (external repo, keep as-is for miss)
    """
    # Strip leading slash
    path = filepath.lstrip("/")
    # Remove blob/<branch-or-sha>/ prefix (with optional repo name before it)
    match = re.match(r'(?:[\w-]+/)?blob/[^/]+/(.*)', path)
    if match:
        return match.group(1)
    return path


def extract_references(description):
    """Extract potential UI element references from a ticket description."""
    refs = {
        "routes": [],
        "components": [],
        "files": [],
        "ui_elements": [],
    }

    if not description:
        return refs

    text = description.lower()

    # Extract /openshift/... routes and normalize by stripping the /openshift prefix
    # Only match /openshift followed by / (app route prefix), not /openshift-v4 etc.
    route_patterns = re.findall(r'/openshift/[\w/:-]*', description, re.IGNORECASE)
    normalized_routes = []
    for route in route_patterns:
        relative = re.sub(r'^/openshift', '', route)
        if relative and relative != '/':
            normalized_routes.append(relative)
    refs["routes"] = list(set(normalized_routes))

    # Extract file references — filter out non-source noise
    file_patterns = re.findall(
        r'[\w/.@-]+\.(?:tsx?|jsx?|css|scss)',
        description, re.IGNORECASE
    )
    cleaned_files = []
    for fp in file_patterns:
        normalized = normalize_file_reference(fp)
        # Skip obviously non-repo files (bundled hashes, node_modules, external)
        if re.match(r'^[a-f0-9]{10,}\.js$', normalized):
            continue
        if 'node_modules' in normalized or 'sentry/' in normalized:
            continue
        # Skip bare filenames without a path that are too generic (e.g. "Node.js", "package.js")
        if '/' not in normalized and not normalized.startswith('src'):
            continue
        cleaned_files.append(normalized)
    refs["files"] = list(set(cleaned_files))

    component_patterns = re.findall(
        r'\b([A-Z][a-zA-Z]+(?:Screen|Page|Modal|Drawer|Wizard|Form|Panel|Tab|Step|Dialog|View))\b',
        description
    )
    refs["components"] = list(set(component_patterns))

    ui_element_patterns = re.findall(
        r'\b(?:wizard step|form field|tab|modal|drawer|page|screen)\s+["\']?(\w+)["\']?',
        text
    )
    refs["ui_elements"] = list(set(ui_element_patterns))

    return refs


def route_matches(ticket_route, codebase_route):
    """Check if a ticket's route reference matches a codebase route definition.
    
    Handles parameterized routes like /details/:id matching /details/00000000.
    """
    # Direct substring match (either direction)
    if ticket_route in codebase_route or codebase_route in ticket_route:
        return True
    # Compare the static prefix of the codebase route (up to first :param)
    # against the ticket route
    parts = codebase_route.split("/")
    static_parts = []
    for part in parts:
        if part.startswith(":") or part == "*":
            break
        static_parts.append(part)
    if len(static_parts) > 1:
        static_prefix = "/".join(static_parts)
        if ticket_route.startswith(static_prefix):
            return True
    return False


def check_references_against_codebase(refs, src_components, src_dirs, src_files, routes):
    """Check if extracted references still exist in the codebase."""
    findings = []

    for route in refs["routes"]:
        route_normalized = route.rstrip("/")
        found = any(route_matches(route_normalized, r) for r in routes)
        if not found:
            findings.append({
                "type": "route",
                "reference": route,
                "status": "not_found",
            })

    for filepath in refs["files"]:
        normalized = filepath.lower()
        # Check if the file path (or its basename) matches any tracked file
        basename = Path(filepath).name.lower()
        found = (
            any(normalized in f.lower() for f in src_files) or
            any(f.lower().endswith(normalized) for f in src_files) or
            any(basename == Path(f).name.lower() for f in src_files)
        )
        if not found:
            findings.append({
                "type": "file",
                "reference": filepath,
                "status": "not_found",
            })

    for component in refs["components"]:
        comp_lower = component.lower()
        found = comp_lower in src_components or comp_lower in src_dirs
        if not found:
            findings.append({
                "type": "component",
                "reference": component,
                "status": "not_found",
            })

    return findings


def compute_age_months(date_str):
    """Compute months since a date string."""
    if not date_str:
        return 0
    try:
        dt = datetime.fromisoformat(date_str.replace("Z", "+00:00"))
        now = datetime.now(timezone.utc)
        delta = now - dt
        return delta.days / 30.44
    except (ValueError, TypeError):
        return 0


def main():
    print("Loading tickets...")
    tickets = load_tickets()
    print(f"  {len(tickets)} tickets loaded")

    print("\nLoading team members...")
    members = load_team_members()
    member_emails = {m["jira"] for m in members}
    print(f"  {len(member_emails)} team member emails loaded")

    print("\nScanning codebase...")
    src_components, src_dirs, _ = get_src_components()
    all_files = get_codebase_files()
    routes = get_routes()
    print(f"  {len(all_files)} tracked files, {len(src_components)} component names, {len(routes)} routes")

    print("\nAnalyzing tickets for obsolescence...")
    candidates = []

    for ticket in tickets:
        reasons = []
        score = 0

        refs = extract_references(ticket["description"])
        codebase_findings = check_references_against_codebase(
            refs, src_components, src_dirs, all_files, routes
        )

        if codebase_findings:
            deleted_findings = [f for f in codebase_findings if f["type"] in ("route", "file")]
            component_findings = [f for f in codebase_findings if f["type"] == "component"]

            if deleted_findings:
                score += 40
                reasons.append(
                    f"References {len(deleted_findings)} deleted element(s): "
                    + ", ".join(f["reference"] for f in deleted_findings[:3])
                )
            if component_findings:
                score += 20
                reasons.append(
                    f"References {len(component_findings)} possibly-removed component(s): "
                    + ", ".join(f["reference"] for f in component_findings[:3])
                )

        age_months = compute_age_months(ticket["created"])
        inactivity_months = compute_age_months(ticket["last_comment_date"] or ticket["updated"])

        if age_months >= AGE_MONTHS_THRESHOLD:
            score += 15
            reasons.append(f"Ticket is {age_months:.0f} months old")

        if inactivity_months >= INACTIVITY_MONTHS_THRESHOLD:
            score += 15
            reasons.append(f"No activity in {inactivity_months:.0f} months")

        reporter_gone = ticket["reporter_email"] and ticket["reporter_email"] not in member_emails
        if reporter_gone and age_months >= AGE_MONTHS_THRESHOLD:
            score += 10
            reasons.append(f"Reporter ({ticket['reporter_email']}) not on current team")

        if score >= 20:
            candidates.append({
                "key": ticket["key"],
                "summary": ticket["summary"],
                "type": ticket["type"],
                "status": ticket["status"],
                "created": ticket["created"],
                "updated": ticket["updated"],
                "age_months": round(age_months, 1),
                "inactivity_months": round(inactivity_months, 1),
                "score": score,
                "reasons": reasons,
                "codebase_findings": codebase_findings,
                "reporter_email": ticket["reporter_email"],
                "parent_key": ticket["parent_key"],
                "parent_status": ticket["parent_status"],
            })

    candidates.sort(key=lambda c: -c["score"])

    output = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "total_tickets_analyzed": len(tickets),
        "obsolete_candidates_found": len(candidates),
        "candidates": candidates,
    }

    with open(OUTPUT_FILE, "w") as f:
        json.dump(output, f, indent=2)
    print(f"\nResults written to: {OUTPUT_FILE}")
    print(f"  Score >= 40: {sum(1 for c in candidates if c['score'] >= 40)}")
    print(f"  Score 20-39: {sum(1 for c in candidates if 20 <= c['score'] < 40)}")

    if candidates:
        print(f"\nTop 10 obsolete candidates:")
        for c in candidates[:10]:
            print(f"  [{c['score']}] {c['key']}: {c['summary'][:60]}")
            for r in c["reasons"]:
                print(f"    - {r}")
            print()


if __name__ == "__main__":
    main()
