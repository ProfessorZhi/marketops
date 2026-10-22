#!/usr/bin/env python3
"""Which way of starting Chromium works on this computer, remembered for every crawl.

Playwright's default headless browser is a separate "headless shell" binary. On a
customer's Windows machine it hung on close and was refused network access, while
the full Chromium in new-headless mode (``channel="chromium"``) and the installed
Chrome both worked. A reinstall does not change that, so instead:

* ``choose`` tries the launch modes in order (default, full Chromium, Chrome,
  Edge), each in its own process with a timeout, and keeps the first that starts,
  reads a page and closes. The answer is written next to the browsers it applies
  to (``<install>/browsers/marketops-launch.json``), so the installer, the repair
  and every MCP server on the computer use the same one.
* ``install_launch_default`` makes Playwright use that mode whenever a caller
  (Crawl4AI included, which leaves the channel unset on Windows) does not choose
  one itself. It only fills in a missing ``channel``.
"""
from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

__all__ = ["MODES", "browsers_root", "configured_mode", "probe", "choose",
           "install_launch_default"]

MODES = ("default", "chromium", "chrome", "msedge")
SETTING = "marketops-launch.json"
OVERRIDE_ENV = "MARKETOPS_BROWSER_CHANNEL"
PROBE = Path(__file__).resolve().with_name("research_browser_probe.py")
EXTERNAL_TEST_PAGE = "https://example.com/"


def browsers_root() -> Path | None:
    value = os.environ.get("PLAYWRIGHT_BROWSERS_PATH", "").strip()
    if not value or value.startswith("$" + "{") or value == "0":
        return None
    return Path(value)


def configured_mode(root: Path | None = None) -> str:
    """The mode to use: the override, else the remembered choice, else default."""
    override = os.environ.get(OVERRIDE_ENV, "").strip().lower()
    if override in MODES:
        return override
    folder = root if root is not None else browsers_root()
    if folder is None:
        return "default"
    try:
        mode = json.loads((Path(folder) / SETTING).read_text(encoding="utf-8")).get("mode")
    except (OSError, ValueError, AttributeError):
        return "default"
    return mode if mode in MODES else "default"


def _descendants(pid: int) -> list[int]:
    found, queue = [], [pid]
    while queue:
        try:
            children = subprocess.run(["pgrep", "-P", str(queue.pop())], capture_output=True,
                                      text=True, timeout=10).stdout.split()
        except (OSError, subprocess.SubprocessError):
            break
        for child in children:
            found.append(int(child))
            queue.append(int(child))
    return found


def _kill_tree(process: subprocess.Popen) -> None:
    try:
        if sys.platform == "win32":
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(process.pid)],
                           capture_output=True, timeout=30)
        else:
            # Playwright starts the browser in a process group of its own, so the
            # probe's group alone would leave a hung browser behind.
            for pid in _descendants(process.pid):
                try:
                    os.kill(pid, signal.SIGKILL)
                except OSError:
                    pass
            os.killpg(process.pid, signal.SIGKILL)
    except (OSError, subprocess.SubprocessError):
        process.kill()


def probe(mode: str, *, python: str | None = None, env: dict | None = None,
          timeout: int = 60, external: str = EXTERNAL_TEST_PAGE) -> dict:
    """Start, read and close in one mode; ok only when all three happen in time."""
    command = [python or sys.executable, "-I", str(PROBE), mode, external]
    started = time.monotonic()
    process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                               text=True, env=env if env is not None else os.environ.copy(),
                               start_new_session=sys.platform != "win32")
    try:
        stdout, stderr = process.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        _kill_tree(process)
        process.communicate()
        return {"mode": mode, "ok": False, "external_ok": False,
                "detail": f"no answer within {timeout} s (the browser hung, often on close)",
                "seconds": round(time.monotonic() - started, 1)}
    report = {}
    for line in reversed(stdout.splitlines()):
        try:
            report = json.loads(line)
            break
        except ValueError:
            continue
    ok = bool(report.get("launched") and report.get("local_page") and report.get("closed"))
    detail = "" if ok else ((stderr.strip().splitlines() or ["no error text"])[-1][:160])
    return {"mode": mode, "ok": ok, "external_ok": bool(report.get("external_page")),
            "detail": detail or report.get("external_error", ""),
            "seconds": round(time.monotonic() - started, 1)}


def choose(*, root: Path | None = None, python: str | None = None, env: dict | None = None,
           timeout: int = 60) -> dict:
    """Find a working mode, remember it, and say what was tried.

    A mode that also reads a public page beats one that only reads the local test
    page; without internet the local page decides.
    """
    folder = root if root is not None else browsers_root()
    first = configured_mode(folder)
    order = [first] + [mode for mode in MODES if mode != first]
    tried, fallback = [], None
    for mode in order:
        result = probe(mode, python=python, env=env, timeout=timeout)
        tried.append(result)
        if result["ok"] and result["external_ok"]:
            fallback = result
            break
        if result["ok"] and fallback is None:
            fallback = result
    chosen = fallback["mode"] if fallback else ""
    if chosen and folder is None:
        os.environ[OVERRIDE_ENV] = chosen  # nowhere to keep it: this process at least
    if chosen and folder is not None:
        try:
            Path(folder).mkdir(parents=True, exist_ok=True)
            (Path(folder) / SETTING).write_text(json.dumps({
                "mode": chosen, "checked_at": datetime.now(timezone.utc).isoformat(
                    timespec="seconds"),
                "tried": tried}, indent=2) + "\n", encoding="utf-8")
        except OSError:
            pass
    return {"mode": chosen, "tried": tried}


def install_launch_default() -> str:
    """Make Playwright use the remembered mode when a caller sets no channel."""
    mode = configured_mode()
    if mode == "default":
        return mode
    try:
        from playwright.async_api import BrowserType as AsyncBrowserType
        from playwright.sync_api import BrowserType as SyncBrowserType
    except ImportError:
        return mode
    for cls in (AsyncBrowserType, SyncBrowserType):
        for name in ("launch", "launch_persistent_context"):
            original = getattr(cls, name)
            if getattr(original, "_marketops_channel", False):
                continue

            def wrapper(self, *args, __original=original, **kwargs):
                if not kwargs.get("channel") and not kwargs.get("executable_path"):
                    current = configured_mode()
                    if current != "default":
                        kwargs["channel"] = current
                return __original(self, *args, **kwargs)

            wrapper._marketops_channel = True
            setattr(cls, name, wrapper)
    return mode


if __name__ == "__main__":
    print(json.dumps(choose(), indent=2))
