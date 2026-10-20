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
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlsplit

from research_event_journal import _root

__all__ = ["import_targets", "claim", "complete", "renew", "status", "target_key",
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
                   scene: str = "") -> dict:
    """Add one source's targets; report listed vs imported/duplicates/rejected."""
    _id(pool_id, "pool_id"); _id(source_id, "source_id")
    if not isinstance(targets, list) or not 1 <= len(targets) <= MAX_IMPORT:
        raise ValueError(f"POOL_TARGETS_INVALID: 1-{MAX_IMPORT} targets per import")
    if listed_count is not None and (type(listed_count) is not int or listed_count < 0):
        raise ValueError("POOL_LISTED_COUNT_INVALID: the number of entities the source listed")
    base = _root(root)
    imported = duplicates = 0
    rejected: list[dict] = []
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
    return dict(zip(keys, row))


_COLUMNS = ("target_key,name,kind,website,address,source_id,priority,status,claimed_by,"
            "lease_until,attempts,run_id,organization_key,reason,routes")


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
            f"SELECT {_COLUMNS} FROM pool_targets WHERE pool_id=? AND "
            "(status='pending' OR (status='claimed' AND lease_until<?)) "
            "ORDER BY priority DESC, rowid LIMIT ?", (pool_id, _stamp(now), count)).fetchall()
        for row in rows:
            db.execute("UPDATE pool_targets SET status='claimed', claimed_by=?, lease_until=?, "
                       "attempts=attempts+1, updated_at=? WHERE pool_id=? AND target_key=?",
                       (worker_id, until, _stamp(now), pool_id, row[0]))
        db.commit()
    targets = [{**_row(r), "status": "claimed", "claimed_by": worker_id, "lease_until": until}
               for r in rows]
    return {"pool_id": pool_id, "worker_id": worker_id, "lease_until": until,
            "claimed": len(targets), "targets": targets}


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
            cursor = db.execute(
                "UPDATE pool_targets SET status=?, reason=?, run_id=?, organization_key=?, "
                "routes=?, lease_until='', updated_at=? WHERE pool_id=? AND target_key=? AND "
                "status='claimed' AND claimed_by=?",
                (outcome, str(item.get("reason") or "")[:500], str(item.get("run_id") or ""),
                 str(item.get("organization_key") or ""), int(item.get("routes") or 0),
                 _stamp(), pool_id, key, worker_id))
            (accepted if cursor.rowcount else refused).append(
                {"target_key": key} if cursor.rowcount else
                {"target_key": key, "reason": "NOT_HELD_BY_THIS_WORKER"})
        db.commit()
    return {"accepted": len(accepted), "refused": refused}


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


def status(root, *, pool_id: str) -> dict:
    """Progress every window reads from the same rows: counts, sources, workers."""
    _id(pool_id, "pool_id")
    now = _stamp()
    with closing(_db(_root(root))) as db:
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
    total = sum(counts.values())
    finished = sum(counts.get(s, 0) for s in OUTCOMES)
    return {
        "pool_id": pool_id, "objective": pool[0], "scene": pool[1], "total": total,
        "by_status": {s: counts.get(s, 0) for s in ("pending", "claimed", *sorted(OUTCOMES))},
        "expired_leases": expired, "progress": round(finished / total, 3) if total else 0.0,
        "targets_with_routes": routes[1], "routes": routes[0],
        "hit_rate": round(routes[1] / counts["done"], 3) if counts.get("done") else None,
        "active_workers": {w: n for w, n in workers},
        "sources": [{"source_id": s[0], "provider": s[1], "listed": s[2], "imported": s[3],
                     "duplicates": s[4], "rejected": s[5],
                     "reconciled": s[2] is None or s[2] == s[3] + s[4] + s[5]} for s in sources],
        "top_hold_reasons": [{"reason": r[0][:120], "count": r[1]} for r in holds],
    }
