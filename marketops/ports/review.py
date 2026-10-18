"""Review Inbox 的存储端口 —— 两件人工对象的读写（§12）。

本模块**不得**出现任何厂商名，也不得 import 任何存储引擎（sqlite3 / redis / pymongo …）
—— 与 ``ports/store.py`` 逐字同一条规矩：一个 import 了 sqlite3 的端口看起来完全正常，
它照样能被 ABC 描述、照样能被注入，它只是偷偷变成了唯一实现。

## 两个方法族的区别，以及为什么 ``open_case`` 不是 ``put_case``

* ``open_case`` 是**由问题驱动的**：它的输入是一个「这里需要人看一眼」的判定，它的承诺
  是「同一个问题不新开第二件」。调它的人不知道案件长什么样，也不该知道 —— 它由
  ``(工作区, 场景, 原因, 主体)`` 算出来。
* 状态的迁移（裁决）走 ``record_resolution``，它**不做去重**，因为它记的是
  一件**已经发生的事**（见 ``ports/decisions.py`` 里那段
  「内容寻址挡的是『同一件事被记成两条』，挡不住『同一件事被做两次』」）。

把两者合成一个 ``put_case(case)`` 会让去重落在调用方手里：调用方必须先算出那个 id 才能
构造对象，而「算出正确的 id」正是这一层要替它守住的事。

## 认领为什么是**第三个**动词，而不是 ``record_resolution`` 的一种

``claim`` 改的是**当前状态**（``owner`` 那一格），不追加历史 —— 它进不了
``human_resolution`` 那张只增不改的表。而它与 ``record_resolution`` 共用同一条并发判据
（``expected_version``）：两个人同时领走一件时，后写的那个会把前一个的认领静默吃掉，
而他的屏幕上还写着「我在做这一件」。

它也不是 ``put_case``：那会让「谁在什么时候领走了哪一件」变成一次可以由调用方随手
构造的整行覆盖。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime

from ..domain.review import HumanResolution, ReviewCase, ReviewState

__all__ = ["CaseAdmission", "ReviewCaseStore"]


@dataclass(frozen=True)
class CaseAdmission:
    """``open_case`` 的结果。**两个字段都必要**，理由见 ``ReviewCaseStore.open_case``。"""

    #: 盘上那一件 —— 新开的，或**已经存在的那一件**。
    case: ReviewCase
    #: 这一次调用**是不是**它被开出来的那一次。
    opened: bool

    @property
    def state(self) -> ReviewState:
        return self.case.state


class ReviewCaseStore(ABC):
    """待办与裁决的持久化。"""

    @abstractmethod
    def open_case(self, case: ReviewCase) -> CaseAdmission:
        """把一件待办放上盘。同一个问题第二次调用**不新开、不覆盖**。

        返回 ``opened=False`` 是「它已经在盘上了」。那一个返回值有两个不同的来路，而
        它们的处置不同：

        * 那件还在**未决**：正常去重，调用方什么都不用做。
        * 那件已经**裁决过**：这一格**不重开它** —— 重开是人做的
          （``ResolutionOutcome.REOPEN``），而由系统自己重开等于把人的一句「不值得处理」
          在下一个批次里静默作废。调用方必须把这一件事**报出来**（工具回执里带
          ``opened`` 与 ``state``），而不是当作「已经问过了」丢掉。

        后一种是这一格唯一能出的静默错误，而它**不报错**：一件被吞掉的问题与一件
        从来不存在的问题在报告上完全同形。所以返回值里两格都必要 —— 只看 ``case``
        读不出「这一次是不是它被开出来那一次」。

        Raises:
            StoreError: 盘上那一件的 id 与内容对不上（内容寻址被绕过）。
        """
        raise NotImplementedError

    @abstractmethod
    def get_case(self, case_id: str) -> ReviewCase | None:
        """按 id 读一件。没有就返回 ``None``（``None`` 是「没有」，不是「读失败」）。"""
        raise NotImplementedError

    @abstractmethod
    def list_cases(self) -> tuple[ReviewCase, ...]:
        """这个部署的全部待办，**未决的在前、早的在前**。

        顺序是契约的一部分：报告的第一行必须是「现在最该看的那一件」，而一个由
        存储层随便给的顺序会让两次调用给出两个「第一件」。
        """
        raise NotImplementedError

    @abstractmethod
    def claim(self, case_id: str, *, by: str, at: datetime, expected_version: int) -> ReviewCase:
        """把一件 ``OPEN`` 的待办认领走，返回**新的**那一件。

        ``expected_version`` 与 ``record_resolution`` 逐字同一条判据、同一个理由：
        「先读到的那一版还作不作数」由**存储**在写事务里比，而比较在事务之内。

        Raises:
            ReviewConflictError: 那一件不在盘上、``version`` 已经不是
                ``expected_version``、或者它现在不是 ``OPEN``（已经有人在做了、
                或者已经关了 —— 见 ``domain.review.claim``）。
        """
        raise NotImplementedError

    @abstractmethod
    def record_resolution(
        self, resolution: HumanResolution, *, expected_version: int
    ) -> ReviewCase:
        """追加一条人的决定，并把案件推进到它的结果上。**一次写事务。**

        ``expected_version`` 是人在自己屏幕上看到的那个 ``case.version``。与盘上不一致
        时整次拒绝（陈旧 attempt 不覆盖新结果，``docs/ARCHITECTURE.md:51``）——
        两个人同时裁决同一件时，最后一个写的赢而**两边都以为自己成功了**，是这一格
        唯一挡不住就会发生的事。

        ## 它**同时**欠下下一步，而且和上面那两行在同一个写事务里

        架构 §9 逐字：「提交裁决验证可信 principal、权限、``expected_version`` 与幂等键；
        过时案不写回新事实。**裁决后自动重评受影响对象**，保留历史和恢复进度。」
        这一格就是那句「裁决后」，所以它落账的那一行与欠下的那一行**同生共死**：

        * 写在拒绝路径之前 —— 一次被拒的裁决会给一件**没发生的事**留下一行欠账，
          于是永远有一次重评在等一个不存在的决定；
        * 写在事务之后 —— 进程在两次写之间死掉时，决定在盘上而欠账不在，于是
          「这件裁决跑完之后还有一次重评」**永久丢失**，而流水账上完全看不出来。

        **欠什么由领域决定，不在这里**：``domain/continuation.py::intents_for_resolution``
        给出这一次裁决要欠的那几行（策略在领域，事务在实现）—— 与 ``ports/jobs.py``
        的 ``admit`` / ``settle`` 逐字同一条分工。所以一个「只推进案件、什么都不欠」的
        实现**仍然是错的**，即使它上面的每一条判据都对。

        Raises:
            ReviewConflictError: 那件案件的 ``version`` 已经不是 ``expected_version``，
                或者它不在盘上，或者那条决定与案件当前状态不相容。
            StoreError: 库打不开、版本不匹配、行读不成。
        """
        raise NotImplementedError

    @abstractmethod
    def list_resolutions(self, case_id: str) -> tuple[HumanResolution, ...]:
        """一件案件的**全部**决定，早的在前（同刻按 ``resolution_id``）。

        只增不改的那一半：案件那一行会被覆盖，这一串不会被。§12 的恒等式与
        「谁提议的、谁批的」都只能从它读出来。
        """
        raise NotImplementedError
