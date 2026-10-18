"""B15：观测与根因分开、403 不推 IP 封禁、挑战正文不入证据、Retry-After 可解析。

这一组判据对应采集设计 §4 那张表的**每一行**，以及 §11 验收矩阵里与访问有关的
那几行。它们的共同点是否定式：**大部分判据证明的不是「算出了什么」，而是
「算不出什么」** —— 403 不得产出 ``ip_policy``、200+cf-ray 不得产出挑战、
未观测到的状态码不得折成 200。一个只测正例的实现可以同时满足全部正向期望，
却在最该拦住的那一格里放行。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from marketops.domain.access import (
    RETRY_AFTER_MAX_SECONDS,
    AccessDiagnosis,
    ActionTaken,
    Observation,
    SuspectedCause,
    diagnose,
    parse_retry_after,
    safe_response_headers,
    with_cause_evidence,
)
from marketops.domain.fetch_attempt import (
    BodyKind,
    FetchAttempt,
    MethodKind,
    SettlementState,
)
from marketops.domain.util import utc_now

NOW = datetime(2026, 9, 27, 12, 0, 0, tzinfo=timezone.utc)


# --- 第一组：403 永远不是 ip_policy -----------------------------------------


def test_403_with_ray_id_is_not_an_ip_ban() -> None:
    """**这条判据是本切片存在的理由。**

    采集设计 §4：403 可以由源站权限规则或 Cloudflare 触发；Ray ID 只关联经过
    Cloudflare 的请求，不是封 IP 证明。所以一个「403 + ``cf-ray`` + ``server:
    cloudflare``」的响应，结论必须是根因不明。

    这条判据杀掉的正是那种看起来最像洞察的改动：把 ``cf-ray`` 的存在读成
    「出口 IP 被封」。
    """
    verdict = diagnose(
        Observation(
            observed_status=403,
            headers={"cf-ray": "8a1b2c3d4e5f", "server": "cloudflare"},
            text="<html><body>Forbidden</body></html>",
        )
    )
    assert verdict.cause is SuspectedCause.ACCESS_DENIED_UNKNOWN
    assert verdict.cause is not SuspectedCause.IP_POLICY


@pytest.mark.parametrize(
    "observation",
    [
        Observation(observed_status=403),
        Observation(observed_status=403, headers={"cf-ray": "a" * 20}),
        Observation(observed_status=403, headers={"server": "cloudflare"}),
        Observation(observed_status=200, body_usable=True),
        Observation(observed_status=200, headers={"cf-ray": "b" * 20}, body_usable=True),
        Observation(observed_status=429, headers={"retry-after": "30"}),
        Observation(observed_status=503),
        Observation(observed_status=None, body_usable=True),
        Observation(transport_error="reset", transport_error_kind="ConnectionResetError"),
    ],
)
def test_machine_diagnosis_never_invents_an_ip_policy_ban(observation: Observation) -> None:
    """机器判定**在任何输入下**都不得产出 ``ip_policy``。

    按构造：``IP_POLICY`` 只能由 :func:`with_cause_evidence` 产生，而那个函数
    要求一个非空的证据引用。这条参数化判据把「按构造」变成「按穷举」——
    加一个分支而忘了这条纪律时，它会红。
    """
    assert diagnose(observation).cause is not SuspectedCause.IP_POLICY


def test_ip_policy_requires_a_citation() -> None:
    """没有出处的根因不是诊断 —— 它是某个人记得。"""
    with pytest.raises(ValueError, match="cause_evidence_ref"):
        with_cause_evidence(SuspectedCause.IP_POLICY, "   ", "我猜是封了")
    with pytest.raises(ValueError, match="NONE"):
        with_cause_evidence(SuspectedCause.NONE, "ticket-1", "")

    verdict = with_cause_evidence(
        SuspectedCause.IP_POLICY, "site-operator-mail-2026-09-27", "站点方书面确认"
    )
    assert verdict.cause is SuspectedCause.IP_POLICY
    assert verdict.cause_evidence_ref == "site-operator-mail-2026-09-27"


# --- 第二组：挑战的识别与「正文不当证据」 -----------------------------------


def test_challenge_header_alone_is_enough() -> None:
    """``cf-mitigated: challenge`` 是**明确观测**（CF2），正文可以是任意内容。"""
    verdict = diagnose(
        Observation(observed_status=200, headers={"cf-mitigated": "challenge"}, body_usable=True)
    )
    assert verdict.cause is SuspectedCause.WAF_CHALLENGE
    assert verdict.challenge_detected is True


def test_challenge_text_alone_is_enough() -> None:
    """缺少 ``cf-mitigated`` **不等于**没有其他拦截 —— 正文指纹独立生效。"""
    verdict = diagnose(Observation(observed_status=200, text="Just a moment...", body_usable=True))
    assert verdict.cause is SuspectedCause.WAF_CHALLENGE


def test_a_challenge_page_cannot_become_evidence() -> None:
    """设计 §4：挑战正文「不当网页证据」。这一格由 ``body_may_be_kept`` 承载。"""
    blocked = diagnose(Observation(observed_status=403, headers={"cf-mitigated": "challenge"}))
    assert blocked.body_may_be_kept is False
    normal = diagnose(Observation(observed_status=200, body_usable=True))
    assert normal.body_may_be_kept is True


def test_only_a_challenge_withholds_the_body() -> None:
    """**全表**：扣下正文的根因有且只有 ``WAF_CHALLENGE`` 一个。

    抓取层有一处改写读的就是这一格 —— ``adapters/http.py`` 里那个「分类器说 OK、
    诊断说别留正文」的接缝，把结论改成 ``BLOCKED`` 并写死 ``BlockReason.WAF_CHALLENGE``。
    那条字面量之所以站得住，靠的正是这里的**充要**关系：若哪天第二个根因也开始扣正文，
    那个实现会给它贴上一张 ``WAF_CHALLENGE`` 的标签，而**没有任何一条判据会转红**。

    所以这条不查那两个例子，它查**整个枚举** —— 两个例子的写法在第三个根因出现时
    自动失效，而这一条不会。
    """
    withheld = {
        cause for cause in SuspectedCause if not AccessDiagnosis(cause, ActionTaken.PROCEEDED, "").body_may_be_kept
    }
    assert withheld == {SuspectedCause.WAF_CHALLENGE}


def test_a_plain_page_with_ray_id_is_not_a_challenge() -> None:
    """**反方向的那一条**：200 + ``cf-ray`` + 正常正文，不得判成挑战。

    只测「挑战能被认出来」会漏掉这一半 —— 一个过宽的指纹会把整个 Cloudflare
    上的正常站点全部判成挑战，而那正是「把普通站点当成墙」的另一种事故。
    """
    verdict = diagnose(
        Observation(
            observed_status=200,
            headers={"cf-ray": "c" * 20, "server": "cloudflare", "content-type": "text/html"},
            text="<html><body><p>Welcome to our club</p></body></html>",
            body_usable=True,
        )
    )
    assert verdict.cause is SuspectedCause.NONE
    assert verdict.challenge_detected is False


# --- 第三组：状态码那一格不许被折算 -----------------------------------------


def test_unknown_status_does_not_become_200() -> None:
    """没有观测到状态码时：正文可用则继续，但**报告上那一格是空的**。

    这是 ``status_code or 200`` 那处缺陷在诊断层的对应判据。判据分两半，两半
    都必须在：结论可以继续（正文确实可用），但结论**不得**是一句「HTTP 正常」。
    """
    usable = diagnose(Observation(observed_status=None, body_usable=True))
    assert usable.cause is SuspectedCause.NONE
    assert "未观测到" in usable.detail
    assert "留空" in usable.detail  # 明确说出「如实留空」，而不是折算成一个读数

    # 这一半比上面那句散文更承重：**``cause is NONE`` 不等于「这次访问成功了」**。
    # 观测到 200 与根本没观测到状态码，在这里给出同一个结论 —— 因为这一格回答的是
    # 「有没有理由怀疑出了事」，不是「HTTP 是不是 200」。谁要拿 NONE 当成功读数，
    # 谁就得先解释这两次为什么长得一样。真正的成功读数在 ``FetchAttempt.ok``。
    assert usable.cause is diagnose(Observation(observed_status=200, body_usable=True)).cause

    unusable = diagnose(Observation(observed_status=None, body_usable=False))
    assert unusable.cause is SuspectedCause.CONTENT_UNUSABLE
    assert unusable.action.value == "withhold_body"


def test_transport_error_beats_a_stale_status_code() -> None:
    """一次既超时又带着缓存状态码的记录，说「网络未通」比说「403」更接近事实。"""
    verdict = diagnose(
        Observation(
            observed_status=403,
            transport_error="connection reset by peer",
            transport_error_kind="ConnectionResetError",
        )
    )
    assert verdict.cause is SuspectedCause.NETWORK_UNKNOWN


def test_unrecognised_transport_error_is_transient_not_permanent() -> None:
    """没见过这个错误名 ≠ 这是一次永久失败。"""
    verdict = diagnose(Observation(transport_error="who knows", transport_error_kind="WeirdError"))
    assert verdict.cause is SuspectedCause.TRANSIENT_OR_UNKNOWN


# --- 第四组：Retry-After ------------------------------------------------------


def test_retry_after_accepts_both_legal_forms() -> None:
    """RFC 9110 的两种写法都要认 —— 只认一种会把另一种读成「服务端没说」。"""
    assert parse_retry_after("120") == 120
    assert parse_retry_after("0") == 0
    future = (NOW + timedelta(seconds=90)).strftime("%a, %d %b %Y %H:%M:%S GMT")
    parsed = parse_retry_after(future, now=NOW)
    assert parsed is not None and 85 <= parsed <= 95


def test_retry_after_refuses_to_guess() -> None:
    """解析不出来就是 ``None`` —— 猜一个默认值会变成一次立刻重试。"""
    assert parse_retry_after(None) is None
    assert parse_retry_after("") is None
    assert parse_retry_after("soon") is None
    assert parse_retry_after("-5") is None
    assert parse_retry_after("Wed, 21 Oct 2099 07:28:00 GMT", now=NOW) is None
    assert parse_retry_after(str(RETRY_AFTER_MAX_SECONDS + 1)) is None


def test_a_past_http_date_is_zero_not_none() -> None:
    """「服务端给了一个已经过去的时间」与「服务端没给」是两件事。"""
    assert parse_retry_after("Wed, 21 Oct 2015 07:28:00 GMT", now=NOW) == 0
    assert parse_retry_after("not a date at all") is None


# --- 第五组：敏感头不许进报告 ------------------------------------------------


def test_safe_response_headers_drops_everything_not_on_the_allowlist() -> None:
    kept = safe_response_headers(
        {
            "Content-Type": "text/html",
            "CF-RAY": "abc123",
            "Set-Cookie": "session=secret",
            "Authorization": "Bearer secret",
            "X-Api-Key": "secret",
            "Location": "/elsewhere",
        }
    )
    assert kept == {"content-type": "text/html", "cf-ray": "abc123", "location": "/elsewhere"}
    assert "set-cookie" not in kept and "authorization" not in kept


def test_attempt_resanitises_headers_the_caller_forgot() -> None:
    """**调用方洗过了**是一次假设；它失效的后果是凭据进报告。这里再洗一遍。"""
    attempt = FetchAttempt(
        attempt_id="att-1",
        workspace="ws",
        method=MethodKind.HTTP,
        url="https://example.com/a",
        started_at=NOW,
        ended_at=NOW + timedelta(milliseconds=120),
        observed_status=200,
        response_headers={"Set-Cookie": "s=1", "cf-ray": "deadbeef", "content-type": "text/html"},
    )
    assert attempt.response_headers == {"cf-ray": "deadbeef", "content-type": "text/html"}


# --- 第六组：FetchAttempt 自己那几格 ----------------------------------------


def test_attempt_keeps_an_unobserved_status_empty() -> None:
    attempt = FetchAttempt(
        attempt_id="att-2",
        workspace="ws",
        method=MethodKind.BROWSER,
        url="https://example.com/a",
        started_at=NOW,
        ended_at=NOW + timedelta(seconds=2),
        observed_status=None,
        body_kind=BodyKind.HTML,
        body_bytes=10,
        body_sha256="a" * 64,
    )
    assert attempt.status_unknown is True
    assert attempt.ok is False
    assert attempt.body_kept is True
    assert attempt.elapsed_ms == 2000


def test_attempt_rejects_a_bogus_hash_and_naive_clock() -> None:
    """两格都要能被钉住：摘要不是摘要、时刻不带时区都不得写进记录。"""
    with pytest.raises(ValueError):
        FetchAttempt(
            attempt_id="att-3",
            workspace="ws",
            method=MethodKind.HTTP,
            url="https://example.com/a",
            started_at=NOW,
            ended_at=NOW,
            body_sha256="not-a-hash",
        )
    with pytest.raises(ValueError, match="时区"):
        FetchAttempt(
            attempt_id="att-4",
            workspace="ws",
            method=MethodKind.HTTP,
            url="https://example.com/a",
            started_at=utc_now().replace(tzinfo=None),
            ended_at=NOW,
        )


def test_attempt_defaults_are_honest() -> None:
    """缺省值必须是「我们没做」，不是一个听起来像成功的值。"""
    attempt = FetchAttempt(
        attempt_id="att-5",
        workspace="ws",
        method=MethodKind.HTTP,
        url="https://example.com/a",
        started_at=NOW,
        ended_at=NOW,
    )
    assert attempt.suspected_cause is SuspectedCause.NONE
    assert attempt.budget_settlement is SettlementState.NOT_APPLICABLE
    assert attempt.body_kind is BodyKind.NONE
    assert attempt.retry_after_s is None
    assert attempt.observed_status is None
    assert attempt.action_taken.value == "proceeded"
