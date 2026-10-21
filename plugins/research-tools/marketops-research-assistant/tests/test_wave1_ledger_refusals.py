"""How this ledger's refusals reach a host: governed, contract-named, pinned.

QA round 5 measured the failure these tests exist for. The ledger refused correctly,
the gateway could not express the refusal, and the host got an unhandled
``ContractViolation: ledger refused with a code outside the closed set: None``. Two
causes: the exception carried ``code`` while the gateway reads ``reason_code``, and the
two vocabularies overlapped in one code out of dozens.

Three things are asserted here:

* a caller of the ledger never receives SQLite's exception - contention becomes
  ``LEDGER_BUSY``, with a bounded wait and nothing written;
* every code the module can raise has a disposition, and every disposition's target is
  a real §5.3 name (or the empty string, meaning "this is a caller error, raise
  ContractViolation rather than report a budget decision");
* ceiling refusals already carry the contract's names, with the dimension alongside.

Offline: private temporary databases, no network, no credential, no provider call.
"""
from __future__ import annotations

import re
import sqlite3
import sys
import tempfile
import time
import unittest
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import research_grants as grants
import research_usage_ledger as usage

NOW = datetime(2026, 10, 17, 9, 0, tzinfo=timezone.utc)
LEDGER_SOURCE = (ROOT / "scripts" / "research_usage_ledger.py").read_text(encoding="utf-8")


def registry() -> grants.CapabilityRegistry:
    return grants.CapabilityRegistry((
        grants.Capability("search", "serper", grants.Tier.T2, "search_query", "read_only"),
    ))


def request(**overrides) -> usage.OperationRequest:
    base = dict(project="devon", run_id="run-01", operation_id="op-01",
                provider="serper", capability="search", account_ref="serper-main",
                transport="rest_https", intent="find the venue",
                args={"query": "devon venue"})
    base.update(overrides)
    return usage.OperationRequest(**base)


class RefusalCase(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "state" / "usage-ledger.sqlite3"
        self.clock = lambda: NOW

    def ledger(self, *, busy_timeout_ms=20_000, calls=100) -> usage.UsageLedger:
        ledger = usage.UsageLedger(self.path, pricing=usage.documented_pricing(),
                                   registry=registry(), clock=self.clock,
                                   busy_timeout_ms=busy_timeout_ms)
        ledger.set_cap("run", "devon/run-01", ceiling_micros=0, ceiling_calls=calls,
                       ceiling_seconds=3_600, source="refusal-test")
        return ledger

    def holder(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.path, isolation_level=None)
        self.addCleanup(db.close)
        db.execute("BEGIN EXCLUSIVE")
        return db


class TestContentionIsGoverned(RefusalCase):
    """A ledger caller gets a refusal with a code, never SQLite's exception."""

    def test_a_reserve_against_a_held_write_lock_is_refused_with_a_code(self):
        ledger = self.ledger(busy_timeout_ms=200)
        holder = self.holder()
        started = time.monotonic()
        try:
            with self.assertRaises(usage.LedgerRefused) as ctx:
                ledger.reserve(request())
            elapsed = time.monotonic() - started
        except sqlite3.OperationalError as exc:          # what the module did before
            self.fail(f"a raw driver error escaped instead of a governed refusal: {exc}")
        finally:
            holder.execute("ROLLBACK")
        self.assertEqual(ctx.exception.code, "LEDGER_BUSY")
        self.assertEqual(ctx.exception.reason_code, "LEDGER_BUSY")
        self.assertLess(elapsed, 5, "the wait must be the bounded busy timeout, not a spin")
        self.assertEqual(ledger.operations(), ())

    def test_the_ledger_records_nothing_when_it_cannot_record(self):
        ledger = self.ledger(busy_timeout_ms=200)
        holder = self.holder()
        try:
            with self.assertRaises(usage.LedgerRefused):
                ledger.reserve(request())
        finally:
            holder.execute("ROLLBACK")
        # Once the lock is gone the same operation succeeds, and exactly one row exists.
        ledger.reserve(request())
        self.assertEqual(len(ledger.operations()), 1)

    def test_a_cap_change_under_contention_is_also_governed(self):
        ledger = self.ledger(busy_timeout_ms=200)
        holder = self.holder()
        try:
            with self.assertRaises(usage.LedgerRefused) as ctx:
                ledger.set_cap("run", "devon/run-01", ceiling_calls=5, source="refusal-test")
        finally:
            holder.execute("ROLLBACK")
        self.assertEqual(ctx.exception.code, "LEDGER_BUSY")

    def test_the_busy_timeout_is_configurable_and_validated(self):
        self.ledger(busy_timeout_ms=50)          # accepted
        for bad in (0, -1, 1.5, "200"):
            with self.subTest(bad=bad):
                with self.assertRaises(usage.LedgerRefused) as ctx:
                    usage.UsageLedger(self.path, pricing=usage.documented_pricing(),
                                      registry=registry(), busy_timeout_ms=bad)
                self.assertEqual(ctx.exception.code, "INVALID_REQUEST")


class TestTheRefusalVocabularyIsPinned(RefusalCase):
    """Every code the module can raise has a disposition, and every target is real."""

    def raised_codes(self) -> set[str]:
        return set(re.findall(r'LedgerRefused\(\s*"([A-Z0-9_]+)"', LEDGER_SOURCE))

    def test_every_literal_refusal_has_a_disposition(self):
        unclassified = sorted(code for code in self.raised_codes()
                              if code not in usage.CONTRACT_REASON_CODES
                              and code not in usage.REFUSAL_DISPOSITION)
        self.assertEqual(unclassified, [],
                         "a refusal code with no disposition would reach a host as an "
                         "unhandled ContractViolation; add it to REFUSAL_DISPOSITION")

    def test_every_disposition_target_is_a_real_contract_code_or_empty(self):
        bad = sorted(target for target in usage.REFUSAL_DISPOSITION.values()
                     if target and target not in usage.CONTRACT_REASON_CODES)
        self.assertEqual(bad, [], "a disposition must point at a §5.3 name, or be empty "
                                  "to mean 'this is a caller error'")

    def test_the_codes_relayed_from_the_grant_layer_are_governed(self):
        # ``_verified_grant`` re-raises GrantRefused verbatim, so these reach a host
        # through a managed call and cannot be left unclassified.
        relayed = ("GRANT_VERIFICATION_UNAVAILABLE", "GRANT_MALFORMED", "GRANT_TIER_TOO_LOW",
                   "GRANT_SITE_REQUIRED", "GRANT_SCOPE_MISMATCH_PROJECT",
                   "GRANT_SCOPE_MISMATCH_RUN_ID", "GRANT_SCOPE_MISMATCH_PROVIDER",
                   "GRANT_SCOPE_MISMATCH_CAPABILITY", "GRANT_SCOPE_MISMATCH_ACCOUNT_REF",
                   "GRANT_SCOPE_MISMATCH_SITE", "GRANT_EXPIRED", "GRANT_SIGNATURE_INVALID")
        for code in relayed:
            with self.subTest(code=code):
                self.assertEqual(usage.refusal_disposition(code)[0], "BLOCKED", code)

    def test_contract_codes_are_returned_unchanged(self):
        for code in sorted(usage.CONTRACT_REASON_CODES):
            with self.subTest(code=code):
                self.assertEqual(usage.refusal_disposition(code), ("BLOCKED", code))

    def test_a_state_error_is_a_contract_violation_not_a_budget_decision(self):
        for code in ("ALREADY_TERMINAL", "NOT_RESERVED", "NOT_DISPATCHED",
                     "UNKNOWN_OPERATION", "INTERNAL", "EVIDENCE_REQUIRED"):
            with self.subTest(code=code):
                self.assertEqual(usage.refusal_disposition(code), ("CONTRACT_VIOLATION", code))

    def test_an_unknown_or_empty_code_never_becomes_a_blocked_with_no_reason(self):
        for code in ("", None):
            with self.subTest(code=code):
                self.assertEqual(usage.refusal_disposition(code), ("CONTRACT_VIOLATION", ""))
        # An unrecognised code is echoed: it is what the gateway puts in the
        # ContractViolation, and an empty string there would hide which code it was.
        self.assertEqual(usage.refusal_disposition("NOT_A_CODE"),
                         ("CONTRACT_VIOLATION", "NOT_A_CODE"))

    def test_reason_code_is_the_contracts_name_for_the_same_value(self):
        ledger = self.ledger()
        with self.assertRaises(usage.LedgerRefused) as ctx:
            ledger.set_cap("run", "devon/run-01", ceiling_calls=1, ceiling_micros=1,
                           ceiling_seconds=1, source="")   # no source -> refusal
        self.assertEqual(ctx.exception.reason_code, ctx.exception.code)
        self.assertNotEqual(ctx.exception.code, "")


class TestCeilingRefusalsUseTheContractsNames(RefusalCase):
    """The scope is the code; the dimension rides alongside it."""

    def test_a_run_ceiling_speaks_the_contracts_name(self):
        ledger = self.ledger(calls=1)
        ledger.reserve(request())
        with self.assertRaises(usage.LedgerRefused) as ctx:
            ledger.reserve(request(operation_id="op-2", args={"query": "b"}))
        self.assertEqual(ctx.exception.code, "BUDGET_RUN_CEILING")
        self.assertEqual(ctx.exception.dimension, "calls")
        self.assertEqual(usage.refusal_disposition(ctx.exception.code)[0], "BLOCKED")

    def test_a_provider_account_ceiling_speaks_its_own_name(self):
        ledger = self.ledger(calls=100)
        ledger.set_cap("provider_account", "devon/serper/serper-main", ceiling_calls=1,
                       source="refusal-test")
        ledger.reserve(request())
        with self.assertRaises(usage.LedgerRefused) as ctx:
            ledger.reserve(request(operation_id="op-2", args={"query": "b"}))
        self.assertEqual(ctx.exception.code, "BUDGET_PROVIDER_CEILING")

    def test_every_ceiling_scope_maps_to_a_distinct_contract_code(self):
        self.assertEqual(set(usage._CEILING_CODE.values()),
                         {"BUDGET_OPERATION_CEILING", "BUDGET_RUN_CEILING",
                          "BUDGET_PROVIDER_CEILING", "BUDGET_PROJECT_CEILING"})
        for code in usage._CEILING_CODE.values():
            self.assertIn(code, usage.CONTRACT_REASON_CODES)


if __name__ == "__main__":
    unittest.main()
