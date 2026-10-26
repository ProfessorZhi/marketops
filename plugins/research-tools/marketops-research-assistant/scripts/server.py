#!/usr/bin/env python3
"""MarketOps research MCP: nine tools, one project folder, no gates.

project  open/create a project folder, write the brief, see progress and costs
search   Serper + SerpApi + DeepSeek + TinyFish at once (web or maps)
fetch    read one page with Crawl4AI + TinyFish + DeepSeek at once; data files download
crawl    background: read many organisation sites (home + contact/people pages)
browse   background: TinyFish Agent for pages that need clicks
judge    Jev answers one multiple-choice question per row of any pool (in scope? own site?)
grade    Jev grades contacts A/B/C/D/X from the brief; or set grades yourself
run      background long research: crawl, grade, dig the gaps round after round, export
export   one Excel workbook (+ Bruce's 18-column routes CSV) for the salesperson

Headless: python server.py run <project> [--rounds N] [--agent]
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
os.environ.setdefault("CRAWL4AI_NO_CLOUD_NOTICE", "1")  # its banner would corrupt the stdio channel

import mo_budget  # noqa: E402
import mo_config  # noqa: E402
import mo_export  # noqa: E402
import mo_grade  # noqa: E402
import mo_providers  # noqa: E402
import mo_run  # noqa: E402
import mo_workspace as ws  # noqa: E402

try:
    VERSION = json.loads((Path(__file__).resolve().parents[1] / ".claude-plugin" / "plugin.json")
                         .read_text(encoding="utf-8"))["version"]
except (OSError, ValueError, KeyError):
    VERSION = "0.6"


def _err(exc: Exception) -> dict:
    return {"error": mo_config.scrub(f"{type(exc).__name__}: {exc}")[:300]}


def project(name: str, brief: dict | None = None) -> dict:
    """Open (or create) a project folder and report where it stands.

    brief (optional, saved as brief.json): {"goal": "...", "region": "...",
    "grades": {"A": "...", "B": "...", "C": "...", "D": "...", "X": "..."}, "exclude": "..."}.
    Grades describe, in plain words and the market's own job titles, what counts as each
    grade; leave any out to use Bruce's default. Returns pools, contacts by grade, crawl and
    agent progress, costs, and account balances with their red lines.
    """
    try:
        pdir = ws.project_dir(name)
        if brief:
            (pdir / "brief.json").write_text(json.dumps(brief, ensure_ascii=False, indent=1), encoding="utf-8")
        out = ws.status(pdir)
        out["version"] = VERSION
        out["providers"] = mo_config.configured() + ["crawl4ai"]
        out["accounts"] = mo_budget.accounts()
        return out
    except Exception as exc:  # noqa: BLE001
        return _err(exc)


def search(query: str, project: str = "", engines: list[str] | None = None, country: str = "",
           lang: str = "", n: int = 10, maps: bool = False) -> dict:
    """Search the web with every engine at once and merge by URL (found_by says who found it).

    engines: any of serper, serpapi, deepseek, tinyfish (default all with a key).
    maps=True: Google Maps places (name, address, phone, website) via Serper and SerpApi.
    country/lang: two-letter codes, e.g. "ch", "de".
    """
    try:
        pdir = ws.project_dir(project) if project else None
        out = mo_providers.search(query, engines, country, lang, n, maps, pdir)
        out["results"] = out["results"][: max(5, n * 2)]
        return out
    except Exception as exc:  # noqa: BLE001
        return _err(exc)


def fetch(project: str, url: str, readers: list[str] | str | None = None, org_id: str = "",
          org: str = "", max_chars: int = 3000) -> dict:
    """Read one page with Crawl4AI, TinyFish and DeepSeek at the same time and save every version.

    Returns each reader's outcome, the emails/phones found (with the lines around them),
    same-site links that look like contact/people pages, and the page text (max_chars;
    0 for none). With org_id/org the contacts are added to contacts.csv for that
    organisation. readers: a subset of crawl4ai, tinyfish, deepseek, or "download" for a
    data file (CSV/JSON/XLSX/PDF exports are downloaded automatically).
    """
    try:
        out = ws.fetch(ws.project_dir(project), url, readers, org_id, org)
        out["text"] = out["text"][:max_chars]
        out["contacts"] = [{k: c[k] for k in ("kind", "value", "mailbox", "same_site", "found_by", "quote")}
                           for c in out["contacts"]]
        return out
    except Exception as exc:  # noqa: BLE001
        return _err(exc)


def crawl(project: str, targets: list[dict] | str = "orgs.csv", max_pages: int = 15,
          readers: list[str] | None = None, workers: int = 6) -> dict:
    """Read many organisation sites in the background and return at once.

    targets: "orgs.csv" (every row with id, name, website) or a list of
    {"id", "name", "website"} (optional "priority": high/normal/low goes first). For each
    site: the home page, then the best-scoring same-site pages two levels deep (contact,
    imprint, team, board, staff, venue pages and PDFs), up to max_pages; every email and
    phone goes to contacts.csv. Saved pages are never fetched twice; finished organisations
    are skipped, so calling again resumes. Progress: project(name). For the whole job
    (crawl, grade, dig the gaps, export) use run instead.
    """
    try:
        pdir = ws.project_dir(project)
        rows = ws.read_csv(pdir / targets) if isinstance(targets, str) else targets
        if not rows:
            return {"error": f"no targets (is {targets} empty?)"}
        return ws.crawl(pdir, rows, max_pages, readers, workers)
    except Exception as exc:  # noqa: BLE001
        return _err(exc)


def browse(project: str, url: str, goal: str = "", org_id: str = "", org: str = "") -> dict:
    """TinyFish Agent opens the page in a real browser (clicks tabs, 'show more') in the background.

    Paid per step (about $0.016). Use it when readers return no text or the contacts sit
    behind clicks. Each URL is submitted once per project. The result is saved as a page and
    its contacts added; project(name) shows when it is done.
    """
    try:
        return ws.browse(ws.project_dir(project), url, goal, org_id, org)
    except Exception as exc:  # noqa: BLE001
        return _err(exc)


def grade(project: str, set: list[dict] | None = None, regrade: bool = False, limit: int = 0) -> dict:
    """Grade email candidates in contacts.csv.

    Without set: Jev grades ungraded rows from each row's quote against the brief's grades
    (any language), all of them unless limit. A/B rows that need a look (low confidence,
    address shared with other organisations, page on another site) get review=yes. With
    set: your own judgement, a list of {"value": email, "org_id"?, "grade"?, "person"?,
    "role"?, "note"?}; rows you set are never regraded by Jev.
    """
    try:
        pdir = ws.project_dir(project)
        return mo_grade.manual(pdir, set) if set else mo_grade.auto(pdir, limit, regrade)
    except Exception as exc:  # noqa: BLE001
        return _err(exc)


def judge(project: str, file: str, question: str, options: dict, column: str,
          fields: list[str] | None = None, limit: int = 300, redo: bool = False) -> dict:
    """Let Jev make a repeated decision over the rows of a project CSV (venues.csv, orgs.csv, ...).

    question: one plain question; options: {answer: what it means}, e.g. {"yes": "outdoor
    natural grass football pitch", "no": "indoor, artificial or not football", "unsure": "..."}.
    The answer goes into `column` (+ `column`_conf). fields: the columns Jev sees (default
    all). Unanswered rows only, unless redo. Nothing is dropped: filter on the column later.
    """
    try:
        return mo_grade.judge(ws.project_dir(project), file, question, options, column, fields, limit, redo)
    except Exception as exc:  # noqa: BLE001
        return _err(exc)


def run(project: str, rounds: int = 6, max_pages: int = 15, goal_ab_per_100: float | None = None,
        agent: bool = False) -> dict:
    """Long research in the background, without stopping: the whole orgs.csv to graded contacts.

    Round 0 crawls every site and grades. Each further round takes the organisations still
    without A/B and digs a different way (the market's words from brief.focus.search_terms,
    inside the site and its PDFs, third-party directories from focus.directory_terms, maps,
    broad searches), reads what it finds and grades again. Stops at goal_ab_per_100 (A+B
    routes per 100 organisations), after two rounds with no new A/B organisation, or after
    `rounds`; then exports. agent=True also lets the TinyFish Agent open pages whose email
    buttons hide the address (paid per step). Resumable; progress in project(name).run.
    """
    try:
        return mo_run.run(ws.project_dir(project), rounds=rounds, max_pages=max_pages,
                          goal_ab_per_100=goal_ab_per_100, agent=agent)
    except Exception as exc:  # noqa: BLE001
        return _err(exc)


def export(project: str) -> dict:
    """Write the Excel workbook (summary with clearly defined metrics, per-batch table, contacts A
    first with page, quote and review flag, organisations, venues, costs, X rows) and Bruce's
    18-column routes CSV."""
    try:
        return mo_export.export(ws.project_dir(project))
    except Exception as exc:  # noqa: BLE001
        return _err(exc)


TOOLS = (project, search, fetch, crawl, browse, judge, grade, run, export)


def check() -> int:
    """Installer self-check: packages import, which keys are present (names only)."""
    problems = []
    for mod in ("mcp", "crawl4ai", "typesafe_sdk", "httpx"):
        try:
            __import__(mod)
        except Exception as exc:  # noqa: BLE001
            problems.append(f"{mod}: {exc}")
    keys = mo_config.configured()
    print(f"MarketOps {VERSION}: keys for {', '.join(keys) or 'nothing yet'}; "
          f"projects in {mo_config.projects_root()}")
    for p in problems:
        print("  missing package -", p)
    if not any(k in keys for k in ("serper", "serpapi", "deepseek", "tinyfish")):
        print("  no search key yet: research needs at least one of Serper, SerpApi, DeepSeek, TinyFish")
    return 1 if problems else 0


def headless(argv: list[str]) -> int:
    """python server.py run <project> [--rounds N] [--max-pages N] [--goal X] [--agent]"""
    import argparse
    ap = argparse.ArgumentParser(prog="server.py run")
    ap.add_argument("project")
    ap.add_argument("--rounds", type=int, default=6)
    ap.add_argument("--max-pages", type=int, default=15)
    ap.add_argument("--goal", type=float, default=None)
    ap.add_argument("--agent", action="store_true")
    a = ap.parse_args(argv)
    state = mo_run.run_sync(ws.project_dir(a.project), rounds=a.rounds, max_pages=a.max_pages,
                            goal_ab_per_100=a.goal, agent=a.agent)
    print(json.dumps({k: state.get(k) for k in ("stopped_because", "result", "export", "history")},
                     ensure_ascii=False, indent=1))
    return 0


def main() -> None:
    if "--check" in sys.argv:
        raise SystemExit(check())
    if len(sys.argv) > 1 and sys.argv[1] == "run":
        raise SystemExit(headless(sys.argv[2:]))
    from mcp.server import MCPServer
    server = MCPServer("MarketOps Research", version=VERSION)
    for fn in TOOLS:
        server.tool()(fn)
    server.run(transport="stdio")


if __name__ == "__main__":
    main()
