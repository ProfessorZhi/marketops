"""Offline tests for the unified usage ledger.

Nothing here calls a provider, reads a credential or writes anywhere except a
temporary directory. The crash cases really do kill a subprocess: a ledger whose
crash behaviour is only tested in-process is a ledger whose commit order and
reopen path are untested.

This is the minimum set named in the P1-03 contract (docs/architecture/
MARKETOPS_ARCHITECTURE_CONVERGENCE_DECISION_AND_P0_P2_PLAN_2026-10-17.md,
Appendix D, on branch docs/independent-technical-audit-20261016 - not on this
branch): concurrent same-signature race, two signatures sharing a run limit,
two provider accounts, REST/MCP transport dedupe, cross-provider escalation,
crash before/after dispatch and around settle, unknown-charge persistence,
period reset, malformed cost config, unreviewed tool default deny, history
readback. `TestReviewRegressions` holds one more test per hole an adversarial
review found by running code against the module.
"""
from __future__ import annotations

import importlib.util
import inspect
import json
import subprocess
import sys
import tempfile
import threading
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import research_grants as grants
import research_usage_ledger as usage

NOW = datetime(2026, 10, 17, 9, 0, tzinfo=timezone.utc)
CRYPTO = importlib.util.find_spec("cryptography") is not None


def keypair():
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import ed25519
    key = ed25519.Ed25519PrivateKey.generate()
    private = key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption())
    public = key.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo)
    return private, public


def sign_grant(private: bytes, **overrides) -> str:
    """A T3 grant covering the agent_request scope, signed by the given key."""
    fields = dict(tier=grants.Tier.T3, project="devon", run_id="run-01", provider="tinyfish",
                  capability="agent_run", account_ref="tinyfish-main", max_micros=1_000_000,
                  max_calls=1, ttl_seconds=3600, issued_by="operator-member-b", now=NOW)
    fields.update(overrides)
    return grants.mint_grant(private_key_bytes=private, **fields).token()


class Clock:
    """A clock the test moves: a period window must not depend on wall time."""

    def __init__(self, now: datetime = NOW):
        self.now = now

    def __call__(self) -> datetime:
        return self.now

    def advance(self, **delta) -> None:
        self.now = self.now + timedelta(**delta)


def registry() -> grants.CapabilityRegistry:
    return grants.CapabilityRegistry((
        grants.Capability("search", provider="serper", tier=grants.Tier.T2,
                          meter="search_query", effect="read_only"),
        grants.Capability("search", provider="serpapi", tier=grants.Tier.T2,
                          meter="search_query", effect="read_only"),
        grants.Capability("search", provider="tinyfish", tier=grants.Tier.T1,
                          meter="search_query", effect="read_only"),
        grants.Capability("fetch", provider="tinyfish", tier=grants.Tier.T1,
                          meter="fetch_url", effect="read_only"),
        grants.Capability("agent_run", provider="tinyfish", tier=grants.Tier.T3,
                          meter="agent_step", effect="paid_automation"),
        grants.Capability("profile_read", provider="tinyfish", tier=grants.Tier.T4,
                          meter="session", effect="authenticated_read"),
        grants.Capability("render", provider="crawl4ai", tier=grants.Tier.T1,
                          meter="local_page", effect="local_compute"),
    ))


def pricing() -> usage.PricingCatalog:
    return usage.PricingCatalog((
        usage.Price(provider="serper", capability="search", unit_micros=750,
                    quota_unit="credits", quota_per_call=1, source="documented_rate",
                    version="2026-10-17", observed_at="2026-10-17T00:00:00+00:00"),
        usage.Price(provider="serpapi", capability="search", unit_micros=1000,
                    quota_unit="searches", quota_per_call=1, source="documented_rate",
                    version="2026-10-17", observed_at="2026-10-17T00:00:00+00:00"),
        usage.Price(provider="tinyfish", capability="search", unit_micros=0, free_tier=True,
                    quota_unit="requests", quota_per_call=1, source="provider_declared_rate",
                    version="2026-10-17", observed_at="2026-10-17T00:00:00+00:00"),
        usage.Price(provider="tinyfish", capability="fetch", unit_micros=0, free_tier=True,
                    quota_unit="urls", quota_per_call=1, source="provider_declared_rate",
                    version="2026-10-17", observed_at="2026-10-17T00:00:00+00:00"),
        usage.Price(provider="tinyfish", capability="agent_run", unit_micros=16000,
                    quota_unit="steps", quota_per_call=1, source="provider_declared_rate",
                    version="2026-10-17", observed_at="2026-10-17T00:00:00+00:00",
                    verified=False),
        usage.Price(provider="tinyfish", capability="profile_read", unit_micros=0,
                    quota_unit="sessions", quota_per_call=1, source="operator_policy",
                    version="2026-10-17", observed_at="2026-10-17T00:00:00+00:00"),
        usage.Price(provider="crawl4ai", capability="render", unit_micros=0, free_tier=True,
                    quota_unit="pages", quota_per_call=1, source="local_compute",
                    version="2026-10-17", observed_at="2026-10-17T00:00:00+00:00"),
    ))


def make_request(**overrides) -> usage.OperationRequest:
    """One operation, spelled once for both the tests and the crash children."""
    base = dict(
        project="devon", run_id="run-01", operation_id="op-01",
        provider="serper", capability="search", account_ref="serper-main",
        transport="rest_https", intent="find official site of devon surf club",
        args={"query": "devon surf club", "num": 10},
    )
    base.update(overrides)
    return usage.OperationRequest(**base)


def open_ledger(path, clock) -> usage.UsageLedger:
    return usage.UsageLedger(path, pricing=pricing(), registry=registry(), clock=clock)


class LedgerCase(unittest.TestCase):
    """A ledger in a temporary directory, with a moved clock and injected config."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "state" / "usage-ledger.sqlite3"
        self.clock = Clock()
        self.ledger = open_ledger(self.path, self.clock)

    def reopen(self) -> usage.UsageLedger:
        """What a second process sees: no state may live only in memory."""
        return open_ledger(self.path, self.clock)

    def agent_request(self, **overrides) -> usage.OperationRequest:
        """A T3 operation: multi-step browser work, which is closed by default."""
        base = dict(operation_id="op-agent", provider="tinyfish", account_ref="tinyfish-main",
                    capability="agent_run", intent="multi-step browser research",
                    args={"url": "https://example.test/", "goal": "find the venue"})
        base.update(overrides)
        return make_request(**base)

    def open_caps(self, *, money=2_000_000, calls=10, seconds=3600) -> None:
        for kind, key in (("run", "devon/run-01"), ("project", "devon"),
                          ("provider_account", "devon/serper/serper-main"),
                          ("provider_account", "devon/serpapi/serpapi-main"),
                          ("provider_account", "devon/tinyfish/tinyfish-main"),
                          ("provider_account", "devon/crawl4ai/local")):
            self.ledger.set_cap(kind, key, ceiling_micros=money, ceiling_calls=calls,
                                ceiling_seconds=seconds, source="test-operator")


class TestReservationAndDeduplication(LedgerCase):
    def test_reserve_holds_money_and_quota_and_records_the_price_basis(self):
        self.open_caps()
        op = self.ledger.reserve(make_request())
        self.assertEqual(op.charge_state, usage.ChargeState.RESERVED)
        self.assertEqual(op.estimated_micros, 750)
        self.assertEqual(op.reserved_micros, 750)
        self.assertIsNone(op.actual_micros)
        self.assertEqual(op.quota_units, 1)
        self.assertEqual(op.quota_unit, "credits")
        self.assertEqual(op.price_source, "documented_rate")
        self.assertEqual(op.price_version, "2026-10-17")
        self.assertEqual(op.price_observed_at, "2026-10-17T00:00:00+00:00")
        self.assertTrue(op.price_verified)
        # A reservation is not a charge: nothing may read it as money spent.
        head = self.ledger.remaining("run", "devon/run-01")
        self.assertEqual(head.held_micros, 750)
        self.assertEqual(head.spent_micros, 0)
        self.assertEqual(head.unknown_micros, 0)

    def test_concurrent_same_signature_race_reserves_exactly_once(self):
        self.open_caps()
        results: list[str] = []
        barrier = threading.Barrier(6)

        def attempt() -> None:
            ledger = self.reopen()
            barrier.wait()
            try:
                ledger.reserve(make_request(operation_id=f"op-{threading.get_ident()}"))
                results.append("RESERVED")
            except usage.LedgerRefused as exc:
                results.append(exc.code)

        threads = [threading.Thread(target=attempt) for _ in range(6)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(results.count("RESERVED"), 1, results)
        self.assertEqual(results.count("DUPLICATE_OPERATION_SIGNATURE"), 5, results)
        self.assertEqual(self.ledger.remaining("run", "devon/run-01").held_micros, 750)

    def test_renaming_the_operation_id_cannot_reissue_the_same_operation(self):
        self.open_caps()
        self.ledger.reserve(make_request())
        with self.assertRaises(usage.LedgerRefused) as ctx:
            self.ledger.reserve(make_request(
                operation_id="op-02", args={"num": 10, "query": "devon surf club"}))
        self.assertEqual(ctx.exception.code, "DUPLICATE_OPERATION_SIGNATURE")

    def test_a_free_page_fetch_may_be_retried_under_a_new_operation_id(self):
        # A host timeout leaves the first attempt unsettled; a local render
        # costs no vendor money, so the same page may be fetched again.
        self.open_caps()
        local = dict(provider="crawl4ai", capability="render", account_ref="local",
                     transport="local_python", intent="read club contact page",
                     args={"url": "https://club.example/contact"})
        self.ledger.reserve(make_request(operation_id="op-r1", **local))
        self.ledger.reserve(make_request(operation_id="op-r2", **local))
        with self.assertRaises(usage.LedgerRefused) as ctx:
            self.ledger.reserve(make_request(operation_id="op-r2", **{
                **local, "args": {"url": "https://club.example/other"}}))
        self.assertEqual(ctx.exception.code, "OPERATION_ID_COLLISION")

    def test_one_operation_id_cannot_name_two_different_operations(self):
        self.open_caps()
        self.ledger.reserve(make_request())
        with self.assertRaises(usage.LedgerRefused) as ctx:
            self.ledger.reserve(make_request(
                operation_id="op-01", args={"query": "a different objective"}))
        self.assertEqual(ctx.exception.code, "OPERATION_ID_COLLISION")

    def test_rest_and_mcp_are_one_operation_not_two_expenses(self):
        self.open_caps()
        first = self.ledger.reserve(make_request(transport="rest_https"))
        with self.assertRaises(usage.LedgerRefused) as ctx:
            self.ledger.reserve(make_request(operation_id="op-mcp",
                                             transport="embedded_official_mcp"))
        self.assertEqual(ctx.exception.code, "DUPLICATE_OPERATION_SIGNATURE")
        # The refusal names where the earlier attempt went, so a reader can look it up.
        self.assertIn("rest_https", str(ctx.exception))
        self.assertEqual(self.ledger.get(first.operation_id).transport, "rest_https")
        self.assertEqual(self.ledger.remaining("run", "devon/run-01").held_micros, 750)

    def test_two_signatures_share_one_run_ceiling(self):
        self.open_caps(calls=1)
        self.ledger.reserve(make_request())
        with self.assertRaises(usage.LedgerRefused) as ctx:
            self.ledger.reserve(make_request(operation_id="op-02",
                                             args={"query": "second objective"}))
        self.assertEqual(ctx.exception.code, "BUDGET_RUN_CEILING")
        self.assertEqual(ctx.exception.dimension, "calls")

    def test_a_second_run_has_its_own_ceiling_and_the_project_window_counts_both(self):
        self.open_caps(calls=2)
        self.ledger.set_cap("run", "devon/run-02", ceiling_micros=2_000_000,
                            ceiling_calls=1, ceiling_seconds=600, source="test-operator")
        self.ledger.reserve(make_request())
        self.ledger.reserve(make_request(run_id="run-02", operation_id="op-02"))
        with self.assertRaises(usage.LedgerRefused) as ctx:
            self.ledger.reserve(make_request(run_id="run-02", operation_id="op-03",
                                             args={"query": "third"}))
        self.assertEqual(ctx.exception.code, "BUDGET_RUN_CEILING")
        self.assertEqual(ctx.exception.dimension, "calls")
        self.assertEqual(self.ledger.remaining("project", "devon").calls_used, 2)

    def test_two_provider_accounts_have_independent_ceilings(self):
        self.open_caps(calls=10)
        for account in ("devon/serper/serper-main", "devon/serpapi/serpapi-main"):
            self.ledger.set_cap("provider_account", account, ceiling_micros=1_000_000,
                                ceiling_calls=1, ceiling_seconds=600, source="test-operator")
        self.ledger.reserve(make_request())
        with self.assertRaises(usage.LedgerRefused) as ctx:
            self.ledger.reserve(make_request(operation_id="op-02",
                                             args={"query": "another on the same account"}))
        self.assertEqual(ctx.exception.code, "BUDGET_PROVIDER_CEILING")
        self.assertEqual(ctx.exception.dimension, "calls")
        second = self.ledger.reserve(make_request(
            operation_id="op-03", provider="serpapi", account_ref="serpapi-main",
            intent="corroborate the same objective on a second vendor",
            args={"query": "devon surf club"}))
        self.assertEqual(second.provider, "serpapi")
        self.assertEqual(self.ledger.remaining(
            "provider_account", "devon/serpapi/serpapi-main").held_micros, 1000)

    def test_a_paid_operation_without_a_configured_run_ceiling_is_refused(self):
        # Not configured is not the same as unlimited, and it must not read as $0.
        with self.assertRaises(usage.LedgerRefused) as ctx:
            self.ledger.reserve(make_request())
        self.assertEqual(ctx.exception.code, "CAP_NOT_CONFIGURED")

    def test_operation_and_time_ceilings_are_enforced(self):
        self.open_caps()
        self.ledger.set_cap("operation", "devon/*", ceiling_micros=100, source="test-operator")
        with self.assertRaises(usage.LedgerRefused) as ctx:
            self.ledger.reserve(make_request())
        # The code names the dimension and the message names the scope, so four
        # scopes times three dimensions stay three codes instead of twelve.
        self.assertEqual(ctx.exception.code, "BUDGET_OPERATION_CEILING")
        self.assertEqual(ctx.exception.dimension, "money")
        self.assertIn("operation", str(ctx.exception))
        self.ledger.set_cap("operation", "devon/*", ceiling_micros=100_000,
                            source="test-operator")
        with self.assertRaises(usage.LedgerRefused) as ctx:
            self.ledger.reserve(make_request(estimated_seconds=7200))
        self.assertEqual(ctx.exception.code, "BUDGET_RUN_CEILING")
        self.assertEqual(ctx.exception.dimension, "seconds")


class TestCrossProviderEscalation(LedgerCase):
    def test_same_intent_on_a_second_provider_needs_a_stated_reason(self):
        self.open_caps()
        self.ledger.reserve(make_request())
        with self.assertRaises(usage.LedgerRefused) as ctx:
            self.ledger.reserve(make_request(
                operation_id="op-02", provider="serpapi", account_ref="serpapi-main",
                args={"query": "devon surf club"}))
        self.assertEqual(ctx.exception.code, "CROSS_PROVIDER_ESCALATION_UNJUSTIFIED")

    def test_a_stated_reason_permits_the_escalation_and_records_its_origin(self):
        self.open_caps()
        first = self.ledger.reserve(make_request())
        second = self.ledger.reserve(make_request(
            operation_id="op-02", provider="serpapi", account_ref="serpapi-main",
            args={"query": "devon surf club"},
            escalation_reason="free provider returned no official site"))
        self.assertEqual(second.escalates_from, first.operation_id)
        self.assertEqual(second.provider, "serpapi")
        # Both are charged: an escalation adds a second expense, it does not move one.
        self.assertEqual(self.ledger.remaining("run", "devon/run-01").held_micros, 1750)

    def test_a_different_intent_on_a_second_provider_is_an_ordinary_operation(self):
        self.open_caps()
        self.ledger.reserve(make_request())
        second = self.ledger.reserve(make_request(
            operation_id="op-02", provider="serpapi", account_ref="serpapi-main",
            intent="find the contact page of devon surf club",
            args={"query": "devon surf club contact"}))
        self.assertEqual(second.escalates_from, "")
        self.assertNotEqual(second.intent_fingerprint,
                            self.ledger.get("op-01").intent_fingerprint)

    def test_the_same_intent_in_a_later_run_is_not_an_escalation(self):
        # Intent is scoped to a run: another run re-asking the same question is
        # that run's work, not unapproved parallel spend.
        self.open_caps()
        self.ledger.set_cap("run", "devon/run-02", ceiling_micros=2_000_000,
                            ceiling_calls=10, ceiling_seconds=600, source="test-operator")
        self.ledger.reserve(make_request())
        second = self.ledger.reserve(make_request(
            run_id="run-02", operation_id="op-02", provider="serpapi",
            account_ref="serpapi-main", args={"query": "devon surf club"}))
        self.assertEqual(second.escalates_from, "")


class TestChargeStateMachine(LedgerCase):
    def test_settle_records_the_actual_and_its_source(self):
        self.open_caps()
        op = self.ledger.reserve(make_request())
        self.ledger.mark_dispatch_intent(op.operation_id)
        settled = self.ledger.settle(op.operation_id, actual_micros=750,
                                     actual_source=usage.ActualSource.PROVIDER_REPORTED,
                                     upstream_ref="req-123")
        self.assertEqual(settled.charge_state, usage.ChargeState.SETTLED)
        self.assertEqual(settled.actual_micros, 750)
        self.assertEqual(settled.upstream_ref, "req-123")
        head = self.ledger.remaining("run", "devon/run-01")
        self.assertEqual(head.spent_micros, 750)
        self.assertEqual(head.held_micros, 0)

    def test_settling_twice_with_different_numbers_is_refused(self):
        self.open_caps()
        op = self.ledger.reserve(make_request())
        self.ledger.mark_dispatch_intent(op.operation_id)
        self.ledger.settle(op.operation_id, actual_micros=750,
                           actual_source=usage.ActualSource.PROVIDER_REPORTED,
                           upstream_ref="req-750")
        with self.assertRaises(usage.LedgerRefused) as ctx:
            self.ledger.settle(op.operation_id, actual_micros=5000,
                               actual_source=usage.ActualSource.PROVIDER_REPORTED,
                               upstream_ref="req-5000")
        self.assertEqual(ctx.exception.code, "ALREADY_TERMINAL")
        self.assertEqual(self.ledger.get(op.operation_id).actual_micros, 750)

    def test_a_free_tier_settlement_needs_a_recorded_free_rate(self):
        self.open_caps()
        op = self.ledger.reserve(make_request(provider="tinyfish",
                                              account_ref="tinyfish-main"))
        self.ledger.mark_dispatch_intent(op.operation_id)
        settled = self.ledger.settle_free_tier(op.operation_id, quota_units=1,
                                               upstream_ref="tf-1")
        self.assertEqual(settled.charge_state, usage.ChargeState.FREE_TIER)
        self.assertEqual(settled.actual_micros, 0)
        paid = self.ledger.reserve(make_request(
            operation_id="op-paid", provider="serpapi", account_ref="serpapi-main",
            intent="a different question on a metered vendor",
            args={"query": "devon surf club"}))
        self.ledger.mark_dispatch_intent(paid.operation_id)
        with self.assertRaises(usage.LedgerRefused) as ctx:
            self.ledger.settle_free_tier(paid.operation_id, quota_units=1)
        self.assertEqual(ctx.exception.code, "FREE_TIER_CONTRADICTS_RATE")

    def test_no_charge_can_only_be_proven_before_dispatch(self):
        self.open_caps()
        op = self.ledger.reserve(make_request())
        proven = self.ledger.prove_no_charge(op.operation_id, reason="PRE_DISPATCH_REFUSAL",
                                            evidence_ref="policy-blocked")
        self.assertEqual(proven.charge_state, usage.ChargeState.NO_CHARGE_PROVEN)
        self.assertEqual(self.ledger.remaining("run", "devon/run-01").held_micros, 0)

        dispatched = self.ledger.reserve(make_request(operation_id="op-02",
                                                      args={"query": "another"}))
        self.ledger.mark_dispatch_intent(dispatched.operation_id)
        with self.assertRaises(usage.LedgerRefused) as ctx:
            self.ledger.prove_no_charge(dispatched.operation_id,
                                        reason="PRE_DISPATCH_REFUSAL")
        self.assertEqual(ctx.exception.code, "CANNOT_PROVE_NO_CHARGE_AFTER_DISPATCH")

    def test_an_unbounded_reason_cannot_be_used_to_prove_no_charge(self):
        self.open_caps()
        op = self.ledger.reserve(make_request())
        with self.assertRaises(usage.LedgerRefused) as ctx:
            self.ledger.prove_no_charge(op.operation_id, reason="looked fine to me")
        self.assertEqual(ctx.exception.code, "REASON_NOT_ACCEPTED")

    def test_unknown_can_only_be_declared_after_dispatch_intent(self):
        self.open_caps()
        op = self.ledger.reserve(make_request())
        with self.assertRaises(usage.LedgerRefused) as ctx:
            self.ledger.mark_unknown(op.operation_id, reason="PROVIDER_TIMEOUT")
        self.assertEqual(ctx.exception.code, "NOT_DISPATCHED")

    def test_unknown_charge_keeps_its_hold_until_it_is_reconciled(self):
        self.open_caps(calls=1)
        op = self.ledger.reserve(make_request())
        self.ledger.mark_dispatch_intent(op.operation_id)
        unknown = self.ledger.mark_unknown(op.operation_id, reason="PROVIDER_TIMEOUT")
        self.assertEqual(unknown.charge_state, usage.ChargeState.UNKNOWN)
        head = self.ledger.remaining("run", "devon/run-01")
        self.assertEqual(head.unknown_micros, 750)
        self.assertEqual(head.spent_micros, 0)
        # Exposure is not a free slot: the next operation is refused.
        with self.assertRaises(usage.LedgerRefused) as ctx:
            self.ledger.reserve(make_request(operation_id="op-02",
                                             args={"query": "next question"}))
        self.assertEqual(ctx.exception.code, "BUDGET_RUN_CEILING")
        self.assertEqual(ctx.exception.dimension, "calls")
        # Only an explicit, evidenced reconciliation closes it.
        reconciled = self.ledger.reconcile(
            op.operation_id, actual_micros=0, actual_source=usage.ActualSource.DERIVED,
            upstream_ref="late-status-1")
        self.assertEqual(reconciled.charge_state, usage.ChargeState.SETTLED)
        self.assertEqual(self.ledger.remaining("run", "devon/run-01").held_micros, 0)

    def test_reconciling_an_unknown_to_a_charge_keeps_it_in_the_books(self):
        self.open_caps(calls=5)
        op = self.ledger.reserve(make_request())
        self.ledger.mark_dispatch_intent(op.operation_id)
        self.ledger.mark_unknown(op.operation_id, reason="PROVIDER_TIMEOUT")
        reconciled = self.ledger.reconcile(
            op.operation_id, actual_micros=2250,
            actual_source=usage.ActualSource.PROVIDER_REPORTED, upstream_ref="late-1")
        self.assertEqual(reconciled.actual_micros, 2250)
        head = self.ledger.remaining("run", "devon/run-01")
        self.assertEqual(head.spent_micros, 2250)
        self.assertEqual(head.unknown_micros, 0)

    def test_reconciling_a_post_dispatch_no_charge_requires_evidence(self):
        self.open_caps()
        op = self.ledger.reserve(make_request())
        self.ledger.mark_dispatch_intent(op.operation_id)
        self.ledger.mark_unknown(op.operation_id, reason="PROVIDER_TIMEOUT")
        with self.assertRaises(usage.LedgerRefused) as ctx:
            self.ledger.reconcile_no_charge(op.operation_id, evidence_ref="")
        self.assertEqual(ctx.exception.code, "EVIDENCE_REQUIRED")
        closed = self.ledger.reconcile_no_charge(
            op.operation_id, evidence_ref="provider-refund-or-unbilled-4xx")
        self.assertEqual(closed.charge_state, usage.ChargeState.NO_CHARGE_PROVEN)
        self.assertEqual(closed.actual_source, usage.ActualSource.PROVIDER_REPORTED)
        self.assertEqual(self.ledger.remaining("run", "devon/run-01").held_micros, 0)

    def test_no_reachable_path_ends_a_dispatched_operation_without_evidence(self):
        """Every verb that could zero a dispatched hold, attempted in turn.

        Checking that four method *names* do not exist was the earlier version of this
        test, and an adversarial review walked straight past it: ``settle`` at zero and
        a caller-named local reuse both released or hid the hold without any of those
        names. What matters is the behaviour of the verbs that do exist.
        """
        self.open_caps()
        op = self.ledger.reserve(make_request())
        self.ledger.mark_dispatch_intent(op.operation_id)
        attempts = (
            ("settle at zero with no provider record",
             lambda: self.ledger.settle(op.operation_id, actual_micros=0,
                                        actual_source=usage.ActualSource.PROVIDER_REPORTED),
             "EVIDENCE_REQUIRED"),
            ("reconcile a row that was never unknown",
             lambda: self.ledger.reconcile(op.operation_id, actual_micros=0,
                                           actual_source=usage.ActualSource.DERIVED),
             "WRONG_STATE"),
            ("prove no charge after dispatch",
             lambda: self.ledger.prove_no_charge(op.operation_id,
                                                 reason="PRE_DISPATCH_REFUSAL"),
             "CANNOT_PROVE_NO_CHARGE_AFTER_DISPATCH"),
            ("reconcile no charge with no evidence",
             lambda: self.ledger.reconcile_no_charge(op.operation_id, evidence_ref=""),
             "EVIDENCE_REQUIRED"),
            ("free-tier settlement at a paid rate",
             lambda: self.ledger.settle_free_tier(op.operation_id, quota_units=1),
             "FREE_TIER_CONTRADICTS_RATE"),
        )
        for label, attempt, expected in attempts:
            with self.subTest(label=label):
                with self.assertRaises(usage.LedgerRefused) as ctx:
                    attempt()
                self.assertEqual(ctx.exception.code, expected)
        self.assertEqual(self.ledger.remaining("run", "devon/run-01").held_micros, 750)
        self.assertEqual(self.ledger.remaining("run", "devon/run-01").unknown_micros, 0)

    def test_dispatch_intent_is_idempotent_and_ordered(self):
        self.open_caps()
        op = self.ledger.reserve(make_request())
        first = self.ledger.mark_dispatch_intent(op.operation_id)
        second = self.ledger.mark_dispatch_intent(op.operation_id)
        self.assertEqual(first.dispatch_intent_at, second.dispatch_intent_at)
        with self.assertRaises(usage.LedgerRefused) as ctx:
            self.ledger.mark_dispatch_intent("op-does-not-exist")
        self.assertEqual(ctx.exception.code, "UNKNOWN_OPERATION")

    def test_local_reuse_records_no_upstream_call_and_cannot_be_dispatched(self):
        self.open_caps()
        reuse = self.ledger.record_local_reuse(
            project="devon", run_id="run-01", capability="render",
            evidence_ref="sha256:prior-page", intent="reuse the saved page")
        self.assertEqual(reuse.charge_state, usage.ChargeState.NO_CHARGE_PROVEN)
        self.assertEqual(reuse.origin, "local")
        self.assertEqual(reuse.counts_against_caps, 0)
        self.assertEqual(self.ledger.remaining("run", "devon/run-01").held_micros, 0)
        with self.assertRaises(usage.LedgerRefused) as ctx:
            self.ledger.mark_dispatch_intent(reuse.operation_id)
        self.assertEqual(ctx.exception.code, "NOT_RESERVED")
        # Recording the same reuse twice is one row, not two.
        again = self.ledger.record_local_reuse(
            project="devon", run_id="run-01", capability="render",
            evidence_ref="sha256:prior-page", intent="reuse the saved page")
        self.assertEqual(again.operation_id, reuse.operation_id)


class TestCrashSafety(LedgerCase):
    """Crash is a real kill: the process is replaced, the database is not."""

    def _crash(self, script: str) -> None:
        code = "\n".join((
            "import sys, os",
            f"sys.path.insert(0, {str(ROOT / 'scripts')!r})",
            f"sys.path.insert(0, {str(Path(__file__).resolve().parent)!r})",
            "import test_research_usage_ledger as t",
            f"ledger = t.open_ledger({str(self.path)!r}, t.Clock())",
            "ledger.set_cap('run', 'devon/run-01', ceiling_micros=2000000,"
            " ceiling_calls=10, ceiling_seconds=3600, source='test-operator')",
            script,
        ))
        proc = subprocess.run([sys.executable, "-c", code], capture_output=True,
                              text=True, timeout=180)
        self.assertIn("CRASH", proc.stdout, proc.stdout + proc.stderr)
        self.assertEqual(proc.returncode, 9, proc.stdout + proc.stderr)

    def test_crash_right_after_reserve_leaves_a_row_that_holds_money(self):
        self._crash("\n".join((
            "op = ledger.reserve(t.make_request())",
            "print('CRASH', flush=True)",
            "os._exit(9)",
        )))
        reopened = self.reopen()
        rows = reopened.open_operations(run_id="run-01")
        self.assertEqual(len(rows), 1)
        self.assertIsNone(rows[0].dispatch_intent_at)
        # It never reached the network, so this one really is provably uncharged.
        proven = reopened.prove_no_charge(rows[0].operation_id,
                                          reason="PRE_DISPATCH_REFUSAL",
                                          evidence_ref="crash-before-dispatch")
        self.assertEqual(proven.charge_state, usage.ChargeState.NO_CHARGE_PROVEN)

    def test_crash_after_dispatch_intent_can_never_be_written_off(self):
        self._crash("\n".join((
            "op = ledger.reserve(t.make_request())",
            "ledger.mark_dispatch_intent(op.operation_id)",
            "print('CRASH', flush=True)",
            "os._exit(9)",
        )))
        reopened = self.reopen()
        rows = reopened.open_operations(run_id="run-01")
        self.assertEqual(len(rows), 1)
        # Intent is recorded before the call, so a crash moves the books in the
        # only safe direction: the operation counts as possibly charged.
        self.assertIsNotNone(rows[0].dispatch_intent_at)
        with self.assertRaises(usage.LedgerRefused):
            reopened.prove_no_charge(rows[0].operation_id, reason="PRE_DISPATCH_REFUSAL")
        unknown = reopened.mark_unknown(rows[0].operation_id, reason="PROCESS_DIED")
        self.assertEqual(unknown.charge_state, usage.ChargeState.UNKNOWN)
        self.assertEqual(reopened.remaining("run", "devon/run-01").unknown_micros, 750)

    def test_a_settlement_survives_a_reopen(self):
        self.open_caps()
        op = self.ledger.reserve(make_request())
        self.ledger.mark_dispatch_intent(op.operation_id)
        self.ledger.settle(op.operation_id, actual_micros=750,
                           actual_source=usage.ActualSource.PROVIDER_REPORTED,
                           upstream_ref="req-reopen")
        reopened = self.reopen()
        self.assertEqual(reopened.get(op.operation_id).charge_state,
                         usage.ChargeState.SETTLED)
        self.assertEqual(reopened.remaining("run", "devon/run-01").spent_micros, 750)

    def test_a_refused_settlement_writes_nothing(self):
        self.open_caps()
        op = self.ledger.reserve(make_request())
        self.ledger.mark_dispatch_intent(op.operation_id)
        with self.assertRaises(usage.LedgerRefused):
            self.ledger.settle(op.operation_id, actual_micros=750,
                               actual_source=usage.ActualSource.NONE)
        self.assertEqual(self.reopen().get(op.operation_id).charge_state,
                         usage.ChargeState.RESERVED)

    def test_recovery_reports_open_rows_without_deciding_anything(self):
        self.open_caps()
        self.ledger.reserve(make_request())
        self.ledger.reserve(make_request(operation_id="op-02", args={"query": "two"}))
        report = self.reopen().recover(project="devon")
        self.assertEqual(report["open_operations"], 2)
        self.assertEqual(report["dispatched_unsettled"], 0)
        self.assertEqual(report["never_dispatched"], 2)
        self.assertEqual(report["action_taken"], "REPORT_ONLY")
        self.assertEqual(self.ledger.remaining("run", "devon/run-01").held_micros, 1500)


class TestCapsWindowsAndConfig(LedgerCase):
    def test_a_daily_window_resets_on_the_next_day(self):
        self.open_caps(calls=10)
        self.ledger.set_cap("run", "devon/run-01", window="day", ceiling_micros=2_000_000,
                            ceiling_calls=1, ceiling_seconds=3600, source="test-operator")
        self.ledger.reserve(make_request())
        with self.assertRaises(usage.LedgerRefused) as ctx:
            self.ledger.reserve(make_request(operation_id="op-02", args={"query": "two"}))
        self.assertEqual(ctx.exception.code, "BUDGET_RUN_CEILING")
        self.assertEqual(ctx.exception.dimension, "calls")
        self.clock.advance(days=1)
        tomorrow = self.ledger.reserve(make_request(operation_id="op-03",
                                                    args={"query": "three"}))
        self.assertEqual(tomorrow.charge_state, usage.ChargeState.RESERVED)

    def test_a_monthly_window_does_not_reset_at_a_day_boundary(self):
        self.open_caps(calls=10)
        self.ledger.set_cap("project", "devon", window="month", ceiling_micros=1_000_000,
                            ceiling_calls=1, ceiling_seconds=3600, source="test-operator")
        self.ledger.reserve(make_request())
        self.clock.advance(days=1)
        with self.assertRaises(usage.LedgerRefused) as ctx:
            self.ledger.reserve(make_request(operation_id="op-02", args={"query": "two"}))
        self.assertEqual(ctx.exception.code, "BUDGET_PROJECT_CEILING")
        self.assertEqual(ctx.exception.dimension, "calls")
        self.clock.advance(days=40)
        self.ledger.reserve(make_request(operation_id="op-03", args={"query": "three"}))

    def test_a_ceiling_cannot_be_lowered_below_what_is_already_held(self):
        self.open_caps()
        self.ledger.reserve(make_request())
        with self.assertRaises(usage.LedgerRefused) as ctx:
            self.ledger.set_cap("run", "devon/run-01", ceiling_micros=100,
                                ceiling_calls=10, ceiling_seconds=3600,
                                source="test-operator")
        self.assertEqual(ctx.exception.code, "CAP_BELOW_HELD")

    def test_an_unreviewed_capability_is_denied_without_a_tier_to_fall_back_on(self):
        self.open_caps()
        with self.assertRaises(usage.LedgerRefused) as ctx:
            self.ledger.reserve(make_request(capability="brand_new_vendor_tool"))
        # One condition, one code: the registry's answer is relayed verbatim rather
        # than renamed by the layer that happened to receive it.
        self.assertEqual(ctx.exception.code, "UNREVIEWED_CAPABILITY")

    def test_an_unpriced_capability_is_refused_rather_than_estimated(self):
        self.open_caps()
        bare = usage.UsageLedger(self.path, pricing=usage.PricingCatalog(()),
                                 registry=registry(), clock=self.clock)
        with self.assertRaises(usage.LedgerRefused) as ctx:
            bare.reserve(make_request())
        self.assertEqual(ctx.exception.code, "PRICE_UNKNOWN")

    def test_a_tier_three_operation_is_refused_without_a_grant(self):
        self.open_caps()
        with self.assertRaises(usage.LedgerRefused) as ctx:
            self.ledger.reserve(self.agent_request())
        self.assertEqual(ctx.exception.code, "GRANT_REQUIRED")

    def test_a_tier_three_operation_is_refused_when_no_key_can_verify_a_grant(self):
        # Nothing is configured that could verify a token, so even a well-formed one
        # is refused: T3 stays closed rather than trusting what the caller produced.
        self.open_caps()
        with self.assertRaises(usage.LedgerRefused) as ctx:
            self.ledger.reserve(self.agent_request(grant="marketops-grant-v1.eyJhIjoxfQ.AAAA"))
        self.assertEqual(ctx.exception.code, "GRANT_VERIFICATION_UNAVAILABLE")

    @unittest.skipUnless(CRYPTO, "Ed25519 signing needs the optional cryptography package")
    def test_a_verified_grant_opens_tier_three_and_a_foreign_key_does_not(self):
        private, public = keypair()
        authority = grants.GrantAuthority(grants.load_public_key(public))
        ledger = usage.UsageLedger(self.path, pricing=pricing(), registry=registry(),
                                   clock=self.clock, grants_authority=authority)
        self.open_caps()
        op = ledger.reserve(self.agent_request(grant=sign_grant(private)))
        self.assertEqual(op.charge_state, usage.ChargeState.RESERVED)
        self.assertTrue(op.grant_ref.startswith("gnt-"))
        # A token signed by a key this authority does not know is refused, so the
        # grant does work rather than being a shape the caller can fill in.
        with self.assertRaises(usage.LedgerRefused) as ctx:
            ledger.reserve(self.agent_request(operation_id="op-agent-2",
                                              grant=sign_grant(keypair()[0])))
        self.assertEqual(ctx.exception.code, "GRANT_SIGNATURE_INVALID")

    def test_a_malformed_pricing_catalog_is_refused_at_construction(self):
        for bad in ({"provider": "serper"},
                    {"provider": "serper", "capability": "search", "unit_micros": "free"},
                    {"provider": "serper", "capability": "search", "unit_micros": 1.5},
                    {"provider": "serper", "capability": "search", "unit_micros": -1},
                    {"provider": "serper", "capability": "search", "unit_micros": 1,
                     "quota_per_call": 0},
                    {"provider": "serper", "capability": "search", "unit_micros": 1,
                     "quota_unit": "credits", "quota_per_call": 1, "source": ""}):
            with self.subTest(bad=bad):
                with self.assertRaises(usage.PricingConfigInvalid):
                    usage.PricingCatalog.from_entries((bad,))
        with self.assertRaises(usage.PricingConfigInvalid):
            usage.PricingCatalog.from_json("not json at all")

    def test_a_partial_operation_request_is_refused(self):
        self.open_caps()
        for overrides in ({"project": ""}, {"run_id": "   "}, {"operation_id": "has space"},
                          {"account_ref": ""}, {"intent": ""}, {"units": 0}):
            with self.subTest(overrides=overrides):
                with self.assertRaises(usage.LedgerRefused) as ctx:
                    self.ledger.reserve(make_request(**overrides))
                self.assertEqual(ctx.exception.code, "INVALID_REQUEST")

    def test_arguments_that_cannot_be_canonicalised_are_refused(self):
        self.open_caps()
        with self.assertRaises(usage.LedgerRefused) as ctx:
            self.ledger.reserve(make_request(args={"num": 1.5}))
        self.assertEqual(ctx.exception.code, "ARGS_NOT_CANONICAL")
        # Key order is not a difference: the same call written two ways is one call.
        self.ledger.reserve(make_request(args={"query": "a b", "num": 10}))
        with self.assertRaises(usage.LedgerRefused) as ctx:
            self.ledger.reserve(make_request(operation_id="op-02",
                                             args={"num": 10, "query": "a b"}))
        self.assertEqual(ctx.exception.code, "DUPLICATE_OPERATION_SIGNATURE")


class TestReporting(LedgerCase):
    def test_report_keeps_money_and_quota_units_and_unknown_exposure_apart(self):
        self.open_caps()
        settled = self.ledger.reserve(make_request())
        self.ledger.mark_dispatch_intent(settled.operation_id)
        self.ledger.settle(settled.operation_id, actual_micros=750,
                           actual_source=usage.ActualSource.PROVIDER_REPORTED,
                           upstream_ref="req-settled")
        free = self.ledger.reserve(make_request(operation_id="op-free", provider="tinyfish",
                                                account_ref="tinyfish-main",
                                                intent="a separate question answered for free",
                                                args={"query": "free tier question"}))
        self.ledger.mark_dispatch_intent(free.operation_id)
        self.ledger.settle_free_tier(free.operation_id, quota_units=1, upstream_ref="tf-free")
        unknown = self.ledger.reserve(make_request(operation_id="op-unknown",
                                                   provider="serpapi",
                                                   account_ref="serpapi-main",
                                                   intent="yet another question",
                                                   args={"query": "third question"}))
        self.ledger.mark_dispatch_intent(unknown.operation_id)
        self.ledger.mark_unknown(unknown.operation_id, reason="PROVIDER_TIMEOUT")

        report = self.ledger.report("devon", accepted_outputs=2)
        self.assertEqual(report["operations"], 3)
        self.assertEqual(report["by_charge_state"][usage.ChargeState.SETTLED.value], 1)
        self.assertEqual(report["by_charge_state"][usage.ChargeState.FREE_TIER.value], 1)
        self.assertEqual(report["by_charge_state"][usage.ChargeState.UNKNOWN.value], 1)
        self.assertEqual(report["money"]["actual_micros"], 750)
        self.assertEqual(report["money"]["unknown_exposure_micros"], 1000)
        self.assertEqual(report["money"]["estimated_micros"], 1750)
        self.assertEqual(report["quota_units"]["credits"], 1)
        self.assertEqual(report["quota_units"]["requests"], 1)
        self.assertEqual(report["quota_units"]["searches"], 1)
        # 750 settled against 2 accepted outputs is 375 *confirmed* - but 1000 micros of
        # this run's spend is unresolved, so the cost of those two outputs is somewhere
        # between 375 and 875 and nobody knows where. The derived figure is withheld
        # rather than printed as the low end: an unknown cost must not read as a fact.
        self.assertIsNone(report["cost_per_accepted_output_micros"])
        self.assertEqual(report["money"]["basis"], "INDETERMINATE")
        self.assertEqual(report["money"]["basis_code"], "UNKNOWN_COST_OUTSTANDING")
        self.assertEqual(report["cost_per_accepted_output_basis"], "UNKNOWN_COST_OUTSTANDING")
        self.assertEqual(report["by_provider"]["serper"]["actual_micros"], 750)
        self.assertEqual(report["by_provider"]["serpapi"]["unknown_exposure_micros"], 1000)
        self.assertIn("not a vendor invoice", " ".join(report["notes"]))
        self.assertTrue(any("withheld" in note for note in report["notes"]))

    def test_a_credential_shaped_string_cannot_be_injected_anywhere(self):
        """Injected through the public API, then looked for everywhere it could land.

        The earlier version asserted only that ``report()`` contained no key-shaped
        *field name*, which can only fail if the module invents one. The realistic
        vector is a caller-supplied value, and it has a specific shape: a provider
        error body, a URL with a query string, anything free-text. Those are refused.

        What is **not** claimed: an identifier-shaped secret - ``sk-live-<hex>`` is a
        perfectly good identifier - is stored, because a reference field exists to hold
        the provider's own id and the ledger cannot tell one string from another. So
        the test asserts the two things that are true: free text is refused at the
        boundary, and nothing prints references into the report or the recovery
        reading, which is where a leak would be read rather than found.
        """
        self.open_caps()
        op = self.ledger.reserve(make_request())
        self.ledger.mark_dispatch_intent(op.operation_id)
        for free_text in ('HTTP 401 {"error":"invalid key sk-live-DEADBEEF"}',
                          "https://serpapi.com/search.json?api_key=abc123",
                          "token\nsecond line"):
            with self.subTest(free_text=free_text[:24]):
                with self.assertRaises(usage.LedgerRefused) as ctx:
                    self.ledger.settle(op.operation_id, actual_micros=750,
                                       actual_source=usage.ActualSource.PROVIDER_REPORTED,
                                       upstream_ref=free_text)
                self.assertEqual(ctx.exception.code, "REFERENCE_NOT_OPAQUE")
        with self.assertRaises(usage.LedgerRefused):
            self.ledger.prove_no_charge(op.operation_id, reason="PRE_DISPATCH_REFUSAL",
                                        evidence_ref="body with spaces")
        # An identifier-shaped value is accepted and bounded, and the report and the
        # recovery reading do not carry it.
        self.ledger.settle(op.operation_id, actual_micros=750,
                           actual_source=usage.ActualSource.PROVIDER_REPORTED,
                           upstream_ref="req-abc123")
        printed = (json.dumps(self.ledger.report("devon"))
                   + json.dumps(self.ledger.recover(project="devon")))
        self.assertNotIn("req-abc123", printed)
        for marker in ("api_key", "secret", "password", "bearer"):
            self.assertNotIn(marker, printed)

    def test_the_audit_trail_is_append_only_and_readable(self):
        self.open_caps()
        op = self.ledger.reserve(make_request())
        self.ledger.mark_dispatch_intent(op.operation_id)
        self.ledger.mark_unknown(op.operation_id, reason="PROVIDER_TIMEOUT")
        events = self.ledger.events(op.operation_id)
        self.assertEqual([event["kind"] for event in events],
                         ["reserve", "dispatch_intent", "mark_unknown"])
        self.assertEqual(self.ledger.events()[0]["kind"], "reserve")
        self.assertEqual(events[-1]["code"], "PROVIDER_TIMEOUT")

    def test_usage_is_reported_per_scope_and_unknown_counts_as_held(self):
        self.open_caps()
        op = self.ledger.reserve(make_request())
        self.ledger.mark_dispatch_intent(op.operation_id)
        self.ledger.mark_unknown(op.operation_id, reason="PROVIDER_TIMEOUT")
        row = self.ledger.usage("run", "devon/run-01")[0]
        self.assertEqual(row.scope_key, "devon/run-01")
        self.assertEqual(row.reserved_micros, 0)
        self.assertEqual(row.unknown_micros, 750)
        self.assertEqual(row.held_micros, 750)
        self.assertEqual(row.remaining_micros, 2_000_000 - 750)
        self.assertEqual(row.calls_used, 1)
        self.assertEqual(row.remaining_calls, 9)

    def test_history_of_a_finished_run_still_reads_back(self):
        self.open_caps()
        op = self.ledger.reserve(make_request())
        self.ledger.mark_dispatch_intent(op.operation_id)
        self.ledger.settle(op.operation_id, actual_micros=750,
                           actual_source=usage.ActualSource.PROVIDER_REPORTED,
                           upstream_ref="req-history")
        reopened = self.reopen()
        row = reopened.get(op.operation_id)
        self.assertEqual(row.charge_state, usage.ChargeState.SETTLED)
        self.assertEqual(row.op_signature, op.op_signature)
        self.assertEqual(row.price_version, "2026-10-17")
        self.assertEqual(reopened.operations(project="devon")[0].operation_id,
                         op.operation_id)


class TestReviewRegressions(LedgerCase):
    """One test per hole an adversarial review found by running code against the module.

    Each of these was reachable before the fix; the docstring says which claim it
    closes so that a future reader can tell what would break if it were removed.
    """

    def test_settle_without_a_dispatch_intent_is_refused(self):
        # Otherwise a caller can reach the network, never record the intent, and then
        # write the call off as never dispatched - the one ordering the ledger cannot
        # see. Requiring the intent for a settle removes the half of it it can.
        self.open_caps()
        op = self.ledger.reserve(make_request())
        with self.assertRaises(usage.LedgerRefused) as ctx:
            self.ledger.settle(op.operation_id, actual_micros=750,
                               actual_source=usage.ActualSource.DERIVED)
        self.assertEqual(ctx.exception.code, "NOT_DISPATCHED")
        self.assertEqual(self.ledger.remaining("run", "devon/run-01").held_micros, 750)

    def test_a_provider_reported_amount_must_name_its_record(self):
        # "Settled at zero, provider said so, no reference" was indistinguishable from
        # a caller deciding an inconvenient hold was free.
        self.open_caps()
        op = self.ledger.reserve(make_request())
        self.ledger.mark_dispatch_intent(op.operation_id)
        with self.assertRaises(usage.LedgerRefused) as ctx:
            self.ledger.settle(op.operation_id, actual_micros=0,
                               actual_source=usage.ActualSource.PROVIDER_REPORTED)
        self.assertEqual(ctx.exception.code, "EVIDENCE_REQUIRED")
        self.assertEqual(self.ledger.remaining("run", "devon/run-01").held_micros, 750)
        settled = self.ledger.settle(op.operation_id, actual_micros=0,
                                     actual_source=usage.ActualSource.PROVIDER_REPORTED,
                                     upstream_ref="provider-req-0")
        self.assertEqual(settled.charge_state, usage.ChargeState.SETTLED)
        self.assertEqual(self.ledger.remaining("run", "devon/run-01").held_micros, 0)

    def test_a_reconciliation_must_name_where_its_number_came_from(self):
        self.open_caps()
        op = self.ledger.reserve(make_request())
        self.ledger.mark_dispatch_intent(op.operation_id)
        self.ledger.mark_unknown(op.operation_id, reason="PROVIDER_TIMEOUT")
        with self.assertRaises(usage.LedgerRefused) as ctx:
            self.ledger.reconcile(op.operation_id, actual_micros=2250,
                                  actual_source=usage.ActualSource.PROVIDER_REPORTED)
        self.assertEqual(ctx.exception.code, "EVIDENCE_REQUIRED")
        self.assertEqual(self.ledger.remaining("run", "devon/run-01").unknown_micros, 750)

    def test_a_local_reuse_is_never_handed_back_as_a_live_hold(self):
        # The id used to be a caller argument, so naming a live reservation returned
        # that reservation to a caller who believed it was a cache hit.
        params = inspect.signature(usage.UsageLedger.record_local_reuse).parameters
        self.assertNotIn("operation_id", params)
        self.open_caps()
        live = self.ledger.reserve(make_request())
        self.ledger.mark_dispatch_intent(live.operation_id)
        reuse = self.ledger.record_local_reuse(
            project="devon", run_id="run-01", capability="render",
            evidence_ref="sha256:cached-page", intent="reuse the saved page")
        self.assertEqual(reuse.origin, "local")
        self.assertEqual(reuse.charge_state, usage.ChargeState.NO_CHARGE_PROVEN)
        self.assertIsNone(reuse.dispatch_intent_at)
        self.assertNotEqual(reuse.operation_id, live.operation_id)
        self.assertEqual(self.ledger.remaining("run", "devon/run-01").held_micros, 750)

    def test_a_transport_outside_the_reviewed_allowlist_is_refused(self):
        # The allowlist existed in the policy module and was inert on the path that
        # actually holds budget.
        registry_with_allowlist = grants.CapabilityRegistry((
            grants.Capability("search", "serper", grants.Tier.T2, "search_query",
                              "read_only", allowed_transports=("embedded_official_mcp",)),
        ))
        ledger = usage.UsageLedger(self.path, pricing=pricing(),
                                   registry=registry_with_allowlist, clock=self.clock)
        self.open_caps()
        with self.assertRaises(usage.LedgerRefused) as ctx:
            ledger.reserve(make_request(transport="rest_https"))
        self.assertEqual(ctx.exception.code, "TRANSPORT_NOT_REVIEWED")
        allowed = ledger.reserve(make_request(operation_id="op-mcp",
                                              transport="embedded_official_mcp"))
        self.assertEqual(allowed.transport, "embedded_official_mcp")

    def test_units_are_part_of_what_is_being_bought(self):
        # 1 step and 25 steps used to share a signature, so the second purchase was
        # refused as a duplicate of the first.
        self.open_caps()
        single = self.ledger.reserve(make_request())
        bundle = self.ledger.reserve(make_request(operation_id="op-bundle", units=25))
        self.assertNotEqual(single.op_signature, bundle.op_signature)
        self.assertEqual(bundle.estimated_micros, 750 * 25)
        self.assertEqual(bundle.quota_units, 25)

    def test_the_same_query_in_two_unicode_encodings_is_one_operation(self):
        self.open_caps()
        self.ledger.reserve(make_request(args={"query": "caf\u00e9 listings"}))
        with self.assertRaises(usage.LedgerRefused) as ctx:
            self.ledger.reserve(make_request(operation_id="op-nfd",
                                             args={"query": "cafe\u0301 listings"}))
        self.assertEqual(ctx.exception.code, "DUPLICATE_OPERATION_SIGNATURE")

    def test_two_sites_are_two_operations(self):
        base = dict(project="devon", run_id="run-01", operation_id="op-01",
                    provider="tinyfish", capability="profile_read", account_ref="main",
                    transport="embedded_official_mcp", intent="read the portal",
                    args={"portal": "example"})
        first = usage.OperationRequest(**base, site="portal-a")
        second = usage.OperationRequest(**base, site="portal-b")
        self.assertNotEqual(usage.op_signature(first, first.args),
                            usage.op_signature(second, second.args))

    def test_a_t4_operation_must_name_its_site(self):
        self.open_caps()
        with self.assertRaises(usage.LedgerRefused) as ctx:
            self.ledger.reserve(make_request(
                operation_id="op-portal", provider="tinyfish", account_ref="tinyfish-main",
                capability="profile_read", intent="read the portal", args={"portal": "example"}))
        self.assertEqual(ctx.exception.code, "SITE_REQUIRED")

    @unittest.skipUnless(CRYPTO, "Ed25519 signing needs the optional cryptography package")
    def test_a_grants_own_limits_are_consulted(self):
        # max_micros and max_calls were minted and then never read, which made them
        # decoration. A grant that names a limit is now checked against the run's
        # actual totals, in the same transaction as the ceilings.
        private, public = keypair()
        ledger = usage.UsageLedger(self.path, pricing=pricing(), registry=registry(),
                                   clock=self.clock,
                                   grants_authority=grants.GrantAuthority(
                                       grants.load_public_key(public)))
        self.open_caps()
        token = sign_grant(private)  # max_calls=1, max_micros=1_000_000
        ledger.reserve(self.agent_request(grant=token))
        with self.assertRaises(usage.LedgerRefused) as ctx:
            ledger.reserve(self.agent_request(operation_id="op-agent-2",
                                              intent="a second multi-step run",
                                              args={"url": "https://example.test/two"},
                                              grant=token))
        self.assertEqual(ctx.exception.code, "GRANT_CALL_LIMIT_EXCEEDED")
        tiny = sign_grant(private, max_micros=1_000, max_calls=5)
        with self.assertRaises(usage.LedgerRefused) as ctx:
            ledger.reserve(self.agent_request(operation_id="op-agent-3",
                                              intent="a third multi-step run",
                                              args={"url": "https://example.test/three"},
                                              grant=tiny))
        self.assertEqual(ctx.exception.code, "GRANT_AMOUNT_EXCEEDED")

    def test_a_quota_unit_that_is_not_reviewed_is_refused(self):
        # ``quota_unit="USD"`` used to be accepted and then filed next to money as if
        # it were not money.
        self.open_caps()
        with self.assertRaises(usage.LedgerRefused) as ctx:
            self.ledger.reserve(make_request(quota_unit="USD"))
        self.assertEqual(ctx.exception.code, "QUOTA_UNIT_UNREVIEWED")
        with self.assertRaises(usage.LedgerRefused) as ctx:
            self.ledger.reserve(make_request(quota_unit="micros"))
        self.assertEqual(ctx.exception.code, "QUOTA_UNIT_UNREVIEWED")

    def test_an_operation_ceiling_is_per_operation_and_stays_in_its_project(self):
        self.open_caps()
        self.ledger.set_cap("project", "other", ceiling_micros=2_000_000, ceiling_calls=10,
                            ceiling_seconds=3600, source="test-operator")
        self.ledger.set_cap("run", "other/run-01", ceiling_micros=2_000_000, ceiling_calls=10,
                            ceiling_seconds=3600, source="test-operator")
        self.ledger.set_cap("operation", "devon/*", ceiling_micros=100, source="test-operator")
        with self.assertRaises(usage.LedgerRefused) as ctx:
            self.ledger.reserve(make_request())
        self.assertEqual(ctx.exception.code, "BUDGET_OPERATION_CEILING")
        self.assertEqual(ctx.exception.dimension, "money")
        # It bounds this project's operations only, and it bounds one operation - not
        # the project's accumulated spend.
        elsewhere = self.ledger.reserve(make_request(project="other", operation_id="op-other"))
        self.assertEqual(elsewhere.charge_state, usage.ChargeState.RESERVED)
        # And a bare '*' cannot be used to bound every project at once.
        with self.assertRaises(usage.LedgerRefused) as ctx:
            self.ledger.set_cap("operation", "*", ceiling_micros=1, source="test-operator")
        self.assertEqual(ctx.exception.code, "INVALID_REQUEST")


if __name__ == "__main__":
    unittest.main()
