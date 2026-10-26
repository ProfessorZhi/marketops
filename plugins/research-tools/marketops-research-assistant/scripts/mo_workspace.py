"""A project is a plain folder the agent and the user can open.

    <project>/brief.json      what is wanted (roles per grade, region, exclusions)
    <project>/venues.csv      the venue pool       (agent-owned: write it any way you like)
    <project>/orgs.csv        the organisation pool (agent-owned; crawl reads id,name,website)
    <project>/contacts.csv    contact candidates with quote, page and grade (tools add rows;
                              the agent may edit person/role/grade/note by hand)
    <project>/crawl.csv       one line per crawled organisation (status, pages, contacts)
    <project>/pages/          every page as read, one file per reader
    <project>/pages.csv       index of saved pages
    <project>/agent.csv       TinyFish Agent runs
    <project>/costs.jsonl     every provider call
"""
from __future__ import annotations

import csv
import hashlib
import json
import re
import threading
import time
import urllib.parse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

import mo_budget
import mo_config
import mo_pages
import mo_providers

CONTACT_COLS = ["org_id", "org", "kind", "value", "person", "role", "grade", "grade_reason",
                "graded_by", "review", "review_reason", "mailbox", "same_site", "email_site", "page_site", "page_on_org_site",
                "page_url", "found_by", "quote", "quote_from", "note", "added"]
PHONE_COLS = ["org_id", "org", "value", "page_url", "quote", "found_by", "added"]
PAGE_COLS = ["url", "final_url", "reader", "file", "chars", "org_id", "time"]
CRAWL_COLS = ["org_id", "org", "website", "status", "pages", "emails", "readers_ok", "email_buttons",
              "pass", "note", "time"]
AGENT_COLS = ["run_id", "url", "goal", "org_id", "status", "file", "time"]
DATA_EXT = re.compile(r"\.(csv|json|geojson|xlsx?|zip|xml|txt)(\?|$)|/exports?/|/api/|format=(csv|json)", re.I)
PDF = re.compile(r"\.pdf(\?|$)", re.I)
_page_index: dict[str, dict[str, list[dict]]] = {}

_jobs: dict[str, dict] = {}


class _PathLock:
    """One writer at a time for a project file: threads in this process, and other processes
    (the background run, other agents' servers) through an OS lock on <file>.lock."""

    def __init__(self, path: Path):
        self.thread = threading.Lock()
        self.lockfile = str(path) + ".lock"
        self.fh = None

    def __enter__(self):
        self.thread.acquire()
        try:
            import fcntl
            self.fh = open(self.lockfile, "a")
            fcntl.flock(self.fh, fcntl.LOCK_EX)
        except (ImportError, OSError):  # Windows: threads only
            self.fh = None
        return self

    def __exit__(self, *exc):
        if self.fh is not None:
            import fcntl
            fcntl.flock(self.fh, fcntl.LOCK_UN)
            self.fh.close()
            self.fh = None
        self.thread.release()


_locks: dict[str, _PathLock] = {}
_locks_guard = threading.Lock()


def _lock(path: Path) -> _PathLock:
    with _locks_guard:
        return _locks.setdefault(str(path), _PathLock(path))


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def project_dir(name: str) -> Path:
    slug = re.sub(r"[^\w.-]+", "-", name.strip()).strip("-.")[:80]
    if not slug:
        raise ValueError("project name is empty")
    path = mo_config.projects_root() / slug
    (path / "pages").mkdir(parents=True, exist_ok=True)
    return path


def read_csv(path: Path) -> list[dict]:
    try:
        with open(path, newline="", encoding="utf-8-sig") as fh:
            return list(csv.DictReader(fh))
    except OSError:
        return []


def write_csv(path: Path, cols: list[str], rows: list[dict]) -> None:
    extra = [k for r in rows for k in r if k not in cols]
    cols = cols + list(dict.fromkeys(extra))
    tmp = path.with_suffix(".tmp")
    with open(tmp, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k, "") for k in cols})
    tmp.replace(path)


def append_csv(path: Path, cols: list[str], row: dict) -> None:
    with _lock(path):
        new = not path.exists()
        with open(path, "a", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=cols, extrasaction="ignore")
            if new:
                w.writeheader()
            w.writerow({k: row.get(k, "") for k in cols})


def weights(pdir: Path, kind: str) -> dict:
    """Provider shares for 'search' or 'fetch': brief.providers.<kind> over the defaults."""
    own = (brief(pdir).get("providers") or {}).get(kind) or {}
    return {**mo_providers.DEFAULT_WEIGHTS[kind], **own}


def brief(pdir: Path) -> dict:
    try:
        return json.loads((pdir / "brief.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


# ------------------------------------------------------------------ pages
def _save(pdir: Path, url: str, reader: str, body: bytes, ext: str, final_url: str, org_id: str) -> str:
    name = hashlib.sha256(url.encode()).hexdigest()[:16] + f".{reader}.{ext}"
    (pdir / "pages" / name).write_bytes(body)
    row = {"url": url, "final_url": final_url, "reader": reader, "file": "pages/" + name,
           "chars": len(body), "org_id": org_id, "time": _now()}
    append_csv(pdir / "pages.csv", PAGE_COLS, row)
    _pages(pdir).setdefault(url, []).append(row)
    return "pages/" + name


def _pages(pdir: Path) -> dict[str, list[dict]]:
    key = str(pdir)
    if key not in _page_index:
        index: dict[str, list[dict]] = {}
        for r in read_csv(pdir / "pages.csv"):
            index.setdefault(r["url"], []).append(r)
        _page_index[key] = index
    return _page_index[key]


def _merge_phones(pdir: Path, phones: list[dict], org_id: str, org: str) -> None:
    path = pdir / "phones.csv"
    with _lock(path):
        rows = read_csv(path)
        have = {(r["org_id"], re.sub(r"\D", "", r["value"])) for r in rows}
        for c in phones:
            key = (org_id, re.sub(r"\D", "", c["value"]))
            if key not in have:
                have.add(key)
                rows.append({"org_id": org_id, "org": org, "value": c["value"], "page_url": c.get("page_url", ""),
                             "quote": c.get("quote", ""), "found_by": c.get("found_by", ""), "added": _now()})
        write_csv(path, PHONE_COLS, rows)


def merge_contacts(pdir: Path, found: list[dict], org_id: str = "", org: str = "") -> int:
    """Add candidates to contacts.csv (emails) and phones.csv; one row per value and org."""
    phones = [c for c in found if c.get("kind") == "phone"]
    if phones:
        _merge_phones(pdir, phones, org_id, org)
    found = [c for c in found if c.get("kind") != "phone"]
    path = pdir / "contacts.csv"
    with _lock(path):
        rows = read_csv(path)
        index = {(r.get("org_id", ""), r.get("value", "").lower()): r for r in rows}
        added = 0
        for c in found:
            key = (org_id, c["value"].lower())
            row = index.get(key)
            if row is None:
                row = {**{k: "" for k in CONTACT_COLS}, **{k: c.get(k, "") for k in CONTACT_COLS},
                       "org_id": org_id, "org": org, "found_by": c.get("found_by", ""), "added": _now()}
                if c.get("kind") == "email" and c.get("mailbox") == "generic":
                    row["grade"], row["graded_by"] = "D", "rule: generic mailbox"
                rows.append(row)
                index[key] = row
                added += 1
            else:
                by = set(filter(None, row.get("found_by", "").split("+"))) | set(filter(None, c.get("found_by", "").split("+")))
                row["found_by"] = "+".join(sorted(by))
                better = (c.get("quote_from") == "block") > (row.get("quote_from") == "block") or (
                    c.get("quote_from") == row.get("quote_from") and len(c.get("quote", "")) > len(row.get("quote", "")))
                if better and not row.get("graded_by", "").startswith("agent"):
                    for f in ("quote", "quote_from", "page_url", "page_site", "page_on_org_site"):
                        row[f] = c.get(f, row.get(f, ""))
        write_csv(path, CONTACT_COLS, rows)
    return added


def _download(pdir: Path, url: str, org_id: str) -> dict:
    report = {}
    try:
        got = mo_providers.download(url)
        ext = (re.search(r"\.(\w{2,5})(\?|$)", urllib.parse.urlsplit(got["final_url"]).path) or [None, "bin"])[1]
        for kind in ("json", "csv", "html"):
            if kind in got["content_type"]:
                ext = kind
        f = _save(pdir, url, "download", got["bytes"], ext, got["final_url"], org_id)
        mo_budget.record(pdir, "download", "fetch", True, url[:120])
        report["download"] = {"file": f, "bytes": len(got["bytes"]), "type": got["content_type"]}
    except Exception as exc:  # noqa: BLE001
        report["download"] = {"error": f"{type(exc).__name__}: {exc}"[:160]}
    return {"url": url, "readers": report, "contacts": [], "links": [], "text": ""}


def _parse_saved(pdir: Path, row: dict) -> dict:
    body = (pdir / row["file"]).read_text(encoding="utf-8", errors="replace")
    if row["file"].endswith(".html"):
        return mo_pages.from_html(body, row["final_url"] or row["url"])
    return mo_pages.from_markdown(body, row["final_url"] or row["url"])


def fetch(pdir: Path, url: str, readers=None, org_id: str = "", org: str = "",
          org_site: str = "", refresh: bool = False) -> dict:
    """Read a page with every chosen reader at once; save each version; collect contacts.

    A URL already read in this project is parsed from its saved copies (no new calls)
    unless refresh. PDFs go to the readers that can read them (TinyFish, DeepSeek).
    """
    url = mo_providers.public_url(url)
    if readers in (None, "auto", "all", []) and DATA_EXT.search(url):
        readers = ["download"]
    readers = readers if isinstance(readers, list) else (None if readers in (None, "auto", "all") else [readers])
    if readers == ["download"]:
        return _download(pdir, url, org_id)
    if readers is None and PDF.search(url):
        readers = [r for r in ("tinyfish", "deepseek") if mo_config.key(r)] or ["crawl4ai"]
    report, parsed_by = {}, {}
    saved = [] if refresh else [r for r in _pages(pdir).get(url, []) if r["reader"] != "download"]
    if saved:
        for row in saved:
            try:
                parsed_by[row["reader"]] = _parse_saved(pdir, row)
                report[row["reader"]] = {"file": row["file"], "cached": True}
            except OSError:
                continue
    def read_with(chosen):
        for reader, res in mo_providers.read(url, chosen, project_dir=pdir).items():
            if "error" in res:
                report[reader] = {"error": res["error"]}
                continue
            if res.get("html"):
                f = _save(pdir, url, reader, res["html"].encode("utf-8"), "html", res["final_url"], org_id)
                parsed = mo_pages.from_html(res["html"], res["final_url"])
            else:
                f = _save(pdir, url, reader, res["text"].encode("utf-8"), "md", res["final_url"], org_id)
                parsed = mo_pages.from_markdown(res["text"], res["final_url"])
            parsed_by[reader] = parsed
            report[reader] = {"file": f, "chars": sum(map(len, parsed["lines"])), "final_url": res["final_url"]}

    if not parsed_by:
        if readers is None:
            now, held = mo_providers.pick(url, mo_providers.available_readers(), weights(pdir, "fetch"),
                                          always=("crawl4ai",))
        else:
            now, held = readers, []
        read_with(now)
        # Quality floor: nothing read, or a contact-looking page with no address -> the rest too.
        emails = any(mo_pages.EMAIL.search(" ".join(p["lines"])) for p in parsed_by.values())
        contactish = any(h in url.lower() for h in ("kontakt", "contact", "impressum", "vorstand", "team",
                                                     "staff", "people", "board", "committee", "equipe"))
        if held and (not parsed_by or (contactish and not emails)):
            read_with(held)
    page_site = mo_pages.site_of(url)
    org_site = org_site or page_site
    found: dict[str, dict] = {}
    links: list = []
    buttons = 0
    for reader, parsed in parsed_by.items():
        links += parsed["links"]
        buttons = max(buttons, parsed.get("hidden_email_buttons", 0))
        for c in mo_pages.contacts(parsed["lines"], url, org_site, parsed.get("blocks"), parsed.get("mailtos")):
            c.update(page_site=page_site, page_on_org_site="yes" if page_site == org_site else "no")
            k = c["value"].lower()
            if k in found:
                found[k]["found_by"] = "+".join(sorted(set(found[k]["found_by"].split("+")) | {reader}))
                if (c["quote_from"] == "block") > (found[k]["quote_from"] == "block"):
                    found[k]["quote"], found[k]["quote_from"] = c["quote"], "block"
            else:
                found[k] = {**c, "found_by": reader}
    contacts = list(found.values())
    added = merge_contacts(pdir, contacts, org_id, org) if (org_id or org) else 0
    best = max((p["lines"] for p in parsed_by.values()), key=lambda ls: sum(map(len, ls)), default=[])
    emails_seen = sum(c["kind"] == "email" for c in contacts)
    return {"url": url, "readers": report, "contacts": contacts, "contacts_added": added,
            "links": mo_pages.contact_links(links, url, limit=25),
            "scored_links": mo_pages.contact_links(links, url, limit=25, scores=True),
            "links_total": len(links), "email_buttons_without_address": max(0, buttons - emails_seen),
            "text": "\n".join(best)}


# ------------------------------------------------------------------ crawl jobs
def _crawl_one(pdir: Path, t: dict, max_pages: int, readers, extra: list[str] | None = None,
               label: str = "site") -> dict:
    """Read one organisation's site: home, then the best-scoring same-site pages two levels
    deep (contact, imprint, team, board, staff, venue pages and PDFs), up to max_pages."""
    import heapq
    org_id, org, site = str(t.get("id") or t.get("org_id") or ""), str(t.get("name") or t.get("org") or ""), \
        str(t.get("website") or t.get("url") or "").strip()
    row = {"org_id": org_id, "org": org, "website": site, "time": _now(), "pass": label}
    if not site and not extra:
        return {**row, "status": "no_website"}
    if site and not site.startswith(("http://", "https://")):
        site = "https://" + site
    org_site = mo_pages.site_of(site) if site else ""
    heap: list = []
    for n, u in enumerate(([site] if site else []) + (extra or [])):
        heapq.heappush(heap, (-100 + n, 0, u))
    seen, pages, emails, ok_readers, notes, buttons = set(), 0, 0, set(), [], 0
    while heap and pages < max_pages:
        _neg, depth, url = heapq.heappop(heap)
        if url in seen:
            continue
        seen.add(url)
        try:
            got = fetch(pdir, url, readers, org_id, org, org_site)
        except Exception as exc:  # noqa: BLE001
            notes.append(f"{url[:60]}: {exc}"[:100])
            if pages == 0 and url.startswith("https://") and "://www." not in url:
                heapq.heappush(heap, (-100, 0, url.replace("https://", "https://www.", 1)))
            continue
        ok = [r for r, v in got["readers"].items() if "error" not in v]
        if not ok:
            if pages == 0 and url == site:
                notes.append("no reader could open the home page")
            continue
        pages += 1
        ok_readers |= set(ok)
        emails += sum(c["kind"] == "email" for c in got["contacts"])
        if got["email_buttons_without_address"] >= 3:
            buttons += got["email_buttons_without_address"]
            notes.append(f"{got['email_buttons_without_address']} email buttons without address on {url[:80]}: try browse")
        if depth < 2 and mo_pages.site_of(url) == (org_site or mo_pages.site_of(url)):
            for link, score in got["scored_links"]:
                if link not in seen:
                    heapq.heappush(heap, (-score + depth, depth + 1, link))
    status = "done" if ok_readers else "unreadable"
    return {**row, "status": status, "pages": pages, "emails": emails, "email_buttons": buttons,
            "readers_ok": "+".join(sorted(ok_readers)), "note": "; ".join(notes)[:400]}


def _ordered(targets: list[dict]) -> list[dict]:
    rank = {"high": 0, "1": 0, "normal": 1, "": 1, "2": 1, "low": 2, "3": 2}
    return sorted(targets, key=lambda t: rank.get(str(t.get("priority", "")).strip().lower(), 1))


def _todo(pdir: Path, targets: list[dict], label: str) -> list[dict]:
    done = {(r["org_id"], r.get("pass") or "site") for r in read_csv(pdir / "crawl.csv")
            if r.get("status") in ("done", "no_website")}
    return [t for t in _ordered(targets) if (str(t.get("id") or t.get("org_id") or ""), label) not in done]


def crawl_sync(pdir: Path, targets: list[dict], max_pages: int = 15, readers=None, workers: int = 6,
               job: dict | None = None, label: str = "site", extra_urls: dict | None = None) -> int:
    todo = _todo(pdir, targets, label)
    if job is not None:
        job.update(total=job.get("total", 0) + len(todo), stage=label)
        if callable(job.get("on_progress")):
            job["on_progress"]()

    def one(t):
        if stop_requested(pdir):
            return None
        oid = str(t.get("id") or t.get("org_id") or "")
        return _crawl_one(pdir, t, max_pages, readers, (extra_urls or {}).get(oid), label)
    with ThreadPoolExecutor(max_workers=workers) as pool:
        for result in pool.map(one, todo):
            if result is None:
                continue
            append_csv(pdir / "crawl.csv", CRAWL_COLS, result)
            if job is not None:
                job["finished"] = job.get("finished", 0) + 1
                if callable(job.get("on_progress")):
                    job["on_progress"]()
    return len(todo)


def stop_requested(pdir: Path) -> bool:
    return (pdir / "STOP").exists()


def crawl(pdir: Path, targets: list[dict], max_pages: int = 15, readers=None, workers: int = 6) -> dict:
    """Start crawling organisation sites in the background; returns at once."""
    job = new_job(pdir, "crawl")
    queued = len(_todo(pdir, targets, "site"))

    def work():
        try:
            crawl_sync(pdir, targets, max_pages, readers, workers, job)
        finally:
            job["running"] = False
    threading.Thread(target=work, daemon=True, name=job["id"]).start()
    return {"job": job["id"], "queued": queued, "skipped_already_done": len(targets) - queued}


def new_job(pdir: Path, kind: str) -> dict:
    job = {"id": f"{kind}-" + hashlib.sha256(f"{pdir}{time.time()}".encode()).hexdigest()[:8],
           "project": str(pdir), "total": 0, "finished": 0, "running": True, "started": _now()}
    _jobs[job["id"]] = job
    return job


# ------------------------------------------------------------------ TinyFish Agent
DEFAULT_GOAL = ("Return the visible text of this page verbatim, including every person's name, "
                "role or job title, email address and phone number. Open on-page tabs or 'show more' "
                "if needed. Do not log in, submit forms or accept anything.")


def browse(pdir: Path, url: str, goal: str = "", org_id: str = "", org: str = "") -> dict:
    url = mo_providers.public_url(url)
    for r in read_csv(pdir / "agent.csv"):
        if r["url"] == url and r["status"] not in ("FAILED", "CANCELLED", "error"):
            return {"run_id": r["run_id"], "status": r["status"], "note": "already submitted for this URL"}
    run_id = mo_providers.agent_submit(url, goal or DEFAULT_GOAL)
    append_csv(pdir / "agent.csv", AGENT_COLS, {"run_id": run_id, "url": url, "goal": goal or "default",
                                                "org_id": org_id, "status": "RUNNING", "time": _now()})
    _jobs["agent-" + run_id] = {"org": org}
    return {"run_id": run_id, "status": "RUNNING", "next": "check project status in a few minutes"}


def refresh_agent_runs(pdir: Path) -> list[dict]:
    rows = read_csv(pdir / "agent.csv")
    changed = False
    for r in rows:
        if r["status"] not in ("RUNNING", "PENDING"):
            continue
        try:
            st = mo_providers.agent_status(r["run_id"])
        except Exception:  # noqa: BLE001
            continue
        status = str(st.get("status") or "").upper() or r["status"]
        if status == "COMPLETED":
            result = st.get("result") or st.get("output") or st
            if isinstance(result, dict) and isinstance(result.get("result"), str):
                result = result["result"]
            text = result if isinstance(result, str) else json.dumps(result, ensure_ascii=False, indent=1)
            r["file"] = _save(pdir, r["url"], "tinyfish-agent", text.encode("utf-8"), "md", r["url"], r["org_id"])
            steps = st.get("num_of_steps") or len(st.get("steps") or []) or 20
            mo_budget.record(pdir, "tinyfish", "agent", True, r["url"][:120], usd=0.016 * float(steps))
            lines = [ln for ln in (" ".join(x.split()) for x in text.splitlines()) if ln]
            found = [{**c, "found_by": "tinyfish-agent"} for c in mo_pages.contacts(lines, r["url"])]
            org = (_jobs.get("agent-" + r["run_id"]) or {}).get("org", "")
            merge_contacts(pdir, found, r["org_id"], org)
        elif status in ("FAILED", "CANCELLED"):
            mo_budget.record(pdir, "tinyfish", "agent", False, r["url"][:120])
        r["status"] = status
        changed = True
    if changed:
        write_csv(pdir / "agent.csv", AGENT_COLS, rows)
    return rows


def status(pdir: Path) -> dict:
    contacts = read_csv(pdir / "contacts.csv")
    grades: dict[str, int] = {}
    orgs_with = {}
    for c in contacts:
        if c.get("kind") != "email":
            continue
        g = c.get("grade") or "ungraded"
        grades[g] = grades.get(g, 0) + 1
        orgs_with.setdefault(g, set()).add(c.get("org_id"))
    crawl = read_csv(pdir / "crawl.csv")
    agent = refresh_agent_runs(pdir) if (pdir / "agent.csv").exists() else []
    jobs = [j for j in _jobs.values() if j.get("project") == str(pdir)]
    return {
        "project": str(pdir), "brief": brief(pdir) or "missing: write brief.json (see skill)",
        "files": sorted(p.name for p in pdir.iterdir() if p.is_file()),
        "pools": {n: len(read_csv(pdir / f"{n}.csv")) for n in ("venues", "orgs")},
        "contacts": {"emails_by_grade": grades,
                     "orgs_by_grade": {g: len(s) for g, s in orgs_with.items()},
                     "phones": len(read_csv(pdir / "phones.csv"))},
        "crawl": {"orgs": len(crawl), "by_status": _count(crawl, "status"),
                  "jobs": [{k: j.get(k) for k in ("id", "total", "finished", "running", "stage")} for j in jobs]},
        "stop_requested": stop_requested(pdir),
        "run": _runs(pdir),
        "queue": _queue(pdir),
        "agent_runs": _count(agent, "status"),
        "costs": mo_budget.summary(pdir),
    }


def _runs(pdir: Path):
    import mo_run
    states = mo_run.run_states(pdir)
    alive = [p for p in mo_run._pids(pdir) if mo_run._alive(p)]
    if not states:
        return None
    return {"processes_running": len(alive), "parts": states}


def _queue(pdir: Path):
    import mo_tasks
    return mo_tasks.summary(pdir)["queue"] or None


def _run_state(pdir: Path) -> dict | None:
    try:
        return json.loads((pdir / "run.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _count(rows, field):
    out: dict[str, int] = {}
    for r in rows:
        out[r.get(field, "")] = out.get(r.get(field, ""), 0) + 1
    return out
