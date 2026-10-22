#!/usr/bin/env python3
"""Shared target pool for many agents on one machine.

Several conversations (Claude Code, Codex, WorkBuddy) each run the plugin's MCP
server against the same data root. This module is the hand-off between them:
an orchestrator imports targets from a source with the count that source
listed; workers claim batches under a lease, research them into their own Run
and report each target's outcome; anyone reads progress from the same rows.
Nothing is passed through files or chat.

Rules that came from Member A's runs:

* Dedupe on a normalised name plus postcode district (or region), never on the
  web domain alone: one domain once merged 40 venues into 12.
* Every source records how many entities it listed, so listed = imported +
  duplicates + rejected can be checked before delivery (silent drops left no
  error signal and lost 582 emails once).
* A claim is a lease. A crashed conversation does not strand its targets; once
  the lease expires another worker may claim them.
"""
from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import uuid
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlsplit

from research_event_journal import _root

__all__ = ["import_targets", "claim", "complete", "renew", "reopen", "status", "target_key",
           "list_targets", "OUTCOMES"]

OUTCOMES = frozenset({"done", "hold", "excluded", "duplicate"})
STATUSES = frozenset({"pending", "claimed"}) | OUTCOMES
ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,95}$")
MAX_IMPORT = 5_000
MAX_CLAIM = 200
_POSTCODE = re.compile(r"\b([A-Z]{1,2}\d[A-Z\d]?)\s*\d[A-Z]{2}\b", re.I)  # UK outward code
_STOP = {"the", "ltd", "limited", "plc", "llp", "cic", "inc", "and", "&", "of"}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _stamp(moment: datetime | None = None) -> str:
    return (moment or _now()).isoformat()


def _id(value: str, name: str) -> str:
    if not isinstance(value, str) or not ID.fullmatch(value):
        raise ValueError(f"POOL_{name.upper()}_INVALID: 1-96 characters [A-Za-z0-9_.:-]")
    return value


def _db(root: Path) -> sqlite3.Connection:
    folder = root / "state"
    folder.mkdir(parents=True, exist_ok=True, mode=0o700)
    db = sqlite3.connect(folder / "research-pool.sqlite3", timeout=30)
    db.execute("PRAGMA journal_mode=WAL")
    db.execute("PRAGMA busy_timeout=30000")
    db.executescript("""
        CREATE TABLE IF NOT EXISTS pools (
            pool_id TEXT PRIMARY KEY, objective TEXT NOT NULL, scene TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS pool_sources (
            pool_id TEXT NOT NULL, source_id TEXT NOT NULL, url TEXT NOT NULL,
            provider TEXT NOT NULL, listed_count INTEGER, imported INTEGER NOT NULL,
            duplicates INTEGER NOT NULL, rejected INTEGER NOT NULL, created_at TEXT NOT NULL,
            PRIMARY KEY (pool_id, source_id));
        CREATE TABLE IF NOT EXISTS pool_targets (
            pool_id TEXT NOT NULL, target_key TEXT NOT NULL, name TEXT NOT NULL,
            kind TEXT NOT NULL, website TEXT NOT NULL DEFAULT '', address TEXT NOT NULL DEFAULT '',
            source_id TEXT NOT NULL, priority REAL NOT NULL DEFAULT 0,
            status TEXT NOT NULL, claimed_by TEXT NOT NULL DEFAULT '',
            lease_until TEXT NOT NULL DEFAULT '', attempts INTEGER NOT NULL DEFAULT 0,
            run_id TEXT NOT NULL DEFAULT '', organization_key TEXT NOT NULL DEFAULT '',
            reason TEXT NOT NULL DEFAULT '', routes INTEGER NOT NULL DEFAULT 0,
            extra_json TEXT NOT NULL DEFAULT '{}', updated_at TEXT NOT NULL,
            PRIMARY KEY (pool_id, target_key));
        CREATE INDEX IF NOT EXISTS pool_targets_status
            ON pool_targets(pool_id, status, priority DESC);
        CREATE TABLE IF NOT EXISTS pool_batch_jobs (
            pool_id TEXT NOT NULL, target_key TEXT NOT NULL, worker_id TEXT NOT NULL,
            run_id TEXT NOT NULL, lease_attempt INTEGER NOT NULL,
            result_json TEXT, updated_at TEXT NOT NULL,
            finalizer TEXT NOT NULL DEFAULT '', finalize_until TEXT NOT NULL DEFAULT '',
            PRIMARY KEY(pool_id,target_key));
    """)
    return db


def _words(text: str) -> list[str]:
    return [w for w in re.findall(r"[0-9a-z]+", text.casefold()) if w not in _STOP]


def target_key(name: str, address: str = "", website: str = "") -> str:
    """Stable dedupe key: normalised name + postcode district, else region words.

    The domain only breaks a tie when there is no address at all; it is never
    the key by itself, because one domain can run many venues.
    """
    words = _words(name)
    if not words:
        raise ValueError("POOL_TARGET_NAME_INVALID")
    postcode = _POSTCODE.search(address or "")
    if postcode:
        anchor = postcode.group(1).upper()
    elif address and address.strip():
        anchor = " ".join(_words(address)[-2:])
    else:
        host = (urlsplit(website).hostname or "") if website else ""
        anchor = host.casefold().removeprefix("www.")
    basis = " ".join(words) + "|" + anchor
    return "t-" + hashlib.sha256(basis.encode()).hexdigest()[:20]


def import_targets(root, *, pool_id: str, source_id: str, source_url: str, provider: str,
                   listed_count: int | None, targets: list[dict], objective: str = "",
                   scene: str = "", excluded_count: int = 0) -> dict:
    """Add one source's targets; report listed vs imported/duplicates/rejected."""
    _id(pool_id, "pool_id"); _id(source_id, "source_id")
    if type(excluded_count) is not int or excluded_count < 0 or excluded_count > MAX_IMPORT:
        raise ValueError("POOL_EXCLUDED_COUNT_INVALID")
    if not isinstance(targets, list) or not 0 <= len(targets) <= MAX_IMPORT or (not targets and not excluded_count):
        raise ValueError(f"POOL_TARGETS_INVALID: 1-{MAX_IMPORT} targets per import")
    if listed_count is not None and (type(listed_count) is not int or listed_count < 0):
        raise ValueError("POOL_LISTED_COUNT_INVALID: the number of entities the source listed")
    base = _root(root)
    imported = duplicates = 0
    rejected: list[dict] = [{"reason": "EXPLICIT_SOURCE_FILTER"} for _ in range(excluded_count)]
    with closing(_db(base)) as db:
        db.execute("BEGIN IMMEDIATE")
        db.execute("INSERT OR IGNORE INTO pools VALUES(?,?,?,?)",
                   (pool_id, objective or pool_id, scene, _stamp()))
        if db.execute("SELECT 1 FROM pool_sources WHERE pool_id=? AND source_id=?",
                      (pool_id, source_id)).fetchone():
            db.rollback()
            raise ValueError("POOL_SOURCE_ALREADY_IMPORTED: use a new source_id for another page")
        for index, item in enumerate(targets):
            name = item.get("name") if isinstance(item, dict) else None
            if not isinstance(name, str) or not name.strip() or len(name) > 200:
                rejected.append({"index": index, "reason": "NAME_MISSING_OR_TOO_LONG"})
                continue
            website = item.get("website") or ""
            address = item.get("address") or ""
            if website and urlsplit(str(website)).scheme not in {"http", "https"}:
                website = ""
            try:
                key = target_key(name, str(address), str(website))
            except ValueError:
                rejected.append({"index": index, "reason": "NAME_HAS_NO_WORDS"})
                continue
            extra = {k: v for k, v in item.items()
                     if k not in {"name", "website", "address", "kind", "priority"}}
            cursor = db.execute(
                "INSERT OR IGNORE INTO pool_targets(pool_id,target_key,name,kind,website,address,"
                "source_id,priority,status,extra_json,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                (pool_id, key, " ".join(name.split()), str(item.get("kind") or "organization"),
                 str(website), str(address), source_id, float(item.get("priority") or 0),
                 "pending", json.dumps(extra, ensure_ascii=False)[:4000], _stamp()))
            if cursor.rowcount:
                imported += 1
            else:
                duplicates += 1
                if website:  # fill a website the first source lacked
                    db.execute("UPDATE pool_targets SET website=? WHERE pool_id=? AND "
                               "target_key=? AND website=''", (str(website), pool_id, key))
        db.execute("INSERT INTO pool_sources VALUES(?,?,?,?,?,?,?,?,?)",
                   (pool_id, source_id, str(source_url)[:2048], str(provider)[:60], listed_count,
                    imported, duplicates, len(rejected), _stamp()))
        db.commit()
    accounted = imported + duplicates + len(rejected)
    return {"pool_id": pool_id, "source_id": source_id, "listed_count": listed_count,
            "imported": imported, "duplicates": duplicates, "rejected": rejected[:20],
            "reconciled": listed_count is None or listed_count == accounted,
            "unaccounted": None if listed_count is None else listed_count - accounted}


def _row(row) -> dict:
    keys = ("target_key", "name", "kind", "website", "address", "source_id", "priority",
            "status", "claimed_by", "lease_until", "attempts", "run_id", "organization_key",
            "reason", "routes")
    result = dict(zip(keys, row))
    if len(row) > len(keys):
        result["source_context"] = json.loads(row[len(keys)])
    return result


_COLUMNS = ("target_key,name,kind,website,address,source_id,priority,status,claimed_by,"
            "lease_until,attempts,run_id,organization_key,reason,routes,extra_json")


def claim(root, *, pool_id: str, worker_id: str, count: int = 20,
          lease_minutes: int = 30) -> dict:
    """Atomically lease up to `count` pending (or lease-expired) targets to one worker."""
    _id(pool_id, "pool_id"); _id(worker_id, "worker_id")
    if type(count) is not int or not 1 <= count <= MAX_CLAIM:
        raise ValueError(f"POOL_CLAIM_COUNT_INVALID: 1-{MAX_CLAIM}")
    if type(lease_minutes) is not int or not 1 <= lease_minutes <= 24 * 60:
        raise ValueError("POOL_LEASE_INVALID: 1-1440 minutes")
    now = _now()
    until = _stamp(now + timedelta(minutes=lease_minutes))
    with closing(_db(_root(root))) as db:
        db.execute("BEGIN IMMEDIATE")
        rows = db.execute(
            f"SELECT {_COLUMNS} FROM pool_targets t WHERE pool_id=? AND "
            "(status='pending' OR (status='claimed' AND lease_until<?)) "
            "AND NOT EXISTS (SELECT 1 FROM pool_batch_jobs j WHERE j.pool_id=t.pool_id "
            "AND j.target_key=t.target_key AND j.lease_attempt=t.attempts AND j.result_json IS NOT NULL) "
            "ORDER BY priority DESC, rowid LIMIT ?", (pool_id, _stamp(now), count)).fetchall()
        for row in rows:
            db.execute("UPDATE pool_targets SET status='claimed', claimed_by=?, lease_until=?, "
                       "attempts=attempts+1, updated_at=? WHERE pool_id=? AND target_key=?",
                       (worker_id, until, _stamp(now), pool_id, row[0]))
        db.commit()
    targets = [{**_row(r), "status": "claimed", "claimed_by": worker_id, "lease_until": until,
                "attempts": _row(r)["attempts"] + 1}
               for r in rows]
    return {"pool_id": pool_id, "worker_id": worker_id, "lease_until": until,
            "claimed": len(targets), "targets": targets}


def release(root, *, pool_id: str, worker_id: str, target_keys: list[str], lease_attempts=None) -> int:
    """Hand targets this worker never started back to the pool, uncounted."""
    _id(pool_id, "pool_id"); _id(worker_id, "worker_id")
    if not target_keys:
        return 0
    with closing(_db(_root(root))) as db:
        db.execute("BEGIN IMMEDIATE")
        released = 0
        for key in target_keys:
            attempt = lease_attempts.get(key) if lease_attempts is not None else None
            db.execute("DELETE FROM pool_batch_jobs WHERE pool_id=? AND target_key=? AND worker_id=? "
                       "AND (? IS NULL OR lease_attempt=?)", (pool_id, key, worker_id, attempt, attempt))
            released += db.execute(
                "UPDATE pool_targets SET status='pending', claimed_by='', lease_until='', "
                "attempts=MAX(attempts-1,0), updated_at=? WHERE pool_id=? AND target_key=? AND "
                "status='claimed' AND claimed_by=? AND (? IS NULL OR attempts=?)",
                (_stamp(), pool_id, key, worker_id, attempt, attempt)).rowcount
        db.commit()
    return released


def claim_batch(root, *, pool_id: str, worker_id: str, run_id: str,
                count: int = 20, lease_minutes: int = 10) -> dict:
    """Resume manual leases, but never dispatch a second live job for a target."""
    for value, name in ((pool_id, "pool_id"), (worker_id, "worker_id"), (run_id, "run_id")):
        _id(value, name)
    if type(count) is not int or not 1 <= count <= MAX_CLAIM:
        raise ValueError("POOL_CLAIM_COUNT_INVALID")
    if type(lease_minutes) is not int or not 1 <= lease_minutes <= 1440:
        raise ValueError("POOL_LEASE_INVALID")
    now, until = _stamp(), _stamp(_now() + timedelta(minutes=lease_minutes))
    with closing(_db(_root(root))) as db:
        db.execute("BEGIN IMMEDIATE")
        rows = db.execute(
            f"SELECT {_COLUMNS} FROM pool_targets t WHERE pool_id=? AND "
            "(status='pending' OR (status='claimed' AND lease_until<?) OR "
            "(status='claimed' AND claimed_by=? AND NOT EXISTS (SELECT 1 FROM pool_batch_jobs j "
            "WHERE j.pool_id=t.pool_id AND j.target_key=t.target_key AND j.lease_attempt=t.attempts))) "
            "AND NOT EXISTS (SELECT 1 FROM pool_batch_jobs j WHERE j.pool_id=t.pool_id "
            "AND j.target_key=t.target_key AND j.lease_attempt=t.attempts AND j.result_json IS NOT NULL) "
            "ORDER BY CASE WHEN claimed_by=? AND status='claimed' THEN 0 ELSE 1 END, priority DESC, rowid LIMIT ?",
            (pool_id, now, worker_id, worker_id, count)).fetchall()
        targets, resumed = [], 0
        for row in rows:
            target = _row(row)
            owned = target["status"] == "claimed" and target["claimed_by"] == worker_id and target["lease_until"] >= now
            attempt = target["attempts"] + (0 if owned else 1)
            resumed += int(owned)
            db.execute("UPDATE pool_targets SET status='claimed',claimed_by=?,lease_until=?,attempts=?,run_id=?,updated_at=? "
                       "WHERE pool_id=? AND target_key=?", (worker_id, until, attempt, run_id, now, pool_id, target["target_key"]))
            db.execute("INSERT OR REPLACE INTO pool_batch_jobs(pool_id,target_key,worker_id,run_id,lease_attempt,result_json,updated_at) "
                       "VALUES(?,?,?,?,?,NULL,?)",
                       (pool_id, target["target_key"], worker_id, run_id, attempt, now))
            targets.append({**target, "status": "claimed", "claimed_by": worker_id,
                            "lease_until": until, "attempts": attempt, "run_id": run_id})
        db.commit()
    return {"pool_id": pool_id, "worker_id": worker_id, "claimed": len(targets),
            "resumed_owned": resumed, "targets": targets}


def save_batch_result(root, *, pool_id, worker_id, run_id, target_key, lease_attempt, result):
    """Write before finalization, so recovery needs no repeat provider calls."""
    body = json.dumps(result, ensure_ascii=False)
    if len(body.encode("utf-8")) > 4_000_000:
        raise ValueError("POOL_BATCH_RESULT_TOO_LARGE")
    with closing(_db(_root(root))) as db:
        changed = db.execute("UPDATE pool_batch_jobs SET result_json=?,updated_at=? WHERE pool_id=? AND target_key=? "
            "AND worker_id=? AND run_id=? AND lease_attempt=?", (body, _stamp(), pool_id, target_key,
                                                               worker_id, run_id, lease_attempt)).rowcount
        db.commit()
    return bool(changed)


def ready_batch_results(root, *, pool_id, worker_id, run_id):
    with closing(_db(_root(root))) as db:
        rows = db.execute("SELECT target_key,lease_attempt,result_json,worker_id,run_id FROM pool_batch_jobs "
                          "WHERE pool_id=? AND (?='' OR worker_id=?) AND (?='' OR run_id=?) AND result_json IS NOT NULL",
                          (pool_id, worker_id, worker_id, run_id, run_id)).fetchall()
    return [{"target_key": key, "lease_attempt": attempt, "result": json.loads(body), "worker_id": owner, "run_id": original_run}
            for key, attempt, body, owner, original_run in rows]


def batch_target(root, *, pool_id, target_key):
    with closing(_db(_root(root))) as db:
        row = db.execute(f"SELECT {_COLUMNS} FROM pool_targets WHERE pool_id=? AND target_key=?",
                         (pool_id, target_key)).fetchone()
    return _row(row) if row else None


def begin_batch_finalization(root, *, pool_id, worker_id, target_key, lease_attempt):
    token = uuid.uuid4().hex
    now, until = _stamp(), _stamp(_now() + timedelta(seconds=60))
    with closing(_db(_root(root))) as db:
        changed = db.execute("UPDATE pool_batch_jobs SET finalizer=?,finalize_until=? WHERE pool_id=? AND target_key=? "
                             "AND worker_id=? AND lease_attempt=? AND result_json IS NOT NULL "
                             "AND (finalizer='' OR finalize_until<?)",
                             (token, until, pool_id, target_key, worker_id, lease_attempt, now)).rowcount
        db.commit()
    return token if changed else ""


def end_batch_finalization(root, *, pool_id, worker_id, target_key, lease_attempt, token, complete=False):
    with closing(_db(_root(root))) as db:
        where = " WHERE pool_id=? AND target_key=? AND worker_id=? AND lease_attempt=? AND finalizer=?"
        args = (pool_id, target_key, worker_id, lease_attempt, token)
        db.execute(("DELETE FROM pool_batch_jobs" if complete else
                    "UPDATE pool_batch_jobs SET finalizer='',finalize_until='' ") + where, args)
        db.commit()


def reopen(root, *, pool_id: str, reason: str, max_attempts: int = 3) -> dict:
    """Put HOLD targets whose reason starts with `reason` back to pending.

    For when the cause is fixed (a missing site now found automatically, a
    method improved). Targets already tried `max_attempts` times stay HOLD, so a
    reopen cannot loop for ever on a target that keeps failing.
    """
    _id(pool_id, "pool_id")
    reason = str(reason or "").strip()
    if not reason:
        raise ValueError("POOL_REOPEN_REASON_REQUIRED: name the HOLD reason to reopen, or ALL")
    with closing(_db(_root(root))) as db:
        db.execute("BEGIN IMMEDIATE")
        match = "" if reason == "ALL" else reason
        cursor = db.execute(
            "UPDATE pool_targets SET status='pending', claimed_by='', lease_until='', "
            "reason='', updated_at=? WHERE pool_id=? AND status='hold' AND attempts<? "
            "AND substr(reason,1,?)=?",
            (_stamp(), pool_id, max_attempts, len(match), match))
        reopened = cursor.rowcount
        still = db.execute("SELECT COUNT(*) FROM pool_targets WHERE pool_id=? AND status='hold'",
                           (pool_id,)).fetchone()[0]
        db.commit()
    return {"pool_id": pool_id, "reopened": reopened, "still_hold": still,
            "next": "workers claim the reopened targets with marketops_harvest_batch"}


def renew(root, *, pool_id: str, worker_id: str, lease_minutes: int = 30) -> dict:
    """Extend every live lease this worker holds (call between long batches)."""
    _id(pool_id, "pool_id"); _id(worker_id, "worker_id")
    until = _stamp(_now() + timedelta(minutes=lease_minutes))
    with closing(_db(_root(root))) as db:
        cursor = db.execute("UPDATE pool_targets SET lease_until=?, updated_at=? WHERE "
                            "pool_id=? AND status='claimed' AND claimed_by=?",
                            (until, _stamp(), pool_id, worker_id))
        db.commit()
    return {"renewed": cursor.rowcount, "lease_until": until}


def complete(root, *, pool_id: str, worker_id: str, results: list[dict]) -> dict:
    """Record outcomes for targets this worker still holds; refuse stolen leases."""
    _id(pool_id, "pool_id"); _id(worker_id, "worker_id")
    if not isinstance(results, list) or not 1 <= len(results) <= MAX_CLAIM:
        raise ValueError(f"POOL_RESULTS_INVALID: 1-{MAX_CLAIM} results")
    accepted, refused = [], []
    with closing(_db(_root(root))) as db:
        db.execute("BEGIN IMMEDIATE")
        for item in results:
            key = item.get("target_key") if isinstance(item, dict) else None
            outcome = item.get("outcome") if isinstance(item, dict) else None
            if outcome not in OUTCOMES:
                refused.append({"target_key": key, "reason": "OUTCOME_INVALID: done|hold|excluded|duplicate"})
                continue
            if outcome != "done" and not str(item.get("reason") or "").strip():
                refused.append({"target_key": key, "reason": "REASON_REQUIRED_FOR_" + outcome.upper()})
                continue
            attempt = item.get("lease_attempt")
            if attempt is None and db.execute("SELECT 1 FROM pool_batch_jobs WHERE pool_id=? AND target_key=?",
                                              (pool_id, key)).fetchone():
                refused.append({"target_key": key, "reason": "BATCH_JOB_OWNS_THIS_LEASE"})
                continue
            cursor = db.execute(
                "UPDATE pool_targets SET status=?, reason=?, run_id=?, organization_key=?, "
                "routes=?, lease_until='', updated_at=? WHERE pool_id=? AND target_key=? AND "
                "status='claimed' AND claimed_by=? AND (? IS NULL OR attempts=?)",
                (outcome, str(item.get("reason") or "")[:500], str(item.get("run_id") or ""),
                 str(item.get("organization_key") or ""), int(item.get("routes") or 0),
                 _stamp(), pool_id, key, worker_id, attempt, attempt))
            (accepted if cursor.rowcount else refused).append(
                {"target_key": key, "outcome": outcome, "reason": str(item.get("reason") or ""),
                 "run_id": str(item.get("run_id") or ""), "attempt": attempt}
                if cursor.rowcount else
                {"target_key": key, "reason": "NOT_HELD_BY_THIS_WORKER"})
        db.commit()
    _journal_walls(root, pool_id, accepted)
    return {"accepted": len(accepted), "refused": refused}


def _journal_walls(root, pool_id: str, accepted: list[dict]) -> None:
    """Every target that ended HOLD is a wall: record it in the experience journal.

    Site-level outcomes (name not on the site, site unreachable, no public route)
    were only in the pool table, so the experience file never showed where whole
    target lists stalled.
    """
    import hashlib
    try:
        from research_event_journal import append_event
    except ImportError:
        return
    for item in accepted:
        if item.get("outcome") != "hold" or not item.get("run_id"):
            continue
        code = (re.match(r"[A-Z][A-Z0-9_]{2,80}", item["reason"]) or [None])[0] or "POOL_TARGET_HOLD"
        op = "pool-hold-" + hashlib.sha256(f"{pool_id}:{item['target_key']}:{item.get('attempt')}"
                                           .encode()).hexdigest()[:24]
        try:
            append_event(root, run_id=item["run_id"], operation_id=op, stage="acquisition",
                         kind="source", status="hold", provider="local", reason_code=code)
        except Exception:  # noqa: BLE001 - the journal never blocks the pool
            pass


def list_targets(root, *, pool_id: str, status: str = "", limit: int = 100,
                 after_rowid: int = 0) -> dict:
    _id(pool_id, "pool_id")
    if status and status not in STATUSES:
        raise ValueError("POOL_STATUS_INVALID")
    with closing(_db(_root(root))) as db:
        rows = db.execute(
            f"SELECT rowid,{_COLUMNS} FROM pool_targets WHERE pool_id=? AND (?='' OR status=?) "
            "AND rowid>? ORDER BY rowid LIMIT ?",
            (pool_id, status, status, after_rowid, max(1, min(limit, 1000)))).fetchall()
    return {"targets": [_row(r[1:]) for r in rows],
            "next_after_rowid": rows[-1][0] if rows else None}


def status(root, *, pool_id: str, read_only: bool = False) -> dict:
    """Progress every window reads from the same rows: counts, sources, workers."""
    _id(pool_id, "pool_id")
    now = _stamp()
    path = Path(_root(root)) / "state/research-pool.sqlite3"
    connection = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True) if read_only else _db(_root(root))
    with closing(connection) as db:
        pool = db.execute("SELECT objective, scene FROM pools WHERE pool_id=?",
                          (pool_id,)).fetchone()
        if pool is None:
            raise ValueError("POOL_NOT_FOUND")
        counts = dict(db.execute("SELECT status, COUNT(*) FROM pool_targets WHERE pool_id=? "
                                 "GROUP BY status", (pool_id,)).fetchall())
        expired = db.execute("SELECT COUNT(*) FROM pool_targets WHERE pool_id=? AND "
                             "status='claimed' AND lease_until<?", (pool_id, now)).fetchone()[0]
        workers = db.execute(
            "SELECT claimed_by, COUNT(*) FROM pool_targets WHERE pool_id=? AND status='claimed' "
            "AND lease_until>=? GROUP BY claimed_by", (pool_id, now)).fetchall()
        routes = db.execute("SELECT COALESCE(SUM(routes),0), COUNT(CASE WHEN routes>0 THEN 1 END) "
                            "FROM pool_targets WHERE pool_id=?", (pool_id,)).fetchone()
        sources = db.execute("SELECT source_id, provider, listed_count, imported, duplicates, "
                             "rejected FROM pool_sources WHERE pool_id=? ORDER BY created_at",
                             (pool_id,)).fetchall()
        holds = db.execute("SELECT reason, COUNT(*) FROM pool_targets WHERE pool_id=? AND "
                           "status IN ('hold','excluded') GROUP BY reason ORDER BY 2 DESC LIMIT 8",
                           (pool_id,)).fetchall()
        jobs = db.execute("SELECT COUNT(CASE WHEN j.result_json IS NULL AND t.status='claimed' AND t.lease_until>=? THEN 1 END), "
                          "COUNT(CASE WHEN j.result_json IS NOT NULL THEN 1 END) FROM pool_batch_jobs j "
                          "JOIN pool_targets t ON t.pool_id=j.pool_id AND t.target_key=j.target_key "
                          "AND t.attempts=j.lease_attempt WHERE j.pool_id=?", (now, pool_id)).fetchone() if db.execute(
                              "SELECT 1 FROM sqlite_master WHERE name='pool_batch_jobs'").fetchone() else (0, 0)
    total = sum(counts.values())
    finished = sum(counts.get(s, 0) for s in OUTCOMES)
    return {
        "pool_id": pool_id, "objective": pool[0], "scene": pool[1], "total": total,
        "by_status": {s: counts.get(s, 0) for s in ("pending", "claimed", *sorted(OUTCOMES))},
        "expired_leases": expired, "progress": round(finished / total, 3) if total else 0.0,
        "in_flight": jobs[0], "results_awaiting_finalization": jobs[1],
        "queue_complete": bool(total) and not counts.get("pending", 0) and not counts.get("claimed", 0) and not any(jobs),
        "continuation_required": bool(counts.get("pending", 0) or counts.get("claimed", 0) or any(jobs)),
        "targets_with_routes": routes[1], "routes": routes[0],
        # Share of researched targets that yielded at least one admitted route
        # (Member A's account rate); excluded/duplicate targets are not attempts.
        "hit_rate": (round(routes[1] / (counts.get("done", 0) + counts.get("hold", 0)), 3)
                     if counts.get("done", 0) + counts.get("hold", 0) else None),
        "active_workers": {w: n for w, n in workers},
        "sources": [{"source_id": s[0], "provider": s[1], "listed": s[2], "imported": s[3],
                     "duplicates": s[4], "rejected": s[5],
                     "reconciled": s[2] is None or s[2] == s[3] + s[4] + s[5]} for s in sources],
        "top_hold_reasons": [{"reason": r[0][:120], "count": r[1]} for r in holds],
    }
