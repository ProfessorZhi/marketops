#!/usr/bin/env python3
"""Single-user, append-only local research *staging* store. Never the MarketOps production DB.

Source material, model suggestions and business verification are separate objects.
No method grants contact-route readiness, sends messages or changes SOP.
"""
from __future__ import annotations

import csv
import hashlib
import json
import os
import re
import sqlite3
import tempfile
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

from research_event_journal import _root

ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,95}$")
KINDS = frozenset({"source", "venue", "organization", "relationship", "person",
                   "role", "contact_point", "contact_route", "observation", "review"})
STAGES = frozenset({"discovery", "acquisition", "entities", "qualification", "people",
                    "routes", "review", "export"})
MAX_RECORD = 16_384
NO_EVIDENCE = "evidence_missing_not_verified"


def _id(value: str, name: str) -> str:
    if not isinstance(value, str) or not ID.fullmatch(value):
        raise ValueError(f"{name}: invalid opaque ID")
    return value


def _connection(root: Path) -> sqlite3.Connection:
    folder = root / "state"
    folder.mkdir(parents=True, exist_ok=True, mode=0o700)
    db = sqlite3.connect(folder / "research-lite.sqlite3", timeout=20)
    db.execute("PRAGMA journal_mode=WAL")
    db.execute("PRAGMA foreign_keys=ON")
    db.executescript("""
        CREATE TABLE IF NOT EXISTS runs (
            run_id TEXT PRIMARY KEY, scope TEXT NOT NULL,
            objective TEXT NOT NULL, rule_version TEXT NOT NULL,
            created_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS artifacts (
            artifact_id TEXT PRIMARY KEY, run_id TEXT NOT NULL REFERENCES runs(run_id),
            provider TEXT NOT NULL, origin_url TEXT NOT NULL,
            material_kind TEXT NOT NULL, content_sha256 TEXT NOT NULL,
            relative_path TEXT NOT NULL, acquired_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS records (
            record_id TEXT PRIMARY KEY, run_id TEXT NOT NULL REFERENCES runs(run_id),
            stage TEXT NOT NULL, kind TEXT NOT NULL, status TEXT NOT NULL,
            entity_key TEXT NOT NULL, payload_json TEXT NOT NULL,
            artifact_ids_json TEXT NOT NULL, created_at TEXT NOT NULL);
    """)
    return db


def start_run(root: Path | str | None, run_id: str, scope: str, objective: str,
              rule_version: str) -> dict:
    run_id = _id(run_id, "run_id")
    if not all(isinstance(x, str) and 1 <= len(x.strip()) <= 300 for x in (scope, objective, rule_version)):
        raise ValueError("run fields must be short nonempty values")
    store_root = _root(root)
    with closing(_connection(store_root)) as db:
        db.execute("BEGIN IMMEDIATE")
        prior = db.execute("SELECT scope,objective,rule_version FROM runs WHERE run_id=?", (run_id,)).fetchone()
        if prior and prior != (scope, objective, rule_version):
            raise ValueError("RUN_ID_COLLISION: a previous run has different scope or policy")
        if prior:
            return {"run_id": run_id, "status": "EXISTS_NO_CHANGE"}
        db.execute("INSERT INTO runs VALUES (?,?,?,?,?)",
                   (run_id, scope, objective, rule_version, datetime.now(timezone.utc).isoformat()))
        db.commit()
    return {"run_id": run_id, "status": "RUN_CREATED"}


def _safe_name(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def save_artifact(root: Path | str | None, run_id: str, artifact_id: str,
                  provider: str, origin_url: str, content: bytes,
                  material_kind: str) -> dict:
    store_root = _root(root)
    _id(run_id, "run_id"); _id(artifact_id, "artifact_id")
    # Reject invalid Run BEFORE writing any customer material bytes.
    list_run(store_root, run_id, limit=1)
    if not isinstance(content, bytes) or not content or len(content) > 4_000_000:
        raise ValueError("Nonempty material bytes required (<=4 MB)")
    if material_kind not in {"provider_extracted", "rendered_html", "raw_response", "decision_response"}:
        raise ValueError("Unknown material kind")
    if len(origin_url) > 2048 or len(provider) > 60 or not provider:
        raise ValueError("Invalid material metadata")
    digest = hashlib.sha256(content).hexdigest()
    # Check key ownership BEFORE touching the per-run artifact directory. The
    # transaction below repeats this check to defend against concurrent writers.
    with closing(_connection(store_root)) as db:
        previous = db.execute("SELECT run_id,content_sha256 FROM artifacts WHERE artifact_id=?",
                              (artifact_id,)).fetchone()
        if previous and (previous[0] != run_id or previous[1] != digest):
            raise ValueError("ARTIFACT_ID_COLLISION_OR_CROSS_RUN")
    relative = Path("artifacts") / run_id / (_safe_name(artifact_id) + ".bin")
    target = store_root / relative
    target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    if target.exists():
        if hashlib.sha256(target.read_bytes()).hexdigest() != digest:
            raise ValueError("ARTIFACT_ID_COLLISION: source bytes changed")
    else:
        with tempfile.NamedTemporaryFile(dir=target.parent, prefix=".tmp-", delete=False) as fh:
            temp_name = fh.name
            fh.write(content)
            fh.flush()
            os.fsync(fh.fileno())
        try:
            if target.exists():
                raise ValueError("ARTIFACT_RACE")
            os.replace(temp_name, target)
        finally:
            if os.path.exists(temp_name):
                os.unlink(temp_name)
    with closing(_connection(store_root)) as db:
        db.execute("BEGIN IMMEDIATE")
        existing = db.execute("SELECT run_id,content_sha256 FROM artifacts WHERE artifact_id=?", (artifact_id,)).fetchone()
        if existing and (existing[0] != run_id or existing[1] != digest):
            raise ValueError("ARTIFACT_ID_COLLISION_OR_CROSS_RUN")
        if not existing:
            db.execute("INSERT INTO artifacts VALUES (?,?,?,?,?,?,?,?)",
                       (artifact_id, run_id, provider, origin_url, material_kind, digest,
                        str(relative), datetime.now(timezone.utc).isoformat()))
        db.commit()
    return {"artifact_id": artifact_id, "sha256": digest, "material_kind": material_kind,
            "storage_status": "SAVED_NOT_BUSINESS_VERIFIED"}


def _insert_candidate(db: sqlite3.Connection, run_id: str, record_id: str,
                      stage: str, kind: str, entity_key: str, payload: dict,
                      artifact_ids: list[str] | None = None) -> dict:
    """One candidate insertion on the caller's transaction; no second DB."""
    _id(run_id, "run_id"); _id(record_id, "record_id"); _id(entity_key, "entity_key")
    if stage not in STAGES or kind not in KINDS:
        raise ValueError("Unknown stage or object category")
    if not isinstance(payload, dict):
        raise ValueError("Record payload must be an object")
    if any(k in payload for k in ("verified", "ready", "send_authorized", "approved", "reviewed_by")):
        raise ValueError("Promotion and authorization fields cannot be written by this staging tool")
    body = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    if len(body.encode("utf-8")) > MAX_RECORD:
        raise ValueError("Record too large; store source material as an artifact reference")
    ids = artifact_ids if artifact_ids is not None else []
    if not isinstance(ids, list) or len(ids) > 12 or any(not isinstance(a, str) for a in ids):
        raise ValueError("artifact_ids must be a bounded list")
    if len(ids) != len(set(ids)):
        raise ValueError("Duplicate artifact reference")
    for aid in ids:
        _id(aid, "artifact_id")
        if db.execute("SELECT 1 FROM artifacts WHERE artifact_id=? AND run_id=?",
                      (aid, run_id)).fetchone() is None:
            raise ValueError("ARTIFACT_NOT_IN_RUN")
    prior = db.execute("SELECT run_id,stage,kind,entity_key,payload_json,artifact_ids_json "
                       "FROM records WHERE record_id=?", (record_id,)).fetchone()
    refs = json.dumps(ids, sort_keys=True)
    if prior:
        if prior != (run_id, stage, kind, entity_key, body, refs):
            raise ValueError("RECORD_ID_COLLISION_OR_CROSS_RUN: append a new ID")
        return {"record_id": record_id, "status": "IDEMPOTENT"}
    status = "candidate_with_material" if ids else "candidate_unverified"
    db.execute("INSERT INTO records VALUES (?,?,?,?,?,?,?,?,?)",
               (record_id, run_id, stage, kind, status, entity_key,
                body, refs, datetime.now(timezone.utc).isoformat()))
    return {"record_id": record_id, "status": status, "artifact_ids": ids}


def put_candidate(root: Path | str | None, run_id: str, record_id: str, stage: str,
                  kind: str, entity_key: str, payload: dict,
                  artifact_ids: list[str] | None = None) -> dict:
    """Staging only. Single-row and bounded batch share the exact validation."""
    store_root = _root(root)
    with closing(_connection(store_root)) as db:
        db.execute("BEGIN IMMEDIATE")
        result = _insert_candidate(db, run_id, record_id, stage, kind, entity_key,
                                   payload, artifact_ids)
        db.commit()
    return result


def put_candidates_batch(root: Path | str | None, run_id: str,
                         items: list[dict]) -> dict:
    """One atomic, idempotent batch, returning compact IDs rather than facts.

    Bounded to avoid dumping large documents into a single tool call. On any
    invalid item nothing from this batch is committed; existing data is intact.
    """
    _id(run_id, "run_id")
    if type(items) is not list or not 1 <= len(items) <= 20:
        raise ValueError("BATCH_SIZE_1_TO_20_REQUIRED")
    if any(type(x) is not dict for x in items):
        raise ValueError("BATCH_ITEM_INVALID")
    if len(json.dumps(items, ensure_ascii=False).encode("utf-8")) > 65_536:
        raise ValueError("BATCH_PAYLOAD_TOO_LARGE")
    store_root = _root(root)
    results = []
    with closing(_connection(store_root)) as db:
        db.execute("BEGIN IMMEDIATE")
        if db.execute("SELECT 1 FROM runs WHERE run_id=?", (run_id,)).fetchone() is None:
            raise ValueError("Unknown Run")
        for item in items:
            if set(item) - {"record_id", "stage", "kind", "entity_key", "facts", "artifact_ids"}:
                raise ValueError("BATCH_ITEM_UNRECOGNIZED_FIELD")
            for field in ("record_id", "stage", "kind", "entity_key", "facts"):
                if field not in item:
                    raise ValueError("BATCH_ITEM_MISSING_FIELD")
            results.append(_insert_candidate(
                db, run_id, item["record_id"], item["stage"], item["kind"],
                item["entity_key"], item["facts"], item.get("artifact_ids")))
        db.commit()
    return {"status": "CANDIDATE_BATCH_STAGED_NOT_VERIFIED",
            "requested": len(items),
            "created": sum(x["status"] != "IDEMPOTENT" for x in results),
            "idempotent": sum(x["status"] == "IDEMPOTENT" for x in results),
            "record_ids": [x["record_id"] for x in results]}

def list_run(root: Path | str | None, run_id: str, limit: int = 300) -> dict:
    _id(run_id, "run_id")
    if not 1 <= limit <= 500:
        raise ValueError("Invalid limit")
    with closing(_connection(_root(root))) as db:
        run = db.execute("SELECT scope, objective, rule_version FROM runs WHERE run_id=?",
                         (run_id,)).fetchone()
        if run is None:
            raise ValueError("Unknown Run")
        rows = db.execute("SELECT record_id,stage,kind,status,entity_key,payload_json,artifact_ids_json "
                          "FROM records WHERE run_id=? ORDER BY created_at,record_id LIMIT ?",
                          (run_id, limit)).fetchall()
        count = db.execute("SELECT COUNT(*) FROM records WHERE run_id=?", (run_id,)).fetchone()[0]
        artifact_count = db.execute("SELECT COUNT(*) FROM artifacts WHERE run_id=?", (run_id,)).fetchone()[0]
    return {"run_id": run_id, "scope": run[0], "objective": run[1], "rule_version": run[2],
            "record_count": count, "returned": len(rows), "artifact_count": artifact_count,
            "records": [{"record_id": row[0], "stage": row[1], "kind": row[2],
                         "status": row[3], "entity_key": row[4],
                         "data": json.loads(row[5]), "artifact_ids": json.loads(row[6])}
                        for row in rows]}


def export_csv(root: Path | str | None, run_id: str, output: Path) -> dict:
    """Never re-label candidate output as a formally reviewed, market-complete result."""
    snapshot = list_run(root, run_id, limit=500)
    if snapshot["record_count"] > snapshot["returned"]:
        raise ValueError("EXPORT_INCOMPLETE: too many rows, request paginated export")
    output = output.expanduser().resolve()
    if output.exists():
        raise FileExistsError("Output exists; historical exports are immutable")
    customer_root = _root(root)
    if not output.is_relative_to(customer_root / "exports"):
        raise ValueError("EXPORT_OUTSIDE_CUSTOMER_APPROVED_DIRECTORY")
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("x", encoding="utf-8-sig", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(["record_id", "run_id", "stage", "kind", "status", "entity_key",
                         "artifact_ids_json", "candidate_data_json"])
        for row in snapshot["records"]:
            text = json.dumps(row["data"], ensure_ascii=False)
            # Escape spreadsheet-formula-looking external text, including JSON strings.
            safe = "'" + text if text.lstrip().startswith(("=", "+", "-", "@")) else text
            writer.writerow([row["record_id"], run_id, row["stage"], row["kind"],
                             row["status"], row["entity_key"],
                             json.dumps(row["artifact_ids"]), safe])
    return {"status": "CANDIDATE_CSV_EXPORTED_NOT_FORMALLY_VERIFIED",
            "rows": snapshot["record_count"], "path": str(output)}
