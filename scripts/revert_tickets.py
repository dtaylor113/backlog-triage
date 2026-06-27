#!/usr/bin/env python3
"""Revert triage-closed tickets back to their previous status.

For each ticket:
1. Looks up the previous status from the ticket's changelog
2. Transitions back to that status
3. Removes the triage-closed-* label
4. Adds triage-reverted label

Usage:
    python3 scripts/revert_tickets.py                    # dry-run (default)
    python3 scripts/revert_tickets.py --execute          # actually revert
    python3 scripts/revert_tickets.py --execute --stdin  # read keys from stdin JSON
"""

import argparse
import json
import os
import sys
import time
from pathlib import Path

import requests

JIRA_INSTANCE = "redhat.atlassian.net"
BASE_DIR = Path(__file__).resolve().parent.parent

TRANSITION_IDS = {
    "To Do": "11",
    "In Progress": "21",
    "Code Review": "31",
    "Review": "41",
    "Closed": "51",
}

TRIAGE_LABELS = [
    "triage-closed-orphan",
    "triage-closed-duplicate",
    "triage-closed-obsolete",
]


def get_auth():
    email = os.environ.get("JIRA_EMAIL")
    token = os.environ.get("JIRA_TOKEN")
    if not email or not token:
        print("ERROR: JIRA_EMAIL and JIRA_TOKEN environment variables required", file=sys.stderr)
        sys.exit(1)
    return (email, token)


def get_previous_status(key, auth):
    """Look up the status before the most recent Close transition."""
    url = f"https://{JIRA_INSTANCE}/rest/api/3/issue/{key}/changelog"
    resp = requests.get(url, auth=auth)
    if resp.status_code != 200:
        return None
    data = resp.json()
    for history in reversed(data.get("values", [])):
        for item in history.get("items", []):
            if item["field"] == "status" and item.get("toString") == "Closed":
                return item.get("fromString")
    return None


def transition_to(key, status_name, auth):
    """Transition a ticket to the given status."""
    transition_id = TRANSITION_IDS.get(status_name)
    if not transition_id:
        print(f"  ERROR: Unknown status '{status_name}' — no transition ID mapped", file=sys.stderr)
        return False

    url = f"https://{JIRA_INSTANCE}/rest/api/3/issue/{key}/transitions"
    payload = {"transition": {"id": transition_id}}
    resp = requests.post(url, json=payload, auth=auth)
    if resp.status_code == 429:
        time.sleep(int(resp.headers.get("Retry-After", 5)))
        resp = requests.post(url, json=payload, auth=auth)
    if resp.status_code != 204:
        print(f"  ERROR transitioning {key} to {status_name}: {resp.status_code} {resp.text[:200]}", file=sys.stderr)
        return False
    return True


def update_labels(key, remove_labels, add_labels, auth):
    """Remove triage-closed-* labels and add triage-reverted."""
    url = f"https://{JIRA_INSTANCE}/rest/api/3/issue/{key}"
    updates = []
    for label in remove_labels:
        updates.append({"remove": label})
    for label in add_labels:
        updates.append({"add": label})
    payload = {"update": {"labels": updates}}
    resp = requests.put(url, json=payload, auth=auth)
    if resp.status_code == 429:
        time.sleep(int(resp.headers.get("Retry-After", 5)))
        resp = requests.put(url, json=payload, auth=auth)
    return resp.status_code == 204


def main():
    parser = argparse.ArgumentParser(description="Revert triage-closed tickets")
    parser.add_argument("--execute", action="store_true", help="Actually revert (default is dry-run)")
    parser.add_argument("--stdin", action="store_true", help="Read JSON with keys from stdin")
    args = parser.parse_args()

    if args.stdin:
        data = json.load(sys.stdin)
        keys = [item["key"] for item in data.get("items", [])]
    else:
        print("ERROR: Provide ticket keys via --stdin", file=sys.stderr)
        print('Example: echo \'{"items":[{"key":"OCMUI-1234"}]}\' | python3 scripts/revert_tickets.py --execute --stdin', file=sys.stderr)
        sys.exit(1)

    if not keys:
        print("No tickets to revert.")
        return

    auth = get_auth()

    print(f"{'DRY RUN' if not args.execute else 'EXECUTING'} — {len(keys)} tickets to revert")
    print()

    for i, key in enumerate(keys, 1):
        prev_status = get_previous_status(key, auth)
        if not prev_status:
            print(f"[{i}/{len(keys)}] {key} — could not determine previous status, skipping")
            continue

        print(f"[{i}/{len(keys)}] {key} → revert to '{prev_status}'", end="")

        if not args.execute:
            print(" (dry-run)")
            continue

        print("...", end=" ")

        ok = transition_to(key, prev_status, auth)
        if not ok:
            print("FAILED (transition)")
            continue

        update_labels(key, TRIAGE_LABELS, ["triage-reverted"], auth)
        print("OK")
        time.sleep(0.3)

    if not args.execute:
        print("\nRun with --execute to actually revert these tickets.")


if __name__ == "__main__":
    main()
