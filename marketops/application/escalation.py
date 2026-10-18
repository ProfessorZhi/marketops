"""HTTP → Browser 升级决策。

这是本模块存在的**唯一**理由：把「什么时候可以启动浏览器」收敛成一个纯函数。

需求原文是「HTTP 成功就不要启动浏览器」，以及只在
「JS required / empty shell / 技术性 browser-renderable failure」时才升级。
把这条规则写成一个可穷举测试的函数，它才不会随着调用链增长而悄悄失效。
"""

from __future__ import annotations

from dataclasses import dataclass

from ..domain.enums import BROWSER_ESCALATION_OUTCOMES, FetchOutcome, TERMINAL_OUTCOMES
from ..ports.acquisition import FetchResult


@dataclass(frozen=True)
class EscalationDecision:
    """升级与否 + 可读理由（理由会进 benchmark，用于解释浏览器秒数）。"""

    escalate: bool
    reason: str


def decide(
    initial: FetchResult,
    *,
    browser_available: bool = True,
    already_escalated: bool = False,
    unavailable_reason: str = "browser_unavailable",
) -> EscalationDecision:
    """决定是否把一次 HTTP 结果升级到 browser 重试。

    判定顺序即优先级：

    1. ``OK`` —— HTTP 已拿到完整内容，**一律不升级**。
    2. ``BLOCKED`` / ``NOT_FOUND`` —— 终态，不升级。
    3. 已经升级过一次 —— 不再次升级（浏览器渲染失败就是失败，不无限重试）。
    4. 浏览器不可用 —— 不升级（返回理由，由上层记为 metric）。
    5. ``JS_REQUIRED`` / ``EMPTY_SHELL`` / ``RENDER_FAILURE`` —— 升级。

    注意 ``TIMEOUT`` / ``ERROR`` **不在**升级集合内：它们是传输层问题，
    不是渲染问题。把它们当成「启动浏览器的理由」会让每个慢站都白烧一次浏览器。

    ``unavailable_reason`` 是**不可用的哪一种**，由调用方填（编排层分得清
    「没接线」与「接线了但依赖没装」，本模块分不清，也不该去猜）。它只影响
    第 4 条吐出来的那句话，不参与判定 —— 所以缺省值下本函数的行为与从前逐字
    相同，``tests/test_escalation.py`` 一个字都不用改。
    """
    outcome = initial.outcome

    if outcome is FetchOutcome.OK:
        return EscalationDecision(False, "http_ok")

    if outcome in TERMINAL_OUTCOMES:
        detail = initial.block_reason.value if initial.block_reason else outcome.value
        return EscalationDecision(False, f"terminal:{detail}")

    if already_escalated:
        return EscalationDecision(False, "already_escalated")

    if not browser_available:
        return EscalationDecision(False, f"{unavailable_reason}:{outcome.value}")

    if outcome in BROWSER_ESCALATION_OUTCOMES:
        return EscalationDecision(True, f"browser_renderable:{outcome.value}")

    return EscalationDecision(False, f"no_escalation_rule:{outcome.value}")


__all__ = ["EscalationDecision", "decide"]
