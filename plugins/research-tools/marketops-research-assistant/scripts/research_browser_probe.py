#!/usr/bin/env python3
"""Start Chromium in one launch mode, read a page, close it; print what happened.

Run as its own process by ``research_browser_launch`` so a browser that hangs on
close (seen on a customer's Windows machine with Playwright's headless shell) is
killed by a timeout instead of hanging the installer or the MCP server.

usage: research_browser_probe.py <mode> [external-url]
"""
from __future__ import annotations

import http.server
import json
import sys
import threading
import time

PROBE_TEXT = "marketops-browser-probe"


class _Page(http.server.BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802 - http.server API
        body = f"<html><body>{PROBE_TEXT}</body></html>".encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


def main(mode: str, external: str = "") -> dict:
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Page)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    out = {"mode": mode, "launched": False, "local_page": False, "external_page": None,
           "closed": False}
    from playwright.sync_api import sync_playwright
    options = {"headless": True}
    if mode != "default":
        options["channel"] = mode
    started = time.monotonic()
    with sync_playwright() as pw:
        browser = pw.chromium.launch(**options)
        out["launched"] = True
        page = browser.new_page()
        page.goto(f"http://127.0.0.1:{server.server_address[1]}/", timeout=20000)
        out["local_page"] = PROBE_TEXT in page.content()
        if external:
            try:
                response = page.goto(external, timeout=20000)
                out["external_page"] = bool(response and response.ok)
            except Exception as exc:  # noqa: BLE001 - recorded, the local result still counts
                out["external_page"] = False
                out["external_error"] = str(exc).splitlines()[0][:160]
        browser.close()
        out["closed"] = True
    server.shutdown()
    out["seconds"] = round(time.monotonic() - started, 1)
    return out


if __name__ == "__main__":
    print(json.dumps(main(sys.argv[1], sys.argv[2] if len(sys.argv) > 2 else "")))
