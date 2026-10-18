"""**调度那一个应用用例**：声明、控制、点火，以及全仓**唯一**那个触发驱动。

``docs/MULTI_SESSION_PLUGIN_AND_NOTIFICATION_ARCHITECTURE.md`` §8。这一层是
``catalog.py`` 里四条调度工具 summary 的落点，而它们说的是四句不同的话：

* ``put_schedule_definition`` —— 「MCP 配置、读、控制；它**自己不跑定时器** —— 执行
  属于**唯一那个**受管的触发驱动」。于是本模块的 :func:`tick` 是那一件事的**唯一**
  实现，而它里面没有一行计时器：它拿 ``at`` 当参数，被叫一次就走一步。
* ``control_schedule`` —— 「改范围、改预算或改通知目标**是一条新 revision 加一次
  授权，不是改一个字段**」。于是 :func:`control_schedule` 只有一条写法：读当前版、
  产下一版、按 CAS 写回。
* ``fire_schedule`` —— 「重放同一次 fire 回**同一个结果、不建第二轮 Run**；到期检查
  发生在**任何 provider 调用之前**，空队列不花钱」。
* ``list_schedule_definitions`` 要报的那一格（「动作规格已经指不到东西的调度如实报成
  那个样子」）由 :func:`describe_schedules` 从**本片的表**里读出来。

## 次序是这一层唯一有判断的地方，而它每一格都承重

:func:`fire_schedule` 的步骤，按**发生**的次序：

1. 读那一版声明（指名 revision，找不到就拒绝 —— 一个抄错的版号不该被当成「不到期」）；
2. :meth:`ScheduleDefinition.due` —— 纯判定，**没有任何端口调用，也没有任何写入**。
   不到期就到此为止：一次 provider 调用都没有发生，``schedule_fire`` 里也没有行。
   这就是「空队列不调用 LLM」，而它可以被观测成**动作被调了几次**（0 次）。
3. ``claim_fire`` —— 把一行 ``outcome=pending`` 的占位写下去。**这一步在动作之前**，
   而它是「重复 fire 幂等」的全部载体：插得进去的那个调用方去跑动作，插不进去的
   那个直接读回盘上那一行。一个「先查一查再决定」的实现会在两个调用方并发时各跑
   一轮动作 —— 两轮研究，而报告上一切正常。
4. ``action.perform`` —— **整个用例里唯一会花钱的一步**。
5. 记一条 :class:`~marketops.domain.notification.NotificationEvent`（一次业务变化
   一条，``dedupe_key`` 由**本层**算，见 :func:`fire_dedupe_key`）。
6. ``settle_fire`` 写下结论，然后写下一版声明（``next_due_at`` 推后）。

5 与 6 的次序是刻意的：事件记在**推进之前**，于是「推进失败」不会让那条事件消失
（事件是那条业务事实，声明推进是它的后果）。第 7 步不在这个函数里 ——
**投递**是 :func:`fire_schedule` 之外的另一件事，见
``application/notification.py``，它由 :func:`tick` 在同一轮里接着做。

## 重放要把没做完的收尾做完（``_finish``）

「插不进去就直接回盘上那一行」会漏掉一种情况：动作跑完、结论已经写下，而进程在
写下一版声明之前没了。那时下一次 tick 会读到一行**已经结了**的 fire，而声明还停在
``next_due_at`` 上 —— 不去收尾的话，这条调度**永远不再点火**，而报告上一切正常
（一切正常是这一族最坏的失败：它与「还没到点」逐字同形）。

所以重放路径会做一次**幂等的收尾**：事件按 ``dedupe_key`` 重记一次是 no-op，
声明的推进按 CAS 写回（已经推进过就跳过）。两件都是「再做一次没有副作用」的，
而这就是重放能做它们的前提。

## 一份 ``pending`` 的占位不会被自动收掉

占位行意味着动作**可能正在跑**，也可能跑它的进程没了 —— 从盘上分不出这两种。
自动把它判成失败并推进声明，会在动作其实还在跑的时候造出第二次点火所需的全部条件；
自动重跑更糟（那正是幂等键要挡的事）。所以本层**如实报出它**：回执里那条 fire 的
``outcome`` 是 ``pending``，``list_schedule_definitions`` 里那一条调度读成
``pending``。人要把它收掉，用的是一条已经存在的路：``control_schedule`` 写下一版
（于是 ``schedule_revision`` 变了，同一个 ``fire_id`` 是**另一次** fire）。这条恢复
路径不是本层补的，它就是那个复合主键里 ``schedule_revision`` 那一格的用处。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Mapping

from ..domain.errors import ScheduleConflictError
from ..domain.notification import (
    NotificationEvent,
    NotificationScopeKind,
    NotificationSeverity,
    notification_event_id,
)
from ..domain.scheduling import (
    ActionReport,
    FireOutcome,
    FireReceipt,
    FireStatus,
    ScheduleControl,
    ScheduleDefinition,
    ScheduleFire,
    due_fire_id,
    due_schedules,
)
from ..domain.util import SEP, to_utc, utc_now
from ..ports.notification import NotificationSink, NotificationStore
from ..ports.scheduling import ScheduleAction, ScheduleStore, ScheduleWrite
from .notification import DeliveryReport, deliver_event

__all__ = [
    "SUGGESTED_LIMIT",
    "ScheduleView",
    "TickReport",
    "control_schedule",
    "describe_schedules",
    "fire_dedupe_key",
    "fire_schedule",
    "tick",
    "write_schedule",
]

#: 一轮 tick 里最多处理多少条到期调度与多少条待投事件。
#:
#: **有界是这条的性质**，而这个数不是从文档里推出来的（§8 只说「只唤醒最早到期的
#: 一批」）：它必须小到一轮醒来不会跑一整天，又必须大到一次落后的追赶不会分几十轮。
#: 一个定义处 —— 两处各写一遍的后果是「驱动说处理完了、日志说还剩很多」。
SUGGESTED_LIMIT = 25


def fire_dedupe_key(fire: ScheduleFire) -> str:
    """一次 fire 对应的**业务变化**去重键。

    ## 它由这个处置点算，所以通知层算不出第二个去重域

    ``db.py`` 的 ``notification_event`` 那一段逐字：「``dedupe_key`` 由业务处置点算
    （它知道「这一次变化」是什么），而**不是**由通知层算 —— 通知层算的话，加一个
    sink 就会多一个去重域」。这个函数就是那个「处置点」的落点，而
    ``domain/notification.py`` 里**没有**任何按事件内容合成去重键的助手
    （「按 (事件, sink) 去重」那种写法在那里需要先拿到一个 sink，而它拿不到）。

    ## 为什么三段都用 ``SEP`` 而不是 ``:``

    ``fire_id`` 的形状归调用方定（§8 ``:320`` 只说「调用方给的 ``fire_id``」），
    而一个真实存在过的写法是 ``"2026-09-29T06:00"`` —— 里面**就有**冒号。
    用 ``:`` 拼的键因此可以让两个不同的 ``(schedule_id, fire_id)`` 组合拼出同一个
    字符串，而那两个组合是两次不同的业务变化：其中一次会被去重掉，报告上显示
    「今天这条调度没动静」。``SEP``（U+001F）在 ``check_reference`` 的拒绝面里，
    所以它不可能出现在任何一段里 —— 这个函数因此是单射的。
    """
    return SEP.join(("schedule_fired", fire.schedule_id, str(fire.schedule_revision), fire.fire_id))


def _event_for(definition: ScheduleDefinition, fire: ScheduleFire) -> NotificationEvent | None:
    """这一次 fire 该不该成为一条业务变化事件。**纯判定，不写任何东西。**

    两档严重度、一个「不发」：

    * ``RUN_ADMITTED`` → ``MATERIAL_CHANGE``（「新路线、账号身份/责任关系变化」那一档：
      一轮研究被受理了，它是一个值得按 Watch 配置决定即时还是摘要的变化）；
    * ``UNRESOLVED`` / ``FAILED`` → ``NEEDS_HUMAN``（动作规格指不到东西、或者动作失败；
      两者都是**要人看一眼**的，而它们不重试：重试一条指不到东西的规格只会再失败一次）；
    * ``SKIPPED`` → ``None``。动作跑完了而按它自己的判据什么都不用做 —— **没有业务
      变化就没有事件**：给它记一条会让「什么都没发生」也占一格通知配额，
      而 ``ROUTINE`` 那一档存在的理由正是压掉这种噪声。

    ``scope_kind``/``scope_ref`` 是**工作区**，不是被看的那个对象：一条事件只有一对
    范围（订阅也只有一对），而这条变化的生产者是**这条调度**。被看的对象与
    ``notification_policy_ref`` 一起进 ``payload``，于是「谁订阅了这个工作区的调度
    事件」与「这条事件说的是哪个调度」两件事各自读得出来，不需要一个共同的格子。

    ``notification_policy_ref`` 在这里**不被解释**，而且**它的存在与否不改变
    「记不记这条事件」**：事件是那条业务事实，通知策略是「这条事实要不要推给人」。
    让一个没配策略引用的调度不记事件，等于让一次配置缺失**删掉**一条事实 ——
    而它在清单上与「今天什么都没发生」逐字同形。
    """
    if fire.outcome is FireOutcome.SKIPPED:
        return None
    severity = (
        NotificationSeverity.MATERIAL_CHANGE
        if fire.outcome is FireOutcome.RUN_ADMITTED
        else NotificationSeverity.NEEDS_HUMAN
    )
    dedupe_key = fire_dedupe_key(fire)
    return NotificationEvent(
        event_id=notification_event_id(fire.workspace, dedupe_key),
        workspace=fire.workspace,
        dedupe_key=dedupe_key,
        kind="schedule_fired",
        severity=severity,
        scope_kind=NotificationScopeKind.WORKSPACE,
        scope_ref=fire.workspace,
        summary=(
            f"调度 {definition.schedule_id} 第 {definition.schedule_revision} 版点火："
            f"{fire.outcome.value}"
        ),
        occurred_at=fire.fired_at,
        payload={
            "schedule_id": definition.schedule_id,
            "schedule_revision": definition.schedule_revision,
            "fire_id": fire.fire_id,
            "fired_scope_ref": definition.scope_ref,
            "action_kind": definition.action_spec.kind.value,
            "notification_policy_ref": definition.notification_policy_ref,
            "message": fire.message,
        },
    )


# ----------------------------------------------------------------------
# 声明
# ----------------------------------------------------------------------
def write_schedule(
    schedules: ScheduleStore, definition: ScheduleDefinition, *, at: datetime | None = None
) -> ScheduleWrite:
    """写下一条调度：**第 1 版是首次声明，第 n>1 版是改一次声明**。

    一条路，因为 ``put_schedule_definition`` 也是一个工具：**第几版由那一版自己说**，
    而这一层不设「声明」与「改声明」两个入口 —— 两个入口意味着调用方要判一次
    「这是不是第一次」，而判错的那一次（把一次改声明当成声明）会撞在 CAS 上，
    报出来的却是一条读起来像「id 撞车」的冲突。

    ``expected_revision`` 的推法只有这一行：

    * ``schedule_revision == 1`` ⇒ ``expected_revision=None``（**必须不存在**）。
      盘上已经有这个 ``schedule_id`` 时整次拒绝
      （:class:`~marketops.domain.errors.ScheduleConflictError`），**不**顺手写成
      第 2 版：那会让人以「声明」的名义覆盖掉别人的一条调度。
    * ``n > 1`` ⇒ ``expected_revision = n - 1``。调用方交上来的这一版自己说清了它是
      基于哪一版算出来的，而这一层**不替它猜**：一个「读当前版再加一」的实现在这里
      写不出来 —— 那正是 §8 ``:324`` 要挡的「『每天看一下』偷偷变成每天全量重爬」，
      读-改-写会让人跳过中间那一版，而报告上只显示版本号跳了一格。

    只盖一格：``updated_at``。``created_at`` 由调用方带着（他读到那一版时它就在），
    改版时它**不该变** —— 一个每次都盖 ``created_at`` 的实现会让「这条调度是什么
    时候被声明的」读成「上次改它是什么时候」，而那一格正是这条调度寿命的起点。
    """
    moment = utc_now() if at is None else to_utc(at)
    written = ScheduleDefinition.model_validate(
        {**definition.model_dump(), "updated_at": moment}
    )
    return schedules.put_definition(
        written,
        expected_revision=None if written.schedule_revision == 1 else written.schedule_revision - 1,
    )


def control_schedule(
    schedules: ScheduleStore,
    *,
    schedule_id: str,
    schedule_revision: int,
    control: ScheduleControl,
    reason: str | None = None,
    at: datetime | None = None,
) -> ScheduleWrite:
    """开 / 关 / 暂停一条调度。**一次控制就是一次新版本写入。**

    指名 ``schedule_revision`` 而不是「当前版」：一个不知道自己在控制哪一版的人，
    他的控制与「刚才另一个人的控制」之间会发生什么没有答案。盘上没有这一版时抛
    ``ScheduleConflictError`` —— 而它与「有人先写了」在回执上必须分得开，
    见那个异常自己的 docstring。
    """
    moment = utc_now() if at is None else to_utc(at)
    definition = schedules.get_definition(schedule_id, schedule_revision)
    if definition is None:
        raise ScheduleConflictError(
            f"盘上没有 schedule_id={schedule_id!r} 的第 {schedule_revision} 版。\n"
            "要么这个 id 抄错了，要么那一版还没被写下 —— `list_schedule_definitions` "
            "报的是每一条调度**当前**那一版，用它核对之后再用那个版号控制。"
            "不顺手改成「当前版」：控制哪一版是调用方说的事，猜一次会让别人的"
            "一次改动被静默跳过。"
        )
    return schedules.put_definition(
        definition.controlled(control, at=moment, reason=reason),
        expected_revision=schedule_revision,
    )


# ----------------------------------------------------------------------
# 点火
# ----------------------------------------------------------------------
def fire_schedule(
    schedules: ScheduleStore,
    notifications: NotificationStore,
    action: ScheduleAction,
    *,
    schedule_id: str,
    schedule_revision: int,
    fire_id: str,
    at: datetime | None = None,
) -> FireReceipt:
    """点一次火。**任何触发器都走这一个用例**（§8 ``:320`` 逐字）。

    Args:
        schedules: 调度存储。
        notifications: 事件存储。它在这里的用处只有一处 —— 记下那条业务变化事件。
            收下它是刻意的：**去重键由本层算**，而本层是那个「处置点」。
        action: 到点了要做的那件事。它是这个用例里**唯一**会花钱的一步。
        schedule_id / schedule_revision: 指名那一版声明。
        fire_id: 调用方给的幂等键第三段。驱动那一侧由 :func:`due_fire_id` 从**到期
            时刻**推出来（外面那几个驱动唯一能商量出来的东西就是盘上那条调度说它
            该在什么时候醒）；人工触发可以给别的形状。
        at: 这一刻。``None`` = ``utc_now()``。它是**这次调用**的时刻，落在回执的
            ``at`` 与下一版声明的 ``updated_at`` 上 —— 而**不是** fire 行上的
            ``fired_at``：那一格是这一刻**该醒来的时刻**（见下面那条断言上方的说明），
            于是同一个 ``fire_id`` 在任何时刻重来一次都是同一次 fire。

    Returns:
        :class:`~marketops.domain.scheduling.FireReceipt`。三种 ``ADMITTED``、
        ``NOT_DUE`` 与 ``DISABLED`` 的差别见那个模型；``replayed=True`` 且
        ``fire.outcome`` 是 ``pending`` 时，这一次点火被人接下了而结果不知道。
    """
    moment = utc_now() if at is None else to_utc(at)
    definition = schedules.get_definition(schedule_id, schedule_revision)
    if definition is None:
        raise ScheduleConflictError(
            f"盘上没有 schedule_id={schedule_id!r} 的第 {schedule_revision} 版："
            f"这次 fire（fire_id={fire_id!r}）没有被处理，一个字节都没写。\n"
            "调度员与调度驱动都必须带着**盘上真的存在**的那一版来点火。"
        )
    if not definition.due(moment):
        # 到此为止：没有任何端口调用、没有任何写入。两个成因分开报，因为「关着的」
        # 与「还没到点」的处置完全不同（一个要开它，一个什么都不用做），而
        # `due()` 只回一个布尔 —— 所以那一格在这里由 `enabled` 自己读出来。
        return FireReceipt(
            schedule_id=definition.schedule_id,
            schedule_revision=definition.schedule_revision,
            fire_id=fire_id,
            workspace=definition.workspace,
            status=FireStatus.DISABLED if not definition.enabled else FireStatus.NOT_DUE,
            replayed=False,
            at=moment,
            fire=None,
            advanced_to=None,
        )
    # ``fired_at`` 是**这一行描述的那一刻**，不是这次调用的墙上时钟。
    #
    # 它取 ``next_due_at``（到期时刻），因为这个时刻与 ``fire_id`` 已经是一对
    # （:func:`due_fire_id` 就是从它推出来的）：一次 fire 的**身份**因此只由声明加
    # 到期时刻决定，而这正是「重放」这个词成立的前提 —— 同一个键在任何时刻、任何
    # 驱动上重来一次，描述的都是同一次 fire。
    #
    # 取 ``moment`` 会让这一行的内容**依赖调用方的时钟**，而后果不是一句理论：
    # ``claim_fire`` 拿 ``fired_at`` 判「这是不是同一次 fire」（
    # ``ports/scheduling.py::claim_fire`` 第三条），于是第二次来的那个调用方 ——
    # 它的 ``at`` 与第一次不同 —— 撞上 ``IdempotencyConflictError``。而那条路是
    # **必须**走得通的那一条：进程在 ``claim_fire`` 与 ``settle_fire`` 之间死掉时，
    # 声明还停在原来那一版、仍然到期，下一次醒来会拿**同一个** ``fire_id`` 再来一次
    # （``_finish`` 里那句「占位行进来时什么都不做，也不重跑动作」等的就是它）。
    # 用墙上时钟，那条路到不了：它会变成一句「有人改过 id」，而真正的后果是这条调度
    # 每一次醒来都抛一次，驱动整轮挂掉。
    assert definition.next_due_at is not None  # due() 已经答过这一格
    claim = ScheduleFire(
        schedule_id=definition.schedule_id,
        schedule_revision=definition.schedule_revision,
        fire_id=fire_id,
        workspace=definition.workspace,
        fired_at=definition.next_due_at,
        outcome=FireOutcome.PENDING,
        detail={"revision_reason": definition.revision_reason},
    )
    written = schedules.claim_fire(claim)
    if not written.recorded:
        return _finish(
            schedules,
            notifications,
            definition=definition,
            fire=written.fire,
            at=moment,
        )
    try:
        report = action.perform(definition, fire_id=fire_id, at=moment)
    except Exception as exc:  # noqa: BLE001 —— 见下面那段
        # 动作抛了：**这是一条结果**（这一次点火失败了），不是一次可以重来的意外。
        # 记成 FAILED 之后，重放会回这一格而不是再跑一次 —— 一个让异常逃出本函数的
        # 实现在报告上显示「这次调用失败了」，而盘上那一行还停在 pending：
        # 下一次醒来它会读到一个「有人在跑」的假象。
        report = ActionReport(
            outcome=FireOutcome.FAILED,
            message=f"{type(exc).__name__}: {exc}",
            next_due_at=definition.next_due_at,
        )
    settled = schedules.settle_fire(written.fire.settled(report, at=moment))
    if not settled.recorded:
        # 有人先结了这一次 fire（只可能是同一个 claim 的另一个收尾者）。停手：不记事件、
        # 不推进声明 —— 盘上那个结论不是这一次算出来的，据它去改声明就是据别人的
        # 结论改配置。
        return _finish(
            schedules, notifications, definition=definition, fire=settled.fire, at=moment
        )
    return _settle_tail(
        schedules, notifications, definition=definition, fire=settled.fire, at=moment
    )


def _settle_tail(
    schedules: ScheduleStore,
    notifications: NotificationStore,
    *,
    definition: ScheduleDefinition,
    fire: ScheduleFire,
    at: datetime,
    replayed: bool = False,
) -> FireReceipt:
    """结论之后的那两件事：记事件、推进声明。**两件都是幂等的。**

    ``replayed`` 由调用方说：这条尾巴可能是第一次点火之后的那一次，也可能是重放
    补做的那一次（:func:`_finish`）。它**不能由这里推**——两处都会走到这两件事上，
    而 ``replayed`` 那一格回答的是「这一次调用有没有跑动作」，那是调用方才知道的
    事。C 报告里记着这一条：``_finish`` 曾经走到这里、而这里写死 ``False``，
    于是「补做了一条没做完的尾巴」在回执上长得**与「刚刚点了火」逐字同形**
    （两者都是 ``replayed=False`` + ``advanced_to`` 有值），而它们的处置不同 ——
    一个是「去看那一轮 Run」，一个是「有人已经点过了，别再看」。
    """
    event = _event_for(definition, fire)
    if event is not None:
        notifications.put_event(event)
    advanced_to = _advance(schedules, definition=definition, fire=fire, at=at)
    return FireReceipt(
        schedule_id=fire.schedule_id,
        schedule_revision=fire.schedule_revision,
        fire_id=fire.fire_id,
        workspace=fire.workspace,
        status=FireStatus.ADMITTED,
        replayed=replayed,
        at=at,
        fire=fire,
        advanced_to=advanced_to,
    )


def _advance(
    schedules: ScheduleStore,
    *,
    definition: ScheduleDefinition,
    fire: ScheduleFire,
    at: datetime,
) -> int | None:
    """把 ``next_due_at`` 推过去，写下一版。返回新版本号（已经推进过则 ``None``）。

    ``fire.outcome`` 是 ``PENDING`` 时**不推进**：那次点火的结论还不知道，
    而按一个占位的 ``next_due_at``（它是空的）推进会把这条调度关掉。
    """
    if fire.pending:
        return None
    report = ActionReport(
        outcome=fire.outcome, message=fire.message, next_due_at=fire.next_due_at
    )
    return schedules.put_definition(
        definition.advanced(report, at=at), expected_revision=definition.schedule_revision
    ).revision_written


def _finish(
    schedules: ScheduleStore,
    notifications: NotificationStore,
    *,
    definition: ScheduleDefinition,
    fire: ScheduleFire,
    at: datetime,
) -> FireReceipt:
    """重放路径：回盘上那一行，并把**没做完的收尾**做完。

    「没做完」的判据只有一条，而且是确定性的：**下一版还没被写下**
    （``get_definition(schedule_id, revision + 1) is None``）。它成立时这次 fire
    的收尾（记事件、推进声明）都还没有发生，而两件都幂等，所以再做一次是对的。

    下一版**已经在了**就不动：那可能是另一次收尾，也可能是人在那之后的一次控制
    （``control_schedule`` 写下的那一版）。后一种情况下不推进才是对的 ——
    一次显式控制不该被一个落后的收尾盖掉。

    占位行（``pending``）进来时这里什么都不做，也**不重跑动作**：
    分不出「正在跑」与「跑它的进程没了」，而赌错任何一边的代价都写在模块 docstring
    的最后一段里。它被如实报出去。
    """
    if not fire.pending and schedules.get_definition(fire.schedule_id, fire.schedule_revision + 1) is None:
        return _settle_tail(
            schedules,
            notifications,
            definition=definition,
            fire=fire,
            at=at,
            replayed=True,
        )
    return FireReceipt(
        schedule_id=fire.schedule_id,
        schedule_revision=fire.schedule_revision,
        fire_id=fire.fire_id,
        workspace=fire.workspace,
        status=FireStatus.ADMITTED,
        replayed=True,
        at=at,
        fire=fire,
        advanced_to=None,
    )


# ----------------------------------------------------------------------
# 唯一的那个触发驱动
# ----------------------------------------------------------------------
@dataclass(frozen=True)
class TickReport:
    """一轮 tick 的读数。

    ``fired`` 是这一轮真的处理过的点火（含重放），``delivered`` 是这一轮的投递读数
    （``application/notification.py::DeliveryReport``）。**两个清单都不是「全部」**：
    一轮是有界的（:data:`SUGGESTED_LIMIT`），而两个 ``remaining_*`` 说清了还剩多少 ——
    一个只报「处理了 N 条」的实现会让一次落后的追赶读起来像「都处理完了」。
    """

    at: datetime
    workspace: str
    fired: tuple[FireReceipt, ...]
    delivered: tuple[DeliveryReport, ...]
    remaining_due: int
    remaining_events: int


def tick(
    *,
    at: datetime | None = None,
    workspace: str,
    schedules: ScheduleStore,
    notifications: NotificationStore,
    sinks: Mapping[str, NotificationSink],
    action: ScheduleAction,
    limit: int = SUGGESTED_LIMIT,
) -> TickReport:
    """**全仓唯一的触发驱动的一步。** 醒来一次：先点火，再投递。

    ## 它不是定时器，也没有计时器

    ``at`` 是参数，本函数不读时钟之外的时间、不 sleep、不起线程。于是「同一个库在
    同一个 ``at`` 上问两次得到同一个答案」，而「驱动到底做了几件事」是一个可以被
    测出来的数（判据：``tests/test_scheduling_vertical.py`` 里数动作被调了几次）。

    ``at=None`` 时才读一次 ``utc_now()`` —— 而且**只读一次**：一条链上读两次时钟
    会让「到期」与「记下的时刻」来自两个瞬间，而在那一瞬间的差异里，同一轮里的两个
    判断可以给出矛盾的答案。

    ## 两半共用一个上限

    ``limit`` 同时约束到期调度与待投事件：一次醒来最多做 ``limit`` 件事。分开两个
    上限会让「一轮醒来」的最坏耗时是一个乘积，而它只在库很大时才现形。

    ## 它不认识研究

    ``action`` 是**调用方**接进来的那一段（真正的实现走受理面去开一轮 Run）。
    本层因此对「一轮研究怎么开」一无所知，而这也是「投递失败不会重跑研究」在
    import 图上的形态：投递那一半（``application/notification.py``）连
    :class:`ScheduleAction` 这个名字都拿不到。
    """
    moment = utc_now() if at is None else to_utc(at)
    runnable = due_schedules(schedules.due(at=moment, workspace=workspace), at=moment)
    fired: list[FireReceipt] = []
    for definition in runnable[:limit]:
        fire_id = due_fire_id(definition)
        assert fire_id is not None  # due() 已保证 next_due_at 非空
        fired.append(
            fire_schedule(
                schedules,
                notifications,
                action,
                schedule_id=definition.schedule_id,
                schedule_revision=definition.schedule_revision,
                fire_id=fire_id,
                at=moment,
            )
        )
    events = notifications.events(workspace=workspace)
    delivered: list[DeliveryReport] = []
    for event in events[:limit]:
        delivered.append(
            deliver_event(notifications, sinks, event_id=event.event_id, at=moment)
        )
    return TickReport(
        at=moment,
        workspace=workspace,
        fired=tuple(fired),
        delivered=tuple(delivered),
        remaining_due=max(0, len(runnable) - limit),
        remaining_events=max(0, len(events) - limit),
    )


# ----------------------------------------------------------------------
# 清单
# ----------------------------------------------------------------------
@dataclass(frozen=True)
class ScheduleView:
    """``list_schedule_definitions`` 里的一行。

    ``resolution`` 是那条 summary 要求的那个读数（「动作规格**已经指不到东西**的调度
    如实报成那个样子，而不是留在那儿看着像备好弦的」），五个取值：

    * ``never_fired`` —— 这一版从没点过火；
    * ``ok`` —— 上一次点火产生了 Run（``run_admitted``）或者按判据跳过（``skipped``）；
    * ``unresolved`` —— 上一次点火的动作规格指不到东西；
    * ``failed`` —— 上一次点火失败了；
    * ``pending`` —— 上一次点火被接下了，结论还没落定（见模块 docstring 最后一段）。

    外加 ``due_now``：这一版是开着的而 ``next_due_at`` 已经过去 —— 也就是说
    **它在等驱动**。这一格与上面五格不重叠（一个 ``failed`` 的调度也可能 ``due_now``），
    所以它是独立的一个布尔而不是第六个取值：合成一格会让「失败了而且在等下一次」
    读成二选一。
    """

    definition: ScheduleDefinition
    resolution: str
    due_now: bool
    last_fire: ScheduleFire | None


def describe_schedules(
    schedules: ScheduleStore, *, workspace: str, at: datetime | None = None
) -> tuple[ScheduleView, ...]:
    """每一条调度的当前版，配上「最近一次点火是什么样」。

    判据全部来自**本片自己的两张表**（``schedule_definition`` / ``schedule_fire``）：
    一次跨到别的切片的存储去问「这个动作的目标还在不在」，会让这一格的读数取决于
    另一个切片今天有没有接上 —— 而它红了之后看起来像是本片的问题。
    """
    moment = utc_now() if at is None else to_utc(at)
    views = []
    for definition in schedules.definitions(workspace=workspace):
        fires = schedules.fires(workspace=workspace, schedule_id=definition.schedule_id)
        last = fires[-1] if fires else None
        if last is None:
            resolution = "never_fired"
        elif last.pending:
            resolution = "pending"
        elif last.outcome is FireOutcome.RUN_ADMITTED or last.outcome is FireOutcome.SKIPPED:
            resolution = "ok"
        elif last.outcome is FireOutcome.UNRESOLVED:
            resolution = "unresolved"
        else:
            resolution = "failed"
        views.append(
            ScheduleView(
                definition=definition,
                resolution=resolution,
                due_now=definition.due(moment),
                last_fire=last,
            )
        )
    return tuple(views)
