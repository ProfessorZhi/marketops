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
    ab, seen_people = [], set()
    for c in mine:
        if c.get("grade") in ("A", "B") and c.get("review") != "yes":
            who = (c.get("org_id"), " ".join(c.get("person", "").lower().split()))
            if who[1] and who in seen_people:
                continue  # one person with two addresses is one route
            seen_people.add(who)
            ab.append(c)
    per_org: dict = {}
    for c in ab:
        per_org[c["org_id"]] = per_org.get(c["org_id"], 0) + 1
    pending = [c for c in mine if c.get("grade") in ("A", "B") and c.get("review") == "yes"]
    covered = {c.get("org_id") for c in mine if c.get("grade") in ("A", "B", "C", "D")}
    return {"orgs": len(idset), "ab_routes": len(ab), "ab_orgs": len({c["org_id"] for c in ab}),
            "ab_org_share": round(100 * len({c["org_id"] for c in ab}) / n, 1),
            "ab_routes_per_100_orgs": round(100 * len(ab) / n, 1),
            "ab_routes_per_100_orgs_max3": round(100 * sum(min(3, k) for k in per_org.values()) / n, 1),
            "coverage_abcd": round(100 * len(covered) / n, 1),
            "ab_to_review": len(pending),
            "ab_routes_per_100_orgs_if_review_holds": round(100 * (len(ab) + len(pending)) / n, 1)}


def _sheet(name, rows, cols, widths=()):
    return Sheet(name=name, headers=tuple(cols),
                 rows=tuple(tuple(str(r.get(c, "") or "") for c in cols) for r in rows),
                 widths=tuple(widths))


def _reconcile(org_rows: list, total: int) -> str:
    """Bruce could never tell which of three numbers to trust: every organisation is counted once,
    under its best grade, and the parts must add up to the total."""
    parts = [sum(1 for r in org_rows if str(r.get("最佳等级", "")).startswith(g)) for g in "ABCD"]
    none = sum(1 for r in org_rows if r.get("最佳等级") == "未找到")
    unread = sum(1 for r in org_rows if r.get("最佳等级") == "未找到" and str(r.get("抓取状态", "")).startswith("未抓取"))
    ok = "对得上" if sum(parts) + none == total else "对不上，请检查"
    return " + ".join(map(str, parts)) + f" + {none}（{unread}） = {sum(parts) + none} / {total}，{ok}"


def trace_summary(pdir) -> list[dict]:
    """trace.jsonl, summed: per source and per stage, how long organisations spent and how
    often the stage was where an organisation's first A/B appeared."""
    path = pdir / "trace.jsonl"
    if not path.exists():
        return []
    import json
    traces = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            traces.append(json.loads(line))
        except ValueError:
            continue
    groups: dict[tuple, dict] = {}
    for t in traces:
        first_ab = next((st for st, ok in (t.get("ab") or {}).items() if ok), "")
        for src in ("全部", t.get("source") or "-"):
            for stage, sec in (t.get("seconds") or {}).items():
                g = groups.setdefault((src, stage), {"orgs": 0, "sec": 0.0, "first_ab": 0})
                g["orgs"] += 1
                g["sec"] += sec
                g["first_ab"] += first_ab == stage
    return [{"来源": src, "阶段": stage, "机构数": g["orgs"], "平均秒/机构": round(g["sec"] / g["orgs"], 1),
             "合计分钟": round(g["sec"] / 60, 1), "首个 A+B 在此阶段": g["first_ab"]}
            for (src, stage), g in sorted(groups.items(), key=lambda kv: (kv[0][0] != "全部", kv[0]))]


def export(pdir) -> dict:
    import mo_evidence
    mo_evidence.rescore(pdir)
    ws.snapshot_contacts(pdir)
    contacts = ws.read_contacts(pdir)
    emails = [c for c in contacts if c.get("kind") == "email"]
    phones = {}
    for c in contacts + ws.read_csv(pdir / "phones.csv"):
        if c.get("kind", "phone") == "phone":
            phones.setdefault(c.get("org_id"), []).append(c["value"])
    orgs = ws.read_csv(pdir / "orgs.csv")
    venues = ws.read_csv(pdir / "venues.csv")
    org_venues: dict[str, list[str]] = {}
    for v in venues:
        if v.get("org_id"):
            org_venues.setdefault(v["org_id"], []).append(str(v.get("name") or v.get("id") or ""))

    def venue_text(oid: str) -> str:
        names = org_venues.get(oid, [])
        return "; ".join(names[:5]) + (f" 等 {len(names)} 处" if len(names) > 5 else "")
    crawl_rows = ws.read_csv(pdir / "crawl.csv")
    crawl = {r["org_id"]: r for r in crawl_rows if (r.get("pass") or "site") == "site"}
    dug: dict[str, int] = {}
    for r in crawl_rows:
        if (r.get("pass") or "").startswith("deep"):
            dug[r["org_id"]] = dug.get(r["org_id"], 0) + 1

    best = {}
    for c in emails:
        g = c.get("grade", "")
        if c.get("org_id") not in best or ORDER.get(g, 4) < ORDER.get(best[c["org_id"]], 4):
            best[c.get("org_id")] = g

    route_rows = []
    for c in sorted(emails, key=lambda c: (ORDER.get(c.get("grade", ""), 4), -int(c.get("evidence_score") or 0),
                                           c.get("org", ""))):
        route_rows.append({
            "机构": c.get("org"), "机构ID": c.get("org_id"), "名下场地": venue_text(c.get("org_id", "")),
            "等级": LABEL.get(c.get("grade", ""), c.get("grade")),
            "姓名": c.get("person"), "岗位": c.get("role"), "邮箱": c.get("value"),
            "机构电话": " / ".join(dict.fromkeys(phones.get(c.get("org_id"), [])))[:120],
            "邮箱类型": {"generic": "通用", "named_or_role": "个人或岗位"}.get(c.get("mailbox"), c.get("mailbox")),
            "同站": c.get("same_site"), "来源页面": c.get("page_url"), "原文引用": c.get("quote"),
            "读取来源": c.get("found_by"), "评级依据": c.get("grade_reason") or c.get("graded_by"),
            "待复核": c.get("review_reason") or ("是" if c.get("review") == "yes" else ""),
            "证据": {"strong": "强", "medium": "中", "weak": "弱（证据少，人工判断）"}.get(c.get("evidence"), "") +
                    (f" {c.get('evidence_score')}" if c.get("evidence_score") else ""),
            "证据说明": c.get("evidence_why"), "也出现在": c.get("also_on"),
            "最后核实": (c.get("last_verified") or "")[:10] + (" 页面上已找不到" if c.get("still_listed") == "no" else ""),
            "首次发现": (c.get("added") or "")[:10], "备注": c.get("note")})
    route_cols = ["机构", "机构ID", "名下场地", "等级", "证据", "待复核", "姓名", "岗位", "邮箱", "机构电话", "邮箱类型", "同站",
                  "来源页面", "原文引用", "读取来源", "评级依据", "证据说明", "也出现在", "最后核实", "首次发现", "备注"]

    org_cols = list(orgs[0].keys()) if orgs else ["id", "name", "website"]
    b = ws.brief(pdir)
    brief_country, brief_scenario = str(b.get("country") or ""), str(b.get("scenario") or "")
    order_key = lambda c: (ORDER.get(c.get("grade", ""), 4), c.get("review") == "yes",  # noqa: E731
                           -int(c.get("evidence_score") or 0))
    by_org: dict[str, list[dict]] = {}
    for c in emails:
        by_org.setdefault(c.get("org_id"), []).append(c)
    org_rows = []
    for o in orgs:
        oid = str(o.get("id") or o.get("org_id") or "")
        mine = sorted(by_org.get(oid, []), key=order_key)
        top = next((c for c in mine if c.get("grade") in ("A", "B", "C", "D")), None)
        n = {g: sum(1 for c in mine if c.get("grade") == g) for g in "ABCD"}
        cr = crawl.get(oid) or {}
        if top:
            why_none = ""
        elif not o.get("website") and not mine:
            why_none = "没有官网，深挖也未找到邮箱"
        elif cr.get("status") == "unreadable":
            why_none = "官网打不开：" + (cr.get("note") or "")
        elif mine:
            why_none = "只有排除（X）或未评级的地址"
        else:
            why_none = "页面上没有公开邮箱" + (("；" + cr.get("note")) if cr.get("note") else "")
        # Bruce: every delivered row names its country, region and scenario so the next team can
        # split the list without looking anything up; a country is never left unidentified.
        where = {"国家": o.get("country") or brief_country,
                 "地区": o.get("region") or o.get("state") or o.get("county") or o.get("city") or "",
                 "场景": o.get("scenario") or brief_scenario or o.get("category") or ""}
        n_ab = sum(c.get("grade") in ("A", "B") and c.get("review") != "yes" for c in mine)
        org_rows.append({**where, **o, "最佳等级": LABEL.get(top.get("grade", ""), "") if top else "未找到",
                         "A/B 路线数": n_ab,
                         "首选联系人": (top or {}).get("person", ""), "首选岗位": (top or {}).get("role", ""),
                         "首选邮箱": (top or {}).get("value", ""),
                         "首选证据": ({"strong": "强", "medium": "中", "weak": "弱"}.get((top or {}).get("evidence"), "")
                                    + (" 待复核" if (top or {}).get("review") == "yes" else "")),
                         "首选来源页面": (top or {}).get("page_url", ""),
                         "机构电话": " / ".join(dict.fromkeys(phones.get(oid, [])))[:120],
                         "A": n["A"], "B": n["B"], "C": n["C"], "D": n["D"],
                         "名下场地": venue_text(oid), "未找到原因": why_none,
                         "抓取状态": {"done": "官网已读", "no_website": "输入无官网", "unreadable": "官网打不开",
                                  "no_dns": "官网域名已失效"}.get(
                             cr.get("status", ""), "未抓取") + (f"，深挖 {dug[oid]} 轮" if dug.get(oid) else "")})
    org_cols = ["国家", "地区", "场景"] + [c for c in org_cols if c not in ("国家", "地区", "场景")]
    org_cols += ["最佳等级", "A/B 路线数", "首选联系人", "首选岗位", "首选邮箱", "首选证据", "首选来源页面", "机构电话",
                 "A", "B", "C", "D", "名下场地", "未找到原因", "抓取状态"]

    m = metrics(orgs, emails)
    total = m["orgs"]
    summary = [
        {"项目": "机构数", "值": total},
        {"项目": "场地数", "值": len(venues)},
        {"项目": "广度：有 A+B 的机构占比（有 A/B 的机构 ÷ 机构数）", "值": f"{m['ab_org_share']}%"},
        {"项目": "每百机构 A+B 路线数（A+B 条数 ÷ 机构数 × 100，Bruce 口径；同一人两地址计一条）", "值": m["ab_routes_per_100_orgs"]},
        {"项目": "每百机构 A+B 路线数，每机构最多计 3 条", "值": m["ab_routes_per_100_orgs_max3"]},
        {"项目": "A+B 路线数 / 有 A+B 的机构数", "值": f"{m['ab_routes']} / {m['ab_orgs']}"},
        {"项目": "覆盖率（有任一 A–D 路线的机构 ÷ 机构数）", "值": f"{m['coverage_abcd']}%"},
        {"项目": "待复核的 A/B（低置信度、共用地址或来自他站；不计入上面的数）", "值": m["ab_to_review"]},
        {"项目": "若待复核全部成立：每百机构 A+B 路线数", "值": m["ab_routes_per_100_orgs_if_review_holds"]},
        {"项目": "对账：机构按最佳等级 A / B / C / D / 未找到（其中未抓取） = 机构数", "值": _reconcile(org_rows, total)},
        {"项目": "国家未识别的机构（应补齐，不得交付为未识别）", "值": sum(1 for r in org_rows if not r.get("国家"))},
        {"项目": "邮箱总数（A/B/C/D/未评级/X）", "值": " / ".join(str(sum(c.get("grade", "") == g for c in emails)) for g in ("A", "B", "C", "D", "", "X"))},
        {"项目": "证据强/中/弱（邮箱数；弱的也保留，由人判断）", "值": " / ".join(str(sum(c.get("evidence") == lv for c in emails)) for lv in ("strong", "medium", "weak"))},
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

    widths = (24, 10, 30, 12, 12, 18, 18, 22, 30, 18, 10, 6, 40, 70, 18, 22, 30, 30, 14, 12, 20)
    kept = [r for r in route_rows if not r["等级"].startswith("X")]
    other = [r for r in route_rows if r["等级"].startswith("X")]
    sheets = [_sheet("说明", summary, ["项目", "值"], (28, 60)),
              _sheet("机构（一家一行，带首选联系人）", org_rows, org_cols),
              _sheet("联系人", kept, route_cols, widths),
              _sheet("排除（X，可复核）", other, route_cols, widths)]
    if batch_rows:
        sheets.insert(1, _sheet("分批", batch_rows, list(batch_rows[0].keys())))
    src_rows, contrib_rows = _sources_and_contribution(pdir, orgs, emails)
    if src_rows:
        sheets.append(_sheet("数据源", src_rows, list(src_rows[0].keys()), (50, 12, 10, 10, 10, 10, 12)))
    if contrib_rows:
        sheets.append(_sheet("贡献（消融）", contrib_rows, ["维度", "项", "A+B 路线", "独有 A+B 路线",
                                                         "首个 A+B 来自这里的机构"], (10, 30, 12, 14, 22)))
    trace_rows = trace_summary(pdir)
    if trace_rows:
        sheets.append(_sheet("过程（耗时）", trace_rows, list(trace_rows[0].keys()), (12, 24, 10, 12, 12, 14)))
    if venues:
        sheets.append(_sheet("场地", venues, list(venues[0].keys())))
    sheets.append(_sheet("成本", costs, ["供应商", "调用", "成功", "估算美元"]))
    stamp = datetime.now().strftime('%Y%m%d-%H%M')
    out = pdir / f"{pdir.name}-{stamp}.xlsx"
    out.write_bytes(render_workbook(sheets))
    routes = _routes_csv(pdir, emails, stamp)
    return {"file": str(out), "routes_csv": str(routes), "contacts": len(route_rows), **m,
            "to_review": sum(c.get("review") == "yes" for c in emails)}


def _sources_and_contribution(pdir, orgs: list, emails: list) -> tuple[list, list]:
    """What each data source, stage and reader brought - read straight off one run, so a stage
    whose share stays near zero is a candidate to drop (ablation without re-running)."""
    confirmed = [c for c in emails if c.get("grade") in ("A", "B") and c.get("review") != "yes"]
    ab_orgs = {c["org_id"] for c in confirmed}
    log = ws.read_csv(pdir / "sources.csv")
    by_source: dict[str, dict] = {}
    for r in log:
        d = by_source.setdefault(r["source"], {"来源": r["source"], "类型": r.get("kind", ""), "机构": 0,
                                               "有 A+B 的机构": 0, "新增机构": 0, "合并机构": 0, "新增场地": 0})
        for k, f in (("新增机构", "orgs_added"), ("合并机构", "orgs_merged"), ("新增场地", "venues_added")):
            try:
                d[k] += int(r.get(f) or 0)
            except ValueError:
                pass
    for o in orgs:
        oid = str(o.get("id") or o.get("org_id") or "")
        for src in [x for x in str(o.get("sources") or "").split("|") if x]:
            d = by_source.setdefault(src, {"来源": src, "类型": "", "机构": 0, "有 A+B 的机构": 0, "新增机构": 0,
                                           "合并机构": 0, "新增场地": 0})
            d["机构"] += 1
            d["有 A+B 的机构"] += oid in ab_orgs
    src_rows = []
    for d in by_source.values():
        d["A+B 占比"] = f"{round(100 * d['有 A+B 的机构'] / d['机构'], 1)}%" if d["机构"] else ""
        src_rows.append(d)
    src_rows.sort(key=lambda d: -d["有 A+B 的机构"])

    first: dict[str, tuple] = {}
    for c in confirmed:
        if c["org_id"] not in first or c.get("added", "") < first[c["org_id"]][0]:
            first[c["org_id"]] = (c.get("added", ""), c.get("found_in") or "?")
    contrib = []
    stages: dict[str, list] = {}
    for c in confirmed:
        stages.setdefault(c.get("found_in") or "?", []).append(c)
    for st, rows in sorted(stages.items()):
        contrib.append({"维度": "环节", "项": st, "A+B 路线": len(rows), "独有 A+B 路线": "",
                        "首个 A+B 来自这里的机构": sum(1 for v in first.values() if v[1] == st)})
    readers = sorted({r for c in confirmed for r in c.get("found_by", "").split("+") if r})
    for rd in readers:
        with_r = [c for c in confirmed if rd in c.get("found_by", "").split("+")]
        only = [c for c in with_r if c.get("found_by") == rd]
        contrib.append({"维度": "读取器", "项": rd, "A+B 路线": len(with_r), "独有 A+B 路线": len(only),
                        "首个 A+B 来自这里的机构": ""})
    return src_rows, contrib


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
                        "evidence_text": c.get("quote"),
                        "qualification_rationale": f"{c.get('grade_reason')}; evidence {c.get('evidence')} "
                                                   f"{c.get('evidence_score')}: {c.get('evidence_why')}",
                        "qualification_status": ("needs_review: " + (c.get("review_reason") or "")) if c.get("review") == "yes" else "qualified",
                        "research_only": "TRUE", "send_authorized": "FALSE"})
    return path
