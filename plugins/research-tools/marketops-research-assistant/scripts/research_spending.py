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
PROVIDERS = ("serper", "serpapi", "deepseek", "typesafe_jev", "tinyfish")
MAX_AGE_SECONDS = 7 * 24 * 3600
#: No total cap unless the owner sets one (--total-usd): research is bounded by each
#: account's red line, and by an account's own "out of credit" answer when its
#: balance cannot be read. A cap is an option, not the default.
DEFAULT_TOTAL_RED_LINE_USD = None
#: Share of a balance research may use under the default red line (keeps 10%).
DEFAULT_RED_LINE_FRACTION = "0.90"


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
              provider_usd=None, observations=None, renew=False, model="RED_LINE",
              limits=None):
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
    for name, amount in (limits or {}).items():
        # A limit per account in its own unit (searches, credits, USD), set before
        # the Run; it can change later with set_limit.
        if name not in providers:
            raise ValueError("MONEY_PROVIDER_INVALID")
        try:
            value = Decimal(str(amount))
        except InvalidOperation:
            raise ValueError("LIMIT_INVALID") from None
        if not value.is_finite() or value < 0:
            raise ValueError("LIMIT_INVALID")
        providers[name]["limit"] = str(value)
    if model == "RED_LINE" and total_usd is None:
        # The account red lines bound spending; there is no total unless asked for.
        ceiling = None
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
              ("deepseek", "search", 750, False, "https://api-docs.deepseek.com/guides/search/"),
              ("deepseek", "fetch", 750, False, "https://api-docs.deepseek.com/guides/search/"),
              ("typesafe_jev", "judge", 200000, False, "https://typesafe.ai/"),
              # Reserve per Agent run (about 30 steps at the published step rate);
              # the actual charge stays UNKNOWN until the wallet is read again.
              ("tinyfish", "agent_run", 500000, False, "reserve: ~30 agent steps")]
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
    if provider.get("status") == "LIMIT_ONLY":
        # No balance can be read (Jev has no balance API): the user's limit, in
        # requests, is the only ceiling, and nothing past it is spent.
        current = _totals(db, request.project, plan).get(request.provider, {"committed_micros": 0, "units": 0})
        units = getattr(price, "quota_per_call", 1) * request.units if price is not None else request.units
        if Decimal(current["units"]) + Decimal(units) > Decimal(str(provider["limit"])):
            raise LedgerRefused(
                "BUDGET_PROVIDER_CEILING",
                f"limit: {request.provider} may use {provider['limit']} requests and has used "
                f"{current['units']}. If the user wants more, marketops_costs(action='set_limit', "
                "provider=..., limit=..., user_words=...)", dimension="limit")
        return
    if plan.get("model") == "RED_LINE" and provider.get("status") != "CONFIGURED":
        # No balance reading, so no red line to keep: the account's own "out of
        # credit" answer is the floor (provider health then routes around it).
        # Refusing here stalled research with money in the account.
        return
    if plan.get("model") != "RED_LINE" and (
            provider.get("status") != "CONFIGURED" or not _fresh(observation.get("observed_at"))):
        raise LedgerRefused(
            "BUDGET_PROVIDER_CEILING",
            "balance unknown/stale; keep the provider reserve. An account with no balance API can "
            "run under a limit: marketops_costs(action='set_limit', provider=..., limit=<requests>, "
            "user_words=...)", dimension="money")
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
        raise LedgerRefused("BUDGET_PROVIDER_CEILING", "provider money envelope would breach its balance "
                            "reserve. If the user wants more: marketops_costs(action='set_limit' or "
                            "'set_red_line', ..., user_words=...)", dimension="money")
    quota = provider.get("quota_budget")
    if quota is not None and current["units"] + price.quota_per_call * request.units > quota:
        raise LedgerRefused("BUDGET_PROVIDER_CEILING", "provider credit reserve would be breached. If the "
                            "user wants more: marketops_costs(action='set_limit' or 'set_red_line', ..., "
                            "user_words=...)", dimension="quota")
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
    cap = provider.get("limit")
    if cap is not None:
        used = (Decimal(current["committed_micros"]) / 1_000_000 if unit == "USD"
                else Decimal(current["units"]))
        if used + this_call > Decimal(str(cap)):
            raise LedgerRefused(
                "BUDGET_PROVIDER_CEILING",
                f"limit: {request.provider} may use {cap} {unit} under this policy and has used "
                f"{used}. If the user wants more, marketops_costs(action='set_limit', "
                "provider=..., limit=..., user_words=...)", dimension="limit")


def _as_red_line(plan) -> None:
    """Turn a frozen ENVELOPE policy into red lines, on the user's request.

    Each account gets the default red line (10% of its balance at setup) and its old
    envelope as its limit; the user's change then applies on top.
    """
    if plan.get("model") == "RED_LINE":
        return
    plan["model"] = "RED_LINE"
    keep = 1 - Decimal(DEFAULT_RED_LINE_FRACTION)
    for entry in plan.get("providers", {}).values():
        if entry.get("status") != "CONFIGURED":
            continue
        start = Decimal(str(entry.get("balance_at_start", "0")))
        entry["reserve_units"] = str(start * keep)
        if entry.get("quota_budget") is not None:
            entry["limit"] = str(entry["quota_budget"])
        elif entry.get("unit") == "USD":
            entry["limit"] = str(Decimal(entry.get("budget_micros", 0)) / 1_000_000)
    if plan.get("total_budget_micros") == sum(p.get("budget_micros", 0) for p in plan["providers"].values()):
        plan["total_budget_micros"] = None


def _history(root, entry: dict) -> None:
    import research_runtime_setup as runtime
    history = runtime._path(root, "money-policy-history.jsonl")
    history.parent.mkdir(parents=True, exist_ok=True)
    with os.fdopen(os.open(history, os.O_APPEND | os.O_CREAT | os.O_WRONLY, 0o600), "a",
                   encoding="utf-8") as handle:
        handle.write(json.dumps({"at": datetime.now(timezone.utc).isoformat(), **entry},
                                ensure_ascii=False, sort_keys=True) + "\n")


def adopt_new_providers(root) -> list[str]:
    """Give a red line to an account whose balance became readable after setup.

    A provider enabled later (DeepSeek after the policy existed) or one whose first
    reading failed had no entry, and every call to it was refused as 'balance
    unknown'. Only absent or unknown entries are filled, with the default red line;
    an entry the user set (a limit, a red line) is never touched.
    """
    import research_runtime_setup as runtime
    root = Path(root).resolve()
    settings = runtime.config(root) or {}
    plan = settings.get("spending") or {}
    if plan.get("mode") != "MONEY" or plan.get("model") != "RED_LINE":
        return []
    from research_balances import readings
    latest, adopted = readings(root), []
    keep = 1 - Decimal(DEFAULT_RED_LINE_FRACTION)
    for name in PROVIDERS:
        entry = plan.setdefault("providers", {}).get(name)
        if entry and entry.get("status") not in {None, "BALANCE_UNKNOWN_OR_STALE"}:
            continue
        reading = latest.get(name) or {}
        try:
            left = Decimal(str(reading.get("left")))
        except InvalidOperation:
            continue
        unit = reading.get("unit")
        if not (left.is_finite() and left >= 0 and _fresh(reading.get("observed_at"))
                and unit in {"USD", "credit", "search", "request"}):
            continue
        plan["providers"][name] = {
            "status": "CONFIGURED", "observed_at": reading["observed_at"], "unit": unit,
            "balance_at_start": str(left), "reserve_units": str(left * keep),
            "quota_budget": None, "budget_micros": 0,
            "balance_evidence": reading.get("evidence", "UNKNOWN"),
            "amount_basis": "USD" if unit == "USD" else "UNITS", "adopted_after_setup": True}
        adopted.append(name)
    if adopted:
        runtime._write(runtime._path(root, runtime.CONFIG_NAME), settings)
        _history(root, {"adopted": adopted, "red_line_fraction_kept": str(keep)})
    return adopted


def set_limit(root, *, provider="", limit, user_words=""):
    """Change how much research may spend: one account (in its unit) or the total (USD).

    Lowering is always allowed (not below what is already held). Raising needs the
    user's own words, kept in the policy history. The red line still applies on top.
    """
    import research_runtime_setup as runtime
    import research_usage_ledger as usage
    root = Path(root).resolve()
    provider = "typesafe_jev" if provider == "jev" else provider
    try:
        new = Decimal(str(limit))
    except InvalidOperation:
        raise ValueError("LIMIT_INVALID") from None
    if not new.is_finite() or new < 0:
        raise ValueError("LIMIT_INVALID: zero or more")
    words = " ".join(str(user_words or "").split())[:300]
    usage.for_root(root)
    path = root / "state/usage-ledger.sqlite3"
    with closing(sqlite3.connect(path, timeout=20)) as db:
        db.execute("BEGIN IMMEDIATE")
        settings = runtime.config(root)
        plan = settings.get("spending", {})
        if plan.get("mode") != "MONEY" or (provider and provider not in plan.get("providers", {})):
            raise ValueError("MONETARY_POLICY_NOT_CONFIGURED: run the local money setup once")
        totals = _totals(db, usage.project_for_root(root), plan)
        if provider:
            entry = plan["providers"][provider]
            if entry.get("status") not in {"CONFIGURED", "LIMIT_ONLY"}:
                # No balance to measure a red line against: the limit itself becomes
                # the ceiling. Spending goes from nothing to something, so it needs the
                # user's words like any raise.
                if len(words) < 4:
                    raise ValueError("USER_WORDS_REQUIRED: this account's balance is unknown, so a "
                                     "limit lets it spend; quote what the user said")
                entry.clear()
                entry.update({"status": "LIMIT_ONLY", "unit": "request", "limit": str(new)})
                runtime._write(runtime._path(root, runtime.CONFIG_NAME), settings)
                _history(root, {"limit": {"provider": provider, "from": None, "to": str(new),
                                          "basis": "BALANCE_UNKNOWN", "user_words": words}})
                db.commit()
                return status(root)
            unit = entry.get("unit")
            held = totals.get(provider, {"committed_micros": 0, "units": 0})
            used = Decimal(held["committed_micros"]) / 1_000_000 if unit == "USD" else Decimal(held["units"])
            if plan.get("model") == "RED_LINE" and entry.get("limit") is not None:
                old = Decimal(str(entry["limit"]))
            elif plan.get("model") == "RED_LINE":
                old = None  # no limit yet: only the red line
            else:
                old = Decimal(str(entry["quota_budget"])) if entry.get("quota_budget") is not None \
                    else Decimal(entry.get("budget_micros", 0)) / 1_000_000
        else:
            unit = "USD"
            used = Decimal(sum(v["committed_micros"] for v in totals.values())) / 1_000_000
            old = None if plan.get("total_budget_micros") is None else \
                Decimal(plan["total_budget_micros"]) / 1_000_000
        if new < used:
            raise ValueError(f"LIMIT_BELOW_USED: {used} {unit} already used or held")
        # A first limit only restricts (the red line was the only bound); raising one spends more.
        if old is not None and new > old and len(words) < 4:
            raise ValueError("USER_WORDS_REQUIRED: raising a limit spends more; quote what the user said")
        _as_red_line(plan)
        if provider:
            plan["providers"][provider]["limit"] = str(new)
        else:
            plan["total_budget_micros"] = _amount(new)
        runtime._write(runtime._path(root, runtime.CONFIG_NAME), settings)
        _history(root, {"limit": {"provider": provider or "TOTAL", "unit": unit,
                                  "from": None if old is None else str(old), "to": str(new),
                                  "user_words": words}})
        db.commit()
    return status(root)


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
        _as_red_line(plan)
        entry["reserve_units"] = str(new)
        runtime._write(runtime._path(root, runtime.CONFIG_NAME), settings)
        _history(root, {"red_line": {"provider": provider, "from": str(old), "to": str(new),
                                     "user_words": words}})
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
                if entry.get("status") == "LIMIT_ONLY":
                    used = Decimal(totals.get(name, {"units": 0})["units"])
                    room = max(Decimal(0), Decimal(str(entry["limit"])) - used)
                    providers[name] = {"unit": "request", "left_now": "UNKNOWN_NO_BALANCE_API",
                                       "limit": entry["limit"], "used_under_policy": str(used),
                                       "usable_under_limit": str(room),
                                       "state": "AT_LIMIT" if room <= 0 else "OK"}
                    continue
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
                held = totals.get(name, {"committed_micros": 0, "units": 0})
                used = (Decimal(held["committed_micros"]) / 1_000_000 if unit == "USD"
                        else Decimal(held["units"]))
                if entry.get("limit") is not None:
                    room = min(room, max(Decimal(0), Decimal(str(entry["limit"])) - used))
                providers[name] = {"unit": unit, "left_now": str(left), "red_line": str(red_line),
                                   "limit": entry.get("limit"), "used_under_policy": str(used),
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
                "how_to_change": "Spending stops at an account's limit (if set) or its red line. If "
                                 "the user wants to spend more: marketops_costs(action='set_limit', "
                                 "provider=... or '' for the total, limit=..., user_words='<what they "
                                 "said>') or action='set_red_line' with keep=<amount to leave>.",
                "note": "Measured against the freshest balance reading; held and unknown charges count."}
    return {"budget_mode": "MONEY", "configured": True, "request_limit_active": False,
            "free_crawl_cost_usd": "0", "fraction": plan["fraction"],
            "budget_epoch_started_at": plan["started_at"],
            "total_budget_usd": str(Decimal(plan["total_budget_micros"]) / 1_000_000),
            "remaining_usd": str(Decimal(max(0, plan["total_budget_micros"] - sum(v["committed_micros"] for v in totals.values()))) / 1_000_000),
            "providers": {p: {**v, **totals.get(p, {}),
                "remaining_usd": str(Decimal(max(0, v["budget_micros"] - totals.get(p, {}).get("committed_micros", 0))) / 1_000_000),
                "balance_fresh": _fresh(v.get("observed_at"))} for p, v in plan["providers"].items()},
            "note": "Amounts include held/unknown exposure. Credit values are not cash invoices. Other applications' spending requires a refreshed balance observation.",
            "how_to_change": "This is the older fixed-share policy. If the user wants to spend more, "
                             "marketops_costs(action='set_limit' or 'set_red_line', ..., user_words=...) "
                             "turns it into red lines and applies the change."}


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
            raise ValueError("BUDGET_INCREASE_REQUIRES_LOCAL_OWNER_POLICY: to spend more with the "
                             "user's agreement use action='set_limit' with user_words")
        if limit < held:
            raise ValueError("CAP_BELOW_HELD")
        if provider:
            plan["providers"][provider]["budget_micros"] = limit
        else:
            plan["total_budget_micros"] = limit
        runtime._write(runtime._path(root, runtime.CONFIG_NAME), settings)
        db.commit()
    return status(root)
