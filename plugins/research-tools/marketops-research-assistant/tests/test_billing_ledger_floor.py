"""The owner's campaign floor, asserted against the ledger that enforces it.

Source of the rule: ``docs/acceptance/MARKETOPS_LIVE_CC_QUALITY_AND_BUDGET_GATE_2026-10-17.md``
§1, branch ``docs/independent-technical-audit-20261016`` commit ``29f2a42``; coordination copy
``docs/wave1-coordination/BUDGET-FLOOR.md``.

The arithmetic is ``floor = ceil(0.20 * B0)`` in the meter B0 was read in, and for a TinyFish
wallet ``max(0.20 * starting_wallet_usd, US$0.20)`` — the explicit minimum is what stops a bare
ratio from letting a US$0.50 wallet fall to US$0.10.

**No balance from any real account appears here.** Every B0 below is synthetic, and the module
holds no balance at all: it does arithmetic on a reading the caller supplies and refuses when that
reading is not evidence. Offline throughout — no network, no credential, no billable operation.
"""
from __future__ import annotations

import subprocess
import sys
import tempfile
import threading
import unittest
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import research_grants as grants
import research_usage_ledger as usage

NOW = datetime(2026, 10, 17, 9, 0, tzinfo=timezone.utc)
USD = usage.USD_MICROS
USD_MICROS = int(usage.MICRO)          # micro-USD in one US dollar
CREDIT = usage.MeterReadingSource.VENDOR_ACCOUNT_READ


#: Distinguishes "the test did not say" from "the test said None", because the
#: second is a case under test and `or` would have quietly replaced it with now().
_UNSET = object()


def baseline(*, provider="serper", account_ref="serper-main", meter="credits", units=500,
             source=CREDIT, observed_at=_UNSET, campaign_id="wave1-qa"):
    return usage.CampaignBaseline(
        provider=provider, account_ref=account_ref, meter=meter, units=units,
        observed_at=NOW.isoformat() if observed_at is _UNSET else observed_at,
        source=source, campaign_id=campaign_id)


def floor_for(*, provider="serper", account_ref="serper-main", meter="credits", units=500,
              source=CREDIT, observed_at=_UNSET, campaign_id="wave1-qa", **floor_kwargs):
    """A floor over a credit meter unless the caller names another provider/meter.

    Extra keyword arguments go to :class:`CampaignFloor` (ratio, tolerance_units,
    max_age_seconds), so a test cannot accidentally pass a baseline field where a
    floor field belongs.
    """
    return usage.CampaignFloor(
        baseline(provider=provider, account_ref=account_ref, meter=meter, units=units,
                 source=source, observed_at=observed_at, campaign_id=campaign_id),
        **floor_kwargs)


def tinyfish_wallet(units: int, **kwargs):
    """A TinyFish wallet B0 given in micro-USD."""
    return floor_for(provider="tinyfish", account_ref="tinyfish-main", meter=USD,
                     units=units, **kwargs)


def usd_wallet(units: int, *, provider="serpapi", account_ref="serpapi-main", **kwargs):
    """A non-TinyFish USD meter, where only the percentage floor applies."""
    return floor_for(provider=provider, account_ref=account_ref, meter=USD, units=units, **kwargs)


# --- the owner's own worked examples, through the product code -------------


class TestTheOwnersExamples(unittest.TestCase):
    def test_the_credit_example_500_gives_floor_100_and_400_spendable(self):
        floor = floor_for(units=500)
        self.assertEqual(floor.floor_units, 100)
        self.assertEqual(floor.max_drawdown_units, 400)
        self.assertEqual(floor.spendable_units, 400)

    def test_the_tinyfish_half_dollar_wallet_raises_the_floor_to_twenty_cents(self):
        floor = tinyfish_wallet(int(Decimal("0.50") * USD_MICROS))
        self.assertEqual(floor.floor_units, int(Decimal("0.20") * USD_MICROS))
        self.assertEqual(floor.spendable_units, int(Decimal("0.30") * USD_MICROS))
        # A bare ratio would have left only 0.20 * 0.50 = US$0.10 in the wallet.
        self.assertGreater(floor.floor_units, int(Decimal("0.10") * USD_MICROS))

    def test_the_tinyfish_two_dollar_wallet_uses_the_percentage_floor(self):
        floor = tinyfish_wallet(2 * USD_MICROS)
        self.assertEqual(floor.floor_units, int(Decimal("0.40") * USD_MICROS))
        self.assertEqual(floor.max_drawdown_units, int(Decimal("1.60") * USD_MICROS))

    def test_the_us_dollar_minimum_is_attached_to_the_wallet_not_to_every_meter(self):
        # §1.4 attaches US$0.20 to a TinyFish *wallet*; a credit account gets the
        # ratio and nothing else, and a non-TinyFish USD meter likewise.
        self.assertEqual(floor_for(meter="searches", provider="serpapi",
                                   account_ref="serpapi-main", units=100).floor_units, 20)
        self.assertEqual(floor_for(meter="requests", units=3).floor_units, 1)
        self.assertEqual(usd_wallet(USD_MICROS).floor_units, 200_000)   # US$1.00 -> US$0.20

    def test_the_constants_are_the_owners_figures(self):
        self.assertEqual(usage.TINYFISH_MINIMUM_USD, Decimal("0.20"))
        self.assertEqual(usage.TINYFISH_MINIMUM_MICROS, 200_000)
        self.assertEqual(usage.CAMPAIGN_FLOOR_RATIO, Decimal("0.20"))
        self.assertEqual(usage.CAMPAIGN_MAX_DRAWDOWN_RATIO, Decimal("0.80"))


class TestIndivisibleRounding(unittest.TestCase):
    def test_an_indivisible_meter_rounds_the_floor_up(self):
        for b0, expected in ((7, 2), (3, 1), (11, 3), (1, 1), (0, 0), (5, 1), (500, 100)):
            with self.subTest(b0=b0):
                self.assertEqual(floor_for(units=b0).floor_units, expected)

    def test_a_rounding_down_implementation_would_show_up_here(self):
        # 1.4 -> 2, never 1: rounding down leaves the account under the floor.
        self.assertGreater(floor_for(units=7).floor_units, 1)
        self.assertGreater(floor_for(units=3).floor_units, 0)

    def test_a_micro_usd_floor_rounds_up_to_the_micro(self):
        self.assertEqual(usd_wallet(5).floor_units, 1)      # 1.0 -> 1
        self.assertEqual(usd_wallet(6).floor_units, 2)      # 1.2 -> 2
        self.assertEqual(usd_wallet(9).floor_units, 2)      # 1.8 -> 2

    def test_the_tinyfish_minimum_binds_just_above_the_percentage_floor(self):
        b0 = 200_001                        # percentage floor 40_001; minimum 200_000
        floor = tinyfish_wallet(b0)
        self.assertEqual(floor.floor_units, usage.TINYFISH_MINIMUM_MICROS)
        self.assertEqual(floor.spendable_units, 1)
        # One micro lower and the percentage floor is already 40_000, still under
        # the minimum, so the minimum keeps binding.
        self.assertEqual(tinyfish_wallet(199_999).floor_units, usage.TINYFISH_MINIMUM_MICROS)


class TestLowAndUnverifiableBalances(unittest.TestCase):
    def test_a_balance_at_or_below_the_tinyfish_floor_permits_nothing(self):
        for wallet in ("0.20", "0.10", "0.00"):
            with self.subTest(wallet=wallet):
                units = int(Decimal(wallet) * USD_MICROS)
                decision = usage.CampaignBudget(tinyfish_wallet(units)).admit(spend_units=1)
                self.assertFalse(decision.allowed)
                self.assertEqual(decision.verdict, usage.BudgetVerdict.BLOCKED)
                self.assertEqual(decision.code, "CAMPAIGN_B0_AT_OR_BELOW_FLOOR")
                self.assertEqual(decision.spendable_units, 0)

    def test_a_negative_reading_is_refused_at_construction(self):
        with self.assertRaises(usage.LedgerRefused) as ctx:
            tinyfish_wallet(-10_000)
        self.assertEqual(ctx.exception.code, "CAMPAIGN_BASELINE_INVALID")

    def test_a_one_credit_account_can_spend_nothing(self):
        floor = floor_for(units=1)
        self.assertEqual(floor.floor_units, 1)
        self.assertEqual(usage.CampaignBudget(floor).admit(
            spend_units=1).code, "CAMPAIGN_B0_AT_OR_BELOW_FLOOR")

    def test_every_non_evidence_source_is_not_captured_rather_than_authorized(self):
        for source in (usage.MeterReadingSource.ENVIRONMENT_VARIABLE,
                       usage.MeterReadingSource.DOCUMENTATION,
                       usage.MeterReadingSource.MOCK,
                       usage.MeterReadingSource.CREDENTIAL_FILE_PRESENCE,
                       usage.MeterReadingSource.UNKNOWN):
            with self.subTest(source=source.value):
                self.assertFalse(source.is_evidence)
                budget = usage.CampaignBudget(
                    usage.CampaignFloor(baseline(units=500, source=source)))
                decision = budget.admit(spend_units=1)
                self.assertFalse(decision.allowed)
                self.assertEqual(decision.verdict, usage.BudgetVerdict.NOT_CAPTURED)
                self.assertEqual(decision.code, "CAMPAIGN_BASELINE_NOT_EVIDENCE")

    def test_the_two_evidenced_sources_are_accepted(self):
        for source in (usage.MeterReadingSource.VENDOR_ACCOUNT_READ,
                       usage.MeterReadingSource.OFFICIAL_READONLY_ENDPOINT):
            with self.subTest(source=source.value):
                self.assertTrue(source.is_evidence)
                self.assertTrue(usage.CampaignBudget(usage.CampaignFloor(
                    baseline(units=500, source=source))).admit(spend_units=1).allowed)

    def test_a_baseline_without_a_useful_observation_time_is_refused(self):
        for observed_at in ("", "yesterday", "2026-10-17T09:00:00", None, 12345):
            with self.subTest(observed_at=observed_at):
                with self.assertRaises(usage.LedgerRefused) as ctx:
                    baseline(observed_at=observed_at)
                self.assertEqual(ctx.exception.code, "CAMPAIGN_BASELINE_INVALID")

    def test_an_incomplete_baseline_is_refused(self):
        for override in ({"units": -1}, {"units": 1.5}, {"meter": ""}, {"provider": ""},
                         {"account_ref": ""}, {"campaign_id": ""}, {"campaign_id": "has space"}):
            with self.subTest(override=override):
                fields = dict(provider="serper", account_ref="serper-main", meter="credits",
                              units=500, observed_at=NOW.isoformat(), source=CREDIT,
                              campaign_id="wave1-qa")
                fields.update(override)
                with self.assertRaises(usage.LedgerRefused) as ctx:
                    usage.CampaignBaseline(**fields)
                self.assertEqual(ctx.exception.code, "CAMPAIGN_BASELINE_INVALID")

    def test_a_stale_reading_is_not_captured_when_freshness_is_required(self):
        decision = usage.CampaignBudget(floor_for(units=500, max_age_seconds=3600)).admit(
            spend_units=1, now=NOW + timedelta(hours=5))
        self.assertEqual(decision.verdict, usage.BudgetVerdict.NOT_CAPTURED)
        self.assertEqual(decision.code, "CAMPAIGN_BASELINE_STALE")
        # A campaign baseline is frozen for the campaign, so freshness is opt-in.
        self.assertTrue(usage.CampaignBudget(floor_for(units=500)).admit(
            spend_units=1, now=NOW + timedelta(days=30)).allowed)

    def test_a_bad_ratio_or_allowance_is_refused(self):
        for kwargs, code in (({"ratio": Decimal("0")}, "CAMPAIGN_FLOOR_INVALID"),
                             ({"ratio": Decimal("1")}, "CAMPAIGN_FLOOR_INVALID"),
                             ({"tolerance_units": -1}, "CAMPAIGN_FLOOR_INVALID"),
                             ({"max_age_seconds": 0}, "CAMPAIGN_FLOOR_INVALID")):
            with self.subTest(kwargs=kwargs):
                with self.assertRaises(usage.LedgerRefused) as ctx:
                    floor_for(units=500, **kwargs)
                self.assertEqual(ctx.exception.code, code)


class TestTheFloorIsAnchoredToAFrozenB0(unittest.TestCase):
    def test_the_floor_does_not_follow_a_reduced_balance(self):
        """§1.2: the same B0 throughout. Re-deriving it per check *is* the bypass."""
        floor = floor_for(units=500)
        budget = usage.CampaignBudget(floor)
        for spent in (0, 100, 250, 380, 399):
            with self.subTest(spent=spent):
                self.assertEqual(budget.admit(spend_units=1, spent_units=spent).floor_units, 100)

    def test_the_baseline_cannot_be_rewritten_in_place(self):
        floor = floor_for(units=500)
        with self.assertRaises(Exception):
            floor.baseline.units = 100          # frozen: there is no setter
        self.assertEqual(floor.floor_units, 100)

    def test_a_rebuilt_lower_reading_is_a_different_campaign_not_a_drift(self):
        original = floor_for(units=500)
        rebuilt = floor_for(units=120)
        self.assertEqual(original.floor_units, 100)
        self.assertEqual(rebuilt.floor_units, 24)
        self.assertNotEqual(original.baseline, rebuilt.baseline)

    def test_other_users_of_the_account_are_counted(self):
        """§1.2: account for usage by other account users as well."""
        budget = usage.CampaignBudget(floor_for(units=500), external_spend_units=395)
        decision = budget.admit(spend_units=10)
        self.assertEqual(decision.verdict, usage.BudgetVerdict.BLOCKED)
        self.assertEqual(decision.code, "CAMPAIGN_FLOOR_WOULD_BREACH")

    def test_an_unreconciled_unknown_charge_is_counted(self):
        # B0 500, floor 100 -> 400 spendable. Committing 400 leaves exactly the floor,
        # so the operation that would land on top of an unknown charge is refused.
        budget = usage.CampaignBudget(floor_for(units=500))
        self.assertTrue(budget.admit(spend_units=399).allowed)
        self.assertTrue(budget.admit(spend_units=400).allowed)          # lands exactly on 100
        self.assertEqual(budget.admit(spend_units=400, unknown_units=1).code,
                         "CAMPAIGN_FLOOR_WOULD_BREACH")
        self.assertEqual(budget.admit(spend_units=1, unknown_units=400).code,
                         "CAMPAIGN_FLOOR_WOULD_BREACH")

    def test_the_safety_allowance_is_part_of_the_floor(self):
        budget = usage.CampaignBudget(floor_for(units=500, tolerance_units=25))
        self.assertTrue(budget.admit(spend_units=375).allowed)      # leaves 125 >= 100+25
        self.assertEqual(budget.admit(spend_units=376).code,
                         "CAMPAIGN_FLOOR_WOULD_BREACH")             # leaves 124 < 125

    def test_the_two_bounds_are_kept_apart(self):
        # 0.80 * B0 is an upper bound on cumulative spend; the floor is the binding
        # constraint. For a TinyFish wallet they differ, and the stricter one wins.
        floor = tinyfish_wallet(int(Decimal("0.50") * USD_MICROS))
        self.assertEqual(floor.max_drawdown_units, int(Decimal("0.40") * USD_MICROS))
        self.assertEqual(floor.spendable_units, int(Decimal("0.30") * USD_MICROS))
        acceptable = usage.CampaignBudget(floor).admit(
            spend_units=int(Decimal("0.30") * USD_MICROS))
        self.assertTrue(acceptable.allowed)
        self.assertEqual(usage.CampaignBudget(floor).admit(
            spend_units=int(Decimal("0.40") * USD_MICROS)).code, "CAMPAIGN_FLOOR_WOULD_BREACH")

    def test_a_bad_spend_input_is_refused_rather_than_coerced(self):
        budget = usage.CampaignBudget(floor_for(units=500))
        for kwargs in ({"spend_units": -1}, {"spend_units": "10"},
                       {"spend_units": 1, "spent_units": -1},
                       {"spend_units": 1, "unknown_units": 1.5}):
            with self.subTest(kwargs=kwargs):
                with self.assertRaises(usage.LedgerRefused) as ctx:
                    budget.admit(**kwargs)
                self.assertEqual(ctx.exception.code, "CAMPAIGN_BUDGET_INVALID")

    def test_a_bad_external_spend_is_refused_at_construction(self):
        with self.assertRaises(usage.LedgerRefused) as ctx:
            usage.CampaignBudget(floor_for(units=500), external_spend_units=-1)
        self.assertEqual(ctx.exception.code, "CAMPAIGN_BUDGET_INVALID")

    def test_the_verdict_set_is_the_coordinators_closed_set_worst_of(self):
        verdict = usage.BudgetVerdict
        self.assertEqual({v.value for v in verdict}, {"AUTHORIZED", "BLOCKED", "NOT_CAPTURED"})
        self.assertEqual(verdict.worst([verdict.AUTHORIZED, verdict.NOT_CAPTURED]),
                         verdict.NOT_CAPTURED)
        self.assertEqual(verdict.worst([verdict.NOT_CAPTURED, verdict.BLOCKED]), verdict.BLOCKED)
        self.assertEqual(verdict.worst([verdict.AUTHORIZED]), verdict.AUTHORIZED)
        # "nothing was verified" must not read as "approved".
        self.assertEqual(verdict.worst([]), verdict.NOT_CAPTURED)


# --- the floor, wired into the ledger the way a caller will use it ---------

def pricing() -> usage.PricingCatalog:
    return usage.PricingCatalog((
        usage.Price(provider="serper", capability="search", unit_micros=750,
                    quota_unit="credits", quota_per_call=1, source="documented_rate",
                    version="2026-10-17", observed_at="2026-10-17T00:00:00+00:00"),
        usage.Price(provider="tinyfish", capability="agent_run", unit_micros=16_000,
                    quota_unit="steps", quota_per_call=1, source="provider_declared_rate",
                    version="2026-10-17", observed_at="2026-10-17T00:00:00+00:00",
                    verified=False),
    ))


def registry() -> grants.CapabilityRegistry:
    return grants.CapabilityRegistry((
        grants.Capability("search", "serper", grants.Tier.T2, "search_query", "read_only"),
        grants.Capability("agent_run", "tinyfish", grants.Tier.T3, "agent_step",
                          "paid_automation"),
    ))


def request(**overrides) -> usage.OperationRequest:
    base = dict(project="devon", run_id="run-01", operation_id="op-01",
                provider="serper", capability="search", account_ref="serper-main",
                transport="rest_https", intent="find the venue",
                args={"query": "devon venue"})
    base.update(overrides)
    return usage.OperationRequest(**base)


class LedgerFloorCase(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "state" / "usage-ledger.sqlite3"
        self.clock = lambda: NOW

    def ledger(self, budgets, *, pricing_=None) -> usage.UsageLedger:
        return usage.UsageLedger(self.path, pricing=pricing_ or pricing(), registry=registry(),
                                 clock=self.clock, campaign_budgets=budgets)

    def seed_caps(self, ledger, *, run="devon/run-01") -> None:
        ledger.set_cap("run", run, ceiling_micros=100 * USD_MICROS, ceiling_calls=1000,
                       ceiling_seconds=36_000, source="test-operator")

    def serper_budget(self, units=500) -> usage.CampaignBudget:
        return usage.CampaignBudget(floor_for(units=units))


class TestLedgerCampaignIntegration(LedgerFloorCase):
    def test_a_chargeable_operation_on_an_uncaptured_account_is_refused(self):
        # Enforcement is on (one budget exists), so an account nobody captured cannot
        # spend: "we did not check this account" is not a pass.
        ledger = self.ledger({("tinyfish", "tinyfish-main"):
                              usage.CampaignBudget(floor_for(provider="tinyfish",
                                                             account_ref="tinyfish-main",
                                                             meter="steps", units=1000))})
        self.seed_caps(ledger)
        with self.assertRaises(usage.LedgerRefused) as ctx:
            ledger.reserve(request())
        self.assertEqual(ctx.exception.code, "CAMPAIGN_NOT_CAPTURED")

    def test_no_campaign_budgets_means_no_campaign_checking(self):
        ledger = self.ledger(None)
        self.seed_caps(ledger)
        self.assertEqual(ledger.reserve(request()).charge_state, usage.ChargeState.RESERVED)

    def test_a_free_operation_is_unaffected_by_the_campaign_floor(self):
        free = usage.PricingCatalog((
            usage.Price(provider="serper", capability="search", unit_micros=0, free_tier=True,
                        quota_unit="requests", quota_per_call=1, source="provider_declared_rate",
                        version="2026-10-17", observed_at="2026-10-17T00:00:00+00:00"),))
        ledger = self.ledger({}, pricing_=free)
        self.seed_caps(ledger)
        self.assertEqual(ledger.reserve(request()).charge_state, usage.ChargeState.RESERVED)

    def test_the_grant_gate_is_reached_before_the_campaign_question(self):
        # Ordering, stated so it cannot drift silently: a T3 operation with no grant
        # is refused for the grant, not admitted or refused on the floor.
        ledger = self.ledger({("tinyfish", "tinyfish-main"):
                              usage.CampaignBudget(floor_for(provider="tinyfish",
                                                             account_ref="tinyfish-main",
                                                             meter="steps", units=1000))})
        self.seed_caps(ledger)
        with self.assertRaises(usage.LedgerRefused) as ctx:
            ledger.reserve(request(provider="tinyfish", capability="agent_run",
                                   account_ref="tinyfish-main",
                                   transport="embedded_official_mcp"))
        self.assertEqual(ctx.exception.code, "GRANT_REQUIRED")

    def test_a_credits_floor_stops_the_operation_that_would_breach_it(self):
        # B0 = 4 credits -> floor 1 -> three spendable; each search is one credit.
        ledger = self.ledger({("serper", "serper-main"): self.serper_budget(units=4)})
        self.seed_caps(ledger)
        for index in range(3):
            ledger.reserve(request(operation_id=f"op-{index}", args={"query": f"venue {index}"}))
        with self.assertRaises(usage.LedgerRefused) as ctx:
            ledger.reserve(request(operation_id="op-4", args={"query": "venue 4"}))
        self.assertEqual(ctx.exception.code, "CAMPAIGN_FLOOR_WOULD_BREACH")
        self.assertEqual(ledger.account_totals("serper", "serper-main").quota_units, 3)

    def test_a_new_run_cannot_start_with_a_fresh_allowance(self):
        ledger = self.ledger({("serper", "serper-main"): self.serper_budget(units=3)})
        self.seed_caps(ledger)
        self.seed_caps(ledger, run="devon/run-02")
        ledger.reserve(request())
        ledger.reserve(request(run_id="run-02", operation_id="op-2", args={"query": "another"}))
        with self.assertRaises(usage.LedgerRefused) as ctx:
            ledger.reserve(request(run_id="run-02", operation_id="op-3", args={"query": "third"}))
        self.assertEqual(ctx.exception.code, "CAMPAIGN_FLOOR_WOULD_BREACH")

    def test_the_account_totals_span_every_project(self):
        ledger = self.ledger({("serper", "serper-main"): self.serper_budget(units=4)})
        self.seed_caps(ledger)
        self.seed_caps(ledger, run="other/run-01")
        ledger.reserve(request())
        ledger.reserve(request(project="other", operation_id="op-2", args={"query": "b"}))
        totals = ledger.account_totals("serper", "serper-main")
        self.assertEqual(totals.quota_units, 2)
        self.assertEqual(totals.reserved_micros, 1500)
        self.assertEqual(totals.committed_micros, 1500)
        self.assertEqual(totals.unknown_micros, 0)
        self.assertEqual(totals.calls, 2)

    def test_an_unreconciled_unknown_holds_the_campaign_floor_too(self):
        ledger = self.ledger({("serper", "serper-main"): self.serper_budget(units=3)})
        self.seed_caps(ledger)
        first = ledger.reserve(request())
        ledger.mark_dispatch_intent(first.operation_id)
        ledger.mark_unknown(first.operation_id, reason="PROVIDER_TIMEOUT")
        self.assertEqual(ledger.account_totals("serper", "serper-main").unknown_micros, 750)
        ledger.reserve(request(operation_id="op-2", args={"query": "second"}))
        with self.assertRaises(usage.LedgerRefused) as ctx:
            ledger.reserve(request(operation_id="op-3", args={"query": "third"}))
        self.assertEqual(ctx.exception.code, "CAMPAIGN_FLOOR_WOULD_BREACH")

    def test_the_ledger_refusal_names_the_campaign_code_for_a_non_evidence_b0(self):
        ledger = self.ledger({("serper", "serper-main"): usage.CampaignBudget(usage.CampaignFloor(
            baseline(units=500, source=usage.MeterReadingSource.ENVIRONMENT_VARIABLE)))})
        self.seed_caps(ledger)
        with self.assertRaises(usage.LedgerRefused) as ctx:
            ledger.reserve(request())
        self.assertEqual(ctx.exception.code, "CAMPAIGN_BASELINE_NOT_EVIDENCE")
        self.assertEqual(len(ledger.operations()), 0)


class TestLedgerCampaignConcurrency(LedgerFloorCase):
    """A real race between processes: the floor must not be crossed twice."""

    #: One operation's cost with ``units=800``: 800 * 750 micro-USD = US$0.60.
    OP_UNITS = 800

    def _child_script(self) -> str:
        return "\n".join((
            "import sys",
            f"sys.path.insert(0, {str(ROOT / 'scripts')!r})",
            "import research_grants as grants",
            "import research_usage_ledger as usage",
            "PRICING = usage.PricingCatalog((usage.Price(provider='serper',"
            " capability='search', unit_micros=750, quota_unit='credits', quota_per_call=1,"
            " source='documented', version='2026-10-17',"
            " observed_at='2026-10-17T00:00:00+00:00'),))",
            "REGISTRY = grants.CapabilityRegistry((grants.Capability('search', 'serper',"
            " grants.Tier.T2, 'search_query', 'read_only'),))",
            "budget = usage.CampaignBudget(usage.CampaignFloor(usage.CampaignBaseline("
            "provider='serper', account_ref='serper-main', meter=usage.USD_MICROS,"
            f" units={USD_MICROS}, observed_at='2026-10-17T09:00:00+00:00',"
            " source=usage.MeterReadingSource.VENDOR_ACCOUNT_READ, campaign_id='race')))",
            f"ledger = usage.UsageLedger({str(self.path)!r}, pricing=PRICING, registry=REGISTRY,",
            "    clock=lambda: __import__('datetime').datetime(2026, 10, 17, 9, 0,"
            " tzinfo=__import__('datetime').timezone.utc),",
            "    campaign_budgets={('serper', 'serper-main'): budget})",
            "try:",
            "    ledger.reserve(usage.OperationRequest(",
            "        project='devon', run_id='run-01', operation_id=sys.argv[1],",
            "        provider='serper', capability='search', account_ref='serper-main',",
            "        transport='rest_https', intent='race',",
            f"        args={{'query': 'venue ' + sys.argv[1]}}, units={self.OP_UNITS}))",
            "    print('OK')",
            "except usage.LedgerRefused as exc:",
            "    print(exc.code)",
        ))

    def _race(self, processes: int):
        self.seed_caps(usage.UsageLedger(self.path, pricing=pricing(), registry=registry(),
                                         clock=self.clock))
        script = self._child_script()
        procs = [subprocess.Popen([sys.executable, "-c", script, f"op-{index}"],
                                  stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
                 for index in range(processes)]
        results = []
        for proc in procs:
            out, err = proc.communicate(timeout=180)
            results.append((out or err).strip())
        return results

    def test_concurrent_reservations_stop_exactly_at_the_floor(self):
        # B0 = US$1.00, floor = US$0.20 -> US$0.80 spendable. One operation is
        # US$0.60, so exactly one may proceed and the rest must be refused.
        results = self._race(6)
        self.assertEqual(results.count("OK"), 1, results)
        self.assertEqual(results.count("CAMPAIGN_FLOOR_WOULD_BREACH"), 5, results)

    def test_a_refused_race_loser_left_no_row_and_no_dispatch(self):
        results = self._race(4)
        ledger = usage.UsageLedger(self.path, pricing=pricing(), registry=registry(),
                                   clock=self.clock)
        rows = ledger.operations()
        self.assertEqual(len(rows), results.count("OK"))
        for row in rows:
            self.assertIsNone(row.dispatch_intent_at)
            self.assertEqual(row.charge_state, usage.ChargeState.RESERVED.value)


class TestThreadedReservation(LedgerFloorCase):
    def test_threads_cannot_both_cross_the_floor(self):
        budget_for = lambda: usage.CampaignBudget(floor_for(units=4))     # noqa: E731
        ledger = self.ledger({("serper", "serper-main"): budget_for()})
        self.seed_caps(ledger)
        results: list[str] = []
        barrier = threading.Barrier(4)

        def attempt(index: int) -> None:
            worker = usage.UsageLedger(self.path, pricing=pricing(), registry=registry(),
                                       clock=self.clock,
                                       campaign_budgets={("serper", "serper-main"):
                                                         budget_for()})
            barrier.wait()
            try:
                worker.reserve(request(operation_id=f"op-{index}",
                                       args={"query": f"venue {index}"}))
                results.append("OK")
            except usage.LedgerRefused as exc:
                results.append(exc.code)

        threads = [threading.Thread(target=attempt, args=(index,)) for index in range(4)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        # B0 4 credits, floor 1 -> three spendable.
        self.assertEqual(results.count("OK"), 3, results)
        self.assertEqual(results.count("CAMPAIGN_FLOOR_WOULD_BREACH"), 1, results)


if __name__ == "__main__":
    unittest.main()
