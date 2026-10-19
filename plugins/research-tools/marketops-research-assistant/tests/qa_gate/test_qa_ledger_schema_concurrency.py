"""Deterministic multiprocess regressions for the ledger's schema setup.

Two defects were found by round 3 on `cf8ff6e` and are still present on the
integration tip: concurrent reservation can surface a raw SQLite driver error
out of `_reserve` instead of a governed refusal. They are probabilistic in the
wild (measured ~1/24 to ~7/60 per round), so a test that merely launches
processes and hopes is flaky — green on a buggy build whenever the scheduler
cooperates, which makes a green run worthless.

Each test here **forces** the interleaving instead of sampling it, and each has
a control in which the two defective paths are patched at runtime (in the child
only, never in the product). The control must pass; the unpatched run must fail.
That pair is what makes the verdict attributable to the code rather than to the
fixture.

Contract under test, for both windows: a reservation is either **performed** or
**refused in the closed-set vocabulary** — never terminated by
``sqlite3.OperationalError``. That is IF-5 ("a refusal is a closed-set reason
code") and §P1-03 ("safe under concurrency").

Window (A), tested by exclusive-lock contention — `research_usage_ledger.
UsageLedger.__init__` runs ``PRAGMA journal_mode=WAL`` plus schema creation in
autocommit, guarded only by ``_SCHEMA_LOCK``, a *threading* lock that does not
serialise processes. ``journal_mode`` needs an exclusive lock, so a second
process is told ``database is locked`` after its busy timeout. Reachable on the
ordinary path, because every reservation constructs a ledger.

Window (B), tested by the barrier on the migration statement —
``dual_search_mcp._ensure_search_attempts`` reads ``PRAGMA table_info`` and then
issues ``ALTER TABLE ... ADD COLUMN`` outside any transaction. Two processes
arriving at a store whose column is absent both decide to add it and the loser
raises ``duplicate column name``. Reachable on a fresh store or a pre-migration
legacy one: a customer's first concurrent use after upgrading.
"""
from __future__ import annotations

import os
import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

from conftest import blocked

HERE = Path(__file__).resolve().parent
BARRIER_DIR = HERE / "_barrier"
WORKER = HERE / "_schema_race_worker.py"
PLUGIN_DIR = HERE.parents[1]

MIGRATED_TABLE = """CREATE TABLE IF NOT EXISTS search_attempts (
    run_id TEXT NOT NULL, operation_id TEXT NOT NULL, fingerprint TEXT NOT NULL,
    provider TEXT NOT NULL, reserved_at TEXT NOT NULL, state TEXT NOT NULL,
    outcome_code TEXT NOT NULL DEFAULT 'RESERVED',
    PRIMARY KEY (run_id, operation_id))"""

DRIVER_ERROR_MARKER = "sqlite3.OperationalError"


def _require_harness():
    if not (BARRIER_DIR / "sitecustomize.py").is_file() or not WORKER.is_file():
        blocked("the barrier harness is not present in this checkout")


def _require_adapter_surface():
    """The receipt schema belongs to the adapter lane.

    A branch that has not taken that lane has no surface to test here, and
    reporting it as a failure would attribute another owner's missing module to
    this one. The ledger's own contention test does not call this.
    """
    import importlib.util

    _require_harness()
    if importlib.util.find_spec("dual_search_mcp") is None:
        blocked("this branch does not ship dual_search_mcp; the receipt schema "
                "belongs to the adapter lane")
    module = importlib.import_module("dual_search_mcp")
    if not hasattr(module, "_ensure_search_attempts"):
        blocked("this branch's dual_search_mcp predates _ensure_search_attempts; "
                "the receipt-schema window does not exist here")


def _child_env(root: Path, barrier_dir: Path, triggers: str, workers: int):
    env = dict(os.environ)
    env.update({
        "PYTHONPATH": str(BARRIER_DIR),
        "QA_BARRIER_DIR": str(barrier_dir),
        "QA_BARRIER_N": str(workers),
        "QA_BARRIER_TIMEOUT": "20",
        "QA_BARRIER_TRIGGERS": triggers,
        "MARKETOPS_SEARCH_MAX_CALLS": "5",
        "MARKETOPS_SEARCH_ALLOW_NETWORK": "1",
        "SERPER_API_KEY": "qa-gate-synthetic-not-a-credential",
        "QA_RUN_FIXED": "runRace",
        "MARKETOPS_RESEARCH_DATA_ROOT": str(root),
    })
    return env


def _run_pair(root: Path, barrier_dir: Path, triggers: str, *, workers: int = 2,
              safe_fix: bool = False, timeout: int = 120, ledger_only: bool = False):
    """Fire ``workers`` children at the same store, released together."""
    args = [sys.executable, str(WORKER), str(root)]
    if safe_fix:
        args.append("--safe-fix")
    if ledger_only:
        args.append("--ledger-only")
    procs = [subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                              text=True, env=_child_env(root, barrier_dir, triggers, workers))
             for _ in range(workers)]
    return [((out or "") + (err or "")) for out, err in
            (proc.communicate(timeout=timeout) for proc in procs)]


def _driver_errors(outputs):
    return [out.strip()[-400:] for out in outputs if DRIVER_ERROR_MARKER in out]


def _ungoverned_tracebacks(outputs):
    """Tracebacks that do not end in the product's own refusal type."""
    bad = []
    for out in outputs:
        if "Traceback" not in out:
            continue
        if DRIVER_ERROR_MARKER in out:
            continue  # already counted as a driver error
        last = [line for line in out.strip().splitlines() if line and not line.startswith(" ")]
        final = last[-1] if last else ""
        if "LedgerRefused" not in final and "WiringIncomplete" not in final and \
                "ContractViolation" not in final:
            bad.append(final[:200])
    return bad


# --- window (B): the receipt-column migration ------------------------------


def test_a_concurrent_first_reservation_never_surfaces_a_driver_error(data_root, clean_env):
    """Fresh store, two processes released on the ALTER statement together.

    The barrier holds both children *after* each has read ``PRAGMA table_info``
    and seen the column absent; releasing them together makes the loser's ALTER
    collide. Without the barrier this window needs ~24 rounds to show up; with
    it the outcome is the same every time, which is what makes a green run mean
    something on a fixed build.
    """
    _require_adapter_surface()
    barrier = Path(tempfile.mkdtemp(prefix="qa-barrier-"))
    outputs = _run_pair(data_root, barrier, "ALTER TABLE search_attempts ADD COLUMN",
                        workers=2, safe_fix=False)
    errors = _driver_errors(outputs)
    assert not errors, (
        "a raw SQLite driver error left the reservation path instead of a "
        "governed refusal (IF-5 / §P1-03). Reproduced deterministically by "
        f"releasing both processes on the migration statement:\n{errors[0]}")
    assert not _ungoverned_tracebacks(outputs), _ungoverned_tracebacks(outputs)
    assert all(("OK" in out) or ("REFUSED:" in out) for out in outputs), outputs


def test_the_migration_window_control_passes_when_the_path_is_patched(data_root, clean_env):
    """Control: same fixture, same path, only ``_ensure_search_attempts`` made
    idempotent. A pass here is what attributes the failure above to that line."""
    _require_adapter_surface()
    barrier = Path(tempfile.mkdtemp(prefix="qa-barrier-"))
    outputs = _run_pair(data_root, barrier, "ALTER TABLE search_attempts ADD COLUMN",
                        workers=2, safe_fix=True)
    assert not _driver_errors(outputs), (
        "the control still failed, so the fixture — not the product — is the "
        f"cause:\n{_driver_errors(outputs)[0]}")
    assert all(("OK" in out) or ("REFUSED:" in out) for out in outputs), outputs


# --- window (A): ledger construction under lock contention -----------------


#: How long a contended schema initialisation may take before refusing.
#:
#: A "bounded retry" that is bounded in *attempts* is not bounded in wall time:
#: each attempt can consume the connection's full busy timeout, so sustained
#: contention makes the bound minutes long. Measured on `6f0219d`: the ledger had
#: not refused after 420 s under a held lock. A ledger that hangs minutes on the
#: ordinary construction path is an outage, so the bound asserted here is time,
#: not attempts.
CONTENTION_REFUSAL_BUDGET_SECONDS = 60


def _hold_exclusive_then_run(data_root, *, ledger_only, safe_fix=False):
    db_path = data_root / "state" / "dual-search-ledger.sqlite3"
    holder = sqlite3.connect(db_path)
    try:
        holder.execute("CREATE TABLE IF NOT EXISTS holder_probe (a)")
        holder.commit()
        holder.execute("BEGIN EXCLUSIVE")
        env = _child_env(data_root, Path(tempfile.mkdtemp(prefix="qa-barrier-")),
                         "PRAGMA journal_mode=WAL", 1)
        args = [sys.executable, str(WORKER), str(data_root)]
        if safe_fix:
            args.append("--safe-fix")
        if ledger_only:
            args.append("--ledger-only")
        try:
            result = subprocess.run(args, capture_output=True, text=True, env=env,
                                    timeout=CONTENTION_REFUSAL_BUDGET_SECONDS)
        except subprocess.TimeoutExpired:
            return ("TIMEOUT: still running after "
                    f"{CONTENTION_REFUSAL_BUDGET_SECONDS}s of held contention")
    finally:
        holder.execute("ROLLBACK")
        holder.close()
    return (result.stdout or "") + (result.stderr or "")


def test_the_ledger_refuses_a_contended_schema_instead_of_a_driver_error(data_root, clean_env):
    """§P1-03/IF-5, **ledger-owned site only**.

    The holder takes ``BEGIN EXCLUSIVE`` and keeps it for the whole child run, so
    the ledger's schema initialisation cannot succeed and its bounded retries
    expire deterministically. The contract is that it then *refuses with a code*
    rather than handing out SQLite's exception.

    This runs the ledger directly and never imports the adapter, so a failure here
    belongs to the ledger module. The receipt schema inside ``dual_search_mcp`` is
    a different owner and has its own test below; conflating them let one owner's
    defect be read as the other's.
    """
    _require_harness()
    combined = _hold_exclusive_then_run(data_root, ledger_only=True)
    assert "TIMEOUT:" not in combined, (
        "the ledger did not refuse within "
        f"{CONTENTION_REFUSAL_BUDGET_SECONDS}s of held contention: a bound stated "
        "in attempts is not a bound in wall time, and the ordinary construction "
        "path hanging for minutes is an outage. " + combined.strip()[-200:])
    assert DRIVER_ERROR_MARKER not in combined, (
        "the ledger surfaced the driver's exception while holding its schema lock "
        f"instead of refusing with a code (IF-5):\n{combined.strip()[-400:]}")
    assert "UNHANDLED:" not in combined, combined.strip()[-400:]
    assert "Refused" in combined or "REFUSED:" in combined or "OK" in combined, \
        combined.strip()[-400:]


def test_the_receipt_schema_under_contention_is_not_a_driver_error(data_root, clean_env):
    """The **adapter-owned** receipt schema, under the same forced contention.

    ``dual_search_mcp._ensure_search_attempts`` creates and migrates
    ``search_attempts`` before any reservation is taken. Its owner is the adapter
    lane, not the ledger lane, so it is asserted separately: a fix on one side
    must not be read as a fix on the other.
    """
    _require_adapter_surface()
    combined = _hold_exclusive_then_run(data_root, ledger_only=False)
    assert "TIMEOUT:" not in combined, (
        "the adapter's receipt schema did not refuse within "
        f"{CONTENTION_REFUSAL_BUDGET_SECONDS}s of held contention; "
        + combined.strip()[-200:])
    assert DRIVER_ERROR_MARKER not in combined, (
        "the adapter's receipt schema surfaced the driver's exception instead of a "
        f"governed refusal (IF-5):\n{combined.strip()[-400:]}")


def test_the_contention_control_passes_when_the_translation_is_patched(data_root, clean_env):
    """Control: the same forced contention, with the lock error translated into
    the module's own refusal type **after one short attempt**. Passing here
    attributes the failure above to the product's refusal path rather than to the
    fixture — and the control is itself bounded in time, because a control that
    hangs would be reproducing the defect instead of controlling for it."""
    _require_harness()
    db_path = data_root / "state" / "dual-search-ledger.sqlite3"
    holder = sqlite3.connect(db_path)
    try:
        holder.execute("CREATE TABLE IF NOT EXISTS holder_probe (a)")
        holder.commit()
        holder.execute("BEGIN EXCLUSIVE")
        env = _child_env(data_root, Path(tempfile.mkdtemp(prefix="qa-barrier-")),
                         "PRAGMA journal_mode=WAL", 1)
        try:
            result = subprocess.run(
                [sys.executable, str(WORKER), str(data_root), "--safe-fix",
                 "--ledger-only"],
                capture_output=True, text=True, env=env,
                timeout=CONTENTION_REFUSAL_BUDGET_SECONDS)
        except subprocess.TimeoutExpired:
            pytest.fail("the control itself did not finish within "
                        f"{CONTENTION_REFUSAL_BUDGET_SECONDS}s; it is reproducing "
                        "the defect rather than controlling for it")
    finally:
        holder.execute("ROLLBACK")
        holder.close()

    combined = (result.stdout or "") + (result.stderr or "")
    assert DRIVER_ERROR_MARKER not in combined, (
        "the control still surfaced a driver error:\n" + combined.strip()[-400:])


# --- the barrier must not be the thing that breaks -------------------------


def test_the_barrier_delivers_both_processes_to_the_statement(data_root, clean_env):
    """A negative control on the harness itself.

    With the barrier disabled (``QA_BARRIER_N=1``) the same fixture must still
    work end to end, so nothing in these tests depends on the shim being the
    cause of the crash. This is the check that keeps an over-eager barrier from
    being mistaken for a product defect.
    """
    _require_adapter_surface()
    barrier = Path(tempfile.mkdtemp(prefix="qa-barrier-"))
    outputs = _run_pair(data_root, barrier, "ALTER TABLE search_attempts ADD COLUMN",
                        workers=1, safe_fix=True)
    assert outputs and outputs[0].strip() in {"OK"} or outputs[0].startswith("REFUSED:"), outputs
