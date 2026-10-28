"""The research as a super-pipeline of pools, worked by agents.

    sources ──▶ pages ──▶ venues ──▶ organisations ──▶ contacts
    (lists)    (list and   (places)   (who runs them)   (people, graded)
               detail pages)

Every arrow is a stage with its own queue, and every stage runs at the same time as the others:
while one agent mines the first list into pages, venues and organisations, another is already
looking for the next list, and researchers read the organisations that have arrived. Inside a
stage several agents work at once, each on its own claim (one source, one package of
organisations), so no two writers touch the same thing. Arrows also run backwards: a page that
links to another directory is a new source, a venue's real operator is a new organisation.

This module holds the two pools the older chain lacked as pools of their own:
- the source pool (source_pool.csv): every list found, with its state - found, mining,
  harvested, pooled, dropped - so finding and mining are separate, parallel jobs;
- the page frontier (frontier.csv): list and detail pages queued from a source, read in
  bulk by `harvest`, each detail page turned into one row an agent can check and pool.
and `flow()`: how full each pool is, what waits between pools, who works where.
"""
from __future__ import annotations

import hashlib
import re
import urllib.parse
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import mo_pages
import mo_workspace as ws

SOURCE_POOL_COLS = ["source", "url", "kind", "row_is", "expect", "status", "found_by", "miner", "note", "time"]
# scale: an agent's call that a source gets all its organisations whatever its pilot gave
SOURCE_STATES = ("found", "mining", "harvested", "pooled", "scale", "dropped")
FRONTIER_COLS = ["url", "kind", "source", "status", "found_by", "time", "note"]
HARVEST_COLS = ["page_url", "source", "name", "title", "kind", "website", "website_candidates", "emails",
                "phones", "address", "city", "postcode", "lines"]
LIST_PAGES_MAX = 60      # pagination pages of one source queued in one call (more next call)
READ_AT_ONCE = 6         # detail pages read at the same time by one harvest call
PLATFORMS = ("facebook.", "instagram.", "twitter.", "x.com", "linkedin.", "youtube.", "google.", "wikipedia.",
             "tripadvisor.", "yelp.", "apple.com", "pagesjaunes.", "mappy.", "waze.", "booking.com", "tiktok.",
             "openstreetmap.", "bing.com", "doubleclick.", "addthis.", "sharethis.", "w3.org", "schema.org",
             "cookiebot.", "gstatic.", "googleapis.", "wa.me", "whatsapp.", "pinterest.", "vimeo.")
# a postcode and a town on a short address line: "75002 Paris", "1 rue X, 8004 Zürich" - not a
# year ("2026 Prize") and not a sentence
POSTCODE = re.compile(r"(?:^|,\s*|\s)(?:[A-Z]{1,2}-)?(?!(?:19|20)\d\d\b)(\d{4,5})\s+([A-ZÀ-Ý][\w'’-]+(?:[ -][\w'’-]+){0,2})\s*$")
KINDS = {
    "venue": "a place: a sports ground, pitch, hall, pool, course or other facility",
    "club_or_association": "a club, association or society that plays or organises an activity",
    "public_body": "a council, municipality, district, school or other public body or department",
    "company": "a business: an operator, contractor, shop or service provider",
    "list_or_other": "a list of several entries, a news item, or anything else",
}


def _slug(text: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")[:40]
    return s or hashlib.sha256((text or "").encode()).hexdigest()[:8]


def _host(url: str) -> str:
    return urllib.parse.urlsplit(url).netloc.lower().removeprefix("www.")


def _base(host: str) -> str:
    """The registered domain, roughly: portail.example.fr and www.example.fr are one site."""
    parts = host.split(".")
    keep = 3 if len(parts) > 2 and len(parts[-1]) == 2 and parts[-2] in ("co", "com", "org", "net", "gov", "ac") else 2
    return ".".join(parts[-keep:])


# ------------------------------------------------------------------ the source pool

def source_pool(pdir: Path) -> list[dict]:
    return ws.read_csv(pdir / "source_pool.csv")


def add_sources(pdir: Path, items: list[dict], agent: str = "") -> dict:
    """Sources an agent found: registered as `found`, mined later by whoever claims them.
    The same URL or name twice is one source (the second finder is noted)."""
    path = pdir / "source_pool.csv"
    added, known = [], []
    with ws._lock(path):
        rows = ws.read_csv(path)
        by_key = {}
        for r in rows:
            by_key[r["source"].casefold()] = r
            if r.get("url"):
                by_key[r["url"].rstrip("/").casefold()] = r
        for it in items or []:
            if not isinstance(it, dict):
                continue
            url = str(it.get("url") or "").strip()
            name = str(it.get("source") or it.get("name") or "").strip() or (_host(url) + urllib.parse.urlsplit(url).path)[:60]
            if not name:
                continue
            hit = by_key.get(name.casefold()) or (by_key.get(url.rstrip("/").casefold()) if url else None)
            if hit:
                if agent and agent not in hit.get("found_by", ""):
                    hit["found_by"] = "|".join(x for x in (hit.get("found_by"), agent) if x)
                known.append(hit["source"])
                continue
            row = {"source": name, "url": url, "kind": str(it.get("kind") or ""), "row_is": str(it.get("row_is") or ""),
                   "expect": str(it.get("expect") or ""), "status": "found", "found_by": agent,
                   "miner": "", "note": str(it.get("note") or "")[:300], "time": ws._now()}
            rows.append(row)
            by_key[name.casefold()] = row
            if url:
                by_key[url.rstrip("/").casefold()] = row
            added.append(name)
        ws.write_csv(path, SOURCE_POOL_COLS, rows)
    return {"added": added, "already_known": known}


def set_sources(pdir: Path, items: list[dict]) -> int:
    """Change a source's state or note: status found|mining|harvested|pooled|scale|dropped."""
    path = pdir / "source_pool.csv"
    n = 0
    with ws._lock(path):
        rows = ws.read_csv(path)
        idx = {r["source"].casefold(): r for r in rows}
        for it in items or []:
            r = idx.get(str(it.get("source") or "").casefold())
            if r is None:
                continue
            for f in ("status", "note", "row_is", "expect", "kind", "url", "miner"):
                if f in it and (f != "status" or it[f] in SOURCE_STATES):
                    r[f] = str(it[f])[:300]
            n += 1
        ws.write_csv(path, SOURCE_POOL_COLS, rows)
    return n


def mark_source(pdir: Path, source: str, status: str, miner: str = "", url: str = "") -> None:
    """Register the source if it is new, and move it forward (never back: pooled stays pooled)."""
    path = pdir / "source_pool.csv"
    with ws._lock(path):
        rows = ws.read_csv(path)
        hit = next((r for r in rows if r["source"].casefold() == source.casefold()), None)
        if hit is None:
            hit = {"source": source, "url": url, "status": "found", "found_by": miner, "time": ws._now()}
            rows.append(hit)
        order = {s: i for i, s in enumerate(SOURCE_STATES)}
        if hit.get("status") != "dropped" and order.get(status, 0) >= order.get(hit.get("status") or "found", 0):
            hit["status"] = status
        if miner and status == "mining":
            hit["miner"] = miner
        if url and not hit.get("url"):
            hit["url"] = url
        ws.write_csv(path, SOURCE_POOL_COLS, rows)


# ------------------------------------------------------------------ the page frontier

def queue_pages(pdir: Path, items: list[dict], agent: str = "") -> int:
    """Queue pages to read for a source: kind list (paginated listing: its detail links and next
    pages are followed) or detail (one entry: becomes one harvest row). Known URLs are skipped."""
    path = pdir / "frontier.csv"
    with ws._lock(path):
        rows = ws.read_csv(path)
        seen = {r["url"] for r in rows}
        n = 0
        for it in items or []:
            url = str(it.get("url") or "").strip()
            if not url.startswith("http") or url in seen:
                continue
            rows.append({"url": url, "kind": it.get("kind") or "detail", "source": it.get("source") or "",
                         "status": "queued", "found_by": agent, "time": ws._now(), "note": ""})
            seen.add(url)
            n += 1
        ws.write_csv(path, FRONTIER_COLS, rows)
    return n


READING_LEASE_MIN = 10   # a page claimed for reading longer than this (its harvest died) is queued again


def _take(pdir: Path, source: str, limit: int) -> list[dict]:
    """Claim up to `limit` queued pages of a source (list pages first) so two harvest calls on
    one source never read the same page. A claim is a lease: pages a dead harvest left in
    `reading` come back after READING_LEASE_MIN minutes."""
    from datetime import datetime, timedelta, timezone
    stale = (datetime.now(timezone.utc) - timedelta(minutes=READING_LEASE_MIN)).isoformat(timespec="seconds")
    path = pdir / "frontier.csv"
    with ws._lock(path):
        rows = ws.read_csv(path)
        mine = [r for r in rows if r.get("source") == source and (
            r.get("status") == "queued" or (r.get("status") == "reading" and r.get("time", "") < stale))]
        mine.sort(key=lambda r: r.get("kind") != "list")
        mine = mine[:limit]
        for r in mine:
            r["status"], r["time"] = "reading", ws._now()
        ws.write_csv(path, FRONTIER_COLS, rows)
    return [dict(r) for r in mine]


def _finish(pdir: Path, done: dict[str, tuple[str, str]]) -> None:
    path = pdir / "frontier.csv"
    with ws._lock(path):
        rows = ws.read_csv(path)
        for r in rows:
            if r["url"] in done:
                r["status"], r["note"] = done[r["url"]]
        ws.write_csv(path, FRONTIER_COLS, rows)


def sitemap_urls(url: str, pattern: str, limit: int = 2000) -> list[str]:
    """Detail URLs from a site's sitemap (and one level of sitemap index), matching `pattern` -
    the same idea as Crawl4AI's URL seeding, without a browser: a directory's every entry, found
    before a single listing page is paged through."""
    import mo_providers
    parts = urllib.parse.urlsplit(url)
    todo = [url] if url.endswith(".xml") else [f"{parts.scheme}://{parts.netloc}/sitemap.xml",
                                                 f"{parts.scheme}://{parts.netloc}/sitemap_index.xml"]
    rx = re.compile(pattern, re.I) if pattern else None
    out, seen = [], set()
    while todo and len(out) < limit and len(seen) < 60:
        sm = todo.pop(0)
        if sm in seen:
            continue
        seen.add(sm)
        try:
            body = mo_providers.download(sm, max_bytes=30_000_000)["bytes"]
            if sm.endswith(".gz") or body[:2] == b"\x1f\x8b":
                import gzip
                body = gzip.decompress(body)
        except Exception:  # noqa: BLE001
            continue
        text = body.decode("utf-8", errors="replace")
        for loc in re.findall(r"<loc>\s*([^<\s]+)\s*</loc>", text):
            loc = loc.replace("&amp;", "&")
            if "<sitemapindex" in text[:500] or loc.endswith((".xml", ".xml.gz")):
                todo.append(loc)
            elif not rx or rx.search(loc):
                out.append(loc)
    return list(dict.fromkeys(out))[:limit]


def _row_from_page(page: dict, source_host: str) -> dict:
    """One detail page -> one candidate row. Code finds the candidates (name, links off the
    site, addresses, phones, postcode lines); choosing among them is Jev's or the agent's job."""
    own = {_base(_host(page["url"])), _base(source_host)} - {""}
    seen, cands = set(), []
    for href, label in page.get("page_links") or page.get("content_links") or []:
        h = _host(href)
        if not h or _base(h) in own or any(p in h for p in PLATFORMS) or not href.startswith("http"):
            continue
        site = f"{urllib.parse.urlsplit(href).scheme}://{urllib.parse.urlsplit(href).netloc}"
        if site not in seen:
            seen.add(site)
            cands.append((site, " ".join(str(label).split())[:60]))
    title = re.split(r"\s+[|–—-]\s+", page.get("title") or "")[0].strip()
    lines = [x for x in (page.get("text") or "").split("\n") if x.strip()]
    if not title:
        # no <title> (a script-built page): its first short heading-like line, else the URL's last
        # segment - a row with no name never reaches the pools (072: 173 of 176 rows lost)
        title = next((x.strip("# ").strip() for x in lines[:15] if 3 <= len(x.strip("# ").strip()) <= 80
                      and not mo_pages.EMAIL.search(x)), "")
        if not title:
            slug = urllib.parse.unquote(urllib.parse.urlsplit(page["url"]).path.rstrip("/").rsplit("/", 1)[-1])
            title = re.sub(r"[-_]+", " ", re.sub(r"\.\w{2,5}$", "", slug)).strip().title()
    pc = next((m for m in (POSTCODE.search(x) for x in lines[:200] if len(x) <= 90) if m), None)
    emails = [c["value"] for c in page.get("contacts") or [] if c.get("kind") == "email"]
    phones = [c["value"] for c in page.get("contacts") or [] if c.get("kind") == "phone"]
    return {"page_url": page["url"], "name": title, "title": page.get("title") or "",
            "website_candidates": " ".join(f"{s} ({lab})" if lab else s for s, lab in cands[:8]),
            "_cands": [s for s, _ in cands[:8]], "emails": " ".join(dict.fromkeys(emails))[:400],
            "phones": " ".join(dict.fromkeys(phones))[:200],
            "address": pc.string[:160] if pc else "", "postcode": pc.group(1) if pc else "",
            "city": pc.group(2).strip()[:40] if pc else "", "lines": " / ".join(lines[:12])[:600]}


def _guess_site(row: dict) -> str:
    """Without Jev: the one candidate whose domain carries a word of the name, else nothing
    (the agent picks from website_candidates)."""
    words = [w for w in re.findall(r"[a-z0-9]+", mo_pages.unescape(row.get("name", "")).lower()) if len(w) > 3]
    hits = [c for c in row.get("_cands", []) if any(w in re.sub(r"[^a-z0-9]", "", _host(c)) for w in words)]
    return hits[0] if len(hits) == 1 else ""


def _jev_rows(pdir: Path, rows: list[dict]) -> int:
    """Jev in the page pool, the TypeSafe way - select instead of generate: for each detail page,
    which of the links code found is the described organisation's own website (with a Noul for
    "none of them"), and what kind of entry the page describes. One request per few pages, the
    page in its own question. Returns rows judged; 0 when Jev is not there (rows keep their
    candidates, the agent picks)."""
    import mo_budget
    import mo_grade
    if not mo_budget.jev_ready():
        return 0
    import typesafe_sdk as sdk
    judged = 0
    for i in range(0, len(rows), 8):
        chunk = rows[i:i + 8]
        state, qs = {}, {}
        for n, r in enumerate(chunk):
            k = f"p{n + 1}"
            state[k] = {"title": r["title"][:200], "text": r["lines"][:600], "links_off_the_site": r["_cands"]}
            qs[f"{k}_kind"] = sdk.Choice(instructions=f"What does the page `{k}` describe?",
                                         criteria=dict(KINDS))
            if r["_cands"]:
                qs[f"{k}_own"] = sdk.Noul(
                    instructions=f"Is one of `{k}.links_off_the_site` the own website of what page `{k}` describes?",
                    criteria={"true": "one link is the described club, venue, body or company's own site",
                              "false": "the links are sponsors, federations, platforms, partners or tools"})
                qs[f"{k}_site"] = sdk.Choice(
                    instructions=f"Which link is the own website of what page `{k}` describes?",
                    criteria={c: f"the site at {_host(c)}" for c in r["_cands"]})
        try:
            resp = mo_grade._jev({"pages": state}, qs)
        except Exception as exc:  # noqa: BLE001
            mo_budget.record(pdir, "jev", "grade", False, f"{type(exc).__name__}: {exc}")
            continue
        mo_budget.record(pdir, "jev", "grade", True, f"harvest x{len(chunk)}")
        got = mo_grade.answers(resp)
        for n, r in enumerate(chunk):
            k = f"p{n + 1}"
            if got.get(f"{k}_kind") is not None:
                r["kind"] = got[f"{k}_kind"].choice
                judged += 1
            own = mo_grade.yes(got.get(f"{k}_own"))
            site = got.get(f"{k}_site")
            if r.get("kind") == "list_or_other":   # a list of several entries has no own site of one
                continue
            if own is not None and site is not None:
                if own >= 0.7:
                    r["website"] = site.choice
                elif own > 0.3:   # the page does not settle it: keep the candidates for the agent
                    r["website"] = ""
    return judged


def _prefetch(pdir: Path, urls: list[str]) -> int:
    """Pages not read before go to TinyFish Fetch ten at a time (one request, read in parallel on
    its side) and into the shared library; the reads that follow take them from there."""
    import mo_library
    import mo_providers
    fresh = [u for u in urls if u not in ws._pages(pdir) and not mo_library.get_pages(u)]
    if len(fresh) < 2:
        return 0
    got = mo_providers.prefetch_tinyfish(fresh, pdir)
    for u, res in got.items():
        body, ext = (res["html"], "html") if res.get("html") else (res.get("text") or "", "md")
        if u in fresh and body:
            mo_library.put_page(u, "tinyfish", body.encode("utf-8"), ext, res.get("final_url") or u)
    return len(got)


def harvest(pdir: Path, source: str, start: str = "", follow: str = "", next: str = "", sitemap: bool = False,
            urls: list[str] | None = None, limit: int = 40, agent: str = "") -> dict:
    """Mine one source into the page pool and out of it: list pages are paged through, their
    entries' detail pages queued and read in bulk, and each detail page becomes one candidate
    row in pages/harvest-<source>.csv (name, website, kind, emails, phones, address). Call it
    again to continue: the frontier remembers what is queued and read. Pool the rows with
    pool(project, "pages/harvest-<source>.csv", into="orgs"|"venues", map=..., source=...)."""
    source = source.strip()
    if not source:
        return {"error": "source: the name of the list being mined (as in sources(...))"}
    mark_source(pdir, source, "mining", agent, start)
    src_host = _host(start) if start else ""
    queued = 0
    if start:
        # the start page is a listing: its entry links (follow), its next pages (next), or without
        # follow its own links off the site are the entries
        queued += queue_pages(pdir, [{"url": start, "kind": "list", "source": source}], agent)
    if urls:
        queued += queue_pages(pdir, [{"url": u, "kind": "detail", "source": source} for u in urls], agent)
    if sitemap and start:
        found = sitemap_urls(start, follow)
        queued += queue_pages(pdir, [{"url": u, "kind": "detail", "source": source} for u in found], agent)
    follow_rx = re.compile(follow, re.I) if follow else None
    next_rx = re.compile(next, re.I) if next else None
    out_path = pdir / "pages" / f"harvest-{_slug(source)}.csv"
    have = {r["page_url"] for r in ws.read_csv(out_path)}
    read = lists = 0
    new_rows: list[dict] = []
    while read < limit:
        batch = _take(pdir, source, min(READ_AT_ONCE * 2, limit - read))
        if not batch:
            break
        done: dict[str, tuple[str, str]] = {}
        _prefetch(pdir, [b["url"] for b in batch])

        def one(item):
            try:
                return item, ws.fetch(pdir, item["url"], via="harvest")
            except Exception as exc:  # noqa: BLE001
                return item, {"error": f"{type(exc).__name__}: {exc}"[:120]}
        with ThreadPoolExecutor(READ_AT_ONCE) as ex:
            results = list(ex.map(one, batch))
        more: list[dict] = []
        for item, page in results:
            read += 1
            if "error" in page or not (page.get("text") or page.get("page_links")):
                done[item["url"]] = ("failed", (page.get("error") or "no text")[:120])
                continue
            if item["kind"] == "list":
                lists += 1
                # every link on the listing: entry cards often sit outside the main content block
                links = list(dict.fromkeys(map(tuple, page.get("page_links") or page.get("content_links") or [])))
                details = [h for h, _ in links if follow_rx and follow_rx.search(h)]
                pages = [h for h, _ in links if next_rx and next_rx.search(h)][:LIST_PAGES_MAX]
                more += [{"url": h, "kind": "detail", "source": source} for h in details]
                more += [{"url": h, "kind": "list", "source": source} for h in pages]
                if not follow_rx:   # no detail pages: the listing's own links off the site are the entries
                    for href, label in links:
                        h = _host(href)
                        if h and h != _host(item["url"]) and not any(p in h for p in PLATFORMS) and str(label).strip():
                            new_rows.append({"page_url": href, "source": source, "name": " ".join(str(label).split())[:120],
                                             "website": f"{urllib.parse.urlsplit(href).scheme}://{urllib.parse.urlsplit(href).netloc}",
                                             "lines": f"listed on {item['url']}"})
                done[item["url"]] = ("read", f"{len(details)} entries, {len(pages)} pages")
            else:
                row = _row_from_page(page, src_host)
                row["source"] = source
                if row["page_url"] not in have:
                    new_rows.append(row)
                done[item["url"]] = ("read", "")
        _finish(pdir, done)
        if more:
            queued += queue_pages(pdir, more, agent)
    rows = [r for r in new_rows if r["page_url"] not in have]
    rows = list({r["page_url"]: r for r in rows}.values())
    # a link on most entry pages of a source is the portal's own chrome (its partners, its app),
    # not any entry's website
    entry_rows = [r for r in rows if "_cands" in r]
    if len(entry_rows) >= 4:
        freq: dict[str, int] = {}
        for r in entry_rows:
            for c in set(r["_cands"]):
                freq[c] = freq.get(c, 0) + 1
        chrome = {c for c, n in freq.items() if n > len(entry_rows) / 2}
        for r in entry_rows:
            if chrome & set(r["_cands"]):
                r["_cands"] = [c for c in r["_cands"] if c not in chrome]
                r["website_candidates"] = " ".join(x for x in r["website_candidates"].split(" ")
                                                   if not any(x.startswith(c) for c in chrome) and x)
    judged = _jev_rows(pdir, [r for r in rows if "_cands" in r])
    for r in rows:
        if "_cands" in r and not r.get("website") and judged == 0:
            r["website"] = _guess_site(r)
        r.pop("_cands", None)
    if rows:
        with ws._lock(out_path):
            old = ws.read_csv(out_path)
            ws.write_csv(out_path, HARVEST_COLS, old + rows)
        if not old:
            ws.note_made(pdir, f"pages/{out_path.name}")
    left = sum(1 for r in ws.read_csv(pdir / "frontier.csv") if r.get("source") == source and r.get("status") in ("queued", "reading"))
    if not left:
        mark_source(pdir, source, "harvested")
    total = len(ws.read_csv(out_path))
    return {"source": source, "pages_read": read, "list_pages": lists, "queued_now": queued, "still_queued": left,
            "rows_new": len(rows), "rows_total": total, "file": f"pages/{out_path.name}",
            "judged_by_jev": judged, "sample": [{k: r.get(k, "") for k in ("name", "kind", "website", "emails", "city")}
                                                for r in rows[:5]],
            "next": ("call harvest again to continue" if left else "this source's pages are read") +
                    f"; check 20 rows, then pool(project, 'pages/{out_path.name}', into='orgs' or 'venues', "
                    "map={'name': 'name', 'website': 'website', 'city': 'city', 'type': 'kind', 'page_url': 'page_url'}, "
                    f"source='{source}') - its pages are already read, so their addresses join the new organisations "
                    "at no cost." + ("" if judged or not rows else
                                     " No Jev: website is set only where one candidate carries the name; "
                                     "pick from website_candidates where it is empty.")}


# ------------------------------------------------------------------ the flow

def flow(pdir: Path) -> dict:
    """How full each pool is, what waits between pools, and who works where - the lead staffs
    the stage where work piles up, and every stage keeps running while the others do."""
    spool = source_pool(pdir)
    ledger = [r for r in ws.read_csv(pdir / "sources.csv") if r.get("source")]
    pooled_names = {r["source"] for r in ledger}
    by_state = {s: 0 for s in SOURCE_STATES}
    for r in spool:
        st = "pooled" if r["source"] in pooled_names and r.get("status") != "dropped" else (r.get("status") or "found")
        by_state[st] = by_state.get(st, 0) + 1
    sat = ws.source_saturation(pdir)
    frontier = ws.read_csv(pdir / "frontier.csv")
    harvests = sorted((pdir / "pages").glob("harvest-*.csv")) if (pdir / "pages").exists() else []
    pooled_files = {Path(r.get("query") or r["source"]).name for r in ledger} | {r["source"] for r in ledger}
    unpooled = []
    for f in harvests:
        src = next((r["source"] for r in spool if f"harvest-{_slug(r['source'])}.csv" == f.name), "")
        if f.name not in pooled_files and f"pages/{f.name}" not in pooled_files and src not in pooled_names:
            unpooled.append(f"pages/{f.name}")
    venues = ws.read_csv(pdir / "venues.csv")
    orgs = ws.read_csv(pdir / "orgs.csv")
    ids = {str(o.get("id") or "") for o in orgs}
    crawl = ws.read_csv(pdir / "crawl.csv")
    read = {r["org_id"] for r in crawl}
    contacts = ws.read_contacts(pdir)
    confirmed = {c["org_id"] for c in contacts if c.get("grade") in ("A", "B") and c.get("review") != "yes"}
    from_sources = 0
    for r in ledger:
        try:
            from_sources += int(r.get("orgs_added") or 0)
        except ValueError:
            pass
    lanes: dict = {}
    for t in ws.read_csv(pdir / "tasks.csv"):
        lane = lanes.setdefault(t["kind"], {"open": 0, "claimed": 0, "done": 0, "agents": set()})
        lane[t["status"]] = lane.get(t["status"], 0) + 1
        if t["status"] == "claimed" and t.get("agent"):
            lane["agents"].add(t["agent"])
    for lane in lanes.values():
        lane["agents"] = sorted(lane["agents"])
    pools = {
        "sources": {"known": len({r["source"] for r in spool} | pooled_names),
                    **by_state, "saturated": sat["saturated"], "new_share_last": sat["new_share_last"]},
        "pages": {"queued": sum(r["status"] == "queued" for r in frontier),
                  "reading": sum(r["status"] == "reading" for r in frontier),
                  "read": sum(r["status"] == "read" for r in frontier),
                  "failed": sum(r["status"] == "failed" for r in frontier),
                  "harvest_files_not_pooled": unpooled},
        "venues": {"total": len(venues), "without_organisation": sum(1 for v in venues if not v.get("org_id"))},
        "organisations": {"total": len(ids), "no_website": sum(1 for o in orgs if not o.get("website")),
                          "not_read": len(ids - read), "with_confirmed_ab": len(confirmed & ids)},
        "contacts": {"rows": len(contacts), "to_review": sum(1 for c in contacts if c.get("review") == "yes")},
    }
    waiting = {
        "sources -> pages": by_state.get("found", 0),
        "pages -> pools": pools["pages"]["queued"] + len(unpooled),
        "venues -> organisations": pools["venues"]["without_organisation"],
        "organisations -> contacts": pools["organisations"]["not_read"],
        "contacts -> delivery": pools["contacts"]["to_review"],
    }
    unlogged = sat["orgs_without_logged_source"]
    import mo_tasks
    turns = mo_tasks.source_yield(pdir)
    return {"pools": pools, "waiting_between_pools": waiting, "lanes": lanes,
            # each source's pilot and yield, as the research lanes hand them out (scale / drop:
            # sources(set=[{source, status}]))
            "sources_take_turns": dict(sorted(turns.items(), key=lambda kv: -kv[1]["orgs"])[:40]),
            "reconcile": {"organisations": len(ids), "added_by_logged_sources": from_sources,
                          "without_logged_source": unlogged,
                          "adds_up": len(ids) == from_sources + unlogged}}
