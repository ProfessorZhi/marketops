"""检索失败时，报告要说清**量到了什么**与**据此认为发生了什么** —— 两句话都不许编。

## 这条判据守的那一句断言

``adapters/serper.py`` 曾经这样报错：

    401/403 通常是 key 失效或额度耗尽。

这一句里压着三件没有出处的东西：两个不同的状态码（401 与 403）合成一句、两种
**从未被观测**的根因（key 失效 / 额度耗尽）、以及一个「通常」——它不是统计，是
一句从注释里长出来的供应商计费语义。设计文档点名的正是这一句
（``docs/ACQUISITION_AND_PROVIDER_DESIGN.md`` §2：「错误提示把401/403与key/额度
混说」，紧接着的那句是「**不从代码注释推测供应商所有计费语义**」）。

处置不是「把句子改得客气一点」，是让那句断言**表达不出来**：
``SuspectedCause`` 这个闭集里没有「额度耗尽」，也没有「key 失效」；403 的默认
结论就是 ``ACCESS_DENIED_UNKNOWN``（「根因不明」）。所以本文件同时钉两件事：

* **结构上** —— 四个状态码各自落到不同的根因上（第一组），且词表里根本没有一个
  能表达计费结论的成员（第五组）；
* **字面上** —— 渲染出来的那句话里没有额度主张。这一条不是多余的：那份字符串
  是**人和报告真正读到的东西**（``adapters/mcp_server/research.py`` 把
  ``str(receipt.error)`` 拼进 ``errors``），而闭集只管得住结构，管不住句子。

## 观测与判断是两个字段，都挂在异常上

``SearchError`` 现在带一个 :class:`~marketops.domain.access.Observation`（量到了
什么）与一个 :class:`~marketops.domain.access.AccessDiagnosis`（据此认为发生了
什么）。这两个对象是 ``domain/access.py`` **已有的** —— 检索面与抓取面因此用同一
套词汇、同一张表（设计 §4），而不是第二套。

旧写法 ``SearchError("一句话")`` 继续可用，它的 ``suspected_cause`` 是
``UNKNOWN`` 而不是 ``NONE``：``UNKNOWN`` 的 docstring 逐字写着它的含义是「有异常，
但没有任何证据把它归到下面任何一类」，而一句手写的消息正是这个处境；``NONE`` 要说
的是「这一次没有任何异常」，拿它填一个失败是错的（第六组）。
"""

from __future__ import annotations

import re
from dataclasses import replace

import httpx
import pytest

from marketops.adapters.serper import SERPER_ENDPOINT, SerperSearchProvider
from marketops.config import Settings
from marketops.domain.access import ActionTaken, SuspectedCause
from marketops.domain.errors import MarketOpsError, SearchError
from marketops.ports.search import SearchQuery

MARKET = {"country": "GB", "language": "en"}

#: 这几个字是「计费结论」的代理词。它们出现在**渲染出来的那句话**里就是本片
#: 要消灭的那种断言；出现在下面这条判据的注释里是允许的。
_BILLING_WORDS = ("quota", "credit", "billing", "balance", "payment")


def _query(**overrides) -> SearchQuery:
    fields = {"text": "cricket club devon", "scope": "probe-001", **MARKET}
    fields.update(overrides)
    return SearchQuery(**fields)


def _provider(settings: Settings, handler) -> SerperSearchProvider:
    keyed = replace(settings, serper_api_key="test-key-not-a-real-one")
    return SerperSearchProvider(
        keyed, client=httpx.Client(transport=httpx.MockTransport(handler))
    )


def _responds(
    settings: Settings,
    *,
    status: int,
    headers: dict[str, str] | None = None,
    body: bytes = b"",
) -> SerperSearchProvider:
    def responder(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, headers=headers or {}, content=body)

    return _provider(settings, responder)


def _caught(provider: SerperSearchProvider, query: SearchQuery | None = None) -> SearchError:
    """跑一次注定失败的检索，交回那个异常。"""
    with pytest.raises(SearchError) as caught:
        provider.search(query or _query())
    return caught.value


# --- 一、四个状态码分开说 ---------------------------------------------------------


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        (401, SuspectedCause.AUTH_REQUIRED),
        # 403 的默认结论是「根因不明」。这一格是本片最要紧的一条：它**允许**
        # 我们说自己不知道，而旧那句话不允许。
        (403, SuspectedCause.ACCESS_DENIED_UNKNOWN),
        (429, SuspectedCause.RATE_LIMITED),
        (500, SuspectedCause.TRANSIENT_OR_UNKNOWN),
        (503, SuspectedCause.TRANSIENT_OR_UNKNOWN),
        (404, SuspectedCause.SOURCE_GONE),
    ],
)
def test_each_status_gets_its_own_cause(settings, status, expected) -> None:
    """状态码不同 ⇒ 根因不同。旧写法把 401 与 403 合成同一句，于是两条处置不同的
    失败在报告上同形。"""
    error = _caught(_responds(settings, status=status, body=b"{}"))

    assert error.suspected_cause is expected
    assert error.observation is not None
    assert error.observation.observed_status == status, "观测没把状态码带出来"


def test_401_and_403_do_not_share_a_cause(settings) -> None:
    """正控的反面：这两条**必须**不同。合成一条时这条转红，而它看起来只是「省了一格」。"""
    unauthorized = _caught(_responds(settings, status=401, body=b"{}"))
    forbidden = _caught(_responds(settings, status=403, body=b"{}"))

    assert unauthorized.suspected_cause is not forbidden.suspected_cause


def test_a_403_never_becomes_an_ip_policy_verdict(settings) -> None:
    """403 就是 403 —— 它**不是**「出口 IP 被封」。

    ``IP_POLICY`` 只能由 ``with_cause_evidence`` 带凭据产生（设计 §4：Ray ID 不是
    封 IP 证明），而检索面一路走的是 ``diagnose``，它的返回集合里没有那个成员。
    """
    error = _caught(_responds(settings, status=403, headers={"cf-ray": "abc123-LHR"}, body=b"{}"))

    assert error.suspected_cause is SuspectedCause.ACCESS_DENIED_UNKNOWN
    assert error.suspected_cause is not SuspectedCause.IP_POLICY


def test_the_diagnosis_is_built_by_the_shared_table(settings) -> None:
    """根因与处置来自 ``domain/access.py`` 那张表，不是本适配器另写一份判断。

    429 的处置在表里是「按等待期退避」，5xx 是「有限重试」—— 两者都带
    ``DEFERRED_BACKOFF``；403 是「人工/运维复核」，带 ``REVIEW_REQUESTED``。
    适配器要是在这里自己写一套，它就会在表更新之后停在旧结论上。
    """
    assert (
        _caught(_responds(settings, status=429, body=b"{}")).diagnosis.action
        is ActionTaken.DEFERRED_BACKOFF
    )
    assert (
        _caught(_responds(settings, status=503, body=b"{}")).diagnosis.action
        is ActionTaken.DEFERRED_BACKOFF
    )
    assert (
        _caught(_responds(settings, status=403, body=b"{}")).diagnosis.action
        is ActionTaken.REVIEW_REQUESTED
    )


# --- 二、额度不是一句话能加的 -----------------------------------------------------


@pytest.mark.parametrize(
    "provider_factory",
    [
        lambda settings: _responds(settings, status=401, body=b"{}"),
        lambda settings: _responds(settings, status=403, body=b"{}"),
        lambda settings: _responds(settings, status=429, body=b"{}"),
        lambda settings: _responds(settings, status=500, body=b"{}"),
        lambda settings: _responds(settings, status=200, body=b"not json at all"),
    ],
)
def test_no_failure_claims_a_billing_cause(settings, provider_factory) -> None:
    """**渲染出来的那句话**里没有计费主张。

    上一条判据管的是结构（词表里没有那个成员），这一条管的是句子 —— 报告里给人
    看的是 ``str(error)``，一句自由文本可以绕开整个闭集。旧写的
    「401/403 通常是 key 失效或额度耗尽」正是这么绕过去的。
    """
    message = str(_caught(provider_factory(settings)))

    assert "额度" not in message
    assert not re.search(r"配额|quota|余额", message, re.IGNORECASE)
    assert "通常是" not in message, "「通常」不是一次观测，是一句猜测的措辞"


def test_the_cause_vocabulary_cannot_express_a_billing_verdict() -> None:
    """词表里没有一个成员能表达「额度耗尽」—— 要说出它必须先加一个成员。

    这条判据的价值在于**它会红**：谁哪天给 ``SuspectedCause`` 加一个
    ``QUOTA_EXHAUSTED``，这里就失败，于是那件事必须是一次写下来的设计决定，
    而不是顺手补一格。
    """
    offenders = [
        cause.name
        for cause in SuspectedCause
        if any(word in cause.value.lower() for word in _BILLING_WORDS)
    ]

    assert not offenders, (
        f"根因闭集里出现了计费结论：{offenders}。"
        "供应商的计费语义不是从状态码推出来的（设计 §2：不从代码注释推测）。"
    )


# --- 三、Retry-After 要真的读出来 -------------------------------------------------


def test_a_retry_after_in_seconds_is_read(settings) -> None:
    """429 带 ``Retry-After: 120`` ⇒ 观测里有 120。"""
    error = _caught(_responds(settings, status=429, headers={"retry-after": "120"}, body=b"{}"))

    assert error.suspected_cause is SuspectedCause.RATE_LIMITED
    assert error.diagnosis.retry_after_s == 120


def test_a_retry_after_that_is_absent_is_none_not_zero(settings) -> None:
    """没给 ``Retry-After`` ⇒ ``None``，**不是** 0。

    0 的语义是「现在就可以重试」，而服务端什么都没说。把「没说」读成「立刻可以」，
    就是那种「重试刷门」的起点 —— 同一个区分在 ``domain/access.py`` 里已经写过。
    """
    error = _caught(_responds(settings, status=429, body=b"{}"))

    assert error.diagnosis.retry_after_s is None
    assert "未给可用" in str(error)


def test_a_retry_after_beyond_the_ceiling_is_none(settings) -> None:
    """``Retry-After: 86400`` ⇒ ``None``：那在语义上等于「今天别来」。

    把它当成一次 24 小时的等待，只会让 worker 挂在那里而 run 的 deadline 早过了。
    """
    error = _caught(_responds(settings, status=429, headers={"retry-after": "86400"}, body=b"{}"))

    assert error.diagnosis.retry_after_s is None


def test_a_retry_after_of_zero_stays_zero(settings) -> None:
    """``Retry-After: 0`` 是**服务端给了一个值**，只是它是 0。与「没给」不同。"""
    error = _caught(_responds(settings, status=429, headers={"retry-after": "0"}, body=b"{}"))

    assert error.diagnosis.retry_after_s == 0


# --- 四、正文要真的进到观测里（否则判据是纸上的）--------------------------------


def test_a_challenge_page_on_a_403_is_recognised(settings) -> None:
    """403 的正文是 Cloudflare 挑战页 ⇒ 根因是 ``WAF_CHALLENGE``，处置也随之变。

    这一条同时证明正文**确实喂进了** ``diagnose``：把正文扔掉、只留状态码的实现
    在这里会答 ``ACCESS_DENIED_UNKNOWN`` —— 而两者的处置不同（挑战的正文不得进入
    业务证据）。
    """
    error = _caught(
        _responds(
            settings,
            status=403,
            body=b"<html><title>Just a moment...</title><body>checking your browser</body></html>",
        )
    )

    assert error.suspected_cause is SuspectedCause.WAF_CHALLENGE
    assert error.diagnosis.challenge_detected is True
    assert error.diagnosis.body_may_be_kept is False


def test_a_2xx_that_is_not_json_is_content_unusable(settings) -> None:
    """200 但正文不是 JSON ⇒ ``CONTENT_UNUSABLE``，**不是**「成功、零结果」。

    这一格是「失败长得像成功」的典型：``_to_hits`` 对一份解析不出来的载荷会答
    「没有 organic 结果」，而报告上那与「这次检索真的没有结果」同形。
    """
    error = _caught(_responds(settings, status=200, body=b"<html>502 Bad Gateway</html>"))

    assert error.suspected_cause is SuspectedCause.CONTENT_UNUSABLE
    assert error.observation.body_usable is False


def test_a_transport_failure_leaves_the_status_unset(settings) -> None:
    """传输层失败 ⇒ ``observed_status`` 是 ``None``，**不是** 200 也不是 0。

    「没问到状态码」与「问到了 200」是两件事；折成一个数之后，一次连不上的调用
    会在报告里读成一次成功的访问。
    """

    def responder(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectTimeout("模拟的连接超时")

    error = _caught(_provider(settings, responder))

    assert error.observation.observed_status is None
    assert error.observation.transport_error_kind == "ConnectTimeout"
    assert error.suspected_cause is SuspectedCause.NETWORK_UNKNOWN


def test_a_redirect_is_not_read_as_a_normal_response(settings) -> None:
    """302 是一跳我们**不跟**的重定向 ⇒ 这一次检索没有结果。

    ``diagnose`` 给 3xx 的那句是**为抓取面写的**（抓取面逐跳自己跟），它的结论是
    ``NONE`` / ``PROCEEDED``（「来源是通的」）。检索面直接复用那一格，就会让一次
    **没有拿到结果的失败**带着「一切正常」的根因抛出来。
    """
    error = _caught(
        _responds(settings, status=302, headers={"location": "https://cdn.example/x"}, body=b"")
    )

    assert error.suspected_cause is SuspectedCause.UNKNOWN
    assert error.suspected_cause is not SuspectedCause.NONE
    assert error.diagnosis.action is ActionTaken.REVIEW_REQUESTED
    assert "cdn.example" in str(error), "重定向的目标要能被人读到，否则没法复核"


def test_a_redirect_without_a_location_is_still_a_failure(settings) -> None:
    """302 且**没有** ``Location``：也一样。这条钉的是「不靠 ``raise_for_status``
    替我们判成败」—— httpx 只在有 ``Location`` 时才为 3xx 抛。"""
    error = _caught(_responds(settings, status=302, body=b""))

    assert error.suspected_cause is SuspectedCause.UNKNOWN


# --- 五、正文有界，且不进那句话 ---------------------------------------------------


def test_the_body_is_bounded(settings) -> None:
    """正文按 ``http_max_bytes`` 截断，不跟着异常一直在内存里待到运行结束。"""
    small = replace(settings, http_max_bytes=1024)
    error = _caught(_responds(small, status=500, body=b"x" * 4096))

    assert len(error.observation.text) == 1024


def test_the_body_does_not_leak_into_the_rendered_message(settings) -> None:
    """正文留在观测里，**不进** ``str(error)``。

    报告里那一行是 ``str(receipt.error)``（``adapters/mcp_server/research.py`` 把
    它拼进 ``errors``），所以一个 8 MiB 的响应正文若混进那句话，会跟着报告落到盘上。
    与设计 §3 的「原始敏感 headers/cookies/key/IP 不出报告」是同一条边界。
    """
    error = _caught(_responds(settings, status=500, body=b"SECRET-MARKER" + b"y" * 512))

    assert "SECRET-MARKER" in error.observation.text, "正文根本没进观测 —— 那第四组就是纸上的"
    assert "SECRET-MARKER" not in str(error)


def test_the_response_headers_are_filtered(settings) -> None:
    """响应头走白名单（``safe_response_headers``）：凭据/出口标识不进报告。"""
    error = _caught(
        _responds(
            settings,
            status=403,
            headers={"cf-ray": "abc123-LHR", "set-cookie": "session=SECRET", "x-api-key": "SECRET"},
            body=b"{}",
        )
    )

    assert error.observation.headers.get("cf-ray") == "abc123-LHR"
    assert "set-cookie" not in error.observation.headers
    assert "x-api-key" not in error.observation.headers


# --- 六、旧写法继续可用 -----------------------------------------------------------


def test_the_old_single_argument_form_still_works() -> None:
    """``SearchError("一句话")`` 还能构造 —— 仓里两个替身与两条判据在用这个形状。

    它的 ``suspected_cause`` 是 ``UNKNOWN``：一句手写的消息就是「有异常、没有证据
    把它归到任何一类」那个处境。填 ``NONE`` 是错的 —— 那个成员说「这一次没有任何
    异常」。
    """
    error = SearchError("一句话")

    assert str(error) == "一句话"
    assert error.observation is None
    assert error.diagnosis is None
    assert error.suspected_cause is SuspectedCause.UNKNOWN
    assert isinstance(error, MarketOpsError), "它必须仍是 MarketOpsError 的子类"


def test_it_is_still_a_market_ops_error(settings) -> None:
    """带回执的那条路按 ``MarketOpsError`` 捕获（``application/billed_search.py``）——
    加了字段之后这个继承关系不能变，否则一条 403 会被读成一次编程错误往上抛。"""
    error = _caught(_responds(settings, status=403, body=b"{}"))

    assert isinstance(error, MarketOpsError)


# --- 七、成功路径不变（正控）------------------------------------------------------


def test_a_successful_search_still_returns_hits(settings) -> None:
    """正控：上面那套诊断没有把正常路径改成失败。"""
    provider = _provider(
        settings,
        lambda request: httpx.Response(
            200,
            json={"organic": [{"link": "https://example.org/a", "title": "A", "snippet": "s"}]},
        ),
    )

    result = provider.search(_query())

    assert [hit.url for hit in result.hits] == ["https://example.org/a"]
    assert result.api_calls == 1


def test_an_empty_result_is_not_a_failure(settings) -> None:
    """零结果**是**一次成功的检索 —— 它不该被读成失败，也不该被读成有结果。"""
    provider = _provider(settings, lambda request: httpx.Response(200, json={"organic": []}))

    result = provider.search(_query())

    assert result.hits == ()
    assert result.raw_response_path is not None


def test_the_error_names_the_query_it_belongs_to(settings) -> None:
    """那句话里要有**这条检索式**（含市场），否则并发多市场时读不出是哪一条失败。"""
    error = _caught(_responds(settings, status=403, body=b"{}"), _query(text="acme rugby"))

    assert "GB/en/acme rugby" in str(error)
    assert SERPER_ENDPOINT not in str(error), "端点常量对人没用，不该占那一行"
