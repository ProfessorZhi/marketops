"""Long research without a person in the loop: crawl, grade, find the gaps, dig, repeat.

    round 0  read every organisation's own site (home + best contact/people pages, PDFs)
    grade    Jev grades every new address
    round n  for organisations still without A/B, dig a different way each round:
             1 name + the market's words for the people wanted (focus.search_terms)
             2 inside their own site, PDFs included (site:, filetype:pdf)
             3 third-party lists: associations, federations, council directories
               (focus.directory_terms); maps for organisations without a website
             4 broad name + city + email; then the cycle repeats with what is still missing
             read what was found, optionally let the TinyFish Agent open pages whose email
             buttons hide the address; grade again
    stop     when the goal is met, two rounds in a row add no new A/B organisation, or
             rounds run out (default 6)
    export   the workbook and the routes CSV

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
    emails = [c for c in ws.read_csv(pdir / "contacts.csv") if c.get("kind") == "email"]
    return mo_export.metrics(_mine(ws.read_csv(pdir / "orgs.csv"), shard), emails)


def _gap_orgs(pdir, shard: tuple[int, int] = (0, 1)) -> list[dict]:
    have = {c["org_id"] for c in ws.read_csv(pdir / "contacts.csv")
            if c.get("kind") == "email" and c.get("grade") in ("A", "B") and c.get("review") != "yes"}
    return [o for o in _mine(ws.read_csv(pdir / "orgs.csv"), shard)
            if str(o.get("id") or o.get("org_id") or "") not in have]


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
           per_org: int = 8) -> list[str]:
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
    ranked = _rank(pdir, org, list(found.values())[:20])
    return [h["url"] for h in ranked][:per_org]


def _button_pages(pdir, org_ids: set[str]) -> list[tuple[str, str]]:
    pages = []
    for r in ws.read_csv(pdir / "crawl.csv"):
        if r.get("org_id") in org_ids:
            for url in re.findall(r"email buttons without address on (\S+): try browse", r.get("note", "")):
                pages.append((r["org_id"], url))
    return pages


def _progress(pdir, state: dict, job: dict) -> None:
    """Throughput, time left and projected cost of the current stage, written to run.json."""
    import mo_budget
    now = time.time()
    t0 = job.setdefault("t0", now)
    done, total = job.get("finished", 0), job.get("total", 0)
    spent = sum(v["usd"] for v in mo_budget.summary(pdir).values())
    rate = done / max(now - t0, 1) * 60
    left = max(total - done, 0)
    state["progress"] = {"stage": state.get("stage"), "done": done, "total": total,
                         "orgs_per_minute": round(rate, 2),
                         "minutes_left_this_stage": round(left / rate, 1) if rate else None,
                         "usd_spent": round(spent, 3),
                         "usd_per_org_so_far": round(spent / max(done, 1), 4)}
    if job.get("finished", 0) % 5 in (0, 1) or not left:
        _save_state(pdir, state)


def run_sync(pdir, rounds: int = 6, max_pages: int = 15, deep_pages: int = 10, goal_ab_org_share: float | None = None,
             agent: bool = False, workers: int = 12, job: dict | None = None, shard: str = "0/1") -> dict:
    import os
    k, n = (int(x) for x in shard.split("/"))
    sh = (k, n)
    fname = "run.json" if n == 1 else f"run.{k}.json"
    try:
        state = json.loads((pdir / fname).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        state = {}
    state["file"], state["shard"] = fname, shard
    state.update(started=state.get("started") or ws._now(), done=False, stopped_because="", pid=os.getpid(),
                 settings={"rounds": rounds, "max_pages": max_pages, "goal_ab_org_share": goal_ab_org_share,
                           "agent": agent})
    job = job if job is not None else {"finished": 0, "total": 0}
    history = state.setdefault("history", [])
    b = ws.brief(pdir)
    focus = b.get("focus") if isinstance(b.get("focus"), dict) else {}
    terms = focus.get("search_terms") or DEFAULT_TERMS
    dir_terms = focus.get("directory_terms") or DEFAULT_DIRECTORY_TERMS
    dry = 0
    engines = None  # by the project's provider weights

    def stage(name, **extra):
        state.update(stage=name, **extra)
        job.update(stage=name, finished=0, total=0, t0=time.time())
        _save_state(pdir, state)
    job["on_progress"] = lambda: _progress(pdir, state, job)

    def stopping() -> bool:
        if ws.stop_requested(pdir):
            state["stopped_because"] = "stopped on request"
            return True
        return False

    orgs = _mine(ws.read_csv(pdir / "orgs.csv"), sh)
    if not orgs:
        raise ValueError("orgs.csv is empty: build the organisation pool first")
    stage("crawl", round=0, orgs=len(orgs))
    ws.crawl_sync(pdir, orgs, max_pages, None, workers, job, label="site")
    stage("grade", round=0)
    mo_grade.auto(pdir)
    m = measure(pdir, sh)
    history.append({"round": 0, **m, "time": ws._now()})
    for rnd in range(1, rounds + 1):
        if stopping():
            break
        if goal_ab_org_share and m["ab_org_share"] >= goal_ab_org_share:
            state["stopped_because"] = f"goal reached ({m['ab_org_share']}% of organisations have A/B)"
            break
        gaps = _gap_orgs(pdir, sh)
        if not gaps:
            state["stopped_because"] = "every organisation has an A/B route"
            break
        stage("deep-search", round=rnd, gaps=len(gaps))
        extra: dict[str, list[str]] = {}
        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(max_workers=workers) as pool:
            for org, leads in zip(gaps, pool.map(lambda o: _leads(pdir, o, rnd, terms, dir_terms, engines), gaps)):
                if leads:
                    extra[str(org.get("id") or org.get("org_id") or "")] = leads
        targets = [o for o in gaps if str(o.get("id") or o.get("org_id") or "") in extra]
        stage("deep-read", round=rnd, gaps=len(gaps), with_leads=len(targets))
        ws.crawl_sync(pdir, targets, deep_pages, None, workers, job, label=f"deep{rnd}", extra_urls=extra)
        if agent and mo_config.key("tinyfish"):
            gap_ids = {str(o.get("id") or o.get("org_id") or "") for o in gaps}
            names = {str(o.get("id") or o.get("org_id") or ""): o.get("name", "") for o in gaps}
            stage("agent", round=rnd)
            for oid, url in _button_pages(pdir, gap_ids)[:AGENT_PAGES_PER_ROUND]:
                try:
                    ws.browse(pdir, url, org_id=oid, org=names.get(oid, ""))
                except Exception:  # noqa: BLE001
                    continue
            deadline = time.time() + 900
            while time.time() < deadline and any(
                    r["status"] in ("RUNNING", "PENDING") for r in ws.refresh_agent_runs(pdir)):
                time.sleep(20)
        if stopping():
            mo_grade.auto(pdir)
            break
        stage("grade", round=rnd)
        mo_grade.auto(pdir)
        before = m["ab_orgs"]
        m = measure(pdir, sh)
        history.append({"round": rnd, **m, "new_ab_orgs": m["ab_orgs"] - before, "time": ws._now()})
        dry = dry + 1 if m["ab_orgs"] == before else 0
        if dry >= 2:
            state["stopped_because"] = f"rounds {rnd - 1} and {rnd} found no new A/B organisation"
            break
    else:
        state["stopped_because"] = state.get("stopped_because") or f"{rounds} rounds done"
    stage("export")
    m = measure(pdir, sh)
    out = mo_export.export(pdir)
    state.update(done=True, stage="done", export=out, result=m)
    if not any(_alive(p) for p in _pids(pdir) if str(p) != str(os.getpid())):
        (pdir / "STOP").unlink(missing_ok=True)
    _save_state(pdir, state)
    return state


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
        agent: bool = False, stop: bool = False, workers: int = 12, shards: int = 1) -> dict:
    """Start the long run as its own process(es) - shards splits orgs.csv between them - or stop it."""
    import os
    import subprocess
    import sys
    from pathlib import Path
    pids = [p for p in _pids(pdir) if _alive(p)]
    if stop:
        if not pids:
            return {"stopped": False, "note": "no run is going in this project"}
        (pdir / "STOP").write_text(ws._now(), encoding="utf-8")
        return {"stopped": True, "processes": len(pids),
                "note": "each process finishes the organisations in hand, grades, exports and ends; run again to resume"}
    if pids:
        return {"already_running": True, "pids": pids, "state": run_states(pdir)}
    (pdir / "STOP").unlink(missing_ok=True)
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
        out.append({k: st.get(k) for k in ("shard", "stage", "round", "gaps", "progress", "stopped_because",
                                           "result", "done", "error")})
    return out
