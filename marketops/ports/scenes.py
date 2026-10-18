"""``SceneStore`` / ``MaintenanceStore``：立项与持续经营的持久化契约。

## 为什么是**两个**端口

它们守的失败不同，而且**写频率差一个数量级**：

* ``SceneStore`` 守的是「规则」。它一次写入、多次读取，写入必须留下审批痕迹，
  冲突必须显性失败。写得少，读得多，一旦写错，下游每一处判定都错。
* ``MaintenanceStore`` 守的是「这一格上一次是什么时候看的」。它高频写入、
  按主体逐格读，最要命的失败不是「写错」而是「**做了两次**」。

合成一个端口，那个高杠杆的 CAS 会被淹没在每日几千次的回执写里 ——
而它恰恰是最需要被单独看清楚的那一条。

## 版本是 append-only，状态是 CAS

``put_version`` 与 ``put_record`` 的形状不一样，这不是不一致，是**两个对象的
性质不同**：

* 版本一经写下就不再变。同一个 ``(scene_id, version)`` 再写一次，内容相同就是
  幂等，内容不同就是**有人手工改过 id** —— 必须报错。这与
  ``EvidenceStore.put_evidence`` 是同一条纪律。
* 状态每次 pause / resume / 激活都在变，所以它有 ``revision``，写的时候必须
  带上「我读到的是第几版」，不匹配就整次拒绝。这就是
  ``put_record(..., expected_revision=...)`` 的全部内容。

## ``expected_revision=None`` 的意思是「必须不存在」

不是「随便写」。一个用 ``None`` 表示「不做检查」的实现，会让「这已经是第一次
创建了」与「我以为这是第一次创建」变成同一件事 —— 而后者正是一次并发重复立项。
两个实现都必须把它当成「插入，冲突即失败」。

## 端口上**没有**的东西

* 没有 ``delete``。Scene 是记录：归档是一个状态（``ARCHIVED``），不是一次删除。
  删掉一个被报告引用过的场景，等于让那份报告指向一个不存在的东西。
* 没有 ``next_due`` 的独立列。到期时刻是 ``观察时刻 + 该 aspect 的节奏`` 算出来的，
  存一份就等于有两个真相来源 —— 而它们不一致时，读的人无从判断哪个对。
* 没有按 dedupe_key 单独读回执的入口。回执只从它所属的 Scene 出发被列举
  （``claims``）：它不是独立存在的东西。

本模块**不得**出现任何厂商名，也不得 import 任何存储引擎。判据由
``tests/test_ports_are_contracts_only.py`` 执行。
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from ..domain.maintenance import (
    ChangeSignal,
    ClaimOutcome,
    MaintenanceClaim,
    Observation,
)
from ..domain.scene import SceneRecord, SceneVersion


class SceneStore(ABC):
    """Scene 的版本与当前状态。"""

    @abstractmethod
    def put_version(self, version: SceneVersion) -> None:
        """写入一版。同 ``(scene_id, version)`` 且内容相同 → 幂等；不同 → 报错。"""

    @abstractmethod
    def get_version(self, scene_id: str, version: int) -> SceneVersion | None:
        """读一版。不存在返回 ``None`` —— **不是抛异常**。"""

    @abstractmethod
    def versions(self, scene_id: str) -> tuple[SceneVersion, ...]:
        """按版本号升序列出全部版本。"""

    @abstractmethod
    def put_record(self, record: SceneRecord, *, expected_revision: int | None) -> None:
        """写入当前状态。

        ``expected_revision=None`` 表示**记录必须不存在**（首次创建）；
        给一个整数表示「我读到的是第 ``n`` 版」，不匹配即
        :class:`~marketops.domain.errors.SceneConflictError`，且**一个字节都不写**。
        """

    @abstractmethod
    def get_record(self, scene_id: str) -> SceneRecord | None:
        """读当前状态。不存在返回 ``None``。"""

    @abstractmethod
    def records(self, *, workspace: str | None = None) -> tuple[SceneRecord, ...]:
        """列出场景，按 ``scene_id`` 升序。``workspace`` 为 None 时列全部。

        **给 None 的调用方必须自己已经做过授权判定。** 这个参数存在是为了让
        控制面能列出「我这一版的场景」，不是为了让越权读有一个入口 ——
        判据在 ``security/workspace.py``，不在存储层。
        """


class MaintenanceStore(ABC):
    """观测、变更信号与复核回执。"""

    @abstractmethod
    def put_observation(self, observation: Observation) -> None:
        """记下一次观测。同一 ``(主体, aspect, 时刻, 来源)`` 重复写入是幂等的。

        **同一格不同时刻的观测不去重**：它们是「它当时是什么样」的不同事实，
        合并等于让「变了」这件事从记录里消失。
        """

    @abstractmethod
    def observations(
        self, *, workspace: str, subject_kind: str | None = None
    ) -> tuple[Observation, ...]:
        """按工作区列出观测，按 ``(主体, aspect, 时刻)`` 升序。"""

    @abstractmethod
    def put_signal(self, signal: ChangeSignal) -> None:
        """记下一条变更信号。同 ``(主体, aspect, 时刻)`` 幂等。"""

    @abstractmethod
    def signals(self, *, workspace: str) -> tuple[ChangeSignal, ...]:
        """列出变更信号，按信号时刻升序。"""

    @abstractmethod
    def claim(self, claim: MaintenanceClaim) -> ClaimOutcome:
        """认领一件到期工作。返回 ``(回执, 是不是我写的)``。

        已经有一条**同 dedupe_key** 的回执时，行为由它的状态决定：

        * ``DONE`` —— 返回**已有的那条**，不新建、不覆盖，``won=False``。
          这是「重启或重复 tick 不得重复计费」的全部内容。
        * ``CLAIMED`` —— 同样返回已有的那条，``attempt`` 不变，``won=False``。
          一个还在跑的格子被第二次认领，是本次调用**没有**拿到它。
        * ``FAILED`` —— 写入一条新的（``attempt`` 加一，状态回到 ``CLAIMED``，
          清掉上一次的失败原因），``won=True``。**换一条记录而不是就地改**，
          因为失败过几次是运维要看的数，就地改会让它永远是 1。

        ``won`` 由存储回答，**不能由调用方比对 ``claimed_at`` 推出来** ——
        两个驱动传同一个 ``at`` 时那个推法会给出两个 ``True``，而 ``now`` 在
        这一层是参数，不是时钟，所以那不是假想的巧合。见 ``ClaimOutcome``。
        """

    @abstractmethod
    def finish(self, claim: MaintenanceClaim) -> None:
        """把一条回执置为终态（``DONE`` 或 ``FAILED``）。

        只接受 ``CLAIMED`` 之外的状态；对一条 ``CLAIMED`` 的**旧**回执再调一次
        是幂等的（同一个 dedupe_key 已经是终态就什么都不做）。
        """

    @abstractmethod
    def claims(self, scene_id: str) -> tuple[MaintenanceClaim, ...]:
        """按 ``(认领时刻, 主体, aspect)`` 升序列出该 Scene 的回执。"""


__all__ = ["SceneStore", "MaintenanceStore"]
