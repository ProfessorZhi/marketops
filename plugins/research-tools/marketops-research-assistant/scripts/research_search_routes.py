#!/usr/bin/env python3
"""Which search route finds what: a recall log and a per-route quality report.

Every multi-route search (provider='all') records each route's hits. The report
then answers, per route:
  * how many queries it answered, failed or sat out (no money, red line);
  * how many pages it found, and how many no other route found;
  * how many of the sites behind A/B routes it found, and how many only it found.
The last line is the one that matters: a route that only repeats the others costs
money without adding contacts, and a route that alone finds the A/B sites is worth
keeping even when it is the dearest.
"""
from __future__ import annotations

import sqlite3
from collections import defaultdict
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

__all__ = ["record", "report"]


def _db(root, write: bool):
    path = Path(root) / "state" / "search-routes.sqlite3"
    if not write:
        return sqlite3.connect(path.as_uri() + "?mode=ro", uri=True) if path.is_file() else None
    path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path, timeout=30)
    db.execute("CREATE TABLE IF NOT EXISTS route_hits (run_id TEXT, query TEXT, route TEXT, "
               "url TEXT, host TEXT, rank INTEGER, at TEXT)")
    db.execute("CREATE TABLE IF NOT EXISTS route_calls (run_id TEXT, query TEXT, route TEXT, "
               "outcome TEXT, hits INTEGER, at TEXT)")
    return db


def _host(url: str) -> str:
    return (urlsplit(url).hostname or "").casefold().removeprefix("www.")


def record(root, run_id: str, query: str, answers: dict) -> None:
    """One multi-route search: each route's outcome and hits."""
    from research_mcp import _hits
    now = datetime.now(timezone.utc).isoformat()
    with closing(_db(root, write=True)) as db, db:
        for route, answer in answers.items():
            if "error" in answer:
                text = str(answer["error"])
                outcome = ("NO_MONEY_OR_RED_LINE" if "BUDGET" in text or "BALANCE" in text
                           or "NO_BALANCE" in text else "FAILED")
                db.execute("INSERT INTO route_calls VALUES (?,?,?,?,?,?)",
                           (run_id, query[:300], route, outcome, 0, now))
                continue
            hits = _hits(answer)
            db.execute("INSERT INTO route_calls VALUES (?,?,?,?,?,?)",
                       (run_id, query[:300], answer.get("substituted_by", route), "OK", len(hits), now))
            for rank, hit in enumerate(hits, 1):
                url = hit.get("url") or hit.get("link") or ""
                if isinstance(url, str) and url.startswith(("http://", "https://")):
                    db.execute("INSERT INTO route_hits VALUES (?,?,?,?,?,?,?)",
                               (run_id, query[:300], answer.get("substituted_by", route),
                                url[:2000], _host(url), rank, now))


def report(root, run_id: str, ab_hosts: set[str] | None = None) -> dict:
    """Per route: calls by outcome, pages found, unique pages, A/B sites found."""
    db = _db(root, write=False)
    if db is None:
        return {"routes": {}, "note": "no multi-route searches recorded"}
    with closing(db):
        calls = db.execute("SELECT route, outcome, COUNT(*), SUM(hits) FROM route_calls "
                           "WHERE run_id=? GROUP BY route, outcome", (run_id,)).fetchall()
        hits = db.execute("SELECT route, url, host FROM route_hits WHERE run_id=?", (run_id,)).fetchall()
    by_url, by_host = defaultdict(set), defaultdict(set)
    for route, url, host in hits:
        by_url[url].add(route)
        by_host[host].add(route)
    out = defaultdict(lambda: {"calls": {}, "pages": 0, "unique_pages": 0,
                               "ab_sites": 0, "unique_ab_sites": 0})
    for route, outcome, count, _ in calls:
        out[route]["calls"][outcome] = count
    for url, routes in by_url.items():
        for route in routes:
            out[route]["pages"] += 1
            out[route]["unique_pages"] += len(routes) == 1
    for host in ab_hosts or ():
        routes = by_host.get(host, set())
        for route in routes:
            out[route]["ab_sites"] += 1
            out[route]["unique_ab_sites"] += len(routes) == 1
    return {"routes": dict(out),
            "ab_sites_total": len(ab_hosts or ()),
            "ab_sites_found_by_search": sum(1 for h in ab_hosts or () if h in by_host),
            "unit": "pages are distinct URLs; A/B sites are hosts of the pages that gave A or B routes"}
