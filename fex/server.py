"""Serve the browser UI locally with nothing but the standard library.

    python -m fex.cli serve

The page runs the JavaScript port of the engine (``web/engine.js``) against a
bundle exported from the same YAML the Python engine reads, so the local server
is a static file server and nothing more. If the bundle is missing or older than
the data files, it is regenerated on start.
"""

from __future__ import annotations

import functools
import http.server
import os
import socketserver
import subprocess
import sys
import webbrowser

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WEB_DIR = os.path.join(ROOT, "web")
BUNDLE = os.path.join(WEB_DIR, "bundle.json")
EXPORTER = os.path.join(ROOT, "tools", "export_bundle.py")
DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")


def _data_mtime() -> float:
    newest = 0.0
    for dirpath, _, filenames in os.walk(DATA_DIR):
        for name in filenames:
            if name.endswith((".yaml", ".yml")):
                newest = max(newest, os.path.getmtime(os.path.join(dirpath, name)))
    return newest


def ensure_bundle() -> None:
    """Rebuild web/bundle.json when it is missing or behind the YAML."""
    if os.path.exists(BUNDLE) and os.path.getmtime(BUNDLE) >= _data_mtime():
        return
    if not os.path.exists(EXPORTER):
        raise SystemExit(f"cannot find the bundle exporter at {EXPORTER}")
    print("Rulebase changed - rebuilding web/bundle.json ...")
    subprocess.run([sys.executable, EXPORTER], check=True)


class Handler(http.server.SimpleHTTPRequestHandler):
    def end_headers(self) -> None:
        # The bundle is regenerated in place, so never let a browser cache it.
        self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def log_message(self, fmt: str, *args) -> None:
        if "GET" in (args[0] if args else ""):
            return
        super().log_message(fmt, *args)


class ReusableServer(socketserver.TCPServer):
    allow_reuse_address = True


def serve(host: str = "127.0.0.1", port: int = 8000, open_browser: bool = True) -> None:
    ensure_bundle()
    handler = functools.partial(Handler, directory=WEB_DIR)
    with ReusableServer((host, port), handler) as httpd:
        url = f"http://{host}:{port}/"
        print(f"Final expense underwriter running at {url}  (ctrl-c to stop)")
        if open_browser:
            try:
                webbrowser.open(url)
            except Exception:
                pass
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print("\nstopped")
