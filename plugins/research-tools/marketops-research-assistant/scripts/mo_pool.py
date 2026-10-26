"""Build the venue pool and the organisation pool from a saved data file (open-data export).

    pool(project, "pages/<file>.json", filters={"dep_code": "75", "equip_nature": "Intérieur"},
         map={"id": "equip_numero", "name": "equip_nom", "operator": "equip_gest_nom", ...},
         group_by="operator")

reads the file `fetch` saved (JSON list, {"results": [...]}, GeoJSON or CSV), keeps the rows
that match the filters, writes them to venues.csv with the mapped column names, and - with
group_by - adds one organisation per distinct value of that column to orgs.csv (its venues
linked by org_id). Calling it again with another file or slice appends; nothing is dropped.
"""
from __future__ import annotations

import csv
import hashlib
import io
import json
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
    for r, orig in zip(rows, kept):
        r["source"] = file
        if group_by:
            key = str(orig.get(group_by) or r.get(group_by) or "").strip()
            r["org_id"] = _oid(key) if key else ""
    target = pdir / f"{into}.csv"
    existing = ws.read_csv(target)
    seen = {r.get("id") or json.dumps(r, sort_keys=True) for r in existing}
    added = [r for r in rows if (r.get("id") or json.dumps(r, sort_keys=True)) not in seen]
    cols = list(existing[0].keys()) if existing else (list(rows[0].keys()) if rows else ["id"])
    ws.write_csv(target, cols, existing + added)
    orgs_added = 0
    if group_by and into == "venues":
        orgs = ws.read_csv(pdir / "orgs.csv")
        have = {o.get("id") for o in orgs}
        groups: dict[str, dict] = {}
        fresh = {id(r) for r in added}
        for r, orig in zip(rows, kept):
            if id(r) not in fresh:
                continue
            key = str(orig.get(group_by) or r.get(group_by) or "").strip()
            if not key:
                continue
            g = groups.setdefault(_oid(key), {"id": _oid(key), "name": key, "website": "", "venues": 0})
            g["venues"] += 1
            for target_col, source_col in (org_fields or {}).items():
                if not g.get(target_col) and orig.get(source_col):
                    g[target_col] = orig[source_col]
        new = [g for oid, g in groups.items() if oid not in have]
        for g in groups.values():
            for o in orgs:
                if o.get("id") == g["id"]:
                    o["venues"] = str(int(o.get("venues") or 0) + g["venues"])
        cols = list(orgs[0].keys()) if orgs else ["id", "name", "website", "venues"]
        ws.write_csv(pdir / "orgs.csv", cols, orgs + new)
        orgs_added = len(new)
    return {"read": len(raw), "kept": len(kept), "added": len(added), "orgs_added": orgs_added,
            "columns_in_file": list(raw[0].keys())[:60] if raw else [],
            "next": "orgs without a website: search for them or let run find them (maps round)"}
