"""Research Pipeline 编排。

    Known Account → Serper Search → Candidate URLs → HTTP Fetch
        →（必要时）Browser Fetch → HTML/PDF → Document Parsing
        → literal email extraction → Name/Role/Email evidence binding
        → ContactRoute + RouteOpportunity
        → 时效（这一行今天还能不能用）→ A/B/C/D/X（路线等级）
        + AccountStatus（账号准入）→ 本地 Evidence Store

三个判定**都在这里调用，但互不影响**：

* ``grade_route`` 决定 ``ContactRoute.grade``；
* ``grade_account`` 决定 ``AccountStatus``；
* ``application/freshness.judge`` 决定行上的 ``currentness``。

把后两者塞进 ``grade_route``，或者用其中一个去推另一个，都会让「组织对、人不对」
这类情形无处安放 —— 而那是这类研究里最常见的结果之一。时效同理：「这条路线对，
但证据是两年前的」是一条**独立**的结论，压进等级里就再也读不出来了。

时效的时限口径来自 bundle 的 ``freshness`` 声明，**没有声明就抛**（``policy_of``），
不落一组缺省天数 —— 理由写在 ``application/freshness.py`` 与 profile 的注释里。

全部依赖通过 ``PipelineDeps`` 注入。pipeline 本身不构造任何 adapter，
因此可以在没有任何真实凭据的情况下用 fake 依赖跑完整条链路 ——
这是「本地 pipeline 必须能脱离飞书凭据独立运行」的落地方式。

## 检索额度扣在**起飞之前**

每一次检索的次序是**先预留、再调用、后结算**（``RunBudget``）。这条规则今天
住在 ``application/billed_search.py``，**不在这里** —— 服务面的 ``search_web``
要遵守逐字相同的一套，而其中两条抄错了不会报错（换了幂等键的写法，「付过账的
不重发」当场失效；失败分支顺手退款，§8 要的正是不做那件事）。所以本模块只负责
循环、指标与候选收敛，规则本身只有一份。

真正的含义写在 ``application/budget.py``：一个账号的检索额度是**这个账号的**，
不是这次运行的；要再来一轮，改 profile 的声明（那是一次可评审的 diff），
不是把上限调高到没人看得见的地方。
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

from ..ports.acquisition import DocumentFetch, FetchMode, FetchRequest, FetchResult
from ..config import Settings
from .acquisition import DocumentAcquisition
from .contact_routes import RouteContext, RouteMaterial, produce_routes
from .document_router import DocumentRouter
from ..domain.bind import BindingSubject, RouteVocabulary
from ..domain.entities import ContactRoute, RouteOpportunity
from ..domain.enums import (
    FetchOutcome,
    FetchTier,
    QueryOutcome,
    SourceKind,
)
from ..domain.errors import MarketOpsError, ParseError
from ..domain.evidence import AccountRecord, ResearchRun
from .grading import grade_account
from ..domain.profile import ProfileBundle
from ..ports.budget import BudgetLedger
from ..ports.scenes import MaintenanceStore
from ..ports.search import SearchProvider
from ..ports.store import EvidenceStore
from .billed_search import BilledSearch
from .budget import RunBudget
from .caps import apply_cap
from .queries import build_queries
from ..domain.util import slugify, utc_now
from .metrics import AccountMetrics, RunReport


@dataclass
class AccountRunResult:
    """单账号结果，便于上层做 per-account 报告。

    ``routes`` 与 ``opportunities`` 分开 —— 见 ``metrics.AccountMetrics``。
    """

    account_id: str
    metrics: AccountMetrics
    routes: list[ContactRoute] = field(default_factory=list)
    opportunities: list[RouteOpportunity] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


@dataclass
class PipelineDeps:
    """pipeline 的全部外部依赖。

    ``acquisition`` 收的是**统一入口**而不是两个 fetcher（§10）。它内部可以为
    ``None`` 浏览器 —— 没装浏览器时 pipeline 仍应能跑完（只是浏览器指标为 0），
    这比让整个 run 失败合理得多。

    ``budget`` **必填，没有缺省**。这一处与浏览器的可选性不对称是刻意的：没有浏览器
    会让 ``browser_escalations`` 停在 0，而这个 0 在一份报告里是可以追问的（「这次
    一次都没升级？」）；没有一个账本则**什么都不显示** —— 「没有账本」与「账本上
    还有大量余额」在任何指标上完全同形。§8 要求上限在调用之前就生效，那么本 dataclass
    必须让「忘了决定预算」写不出来，而不是默认成无上限。

    **这里曾经是 ``http`` 与 ``browser`` 两个字段。** 换成一个是把「HTTP 成功就不
    要启动浏览器」这条规则从 pipeline 里拿出去，交给 ``application/acquisition.py``
    —— 那条规则每多一个调用方就被多抄一遍，而抄错只在某一个调用方身上发生。

    ``maintenance`` **必填，没有缺省**，理由与 ``budget`` 同类但落点不同：
    ``produce_routes`` 在这一趟里**真的**落了新证据行时，要当场给维护账记一条变更
    信号（见 ``application/contact_routes._signal_for_new_sources``）。可省略的
    维护账意味着「忘了注入」与「这一趟真的没有新来源」在报告上完全同形，而前者
    会让来源供给的变化要等满一个复核周期才被看见 —— ``application/discovery_run.py``
    已经在同一类取舍上吃过一次亏，那里写下的结论是「宁可让调用点少一个，也不要
    让『忘了决定』写得出来」。
    """

    settings: Settings
    bundle: ProfileBundle
    search: SearchProvider
    acquisition: DocumentAcquisition
    store: EvidenceStore
    router: DocumentRouter
    budget: BudgetLedger
    maintenance: MaintenanceStore


def _derived_run_id() -> str:
    """没有给出 run id 时现算一个（``run-20260926t101530`` 一类）。

    取的是 UTC 秒级时刻 —— 与 :class:`~marketops.domain.evidence.ResearchRun` 那
    一侧同名同形。**它不是一个可以用来追责的 id**，它只保证「这一趟有一个名字」
    这件事成立；要追责的调用方传自己的 run id 进来（服务面那条路传的是结算的
    ``run_id``）。

    **不返回空串。** 空串会让「这一趟没有名字」与「这一格忘了填」在拒收回执上
    长得一样，而 :class:`RouteContext` 存在的全部意义就是分得开这两件事。
    """
    return f"run-{slugify(utc_now().isoformat(timespec='seconds'))}"


class ResearchPipeline:
    """把各层串起来。本类只做编排，不含任何业务规则。"""

    def __init__(self, deps: PipelineDeps) -> None:
        self._deps = deps
        # 词表在这里编译一次：每个 URL 都从 YAML 重读一次没有必要，
        # 而且会让「同一个 profile 在一次 run 内产出不同结果」成为可能。
        self._vocabulary = RouteVocabulary.from_bundle(deps.bundle)

    # ------------------------------------------------------------------
    def run(self, accounts: list[AccountRecord], *, run_id: str | None = None) -> RunReport:
        resolved_run_id = run_id or _derived_run_id()
        report = RunReport(run_id=resolved_run_id)
        for account in accounts:
            result = self.run_account(account, run_id=resolved_run_id)
            report.accounts.append(result.metrics)
        return report

    # ------------------------------------------------------------------
    def run_account(self, account: AccountRecord, *, run_id: str | None = None) -> AccountRunResult:
        """跑一个账号。

        ``run_id`` 缺省时**现算一个**（与 :meth:`run` 逐字同一个取法），而不是留空：
        它要进每一张拒收回执（§6.3），而一张说不出「哪一趟」的回执与一张忘了填的
        回执在盘上一模一样。整轮跑时由 :meth:`run` 传下来，于是同一轮里的每一份
        产出共享同一个值 —— 那正是「这一批是同一次运行的产物」在盘上的样子。
        """
        context = RouteContext(run_id=run_id or _derived_run_id())
        started = time.perf_counter()
        metrics = AccountMetrics(account_id=account.account_id, cohort=account.cohort)
        errors: list[str] = []
        routes: list[ContactRoute] = []
        opportunities: list[RouteOpportunity] = []

        # 上限在这里播种，**在第一次检索之前** —— 见文件头「检索额度扣在起飞之前」。
        # 未声明上限时 for_account 失败关闭，异常直接穿出 run_account：一次没有
        # 上限的运行不能表现为「跑完了、没有候选」。
        budget = RunBudget.for_account(
            self._deps.budget, self._deps.bundle, account.account_id
        )
        candidates = self._discover(account, metrics, errors, budget, context)
        for url in candidates:
            try:
                produced_routes, produced_opportunities, material_errors = self._process_url(
                    account, url, metrics, context
                )
            except MarketOpsError as exc:
                errors.append(f"{url}: {type(exc).__name__}: {exc}")
                continue
            routes.extend(produced_routes)
            opportunities.extend(produced_opportunities)
            # 这一页的材料没过判据（不是抓取失败、也不是解析失败，是**材料本身**
            # 在绑定层被拒）。它已经逐条带着 URL 与判据原文，直接并进这个账号的
            # 错误清单 —— §5 要的 fail-soft 具体就是这一行：页面作废，整轮继续，
            # 而那一页在报告里留痕。
            errors.extend(material_errors)

        metrics.routes = len(routes)
        metrics.opportunities = len(opportunities)
        metrics.elapsed_seconds = time.perf_counter() - started
        return AccountRunResult(
            account_id=account.account_id,
            metrics=metrics,
            routes=routes,
            opportunities=opportunities,
            errors=errors,
        )

    # ------------------------------------------------------------------
    def grade_account(self, account: AccountRecord):
        """账号准入判定。**不改写账号自身** —— 它不写回任何东西。

        ``researched`` / ``archived`` 这类由流程推进的状态不该被一次判定退回，
        因此调用方拿到的只是一个结论，怎么用由它自己决定。
        """
        return grade_account(account, self._deps.bundle)

    # ------------------------------------------------------------------
    def _discover(
        self,
        account: AccountRecord,
        metrics: AccountMetrics,
        errors: list[str],
        budget: RunBudget,
        context: RouteContext,
    ) -> list[str]:
        """检索并把结果收敛成候选 URL 列表。

        三段式记账与「付过账的不重发」不在这里 —— 它们在
        ``application/billed_search.py``，因为服务面的 ``search_web`` 要遵守
        **同一套**规则。本函数只剩编排：循环、指标、候选收敛。

        ``context`` 一路上是同一个值：snippet 被拒也要落一张**收据**（§6.3），
        而收据里有一格「哪一趟运行」—— 按 ``run_id`` 缺省现算的话，同一次运行里
        每一处的拒收会各说自己属于不同的一趟。
        """
        queries = build_queries(account, self._deps.bundle)
        metrics.serp_queries = len(queries)

        urls: list[str] = []
        seen: set[str] = set()
        billed = BilledSearch(search=self._deps.search, budget=budget)

        for query in queries:
            receipt = billed.query(query)
            if receipt.outcome is QueryOutcome.EXHAUSTED:
                metrics.search_budget_exhausted = True
                errors.append(f"search:{query.text!r}: BudgetExhaustedError: {receipt.error}")
                break
            if receipt.outcome is QueryOutcome.SKIPPED:
                metrics.serp_queries_skipped += 1
                continue
            if receipt.outcome is QueryOutcome.FAILED:
                # 这一发**已经发出去了**，而它花了多少没有人知道 —— 记进「未知」那一格。
                # 这一行必须住在这一支里：下面的 ``continue`` 是这一支唯一的去向，
                # 把计数写在循环末尾（挨着 ``serp_api_calls`` 那一行）会永远不执行，
                # 而症状恰恰是这一发在报告上彻底消失。反过来，把它折进
                # ``serp_api_calls`` 是另一个谎 —— 那一格里的 0 说的是「没有结果可以
                # 读」，不是「用量是 0」。规则的名字在 ``QueryReceipt.usage_unknown``
                # 上，这里不重述一遍（两个写法会漂开，见 ``billed_search`` 的文件头）。
                if receipt.usage_unknown:
                    metrics.serp_api_calls_unknown += 1
                exc = receipt.error
                errors.append(f"search:{query.text!r}: {type(exc).__name__}: {exc}")
                continue

            metrics.serp_api_calls += receipt.api_calls
            for hit in receipt.hits:
                if hit.url in seen:
                    continue
                seen.add(hit.url)
                urls.append(hit.url)
                # snippet 也要过一遍绑定 —— 不是为了采纳，是为了把「被拒绝」
                # 记成可观测的数字。见 §10 的 snippet 指标。
                self._scan_snippet(account, hit.url, hit.snippet, metrics, context)

        # 上限截断走 ``application/caps.py`` —— 那里有一条别处没有的东西：
        # **被砍掉的那几条**。以前这里是一行 ``min(...)`` 加一行切片，切掉多少
        # 一个字都没留下，于是「没找到」与「找到了但被上限砍了」在报告上同形。
        kept, decision = apply_cap(urls, self._deps.settings.max_candidates_per_account)
        metrics.candidate_urls = decision.kept
        metrics.candidate_urls_dropped = decision.dropped
        return kept

    def _scan_snippet(
        self,
        account: AccountRecord,
        url: str,
        snippet: str,
        metrics: AccountMetrics,
        context: RouteContext,
    ) -> None:
        """对 SERP snippet 跑一次**完整的**路线生产，并断言它一条都产不出来。

        **产出永远为空** —— 因为 ``SEARCH_SNIPPET`` 不在 ``BINDING_ELIGIBLE_SOURCES``
        里，绑定层会整体拒绝。这里只统计拒绝数：如果这个数长期为 0，说明 SERP 里
        根本没出现过邮箱，这条不变量就没被真正考验过。

        走的是 ``produce_routes``（而不是直接调 ``bind_from_text``），因为要守的
        命题是**这一趟**产不出路线，不是某一个函数不肯构造对象 —— 直接调的话，
        将来有人在这一趟里加一步「顺手也绑一下 snippet」，这条断言看不见它。
        代价是每扫一条 snippet 都要过一次 ``policy_of``；那份口径是进程里读一次
        就有的东西，而这条不变量值这个价。

        ``maintenance`` 在这里**必然用不上**，而它照样要传：这一趟一条证据都不写
        （下面三行断言就是这件事），所以 ``produce_routes`` 里那段「新落了证据行
        才记信号」的循环一次都不会进。让这个参数可选会是更省事的选择，但那等于
        给 ``produce_routes`` 开一个「可以不带维护账」的入口 —— 而真正需要它的
        那两个调用点会跟着一起可选，忘了注入就不再有东西拦得住。
        """
        if not snippet.strip():
            return
        production = produce_routes(
            materials=[
                RouteMaterial(
                    workspace=self._deps.settings.profile,
                    subject=BindingSubject.from_account_record(account),
                    text=snippet,
                    source_kind=SourceKind.SEARCH_SNIPPET,
                    source_url=url,
                    content_sha256="",
                    fetched_at=utc_now(),
                    origin=f"snippet:{url}",
                )
            ],
            bundle=self._deps.bundle,
            store=self._deps.store,
            maintenance=self._deps.maintenance,
            context=context,
            vocabulary=self._vocabulary,
        )
        metrics.snippet_bindings_rejected += len(production.rejected)
        assert not production.routes, "不变量被破坏：SERP snippet 产生了 ContactRoute"
        assert not production.opportunities, (
            "不变量被破坏：SERP snippet 产生了 RouteOpportunity"
        )
        assert not production.evidence, "不变量被破坏：SERP snippet 写入了 Evidence"

    # ------------------------------------------------------------------
    def _process_url(
        self,
        account: AccountRecord,
        url: str,
        metrics: AccountMetrics,
        context: RouteContext,
    ) -> tuple[list[ContactRoute], list[RouteOpportunity], list[str]]:
        request = FetchRequest(url=url, scope=account.account_id)

        fetched = self._deps.acquisition.fetch_document(request, mode=FetchMode.AUTO)
        self._tally_attempts(metrics, fetched)

        if fetched.final.body_path is None:
            return [], [], []

        return self._parse_and_bind(account, fetched.final, metrics, context)

    def _tally_attempts(self, metrics: AccountMetrics, fetched: DocumentFetch) -> None:
        """把一次 ``fetch_document`` 走过的**每一次**记进指标。

        逐次记而不是只看最终结果：``http_js_required`` 这类计数器的唯一来源就是
        那一次被升级掉的 HTTP 结果，只记最终结果会让「先 JS_REQUIRED 再浏览器成功」
        在报告上读成「HTTP 一次都没失败过」。

        ``browser_seconds`` 取编排层测的墙钟时间，不取 ``elapsed_ms`` —— 两个量不
        一样，理由写在 ``application/acquisition.py``。
        """
        for attempt in fetched.attempts:
            escalated = attempt.tier is FetchTier.BROWSER
            self._tally_fetch(metrics, attempt, escalated=escalated)
        if any(attempt.tier is FetchTier.BROWSER for attempt in fetched.attempts):
            metrics.browser_escalations += 1
            metrics.browser_seconds += fetched.browser_seconds

    def _parse_and_bind(
        self,
        account: AccountRecord,
        fetched: FetchResult,
        metrics: AccountMetrics,
        context: RouteContext,
    ) -> tuple[list[ContactRoute], list[RouteOpportunity], list[str]]:
        assert fetched.body_path is not None
        try:
            document = self._deps.router.parse(
                fetched.body_path, content_type=fetched.content_type
            )
        except ParseError:
            # 解析失败**不是** X（SOP L119）。它连一条候选都不是 ——
            # 这里返回空，计数记在 parse_failures 上。
            metrics.parse_failures += 1
            return [], [], []

        if document.is_empty:
            metrics.parse_failures += 1
            return [], [], []

        metrics.documents_parsed += 1

        # 绑定、判时效、定级、落盘**全在** ``application/contact_routes.py`` 里 ——
        # 服务面的 ``submit_findings`` 走的也是那一个用例。这里只剩「材料从哪来」：
        # CLI 这一侧的材料是**整篇文档**，因为文本已经在手上；服务面那一侧只能是
        # 本次结算**已引用**的证据行。两边共用同一条判据，就不存在「同一条路线
        # 经由两个入口落库、等级却不同」。
        production = produce_routes(
            materials=[
                RouteMaterial(
                    # 血统取自**配置**，不取自这份文档、也不取自账号：账号这一层没有
                    # Scene 这一维（那正是这三张表缺一格的原因），而文档只说它自己
                    # 是什么。同一条取值在 ``application/discovery_run.py`` 的
                    # ``_ingest`` 上，两处取到的必须是同一个值 —— 见
                    # ``adapters/profile_loader.py`` 那条「profile id 与工作区同名」。
                    workspace=self._deps.settings.profile,
                    # ``AccountRecord`` → ``BindingSubject`` 的投影只在这里做一次：
                    # 绑定这一层要的只有 id / 组织名 / 域三项，见 ``BindingSubject``。
                    subject=BindingSubject.from_account_record(account),
                    text=document.text,
                    source_kind=document.source_kind,
                    source_url=fetched.final_url or fetched.request.url,
                    content_sha256=document.sha256,
                    fetched_at=utc_now(),
                    parser=document.parser,
                    http_status=fetched.status_code,
                    tier=fetched.tier.value,
                    origin=f"fetch:{fetched.final_url or fetched.request.url}",
                )
            ],
            bundle=self._deps.bundle,
            store=self._deps.store,
            maintenance=self._deps.maintenance,
            context=context,
            vocabulary=self._vocabulary,
        )

        metrics.evidence_written += len(production.evidence)
        metrics.literal_emails_found += sum(
            1 for item in production.evidence if item.field and item.field.value == "email"
        )
        metrics.role_evidence_found += sum(
            1 for item in production.evidence if item.field and item.field.value == "role"
        )
        metrics.snippet_bindings_rejected += production.snippet_bindings_rejected

        for judgement in production.judgements:
            metrics.record_freshness(judgement)

        for route in production.routes:
            metrics.record_grade(route.grade)
            if route.has_conflict:
                metrics.conflicts += 1

        # 材料没过判据（例如页面上那个不可能的未来日期）：**只有这一页**作废，
        # 整轮继续。理由回流给 ``run_account`` 进 ``errors``，于是报告上看得见是
        # 哪一页、为什么 —— fail-soft 不等于不留痕，恰恰相反：一个被跳过的页面
        # 如果不在报告里，读者只会看到「这轮少了几行」。
        material_errors = [
            f"{outcome.origin}: SourceMaterialError: {outcome.reason}"
            for outcome in production.invalid_materials
        ]
        metrics.invalid_materials += len(material_errors)

        return (
            list(production.routes),
            list(production.opportunities),
            material_errors,
        )

    # ------------------------------------------------------------------
    @staticmethod
    def _tally_fetch(metrics: AccountMetrics, result: FetchResult, *, escalated: bool = False) -> None:
        match result.outcome:
            case FetchOutcome.OK:
                # 升级后成功不再计入 http_ok，避免同一 URL 被计两次。
                if not escalated:
                    metrics.http_ok += 1
            case FetchOutcome.BLOCKED:
                metrics.http_blocked += 1
            case FetchOutcome.EMPTY_SHELL:
                metrics.http_empty_shell += 1
            case FetchOutcome.JS_REQUIRED:
                metrics.http_js_required += 1
            case FetchOutcome.NOT_FOUND:
                metrics.http_not_found += 1
            case _:
                metrics.http_error += 1


def build_run_record(
    *, run_id: str, profile: str, accounts_total: int, completed: int, started_at
) -> ResearchRun:
    """构造运行记录。``research_only`` / ``send_authorized`` 由模型层强制。"""
    return ResearchRun(
        run_id=run_id,
        profile=profile,
        started_at=started_at,
        finished_at=utc_now(),
        accounts_total=accounts_total,
        accounts_completed=completed,
    )


__all__ = ["ResearchPipeline", "PipelineDeps", "AccountRunResult", "build_run_record"]
