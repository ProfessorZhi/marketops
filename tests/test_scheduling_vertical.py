"""调度 → 通知 → 投递的**竖向验收**：一条链子从头走到尾，数三样东西。

三条判据分得开，因为它们各自最容易被写成一种「看起来通过」的样子：

1. **一次业务变化 = 一条事件，N 个出口 = N 张收据。**
   把它写成「N 个出口 = N 条事件」的实现在清单上完全正常 —— 通知列出得多一点而已。
   这条判据因此同时数**事件条数**与**收据条数**，而且要求两者对同一条 ``event_id``。
2. **一个出口投递失败只重试通知，不重跑研究。**
   ``action.perform`` 被调了几次是这一条唯一问得出来的量（``RecordingScheduleAction``
   的 docstring 逐字）。一个「投递失败 ⇒ 重新点一次火」的实现在回执上长得与对的
   那个一样：事件还在、收据还在、推进也发生了。
3. **重试插新行，不覆盖。** ``delivery_receipt`` 的主键含 ``attempt``，于是
   「第 1 次失败的那句话」在第 2 次之后仍然读得回来。一个按
   ``(event, subscription, sink)`` 就地 UPDATE 的实现在报告上显示「投了 2 次」，
   而盘上只有一行 —— 第一次为什么失败从此不可考。

## 为什么用真存储、真 sink，而不是替身

这一份是**竖向**的：它要跨过 ``application`` 与 ``adapters`` 两层之间的那条缝，而
缝里的错（去重键由谁算、收据有几个键、事件体有没有被顺手改一下）只有真的写盘才
现形。两个接口替身在这里被换成了 :class:`SqliteScheduleStore` /
:class:`SqliteNotificationStore` 与 :class:`InboxSink`，唯一留下的替身是
``RecordingScheduleAction``（研究那一侧**在本片里本来就没有真实现** —— 它走受理面
去开一轮 Run，见 C 报告里的 WIRING-REQUEST 第 4 条）。sink 这一侧不假设：
:class:`InboxSink` 是**唯一一个 ``accepted`` 证明得出来的出口**
（``adapters/notification.py`` 逐字），所以「收下了」在这份测试里有内容可指 ——
那一行此刻在盘上、并且读得回来。
"""

from __future__ import annotations

import ast
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from marketops.adapters.memory import FailingSink, RecordingScheduleAction
from marketops.adapters.notification import InboxSink
from marketops.adapters.sqlite.notification import SqliteNotificationStore
from marketops.adapters.sqlite.scheduling import SqliteScheduleStore
from marketops.application.notification import (
    DeliveryState,
    put_subscription,
)
from marketops.application.scheduling import (
    control_schedule,
    fire_schedule,
    tick,
    write_schedule,
)
from marketops.domain.notification import (
    DELIVERY_ATTEMPT_LIMIT,
    DeliveryMode,
    DeliveryOutcome,
    NotificationEvent,
    NotificationScopeKind,
    NotificationSeverity,
    NotificationSubscription,
    notification_event_id,
)
from marketops.domain.scheduling import (
    FireOutcome,
    FireStatus,
    ScheduleControl,
    ScheduleFire,
    due_fire_id,
)
from tests.test_scheduling_store_contract import T0, WORKSPACE, make_definition
from tests.test_session_binding_negatives import identifiers_of, source_of

#: 动作下一次该醒来是一天以后 —— 于是「同一个 ``at`` 上再 tick 一次」只可能是在
#: 重试投递，不可能是又点了一次火。这条差别是第 2 条判据的前提。
LATER = T0 + timedelta(days=1)

#: 重试用的那几个时刻。**都在同一个到期时刻之内**（远早于 ``LATER``），于是它们之间
#: 的差别只有投递那一半。
T1 = T0 + timedelta(minutes=1)
T2 = T0 + timedelta(minutes=2)
T3 = T0 + timedelta(minutes=3)
T4 = T0 + timedelta(minutes=4)
T5 = T0 + timedelta(minutes=5)
T6 = T0 + timedelta(minutes=6)


def subscription(
    *,
    subscription_id: str = "sub-0001",
    sink: str = "inbox",
    principal: str = "alice",
    severities: tuple[NotificationSeverity, ...] = (),
    delivery_mode: DeliveryMode = DeliveryMode.IMMEDIATE,
    quiet_hours: str | None = None,
    updated_at: datetime = T0,
) -> NotificationSubscription:
    """一条订阅。**范围是工作区**，因为事件的范围就是工作区。

    ``_event_for`` 逐字：「``scope_kind``/``scope_ref`` 是**工作区**，不是被看的那个
    对象」。一份把订阅写成 ``WATCH/watch-0001`` 的测试在这里会一条都投不出去，而
    报告上看起来像「这条事件没有读者」—— 那是 ``matches`` 拦的，不是缺陷。

    ``severities`` 默认空集 = 不过滤（``domain/notification.py`` 那一节：空集与
    「一条都不匹配」是两件事）。取空集是刻意的：这条判据要问的是**几条事件、几张
    收据**，不该顺带把「MATERIAL_CHANGE 有没有被写对」也考一遍。
    """
    return NotificationSubscription(
        subscription_id=subscription_id,
        revision=1,
        workspace=WORKSPACE,
        principal=principal,
        scope_kind=NotificationScopeKind.WORKSPACE,
        scope_ref=WORKSPACE,
        kinds=(),
        severities=severities,
        sink=sink,
        delivery_mode=delivery_mode,
        quiet_hours=quiet_hours,
        timezone=None,
        updated_at=updated_at,
    )


def make_event(*, dedupe_key: str, occurred_at: datetime = T0) -> NotificationEvent:
    """一条事件。``event_id`` **由键推出来** —— 那是唯一的合法写法。

    ``book_event`` 会核这一格（两条事件各有各的 id 时，唯一约束判的是去重键，
    挡不住它们），所以这里不手写 id。
    """
    return NotificationEvent(
        event_id=notification_event_id(WORKSPACE, dedupe_key),
        workspace=WORKSPACE,
        dedupe_key=dedupe_key,
        kind="schedule_fired",
        severity=NotificationSeverity.MATERIAL_CHANGE,
        scope_kind=NotificationScopeKind.WORKSPACE,
        scope_ref=WORKSPACE,
        summary="调度到点，已点火",
        occurred_at=occurred_at,
        source_event_ref=None,
        payload={},
    )


def declare_due_schedule(schedules: SqliteScheduleStore) -> None:
    """写下一条**此刻到期**的调度，动作下一次醒来在 ``LATER``。"""
    write_schedule(
        schedules,
        make_definition(next_due_at=T0, scope_ref=WORKSPACE, created_at=T0),
        at=T0,
    )


@pytest.fixture()
def wired(tmp_path: Path):
    """一份接好的竖切：一个库、一个动作、一份出口表。"""
    database = tmp_path / "marketops.db"
    schedules = SqliteScheduleStore(database)
    notifications = SqliteNotificationStore(database)
    action = RecordingScheduleAction(next_due_at=LATER)
    inbox_root = tmp_path / "inbox"
    return schedules, notifications, action, inbox_root


def read_inbox(root: Path, workspace: str = WORKSPACE) -> list[dict]:
    """把收件箱读回来。**投递的 ``accepted`` 靠它才有内容可指。**"""
    path = root / workspace / "inbox.jsonl"
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


# ----------------------------------------------------------------------
# 一、一次点火 = 一条事件；N 个出口 = N 张收据
# ----------------------------------------------------------------------
def test_one_fire_becomes_one_event_and_two_sinks_become_two_receipts(wired) -> None:
    """**这一条就是「一个业务变化产生恰好一条 NotificationEvent」的竖向形态。**

    两个出口（都是 :class:`InboxSink`，名字不同、根不同）各写一行，而**事件只有一条**。
    一个「每投一次记一条事件」的实现会让事件表在第二个出口上多一行 —— 而
    ``list_notification_events`` 上那读起来像「这条调度今天点了两次火」。
    """
    schedules, notifications, action, inbox_root = wired
    declare_due_schedule(schedules)
    put_subscription(
        notifications, subscription(subscription_id="sub-0001", sink="inbox"),
        expected_revision=None, at=T0,
    )
    put_subscription(
        notifications,
        subscription(subscription_id="sub-0002", sink="audit"),
        expected_revision=None,
        at=T0,
    )

    report = tick(
        at=T0,
        workspace=WORKSPACE,
        schedules=schedules,
        notifications=notifications,
        sinks={
            "inbox": InboxSink(inbox_root / "inbox"),
            "audit": InboxSink(inbox_root / "audit", name="audit"),
        },
        action=action,
    )

    # 火点了恰好一次，动作跑了恰好一次。
    assert [receipt.status.value for receipt in report.fired] == ["admitted"]
    assert action.attempts == 1

    events = notifications.events(workspace=WORKSPACE)
    assert len(events) == 1
    event = events[0]

    # 一轮里投了两条订阅，而它们是**同一条事件**的两个读者。
    assert len(report.delivered) == 1
    outcomes = report.delivered[0].outcomes
    assert sorted(o.state.value for o in outcomes) == ["accepted", "accepted"]
    assert {o.subscription_id for o in outcomes} == {"sub-0001", "sub-0002"}
    assert {o.attempt for o in outcomes} == {1}

    receipts = notifications.receipts(workspace=WORKSPACE, event_id=event.event_id)
    assert len(receipts) == 2
    assert {row.subscription_id for row in receipts} == {"sub-0001", "sub-0002"}
    assert all(row.outcome is DeliveryOutcome.ACCEPTED for row in receipts)

    # 收据上的 sink 列取自**订阅**，两份因此分得开。
    assert {row.sink for row in receipts} == {"inbox", "audit"}

    # 而去重键里没有 sink 这一格：加一个出口不该多出一个去重域。
    assert "inbox" not in event.dedupe_key and "audit" not in event.dedupe_key
    assert event.dedupe_key.count("\x1f") == 3

    # ``message_ref`` 可复核：指的那一行真的在盘上，而且是这条投递。
    for row in receipts:
        relative, _, lineno = (row.message_ref or "").rpartition("#")
        assert relative == f"{WORKSPACE}/inbox.jsonl"
        assert lineno.isdigit()
        line = read_inbox(inbox_root / row.sink)[int(lineno) - 1]
        assert line["event_id"] == event.event_id
        assert line["subscription_id"] == row.subscription_id
        assert line["dedupe_key"] == event.dedupe_key


def test_a_subscription_naming_an_unregistered_sink_is_recorded(wired, tmp_path: Path) -> None:
    """订阅写了一个这个部署没有的出口 ⇒ **一次记在案的 ``unknown``**，不是静默跳过。

    「静默跳过」与「投出去了」在报告上同形（都是一条事件、零个错误），而这个出口
    名字是被谁写上去的、什么时候写的，从此没有任何一处记得。

    ## 它的 ``state`` 是 ``RETRYING``，不是 ``UNKNOWN_SINK``

    这一格值得写下来，因为它看着像个反例。``UNKNOWN_SINK`` 是
    :class:`SubscriptionOutcome` 上**唯一一个不在 ``retry_delivery`` 七个返回点里**
    的成员：走的是与真出口完全相同的那条路（同一个 ``DeliveryAttempt``、同一行收据、
    同一个 ``attempt`` 计数），于是终态由那张收据的 ``outcome`` 决定 ——
    ``unknown`` ⇒ ``RETRYING``。分得开它们的是收据里的
    ``detail["reason"] == "unknown_sink"`` 与那句话，而**不是**一个更漂亮的终态：
    给它一个自己的终态会让它从 ``report.pending`` 里掉出去，而它**还是欠着的**
    （``pending`` 那一段逐字：把它移出待办会让「这条通知没人收到过」在清单上消失）。

    于是「记在案」这句话的判据在这里是三样：收据在盘上、``reason`` 指名是哪一种坏、
    而它仍然列在待办里。
    """
    schedules, notifications, action, _ = wired
    declare_due_schedule(schedules)
    put_subscription(
        notifications, subscription(sink="feishu"), expected_revision=None, at=T0
    )

    report = tick(
        at=T0,
        workspace=WORKSPACE,
        schedules=schedules,
        notifications=notifications,
        sinks={},
        action=action,
    )

    outcome = report.delivered[0].outcomes[0]
    assert outcome.receipt is not None
    assert outcome.receipt.outcome is DeliveryOutcome.UNKNOWN
    assert outcome.receipt.detail["reason"] == "unknown_sink"
    assert "feishu" in (outcome.receipt.failure or "")
    assert outcome.receipt.message_ref is None  # 没有东西收下过它，就没有可指的落点
    assert outcome.state is DeliveryState.RETRYING
    assert outcome in report.delivered[0].pending
    # 盘上真的有一行 —— 不是一次静默跳过。
    receipts = notifications.receipts(workspace=WORKSPACE)
    assert len(receipts) == 1 and receipts[0].attempt == 1


# ----------------------------------------------------------------------
# 二、出口失败只重试通知，不重跑研究
# ----------------------------------------------------------------------
def test_a_failing_sink_retries_the_notification_and_never_reruns_research(wired) -> None:
    """**这一片最承重的一条竖向判据。**

    ``FailingSink(retry_after=None)`` 每次都说「还没成，而且现在就可以再试」——
    于是每一轮 tick 都是一次新的尝试。要同时看到三件事：

    * 收据**一行一层往上加**（不覆盖）；
    * 事件**一个字节都不动**（重试不是一次新业务变化）；
    * ``action.attempts`` **停在 1**（研究没有被重跑）。
    """
    schedules, notifications, action, _ = wired
    declare_due_schedule(schedules)
    put_subscription(notifications, subscription(sink="failing"), expected_revision=None, at=T0)
    failing = FailingSink(failure="出口在这条测试里永远失败")

    first = tick(
        at=T0, workspace=WORKSPACE, schedules=schedules, notifications=notifications,
        sinks={"failing": failing}, action=action,
    )
    assert action.attempts == 1
    event = notifications.events(workspace=WORKSPACE)[0]
    before = event.model_dump()

    assert first.delivered[0].outcomes[0].state is DeliveryState.RETRYING
    assert first.delivered[0].outcomes[0].attempt == 1

    ticks = (T1, T2)
    for moment in ticks:
        report = tick(
            at=moment, workspace=WORKSPACE, schedules=schedules, notifications=notifications,
            sinks={"failing": failing}, action=action,
        )
        assert report.delivered[0].outcomes[0].state is DeliveryState.RETRYING
        # 一次都没点着火：这条调度下一次醒来是 LATER。
        assert report.fired == ()

    receipts = notifications.receipts(
        workspace=WORKSPACE, event_id=event.event_id, subscription_id="sub-0001"
    )
    assert [row.attempt for row in receipts] == [1, 2, 3]
    assert all(row.outcome is DeliveryOutcome.UNKNOWN for row in receipts)
    assert all(row.message_ref is None for row in receipts)
    # **第 1 次失败的那句话还在。** 一个就地覆盖的实现到这里只剩最后一行的那一句，
    # 而「第一次为什么失败」是不可考的 —— 它可能与前两次是同一个原因，也可能不是。
    assert {row.failure for row in receipts} == {"出口在这条测试里永远失败"}
    assert len({row.recorded_at for row in receipts}) == 3

    # 事件体一个字节都没动，而且仍然只有一条。
    assert notifications.get_event(event.event_id).model_dump() == before
    assert len(notifications.events(workspace=WORKSPACE)) == 1

    # 研究跑了**一次**。投递失败那一侧连 ``ScheduleAction`` 这个名字都拿不到
    # （``application/notification.py`` 的 import 图），这一格是它的竖向读数。
    assert action.attempts == 1
    assert [call[1] for call in action.perform_calls] == [event.payload["fire_id"]]


#: 投递那一半**读得到**的三个模块。它们之间没有通往研究侧的边。
DELIVERY_SIDE_MODULES = (
    "marketops.application.notification",
    "marketops.ports.notification",
    "marketops.domain.notification",
)

#: 研究侧的标识符。``perform`` 也列进来：一个 ``deliver(..., action, ...)`` 的变异
#: 要真去重跑研究，它一定得**调用**那个东西，而那次调用一定会写下一个名字。
RESEARCH_SIDE_NAMES = frozenset(
    {
        "ScheduleAction",
        "RecordingScheduleAction",
        "RunAdmission",
        "admit_run",
        "fire_schedule",
        "tick",
        "ScheduleFire",
        "schedule_id",
        "schedule_revision",
        "fire_id",
        "perform",
    }
)


def test_the_delivery_half_cannot_reach_the_action() -> None:
    """**第 2 条判据的结构性那一半**：投递那一半拿不到研究侧的任何一格。

    上面那条竖向用例数的是 ``action.attempts``；这一条问的是**为什么**它数得出来。
    ``tick`` 的 docstring 逐字说「投递那一半（``application/notification.py``）
    连 :class:`ScheduleAction` 这个名字都拿不到」，而那句话在这里是一个可执行的形状。

    两条缺一不可：一条实现在投递失败时**想去**重跑研究，完全可以先把
    ``attempts`` 留在 1 上（它会失败、会静默、会在别的一天成功）。所以「重跑不了」
    必须由 import 图与标识符集来说 —— 往 ``application/notification.py`` 里写下
    这个类型名，这一条就转红，而上一条竖向用例不会有任何反应。

    只看**标识符与 import 边**，不看字符串：这几个模块的 docstring 里逐字写着
    :class:`ScheduleAction`，一句 ``"ScheduleAction" not in source`` 会恰好在
    把纪律说得最清楚的那段散文上变红。
    """
    for module_name in DELIVERY_SIDE_MODULES:
        found = identifiers_of(module_name) & RESEARCH_SIDE_NAMES
        assert not found, (
            f"{module_name} 里出现了研究侧的标识符 {sorted(found)}：一个出口投递失败"
            "只重试通知；重跑研究只能从研究那一侧发起。"
        )
        for node in ast.walk(ast.parse(source_of(module_name))):
            imported = (
                [alias.name for alias in node.names] if isinstance(node, ast.Import) else []
            )
            if isinstance(node, ast.ImportFrom) and (node.module or "").endswith("scheduling"):
                imported.append(node.module or "")
            for name in imported:
                assert not name.endswith("scheduling"), (
                    f"{module_name} 从 {name!r} 导入了东西：投递与研究是两半，"
                    "它们之间没有 import 边。"
                )


def test_retries_stop_at_the_attempt_limit_and_stay_listed_as_pending(wired) -> None:
    """试到上限就停 —— 但**仍然列在待办里**，而且不是「投递失败」的结论。

    ``EXHAUSTED`` 那句话逐字：「它说的是『还没人知道成没成，而我们已经不再自己问了』」。
    把它移出 ``report.pending`` 会让「这条通知没人收到过」在清单上消失。
    """
    schedules, notifications, action, _ = wired
    declare_due_schedule(schedules)
    put_subscription(notifications, subscription(sink="failing"), expected_revision=None, at=T0)
    failing = FailingSink()

    moments = (T0, T1, T2, T3, T4)
    for moment in moments:
        tick(
            at=moment, workspace=WORKSPACE, schedules=schedules, notifications=notifications,
            sinks={"failing": failing}, action=action,
        )

    event_id = notifications.events(workspace=WORKSPACE)[0].event_id
    assert len(
        notifications.receipts(workspace=WORKSPACE, event_id=event_id)
    ) == DELIVERY_ATTEMPT_LIMIT

    # 第 6 轮：到顶了，什么都不写。
    last = tick(
        at=T5, workspace=WORKSPACE, schedules=schedules, notifications=notifications,
        sinks={"failing": failing}, action=action,
    )
    outcome = last.delivered[0].outcomes[0]
    assert outcome.state is DeliveryState.EXHAUSTED
    assert outcome.receipt is None and outcome.attempt is None
    assert outcome in last.delivered[0].pending
    assert str(DELIVERY_ATTEMPT_LIMIT) in (outcome.note or "")
    assert len(
        notifications.receipts(workspace=WORKSPACE, event_id=event_id)
    ) == DELIVERY_ATTEMPT_LIMIT
    assert failing.attempts == DELIVERY_ATTEMPT_LIMIT
    assert action.attempts == 1

    # 第 7 轮仍然是 ``EXHAUSTED``，而不是悄悄从头再来一遍。
    again = tick(
        at=T6, workspace=WORKSPACE, schedules=schedules, notifications=notifications,
        sinks={"failing": failing}, action=action,
    )
    assert again.delivered[0].outcomes[0].state is DeliveryState.EXHAUSTED
    assert failing.attempts == DELIVERY_ATTEMPT_LIMIT


def test_a_sink_asking_to_wait_costs_no_receipt_and_no_research(wired) -> None:
    """出口说了 ``retry_after`` 而那一刻还没到 ⇒ ``WAITING``，**这一轮什么都没写**。

    退避是**出口说的话**，不是这一层编的曲线（``retry_delivery`` 第 5 步）。把
    ``WAITING`` 也记一张收据的实现在清单上显示「这条订阅一直在被投」，而实际上
    我们只是没到点 —— 十次「没到点」会把上限白白吃掉，最后报一次 ``EXHAUSTED``：
    一个**因为遵守退避而耗尽**的通知，读起来像出口一直坏着。
    """
    schedules, notifications, action, _ = wired
    declare_due_schedule(schedules)
    put_subscription(notifications, subscription(sink="failing"), expected_revision=None, at=T0)
    slow = FailingSink(retry_after=timedelta(hours=1))

    tick(
        at=T0, workspace=WORKSPACE, schedules=schedules, notifications=notifications,
        sinks={"failing": slow}, action=action,
    )
    event_id = notifications.events(workspace=WORKSPACE)[0].event_id
    assert len(notifications.receipts(workspace=WORKSPACE, event_id=event_id)) == 1

    waiting = tick(
        at=T1, workspace=WORKSPACE, schedules=schedules, notifications=notifications,
        sinks={"failing": slow}, action=action,
    )
    outcome = waiting.delivered[0].outcomes[0]
    assert outcome.state is DeliveryState.WAITING
    assert outcome.receipt is None
    assert outcome in waiting.delivered[0].pending
    # 一次都没有再问过出口，于是盘上还是那一行。
    assert slow.attempts == 1
    assert len(notifications.receipts(workspace=WORKSPACE, event_id=event_id)) == 1
    assert action.attempts == 1


def test_an_accepted_delivery_settles_and_is_not_re_sent_every_tick(wired) -> None:
    """已经 ``accepted`` 的那条投递不再重投 —— **一次收下的投递不是一件待办**。

    每轮重投的实现会让收件箱变成一份心跳日志（同一条事件一天几十行），而
    ``delivery_receipt`` 那一张表会长成一个与事件数无关的量。
    """
    schedules, notifications, action, inbox_root = wired
    declare_due_schedule(schedules)
    put_subscription(notifications, subscription(sink="inbox"), expected_revision=None, at=T0)
    inbox = InboxSink(inbox_root)

    for moment in (T0, T1, T2):
        report = tick(
            at=moment, workspace=WORKSPACE, schedules=schedules, notifications=notifications,
            sinks={"inbox": inbox}, action=action,
        )
        state = report.delivered[0].outcomes[0].state
        assert state in (DeliveryState.ACCEPTED, DeliveryState.SETTLED)

    event_id = notifications.events(workspace=WORKSPACE)[0].event_id
    receipts = notifications.receipts(workspace=WORKSPACE, event_id=event_id)
    assert [row.attempt for row in receipts] == [1]
    assert len(read_inbox(inbox_root)) == 1
    assert action.attempts == 1


# ----------------------------------------------------------------------
# 三、重投落在同一行上（出口那一侧的幂等）—— 与「重试插新行」是两件事
# ----------------------------------------------------------------------
def test_the_same_delivery_written_twice_does_not_append_a_second_inbox_line(
    wired, tmp_path: Path,
) -> None:
    """收据重试会插新行，而**收件箱那一行只写一次**。

    两件都是对的，而且它们是同一次重试的两侧：收据记得我们问了几次，收件箱记得
    那条事件落在第几行。``ports/notification.py`` 那一节的理由逐字 —— 写盘成功而
    返回的路上崩掉时，调用方会记一条 ``unknown`` 并重试，那次重试**必须**落在同一行
    上，否则「投了两次」在收件箱里读起来是两条事件，而它们是同一条。

    这一条因此用**同一个 sink** 投两次（不经 tick 的那条路，直接调 ``deliver``）：
    走 tick 的话第二次已经是 ``SETTLED``，出口根本不会被问到。
    """
    _, _, _, inbox_root = wired
    inbox = InboxSink(inbox_root)
    event = make_event(dedupe_key="schedule_fired\x1fsched-0001\x1f1\x1ffire-1")
    own = subscription()

    first = inbox.deliver(event, own)
    second = inbox.deliver(event, own)

    assert first.outcome is DeliveryOutcome.ACCEPTED
    assert second.outcome is DeliveryOutcome.ACCEPTED
    assert second.message_ref == first.message_ref
    assert len(read_inbox(inbox_root)) == 1


# ----------------------------------------------------------------------
# 四、火本身失败/跳过时的三档严重度
# ----------------------------------------------------------------------
@pytest.mark.parametrize(
    ("outcome", "expected_event", "expected_severity"),
    [
        (FireOutcome.RUN_ADMITTED, True, NotificationSeverity.MATERIAL_CHANGE),
        (FireOutcome.UNRESOLVED, True, NotificationSeverity.NEEDS_HUMAN),
        (FireOutcome.FAILED, True, NotificationSeverity.NEEDS_HUMAN),
        (FireOutcome.SKIPPED, False, None),
    ],
)
def test_only_a_skipped_action_produces_no_event(
    tmp_path: Path, outcome: FireOutcome, expected_event: bool,
    expected_severity: NotificationSeverity | None,
) -> None:
    """四档结论各记一条事件、各是一档严重度，**只有 ``SKIPPED`` 一条都不记**。

    ``SKIPPED`` 是「动作跑完了而按它自己的判据什么都不用做」—— 给它记一条会让
    「什么都没发生」也占一格通知配额。而 ``UNRESOLVED``/``FAILED`` 记的是
    ``NEEDS_HUMAN``：两者都是**要人看一眼**的，且它们**不重试**
    （``_event_for`` 逐字：重试一条指不到东西的规格只会再失败一次）。
    """
    database = tmp_path / "marketops.db"
    schedules = SqliteScheduleStore(database)
    notifications = SqliteNotificationStore(database)
    declare_due_schedule(schedules)
    action = RecordingScheduleAction(outcome=outcome, next_due_at=LATER)

    tick(
        at=T0, workspace=WORKSPACE, schedules=schedules, notifications=notifications,
        sinks={}, action=action,
    )

    events = notifications.events(workspace=WORKSPACE)
    assert len(events) == (1 if expected_event else 0)
    if expected_event:
        assert events[0].severity is expected_severity
        assert events[0].kind == "schedule_fired"
        assert events[0].occurred_at == T0  # = 到期时刻，不是这次调用的时钟
    # 四档都推进了声明（``SKIPPED`` 也推进：动作跑过了，这条调度该往下排）。
    assert schedules.get_definition("sched-0001", 2) is not None


def test_a_failed_action_is_not_retried_by_the_next_tick(tmp_path: Path) -> None:
    """动作失败是一次**结论**，不是一件待办：下一轮 tick 不会再跑一次。

    这条与「投递失败会重试」摆在一起才说明白那一句「只重试通知」—— 两边的处置相反，
    而它们的差别只在于**是哪一半失败了**。
    """
    database = tmp_path / "marketops.db"
    schedules = SqliteScheduleStore(database)
    notifications = SqliteNotificationStore(database)
    declare_due_schedule(schedules)
    action = RecordingScheduleAction(outcome=FireOutcome.FAILED, next_due_at=LATER)

    first = tick(
        at=T0, workspace=WORKSPACE, schedules=schedules, notifications=notifications,
        sinks={}, action=action,
    )
    assert [receipt.fire.outcome for receipt in first.fired] == [FireOutcome.FAILED]
    assert action.attempts == 1

    second = tick(
        at=T1, workspace=WORKSPACE, schedules=schedules, notifications=notifications,
        sinks={}, action=action,
    )
    assert second.fired == ()
    assert action.attempts == 1
    # 那条结论在盘上，而且**只有一版**：一次失败没有被写成两行。
    fires = schedules.fires(workspace=WORKSPACE, schedule_id="sched-0001")
    assert len(fires) == 1
    assert fires[0].outcome is FireOutcome.FAILED


def test_the_event_survives_the_schedule_being_disabled_afterwards(tmp_path: Path) -> None:
    """点火之后关掉这条调度：**事件与收据一个字节都不动**。

    订阅与投递是分开的两张表，所以「把这条关掉」是一次写订阅，而它**不会**改写任何
    历史事件（``domain/notification.py`` 逐字）。一个「关掉调度就顺手清掉它的事件」的
    实现在这里会把唯一的证据删掉。
    """
    database = tmp_path / "marketops.db"
    schedules = SqliteScheduleStore(database)
    notifications = SqliteNotificationStore(database)
    declare_due_schedule(schedules)
    put_subscription(notifications, subscription(sink="inbox"), expected_revision=None, at=T0)
    action = RecordingScheduleAction(next_due_at=LATER)
    root = tmp_path / "inbox"

    tick(
        at=T0, workspace=WORKSPACE, schedules=schedules, notifications=notifications,
        sinks={"inbox": InboxSink(root)}, action=action,
    )
    event = notifications.events(workspace=WORKSPACE)[0]
    before = event.model_dump()
    receipts_before = notifications.receipts(workspace=WORKSPACE, event_id=event.event_id)
    assert len(receipts_before) == 1

    # 关掉这条调度：指名**盘上当前那一版**（第一次点火把它推到了第 2 版），
    # 写出来是第 3 版。一个「控制当前版」的实现拿不到这一格 —— 控制哪一版是
    # 调用方说的事（``control_schedule`` 逐字）。
    assert schedules.get_definition("sched-0001", 2) is not None
    control_schedule(
        schedules,
        schedule_id="sched-0001",
        schedule_revision=2,
        control=ScheduleControl.DISABLE,
        at=T1,
    )
    report = tick(
        at=T1, workspace=WORKSPACE, schedules=schedules, notifications=notifications,
        sinks={"inbox": InboxSink(root)}, action=action,
    )

    # 关着的调度**不在 ``due()`` 的答案里**，于是 tick 连问都不问它 —— 这是
    # 「驱动只问一处」的形态：它不是挨个问每条调度「你要不要跑」，而是问存储
    # 「谁到点了」，而 ``due()`` 已经把关着的那些剔掉了。
    assert report.fired == ()
    # 于是 ``DISABLED`` 那一档只在**显式指名那一版**点火时读得到（驱动走不到那条路，
    # 而人工点火走得通 —— 它是 ``fire_schedule`` 的第三个返回值）。
    explicit = fire_schedule(
        schedules,
        notifications,
        action,
        schedule_id="sched-0001",
        schedule_revision=3,
        fire_id="manual-1",
        at=T1,
    )
    assert explicit.status is FireStatus.DISABLED
    assert explicit.fire is None and explicit.advanced_to is None

    assert notifications.get_event(event.event_id).model_dump() == before
    assert notifications.receipts(workspace=WORKSPACE, event_id=event.event_id) == receipts_before
    assert action.attempts == 1


# ----------------------------------------------------------------------
# 五、``at`` 是可复算的：同一轮问两次，答案是同一个
# ----------------------------------------------------------------------
def test_the_same_at_asked_twice_gives_the_same_answer(wired) -> None:
    """``tick`` 不读时钟之外的时间、不 sleep、不起线程，所以它是可复算的。

    「驱动到底做了几件事」因此是一个可以被测出来的数（``tick`` 的 docstring 逐字）：
    同一个 ``at`` 上问十遍，动作跑一次、事件记一条、fire 行一行、声明推进一版。

    ## 这条判据**不是**在测「幂等挡住了重复点火」

    两者最容易混，而它们在报告上都长成「第二次什么都没发生」。挡住重复点火的是
    ``schedule_fire`` 的那条主键（判据在
    ``test_a_crash_between_claim_and_settle_is_finished_by_the_next_tick``）；
    这里读的是另一件事：第一次醒来把声明推到了 ``LATER``，于是同一个 ``at`` 上再问
    一次时**根本没有东西到期**。一个内部自己取 ``utc_now()`` 的驱动无法满足下面
    那个数 —— 它在两次调用之间会让盘上的东西变多。
    """
    schedules, notifications, action, _ = wired
    declare_due_schedule(schedules)

    first = tick(
        at=T0, workspace=WORKSPACE, schedules=schedules, notifications=notifications,
        sinks={}, action=action,
    )
    second = tick(
        at=T0, workspace=WORKSPACE, schedules=schedules, notifications=notifications,
        sinks={}, action=action,
    )

    assert first.at == second.at == T0
    assert [receipt.status.value for receipt in first.fired] == ["admitted"]
    # 第二次**一条都不点**：第一次已经把声明推到了 ``LATER``，所以同一个 ``at`` 上
    # 第二次醒来时没有东西到期。这不是「幂等挡住了重复」—— 挡住重复的是 ``claim_fire``
    # 的那条主键（判据在 ``test_a_crash_between_claim_and_settle...``），而这里读的是
    # 另一件事：**同一个 `at` 不会因为问了两遍而多做一件事**。
    assert second.fired == ()
    assert action.attempts == 1
    # 事件还是一条 —— 「同一个 at 上问两次」不是两次业务变化。
    assert len(notifications.events(workspace=WORKSPACE)) == 1
    assert len(schedules.fires(workspace=WORKSPACE, schedule_id="sched-0001")) == 1
    # 声明也没有被推第二次：第二次 tick 什么都没写。
    assert schedules.get_definition("sched-0001", 3) is None


def test_a_crash_between_claim_and_settle_is_finished_by_the_next_tick(
    schedules_and_notifications,
) -> None:
    """进程死在 ``claim_fire`` 与 ``settle_fire`` 之间：下一轮 tick 把尾巴补完。

    补出来的回执 ``replayed=True`` —— 「补做了一条没做完的尾巴」与「刚刚点了火」
    在报告上必须分得开（``_settle_tail`` 的 docstring 逐字：一个写死 ``False`` 的实现
    让两者逐字同形）。而**动作不再跑一次**：占位行已经在盘上，那正是它存在的理由。
    """
    schedules, notifications = schedules_and_notifications
    declare_due_schedule(schedules)
    action = RecordingScheduleAction(next_due_at=LATER)

    definition = schedules.get_definition("sched-0001", 1)
    assert definition is not None and definition.next_due_at is not None
    # 驱动那一侧唯一能商量出来的东西就是盘上那条调度说它该在什么时候醒。
    fire_id = due_fire_id(definition)
    assert fire_id is not None and fire_id.startswith("sched-0001@")
    claimed = schedules.claim_fire(
        ScheduleFire(
            schedule_id="sched-0001",
            schedule_revision=1,
            fire_id=fire_id,
            workspace=WORKSPACE,
            fired_at=T0,
            outcome=FireOutcome.PENDING,
            detail={},
        )
    )
    assert claimed.recorded is True
    assert schedules.get_definition("sched-0001", 1).revision_reason == definition.revision_reason

    report = tick(
        at=T0, workspace=WORKSPACE, schedules=schedules, notifications=notifications,
        sinks={}, action=action,
    )

    receipt = report.fired[0]
    assert receipt.replayed is True
    assert receipt.fire.outcome is FireOutcome.PENDING
    assert receipt.advanced_to is None  # 占位行不推进（``_advance`` 逐字）
    assert action.attempts == 0
    # 占位行还是那一行，而**没有被写成第二个 outcome**。
    fires = schedules.fires(workspace=WORKSPACE, schedule_id="sched-0001")
    assert len(fires) == 1 and fires[0].outcome is FireOutcome.PENDING


def test_the_claim_is_decided_by_the_insert_and_not_by_a_query_first() -> None:
    """**幂等的载体是那条主键**：判据是「插不进去」，不是「先查一查再决定」。

    ``claim_fire`` 的 docstring 逐字：判据是 ``ON CONFLICT DO NOTHING`` 的
    ``rowcount``。而一个「先 SELECT、没有就 INSERT」的实现**在单进程里与它逐字同答**
    —— 同一条 ``BEGIN IMMEDIATE`` 事务里，读与写之间没有别人插得进来 —— 于是它过得了
    上面每一条行为判据（重放回同一行、动作不再跑一次），只在并发下丢一次幂等，
    而那时它丢的是「重放回**同一个**结果」里的那个「同一个」。

    所以这一条问的是**形状**，读的是源码里那几条 SQL 字面量，而不是跑一遍：
    这一次抢对 ``schedule_fire`` 的**第一次接触是一次 INSERT**，而那条 INSERT
    带着 ``ON CONFLICT DO NOTHING``（键才是说「不」的那个东西）。

    边界要说清：它看不见真并发，也不看内存替身（那个替身没有 SQL，读—写是它的
    天然形状）。上面那条重放判据管**结果**，这一条管**载体**，两条缺一不可。
    """
    tree = ast.parse(source_of("marketops.adapters.sqlite.scheduling"))
    claim = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef) and node.name == "claim_fire"
    )
    touched = sorted(
        (
            node
            for node in ast.walk(claim)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "execute"
            and node.args
            and isinstance(node.args[0], ast.Constant)
            and isinstance(node.args[0].value, str)
            and "schedule_fire" in node.args[0].value
        ),
        key=lambda node: node.lineno,
    )
    assert touched, "claim_fire 里没有一条语句碰 schedule_fire —— 判据本身该改。"
    first = touched[0].args[0].value
    assert first.lstrip().startswith("INSERT"), (
        f"抢一次 fire 的第一次接触不是 INSERT 而是读：{first!r}。\n"
        "「先查一查再决定」在单进程里与「插不进去就是有人先写了」同答，"
        "于是它过得了每一条行为判据，只在并发下丢一次幂等。"
    )
    assert "ON CONFLICT DO NOTHING" in first, (
        f"那条 INSERT 不再带 ON CONFLICT DO NOTHING：{first!r}\n"
        "键于是不再是说「不」的那个东西 —— 重写同一行会变成覆盖，而覆盖把"
        "「有人先写了」读成「我刚写的」。"
    )


@pytest.fixture()
def schedules_and_notifications(tmp_path: Path):
    database = tmp_path / "marketops.db"
    return SqliteScheduleStore(database), SqliteNotificationStore(database)


#: 这一份里**没有一处** ``threading`` / ``asyncio`` / ``sched`` / ``signal`` / ``sleep``。
#: 它由 ``tests/test_scheduling_mcp.py`` 里那条源码扫描判据守着（那一条可以今天就跑，
#: 与 MCP 有没有接线无关）。这里留一个常量，是为了让「这一片的驱动是**被调用的**、
#: 不是自己醒来的」这句话在竖向这一层也看得见。
MOMENT_IS_A_PARAMETER = datetime(2026, 9, 29, 1, 0, tzinfo=timezone.utc)


def test_the_driver_is_called_and_never_wakes_itself(wired) -> None:
    """10 轮 tick 里**每一轮都有人给时刻**，而没有任何一轮自己走到下一步。

    这条判据问的是时间轴：把 ``at`` 全部给成同一个时刻，盘上的火不会多一条、事件
    不会多一条 —— 一个自己排下一次唤醒的实现无法满足这一点（它会在两次调用之间把
    ``next_due_at`` 推走）。
    """
    schedules, notifications, action, _ = wired
    declare_due_schedule(schedules)

    for _ in range(10):
        tick(
            at=MOMENT_IS_A_PARAMETER, workspace=WORKSPACE, schedules=schedules,
            notifications=notifications, sinks={}, action=action,
        )

    assert action.attempts == 1
    assert len(notifications.events(workspace=WORKSPACE)) == 1
    assert len(schedules.fires(workspace=WORKSPACE, schedule_id="sched-0001")) == 1
