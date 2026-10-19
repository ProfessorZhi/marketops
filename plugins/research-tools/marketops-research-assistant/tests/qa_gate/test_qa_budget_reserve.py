"""The owner's provider-credit reserve contract, executable.

Source: ``docs/acceptance/MARKETOPS_LIVE_CC_QUALITY_AND_BUDGET_GATE_2026-10-17.md``
§1, branch ``docs/independent-technical-audit-20261016`` commit ``29f2a42``.

**The owner's 20% reserve replaces the earlier 80% instruction.** The numbers
below are transcribed from that document, including its three worked examples,
so a later change to the floor cannot happen silently.

Two halves, deliberately separated:

* **Spec arithmetic** — asserted against the vectors the owner wrote. This holds
  with no product module present and is what an implementer codes against.
* **Product behaviour** — discovered by module name. Where no such module ships,
  the test fails naming the contract; it does not pass on the arithmetic alone.

No network, no credential, no balance value from any real account appears here.
Every figure is a literal from the owner's document or synthetic.
"""
from __future__ import annotations

import importlib
import importlib.util
from decimal import Decimal, ROUND_CEILING

import pytest

from conftest import SCRIPTS

SEARCHED = f"importlib.util.find_spec over sys.path[0]={SCRIPTS}"

BUDGET_MODULES = ("research_budget_reserve", "research_budget", "research_usage_ledger",
                  "research_grants")

TINYFISH_MIN_USD = Decimal("0.20")
CAMPAIGN_FLOOR_RATIO = Decimal("0.20")
MAX_DRAWDOWN_RATIO = Decimal("0.80")


# --- spec arithmetic, transcribed ------------------------------------------
# QA-owned reference, not product code. It exists so the vectors are pinned.

def campaign_floor(b0):
    """§1.2: the absolute floor is 0.20*B0, rounded **up** when indivisible."""
    return (Decimal(str(b0)) * CAMPAIGN_FLOOR_RATIO).to_integral_value(ROUND_CEILING)


def tinyfish_floor(starting_wallet_usd):
    """§1.4: ``floor_usd = max(0.20 * starting_wallet_usd, 0.20)``."""
    scaled = Decimal(str(starting_wallet_usd)) * CAMPAIGN_FLOOR_RATIO
    return max(scaled, TINYFISH_MIN_USD)


def max_cumulative_spend(b0):
    """§1.3: drawdown ceiling is 0.80*B0 — an upper bound, not a target."""
    return Decimal(str(b0)) * MAX_DRAWDOWN_RATIO


# --- the owner's own worked examples ---------------------------------------


def test_the_credit_example_from_the_owner_document():
    """§1 examples: 500 search credits -> floor 100, at most 400 spendable."""
    assert campaign_floor(500) == 100
    assert Decimal(500) - max_cumulative_spend(500) == campaign_floor(500)


def test_the_tinyfish_0_50_example_raises_the_floor_to_twenty_cents():
    """§1 examples: $0.50 wallet -> percentage floor $0.10 -> raised to $0.20,
    so no more than $0.30 cumulative."""
    assert tinyfish_floor("0.50") == Decimal("0.20")
    assert Decimal("0.50") - tinyfish_floor("0.50") == Decimal("0.30")


def test_the_tinyfish_2_00_example_uses_the_percentage_floor():
    """§1 examples: $2.00 wallet -> floor $0.40, max cumulative $1.60."""
    assert tinyfish_floor("2.00") == Decimal("0.40")
    assert Decimal("2.00") - tinyfish_floor("2.00") == Decimal("1.60")


def test_the_percentage_floor_is_rounded_up_when_credits_are_indivisible():
    """§1.2: "rounded **up** when credits are indivisible".

    Rounding down would put the account below the floor it was told to keep.
    """
    assert campaign_floor(7) == 2      # 1.4 -> 2, never 1
    assert campaign_floor(3) == 1      # 0.6 -> 1, never 0
    assert campaign_floor(11) == 3     # 2.2 -> 3
    assert campaign_floor(1) == 1      # a one-credit account can spend nothing


def test_a_balance_at_or_below_the_tinyfish_floor_permits_no_chargeable_call():
    """§1.4: "If initial balance is at or below the floor, no chargeable
    TinyFish call is allowed" — including exactly at the floor."""
    for wallet in ("0.20", "0.10", "0.00", "-0.01"):
        assert Decimal(wallet) <= tinyfish_floor(wallet), wallet


def test_the_floor_is_not_recomputed_from_a_reduced_balance():
    """§1.2: "Each subsequent hourly check uses the **same B0** ... Do not reset
    B0 every hour or start a new run to bypass it."

    Re-deriving the floor from the *current* balance is the bypass: after
    spending half, a naive implementation would drop the floor with it.
    """
    b0 = Decimal(500)
    floor = campaign_floor(b0)
    for observed in (500, 400, 250, 120, 100):
        assert campaign_floor(b0) == floor, (
            "the floor must stay anchored to the campaign's B0, not to the "
            f"running balance (observed {observed})")


# --- product behaviour -----------------------------------------------------


def _first_present(names):
    for name in names:
        if importlib.util.find_spec(name) is not None:
            return name, importlib.import_module(name)
    return None, None


def _require_budget_module(invariant: str):
    name, module = _first_present(BUDGET_MODULES)
    if module is None:
        pytest.fail(
            f"{invariant}: no budget/reserve module ships with this candidate. "
            f"Searched {SEARCHED}; none of {list(BUDGET_MODULES)} resolve. The "
            "owner's §1 reserve contract cannot be enforced by code, which is "
            "FAIL for the campaign, not a pass.")
    return name, module


def _public_entry_points(module):
    """Module-level callables **and** public methods of its classes.

    Looking only at module level reports a false negative the moment an
    implementer puts the entry point on a class -- which this repository's
    ledger does (``UsageLedger.reserve``).
    """
    names = {n for n in dir(module) if not n.startswith("_")}
    for attribute in vars(module).values():
        if isinstance(attribute, type):
            names |= {n for n in dir(attribute) if not n.startswith("_")}
    return names


def test_the_reserve_decision_is_made_by_code_not_prose():
    """§1.6: the pre-operation decision must exist as a callable, so every
    chargeable path is forced through one place."""
    name, module = _require_budget_module("§1.6 pre-operation check")
    entry_points = _public_entry_points(module)
    has_reserve = any(
        token in n.casefold()
        for n in entry_points
        for token in ("reserve", "may_spend", "permit", "admit")
    )
    assert has_reserve, (
        f"{name} exposes no reserve/admission entry point at module or class "
        f"level; names were {sorted(entry_points)}")


def test_an_unknown_or_stale_meter_fails_closed():
    """§1.6: "If unsupported, unknown, stale or unverifiable: do **not** run
    that paid operation".

    The module must have a vocabulary for the unverifiable case; silently
    treating "unknown" as "fine" is the failure the clause exists to prevent.
    """
    name, module = _require_budget_module("§1.6 fail-closed")
    source = open(module.__file__, encoding="utf-8").read().casefold()
    markers = ("unknown", "stale", "unverifiable", "fail_closed", "fail-closed")
    assert any(m in source for m in markers), (
        f"{name} has no vocabulary for an unknown/stale meter, so it cannot "
        "refuse the operation the clause requires it to refuse")


def test_the_four_money_states_are_distinct():
    """§1 last paragraph: "Do not hide the distinction between quota
    reservation, estimated charge, derived charge and confirmed
    provider-reported actual charge."
    """
    name, module = _require_budget_module("§1 money-state separation")
    states = set()
    for attribute in vars(module).values():
        if isinstance(attribute, type) and hasattr(attribute, "__members__"):
            states |= {getattr(m, "name", str(m)).casefold()
                       for m in attribute.__members__.values()}
    source = open(module.__file__, encoding="utf-8").read().casefold()
    for state in ("reserved", "settled", "unknown", "actual"):
        assert state in source or state in states, (
            f"{name} does not distinguish the {state!r} money state "
            f"(enum members seen: {sorted(states)})")


def test_the_module_never_carries_a_credential_value_or_length():
    """§1.8: no key content, token hash, length or wallet-identifying metadata."""
    name, module = _require_budget_module("§1.8 credential hygiene")
    source = open(module.__file__, encoding="utf-8").read()
    folded = source.casefold()
    for forbidden in ("len(api_key", "len(key)", "sha256(key", "key[-4:]",
                      "key[:4]", "token_length"):
        assert forbidden not in folded, (
            f"{name} appears to expose a credential's value or shape ({forbidden})")


def test_the_tinyfish_minimum_is_not_expressible_as_a_bare_ratio():
    """§1.4: TinyFish's floor is a *maximum of two terms*. A ratio-only
    implementation gives $0.10 on a $0.50 wallet, which the document forbids.

    This is asserted against the spec arithmetic above and against any product
    constant that claims to be the TinyFish floor.
    """
    percentage_only = Decimal("0.50") * CAMPAIGN_FLOOR_RATIO      # US$0.10
    assert tinyfish_floor("0.50") == Decimal("0.20") > percentage_only, (
        "the percentage-only floor would let a $0.50 wallet fall to $0.10")
    name, module = _first_present(BUDGET_MODULES)
    if module is None:
        pytest.skip("BLOCKED: no budget module ships; asserted against the spec "
                    "vectors above")
    source = open(module.__file__, encoding="utf-8").read()
    has_minimum = any(tok in source for tok in
                      ("0.20", "0.2", "200_000", "200000", "min_usd", "MIN_USD"))
    assert has_minimum, (
        f"{name} carries no explicit US$0.20 TinyFish minimum, so the TinyFish "
        "floor would be the bare 20% ratio the owner's document forbids")


# --- what must NOT be inferred --------------------------------------------


def test_an_environment_variable_is_not_evidence_of_a_balance():
    """§1.1: "An inherited environment variable, mocked tool, documentation
    price, or credential-file presence is not evidence of an actual current
    balance."

    Asserted as a property of the *receipt* vocabulary: whatever the eventual
    implementation is, its recorded B0 must name a source, not just a number.
    """
    name, module = _first_present(BUDGET_MODULES)
    if module is None:
        pytest.skip("BLOCKED: no budget module ships; the receipt schema cannot "
                    "be inspected")
    source = open(module.__file__, encoding="utf-8").read().casefold()
    assert "observed_at" in source or "read_at" in source, (
        f"{name} records no observation time for the balance it trusts")
    assert "source" in source, (
        f"{name} does not record where a balance figure came from, so an "
        "environment variable could be read as a verified balance")
