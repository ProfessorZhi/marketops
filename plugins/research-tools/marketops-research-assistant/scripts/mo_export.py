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


STRICT = {"A": ("A",), "AB": ("A", "B"), "named_A": ("A",)}   # brief.strict_ledger; default AB


def ledgers(emails: list, strict: str = "AB") -> dict:
    """Bruce's two ledgers, never one number: research-valid routes (confirmed A-D, each labelled)
    and the strict ledger the brief names (named A only, A, or A+B)."""
    ok = [c for c in emails if c.get("review") != "yes"]
    valid = [c for c in ok if c.get("grade") in ("A", "B", "C", "D")]
    grades = STRICT.get(strict, STRICT["AB"])
    strict_rows = [c for c in ok if c.get("grade") in grades
                   and (strict != "named_A" or str(c.get("person") or "").strip())]
    # one address is one route, however many rows of the pool it landed on (073: two pooled
    # twice clubs counted the same president twice)
    strict_rows = list({str(c.get("value") or "").lower() or n: c for n, c in enumerate(strict_rows)}.values())
    return {"valid_routes": len(valid), "valid_orgs": len({c.get("org_id") for c in valid}),
            "valid_by_grade": " / ".join(str(sum(c.get("grade") == g for c in valid)) for g in "ABCD"),
            "strict": strict if strict in STRICT else "AB", "strict_routes": len(strict_rows),
            "strict_orgs": len({c.get("org_id") for c in strict_rows})}


def metrics(orgs: list, emails: list) -> dict:
    """Headline numbers count confirmed A/B only; rows still marked review=yes are reported apart.
    A store filed under its chain (part_of) is not counted on its own: the chain is."""
    orgs = [o for o in orgs if ws.counted(o)] or orgs
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
    ab_orgs = {c["org_id"] for c in ab}
    return {"orgs": len(idset),
            # Bruce's 71/249: his 249 graded accounts are the ones he found a route for (A 4 / B 67 /
            # C 102 / D 76) - A+B over organisations with any A-D route is the number to set beside it
            "ab_share_of_covered": round(100 * len(ab_orgs) / (len(covered) or 1), 1),
            "covered_orgs": len(covered), "ab_routes": len(ab), "ab_orgs": len({c["org_id"] for c in ab}),
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


CHECK_SAMPLE = 100


def _check_sample(emails: list) -> list[dict]:
    """A fixed random sample of confirmed A/B rows for the delivery spot check (the same rows on
    every export, so checks recorded with grade(set=[{..., "checked": "ok"|"wrong"}]) stay put)."""
    import random
    pool = sorted((c for c in emails if c.get("grade") in ("A", "B") and c.get("review") != "yes"),
                  key=lambda c: (c.get("org_id", ""), c.get("value", "")))
    pick = random.Random(20261027).sample(pool, min(CHECK_SAMPLE, len(pool)))
    return [{"机构": c.get("org"), "邮箱": c.get("value"), "等级": c.get("grade"), "姓名": c.get("person"),
             "岗位": c.get("role"), "来源页面": c.get("page_url"), "原文引用": c.get("quote"),
             "核对结果": {"ok": "正确", "wrong": "错误"}.get(c.get("checked", ""), "未核对"), "备注": c.get("note")}
            for c in pick]


CHECK_MIN = 20


def delivery_gate(emails: list, pdir=None) -> dict:
    """Whether the workbook may go out: at least CHECK_MIN confirmed A/B rows opened and recorded
    (grade(set=[{value, org_id, checked}])) at >= 90% - 073 and 074 both exported without one
    check, so the precision bar had no reading. Every export says so on its first line."""
    ab = [c for c in emails if c.get("grade") in ("A", "B") and c.get("review") != "yes"]
    done = [c for c in emails if c.get("checked") in ("ok", "wrong")]
    need = min(CHECK_MIN, len(ab))
    # the lead's close-out comes before delivery (078: merges, reviews and held rows were left as
    # questions in a final report): each open item keeps the workbook from reading 可交付
    open_items = []
    review = sum(1 for c in emails if c.get("grade") in ("A", "B") and c.get("review") == "yes")
    if review:
        open_items.append(f"{review} 条 A/B 待复核（接受、更正或降级，写明原因）")
    if pdir is not None:
        import mo_tasks
        dups = mo_tasks.possible_duplicates(pdir)
        if dups:
            open_items.append(f"{len(dups)} 组疑似重复（orgs(merge=\"suggested\", keep_apart=[...])）")
        held = sum(1 for c in ws.read_contacts(pdir) if c.get("graded_by") == "held")
        if held:
            open_items.append(f"{held} 条暂扣的名录地址（grade(held=...)）")
        # an address with no grade is in no ledger, and the gate let 7 through (paris-all 0.9.0)
        ungraded = sum(1 for c in emails if not c.get("grade") and c.get("graded_by") != "held")
        if ungraded:
            open_items.append(f"{ungraded} 条地址还没有评级（grade(project) 或 grade(set=...)）")
        # the team still at work: what lands after this export is not in it (paris-all 0.9.0: a
        # worker wrote for 15 more minutes after the lead exported)
        held_tasks = [t for t in ws.read_csv(pdir / "tasks.csv") if t.get("status") == "claimed"]
        if held_tasks:
            open_items.append(f"{len(held_tasks)} 个包还在 worker 手里（" + ("等他们交回再导出；worker 已不在的，用 "
                              "tasks(action=\"done\", task_id, release=True) 收回" if ws.team_stop(pdir) else
                              "先 tasks(action=\"stop\")，等他们交回再导出") + "）")
    if not ab:
        return {"ready": False, "status": "未交付：没有确认的 A/B"}
    if len(done) < need:
        return {"ready": False, "status": f"未抽检，不得交付：已核对 {len(done)} / 至少 {need} 条（抽检页逐条打开原页，"
                                          "用 grade(set=[{value, org_id, checked: 'ok'|'wrong'}]) 记下）",
                "todo": "spot check: open the 抽检 sheet's rows, record each with grade(set=[{value, org_id, checked}])"}
    ok = sum(c["checked"] == "ok" for c in done) / len(done)
    if ok < 0.9:
        return {"ready": False, "status": f"抽检准确率 {ok:.0%} 低于 90%，不得交付：修正错误行并查同类错误"}
    if open_items:
        return {"ready": False, "status": "收尾未完成，不得交付：" + "；".join(open_items), "todo": open_items}
    return {"ready": True, "status": f"可交付：抽检 {len(done)} 条，准确率 {ok:.0%}"}


def _precision(emails: list) -> str:
    done = [c for c in emails if c.get("checked") in ("ok", "wrong")]
    if not done:
        return "未抽检（打开抽检页逐条核对，用 grade(set=[{value, org_id, checked}]) 记下结果）"
    ok = sum(c["checked"] == "ok" for c in done)
    return f"{round(100 * ok / len(done), 1)}%（{ok}/{len(done)}）"


def _researched_share(pdir, orgs: list, emails: list) -> str:
    """A+B among the organisations the work reached (an agent verdict or the tools' final pass):
    the pool's unread rest says nothing about how well the research does."""
    ids = {str(o.get("id") or o.get("org_id") or "") for o in orgs if ws.counted(o)}
    done = ({v["org_id"] for v in ws.read_csv(pdir / "verdicts.csv")}
            | {r["org_id"] for r in ws.read_csv(pdir / "crawl.csv") if r.get("pass") == "final"}) & ids
    if not done:
        return ""
    ab = {c["org_id"] for c in emails if c.get("grade") in ("A", "B") and c.get("review") != "yes"} & done
    return f"{round(100 * len(ab) / len(done), 1)}%（{len(ab)}/{len(done)}）"


def _reconcile(org_rows: list, total: int) -> str:
    """Bruce could never tell which of three numbers to trust: every organisation is counted once,
    under its best grade, and the parts must add up to the total."""
    held = lambda r: str(r.get("最佳等级", "")).endswith("（待复核）")  # noqa: E731
    parts = [sum(1 for r in org_rows if str(r.get("最佳等级", "")).startswith(g) and not held(r)) for g in "AB"] + \
        [sum(1 for r in org_rows if held(r))] + \
        [sum(1 for r in org_rows if str(r.get("最佳等级", "")).startswith(g)) for g in "CD"]
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
    # held back from a flood (a directory printed on one organisation's pages): not its routes,
    # not in any total until an agent clears them (grade(held=...)) - their own sheet
    held_rows = [c for c in contacts if c.get("kind") == "email" and c.get("graded_by") == "held"]
    emails = [c for c in contacts if c.get("kind") == "email" and c.get("graded_by") != "held"]
    phones = {}
    for c in contacts + ws.read_csv(pdir / "phones.csv"):
        if c.get("kind", "phone") == "phone":
            phones.setdefault(c.get("org_id"), []).append(c["value"])
    orgs = [o for o in ws.read_csv(pdir / "orgs.csv") if not o.get("merged_into")]   # a duplicate lives on in its keeper
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
    # confirmed A/B first, then A/B still to review, then C/D: the org's first contact is a confirmed
    # route whenever it has one
    order_key = lambda c: (0 if c.get("grade") in ("A", "B") and c.get("review") != "yes" else  # noqa: E731
                           1 if c.get("grade") in ("A", "B") else 2,
                           ORDER.get(c.get("grade", ""), 4), -int(c.get("evidence_score") or 0))
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
        best = (LABEL.get(top.get("grade", ""), "") + ("（待复核）" if top.get("grade") in ("A", "B")
                                                      and top.get("review") == "yes" else "")) if top else "未找到"
        org_rows.append({**where, **o, "最佳等级": best,
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
    gate = delivery_gate(emails, pdir)
    summary = [
        {"项目": "交付状态", "值": gate["status"]},
        {"项目": "机构数", "值": total},
        {"项目": "场地数", "值": len(venues)},
        {"项目": "数据源饱和", "值": (lambda st: f"{st['sources']} 个数据源；最近新增占比 "
                                     f"{', '.join(f'{x:.0%}' for x in st['new_share_last']) or '-'}；"
                                     + ("已饱和" if st["saturated"] else "未饱和，池子可能不全"))(ws.source_saturation(pdir))},
        *(lambda lg: [
            {"项目": "研究有效账：确认的 A–D 研究有效邮箱（A / B / C / D，各级标注；不含待复核）",
             "值": f"{lg['valid_routes']} 条（{lg['valid_by_grade']}），{lg['valid_orgs']} 家机构"},
            {"项目": f"严格账（简报 strict_ledger = {lg['strict']}：named_A 只算具名 A，A 只算 A，AB 算 A+B）",
             "值": f"{lg['strict_routes']} 条，{lg['strict_orgs']} 家机构"},
        ])(ledgers([c for c in emails if not orgs or c.get("org_id") in {str(o.get("id") or o.get("org_id") or "") for o in orgs}],
                   str(ws.brief(pdir).get("strict_ledger") or "AB"))),
        {"项目": "广度：有确认 A+B 的机构占比（有确认 A/B 的机构 ÷ 机构数；不含待复核）", "值": f"{m['ab_org_share']}%（{m['ab_orgs']} 家）"},
        {"项目": "每百机构 A+B 路线数（A+B 条数 ÷ 机构数 × 100，Bruce 口径；同一人两地址计一条）", "值": m["ab_routes_per_100_orgs"]},
        {"项目": "每百机构 A+B 路线数，每机构最多计 3 条", "值": m["ab_routes_per_100_orgs_max3"]},
        {"项目": "A+B 路线数 / 有 A+B 的机构数", "值": f"{m['ab_routes']} / {m['ab_orgs']}"},
        {"项目": "覆盖率（有任一 A–D 路线的机构 ÷ 机构数）", "值": f"{m['coverage_abcd']}%"},
        {"项目": "Bruce 同口径：有确认 A+B 的机构 ÷ 有任一 A–D 路线的机构（Bruce 法国 71/249 = 28.5%）",
         "值": f"{m['ab_share_of_covered']}%（{m['ab_orgs']}/{m['covered_orgs']}）"},
        *(lambda r: [{"项目": "已研究机构（agent 下过结论或工具读完）中有确认 A+B 的占比", "值": r}] if r else [])(
            _researched_share(pdir, orgs, emails)),
        {"项目": "暂扣的名录地址（某机构页面上的整张名录，未计入任何总数；agent 用 grade(held=...) 处理）",
         "值": len(held_rows)},
        {"项目": "待复核的 A/B（低置信度、共用地址或来自他站；不计入上面的数）", "值": m["ab_to_review"]},
        {"项目": "若待复核全部成立：每百机构 A+B 路线数", "值": m["ab_routes_per_100_orgs_if_review_holds"]},
        {"项目": "对账：机构按最佳等级 确认A / 确认B / 只有待复核的A或B / C / D / 未找到（其中未抓取） = 机构数；确认A + 确认B = 上面的有 A+B 机构数",
         "值": _reconcile([r for r in org_rows if ws.counted(r)], total)},   # a store filed under its chain is in neither
        {"项目": "国家未识别的机构（应补齐，不得交付为未识别）", "值": sum(1 for r in org_rows if not r.get("国家"))},
        {"项目": "邮箱总数（A/B/C/D/未评级/X）", "值": " / ".join(str(sum(c.get("grade", "") == g for c in emails)) for g in ("A", "B", "C", "D", "", "X"))},
        {"项目": "证据强/中/弱（邮箱数；弱的也保留，由人判断）", "值": " / ".join(str(sum(c.get("evidence") == lv for c in emails)) for lv in ("strong", "medium", "weak"))},
        {"项目": "抽检准确率（抽检页已核对的确认 A/B 中正确的比例；交付门槛 ≥ 90%）", "值": _precision(emails)},
        {"项目": "agent 已下结论的机构（agent 结论页）", "值": len({v["org_id"] for v in ws.read_csv(pdir / "verdicts.csv")})},
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
    if held_rows:
        sheets.append(_sheet("暂扣（名录地址）", [{"机构": c.get("org"), "机构ID": c.get("org_id"), "邮箱": c.get("value"),
                                              "来源页面": c.get("page_url"), "原文引用": c.get("quote")}
                                             for c in held_rows], ["机构", "机构ID", "邮箱", "来源页面", "原文引用"],
                             (24, 16, 30, 50, 70)))
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
    check_rows = _check_sample(emails)
    if check_rows:
        sheets.append(_sheet("抽检", check_rows, list(check_rows[0].keys()), (24, 30, 6, 18, 24, 50, 70, 10, 30)))
    verdict_rows = [{"机构": v.get("org"), "机构ID": v.get("org_id"), "agent": v.get("agent"), "结论": v.get("verdict"),
                     "找到了什么": v.get("found"), "在哪类页面": v.get("where"), "为什么": v.get("why"), "时间": v.get("time")}
                    for v in ws.read_csv(pdir / "verdicts.csv")]
    if verdict_rows:
        sheets.append(_sheet("agent 结论", verdict_rows, list(verdict_rows[0].keys()), (24, 10, 12, 10, 40, 30, 50, 18)))
    sheets.append(_sheet("成本", costs, ["供应商", "调用", "成功", "估算美元"]))
    stamp = datetime.now().strftime('%Y%m%d-%H%M')
    out = pdir / f"{pdir.name}-{stamp}.xlsx"
    out.write_bytes(render_workbook(sheets))
    routes = _routes_csv(pdir, emails, stamp)
    import mo_journal
    checked = [c for c in emails if c.get("checked") in ("ok", "wrong")]
    if len(checked) >= 20 and sum(c["checked"] == "ok" for c in checked) / len(checked) < 0.9:
        mo_journal.auto(pdir, "precision", f"spot check precision {_precision(emails)} - under the 90% delivery bar",
                        "抽检 sheet")
    retro = mo_journal.retro(pdir, {r["项目"]: r["值"] for r in summary if r["项目"] not in ("说明", "导出时间")},
                             ws.stages(pdir),
                             {"Playbook segments": sorted(ws.playbook(pdir))[:40],
                              "Agent verdicts": [f"{k}: {n}" for k, n in sorted(
                                  __import__("collections").Counter(v.get("verdict", "") for v in
                                                                    ws.read_csv(pdir / "verdicts.csv")).items())]})
    return {"file": str(out), "routes_csv": str(routes), "retro": str(retro), "contacts": len(route_rows), **m,
            "to_review": sum(c.get("review") == "yes" for c in emails), "delivery": gate}


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
