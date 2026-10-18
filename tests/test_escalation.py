"""升级判定的穷举测试。

「HTTP 成功就不要启动浏览器」与「明确受阻不要硬闯」这两条，
在这里被逐种终态钉死。任何一个新加的 ``FetchOutcome`` 成员如果不在这张表里，
``test_every_outcome_is_covered`` 会失败 —— 逼实现者显式决定它的升级行为。
"""

from __future__ import annotations

import pytest

from marketops.ports.acquisition import FetchRequest, FetchResult
from marketops.application.escalation import decide
from marketops.domain.enums import BlockReason, FetchOutcome, FetchTier

REQUEST = FetchRequest(url="https://example.co.uk/")


def _result(outcome: FetchOutcome, *, block_reason: BlockReason | None = None) -> FetchResult:
    return FetchResult(
        request=REQUEST, outcome=outcome, tier=FetchTier.HTTP, block_reason=block_reason
    )


@pytest.mark.parametrize(
    "outcome",
    [FetchOutcome.JS_REQUIRED, FetchOutcome.EMPTY_SHELL, FetchOutcome.RENDER_FAILURE],
)
def test_browser_renderable_outcomes_escalate(outcome: FetchOutcome) -> None:
    decision = decide(_result(outcome))
    assert decision.escalate is True
    assert outcome.value in decision.reason


def test_http_ok_never_escalates() -> None:
    """需求原文：HTTP 成功就不要启动浏览器。"""
    decision = decide(_result(FetchOutcome.OK))
    assert decision.escalate is False
    assert decision.reason == "http_ok"


def test_blocked_is_terminal_even_when_browser_available() -> None:
    """WAF / challenge 明确受阻 → 不得尝试绕过，浏览器也不行。"""
    decision = decide(
        _result(FetchOutcome.BLOCKED, block_reason=BlockReason.WAF_CHALLENGE),
        browser_available=True,
    )
    assert decision.escalate is False
    assert decision.reason == "terminal:waf_challenge"


def test_not_found_is_terminal() -> None:
    decision = decide(_result(FetchOutcome.NOT_FOUND))
    assert decision.escalate is False
    assert decision.reason.startswith("terminal:")


@pytest.mark.parametrize("outcome", [FetchOutcome.TIMEOUT, FetchOutcome.ERROR])
def test_transport_failures_do_not_escalate(outcome: FetchOutcome) -> None:
    """超时/错误是传输问题，不是渲染问题。

    把它们当成启动浏览器的理由，会让每个慢站在浏览器上白烧几十秒。
    """
    decision = decide(_result(outcome))
    assert decision.escalate is False
    assert decision.reason.startswith("no_escalation_rule")


def test_empty_shell_without_browser_does_not_escalate() -> None:
    decision = decide(_result(FetchOutcome.EMPTY_SHELL), browser_available=False)
    assert decision.escalate is False
    assert decision.reason == "browser_unavailable:empty_shell"


def test_no_second_escalation() -> None:
    """浏览器渲染失败就是失败，不无限重试。"""
    decision = decide(_result(FetchOutcome.RENDER_FAILURE), already_escalated=True)
    assert decision.escalate is False
    assert decision.reason == "already_escalated"


def test_priority_ok_beats_escalation_flag() -> None:
    """即使已经升级过，OK 也永远是终点 —— 顺序不能影响结论。"""
    assert decide(_result(FetchOutcome.OK), already_escalated=True).escalate is False


def test_every_outcome_is_covered() -> None:
    """新增 FetchOutcome 时必须显式决定它的升级行为，而不是继承默认分支。"""
    covered = {
        FetchOutcome.OK,
        FetchOutcome.JS_REQUIRED,
        FetchOutcome.EMPTY_SHELL,
        FetchOutcome.RENDER_FAILURE,
        FetchOutcome.BLOCKED,
        FetchOutcome.NOT_FOUND,
        FetchOutcome.TIMEOUT,
        FetchOutcome.ERROR,
    }
    assert covered == set(FetchOutcome), (
        "FetchOutcome 有变动。请在 test_escalation.py 中为新成员补一条升级行为断言。"
    )
