# Backlog Triage

AI-assisted Jira backlog analysis tool that identifies duplicates, computes priority scores, and tracks lifecycle status. Generates an interactive HTML dashboard for team review.

Built for the [OCMUI](https://console.redhat.com/openshift) team but adaptable to any Jira project.

## What It Does

- **Priority Scoring** — computes a 0–100 priority score for each ticket based on reporter source, comment activity, Jira priority field, age, linked epics, watchers, and more
- **Duplicate Detection** — finds generated/template tickets that were created multiple times under the same parent epic (automation ran twice)
- **Lifecycle Tracking** — shows tickets approaching auto-close (lifecycle-stale) with estimated close dates
- **Orphaned Issues** — tickets whose parent epic is already Closed
- **Close History** — tracks all triage closures with labels, resolutions, and revert capability

All operations are **read-only** — nothing in Jira is modified without explicit permission.

## Prerequisites

- Python 3.10+
- Jira Cloud API access (`JIRA_EMAIL` and `JIRA_TOKEN` environment variables)
- Team member data (JSON with emails/roles for reporter classification)

## Install

```bash
git clone https://github.com/dtaylor113/backlog-triage.git
cd backlog-triage
pip install -r requirements.txt
```

## Usage

```bash
# Set credentials
export JIRA_EMAIL="you@company.com"
export JIRA_TOKEN="your-jira-api-token"

# Run all analysis steps
python3 scripts/fetch_tickets.py && \
python3 scripts/detect_duplicates.py && \
python3 scripts/score_priorities.py && \
python3 scripts/report.py && \
python3 scripts/build_html_data.py && \
python3 scripts/build_standalone.py
```

Then open `triage-report.html` in a browser (requires `triage-data.js` in the same directory), or share `triage-report-standalone.html` as a single self-contained file.

## Scripts

| Script | Purpose |
|--------|---------|
| `fetch_tickets.py` | Fetch tickets from Jira (paginated v3 API) |
| `detect_duplicates.py` | TF-IDF similarity detection for duplicate pairs |
| `score_priorities.py` | Compute priority scores (0–100) |
| `report.py` | Generate markdown summary report |
| `build_html_data.py` | Build `triage-data.js` for the HTML dashboard |
| `build_standalone.py` | Inline data into a single shareable HTML file |
| `apply_labels.py` | Apply suggested labels to Jira (requires `--apply` flag) |
| `close_tickets.py` | Close tickets via CLI (from dashboard Copy CLI cmd) |
| `revert_tickets.py` | Revert triage closures (restore status, remove labels) |
| `set_priority.py` | Set priority to suggested value |
| `revert_priority.py` | Revert priority changes |
| `freeze_tickets.py` | Freeze lifecycle-stale tickets |
| `unfreeze_tickets.py` | Unfreeze frozen tickets |

## Dashboard Features

- Dark theme, sortable and resizable columns
- Tabbed sections: Priority Scoring, Lifecycle, Possible Duplicates, Orphaned Issues, Close History
- Summary cards matching Jira board layout (Backlog + Sprint + Epics = Total)
- Filter inputs per section
- Info tooltips explaining scoring logic
- Color-coded score bars and priority badges
- Edit modes (triple-click activation) for closing, reverting, priority changes, and lifecycle freeze/unfreeze

## Configuration

The scripts reference paths specific to the OCMUI project. To adapt for another project, update:

- `PROJECT` constant in `fetch_tickets.py` (Jira project key)
- `MEMBERS_FILE` in `score_priorities.py` (team member JSON)
- `DUP_PARENT_WHITELIST` in `triage-report.html` (parent epics to exclude from duplicate detection)

## License

MIT
