"""**通知那一个应用用例**：记事件、投递、重试。§9 与 §13。

这个模块守的三句话，每一句都不靠纪律而是靠结构：

* **一次业务变化只有一条事件。** :func:`book_event` 的入参是一整个
  :class:`~marketops.domain.notification.NotificationEvent`，而这个模型里**没有
  ``sink`` 这一格** —— 通知层因此造不出「每个 sink 一条事件」这种写法：它连那件
  东西的形状都表示不出来。要按 sink 分身，先要能写出 ``event(sink=...)``，
  而这个模型写不出来。
* **投递给 N 个 sink 是 N 张收据。** :func:`deliver_event` 遍历的是**订阅**
  （一个人 × 一个出口 × 一个范围），每一条写一行收据，而它从头到尾没有一次
  ``put_event``。
* **`sink` 失败只重试投递。** §13 ``:412`` 逐字。这条在 import 图上的形态是：
  本模块**够不到** :class:`~marketops.ports.scheduling.ScheduleAction`
  （``ports/notification.py`` 那一句论证过同一个理由）。一个「重试时顺手再跑一次
  研究」的实现在这里要先 import 一个本模块拿不到的名字。

## 投递状态的七个值，而它们里没有「已送达」

:class:`DeliveryState` 的七个成员逐条对应「这一轮之后，这条订阅与这条事件之间还
欠着什么」。其中 ``ACCEPTED`` **不是**「送达」：sink 回了 ``accepted`` 只证明
**它收下了**（``domain/notification.py::DeliveryOutcome`` 逐字）。本模块在报告上
因此不写 ``delivered`` 这个词 —— 一个更漂亮的词会让运维据此停止重试，而它证明
不出来。

## ``retry_delivery`` 与 ``deliver_event`` 是同一条路

:func:`retry_delivery` 是单条订阅的形式，:func:`deliver_event` 遍历时用的就是它。
两处各写一份重试判据的那天，「清单说还能再试、投递说已经到顶」会是一份没有一处
会红的报告 —— 而 ``DELIVERY_ATTEMPT_LIMIT`` 只有**一个**定义处
（``domain/notification.py``），本模块只是读它。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Mapping

from ..domain.errors import StoreError
from ..domain.notification import (
    DELIVERY_ATTEMPT_LIMIT,
    DeliveryAttempt,
    DeliveryMode,
    DeliveryOutcome,
    DeliveryReceipt,
    NotificationEvent,
    NotificationSubscription,
    notification_event_id,
)
from ..domain.util import to_utc, utc_now
from ..ports.notification import EventWrite, NotificationSink, NotificationStore

__all__ = [
    "DeliveryReport",
    "DeliveryState",
    "SubscriptionOutcome",
    "book_event",
    "deliver_event",
    "put_subscription",
    "retry_delivery",
]


class DeliveryState(str, Enum):
    """一条订阅与一条事件之间，这一轮之后**还欠着什么**。七个成员，不多不少。"""

    #: sink 回了 ``accepted``：**它收下了**。这不是「送达了」——
    #: ``DeliveryOutcome`` 只有两个成员，而这是其中一个。
    ACCEPTED = "accepted"
    #: 这一次真的试了，sink 没回 ``accepted``（回了 ``unknown`` 或抛了）。**下一次还会试。**
    RETRYING = "retrying"
    #: 试到 ``DELIVERY_ATTEMPT_LIMIT`` 了，不再试。**这不是「投递失败」的结论**——
    #: 它说的是「它还不知道成没成，而我们已经不再问了」。
    EXHAUSTED = "exhausted"
    #: sink 上一次说了 ``retry_after``，而那一刻还没到。这一轮什么都没写。
    WAITING = "waiting"
    #: 之前已经有一次 ``accepted``。这一轮什么都不做 —— 一次收下的投递不是一次
    #: 待办，而每一轮都重投一遍的实现会让一张收据表变成一份心跳日志。
    SETTLED = "settled"
    #: 免打扰。**没有写收据** —— 一次没有发生的投递不是一次尝试。
    DEFERRED = "deferred"
    #: ``digest`` 或 ``inbox_only``。**没有写收据** —— 它们不是推送投递，
    #: 它们的落点就是那条事件本身（Inbox 就是事件表），而给它们记一张 ``accepted``
    #: 收据会替一个没有对外说过话的东西作证。
    NOT_IMMEDIATE = "not_immediate"
    #: 订阅上写的那个 ``sink`` 这个部署没有注册。这**是一次记在案的** ``unknown``
    #: （``ports/notification.py::NotificationSink`` 那一句），不是一次静默跳过。
    UNKNOWN_SINK = "unknown_sink"


@dataclass(frozen=True)
class SubscriptionOutcome:
    """一条订阅在这一轮里的读数。"""

    subscription_id: str
    sink: str
    state: DeliveryState
    #: 这一次写在盘上的那个 attempt（只有真的试了才有）。
    attempt: int | None
    #: 这一次写下去的那一行（只有真的试了才有）。
    receipt: DeliveryReceipt | None
    #: 一句话说明。``DEFERRED`` / ``UNKNOWN_SINK`` / ``EXHAUSTED`` 上它承重：
    #: 三种「这一轮没投出去」在报告上必须分得开，而它们的分开只活在这句话里。
    note: str | None = None


@dataclass(frozen=True)
class DeliveryReport:
    """一条事件这一轮的投递读数。

    ``outcomes`` **只含与这条事件匹配的订阅**（``NotificationSubscription.matches``）：
    一条不匹配的订阅不在报告里，而这不是一处省略 —— 它在这一轮里根本不是这件事的
    读者，把它列成 ``DEFERRED`` 会让「这条事件有 3 个读者」读成 5 个。
    """

    event_id: str
    at: datetime
    outcomes: tuple[SubscriptionOutcome, ...]

    @property
    def attempted(self) -> tuple[SubscriptionOutcome, ...]:
        """真的写下了收据的那些 —— 也就是「这一次对外说了几次话」。"""
        return tuple(o for o in self.outcomes if o.receipt is not None)

    @property
    def pending(self) -> tuple[SubscriptionOutcome, ...]:
        """这一轮之后还欠着的那些（``RETRYING`` / ``WAITING`` / ``EXHAUSTED``）。

        ``EXHAUSTED`` 在里面是刻意的：它**还是欠着**的，只是我们不再自己试了 ——
        把它移出待办会让「这条通知没人收到过」这件事在清单上消失。
        """
        return tuple(
            o
            for o in self.outcomes
            if o.state in (DeliveryState.RETRYING, DeliveryState.WAITING, DeliveryState.EXHAUSTED)
        )


def book_event(notifications: NotificationStore, event: NotificationEvent) -> EventWrite:
    """记下一条业务变化事件。**调用方先算好 ``dedupe_key``**（§13 ``:410``）。

    ``dedupe_key`` 由业务处置点算 —— 它知道「这一次变化」是什么。本函数因此不生成
    去重键，只做一件这个模块才有理由做的事：**核对 ``event_id`` 真的是从
    ``(workspace, dedupe_key)`` 推出来的**。

    这一格不是形式：``notification_event`` 上的唯一约束是 ``(workspace, dedupe_key)``，
    而一个手写的 ``event_id`` 会写得进去 —— 于是同一次业务变化的两条事件各自拿着
    一个 id，去重键那一格挡不住它们，而 ``list_notification_events`` 会如实显示
    「今天这条调度点了两次火」。在这里当场拒它，代价是一次 ``ValueError``；
    不拒，代价是一次**只在重复点火时才现形**的重复通知。
    """
    expected = notification_event_id(event.workspace, event.dedupe_key)
    if event.event_id != expected:
        raise ValueError(
            f"event_id={event.event_id!r} 不是从 (workspace={event.workspace!r}, "
            f"dedupe_key={event.dedupe_key!r}) 推出来的（应为 {expected!r}）。\n"
            "event_id 与去重键必须一致：唯一约束判的是去重键，而手写的 id 会让"
            "同一次业务变化写成两条事件 —— 两条事件各有各的 id，谁也挡不住谁。"
        )
    return notifications.put_event(event)


def put_subscription(
    notifications: NotificationStore,
    subscription: NotificationSubscription,
    *,
    expected_revision: int | None,
    at: datetime | None = None,
) -> NotificationSubscription:
    """写一版订阅。CAS 的三条限定词在 ``ports/notification.py::put_subscription`` 上。

    只改一格：``updated_at`` 由本层盖成这一刻。让调用方给它的实现在「谁先写的」
    上会给出两个答案（调用方给的时刻与写入发生的时刻），而那一格正是运维排序
    订阅变更时唯一的时间依据。
    """
    moment = utc_now() if at is None else to_utc(at)
    written = notifications.put_subscription(
        NotificationSubscription.model_validate(
            {**subscription.model_dump(), "updated_at": moment}
        ),
        expected_revision=expected_revision,
    )
    return written.subscription


def deliver_event(
    notifications: NotificationStore,
    sinks: Mapping[str, NotificationSink],
    *,
    event_id: str,
    at: datetime | None = None,
) -> DeliveryReport:
    """把一条事件投给它**全部**匹配的订阅。**事件一个字节都不动。**

    ``sinks`` 是「这个部署注册了哪些出口」的读数，按名字索引。订阅上写的名字在这
    里面找不到时**照样记一张收据**（``UNKNOWN_SINK``，outcome 是 ``unknown``）：
    那次尝试真的发生了（有人把这条订阅写成了那个名字），而它因此受
    ``DELIVERY_ATTEMPT_LIMIT`` 约束 —— 一张写着理由的收据在运维那里是一句话，
    一次静默跳过的是一个永远不会有人知道的洞。

    事件不存在时抛 :class:`~marketops.domain.errors.StoreError`：调用方（``tick``）
    是从**同一个存储**里读到这个 id 的，而事件表只增不改，所以「刚才还在、现在
    没了」是一次存储异常 —— 停线比报一次没有主语的成功好。
    """
    moment = utc_now() if at is None else to_utc(at)
    event = notifications.get_event(event_id)
    if event is None:
        raise StoreError(
            f"事件 {event_id!r} 不在盘上，而调用方刚从这个存储里读到过它。\n"
            "事件表是只增的，所以这一格只可能是存储异常。不在这里回一份空报告："
            "那会让「一条都没投出去」与「这条事件有零个订阅者」长得一模一样。"
        )
    outcomes = tuple(
        retry_delivery(notifications, sinks, event=event, subscription=subscription, at=moment)
        for subscription in notifications.subscriptions(workspace=event.workspace)
        if subscription.matches(event)
    )
    return DeliveryReport(event_id=event_id, at=moment, outcomes=outcomes)


def retry_delivery(
    notifications: NotificationStore,
    sinks: Mapping[str, NotificationSink],
    *,
    event: NotificationEvent,
    subscription: NotificationSubscription,
    at: datetime | None = None,
) -> SubscriptionOutcome:
    """（再）试一次把这条事件送给这条订阅。**这是唯一的投递路径。**

    判据按次序，每一步都在盘上有一格：

    1. ``delivery_mode`` 不是 ``immediate`` ⇒ ``NOT_IMMEDIATE``，不写收据。
    2. 免打扰（``subscription.deferred``）⇒ ``DEFERRED``，不写收据。
    3. 已经有过一次 ``accepted``（同一个 ``(event, subscription, sink)`` 上的最后
       一张收据）⇒ ``SETTLED``，什么都不做。
    4. 到 ``DELIVERY_ATTEMPT_LIMIT`` 了 ⇒ ``EXHAUSTED``，什么都不做。
    5. 上一张收据说 ``retry_after`` 还没到 ⇒ ``WAITING``，什么都不做。
    6. 否则**真的试一次**：``attempt = notifications.attempts(...) + 1``，
       调 sink、记一行**新**收据。

    第 6 步的 ``attempt`` 取自 ``attempts()`` 而不是 ``len(receipts(...))``：它是
    主键的下一格，而两处各算一遍的那天会撞在 ``IntegrityError`` 上 —— 那句报错
    读起来像存储事故，实际发生的是「同一件事被算了两遍」。

    **sink 抛异常**被记成一次 ``unknown`` 加上那句异常文本，而不是让它逃出去：
    一个逃出去的异常会让这次投递在盘上**什么都不留**，于是重试没有依据、
    ``attempts`` 永远停在 0，而清单上这条订阅看起来像从没被投过。
    """
    moment = utc_now() if at is None else to_utc(at)
    if subscription.delivery_mode is not DeliveryMode.IMMEDIATE:
        return SubscriptionOutcome(
            subscription_id=subscription.subscription_id,
            sink=subscription.sink,
            state=DeliveryState.NOT_IMMEDIATE,
            attempt=None,
            receipt=None,
            note=(
                f"delivery_mode={subscription.delivery_mode.value}："
                "这条订阅不是即时推送，事件本身（Inbox）就是它的落点。"
                "没有写收据 —— 一次没有发生的投递不是一次尝试。"
            ),
        )
    deferred = subscription.deferred(moment)
    if deferred is not None:
        return SubscriptionOutcome(
            subscription_id=subscription.subscription_id,
            sink=subscription.sink,
            state=DeliveryState.DEFERRED,
            attempt=None,
            receipt=None,
            note=deferred,
        )
    history = [
        receipt
        for receipt in notifications.receipts(
            workspace=event.workspace,
            event_id=event.event_id,
            subscription_id=subscription.subscription_id,
        )
        if receipt.sink == subscription.sink
    ]
    last = history[-1] if history else None
    if last is not None and last.outcome is DeliveryOutcome.ACCEPTED:
        return SubscriptionOutcome(
            subscription_id=subscription.subscription_id,
            sink=subscription.sink,
            state=DeliveryState.SETTLED,
            attempt=last.attempt,
            receipt=None,
            note=f"第 {last.attempt} 次投递时 sink 收下了；这一轮不再投。",
        )
    attempt = notifications.attempts(
        event_id=event.event_id,
        subscription_id=subscription.subscription_id,
        sink=subscription.sink,
    ) + 1
    if attempt > DELIVERY_ATTEMPT_LIMIT:
        return SubscriptionOutcome(
            subscription_id=subscription.subscription_id,
            sink=subscription.sink,
            state=DeliveryState.EXHAUSTED,
            attempt=None,
            receipt=None,
            note=(
                f"已经试了 {attempt - 1} 次，到顶（DELIVERY_ATTEMPT_LIMIT="
                f"{DELIVERY_ATTEMPT_LIMIT}）。它**不是**「投递失败」的结论 —— "
                "它说的是「还没人知道成没成，而我们已经不再自己问了」。"
            ),
        )
    if last is not None and last.retry_after is not None and to_utc(last.retry_after) > moment:
        return SubscriptionOutcome(
            subscription_id=subscription.subscription_id,
            sink=subscription.sink,
            state=DeliveryState.WAITING,
            attempt=None,
            receipt=None,
            note=(
                f"sink 在上一张收据里说了 retry_after={last.retry_after.isoformat()}，"
                "那一刻还没到。这一轮什么都没写 —— 退避是 sink 说的话，不是这一层编的曲线。"
            ),
        )
    sink = sinks.get(subscription.sink)
    if sink is None:
        attempt_result = _unknown_sink(subscription.sink)
    else:
        try:
            attempt_result = sink.deliver(event, subscription)
        except Exception as exc:  # noqa: BLE001 —— 见 _failed_attempt 那一段
            attempt_result = _failed_attempt(exc)
    receipt = notifications.record_receipt(
        DeliveryReceipt(
            event_id=event.event_id,
            subscription_id=subscription.subscription_id,
            sink=subscription.sink,
            attempt=attempt,
            workspace=event.workspace,
            recorded_at=moment,
            outcome=attempt_result.outcome,
            message_ref=attempt_result.message_ref,
            failure=attempt_result.failure,
            retry_after=attempt_result.retry_after,
            detail=attempt_result.detail,
        )
    ).receipt
    return SubscriptionOutcome(
        subscription_id=subscription.subscription_id,
        sink=subscription.sink,
        state=(
            DeliveryState.ACCEPTED
            if receipt.outcome is DeliveryOutcome.ACCEPTED
            else DeliveryState.RETRYING
        ),
        attempt=receipt.attempt,
        receipt=receipt,
        note=receipt.failure,
    )


def _unknown_sink(name: str) -> DeliveryAttempt:
    """这个部署没注册那个 sink：**一次真实的、记在案的** ``unknown``。

    走的是与真 sink 完全相同的那条路（同一个 ``DeliveryAttempt``、同一行收据、
    同一个 ``attempt`` 计数），所以它受 ``DELIVERY_ATTEMPT_LIMIT`` 约束 ——
    一条永远投不出去的订阅因此会**停下来并留下五张收据**，而不是每醒来一次
    就无声地重试一轮。
    """
    return DeliveryAttempt(
        outcome=DeliveryOutcome.UNKNOWN,
        failure=(
            f"这个部署没有注册 sink={name!r}。订阅写得下这个名字，而能送达它的东西"
            "不存在 —— 记在案，而不是静默跳过。"
        ),
        detail={"reason": "unknown_sink", "sink": name},
    )


def _failed_attempt(exc: Exception) -> DeliveryAttempt:
    """sink 抛了：**记成一次 ``unknown`` 加上那句异常文本。**

    §13 ``:412``「一个 sink 投递失败只重试通知，不重跑研究」在这里的形态是：这一格
    里只有「下一次什么时候再试」（``retry_after``），**没有**任何能让研究重跑的
    格子 —— 重跑研究需要一个 Run 引用或一个 :class:`ScheduleAction`，而两者都
    不在 ``DeliveryAttempt`` 里，本模块也 import 不到它们。

    让异常逃出去更糟：那次投递在盘上**什么都不留**，于是 ``attempts`` 永远停在 0，
    而清单上这条订阅读起来像「从没被投过」。
    """
    return DeliveryAttempt(
        outcome=DeliveryOutcome.UNKNOWN,
        failure=f"sink 抛了 {type(exc).__name__}: {exc}",
        detail={"reason": "sink_raised", "exception": type(exc).__name__},
    )
