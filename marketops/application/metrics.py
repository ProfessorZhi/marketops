"""运行指标。

需求 §10 明确：核心不是 ``domains/min``，而是

    net-new A/B · net-new C/D · blocked recovery ·
    false attribution · SERP queries · browser seconds

所以这些量必须是一等公民 —— 在场数据结构里各有一个明确的字段，
而不是让人事后从日志里 grep 出来。
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .freshness import Judgement
from ..domain.enums import Cohort, Grade


@dataclass
class AccountMetrics:
    """单个账号的运行指标。"""

    account_id: str
    cohort: Cohort

    # --- 检索 ---
    #: 计划发出的检索数。**不是**实际发出的数 —— 差额看下面两个字段。
    serp_queries: int = 0
    serp_api_calls: int = 0
    #: **已经发出去了、而用量不知道**的检索调用数（请求派发出去了，回来的是异常）。
    #:
    #: 与 ``serp_api_calls`` 是一对，必须一起读：失败的那一发不进 ``serp_api_calls``
    #: （那里的 0 说的是「没有结果可以读」），而它**确实是发出去了**的 —— 只是这一发
    #: 花了多少（发了几跳、对方计费没有）没有任何地方知道，§8 正因此不肯退款。少了
    #: 这一格，一次已经发出、但失败的检索在报告上与「压根没发」同形，而前者是一条
    #: **悬着的预留**与一笔可能已经记在对方账上的钱。
    #:
    #: 记的是**派发出去的次数**（每一次派发记 1）。真实的请求数按定义未知，所以这是
    #: 一个**下界**，不是估计值 —— 这里不编一个数，只把「不知道」这件事记成它自己。
    #: 一个 0 在这一格是「这一轮没有发生过这种事」，见 ``QueryReceipt.usage_unknown``。
    serp_api_calls_unknown: int = 0
    #: 收下的候选 URL（**上限截断之后**的数）。
    candidate_urls: int = 0
    #: 被 ``max_candidates_per_account`` 砍掉、因此**没有进入这条流水线**的候选 URL。
    #:
    #: 与 ``candidate_urls`` 是一对，必须一起读：只有前者时，「这次没发现新东西」
    #: 与「发现了但被上限砍了」在报告上长得一模一样 —— 而两者的处置完全不同
    #: （前者是市场结论，后者是配置结论）。截断记在 ``application/caps.py`` 里，
    #: 这个字段是它落到报告上的那一格。
    candidate_urls_dropped: int = 0
    #: 因为额度已经用完而**没有发出**的检索。
    #:
    #: 与「候选枯竭」是两件事，必须分开：前者是一个需要人处置的结局（改 profile 的
    #: 声明，或者接受这个账号只查到一半），后者是这个账号本来就没有更多东西。混成
    #: 一个「0 个候选」，一次被预算截断的运行看起来就跟一次跑完了一模一样。
    search_budget_exhausted: bool = False
    #: 因为**上一次运行已经付过账**而跳过重发的检索（见 ``pipeline._discover``）。
    #: 同样是「没有发出」，理由却相反：一条都没花。静默跳过会让「检索过、没东西」
    #: 与「压根没检索」在报告上同形，所以它必须是能被看见的数字。
    serp_queries_skipped: int = 0

    # --- 抓取 ---
    http_ok: int = 0
    http_blocked: int = 0
    http_empty_shell: int = 0
    http_js_required: int = 0
    http_not_found: int = 0
    http_error: int = 0

    # --- 浏览器（成本指标）---
    browser_escalations: int = 0
    browser_seconds: float = 0.0

    # --- 解析与抽取 ---
    documents_parsed: int = 0
    parse_failures: int = 0
    #: 材料**解析出来了**、但在绑定层没过判据的份数（§5 的 fail-soft）。
    #:
    #: 与 :attr:`parse_failures` 分开，是因为两者的下一步动作不同：解析失败要去看
    #: 解析器能不能读这种格式，材料不合法要去看**页面上写了什么**（最常见的是被解析
    #: 成未来日期的发布时间）。合成一个数，「这一轮丢了几页」就分不出该修哪一头。
    invalid_materials: int = 0
    literal_emails_found: int = 0
    role_evidence_found: int = 0
    evidence_written: int = 0

    # --- 结果 ---
    #: 已定级的**联系路线**数。
    routes: int = 0
    #: 未解决的路线机会数（有具名角色、没有 literal 邮箱）。
    #: 与 ``routes`` 分开计数，因为它们是两种不同的产出，混在一起会让
    #: 「拿到路线的比例」看起来比实际高 —— 机会不是路线（SOP L126）。
    opportunities: int = 0
    grades: dict[str, int] = field(default_factory=dict)
    conflicts: int = 0
    snippet_bindings_rejected: int = 0

    # --- 时效（§9·2b）---
    #: 判过时效的产出行数（路线与路线机会各判一次，都算）。
    freshness_judged: int = 0
    #: **判据没能开工**的行数：这一行没有观测时刻，断言 id 算不出来。
    #:
    #: 与 ``freshness_judged`` 必须一起读，理由与 ``candidate_urls`` /
    #: ``candidate_urls_dropped`` 那一对逐字相同：只有前者时，「这一行判过了、结论是
    #: unknown」与「这一行压根没判」在报告上同形 —— 而后者是一条**没有时刻的行正
    #: 挂在库里**，处置方式与前者完全不同。
    freshness_without_a_time_anchor: int = 0
    #: 判出来的取值分布（``current`` / ``stale`` / ``historical`` / ``unknown``）。
    #:
    #: 只有判过的行进来。零分母时它是空表而**不是** ``{"stale": 0}`` —— 见
    #: ``application/metrics.py`` 的纪律与 §「未知不得写成 0」。
    currentness: dict[str, int] = field(default_factory=dict)

    elapsed_seconds: float = 0.0

    def record_grade(self, grade: Grade) -> None:
        self.grades[grade.value] = self.grades.get(grade.value, 0) + 1

    def record_freshness(self, judgement: Judgement) -> None:
        """把一次时效判定记进指标。

        收的是**整个** ``Judgement`` 而不是「结果 + 取值」两个参数：这两格在
        ``Judgement`` 上本来就是一对（``NO_TIME_ANCHOR`` 的行按定义没有取值），
        拆开传就多了一次把 A 行的结果配到 B 行取值上的机会，而那个错误在报告上
        看不出来。
        """
        if judgement.currentness is None:
            self.freshness_without_a_time_anchor += 1
            return
        self.freshness_judged += 1
        key = judgement.currentness.value
        self.currentness[key] = self.currentness.get(key, 0) + 1

    @property
    def net_new_ab(self) -> int:
        return self.grades.get(Grade.A.value, 0) + self.grades.get(Grade.B.value, 0)

    @property
    def net_new_cd(self) -> int:
        return self.grades.get(Grade.C.value, 0) + self.grades.get(Grade.D.value, 0)

    @property
    def produced_anything(self) -> bool:
        """这次跑出了可用的东西 —— 路线**或**路线机会。

        「机会」也算产出：它证明抓取与抽取走到了位，只是邮箱这一步还没有。
        把它排除在外，一次「抓到 30 个具名角色但都没有邮箱」的运行会被记成
        「什么都没产出」，而那是完全不同的一件事。
        """
        return self.routes > 0 or self.opportunities > 0

    def to_dict(self) -> dict[str, object]:
        return {
            "account_id": self.account_id,
            "cohort": self.cohort.value,
            "serp_queries": self.serp_queries,
            "serp_api_calls": self.serp_api_calls,
            "serp_api_calls_unknown": self.serp_api_calls_unknown,
            "serp_queries_skipped": self.serp_queries_skipped,
            "search_budget_exhausted": self.search_budget_exhausted,
            "candidate_urls": self.candidate_urls,
            "candidate_urls_dropped": self.candidate_urls_dropped,
            "http_ok": self.http_ok,
            "http_blocked": self.http_blocked,
            "http_empty_shell": self.http_empty_shell,
            "http_js_required": self.http_js_required,
            "http_not_found": self.http_not_found,
            "http_error": self.http_error,
            "browser_escalations": self.browser_escalations,
            "browser_seconds": round(self.browser_seconds, 3),
            "documents_parsed": self.documents_parsed,
            "parse_failures": self.parse_failures,
            "invalid_materials": self.invalid_materials,
            "literal_emails_found": self.literal_emails_found,
            "role_evidence_found": self.role_evidence_found,
            "evidence_written": self.evidence_written,
            "routes": self.routes,
            "opportunities": self.opportunities,
            "grades": dict(self.grades),
            "conflicts": self.conflicts,
            "snippet_bindings_rejected": self.snippet_bindings_rejected,
            "freshness_judged": self.freshness_judged,
            "freshness_without_a_time_anchor": self.freshness_without_a_time_anchor,
            "currentness": dict(self.currentness),
            "net_new_ab": self.net_new_ab,
            "net_new_cd": self.net_new_cd,
            "elapsed_seconds": round(self.elapsed_seconds, 3),
        }


@dataclass
class RunReport:
    """一次运行的汇总。对应 §10 的「核心看什么」。"""

    run_id: str
    accounts: list[AccountMetrics] = field(default_factory=list)

    # --- 需求 §10 指定的核心指标 ---
    @property
    def net_new_ab(self) -> int:
        return sum(item.net_new_ab for item in self.accounts)

    @property
    def net_new_cd(self) -> int:
        return sum(item.net_new_cd for item in self.accounts)

    @property
    def blocked_recovery(self) -> int:
        """previous_blocked 队列里这次**产出了东西**的账号数。

        注意这是「这次跑通了」，不是「之前的拦截被绕过了」——
        本项目不绕过拦截，recovery 只可能来自改走其他公开来源。
        """
        return sum(
            1
            for item in self.accounts
            if item.cohort is Cohort.PREVIOUSLY_BLOCKED and item.produced_anything
        )

    @property
    def still_blocked(self) -> int:
        return sum(
            1
            for item in self.accounts
            if item.cohort is Cohort.PREVIOUSLY_BLOCKED and not item.produced_anything
        )

    @property
    def false_attribution(self) -> int:
        """被标记为归属冲突的路线数。目标是 0。"""
        return sum(item.conflicts for item in self.accounts)

    @property
    def serp_queries(self) -> int:
        return sum(item.serp_queries for item in self.accounts)

    @property
    def budget_exhausted_accounts(self) -> int:
        """被检索额度**截断**的账号数。

        它是**计数**而不是布尔：一次运行里「有一个账号撞上了上限」与「所有账号都
        撞上了」需要不同的处置（前者是那个账号的额度定小了，后者是这个 profile 的
        声明与这次研究规模不匹配），而一个 ``bool`` 会把两者压成同一个值。
        """
        return sum(1 for item in self.accounts if item.search_budget_exhausted)

    @property
    def serp_queries_skipped(self) -> int:
        """因上一次运行已付账而未重发的检索数。见 ``AccountMetrics`` 的同名字段。"""
        return sum(item.serp_queries_skipped for item in self.accounts)

    @property
    def serp_api_calls_unknown(self) -> int:
        """**发出去了、而用量不知道**的检索调用数。见 ``AccountMetrics`` 的同名字段。

        汇总在运行这一层，与 ``serp_queries_skipped`` 同一个理由：``serp_api_calls``
        与它是一对（**已知 / 未知**），而一对里只汇总一半，会让「这一轮有多少发是
        去向不明的」只能靠人把 ``accounts[]`` 加起来。
        """
        return sum(item.serp_api_calls_unknown for item in self.accounts)

    @property
    def browser_seconds(self) -> float:
        return round(sum(item.browser_seconds for item in self.accounts), 3)

    @property
    def snippet_rejections(self) -> int:
        return sum(item.snippet_bindings_rejected for item in self.accounts)

    @property
    def freshness_judged(self) -> int:
        return sum(item.freshness_judged for item in self.accounts)

    @property
    def freshness_without_a_time_anchor(self) -> int:
        return sum(item.freshness_without_a_time_anchor for item in self.accounts)

    @property
    def currentness(self) -> dict[str, int]:
        """判过的产出行按取值汇总。**空表不是全零** —— 它说的是没有一行判过。"""
        merged: dict[str, int] = {}
        for item in self.accounts:
            for key, count in item.currentness.items():
                merged[key] = merged.get(key, 0) + count
        return merged

    def to_dict(self) -> dict[str, object]:
        return {
            "run_id": self.run_id,
            "accounts_total": len(self.accounts),
            "net_new_ab": self.net_new_ab,
            "net_new_cd": self.net_new_cd,
            "blocked_recovery": self.blocked_recovery,
            "still_blocked": self.still_blocked,
            "false_attribution": self.false_attribution,
            "serp_queries": self.serp_queries,
            "serp_queries_skipped": self.serp_queries_skipped,
            "serp_api_calls_unknown": self.serp_api_calls_unknown,
            "budget_exhausted_accounts": self.budget_exhausted_accounts,
            "browser_seconds": self.browser_seconds,
            "snippet_rejections": self.snippet_rejections,
            "freshness_judged": self.freshness_judged,
            "freshness_without_a_time_anchor": self.freshness_without_a_time_anchor,
            "currentness": self.currentness,
            "accounts": [item.to_dict() for item in self.accounts],
        }


__all__ = ["AccountMetrics", "RunReport"]
