#!/usr/bin/env python3
"""Local Crawl4AI REST API, served from the official Python package.

Why this exists instead of only the Docker image: pulling
``unclecode/crawl4ai:0.9.5`` from this host stalled at roughly 35 KiB/s against
a 2.3 GB image. The Python package is the same official project at the same
version, and Playwright's Chromium came down from a domestic mirror in a
minute. The REST surface below mirrors the image's, so a caller does not have
to care which of the two is behind the port.

Request/response shape follows the image's ``POST /crawl``::

    POST /crawl  {"urls": ["..."], "crawler_config": {"wait_until": "networkidle"}}
    -> {"success": true, "results": [{"url", "success", "status_code", "markdown", ...}]}

The same bearer token as the compose file is required. Nothing here accepts a
token in a query string: it would end up in access logs.

Start:
    PLAYWRIGHT_BROWSERS_PATH=... python py_api.py
"""

from __future__ import annotations

import inspect
import os
import secrets
import sys
import time
import uvicorn
from fastapi import FastAPI, Header, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from crawl4ai import AsyncWebCrawler, BrowserConfig, CrawlerRunConfig

TOKEN = os.environ.get("CRAWL4AI_API_TOKEN", "").strip()
HOST = os.environ.get("CRAWL4AI_HOST", "127.0.0.1")
PORT = int(os.environ.get("CRAWL4AI_PORT", "11235"))
STARTED = time.time()

app = FastAPI(title="MarketOps Crawl4AI Local", version="0.9.5")


class CrawlRequest(BaseModel):
    urls: list[str] = Field(min_length=1)
    # Deliberately narrow: the benchmark needs a generic wait policy, not a
    # way to pass a selector that only works because the caller knows the page.
    crawler_config: dict | None = None


def _auth(authorization: str | None) -> None:
    if not TOKEN:
        raise HTTPException(status_code=503, detail="server started without CRAWL4AI_API_TOKEN")
    expected = f"Bearer {TOKEN}"
    if not authorization or not secrets.compare_digest(authorization, expected):
        raise HTTPException(status_code=401, detail="unauthorized")


DEFAULTS = {
    "wait_until": "networkidle",
    "delay_before_return_html": 1.5,
    "page_timeout": int(os.environ.get("CRAWL4AI_PAGE_TIMEOUT_MS", "30000")),
    "verbose": False,
}


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "version": "0.9.5", "uptime_s": round(time.time() - STARTED, 1)}


@app.post("/crawl")
async def crawl(req: CrawlRequest, authorization: str | None = Header(default=None)):
    _auth(authorization)
    cfg = {**DEFAULTS, **(req.crawler_config or {})}
    # Only keys the crawler understands; an unknown key would raise deep inside
    # the browser strategy and surface as a 500 rather than a rejection.
    accepted = set(inspect.signature(CrawlerRunConfig).parameters)
    run_cfg = CrawlerRunConfig(**{k: v for k, v in cfg.items() if k in accepted})
    results = []
    async with AsyncWebCrawler(config=BrowserConfig(headless=True, verbose=False)) as crawler:
        for url in req.urls:
            started = time.monotonic()
            try:
                r = await crawler.arun(url=url, config=run_cfg)
                results.append({
                    "url": url,
                    "success": bool(r.success),
                    "status_code": r.status_code,
                    "final_url": getattr(r, "redirected_url", None) or url,
                    "markdown": r.markdown or "",
                    "html": r.html or "",
                    "error": r.error_message,
                    "elapsed_s": round(time.monotonic() - started, 3),
                })
            except Exception as exc:
                results.append({
                    "url": url, "success": False, "status_code": None,
                    "final_url": url, "markdown": "", "html": "",
                    "error": f"{type(exc).__name__}: {exc}",
                    "elapsed_s": round(time.monotonic() - started, 3),
                })
    return JSONResponse({"success": all(r["success"] for r in results), "results": results})


def main() -> int:
    if not TOKEN:
        print("CRAWL4AI_API_TOKEN is not set; refusing to start an unauthenticated crawler",
              file=sys.stderr)
        return 2
    if not os.environ.get("PLAYWRIGHT_BROWSERS_PATH"):
        print("warning: PLAYWRIGHT_BROWSERS_PATH unset; Playwright will look in $HOME",
              file=sys.stderr)
    uvicorn.run(app, host=HOST, port=PORT, log_level="warning")
    return 0


if __name__ == "__main__":
    sys.exit(main())
