"""来源账**真的接在请求路径上**吗（§5 / B15-d）。

``tests/test_access_governance.py`` 证明治理层的判据本身是活的（纯函数 + 注入时钟）；
``tests/test_access_diagnosis.py`` 证明诊断分得清 403、429 与挑战页。本文件证明第三件事，
而它正是前两件加起来仍然缺的那一件：**两者被串在一条真实的请求路径上**。

一条没有生产者的判据等于没有判据。一个把治理层写得完全正确、却在每次抓取时自建一本账
的实现，会让下面这些用例全绿，而线上表现为：20 个账号各自「只失败了一次」，报告上一切
正常，同一个来源已经被问了 20 遍。

## 为什么这一层必须用真的抓取器测

``tests/fixtures/http_fakes.py`` 的替身替掉的是「抓取」这件事，不是「治理」这件事 ——
用替身测治理，测出来的是替身自己的实现。所以本文件用 ``httpx.MockTransport`` 造出真实的
``httpx.Response``，并记录**每一个真的被发出的请求**。最承重的那几条断言是
``len(requests_seen) == ...`` 这样的计数：它们证明某一跳**根本没有离开进程**，
而不是「离开了但结果被丢弃了」—— 后者对 §5 那些要求毫无意义。

那个架子（假传输 + 假 DNS + 两条记录 + 清理由 ``conftest.py`` 提供的 ``requests_seen``
夹具）现在住在 ``tests/fixtures/http_transport.py``：``test_http_fetch_attempts.py``
是它的第二个消费者，而两份会分叉的架子会让两个文件对同一件事给出不同答案。

## 一条判据与它的对照组

本文件里几处成对出现（20 个账号 3 次 / 20 本账 20 次；挑战页不落盘 / 同一批字节正常落盘；
同一个正文带不带那个头）。对照组不是冗余：一条「什么都不落盘」或「什么都不发」的实现
会让相反的判据照样通过，而它只在这一半里变红。
"""

from __future__ import annotations

import httpx
import pytest

from marketops.adapters.http import HttpxFetcher
from marketops.domain.access import ActionTaken, Observation, SuspectedCause, diagnose
from marketops.domain.access_governance import AccessGovernor, AccessPolicy
from marketops.domain.enums import BlockReason, FetchOutcome, FetchTier
from marketops.domain.util import sha256_text
from marketops.interfaces.composition import default_acquisition, default_http
from marketops.ports.acquisition import FetchRequest, FetchResult

from .fixtures.http_transport import (
    CHALLENGE_BODY,
    PAGE,
    PLAIN_BODY,
    SECOND,
    SITE,
    make_fetcher,
    mirrored,
    page,
    redirect,
)

ACCOUNTS = 20


# ----------------------------------------------------------------------
# §5 的那条判据：20 个账号、同一个来源、一本账
# ----------------------------------------------------------------------
def test_twenty_accounts_on_one_challenge_page_send_three_requests(
    settings, requests_seen: list[str]
) -> None:
    """§5 逐字：「同一个源挑战页上的 20 个账号，不得生成 20 个无界重试套件」。

    20 个账号、20 次独立抓取、**一本共享的账**。真正发出去的恰好是
    ``circuit_setback_threshold`` 次 —— 「有界」在这条判据上的读数就是它，而它是
    **从 policy 读出来的**，不是本文件里另写的一个 3。
    """
    governor = AccessGovernor()
    fetcher = make_fetcher(settings, {PAGE: page(CHALLENGE_BODY)}, governor=governor)
    budget = governor.policy.circuit_setback_threshold

    results = [
        fetcher.fetch(FetchRequest(url=PAGE, scope=f"account-{index:03d}"))
        for index in range(ACCOUNTS)
    ]

    assert len(requests_seen) == budget, (
        f"线上发出了 {len(requests_seen)} 次请求；共享一本账时应当是 {budget} 次"
        "（熔断在那一刻打开，之后一个字节都不发）"
    )
    assert all(result.outcome is FetchOutcome.BLOCKED for result in results)
    assert all(
        result.block_reason is BlockReason.WAF_CHALLENGE for result in results[:budget]
    ), "前几个账号真的拿到了那份挑战页"
    assert all(result.body_path is None for result in results)
    assert mirrored(settings) == [], "挑战正文一个字节都不落盘"
    assert all(result.diagnosis is not None for result in results), (
        "直连那一层的每一份回执都带着诊断。它没有诊断的那一天，「这一层没接过诊断」"
        "会与「这一次一切正常」读成同一句（见 FetchResult.diagnosis）"
    )

    for result in results[budget:]:
        assert result.block_reason is BlockReason.UNKNOWN, (
            "这一跳没有观测，所以没有任何一条细分理由成立 —— UNKNOWN 是那诚实的回答"
        )
        assert result.reason == f"governed:{ActionTaken.CIRCUIT_OPENED.value}"


def test_with_one_book_per_account_the_same_page_costs_twenty_requests(
    settings, requests_seen: list[str]
) -> None:
    """**对照组**：把「共享」那一步拿掉，同一条线上会发出多少次。

    这一条不是产品行为，它是上面那条判据的分母。没有它，那个 3 可能只是
    「20 个账号各发了一次、其中 17 次因为别的原因没发出去」的巧合。
    """
    results = []
    for index in range(ACCOUNTS):
        fetcher = make_fetcher(settings, {PAGE: page(CHALLENGE_BODY)})
        results.append(fetcher.fetch(FetchRequest(url=PAGE, scope=f"account-{index:03d}")))

    assert len(requests_seen) == ACCOUNTS, (
        "每个账号各拿一本新账时，熔断永远不会打开 —— 账上永远只有一次挫折"
    )
    assert all(result.outcome is FetchOutcome.BLOCKED for result in results)


def test_a_refused_hop_never_leaves_the_process(settings, requests_seen: list[str]) -> None:
    """被治理层拒付的那一跳：**根本不存在**，而回执上说得出为什么。

    用 ``circuit_setback_threshold=1`` 把这条判据压到一次抓取上：第一次因为挑战
    触到阈值，第二次连问都没问。
    """
    governor = AccessGovernor(policy=AccessPolicy(circuit_setback_threshold=1))
    fetcher = make_fetcher(settings, {PAGE: page(CHALLENGE_BODY)}, governor=governor)

    first = fetcher.fetch(FetchRequest(url=PAGE, scope="account-001"))
    assert first.outcome is FetchOutcome.BLOCKED
    assert first.diagnosis is not None
    assert first.diagnosis.action is ActionTaken.CIRCUIT_OPENED

    refused = fetcher.fetch(FetchRequest(url=PAGE, scope="account-002"))

    assert len(requests_seen) == 1, "第二次抓取一个请求都没发"
    assert refused.outcome is FetchOutcome.BLOCKED
    assert refused.reason == f"governed:{ActionTaken.CIRCUIT_OPENED.value}"
    assert refused.redirect_trace == (), "被拒的那一跳不在链上 —— 它没有发生"
    assert refused.final_url == PAGE, "final_url 是**本来要去**的地址"
    assert refused.status_code is None
    assert refused.body_path is None
    assert refused.block_reason is BlockReason.UNKNOWN
    assert refused.error, "拒付必须留下人话，否则报告里只有一栏 BLOCKED"
    assert refused.diagnosis is not None
    assert refused.diagnosis.cause is SuspectedCause.WAF_CHALLENGE, (
        "根因取自**来源的账**：这个来源上一次因为挑战被记了一笔。这一份诊断不是"
        "重新观测出来的，它说的是账上那个理由"
    )
    assert refused.diagnosis.action is ActionTaken.CIRCUIT_OPENED
    assert "熔断打开中" in refused.diagnosis.detail


# ----------------------------------------------------------------------
# 挑战正文不进业务证据（§4）—— 以及它的两半对照组
# ----------------------------------------------------------------------
def test_a_challenge_page_is_never_mirrored(settings, requests_seen: list[str]) -> None:
    """§4 逐字：挑战正文「不当网页证据」。落点是 ``body_path is None``。

    同一条判据里必须有对照：一个**什么都不落盘**的实现会让上面那一半照样通过。
    """
    blocked = make_fetcher(settings, {PAGE: page(CHALLENGE_BODY)}).fetch(
        FetchRequest(url=PAGE, scope="account-001")
    )

    assert blocked.outcome is FetchOutcome.BLOCKED
    assert blocked.block_reason is BlockReason.WAF_CHALLENGE
    assert blocked.body_bytes > 0, "字节**收到了** —— 这条测的是「扣下」，不是「没收到」"
    assert blocked.body_path is None
    assert blocked.body_sha256 == "", "没有落盘就不该有一个指向字节的摘要"
    assert mirrored(settings) == []

    kept = make_fetcher(settings, {PAGE: page(PLAIN_BODY)}).fetch(
        FetchRequest(url=PAGE, scope="account-002")
    )

    assert kept.outcome is FetchOutcome.OK
    assert kept.body_path is not None and kept.body_path.is_file()
    assert kept.body_path.read_bytes() == PLAIN_BODY.encode()
    assert kept.body_sha256 == sha256_text(PLAIN_BODY)
    assert mirrored(settings) == [kept.body_path]


def test_a_200_with_the_challenge_header_is_refused_and_its_body_is_not_kept(
    settings, requests_seen: list[str]
) -> None:
    """``cf-mitigated: challenge`` 是**响应头**，而分类器只看状态码与正文。

    正文里没有挑战指纹的挑战页，在分类器眼里就是一个正常的 200 —— 那一页的正文会
    作为业务证据落盘。``domain/access.py`` 读得到那个头，所以「以诊断为准」这一次
    改写发生在接缝上，而不是在分类器里。
    """
    result = make_fetcher(
        settings, {PAGE: page(PLAIN_BODY, headers={"cf-mitigated": "challenge"})}
    ).fetch(FetchRequest(url=PAGE, scope="account-001"))

    assert result.status_code == 200
    assert result.body_bytes > 0
    assert result.outcome is FetchOutcome.BLOCKED, (
        "分类器答的是 OK；结论必须被诊断改写 —— 否则挑战正文会成为业务证据"
    )
    assert result.block_reason is BlockReason.WAF_CHALLENGE
    assert result.body_path is None
    assert result.body_sha256 == ""
    assert mirrored(settings) == []
    assert result.diagnosis is not None and result.diagnosis.challenge_detected


def test_the_challenge_header_is_what_made_the_difference(
    settings, requests_seen: list[str]
) -> None:
    """**对照**：同一个状态码、同一批字节，只少了那个头。

    两条回执读起来必须完全相反，于是「不同」只能归因到那个头上。
    """
    with_header = make_fetcher(
        settings, {PAGE: page(PLAIN_BODY, headers={"cf-mitigated": "challenge"})}
    ).fetch(FetchRequest(url=PAGE, scope="account-001"))
    without = make_fetcher(settings, {PAGE: page(PLAIN_BODY)}).fetch(
        FetchRequest(url=PAGE, scope="account-002")
    )

    assert with_header.status_code == without.status_code == 200
    assert with_header.body_bytes == without.body_bytes
    assert with_header.outcome is FetchOutcome.BLOCKED
    assert without.outcome is FetchOutcome.OK
    assert with_header.body_path is None
    assert without.body_path is not None
    assert without.diagnosis is not None
    assert without.diagnosis.cause is SuspectedCause.NONE
    assert without.diagnosis.action is ActionTaken.PROCEEDED


# ----------------------------------------------------------------------
# 429 / Retry-After：等待期是**来源**的
# ----------------------------------------------------------------------
def test_a_retry_after_on_one_account_holds_the_whole_origin(
    settings, requests_seen: list[str]
) -> None:
    """「按源共享速率限制」在这里是什么意思：另一个账号会撞上第一个账号挣来的等待期。

    两件事必须同时成立 —— 第一个账号的回执**保住它的观测**（状态码、Retry-After），
    第二个账号说得出它为什么没发（根因从**账**上读，不从自己的观测读，因为它没有观测）。
    """
    governor = AccessGovernor()
    routes = {
        PAGE: page("Too many requests. Please slow down.", status=429, headers={"retry-after": "120"})
    }
    fetcher = make_fetcher(settings, routes, governor=governor)

    first = fetcher.fetch(FetchRequest(url=PAGE, scope="account-001"))

    assert first.status_code == 429, (
        "有观测就带着观测走：把状态码换成一句 governed:* 会让报告里那条 URL 变成"
        "一次**没有观测**的失败，而排查要看的第一样东西正是「那一跳看到了什么」"
    )
    assert first.outcome is FetchOutcome.BLOCKED
    assert first.block_reason is BlockReason.HTTP_429
    assert first.body_path is None
    assert first.diagnosis is not None
    assert first.diagnosis.cause is SuspectedCause.RATE_LIMITED
    assert first.diagnosis.action is ActionTaken.DEFERRED_BACKOFF
    assert first.diagnosis.retry_after_s == 120

    second = fetcher.fetch(FetchRequest(url=PAGE, scope="account-002"))

    assert len(requests_seen) == 1, "第二个账号**一个请求都没发** —— 等待期是来源的，不是账号的"
    assert second.reason == f"governed:{ActionTaken.DEFERRED_BACKOFF.value}"
    assert second.redirect_trace == ()
    assert second.diagnosis is not None
    assert second.diagnosis.cause is SuspectedCause.RATE_LIMITED

    (report,) = governor.snapshot()
    assert report.origin == SITE
    assert 118.0 <= report.wait_s <= 120.0, (
        "服务端要的 120 秒被记在**来源**上。抓取器不 sleep —— 一次 "
        "``Retry-After: 300`` 变成一次 300 秒的 sleep 会占住一个 worker"
    )


def test_a_redirect_does_not_spend_the_retry_budget(settings, requests_seen: list[str]) -> None:
    """预算按**跳**算，不按调用算。

    ``max_attempts_per_fetch=1`` 是一个「不重试」的策略。若一个 permit 跨跳共享，
    一条 ``302 → 200`` 的链会在第二跳上报「预算用尽」—— 那时我们看到的是一个**成功**
    的页面被丢掉，而理由是「重试太多」。
    """
    governor = AccessGovernor(policy=AccessPolicy(max_attempts_per_fetch=1))
    fetcher = make_fetcher(
        settings, {PAGE: redirect(PAGE, SECOND), SECOND: page(PLAIN_BODY)}, governor=governor
    )

    result = fetcher.fetch(FetchRequest(url=PAGE, scope="account-001"))

    assert result.outcome is FetchOutcome.OK, result.reason
    assert requests_seen == [PAGE, SECOND]
    assert result.redirect_trace == (PAGE, SECOND)
    assert result.final_url == SECOND
    assert result.diagnosis is not None and result.diagnosis.cause is SuspectedCause.NONE


@pytest.mark.parametrize(
    ("cap", "expected_action"),
    [(1, ActionTaken.STOPPED_METHOD), (3, ActionTaken.CIRCUIT_OPENED)],
)
def test_a_source_that_keeps_saying_retry_now_is_still_bounded(
    settings, requests_seen: list[str], cap: int, expected_action: ActionTaken
) -> None:
    """服务端连着说「现在就试」（``Retry-After: 0``）时，界在哪里。

    这是 ``max_attempts_per_fetch`` 存在的理由。在「不 sleep、等待期记在来源上」的
    实现里，同一个调用里再发第二次只发生在服务端明说现在就可以的时候 —— 而**次数由
    policy 数，不由服务端数**。

    两个 cap 收在**两个不同的理由**上，而两个都不是「还要再试」：``cap=1`` 撞上这次
    访问的重试预算；``cap=3`` 在第三次挫折上打开熔断（阈值恰好是 3，所以熔断那一格
    先赢——先记下结果再判处置）。这两条一起说明「有界」不是只靠一种机制。
    """
    governor = AccessGovernor(policy=AccessPolicy(max_attempts_per_fetch=cap))
    routes = {
        PAGE: page("Too many requests. Please slow down.", status=429, headers={"retry-after": "0"})
    }
    fetcher = make_fetcher(settings, routes, governor=governor)

    result = fetcher.fetch(FetchRequest(url=PAGE, scope="account-001"))

    assert len(requests_seen) == cap, "次数由 policy 数，不由服务端数"
    assert result.status_code == 429
    assert result.outcome is FetchOutcome.BLOCKED
    assert result.block_reason is BlockReason.HTTP_429
    assert result.body_path is None
    assert result.diagnosis is not None
    assert result.diagnosis.cause is SuspectedCause.RATE_LIMITED
    assert result.diagnosis.action is expected_action
    assert result.diagnosis.action is not ActionTaken.DEFERRED_BACKOFF, (
        "收场的那一格不能是一份读起来像还要重试的回执"
    )
    if expected_action is ActionTaken.STOPPED_METHOD:
        assert "预算已用尽" in result.diagnosis.detail

    (report,) = governor.snapshot()
    assert report.opened_count == (1 if expected_action is ActionTaken.CIRCUIT_OPENED else 0)


# ----------------------------------------------------------------------
# SDK 的隐藏重试 / 403 的默认结论 / 传输失败 / 组合根
# ----------------------------------------------------------------------
def test_the_sdk_hidden_retries_are_pinned_to_zero(settings) -> None:
    """§5：「SDK 隐藏的重试必须可观察，且不得与上层的重试双重放大」。

    ``httpx`` 的默认值**恰好也是** 0，所以这一条读回而不是断言「默认如此」：一次库
    升级、一个被包装过的 client、或者谁把 transport 换掉，都会让这一层在我们数不到的
    地方重发请求。下面那个三行的对照组证明这个读数是活的。
    """
    assert httpx.Client(transport=httpx.HTTPTransport(retries=3))._transport._pool._retries == 3, (
        "对照组：读数本身是活的（一个真的开了重试的 transport 读回 3）"
    )
    assert HttpxFetcher(settings)._client._transport._pool._retries == 0
    assert HttpxFetcher(settings, governor=AccessGovernor())._client._transport._pool._retries == 0


def test_a_403_is_not_an_ip_ban_and_it_does_book_a_setback(
    settings, requests_seen: list[str]
) -> None:
    """§5：403 与 Ray ID 都**不是**「出口 IP 被封」的证据。

    这一条同时钉住「判定与处置分开」：``diagnose`` 对一次 403 说的是「请人看一眼」，
    而治理层把**处置**改成「这个方法到此为止」—— 一次 403 不是一次推迟，重试不会把它
    变成 200。两句话并排写在同一份回执上。
    """
    raw = diagnose(Observation(observed_status=403, text="Forbidden"))
    assert raw.cause is SuspectedCause.ACCESS_DENIED_UNKNOWN
    assert raw.cause is not SuspectedCause.IP_POLICY
    assert raw.action is ActionTaken.REVIEW_REQUESTED, "判定说的是「根因不明，请人看」"

    governor = AccessGovernor()
    fetcher = make_fetcher(settings, {PAGE: page("Forbidden", status=403)}, governor=governor)

    result = fetcher.fetch(FetchRequest(url=PAGE, scope="account-001"))

    assert result.outcome is FetchOutcome.BLOCKED
    assert result.block_reason is BlockReason.HTTP_403
    assert result.body_path is None
    assert result.redirect_trace == (PAGE,)
    assert result.diagnosis is not None
    assert result.diagnosis.cause is SuspectedCause.ACCESS_DENIED_UNKNOWN
    assert result.diagnosis.action is ActionTaken.STOPPED_METHOD, (
        "而**处置**是治理层那一格 —— 同一个根因在不同 policy 下处置不同"
    )

    (report,) = governor.snapshot()
    assert report.origin == SITE
    assert report.consecutive_setbacks == 1
    assert report.last_cause == SuspectedCause.ACCESS_DENIED_UNKNOWN.value
    assert report.wait_s == 0.0, "只有「服务端要过等待」的那一类才记等待期；一次 403 没有"


def test_a_transport_failure_keeps_its_hop_and_says_why(
    settings, requests_seen: list[str]
) -> None:
    """一次传输失败：那一跳**在链上**，诊断有根因，而来源账上记了一笔等待。

    ``observed_status`` 留空而不是折成一个数 —— 一次没问到状态码的访问不是一次 200，
    也不是一次 0。
    """
    governor = AccessGovernor()
    fetcher = make_fetcher(
        settings, {PAGE: httpx.ConnectTimeout("连接超时")}, governor=governor
    )

    result = fetcher.fetch(FetchRequest(url=PAGE, scope="account-001"))

    assert result.outcome is FetchOutcome.TIMEOUT
    assert result.status_code is None, "没有状态码就是 None，不是 0，也不是 200"
    assert result.reason == f"transport:{FetchOutcome.TIMEOUT.value}"
    assert result.redirect_trace == (PAGE,), "它真的发出去了 —— 链上留着它"
    assert result.error and result.body_path is None
    assert result.diagnosis is not None
    assert result.diagnosis.cause is SuspectedCause.NETWORK_UNKNOWN, (
        "一次 ConnectTimeout 是网络未通，不是「网站不存在」"
    )
    assert result.diagnosis.action is ActionTaken.DEFERRED_BACKOFF

    assert len(requests_seen) == 1, (
        "抓取器**不 sleep**：等待期记在来源上，这次访问到此为止。同一个调用里再发"
        "第二次只发生在服务端明说「现在就试」的时候"
    )
    (report,) = governor.snapshot()
    assert report.consecutive_setbacks == 1
    assert report.wait_s > 0.0


def test_the_composition_root_hands_every_fetcher_the_same_book(settings) -> None:
    """一个进程一本账，而**注入发生在组合根这一处、也只有这一处**。

    抓取器缺省自建一本是**对的**（单测与离线重放要它），所以「共享」必须有人承担 ——
    它就是组合根。这一条测的正是那个承担者还在不在。
    """
    first = default_http(settings)
    second = default_http(settings)

    assert first is not second, "两个抓取器"
    assert first._governor is second._governor, "同一本账"

    mine = AccessGovernor()
    assert default_http(settings, governor=mine)._governor is mine, (
        "显式注入的那一本必须真的被用上 —— 判据与离线重放靠的是它"
    )

    off = default_acquisition(settings, allow_browser=False)
    assert off.http._governor is first._governor, (
        "§10 那唯一的统一入口与直连抓取器必须共用一本 —— 二十个账号走的就是那条路"
    )

    on = default_acquisition(settings, allow_browser=True)
    assert on._browser is not None  # noqa: SLF001 - 组合根接没接上浏览器只有这一格答得出
    assert on._browser._governor is first._governor, (  # noqa: SLF001
        "**浏览器那一层也必须是这一本。** 它是直连失败之后的升级路径，两次请求落在"
        "**同一个来源**上；那里各拿一本干净的账，正是「同一个挑战页让 20 个账号各自"
        "无界重试」长出来的方式（§5）"
    )


def test_the_composition_root_hands_both_fetchers_one_attempt_ledger(settings) -> None:
    """两个抓取器往**同一本**尝试账上记。

    两本账的后果在报告里看不出来：同一个 URL 的两条尝试落在两个文件上，而
    「这次一共试过几次」没有一个地方答得出来 —— 与上面那条同源。

    观察的是**工厂**而不是 store 实例：``SqliteFetchAttemptStore`` 一构造就
    ``ensure_schema``（会建库），所以组合根给的是工厂，而「是不是同一个」在
    ``default_http``/``Crawl4AIBrowserFetcher`` 里都记在 ``_attempts`` 上。
    """
    calls: list[object] = []

    def factory() -> object:  # pragma: no cover - 只用来比同一性，不真的记账
        calls.append(None)
        raise AssertionError("工厂只有在第一次真的要记账时才该被调用")

    acquisition = default_acquisition(settings, attempts=factory)  # type: ignore[arg-type]

    assert acquisition.http._attempts is factory, "直连那一层"
    assert acquisition._browser is not None  # noqa: SLF001
    assert acquisition._browser._attempts is factory, (  # noqa: SLF001
        "浏览器那一层拿的必须是**同一个**工厂 —— 一处接、一处漏，账上就少了半张纸"
    )
    assert calls == [], "构造抓取器不该建库（与 `_attempt_store_or_none` 的惰性一致）"


def test_no_diagnosis_is_not_the_same_as_no_anomaly() -> None:
    """两句话必须分得开：``diagnosis is None`` 与 ``cause is NONE``。

    前者是「这一层没有产生诊断」，后者是「这一次没有任何异常」。把前者读成后者，
    正是本切片要消灭的那种错 —— 而它看起来一切正常。

    ## 这个区分今天靠什么成立

    那两句话**在类型上仍不可分**（``diagnosis`` 是可空的 ``AccessDiagnosis | None``），
    所以分开它们靠的是**生产者那一侧**：两个抓取器每一个出口都带诊断，而
    ``adapters/fetch_attempts.py::require_diagnosis`` 在收账时拒绝 ``None``。
    构造器这条口子必须留着 —— 一个白盒测试要造得出「没带诊断的回执」才测得了
    那个守卫（见 ``tests/test_http_fetch_attempts.py`` 的 ``_Undiagnosed``）。
    """
    bare = FetchResult(
        request=FetchRequest(url=PAGE), outcome=FetchOutcome.OK, tier=FetchTier.HTTP
    )
    assert bare.diagnosis is None

    assert diagnose(Observation(observed_status=200, body_usable=True)).cause is SuspectedCause.NONE
    assert diagnose(Observation()).cause is SuspectedCause.CONTENT_UNUSABLE, (
        "一份**空**的观测不是「没有异常」—— 读不出状态码且正文不可用是一次内容问题。"
        "这与本切片同源：把「不知道」折成「正常」正是 ``status_code or 200`` 那一类错，"
        "而诊断层拒绝替它做那次折算"
    )
