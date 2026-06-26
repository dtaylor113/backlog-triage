#!/usr/bin/env python3
"""Apply triage labels to Jira tickets. READ-ONLY UNLESS --apply flag is passed.

Usage:
  python apply_labels.py --dry-run          # Show what would be applied (default)
  python apply_labels.py --apply            # Actually apply labels to Jira
  python apply_labels.py --apply --batch 20 # Apply to first 20 candidates only

This script reads duplicate_candidates.json, obsolete_candidates.json, and
priority_scores.json and applies the appropriate labels to tickets.

Labels applied:
- duplicate-candidate (from duplicate detection)
- obsolete-candidate (from obsolete detection, high+medium confidence only)
- suggested-priority-{blocker,critical,major,normal,minor} (from priority scoring)
"""

import argparse
import json
import os
import sys
import time
from pathlib import Path

import requests

BASE_DIR = Path(__file__).resolve().parent.parent
JIRA_INSTANCE = "redhat.atlassian.net"


def get_auth():
    email = os.environ.get("JIRA_EMAIL")
    token = os.environ.get("JIRA_TOKEN")
    if not email or not token:
        print("ERROR: JIRA_EMAIL and JIRA_TOKEN environment variables required", file=sys.stderr)
        sys.exit(1)
    return (email, token)


def load_json(path):
    if not path.exists():
        return None
    with open(path) as f:
        return json.load(f)


def add_label_to_ticket(ticket_key, label, auth, dry_run=True):
    """Add a single label to a Jira ticket."""
    if dry_run:
        print(f"  [DRY RUN] Would add label '{label}' to {ticket_key}")
        return True

    url = f"https://{JIRA_INSTANCE}/rest/api/3/issue/{ticket_key}"
    payload = {
        "update": {
            "labels": [{"add": label}]
        }
    }

    resp = requests.put(url, json=payload, auth=auth)
    if resp.status_code == 204:
        print(f"  [APPLIED] Added label '{label}' to {ticket_key}")
        return True
    elif resp.status_code == 429:
        retry_after = int(resp.headers.get("Retry-After", 10))
        print(f"  Rate limited, sleeping {retry_after}s...")
        time.sleep(retry_after)
        return add_label_to_ticket(ticket_key, label, auth, dry_run=False)
    else:
        print(f"  [ERROR] Failed to add label to {ticket_key}: {resp.status_code} {resp.text[:100]}")
        return False


def collect_label_actions(batch_limit=None):
    """Collect all label actions from analysis outputs."""
    actions = []

    # Duplicate candidates
    duplicates = load_json(BASE_DIR / "duplicate_candidates.json")
    if duplicates:
        seen_keys = set()
        for pair in duplicates.get("pairs", []):
            for key_field in ("ticket_a", "ticket_b"):
                key = pair[key_field]
                if key not in seen_keys:
                    seen_keys.add(key)
                    actions.append({
                        "key": key,
                        "label": "duplicate-candidate",
                        "reason": f"Similar to {pair['ticket_b' if key_field == 'ticket_a' else 'ticket_a']} "
                                  f"(similarity: {pair['similarity']:.3f})",
                    })

    # Obsolete candidates (high + medium confidence only)
    obsolete = load_json(BASE_DIR / "obsolete_candidates.json")
    if obsolete:
        for candidate in obsolete.get("candidates", []):
            if candidate["confidence"] in ("high", "medium"):
                actions.append({
                    "key": candidate["key"],
                    "label": "obsolete-candidate",
                    "reason": "; ".join(candidate["reasons"][:2]),
                })

    # Priority scores (only apply if mismatch)
    priorities = load_json(BASE_DIR / "priority_scores.json")
    if priorities:
        for scored in priorities.get("scored_tickets", []):
            if scored["priority_mismatch"]:
                actions.append({
                    "key": scored["key"],
                    "label": scored["suggested_label"],
                    "reason": f"Score: {scored['score']}, current: {scored['current_priority']}",
                })

    if batch_limit:
        actions = actions[:batch_limit]

    return actions


def main():
    parser = argparse.ArgumentParser(description="Apply triage labels to Jira tickets")
    parser.add_argument("--apply", action="store_true", help="Actually apply labels (default is dry-run)")
    parser.add_argument("--batch", type=int, default=None, help="Limit to N label applications")
    parser.add_argument("--type", choices=["duplicates", "obsolete", "priorities", "all"],
                        default="all", help="Which type of labels to apply")
    args = parser.parse_args()

    dry_run = not args.apply

    if dry_run:
        print("=== DRY RUN MODE (use --apply to actually modify tickets) ===\n")
    else:
        print("=== LIVE MODE: Labels will be applied to Jira ===\n")
        auth = get_auth()

    actions = collect_label_actions(batch_limit=args.batch)

    if args.type != "all":
        label_filter = {
            "duplicates": "duplicate-candidate",
            "obsolete": "obsolete-candidate",
            "priorities": "suggested-priority-",
        }[args.type]
        actions = [a for a in actions if a["label"].startswith(label_filter) or a["label"] == label_filter]

    print(f"Total label actions: {len(actions)}\n")

    if not actions:
        print("Nothing to apply.")
        return

    # Group by label type for summary
    by_label = {}
    for a in actions:
        label = a["label"]
        by_label.setdefault(label, []).append(a)

    print("Label breakdown:")
    for label, items in sorted(by_label.items()):
        print(f"  {label}: {len(items)} tickets")
    print()

    success = 0
    failed = 0
    for i, action in enumerate(actions, 1):
        if dry_run:
            print(f"  [{i}/{len(actions)}] {action['key']} <- '{action['label']}' ({action['reason'][:60]})")
        else:
            ok = add_label_to_ticket(action["key"], action["label"], auth, dry_run=False)
            if ok:
                success += 1
            else:
                failed += 1
            if i % 10 == 0:
                time.sleep(1)

    print(f"\n{'DRY RUN ' if dry_run else ''}Complete.")
    if not dry_run:
        print(f"  Applied: {success}, Failed: {failed}")


if __name__ == "__main__":
    main()
