"""Separate a saved checkpoint from observed research completion."""
from contextlib import closing
from pathlib import Path
import sqlite3


def assess(root, run_id, *, goal, budget=None):
    import research_runtime_setup as runtime
    import research_runtime_status as runtime_status
    import research_usage_ledger as usage
    import research_pool as pool
    base = Path(root).resolve()
    settings = runtime.config(base)
    remaining = None
    if settings:
        limits = runtime.cap_plan(settings)
        remaining = runtime_status.usage(base, limits["project_max_calls"])["remaining"]
        path = base / "state/usage-ledger.sqlite3"
        if path.is_file():
            with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as db:
                used = db.execute("SELECT COUNT(*) FROM operations WHERE project=? AND run_id=? "
                                  "AND counts_against_caps=1 AND charge_state<>'NO_CHARGE_PROVEN'",
                                  (usage.project_for_root(base), run_id)).fetchone()[0]
            if remaining is not None and limits["run_max_calls"] is not None:
                remaining = min(remaining, max(0, limits["run_max_calls"] - used))
    elif isinstance(budget, dict):
        remaining = budget.get("search_remaining")
    pools = []
    path = base / "state/research-pool.sqlite3"
    if path.is_file():
        with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as db:
            names = {r[0] for r in db.execute("SELECT DISTINCT pool_id FROM pool_targets WHERE run_id=?", (run_id,))}
            if db.execute("SELECT 1 FROM sqlite_master WHERE name='pool_batch_jobs'").fetchone():
                names.update(r[0] for r in db.execute("SELECT DISTINCT pool_id FROM pool_batch_jobs WHERE run_id=?", (run_id,)))
        pools = [pool.status(base, pool_id=name, read_only=True) for name in sorted(names)]
    pending = sum(p["by_status"]["pending"] + p["by_status"]["claimed"] for p in pools)
    running = sum(p.get("in_flight", 0) for p in pools)
    saved = sum(p.get("results_awaiting_finalization", 0) for p in pools)
    reconciled = all(s["reconciled"] for p in pools for s in p["sources"])
    reached = goal.get("goal_satisfied") is True
    if saved:
        state, action, final = "CHECKPOINT_FINALIZE_SAVED_RESULTS", "CALL_HARVEST_BATCH_TO_FINALIZE", False
    elif running:
        state, action, final = "CHECKPOINT_WAIT_FOR_WORKERS", "WAIT_AND_CHECK_BATCH_PROGRESS", False
    elif remaining == 0:
        state, action, final = "FINAL_BUDGET_EXHAUSTED", "REPORT_PARTIAL_GOAL_AND_REMAINING_TARGETS", True
    elif pending:
        state, action, final = "CHECKPOINT_CONTINUE_RESEARCH", "CONTINUE_OR_RESUME_TARGET_POOL", False
    elif not reconciled:
        state, action, final = "CHECKPOINT_RECONCILE_SOURCES", "RECONCILE_SOURCE_COUNTS", False
    elif reached:
        state, action, final = "FINAL_KNOWN_TARGETS_RESEARCHED", "REPORT_CANDIDATES_AND_COVERAGE_LIMITS", True
    else:
        state, action, final = "CHECKPOINT_CONTINUE_RESEARCH", "FOLLOW_MARKETOPS_NEXT_OR_CHANGE_SOURCE_METHOD", False
    import research_methods
    methods = research_methods.status(base, run_id)
    ladder = methods["contacts"]
    # A venue inventory is only contact-researched when its operators were: the Paris
    # run tried 20 brands, called the ladder exhausted and left 4,737 venues untouched.
    groups = None
    if not reached:
        import research_bulk
        try:
            groups = research_bulk.venue_groups(base, run_id=run_id, by="operator", limit=1)
            if groups["venues_total"] and groups["groups_total"] == 0:
                groups = research_bulk.venue_groups(base, run_id=run_id, by="city", limit=1)
        except Exception:  # noqa: BLE001 - grouping is advice, not a gate on reading
            groups = None
    # Covering every operator group is the route, not the goal: once OPERATOR has been
    # worked hard enough to be recorded exhausted (an earned record, see
    # research_methods.effort), more groups of the same kind will not change the answer.
    # Run2 kept adding towns, 0 personal emails per batch, and could never close.
    operator_done = any(t["method"] == "OPERATOR" and t["outcome"] == "exhausted"
                        for t in ladder["tried"])
    uncovered = bool(groups and groups["groups_uncovered"]) and not operator_done
    if uncovered and (final or state == "CHECKPOINT_CONTINUE_RESEARCH") and remaining != 0:
        state, action, final = ("CHECKPOINT_CONTINUE_RESEARCH",
                                "IMPORT_VENUE_OPERATORS_AND_HARVEST", False)
        ladder = {**ladder, "next_method": "OPERATOR", "all_exhausted": False,
                  "next_method_how": dict(research_methods.LADDERS["contacts"])["OPERATOR"]
                  + f" Use marketops_venue_groups(run_id, by='{groups['by']}', "
                    "import_to_pool=...) to turn the venues into operator targets, largest first."}
    if state == "CHECKPOINT_CONTINUE_RESEARCH" and not pending and ladder["all_exhausted"]:
        # Every method on the ladder was tried and said exhausted: a real stop.
        state, action, final = "FINAL_METHODS_EXHAUSTED", "REPORT_PARTIAL_GOAL_AND_METHODS_TRIED", True
    out = {"state": state, "is_final": final, "continuation_required": not final,
            "next_action": action, "remaining_requests": remaining,
            "pending_pool_targets": pending, "running_batch_jobs": running,
            "saved_results_to_finalize": saved, "sources_reconciled": reconciled,
            "goal_satisfied": reached, "coverage_scope": "known_targets_not_an_exhaustive_market_claim",
            "pools": [{"pool_id": p["pool_id"], "queue": p["by_status"], "queue_complete": p["queue_complete"]} for p in pools],
            "methods": methods,
            "venue_operator_groups": None if groups is None else {
                k: groups[k] for k in ("by", "venues_total", "groups_total", "groups_uncovered",
                                       "venues_in_uncovered_groups")},
            "next_method": None if final else ladder["next_method"],
            "next_method_how": "" if final else ladder["next_method_how"]}
    if not final:
        out["turn_rule"] = (
            "Research is not finished. Do not end your turn or write a final answer: do "
            "next_action now. A method that finds little is recorded with marketops_method("
            "outcome='exhausted') and the next method is used on the remaining targets; "
            "stopping one method never ends the research. Stop only when the budget is "
            "spent, the goal is met, every method is exhausted, or a decision only the user "
            "can make is needed (then ask that one question).")
    return out
