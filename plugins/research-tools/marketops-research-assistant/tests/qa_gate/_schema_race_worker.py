"""One reservation attempt, optionally with a *simulated* fix applied in-child.

Run as ``python _schema_race_worker.py <root> [--safe-fix]``.

The ``--safe-fix`` mode exists so the concurrency regression has a real control:
the same script, the same path, the same database state, with only the two
defective code paths patched at runtime. If the unpatched run fails and the
patched run passes, the failure is caused by those lines and by nothing else in
the fixture.

The patches are applied **in the child process only** and are never written to
the product. They are the smallest change that makes each path honour the
contract the tests assert:

* the receipt-column migration becomes idempotent under a race;
* every schema statement that cannot take the lock is translated into the
  module's own governed refusal instead of surfacing SQLite's exception.

Both schema sites are patched, because a run under exclusive contention reaches
both: the ledger's ``journal_mode``/create-schema in ``UsageLedger.__init__``
and the receipt table's create/ALTER in ``_ensure_search_attempts``. An earlier
version of this control patched only the first and the control itself failed,
which is how the second site was found.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent.parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS))

root = Path(sys.argv[1])
safe_fix = "--safe-fix" in sys.argv
ledger_only = "--ledger-only" in sys.argv
run_id = os.environ.get("QA_RUN_FIXED", "runRace")


def apply_safe_fix() -> None:
    """Patch, in this process only, the two paths the regression targets."""
    import sqlite3

    import dual_search_mcp as dsm
    import research_usage_ledger as ledger

    original_ensure = getattr(dsm, "_ensure_search_attempts", None)
    if original_ensure is None:
        # This branch predates the adapter's receipt schema; the patch has nothing
        # to make idempotent, and pretending otherwise would fail the control for
        # a reason unrelated to the defect under test.
        original_ensure = lambda db: None

    def safe_ensure(db):
        try:
            return original_ensure(db)
        except sqlite3.OperationalError as exc:
            if "duplicate column name" in str(exc):
                return  # the racing process already added it; that is success
            if "locked" in str(exc) or "busy" in str(exc):
                raise ledger.LedgerRefused(
                    "SCHEMA_LOCK_CONTENDED",
                    "the receipt schema could not be migrated; retry later") from None
            raise

    if hasattr(dsm, "_ensure_search_attempts"):
        dsm._ensure_search_attempts = safe_ensure

    original_init = ledger.UsageLedger.__init__

    def safe_init(self, *args, **kwargs):
        try:
            return original_init(self, *args, **kwargs)
        except sqlite3.OperationalError as exc:
            if "locked" in str(exc):
                raise ledger.LedgerRefused(
                    "LEDGER_LOCK_CONTENDED",
                    "the ledger could not take its schema lock; retry later") from None
            raise

    if "--ledger-only" in sys.argv:
        # The control has to bound *time*, not attempts, or it reproduces the very
        # defect it is controlling for: the product's retries each consume the full
        # busy timeout, so translating after them would take the same minutes the
        # assertion is complaining about. One short attempt, then a governed code.
        import sqlite3 as _sqlite3

        def bounded_init(self, *args, **kwargs):
            try:
                with _sqlite3.connect(self._path, timeout=1, isolation_level=None) as db:
                    db.execute("PRAGMA journal_mode")
            except _sqlite3.OperationalError as exc:
                if "locked" in str(exc) or "busy" in str(exc):
                    raise ledger.LedgerRefused(
                        "LEDGER_LOCK_CONTENDED",
                        "the ledger could not take its schema lock; retry later") from None
                raise
            return original_init(self, *args, **kwargs)

        safe_init = bounded_init

    ledger.UsageLedger.__init__ = safe_init


def main() -> int:
    if safe_fix:
        apply_safe_fix()
    if ledger_only:
        # Exercise the ledger's own schema initialisation and nothing else, so a
        # failure here belongs to the ledger module; the adapter's receipt schema
        # is a separate owner and a separate test.
        import research_usage_ledger as ledger

        try:
            ledger.UsageLedger(root / "state" / "dual-search-ledger.sqlite3")
        except (ValueError, ledger.LedgerRefused) as exc:
            # `LedgerRefused` is the module's own governed refusal and derives from
            # Exception rather than ValueError; treating it as "unhandled" made a
            # correct refusal read as a defect on the branch that fixed it.
            sys.stdout.write("REFUSED:" + str(exc))
        except Exception as exc:  # noqa: BLE001 - the driver error is the finding
            sys.stdout.write("UNHANDLED:" + type(exc).__name__ + ":" + str(exc))
        else:
            sys.stdout.write("OK")
        return 0
    import dual_search_mcp as dsm

    try:
        dsm._reserve(root, run_id=run_id, operation_id="op-1", fingerprint="sig-1",
                     provider="serper", limit=int(os.environ["MARKETOPS_SEARCH_MAX_CALLS"]))
    except ValueError as exc:
        # A governed refusal: the closed-set vocabulary the contract requires.
        sys.stdout.write("REFUSED:" + str(exc))
    else:
        sys.stdout.write("OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
