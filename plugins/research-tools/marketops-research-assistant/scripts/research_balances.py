#!/usr/bin/env python3
"""Read what each provider account says is left, without spending research budget.

* SerpApi: the official Account API (free; it does not use a search).
* TinyFish: the official MCP ``get_wallet`` read (free; read-only).
* Serper and Jev publish no balance endpoint we can rely on. The operator
  enters what the console shows (``record_manual``) and the report estimates
  what is left from the calls made since.

Every read goes out through the managed egress, allowlisted to that one host,
and only for a provider the operator enabled. Readings are kept with their time,
so a report always says how old a balance is. No key is ever stored or logged.
"""
from __future__ import annotations

import asyncio
import json
import os
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path

__all__ = ["read_all", "record_manual", "readings"]

SERPAPI_ACCOUNT = "https://serpapi.com/account.json"
AUTOMATIC = ("serpapi", "tinyfish")
MANUAL = ("serper", "typesafe_jev")


def _path(root: Path) -> Path:
    return Path(root) / "state" / "provider-balances.json"


def readings(root: Path) -> dict:
    try:
        data = json.loads(_path(root).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _save(root: Path, provider: str, reading: dict) -> None:
    data = readings(root)
    data[provider] = reading
    path = _path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _enabled(root: Path, provider: str) -> str:
    import research_runtime_setup as runtime
    settings = runtime.config(root) or {}
    if os.environ.get("MARKETOPS_RESEARCH_ALLOW_NETWORK") == "0" or not settings.get("network_enabled"):
        return "RESEARCH_NETWORK_DISABLED"
    if provider not in settings.get("providers", ()):
        return "PROVIDER_NOT_ENABLED"
    key = {"serpapi": "SERPAPI_API_KEY", "tinyfish": "TINYFISH_API_KEY"}[provider]
    if not os.environ.get(key, "").strip():
        return "KEY_MISSING"
    return ""


def read_serpapi(root: Path) -> dict:
    import httpx2
    from research_egress import open_session
    with open_session(allowlist=frozenset({"serpapi.com"})) as connection:
        with httpx2.Client(proxy=connection.proxy.url, trust_env=False, follow_redirects=False,
                           timeout=httpx2.Timeout(20, connect=10)) as http:
            answer = http.get(SERPAPI_ACCOUNT,
                              params={"api_key": os.environ["SERPAPI_API_KEY"].strip()})
    if answer.status_code != 200:
        return {"status": "READ_FAILED", "http_status": answer.status_code}
    data = answer.json()
    fields = ("plan_name", "searches_per_month", "plan_searches_left", "extra_credits",
              "total_searches_left", "this_month_usage", "account_rate_limit_per_hour")
    return {"status": "READ", "unit": "search",
            **{f: data.get(f) for f in fields if f in data},
            "left": data.get("total_searches_left", data.get("plan_searches_left")),
            "evidence": "PROVIDER_ACCOUNT_API"}


def read_tinyfish(root: Path) -> dict:
    from research_adapter_tinyfish import wallet_observation
    from research_official_mcp import _extract
    from research_runtime_transport import mcp_session

    async def go():
        async with mcp_session("tinyfish", root=root) as session:
            return _extract(await session.call_tool("get_wallet", {}))
    parsed = asyncio.run(go())
    payload = parsed.get("tool_result", parsed) if isinstance(parsed, dict) else parsed
    wallet = wallet_observation(payload)
    if not wallet.get("shape_recognized") and isinstance(payload, dict) and payload.get("currency") == "USD":
        # Wallet balance is independently useful even when the paid-Agent rate
        # catalogue has changed shape. Never relax the Agent's strict parser.
        try:
            balance = Decimal(str(payload["available_balance"]))
            if balance.is_finite() and 0 <= balance <= 100_000_000:
                return {"status": "READ_BALANCE_ONLY", "unit": "USD", "left": str(balance),
                        "agent_step_rate_usd": None, "auto_reload_enabled": None,
                        "evidence": "PROVIDER_WALLET_READ", "agent_pricing_verified": False}
        except (KeyError, InvalidOperation, ValueError):
            pass
    return {"status": "READ" if wallet.get("shape_recognized") else "READ_UNRECOGNISED",
            "unit": "USD", "left": wallet.get("balance"),
            "auto_reload_enabled": wallet.get("auto_reload_enabled"),
            "agent_step_rate_usd": wallet.get("agent_step_rate"),
            "evidence": "PROVIDER_WALLET_READ"}


def read_all(root: Path) -> dict:
    """Read every account that can be read; keep the last reading of each."""
    out = {}
    for provider, reader in (("serpapi", read_serpapi), ("tinyfish", read_tinyfish)):
        refused = _enabled(root, provider)
        if refused:
            out[provider] = {"status": refused}
            continue
        try:
            reading = reader(root)
        except Exception as exc:  # noqa: BLE001 - a failed read is reported, never raised
            reading = {"status": "READ_FAILED", "reason": type(exc).__name__}
        reading["observed_at"] = _now()
        if reading["status"] in {"READ", "READ_BALANCE_ONLY"} and reading.get("left") is not None:
            _save(root, provider, reading)
            _refresh_health(root, provider, reading)
        out[provider] = reading
    stored = readings(root)
    for provider in MANUAL:
        out[provider] = stored.get(provider) or {
            "status": "NO_PUBLIC_BALANCE_API",
            "next": "check the provider console, then record it with "
                    "marketops_costs(action='set_balance')"}
    return out


def record_manual(root: Path, *, provider: str, left: float, unit: str, note: str = "") -> dict:
    """What the provider's console shows, entered by a person, with its time."""
    if provider not in MANUAL + AUTOMATIC:
        raise ValueError("BALANCE_PROVIDER_INVALID: " + ", ".join(MANUAL + AUTOMATIC))
    if unit not in {"credit", "search", "request", "USD"}:
        raise ValueError("BALANCE_UNIT_INVALID: credit, search, request or USD")
    if float(left) < 0:
        raise ValueError("BALANCE_INVALID: not negative")
    reading = {"status": "MANUAL", "unit": unit, "left": float(left), "observed_at": _now(),
               "note": str(note)[:120], "evidence": "OPERATOR_READ_FROM_CONSOLE"}
    _save(root, provider, reading)
    _refresh_health(root, provider, reading)
    return {"saved": provider, "reading": reading}


def _refresh_health(root: Path, provider: str, reading: dict) -> None:
    """A balance above zero lifts an out-of-credit mark; zero sets one."""
    import research_provider_health as health
    try:
        left = float(reading.get("left"))
    except (TypeError, ValueError):
        return
    if left > 0:
        health.clear(root, provider)
    else:
        health.record(root, provider, "QUOTA_EXHAUSTED", detail="account reading shows 0 left")


def warnings(root: Path) -> list[str]:
    """Plain-language warnings for balances that are low or gone."""
    out = []
    for provider, reading in readings(root).items():
        left, unit = reading.get("left"), reading.get("unit")
        try:
            left = float(left)
        except (TypeError, ValueError):
            continue
        per_month = reading.get("searches_per_month")
        low = (left <= 0 or (per_month and left <= 0.2 * float(per_month))
               or (unit == "USD" and left < 1) or (unit == "credit" and left < 200))
        if low:
            out.append(f"{provider}: {left:g} {unit} left (read {str(reading.get('observed_at'))[:16]})")
    return out
