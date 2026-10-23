#!/usr/bin/env python3
"""Claude Code SessionStart check: will the MarketOps research server start here?

`/plugin install` copies the plugin; it does not create the private Python runtime
the research server needs (the MCP SDK, Jev's SDK, the page reader). Without it
the server exits at launch, the session simply has no marketops_* tools, and
nothing says why. This check finds that case and says it, once, at the start of
the session: what is missing and the one installer to run. It prints nothing
when the runtime is there.

Standard library only; nothing is installed, downloaded or read from a
credential store. The same interpreter search as scripts/launch_research_mcp.py.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(os.environ.get("CLAUDE_PLUGIN_ROOT") or Path(__file__).resolve().parents[1])
RELEASES = "https://github.com/ProfessorZhi/marketops/releases"


def _usable(command: list[str]) -> bool:
    try:
        return subprocess.run([*command, "-c", "import mcp"], stdout=subprocess.DEVNULL,
                              stderr=subprocess.DEVNULL, timeout=20).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def _venv_python(home: Path) -> Path:
    return home / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")


def runtime() -> str:
    """The interpreter the server would start with, or ""."""
    explicit = os.environ.get("MARKETOPS_RESEARCH_PYTHON", "")
    if explicit:
        return explicit if os.path.isabs(explicit) and _usable([explicit]) else ""
    for home in (ROOT / ".venv", ROOT.parent / ".venv", ROOT.parent.parent / ".venv"):
        if _venv_python(home).is_file() and _usable([str(_venv_python(home))]):
            return str(_venv_python(home))
    if _usable([sys.executable]):
        return sys.executable
    for name in ("python3", "python"):
        found = shutil.which(name)
        if found and _usable([found]):
            return found
    return ""


def _command() -> str:
    try:
        config = json.loads((ROOT / ".mcp.json").read_text(encoding="utf-8"))
        return str(next(iter(config["mcpServers"].values())).get("command") or "")
    except (OSError, ValueError, KeyError, StopIteration, AttributeError):
        return ""


def message() -> str:
    """What to tell the user, or "" when the research server can start."""
    if sys.platform == "win32" and _command() == "sh":
        # The marketplace copy launches through `sh`, which Windows does not have.
        return ("MarketOps: the research tools cannot start in this Claude Code session: on "
                "Windows the plugin must be installed with its installer, not only with "
                "/plugin install. Tell the user to download the MarketOps release ZIP from "
                f"{RELEASES}, run Install.cmd -Client claude, and then start Claude Code with "
                "the plugin folder the installer prints (claude --plugin-dir ...). Do not try "
                "to install Python packages yourself, and do not do the research by other "
                "means.")
    if runtime():
        return ""
    return ("MarketOps: the research tools cannot start in this Claude Code session: no Python "
            "with the MarketOps runtime (the MCP SDK and provider SDKs) was found. Tell the user "
            f"to install it once with the installer from {RELEASES}: install.sh <folder> claude "
            "(macOS/Linux) or Install.cmd -Client claude (Windows), then restart Claude Code; or "
            "set MARKETOPS_RESEARCH_PYTHON to the absolute path of a Python that has it. Do not "
            "try to install packages yourself, and do not do the research by other means.")


if __name__ == "__main__":
    text = message()
    if text:
        print(text)
