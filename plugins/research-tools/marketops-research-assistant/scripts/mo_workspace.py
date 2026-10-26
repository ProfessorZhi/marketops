"""A project is a plain folder the agent and the user can open.

    <project>/brief.json      what is wanted (roles per grade, region, exclusions)
    <project>/venues.csv      the venue pool       (agent-owned: write it any way you like)
    <project>/orgs.csv        the organisation pool (agent-owned; crawl reads id,name,website)
    <project>/sources.csv     every data source pooled, with what it added
    <project>/contacts/       contact candidates, one file per organisation (tools write them;
                              change grades/people with grade(set=...))
    <project>/contacts.csv    a snapshot of all contacts for people to open
    <project>/crawl.csv       one line per crawled organisation (status, pages, contacts)
    <project>/pages/          every page as read, one file per reader
    <project>/pages.csv       index of saved pages
    <project>/agent.csv       TinyFish Agent runs
    <project>/costs.jsonl     every provider call
"""
from __future__ import annotations

import csv
import json
import re
import threading
import urllib.parse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

import mo_budget
import mo_config
import mo_pages
import mo_providers

CONTACT_COLS = ["org_id", "org", "kind", "value", "person", "role", "grade", "grade_reason",
                "graded_by", "review", "review_reason", "evidence", "evidence_score", "evidence_why",
                "pages_seen", "also_on", "last_verified", "still_listed", "email_on_org", "found_in", "mailbox", "same_site", "email_site", "page_site", "page_on_org_site",
                "page_url", "found_by", "quote", "quote_from", "note", "added"]
PHONE_COLS = ["org_id", "org", "value", "page_url", "quote", "found_by", "added"]
PAGE_COLS = ["url", "final_url", "reader", "file", "chars", "org_id", "time"]
CRAWL_COLS = ["org_id", "org", "website", "status", "pages", "emails", "readers_ok", "email_buttons",
              "pass", "note", "time"]
AGENT_COLS = ["run_id", "url", "goal", "org_id", "status", "file", "time"]
DATA_EXT = re.compile(r"\.(csv|json|geojson|xlsx?|zip|xml|txt)(\?|$)|/exports?/|/api/|format=(csv|json)", re.I)
PAGE_PARALLEL = 4   # pages of one organisation read at the same time
PDF = re.compile(r"\.pdf(\?|$)", re.I)
_page_index: dict[str, dict[str, list[dict]]] = {}

_agent_orgs: dict[str, str] = {}


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


def append_jsonl(path: Path, row: dict) -> None:
    with _lock(path):
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")


def weights(pdir: Path, kind: str) -> dict:
    """Provider shares for 'search' or 'fetch': brief.providers.<kind> over the defaults."""
    own = (brief(pdir).get("providers") or {}).get(kind) or {}
    return {**mo_providers.DEFAULT_WEIGHTS[kind], **own}


def brief(pdir: Path) -> dict:
    try:
        return json.loads((pdir / "brief.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def locale(pdir: Path) -> dict:
    """Where and in what language to search (brief.country ISO-2, brief.language), and the goal
    as the search intent. Engines default to US / English without them."""
    b = brief(pdir)
    return {"country": str(b.get("country") or "").strip(), "language": str(b.get("language") or "").strip(),
            "purpose": str(b.get("goal") or "").strip()}


# ------------------------------------------------------------------ pages
def _save(pdir: Path, url: str, reader: str, body: bytes, ext: str, final_url: str, org_id: str) -> str:
    """The page goes into the shared library (one copy for every project); the project
    indexes it in pages.csv."""
    import mo_library
    path = mo_library.put_page(url, reader, body, ext, final_url)
    _index_page(pdir, {"url": url, "final_url": final_url, "reader": reader, "file": path,
                       "chars": len(body), "org_id": org_id, "time": _now()})
    return path


def _index_page(pdir: Path, row: dict) -> None:
    append_csv(pdir / "pages.csv", PAGE_COLS, row)
    _pages(pdir).setdefault(row["url"], []).append(row)


def _pages(pdir: Path) -> dict[str, list[dict]]:
    key = str(pdir)
    if key not in _page_index:
        index: dict[str, list[dict]] = {}
        for r in read_csv(pdir / "pages.csv"):
            index.setdefault(r["url"], []).append(r)
        _page_index[key] = index
    return _page_index[key]


# ------------------------------------------------------------------ contact store
# One small file per organisation (contacts/<org>.csv), so finishing a page rewrites only that
# organisation's file - a project with tens of thousands of addresses stays fast with many
# workers. contacts.csv is a snapshot of all of them for people to open (written on export
# and every few minutes during a run); change contacts through the tools, not that snapshot.
_contact_cache: dict[str, tuple] = {}
_phone_seen: dict[str, set] = {}


def _cfile(pdir: Path, oid: str) -> Path:
    return pdir / "contacts" / (re.sub(r"[^\w.-]", "_", oid or "_none")[:90] + ".csv")


def _migrate(pdir: Path) -> None:
    folder = pdir / "contacts"
    if folder.exists():
        return
    folder.mkdir(parents=True, exist_ok=True)
    groups: dict[str, list[dict]] = {}
    for r in read_csv(pdir / "contacts.csv"):   # a project from before per-organisation files
        groups.setdefault(r.get("org_id", ""), []).append(r)
    for oid, rows in groups.items():
        write_csv(_cfile(pdir, oid), CONTACT_COLS, rows)


def read_contacts(pdir: Path) -> list[dict]:
    """Every contact row of the project (fresh copies; cached until a file changes)."""
    _migrate(pdir)
    files = sorted((pdir / "contacts").glob("*.csv"))
    sig = tuple((f.name, f.stat().st_mtime_ns, f.stat().st_size) for f in files)
    cached = _contact_cache.get(str(pdir))
    if not cached or cached[0] != sig:
        rows = [r for f in files for r in read_csv(f)]
        _contact_cache[str(pdir)] = cached = (sig, rows)
    return [dict(r) for r in cached[1]]


def contacts_of(pdir: Path, oid: str) -> list[dict]:
    _migrate(pdir)
    return read_csv(_cfile(pdir, oid))


def update_contacts(pdir: Path, rows: list[dict], fields: list[str], guard=None) -> int:
    """Write `fields` of these rows back, organisation by organisation, each under its own
    lock and re-read first, so nothing another worker added meanwhile is lost. guard(current
    row) -> False keeps that row as it is (e.g. a grade the agent set)."""
    groups: dict[str, list[dict]] = {}
    for r in rows:
        groups.setdefault(r.get("org_id", ""), []).append(r)
    changed = 0
    for oid, group in groups.items():
        path = _cfile(pdir, oid)
        with _lock(path):
            current = read_csv(path)
            index = {r.get("value", "").lower(): r for r in current}
            for r in group:
                cur = index.get(r.get("value", "").lower())
                if cur is not None and (guard is None or guard(cur)):
                    cur.update({f: r.get(f, "") for f in fields})
                    changed += 1
            write_csv(path, CONTACT_COLS, current)
    return changed


def snapshot_contacts(pdir: Path) -> None:
    write_csv(pdir / "contacts.csv", CONTACT_COLS, read_contacts(pdir))


def _merge_phones(pdir: Path, phones: list[dict], org_id: str, org: str) -> None:
    path = pdir / "phones.csv"
    with _lock(path):
        seen = _phone_seen.get(str(pdir))
        if seen is None:
            seen = {(r["org_id"], re.sub(r"\D", "", r["value"])) for r in read_csv(path)}
            _phone_seen[str(pdir)] = seen
        for c in phones:
            key = (org_id, re.sub(r"\D", "", c["value"]))
            if key not in seen:
                seen.add(key)
                new = not path.exists()
                with open(path, "a", newline="", encoding="utf-8") as fh:
                    w = csv.DictWriter(fh, fieldnames=PHONE_COLS, extrasaction="ignore")
                    if new:
                        w.writeheader()
                    w.writerow({"org_id": org_id, "org": org, "value": c["value"], "page_url": c.get("page_url", ""),
                                "quote": c.get("quote", ""), "found_by": c.get("found_by", ""), "added": _now()})


def address_rule(pdir: Path, email: str) -> str:
    """The grade the brief gives an address by its name alone (brief.address_rules, e.g.
    {"info": "X", "book": "X"}): the whole name or its first part. "" when no rule applies."""
    rules = brief(pdir).get("address_rules") or {}
    local = email.split("@")[0].lower()
    for word, grade in rules.items():
        word = str(word).lower()
        if grade in ("A", "B", "C", "D", "X") and (local == word or re.match(re.escape(word) + r"[\W_\d]", local)):
            return grade
    return ""


def merge_contacts(pdir: Path, found: list[dict], org_id: str = "", org: str = "", via: str = "") -> int:
    """Add candidates to the organisation's contact file (emails) and phones.csv; one row per
    address and organisation. via: the stage that found them (site, deep2, evidence, ...)."""
    phones = [c for c in found if c.get("kind") == "phone"]
    if phones:
        _merge_phones(pdir, phones, org_id, org)
    found = [c for c in found if c.get("kind") != "phone"]
    if not found:
        return 0
    _migrate(pdir)
    path = _cfile(pdir, org_id)
    with _lock(path):
        rows = read_csv(path)
        index = {r.get("value", "").lower(): r for r in rows}
        added = 0
        for c in found:
            key = c["value"].lower()
            row = index.get(key)
            if row is None:
                row = {**{k: "" for k in CONTACT_COLS}, **{k: c.get(k, "") for k in CONTACT_COLS},
                       "org_id": org_id, "org": org, "found_by": c.get("found_by", ""), "added": _now(),
                       "found_in": via or c.get("found_in", "")}
                rule = address_rule(pdir, c["value"]) if c.get("kind") == "email" else ""
                if rule:
                    row["grade"], row["graded_by"], row["grade_reason"] = rule, "rule", f"address rule: {rule}"
                elif c.get("kind") == "email" and c.get("mailbox") == "generic":
                    row["grade"], row["graded_by"] = "D", "rule: generic mailbox"
                rows.append(row)
                index[key] = row
                added += 1
            else:
                by = set(filter(None, row.get("found_by", "").split("+"))) | set(filter(None, c.get("found_by", "").split("+")))
                row["found_by"] = "+".join(sorted(by))
                if c.get("last_verified", "") > row.get("last_verified", ""):
                    row["last_verified"], row["still_listed"] = c["last_verified"], "yes"
                pages = [row.get("page_url", "")] + [u for u in row.get("also_on", "").split(" ") if u]
                if c.get("page_url") and c["page_url"] not in pages:
                    pages.append(c["page_url"])
                    row["also_on"] = " ".join(pages[1:6])
                    row["pages_seen"] = str(len(pages))
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
        local = pdir / "pages" / Path(f).name   # data files also in the project, for pool(...)
        local.write_bytes(got["bytes"])
        mo_budget.record(pdir, "download", "fetch", True, url[:120])
        report["download"] = {"file": "pages/" + local.name, "bytes": len(got["bytes"]), "type": got["content_type"]}
    except Exception as exc:  # noqa: BLE001
        report["download"] = {"error": f"{type(exc).__name__}: {exc}"[:160]}
    return {"url": url, "readers": report, "contacts": [], "links": [], "text": ""}


def _parse_saved(pdir: Path, row: dict) -> dict:
    body = (pdir / row["file"]).read_text(encoding="utf-8", errors="replace")
    if row["file"].endswith(".html"):
        return mo_pages.from_html(body, row["final_url"] or row["url"])
    return mo_pages.from_markdown(body, row["final_url"] or row["url"])


def fetch(pdir: Path, url: str, readers=None, org_id: str = "", org: str = "",
          org_site: str = "", refresh: bool = False, via: str = "fetch") -> dict:
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
    if not saved and not refresh:
        import mo_library
        for r in mo_library.get_pages(url):   # read before, by any project: reuse, no new call
            row = {**r, "org_id": org_id}
            _index_page(pdir, row)
            saved.append({**row, "library": True})
    read_at: dict[str, str] = {}
    if saved:
        for row in saved:
            try:
                parsed_by[row["reader"]] = _parse_saved(pdir, row)
                read_at[row["reader"]] = row.get("time", "")
                report[row["reader"]] = {"file": row["file"], "cached": "library" if row.get("library") else "project"}
            except OSError:
                continue
    page_site = mo_pages.site_of(url)
    words = [w for w in re.findall(r"[a-z0-9]+", (org or "").lower()) if len(w) > 3]
    known_site = org_site  # the organisation's own website, if the pool has one

    def belongs(site: str) -> str:
        if known_site:
            return "yes" if site == known_site else "no"
        # no known website: a domain carrying the organisation's name is likely its own
        squashed = re.sub(r"[^a-z0-9]", "", site)
        return "likely" if words and any(w in squashed for w in words) else "unknown"
    on_org = belongs(page_site)
    org_site = org_site or page_site

    def take(reader: str, res: dict):
        if "error" in res:
            report[reader] = {"error": res["error"]}
            return None
        if res.get("html"):
            f = _save(pdir, url, reader, res["html"].encode("utf-8"), "html", res["final_url"], org_id)
            parsed = mo_pages.from_html(res["html"], res["final_url"])
        else:
            f = _save(pdir, url, reader, res["text"].encode("utf-8"), "md", res["final_url"], org_id)
            parsed = mo_pages.from_markdown(res["text"], res["final_url"])
        read_at[reader] = _now()
        report[reader] = {"file": f, "chars": sum(map(len, parsed["lines"])), "final_url": res["final_url"]}
        return parsed

    def extract(parsed_map: dict) -> list[dict]:
        found: dict[str, dict] = {}
        for reader, parsed in parsed_map.items():
            for c in mo_pages.contacts(parsed["lines"], url, org_site, parsed.get("blocks"), parsed.get("mailtos")):
                c.update(page_site=page_site, page_on_org_site=on_org, last_verified=read_at.get(reader, ""),
                         email_on_org=belongs(c.get("email_site", "")) if c.get("kind") == "email" else "")
                k = c["value"].lower()
                if k in found:
                    found[k]["found_by"] = "+".join(sorted(set(found[k]["found_by"].split("+")) | {reader}))
                    found[k]["last_verified"] = max(found[k].get("last_verified", ""), c.get("last_verified", ""))
                    if (c["quote_from"] == "block") > (found[k]["quote_from"] == "block"):
                        found[k]["quote"], found[k]["quote_from"] = c["quote"], "block"
                else:
                    found[k] = {**c, "found_by": reader}
        return list(found.values())

    def late(reader: str, res: dict) -> None:
        """A slower reader finished after the page moved on: its version is kept and its
        contacts merged all the same."""
        try:
            parsed = take(reader, res)
            if parsed and (org_id or org):
                merge_contacts(pdir, extract({reader: parsed}), org_id, org, via)
        except Exception:  # noqa: BLE001
            pass

    def read_with(chosen, race: bool = False):
        got = mo_providers.read_race(url, chosen, project_dir=pdir, on_late=late) if race \
            else mo_providers.read(url, chosen, project_dir=pdir)
        for reader, res in got.items():
            parsed = take(reader, res)
            if parsed:
                parsed_by[reader] = parsed

    if not parsed_by:
        if readers is None:
            now, held = mo_providers.pick(url, mo_providers.available_readers(), weights(pdir, "fetch"),
                                          always=("crawl4ai",))
        else:
            now, held = readers, []
        read_with(now, race=len(now) > 1)
        # Quality floor: nothing read, or a contact-looking page with no address -> the rest too.
        emails = any(mo_pages.EMAIL.search(" ".join(p["lines"])) for p in parsed_by.values())
        contactish = any(h in url.lower() for h in ("kontakt", "contact", "impressum", "vorstand", "team",
                                                     "staff", "people", "board", "committee", "equipe"))
        if held and (not parsed_by or (contactish and not emails)):
            read_with(held)
    links: list = []
    content_links: list = []
    buttons = 0
    for reader, parsed in parsed_by.items():
        links += parsed["links"]
        content_links += parsed.get("content_links", parsed["links"])
        buttons = max(buttons, parsed.get("hidden_email_buttons", 0))
    found = {c["value"].lower(): c for c in extract(parsed_by)}
    contacts = list(found.values())
    added = merge_contacts(pdir, contacts, org_id, org, via) if (org_id or org) else 0
    best = max((p["lines"] for p in parsed_by.values()), key=lambda ls: sum(map(len, ls)), default=[])
    emails_seen = sum(c["kind"] == "email" for c in contacts)
    return {"url": url, "readers": report, "contacts": contacts, "contacts_added": added,
            "links": mo_pages.contact_links(links, url, limit=25),
            "scored_links": mo_pages.contact_links(links, url, limit=25, scores=True),
            "links_total": len(links), "content_links": content_links[:800],
            "email_buttons_without_address": max(0, buttons - emails_seen),
            "text": "\n".join(best)}


# ------------------------------------------------------------------ crawl jobs
SHARED_SITE_ORGS = 3   # a website domain listed for this many organisations is a portal, not their own site
_shared_cache: dict[str, tuple] = {}


def shared_sites(pdir: Path) -> set[str]:
    """Website domains that several organisations of the pool share (a city portal, a
    federation's club pages, a site builder): no organisation owns them."""
    f = pdir / "orgs.csv"
    sig = f.stat().st_mtime_ns if f.exists() else 0
    cached = _shared_cache.get(str(pdir))
    if not cached or cached[0] != sig:
        names: dict[str, set] = {}
        for o in read_csv(f):
            site = mo_pages.site_of(str(o.get("website") or ""))
            if site:
                names.setdefault(site, set()).add(str(o.get("name") or "").strip().lower())
        _shared_cache[str(pdir)] = cached = (sig, {s for s, n in names.items() if len(n) >= SHARED_SITE_ORGS})
    return cached[1]


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
    # On a portal shared by many organisations only the organisation's own page and the pages
    # below it are followed - never the whole portal (its inboxes belong to nobody here).
    scope = ""
    if org_site and org_site in shared_sites(pdir):
        parts = urllib.parse.urlsplit(site)
        scope = parts.netloc.lower() + (parts.path.rstrip("/") or "/")

    def in_scope(url: str) -> bool:
        if not scope:
            return True
        parts = urllib.parse.urlsplit(url)
        key = parts.netloc.lower() + (parts.path.rstrip("/") or "/")
        return key == scope or key.startswith(scope + "/")

    heap: list = []
    for n, u in enumerate(([site] if site else []) + (extra or [])):
        heapq.heappush(heap, (-100 + n, 0, u))
    seen, pages, emails, ok_readers, notes, buttons = set(), 0, 0, set(), [], 0

    def get(url):
        try:
            return url, fetch(pdir, url, readers, org_id, org, org_site, via=label), None
        except Exception as exc:  # noqa: BLE001
            return url, None, exc

    with ThreadPoolExecutor(max_workers=PAGE_PARALLEL) as pages_pool:
        while heap and pages < max_pages:
            batch = []   # the best few waiting pages, read at the same time
            while heap and len(batch) < min(PAGE_PARALLEL, max_pages - pages):
                _neg, depth, url = heapq.heappop(heap)
                if url not in seen:
                    seen.add(url)
                    batch.append((depth, url))
            depths = dict((u, d) for d, u in batch)
            for url, got, exc in pages_pool.map(get, [u for _d, u in batch]):
                depth = depths[url]
                if exc is not None:
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
                    notes.append(f"{got['email_buttons_without_address']} email buttons without address on "
                                 f"{url[:80]}: try browse")
                if depth < 2 and mo_pages.site_of(url) == (org_site or mo_pages.site_of(url)):
                    for link, score in got["scored_links"]:
                        if link not in seen and in_scope(link):
                            heapq.heappush(heap, (-score + depth, depth + 1, link))
    status = "done" if ok_readers else "unreadable"
    if scope:
        notes.insert(0, f"shared portal: only {scope} and below")
    return {**row, "status": status, "pages": pages, "emails": emails, "email_buttons": buttons,
            "readers_ok": "+".join(sorted(ok_readers)), "note": "; ".join(notes)[:400]}


def stop_requested(pdir: Path) -> bool:
    return (pdir / "STOP").exists()


# ------------------------------------------------------------------ TinyFish Agent
# Written to TinyFish's goal guide (docs.tinyfish.ai/prompting-guide): objective, target, steps,
# fields, output shape, guardrails, edge case - specific goals ran 4.9x faster in their tests.
# One line per entry so the contact parser keeps each role, name and address together.
DEFAULT_GOAL = """Extract every person and every role mailbox shown on this page (staff, board, committee, contact or venue sections).
Steps:
1. If a cookie or consent banner appears, close it.
2. On this page only, click tabs, accordions, "show more", "show email" or "contact" buttons that reveal contact details.
For each entry return: role or job title as displayed; person name as displayed; email address as displayed or revealed; phone number.
Return one entry per line, exactly: ROLE | NAME | EMAIL | PHONE - use - for anything missing.
Do not log in, submit forms, send messages, or accept terms. Do not leave the site. Never guess or construct an email address: only addresses shown on the page.
If no person and no address is shown, return NONE.
"""


def browse(pdir: Path, url: str, goal: str = "", org_id: str = "", org: str = "") -> dict:
    url = mo_providers.public_url(url)
    for r in read_csv(pdir / "agent.csv"):
        if r["url"] == url and r["status"] not in ("FAILED", "CANCELLED", "error"):
            return {"run_id": r["run_id"], "status": r["status"], "note": "already submitted for this URL"}
    run_id = mo_providers.agent_submit(url, goal or DEFAULT_GOAL)
    append_csv(pdir / "agent.csv", AGENT_COLS, {"run_id": run_id, "url": url, "goal": goal or "default",
                                                "org_id": org_id, "status": "RUNNING", "time": _now()})
    _agent_orgs[run_id] = org
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
            org = _agent_orgs.get(r["run_id"], "")
            merge_contacts(pdir, found, r["org_id"], org, "agent")
        elif status in ("FAILED", "CANCELLED"):
            mo_budget.record(pdir, "tinyfish", "agent", False, r["url"][:120])
        r["status"] = status
        changed = True
    if changed:
        write_csv(pdir / "agent.csv", AGENT_COLS, rows)
    return rows


def status(pdir: Path) -> dict:
    contacts = read_contacts(pdir)
    grades: dict[str, int] = {}
    orgs_with = {}
    for c in contacts:
        if c.get("kind") != "email":
            continue
        g = c.get("grade") or "ungraded"
        grades[g] = grades.get(g, 0) + 1
        orgs_with.setdefault(g, set()).add(c.get("org_id"))
    crawl = [r for r in read_csv(pdir / "crawl.csv") if (r.get("pass") or "site") == "site"]
    agent = refresh_agent_runs(pdir) if (pdir / "agent.csv").exists() else []
    return {
        "project": str(pdir), "brief": brief(pdir) or "missing: write brief.json (see skill)",
        "files": sorted(p.name for p in pdir.iterdir() if p.is_file()),
        "pools": {n: len(read_csv(pdir / f"{n}.csv")) for n in ("venues", "orgs")},
        "contacts": {"emails_by_grade": grades,
                     "orgs_by_grade": {g: len(s) for g, s in orgs_with.items()},
                     "phones": len(read_csv(pdir / "phones.csv"))},
        "sites_read": {"orgs": len(crawl), "by_status": _count(crawl, "status")},
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
