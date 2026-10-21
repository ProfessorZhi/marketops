#!/usr/bin/env python3
"""Which providers are out of credit or slowed down, shared by every conversation.

A vendor saying "not enough credits" used to look like any other failure: the
next call tried the same provider again, and nobody was told. Here an answer is
classified once, the state is kept under the data root, and every later call to
that provider is answered locally with a refusal the fallback chain skips, so
the next provider takes over at once.

* QUOTA_EXHAUSTED stays until the period resets (SerpApi: the 1st of next
  month), until a person records a new balance, or for a day, whichever applies.
* RATE_LIMITED stays for a minute.
"""
from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path

__all__ = ["classify", "record", "blocked", "status", "clear", "refusal"]

QUOTA_MARKERS = ("not enough credits", "insufficient", "out of searches", "run out of searches",
                 "ran out of searches", "searches left", "quota", "http 402", "payment required",
                 "upstream_quota_or_payment", "no credits", "credit balance", "plan limit")
RATE_MARKERS = ("http 429", "too many requests", "rate limit", "ratelimit",
                "upstream_rate_limited", "throttl")
RATE_COOLDOWN = timedelta(minutes=1)
QUOTA_HOLD = timedelta(days=1)


def _path(root: Path) -> Path:
    return Path(root) / "state" / "provider-health.json"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _read(root: Path) -> dict:
    database = Path(root) / "state" / "provider-health.sqlite3"
    if database.is_file():
        with closing(sqlite3.connect(database, timeout=30)) as db:
            if db.execute("SELECT 1 FROM sqlite_master WHERE name='health'").fetchone() is None:
                return _legacy_read(root)
            return {provider: json.loads(entry)
                    for provider, entry in db.execute("SELECT provider, entry FROM health")}
    return _legacy_read(root)


def _legacy_read(root: Path) -> dict:
    try:
        data = json.loads(_path(root).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _update(root: Path, provider: str, entry: dict | None) -> None:
    # Discovery runs two providers concurrently, and several chats share this
    # root. A JSON read/modify/replace can lose another provider's quota mark.
    path = Path(root) / "state" / "provider-health.sqlite3"
    path.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(path, timeout=30)) as db, db:
        db.execute("BEGIN IMMEDIATE")
        db.execute("CREATE TABLE IF NOT EXISTS health (provider TEXT PRIMARY KEY, entry TEXT)")
        db.execute("CREATE TABLE IF NOT EXISTS migration (version INTEGER PRIMARY KEY)")
        if db.execute("SELECT 1 FROM migration WHERE version=1").fetchone() is None:
            db.executemany("INSERT OR IGNORE INTO health VALUES (?, ?)",
                           [(p, json.dumps(e)) for p, e in _legacy_read(root).items()])
            db.execute("INSERT INTO migration VALUES (1)")
        if entry is None:
            db.execute("DELETE FROM health WHERE provider=?", (provider,))
        else:
            db.execute("INSERT OR REPLACE INTO health VALUES (?, ?)",
                       (provider, json.dumps(entry)))


def classify(answer: dict) -> str:
    """QUOTA_EXHAUSTED, RATE_LIMITED or "" for one managed-call answer."""
    if not isinstance(answer, dict) or answer.get("status") == "OK":
        return ""
    text = json.dumps({k: answer.get(k) for k in ("reason_code", "caveats", "payload",
                                                  "provider_reason_codes")},
                      ensure_ascii=False, default=str).casefold()
    if any(marker in text for marker in QUOTA_MARKERS):
        return "QUOTA_EXHAUSTED"
    if any(marker in text for marker in RATE_MARKERS):
        return "RATE_LIMITED"
    return ""


def _until(provider: str, state: str) -> str:
    now = _now()
    if state == "RATE_LIMITED":
        return (now + RATE_COOLDOWN).isoformat()
    if provider == "serpapi":
        first = (now.replace(day=1) + timedelta(days=32)).replace(day=1, hour=0, minute=0,
                                                                  second=0, microsecond=0)
        return first.isoformat()
    return (now + QUOTA_HOLD).isoformat()


def record(root: Path, provider: str, state: str, *, detail: str = "") -> dict:
    entry = {"state": state, "since": _now().isoformat(), "until": _until(provider, state),
             "detail": str(detail)[:120]}
    _update(root, provider, entry)
    return entry


def clear(root: Path, provider: str) -> None:
    """A person topped up or a new balance was read: try the provider again."""
    if provider in _read(root):
        _update(root, provider, None)


def blocked(root: Path, provider: str) -> dict | None:
    entry = _read(root).get(provider)
    return entry if entry and _active(entry) else None


def status(root: Path) -> dict:
    return {p: e for p, e in _read(root).items() if _active(e)}


def _active(entry: dict) -> bool:
    try:
        return datetime.fromisoformat(entry["until"]) > _now()
    except (KeyError, ValueError, TypeError):
        return False


def refusal(provider: str, capability_id: str, entry: dict) -> dict:
    """The local answer for a provider known to be out: the chain skips it."""
    detail = (f"PROVIDER_{entry['state']}: {provider} until {entry['until'][:16]}; "
              "the next provider in the chain is used")
    return {"status": "BLOCKED", "reason_code": "BUDGET_PROVIDER_CEILING",
            "primary_reason_code": "BUDGET_PROVIDER_CEILING",
            "provider_reason_codes": ["BUDGET_PROVIDER_CEILING", "PROVIDER_" + entry["state"]],
            "provider": provider, "capability_id": capability_id, "gateway_invoked": False,
            "actual_usd": None, "payload": {"detail": detail}, "artifact_ids": []}
