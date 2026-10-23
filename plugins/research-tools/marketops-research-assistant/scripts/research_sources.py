#!/usr/bin/env python3
"""Which target sources yield A+B, and stopping the ones that do not.

Past manual work measured the same method on different lists: 30% A+B accounts
from one dealer locator, 3.33% from another; 26.67% from an association list,
3.33% from a public-contracts register. The list chosen mattered nine times more
than how deep each account was researched, yet the 30-account pilot rule that
caught this was applied by hand on two lines out of a dozen. Here it is applied
by code to every source a Run imports targets from:

  first PILOT targets finished (default 30), then
    A+B accounts >= PROMOTE (10%)  -> PROMOTE, keep going
    >= STOP (5%)                   -> OPTIMIZE_ONCE: change the approach once
    below                          -> STOP: the source's pending targets are parked
  after 2 x PILOT finished, below PROMOTE -> STOP (it was optimised once).

Parked targets are HOLD with reason SOURCE_YIELD_STOP and can be reopened with
marketops_pool_reopen(reason='SOURCE_YIELD_STOP'). A brief can tune or switch
off the gate: source_gate={pilot, promote, stop} or source_gate=false.
"""
from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

import research_grading as grading
import research_workflow as workflow

__all__ = ["PARK_REASON", "settings", "graded", "yields", "park_stopped", "finished_targets",
           "run_targets", "FAMILIES", "set_family", "families"]

PARK_REASON = "SOURCE_YIELD_STOP"

#: The kinds of organisation a contacts goal pilots, each as its own pool source. In the
#: live Paris run only operators and towns were researched; the A and B came from club
#: committee pages, a family the Run never imported.
FAMILIES = {
    "OPERATORS": "who owns or runs the targets: town or council departments, management "
                 "companies, chains, trusts",
    "CLUBS_ASSOCIATIONS": "clubs and associations based at the targets or in the area; their "
                          "committee pages name officers with their own addresses",
    "SCHOOLS_UNIVERSITIES": "schools, colleges and universities with sports facilities or a "
                            "sports service",
    "FEDERATION_LISTS": "each sport's federation or league list of clubs in the area",
}
DEFAULTS = {"pilot": 30, "promote": 0.10, "stop": 0.05}


def _records(root: Path, run_id: str) -> list[dict]:
    path = Path(root) / "state" / "research-lite.sqlite3"
    if not path.is_file():
        return []
    with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as db:
        rows = db.execute("SELECT record_id,stage,kind,status,entity_key,payload_json,artifact_ids_json "
                          "FROM records WHERE run_id=? ORDER BY created_at,record_id", (run_id,)).fetchall()
    out = []
    for r in rows:
        try:
            out.append({"record_id": r[0], "stage": r[1], "kind": r[2], "status": r[3],
                        "entity_key": r[4], "data": json.loads(r[5]),
                        "artifact_ids": json.loads(r[6])})
        except ValueError:
            continue
    return out


def _brief(records: list[dict]) -> dict:
    briefs = [r for r in records if r["kind"] == "observation"
              and (r.get("data") or {}).get("record_type") == "research_brief"]
    tip = workflow._tip(briefs) if briefs else None
    return (tip or {}).get("data") or {}


def settings(brief: dict) -> dict | None:
    """Gate thresholds for this Run, or None when the brief switched the gate off."""
    gate = brief.get("source_gate", True)
    if gate is False:
        return None
    out = dict(DEFAULTS)
    if isinstance(gate, dict):
        for key, kind in (("pilot", int), ("promote", float), ("stop", float)):
            value = gate.get(key)
            if isinstance(value, (int, float)) and not isinstance(value, bool) and value > 0:
                out[key] = kind(value)
    return out


def graded(root, run_id: str) -> tuple[dict, dict, dict]:
    """(grades by route, rules, brief) for the Run's active records."""
    records = _records(root, run_id)
    effective, _ = workflow.active_records(records) if records else ([], [])
    brief = _brief(records)
    grades, rules = grading.grade_routes(effective, brief)
    return grades, rules, brief


def _pool_rows(root, run_id: str) -> list[tuple]:
    path = Path(root) / "state" / "research-pool.sqlite3"
    if not path.is_file():
        return []
    with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as db:
        pools = [p for (p,) in db.execute(
            "SELECT DISTINCT pool_id FROM pool_targets WHERE run_id=?", (run_id,))]
        rows = []
        for pool_id in pools:
            rows += db.execute(
                "SELECT pool_id, source_id, status, run_id, organization_key, reason "
                "FROM pool_targets WHERE pool_id=?", (pool_id,)).fetchall()
    return rows


def _finished(status: str, reason: str) -> bool:
    return status in {"done", "hold"} and not str(reason).startswith(PARK_REASON)


def run_targets(root, run_id: str) -> list[dict]:
    """Every pool target this Run touched, for the no-route list of a delivery."""
    path = Path(root) / "state" / "research-pool.sqlite3"
    if not path.is_file():
        return []
    with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as db:
        rows = db.execute("SELECT name, website, address, status, reason, source_id "
                          "FROM pool_targets WHERE run_id=? ORDER BY source_id, name", (run_id,)).fetchall()
    return [dict(zip(("name", "website", "address", "status", "reason", "source_id"), r)) for r in rows]


def _families_db(root, write: bool = False):
    path = Path(root) / "state" / "research-pool.sqlite3"
    if not write:
        if not path.is_file():
            return None
        return sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)
    path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path, timeout=30)
    db.execute("CREATE TABLE IF NOT EXISTS source_families (pool_id TEXT NOT NULL, "
               "source_id TEXT NOT NULL, family TEXT NOT NULL, PRIMARY KEY(pool_id, source_id))")
    return db


def set_family(root, pool_id: str, source_id: str, family: str) -> None:
    """Record which kind of organisation a pool source lists."""
    family = str(family or "").strip().upper()
    if family not in FAMILIES:
        raise ValueError("TARGET_FAMILY_INVALID: one of " + ", ".join(FAMILIES))
    with closing(_families_db(root, write=True)) as db, db:
        db.execute("INSERT OR REPLACE INTO source_families VALUES(?,?,?)", (pool_id, source_id, family))


def families(root, run_id: str, *, brief: dict, sources: list[dict]) -> dict:
    """Which organisation families a contacts goal still has to pilot.

    A family is piloted once one of its sources has finished its pilot (or every target
    it listed); the agent may also record a family as not applicable with a reason
    (marketops_method(method=<family>, outcome='not_applicable', note=...)).
    """
    wanted = brief.get("target_families")
    if not isinstance(wanted, list) or not wanted:
        wanted = list(FAMILIES) if brief.get("goal") == "contacts" else []
    wanted = [f for f in (str(x).upper() for x in wanted) if f in FAMILIES]
    labels = {}
    db = _families_db(root)
    if db is not None:
        with closing(db):
            if db.execute("SELECT 1 FROM sqlite_master WHERE name='source_families'").fetchone():
                labels = {(p, s): f for p, s, f in db.execute(
                    "SELECT pool_id, source_id, family FROM source_families")}
    gate = settings(brief) or DEFAULTS
    piloted, started = set(), set()
    for row in sources:
        family = labels.get((row["pool_id"], row["source_id"]))
        if not family:
            continue
        started.add(family)
        if row["finished"] >= gate["pilot"] or (row["finished"] and not row["pending"]):
            piloted.add(family)
    import research_methods
    skipped = {t["method"] for t in research_methods.status(root, run_id).get("families", {})
               .get("tried", []) if t["outcome"] in {"not_applicable", "exhausted"}}
    missing = [f for f in wanted if f not in piloted and f not in skipped]
    nxt = next((f for f in missing if f not in started), missing[0] if missing else None)
    return {"wanted": wanted, "piloted": sorted(piloted), "started": sorted(started),
            "not_applicable": sorted(skipped & set(wanted)), "missing": missing,
            "next_family": nxt, "next_family_how": FAMILIES.get(nxt, "")}


def finished_targets(root, run_id: str) -> int:
    """Pool targets this Run actually finished (parked ones are not work done)."""
    return sum(1 for _, _, status, rid, _, reason in _pool_rows(root, run_id)
               if rid == run_id and _finished(status, reason))


def yields(root, run_id: str, *, grades: dict | None = None, brief: dict | None = None) -> dict:
    """Per source: finished, with A+B, pending, rate and verdict."""
    if grades is None:
        grades, _, brief = graded(root, run_id)
    gate = settings(brief or {})
    ab_orgs = {g["organization_id"] for g in grades.values()
               if g["grade"] in {"A", "B"} and g["organization_id"]}
    # A club split from a target's list page earns its A/B for that target's source:
    # the list is what the source delivered.
    for record in _records(root, run_id):
        data = record.get("data") or {}
        if (record["kind"] == "organization" and record["entity_key"] in ab_orgs
                and data.get("listed_by_organization_id")):
            ab_orgs.add(data["listed_by_organization_id"])
    table: dict[tuple[str, str], dict] = {}
    for pool_id, source_id, status, rid, org, reason in _pool_rows(root, run_id):
        row = table.setdefault((pool_id, source_id), {
            "pool_id": pool_id, "source_id": source_id, "finished": 0, "with_ab": 0,
            "pending": 0, "parked": 0})
        if status == "pending":
            row["pending"] += 1
        elif status == "hold" and str(reason).startswith(PARK_REASON):
            row["parked"] += 1
        elif rid == run_id and _finished(status, reason):
            row["finished"] += 1
            row["with_ab"] += int(bool(org) and org in ab_orgs)
    for row in table.values():
        n = row["finished"]
        row["ab_rate"] = round(row["with_ab"] / n, 3) if n else None
        if gate is None:
            row["verdict"] = "GATE_OFF"
        elif n < gate["pilot"] and row["pending"]:
            row["verdict"] = "PILOT"
            row["needs"] = gate["pilot"] - n
        elif n < gate["pilot"]:
            # A short list, all done: judged on what it gave, never left in pilot.
            row["verdict"] = "PROMOTE" if n and row["ab_rate"] >= gate["promote"] else "SMALL_DONE"
        elif row["ab_rate"] >= gate["promote"]:
            row["verdict"] = "PROMOTE"
        elif n >= 2 * gate["pilot"] or row["ab_rate"] < gate["stop"]:
            row["verdict"] = "STOP"
        else:
            row["verdict"] = "OPTIMIZE_ONCE"
    return {"gate": gate, "sources": sorted(table.values(), key=lambda r: (r["pool_id"], r["source_id"])),
            "unit": "pool targets finished by this Run; A+B = organisation has an A or B route"}


def _note_yields(root, run_id: str, stopped: list[dict]) -> None:
    """Each stopped source goes into the experience pack, like a wall: the next Run on
    a similar scene sees which kind of list gave nothing."""
    import re
    from research_event_journal import append_note
    for row in stopped:
        ref = re.sub(r"[^A-Za-z0-9_.-]+", "-", f"yield-{row['pool_id']}-{row['source_id']}")[:120]
        try:
            append_note(root, run_id=run_id, note_id=ref, stage="discovery",
                        category="source_low_yield",
                        problem=f"Source {row['source_id']} gave {row['with_ab']} of {row['finished']} "
                                "finished targets with an A or B route",
                        action_taken="Parked its remaining targets (SOURCE_YIELD_STOP)",
                        observed_result=f"A+B account rate {row['ab_rate']}",
                        hypothesis="This kind of list rarely publishes role-bound emails for this "
                                   "scene; prefer sources whose pages name the wanted roles",
                        evidence_ref=ref)
        except (ValueError, OSError):
            continue  # the parking stands; a note that cannot be written is not a reason to undo it


def park_stopped(root, run_id: str) -> dict:
    """Park pending targets of STOP sources; returns what was parked."""
    report = yields(root, run_id)
    stopped = [r for r in report["sources"] if r["verdict"] == "STOP" and r["pending"]]
    if not stopped:
        return {"parked": 0, "sources": []}
    path = Path(root) / "state" / "research-pool.sqlite3"
    now = datetime.now(timezone.utc).isoformat()
    parked = 0
    with closing(sqlite3.connect(path, timeout=30)) as db:
        db.execute("BEGIN IMMEDIATE")
        for row in stopped:
            reason = (f"{PARK_REASON}: source {row['source_id']} gave {row['with_ab']}/{row['finished']} "
                      "targets with an A or B route; reopen with marketops_pool_reopen if wanted")
            parked += db.execute(
                "UPDATE pool_targets SET status='hold', reason=?, run_id=?, updated_at=? "
                "WHERE pool_id=? AND source_id=? AND status='pending'",
                (reason[:500], run_id, now, row["pool_id"], row["source_id"])).rowcount
        db.commit()
    _note_yields(root, run_id, stopped)
    return {"parked": parked, "sources": [
        {k: r[k] for k in ("pool_id", "source_id", "finished", "with_ab", "ab_rate")} for r in stopped]}
