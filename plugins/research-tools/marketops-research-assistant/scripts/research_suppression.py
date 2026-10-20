#!/usr/bin/env python3
"""One suppression (No-Go / unsubscribe / complaint) list shared by every Run.

Rules that came from Member A's runs:

* An address already on the No-Go list was mailed again and complained twice.
  The list existed; nothing stood between it and the send queue. Here the list
  is read inside the delivery gate, so a suppressed route never reaches a
  client sheet.
* "No list" and "checked and clean" must not look alike. Every check reports
  how many entries it read; zero entries is NO_LIST, never CLEAN.
* Two copies of the list drifted apart (6,931 rows vs 423, 71 shared). There is
  one table under the data root; every conversation reads and writes it.

Entries are an exact email or a whole domain (``@example.org`` or
``example.org``). Removing an entry is a person's decision and is not offered.
"""
from __future__ import annotations

import re
import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

__all__ = ["add_entries", "check", "status", "normalize"]

REASONS = frozenset({"unsubscribe", "complaint", "no_go", "competitor", "bounce", "other"})
MAX_ENTRIES = 50_000
EMAIL = re.compile(r"^[^@\s]+@[a-z0-9.-]+\.[a-z]{2,}$")
DOMAIN = re.compile(r"^[a-z0-9-]+(\.[a-z0-9-]+)*\.[a-z]{2,}$")


def _db(root: Path) -> sqlite3.Connection:
    folder = root / "state"
    folder.mkdir(parents=True, exist_ok=True, mode=0o700)
    db = sqlite3.connect(folder / "suppression.sqlite3", timeout=30)
    db.execute("PRAGMA journal_mode=WAL")
    db.execute("PRAGMA busy_timeout=30000")
    db.executescript("""
        CREATE TABLE IF NOT EXISTS suppression (
            value TEXT PRIMARY KEY, kind TEXT NOT NULL, reason TEXT NOT NULL,
            source TEXT NOT NULL, note TEXT NOT NULL DEFAULT '', added_at TEXT NOT NULL);
    """)
    return db


def normalize(value: str) -> tuple[str, str] | None:
    """('email', 'a@b.org') or ('domain', 'b.org'); None when it is neither."""
    text = str(value or "").strip().casefold()
    if text.startswith("mailto:"):
        text = text[7:]
    if text.startswith("@"):
        text = text[1:]
        return ("domain", text) if DOMAIN.fullmatch(text) else None
    if EMAIL.fullmatch(text):
        return ("email", text)
    if text.startswith("www."):
        text = text[4:]
    return ("domain", text) if DOMAIN.fullmatch(text) else None


def add_entries(root: Path, *, entries: list[str], reason: str, source: str,
                note: str = "") -> dict:
    """Add entries; existing ones keep their first reason. Returns the reconciliation."""
    if reason not in REASONS:
        raise ValueError("SUPPRESSION_REASON_INVALID: " + ", ".join(sorted(REASONS)))
    if not str(source).strip():
        raise ValueError("SUPPRESSION_SOURCE_REQUIRED: say where the list came from")
    if not isinstance(entries, list) or len(entries) > MAX_ENTRIES:
        raise ValueError(f"SUPPRESSION_ENTRIES_INVALID: a list of at most {MAX_ENTRIES}")
    now = datetime.now(timezone.utc).isoformat()
    added, already, invalid = 0, 0, []
    with closing(_db(root)) as db, db:
        for raw in entries:
            parsed = normalize(raw)
            if parsed is None:
                invalid.append(str(raw)[:120])
                continue
            kind, value = parsed
            cursor = db.execute(
                "INSERT OR IGNORE INTO suppression VALUES (?,?,?,?,?,?)",
                (value, kind, reason, str(source)[:300], str(note)[:300], now))
            if cursor.rowcount:
                added += 1
            else:
                already += 1
    return {"submitted": len(entries), "added": added, "already_present": already,
            "invalid": len(invalid), "invalid_samples": invalid[:10],
            "reconciled": added + already + len(invalid) == len(entries),
            "total_entries": status(root)["entries"]}


def status(root: Path) -> dict:
    with closing(_db(root)) as db:
        rows = db.execute("SELECT kind, reason, COUNT(*) FROM suppression "
                          "GROUP BY kind, reason").fetchall()
    total = sum(n for _, _, n in rows)
    by_reason: dict[str, int] = {}
    for _, reason, n in rows:
        by_reason[reason] = by_reason.get(reason, 0) + n
    return {"entries": total, "by_reason": by_reason,
            "list_status": "LOADED" if total else "NO_LIST"}


def check(root: Path, values: list[str]) -> dict:
    """Which values are suppressed. An empty list is NO_LIST, never CLEAN."""
    with closing(_db(root)) as db:
        entries = {value: (kind, reason) for value, kind, reason in
                   db.execute("SELECT value, kind, reason FROM suppression")}
    hits: dict[str, dict] = {}
    for raw in values:
        parsed = normalize(raw)
        if parsed is None or parsed[0] != "email":
            continue
        email = parsed[1]
        domain = email.rsplit("@", 1)[1]
        parts = domain.split(".")
        candidates = [email] + [".".join(parts[i:]) for i in range(len(parts) - 1)]
        for candidate in candidates:
            if candidate in entries:
                kind, reason = entries[candidate]
                hits[raw] = {"matched": candidate, "match_kind": kind, "reason": reason}
                break
    if not entries:
        list_status = "NO_LIST"
    else:
        list_status = "HITS_EXCLUDED" if hits else "CHECKED_CLEAN"
    return {"list_status": list_status, "entries_checked_against": len(entries),
            "values_checked": len(values), "hits": hits}
