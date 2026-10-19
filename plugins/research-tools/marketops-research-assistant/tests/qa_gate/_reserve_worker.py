"""Subprocess entry point: attempt one reservation, print the verdict.

Run as ``python _reserve_worker.py <mode> <root> <run_id> <operation_id> <signature>``.

It exists so the concurrency tests race *real processes* against the real
SQLite files. An in-process thread race would not exercise the
``BEGIN IMMEDIATE`` / ``busy_timeout`` path that actually decides who wins.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent.parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS))

mode, root, run_id, operation_id, signature = sys.argv[1:6]
root_path = Path(root)
run_id = (os.environ.get("QA_RUN_FIXED")
           or os.environ.get("QA_RUN_PREFIX", "") + run_id)

try:
    if mode == "search":
        import dual_search_mcp as mod

        mod._reserve(root_path, run_id=run_id, operation_id=operation_id,
                     fingerprint=signature, provider="serper",
                     limit=int(os.environ["MARKETOPS_SEARCH_MAX_CALLS"]))
    elif mode == "ext":
        import research_provider_extensions as mod

        mod._reserve(root_path, run_id=run_id, operation_id=operation_id,
                     action="serpapi_mcp_search", signature=signature)
    else:
        raise SystemExit("unknown mode " + mode)
except ValueError as exc:
    sys.stdout.write("ERR:" + str(exc))
else:
    sys.stdout.write("OK")
