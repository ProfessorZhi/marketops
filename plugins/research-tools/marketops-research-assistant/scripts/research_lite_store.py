#!/usr/bin/env python3
"""Single-user, append-only local research *staging* store. Never the MarketOps production DB.

Source material, model suggestions and business verification are separate objects.
No method grants contact-route readiness, sends messages or changes SOP.
"""
from __future__ import annotations

import base64
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
#: Material kinds a caller may store. `image` holds fetched picture bytes so a
#: visual judgment (Jev Box) can be bound to a *saved* artifact like any other
#: evidence; its bytes are validated against the real file signatures below, so
#: text relabelled as a picture is refused rather than stored.
MATERIAL_KINDS = frozenset({"provider_extracted", "provider_html", "rendered_html", "raw_response",
                            "decision_response", "image"})
#: File signatures the SDK itself recognises (see `typesafe_sdk._core.assets`).
IMAGE_SIGNATURES = (b"\x89PNG\r\n\x1a\n", b"\xff\xd8\xff")
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
        CREATE TABLE IF NOT EXISTS evidence_links (
            link_id TEXT PRIMARY KEY, run_id TEXT NOT NULL REFERENCES runs(run_id),
            record_id TEXT NOT NULL, artifact_id TEXT NOT NULL,
            content_sha256 TEXT NOT NULL, span_sha256 TEXT NOT NULL,
            locator_kind TEXT NOT NULL, locator TEXT NOT NULL,
            material_kind TEXT NOT NULL, field TEXT NOT NULL,
            created_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS evidence_envelopes (
            evidence_id TEXT PRIMARY KEY, run_id TEXT NOT NULL REFERENCES runs(run_id),
            operation_id TEXT NOT NULL, artifact_id TEXT NOT NULL,
            ledger_id TEXT, provider TEXT NOT NULL, capability_id TEXT NOT NULL,
            source_url TEXT, retrieved_at TEXT NOT NULL, content_sha256 TEXT NOT NULL,
            raw_artifact_ref TEXT NOT NULL, derived_refs_json TEXT NOT NULL,
            extraction_method TEXT NOT NULL, verified_state TEXT NOT NULL,
            candidate_only INTEGER NOT NULL, egress_decision_ref TEXT,
            caveats_json TEXT NOT NULL, created_at TEXT NOT NULL);
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


def list_runs(root: Path | str | None, *, limit: int = 10, cursor: str = "",
              scope: str = "") -> dict:
    """Discover resumable tasks without opening a writable database or creating one."""
    if type(limit) is not int or not 1 <= limit <= 50:
        raise ValueError("RUN_LIST_LIMIT_INVALID")
    if not isinstance(scope, str) or len(scope) > 300:
        raise ValueError("RUN_LIST_SCOPE_INVALID")
    if cursor:
        _id(cursor, "cursor")
    base = _root(root, create=False)
    path = (base / "state" / "research-lite.sqlite3").resolve()
    if not path.is_relative_to(base):
        raise ValueError("RUN_STORE_OUTSIDE_DATA_ROOT")
    if not path.is_file():
        if cursor:
            raise ValueError("RUN_CURSOR_UNKNOWN")
        return {"runs": [], "next_cursor": None, "limit": limit, "mutates_nothing": True}
    clauses, arguments = [], []
    with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=20)) as db:
        db.execute("BEGIN")
        if scope:
            clauses.append("r.scope=?")
            arguments.append(scope)
        if cursor:
            previous = db.execute("SELECT created_at,scope FROM runs WHERE run_id=?", (cursor,)).fetchone()
            if previous is None or (scope and previous[1] != scope):
                raise ValueError("RUN_CURSOR_UNKNOWN")
            clauses.append("(r.created_at<? OR (r.created_at=? AND r.run_id<?))")
            arguments.extend((previous[0], previous[0], cursor))
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        rows = db.execute(
            "SELECT r.run_id,r.scope,r.rule_version,r.objective,r.created_at,"
            "(SELECT COUNT(*) FROM records c WHERE c.run_id=r.run_id) "
            "FROM runs r" + where + " ORDER BY r.created_at DESC,r.run_id DESC LIMIT ?",
            (*arguments, limit + 1),
        ).fetchall()
    names = ("run_id", "scope", "rule_version", "objective", "created_at", "record_count")
    return {"runs": [dict(zip(names, row)) for row in rows[:limit]],
            "next_cursor": rows[limit - 1][0] if len(rows) > limit else None,
            "limit": limit, "mutates_nothing": True}


def list_materials(root: Path | str | None, run_id: str, *, limit: int = 20,
                   cursor: str = "") -> dict:
    """Read-only metadata discovery; a new session need not remember artifact IDs."""
    _id(run_id, "run_id")
    if type(limit) is not int or not 1 <= limit <= 50:
        raise ValueError("MATERIAL_LIST_LIMIT_INVALID")
    base = _root(root, create=False)
    path = (base / "state" / "research-lite.sqlite3").resolve()
    if not path.is_relative_to(base) or not path.is_file():
        raise ValueError("MATERIAL_STORE_UNAVAILABLE")
    with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=20)) as db:
        db.execute("BEGIN")
        if db.execute("SELECT 1 FROM runs WHERE run_id=?", (run_id,)).fetchone() is None:
            raise ValueError("UNKNOWN_RUN")
        if cursor:
            try:
                if not isinstance(cursor, str) or len(cursor) > 2048:
                    raise ValueError()
                token = json.loads(base64.b64decode(cursor.encode(), altchars=b"-_", validate=True))
                high, after = token["high"], token["after"]
                if (token.get("kind") != "materials" or token.get("run_id") != run_id
                        or type(high) is not int or type(after) is not int
                        or not 0 <= after <= high <= 2**63 - 1):
                    raise ValueError()
            except (ValueError, KeyError, TypeError, UnicodeError):
                raise ValueError("MATERIAL_CURSOR_INVALID_OR_MISMATCHED") from None
        else:
            high = db.execute("SELECT COALESCE(MAX(rowid),0) FROM artifacts WHERE run_id=?",
                              (run_id,)).fetchone()[0]
            after = 0
        total = db.execute("SELECT COUNT(*) FROM artifacts WHERE run_id=? AND rowid<=?",
                           (run_id, high)).fetchone()[0]
        rows = db.execute("SELECT rowid,artifact_id,provider,origin_url,material_kind,content_sha256,acquired_at "
                          "FROM artifacts WHERE run_id=? AND rowid>? AND rowid<=? ORDER BY rowid LIMIT ?",
                          (run_id, after, high, limit + 1)).fetchall()
    visible = rows[:limit]
    next_cursor = None
    if len(rows) > limit:
        next_cursor = base64.urlsafe_b64encode(json.dumps(
            {"kind": "materials", "run_id": run_id, "high": high, "after": visible[-1][0]},
            separators=(",", ":")).encode()).decode()
    fields = ("artifact_id", "provider", "source_url", "material_kind", "content_sha256", "acquired_at")
    return {"run_id": run_id, "kind": "artifact", "artifact_count": total,
            "artifacts": [dict(zip(fields, row[1:])) for row in visible],
            "next_cursor": next_cursor, "mutates_nothing": True,
            "read_with": "marketops_material_excerpt", "facts_verified": False}


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
    if material_kind not in MATERIAL_KINDS:
        raise ValueError("Unknown material kind")
    if material_kind == "provider_html":
        from research_provider_html import canonical_url, validate_content
        canonical_url(origin_url)
        validate_content(content.decode("utf-8"))
    # A picture is only a picture if its leading bytes say so. Without this check a
    # text page stored as `image` would later be handed to a visual judgment as if
    # it were a fetched screenshot.
    if material_kind == "image" and not content.startswith(IMAGE_SIGNATURES):
        raise ValueError("IMAGE_ARTIFACT_REQUIRES_IMAGE_BYTES")
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


def _span_caveat(kind: str, payload: dict, artifact_ids, linked) -> str:
    """Say it at write time: material without a linked quote never reaches delivery.

    The delivery gate reports a "spanless" record only after the run has ended,
    which is exactly when the Agent can no longer fix it. Staging such a record
    stays legal — it simply does not reach the business tables — so this is a
    caveat, never a refusal.
    """
    if not artifact_ids or linked:
        return ""
    if kind not in {"venue", "organization", "relationship", "person", "role",
                    "contact_point", "contact_route"}:
        return ""
    claims = (payload or {}).get("claims")
    if not claims:
        return ("SPAN_NOT_RECORDED: no facts.claims, so no quote is linked to "
                + ", ".join(str(a) for a in artifact_ids[:3])
                + "; add facts.claims=[{field, artifact_id, quote}] with a 12-600 character "
                  "quote taken from that artifact, or this record stays out of the "
                  "business tables")
    return ("SPAN_NOT_RECORDED: " + str(len(claims)) + " claim(s) supplied but none linked; "
            "each claim needs artifact_id from artifact_ids, a field matching "
            "^[a-z][a-z0-9_]{0,39}$ and a 12-600 character quote found in that artifact")


def put_candidate(root: Path | str | None, run_id: str, record_id: str, stage: str,
                  kind: str, entity_key: str, payload: dict,
                  artifact_ids: list[str] | None = None) -> dict:
    """Staging only. Single-row and bounded batch share the exact validation."""
    store_root = _root(root)
    with closing(_connection(store_root)) as db:
        db.execute("BEGIN IMMEDIATE")
        result = _insert_candidate(db, run_id, record_id, stage, kind, entity_key,
                                   payload, artifact_ids)
        from research_evidence_store import link_candidate_claims
        linked = link_candidate_claims(store_root, db, run_id, record_id, payload, artifact_ids)
        db.commit()
    caveat = _span_caveat(kind, payload, artifact_ids, linked)
    return {**result, "caveats": [caveat]} if caveat else result


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
    results, caveats = [], []
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
            from research_evidence_store import link_candidate_claims
            linked = link_candidate_claims(store_root, db, run_id, item["record_id"],
                                           item["facts"], item.get("artifact_ids"))
            caveat = _span_caveat(item["kind"], item["facts"], item.get("artifact_ids"), linked)
            if caveat:
                caveats.append(item["record_id"] + " " + caveat)
        db.commit()
    report = {"status": "CANDIDATE_BATCH_STAGED_NOT_VERIFIED",
              "requested": len(items),
              "created": sum(x["status"] != "IDEMPOTENT" for x in results),
              "idempotent": sum(x["status"] == "IDEMPOTENT" for x in results),
              "record_ids": [x["record_id"] for x in results]}
    if caveats:
        report["caveats"] = caveats[:8]
    return report

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


# --------------------------------------------------------------------- readback
def read_artifact(root: Path | str | None, run_id: str, artifact_id: str) -> dict:
    """Saved bytes, read back from disk and hash-verified against the recorded row.

    An artifact is only usable as evidence if the bytes on disk still hash to the
    value written at save time. Reading without that check would let a later edit
    pass silently as the original material.
    """
    store_root = _root(root)
    _id(run_id, "run_id"); _id(artifact_id, "artifact_id")
    with closing(_connection(store_root)) as db:
        row = db.execute(
            "SELECT material_kind,origin_url,content_sha256,relative_path,acquired_at,provider "
            "FROM artifacts WHERE run_id=? AND artifact_id=?", (run_id, artifact_id)).fetchone()
    if row is None:
        raise ValueError("ARTIFACT_NOT_IN_RUN")
    path = (store_root / row[3]).resolve()
    if not path.is_relative_to((store_root / "artifacts").resolve()):
        raise ValueError("ARTIFACT_PATH_OUTSIDE_ROOT")
    if not path.is_file():
        raise ValueError("ARTIFACT_MISSING")
    data = path.read_bytes()
    if hashlib.sha256(data).hexdigest() != row[2]:
        raise ValueError("ARTIFACT_READBACK_HASH_MISMATCH")
    return {"artifact_id": artifact_id, "run_id": run_id, "content": data,
            "material_kind": row[0], "origin_url": row[1], "content_sha256": row[2],
            "provider": row[5], "acquired_at": row[4]}


def artifact_sha256(root: Path | str | None, run_id: str, artifact_id: str) -> str:
    return read_artifact(root, run_id, artifact_id)["content_sha256"]


def artifact_kind(root: Path | str | None, run_id: str, artifact_id: str) -> str:
    return read_artifact(root, run_id, artifact_id)["material_kind"]


def is_image_artifact(root: Path | str | None, run_id: str, artifact_id: str) -> bool:
    return artifact_kind(root, run_id, artifact_id) == "image"
