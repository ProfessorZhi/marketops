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
    calls, unknown, reserved, ambiguous = 0, 0, 0, 0
    state = "NO_OPERATIONS_YET"
    if not path.is_relative_to(base):
        raise ValueError("RUNTIME_LEDGER_PATH_ESCAPE")
    if path.exists():
        try:
            with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=5)) as db:
                calls, unknown, reserved, ambiguous = db.execute(
                    "SELECT COALESCE(SUM(CASE WHEN charge_state <> 'NO_CHARGE_PROVEN' "
                    "AND counts_against_caps=1 THEN 1 ELSE 0 END),0), "
                    "COALESCE(SUM(CASE WHEN charge_state='UNKNOWN' THEN 1 ELSE 0 END),0), "
                    "COALESCE(SUM(CASE WHEN charge_state='RESERVED' THEN 1 ELSE 0 END),0), "
                    "COALESCE(SUM(CASE WHEN charge_state='UNKNOWN' AND "
                    "COALESCE(execution_outcome,'') <> 'OK' THEN 1 ELSE 0 END),0) "
                    "FROM operations WHERE project=?", (ledger.project_for_root(base),)
                ).fetchone()
            state = "READ_FROM_UNIFIED_LEDGER"
        except (sqlite3.Error, OSError):
            calls = unknown = reserved = ambiguous = None
            state = "LEDGER_UNREADABLE"
    return {"limit": limit, "calls_used": calls,
            "remaining": max(0, limit - calls) if calls is not None else None,
            "unknown_charge_operations": unknown, "reserved_operations": reserved,
            # Of those, calls whose outcome is itself unknown (timeouts, lost
            # replies). Successful calls without a vendor bill are counted above
            # but are not a reason to stop.
            "ambiguous_outcome_operations": ambiguous,
            "scope": "project_total_preserved_across_runs", "metric": "requests_not_dollars",
            "state": state, "actual_usd": None}


def view(root, *, task_kind=None):
    settings = runtime.config(root)
    if task_kind is not None and task_kind not in ROUTES:
        raise ValueError("UNKNOWN_RESEARCH_TASK_KIND")
    current = runtime.status(root)
    # The project counter is measured against the project ceiling, not the
    # historical single ``max_calls``. Reading the wrong one made a run-level
    # bound look like a project-wide one, and the ``remaining`` number disagreed
    # with what the ledger would actually allow.
    budget = usage(root, settings.get("project_max_calls", settings["max_calls"]))
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
            "credits_verified": False,
            # Read from the same consent the ledger enforces. A hard-coded
            # ``False`` here disagreed with ``runtime.status`` the moment an
            # operator stated a USD cap, and the matrix is what a client reads.
            "dollar_limit_enforced": bool(current.get("dollar_limit_enforced")),
            "dollar_limit_enforcement": current.get("dollar_limit_enforcement"),
            "budget_mode": current.get("budget_mode"),
            "run_max_calls": current.get("run_max_calls"),
            "project_max_calls": current.get("project_max_calls"),
            "usd_caps": current.get("usd_caps"),
            "pricing_source": current.get("pricing_source"),
        }
    route = None if task_kind is None else {
        "task_kind": task_kind, "mode": "MANAGED_FINITE_REQUESTS",
        "candidates": [matrix[name] for name in ROUTES[task_kind]],
        "instruction": "Choose an admitted capability; source quality and actual charges remain independently checked.",
    }
    return {"runtime": current, "capability_matrix": matrix,
            "budgets": {"managed": budget}, "routing_advice": route,
            "providers": {
                "serper": {"transport": "MANAGED_REST"},
                "serpapi": {"transport": "EMBEDDED_OFFICIAL_MCP"},
                "tinyfish": {"transport": "EMBEDDED_OFFICIAL_MCP",
                             "agent": "DISABLED_IN_RESEARCH_PROFILE"},
                "crawl4ai": {"transport": "LOCAL_SDK_RESTRICTED_BROWSER"},
                "jev": {"transport": "TYPESAFE_SDK_SAVED_EVIDENCE_ONLY"},
            }}
