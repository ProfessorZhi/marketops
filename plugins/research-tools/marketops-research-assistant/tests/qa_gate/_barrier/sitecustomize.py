"""A deterministic barrier for multiprocess races, injected via ``PYTHONPATH``.

Why this exists
---------------
A concurrency regression written as "start N processes and hope they collide"
is a flaky test: it passes on a buggy build whenever the scheduler happens to
serialise the processes, and a green run then means nothing. The two defects
this harness targets are real but probabilistic (measured ~4-12% per round), so
repeating rounds buys confidence at the cost of runtime and still never gives a
deterministic verdict.

What it does
------------
Python imports ``sitecustomize`` from ``sys.path`` at interpreter start, before
the module under test is imported. This shim replaces ``sqlite3.connect`` with a
thin proxy that, immediately before executing a statement whose text contains a
trigger, drops a marker file and waits until ``QA_BARRIER_N`` markers exist.

That widens a window that is **already reachable in production** — two
processes genuinely can reach the same schema statement together; it does not
create an unreachable interleaving. Both processes are held *after* their own
pre-condition read (e.g. ``PRAGMA table_info``) and released together, which is
exactly the interleaving that makes check-then-act fail.

It is inert unless ``QA_BARRIER_TRIGGERS`` and ``QA_BARRIER_DIR`` are set, so
importing it cannot affect any other test.

Environment
-----------
``QA_BARRIER_TRIGGERS``  ``;``-separated SQL substrings to stop before.
``QA_BARRIER_DIR``       directory for the marker files.
``QA_BARRIER_N``         how many processes must arrive before any proceeds.
``QA_BARRIER_TIMEOUT``   seconds to wait before proceeding anyway (default 20).

The timeout is a safety valve, not the mechanism: if it fires, the run degrades
to the ordinary probabilistic race rather than hanging the suite.
"""
from __future__ import annotations

import hashlib
import os
import pathlib
import sqlite3
import time

_TRIGGERS = tuple(t for t in os.environ.get("QA_BARRIER_TRIGGERS", "").split(";") if t)
_DIR = os.environ.get("QA_BARRIER_DIR", "")
_EXPECTED = int(os.environ.get("QA_BARRIER_N", "0") or 0)
_TIMEOUT = float(os.environ.get("QA_BARRIER_TIMEOUT", "20") or 20)


def _barrier(trigger: str) -> None:
    key = hashlib.sha256(trigger.encode("utf-8")).hexdigest()[:12]
    directory = pathlib.Path(_DIR)
    directory.mkdir(parents=True, exist_ok=True)
    (directory / f"ready-{key}-{os.getpid()}").write_text("1", encoding="utf-8")
    deadline = time.monotonic() + _TIMEOUT
    while time.monotonic() < deadline:
        if len(list(directory.glob(f"ready-{key}-*"))) >= _EXPECTED:
            return
        time.sleep(0.005)


if _TRIGGERS and _DIR and _EXPECTED > 1:  # pragma: no cover - runs in children only
    _real_connect = sqlite3.connect

    class _BarrierConnection:
        """Delegates everything; stops before a triggered statement."""

        __slots__ = ("_conn",)

        def __init__(self, conn):
            self._conn = conn

        def execute(self, sql, *args, **kwargs):
            if isinstance(sql, str):
                for trigger in _TRIGGERS:
                    if trigger in sql:
                        _barrier(trigger)
                        break
            return self._conn.execute(sql, *args, **kwargs)

        def executemany(self, sql, *args, **kwargs):
            return self._conn.executemany(sql, *args, **kwargs)

        def executescript(self, sql, *args, **kwargs):
            return self._conn.executescript(sql, *args, **kwargs)

        def __enter__(self):
            self._conn.__enter__()
            return self

        def __exit__(self, *exc_info):
            return self._conn.__exit__(*exc_info)

        def __getattr__(self, name):
            return getattr(self._conn, name)

        def __setattr__(self, name, value):
            # ``row_factory`` and the like are set on the connection object; a
            # proxy that only forwards reads turns a supported assignment into
            # an AttributeError, which would make this harness the thing that
            # breaks rather than the product.
            if name == "_conn":
                object.__setattr__(self, name, value)
            else:
                setattr(self._conn, name, value)

    def connect(*args, **kwargs):
        return _BarrierConnection(_real_connect(*args, **kwargs))

    sqlite3.connect = connect
