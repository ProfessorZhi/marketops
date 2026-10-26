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
from pathlib import Path

import mo_pages
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


def venue_key(r: dict) -> str:
    name = norm(r.get("name"))
    place = norm(r.get("postcode") or r.get("zip") or r.get("city") or r.get("address", "")[:24])
    return f"v:{name}|{place}" if name else f"id:{r.get('id', '')}"


def org_key(r: dict) -> str:
    site = mo_pages.site_of(str(r.get("website") or "")) if r.get("website") else ""
    if site:
        return f"d:{site}"
    return f"n:{norm(r.get('name'))}|{norm(r.get('city'))}"


def _merge_into(existing: list[dict], incoming: list[dict], key, source: str,
                by_name: bool = False) -> tuple[int, int, dict]:
    """Union of rows: a row whose key is already there fills that row's empty fields and adds
    its source; anything else is a new row. by_name: when one side has no city (or no
    website), a single row with the same name is the same organisation. Returns (added,
    merged, key -> row)."""
    index, names = {}, {}
    for r in existing:
        index.setdefault(key(r), r)
        names.setdefault(norm(r.get("name")), []).append(r)
    added = merged = 0
    for r in incoming:
        k = key(r)
        hit = index.get(k)
        if hit is None and by_name:
            same = names.get(norm(r.get("name")), [])
            if len(same) == 1 and (not r.get("city") or not same[0].get("city")
                                   or norm(r.get("city")) == norm(same[0].get("city"))):
                hit = same[0]
                index[k] = hit
        if hit is None:
            r["sources"] = source
            existing.append(r)
            index[k] = r
            names.setdefault(norm(r.get("name")), []).append(r)
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


def pool(pdir: Path, file: str, into: str = "venues", filters: dict | None = None,
         map: dict | None = None, group_by: str = "", org_fields: dict | None = None) -> dict:
    # several agents may build pools at once: one writer per project for the pool files
    with ws._lock(pdir / "pools"):
        return _pool(pdir, file, into, filters, map, group_by, org_fields)


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
    orgs = ws.read_csv(pdir / "orgs.csv")
    o_added = o_merged = 0
    if into == "orgs":
        for r in rows:
            r.setdefault("id", "")
        o_added, o_merged, _ = _merge_into(orgs, rows, org_key, file, by_name=True)
        for o in orgs:
            if not o.get("id"):
                o["id"] = _oid(org_key(o))
        _write(pdir / "orgs.csv", orgs, ["id", "name", "website", "city"])
        return {"read": len(raw), "kept": len(kept), "orgs_added": o_added, "orgs_merged": o_merged,
                "columns_in_file": list(raw[0].keys())[:60] if raw else []}
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
        o_added, o_merged, index = _merge_into(orgs, groups, org_key, file, by_name=True)
        for o in orgs:
            if not o.get("id"):
                o["id"] = _oid(org_key(o))
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
    return {"read": len(raw), "kept": len(kept), "added": v_added, "merged_with_existing": v_merged,
            "orgs_added": o_added, "orgs_merged": o_merged,
            "columns_in_file": list(raw[0].keys())[:60] if raw else [],
            "next": "every source goes into the same pools (union); duplicates across sources are merged "
                    "on name+place / website. Near-duplicates the names cannot catch: judge(...) them."}


def _write(path: Path, rows: list[dict], first: list[str]) -> None:
    cols = list(dict.fromkeys(first + [k for r in rows for k in r if not k.startswith("_")]))
    ws.write_csv(path, cols, rows)
