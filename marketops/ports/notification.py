"""``NotificationStore`` / ``NotificationSink``：通知怎么落盘，以及「送出去」这一步。

## 为什么是两个契约（与 ``ports/scheduling.py`` 那一段逐字同源）

* :class:`NotificationStore` 是**状态**：事件、订阅、收据。它的三处判断全部是
  「同一个键再写一次」—— 判据落在唯一键/主键上。
* :class:`NotificationSink` 是**对外那一步**：一次投递出去，我们只能知道
  「对方收下了」或者「不知道」（见 ``domain/notification.py`` 的最后一个成员说明）。
  它没有第三个结果，所以这个契约的返回值里也**没有** ``delivered``。

## 端口上**没有**的东西，每一条都是一个被挡掉的写法

* **没有** ``mark_notified(event)``、没有 ``event.notified_at`` 这样的写入口。
  §9 ``:337`` 逐字「事件本身不写「已通知 Codex / 飞书」」；允许改事件就等于允许
  「改一条订阅」变成「改写历史」，而 ``notification_event`` 是不可变的。
* **没有** ``put_receipt`` 之外的任何收据写入口 —— 尤其没有 ``update_receipt``。
  主键带 ``attempt``，所以重试**产生新行**；一个「把上一次那行改成新的结果」的
  入口会让「投了三次都没成」在盘上变成「投了一次」。
* **没有**按 sink 复制事件的入口（``put_event`` 只收 :class:`NotificationEvent`，
  而那个模型里根本没有 ``sink`` 这一格）。
* **没有** ``delete_subscription``。订阅是一条版本化的配置：取消订阅是写下一版
  （``enabled`` 那一格在 :class:`NotificationSubscription` 上**不存在** —— 见下面
  那条说明），而不是删掉一行历史。
* **没有**任何指向研究/Run 的入口。§13 ``:412`` 逐字「一个 sink 投递失败只重试
  通知，不重跑研究」，而它在端口这一层的形态是：**投递路径根本够不到**
  ``ports/scheduling.py`` 的 :class:`~marketops.ports.scheduling.ScheduleAction`。
  一个把 action 当参数收下的投递函数会让「重试通知」在签名上就摆着一条重跑研究的路。

## 订阅的「取消」写在哪

``notification_subscription`` 上没有 ``enabled`` 列，而 :class:`~marketops.domain.
notification.NotificationSubscription` 上也没有那一格 —— 所以本片**没有**取消订阅
这个动作。想要「先别发给我」的那个人有一个**真实可用**的替代：把 ``severities``
收窄到空集以外的一档、或把 ``delivery_mode`` 改成 ``inbox_only``/``digest``
（事件照旧留在 Inbox，只是不再推）。这一句写在这里而不是含糊过去，因为「读不到
关掉它的方法」与「这个部署不支持关掉」在报告上同形，而它们是两句不同的话。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Protocol

from ..domain.notification import (
    DeliveryAttempt,
    DeliveryReceipt,
    NotificationEvent,
    NotificationSeverity,
    NotificationSubscription,
)

__all__ = [
    "EventWrite",
    "NotificationSink",
    "NotificationStore",
    "ReceiptWrite",
    "SubscriptionWrite",
]


@dataclass(frozen=True)
class EventWrite:
    """一次事件写入的结果。两个字段的理由与 ``ports/views.py::ViewWrite`` 逐字相同。

    ``recorded=False`` 是**幂等命中**：``(workspace, dedupe_key)`` 上已经有这一条，
    而盘上那一行就是这一次要写的东西（内容不同则是 ``StoreError``）。它与
    ``put_event`` 的入参不是同一个对象 —— 返回的是**读回来的那一行**。
    """

    event: NotificationEvent
    recorded: bool


@dataclass(frozen=True)
class SubscriptionWrite:
    """一次订阅写入的结果。``revision_written=None`` = 内容一字不差，没有新版本。"""

    subscription: NotificationSubscription
    revision_written: int | None


@dataclass(frozen=True)
class ReceiptWrite:
    """一次收据写入的结果。

    ``recorded=False`` 是「这一次尝试已经在盘上了」（同一个
    ``(event_id, subscription_id, sink, attempt)`` 且内容相同）—— 它**不是**一次
    覆盖：那个 attempt 的旧行一个字节都没动。
    """

    receipt: DeliveryReceipt
    recorded: bool


class NotificationStore(ABC):
    """事件 / 订阅 / 收据的持久化契约。三张表的写路径**互不相同**，见各自的方法。"""

    # -------------------------------------------------------------- 事件

    @abstractmethod
    def put_event(self, event: NotificationEvent) -> EventWrite:
        """记下一条业务变化事件。**只增，永不改。**

        判据是 ``UNIQUE (workspace, dedupe_key)``：

        * 没有这一条 → 写入，``recorded=True``；
        * 已经有、且内容与这一次一字不差 → 不写，``recorded=False``，回盘上那一行。
          这就是「一条业务变化只生成一次」在**重复调用**下的形态（两个业务处置点
          各自跑到同一件事时，第二个人的写入是一次 no-op，而不是一次事故）；
        * 已经有、而内容不同 → :class:`~marketops.domain.errors.StoreError`，
          消息里点名 ``dedupe_key`` 与不同的那几格。**同一个去重键配两份内容**意味着
          两个不同的业务变化共用了同一个键 —— 静默留下第一条会让第二条**消失**，
          而它在报告上与「这次没有变化」逐字同形。

        ``event_id`` 与 ``dedupe_key`` 的一致性由调用方保证
        （``domain/notification.notification_event_id``）：写一个不由键推出来的 id 的
        调用方会撞在 UNIQUE 上，而那条路径读起来像一次存储事故 —— 这正是那个函数
        存在的原因。
        """
        raise NotImplementedError

    @abstractmethod
    def get_event(self, event_id: str) -> NotificationEvent | None:
        """读一条事件。没有就是 ``None``。"""
        raise NotImplementedError

    @abstractmethod
    def events(
        self,
        *,
        workspace: str,
        kind: str | None = None,
        severity: NotificationSeverity | None = None,
        limit: int | None = None,
    ) -> tuple[NotificationEvent, ...]:
        """本工作区的事件，按 ``(occurred_at, event_id)`` **升序**（早的在前）。

        升序而不是降序，理由在**谁在读它**：``tick`` 要把还没投出去的事件按发生次序
        投出去，而「最早的那一条还没送出去」正是它要找的那一条。``list_notification_events``
        那一侧看到的因此也是升序 —— 一条清单的次序由**唯一一个**读法定，
        两个读法各定一次的话，「最早的一条」会有两个答案。

        两个可选过滤都是**闭集过滤**（不是子串匹配）：``kind`` 是规范 slug，
        ``severity`` 是那个枚举。``limit`` 是给驱动用的有界窗口，``None`` = 全部。
        """
        raise NotImplementedError

    # -------------------------------------------------------------- 订阅

    @abstractmethod
    def put_subscription(
        self, subscription: NotificationSubscription, *, expected_revision: int | None
    ) -> SubscriptionWrite:
        """写一版订阅。``expected_revision=None`` = **必须不存在**（首次），否则必须是
        盘上当前那一版。CAS 载体与三条限定词与 ``ports/views.py::SavedViewStore.put_view``
        逐字相同（新版本行 + 基准版本判据，两道都要），拒绝是
        :class:`~marketops.domain.errors.SubscriptionConflictError` —— 它与
        ``SavedViewConflictError`` 是兄弟，分开的唯一理由是名字要说得出主语
        （见那个异常自己的 docstring）。

        **内容一字不差时不产生新版本**（``revision_written=None``）：重发一次同样的
        订阅不是一次改动，而一个每次都产生新版本的实现会让「有人反复点保存」在盘上
        留下几十版。

        ## 名字／范围**没有**唯一性判据

        ``(workspace, principal, sink)`` 上只有一条普通索引（``db.py`` 那一句），
        而这是对的：同一个人对同一个 sink 合法地持有两条订阅 —— 一条
        ``severities=(critical,)`` 即时、一条 ``routine`` 每日 digest。
        把那条索引升级成唯一约束会让第二种写法**做不成**，而它正是
        ``delivery_mode`` 那一格存在的理由。所以这一层不判「同一个人同 sink 只能有一条」。
        """
        raise NotImplementedError

    @abstractmethod
    def get_subscription(
        self, subscription_id: str, revision: int
    ) -> NotificationSubscription | None:
        """读指名的某一版。没有就是 ``None``。"""
        raise NotImplementedError

    @abstractmethod
    def subscriptions(
        self, *, workspace: str, principal: str | None = None
    ) -> tuple[NotificationSubscription, ...]:
        """本工作区的**全部当前版**订阅，按 ``subscription_id`` 升序。

        ``principal=None`` = 不过滤（所有人的）。**当前版**的那句子查询在这里防的
        与 ``sqlite/views.py`` 逐字同一条：历史版本与当前版长得一模一样，混进来只会
        让「这条订阅改过两次」在投递时被投两遍。

        ``principal`` 那一格进 WHERE 而不是在 Python 里筛：它是这一族唯一的过滤条件
        （``list_notification_events`` 与 ``tick`` 都要读它）。
        """
        raise NotImplementedError

    # -------------------------------------------------------------- 收据

    @abstractmethod
    def record_receipt(self, receipt: DeliveryReceipt) -> ReceiptWrite:
        """记下一次逐 sink 的投递尝试。**只追加。**

        ``(event_id, subscription_id, sink, attempt)`` 上已经有行时：内容相同 →
        ``recorded=False``（同一次尝试被记了两次）；内容不同 → ``StoreError``
        并点名那四格 —— 那意味着「第 2 次尝试」这一个位置上已经有了另一个结果，
        而覆盖它会让重试历史里少一次失败。

        **它永远不碰事件。** 一个「顺手把事件标记成已投递」的实现在这里写不出来：
        本方法的入参是 :class:`~marketops.domain.notification.DeliveryReceipt`，
        里面没有事件的任何可变字段。
        """
        raise NotImplementedError

    @abstractmethod
    def receipts(
        self,
        *,
        workspace: str,
        event_id: str | None = None,
        subscription_id: str | None = None,
    ) -> tuple[DeliveryReceipt, ...]:
        """收据，按 ``(event_id, subscription_id, sink, attempt)`` 升序。

        顺序是契约的一部分（``ReviewCaseStore.list_cases`` 逐字同一条）：读它的人要
        看的是「第 1 次失败、第 2 次失败、第 3 次接受」这条**序列**，而一个按写盘次序
        给的顺序在两次调用之间可能不同。
        """
        raise NotImplementedError

    @abstractmethod
    def attempts(self, *, event_id: str, subscription_id: str, sink: str) -> int:
        """这三格上**已经记过几次**。投递器用它算下一个 ``attempt``（``+1``）。

        单独一个方法而不是让调用方 ``len(receipts(...))``：下一个 attempt 是
        **主键的下一格**，而它与「列表有多长」在两处各算一遍的那天会分家 ——
        分家的形态是一次 ``IntegrityError``，读起来像存储事故。
        """
        raise NotImplementedError


class NotificationSink(Protocol):
    """一个投递出口。**它是这一族唯一会对外说话的东西。**

    ``name`` 是这条 sink 的规范名，而它必须与
    :attr:`~marketops.domain.notification.NotificationSubscription.sink` 上写的那一格
    **逐字相同** —— 收据上的 ``sink`` 列取自订阅，于是「订阅写着 ``inbox`` 而实际送到
    别处」在盘上读不出来。名字对不上的订阅在投递时是一条**记在案的**
    ``unknown``（理由是「这个部署没有注册这个 sink」），不是一次静默跳过：
    静默跳过会让一条永远投不出去的订阅在清单上与一条健康的订阅同形。

    ## 返回值里没有「已投递」

    :meth:`deliver` 只能回 ``accepted`` 或 ``unknown``（``DeliveryOutcome``）。
    理由逐字写在那个枚举的 docstring 里 —— 一个更漂亮的 ``DELIVERED`` 会让运维
    据此停止重试，而它证明不出来。

    ## 它不许做的事

    * 不许收下定义/声明一类的参数 —— 它只认识事件与订阅（``:412``）。
    * 不许把失败变成一句布尔：``DeliveryAttempt`` 里要带 ``failure`` 与
      ``retry_after``，因为**读的人**要靠它们决定是自己重试还是去修配置。
    * 不许自己决定下一次什么时候试（``retry_after`` 是 sink 说的话，不是它编的
      退避曲线）。
    """

    #: 与订阅上 ``sink`` 那一格逐字相同的名字。
    name: str

    def deliver(
        self, event: NotificationEvent, subscription: NotificationSubscription
    ) -> DeliveryAttempt:
        """把这条事件送给这条订阅的那个人。回一次尝试的结果（不是「投递结果」）。"""
        ...
