#!/usr/bin/env python3
"""Hard pages: TinyFish Agent runs in the background so no tool call waits minutes.

The vendor's ``run_web_automation`` is synchronous and "may take several
minutes"; a host ends a tool call long before that. So a hard page is queued
here, run on a small worker pool inside the MCP server, and its result is saved
as material in the Run like any other fetch. The Agent asks for the status and
carries on with other targets meanwhile.

A run that was in flight when the server stopped is never resubmitted: the
vendor warns that a timed-out run may still be executing, so a retry could pay
twice. It is reported as UNKNOWN_AFTER_RESTART instead.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

from research_event_journal import _root

__all__ = ["enqueue", "status", "recover", "DEFAULT_GOAL", "MAX_RUNNING"]

MAX_RUNNING = 2
DEFAULT_GOAL = ("Open this page and return its visible text verbatim, exactly as written on the "
                "page (no summary, no table you made up), including every person's name, role or "
                "job title, email address and phone number. Follow on-page tabs or 'show more' "
                "if needed. Do not log in, submit forms or accept anything.")
_POOL = ThreadPoolExecutor(max_workers=MAX_RUNNING, thread_name_prefix="hard-page")
_LOCK = threading.Lock()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _db(root) -> sqlite3.Connection:
    path = Path(_root(root)) / "state" / "research-hard-pages.sqlite3"
    path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path, timeout=30)
    db.execute("PRAGMA journal_mode=WAL")
    db.execute("CREATE TABLE IF NOT EXISTS hard_pages (run_id TEXT NOT NULL, url TEXT NOT NULL, "
               "goal TEXT NOT NULL, operation_id TEXT NOT NULL, status TEXT NOT NULL, "
               "reason TEXT NOT NULL DEFAULT '', artifact_ids TEXT NOT NULL DEFAULT '[]', "
               "queued_at TEXT NOT NULL, started_at TEXT NOT NULL DEFAULT '', "
               "finished_at TEXT NOT NULL DEFAULT '', PRIMARY KEY (run_id, url))")
    return db


def _update(root, run_id: str, url: str, **fields) -> None:
    sets = ", ".join(f"{name}=?" for name in fields)
    with closing(_db(root)) as db:
        db.execute(f"UPDATE hard_pages SET {sets} WHERE run_id=? AND url=?",
                   (*fields.values(), run_id, url))
        db.commit()


def _work(root, run_id: str, url: str, goal: str, operation_id: str, runner) -> None:
    _update(root, run_id, url, status="running", started_at=_now())
    try:
        answer = runner(run_id, url, goal, operation_id)
    except Exception as exc:  # noqa: BLE001 - a refusal is a recorded outcome, not a crash
        _update(root, run_id, url, status="failed", reason=str(exc).split(":", 1)[0][:120],
                finished_at=_now())
        return
    ok = answer.get("status") == "OK" and answer.get("artifact_ids")
    _update(root, run_id, url, status="done" if ok else
            ("unknown" if answer.get("status") == "UNKNOWN" else "failed"),
            reason=str(answer.get("reason_code") or "")[:120],
            artifact_ids=json.dumps(answer.get("artifact_ids") or []), finished_at=_now())


def enqueue(root, run_id: str, url: str, *, goal: str = "", runner=None) -> dict:
    """Queue one hard page for this Run (once per URL) and start it in the background."""
    if not isinstance(url, str) or not url.startswith("https://") or len(url) > 2048:
        raise ValueError("HARD_PAGE_URL_INVALID: one public https page")
    goal = " ".join((goal or DEFAULT_GOAL).split())
    if not 10 <= len(goal) <= 1000:
        raise ValueError("HARD_PAGE_GOAL_INVALID: 10-1000 characters")
    operation_id = "hard-" + hashlib.sha256(f"{run_id}:{url}".encode()).hexdigest()[:24]
    with _LOCK, closing(_db(root)) as db:
        row = db.execute("SELECT status FROM hard_pages WHERE run_id=? AND url=?",
                         (run_id, url)).fetchone()
        if row is not None:
            return {"url": url, "status": row[0], "queued": False,
                    "note": "already queued for this Run; never submitted twice"}
        db.execute("INSERT INTO hard_pages(run_id,url,goal,operation_id,status,queued_at) "
                   "VALUES(?,?,?,?,?,?)", (run_id, url, goal, operation_id, "queued", _now()))
        db.commit()
    if runner is not None:
        _POOL.submit(_work, root, run_id, url, goal, operation_id, runner)
    return {"url": url, "status": "queued", "queued": True, "operation_id": operation_id,
            "next": "carry on with other targets; marketops_hard_pages(run_id) shows the "
                    "result and its artifact ids in a few minutes"}


def status(root, run_id: str) -> dict:
    with closing(_db(root)) as db:
        rows = db.execute("SELECT url,status,reason,artifact_ids,queued_at,finished_at "
                          "FROM hard_pages WHERE run_id=? ORDER BY queued_at", (run_id,)).fetchall()
    pages = [{"url": u, "status": s, "reason": r or None, "artifact_ids": json.loads(a),
              "queued_at": q, "finished_at": f or None} for u, s, r, a, q, f in rows]
    counts: dict[str, int] = {}
    for page in pages:
        counts[page["status"]] = counts.get(page["status"], 0) + 1
    return {"run_id": run_id, "counts": counts, "pages": pages,
            "next": ("harvest each done page's artifact ids (quotes come from them); "
                     "queued/running ones finish in the background")}


def recover(root) -> int:
    """At server start: runs that were in flight are UNKNOWN, never resubmitted."""
    try:
        with closing(_db(root)) as db:
            moved = db.execute("UPDATE hard_pages SET status='unknown_after_restart', "
                               "reason='SERVER_RESTARTED_DURING_RUN', finished_at=? "
                               "WHERE status IN ('queued','running')", (_now(),)).rowcount
            db.commit()
        return moved
    except (OSError, sqlite3.Error):
        return 0
