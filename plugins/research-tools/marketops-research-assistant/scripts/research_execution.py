"""Separate a saved checkpoint from observed research completion."""
from contextlib import closing
from pathlib import Path
import sqlite3


#: A contacts brief with no number ("dozens to hundreds") still has a bar to clear.
DEFAULT_MINIMUM_AB = 50


def expansion_spent(base, run_id) -> bool:
    """The agent recorded EXPAND_SOURCE exhausted: new lists stopped paying."""
    import research_methods
    tried = research_methods.status(base, run_id)["contacts"]["tried"]
    return any(t["method"] == "EXPAND_SOURCE" and t["outcome"] == "exhausted" for t in tried)


def rules_ok(kpi) -> bool:
    return bool(kpi and kpi.get("rules_source"))


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
    # The source gate: a source whose first targets gave too few A+B accounts has its
    # remaining targets parked before the queue is counted, so the Run moves on to
    # better sources (or closes) instead of researching the rest of a poor list.
    import research_grading as grading
    import research_sources as sources
    brief = {}
    try:
        parked = sources.park_stopped(base, run_id)
        grades, rules, brief = sources.graded(base, run_id)
        source_yield = sources.yields(base, run_id, grades=grades, brief=brief)
        kpi = {**grading.summary(grades, targets_finished=sources.finished_targets(base, run_id)),
               "rules_source": rules["source"]}
        family_state = sources.families(base, run_id, brief=brief, sources=source_yield["sources"])
    except (sqlite3.Error, OSError, ValueError) as exc:
        parked, source_yield = {"parked": 0, "sources": []}, {"sources": [], "error": type(exc).__name__}
        kpi, family_state = None, None
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
    # A contacts goal is not finished while a kind of organisation was never piloted: the
    # live Paris run closed on operators and towns and never looked at clubs.
    if (family_state and family_state["missing"] and not reached and remaining != 0
            and state in {"FINAL_METHODS_EXHAUSTED", "FINAL_KNOWN_TARGETS_RESEARCHED",
                          "CHECKPOINT_CONTINUE_RESEARCH"} and not pending):
        state, action, final = ("CHECKPOINT_CONTINUE_RESEARCH", "IMPORT_NEXT_TARGET_FAMILY", False)
    # A source that pays is widened before anything closes: more lists of the same
    # kind (next sport, département, town). The first live Claude Code run found its
    # A/B on committee officer pages, said so, and stopped at 12 people.
    try:
        wanted_ab = int(brief.get("minimum_ab") or 0) or None
    except (TypeError, ValueError):
        wanted_ab = None
    if wanted_ab is None and brief.get("goal") == "contacts":
        wanted_ab = DEFAULT_MINIMUM_AB
    ab_now = (kpi or {}).get("ab_people") or 0
    productive = [r for r in source_yield["sources"] if r.get("verdict") == "PROMOTE"]
    if wanted_ab and ab_now >= wanted_ab:
        reached = True
    expand = bool(productive and wanted_ab and ab_now < wanted_ab and remaining != 0
                  and not pending and not expansion_spent(base, run_id)
                  and state in {"FINAL_METHODS_EXHAUSTED", "FINAL_KNOWN_TARGETS_RESEARCHED",
                                "CHECKPOINT_CONTINUE_RESEARCH"})
    if expand:
        state, action, final = ("CHECKPOINT_CONTINUE_RESEARCH", "EXPAND_PRODUCTIVE_SOURCE", False)
    out = {"state": state, "is_final": final, "continuation_required": not final,
            "next_action": action, "remaining_requests": remaining,
            "pending_pool_targets": pending, "running_batch_jobs": running,
            "saved_results_to_finalize": saved, "sources_reconciled": reconciled,
            "goal_satisfied": reached, "ab_goal": wanted_ab, "coverage_scope": "known_targets_not_an_exhaustive_market_claim",
            "pools": [{"pool_id": p["pool_id"], "queue": p["by_status"], "queue_complete": p["queue_complete"]} for p in pools],
            "methods": methods,
            "venue_operator_groups": None if groups is None else {
                k: groups[k] for k in ("by", "venues_total", "groups_total", "groups_uncovered",
                                       "venues_in_uncovered_groups")},
            # Compact on purpose: marketops_next has to stay small in the agent's context.
            "grading": None if kpi is None else {
                k: kpi[k] for k in ("grades", "ab_people", "ab_per_100", "per_100_of", "rules_source")},
            "target_families": None if not family_state or not family_state["wanted"] else {
                k: family_state[k] for k in ("missing", "next_family")},
            "source_gate": {"parked_now": parked.get("parked", 0), "sources": [
                {k: r.get(k) for k in ("source_id", "finished", "with_ab", "pending", "verdict")}
                for r in source_yield["sources"][:12]]},
            "next_method": None if final else ladder["next_method"],
            "next_method_how": "" if final else ladder["next_method_how"]}
    advice = []
    for row in source_yield["sources"]:
        if row.get("verdict") == "OPTIMIZE_ONCE" and row["pending"]:
            advice.append(f"Source {row['source_id']}: {row['with_ab']}/{row['finished']} targets with "
                          "A or B. Change the approach for it once (other pages, roles or operator), "
                          "then go on; below 10% after that it is stopped.")
        elif row.get("verdict") == "STOP":
            advice.append(f"Source {row['source_id']} stopped at {row['with_ab']}/{row['finished']} "
                          "targets with A or B; its pending targets are parked. Find a better source "
                          "for this target type (see the scene's source order).")
    if expand:
        best = sorted(productive, key=lambda r: -(r["ab_rate"] or 0))[:3]
        advice.insert(0, f"{ab_now} A+B people of the {wanted_ab} wanted. These sources pay: "
                      + "; ".join(f"{r['source_id']} ({r['with_ab']}/{r['finished']} targets with A or B)"
                                  for r in best)
                      + ". Find more lists of the same kind before anything else: the same search "
                        "with the next sport, département, town or federation (DeepSeek search is "
                        "cheap), import each new list as its own source "
                        "(source_id '<same-kind>-<value>') and harvest it. Keep going while new "
                        "lists keep giving A or B; when a round of 10 new lists gives none, record "
                        "marketops_method(method='EXPAND_SOURCE', outcome='exhausted', note=...).")
    if family_state and family_state["missing"] and not final:
        nxt = family_state["next_family"]
        advice.append(f"Target family {nxt} has not been piloted: {family_state['next_family_how']}. "
                      f"Import 30 or more of them as their own source (marketops_pool_import("
                      f"family='{nxt}', ...)) and harvest them; if none exist for this task, "
                      f"record marketops_method(method='{nxt}', outcome='not_applicable', note=why).")
    if advice:
        out["source_advice"] = advice
    if not rules_ok(kpi) and not final:
        out["grading_advice"] = ("No role rules for grading: add scene_rules to the brief "
                                 "(decision / operational / entry / excluded title words in the "
                                 "market's language) with marketops_propose(supersedes=<brief>).")
    if not final:
        out["turn_rule"] = (
            "Research is not finished. Do not end your turn or write a final answer: do "
            "next_action now. A method that finds little is recorded with marketops_method("
            "outcome='exhausted') and the next method is used on the remaining targets; "
            "stopping one method never ends the research. Stop only when the budget is "
            "spent, the goal is met, every method is exhausted, or a decision only the user "
            "can make is needed (then ask that one question).")
    return out
