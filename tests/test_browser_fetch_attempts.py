"""``Crawl4AIBrowserFetcher`` 每一个出口都留下**恰好一行**尝试账（§5）。

## 这一份补的是哪一格

``tests/test_http_fetch_attempts.py`` 判的是直连那一层。浏览器那一层此前**只产回执、
不产诊断**，于是它既写不出尝试账（``_attempt_from`` 会对 ``diagnosis is None`` 抛
``StoreError``），也没有来源账可记 —— 而它的 ``FetchResult`` 与直连那一层长得一模一样。
两份回执在报告上不可分，差别只是其中一份的背后什么都没有。

所以这里的判据与那一份**逐条同形**：一次 ``fetch()`` 之后账上恰好一行、每一个出口
各记成什么样、失败不许落成「一切正常」，外加两格这一层特有的东西：

* **一次渲染只有一行账**。一次浏览器渲染里的每一次请求（主导航、重定向、iframe/worker、
  子资源）都过出口判据，但那些**不是**这一次抓取的结果 —— 它们的形状是
  ``FetchResult.egress_refusals``。写成一页一行之外的任何形状，都会让「这次试了几次」
  在一份渲染里凭空变大。
* **可用性检查在 ``permit.admit()`` 之前**。``admit`` 在放行那一刻就记一次尝试，而
  「这台机器没装浏览器」根本发不出去 —— 让来源账为一台没装浏览器的机器记一笔，
  会把部署问题读成站点问题。

## 为什么替身停在**库**这一层

``tests/test_browser_egress_live.py`` 真开一次浏览器，判的是「网络动作真的从出口走」。
那件事测不了策略：它慢、它要装浏览器、它只能在两个本机上立起来的靶子之间做。
所以这里 monkeypatch 掉 ``crawl4ai`` 的三个名字（``AsyncWebCrawler`` /
``BrowserConfig`` / ``CrawlerRunConfig``），而**抓取器本身、它的入口判据、它的诊断、
它的记账全是真的**。最承重的那几条断言因此可以写成 ``lib.calls == []`` 这样的计数：
它证明那次抓取**一次都没到浏览器**，而不是「到了但结果被丢弃了」—— 后者对 §7 毫无意义。

这一格同时是这套判据**唯一的**替身边界，写出来免得下一次有人把它读成
「真实浏览器路径已被验证」—— 那是 ``test_browser_egress_live.py`` 判的事。

## 替身照着**实测**的字段名写

``_FakeResult`` 的字段名与本机实测一致（探针与输出在
``artifacts/work-items/marketops/TARGET-CONVERGENCE-B15H/``）：``response_headers``
存在且键已小写，``headers`` 那一格**不存在**，而 ``dir(result)`` 只列 ``_results``
（那个类覆盖了 ``dir()``）。下面 ``_FakeResult.__dir__`` 把这个谎**照抄**下来，
于是「字段名从 ``dir()`` 读」这种实现在这里当场红 —— 它在真库上也会红，只是那时
不会有人看见。
"""

from __future__ import annotations

from asyncio import CancelledError
from collections.abc import Callable
from dataclasses import dataclass

import pytest

from marketops.adapters import crawl4ai_fetcher as c4a
from marketops.adapters.classify import assess_http_response
from marketops.adapters.crawl4ai_fetcher import (
    Crawl4AIBrowserFetcher,
    _response_headers_of,
    crawl4ai_available,
    crawl4ai_unavailable_reason,
)
from marketops.adapters.sqlite.fetch_attempt import SqliteFetchAttemptStore
from marketops.config import Settings
from marketops.domain.access import ActionTaken, SuspectedCause
from marketops.domain.access_governance import AccessGovernor, AccessPolicy
from marketops.domain.enums import BlockReason, FetchOutcome, FetchTier
from marketops.domain.errors import ParserUnavailableError, StoreError
from marketops.domain.fetch_attempt import MethodKind, SettlementState
from marketops.ports.acquisition import FetchRequest, FetchResult
from marketops.ports.dns import DnsFailureKind, HostResolver
from marketops.ports.fetch_attempt import FetchAttemptStore

from .fixtures.http_transport import (
    CHALLENGE_BODY,
    PAGE,
    PLAIN_BODY,
    FailingResolver,
    FakeResolver,
    mirrored,
)

SCOPE = "account-001"
#: 一个真的私网字面量。``check_target`` 在**开浏览器之前**就该拒它。
LOOPBACK_URL = "http://127.0.0.1/admin"
#: 一个渲染之后才落到的私网地址 —— 落点判据那一格。
LOOPBACK_LANDING = "http://127.0.0.1:8080/admin"

#: 一个「正文太少 + 有 SPA 挂载点」的空壳页。渲染**之后**仍是空壳 → ``RENDER_FAILURE``。
SHELL_BODY = (
    '<html><head><title>loading</title></head>'
    '<body><div id="root"></div></body></html>'
)


@pytest.fixture
def ledger(settings: Settings) -> SqliteFetchAttemptStore:
    """**落盘**的那一份，与 ``test_http_fetch_attempts.py`` 同名同形。"""
    return SqliteFetchAttemptStore(settings.database_path)


def ledger_factory(store: FetchAttemptStore) -> Callable[[], FetchAttemptStore]:
    return lambda: store


# ----------------------------------------------------------------------
# 替身：库（crawl4ai）、出口、结果对象
# ----------------------------------------------------------------------
class _FakeResult:
    """crawl4ai 结果对象的最小替身。字段名与实测一致（见模块 docstring）。"""

    def __init__(
        self,
        *,
        url: str = PAGE,
        status_code: int | None = 200,
        html: str = PLAIN_BODY,
        headers: dict[str, str] | None = None,
    ) -> None:
        self.url = url
        self.status_code = status_code
        self.html = html
        self.response_headers = dict(headers or {})

    def __dir__(self) -> list[str]:
        # 实测：这个类的 ``__dir__`` 只列 ``_results`` 一项。照抄它是有用的 ——
        # 一个把字段名从 ``dir()`` 读出来的实现（而不是 ``getattr``）在这里就会
        # 得到空字符串/空字典，而它在真库上犯的是同一个错。
        return ["_results"]


class _FakeEgress:
    """一个不存在的受管出口：只回答被读的那两格（``url`` / ``refusals``）。

    真起一个 ``EgressProxy`` 会绑一个本机端口 —— 这一份判的不是出口，所以注入一个
    只有形状的替身。它在 ``_browser_config`` 里被写进 ``proxy_config["server"]``，
    而下面对它的断言是「浏览器的全部网络动作都指着这一个地址」。
    """

    def __init__(self, url: str = "http://127.0.0.1:9/") -> None:
        self.url = url
        self.refusals: tuple[str, ...] = ()


class _Crawler:
    """``AsyncWebCrawler`` 的替身：**只替库**，不替抓取器。"""

    def __init__(self, library: _Library, config: object) -> None:
        self._library = library
        library.configs.append(config)

    async def __aenter__(self) -> _Crawler:
        return self

    async def __aexit__(self, *exc: object) -> bool:
        return False

    async def arun(self, *, url: str, config: object = None) -> object:
        self._library.calls.append(url)
        # 交给 ``arun`` 的 run config 也要记：``_run_config()`` 里写了什么，
        # 与 ``_render`` 真的**用了**什么，是两件事 —— 只判前者的话，一个在
        # ``_render`` 里另构造一份的实现会全绿。
        self._library.run_configs.append(config)
        if self._library.raises is not None:
            raise self._library.raises
        return self._library.result


class _Library:
    """装进 ``crawl4ai_fetcher`` 模块的那三个名字，外加两条记录。"""

    def __init__(
        self,
        *,
        result: _FakeResult | None = None,
        raises: BaseException | None = None,
        available: bool = True,
    ) -> None:
        self.result = result if result is not None else _FakeResult()
        self.raises = raises
        self.available = available
        #: 真的**到过**浏览器的那几次渲染。这是本文件最承重的一条读数。
        self.calls: list[str] = []
        #: 每次渲染交出去的 ``BrowserConfig``（经 ``AsyncWebCrawler(config=...)``）。
        self.configs: list[object] = []
        #: 每次渲染交出去的 ``CrawlerRunConfig``（经 ``arun(config=...)``）。
        self.run_configs: list[object] = []

    def install(self, monkeypatch: pytest.MonkeyPatch) -> None:
        if not self.available:
            monkeypatch.setattr(c4a, "AsyncWebCrawler", None)
            return
        monkeypatch.setattr(c4a, "AsyncWebCrawler", lambda *, config=None: _Crawler(self, config))
        monkeypatch.setattr(c4a, "BrowserConfig", lambda **kwargs: {"browser": kwargs})
        monkeypatch.setattr(c4a, "CrawlerRunConfig", lambda **kwargs: {"run": kwargs})


def _fetcher(
    settings: Settings,
    *,
    governor: AccessGovernor | None = None,
    attempts: Callable[[], FetchAttemptStore] | None = None,
    resolver: HostResolver | None = None,
    egress: object | None = None,
) -> Crawl4AIBrowserFetcher:
    """一个真的浏览器抓取器：假的库 + 假的 DNS + 形状正确的出口。"""
    return Crawl4AIBrowserFetcher(
        settings,
        resolver=FakeResolver() if resolver is None else resolver,
        egress=_FakeEgress() if egress is None else egress,
        governor=governor,
        attempts=attempts,
    )


# ----------------------------------------------------------------------
# 每一个出口：一次 fetch 恰好一行账
# ----------------------------------------------------------------------
@dataclass
class Case:
    """一个出口：它的局面、真的到过浏览器几次、以及该记成什么样。"""

    result: _FakeResult | None = None
    raises: BaseException | None = None
    url: str = PAGE
    resolver: HostResolver | None = None
    policy: AccessPolicy | None = None
    #: **真的到过浏览器**几次。被入口判据拒、解析失败时是 0。
    calls: int = 1
    outcome: FetchOutcome = FetchOutcome.OK
    cause: SuspectedCause = SuspectedCause.NONE
    action: ActionTaken = ActionTaken.PROCEEDED
    block_reason: BlockReason | None = None


CASES = [
    pytest.param(Case(result=_FakeResult()), id="200-正常页面"),
    pytest.param(
        Case(
            result=_FakeResult(status_code=403),
            outcome=FetchOutcome.BLOCKED,
            cause=SuspectedCause.ACCESS_DENIED_UNKNOWN,
            # 这一格与 ``diagnose`` 单独给出的处置**不同**，而那是有意的分工：
            # ``access.diagnose`` 判根因与**默认**处置（403 默认 ``REVIEW_REQUESTED``，
            # 「保存诊断并走合法替代来源」），``FetchPermit.record`` 按**当前 policy**
            # 决定实际处置 —— 而 403 属于「来源级挫折但不可延后」这一类，于是被改写成
            # ``STOPPED_METHOD``。所以这一条同时钉住两件事：根因是「不明」而不是
            # ``IP_POLICY``（§5：403 不得被读成 IP 封禁），以及那个改写真的发生了。
            action=ActionTaken.STOPPED_METHOD,
            block_reason=BlockReason.HTTP_403,
        ),
        id="403",
    ),
    pytest.param(
        Case(
            result=_FakeResult(status_code=429, headers={"retry-after": "120"}),
            outcome=FetchOutcome.BLOCKED,
            cause=SuspectedCause.RATE_LIMITED,
            action=ActionTaken.DEFERRED_BACKOFF,
            block_reason=BlockReason.HTTP_429,
        ),
        id="429-带Retry-After",
    ),
    pytest.param(
        Case(
            result=_FakeResult(status_code=404, html="<html>gone</html>"),
            # **不是 ``ERROR``**：``assess_http_response`` 的第一条就是 404/410 →
            # ``NOT_FOUND``。它是「这个来源没了」，不是「这一页出问题了」。
            outcome=FetchOutcome.NOT_FOUND,
            cause=SuspectedCause.SOURCE_GONE,
            action=ActionTaken.STOPPED_METHOD,
        ),
        id="404",
    ),
    pytest.param(
        Case(
            result=_FakeResult(status_code=500),
            outcome=FetchOutcome.ERROR,
            cause=SuspectedCause.TRANSIENT_OR_UNKNOWN,
            action=ActionTaken.DEFERRED_BACKOFF,
        ),
        id="500",
    ),
    pytest.param(
        Case(
            result=_FakeResult(html=CHALLENGE_BODY),
            outcome=FetchOutcome.BLOCKED,
            cause=SuspectedCause.WAF_CHALLENGE,
            action=ActionTaken.STOPPED_METHOD,
            block_reason=BlockReason.WAF_CHALLENGE,
        ),
        id="挑战页-正文指纹",
    ),
    pytest.param(
        Case(
            result=_FakeResult(headers={"cf-mitigated": "challenge"}),
            outcome=FetchOutcome.BLOCKED,
            cause=SuspectedCause.WAF_CHALLENGE,
            action=ActionTaken.STOPPED_METHOD,
            block_reason=BlockReason.WAF_CHALLENGE,
        ),
        id="挑战页-响应头",
    ),
    pytest.param(
        Case(
            result=_FakeResult(html=SHELL_BODY),
            # 渲染**之后**仍是空壳 = 这一次渲染没拿到内容，终态。
            outcome=FetchOutcome.RENDER_FAILURE,
            cause=SuspectedCause.CONTENT_UNUSABLE,
            action=ActionTaken.WITHHOLD_BODY,
        ),
        id="渲染后仍是空壳",
    ),
    pytest.param(
        Case(raises=RuntimeError("测试造的渲染异常"),
             outcome=FetchOutcome.RENDER_FAILURE,
             cause=SuspectedCause.TRANSIENT_OR_UNKNOWN,
             action=ActionTaken.DEFERRED_BACKOFF),
        id="渲染抛异常",
    ),
    pytest.param(
        Case(raises=TimeoutError("测试造的渲染超时"),
             outcome=FetchOutcome.RENDER_FAILURE,
             cause=SuspectedCause.NETWORK_UNKNOWN,
             action=ActionTaken.DEFERRED_BACKOFF),
        id="渲染超时",
    ),
    pytest.param(
        Case(result=_FakeResult(url=LOOPBACK_LANDING),
             outcome=FetchOutcome.BLOCKED,
             cause=SuspectedCause.UNSAFE_TARGET,
             action=ActionTaken.REFUSED_UNSAFE_TARGET,
             block_reason=BlockReason.UNSAFE_TARGET),
        id="渲染之后落在私网地址",
    ),
    pytest.param(
        Case(resolver=FailingResolver(DnsFailureKind.NONEXISTENT), calls=0,
             outcome=FetchOutcome.ERROR, cause=SuspectedCause.SOURCE_GONE,
             action=ActionTaken.STOPPED_METHOD),
        id="DNS-名字不存在",
    ),
    pytest.param(
        Case(resolver=FailingResolver(DnsFailureKind.TIMEOUT), calls=0,
             outcome=FetchOutcome.ERROR, cause=SuspectedCause.NETWORK_UNKNOWN,
             action=ActionTaken.DEFERRED_BACKOFF),
        id="DNS-超时",
    ),
    pytest.param(
        Case(url=LOOPBACK_URL, calls=0, outcome=FetchOutcome.BLOCKED,
             cause=SuspectedCause.UNSAFE_TARGET,
             action=ActionTaken.REFUSED_UNSAFE_TARGET,
             block_reason=BlockReason.UNSAFE_TARGET),
        id="URL 是环回地址",
    ),
    pytest.param(
        Case(resolver=FakeResolver("10.0.0.7"), calls=0, outcome=FetchOutcome.BLOCKED,
             cause=SuspectedCause.UNSAFE_TARGET,
             action=ActionTaken.REFUSED_UNSAFE_TARGET,
             block_reason=BlockReason.UNSAFE_TARGET),
        id="域名解析到内网",
    ),
]


def test_the_case_table_covers_every_exit() -> None:
    """表的规模本身是一条判据 —— 它挡的是「删掉一行**没有替代**」。

    它判不了「新加了一个出口而表里没有对应行」：表不知道 ``_run`` 有几个出口。
    能立刻让新出口现形的是 ``test_no_exit_forgets_to_leave_a_diagnosis`` ——
    只要那个出口进了这张表。写下来是为了让下一个人知道这一条守到哪为止。
    """
    ids = [param.id for param in CASES]
    assert len(ids) == len(set(ids)), f"用例名字重复：{ids}"
    assert len(CASES) >= 15, (
        f"出口表缩到 {len(CASES)} 条了。删掉一行等于删掉一个出口的判据，"
        "而剩下的行照样全绿。"
    )


@pytest.mark.parametrize("case", CASES)
def test_every_exit_leaves_exactly_one_attempt(
    settings: Settings,
    ledger: SqliteFetchAttemptStore,
    monkeypatch: pytest.MonkeyPatch,
    case: Case,
) -> None:
    """**一次抓取一行账**，而「到过浏览器几次」逐格对得上。

    「恰好一行」而不是「至少一行」：一次浏览器渲染里的子资源请求各写一行，会让这个
    来源上的尝试数在一份渲染里凭空变大几倍 —— 而报告上那是「这个 URL 被试了很多次」，
    排查的人于是去找一次并不存在的重试。

    ``outcome`` / ``cause`` / ``action`` / ``block_reason`` 一起判：它们各自单独都能
    被一个错误的实现凑出来。一次被地址判据拒掉的抓取在 ``outcome`` 上是 ``BLOCKED``
    （与一次 403 同形），而区分它们的正是后面几格。
    """
    library = _Library(result=case.result, raises=case.raises)
    library.install(monkeypatch)
    fetcher = _fetcher(
        settings,
        governor=AccessGovernor() if case.policy is None else AccessGovernor(policy=case.policy),
        resolver=case.resolver,
        attempts=ledger_factory(ledger),
    )
    result = fetcher.fetch(FetchRequest(url=case.url, scope=SCOPE))

    assert len(library.calls) == case.calls, (
        f"真的到过浏览器 {len(library.calls)} 次，期望 {case.calls}。被入口判据拒掉与"
        "解析失败的那些**一次都不该到** —— 计数能证明「没有到浏览器」，而「到了但结果"
        "被丢弃」证明不了这件事（§7）"
    )

    rows = ledger.list_attempts()
    assert len(rows) == 1, f"一次抓取留下了 {len(rows)} 行账"
    (row,) = rows

    assert result.outcome is case.outcome, f"{result.reason} / {result.error}"
    assert row.suspected_cause is case.cause
    assert row.action_taken is case.action
    assert result.block_reason is case.block_reason
    assert row.observed_status == result.status_code, (
        "账上的状态码必须就是回执上那一格 —— 两处各算一遍的实现会在某一天分叉，"
        "而分叉之后没有人知道该信哪一个"
    )


@pytest.mark.parametrize("case", CASES)
def test_no_exit_forgets_to_leave_a_diagnosis(
    settings: Settings,
    ledger: SqliteFetchAttemptStore,
    monkeypatch: pytest.MonkeyPatch,
    case: Case,
) -> None:
    """**每一个出口都带诊断**，而失败的那个不许落成「一切正常」。

    ``diagnose`` 对一次真正的 200 答 ``cause=none, action=proceeded`` —— 那是一份
    **有效**的诊断，不是搪塞。所以这一条两边都判：正常那一格必须是它，其余一格都
    不许是它。少了前半句，一个把每一份诊断都写成「一切正常」的实现会全绿。

    这一条为什么是硬的：收账时 ``require_diagnosis`` 对 ``None`` 抛 ``StoreError``，
    而**来源账**那一头是 ``permit.record`` —— 少了它，这次失败不会被记进那本账，
    于是 §5 那条「同一个挑战页让 20 个账号各自无界重试」的结局可以无声地长出来。
    """
    library = _Library(result=case.result, raises=case.raises)
    library.install(monkeypatch)
    fetcher = _fetcher(settings, resolver=case.resolver, attempts=ledger_factory(ledger))
    result = fetcher.fetch(FetchRequest(url=case.url, scope=SCOPE))

    assert result.diagnosis is not None, (
        f"{case.url} 的这一份回执没有诊断 —— 收账会抛 ``StoreError``，"
        "而一份不带诊断的回执在报告上与「一切正常」同形"
    )

    if case.cause is SuspectedCause.NONE:
        assert case.outcome is FetchOutcome.OK, "只有真的拿到了内容的那个出口才是「没有异常」"
        assert result.diagnosis.action is ActionTaken.PROCEEDED
        return

    assert not (
        result.diagnosis.cause is SuspectedCause.NONE
        and result.diagnosis.action is ActionTaken.PROCEEDED
    ), f"{case.url} 的回执写着「没有任何异常，正常继续」，而它在表里不是一个正常局面"


# ----------------------------------------------------------------------
# 那一行账里写的是这一层**真的用过**的方法
# ----------------------------------------------------------------------
def test_the_row_names_the_browser_as_the_method_and_the_landing_as_the_final_url(
    settings: Settings, ledger: SqliteFetchAttemptStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``method`` 是 ``BROWSER``，``final_url`` 是**落在**的那个地址。

    ``method`` 有两件事挂在它上面（见 ``attempt_from`` 的 docstring）：它是
    ``attempt_id`` 的种子之一（写死 ``HTTP`` 会让这一跳算出一个 HTTP 尝试的 id，
    两种不同的尝试从此可能撞成一条），以及它是 §5 那条替代策略回执要的那一格
    —— 「这一次真的走了浏览器」必须能从账上读出来。

    ``settlement`` 是 ``NOT_APPLICABLE``，而那是**判定不是缺省**：这次抓取不消费任何
    计费额度。它同样必须写出来 —— 让缺省值当答案，等于放弃这一格的信息量。
    """
    landing = f"{PAGE}?rendered=1"
    library = _Library(result=_FakeResult(url=landing))
    library.install(monkeypatch)
    fetcher = _fetcher(settings, attempts=ledger_factory(ledger))
    fetcher.fetch(FetchRequest(url=PAGE, scope=SCOPE))

    (row,) = ledger.list_attempts()
    assert row.method is MethodKind.BROWSER
    assert row.budget_settlement is SettlementState.NOT_APPLICABLE
    assert row.budget_reservation_id == "", "没有预留就没有预留号 —— 不许编一个"
    assert row.url == PAGE, "``url`` 是**问过的**那个"
    assert row.final_url == landing, "``final_url`` 是**落在的**那个"
    assert row.scope == SCOPE
    assert row.workspace == settings.profile
    assert row.attempt_id.startswith("att-")


# ----------------------------------------------------------------------
# §4 闭环：挑战页的正文不进业务证据 —— 两半 + 对照组
# ----------------------------------------------------------------------
def test_a_challenge_page_is_never_mirrored(
    settings: Settings, ledger: SqliteFetchAttemptStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """§4 逐字：挑战正文「不当网页证据」。落点是 ``body_path is None``。

    两半各判一次，另加对照组：

    * 正文指纹（``just a moment`` / ``checking your browser``）—— 分类器那一半；
    * ``cf-mitigated: challenge`` 响应头 —— **分类器看不见那一半**（它只看状态码与正文），
      所以一个 200、正文干净的页面在它眼里是 OK，改判必须发生在诊断那一侧；
    * 对照组：**同一批字节**去掉那个头，必须照常落盘。少了它，一个「什么都不落盘」的
      实现会让上面两半照样通过。
    """
    header_only = _FakeResult(headers={"cf-mitigated": "challenge"})
    # 前提本身要判一次：分类器对这个页面答的是 OK。否则这一条测的就不是接缝，
    # 而是分类器早就判过的那个局面。
    assert assess_http_response(
        status_code=200, content_type="text/html", text=PLAIN_BODY
    )[0] is FetchOutcome.OK

    library = _Library(result=_FakeResult(html=CHALLENGE_BODY))
    library.install(monkeypatch)
    by_text = _fetcher(settings, attempts=ledger_factory(ledger)).fetch(
        FetchRequest(url=PAGE, scope=SCOPE)
    )
    assert by_text.outcome is FetchOutcome.BLOCKED
    assert by_text.block_reason is BlockReason.WAF_CHALLENGE
    assert by_text.body_bytes > 0, "字节**收到了** —— 这条测的是「扣下」，不是「没收到」"
    assert by_text.body_path is None
    assert by_text.body_sha256 == "", "没有落盘就不该有一个指向字节的摘要"
    assert by_text.diagnosis is not None and by_text.diagnosis.challenge_detected is True
    assert mirrored(settings) == []

    library.result = header_only
    by_header = _fetcher(settings, attempts=ledger_factory(ledger)).fetch(
        FetchRequest(url=PAGE, scope=SCOPE)
    )
    assert by_header.outcome is FetchOutcome.BLOCKED, (
        "分类器答的是 OK；结论必须被诊断改写 —— 否则挑战正文会成为业务证据"
    )
    assert by_header.block_reason is BlockReason.WAF_CHALLENGE
    assert by_header.body_path is None
    assert by_header.diagnosis is not None and by_header.diagnosis.challenge_detected is True
    assert mirrored(settings) == [], "同一批字节带上那个头之后，一个都不许落盘"

    library.result = _FakeResult()
    kept = _fetcher(settings, attempts=ledger_factory(ledger)).fetch(
        FetchRequest(url=PAGE, scope=SCOPE)
    )
    assert kept.outcome is FetchOutcome.OK
    assert kept.body_path is not None and kept.body_path.is_file()
    assert mirrored(settings) == [kept.body_path], "对照组：同一批字节本来是该落盘的"


def test_the_retry_after_header_survives_into_the_receipt_and_the_ledger(
    settings: Settings, ledger: SqliteFetchAttemptStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``Retry-After`` 必须活到账上 —— 三个月后复核那次退避要看的正是它。

    这一格证过一次真事：浏览器那一层的 ``response_headers`` 到底读不读得到。实测
    （探针见模块 docstring）读得到，而且键已经是小写的 —— 所以这一条把「读得到」
    钉在**回执**与**账**两处，而不只是在某个内部变量上。
    """
    library = _Library(result=_FakeResult(status_code=429, headers={"retry-after": "120"}))
    library.install(monkeypatch)
    result = _fetcher(settings, attempts=ledger_factory(ledger)).fetch(
        FetchRequest(url=PAGE, scope=SCOPE)
    )

    assert result.response_headers.get("retry-after") == "120"
    assert result.diagnosis is not None and result.diagnosis.retry_after_s == 120.0

    (row,) = ledger.list_attempts()
    assert row.response_headers.get("retry-after") == "120"
    assert row.retry_after_s == 120.0
    assert row.suspected_cause is SuspectedCause.RATE_LIMITED


# ----------------------------------------------------------------------
# 来源账：接在请求路径上，而且**在可用性检查之后**
# ----------------------------------------------------------------------
def test_a_refused_or_unresolvable_target_never_touches_the_source_ledger(
    settings: Settings,
    ledger: SqliteFetchAttemptStore,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """入口判据与解析判据的拒绝**不消耗 permit**，所以来源账上连一行都不该有。

    ``AccessGovernor.begin`` 会为每个来源 ``setdefault`` 一条记录，而 ``admit`` 只在
    **放行**的那一刻记一次尝试。（拒绝不记，也不需要 ``record`` —— 成对的是
    「放行 + ``record``」。）所以 ``snapshot() == ()`` 是一个能观察的读数：它说的是
    「这一层一次都没打算发出去」，而不是「发了但被拒了」。
    """
    library = _Library()
    library.install(monkeypatch)

    for url, resolver in ((LOOPBACK_URL, None), (PAGE, FakeResolver("10.0.0.7"))):
        governor = AccessGovernor()
        fetcher = _fetcher(
            settings,
            governor=governor,
            resolver=resolver,
            attempts=ledger_factory(ledger),
        )
        result = fetcher.fetch(FetchRequest(url=url, scope=SCOPE))

        assert result.outcome is FetchOutcome.BLOCKED
        assert governor.snapshot() == (), (
            f"{url} 被入口判据拒掉了，来源账上却多了一条记录 —— 一个**没打算发出去**的"
            "请求不该在那个来源的账上留下任何东西"
        )

    assert library.calls == [], "被拒的目标一次都不该到浏览器"
    assert len(ledger.list_attempts()) == 2, "但两次抓取各留一行账（§5 记的是尝试，不是成功）"


def test_the_availability_check_comes_before_the_permit(
    settings: Settings, ledger: SqliteFetchAttemptStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """**没装浏览器**时来源账一个字都不该变。

    顺序是承重的：``admit`` 一放行就记一次尝试，而 ``admit``/``record`` 必须成对 ——
    一个在这里抛的实现如果先取了 permit，那本账会把**部署问题**记成**站点问题**，
    于是同一个来源的熔断会被一台没装浏览器的机器打开。
    """
    library = _Library(available=False)
    library.install(monkeypatch)
    governor = AccessGovernor()
    fetcher = _fetcher(settings, governor=governor, attempts=ledger_factory(ledger))

    with pytest.raises(ParserUnavailableError, match="crawl4ai 不可用"):
        fetcher.fetch(FetchRequest(url=PAGE, scope=SCOPE))

    assert governor.snapshot() == (), "部署状态不该写进来源账"
    assert library.calls == []
    assert ledger.list_attempts() == [], "抛出来的那次抓取没有回执，所以也没有账"


def test_a_governed_fetch_never_reaches_the_browser(
    settings: Settings, ledger: SqliteFetchAttemptStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """熔断打开之后第二次抓取：**一次浏览器都不启动**，而账照记。

    用 ``circuit_setback_threshold=1`` 把这条判据压到一次抓取上。承重的读数是
    ``library.calls`` 的**计数**：它证明第二次抓取根本没有到浏览器，而不是
    「到了但结果被丢弃了」—— 后者对 §5「同一个源挑战页上的 20 个账号不得生成 20 个
    无界重试套件」毫无意义。

    第二次也要**留一行账**：一次没有离开进程的尝试同样是一次尝试，而它上面的
    ``governed:*`` 是「为什么这一次没发」的答案。
    """
    library = _Library(result=_FakeResult(status_code=429, headers={"retry-after": "120"}))
    library.install(monkeypatch)
    governor = AccessGovernor(policy=AccessPolicy(circuit_setback_threshold=1))
    fetcher = _fetcher(settings, governor=governor, attempts=ledger_factory(ledger))

    first = fetcher.fetch(FetchRequest(url=PAGE, scope="account-001"))
    assert first.outcome is FetchOutcome.BLOCKED
    assert first.diagnosis is not None
    assert first.diagnosis.action is ActionTaken.CIRCUIT_OPENED, (
        "一次 429 就把熔断打开了 —— 而这一份回执说的正是那件事"
    )

    second = fetcher.fetch(FetchRequest(url=PAGE, scope="account-002"))

    assert len(library.calls) == 1, "第二次抓取一次都没到浏览器"
    assert second.outcome is FetchOutcome.BLOCKED
    assert second.reason == f"governed:{ActionTaken.CIRCUIT_OPENED.value}"
    assert second.block_reason is BlockReason.UNKNOWN, (
        "这一跳没有观测，所以没有任何一条细分理由成立 —— UNKNOWN 是那句诚实的回答"
    )
    assert second.status_code is None
    assert second.body_path is None
    assert second.final_url == PAGE, "``final_url`` 是**本来要去**的地址"
    assert second.redirect_trace == (), (
        "这一层的一次调用只有一跳，而那一跳没有发生过 —— 写 ``(PAGE,)`` 会把"
        "「本来要去的地址」记成「去过的地方」"
    )
    assert second.diagnosis is not None
    assert second.diagnosis.cause is SuspectedCause.RATE_LIMITED, (
        "根因取自**来源的账**（上一次是限流），而不是这一次的（这一次没有观测）"
    )

    rows = ledger.list_attempts()
    assert len(rows) == 2, "两次抓取两行账 —— 「没发出去」也是一次尝试"
    assert {row.scope for row in rows} == {"account-001", "account-002"}


# ----------------------------------------------------------------------
# 收口点：没有诊断的回执不许被记成「一切正常」
# ----------------------------------------------------------------------
class _DiagnosisLess(Crawl4AIBrowserFetcher):
    """一个 ``_fetch_async`` 交回**没有诊断**的回执的抓取器。

    替的是 ``_fetch_async`` 而不是 ``_run``：``_run`` 的收口点上就有那句
    ``require_diagnosis``，而这一条要判的正是**它**会不会放行。
    """

    async def _fetch_async(self, request: FetchRequest, started: float) -> FetchResult:  # noqa: ARG002
        return FetchResult(request=request, outcome=FetchOutcome.OK, tier=FetchTier.BROWSER)


def test_a_diagnosis_free_result_is_refused_before_it_reaches_the_ledger(
    settings: Settings, ledger: SqliteFetchAttemptStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """收口点**当场拒绝**一份没有诊断的回执，而调用方拿不到那份回执。

    两件事一起判：抛的是 ``StoreError``（不是一次静默回退），而且**账上没有行**。
    一个「记一行 ``cause=none, action=proceeded`` 再返回」的实现在这里全绿 ——
    而它在报告上说这次抓取一切正常。

    这一格正是这一层此前六个月里的处境：``diagnosis`` 恒为 ``None``，于是它既写不出
    账，也不进来源账。守卫是**刻意**留在那里的 —— ``None`` 是一条假话。
    """
    library = _Library()
    library.install(monkeypatch)
    fetcher = _DiagnosisLess(
        settings,
        resolver=FakeResolver(),
        egress=_FakeEgress(),
        attempts=ledger_factory(ledger),
    )

    with pytest.raises(StoreError, match="browser 这一层"):
        fetcher.fetch(FetchRequest(url=PAGE, scope=SCOPE))

    assert ledger.list_attempts() == []


def test_an_in_flight_cancellation_leaves_no_row(
    settings: Settings, ledger: SqliteFetchAttemptStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """一次**真的炸在途中**的抓取不留账。**这是一条已知边界，不是一条好消息。**

    账记在 ``fetch`` 的返回路径上，所以一次在途崩溃不留痕。要覆盖它得再引入一次
    「开始记账 + 收尾」的两段式写入，而那会让这张表上出现「没有结论的行」这一新状态；
    本片**刻意没有**做，理由与 ``test_http_fetch_attempts.py`` 里那一条逐字相同。

    ## 用的是 ``CancelledError`` 而不是某个 ``Exception``

    ``_render`` 的兜底写的是 ``except Exception``（那条兜底是对的：一次渲染失败是一次
    **结果**，不是一次崩溃）。而取消**不是**渲染失败 —— 是没有人再等这一次渲染了。
    所以它必须穿过去，否则一次被取消的抓取会落成一行 ``render_failure``，
    报告上那就是「这个站点渲染不出来」，而事实是「我们自己不做了」。这个区别恰好是
    ``except Exception`` 与 ``except BaseException`` 的分界，所以这一条同时也钉住
    那条兜底**没有**宽到把取消也收编。

    ``ParserUnavailableError`` **不在**这一条里：它抛在回执产生之前，但那一格有它自己的
    判据（``test_the_availability_check_comes_before_the_permit``）。
    """
    library = _Library(raises=CancelledError("测试造的途中取消"))
    library.install(monkeypatch)
    fetcher = _fetcher(settings, attempts=ledger_factory(ledger))

    with pytest.raises(CancelledError):
        fetcher.fetch(FetchRequest(url=PAGE, scope=SCOPE))

    assert ledger.list_attempts() == []


# ----------------------------------------------------------------------
# 隐藏重试：这一层不能比账上那一行多试
# ----------------------------------------------------------------------
def test_the_run_config_asks_for_no_hidden_retries(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    """抓取器**显式**要求 ``max_retries=0``，不吃库的缺省值。

    §5：「SDK 隐藏重试必须可观察，且不得双重放大」。这一条判的是**我们写了那个决定**
    —— 断言抓取器交给 ``CrawlerRunConfig`` 的那一格是 0。它**判不了**「库真的读了
    它」，那是下面那一条的事；两条分开，因为替身能判前者、只有真库能判后者。

    为什么非有不可：那个字段驱动的是**防反爬重试**（实测 0.9.3
    ``async_webcrawler.py:405`` 的 ``_max_attempts = 1 + getattr(config, "max_retries", 0)``，
    循环里的日志 tag 是 ``ANTIBOT``）。它今天为 0 只因那是库的缺省值，而一次库升级把
    它改成 3 之后，我们那个来源上真的发出的请求数会翻几倍 —— 而**账上仍然是每次
    ``fetch()`` 一行**，于是报告上的尝试数一个字都不变。同源共享限流正是按那个数算的。
    """
    library = _Library()
    library.install(monkeypatch)
    _fetcher(settings, attempts=None).fetch(FetchRequest(url=PAGE, scope=SCOPE))

    assert library.run_configs, "一次渲染都没发生 —— 这一条判不到任何东西"
    (config,) = library.run_configs
    assert config["run"]["max_retries"] == 0
    assert config["run"]["cache_mode"] == "BYPASS", (
        "顺手核对：缓存模式不是这一条的主题，但一个把 ``CrawlerRunConfig`` 换成别的"
        "构造方式的实现会一起把它丢掉，而缓存会让两次抓取读到同一份旧正文"
    )


@pytest.mark.skipif(
    not crawl4ai_available(),
    reason=f"crawl4ai 不可用（可选运行依赖）：{crawl4ai_unavailable_reason()}",
)
def test_the_library_actually_reads_that_field(settings: Settings) -> None:
    """**正控**：``max_retries`` 这一格在真库上读得出来，且抓取器写的就是 0。

    上一条只证明「我们传了 0」。如果这个字段在库里根本不存在（或者被改名、被
    ``**kwargs`` 吞掉），上一条会照样全绿 —— 于是「没有隐藏重试」是一句空话，
    而我们**以为**自己钉住了它。所以这里先证明非零值读得回来（``3`` 读成 ``3``），
    再证明抓取器构造出来的那一格是 0。

    这与直连那一层的正控同一形状（``tests/test_http_access_governance.py`` 先证明
    ``httpx.HTTPTransport(retries=3)`` 读得到 3，再证明抓取器的是 0）。
    """
    from crawl4ai import CrawlerRunConfig  # noqa: PLC0415 - 可选依赖，只在跑的机器上导入

    assert CrawlerRunConfig(max_retries=3).max_retries == 3, (
        "非零值读不回来 —— 那说明这一格在库里不存在或被吞了，"
        "而「我们钉住了它」这句话就没有依据"
    )

    egress = _FakeEgress()
    fetcher = _fetcher(settings, egress=egress)
    config = fetcher._run_config()  # noqa: SLF001 - 这一条判的就是这一格
    assert config.max_retries == 0
    assert config.page_timeout == int(settings.browser_timeout_seconds * 1000), (
        "顺手核对：超时那一格也要真的到位 —— 一个只写了 max_retries 的实现"
        "会让这一格回到缺省（或干脆构造不出来）"
    )
    assert config.cache_mode == "BYPASS"


# ----------------------------------------------------------------------
# 响应头那一格的两个形状
# ----------------------------------------------------------------------
def test_response_headers_are_read_by_attribute_and_washed() -> None:
    """``_response_headers_of`` 按**属性**读（不是 ``dir()``），并过白名单。

    两件事一起判，因为它们各自都能被一个错实现凑出来：

    * ``_FakeResult.__dir__`` 只列 ``_results``（与真实库一致）。一个从 ``dir()`` 读
      字段名的实现拿到的是空字典 —— 于是 ``cf-mitigated`` 读不到，而 §4 那一次改判
      会静默地不发生；
    * 白名单必须**真的**在起作用：``set-cookie`` 与 ``authorization`` 一个都不许
      带出去（设计 §3：「原始敏感 headers/cookies/key/IP 不出报告」）。
    """
    result = _FakeResult(
        headers={
            "cf-mitigated": "challenge",
            "retry-after": "120",
            "set-cookie": "session=secret",
            "authorization": "Bearer secret",
        }
    )
    assert "response_headers" not in dir(result), "前提：这个替身的 ``dir()`` 与真库一样不说实话"

    washed = _response_headers_of(result)
    assert washed["cf-mitigated"] == "challenge"
    assert washed["retry-after"] == "120"
    assert "set-cookie" not in washed
    assert "authorization" not in washed

    assert _response_headers_of(object()) == {}, (
        "认不出来的形状交回空字典，而不是抛 —— 库的字段名变动不该让一次已经量好的"
        "抓取变成一次崩溃"
    )
    assert _response_headers_of(_FakeResult(headers={})) == {}


def test_the_governed_disposition_replaces_the_default_one_without_losing_the_cause(
    settings: Settings, ledger: SqliteFetchAttemptStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """回执上的处置是**治理层改写过**的那个，而根因与人话原样留着。

    ``diagnose`` 单独对一次 403 给的是 ``REVIEW_REQUESTED``（「保存诊断并走合法替代
    来源」），而来源账那一层按 policy 把它改写成 ``STOPPED_METHOD``。两句话都得能读到：

    * 处置是**实际采取了什么**，所以它必须是改写之后的那个 —— 一个只把
      ``diagnose`` 的默认值抄进回执的实现，会让报告说「已转人工复核」而实际上什么
      都没发生；
    * 根因与人话是**判断**，与 policy 无关，所以它们必须活着 —— 把整份诊断换成一句
      ``STOPPED_METHOD`` 会让「为什么」在报告上消失，而 403 那一格的「为什么」正是
      §5 唯一要传下去的东西。

    两件事一起判，因为任何一边丢掉都不会让别处变红。
    """
    library = _Library(result=_FakeResult(status_code=403))
    library.install(monkeypatch)
    result = _fetcher(settings, attempts=ledger_factory(ledger)).fetch(
        FetchRequest(url=PAGE, scope=SCOPE)
    )

    assert result.diagnosis is not None
    assert result.diagnosis.cause is SuspectedCause.ACCESS_DENIED_UNKNOWN
    assert result.diagnosis.action is ActionTaken.STOPPED_METHOD, "回执上必须是改写之后的处置"
    assert "不得据此判定出口 IP 被封" in result.diagnosis.detail, (
        "403 那一格的判断句不见了 —— 报告上只剩「停了」，而读它的人会自己补一个"
        "根因（最可能补成「IP 被封」），那正是 §5 要挡的那句话"
    )

    (row,) = ledger.list_attempts()
    assert row.action_taken is result.diagnosis.action, (
        "账与回执的处置必须同一句话 —— 两处各算一遍会在某一天分叉"
    )
    assert row.suspected_cause is result.diagnosis.cause


def test_the_injected_egress_address_reaches_the_browser_config(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    """替身那一半：地址被放进 ``BrowserConfig.proxy_config`` 那一格。

    §7 那一句（「若所锁浏览器接口不能在发送前拦截全部必要请求，组合根需采用可验证的
    受管网络层/拦截器」）在实现层面就是这一格：浏览器的全部网络动作按它走。

    这一条判「抓取器把它放在哪一格、值是不是那一串」；「库认不认那一格」是下面那一条。
    两条分开的理由与 ``max_retries`` 那两条逐字相同 —— 而这一条**在任何机器上都跑**，
    所以一条把出口地址丢掉的改动不会因为没装 crawl4ai 而在这里静默通过。
    """
    library = _Library()
    library.install(monkeypatch)
    egress = _FakeEgress(url="http://127.0.0.1:54321/")

    _fetcher(settings, egress=egress).fetch(FetchRequest(url=PAGE, scope=SCOPE))

    assert library.configs, "一次渲染都没发生 —— 这一条判不到任何东西"
    (config,) = library.configs
    assert config["browser"]["proxy_config"] == {"server": egress.url}


@pytest.mark.skipif(
    not crawl4ai_available(),
    reason=f"crawl4ai 不可用（可选运行依赖）：{crawl4ai_unavailable_reason()}",
)
def test_the_library_actually_uses_that_proxy(settings: Settings) -> None:
    """**真库那一半**：抓取器算出来的那个地址，是库自己会用的那个。

    真的库里 ``proxy_config`` 会被**收编成一个 ``ProxyConfig`` 对象**（不是留着那个
    字典），而浏览器启动参数里的 ``--proxy-server`` 取自这里
    （``browser_manager.py:122``）。所以只有真的构造一次才判得到「这个字段名库认，
    且值原样活到了它会用的那一格」—— 字段名写错在真库上会撞出
    ``ProxyConfig.server`` 的 ``AttributeError``，而那正是这一条要挡的形状。

    **这一格为什么在 ``BrowserConfig`` 上而不是 run config 上**：run config 上那一格
    会被 ``arun`` 里的防反爬循环覆写（``async_webcrawler.py:449``），把出口地址放在那里
    等于交给一个我们不控制的循环去改。

    没装 crawl4ai 的机器上它 skip，与 ``test_browser_egress_live.py`` 同一口径：
    这是可选运行依赖。上面那一条替身判据在没装的机器上照常跑。
    """
    egress = _FakeEgress(url="http://127.0.0.1:54321/")
    fetcher = _fetcher(settings, egress=egress)

    config = fetcher._browser_config(egress)  # noqa: SLF001 - 这一条判的就是这一格

    assert config.proxy_config.server == egress.url
