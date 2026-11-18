#!/usr/bin/env python3
"""Crawl4AI Local smoke test.

Four checks, in this order, because a later one passing means nothing if an
earlier one failed:

  1. /health answers without credentials
  2. /crawl refuses a request that carries no token
  3. /crawl with the token returns real text for a static page
  4. the same endpoint renders a JS-built page (this is what "browser" buys)

Writes a JSON result to stdout. Exit code is 0 only if every check passed.
Nothing here prints the token.
"""

from __future__ import annotations

import json
import os
import pathlib
import sys
import time
import urllib.error
import urllib.request

BASE = os.environ.get("CRAWL4AI_BASE_URL", "http://127.0.0.1:11235")
ENV_FILE = pathlib.Path(__file__).with_name(".env")

STATIC_URL = "https://example.com/"
JS_URL = "https://quotes.toscrape.com/js/"


def load_token() -> str:
    for line in ENV_FILE.read_text().splitlines():
        if line.startswith("CRAWL4AI_API_TOKEN="):
            return line.split("=", 1)[1].strip()
    raise SystemExit("CRAWL4AI_API_TOKEN not found in .env")


def post(path: str, payload: dict, token: str | None, timeout: int = 120):
    body = json.dumps(payload).encode()
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(BASE + path, data=body, headers=headers, method="POST")
    started = time.monotonic()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
            return resp.status, json.loads(raw or b"{}"), int((time.monotonic() - started) * 1000)
    except urllib.error.HTTPError as exc:
        return exc.code, {"error": exc.read().decode(errors="replace")[:300]}, int(
            (time.monotonic() - started) * 1000
        )


def get(path: str, timeout: int = 15):
    try:
        with urllib.request.urlopen(BASE + path, timeout=timeout) as resp:
            return resp.status, resp.read().decode(errors="replace")[:600]
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode(errors="replace")[:600]


def first_markdown(payload: dict) -> str:
    results = payload.get("results") or []
    if not results:
        return ""
    item = results[0]
    return item.get("markdown") or item.get("html") or ""


def main() -> int:
    token = load_token()
    out: dict[str, object] = {"base_url": BASE, "checks": []}

    status, body = get("/health")
    out["checks"].append(
        {"name": "health", "status": status, "passed": status == 200, "body": body}
    )

    status, payload, ms = post("/crawl", {"urls": [STATIC_URL]}, token=None, timeout=60)
    out["checks"].append(
        {
            "name": "auth_required",
            "status": status,
            "passed": status in (401, 403),
            "latency_ms": ms,
            "detail": str(payload.get("error", ""))[:160],
        }
    )

    status, payload, ms = post("/crawl", {"urls": [STATIC_URL]}, token=token, timeout=120)
    text = first_markdown(payload)
    out["checks"].append(
        {
            "name": "static_page",
            "status": status,
            "passed": status == 200 and "Example Domain" in text,
            "latency_ms": ms,
            "text_chars": len(text),
            "excerpt": text[:160].replace("\n", " "),
        }
    )

    status, payload, ms = post("/crawl", {"urls": [JS_URL]}, token=token, timeout=180)
    text = first_markdown(payload)
    out["checks"].append(
        {
            "name": "js_rendered_page",
            "status": status,
            "passed": status == 200 and "Albert Einstein" in text,
            "latency_ms": ms,
            "text_chars": len(text),
            "excerpt": text[:160].replace("\n", " "),
        }
    )

    out["passed"] = all(c["passed"] for c in out["checks"])
    print(json.dumps(out, ensure_ascii=False, indent=2))
    return 0 if out["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
