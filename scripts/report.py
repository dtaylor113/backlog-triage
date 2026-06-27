#!/usr/bin/env python3
"""Generate a consolidated markdown report from all triage analysis outputs.

Reads duplicate_candidates.json, obsolete_candidates.json, and priority_scores.json,
then produces a single triage-report.md with actionable summaries.
"""

import json
import time
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
DUPLICATES_FILE = BASE_DIR / "duplicate_candidates.json"
OBSOLETE_FILE = BASE_DIR / "obsolete_candidates.json"
PRIORITIES_FILE = BASE_DIR / "priority_scores.json"
OUTPUT_FILE = BASE_DIR / "triage-report.md"

JIRA_BASE = "https://redhat.atlassian.net/browse"


def load_json(path):
    if not path.exists():
        return None
    with open(path) as f:
        return json.load(f)


def section_duplicates(data):
    if not data or not data.get("pairs"):
        return "## Duplicate Candidates\n\nNo duplicate candidates found (or analysis not yet run).\n"

    method = data.get("model", data.get("method", "unknown"))
    lines = [
        "## Duplicate Candidates",
        "",
        f"**{data['duplicate_pairs_found']} pairs** found above similarity threshold "
        f"({data['threshold']}) using {method}.",
        "",
        "| Similarity | Ticket A | Ticket B | Same Reporter | Same Component |",
        "|-----------|----------|----------|:---:|:---:|",
    ]

    for pair in data["pairs"][:50]:
        sim = f"{pair['similarity']:.3f}"
        a_link = f"[{pair['ticket_a']}]({JIRA_BASE}/{pair['ticket_a']})"
        b_link = f"[{pair['ticket_b']}]({JIRA_BASE}/{pair['ticket_b']})"
        reporter = "Yes" if pair["same_reporter"] else ""
        component = "Yes" if pair["same_components"] else ""
        lines.append(f"| {sim} | {a_link} | {b_link} | {reporter} | {component} |")

    lines.append("")
    lines.append("### Pair Details")
    lines.append("")

    for i, pair in enumerate(data["pairs"][:30], 1):
        lines.append(
            f"{i}. **{pair['similarity']:.3f}** — "
            f"[{pair['ticket_a']}]({JIRA_BASE}/{pair['ticket_a']}): {pair['summary_a'][:80]}"
        )
        lines.append(
            f"   vs [{pair['ticket_b']}]({JIRA_BASE}/{pair['ticket_b']}): {pair['summary_b'][:80]}"
        )

    lines.append("")
    return "\n".join(lines)


def section_obsolete(data):
    if not data or not data.get("candidates"):
        return "## Obsolete Candidates\n\nNo obsolete candidates found (or analysis not yet run).\n"

    lines = [
        "## Obsolete Candidates",
        "",
        f"**{data['obsolete_candidates_found']} candidates** found (score ≥ 20)",
        "",
        "| Ticket | Summary | Score | Age (mo) | Inactive (mo) | Reasons |",
        "|--------|---------|:-----:|:--------:|:------------:|---------|",
    ]

    for c in data["candidates"][:50]:
        link = f"[{c['key']}]({JIRA_BASE}/{c['key']})"
        reasons = "; ".join(c["reasons"][:2])
        lines.append(
            f"| {link} | {c['summary'][:50]} | {c['score']} | {c['age_months']:.0f} | "
            f"{c['inactivity_months']:.0f} | {reasons} |"
        )
    lines.append("")

    return "\n".join(lines)


def section_priorities(data):
    if not data or not data.get("scored_tickets"):
        return "## Priority Scoring\n\nNo priority scores computed (or analysis not yet run).\n"

    lines = [
        "## Priority Scoring",
        "",
        f"**{data['total_tickets_scored']} tickets** scored.",
        "",
        "### Distribution",
        "",
        "| Suggested Priority | Count |",
        "|-------------------|:-----:|",
    ]

    for label, count in sorted(data["label_distribution"].items()):
        display = label.replace("suggested-priority-", "").capitalize()
        lines.append(f"| {display} | {count} |")

    lines.append("")
    lines.append(f"- Auto-escalated (linked to escalation/blocking): **{data['auto_escalated_count']}**")
    lines.append(f"- Priority mismatches (suggested != current Jira Priority): **{data['priority_mismatches']}**")
    lines.append("")

    lines.append("### Top 30 Highest Priority Tickets")
    lines.append("")
    lines.append("| Score | Ticket | Summary | Type | Current | Suggested | Escalated |")
    lines.append("|:-----:|--------|---------|------|---------|-----------|:---------:|")

    for s in data["scored_tickets"][:30]:
        link = f"[{s['key']}]({JIRA_BASE}/{s['key']})"
        suggested = s["suggested_label"].replace("suggested-priority-", "").capitalize()
        escalated = "Yes" if s["auto_escalated"] else ""
        lines.append(
            f"| {s['score']} | {link} | {s['summary'][:45]} | {s['type']} | "
            f"{s['current_priority']} | {suggested} | {escalated} |"
        )

    lines.append("")

    mismatches = [s for s in data["scored_tickets"] if s["priority_mismatch"]]
    if mismatches:
        lines.append("### Priority Mismatches (suggested differs from current Jira Priority)")
        lines.append("")
        lines.append("| Ticket | Summary | Current | Suggested | Score |")
        lines.append("|--------|---------|---------|-----------|:-----:|")
        for s in mismatches[:40]:
            link = f"[{s['key']}]({JIRA_BASE}/{s['key']})"
            suggested = s["suggested_label"].replace("suggested-priority-", "").capitalize()
            lines.append(
                f"| {link} | {s['summary'][:45]} | {s['current_priority']} | "
                f"{suggested} | {s['score']} |"
            )
        lines.append("")

    return "\n".join(lines)


def section_cleanup_metrics(tickets_data):
    """Additional cleanup metrics from 1D."""
    if not tickets_data:
        return ""

    tickets = tickets_data.get("tickets", [])
    if not tickets:
        return ""

    closed_parent = [t for t in tickets if t.get("parent_status") in ("Closed", "Done")]
    unassigned_old = [
        t for t in tickets
        if not t.get("reporter_email")  # proxy for unassigned (would need assignee field)
    ]

    lines = [
        "## Additional Cleanup Flags",
        "",
        f"### Closed-Parent Tickets ({len(closed_parent)} found)",
        "",
        "Stories/Tasks whose parent Epic is already Closed:",
        "",
    ]

    if closed_parent:
        lines.append("| Ticket | Summary | Parent | Parent Status |")
        lines.append("|--------|---------|--------|:------------:|")
        for t in closed_parent[:25]:
            link = f"[{t['key']}]({JIRA_BASE}/{t['key']})"
            parent_link = f"[{t['parent_key']}]({JIRA_BASE}/{t['parent_key']})" if t["parent_key"] else "—"
            lines.append(
                f"| {link} | {t['summary'][:50]} | {parent_link} | {t['parent_status']} |"
            )
        lines.append("")
    else:
        lines.append("None found.\n")

    return "\n".join(lines)


def main():
    print("Generating consolidated triage report...")

    duplicates = load_json(DUPLICATES_FILE)
    obsolete = load_json(OBSOLETE_FILE)
    priorities = load_json(PRIORITIES_FILE)
    tickets_data = load_json(BASE_DIR / "tickets.json")

    report_lines = [
        "# OCMUI Backlog Triage Report",
        "",
        f"> Generated: {time.strftime('%Y-%m-%d %H:%M:%S %Z')}",
        "",
        "---",
        "",
    ]

    # Summary stats
    stats = []
    if duplicates:
        stats.append(f"- Duplicate pairs: **{duplicates.get('duplicate_pairs_found', 0)}**")
    if obsolete:
        stats.append(f"- Obsolete candidates: **{obsolete.get('obsolete_candidates_found', 0)}**")
    if priorities:
        stats.append(f"- Tickets scored: **{priorities.get('total_tickets_scored', 0)}**")
        stats.append(f"- Priority mismatches: **{priorities.get('priority_mismatches', 0)}**")
        stats.append(f"- Auto-escalated: **{priorities.get('auto_escalated_count', 0)}**")

    if stats:
        report_lines.append("## Summary\n")
        report_lines.extend(stats)
        report_lines.append("\n---\n")

    report_lines.append(section_duplicates(duplicates))
    report_lines.append("\n---\n")
    report_lines.append(section_obsolete(obsolete))
    report_lines.append("\n---\n")
    report_lines.append(section_priorities(priorities))
    report_lines.append("\n---\n")
    report_lines.append(section_cleanup_metrics(tickets_data))

    report_content = "\n".join(report_lines)

    with open(OUTPUT_FILE, "w") as f:
        f.write(report_content)

    print(f"Report written to: {OUTPUT_FILE}")
    print(f"  Size: {len(report_content)} chars")


if __name__ == "__main__":
    main()
