#!/usr/bin/env python3
"""Translate the integrated usage ledger into the shape the quality scorecard reads.

Two cost accountants exist in this repository and they do not speak the same units.
`research_usage_ledger` keeps **integer micro-USD** with a `price_verified` flag and
its own charge states; W-QA's `cost-ledger.schema.json` — which the scorecard reads —
keeps **decimal USD strings** with a `meter_trust` enum. Nothing converted between
them, so "cost per accepted result" could be computed on one side and not read on the
other.

## What this bridge refuses to lose

Measured on the integration SHA `cf8ff6e` (scratch run; no network, no credential):

* with the ledger's own documented credit baseline every rate is `unit_micros=0,
  verified=False`, and `report(accepted_outputs=1)` returned
  `cost_per_accepted_output_micros = 0` while `price_evidence` read `unverified: 1`;
* with a verified operator price, one settled charge and one unreconciled charge,
  `money` was `actual_micros=1000, unknown_exposure_micros=1000` and
  `cost_per_accepted_output_micros` was `1000` — the confirmed part alone.

Neither is a crash, and the ledger's own notes say as much. But a single number a
reader takes at face value cannot carry a caveat, so the distinctions travel through
this translation as **structure** instead of prose:

* an unverified price sets the provider's `meter_trust` to `unknown`, which makes the
  scorecard report the dollars as `UNKNOWN` rather than as a figure built on a price
  nobody confirmed (owner acceptance §1.6);
* an unreconciled charge is carried as `actual: null` plus its own exposure, so the
  scorecard reports cost-per-result `PARTIAL_UNKNOWN` with `all_in_usd` beside it;
* `op_signature` is passed through, which is what makes the duplicate-charge check
  evaluable at all — QA's schema has no such column, so without this it stayed BLOCKED;
* B0 is reported only when it was supplied. It is read once at campaign start from the
  operator's local ledger; inventing one here would be the bypass §1.2 forbids.
"""
from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP
from typing import Iterable, Mapping

SCHEMA = "marketops.qa.cost_ledger.v1"
PLACES = Decimal("0.000001")
MICRO = 1_000_000

#: Integrated charge state -> the schema's enum. The two names for "dispatched, cost
#: unknown" are the one place the vocabularies genuinely differ; everything else is a
#: rename, which is why a table beats a guess at the call site.
CHARGE_STATES = {
    "RESERVED": "RESERVED",
    "SETTLED": "SETTLED",
    "FREE_TIER": "FREE_TIER",
    "NO_CHARGE_PROVEN": "NO_CHARGE_PROVEN",
    "UNKNOWN": "UNKNOWN_CHARGE_OUTSTANDING",
}
CHARGED_STATES = frozenset({"SETTLED", "FREE_TIER", "UNKNOWN_CHARGE_OUTSTANDING"})
#: Two provider vocabularies, both authoritative in their own scope: the registry and
#: the ledger route Jev as `typesafe_jev`, while W-QA's `cost-ledger.schema.json`
#: enumerates `jev`. This bridge is the one place they meet, so the translation lives
#: here rather than in either side's file.
PROVIDER_ALIASES = {"typesafe_jev": "jev"}


def usd(micros: int | None) -> str | None:
    """Micro-USD to a decimal USD string. `None` in, `None` out — never `"0"`."""
    if micros is None:
        return None
    return str((Decimal(int(micros)) / MICRO).quantize(PLACES, ROUND_HALF_UP))


def ledger_from_operations(operations: Iterable[Mapping], *, project: str,
                           baselines: Mapping[str, Mapping] | None = None,
                           meter_by_provider: Mapping[str, str] | None = None) -> dict:
    """One schema-shaped ledger from the integrated ledger's operation rows.

    `baselines` and `meter_by_provider` are the caller's, because neither is derivable
    from the rows: B0 is read at campaign start, and a provider's meter is a property
    of its account rather than of a particular call.
    """
    baselines = dict(baselines or {})
    meter_by_provider = dict(meter_by_provider or {})
    rows, providers = [], {}
    for record in operations:
        routing_key = str(record.get("provider", ""))
        # Emit the schema's spelling; keep the routing key for looking up the caller's
        # own per-provider inputs, which are keyed by whichever name that caller uses.
        provider = PROVIDER_ALIASES.get(routing_key, routing_key)
        state = CHARGE_STATES.get(str(record.get("charge_state", "")), "RESERVED")
        tier = record.get("tier")
        rows.append({
            "operation_id": str(record.get("operation_id", "")),
            "provider": provider,
            "capability_id": f"{provider}.{record.get('capability', '')}".strip("."),
            "tier": f"T{int(tier)}" if isinstance(tier, int) else "T2",
            "initiating_client": "offline_benchmark",
            "reserved": usd(record.get("reserved_micros")) or "0",
            "estimated": usd(record.get("estimated_micros")) or "0",
            "derived": usd(record.get("estimated_micros")) or "0",
            # A settled row with no confirmed actual stays null: the schema says null is
            # "not confirmed", and the one thing it must never become is zero.
            "actual": usd(record.get("actual_micros")),
            "state": state,
            "reason_code": None,
            # Not in QA's schema; carried because the scorecard's duplicate-charge
            # check has nothing to compare without it. Its own shape check ignores it.
            "op_signature": str(record.get("op_signature", "")),
            "quota_unit": str(record.get("quota_unit", "")),
            "quota_units": int(record.get("quota_units") or 0),
        })
        # The meter is the unit the ceiling is enforced in, so it comes from the
        # operation's own quota unit unless the caller knows better. Defaulting
        # silently to USD would put a vendor's credit count in the money column.
        meter = (meter_by_provider.get(provider) or meter_by_provider.get(routing_key)
                 or str(record.get("quota_unit") or "") or "usd")
        bucket = providers.setdefault(provider, {
            "operations": 0, "confirmed_usd": Decimal(0), "unknown_usd": Decimal(0),
            "quota_units": 0, "price_verified": True, "meter": meter,
        })
        bucket["operations"] += 1
        bucket["quota_units"] += int(record.get("quota_units") or 0)
        if state in {"SETTLED", "FREE_TIER"} and record.get("actual_micros") is not None:
            bucket["confirmed_usd"] += Decimal(int(record["actual_micros"])) / MICRO
        elif state == "UNKNOWN_CHARGE_OUTSTANDING":
            bucket["unknown_usd"] += Decimal(int(record.get("reserved_micros") or 0)) / MICRO
        if not record.get("price_verified", False):
            bucket["price_verified"] = False

    provider_rows, totals = [], {"confirmed": Decimal(0), "unknown": Decimal(0),
                                 "quota": {}}
    for name in sorted(providers):
        bucket = providers[name]
        # Accept a baseline under either spelling; the caller read B0 from a console
        # that may well print the vendor's short name.
        baseline = baselines.get(name) or next(
            (baselines[key] for key, alias in PROVIDER_ALIASES.items()
             if alias == name and key in baselines), {})
        provider_rows.append({
            "provider": name,
            "account_ref": str(baseline.get("account_ref") or f"B0_NOT_SUPPLIED:{name}"),
            "meter": bucket["meter"],
            "b0": str(baseline.get("b0", "")),
            "b0_source": str(baseline.get("source", "")),
            "b0_observed_at": str(baseline.get("observed_at", "")),
            "floor": str(baseline.get("floor", "")),
            "max_cumulative_drawdown": str(baseline.get("max_cumulative_drawdown", "")),
            "spendable_ceiling": str(baseline.get("spendable_ceiling", "")),
            "current_available": str(baseline.get("current_available", "")),
            "checked_at": str(baseline.get("checked_at", "")),
            # An unverified rate is not a known rate. Saying `unknown` here is what
            # stops a downstream reader treating the dollars as settled fact.
            "meter_trust": "verified" if bucket["price_verified"] else "unknown",
            "in_flight_or_unknown_exposure": str(bucket["unknown_usd"].quantize(PLACES)),
            "external_account_spend": str(baseline.get("external_account_spend", "")),
        })
        totals["confirmed"] += bucket["confirmed_usd"]
        totals["unknown"] += bucket["unknown_usd"]
        if bucket["meter"] != "usd":
            totals["quota"][bucket["meter"]] = (
                totals["quota"].get(bucket["meter"], 0) + bucket["quota_units"])

    return {
        "schema": SCHEMA,
        "campaign_id": project,
        "providers": provider_rows,
        "operations": rows,
        "totals": {
            "attempted": len(rows),
            "succeeded": sum(1 for r in rows if r["state"] in {"SETTLED", "FREE_TIER"}),
            "blocked": sum(1 for r in rows if r["state"] == "NO_CHARGE_PROVEN"),
            "unknown": sum(1 for r in rows if r["state"] == "UNKNOWN_CHARGE_OUTSTANDING"),
            "usd_estimated": str(sum((Decimal(r["estimated"]) for r in rows),
                                     Decimal(0)).quantize(PLACES)),
            "usd_actual_confirmed": str(totals["confirmed"].quantize(PLACES)),
            "usd_unreconciled": str(totals["unknown"].quantize(PLACES)),
            "accepted_rows": 0,
            "usd_per_accepted_row": None,
            "credits_per_accepted_row": None,
            "tokens_in": 0,
            "tokens_out": 0,
            "floor_breached": False,
            "quota_units": totals["quota"],
        },
    }


def operations_from_ledger(ledger, *, project: str, run_id: str | None = None) -> list[dict]:
    """The integrated ledger's own rows, as plain mappings.

    Import the ledger at the call site rather than here: this module is imported by
    the scorecard's tests on branches where the ledger does not exist yet, and a
    hard dependency would make the bridge unimportable exactly where it is needed.
    """
    rows = ledger.operations(project=project) if run_id is None else \
        ledger.operations(project=project, run_id=run_id)
    return [dict(row.__dict__) for row in rows]
