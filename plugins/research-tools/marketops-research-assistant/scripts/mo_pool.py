"""Build the venue pool and the organisation pool from a saved data file (open-data export).

    pool(project, "pages/<file>.json", filters={"dep_code": "75", "equip_nature": "Intérieur"},
         map={"id": "equip_numero", "name": "equip_nom", "operator": "equip_gest_nom", ...},
         group_by="operator")

reads the file `fetch` saved (JSON list, {"results": [...]}, GeoJSON or CSV), keeps the rows
that match the filters, and adds them to venues.csv with the mapped column names; with
group_by, one organisation per distinct value of that column goes to orgs.csv (venues linked
by org_id). Every source is added to the same pools - the pools are the union of all sources:
the same venue (name + postcode/city) or organisation (website, else name + city) seen in
another source becomes one row, its empty fields filled from the new source, all sources
listed in `sources`. Nothing is dropped.
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
        sample = text[:5000]
        delim = ";" if sample.count(";") > sample.count(",") else ","
        return list(csv.DictReader(io.StringIO(text), delimiter=delim))
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
    hay = norm(text)
    if counts and min(words, key=lambda w: counts.get(w, 0)) not in hay:
        return False
    return sum(w in hay for w in words) >= max(1, round(len(words) * 0.6))


def venue_key(r: dict) -> str:
    name = norm(r.get("name"))
    place = norm(r.get("postcode") or r.get("zip") or r.get("city") or r.get("address", "")[:24])
    return f"v:{name}|{place}" if name else f"id:{r.get('id', '')}"


def org_key(r: dict) -> str:
    """An organisation is its name in its place (Bruce's rule: never the domain - one domain
    once merged 40 venues into 12, a shared portal or club platform would merge hundreds)."""
    return f"n:{norm(r.get('name'))}|{norm(r.get('city'))}"


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
    for r in existing:
        index.setdefault(key(r), r)
        names.setdefault(norm(r.get("name")), []).append(r)
        if signal and signal(r):
            sites.setdefault(signal(r).partition("/")[0], []).append(r)

    def clash(a: dict, b: dict) -> bool:
        return bool(signal) and sites_differ(signal(a), signal(b))
    added = merged = 0
    for r in incoming:
        k = key(r)
        hit = index.get(k)
        if hit is not None and clash(hit, r):
            hit, k = None, k + "|" + signal(r)
            hit = index.get(k)
        if hit is None and by_name:
            same = [x for x in names.get(norm(r.get("name")), []) if not clash(x, r)]
            if len(same) == 1 and (not r.get("city") or not same[0].get("city")
                                   or norm(r.get("city")) == norm(same[0].get("city"))):
                hit = same[0]
        if hit is None and signal and signal(r):
            host = signal(r).partition("/")[0]
            same_site = [x for x in sites.get(host, []) if not clash(x, r) and _same_org_names(x, r)]
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
    return added, merged, index


SOURCE_COLS = ["time", "source", "kind", "query", "read", "kept", "venues_added", "venues_merged",
               "orgs_added", "orgs_merged"]


def log_source(pdir: Path, source: str, kind: str, out: dict, query: str = "") -> None:
    """One line per source pooled: the data-source pool (what was tried, what it brought)."""
    ws.append_csv(pdir / "sources.csv", SOURCE_COLS, {
        "time": ws._now(), "source": source, "kind": kind, "query": query, "read": out.get("read", ""),
        "kept": out.get("kept", ""), "venues_added": out.get("added", ""),
        "venues_merged": out.get("merged_with_existing", ""), "orgs_added": out.get("orgs_added", ""),
        "orgs_merged": out.get("orgs_merged", "")})


def pool(pdir: Path, file: str, into: str = "venues", filters: dict | None = None,
         map: dict | None = None, group_by: str = "", org_fields: dict | None = None,
         kind: str = "file", source: str = "") -> dict:
    """source: the name of the list in the source pool (sources(...) / harvest(...)); the
    pooling is logged under it and the source moves to `pooled`."""
    # several agents may build pools at once: one writer per project for the pool files
    with ws._lock(pdir / "pools"):
        out = _pool(pdir, file, into, filters, map, group_by, org_fields)
    if "error" not in out:
        log_source(pdir, source or file, kind, out, query=file if source else "")
        import mo_flow
        mo_flow.mark_source(pdir, source or file, "pooled")
        out["contacts_from_saved_pages"] = _bind_saved_pages(pdir, out.pop("_bind", []))
    return out


def _bind_saved_pages(pdir: Path, pairs: list[tuple[str, str, str]]) -> int:
    """Rows that came with the page they were found on (a harvested detail page): that page is
    already saved, so its addresses join the organisation now, read from the copy - no new call.
    Pages not saved in this project are left for the researchers."""
    saved = ws._pages(pdir)
    n = 0
    for oid, name, url in pairs:
        if url in saved:
            try:
                n += ws.fetch(pdir, url, org_id=oid, org=name, via="source").get("contacts_added", 0)
            except Exception:  # noqa: BLE001
                continue
    return n


def add_orgs(pdir: Path, rows: list[dict], source: str, kind: str, query: str = "") -> dict:
    """Organisations found by discovery (maps, directory pages): merged into orgs.csv."""
    with ws._lock(pdir / "pools"):
        orgs = ws.read_csv(pdir / "orgs.csv")
        added, merged, _ = _merge_into(orgs, rows, org_key, source, by_name=True, signal=site_signal)
        for o in orgs:
            if not o.get("id"):
                o["id"] = new_oid(o)
        _write(pdir / "orgs.csv", orgs, ["id", "name", "website", "city"])
    out = {"read": len(rows), "kept": len(rows), "orgs_added": added, "orgs_merged": merged}
    log_source(pdir, source, kind, out, query)
    return out


def _pool(pdir: Path, file: str, into: str, filters: dict | None, map: dict | None, group_by: str,
          org_fields: dict | None) -> dict:
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
        for r in rows:
            r.setdefault("id", "")
        o_added, o_merged, index = _merge_into(orgs, rows, org_key, file, by_name=True, signal=site_signal)
        for o in orgs:
            if not o.get("id"):
                o["id"] = new_oid(o)
        _write(pdir / "orgs.csv", orgs, ["id", "name", "website", "city"])
        bind = []
        for r in rows:
            hit = index.get(org_key(r)) or index.get(org_key(r) + "|" + site_signal(r))
            if r.get("page_url") and hit is not None:
                bind.append((hit["id"], hit.get("name", ""), r["page_url"]))
        return {"read": len(raw), "kept": len(kept), "skipped_no_name": no_name, "orgs_added": o_added,
                "orgs_merged": o_merged, "columns_in_file": list(raw[0].keys())[:60] if raw else [], "_bind": bind}
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
        o_added, o_merged, index = _merge_into(orgs, groups, org_key, file, by_name=True, signal=site_signal)
        for o in orgs:
            if not o.get("id"):
                o["id"] = new_oid(o)
        for r in rows:
            if r.get("_org"):
                r["org_id"] = index[r.pop("_org")]["id"]
    venues = ws.read_csv(pdir / "venues.csv")
    v_added, v_merged, _ = _merge_into(venues, rows, venue_key, file)
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
            "merged_with_existing": v_merged,
            "orgs_added": o_added, "orgs_merged": o_merged,
            "columns_in_file": list(raw[0].keys())[:60] if raw else [],
            "next": "every source goes into the same pools (union); duplicates across sources are merged "
                    "on name+place / website. Near-duplicates the names cannot catch: judge(...) them."}


def _write(path: Path, rows: list[dict], first: list[str]) -> None:
    cols = list(dict.fromkeys(first + [k for r in rows for k in r if not k.startswith("_")]))
    ws.write_csv(path, cols, rows)
