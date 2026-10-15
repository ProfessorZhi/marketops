#!/usr/bin/env python3
"""Small append-only local research event ledger. No provider/network/model calls.

An instrumented operation saves its event automatically through append_event().
Calls made directly against third-party MCP servers cannot be observed here.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parents[1]
ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,95}$")
STAGES = {"intake", "discovery", "acquisition", "entities", "qualification",
          "people", "routes", "export", "handoff", "review"}
KINDS = {"run", "tool", "source", "evidence", "entity", "decision",
         "route", "export", "review", "handoff", "correction"}
STATUSES = {"started", "succeeded", "failed", "blocked", "hold", "unknown"}


def _identifier(value: str, field: str, optional: bool = False) -> str:
    if optional and not value:
        return ""
    if not isinstance(value, str) or not ID.fullmatch(value):
        raise ValueError(f"{field}: use a nonempty opaque ASCII ID, never PII, URL, path or token")
    return value


def _root(value: str | Path | None) -> Path:
    configured = value or os.environ.get("MARKETOPS_RESEARCH_DATA_ROOT")
    if not configured:
        raise ValueError("Specify --data-root or MARKETOPS_RESEARCH_DATA_ROOT")
    root = Path(configured).expanduser().resolve()
    if root == HERE or HERE in root.parents:
        raise ValueError("Runtime/customer data must not be inside plugin source")
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    return root


def _connect(root: Path) -> sqlite3.Connection:
    state = root / "state"
    state.mkdir(parents=True, exist_ok=True, mode=0o700)
    db = sqlite3.connect(state / "research-events.sqlite3", timeout=20)
    db.execute("PRAGMA busy_timeout=20000")
    db.execute("PRAGMA journal_mode=WAL")
    db.execute("""CREATE TABLE IF NOT EXISTS experience_events (
        sequence INTEGER PRIMARY KEY AUTOINCREMENT,
        run_id TEXT NOT NULL, operation_id TEXT NOT NULL,
        stage TEXT NOT NULL, kind TEXT NOT NULL, status TEXT NOT NULL,
        recorded_at TEXT NOT NULL, payload_sha256 TEXT NOT NULL,
        payload_json TEXT NOT NULL,
        UNIQUE(run_id, operation_id, kind, status)
    )""")
    return db


def append_event(data_root: str | Path | None, *, run_id: str, operation_id: str,
                 stage: str, kind: str, status: str, provider: str = "",
                 receipt_ref: str = "", reason_code: str = "") -> dict:
    """Idempotently commit *metadata only*. No raw HTML, email, prompts or secrets."""
    payload = {
        "run_id": _identifier(run_id, "run_id"),
        "operation_id": _identifier(operation_id, "operation_id"),
        "stage": stage, "kind": kind, "status": status,
        "provider": _identifier(provider, "provider", True),
        "receipt_ref": _identifier(receipt_ref, "receipt_ref", True),
        "reason_code": _identifier(reason_code, "reason_code", True),
    }
    if stage not in STAGES or kind not in KINDS or status not in STATUSES:
        raise ValueError("Unsupported stage/kind/status")
    encoded = json.dumps(payload, sort_keys=True, ensure_ascii=True, separators=(",", ":"))
    digest = hashlib.sha256(encoded.encode("utf-8")).hexdigest()
    root = _root(data_root)
    with _connect(root) as db:
        db.execute("BEGIN IMMEDIATE")
        existing = db.execute(
            "SELECT sequence,payload_sha256 FROM experience_events "
            "WHERE run_id=? AND operation_id=? AND kind=? AND status=?",
            (run_id, operation_id, kind, status),
        ).fetchone()
        if existing:
            if existing[1] != digest:
                raise ValueError("Conflicting replay of an existing operation event")
            return {"result": "IDEMPOTENT", "sequence": existing[0], "payload_sha256": digest}
        cursor = db.execute(
            "INSERT INTO experience_events "
            "(run_id,operation_id,stage,kind,status,recorded_at,payload_sha256,payload_json) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (run_id, operation_id, stage, kind, status,
             datetime.now(timezone.utc).isoformat(), digest, encoded),
        )
        return {"result": "RECORDED", "sequence": cursor.lastrowid, "payload_sha256": digest}


def read_events(data_root: str | Path | None, run_id: str) -> list[dict]:
    _identifier(run_id, "run_id")
    with _connect(_root(data_root)) as db:
        rows = db.execute("SELECT sequence,recorded_at,payload_sha256,payload_json "
                          "FROM experience_events WHERE run_id=? ORDER BY sequence",
                          (run_id,)).fetchall()
    return [{**json.loads(r[3]), "sequence": r[0], "recorded_at": r[1],
             "payload_sha256": r[2]} for r in rows]


def draft_experience(data_root: str | Path | None, *, run_id: str, scope: str,
                     rule_version: str, skill_version: str, output: Path) -> dict:
    """Derive an unapproved incident draft; never infer causes or lessons."""
    rows = read_events(data_root, run_id)
    refs = [{"ref": f"event-{x['sequence']}", "kind": "runtime_receipt",
             "locator": f"research-events.sqlite3#event-{x['sequence']}",
             "sha256": x["payload_sha256"]} for x in rows]
    incidents = []
    for x in rows:
        if x["status"] not in {"failed", "blocked", "hold", "unknown"}:
            continue
        incidents.append({
            "id": f"incident-{x['sequence']}", "stage": x["stage"], "category": "unknown",
            "operation_ref": x["operation_id"], "expected": "A usable outcome or an explicit blocker",
            "observation": f"{x['kind']} returned {x['status']}; code={x['reason_code'] or 'unknown'}",
            "impact": "No business completion is inferred from this event",
            "root_cause_status": "unknown", "validation_refs": [],
            "evidence_refs": [f"event-{x['sequence']}"],
            "next_action": "Inspect the referenced receipt; determine remediation with a human",
        })
    draft = {
        "schema_version": "marketops.experience.v1", "run_id": run_id,
        "scope": scope, "store_ref": "local-research-events",
        "rule_version": rule_version, "skill_version": skill_version,
        "privacy": "sanitized_candidate", "promotion": "candidate_only",
        "evidence_manifest": refs, "incidents": incidents, "lessons": [], "metrics": [],
    }
    target = output.expanduser()
    if target.resolve().is_relative_to(HERE):
        raise ValueError("Experience output must be outside plugin source")
    with target.open("x", encoding="utf-8") as f:
        json.dump(draft, f, indent=2, ensure_ascii=False)
        f.write("\n")
    return {"result": "CANDIDATE_DRAFT_NOT_VERIFIED", "events": len(rows),
            "incidents": len(incidents), "lessons": 0, "path": str(target)}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-root", help="customer-controlled directory outside Git")
    sub = parser.add_subparsers(dest="command", required=True)
    record = sub.add_parser("record")
    for key in ("run-id", "operation-id", "stage", "kind", "status"):
        record.add_argument("--" + key, required=True)
    for key in ("provider", "receipt-ref", "reason-code"):
        record.add_argument("--" + key, default="")
    for action in ("list", "draft"):
        p = sub.add_parser(action)
        p.add_argument("--run-id", required=True)
        if action == "draft":
            p.add_argument("--scope", required=True)
            p.add_argument("--rule-version", required=True)
            p.add_argument("--skill-version", required=True)
            p.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        if args.command == "record":
            result = append_event(args.data_root, run_id=args.run_id, operation_id=args.operation_id,
                                  stage=args.stage, kind=args.kind, status=args.status,
                                  provider=args.provider, receipt_ref=args.receipt_ref,
                                  reason_code=args.reason_code)
        elif args.command == "list":
            result = {"run_id": args.run_id, "events": read_events(args.data_root, args.run_id)}
        else:
            result = draft_experience(args.data_root, run_id=args.run_id, scope=args.scope,
                                      rule_version=args.rule_version, skill_version=args.skill_version,
                                      output=args.output)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except (OSError, ValueError, sqlite3.Error) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
