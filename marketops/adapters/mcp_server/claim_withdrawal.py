"""撤回一条已批准说法的处理器（B02，冻结表 §3.2 那条「Agent A 额外的一条判据」）。

冻结表把它写成一句话：``withdraw_claim`` 撤回一条 ``ApprovedClaim``，受影响**未发**
草稿 → ``hold``，**无关任务不变**，scoped + atomic。那一句的三个落点各在别处：

* 「受影响集合由证据算出来而不是关键词匹配」在
  ``adapters/sqlite/claim_withdrawal.py::drafts_citing``（判据是
  ``OutreachDraft.claim_ids`` 那个结构化引用）；
* 「全成或全不成」在那条端口的 ``withdraw``（一次事务，两半都是它的入参）；
* 「还没发出去」在 ``application/claim_withdrawal.py::_sent_keys``
  （``SENT_OR_UNKNOWN`` 那个四元集合，``FAILED`` 不在里面而 ``DELIVERY_UNKNOWN``
  在里面）。

这一层因此**一个格子都不判**：它只做三件事 —— 取时刻、把那条说法按 id 找出来、
把结果投影成一份可读回执。**为什么找哪一行在这一层、判它算不算数在应用层**，见下。

## 说法的版本由这一层挑，它的效力由应用层判

``approved_claims()`` 每个 ``claim_id`` 只回**最新**一版，而过滤过期**不在**它那一层
（端口 docstring 逐字：「过期是相对于 ``at`` 的判定，由调用方带着它的时刻做」）。
所以这一层按 id 找到那一行、**原样**交给应用层，由它判「这一刻还算不算数」并给一句
点名 ``expires_at`` 的话。

在这里顺手筛一遍 ``is_live_at`` 会把两件事合成一件：一条**已经过期**的说法会以
「没有这条说法」的形状出现，而两者的处置相反（一个去重新读最新那一版，一个去核对
id）。这也是为什么这一层**不**收 ``revision``：调用方给一个过期的版本号与给一个
正确的 id，是两件要分开的事，而「撤哪一版」不是这条工具的问题（撤回是给这个
``claim_id`` 追加一版 ``expires_at=撤回那一刻``）。

## ``workspace`` 取自令牌声明，不是参数

``ApprovedClaim`` 上没有工作区那一格（``outreach_claim`` 的主键是
``(claim_id, revision)``），而受影响的草稿**按工作区**枚举 —— 所以这一格只能由上面
给，而它取 ``settings.profile``（本部署服务的那个工作区），与
``application/claim_withdrawal.withdraw_claim`` 的 docstring 逐字同一条。
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from typing import Any

from mcp.server.mcpserver.exceptions import ToolError

from ...application.claim_withdrawal import WithdrawalResult
from ...application.claim_withdrawal import withdraw_claim as _withdraw
from ...config import Settings
from ...domain.errors import ConfigError, StoreError
from ...domain.outreach import OutreachDraft
from ...domain.util import check_canonical_id, utc_now
from ...ports.claim_withdrawal import ClaimWithdrawalStore
from ...ports.outreach import OutreachStore
from ._shared import Handler, store_failure

__all__ = ["claim_withdrawal_handlers"]


def _stamp(value: datetime | None) -> str | None:
    return None if value is None else value.isoformat()


def _draft(draft: OutreachDraft) -> dict[str, Any]:
    """一份草稿那一行的投影。``state`` 那一格是这里最要紧的（``held`` / 别的）。"""
    return draft.model_dump(mode="json")


def _result(result: WithdrawalResult, *, at: datetime) -> dict[str, Any]:
    """一次撤回的回执：**压了哪几份、放过了哪几份、各为什么**。

    ``left_alone`` 每一份都带一句为什么（应用层给的）—— 那一格是这条回执存在的理由：
    一个只报「放过了 3 份」的读数，读的人分不清那三份是「已经出去了」（处置：去对账
    那条投递链，必要时人工联系）还是「本来就压着」（处置：什么都不用做），而两者的
    下一步相反。

    ``held`` 是**真的进了 hold 的那几份的新版本**（``state=held``），不是请求里那几份：
    两条列表一起给，读的人才知道这一次的写落成了什么形状。

    ``held`` 为空是一个**正常结果**（这条说法没有任何未发的草稿引用它），而它不歧义：
    这条工具跑过了、``retired_revision`` 在，所以空列表说的是「没有受影响的」，不是
    「读错了工作区」—— 与 ``list_workstreams`` 那边要为一张空表专门说一句话的处境不同。
    """
    return {
        "claim_id": result.retirement.claim_id,
        "retired_revision": result.retirement.revision,
        "expires_at": _stamp(result.retirement.expires_at),
        "withdrawn_at": at.isoformat(),
        "held": [_draft(draft) for draft in result.held],
        "left_alone": [
            {"draft": _draft(draft), "why": why} for draft, why in result.left_alone
        ],
    }


def claim_withdrawal_handlers(
    *,
    settings: Settings,
    claims: Callable[[], OutreachStore],
    withdrawals: Callable[[], ClaimWithdrawalStore],
) -> dict[str, Handler]:
    """``withdraw_claim`` 这一条，按工具名索引。

    两个工厂都要，而它们答的是**两个不同的问题**：``claims`` 答「``claim_id`` 指的是
    哪一行」（每个 id 的最新一版），``withdrawals`` 答「本工作区里有哪些草稿引用了它、
    以及那一次原子的写」。**不能只给一个**：拿 ``OutreachStore`` 去写草稿是另一条
    路径（它一次只写一版，而撤回要两半在一个事务里），拿 ``ClaimWithdrawalStore``
    去读说法则根本读不到（那个端口只有草稿那两格）。

    ``claims`` 缺省会让「这个部署没有外联存储」与「没有这条说法」同形，而两者的
    处置不同（一个去接线，一个去核对 id）—— 与 ``launch.py`` 的 ``claims`` 那一格
    逐字同一条理由。
    """

    def withdraw_claim(claim_id: str = "") -> dict[str, Any]:
        wanted = (claim_id or "").strip()
        if not wanted:
            raise ToolError(
                "``claim_id`` 是空的。\n"
                "  这一条按 id 撤回**一条**已批准说法，而没有 id 时没有可撤的东西 —— "
                "一个「撤最近那一条」的兜底会让一次漏传参数变成一次真的撤回，而报告上"
                "看不出来撤的是哪一条。"
            )
        try:
            check_canonical_id(wanted, "claim_id")
        except ValueError as exc:
            raise ToolError(
                f"{exc}\n"
                "  这一格是**规范 id**（``ApprovedClaim.claim_id`` 过同一个检查），"
                "所以形状不对时不必去查库：它与「这条说法不在盘上」是两件事，处置也"
                "不同（一个改参数，一个核对清单）。"
            ) from exc

        at = utc_now()
        try:
            book = claims()
            # 只按 id 挑，**不**在这里判它还算不算数 —— 那一句由应用层带着 ``at``
            # 说，见模块 docstring 那一节。
            claim = next(
                (row for row in book.approved_claims() if row.claim_id == wanted), None
            )
            if claim is None:
                raise ToolError(
                    f"没有 {wanted!r} 这条已批准说法（``approved_claims()`` 里读不到）。\n"
                    "  这一条读的是每个 ``claim_id`` 的**最新**一版，所以「读不到」"
                    "只可能是这个 id 从来没被批准过 —— 请核对它是不是那条说法的 id"
                    "（草稿那一行的 ``claim_ids`` 与登记它时用的那个 id 是同一格）。\n"
                    "  本构建没有一条读说法的工具：这一格今天只能从草稿回链或登记回执"
                    "上读回来（``put_claim`` 的回执里有它）。"
                )
            result = _withdraw(
                workspace=settings.profile,
                claim=claim,
                at=at,
                store=withdrawals(),
            )
        except ConfigError as exc:
            # 「这一版在 ``at`` 这一刻已经不算数了」—— 一条**结果**，不是崩溃。要改的
            # 是调用方读到的那个版本（去读最新那一版），而这句话里点名了 expires_at。
            raise ToolError(str(exc)) from exc
        except StoreError as exc:
            raise store_failure(exc) from exc
        return _result(result, at=at)

    return {"withdraw_claim": withdraw_claim}
