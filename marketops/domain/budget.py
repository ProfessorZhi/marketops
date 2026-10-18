"""预算的值对象与**身份的推导规则** —— 纯的，不碰存储。

分家情况：科目与状态在 ``domain/enums.py``（``BudgetLine`` / ``ReservationState``），
账本的契约在 ``ports/budget.py``，实现分别在 ``adapters/sqlite/ledger.py`` 与
``adapters/memory.py``。本模块放的是**两方都必须一致**的三样：

* :class:`Reservation` —— 一条预留的完整状态；
* :class:`BudgetUsage` —— 一个科目的用量分解；
* :func:`reservation_id` —— 内容寻址的预留 id；
* :func:`check_reservation_id` —— 那条推导的**输出形状**（把一份预留的名字写进
  别处的那一格要过它，理由写在它自己的 docstring 上）。

## 为什么 id 的推导在领域层，而不是各实现自己生成

如果 id 由实现自己造（自增、UUID、行号），那么「同一个 ``(scope, line, key)``
永远得到同一条预留」这条**幂等承诺**就变成了实现的巧合 —— 而 §8 要求它是契约。
放在这里之后，两个实现算出同一个 id，契约测试里那条「``("a:b", "c")`` 与
``("a", "b:c")`` 不是同一条」才在测真的东西；否则它只是在测一个自增计数器。

分隔符是 :data:`marketops.domain.util.SEP`（理由写在那里）：三个分段都可能含 ``:``
（scope 是 ``<profile>:<run>`` 这类形状），而 ``("a:b", "c")`` 与 ``("a", "b:c")``
拼出来必须不同。同一条推导规则在 ``domain/job.py`` 里还会用三次，所以分隔符只有一个
定义，不在各模块各写一份。
"""

from __future__ import annotations

from dataclasses import dataclass

from .enums import BudgetLine, ReservationState
from .util import SEP, sha256_text


def reservation_id(scope: str, line: BudgetLine, idempotency_key: str) -> str:
    """内容寻址：同样的 ``(scope, line, key)`` 永远得到同一个 id。

    让幂等由**主键**保证，而不是靠「写之前先查一下」—— 后者的正确性依赖调用方
    记得查，而且它自己也要在事务里，否则回到同一个竞态。
    """
    return sha256_text(SEP.join((scope, line.value, idempotency_key)))[:32]


#: 预留 id 的形状：:func:`reservation_id` 输出多少位十六进制。
_RESERVATION_ID_LENGTH = 32
_HEX_DIGITS = frozenset("0123456789abcdef")


def check_reservation_id(value: str, field: str) -> str:
    """一个**本账本里**的预留 id，或者抛。

    它与 :func:`reservation_id` 挨着放，因为它是那条推导的**输出形状**：谁把一份
    预留的名字写到别处，写的就该是这里算出来的那个串。

    ## 为什么这一格判形状，而 ``FetchAttempt.budget_reservation_id`` 不判

    那两处看起来是两条相反的规则，其实问的不是同一个问题：**这一格是谁算出来的**。

    * 这里（以及 ``FetchRequest.reservation_id``）记的是**我们自己的账本**里那一条。
      它的来源只有 :func:`reservation_id` 一个，所以它必须长得像它。
    * ``FetchAttempt`` 那一格是**事后凭证**，而 ``SETTLED`` 与
      ``UNKNOWN_PENDING_RECONCILIATION`` 两态下它可以是**供应商账单上的**一个引用
      （对账正是拿它去核），所以那边只管「有没有」、不管「长什么样」——
      完整理由写在 ``domain/fetch_attempt.py`` 的 ``_the_settlement_names_its_reservation``
      上。

    ## 为什么是拒绝而不是放过

    写错这一格的两个真实形态都不是「一眼看出不对」：一个幂等键（``document:<url>``）
    或别处算出来的一个 id。它们落出来的行仍然「说得通」，只是那份预留**在账本里
    不存在** —— 拿这一行去核账的人核不出东西，而报告上也看不出这一行从一开始就不对。

    Raises:
        ValueError: 不是 :data:`_RESERVATION_ID_LENGTH` 个十六进制字符。消息里带上
            收到的那个值：这一格写错时，错的那个值往往**是另一个东西的名字**
            （一个幂等键、一个 URL），而对账的人要看见它才认得出错在哪。
    """
    if len(value) == _RESERVATION_ID_LENGTH and not set(value) - _HEX_DIGITS:
        return value
    raise ValueError(
        f"{field} 必须是本账本里的一条预留 id（{_RESERVATION_ID_LENGTH} 个十六进制"
        f"字符，由 domain/budget.py::reservation_id 算出），得到 {value!r}。\n"
        "它**不是**幂等键、不是 URL、也不是供应商的账单号：这一格要能对着账本里的"
        "那一条查回来，而查不回来的引用会让这一行读起来像「押着一份额度」。"
    )


@dataclass(frozen=True)
class Reservation:
    """一条预留的完整状态。"""

    reservation_id: str
    scope: str
    line: BudgetLine
    units: int
    state: ReservationState
    idempotency_key: str
    created_at: str
    settled_at: str | None = None
    note: str = ""

    @property
    def settles_itself(self) -> bool:
        """``False`` 表示它还扣着额度。"""
        return self.state is not ReservationState.RESERVED


@dataclass(frozen=True)
class BudgetUsage:
    """一个科目的用量分解。

    三个数字**分开列出**，不给一个「已用」汇总：``reserved`` 与 ``spent`` 加在
    一起才是占用额度的量，但它们的处置完全不同 —— 一个等对账，一个已经落地。
    汇总成一个数，会让「有多少笔还没对账」这个问题无法从账本回答。
    """

    line: BudgetLine
    ceiling: int
    spent: int
    reserved: int
    refunded: int

    @property
    def held(self) -> int:
        """占着额度的量。**含** ``reserved`` —— 见 ``ports/budget.py`` 的 docstring。"""
        return self.spent + self.reserved

    @property
    def remaining(self) -> int:
        return max(0, self.ceiling - self.held)


__all__ = ["reservation_id", "check_reservation_id", "Reservation", "BudgetUsage"]
