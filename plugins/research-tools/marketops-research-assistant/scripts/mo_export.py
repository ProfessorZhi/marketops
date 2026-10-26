"""The project as one Excel workbook for the salesperson."""
from __future__ import annotations

from datetime import datetime

import mo_budget
import mo_workspace as ws
from _xlsx import Sheet, render_workbook

ORDER = {"A": 0, "B": 1, "C": 2, "D": 3, "": 4, "X": 5}
LABEL = {"A": "A 关键岗位", "B": "B 相关操作人", "C": "C 业务入口", "D": "D 通用入口", "X": "X 排除", "": "未评级"}


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
            "备注": c.get("note")})
    route_cols = ["机构", "机构ID", "等级", "姓名", "岗位", "邮箱", "机构电话", "邮箱类型", "同站",
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

    total = len(orgs) or len({c.get("org_id") for c in emails})
    ab = {c.get("org_id") for c in emails if c.get("grade") in ("A", "B")}
    covered = {c.get("org_id") for c in emails if c.get("grade") in ("A", "B", "C", "D")}
    summary = [
        {"项目": "机构数", "值": total},
        {"项目": "场地数", "值": len(venues)},
        {"项目": "有 A/B 的机构", "值": len(ab)},
        {"项目": "每百机构 A+B 机构数", "值": round(100 * len(ab) / total, 1) if total else 0},
        {"项目": "A+B 路线数", "值": sum(c.get("grade") in ("A", "B") for c in emails)},
        {"项目": "覆盖率（有任一 A-D 路线）", "值": f"{round(100 * len(covered) / total, 1) if total else 0}%"},
        {"项目": "邮箱总数（A/B/C/D/未评级/X）", "值": " / ".join(str(sum(c.get("grade", "") == g for c in emails)) for g in ("A", "B", "C", "D", "", "X"))},
        {"项目": "说明", "值": "仅供调研，未授权外联。每行都有来源页面和原文引用，可复核。"},
        {"项目": "导出时间", "值": datetime.now().strftime("%Y-%m-%d %H:%M")},
    ]
    costs = [{"供应商": p, "调用": v["calls"], "成功": v["ok"], "估算美元": v["usd"]}
             for p, v in mo_budget.summary(pdir).items()]

    widths = (24, 10, 12, 18, 22, 30, 18, 10, 6, 40, 70, 18, 22, 20)
    kept = [r for r in route_rows if not r["等级"].startswith("X")]
    other = [r for r in route_rows if r["等级"].startswith("X")]
    sheets = [_sheet("说明", summary, ["项目", "值"], (28, 60)),
              _sheet("联系人", kept, route_cols, widths),
              _sheet("排除（X，可复核）", other, route_cols, widths),
              _sheet("机构", org_rows, org_cols)]
    if venues:
        sheets.append(_sheet("场地", venues, list(venues[0].keys())))
    sheets.append(_sheet("成本", costs, ["供应商", "调用", "成功", "估算美元"]))
    out = pdir / f"{pdir.name}-{datetime.now().strftime('%Y%m%d-%H%M')}.xlsx"
    out.write_bytes(render_workbook(sheets))
    return {"file": str(out), "contacts": len(route_rows), "orgs": total,
            "ab_orgs_per_100": round(100 * len(ab) / total, 1) if total else 0,
            "coverage": round(100 * len(covered) / total, 1) if total else 0}
