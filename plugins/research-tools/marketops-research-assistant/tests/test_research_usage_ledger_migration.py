"""Offline tests for the legacy-ledger migration.

The fixtures are the two legacy schemas as they were actually written, built in a
temporary directory outside every Git checkout. Each test re-hashes the legacy
files afterwards: a migration that is only *intended* to be non-destructive is not
evidence that it is.
"""
from __future__ import annotations

import json
import sqlite3
import sys
import tempfile
import unittest
from hashlib import sha256
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import research_grants as grants
import research_usage_ledger as usage
import research_usage_ledger_migration as migration

DUAL_ROWS = (
    ("run-01", "op-01", "fingerprint-a", "serper", "2026-10-15T10:00:00+00:00", "reserved"),
    ("run-01", "op-02", "fingerprint-b", "serpapi", "2026-10-15T10:05:00+00:00", "reserved"),
    ("run-02", "op-03", "fingerprint-c", "serper", "2026-10-16T08:00:00+00:00", "reserved"),
)
# The shape before host_key/outcome_code were added: the reader must tolerate drift.
RESERVATION_ROWS = (
    ("run-01", "op-10", "tinyfish_mcp_agent", "sig-10", "2026-10-15T11:00:00+00:00"),
    ("run-01", "op-11", "crawl4ai_local", "sig-11", "2026-10-15T11:30:00+00:00"),
)


def build_legacy(root: Path, *, extra_unmappable: bool = False, missing_table: bool = False
                 ) -> None:
    state = root / "state"
    state.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(state / "dual-search-ledger.sqlite3") as db:
        if not missing_table:
            db.execute(
                "CREATE TABLE search_attempts (run_id TEXT NOT NULL, operation_id TEXT NOT NULL,"
                " fingerprint TEXT NOT NULL, provider TEXT NOT NULL, reserved_at TEXT NOT NULL,"
                " state TEXT NOT NULL, PRIMARY KEY (run_id, operation_id))")
            db.executemany("INSERT INTO search_attempts VALUES (?,?,?,?,?,?)", DUAL_ROWS)
        else:
            db.execute("CREATE TABLE something_else (x TEXT)")
        if extra_unmappable:
            # A row whose identifiers cannot be represented in the ledger.
            db.execute("INSERT INTO search_attempts VALUES (?,?,?,?,?,?)",
                       ("run with spaces", "op-99", "fingerprint-d", "serper",
                        "2026-10-16T09:00:00+00:00", "reserved"))
    with sqlite3.connect(state / "provider-reservations.sqlite3") as db:
        db.execute(
            "CREATE TABLE provider_reservations (run_id TEXT NOT NULL, operation_id TEXT NOT NULL,"
            " action TEXT NOT NULL, signature TEXT NOT NULL, created_at TEXT NOT NULL,"
            " PRIMARY KEY (run_id, operation_id))")
        db.executemany("INSERT INTO provider_reservations VALUES (?,?,?,?,?)", RESERVATION_ROWS)


def digest(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()


def registry() -> grants.CapabilityRegistry:
    return grants.CapabilityRegistry((
        grants.Capability("search", "serper", grants.Tier.T2, "search_query", "read_only"),
    ))


def pricing() -> usage.PricingCatalog:
    return usage.PricingCatalog((
        usage.Price(provider="serper", capability="search", unit_micros=750,
                    quota_unit="credits", quota_per_call=1, source="documented_rate",
                    version="2026-10-17", observed_at="2026-10-17T00:00:00+00:00"),
    ))


class MigrationCase(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "customer-data"
        self.ledger_path = Path(self.temp.name) / "ledger" / "usage-ledger.sqlite3"

    def legacy_sources(self):
        return [self.root / store.relative_path for store in migration.LEGACY_STORES
                if (self.root / store.relative_path).is_file()]

    def snapshot(self):
        return {path.name: (digest(path), path.stat().st_size, path.stat().st_mtime_ns)
                for path in self.legacy_sources()}

    def sidecars(self):
        """Anything SQLite left beside a legacy file. There must never be any.

        The earlier version of this fixture compared only the main files, so a survey
        that created ``-wal`` and ``-shm`` beside every store passed it - which is how
        an "opened read-only" plan modified the thing it promised not to touch.
        """
        found = []
        for path in self.legacy_sources():
            found.extend(item.name for item in path.parent.glob(path.name + "-*"))
        return sorted(found)

    def assert_sources_untouched(self, before):
        after = self.snapshot()
        self.assertEqual(before, after, "a legacy database changed during migration")
        self.assertEqual(self.sidecars(), [], "a legacy database grew a journal sidecar")


class TestPlan(MigrationCase):
    def test_a_plan_reads_both_stores_and_writes_nothing(self):
        build_legacy(self.root)
        before = self.snapshot()
        report = migration.plan(self.root, ledger_path=self.ledger_path)
        self.assertEqual(report["action"], "plan")
        self.assertEqual(report["totals"]["stores_present"], 2)
        self.assertEqual(report["totals"]["source_rows"], 5)
        self.assertEqual(report["totals"]["mappable_rows"], 5)
        self.assertEqual(report["totals"]["unmappable_rows"], 0)
        dual = report["stores"][0]
        self.assertEqual(dual["rows"], len(DUAL_ROWS))
        self.assertEqual(dual["by_run"], {"run-01": 2, "run-02": 1})
        self.assertEqual(len(dual["sha256"]), 64)
        self.assertEqual(len(dual["content_sha256"]), 64)
        self.assertFalse(self.ledger_path.exists())
        self.assert_sources_untouched(before)

    def test_column_drift_is_reported_and_the_readable_rows_still_plan(self):
        build_legacy(self.root)
        report = migration.plan(self.root)
        reservations = report["stores"][1]
        self.assertEqual(reservations["missing_columns"], ["host_key", "outcome_code"])
        self.assertEqual(reservations["rows"], len(RESERVATION_ROWS))
        self.assertEqual(reservations["unmappable"], [])

    def test_a_missing_store_and_a_missing_table_are_reported_not_invented(self):
        (self.root / "state").mkdir(parents=True)
        build_legacy(self.root, missing_table=True)
        report = migration.plan(self.root)
        self.assertEqual(report["stores"][0]["unmappable"][0]["reason"], "TABLE_MISSING")
        self.assertEqual(report["stores"][0].get("rows"), 0)
        empty = migration.plan(Path(self.temp.name) / "nothing-here")
        self.assertEqual(empty["totals"]["source_rows"], 0)
        self.assertFalse(empty["stores"][0]["exists"])


class TestDryRun(MigrationCase):
    def test_a_dry_run_reports_the_work_and_creates_no_ledger(self):
        build_legacy(self.root)
        before = self.snapshot()
        report = migration.migrate(self.root, self.ledger_path)
        self.assertEqual(report["action"], "dry_run")
        self.assertFalse(report["written"])
        self.assertEqual(report["would_import"], 5)
        self.assertFalse(self.ledger_path.exists())
        self.assert_sources_untouched(before)


class TestNothingIsTouched(MigrationCase):
    """The strongest promise this module makes, tested the way it was broken."""

    def build_wal_store(self) -> None:
        """A legacy store in WAL mode, closed cleanly, so it starts with no sidecars."""
        build_legacy(self.root)
        for name in ("dual-search-ledger.sqlite3", "provider-reservations.sqlite3"):
            with sqlite3.connect(self.root / "state" / name) as db:
                db.execute("PRAGMA journal_mode=WAL")
        self.assertEqual(self.sidecars(), [], "the fixture itself left a sidecar behind")

    def test_a_plan_does_not_create_a_journal_beside_a_store(self):
        # An earlier version opened the source with ``mode=ro``: opening a WAL database
        # read-only still creates ``-wal`` and ``-shm`` next to it, so a "read-only"
        # survey modified the directory it promised to leave alone.
        self.build_wal_store()
        before = self.snapshot()
        migration.plan(self.root, ledger_path=self.ledger_path)
        self.assert_sources_untouched(before)
        self.assertEqual(self.sidecars(), [])

    def test_a_migration_does_not_create_a_journal_beside_a_store(self):
        self.build_wal_store()
        before = self.snapshot()
        migration.migrate(self.root, self.ledger_path, dry_run=False)
        self.assert_sources_untouched(before)
        for path in self.legacy_sources():
            self.assertEqual(sorted(item.name for item in
                                    path.parent.glob(path.name + "-*")), [])

    def test_a_store_that_cannot_be_read_is_reported_not_raised(self):
        build_legacy(self.root)
        unreadable = self.root / "state" / "dual-search-ledger.sqlite3"
        self.addCleanup(unreadable.chmod, 0o600)
        unreadable.chmod(0o000)
        report = migration.plan(self.root, ledger_path=self.ledger_path)
        self.assertEqual(report["stores"][0]["unreadable"], "PermissionError")
        self.assertEqual(report["totals"]["stores_present"], 2)
        applied = migration.migrate(self.root, self.ledger_path, dry_run=False)
        self.assertEqual(applied["stores"][0]["skip_reason"], "SOURCE_UNREADABLE")
        self.assertEqual(applied["imported_rows"], len(RESERVATION_ROWS))
        self.assertTrue(applied["reconciliation"]["reconciled"])

    def test_a_legacy_outcome_body_is_not_copied_into_the_ledger(self):
        # A legacy outcome column can hold a provider error body - which is where a
        # key turns up. The ledger's outcome is a bounded code, so a value that is not
        # code-shaped is replaced rather than stored and later printed.
        build_legacy(self.root)
        with sqlite3.connect(self.root / "state" / "provider-reservations.sqlite3") as db:
            db.execute("ALTER TABLE provider_reservations ADD COLUMN outcome_code TEXT")
            db.execute("UPDATE provider_reservations SET outcome_code=? WHERE operation_id=?",
                       ('HTTP 401 {"error":"invalid key sk-live-DEADBEEF"}', "op-10"))
        report = migration.migrate(self.root, self.ledger_path, dry_run=False)
        self.assertTrue(report["written"])
        ledger = usage.UsageLedger(self.ledger_path)
        outcomes = {row.outcome for row in ledger.operations()}
        self.assertIn("LEGACY_UNKNOWN_OUTCOME", outcomes)
        self.assertNotIn("sk-live-DEADBEEF",
                         self.ledger_path.read_bytes().decode("utf-8", "ignore"))

    def test_an_imported_row_cannot_be_reconciled_without_evidence_either(self):
        build_legacy(self.root)
        migration.migrate(self.root, self.ledger_path, dry_run=False)
        ledger = usage.UsageLedger(self.ledger_path)
        row = ledger.operations(project="legacy-import")[0]
        with self.assertRaises(usage.LedgerRefused) as ctx:
            ledger.reconcile(row.operation_id, actual_micros=999_000,
                             actual_source=usage.ActualSource.PROVIDER_REPORTED)
        self.assertEqual(ctx.exception.code, "EVIDENCE_REQUIRED")
        self.assertEqual(ledger.report("legacy-import")["money"]["actual_micros"], 0)


class TestApply(MigrationCase):
    def test_every_mappable_row_arrives_and_the_report_reconciles(self):
        build_legacy(self.root)
        before = self.snapshot()
        report = migration.migrate(self.root, self.ledger_path, dry_run=False)
        self.assertTrue(report["written"])
        self.assertEqual(report["imported_rows"], 5)
        self.assertEqual(report["skipped_existing_rows"], 0)
        self.assertTrue(report["reconciliation"]["reconciled"])
        self.assertEqual(report["reconciliation"]["unaccounted_rows"], 0)
        self.assert_sources_untouched(before)
        for path in self.legacy_sources():
            sidecars = sorted(item.name for item in path.parent.glob(path.name + "-*"))
            self.assertEqual(sidecars, [], f"{path.name} grew a journal sidecar")

        ledger = usage.UsageLedger(self.ledger_path)
        rows = ledger.operations()
        self.assertEqual(len(rows), 5)
        for row in rows:
            self.assertEqual(row.origin, "legacy")
            self.assertEqual(row.charge_state, usage.ChargeState.UNKNOWN.value)
            self.assertEqual(row.tier, -1)  # imported history has no policy tier
            self.assertEqual(row.estimated_micros, 0)
            self.assertEqual(row.reserved_micros, 0)
            self.assertIsNone(row.actual_micros)
            self.assertEqual(row.actual_source, usage.ActualSource.NONE.value)
            self.assertFalse(row.counts_against_caps)
            self.assertTrue(row.legacy_ref.startswith("state/"))
        providers = {row.provider for row in rows}
        self.assertEqual(providers, {"serper", "serpapi", "tinyfish", "crawl4ai"})
        self.assertEqual({row.created_at for row in rows} - {""},
                         {"2026-10-15T10:00:00+00:00", "2026-10-15T10:05:00+00:00",
                          "2026-10-16T08:00:00+00:00", "2026-10-15T11:00:00+00:00",
                          "2026-10-15T11:30:00+00:00"})

    def test_running_the_migration_again_imports_nothing_new(self):
        build_legacy(self.root)
        migration.migrate(self.root, self.ledger_path, dry_run=False)
        again = migration.migrate(self.root, self.ledger_path, dry_run=False)
        self.assertEqual(again["imported_rows"], 0)
        self.assertEqual(again["skipped_existing_rows"], 5)
        self.assertEqual(again["ledger_rows_after"], again["ledger_rows_before"])
        self.assertTrue(again["reconciliation"]["reconciled"])

    def test_an_unmappable_row_is_reported_and_never_dropped_silently(self):
        build_legacy(self.root, extra_unmappable=True)
        report = migration.migrate(self.root, self.ledger_path, dry_run=False)
        dual = report["stores"][0]
        self.assertEqual(dual["rows"], 4)
        self.assertEqual(dual["mappable"], 3)
        self.assertEqual([item["reason"] for item in dual["unmappable"]],
                         ["IDENTIFIER_NOT_MAPPABLE"])
        self.assertEqual(report["totals"]["source_rows"], 6)
        self.assertEqual(report["totals"]["unmappable_rows"], 1)
        self.assertEqual(report["reconciliation"]["mappable_source_rows"], 5)
        self.assertTrue(report["reconciliation"]["reconciled"])
        ledger = usage.UsageLedger(self.ledger_path)
        self.assertEqual(len(ledger.operations()), 5)
        self.assertNotIn("run with spaces", {row.run_id for row in ledger.operations()})

    def test_a_missing_store_leaves_the_ledger_empty_rather_than_failing(self):
        report = migration.migrate(self.root, self.ledger_path, dry_run=False)
        self.assertEqual(report["imported_rows"], 0)
        self.assertEqual([store["skip_reason"] for store in report["stores"]],
                         ["SOURCE_MISSING", "SOURCE_MISSING"])
        self.assertTrue(report["reconciliation"]["reconciled"])
        self.assertTrue(self.ledger_path.exists())

    def test_a_table_that_is_missing_is_skipped_with_a_reason(self):
        (self.root / "state").mkdir(parents=True)
        build_legacy(self.root, missing_table=True)
        report = migration.migrate(self.root, self.ledger_path, dry_run=False)
        self.assertEqual(report["stores"][0]["skip_reason"], "TABLE_MISSING")
        self.assertEqual(report["imported_rows"], len(RESERVATION_ROWS))

    def test_imported_history_does_not_consume_todays_ceilings(self):
        build_legacy(self.root)
        # Same project and run as the ledger's own ceilings, so the scopes overlap and
        # the test can tell the difference the flag makes. The earlier version put the
        # import in another project, where the predicates could never match: it passed
        # with the opt-in flag on as well, so it could not have detected the behaviour
        # it named.
        migration.migrate(self.root, self.ledger_path, dry_run=False, project="devon")
        ledger = usage.UsageLedger(self.ledger_path, pricing=pricing(), registry=registry())
        ledger.set_cap("run", "devon/run-01", ceiling_micros=2_000_000, ceiling_calls=1,
                       ceiling_seconds=600, source="test-operator")
        fresh = ledger.reserve(usage.OperationRequest(
            project="devon", run_id="run-01", operation_id="new-01", provider="serper",
            capability="search", account_ref="serper-main", transport="rest_https",
            intent="a new question", args={"query": "a new question"}))
        self.assertEqual(fresh.charge_state, usage.ChargeState.RESERVED)
        self.assertEqual(ledger.remaining("run", "devon/run-01").calls_used, 1)
        imported = ledger.report("devon")
        self.assertEqual(imported["operations"], 6)  # five of history plus this one
        self.assertEqual(imported["legacy_operations"], 5)
        self.assertEqual(imported["money"]["actual_micros"], 0)
        self.assertEqual(imported["money"]["unknown_exposure_micros"], 0)
        self.assertEqual(imported["quota_units"], {"credits": 1})  # history counts nothing
        self.assertEqual(imported["by_charge_state"][usage.ChargeState.UNKNOWN.value], 5)

    def test_the_opt_in_really_does_put_imported_rows_against_the_ceiling(self):
        build_legacy(self.root)
        migration.migrate(self.root, self.ledger_path, dry_run=False, project="devon",
                          counts_against_caps=True)
        ledger = usage.UsageLedger(self.ledger_path, pricing=pricing(), registry=registry())
        ledger.set_cap("run", "devon/run-01", ceiling_micros=2_000_000, ceiling_calls=1,
                       ceiling_seconds=600, source="test-operator")
        # Five legacy attempts in this run are already counted, so the ceiling of one
        # is gone. That is the reading the flag asks for; without the flag the same
        # reservation succeeds (asserted in the test above).
        with self.assertRaises(usage.LedgerRefused) as ctx:
            ledger.reserve(usage.OperationRequest(
                project="devon", run_id="run-01", operation_id="new-01", provider="serper",
                capability="search", account_ref="serper-main", transport="rest_https",
                intent="a new question", args={"query": "a new question"}))
        self.assertEqual(ctx.exception.code, "BUDGET_RUN_CEILING")
        self.assertEqual(ctx.exception.dimension, "calls")
        # Two attempts from each store belong to this run; the third dual-search row
        # is in run-02 and does not count here.
        self.assertEqual(ledger.remaining("run", "devon/run-01").calls_used, 4)

    def test_imported_rows_can_be_read_as_open_and_are_identified_as_history(self):
        build_legacy(self.root)
        migration.migrate(self.root, self.ledger_path, dry_run=False)
        ledger = usage.UsageLedger(self.ledger_path)
        # A legacy attempt is unresolved history, so it shows up as open - which is
        # honest - but never as a live hold, because it carries no money.
        self.assertEqual(len(ledger.open_operations(project="legacy-import")), 5)
        recovery = ledger.recover(project="legacy-import")
        self.assertEqual(recovery["unknown_exposure_micros"], 0)
        self.assertEqual(recovery["action_taken"], "REPORT_ONLY")

    def test_imported_rows_keep_their_legacy_reference_for_later_reading(self):
        build_legacy(self.root)
        migration.migrate(self.root, self.ledger_path, dry_run=False)
        ledger = usage.UsageLedger(self.ledger_path)
        refs = sorted(row.legacy_ref for row in ledger.operations())
        self.assertEqual(refs[0],
                         "state/dual-search-ledger.sqlite3:search_attempts:run-01:op-01")
        self.assertEqual(len(set(refs)), len(refs))
        events = ledger.events()
        self.assertEqual({event["kind"] for event in events}, {"import_legacy"})

    def test_a_ledger_inside_a_git_checkout_is_refused(self):
        build_legacy(self.root)
        with self.assertRaises(migration.MigrationRefused) as ctx:
            migration.migrate(self.root, ROOT / "state" / "usage-ledger.sqlite3", dry_run=False)
        self.assertEqual(ctx.exception.code, "LEDGER_INSIDE_CHECKOUT")

    def test_the_cli_plans_without_writing_and_applies_only_with_the_flag(self):
        build_legacy(self.root)
        import io
        import os
        from contextlib import redirect_stdout
        from unittest.mock import patch
        buffer = io.StringIO()
        with patch.dict(os.environ, {}, clear=False):
            with redirect_stdout(buffer):
                code = migration.main(["plan", "--root", str(self.root)])
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(buffer.getvalue())["totals"]["source_rows"], 5)
        self.assertFalse(self.ledger_path.exists())

        buffer = io.StringIO()
        with redirect_stdout(buffer):
            code = migration.main(["migrate", "--root", str(self.root),
                                   "--ledger", str(self.ledger_path)])
        self.assertEqual(code, 0)
        self.assertFalse(self.ledger_path.exists(), "migrate without --apply must write nothing")

        buffer = io.StringIO()
        with redirect_stdout(buffer):
            code = migration.main(["migrate", "--root", str(self.root),
                                   "--ledger", str(self.ledger_path), "--apply"])
        self.assertEqual(code, 0)
        self.assertTrue(self.ledger_path.exists())
        self.assertEqual(json.loads(buffer.getvalue())["imported_rows"], 5)

    def test_migrating_without_a_ledger_path_is_refused(self):
        build_legacy(self.root)
        import io
        from contextlib import redirect_stdout
        buffer = io.StringIO()
        with redirect_stdout(buffer):
            code = migration.main(["migrate", "--root", str(self.root)])
        self.assertEqual(code, 2)
        self.assertIn("LEDGER_PATH_REQUIRED", buffer.getvalue())


if __name__ == "__main__":
    unittest.main()
