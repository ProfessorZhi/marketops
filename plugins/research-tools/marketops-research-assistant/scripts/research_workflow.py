#!/usr/bin/env python3
"""Read-only structural workflow over the existing append-only Lite store.

Not a scheduler, a semantic fact verifier, a SOP engine or an outreach grant.
Briefs and dispositions are ordinary candidate records; there is no second DB.
Only record_goal_gaps() writes, and only factual metadata to the existing journal.
"""
from __future__ import annotations

import base64
import hashlib
import json
import sqlite3
from collections import Counter, defaultdict
from contextlib import closing
from pathlib import Path

import research_lite_store as store
from research_event_journal import append_event, append_note

MAX_ROWS = 20_000
DISPOSITIONS = frozenset({"RESEARCHED_CANDIDATE", "HOLD", "EXCLUDED", "DUPLICATE"})
SKILLS = {
    "intake": "marketops-intake", "discovery": "marketops-source-discovery",
    "entities": "marketops-venue-account", "acquisition": "marketops-evidence",
    "people": "marketops-person-research", "review": "marketops-research-acceptance",
    "handoff": "marketops-handoff",
}


def _read_connection(root: Path | str) -> sqlite3.Connection:
    # mode=ro must not silently create an empty store and report a successful Run.
    path = Path(root).expanduser().resolve() / "state" / "research-lite.sqlite3"
    if not path.is_file():
        raise ValueError("RESEARCH_STORE_NOT_FOUND")
    db = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=20)
    db.execute("BEGIN")
    return db


def _decode(row: tuple) -> dict:
    payload, refs = json.loads(row[5]), json.loads(row[6])
    if not isinstance(payload, dict) or not isinstance(refs, list):
        raise ValueError("STORED_RECORD_SCHEMA_INVALID")
    return dict(zip(("record_id", "stage", "kind", "status", "entity_key"), row[:5]),
                data=payload, artifact_ids=refs)


def _run(db: sqlite3.Connection, run_id: str) -> dict:
    store._id(run_id, "run_id")
    row = db.execute("SELECT scope,objective,rule_version FROM runs WHERE run_id=?",
                     (run_id,)).fetchone()
    if row is None:
        raise ValueError("UNKNOWN_RUN")
    return dict(zip(("scope", "objective", "rule_version"), row))


def _tip(rows: list[dict]) -> dict | None:
    """An explicit, unbroken supersedes chain; never silently choose a duplicate.

    New append-only corrections may supersede the previous record of the same
    kind/entity. Forks, cycles and references outside this group are conflicts.
    """
    if not rows or len({(r["kind"], r["entity_key"]) for r in rows}) != 1:
        return None
    index = {row["record_id"]: row for row in rows}
    parents = set()
    for row in rows:
        parent = row["data"].get("supersedes")
        if parent is None or parent == "":
            continue
        if not isinstance(parent, str) or parent not in index:
            return None
        parents.add(parent)
    tips = set(index) - parents
    if len(tips) != 1:
        return None
    tip = index[next(iter(tips))]
    current, seen = tip, set()
    while current:
        rid = current["record_id"]
        if rid in seen:
            return None
        seen.add(rid)
        parent = current["data"].get("supersedes")
        current = index.get(parent) if parent else None
    return tip if len(seen) == len(index) else None


def _text(value: object) -> bool:
    return (isinstance(value, str) and bool(value.strip())
            and value.strip().upper() not in {"UNKNOWN", "NONE", "N/A", "NULL", "TBD", "NOT_FOUND"})


def _supported(row: dict) -> bool:
    # This is intentionally only a structural check. Export independently reads
    # source bytes/hashes; an Agent must still check meaning, age and authority.
    return bool(row.get("artifact_ids"))


def assess_records(run_id: str, records: list[dict], *, limit: int = 25) -> dict:
    """Assess one immutable record snapshot, returning only a bounded work batch.

    A missing structured brief is UNKNOWN, never inferred from arbitrary prose.
    Evidence presence is NOT evidence validity or formal business approval.
    """
    store._id(run_id, "run_id")
    if type(limit) is not int or not 1 <= limit <= 100:
        raise ValueError("INVALID_WORKFLOW_LIMIT")
    if len(records) > MAX_ROWS:
        raise ValueError("LITE_WORKFLOW_TOO_MANY_RECORDS")
    # Stable across SQL query order; identical candidate states have one note ID.
    snapshot = hashlib.sha256(json.dumps(
        sorted(records, key=lambda r: r["record_id"]), ensure_ascii=False,
        sort_keys=True, separators=(",", ":")
    ).encode()).hexdigest()
    counts = Counter(row["kind"] for row in records)
    base = {
        "run_id": run_id, "snapshot_id": snapshot, "record_count": len(records),
        "counts": dict(sorted(counts.items())), "business_verified": False,
        "coverage_complete": False, "send_authorized": False,
        "assessment_scope": "STRUCTURAL_CANDIDATE_COMPLETENESS_NOT_FACT_VERIFICATION",
        "allowed_capabilities": ["read_local_state", "propose_candidates"],
        "network_authorization": "NOT_GRANTED_BY_THIS_RESULT",
    }
    briefs = [r for r in records if r["kind"] == "observation"
              and r["data"].get("record_type") == "research_brief"]
    brief_row = _tip(briefs)
    if brief_row is None:
        code = "BRIEF_REQUIRED" if not briefs else "BRIEF_CONFLICT"
        return {**base, "status": code, "stage": "intake", "recommended_skill": SKILLS["intake"],
                "targets": [], "target_ids": [], "gap_counts": {code: 1},
                "goal_satisfied": False, "blocked_target_count": 0,
                "completion_rule": "persist_one_explicit_structured_brief"}
    brief = brief_row["data"]
    kind, goal = brief.get("target_kind"), brief.get("goal")
    minimum, named = brief.get("minimum_targets"), brief.get("require_named_person")
    minimum_unit = brief.get("minimum_targets_unit")
    # A quantity cannot silently migrate from "venues to investigate" to
    # "organizations with named email routes". Aspirational quantities are not
    # hard minimums; old briefs must explicitly confirm their unit.
    if (not isinstance(kind, str) or kind not in {"venue", "organization"}
            or not isinstance(goal, str) or goal not in {"pool", "contacts"}
            or (minimum is not None and (type(minimum) is not int or not 1 <= minimum <= MAX_ROWS))
            or type(named) is not bool
            or (goal == "contacts" and kind != "organization")
            or (named and goal != "contacts")
            or (minimum is not None and minimum_unit != kind)
            or (minimum is None and minimum_unit not in {None, kind})):
        return {**base, "status": "BRIEF_INVALID", "stage": "intake",
                "recommended_skill": SKILLS["intake"], "targets": [], "target_ids": [],
                "gap_counts": {"BRIEF_INVALID": 1}, "goal_satisfied": False,
                "blocked_target_count": 0, "completion_rule": "correct_the_structured_brief"}

    grouped, associated, dispositions = defaultdict(list), defaultdict(list), defaultdict(list)
    related_groups, related_conflicts = defaultdict(list), set()
    for row in records:
        if row["kind"] == kind:
            grouped[row["entity_key"]].append(row)
        if row["kind"] in {"person", "role", "contact_point"}:
            related_groups[(row["kind"], row["entity_key"])].append(row)
        if (row["kind"] == "review" and row["data"].get("review_type") == "target_disposition"
                and row["data"].get("target_kind") == kind):
            key = row["data"].get("target_entity_key")
            if isinstance(key, str):
                dispositions[key].append(row)
    for versions in related_groups.values():
        active = _tip(versions)
        if active is None:
            for row in versions:
                org = row["data"].get("organization_id")
                if isinstance(org, str):
                    related_conflicts.add(org)
        else:
            org = active["data"].get("organization_id")
            if isinstance(org, str):
                associated[org].append(active)

    tasks, gaps, outcomes = [], Counter(), Counter()
    researched = blocked = 0
    for key in sorted(grouped):
        row = _tip(grouped[key])
        review_rows = dispositions.get(key, [])
        disposition = _tip(review_rows)
        missing, stage = [], "review"
        if row is None:
            missing.append("entity_version_conflict")
        if review_rows and disposition is None:
            missing.append("disposition_version_conflict")
        outcome = disposition["data"].get("disposition") if disposition else None
        if disposition is not None and (not isinstance(outcome, str) or outcome not in DISPOSITIONS
                                       or not _text(disposition["data"].get("reason"))):
            missing.append("invalid_disposition")
            outcome = None
        if not missing and outcome in {"HOLD", "EXCLUDED", "DUPLICATE"}:
            outcomes[outcome] += 1
            if outcome == "HOLD":
                blocked += 1
                gaps["target_hold"] += 1
            # A legitimate exclusion/duplicate is a resolved disposition, not
            # itself a research failure; it still does not count toward a quota.
            continue
        if row is not None and not _supported(row):
            missing.append("target_source_material")
            stage = "acquisition"
        if row is not None and goal == "contacts":
            related = associated.get(key, [])
            if key in related_conflicts:
                missing.append("related_entity_version_conflict")
            people = [r for r in related if r["kind"] == "person"
                      and _supported(r) and _text(r["data"].get("name"))]
            roles = [r for r in related if r["kind"] == "role" and _supported(r)
                     and (_text(r["data"].get("role_title"))
                          or _text(r["data"].get("role_function")))]
            contacts = [r for r in related if r["kind"] == "contact_point" and _supported(r)
                        and _text(r["data"].get("type", r["data"].get("channel")))
                        and _text(r["data"].get("value", r["data"].get("email")))]
            if named:
                people_keys = {r["entity_key"] for r in people}
                role_people = {r["data"].get("person_id") for r in roles
                               if isinstance(r["data"].get("person_id"), str)} & people_keys
                contact_people = {r["data"].get("person_id") for r in contacts
                                  if isinstance(r["data"].get("person_id"), str)} & people_keys
                if not people_keys:
                    missing.append("named_person_candidate_with_source")
                if not role_people:
                    missing.append("person_linked_role_candidate_with_source")
                if not (role_people & contact_people):
                    missing.append("same_person_linked_contact_candidate_with_source")
            else:
                if not roles:
                    missing.append("role_candidate_with_source")
                if not contacts:
                    missing.append("contact_candidate_with_source")
            if missing and stage != "acquisition" and not any("conflict" in m for m in missing):
                stage = "people"
        if outcome != "RESEARCHED_CANDIDATE":
            missing.append("target_disposition")
        if missing:
            # A disposition may not make missing facts disappear.
            gaps.update(missing)
            tasks.append({"entity_key": key, "record_id": row["record_id"] if row else "",
                          "stage": stage, "missing_facts": missing})
        else:
            researched += 1
            outcomes["RESEARCHED_CANDIDATE"] += 1
    shortfall = max(0, minimum - researched) if minimum is not None else 0
    if shortfall:
        gaps["minimum_researched_targets_shortfall"] = shortfall
    if not grouped:
        stage = "entities" if counts["source"] else "discovery"
        status = "MORE_RESEARCH_REQUIRED"
        gaps["target_pool_missing"] = minimum if minimum is not None else 1
    elif tasks:
        order = {"acquisition": 0, "people": 1, "review": 2}
        stage = min(tasks, key=lambda t: order.get(t["stage"], 3))["stage"]
        status = "MORE_RESEARCH_REQUIRED"
    elif blocked:
        stage, status = "handoff", "STOPPED_WITH_GAPS"
    elif shortfall:
        stage, status = "discovery", "MORE_RESEARCH_REQUIRED"
    elif not researched:
        # An all-excluded sample is a valid negative result, not useful positive
        # candidate delivery or proof that the market has no customers.
        gaps["no_researched_candidates"] = 1
        stage, status = "handoff", "STOPPED_WITH_GAPS"
    else:
        stage, status = "review", "READY_FOR_CANDIDATE_REVIEW"
    batch = [t for t in tasks if t["stage"] == stage][:limit]
    return {**base, "status": status, "stage": stage, "recommended_skill": SKILLS[stage],
            "brief_record_id": brief_row["record_id"], "target_kind": kind, "goal": goal,
            "minimum_targets": minimum, "minimum_targets_unit": minimum_unit,
            "require_named_person": named,
            "quantity_target_status": "EXPLICIT_MINIMUM" if minimum is not None else "UNSPECIFIED",
            "target_count": len(grouped), "researched_target_count": researched,
            "blocked_target_count": blocked, "pending_target_count": len(tasks),
            "disposition_counts": dict(sorted(outcomes.items())),
            "returned_targets": len(batch), "targets": batch,
            "target_ids": [t["entity_key"] for t in batch],
            "gap_counts": dict(sorted(gaps.items())),
            "goal_satisfied": not gaps,
            "completion_rule": "every_target_has_disposition_and_explicit_minimum_if_present",
            "warning": "Source presence and candidate disposition do not validate facts, SOP grade or market coverage."}


def next_step(root: Path | str, run_id: str, limit: int = 25) -> dict:
    with closing(_read_connection(root)) as db:
        _run(db, run_id)
        rows = db.execute(
            "SELECT record_id,stage,kind,status,entity_key,payload_json,artifact_ids_json "
            "FROM records WHERE run_id=? ORDER BY record_id LIMIT ?", (run_id, MAX_ROWS + 1)
        ).fetchall()
    return assess_records(run_id, [_decode(r) for r in rows], limit=limit)


def page(root: Path | str, run_id: str, limit: int = 100, cursor: str = "",
         kind: str = "", entity_key: str = "") -> dict:
    """Stable rowid high-water pagination; later appends never enter an old page set."""
    store._id(run_id, "run_id")
    if type(limit) is not int or not 1 <= limit <= 500:
        raise ValueError("INVALID_PAGE_LIMIT")
    if not isinstance(kind, str) or (kind and kind not in store.KINDS):
        raise ValueError("INVALID_PAGE_KIND")
    if not isinstance(entity_key, str):
        raise ValueError("INVALID_PAGE_ENTITY_KEY")
    if entity_key:
        store._id(entity_key, "entity_key")
    if not isinstance(cursor, str):
        raise ValueError("INVALID_OR_MISMATCHED_PAGE_CURSOR")
    binding = [run_id, kind, entity_key]
    with closing(_read_connection(root)) as db:
        run = _run(db, run_id)
        if cursor:
            try:
                if len(cursor) > 2048:
                    raise ValueError()
                token = json.loads(base64.b64decode(cursor.encode(), altchars=b"-_", validate=True))
                if (not isinstance(token, dict) or type(token.get("v")) is not int or token.get("v") != 1
                        or token.get("binding") != binding
                        or type(token.get("high")) is not int or type(token.get("after")) is not int
                        or not 0 <= token["after"] <= token["high"] <= 2**63 - 1):
                    raise ValueError()
                high, after = token["high"], token["after"]
            except (ValueError, KeyError, TypeError, UnicodeError):
                raise ValueError("INVALID_OR_MISMATCHED_PAGE_CURSOR") from None
        else:
            high = db.execute("SELECT COALESCE(MAX(rowid),0) FROM records WHERE run_id=?",
                              (run_id,)).fetchone()[0]
            after = 0
        where, params = "run_id=? AND rowid<=?", [run_id, high]
        if kind:
            where += " AND kind=?"
            params.append(kind)
        if entity_key:
            where += " AND entity_key=?"
            params.append(entity_key)
        count = db.execute("SELECT COUNT(*) FROM records WHERE " + where, params).fetchone()[0]
        rows = db.execute(
            "SELECT rowid,record_id,stage,kind,status,entity_key,payload_json,artifact_ids_json "
            "FROM records WHERE " + where + " AND rowid>? ORDER BY rowid LIMIT ?",
            [*params, after, limit + 1]).fetchall()
        artifact_count = db.execute("SELECT COUNT(*) FROM artifacts WHERE run_id=?", (run_id,)).fetchone()[0]
    visible, more = rows[:limit], len(rows) > limit
    next_cursor = ""
    if more:
        next_cursor = base64.urlsafe_b64encode(json.dumps(
            {"v": 1, "binding": binding, "high": high, "after": visible[-1][0]},
            separators=(",", ":")).encode()).decode()
    return {"run_id": run_id, **run, "record_count": count, "returned": len(visible),
            "artifact_count": artifact_count, "artifact_count_scope": "current_run_not_record_page_snapshot",
            "records": [_decode(r[1:]) for r in visible], "has_more": more,
            "next_cursor": next_cursor, "snapshot_high_water": high}


def record_goal_gaps(root: Path | str, run_id: str, assessment: dict) -> dict:
    """Persist sanitized aggregate observations at delivery, not an inferred root cause."""
    if assessment.get("goal_satisfied"):
        return {"status": "NO_STRUCTURAL_GAP_OBSERVED_NOT_BUSINESS_VERIFIED"}
    # The counters are our closed mechanism codes, never raw brief/person/source text.
    observed = json.dumps({"status": assessment["status"], "gaps": assessment["gap_counts"]},
                          sort_keys=True, separators=(",", ":"))
    # All counters remain in the delivery manifest; a bounded journal note is enough.
    if len(observed) > 550:
        observed = json.dumps({"status": assessment["status"],
                               "gap_types": len(assessment["gap_counts"]),
                               "detail": "delivery_manifest_goal_assessment"}, sort_keys=True)
    note_id = "goal-" + assessment["snapshot_id"][:48]
    event = append_event(root, run_id=run_id, operation_id=note_id,
                         stage="review", kind="review", status="hold",
                         provider="local", reason_code="business_goal_gap")
    note = append_note(root, run_id=run_id, note_id=note_id, stage="review",
                       category="other", problem="结构化研究目标尚未满足，或目标合同缺失。",
                       action_taken="对交付所用候选快照执行确定性结构检查；不推断根因。",
                       observed_result=observed, hypothesis="", evidence_ref=note_id)
    return {"status": "GOAL_GAP_RECORDED", "event": event, "note": note}
