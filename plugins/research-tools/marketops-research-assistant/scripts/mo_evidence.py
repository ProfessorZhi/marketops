"""How well is each contact backed? A simple, readable score - nothing is ever dropped for it.

Every address was read literally on a page; the score says how much more stands behind it.
Weak rows stay in the delivery marked "weak" (sorted after the strong ones of the same
grade); a person decides. The reasons are written next to the score.
"""
from __future__ import annotations

import re

import mo_pages
import mo_workspace as ws

LEVELS = ((70, "strong"), (45, "medium"), (0, "weak"))


def score(row: dict, org_site: str, shared: int) -> tuple[int, str, str]:
    pts, why = 30, ["literal on a page"]
    if row.get("quote_from") == "block":
        pts += 15
        why.append("in the same card/row as its text")
    else:
        pts += 5
    if row.get("page_on_org_site") == "yes":
        pts += 15
        why.append("on the organisation's own site")
    elif row.get("page_on_org_site") == "likely":
        pts += 10
        why.append("on a site carrying the organisation's name")
    if (org_site and row.get("email_site") == org_site) or row.get("email_on_org") == "yes":
        pts += 10
        why.append("address on the organisation's domain")
    elif row.get("email_on_org") == "likely":
        pts += 7
        why.append("address on a domain carrying the organisation's name")
    elif row.get("page_on_org_site") == "unknown" and row.get("email_on_org") == "unknown":
        pts -= 10
        why.append("neither page nor address on the organisation's own site")
    if row.get("person"):
        pts += 8
        why.append("person named")
    if row.get("role"):
        pts += 7
        why.append("role stated")
    readers = [r for r in row.get("found_by", "").split("+") if r]
    if len(readers) > 1:
        pts += min(10, 5 * (len(readers) - 1))
        why.append(f"read by {len(readers)} readers")
    seen = int(row.get("pages_seen") or 1)
    if seen > 1:
        pts += min(15, 5 * (seen - 1))
        why.append(f"seen on {seen} pages")
    m = re.search(r"conf (\d\.\d+)", row.get("grade_reason", ""))
    if m:
        pts += round(10 * (float(m.group(1)) - 0.5))
    if row.get("still_listed") == "no":
        pts -= 30
        why.append("no longer on its page")
    if shared > 0:
        pts -= 15
        why.append(f"also listed for {shared} other organisations")
    if row.get("graded_by", "").startswith("agent"):
        pts += 10
        why.append("checked by the agent")
    pts = max(0, min(100, pts))
    level = next(name for floor, name in LEVELS if pts >= floor)
    return pts, level, "; ".join(why)


def rescore(pdir) -> dict:
    """Write evidence_score / evidence / evidence_why on every email row."""
    path = pdir / "contacts.csv"
    sites = {str(o.get("id") or o.get("org_id") or ""): mo_pages.site_of(str(o.get("website") or ""))
             for o in ws.read_csv(pdir / "orgs.csv")}
    counts = {"strong": 0, "medium": 0, "weak": 0}
    with ws._lock(path):
        rows = ws.read_csv(path)
        holders: dict[str, set] = {}
        for r in rows:
            if r.get("kind") == "email":
                holders.setdefault(r["value"].lower(), set()).add(sites.get(r.get("org_id")) or r.get("org_id"))
        for r in rows:
            if r.get("kind") != "email":
                continue
            pts, level, why = score(r, sites.get(r.get("org_id"), ""), len(holders[r["value"].lower()]) - 1)
            r["evidence_score"], r["evidence"], r["evidence_why"] = str(pts), level, why
            counts[level] += 1
        ws.write_csv(path, ws.CONTACT_COLS, rows)
    return counts
