"""一次折入**归属于哪一轮 Run** —— 两个形状，以及它们为什么是域层的。

## 要回答的是什么

``close_run`` 收一份 ``folded``，而那是**调用方递上来的一份主张**。它今天收的是一串
**池子句柄**（e4 收窄的那一段写在 ``application/run_reconciliation.py``），而在账本
落地**之前**盘上没有第二处能佐证它，于是一条走得通的假账是：**折了却没记，然后在结束
时报一份空的 ``folded``** —— 账照旧平，池子却变大了。而「池子变大」正是 §B17 条件 2
唯一要发现的那件事。

让主张不可伪造，需要「哪一轮折了哪一行」这件事**落盘**。落盘需要两个形状：

* :class:`FoldAttribution` —— **交上去的那一份**：「这一次池子的写，归属于哪一轮的
  哪一行回执」。它由调用方给，里面没有一个字是盘上读来的。
* :class:`FoldReceipt` —— **落盘之后读回来的那一份**：归属 + 折进池子的那个句柄 +
  记账时刻。句柄和时刻都不是调用方给的（前者是池子算出来的那一行，后者是写这一笔
  的时钟）。

## 为什么这两个形状住在 ``domain/``，而不是 application 或 ports

因为**端口要引用它们**：``ports/pool.py`` 的 ``upsert_asset`` 收一个归属，
``ports/runs.py`` 的 ``folds_of`` 吐一串凭据。端口能引用的只有域层 —— 让端口反过来
引 ``application/promotion.py`` 的类型是反的，会成环（application → ports 是既有的
方向）。

而这件事**没有**连累 ``PromotionOutcome`` / ``PromotionDisposition``：它们留在
``application/promotion.py`` 原地不动。理由是这一层要记的是**池子那一行落了盘**，
不是「某一行的处置最终是什么」—— 后者是折入动作的语义，只有 application 知道。
把账本的范围收窄到「池子被写过」，分层环就自己解开了。

## 记账的口径：**池子的写**，不是**处置**

这条口径是承重的，因为它决定了「账上有」与「这一轮折过它」是不是同一件事：

``reconcile_run_delta`` 收的是**一串句柄**（e4 起），它不再看 ``PromotionOutcome``；
而在 ``_promote_one`` 里 **``asset`` 非空的行不全是 ``PROMOTED``**：账号那一行没写
进去的那些是 ``REJECTED``，而它们的 ``asset`` 是**有的**（池子那一行确实落了盘）。
所以「处置等于 rejected」推不出「池子没被写」，而按处置记账的账本会把这一类**漏掉** ——
漏掉的那一条正是假账本身。唯一正确的口径是**在池子那一行落盘的同一个事务里记一笔**。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class FoldAttribution:
    """**交上去的**归属声明：这一次池子的写，属于哪一轮的哪一行回执。

    三个格子都必填、且都有话说：

    * ``run_id`` —— 哪一轮。它是这件事的全部意义所在。
    * ``receipt_id`` —— 哪一份来源回执。与 ``ordinal`` 一起构成**重放判据**：
      同一个 ``(run_id, receipt_id, ordinal)`` 再来一次是重放，不是第二笔。
    * ``ordinal`` —— 回执里的第几行。一份回执里两行可以折进**同一块**场地
      （没有来源编号、名字与地点又相同的那两行），所以句柄不能单独当重放键 ——
      它会把两条不同的行当成同一条。

    **没有 ``at``**：记账时刻由写这一笔的那一层取，不由交归属的人给 —— 与
    ``RunStore.admit`` 的 ``at`` 同一条纪律（一条要能被逐条断言的规则不该自己去问
    现在几点），但方向相反：这里是**存储**记下「这一笔是什么时候落下的」。
    """

    run_id: str
    receipt_id: str
    ordinal: int

    def __post_init__(self) -> None:
        for field in ("run_id", "receipt_id"):
            value = getattr(self, field)
            if not value:
                raise ValueError(
                    f"FoldAttribution.{field} 是空串。\n"
                    "归属的两个身份格都是承重的：空的 run_id 会让这一笔落在一个"
                    "「不属于任何一轮」的桶里（而那与「没有归属」在盘上同形），"
                    "空的 receipt_id 会让重放判据退化成「同一个序号折过两次」。"
                )
        if self.ordinal < 0:
            raise ValueError(
                f"FoldAttribution.ordinal 是 {self.ordinal}，而回执的行号从 0 起。\n"
                "它进重放判据，所以负数不是一个「还没设」的占位 —— 它是一个永远不会"
                "与真实行号相撞的键，于是同一条回执的每一次重放都会被读成新的。"
            )


@dataclass(frozen=True)
class FoldReceipt:
    """**落盘之后读回来的**那一笔：这一轮折进池子的这一行。

    ``venue_asset_id`` 是池子那一行的句柄（``upsert_asset`` 折出来的那一行），
    ``at`` 是记账时刻。两个都不是交归属的人给的。

    **它证明的是「池子那一行落了盘」**，而且是在**同一个写事务**里落下的 ——
    这正是它能当权威的原因。见模块 docstring 的「记账的口径」一节。
    """

    run_id: str
    receipt_id: str
    ordinal: int
    venue_asset_id: str
    at: datetime


__all__ = ["FoldAttribution", "FoldReceipt"]
