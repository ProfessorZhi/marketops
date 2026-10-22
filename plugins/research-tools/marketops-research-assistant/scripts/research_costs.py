#!/usr/bin/env python3
"""What each provider call cost, what each project cost, and what is left.

The usage ledger counts operations and vendor units; it deliberately holds no
dollar rate it cannot prove. This module puts a *rate card* beside it so a
person can read money: every figure says whether it is an estimate from the
card, a reading from the provider's own account, or unknown.

Rules that came from the field runs:

* A count of operations is not a cost. Serper bills a second credit when more
  than ten results are asked for, so billed units are computed per call
  (``billed_units``) and stored in the ledger.
* Free allowances are part of the price: SerpApi's free plan is 250 searches a
  month, Serper's sign-up grant is 2,500 credits once. Spend inside an allowance
  is $0 but still uses up the allowance, and the report says how much is left.
* An unknown rate stays unknown. Jev has no published per-request price here;
  its cost is reported as UNKNOWN until the operator enters one.
"""
from __future__ import annotations

import json
from collections import defaultdict
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path

__all__ = ["billed_units", "rate_card", "set_rate", "report", "DEFAULT_RATES"]

CHECKED = "2026-10-21"

#: (provider, ledger capability) -> how the vendor bills it. usd_per_unit None
#: means nobody has a rate for it; the operator can enter one with set_rate.
DEFAULT_RATES: dict[tuple[str, str], dict] = {
    ("serper", "search"): {
        "unit": "credit", "usd_per_unit": "0.001", "free_units": 2500, "free_period": "once",
        "plan": "pay-as-you-go", "source": "https://serper.dev/"},
    ("serpapi", "search"): {
        "unit": "search", "usd_per_unit": "0", "free_units": 250, "free_period": "month",
        "plan": "free", "source": "https://serpapi.com/pricing"},
    ("tinyfish", "search"): {
        "unit": "search", "usd_per_unit": "0", "free_units": None, "free_period": None,
        "plan": "free tier", "source": "https://www.tinyfish.ai/pricing"},
    ("tinyfish", "fetch"): {
        "unit": "url", "usd_per_unit": "0", "free_units": None, "free_period": None,
        "plan": "free tier", "source": "https://www.tinyfish.ai/pricing"},
    ("tinyfish", "agent_run"): {
        "unit": "step", "usd_per_unit": None, "free_units": None, "free_period": None,
        "plan": "wallet", "source": "account wallet rate (get_wallet)"},
    ("crawl4ai", "render"): {
        "unit": "page", "usd_per_unit": "0", "free_units": None, "free_period": None,
        "plan": "local", "source": "runs on this computer"},
    ("crawl4ai", "extract"): {
        "unit": "page", "usd_per_unit": "0", "free_units": None, "free_period": None,
        "plan": "local", "source": "runs on this computer"},
    # Jev lists input tokens at $0.042 per million (official case study);
    # the ledger counts requests, so this is ~3,000 input tokens per request.
    ("typesafe_jev", "judge"): {
        "unit": "request", "usd_per_unit": "0.000126", "free_units": None, "free_period": None,
        "plan": "account credits (estimate: ~3k input tokens at $0.042/M)",
        "source": "https://typesafe.ai/blog/jack-jill-jev-case-study (3k tokens/request assumed)"},
}

#: Serper bills one credit per ten results.
SERPER_RESULTS_PER_CREDIT = 10


def billed_units(provider: str, capability: str, params: dict) -> int:
    """Vendor units one call consumes, before it is dispatched."""
    if provider == "serper" and capability == "search":
        try:
            wanted = int(params.get("limit") or params.get("num") or 10)
        except (TypeError, ValueError):
            wanted = 10
        return max(1, -(-max(1, wanted) // SERPER_RESULTS_PER_CREDIT))
    return 1


def _card_path(root: Path) -> Path:
    return Path(root) / "state" / "rate-card.json"


def rate_card(root: Path) -> dict[str, dict]:
    """Defaults overlaid with the operator's own rates (their plan, their prices)."""
    card = {f"{p}/{c}": {**v, "checked": CHECKED, "origin": "default"}
            for (p, c), v in DEFAULT_RATES.items()}
    try:
        own = json.loads(_card_path(root).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        own = {}
    for key, value in (own if isinstance(own, dict) else {}).items():
        base = key.split("@", 1)[0]
        if base in card and isinstance(value, dict):
            card[key] = {**card[base], **value, "origin": "operator"}
    return card


def set_rate(root: Path, *, provider: str, capability: str, usd_per_unit: str | None = None,
             plan: str = "", free_units: int | None = None, free_period: str | None = None,
             source: str = "", account_ref: str = "") -> dict:
    """Record the operator's own rate for one provider capability."""
    key = f"{provider}/{capability}"
    if key not in rate_card(root):
        raise ValueError("RATE_UNKNOWN_CAPABILITY: one of " + ", ".join(sorted(rate_card(root))))
    if account_ref and account_ref != provider:
        from research_accounts import entries
        if account_ref not in {a["account_ref"] for a in entries(root, provider)}:
            raise ValueError("ACCOUNT_NOT_REGISTERED")
        key += "@" + account_ref
    entry: dict = {"checked": datetime.now(timezone.utc).date().isoformat()}
    if usd_per_unit is not None:
        try:
            value = Decimal(str(usd_per_unit))
        except InvalidOperation:
            raise ValueError("RATE_INVALID: a decimal number of US dollars per unit") from None
        if not value.is_finite() or value < 0:
            raise ValueError("RATE_INVALID: a nonnegative number of US dollars per unit")
        entry["usd_per_unit"] = str(value)
    if plan:
        entry["plan"] = str(plan)[:80]
    if free_units is not None:
        entry["free_units"] = int(free_units)
    if free_period is not None:
        entry["free_period"] = free_period if free_period in {"once", "month"} else None
    if source:
        entry["source"] = str(source)[:200]
    path = _card_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        own = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        own = {}
    own[key] = {**own.get(key, {}), **entry}
    path.write_text(json.dumps(own, indent=2) + "\n", encoding="utf-8")
    return {"saved": key, "rate": rate_card(root)[key]}


def _usd(units: int, rate: dict) -> Decimal | None:
    if rate.get("usd_per_unit") is None:
        return None
    return Decimal(str(rate["usd_per_unit"])) * units


def balances(root: Path, *, read: bool = True) -> dict:
    """Each account's last known balance, and what is likely left now.

    A provider reading is the authority at its time; calls recorded in the
    ledger after that time are subtracted to estimate the present balance.
    """
    import research_balances
    import research_usage_ledger as usage_ledger
    root = Path(root)
    current = research_balances.read_all(root) if read else research_balances.readings(root)
    rows = (usage_ledger.for_root(root).operations(project=usage_ledger.project_for_root(root))
            if usage_ledger.default_path(root).is_file() else [])
    card = rate_card(root)
    out = {}
    for account_ref, reading in current.items():
        provider = reading.get("provider", account_ref)
        left = reading.get("left")
        entry = {**reading}
        if left is not None and reading.get("observed_at"):
            baseline = reading.get("usage_baseline")
            since = [op for i, op in enumerate(rows)
                     if op.provider == provider and op.counts_against_caps
                     and getattr(op, "account_ref", op.provider) == account_ref
                     and getattr(op, "charge_state", "") != "NO_CHARGE_PROVEN"
                     and (research_balances.operation_key(op, i) not in baseline
                          if baseline is not None else op.created_at > reading["observed_at"])]
            amounts = [_balance_consumption(op, reading["unit"], card) for op in since]
            missing = sum(value is None for value in amounts)
            spent = sum((value for value in amounts if value is not None), Decimal(0))
            remaining = max(Decimal(0), Decimal(str(left)) - spent)
            entry["estimated_left_now"] = (None if missing else f"{remaining:.6f}"
                                            if reading["unit"] == "USD" else float(remaining))
            entry["estimated_consumed_since_reading"] = str(spent)
            entry["consumption_unknown_operations"] = missing
            entry["unresolved_charge_operations"] = sum(
                getattr(op, "charge_state", "") in {"UNKNOWN", "RESERVED"} for op in since)
            entry["balance_basis"] = "UNKNOWN" if missing else "LOCAL_USAGE_ESTIMATE"
            entry["reconciliation_note"] = (
                "Last reported balance minus recorded usage; external use, expiry, top-ups and "
                "unverified rates can cause drift. Unresolved exposure is retained, not refunded.")
            entry["calls_since_reading"] = len(since)
        out[account_ref] = entry
    return out


def _balance_consumption(op, unit, card):
    rate = _operation_rate(card, op)
    if unit == "USD":
        if op.actual_micros is not None and op.actual_source:
            return Decimal(op.actual_micros) / Decimal(1_000_000)
        return _usd(int(op.quota_units or 1), rate)
    if unit == "request":
        return Decimal(1)
    aliases = {"credit": {"credit", "credits"}, "search": {"search", "searches"}}
    if (getattr(op, "quota_unit", "") in aliases.get(unit, set())
            or rate.get("unit") == unit):
        return Decimal(int(op.quota_units or 1))
    return None


def _operation_rate(card, op):
    key = f"{op.provider}/{op.capability}"
    return card.get(key + "@" + getattr(op, "account_ref", op.provider), card.get(key, {}))


def report(root: Path, *, run_id: str | None = None, since: str = "") -> dict:
    """Spend per project, per Run and per provider, from the ledger and the rate card.

    Every amount is an estimate from the rate card unless a provider account
    reading says otherwise; amounts with no known rate are UNKNOWN, never 0.
    """
    import research_usage_ledger as usage_ledger
    root = Path(root)
    ledger = usage_ledger.for_root(root)
    project = usage_ledger.project_for_root(root)
    rows = ledger.operations(project=project) if run_id is None else \
        ledger.operations(project=project, run_id=run_id)
    if since:
        try:
            start = datetime.fromisoformat(since.replace("Z", "+00:00"))
            if start.tzinfo is None:
                raise ValueError()
        except (ValueError, TypeError):
            raise ValueError("USAGE_SINCE_REQUIRES_TIMEZONE_TIMESTAMP") from None
        rows = [r for r in rows if datetime.fromisoformat(r.created_at.replace("Z", "+00:00")) >= start]
    card = rate_card(root)
    month = datetime.now(timezone.utc).strftime("%Y-%m")
    by_provider: dict[str, dict] = defaultdict(lambda: {"calls": 0, "units": 0, "usd": Decimal(0),
                                                       "usd_unknown_calls": 0})
    by_run: dict[str, dict] = defaultdict(lambda: {"calls": 0, "usd": Decimal(0),
                                                  "usd_unknown_calls": 0})
    by_account: dict[str, dict] = defaultdict(lambda: {"calls": 0, "units": 0, "usd": Decimal(0),
                                                      "usd_unknown_calls": 0})
    allowance_used: dict[str, int] = defaultdict(int)
    for op in rows:
        if not op.counts_against_caps or op.charge_state == "NO_CHARGE_PROVEN":
            continue
        key = f"{op.provider}/{op.capability}"
        rate = _operation_rate(card, op)
        units = int(op.quota_units or 1)
        cost = _usd(units, rate)
        if op.actual_micros is not None and op.actual_source:
            cost = Decimal(op.actual_micros) / Decimal(1_000_000)
        account_ref = getattr(op, "account_ref", op.provider)
        for bucket in (by_provider[op.provider], by_run[op.run_id], by_account[account_ref]):
            bucket["calls"] += 1
            if cost is None:
                bucket["usd_unknown_calls"] += 1
            else:
                bucket["usd"] += cost
        by_provider[op.provider]["units"] += units
        by_account[account_ref]["units"] += units
        if rate.get("free_period") == "month" and op.created_at.startswith(month):
            allowance_used[key] += units
        elif rate.get("free_period") == "once":
            allowance_used[key] += units

    def money(bucket):
        out = {k: v for k, v in bucket.items() if k != "usd"}
        out["estimated_usd"] = f"{bucket['usd']:.4f}"
        out["estimated_usd_precise"] = f"{bucket['usd']:.6f}" if not bucket["usd_unknown_calls"] else None
        if bucket["usd_unknown_calls"]:
            out["estimated_usd"] += f" + UNKNOWN ({bucket['usd_unknown_calls']} calls without a rate)"
        return out

    allowances = {}
    for key, rate in card.items():
        if rate.get("free_units"):
            used = allowance_used.get(key, 0)
            allowances[key] = {"free_units": rate["free_units"], "period": rate["free_period"],
                               "used": used, "left": max(0, rate["free_units"] - used),
                               "basis": "LOCAL_LEDGER_ESTIMATE"}
    total = sum((b["usd"] for b in by_provider.values()), Decimal(0))
    unknown = sum(b["usd_unknown_calls"] for b in by_provider.values())
    return {"project": project, "run_id": run_id, "since": since or None,
            "measurement_scope": "SESSION_SINCE_CHECKPOINT" if since else "RUN_CUMULATIVE" if run_id else "PROJECT_CUMULATIVE",
            "total_estimated_usd": f"{total:.4f}" + (f" + UNKNOWN ({unknown} calls)" if unknown else ""),
            "total_estimated_usd_precise": f"{total:.6f}" if not unknown else None,
            "by_provider": {p: money(b) for p, b in sorted(by_provider.items())},
            "by_account": {a: money(b) for a, b in sorted(by_account.items())},
            "by_run": {r: money(b) for r, b in sorted(by_run.items())} if run_id is None else None,
            "free_allowances": allowances,
            "rates": {k: {f: v.get(f) for f in ("unit", "usd_per_unit", "plan", "origin", "checked")}
                      for k, v in card.items()},
            "note": ("Estimated at list price from the rate card: calls inside a free "
                     "allowance may in fact cost $0. Free-allowance use is this data "
                     "directory's count; the account itself may have been used elsewhere. "
                     "A provider account reading (action=balances) is the authority.")}
