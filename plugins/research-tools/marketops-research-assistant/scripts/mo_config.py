"""Where MarketOps keeps things, and the provider keys.

Home: ``MARKETOPS_HOME`` or ``~/.marketops`` (keys.env, red lines, ledger).
Projects: ``MARKETOPS_PROJECTS`` or ``~/MarketOps`` - one plain folder per project.
Keys come from the environment, ``~/.marketops/keys.env`` (KEY=value lines, written by the
installer, readable only by the user) and ``MARKETOPS_KEYS_FILE`` if set (e.g. a vault file).
A key is never returned to the agent. ``DEEPSEEK_BASE_URL`` (same places) points the DeepSeek
calls at a gateway such as LiteLLM; DeepSeek's own address is the default.

Several keys per provider (slots): ``TYPESAFE_API_KEY``, ``TYPESAFE_API_KEY_2`` ... ``_N`` for
any N, or several in one value separated by commas. There is no cap on the number of slots.
The first slot that is not used up is used; a slot
the provider refuses for money, quota or an invalid key is set aside for SLOT_RETRY_H hours and
the next one takes over in the same run. The key files are read again when they change, so a
new or topped-up key works without restarting anything.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import threading
import time
from pathlib import Path

SLOT_RETRY_H = 6          # a used-up slot is tried again after this (it may have been topped up)
_slot_lock = threading.Lock()
_file_cache: dict[str, tuple] = {}

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


def _read_env_file(path: Path) -> dict[str, str]:
    try:
        sig = path.stat().st_mtime_ns
    except OSError:
        return {}
    cached = _file_cache.get(str(path))
    if cached and cached[0] == sig:
        return cached[1]
    out: dict[str, str] = {}
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            m = re.match(r"\s*(?:export\s+)?([A-Z0-9_]+)\s*=\s*(.*?)\s*$", line)
            if m and not line.lstrip().startswith("#"):
                out[m.group(1)] = m.group(2).strip("'\"")
    except OSError:
        pass
    _file_cache[str(path)] = (sig, out)
    return out


BASE_URLS = {"deepseek": ("DEEPSEEK_BASE_URL", "https://api.deepseek.com")}


def base_url(provider: str) -> str:
    """Where a provider's API is called: DEEPSEEK_BASE_URL (environment or keys.env) sends the
    DeepSeek calls through a gateway such as LiteLLM; without it, DeepSeek's own address. Given
    with or without a trailing /v1."""
    name, default = BASE_URLS.get(provider, ("", ""))
    value = (os.environ.get(name) or _file_keys().get(name) or "").strip() if name else ""
    value = value.rstrip("/")
    if value.endswith("/v1"):
        value = value[:-3]
    return value or default


def official(provider: str) -> bool:
    return base_url(provider) == BASE_URLS.get(provider, ("", ""))[1]


def _file_keys() -> dict[str, str]:
    """keys.env, then MARKETOPS_KEYS_FILE - read again whenever they change."""
    out = dict(_read_env_file(home() / "keys.env"))
    extra = os.environ.get("MARKETOPS_KEYS_FILE")
    if extra:
        out.update({k: v for k, v in _read_env_file(Path(extra)).items() if v})
    return out


def slots(provider: str) -> list[str]:
    """Every key configured for a provider, in order: the key files first (they can be changed
    while running), then the environment.

    The names are ``<NAME>``, ``<NAME>_2`` ... ``<NAME>_N`` with no upper bound - every numbered
    name that is set is a slot - and one value may hold several keys separated by commas."""
    name = KEYS.get(provider)
    if not name:   # crawl4ai, plain http, downloads: nothing to configure
        return []
    pattern = re.compile(rf"^{re.escape(name)}(?:_(\d+))?$")
    out: list[str] = []
    for source in (_file_keys(), os.environ):
        numbered: list[tuple[int, str]] = []
        for slot_name, value in source.items():
            m = pattern.match(str(slot_name))
            if m:   # _2 before _10, and a bare name first
                numbered.append((int(m.group(1) or 1), str(value or "")))
        for _, value in sorted(numbered, key=lambda slot: slot[0]):
            for k in re.split(r"[,\s]+", value):
                if k.strip() and k.strip() not in out:
                    out.append(k.strip())
    return out


def fingerprint(k: str) -> str:
    return hashlib.sha256(k.encode()).hexdigest()[:8]


def _slot_state() -> dict:
    try:
        return json.loads((home() / "slots.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def key(provider: str) -> str:
    """The provider's key in use: the first slot not set aside ("" when none is left)."""
    state, now = _slot_state(), time.time()
    for k in slots(provider):
        if state.get(fingerprint(k), {}).get("until", 0) <= now:
            return k
    return ""


def mark_slot_used_up(provider: str, why: str, k: str = "") -> str:
    """The provider refused this key (money, quota, invalid): set it aside and move on.
    Returns the fingerprint of the next slot in use ("" when none is left)."""
    k = k or key(provider)
    if not k:
        return ""
    with _slot_lock:
        state = _slot_state()
        state[fingerprint(k)] = {"provider": provider, "why": why[:160], "since": time.time(),
                                 "until": time.time() + SLOT_RETRY_H * 3600}
        tmp = home() / f"slots.{os.getpid()}.{threading.get_ident()}.tmp"
        tmp.write_text(json.dumps(state, indent=1), encoding="utf-8")
        tmp.replace(home() / "slots.json")
    nxt = key(provider)
    return fingerprint(nxt) if nxt else ""


def slot_status(provider: str) -> list[dict]:
    """Each slot by fingerprint (never the key): in use, waiting, or set aside and why."""
    state, now, active = _slot_state(), time.time(), key(provider)
    out = []
    for k in slots(provider):
        st = state.get(fingerprint(k), {})
        aside = st.get("until", 0) > now
        out.append({"slot": fingerprint(k), "state": "set aside" if aside else ("in use" if k == active else "ready"),
                    **({"why": st.get("why"), "retry_at": time.strftime("%Y-%m-%d %H:%M", time.localtime(st["until"]))}
                       if aside else {})})
    return out


def configured() -> list[str]:
    return [p for p in KEYS if slots(p)]


def scrub(text: str) -> str:
    """Remove any configured key from text that may come back from a provider."""
    for p in KEYS:
        for secret in slots(p):
            if len(secret) >= 8:
                text = text.replace(secret, "[key]")
    return text
