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
                "graded_by", "mailbox", "same_site", "email_site", "page_url", "found_by",
                "quote", "note", "added"]
PAGE_COLS = ["url", "final_url", "reader", "file", "chars", "org_id", "time"]
CRAWL_COLS = ["org_id", "org", "website", "status", "pages", "emails", "readers_ok", "note", "time"]
AGENT_COLS = ["run_id", "url", "goal", "org_id", "status", "file", "time"]
DATA_EXT = re.compile(r"\.(csv|json|geojson|xlsx?|pdf|zip|xml|txt)(\?|$)|/exports?/|/api/|format=(csv|json)", re.I)

_locks: dict[str, threading.Lock] = {}
_jobs: dict[str, dict] = {}


def _lock(path: Path) -> threading.Lock:
    return _locks.setdefault(str(path), threading.Lock())


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


def brief(pdir: Path) -> dict:
    try:
        return json.loads((pdir / "brief.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


# ------------------------------------------------------------------ pages
def _save(pdir: Path, url: str, reader: str, body: bytes, ext: str, final_url: str, org_id: str) -> str:
    name = hashlib.sha256(url.encode()).hexdigest()[:16] + f".{reader}.{ext}"
    (pdir / "pages" / name).write_bytes(body)
    append_csv(pdir / "pages.csv", PAGE_COLS, {"url": url, "final_url": final_url, "reader": reader,
                                               "file": "pages/" + name, "chars": len(body),
                                               "org_id": org_id, "time": _now()})
    return "pages/" + name


def merge_contacts(pdir: Path, found: list[dict], org_id: str = "", org: str = "") -> int:
    """Add candidates to contacts.csv; the same value for the same org is one row."""
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
                if len(c.get("quote", "")) > len(row.get("quote", "")) and not row.get("graded_by", "").startswith("agent"):
                    row["quote"], row["page_url"] = c["quote"], c["page_url"]
        write_csv(path, CONTACT_COLS, rows)
    return added


def fetch(pdir: Path, url: str, readers=None, org_id: str = "", org: str = "",
          org_site: str = "") -> dict:
    """Read a page with every chosen reader at once; save each version; collect contacts."""
    url = mo_providers.public_url(url)
    if readers in (None, "auto", "all", []) and DATA_EXT.search(url):
        readers = ["download"]
    readers = readers if isinstance(readers, list) else (None if readers in (None, "auto", "all") else [readers])
    report, texts, links = {}, {}, []
    if readers == ["download"]:
        try:
            got = mo_providers.download(url)
            ext = (re.search(r"\.(\w{2,5})(\?|$)", urllib.parse.urlsplit(got["final_url"]).path) or [None, "bin"])[1]
            if "json" in got["content_type"]:
                ext = "json"
            elif "csv" in got["content_type"]:
                ext = "csv"
            elif "html" in got["content_type"]:
                ext = "html"
            f = _save(pdir, url, "download", got["bytes"], ext, got["final_url"], org_id)
            mo_budget.record(pdir, "download", "fetch", True, url[:120])
            report["download"] = {"file": f, "bytes": len(got["bytes"]), "type": got["content_type"]}
            if ext == "html":
                parsed = mo_pages.from_html(got["bytes"].decode("utf-8", "replace"), got["final_url"])
                texts["download"], links = parsed["lines"], parsed["links"]
            elif ext in ("txt",):
                texts["download"] = got["bytes"].decode("utf-8", "replace").splitlines()
        except Exception as exc:  # noqa: BLE001
            report["download"] = {"error": f"{type(exc).__name__}: {exc}"[:160]}
        return {"url": url, "readers": report, "contacts": [], "links": [], "text": ""}
    results = mo_providers.read(url, readers, project_dir=pdir)
    for reader, res in results.items():
        if "error" in res:
            report[reader] = {"error": res["error"]}
            continue
        if res.get("html"):
            f = _save(pdir, url, reader, res["html"].encode("utf-8"), "html", res["final_url"], org_id)
            parsed = mo_pages.from_html(res["html"], res["final_url"])
        else:
            f = _save(pdir, url, reader, res["text"].encode("utf-8"), "md", res["final_url"], org_id)
            parsed = mo_pages.from_markdown(res["text"], res["final_url"])
        texts[reader] = parsed["lines"]
        links += parsed["links"]
        report[reader] = {"file": f, "chars": sum(map(len, parsed["lines"])), "final_url": res["final_url"]}
    found: dict[str, dict] = {}
    for reader, lines in texts.items():
        for c in mo_pages.contacts(lines, url, org_site or mo_pages.site_of(url)):
            k = c["value"].lower()
            if k in found:
                found[k]["found_by"] = "+".join(sorted(set(found[k]["found_by"].split("+")) | {reader}))
                if len(c["quote"]) > len(found[k]["quote"]):
                    found[k]["quote"] = c["quote"]
            else:
                found[k] = {**c, "found_by": reader}
    contacts = list(found.values())
    added = merge_contacts(pdir, contacts, org_id, org) if (org_id or org) else 0
    best = max(texts.values(), key=lambda ls: sum(map(len, ls)), default=[])
    return {"url": url, "readers": report, "contacts": contacts, "contacts_added": added,
            "links": mo_pages.contact_links(links, url), "links_total": len(links),
            "text": "\n".join(best)}


# ------------------------------------------------------------------ crawl jobs
def _crawl_one(pdir: Path, t: dict, max_pages: int, readers) -> dict:
    org_id, org, site = str(t.get("id") or t.get("org_id") or ""), str(t.get("name") or t.get("org") or ""), \
        str(t.get("website") or t.get("url") or "").strip()
    row = {"org_id": org_id, "org": org, "website": site, "time": _now()}
    if not site:
        return {**row, "status": "no_website"}
    if not site.startswith(("http://", "https://")):
        site = "https://" + site
    org_site = mo_pages.site_of(site)
    seen, queue, pages, emails, ok_readers, notes = set(), [site], 0, 0, set(), []
    while queue and pages < max_pages:
        url = queue.pop(0)
        if url in seen:
            continue
        seen.add(url)
        try:
            got = fetch(pdir, url, readers, org_id, org, org_site)
        except Exception as exc:  # noqa: BLE001
            notes.append(f"{url[:60]}: {exc}"[:100])
            if pages == 0 and url.startswith("https://") and "://www." not in url:
                queue.insert(0, url.replace("https://", "https://www.", 1))
            continue
        pages += 1
        ok = [r for r, v in got["readers"].items() if "error" not in v]
        ok_readers |= set(ok)
        emails += sum(c["kind"] == "email" for c in got["contacts"])
        if not ok and pages == 1:
            notes.append("no reader could open the home page")
        if pages == 1:
            queue += [u for u in got["links"] if u not in seen]
    status = "done" if ok_readers else "unreadable"
    return {**row, "status": status, "pages": pages, "emails": emails,
            "readers_ok": "+".join(sorted(ok_readers)), "note": "; ".join(notes)[:300]}


def crawl(pdir: Path, targets: list[dict], max_pages: int = 6, readers=None, workers: int = 4) -> dict:
    """Start crawling organisation sites in the background; returns at once."""
    done = {r["org_id"] for r in read_csv(pdir / "crawl.csv") if r.get("status") in ("done", "no_website")}
    todo = [t for t in targets if str(t.get("id") or t.get("org_id") or "") not in done]
    job_id = "crawl-" + hashlib.sha256(f"{pdir}{time.time()}".encode()).hexdigest()[:8]
    job = {"id": job_id, "total": len(todo), "finished": 0, "running": True, "started": _now()}
    _jobs[job_id] = job

    def work():
        with ThreadPoolExecutor(max_workers=workers) as pool:
            for result in pool.map(lambda t: _crawl_one(pdir, t, max_pages, readers), todo):
                append_csv(pdir / "crawl.csv", CRAWL_COLS, result)
                job["finished"] += 1
        job["running"] = False
    threading.Thread(target=work, daemon=True, name=job_id).start()
    return {"job": job_id, "queued": len(todo), "skipped_already_done": len(targets) - len(todo)}


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
    jobs = [j for j in _jobs.values() if j.get("id", "").startswith("crawl-")]
    return {
        "project": str(pdir), "brief": brief(pdir) or "missing: write brief.json (see skill)",
        "files": sorted(p.name for p in pdir.iterdir() if p.is_file()),
        "pools": {n: len(read_csv(pdir / f"{n}.csv")) for n in ("venues", "orgs")},
        "contacts": {"emails_by_grade": grades,
                     "orgs_by_grade": {g: len(s) for g, s in orgs_with.items()},
                     "phones": sum(c.get("kind") == "phone" for c in contacts)},
        "crawl": {"orgs": len(crawl), "by_status": _count(crawl, "status"),
                  "jobs": [{k: j[k] for k in ("id", "total", "finished", "running")} for j in jobs]},
        "agent_runs": _count(agent, "status"),
        "costs": mo_budget.summary(pdir),
    }


def _count(rows, field):
    out: dict[str, int] = {}
    for r in rows:
        out[r.get(field, "")] = out.get(r.get(field, ""), 0) + 1
    return out
