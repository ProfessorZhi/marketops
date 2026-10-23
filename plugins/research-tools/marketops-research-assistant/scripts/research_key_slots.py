#!/usr/bin/env python3
"""A second key slot for the searches that run out first (Serper, SerpApi).

Each slot is one of the user's own keys: slot 1 is ``SERPER_API_KEY``, slot 2 is
``SERPER_API_KEY_2`` (same for SerpApi), from the environment or the OS keyring.
Which slot is in use is the user's choice, stored per data root, and changes only
when the user says so (``marketops_accounts(action='use_slot',
account_ref='serper:2')``). Nothing here rotates keys by itself: some providers'
terms allow one account per person, and using another account is the user's
decision, not the plugin's.

Switching sets the provider's usual variable to the chosen key for this process,
so every reader (adapters, balance reads) uses it unchanged; it then clears the
provider's out-of-credit state, which belonged to the other key, so the next
search is tried on the new key and the next balance read is that key's balance.
No key value is ever returned or logged.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path

__all__ = ["SLOTTED", "apply", "status", "use"]

SLOTTED = {"serper": "SERPER_API_KEY", "serpapi": "SERPAPI_API_KEY"}
#: Where slot 1's own key is kept once slot 2 has taken the usual variable.
_FIRST = "_MARKETOPS_SLOT1_"


def _path(root) -> Path:
    return Path(root) / "state" / "key-slots.json"


def _chosen(root) -> dict:
    try:
        data = json.loads(_path(root).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return {p: int(s) for p, s in data.get("slots", {}).items() if p in SLOTTED and s in (1, 2)}


def _key(provider: str, slot: int, env) -> str:
    name = SLOTTED[provider]
    if slot == 2:
        return (env.get(name + "_2") or "").strip()
    return (env.get(_FIRST + name) or env.get(name) or "").strip()


def apply(root, environ=None) -> dict:
    """Put each provider's chosen key in its usual variable; {provider: slot}."""
    env = os.environ if environ is None else environ
    out = {}
    for provider, name in SLOTTED.items():
        slot = _chosen(root).get(provider, 1) if root is not None else 1
        if _FIRST + name not in env and env.get(name):
            env[_FIRST + name] = env[name]
        value = _key(provider, slot, env)
        if slot == 2 and not value:
            slot, value = 1, _key(provider, 1, env)  # slot 2 has no key here: stay on 1
        if value:
            env[name] = value
        out[provider] = slot
    return out


def status(root, environ=None) -> dict:
    env = os.environ if environ is None else environ
    chosen = _chosen(root)
    return {provider: {"in_use": chosen.get(provider, 1),
                       "slots_with_a_key": [s for s in (1, 2) if _key(provider, s, env)]}
            for provider in SLOTTED}


def use(root, provider: str, slot: int, environ=None) -> dict:
    """The user's switch. Refuses a slot with no key; never returns a key."""
    env = os.environ if environ is None else environ
    if provider not in SLOTTED:
        raise ValueError("KEY_SLOT_PROVIDER_INVALID: serper or serpapi")
    if slot not in (1, 2):
        raise ValueError("KEY_SLOT_INVALID: 1 or 2")
    if not _key(provider, slot, env):
        raise ValueError(f"KEY_SLOT_EMPTY: no key for {provider} slot {slot}; store it with "
                         f"research_credentials.py configure --stdin as {provider}_2")
    path = _path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    data = {"slots": {**_chosen(root), provider: slot},
            "changed_at": datetime.now(timezone.utc).isoformat()}
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data), encoding="utf-8")
    tmp.replace(path)
    apply(root, env)
    import research_provider_health as health
    health.clear(Path(root), provider)
    return {"provider": provider, "in_use": slot, "status": status(root, env),
            "next": "read this key's balance: marketops_costs(action='balances')"}
