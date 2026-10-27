#!/usr/bin/env python3
"""Why a project has few A/B routes: counts only, offline.

Reads the project's CSV files directly with the standard library. It does not import the
plugin, does not open the workspace (no mkdir, no migration), does not read balances and
writes nothing. Output never contains an address, name, phone, URL, host or rule string.

    python scripts/diagnose_offline.py <project_dir> [--json]
"""
from __future__ import annotations

import csv
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path


def _rows(path: Path) -> list[dict]:
    try:
        with path.open(encoding="utf-8", newline="") as fh:
            return list(csv.DictReader(fh))
    except OSError:
        return []


def _pct(a: int, b: int) -> str:
    return f"{100.0 * a / b:.2f}%" if b else "n/a"


def collect(pdir: Path) -> dict:
    orgs = _rows(pdir / "orgs.csv")
    contacts = _rows(pdir / "contacts.csv")
    crawl = _rows(pdir / "crawl.csv")
    costs = []
    try:
        costs = [json.loads(x) for x in
                 (pdir / "costs.jsonl").read_text(encoding="utf-8").splitlines() if x.strip()]
    except OSError:
        pass

    out: dict = {}
    org_ids = {o.get("id") for o in orgs}
    out["orgs_rows"] = len(orgs)
    out["orgs_unique_ids"] = len(org_ids)
    out["orgs_no_website"] = sum(1 for o in orgs if not (o.get("website") or "").strip())

    email_rows = [r for r in contacts if (r.get("kind") or "email") == "email"]
    out["contact_rows"] = len(contacts)
    out["email_rows"] = len(email_rows)
    out["unique_org_email_pairs"] = len({(r.get("org_id"), (r.get("value") or "").lower())
                                         for r in email_rows})
    by_addr: dict[str, set] = defaultdict(set)
    for r in email_rows:
        by_addr[(r.get("value") or "").lower()].add(r.get("org_id"))
    out["distinct_addresses"] = len(by_addr)
    shared3 = {a for a, s in by_addr.items() if len(s) >= 3}
    rows3 = sum(1 for r in email_rows if (r.get("value") or "").lower() in shared3)
    out["addresses_on_ge3_orgs"] = len(shared3)
    out["rows_on_ge3_orgs"] = rows3
    out["rows_on_ge3_orgs_share"] = _pct(rows3, len(email_rows))
    out["addresses_on_ge10_orgs"] = sum(1 for s in by_addr.values() if len(s) >= 10)
    out["max_orgs_per_address"] = max((len(s) for s in by_addr.values()), default=0)

    out["rows_by_stage"] = dict(Counter(r.get("found_in") or "?" for r in email_rows).most_common())
    out["shared_ge3_rows_by_stage"] = dict(Counter(
        r.get("found_in") or "?" for r in email_rows
        if (r.get("value") or "").lower() in shared3).most_common())
    out["page_on_org_site"] = dict(Counter(r.get("page_on_org_site") or "?" for r in email_rows))
    out["email_on_org"] = dict(Counter(r.get("email_on_org") or "?" for r in email_rows))

    out["grades"] = dict(Counter(r.get("grade") or "none" for r in contacts))
    out["graded_by"] = dict(Counter(r.get("graded_by") or "none" for r in contacts))
    reasons = Counter()
    for r in contacts:
        if r.get("grade") != "X":
            continue
        why = (r.get("grade_reason") or "").lower()
        reasons["jev" if why.startswith("jev") else
                "address_rule" if "address rule" in why else
                "other_org_page" if "not this organisation" in why else "other"] += 1
    out["x_reasons"] = dict(reasons)

    ab = [r for r in contacts if r.get("grade") in ("A", "B")]
    ab_orgs = {r.get("org_id") for r in ab}
    out["ab_rows"] = len(ab)
    out["ab_distinct_orgs"] = len(ab_orgs)
    out["ab_named_rows"] = sum(1 for r in ab if (r.get("person") or "").strip())
    out["ab_role_empty"] = sum(1 for r in ab if not (r.get("role") or "").strip())
    out["ab_person_and_role_empty"] = sum(1 for r in ab if not (r.get("person") or "").strip()
                                          and not (r.get("role") or "").strip())
    out["ab_review_yes"] = sum(1 for r in ab if r.get("review") == "yes")
    out["ab_not_in_review"] = sum(1 for r in ab if r.get("review") != "yes")

    per_org = Counter(r.get("org_id") for r in email_rows)
    out["max_rows_per_org"] = max(per_org.values(), default=0)
    out["orgs_with_gt50_rows"] = sum(1 for v in per_org.values() if v > 50)
    out["orgs_with_gt100_rows"] = sum(1 for v in per_org.values() if v > 100)
    per_site: dict[str, set] = defaultdict(set)
    per_site_rows = Counter()
    for r in email_rows:
        per_site[r.get("page_site") or "?"].add(r.get("org_id"))
        per_site_rows[r.get("page_site") or "?"] += 1
    out["distinct_page_sites"] = len(per_site)
    out["page_sites_on_ge3_orgs"] = sum(1 for s in per_site.values() if len(s) >= 3)
    out["max_orgs_per_page_site"] = max((len(s) for s in per_site.values()), default=0)
    out["max_rows_per_page_site"] = max(per_site_rows.values(), default=0)

    out["orgs_by_source"] = dict(Counter((o.get("source") or "?").split(" (")[0]
                                         for o in orgs).most_common(12))
    fam_ab = Counter((o.get("source") or "?").split(" (")[0] for o in orgs if o.get("id") in ab_orgs)
    out["ab_orgs_by_source"] = dict(fam_ab.most_common(12))

    out["crawl_rows"] = len(crawl)
    out["crawl_passes"] = dict(Counter(r.get("pass") or "?" for r in crawl).most_common())
    out["crawl_status"] = dict(Counter(r.get("status") or "?" for r in crawl).most_common())
    fin = {r.get("org_id") for r in crawl if r.get("pass") == "final"}
    out["final_distinct_orgs"] = len(fin)
    out["ab_org_share_all"] = _pct(len(ab_orgs & org_ids), len(org_ids))
    out["ab_org_share_final"] = _pct(len(ab_orgs & fin), len(fin))

    usd = Counter()
    for c in costs:
        usd[c.get("provider")] += float(c.get("usd") or 0)
    out["cost_usd_total"] = round(sum(usd.values()), 4)
    out["cost_usd_by_provider"] = {k: round(v, 4) for k, v in usd.items()}
    return out


def main() -> int:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    pdir = Path(args[0] if args else "./").resolve()
    data = collect(pdir)
    if "--json" in sys.argv:
        print(json.dumps(data, ensure_ascii=False, indent=1))
        return 0
    for key, value in data.items():
        print(f"{key}: {json.dumps(value, ensure_ascii=False) if isinstance(value, dict) else value}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
