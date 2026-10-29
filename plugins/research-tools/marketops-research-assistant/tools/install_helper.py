"""What the installers ask before they download (standard library only, any Python 3.10+).

    python install_helper.py net       -> {"drop_proxy": bool, "pip_index": url|""}
    python install_helper.py browser   -> the browser Crawl4AI will use, written to
                                          <MARKETOPS_HOME>/browser.txt: "chromium" (already
                                          downloaded), "chrome" or "msedge" (installed on the
                                          computer), or "" (none: download Chromium)

net: a local proxy that answers every request with 502 broke a whole install (WorkBuddy on
Windows, 0.8.2); PyPI from China took most of half an hour. The installers use a direct
connection when the proxy fails and a direct one works, and a mirror when PyPI is slow and the
mirror is not - nothing changes when the user set PIP_INDEX_URL themselves.

browser: Chromium is a 150-300 MB download; Chrome or Edge, if the computer has one, does the
same job (Crawl4AI drives it through Playwright's channel).
"""
from __future__ import annotations

import json
import os
import sys
import time
import urllib.request

PYPI = "https://pypi.org/simple/pip/"
MIRRORS = ("https://pypi.tuna.tsinghua.edu.cn/simple", "https://mirrors.aliyun.com/pypi/simple")
SLOW_S = 4.0


def _get(url: str, proxy: bool = True, timeout: float = 8.0) -> float | None:
    """Seconds to fetch the first bytes of url, or None when it fails."""
    handlers = [] if proxy else [urllib.request.ProxyHandler({})]
    opener = urllib.request.build_opener(*handlers)
    t0 = time.time()
    try:
        with opener.open(urllib.request.Request(url, headers={"User-Agent": "marketops-installer"}),
                         timeout=timeout) as r:
            r.read(2048)
        return time.time() - t0
    except Exception:  # noqa: BLE001 - any failure is "this way does not work"
        return None


def net() -> dict:
    # the proxies Python itself will use: the environment, and on Windows the system settings too
    # (pip reads both)
    has_proxy = bool({k: v for k, v in urllib.request.getproxies().items() if k in ("http", "https", "all")})
    via_proxy = _get(PYPI) if has_proxy else None
    direct = _get(PYPI, proxy=False) if (not has_proxy or via_proxy is None) else None
    drop_proxy = has_proxy and via_proxy is None and direct is not None
    best = via_proxy if via_proxy is not None else direct
    pip_index = ""
    if not os.environ.get("PIP_INDEX_URL") and (best is None or best > SLOW_S):
        for m in MIRRORS:
            t = _get(m.rstrip("/") + "/pip/", proxy=has_proxy and not drop_proxy)
            if t is not None and (best is None or t < best / 2):
                pip_index = m
                break
    return {"drop_proxy": drop_proxy, "pip_index": pip_index, "has_proxy": has_proxy,
            "pypi_seconds": None if best is None else round(best, 1)}


def browser() -> str:
    """The first browser Playwright can start: its own Chromium, then Chrome, then Edge."""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return ""
    found = ""
    with sync_playwright() as p:
        for channel in ("chromium", "chrome", "msedge"):
            try:
                b = p.chromium.launch(headless=True, **({} if channel == "chromium" else {"channel": channel}))
                b.close()
                found = channel
                break
            except Exception:  # noqa: BLE001 - not installed or will not start: try the next
                continue
    home = os.environ.get("MARKETOPS_HOME") or os.path.join(os.path.expanduser("~"), ".marketops")
    os.makedirs(home, exist_ok=True)
    with open(os.path.join(home, "browser.txt"), "w", encoding="utf-8") as fh:
        fh.write(found)
    return found


if __name__ == "__main__":
    what = sys.argv[1] if len(sys.argv) > 1 else "net"
    print(json.dumps(net()) if what == "net" else browser())
