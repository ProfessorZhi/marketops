"""Build the venue pool and the organisation pool from a saved data file (open-data export).

    pool(project, "pages/<file>.json", filters={"dep_code": "75", "equip_nature": "Intérieur"},
         map={"id": "equip_numero", "name": "equip_nom", "operator": "equip_gest_nom", ...},
         group_by="operator")

reads the file `fetch` saved (JSON list, {"results": [...]}, GeoJSON or CSV), keeps the rows
that match the filters, and adds them to venues.csv with the mapped column names; with
group_by, one organisation per distinct value of that column goes to orgs.csv (venues linked
by org_id). Every source is added to the same pools - the pools are the union of all sources:
the same venue (name + postcode/city) or organisation (its registry number - UAI, SIRET, RNA -
else name + town) seen in another source becomes one row, its empty fields filled from the new
source, all sources listed in `sources`. Nothing is dropped.

A list that prints an address on an organisation's own row (a school directory's mailbox, a
federation's club list, a timetable with each club's contact) hands it to that organisation as
a contact at once: the row is the record that ties them, and it is graded like any other.
"""
from __future__ import annotations

import csv
import hashlib
import io
import json
import re
import unicodedata
import urllib.parse
from pathlib import Path

import mo_workspace as ws


def _rows(path: Path) -> list[dict]:
    text = path.read_text(encoding="utf-8-sig", errors="replace")
    if path.suffix.lower() == ".csv" or not text.lstrip().startswith(("[", "{")):
        # the header decides the delimiter: French open data is ';'-separated and its text fields
        # are full of ',' (and the other way round) - counting the whole sample guessed wrong (072)
        header = text.lstrip().split("\n", 1)[0]
        delim = max((";", ",", "\t", "|"), key=header.count)
        return list(csv.DictReader(io.StringIO(text), delimiter=delim if header.count(delim) else ","))
    data = json.loads(text)
    if isinstance(data, dict):
        if isinstance(data.get("features"), list):
            return [{**(f.get("properties") or {})} for f in data["features"]]
        for key in ("results", "records", "data", "items", "rows"):
            if isinstance(data.get(key), list):
                data = data[key]
                break
    if not isinstance(data, list):
        raise ValueError("no list of rows in this file")
    out = []
    for r in data:
        if isinstance(r, dict):
            r = r.get("fields") or r.get("record", {}).get("fields") or r
            out.append({k: (json.dumps(v, ensure_ascii=False) if isinstance(v, (dict, list)) else v)
                        for k, v in r.items()})
    return out


def _match(row: dict, filters: dict) -> bool:
    for col, want in (filters or {}).items():
        have = str(row.get(col, "")).strip().casefold()
        wanted = want if isinstance(want, list) else [want]
        if have not in {str(w).strip().casefold() for w in wanted}:
            return False
    return True


def _oid(value: str) -> str:
    return "org-" + hashlib.sha256(value.strip().casefold().encode()).hexdigest()[:10]


def norm(text) -> str:
    """Name for matching across sources: no accents, no punctuation, lower case."""
    t = unicodedata.normalize("NFKD", str(text or "")).encode("ascii", "ignore").decode().lower()
    return " ".join(re.sub(r"[^a-z0-9]+", " ", t).split())


NAME_FILLER = {"de", "du", "des", "la", "le", "les", "et", "the", "of", "and", "von", "der", "die", "das", "und",
               "club", "sport", "sports", "association", "ville", "city", "fc", "ac", "as", "us", "sc", "e", "v"}


def name_words(name: str) -> list[str]:
    return [w for w in norm(name).split() if len(w) > 2 and w not in NAME_FILLER]


def common_words(names: list[str]) -> dict[str, int]:
    """How many organisation names of one pool carry each word: "paris", "centre", "sportif"
    say which city or kind of place, a rare word says which organisation."""
    seen: dict[str, int] = {}
    for n in names:
        for w in set(name_words(n)):
            seen[w] = seen.get(w, 0) + 1
    return seen


def mentions(name: str, text: str, counts: dict | None = None) -> bool:
    """Does this result talk about the organisation? Most of its distinctive name words are
    there - accents, hyphens and word order do not matter. With the pool's word counts, the
    name's rarest word must be among them: "Paris Saint Germain" on a page is not
    "HealthCity Paris Saint Germain"."""
    words = name_words(name)[:4]
    if not words:
        return bool(name) and norm(name) in norm(text)
    hay = " " + norm(text)

    def has(w: str) -> bool:   # at the start of a word: "eta" is not in "zeta" (plurals still match)
        return " " + w in hay
    if counts and not has(min(words, key=lambda w: counts.get(w, 0))):
        return False
    return sum(has(w) for w in words) >= max(1, round(len(words) * 0.6))


#: registry numbers that name one organisation or establishment, strongest first: two rows with
#: the same number are one organisation whatever their names; two different numbers never merge
REGISTRY = ("uai", "siret", "rna", "siren", "registry_id")


def registry_id(r: dict) -> str:
    for col in REGISTRY:
        v = re.sub(r"\s", "", str(r.get(col) or "")).upper()
        if v:
            return f"{col}:{v}"
    return ""


def place(r: dict) -> str:
    """The town for matching; a bare postcode is not one ("75011" and "Paris" are the same
    town: 744 clubs became two organisations each in 072)."""
    city = norm(r.get("city"))
    return "" if re.fullmatch(r"\d{4,5}", city or "x") else city


def venue_key(r: dict) -> str:
    name = norm(r.get("name"))
    place = norm(r.get("postcode") or r.get("zip") or r.get("city") or r.get("address", "")[:24])
    return f"v:{name}|{place}" if name else f"id:{r.get('id', '')}"


def org_key(r: dict) -> str:
    """An organisation is its registry number when it has one, else its name in its town
    (Bruce's rule: never the domain - one domain once merged 40 venues into 12, a shared portal
    or club platform would merge hundreds)."""
    return registry_id(r) or f"n:{norm(r.get('name'))}|{place(r)}"


def site_signal(r: dict) -> str:
    """The exact website (host + path) - a hint that two rows are one organisation, never a key."""
    url = str(r.get("website") or "").strip()
    if not url:
        return ""
    parts = urllib.parse.urlsplit(url if "://" in url else "https://" + url)
    host = (parts.hostname or "").lower().removeprefix("www.")
    return host + parts.path.rstrip("/") if host else ""


def sites_differ(a: str, b: str) -> bool:
    """Two website signals that cannot be one organisation: other hosts, or two different pages
    deeper than the home page on one host (a city portal's venue pages)."""
    if not a or not b:
        return False
    ha, _, pa = a.partition("/")
    hb, _, pb = b.partition("/")
    return ha != hb or bool(pa and pb and pa != pb)


def _same_org_names(a: dict, b: dict) -> bool:
    return mentions(str(a.get("name") or ""), str(b.get("name") or "")) or \
        mentions(str(b.get("name") or ""), str(a.get("name") or ""))


def new_oid(r: dict) -> str:
    return _oid(org_key(r) + "|" + site_signal(r))


def _merge_into(existing: list[dict], incoming: list[dict], key, source: str,
                by_name: bool = False, signal=None) -> tuple[int, int, dict]:
    """Union of rows: a row whose key is already there fills that row's empty fields and adds
    its source; anything else is a new row. by_name: when one side has no city, a single row
    with the same name is the same organisation. signal (organisations): two different
    websites keep same-named rows apart, the same exact website with matching names joins
    them. Returns (added, merged, key -> row)."""
    index, names, sites = {}, {}, {}
    by_id = {str(r.get("id") or ""): r for r in existing if r.get("id")}

    def live(r: dict) -> dict:
        """A row merged into another (orgs(merge=...)) answers for its keeper: pooling the same
        name again must not bring the dropped row back (074: one club, three rows)."""
        seen = 0
        while r.get("merged_into") and str(r["merged_into"]) in by_id and seen < 10:
            r, seen = by_id[str(r["merged_into"])], seen + 1
        return r
    for r in existing:
        index.setdefault(key(r), r)
        if key is org_key and registry_id(r):   # also findable by name: a source without the number
            index.setdefault(f"n:{norm(r.get('name'))}|{place(r)}", r)
        names.setdefault(norm(r.get("name")), []).append(r)
        if signal and signal(r):
            sites.setdefault(signal(r).partition("/")[0], []).append(r)

    def clash(a: dict, b: dict) -> bool:
        ra, rb = registry_id(a), registry_id(b)
        if ra and rb:
            return ra != rb
        return bool(signal) and sites_differ(signal(a), signal(b))
    added = merged = 0
    for r in incoming:
        k = key(r)
        hit = index.get(k)
        hit = live(hit) if hit is not None else None
        if hit is not None and clash(hit, r):
            hit, k = None, k + "|" + signal(r)
            hit = index.get(k)
        if hit is None and by_name:
            same = list({id(y): y for y in (live(x) for x in names.get(norm(r.get("name")), []))}.values())
            same = [x for x in same if not clash(x, r)]
            if len(same) == 1 and (not place(r) or not place(same[0]) or place(r) == place(same[0])):
                hit = same[0]
        if hit is None and signal and signal(r):
            host = signal(r).partition("/")[0]
            same_site = list({id(y): y for y in (live(x) for x in sites.get(host, []))}.values())
            same_site = [x for x in same_site if not clash(x, r) and _same_org_names(x, r)]
            if len(same_site) == 1:
                hit = same_site[0]
        if hit is not None:
            index[k] = hit
        if hit is None:
            r["sources"] = source
            existing.append(r)
            index[k] = r
            names.setdefault(norm(r.get("name")), []).append(r)
            if signal and signal(r):
                sites.setdefault(signal(r).partition("/")[0], []).append(r)
            added += 1
            continue
        for f, v in r.items():
            if v not in ("", None) and not hit.get(f):
                hit[f] = v
        srcs = [x for x in str(hit.get("sources") or hit.get("source") or "").split("|") if x]
        if source not in srcs:
            hit["sources"] = "|".join(srcs + [source])
        merged += 1
    return added, merged, {k: live(v) for k, v in index.items()}


SOURCE_COLS = ["time", "source", "kind", "query", "read", "kept", "venues_added", "venues_merged",
               "orgs_added", "orgs_merged", "contacts_from_rows", "provenance"]


def log_source(pdir: Path, source: str, kind: str, out: dict, query: str = "") -> None:
    """One line per source pooled: the data-source pool (what was tried, what it brought)."""
    ws.append_csv(pdir / "sources.csv", SOURCE_COLS, {
        "time": ws._now(), "source": source, "kind": kind, "query": query, "read": out.get("read", ""),
        "kept": out.get("kept", ""), "venues_added": out.get("added", ""),
        "venues_merged": out.get("merged_with_existing", ""), "orgs_added": out.get("orgs_added", ""),
        "orgs_merged": out.get("orgs_merged", ""), "contacts_from_rows": out.get("contacts_from_rows", ""),
        "provenance": out.get("provenance", "")})


def provenance(pdir: Path, file: str, source: str) -> str:
    """Where a pooled file came from: `tools` (downloaded or harvested in this project), `source`
    (an agent's own file for a list registered with its URL), else `brought-in` - a file from
    elsewhere, such as an earlier project's pool. Shown, never refused: the report says what a
    project started from."""
    if ws.made_here(pdir, file):
        return "tools"
    import mo_flow
    reg = next((r for r in mo_flow.source_pool(pdir) if r["source"].casefold() == (source or file).casefold()), None)
    return "source" if reg and reg.get("url") else "brought-in"


def pool(pdir: Path, file: str, into: str = "venues", filters: dict | None = None,
         map: dict | None = None, group_by: str = "", org_fields: dict | None = None,
         kind: str = "file", source: str = "", replace_operator: bool = False) -> dict:
    """source: the name of the list in the source pool (sources(...) / harvest(...)); the
    pooling is logged under it and the source moves to `pooled`."""
    # several agents may build pools at once: one writer per project for the pool files
    with ws._lock(pdir / "pools"):
        out = _pool(pdir, file, into, filters, map, group_by, org_fields, replace_operator, tag=source or file)
    if "error" not in out:
        out["provenance"] = provenance(pdir, file, source)
        if out["provenance"] == "brought-in":
            out["warning"] = ("this file was neither fetched nor harvested in this project, nor is its source "
                              "registered with a URL: register where it comes from (sources(add=[...])) - "
                              + ("this project is fresh (brief.fresh), so the report flags it as inherited"
                                 if ws.fresh(pdir) else "the report lists it as brought in"))
        log_source(pdir, source or file, kind, out, query=file if source else "")
        import mo_flow
        mo_flow.mark_source(pdir, source or file, "pooled")
        out["contacts_from_saved_pages"] = _bind_saved_pages(pdir, out.pop("_bind", []))
    return out


def _bind_saved_pages(pdir: Path, pairs: list[tuple]) -> int:
    """Rows that came with the page they were found on (a harvested detail page): that page is
    already saved, so its addresses join the organisation now, read from the copy - no new call;
    then the row's own addresses (an address the row printed that the page did not). Pages not
    saved in this project are left for the researchers."""
    saved = ws._pages(pdir)
    n = 0
    for oid, name, url, *rest in pairs:
        if url in saved:
            try:
                n += ws.fetch(pdir, url, org_id=oid, org=name, via="source").get("contacts_added", 0)
            except Exception:  # noqa: BLE001
                pass
        if rest and rest[0]:
            ws.merge_contacts(pdir, rest[0], oid, name, via="list")
    return n


ROW_CONTACT = ("email", "person", "role", "page_url", "quote")


def add_orgs(pdir: Path, rows: list[dict], source: str, kind: str, query: str = "") -> dict:
    """Organisations found by discovery or by an agent (maps, directory pages): merged into
    orgs.csv. A row that carries an address (email, with person / role / page_url / quote) gives
    it to that organisation as a contact, quoted from the row - what an agent read on a list
    reaches the contact pool without a file (073 smoke: a worker with no file tool lost them)."""
    with ws._lock(pdir / "pools"):
        orgs = ws.read_csv(pdir / "orgs.csv")
        extra = [{k: r.pop(k) for k in ROW_CONTACT if k in r} for r in rows]
        added, merged, index = _merge_into(orgs, rows, org_key, source, by_name=True, signal=site_signal)
        for o in orgs:
            if not o.get("id"):
                o["id"] = new_oid(o)
        _write(pdir / "orgs.csv", orgs, ["id", "name", "website", "city"])
    listed = 0
    for r, x in zip(rows, extra):
        hit = index.get(org_key(r)) or index.get(org_key(r) + "|" + site_signal(r))
        if hit is not None and x.get("email"):
            found = _row_contacts({**r, **x}, x, hit, x.get("page_url") or f"list:{source}")
            listed += ws.merge_contacts(pdir, found, hit["id"], hit.get("name", ""), via="list")
    out = {"read": len(rows), "kept": len(rows), "orgs_added": added, "orgs_merged": merged,
           "contacts_from_rows": listed}
    log_source(pdir, source, kind, out, query)
    return out


EMAIL_COLS = ("email", "emails", "mail", "courriel", "e_mail")


def _row_contacts(orig: dict, mapped: dict, org: dict, where: str) -> list[dict]:
    """The addresses a list prints on this organisation's own row, as contacts: the row is the
    record that ties them to it (quote = the row). Graded later like any address."""
    import mo_pages
    text = " ".join(str(v) for v in orig.values() if isinstance(v, (str, int, float)))
    found = [str(mapped.get(c) or "") for c in EMAIL_COLS] + mo_pages.EMAIL.findall(text)
    values = list(dict.fromkeys(e.strip().strip(".;,").lower() for f in found
                                for e in mo_pages.EMAIL.findall(f)))
    if not values:
        return []
    quote = str(mapped.get("quote") or "").strip()[:500] or " / ".join(dict.fromkeys(
        str(v).strip() for v in list(mapped.values()) + list(orig.values())
        if isinstance(v, (str, int, float)) and str(v).strip() and len(str(v)) < 200))[:500]
    name = str(org.get("name") or mapped.get("name") or "").strip()
    if name and not mentions(name, quote):
        # the grader reads the quote: a row's "label / address" alone names nobody (074: Jev gave X
        # to presidents' addresses until a worker re-graded them by hand)
        quote = f"{name} / {quote}"[:500]
    own = mo_pages.site_of(str(org.get("website") or ""))
    out = []
    for e in values:
        dom = mo_pages.site_of("http://" + e.split("@")[1])
        local = e.split("@")[0]
        out.append({"kind": "email", "value": e, "quote": quote, "quote_from": "block",
                    "page_url": str(mapped.get("page_url") or where), "page_site": "",
                    # the organisation's own row on a list: not "another site's page" (paris-all 0.9.0: Jev
                    # gave X to a thousand clubs' own mailboxes on the city's booking table)
                    "page_on_org_site": "row",
                    "email_site": dom, "same_site": "yes" if own and dom == own else "no",
                    "email_on_org": ("yes" if own and dom == own else "no") if own else "unknown",
                    "person": str(mapped.get("person") or ""), "role": str(mapped.get("role") or ""),
                    "mailbox": "generic" if re.split(r"[._-]", local)[0] in mo_pages.GENERIC else "named_or_role",
                    "found_by": "list"})
    return out


def _pool(pdir: Path, file: str, into: str, filters: dict | None, map: dict | None, group_by: str,
          org_fields: dict | None, replace_operator: bool = False, tag: str = "") -> dict:
    # rows carry the source's name from the source pool (not the saved file's name), so its
    # pilot, its scale or drop and the report all speak of the same source
    tag = tag or file
    src = (pdir / file).resolve()
    if pdir.resolve() not in src.parents:
        return {"error": "file: a path inside the project folder (e.g. pages/<file>.json)"}
    if into not in ("venues", "orgs"):
        return {"error": "into: venues or orgs"}
    raw = _rows(src)
    kept = [r for r in raw if _match(r, filters or {})]
    rows = [{t: r.get(s, "") for t, s in map.items()} if map else dict(r) for r in kept]
    # a row with no name is not a venue or an organisation (a listing page, a blank line): it
    # stays in the file, it does not enter the pool
    named = [(r, o) for r, o in zip(rows, kept) if str(r.get("name") or "").strip()]
    no_name = len(rows) - len(named)
    rows, kept = [r for r, _ in named], [o for _, o in named]
    orgs = ws.read_csv(pdir / "orgs.csv")
    o_added = o_merged = 0
    if into == "orgs":
        import mo_pages
        for r in rows:
            r.setdefault("id", "")
            # the list's own site is not its entry's website (074: a federation's club fiche pointing
            # home made the federation's whole site the club's - and its officers the club's A)
            w, pu = str(r.get("website") or ""), str(r.get("page_url") or "")
            root = lambda u: not urllib.parse.urlsplit(u if "://" in u else "https://" + u).path.strip("/")  # noqa: E731
            if w and pu and mo_pages.site_of(w) == mo_pages.site_of(pu) and root(w) and not root(pu):
                r["website"] = ""   # a fiche deep in a directory pointing at the directory's home
        o_added, o_merged, index = _merge_into(orgs, rows, org_key, tag, by_name=True, signal=site_signal)
        for o in orgs:
            if not o.get("id"):
                o["id"] = new_oid(o)
        _write(pdir / "orgs.csv", orgs, ["id", "name", "website", "city"])
        bind, listed = [], 0
        saved = ws._pages(pdir)
        # a page several rows came from is a list: its addresses are its rows', each on its own row
        # (074: a PDF of nine clubs, pooled as rows, gave two of them all nine mailboxes)
        per_page: dict[str, int] = {}
        for r in rows:
            if r.get("page_url"):
                per_page[r["page_url"]] = per_page.get(r["page_url"], 0) + 1
        for r, orig in zip(rows, kept):
            hit = index.get(org_key(r)) or index.get(org_key(r) + "|" + site_signal(r))
            if hit is None:
                continue
            # the row's own addresses always reach it (074: a saved page made pooling skip them); its
            # own detail page, already read, is bound first - its quotes are the page's blocks
            found = _row_contacts(orig, r, hit, f"list:{file}")
            if r.get("page_url") and r["page_url"] in saved and per_page.get(r["page_url"], 0) == 1:
                bind.append((hit["id"], hit.get("name", ""), r["page_url"], found))
            elif found:
                listed += ws.merge_contacts(pdir, found, hit["id"], hit.get("name", ""), via="list")
        return {"read": len(raw), "kept": len(kept), "skipped_no_name": no_name, "orgs_added": o_added,
                "orgs_merged": o_merged, "contacts_from_rows": listed,
                "columns_in_file": list(raw[0].keys())[:60] if raw else [], "_bind": bind}
    # venues, and with group_by the organisations behind them
    if group_by:
        groups = []
        for r, orig in zip(rows, kept):
            name = str(orig.get(group_by) or r.get(group_by) or "").strip()
            if not name:
                continue
            g = {"id": "", "name": name, "website": "", "city": ""}
            for target_col, source_col in (org_fields or {}).items():
                if orig.get(source_col):
                    g[target_col] = orig[source_col]
            r["_org"] = org_key(g)
            groups.append(g)
        o_added, o_merged, index = _merge_into(orgs, groups, org_key, tag, by_name=True, signal=site_signal)
        for o in orgs:
            if not o.get("id"):
                o["id"] = new_oid(o)
        for r in rows:
            if r.get("_org"):
                r["org_id"] = index[r.pop("_org")]["id"]
    venues = ws.read_csv(pdir / "venues.csv")
    v_added, v_merged, v_index = _merge_into(venues, rows, venue_key, tag)
    replaced = 0
    if group_by and replace_operator:
        # this source knows who runs the venue better than the one that pooled it first (an
        # owner's name, a council, where the operator is a contractor): its organisation wins,
        # the earlier one is kept as previous_org_id
        for r in rows:
            v = v_index.get(venue_key(r))
            if v is not None and r.get("org_id") and v.get("org_id") != r["org_id"]:
                v["previous_org_id"], v["org_id"] = v.get("org_id", ""), r["org_id"]
                replaced += 1
    emails_seen = sum(1 for o in kept if any(mo_pages_email(str(x)) for x in o.values()))
    counts: dict[str, int] = {}
    for v in venues:
        if v.get("org_id"):
            counts[v["org_id"]] = counts.get(v["org_id"], 0) + 1
    for o in orgs:
        if o.get("id") in counts:
            o["venues"] = str(counts[o["id"]])
    _write(pdir / "venues.csv", venues, ["id", "name"])
    if group_by:
        _write(pdir / "orgs.csv", orgs, ["id", "name", "website", "city", "venues"])
    return {"read": len(raw), "kept": len(kept), "skipped_no_name": no_name, "added": v_added,
            "merged_with_existing": v_merged, "operators_replaced": replaced,
            "orgs_added": o_added, "orgs_merged": o_merged,
            **({"rows_with_addresses": emails_seen,
                "addresses_note": "these rows print addresses; venues hold no contacts - pool the same file "
                                  "into='orgs' (name = the organisation the address belongs to) to give them to it"}
               if emails_seen else {}),
            "columns_in_file": list(raw[0].keys())[:60] if raw else [],
            "next": "every source goes into the same pools (union); duplicates across sources are merged "
                    "on name+place / website. Near-duplicates the names cannot catch: judge(...) them."}


def mo_pages_email(text: str) -> bool:
    import mo_pages
    return bool(mo_pages.EMAIL.search(text))


def _write(path: Path, rows: list[dict], first: list[str]) -> None:
    cols = list(dict.fromkeys(first + [k for r in rows for k in r if not k.startswith("_")]))
    ws.write_csv(path, cols, rows)
