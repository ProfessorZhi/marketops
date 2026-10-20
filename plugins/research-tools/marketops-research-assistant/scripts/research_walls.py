#!/usr/bin/env python3
"""Automatic wall-event learning shared by every window on one data root.

Every bounded research step records what it ran into (blocked site, empty
extract, local browser down, provider error) and which rung finally worked.
Nobody has to remember to write a note. Two things come out of it:

* domain memory: once the local crawler failed on a host and the TinyFish
  fetch succeeded there, later fetches of that host start at TinyFish, in any
  window, saving a doomed attempt per page;
* a lessons report (Markdown + CSV) for people: which walls dominate, how often
  each rung rescues a page, cost per admitted route, hold reasons. Its
  suggested lessons are candidates for a human to accept into a scene pack.
"""
from __future__ import annotations

import csv
import json
import sqlite3
from collections import Counter, defaultdict
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

from research_event_journal import _root

__all__ = ["record_chain", "preferred_route", "report", "host_of"]

LOCAL_RUNG = "crawl4ai.fetch_page"
CLOUD_RUNG = "tinyfish.fetch_content"
RUNTIME_CODES = frozenset({"LOCAL_CRAWL_NOT_SAFE_TO_RUN", "LOCAL_BROWSER_NOT_INSTALLED",
                           "CAPABILITY_NOT_AVAILABLE", "CRAWL_CACHE_CONFIGURATION_REFUSED",
                           "EGRESS_REFUSED", "RESEARCH_NETWORK_DISABLED"})


def _db(root: Path) -> sqlite3.Connection:
    folder = root / "state"
    folder.mkdir(parents=True, exist_ok=True, mode=0o700)
    db = sqlite3.connect(folder / "research-walls.sqlite3", timeout=30)
    db.execute("PRAGMA journal_mode=WAL")
    db.execute("PRAGMA busy_timeout=30000")
    db.executescript("""
        CREATE TABLE IF NOT EXISTS wall_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT, at TEXT NOT NULL, run_id TEXT NOT NULL,
            task_kind TEXT NOT NULL, host TEXT NOT NULL, capability TEXT NOT NULL,
            status TEXT NOT NULL, code TEXT NOT NULL, rung INTEGER NOT NULL,
            chain_outcome TEXT NOT NULL, rescued_by TEXT NOT NULL, cost_unknown INTEGER NOT NULL);
        CREATE INDEX IF NOT EXISTS wall_events_host ON wall_events(host, capability);
    """)
    return db


def host_of(url: str) -> str:
    try:
        host = (urlsplit(url).hostname or "").casefold()
    except ValueError:
        return ""
    return host.removeprefix("www.")


def record_chain(root, *, run_id: str, task_kind: str, url: str, query: str,
                 attempts: list[dict], selected: str | None) -> int:
    """Store one row per attempt of a finished fallback chain. Never raises."""
    try:
        host = host_of(url) if url else ("search" if query else "")
        stamp = datetime.now(timezone.utc).isoformat()
        rows = [(stamp, run_id, task_kind, host, str(a.get("capability_id", "")),
                 str(a.get("status", "")),
                 str(a.get("primary_reason_code") or a.get("reason_code") or
                     a.get("decision_reason") or ""),
                 int(a.get("ordinal") or 0), "SUCCESS" if selected else "HOLD",
                 selected or "", 1 if a.get("cost_unknown") else 0) for a in attempts]
        if not rows:
            return 0
        with closing(_db(_root(root))) as db:
            db.executemany("INSERT INTO wall_events(at,run_id,task_kind,host,capability,status,"
                           "code,rung,chain_outcome,rescued_by,cost_unknown) "
                           "VALUES(?,?,?,?,?,?,?,?,?,?,?)", rows)
            db.commit()
        return len(rows)
    except Exception:  # noqa: BLE001 - learning must never break research
        return 0


def preferred_route(root, url: str, route: tuple[str, ...]) -> tuple[str, ...]:
    """Skip the local rung on a host where it failed and the cloud fetch worked."""
    host = host_of(url)
    if not host or LOCAL_RUNG not in route or CLOUD_RUNG not in route:
        return route
    if urlsplit(url).path.casefold().endswith(".pdf"):
        # The local browser renders pages; TinyFish fetch returns a PDF's text.
        return tuple(c for c in route if c != LOCAL_RUNG)
    try:
        with closing(_db(_root(root))) as db:
            local_ok = db.execute(
                "SELECT COUNT(*) FROM wall_events WHERE host=? AND capability=? AND status='OK'",
                (host, LOCAL_RUNG)).fetchone()[0]
            # Only a failure the site caused teaches anything about the site; a local
            # runtime that could not start says nothing about this host.
            marks = ",".join("?" * len(RUNTIME_CODES))
            rescued = db.execute(
                "SELECT COUNT(*) FROM wall_events WHERE host=? AND capability=? AND "
                f"status NOT IN ('OK','SKIPPED') AND rescued_by=? AND code NOT IN ({marks})",
                (host, LOCAL_RUNG, CLOUD_RUNG, *sorted(RUNTIME_CODES))).fetchone()[0]
    except Exception:  # noqa: BLE001
        return route
    if rescued and not local_ok:
        return tuple(c for c in route if c != LOCAL_RUNG)
    return route


def report(root, *, out_dir: Path | None = None, pool_id: str = "") -> dict:
    """Aggregate wall events (and pool outcomes) into Markdown + CSV for people."""
    base = _root(root)
    with closing(_db(base)) as db:
        events = db.execute("SELECT task_kind,host,capability,status,code,rung,chain_outcome,"
                            "rescued_by,cost_unknown FROM wall_events").fetchall()
    failures = [e for e in events if e[3] not in ("OK", "SKIPPED")]
    by_code = Counter(e[4] or "UNCLASSIFIED" for e in failures)
    by_capability = Counter(e[2] for e in failures)
    rescue = defaultdict(lambda: [0, 0])  # failed capability -> [chains, rescued]
    for e in failures:
        rescue[e[2]][0] += 1
        if e[6] == "SUCCESS" and e[7] and e[7] != e[2]:
            rescue[e[2]][1] += 1
    hosts = Counter(e[1] for e in failures if e[1] and e[1] != "search")
    chains = Counter((e[0], e[6]) for e in events if e[5] == 1)
    pool_view = None
    if pool_id:
        import research_pool
        try:
            pool_view = research_pool.status(base, pool_id=pool_id)
        except ValueError:
            pool_view = None
    calls = sum(1 for e in events if e[3] not in ("SKIPPED",))
    suggestions = []
    for capability, (failed, saved) in sorted(rescue.items(), key=lambda kv: -kv[1][0]):
        if failed >= 5 and saved / failed >= 0.6:
            suggestions.append(f"{capability} fails often and the next rung rescues "
                               f"{saved}/{failed}: start that host class at the next rung.")
    for code, count in by_code.most_common(3):
        if count >= 5:
            suggestions.append(f"Wall '{code}' hit {count} times: add a handling note to the scene pack.")
    if pool_view and pool_view.get("hit_rate") is not None and pool_view["hit_rate"] < 0.05:
        suggestions.append("Account hit rate below 5% (GP-06): stop this method and change source.")

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    target = Path(out_dir) if out_dir else base / "exports" / "lessons" / stamp
    target.mkdir(parents=True, exist_ok=True)
    with (target / "wall_events_summary.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["dimension", "key", "count", "rescued"])
        for code, count in by_code.most_common():
            writer.writerow(["wall_code", code, count, ""])
        for capability, (failed, saved) in rescue.items():
            writer.writerow(["failed_rung", capability, failed, saved])
        for host, count in hosts.most_common(50):
            writer.writerow(["blocked_host", host, count, ""])
    lines = ["# MarketOps lessons report", "", f"Generated {stamp}. Events: {len(events)}, "
             f"failed attempts: {len(failures)}, provider attempts: {calls}.", ""]
    lines += ["## Walls by type", ""] + [f"- {c}: {n}" for c, n in by_code.most_common(12)] + [""]
    lines += ["## Failed rung and rescue by the next rung", ""]
    lines += [f"- {c}: failed {f}, rescued {s} ({round(100 * s / f) if f else 0}%)"
              for c, (f, s) in sorted(rescue.items(), key=lambda kv: -kv[1][0])] + [""]
    lines += ["## Hosts that blocked most", ""] + [f"- {h}: {n}" for h, n in hosts.most_common(15)] + [""]
    lines += ["## Chains by task", ""] + [f"- {k}: {o} {n}" for (k, o), n in sorted(chains.items())] + [""]
    if pool_view:
        lines += ["## Pool", "", f"- progress {pool_view['progress']}, done {pool_view['by_status']['done']}, "
                  f"hold {pool_view['by_status']['hold']}, routes {pool_view['routes']}, "
                  f"hit rate {pool_view['hit_rate']}",
                  f"- provider attempts per admitted route: "
                  f"{round(calls / pool_view['routes'], 2) if pool_view['routes'] else 'n/a'}"]
        lines += [f"- hold: {h['reason']} ×{h['count']}" for h in pool_view["top_hold_reasons"]] + [""]
    lines += ["## Suggested lessons (candidates, need human review)", ""]
    lines += [f"- {s}" for s in suggestions] or ["- none yet"]
    (target / "LESSONS.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return {"directory": str(target), "events": len(events), "failed_attempts": len(failures),
            "walls": dict(by_code.most_common(8)),
            "rescue": {c: {"failed": f, "rescued": s} for c, (f, s) in rescue.items()},
            "top_blocked_hosts": dict(hosts.most_common(8)), "suggestions": suggestions,
            "pool": None if not pool_view else {k: pool_view[k] for k in
                                                ("progress", "routes", "hit_rate", "by_status")}}
