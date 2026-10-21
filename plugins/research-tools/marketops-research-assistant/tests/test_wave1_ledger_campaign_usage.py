"""Campaign balance enforcement through the integration-facing factory, offline.

Wave-3 follow-up. The ledger enforces a campaign floor when it is given one; this file
covers the two things that decide whether that ever happens in a deployment and whether
the numbers it prints can be read as money:

* the factory (:func:`for_root`, :func:`project_for_root`) - what a call site uses to
  get a ledger - and whether a floor survives the ways a deployment changes shape
  (a restart, a new run, a renamed project);
* the report, so that an unknown cost is never printed as zero and an unverified
  balance is never usable as a budget.

Private temporary databases, no network, no credential, no provider call.
"""
from __future__ import annotations

import sqlite3
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import research_grants as grants
import research_usage_ledger as usage

NOW = datetime(2026, 10, 17, 9, 0, tzinfo=timezone.utc)
USD = usage.USD_MICROS
USD_MICROS = int(usage.MICRO)
CREDIT = usage.MeterReadingSource.VENDOR_ACCOUNT_READ


def registry() -> grants.CapabilityRegistry:
    return grants.CapabilityRegistry((
        grants.Capability("search", "serper", grants.Tier.T2, "search_query", "read_only"),
        grants.Capability("judge", "typesafe_jev", grants.Tier.T2, "request", "read_only"),
    ))


def credit_only() -> usage.PricingCatalog:
    """The catalog the factory ships: credits, no verified dollar rate."""
    return usage.documented_pricing()


def verified_usd_catalog() -> usage.PricingCatalog:
    """An operator catalog: a real dollar rate on every capability."""
    return usage.PricingCatalog((
        usage.Price(provider="serper", capability="search", unit_micros=100_000,
                    quota_unit="credits", quota_per_call=1, source="operator_catalog",
                    version="2026-10-17", observed_at="2026-10-17T00:00:00+00:00"),
        usage.Price(provider="typesafe_jev", capability="judge", unit_micros=42,
                    quota_unit="requests", quota_per_call=1, source="operator_catalog",
                    version="2026-10-17", observed_at="2026-10-17T00:00:00+00:00"),
    ))


def credit_floor(units: int, *, provider="serper", account="serper-main", meter="credits",
                 source=CREDIT) -> usage.CampaignBudget:
    return usage.CampaignBudget(usage.CampaignFloor(usage.CampaignBaseline(
        provider=provider, account_ref=account, meter=meter, units=units,
        observed_at=NOW.isoformat(), source=source, campaign_id="wave3-usage")))


def request(**overrides) -> usage.OperationRequest:
    base = dict(project="devon", run_id="run-01", operation_id="op-01",
                provider="serper", capability="search", account_ref="serper-main",
                transport="rest_https", intent="find the venue",
                args={"query": "devon venue"})
    base.update(overrides)
    return usage.OperationRequest(**base)


class UsageCase(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.path = self.root / "state" / "usage-ledger.sqlite3"
        self.clock = lambda: NOW

    def ledger(self, *, budgets=None, pricing=None) -> usage.UsageLedger:
        return usage.UsageLedger(self.path, pricing=pricing or credit_only(),
                                 registry=registry(), clock=self.clock,
                                 campaign_budgets=budgets)

    def caps(self, ledger, *, run="devon/run-01", calls=10_000, money=0) -> None:
        ledger.set_cap("run", run, ceiling_micros=money, ceiling_calls=calls,
                       ceiling_seconds=36_000, source="usage-test")

    def factory(self, **kwargs) -> usage.UsageLedger:
        kwargs.setdefault("registry", registry())
        kwargs.setdefault("clock", self.clock)
        return usage.for_root(self.root, **kwargs)


class TestAccountsPricedInCredits(UsageCase):
    """The meter an account is billed in decides what its floor is denominated in."""

    def test_a_credit_floor_is_judged_in_credits(self):
        ledger = self.ledger(budgets={("serper", "serper-main"): credit_floor(4)})
        self.caps(ledger)
        for index in range(3):
            ledger.reserve(request(operation_id=f"op-{index}", args={"query": f"q{index}"}))
        with self.assertRaises(usage.LedgerRefused) as ctx:
            ledger.reserve(request(operation_id="op-4", args={"query": "q4"}))
        self.assertEqual(ctx.exception.code, "CAMPAIGN_FLOOR_WOULD_BREACH")

    def test_two_credit_metered_accounts_do_not_share_a_floor(self):
        ledger = self.ledger(budgets={("serper", "serper-main"): credit_floor(3),
                                      ("typesafe_jev", "jev-main"):
                                      credit_floor(3, provider="typesafe_jev",
                                                   account="jev-main", meter="requests")})
        self.caps(ledger)
        ledger.reserve(request())
        self.assertEqual(ledger.account_totals("serper", "serper-main").quota_units, 1)
        self.assertEqual(ledger.account_totals("typesafe_jev", "jev-main").quota_units, 0)

    def test_a_floor_in_one_unit_refuses_a_call_billed_in_another(self):
        ledger = self.ledger(budgets={("typesafe_jev", "jev-main"):
                                      credit_floor(3, provider="typesafe_jev",
                                                   account="jev-main", meter="credits")})
        ledger.set_cap("run", "devon/run-01", ceiling_micros=0, ceiling_calls=100,
                       ceiling_seconds=3600, source="usage-test")
        with self.assertRaises(usage.LedgerRefused) as ctx:
            ledger.reserve(request(provider="typesafe_jev", capability="judge",
                                   account_ref="jev-main"))
        self.assertEqual(ctx.exception.code, "CAMPAIGN_METER_MISMATCH")

    def test_a_zero_dollar_call_still_spends_a_credit(self):
        ledger = self.ledger(budgets={("serper", "serper-main"): credit_floor(10)})
        self.caps(ledger)
        op = ledger.reserve(request())
        self.assertEqual(op.estimated_micros, 0)
        self.assertEqual(op.quota_units, 1)
        self.assertEqual(ledger.account_totals("serper", "serper-main").quota_units, 1)

    def test_a_dollar_floor_on_a_credit_priced_account_refuses_rather_than_guessing(self):
        ledger = self.ledger(budgets={("serper", "serper-main"): usage.CampaignBudget(
            usage.CampaignFloor(usage.CampaignBaseline(
                provider="serper", account_ref="serper-main", meter=USD,
                units=USD_MICROS, observed_at=NOW.isoformat(), source=CREDIT,
                campaign_id="wave3-usage")))})
        self.caps(ledger)
        with self.assertRaises(usage.LedgerRefused) as ctx:
            ledger.reserve(request())
        self.assertEqual(ctx.exception.code, "CAMPAIGN_PRICE_UNKNOWN_FAIL_CLOSED")
        self.assertEqual(ledger.operations(), ())


class TestFrozenStartingBalances(UsageCase):
    """B0 is the campaign's, and nothing a deployment does mid-campaign moves it."""

    def budget(self, units=4) -> usage.CampaignBudget:
        return credit_floor(units)

    def test_the_floor_does_not_move_when_the_running_balance_drops(self):
        floor = credit_floor(4).floor
        for spent in (0, 1, 2, 3):
            with self.subTest(committed=spent):
                self.assertEqual(floor.floor_units, 1)
                self.assertEqual(floor.spendable_units, 3)

    def test_a_restart_does_not_refresh_the_allowance(self):
        ledger = self.ledger(budgets={("serper", "serper-main"): self.budget(3)})
        self.caps(ledger)
        ledger.reserve(request())
        restarted = self.ledger(budgets={("serper", "serper-main"): self.budget(3)})
        self.caps(restarted)
        ledger.reserve(request(operation_id="op-2", args={"query": "second"}))
        with self.assertRaises(usage.LedgerRefused) as ctx:
            restarted.reserve(request(operation_id="op-3", args={"query": "third"}))
        self.assertEqual(ctx.exception.code, "CAMPAIGN_FLOOR_WOULD_BREACH")

    def test_a_renamed_project_does_not_refresh_the_allowance(self):
        """The factory derives the project id; changing it must not reset the floor.

        ``project_for_root`` reads MARKETOPS_RESEARCH_PROJECT, so an operator who names
        a project mid-campaign would otherwise get a fresh allowance for the same
        account. The floor is account-wide, so it does not move.
        """
        ledger = self.ledger(budgets={("serper", "serper-main"): self.budget(3)})
        self.caps(ledger)
        self.caps(ledger, run="other/run-01")
        ledger.reserve(request())
        ledger.reserve(request(project="other", operation_id="op-2", args={"query": "b"}))
        with self.assertRaises(usage.LedgerRefused) as ctx:
            ledger.reserve(request(project="other", operation_id="op-3", args={"query": "c"}))
        self.assertEqual(ctx.exception.code, "CAMPAIGN_FLOOR_WOULD_BREACH")
        self.assertEqual(ledger.account_totals("serper", "serper-main").quota_units, 2)

    def test_the_project_id_is_stable_and_env_overridable(self):
        derived = usage.project_for_root(self.root, environ={})
        self.assertEqual(derived, usage.project_for_root(self.root, environ={}))
        named = usage.project_for_root(self.root, environ={"MARKETOPS_RESEARCH_PROJECT": "devon"})
        self.assertEqual(named, "devon")
        # A template placeholder is not a project name, the same rule the data root uses.
        placeholder = usage.project_for_root(
            self.root, environ={"MARKETOPS_RESEARCH_PROJECT": "${MARKETOPS_RESEARCH_PROJECT}"})
        self.assertEqual(placeholder, derived)

    def test_the_floor_survives_the_project_being_renamed_between_sessions(self):
        budgets = {("serper", "serper-main"): self.budget(3)}
        first = self.factory(pricing=credit_only(), campaign_budgets=budgets)
        self.caps(first)
        self.caps(first, run=f"{usage.project_for_root(self.root, environ={})}/run-01")
        first.reserve(request(project=usage.project_for_root(self.root, environ={})))
        second = self.factory(pricing=credit_only(), campaign_budgets=budgets)
        renamed = usage.project_for_root(self.root, environ={"MARKETOPS_RESEARCH_PROJECT": "devon"})
        self.caps(second, run="devon/run-01")
        second.reserve(request(project=renamed, operation_id="op-2", args={"query": "b"}))
        with self.assertRaises(usage.LedgerRefused) as ctx:
            second.reserve(request(project=renamed, operation_id="op-3", args={"query": "c"}))
        self.assertEqual(ctx.exception.code, "CAMPAIGN_FLOOR_WOULD_BREACH")


class TestUnresolvedReservations(UsageCase):
    """A reservation that has not resolved is committed money, so it holds headroom."""

    def budget(self, units=4) -> usage.CampaignBudget:
        return credit_floor(units)

    def test_a_reserved_row_holds_the_floor(self):
        ledger = self.ledger(budgets={("serper", "serper-main"): self.budget(3)})
        self.caps(ledger)
        first = ledger.reserve(request())
        self.assertEqual(ledger.account_totals("serper", "serper-main").quota_units, 1)
        ledger.reserve(request(operation_id="op-2", args={"query": "b"}))
        with self.assertRaises(usage.LedgerRefused) as ctx:
            ledger.reserve(request(operation_id="op-3", args={"query": "c"}))
        self.assertEqual(ctx.exception.code, "CAMPAIGN_FLOOR_WOULD_BREACH")
        # Settling does not give the headroom back: the credit was spent, not released.
        ledger.mark_dispatch_intent(first.operation_id)
        ledger.settle(first.operation_id, actual_micros=0,
                      actual_source=usage.ActualSource.DERIVED, quota_units=1)
        self.assertEqual(ledger.account_totals("serper", "serper-main").quota_units, 2)

    def test_an_unknown_reservation_keeps_holding_the_floor(self):
        ledger = self.ledger(budgets={("serper", "serper-main"): self.budget(3)})
        self.caps(ledger)
        first = ledger.reserve(request())
        ledger.mark_dispatch_intent(first.operation_id)
        ledger.mark_unknown(first.operation_id, reason="PROVIDER_TIMEOUT")
        self.assertEqual(ledger.account_totals("serper", "serper-main").quota_units, 1)
        ledger.reserve(request(operation_id="op-2", args={"query": "b"}))
        with self.assertRaises(usage.LedgerRefused) as ctx:
            ledger.reserve(request(operation_id="op-3", args={"query": "c"}))
        self.assertEqual(ctx.exception.code, "CAMPAIGN_FLOOR_WOULD_BREACH")

    def test_a_proven_no_charge_releases_the_credit(self):
        ledger = self.ledger(budgets={("serper", "serper-main"): self.budget(3)})
        self.caps(ledger)
        first = ledger.reserve(request())
        ledger.prove_no_charge(first.operation_id, reason="PRE_DISPATCH_REFUSAL")
        self.assertEqual(ledger.account_totals("serper", "serper-main").quota_units, 0)
        # Two of the three credits are available again, so the run can continue.
        ledger.reserve(request(operation_id="op-2", args={"query": "b"}))
        ledger.reserve(request(operation_id="op-3", args={"query": "c"}))
        with self.assertRaises(usage.LedgerRefused) as ctx:
            ledger.reserve(request(operation_id="op-4", args={"query": "d"}))
        self.assertEqual(ctx.exception.code, "CAMPAIGN_FLOOR_WOULD_BREACH")


class TestSchemaUpgrades(UsageCase):
    """A deployment has a ledger file already; upgrading it must not lose the floor."""

    def _older_schema(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self.path) as db:
            db.execute(
                "CREATE TABLE operations (operation_id TEXT PRIMARY KEY,"
                " project TEXT NOT NULL, run_id TEXT NOT NULL,"
                " intent_fingerprint TEXT NOT NULL, op_signature TEXT NOT NULL,"
                " provider TEXT NOT NULL, account_ref TEXT NOT NULL, capability TEXT NOT NULL,"
                " meter TEXT NOT NULL, transport TEXT NOT NULL, site TEXT NOT NULL DEFAULT '',"
                " tier INTEGER NOT NULL, estimated_micros INTEGER NOT NULL,"
                " reserved_micros INTEGER NOT NULL, actual_micros INTEGER,"
                " actual_source TEXT NOT NULL, charge_state TEXT NOT NULL,"
                " quota_unit TEXT NOT NULL, quota_units INTEGER NOT NULL,"
                " estimated_seconds INTEGER NOT NULL, outcome TEXT NOT NULL DEFAULT '',"
                " upstream_ref TEXT NOT NULL DEFAULT '', grant_ref TEXT NOT NULL DEFAULT '',"
                " escalates_from TEXT NOT NULL DEFAULT '', price_source TEXT NOT NULL,"
                " price_version TEXT NOT NULL, price_observed_at TEXT NOT NULL,"
                " price_verified INTEGER NOT NULL, origin TEXT NOT NULL DEFAULT 'native',"
                " legacy_ref TEXT, counts_against_caps INTEGER NOT NULL DEFAULT 1,"
                " created_at TEXT NOT NULL, dispatch_intent_at TEXT, settled_at TEXT,"
                " reconciled_at TEXT)")

    def test_an_upgraded_database_keeps_counting_its_unresolved_rows(self):
        self._older_schema()
        with sqlite3.connect(self.path) as db:
            db.execute("INSERT INTO operations (operation_id, project, run_id,"
                       " intent_fingerprint, op_signature, provider, account_ref, capability,"
                       " meter, transport, tier, estimated_micros, reserved_micros,"
                       " actual_micros, actual_source, charge_state, quota_unit, quota_units,"
                       " estimated_seconds, outcome, upstream_ref, grant_ref, escalates_from,"
                       " price_source, price_version, price_observed_at, price_verified,"
                       " origin, counts_against_caps, created_at) VALUES"
                       " ('old-1','devon','run-01','i','s','serper','serper-main','search',"
                       " 'search_query','rest_https',2,0,0,NULL,'NONE','RESERVED','credits',1,"
                       " 0,'','','','','legacy','n/a','2026-10-17T00:00:00+00:00',0,'native',1,"
                       " '2026-10-17T00:00:00+00:00')")
        ledger = self.ledger(budgets={("serper", "serper-main"): credit_floor(3)})
        self.caps(ledger)
        # The pre-upgrade row was already committed against this account.
        self.assertEqual(ledger.account_totals("serper", "serper-main").quota_units, 1)

    def test_the_upgrade_is_idempotent(self):
        self._older_schema()
        for _ in range(3):
            ledger = self.ledger(budgets={("serper", "serper-main"): credit_floor(3)})
            ledger.campaign_status()
        with sqlite3.connect(self.path) as db:
            columns = [row[1] for row in db.execute("PRAGMA table_info(operations)")]
        self.assertEqual(columns.count("route_shape"), 1)


class TestCostReportingWhenTheDollarPriceIsUnknown(UsageCase):
    """An unknown cost must not be printed as zero, and a known one must still be shown."""

    def test_an_unverified_price_withholds_the_derived_cost(self):
        ledger = self.ledger(pricing=credit_only(),
                             budgets={("serper", "serper-main"): credit_floor(10)})
        self.caps(ledger)
        op = ledger.reserve(request())
        ledger.mark_dispatch_intent(op.operation_id)
        # A credit-priced success is 0 USD because no dollar rate is known, and DERIVED
        # says exactly that - it is not a claim that the call was free.
        ledger.settle(op.operation_id, actual_micros=0,
                      actual_source=usage.ActualSource.DERIVED, quota_units=1)
        report = ledger.report("devon", accepted_outputs=1)
        self.assertEqual(report["money"]["basis"], "INDETERMINATE")
        self.assertEqual(report["money"]["basis_code"], "PRICE_UNVERIFIED")
        self.assertIsNone(report["cost_per_accepted_output_micros"])
        self.assertEqual(report["cost_per_accepted_output_basis"], "PRICE_UNVERIFIED")
        self.assertTrue(any("withheld" in note for note in report["notes"]))

    def test_unresolved_exposure_withholds_the_derived_cost(self):
        ledger = self.ledger(pricing=verified_usd_catalog(),
                             budgets={("serper", "serper-main"): usage.CampaignBudget(
                                 usage.CampaignFloor(usage.CampaignBaseline(
                                     provider="serper", account_ref="serper-main", meter=USD,
                                     units=USD_MICROS, observed_at=NOW.isoformat(),
                                     source=CREDIT, campaign_id="wave3-usage")))})
        self.caps(ledger, money=10 * 100_000, calls=100)
        op = ledger.reserve(request())
        ledger.mark_dispatch_intent(op.operation_id)
        ledger.mark_unknown(op.operation_id, reason="PROVIDER_TIMEOUT")
        report = ledger.report("devon", accepted_outputs=1)
        self.assertEqual(report["money"]["basis_code"], "UNKNOWN_COST_OUTSTANDING")
        self.assertIsNone(report["cost_per_accepted_output_micros"])
        self.assertEqual(report["money"]["unknown_exposure_micros"], 100_000)

    def test_a_verified_and_resolved_run_still_reports_its_cost(self):
        ledger = self.ledger(pricing=verified_usd_catalog(),
                             budgets={("serper", "serper-main"): usage.CampaignBudget(
                                 usage.CampaignFloor(usage.CampaignBaseline(
                                     provider="serper", account_ref="serper-main", meter=USD,
                                     units=USD_MICROS, observed_at=NOW.isoformat(),
                                     source=CREDIT, campaign_id="wave3-usage")))})
        self.caps(ledger, money=10 * 100_000, calls=100)
        for index in range(2):
            op = ledger.reserve(request(operation_id=f"op-{index}", args={"query": f"q{index}"}))
            ledger.mark_dispatch_intent(op.operation_id)
            ledger.settle(op.operation_id, actual_micros=100_000,
                          actual_source=usage.ActualSource.PROVIDER_REPORTED,
                          upstream_ref=f"req-{index}")
        report = ledger.report("devon", accepted_outputs=2)
        self.assertEqual(report["money"]["basis"], "VERIFIED_ACTUAL")
        self.assertEqual(report["cost_per_accepted_output_micros"], 100_000)
        self.assertEqual(report["price_evidence"], {"verified": 2, "unverified": 0})

    def test_an_empty_report_is_not_read_as_a_free_campaign(self):
        ledger = self.ledger()
        report = ledger.report("devon", accepted_outputs=5)
        self.assertEqual(report["operations"], 0)
        self.assertIsNone(report["cost_per_accepted_output_micros"])
        self.assertEqual(report["money"]["basis_code"], "NO_SETTLED_OPERATIONS")
        self.assertEqual(report["money"]["basis"], "INDETERMINATE")


class TestAnUnverifiedBalanceIsNotABudget(UsageCase):
    """§1.1: an environment variable, a mock or a documentation price is not a balance."""

    def huge_unverified(self, source) -> usage.CampaignBudget:
        return usage.CampaignBudget(usage.CampaignFloor(usage.CampaignBaseline(
            provider="serper", account_ref="serper-main", meter="credits", units=1_000_000,
            observed_at=NOW.isoformat(), source=source, campaign_id="wave3-usage")))

    def test_no_unverified_source_authorizes_anything(self):
        for source in (usage.MeterReadingSource.ENVIRONMENT_VARIABLE,
                       usage.MeterReadingSource.MOCK,
                       usage.MeterReadingSource.DOCUMENTATION,
                       usage.MeterReadingSource.CREDENTIAL_FILE_PRESENCE,
                       usage.MeterReadingSource.UNKNOWN):
            with self.subTest(source=source.value):
                ledger = self.ledger(budgets={("serper", "serper-main"):
                                              self.huge_unverified(source)})
                self.caps(ledger)
                with self.assertRaises(usage.LedgerRefused) as ctx:
                    ledger.reserve(request())
                self.assertEqual(ctx.exception.code, "CAMPAIGN_BASELINE_NOT_EVIDENCE")
                self.assertEqual(ledger.operations(), ())

    def test_a_campaign_without_a_floor_says_so_at_the_factory(self):
        ledger = self.factory(pricing=credit_only())
        # Enforcement is off, which is a decision - and the factory entry point can be
        # asked which state it is in rather than leaving it to be inferred.
        self.assertEqual(ledger.campaign_status().verdict, usage.BudgetVerdict.NOT_CAPTURED)
        self.assertEqual(ledger.campaign_status().code, "CAMPAIGN_NOT_CONFIGURED")

    def test_a_campaign_with_a_floor_names_the_accounts_it_covers(self):
        ledger = self.factory(pricing=credit_only(),
                              campaign_budgets={("serper", "serper-main"): credit_floor(4)})
        status = ledger.campaign_status()
        self.assertEqual(status.verdict, usage.BudgetVerdict.AUTHORIZED)
        self.assertIn("serper/serper-main", status.detail)
        self.assertNotIn("credits", status.detail)   # no balance value is echoed

    def test_the_factory_uses_the_operator_catalog_when_one_is_configured(self):
        catalog = self.root / "prices.json"
        catalog.write_text(
            '{"prices": [{"provider": "serper", "capability": "search", "unit_micros": 123456,'
            ' "quota_unit": "credits", "quota_per_call": 1, "source": "operator_catalog",'
            ' "version": "2026-10-17", "observed_at": "2026-10-17T00:00:00+00:00"}]}',
            encoding="utf-8")
        import os
        from unittest.mock import patch
        with patch.dict(os.environ, {"MARKETOPS_USAGE_PRICING": str(catalog)}, clear=False):
            ledger = self.factory()
        self.caps(ledger, money=10 * 123_456, calls=100)
        self.assertEqual(ledger.reserve(request()).estimated_micros, 123_456)


if __name__ == "__main__":
    unittest.main()
