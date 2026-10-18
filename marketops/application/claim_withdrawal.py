"""**撤一条已批准的说法。** 承重点 3 的落点：受影响集合由**证据**算出来，而两半
写下去这件事**全成或全不成**。

## 三句话逐字来自那条 summary，各有各的落点

「**所有还没发出去的**草稿进 ``hold``」——「还没发出去」不是猜的
（:func:`_sent_keys`）：判据是 ``OutboundAttempt.draft_id`` 与
``draft_version`` 两格，配上 ``domain/outreach.py`` 自己那两个属性
（``sent_or_unknown`` 与 ``SENT_OR_UNKNOWN`` 那个四元集合，它的 docstring 逐字
写着 ``FAILED`` 不在里面而 ``DELIVERY_UNKNOWN`` 在里面）。在这一层再写一遍那
四条判断就是同一句话的第二份实现，而两份分岔的那天，「哪几封已经出去过」这一格
会有两个答案。

「**无关的活一格不变**」——这一层只写两类行：那条说法的一个新版本，以及受影响
草稿各自的新版本。**没有第三个写点**，也没有一次全表扫描后的批量更新。

「受影响集合由证据算出来而**不是关键词匹配**」——过滤发生在
``adapters/sqlite/claim_withdrawal.py::drafts_citing`` 里，判据是
``OutreachDraft.claim_ids`` 那个**结构化引用**。关键词匹配会把两件事同时做错
（改写过而没引用的被误伤、引用了却没提到字面的被漏掉），而两者在报告上都表现为
「撤干净了」。

## 「全成或全不成」是**一个**调用，不是一个 try

两半（那条说法的新版本、那几份草稿的新版本）是同一次
:meth:`~marketops.ports.claim_withdrawal.ClaimWithdrawalStore.withdraw` 的入参。
端口那一份的 docstring 逐字写了为什么不能拆成两次调用：拆开之后两次之间有一个
「说法已经撤了、草稿还压着」的窗口，而那个窗口里发出去的东西，事后没人能把它与
「本来就不该发」的那一半分开。

把两半拆开的写法在本层是**写不出来**的：这一层只调一次 ``withdraw``。所以那条
变异（把 hold 那一步挪到事务外面）要去改**适配器**，而不是这一层 —— 判据见
``tests/test_claim_withdrawal_store_contract.py`` 里那条「半途失败之后盘上要么
两半都在、要么两半都不在」。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from ..domain.errors import ConfigError
from ..domain.outreach import ApprovedClaim, OutboundAttempt, OutreachDraft
from ..domain.outreach import DraftState
from ..domain.util import to_utc
from ..ports.claim_withdrawal import ClaimWithdrawalStore

__all__ = ["WithdrawalResult", "withdraw_claim"]


@dataclass(frozen=True)
class WithdrawalResult:
    """一次撤回的**实测结果**：压了哪几份、放过了哪几份、各为什么。"""

    #: 写下去的那一版说法（``expires_at`` 落在撤回那一刻）。
    retirement: ApprovedClaim
    #: 真的进了 ``hold`` 的那几份草稿的**新版本**（``state=HELD``）。
    held: tuple[OutreachDraft, ...]
    #: 没有动的那些，**每一份带一句为什么**。``(草稿, 原因)``。
    #:
    #: 它是一格而不是一个数：一个只报「放过了 3 份」的回执，读的人分不清那三份
    #: 是「已经出去了」（处置：去对账那条链，必要时人工联系）还是「本来就压着」
    #: （处置：什么都不用做），而两者的下一步相反。
    left_alone: tuple[tuple[OutreachDraft, str], ...]

    @property
    def held_ids(self) -> tuple[str, ...]:
        return tuple(draft.draft_id for draft in self.held)


def _sent_keys(attempts: tuple[OutboundAttempt, ...]) -> dict[tuple[str, int], str]:
    """**已经出去过**的那些 ``(draft_id, draft_version)`` → 一句出处。

    值那一格是「凭什么说它出去了」：写到回执里，读的人可以直接去看那一条尝试。
    一个只回一个集合的实现，让回执里那句「已经发过了」变成一句没有出处的断言。

    按 ``(draft_id, draft_version)`` 而不是只按 ``draft_id``：一次尝试是**针对
    某一版**的。一份改写过的新版本没有出去过，按 id 判会让它逃过这一次撤回。
    """
    sent: dict[tuple[str, int], str] = {}
    for attempt in attempts:
        if not attempt.sent_or_unknown:
            continue
        key = (attempt.draft_id, attempt.draft_version)
        sent.setdefault(
            key,
            f"投递尝试 {attempt.send_key!r} 的状态是 {attempt.state.value}"
            f"（{attempt.started_at.isoformat()}）",
        )
    return sent


def _held_revision(draft: OutreachDraft) -> OutreachDraft:
    """这一份草稿进 ``hold`` 的**新版本**。

    走 ``model_validate`` 而不是 ``model_copy``：``model_copy`` **不校验**，于是
    ``supersedes`` / ``draft_version`` 那条「必须是上一版」的判据在它这里根本不
    跑 —— 而它正是「出的是新的一版」这件事的判据。本仓为这一点写过一次
    （``SceneRecord`` 上那条注释：``model_copy`` 不校验，所以时间字段各自过一遍
    ``to_utc``）；这里选择整个对象再过一次构造器，把那类问题一并盖住。
    """
    return OutreachDraft.model_validate(
        {
            **draft.model_dump(mode="python"),
            "draft_version": draft.draft_version + 1,
            "supersedes": draft.draft_version,
            "state": DraftState.HELD,
        }
    )


def withdraw_claim(
    *,
    workspace: str,
    claim: ApprovedClaim,
    at: datetime,
    store: ClaimWithdrawalStore,
) -> WithdrawalResult:
    """撤一条已批准的说法。**一次事务**：那条说法的新版本 + 那几份草稿的新版本。

    ``workspace`` 是必填关键字而不是从 ``claim`` 上读的：``ApprovedClaim``
    **没有** workspace 那一格（``outreach_claim`` 表的主键是
    ``(claim_id, revision)``，上面也没有工作区），而受影响的草稿是**按工作区**
    枚举的。所以这一格只能由调用方给 —— 而它应当取自令牌声明。
    """
    at = to_utc(at)

    if not claim.is_live_at(at):
        raise ConfigError(
            f"说法 {claim.claim_id!r} 第 {claim.revision} 版在 {at.isoformat()} "
            f"这一刻已经不算数了（expires_at={claim.expires_at}）。\n"
            "  撤回一条已经不在的说法是一次**没有效果的写**：它会在盘上多出一版"
            "「更正」而那句话本来就已经不生效了。它出现的场合通常是调用方读到了"
            "一个过期的版本 —— 处置是重新读一遍最新的那一版，不是撤这一版。"
        )

    retirement = ApprovedClaim.model_validate(
        {
            **claim.model_dump(mode="python"),
            "revision": claim.revision + 1,
            # 说法的退场是**追加一版**：``expires_at`` 落在撤回这一刻。就地改写会让
            # 「当时批准的那句话」在盘上变成另一句（``outreach_claim`` 是
            # ``(claim_id, revision)`` 的表）。
            "expires_at": at,
            # 而期限这一格**不足以**说清这一版与一次普通的再批准的区别：两者在盘上
            # 都是「最新版不活」。``retired`` 是那个区别本身 —— 见
            # ``domain/outreach.py::ApprovedClaim`` 的「退场与过期是两个事件」。
            "retired": True,
        }
    )

    sent = _sent_keys(store.attempts(workspace=workspace))
    citing = store.drafts_citing(workspace=workspace, claim_id=claim.claim_id)

    held: list[OutreachDraft] = []
    left_alone: list[tuple[OutreachDraft, str]] = []
    for draft in citing:
        if draft.state is DraftState.HELD:
            left_alone.append(
                (
                    draft,
                    "它已经是 held 了。再压一版是**同一句话的第二个版本**，而"
                    "「压了几次」这个数会因此虚高 —— 处置与这一条判据无关，"
                    "它已经不在待发队列里了。",
                )
            )
            continue
        why = sent.get((draft.draft_id, draft.draft_version))
        if why is not None:
            left_alone.append(
                (
                    draft,
                    f"这一版**已经出去过**：{why}。"
                    "  把已经出去的东西标成 hold 是一种谎 —— 它没被拦下来。"
                    "这一份的处置在那条投递链上（对账 / 必要时人工联系），"
                    "而不在这里。",
                )
            )
            continue
        held.append(_held_revision(draft))

    # **唯一**一次写。两半是这一次调用的入参，所以「全成或全不成」不是这一层的
    # 自觉，是它没有第二种写法。
    store.withdraw(retirement=retirement, drafts=tuple(held))

    return WithdrawalResult(
        retirement=retirement,
        held=tuple(held),
        left_alone=tuple(left_alone),
    )
