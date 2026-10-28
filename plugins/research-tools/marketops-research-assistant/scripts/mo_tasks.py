"""A shared work queue so several agents can work one project at the same time.

The coordinator plans packages; each worker agent claims one, does it with the normal tools
(search, fetch, browse, grade(set=...), pool) and hands it back, until the queue is empty.
A claim older than CLAIM_MINUTES is offered again, so a worker that dies never blocks the job.

    dig     organisations the tools read without finding a confirmed A/B: find their
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
             "their sites at once (no digging); orgs(project, ids=[...], wait=50) returns when they are through; "
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


def taken_orgs(pdir) -> set[str]:
    """Organisations an agent owns or has worked: in an open or claimed research package, with a
    verdict, or already handed out twice (a sweep and a dig) - the lanes never hand those out
    again. One swept without a verdict goes on to the dig lane once (075: such organisations kept
    a source "on its pilot" for good, and a worker waited for a pilot that could not end)."""
    done: dict[str, int] = {}
    taken: set[str] = set()
    for r in ws.read_csv(_path(pdir)):
        if r.get("kind") not in ("sweep", "dig"):
            continue
        for i in r.get("items", "").split("|"):
            oid = i.split("::", 1)[0]
            if not oid:
                continue
            if r.get("status") != "done" or r.get("kind") == "dig":
                taken.add(oid)
            else:
                done[oid] = done.get(oid, 0) + 1
    taken |= {o for o, n in done.items() if n >= 2}
    return taken | {r["org_id"] for r in ws.read_csv(pdir / "verdicts.csv")}


# ------------------------------------------------------------------ sources take turns
# Bruce's rule, in the lanes agents claim from: a source is tried on PILOT organisations before
# it gets more; at SCALE (10%) with A/B it gets all it has, under STOP (5%) its other
# organisations wait. In 072 the sweep lane took organisations in pool order: the first list
# pooled got every researcher for forty minutes and 26 other sources were never read.
# Agents decide over it: sources(set=[{source, status: "scale"}]) gives a waiting source all
# its organisations, status "dropped" none; a researcher may still claim any package.
PILOT, SCALE, STOP = 30, 0.10, 0.05
# a source is never paused on fewer: 0 of 5 says nothing about a 5% bar (075: a list paused at 0/5
# gave 9 A/B from 62 once scaled) - a small run's pilot is extended to this before the pause
PAUSE_MIN = 10


def source_of(org: dict) -> str:
    return (str(org.get("sources") or org.get("source") or "").split("|")[0].strip()) or "-"


def pilot(pdir) -> int:
    """The pilot size: brief.pilot for a small run (a smoke test of 20 organisations pilots on 5),
    else Bruce's 30."""
    try:
        return max(1, int(ws.brief(pdir).get("pilot") or PILOT))
    except (TypeError, ValueError):
        return PILOT


def source_yield(pdir) -> dict[str, dict]:
    """Per source (an organisation's first source): organisations in scope, how many agents
    worked (verdict or confirmed A/B), how many have A/B, and its state - pilot, scaling,
    waiting (pilot done under STOP), scale (an agent's call) or dropped."""
    import mo_flow
    states = {r["source"].casefold(): r.get("status", "") for r in mo_flow.source_pool(pdir)}
    judged = {r["org_id"] for r in ws.read_csv(pdir / "verdicts.csv")}
    ab = {c["org_id"] for c in ws.read_contacts(pdir) if c.get("grade") in ("A", "B") and c.get("review") != "yes"}
    out: dict[str, dict] = {}
    size = pilot(pdir)
    for o in ws.read_csv(pdir / "orgs.csv"):
        if o.get("scope") == "out" or not ws.counted(o):
            continue
        src, oid = source_of(o), str(o.get("id") or "")
        y = out.setdefault(src, {"orgs": 0, "worked": 0, "with_ab": 0})
        y["orgs"] += 1
        y["worked"] += oid in judged or oid in ab
        y["with_ab"] += oid in ab
    for src, y in out.items():
        st = states.get(src.casefold()) or states.get(src.rsplit("/", 1)[-1].casefold(), "")
        rate = y["with_ab"] / y["worked"] if y["worked"] else 0.0
        y["ab_rate"] = round(rate, 3)
        # dropped stops new handouts; a source that already gave organisations is "stopped", and what
        # it gave still counts (077: the richest source read "dropped" beside 28 organisations with A/B)
        y["state"] = (("stopped" if y["orgs"] else "dropped") if st == "dropped" else "scale" if st == "scale"
                      else "pilot" if y["worked"] < min(size, y["orgs"])
                      else "scaling" if rate >= STOP
                      else "pilot" if y["worked"] < min(PAUSE_MIN, y["orgs"])   # too few to judge yet
                      else "waiting")
    return out


def _mix(pdir, items: list[str], rows: list[dict]) -> list[str]:
    """Order a lane's organisations so every source takes turns, each within what its state
    allows (a pilot its first PILOT, a waiting or dropped source nothing)."""
    orgs = {str(o.get("id") or ""): o for o in ws.read_csv(pdir / "orgs.csv")}
    yields = source_yield(pdir)
    inflight: dict[str, int] = {}
    for r in rows:
        if r["kind"] in ("sweep", "dig") and r["status"] != "done":
            for i in r["items"].split("|"):
                if i in orgs:
                    inflight[source_of(orgs[i])] = inflight.get(source_of(orgs[i]), 0) + 1
    queues: dict[str, list[str]] = {}
    items = [i for i in items if ws.counted(orgs.get(i, {}))]   # its chain / the row it duplicates is researched instead
    for i in items:
        queues.setdefault(source_of(orgs.get(i, {})), []).append(i)
    room, size = {}, pilot(pdir)
    cap = int(ws.brief(pdir).get("max_per_source") or 0)   # a run's own limit per source, if it set one
    for src in queues:
        y = yields.get(src, {"state": "pilot", "worked": 0})
        room[src] = (0 if y["state"] in ("dropped", "stopped", "waiting") else
                     max(0, max(size, PAUSE_MIN if y["worked"] >= size else 0) - y["worked"] - inflight.get(src, 0))
                     if y["state"] == "pilot" else len(queues[src]))
        if cap:
            room[src] = min(room[src], max(0, cap - y["worked"] - inflight.get(src, 0)))
    out = []
    while any(queues[s] and room[s] > 0 for s in queues):
        for s in queues:
            if queues[s] and room[s] > 0:
                out.append(queues[s].pop(0))
                room[s] -= 1
    return out


def possible_duplicates(pdir) -> list[dict]:
    """One organisation pooled twice (two lists, two spellings - 073: six clubs, each counted
    twice in the strict ledger): the same name words, the same registry number, or the same
    personal or role address (not a generic inbox) found for both. The lead merges them with
    orgs(merge=[{keep, drop}]); they are not a chain."""
    import mo_pool
    orgs = [o for o in ws.read_csv(pdir / "orgs.csv") if ws.counted(o) and o.get("scope") != "out"]
    names = {str(o.get("id") or ""): str(o.get("name") or "") for o in orgs}
    groups: dict[str, set] = {}

    def link(key: str, oid: str) -> None:
        groups.setdefault(key, set()).add(oid)
    for o in orgs:
        oid = str(o.get("id") or "")
        rid = mo_pool.registry_id(o)
        if rid:
            link("id:" + rid, oid)
    import mo_pages
    site = {str(o.get("id") or ""): mo_pages.site_of(str(o.get("website") or "")) for o in orgs}
    shared = ws.shared_sites(pdir)
    for a, b in _similar_names(orgs):
        # two websites of their own, on two domains: two organisations whatever their names say
        # (075: Paris Vo Thuat Club and AS Vo Thuat) - a registry number or one address still links them
        if site[a] and site[b] and site[a] != site[b] and not ({site[a], site[b]} & shared):
            continue
        link("name:" + a + "|" + b, a)
        link("name:" + a + "|" + b, b)
    for c in ws.read_contacts(pdir):
        if c.get("kind", "email") == "email" and c.get("mailbox") != "generic" and c.get("org_id") in names \
                and c.get("grade") in ("A", "B", "C") and c.get("graded_by") != "held":
            link("email:" + str(c.get("value") or "").lower(), c["org_id"])
    # pairs that overlap are one organisation written three or four ways: one group each
    parent: dict[str, str] = {}

    def root(x: str) -> str:
        while parent.get(x, x) != x:
            x = parent[x]
        return x
    whys: dict[str, set] = {}
    for key, ids in groups.items():
        if len(ids) < 2 or len(ids) > 4:   # one address on five clubs is a shared inbox
            continue
        ids = sorted(ids)
        for i in ids[1:]:
            parent[root(i)] = root(ids[0])
        whys.setdefault(key, set()).update(ids)
    merged: dict[str, set] = {}
    for key, ids in whys.items():
        merged.setdefault(root(next(iter(ids))), set()).update(ids)
    out = []
    for r, ids in merged.items():
        why = sorted({{"name": "one name written two ways", "id": "the same registry number",
                       "email": "the same address"}[k.split(":", 1)[0]] for k, v in whys.items() if v & ids})
        out.append({"ids": sorted(ids), "names": [names[i][:40] for i in sorted(ids)], "why": "; ".join(why)})
    # all of them at once: a list cut at 40 looked like new pairs appearing after each merge (075: 51)
    return sorted(out, key=lambda d: d["names"])


_ROMAN = {"i": "1", "ii": "2", "iii": "3", "iv": "4", "v": "5", "vi": "6", "vii": "7", "viii": "8", "ix": "9",
          "x": "10", "xi": "11", "xii": "12", "xiii": "13", "xiv": "14", "xv": "15", "xvi": "16", "xvii": "17",
          "xviii": "18", "xix": "19", "xx": "20"}


def name_forms(name: str) -> list[frozenset]:
    """The ways one organisation's name is written, as word sets: its main name (without the
    bracketed or trailing acronym), and each bracketed alias. Numbers are numbers however they are
    written (XI, 11e, 11ème -> 11); filler words and single letters go."""
    import mo_pool
    raw = str(name or "")
    aliases = re.findall(r"\(([^)]*)\)", raw)
    main = re.sub(r"\([^)]*\)", " ", raw)
    main = re.split(r"\s[-–]\s*$|\s[-–]\s(?=[A-Z .]{1,20}$)", main)[0]

    def words(text: str) -> frozenset:
        out = []
        for w in mo_pool.norm(text).split():
            w = re.sub(r"^(\d+)(?:e|eme|er|ere|th|st|nd|rd)$", r"\1", w)
            w = re.sub(r"^([ivx]+)e$", lambda m: _ROMAN.get(m.group(1), m.group(0)), w)
            w = _ROMAN.get(w, w) if len(w) > 1 or w in ("v", "x") else w
            if w in mo_pool.NAME_FILLER or (len(w) < 2 and not w.isdigit()):
                continue
            out.append(w)
        return frozenset(out)
    forms = [words(main)] + [words(a) for a in aliases]
    acronym = "".join(w[0] for w in mo_pool.norm(main).split() if w not in ("de", "du", "des", "la", "le", "les", "et"))
    if len(acronym) >= 3:   # matches another's written acronym only, never another initials
        forms.append(frozenset(["#" + acronym]))
    return [f for f in dict.fromkeys(forms) if f]


def _near(a: str, b: str) -> bool:
    """One letter apart, for words long enough to carry a typo (Aicart / Aicard)."""
    if a == b:
        return True
    if min(len(a), len(b)) < 5 or abs(len(a) - len(b)) > 1:
        return False
    if len(a) == len(b):
        return sum(x != y for x, y in zip(a, b)) == 1
    s, t = (a, b) if len(a) < len(b) else (b, a)
    return any(t[:i] + t[i + 1:] == s for i in range(len(t)))


def _similar_names(orgs: list[dict]) -> list[tuple[str, str]]:
    """Pairs whose names are one organisation written two ways (074: fifteen merged by hand -
    acronyms in brackets, XI and 11, a letter off, one name inside the other). Compared only
    within a word they share, so a large pool stays fast; towns must not disagree."""
    import mo_pool
    forms = {str(o.get("id") or ""): name_forms(o.get("name")) for o in orgs}
    town = {str(o.get("id") or ""): (mo_pool.place(o).split() or [""])[0] for o in orgs}
    count: dict[str, int] = {}
    for fs in forms.values():
        for w in set().union(*fs) if fs else ():
            count[w.lstrip("#")] = count.get(w.lstrip("#"), 0) + 1
    by_word: dict[str, list[str]] = {}
    for oid, fs in forms.items():
        for w in {x.lstrip("#") for x in set().union(*fs)} if fs else ():
            if count[w] <= 6:   # common words ("paris", "football") do not make pairs
                by_word.setdefault(w, []).append(oid)
    pairs: set[tuple[str, str]] = set()
    for ids in by_word.values():
        for i, a in enumerate(ids):
            for b in ids[i + 1:]:
                if a != b and (not town[a] or not town[b] or town[a] == town[b]) and _same_name(forms[a], forms[b], count):
                    pairs.add(tuple(sorted((a, b))))
    return sorted(pairs)


def _same_name(fa: list[frozenset], fb: list[frozenset], count: dict) -> bool:
    for x in fa:
        for y in fb:
            ax, ay = any(w.startswith("#") for w in x), any(w.startswith("#") for w in y)
            if ax or ay:   # initials: against the other's written acronym, alone in its brackets
                if ax != ay and {w.lstrip("#") for w in (x if ax else y)} == (y if ax else x):
                    return True
                continue
            if x == y and (len(x) >= 2 or all(len(w) >= 4 for w in x)):
                return True
            small, big = (x, y) if len(x) <= len(y) else (y, x)
            # one name inside the other: the smaller carries a word only these two share
            if small < big and (len(small) >= 2 or len(big) <= 4) and any(count.get(w, 0) <= 2 and len(w) >= 4
                                                                        for w in small):
                return True
            if len(x) == len(y) >= 2:   # the same words, one letter off in one of them
                left = sorted(x - y)
                right = sorted(y - x)
                if len(left) == 1 and len(right) == 1 and _near(left[0], right[0]):
                    return True
    return False


def possible_chains(pdir) -> list[dict]:
    """Organisations that look like stores of one chain: two or more in scope on one website
    domain, with a name word in common (duplicates of one organisation are not a chain). A hint for the lead - the chain's head office is the
    organisation to research (orgs(add=...) it, then orgs(set=[{org_id, part_of}]) its stores)."""
    import mo_pages
    import mo_pool
    by: dict[str, list[dict]] = {}
    for o in ws.read_csv(pdir / "orgs.csv"):
        site = mo_pages.site_of(str(o.get("website") or ""))
        if site and o.get("scope") != "out" and ws.counted(o):
            by.setdefault(site, []).append(o)
    dup = {i for d in possible_duplicates(pdir) for i in d["ids"]}
    out = []
    for site, group in by.items():
        group = [o for o in group if str(o.get("id") or "") not in dup]
        if len(group) < 2:
            continue
        words = [set(mo_pool.name_words(str(o.get("name") or ""))) for o in group]
        if set.intersection(*words):
            out.append({"site": site, "organisations": len(group), "ids": [o["id"] for o in group][:12],
                        "names": [str(o.get("name") or "")[:40] for o in group][:5]})
    return sorted(out, key=lambda x: -x["organisations"])[:20]


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
            # an organisation an agent owns or already worked (a package of any state, a verdict)
            # is not handed out again
            taken = taken_orgs(pdir) | held_orgs(pdir)
            items = [i for i in items if i and i not in have and i in finished and i not in taken]
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
            # nor one an agent already took or judged (it may have read it with fetch, not run)
            taken = taken_orgs(pdir)
            items = [i for i in items if i and i not in started and i not in held and i not in taken]
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
        if kind in ("sweep", "dig", "site"):
            items = _mix(pdir, items, rows)   # every source takes turns, each piloted first
        new = []
        for k in range(0, len(items), max(1, size)):
            chunk = items[k:k + size]
            tid = f"{kind}-" + hashlib.sha256("|".join(chunk).encode()).hexdigest()[:8]
            new.append({"task_id": tid, "kind": kind, "title": f"{kind} {len(chunk)} items", "items": "|".join(chunk),
                        "status": "open"})
        if new:   # an empty plan writes nothing: the queue's age is how long nothing has moved
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


# lanes that refill themselves from the pools: a researcher claiming `dig` takes the organisations
# the tools have read and left without a confirmed A/B - the run waits for exactly this
AUTO_PLAN = {"sweep": 10, "source": 1, "site": 15, "dig": 10}
RESEARCH_LANES = {"sweep": ("dig",), "dig": ("sweep",)}


def claim(pdir, agent: str, kind: str = "") -> dict:
    """Give the caller the next open package (or one whose claim went stale). An empty lane that
    the pools can refill (new organisations, new sources) is planned on the spot, so a worker
    never waits for the lead to plan."""
    got = _claim(pdir, agent, kind)
    if got.get("task") is None and kind in AUTO_PLAN:
        plan(pdir, kind, AUTO_PLAN[kind])
        got = _claim(pdir, agent, kind)
    # a researcher's lanes are one job: an empty sweep hands out dig (organisations the tools have
    # read - a whole-project run takes them out of sweep), and an empty dig hands out sweep (077:
    # 14 empty sweep claims while 50 read organisations waited in a dig lane nobody had planned)
    for other in RESEARCH_LANES.get(kind, ()):
        if got.get("task") is not None:
            break
        got = _claim(pdir, agent, other)
        if got.get("task") is None:
            plan(pdir, other, AUTO_PLAN[other])
            got = _claim(pdir, agent, other)
    if got.get("task") is None:
        got.update(outlook(pdir, kind, agent))
    if got.get("task") and got["task"]["kind"] == "source":
        import mo_flow
        for i in got["task"]["items"].split("|"):
            if i:
                mo_flow.mark_source(pdir, i, "mining", agent)
    return got


QUIET_S = 600   # no pool, queue or verdict file changed for this long: nothing is coming


def _quiet_for(pdir) -> float:
    """Seconds since anything in the project's pools last changed."""
    import time
    files = ["tasks.csv", "orgs.csv", "verdicts.csv", "crawl.csv", "source_pool.csv", "sources.csv", "frontier.csv"]
    stamps = [(pdir / f).stat().st_mtime for f in files if (pdir / f).exists()]
    stamps += [b.stat().st_mtime for b in pdir.glob("batch.*.json")]
    cdir = pdir / "contacts"
    if cdir.exists():
        stamps.append(cdir.stat().st_mtime)
    return time.time() - max(stamps) if stamps else 0.0


def outlook(pdir, kind: str = "", agent: str = "") -> dict:
    """An empty lane, explained: is the work over, or does it wait - on a pilot, on a run's own
    limit per source, on pools still filling, on the lead? 073: three workers read `open: 0`,
    went home at 14:12 with 310 organisations unread, and the lead found out 13 minutes later.
    `lane_finished` is true only when nothing anywhere can refill this lane; otherwise
    `keep_waiting` says why, `other_lanes` where work is open now, and the lead hears of it
    (needs_you)."""
    why, waits = [], False
    ys = source_yield(pdir)
    waiting = {s: y["orgs"] - y["worked"] for s, y in ys.items() if y["state"] == "waiting"}
    piloting = [s for s, y in ys.items() if y["state"] == "pilot" and y["orgs"] > y["worked"]]
    cap = int(ws.brief(pdir).get("max_per_source") or 0)
    capped = {s: y["orgs"] - y["worked"] for s, y in ys.items()
              if cap and y["worked"] >= cap and y["orgs"] > y["worked"] and y["state"] not in ("dropped", "stopped", "waiting")}
    quiet = _quiet_for(pdir) >= QUIET_S
    if piloting and not quiet:
        waits = True
        why.append(f"{len(piloting)} source(s) are on their pilot: their next organisations come as the pilot's "
                   "verdicts come in")
    if waiting:
        # Bruce's rule decides by default - under 5% after the pilot, the rest of a source is paused;
        # nobody waits for a verdict (074: two workers idled seven minutes on one)
        why.append(f"{sum(waiting.values())} organisations are paused by the pilot rule (their sources gave A/B "
                   "under 5% on the pilot: " + ", ".join(list(waiting)[:5]) + "); the lead resumes one with "
                   "sources(set=[{source, status: 'scale'}]) if it thinks otherwise")
    if capped:
        why.append(f"{sum(capped.values())} organisations are unread because the brief's max_per_source={cap} is "
                   "reached for " + ", ".join(list(capped)[:5]) + ": the lead raises it "
                   "(project(name, brief={\"max_per_source\": 0})) or stops here")
    import mo_flow
    import mo_run
    spool = mo_flow.source_pool(pdir)
    found = [r["source"] for r in spool if r.get("status") in ("found", "mining")]
    rows = ws.read_csv(_path(pdir))
    if not spool and not ws.read_csv(pdir / "orgs.csv"):
        # a project just started: nothing found yet is not nothing to do (074: a researcher was
        # told lane_finished at minute 3, before the finders had registered a list)
        waits = True
        why.append("no source is registered and no organisation pooled yet: the finders are starting")
    if found and kind in ("sweep", "site", "dig", "source", "") and not quiet:
        waits = True
        why.append(f"{len(found)} source(s) are still being found or mined: their organisations come next")
    upstream = {"sweep": ("source", "find", "site"), "site": ("source", "find"), "dig": ("source", "find", "site", "sweep"),
                "source": ("find",)}.get(kind, ())
    stale = (_now() - timedelta(minutes=CLAIM_MINUTES)).isoformat(timespec="seconds")
    busy = {r["kind"] for r in rows if r["status"] == "claimed" and r["kind"] in upstream and r.get("claimed", "") >= stale}
    if busy and not quiet:
        waits = True
        why.append("agents still hold " + ", ".join(sorted(busy)) + " packages: what they bring comes here")
    running = [b for b in mo_run.batch_states(pdir) if not b.get("done")]
    if quiet and (piloting or found or busy):
        why.append(f"nothing has moved in the pools for {QUIET_S // 60} minutes: what still looks unfinished "
                   "(a pilot, a source marked mining, a claimed package) is not coming - the lead checks it")
    if kind == "dig" and (running or any(r["kind"] == "sweep" and r["status"] != "done" for r in rows)):
        waits = True
        why.append("reads and research packages are still going: what they leave without A/B comes to dig")
    others = {}
    for r in rows:
        if r["status"] == "open" and r["kind"] != kind:
            others[r["kind"]] = others.get(r["kind"], 0) + 1
    out: dict = {}
    if others:
        out["other_lanes"] = others
    if why:
        out["why_empty"] = "; ".join(why)
    unread = sum(1 for o in ws.read_csv(pdir / "orgs.csv") if ws.counted(o) and o.get("scope") != "out") \
        - len(taken_orgs(pdir))
    if waits:
        out["keep_waiting"] = True
        out["next"] = "the lane refills: claim again with wait=50 (or take one of other_lanes) - do not stop"
    elif others:
        out["next"] = "this lane is empty for now: take one of other_lanes, then come back"
    else:
        # paused sources and the brief's own cap are decisions already taken: the lane is done unless
        # the lead changes them (needs_you says so) - workers stop instead of waiting on a verdict
        out["lane_finished"] = True
        out["next"] = ("nothing can refill it: report and stop" if not (waiting or capped) else
                       "done under the current decisions (see why_empty): report and stop - the lead can still "
                       "resume a paused source or raise the cap, and the lane refills")
    if kind and not waits and (waiting or capped or unread > 0) and why:
        import mo_journal
        mo_journal.auto(pdir, f"lane-empty:{kind}", f"lane {kind} empty for {agent or 'a worker'} while work is left: "
                        + "; ".join(why), "tasks", every_s=300)
    return out


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
