#!/usr/bin/env python3
"""Candidate evidence provenance: which span of which artifact a candidate came from.

The staging store already records that an artifact was *attached* to a record. That
is a weaker claim than it looks: an attachment says a file is associated with a
candidate, not that the candidate was read from it, and not from which part. The
audit's F-6 finding is the same shape one level down — a judgment whose input was
never bound to stored material cannot be traced back to it at all.

This module adds the missing layer, and it is deliberately separate from the store:
the store owns bytes and their hashes, this owns the *claims about* those bytes.

* `link_evidence` records one (record, artifact, span) triple. Append-only,
  idempotent, and it refuses a link whose identifiers are not in the same run.
* `evidence_envelope` builds the contract's §3.5 envelope; `record_envelope`
  persists it so an export republishes what was recorded rather than re-deriving a
  shape that would have lost its ledger link and its originating operation.
* `stored_envelopes` reads them back.

Nothing here promotes a candidate, invents a fact or asserts a business state. An
envelope with no `ledger_id` is reported as such: the contract makes it ineligible
for export, and filling that field with a placeholder would hide the gap.
"""
from __future__ import annotations

import hashlib
import json
import re
from contextlib import closing, nullcontext
from datetime import datetime, timezone
from pathlib import Path

from research_event_journal import _root
from research_lite_store import _connection, _id, read_artifact

#: The exact field set of an evidence envelope, frozen by the Wave-1 shared
#: interface contract (§3.5). Kept as a tuple here so a test can assert this module
#: produces that set and nothing else — a field added on one side only would
#: otherwise stay invisible until it reached an export.
ENVELOPE_FIELDS = (
    "evidence_id", "run_id", "operation_id", "ledger_id", "provider",
    "capability_id", "source_url", "retrieved_at", "content_sha256",
    "raw_artifact_ref", "derived_refs", "extraction_method", "verified_state",
    "candidate_only", "egress_decision_ref", "caveats",
)
VERIFIED_STATES = frozenset({"UNVERIFIED", "CORROBORATED", "CONTRADICTED", "HUMAN_REVIEWED"})


# -------------------------------------------------------- candidate provenance
def link_evidence(root: Path | str | None, run_id: str, record_id: str, artifact_id: str, *,
                  content_sha256: str, span_sha256: str, locator_kind: str,
                  locator: str = "", material_kind: str = "", field: str = "", _db=None) -> dict:
    """Record *which span* of *which artifact* a candidate was built from.

    A record's `artifact_ids` says an artifact was attached. This says what part of
    it was read, so a mis-attribution can be audited per link instead of being
    inferred from the attachment alone. Append-only and idempotent.
    """
    store_root = _root(root)
    _id(run_id, "run_id"); _id(record_id, "record_id"); _id(artifact_id, "artifact_id")
    for value in (content_sha256, span_sha256):
        if not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{64}", value):
            raise ValueError("EVIDENCE_LINK_HASH_INVALID")
    if (not isinstance(locator_kind, str) or not 1 <= len(locator_kind) <= 20
            or not isinstance(locator, str) or len(locator) > 300
            or not isinstance(field, str) or len(field) > 40
            or not isinstance(material_kind, str) or len(material_kind) > 40):
        raise ValueError("EVIDENCE_LINK_FIELD_INVALID")
    link_id = "lnk-" + hashlib.sha256("|".join(
        (run_id, record_id, artifact_id, span_sha256, field)).encode("utf-8")).hexdigest()[:32]
    values = (content_sha256, span_sha256, locator_kind, locator, material_kind, field)
    with (closing(_connection(store_root)) if _db is None else nullcontext(_db)) as db:
        if _db is None:
            db.execute("BEGIN IMMEDIATE")
        if db.execute("SELECT 1 FROM records WHERE run_id=? AND record_id=?",
                      (run_id, record_id)).fetchone() is None:
            raise ValueError("EVIDENCE_LINK_RECORD_NOT_IN_RUN")
        if db.execute("SELECT 1 FROM artifacts WHERE run_id=? AND artifact_id=?",
                      (run_id, artifact_id)).fetchone() is None:
            raise ValueError("ARTIFACT_NOT_IN_RUN")
        prior = db.execute("SELECT content_sha256,span_sha256,locator_kind,locator,material_kind,field "
                           "FROM evidence_links WHERE link_id=?", (link_id,)).fetchone()
        if prior is not None:
            if _db is None:
                db.commit()
            if tuple(prior) != values:
                raise ValueError("EVIDENCE_LINK_COLLISION")
            return {"link_id": link_id, "status": "IDEMPOTENT"}
        db.execute("INSERT INTO evidence_links VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                   (link_id, run_id, record_id, artifact_id, *values,
                    datetime.now(timezone.utc).isoformat()))
        if _db is None:
            db.commit()
    return {"link_id": link_id, "status": "LINKED"}


def link_candidate_claims(root, db, run_id, record_id, payload, artifact_ids):
    """Link actual matching spans in the candidate writer's transaction.

    Invalid/missing quotes remain ordinary candidate gaps for the evidence
    audit. A corrupted source refuses the transaction; a partial batch cannot
    leave links for records that were rolled back. Normalization is exactly the
    audit's versioned text representation, not a claim of raw-byte offsets.
    """
    from research_evidence import KEY, _norm, _text
    claims = payload.get("claims", [])
    if not isinstance(claims, list) or len(claims) > 12:
        return []
    base = Path(root).resolve()
    linked = []
    for claim in claims:
        if not isinstance(claim, dict):
            continue
        aid, quote, field = (claim.get("artifact_id"), claim.get("quote"), claim.get("field"))
        if (not isinstance(aid, str) or aid not in (artifact_ids or [])
                or not isinstance(quote, str) or not 12 <= len(quote) <= 600
                or not isinstance(field, str) or not KEY.fullmatch(field)):
            continue
        row = db.execute("SELECT material_kind,content_sha256,relative_path FROM artifacts "
                         "WHERE run_id=? AND artifact_id=?", (run_id, aid)).fetchone()
        if row is None:
            raise ValueError("ARTIFACT_NOT_IN_RUN")
        if row[0] in {"image", "decision_response"}:
            continue
        relative = Path(row[2])
        path = (base / relative).resolve()
        if relative.is_absolute() or ".." in relative.parts or not path.is_relative_to(base / "artifacts"):
            raise ValueError("EVIDENCE_LINK_ARTIFACT_PATH_OUTSIDE_ROOT")
        if not path.is_file() or not 0 < path.stat().st_size <= 4_000_000:
            raise ValueError("EVIDENCE_LINK_ARTIFACT_MISSING_OR_INVALID")
        content = path.read_bytes()
        if hashlib.sha256(content).hexdigest() != row[1]:
            raise ValueError("ARTIFACT_READBACK_HASH_MISMATCH")
        normalized = _text({"kind": row[0], "text": content.decode("utf-8", errors="replace")})
        wanted = _norm(quote)
        start = normalized.find(wanted)
        if start < 0:
            continue
        end = start + len(wanted)
        linked.append(link_evidence(base, run_id, record_id, aid, content_sha256=row[1],
            span_sha256=hashlib.sha256(normalized[start:end].encode("utf-8")).hexdigest(),
            locator_kind="normalized_text_v1", locator=f"{start}:{end}", material_kind=row[0],
            field=field, _db=db))
    return linked


def evidence_links(root: Path | str | None, run_id: str, *,
                   record_id: str | None = None) -> list[dict]:
    """Append-only provenance links for a run, optionally narrowed to one record."""
    store_root = _root(root)
    _id(run_id, "run_id")
    if record_id is not None:
        _id(record_id, "record_id")
    sql = ("SELECT link_id,record_id,artifact_id,content_sha256,span_sha256,locator_kind,"
           "locator,material_kind,field,created_at FROM evidence_links WHERE run_id=?")
    args: list = [run_id]
    if record_id is not None:
        sql += " AND record_id=?"
        args.append(record_id)
    with closing(_connection(store_root)) as db:
        rows = db.execute(sql + " ORDER BY created_at,link_id", args).fetchall()
    keys = ("link_id", "record_id", "artifact_id", "content_sha256", "span_sha256",
            "locator_kind", "locator", "material_kind", "field", "created_at")
    return [dict(zip(keys, row)) for row in rows]


# --------------------------------------------------------------------- envelope
def evidence_envelope(root: Path | str | None, run_id: str, artifact_id: str, *,
                      operation_id: str, provider: str, capability_id: str,
                      extraction_method: str, ledger_id: str | None = None,
                      egress_decision_ref: str | None = None,
                      derived_refs: tuple[str, ...] | list[str] = (),
                      verified_state: str = "UNVERIFIED",
                      caveats: tuple[str, ...] | list[str] = ()) -> dict:
    """One evidence envelope, field-for-field per the Wave-1 contract §3.5.

    `ledger_id` is the link to the charge that produced this evidence. While the
    ledger is unwired this stays `None` and the envelope says so, because the
    contract makes an unlinked envelope ineligible for export — a gap that must be
    visible rather than filled with a placeholder.
    """
    material = read_artifact(root, run_id, artifact_id)
    _id(operation_id, "operation_id")
    if verified_state not in VERIFIED_STATES:
        raise ValueError("ENVELOPE_VERIFIED_STATE_INVALID")
    envelope = {
        # The operation is part of the identity: two judgments reading the same page
        # are two pieces of provenance, and collapsing them would lose one.
        "evidence_id": "ev-" + hashlib.sha256(
            "|".join((run_id, artifact_id, operation_id, capability_id)).encode("utf-8")
        ).hexdigest()[:32],
        "run_id": run_id,
        "operation_id": operation_id,
        "ledger_id": ledger_id,
        "provider": provider,
        "capability_id": capability_id,
        "source_url": material["origin_url"] or None,
        "retrieved_at": material["acquired_at"],
        "content_sha256": material["content_sha256"],
        "raw_artifact_ref": artifact_id,
        "derived_refs": tuple(derived_refs),
        "extraction_method": extraction_method,
        "verified_state": verified_state,
        "candidate_only": True,
        "egress_decision_ref": egress_decision_ref,
        "caveats": tuple(caveats) + (() if ledger_id else ("LEDGER_NOT_WIRED",)),
    }
    if set(envelope) != set(ENVELOPE_FIELDS):
        raise ValueError("ENVELOPE_FIELD_SET_DRIFT")
    return envelope


def record_envelope(root: Path | str | None, run_id: str, artifact_id: str, *,
                    operation_id: str, provider: str, capability_id: str,
                    extraction_method: str, ledger_id: str | None = None,
                    egress_decision_ref: str | None = None,
                    derived_refs: tuple[str, ...] | list[str] = (),
                    verified_state: str = "UNVERIFIED",
                    caveats: tuple[str, ...] | list[str] = ()) -> dict:
    """Build an envelope and persist it, so the export republishes what was recorded.

    Re-deriving envelopes at export time would silently drop the ledger link and the
    operation that produced them — the two fields that make an envelope worth
    storing. Append-only, and idempotent for an identical envelope.
    """
    store_root = _root(root)
    envelope = evidence_envelope(
        store_root, run_id, artifact_id, operation_id=operation_id, provider=provider,
        capability_id=capability_id, extraction_method=extraction_method,
        ledger_id=ledger_id, egress_decision_ref=egress_decision_ref,
        derived_refs=derived_refs, verified_state=verified_state, caveats=caveats)
    values = (run_id, envelope["operation_id"], artifact_id, envelope["ledger_id"],
              envelope["provider"], envelope["capability_id"], envelope["source_url"],
              envelope["retrieved_at"], envelope["content_sha256"],
              envelope["raw_artifact_ref"],
              json.dumps(list(envelope["derived_refs"]), sort_keys=True),
              envelope["extraction_method"], envelope["verified_state"],
              int(envelope["candidate_only"]), envelope["egress_decision_ref"],
              json.dumps(list(envelope["caveats"]), sort_keys=True))
    with closing(_connection(store_root)) as db:
        db.execute("BEGIN IMMEDIATE")
        prior = db.execute(
            "SELECT run_id,operation_id,artifact_id,ledger_id,provider,capability_id,"
            "source_url,retrieved_at,content_sha256,raw_artifact_ref,derived_refs_json,"
            "extraction_method,verified_state,candidate_only,egress_decision_ref,"
            "caveats_json FROM evidence_envelopes WHERE evidence_id=?",
            (envelope["evidence_id"],)).fetchone()
        if prior is not None:
            db.commit()
            if tuple(prior) != values:
                raise ValueError("ENVELOPE_ID_COLLISION: append a new operation")
            return {"evidence_id": envelope["evidence_id"], "status": "IDEMPOTENT",
                    "envelope": envelope}
        db.execute(
            "INSERT INTO evidence_envelopes VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (envelope["evidence_id"], *values,
             datetime.now(timezone.utc).isoformat()))
        db.commit()
    return {"evidence_id": envelope["evidence_id"], "status": "RECORDED",
            "envelope": envelope}


def stored_envelopes(root: Path | str | None, run_id: str, *,
                     artifact_id: str | None = None) -> list[dict]:
    """Recorded envelopes for a run, optionally narrowed to one artifact."""
    store_root = _root(root)
    _id(run_id, "run_id")
    if artifact_id is not None:
        _id(artifact_id, "artifact_id")
    sql = ("SELECT evidence_id,run_id,operation_id,ledger_id,provider,capability_id,"
           "source_url,retrieved_at,content_sha256,raw_artifact_ref,derived_refs_json,"
           "extraction_method,verified_state,candidate_only,egress_decision_ref,"
           "caveats_json FROM evidence_envelopes WHERE run_id=?")
    args: list = [run_id]
    if artifact_id is not None:
        sql += " AND artifact_id=?"
        args.append(artifact_id)
    with closing(_connection(store_root)) as db:
        rows = db.execute(sql + " ORDER BY created_at,evidence_id", args).fetchall()
    out = []
    for row in rows:
        envelope = {
            "evidence_id": row[0], "run_id": row[1], "operation_id": row[2],
            "ledger_id": row[3], "provider": row[4], "capability_id": row[5],
            "source_url": row[6], "retrieved_at": row[7], "content_sha256": row[8],
            "raw_artifact_ref": row[9], "derived_refs": tuple(json.loads(row[10])),
            "extraction_method": row[11], "verified_state": row[12],
            "candidate_only": bool(row[13]), "egress_decision_ref": row[14],
            "caveats": tuple(json.loads(row[15])),
        }
        if set(envelope) != set(ENVELOPE_FIELDS):
            raise ValueError("ENVELOPE_FIELD_SET_DRIFT")
        out.append(envelope)
    return out
