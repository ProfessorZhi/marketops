"""``BudgetLedger`` 抽象：额度怎么被**扣在起飞之前**。

## 它约束的是哪一件事

§8 把预算写成两条要求，这个接口各承担一半：

* 「Reserve ... budget atomically before attempts」—— ``reserve`` 发生在**调用
  之前**，所以上限约束的是「在途 + 已花」，而不是「已花」。一次正在途中的调用
  不占额度时，「上限 20 次」允许 20 个调用同时起飞，而它们全部会花钱。
* 「the caller cannot silently expand them」—— ``set_ceiling`` 要 ``source``。
  调用方不是不许改上限，是改了要**留名**，而且这个名要能从账本本身读回来。

## 为什么端口上**没有** ``timeout`` / ``expire`` / ``release``

§8 逐字：「在超时或 provider-unknown 时，不要默认释放预算」。这条规则在这里是
一个**不存在的参数**，而不是一句写在文档里的约定：超时之后没有哪条分支能顺手
把额度还回去，因为接口里根本没有那个动作。要退款，调用方得显式写出
``refund(..., reason=...)`` 并给出理由。

把它做成「没有的入口」而不是「有的入口 + 一条注意事项」，是因为前者的默认行为
（什么都不做）恰好就是正确行为 —— 见 ``domain/enums.py::ReservationState``。

## 两个实现，同一份判据

``adapters/sqlite/ledger.py``（跨进程正确，靠 ``BEGIN IMMEDIATE``）与
``adapters/memory.py``（单进程测试替身）。B3 立的判据是**端口在第二个实现出现时
才提取**，这里是两个。

两者都跑 ``tests/test_budget_contract.py`` 的同一组判据：哪个实现先偏离契约，
就在那里红。特别是「``reserved`` 算不算占用」这一条 —— 它只要在一个实现里写错，
超时就等于自动退款。

本模块**不得**出现任何存储引擎或厂商名（``sqlite3`` / ``redis`` …），由
``tests/test_ports_are_contracts_only.py`` 执行。
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from ..domain.budget import BudgetUsage, Reservation
from ..domain.enums import BudgetLine


class BudgetLedger(ABC):
    """额度的两段式账本：**预留 → 结算**。

    返回值类型（``Reservation`` / ``BudgetUsage``）与 id 的推导
    （``domain.budget.reservation_id``）都在领域层，因为它们必须跨实现一致：
    id 若由实现自己造，幂等就退化成该实现的巧合。
    """

    @abstractmethod
    def set_ceiling(
        self, scope: str, line: BudgetLine, units: int, *, source: str
    ) -> None:
        """设定（或调整）一个科目的上限。``source`` 必填，理由见模块 docstring。

        调低到**低于已占用**必须失败：那会让账本立刻处于「已经超支」的状态，
        而超支之后所有后续预留都被拒 —— 一个看起来像 bug 的合法状态。
        """

    @abstractmethod
    def reserve(
        self, scope: str, line: BudgetLine, units: int, *, idempotency_key: str
    ) -> Reservation:
        """在**起飞前**扣住额度。

        Raises:
            BudgetNotConfiguredError: 该科目没有上限。未配置不等于无限。
            BudgetExhaustedError: 预留会越过上限。
            IdempotencyConflictError: 同一个键配了不同的 ``(line, units)``。
        """

    @abstractmethod
    def spend(self, reservation_id: str) -> Reservation:
        """把预留落成实际花费。幂等 —— 重复结算不重复计。"""

    @abstractmethod
    def refund(self, reservation_id: str, *, reason: str) -> Reservation:
        """把预留退回。``reason`` 必填非空。

        **只有**这条路径能把额度要回来，所以没有哪条超时分支能顺手替它做决定。
        """

    @abstractmethod
    def usage(self, scope: str) -> tuple[BudgetUsage, ...]:
        """``scope`` 下每个科目的用量分解，按科目名排序。"""

    @abstractmethod
    def remaining(self, scope: str, line: BudgetLine) -> int:
        """还剩多少额度。

        **没有上限时抛**，不返回 0：返回 0 会让「这个科目没配置」与「这个科目
        已经用完」变成同一个数字，而调用方看到 0 的正常反应是「停下来报告预算
        耗尽」—— 于是忘写配置会伪装成一次正常的预算耗尽。
        """

    @abstractmethod
    def open_reservations(self, scope: str | None = None) -> tuple[Reservation, ...]:
        """还没结算的预留 —— **对账的入口**，按创建时间排序。

        不自动退款意味着一定会有停在 ``RESERVED`` 的行，那么必须有一个地方能
        把它们列出来，否则「有多少额度悬着」只能靠翻库。
        """

    @abstractmethod
    def get(self, reservation_id: str) -> Reservation | None:
        """按 id 取一条预留；不存在时返回 ``None``。"""


__all__ = ["BudgetLedger"]
