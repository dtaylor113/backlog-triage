#!/usr/bin/env python3
"""Fetch all actionable OCMUI Jira tickets via paginated v3 API.

Exports to ~/repos/work/AI/backlog-triage/tickets.json with fields needed
for duplicate detection, obsolete detection, and priority scoring.
"""

import json
import os
import sys
import time
from pathlib import Path
from urllib.parse import urlencode

import requests

JIRA_INSTANCE = "redhat.atlassian.net"
PROJECT = "OCMUI"
OUTPUT_DIR = Path(__file__).resolve().parent.parent
OUTPUT_FILE = OUTPUT_DIR / "tickets.json"

JQL = (
    f"project = {PROJECT} "
    "AND status not in (Closed, Done) "
    "AND issuetype not in (Sub-task) "
    "AND (labels is EMPTY OR labels not in (lifecycle-stale, lifecycle-frozen))"
)

JQL_STALE = (
    f"project = {PROJECT} "
    "AND status not in (Closed, Done) "
    "AND issuetype not in (Sub-task) "
    "AND labels in (lifecycle-stale)"
)

JQL_FROZEN = (
    f"project = {PROJECT} "
    "AND status not in (Closed, Done) "
    "AND issuetype not in (Sub-task) "
    "AND labels in (lifecycle-frozen)"
)

FIELDS = [
    "key", "summary", "description", "labels", "components",
    "reporter", "priority", "created", "updated", "comment",
    "watches", "issuelinks", "issuetype", "status", "parent",
    "customfield_10020",  # Sprint
]

MAX_RESULTS = 50  # Jira page size


def get_auth():
    email = os.environ.get("JIRA_EMAIL")
    token = os.environ.get("JIRA_TOKEN")
    if not email or not token:
        print("ERROR: JIRA_EMAIL and JIRA_TOKEN environment variables required", file=sys.stderr)
        sys.exit(1)
    return (email, token)


def extract_text_from_adf(node):
    """Recursively extract plain text from Atlassian Document Format."""
    if node is None:
        return ""
    if isinstance(node, str):
        return node
    text_parts = []
    if node.get("type") == "text":
        text_parts.append(node.get("text", ""))
    for child in node.get("content", []):
        text_parts.append(extract_text_from_adf(child))
    return " ".join(text_parts)


def fetch_all_tickets(jql=None):
    auth = get_auth()
    base_url = f"https://{JIRA_INSTANCE}/rest/api/3/search/jql"

    all_tickets = []
    next_page_token = None
    page = 0

    while True:
        params = {
            "jql": jql or JQL,
            "maxResults": MAX_RESULTS,
            "fields": ",".join(FIELDS),
        }
        if next_page_token:
            params["nextPageToken"] = next_page_token

        resp = requests.get(base_url, params=params, auth=auth)
        if resp.status_code == 429:
            retry_after = int(resp.headers.get("Retry-After", 10))
            print(f"  Rate limited, sleeping {retry_after}s...")
            time.sleep(retry_after)
            continue
        resp.raise_for_status()
        data = resp.json()

        issues = data.get("issues", [])
        page += 1
        print(f"  Page {page}: fetched {len(issues)} issues (total so far: {len(all_tickets) + len(issues)})")

        for issue in issues:
            ticket = normalize_issue(issue)
            all_tickets.append(ticket)

        if data.get("isLast", True):
            break
        next_page_token = data.get("nextPageToken")
        if not next_page_token:
            break

    return all_tickets


def normalize_issue(issue):
    """Flatten a Jira issue into a clean dict for local processing."""
    fields = issue.get("fields", {})
    reporter = fields.get("reporter") or {}
    comments = fields.get("comment", {}).get("comments", [])
    watches = fields.get("watches", {})
    parent = fields.get("parent") or {}
    links = fields.get("issuelinks", [])

    description_adf = fields.get("description")
    description_text = extract_text_from_adf(description_adf) if description_adf else ""

    normalized_links = []
    for link in links:
        link_type = link.get("type", {}).get("name", "")
        if "inwardIssue" in link:
            target = link["inwardIssue"]
            direction = "inward"
            relation = link.get("type", {}).get("inward", link_type)
        elif "outwardIssue" in link:
            target = link["outwardIssue"]
            direction = "outward"
            relation = link.get("type", {}).get("outward", link_type)
        else:
            continue
        normalized_links.append({
            "direction": direction,
            "relation": relation,
            "key": target.get("key", ""),
            "summary": target.get("fields", {}).get("summary", ""),
            "status": target.get("fields", {}).get("status", {}).get("name", ""),
        })

    # Sprint field (customfield_10020) — array of sprint objects, take the latest active/future one
    sprint_data = fields.get("customfield_10020") or []
    sprint_name = ""
    if sprint_data and isinstance(sprint_data, list):
        # Prefer active sprint, then future, then last one
        active = [s for s in sprint_data if s.get("state") == "active"]
        future = [s for s in sprint_data if s.get("state") == "future"]
        chosen = active[0] if active else (future[0] if future else sprint_data[-1])
        sprint_name = chosen.get("name", "")

    return {
        "key": issue.get("key", ""),
        "summary": fields.get("summary", ""),
        "description": description_text[:2000],
        "description_full_length": len(description_text),
        "type": fields.get("issuetype", {}).get("name", ""),
        "status": fields.get("status", {}).get("name", ""),
        "priority": fields.get("priority", {}).get("name", ""),
        "labels": fields.get("labels", []),
        "components": [c.get("name", "") for c in fields.get("components", [])],
        "reporter_email": reporter.get("emailAddress", ""),
        "reporter_name": reporter.get("displayName", ""),
        "created": fields.get("created", ""),
        "updated": fields.get("updated", ""),
        "comment_count": len(comments),
        "last_comment_date": comments[-1].get("created", "") if comments else "",
        "watch_count": watches.get("watchCount", 0),
        "parent_key": parent.get("key", ""),
        "parent_summary": parent.get("fields", {}).get("summary", "") if parent else "",
        "parent_status": parent.get("fields", {}).get("status", {}).get("name", "") if parent else "",
        "parent_type": parent.get("fields", {}).get("issuetype", {}).get("name", "") if parent else "",
        "links": normalized_links,
        "sprint": sprint_name,
    }


def fetch_parent_resolution_dates(parent_keys):
    """Fetch resolutiondate for a list of parent issue keys."""
    if not parent_keys:
        return {}
    auth = get_auth()
    base_url = f"https://{JIRA_INSTANCE}/rest/api/3/search/jql"
    keys_jql = ", ".join(parent_keys)
    jql = f"key in ({keys_jql})"
    results = {}
    next_page_token = None

    while True:
        params = {
            "jql": jql,
            "maxResults": 50,
            "fields": "key,resolutiondate",
        }
        if next_page_token:
            params["nextPageToken"] = next_page_token
        resp = requests.get(base_url, params=params, auth=auth)
        resp.raise_for_status()
        data = resp.json()
        for issue in data.get("issues", []):
            key = issue.get("key", "")
            rd = issue.get("fields", {}).get("resolutiondate", "")
            results[key] = rd[:10] if rd else ""
        if data.get("isLast", True):
            break
        next_page_token = data.get("nextPageToken")
        if not next_page_token:
            break

    return results


def main():
    print(f"Fetching open {PROJECT} tickets (excl. stale/frozen)...")
    print(f"JQL: {JQL}")
    print()

    tickets = fetch_all_tickets(JQL)
    print(f"\nTotal open tickets fetched: {len(tickets)}")

    print(f"\nFetching lifecycle-stale tickets...")
    print(f"JQL: {JQL_STALE}")
    stale_tickets = fetch_all_tickets(JQL_STALE)
    print(f"Total stale tickets fetched: {len(stale_tickets)}")

    print(f"\nFetching lifecycle-frozen tickets...")
    print(f"JQL: {JQL_FROZEN}")
    frozen_tickets = fetch_all_tickets(JQL_FROZEN)
    print(f"Total frozen tickets fetched: {len(frozen_tickets)}")

    # Fetch actual resolution dates for closed parent issues
    all_tickets = tickets + stale_tickets + frozen_tickets
    closed_parent_keys = list({
        t["parent_key"] for t in all_tickets
        if t.get("parent_status") in ("Closed", "Done") and t.get("parent_key")
    })
    parent_dates = {}
    if closed_parent_keys:
        print(f"\nFetching resolution dates for {len(closed_parent_keys)} closed parents...")
        parent_dates = fetch_parent_resolution_dates(closed_parent_keys)
        print(f"  Got dates for {sum(1 for v in parent_dates.values() if v)} parents")

    # Inject parent_resolutiondate into tickets
    for t in all_tickets:
        pk = t.get("parent_key", "")
        t["parent_resolutiondate"] = parent_dates.get(pk, "")

    OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(OUTPUT_FILE, "w") as f:
        json.dump({
            "fetched_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            "jql": JQL,
            "count": len(tickets),
            "tickets": tickets,
            "stale_jql": JQL_STALE,
            "stale_count": len(stale_tickets),
            "stale_tickets": stale_tickets,
            "frozen_jql": JQL_FROZEN,
            "frozen_count": len(frozen_tickets),
            "frozen_tickets": frozen_tickets,
        }, f, indent=2)

    total = len(tickets) + len(stale_tickets) + len(frozen_tickets)
    print(f"\nTotal: {len(tickets)} open + {len(stale_tickets)} stale + {len(frozen_tickets)} frozen = {total}")
    print(f"Written to: {OUTPUT_FILE}")


if __name__ == "__main__":
    main()
