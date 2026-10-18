"""``application/scheduling.py`` 的次序判据 —— 「先判到期、再抢行、再跑动作」。

这一份测的不是「函数回了什么」，而是**端口被调了几次、在什么时候被调**。
理由写在 ``ports/scheduling.py::ScheduleAction`` 那一节：一条把 due check 挪到动作
之后的实现在返回值上**不留痕迹**（报告里照样 ``fired=0``），它唯一的痕迹是那次
真实的调用 —— 所以判据必须数调用次数。

四条次序，每一条都有一个「看着一样」的错法：

1. **先判到期、再花钱**（空队列不调用 provider）。错法是「先跑再判」，而它在
   回执上与正解同形。
2. **抢占位在动作之前**。错法是把 ``claim_fire`` 挪到动作之后 —— 两个调用方并发
   时各自都查不到行、各自都跑一遍动作，两轮研究。这一条用
   ``RecordingScheduleAction`` 的 ``observer`` 观测：动作**跑的时候**盘上那一行
   在不在。
3. **同一个 ``(schedule_id, revision, fire_id)`` 第二次不跑动作**。错法是「跑完再
   回同一个结果」——在报告上与真的挡住了重跑逐字同形。
4. **改过一版之后同一个 ``fire_id`` **不**被挡住**（``schedule_revision`` 在键里
   的那一半）。错法是把键做成 ``(schedule_id, fire_id)``，那样一次正常的
   「改过声明之后再点一次」会被报成「已经点过火了」。

最后一段是那两件收尾（记事件、推进声明）的**幂等与可恢复**：一次崩溃停在
「结论写了、收尾没做」时，重放要把没做完的做完，而且**不重跑动作**。
"""

from __future__ import annotations

from datetime import timedelta

import pytest

from marketops.adapters.memory import (
    InMemoryNotificationStore,
    InMemoryScheduleStore,
    RecordingScheduleAction,
)
from marketops.application.scheduling import (
    fire_dedupe_key,
    fire_schedule,
    tick,
    write_schedule,
)
from marketops.domain.errors import ScheduleConflictError
from marketops.domain.scheduling import (
    ActionReport,
    FireOutcome,
    FireStatus,
    ScheduleControl,
    ScheduleFire,
)
from tests.test_scheduling_store_contract import T0, WORKSPACE, make_definition

LATER = T0 + timedelta(hours=1)


@pytest.fixture()
def schedules() -> InMemoryScheduleStore:
    return InMemoryScheduleStore()


@pytest.fixture()
def notifications() -> InMemoryNotificationStore:
    return InMemoryNotificationStore()


def declare(
    schedules: InMemoryScheduleStore, *, next_due_at=LATER, enabled: bool = True, revision: int = 1
):
    """写下一条声明。``write_schedule`` 是唯一入口，判据不在这里重写一遍。"""
    return write_schedule(
        schedules,
        make_definition(revision=revision, next_due_at=next_due_at, enabled=enabled),
        at=T0,
    )


# ----------------------------------------------------------------------
# 1. 空队列不花钱
# ----------------------------------------------------------------------
def test_a_queue_that_is_not_due_costs_nothing(
    schedules: InMemoryScheduleStore, notifications: InMemoryNotificationStore
) -> None:
    """没到点 ⇒ ``NOT_DUE``，**一次 provider 调用都没有**，也没有 fire 行。

    「报告里 ``fired=0``」不是这条判据 —— 那是返回值，而先跑再判的实现照样给出
    ``fired=0``。承重的是 ``action.attempts == 0`` 与盘上一条 fire 行都没有。
    """
    declare(schedules, next_due_at=LATER)
    action = RecordingScheduleAction(next_due_at=LATER)

    receipt = fire_schedule(
        schedules,
        notifications,
        action,
        schedule_id="sched-0001",
        schedule_revision=1,
        fire_id="fire-1",
        at=T0,
    )

    assert receipt.status is FireStatus.NOT_DUE
    assert receipt.fire is None
    assert action.attempts == 0, action.perform_calls
    assert schedules.fires(workspace=WORKSPACE) == ()
    assert notifications.events(workspace=WORKSPACE) == ()


def test_a_disabled_schedule_costs_nothing_and_says_so(
    schedules: InMemoryScheduleStore, notifications: InMemoryNotificationStore
) -> None:
    """关着的调度：``DISABLED``，与 ``NOT_DUE`` **分开报**（处置完全不同）。"""
    declare(schedules, next_due_at=T0 - timedelta(hours=1), enabled=False)
    action = RecordingScheduleAction(next_due_at=LATER)

    receipt = fire_schedule(
        schedules,
        notifications,
        action,
        schedule_id="sched-0001",
        schedule_revision=1,
        fire_id="fire-1",
        at=T0,
    )

    assert receipt.status is FireStatus.DISABLED
    assert action.attempts == 0
    assert schedules.fires(workspace=WORKSPACE) == ()


def test_firing_a_revision_that_is_not_on_disk_writes_nothing(
    schedules: InMemoryScheduleStore, notifications: InMemoryNotificationStore
) -> None:
    """指名的那一版不在盘上 ⇒ 冲突，**一个字节都没写**。"""
    declare(schedules, next_due_at=LATER)
    action = RecordingScheduleAction(next_due_at=LATER)

    with pytest.raises(ScheduleConflictError):
        fire_schedule(
            schedules,
            notifications,
            action,
            schedule_id="sched-0001",
            schedule_revision=7,
            fire_id="fire-1",
            at=T0,
        )

    assert action.attempts == 0
    assert schedules.fires(workspace=WORKSPACE) == ()


# ----------------------------------------------------------------------
# 2. 抢占位在动作之前
# ----------------------------------------------------------------------
def test_the_placeholder_is_on_disk_while_the_action_runs(
    schedules: InMemoryScheduleStore, notifications: InMemoryNotificationStore
) -> None:
    """``observer`` 在**动作跑的时候**读盘，读到的是那一行占位。

    「幂等来自插入失败」这句话的**唯一**可执行形态就在这里：一个把 ``claim_fire``
    挪到 ``perform`` 之后的实现，在这条判据里读到的是 ``None``——而它在报告上
    一切正常（回执照样 ``ADMITTED``、事件照样一条）。
    """
    declare(schedules, next_due_at=T0 - timedelta(hours=1))
    seen: list[ScheduleFire | None] = []
    action = RecordingScheduleAction(
        next_due_at=LATER,
        observer=lambda definition, fire_id: seen.append(
            schedules.get_fire(definition.schedule_id, definition.schedule_revision, fire_id)
        ),
    )

    fire_schedule(
        schedules,
        notifications,
        action,
        schedule_id="sched-0001",
        schedule_revision=1,
        fire_id="fire-1",
        at=T0,
    )

    assert len(seen) == 1
    assert seen[0] is not None, "动作跑的时候盘上必须先有一行占位"
    assert seen[0].pending is True, seen[0]
    # 而结论是在动作**之后**才写上的。
    assert schedules.get_fire("sched-0001", 1, "fire-1").outcome is FireOutcome.RUN_ADMITTED


# ----------------------------------------------------------------------
# 3. 同一个键第二次不跑动作
# ----------------------------------------------------------------------
def test_the_same_fire_id_runs_the_action_once(
    schedules: InMemoryScheduleStore, notifications: InMemoryNotificationStore
) -> None:
    """同一个 ``(schedule_id, revision, fire_id)`` 点两次：**动作只跑一次**。

    第二次回的是**盘上那一行**（同一个结论），而不是一份重新算出来的报告。
    """
    declare(schedules, next_due_at=T0 - timedelta(hours=1))
    action = RecordingScheduleAction(next_due_at=LATER)

    first = fire_schedule(
        schedules,
        notifications,
        action,
        schedule_id="sched-0001",
        schedule_revision=1,
        fire_id="fire-1",
        at=T0,
    )
    second = fire_schedule(
        schedules,
        notifications,
        action,
        schedule_id="sched-0001",
        schedule_revision=1,
        fire_id="fire-1",
        at=T0 + timedelta(seconds=30),
    )

    assert first.replayed is False and second.replayed is True
    assert second.fire is not None and second.fire.outcome is FireOutcome.RUN_ADMITTED
    assert action.attempts == 1, action.perform_calls
    assert len(schedules.fires(workspace=WORKSPACE)) == 1
    # 重放**不建第二条事件**：一条业务变化只生成一次。
    assert len(notifications.events(workspace=WORKSPACE)) == 1


def test_a_re_fire_after_an_edit_is_not_blocked(
    schedules: InMemoryScheduleStore, notifications: InMemoryNotificationStore
) -> None:
    """改过一版之后，同一个 ``fire_id`` **不**被挡住 —— ``schedule_revision`` 在键里。

    把幂等键做成 ``(schedule_id, fire_id)`` 的实现会在这里回一个
    「已经点过火了」，而那是一次**真的该发生的**点火被挡掉：声明改了，到期时刻也
    重新算过，这一次的 ``next_due_at`` 是那一版自己说的。
    """
    declare(schedules, next_due_at=T0 - timedelta(hours=1))
    action = RecordingScheduleAction(next_due_at=LATER)

    first = fire_schedule(
        schedules,
        notifications,
        action,
        schedule_id="sched-0001",
        schedule_revision=1,
        fire_id="fire-same",
        at=T0,
    )
    assert first.replayed is False
    assert first.advanced_to == 2
    assert len(schedules.fires(workspace=WORKSPACE)) == 1

    # 一次「改声明」：换掉范围、并把 ``next_due_at`` 拉回过去（仍然到期）⇒ 第 3 版。
    write_schedule(
        schedules,
        make_definition(revision=3, next_due_at=T0 - timedelta(hours=1), scope_ref="watch-0002"),
        at=T0 + timedelta(minutes=1),
    )

    second = fire_schedule(
        schedules,
        notifications,
        action,
        schedule_id="sched-0001",
        schedule_revision=3,
        fire_id="fire-same",
        at=T0 + timedelta(minutes=1),
    )

    assert second.replayed is False, "第 2 版上的同一个 fire_id 是**另一把**没用过的键"
    assert action.attempts == 2, action.perform_calls
    assert len(schedules.fires(workspace=WORKSPACE)) == 2


# ----------------------------------------------------------------------
# 4. 失败是一条结果
# ----------------------------------------------------------------------
def test_an_action_that_raises_becomes_a_failed_row_not_a_retry(
    schedules: InMemoryScheduleStore, notifications: InMemoryNotificationStore
) -> None:
    """动作抛了 ⇒ 记成 ``FAILED`` 的**一行**，重放回这一格而**不再跑一次**。

    一个让异常逃出去的实现在报告上显示「这次调用失败了」，而盘上那一行还停在
    ``pending``：下一次醒来它会读到一个「有人在跑」的假象 —— 而那个假象永远不会
    自己消失。
    """
    declare(schedules, next_due_at=T0 - timedelta(hours=1))
    action = RecordingScheduleAction(raises=RuntimeError("上游 503"))

    receipt = fire_schedule(
        schedules,
        notifications,
        action,
        schedule_id="sched-0001",
        schedule_revision=1,
        fire_id="fire-1",
        at=T0,
    )

    assert receipt.status is FireStatus.ADMITTED
    assert receipt.fire is not None and receipt.fire.outcome is FireOutcome.FAILED
    assert "RuntimeError" in (receipt.fire.message or "")

    again = fire_schedule(
        schedules,
        notifications,
        action,
        schedule_id="sched-0001",
        schedule_revision=1,
        fire_id="fire-1",
        at=T0 + timedelta(minutes=1),
    )
    assert again.fire is not None and again.fire.outcome is FireOutcome.FAILED
    assert action.attempts == 1, action.perform_calls


def test_a_rule_with_no_next_moment_closes_the_schedule(
    schedules: InMemoryScheduleStore, notifications: InMemoryNotificationStore
) -> None:
    """动作回 ``next_due_at=None`` ⇒ 这条调度被**关掉**，而且理由写在盘上。

    不关掉它，它会永远停在 ``next_due_at`` 上，于是每一次醒来都拿同一个 ``fire_id``
    去撞同一行主键、回同一个「已经点过了」—— 报告上一切正常。
    """
    declare(schedules, next_due_at=T0 - timedelta(hours=1))
    action = RecordingScheduleAction(next_due_at=None)

    receipt = fire_schedule(
        schedules,
        notifications,
        action,
        schedule_id="sched-0001",
        schedule_revision=1,
        fire_id="fire-1",
        at=T0,
    )

    assert receipt.advanced_to == 2
    advanced = schedules.get_definition("sched-0001", 2)
    assert advanced is not None
    assert advanced.enabled is False
    assert advanced.next_due_at is None
    assert advanced.revision_reason == "fired:no_next_due"

    # 而第 2 版上再点一次：``DISABLED``，动作一次都不跑。
    after = fire_schedule(
        schedules,
        notifications,
        action,
        schedule_id="sched-0001",
        schedule_revision=2,
        fire_id="fire-2",
        at=T0 + timedelta(days=1),
    )
    assert after.status is FireStatus.DISABLED
    assert action.attempts == 1


def test_a_rule_that_asks_for_now_again_is_visible_as_a_new_revision(
    schedules: InMemoryScheduleStore, notifications: InMemoryNotificationStore
) -> None:
    """动作回一个**不晚于**刚才那一刻的时刻 ⇒ 下一版仍然到期，于是它会再点一次。

    这一层**不替调用方纠正**（把 ``now`` 悄悄换成 ``now + 一刻钟`` 是在编一个策略）。
    它做的是让这件事在盘上看得见：版本号每一轮加一、``next_due_at`` 一直等于现在。
    看得见的循环是可以被人叫停的，静默的停摆不能 —— 所以这条判据数的正是
    「看得见的第二轮」。
    """
    declare(schedules, next_due_at=T0 - timedelta(hours=1))
    action = RecordingScheduleAction(next_due_at=T0)  # 「马上再来」

    fire_schedule(
        schedules,
        notifications,
        action,
        schedule_id="sched-0001",
        schedule_revision=1,
        fire_id="fire-1",
        at=T0,
    )
    advanced = schedules.get_definition("sched-0001", 2)
    assert advanced is not None and advanced.next_due_at == T0 and advanced.enabled is True
    assert advanced.revision_reason == "fired:advanced"

    report = tick(
        at=T0,
        workspace=WORKSPACE,
        schedules=schedules,
        notifications=notifications,
        sinks={},
        action=action,
    )

    assert [item.schedule_revision for item in report.fired] == [2]
    assert action.attempts == 2


# ----------------------------------------------------------------------
# 5. 收尾：幂等，而且可恢复
# ----------------------------------------------------------------------
def test_a_replay_finishes_the_tail_that_the_crash_left_undone(
    schedules: InMemoryScheduleStore, notifications: InMemoryNotificationStore
) -> None:
    """结论写了、收尾没做 ⇒ 重放把没做完的**做完**，而动作**不重跑**。

    「没做完」的判据是确定性的：下一版还没被写下。它成立时事件也一定还没记
    （两件在同一条尾巴上），而两件都幂等，所以再做一次是对的。
    """
    declare(schedules, next_due_at=T0 - timedelta(hours=1))
    action = RecordingScheduleAction(next_due_at=LATER)

    # 模拟一次崩溃：占位与结论都写了，收尾（记事件 + 推进声明）一步都没做。
    schedules.claim_fire(
        ScheduleFire(
            schedule_id="sched-0001",
            schedule_revision=1,
            fire_id="fire-1",
            workspace=WORKSPACE,
            # **这一刻该醒来的时刻**，不是「崩溃那一刻」：fire 行的 ``fired_at``
            # 是这一次 fire 描述的那一刻，而重放要认得出它是同一次。
            fired_at=T0 - timedelta(hours=1),
            outcome=FireOutcome.PENDING,
            detail={},
        )
    )
    pending = schedules.get_fire("sched-0001", 1, "fire-1")
    assert pending is not None
    schedules.settle_fire(
        pending.settled(ActionReport(outcome=FireOutcome.RUN_ADMITTED, next_due_at=LATER), at=T0)
    )
    assert notifications.events(workspace=WORKSPACE) == ()
    assert schedules.get_definition("sched-0001", 2) is None

    receipt = fire_schedule(
        schedules,
        notifications,
        action,
        schedule_id="sched-0001",
        schedule_revision=1,
        fire_id="fire-1",
        at=T0 + timedelta(minutes=1),
    )

    assert receipt.replayed is True
    assert receipt.fire is not None and receipt.fire.outcome is FireOutcome.RUN_ADMITTED
    assert receipt.advanced_to == 2, "没做完的那一半尾巴必须被补上"
    assert len(notifications.events(workspace=WORKSPACE)) == 1
    # 承重的一格：补尾巴**不是**一次重跑。
    assert action.attempts == 0, action.perform_calls


def test_a_replay_does_not_touch_a_revision_someone_else_wrote(
    schedules: InMemoryScheduleStore, notifications: InMemoryNotificationStore
) -> None:
    """下一次收尾时下一版**已经在了** ⇒ 不推进。

    那可能是另一次收尾，也可能是人在那之后的一次控制 —— 后一种情况下不推进才是
    对的：一次显式控制不该被一个落后的收尾盖掉。
    """
    declare(schedules, next_due_at=T0 - timedelta(hours=1))
    action = RecordingScheduleAction(next_due_at=LATER)

    fire_schedule(
        schedules,
        notifications,
        action,
        schedule_id="sched-0001",
        schedule_revision=1,
        fire_id="fire-1",
        at=T0,
    )
    # 人在那之后控制了一次：第 3 版（关掉它）。
    controlled = schedules.get_definition("sched-0001", 2)
    assert controlled is not None
    schedules.put_definition(
        controlled.controlled(
            ScheduleControl.DISABLE, at=T0 + timedelta(minutes=1), reason="人手按的"
        ),
        expected_revision=2,
    )

    replay = fire_schedule(
        schedules,
        notifications,
        action,
        schedule_id="sched-0001",
        schedule_revision=1,
        fire_id="fire-1",
        at=T0 + timedelta(minutes=2),
    )

    assert replay.replayed is True
    assert replay.advanced_to is None
    kept = schedules.get_definition("sched-0001", 3)
    assert kept is not None and kept.enabled is False
    assert kept.revision_reason.startswith("controlled:disable")
    # 事件没有被记第二遍。
    assert len(notifications.events(workspace=WORKSPACE)) == 1


# ----------------------------------------------------------------------
# 6. 去重键由**处置点**算
# ----------------------------------------------------------------------
def test_the_event_dedupe_key_is_the_one_the_disposal_point_computed(
    schedules: InMemoryScheduleStore, notifications: InMemoryNotificationStore
) -> None:
    """事件上的 ``dedupe_key`` 是本层算出来的那一个，而且它不含 sink。

    通知层算不出第二个去重域 —— 一个按 ``(事件, sink)`` 去重的写法在那里连一个
    sink 都拿不到；判据因此是「键的组成里没有 sink 的位置」。
    """
    declare(schedules, next_due_at=T0 - timedelta(hours=1))
    action = RecordingScheduleAction(next_due_at=LATER)

    fire_schedule(
        schedules,
        notifications,
        action,
        schedule_id="sched-0001",
        schedule_revision=1,
        fire_id="fire-1",
        at=T0,
    )

    events = notifications.events(workspace=WORKSPACE)
    assert len(events) == 1
    stored = schedules.get_fire("sched-0001", 1, "fire-1")
    assert stored is not None
    assert events[0].dedupe_key == fire_dedupe_key(stored)
    assert "inbox" not in events[0].dedupe_key
    # 两段用 SEP 拼（不是 ``:``），所以带冒号的 ``fire_id`` 拼不出同一个键。
    assert fire_dedupe_key(stored).count("\x1f") == 3


def test_a_skipped_action_records_no_event(
    schedules: InMemoryScheduleStore, notifications: InMemoryNotificationStore
) -> None:
    """动作说「什么都不用做」⇒ **没有业务变化就没有事件**。

    给它记一条会让「什么都没发生」也占一格通知配额，而 ``ROUTINE`` 那一档存在的
    理由正是压掉这种噪声。
    """
    declare(schedules, next_due_at=T0 - timedelta(hours=1))
    action = RecordingScheduleAction(outcome=FireOutcome.SKIPPED, next_due_at=LATER)

    receipt = fire_schedule(
        schedules,
        notifications,
        action,
        schedule_id="sched-0001",
        schedule_revision=1,
        fire_id="fire-1",
        at=T0,
    )

    assert receipt.fire is not None and receipt.fire.outcome is FireOutcome.SKIPPED
    assert notifications.events(workspace=WORKSPACE) == ()
    # 但声明照旧推进：这一次点火本身是发生过的。
    assert receipt.advanced_to == 2
