#!/usr/bin/env python3
"""Build a single self-contained HTML file with data inlined.

Replaces <script src="triage-data.js"></script> with an inline <script> block
containing the full data, producing one portable file that works offline.
"""

from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
HTML_FILE = BASE_DIR / "triage-report.html"
DATA_FILE = BASE_DIR / "triage-data.js"
OUTPUT_FILE = BASE_DIR / "triage-report-standalone.html"


def main():
    html = HTML_FILE.read_text()
    data_js = DATA_FILE.read_text()

    inline_script = f"<script>\n{data_js}</script>"
    result = html.replace('<script src="triage-data.js"></script>', inline_script)

    OUTPUT_FILE.write_text(result)
    size_mb = OUTPUT_FILE.stat().st_size / (1024 * 1024)
    print(f"Built: {OUTPUT_FILE} ({size_mb:.1f} MB)")


if __name__ == "__main__":
    main()
