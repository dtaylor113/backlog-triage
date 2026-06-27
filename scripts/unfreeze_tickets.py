#!/usr/bin/env python3
"""Unfreeze tickets previously frozen by triage, restoring them to stale lifecycle.

For each ticket:
1. Removes lifecycle-frozen label (bot will resume countdown)
2. Removes triage-frozen label

Usage:
    python3 scripts/unfreeze_tickets.py                    # dry-run (default)
    python3 scripts/unfreeze_tickets.py --execute          # actually unfreeze
    python3 scripts/unfreeze_tickets.py --execute --stdin  # read keys from stdin JSON
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


def remove_labels(key, auth):
    """Remove lifecycle-frozen and triage-frozen labels."""
    url = f"https://{JIRA_INSTANCE}/rest/api/3/issue/{key}"
    payload = {"update": {"labels": [{"remove": "lifecycle-frozen"}, {"remove": "triage-frozen"}]}}
    resp = requests.put(url, json=payload, auth=auth)
    if resp.status_code == 429:
        time.sleep(int(resp.headers.get("Retry-After", 5)))
        resp = requests.put(url, json=payload, auth=auth)
    if resp.status_code != 204:
        print(f"  ERROR removing labels from {key}: {resp.status_code} {resp.text[:200]}", file=sys.stderr)
        return False
    return True


def main():
    parser = argparse.ArgumentParser(description="Unfreeze triage-frozen tickets")
    parser.add_argument("--execute", action="store_true", help="Actually unfreeze (default is dry-run)")
    parser.add_argument("--stdin", action="store_true", help="Read JSON with keys from stdin")
    args = parser.parse_args()

    if args.stdin:
        data = json.load(sys.stdin)
        keys = [item["key"] for item in data.get("items", [])]
    else:
        print("ERROR: Provide ticket keys via --stdin", file=sys.stderr)
        print('Example: echo \'{"items":[{"key":"OCMUI-1234"}]}\' | python3 scripts/unfreeze_tickets.py --execute --stdin', file=sys.stderr)
        sys.exit(1)

    if not keys:
        print("No tickets to unfreeze.")
        return

    auth = get_auth()

    print(f"{'DRY RUN' if not args.execute else 'EXECUTING'} — {len(keys)} tickets to unfreeze")
    print()

    success = 0
    failed = 0
    for i, key in enumerate(keys, 1):
        print(f"[{i}/{len(keys)}] {key} → unfreeze", end="")

        if not args.execute:
            print(" (dry-run)")
            continue

        print("...", end=" ")

        ok = remove_labels(key, auth)
        if ok:
            print("OK")
            success += 1
        else:
            print("FAILED")
            failed += 1
        time.sleep(0.3)

    if args.execute:
        print(f"\nDone: {success} unfrozen, {failed} failed")
    else:
        print(f"\nRun with --execute to actually unfreeze these tickets.")


if __name__ == "__main__":
    main()
