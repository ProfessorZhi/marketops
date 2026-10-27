"""A shared work queue so several agents can work one project at the same time.

The coordinator plans packages; each worker agent claims one, does it with the normal tools
(search, fetch, browse, grade(set=...), pool) and hands it back, until the queue is empty.
A claim older than CLAIM_MINUTES is offered again, so a worker that dies never blocks the job.

    dig     organisations still without a confirmed A/B after the run's rounds: find their
            people (staff/board pages, PDFs, directories, other spellings, maps) and fetch
            what you find with org_id
    review  A/B rows marked review: open the quote and confirm or correct with grade(set=...)
    browse  pages whose email buttons hide the address: browse them
    sweep   a research package: organisations nobody has worked yet - the researcher reads them
            all at once with run(orgs=..., rounds=0), judges, digs and writes a verdict for each
    source  one list from the source pool, found and not yet mined: harvest it into pages and
            rows, check them, pool them - several miners work different sources at once while
            the finders keep adding new ones
    site    organisations with no website yet: find each one's own site or page
    find    a lane for a source finder (a segment, a region, a kind of list) - added by the lead
    custom  anything the coordinator adds: a source family, a district, a federation list
"""
from __future__ import annotations

import hashlib
import re
from datetime import datetime, timedelta, timezone

import mo_workspace as ws

TASK_COLS = ["task_id", "kind", "title", "items", "status", "agent", "claimed", "finished", "note"]
CLAIM_MINUTES = 40
GUIDE = {
    "dig": "For each organisation: search its board/staff/committee/contact pages, PDFs and directory "
           "entries (other spellings, the city, maps for a missing website); fetch what you find with its "
           "org_id; grade(project) then set person/role on the A/B rows. Note what you tried.",
    "review": "For each row: read the quote (fetch the page if unsure); confirm or correct with "
              "grade(set=[{value, org_id, grade, person, role}]) - setting clears the review mark.",
    "browse": "browse(project, url, org_id=...) each page, then project(name) until the runs finish.",
    "evidence": "For each row: find more proof the person and role belong to this organisation - search the "
                "address and the name, fetch the pages that print them with org_id (each page raises the "
                "evidence score); fix person/role with grade(set=...) if the pages say otherwise.",
    "sweep": "These organisations are yours from start to finish: run(project, orgs=[their ids], rounds=0) reads "
             "their sites at once (no digging); read orgs(project, ids=[...]) until each shows 'pipeline done'; "
             "then for each: judge what was found, dig where a route is missing, verdict. See references/roles/research.md.",
    "source": "Mine this list (references/roles/pool.md): harvest(project, source, start=url, follow=..., next=...) "
              "until nothing is queued, check 20 rows, then pool(project, file, into=..., map=..., source=...). "
              "A new list seen on the way: sources(project, add=[...]). Not worth it: sources(project, set=[{source, "
              "status: 'dropped', note}]).",
    "site": "For each organisation find its own website (references/roles/websites.md) and record it with "
            "orgs(project, set=[{org_id, website}]); then run(project, orgs=[...], rounds=0, again=True).",
    "find": "Find new lists for this lane (references/roles/sources.md) and register each with sources(project, "
            "add=[{source, url, kind, row_is, expect}]); miners take them from there. Do not mine them yourself.",
    "custom": "Do what the title says with the tools; add organisations to orgs.csv or rows via pool.",
}


def _path(pdir):
    return pdir / "tasks.csv"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _open_items(rows: list[dict], kind: str) -> set[str]:
    return {i for r in rows if r["kind"] == kind and r["status"] != "done" for i in r["items"].split("|") if i}


def held_orgs(pdir) -> set[str]:
    """Organisations an agent is working on right now (in a claimed package whose claim is not
    stale): the pipeline leaves them alone until the package comes back, so two writers never
    work one organisation at once."""
    stale = (_now() - timedelta(minutes=CLAIM_MINUTES)).isoformat(timespec="seconds")
    return {i.split("::", 1)[0] for r in ws.read_csv(_path(pdir))
            if r.get("status") == "claimed" and r.get("claimed", "") >= stale
            for i in r.get("items", "").split("|") if i}


def plan(pdir, kind: str, size: int = 15) -> dict:
    """Cut the current work of one kind into packages of `size` and add them to tasks.csv."""
    with ws._lock(_path(pdir)):
        rows = ws.read_csv(_path(pdir))
        busy = _open_items(rows, kind)
        if kind == "dig":
            have = {c["org_id"] for c in ws.read_contacts(pdir)
                    if c.get("grade") in ("A", "B") and c.get("review") != "yes"}
            # only organisations the pipeline has finished: one still in its flow is not a gap yet,
            # and handing it out would put two writers on it
            finished = {r["org_id"] for r in ws.read_csv(pdir / "crawl.csv") if r.get("pass") == "final"}
            items = [str(o.get("id") or o.get("org_id") or "") for o in ws.read_csv(pdir / "orgs.csv")]
            items = [i for i in items if i and i not in have and i in finished]
        elif kind == "review":
            items = [f"{c['org_id']}::{c['value']}" for c in ws.read_contacts(pdir)
                     if c.get("review") == "yes" and not c.get("graded_by", "").startswith("agent")]
        elif kind == "evidence":
            items = [f"{c['org_id']}::{c['value']}" for c in ws.read_contacts(pdir)
                     if c.get("grade") in ("A", "B") and c.get("evidence") in ("weak", "medium")]
        elif kind == "sweep":
            # not started by the pipeline (no site pass yet) and not held by anyone
            started = {r["org_id"] for r in ws.read_csv(pdir / "crawl.csv")}
            held = held_orgs(pdir)
            items = [str(o.get("id") or o.get("org_id") or "") for o in ws.read_csv(pdir / "orgs.csv")
                     if o.get("scope") != "out"]
            items = [i for i in items if i and i not in started and i not in held]
        elif kind == "source":
            import mo_flow
            items = [r["source"] for r in mo_flow.source_pool(pdir) if r.get("status") == "found"]
            size = 1   # one source, one miner
        elif kind == "site":
            held = held_orgs(pdir)
            items = [str(o.get("id") or "") for o in ws.read_csv(pdir / "orgs.csv")
                     if not o.get("website") and o.get("scope") != "out"]
            items = [i for i in items if i and i not in held]
        elif kind == "browse":
            items = [f"{r['org_id']}::{u}" for r in ws.read_csv(pdir / "crawl.csv")
                     for u in re.findall(r"email buttons without address on (\S+): try browse", r.get("note", ""))]
        else:
            return {"error": "kind: sweep, source, site, dig, review, evidence or browse (find and custom tasks: action='add')"}
        items = [i for i in dict.fromkeys(items) if i not in busy]
        new = []
        for k in range(0, len(items), max(1, size)):
            chunk = items[k:k + size]
            tid = f"{kind}-" + hashlib.sha256("|".join(chunk).encode()).hexdigest()[:8]
            new.append({"task_id": tid, "kind": kind, "title": f"{kind} {len(chunk)} items", "items": "|".join(chunk),
                        "status": "open"})
        ws.write_csv(_path(pdir), TASK_COLS, rows + new)
    return {"planned": len(new), "items": len(items), "kind": kind, **summary(pdir)}


def add(pdir, title: str, note: str = "", kind: str = "custom") -> dict:
    with ws._lock(_path(pdir)):
        rows = ws.read_csv(_path(pdir))
        tid = f"{kind}-" + hashlib.sha256(f"{title}{_now()}".encode()).hexdigest()[:8]
        rows.append({"task_id": tid, "kind": kind, "title": title, "items": "", "status": "open", "note": note})
        ws.write_csv(_path(pdir), TASK_COLS, rows)
    return {"added": tid}


def _payload(pdir, task: dict) -> list[dict]:
    items = [i for i in task["items"].split("|") if i]
    if task["kind"] == "source":
        import mo_flow
        return [r for r in mo_flow.source_pool(pdir) if r["source"] in items]
    if task["kind"] in ("dig", "sweep", "site"):
        orgs = {str(o.get("id") or o.get("org_id") or ""): o for o in ws.read_csv(pdir / "orgs.csv")}
        crawl = {r["org_id"]: r for r in ws.read_csv(pdir / "crawl.csv")}
        out = []
        for i in items:
            o = orgs.get(i, {"id": i})
            c = crawl.get(i, {})
            out.append({"org_id": i, "name": o.get("name"), "website": o.get("website"), "city": o.get("city"),
                        "crawl": c.get("status"), "pages_read": c.get("pages"), "crawl_note": c.get("note")})
        return out
    if task["kind"] in ("review", "evidence"):
        want = {tuple(i.split("::", 1)) for i in items}
        return [{k: c.get(k) for k in ("org_id", "org", "value", "grade", "review_reason", "evidence",
                                       "evidence_why", "person", "role", "page_url", "quote")}
                for c in ws.read_contacts(pdir) if (c.get("org_id"), c.get("value")) in want]
    if task["kind"] == "browse":
        return [{"org_id": i.split("::", 1)[0], "url": i.split("::", 1)[1]} for i in items]
    return []


AUTO_PLAN = {"sweep": 10, "source": 1, "site": 15}   # lanes that refill themselves from the pools


def claim(pdir, agent: str, kind: str = "") -> dict:
    """Give the caller the next open package (or one whose claim went stale). An empty lane that
    the pools can refill (new organisations, new sources) is planned on the spot, so a worker
    never waits for the lead to plan."""
    got = _claim(pdir, agent, kind)
    if got.get("task") is None and kind in AUTO_PLAN:
        plan(pdir, kind, AUTO_PLAN[kind])
        got = _claim(pdir, agent, kind)
    if got.get("task") and got["task"]["kind"] == "source":
        import mo_flow
        for i in got["task"]["items"].split("|"):
            if i:
                mo_flow.mark_source(pdir, i, "mining", agent)
    return got


def _claim(pdir, agent: str, kind: str = "") -> dict:
    stale = (_now() - timedelta(minutes=CLAIM_MINUTES)).isoformat(timespec="seconds")
    with ws._lock(_path(pdir)):
        rows = ws.read_csv(_path(pdir))
        for r in rows:
            free = r["status"] == "open" or (r["status"] == "claimed" and r.get("claimed", "") < stale)
            if free and (not kind or r["kind"] == kind):
                r.update(status="claimed", agent=agent, claimed=_now().isoformat(timespec="seconds"))
                ws.write_csv(_path(pdir), TASK_COLS, rows)
                return {"task": r, "how": GUIDE.get(r["kind"], GUIDE["custom"]), "items": _payload(pdir, r),
                        "when_done": "tasks(project, action='done', task_id=..., note='what you found/tried')"}
    return {"task": None, "note": "queue empty", **summary(pdir)}


def done(pdir, task_id: str, note: str = "", release: bool = False) -> dict:
    with ws._lock(_path(pdir)):
        rows = ws.read_csv(_path(pdir))
        for r in rows:
            if r["task_id"] == task_id:
                r.update(status="open" if release else "done", note=note[:500],
                         finished="" if release else _now().isoformat(timespec="seconds"))
        ws.write_csv(_path(pdir), TASK_COLS, rows)
    return summary(pdir)


def summary(pdir) -> dict:
    out: dict = {}
    for r in ws.read_csv(_path(pdir)):
        k = out.setdefault(r["kind"], {"open": 0, "claimed": 0, "done": 0})
        k[r["status"]] = k.get(r["status"], 0) + 1
    return {"queue": out}
