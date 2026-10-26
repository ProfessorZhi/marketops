"""The project as one Excel workbook for the salesperson."""
from __future__ import annotations

import csv
from datetime import datetime

import mo_budget
import mo_workspace as ws
from _xlsx import Sheet, render_workbook

ORDER = {"A": 0, "B": 1, "C": 2, "D": 3, "": 4, "X": 5}
LABEL = {"A": "A 关键岗位", "B": "B 相关操作人", "C": "C 业务入口", "D": "D 通用入口", "X": "X 排除", "": "未评级"}
# Bruce's qualified_email_routes.csv
ROUTE_CSV = ["account_id", "account_name", "product_profile", "email", "contact_name", "contact_title",
             "department", "evidence_tier", "role_priority", "decision_function", "role_relevance",
             "source_url", "mirror_path", "evidence_text", "qualification_rationale",
             "qualification_status", "research_only", "send_authorized"]
PRIORITY = {"A": "A_key_role", "B": "B_related_operator", "C": "C_official_business_entry",
            "D": "D_official_general_research_entry"}


def _tier(c: dict) -> str:
    g = c.get("grade")
    if g in ("A", "B"):
        return "strict_named_role_email" if c.get("person") or "named=yes" in c.get("grade_reason", "") \
            else "role_bound_public_email"
    return {"C": "observed_business_route", "D": "official_general_research_route"}.get(g, "inferred_candidate")


def metrics(orgs: list, emails: list) -> dict:
    """Headline numbers count confirmed A/B only; rows still marked review=yes are reported apart."""
    ids = [str(o.get("id") or o.get("org_id") or "") for o in orgs] or list({c.get("org_id") for c in emails})
    idset, n = set(ids), len(set(ids)) or 1
    mine = [c for c in emails if c.get("org_id") in idset]
    ab = [c for c in mine if c.get("grade") in ("A", "B") and c.get("review") != "yes"]
    pending = [c for c in mine if c.get("grade") in ("A", "B") and c.get("review") == "yes"]
    covered = {c.get("org_id") for c in mine if c.get("grade") in ("A", "B", "C", "D")}
    return {"orgs": len(idset), "ab_routes": len(ab), "ab_orgs": len({c["org_id"] for c in ab}),
            "ab_routes_per_100_orgs": round(100 * len(ab) / n, 1),
            "ab_org_share": round(100 * len({c["org_id"] for c in ab}) / n, 1),
            "coverage_abcd": round(100 * len(covered) / n, 1),
            "ab_to_review": len(pending),
            "ab_routes_per_100_orgs_if_review_holds": round(100 * (len(ab) + len(pending)) / n, 1)}


def _sheet(name, rows, cols, widths=()):
    return Sheet(name=name, headers=tuple(cols),
                 rows=tuple(tuple(str(r.get(c, "") or "") for c in cols) for r in rows),
                 widths=tuple(widths))


def export(pdir) -> dict:
    contacts = ws.read_csv(pdir / "contacts.csv")
    emails = [c for c in contacts if c.get("kind") == "email"]
    phones = {}
    for c in contacts:
        if c.get("kind") == "phone":
            phones.setdefault(c.get("org_id"), []).append(c["value"])
    orgs = ws.read_csv(pdir / "orgs.csv")
    venues = ws.read_csv(pdir / "venues.csv")
    crawl = {r["org_id"]: r for r in ws.read_csv(pdir / "crawl.csv")}

    best = {}
    for c in emails:
        g = c.get("grade", "")
        if c.get("org_id") not in best or ORDER.get(g, 4) < ORDER.get(best[c["org_id"]], 4):
            best[c.get("org_id")] = g

    route_rows = []
    for c in sorted(emails, key=lambda c: (ORDER.get(c.get("grade", ""), 4), c.get("org", ""))):
        route_rows.append({
            "机构": c.get("org"), "机构ID": c.get("org_id"), "等级": LABEL.get(c.get("grade", ""), c.get("grade")),
            "姓名": c.get("person"), "岗位": c.get("role"), "邮箱": c.get("value"),
            "机构电话": " / ".join(dict.fromkeys(phones.get(c.get("org_id"), [])))[:120],
            "邮箱类型": {"generic": "通用", "named_or_role": "个人或岗位"}.get(c.get("mailbox"), c.get("mailbox")),
            "同站": c.get("same_site"), "来源页面": c.get("page_url"), "原文引用": c.get("quote"),
            "读取来源": c.get("found_by"), "评级依据": c.get("grade_reason") or c.get("graded_by"),
            "待复核": "是" if c.get("review") == "yes" else "", "备注": c.get("note")})
    route_cols = ["机构", "机构ID", "等级", "待复核", "姓名", "岗位", "邮箱", "机构电话", "邮箱类型", "同站",
                  "来源页面", "原文引用", "读取来源", "评级依据", "备注"]

    org_cols = list(orgs[0].keys()) if orgs else ["id", "name", "website"]
    org_rows = []
    for o in orgs:
        oid = str(o.get("id") or o.get("org_id") or "")
        n = {g: sum(1 for c in emails if c.get("org_id") == oid and c.get("grade") == g) for g in "ABCD"}
        org_rows.append({**o, "最佳等级": LABEL.get(best.get(oid, ""), "") if oid in best else "未找到邮箱",
                         "A": n["A"], "B": n["B"], "C": n["C"], "D": n["D"],
                         "抓取状态": (crawl.get(oid) or {}).get("status", "未抓取"),
                         "抓取备注": (crawl.get(oid) or {}).get("note", "")})
    org_cols += ["最佳等级", "A", "B", "C", "D", "抓取状态", "抓取备注"]

    m = metrics(orgs, emails)
    total = m["orgs"]
    summary = [
        {"项目": "机构数", "值": total},
        {"项目": "场地数", "值": len(venues)},
        {"项目": "每百机构 A+B 路线数（A+B 条数 ÷ 机构数 × 100，Bruce 口径）", "值": m["ab_routes_per_100_orgs"]},
        {"项目": "有 A+B 的机构占比（有 A/B 的机构 ÷ 机构数）", "值": f"{m['ab_org_share']}%"},
        {"项目": "A+B 路线数 / 有 A+B 的机构数", "值": f"{m['ab_routes']} / {m['ab_orgs']}"},
        {"项目": "覆盖率（有任一 A–D 路线的机构 ÷ 机构数）", "值": f"{m['coverage_abcd']}%"},
        {"项目": "待复核的 A/B（低置信度、共用地址或来自他站；不计入上面的数）", "值": m["ab_to_review"]},
        {"项目": "若待复核全部成立：每百机构 A+B 路线数", "值": m["ab_routes_per_100_orgs_if_review_holds"]},
        {"项目": "邮箱总数（A/B/C/D/未评级/X）", "值": " / ".join(str(sum(c.get("grade", "") == g for c in emails)) for g in ("A", "B", "C", "D", "", "X"))},
        {"项目": "说明", "值": "仅供调研，未授权外联。每行都有来源页面和原文引用，可复核。"},
        {"项目": "导出时间", "值": datetime.now().strftime("%Y-%m-%d %H:%M")},
    ]
    batches = sorted({str(o.get("batch")) for o in orgs if o.get("batch")})
    batch_rows = []
    for b in batches:
        sub = [o for o in orgs if str(o.get("batch")) == b]
        ids = {str(o.get("id") or o.get("org_id") or "") for o in sub}
        bm = metrics(sub, [c for c in emails if c.get("org_id") in ids])
        batch_rows.append({"批次": b, "机构": bm["orgs"], "每百机构 A+B 路线": bm["ab_routes_per_100_orgs"],
                           "有 A+B 机构占比": f"{bm['ab_org_share']}%", "覆盖率": f"{bm['coverage_abcd']}%"})
    costs = [{"供应商": p, "调用": v["calls"], "成功": v["ok"], "估算美元": v["usd"]}
             for p, v in mo_budget.summary(pdir).items()]

    widths = (24, 10, 12, 18, 22, 30, 18, 10, 6, 40, 70, 18, 22, 20)
    kept = [r for r in route_rows if not r["等级"].startswith("X")]
    other = [r for r in route_rows if r["等级"].startswith("X")]
    sheets = [_sheet("说明", summary, ["项目", "值"], (28, 60)),
              _sheet("联系人", kept, route_cols, widths),
              _sheet("排除（X，可复核）", other, route_cols, widths),
              _sheet("机构", org_rows, org_cols)]
    if batch_rows:
        sheets.insert(1, _sheet("分批", batch_rows, list(batch_rows[0].keys())))
    if venues:
        sheets.append(_sheet("场地", venues, list(venues[0].keys())))
    sheets.append(_sheet("成本", costs, ["供应商", "调用", "成功", "估算美元"]))
    stamp = datetime.now().strftime('%Y%m%d-%H%M')
    out = pdir / f"{pdir.name}-{stamp}.xlsx"
    out.write_bytes(render_workbook(sheets))
    routes = _routes_csv(pdir, emails, stamp)
    return {"file": str(out), "routes_csv": str(routes), "contacts": len(route_rows), **m,
            "to_review": sum(c.get("review") == "yes" for c in emails)}


def _routes_csv(pdir, emails: list, stamp: str):
    """Bruce's 18-column route table (A-D only), one row per address and account."""
    b = ws.brief(pdir)
    mirror = {}
    for r in ws.read_csv(pdir / "pages.csv"):
        mirror.setdefault(r["url"], str(pdir / r["file"]))
    path = pdir / f"{pdir.name}-{stamp}-routes.csv"
    with open(path, "w", newline="", encoding="utf-8-sig") as fh:
        w = csv.DictWriter(fh, fieldnames=ROUTE_CSV)
        w.writeheader()
        for c in sorted(emails, key=lambda c: (ORDER.get(c.get("grade", ""), 4), c.get("org_id", ""))):
            if c.get("grade") not in PRIORITY:
                continue
            w.writerow({"account_id": c.get("org_id"), "account_name": c.get("org"),
                        "product_profile": b.get("product_profile") or b.get("goal", ""),
                        "email": c.get("value"), "contact_name": c.get("person"), "contact_title": c.get("role"),
                        "department": "", "evidence_tier": _tier(c), "role_priority": PRIORITY[c["grade"]],
                        "decision_function": c.get("role"), "role_relevance": c.get("grade_reason"),
                        "source_url": c.get("page_url"), "mirror_path": mirror.get(c.get("page_url"), ""),
                        "evidence_text": c.get("quote"), "qualification_rationale": c.get("grade_reason"),
                        "qualification_status": "needs_review" if c.get("review") == "yes" else "qualified",
                        "research_only": "TRUE", "send_authorized": "FALSE"})
    return path
