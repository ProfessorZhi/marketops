"""Spending: every call is written down, and each account keeps a red line.

The red line is 10% of the balance the first time it was read; below it that provider
is skipped and the others carry on. ``~/.marketops/redlines.json`` is a plain file -
delete a provider's entry to set a new line from its current balance. Providers with
no balance endpoint (Jev) have no line; when they refuse for money they are skipped
until the server restarts. No total cap, no call cap.
"""
from __future__ import annotations

import json
import threading
import time
from datetime import datetime, timezone

import mo_config

RED_LINE_SHARE = 0.10
BALANCE_TTL = 600

# USD per successful call, list prices (an estimate for the cost sheet, not a bill).
PRICES = {
    ("serper", "search"): 0.001,
    ("serpapi", "search"): 0.015,
    ("deepseek", "search"): 0.00075,
    ("deepseek", "fetch"): 0.00075,
    ("tinyfish", "search"): 0.0,
    ("tinyfish", "fetch"): 0.0,
    ("tinyfish", "agent"): 0.30,
    ("crawl4ai", "fetch"): 0.0,
    ("jev", "grade"): 0.0005,
}

_lock = threading.Lock()
_balances: dict[str, tuple[float, dict]] = {}
_exhausted: dict[str, str] = {}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def record(project_dir, provider: str, action: str, ok: bool, note: str = "",
           usd: float | None = None) -> None:
    cost = (PRICES.get((provider, action), 0.0) if ok else 0.0) if usd is None else usd
    row = {"time": _now(), "provider": provider, "action": action, "ok": ok,
           "usd": round(cost, 6), "note": mo_config.scrub(note)[:200]}
    line = json.dumps(row, ensure_ascii=False) + "\n"
    with _lock:
        with open(mo_config.home() / "ledger.jsonl", "a", encoding="utf-8") as fh:
            fh.write(line)
        if project_dir is not None:
            with open(project_dir / "costs.jsonl", "a", encoding="utf-8") as fh:
                fh.write(line)


def mark_exhausted(provider: str, why: str) -> None:
    """The provider refused the key in use for money or quota: that slot is set aside and the
    next key takes over; only when no slot is left is the provider skipped."""
    if provider in mo_config.KEYS:
        if mo_config.mark_slot_used_up(provider, why):
            return
    _exhausted[provider] = why


def _read_balance(provider: str) -> dict | None:
    import httpx
    k = mo_config.key(provider)
    if not k:
        return None
    try:
        if provider == "serper":
            r = httpx.get("https://google.serper.dev/account", headers={"X-API-KEY": k}, timeout=20)
            return {"left": float(r.json()["balance"]), "unit": "credits"} if r.status_code == 200 else None
        if provider == "serpapi":
            r = httpx.get("https://serpapi.com/account.json", params={"api_key": k}, timeout=20)
            d = r.json() if r.status_code == 200 else {}
            left = d.get("total_searches_left", d.get("plan_searches_left"))
            return {"left": float(left), "unit": "searches"} if left is not None else None
        if provider == "deepseek":
            if not mo_config.official("deepseek"):
                return None   # behind a gateway the balance is the gateway's to tell, not DeepSeek's
            r = httpx.get(mo_config.base_url("deepseek") + "/user/balance",
                          headers={"Authorization": "Bearer " + k}, timeout=20)
            infos = (r.json() or {}).get("balance_infos") if r.status_code == 200 else None
            if infos:
                return {"left": float(infos[0].get("total_balance") or 0),
                        "unit": infos[0].get("currency") or "money"}
            return None
        if provider == "tinyfish":
            import mo_providers
            wallet = mo_providers.tinyfish_mcp("get_wallet", {})
            for name in ("available_balance", "balance", "credits"):
                if isinstance(wallet, dict) and wallet.get(name) is not None:
                    return {"left": float(wallet[name]), "unit": wallet.get("currency") or "USD"}
            return None
    except Exception:  # noqa: BLE001 - an unreadable balance never stops research
        return None
    return None


def balance(provider: str, fresh: bool = False) -> dict | None:
    slot = f"{provider}:{mo_config.fingerprint(mo_config.key(provider))}" if provider in mo_config.KEYS else provider
    cached = _balances.get(slot)
    if cached and not fresh and time.time() - cached[0] < BALANCE_TTL:
        return cached[1]
    reading = _read_balance(provider)
    if reading is not None:
        _balances[slot] = (time.time(), reading)
    return reading


def _lines() -> dict:
    try:
        return json.loads((mo_config.home() / "redlines.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


_SDK: list[bool] = []


def jev_ready() -> bool:
    """Jev can be used now: a key in a slot that is not set aside, credit above the red line, and
    the TypeSafe SDK installed. Every Jev call checks this, so a missing piece steps the plugin
    down to rules + agents instead of failing."""
    if not _SDK:
        import importlib.util
        _SDK.append(importlib.util.find_spec("typesafe_sdk") is not None)
    return _SDK[0] and bool(mo_config.key("jev")) and allowed("jev")[0]


def allowed(provider: str, action: str = "") -> tuple[bool, str]:
    """May this provider be called now? (False, reason) when it is under its red line."""
    if provider in mo_config.KEYS:
        if mo_config.slots(provider) and not mo_config.key(provider):
            return False, f"every {provider} key is used up or set aside (project() shows the slots)"
        _exhausted.pop(provider, None)   # a new or topped-up key brings the provider back
    if provider in _exhausted:
        return False, _exhausted[provider]
    if provider in ("crawl4ai", "http", "download", "jev") or PRICES.get((provider, action), 1) == 0:
        return True, ""  # free calls never wait on a balance
    for _ in range(len(mo_config.slots(provider)) or 1):
        reading = balance(provider)
        if reading is None:
            return True, ""
        slot = f"{provider}:{mo_config.fingerprint(mo_config.key(provider))}"   # a red line per key
        with _lock:
            lines = _lines()
            if slot not in lines:
                lines[slot] = {"baseline": reading["left"], "unit": reading["unit"],
                               "red_line": round(reading["left"] * RED_LINE_SHARE, 4), "set": _now()}
                (mo_config.home() / "redlines.json").write_text(json.dumps(lines, indent=1), encoding="utf-8")
        line = lines[slot]["red_line"]
        if reading["left"] > line:
            return True, ""
        # this key is at its red line: the next slot takes over, if there is one
        if not mo_config.mark_slot_used_up(provider, f"below red line ({reading['left']} {reading['unit']} left)"):
            return False, f"below red line ({reading['left']} {reading['unit']} left, line {line}) on every key"
    return False, "no key left above its red line"


def summary(project_dir=None) -> dict:
    source = (project_dir / "costs.jsonl") if project_dir is not None else mo_config.home() / "ledger.jsonl"
    totals: dict[str, dict] = {}
    try:
        for line in source.read_text(encoding="utf-8").splitlines():
            row = json.loads(line)
            t = totals.setdefault(row["provider"], {"calls": 0, "ok": 0, "usd": 0.0})
            t["calls"] += 1
            t["ok"] += bool(row["ok"])
            t["usd"] = round(t["usd"] + row["usd"], 4)
    except (OSError, ValueError):
        pass
    return totals


def accounts() -> dict:
    lines = _lines()
    out = {}
    for p in mo_config.configured():
        reading = balance(p) if p not in ("jev",) else None
        slot = f"{p}:{mo_config.fingerprint(mo_config.key(p))}" if mo_config.key(p) else p
        out[p] = {"balance": reading, "red_line": (lines.get(slot) or lines.get(p) or {}).get("red_line"),
                  "skipped": _exhausted.get(p) or (None if mo_config.key(p) else "every key used up"),
                  "slots": mo_config.slot_status(p)}
    return out
