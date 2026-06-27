#!/usr/bin/env python3
"""Revert triage priority changes back to the original priority.

For each ticket:
1. Reads the ticket's labels to find triage-original-priority-{name}
2. Maps that name back to a Jira priority ID
3. Sets the priority back to the original value
4. Removes the triage-original-priority-* label
5. Adds triage-priority-reverted label

Usage:
    python3 scripts/revert_priority.py                    # dry-run (default)
    python3 scripts/revert_priority.py --execute          # actually revert
    python3 scripts/revert_priority.py --execute --stdin  # read keys from stdin JSON
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

PRIORITY_IDS = {
    "blocker": "10000",
    "critical": "10001",
    "major": "10002",
    "normal": "10003",
    "minor": "10004",
    "undefined": "10005",
}


def get_auth():
    email = os.environ.get("JIRA_EMAIL")
    token = os.environ.get("JIRA_TOKEN")
    if not email or not token:
        print("ERROR: JIRA_EMAIL and JIRA_TOKEN environment variables required", file=sys.stderr)
        sys.exit(1)
    return (email, token)


def get_original_priority_from_labels(key, auth):
    """Fetch the ticket's labels and find the triage-original-priority-{name} label."""
    url = f"https://{JIRA_INSTANCE}/rest/api/3/issue/{key}?fields=labels"
    resp = requests.get(url, auth=auth)
    if resp.status_code != 200:
        return None, None
    labels = resp.json().get("fields", {}).get("labels", [])
    for label in labels:
        if label.startswith("triage-original-priority-"):
            name = label.replace("triage-original-priority-", "")
            return name, label
    return None, None


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


def update_labels(key, remove_label, auth):
    """Remove triage-original-priority-* label and add triage-priority-reverted."""
    url = f"https://{JIRA_INSTANCE}/rest/api/3/issue/{key}"
    payload = {"update": {"labels": [{"remove": remove_label}, {"add": "triage-priority-reverted"}]}}
    resp = requests.put(url, json=payload, auth=auth)
    if resp.status_code == 429:
        time.sleep(int(resp.headers.get("Retry-After", 5)))
        resp = requests.put(url, json=payload, auth=auth)
    return resp.status_code == 204


def main():
    parser = argparse.ArgumentParser(description="Revert triage priority changes")
    parser.add_argument("--execute", action="store_true", help="Actually revert (default is dry-run)")
    parser.add_argument("--stdin", action="store_true", help="Read JSON with keys from stdin")
    args = parser.parse_args()

    if args.stdin:
        data = json.load(sys.stdin)
        keys = [item["key"] for item in data.get("items", [])]
    else:
        print("ERROR: Provide ticket keys via --stdin", file=sys.stderr)
        print('Example: echo \'{"items":[{"key":"OCMUI-1234"}]}\' | python3 scripts/revert_priority.py --execute --stdin', file=sys.stderr)
        sys.exit(1)

    if not keys:
        print("No tickets to revert.")
        return

    auth = get_auth()

    print(f"{'DRY RUN' if not args.execute else 'EXECUTING'} — {len(keys)} tickets to revert priority")
    print()

    success = 0
    failed = 0
    for i, key in enumerate(keys, 1):
        original_name, original_label = get_original_priority_from_labels(key, auth)
        if not original_name:
            print(f"[{i}/{len(keys)}] {key} — no triage-original-priority-* label found, skipping")
            continue

        priority_id = PRIORITY_IDS.get(original_name)
        if not priority_id:
            print(f"[{i}/{len(keys)}] {key} — unknown priority '{original_name}', skipping")
            continue

        print(f"[{i}/{len(keys)}] {key} → revert to '{original_name}'", end="")

        if not args.execute:
            print(" (dry-run)")
            continue

        print("...", end=" ")

        ok = set_priority(key, priority_id, auth)
        if not ok:
            print("FAILED (priority)")
            failed += 1
            continue

        update_labels(key, original_label, auth)
        print("OK")
        success += 1
        time.sleep(0.3)

    if args.execute:
        print(f"\nDone: {success} reverted, {failed} failed")
    else:
        print(f"\nRun with --execute to actually revert these tickets.")


if __name__ == "__main__":
    main()
