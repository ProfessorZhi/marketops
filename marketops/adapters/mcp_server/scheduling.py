"""调度 / 通知 / 多会话那**八条**工具的处理器（B25，切片 §8 / §9 / §7.1）。

## 这一层不做判断，它做**接线**

八条工具没有一条自己算过一次到期、自己推过一次 ``next_due_at``、自己生成过一次
``dedupe_key``。它们把入参拼成 domain 模型、把存储与动作**注入**应用用例、把结果
摊成可 JSON 的回执。判据全部在三个应用模块里（``application/scheduling.py`` /
``application/notification.py`` / ``application/session.py``），而这一层之所以要薄，
是因为每一条工具都还有一个**非 MCP 的调用方**：

* ``fire_schedule`` 的另外几个调用方是外部 cron / Hosting / ServerOS
  （``catalog.py`` 那条 summary 逐字）。它们不走 MCP，而它们必须走进**同一个**
  用例 —— 判据「幂等键是 DDL 上那条主键」要成立，靠的是所有调用方共用那一行
  代码，不是靠它们都读同一份文档。
* ``tick``（唯一的触发驱动的一步）根本没有工具面，它是驱动进程调的。

## 这一层**没有定时器**，而这是它在类型上的形状

本模块不 import ``threading`` / ``asyncio`` / ``sched`` / ``signal`` / ``time``，
也没有任何一处 sleep。八条工具里没有一条会「稍后再做」某件事：
``put_schedule_definition`` 写的是**配置**，``fire_schedule`` 是**一次**点火
（做完才返回），``list_*`` 是读数。判据在
``tests/test_scheduling_mcp.py::test_no_timer_lives_in_the_scheduling_surface`` ——
它扫的是本模块与 ``application/scheduling.py`` 的 import 与源码，因为「三套
scheduler 各自跑全量刷新」最可能的形态是**某一天顺手在这里起一个线程**，而那一次
提交看起来完全正常。

## 每个处理器都是 ``def``，没有一个是 ``async def``

``MCPServer`` 会把同步处理器丢进线程池，而 ``sqlite3`` 的连接不能跨线程 ——
所以这里每一条都开一次工厂、用完即关（与 ``followups.py`` / ``views.py`` 逐字
同一条纪律）。把某一条写成 ``async def`` 的后果不是「更快」，是它会在事件循环
线程上直接跑阻塞 I/O，而其他请求在那段时间里一个都不被处理。

## ``workspace`` 从 ``settings.profile`` 来，不从参数来

与 ``views.py`` / ``launch.py`` 那几族逐字同一条：一个部署只服务一个 Scene，
它的证据、预算与凭据都属于那一个。让调用方在参数里给 ``workspace`` 的实现，
会在两个部署共用一个库时把两批产出混在一起 —— 而 ``db.py::bind_workspace``
挡得住混装，挡不住**在正确的工作区里写下另一个工作区的对象**。

## 为什么这一族必须够得到 ``ScheduleAction``

``fire_schedule`` 是这一族里唯一会花钱的那一步的入口，而它花钱的方式是调用
调用方接进来的 :class:`~marketops.ports.scheduling.ScheduleAction`。这一层因此
**必须**能拿到那个对象，而**接线的那一格**（谁实现了它、它走不走受理面）不在
本文件里 —— 见 ``server.py::build_server`` 的 ``scheduling`` 参数与
``D/REPORT.md`` 里那四条接线请求。
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import datetime
from typing import Any

from mcp.server.mcpserver.exceptions import ToolError
from pydantic import ValidationError

from ...application.notification import put_subscription as _put_subscription
from ...application.scheduling import (
    SUGGESTED_LIMIT,
    control_schedule as _control,
)
from ...application.scheduling import (
    describe_schedules as _describe,
)
from ...application.scheduling import (
    fire_schedule as _fire,
)
from ...application.scheduling import (
    write_schedule as _write,
)
from ...application.session import put_session_binding as _put_binding
from ...config import Settings
from ...domain.errors import (
    IdempotencyConflictError,
    ScheduleConflictError,
    StoreError,
    SubscriptionConflictError,
)
from ...domain.notification import (
    DeliveryMode,
    NotificationScopeKind,
    NotificationSeverity,
    NotificationSubscription,
)
from ...domain.scheduling import (
    ActionSpec,
    ScheduleControl,
    ScheduleDefinition,
    TriggerType,
)
from ...domain.util import utc_now
from ...ports.notification import NotificationStore
from ...ports.scheduling import ScheduleAction, ScheduleStore
from ...ports.session import SessionBindingStore
from ._shared import Handler, readable, require_scene, store_failure

#: ``list_*`` 两条读数工具的默认上限。**与 ``SUGGESTED_LIMIT`` 分开**（它们答的是
#: 两个问题：一轮驱动处理多少件事、一个人一次读回多少行），但取值相同 ——
#: 一条清单默认返回的行数不该比一轮驱动能处理的还多。
DEFAULT_LIST_LIMIT = SUGGESTED_LIMIT


def _moment(value: str | None, *, field: str) -> datetime | None:
    """一格可选的时间戳。``None`` 就是 ``None``（不是 ``utc_now()``）。

    缺省填 ``utc_now()`` 的实现会让「这一版的 ``created_at`` 是什么时候」 在
    每一次改版时被静默刷新 —— 而那一格是这条调度寿命的起点
    （``application/scheduling.py::write_schedule`` 那一段）。所以缺就是缺：
    调用方要么给一个时刻，要么让应用层替它填 ``updated_at``。
    """
    if value is None:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ToolError(
            f"``{field}`` 不是一个能读的时间戳：{value!r}（{exc}）。\n"
            "  用 ISO 8601。一个读不出来的时刻在这里被当成 «现在» 的话，"
            "盘上那一格会写着一个与调用方本意差了时区那么久的时刻。"
        ) from exc


def _action_spec(payload: Mapping[str, Any] | None) -> ActionSpec:
    """``action_spec`` 那一格必须是**一张表**，而且它指名一个目标。"""
    if not isinstance(payload, Mapping):
        raise ToolError(
            "``action_spec`` 必填，且必须是一张表"
            "（``{kind, target_ref, params?}``）。\n"
            "  一条没有动作规格的调度在清单上与一条健康的调度同形，只有点火那一刻"
            "才会成为一次 UNRESOLVED —— 所以它在这里被拒，而不是在三个月后被读到。"
        )
    try:
        return ActionSpec.model_validate(dict(payload))
    except ValidationError as exc:
        raise ToolError("``action_spec`` 拼不成一个 ActionSpec：\n" + readable(exc)) from exc


def _severities(values: list[str] | None) -> tuple[NotificationSeverity, ...]:
    """空列表 = **不过滤**（``NotificationSubscription`` 那一节逐字）。"""
    try:
        return tuple(NotificationSeverity(item) for item in (values or ()))
    except ValueError as exc:
        raise ToolError(
            f"``severities`` 里有这一档不认识的值：{exc}。\n"
            "  取值是 critical / high / normal / low / info 五档。"
        ) from exc


def _limitation(limit: int | None) -> int:
    if limit is None:
        return DEFAULT_LIST_LIMIT
    if limit < 1:
        raise ToolError(
            f"``limit`` 从 1 起，收到的是 {limit}。\n"
            "  一条清单默认有上限（见 DEFAULT_LIST_LIMIT）；给 0 会让「没有这一条」"
            "与「你没要看」在回执上同形。"
        )
    return limit


def scheduling_handlers(
    *,
    settings: Settings,
    schedules: Callable[[], ScheduleStore],
    notifications: Callable[[], NotificationStore],
    bindings: Callable[[], SessionBindingStore],
    action: ScheduleAction,
) -> dict[str, Handler]:
    """调度 / 通知 / 多会话那八条，按工具名索引。

    Args:
        settings: 配置。工作区与场景都从它来（见模块 docstring）。
        schedules / notifications / bindings: 三个存储的**工厂**（``sqlite3`` 的连接
            不能跨线程）。三格**都必填、都没有缺省**：一个可以为 ``None`` 的接线口
            会让「没有这个存储」与「这个工作区一条都没有」在报告上同形，而两者的
            处置分别是「去接线」与「去写一条」。
        action: 到点了要做的那件事。**必填**。缺省的后果最坏：一个
            「什么都不做、回 SKIPPED」的动作会让每一次点火都在清单上显示
            ``ok`` —— 而它一次研究都没开过。真要一个空动作，请显式接一个
            （``adapters/memory.py::RecordingScheduleAction`` 就是给测试用的那个）。

    ## 这里**没有** ``sinks`` 这一格，而这是一个决定

    八条工具里没有一条投递：投递是 :func:`~marketops.application.scheduling.tick`
    那一半的事（「先点火，再投递」），而 ``tick`` **不是工具** —— 它是触发驱动
    进程调的一步。把 sink 表接进这一个工厂的后果，是一个**用不到的参数**，
    而它的收场有两种，两种都坏：要么它被悄悄忽略（接线的人以为接上就投得出去了），
    要么它诱使某一条工具顺手投一次 —— 而那会让「一条事件投给 N 个 sink 是 N 张
    收据」变成「一次 fire 顺手投了一批」，两个事实从此挤在一条路径上。

    所以 sink 表的接线点是**驱动那一侧** —— 而它已经接上了：
    ``interfaces/trigger_driver.py::deployment_sinks`` 在那里注册了 ``InboxSink``。
    这一格仍然空着，**但原因变了**：现在是「这一层不该有它」，不再是「还没人接」。

    Returns:
        八条工具。**没有一条跑定时器、没有一条自己投递**（见模块 docstring）。
    """

    # ------------------------------------------------------------------ 调度
    def put_schedule_definition(
        schedule_id: str,
        schedule_revision: int,
        scope_ref: str,
        trigger_type: str,
        timezone: str,
        action_spec: Mapping[str, Any],
        owner: str | None = None,
        calendar_rule: str | None = None,
        window: str | None = None,
        next_due_at: str | None = None,
        budget_policy_ref: str | None = None,
        notification_policy_ref: str | None = None,
        enabled: bool = True,
        revision_reason: str = "declared",
        created_at: str | None = None,
        scene_id: str | None = None,
        at: str | None = None,
    ) -> dict[str, Any]:
        """写一版调度声明。第 1 版是首次声明，第 n>1 版是改一次声明。"""
        workspace = require_scene(settings, scene_id)
        moment = _moment(at, field="at") or utc_now()
        try:
            definition = ScheduleDefinition.model_validate(
                {
                    "schedule_id": schedule_id,
                    "schedule_revision": schedule_revision,
                    "workspace": workspace,
                    "owner": owner or workspace,
                    "scope_ref": scope_ref,
                    "trigger_type": TriggerType(trigger_type),
                    "timezone": timezone,
                    "calendar_rule": calendar_rule,
                    "window": window,
                    "next_due_at": _moment(next_due_at, field="next_due_at"),
                    "action_spec": _action_spec(action_spec),
                    "budget_policy_ref": budget_policy_ref,
                    "notification_policy_ref": notification_policy_ref,
                    "enabled": enabled,
                    "revision_reason": revision_reason,
                    # ``created_at`` 缺省取这一刻**只对第 1 版是对的**；改版时
                    # 调用方要把它读到的那一个带回来 —— 见 :func:`_moment`。
                    "created_at": _moment(created_at, field="created_at") or moment,
                    "updated_at": moment,
                }
            )
        except ValidationError as exc:
            # **这一支必须在 ``ValueError`` 之前**：pydantic 的 ``ValidationError``
            # 是 ``ValueError`` 的子类，顺序反过来的话字段级的那几行（哪一个字段、
            # 为什么不行）会被 ``str(exc)`` 那一整块回显盖掉 —— 那正是 ``readable``
            # 存在要避免的东西。
            raise ToolError("这一版调度拼不出来：\n" + readable(exc)) from exc
        except ValueError as exc:
            raise ToolError(
                "这一版调度拼不出来：\n"
                f"  - {exc}\n"
                "  取值：trigger_type ∈ one_shot / calendar / due_state / event；"
                "action_spec.kind ∈ research_run / refresh / digest / notify。"
            ) from exc
        try:
            written = _write(schedules(), definition, at=moment)
        except (ScheduleConflictError, StoreError) as exc:
            raise store_failure(exc) from exc
        return _declaration(
            written.definition,
            written=written.revision_written is not None,
            at=moment,
        )

    def list_schedule_definitions(
        scene_id: str | None = None,
        limit: int | None = None,
        at: str | None = None,
    ) -> dict[str, Any]:
        """每一条调度的当前版，配上「最近一次点火是什么样」。"""
        workspace = require_scene(settings, scene_id)
        moment = _moment(at, field="at") or utc_now()
        views = _describe(schedules(), workspace=workspace, at=moment)
        capped = _limitation(limit)
        shown = views[:capped]
        return {
            "workspace": workspace,
            "at": moment.isoformat(),
            "count": len(shown),
            "total": len(views),
            # 截断必须被说出来：一个只报「有 N 条」的实现会让一次被截断的读数
            # 读起来像「这个工作区就这些」。
            "truncated": len(views) > capped,
            "schedules": [_declaration_view(view) for view in shown],
        }

    def control_schedule(
        schedule_id: str,
        schedule_revision: int,
        control: str,
        reason: str | None = None,
        scene_id: str | None = None,
        at: str | None = None,
    ) -> dict[str, Any]:
        """开 / 关 / 暂停一条调度。**一次控制就是一次新版本写入。**"""
        require_scene(settings, scene_id)
        moment = _moment(at, field="at") or utc_now()
        try:
            verb = ScheduleControl(control)
        except ValueError as exc:
            raise ToolError(
                f"``control`` 只有三个动词：enable / disable / pause，收到的是 "
                f"{control!r}。\n"
                "  disable 是「这条规则不再属于这个部署」，pause 是「它还是我的，"
                "只是现在不跑」—— 两者都落在 enabled=False 上，区别写在 "
                "revision_reason 里。"
            ) from exc
        try:
            written = _control(
                schedules(),
                schedule_id=schedule_id,
                schedule_revision=schedule_revision,
                control=verb,
                reason=reason,
                at=moment,
            )
        except (ScheduleConflictError, StoreError) as exc:
            raise store_failure(exc) from exc
        return _declaration(
            written.definition,
            written=written.revision_written is not None,
            at=moment,
        )

    def fire_schedule(
        schedule_id: str,
        schedule_revision: int,
        fire_id: str,
        scene_id: str | None = None,
        at: str | None = None,
    ) -> dict[str, Any]:
        """点一次火。**任何触发器都走这一个用例。**

        ``fire_id`` **必填、没有缺省**。一个「不给就按到期时刻推」的缺省会让
        调用方以为自己在手动点名一次点火，而它实际拿到的是一个由盘上那一格推出来
        的键 —— 那正是驱动那一侧要做的事（``due_fire_id``），也正是「重复 fire
        幂等」赖以成立的那一格。在这里替调用方推，会让两种完全不同的语义共用
        一个参数位，而它们在回执上同形。
        """
        require_scene(settings, scene_id)
        moment = _moment(at, field="at") or utc_now()
        try:
            receipt = _fire(
                schedules(),
                notifications(),
                action,
                schedule_id=schedule_id,
                schedule_revision=schedule_revision,
                fire_id=fire_id,
                at=moment,
            )
        except (
            ScheduleConflictError,
            IdempotencyConflictError,
            StoreError,
        ) as exc:
            raise store_failure(exc) from exc
        return {
            "schedule_id": receipt.schedule_id,
            "schedule_revision": receipt.schedule_revision,
            "fire_id": receipt.fire_id,
            "workspace": receipt.workspace,
            "status": receipt.status.value,
            # ``replayed`` 与 ``status`` 分开报（``FireReceipt`` docstring 那一段）：
            # 合成一格会让「重复 fire 幂等」与「这次点火成功了」互相冒充。
            "replayed": receipt.replayed,
            "at": receipt.at.isoformat(),
            "advanced_to": receipt.advanced_to,
            "fire": None if receipt.fire is None else _fire_row(receipt.fire),
        }

    # ------------------------------------------------------------------ 通知
    def put_notification_subscription(
        subscription_id: str,
        revision: int,
        scope_kind: str,
        scope_ref: str,
        sink: str,
        principal: str | None = None,
        kinds: list[str] | None = None,
        severities: list[str] | None = None,
        delivery_mode: str = "immediate",
        quiet_hours: str | None = None,
        timezone: str | None = None,
        scene_id: str | None = None,
        at: str | None = None,
    ) -> dict[str, Any]:
        """写一版订阅。**事件本身永远不记谁被通知了。**"""
        workspace = require_scene(settings, scene_id)
        moment = _moment(at, field="at") or utc_now()
        try:
            subscription = NotificationSubscription.model_validate(
                {
                    "subscription_id": subscription_id,
                    "revision": revision,
                    "workspace": workspace,
                    # 缺省是**这个部署**（工作区自己是一条合法的 principal）。
                    "principal": principal or workspace,
                    "scope_kind": NotificationScopeKind(scope_kind),
                    "scope_ref": scope_ref,
                    "kinds": tuple(kinds or ()),
                    "severities": _severities(severities),
                    "sink": sink,
                    "delivery_mode": DeliveryMode(delivery_mode),
                    "quiet_hours": quiet_hours,
                    "timezone": timezone,
                    "updated_at": moment,
                }
            )
        except ValidationError as exc:
            raise ToolError("这一版订阅拼不出来：\n" + readable(exc)) from exc
        except ValueError as exc:
            raise ToolError(
                "这一版订阅拼不出来：\n"
                f"  - {exc}\n"
                "  取值：scope_kind ∈ workspace / workstream / view / watch / review；"
                "delivery_mode ∈ immediate / digest / inbox_only。"
            ) from exc
        try:
            written = _put_subscription(
                notifications(),
                subscription,
                # 第 1 版是首次创建，第 n>1 版接在第 n-1 版之后 —— 与
                # ``put_schedule_definition`` 逐字同一条推法，**不读当前版**。
                expected_revision=None if revision == 1 else revision - 1,
                at=moment,
            )
        except (SubscriptionConflictError, StoreError) as exc:
            raise store_failure(exc) from exc
        return {
            **_subscription_row(written),
            "at": moment.isoformat(),
        }

    def list_notification_events(
        kind: str | None = None,
        severity: str | None = None,
        limit: int | None = None,
        scene_id: str | None = None,
    ) -> dict[str, Any]:
        """不可变的业务变化事件，**每条只产生一次**。"""
        workspace = require_scene(settings, scene_id)
        try:
            chosen = None if severity is None else NotificationSeverity(severity)
        except ValueError as exc:
            raise ToolError(
                f"``severity`` 里有这一档不认识的值：{exc}。\n"
                "  取值是 critical / high / normal / low / info 五档。"
            ) from exc
        capped = _limitation(limit)
        found = notifications().events(
            workspace=workspace, kind=kind, severity=chosen, limit=capped
        )
        return {
            "workspace": workspace,
            # 这里**没有** ``total``：``events(limit=...)`` 在存储那一侧就截断了，
            # 而这个模块拿不到「本来有几条」。编一个出来（比如 ``len(found)``）
            # 会让 ``truncated`` 恒为假 —— 一次被截断的读数于是读起来像全部。
            "count": len(found),
            "events": [_event_row(event) for event in found],
        }

    def list_delivery_receipts(
        event_id: str | None = None,
        subscription_id: str | None = None,
        limit: int | None = None,
        scene_id: str | None = None,
    ) -> dict[str, Any]:
        """逐 sink 的投递尝试：第几次、已接受还是结果未知、失败原因与重试时刻。

        ``outcome`` 只有两个取值（``accepted`` / ``unknown``，见
        ``DeliveryOutcome``）—— **这一条工具不产出 ``delivered``**。一个由收据推出来的
        「已送达」会让运维据此停止重试，而它证明不了。
        """
        workspace = require_scene(settings, scene_id)
        capped = _limitation(limit)
        found = notifications().receipts(
            workspace=workspace, event_id=event_id, subscription_id=subscription_id
        )
        shown = found[:capped]
        return {
            "workspace": workspace,
            "count": len(shown),
            "total": len(found),
            "truncated": len(found) > capped,
            "receipts": [_receipt_row(receipt) for receipt in shown],
        }

    # -------------------------------------------------------------- 多会话
    def put_session_binding(
        client_kind: str,
        focus_refs: list[str],
        report_cursor: str | None,
        expires_at: str | None,
        client_session_ref: str | None = None,
        binding_id: str | None = None,
        principal: str | None = None,
        scene_id: str | None = None,
        at: str | None = None,
    ) -> dict[str, Any]:
        """记下一个客户端会话的便利焦点。**它不授予任何权限。**

        三个内容格（``focus_refs`` / ``report_cursor`` / ``expires_at``）在签名里
        **没有缺省值** —— 一个省略掉它们的处理器会让「只说变了的那一格」在调用点
        看起来是合法的，而在这一层之外，那件事的形态就是**隐式继承上一条**
        （``application/session.py`` 的模块 docstring 第 3 段）。
        """
        workspace = require_scene(settings, scene_id)
        moment = _moment(at, field="at") or utc_now()
        try:
            binding = _put_binding(
                bindings(),
                workspace=workspace,
                principal=principal or workspace,
                client_kind=client_kind,
                client_session_ref=client_session_ref,
                focus_refs=tuple(focus_refs),
                report_cursor=report_cursor,
                expires_at=_moment(expires_at, field="expires_at"),
                binding_id=binding_id,
                at=moment,
            )
        except ValidationError as exc:
            # ``SessionBinding`` 的字段级拒绝。``client_kind`` 不是 slug、``focus_refs``
            # 里有重复项这类都落在这里。
            raise ToolError("这一条绑定拼不出来：\n" + readable(exc)) from exc
        except ValueError as exc:
            # ``application/session.py`` 那一条：``client_session_ref`` 与
            # ``binding_id`` 都没有。它自己就给了一句很准的话，照原样透出去。
            raise ToolError(str(exc)) from exc
        except StoreError as exc:
            raise store_failure(exc) from exc
        return {**_binding_row(binding), "at": moment.isoformat()}

    return {
        "put_schedule_definition": put_schedule_definition,
        "list_schedule_definitions": list_schedule_definitions,
        "control_schedule": control_schedule,
        "fire_schedule": fire_schedule,
        "put_notification_subscription": put_notification_subscription,
        "list_notification_events": list_notification_events,
        "list_delivery_receipts": list_delivery_receipts,
        "put_session_binding": put_session_binding,
    }


# ----------------------------------------------------------------------
# 回执形状
# ----------------------------------------------------------------------
def _declaration_view(view: Any) -> dict[str, Any]:
    """``list_schedule_definitions`` 的一行。"""
    definition: ScheduleDefinition = view.definition
    return {
        **_declaration(definition, written=False, at=None),
        "resolution": view.resolution,
        "due_now": view.due_now,
        "last_fire": None if view.last_fire is None else _fire_row(view.last_fire),
    }


def _declaration(
    definition: ScheduleDefinition, *, written: bool, at: datetime | None
) -> dict[str, Any]:
    """一条调度声明在回执上的形状。**一处定义**，两条工具都用它。

    两处各拼一份的后果是「同一版声明，写入的回执与清单的那一行不一样」——
    两份都自洽，而只有把它们摆在一起才看得出来（与 ``effective_scene.py``
    的 ``_reading`` 同一条纪律）。

    ``trigger_type`` **点名写出**（``catalog.py`` 那条 summary 逐字）：它是这一族
    最要紧的一格 —— 「四类触发」决定谁负责唤醒它，而一个只回 ``due_state`` 这个
    内部取值的回执需要读的人知道那个映射表。
    """
    return {
        "schedule_id": definition.schedule_id,
        "schedule_revision": definition.schedule_revision,
        "workspace": definition.workspace,
        "owner": definition.owner,
        "scope_ref": definition.scope_ref,
        "trigger_type": definition.trigger_type.value,
        "timezone": definition.timezone,
        "calendar_rule": definition.calendar_rule,
        "window": definition.window,
        "enabled": definition.enabled,
        "revision_reason": definition.revision_reason,
        "next_due_at": (
            None if definition.next_due_at is None else definition.next_due_at.isoformat()
        ),
        "action_spec": {
            "kind": definition.action_spec.kind.value,
            "target_ref": definition.action_spec.target_ref,
            "params": dict(definition.action_spec.params),
        },
        "budget_policy_ref": definition.budget_policy_ref,
        "notification_policy_ref": definition.notification_policy_ref,
        "created_at": definition.created_at.isoformat(),
        "updated_at": definition.updated_at.isoformat(),
        # 「这一次写下去新产生了一版吗」—— 内容与当前那一版逐字相同时它是 False。
        # 没有这一格的话，「有人反复点保存」与「真的改了一版」在回执上同形。
        "written": written,
        "at": None if at is None else at.isoformat(),
    }


def _fire_row(fire: Any) -> dict[str, Any]:
    """一条 fire 在回执上的形状。``pending`` 是**看得见**的一格。"""
    return {
        "schedule_id": fire.schedule_id,
        "schedule_revision": fire.schedule_revision,
        "fire_id": fire.fire_id,
        "workspace": fire.workspace,
        "fired_at": fire.fired_at.isoformat(),
        "outcome": fire.outcome.value,
        "message": fire.message,
        "next_due_at": None if fire.next_due_at is None else fire.next_due_at.isoformat(),
        # 占位行**不是失败也不是成功**（``FireReceipt`` docstring 那一格）。
        # 它单独报出来，读的人据此知道「这一次点火被人接下了，结果不知道」。
        "pending": fire.pending,
        "detail": dict(fire.detail),
    }


def _event_row(event: Any) -> dict[str, Any]:
    """一条事件在回执上的形状。

    ``source_event_ref`` 就是那条 summary 要的「它来自哪一个源事件」。
    这里**没有**任何一格说「通知了谁」—— 事件表上不存在那样一列，而这一层
    也不替它拼一个。
    """
    return {
        "event_id": event.event_id,
        "workspace": event.workspace,
        "dedupe_key": event.dedupe_key,
        "kind": event.kind,
        "severity": event.severity.value,
        "scope_kind": event.scope_kind.value,
        "scope_ref": event.scope_ref,
        "summary": event.summary,
        "occurred_at": event.occurred_at.isoformat(),
        "source_event_ref": event.source_event_ref,
        "payload": dict(event.payload),
    }


def _receipt_row(receipt: Any) -> dict[str, Any]:
    """一张收据在回执上的形状。**``outcome`` 不翻译、不美化。**

    没有把 ``unknown`` 说成「待确认」或「重试中」：它是
    ``DeliveryOutcome.UNKNOWN`` 这个成员的原文 —— 「这个部署不能声称对方收下了」。
    一句话的润色在这里的代价是运维据此停掉重试。
    """
    return {
        "event_id": receipt.event_id,
        "subscription_id": receipt.subscription_id,
        "sink": receipt.sink,
        "attempt": receipt.attempt,
        "workspace": receipt.workspace,
        "recorded_at": receipt.recorded_at.isoformat(),
        "outcome": receipt.outcome.value,
        "message_ref": receipt.message_ref,
        "failure": receipt.failure,
        "retry_after": None if receipt.retry_after is None else receipt.retry_after.isoformat(),
        "detail": dict(receipt.detail),
    }


def _subscription_row(subscription: Any) -> dict[str, Any]:
    """一条订阅在回执上的形状。``quiet_hours`` 的**未生效**理由不在这里 ——
    它取决于「这一刻」，而那是 ``retry_delivery`` 的读数（``DEFERRED`` 的 note）。"""
    return {
        "subscription_id": subscription.subscription_id,
        "revision": subscription.revision,
        "workspace": subscription.workspace,
        "principal": subscription.principal,
        "scope_kind": subscription.scope_kind.value,
        "scope_ref": subscription.scope_ref,
        "kinds": list(subscription.kinds),
        "severities": [item.value for item in subscription.severities],
        "sink": subscription.sink,
        "delivery_mode": subscription.delivery_mode.value,
        "quiet_hours": subscription.quiet_hours,
        "timezone": subscription.timezone,
        "updated_at": subscription.updated_at.isoformat(),
    }


def _binding_row(binding: Any) -> dict[str, Any]:
    """一条会话绑定在回执上的形状。

    没有一格说这个会话「能做什么」（``application/session.py`` 逐字：不是权限），
    也没有一格是别的东西的外键 —— ``focus_refs`` 照原样回，**不解析**。
    """
    return {
        "binding_id": binding.binding_id,
        "workspace": binding.workspace,
        "principal": binding.principal,
        "client_kind": binding.client_kind,
        "client_session_ref": binding.client_session_ref,
        "focus_refs": list(binding.focus_refs),
        "report_cursor": binding.report_cursor,
        "expires_at": None if binding.expires_at is None else binding.expires_at.isoformat(),
        "last_seen": binding.last_seen.isoformat(),
    }


__all__ = ["DEFAULT_LIST_LIMIT", "scheduling_handlers"]
