"""Wave-2 audit of the ledger as integrated at PR #78 @ ``cf8ff6e``.

Five areas were named for audit: concurrency, durable reservations, UNKNOWN charges,
migration, and the campaign floor. This file holds the probes that correspond to
**defects**, so each test here is written against the behaviour the contract requires
and fails while the defect is present. It is not a regression suite for behaviour that
already worked - ``test_billing_usage_ledger.py`` and ``test_billing_ledger_floor.py``
keep that.

Provenance, so a later reader can tell what was audited: the integration's
``research_usage_ledger.py`` is W-B's module plus ~112 lines added at integration time
(a cross-transport "route" refusal, ``project_for_root``, ``documented_pricing``,
``for_root``). These tests are written against **that** file, and the fixes land in the
same file on W-B's branch.

Everything offline: private temporary databases, no network, no credential.
"""
from __future__ import annotations

import sqlite3
import subprocess
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


def registry() -> grants.CapabilityRegistry:
    return grants.CapabilityRegistry((
        grants.Capability("search", "serper", grants.Tier.T2, "search_query", "read_only"),
    ))


def credit_only_pricing() -> usage.PricingCatalog:
    """What the integration ships by default: credits, no verified dollar rate."""
    return usage.documented_pricing()


def request(**overrides) -> usage.OperationRequest:
    base = dict(project="devon", run_id="run-01", operation_id="op-01",
                provider="serper", capability="search", account_ref="serper-main",
                transport="rest_https", intent="find the venue",
                args={"query": "devon venue"})
    base.update(overrides)
    return usage.OperationRequest(**base)


def credit_budget(decimal_units: int, *, account_ref="serper-main") -> usage.CampaignBudget:
    """A campaign floor over a credit meter, which is what Serper bills in."""
    return usage.CampaignBudget(usage.CampaignFloor(usage.CampaignBaseline(
        provider="serper", account_ref=account_ref, meter="credits", units=decimal_units,
        observed_at=NOW.isoformat(),
        source=usage.MeterReadingSource.VENDOR_ACCOUNT_READ, campaign_id="wave2-audit")))


class AuditCase(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "state" / "usage-ledger.sqlite3"
        self.clock = lambda: NOW

    def ledger(self, *, pricing=None, budgets=None) -> usage.UsageLedger:
        return usage.UsageLedger(self.path, pricing=pricing or credit_only_pricing(),
                                 registry=registry(), clock=self.clock,
                                 campaign_budgets=budgets)

    def caps(self, ledger, *, run="devon/run-01", calls=1000, money=0) -> None:
        ledger.set_cap("run", run, ceiling_micros=money, ceiling_calls=calls,
                       ceiling_seconds=36_000, source="audit")


class TestTheCampaignFloorIsReachableForCreditMeteredWork(AuditCase):
    """The floor must bite where the integration actually runs.

    The shipped default catalog prices every capability at ``unit_micros=0`` with a
    real quota unit, because no verified dollar rate exists for this checkout. A gate
    that only recognises "potentially charged" by a non-zero *dollar* estimate is
    therefore unreachable in the default deployment: the campaign floor would never be
    consulted, and the owner's §1.6 pre-operation rule would be satisfied on paper only.
    """

    def test_a_credit_metered_operation_is_a_potentially_charged_operation(self):
        ledger = self.ledger(budgets={("serper", "serper-main"): credit_budget(4)})
        self.caps(ledger)
        for index in range(3):
            ledger.reserve(request(operation_id=f"op-{index}", args={"query": f"venue {index}"}))
        with self.assertRaises(usage.LedgerRefused) as ctx:
            ledger.reserve(request(operation_id="op-4", args={"query": "venue 4"}))
        self.assertEqual(ctx.exception.code, "CAMPAIGN_FLOOR_WOULD_BREACH")

    def test_the_floor_is_read_from_the_accounts_own_credit_totals(self):
        ledger = self.ledger(budgets={("serper", "serper-main"): credit_budget(4)})
        self.caps(ledger)
        ledger.reserve(request())
        self.assertEqual(ledger.account_totals("serper", "serper-main").quota_units, 1)

    def test_a_zero_dollar_estimate_still_carries_its_credit(self):
        ledger = self.ledger()
        self.caps(ledger)
        op = ledger.reserve(request())
        self.assertEqual(op.estimated_micros, 0)
        self.assertEqual(op.quota_units, 1)
        self.assertEqual(op.quota_unit, "credits")

    def test_the_floor_survives_a_process_restart(self):
        """Across sessions: the floor is in the rows, not in this object's memory."""
        ledger = self.ledger(budgets={("serper", "serper-main"): credit_budget(4)})
        self.caps(ledger, calls=10_000)
        for index in range(3):
            ledger.reserve(request(operation_id=f"op-{index}", args={"query": f"q{index}"}))
        reopened = usage.UsageLedger(self.path, pricing=credit_only_pricing(),
                                     registry=registry(), clock=self.clock,
                                     campaign_budgets={("serper", "serper-main"):
                                                       credit_budget(4)})
        self.caps(reopened, calls=10_000)
        with self.assertRaises(usage.LedgerRefused) as ctx:
            reopened.reserve(request(operation_id="op-4", args={"query": "q4"}))
        self.assertEqual(ctx.exception.code, "CAMPAIGN_FLOOR_WOULD_BREACH")
        self.assertEqual(reopened.account_totals("serper", "serper-main").quota_units, 3)

    def test_the_account_wide_totals_are_used_not_the_runs(self):
        ledger = self.ledger(budgets={("serper", "serper-main"): credit_budget(3)})
        self.caps(ledger)
        self.caps(ledger, run="devon/run-02")
        ledger.reserve(request())
        ledger.reserve(request(run_id="run-02", operation_id="op-2", args={"query": "b"}))
        with self.assertRaises(usage.LedgerRefused) as ctx:
            ledger.reserve(request(run_id="run-02", operation_id="op-3", args={"query": "c"}))
        self.assertEqual(ctx.exception.code, "CAMPAIGN_FLOOR_WOULD_BREACH")


class TestTheProductEntryPointActuallyEnforcesAFloor(AuditCase):
    """``for_root`` is what call sites use; the floor has to be reachable through it."""

    def test_for_root_can_be_given_a_campaign_floor(self):
        ledger = usage.for_root(Path(self.temp.name), pricing=credit_only_pricing(),
                                registry=registry(), clock=self.clock,
                                campaign_budgets={("serper", "serper-main"):
                                                  credit_budget(4)})
        self.caps(ledger)
        for index in range(3):
            ledger.reserve(request(operation_id=f"op-{index}", args={"query": f"venue {index}"}))
        with self.assertRaises(usage.LedgerRefused) as ctx:
            ledger.reserve(request(operation_id="op-4", args={"query": "venue 4"}))
        self.assertEqual(ctx.exception.code, "CAMPAIGN_FLOOR_WOULD_BREACH")

    def test_for_root_reports_whether_a_campaign_floor_is_wired(self):
        # A product path that silently runs without a floor is the thing §1.6 forbids,
        # so the entry point must be able to say which state it is in rather than
        # leaving it to be inferred from an empty mapping.
        ledger = usage.for_root(Path(self.temp.name), pricing=credit_only_pricing(),
                                registry=registry(), clock=self.clock)
        self.assertEqual(ledger.campaign_status().verdict, usage.BudgetVerdict.NOT_CAPTURED)


class TestTheTransportRouteRefusalIsScopedToTheOperation(AuditCase):
    """A route refusal must refuse *the same* operation, not every operation.

    The integration added a refusal so that a second transport reaching one vendor
    account is treated as a route change rather than as a second budget. As written it
    compares only (project, provider) and ignores the operation itself, so after one
    Serper REST search **no** Serper search over the embedded MCP can ever run in that
    project - including a completely different query, and including the migration the
    integration is trying to make (REST disabled, MCP the path).
    """

    def test_a_different_query_on_another_transport_is_a_different_operation(self):
        ledger = self.ledger()
        self.caps(ledger, calls=100)
        self.caps(ledger, run="devon/run-02")
        ledger.reserve(request(operation_id="rest-1", transport="rest_https",
                               args={"query": "first question"}))
        second = ledger.reserve(request(operation_id="mcp-1", run_id="run-02",
                                       transport="embedded_official_mcp",
                                       intent="a different question",
                                       args={"query": "second question"}))
        self.assertEqual(second.charge_state, usage.ChargeState.RESERVED)

    def test_the_same_operation_over_a_second_transport_is_still_refused(self):
        ledger = self.ledger()
        self.caps(ledger, calls=100)
        ledger.reserve(request(operation_id="rest-1", transport="rest_https"))
        with self.assertRaises(usage.LedgerRefused) as ctx:
            ledger.reserve(request(operation_id="mcp-1", transport="embedded_official_mcp"))
        self.assertEqual(ctx.exception.code, "DUPLICATE_OPERATION_SIGNATURE")

    def test_the_route_refusal_does_not_fire_across_projects(self):
        ledger = self.ledger()
        self.caps(ledger, calls=100)
        self.caps(ledger, run="other/run-01")
        ledger.reserve(request(operation_id="rest-1", transport="rest_https"))
        second = ledger.reserve(request(project="other", operation_id="mcp-1",
                                       transport="embedded_official_mcp"))
        self.assertEqual(second.charge_state, usage.ChargeState.RESERVED)

    def test_a_refused_route_leaves_no_row_behind(self):
        ledger = self.ledger()
        self.caps(ledger, calls=100)
        ledger.reserve(request(operation_id="rest-1", transport="rest_https"))
        with self.assertRaises(usage.LedgerRefused):
            ledger.reserve(request(operation_id="mcp-1", transport="embedded_official_mcp"))
        self.assertEqual([row.operation_id for row in ledger.operations()], ["rest-1"])


class TestDurableReservationsAndUnknownSurviveTheIntegration(AuditCase):
    """Re-asserted on the integrated file, because the integration rewired callers."""

    def test_a_reservation_is_held_until_it_is_settled(self):
        ledger = self.ledger()
        self.caps(ledger)
        op = ledger.reserve(request())
        reopened = usage.UsageLedger(self.path, pricing=credit_only_pricing(),
                                     registry=registry(), clock=self.clock)
        self.assertEqual(reopened.get(op.operation_id).charge_state,
                         usage.ChargeState.RESERVED.value)
        self.assertEqual(reopened.remaining("run", "devon/run-01").calls_used, 1)

    def test_an_unknown_charge_keeps_its_hold_and_its_credit(self):
        ledger = self.ledger()
        self.caps(ledger, calls=1)
        op = ledger.reserve(request())
        ledger.mark_dispatch_intent(op.operation_id)
        ledger.mark_unknown(op.operation_id, reason="PROVIDER_TIMEOUT")
        reopened = usage.UsageLedger(self.path, pricing=credit_only_pricing(),
                                     registry=registry(), clock=self.clock)
        self.assertEqual(reopened.account_totals("serper", "serper-main").unknown_micros, 0)
        self.assertEqual(reopened.account_totals("serper", "serper-main").quota_units, 1)
        self.assertEqual(reopened.remaining("run", "devon/run-01").calls_used, 1)
        with self.assertRaises(usage.LedgerRefused) as ctx:
            reopened.reserve(request(operation_id="op-2", args={"query": "next"}))
        self.assertEqual(ctx.exception.code, "BUDGET_RUN_CEILING")
        self.assertEqual(ctx.exception.dimension, "calls")


class TestADatabaseWrittenBeforeTheUpgradeStillWorks(AuditCase):
    """An integration deployment already has a ledger file; a new column must not brick it."""

    def _legacy_schema(self) -> None:
        """The operations table as the pre-audit module created it: no route_shape."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        import sqlite3
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
            db.execute("UPDATE operations SET outcome=outcome")

    def test_an_older_database_gains_the_column_and_keeps_reserving(self):
        self._legacy_schema()
        ledger = self.ledger()
        self.caps(ledger)
        op = ledger.reserve(request())
        self.assertEqual(op.charge_state, usage.ChargeState.RESERVED)
        self.assertTrue(op.route_shape)

    def test_a_row_that_predates_the_column_matches_no_route(self):
        # An empty shape is the conservative reading: "we did not record what this
        # operation was", so it can neither block nor be blocked as a duplicate route.
        self._legacy_schema()
        import sqlite3
        with sqlite3.connect(self.path) as db:
            db.execute("INSERT INTO operations (operation_id, project, run_id,"
                       " intent_fingerprint, op_signature, provider, account_ref, capability,"
                       " meter, transport, tier, estimated_micros, reserved_micros,"
                       " actual_micros, actual_source, charge_state, quota_unit, quota_units,"
                       " estimated_seconds, outcome, upstream_ref, grant_ref, escalates_from,"
                       " price_source, price_version, price_observed_at, price_verified,"
                       " origin, counts_against_caps, created_at) VALUES"
                       " ('old-1','devon','run-00','i','s','serper','serper-main','search',"
                       " 'search_query','rest_https',2,0,0,NULL,'NONE','RESERVED','credits',1,"
                       " 0,'','','','','legacy','n/a','2026-10-17T00:00:00+00:00',0,'native',1,"
                       " '2026-10-17T00:00:00+00:00')")
        ledger = self.ledger()
        self.caps(ledger)
        # The pre-upgrade row is in another run and carries no shape, so it must not
        # be read as a route this project has already taken.
        op = ledger.reserve(request(transport="embedded_official_mcp"))
        self.assertEqual(op.charge_state, usage.ChargeState.RESERVED)


class TestInitialisationIsBoundedInWallClock(AuditCase):
    """QA round 7: initialisation held an exclusive lock for more than 200 seconds.

    The budget used to be twelve *attempts*, and each attempt carried the driver's own
    20 s busy timeout - so the real bound was about four minutes, and the observed
    behaviour was "still waiting when the reviewer gave up". An attempt count is not a
    bound when the thing being counted is itself unbounded. These tests measure the
    wall clock, not the attempt count.
    """

    def _fresh_non_wal(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        db = sqlite3.connect(self.path, isolation_level=None)
        db.execute("PRAGMA journal_mode=delete")
        db.execute("CREATE TABLE IF NOT EXISTS seed (x TEXT)")
        db.commit()
        return db

    def test_a_held_exclusive_lock_is_refused_within_the_budget(self):
        holder = self._fresh_non_wal()
        self.addCleanup(holder.close)
        holder.execute("BEGIN EXCLUSIVE")
        started = time.monotonic()
        try:
            with self.assertRaises(usage.LedgerRefused) as ctx:
                usage.UsageLedger(self.path, pricing=credit_only_pricing())
            elapsed = time.monotonic() - started
        except sqlite3.OperationalError as exc:
            self.fail(f"a raw driver error escaped instead of a governed refusal: {exc}")
        finally:
            holder.execute("ROLLBACK")
        self.assertEqual(ctx.exception.code, "LEDGER_INIT_BUSY")
        self.assertEqual(ctx.exception.reason_code, "LEDGER_INIT_BUSY")
        # The default budget is 3 s; ten seconds is a generous ceiling that still
        # fails on the old behaviour by two orders of magnitude.
        self.assertLess(elapsed, 10, f"took {elapsed:.1f}s; the budget is not a deadline")

    def test_a_shorter_budget_is_honoured_too(self):
        holder = self._fresh_non_wal()
        self.addCleanup(holder.close)
        holder.execute("BEGIN EXCLUSIVE")
        started = time.monotonic()
        try:
            with self.assertRaises(usage.LedgerRefused):
                usage.UsageLedger(self.path, pricing=credit_only_pricing(),
                                  init_budget_seconds=0.4)
            elapsed = time.monotonic() - started
        finally:
            holder.execute("ROLLBACK")
        self.assertLess(elapsed, 3, f"took {elapsed:.1f}s for a 0.4s budget")

    def test_the_budget_is_validated(self):
        for bad in (0, -1, 0.0, True, "3", 601):
            with self.subTest(bad=bad):
                with self.assertRaises(usage.LedgerRefused) as ctx:
                    usage.UsageLedger(self.path, pricing=credit_only_pricing(),
                                      init_budget_seconds=bad)
                self.assertEqual(ctx.exception.code, "INVALID_REQUEST")

    def test_every_concurrent_constructor_is_refused_within_the_budget(self):
        """The multi-process case, with the lock held for the whole window.

        No reliance on a race: the parent holds ``BEGIN EXCLUSIVE`` before spawning, so
        every child must hit it. What is asserted is the wall clock *and* that each
        child reports a governed code rather than the driver's exception.
        """
        holder = self._fresh_non_wal()
        self.addCleanup(holder.close)
        holder.execute("BEGIN EXCLUSIVE")
        script = (
            "import sys, time\n"
            f"sys.path.insert(0, {str(ROOT / 'scripts')!r})\n"
            "import research_usage_ledger as usage\n"
            "started = time.monotonic()\n"
            "try:\n"
            f"    usage.UsageLedger({str(self.path)!r},"
            " pricing=usage.documented_pricing())\n"
            "    print(f'CONSTRUCTED {time.monotonic()-started:.2f}')\n"
            "except usage.LedgerRefused as exc:\n"
            "    print(f'REFUSED {exc.code} {time.monotonic()-started:.2f}')\n"
            "except Exception as exc:\n"
            "    print(f'RAW {type(exc).__name__} {time.monotonic()-started:.2f}')\n"
        )
        started = time.monotonic()
        procs = [subprocess.Popen([sys.executable, "-c", script],
                                  stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
                 for _ in range(6)]
        outputs = []
        for proc in procs:
            out, err = proc.communicate(timeout=120)
            outputs.append((out or err).strip())
        total = time.monotonic() - started
        holder.execute("ROLLBACK")
        for output in outputs:
            with self.subTest(output=output):
                self.assertTrue(output.startswith("REFUSED LEDGER_INIT_BUSY"), outputs)
        self.assertLess(total, 20, f"six concurrent constructors took {total:.1f}s")
        users = [float(item.split()[-1]) for item in outputs]
        self.assertLess(max(users), 10, f"a child waited {max(users):.1f}s")

    def test_a_healthy_ledger_still_opens_quickly_and_keeps_its_unknown_rows(self):
        # The bound must not have been bought by making the ordinary path slow, and an
        # UNKNOWN charge must survive a reopen untouched - the whole point of the hold.
        ledger = usage.UsageLedger(self.path, pricing=credit_only_pricing(),
                                   registry=registry(), clock=self.clock)
        ledger.set_cap("run", "devon/run-01", ceiling_micros=0, ceiling_calls=10,
                       ceiling_seconds=3_600, source="audit")
        op = ledger.reserve(request())
        ledger.mark_dispatch_intent(op.operation_id)
        ledger.mark_unknown(op.operation_id, reason="PROVIDER_TIMEOUT")
        started = time.monotonic()
        reopened = usage.UsageLedger(self.path, pricing=credit_only_pricing())
        elapsed = time.monotonic() - started
        self.assertLess(elapsed, 2, f"reopening a healthy ledger took {elapsed:.2f}s")
        row = reopened.get(op.operation_id)
        self.assertEqual(row.charge_state, usage.ChargeState.UNKNOWN.value)
        self.assertEqual(row.held_micros, 0)      # credit-priced: the credit, not money
        self.assertEqual(reopened.account_totals("serper", "serper-main").quota_units, 1)


class TestSchemaInitialisationSurvivesOtherProcesses(AuditCase):
    """Wave-3 / QA round 3: ``PRAGMA journal_mode=WAL`` under concurrency.

    On a fresh file the mode change wants exclusive access and does not reliably consult
    the busy handler, so several processes starting at once can each get
    ``sqlite3.OperationalError: database is locked``. Measured on the integration module:
    2 of 12 concurrent constructors on one fresh database, and 1 of 24 in two of four
    rounds - intermittent, which is why the committed gate below is deterministic (hold
    the lock, then spawn) rather than a race that may or may not fire.
    """

    def _fresh_non_wal(self, path: Path):
        import sqlite3
        path.parent.mkdir(parents=True, exist_ok=True)
        db = sqlite3.connect(path, isolation_level=None)
        db.execute("PRAGMA journal_mode=delete")
        db.execute("CREATE TABLE IF NOT EXISTS seed (x TEXT)")
        db.commit()
        return db

    def test_a_locked_database_refuses_with_a_code_not_a_raw_sqlite_error(self):
        import sqlite3
        import time
        holder = self._fresh_non_wal(self.path)
        self.addCleanup(holder.close)
        holder.execute("BEGIN IMMEDIATE")
        started = time.monotonic()
        try:
            with self.assertRaises(usage.LedgerRefused) as ctx:
                usage.UsageLedger(self.path, pricing=credit_only_pricing())
            self.assertEqual(ctx.exception.code, "LEDGER_INIT_BUSY")
            # Bounded, never a spin: twelve attempts with short pauses.
            self.assertLess(time.monotonic() - started, 15)
        except sqlite3.OperationalError as exc:      # what the integration module raises
            self.fail(f"a raw sqlite3 error escaped instead of a ledger refusal: {exc}")

    def test_construction_succeeds_as_soon_as_the_lock_is_released(self):
        import sqlite3
        holder = self._fresh_non_wal(self.path)
        holder.execute("BEGIN IMMEDIATE")
        holder.execute("ROLLBACK")
        holder.close()
        ledger = usage.UsageLedger(self.path, pricing=credit_only_pricing())
        self.assertEqual(ledger.campaign_status().verdict, usage.BudgetVerdict.NOT_CAPTURED)
        with sqlite3.connect(self.path) as db:
            self.assertEqual(str(db.execute("PRAGMA journal_mode").fetchone()[0]).lower(), "wal")

    def test_a_wal_file_is_opened_without_taking_an_exclusive_lock(self):
        # The common case: every start after the first. Asking for WAL on a file that
        # already has it is a read, so a writer holding the file does not block opens.
        import sqlite3
        usage.UsageLedger(self.path, pricing=credit_only_pricing())
        holder = sqlite3.connect(self.path, isolation_level=None)
        self.addCleanup(holder.close)
        holder.execute("BEGIN IMMEDIATE")
        usage.UsageLedger(self.path, pricing=credit_only_pricing())

    def test_concurrent_construction_on_an_older_schema_all_succeed(self):
        """The same check-then-ALTER race, on the column this ledger added itself.

        Twelve processes meeting a database that lacks ``route_shape``: with a plain
        check-then-ALTER, whoever loses reads a missing column and then gets
        ``duplicate column name`` from the ALTER. The column existing is what was
        wanted, so that outcome must not be reported as an initialisation failure.

        Scope, stated so it is not miscounted: this guards the upgrade path of the
        column added in this branch's Wave-2 change. Against a build that has no such
        column the path does not exist, so the test passes there for want of anything
        to exercise - it is a guard on this branch's own code, not evidence of a defect
        in the integration candidate.
        """
        import subprocess
        self._fresh_non_wal(self.path)
        with sqlite3.connect(self.path) as db:
            db.execute("PRAGMA journal_mode=WAL")
        script = (
            "import sys\n"
            f"sys.path.insert(0, {str(ROOT / 'scripts')!r})\n"
            "import research_usage_ledger as usage\n"
            "try:\n"
            f"    usage.UsageLedger({str(self.path)!r},"
            " pricing=usage.documented_pricing())\n"
            "    print('OK')\n"
            "except Exception as exc:\n"
            "    print('FAILED ' + type(exc).__name__ + ': ' + str(exc))\n"
        )
        for round_index in range(3):
            procs = [subprocess.Popen([sys.executable, "-c", script],
                                      stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
                     for _ in range(12)]
            outputs = []
            for proc in procs:
                out, err = proc.communicate(timeout=120)
                outputs.append((out or err).strip())
            with self.subTest(round=round_index):
                self.assertEqual([item for item in outputs if item != "OK"], [], outputs)
            with sqlite3.connect(self.path) as db:
                columns = {row[1] for row in db.execute("PRAGMA table_info(operations)")}
                if "route_shape" in columns:
                    db.execute("ALTER TABLE operations DROP COLUMN route_shape")

    def test_every_concurrent_constructor_fails_closed_while_the_lock_is_held(self):
        """A real multi-process probe with a deterministic outcome.

        The lock is held for the whole window, so every child hits it - no reliance on
        a race firing. The requirement is not "it worked" but "it refused with a code":
        a raw sqlite3 error escaping to a caller is what makes an init failure look like
        a crash rather than a decision.
        """
        import subprocess
        holder = self._fresh_non_wal(self.path)
        self.addCleanup(holder.close)
        holder.execute("BEGIN IMMEDIATE")
        script = (
            "import sys\n"
            f"sys.path.insert(0, {str(ROOT / 'scripts')!r})\n"
            "import research_usage_ledger as usage\n"
            "try:\n"
            f"    usage.UsageLedger({str(self.path)!r},"
            " pricing=usage.documented_pricing())\n"
            "    print('CONSTRUCTED')\n"
            "except usage.LedgerRefused as exc:\n"
            "    print('REFUSED ' + exc.code)\n"
            "except Exception as exc:\n"
            "    print('RAW ' + type(exc).__name__ + ': ' + str(exc))\n"
        )
        procs = [subprocess.Popen([sys.executable, "-c", script],
                                  stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
                 for _ in range(6)]
        outputs = []
        for proc in procs:
            out, err = proc.communicate(timeout=120)
            outputs.append((out or err).strip())
        for output in outputs:
            with self.subTest(output=output):
                self.assertEqual(output, "REFUSED LEDGER_INIT_BUSY", outputs)
        holder.execute("ROLLBACK")
        holder.close()
        usage.UsageLedger(self.path, pricing=credit_only_pricing())


class TestADollarFloorCannotJudgeAnUnknownDollarRate(AuditCase):
    """Wave-3 / QA round 3: a credit-priced capability bypassed the US$ floor.

    With the shipped catalog every capability is zero dollars with a real credit unit,
    and a wallet floor captured in USD was compared against that zero: measured, a
    US$1.00 wallet with a US$0.20 floor admitted twelve chargeable calls while its
    committed dollars stayed at zero. An unknown charge is not a free one.
    """

    def usd_budget(self, units: int = 1_000_000, **kwargs) -> usage.CampaignBudget:
        return usage.CampaignBudget(usage.CampaignFloor(usage.CampaignBaseline(
            provider="serper", account_ref="serper-main", meter=usage.USD_MICROS,
            units=units, observed_at=NOW.isoformat(),
            source=usage.MeterReadingSource.VENDOR_ACCOUNT_READ, campaign_id="wave3")),
            **kwargs)

    def test_a_dollar_floor_refuses_a_call_with_no_known_dollar_rate(self):
        ledger = self.ledger(budgets={("serper", "serper-main"): self.usd_budget()})
        self.caps(ledger, calls=10_000)
        with self.assertRaises(usage.LedgerRefused) as ctx:
            ledger.reserve(request())
        self.assertEqual(ctx.exception.code, "CAMPAIGN_PRICE_UNKNOWN_FAIL_CLOSED")
        # Refused before anything was written, not after.
        self.assertEqual(ledger.operations(), ())

    def test_the_refusal_names_the_meter_the_floor_could_be_captured_in(self):
        ledger = self.ledger(budgets={("serper", "serper-main"): self.usd_budget()})
        self.caps(ledger, calls=10_000)
        with self.assertRaises(usage.LedgerRefused) as ctx:
            ledger.reserve(request())
        self.assertIn("credits", str(ctx.exception))

    def test_a_free_tier_rate_is_a_known_zero_and_still_passes(self):
        free = usage.PricingCatalog((
            usage.Price(provider="serper", capability="search", unit_micros=0, free_tier=True,
                        quota_unit="requests", quota_per_call=1, source="provider_declared_rate",
                        version="2026-10-17", observed_at="2026-10-17T00:00:00+00:00"),))
        ledger = self.ledger(pricing=free,
                             budgets={("serper", "serper-main"): self.usd_budget()})
        self.caps(ledger, calls=10_000)
        self.assertEqual(ledger.reserve(request()).charge_state, usage.ChargeState.RESERVED)

    def test_a_priced_capability_still_passes_and_still_moves_the_floor(self):
        priced = usage.PricingCatalog((
            usage.Price(provider="serper", capability="search", unit_micros=100_000,
                        quota_unit="credits", quota_per_call=1, source="operator_catalog",
                        version="2026-10-17", observed_at="2026-10-17T00:00:00+00:00"),))
        # US$1.00 wallet, US$0.20 floor, US$0.10 a call -> eight calls, then the floor.
        ledger = self.ledger(pricing=priced,
                             budgets={("serper", "serper-main"): self.usd_budget()})
        self.caps(ledger, calls=10_000, money=10 * 100_000)
        for index in range(8):
            ledger.reserve(request(operation_id=f"op-{index}", args={"query": f"q{index}"}))
        self.assertEqual(ledger.account_totals("serper", "serper-main").committed_micros,
                         8 * 100_000)
        with self.assertRaises(usage.LedgerRefused) as ctx:
            ledger.reserve(request(operation_id="op-8", args={"query": "q8"}))
        self.assertEqual(ctx.exception.code, "CAMPAIGN_FLOOR_WOULD_BREACH")

    def test_a_credit_floor_is_judged_in_credits_not_dollars(self):
        # The credit branch must be unaffected by the dollar-price check: credits are
        # the meter this account's floor was captured in.
        ledger = self.ledger(budgets={("serper", "serper-main"):
                                      credit_budget(4)})
        self.caps(ledger, calls=10_000)
        for index in range(3):
            ledger.reserve(request(operation_id=f"op-{index}", args={"query": f"q{index}"}))
        with self.assertRaises(usage.LedgerRefused) as ctx:
            ledger.reserve(request(operation_id="op-4", args={"query": "q4"}))
        self.assertEqual(ctx.exception.code, "CAMPAIGN_FLOOR_WOULD_BREACH")


class TestMigrationHistoryIsNotAPolicy(AuditCase):
    """Imported rows are receipts; they must not decide what may run today."""

    def test_an_imported_row_does_not_block_a_route_choice(self):
        ledger = self.ledger()
        self.caps(ledger, calls=100)
        ledger.import_legacy_record(
            legacy_ref="state/dual-search-ledger.sqlite3:search_attempts:run-00:op-old",
            project="devon", run_id="run-00", provider="serper", capability="search",
            transport="legacy_dual_search", created_at=NOW.isoformat())
        # The migration's contract is to preserve history without changing what may
        # run, so a REST-era receipt cannot close the embedded MCP route today.
        op = ledger.reserve(request(transport="embedded_official_mcp"))
        self.assertEqual(op.charge_state, usage.ChargeState.RESERVED)
        self.assertEqual(op.origin, "native")

    def test_an_imported_row_still_carries_no_money_and_no_cap_weight(self):
        ledger = self.ledger()
        self.caps(ledger, calls=1)
        ledger.import_legacy_record(
            legacy_ref="state/dual-search-ledger.sqlite3:search_attempts:run-00:op-old",
            project="devon", run_id="run-00", provider="serper", capability="search",
            transport="legacy_dual_search", created_at=NOW.isoformat())
        totals = ledger.account_totals("serper", "serper-main")
        self.assertEqual(totals.committed_micros, 0)
        self.assertEqual(totals.quota_units, 0)
        # The single call the run ceiling allows is still available to real work.
        self.assertEqual(ledger.reserve(request()).charge_state, usage.ChargeState.RESERVED)


class TestTheDocumentedBaselineIsHonestAboutMoney(AuditCase):
    """The credit-only catalog must not read as "these calls are free"."""

    def test_the_documented_rates_carry_a_named_source_and_no_verified_price(self):
        catalog = usage.documented_pricing()
        self.assertGreaterEqual(len(catalog), 5)
        for provider, capability, quota_unit in usage.DOCUMENTED_CREDIT_RATES:
            price = catalog.price_for(provider, capability)
            self.assertIsNotNone(price, f"{provider}/{capability} missing")
            self.assertEqual(price.unit_micros, 0)
            self.assertFalse(price.verified)
            self.assertEqual(price.quota_unit, quota_unit)
            self.assertTrue(price.source)

    def test_a_zero_dollar_rate_is_not_a_free_tier(self):
        ledger = self.ledger()
        self.caps(ledger)
        op = ledger.reserve(request())
        # free_tier is about the vendor's own terms; "nobody priced this" is not that.
        with self.assertRaises(usage.LedgerRefused) as ctx:
            ledger.settle_free_tier(op.operation_id, quota_units=1)
        self.assertEqual(ctx.exception.code, "FREE_TIER_CONTRADICTS_RATE")


if __name__ == "__main__":
    unittest.main()
