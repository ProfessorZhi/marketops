"""Read the same consent, provider admission and ledger used by real dispatch."""
from contextlib import closing
from pathlib import Path
import sqlite3

import research_runtime_setup as runtime
import research_usage_ledger as ledger

ROUTES = {
    "source_discovery": ("serper.search", "serpapi.search", "tinyfish.search"),
    "known_public_url": ("crawl4ai.fetch_page", "tinyfish.fetch_content"),
    "batch_public_pages": ("crawl4ai.fetch_page", "tinyfish.fetch_content"),
    "dynamic_navigation": ("crawl4ai.fetch_page",),
    "authorized_login_read": (),
    "semantic_choice": ("jev.choice",),
    "semantic_yes_no": ("jev.noul",),
    "semantic_score": ("jev.score",),
}


def usage(root, limit):
    """Request counters only; no schema creation/migration during discovery."""
    base = Path(root).resolve()
    path = (base / "state" / "usage-ledger.sqlite3").resolve()
    calls, unknown, reserved = 0, 0, 0
    state = "NO_OPERATIONS_YET"
    if not path.is_relative_to(base):
        raise ValueError("RUNTIME_LEDGER_PATH_ESCAPE")
    if path.exists():
        try:
            with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=5)) as db:
                calls, unknown, reserved = db.execute(
                    "SELECT COALESCE(SUM(CASE WHEN charge_state <> 'NO_CHARGE_PROVEN' "
                    "AND counts_against_caps=1 THEN 1 ELSE 0 END),0), "
                    "COALESCE(SUM(CASE WHEN charge_state='UNKNOWN' THEN 1 ELSE 0 END),0), "
                    "COALESCE(SUM(CASE WHEN charge_state='RESERVED' THEN 1 ELSE 0 END),0) "
                    "FROM operations WHERE project=?", (ledger.project_for_root(base),)
                ).fetchone()
            state = "READ_FROM_UNIFIED_LEDGER"
        except (sqlite3.Error, OSError):
            calls = unknown = reserved = None
            state = "LEDGER_UNREADABLE"
    return {"limit": limit, "calls_used": calls,
            "remaining": max(0, limit - calls) if calls is not None else None,
            "unknown_charge_operations": unknown, "reserved_operations": reserved,
            "scope": "project_total_preserved_across_runs", "metric": "requests_not_dollars",
            "state": state, "actual_usd": None}


def view(root, *, task_kind=None):
    settings = runtime.config(root)
    if task_kind is not None and task_kind not in ROUTES:
        raise ValueError("UNKNOWN_RESEARCH_TASK_KIND")
    budget = usage(root, settings["max_calls"])
    matrix = {}
    for capability in sorted(runtime.CORE):
        verdict = runtime.admission(root, capability)
        allowed = verdict["allowed"]
        state = "READY_FOR_BOUNDED_ATTEMPT" if allowed else "BLOCKED"
        if allowed and budget["remaining"] is None:
            state = "BUDGET_STATE_UNKNOWN"
        elif allowed and budget["remaining"] == 0:
            state = "ATTEMPT_LIMIT_EXHAUSTED"
        matrix[capability] = {
            "implemented_in_lite": True, "capability": capability, "status": state,
            "reason_code": verdict["reason_code"], "detail": verdict["detail"],
            "authorization_verified": allowed, "authentication_verified": False,
            "credits_verified": False, "dollar_limit_enforced": False,
        }
    route = None if task_kind is None else {
        "task_kind": task_kind, "mode": "MANAGED_FINITE_REQUESTS",
        "candidates": [matrix[name] for name in ROUTES[task_kind]],
        "instruction": "Choose an admitted capability; source quality and actual charges remain independently checked.",
    }
    return {"runtime": runtime.status(root), "capability_matrix": matrix,
            "budgets": {"managed": budget}, "routing_advice": route,
            "providers": {
                "serper": {"transport": "MANAGED_REST"},
                "serpapi": {"transport": "EMBEDDED_OFFICIAL_MCP"},
                "tinyfish": {"transport": "EMBEDDED_OFFICIAL_MCP",
                             "agent": "DISABLED_IN_RESEARCH_PROFILE"},
                "crawl4ai": {"transport": "LOCAL_SDK_RESTRICTED_BROWSER"},
                "jev": {"transport": "TYPESAFE_SDK_SAVED_EVIDENCE_ONLY"},
            }}
