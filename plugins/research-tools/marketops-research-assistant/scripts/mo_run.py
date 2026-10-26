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
import threading
import time

import mo_config
import mo_export
import mo_grade
import mo_pages
import mo_providers
import mo_workspace as ws

DEFAULT_TERMS = ["contact email", "staff team board committee", "facilities grounds manager"]
DEFAULT_DIRECTORY_TERMS = ["association member list", "federation clubs", "directory contact"]


def _save_state(pdir, state: dict) -> None:
    state["updated"] = ws._now()
    (pdir / "run.json").write_text(json.dumps(state, ensure_ascii=False, indent=1), encoding="utf-8")


def measure(pdir) -> dict:
    emails = [c for c in ws.read_csv(pdir / "contacts.csv") if c.get("kind") == "email"]
    return mo_export.metrics(ws.read_csv(pdir / "orgs.csv"), emails)


def _gap_orgs(pdir) -> list[dict]:
    have = {c["org_id"] for c in ws.read_csv(pdir / "contacts.csv")
            if c.get("kind") == "email" and c.get("grade") in ("A", "B") and c.get("review") != "yes"}
    return [o for o in ws.read_csv(pdir / "orgs.csv") if str(o.get("id") or o.get("org_id") or "") not in have]


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


def _leads(pdir, org: dict, rnd: int, terms: list[str], dir_terms: list[str], engines: list[str],
           per_org: int = 8) -> list[str]:
    """Pages worth reading for one organisation: its own pages and PDFs, and other sites
    whose result mentions it (directories, federation and council pages)."""
    name = str(org.get("name") or "").strip()
    site = mo_pages.site_of(str(org.get("website") or ""))
    seen = set(ws._pages(pdir))
    out: list[str] = []
    for query, maps in _queries(org, rnd, terms, dir_terms):
        try:
            res = mo_providers.search(query, engines if not maps else None, n=8, maps=maps, project_dir=pdir)
        except Exception:  # noqa: BLE001
            continue
        for hit in res["results"]:
            url = hit.get("url") or ""
            if not url.startswith("http") or url in out or url in seen:
                continue
            hay = f"{hit.get('title', '')} {hit.get('snippet', '')}".lower()
            own = site and mo_pages.site_of(url) == site
            mentions = name and all(w in hay for w in name.lower().split()[:3])
            if own or mentions or (maps and not site):
                out.append(url)
    return out[:per_org]


def _button_pages(pdir, org_ids: set[str]) -> list[tuple[str, str]]:
    pages = []
    for r in ws.read_csv(pdir / "crawl.csv"):
        if r.get("org_id") in org_ids:
            for url in re.findall(r"email buttons without address on (\S+): try browse", r.get("note", "")):
                pages.append((r["org_id"], url))
    return pages


def run_sync(pdir, rounds: int = 6, max_pages: int = 15, deep_pages: int = 10, goal_ab_per_100: float | None = None,
             agent: bool = False, workers: int = 6, job: dict | None = None) -> dict:
    state = ws._run_state(pdir) or {}
    state.update(started=state.get("started") or ws._now(), done=False, stopped_because="")
    history = state.setdefault("history", [])
    b = ws.brief(pdir)
    focus = b.get("focus") if isinstance(b.get("focus"), dict) else {}
    terms = focus.get("search_terms") or DEFAULT_TERMS
    dir_terms = focus.get("directory_terms") or DEFAULT_DIRECTORY_TERMS
    dry = 0
    engines = [e for e in ("serper", "deepseek", "tinyfish") if mo_config.key(e)] or None

    def stage(name, **extra):
        state.update(stage=name, **extra)
        if job is not None:
            job["stage"] = name
        _save_state(pdir, state)

    orgs = ws.read_csv(pdir / "orgs.csv")
    if not orgs:
        raise ValueError("orgs.csv is empty: build the organisation pool first")
    stage("crawl", round=0)
    ws.crawl_sync(pdir, orgs, max_pages, None, workers, job, label="site")
    stage("grade", round=0)
    mo_grade.auto(pdir)
    m = measure(pdir)
    history.append({"round": 0, **m, "time": ws._now()})
    for rnd in range(1, rounds + 1):
        if goal_ab_per_100 and m["ab_routes_per_100_orgs"] >= goal_ab_per_100:
            state["stopped_because"] = f"goal reached ({m['ab_routes_per_100_orgs']} >= {goal_ab_per_100})"
            break
        gaps = _gap_orgs(pdir)
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
            for oid, url in _button_pages(pdir, gap_ids)[:50]:
                try:
                    ws.browse(pdir, url, org_id=oid, org=names.get(oid, ""))
                except Exception:  # noqa: BLE001
                    continue
            deadline = time.time() + 900
            while time.time() < deadline and any(
                    r["status"] in ("RUNNING", "PENDING") for r in ws.refresh_agent_runs(pdir)):
                time.sleep(20)
        stage("grade", round=rnd)
        mo_grade.auto(pdir)
        before = m["ab_orgs"]
        m = measure(pdir)
        history.append({"round": rnd, **m, "new_ab_orgs": m["ab_orgs"] - before, "time": ws._now()})
        dry = dry + 1 if m["ab_orgs"] == before else 0
        if dry >= 2:
            state["stopped_because"] = f"rounds {rnd - 1} and {rnd} found no new A/B organisation"
            break
    else:
        state["stopped_because"] = state.get("stopped_because") or f"{rounds} rounds done"
    stage("export")
    out = mo_export.export(pdir)
    state.update(done=True, stage="done", export=out, result=m)
    _save_state(pdir, state)
    return state


def run(pdir, **kw) -> dict:
    job = ws.new_job(pdir, "run")

    def work():
        try:
            run_sync(pdir, job=job, **kw)
        except Exception as exc:  # noqa: BLE001
            st = ws._run_state(pdir) or {}
            st.update(stage="failed", error=mo_config.scrub(f"{type(exc).__name__}: {exc}")[:300])
            _save_state(pdir, st)
        finally:
            job["running"] = False
    threading.Thread(target=work, daemon=True, name=job["id"]).start()
    return {"job": job["id"], "next": "project(name) shows run.stage, round and results; the run continues "
                                      "on its own and exports at the end",
            "headless": f"for very long runs outside this session: {_headless(pdir)}"}


def _headless(pdir) -> str:
    import sys
    from pathlib import Path
    server = Path(__file__).resolve().parent / "server.py"
    return f'nohup "{sys.executable}" "{server}" run "{pdir.name}" > "{pdir}/run.log" 2>&1 &'

