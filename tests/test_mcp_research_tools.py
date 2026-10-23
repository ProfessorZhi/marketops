"""研究侧两个工具（``search_web`` / ``fetch_document``）的行为 —— ``MCP_SERVICE.md`` §3。

## 全部走真实服务端，零网络、零凭据

判据经 ``tests/mcp_harness.py`` 起**真的** ``MCPServer``：真的角色判定、真的工具
注册、真的参数校验。这两个工具是目录里唯一会真的花钱的一对，所以它们的前置条件
（有没有接线检索、额度还剩几次）必须经同一条链才读得到 —— 直接调 ``_search_web()``
会把 ``RoleGuard`` 与 schema 校验整段绕过去，而「谁能调、什么参数算合法」正是这一
阶段承重的东西。

``search`` / ``acquisition`` / ``router`` 是注入的 fake（``research_handlers`` 本来
就要求注入）。唯一真实的是**账本**：预算是本阶段唯一跨进程正确的状态，用内存替身
测它等于不测 —— 而「额度还剩几次」恰好是下面好几条判据的主语。

## 每条「不该发生」旁边都有一条「该发生」

「额度没被动过」「没有发出第二次检索」这类断言有一个廉价的反面：把被测功能整个
删掉，它们同样成立。所以每一条旁边都有一条同形的用例证明它在正常工作 ——
``RecordingSearch.sent`` 既是断言的对象，也是那条正控的载体。
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable, Iterator, Mapping, Sequence
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import pytest

from marketops.adapters.mcp_server.research import (
    MAX_CRAWL_SECONDS,
    MAX_DISCOVERY_LINES,
    MAX_LINKS_RETURNED,
    MAX_PAGES_PER_CALL,
    MAX_SEEDS_PER_CALL,
    MAX_URLS_RETURNED,
    research_handlers,
)
from marketops.adapters.memory import InMemoryBudgetLedger, InMemoryEvidenceStore
from marketops.adapters.profile_loader import load_profile
from marketops.adapters.sqlite.evidence_store import SqliteEvidenceStore
from marketops.adapters.sqlite.jobs import SqliteJobStore
from marketops.adapters.sqlite.ledger import SqliteBudgetLedger
from marketops.adapters.text_pdf import HtmlDocumentParser
from marketops.application.acquisition import DocumentAcquisition
from marketops.application.document_router import DocumentRouter
from marketops.application.inputs import discovery_scope_subjects
from marketops.application.openweb import OPEN_WEB_SOURCE_METHOD
from marketops.application.queries import market_locale
from marketops.application.site_crawl import SiteCrawl, StopReason
from marketops.application.sitemap_frontier import SitemapFrontier
from marketops.config import Settings
from marketops.domain.access import ActionTaken
from marketops.domain.access_governance import AccessGovernor
from marketops.domain.budget import BudgetUsage
from marketops.domain.enums import (
    BINDING_ELIGIBLE_SOURCES,
    BlockReason,
    BudgetLine,
    FetchOutcome,
    JobInputKind,
    QueryOutcome,
    SourceKind,
)
from marketops.domain.errors import EvidenceError, SearchError, StoreError
from marketops.domain.evidence import Evidence
from marketops.domain.profile import ProfileBundle
from marketops.domain.sources import SOURCE_METHODS
from marketops.domain.subject import SubjectDossier
from marketops.domain.util import sha256_text
from marketops.interfaces.composition import default_acquisition, default_http
from marketops.ports.documents import DocumentParser, ParsedDocument
from marketops.ports.acquisition import (
    BrowserFetcher,
    FetchMode,
    FetchRequest,
    FetchResult,
    HttpFetcher,
)
from marketops.ports.jobs import JobStore
from marketops.ports.search import SearchHit, SearchProvider, SearchQuery, SearchResult
from marketops.ports.store import EvidenceStore
from marketops.security.catalog import TOOL_CATALOG, Role
from marketops.security.tokens import TokenRing, mint
from tests.fixtures.http_fakes import FakeClock, FakeHttp, SlowHttp, site_replies
from tests.fixtures.http_transport import CHALLENGE_BODY, make_fetcher, page
from tests.fixtures.site_content import page_html
from tests.mcp_harness import SessionOutcome, drive_with_settings, ring_entry, ring_payload

PAGE_URL = "https://example.co.uk/contact"
PAGE_HTML = "<html><body><p>Grounds Manager: Jane Doe</p></body></html>"
ACCOUNT = "test-account-001"

#: 多页抓取那几条判据用的站。它与 ``PAGE_URL`` 同主机，但**站点地图要另摆** ——
#: 前沿读的是 robots 与站点地图两份文档，而不是调用方给的那一个 URL。
SITE = "https://example.co.uk"
ROBOTS = f"{SITE}/robots.txt"
SITEMAP = f"{SITE}/sitemap.xml"

#: 这个站的页面。``*/contact*``（high）与 ``*/``（low）都是**冻结 profile 里真的
#: 写着**的提示（见 ``profiles/*/profile.yaml`` 的 ``page_discovery_hints``），
#: 所以下面那条「tier 来自 profile」的判据是真的走了一遍声明，而不是照着我编的
#: 词表跑 —— 换一份 profile，它会跟着变。
SITE_PAGES = (f"{SITE}/", f"{SITE}/contact-us", f"{SITE}/contact-form")

CrawlFactory = Callable[[Mapping[str, Sequence[str]]], SiteCrawl]

#: ``run_discovery`` 那一节用的三台主机。**必须是三台不同的主机** —— 池子的单位
#: 是站点，同主机的一串 URL 折进池子只是一个候选（见 ``HostSearch``）。
DISCOVERY_HOSTS = (
    "alpha-club.example.co.uk",
    "bravo-club.example.co.uk",
    "charlie-club.example.co.uk",
)

#: 这一轮发现的名字。它同时是**工件名**（``DiscoveryScope.artifact_ref``），
#: 所以它要过 ``check_canonical_id`` —— 不是随便一个字符串。
DISCOVERY_SCOPE = "devon-discovery-v1"

#: 绑给检索式里 ``{region}`` 的冻结值。**必填、没有缺省值**，所以这一节里
#: 每一条正控都要显式给。
DISCOVERY_REGION = "Devon"


# ----------------------------------------------------------------------
# 替身
# ----------------------------------------------------------------------
class RecordingSearch(SearchProvider):
    """一个会记账的检索替身。

    ``fail_on`` 里的查询抛 ``SearchError`` —— 用来把「调用失败」与「额度用尽」
    两条路径分开测，它们在本仓是两种不同的终态，处置也不同。
    """

    name = "recording-search"

    def __init__(self, *, hits: int = 1, fail_on: frozenset[str] = frozenset()) -> None:
        self._hits = hits
        self._fail_on = fail_on
        self.sent: list[SearchQuery] = []
        self.limits: list[int] = []

    def search(self, query: SearchQuery, *, limit: int = 10) -> SearchResult:
        self.sent.append(query)
        self.limits.append(limit)
        if query.text in self._fail_on:
            raise SearchError(f"替身被要求在这条查询上失败：{query.text!r}")
        return SearchResult(
            query=query,
            hits=tuple(
                SearchHit(
                    url=f"https://example.co.uk/{index}",
                    title=f"Result {index}",
                    snippet="a serp snippet, discovery only",
                    rank=index,
                    query=query.text,
                )
                for index in range(self._hits)
            ),
            api_calls=1,
        )


class RecordingHttpFetcher(HttpFetcher):
    """把每个请求的 URL 记下来，成功时往 ``directory`` 落一份可解析的字节。

    ``directory`` 必须落在 ``settings.data_root`` 之下：页面镜像在真实路径上就在
    那里，而 ``body_ref`` 的相对性正是靠这一层关系成立的。写到别处得到的是那条
    下降分支（返回绝对路径），于是「引用是相对的」这条判据会被一条与生产无关的
    测试布置满足。
    """

    name = "recording-http"

    def __init__(
        self,
        *,
        directory: Path | None = None,
        outcome: FetchOutcome = FetchOutcome.OK,
        body: str = PAGE_HTML,
        content_type: str = "text/html",
    ) -> None:
        self._directory = directory
        self._outcome = outcome
        self._body = body
        self._content_type = content_type
        self.calls: list[str] = []

    def fetch(self, request: FetchRequest) -> FetchResult:
        self.calls.append(request.url)
        body_path: Path | None = None
        if self._outcome is FetchOutcome.OK and self._directory is not None:
            self._directory.mkdir(parents=True, exist_ok=True)
            body_path = self._directory / f"body-{len(self.calls)}.html"
            body_path.write_text(self._body, encoding="utf-8")
        return FetchResult(
            request=request,
            outcome=self._outcome,
            tier=self.tier,
            final_url=request.url,
            status_code=200 if self._outcome is FetchOutcome.OK else None,
            content_type=self._content_type,
            body_path=body_path,
            body_sha256="a" * 64 if body_path is not None else "",
            body_bytes=len(self._body.encode("utf-8")),
            reason="替身：预置结果",
        )


class RecordingBrowserFetcher(BrowserFetcher):
    """浏览器侧的同一个形状。``available`` 可关：「没接线」与「装了但用不了」是两件事。"""

    name = "recording-browser"

    def __init__(self, *, available: bool = True) -> None:
        self._available = available
        self.calls: list[str] = []

    @property
    def available(self) -> bool:
        return self._available

    def fetch(self, request: FetchRequest) -> FetchResult:
        self.calls.append(request.url)
        return FetchResult(
            request=request,
            outcome=FetchOutcome.OK,
            tier=self.tier,
            final_url=request.url,
            status_code=200,
            content_type="text/html",
            reason="替身：浏览器预置结果",
        )


def _http(settings: Settings, **kwargs) -> RecordingHttpFetcher:
    return RecordingHttpFetcher(directory=settings.data_root / "pages", **kwargs)


class HostSearch(SearchProvider):
    """每台主机出一条命中的检索替身 —— 池子的单位是**站点**。

    ``RecordingSearch`` 的命中全挂在 ``example.co.uk`` 下（``/0``、``/1``……），
    按主机折进池子只剩**一个**候选。用它测上限、测截断、测逐条读数，得到的都是
    一个恒为 1 的数 —— 而这几条判据要的正是「多台主机」这个形状。

    ``hits_per_host`` 大于 1 时同一台主机会有多条命中：它们折进**同一个**站点
    （其余落进 ``also_seen_urls``），这正是「一台主机挂多个单位时这一轮只看得见
    一条」那条边界的载体，也是「命中数 ≠ 站点数」的来源。
    """

    name = "host-search"

    def __init__(
        self, *, hosts: Sequence[str] = DISCOVERY_HOSTS, hits_per_host: int = 1
    ) -> None:
        self._hosts = tuple(hosts)
        self._per_host = hits_per_host
        self.sent: list[SearchQuery] = []
        self.limits: list[int] = []

    def search(self, query: SearchQuery, *, limit: int = 10) -> SearchResult:
        self.sent.append(query)
        self.limits.append(limit)
        hits: list[SearchHit] = []
        rank = 0
        for host in self._hosts:
            for index in range(self._per_host):
                rank += 1
                hits.append(
                    SearchHit(
                        url=f"https://{host}/page-{index}",
                        title=f"{host} page {index}",
                        snippet="a serp snippet, discovery only",
                        rank=rank,
                        query=query.text,
                    )
                )
        return SearchResult(query=query, hits=tuple(hits), api_calls=1)


class TitledHttpFetcher(RecordingHttpFetcher):
    """``RecordingHttpFetcher``，只是每一页**自己写着自己的名字**。

    父类的正文是固定的一份 ``PAGE_HTML``，而它没有 ``<title>`` —— 于是
    ``usable_name`` 拿到空串，每一台站点都进不了池子。``DiscoverySite.named``
    问的不是「这一页存在吗」，是「这一页说它自己叫什么」；要测池子，就得先有一页
    真的写了名字的页面。

    名字由主机名派生，但**不是**主机名本身：``usable_name`` 会把「名称就是来源
    标识」的那一格判成不是名字（``application/motherpool.py`` 的第 3 条），而那
    是另一个缺口的东西，不是这一节要测的。
    """

    name = "titled-http"

    def fetch(self, request: FetchRequest) -> FetchResult:
        self._body = self._page_for(request.url)
        return super().fetch(request)

    def _page_for(self, url: str) -> str:
        label = (urlparse(url).hostname or "").split(".")[0]
        spoken = label.replace("-", " ").title()
        return (
            f"<html><head><title>{spoken} Grounds Association</title></head>"
            "<body><p>Grounds Manager: contact the grounds team.</p></body></html>"
        )


def _titled(settings: Settings) -> TitledHttpFetcher:
    return TitledHttpFetcher(directory=settings.data_root / "pages")


def _ring(tmp_path: Path, name: str, role: Role) -> tuple[TokenRing, str]:
    """铸一个只属于某个角色的令牌环，并返回 ``(环, 令牌)``。"""
    token, digest = mint(name, [role])
    ring_path = tmp_path / f"{name}.json"
    ring_path.write_text(
        json.dumps(
            ring_payload(ring_entry(digest, name, [role.value]))
        ),
        encoding="utf-8",
    )
    return TokenRing.from_file(ring_path), token


def _usage(ledger: SqliteBudgetLedger, scope: str) -> BudgetUsage | None:
    """这个 scope 的检索用量；一行都没有时返回 ``None``。

    ``None`` 与「用量为 0」是两件事：前者表示这次调用连上限都没播种过，也就
    不可能预留过什么；后者表示有人动过这个 scope。
    """
    return next(
        (item for item in ledger.usage(scope) if item.line is BudgetLine.SEARCH), None
    )


def _scope(bundle: ProfileBundle, account_id: str = ACCOUNT) -> str:
    """本仓的额度是**按账号**记的，所以 scope 里带着账号。

    缺省是这个文件里绝大多数用例的那个账号；§5 那一对判据用的是自己的两个主体，
    它们各自有各自的账 —— 一次拒绝不该在**别的**账号的账上留下痕迹，那正是那条
    「``usage`` 是 ``None``」要断言的事。
    """
    return f"{bundle.profile_id}:{account_id}"


# ----------------------------------------------------------------------
# 夹具
# ----------------------------------------------------------------------
@pytest.fixture
def ledger(settings: Settings) -> SqliteBudgetLedger:
    """真实账本。见模块 docstring：预算是本阶段唯一跨进程正确的状态。"""
    return SqliteBudgetLedger(settings.database_path)


@pytest.fixture
def evidence(settings: Settings) -> SqliteEvidenceStore:
    """真实证据库。与账本同一条理由，而且更直接：

    P0-2 那次事故的形状**就是**「SQLite 里 ``evidence = 0``」—— 内存替身在这里
    恰好会把它遮掉（内存实现当然写得进去）。要钉住的是「抓到的材料进了那个真的
    会被重连后读到的库」，所以这里用真的那一个。
    """
    return SqliteEvidenceStore(settings.database_path)


@pytest.fixture
def researcher(
    settings: Settings,
    tmp_path: Path,
    ledger: SqliteBudgetLedger,
    evidence: SqliteEvidenceStore,
):
    """研究侧会话的驱动器：给定依赖与调用，跑完一整段真实会话并返回观察结果。"""
    ring, token = _ring(tmp_path, "claude-research", Role.RESEARCHER)

    def call(
        tool: str,
        arguments: dict | None = None,
        *,
        search: SearchProvider | None = None,
        http: HttpFetcher | None = None,
        browser: BrowserFetcher | None = None,
        router: DocumentRouter | None = None,
        crawl: CrawlFactory | None = None,
        store: EvidenceStore | None = None,
        jobs: Callable[[], JobStore] | None = None,
    ):
        handlers = research_handlers(
            settings=settings,
            search=search,
            acquisition=DocumentAcquisition(
                http=http if http is not None else _http(settings), browser=browser
            ),
            router=router if router is not None else DocumentRouter([HtmlDocumentParser()]),
            ledger_factory=lambda: ledger,
            evidence_store_factory=lambda: store if store is not None else evidence,
            crawl=crawl,
        )
        outcome = drive_with_settings(
            settings=settings,
            tokens=ring,
            token=token,
            research=handlers,
            calls=((tool, arguments or {}),),
            want_tool_list=False,
            # 缺省 ``None`` = 这个实例没有作业存储。那是研究侧**绝大多数**用例的
            # 常态（这两个工具与作业无关），也是一个要被测的对象；只有 §5 那条
            # 「worker 照着 assignment 走会花几次」需要一个接得上的作业存储。
            jobs=jobs,
        )
        assert not outcome.handshake_error, outcome.handshake_error
        return outcome.call(tool)

    return call


@pytest.fixture
def controller(
    settings: Settings,
    tmp_path: Path,
    ledger: SqliteBudgetLedger,
    evidence: SqliteEvidenceStore,
):
    """控制器会话的驱动器 —— ``run_discovery`` 在目录里是 ``_CONTROL``。

    与 ``researcher`` 同一个形状，只差令牌的**角色**，而角色是这一节承重的那一格：
    ``run_discovery`` 花的是冻结的检索额度，所以能调它的人不是研究 worker。两半
    都要有用例 —— 控制器调得动（下面那一批正控），研究令牌既看不见也调不动
    （``test_a_researcher_cannot_see_or_call_a_discovery_run``），否则「谁能调」
    只是目录里的一行字。

    其余依赖与 ``researcher`` 逐字相同（真账本、真证据库、注入的检索与抓取），
    理由也一样：额度与证据是本阶段唯一跨进程正确的两样状态，用内存替身测它们
    等于不测。
    """
    ring, token = _ring(tmp_path, "claude-control", Role.CONTROLLER)

    def call(
        tool: str,
        arguments: dict | None = None,
        *,
        search: SearchProvider | None = None,
        http: HttpFetcher | None = None,
        want_tool_list: bool = False,
    ):
        handlers = research_handlers(
            settings=settings,
            search=search,
            acquisition=DocumentAcquisition(
                http=http if http is not None else _http(settings)
            ),
            router=DocumentRouter([HtmlDocumentParser()]),
            ledger_factory=lambda: ledger,
            evidence_store_factory=lambda: evidence,
        )
        outcome = drive_with_settings(
            settings=settings,
            tokens=ring,
            token=token,
            research=handlers,
            calls=((tool, arguments or {}),),
            want_tool_list=want_tool_list,
        )
        assert not outcome.handshake_error, outcome.handshake_error
        return outcome

    return call


# ----------------------------------------------------------------------
# search_web —— 记账与三种「没发送」
# ----------------------------------------------------------------------
def test_search_web_sends_one_query_and_settles_it(researcher, ledger, bundle) -> None:
    """正控：正常路径下真的发出了、真的结算了、回执里每一样都在。

    没有这一条，下面每一条「没有发送」都可能是被一个整个坏掉的工具满足的。
    """
    search = RecordingSearch(hits=2)
    payload = researcher(
        "search_web", {"account_id": ACCOUNT, "queries": ["devon clubs"]}, search=search
    ).as_json()

    assert [query.text for query in search.sent] == ["devon clubs"]
    assert payload["queries_sent"] == 1
    assert payload["queries_not_attempted"] == 0
    assert payload["errors"] == []
    assert payload["discovery_only"] is True

    receipt = payload["queries"][0]
    assert receipt["outcome"] == "sent"
    assert receipt["api_calls"] == 1
    assert len(receipt["hits"]) == 2
    assert receipt["reservation_id"]

    # 回执说出这一次问的是哪个市场，而且那句话必须与**真的发出去**的那一条一致
    # （`search.sent` 是 provider 侧收到的东西，不是回执自己记的）。缺了这两格，
    # 一次英国检索与一次美国检索的回执逐字同形 —— 而 `gl`/`hl` 不在检索式文本里，
    # 所以从 `query` 那一格看不出来。两处都断言：一处对**声明**（bundle），
    # 一处对**线上**（sent）。
    locale = market_locale(bundle)
    assert (receipt["country"], receipt["language"]) == (
        locale.country,
        locale.language,
    )
    assert (receipt["country"], receipt["language"]) == (
        search.sent[0].country,
        search.sent[0].language,
    )

    usage = _usage(ledger, _scope(bundle))
    assert usage is not None and (usage.spent, usage.reserved) == (1, 0), (
        "发出去了却没落成已花"
    )


def test_the_caller_given_limit_reaches_the_provider(researcher) -> None:
    """``limit`` 是这次调用的参数 —— 它约束的是**一条查询要几条结果**。

    它与「一次能发几条查询」不是同一件事：后者由服务端定（见下面那两条），
    前者是调用方的正常自由度。这条同时是 limit 校验的正控。
    """
    search = RecordingSearch()
    researcher(
        "search_web", {"account_id": ACCOUNT, "queries": ["x"], "limit": 3}, search=search
    )
    assert search.limits == [3]


def test_a_snippet_hit_is_never_binding_eligible(researcher) -> None:
    """SERP snippet 只用于发现。这条**由判据本身算出来**，不是写死的 False。

    写死的那个 False 与 ``domain/bind.py`` 是否真的排除 snippet 是两个可以各自
    漂移的断言：那个模块哪天把 snippet 放进允许集，这里会继续安静地报 false。
    所以断言分两半 —— 标记与 ``BINDING_ELIGIBLE_SOURCES`` 一致，**并且**那个集合
    确实不含 snippet。只查前一半的话，两个值同时改错时是绿的。
    """
    payload = researcher(
        "search_web", {"account_id": ACCOUNT, "queries": ["one"]}, search=RecordingSearch()
    ).as_json()

    hit = payload["queries"][0]["hits"][0]
    assert hit["binding_eligible"] is (SourceKind.SEARCH_SNIPPET in BINDING_ELIGIBLE_SOURCES)
    assert SourceKind.SEARCH_SNIPPET not in BINDING_ELIGIBLE_SOURCES, (
        "snippet 进了可绑定来源 —— 这条判据与 domain/bind.py 的前提同时变了"
    )
    assert "不能作证据" in payload["note"]


def test_a_query_that_was_already_paid_for_is_not_paid_again(
    researcher, ledger, bundle
) -> None:
    """同一个账号的同一条查询重发一次，第二次**不花钱**。

    「重跑同一个账号不会重复计费」这条规则住在 ``application/billed_search.py``
    （判据在 ``tests/test_billed_search.py``）；这里测的是它在服务面上真的接上了。
    """
    search = RecordingSearch()
    arguments = {"account_id": ACCOUNT, "queries": ["devon clubs"]}
    researcher("search_web", arguments, search=search)
    payload = researcher("search_web", arguments, search=search).as_json()

    assert len(search.sent) == 1, "第二次调用又发了一遍"
    assert payload["queries_sent"] == 0
    assert payload["queries"][0]["outcome"] == "skipped_already_paid"
    usage = _usage(ledger, _scope(bundle))
    assert usage is not None and usage.spent == 1, "幂等重发被重复计费"


def test_exhaustion_stops_and_says_how_many_were_not_attempted(
    researcher, ledger, bundle
) -> None:
    """撞穿上限：剩下的查询**没有发出**，而这件事在回执里必须看得见。

    ``queries_not_attempted`` 是这条路径上唯一的证据。静默丢掉它们会让「查了
    没有结果」与「没查」在报告上同形 —— 本仓把这两件事分得很开。
    """
    ceiling = bundle.profile.search_max_queries
    researcher(
        "search_web",
        {"account_id": ACCOUNT, "queries": [f"q{index}" for index in range(ceiling)]},
        search=RecordingSearch(),
    )

    search = RecordingSearch()
    payload = researcher(
        "search_web",
        {"account_id": ACCOUNT, "queries": ["one-too-many", "and-another"]},
        search=search,
    ).as_json()

    assert search.sent == [], "额度已空，却还是发了查询"
    assert payload["queries"][0]["outcome"] == "budget_exhausted"
    assert payload["queries_not_attempted"] == 1, "剩下的查询既没发出、也没被数出来"
    assert payload["errors"] == [], (
        "额度用尽被记成了调用失败 —— 两者要分开，否则「这次该停下来」读不出来"
    )
    usage = _usage(ledger, _scope(bundle))
    assert usage is not None and usage.remaining == 0


def test_two_accounts_do_not_share_a_budget(researcher, ledger, bundle) -> None:
    """额度按**账号**记，不按运行记（§14：一个 Account = 一个研究任务）。

    共用一个 scope 时，一个跑疯了的账号会把整批账号的额度吃光，而报告上只会
    显示「其余账号没有产出」。
    """
    ceiling = bundle.profile.search_max_queries
    researcher(
        "search_web",
        {"account_id": ACCOUNT, "queries": [f"q{index}" for index in range(ceiling)]},
        search=RecordingSearch(),
    )

    search = RecordingSearch()
    payload = researcher(
        "search_web", {"account_id": "test-account-002", "queries": ["fresh"]}, search=search
    ).as_json()

    assert [query.text for query in search.sent] == ["fresh"], "第二个账号被第一个挡住了"
    assert payload["queries"][0]["outcome"] == "sent"
    other = _usage(ledger, f"{bundle.profile_id}:test-account-002")
    assert other is not None and other.spent == 1


def test_a_failed_query_keeps_holding_the_budget(researcher, ledger, bundle) -> None:
    """调用失败**不退额度**（§8：超时或 provider-unknown 时不得默认释放）。

    那时你并不知道对方是否已经计费。这条与 ``ports/budget.py`` 不放 ``release``
    是同一件事的两个面：默认行为恰好正确，所以没有哪条分支能顺手退款。
    """
    payload = researcher(
        "search_web",
        {"account_id": ACCOUNT, "queries": ["boom"]},
        search=RecordingSearch(fail_on=frozenset({"boom"})),
    ).as_json()

    assert payload["queries"][0]["outcome"] == "failed"
    assert payload["errors"] and "SearchError" in payload["errors"][0]

    usage = _usage(ledger, _scope(bundle))
    assert usage is not None
    assert usage.reserved == 1, "失败的调用把额度退回来了"
    assert usage.spent == 0
    assert usage.remaining == bundle.profile.search_max_queries - 1


def test_a_failure_does_not_stop_the_remaining_queries(researcher) -> None:
    """一条查询失败，后面的查询**继续发**。

    与「额度用尽」的处置相反：额度用尽之后每一条都会得到同一句话，而一条失败
    的查询与下一条毫无关系。合成一种处置会让一次网络抖动截断整轮研究。
    """
    search = RecordingSearch(fail_on=frozenset({"boom"}))
    payload = researcher(
        "search_web", {"account_id": ACCOUNT, "queries": ["boom", "fine"]}, search=search
    ).as_json()

    assert [query.text for query in search.sent] == ["boom", "fine"]
    assert [item["outcome"] for item in payload["queries"]] == ["failed", "sent"]


# ----------------------------------------------------------------------
# search_web —— 没接线时**在预留之前**拒绝
# ----------------------------------------------------------------------
def test_no_provider_is_refused_before_any_budget_is_touched(
    researcher, ledger, bundle
) -> None:
    """没有检索提供方时，一次调用不得动到额度一分一毫。

    反面是「先扣住、再让 provider 抛错」——而那笔预留按 §8 不会退回，于是额度
    会被一次次不存在的检索吃光，报告上只显示「没有候选」。

    所以断言落在**账本**上：一句话说得再对，额度已经被扣掉了就是没接上。这里
    连上限都还没播种（``usage`` 为 ``None``），所以不可能预留过什么 —— 这比
    「用量是 0」强，后者表示有人动过这个 scope。
    """
    result = researcher("search_web", {"account_id": ACCOUNT, "queries": ["x"]})

    assert result.is_error
    assert "没有接线检索提供方" in result.text
    assert "SERPER_API_KEY" in result.text, "拒绝的理由要指出该看哪里"
    assert _usage(ledger, _scope(bundle)) is None, "一次没接线的调用动了账本"


def test_the_no_provider_answer_is_not_a_call_that_found_nothing(researcher) -> None:
    """「没接线」与「检索过、没有结果」必须分得开。

    对照的另一半是上面那条正控：正常路径下 ``queries_sent == 1`` 而 ``errors``
    为空。两条路径的措辞一旦合并，一次部署错误会长得像一次一无所获的研究。
    """
    result = researcher("search_web", {"account_id": ACCOUNT, "queries": ["x"]})
    assert result.is_error, "没接线却返回了一次成功"
    assert "没有结果" in result.text, "这句话本身应当说明它与「没有结果」的区别"


# ----------------------------------------------------------------------
# search_web —— 调用方给的数字都不是上限
# ----------------------------------------------------------------------
def test_the_query_ceiling_is_the_profile_not_the_caller(researcher, bundle) -> None:
    """回执里的 ``ceiling`` 来自冻结的 profile，不是这次调用的参数。

    §8：调用方不得静默扩大上限。调用方可能是一个模型，所以「上限」不能是它
    传进来的任何一个字段。
    """
    payload = researcher(
        "search_web", {"account_id": ACCOUNT, "queries": ["one"]}, search=RecordingSearch()
    ).as_json()

    assert payload["budget"]["ceiling"] == bundle.profile.search_max_queries
    assert payload["budget"]["scope"] == _scope(bundle)
    assert "不是本次调用的参数" in payload["budget"]["note"]


@pytest.mark.parametrize(
    ("arguments", "needle"),
    [
        ({"queries": []}, "非空"),
        ({"queries": ["   "]}, "非空字符串"),
        ({"queries": ["x"] * 21}, "最多 20"),
        ({"queries": ["x" * 401]}, "不截断"),
        ({"queries": ["ok"], "limit": 0}, "limit"),
        ({"queries": ["ok"], "limit": 11}, "limit"),
        ({"queries": ["ok"], "source_method": "not.a.method"}, "未知的来源方法"),
    ],
    ids=[
        "empty",
        "blank",
        "too-many",
        "too-long",
        "limit-zero",
        "limit-high",
        "bad-source-method",
    ],
)
def test_bad_input_is_refused_before_anything_is_sent(
    researcher, ledger, bundle, arguments: dict, needle: str
) -> None:
    """坏输入整次调用被拒，且**一条查询都没发出、账本一行没动**。

    超长查询文本是这张表里最要紧的一个：这里**不截断**，因为截断后的查询词是
    另一个查询，而它的结果会被当成本次的结果读。校验发生在播种上限与预留之前，
    所以一次被拒的调用不该在这个 scope 上留下任何痕迹。
    """
    result = researcher("search_web", {"account_id": ACCOUNT, **arguments})
    assert result.is_error, f"{arguments} 被接受了"
    assert needle in result.text
    assert _usage(ledger, _scope(bundle)) is None, "一次被拒的调用动了账本"


def test_a_wrongly_typed_argument_never_reaches_the_tool_body(
    researcher, ledger, bundle
) -> None:
    """``queries`` 类型不对时，拒它的是**声明出去的 schema**，不是函数体。

    这条单独列出来，是因为它测的是与上面那张表不同的一层：那张表里每一条都真的
    进了函数体（空清单、超长、越界的 limit 都是合法 JSON），而 ``"one"`` 在
    ``tools/call`` 的参数校验上就被挡掉了 —— 于是 ``_query_texts`` 里那句
    ``isinstance(queries, list)`` 对**经服务面**的调用是够不着的。它仍然要留着：
    同一份校验同时也守着直接调用它的代码路径。

    断言因此只要求「坏在 queries 上」，不要求那句具体措辞 —— 校验是 SDK 做的，
    它的措辞由 SDK 版本决定，钉死它等于把一次升级变成一次红的测试。
    """
    result = researcher("search_web", {"account_id": ACCOUNT, "queries": "one"})
    assert result.is_error
    assert "queries" in result.text
    assert _usage(ledger, _scope(bundle)) is None, "一次被拒的调用动了账本"


def test_a_non_canonical_account_id_is_refused_before_any_spend(
    researcher, ledger, bundle
) -> None:
    """``account_id`` 会变成账本 scope 与镜像目录名，所以它必须**已经是**文件名。

    判据不在这一层实现（``domain/util.check_canonical_id``），这里测的是它经服务面
    被翻译成了一条 ``ToolError``。
    """
    result = researcher("search_web", {"account_id": "Devon Clubs", "queries": ["x"]})
    assert result.is_error
    assert "不是规范形式" in result.text
    assert _usage(ledger, "uk-acme-natural-grass:Devon Clubs") is None


# ----------------------------------------------------------------------
# source_method —— 这次调用服务**计划里的哪一步**
# ----------------------------------------------------------------------
#: 三条工具各自声称**不同**的一步。三个不同的字面量是刻意的：判据要能证伪
#: 「实现里给了一个统一的默认步骤」—— 若那样，三条回执会逐字相同，下面三条会红。
#:
#: 这三个 id 不只是 ``SOURCE_METHODS``（闭集在 ``domain/sources.py``）的成员，它们
#: 还必须是**这份声明真的开出来的**那几条路 —— 也就是说它们得在
#: ``EffectiveResearchPlan.source_methods`` 那一格里。两件事今天**不是**同一件：
#: 闭集有五个成员，而声明只开出三条（``search.query_templates`` 开
#: ``search.engine.v1``；``page_discovery_hints`` 开另外两条；见
#: ``marketops/application/research_strategy.py`` 的 ``_SOURCE_ROUTES``）。
#: 一个恰好落在剩下那两条上的字面量，会让「这一步属于这一轮的 plan」那条规则把正控
#: 也一起拒掉 —— 而红的样子会指向实现，真正的原因只是这一行挑错了 id。
#:
#: ``FETCH_STEP`` 因此是 ``search.engine.v1`` 而不是别的那两条：抓一页的由来
#: 最贴它的就是「这一行来自面向公共网络的检索结果」—— 检索命中、再按 URL 取回内容。
SEARCH_STEP = "search.engine.site_scoped.v1"
FETCH_STEP = "search.engine.v1"
CRAWL_STEP = "crawl.site.v1"

#: 一个**在闭集里**、而这份声明**没有**给入口的步骤 —— 「这一步不属于这一轮的 plan」
#: 那条判据的反例。它只与上面那三个字面量差**一个变量**：声明有没有开这条路。
#:
#: 闭集里今天有两条这样的 id，都不是「随便找一条没人用的」：``research.skill.v1``
#: 是研究阶段封存的候选清单，``import.register.v1`` 是上游交进来的登记册导出
#: （两条各自的 ``answers`` 见 ``domain/sources.py``）。两者都不是**任何一份声明**
#: 开得出来的路 —— 声明侧今天只有三块，没有一块描述它们 —— 所以「它们不在计划里」
#: 是这一层的事实，不是这份 profile 的偶然。
UNPLANNED_STEP = "research.skill.v1"


def test_every_step_named_here_is_a_registered_source_method() -> None:
    """这一节用到的四个字面量必须真的在**服务端**的闭集里 —— 乱序不敏感。

    没有这一条，注册表哪天改名，下面每一条都会以「回执里的值与预期不符」的样子红，
    而那会指向一个错误的嫌疑（回显坏了），真正的原因只是一次重命名。这条把它挑明。

    ``UNPLANNED_STEP`` 也在这个元组里：它是反例，不是「闭集外的名字」。以
    「注册表里没有它」为由被拒的那条判据另有其人
    （``test_an_unknown_step_is_refused_before_the_search_provider_is_asked``）。
    """
    for step in (SEARCH_STEP, FETCH_STEP, CRAWL_STEP, UNPLANNED_STEP):
        assert step in SOURCE_METHODS, f"{step} 不在来源方法闭集里"


def test_search_web_echoes_the_plan_step_it_claims(researcher) -> None:
    """声称了步骤时，回执原样回显它 —— 且这次检索真的照常发出了。

    「原样」是这一条的要害：回显一个**归一化过的**值（小写、剥前缀、映射到族名）
    会让反查指向一个与计划里写的不同的字符串，而报告上两者看起来都对。
    """
    search = RecordingSearch()
    payload = researcher(
        "search_web",
        {"account_id": ACCOUNT, "queries": ["devon clubs"], "source_method": SEARCH_STEP},
        search=search,
    ).as_json()

    assert payload["source_method"] == SEARCH_STEP
    assert [query.text for query in search.sent] == ["devon clubs"], (
        "声称了步骤却把这次检索整个跳过了"
    )


def test_fetch_document_echoes_the_plan_step_it_claims(researcher, settings) -> None:
    """同上，换 ``fetch_document``：回显、且那一页真的抓了。"""
    http = _http(settings)
    payload = researcher(
        "fetch_document", {"url": PAGE_URL, "source_method": FETCH_STEP}, http=http
    ).as_json()

    assert payload["source_method"] == FETCH_STEP
    assert http.calls == [PAGE_URL], "声称了步骤却一次都没去取"


def test_crawl_site_echoes_the_plan_step_it_claims(settings, researcher) -> None:
    """同上，换 ``crawl_site``：回显、且前沿真的走过一遍。"""
    http = FakeHttp(settings.data_root / "pages", site_replies(SITE, SITE_PAGES))
    payload = researcher(
        "crawl_site", _crawl_args(source_method=CRAWL_STEP), crawl=_crawl_with(http)
    ).as_json()

    assert payload["source_method"] == CRAWL_STEP
    assert http.asked != [], "声称了步骤却没读过 robots 与站点地图"


def test_search_web_leaves_the_step_null_when_it_is_not_given(researcher) -> None:
    """不给就不声称：回执里是 ``null``，且**这次检索照常发生**。

    这一条守的是条件 7 的**前半**（「反查」那一半）：这一格不许有缺省、不许被伪造。
    一个默认的步骤名会让「这次调用是计划里的一步」在整个发现期都为真，而那份计划里
    根本没有它 —— 反查于是答出一个不存在的步骤，报告上却看不出异常。``null`` 与
    ``""`` 也不等价：后者是一个值，会被读成「声称了空字符串」。

    **它不守后半句**（「没需要的 provider 不为『集成完整』而强制调用」）。那半句要
    的是「这次 Run 开了哪几条路」的读数，而它在两处都还接不上：``RunSpec.plan_ref``
    是内容摘要、无消费者（``docs/B16_ACCEPTANCE_MATRIX_2026-09-28.md`` 关闭条件 7
    缺口 2），而这一条路上的 provider 调用点（本文件那几条工具、``decide``、执行器的
    ``_dispatch_block``）手上**没有 Run** —— 全文不碰 ``run_id``、不碰 ``plan_ref``、
    不数 provider 调用次数。此前这里的 docstring 逐字引了后半句而断的是前半，是一句
    自述它守 A、实际守 B 的话，已改。

    「照常发生」那一半是正控：没有它，``null`` 会被一个整个坏掉的工具满足。
    """
    search = RecordingSearch()
    payload = researcher(
        "search_web", {"account_id": ACCOUNT, "queries": ["x"]}, search=search
    ).as_json()

    assert payload["source_method"] is None
    assert [query.text for query in search.sent] == ["x"]


def test_fetch_document_leaves_the_step_null_when_it_is_not_given(
    researcher, settings
) -> None:
    """同上，换 ``fetch_document``：``null`` 不等于「这次调用没发生」。"""
    http = _http(settings)
    payload = researcher("fetch_document", {"url": PAGE_URL}, http=http).as_json()

    assert payload["source_method"] is None
    assert http.calls == [PAGE_URL]
    assert payload["final"]["outcome"] == FetchOutcome.OK.value


def test_crawl_site_leaves_the_step_null_when_it_is_not_given(
    settings, researcher
) -> None:
    """同上，换 ``crawl_site``。"""
    http = FakeHttp(settings.data_root / "pages", site_replies(SITE, SITE_PAGES))
    payload = researcher(
        "crawl_site", _crawl_args(), crawl=_crawl_with(http)
    ).as_json()

    assert payload["source_method"] is None
    assert payload["receipt_count"] >= 1


def test_a_step_this_scene_opened_passes_and_is_echoed(researcher) -> None:
    """**这份 Scene 的声明开了的一步 ⇒ 通过**，且原样回显、检索照常发出。

    这一条与下面那条反例**并排**才是「这一步属不属于计划」的判据，两条只差**一个
    变量**：那一步在不在 ``plan_source_methods(bundle)`` 里（``SEARCH_STEP`` 在，
    ``UNPLANNED_STEP`` 不在）。只有反例那一半时，一个恒拒的实现会把两条都变绿 ——
    而它拒掉的正是 worker 照着服务端交出的那一步填的值。

    **比的是哪一份，要说准**：``plan_source_methods`` 是**这份声明开出来的那几条
    路**，也就是计划里 ``source_methods`` 那一格在没有场景块收窄时的取值 —— 它是
    **许可**，不是某一个 Run 手上那一份（那一份比它窄，见
    ``marketops/application/research_strategy.py::plan_source_methods`` 的 docstring，
    以及 ``docs/B16_ACCEPTANCE_MATRIX_2026-09-28.md`` 关闭条件 7 缺口 2）。这份
    profile 没有 ``scenario-matrix.yaml``，所以两者在夹具上逐字相同。

    「照常发出」那一半同样是承重的：把这一步当成一个**许可**、进而在拒绝之前
    先跳过这次检索的实现，会让 worker 按回执走却不能检索，而回执上只写着
    「没有候选」。
    """
    search = RecordingSearch()
    payload = researcher(
        "search_web",
        {"account_id": ACCOUNT, "queries": ["devon clubs"], "source_method": SEARCH_STEP},
        search=search,
    ).as_json()

    assert payload["source_method"] == SEARCH_STEP
    assert [query.text for query in search.sent] == ["devon clubs"]


def test_a_step_this_scene_never_opened_is_refused_before_the_provider_is_asked(
    researcher, ledger, bundle
) -> None:
    """**这份 Scene 的声明没开的一步 ⇒ 被拒**，且一次检索都没发出、账本一行没动。

    与上面那条成对，与 ``test_an_unknown_step_is_refused_...`` 是**另一对**：
    那一条的 id 在闭集里根本不存在（注册表拒），这一条的 id 在闭集里、而这份声明
    没有给它入口（计划拒）。两对都要有 —— 只有闭集那一对时，「这一步属于哪一份
    计划」这一格**从来没有被查过**，于是任何注册过的名字都能被声称成计划的一步，
    ``source_method`` 那一栏就退化成「一个拼写正确的字符串」。

    「拒绝」而不是「读成 null」的理由与闭集那条逐字相同，只是它查的是另一件事：
    读成 ``null`` 之后，「worker 声称了计划里没有的一步」在报告上与「这次调用不属于
    任何步骤」同形，而前者是一次**可追溯性失效**，正是 §B16 关闭条件 7 要挡的东西。

    ``UNPLANNED_STEP in SOURCE_METHODS`` 那一句是这条判据自己的反例保险：注册表
    哪天删掉这个 id，这条会以「实现拒了它」的样子绿 —— 而它那时测的是闭集，
    不是计划。先断言它还在，红的样子才会指向真正的原因。
    """
    assert UNPLANNED_STEP in SOURCE_METHODS, "这个反例必须先是注册表里的成员"
    search = RecordingSearch()
    result = researcher(
        "search_web",
        {"account_id": ACCOUNT, "queries": ["x"], "source_method": UNPLANNED_STEP},
        search=search,
    )

    assert result.is_error
    assert "计划" in result.text, f"拒绝理由没说是计划那一格，拿到的是：{result.text}"
    assert search.sent == [], f"被拒了却真的检索了：{[q.text for q in search.sent]}"
    assert search.limits == [], "被拒了却已经问过 limit"
    assert _usage(ledger, _scope(bundle)) is None, "一次被拒的调用动了账本"


def test_the_plan_gate_is_on_fetch_document_too(researcher, settings) -> None:
    """同一个反例换一条工具：``fetch_document`` 也必须拒，且**一次都没去取**。

    这一条挡的是「门只装了一条工具」：三条工具各自有一份 ``source_method`` 参数，
    只给其中一条加计划校验的形态在报告上完全看不出来 —— 少校验的那一条照样回显、
    照样抓取，而它的 ``source_method`` 那一栏从此可以填任何注册过的名字。

    ``http.calls == []`` 是这一侧的零 I/O 判据。它与 ``search.sent == []`` 不是
    同一件事：抓取会把请求真的发到一个外部主机上，而检索只是问一个 API —— 两者
    都是外部副作用，必须各自在自己的工具上被断言。
    """
    http = _http(settings)
    result = researcher(
        "fetch_document", {"url": PAGE_URL, "source_method": UNPLANNED_STEP}, http=http
    )

    assert result.is_error
    assert "计划" in result.text, f"拒绝理由没说是计划那一格，拿到的是：{result.text}"
    assert http.calls == [], f"被拒了却还是去取了：{http.calls}"


def test_an_unknown_step_is_refused_before_the_search_provider_is_asked(
    researcher, ledger, bundle
) -> None:
    """一个没注册的步骤名被拒，且**检索提供方一次都没被问过、账本一行没动**。

    上面对 ``search_web`` 的那张坏输入表断言的是账本侧（``usage is None``）；这里
    补 provider 侧。两处都要：账本没动但请求已经发出去，与两者都没有，在报告上
    同形，而前者是**外部副作用已经发生**。

    「拒绝」而不是「读成 null」是刻意的：一个拼错的步骤名若被读成「没有声称」，
    那条调用在报告上就与「确实没声称」同形 —— 而它们要查的是两件相反的事
    （一处接线错误 vs 一次刻意的临时取证）。
    """
    search = RecordingSearch()
    result = researcher(
        "search_web",
        {"account_id": ACCOUNT, "queries": ["x"], "source_method": "search.engine"},
        search=search,
    )

    assert result.is_error
    assert "未知的来源方法" in result.text
    # 合法值要**列出来**：调用方得知道还有哪几种可选，而不是自己猜一个。
    for step in SOURCE_METHODS:
        assert step in result.text, f"拒绝理由里没有列出 {step}"
    assert search.sent == [], "一次被拒的调用已经问过检索提供方"
    assert _usage(ledger, _scope(bundle)) is None, "一次被拒的调用动了账本"


# ----------------------------------------------------------------------
# 已发出、而用量未知 —— 服务面那一格（B15）
# ----------------------------------------------------------------------
def test_a_failed_query_is_reported_with_unknown_usage_not_as_zero(
    researcher, ledger, bundle
) -> None:
    """发出去了、而用量不知道的那一发，在**服务面**回执上留得下痕迹。

    ``billed_search.QueryReceipt`` 早把两个问题分开了（``dispatched`` / ``usage_known``，
    判据在 ``tests/test_billed_search.py``），流水线层也已经在计数
    （``tests/test_pipeline_budget.py``）。这一条补的是**服务面**：调用方在这里读到的
    回执必须有这一格，否则「这一发去向不明」与「这一发压根没发」在这份报告上同形 ——
    两者的 ``api_calls`` 都是 0，而 ``outcome`` 各说各话，读的人得先**知道**
    ``failed`` 意味着请求已经派发出去，才分得开。这一格把那个前提写进报告本身。

    ``queries_sent == 1`` 与 ``queries_usage_unknown == 1`` 同时成立，是这一条的要害：
    两格**不是互补的**（差的那些里还有 ``skipped`` 与 ``budget_exhausted``，那两种
    压根没派发、用量确切地是 0）。把它们合成一句，去向不明就会重新读成 0。
    """
    search = RecordingSearch(fail_on=frozenset({"devon clubs"}))
    payload = researcher(
        "search_web",
        {"account_id": ACCOUNT, "queries": ["devon clubs", "somerset clubs"]},
        search=search,
    ).as_json()

    failed, ok = payload["queries"][0], payload["queries"][1]
    assert failed["outcome"] == QueryOutcome.FAILED.value
    assert failed["usage_unknown"] is True
    assert failed["api_calls"] == 0, "0 不是用量 —— 它是「没有结果可以读」"
    assert ok["outcome"] == QueryOutcome.SENT.value
    assert ok["usage_unknown"] is False, "这一格恒为真就等于没记"

    assert payload["queries_sent"] == 1
    assert payload["queries_usage_unknown"] == 1, (
        "两发里只有一发去向不明；这一格若等于 queries_asked，它就不是在数「未知」"
    )
    assert payload["errors"] != [], "调用失败仍要逐条说出理由"


# ----------------------------------------------------------------------
# fetch_document
# ----------------------------------------------------------------------
def test_fetch_document_returns_every_attempt_and_the_parsed_body(
    researcher, settings
) -> None:
    """抓取回执里 §5 要的字段逐项在；``body_ref`` 是**相对**引用。

    相对 ``data_root`` 而不是绝对路径：调用方要的是一个能回传的引用，不是这台
    机器的目录布局 —— 绝对路径会把运行目录的形状泄漏进调用方的上下文。
    相对性要连「那份字节真的在」一起断言：一个指向不存在位置的引用比没有引用更坏。
    """
    payload = researcher(
        "fetch_document", {"url": PAGE_URL, "account_id": ACCOUNT}, http=_http(settings)
    ).as_json()

    assert payload["mode"] == FetchMode.AUTO.value
    assert payload["decision"]
    assert len(payload["attempts"]) == 1

    final = payload["final"]
    assert final["outcome"] == FetchOutcome.OK.value
    assert final["url"] == PAGE_URL
    assert final["tier"] == "http"
    assert final["status_code"] == 200
    assert final["content_type"] == "text/html"
    assert final["body_sha256"] and final["body_bytes"] > 0
    assert final["body_ref"] and not Path(final["body_ref"]).is_absolute(), (
        f"body_ref 是绝对路径：{final['body_ref']!r}"
    )
    assert Path(settings.data_root, final["body_ref"]).exists(), (
        "body_ref 指向一个并不存在的位置 —— 调用方拿不到它说的那份字节"
    )
    assert "每一次" in payload["note"], "回执里要说明 attempts 保留的是全部尝试"

    document = payload["document"]
    assert document["parsed"] is True
    assert document["parser"] == "html"
    assert document["source_kind"] == SourceKind.FETCHED_HTML.value
    assert "Grounds Manager" in document["text_preview"]
    assert document["text_truncated"] is False
    assert document["text_chars"] == len(document["text_preview"])


def test_a_long_body_is_previewed_but_the_truncation_is_visible(researcher, settings) -> None:
    """正文预览有界，且**截断了要说出来**。

    一个不说的截断会让调用方把 4000 字符当成整页，于是在「这一页里没有邮箱」
    这个结论上错得理直气壮。
    """
    from marketops.adapters.mcp_server.research import MAX_PREVIEW_CHARS

    long_html = "<html><body><p>" + ("word " * 4000) + "</p></body></html>"
    payload = researcher(
        "fetch_document",
        {"url": PAGE_URL},
        http=_http(settings, body=long_html),
    ).as_json()

    document = payload["document"]
    assert len(document["text_preview"]) == MAX_PREVIEW_CHARS
    assert document["text_chars"] > MAX_PREVIEW_CHARS
    assert document["text_truncated"] is True


def test_a_blocked_target_is_a_result_not_a_crash(researcher, settings) -> None:
    """抓取受阻（这里：目标站点拒绝公开访问）是一次**可分类的结果**。

    它在 SOP 里有一个具体的处置（改走其他公开来源），不是一次工具崩溃 ——
    崩掉的工具会把那些账号整段吞掉。
    """
    result = researcher(
        "fetch_document",
        {"url": PAGE_URL},
        http=_http(settings, outcome=FetchOutcome.BLOCKED),
    )
    assert result.is_error is False
    payload = result.as_json()
    assert payload["final"]["outcome"] == FetchOutcome.BLOCKED.value
    assert payload["final"]["body_sha256"] == ""
    assert payload["document"]["parsed"] is False


def test_an_empty_url_is_refused(researcher) -> None:
    result = researcher("fetch_document", {"url": "   "})
    assert result.is_error
    assert "url" in result.text


def test_an_unknown_mode_lists_the_three_that_exist(researcher) -> None:
    """非法 mode 要**列出合法值**，而不是含混地说一句「参数错误」。

    三个 mode 是三种不同的花费与三种不同的合规姿态，调用方得知道还有哪几种可选。
    合法值从 ``ports/acquisition.FetchMode`` 算出来，不手抄 —— 手抄的清单会在加
    第四种 mode 时安静地过期。
    """
    result = researcher("fetch_document", {"url": PAGE_URL, "mode": "browser-only"})
    assert result.is_error
    for mode in FetchMode:
        assert mode.value in result.text, f"拒绝理由里没有列出 {mode.value}"


def test_mode_http_does_not_escalate_even_when_js_is_required(researcher, settings) -> None:
    """``mode=http`` 是一次**承诺**：不许悄悄升级到浏览器。

    浏览器侧的调用记录是这条判据唯一的证据 —— 只看回执的话，「没升级」与
    「浏览器整个没接线」同形。所以这里配一个**可用**的浏览器作正控。
    """
    browser = RecordingBrowserFetcher()
    payload = researcher(
        "fetch_document",
        {"url": PAGE_URL, "mode": FetchMode.HTTP.value},
        http=_http(settings, outcome=FetchOutcome.JS_REQUIRED),
        browser=browser,
    ).as_json()

    assert browser.calls == [], "mode=http 却启动了浏览器"
    assert payload["mode"] == FetchMode.HTTP.value
    assert payload["final"]["outcome"] == FetchOutcome.JS_REQUIRED.value
    assert payload["final"]["tier"] == "http"
    assert "mode_disallows" in payload["decision"], (
        "「没升级」与「升不了级」必须说得出是哪一种"
    )


def test_mode_auto_escalates_and_mode_browser_goes_straight_there(
    researcher, settings
) -> None:
    """上面那条的正控：同一份依赖下，auto 与 browser 都会真的用上浏览器。

    没有这一条，``test_mode_http_does_not_escalate_even_when_js_is_required``
    在被测行为整个消失时依然是绿的。
    """
    auto_browser = RecordingBrowserFetcher()
    auto = researcher(
        "fetch_document",
        {"url": PAGE_URL, "mode": FetchMode.AUTO.value},
        http=_http(settings, outcome=FetchOutcome.JS_REQUIRED),
        browser=auto_browser,
    ).as_json()
    assert auto_browser.calls == [PAGE_URL], "auto 遇到 js_required 却没有升级"
    assert auto["final"]["tier"] == "browser"
    assert auto["final"]["escalated_from"] == FetchOutcome.JS_REQUIRED.value
    assert len(auto["attempts"]) == 2, "走过的两次都要留下，只回最终结果会丢掉那段历史"
    assert auto["browser_seconds"] >= 0

    direct_browser = RecordingBrowserFetcher()
    direct_http = _http(settings)
    direct = researcher(
        "fetch_document",
        {"url": PAGE_URL, "mode": FetchMode.BROWSER.value},
        http=direct_http,
        browser=direct_browser,
    ).as_json()
    assert direct_http.calls == [], "mode=browser 却先走了一趟 HTTP"
    assert direct_browser.calls == [PAGE_URL]
    assert direct["final"]["tier"] == "browser"


def test_mode_browser_says_which_kind_of_unavailable_it_hit(researcher) -> None:
    """``mode=browser`` 用不了时，回执要说得出是**没接线**还是**装了但用不了**。

    两件事的处置不同（改部署 vs 等依赖），合成一句「浏览器不可用」会让排查方向
    错一半。而且它**不退回 HTTP**：调用方要的是渲染后的页面，拿到的会是一份需要
    JS 的空壳，而两者在报告上只差一个 tier 字段。
    """
    not_wired = researcher(
        "fetch_document", {"url": PAGE_URL, "mode": FetchMode.BROWSER.value}
    ).as_json()
    assert not_wired["final"]["outcome"] == FetchOutcome.ERROR.value
    assert not_wired["final"]["reason"] == "browser_not_wired"
    assert not_wired["attempts"] == [], "一次没发生的抓取不该留下尝试记录"

    unavailable = researcher(
        "fetch_document",
        {"url": PAGE_URL, "mode": FetchMode.BROWSER.value},
        browser=RecordingBrowserFetcher(available=False),
    ).as_json()
    assert unavailable["final"]["outcome"] == FetchOutcome.ERROR.value
    assert unavailable["final"]["reason"] == "browser_unavailable"
    assert unavailable["final"]["error"], "「用不了」要给出一句人能读的理由"
    assert unavailable["final"]["tier"] == "browser", (
        "这一次属于浏览器层级，只是没能发生 —— 记成 http 会让「有多少次真的渲染了」永远答不出来"
    )


def test_mode_auto_on_an_unwired_browser_still_returns_the_http_result(
    researcher, settings
) -> None:
    """``auto`` 与 ``browser`` 对「用不了」的处置相反，而理由必须同一个。

    这里停在 HTTP 的结果上（那是这次拿到的**最好**的东西），而回执里要说明
    为什么没有升级 —— 一个不说的 auto 会把「HTTP 只拿到一半」读成「这就是全部」。
    """
    payload = researcher(
        "fetch_document",
        {"url": PAGE_URL, "mode": FetchMode.AUTO.value},
        http=_http(settings, outcome=FetchOutcome.JS_REQUIRED),
    ).as_json()

    assert payload["final"]["tier"] == "http"
    assert payload["final"]["outcome"] == FetchOutcome.JS_REQUIRED.value
    assert "browser_not_wired" in payload["decision"]

    unavailable = researcher(
        "fetch_document",
        {"url": PAGE_URL, "mode": FetchMode.AUTO.value},
        http=_http(settings, outcome=FetchOutcome.JS_REQUIRED),
        browser=RecordingBrowserFetcher(available=False),
    ).as_json()
    assert "browser_unavailable" in unavailable["decision"]
    assert unavailable["decision"] != payload["decision"], (
        "两种不可用在 auto 路径上被说成了同一句话"
    )


def test_a_parse_failure_is_classified_not_raised(researcher, settings) -> None:
    """解析失败是链上一次**可分类**的结果（SOP L119），不是一次工具崩溃。

    抛异常的解析会把「这个页面读不出来」变成「这次调用失败了」，而前者在 SOP
    里有处置。
    """
    payload = researcher(
        "fetch_document", {"url": PAGE_URL}, http=_http(settings, body="   ")
    ).as_json()

    assert payload["final"]["outcome"] == FetchOutcome.OK.value
    assert payload["document"]["parsed"] is False
    assert "ParseError" in payload["document"]["reason"]


def test_the_receipt_says_which_parser_produced_the_text(researcher, settings) -> None:
    """回执要**如实**报出正文出自哪一条解析路径，包括「先用 primary 失败了」。

    ``DocumentRouter`` 的取舍本身有它自己的一组判据（``test_document_router.py``）；
    这里问的是另一件事：那个取舍有没有走到**调用方看得见的地方**。丢掉
    ``used_fallback`` / ``fallback_reason`` 时，一份扫描件解析出来的正文与一份
    正常 HTML 的正文在回执上完全同形，而下游据此认为「这次的证据是轻量解析来的」。

    两次调用在同一个用例里，因为这两条断言互为对照：只说 ``used_fallback`` 为真，
    在一个恒为真的实现上同样成立。

    这里的 fallback 是**替身**，而且这是刻意的：它要造的是「第二条解析路径成功」
    这个情形，而本机没有任何一条能真的产出正文的 fallback（MinerU 未校准，见
    ``test_mineru_fallback.py``）。真依赖在这里不会让判据更强，只会让它依赖本机
    装了什么。
    """

    class StubFallback(DocumentParser):
        name = "stub-fallback"
        source_kind = SourceKind.FETCHED_PDF
        extracts_links = False

        def can_parse(self, path: Path, *, content_type: str = "") -> bool:
            return True

        def parse(self, path: Path, *, content_type: str = "") -> ParsedDocument:
            return ParsedDocument(
                source_path=path,
                sha256="c" * 64,
                media_type="application/pdf",
                parser=self.name,
                title="",
                text="这条正文来自第二条解析路径",
                links=(),
                extracts_links=self.extracts_links,
                source_kind=self.source_kind,
                used_fallback=True,
                fallback_reason="html -> stub-fallback",
            )

    ordinary = researcher(
        "fetch_document", {"url": PAGE_URL}, http=_http(settings, body="<p>正常页面</p>")
    ).as_json()
    assert ordinary["document"]["parsed"] is True
    assert ordinary["document"]["used_fallback"] is False
    assert ordinary["document"]["fallback_reason"] == ""

    fell_back = researcher(
        "fetch_document",
        {"url": PAGE_URL},
        http=_http(settings, body="   "),
        router=DocumentRouter([HtmlDocumentParser()], [StubFallback()]),
    ).as_json()

    assert fell_back["document"]["parsed"] is True
    assert fell_back["document"]["parser"] == "stub-fallback"
    assert fell_back["document"]["used_fallback"] is True
    assert fell_back["document"]["fallback_reason"] == "html -> stub-fallback"
    assert fell_back["final"]["outcome"] == FetchOutcome.OK.value, (
        "换了一条解析路径不该改变抓取本身的结论"
    )


def test_a_bodyless_fetch_says_so_instead_of_parsing_nothing(researcher, settings) -> None:
    """没有落盘字节时，``document`` 要说明**没有东西可解析**，而不是报一个空正文。"""
    payload = researcher(
        "fetch_document",
        {"url": PAGE_URL},
        http=_http(settings, outcome=FetchOutcome.TIMEOUT),
    ).as_json()

    assert payload["final"]["outcome"] == FetchOutcome.TIMEOUT.value
    assert payload["document"]["parsed"] is False
    assert "没有可解析的内容" in payload["document"]["reason"]


def test_an_empty_body_is_not_passed_off_as_a_mirrored_page(researcher, settings) -> None:
    """§G1 第 5 条对照：**空正文声称镜像成功** —— 一次取回成功、盘上也真的有一份
    镜像，而那份镜像里一个字都没有。这一条问的是：这一趟在回执里长什么样。

    这是八条对照里最容易被「两边都正常」盖住的那一条。抓取层说 ``ok``（HTTP 200，
    ``body_ref`` 指得回盘上那个文件，``body_bytes`` 也是真的），解析层也**真的**解了
    一份文档出来 —— 一份空的。两个数各自都对，而把它们并排放在一起读的人会得到
    「这一页取回来了」，可这一页什么都没有。

    所以判据钉的是**这一趟有没有变成证据**：``no-content`` 与一个空的证据清单。
    镜像在盘上是**有意留的**（空壳字节值得留下，下一次好对比），所以这里**不**断言
    它不存在 —— 断言它不存在会去改抓取层一个刻意的设计，而那条设计本身没有说谎。
    说谎的是「有一份镜像」被读成「有一页内容」，而挡住这个读法的正是下面那两行。

    另一半在 ``tests/test_site_crawl.py``（没取到字节就不该有镜像）与
    ``tests/test_classify.py``（空正文归到哪一类）上；那两处问的是抓取层，
    这一条问的是**工具面把什么交给了下游**。
    """
    payload = researcher(
        "fetch_document",
        {"url": PAGE_URL, "account_id": ACCOUNT},
        http=_http(settings, body=""),
    ).as_json()

    # 前半：抓取这一层确实是「成功」的，而盘上那份镜像是 **0 字节**。这两句是这条
    # 对照赖以成立的前提，不是判据 —— 少了它们，这一条会在一个抓取失败的部署上
    # 照样绿，而那时它什么都没证明。
    assert payload["final"]["outcome"] == FetchOutcome.OK.value
    assert payload["final"]["body_ref"], "镜像确实落在盘上"
    assert payload["final"]["body_bytes"] == 0, (
        "这一条要的正是「有镜像、而镜像是空的」这个形状。它变成非 0 时，下面那几句"
        "仍然会通过，而它们通过的原因已经不是这一条对照了"
    )

    # 后半：而它**没有**被当成一页内容交出去。
    assert payload["document"]["parsed"] is False, (
        "一份 0 字节的 HTML 解析不出来（``ParseError: HTML 文件为空``）。说它解析"
        f"成功，等于把「盘上有一个文件」读成「这一页有内容」。回执：{payload['document']!r}"
    )
    assert "空" in payload["document"]["reason"], (
        f"理由要说得出空在哪：{payload['document']['reason']!r}"
    )

    block = payload["evidence"]
    _assert_evidence_shape(block)
    assert block["status"] == "unparsed", (
        f"空正文落成了一条证据，而它声称取回了内容。证据块：{block!r}"
    )
    assert block["evidence_id"] == "", "没有写进去就不该报一个 id"
    assert block["reason"].strip(), "status 不是 written 时理由恒非空"
    assert SqliteEvidenceStore(settings.database_path).list_evidence(ACCOUNT) == []


def test_fetch_document_reports_the_links_the_page_carries(researcher, settings) -> None:
    """``document.links`` 是这一页的链接 —— **逐字**的 ``href``，不是绝对地址。

    §5 要求每一页记录它的链接。这里回的是文档里写的那个地址：解析规则是**文档与
    页面 URL 的联合函数**（``<base href>`` 只有文档知道，页面 URL 只有抓取层知道），
    所以本构建不替调用方猜；要绝对地址就按同一回执里的 ``final_url`` 解析。
    最后那一条断言守的就是这句话能不能兑现 —— 一份给了链接却不给 ``final_url``
    的回执，等于把这件事推给调用方去别处找。
    """
    body = page_html("contact", links=("/contact-us", "https://other.example/forms"))
    payload = researcher(
        "fetch_document", {"url": PAGE_URL}, http=_http(settings, body=body)
    ).as_json()

    links = payload["document"]["links"]
    assert payload["document"]["parsed"] is True
    assert links["collected"] is True
    assert links["count"] == 2
    assert links["truncated"] is False
    assert links["reason"] == ""
    assert [item["url"] for item in links["items"]] == [
        "/contact-us",
        "https://other.example/forms",
    ]
    assert links["items"][0]["text"] == "contact 的链接 1", "锚文本一起回来"
    assert payload["final"]["final_url"] == PAGE_URL, (
        "「按 final_url 解析相对地址」这句话要能做得到"
    )


def test_a_page_that_cannot_be_parsed_does_not_report_zero_links(
    researcher, settings
) -> None:
    """解析失败时，链接块说的是「**没有抽**」，不是一个空清单。

    一个安静的空清单会被读成一条站点结论：「这一页没有链接」。所以 ``collected``
    为假时**必须**带着理由，而理由要指得到解析那一步的失败上 —— 「这一页没有链接」
    与「这一页没被解析」的处置完全不同。
    """
    payload = researcher(
        "fetch_document", {"url": PAGE_URL}, http=_http(settings, body="   ")
    ).as_json()

    assert payload["document"]["parsed"] is False
    links = payload["document"]["links"]
    assert links["collected"] is False
    assert links["count"] == 0 and links["items"] == []
    assert "ParseError" in links["reason"], "要说得出是解析那一步没成"


def test_a_parser_that_does_not_look_for_links_leaves_a_reason_not_a_silence(
    researcher, settings
) -> None:
    """解析器声明「不抽链接」时，链接块回的是**它自己的名字**加一句为什么。

    PDF 与 MinerU 今天都走这条路：它们不是「这一页没有链接」，是「没人看过」。
    一个 ``count: 0`` 在这两件事上长得一样，而前者是结论、后者是欠账。
    """

    class Blind(DocumentParser):
        """一个能解析、但明确不抽链接的解析器 —— 与 ``TextPdfParser`` 同形。"""

        name = "blind"
        source_kind = SourceKind.FETCHED_PDF
        extracts_links = False

        def can_parse(self, path: Path, *, content_type: str = "") -> bool:
            return True

        def parse(self, path: Path, *, content_type: str = "") -> ParsedDocument:
            return ParsedDocument(
                source_path=path,
                sha256="d" * 64,
                media_type="application/pdf",
                parser=self.name,
                title="",
                text="这份文档的正文在这里",
                links=(),
                extracts_links=self.extracts_links,
                source_kind=self.source_kind,
            )

    payload = researcher(
        "fetch_document",
        {"url": PAGE_URL},
        http=_http(settings),
        router=DocumentRouter([Blind()]),
    ).as_json()

    links = payload["document"]["links"]
    assert payload["document"]["parsed"] is True, "正控：这条路上解析是成功的"
    assert links["collected"] is False
    assert links["count"] == 0 and links["items"] == []
    assert "blind" in links["reason"], "理由要指得到是哪一个解析器没看链接"
    assert "没有链接" in links["reason"], "要让读的人知道这不等于「这一页没有链接」"


def test_the_link_list_is_bounded_and_the_count_is_exact(researcher, settings) -> None:
    """链接清单有界，而**总数是准的**（§11：不得静默截断）。

    一页可以有几百条链接（导航、页脚、标签云）。截断本身没问题 —— 问题是截断了
    不说：``count`` 给的是准确总数，``truncated`` 说这一份清单不全。
    """
    body = page_html("hub", links=tuple(f"/page-{i}" for i in range(MAX_LINKS_RETURNED + 5)))
    payload = researcher(
        "fetch_document", {"url": PAGE_URL}, http=_http(settings, body=body)
    ).as_json()

    links = payload["document"]["links"]
    assert links["count"] == MAX_LINKS_RETURNED + 5
    assert len(links["items"]) == MAX_LINKS_RETURNED
    assert links["truncated"] is True
    assert links["items"][0]["url"] == "/page-0", "截的是尾巴，不是开头"


def test_fetch_document_accepts_an_account_less_call(researcher, settings) -> None:
    """``account_id`` 为空是合法的，且含义明确：这次抓取不属于任何账号。

    它不是「随便哪个账号」—— 编一个 id 出来会让镜像落进一个并不存在的账号名下。
    这条同时是上一条（坏 id 被拒）的正控：判据拦的是**不规范的 id**，不是所有 id。

    抓取本身照常成功；而**证据那一行无处可记**，回执要说得出这件事。见下面
    ``test_a_call_without_an_account_still_fetches_and_says_why_nothing_was_filed``。
    """
    payload = researcher(
        "fetch_document", {"url": PAGE_URL}, http=_http(settings)
    ).as_json()
    assert payload["final"]["url"] == PAGE_URL
    assert payload["document"]["parsed"] is True


def test_a_non_canonical_account_id_never_reaches_the_fetcher(researcher) -> None:
    """坏 ``account_id`` 在**发请求之前**被拒（``FetchRequest`` 的契约）。

    断言连 fetcher 的调用记录一起做：抛在发请求之前是这条契约的全部要点 ——
    一次注定写不进镜像的抓取不该先打出去。
    """
    http = RecordingHttpFetcher()
    result = researcher("fetch_document", {"url": PAGE_URL, "account_id": "A/B"}, http=http)
    assert result.is_error
    assert "不是规范形式" in result.text
    assert http.calls == []


# ----------------------------------------------------------------------
# 抓到的材料落成证据 —— P0-2
# ----------------------------------------------------------------------
#: ``evidence`` 块的两个不变量，写成一处：三个字段只有这几个取值组合是合法的。
#: 各条判据都从它出发，而不是各自记得检查。
def _assert_evidence_shape(block: dict) -> None:
    """``evidence`` 块的**形状**：闭集 + 两条不变量。

    * ``status`` 是闭集里的一个取值；
    * ``evidence_id`` 非空 ⟺ 这一条**可以引用**（``written`` / ``already-filed``）；
    * ``reason`` 非空 ⟺ 这一条**不可引用**。

    后两条是这份回执里唯一把「有 id」与「能引用它」绑在一起的东西 —— 少了它，
    一个 ``status: "store-refused"`` 配一个非空 ``evidence_id`` 的回执会一路走到
    调用方，而调用方会拿那个 id 去引用一条它并不知道存不存在的证据。
    """
    from marketops.adapters.mcp_server.research import (
        EVIDENCE_ALREADY_FILED,
        EVIDENCE_NO_ACCOUNT,
        EVIDENCE_NO_CONTENT,
        EVIDENCE_STORE_REFUSED,
        EVIDENCE_UNPARSED,
        EVIDENCE_WRITTEN,
    )

    statuses = {
        EVIDENCE_WRITTEN,
        EVIDENCE_ALREADY_FILED,
        EVIDENCE_NO_ACCOUNT,
        EVIDENCE_UNPARSED,
        EVIDENCE_NO_CONTENT,
        EVIDENCE_STORE_REFUSED,
    }
    assert block["status"] in statuses, f"evidence.status 不在闭集里：{block['status']!r}"
    citable = block["status"] in {EVIDENCE_WRITTEN, EVIDENCE_ALREADY_FILED}
    assert bool(block["evidence_id"]) is citable, "可以引用与有 id 不是同一件事了"
    assert bool(block["reason"]) is not citable, "不可引用却不给理由"


class _RefusingStore(InMemoryEvidenceStore):
    """一个坏掉的存储替身：每次写入都抛 ``StoreError``。

    **只用于证明 ``store-refused`` 那条路活着。** 与
    ``tests/test_evidence_store_contract.py::_BlindAppender`` 是同一个手法：
    「存储拒绝时回执会说出来」这条判据如果只靠读代码成立，删掉那个 ``except``
    也没有任何东西会红。

    抛的是 ``StoreError``（状态库打不开 / 建不了表）而不是 ``EvidenceError``：
    后者是**冲突**的意思，它有一条自己的分支（见 ``_CollidingStore``），
    用同一个替身同时盖住两条路会让两条里的一条永远测不到。
    """

    def put_evidence(self, evidence) -> None:
        raise StoreError(f"替身被要求写不进去：{evidence.evidence_id}")


class _CollidingStore(InMemoryEvidenceStore):
    """这个 id 下已经躺着**另一份材料**。

    只有 id 被手工指定过才会长成这个形状（``evidence_id`` 是内容寻址的）。
    它挡住的是一个很便宜的「修法」：见到冲突就复用那一行 —— 那会把别人的材料
    当成这一页的引用，而且报告上完全正常。
    """

    def __init__(self, decoy: Evidence) -> None:
        super().__init__()
        self._decoy = decoy

    def get_evidence(self, evidence_id: str) -> Evidence | None:
        return self._decoy

    def put_evidence(self, evidence) -> None:
        raise EvidenceError("同 id 的内容与本次不同")


class _CountingStore(InMemoryEvidenceStore):
    """记下每一次 ``put_evidence`` 的调用。

    「这一行没有落成证据」有两个可能的实现：**没写**，或者**写了一条假的**。
    用真库断言 ``list_evidence(...) == []`` 两条都过得去（后者要拿对的账号才
    读得到），而这里断言的是**这一层根本没有碰过存储** —— 那是更准的话。
    """

    def __init__(self) -> None:
        super().__init__()
        self.puts: list[str] = []

    def put_evidence(self, evidence) -> None:
        self.puts.append(evidence.evidence_id)
        super().put_evidence(evidence)


def test_a_fetched_page_becomes_a_row_a_citing_finding_can_point_at(
    researcher, settings, evidence
) -> None:
    """**这是 P0-2 那一条。** 抓到的材料在证据库里有了 id，而且解得开。

    第一次真实 research run 的形状是：Serper 成功、HTTP fetch 成功、镜像字节也
    落盘了，而 ``evidence`` 表零行。于是 worker 手上没有任何可以引用到
    ``submit_findings`` 的 id，它一条也填不出来，正确地以 ``blocked`` 收场。

    这条判据同时钉住**接线**：``evidence_store_factory`` 在 ``research_handlers``
    上是一个必填参数，而它是唯一一个「端口有实现、有契约测试、生产代码零调用点」
    能被证伪的地方。读另一个 store 实例取那一行 —— 上一行是这次调用写的，
    这一行是从磁盘读回来的。
    """
    http = _http(settings)
    payload = researcher(
        "fetch_document", {"url": PAGE_URL, "account_id": ACCOUNT}, http=http
    ).as_json()

    block = payload["evidence"]
    _assert_evidence_shape(block)
    assert block["status"] == "written", block["reason"]

    reopened = SqliteEvidenceStore(settings.database_path)
    row = reopened.get_evidence(block["evidence_id"])
    assert row is not None, "回执给了一个解不开的 id —— 这比没有 id 更坏"
    assert row.account_id == ACCOUNT
    assert row.source_url == PAGE_URL
    assert row.content_sha256 == payload["document"]["content_sha256"]
    assert row.http_status == 200
    assert row.tier == "http"
    assert row.literal_value in payload["document"]["text_preview"], (
        "证据里的字面量不在回执给出的正文里 —— 引用指向的东西与回执说的不是一回事"
    )


def test_the_same_page_fetched_twice_files_one_row(researcher, settings, evidence) -> None:
    """重复抓取是幂等的：id 相同、只留一行、第二次**说得出自己没新增**。

    这条判据的第二个断言是它真正挡住的东西。证据 id 是内容寻址的，而那一行还
    带着 ``fetched_at`` —— 重抓一遍算出同一个 id 而 payload 不同，存储按契约
    拒绝（它对得对：那正是「id 被手工指定过」的形状）。于是「重抓是幂等的」在
    这一层变成一条假话：每一次正常的重抓都回一句 ``store-refused``，把一个部署
    故障的样子安在一次成功上。

    ``already-filed`` 与 ``written`` 是**两个取值**，因为它们回答两个问题：
    「能不能引用」与「这次调用新增了一行吗」。前者两次相同，后者不同。
    """
    first = researcher(
        "fetch_document", {"url": PAGE_URL, "account_id": ACCOUNT}, http=_http(settings)
    ).as_json()
    second = researcher(
        "fetch_document", {"url": PAGE_URL, "account_id": ACCOUNT}, http=_http(settings)
    ).as_json()

    assert first["evidence"]["status"] == "written"
    assert second["evidence"]["status"] == "already-filed", second["evidence"]["reason"]
    assert first["evidence"]["evidence_id"] == second["evidence"]["evidence_id"]
    assert len(evidence.list_evidence(ACCOUNT)) == 1


def test_two_accounts_fetching_one_page_get_two_rows(researcher, settings, evidence) -> None:
    """同一页、两个账号 → 两行。``account_id`` 进证据 id 的原像是**承重**的。

    共用一行会让第二个账号读到第一个账号的申请记录，而报告上两边都正常 ——
    §10 的 false attribution 最便宜的一种。
    """
    ids = []
    for account in (ACCOUNT, "test-account-002"):
        payload = researcher(
            "fetch_document", {"url": PAGE_URL, "account_id": account}, http=_http(settings)
        ).as_json()
        ids.append(payload["evidence"]["evidence_id"])

    assert ids[0] and ids[1] and ids[0] != ids[1]


def test_a_call_without_an_account_still_fetches_and_says_why_nothing_was_filed(
    researcher, settings
) -> None:
    """没有账号的调用**照常抓取**，只是没有可归档的位置，而回执要说出来。

    「不写证据」在这里必须是一个**说了话的结果**，不是一个沉默的省略：一个
    只看得见 ``evidence_id`` 空着的调用方分不出「这次调用不属于任何账号」与
    「存储层坏了」。而两者一个换调用方式、一个修部署。

    断言的是**这一层根本没碰过存储**（``_CountingStore.puts`` 为空），而不是
    「某个账号下没有行」：后者对一个写进了错账号的实现也成立。
    """
    store = _CountingStore()
    payload = researcher(
        "fetch_document", {"url": PAGE_URL}, http=_http(settings), store=store
    ).as_json()

    assert payload["final"]["outcome"] == FetchOutcome.OK.value
    assert payload["final"]["body_ref"], "抓取本身是成功的"
    assert payload["document"]["parsed"] is True

    block = payload["evidence"]
    _assert_evidence_shape(block)
    assert block["status"] == "no-account"
    assert store.puts == [], "为一次没有账号的调用编了一个账号 id"


def test_a_parsed_page_with_no_text_is_not_filed_as_an_empty_row(
    researcher, settings, evidence
) -> None:
    """解析成功、而输出没有正文 → 没有证据行。**不是**一条 ``literal_value=""`` 的行。

    ``Evidence`` 会拒绝空字面量，而放宽那条校验是把「我读过一页」当成结论的
    依据 —— §4 明令禁止的那种推理。

    这一条要一个**解析得出来的空文档**，而 HTML 解析器对空正文是抛 ``ParseError``
    （那条路是 ``unparsed``，见上一条）。所以这里换一个会返回空文本的解析器：
    ``no-content`` 这个取值存在的理由就是「解析器说它解析成功了，而正文是空的」，
    用一条永远走不到的路径去钉它等于没钉。
    """

    class Blank(DocumentParser):
        """解析成功、正文为空白的解析器（与 ``TextPdfParser`` 同形）。"""

        name = "blank"
        source_kind = SourceKind.FETCHED_PDF
        extracts_links = False

        def can_parse(self, path: Path, *, content_type: str = "") -> bool:
            return True

        def parse(self, path: Path, *, content_type: str = "") -> ParsedDocument:
            return ParsedDocument(
                source_path=path,
                sha256="e" * 64,
                media_type="application/pdf",
                parser=self.name,
                title="",
                text="\n\t  \n",
                links=(),
                extracts_links=self.extracts_links,
                source_kind=self.source_kind,
            )

    payload = researcher(
        "fetch_document",
        {"url": PAGE_URL, "account_id": ACCOUNT},
        http=_http(settings),
        router=DocumentRouter([Blank()]),
    ).as_json()

    block = payload["evidence"]
    _assert_evidence_shape(block)
    assert block["status"] == "no-content"
    assert payload["document"]["parsed"] is True, "解析成功了，只是没有正文"
    assert evidence.list_evidence(ACCOUNT) == []


def test_a_blocked_target_files_nothing_and_says_which_kind_of_nothing(
    researcher, settings, evidence
) -> None:
    """受阻的抓取没有字节可解析 —— ``unparsed``，**不是** ``no-content``。

    两者都要重做研究，但 ``no-content`` 说的是「这一页真的没有正文」（换一页），
    ``unparsed`` 说的是「这次什么都没拿到」（换一条路）。合成一句会让处置无从选。
    """
    payload = researcher(
        "fetch_document",
        {"url": PAGE_URL, "account_id": ACCOUNT},
        http=_http(settings, outcome=FetchOutcome.BLOCKED),
    ).as_json()

    block = payload["evidence"]
    _assert_evidence_shape(block)
    assert block["status"] == "unparsed"
    assert evidence.list_evidence(ACCOUNT) == []


def test_a_refusing_store_is_a_result_not_a_lost_receipt(researcher, settings) -> None:
    """存储层拒绝写入时，**整份抓取回执仍在**，而 ``status`` 说得出为什么。

    这里刻意不抛：抛出去会把已经取回来的字节、body_ref 与「HTTP 成功」一起带走，
    而调用方在「这一页没有证据」与「这次调用崩了」之间分不出来。代价是必须让
    ``store-refused`` 与 ``no-content`` 是两个取值 —— 否则这一层就是拿一句
    「没有证据」把一次部署故障盖住。

    这条同时是正控的反面：``_RefusingStore`` 在别处永远不该出现。
    """
    payload = researcher(
        "fetch_document",
        {"url": PAGE_URL, "account_id": ACCOUNT},
        http=_http(settings),
        store=_RefusingStore(),
    ).as_json()

    assert payload["final"]["body_ref"], "回执被一次存储故障带走了"
    block = payload["evidence"]
    _assert_evidence_shape(block)
    assert block["status"] == "store-refused"
    assert "StoreError" in block["reason"], "为什么不写，要说得出是哪一类失败"


def test_an_id_holding_other_material_is_refused_not_reused(researcher, settings) -> None:
    """这个 id 下已经有**别的**材料时，拒绝 —— 不复用那一行。

    ``evidence_id`` 是内容寻址的，所以这个形状只可能来自一次手工指定。复用那
    一行是最省事的「修法」，也是最坏的一种：引用会指向别人的材料，而报告上
    「有一条证据」与「这一页有证据」长得一模一样。
    """
    decoy = Evidence(
        evidence_id="whatever",
        # 血统必填、无默认值。这一行是**伪造的**（它代表「别人的材料占了同一个
        # id」），而它的 workspace 取本部署 —— 落成别人的 workspace 会让
        # ``_CollidingStore`` 的拒绝变成工作区不匹配的拒绝，测的就不是这条用例
        # 要测的那件事了。
        workspace=settings.profile,
        account_id=ACCOUNT,
        source_url=PAGE_URL,
        source_kind=SourceKind.FETCHED_HTML,
        literal_value="别人的材料",
        content_sha256="f" * 64,
        fetched_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )
    payload = researcher(
        "fetch_document",
        {"url": PAGE_URL, "account_id": ACCOUNT},
        http=_http(settings),
        store=_CollidingStore(decoy),
    ).as_json()

    block = payload["evidence"]
    _assert_evidence_shape(block)
    assert block["status"] == "store-refused"
    assert "内容不同" in block["reason"], "要说清是「同一个 id 下是别的材料」"


# ----------------------------------------------------------------------
# 接线：谁能看见这两个工具、以及「还没接线」怎么说
# ----------------------------------------------------------------------
def test_a_controller_cannot_see_the_research_tools_at_all(settings, tmp_path) -> None:
    """研究侧的两个工具只属于 researcher 角色。

    这里是**另一个**角色的令牌，走的是同一条链：不属于你的工具先被拒绝，而且
    拒绝的形状与「不存在」逐字相同 —— 那两句话的差别本身就是泄漏。
    """
    ring, token = _ring(tmp_path, "claude-control", Role.CONTROLLER)
    handlers = research_handlers(
        settings=settings,
        search=RecordingSearch(),
        acquisition=DocumentAcquisition(http=RecordingHttpFetcher(), browser=None),
        router=DocumentRouter([HtmlDocumentParser()]),
        ledger_factory=lambda: InMemoryBudgetLedger(),
        evidence_store_factory=lambda: InMemoryEvidenceStore(),
    )
    outcome = drive_with_settings(
        settings=settings,
        tokens=ring,
        token=token,
        research=handlers,
        calls=(("search_web", {"account_id": ACCOUNT, "queries": ["x"]}),),
        want_tool_list=True,
    )

    assert not outcome.handshake_error, outcome.handshake_error
    assert "search_web" not in outcome.tool_names, "controller 看得见研究侧的工具"
    assert "search_web" not in outcome.tool_names, "controller 看得见研究侧的工具"
    assert "crawl_site" not in outcome.tool_names, "controller 看得见多页抓取"
    assert outcome.call("search_web").text == "Unknown tool: search_web", (
        "越权与不存在必须是同一句话"
    )


def test_a_control_only_instance_answers_with_a_phase_not_with_absence(
    settings, tmp_path
) -> None:
    """只接控制面的部署里，研究侧的两个工具落回 stub，回答带阶段号。

    这是 ``build_server(research=None)`` 那条路径 —— 控制面单独部署是合法的一种
    部署。少了这一条，「研究侧有没有接线」这个区别就只由组合根的那一句话保证。
    """
    ring, token = _ring(tmp_path, "claude-research", Role.RESEARCHER)
    outcome = drive_with_settings(
        settings=settings,
        tokens=ring,
        token=token,
        calls=(("search_web", {}), ("fetch_document", {})),
        want_tool_list=False,
    )

    assert not outcome.handshake_error, outcome.handshake_error
    for name in ("search_web", "fetch_document"):
        result = outcome.call(name)
        assert result.is_error, f"{name} 在没接线的实例上成功了"
        assert f"Phase {TOOL_CATALOG[name].phase}" in result.text
        assert "Unknown tool" not in result.text


def test_the_capabilities_wired_cell_answers_the_third_question(settings, tmp_path) -> None:
    """``wired`` 是**第三个**问题：有没有工具会真的去调这一个集成。

    它与 ``configured``（配没配）和 ``runtime_ready``（装了没）都不是同一件事，
    而这一格由**这个实例**的处理器表算出来。所以在同一份没配 key 的 Settings 上：
    接了研究侧的实例答 true（工具在，只是会明确说「没接线检索」），只接控制面的
    实例答 false。
    """
    ring, token = _ring(tmp_path, "claude-control", Role.CONTROLLER)

    def capabilities(research_table) -> dict:
        outcome = drive_with_settings(
            settings=settings,
            tokens=ring,
            token=token,
            research=research_table,
            calls=(("get_capabilities", {}),),
            want_tool_list=False,
        )
        assert not outcome.handshake_error, outcome.handshake_error
        return outcome.call("get_capabilities").as_json()

    wired = research_handlers(
        settings=settings,
        search=None,  # 没配 key：configured 为假，但工具在
        acquisition=DocumentAcquisition(http=RecordingHttpFetcher(), browser=None),
        router=DocumentRouter([HtmlDocumentParser()]),
        ledger_factory=lambda: InMemoryBudgetLedger(),
        evidence_store_factory=lambda: InMemoryEvidenceStore(),
    )
    rows = {row["name"]: row for row in capabilities(wired)["integrations"]}
    assert rows["serper"]["wired"] is True
    assert rows["serper"]["configured"] is False, "这条用例的前提是没配 key"
    assert rows["http"]["wired"] is True
    assert rows["crawl4ai"]["wired"] is True
    assert rows["mineru"]["wired"] is True
    for name in ("jev", "laya"):
        assert rows[name]["wired"] is False, f"{name} 还没有会调它的工具"

    control_only = {row["name"]: row for row in capabilities(None)["integrations"]}
    assert control_only["serper"]["wired"] is False
    assert control_only["serper"]["blocker"], "没接线却不说原因"
    assert "Phase D" in control_only["serper"]["blocker"], (
        "没接线的原因要能指向该等什么"
    )


# ----------------------------------------------------------------------
# crawl_site —— 有界多页抓取
# ----------------------------------------------------------------------
def _crawl_with(http: HttpFetcher, *, clock=None) -> CrawlFactory:
    """把一份替身抓取器接成多页抓取 —— **前沿与页面共用它**。

    前沿读 robots.txt 与站点地图，页面走 ``DocumentAcquisition``；两处必须是同一个
    抓取器（``SitemapFrontier`` 的文件头），所以这里只收一个 ``http``。判据要摆的
    是「服务面把 profile 的页面提示交给前沿了吗」，而不是「两份 fetcher 能不能被
    配到一起」—— 后者是组合根的事，有它自己的判据（``tests/test_mcp_entry.py``）。
    """
    acquisition = DocumentAcquisition(http=http, browser=None)

    def build(hints: Mapping[str, Sequence[str]]) -> SiteCrawl:
        return SiteCrawl(
            frontier=SitemapFrontier(http=http, hints=hints),
            acquisition=acquisition,
            clock=clock if clock is not None else time.monotonic,
        )

    return build


def _crawl_args(**overrides) -> dict:
    """一次多页调用的正常参数。**两个上限是调用方给的数字**，所以它们要有默认值。"""
    return {"seeds": [SITE], "purpose": "site", "pages": 10, "seconds": 30.0, **overrides}


def test_crawl_site_walks_the_planned_pages_and_receipts_each_one(
    settings, researcher
) -> None:
    """正控：计划里的每一页都取了回来，一页一条回执，报告与计划对得上。

    没有这一条，下面每一条「没取」「拒绝了」都可能被一个整个坏掉的工具满足。
    """
    http = FakeHttp(settings.data_root / "pages", site_replies(SITE, SITE_PAGES))
    payload = researcher("crawl_site", _crawl_args(), crawl=_crawl_with(http)).as_json()

    assert payload["seeds"] == [SITE]
    assert payload["account_id"] == ""
    assert payload["stopped_reason"] == StopReason.COMPLETED.value
    assert payload["planned_count"] == 4, "种子 + 三页"
    assert payload["receipt_count"] == 4
    assert payload["remaining_count"] == 0
    assert payload["urls_truncated"] is False
    assert payload["planned_urls"] == [SITE, *[p for p in SITE_PAGES if p != f"{SITE}/"], f"{SITE}/"]

    first = payload["receipts"][0]
    assert first["url"] == SITE, "种子由调用方指定，排在第一位"
    assert first["outcome"] == FetchOutcome.OK.value
    assert first["mode"] == FetchMode.AUTO.value
    assert first["fetch_tier"] == "http"
    assert first["tier"] == "" and first["why"] == "seed", (
        "种子不是按页面提示选出来的 —— 给它编一个优先级等于伪造一条规则"
    )
    assert first["body_ref"] and not Path(first["body_ref"]).is_absolute()
    assert Path(settings.data_root, first["body_ref"]).exists()
    assert first["body_sha256"] and first["body_bytes"] > 0

    assert ROBOTS in http.asked and SITEMAP in http.asked, "前沿没有读它该读的两份文档"
    # 计划本身也要在回执里，且**不含 urls**：合并去重之后的那一份在 planned_urls，
    # 同一份清单出现两次会让调用方不知道该信哪一份。
    assert payload["plans"][0]["seed"] == SITE
    assert "urls" not in payload["plans"][0]
    assert payload["plans"][0]["tier_hits"]["high"] == 2


def test_the_page_tier_comes_from_the_frozen_profile(settings, researcher, bundle) -> None:
    """「先抓哪些页面」来自冻结的 profile，不是调用方的参数，也不是代码里的常量。

    §8 的同一条：调用方（可能是一个模型）能请求「取多少页」，**不能**决定哪些页面
    优先。这条是端到端的 —— 命中哪一级、命中哪一条 glob 都是 profile 里写着的，
    所以它换一份 profile 就会跟着变。
    """
    http = FakeHttp(settings.data_root / "pages", site_replies(SITE, SITE_PAGES))
    payload = researcher("crawl_site", _crawl_args(), crawl=_crawl_with(http)).as_json()

    hints = bundle.profile.page_discovery_hints
    by_url = {receipt["url"]: receipt for receipt in payload["receipts"]}
    contact = by_url[f"{SITE}/contact-us"]
    home = by_url[f"{SITE}/"]

    assert contact["tier"] in hints, "命中的分级不在 profile 的提示里"
    assert contact["why"] in hints[contact["tier"]], "回执说的 glob 不是 profile 里那一条"
    assert home["tier"] in hints and home["why"] in hints[home["tier"]]
    assert not any(gap.get("gap") == "no_page_hints" for gap in payload["gaps"]), (
        "前沿没拿到提示 —— 那它会拒绝抓取，而不是抓这几页"
    )


@pytest.mark.parametrize(
    ("arguments", "needle"),
    [
        ({"seeds": []}, "非空"),
        ({"seeds": ["   "]}, "非空字符串"),
        ({"seeds": [f"{SITE}/{index}" for index in range(MAX_SEEDS_PER_CALL + 1)]}, "最多"),
        ({"pages": 0}, "pages"),
        ({"pages": MAX_PAGES_PER_CALL + 1}, "pages"),
        ({"seconds": 0}, "seconds"),
        ({"seconds": MAX_CRAWL_SECONDS + 1}, "seconds"),
        ({"purpose": "   "}, "purpose"),
        ({"mode": "render"}, "mode"),
        ({"account_id": "Devon Clubs"}, "不是规范形式"),
        ({"source_method": "not.a.method"}, "未知的来源方法"),
    ],
    ids=[
        "no-seeds",
        "blank-seed",
        "too-many-seeds",
        "pages-zero",
        "pages-high",
        "seconds-zero",
        "seconds-high",
        "blank-purpose",
        "bad-mode",
        "bad-account",
        "bad-source-method",
    ],
)
def test_the_ceilings_are_the_servers_not_the_callers(
    settings, researcher, arguments: dict, needle: str
) -> None:
    """两个上限的**上界**由服务端定：越界是**拒绝**，不是截断。

    截断的代价不是「少取几页」，而是调用方以为它请求的额度被满足了 —— 于是它把
    那份报告当「取完了」读，而真相是「你要的比这里允许的多」。截断每一次都要多
    写一句话，而那句话会被读成一段说明，不是一个拒绝。

    断言落在**抓取器一次都没被问过**上：拒绝要发生在读 robots 之前，否则一次越界
    调用会先替调用方花掉一次网络往返，而它注定不会取任何页面。
    """
    http = FakeHttp(settings.data_root / "pages", site_replies(SITE, SITE_PAGES))
    result = researcher("crawl_site", _crawl_args(**arguments), crawl=_crawl_with(http))

    assert result.is_error, f"{arguments} 被接受了"
    assert needle in result.text
    assert http.asked == [], "一次被拒的调用已经发出了请求"


def test_a_wrongly_typed_seed_list_never_reaches_the_tool_body(researcher) -> None:
    """``seeds`` 不是列表时，拒它的是**声明出去的 schema**，不是函数体。

    与 ``test_a_wrongly_typed_argument_never_reaches_the_tool_body`` 同一条理由，
    换一个参数：经服务面的调用在这一层就被挡掉了，而 ``_seed_texts`` 里那句
    ``isinstance(seeds, list)`` 仍然要留着 —— 它守着直接调用这个工具的代码路径。
    断言因此只要求「坏在 seeds 上」，不钉 SDK 的措辞。
    """
    result = researcher("crawl_site", _crawl_args(seeds=SITE), crawl=_crawl_with(FakeHttp(Path("/tmp"), {})))
    assert result.is_error
    assert "seeds" in result.text


def test_a_page_ceiling_leaves_a_remaining_that_the_next_call_can_take(
    settings, researcher
) -> None:
    """页数用尽时，``remaining`` 是**续抓唯一的输入**，而且它真的接得上。

    与 ``test_crawl_site_walks_the_planned_pages_and_receipts_each_one`` 是同一条
    判据的两半：那一条说「取完时是 completed」，这一条说「没取完时说得清是哪一种」。

    续抓这一步是这条判据的全部要点：把 ``remaining`` 当种子再调一次，那些页面必须
    真的被取到。只断言 remaining 非空、不断言它能用，会让「剩下的清单」与「剩下
    的清单里装着什么」变成两件事。
    """
    http = FakeHttp(settings.data_root / "pages", site_replies(SITE, SITE_PAGES))
    crawl = _crawl_with(http)
    first = researcher("crawl_site", _crawl_args(pages=2), crawl=crawl).as_json()

    assert first["stopped_reason"] == StopReason.PAGE_CEILING.value
    assert first["receipt_count"] == 2
    assert first["remaining_count"] == first["planned_count"] - 2
    assert first["remaining_urls"] == first["planned_urls"][2:]

    second = researcher(
        "crawl_site", _crawl_args(seeds=first["remaining_urls"]), crawl=crawl
    ).as_json()
    assert {receipt["url"] for receipt in second["receipts"]} >= set(first["remaining_urls"]), (
        "把 remaining 当种子再调一次，那些页面并没有被取到"
    )


def test_the_wall_clock_is_a_ceiling_too(settings, researcher) -> None:
    """墙钟用尽是**另一个**理由，回执上说得出它，调用方因此知道下一步不一样。

    页数用尽时再调一次有用（剩下的清单在 remaining）；墙钟用尽时再调一次只会重复
    同一次超时 —— 该做的是调大预算。合成一个「没取完」就答不出这个问题。

    时钟是注入的：一条要真的等三十秒的判据最后会变成一条没人跑的判据。

    ``free=(ROBOTS, SITEMAP)`` **是承重的**，而它承的不是这条判据要测的东西：
    「墙钟里算不算前沿那两次读」是 ``test_site_crawl.py`` 里成对判据
    （``test_the_budget_covers_the_frontiers_own_reads``）的题目 —— 收费与免费
    两组各跑一遍，差的是**两页**。这里把前沿设成免费，是为了让「服务面的
    seconds 被真的执行了」不被那两次读提前吃掉。

    变异测试量过这条边界：把 ``site_crawl`` 里的 ``started`` 挪到计划之后
    （前沿因此不计时），这条判据**照样绿** —— 因为这个替身本来就让前沿免费。
    所以它没有在测那件事，docstring 也就不该那么说；那件事在别处有判据，
    而且那一处一改就红。
    """
    clock = FakeClock()
    http = SlowHttp(
        settings.data_root / "pages",
        site_replies(SITE, SITE_PAGES),
        clock=clock,
        per_page=60.0,
        free=(ROBOTS, SITEMAP),
    )
    payload = researcher(
        "crawl_site", _crawl_args(seconds=30.0), crawl=_crawl_with(http, clock=clock)
    ).as_json()

    assert payload["stopped_reason"] == StopReason.WALL_CLOCK.value
    assert payload["stopped_reason"] != StopReason.PAGE_CEILING.value, (
        "两个理由在线上必须是两个不同的字符串"
    )
    assert payload["receipt_count"] == 1, "第二页之前墙钟就该到点了"
    assert payload["elapsed_seconds"] > payload["seconds_requested"], (
        "预算是软界：最后一次抓取可以越界，而越界要看得见"
    )
    assert payload["remaining_count"] == payload["planned_count"] - 1


def test_an_unwired_crawl_says_so_instead_of_returning_an_empty_report(
    settings, researcher
) -> None:
    """没接线多页抓取时**明确拒绝**，而不是回一份「没有页面」的报告。

    这个区别与 ``search_web`` 那条逐字同形：一份空报告读起来是「这个站没有可抓的
    页面」，而真相是「这个部署没有这条能力」—— 前者会让调用方去换种子，后者该做
    的是改部署。回执里连一份空计划都不该出现：那也是一份报告。
    """
    result = researcher("crawl_site", _crawl_args())

    assert result.is_error, "没接线却返回了一次成功"
    assert "没有接线多页抓取" in result.text
    assert "没有页面" in result.text, "这句话本身应当说明它与「抓过、没有页面」的区别"


def test_crawl_site_puts_the_pages_links_on_every_receipt(settings, researcher) -> None:
    """§3 要的「逐页回执与**链接**」：每一页回执都带着它自己那几条链接。

    链接来自解析这一页的字节（``ParsedDocument.links``），所以这条判据同时守着
    两件事：回执上有链接，且那条路的每一页都被解析过 —— 「取到了字节」与「知道
    这一页有哪些链接」是两件事，而 §5 要求每一页记录后者。

    链接**逐字**给出：要绝对地址的调用方按同一份回执里的 ``final_url`` 解析。
    """
    http = FakeHttp(
        settings.data_root / "pages", site_replies(SITE, SITE_PAGES, links=("/contact-us",))
    )
    payload = researcher("crawl_site", _crawl_args(), crawl=_crawl_with(http)).as_json()

    assert payload["receipts"], "正控：这次调用一页都没取到，下面每一条与真空同形"
    for receipt in payload["receipts"]:
        links = receipt["links"]
        assert links["collected"] is True, f"{receipt['url']} 上没有链接块"
        assert [item["url"] for item in links["items"]] == ["/contact-us"]
        assert links["count"] == 1 and links["truncated"] is False
        assert receipt["final_url"], "「按 final_url 解析相对地址」要能做得到"

    assert "links_not_collected" not in {gap.get("gap") for gap in payload["gaps"]}, (
        "链接已经采集了，那条 gap 现在是一句假话"
    )
    assert "逐字" in payload["note"], "「链接是逐字的」要说在回执里，不能靠读实现"


def test_the_one_thing_this_build_does_not_deliver_is_still_named(
    settings, researcher
) -> None:
    """§3 要的「持久操作 id」本构建没有交付 —— 它仍在报告里说明。

    链接那一半已经交付了（上一条判据），所以这条只剩操作 id。它写在 ``note`` 而
    不是 ``gaps`` 里是有意的：``gaps`` 收的是「这一次抓取发生了什么、但不完整」
    （站点规则、链接缺席那一类），而操作 id 是**工具契约**上的欠账，与这一次抓了
    哪个站无关。一个安静的缺字段会被读成「这次调用用不着操作 id」。
    """
    http = FakeHttp(settings.data_root / "pages", site_replies(SITE, SITE_PAGES))
    payload = researcher("crawl_site", _crawl_args(), crawl=_crawl_with(http)).as_json()

    assert "操作 id" in payload["note"]
    assert "remaining" in payload["note"], "续抓的入口要说出来"
    assert payload["receipts"], "正控：这条 note 出自一次真的取到了页面的调用"


def test_the_url_lists_are_bounded_and_the_counts_are_exact(settings, researcher) -> None:
    """URL 清单有界，而**计数是准的**。

    站点地图可以有几千条 URL，原样回传会让一次工具调用的回执变成一个几兆的载荷。
    截断本身没问题 —— 问题是截断了不说：``planned_count`` 与 ``remaining_count``
    给的是准确总数，所以「还有多少没取」这个问题仍然答得出来（§11：不得静默截断）。
    """
    many = tuple(f"{SITE}/news/{index}" for index in range(MAX_URLS_RETURNED + 10))
    http = FakeHttp(settings.data_root / "pages", site_replies(SITE, many))
    payload = researcher("crawl_site", _crawl_args(pages=1), crawl=_crawl_with(http)).as_json()

    assert payload["planned_count"] > MAX_URLS_RETURNED
    assert len(payload["planned_urls"]) == MAX_URLS_RETURNED
    assert payload["urls_truncated"] is True
    assert payload["remaining_count"] == payload["planned_count"] - payload["receipt_count"]
    assert len(payload["remaining_urls"]) <= MAX_URLS_RETURNED


# ----------------------------------------------------------------------
# 多页抓取：**每一页**各落一行证据
# ----------------------------------------------------------------------
def test_every_fetched_page_gets_its_own_row(settings, researcher, evidence) -> None:
    """一页一行，id 各不相同，行数与 ``receipt_count`` 对得上。

    抓一遍站只留一行「我抓过这个站」会让被引用的是**那次调用**，而不是某一页的
    字节 —— 于是「这句话在哪一页上」这个问题再也答不出来，而报告上看不出区别。
    """
    http = FakeHttp(settings.data_root / "pages", site_replies(SITE, SITE_PAGES))
    payload = researcher(
        "crawl_site", _crawl_args(account_id=ACCOUNT), crawl=_crawl_with(http)
    ).as_json()

    blocks = [receipt["evidence"] for receipt in payload["receipts"]]
    assert len(blocks) == payload["receipt_count"] > 1, "正控：这一轮真的取了不止一页"
    for block in blocks:
        _assert_evidence_shape(block)
        assert block["status"] == "written", block["reason"]

    ids = [block["evidence_id"] for block in blocks]
    assert len(set(ids)) == len(ids), "同一个站的两页共用了同一个 id"
    assert len(evidence.list_evidence(ACCOUNT)) == len(ids)


def test_a_crawl_without_an_account_says_so_on_every_page(settings, researcher) -> None:
    """没有账号的多页抓取：每一页都照常取了回来，而每一页都说得出没有归档。

    逐页说而不是在顶层说一句：调用方是按页读这些回执的（``remaining_urls``
    就是拿它们续抓的），顶层一句「没有账号」会被读成「整次调用不作数」，
    而事实是每一页的字节都拿到了。
    """
    http = FakeHttp(settings.data_root / "pages", site_replies(SITE, SITE_PAGES))
    payload = researcher("crawl_site", _crawl_args(), crawl=_crawl_with(http)).as_json()

    assert payload["account_id"] == ""
    assert payload["receipt_count"] > 1
    for receipt in payload["receipts"]:
        block = receipt["evidence"]
        _assert_evidence_shape(block)
        assert block["status"] == "no-account", block["reason"]
        assert receipt["body_ref"], "证据没落，字节仍然要在"


# ----------------------------------------------------------------------
# §5 —— 领不到「这次研究谁」，就一次检索都不会发生
# ----------------------------------------------------------------------
#: 这一段脚本化的 worker 用的两个主体。**左边那个是 2026-09-23 那次的形状**：
#: ``known_accounts`` 清单里身份那一格写着内部 id。右边那个只是把名字填对了，
#: 别的每一格都一样 —— 两条判据之间**只差这一个变量**。
SLUG = "kent-003"
NAMED = "example-school-one"
NAMED_ORGANISATION = "Example School One"


def _dispatched_work_item(settings: Settings, *, account_id: str, organisation: str) -> str:
    """摆一个已编译、已派发的 work item，并返回它的 id。

    **走存储层而不是受理面**，而且这是刻意的：受理时 ``identity_refusal`` 会把整份
    清单拒掉（一件都不编译，见 ``tests/test_mcp_job_tools.py``），所以一份只有 slug
    的身份**经过受理面是造不出来的** —— 而它在存储层是合法的一行，历史那一批工作
    就长这样。要测的是「领活这一侧挡不挡得住」，所以要用它真的出现过的那种走法造它。

    编译时读的必须是**盘上此刻那一批**（``load_profile``），不能是 ``tests.fakes``
    里那份合成快照。``get_assignment`` 会把钉住的字节按版本读回来再装载一次 ——
    合成快照里没有 ``profile.yaml``，于是每一件用它的工作在**领活**这一步就被拒，
    而拒的理由（"那一批字节装载不出来"）与这两条判据要测的身份问题毫无关系。
    换成真实的字节之后，「走存储层」这件事一点没变：仍然是 ``SqliteJobStore``
    ── 受理面那道整份拒绝的门照样被绕开，这正是这一段要的形态。
    """
    store = SqliteJobStore(settings.database_path)
    job = store.admit(
        scene_id=settings.profile,
        input_kind=JobInputKind.KNOWN_ACCOUNTS,
        input_ref="kent-2026-09-21",
        idempotency_key=f"subject-identity-{account_id}",
        request_sha256=sha256_text(f"kent-2026-09-21:{account_id}"),
    )
    dossier = SubjectDossier(
        account_id=account_id,
        organisation=organisation,
        domain="",
        country="GB",
        source_ref="tests/test_mcp_research_tools.py",
        input_sha256=sha256_text(f"row:{account_id}"),
    )
    (item,) = store.compile_work_items(
        job.job_id, (dossier,), scene=load_profile(settings).scene_files
    )
    store.map_execution(item.work_item_id, dispatch_task_id="dispatch-task-0001")
    return item.work_item_id


def _scripted_worker(call, work_item_id: str, search: RecordingSearch, jobs) -> None:
    """worker 照回执走的那两步：**先领活，再拿领到的那份身份去检索**。

    这两步之间没有第三种来源 —— 一个 worker 能拿去检索的账户与用词只可能来自
    assignment（``get_assignment`` 是 §3 里唯一交出 ``subject`` 的地方）。所以这一段
    脚本不自己编查询词：领到什么就用什么，领不到就停。

    写成一个函数而不是散在两条用例里：那一对判据的区别只该在**输入那一行**上，
    走法必须逐字相同，否则「0 次」与「1 次」的差别可能来自走法不同。
    """
    assignment = call(
        "get_assignment", {"work_item_id": work_item_id}, search=search, jobs=jobs
    )
    if assignment.is_error:
        return
    subject = assignment.as_json()["subject"]
    call(
        "search_web",
        {
            "account_id": subject["account_id"],
            "queries": [f"{subject['organisation']} grounds staff"],
        },
        search=search,
        jobs=jobs,
    )


def test_a_worker_that_cannot_be_told_who_to_research_spends_nothing(
    settings: Settings, researcher, ledger: SqliteBudgetLedger, bundle: ProfileBundle
) -> None:
    """**§5 的那条判据**：身份那一格只有内部 slug ⇒ 领不到活 ⇒ **检索 0 次**。

    这一条与下面那条正控成对，区别**只在输入那一行的身份那一格**。判据的可判别性
    完全落在这里：一个把 assignment 的拒绝去掉的实现，会让脚本的第二步真的发出
    ``kent-003 grounds staff`` —— 也就是那次真的发生过的事 —— 而 ``search.sent``
    从空变成一条。付钱的次数是这条判据的**主语**，不是它的背景。

    为什么不是「断言 worker 没调 search_web」：那是断言脚本自己没写那一行，与实现
    无关。这里断的是**照着回执走会花几次** —— 脚本会走该走的那一步，走不到第二步
    是因为回执把它挡住了。

    最后一半是账：次数不是金额，但它是本仓唯一逐次可核的那个数。搜索 0 次时这个
    scope 的账本**一行都不该有** —— 一次被拒绝的领活不该顺手播种一条上限，也不该
    留下预留（``usage`` 返回 ``None`` 表示连上限都没设过，与「用量为 0」是两件事）。
    """
    search = RecordingSearch(hits=1)
    work_item_id = _dispatched_work_item(settings, account_id=SLUG, organisation=SLUG)
    jobs = lambda: SqliteJobStore(settings.database_path)  # noqa: E731

    _scripted_worker(researcher, work_item_id, search, jobs)

    assert search.sent == [], f"领不到身份却真的检索了：{[q.text for q in search.sent]}"
    assert search.limits == []
    assert _usage(ledger, _scope(bundle, SLUG)) is None


def test_a_worker_that_can_be_told_who_to_research_spends_exactly_once(
    settings: Settings, researcher, ledger: SqliteBudgetLedger, bundle: ProfileBundle
) -> None:
    """正控：同一个脚本，只是身份那一格填了一个真名字 ⇒ 检索**恰好一次**。

    少了它，上面那条「0 次」是被一个整个坏掉的实现满足的：连 ``get_assignment``
    与 ``search_web`` 都接不上的部署同样会绿，而它证明不了任何事。

    查询词来自回执里的 ``organisation``，不是脚本里写死的名字 —— 它要证明的正是
    「worker 拿到的**是名字**」，而那件事只有在词里看得见。
    """
    search = RecordingSearch(hits=1)
    work_item_id = _dispatched_work_item(
        settings, account_id=NAMED, organisation=NAMED_ORGANISATION
    )
    jobs = lambda: SqliteJobStore(settings.database_path)  # noqa: E731

    _scripted_worker(researcher, work_item_id, search, jobs)

    assert [query.text for query in search.sent] == [f"{NAMED_ORGANISATION} grounds staff"]
    assert search.sent[0].scope == NAMED

    usage = _usage(ledger, _scope(bundle, NAMED))
    assert usage is not None and (usage.spent, usage.reserved) == (1, 0), (
        "真的发出去了却没落成已花"
    )


# ----------------------------------------------------------------------
# 服务端交出的「这一步」，与收下它的那条工具
# ----------------------------------------------------------------------
def test_the_plan_step_the_server_hands_out_is_the_one_the_search_tool_accepts(
    settings: Settings, researcher
) -> None:
    """**生产路径**：``get_assignment`` 交出的那一步，正是 ``search_web`` 收下的那一步。

    上面 ``_scripted_worker`` 那两步之间传的是**身份**；这一条传的是**步骤** ——
    第二段会话里那个字面量不是本文件写下的，它只来自第一段的回执，两段之间隔着
    一次会话边界。所以它证伪的是一件具体的事，而且两个方向都堵住了：

    * 把 ``guidance.plan_steps`` 拿掉 ⇒ 这条红在 ``KeyError`` 上；
    * 让工具那一侧的判据去查**另一张表** ⇒ 这条红在回显上（服务端交出的那一步
      被拒或被改写）。

    两边不可能各自独立地满足它。这正是缺口 A 的形状：「没人把这一步交出去」与
    「交出去的那一步不是工具认的」在报告上同形，而它们都不是「这一格没有数据」。

    ``set(steps) <= set(SOURCE_METHODS)`` 那一句把「这是一张局部副本」也挡掉：
    一张与注册表无关的表（比如三个写死的字符串）能通过回显那两句，却过不了
    ``UNPLANNED_STEP not in steps`` —— 而后者正是这一格**有意义**的条件。
    """
    work_item_id = _dispatched_work_item(
        settings, account_id=NAMED, organisation=NAMED_ORGANISATION
    )
    jobs = lambda: SqliteJobStore(settings.database_path)  # noqa: E731

    assignment = researcher(
        "get_assignment", {"work_item_id": work_item_id}, jobs=jobs
    ).as_json()
    steps = assignment["guidance"]["plan_steps"]

    assert steps, (
        "回执交了一张空表 —— 「这一步服务的是计划里的哪一步」仍然没有人答得出来，"
        "而 worker 手上的 source_method 那一格只能靠猜"
    )
    assert set(steps) <= set(SOURCE_METHODS), f"交出来的步骤不在闭集里：{steps}"
    # 注册表里那两条**没有任何声明给入口**的 id 不该出现在这里。少了这一句，
    # 一个把整个注册表原样倒出来的实现也能过上面那两句。
    assert UNPLANNED_STEP not in steps, (
        f"把没开出来的路也交了出去：{UNPLANNED_STEP} 今天没有任何声明给它入口"
    )

    step = steps[0]
    search = RecordingSearch()
    payload = researcher(
        "search_web",
        {"account_id": ACCOUNT, "queries": ["devon clubs"], "source_method": step},
        search=search,
    ).as_json()

    assert payload["source_method"] == step, (
        f"服务端交出的那一步被工具拒了或改写了：交出 {step!r}，回执 {payload['source_method']!r}"
    )
    assert [query.text for query in search.sent] == ["devon clubs"], (
        "照着回执填了步骤，检索却没发生"
    )


# ----------------------------------------------------------------------
# run_discovery —— 链上第 3 步在干净客户端里走得通（§11）
# ----------------------------------------------------------------------
def _run(
    controller,
    *,
    arguments: dict | None = None,
    scope: str = DISCOVERY_SCOPE,
    region: str = DISCOVERY_REGION,
    **kw,
):
    """跑一轮发现并返回 ``(驱动器观察结果, 回执)``。

    ``scope`` / ``region`` 提出来是因为它们**每一次都要给**（``region`` 没有缺省值）；
    其余参数原样传给驱动器，好让调用点能挑检索替身与抓取替身。
    """
    outcome = controller(
        "run_discovery",
        {"scope": scope, "region": region, **(arguments or {})},
        **kw,
    )
    return outcome, outcome.call("run_discovery")


def test_a_controller_runs_a_round_and_the_pool_can_be_submitted(
    controller, settings: Settings, bundle: ProfileBundle, ledger: SqliteBudgetLedger
) -> None:
    """正控：一趟跑完，工件与回执都在盘上，而且**受理真的收得下它**。

    这一条是 §11 链上第 3 步（Source Discovery）的存在性证明。原先它只有 CLI
    （``interfaces/cli.py::run-discovery``），而 §11 逐字写着「任一步骤只能通过
    CLI 则不计入」—— 干净客户端里这一步根本不存在。

    ``next_step`` 里那句话不是文案：下面真的拿它的 ``artifact_ref`` 去跑了一遍
    ``submit_research`` 用的那个解析函数（``discovery_scope_subjects``，也就是
    ``application/inputs.py`` 里 ``discovery_scope`` 这一类输入的唯一入口）。
    只断言字符串长得对，等于把「链能走通」换成「字符串里出现了另一个工具的名字」。
    """
    search = HostSearch()
    _outcome, result = _run(controller, search=search, http=_titled(settings))

    assert not result.is_error, result.text
    payload = result.as_json()

    assert payload["scene_id"] == bundle.profile_id, "回执里的场景不是这一次真的站的那个"
    assert payload["scope"]["region"] == DISCOVERY_REGION
    assert payload["artifact_ref"] == DISCOVERY_SCOPE

    pool = payload["pool"]
    assert pool["kept"] == len(DISCOVERY_HOSTS)
    assert pool["written"] is True
    assert pool["why_not_written"] is None
    assert pool["counts_line"], "回执里没有那一行计数"

    # 工件与回执都在盘上，而且路径是**相对数据根**的引用 —— 绝对路径会把这次运行
    # 的机器布局带进回执，换一台机器读它就对不上了。
    for key in ("artifact_path", "receipt_path"):
        assert pool[key] and not pool[key].startswith("/"), f"{key} 是绝对路径"
        assert (settings.data_root / pool[key]).exists(), f"{key} 指着一个不存在的文件"
    report_path = settings.benchmarks_dir / f"{payload['run_id']}.json"
    assert report_path.exists()
    assert json.loads(report_path.read_text(encoding="utf-8"))["run_id"] == payload["run_id"]

    # 链的交接棒：这个名字必须被受理那一侧**真的解析得动**。
    resolution = discovery_scope_subjects(settings.inputs_dir, payload["artifact_ref"])
    assert resolution.blocker == "", resolution.blocker
    assert len(resolution.subjects) == len(DISCOVERY_HOSTS), (
        "工件里是几台主机，受理侧就该解析出几个主体"
    )
    assert f'input_kind="discovery_scope", input_ref="{payload["artifact_ref"]}"' in (
        payload["next_step"]
    )

    assert payload["source_method_id"] == OPEN_WEB_SOURCE_METHOD.id
    assert any("补充证据" in caveat for caveat in payload["caveats"]), (
        "SOP L82 那条边界必须在回执里说出来"
    )


def test_the_ceiling_is_the_profile_not_the_caller(
    controller, settings: Settings, bundle: ProfileBundle, ledger: SqliteBudgetLedger
) -> None:
    """额度上限来自冻结的 profile，**调用方给的数字一个都改不了它**。

    ``limit`` 与 ``max_candidates`` 是这一轮的**形状**（一条查询取几条、池子到
    几个为止），不是额度。调用方可能是一个模型，所以「上限」不能是它传进来的
    任何一个字段 —— 这条与 ``search_web`` 的 ``budget.ceiling`` 是同一条纪律。

    两个方向都要钉：把 ``limit`` 调大之后 ``ceiling`` 不动，而且**真的发出去的
    查询条数**仍然只到 profile 那个数 —— profile 展开出来的检索式（这一份是几百
    条）比额度多得多，所以「发了几条」这件事只由额度决定，不由计划决定。

    计划比额度大是**常态**，不是异常：计划是模板的完全展开，额度是这一次运行
    允许花的钱。少掉的那些**不消失** —— 它们留在逐条读数里，另有一条 caveat 把
    「没搜」与「搜了没结果」分开说。静默丢掉它们会让这一轮的读数看起来像「网络里
    只有这二十条检索式的结果」。

    而「这一次到底发出去几条」在 ``queries.by_outcome`` 的 ``sent`` 那一格，**不是**
    在 ``queries.count`` 上：后者是计划展开出来的条数（没轮上的检索式同样占一行，
    它们必须占 —— 否则「跑了 20 条」与「542 条里跑了 20 条」在回执上长得一样）。
    两个数在额度用尽时差得很远，所以这一条同时钉住它们各自的值。
    """
    search = HostSearch(hosts=DISCOVERY_HOSTS[:1])
    _outcome, result = _run(
        controller,
        search=search,
        http=_titled(settings),
        arguments={"limit": 50, "max_candidates": 999},
    )

    assert not result.is_error, result.text
    payload = result.as_json()
    ceiling = bundle.profile.search_max_queries
    queries = payload["readings"]["queries"]

    assert payload["readings"]["usage"][0]["ceiling"] == ceiling
    assert payload["readings"]["usage"][0]["spent"] == len(search.sent) == ceiling, (
        "额度没有正好用到上限：少了是没跑完，多了是超了"
    )
    assert queries["count"] > ceiling, (
        "这一份 profile 展开出来的检索式不该比额度还少，否则下面那条 caveat 无从谈起"
    )
    assert queries["by_outcome"]["sent"] == ceiling, (
        "「发出去几条」这一格必须等于额度上限，而不是计划条数"
    )
    assert queries["by_outcome"]["budget_exhausted"] == 1, (
        "撞上上限的那**一条**检索式要自己占一格：它的处置与「没轮上」不同 —— "
        "「预留下了但没成」是可以解释给调用方的，而它一旦并进 not_attempted，"
        "「额度用尽」就只剩 stops 里的一句话，看不出是哪一条撞的"
    )
    assert queries["by_outcome"]["not_attempted"] == queries["count"] - ceiling - 1, (
        "没轮上的检索式必须逐条记着，不是从条数里消失"
    )
    assert "budget_exhausted" in payload["stops"], "额度用尽没被记成一档停止"
    assert any("没有发出去" in caveat for caveat in payload["caveats"]), (
        "没发出去的那些检索式必须被说出来"
    )
    assert search.limits and all(limit == 50 for limit in search.limits), (
        "调用方给的 limit 本来就该传给检索端口 —— 它不是被忽略了，是它改不了上限"
    )


def test_a_blank_region_is_refused_before_any_query_is_sent(
    controller, ledger: SqliteBudgetLedger
) -> None:
    """``region`` 空白 ⇒ 拒绝，而且**在任何检索之前**、在任何额度之前。

    反面是「先扣住、再让这一轮跑不起来」：那笔预留按 §8 不会退回，于是额度会被
    一次次不成立的调用吃光，而报告上只显示「没有候选」。所以断言落在**账本**上：
    ``usage`` 是 ``None``（连上限都没播种过），而不是「用量是 0」——后者表示有人
    动过这个 scope。
    """
    search = HostSearch()
    _outcome, result = _run(controller, region="   ", search=search)

    assert result.is_error
    assert "region" in result.text and "空白" in result.text
    assert search.sent == [], f"region 不成立却真的检索了：{[q.text for q in search.sent]}"
    assert _usage(ledger, DISCOVERY_SCOPE) is None, "一次没成立的调用动了账本"


def test_a_missing_region_is_refused(controller) -> None:
    """``region`` **没有缺省值** —— 不给就是不给，不是「默认 GB」。

    替它编一个值等于替这一次运行做了一个它没做过的决定，而那个决定不留痕：它会
    逐字进检索式、进工件、进回执，读的人无从分辨那是冻结的地区还是引擎的兜底。
    这一条与 CLI 的 ``--region`` 必填是同一个决定，经同一个 ``ToolError`` 说出来。
    """
    outcome = controller("run_discovery", {"scope": DISCOVERY_SCOPE})
    result = outcome.call("run_discovery")
    assert result.is_error, "少一个必填参数却成功了"


def test_no_search_provider_is_refused_before_anything_is_spent(
    controller, settings: Settings, ledger: SqliteBudgetLedger
) -> None:
    """没有接线检索提供方时，一次调用不得动到额度一分一毫、也不得留下工件。

    与 ``test_no_provider_is_refused_before_any_budget_is_touched`` 同一处、同一条
    理由：没有接线的部署上，一次「先扣额度、再让 provider 抛错」的调用会把额度一次次
    吃光，而报告上只显示「没有候选」。这不是一次「跑过、没有候选」。
    """
    outcome, result = _run(controller, search=None)

    assert result.is_error
    assert "没有接线检索提供方" in result.text
    assert "SERPER_API_KEY" in result.text, "拒绝的理由要指出该看哪里"
    assert _usage(ledger, DISCOVERY_SCOPE) is None, "一次没接线的调用动了账本"
    assert not (settings.inputs_dir / f"{DISCOVERY_SCOPE}.jsonl").exists(), (
        "没跑起来的调用在盘上留下了工件"
    )


def test_an_empty_pool_writes_nothing_and_says_where_the_answer_is(
    controller, settings: Settings
) -> None:
    """一行都没产出时**不写工件**，并把「为什么空」指向 stops 与逐条读数。

    空工件会被读成「跑了、一行都没成」，而它其实什么都没声明 —— 连
    ``read_motherpool`` 都会拒绝它。这一次的产物不是一份看起来正常的空池，
    是一条说得出的停止理由。
    """
    _outcome, result = _run(
        controller,
        search=HostSearch(hosts=()),  # 检索过、一条命中都没有
        http=_titled(settings),
    )

    assert not result.is_error, result.text
    payload = result.as_json()

    assert payload["pool"]["kept"] == 0
    assert payload["pool"]["written"] is False
    assert payload["pool"]["artifact_path"] is None
    assert payload["pool"]["why_not_written"] and "空工件" in payload["pool"]["why_not_written"]
    assert payload["stops"], "池子空了却没说出停在哪一档"
    assert not (settings.inputs_dir / f"{DISCOVERY_SCOPE}.jsonl").exists(), (
        "一行都没有却写了工件"
    )
    assert "没有可以喂进受理的东西" in payload["next_step"], (
        "空池的下一步不能说成「把这个工件喂进受理」"
    )


def test_the_artifact_name_cannot_be_reused_and_the_refusal_costs_nothing(
    controller, settings: Settings
) -> None:
    """同一个范围名跑第二遍 ⇒ 拒绝，而且**在花掉任何一次检索之前**。

    名字在跑之前就是已知的，所以「这个名字被占了」这件事必须在**发出第一发查询
    之前**说出来 —— 否则它会变成一次已经付过钱的失败。工件用 ``"x"`` 打开、存在
    即抛，不覆盖：盘上有两份字节而回执只指着其中一份，是「这份回执指着哪一份」
    这个问题的答案是两个。
    """
    search = HostSearch()
    _outcome, first = _run(controller, search=search, http=_titled(settings))
    assert not first.is_error, first.text
    spent = len(search.sent)
    assert spent > 0, "第一遍一条检索都没发，下面那条「第二遍没花钱」就没有意义"

    _outcome, second = _run(controller, search=search, http=_titled(settings))
    assert second.is_error, "同一份工件被写了两遍"
    assert f"{DISCOVERY_SCOPE}.jsonl" in second.text and "已经存在" in second.text
    assert len(search.sent) == spent, "被拒的那一遍已经花过钱了"
    assert "换一个范围名" in second.text, "拒绝的理由要给出路"


def test_a_bounded_reading_says_it_was_bounded_and_the_count_stays_exact(
    controller, settings: Settings
) -> None:
    """逐条读数被截到上限，而**每一格的 count 仍然是准确的总数**。

    静默截断会让「一共 50 条」与「有 500 条、你看到的是前 50 条」在回执上长得
    一模一样，而两者的下一步不同（前者可以据此收工，后者要去看盘上的完整报告）。
    与 ``crawl_site`` 的 ``MAX_LINKS_RETURNED`` 同一条纪律。

    命中数在多台主机上会成倍于站点数（每条检索式对每台主机各出一条），所以这一条
    用 ``hits_per_host`` 把命中数顶到上限之上，同时不动站点数 —— 于是「截了」与
    「总数还在」两件事在同一份回执里各有一个格。
    """
    search = HostSearch(hosts=DISCOVERY_HOSTS, hits_per_host=8)
    _outcome, result = _run(controller, search=search, http=_titled(settings))

    assert not result.is_error, result.text
    payload = result.as_json()
    hits = payload["readings"]["hits"]

    assert hits["count"] > MAX_DISCOVERY_LINES, "这一轮没顶到上限，这条判据什么都没测"
    assert hits["truncated"] is True
    assert len(hits["records"]) == MAX_DISCOVERY_LINES
    assert payload["truncated"]["lines"] == MAX_DISCOVERY_LINES
    assert payload["readings"]["sites"]["count"] == len(DISCOVERY_HOSTS), (
        "站点数跟着命中的截断一起缩了"
    )
    assert payload["pool"]["kept"] == len(DISCOVERY_HOSTS)


def test_the_planned_count_and_the_sent_count_are_two_different_numbers(
    controller, settings: Settings
) -> None:
    """``queries.count`` 是**计划**的条数，发出去几条在 ``by_outcome.sent``。

    这一份 profile 的模板完全展开之后远多于这一次运行允许花的额度，所以额度用尽时
    两个数差得很远。它们如果在回执上合成一个，读到的就是「542 条里跑了 20 条」被读成
    「跑了 542 条」—— 而这一轮最要紧的一个数恰恰是前者。

    没轮上的那些检索式**必须**逐条记着：它们既不是「搜了没结果」，也不是「不存在」。
    所以第五格 ``not_attempted`` 是这一层的话，不是
    :class:`~marketops.domain.enums.QueryOutcome` 的第五个成员 —— 那几条查询根本没走到
    记账那一步，把它们塞进终态词表会让「预算用尽」多出一个同义词。
    """
    search = HostSearch(hosts=DISCOVERY_HOSTS[:1])
    _outcome, result = _run(controller, search=search, http=_titled(settings))

    assert not result.is_error, result.text
    payload = result.as_json()
    queries = payload["readings"]["queries"]

    assert sum(queries["by_outcome"].values()) == queries["count"], (
        "各格之和必须回到计划条数，否则有检索式从读数里漏掉了"
    )
    assert queries["by_outcome"]["sent"] < queries["count"], (
        "这一轮没有用尽额度，这条判据什么都没测"
    )
    assert queries["by_outcome"]["sent"] == payload["readings"]["usage"][0]["spent"], (
        "「发出去了几条」与台账上扣掉的格数必须是同一个数，不是两个都可以的说法"
    )
    assert "not_attempted" not in {outcome.value for outcome in QueryOutcome}, (
        "「没轮上」被写进了终态词表"
    )
    assert len(queries["records"]) == min(queries["count"], MAX_DISCOVERY_LINES)


def test_a_researcher_cannot_see_or_call_a_discovery_run(
    settings: Settings, tmp_path: Path
) -> None:
    """研究令牌**看不见**也**调不动**这条工具 —— 两半都要。

    ``run_discovery`` 花的是冻结的检索额度，所以它是 ``_CONTROL``。``MCP_SERVICE.md``
    §6 要求越权的表现与「不存在」是**同一句话**（``Unknown tool``），否则一次越权
    探测能从错误信息里读出一条工具的存在。而只看 ``tools/list`` 是不够的 —— 列表
    是给人看的，调用才是承重的那一半。
    """
    ring, token = _ring(tmp_path, "claude-research-2", Role.RESEARCHER)
    handlers = research_handlers(
        settings=settings,
        search=HostSearch(),
        acquisition=DocumentAcquisition(http=_titled(settings)),
        router=DocumentRouter([HtmlDocumentParser()]),
        ledger_factory=lambda: SqliteBudgetLedger(settings.database_path),
        evidence_store_factory=lambda: SqliteEvidenceStore(settings.database_path),
    )
    outcome = drive_with_settings(
        settings=settings,
        tokens=ring,
        token=token,
        research=handlers,
        calls=(("run_discovery", {"scope": DISCOVERY_SCOPE, "region": DISCOVERY_REGION}),),
        want_tool_list=True,
    )

    assert not outcome.handshake_error, outcome.handshake_error
    assert "run_discovery" not in outcome.tool_names, "研究令牌看得见控制器侧的工具"
    assert outcome.call("run_discovery").text == "Unknown tool: run_discovery", (
        "越权与不存在必须是同一句话"
    )


# ----------------------------------------------------------------------
# 跨 session 共享 —— 两个会话、一本账（G4 §10 那半句）
# ----------------------------------------------------------------------
#
# §10 逐字：「**预算和限流跨 session 共享**」。这句话在本文件里落成两对判据，
# 一对一种账，而两对的主语都是**两个会话**：
#
# * 预算那一对看的是**账本**（``SqliteBudgetLedger``，落盘在 ``settings.database_path``）；
# * 限流那一对看的是**来源账**（``interfaces/composition.py::_GOVERNOR``，进程里那一本）。
#
# ## 为什么主语必须是「两个会话」而不是「两个对象」
#
# 「同一本账」在本仓已经有用例（``test_http_access_governance.py`` 的
# ``test_the_composition_root_hands_every_fetcher_the_same_book`` 断言两个抓取器
# ``_governor is`` 同一个）。那一条判的是**组合根**，而 §10 问的是另一个问题：
# **会话边界会不会把账重置**。一个「每个会话各建一份依赖」的实现在对象同一性上
# 看不出来 —— 它每一次都忠实地把**自己那一本**交给了自己那两个抓取器。
#
# ## 两个会话在这里是**真的两个**
#
# 每一段会话一次独立的 ``initialize`` 握手、一条独立的 ``Authorization`` 头、
# 一个**不同的令牌**（两个 ``principal``、两个 ``digest``），而令牌是服务端从
# 传输层读出来的身份，不是参数（见 ``tests/test_mcp_isolation.py`` 的 §7 那一段）。
# 两个 ``MCPServer`` 实例也是两个 —— 比线上更严：线上那一个进程只服务一个实例。
SESSION_ONE = "claude-research"
SESSION_TWO = "claude-research-2"


def _session(
    *,
    settings: Settings,
    ring: TokenRing,
    token: str,
    calls: tuple[tuple[str, dict[str, object]], ...],
    ledger: SqliteBudgetLedger,
    evidence: SqliteEvidenceStore,
    search: SearchProvider | None = None,
    http: HttpFetcher | None = None,
) -> SessionOutcome:
    """一段**真的**会话：一次握手 + 若干次调用。

    与 ``researcher`` 夹具同一个形状，只差两点：令牌由调用方给（于是两次调用之间
    可以换一个身份），以及一次会话里可以走**多次**调用 —— 后者是这一节承重的地方，
    「一个会话」的界是那次握手，不是那一次调用。
    """
    handlers = research_handlers(
        settings=settings,
        search=search,
        acquisition=DocumentAcquisition(
            http=http if http is not None else _http(settings)
        ),
        router=DocumentRouter([HtmlDocumentParser()]),
        ledger_factory=lambda: ledger,
        evidence_store_factory=lambda: evidence,
    )
    outcome = drive_with_settings(
        settings=settings,
        tokens=ring,
        token=token,
        research=handlers,
        calls=calls,
        want_tool_list=False,
    )
    assert not outcome.handshake_error, outcome.handshake_error
    return outcome


def _two_rings(
    tmp_path: Path,
) -> tuple[tuple[TokenRing, str], tuple[TokenRing, str]]:
    """两个**不同的**主体各铸一个环。相同则这一节的两条判据都退化成同一个人。"""
    one = _ring(tmp_path, SESSION_ONE, Role.RESEARCHER)
    two = _ring(tmp_path, SESSION_TWO, Role.RESEARCHER)
    assert one[1] != two[1], "两个会话拿到了同一个令牌"
    return one, two


# ----------------------------------------------------------------------
# 预算：两个会话花同一笔
# ----------------------------------------------------------------------
def test_two_sessions_spend_one_search_ceiling(
    settings: Settings,
    tmp_path: Path,
    ledger: SqliteBudgetLedger,
    evidence: SqliteEvidenceStore,
    bundle: ProfileBundle,
) -> None:
    """同一个账号的两个会话，**合计**不得超过那一条上限。

    上限来自冻结的 profile（``search.max_queries``），不是参数 —— 所以第二个会话
    不是「把它自己那 4 条花完」，而是「把第一个会话剩下的花完」：撞穿之后它发出
    去的是 ``ceiling - first`` 条，而不是它请求的那 4 条。**这正是「跨 session
    共享」的读数**：一个每个会话各有一本账的实现，会让两个会话各自花满 18 与 4
    条，而报告上两边都「跑完了」。
    """
    ceiling = bundle.profile.search_max_queries
    first = ceiling - 2
    assert first >= 2 and 4 <= ceiling, "这一条判据要留出「第二个会话撞穿」的空间"

    (one_ring, one_token), (two_ring, two_token) = _two_rings(tmp_path)
    # 一个提供方，两段会话 —— 与线上同形：进程里只有一条通向厂商的线。
    search = RecordingSearch()

    first_session = _session(
        settings=settings,
        ring=one_ring,
        token=one_token,
        ledger=ledger,
        evidence=evidence,
        search=search,
        calls=(
            (
                "search_web",
                {
                    "account_id": ACCOUNT,
                    "queries": [f"session-one-q{index}" for index in range(first)],
                },
            ),
        ),
    )
    first_payload = first_session.call("search_web").as_json()
    assert first_payload["queries_sent"] == first, "第一个会话自己就没花完"
    assert first_payload["errors"] == []

    second_session = _session(
        settings=settings,
        ring=two_ring,
        token=two_token,
        ledger=ledger,
        evidence=evidence,
        search=search,
        calls=(
            (
                "search_web",
                {
                    "account_id": ACCOUNT,
                    "queries": [f"session-two-q{index}" for index in range(4)],
                },
            ),
        ),
    )
    second_payload = second_session.call("search_web").as_json()

    assert len(search.sent) == ceiling, (
        f"两段会话一共发出了 {len(search.sent)} 条查询，上限是 {ceiling} —— "
        "多出来的那几条说明第二个会话没有看见第一个会话花掉的钱"
    )
    # 第二个会话请求的 4 条里，只有上限剩的那 2 条出去了；第 3 条撞穿，第 4 条
    # **连试都没试**（`_search_web` 在第一次 EXHAUSTED 上就停）。三样都要断言：
    # 「发出去了几条」与「请求了几条」是两件事，合成一句就会把「有界停止」读成
    # 「这个会话只请求了 3 条」。
    assert second_payload["queries_sent"] == 2
    assert [
        receipt["outcome"] for receipt in second_payload["queries"]
    ] == ["sent", "sent", "budget_exhausted"]
    assert second_payload["queries_not_attempted"] == 1
    assert second_payload["errors"] == [], (
        "撞穿上限不是一次「调用失败」—— 合成一句话之后，报告上就再也分不出"
        "「额度用尽，该停」与「厂商拒了」"
    )
    assert second_payload["budget"]["remaining"] == 0
    assert second_payload["budget"]["ceiling"] == ceiling

    usage = _usage(ledger, _scope(bundle))
    assert usage is not None and (usage.spent, usage.reserved) == (ceiling, 0), (
        f"账上的净额是 {usage}，而不是恰好那一条上限"
    )


def test_the_same_second_session_alone_spends_all_four(
    settings: Settings,
    tmp_path: Path,
    ledger: SqliteBudgetLedger,
    evidence: SqliteEvidenceStore,
) -> None:
    """**对照**：同一个账号、同一段请求，账上没被别人花过时四条全都发得出去。

    没有这一条，上面那个「只发了 2 条」可能只是「4 条查询里有 2 条本来就不该发」
    （参数校验、上限写死、提供方拒了……）。它是那条判据的分母：差别只在于
    第一个会话在不在。
    """
    (_, one_token), (two_ring, two_token) = _two_rings(tmp_path)
    assert one_token != two_token
    search = RecordingSearch()

    second_session = _session(
        settings=settings,
        ring=two_ring,
        token=two_token,
        ledger=ledger,
        evidence=evidence,
        search=search,
        calls=(
            (
                "search_web",
                {
                    "account_id": ACCOUNT,
                    "queries": [f"session-two-q{index}" for index in range(4)],
                },
            ),
        ),
    )
    payload = second_session.call("search_web").as_json()

    assert payload["queries_sent"] == 4
    assert payload["queries_not_attempted"] == 0
    assert len(search.sent) == 4


# ----------------------------------------------------------------------
# 限流：两个会话看同一本来源账
# ----------------------------------------------------------------------
def _book_fetcher(
    settings: Settings, routes: dict[str, Any], book: AccessGovernor
) -> HttpFetcher:
    """**组合根那一本账** + 一块替掉的传输。

    替掉的只有传输（``client=`` 与假 DNS）—— 判据不该真的去碰网络。账**不是**这个
    替身自己的：它取自 ``default_acquisition(settings)``，也就是 ``wire_research``
    交给研究侧的那一本（``interfaces/composition.py::_GOVERNOR``）。给它自建一本的
    话，这一节的两条判据会全绿，而线上恰好是坏的 —— 那正是 §5 说的「20 个账号
    各自无界重试」的形状。
    """
    return make_fetcher(settings, routes, governor=book)


@pytest.fixture
def source_book(settings: Settings, requests_seen: list[str]) -> Iterator[AccessGovernor]:
    """进程那一本来源账，**用完恢复原状**。

    它是模块级的可变全局（``_GOVERNOR``），所以一条把某个来源熔断掉的判据会把那个
    状态留给自己之后的所有用例。这里在开始前清空、结束后写回原来的内容：写回而不是
    「清空」是因为别的东西（别的文件、别的用例）可能已经在上面记过一笔，而抹掉它
    会让那些用例读到一本它们没写过的账。

    依赖 ``requests_seen`` 只是为了那一格夹具的 ``reset()`` 与这一条同序 ——
    两个都是「这一次用例开始时是干净的」。
    """
    book = default_acquisition(settings, allow_browser=False).http._governor  # noqa: SLF001
    assert default_http(settings)._governor is book, (  # noqa: SLF001
        "组合根给直连抓取器的那一本不是进程那一本 —— 下面这两条判据的主语就没了"
    )
    saved = dict(book._origins)  # noqa: SLF001
    book._origins.clear()  # noqa: SLF001
    try:
        yield book
    finally:
        book._origins.clear()  # noqa: SLF001
        book._origins.update(saved)  # noqa: SLF001


def test_two_sessions_share_one_source_book(
    settings: Settings,
    tmp_path: Path,
    ledger: SqliteBudgetLedger,
    evidence: SqliteEvidenceStore,
    requests_seen: list[str],
    source_book: AccessGovernor,
) -> None:
    """第一个会话把那个来源熔断掉了，第二个会话**看得见**。

    读数有两格，缺一不可：

    * **线上那条计数**（``requests_seen``）—— 第二个会话的那一跳**根本没有离开
      进程**。只看回执的话，「发出去了但结果被丢弃」与「没发」在报告上同形。
    * **回执上的理由**（``governed:circuit_opened``）—— 它是第一条的成因，而不是
      另一条独立的观察。两者分开会得到「少发了一次，不知道为什么」。

    阈值取自 ``source_book.policy`` 而不是在这里写一个 3：政策的数字改了，这条判据
    跟着走；写死的话，它测的会是「恰好三次」这个巧合。
    """
    threshold = source_book.policy.circuit_setback_threshold
    assert threshold >= 2, "一次就熔断时，「20 个账号」的形状在这一条里看不出来"
    routes = {PAGE_URL: page(CHALLENGE_BODY)}
    (one_ring, one_token), (two_ring, two_token) = _two_rings(tmp_path)

    first = _session(
        settings=settings,
        ring=one_ring,
        token=one_token,
        ledger=ledger,
        evidence=evidence,
        http=_book_fetcher(settings, routes, source_book),
        calls=tuple(
            ("fetch_document", {"url": PAGE_URL, "account_id": f"account-{index:03d}"})
            for index in range(threshold)
        ),
    )
    assert len(requests_seen) == threshold, (
        "第一个会话自己就没走到熔断那一步 —— 下面那条判据没有可断言的成因"
    )
    assert [call.is_error for call in first.calls] == [False] * threshold, (
        "一次挑战页的抓取是一份**结果**，不是一次工具崩溃：它带着 block_reason 回来"
    )

    second = _session(
        settings=settings,
        ring=two_ring,
        token=two_token,
        ledger=ledger,
        evidence=evidence,
        http=_book_fetcher(settings, routes, source_book),
        calls=(("fetch_document", {"url": PAGE_URL, "account_id": "account-999"}),),
    )
    receipt = second.call("fetch_document").as_json()

    assert len(requests_seen) == threshold, (
        f"第二个会话又发出了 {len(requests_seen) - threshold} 次请求 —— "
        "它没有看见第一个会话把那个来源熔断掉了"
    )
    assert receipt["final"]["reason"] == f"governed:{ActionTaken.CIRCUIT_OPENED.value}", (
        "被拒的那一跳必须说得出它是**被来源账**拒的；"
        f"回执是：{receipt['final']}"
    )
    assert receipt["final"]["block_reason"] == "unknown", (
        "那一跳没有观测，所以没有任何一条细分理由成立 —— UNKNOWN 是那诚实的回答"
    )


def test_with_one_book_per_session_the_same_source_costs_one_more_request(
    settings: Settings,
    tmp_path: Path,
    ledger: SqliteBudgetLedger,
    evidence: SqliteEvidenceStore,
    requests_seen: list[str],
) -> None:
    """**对照**：每个会话各拿一本新账时，第二个会话照发不误。

    它不是产品行为，它是上面那条判据的分母：没有它，那个「第二个会话一次都没发」
    可能只是「那个会话的抓取器什么都没做」的巧合。
    """
    threshold = AccessGovernor().policy.circuit_setback_threshold
    routes = {PAGE_URL: page(CHALLENGE_BODY)}
    (one_ring, one_token), (two_ring, two_token) = _two_rings(tmp_path)

    for ring, token, accounts in (
        (one_ring, one_token, [f"account-{index:03d}" for index in range(threshold)]),
        (two_ring, two_token, ["account-999"]),
    ):
        # 每一段会话自己建一本 —— 这正是「共享」被拿掉之后的样子。
        _session(
            settings=settings,
            ring=ring,
            token=token,
            ledger=ledger,
            evidence=evidence,
            http=_book_fetcher(settings, routes, AccessGovernor()),
            calls=tuple(
                ("fetch_document", {"url": PAGE_URL, "account_id": account})
                for account in accounts
            ),
        )

    assert len(requests_seen) == threshold + 1, (
        "每个会话各一本账时，熔断在会话边界上被重置 —— 账上永远只有一次挫折"
    )


def test_two_sessions_share_one_retry_after(
    settings: Settings,
    tmp_path: Path,
    ledger: SqliteBudgetLedger,
    evidence: SqliteEvidenceStore,
    requests_seen: list[str],
    source_book: AccessGovernor,
) -> None:
    """同一个来源的**等待期**也跨会话：第二个会话撞上第一个会话挣来的那个 120 秒。

    这条与上面那条是同一本账上的两种状态：熔断（``open``）与等待期（``not_before``）。
    两条都写出来，是因为它们能被分别弄坏 —— 一个把 ``not_before`` 存在抓取器实例上、
    把 ``open`` 存在账上的实现，会让「二十个账号撞同源只有有界探针」这道门半开，而
    报告上两半各自都正常。

    对照组是上面那条的邻居（``test_with_one_book_per_session_...``）：每个会话各一本
    账时，同一个来源会被问第二次。这里不重复它。
    """
    routes = {
        PAGE_URL: page(
            "Too many requests. Please slow down.",
            status=429,
            headers={"retry-after": "120"},
        )
    }
    (one_ring, one_token), (two_ring, two_token) = _two_rings(tmp_path)

    first = _session(
        settings=settings,
        ring=one_ring,
        token=one_token,
        ledger=ledger,
        evidence=evidence,
        http=_book_fetcher(settings, routes, source_book),
        calls=(("fetch_document", {"url": PAGE_URL, "account_id": "account-001"}),),
    )
    first_final = first.call("fetch_document").as_json()["final"]

    assert first_final["status_code"] == 429, (
        "有观测就带着观测走：把状态码换成一句 governed:* 会让报告里那条 URL 变成一次"
        "**没有观测**的失败，而排查要看的第一样东西正是「那一跳看到了什么」"
    )
    assert first_final["block_reason"] == BlockReason.HTTP_429.value
    assert len(requests_seen) == 1

    second = _session(
        settings=settings,
        ring=two_ring,
        token=two_token,
        ledger=ledger,
        evidence=evidence,
        http=_book_fetcher(settings, routes, source_book),
        calls=(("fetch_document", {"url": PAGE_URL, "account_id": "account-002"}),),
    )
    second_final = second.call("fetch_document").as_json()["final"]

    assert len(requests_seen) == 1, (
        "第二个会话又发了请求 —— 等待期没有跨会话，被共享的只是它自己那一本"
    )
    assert second_final["reason"] == f"governed:{ActionTaken.DEFERRED_BACKOFF.value}"
    assert second_final["status_code"] is None, (
        "第二个会话**没有任何观测**（一个字节都没发）—— 它的根因是从账上读的，"
        "不是从自己那一跳读的。把这一格写成 429 会让「没问」与「问了被拒」同形"
    )
