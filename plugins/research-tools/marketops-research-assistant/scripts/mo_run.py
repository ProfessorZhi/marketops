"""Long research without a person in the loop, as a pipeline: organisations flow through
the stages one by one, many at a time, and new ones join while the pool is still growing.

    site      read the organisation's own site (home + best contact/people pages, PDFs)
    grade     a background grader batches every new address to Jev, names and roles included
    dig       no confirmed A/B yet: dig round 1..n, a different way each round (below)
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

import mo_config
import mo_export
import mo_grade
import mo_pages
import mo_providers
import mo_workspace as ws

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


def _queries(org: dict, rnd: int, terms: list[str], dir_terms: list[str]) -> list[tuple[str, bool]]:
    """The searches for one organisation in round rnd: each round digs a different way.
    Returns (query, maps)."""
    name = str(org.get("name") or "").strip()
    site = mo_pages.site_of(str(org.get("website") or ""))
    city = str(org.get("city") or "").strip()
    q = f'"{name}"' if name else ""
    kind = (rnd - 1) % 4
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
    if len(hits) < 2 or not mo_config.key("jev"):
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
        import mo_budget
        mo_budget.record(pdir, "jev", "grade", True, f"rank leads x{len(hits)}")
        choices = getattr(resp, "choices", None) or {}

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


def _leads(pdir, org: dict, rnd: int, terms: list[str], dir_terms: list[str], engines: list[str] | None,
           per_org: int = 8, rank: bool = True) -> list[str]:
    """Pages worth reading for one organisation: its own pages and PDFs, and other sites
    whose result mentions it (directories, federation and council pages)."""
    name = str(org.get("name") or "").strip()
    site = mo_pages.site_of(str(org.get("website") or ""))
    seen = set(ws._pages(pdir))
    found: dict[str, dict] = {}
    weights = ws.weights(pdir, "search")
    for query, maps in _queries(org, rnd, terms, dir_terms):
        try:
            res = mo_providers.search(query, None, n=8, maps=maps, project_dir=pdir, weights=weights)
        except Exception:  # noqa: BLE001
            continue
        for hit in res["results"]:
            url = hit.get("url") or ""
            if not url.startswith("http") or url in found or url in seen:
                continue
            hay = f"{hit.get('title', '')} {hit.get('snippet', '')}".lower()
            own = site and mo_pages.site_of(url) == site
            mentions = name and all(w in hay for w in name.lower().split()[:3])
            if own or mentions or (maps and not site):
                found[url] = hit
    ranked = _rank(pdir, org, list(found.values())[:20]) if rank else list(found.values())
    return [h["url"] for h in ranked][:per_org]


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
        seen, n = set(ws._pages(pdir)), 0
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


class _Grader:
    """One background grader per run: whatever new addresses the organisations produce are
    graded together in batches (Jev stays efficient while the pipeline keeps moving)."""

    def __init__(self, pdir):
        import threading
        self.pdir, self.stop, self.busy = pdir, threading.Event(), threading.Lock()
        self.kick = threading.Event()
        self.enabled = bool(mo_config.key("jev"))   # no Jev: nothing to wait for, the agent grades later
        self.thread = threading.Thread(target=self._loop, daemon=True, name="grader")

    def _pending(self) -> bool:
        return any(c.get("kind") == "email" and not c.get("grade") for c in ws.read_contacts(self.pdir))

    def grade_now(self) -> None:
        with self.busy:
            try:
                import fcntl
                fh = open(self.pdir / "grade.lock", "a")
                try:
                    fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except OSError:
                    fh.close()
                    return  # another process is grading this project right now
            except ImportError:
                fh = None
            try:
                if self._pending():
                    mo_grade.auto(self.pdir)
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
        if not self.enabled:
            return
        deadline = time.time() + timeout
        while time.time() < deadline:
            if not any(not c.get("grade") for c in _emails_of(self.pdir, oid)):
                return
            self.kick.set()
            time.sleep(1)


def run_sync(pdir, rounds: int = 6, max_pages: int = 15, deep_pages: int = 10, goal_ab_org_share: float | None = None,
             agent: bool = False, workers: int = 12, job: dict | None = None, shard: str = "0/1",
             follow: bool = False) -> dict:
    """The pipeline. Every organisation flows on its own: own site -> graded -> (no confirmed
    A/B) dig round 1..n, each graded -> evidence corroborated -> done. Many organisations are
    in flight at once; with follow=True new rows appended to orgs.csv (by agents still building
    the pool) join while it runs, until run(pool_done=True), stop, or 30 idle minutes."""
    import os
    from concurrent.futures import ThreadPoolExecutor
    k, n = (int(x) for x in shard.split("/"))
    sh = (k, n)
    fname = "run.json" if n == 1 else f"run.{k}.json"
    try:
        state = json.loads((pdir / fname).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        state = {}
    state["file"], state["shard"] = fname, shard
    state.update(started=state.get("started") or ws._now(), done=False, stopped_because="", pid=os.getpid(),
                 stage="pipeline", settings={"rounds": rounds, "max_pages": max_pages, "workers": workers,
                                             "goal_ab_org_share": goal_ab_org_share, "agent": agent, "follow": follow})
    history = state.setdefault("history", [])
    b = ws.brief(pdir)
    focus = b.get("focus") if isinstance(b.get("focus"), dict) else {}
    terms = focus.get("search_terms") or DEFAULT_TERMS
    dir_terms = focus.get("directory_terms") or DEFAULT_DIRECTORY_TERMS
    weights = ws.weights(pdir, "search")
    # Ablation: switch stages off to measure what each one brings (brief.ablate).
    off = set(b.get("ablate") or [])
    finished = {r["org_id"] for r in ws.read_csv(pdir / "crawl.csv") if r.get("pass") == "final"}
    flow = {"queued": 0, "site": 0, "grading": 0, "digging": 0, "evidence": 0, "done": len(finished)}
    agent_budget = [AGENT_PAGES_PER_ROUND * max(1, rounds // 2)]
    import mo_budget
    t0, spent0 = time.time(), sum(v["usd"] for v in mo_budget.summary(pdir).values())
    grader = _Grader(pdir)
    grader.thread.start()
    goal_hit = [False]

    def stopping() -> bool:
        return ws.stop_requested(pdir) or goal_hit[0]

    def move(a: str, b_: str) -> None:
        flow[a] -= 1
        flow[b_] += 1

    def org_flow(org: dict) -> None:
        oid = _oid(org)
        move("queued", "site")
        if (oid, "site") not in _done_passes(pdir):
            ws.append_csv(pdir / "crawl.csv", ws.CRAWL_COLS, ws._crawl_one(pdir, org, max_pages, None, None, "site"))
        move("site", "grading")
        grader.wait_for(oid)
        move("grading", "digging")
        dry = 0
        for rnd in range(1, 0 if "dig" in off else rounds + 1):
            if stopping() or _has_ab(pdir, oid):
                break
            before = len(_emails_of(pdir, oid))
            leads = _leads(pdir, org, rnd, terms, dir_terms, None, rank="rank" not in off)
            if leads:
                ws.append_csv(pdir / "crawl.csv", ws.CRAWL_COLS,
                              ws._crawl_one(pdir, org, deep_pages, None, leads, f"deep{rnd}"))
                grader.wait_for(oid)
            dry = dry + 1 if len(_emails_of(pdir, oid)) == before else 0
            if dry >= 2:
                break
        if agent and not _has_ab(pdir, oid) and mo_config.key("tinyfish"):
            for _oid_, url in _button_pages(pdir, {oid}):
                if agent_budget[0] <= 0:
                    break
                agent_budget[0] -= 1
                try:
                    ws.browse(pdir, url, org_id=oid, org=str(org.get("name") or ""))
                except Exception:  # noqa: BLE001
                    pass
        move("digging", "evidence")
        if not stopping():
            if "reverify" not in off:
                _reverify(pdir, org)
            if "evidence" not in off and _corroborate(pdir, org, weights):
                grader.wait_for(oid)
        ws.append_csv(pdir / "crawl.csv", ws.CRAWL_COLS, {"org_id": oid, "org": org.get("name", ""), "pass": "final",
                                                          "status": "done", "time": ws._now()})
        move("evidence", "done")

    def snapshot() -> dict:
        m = measure(pdir, sh)
        spent = sum(v["usd"] for v in mo_budget.summary(pdir).values()) - spent0
        total = len(_mine(ws.read_csv(pdir / "orgs.csv"), sh))
        rate = (flow["done"] - len(finished)) / max(time.time() - t0, 1) * 60
        left = max(total - flow["done"], 0)
        state["progress"] = {"flow": dict(flow), "orgs": total, "orgs_per_minute": round(rate, 2),
                             "minutes_left": round(left / rate, 1) if rate else None,
                             "usd_this_run": round(spent, 3),
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
    if k == 0 and "discover" not in off and mo_discover.combos(pdir):
        discovering.set()

        def discovery_line():
            try:
                state["discovery"] = mo_discover.discover(pdir, stopping)
            finally:
                discovering.clear()
        threading.Thread(target=discovery_line, daemon=True, name="discover").start()
    submitted, futures = set(finished), []
    last_new, last_snap, last_export = time.time(), 0.0, time.time()
    with ThreadPoolExecutor(max_workers=workers) as pool:
        while True:
            if not stopping():
                for org in _mine(ws.read_csv(pdir / "orgs.csv"), sh):
                    oid = _oid(org)
                    if oid and oid not in submitted and org.get("scope") != "out":
                        submitted.add(oid)
                        flow["queued"] += 1
                        futures.append(pool.submit(org_flow, org))
                        last_new = time.time()
            busy = any(not f.done() for f in futures)
            pool_open = discovering.is_set() or (
                follow and not (pdir / "POOL_DONE").exists() and time.time() - last_new < 1800)
            if time.time() - last_snap > 30:
                last_snap = time.time()
                m = snapshot()
                ws.snapshot_contacts(pdir)
                if time.time() - last_export > 1800:   # long runs: a fresh workbook every half hour
                    last_export = time.time()
                    mo_export.export(pdir)
                    history.append({"at": ws._now(), **m})
            if not busy and (stopping() or not pool_open):
                break
            time.sleep(3)
    for f in futures:
        if f.exception() is not None:
            state.setdefault("errors", []).append(str(f.exception())[:200])
    grader.stop.set()
    grader.kick.set()
    grader.grade_now()
    m = snapshot()
    history.append({"at": ws._now(), **m})
    if ws.stop_requested(pdir):
        state["stopped_because"] = "stopped on request"
    state["stopped_because"] = state.get("stopped_because") or "every organisation went through the pipeline"
    out = mo_export.export(pdir)
    state.update(done=True, stage="done", export=out, result=m)
    if not any(_alive(p) for p in _pids(pdir) if str(p) != str(os.getpid())):
        (pdir / "STOP").unlink(missing_ok=True)
    _save_state(pdir, state)
    return state


def _done_passes(pdir) -> set:
    return {(r["org_id"], r.get("pass") or "site") for r in ws.read_csv(pdir / "crawl.csv")
            if r.get("status") in ("done", "no_website")}


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


def run(pdir, rounds: int = 6, max_pages: int = 15, goal_ab_org_share: float | None = None,
        agent: bool = False, stop: bool = False, workers: int = 12, shards: int = 1,
        follow: bool = False, pool_done: bool = False) -> dict:
    """Start the long run as its own process(es) - shards splits orgs.csv between them - or stop it."""
    import os
    import subprocess
    import sys
    from pathlib import Path
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
    server = Path(__file__).resolve().parent / "server.py"
    env = dict(os.environ, MARKETOPS_PROJECTS=str(pdir.parent))
    shards = max(1, min(int(shards), 16))
    started = []
    for k in range(shards):
        cmd = [sys.executable, str(server), "run", pdir.name, "--rounds", str(rounds), "--max-pages",
               str(max_pages), "--workers", str(workers), "--shard", f"{k}/{shards}"]
        if goal_ab_org_share:
            cmd += ["--goal", str(goal_ab_org_share)]
        if agent:
            cmd.append("--agent")
        if follow:
            cmd.append("--follow")
        with open(pdir / ("run.log" if shards == 1 else f"run.{k}.log"), "a", encoding="utf-8") as log:
            proc = subprocess.Popen(cmd, stdout=log, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                                    start_new_session=True, env=env)
        _procs[proc.pid] = proc
        started.append(str(proc.pid))
        fname = "run.json" if shards == 1 else f"run.{k}.json"
        _save_state(pdir, {"file": fname, "shard": f"{k}/{shards}", "stage": "starting", "done": False,
                           "pid": proc.pid})
    (pdir / "run.pid").write_text("\n".join(started), encoding="utf-8")
    return {"started": True, "processes": len(started), "pids": started,
            "next": "project(name).run shows each process's stage, round and progress (organisations per minute, "
                    "minutes left, cost per organisation); run(name, stop=True) stops them gracefully; meanwhile "
                    "worker agents can take packages from tasks(...). It keeps going if this session ends."}


def run_states(pdir) -> list[dict]:
    out = []
    for f in sorted(pdir.glob("run*.json")):
        try:
            st = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        out.append({k: st.get(k) for k in ("shard", "stage", "progress", "stopped_because", "result", "done",
                                           "error", "errors")})
    return out
