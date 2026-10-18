"""统一抓取入口：``auto`` / ``http`` / ``browser`` 三种 mode 的判据。

## 这个文件在测什么，以及为什么它不是 ``test_pipeline`` 的复制

§10 要求抓取只有**一个**入口：``fetch_document(mode=auto/http/browser)``。
在统一之前，「HTTP 成功就不开浏览器」这条规则在 pipeline 里、在 CLI 里、
在每一个将来的调用方里各写一遍 —— 而它每被抄一遍就少一次被考验的机会。

所以这里测的是那条规则**作为一条规则**的行为：给定 mode 与一次 HTTP 结果，
谁被调用、谁没有被调用、回执里留下了什么。全部用 fake fetcher，零网络、
零磁盘：本文件不构造 ``Settings``，因为被测量的是编排，不是落盘。

## "没被调用"必须配一个"被调用"的正控

「浏览器没有被启动」这条断言有一个廉价的反面：把浏览器整个忘了传进去，
它同样成立。所以每一条「不该升级」旁边都有一条同形的「该升级时确实升级了」，
用**同一个** RecordingBrowser。一条只证明「没发生」的判据，在功能整体失效时
是绿的。
"""

from __future__ import annotations

import pytest

from marketops.application.acquisition import DocumentAcquisition
from marketops.domain.enums import BlockReason, FetchOutcome, FetchTier
from marketops.ports.acquisition import (
    BrowserFetcher,
    DocumentFetch,
    FetchMode,
    FetchRequest,
    FetchResult,
    HttpFetcher,
    mode_allows,
)

URL = "https://example.co.uk/contact"
BLOCK = BlockReason.WAF_CHALLENGE


class RecordingHttpFetcher(HttpFetcher):
    """记下被调用过的 URL，并回一个预置的 outcome。"""

    name = "recording-http"

    def __init__(self, outcome: FetchOutcome) -> None:
        self._outcome = outcome
        self.calls: list[str] = []

    def fetch(self, request: FetchRequest) -> FetchResult:
        self.calls.append(request.url)
        return _result(request, self._outcome, FetchTier.HTTP)


class RecordingBrowserFetcher(BrowserFetcher):
    """浏览器侧的同一个形状。

    ``available`` 可关：**已接线但运行时不可用**与**压根没接线**是两件事，
    上面那条「不可用时不得升级」的判据要的正是前者的形态。
    """

    name = "recording-browser"

    def __init__(self, outcome: FetchOutcome, *, available: bool = True) -> None:
        self._outcome = outcome
        self._available = available
        self.calls: list[str] = []

    @property
    def available(self) -> bool:
        return self._available

    def fetch(self, request: FetchRequest) -> FetchResult:
        self.calls.append(request.url)
        return _result(request, self._outcome, FetchTier.BROWSER)


def _result(request: FetchRequest, outcome: FetchOutcome, tier: FetchTier) -> FetchResult:
    return FetchResult(
        request=request,
        outcome=outcome,
        tier=tier,
        final_url=request.url,
        status_code=200 if outcome is FetchOutcome.OK else None,
        content_type="text/html",
        elapsed_ms=7,
        block_reason=BLOCK if outcome is FetchOutcome.BLOCKED else None,
    )


def _service(
    http: HttpFetcher, browser: BrowserFetcher | None = None
) -> DocumentAcquisition:
    return DocumentAcquisition(http=http, browser=browser)


def _fetch(
    http: HttpFetcher, browser: BrowserFetcher | None, mode: FetchMode
) -> DocumentFetch:
    return _service(http, browser).fetch_document(FetchRequest(url=URL), mode=mode)


# ----------------------------------------------------------------------
# auto：由 escalation.decide 决定，不由调用方决定
# ----------------------------------------------------------------------
def test_auto_does_not_start_a_browser_when_http_succeeds() -> None:
    """HTTP 成功就不开浏览器 —— 需求原文那一条。"""
    http = RecordingHttpFetcher(FetchOutcome.OK)
    browser = RecordingBrowserFetcher(FetchOutcome.OK)

    outcome = _fetch(http, browser, FetchMode.AUTO)

    assert browser.calls == []
    assert [attempt.tier for attempt in outcome.attempts] == [FetchTier.HTTP]
    assert outcome.decision == "http_ok"
    assert outcome.final.tier is FetchTier.HTTP


def test_auto_does_escalate_on_a_renderable_outcome() -> None:
    """正控：同一个 RecordingBrowser 在 JS_REQUIRED 下**确实**被调用。

    没有这一条，上面那条「没被调用」在「浏览器根本没接进来」时同样成立。
    """
    http = RecordingHttpFetcher(FetchOutcome.JS_REQUIRED)
    browser = RecordingBrowserFetcher(FetchOutcome.OK)

    outcome = _fetch(http, browser, FetchMode.AUTO)

    assert browser.calls == [URL]
    assert [attempt.tier for attempt in outcome.attempts] == [
        FetchTier.HTTP,
        FetchTier.BROWSER,
    ]
    assert outcome.final.tier is FetchTier.BROWSER
    assert outcome.decision == "browser_renderable:js_required"


def test_the_http_attempt_is_kept_so_both_tiers_can_be_tallied() -> None:
    """升级之后 HTTP 那一次**仍在回执里**。

    它是 ``http_js_required`` 这类计数器唯一的来源。只回最终结果，会让一次
    「先 JS_REQUIRED 再浏览器成功」在报告上读成「HTTP 一次都没失败过」——
    而那个数字正是「这个站该不该进浏览器」这个问题的答案。
    """
    http = RecordingHttpFetcher(FetchOutcome.EMPTY_SHELL)
    browser = RecordingBrowserFetcher(FetchOutcome.OK)

    outcome = _fetch(http, browser, FetchMode.AUTO)

    assert outcome.attempts[0].outcome is FetchOutcome.EMPTY_SHELL
    assert outcome.attempts[0].tier is FetchTier.HTTP
    assert outcome.attempts[1].outcome is FetchOutcome.OK


def test_the_escalation_carries_what_triggered_it() -> None:
    """升级之后，``escalated_from`` 要说出**是哪一次 HTTP 结果**触发的。

    这个字段住在 ``FetchResult`` 上，而填它的地方只有这一层：浏览器 fetcher 看不见
    触发它的那次 HTTP 结果。声明了却没人填的字段比没有这个字段更坏 —— 它看起来像
    一条记录，读起来永远是一个「这次不是升级来的」的假结论。

    断言用的是**非 OK** 的那个 outcome：``escalated_from`` 允许升级的三种原因分别
    是 js_required / empty_shell / render_failure，取一个足够区分「填对了」与
    「顺手填了 OK」的值。
    """
    http = RecordingHttpFetcher(FetchOutcome.EMPTY_SHELL)
    browser = RecordingBrowserFetcher(FetchOutcome.OK)

    escalated = _fetch(http, browser, FetchMode.AUTO)
    assert escalated.final.escalated_from is FetchOutcome.EMPTY_SHELL

    # 正控的另一半：没有升级时它必须是 None。一个恒被填成某种值的字段同样说不出
    # 「这一次是升级来的」。
    plain = _fetch(http, browser, FetchMode.HTTP)
    assert plain.final.escalated_from is None


def test_auto_does_not_escalate_on_a_transport_error() -> None:
    """``TIMEOUT`` 是传输问题，不是渲染问题 —— 它不构成启动浏览器的理由。

    这条与上一条成对：同一个浏览器，只在 outcome 上不同。
    """
    http = RecordingHttpFetcher(FetchOutcome.TIMEOUT)
    browser = RecordingBrowserFetcher(FetchOutcome.OK)

    outcome = _fetch(http, browser, FetchMode.AUTO)

    assert browser.calls == []
    assert outcome.final.tier is FetchTier.HTTP
    assert outcome.decision.startswith("no_escalation_rule")


def test_auto_does_not_escalate_into_an_unavailable_browser() -> None:
    """已接线但运行时不可用时，auto 停在 HTTP 结果上，并说明原因。"""
    http = RecordingHttpFetcher(FetchOutcome.JS_REQUIRED)
    browser = RecordingBrowserFetcher(FetchOutcome.OK, available=False)

    outcome = _fetch(http, browser, FetchMode.AUTO)

    assert browser.calls == []
    assert outcome.decision == "browser_unavailable:js_required"


def test_auto_says_which_kind_of_unavailable_it_ran_into() -> None:
    """``auto`` 是 pipeline 走的那条路 —— 它同样要说清是**哪一种**「用不了」。

    只接线判断是反的：今天唯一会被真正大量走过的 mode 是 ``auto``，而
    「没接线」（去改构建）与「接线了但没装依赖」（去装 Playwright）如果在这里
    同形，那么两种诊断中**常用的那一种**先把人引错方向。``decision`` 里带上
    那个词，与 ``mode=browser`` 下 ``final.reason`` 用的是同一套词。
    """
    http = RecordingHttpFetcher(FetchOutcome.JS_REQUIRED)

    missing = _fetch(http, None, FetchMode.AUTO)
    unavailable = _fetch(
        http, RecordingBrowserFetcher(FetchOutcome.OK, available=False), FetchMode.AUTO
    )

    assert missing.decision == "browser_not_wired:js_required"
    assert unavailable.decision == "browser_unavailable:js_required"
    assert missing.final.outcome is FetchOutcome.JS_REQUIRED, "正控：都停在 HTTP 结果上"
    assert unavailable.final.outcome is FetchOutcome.JS_REQUIRED


def test_auto_escalates_at_most_once() -> None:
    """浏览器渲染失败就是失败，不无限套娃。"""
    http = RecordingHttpFetcher(FetchOutcome.RENDER_FAILURE)
    browser = RecordingBrowserFetcher(FetchOutcome.RENDER_FAILURE)

    outcome = _fetch(http, browser, FetchMode.AUTO)

    assert browser.calls == [URL], "浏览器被调用了不止一次"
    assert outcome.attempts[-1].outcome is FetchOutcome.RENDER_FAILURE


# ----------------------------------------------------------------------
# http / browser：调用方指定层级
# ----------------------------------------------------------------------
def test_mode_http_never_starts_a_browser() -> None:
    """调用方说只用 HTTP，就只用 HTTP —— 即使那个结果是可升级的。

    它与 ``auto`` 的区别必须**在回执里看得见**：一个没有理由的「没升级」
    与一个因为 mode 而没升级，在报告上是同一件事。
    """
    http = RecordingHttpFetcher(FetchOutcome.JS_REQUIRED)
    browser = RecordingBrowserFetcher(FetchOutcome.OK)

    outcome = _fetch(http, browser, FetchMode.HTTP)

    assert browser.calls == []
    assert outcome.final.tier is FetchTier.HTTP
    assert outcome.decision == "mode_disallows:browser"


def test_mode_browser_does_not_call_http_at_all() -> None:
    """指定 browser 时 HTTP 一次都不发 —— 不是「先直连试试」。"""
    http = RecordingHttpFetcher(FetchOutcome.OK)
    browser = RecordingBrowserFetcher(FetchOutcome.OK)

    outcome = _fetch(http, browser, FetchMode.BROWSER)

    assert http.calls == [], "指定了 browser，却仍然发了一次直连请求"
    assert browser.calls == [URL]
    assert [attempt.tier for attempt in outcome.attempts] == [FetchTier.BROWSER]
    assert outcome.final.tier is FetchTier.BROWSER


def test_mode_browser_without_a_wired_browser_is_an_explicit_result() -> None:
    """没有浏览器时**返回一个结果**，不抛异常，也不退回 HTTP。

    退回 HTTP 是最坏的那种处置：调用方要的是渲染后的页面，拿到的是一份
    需要 JS 的空壳，而两者在报告上只差一个 tier 字段。
    """
    http = RecordingHttpFetcher(FetchOutcome.OK)

    outcome = _fetch(http, None, FetchMode.BROWSER)

    assert http.calls == []
    assert outcome.final.outcome is FetchOutcome.ERROR
    assert outcome.final.tier is FetchTier.BROWSER
    assert outcome.final.reason == "browser_not_wired"
    assert outcome.decision == "browser_not_wired"


def test_an_unavailable_browser_is_not_the_same_failure_as_a_missing_one() -> None:
    """「没接线」与「接线了但依赖没装」是两件事，两句话。

    合并成一句的代价是排查方向被引错：前者要去改构建，后者要去装依赖。
    """
    http = RecordingHttpFetcher(FetchOutcome.OK)
    missing = _fetch(http, None, FetchMode.BROWSER)
    unavailable = _fetch(
        http, RecordingBrowserFetcher(FetchOutcome.OK, available=False), FetchMode.BROWSER
    )

    assert missing.final.reason == "browser_not_wired"
    assert unavailable.final.reason == "browser_unavailable"
    assert unavailable.final.error != missing.final.error


def test_an_unavailable_browser_never_reaches_the_network() -> None:
    """不可用时**一次调用都不发** —— 判据在 fetcher 之前拦下。"""
    browser = RecordingBrowserFetcher(FetchOutcome.OK, available=False)

    _fetch(RecordingHttpFetcher(FetchOutcome.OK), browser, FetchMode.BROWSER)

    assert browser.calls == []


# ----------------------------------------------------------------------
# 回执里必须留下的东西
# ----------------------------------------------------------------------
def test_a_blocked_target_is_reported_as_the_fetcher_reported_it() -> None:
    """被拒绝的目标原样回传，本层不重新判定、也不改写。

    安全判据住在 ``security/network.py`` 且由**每个 fetcher** 在发请求之前调用。
    在这一层再判一次，就是「一份判据两个实现」—— 而漂移的那一份会表现为
    「编排层说可以、fetcher 说不可以」（或反过来）。
    """
    http = RecordingHttpFetcher(FetchOutcome.BLOCKED)
    browser = RecordingBrowserFetcher(FetchOutcome.OK)

    outcome = _fetch(http, browser, FetchMode.AUTO)

    assert outcome.final.outcome is FetchOutcome.BLOCKED
    assert outcome.final.block_reason is BLOCK
    assert outcome.final.tier is FetchTier.HTTP
    assert browser.calls == [], "目标是终态 BLOCKED，仍然启动了浏览器"


def test_the_mode_is_recorded_on_the_receipt() -> None:
    """回执带着它是被哪一种 mode 取得的 —— 事后无法从别处还原这件事。"""
    outcome = _fetch(RecordingHttpFetcher(FetchOutcome.OK), None, FetchMode.AUTO)

    assert outcome.mode is FetchMode.AUTO
    assert outcome.request.url == URL


def test_the_service_measures_the_browser_wall_time_itself() -> None:
    """``browser_seconds`` 由本层测，不从 ``elapsed_ms`` 反推。

    两者不是同一个量：``elapsed_ms`` 是 fetcher 在自己的计时器内测到的渲染
    时间，而报告里的浏览器秒数一直是「这一次调用占用的墙钟时间」（含
    ``asyncio.run`` 的起停）。换一个量而不说明，等于把一条历史指标的含义
    悄悄改掉。
    """
    http = RecordingHttpFetcher(FetchOutcome.JS_REQUIRED)
    browser = RecordingBrowserFetcher(FetchOutcome.OK)

    outcome = _fetch(http, browser, FetchMode.AUTO)

    assert outcome.browser_seconds > 0
    assert outcome.attempts[-1].elapsed_ms == 7, "fetcher 自己的读数不该被改写"


def test_no_browser_means_no_browser_seconds() -> None:
    """正控的反面：没升级时那个数字**是 0**，不是「恰好很小」。"""
    outcome = _fetch(RecordingHttpFetcher(FetchOutcome.OK), None, FetchMode.AUTO)

    assert outcome.browser_seconds == 0.0


# ----------------------------------------------------------------------
# mode 本身是一条可穷举的判据
# ----------------------------------------------------------------------
@pytest.mark.parametrize(
    ("mode", "tier", "allowed"),
    [
        (FetchMode.AUTO, FetchTier.HTTP, True),
        (FetchMode.AUTO, FetchTier.BROWSER, True),
        (FetchMode.HTTP, FetchTier.HTTP, True),
        (FetchMode.HTTP, FetchTier.BROWSER, False),
        (FetchMode.BROWSER, FetchTier.HTTP, False),
        (FetchMode.BROWSER, FetchTier.BROWSER, True),
    ],
)
def test_mode_allows_is_the_whole_table(mode: FetchMode, tier: FetchTier, allowed: bool) -> None:
    """2 × 2 + auto 两行，就是这张表的全部 —— 没有第三种答案。"""
    assert mode_allows(mode, tier) is allowed


def test_mode_values_are_the_three_the_spec_names() -> None:
    """§10 的字符串形状是契约（``MCP_SERVICE.md`` 的工具参数按它写）。"""
    assert [mode.value for mode in FetchMode] == ["auto", "http", "browser"]
