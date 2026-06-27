#!/usr/bin/env python3
"""Detect obsolete OCMUI Jira tickets by cross-referencing against the codebase.

Extracts UI element references (routes, component names, file paths) from ticket
descriptions and checks whether they still exist in the codebase.

Confidence tiers:
- High: ticket references a component/route deleted from src/
- Medium: ticket references a wizard step significantly refactored
- Low: 18+ months old, 0 comments in 12 months, reporter gone
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
    result = subprocess.run(
        ["rg", "--no-heading", "-oP", r"(?:path|to)=['\"]([^'\"]+)['\"]", "src/"],
        capture_output=True, text=True, cwd=REPO_ROOT
    )
    routes = set()
    for line in result.stdout.strip().split("\n"):
        match = re.search(r"['\"]([/][^'\"]+)['\"]", line)
        if match:
            routes.add(match.group(1))
    return routes


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

    route_patterns = re.findall(r'/openshift[/\w-]*', description, re.IGNORECASE)
    refs["routes"] = list(set(route_patterns))

    file_patterns = re.findall(
        r'[\w/]+\.(?:tsx?|jsx?|css|scss)',
        description, re.IGNORECASE
    )
    refs["files"] = list(set(file_patterns))

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


def check_references_against_codebase(refs, src_components, src_dirs, src_files, routes):
    """Check if extracted references still exist in the codebase."""
    findings = []

    for route in refs["routes"]:
        route_normalized = route.rstrip("/")
        found = any(route_normalized in r for r in routes)
        if not found:
            findings.append({
                "type": "route",
                "reference": route,
                "status": "not_found",
            })

    for filepath in refs["files"]:
        normalized = filepath.lower()
        found = any(normalized in f.lower() for f in src_files)
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
    src_components, src_dirs, src_files = get_src_components()
    routes = get_routes()
    print(f"  {len(src_files)} tracked files, {len(src_components)} component names, {len(routes)} routes")

    print("\nAnalyzing tickets for obsolescence...")
    candidates = []

    for ticket in tickets:
        reasons = []
        score = 0

        refs = extract_references(ticket["description"])
        codebase_findings = check_references_against_codebase(
            refs, src_components, src_dirs, src_files, routes
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
