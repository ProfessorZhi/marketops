#!/usr/bin/env python3
"""Is this server still running the plugin code that is on disk?

An update (Update.cmd, /plugin update, a manual copy) replaces the files, but a
running MCP server keeps the modules it imported. The rc12 Paris run kept reading
"budget exhausted" for hours after the fix was on disk, because nothing said the
fix was not loaded. The fingerprint is taken once at import, which is server
start, and compared on demand: the version in plugin.json and the newest change
among the scripts.
"""
from __future__ import annotations

import json
from pathlib import Path

__all__ = ["fingerprint", "drift"]

PLUGIN = Path(__file__).resolve().parents[1]


def fingerprint() -> dict:
    version = ""
    try:
        version = json.loads((PLUGIN / ".claude-plugin" / "plugin.json").read_text(
            encoding="utf-8")).get("version", "")
    except (OSError, ValueError):
        pass
    newest = 0
    for path in (PLUGIN / "scripts").glob("*.py"):
        try:
            newest = max(newest, path.stat().st_mtime_ns)
        except OSError:
            continue
    return {"version": version, "scripts_changed_ns": newest}


STARTED = fingerprint()


def drift() -> dict | None:
    """None while the loaded code matches the disk; otherwise what changed."""
    now = fingerprint()
    if now == STARTED:
        return None
    return {"code": "PLUGIN_CODE_CHANGED_RESTART_REQUIRED",
            "loaded_version": STARTED["version"], "disk_version": now["version"],
            "detail": ("The plugin on disk changed after this server started. Its fixes are "
                       "not active until the client session (Claude Code, Codex, WorkBuddy) is "
                       "restarted. Tell the user once, then keep working; do not read old "
                       "behaviour as the updated one.")}
