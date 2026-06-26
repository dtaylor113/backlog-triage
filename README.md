# Backlog Triage

AI-assisted Jira backlog analysis tool that identifies duplicates, obsolete tickets, and computes priority scores. Generates an interactive HTML dashboard for team review.

Built for the [OCMUI](https://console.redhat.com/openshift) team but adaptable to any Jira project.

## What It Does

- **Priority Scoring** — computes a 0–100 priority score for each ticket based on reporter source, comment activity, Jira priority field, age, linked epics, watchers, and more
- **Duplicate Detection** — finds generated/template tickets that were created multiple times under the same parent epic (automation ran twice)
- **Obsolete Detection** — cross-references ticket descriptions against the codebase to find tickets referencing deleted routes, components, or files
- **Lifecycle Tracking** — shows tickets approaching auto-close (lifecycle-stale) with estimated close dates
- **Orphaned Issues** — tickets whose parent epic is already Closed

All operations are **read-only** — nothing in Jira is modified without explicit permission.

## Prerequisites

- Python 3.10+
- Jira Cloud API access (`JIRA_EMAIL` and `JIRA_TOKEN` environment variables)
- A cloned copy of your target codebase (for obsolete detection)
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
python3 scripts/detect_obsolete.py && \
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
| `detect_obsolete.py` | Cross-reference tickets against codebase |
| `score_priorities.py` | Compute priority scores (0–100) |
| `report.py` | Generate markdown summary report |
| `build_html_data.py` | Build `triage-data.js` for the HTML dashboard |
| `build_standalone.py` | Inline data into a single shareable HTML file |
| `apply_labels.py` | Apply suggested labels to Jira (requires `--apply` flag) |

## Dashboard Features

- Dark theme, sortable and resizable columns
- Tabbed sections: Priority Scoring, Lifecycle, Obsolete Candidates, Possible Duplicates, Orphaned Issues
- Summary cards matching Jira board layout (Backlog + Sprint + Epics = Total)
- Filter inputs per section
- Info tooltips explaining scoring logic
- Color-coded score bars and priority badges
- Clickable summary cards for quick navigation

## Configuration

The scripts reference paths specific to the OCMUI project. To adapt for another project, update:

- `PROJECT` constant in `fetch_tickets.py` (Jira project key)
- `REPO_ROOT` in `detect_obsolete.py` (path to your codebase)
- `MEMBERS_FILE` in `detect_obsolete.py` and `score_priorities.py` (team member JSON)
- `DUP_PARENT_WHITELIST` in `triage-report.html` (parent epics to exclude from duplicate detection)

## License

MIT
