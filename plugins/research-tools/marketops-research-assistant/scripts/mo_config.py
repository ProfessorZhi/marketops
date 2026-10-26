"""Where MarketOps keeps things, and the provider keys.

Home: ``MARKETOPS_HOME`` or ``~/.marketops`` (keys.env, red lines, ledger).
Projects: ``MARKETOPS_PROJECTS`` or ``~/MarketOps`` - one plain folder per project.
Keys come from the environment first, then ``~/.marketops/keys.env`` (KEY=value lines,
written by the installer, readable only by the user). A key is never returned to the agent.
"""
from __future__ import annotations

import os
import re
from pathlib import Path

KEYS = {
    "serper": "SERPER_API_KEY",
    "serpapi": "SERPAPI_API_KEY",
    "deepseek": "DEEPSEEK_API_KEY",
    "tinyfish": "TINYFISH_API_KEY",
    "jev": "TYPESAFE_API_KEY",
}


def home() -> Path:
    path = Path(os.environ.get("MARKETOPS_HOME") or Path.home() / ".marketops")
    path.mkdir(parents=True, exist_ok=True)
    return path


def projects_root() -> Path:
    path = Path(os.environ.get("MARKETOPS_PROJECTS") or Path.home() / "MarketOps")
    path.mkdir(parents=True, exist_ok=True)
    return path


def _file_keys() -> dict[str, str]:
    path = home() / "keys.env"
    out: dict[str, str] = {}
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            m = re.match(r"\s*([A-Z0-9_]+)\s*=\s*(.*?)\s*$", line)
            if m and not line.lstrip().startswith("#"):
                out[m.group(1)] = m.group(2).strip("'\"")
    except OSError:
        pass
    return out


def key(provider: str) -> str:
    name = KEYS[provider]
    return (os.environ.get(name) or _file_keys().get(name) or "").strip()


def configured() -> list[str]:
    return [p for p in KEYS if key(p)]


def scrub(text: str) -> str:
    """Remove any configured key from text that may come back from a provider."""
    for p in KEYS:
        secret = key(p)
        if len(secret) >= 8:
            text = text.replace(secret, "[key]")
    return text
