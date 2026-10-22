"""Balance-derived money envelopes, enforced inside the existing ledger transaction.

An envelope is frozen across conversations. Refreshing a balance can reduce its
remaining allowance, never repeatedly grant another fraction of the remainder.
All reservations and unknown outcomes stay in the authoritative usage ledger.
"""
from contextlib import closing
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation, ROUND_CEILING, ROUND_FLOOR
import os
import json
from pathlib import Path
import sqlite3

FREE = frozenset({("crawl4ai", "render"), ("crawl4ai", "extract"),
                  ("tinyfish", "search"), ("tinyfish", "fetch")})
PROVIDERS = ("serper", "serpapi", "typesafe_jev", "tinyfish")
MAX_AGE_SECONDS = 7 * 24 * 3600
#: The total red line when the owner sets none: no research spends more than this
#: across all accounts without the local setup saying so (--total-usd).
DEFAULT_TOTAL_RED_LINE_USD = "500"


def policy(root):
    if root is None:
        return {}
    try:
        config = json.loads((Path(root) / "state/runtime-config.json").read_text(encoding="utf-8"))
        value = config.get("spending", {})
    except (OSError, ValueError):
        return {}
    return value if value.get("mode") == "MONEY" else {}


def _amount(value):
    try:
        amount = Decimal(str(value))
    except InvalidOperation:
        raise ValueError("MONEY_BUDGET_INVALID") from None
    if not amount.is_finite() or amount < 0:
        raise ValueError("MONEY_BUDGET_INVALID")
    return int((amount * 1_000_000).to_integral_value(ROUND_FLOOR))


def _fresh(stamp):
    try:
        when = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
        return when.tzinfo is not None and 0 <= (datetime.now(timezone.utc) - when).total_seconds() <= MAX_AGE_SECONDS
    except (ValueError, AttributeError, TypeError):
        return False


def configure(root, *, confirm=False, fraction="0.90", total_usd=None,
              provider_usd=None, observations=None, renew=False, model="RED_LINE"):
    """Local setup only; no MCP consent endpoint and no implicit renewal.

    model="RED_LINE" (default): each account keeps a red line of (1 - fraction) of
    its balance; research may spend everything above it, measured against the
    freshest balance reading. The red line moves only with the user's words
    (move_red_line). model="ENVELOPE": the older frozen share of each balance.
    """
    if model not in {"RED_LINE", "ENVELOPE"}:
        raise ValueError("MONEY_MODEL_INVALID: RED_LINE or ENVELOPE")
    import research_runtime_setup as runtime
    import research_costs
    root = Path(root).resolve()
    try:
        ratio = Decimal(str(fraction))
    except InvalidOperation:
        raise ValueError("EXPLICIT_MONEY_POLICY_REQUIRED") from None
    if confirm is not True or not ratio.is_finite() or not 0 < ratio < 1:
        raise ValueError("EXPLICIT_MONEY_POLICY_REQUIRED")
    settings = runtime.config(root)
    if not settings:
        raise ValueError("RUNTIME_CONFIG_REQUIRED")
    previous = policy(root)
    if previous and not renew:
        # Re-running setup/restarting a chat cannot replenish spent envelopes.
        for provider, amount in (provider_usd or {}).items():
            tighten(root, amount_usd=amount, provider=provider)
        if total_usd is not None:
            tighten(root, amount_usd=total_usd)
        return status(root)
    observations = observations if observations is not None else research_costs.balances(root, read=False)
    overrides = provider_usd or {}
    if set(overrides) - set(PROVIDERS):
        raise ValueError("MONEY_PROVIDER_INVALID")
    providers = {}
    for name in PROVIDERS:
        reading = observations.get(name, {})
        left = reading.get("estimated_left_now", reading.get("left"))
        unit = reading.get("unit")
        try:
            left = Decimal(str(left))
            numeric = left.is_finite() and left >= 0
        except InvalidOperation:
            numeric = False
        valid = numeric and _fresh(reading.get("observed_at")) and unit in {"USD", "credit", "search", "request"}
        if not valid:
            providers[name] = {"status": "BALANCE_UNKNOWN_OR_STALE", "budget_micros": 0,
                               "observed_at": reading.get("observed_at"), "unit": unit}
            continue
        left = max(Decimal(0), left)
        allowance = left * ratio
        # Serper: a conservative credit-value envelope; not a cash invoice.
        micros = _amount(allowance if unit == "USD" else allowance * Decimal("0.001") if name == "serper" else 0)
        if name in overrides:
            micros = min(micros, _amount(overrides[name]))
        providers[name] = {"status": "CONFIGURED", "observed_at": reading["observed_at"],
                           "unit": unit, "balance_at_start": str(left),
                           "reserve_units": str(left - allowance),
                           "quota_budget": int(allowance.to_integral_value(ROUND_FLOOR)) if unit != "USD" else None,
                           "budget_micros": micros, "balance_evidence": reading.get("evidence", "UNKNOWN"),
                           "amount_basis": "USD" if unit == "USD" else "CREDIT_VALUE" if name == "serper" else "FREE_QUOTA"}
    path = root / "state/usage-ledger.sqlite3"
    watermark = baseline_money = 0
    if path.is_file():
        with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as db:
            watermark = db.execute("SELECT COALESCE(MAX(rowid),0) FROM operations").fetchone()[0]
            baseline_money = db.execute("SELECT COALESCE(SUM(CASE WHEN charge_state IN ('RESERVED','UNKNOWN') "
                "THEN reserved_micros ELSE COALESCE(actual_micros,reserved_micros) END),0) "
                "FROM operations WHERE charge_state<>'NO_CHARGE_PROVEN'").fetchone()[0]
    ceiling = sum(x["budget_micros"] for x in providers.values())
    if total_usd is not None:
        ceiling = min(ceiling, _amount(total_usd))
    if model == "RED_LINE" and total_usd is None:
        # The account red lines bound spending; the total red line is a safety stop.
        ceiling = _amount(DEFAULT_TOTAL_RED_LINE_USD)
    settings["spending"] = {"mode": "MONEY", "model": model, "fraction": str(ratio),
        "started_at": datetime.now(timezone.utc).isoformat(), "start_rowid": watermark,
        "ledger_money_baseline": baseline_money,
        "total_budget_micros": ceiling, "providers": providers,
        "free_operations_consume_money": False, "renewal_requires_explicit_consent": True}
    settings["budget_mode"] = "MONEY"
    settings["revision"] = int(settings.get("revision", 1)) + 1
    history = runtime._path(root, "money-policy-history.jsonl")
    history.parent.mkdir(parents=True, exist_ok=True)
    with os.fdopen(os.open(history, os.O_APPEND | os.O_CREAT | os.O_WRONLY, 0o600), "a", encoding="utf-8") as handle:
        handle.write(json.dumps({"at": settings["spending"]["started_at"], "previous": previous,
                                 "new": settings["spending"]}, sort_keys=True) + "\n")
    runtime._write(runtime._path(root, runtime.CONFIG_NAME), settings)
    return status(root)


def pricing_entries():
    # Jev's three text spans are <= 4k characters each and request arguments
    # <= 64 KiB. Reserve a deliberately conservative $0.20; derive the terminal
    # charge from provider-reported input tokens at $0.042/M, free output.
    pairs = [(p, c, 0, True, "local_or_published_free_fetch") for p, c in sorted(FREE)]
    pairs += [("serper", "search", 1000, False, "https://serper.dev/"),
              ("serpapi", "search", 0, True, "observed_free_search_quota"),
              ("typesafe_jev", "judge", 200000, False, "https://typesafe.ai/")]
    return [{"provider": p, "capability": c, "unit_micros": amount,
             "free_tier": free, "quota_unit": "requests", "quota_per_call": 1,
             "source": source, "version": "money-envelope-1", "observed_at": "2026-10-22T00:00:00+00:00", "verified": True}
            for p, c, amount, free, source in pairs]


def _totals(db, project, plan):
    rows = db.execute("SELECT provider,COALESCE(SUM(CASE WHEN charge_state IN ('RESERVED','UNKNOWN') "
        "THEN reserved_micros ELSE COALESCE(actual_micros,reserved_micros) END),0), "
        "COALESCE(SUM(quota_units),0) FROM operations WHERE project=? AND rowid>? "
        "AND charge_state<>'NO_CHARGE_PROVEN' GROUP BY provider", (project, plan["start_rowid"])).fetchall()
    return {p: {"committed_micros": int(m), "units": int(u)} for p, m, u in rows}


def enforce(db, request, price, estimated, root):
    """Called before insert, inside UsageLedger.reserve's BEGIN IMMEDIATE."""
    from research_usage_ledger import LedgerRefused, usd_cap_basis
    plan = policy(root)
    if not plan:
        return
    if ((request.provider, request.capability) in FREE and price is not None
            and price.free_tier and price.verified and price.unit_micros == 0):
        return
    provider = plan["providers"].get(request.provider, {})
    from research_balances import readings
    latest = readings(Path(root)).get(request.provider, {})
    observation = latest if (latest.get("unit") == provider.get("unit") and str(latest.get("observed_at") or "") > str(provider.get("observed_at") or "")) else provider
    if provider.get("status") != "CONFIGURED" or not _fresh(observation.get("observed_at")):
        raise LedgerRefused("BUDGET_PROVIDER_CEILING", "balance unknown/stale; keep the provider reserve", dimension="money")
    if not usd_cap_basis(price)[0]:
        raise LedgerRefused("PRICE_UNKNOWN", "a money reservation requires a verified upper bound", dimension="money")
    totals = _totals(db, request.project, plan)
    current = totals.get(request.provider, {"committed_micros": 0, "units": 0})
    if plan.get("model") == "RED_LINE":
        _enforce_red_line(db, request, price, estimated, plan, provider, latest, current)
        total_cap = plan.get("total_budget_micros")
        if total_cap is not None and sum(v["committed_micros"] for v in totals.values()) + estimated > total_cap:
            raise LedgerRefused("BUDGET_PROJECT_CEILING", "total spending red line would be crossed",
                                dimension="money")
        return
    # New observations may tighten the fixed floor, never renew an envelope.
    if (latest.get("unit") == provider.get("unit") and latest.get("left") is not None
            and _fresh(latest.get("observed_at")) and latest["observed_at"] > provider["observed_at"]):
        held = db.execute("SELECT COALESCE(SUM(CASE WHEN charge_state IN ('RESERVED','UNKNOWN') "
            "THEN reserved_micros ELSE COALESCE(actual_micros,reserved_micros) END),0), COALESCE(SUM(quota_units),0) "
            "FROM operations WHERE project=? AND provider=? AND rowid>? AND charge_state<>'NO_CHARGE_PROVEN' "
            "AND (created_at>? OR charge_state IN ('RESERVED','UNKNOWN'))", (request.project, request.provider,
            plan["start_rowid"], latest["observed_at"])).fetchone()
        if latest["unit"] == "USD":
            draw = Decimal(held[0] + estimated) / 1_000_000
        else:
            draw = Decimal(held[1] + price.quota_per_call * request.units)
        if Decimal(str(latest["left"])) - draw < Decimal(provider["reserve_units"]):
            raise LedgerRefused("BUDGET_PROVIDER_CEILING", "new balance observation would breach the frozen reserve", dimension="money")
    if current["committed_micros"] + estimated > provider["budget_micros"]:
        raise LedgerRefused("BUDGET_PROVIDER_CEILING", "provider money envelope would breach its balance reserve", dimension="money")
    quota = provider.get("quota_budget")
    if quota is not None and current["units"] + price.quota_per_call * request.units > quota:
        raise LedgerRefused("BUDGET_PROVIDER_CEILING", "provider credit reserve would be breached", dimension="quota")
    if sum(v["committed_micros"] for v in totals.values()) + estimated > plan["total_budget_micros"]:
        raise LedgerRefused("BUDGET_PROJECT_CEILING", "total monetary envelope would be exceeded", dimension="money")


def _available(db, project, provider_name, provider, latest, start_rowid, current):
    """(balance now, red line, unit) from the freshest reading minus what is held since."""
    newer = (latest.get("unit") == provider.get("unit") and latest.get("left") is not None
             and _fresh(latest.get("observed_at"))
             and str(latest.get("observed_at")) > str(provider.get("observed_at") or ""))
    unit = provider.get("unit")
    if newer:
        held = db.execute("SELECT COALESCE(SUM(CASE WHEN charge_state IN ('RESERVED','UNKNOWN') "
            "THEN reserved_micros ELSE COALESCE(actual_micros,reserved_micros) END),0), COALESCE(SUM(quota_units),0) "
            "FROM operations WHERE project=? AND provider=? AND rowid>? AND charge_state<>'NO_CHARGE_PROVEN' "
            "AND (created_at>? OR charge_state IN ('RESERVED','UNKNOWN'))",
            (project, provider_name, start_rowid, latest["observed_at"])).fetchone()
        base = Decimal(str(latest["left"]))
    else:
        held = (current["committed_micros"], current["units"])
        base = Decimal(str(provider.get("balance_at_start", "0")))
    used = Decimal(held[0]) / 1_000_000 if unit == "USD" else Decimal(held[1])
    return base - used, Decimal(str(provider.get("reserve_units", "0"))), unit


def _enforce_red_line(db, request, price, estimated, plan, provider, latest, current):
    from research_usage_ledger import LedgerRefused
    left, red_line, unit = _available(db, request.project, request.provider, provider, latest,
                                      plan["start_rowid"], current)
    this_call = (Decimal(estimated) / 1_000_000 if unit == "USD"
                 else Decimal(price.quota_per_call * request.units))
    if left - this_call < red_line:
        raise LedgerRefused(
            "BUDGET_PROVIDER_CEILING",
            f"red line: {request.provider} keeps {red_line} {unit}; about {max(Decimal(0), left)} "
            f"{unit} left. Ask the user before moving the red line "
            "(marketops_costs action=set_red_line)", dimension="red_line")


def move_red_line(root, *, provider, keep, user_words=""):
    """Set how much of an account research must leave untouched.

    Raising the red line (spending less) is always allowed. Lowering it (spending
    more) needs the user's own words, which are kept in the policy history; it can
    never go below zero. A frozen ENVELOPE policy becomes RED_LINE here.
    """
    import research_runtime_setup as runtime
    import research_usage_ledger as usage
    root = Path(root).resolve()
    provider = "typesafe_jev" if provider == "jev" else provider
    try:
        new = Decimal(str(keep))
    except InvalidOperation:
        raise ValueError("RED_LINE_INVALID") from None
    if not new.is_finite() or new < 0:
        raise ValueError("RED_LINE_INVALID: zero or more")
    usage.for_root(root)
    path = root / "state/usage-ledger.sqlite3"
    with closing(sqlite3.connect(path, timeout=20)) as db:
        db.execute("BEGIN IMMEDIATE")
        settings = runtime.config(root)
        plan = settings.get("spending", {})
        if plan.get("mode") != "MONEY" or provider not in plan.get("providers", {}):
            raise ValueError("MONETARY_POLICY_NOT_CONFIGURED: run the local money setup once")
        entry = plan["providers"][provider]
        if entry.get("status") != "CONFIGURED":
            raise ValueError("BALANCE_UNKNOWN_OR_STALE: read or enter the balance first")
        old = Decimal(str(entry.get("reserve_units", "0")))
        words = " ".join(str(user_words or "").split())[:300]
        if new < old and len(words) < 4:
            raise ValueError("USER_WORDS_REQUIRED: lowering the red line spends more; quote what the "
                             "user said")
        entry["reserve_units"] = str(new)
        if plan.get("model") != "RED_LINE":
            plan["model"] = "RED_LINE"
            if plan.get("total_budget_micros") == sum(p.get("budget_micros", 0) for p in plan["providers"].values()):
                # The old sum of shares is not a red line; use the safety stop instead.
                plan["total_budget_micros"] = _amount(DEFAULT_TOTAL_RED_LINE_USD)
        runtime._write(runtime._path(root, runtime.CONFIG_NAME), settings)
        history = runtime._path(root, "money-policy-history.jsonl")
        with os.fdopen(os.open(history, os.O_APPEND | os.O_CREAT | os.O_WRONLY, 0o600), "a",
                       encoding="utf-8") as handle:
            handle.write(json.dumps({"at": datetime.now(timezone.utc).isoformat(), "red_line": {
                "provider": provider, "from": str(old), "to": str(new), "user_words": words}},
                ensure_ascii=False, sort_keys=True) + "\n")
        db.commit()
    return status(root)


def derived_cost(operation, payload):
    if operation.provider == "typesafe_jev" and operation.price_version == "money-envelope-1":
        tokens = (payload.get("usage") or {}).get("input_tokens") if isinstance(payload, dict) else None
        if type(tokens) is int and tokens >= 0:
            return Decimal(int((Decimal(tokens) * Decimal("0.042")).to_integral_value(ROUND_CEILING))) / 1_000_000
    return None


def status(root):
    import research_usage_ledger as usage
    root = Path(root).resolve()
    plan = policy(root)
    if not plan:
        return {"budget_mode": "REQUEST_COUNT", "configured": False}
    totals = {}
    path = root / "state/usage-ledger.sqlite3"
    if path.is_file():
        with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as db:
            totals = _totals(db, usage.project_for_root(root), plan)
    if plan.get("model") == "RED_LINE":
        from research_balances import readings
        if not path.is_file():
            usage.for_root(root)
        latest_all = readings(root)
        providers = {}
        with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as db:
            for name, entry in plan["providers"].items():
                if entry.get("status") != "CONFIGURED":
                    providers[name] = {"state": entry.get("status"), "unit": entry.get("unit")}
                    continue
                left, red_line, unit = _available(
                    db, usage.project_for_root(root), name, entry, latest_all.get(name, {}),
                    plan["start_rowid"], totals.get(name, {"committed_micros": 0, "units": 0}))
                room = max(Decimal(0), left - red_line)
                if unit != "USD":
                    room = room.to_integral_value(ROUND_FLOOR)  # whole searches / credits
                start_room = max(Decimal(0), Decimal(str(entry.get("balance_at_start", "0"))) - red_line)
                providers[name] = {"unit": unit, "left_now": str(left), "red_line": str(red_line),
                                   "usable_above_red_line": str(room),
                                   "state": "AT_RED_LINE" if room <= (Decimal("0.01") if unit == "USD" else 0) else
                                            "NEAR_RED_LINE" if start_room and room < start_room * Decimal("0.2")
                                            else "OK"}
        cap = plan.get("total_budget_micros")
        return {"budget_mode": "MONEY", "model": "RED_LINE", "configured": True,
                "request_limit_active": False, "free_crawl_cost_usd": "0",
                "total_red_line_usd": None if cap is None else str(Decimal(cap) / 1_000_000),
                "spent_since_policy_usd": str(Decimal(sum(v["committed_micros"] for v in totals.values())) / 1_000_000),
                "providers": providers,
                "how_to_change": "Spending stops only at an account's red line. If the user wants to "
                                 "spend more, marketops_costs(action='set_red_line', provider=..., "
                                 "keep=<amount to leave>, user_words='<what they said>').",
                "note": "Measured against the freshest balance reading; held and unknown charges count."}
    return {"budget_mode": "MONEY", "configured": True, "request_limit_active": False,
            "free_crawl_cost_usd": "0", "fraction": plan["fraction"],
            "budget_epoch_started_at": plan["started_at"],
            "total_budget_usd": str(Decimal(plan["total_budget_micros"]) / 1_000_000),
            "remaining_usd": str(Decimal(max(0, plan["total_budget_micros"] - sum(v["committed_micros"] for v in totals.values()))) / 1_000_000),
            "providers": {p: {**v, **totals.get(p, {}),
                "remaining_usd": str(Decimal(max(0, v["budget_micros"] - totals.get(p, {}).get("committed_micros", 0))) / 1_000_000),
                "balance_fresh": _fresh(v.get("observed_at"))} for p, v in plan["providers"].items()},
            "note": "Amounts include held/unknown exposure. Credit values are not cash invoices. Other applications' spending requires a refreshed balance observation."}


def tighten(root, *, amount_usd, provider=""):
    """MCP may narrow an owner-approved envelope, never replenish or widen it."""
    import research_runtime_setup as runtime
    import research_usage_ledger as usage
    root = Path(root).resolve()
    limit = _amount(amount_usd)
    provider = "typesafe_jev" if provider == "jev" else provider
    path = root / "state/usage-ledger.sqlite3"
    # Ensure the standard schema exists before taking the shared reservation lock.
    usage.for_root(root)
    with closing(sqlite3.connect(path, timeout=20)) as db:
        db.execute("BEGIN IMMEDIATE")
        settings = runtime.config(root)
        plan = settings.get("spending", {})
        if plan.get("mode") != "MONEY" or (provider and provider not in plan["providers"]):
            raise ValueError("MONETARY_ENVELOPE_NOT_CONFIGURED")
        totals = _totals(db, usage.project_for_root(root), plan)
        old = plan["providers"][provider]["budget_micros"] if provider else plan["total_budget_micros"]
        held = totals.get(provider, {}).get("committed_micros", 0) if provider else sum(v["committed_micros"] for v in totals.values())
        if limit > old:
            raise ValueError("BUDGET_INCREASE_REQUIRES_LOCAL_OWNER_POLICY")
        if limit < held:
            raise ValueError("CAP_BELOW_HELD")
        if provider:
            plan["providers"][provider]["budget_micros"] = limit
        else:
            plan["total_budget_micros"] = limit
        runtime._write(runtime._path(root, runtime.CONFIG_NAME), settings)
        db.commit()
    return status(root)
