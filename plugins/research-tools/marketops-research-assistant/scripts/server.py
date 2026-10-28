#!/usr/bin/env python3
"""MarketOps research MCP: thirteen tools, one project folder, many agents at once.

The research is a super-pipeline of pools - sources -> pages -> venues -> organisations ->
contacts - every stage running at once and several agents inside each stage.

project  open/create a project folder, write the brief, see progress, the flow between pools, costs
sources  the source pool: register lists as they are found, see each one's state and yield
harvest  mine one source: page through its listing, read its entries' pages in bulk, one row each
search   Serper + SerpApi + DeepSeek + TinyFish at once (web or maps)
pool     build venues.csv / orgs.csv from a saved open-data file (filter, map, group by operator)
fetch    read one page with Crawl4AI + TinyFish + DeepSeek at once; data files download
browse   background: TinyFish Agent for pages that need clicks
judge    Jev answers one multiple-choice question per row of any pool (in scope? own site?)
grade    Jev grades contacts A/B/C/D/X from the brief; or set grades yourself
run      the agents' bulk reader: read a package's (or the pool's) sites in the background and grade
         them - paced by the agents, never digging or building pools on its own
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

import mo_logsafe  # noqa: E402,F401 - no provider key may reach a log line (issue #86)

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

    brief (optional, merged into brief.json - send only the keys you change; a key set to null is
    removed): {"goal": "...", "region": "...", "country": "FR",
    "language": "fr", "scenario": "...", "grades": {"A": "...", ...}, "exclude": "...",
    "address_rules": {"info": "X"}, "routes_per_org": 3, "strict_ledger": "AB" | "A" | "named_A",
    "focus": {...}, "fresh": true, "pilot": 30, "max_per_source": 0}. pilot: how many organisations
    each source is tried on before the research lanes give it more (Bruce's 30; 5 for a run of 20);
    max_per_source: a run's own limit of organisations researched per source (0: none). strict_ledger: which grades the strict ledger counts (the workbook
    reports it next to the research-valid ledger, A-D). fresh: the project starts from nothing -
    pages, searches and lessons other projects left in the shared library are not reused (its
    own are), and a pooled file nobody fetched, harvested or registered here is flagged as
    brought in. For tests and for any user who wants a clean start; without it a project
    reuses what earlier work read, which is cheaper.
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
            # merged into the brief, not replacing it: a lead who sends one key keeps the goal,
            # region and fresh it set before (073: {"max_per_source": 8} wiped them); null removes a key
            brief = {k: v for k, v in {**ws.brief(pdir), **brief}.items() if v is not None}
            if brief.get("fresh") and not brief.get("fresh_since"):
                # from this moment on: nothing read or learned before it, by any project, is reused
                brief = {**brief, "fresh_since": ws._now()}
            (pdir / "brief.json").write_text(json.dumps(brief, ensure_ascii=False, indent=1), encoding="utf-8")
        if playbook:
            ws.update_playbook(pdir, playbook)
        if journal:
            import mo_journal
            mo_journal.add(pdir, journal, agent)
        out = ws.status(pdir)
        import mo_flow
        out["flow"] = mo_flow.flow(pdir)
        out["version"] = VERSION
        out["plugin_root"] = str(Path(__file__).resolve().parents[1])   # references/roles/<role>.md live here
        out["providers"] = mo_config.configured() + ["crawl4ai"]
        out["accounts"] = mo_budget.accounts()
        out["capabilities"] = mo_providers.capabilities()
        import mo_library
        out["library"] = mo_library.stats()
        out["needs_you"] = needs_you(pdir, out)
        return out
    except Exception as exc:  # noqa: BLE001
        return _err(exc)


def _minutes_ago(n: int) -> str:
    import time as _time
    return _time.strftime("%Y-%m-%dT%H:%M:%S", _time.localtime(_time.time() - 60 * n))


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
    import mo_tasks
    for src, y in mo_tasks.source_yield(pdir).items():
        if y["state"] == "waiting":
            items.append(f"Source '{src}': A/B for {y['with_ab']} of the {y['worked']} organisations worked (pilot, "
                         f"under {int(mo_tasks.STOP * 100)}%); its other {y['orgs'] - y['worked']} are paused by the pilot "
                         "rule. Nothing waits on it; resume it with sources(set=[{source, status: 'scale'}]) if it "
                         "should get them after all.")
    dups = (status.get("flow") or {}).get("possible_duplicates") or []
    if dups:
        items.append(f"{len(dups)} organisations look pooled twice (" + "; ".join(
            " = ".join(d["names"][:2]) + f" ({d['why']})" for d in dups[:4]) + "): the lead merges each pair with "
            "orgs(merge=[{keep, drop}]) - until then their routes count twice.")
    import mo_journal
    recent = [e for e in mo_journal.read(pdir)[-200:] if e.get("type") == "auto"
              and str(e.get("key", "")).startswith("lane-empty:") and str(e.get("time", "")) >= _minutes_ago(30)]
    for e in recent[-3:]:
        items.append(e["text"] + ".")
    held_n: dict[str, int] = {}
    for c in rows:
        if c.get("graded_by") == "held":
            held_n[c.get("org", "") or c.get("org_id", "")] = held_n.get(c.get("org", "") or c.get("org_id", ""), 0) + 1
    if held_n:
        items.append(f"{sum(held_n.values())} addresses are held back from directory pages on " + ", ".join(
            f"{k} ({n})" for k, n in list(held_n.items())[:4]) + ": an agent pools the list's entries and clears "
            "them with grade(held=[{org_id, held: 'X'}]) - they stay out of the totals meanwhile.")
    ab_ok = [r for r in rows if r.get("grade") in ("A", "B") and r.get("review") != "yes"]
    checked = sum(r.get("checked") in ("ok", "wrong") for r in rows)
    need = min(mo_export.CHECK_MIN, len(ab_ok))
    if len(ab_ok) >= 10 and checked < need:
        items.append(f"Spot check {checked} of at least {need} on {len(ab_ok)} confirmed A/B: the workbook is marked "
                     "not deliverable until the lead (or a finish worker) opens the 抽检 rows' pages and records "
                     "each with grade(set=[{value, org_id, checked: 'ok'|'wrong'}]) - 20 rows take a few minutes.")
    chains = (status.get("flow") or {}).get("possible_chains") or []
    if chains:
        items.append("Stores of one chain are researched one by one: " + "; ".join(
            f"{c['site']} ({c['organisations']})" for c in chains[:5]) + ". The lead files them under the chain's "
            "head office (orgs(add=...) it, orgs(set=[{org_id, part_of}]) the stores) - a store page rarely names "
            "more than its front desk.")
    wanted = ws.read_csv(pdir / "browse_wanted.csv")
    if wanted and not ws.paid_agent_allowed(pdir):
        items.append(f"Agents want the paid TinyFish Agent on {len(wanted)} page(s) whose contacts sit behind clicks "
                     f"(about $0.016 a step, up to 40 steps each - at most ${len(wanted) * 0.64:.2f}); see "
                     "browse_wanted.csv. Allow it with project(name, brief={..., \"paid_agent\": true}), or leave it off.")
    unsure = sum(o.get("scope") == "unsure" for o in ws.read_csv(pdir / "orgs.csv"))
    if unsure:
        items.append(f"{unsure} organisations could not be placed in or out of scope (orgs.csv scope=unsure).")
    for st in mo_run.run_states(pdir):
        for src, y in ((st.get("progress") or {}).get("source_yield") or st.get("source_yield") or {}).items():
            if y.get("turns") == 0:
                items.append(f"Source '{src}' gave A/B for {y['with_ab']} of {y['done']} organisations (<5%): "
                             "keep it at the back of the queue, or drop the rest of it?")
        if st.get("waiting_for_agents"):
            items.append(st["waiting_for_agents"]["note"] + ".")
        if st.get("stage") == "failed" or (st.get("restarts") or 0) >= mo_run.MAX_RESTARTS:
            items.append(f"Run shard {st.get('shard')} stopped: {st.get('error') or 'restarted too often'}.")
    sat = ws.source_saturation(pdir)
    if ws.read_csv(pdir / "orgs.csv") and not sat["saturated"]:
        items.append(f"Data sources are not saturated ({sat['status']}): keep a sources worker running until "
                     "the last three sources each add under 5% new organisations.")
    fl = status.get("flow") or {}
    pools, lanes = fl.get("pools") or {}, fl.get("lanes") or {}
    found = (pools.get("sources") or {}).get("found", 0)
    if found and not (lanes.get("source") or {}).get("claimed"):
        items.append(f"{found} sources are found but nobody mines them: start a miner (pool role, "
                     "tasks(action='claim', kind='source')) next to the finders.")
    for f in (pools.get("pages") or {}).get("harvest_files_not_pooled") or []:
        items.append(f"{f} was harvested but never pooled: check 20 rows and pool it (rows that never reach the "
                     "pools are work thrown away).")
    loose = (pools.get("venues") or {}).get("without_organisation", 0)
    if loose:
        items.append(f"{loose} venues have no organisation behind them: decide who runs them (pool role) - contacts "
                     "hang on organisations, not venues.")
    rec = fl.get("reconcile") or {}
    if rec and not rec.get("adds_up"):
        items.append(f"The organisation pool does not add up: {rec.get('organisations')} organisations, "
                     f"{rec.get('added_by_logged_sources')} added by logged sources, {rec.get('without_logged_source')} "
                     "without a source - pool every list through pool(..., source=...) so each one is counted.")
    cap = status.get("capabilities") or {}
    if cap.get("level") in ("reduced", "minimal") and cap.get("effects"):
        items.append(f"Working at '{cap['level']}' level ({', '.join(cap.get('missing') or []) or 'no keys'} missing): "
                     + "; ".join(cap["effects"]) + ". Add the missing keys (several per provider allowed) to go back to full.")
    for name, acc in (status.get("accounts") or {}).items():
        if not isinstance(acc, dict):
            continue
        # the skip mark lives in the run's processes, so read the balance here as well
        bal, line = (acc.get("balance") or {}).get("left") if isinstance(acc.get("balance"), dict) else acc.get("balance"), \
            acc.get("red_line")
        low = isinstance(bal, (int, float)) and (bal <= 0 or (isinstance(line, (int, float)) and bal <= line))
        if acc.get("skipped") or low:
            items.append(f"{name} is out of credit or at its red line (balance {bal}): top it up or leave it off"
                         + (" - without it there is no maps search and digging leans on DeepSeek and TinyFish." if name in ("serper", "serpapi") else "."))
    return [f"{n}. {t}" for n, t in enumerate(dict.fromkeys(items), 1)]


def search(query: str, project: str = "", engines: list[str] | None = None, country: str = "",
           lang: str = "", n: int = 10, maps: bool = False, pages: int = 1, near: list[str] | None = None,
           agent: str = "") -> dict:
    """Search the web with every engine at once and merge by URL (found_by says who found it).

    engines: any of serper, serpapi, deepseek, tinyfish (default: by the project's provider
    weights, brief.providers.search; the held-back engines join when results are thin).
    maps=True: Google Maps places (name, address, phone, website) via Serper and SerpApi, about
    20 a page. To cover a town rather than its top 20: pages (up to 6; Google Maps repeats itself
    beyond) and near - map centres "@lat,lon,14z" (SerpApi), one search per centre; a grid of
    centres over a city finds what one search never shows.
    country/lang: two-letter codes, e.g. "ch", "de".
    """
    try:
        pdir = ws.project_dir(project) if project else None
        pages = max(1, min(int(pages or 1), 6 if maps else 3))
        centres = (near or [""])[:25]
        merged: dict = {}
        out: dict = {}
        for centre in centres:
            for page in range(1, pages + 1):
                got = mo_providers.search(query, engines, country, lang, n, maps, pdir,
                                          weights=ws.weights(pdir, "search") if pdir else None,
                                          page=page, near=centre)
                if not out:
                    out = {k: v for k, v in got.items() if k != "results"}
                for r in got.get("results", []):
                    k = (r.get("url") or "") + "|" + (r.get("title") or "") if maps else r.get("url") or r.get("title")
                    merged.setdefault(k, r)
                if len(got.get("results", [])) < (15 if maps else n // 2):
                    break   # the last page came back short: nothing more behind it
        out["results"] = list(merged.values())[: (len(merged) if maps and (pages > 1 or near) else max(5, n * 2))]
        return out
    except Exception as exc:  # noqa: BLE001
        return _err(exc)


def fetch(project: str, url: str, readers: list[str] | str | None = None, org_id: str = "",
          org: str = "", max_chars: int = 3000, refresh: bool = False, agent: str = "") -> dict:
    """Read one page with Crawl4AI, TinyFish and DeepSeek at the same time and save every version.

    Returns each reader's outcome, the emails/phones found (with the lines around them),
    same-site links that look like contact/people pages, and the page text (max_chars;
    0 for none). With org_id/org the contacts are added to that organisation's contacts.
    readers: a subset of crawl4ai, tinyfish, deepseek, or "download" for a
    data file (CSV/JSON/XLSX/PDF exports are downloaded automatically).
    A page or site that failed repeatedly is set aside for a while (site health); refresh=True
    reads it anyway, now, and a success restores it.
    A PDF is read here first (its text, in seconds; pypdf installs itself the first time) and the
    file itself is kept: `pdf_file` is its path - when your host reads PDFs (Claude Code's Read does,
    scans and tables included), open it there for what the text loses, then pool what you read
    (orgs(add=[{name, email, person, role, quote}]) or pool(rows=...)).
    """
    try:
        out = ws.fetch(ws.project_dir(project), url, readers, org_id, org, refresh=refresh)
        out["text"] = out["text"][:max_chars]
        out.pop("content_links", None)
        out.pop("page_links", None)
        out.pop("scored_links", None)   # the same links as `links`, with scores: half the size for the agent
        cs = out["contacts"]
        # a directory page can print hundreds: the answer must fit the host's tool output (073: 235k)
        out["contacts"] = [{**{k: c[k] for k in ("kind", "value", "mailbox", "same_site", "found_by")},
                            "quote": str(c.get("quote") or "")[:240]} for c in cs[:60]]
        if len(cs) > 60:
            out["contacts_not_shown"] = len(cs) - 60
            out["note"] = (f"{len(cs)} addresses on this page: a list, not one organisation's people - its rows "
                           "belong to the organisations they name (orgs(add=[...email...]) or sources(add=...))")
        return out
    except Exception as exc:  # noqa: BLE001
        return _err(exc)


def browse(project: str, url: str, goal: str = "", org_id: str = "", org: str = "", stealth: bool = False,
           why: str = "", agent: str = "") -> dict:
    """TinyFish Agent opens the page in a real browser (clicks tabs, 'show more') in the background.

    Paid per step (about $0.016), capped at 40 steps and 5 minutes a run, and OFF until the user
    allows it (brief.paid_agent: true): until then a call records the page (with why: what you
    expect behind the clicks) and needs_you asks the user about all such pages at once - keep
    working with the free tools meanwhile. Use it when readers
    return no text or the contacts sit behind clicks - on pages that carry several people, not a
    single inbox. One site per run; it exits from the brief's country when TinyFish supports it
    (US, GB, CA, DE, FR, JP, AU - regional blocks); stealth=True for bot-protected pages. Each URL
    is submitted once per project. The result is saved as a page and its contacts added;
    project(name) shows when it is done. A custom goal names the objective, the fields, the steps,
    what not to do and the edge cases.
    """
    try:
        return ws.browse(ws.project_dir(project), url, goal, org_id, org, stealth, why, agent)
    except Exception as exc:  # noqa: BLE001
        return _err(exc)


def grade(project: str, set: list[dict] | None = None, regrade: bool = False, limit: int = 0,
          orgs: list[str] | None = None, agent: str = "", held: list[dict] | None = None) -> dict:
    """Grade the project's email candidates.

    Without set: Jev grades ungraded rows from each row's quote against the brief's grades
    (any language), all of them unless limit. A/B rows that need a look (low confidence,
    address shared with other organisations, page on another site) get review=yes. With
    set: your own judgement, a list of {"value": email, "org_id"?, "grade"?, "person"?,
    "role"?, "note"?, "confirm"?}; rows you set are never regraded by Jev. An A or B that the
    grade table or the brief contradicts (a general inbox like info@ or accueil@, an A without
    the person, no role) is kept but marked review, and the answer quotes the table - correct
    it, or confirm=true when the page really supports it. Spot check: {"value", "org_id",
    "checked": "ok"|"wrong", "note"?} records that you opened the page and the row holds (or
    not) - the workbook's 抽检 sheet lists the sample and the precision. orgs=[ids]: grade only
    these organisations' rows - pass your package, so you never wait on other agents' rows.
    An address set with an org_id that has no row there yet (a list's entry kept aside, one on
    another organisation, one on the page you name with page_url) is attached to it from that
    page - `attached`; one no page read here prints comes back in `not_on_a_page`.
    held=[{org_id, held: "X" | "grade"}]: the addresses held back from a flood on that organisation
    (a directory on its pages; they wait outside the totals): "X" marks them all not its own -
    pool the list's entries as organisations instead - "grade" sends them to Jev after all.
    """
    try:
        pdir = ws.project_dir(project)
        if held:
            out = ws.release_held(pdir, held)
            if any(str(h.get("held")) == "grade" for h in held if isinstance(h, dict)):
                out["graded"] = mo_grade.auto(pdir, limit, False, orgs=[str(h.get("org_id")) for h in held])
            return out
        return mo_grade.manual(pdir, set) if set else mo_grade.auto(pdir, limit, regrade, orgs=orgs)
    except Exception as exc:  # noqa: BLE001
        return _err(exc)


def judge(project: str, file: str, question: str, options: dict, column: str,
          fields: list[str] | None = None, limit: int = 300, redo: bool = False, agent: str = "") -> dict:
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


def run(project: str, rounds: int = 0, max_pages: int = 15, goal_ab_org_share: float | None = None,
        paid_agent: bool = False, stop: bool = False, workers: int = 8, shards: int = 1,
        follow: bool = False, pool_done: bool = False, orgs: list[str] | None = None, again: bool = False,
        discover: bool = False, agent: str = "") -> dict:
    """The agents' bulk reader, in background processes (survives this session). Agents lead
    the research; this reads organisations' own sites for them, many at once.

    orgs=[ids] (the usual call, from a researcher's package): read just these organisations -
    find a missing site, read the high-value pages and PDFs, extract, grade - and come back;
    read the result with orgs(project, ids=[...], wait=50), then judge, dig with search/fetch
    and write a verdict yourself. rounds (0-2, default 0) adds template dig rounds for this
    package only, stopping at the first round that finds nothing; again=True reads anew with
    the playbook as it is now.

    Without orgs: the whole pool's sites are read ahead of the researchers. It never digs, and
    it paces itself on the agents: once 30 organisations are read and wait for a researcher it
    starts no more (project(name).run shows waiting_for_agents), and after 60 minutes with no
    agent taking work it ends - the tools never research alone. follow=True keeps taking rows
    agents append to orgs.csv, until run(pool_done=True). discover=True also lets the tools look
    for new organisations from brief.focus (maps, list pages) - only when a finder agent wants
    that; building the pools is the finders' and miners' work. paid_agent=True lets the TinyFish Agent
    open pages whose email buttons hide the address (paid per step). stop=True ends it
    gracefully (it exports what it has). Resumable. workers: organisations at once per process,
    shards: processes - both capped to what the machine's threads and memory hold.
    max_pages is a floor: a site keeps being read while its pages bring new addresses (up to 40).
    """
    try:
        return mo_run.run(ws.project_dir(project), rounds=rounds, max_pages=max_pages,
                          goal_ab_org_share=goal_ab_org_share, agent=paid_agent, stop=stop, workers=workers,
                          shards=shards, follow=follow, pool_done=pool_done, orgs=orgs, again=again,
                          discover=discover)
    except Exception as exc:  # noqa: BLE001
        return _err(exc)


# A wait inside one tool call stays under the host's tool timeout (Claude Code's MCP default is
# 60 s): a call the host gave up on still claimed a package nobody received (073 smoke).
WAIT_MAX = int(os.environ.get("MARKETOPS_WAIT_MAX", "50"))

ROLE_FOR_KIND = {"sweep": "research", "dig": "dig", "source": "pool", "site": "websites", "find": "sources",
                 "review": "finish", "evidence": "finish"}
ROLES = Path(__file__).resolve().parents[1] / "references" / "roles"


def role_guide(kind: str) -> str:
    """The role file for a lane, handed to the worker with its package: a worker whose host
    cannot open plugin files still gets its instructions (073: a worker could not read them)."""
    try:
        return (ROLES / f"{ROLE_FOR_KIND.get(kind, kind)}.md").read_text(encoding="utf-8")[:4000]
    except OSError:
        return ""


def tasks(project: str, action: str = "status", kind: str = "", size: int = 15, agent: str = "",
          task_id: str = "", note: str = "", title: str = "", release: bool = False,
          verdicts: list[dict] | None = None, wait: int = 0) -> dict:
    """Shared work queue so several agents work one project in parallel.

    Coordinator: action="plan", kind="dig" (organisations still without a confirmed A/B),
    "review" (A/B rows to check), "evidence" (A/B rows with weak or medium evidence),
    "browse" (pages whose email buttons hide addresses), cut into packages of `size`;
    action="add", title="..." for any other job (a source
    family, a district, a federation list). Worker: action="claim", agent="<your name>"
    (optionally kind) returns one package with its organisations or rows and how to do it;
    work it with the other tools, then action="done", task_id, note="what you found/tried"
    (release=True hands it back undone). A claim untouched for 40 minutes is offered again.
    The first claim of a lane also returns your role's guide (role_guide). wait=seconds (at most
    50, under the host's tool timeout; call again for longer) with claim: when the lane is empty because the pools before it are still filling (a
    researcher starting before the miners pooled anything), the call waits for a package
    instead of returning empty - one call, no polling; an empty answer after waiting is the
    real end of the lane. An empty claim says why: keep_waiting (the lane refills - claim again;
    never stop on it), other_lanes (open work elsewhere), lane_finished (nothing can refill it).
    Stop only on lane_finished; until then an empty lane is a pause, and the lead hears of it.
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
            import time as _time
            end = _time.time() + max(0, min(int(wait or 0), WAIT_MAX))
            while True:
                got = mo_tasks.claim(pdir, agent or "worker", kind)
                if got.get("task") or _time.time() >= end:
                    break
                _time.sleep(5)
            k = (got.get("task") or {}).get("kind") or kind
            seen = {t.get("agent") for t in ws.read_csv(pdir / "tasks.csv")
                    if t.get("kind") == k and t.get("task_id") != (got.get("task") or {}).get("task_id")}
            if k and (agent or "worker") not in seen:
                got["role_guide"] = role_guide(k)
            return got
        if action == "done":
            return mo_tasks.done(pdir, task_id, note, release)
        if action == "verdict":
            return {"recorded": ws.add_verdicts(pdir, verdicts or [], agent), **ws.stages(pdir)}
        # open: 0 is not "the work is over" - the lanes refill as the pools grow (073: three workers
        # read it as the end at 14:12); outlook says whether it is
        return {**mo_tasks.summary(pdir), **mo_tasks.outlook(pdir, kind, agent)}
    except Exception as exc:  # noqa: BLE001
        return _err(exc)


def orgs(project: str, ids: list[str] | None = None, stage: str = "", limit: int = 30,
         set: list[dict] | None = None, add: list[dict] | None = None, agent: str = "",
         wait: int = 0, source: str = "", merge: list[dict] | None = None) -> dict:
    """One summary per organisation, for an agent deciding what to do next with it.

    ids: these organisations; or stage: "no_ab" (pipeline done, no confirmed A/B, no agent
    verdict yet), "review" (has A/B rows to review), "no_website", "unread", "held" (an agent
    has it). Each: name, segment, website, pages read and how the site pass went, addresses by
    grade, its confirmed A/B (address, person, role, page), rows to review with the reason,
    the playbook entries for its segment, and agent verdicts so far.
    set=[{org_id, website?, segment?, scope?, city?, note?, part_of?}]: correct organisations - a website
    you found or fixed (website: "" clears a wrong one) (the sweep reads it next; re-sweep with run(orgs=[...], again=True)),
    the segment the playbook keys on, scope in/out/unsure, part_of: the id of the organisation
    that decides for this one (a chain's head office for its store) - the lanes then research
    the chain, not the store, and the headline counts the chain once.
    add=[{name, city, website?, segment?, note?, source?, email?, person?, role?, page_url?,
    quote?}]: organisations you found (the council
    department or contractor that really runs a venue, a club found on a federation page, the
    places a maps search returned) - merged into the pool like any source. source: the
    registered source they came from (sources(add=...) first), per row or for all with the
    `source` argument - without it they are filed under your name, and the lanes cannot give
    each source its turn. A row with an email (the address the list prints for it, with the
    person and role next to it, the page and the line it is on as quote) gives that address
    to the organisation as a contact at once - no file needed.
    merge=[{keep, drop}] (drop: an id or a list): one organisation pooled twice (two lists, two
    spellings; flow.possible_duplicates names them) becomes one - contacts, verdicts and empty
    fields move to `keep`, the dropped row stays only as merged_into for the source counts. A
    chain's stores are not duplicates: those take part_of.
    wait=seconds (with ids): return once every one of them is through the tools' read (after
    run(orgs=...)), or after that many seconds (at most 50, under the host's tool timeout - call
    again until they are through) - one call per 50 seconds instead of asking again
    and again.
    """
    try:
        pdir = ws.project_dir(project)
        if add:
            import mo_pool
            import mo_flow
            keep = ("name", "city", "website", "segment", "note", "type", "uai", "siret", "rna", "siren",
                    "email", "person", "role", "page_url", "quote")
            groups: dict[str, list] = {}
            for r in add:
                if isinstance(r, dict) and str(r.get("name") or "").strip():
                    src = str(r.get("source") or source or "").strip() or f"agent:{agent or 'agent'}"
                    groups.setdefault(src, []).append({k: str(v) for k, v in r.items() if k in keep})
            out = {"orgs_added": 0, "orgs_merged": 0, "contacts_from_rows": 0, "sources": {}}
            for src, rows in groups.items():
                got = mo_pool.add_orgs(pdir, rows, src, "agent")
                out["orgs_added"] += got["orgs_added"]
                out["orgs_merged"] += got["orgs_merged"]
                out["contacts_from_rows"] += got["contacts_from_rows"]
                out["sources"][src] = got["orgs_added"] + got["orgs_merged"]
                if not src.startswith("agent:"):
                    mo_flow.mark_source(pdir, src, "pooled", agent)
            if not ids and not stage and not set:
                return out
        if merge:
            merged = ws.merge_orgs(pdir, merge)
            if not ids and not stage and not set:
                return merged
        if set:
            changed = ws.update_orgs(pdir, set)
            if not ids and not stage:
                return {"updated": changed}
        if ids and wait:
            import time
            want, end = {str(i) for i in ids}, time.time() + max(1, min(int(wait), WAIT_MAX))

            def reading() -> set:
                # a batch still running on them: an earlier pass's final row is not this one's (073:
                # a re-read returned at once and the researcher polled)
                busy = {*()}   # `set` is this tool's own argument here
                for st in mo_run.batch_states(pdir):
                    if not st.get("done"):
                        try:
                            job = json.loads((pdir / f"batch.{st['batch']}.json").read_text(encoding="utf-8"))
                        except (OSError, ValueError):
                            job = {}
                        busy |= {str(i) for i in job.get("orgs_ids") or []} or want
                return busy & want
            while time.time() < end:
                done = {r["org_id"] for r in ws.read_csv(pdir / "crawl.csv") if r.get("pass") == "final"}
                if want <= done and not reading():
                    break
                time.sleep(3)
            left = reading() | (want - {r["org_id"] for r in ws.read_csv(pdir / "crawl.csv") if r.get("pass") == "final"})
            if left:
                out = ws.org_summaries(pdir, ids, stage, limit)
                out["still_reading"] = sorted(left)[:30]
                out["next"] = "the tools are still reading these: call orgs(..., wait=50) again"
                return out
        return ws.org_summaries(pdir, ids, stage, limit)
    except Exception as exc:  # noqa: BLE001
        return _err(exc)


def sources(project: str, add: list[dict] | None = None, set: list[dict] | None = None, agent: str = "") -> dict:
    """The source pool: every list found for the project and where it stands.

    Finding and mining are separate jobs that run at the same time: finders register lists here
    as they find them, miners claim them (tasks(action="claim", kind="source")) and harvest and
    pool them, while the finders keep looking.
    add=[{source: short name, url, kind: register|federation|open_data|maps|directory|client|other,
    row_is: what one row really is (venue, club, operator...), expect: rows / yield you expect,
    note}]: register lists you found (the same url or name twice is one source).
    set=[{source, status: found|mining|harvested|pooled|scale|dropped, note}]: move or drop a
    source (say why in note). The research lanes give every pooled source a pilot of 30
    organisations and hand out more only while it yields A/B (>= 5%); `scale` gives a source all
    its organisations whatever its pilot said, `dropped` none - your call, the pilot only
    advises. Returns the pool with each source's state, what the pooled ones brought, its pilot
    (flow.sources_take_turns), saturation and the flow between all pools.
    """
    try:
        import mo_flow
        pdir = ws.project_dir(project)
        out: dict = {}
        if add:
            out.update(mo_flow.add_sources(pdir, add, agent))
        if set:
            out["updated"] = mo_flow.set_sources(pdir, set)
        brought = {}
        for r in ws.read_csv(pdir / "sources.csv"):
            if r.get("source"):
                b = brought.setdefault(r["source"], {"orgs_added": 0, "orgs_merged": 0, "venues_added": 0})
                for k in b:
                    try:
                        b[k] += int(r.get(k) or 0)
                    except ValueError:
                        pass
        out["sources"] = [{**{k: r.get(k, "") for k in ("source", "url", "kind", "row_is", "status", "found_by",
                                                         "miner", "note")}, **brought.get(r["source"], {})}
                          for r in mo_flow.source_pool(pdir)]
        out["saturation"] = ws.source_saturation(pdir)
        out["flow"] = mo_flow.flow(pdir)
        return out
    except Exception as exc:  # noqa: BLE001
        return _err(exc)


def harvest(project: str, source: str, start: str = "", follow: str = "", next: str = "",
            sitemap: bool = False, urls: list[str] | None = None, limit: int = 40, agent: str = "",
            background: bool = False) -> dict:
    """Mine one source into the page pool and out of it, in bulk - the miner's power tool.

    start: the source's listing (or first results page); follow: a regex for the links that are
    entries (each entry's detail page, e.g. "/club/\\d+"); next: a regex for the listing's next
    pages ("page=\\d+"); sitemap=True: take the entries from the site's sitemap instead of paging
    (with follow as the filter); urls: detail pages you already have. Without follow, a listing's
    own links off the site are the entries. Reads up to `limit` pages per call, several at once
    with every reader, and writes one candidate row per detail page to
    pages/harvest-<source>.csv: name, kind, website (picked by Jev from the links the page shows,
    or left with its candidates for you), emails, phones, address, postcode, city. Call again
    until still_queued is 0 - several miners can harvest different sources at once. Then check
    20 rows and pool(project, file, into=..., map=..., source=...): the pages are already saved,
    so their addresses join the new organisations without a new call.
    background=True: read the first `limit` pages now, then keep reading the rest of the source
    in the background (this server) while you check the first rows; sources(project) shows
    where it stands, and harvest(project, source) again picks up whatever is left.
    """
    try:
        import mo_flow
        pdir = ws.project_dir(project)
        out = mo_flow.harvest(pdir, source, start, follow, next, sitemap, urls, max(1, min(int(limit), 300)), agent)
        if background and out.get("still_queued"):
            import threading

            def rest():
                try:
                    while mo_flow.harvest(pdir, source, limit=100, agent=agent).get("still_queued"):
                        pass
                except Exception:  # noqa: BLE001 - leased pages return to the queue for the next call
                    pass
            threading.Thread(target=rest, daemon=True, name=f"harvest-{source}").start()
            out["background"] = "reading the rest of this source now; check the first rows meanwhile"
        return out
    except Exception as exc:  # noqa: BLE001
        return _err(exc)


def pool(project: str, file: str = "", into: str = "venues", filters: dict | None = None,
         map: dict | None = None, group_by: str = "", org_fields: dict | None = None,
         source: str = "", replace_operator: bool = False, agent: str = "",
         rows: list[dict] | None = None) -> dict:
    """Build the pools from a data file fetch saved (JSON, GeoJSON, CSV; e.g. an open-data export).

    filters: {column: value or [values]} kept rows (exact, case-insensitive). map: {new column:
    file column}, e.g. {"id": "equip_numero", "name": "inst_nom", "city": "com_nom",
    "operator": "equip_gest_nom"}; without map all columns are kept. into: venues (default) or
    orgs. group_by: a file column (e.g. the operator or owner) - each distinct value becomes an
    organisation in orgs.csv, its venues linked by org_id; org_fields: {orgs column: file column}
    to carry e.g. city or website. Call it once per source (open data, federation lists, maps
    results saved as CSV, your own lists): the pools are the union of all of them - the same
    venue or organisation from two sources becomes one row with both sources and the fields
    of both. Returns the file's columns so you can map them. source: the list's name in the
    source pool (logged under it, moved to pooled). A page_url column (harvest files have one):
    addresses on those saved pages join the organisations at once.
    Addresses printed on a row (an email / courriel column, or any address in the row) join that
    row's organisation as contacts with the row as their quote - map "email", "person" and
    "role" when the file has them. Registry numbers - map "uai", "siret", "rna" or "siren" -
    identify an organisation across sources whatever its spelling or town; a bare postcode as
    city is not a town. replace_operator=True (with group_by): this source's organisation runs
    the venue rather than the one pooled first (the earlier one stays as previous_org_id).
    Returns provenance: tools (fetched or harvested here), source (a registered list) or
    brought-in (say where it came from).
    rows=[{...}, ...] instead of file: the rows you read yourself (a page that lists venues or
    clubs, a map, a PDF) - the same columns a file would have; they are saved under pages/ and
    pooled the same way. A host with no file tool can still pool what its agents read.
    """
    try:
        pdir = ws.project_dir(project)
        if rows and not file:
            import hashlib
            body = json.dumps([r for r in rows if isinstance(r, dict)], ensure_ascii=False)
            name = "agent-rows-" + hashlib.sha256((source + body).encode()).hexdigest()[:10] + ".json"
            (pdir / "pages").mkdir(parents=True, exist_ok=True)
            (pdir / "pages" / name).write_text(body, encoding="utf-8")
            file = "pages/" + name
        if not file:
            return {"error": "file: a saved data file (pages/...), or rows=[...] you read yourself"}
        return mo_pool.pool(pdir, file, into, filters, map, group_by, org_fields,
                            source=source, replace_operator=replace_operator)
    except Exception as exc:  # noqa: BLE001
        return _err(exc)


def export(project: str, agent: str = "") -> dict:
    """Write the Excel workbook (summary with clearly defined metrics, per-batch table, contacts A
    first with page, quote and review flag, organisations, venues, costs, X rows) and Bruce's
    18-column routes CSV."""
    try:
        return mo_export.export(ws.project_dir(project))
    except Exception as exc:  # noqa: BLE001
        return _err(exc)


TOOLS = (project, sources, harvest, search, pool, fetch, browse, judge, grade, run, tasks, orgs, export)

_EMAIL = __import__("re").compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")


def _brief_arg(v):
    """An argument as the call log keeps it: short text (addresses masked), sizes of the rest."""
    if isinstance(v, str):
        return mo_config.scrub(_EMAIL.sub("<email>", v))[:120]
    if isinstance(v, (int, float, bool)) or v is None:
        return v
    if isinstance(v, dict):
        return {"keys": sorted(map(str, v))[:12]}
    if isinstance(v, (list, tuple)):
        return {"items": len(v)}
    return type(v).__name__


def logged(fn):
    """Every tool call, written to the project's calls.jsonl: which agent, which tool, the
    arguments in brief, how long, what came back (size, error). With it the whole chain of a
    run - who did what, where it waited, what failed - is on disk, with no transcript."""
    import functools
    import inspect
    import time as _time
    sig = inspect.signature(fn)

    @functools.wraps(fn)
    def call(*a, **k):
        t0 = _time.time()
        out, err = None, ""
        try:
            out = fn(*a, **k)
            return out
        except Exception as exc:  # noqa: BLE001
            err = f"{type(exc).__name__}: {exc}"
            raise
        finally:
            try:
                args = sig.bind_partial(*a, **k).arguments
                name = args.get("project") or (args.get("name") if fn.__name__ == "project" else "")
                if name:
                    pdir = ws.project_dir(str(name))
                    if isinstance(out, dict) and out.get("error"):
                        err = str(out["error"])
                    ws.append_jsonl(pdir / "calls.jsonl", {
                        "time": ws._now(), "tool": fn.__name__,
                        "agent": str(args.get("agent") if isinstance(args.get("agent"), str) else ""),
                        "args": {key: _brief_arg(v) for key, v in args.items() if key not in ("project", "agent")},
                        "seconds": round(_time.time() - t0, 2), "ok": not err,
                        "error": mo_config.scrub(_EMAIL.sub("<email>", err))[:200],
                        "result_chars": len(json.dumps(out, ensure_ascii=False, default=str)) if out is not None else 0})
            except Exception:  # noqa: BLE001 - the log never breaks a tool
                pass
    return call


def check() -> int:
    """Installer self-check: packages import, which keys are present (names only)."""
    problems = []
    for mod in ("mcp", "httpx"):
        try:
            __import__(mod)
        except Exception as exc:  # noqa: BLE001
            problems.append(f"{mod}: {exc}")
    keys = mo_config.configured()
    print(f"MarketOps {VERSION}: keys for {', '.join(keys) or 'nothing yet'}; "
          f"projects in {mo_config.projects_root()}")
    for p in problems:
        print("  missing package -", p)
    cap = mo_providers.capabilities()
    print(f"  level: {cap['level']} - search {cap['search'] or 'none'}, read {cap['read']}, grade {cap['grade']}")
    for e in cap["effects"]:
        print("  -", e)
    for mod in ("crawl4ai", "typesafe_sdk"):   # optional: their absence steps the plugin down, not out
        try:
            __import__(mod)
        except Exception:  # noqa: BLE001
            print(f"  optional package missing - {mod} (run install.sh to add it)")
    return 1 if problems else 0


def headless(argv: list[str]) -> int:
    """python server.py run <project> [--rounds N] [--max-pages N] [--goal X] [--agent]"""
    import argparse
    ap = argparse.ArgumentParser(prog="server.py run")
    ap.add_argument("project")
    ap.add_argument("--rounds", type=int, default=0)
    ap.add_argument("--max-pages", type=int, default=15)
    ap.add_argument("--goal", type=float, default=None, help="%% of organisations with A/B")
    ap.add_argument("--agent", action="store_true")
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--shard", default="0/1")
    ap.add_argument("--follow", action="store_true")
    ap.add_argument("--discover", action="store_true")
    a = ap.parse_args(argv)
    pdir = ws.project_dir(a.project)
    try:
        state = mo_run.run_sync(pdir, rounds=a.rounds, max_pages=a.max_pages,
                                goal_ab_org_share=a.goal, agent=a.agent, workers=a.workers, shard=a.shard,
                                follow=a.follow, discover=a.discover)
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
        server.tool()(logged(fn))
    server.run(transport="stdio")


if __name__ == "__main__":
    main()
