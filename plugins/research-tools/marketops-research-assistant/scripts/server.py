#!/usr/bin/env python3
"""MarketOps research MCP: ten tools, one project folder, no gates, many agents at once.

project  open/create a project folder, write the brief, see progress and costs
search   Serper + SerpApi + DeepSeek + TinyFish at once (web or maps)
pool     build venues.csv / orgs.csv from a saved open-data file (filter, map, group by operator)
fetch    read one page with Crawl4AI + TinyFish + DeepSeek at once; data files download
browse   background: TinyFish Agent for pages that need clicks
judge    Jev answers one multiple-choice question per row of any pool (in scope? own site?)
grade    Jev grades contacts A/B/C/D/X from the brief; or set grades yourself
run      the pipeline in background processes: discover sources and organisations, read their
         sites, grade, dig the gaps, corroborate - all at once - then export
tasks    shared work queue: the coordinator plans packages, worker agents claim and finish them
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
import mo_pool  # noqa: E402
import mo_providers  # noqa: E402
import mo_run  # noqa: E402
import mo_tasks  # noqa: E402
import mo_workspace as ws  # noqa: E402

try:
    VERSION = json.loads((Path(__file__).resolve().parents[1] / ".claude-plugin" / "plugin.json")
                         .read_text(encoding="utf-8"))["version"]
except (OSError, ValueError, KeyError):
    VERSION = "0.6"


def _err(exc: Exception) -> dict:
    return {"error": mo_config.scrub(f"{type(exc).__name__}: {exc}")[:300]}


def project(name: str, brief: dict | None = None, playbook: dict | None = None,
            journal: dict | list | None = None, agent: str = "") -> dict:
    """Open (or create) a project folder and report where it stands.

    brief (optional, saved as brief.json): {"goal": "...", "region": "...", "country": "FR",
    "language": "fr", "scenario": "...", "grades": {"A": "...", ...}, "exclude": "...",
    "address_rules": {"info": "X"}, "routes_per_org": 3, "focus": {...}}.
    Grades describe, in plain words and the market's own job titles, what counts as each
    grade; leave any out to use Bruce's default. Returns pools, contacts by grade, run and
    agent progress, costs, account balances with their red lines, and `needs_you`: the numbered
    list of things only the user can decide - present it in one message, take one reply.
    `stages` shows the work waiting at each stage of the chain (sources, organisations not
    read, done without a confirmed A/B and waiting for an agent, rows to review) - staff the
    stage where it piles up.
    playbook (optional, merged into playbook.json): what you learned about finding people for
    one kind of organisation, {segment: {"paths": ["/annuaire", ...], "queries": ['"{name}"
    {city} responsable', ...], "roles": ["chef d'établissement", ...], "notes": "..."}};
    segment is an organisation's segment/type/category/source ("*" for all). The pipeline reads
    the paths first and runs the queries first for every organisation of that kind; sweep the
    rest of the segment again with run(project, orgs=[...], again=True).
    journal (optional): what you ran into, for the run's retrospective (RETRO.md at export) and
    the plugin's next changes - {"type": "blocker"|"smooth"|"decision"|"idea"|"retro", "text":
    "...", "evidence": "file / sheet / count"} or a list; agent: your name.
    """
    try:
        pdir = ws.project_dir(name)
        if brief:
            (pdir / "brief.json").write_text(json.dumps(brief, ensure_ascii=False, indent=1), encoding="utf-8")
        if playbook:
            ws.update_playbook(pdir, playbook)
        if journal:
            import mo_journal
            mo_journal.add(pdir, journal, agent)
        out = ws.status(pdir)
        out["version"] = VERSION
        out["providers"] = mo_config.configured() + ["crawl4ai"]
        out["accounts"] = mo_budget.accounts()
        import mo_library
        out["library"] = mo_library.stats()
        out["needs_you"] = needs_you(pdir, out)
        return out
    except Exception as exc:  # noqa: BLE001
        return _err(exc)


def needs_you(pdir, status: dict) -> list[str]:
    """Everything waiting on the user's judgement, as one numbered list (Bruce: "需要我人工复合的
    内容清单发本窗口，我这里批量回复你") - never one question at a time."""
    items = []
    b = ws.brief(pdir)
    if not b.get("goal"):
        items.append("The brief has no goal: what is sold, and who should be found?")
    if b and not (b.get("country") and b.get("language")):
        items.append("Set country and language in the brief (search engines answer as US / English without them).")
    rows = ws.read_contacts(pdir)
    review = [r for r in rows if r.get("grade") in ("A", "B") and r.get("review") == "yes"]
    if review:
        kinds: dict[str, int] = {}
        for r in review:
            why = str(r.get("review_reason") or "")
            k = ("unsure grade" if "unsure" in why else "also listed for other organisations" if "also listed" in why
                 else "page on another site" if "site" in why else "other")
            kinds[k] = kinds.get(k, 0) + 1
        split = ", ".join(f"{k} {n}" for k, n in sorted(kinds.items(), key=lambda kv: -kv[1]))
        items.append(f"{len(review)} A/B rows are marked review ({split}): accept or correct them with "
                     "grade(set=[...]); unanswered, they are delivered marked as review.")
    unsure = sum(o.get("scope") == "unsure" for o in ws.read_csv(pdir / "orgs.csv"))
    if unsure:
        items.append(f"{unsure} organisations could not be placed in or out of scope (orgs.csv scope=unsure).")
    for st in mo_run.run_states(pdir):
        for src, y in ((st.get("progress") or {}).get("source_yield") or st.get("source_yield") or {}).items():
            if y.get("turns") == 0:
                items.append(f"Source '{src}' gave A/B for {y['with_ab']} of {y['done']} organisations (<5%): "
                             "keep it at the back of the queue, or drop the rest of it?")
        if st.get("stage") == "failed" or (st.get("restarts") or 0) >= mo_run.MAX_RESTARTS:
            items.append(f"Run shard {st.get('shard')} stopped: {st.get('error') or 'restarted too often'}.")
    for name, acc in (status.get("accounts") or {}).items():
        if not isinstance(acc, dict):
            continue
        # the skip mark lives in the run's processes, so read the balance here as well
        bal, line = (acc.get("balance") or {}).get("left") if isinstance(acc.get("balance"), dict) else acc.get("balance"), \
            acc.get("red_line")
        low = isinstance(bal, (int, float)) and (bal <= 0 or (isinstance(line, (int, float)) and bal <= line))
        if acc.get("skipped") or low:
            items.append(f"{name} is out of credit or at its red line (balance {bal}): top it up or leave it off"
                         + (" - search runs on the slower engines without it." if name in ("serper", "serpapi") else "."))
    return [f"{n}. {t}" for n, t in enumerate(dict.fromkeys(items), 1)]


def search(query: str, project: str = "", engines: list[str] | None = None, country: str = "",
           lang: str = "", n: int = 10, maps: bool = False) -> dict:
    """Search the web with every engine at once and merge by URL (found_by says who found it).

    engines: any of serper, serpapi, deepseek, tinyfish (default: by the project's provider
    weights, brief.providers.search; the held-back engines join when results are thin).
    maps=True: Google Maps places (name, address, phone, website) via Serper and SerpApi.
    country/lang: two-letter codes, e.g. "ch", "de".
    """
    try:
        pdir = ws.project_dir(project) if project else None
        out = mo_providers.search(query, engines, country, lang, n, maps, pdir,
                                  weights=ws.weights(pdir, "search") if pdir else None)
        out["results"] = out["results"][: max(5, n * 2)]
        return out
    except Exception as exc:  # noqa: BLE001
        return _err(exc)


def fetch(project: str, url: str, readers: list[str] | str | None = None, org_id: str = "",
          org: str = "", max_chars: int = 3000, refresh: bool = False) -> dict:
    """Read one page with Crawl4AI, TinyFish and DeepSeek at the same time and save every version.

    Returns each reader's outcome, the emails/phones found (with the lines around them),
    same-site links that look like contact/people pages, and the page text (max_chars;
    0 for none). With org_id/org the contacts are added to that organisation's contacts.
    readers: a subset of crawl4ai, tinyfish, deepseek, or "download" for a
    data file (CSV/JSON/XLSX/PDF exports are downloaded automatically).
    A page or site that failed repeatedly is set aside for a while (site health); refresh=True
    reads it anyway, now, and a success restores it.
    """
    try:
        out = ws.fetch(ws.project_dir(project), url, readers, org_id, org, refresh=refresh)
        out["text"] = out["text"][:max_chars]
        out.pop("content_links", None)
        out.pop("scored_links", None)   # the same links as `links`, with scores: half the size for the agent
        out["contacts"] = [{k: c[k] for k in ("kind", "value", "mailbox", "same_site", "found_by", "quote")}
                           for c in out["contacts"]]
        return out
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
    """Grade the project's email candidates.

    Without set: Jev grades ungraded rows from each row's quote against the brief's grades
    (any language), all of them unless limit. A/B rows that need a look (low confidence,
    address shared with other organisations, page on another site) get review=yes. With
    set: your own judgement, a list of {"value": email, "org_id"?, "grade"?, "person"?,
    "role"?, "note"?}; rows you set are never regraded by Jev. Spot check: {"value", "org_id",
    "checked": "ok"|"wrong", "note"?} records that you opened the page and the row holds (or
    not) - the workbook's 抽检 sheet lists the sample and the precision.
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


def run(project: str, rounds: int = 6, max_pages: int = 15, goal_ab_org_share: float | None = None,
        agent: bool = False, stop: bool = False, workers: int = 24, shards: int = 1,
        follow: bool = False, pool_done: bool = False, orgs: list[str] | None = None, again: bool = False) -> dict:
    """Long research as a pipeline in its own background process(es) (survives this session).

    Each organisation in orgs.csv flows on its own, many at once: its site is read, a
    background grader grades the new addresses, then - while it has no confirmed A/B - up to
    `rounds` dig rounds, each a different way (brief.focus.search_terms, inside the site and
    its PDFs, focus.directory_terms directories, maps, broad searches), then the evidence for
    its A/B addresses is corroborated. follow=True keeps taking organisations appended to
    orgs.csv while agents still build the pool, until run(pool_done=True). Ends when every
    organisation is done, at goal_ab_org_share (% of organisations with A/B) or on stop; the
    workbook is refreshed every half hour and at the end. agent=True lets the TinyFish Agent
    open pages whose email buttons hide the address (paid per step). stop=True ends a run
    gracefully (it exports what it has). Resumable. project(name).run shows stage, round,
    progress (organisations per minute, minutes left, cost per organisation) and results.
    workers: organisations worked at the same time per process (each read by several providers
    at once); shards: split orgs.csv between that many processes running in parallel.
    orgs=[ids]: a batch for an agent - sweep just these organisations in the background, beside
    any main run (again=True: sweep them anew with the playbook as it is now); then read each
    one's result with orgs(project, ids=[...]). max_pages is a floor: a site keeps being read
    while its pages bring new addresses (up to 40).
    """
    try:
        return mo_run.run(ws.project_dir(project), rounds=rounds, max_pages=max_pages,
                          goal_ab_org_share=goal_ab_org_share, agent=agent, stop=stop, workers=workers,
                          shards=shards, follow=follow, pool_done=pool_done, orgs=orgs, again=again)
    except Exception as exc:  # noqa: BLE001
        return _err(exc)


def tasks(project: str, action: str = "status", kind: str = "", size: int = 15, agent: str = "",
          task_id: str = "", note: str = "", title: str = "", release: bool = False,
          verdicts: list[dict] | None = None) -> dict:
    """Shared work queue so several agents work one project in parallel.

    Coordinator: action="plan", kind="dig" (organisations still without a confirmed A/B),
    "review" (A/B rows to check), "evidence" (A/B rows with weak or medium evidence),
    "browse" (pages whose email buttons hide addresses), cut into packages of `size`;
    action="add", title="..." for any other job (a source
    family, a district, a federation list). Worker: action="claim", agent="<your name>"
    (optionally kind) returns one package with its organisations or rows and how to do it;
    work it with the other tools, then action="done", task_id, note="what you found/tried"
    (release=True hands it back undone). A claim untouched for 40 minutes is offered again.
    While an agent holds a package, the pipeline leaves its organisations alone.
    action="verdict", verdicts=[{org_id, verdict: routes|partial|none|user, found, where, why}]:
    one line per organisation you finished - what you found, on which kind of page, why that
    grade (goes to verdicts.csv and the workbook).
    """
    try:
        pdir = ws.project_dir(project)
        if action == "plan":
            return mo_tasks.plan(pdir, kind, size)
        if action == "add":
            return mo_tasks.add(pdir, title, note, kind or "custom")
        if action == "claim":
            return mo_tasks.claim(pdir, agent or "worker", kind)
        if action == "done":
            return mo_tasks.done(pdir, task_id, note, release)
        if action == "verdict":
            return {"recorded": ws.add_verdicts(pdir, verdicts or [], agent), **ws.stages(pdir)}
        return mo_tasks.summary(pdir)
    except Exception as exc:  # noqa: BLE001
        return _err(exc)


def orgs(project: str, ids: list[str] | None = None, stage: str = "", limit: int = 30,
         set: list[dict] | None = None) -> dict:
    """One summary per organisation, for an agent deciding what to do next with it.

    ids: these organisations; or stage: "no_ab" (pipeline done, no confirmed A/B, no agent
    verdict yet), "review" (has A/B rows to review), "no_website", "unread", "held" (an agent
    has it). Each: name, segment, website, pages read and how the site pass went, addresses by
    grade, its confirmed A/B (address, person, role, page), rows to review with the reason,
    the playbook entries for its segment, and agent verdicts so far.
    set=[{org_id, website?, segment?, scope?, city?, note?}]: correct organisations - a website
    you found or fixed (the sweep reads it next; re-sweep with run(orgs=[...], again=True)),
    the segment the playbook keys on, scope in/out/unsure.
    """
    try:
        pdir = ws.project_dir(project)
        if set:
            changed = ws.update_orgs(pdir, set)
            if not ids and not stage:
                return {"updated": changed}
        return ws.org_summaries(pdir, ids, stage, limit)
    except Exception as exc:  # noqa: BLE001
        return _err(exc)


def pool(project: str, file: str, into: str = "venues", filters: dict | None = None,
         map: dict | None = None, group_by: str = "", org_fields: dict | None = None) -> dict:
    """Build the pools from a data file fetch saved (JSON, GeoJSON, CSV; e.g. an open-data export).

    filters: {column: value or [values]} kept rows (exact, case-insensitive). map: {new column:
    file column}, e.g. {"id": "equip_numero", "name": "inst_nom", "city": "com_nom",
    "operator": "equip_gest_nom"}; without map all columns are kept. into: venues (default) or
    orgs. group_by: a file column (e.g. the operator or owner) - each distinct value becomes an
    organisation in orgs.csv, its venues linked by org_id; org_fields: {orgs column: file column}
    to carry e.g. city or website. Call it once per source (open data, federation lists, maps
    results saved as CSV, your own lists): the pools are the union of all of them - the same
    venue or organisation from two sources becomes one row with both sources and the fields
    of both. Returns the file's columns so you can map them.
    """
    try:
        return mo_pool.pool(ws.project_dir(project), file, into, filters, map, group_by, org_fields)
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


TOOLS = (project, search, pool, fetch, browse, judge, grade, run, tasks, orgs, export)


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
    ap.add_argument("--goal", type=float, default=None, help="%% of organisations with A/B")
    ap.add_argument("--agent", action="store_true")
    ap.add_argument("--workers", type=int, default=24)
    ap.add_argument("--shard", default="0/1")
    ap.add_argument("--follow", action="store_true")
    a = ap.parse_args(argv)
    pdir = ws.project_dir(a.project)
    try:
        state = mo_run.run_sync(pdir, rounds=a.rounds, max_pages=a.max_pages,
                                goal_ab_org_share=a.goal, agent=a.agent, workers=a.workers, shard=a.shard,
                                follow=a.follow)
    except Exception as exc:  # noqa: BLE001
        k, n = a.shard.split("/")
        st = {"file": "run.json" if n == "1" else f"run.{k}.json", "shard": a.shard}
        st.update(stage="failed", done=True, error=mo_config.scrub(f"{type(exc).__name__}: {exc}")[:300])
        mo_run._save_state(pdir, st)
        raise
    print(json.dumps({k: state.get(k) for k in ("stopped_because", "result", "export", "history")},
                     ensure_ascii=False, indent=1))
    return 0


def main() -> None:
    if "--check" in sys.argv:
        raise SystemExit(check())
    if len(sys.argv) > 1 and sys.argv[1] == "run":
        raise SystemExit(headless(sys.argv[2:]))
    if len(sys.argv) > 1 and sys.argv[1] == "batch":
        import argparse
        ap = argparse.ArgumentParser(prog="server.py batch")
        ap.add_argument("project")
        ap.add_argument("--job", required=True)
        ap.add_argument("--opts", default="{}")
        a = ap.parse_args(sys.argv[2:])
        pdir, job, o = ws.project_dir(a.project), json.loads(a.job), json.loads(a.opts)
        try:
            mo_run.run_sync(pdir, rounds=int(o.get("rounds", 6)), max_pages=int(o.get("max_pages", 15)),
                            agent=bool(o.get("agent")), workers=int(o.get("workers", 12)), job=job)
        except Exception as exc:  # noqa: BLE001
            mo_run._save_state(pdir, {"file": f"batch.{job['id']}.json", "stage": "failed", "done": True,
                                      "error": mo_config.scrub(f"{type(exc).__name__}: {exc}")[:300]})
            raise
        raise SystemExit(0)
    if len(sys.argv) > 1 and sys.argv[1] == "supervise":
        import argparse
        ap = argparse.ArgumentParser(prog="server.py supervise")
        ap.add_argument("project")
        ap.add_argument("--shards", type=int, default=1)
        ap.add_argument("--opts", default="{}")
        a = ap.parse_args(sys.argv[2:])
        print(json.dumps(mo_run.supervise(ws.project_dir(a.project), a.shards, json.loads(a.opts))))
        raise SystemExit(0)
    from mcp.server import MCPServer
    server = MCPServer("MarketOps Research", version=VERSION)
    for fn in TOOLS:
        server.tool()(fn)
    server.run(transport="stdio")


if __name__ == "__main__":
    main()
