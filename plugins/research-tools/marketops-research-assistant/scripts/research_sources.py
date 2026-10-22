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

__all__ = ["PARK_REASON", "settings", "graded", "yields", "park_stopped", "finished_targets"]

PARK_REASON = "SOURCE_YIELD_STOP"
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
        elif n < gate["pilot"]:
            row["verdict"] = "PILOT"
            row["needs"] = gate["pilot"] - n
        elif row["ab_rate"] >= gate["promote"]:
            row["verdict"] = "PROMOTE"
        elif n >= 2 * gate["pilot"] or row["ab_rate"] < gate["stop"]:
            row["verdict"] = "STOP"
        else:
            row["verdict"] = "OPTIMIZE_ONCE"
    return {"gate": gate, "sources": sorted(table.values(), key=lambda r: (r["pool_id"], r["source_id"])),
            "unit": "pool targets finished by this Run; A+B = organisation has an A or B route"}


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
    return {"parked": parked, "sources": [
        {k: r[k] for k in ("pool_id", "source_id", "finished", "with_ab", "ab_rate")} for r in stopped]}
