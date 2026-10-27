"""The tools' side of the pipeline: reading organisations' sites in bulk while agents lead.

Agents do the research (finders, miners, researchers, finish - marketops-research); this
module is the power tool they call. A whole-project run reads the organisations' own sites
ahead of the researchers and grades what it finds - no more: digging, judging whose an
address is, building the pools and dropping a source are agent work. It never runs far ahead
of them: once AHEAD organisations are read and wait for a researcher it pauses, and when no
agent has taken any for AGENT_IDLE_MIN minutes it ends (Paris 071: a script digging six
rounds alone for six hours brought 15 organisations with A/B for 105 organisation-hours).
Dig rounds are only what an agent asks for, for its own package (orgs=[...], rounds<=2).

    site      read the organisation's own site (home + best contact/people pages, PDFs)
    grade     a background grader batches every new address to Jev, names and roles included
    dig       only when an agent asks (batch, rounds<=MAX_ROUNDS): a different way each round
    evidence  A/B addresses not yet strong: search the address itself, read pages printing it
    done      the organisation is finished; the workbook is refreshed every half hour

Each dig round digs a different way: 1 the organisation with the market's words for the people
wanted (focus.search_terms); 2 inside its site and PDFs; 3 third-party lists (focus.directory_terms),
maps for organisations without a website; 4 broad name + city + email; then the cycle repeats.

Progress is kept in <project>/run.json; calling run again resumes where it stopped
(finished crawls are never repeated, saved pages are never fetched twice).
"""
from __future__ import annotations

import json
import re
import time
import urllib.parse

import mo_budget
import mo_config
import mo_export
import mo_grade
import mo_pages
import mo_providers
import mo_knowledge
import mo_tasks
import mo_workspace as ws
from mo_pool import mentions as _mentions, name_words as _name_words

MAX_ROUNDS = 2       # code dig rounds an agent may ask for; later rounds found almost nothing (071)
AHEAD = 30           # organisations read and waiting for researchers before a whole-project run pauses
AGENT_IDLE_MIN = 60  # paused this long with no agent taking work: the run ends (agents lead, tools follow)
PACE_CHECK_S = 30    # how often a whole-project run looks at the agents' backlog
AGENT_PAGES_PER_ROUND = 20  # the paid TinyFish Agent: only with agent=True, and sparingly
DEFAULT_TERMS = ["contact email", "staff team board committee", "facilities grounds manager"]
DEFAULT_DIRECTORY_TERMS = ["association member list", "federation clubs", "directory contact"]


def _save_state(pdir, state: dict) -> None:
    state["updated"] = ws._now()
    name = state.get("file") or "run.json"
    (pdir / name).write_text(json.dumps(state, ensure_ascii=False, indent=1), encoding="utf-8")


def _mine(orgs: list[dict], shard: tuple[int, int]) -> list[dict]:
    k, n = shard
    return [o for i, o in enumerate(orgs) if i % n == k]


def measure(pdir, shard: tuple[int, int] = (0, 1)) -> dict:
    emails = [c for c in ws.read_contacts(pdir) if c.get("kind") == "email"]
    return mo_export.metrics(_mine(ws.read_csv(pdir / "orgs.csv"), shard), emails)


def _queries(org: dict, rnd: int, terms: list[str], dir_terms: list[str],
             order: list[str] | None = None) -> list[tuple[str, bool]]:
    """The searches for one organisation in round rnd: each round digs a different way, in the
    order that has worked best for this market and kind of organisation (mo_knowledge).
    Returns (query, maps)."""
    name = str(org.get("name") or "").strip()
    site = mo_pages.site_of(str(org.get("website") or ""))
    city = str(org.get("city") or "").strip()
    q = f'"{name}"' if name else ""
    kinds = list(order or mo_knowledge.DIG_KINDS)
    kind = mo_knowledge.DIG_KINDS.index(kinds[(rnd - 1) % len(kinds)])
    if kind == 0:   # the organisation with the market's own words for the people wanted
        return [(" ".join(x for x in (q, city, t) if x), False) for t in terms[:4]]
    if kind == 1:   # inside its own site, PDFs included (address lists, staff lists, minutes)
        if site:
            return [(f"site:{site} {t}", False) for t in terms[:2]] + [(f"site:{site} filetype:pdf", False)]
        return [(" ".join(x for x in (q, city, "pdf") if x), False)]
    if kind == 2:   # third-party lists: associations, federations, council/canton directories
        out = [(" ".join(x for x in (q, city, t) if x), False) for t in dir_terms[:3]]
        if not site:
            out.append((" ".join(x for x in (name, city) if x), True))
        return out
    return [(" ".join(x for x in (q, city, "email") if x), False),
            (" ".join(x for x in (name, city, terms[0] if terms else "contact") if x), False)]


def _rank(pdir, org: dict, hits: list[dict]) -> list[dict]:
    """Jev puts the leads most likely to name this organisation's people first. Order only:
    nothing is dropped here."""
    if len(hits) < 2 or not mo_budget.jev_ready():
        return hits
    try:
        import typesafe_sdk as sdk
        listing = "\n".join(f"[{k + 1}] {h.get('title', '')} | {h.get('url', '')} | {str(h.get('snippet', ''))[:200]}"
                             for k, h in enumerate(hits))
        qs = {f"r{k + 1}": sdk.Choice(
            instructions=f"Will page [{k + 1}] name people of {org.get('name')} with their roles or email "
                         "addresses (board, staff, committee, contact or address list)?",
            criteria={"likely": "yes, probably lists people or addresses of this organisation",
                      "unlikely": "no, news, other organisations or general content"})
            for k in range(len(hits))}
        resp = mo_grade._jev({"organisation": str(org.get("name")), "pages": listing}, qs)
        mo_budget.record(pdir, "jev", "grade", True, f"rank leads x{len(hits)}")
        choices = mo_grade.answers(resp)

        def score(k):
            a = choices.get(f"r{k + 1}")
            if a is None:
                return 0.5
            conf = getattr(a, "confidence", None) or 0.5
            return conf if a.choice == "likely" else 1 - conf
        order = sorted(range(len(hits)), key=lambda k: -score(k))
        return [hits[k] for k in order]
    except Exception:  # noqa: BLE001 - ranking is a nicety; the leads stay as found
        return hits


def _playbook_queries(org: dict, templates: list[str]) -> list[tuple[str, bool]]:
    """The playbook's searches for this kind of organisation, filled in: {name} {city} {site}."""
    vals = {"name": str(org.get("name") or "").strip(), "city": str(org.get("city") or "").strip(),
            "site": mo_pages.site_of(str(org.get("website") or ""))}
    out = []
    for t in templates:
        try:
            q = t.format_map(vals).strip()
        except (KeyError, ValueError, IndexError):
            continue
        if q and ("{site}" not in t or vals["site"]):
            out.append((q, False))
    return out


def _leads(pdir, org: dict, rnd: int, terms: list[str], dir_terms: list[str], engines: list[str] | None,
           per_org: int = 8, rank: bool = True, playbook_queries: list[str] | None = None,
           order: list[str] | None = None) -> list[str]:
    """Pages worth reading for one organisation: its own pages and PDFs, and other sites
    whose result mentions it (directories, federation and council pages)."""
    name = str(org.get("name") or "").strip()
    site = mo_pages.site_of(str(org.get("website") or ""))
    seen = ws.read_for(pdir, _oid(org))
    found: dict[str, dict] = {}
    weights = ws.weights(pdir, "search")
    # round 1 starts with what agents learned works for this kind of organisation
    planned = (_playbook_queries(org, playbook_queries or []) if rnd == 1 else []) + \
        _queries(org, rnd, terms, dir_terms, order)
    for query, maps in planned:
        try:
            res = mo_providers.search(query, None, n=8, maps=maps, project_dir=pdir, weights=weights)
        except Exception:  # noqa: BLE001
            continue
        for hit in res["results"]:
            url = hit.get("url") or ""
            if not url.startswith("http") or url in found or url in seen:
                continue
            own = site and mo_pages.site_of(url) == site
            mentions = _mentions(name, f"{hit.get('title', '')} {hit.get('snippet', '')} {url}")
            if own or mentions or (maps and not site):
                found[url] = hit
    ranked = _rank(pdir, org, list(found.values())[:20]) if rank else list(found.values())
    return [h["url"] for h in ranked][:per_org]


def _find_site(pdir, org: dict, replace: bool = False) -> str:
    """An organisation the pool gave no website: search its name and town once and take the
    result on a domain carrying its name (not a platform, not a directory). Written back to
    orgs.csv (website_from=search) so every later stage and run uses it."""
    import mo_discover
    name, city = str(org.get("name") or "").strip(), str(org.get("city") or "").strip()
    words = [w for w in _name_words(name) if len(w) > 3]
    if not name or not words:
        return ""
    try:
        res = mo_providers.search(" ".join(x for x in (f'"{name}"', city) if x), None, n=8, project_dir=pdir,
                                  weights=ws.weights(pdir, "search"))
    except Exception:  # noqa: BLE001
        return ""
    for hit in res["results"]:
        url = hit.get("url") or ""
        site = mo_pages.site_of(url)
        if not url.startswith("http") or any(p in url for p in mo_discover.PLATFORMS) or site in ws.shared_sites(pdir):
            continue
        squashed = re.sub(r"[^a-z0-9]", "", site)
        home_page = urllib.parse.urlsplit(url).path.strip("/").count("/") == 0 and len(urllib.parse.urlsplit(url).path) < 30
        # its name in the domain, or (clubs often use initials) a home page titled with its name
        if any(w in squashed for w in words) or (home_page and _mentions(name, hit.get("title") or "")):
            home = urllib.parse.urlsplit(url)._replace(path="/", query="", fragment="").geturl()
            oid = _oid(org)
            with ws._lock(pdir / "pools"):
                rows = ws.read_csv(pdir / "orgs.csv")
                cols = list(rows[0].keys()) if rows else []
                for col in ("website", "website_from"):
                    if col not in cols:
                        cols.append(col)
                for r in rows:
                    if _oid(r) == oid and (replace or not r.get("website")):
                        r["website"], r["website_from"] = home, "search" + (" (listed site dead)" if replace else "")
                ws.write_csv(pdir / "orgs.csv", cols, rows)
            return home
    return ""


def _button_pages(pdir, org_ids: set[str]) -> list[tuple[str, str]]:
    pages = []
    for r in ws.read_csv(pdir / "crawl.csv"):
        if r.get("org_id") in org_ids:
            for url in re.findall(r"email buttons without address on (\S+): try browse", r.get("note", "")):
                pages.append((r["org_id"], url))
    return pages


def _oid(o: dict) -> str:
    return str(o.get("id") or o.get("org_id") or "")


def _emails_of(pdir, oid: str) -> list[dict]:
    return [c for c in ws.contacts_of(pdir, oid) if c.get("kind") == "email"]


ROUTES_PER_ORG = 3   # key-role addresses wanted per organisation (brief.routes_per_org)


def _ab_count(pdir, oid: str) -> int:
    return sum(c.get("grade") in ("A", "B") and c.get("review") != "yes" for c in _emails_of(pdir, oid))


def _has_ab(pdir, oid: str) -> bool:
    return any(c.get("grade") in ("A", "B") and c.get("review") != "yes" for c in _emails_of(pdir, oid))


def _reverify(pdir, org: dict) -> int:
    """Results kept from earlier runs: an A/B address whose page was last read more than the
    library's PAGE_DAYS ago is checked again on that page. Still there -> fresh date; gone ->
    kept, marked still_listed=no for a person to judge."""
    import mo_library
    from datetime import datetime, timedelta, timezone
    oid, checked = _oid(org), 0
    since = (datetime.now(timezone.utc) - timedelta(days=mo_library.PAGE_DAYS)).isoformat(timespec="seconds")
    for c in _emails_of(pdir, oid):
        if c.get("grade") not in ("A", "B") or not c.get("page_url") or (c.get("last_verified") or "") >= since:
            continue
        try:
            got = ws.fetch(pdir, c["page_url"], None, oid, str(org.get("name") or ""),
                           mo_pages.site_of(str(org.get("website") or "")), refresh=True, via="reverify")
        except Exception:  # noqa: BLE001
            continue
        checked += 1
        if not any(x["value"].lower() == c["value"].lower() for x in got["contacts"]):
            c["still_listed"] = "no"
            c["note"] = (c.get("note", "") + f" not on its page any more ({ws._now()[:10]})").strip()
            ws.update_contacts(pdir, [c], ["still_listed", "note"])
    return checked


def _corroborate(pdir, org: dict, weights: dict, most: int = 3) -> int:
    """More evidence for this organisation's A/B addresses that are not yet strong: search for
    the address itself and read the pages that print it (each extra page raises the score).
    At most `most` addresses per organisation, all at the same time."""
    from concurrent.futures import ThreadPoolExecutor
    oid = _oid(org)
    todo = [c for c in _emails_of(pdir, oid) if c.get("grade") in ("A", "B") and c.get("evidence") != "strong"][:most]

    def one(c) -> int:
        try:
            res = mo_providers.search(f'"{c["value"]}"', None, n=5, project_dir=pdir, weights=weights)
        except Exception:  # noqa: BLE001
            return 0
        seen, n = ws.read_for(pdir, oid), 0
        for hit in res["results"][:2]:
            url = hit.get("url") or ""
            if url.startswith("http") and url not in seen:
                try:
                    ws.fetch(pdir, url, None, oid, str(org.get("name") or ""),
                             mo_pages.site_of(str(org.get("website") or "")), via="evidence")
                    n += 1
                except Exception:  # noqa: BLE001
                    continue
        return n
    if not todo:
        return 0
    with ThreadPoolExecutor(max_workers=len(todo)) as pool:
        return sum(pool.map(one, todo))


def mo_budget_allowed(provider: str) -> bool:
    return mo_budget.allowed(provider)[0]


class _Grader:
    """One background grader per run: whatever new addresses the organisations produce are
    graded together in batches (Jev stays efficient while the pipeline keeps moving)."""

    def __init__(self, pdir, only: set | None = None):
        import threading
        self.pdir, self.stop, self.busy = pdir, threading.Event(), threading.Lock()
        # a batch grades its own organisations only: batches of different agents never grade
        # the same rows, and one never waits on another's
        self.only = {str(i) for i in only} if only else None
        self.kick = threading.Event()
        self.enabled = bool(mo_config.slots("jev"))   # no Jev: nothing to wait for, the agent grades later
        self.thread = threading.Thread(target=self._loop, daemon=True, name="grader")

    def _pending(self) -> bool:
        if self.only is not None:
            return any(c.get("kind") == "email" and not c.get("grade")
                       for oid in self.only for c in ws.contacts_of(self.pdir, oid))
        return any(c.get("kind") == "email" and not c.get("grade") for c in ws.read_contacts(self.pdir))

    def grade_now(self) -> None:
        with self.busy:
            try:
                import fcntl
                import hashlib
                name = "grade.lock" if self.only is None else \
                    "grade." + hashlib.sha256("|".join(sorted(self.only)).encode()).hexdigest()[:10] + ".lock"
                fh = open(self.pdir / name, "a")
                try:
                    fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except OSError:
                    fh.close()
                    return  # another process is grading this project right now
            except ImportError:
                fh = None
            try:
                if self._pending():
                    mo_grade.auto(self.pdir, orgs=self.only)
            finally:
                if fh is not None:
                    fh.close()

    def _loop(self) -> None:
        while not self.stop.is_set():
            try:
                self.grade_now()
            except Exception:  # noqa: BLE001 - grading retries on the next beat
                pass
            self.kick.wait(8)
            self.kick.clear()

    def wait_for(self, oid: str, timeout: int = 90) -> None:
        # no usable Jev key (none, or every slot used up): nothing will be graded, do not wait -
        # the rows stay ungraded for later and the organisation moves on
        if not self.enabled or not mo_budget.jev_ready():
            return
        deadline = time.time() + timeout
        while time.time() < deadline:
            if not any(not c.get("grade") for c in _emails_of(self.pdir, oid)):
                return
            self.kick.set()
            time.sleep(1)


def _source(org: dict) -> str:
    return (str(org.get("sources") or org.get("source") or "").split(";")[0].split("|")[0].strip()) or "-"


SOURCE_SAMPLE, SOURCE_SCALE, SOURCE_STOP = 30, 0.10, 0.05


def _turns(done_ab) -> int:
    """How many organisations a source starts per round: 1 until it has a sample of 30, then
    2 at >=10% with A/B, 0 (only when nothing else waits) under 5%."""
    if not done_ab or done_ab[0] < SOURCE_SAMPLE:
        return 1
    rate = done_ab[1] / done_ab[0]
    return 2 if rate >= SOURCE_SCALE else 0 if rate < SOURCE_STOP else 1


def agent_backlog(pdir, mine: set[str]) -> int:
    """Organisations of `mine` the tools have read, still without a confirmed A/B, that no
    agent has taken yet (in no package, no verdict): the work waiting for researchers."""
    final = {r["org_id"] for r in ws.read_csv(pdir / "crawl.csv") if r.get("pass") == "final"} & mine
    if not final:
        return 0
    left = final - mo_tasks.taken_orgs(pdir) - mo_tasks.held_orgs(pdir)
    if not left:
        return 0
    have = {c["org_id"] for c in ws.read_contacts(pdir)
            if c.get("org_id") in left and c.get("grade") in ("A", "B") and c.get("review") != "yes"}
    return len(left - have)


def last_agent_activity(pdir) -> float:
    """When an agent last claimed or finished a package or wrote a verdict (epoch seconds, 0: never)."""
    from datetime import datetime
    stamps = [t.get(k, "") for t in ws.read_csv(pdir / "tasks.csv") for k in ("claimed", "finished")]
    stamps += [v.get("time", "") for v in ws.read_csv(pdir / "verdicts.csv")]
    best = 0.0
    for s in stamps:
        try:
            best = max(best, datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp())
        except ValueError:
            continue
    return best


def run_sync(pdir, rounds: int = 0, max_pages: int = 15, deep_pages: int = 10, goal_ab_org_share: float | None = None,
             agent: bool = False, workers: int = 8, job: dict | None = None, shard: str = "0/1",
             follow: bool = False, discover: bool = False) -> dict:
    """Every organisation flows on its own: own site -> graded -> (rounds an agent asked for,
    at most MAX_ROUNDS, stopping at the first round that adds nothing) -> evidence -> done.
    Many organisations are in flight at once; with follow=True new rows appended to orgs.csv
    (by agents still building the pool) join while it runs, until run(pool_done=True), stop,
    or 30 idle minutes. A whole-project run paces itself on the agents (AHEAD, AGENT_IDLE_MIN)."""
    import os
    from concurrent.futures import ThreadPoolExecutor
    # digging is agent work: only an agent's own package (a batch job) may ask the tools for rounds
    rounds = max(0, min(int(rounds or 0), MAX_ROUNDS)) if job else 0
    workers = max(1, min(int(workers), mo_providers.MAX_ORGS_IN_FLIGHT))
    k, n = (int(x) for x in shard.split("/"))
    sh = (k, n)
    fname = "run.json" if n == 1 else f"run.{k}.json"
    # A batch job: an agent asked for these organisations to be swept (again, with what the
    # playbook now says); it runs beside the main run and ends when they are done.
    batch = {str(i) for i in (job or {}).get("orgs") or []}
    again = bool((job or {}).get("again"))
    if job:
        fname = f"batch.{job['id']}.json"
    try:
        state = json.loads((pdir / fname).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        state = {}
    state["file"], state["shard"] = fname, shard
    state.update(started=state.get("started") or ws._now(), done=False, stopped_because="", pid=os.getpid(),
                 stage="pipeline", settings={"rounds": rounds, "max_pages": max_pages, "workers": workers,
                                             "goal_ab_org_share": goal_ab_org_share, "agent": agent, "follow": follow,
                                             "discover": discover})
    history = state.setdefault("history", [])
    b = ws.brief(pdir)
    focus = b.get("focus") if isinstance(b.get("focus"), dict) else {}
    terms = focus.get("search_terms") or DEFAULT_TERMS
    dir_terms = focus.get("directory_terms") or DEFAULT_DIRECTORY_TERMS
    weights = ws.weights(pdir, "search")
    target = max(1, int(b.get("routes_per_org") or ROUTES_PER_ORG))
    country = str(b.get("country") or "")
    # Ablation: switch stages off to measure what each one brings (brief.ablate).
    off = set(b.get("ablate") or [])
    mine = {str(o.get("id") or o.get("org_id") or "") for o in _mine(ws.read_csv(pdir / "orgs.csv"), sh)}
    if job:
        mine = batch
    # this shard's finished organisations only: counting the whole project's made "done" pass the
    # shard's size after a restart
    finished = set() if again else \
        {r["org_id"] for r in ws.read_csv(pdir / "crawl.csv") if r.get("pass") == "final" and r["org_id"] in mine}
    flow = {"queued": 0, "site": 0, "grading": 0, "digging": 0, "evidence": 0, "done": len(finished), "failed": 0}
    agent_budget = [AGENT_PAGES_PER_ROUND * max(1, rounds // 2)]
    t0, spent0 = time.time(), sum(v["usd"] for v in mo_budget.summary(pdir).values())
    ab0 = measure(pdir, sh).get("ab_routes", 0)
    grader = _Grader(pdir, batch or None)
    grader.thread.start()
    goal_hit = [False]
    agents_idle = [False]   # this process ends; agents' own batches keep going (no project STOP)

    def stopping() -> bool:
        return ws.stop_requested(pdir) or goal_hit[0] or agents_idle[0]

    def move(a: str, b_: str) -> None:
        flow[a] -= 1
        flow[b_] += 1

    def org_flow(org: dict) -> None:
        # the flow counts are what is in flight: an organisation that fails leaves its stage
        # (071: a leak showed 84 "reading sites" with 6 workers)
        at = ["queued"]

        def step(b_: str) -> None:
            move(at[0], b_)
            at[0] = b_
        try:
            _org_flow(org, step)
        except RuntimeError as exc:
            if "thread" in str(exc):   # the machine is out of threads: start fewer at once
                limit[0] = max(1, limit[0] // 2)
            raise
        finally:
            if at[0] != "done":
                move(at[0], "failed")

    def _org_flow(org: dict, move) -> None:
        oid = _oid(org)
        # The trace: one line per organisation in trace.jsonl - how long each stage took and
        # what it brought - to see where the time goes and which stage pays off.
        tr = {"org_id": oid, "org": org.get("name", ""), "source": _source(org), "start": ws._now(),
              "seconds": {}, "emails": {}, "ab": {}}
        clock = [time.time()]

        def mark(stage: str) -> None:
            now = time.time()
            tr["seconds"][stage] = round(tr["seconds"].get(stage, 0) + now - clock[0], 1)
            tr["emails"][stage] = len(_emails_of(pdir, oid))
            tr["ab"][stage] = _has_ab(pdir, oid)
            clock[0] = now

        move("site")
        if not str(org.get("website") or "").strip() and "findsite" not in off:
            found_site = _find_site(pdir, org)
            if found_site:
                org = {**org, "website": found_site}
            tr["found_site"] = found_site
            mark("find_site")
        hints = ws.playbook_for(pdir, org)
        if again or (oid, "site") not in _done_passes(pdir):
            res = ws._crawl_one(pdir, org, max_pages, None, None, "site", hint_paths=hints["paths"])
            ws.append_csv(pdir / "crawl.csv", ws.CRAWL_COLS, res)
            if res.get("status") == "no_dns" and "findsite" not in off:   # the listed site is dead: look again
                new_site = _find_site(pdir, {**org, "website": ""}, replace=True)
                if new_site and mo_pages.site_of(new_site) != mo_pages.site_of(str(org.get("website") or "")):
                    org = {**org, "website": new_site}
                    ws.append_csv(pdir / "crawl.csv", ws.CRAWL_COLS,
                                  ws._crawl_one(pdir, org, max_pages, None, None, "site", hint_paths=hints["paths"]))
        mark("site")
        move("grading")
        grader.wait_for(oid)
        mark("grade_wait")
        move("digging")
        dry = 0
        seg = ws.segment_of(org)
        learn = mo_knowledge.enabled()
        order = mo_knowledge.KNOWLEDGE.dig_order(country, seg) if learn else list(mo_knowledge.DIG_KINDS)
        for rnd in range(1, 0 if "dig" in off else rounds + 1):
            have = _ab_count(pdir, oid)
            if stopping() or have >= target:
                break
            before = len(_emails_of(pdir, oid))
            leads = _leads(pdir, org, rnd, hints["roles"] + terms, dir_terms, None, rank="rank" not in off,
                           playbook_queries=hints["queries"], order=order)
            if leads:
                ws.append_csv(pdir / "crawl.csv", ws.CRAWL_COLS,
                              ws._crawl_one(pdir, org, deep_pages, None, leads, f"deep{rnd}"))
                grader.wait_for(oid)
            tr["dig_rounds"] = rnd
            mark("dig")
            dry = dry + 1 if len(_emails_of(pdir, oid)) == before else 0
            # Bruce: 3-5 key-role addresses per organisation, at least one, "不能硬3-5个降低效率" -
            # with one A/B already, a round that adds no further A/B ends the dig.
            now = _ab_count(pdir, oid)
            if learn:   # which kind of search found an A/B here, for the next organisations and projects
                mo_knowledge.KNOWLEDGE.record_dig(country, seg, order[(rnd - 1) % len(order)], now > have)
            # a round that adds no address ends the dig: later rounds found almost nothing (071)
            if dry >= 1 or (have >= 1 and now == have):
                break
        if learn and (hints["paths"] or hints["queries"]):   # did the playbook lead somewhere
            mo_knowledge.KNOWLEDGE.record_use(country, seg, _has_ab(pdir, oid))
        if agent and not _has_ab(pdir, oid) and mo_config.key("tinyfish"):
            for _oid_, url in _button_pages(pdir, {oid}):
                if agent_budget[0] <= 0:
                    break
                agent_budget[0] -= 1
                try:
                    ws.browse(pdir, url, org_id=oid, org=str(org.get("name") or ""))
                except Exception:  # noqa: BLE001
                    pass
            mark("agent")
        move("evidence")
        if not stopping():
            if "reverify" not in off:
                _reverify(pdir, org)
            if "evidence" not in off and _corroborate(pdir, org, weights):
                grader.wait_for(oid)
            mark("evidence")
        ws.append_csv(pdir / "crawl.csv", ws.CRAWL_COLS, {"org_id": oid, "org": org.get("name", ""), "pass": "final",
                                                          "status": "done", "time": ws._now()})
        tr["total_seconds"] = round(sum(tr["seconds"].values()), 1)
        d, a = yields.get(tr["source"], (0, 0))
        yields[tr["source"]] = (d + 1, a + _has_ab(pdir, oid))
        ws.append_jsonl(pdir / "trace.jsonl", tr)
        move("done")

    def snapshot() -> dict:
        m = measure(pdir, sh)
        spent = sum(v["usd"] for v in mo_budget.summary(pdir).values()) - spent0
        total = len(_mine(ws.read_csv(pdir / "orgs.csv"), sh))
        rate = (flow["done"] - len(finished)) / max(time.time() - t0, 1) * 60
        left = max(total - flow["done"], 0)
        elapsed = max(time.time() - t0, 1)
        state["progress"] = {"flow": dict(flow), "orgs": total, "orgs_per_minute": round(rate, 2),
                             # Bruce's one hard rhythm: valid key-role addresses per 30 minutes
                             "ab_routes_per_30_min": round((m.get("ab_routes", 0) - ab0) / elapsed * 1800, 1),
                             "minutes_left": round(left / rate, 1) if rate else None,
                             # the cost log is the project's, not the shard's: never add this up across shards
                             "usd_project_since_start": round(spent, 3),
                             "usd_per_org": round(spent / max(flow["done"] - len(finished), 1), 4)}
        state["result"] = m
        if agent and (pdir / "agent.csv").exists():
            ws.refresh_agent_runs(pdir)   # merge finished TinyFish Agent pages while the run goes on
        if goal_ab_org_share and m["ab_org_share"] >= goal_ab_org_share:
            goal_hit[0] = True
            state["stopped_because"] = f"goal reached ({m['ab_org_share']}% of organisations have A/B)"
        _save_state(pdir, state)
        return m

    import threading
    import mo_discover
    discovering = threading.Event()
    # building pools is agent work (finders and miners); the tools discover only when asked
    if discover and k == 0 and "discover" not in off and mo_discover.combos(pdir):
        discovering.set()

        def discovery_line():
            try:
                state["discovery"] = mo_discover.discover(pdir, stopping)
            finally:
                discovering.clear()
        threading.Thread(target=discovery_line, daemon=True, name="discover").start()
    def prune(fs: list) -> list:
        for f in fs:
            if f.done() and f.exception() is not None:
                state.setdefault("errors", []).append(str(f.exception())[:200])
        return [f for f in fs if not f.done()]

    yields: dict[str, tuple[int, int]] = {}   # source -> (organisations done, with A/B) this run
    submitted, futures = set(finished), []
    waiting: dict[str, list] = {}   # source -> organisations not yet started
    last_new, last_snap, last_export = time.time(), 0.0, time.time()
    last_watch = [time.time()]
    limit = [workers * 2]   # organisations started and not finished; halved when threads run out
    paced = not job          # an agent's own batch is never paced: the agent is right there
    pause = {"backlog": 0, "since": 0.0, "checked": 0.0}
    with ThreadPoolExecutor(max_workers=workers) as pool:
        while True:
            if paced and time.time() - pause["checked"] > PACE_CHECK_S:
                pause["checked"] = time.time()
                pause["backlog"] = agent_backlog(pdir, mine | submitted)
                if pause["backlog"] >= AHEAD:
                    pause["since"] = pause["since"] or time.time()
                    state["waiting_for_agents"] = {
                        "organisations": pause["backlog"], "since": ws._now(),
                        "note": f"{pause['backlog']} organisations are read and wait for researchers; the tools "
                                "start no more until agents take them - start researchers on the dig lane "
                                "(tasks(action='claim', kind='dig'))"}
                    idle = time.time() - max(last_agent_activity(pdir), pause["since"])
                    if idle > AGENT_IDLE_MIN * 60:
                        state["stopped_because"] = (f"no agent took work for {AGENT_IDLE_MIN} minutes: the tools do "
                                                    "not research alone; start researchers and run again")
                        agents_idle[0] = True
                else:
                    pause["since"] = 0.0
                    state.pop("waiting_for_agents", None)
            if not stopping():
                for org in ([o for o in ws.read_csv(pdir / "orgs.csv") if _oid(o) in batch] if job
                            else _mine(ws.read_csv(pdir / "orgs.csv"), sh)):
                    oid = _oid(org)
                    if oid and oid not in submitted and org.get("scope") != "out":
                        submitted.add(oid)
                        flow["queued"] += 1
                        waiting.setdefault(_source(org), []).append(org)
                        last_new = time.time()
                # Start organisations from every source in turn, a few more than the workers:
                # a source pooled later (clubs after a big register) does not wait for all of it.
                # After 30 organisations a source's own A/B rate sets its share (Bruce's rule:
                # >=10% scale it, <5% stop spending on it): a strong source gets two turns, a
                # weak one waits until the others are empty - its organisations are never dropped.
                futures = prune(futures)
                held, deferred = mo_tasks.held_orgs(pdir) - batch, []   # an agent has these (its own batch excepted)
                # a researcher's package is the researcher's: the whole-project run leaves it alone
                taken = set() if job else mo_tasks.taken_orgs(pdir)
                while waiting and len(futures) < limit[0] and not (paced and pause["backlog"] >= AHEAD):
                    turns = {src: _turns(yields.get(src)) for src in waiting}
                    if not any(turns.values()):
                        turns = dict.fromkeys(waiting, 1)
                    for src, k in turns.items():
                        for _ in range(k):
                            if waiting.get(src):
                                org = waiting[src].pop(0)
                                if _oid(org) in taken:
                                    flow["queued"] -= 1
                                    continue
                                if _oid(org) in held:
                                    deferred.append(org)
                                else:
                                    futures.append(pool.submit(org_flow, org))
                        if not waiting.get(src):
                            waiting.pop(src, None)
                for org in deferred:   # back of the queue; taken once the agent hands the package back
                    waiting.setdefault(_source(org), []).append(org)
                state["source_yield"] = {src: {"done": d, "with_ab": a, "turns": _turns((d, a))}
                                         for src, (d, a) in yields.items()}
                for src, (d, a) in yields.items():
                    if _turns((d, a)) == 0:
                        import mo_journal
                        mo_journal.auto(pdir, f"source:{src}", f"source '{src}': A/B for {a} of {d} organisations "
                                        f"(under 5%) - moved to the back; a better source is worth looking for",
                                        "source_yield", every_s=10 ** 9)
            else:
                futures = prune(futures)
            # organisations waiting for an agent to hand them back keep the run alive (a stale claim
            # frees them after CLAIM_MINUTES, so this cannot hang)
            busy = bool(futures) or (bool(waiting) and not stopping())
            pool_open = discovering.is_set() or (
                follow and not (pdir / "POOL_DONE").exists() and time.time() - last_new < 1800)
            if time.time() - last_snap > 30:
                last_snap = time.time()
                m = snapshot()
                ws.snapshot_contacts(pdir)
                if time.time() - last_watch[0] > 300:   # a provider failing far more than usual
                    last_watch[0] = time.time()
                    import mo_journal
                    mo_journal.watch(pdir)
                if time.time() - last_export > 1800:   # long runs: a fresh workbook every half hour
                    last_export = time.time()
                    mo_export.export(pdir)
                    history.append({"at": ws._now(), **m})
            if not busy and (stopping() or not pool_open):
                break
            time.sleep(3)
    prune(futures)
    state["late_readers_left"] = mo_providers.drain()   # slower readers' contacts land before the export
    grader.stop.set()
    grader.kick.set()
    grader.grade_now()
    m = snapshot()
    history.append({"at": ws._now(), **m})
    if ws.stop_requested(pdir) and not state.get("stopped_because"):
        state["stopped_because"] = "stopped on request"
    state["stopped_because"] = state.get("stopped_because") or "every organisation went through the pipeline"
    out = {} if job else mo_export.export(pdir)   # a batch leaves the workbook to the main run / export()
    state.update(done=True, stage="done", export=out, result=m)
    if not any(_alive(p) for p in _pids(pdir) if str(p) != str(os.getpid())):
        (pdir / "STOP").unlink(missing_ok=True)
    _save_state(pdir, state)
    return state


def _done_passes(pdir) -> set:
    return {(r["org_id"], r.get("pass") or "site") for r in ws.read_csv(pdir / "crawl.csv")
            if r.get("status") in ("done", "no_website", "no_dns")}


_procs: dict[int, object] = {}


def _alive(pid) -> bool:
    """Is the run process still working? A child this server started is asked directly (a
    finished child stays a zombie until reaped); anything else by signal 0."""
    import os
    try:
        pid = int(pid)
    except (ValueError, TypeError):
        return False
    if pid in _procs:
        return _procs[pid].poll() is None
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    try:
        with open(f"/proc/{pid}/stat", encoding="utf-8") as fh:
            return fh.read().split(")")[-1].split()[0] != "Z"
    except OSError:
        return True


def _pids(pdir) -> list[str]:
    f = pdir / "run.pid"
    return [x for x in f.read_text().split()] if f.exists() else []


def run(pdir, rounds: int = 0, max_pages: int = 15, goal_ab_org_share: float | None = None,
        agent: bool = False, stop: bool = False, workers: int = 8, shards: int = 1,
        follow: bool = False, pool_done: bool = False, orgs: list | None = None, again: bool = False,
        discover: bool = False) -> dict:
    """Start the long run as its own process(es) - shards splits orgs.csv between them - or stop it.
    orgs=[ids]: a batch job for just these organisations, beside any main run (again=True sweeps
    them anew, with the playbook as it is now)."""
    import os
    import subprocess
    import sys
    from pathlib import Path
    if orgs:
        import hashlib
        ids = list(dict.fromkeys(str(i) for i in orgs if str(i).strip()))[:200]
        jid = hashlib.sha256(("|".join(ids) + ws._now()).encode()).hexdigest()[:8]
        job = {"id": jid, "orgs": ids, "again": bool(again)}
        _save_state(pdir, {"file": f"batch.{jid}.json", "stage": "starting", "done": False, "orgs": len(ids)})
        opts = {"rounds": rounds, "max_pages": max_pages, "workers": min(workers, max(4, len(ids))), "agent": agent}
        server = Path(__file__).resolve().parent / "server.py"
        env = dict(os.environ, MARKETOPS_PROJECTS=str(pdir.parent))
        cmd = [sys.executable, str(server), "batch", pdir.name, "--job", json.dumps(job), "--opts", json.dumps(opts)]
        with open(pdir / "batch.log", "a", encoding="utf-8") as log:
            proc = subprocess.Popen(cmd, stdout=log, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                                    start_new_session=True, env=env)
        _procs[proc.pid] = proc
        return {"batch": jid, "orgs": len(ids), "pid": proc.pid,
                "next": "orgs(project, ids=[...]) shows each organisation's pages, addresses and grades as they "
                        "land; project(name).batches shows when this batch is done."}
    pids = [p for p in _pids(pdir) if _alive(p)]
    if pool_done:
        (pdir / "POOL_DONE").write_text(ws._now(), encoding="utf-8")
        return {"pool_done": True, "note": "the running pipeline finishes the organisations it has and ends"}
    if stop:
        if not pids:
            return {"stopped": False, "note": "no run is going in this project"}
        (pdir / "STOP").write_text(ws._now(), encoding="utf-8")
        return {"stopped": True, "processes": len(pids),
                "note": "each process finishes the organisations in hand, grades, exports and ends; run again to resume"}
    if pids:
        return {"already_running": True, "pids": pids, "state": run_states(pdir)}
    (pdir / "STOP").unlink(missing_ok=True)
    if follow:
        (pdir / "POOL_DONE").unlink(missing_ok=True)
    shards = max(1, min(int(shards), 16))
    # organisations in flight on the whole machine stay under its thread and memory budget
    # (071: 4 shards x 6 x 4 pages + browser threads hit pids.max, then memory.high stalled it)
    shards = min(shards, max(1, mo_providers.MAX_ORGS_IN_FLIGHT // 8))   # each process runs its own browser
    workers = max(1, min(int(workers), mo_providers.MAX_ORGS_IN_FLIGHT // shards or 1))
    opts = {"rounds": 0, "max_pages": max_pages, "workers": workers,   # whole-project runs never dig
            "goal": goal_ab_org_share, "agent": agent, "follow": follow, "discover": discover}
    for k in range(shards):
        fname = "run.json" if shards == 1 else f"run.{k}.json"
        _save_state(pdir, {"file": fname, "shard": f"{k}/{shards}", "stage": "starting", "done": False})
    server = Path(__file__).resolve().parent / "server.py"
    env = dict(os.environ, MARKETOPS_PROJECTS=str(pdir.parent))
    cmd = [sys.executable, str(server), "supervise", pdir.name, "--shards", str(shards), "--opts", json.dumps(opts)]
    with open(pdir / "supervisor.log", "a", encoding="utf-8") as log:
        proc = subprocess.Popen(cmd, stdout=log, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                                start_new_session=True, env=env)
    _procs[proc.pid] = proc
    (pdir / "run.pid").write_text(str(proc.pid), encoding="utf-8")
    return {"started": True, "processes": shards, "supervisor": proc.pid,
            "next": "project(name).run shows each process's stage, round and progress (organisations per minute, "
                    "A/B found per 30 minutes, minutes left, cost per organisation); a process that dies is "
                    "restarted and resumes; run(name, stop=True) stops them gracefully; meanwhile worker agents "
                    "can take packages from tasks(...). It keeps going if this session ends."}


def _shard_cmd(name: str, k: int, shards: int, opts: dict) -> list[str]:
    import sys
    from pathlib import Path
    server = Path(__file__).resolve().parent / "server.py"
    cmd = [sys.executable, str(server), "run", name, "--rounds", str(opts.get("rounds", 0)), "--max-pages",
           str(opts.get("max_pages", 15)), "--workers", str(opts.get("workers", 8)), "--shard", f"{k}/{shards}"]
    if opts.get("goal"):
        cmd += ["--goal", str(opts["goal"])]
    if opts.get("agent"):
        cmd.append("--agent")
    if opts.get("follow"):
        cmd.append("--follow")
    if opts.get("discover"):
        cmd.append("--discover")
    return cmd


MAX_RESTARTS = 5


def supervise(pdir, shards: int, opts: dict, poll: float = 15.0, clear_stop: bool = True) -> dict:
    """Keep every shard running until it is done or a stop is asked: a shard that dies (crash,
    kill, lost machine) is started again and resumes where it was - finished organisations
    are skipped. Bruce's most frequent complaint was work that stopped by itself."""
    if clear_stop:
        # a STOP left by an earlier run (shards ending together each saw the others alive and none
        # removed it) must not end this one before it starts
        (pdir / "STOP").unlink(missing_ok=True)
    import os
    import subprocess
    env = dict(os.environ, MARKETOPS_PROJECTS=str(pdir.parent))
    procs: dict[int, tuple] = {}
    restarts = dict.fromkeys(range(shards), 0)

    def start(k: int) -> None:
        log = open(pdir / ("run.log" if shards == 1 else f"run.{k}.log"), "a", encoding="utf-8")
        p = subprocess.Popen(_shard_cmd(pdir.name, k, shards, opts), stdout=log, stderr=subprocess.STDOUT,
                             stdin=subprocess.DEVNULL, env=env)
        procs[k] = (p, time.time(), log)

    def state_of(k: int) -> dict:
        try:
            return json.loads((pdir / ("run.json" if shards == 1 else f"run.{k}.json")).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}

    def write_pids() -> None:
        live = [str(p.pid) for p, _t, _l in procs.values() if p.poll() is None]
        (pdir / "run.pid").write_text("\n".join([str(os.getpid())] + live), encoding="utf-8")

    for k in range(shards):
        start(k)
    write_pids()
    while True:
        running = False
        for k, (p, t0, log) in list(procs.items()):
            if p.poll() is None:
                running = True
                continue
            st = state_of(k)
            finished = st.get("done") and st.get("stage") != "failed"
            if finished or ws.stop_requested(pdir) or restarts[k] >= MAX_RESTARTS:
                continue
            restarts[k] += 2 if time.time() - t0 < 60 else 1   # dying at once twice is a crash loop
            log.write(f"\n[supervisor {ws._now()}] shard {k} ended without finishing "
                      f"({st.get('stage') or 'no state'}); restart {restarts[k]}\n")
            log.flush()
            import mo_journal
            mo_journal.auto(pdir, f"restart:{k}:{restarts[k]}", f"shard {k} ended without finishing "
                            f"({st.get('stage') or 'no state'}: {str(st.get('error') or '')[:160]}); restarted",
                            "supervisor.log")
            st.update(restarts=restarts[k], done=False)
            _save_state(pdir, st if st.get("file") else {**st, "file": "run.json" if shards == 1 else f"run.{k}.json"})
            start(k)
            running = True
        write_pids()
        if not running:
            break
        time.sleep(poll)
    return {"restarts": restarts}


def batch_states(pdir) -> list[dict]:
    out = []
    for f in sorted(pdir.glob("batch.*.json")):
        try:
            st = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        out.append({"batch": f.name.split(".")[1], **{k: st.get(k) for k in ("stage", "done", "error", "orgs", "progress")}})
    return out


def run_states(pdir) -> list[dict]:
    out = []
    for f in sorted(pdir.glob("run*.json")):
        try:
            st = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        out.append({k: st.get(k) for k in ("shard", "stage", "progress", "stopped_because", "result", "done",
                                           "error", "errors", "restarts", "source_yield",
                                           "waiting_for_agents")})
    return out
