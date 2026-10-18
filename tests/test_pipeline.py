"""Pipeline 端到端测试（全部使用 fake 依赖，零网络）。

这些测试同时是「本地 pipeline 能在没有 Serper key、没有飞书凭据的情况下
独立运行」这条验收标准的证据 —— 整个文件没有读过任何 secret。

本文件里的页面与账号都用 ``example.co.uk``，**不是**真实目标：真实名单与
真实抓取结果都不在仓库里。规则则来自真实 profile —— 因为这里要验证的正是
「SOP 的规则确实接在跑起来的链路上」，用一份合成规则测不出这件事。
"""

from __future__ import annotations

from pathlib import Path

from dataclasses import replace

from marketops.application.acquisition import DocumentAcquisition
from marketops.application.document_router import DocumentRouter
from marketops.ports.acquisition import BrowserFetcher, FetchRequest, FetchResult, HttpFetcher
from marketops.adapters.text_pdf import HtmlDocumentParser
from marketops.domain.enums import AccountStatus, BlockReason, FetchOutcome, FetchTier, Grade
from marketops.domain.evidence import AccountRecord
from marketops.adapters.memory import InMemoryBudgetLedger, InMemoryMaintenanceStore
from marketops.adapters.sqlite.evidence_store import SqliteEvidenceStore
from marketops.application.grading import grade_route
from marketops.application.pipeline import PipelineDeps, ResearchPipeline
from marketops.domain.profile import ProfileBundle
from marketops.ports.search import SearchHit, SearchProvider, SearchQuery, SearchResult


def _store(settings) -> SqliteEvidenceStore:
    """流水线测试用**主存**。

    这一层是这份检出上最接近集成测试的东西，所以它跑的是 pipeline 在真实调用点上
    真正拿到的东西。用 JSONL 跑会通过，而通过的路径在生产里不存在 —— 那种绿是
    关于一个没人用的实现的绿。
    """
    return SqliteEvidenceStore(settings.database_path)


PAGE_URL = "https://example.co.uk/contact"

#: 具名岗位 + 邮箱在**同一记录块**里 —— 真实 profile 下这是 B 级。
#: ``Grounds Manager`` 在 role-matrix 里是 operational，B 要求它。
NAMED_PAGE = """
<html><body>
  <h1>Example Coatings Ltd</h1>
  <p>Grounds Manager: Jane Doe</p>
  <p>Email: jane.doe@example.co.uk</p>
</body></html>
"""

#: 业务口径入口，没有具名人 —— 真实 profile 下这是 C 级。
ENTRY_PAGE = """
<html><body>
  <p>Grounds team: grounds@example.co.uk</p>
</body></html>
"""

#: 财务负责人 + 邮箱 + 强绑定 —— SOP L129 下这是**排除类别**，不是一条采购路线。
FINANCE_PAGE = """
<html><body>
  <p>Finance Director: Jane Doe</p>
  <p>Email: finance@example.co.uk</p>
</body></html>
"""

#: 有具名岗位、没有邮箱 —— 产出的是**路线机会**，不是路线（SOP L126）。
ROLE_ONLY_PAGE = """
<html><body>
  <p>Our Grounds Manager leads specification for the natural grass pitches.</p>
</body></html>
"""

SPA_SHELL = '<html><body><div id="root"></div></body></html>'

WAF_PAGE = "<html><title>Just a moment...</title><body>Checking your browser</body></html>"


class RecordingSearch(SearchProvider):
    """返回固定 URL，并可携带 snippet。"""

    name = "recording"

    def __init__(self, urls: list[str], *, snippet: str = "", page_size: int | None = None) -> None:
        self._urls = urls
        self._snippet = snippet
        #: 覆盖调用方传入的 limit。用于构造「单次检索就能返回很多结果」的场景
        #: （真实 provider 的页大小不受我们控制，测试里必须能显式设定）。
        self._page_size = page_size
        self.queries: list[str] = []

    def search(self, query: SearchQuery, *, limit: int = 10) -> SearchResult:
        self.queries.append(query.text)
        effective = limit if self._page_size is None else self._page_size
        hits = tuple(
            SearchHit(url=url, title="t", snippet=self._snippet, rank=i, query=query.text)
            for i, url in enumerate(self._urls[:effective], start=1)
        )
        return SearchResult(query=query, hits=hits, api_calls=1)


class FakeHttpFetcher(HttpFetcher):
    """按 URL 返回预置页面，并把页面写到磁盘供 parser 读取。"""

    name = "fake-http"

    def __init__(self, settings, pages: dict[str, tuple[FetchOutcome, str]]) -> None:
        self._settings = settings
        self._pages = pages
        self.calls: list[str] = []

    def fetch(self, request: FetchRequest) -> FetchResult:
        self.calls.append(request.url)
        outcome, body = self._pages.get(request.url, (FetchOutcome.NOT_FOUND, ""))

        body_path: Path | None = None
        if outcome in (FetchOutcome.OK, FetchOutcome.EMPTY_SHELL):
            directory = self._settings.mirrors_dir / "pages"
            directory.mkdir(parents=True, exist_ok=True)
            body_path = directory / f"{len(self.calls)}.html"
            body_path.write_text(body, encoding="utf-8")

        block = BlockReason.WAF_CHALLENGE if outcome is FetchOutcome.BLOCKED else None
        return FetchResult(
            request=request,
            outcome=outcome,
            tier=FetchTier.HTTP,
            final_url=request.url,
            status_code=200,
            content_type="text/html",
            body_path=body_path,
            block_reason=block,
        )


class RecordingBrowser(BrowserFetcher):
    """记录被调用次数 —— 用于验证「不该升级时没升级」。"""

    name = "recording-browser"

    def __init__(self, settings, outcome: FetchOutcome, body: str) -> None:
        self._settings = settings
        self._outcome = outcome
        self._body = body
        self.calls: list[str] = []

    def fetch(self, request: FetchRequest) -> FetchResult:
        self.calls.append(request.url)
        directory = self._settings.mirrors_dir / "pages"
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / "rendered.html"
        path.write_text(self._body, encoding="utf-8")
        return FetchResult(
            request=request,
            outcome=self._outcome,
            tier=FetchTier.BROWSER,
            final_url=request.url,
            status_code=200,
            content_type="text/html",
            body_path=path,
        )


def _deps(
    settings,
    bundle: ProfileBundle,
    *,
    search,
    http,
    browser=None,
    budget=None,
    maintenance=None,
) -> PipelineDeps:
    """默认给一个**内存**账本与一本**内存**维护账。

    用内存实现而不是 SQLite：这几条测试问的是「pipeline 有没有按两段式扣额度」，
    与并发无关 —— 并发正确性由 ``tests/test_budget_ledger.py`` 对着 SQLite 那份
    单独证明。``budget=`` 可覆盖，供「额度只有 N 次」这类判据使用。

    ``maintenance=`` 同理，供「这一趟落了新证据有没有记下变更信号」那一条判据
    把账本拿在手里看。其余测试拿到的是随手一本新账 —— 它们不关心信号，但
    ``PipelineDeps`` 上这一格没有缺省（理由写在那个 dataclass 上）。
    """
    return PipelineDeps(
        settings=settings,
        bundle=bundle,
        search=search,
        # 两个 fetcher 在这里合成一个统一入口（§10）。``_deps`` 的签名刻意不变：
        # 这份文件里问的是 pipeline 的行为，不是入口是怎么装配的 —— 入口自己的
        # 判据在 ``tests/test_acquisition_service.py``。
        acquisition=DocumentAcquisition(http=http, browser=browser),
        store=_store(settings),
        router=DocumentRouter([HtmlDocumentParser()]),
        budget=budget if budget is not None else InMemoryBudgetLedger(),
        maintenance=maintenance if maintenance is not None else InMemoryMaintenanceStore(),
    )


def _run(settings, bundle, account, *, page: str = NAMED_PAGE, url: str = PAGE_URL, browser=None):
    search = RecordingSearch([url])
    http = FakeHttpFetcher(settings, {url: (FetchOutcome.OK, page)})
    return ResearchPipeline(
        _deps(settings, bundle, search=search, http=http, browser=browser)
    ).run_account(account)


# --- 正常路径 -----------------------------------------------------------------


def test_pipeline_extracts_a_route_from_a_fetched_page(settings, bundle, account) -> None:
    result = _run(settings, bundle, account)

    metrics = result.metrics
    assert metrics.candidate_urls == 1
    assert metrics.http_ok == 1
    assert metrics.documents_parsed == 1
    assert metrics.literal_emails_found == 1
    assert metrics.routes == 1
    assert metrics.opportunities == 0
    assert result.routes[0].email == "jane.doe@example.co.uk"


def test_evidence_is_persisted(settings, bundle, account) -> None:
    _run(settings, bundle, account)

    stored = _store(settings).list_evidence(account.account_id)
    assert stored, "证据必须落盘 —— 否则结果无法审计"
    assert all(item.source_kind.value == "fetched_html" for item in stored)


def test_routes_are_persisted_separately_from_opportunities(settings, bundle, account) -> None:
    _run(settings, bundle, account)
    store = _store(settings)

    assert len(store.list_routes(account.account_id)) == 1
    assert store.list_opportunities(account.account_id) == []


def test_every_route_resolves_to_stored_evidence(settings, bundle, account) -> None:
    """每条路线上的每一个 evidence_id 都必须指向一条真的落盘了的证据。

    这是「判定可溯源」的最小形式：等级是从证据推出来的，把证据丢掉之后
    剩下的是一个结论和一句「相信我」。**悬空的 evidence_id 比没有 evidence_id
    更糟** —— 前者看起来像是查过了。
    """
    result = _run(settings, bundle, account)
    store = _store(settings)
    stored = {item.evidence_id: item for item in store.list_evidence(account.account_id)}

    assert stored, "没有任何证据落盘"
    for route in result.routes:
        assert route.evidence_ids, "非 X 的路线必须带证据"
        for evidence_id in route.evidence_ids:
            assert evidence_id in stored, f"路线引用了不存在的证据 {evidence_id}"


def test_evidence_can_be_traced_back_to_bytes(settings, bundle, account) -> None:
    """一条证据要能追回**具体字节**：URL 说「在哪」，sha256 说「哪一版」。

    只有 URL 是不够的 —— 页面是会变的，而「我们当时看到的是这个版本」
    正是这份记录要回答的问题。
    """
    _run(settings, bundle, account)

    for item in _store(settings).list_evidence(account.account_id):
        assert item.source_url.startswith("https://")
        assert item.content_sha256, "证据缺少内容哈希 —— 无法指回具体字节"
        assert item.fetched_at is not None
        assert item.literal_value.strip(), "证据的字面值是空的"


# --- 定级确实接在链路上 -------------------------------------------------------


def test_named_operational_role_grades_b(settings, bundle, account) -> None:
    """SOP L126 的 B 级：关键一线运营角色 + 真实公开邮箱 + 强绑定。

    这条断言的是「profile 的规则真的求了值」，而不是「引擎拒绝判定」——
    后者在收敛之前是本仓的常态，也是这次要结束的状态。
    """
    result = _run(settings, bundle, account)
    route = result.routes[0]

    assert route.grade is Grade.B
    assert route.binding.value == "strong"
    assert route.role_class.value == "operational"
    assert result.metrics.net_new_ab == 1
    assert result.metrics.grades == {Grade.B.value: 1}


def test_business_entry_mailbox_grades_c(settings, bundle, account) -> None:
    """SOP L127 的 C 级：与目标账号绑定、带场地业务语境的官方入口。"""
    result = _run(settings, bundle, account, page=ENTRY_PAGE)
    route = result.routes[0]

    assert route.entry_kind.value == "official_business_entry"
    assert route.grade is Grade.C
    assert result.metrics.net_new_cd == 1
    assert result.metrics.net_new_ab == 0


def test_finance_role_grades_x(settings, bundle, account) -> None:
    """SOP L129：财务属于 X。

    这条要走完整条链路才测得出来 —— 词表在 role-matrix 里、排除判定在绑定层、
    等级在 engine 里。旧 profile 把 ``finance director`` 放在 procurement 组里，
    于是一个财务负责人会被判成采购归口，而且**不会报任何错**：
    他有一个具名岗位、一个真实邮箱、一个强绑定，看起来完全像一条 A/B。
    """
    result = _run(settings, bundle, account, page=FINANCE_PAGE)
    route = result.routes[0]

    assert route.role_class.value == "excluded"
    assert route.entry_kind.value == "excluded"
    assert route.grade is Grade.X
    assert route.grade_reason.value == "excluded_entry"
    assert result.metrics.net_new_ab == 0


def test_role_without_email_becomes_an_opportunity(settings, bundle, account) -> None:
    """有具名岗位、没有邮箱：产出机会，**不**产出路线。

    这条守的是收敛里最重要的一处修正：旧 pipeline 把「有具名角色但没有邮箱」
    读成 B 级。那会让「拿到路线的比例」看起来比实际高，而且没有任何一处会报错。
    """
    result = _run(settings, bundle, account, page=ROLE_ONLY_PAGE)

    assert result.routes == []
    assert result.metrics.routes == 0
    assert len(result.opportunities) == 1
    assert result.metrics.opportunities == 1
    assert result.metrics.grades == {}
    assert result.metrics.produced_anything is True


def test_opportunity_is_persisted(settings, bundle, account) -> None:
    _run(settings, bundle, account, page=ROLE_ONLY_PAGE)

    stored = _store(settings).list_opportunities(account.account_id)
    assert len(stored) == 1
    assert stored[0].role_title.lower() == "grounds manager"
    assert stored[0].reason == "no_literal_email"


def test_blocked_page_produces_neither_routes_nor_opportunities(settings, bundle, account) -> None:
    """抓不到**不是** X（SOP L119）—— 它是 blocked，且不产出任何线路。"""
    search = RecordingSearch([PAGE_URL])
    http = FakeHttpFetcher(settings, {PAGE_URL: (FetchOutcome.BLOCKED, WAF_PAGE)})
    result = ResearchPipeline(_deps(settings, bundle, search=search, http=http)).run_account(
        account
    )

    assert result.metrics.http_blocked == 1
    assert result.routes == []
    assert result.opportunities == []
    assert result.metrics.grades == {}


# --- 浏览器升级边界 -----------------------------------------------------------


def test_http_ok_never_starts_browser(settings, bundle, account) -> None:
    """需求原文：HTTP 成功就不要启动浏览器。"""
    browser = RecordingBrowser(settings, FetchOutcome.OK, NAMED_PAGE)
    metrics = _run(settings, bundle, account, browser=browser).metrics

    assert browser.calls == []
    assert metrics.browser_escalations == 0
    assert metrics.browser_seconds == 0.0


def test_empty_shell_escalates_to_browser(settings, bundle, account) -> None:
    search = RecordingSearch([PAGE_URL])
    http = FakeHttpFetcher(settings, {PAGE_URL: (FetchOutcome.EMPTY_SHELL, SPA_SHELL)})
    browser = RecordingBrowser(settings, FetchOutcome.OK, NAMED_PAGE)

    result = ResearchPipeline(
        _deps(settings, bundle, search=search, http=http, browser=browser)
    ).run_account(account)

    assert browser.calls == [PAGE_URL]
    assert result.metrics.browser_escalations == 1
    assert result.metrics.browser_seconds >= 0.0
    assert result.routes[0].email == "jane.doe@example.co.uk"


def test_blocked_does_not_escalate(settings, bundle, account) -> None:
    """明确受阻 → 不得硬闯，浏览器也不行。"""
    search = RecordingSearch([PAGE_URL])
    http = FakeHttpFetcher(settings, {PAGE_URL: (FetchOutcome.BLOCKED, WAF_PAGE)})
    browser = RecordingBrowser(settings, FetchOutcome.OK, NAMED_PAGE)

    metrics = ResearchPipeline(
        _deps(settings, bundle, search=search, http=http, browser=browser)
    ).run_account(account).metrics

    assert browser.calls == []
    assert metrics.http_blocked == 1
    assert metrics.routes == 0


def test_no_browser_available_still_completes(settings, bundle, account) -> None:
    """没装浏览器时 pipeline 仍应跑完 —— 只是浏览器指标为 0。"""
    search = RecordingSearch([PAGE_URL])
    http = FakeHttpFetcher(settings, {PAGE_URL: (FetchOutcome.EMPTY_SHELL, SPA_SHELL)})

    metrics = ResearchPipeline(
        _deps(settings, bundle, search=search, http=http, browser=None)
    ).run_account(account).metrics

    assert metrics.browser_escalations == 0
    assert metrics.http_empty_shell == 1


def test_the_composition_root_answers_whether_a_browser_is_wired(settings) -> None:
    """``--no-browser`` 关掉的是**接线**，不是「这次先别升级」。

    ``allow_browser=False`` 必须落成 ``browser=None``（→ ``browser_not_wired``），
    而不是一个 ``available`` 为假的 fetcher（→ ``browser_unavailable``）：
    前者说的是「这个部署没有浏览器」，后者说的是「装了但依赖没装」，
    修法一个是改构建、一个是装 Playwright。这条判据钉住那个映射 ——
    它是 CLI 那两个词与回执里那两句话之间唯一的一次翻译。
    """
    from marketops.interfaces.composition import default_acquisition

    off = default_acquisition(settings, allow_browser=False)
    on = default_acquisition(settings, allow_browser=True)

    # 这里读的是私有字段，因为它问的就是**接线事实本身**（接没接），而不是
    # 任何可观察行为：能观察到「没接线」的那条路要发一次请求才会显形，
    # 而本条判据必须零 I/O。``allow_browser=True`` 时**不能**用
    # ``mode=BROWSER`` 去做正控 —— 装了 crawl4ai 的机器上那会真的开浏览器。
    assert off._browser is None
    assert on._browser is not None, "正控：默认必须真的接上（可用与否由它自己答）"


# --- SERP snippet 不进入证据链 -------------------------------------------------


def test_snippet_emails_are_counted_as_rejected(settings, bundle, account) -> None:
    """snippet 里的邮箱被拒绝，而且是**可观测地**被拒绝。"""
    search = RecordingSearch([PAGE_URL], snippet="Reach us at snippet@example.co.uk")
    http = FakeHttpFetcher(settings, {PAGE_URL: (FetchOutcome.OK, NAMED_PAGE)})

    result = ResearchPipeline(
        _deps(settings, bundle, search=search, http=http)
    ).run_account(account)

    assert result.metrics.snippet_bindings_rejected == 1
    # 关键：snippet 里的邮箱没有出现在结果里
    assert all(route.email != "snippet@example.co.uk" for route in result.routes)
    stored = _store(settings).list_evidence(account.account_id)
    assert all(item.literal_value != "snippet@example.co.uk" for item in stored)


# --- 检索与去重 ---------------------------------------------------------------


def test_queries_are_generated_from_account(settings, bundle, account) -> None:
    search = RecordingSearch([PAGE_URL])
    http = FakeHttpFetcher(settings, {})

    metrics = ResearchPipeline(
        _deps(settings, bundle, search=search, http=http)
    ).run_account(account).metrics

    assert metrics.serp_queries >= 3
    assert any("Example Coatings Ltd" in query for query in search.queries)
    assert any(str(account.domain) in query for query in search.queries)


def test_duplicate_urls_are_fetched_once(settings, bundle, account) -> None:
    """多个 query 返回同一 URL 时只抓一次。

    去重是**按账号全局**的，不是按 query —— 每个 query 各返回一次同一 URL，
    整个账号仍然只抓一次。否则同一个页面会被重复抓 N 遍，白烧带宽与浏览器时间。
    """
    search = RecordingSearch([PAGE_URL, PAGE_URL, PAGE_URL])
    http = FakeHttpFetcher(settings, {PAGE_URL: (FetchOutcome.OK, NAMED_PAGE)})

    metrics = ResearchPipeline(
        _deps(settings, bundle, search=search, http=http)
    ).run_account(account).metrics

    assert metrics.candidate_urls == 1
    assert len(http.calls) == 1
    assert len(search.queries) > 1, "本测试要验证的是跨 query 去重，搜索必须被调用多次"


def test_candidate_cap_is_respected(settings, bundle, account) -> None:
    """单次检索能返回很多结果时，候选数仍受 max_candidates_per_account 限制。

    **并且被砍掉的那部分要有自己的数字。** 只断言「留下 12 条」时，
    「只有 12 个候选」与「有 30 个、被上限砍到 12」在这条测试里同形 ——
    而它们是两个不同的结论（``docs/CURRENT_STATE.md`` §12 地雷 #2）。
    ``candidate_urls_dropped`` 就是那个差。
    """
    urls = [f"https://example.co.uk/p{i}" for i in range(30)]
    search = RecordingSearch(urls, page_size=len(urls))  # 否则会被默认 limit=10 截断
    http = FakeHttpFetcher(settings, {})

    metrics = ResearchPipeline(
        _deps(settings, bundle, search=search, http=http)
    ).run_account(account).metrics

    assert metrics.candidate_urls == settings.max_candidates_per_account
    assert metrics.candidate_urls_dropped == len(urls) - settings.max_candidates_per_account
    assert metrics.candidate_urls + metrics.candidate_urls_dropped == len(urls)


def test_a_pool_that_fits_the_cap_reports_no_truncation(settings, bundle, account) -> None:
    """装得下时 ``dropped == 0`` —— 界限就在上限那一点上，不是「有上限就算截断」。

    这条与上一条是一对：上一条证明差值被记下来了，这一条证明它**只在真的丢了东西时**
    才非零。少了这一条，一个恒等于 ``30 - cap`` 的字段也能全绿 —— 而那会让「满额」
    与「超限」在报告上同形，恰好是这次要修的那件事。
    """
    cap = settings.max_candidates_per_account
    urls = [f"https://example.co.uk/p{i}" for i in range(cap)]
    search = RecordingSearch(urls, page_size=len(urls))
    http = FakeHttpFetcher(settings, {})

    metrics = ResearchPipeline(
        _deps(settings, bundle, search=search, http=http)
    ).run_account(account).metrics

    assert metrics.candidate_urls == cap
    assert metrics.candidate_urls_dropped == 0


# --- 报告汇总 -----------------------------------------------------------------


def test_run_report_aggregates_cohort_metrics(settings, bundle, account, blocked_account) -> None:
    from marketops.application.cohorts import CohortPlan

    search = RecordingSearch([PAGE_URL])
    http = FakeHttpFetcher(settings, {PAGE_URL: (FetchOutcome.BLOCKED, WAF_PAGE)})

    report = ResearchPipeline(_deps(settings, bundle, search=search, http=http)).run(
        [account, blocked_account]
    )

    payload = report.to_dict()
    assert payload["accounts_total"] == 2
    assert payload["net_new_ab"] == 0
    assert payload["still_blocked"] + payload["blocked_recovery"] == 1
    assert payload["false_attribution"] == 0
    assert CohortPlan().total == 30


def test_run_id_defaults_are_slug_safe(settings, bundle, account) -> None:
    search = RecordingSearch([])
    http = FakeHttpFetcher(settings, {})

    report = ResearchPipeline(_deps(settings, bundle, search=search, http=http)).run([account])
    assert report.run_id
    assert "/" not in report.run_id


# --- 与 grading 的接线 ---------------------------------------------------------


def test_graded_route_matches_engine(bundle, settings, account) -> None:
    """pipeline 的 grade 必须等于 engine 的结论 —— 不允许两套判定。"""
    from marketops.domain.bind import BindingSubject, RouteVocabulary, bind_from_text
    from marketops.domain.enums import SourceKind
    from marketops.domain.util import utc_now

    bound = bind_from_text(
        # 血统取部署配置：pipeline 自己也是这么写的（``_parse_and_bind`` 传
        # ``settings.profile``），所以这条测试测的是同一条路径上的值。
        workspace=settings.profile,
        subject=BindingSubject.from_account_record(account),
        text=NAMED_PAGE,
        source_kind=SourceKind.FETCHED_HTML,
        source_url=PAGE_URL,
        content_sha256="x",
        fetched_at=utc_now(),
        vocabulary=RouteVocabulary.from_bundle(bundle),
    )
    expected = grade_route(bound.routes[0], bundle, account_domains=(account.domain,))
    assert expected.grade is Grade.B
    assert expected.reason.value == "rule_matched"


def test_account_grade_is_available_and_does_not_touch_routes(bundle, account) -> None:
    """账号准入判定是**另一条**链路：它返回结论，不改写任何东西。

    当前真实状态是：SOP 的排除规则引用的是还没有生产者的事实，因此它们
    一律进 ``unchecked``，账号停在 ``discovered``。这里把它断言下来 ——
    「没有规则命中」与「规则求不了值」在审计上是两件事。
    """
    from marketops.application.grading import grade_account

    outcome = grade_account(account, bundle)

    assert outcome.status is AccountStatus.DISCOVERED
    assert outcome.unchecked, "真实 profile 的排除规则目前都还求不了值"
    assert outcome.excludes is False


def test_account_without_domain_skips_site_queries(settings, bundle) -> None:
    from marketops.domain.enums import Cohort

    plain = AccountRecord(account_id="no-domain", organisation="No Domain Ltd", cohort=Cohort.KNOWN_SUCCESS)
    search = RecordingSearch([])
    http = FakeHttpFetcher(settings, {})

    metrics = ResearchPipeline(
        _deps(settings, bundle, search=search, http=http)
    ).run_account(plain).metrics

    assert metrics.serp_queries == 3
    assert not any("site:" in query for query in search.queries)


# --- 时效（§9·2b）------------------------------------------------------------
#
# 时限口径来自 bundle 的声明，**没有声明就失败关闭** —— 这两半都要在这里有执行器，
# 因为这条链路上「一次没有口径的运行」与「一次判过的运行」在报告上唯一能区分的地方
# 就是这里。


def test_every_written_route_carries_a_freshness_conclusion(settings, bundle, account) -> None:
    """落盘的每一行都有判定结论，且结论说的是它自己那一行。

    这一条同时钉住「判的是落盘的那一行」：``freshness.assertion_id`` 必须等于
    这一行自己算出来的那个 id（行的判据也这么要求，所以它一旦不一致，写库那一步
    就会红 —— 构造上不可能悄悄挂错行）。
    """
    result = _run(settings, bundle, account)

    route = result.routes[0]
    assert route.freshness is not None
    assert route.freshness.assertion_id == route.expected_assertion_id


def test_freshness_conclusion_names_the_policy_it_used(settings, bundle, account) -> None:
    """结论上要留下口径与它的标定状态 —— 版本答「按什么算的」，布尔答「认过没有」。"""
    result = _run(settings, bundle, account)

    fresh = result.routes[0].freshness
    assert bundle.freshness is not None
    assert fresh is not None
    assert fresh.policy_version == bundle.freshness.policy_version
    assert fresh.policy_calibrated is bundle.freshness.calibrated


def test_freshness_is_counted_separately_from_not_being_judged(settings, bundle, account) -> None:
    """判过的行数与**判据没能开工**的行数分开记。

    这条页面没有来源发布时间，所以结论是 ``unknown``（判了，判据给的答案是不足），
    而不是「没判」——后者只在没有 ``first_seen_at`` 时发生。
    """
    metrics = _run(settings, bundle, account).metrics

    assert metrics.freshness_judged == 1
    assert metrics.freshness_without_a_time_anchor == 0
    assert metrics.currentness == {"unknown": 1}


def test_opportunities_are_judged_too(settings, bundle, account) -> None:
    """路线机会与路线走同一条判定 —— 只接一半会让机会那一族永远没有时效。"""
    result = _run(settings, bundle, account, page=ROLE_ONLY_PAGE)

    assert result.metrics.opportunities == 1
    assert result.metrics.freshness_judged == 1
    assert result.opportunities[0].freshness is not None


def test_the_conclusion_survives_the_store_round_trip(settings, bundle, account) -> None:
    """结论真的落进库里，再读出来还是同一格（**不需要改表结构**）。

    行是整体以 JSON 落进 ``payload`` 的，所以加一格可选字段不动 DDL —— 这条测试
    是那句话的执行器。

    它读的是**库**里的那一行，不是返回值 —— 这两者在今天等价，而它们的区别正是
    突变测试找出来的：把落库那一步改回「写判之前的那一行」，返回给调用方的列表
    仍然带着结论，只有库里的没有。
    """
    result = _run(settings, bundle, account)
    stored = _store(settings).list_routes(account.account_id)

    assert len(stored) == 1
    assert stored[0].route.freshness == result.routes[0].freshness


def test_the_stored_opportunity_carries_its_conclusion_too(settings, bundle, account) -> None:
    """机会那一族也要落**判过**的那一行。

    与上一条同一条理由，只是换了一族：只断言返回值的话，「机会不判」这个改动
    在报告上完全看不出来。
    """
    result = _run(settings, bundle, account, page=ROLE_ONLY_PAGE)
    stored = _store(settings).list_opportunities(account.account_id)

    assert len(stored) == 1
    assert stored[0].freshness is not None
    assert stored[0].freshness == result.opportunities[0].freshness


def test_the_stored_route_carries_the_grade(settings, bundle, account) -> None:
    """落库的那一行必须带着**定级之后**的等级与原因。

    这一条是突变测试逼出来的：在此之前，整份套件里没有任何一条断言读过**库里**
    那一行的 ``grade``（各条测试读的都是 pipeline 返回的列表）。「返回的是定级过的、
    写库的是原始的」这种错法因此在报告上不留痕迹 —— 下游读的正是库。
    """
    result = _run(settings, bundle, account)
    stored = _store(settings).list_routes(account.account_id)

    assert len(stored) == 1
    assert stored[0].route.grade is Grade.B, "这条页面在真实 profile 下是 B —— 否则本测试不作数"
    assert stored[0].route.grade == result.routes[0].grade
    assert stored[0].route.grade_reason == result.routes[0].grade_reason


def test_a_run_without_a_declared_policy_fails_closed(settings, bundle, account) -> None:
    """没有时限声明 ⇒ **这一行产出不了**，而且报告上看得见原因。

    不许悄悄取一组天数顶上：那组数字没有任何人认过，而每一行 ``currentness``
    都会显得是按某个业务标准算出来的。
    """
    undeclared = replace(bundle, freshness=None)

    result = _run(settings, undeclared, account)

    assert result.routes == []
    assert result.opportunities == []
    assert result.metrics.freshness_judged == 0
    assert len(result.errors) == 1
    assert "ConfigError" in result.errors[0]
    assert "freshness" in result.errors[0], "报错必须点名缺的是哪一份声明"
    assert _store(settings).list_routes(account.account_id) == []
