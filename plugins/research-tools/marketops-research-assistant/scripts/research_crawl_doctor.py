#!/usr/bin/env python3
"""Is the local Crawl4AI really working on this computer, and if not, why and how to fix it.

Crawl4AI is the default reader: free, local, renders JavaScript. "Installed" is
not "working": in the Paris field run the plugin reported READY while 95 page
reads failed on the local browser, and every new site tried it again. Here:

* ``check`` walks four steps and stops at the first that fails: the packages
  import, Playwright's Chromium is present, Chromium actually launches (in a
  separate process, so a crash cannot take the MCP server down), and, when
  research is enabled, one real page is read through the plugin's own path.
* The result is kept in the data root with its time, so status is cheap and
  every conversation on this computer sees the same answer.
* ``note_local_result`` counts runtime failures during research; three in a
  row mark Crawl4AI down for this computer (provider health), so pages go to
  TinyFish fetch instead of retrying a broken browser on every site. The user is
  told and offered the repair; it is never a silent switch.
* ``repair`` reinstalls the packages and Chromium into this install, then checks.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

__all__ = ["check", "cached", "note_local_result", "repair", "repair_command"]

PLUGIN = Path(__file__).resolve().parents[1]
INSTALL = Path(__file__).resolve().parents[3]
FRESH_FOR = timedelta(hours=6)
DOWN_AFTER = 3
#: Reason codes that say the local runtime, not the site, failed.
#: LOCAL_CRAWL_RUNTIME_FAILED also covers one site's HTTP or DNS error, so the
#: browser is launched once more before anything is marked down.
RUNTIME_FAILURES = frozenset({"LOCAL_CRAWL_RUNTIME_FAILED", "CRAWL4AI_LOCAL_NOT_INSTALLED",
                              "LOCAL_BROWSER_NOT_INSTALLED", "LOCAL_CRAWL_NOT_SAFE_TO_RUN",
                              "LOCAL_DEPENDENCY_NOT_INSTALLED"})

FIXES = {
    "PACKAGES_MISSING": "Crawl4AI is not installed in the plugin's Python. Run the repair.",
    "BROWSER_MISSING": "The Chromium browser Crawl4AI uses is missing. Run the repair.",
    "BROWSER_WONT_START": ("Chromium is installed but will not start. Run the repair; if it "
                           "still fails, security software may be blocking it: allow "
                           "<install>\\browsers in the antivirus, or install the Microsoft "
                           "Visual C++ Redistributable."),
    "PAGE_READ_FAILED": ("Chromium starts but could not read a test page through the plugin. "
                         "Check the network (a company proxy: customer_setup.py --proxy), "
                         "then run the check again."),
}


def _state_path(root: Path) -> Path:
    return Path(root) / "state" / "crawl4ai-health.json"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _read(root: Path) -> dict:
    try:
        data = json.loads(_state_path(root).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _write(root: Path, data: dict) -> None:
    path = _state_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(".tmp")
    temp.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    temp.replace(path)


def repair_command() -> list[str]:
    return [sys.executable, str(Path(__file__).resolve()), "repair"]


def _launch_probe() -> tuple[bool, str]:
    """Start headless Chromium and open a blank page, in its own process."""
    probe = ("from playwright.sync_api import sync_playwright\n"
             "with sync_playwright() as p:\n"
             "    b = p.chromium.launch(headless=True)\n"
             "    b.new_page().goto('about:blank')\n"
             "    b.close()\n"
             "print('LAUNCHED')\n")
    try:
        done = subprocess.run([sys.executable, "-c", probe], capture_output=True, text=True,
                              timeout=90, env=os.environ.copy())
    except subprocess.TimeoutExpired:
        return False, "launch timed out after 90 s"
    if done.returncode == 0 and "LAUNCHED" in done.stdout:
        return True, ""
    last = (done.stderr.strip().splitlines() or ["no error text"])[-1]
    return False, last[:160]


def check(root: Path, *, page_read=None) -> dict:
    """Run the steps; keep and return the result. ``page_read()`` -> (ok, detail) or None."""
    from research_crawl4ai_live_local import dependency_state
    result = {"checked_at": _now().isoformat(timespec="seconds"),
              "browser_path": os.environ.get("PLAYWRIGHT_BROWSERS_PATH", "")}
    state = dependency_state()
    if not (state["crawl4ai_importable"] and state["safe_egress_importable"]
            and state["research_egress_importable"]):
        result.update(status="NOT_WORKING", failed_step="PACKAGES_MISSING", detail="")
    elif not state["chromium_present"]:
        result.update(status="NOT_WORKING", failed_step="BROWSER_MISSING",
                      detail=str(state["chromium_path"])[:160])
    else:
        launched, detail = _launch_probe()
        if not launched:
            result.update(status="NOT_WORKING", failed_step="BROWSER_WONT_START", detail=detail)
        elif page_read is not None:
            _clear_down(root)  # else the page read is refused by the old mark
            read = page_read()
            if read is None:
                result.update(status="WORKING", failed_step="", detail="page read not run")
            elif read[0]:
                result.update(status="WORKING", failed_step="", detail="")
            else:
                result.update(status="NOT_WORKING", failed_step="PAGE_READ_FAILED",
                              detail=str(read[1])[:160])
        else:
            result.update(status="WORKING", failed_step="", detail="page read not run")
    if result["status"] == "NOT_WORKING":
        result["fix"] = FIXES[result["failed_step"]]
        result["repair"] = "marketops_crawl4ai_check(repair=True), or run: " + " ".join(
            f'"{part}"' for part in repair_command())
    data = _read(root)
    data["last_check"] = result
    if result["status"] == "WORKING":
        data["consecutive_runtime_failures"] = 0
        _clear_down(root)
    else:
        _mark_down(root, result["failed_step"])
    _write(root, data)
    return result


def cached(root: Path) -> dict | None:
    """The last check if it is recent; None means run one."""
    last = _read(root).get("last_check")
    try:
        when = datetime.fromisoformat(last["checked_at"])
    except (TypeError, KeyError, ValueError):
        return None
    return last if _now() - when < FRESH_FOR else None


def _mark_down(root: Path, detail: str) -> None:
    import research_provider_health as health
    health.record(root, "crawl4ai", "LOCAL_DOWN", detail=detail)


def _clear_down(root: Path) -> None:
    import research_provider_health as health
    health.clear(root, "crawl4ai")


def note_local_result(root: Path, *, ok: bool, reason_code: str = "") -> bool:
    """Count local crawler outcomes during research; True when it was just marked down."""
    data = _read(root)
    if ok:
        if data.get("consecutive_runtime_failures"):
            data["consecutive_runtime_failures"] = 0
            _write(root, data)
        return False
    if reason_code not in RUNTIME_FAILURES:
        return False  # the site refused: says nothing about this computer
    count = int(data.get("consecutive_runtime_failures") or 0) + 1
    data["consecutive_runtime_failures"] = count
    _write(root, data)
    if count < DOWN_AFTER:
        return False
    if check(root)["status"] == "WORKING":
        return False  # the browser is fine: those were the sites
    return True


def repair(root: Path | None = None) -> dict:
    """Reinstall Crawl4AI and Chromium into this install, then check again."""
    lock = PLUGIN / ("requirements-lock-crawl-local-windows.txt" if sys.platform == "win32"
                     else "requirements-lock-crawl-local.txt")
    if not lock.is_file():
        lock = PLUGIN / "requirements-crawl-local.txt"
    browsers = INSTALL / "browsers"
    env = {**os.environ, "PLAYWRIGHT_BROWSERS_PATH": str(browsers)}
    steps = [("packages", [sys.executable, "-m", "pip", "install", "--require-hashes", "-r", str(lock)]
              if "lock" in lock.name else [sys.executable, "-m", "pip", "install", "-r", str(lock)]),
             ("chromium", [sys.executable, "-m", "playwright", "install", "chromium"])]
    done = []
    for name, command in steps:
        result = subprocess.run(command, capture_output=True, text=True, env=env, timeout=1800)
        done.append({"step": name, "ok": result.returncode == 0})
        if result.returncode:
            return {"status": "REPAIR_FAILED", "steps": done,
                    "detail": (result.stderr.strip().splitlines() or [""])[-1][:200]}
    os.environ["PLAYWRIGHT_BROWSERS_PATH"] = str(browsers)
    if root is not None:
        after = check(root)
        working = after["status"] == "WORKING"
    else:
        working, detail = _launch_probe()
        after = {"browser_starts": working, "detail": detail}
    return {"status": "REPAIRED" if working else "STILL_NOT_WORKING", "steps": done,
            "check": after}


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "repair":
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        try:
            import research_config
            data_root = research_config.data_root()
        except Exception:  # noqa: BLE001 - a repair before setup still repairs
            data_root = None
        outcome = repair(data_root)
        print(json.dumps(outcome, ensure_ascii=False, indent=2))
        print("Crawl4AI is working." if outcome["status"] == "REPAIRED" else
              "Crawl4AI is still not working; send this output to whoever supports MarketOps.")
        sys.exit(0 if outcome["status"] == "REPAIRED" else 1)
    else:
        print("usage: research_crawl_doctor.py repair")
