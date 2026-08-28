"""Inline the web UI into one self-contained HTML file.

The output is a body fragment (no <html>/<head>/<body> wrapper) so it can be
published directly as an Artifact, and it carries no external requests at all:
the stylesheet, both scripts and the entire rulebase are inlined.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WEB = os.path.join(ROOT, "web")
OUT = os.path.join(ROOT, "dist", "underwriter.html")


def read(name: str) -> str:
    with open(os.path.join(WEB, name), encoding="utf-8") as fh:
        return fh.read()


def main() -> int:
    subprocess.run([sys.executable, os.path.join(ROOT, "tools", "export_bundle.py")], check=True)

    html = read("index.html")
    # Keep only what lives between <body> and </body>.
    body = re.search(r"<body>(.*)</body>", html, re.S).group(1)
    # Drop the tags that pull in external files; everything gets inlined below.
    body = re.sub(r'<script src="[^"]+"></script>\s*', "", body)
    body = re.sub(r"<script>\s*fetch\(.*?</script>\s*", "", body, flags=re.S)

    parts = [
        "<title>Final Expense Underwriter</title>",
        "<style>\n" + read("styles.css") + "\n</style>",
        body.strip(),
        "<script>\n" + read("engine.js") + "\n</script>",
        "<script>\n" + read("app.js") + "\n</script>",
        "<script>window.FEX_BOOT(" + read("bundle.json") + ");</script>",
    ]

    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as fh:
        fh.write("\n".join(parts))
    print(f"wrote {OUT} ({os.path.getsize(OUT) / 1024:.0f} KB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
