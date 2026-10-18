"""``ScheduleStore`` / ``ScheduleAction``：调度声明怎么落盘，以及到点了谁去做那件事。

## 为什么是**两个**契约，而且第二个不是 ABC

它们守的失败不同，边界也不同：

* :class:`ScheduleStore` 是**状态**。它的三处判断（首次声明的 ``expected_revision=None``、
  改版时的 CAS、fire 那条主键）全部是「有人先写了」这一类，而它们的处置是重读再决定。
* :class:`ScheduleAction` 是**执行**。它守的失败是「这件事做了两次」——
  §8 ``:322`` 逐字「空队列不调用 LLM」，而这句话只有在「动作是整个用例里**唯一**
  会花钱的那一步」时才守得住。

第二个用 ``Protocol`` 而不是 ``ABC`` 是有理由的：它的实现天然是一个**函数**
（受理一轮 Run 的那条路径是一段编排，不是一个有状态的对象），而一个要求
``class X(ActionBase)`` 的契约会让每一个调用点为了满足一层继承而包一个空壳。
第一个用 ``ABC``：它有两个真实实现（SQLite 与内存替身），而它们的形状必须被
同一份契约钉住。

## 端口上**没有**的东西，每一条都是一个被挡掉的写法

* 没有 ``delete_definition``。调度是声明：关掉是一条新版本（``enabled=False``），
  不是一次删除 —— 删掉一条被 fire 记录引用过的调度，等于让那条 fire 指向一个
  不存在的东西。``ScheduleDefinition.controlled`` 是这条的落点。
* 没有 ``next_due`` 的独立写入口。到期时刻是声明的一格，而它能被单独改写
  意味着「声明一版一行」这条纪律有一个后门：一次不经版本的推后会与一次改范围
  在盘上留下同样的痕迹。
* 没有 ``mark_fired`` 或任何「就地更新 outcome」的入口。``schedule_fire`` 是
  **write-once** 的（``db.py`` 那段 DDL 逐字「主键就是幂等键」）：允许改 outcome
  会让「重放回同一个结果」这句话失去载体 —— 重放读到的会是最后一次改写的结果。
* 没有按 ``scope_ref`` 找调度的入口。本片没有一个调用方需要问「谁在看这个对象」，
  而给一个尚不存在的需求先开的口子，会被下一个人当成一条可用的路。

本模块**不得**出现任何厂商名，也不得 import 任何存储引擎。判据由
``tests/test_ports_are_contracts_only.py`` 执行。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from ..domain.scheduling import ActionReport, ScheduleDefinition, ScheduleFire

__all__ = ["FireWrite", "ScheduleAction", "ScheduleStore", "ScheduleWrite"]


@dataclass(frozen=True)
class FireWrite:
    """一次 fire 行写入的结果。两个字段都必要。

    ``fire`` 是**盘上那一行**，不是入参 —— 理由与 ``ports/views.py::ViewWrite``
    逐字相同：直接回传入参的实现在「另一次写入已经落过同一行」这件事上什么也
    证明不了，而它读起来与真正的幂等一模一样。

    ``recorded`` 在 :meth:`ScheduleStore.claim_fire` 上是「这一次抢到了，去跑动作」，
    在 :meth:`ScheduleStore.settle_fire` 上是「这一次把结论写上了」。两处的
    ``False`` 都意味着「有人先做了」，而**处置都是同一个**：停手，读盘上那一行。
    """

    fire: ScheduleFire
    recorded: bool


@dataclass(frozen=True)
class ScheduleWrite:
    """一次声明写入的结果（``ports/views.py::ViewWrite`` 的兄弟）。

    ``revision_written=None`` = 盘上当前那一版与这一次要写的一字不差，**没有新版本
    落盘**；``revision_written=1`` = 这一次把它建了出来。两句不同的话：
    「你刚才那次成功了」与「你第二次点的时候什么都没发生」。
    """

    definition: ScheduleDefinition
    revision_written: int | None


class ScheduleStore(ABC):
    """调度声明与 fire 记录的持久化契约。"""

    # -------------------------------------------------------------- 声明

    @abstractmethod
    def put_definition(
        self, definition: ScheduleDefinition, *, expected_revision: int | None
    ) -> ScheduleWrite:
        """写下一版声明。

        ``expected_revision=None`` 表示**这条调度还不存在**（首次声明，因此
        ``definition.schedule_revision`` 必须是 1）；给一个整数表示「我读到的是
        第 ``n`` 版」，而库里当前最新的一版不是 ``n`` 时整次拒绝、**一个字节
        都不落盘**（:class:`~marketops.domain.errors.ScheduleConflictError`）。

        两件事都要成立，而第二件是那个 CAS：**同一份声明改过之后，写的人必须
        先看见那一版**。只检查「新版本比旧的大」的实现在两个写者并发时会让后到
        的那一个把先到的那一个**跳过**——而它的表现不是冲突，是版本号跳了一格。

        内容一字不差时**不产生新版本**（``revision_written=None``）：同一条声明
        再写一遍不是一次改动，而一个每次都产生新版本的实现会让「有人反复点保存」
        在盘上留下几十版，于是「这条调度上次改是什么时候」读成一个刚刚的时刻 ——
        而 ``revision_reason`` 那一格的用处正是回答那个问题。
        """
        raise NotImplementedError

    @abstractmethod
    def get_definition(
        self, schedule_id: str, schedule_revision: int
    ) -> ScheduleDefinition | None:
        """读指名的某一版。不存在返回 ``None`` —— **不是抛异常**。"""
        raise NotImplementedError

    @abstractmethod
    def definitions(self, *, workspace: str | None = None) -> tuple[ScheduleDefinition, ...]:
        """列出每一条调度的**当前那一版**，按 ``schedule_id`` 升序。

        历史版本不在这里 —— 这一条回答的是「这个部署现在有哪些调度、它们各自长
        什么样」，而一份把四版历史混在一起的清单读起来只是「调度多了一些」。

        ``workspace`` 为 None 时列全部。**给 None 的调用方必须自己已经做过授权
        判定**（与 ``ports/scenes.py::records`` 逐字同一条）。
        """
        raise NotImplementedError

    @abstractmethod
    def due(self, *, at: datetime, workspace: str | None = None) -> tuple[ScheduleDefinition, ...]:
        """这一刻真的该点火的那些（当前那一版），按 ``(next_due_at, schedule_id)`` 升序。

        这个方法是 ``schedule_definition_by_due`` 那条索引存在的**理由**：trigger
        driver 每次醒来问的第一句话就是它，而那句话必须是低成本的（§8 ``:322``
        逐字「先做低成本 due check」）。让调用方去 ``definitions()`` 里自己筛，
        会让「低成本」这件事只活在注释里，同时把一次全表读变成每一次醒来的第一步。

        判据与 :meth:`~marketops.domain.scheduling.ScheduleDefinition.due` **逐字
        同一条**，而两处各写一遍的那天，「清单说它该醒了、驱动说它不到期」会是
        一份没有任何一处会红的报告。
        """
        raise NotImplementedError

    # -------------------------------------------------------------- fire

    @abstractmethod
    def claim_fire(self, fire: ScheduleFire) -> FireWrite:
        """**抢下一次 fire**。这是「重复 fire 幂等」的唯一载体，而它必须发生在
        动作跑之前。

        调用方在这里交上来的是一条 ``outcome=PENDING`` 的占位行（见
        :class:`~marketops.domain.scheduling.FireOutcome` 那两段）。三条出口：

        * 主键 ``(schedule_id, schedule_revision, fire_id)`` 上没有行 → 写入，
          ``recorded=True``。**只有拿到这个结果的调用方去跑动作** —— 这就是
          「幂等来自插入失败」的全部内容。一个「先查一查再决定」的实现在两个调用方
          并发时各自都查到「没有」，然后两条都跑一遍动作：两轮研究，一次也没被挡住。
        * 已经有行 → 什么都不写，``recorded=False``，回**盘上那一行**（可能是另一
          个结论，也可能还是一个占位）。调用方据此停手：重放不重跑动作。
        * 已经有行、而这一次交上来的占位行与它**不是同一次 fire**（``workspace`` /
          ``fired_at`` 不同）⇒ :class:`~marketops.domain.errors.IdempotencyConflictError`。
          同一个 ``fire_id`` 配两条不同的 fire 只可能是有人改过 id，而安静地留下
          第一条会让「重放回同一个结果」变成「回第一次那个」。
        """
        raise NotImplementedError

    @abstractmethod
    def settle_fire(self, fire: ScheduleFire) -> FireWrite:
        """把一次 fire 的结论写上。**只在占位行上生效，一次为限。**

        这是一次 CAS，判据是 ``WHERE outcome = 'pending'`` 加 ``rowcount``：

        * 盘上那一行还是占位 ⇒ 写上结论，``recorded=True``；
        * 盘上那一行**已经结了** ⇒ 什么都不写，``recorded=False``，回盘上那一行。
          这就是「一条 fire 的结论只写一次」—— 一个无条件 ``UPDATE`` 的实现会让
          第二次写入把第一次的结果抹掉，而「重放回同一个结果」这句话在那一刻起
          就只是一句注释。

        ``fire.pending`` 为真时抛 ``ValueError``（``domain`` 那一侧
        :meth:`ScheduleFire.settled` 已经先答过它）：占位不是一个结论。
        """
        raise NotImplementedError

    @abstractmethod
    def get_fire(
        self, schedule_id: str, schedule_revision: int, fire_id: str
    ) -> ScheduleFire | None:
        """读一次 fire。不存在返回 ``None``。

        **三格缺一不可。** 只按 ``(schedule_id, fire_id)`` 查的实现在「声明改过一版、
        同一个 ``fire_id`` 再点一次」时会读到上一版那一行，于是把一次真正该发生的
        点火当成重放挡掉 —— 而报告上写的是「已经点过火了」。
        """
        raise NotImplementedError

    @abstractmethod
    def fires(self, *, workspace: str, schedule_id: str | None = None) -> tuple[ScheduleFire, ...]:
        """按 ``(fired_at, schedule_id, fire_id)`` 升序列出 fire 记录。

        ``schedule_id`` 给 ``None`` = 列出这个工作区里全部 fire。它是
        ``list_schedule_definitions`` 里「这条调度上一次点火点成什么样了」那一格的
        来源 —— 没有它，「动作规格已经指不到东西」这件事只会在点火那一刻存在，
        之后再也读不出来；``pending`` 那一格同理（一次点火被接下了而结果没落定，
        是清单上必须看得见的一种状态）。
        """
        raise NotImplementedError


class ScheduleAction(Protocol):
    """一条调度到期时**真正**要做的那件事。

    ## 它是整个用例里唯一会花钱的那一步

    :func:`marketops.application.scheduling.fire_schedule` 的次序是**构造**出来的：
    读完声明 → 算一次 :meth:`ScheduleDefinition.due`（纯）→ 只有到期才**抢下**
    那一行 fire（:meth:`ScheduleStore.claim_fire`）→ 抢到了才调这一个方法。
    所以两句话都可以被观测成**这个方法的调用次数**，而不是「返回值看起来什么都没
    做」：「空队列不调用 LLM」是 0 次，「重复 fire 幂等」是同一个 ``fire_id``
    点两次还是 0 次之外的那一次 1 次。

    ## 抢下那一行**在它之前**，而这是这条契约的一部分

    实现这一契约的那一半可以假设：**能在这里被调用，就说明这一次 fire 是第一次**。
    它因此不需要自己做幂等（那是 ``schedule_fire`` 主键的事），也不需要问
    「刚才跑过没有」—— 一个自己去查重试的实现会把幂等键变成两处各判一次的东西，
    而两处分歧的那天是两轮研究。

    ## 它不许发通知

    这条契约的返回类型里没有 ``NotificationEvent`` 的位置，也不收通知存储：
    一次业务变化**只产生一条**事件（§13 ``:410``），而那条事件由业务处置点
    （``application/scheduling.py`` 里那个用例）用**它自己**算出来的 ``dedupe_key``
    记下来。动作这一侧顺手发一条通知，会让「一条事件投给 N 个 sink 是 N 张收据」
    变成「一次动作产生 N 条事件」—— 而两者在报告上长得一模一样。

    ## ``next_due_at`` 由它回答，而不是由调用方推

    下一次什么时候来是**这条规则怎么读**的问题，而知道那条规则的是实现这一契约
    的那一半（``calendar_rule`` 的求值需要一个可验证的表示与它自己的测试）。
    返回 ``None`` = 没有下一次，而调用方据此把调度关掉 —— 见
    :meth:`~marketops.domain.scheduling.ScheduleDefinition.advanced`。
    """

    def perform(
        self, definition: ScheduleDefinition, *, fire_id: str, at: datetime
    ) -> ActionReport:
        """做那件事，回一份 :class:`~marketops.domain.scheduling.ActionReport`。

        抛异常也是允许的：调用方会把它记成 ``FireOutcome.FAILED`` 的**一行 fire**，
        而不是一次静默的重来。失败被记下来的那一刻，「这一次已经点过了」这句
        话才成立。

        回 ``outcome=PENDING`` 会被拒（:class:`ActionReport` 上的校验器）：占位是
        调用方写下的，不是动作做出来的。
        """
        ...
