#!/usr/bin/env python3
"""Why does a project have few A/B routes? Counts only - no addresses, names or phones.

    python scripts/diagnose.py <project>
"""
from __future__ import annotations

import collections
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import mo_budget  # noqa: E402
import mo_providers  # noqa: E402
import mo_workspace as ws  # noqa: E402


def top(counter: collections.Counter, n: int = 12) -> list:
    return [[k, v] for k, v in counter.most_common(n)]


def main(project: str) -> dict:
    pdir = ws.project_dir(project)
    orgs = ws.read_csv(pdir / "orgs.csv")
    ids = {str(o.get("id") or "") for o in orgs}
    contacts = ws.read_contacts(pdir)
    emails = [c for c in contacts if c.get("kind") == "email"]
    crawl = ws.read_csv(pdir / "crawl.csv")
    final = {r["org_id"] for r in crawl if r.get("pass") == "final"}
    verdicts = ws.read_csv(pdir / "verdicts.csv")

    per_org = collections.Counter(c.get("org_id") for c in emails)
    holders: dict[str, set] = collections.defaultdict(set)
    for c in emails:
        holders[c["value"].lower()].add(c.get("org_id"))
    shared = [c for c in emails if len(holders[c["value"].lower()]) >= 3]
    site_of_page = collections.Counter(c.get("page_site") or "" for c in emails)
    ab = [c for c in emails if c.get("grade") in ("A", "B")]
    ab_ok = [c for c in ab if c.get("review") != "yes"]
    done_orgs = final & ids
    with_ab = {c["org_id"] for c in ab_ok} & ids
    with_any = {c["org_id"] for c in emails if c.get("grade") in ("A", "B", "C", "D")} & ids

    costs = collections.Counter()
    calls = collections.Counter()
    for line in (pdir / "costs.jsonl").read_text(encoding="utf-8").splitlines() if (pdir / "costs.jsonl").exists() else []:
        try:
            r = json.loads(line)
        except ValueError:
            continue
        key = f"{r.get('provider')}:{r.get('action')}:{'ok' if r.get('ok') else 'fail'}"
        calls[key] += 1
        costs[r.get("provider")] += float(r.get("usd") or 0)

    return {
        "version_capabilities": mo_providers.capabilities(),
        "jev_ready_now": mo_budget.jev_ready(),
        "organisations": {"total": len(ids), "pipeline_done": len(done_orgs), "with_confirmed_ab": len(with_ab),
                          "with_any_A_to_D": len(with_any), "agent_verdicts": len({v['org_id'] for v in verdicts}),
                          "no_website": sum(1 for o in orgs if not o.get("website")),
                          "by_source": top(collections.Counter(s for o in orgs for s in (o.get("sources") or "none").split("|")))},
        "site_pass": {"by_status": top(collections.Counter(r.get("status") for r in crawl if (r.get("pass") or "site") == "site")),
                      "pages_read_median": sorted(int(r.get("pages") or 0) for r in crawl if (r.get("pass") or "site") == "site")[
                          len([r for r in crawl if (r.get("pass") or "site") == "site"]) // 2] if crawl else 0},
        "emails": {"total": len(emails), "distinct_addresses": len(holders),
                   "orgs_with_any_email": len(per_org),
                   "per_org_max": max(per_org.values()) if per_org else 0,
                   "orgs_with_over_50": sum(1 for v in per_org.values() if v > 50),
                   "rows_on_addresses_listed_for_3plus_orgs": len(shared),
                   "by_grade": top(collections.Counter(c.get("grade") or "ungraded" for c in emails)),
                   "by_graded_by": top(collections.Counter(c.get("graded_by") or "-" for c in emails)),
                   "page_on_org_site": top(collections.Counter(c.get("page_on_org_site") or "-" for c in emails)),
                   "top_page_sites": top(site_of_page, 10)},
        "ab": {"rows": len(ab), "confirmed": len(ab_ok), "in_review": len(ab) - len(ab_ok),
               "review_reasons": top(collections.Counter((c.get("review_reason") or "")[:60] for c in ab if c.get("review") == "yes"), 8)},
        "x_reasons": top(collections.Counter((c.get("grade_reason") or "")[:60] for c in emails if c.get("grade") == "X"), 8),
        "costs_usd": {k: round(v, 2) for k, v in costs.items()},
        "calls": top(calls, 20),
        "brief_keys": sorted(ws.brief(pdir)),
        "address_rules": ws.brief(pdir).get("address_rules"),
    }


if __name__ == "__main__":
    print(json.dumps(main(sys.argv[1]), ensure_ascii=False, indent=1, default=str))
