#!/usr/bin/env python3
"""Research methods tried in a Run, and which one comes next.

The Paris continuation stopped with 70 targets pending and about $1.93 left: the
one method in use (each venue's own site) found no named emails on 30 targets,
the session said "stop this method", saved a checkpoint and ended. Stopping a
method was right; ending the research was not. Here each Run keeps which methods
were tried and how they went, so the next step after a poor method is the next
method, and the research ends only when the ladder is exhausted (or the budget,
the goal, or a user decision says so).

Shared by every conversation on this computer (SQLite in the data root).
"""
from __future__ import annotations

import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

__all__ = ["LADDERS", "OUTCOMES", "record", "status"]

#: In order: cheapest and most direct first. Each entry: (name, what to do).
LADDERS = {
    "contacts": (
        ("OWN_SITE", "marketops_site_harvest on each target's own site (contact, team, "
                     "about, legal notice pages)."),
        ("OPERATOR", "Find who runs the target (municipality sports department, school, "
                     "association, club, management company, federation) from the venue's "
                     "listing, address or legal notice; import the operator as an "
                     "organisation and harvest its site. Public venues rarely have their "
                     "own site; their staff are listed by the operator."),
        ("ROLE_SEARCH", "Serper searches for the wanted role titles at each operator, in the "
                        "local language (e.g. 'directeur des sports', 'responsable des "
                        "équipements sportifs' + town), and site: searches on the operator's "
                        "domain; harvest the pages found."),
        ("OFFICIAL_DIRECTORIES", "Official staff and organisation directories (public "
                                 "administration directories, federation and league club "
                                 "directories, municipal organisation charts)."),
        ("DOCUMENTS", "PDFs that name officers: council minutes, annual reports, tenders and "
                      "contract notices, organisation charts. Read them with "
                      "marketops_research_step and marketops_material_excerpt."),
        ("TINYFISH_AGENT", "TinyFish's paid browser agent for the hard pages that are left, "
                           "only if the user allowed it in the brief."),
    ),
    "inventory": (
        ("OFFICIAL_DATASET", "Page through the official or open-data register to the end; "
                             "import every page with its listed count."),
        ("DIRECTORY_LISTINGS", "Directories and federation listings of the venue type; import "
                               "with listed counts."),
        ("MAPS", "SerpApi google_maps by area grid and venue type to fill gaps."),
        ("SEARCH_GRID", "Serper searches per town or district and venue type for what the "
                        "lists miss."),
    ),
}
OUTCOMES = ("started", "productive", "exhausted", "not_applicable")


def _db(root) -> sqlite3.Connection:
    path = Path(root) / "state" / "research-methods.sqlite3"
    path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path, timeout=30)
    db.execute("CREATE TABLE IF NOT EXISTS methods (run_id TEXT, ladder TEXT, method TEXT, "
               "outcome TEXT, note TEXT, at TEXT, PRIMARY KEY (run_id, ladder, method))")
    return db


def record(root, run_id: str, method: str, outcome: str, note: str = "") -> dict:
    """Say how a method went for this Run; returns the updated status."""
    ladder = next((name for name, steps in LADDERS.items()
                   if method in {step for step, _ in steps}), None)
    if ladder is None:
        raise ValueError("METHOD_UNKNOWN: one of " + ", ".join(
            step for steps in LADDERS.values() for step, _ in steps))
    if outcome not in OUTCOMES:
        raise ValueError("METHOD_OUTCOME_INVALID: one of " + ", ".join(OUTCOMES))
    with closing(_db(root)) as db, db:
        db.execute("INSERT OR REPLACE INTO methods VALUES (?,?,?,?,?,?)",
                   (run_id, ladder, method, outcome, str(note)[:300],
                    datetime.now(timezone.utc).isoformat(timespec="seconds")))
    return status(root, run_id)


def status(root, run_id: str) -> dict:
    """Per ladder: what was tried, the next method to use, and whether all are done."""
    path = Path(root) / "state" / "research-methods.sqlite3"
    seen: dict[tuple[str, str], tuple[str, str]] = {}
    if path.is_file():
        with closing(_db(root)) as db:
            for ladder, method, outcome, note in db.execute(
                    "SELECT ladder, method, outcome, note FROM methods WHERE run_id=?", (run_id,)):
                seen[(ladder, method)] = (outcome, note)
    out = {}
    for ladder, steps in LADDERS.items():
        tried = [{"method": m, "outcome": seen[(ladder, m)][0], "note": seen[(ladder, m)][1]}
                 for m, _ in steps if (ladder, m) in seen]
        done = {t["method"] for t in tried if t["outcome"] in {"exhausted", "not_applicable"}}
        current = next((m for m, _ in steps if m not in done), None)
        out[ladder] = {"tried": tried,
                       "next_method": current,
                       "next_method_how": dict(steps).get(current, ""),
                       "all_exhausted": current is None}
    return out
