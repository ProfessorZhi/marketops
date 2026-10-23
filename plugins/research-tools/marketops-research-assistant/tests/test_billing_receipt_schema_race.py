"""The receipt-table migration must be decided by the table, not by who won.

`_ensure_search_attempts` adds ``outcome_code`` to the historical ``search_attempts``
table — the column that makes "may have been billed and cannot yet be proven" a
state rather than an absent field. A plain "read the columns, then ALTER if absent"
is a check-then-act, and the interleaving it loses to is the *ordinary* one for the
concurrent reservations this table exists to serialize: two processes read before
either writes, both decide the column is missing, and the second ``ALTER`` fails
with ``duplicate column name``.

Both halves are tested here. The deterministic half reproduces the exact
interleaving (a reader whose column answer is stale by one statement, which is what
losing the race *is*); the process half runs the real race so the fix is not only
proved against a mock.
"""
from __future__ import annotations

import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import dual_search_mcp as dsm  # noqa: E402


class _StaleCursor:
    """A cursor whose column answer predates another writer's ALTER."""

    def __init__(self, rows):
        self._rows = rows

    def fetchall(self):
        return list(self._rows)

    def __iter__(self):
        return iter(self._rows)


class _StaleByOneStatement:
    """The interleaving, stated: the first column question is answered stale.

    This is not a mock of SQLite. It is a connection that answers *one* question
    with what the table looked like one statement earlier — which is exactly the
    state a process is in when it loses the race. Everything else, including the
    ALTER and the re-read, goes to the real database.
    """

    def __init__(self, real):
        self._real = real
        self._stale_served = False

    def execute(self, sql, *params):
        if "table_info" in sql and not self._stale_served:
            self._stale_served = True
            rows = self._real.execute(sql, *params).fetchall()
            return _StaleCursor([r for r in rows if r[1] != "outcome_code"])
        return self._real.execute(sql, *params)


class StaleReadTests(unittest.TestCase):
    def test_losing_the_migration_race_is_not_an_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "dual-search-ledger.sqlite3"
            # Another process has already migrated the table.
            with sqlite3.connect(path) as db:
                dsm._ensure_search_attempts(db)

            with sqlite3.connect(path) as real:
                # This caller read one statement too early: it believes the column
                # is missing and will try to add it again.
                dsm._ensure_search_attempts(_StaleByOneStatement(real))

                columns = {row[1] for row in real.execute("PRAGMA table_info(search_attempts)")}
            self.assertIn("outcome_code", columns)

    def test_a_genuinely_unmigratable_table_fails_closed_with_a_closed_set_code(self):
        """When the ALTER really failed, the re-read says so and the caller is told.

        Losing a race and failing outright look identical from the exception, so
        the two are only distinguishable by looking at the table afterwards. This
        is the branch where the table still lacks the column, which must refuse
        rather than continue without a financial state to record.
        """
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "dual-search-ledger.sqlite3"
            with sqlite3.connect(path) as db:
                db.execute("CREATE TABLE search_attempts (run_id TEXT)")

                class _AlterFails:
                    """A DDL failure that leaves the column genuinely absent."""

                    def __init__(self, real):
                        self._real = real

                    def execute(self, sql, *params):
                        if sql.lstrip().upper().startswith("ALTER"):
                            raise sqlite3.OperationalError("database is locked")
                        return self._real.execute(sql, *params)

                with self.assertRaises(ValueError) as caught:
                    dsm._ensure_search_attempts(_AlterFails(db))
                self.assertTrue(
                    str(caught.exception).startswith("RECEIPT_SCHEMA_MIGRATION_FAILED"),
                    str(caught.exception))
                columns = {row[1] for row in db.execute("PRAGMA table_info(search_attempts)")}
            self.assertNotIn("outcome_code", columns)


_WORKER = """
import sys
from pathlib import Path
sys.path.insert(0, {scripts!r})
import sqlite3
import dual_search_mcp as dsm
path = Path({path!r})
with sqlite3.connect(path, timeout=20) as db:
    db.execute("PRAGMA busy_timeout=20000")
    dsm._ensure_search_attempts(db)
sys.stdout.write("OK")
"""


class ProcessRaceTests(unittest.TestCase):
    def test_twelve_processes_migrating_at_once_all_succeed(self):
        """The real race, not a mock: one fresh database, twelve simultaneous callers."""
        import json

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "dual-search-ledger.sqlite3"
            code = _WORKER.format(scripts=str(ROOT / "scripts"), path=str(path))
            procs = [subprocess.Popen([sys.executable, "-c", code],
                                      stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                      text=True, env=dict(os.environ))
                     for _ in range(12)]
            results = []
            for proc in procs:
                out, err = proc.communicate(timeout=90)
                results.append((out or err).strip())
            self.assertEqual(["OK"] * 12, results,
                             "a caller that did nothing wrong was failed by the "
                             f"migration: {json.dumps(results)}")
            with sqlite3.connect(path) as db:
                columns = {row[1] for row in db.execute("PRAGMA table_info(search_attempts)")}
            self.assertIn("outcome_code", columns)


if __name__ == "__main__":
    unittest.main()
