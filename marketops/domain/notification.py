"""**通知**：一条业务变化事件，与它逐 sink 的投递尝试。

``docs/MULTI_SESSION_PLUGIN_AND_NOTIFICATION_ARCHITECTURE.md`` §9 与
``docs/DATA_MODEL.md:39-41``。三条逐字的纪律，这个模块的每一格都是它们的落点：

* ``:337`` —— 「**事件本身不写「已通知Codex/飞书」。订阅和投递分开**」。
  所以 :class:`NotificationEvent` 上**没有**任何一格能表示「谁收到过它」：
  一个 ``notified: bool`` 会让「改一条订阅」变成「改写历史」。
* ``:410`` —— 「一个 NotificationEvent 只生成一次；投递到 N 个 sink 是 N 个
  receipt，不是 N 次业务事件」。载体是 ``UNIQUE (workspace, dedupe_key)``，
  而 :func:`notification_event_id` 让同一条 ``dedupe_key`` 只有**一个** id：
  按 sink 复制事件那种写法在这个模块里写不出来，因为事件根本不知道 sink。
* ``:412`` —— 「一个 sink 投递失败只重试通知，不重跑研究」。
  :class:`DeliveryReceipt` 上没有任何一格指向一次研究动作；**投递路径够不到**
  才是这条纪律的执行器，而不是一句注释。

## ``dedupe_key`` 由业务处置点算，这一层只收下它

``db.py`` 的 ``notification_event`` DDL 上那段逐字：``dedupe_key`` 由业务处置点算
（它知道「这一次变化」是什么），而**不是**由通知层算 —— 通知层算的话，加一个 sink
就会多一个去重域，而那正是 ``:410`` 要挡的那件事。

这个模块因此**不提供**任何「按事件内容合成一个 dedupe_key」的助手。少一个助手，
就有一种写法写不出来：按 ``(事件, sink)`` 去重的那个写法需要一个能拿到 sink 的
去重函数，而这里没有。

## 一条如实报的要求：sink 的结果**只有两种**

``:347-349`` 逐字列出的是 ``accepted/message_ref?`` 与 ``delivered_or_unknown``；
``catalog.py`` 里 ``list_delivery_receipts`` 的 summary 把它读成「**已接受**还是
**结果未知**」。所以 :class:`DeliveryOutcome` 只有两个成员，**没有 ``DELIVERED``**。

理由不是措辞：一条「已投递」在多数 sink 上**证明不出来**。写出去的那一次可能已经
到了对方手里而回执丢了，也可能根本没到 —— 这两种情况的处置完全一样（等下一次重试，
并再看一次），而一个更漂亮的 ``DELIVERED`` 会让运维据此**停止重试**。少一个成员，
就少一种停不下来的错误。**被拒收**落在 ``UNKNOWN`` 上，而它由 ``failure`` 那一格
说清楚：``UNKNOWN`` 回答的是「这个部署能不能声称对方收下了」，答案是不能。

## 免打扰：实现了，而且说得出它**什么时候不生效**

``:345`` 的订阅有 ``quiet_hours/timezone`` 两格。把它们收下来而不读，会让
「已经设了免打扰」与「免打扰设了但不生效」在报告上同形 —— 这个模块的选择是
**两个都实现，而答不出来的那一半如实说**：

* ``timezone`` 写成 ``+08:00`` 这类**偏移量**时，免打扰是真的生效的
  （:meth:`NotificationSubscription.deferred` 是纯算术）；
* 写成 ``Asia/Shanghai`` 这类**命名时区**时，它如实返回一句「未生效，因为命名时区
  要向 tzdata 提问，而领域层不得触碰文件系统」。命名时区在这里**不被当作 UTC** ——
  那正是「看着生效、其实错了八小时」那种错。
"""

from __future__ import annotations

from datetime import datetime, time, timedelta, timezone
from enum import Enum
from typing import Any

from pydantic import Field, field_validator

from .base import StrictModel
from .util import check_canonical_id, check_reference, check_slug_safe, sha256_text, to_utc

__all__ = [
    "DELIVERY_ATTEMPT_LIMIT",
    "DeliveryAttempt",
    "DeliveryMode",
    "DeliveryOutcome",
    "DeliveryReceipt",
    "NotificationEvent",
    "NotificationScopeKind",
    "NotificationSeverity",
    "NotificationSubscription",
    "in_quiet_hours",
    "notification_event_id",
    "parse_quiet_hours",
]


#: 一条订阅在一件事件上最多投几次。**有界是这条的性质，这个数不是从文档里推出来的**：
#: §14 M5 只写「有限重试」。取 5 是因为它必须小到一次业务变化不会永远占着投递队列，
#: 又必须大到跨过一次真实 sink 的短时不可用。它只有一个定义处 —— 换它是一次
#: 要说理由的编辑，而两处各写一遍的后果是「列表说还能再试、投递说已经到顶」。
DELIVERY_ATTEMPT_LIMIT = 5


class NotificationScopeKind(str, Enum):
    """订阅的作用域（§9 ``:343`` 逐字 ``scope (workspace/workstream/view/watch/review)``）。"""

    WORKSPACE = "workspace"
    WORKSTREAM = "workstream"
    VIEW = "view"
    WATCH = "watch"
    REVIEW = "review"


class NotificationSeverity(str, Enum):
    """严重度。五档逐字取自 §9「默认通知策略」。"""

    #: 权限/安全/数据完整性、持续失败使关键范围停止 → 立即通知。
    CRITICAL = "critical"
    #: 复核案件/预算扩张/策略冲突 → 立即或短时 digest。
    NEEDS_HUMAN = "needs_human"
    #: 新路线、账号身份/责任关系变化 → 可按 Watch 配置即时/摘要。
    MATERIAL_CHANGE = "material_change"
    #: 完成批次、无变化、正常维护 → 每日/每周 digest，避免噪声。
    ROUTINE = "routine"
    #: **默认不通知**。用户读实时进度即可。
    PROGRESS_HEARTBEAT = "progress_heartbeat"


class DeliveryMode(str, Enum):
    """投递方式（§9 ``:346`` 逐字 ``delivery_mode: immediate | digest | inbox_only``）。

    它说的是**什么时候**投，不是投不投 —— 所以它不参与 :meth:`matches`。
    把它做成一个「投不投」的开关，会让 ``digest`` 订阅在事件那一刻被读成
    「这条不订阅」，而那些事件此后永远不会被汇总。
    """

    IMMEDIATE = "immediate"
    DIGEST = "digest"
    INBOX_ONLY = "inbox_only"


class DeliveryOutcome(str, Enum):
    """一次投递尝试的结果。**两个成员，不多不少，而且没有 ``DELIVERED``。**

    见模块 docstring 最后那一节 —— 「证明不出来的成功」比一个诚实的未知贵得多。
    """

    #: sink 收下了这次消息，并且给了我们一个可以复核的引用（``message_ref``）。
    ACCEPTED = "accepted"
    #: **这个部署不能声称对方收下了**。两种情形都落在这里，由 ``failure`` 区分：
    #: 回执丢了（超时、连接断在写之后），或者 sink 当场拒收。
    UNKNOWN = "unknown"


class NotificationEvent(StrictModel):
    """一条业务变化事件。字段名逐字取自 §9 ``:330-336`` 那一段。

    **不可变、只生成一次、不记谁被通知了。** 三句话在这一层各是一个形状：
    没有 ``revision``、没有 ``notified_*``、没有 ``sink``。

    ``source_event_ref`` 是这一格里唯一的回溯入口：一条由另一条事件推导出来的事件
    （例如「复核解决」由「事实变化」触发）在这里写清它来自哪一条。它**不是**外键 ——
    被引的那一条可能比这一条早很多，而且它可能被归档在不同的分区里。
    """

    event_id: str
    workspace: str
    #: 由**业务处置点**算。见模块 docstring。
    dedupe_key: str
    #: 业务类别，例如 ``claim_withdrawn``。规范形式由 :func:`check_slug_safe` 判。
    kind: str
    severity: NotificationSeverity
    scope_kind: NotificationScopeKind
    scope_ref: str
    summary: str
    occurred_at: datetime
    source_event_ref: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)

    @field_validator("event_id")
    @classmethod
    def _canonical(cls, value: str) -> str:
        return check_canonical_id(value, "event_id")

    @field_validator("kind")
    @classmethod
    def _kind(cls, value: str) -> str:
        return check_slug_safe(value, "kind")

    @field_validator("scope_ref")
    @classmethod
    def _scope(cls, value: str) -> str:
        return check_reference(value, "scope_ref")

    @field_validator("dedupe_key")
    @classmethod
    def _dedupe(cls, value: str) -> str:
        if not value or value != value.strip():
            raise ValueError(
                "dedupe_key 不能为空或首尾带空白：它是「这条变化只生成一次」的载体，"
                "而一个空键会让两条无关的变化互相挡掉（UNIQUE 把它们看成同一条）"
            )
        return value

    @field_validator("occurred_at")
    @classmethod
    def _utc(cls, value: datetime) -> datetime:
        return to_utc(value)


def notification_event_id(workspace: str, dedupe_key: str) -> str:
    """事件的 id **由（工作区, 去重键）推出来**，不是由生产者随手起的。

    这是 ``UNIQUE (workspace, dedupe_key)`` 那句「只生成一次」在**调用方这一侧**
    的落点：两个业务处置点各自给同一条变化起两个 uuid，那条 UNIQUE 会拦住第二次写
    —— 但拦住的形态是一次 ``StoreError``，而它读起来像一次存储事故。由键推 id 之后，
    第二次写入撞的是**同一条主键**，而那条路径是幂等的（内容相同即 no-op）。

    ``workspace`` 进哈希而不是进样式串：不同工作区的同一个业务键是两个不同的事实
    （见 ``db.py::bind_workspace`` 那段），而一个只按 ``dedupe_key`` 推的 id 会让
    它们在全仓范围内撞在一起。
    """
    digest = sha256_text(f"{workspace}\x1f{dedupe_key}")[:32]
    return f"ev-{digest}"


class NotificationSubscription(StrictModel):
    """一条订阅。字段逐字取自 §9 ``:341-350``。

    **它是「谁要什么」的唯一住处。** 事件上不记谁被通知了，所以「把这条关掉」是一次
    写这一张表，而它**不会**改写任何历史事件 —— 这就是把订阅与投递分开换来的东西。

    ## ``kinds`` / ``severities`` 空集合 = 不过滤

    空集与「一条都不匹配」是两件事，而它们在回执上必须分得开：一条 ``severities=[]``
    的订阅是「全都要」，一条 ``severities=[CRITICAL]`` 的订阅才是「只要这一档」。
    让空集表示「不要任何东西」会让一条刚建好、还没来得及填过滤的订阅静默地收不到
    任何通知，而它在清单上看起来完全正常。
    """

    subscription_id: str
    revision: int = Field(ge=1)
    workspace: str
    #: 人或团队（§9 ``:342`` ``principal/team``）。
    principal: str
    scope_kind: NotificationScopeKind
    scope_ref: str
    kinds: tuple[str, ...] = ()
    severities: tuple[NotificationSeverity, ...] = ()
    sink: str
    delivery_mode: DeliveryMode = DeliveryMode.IMMEDIATE
    #: ``"HH:MM-HH:MM"``。跨零点写成 ``"22:00-07:00"``（开始大于结束即为跨零点）。
    quiet_hours: str | None = None
    #: ``"+08:00"`` 这类固定偏移（免打扰真的生效），或 ``Asia/Shanghai`` 这类命名时区
    #: （这一层如实报「未生效」，见 :meth:`deferred`）。
    timezone: str | None = None
    updated_at: datetime

    @field_validator("subscription_id")
    @classmethod
    def _canonical(cls, value: str) -> str:
        return check_canonical_id(value, "subscription_id")

    @field_validator("sink")
    @classmethod
    def _sink(cls, value: str) -> str:
        return check_slug_safe(value, "sink")

    @field_validator("kinds")
    @classmethod
    def _kinds(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        for item in value:
            check_slug_safe(item, "kinds[]")
        if len(set(value)) != len(value):
            raise ValueError(f"kinds 里有重复项：{value!r}。重复项让「这条订阅要几类」算不出来")
        return value

    @field_validator("updated_at")
    @classmethod
    def _utc(cls, value: datetime) -> datetime:
        return to_utc(value)

    # ------------------------------------------------------------------ 内容

    @property
    def content_digest(self) -> str:
        """这一版**业务内容**的摘要。存储实现用它判「这一次要写的是不是已经就是
        盘上那一份」（``ports/notification.py::put_subscription`` 那一格）。

        它**不含** ``revision`` / ``updated_at``：那两格记的是「这是第几版、什么时候
        落的」，而它们不同**正是**新版存在的理由。把任一格放进来，「重发同一份订阅」
        就会每一次都产生一个新版本。

        ``subscription_id`` 也不进来：它判的是**同一条订阅**的两份内容，而同一个 id
        的两版之间那一格恒等 —— 把它算进摘要只会让「这条订阅是谁」与「它现在是什么」
        在同一个字符串里，而读的人从此分不开「改了内容」与「换了一条」。
        """
        parts = [
            f"workspace={self.workspace}",
            f"principal={self.principal}",
            f"scope_kind={self.scope_kind.value}",
            f"scope_ref={self.scope_ref}",
            f"kinds={','.join(self.kinds)}",
            f"severities={','.join(item.value for item in self.severities)}",
            f"sink={self.sink}",
            f"delivery_mode={self.delivery_mode.value}",
            f"quiet_hours={self.quiet_hours}",
            f"timezone={self.timezone}",
        ]
        return sha256_text("\n".join(parts))[:16]

    # ------------------------------------------------------------------ 投影

    def matches(self, event: NotificationEvent) -> bool:
        """这件事件该不该投给这条订阅。**订阅与事件之间唯一的判据。**"""
        if event.workspace != self.workspace:
            return False
        if (event.scope_kind, event.scope_ref) != (self.scope_kind, self.scope_ref):
            return False
        if self.kinds and event.kind not in self.kinds:
            return False
        if self.severities and event.severity not in self.severities:
            return False
        return True

    def deferred(self, at: datetime) -> str | None:
        """这一刻投不投得出去。返回 ``None`` = 可以投；返回一句话 = 为什么推后。

        返回的是**一句话**而不是一个布尔：``quiet_hours`` 生效、``quiet_hours``
        没配、``quiet_hours`` 配了但因为时区读不出来而不生效 —— 三种情况在
        「现在不投」上同形，而它们的处置完全不同（等一会儿 / 什么都不用做 /
        去把时区改成偏移量）。把理由交出去是唯一分得开它们的方法。
        """
        if self.quiet_hours is None:
            return None
        window = parse_quiet_hours(self.quiet_hours)
        offset = _offset_minutes(self.timezone)
        if offset is None:
            return (
                f"quiet_hours={self.quiet_hours!r} 未生效：timezone={self.timezone!r} "
                "不是固定偏移（``+08:00`` 这种形状）。命名时区要向 tzdata 提问，"
                "而领域层不得触碰文件系统 —— 这里不把它当成 UTC 顶替，"
                "因为那会造出一份看着生效、其实错了几个小时的免打扰。"
            )
        local = to_utc(at).astimezone(_FIXED_OFFSETS[offset])
        if in_quiet_hours(window, local.time()):
            return f"quiet_hours={self.quiet_hours!r}（当地 {local:%H:%M}）尚未结束"
        return None


def parse_quiet_hours(value: str) -> tuple[int, int]:
    """``"22:00-07:00"`` → ``(1320, 420)``，单位是当地时间的分钟。

    开始大于结束表示**跨零点**，而不是一个空窗口 —— 一个把 ``22:00-07:00`` 读成
    「不生效」的实现在报告上与「没配免打扰」同形，而这条订阅整个晚上都会响。
    """
    parts = value.split("-")
    if len(parts) != 2:
        raise ValueError(f"quiet_hours 必须是 'HH:MM-HH:MM'（收到 {value!r}）")
    try:
        start = _minutes(parts[0].strip())
        end = _minutes(parts[1].strip())
    except ValueError as exc:
        raise ValueError(f"quiet_hours 里的时刻读不出来（{value!r}）：{exc}") from exc
    if start == end:
        raise ValueError(
            f"quiet_hours={value!r} 的起止相同 —— 它要么表达「一直安静」（那就该关掉"
            "这条订阅的即时投递），要么是写错了一格，两种都不能靠猜"
        )
    return start, end


def in_quiet_hours(window: tuple[int, int], at: time) -> bool:
    """当地时刻在不在这个窗口里。跨零点在这里被处理一次。"""
    start, end = window
    minute = at.hour * 60 + at.minute
    if start < end:
        return start <= minute < end
    return minute >= start or minute < end


def _minutes(value: str) -> int:
    hour, sep, minute = value.partition(":")
    if not sep:
        raise ValueError(f"{value!r} 里没有 ':'")
    hour_i, minute_i = int(hour), int(minute)
    if not (0 <= hour_i <= 23 and 0 <= minute_i <= 59):
        raise ValueError(f"{value!r} 不是一天之内的时刻")
    return hour_i * 60 + minute_i


#: 固定偏移的缓存。``timezone`` 是**声明**，不是配置 —— 一份进程级缓存不会漂。
_FIXED_OFFSETS: dict[int, Any] = {}


def _offset_minutes(declared: str | None) -> int | None:
    """``"+08:00"`` → ``480``；命名时区、空值、写坏的形状 → ``None``。"""
    if not declared:
        return None
    sign = declared[0]
    if sign not in "+-":
        return None
    hour, sep, minute = declared[1:].partition(":")
    if not sep or not (hour.isdigit() and minute.isdigit()):
        return None
    total = int(hour) * 60 + int(minute)
    if total > 14 * 60:
        return None
    signed = total if sign == "+" else -total
    if signed not in _FIXED_OFFSETS:
        _FIXED_OFFSETS[signed] = timezone(timedelta(minutes=signed))
    return signed


class DeliveryAttempt(StrictModel):
    """**sink 回答的那一格** —— 一次投递出去之后我们知道的东西。

    它是端口（:class:`~marketops.ports.notification.NotificationSink`）的返回值，
    而 :class:`DeliveryReceipt` 是它落到盘上的那一行。两者分开，是因为「sink 说了
    什么」与「第几次试、什么时候、属于哪条订阅」是三件由投递器补上去的事。

    ``retry_after`` 是 sink 自己的话（``Retry-After``），不是我们编的退避曲线：
    一个自造退避的实现在两处各算一次，而它们的分歧表现为「有的失败重试得快、
    有的重试得慢」，没有任何一处会红。
    """

    outcome: DeliveryOutcome
    message_ref: str | None = None
    failure: str | None = None
    retry_after: datetime | None = None
    detail: dict[str, Any] = Field(default_factory=dict)

    @field_validator("retry_after")
    @classmethod
    def _utc(cls, value: datetime | None) -> datetime | None:
        return None if value is None else to_utc(value)


class DeliveryReceipt(StrictModel):
    """一次逐 sink 的投递尝试。字段逐字取自 §9 ``:352-358``。

    **主键带 ``attempt``，所以重试产生新的一行，不覆盖。** 覆盖会把「投了三次都没成」
    抹成「投了一次」，而重试策略要读的恰恰是那个事实（``db.py`` 的 DDL 上那段逐字）。

    它**没有** ``delivered`` 那一格（见 :class:`DeliveryOutcome`），也**没有**任何
    一个字段指向一次研究动作 —— 后者是 §13 那句「一个 sink 投递失败只重试通知，
    不重跑研究」在这张表上的形状。
    """

    event_id: str
    subscription_id: str
    sink: str
    attempt: int = Field(ge=1)
    workspace: str
    recorded_at: datetime
    outcome: DeliveryOutcome
    message_ref: str | None = None
    failure: str | None = None
    retry_after: datetime | None = None
    detail: dict[str, Any] = Field(default_factory=dict)

    @field_validator("recorded_at", "retry_after")
    @classmethod
    def _utc(cls, value: datetime | None) -> datetime | None:
        return None if value is None else to_utc(value)
