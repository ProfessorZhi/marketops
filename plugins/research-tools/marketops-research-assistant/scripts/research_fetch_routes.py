#!/usr/bin/env python3
"""Which page reader works for this Run: a probe, a log and an order.

Three readers can open a public page: Crawl4AI on this machine (free, full HTML),
TinyFish's free fetch (cleaned HTML from its servers) and DeepSeek Fetch (Markdown
from its servers, $0.75 per 1,000 pages read; failures free). Each wins somewhere:
the local browser keeps every address on a page it can open, the remote readers
open sites this machine cannot reach. Which one is best depends on the market,
so it is measured, not assumed:

* **probe** - the first ``PROBE_PAGES`` pages of a Run are read by every reader at
  once; the richest reading (most addresses, then most text, local first on a
  tie) is the one used, and every reading is logged;
* **order** - after the probe, the fallback chain tries the readers in the order
  of what they delivered in this Run (share of pages that gave addresses); the
  per-domain memory in ``research_walls`` still starts a known-hard site at the
  reader that worked there;
* **report** - per reader: pages tried, read, failed by reason, addresses found,
  and pages where only it found addresses.
"""
from __future__ import annotations

import re
import sqlite3
from collections import defaultdict
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

__all__ = ["READERS", "PROBE_PAGES", "fetch", "in_probe", "record", "order", "report", "reading"]

READERS = ("crawl4ai.fetch_page", "tinyfish.fetch_content", "deepseek.fetch_page")
PROBE_PAGES = 12

_EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")


def _db(root, write: bool):
    path = Path(root) / "state" / "fetch-routes.sqlite3"
    if not write and not path.is_file():
        return None
    path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path, timeout=30)
    db.execute("CREATE TABLE IF NOT EXISTS fetch_readings (run_id TEXT, url TEXT, reader TEXT, "
               "outcome TEXT, reason TEXT, chars INTEGER, emails INTEGER, probe INTEGER, "
               "used INTEGER, at TEXT)")
    return db


def reading(root, run_id: str, receipt: dict) -> dict:
    """Text size and distinct addresses of the page a chain receipt saved."""
    import research_evidence as evidence
    import research_lite_store as store
    for key in ("primary_artifact_ids", "provider_html_artifact_ids", "artifact_ids"):
        for artifact_id in receipt.get(key) or ():
            try:
                material = store.read_artifact(root, run_id, artifact_id)
            except (ValueError, OSError):
                continue
            text = evidence.readable_text(material["material_kind"],
                                          evidence.decode_material(material["content"]))
            return {"chars": len(text), "emails": len({e.casefold() for e in _EMAIL.findall(text)})}
    return {"chars": 0, "emails": 0}


def in_probe(root, run_id: str, limit: int | None = None) -> bool:
    limit = PROBE_PAGES if limit is None else limit
    db = _db(root, write=False)
    if db is None:
        return limit > 0
    with closing(db):
        pages = db.execute("SELECT COUNT(DISTINCT url) FROM fetch_readings WHERE run_id=? AND probe=1",
                           (run_id,)).fetchone()[0]
    return pages < limit


def record(root, run_id: str, url: str, reader: str, receipt: dict, *, probe: bool,
           used: bool, measured: dict | None = None) -> None:
    measured = measured or {"chars": 0, "emails": 0}
    ok = receipt.get("status") == "SUCCESS_CANDIDATE"
    with closing(_db(root, write=True)) as db, db:
        db.execute("INSERT INTO fetch_readings VALUES (?,?,?,?,?,?,?,?,?,?)",
                   (run_id, url[:2000], reader, "READ" if ok else "FAILED",
                    "" if ok else str(receipt.get("reason_code") or "")[:80],
                    measured["chars"], measured["emails"], int(probe), int(used),
                    datetime.now(timezone.utc).isoformat()))


def order(root, run_id: str) -> tuple[str, ...] | None:
    """Readers ranked by what they delivered in this Run, or None before any data."""
    db = _db(root, write=False)
    if db is None:
        return None
    with closing(db):
        rows = db.execute("SELECT reader, COUNT(*), SUM(outcome='READ'), SUM(emails > 0) "
                          "FROM fetch_readings WHERE run_id=? GROUP BY reader", (run_id,)).fetchall()
    if not rows:
        return None
    # Share of pages that gave addresses, then share read: one guide with 200
    # addresses must not put a reader that fails most pages first.
    score = {reader: (with_emails or 0) / tried + 0.01 * (read or 0) / tried
             for reader, tried, read, with_emails in rows}
    # A reader never tried keeps its place after the measured ones.
    return tuple(sorted(READERS, key=lambda r: (-score.get(r, -1), READERS.index(r))))


def report(root, run_id: str) -> dict:
    db = _db(root, write=False)
    if db is None:
        return {"readers": {}, "note": "no page readings recorded"}
    with closing(db):
        rows = db.execute("SELECT url, reader, outcome, reason, chars, emails, probe, used "
                          "FROM fetch_readings WHERE run_id=?", (run_id,)).fetchall()
    out = defaultdict(lambda: {"tried": 0, "read": 0, "failed": {}, "emails": 0,
                               "pages_with_emails": 0, "only_reader_with_emails": 0, "used": 0})
    by_url = defaultdict(dict)
    for url, reader, outcome, reason, chars, emails, probe, used in rows:
        row = out[reader]
        row["tried"] += 1
        row["used"] += used
        if outcome == "READ":
            row["read"] += 1
            row["emails"] += emails
            row["pages_with_emails"] += emails > 0
        else:
            row["failed"][reason or "UNKNOWN"] = row["failed"].get(reason or "UNKNOWN", 0) + 1
        if probe:
            by_url[url][reader] = emails if outcome == "READ" else -1
    for readings in by_url.values():
        found = [r for r, e in readings.items() if e > 0]
        if len(found) == 1 and len(readings) > 1:
            out[found[0]]["only_reader_with_emails"] += 1
    return {"readers": dict(out), "probe_pages": len(by_url), "order_now": order(root, run_id),
            "unit": "emails are distinct addresses in the page text each reader returned"}


def fetch(root, run_id: str, url: str, child: str, step) -> dict:
    """One page through the readers: all at once while probing, else in measured order.

    ``step(url, operation_id, routes)`` is the managed fallback chain. Probe readings
    use their own operation ids, so a repeated call replays them instead of paying
    again.
    """
    if in_probe(root, run_id):
        from concurrent.futures import ThreadPoolExecutor

        def one(index_reader):
            index, reader = index_reader
            try:
                return reader, step(url, f"{child}-r{index}", (reader,))
            except ValueError as exc:
                return reader, {"status": "HOLD", "reason_code": str(exc).split(":", 1)[0]}
        with ThreadPoolExecutor(max_workers=len(READERS)) as pool:
            receipts = dict(pool.map(one, enumerate(READERS, 1)))
        measured = {reader: (reading(root, run_id, receipt)
                             if receipt.get("status") == "SUCCESS_CANDIDATE" else {"chars": 0, "emails": 0})
                    for reader, receipt in receipts.items()}
        read = [r for r in READERS if receipts[r].get("status") == "SUCCESS_CANDIDATE"]
        # Most addresses, then most text; on a tie the earlier reader (local first).
        best = max(read, key=lambda r: (measured[r]["emails"], measured[r]["chars"], -READERS.index(r)),
                   default=None)
        for reader in READERS:
            if receipts[reader].get("replayed"):
                continue  # already logged when it was first read
            record(root, run_id, url, reader, receipts[reader], probe=True,
                   used=reader == best, measured=measured[reader])
        chosen = receipts[best] if best else receipts[READERS[0]]
        return {**chosen, "reader_probe": {r: {"status": receipts[r].get("status"), **measured[r]}
                                           for r in READERS}}
    receipt = step(url, child, order(root, run_id))
    selected = receipt.get("selected_capability")
    for attempt in () if receipt.get("replayed") else receipt.get("attempts") or ():
        reader = attempt.get("capability_id")
        if reader not in READERS or attempt.get("status") == "SKIPPED":
            continue
        used = reader == selected
        record(root, run_id, url, reader,
               receipt if used else {"status": "HOLD", "reason_code": attempt.get("reason_code")
                                     or attempt.get("decision_reason")},
               probe=False, used=used,
               measured=reading(root, run_id, receipt) if used else None)
    return receipt
