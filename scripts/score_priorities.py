#!/usr/bin/env python3
"""Score OCMUI Jira tickets using the priority model.

Scoring model (0-100 base, can exceed with auto-escalation):
- Reporter source: External=+25, QE=+10, Dev=+0
- Comment count: 5=+8, 6-10=+12, 10+=+15
- Jira Priority field: Blocker=20, Critical=16, Major=12, Normal=6, Minor=2
- Age + activity ratio: High activity on old ticket = high priority
- Linked to active Epic: +10
- Customer-facing (bug visible to end-users): +10
- Watchers count: >3 = +5
- Lifecycle-frozen label: +8 (deliberately preserved from auto-deletion)

Auto-escalation (floor = 70):
- Linked to Salesforce/support case
- Has Due date within 2 sprints (~6 weeks)
- Blocks another team's work

Bonus modifiers:
- Regression recency: +5 (bug <3 months old)
- Sprint bounce: -5 per bounce (max -15)
"""

import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
TICKETS_FILE = BASE_DIR / "tickets.json"
OUTPUT_FILE = BASE_DIR / "priority_scores.json"
MEMBERS_FILE = Path("/Users/dtaylor/repos/work/ocmui-team-dashboard/data/members.json")

PRIORITY_WEIGHTS = {
    "Blocker": 20,
    "Critical": 16,
    "Major": 12,
    "Normal": 6,
    "Minor": 2,
}

SUGGESTED_LABELS = [
    (80, "suggested-priority-blocker"),
    (60, "suggested-priority-critical"),
    (40, "suggested-priority-major"),
    (20, "suggested-priority-normal"),
    (0, "suggested-priority-minor"),
]


def load_tickets():
    with open(TICKETS_FILE) as f:
        data = json.load(f)
    return data["tickets"]


def load_team_members():
    with open(MEMBERS_FILE) as f:
        members = json.load(f)

    dev_emails = set()
    qe_emails = set()
    for m in members:
        role = m.get("role", "").lower()
        if "qe" in role:
            qe_emails.add(m["jira"])
        else:
            dev_emails.add(m["jira"])

    return dev_emails, qe_emails


def compute_age_months(date_str):
    if not date_str:
        return 0
    try:
        dt = datetime.fromisoformat(date_str.replace("Z", "+00:00"))
        now = datetime.now(timezone.utc)
        return (now - dt).days / 30.44
    except (ValueError, TypeError):
        return 0


AUTOMATION_REPORTERS = ["automation for jira"]


def score_reporter(ticket, dev_emails, qe_emails):
    email = ticket.get("reporter_email", "")
    reporter_name = ticket.get("reporter_name", "").lower()

    if any(a in reporter_name for a in AUTOMATION_REPORTERS):
        return 0
    if email in dev_emails:
        return 0
    elif email in qe_emails:
        return 10
    else:
        return 25


def score_comments(ticket):
    count = ticket.get("comment_count", 0)
    if count >= 10:
        return 15
    elif count >= 6:
        return 12
    elif count >= 5:
        return 8
    return 0


def score_priority_field(ticket):
    priority = ticket.get("priority", "Normal")
    return PRIORITY_WEIGHTS.get(priority, 6)


def score_age_activity(ticket):
    """High activity on old ticket = people keep bumping it = higher priority."""
    age_months = compute_age_months(ticket["created"])
    comment_count = ticket.get("comment_count", 0)

    if age_months < 1:
        return 0

    activity_rate = comment_count / max(age_months, 1)

    if activity_rate >= 2.0:
        return 15
    elif activity_rate >= 1.0:
        return 12
    elif activity_rate >= 0.5:
        return 8
    elif age_months >= 12 and comment_count >= 3:
        return 5
    return 0


def score_linked_epic(ticket):
    """Part of an in-progress Epic = +10."""
    parent_status = ticket.get("parent_status", "")
    if parent_status in ("In Progress", "Review", "Refinement"):
        return 10
    return 0


def score_customer_facing(ticket):
    """Bug visible to end-users vs internal tooling = +10."""
    ticket_type = ticket.get("type", "").lower()
    labels = [l.lower() for l in ticket.get("labels", [])]

    if ticket_type == "bug":
        if "internal" not in labels and "tech-debt" not in labels and "tech-maintenance" not in labels:
            return 10
    return 0


def score_watchers(ticket):
    if ticket.get("watch_count", 0) > 3:
        return 5
    return 0


def score_lifecycle_frozen(ticket):
    """Frozen tickets were deliberately preserved from auto-deletion."""
    labels = [l.lower() for l in ticket.get("labels", [])]
    if "lifecycle-frozen" in labels:
        return 8
    return 0


def check_auto_escalation(ticket):
    """Check for signals that auto-escalate to Critical+ (floor=70)."""
    reasons = []
    links = ticket.get("links", [])

    for link in links:
        relation = link.get("relation", "").lower()
        key = link.get("key", "")
        if any(kw in relation for kw in ["caused by", "escalat", "support", "salesforce"]):
            reasons.append(f"Linked to escalation/support: {key}")
        if "blocks" in relation and link.get("direction") == "outward":
            target_key = link.get("key", "")
            if not target_key.startswith("OCMUI-"):
                reasons.append(f"Blocks external team: {target_key}")

    # TODO: Check Due date within 6 weeks (would need 'duedate' field from Jira)

    return reasons


def compute_bonus_modifiers(ticket):
    """Regression recency and sprint bounce."""
    bonus = 0
    age_months = compute_age_months(ticket["created"])

    if ticket.get("type", "").lower() == "bug" and age_months <= 3:
        bonus += 5

    # Sprint bounce detection would require changelog data (not available in current fetch)
    # Placeholder for future enhancement

    return bonus


def get_suggested_label(score):
    for threshold, label in SUGGESTED_LABELS:
        if score >= threshold:
            return label
    return "suggested-priority-minor"


def score_ticket(ticket, dev_emails, qe_emails):
    """Compute total priority score for a ticket."""
    breakdown = {
        "reporter": score_reporter(ticket, dev_emails, qe_emails),
        "comments": score_comments(ticket),
        "priority_field": score_priority_field(ticket),
        "age_activity": score_age_activity(ticket),
        "linked_epic": score_linked_epic(ticket),
        "customer_facing": score_customer_facing(ticket),
        "watchers": score_watchers(ticket),
        "lifecycle_frozen": score_lifecycle_frozen(ticket),
    }

    base_score = sum(breakdown.values())
    bonus = compute_bonus_modifiers(ticket)
    total = base_score + bonus

    escalation_reasons = check_auto_escalation(ticket)
    if escalation_reasons:
        total = max(total, 70)

    total = min(total, 100)
    suggested_label = get_suggested_label(total)

    return {
        "score": total,
        "breakdown": breakdown,
        "bonus": bonus,
        "auto_escalated": bool(escalation_reasons),
        "escalation_reasons": escalation_reasons,
        "suggested_label": suggested_label,
    }


def main():
    print("Loading tickets...")
    tickets = load_tickets()
    print(f"  {len(tickets)} tickets loaded")

    print("\nLoading team members...")
    dev_emails, qe_emails = load_team_members()
    print(f"  {len(dev_emails)} dev emails, {len(qe_emails)} QE emails")

    print("\nScoring tickets...")
    scored = []
    for ticket in tickets:
        result = score_ticket(ticket, dev_emails, qe_emails)
        scored.append({
            "key": ticket["key"],
            "summary": ticket["summary"],
            "type": ticket["type"],
            "status": ticket["status"],
            "priority": ticket["priority"],
            "reporter_email": ticket["reporter_email"],
            "comment_count": ticket["comment_count"],
            "created": ticket["created"],
            "updated": ticket["updated"],
            "score": result["score"],
            "breakdown": result["breakdown"],
            "bonus": result["bonus"],
            "auto_escalated": result["auto_escalated"],
            "escalation_reasons": result["escalation_reasons"],
            "suggested_label": result["suggested_label"],
            "current_priority": ticket["priority"],
            "priority_mismatch": (
                result["suggested_label"].replace("suggested-priority-", "").capitalize()
                != ticket["priority"]
            ),
        })

    scored.sort(key=lambda s: -s["score"])

    label_distribution = {}
    for s in scored:
        label = s["suggested_label"]
        label_distribution[label] = label_distribution.get(label, 0) + 1

    output = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "total_tickets_scored": len(scored),
        "label_distribution": label_distribution,
        "auto_escalated_count": len([s for s in scored if s["auto_escalated"]]),
        "priority_mismatches": len([s for s in scored if s["priority_mismatch"]]),
        "scored_tickets": scored,
    }

    with open(OUTPUT_FILE, "w") as f:
        json.dump(output, f, indent=2)
    print(f"\nResults written to: {OUTPUT_FILE}")

    print(f"\nLabel distribution:")
    for label, count in sorted(label_distribution.items()):
        print(f"  {label}: {count}")

    print(f"\nAuto-escalated: {output['auto_escalated_count']}")
    print(f"Priority mismatches (suggested != current): {output['priority_mismatches']}")

    print(f"\nTop 15 highest priority tickets:")
    for s in scored[:15]:
        flag = " [ESCALATED]" if s["auto_escalated"] else ""
        print(f"  {s['score']:3d}  {s['key']}: {s['summary'][:55]}{flag}")


if __name__ == "__main__":
    main()
