#!/usr/bin/env python3
"""Small append-only local research event ledger. No provider/network/model calls.

An instrumented operation saves its event automatically through append_event().
Calls made directly against third-party MCP servers cannot be observed here.
"""
from __future__ import annotations

import argparse
from collections import Counter
from contextlib import closing
import hashlib
import json
import os
import re
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

import research_config

HERE = Path(__file__).resolve().parents[1]
ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,95}$")
STAGES = {"intake", "discovery", "acquisition", "entities", "qualification",
          "people", "routes", "export", "handoff", "review"}
KINDS = {"run", "tool", "source", "evidence", "entity", "decision",
         "route", "export", "review", "handoff", "correction"}
STATUSES = {"started", "succeeded", "failed", "blocked", "hold", "unknown"}

#: The statuses that mean something actually went wrong or was held back. The
#: summary imports this tuple rather than keeping its own copy, so the two can
#: never disagree about which events are bottlenecks.
BUMP_STATUSES = ("failed", "blocked", "hold", "unknown")

#: A bottleneck the agent actually hit, in Member-A's own audit vocabulary. A closed
#: set so the summary can group by mechanism instead of by similar wording.
NOTE_CATEGORIES = (
    "contact_found_not_stored",     # 发现联系人但未入库
    "field_template_pollution",     # 字段模板污染：一条记录混了两个对象
    "artifact_without_delivery",    # 只生成了文件，没有真正交付
    "source_blocked",               # 官方来源拒绝抓取或改版
    "provider_blocked",             # 收费 Provider 未授权/额度/凭据前置件
    "evidence_missing",             # 结论拿不到可回读证据
    "human_correction",             # 人工纠正了模型判断
    "other",
)

#: The exact column set of `research_notes`. There is deliberately **nowhere** to
#: put a hidden reasoning trace: the table cannot store one, so no validation
#: heuristic has to guess.
NOTE_FIELDS = ("sequence", "run_id", "note_id", "stage", "category", "problem",
               "action_taken", "observed_result", "hypothesis", "evidence_ref",
               "recorded_at", "note_sha256")

#: Refused anywhere in a note. Deliberately broad and value-blind: the error
#: never echoes what it matched.
_REDACT_PATTERNS = (
    ("email", re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")),
    ("url", re.compile(r"(?i)\b(?:https?://|www\.|ftp://|://)")),
    ("key_or_token", re.compile(r"(?i)\b(?:sk-[A-Za-z0-9_-]{12,}|bearer\s|api[_-]?key|"
                                r"access[_-]?token|refresh[_-]?token|client[_-]?secret|"
                                r"password\s*[:=]|token\s*[:=]|secret\s*[:=]|"
                                r"eyJ[A-Za-z0-9_-]{10,}|session\s*[:=])")),
    ("cookie", re.compile(r"(?i)\bcookie\b")),
)
NOTE_TEXT_LIMIT = 600


def _identifier(value: str, field: str, optional: bool = False) -> str:
    if optional and not value:
        return ""
    if not isinstance(value, str) or not ID.fullmatch(value):
        raise ValueError(f"{field}: use a nonempty opaque ASCII ID, never PII, URL, path or token")
    return value


def _root(value: str | Path | None) -> Path:
    configured = value if value is not None else os.environ.get("MARKETOPS_RESEARCH_DATA_ROOT")
    # A blank or unreplaced `${VAR}` override is "not configured", never a path:
    # treating it as one would create a literal directory named after the template.
    if research_config.is_unset_config_value(configured):
        # The first-use config is the normal path now; the env var stays as an
        # explicit per-process override for existing test/deployment setups.
        try:
            root = research_config.data_root()
        except research_config.ConfigError as exc:
            raise ValueError(f"{exc.code}: {exc.message}") from None
    else:
        root = Path(str(configured).strip())
    root = root.expanduser().resolve()
    if root == HERE or HERE in root.parents:
        raise ValueError("Runtime/customer data must not be inside plugin source")
    # A sibling directory inside a cloned Git checkout is also unsafe:
    # future agents may accidentally stage artifacts/PII into Git.
    if any((part / ".git").exists() for part in (root, *root.parents)):
        raise ValueError("Runtime/customer data must be outside all Git checkouts")
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    return root


def data_root_for_pack(pack_dir: str | Path) -> str | Path:
    """Use an explicit runtime root, otherwise the parent of a named run directory."""
    configured = os.environ.get("MARKETOPS_RESEARCH_DATA_ROOT")
    if not research_config.is_unset_config_value(configured):
        return configured
    parent = Path(pack_dir).expanduser().resolve().parent
    return parent.parent if parent.name == "runs" else parent


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
    # Free-text bottleneck notes the agent writes deliberately. Same file, same
    # append-only discipline; nothing here is inferred from a status code.
    db.execute("""CREATE TABLE IF NOT EXISTS research_notes (
        sequence INTEGER PRIMARY KEY AUTOINCREMENT,
        run_id TEXT NOT NULL, note_id TEXT NOT NULL,
        stage TEXT NOT NULL, category TEXT NOT NULL,
        problem TEXT NOT NULL, action_taken TEXT NOT NULL,
        observed_result TEXT NOT NULL, hypothesis TEXT NOT NULL,
        evidence_ref TEXT NOT NULL,
        recorded_at TEXT NOT NULL, note_sha256 TEXT NOT NULL,
        UNIQUE(run_id, note_id)
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
    with closing(_connect(root)) as db:
        db.execute("BEGIN IMMEDIATE")
        existing = db.execute(
            "SELECT sequence,payload_sha256 FROM experience_events "
            "WHERE run_id=? AND operation_id=? AND kind=? AND status=?",
            (run_id, operation_id, kind, status),
        ).fetchone()
        if existing:
            if existing[1] != digest:
                raise ValueError("Conflicting replay of an existing operation event")
            db.commit()
            return {"result": "IDEMPOTENT", "sequence": existing[0], "payload_sha256": digest}
        cursor = db.execute(
            "INSERT INTO experience_events "
            "(run_id,operation_id,stage,kind,status,recorded_at,payload_sha256,payload_json) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (run_id, operation_id, stage, kind, status,
             datetime.now(timezone.utc).isoformat(), digest, encoded),
        )
        db.commit()
        outcome = {"result": "RECORDED", "sequence": cursor.lastrowid, "payload_sha256": digest}
    if status in BUMP_STATUSES:
        # A real bottleneck is exactly when the user's one summary file must move.
        outcome["experience_refresh"] = _refresh_summary_quietly(root)
    return outcome


def _refresh_summary_quietly(root: Path) -> dict:
    """Refresh the collectable summary without ever failing the journaling call.

    The refresh reports its own status either way, so a delivery receipt can show
    that the summary did not move instead of implying it did.
    """
    try:
        from research_experience_summary import refresh_quietly
        return refresh_quietly(data_root=root)
    except Exception as exc:  # noqa: BLE001 - journaling must not depend on the view
        return {"status": "FAILED", "reason_code": type(exc).__name__, "experience_file": None}


def read_events(data_root: str | Path | None, run_id: str) -> list[dict]:
    _identifier(run_id, "run_id")
    with closing(_connect(_root(data_root))) as db:
        rows = db.execute("SELECT sequence,recorded_at,payload_sha256,payload_json "
                          "FROM experience_events WHERE run_id=? ORDER BY sequence",
                          (run_id,)).fetchall()
    return [{**json.loads(r[3]), "sequence": r[0], "recorded_at": r[1],
             "payload_sha256": r[2]} for r in rows]


def summarize_events(data_root: str | Path | None, run_id: str,
                     limit: int = 20) -> dict:
    """Cheap diagnostics: latest N details and whole-Run blocker totals.

    Does not load all successful events into Agent context or memory.
    Source URLs, raw HTML and user messages are not journal payload fields.
    """
    run_id = _identifier(run_id, "run_id")
    if type(limit) is not int or not 1 <= limit <= 50:
        raise ValueError("INVALID_EVENT_PAGE_LIMIT")
    with closing(_connect(_root(data_root))) as db:
        count = db.execute(
            "SELECT COUNT(*) FROM experience_events WHERE run_id=?", (run_id,)
        ).fetchone()[0]
        recent = db.execute(
            "SELECT sequence,recorded_at,payload_sha256,payload_json "
            "FROM experience_events WHERE run_id=? ORDER BY sequence DESC LIMIT ?",
            (run_id, limit)
        ).fetchall()
        blocked_payloads = db.execute(
            "SELECT payload_json FROM experience_events WHERE run_id=? "
            "AND status IN ('blocked','failed','hold','unknown')", (run_id,)
        ).fetchall()
    reasons = Counter()
    for (payload,) in blocked_payloads:
        code = json.loads(payload).get("reason_code")
        if code:
            reasons[code] += 1
    events = [{**json.loads(r[3]), "sequence": r[0], "recorded_at": r[1],
               "payload_sha256": r[2]} for r in reversed(recent)]
    return {"count": count, "events": events,
            "blocker_counts": dict(reasons.most_common(8)),
            "detail_limit": limit, "summary_scope": "full_run"}


def sanitize_note_text(value: str, field: str) -> str:
    """Short, de-identified text only.

    The refusal never echoes the match: a message that quoted the offending
    substring would leak exactly what it refused into the log the user reads.
    """
    if not isinstance(value, str):
        raise ValueError(f"{field}: must be text")
    text = value.strip()
    if len(text) > NOTE_TEXT_LIMIT:
        raise ValueError(f"{field}: too long (limit {NOTE_TEXT_LIMIT} characters)")
    for label, pattern in _REDACT_PATTERNS:
        if pattern.search(text):
            raise ValueError(
                f"NOTE_REJECTED_CONTAINS_{label.upper()}: {field} must not carry an "
                "email, URL, key/token or cookie value; keep only short de-identified text")
    return text


def append_note(data_root: str | Path | None, *, run_id: str, note_id: str, stage: str,
                category: str, problem: str, action_taken: str, observed_result: str,
                hypothesis: str = "", evidence_ref: str = "", **extra) -> dict:
    """Record one real bottleneck the agent met, with what it tried and what happened.

    No root cause is inferred here — ``hypothesis`` is the agent's own candidate
    wording, and a status code alone is never promoted into a cause.
    """
    if extra:
        raise ValueError("NOTE_UNKNOWN_FIELD: " + ",".join(sorted(extra)) +
                         " (a note has no field for hidden reasoning)")
    run_id = _identifier(run_id, "run_id")
    note_id = _identifier(note_id, "note_id")
    if stage not in STAGES:
        raise ValueError("Unsupported note stage")
    if category not in NOTE_CATEGORIES:
        raise ValueError("Unsupported note category")
    evidence_ref = _identifier(evidence_ref, "evidence_ref")
    fields = {
        "run_id": run_id, "note_id": note_id, "stage": stage, "category": category,
        "problem": sanitize_note_text(problem, "problem"),
        "action_taken": sanitize_note_text(action_taken, "action_taken"),
        "observed_result": sanitize_note_text(observed_result, "observed_result"),
        "hypothesis": sanitize_note_text(hypothesis, "hypothesis"),
        "evidence_ref": evidence_ref,
    }
    encoded = json.dumps(fields, sort_keys=True, ensure_ascii=True, separators=(",", ":"))
    digest = hashlib.sha256(encoded.encode("utf-8")).hexdigest()
    root = _root(data_root)
    with closing(_connect(root)) as db:
        db.execute("BEGIN IMMEDIATE")
        existing = db.execute(
            "SELECT sequence,note_sha256 FROM research_notes WHERE run_id=? AND note_id=?",
            (run_id, note_id)).fetchone()
        if existing:
            if existing[1] != digest:
                raise ValueError("Conflicting replay of an existing note")
            db.commit()
            return {"result": "IDEMPOTENT", "sequence": existing[0], "note_sha256": digest}
        cursor = db.execute(
            "INSERT INTO research_notes (run_id,note_id,stage,category,problem,"
            "action_taken,observed_result,hypothesis,evidence_ref,recorded_at,note_sha256) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (run_id, note_id, stage, category, fields["problem"], fields["action_taken"],
             fields["observed_result"], fields["hypothesis"], evidence_ref,
             datetime.now(timezone.utc).isoformat(), digest))
        db.commit()
        return {"result": "RECORDED", "sequence": cursor.lastrowid, "note_sha256": digest}


def read_notes(data_root: str | Path | None, run_id: str | None = None) -> list[dict]:
    """Notes for one Run, or every Run when run_id is None (summary view)."""
    columns = ", ".join(NOTE_FIELDS)
    with closing(_connect(_root(data_root))) as db:
        if run_id is None:
            rows = db.execute(f"SELECT {columns} FROM research_notes ORDER BY sequence").fetchall()
        else:
            _identifier(run_id, "run_id")
            rows = db.execute(f"SELECT {columns} FROM research_notes WHERE run_id=? "
                              "ORDER BY sequence", (run_id,)).fetchall()
    return [dict(zip(NOTE_FIELDS, row)) for row in rows]


def read_all_events(db) -> list[dict]:
    """Every event across every Run, for the cross-Run summary."""
    rows = db.execute("SELECT sequence,run_id,operation_id,stage,kind,status,recorded_at,"
                      "payload_sha256,payload_json FROM experience_events ORDER BY sequence").fetchall()
    return [{"sequence": r[0], "run_id": r[1], "operation_id": r[2], "stage": r[3],
             "kind": r[4], "status": r[5], "recorded_at": r[6], "payload_sha256": r[7],
             **json.loads(r[8])} for r in rows]


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
