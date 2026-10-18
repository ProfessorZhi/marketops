"""``ScheduleStore`` 的契约判据 —— 「幂等来自插入失败」的可执行版本。

要守的失败只有一条，但它有四种长相，而**四种都不报错**：

1. **先查再决定**：两个调用方都查到「没有」，然后两条都去跑动作 —— 两轮研究，
   而两份报告各自都写着「这一次是我点的火」；
2. **重放回最后一个结果**：无条件 ``UPDATE`` 让第二次写入抹掉第一次的结论，
   于是「重放回同一个结果」变成「回最后一次改写」；
3. **``get_fire`` 少一格**：只按 ``(schedule_id, fire_id)`` 查，会在「声明改过一版、
   同一个 ``fire_id`` 再点一次」时读到上一版那一行，把一次真的该发生的点火当成
   重放挡掉 —— 而报告上写的是「已经点过火了」；
4. **「有人反复点保存」每次产出一个新版本**：内容摘要那一支漏掉，盘上会留下几十版
   逐字相同的声明，而「这条调度上次改是什么时候」读成一个刚刚的时刻。

## 为什么两个实现跑同一份

``SqliteScheduleStore``（跨进程）与 ``InMemoryScheduleStore``（测试替身）。它在这里
通过而在 SQLite 那里失败，才是「这是一个契约」有意义的样子
（``tests/test_budget_contract.py`` 那一节逐字同一条）。

## 刻意**不**在这里的判据

* **并发**。内存那份没有锁，也不打算有；``claim_fire`` 真的挡得住两个进程靠的是
  ``ON CONFLICT DO NOTHING`` 与 ``BEGIN IMMEDIATE``，那是 SQLite 的性质。这里能测的
  是**顺序**那一半 —— 见 ``tests/test_scheduling_application.py`` 里那条「动作跑的时候
  盘上有没有那一行占位」。
* **版本与迁移**。那是关于**库文件**的，与契约无关（并且 DDL 是 MAIN 独占的）。

## 为什么对着临时文件测

``SqliteScheduleStore`` 收 ``db_path``。用 ``Settings`` 会让每条判据先构造一份完整
配置（还要读环境变量），而调度存储是纯状态机 —— 它不该需要知道自己是哪个 profile
跑出来的（与 ``test_budget_contract.py`` 逐字同一条）。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from marketops.adapters.memory import InMemoryScheduleStore
from marketops.adapters.sqlite.scheduling import SqliteScheduleStore
from marketops.domain.errors import (
    IdempotencyConflictError,
    ScheduleConflictError,
    StoreError,
)
from marketops.domain.scheduling import (
    ActionKind,
    ActionSpec,
    FireOutcome,
    ScheduleDefinition,
    ScheduleFire,
    TriggerType,
)
from marketops.ports.scheduling import ScheduleStore

WORKSPACE = "ws-contract"
OTHER_WORKSPACE = "ws-elsewhere"
SCOPE = "watch-0001"

#: 这一刻。**固定**，不读时钟 —— 一条按 ``utc_now()`` 走的判据会在跨零点那天
#: 给出两个不同的答案，而它红的时候看起来像代码错了。
T0 = datetime(2026, 9, 29, 1, 0, tzinfo=timezone.utc)

#: 契约判据要跑过哪些实现。
IMPLEMENTATION_PARAMS = ("sqlite", "memory")


def test_the_contract_runs_against_every_implementation() -> None:
    """实现清单缩水时「两个实现都通过」会变成「一个实现通过」。

    ``params`` 是一个可以被删掉的名字：删掉 ``memory`` 之后整份契约照样全绿，
    而「这是一个契约」这句话就没有依据了 —— 与
    ``tests/test_evidence_store_contract.py`` 里那条同一个道理。
    """
    assert set(IMPLEMENTATION_PARAMS) == {"sqlite", "memory"}


@pytest.fixture(params=IMPLEMENTATION_PARAMS)
def store(request: pytest.FixtureRequest, tmp_path: Path) -> ScheduleStore:
    """两个实现各跑一遍。新增实现时这里是唯一要改的地方。"""
    if request.param == "sqlite":
        return SqliteScheduleStore(tmp_path / "marketops.db")
    return InMemoryScheduleStore()


# ----------------------------------------------------------------------
# 造对象：三个 helper，其余文件从这里 import
# ----------------------------------------------------------------------
def make_definition(
    *,
    schedule_id: str = "sched-0001",
    revision: int = 1,
    workspace: str = WORKSPACE,
    next_due_at: datetime | None = None,
    enabled: bool = True,
    scope_ref: str = SCOPE,
    timezone_name: str = "+08:00",
    trigger: TriggerType = TriggerType.CALENDAR,
    reason: str = "declared",
    created_at: datetime = T0,
) -> ScheduleDefinition:
    """一版写得出来的调度声明。**判据不在这个 helper 里** —— 它只是省打字。"""
    return ScheduleDefinition(
        schedule_id=schedule_id,
        schedule_revision=revision,
        workspace=workspace,
        owner=workspace,
        scope_ref=scope_ref,
        trigger_type=trigger,
        timezone=timezone_name,
        calendar_rule="daily",
        window=None,
        next_due_at=next_due_at,
        action_spec=ActionSpec(kind=ActionKind.RESEARCH_RUN, target_ref=SCOPE),
        budget_policy_ref=None,
        notification_policy_ref=None,
        enabled=enabled,
        revision_reason=reason,
        created_at=created_at,
        updated_at=created_at,
    )


def make_fire(
    *,
    schedule_id: str = "sched-0001",
    revision: int = 1,
    fire_id: str = "fire-1",
    workspace: str = WORKSPACE,
    fired_at: datetime = T0,
    outcome: FireOutcome = FireOutcome.PENDING,
) -> ScheduleFire:
    """一条 fire 行。``PENDING`` 是**调用方写下的占位**，默认值刻意取它。"""
    return ScheduleFire(
        schedule_id=schedule_id,
        schedule_revision=revision,
        fire_id=fire_id,
        workspace=workspace,
        fired_at=fired_at,
        outcome=outcome,
        message=None,
        next_due_at=None,
        detail={},
    )


def put(store: ScheduleStore, definition: ScheduleDefinition):
    """按 ``write_schedule`` 的那条推法写一版（``rev 1`` ⇒ 基准 ``None``）。"""
    return store.put_definition(
        definition,
        expected_revision=None if definition.schedule_revision == 1 else definition.schedule_revision - 1,
    )


# ----------------------------------------------------------------------
# 声明：一版一行，CAS 就是那条主键
# ----------------------------------------------------------------------
def test_the_first_definition_lands_and_the_second_claim_of_first_collides(
    store: ScheduleStore,
) -> None:
    """第 1 版写得进去；**再一次声称「这是首次声明」被拒**。

    第二次安静地写成第 2 版是最坏的一种「成功」：它以「声明」的名义覆盖掉别人
    的一条调度，而报告上只显示版本号加了一。
    """
    written = put(store, make_definition())
    assert written.revision_written == 1, written
    assert store.get_definition("sched-0001", 1) is not None

    with pytest.raises(ScheduleConflictError):
        put(store, make_definition())


def test_a_stale_base_is_refused_and_names_both_versions(store: ScheduleStore) -> None:
    """手上那一版不是盘上当前那一版 ⇒ 拒绝，而且**两版都点名**。"""
    put(store, make_definition())
    put(store, make_definition(revision=2, scope_ref="watch-0002"))

    with pytest.raises(ScheduleConflictError) as caught:
        store.put_definition(
            make_definition(revision=2, scope_ref="watch-0009"), expected_revision=1
        )
    message = str(caught.value)
    assert "1" in message and "2" in message, message


def test_a_version_jump_leaves_a_hole_and_is_refused(store: ScheduleStore) -> None:
    """跳版（基准 0 → 第 3 版）被拒：那个空洞里，一个拿旧版本的写者会成功。"""
    put(store, make_definition())
    with pytest.raises(StoreError):
        store.put_definition(make_definition(revision=3), expected_revision=0)


def test_an_identical_content_new_revision_writes_no_row(store: ScheduleStore) -> None:
    """内容与当前版逐字相同时**不产生新版本** —— 「有人反复点保存」是 no-op。

    它只在调用方**推进了版本号**时成立（``expected_revision=1`` 而交上来第 2 版）：
    这才是「保存」这个动作的形状，而它与「忘了推进版本号」是两件事。
    """
    put(store, make_definition())
    written = put(store, make_definition(revision=2, reason="declared"))

    assert written.revision_written is None, written
    assert [row.schedule_revision for row in store.definitions(workspace=WORKSPACE)] == [1]
    # 但 CAS 照旧成立：第 2 版没有落盘，所以「当前版」还是 1。
    assert store.get_definition("sched-0001", 2) is None


def test_a_different_reason_alone_does_not_make_a_new_content(store: ScheduleStore) -> None:
    """``revision_reason`` 不在内容摘要里 —— 它不同**不**足以产生新版本。

    那是一个有代价的选择（``content_digest`` 的 docstring 逐字）：一条已经关掉的
    调度上 disable 与 pause 的意图因此不留痕。代价与收益不对称才这么选。
    """
    put(store, make_definition())
    written = put(store, make_definition(revision=2, reason="controlled:pause"))
    assert written.revision_written is None, written


def test_definitions_list_only_the_current_revision(store: ScheduleStore) -> None:
    """改过一次范围之后，清单上是**一行**（当前版），不是两行。"""
    put(store, make_definition())
    put(store, make_definition(revision=2, scope_ref="watch-0002"))

    rows = store.definitions(workspace=WORKSPACE)
    assert [(row.schedule_id, row.schedule_revision) for row in rows] == [("sched-0001", 2)]


def test_definitions_are_scoped_to_one_workspace(store: ScheduleStore) -> None:
    """另一个工作区的那一条读不出来。一个库只装一个工作区的产出。"""
    put(store, make_definition())
    assert store.definitions(workspace=OTHER_WORKSPACE) == ()


# ----------------------------------------------------------------------
# 到期：三格判据，两处各写一遍的那天没有任何一处会红
# ----------------------------------------------------------------------
def test_due_is_empty_before_the_moment_and_lists_after(store: ScheduleStore) -> None:
    """这一刻之前是空的，之后列出来。**空与「没有这一条」在这里同形**，
    所以下面每一格都有自己的判据。"""
    put(store, make_definition(next_due_at=T0 + timedelta(hours=1)))

    assert store.due(at=T0) == ()
    assert [row.schedule_id for row in store.due(at=T0 + timedelta(hours=1))] == ["sched-0001"]
    assert [row.schedule_id for row in store.due(at=T0 + timedelta(days=1))] == ["sched-0001"]


def test_a_disabled_schedule_is_never_due(store: ScheduleStore) -> None:
    """关着的调度不到期 —— 而它与「还没到点」在 ``due()`` 的返回值上同形，
    所以这两条判据必须分开写。"""
    put(store, make_definition(next_due_at=T0 - timedelta(hours=1), enabled=False))
    assert store.due(at=T0) == ()


def test_a_schedule_without_a_next_moment_is_never_due(store: ScheduleStore) -> None:
    """没有下一次的调度不到期（一次跑完的 one_shot）。

    它与「到点了」在报告上不同形，而它与「被关掉了」在 ``due()`` 上同形 ——
    所以这一条与上面那条是两个用例而不是一个。
    """
    put(store, make_definition(next_due_at=None))
    assert store.due(at=T0 + timedelta(days=3650)) == ()


def test_due_is_ordered_by_the_moment_then_the_id(store: ScheduleStore) -> None:
    """``(next_due_at, schedule_id)`` 升序 —— 一个无序的集合让「这一批」不可复现。"""
    put(store, make_definition(schedule_id="sched-b", next_due_at=T0 - timedelta(hours=1)))
    put(store, make_definition(schedule_id="sched-a", next_due_at=T0 - timedelta(hours=2)))
    put(store, make_definition(schedule_id="sched-c", next_due_at=T0 - timedelta(hours=1)))

    assert [row.schedule_id for row in store.due(at=T0)] == ["sched-a", "sched-b", "sched-c"]


# ----------------------------------------------------------------------
# 抢下那一次 fire：三条出口
# ----------------------------------------------------------------------
def test_claim_then_replay_returns_the_stored_row_and_does_not_write(store: ScheduleStore) -> None:
    """第一次 ``recorded=True``；同一个键第二次 ``recorded=False`` 且回**盘上那一行**。

    回的不是本次交上来那一条：第二次交上来的可能带着另一个结论，而「重放」这个词
    说的是「回第一次那个」。
    """
    first = store.claim_fire(make_fire())
    assert first.recorded is True

    second = store.claim_fire(make_fire())
    assert second.recorded is False
    assert second.fire.outcome is FireOutcome.PENDING, second.fire


def test_the_same_fire_id_at_another_revision_is_a_different_fire(store: ScheduleStore) -> None:
    """``schedule_revision`` 在主键里 —— 这是「改过一版再点一次」那一半。

    **只按 ``(schedule_id, fire_id)`` 做主键的实现在这里会挡住第二次点火**，
    而它报出来的是「已经点过火了」。
    """
    assert store.claim_fire(make_fire(revision=1, fire_id="fire-x")).recorded is True
    assert store.claim_fire(make_fire(revision=2, fire_id="fire-x")).recorded is True

    # 而 ``get_fire`` 必须同样带三格：少一格的实现在这里读到上一版那一行。
    assert store.get_fire("sched-0001", 2, "fire-x") is not None
    assert store.get_fire("sched-0001", 1, "fire-x") is not None
    assert store.get_fire("sched-0001", 3, "fire-x") is None


def test_the_same_key_for_another_fire_is_an_idempotency_conflict(store: ScheduleStore) -> None:
    """同一个 ``fire_id`` 配**另一次** fire（``fired_at`` 不同）⇒ 停线。

    安静地留下第一条会让「重放回同一个结果」变成「回第一次那个」—— 那是两件不同
    的事，只是报告上写着同一句话。
    """
    store.claim_fire(make_fire(fired_at=T0))
    with pytest.raises(IdempotencyConflictError):
        store.claim_fire(make_fire(fired_at=T0 + timedelta(seconds=1)))


def test_a_fire_from_another_workspace_never_lands(store: ScheduleStore) -> None:
    """别的工作区的 fire 写不进去，而且**一行都不留**。

    ## 它红在哪一种实现上

    真正会在这里出错的实现是「按 ``(schedule_id, fire_id)`` 抢、不判工作区」的那种：
    它会把别的租户的一次点火当成「已经点过了」记在自己的账上，而报告上写着
    「这次是重放」。

    ## 为什么断言的是 ``StoreError`` 而不是 ``IdempotencyConflictError``

    两个实现在比较 ``workspace`` 之前**先**撞上工作区绑定（``bind_workspace``）——
    一个状态库只装一个工作区，这是更靠外的一道闸门，所以 ``claim_fire`` 里那段
    「同一个 fire_id、另一个 workspace」的比较**在公开接口上到不了**
    （``R`` 报告里如实记了这一条：它是一段没有判据能碰到的兜底代码，留着是为了
    让「键里没有 workspace」这件事在读过实现的人眼里不是漏掉的一格）。
    断言绑定那一族的错误，才是这一格现在真的会给出的答案。
    """
    store.claim_fire(make_fire())
    with pytest.raises(StoreError):
        store.claim_fire(make_fire(workspace=OTHER_WORKSPACE))

    # 真正承重的那半句：**一点痕迹都没留下**。
    assert store.fires(workspace=OTHER_WORKSPACE) == ()
    assert len(store.fires(workspace=WORKSPACE)) == 1


# ----------------------------------------------------------------------
# 结论：一次为限
# ----------------------------------------------------------------------
def test_settle_writes_once_and_the_second_write_returns_the_first_conclusion(
    store: ScheduleStore,
) -> None:
    """结论只写一次。第二次带着**另一个**结论来，回的是第一次那个。

    一个无条件 ``UPDATE`` 的实现会让第二次把第一次抹掉，而「重放回同一个结果」
    在那之后就只是一句注释。
    """
    store.claim_fire(make_fire())
    settled = store.settle_fire(make_fire(outcome=FireOutcome.RUN_ADMITTED))
    assert settled.recorded is True

    again = store.settle_fire(make_fire(outcome=FireOutcome.FAILED))
    assert again.recorded is False
    assert again.fire.outcome is FireOutcome.RUN_ADMITTED, again.fire
    assert store.get_fire("sched-0001", 1, "fire-1").outcome is FireOutcome.RUN_ADMITTED


def test_settle_refuses_a_placeholder(store: ScheduleStore) -> None:
    """占位不是一个结论 ⇒ ``ValueError``（``domain`` 那一侧已经先答过一次）。"""
    store.claim_fire(make_fire())
    with pytest.raises(ValueError):
        store.settle_fire(make_fire())


def test_settle_without_a_claim_is_a_store_error(store: ScheduleStore) -> None:
    """没有占位就写结论 ⇒ ``StoreError``：那条路没有经过「谁跑了动作」那道闸门。"""
    with pytest.raises(StoreError):
        store.settle_fire(make_fire(outcome=FireOutcome.RUN_ADMITTED))


# ----------------------------------------------------------------------
# 清单：fires
# ----------------------------------------------------------------------
def test_fires_are_ordered_and_scoped(store: ScheduleStore) -> None:
    """按 ``(fired_at, schedule_id, fire_id)`` 升序，并按工作区与 ``schedule_id`` 筛。"""
    store.claim_fire(make_fire(schedule_id="sched-b", fire_id="f1", fired_at=T0))
    store.claim_fire(
        make_fire(schedule_id="sched-a", fire_id="f2", fired_at=T0 + timedelta(hours=1))
    )

    assert [(f.schedule_id, f.fire_id) for f in store.fires(workspace=WORKSPACE)] == [
        ("sched-b", "f1"),
        ("sched-a", "f2"),
    ]
    assert [f.schedule_id for f in store.fires(workspace=WORKSPACE, schedule_id="sched-a")] == [
        "sched-a"
    ]
    assert store.fires(workspace=OTHER_WORKSPACE) == ()
