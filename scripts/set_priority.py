#!/usr/bin/env python3
"""Set Jira ticket priority to the suggested value.

For each ticket:
1. Sets the Jira priority field to the suggested value
2. Adds a triage-original-priority-{name} label (for reversibility)
3. Removes any prior triage-priority-reverted label

Usage:
    python3 scripts/set_priority.py                    # dry-run (default)
    python3 scripts/set_priority.py --execute          # actually update
    python3 scripts/set_priority.py --execute --stdin  # read manifest from stdin
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


def get_auth():
    email = os.environ.get("JIRA_EMAIL")
    token = os.environ.get("JIRA_TOKEN")
    if not email or not token:
        print("ERROR: JIRA_EMAIL and JIRA_TOKEN environment variables required", file=sys.stderr)
        sys.exit(1)
    return (email, token)


def set_priority(key, priority_id, auth):
    """Set the priority field on a Jira issue."""
    url = f"https://{JIRA_INSTANCE}/rest/api/3/issue/{key}"
    payload = {"fields": {"priority": {"id": priority_id}}}
    resp = requests.put(url, json=payload, auth=auth)
    if resp.status_code == 429:
        time.sleep(int(resp.headers.get("Retry-After", 5)))
        resp = requests.put(url, json=payload, auth=auth)
    if resp.status_code != 204:
        print(f"  ERROR setting priority on {key}: {resp.status_code} {resp.text[:200]}", file=sys.stderr)
        return False
    return True


def update_labels(key, original_priority, auth):
    """Add triage-original-priority-{name} label and remove triage-priority-reverted."""
    url = f"https://{JIRA_INSTANCE}/rest/api/3/issue/{key}"
    label = f"triage-original-priority-{original_priority}"
    payload = {"update": {"labels": [{"add": label}, {"remove": "triage-priority-reverted"}]}}
    resp = requests.put(url, json=payload, auth=auth)
    if resp.status_code == 429:
        time.sleep(int(resp.headers.get("Retry-After", 5)))
        resp = requests.put(url, json=payload, auth=auth)
    return resp.status_code == 204


def main():
    parser = argparse.ArgumentParser(description="Set Jira ticket priority to suggested value")
    parser.add_argument("--execute", action="store_true", help="Actually update (default is dry-run)")
    parser.add_argument("--stdin", action="store_true", help="Read JSON manifest from stdin")
    args = parser.parse_args()

    if args.stdin:
        data = json.load(sys.stdin)
        items = data.get("items", [])
    else:
        print("ERROR: Provide manifest via --stdin", file=sys.stderr)
        print('Example: echo \'{"items":[{"key":"OCMUI-1234","priority_id":"10002","priority_name":"Major","original_priority":"normal"}]}\' | python3 scripts/set_priority.py --execute --stdin', file=sys.stderr)
        sys.exit(1)

    if not items:
        print("No tickets to update.")
        return

    auth = get_auth()

    print(f"{'DRY RUN' if not args.execute else 'EXECUTING'} — {len(items)} tickets to update")
    print()

    success = 0
    failed = 0
    for i, item in enumerate(items, 1):
        key = item["key"]
        priority_name = item["priority_name"]
        priority_id = item["priority_id"]
        original = item["original_priority"]

        print(f"[{i}/{len(items)}] {key}: {original} → {priority_name}", end="")

        if not args.execute:
            print(" (dry-run)")
            continue

        print("...", end=" ")

        ok = set_priority(key, priority_id, auth)
        if not ok:
            print("FAILED (priority)")
            failed += 1
            continue

        update_labels(key, original, auth)
        print("OK")
        success += 1
        time.sleep(0.3)

    if args.execute:
        print(f"\nDone: {success} updated, {failed} failed")
    else:
        print(f"\nRun with --execute to actually update these tickets.")


if __name__ == "__main__":
    main()
