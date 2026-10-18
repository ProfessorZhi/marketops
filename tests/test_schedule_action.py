"""真的 ``ScheduleAction``：一次到期 → 一次**真正的 Run 受理**。

## 这一份守的是什么

``tests/test_scheduling_mcp.py`` 量的是「``perform`` 被调用几次」—— 那是 D 够得着的
量具、也是对的量具，但它证明不了 §18 要的那一句：

    fire #1 → **one Run**；同一 fire 重放 → 同一结果 / **不新增 Run**。

那个 Run 今天由这一片造出来，所以判据必须落在**真的存储**上（``research_run`` 里
有几行），而不是落在返回值上。「回了一个 ``RUN_ADMITTED``」与「开出了一轮研究」在
回执上同形，而只有一个是这一条要的。

## 四组

1. **一条调度真的开出一轮 Run** —— 工作区来自**盘上那一版 Scene**；
2. **承重点 1～4** —— 同一 fire 重放只受理一次；声明改版之后同一个 ``fire_id``
   是**另一次** fire；一次点火只受理一次；不顺手建下一次的 Run；工作区来自盘；
3. **另外三个 ``ActionKind`` 失败关闭** —— 回的是闭集里那个明确的非成功值；
4. **解析不出来 = ``UNRESOLVED``** —— 目标不在、声明拼不出来、模式不是自动那两档。

判据读的 ``_rows`` 是**库**，不是处理器返回的对象：一条「先写一行再报错」的实现
在回执上与真拒绝一模一样（``tests/test_mcp_runs.py::_rows`` 逐字）。
"""

from __future__ import annotations

import ast
import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest

from marketops.adapters.mcp_server.schedule_action import (
    RESEARCH_SCHEDULE_ACTION_PARAMS,
    ResearchScheduleAction,
    _next_due_at,
    _Unresolvable,
    run_key_of,
)
from marketops.adapters.memory import InMemoryNotificationStore, InMemoryScheduleStore
from marketops.adapters.sqlite.ledger import SqliteBudgetLedger
from marketops.adapters.sqlite.runs import SqliteRunStore
from marketops.adapters.sqlite.scenes import SqliteMaintenanceStore, SqliteSceneStore
from marketops.application.scheduling import fire_schedule
from marketops.config import Settings
from marketops.domain.enums import BudgetLine, RunStatus
from marketops.interfaces.composition import default_schedule_store
from marketops.interfaces.trigger_driver import run_once
from marketops.domain.run import RunDelta
from marketops.domain.scene import (
    OutreachPolicyDecl,
    RefreshPlan,
    SceneBudgetLine,
    SceneRecord,
    SceneState,
    SceneVersion,
)
from marketops.domain.scheduling import (
    ActionKind,
    ActionSpec,
    FireOutcome,
    ScheduleDefinition,
    TriggerType,
)
from marketops.domain.util import utc_now

T0 = datetime(2026, 9, 29, 1, 0, tzinfo=timezone.utc)

#: 一份说得清楚的声明。逐字取自 ``tests/test_mcp_workstream.py::CONTEXT``（那一份又
#: 取自 ``test_mcp_runs.py``）—— 这一族的受理与 ``submit_run`` 是**同一条路**，
#: 那两处必须能被同一份声明喂饱，否则「同一条路」是一句空话。
CONTEXT: dict[str, Any] = {
    "geo": "GB",
    "entity_classes": ["school"],
    "asset_classes": ["pitch"],
    "organization_classes": ["trust"],
    "scenario_candidates": [
        {
            "name": "secondary school",
            "reading": "k12",
            "provenance": "declared",
            "source": "req-1",
        }
    ],
    "objective": "find accounts for the approved offer",
    "evidence_requirements": ["official_site"],
}

#: 一份写得完整的窗口。``not_after`` **从当下算**（同 ``test_mcp_runs.py::_window``）：
#: 写死一个未来时刻是一颗定时炸弹，过了那一天 ``not_after < cutoff`` 恒成立。
def _window() -> dict[str, Any]:
    return {
        "max_objects": 5,
        "budget": {"search": 40},
        "not_after": (utc_now() + timedelta(days=3)).isoformat(),
    }


def _params(**overrides: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "context": dict(CONTEXT),
        "cutoff_hours": 8,
        "execution_mode": "bounded_auto",
        "window": _window(),
        "budget": {"search": 20},
    }
    body.update(overrides)
    return body


# ----------------------------------------------------------------------
# 夹具：盘上一版生效的 Scene + 一个真的动作
# ----------------------------------------------------------------------
def _scene(settings: Settings) -> SceneVersion:
    """本 profile 的第 1 版声明。``scene_id`` / ``workspace`` 都取部署的 profile：
    ``require_scene`` 只放行它，而这不是省事 —— 一个部署只服务一个 Scene。"""
    return SceneVersion(
        scene_id=settings.profile,
        workspace=settings.profile,
        version=1,
        region=("GB-England-Devon",),
        scenario="sports-surface-maintenance",
        product="acme-natural-grass",
        admissions=("sports-club",),
        exclusions=("golf-course",),
        role_policy="role-policy-v1",
        source_plan=("mother-pool-import",),
        budget=(SceneBudgetLine(line=BudgetLine.SEARCH, ceiling=20),),
        refresh=RefreshPlan.uncalibrated_default(),
        outreach=OutreachPolicyDecl.uncalibrated_default(),
        profile_id=settings.profile,
        profile_version="2026-09-01",
        created_at=T0,
        supersedes=None,
    )


def _seed_scene(settings: Settings, *, version_on_disk: bool = True) -> None:
    store = SqliteSceneStore(settings.database_path)
    store.put_record(
        SceneRecord(
            scene_id=settings.profile,
            workspace=settings.profile,
            state=SceneState.ACTIVE,
            latest_version=1,
            effective_version=1,
            created_at=T0,
            updated_at=T0,
        ),
        expected_revision=None,
    )
    if version_on_disk:
        store.put_version(_scene(settings))


@pytest.fixture()
def seeded(settings: Settings) -> Settings:
    _seed_scene(settings)
    return settings


def _action(settings: Settings) -> ResearchScheduleAction:
    """与组合根 ``_compose`` 同一接法：**工厂**，不是实例。

    ``sqlite3`` 的连接不能跨线程，而这一族会被触发驱动在它自己的线程上调用 ——
    接实例的实现会在真的驱动上第一次调用就炸，而单线程的判据一次都测不出来。
    """
    return ResearchScheduleAction(
        settings=settings,
        scenes=lambda: SqliteSceneStore(settings.database_path),
        runs=lambda: SqliteRunStore(settings.database_path),
        maintenance=lambda: SqliteMaintenanceStore(settings.database_path),
        ledger=lambda: SqliteBudgetLedger(settings.database_path),
        accounts=None,
        cohorts=None,
    )


@pytest.fixture()
def action(seeded: Settings) -> ResearchScheduleAction:
    return _action(seeded)


# ----------------------------------------------------------------------
# 夹具：一条声明，与「盘上真的有几行 Run」
# ----------------------------------------------------------------------
def _definition(settings: Settings, **overrides: Any) -> ScheduleDefinition:
    body: dict[str, Any] = {
        "schedule_id": "sched-0001",
        "schedule_revision": 1,
        "workspace": settings.profile,
        "owner": settings.profile,
        "scope_ref": settings.profile,
        "trigger_type": TriggerType.CALENDAR,
        "timezone": "+08:00",
        "calendar_rule": "daily",
        "next_due_at": T0,
        "action_spec": ActionSpec(
            kind=ActionKind.RESEARCH_RUN,
            target_ref=settings.profile,
            params=_params(),
        ),
        "budget_policy_ref": None,
        "notification_policy_ref": None,
        "enabled": True,
        "revision_reason": "declared",
        "created_at": T0,
        "updated_at": T0,
    }
    body.update(overrides)
    return ScheduleDefinition(**body)


def _rows(settings: Settings) -> int:
    """盘上**真的**有几行 Run。数的是库，不是返回值 —— 见模块 docstring。"""
    with sqlite3.connect(settings.database_path) as conn:
        found = conn.execute(
            "SELECT COUNT(*) FROM research_run WHERE workspace = ?", (settings.profile,)
        ).fetchone()
    return int(found[0])


def _fire(
    action: ResearchScheduleAction,
    definition: ScheduleDefinition,
    *,
    schedules: InMemoryScheduleStore | None = None,
    **overrides: Any,
):
    """走**那个用例**点火，不直接调 ``perform``。

    直接调 ``perform`` 的判据会漏掉整条这一片最要紧的性质：抢下那一行在 ``perform``
    **之前**、以及 ``next_due_at`` 由 ``settle_fire`` → ``advanced`` 推出去。

    ``schedules`` 是「重放」那一条夹具的全部：每一次点火都换一个新 store 的话，
    第二次的 ``claim_fire`` 会插进一张空表里 —— 于是「重放」这个词根本不成立，
    判据测的是一个它自己造出来的、永远第一次的世界。
    """
    store = InMemoryScheduleStore() if schedules is None else schedules
    if schedules is None:
        store.put_definition(definition, expected_revision=None)
    body: dict[str, Any] = {
        "schedule_id": definition.schedule_id,
        "schedule_revision": definition.schedule_revision,
        "fire_id": "fire-1",
        "at": T0,
    }
    body.update(overrides)
    return fire_schedule(store, InMemoryNotificationStore(), action, **body)


def _perform(action: ResearchScheduleAction, definition: ScheduleDefinition, **overrides):
    body: dict[str, Any] = {"fire_id": "fire-1", "at": T0}
    body.update(overrides)
    return action.perform(definition, **body)


# ----------------------------------------------------------------------
# 1 · 一条调度真的开出一轮 Run
# ----------------------------------------------------------------------
def test_a_firing_opens_exactly_one_run(action, seeded: Settings) -> None:
    """一次点火之后，盘上多出**恰好一行** Run。

    这一条是这一片存在的理由：在它之前，``fire_schedule`` 在生产上一条研究都开不出来。
    """
    assert _rows(seeded) == 0
    receipt = _fire(action, _definition(seeded))

    assert receipt.status.value == "admitted"
    assert receipt.fire.outcome is FireOutcome.RUN_ADMITTED
    assert _rows(seeded) == 1


def test_the_run_workspace_comes_from_the_scene_on_disk(action, seeded: Settings) -> None:
    """承重点 4：建出来的 Run 的 ``workspace`` 来自**盘上那一版 Scene**。

    ``spec_of`` 取 ``scene.workspace``，所以「Run 的工作区是哪来的」这句话的答案只有
    一个 —— 而判据要的是它**说的是盘上那个值**，不是「它不是空的」。

    ``definition.workspace`` 与 ``scope_ref`` 在这里刻意写成另一个值：一条从
    ``definition`` 或 ``params`` 取工作区的实现在这条判据上会红。
    """
    definition = _definition(seeded, workspace="ws-elsewhere", scope_ref="ws-elsewhere")
    assert definition.workspace != _scene(seeded).workspace  # 夹具自己先对账

    receipt = _fire(action, definition)

    assert receipt.fire.outcome is FireOutcome.RUN_ADMITTED
    with sqlite3.connect(seeded.database_path) as conn:
        found = conn.execute("SELECT workspace, spec, run_id FROM research_run").fetchall()
    assert len(found) == 1
    run_workspace, spec_json, _run_id = found[0]
    assert run_workspace == _scene(seeded).workspace
    # ``selector`` 是那份规格里「这一轮对谁做」的那一格 —— 它取的是盘上 Scene 的
    # ``scene_id``（``spec_of`` 逐字），所以它同时是「解析的是哪一版」的第二处读数。
    assert json.loads(spec_json)["selector"] == seeded.profile


def test_the_run_key_carries_the_revision_and_the_fire(action, seeded: Settings) -> None:
    """幂等键三格都在里面 —— 它是 ``claim_fire`` 之外**第二条**独立的防线。

    这一条判的是**那个键本身**，因为它是承重点 1 另一半（改版之后同一个 ``fire_id``
    是另一次 fire）的全部机制：``RunStore.admit`` 按 ``(workspace, 键)`` 幂等，
    少了 ``schedule_revision`` 那一格，第二版上真的该发生的那次受理会拿回第一版那一行。
    """
    first = _definition(seeded)
    second = _definition(seeded, schedule_revision=2)

    assert run_key_of(first, "fire-1") == run_key_of(first, "fire-1")
    assert run_key_of(first, "fire-1") != run_key_of(first, "fire-2")
    assert run_key_of(first, "fire-1") != run_key_of(second, "fire-1")


# ----------------------------------------------------------------------
# 2 · 四个承重点
# ----------------------------------------------------------------------
def test_the_same_fire_replayed_admits_once(action, seeded: Settings) -> None:
    """承重点 1 前半：同一个 ``fire_id`` 点两次，只受理一次、只建一行 Run。

    第二次**根本到不了** ``perform`` —— 抢下那一行在它之前（``claim_fire`` 的
    ``INSERT ... ON CONFLICT DO NOTHING``）。这里同时量两件事：盘上的行数，与
    ``perform`` 被调用的次数（``admit_run`` 一次 = 一次点火受理一次）。

    **两半用的是同一张 ``schedule_fire`` 表**：每一次点火换一个新 store 的话，
    第二次的 ``claim_fire`` 插进的是一张空表，于是「重放」这件事在判据里根本不发生
    —— 那会是一条恒绿的判据。
    """
    definition = _definition(seeded)
    schedules = InMemoryScheduleStore()
    schedules.put_definition(definition, expected_revision=None)
    calls: list[str] = []

    class _Counting(ResearchScheduleAction):
        def perform(self, definition, *, fire_id, at):  # type: ignore[override]
            calls.append(fire_id)
            return super().perform(definition, fire_id=fire_id, at=at)

    counting = _Counting(
        settings=action._settings,
        scenes=action._scenes,
        runs=action._runs,
        maintenance=action._maintenance,
        ledger=action._ledger,
    )
    first = _fire(counting, definition, schedules=schedules)
    second = _fire(counting, definition, schedules=schedules)

    assert first.fire.outcome is FireOutcome.RUN_ADMITTED
    assert second.replayed is True
    assert second.fire.outcome is FireOutcome.RUN_ADMITTED
    assert calls == ["fire-1"], f"动作被调了 {len(calls)} 次 —— 重放不该再到它"
    assert _rows(seeded) == 1


def test_a_revised_schedule_makes_the_same_fire_id_a_different_fire(
    action, seeded: Settings
) -> None:
    """承重点 1 后半：声明**改过之后**，同一个 ``fire_id`` 是**另一次** fire。

    两处都必须成立，而它们是两件事：

    * ``schedule_fire`` 的主键含 ``schedule_revision``，所以第二版那一次**不是**重放
      （`replayed is False`），动作真的被调到；
    * 那一轮 Run 的幂等键也含 ``schedule_revision``，所以它**是**新的一行 Run。

    少了后者，第一次受理会把第二次挡回去 —— 回执上写的是「已经受理过了」，而它
    一次都没跑（``ScheduleStore`` 契约那一条的第 3 种长相，逐字同款）。

    **改出来的那一版是第 3 版，不是第 2 版。** 第一次点火之后盘上已经被推进到第 2 版
    （``_next_due_at`` 恒回 ``None`` ⇒ ``advanced`` 写下 ``fired:no_next_due`` 那一版），
    而 ``put_definition`` 的版本号必须**逐字**等于基准加一（``memory.py:3633``）。
    这里不图省事去占那一格：第 2 版是**那个推进写的**，不是这里编的，而把它改写成
    自己的一份会让「谁来推进声明」这件事在判据里消失。

    **改出来的那一版带着自己的一份窗口。** §8 逐字：「改范围、改预算或改通知目标
    是**一条新 revision 加一次授权**」—— 所以第 3 版拿到的是**另一条链**的授权，
    而不是第 1 版那条链的第二轮。反过来写（两版共用逐字同一份 ``params``）会让
    第二次点火撞上 §B17 那条真的互锁（``window_refusal``：这条链上还有 1 轮没有
    结束），于是这一条判据红在一个**与本片无关**的原因上。
    """
    schedules = InMemoryScheduleStore()
    first = _definition(seeded, schedule_revision=1)
    schedules.put_definition(first, expected_revision=None)
    notifications = InMemoryNotificationStore()

    one = fire_schedule(
        schedules, notifications, action, schedule_id="sched-0001",
        schedule_revision=1, fire_id="fire-1", at=T0,
    )
    assert one.fire.outcome is FireOutcome.RUN_ADMITTED
    assert one.advanced_to == 2
    assert _rows(seeded) == 1

    second = _definition(seeded, schedule_revision=3)
    schedules.put_definition(second, expected_revision=2)
    two = fire_schedule(
        schedules, notifications, action, schedule_id="sched-0001",
        schedule_revision=3, fire_id="fire-1", at=T0,
    )

    assert two.replayed is False, "改版之后同一个 fire_id 被当成了重放"
    assert two.fire.outcome is FireOutcome.RUN_ADMITTED
    assert _rows(seeded) == 2, "改版这一次该开出一条新的 Run，而它没有"


def test_one_firing_admits_exactly_once(action, seeded: Settings) -> None:
    """承重点 2：一次确实建了 Run 的点火，``admit_run`` 被调**恰好一次**。

    D 那一半是「空队列 ``perform`` 0 次」；这一半是「真的建了的那一次只花一次钱」。
    量在 ``SqliteRunStore.admit`` 上（不是 ``perform`` 上），因为「受理」这件事的
    落点在那里 —— 一个在重试循环里调 ``admit_run`` 的实现，``perform`` 仍然只被
    调用一次，而它做了三次受理。

    幂等键读的是 ``spec.idempotency_key``：它是 ``RunSpec`` 上的那一格（``admit``
    的签名里没有这个名字的形参），而这一条同时是「键由谁算」的判据 —— 键若由
    ``RunStore`` 自己算，这里读到的就会是空的。
    """
    admissions: list[str] = []

    class _CountingStore(SqliteRunStore):
        def admit(self, spec, **kwargs):  # type: ignore[override]
            admissions.append(spec.idempotency_key)
            return super().admit(spec, **kwargs)

    counting = ResearchScheduleAction(
        settings=action._settings,
        scenes=action._scenes,
        runs=lambda: _CountingStore(seeded.database_path),
        maintenance=action._maintenance,
        ledger=action._ledger,
    )
    receipt = _fire(counting, _definition(seeded))

    assert receipt.fire.outcome is FireOutcome.RUN_ADMITTED
    assert admissions == [run_key_of(_definition(seeded), "fire-1")]
    assert _rows(seeded) == 1


def test_a_firing_does_not_admit_the_next_round(action, seeded: Settings) -> None:
    """承重点 3：``perform`` **只在被点火时**建 Run，不顺手把下一次的也建出来。

    下一次是 ``next_due_at`` 的职责，而下一次要点火才建。所以一次点火之后盘上是
    **一行**，不是两行 —— 一个「顺便跑下一轮」的实现在这一条上留下第二行。
    """
    receipt = _fire(action, _definition(seeded))

    assert receipt.fire.outcome is FireOutcome.RUN_ADMITTED
    assert _rows(seeded) == 1


# ----------------------------------------------------------------------
# 3 · 另外三个 ActionKind **失败关闭**
# ----------------------------------------------------------------------
@pytest.mark.parametrize("kind", [ActionKind.REFRESH, ActionKind.DIGEST, ActionKind.NOTIFY])
def test_the_unimplemented_kinds_fail_closed(action, seeded: Settings, kind: ActionKind) -> None:
    """三类没有实现的动作**不许**回一个「看起来成功」的结果。

    ``adapters/mcp_server/scheduling.py:180-183`` 逐字说了后果：一个「什么都不做、
    回 ``SKIPPED``」的动作会让每一次点火都在清单上显示 ``ok`` —— 而它一次研究都没
    开过。所以判据要两样：**不在** ``{RUN_ADMITTED, SKIPPED}`` 里，且**在**闭集里；
    外加盘上一行 Run 都没有。
    """
    report = _perform(
        action,
        _definition(
            seeded,
            action_spec=ActionSpec(kind=kind, target_ref=seeded.profile, params=_params()),
        ),
    )

    assert report.outcome is FireOutcome.UNRESOLVED
    assert report.outcome in {
        FireOutcome.RUN_ADMITTED,
        FireOutcome.SKIPPED,
        FireOutcome.UNRESOLVED,
        FireOutcome.FAILED,
    }, "动作回了一个不在 FireOutcome 闭集里的取值"
    assert report.outcome not in {FireOutcome.RUN_ADMITTED, FireOutcome.SKIPPED}
    assert kind.value in (report.message or "")
    assert _rows(seeded) == 0


def test_the_unimplemented_kinds_do_not_stop_the_schedule_silently(
    action, seeded: Settings
) -> None:
    """它在**通知清单**上看得见，而不是一次安静的 ``ok``。

    ``application/scheduling.py::_event_for`` 把 ``UNRESOLVED`` 落成 ``NEEDS_HUMAN``：
    这一条判的是那条链路真的接上了 —— 「失败关闭」与「看不见」是两件事，而后者
    才是这一族最坏的形态。
    """
    schedules = InMemoryScheduleStore()
    definition = _definition(
        seeded,
        action_spec=ActionSpec(
            kind=ActionKind.NOTIFY, target_ref=seeded.profile, params=_params()
        ),
    )
    schedules.put_definition(definition, expected_revision=None)
    notifications = InMemoryNotificationStore()

    receipt = fire_schedule(
        schedules, notifications, action, schedule_id="sched-0001",
        schedule_revision=1, fire_id="fire-1", at=T0,
    )

    assert receipt.fire.outcome is FireOutcome.UNRESOLVED
    events = notifications.events(workspace=seeded.profile)
    assert [event.severity.value for event in events] == ["needs_human"]


# ----------------------------------------------------------------------
# 4 · 解析不出来 = UNRESOLVED
# ----------------------------------------------------------------------
def test_a_target_that_is_not_on_disk_is_unresolved(settings: Settings) -> None:
    """没有种 Scene 的部署：``target_ref`` **读盘**解析不出来，于是 ``UNRESOLVED``。

    这一条是「读盘」那两个字的判据 —— 一个不读盘、拿 ``target_ref`` 当工作区用的
    实现在这里会受理成功，而它的 Run 挂在一版**盘上不存在**的 Scene 上。

    这里**不能**取 ``action`` 夹具：它挂在 ``seeded`` 上，而 ``seeded`` 与这一条要的
    「盘上什么都没有」是同一张盘的两面。取它的话这一条测的正好是它的反面。
    """
    definition = _definition(settings)
    report = _perform(_action(settings), definition)

    assert report.outcome is FireOutcome.UNRESOLVED
    assert "Scene" in (report.message or "")
    assert _rows(settings) == 0


def test_a_scene_without_an_effective_version_is_unresolved(settings: Settings) -> None:
    """记录在、但没有生效版本（这个市场还没开张）：同样受理不了。

    「没有生效版本」与「生效版本读不出来」是两句话，而两句都不是这一片能自行处理的
    —— ``_preamble`` 逐字拒，而这一片把它读成 ``UNRESOLVED`` 而不是静默跳过。
    """
    store = SqliteSceneStore(settings.database_path)
    store.put_record(
        SceneRecord(
            scene_id=settings.profile,
            workspace=settings.profile,
            state=SceneState.DRAFT,
            latest_version=1,
            effective_version=None,
            created_at=T0,
            updated_at=T0,
        ),
        expected_revision=None,
    )
    report = _perform(_action(settings), _definition(settings))

    assert report.outcome is FireOutcome.UNRESOLVED
    assert "生效版本" in (report.message or "")


def test_a_manual_mode_is_refused_and_not_downgraded(action, seeded: Settings) -> None:
    """§B25：一条调度只许在 ``bounded_auto`` / ``watch`` 上创建未来 Run。

    ``manual`` **不是**被降级成一次手动受理，而是被拒 —— 降级的形态是「这条调度每天
    都在替人做一次他没有授权的决定」，而它在清单上显示的是一次正常的点火。
    """
    report = _perform(
        action,
        _definition(
            seeded,
            action_spec=ActionSpec(
                kind=ActionKind.RESEARCH_RUN,
                target_ref=seeded.profile,
                params=_params(execution_mode="manual"),
            ),
        ),
    )

    assert report.outcome is FireOutcome.UNRESOLVED
    assert "bounded_auto" in (report.message or "")
    assert _rows(seeded) == 0


@pytest.mark.parametrize(
    "broken",
    [
        {"context": None},
        {"cutoff_hours": 0},
        {"cutoff_hours": True},
        {"cutoff_hours": "8"},
        {"execution_mode": "sometimes"},
        {"execution_mode": None},
        {"budget": 3},
        {"window": 3},
        {"name": 3},
        {"unexpected": 1},
    ],
)
def test_a_malformed_spec_is_unresolved_and_names_the_key(
    action, seeded: Settings, broken: dict[str, Any]
) -> None:
    """参数表坏掉的每一种长相都**指得出那个键**，而且一行 Run 都不写。

    多一个键也在里面（``unexpected``）：一次被静默吞掉的参数会让写它的人以为它约束了
    什么，而 ``submit_run`` 那一条纪律逐字同款（``round_of`` 对非 watch 的 ``name``
    是**拒**，不是忽略）。
    """
    params = _params()
    params.update(broken)

    report = _perform(
        action,
        _definition(
            seeded,
            action_spec=ActionSpec(
                kind=ActionKind.RESEARCH_RUN, target_ref=seeded.profile, params=params
            ),
        ),
    )

    assert report.outcome is FireOutcome.UNRESOLVED
    key = next(iter(broken))
    assert key in (report.message or ""), f"拒词里没有点名 {key!r}"
    assert _rows(seeded) == 0


def test_the_action_only_knows_the_declared_keys() -> None:
    """参数表的闭集是**写下来的**，不是散在 ``if`` 里的。

    这一条是「有人给 ``params`` 加了一格」时的夹子：加键要同时改这里与那段
    docstring，而漏掉任一处会红在一个说得出键名的地方。
    """
    assert RESEARCH_SCHEDULE_ACTION_PARAMS == {
        "context",
        "cutoff_hours",
        "execution_mode",
        "budget",
        "window",
        "name",
        "cohort",
    }


# ----------------------------------------------------------------------
# 5 · next_due_at：CALENDAR 的规则**真的被求值**
#     「读不懂」不许与「本来就没有下一次」在盘上同形
# ----------------------------------------------------------------------
# 这一节之前，``_next_due_at`` 恒回 ``None``，于是任何一条调度在**一次**点火之后
# 就被关掉（``fired:no_next_due``）——「multi-session 关掉之后调度继续」那句话断在
# 这里。现在 ``CALENDAR`` 那一格由 ``domain/calendar_rule.py`` 求值（那一份表示与
# 闭集自己的判据在 ``tests/test_calendar_rule.py``），而这一节判的是**生产这一层**：
# 声明上的三格真的被读进去了、规则读不懂时**不关掉**这条调度、以及端到端真的能点
# 第二次火。
#
# ``None`` 与「读不懂」是两件事，而它们在这一节里各有一条判据，两条读的都是**盘上
# 那一版声明**（``advanced`` 写下的新版）而不是回执 —— 「调度停了」这件事的落点是
# 声明。
def test_the_three_declared_fields_are_the_inputs(action, seeded: Settings) -> None:
    """规则 / 锚 / 时区：**每改一格，答案跟着动**，而两个读数都是逐字的。

    三对互为对照的读数，每对只差一格。合起来排掉的是三种实现：回一个常量的、
    只看 ``at`` 的、以及把 ``timezone`` 丢掉的。期望值是**具体时刻**，不是
    「大于锚」这种任何实现都满足的形状。
    """
    # 1) 规则那一格：``calendar_rule`` 换成别的频率，答案跟着换。
    assert _next_due_at(_definition(seeded, calendar_rule="daily"), at=T0) == T0 + timedelta(days=1)
    assert _next_due_at(_definition(seeded, calendar_rule="hourly"), at=T0) == T0 + timedelta(hours=1)

    # 2) 锚那一格：同一条规则，锚挪一天 ⇒ 答案挪一天（一个只看 ``at`` 的实现给同一个）。
    #    ``BYHOUR=9;BYMINUTE=30`` 落在**锚那一格里更晚的时刻**，所以锚是唯一决定它的东西。
    rule = "FREQ=DAILY;BYHOUR=9;BYMINUTE=30"
    assert _next_due_at(_definition(seeded, calendar_rule=rule), at=T0) == T0 + timedelta(minutes=30)
    assert _next_due_at(
        _definition(seeded, calendar_rule=rule, next_due_at=T0 + timedelta(days=1)), at=T0
    ) == T0 + timedelta(days=1, minutes=30)

    # 3) ``at`` 那一格：点火时刻本身就是锚的那一次之后，答案往后挪一期。
    assert _next_due_at(_definition(seeded), at=T0 + timedelta(days=1)) == T0 + timedelta(days=2)


def test_the_declared_timezone_is_really_used(action, seeded: Settings) -> None:
    """同一个锚、同一份规则，**只换 ``timezone``** ⇒ 两个不同的时刻。

    这是 TASK-K 要的那条「``timezone`` 真的被用上」的判据，而它落在**声明那一格**
    上（不是求值器那个 ``tz=`` 形参上）：换掉 ``ScheduleDefinition.timezone``，
    结果跟着变。

    两个期望值都是逐字的，而且差的那一小时**正是 DST**：``Europe/London`` 那一侧
    跨过了 2026-03-29 的跳变，当地 09:00 已经是 BST ⇒ ``08:00Z``；``+08:00`` 那一侧
    是 ``01:00Z``。一个把 ``timezone`` 当成装饰、或者解析不出来就按 UTC 顶替的实现，
    在这里给出两个相同（或两个都错）的答案。
    """
    anchor = datetime(2026, 3, 27, 9, 0, tzinfo=timezone.utc)
    at = datetime(2026, 3, 28, 9, 0, tzinfo=timezone.utc)
    rule = "FREQ=DAILY;BYHOUR=9;BYMINUTE=0"

    fixed = _next_due_at(
        _definition(seeded, calendar_rule=rule, next_due_at=anchor, timezone="+08:00"), at=at
    )
    london = _next_due_at(
        _definition(seeded, calendar_rule=rule, next_due_at=anchor, timezone="Europe/London"),
        at=at,
    )

    assert fixed == datetime(2026, 3, 29, 1, 0, tzinfo=timezone.utc)
    assert london == datetime(2026, 3, 29, 8, 0, tzinfo=timezone.utc)
    assert fixed != london


def test_the_other_three_trigger_types_still_answer_none(seeded: Settings) -> None:
    """``ONE_SHOT`` / ``DUE_STATE`` / ``EVENT`` 仍然回 ``None``，而三个 ``None``
    的理由**各不相同**（这一条把三条理由钉在一起，免得它们被合成一句）。

    * ``ONE_SHOT`` —— 它的 ``None`` 是**正确**的：「某日重新检查或一次提醒」本来
      就没有下一次。这一片**没有改它**，而这一行就是那个「没改」的读数。
    * ``DUE_STATE`` —— 下一次是「每个事实/路线各自的 ``next_due_at`` 里最早的那
      一个」（§8 逐字）：那是一整批对象的读数，不是这条声明上的一格。**不归这一片**
      （盘上今天没有一处产出那份批量读数），所以它照旧 ``None``。
    * ``EVENT`` —— 下一次由一次业务事件决定，而这一层看不见事件。同样不归这一片。

    后两类的 ``None`` 会让 ``advanced`` 把它们**关掉** —— 那是它们今天的既有行为，
    这一片**没有改**，也没有假装它们被解决了。判据只钉住「它们是 ``None``」这件事，
    而不是「``None`` 是对的」：这两类今天缺的是别人面上的读数（报告里点名了）。
    """
    for trigger in (TriggerType.ONE_SHOT, TriggerType.DUE_STATE, TriggerType.EVENT):
        got = _next_due_at(_definition(seeded, trigger_type=trigger), at=T0)
        assert got is None, f"{trigger.value} 回了 {got!r}"


def test_a_firing_that_admits_a_run_reports_the_next_time(action, seeded: Settings) -> None:
    """受理成功的那一次把下一期**写进声明** —— 这条调度因此**继续**。

    判据读的是盘上那一版声明（``advanced`` 写下的新版）与 ``schedule_fire`` 那一行，
    不是 ``ActionReport``：「下一次什么时候到」这件事的落点是声明。

    ``enabled is True`` 与 ``revision_reason == "fired:advanced"`` 是这一条的重点：
    改之前这里读到的会是 ``False`` + ``fired:no_next_due``（也就是「这条调度被关了」）。
    """
    schedules = InMemoryScheduleStore()
    definition = _definition(seeded)
    schedules.put_definition(definition, expected_revision=None)

    receipt = fire_schedule(
        schedules,
        InMemoryNotificationStore(),
        action,
        schedule_id="sched-0001",
        schedule_revision=1,
        fire_id="fire-1",
        at=T0,
    )

    assert receipt.fire.outcome is FireOutcome.RUN_ADMITTED
    assert receipt.fire.next_due_at == T0 + timedelta(days=1), (
        "一次受理没把下一期带回来 —— 调度会在这一次之后停住"
    )
    advanced = schedules.get_definition("sched-0001", 2)
    assert advanced is not None, "一次点火该推进出一版声明"
    assert advanced.next_due_at == T0 + timedelta(days=1)
    assert advanced.enabled is True, "这条调度被关掉了，而它还有下一期"
    assert advanced.revision_reason == "fired:advanced"


def test_a_rule_outside_the_subset_does_not_stop_the_schedule(action, seeded: Settings) -> None:
    """读不懂的规则：**显式拒绝**，而调度**一个字节都没动**，一行 Run 都不写。

    这一条与上一条是这一节的分量所在。四件事同时判：

    * 回执说得出**为什么**（点名那条规则本身），而结论是闭集里那个明确的非成功值；
    * 回执里的 ``next_due_at`` 是**这一版声明的原值**（``T0``），**不是 ``None``**
      —— ``None`` 在 ``advanced`` 那儿是「没有下一次」⇒ 关掉这条调度，而「读不懂」
      与它同形正是这一片要消掉的那种同形；
    * 盘上那一版**还是原来那一版**（``enabled=True``、``next_due_at=T0``、
      ``revision_reason="declared"``），而且**没有**第二版 —— ``put_definition``
      逐字按 ``content_digest`` 判「这一次要写的是不是已经是盘上那一份」，而一次拒绝
      改不动任何一格业务内容。于是下一轮醒来它**仍然到期**，会再拒一次。
    * Run **零行**（``seeded`` 挂在一条**指得到的** Scene 上，所以这里零行的唯一解释是
      拒绝发生在受理那一步**之前** —— 一个把求值放在 ``admit_run`` 之后的实现会先开出
      一轮研究）。
    """
    schedules = InMemoryScheduleStore()
    definition = _definition(seeded, calendar_rule="0 9 * * *")
    schedules.put_definition(definition, expected_revision=None)

    receipt = fire_schedule(
        schedules,
        InMemoryNotificationStore(),
        action,
        schedule_id="sched-0001",
        schedule_revision=1,
        fire_id="fire-1",
        at=T0,
    )

    assert receipt.fire.outcome is FireOutcome.UNRESOLVED
    assert receipt.fire.outcome is not FireOutcome.SKIPPED
    message = receipt.fire.message or ""
    assert "0 9 * * *" in message, ("拒词没有点名那条规则", message)
    assert receipt.fire.next_due_at == T0, (
        "拒绝那一版把 next_due_at 写成了别的值 —— 调度因此要么停住、要么跳到一个"
        "没人说过它该在的时刻",
        receipt.fire.next_due_at,
    )

    assert receipt.advanced_to is None, "一次拒绝不该推进出任何一版"
    assert schedules.get_definition("sched-0001", 2) is None
    still = schedules.get_definition("sched-0001", 1)
    assert still is not None
    assert (still.enabled, still.revision_reason) == (True, "declared")
    assert still.next_due_at == T0, "调度被推出去了，而它一步都没有动"
    assert _rows(seeded) == 0, "被拒的那一次仍然开出了一轮研究"


def test_an_unreadable_rule_and_a_real_last_time_are_different_on_disk(
    action, seeded: Settings
) -> None:
    """**同一条盘上、同一轮**：``CALENDAR`` 的读不懂 与 ``ONE_SHOT`` 的「没有下一次」
    留下**两种不同的形状** —— 这一条就是「不许混成一件」的读数。

    两条调度同时挂在一条 seed 好的 Scene 上：

    * ``sched-broken``（``CALENDAR`` + 一条不在闭集里的规则）—— 拒绝，**声明一版都没
      推进**（``advanced_to is None``、第 1 版三个格子原样），因此下一轮它还到期、还会
      再拒一次（所以它在清单上一直是「到期的、未受理的」）；
    * ``sched-done``（``ONE_SHOT``）—— 受理了，然后被**正确地关掉**：推进出第 2 版，
      ``enabled=False`` + ``fired:no_next_due`` + ``next_due_at=None``，此后它**不再
      到期**。

    只判其中一条是不够的：只判前者的话，「把两者都关掉」也绿；只判后者的话，
    「两者长得一样」绿。两条同时在一条判据里，读的才是**它们分得开**。
    """
    schedules = InMemoryScheduleStore()
    schedules.put_definition(
        _definition(seeded, schedule_id="sched-broken", calendar_rule="0 9 * * *"),
        expected_revision=None,
    )
    schedules.put_definition(
        _definition(
            seeded,
            schedule_id="sched-done",
            trigger_type=TriggerType.ONE_SHOT,
            calendar_rule=None,
        ),
        expected_revision=None,
    )
    notifications = InMemoryNotificationStore()

    receipts = {
        schedule_id: fire_schedule(
            schedules,
            notifications,
            action,
            schedule_id=schedule_id,
            schedule_revision=1,
            fire_id="fire-1",
            at=T0,
        )
        for schedule_id in ("sched-broken", "sched-done")
    }

    assert receipts["sched-broken"].fire.outcome is FireOutcome.UNRESOLVED
    assert receipts["sched-broken"].advanced_to is None
    assert receipts["sched-done"].fire.outcome is FireOutcome.RUN_ADMITTED
    assert receipts["sched-done"].advanced_to == 2

    broken = schedules.get_definition("sched-broken", 1)
    done = schedules.get_definition("sched-done", 2)
    assert broken is not None and done is not None

    assert (broken.enabled, broken.revision_reason) == (True, "declared")
    assert broken.next_due_at == T0
    assert (done.enabled, done.revision_reason) == (False, "fired:no_next_due")
    assert done.next_due_at is None
    assert _rows(seeded) == 1, "该受理的正好受理一次（``one_shot`` 那一条），另一条零行"


def test_a_timezone_that_cannot_be_resolved_is_a_refusal_not_a_close(
    action, seeded: Settings
) -> None:
    """解析不出来的时区：拒绝，**不按 UTC 顶替、也不动这条调度**。

    ``timezone`` 那一格没有缺省值，而一个「解析不出来就当 UTC」的兜底会在这条判据上
    受理成功并推进 —— 那条调度此后一直在错的时间里点火，而两份报告各自都正常。
    所以这里判的是**盘上那一版**：还是第 1 版、``enabled=True``、``next_due_at`` 没动、
    零行 Run，而 ``timezone`` 那一格在拒词里被点名。
    """
    schedules = InMemoryScheduleStore()
    schedules.put_definition(
        _definition(seeded, timezone="Mars/Olympus"), expected_revision=None
    )

    receipt = fire_schedule(
        schedules,
        InMemoryNotificationStore(),
        action,
        schedule_id="sched-0001",
        schedule_revision=1,
        fire_id="fire-1",
        at=T0,
    )

    assert receipt.fire.outcome is FireOutcome.UNRESOLVED
    assert "Mars/Olympus" in (receipt.fire.message or "")
    advanced = schedules.get_definition("sched-0001", 2)
    assert advanced is None
    still = schedules.get_definition("sched-0001", 1)
    assert still is not None
    assert still.enabled is True and still.next_due_at == T0
    assert _rows(seeded) == 0


def test_a_calendar_declaration_without_a_rule_is_a_refusal(action, seeded: Settings) -> None:
    """一条 ``CALENDAR`` 声明而 ``calendar_rule`` 是空的：也是拒绝，不是「没有下一次」。

    这一格是「``None`` 有且只有一个意思」的第三条读数：字段缺失是**这条声明写坏了**，
    不是「它跑完了」。
    """
    schedules = InMemoryScheduleStore()
    schedules.put_definition(
        _definition(seeded, calendar_rule=None), expected_revision=None
    )

    receipt = fire_schedule(
        schedules,
        InMemoryNotificationStore(),
        action,
        schedule_id="sched-0001",
        schedule_revision=1,
        fire_id="fire-1",
        at=T0,
    )

    assert receipt.fire.outcome is FireOutcome.UNRESOLVED
    assert "calendar_rule" in (receipt.fire.message or "")
    advanced = schedules.get_definition("sched-0001", 2)
    assert advanced is None
    still = schedules.get_definition("sched-0001", 1)
    assert still is not None
    assert still.enabled is True
    assert _rows(seeded) == 0


def test_the_refusal_is_visible_on_the_notification_list(action, seeded: Settings) -> None:
    """一次拒绝落在 ``NEEDS_HUMAN`` 上 —— 「失败关闭」与「看不见」是两件事。

    理由与那三个没实现的动作逐字相同（``_event_for``）：一条只把拒绝写进自己那一版
    声明的实现，在通知清单上什么都不会多出来，而人看到的是一张「一切正常」的清单。
    """
    schedules = InMemoryScheduleStore()
    schedules.put_definition(
        _definition(seeded, calendar_rule="@daily"), expected_revision=None
    )
    notifications = InMemoryNotificationStore()

    fire_schedule(
        schedules,
        notifications,
        action,
        schedule_id="sched-0001",
        schedule_revision=1,
        fire_id="fire-1",
        at=T0,
    )

    events = notifications.events(workspace=seeded.profile)
    assert [event.severity.value for event in events] == ["needs_human"]


def test_the_evaluator_refuses_at_its_own_boundary(seeded: Settings) -> None:
    """``_next_due_at`` 自己就有那道边界：读不懂的规则抛 :class:`_Unresolvable`。

    这一条是**守卫本身**的判据（上面几条判的是它的后果）。拿掉 ``_calendar_next_due``
    里那三处 ``raise``（改成回 ``None``）时，死的是这一条 —— 而它死了之后上面那几条
    后果判据也会跟着红，因为它们读的就是同一条边界。
    """
    for broken in (
        _definition(seeded, calendar_rule="0 9 * * *"),
        _definition(seeded, calendar_rule="@daily"),
        _definition(seeded, calendar_rule=None),
        _definition(seeded, timezone="Mars/Olympus"),
    ):
        with pytest.raises(_Unresolvable):
            _next_due_at(broken, at=T0)


# ----------------------------------------------------------------------
# 6 · 端到端：一条 CALENDAR 调度**点两次火**
# ----------------------------------------------------------------------
# 上面第 5 节判的是「下一期算得对不对」。这一节判的是那句话本身：**调度继续了**。
# 它走**驱动进程的入口**（``run_once``），两次点火之间隔着一次真正的醒来，而第二次
# 的时刻**不是**这一页给的常数，是从盘上那一版声明里读回来的 ``next_due_at``。
#
# 第一次与第二次之间必须**把第一轮结束掉** —— 这不是夹具的方便，而是 §B17 的正文：
# ``window_refusal`` 逐字拒 ``open_runs > 0``（「这条链上还有 N 轮没有结束」），依据是
# 「创建**后续** Run」里的「后续」= 「上一轮之后」，不是「同时」。判据里把这一步显式
# 写出来，是为了让这一条读起来就是生产上的那一次次序：点火 → 跑完那一轮 → 再点火。
def _put(settings: Settings, definition: ScheduleDefinition) -> None:
    """把一条声明写进**驱动真的会读的那张表**（不是内存替身）。

    内存替身喂不了 ``run_once``：驱动自己取 store（``trigger_driver`` 里那一行），
    而「驱动读到的是哪一本账」正是这一节要判的东西（同 ``test_trigger_driver.py::_put``
    的理由）。
    """
    default_schedule_store(settings)().put_definition(definition, expected_revision=None)


def _end_the_round(settings: Settings, receipt: dict[str, Any], *, at: datetime) -> None:
    """把回执上那一轮结束掉（``partial`` + 一份空 Delta）。

    用 ``partial`` 而不是 ``completed``：``ending_refusal`` 逐字拒「``completed`` 配一份
    每一格都空的 Delta」，而这一轮在这里**确实什么都没跑**（它是一个被判据造出来的
    Run，不是一次真的研究）。编一批产出出来才能填 `completed` —— 那是伪造，而这一条
    判据要问的不是「这一轮做成了什么」。
    """
    run_id = receipt["fired"][0]["detail"]["run_id"]
    SqliteRunStore(settings.database_path).stop(
        run_id, delta=RunDelta(), status=RunStatus.PARTIAL, at=at
    )


def test_a_calendar_schedule_fires_a_second_time_at_the_next_moment_on_disk(
    seeded: Settings,
) -> None:
    """**走驱动点两次火**，而第二次的时刻取自盘上推进出来的那一期。

    这是 TASK-K 那条端到端判据，也是 ``_next_due_at`` 的 docstring 里「multi-session
    关掉之后调度继续」那句话的读数。四个读数缺一不可：

    1. 第一次醒来的回执是 ``run_admitted``，而盘上一行 Run；
    2. 那一版声明上的 ``next_due_at`` 是**下一期**（``T0 + 1 天``）—— 不是 ``None``。
       改之前这里读到的是 ``None``，于是声明被判成「没有下一次」并**关掉**；
    3. 第二次醒来喂给驱动的时刻**读自盘**（第 2 步那个值），不是这里写的一个常数 ——
       一个把下一次写死在代码里的实现在这一步红；
    4. 按那个时刻再醒来一次，仍然是 ``run_admitted``，盘上两行 Run，而第三版声明的
       ``next_due_at`` 是再下一期（``T0 + 2 天``）。

    ``window`` 用的是 ``_definition`` 的默认那一份：``bounded_auto`` **必须**带窗口
    （``admission_refusal`` 逐字拒没有窗口的自动受理），所以不能靠「去掉窗口」绕开
    §B17 的链互锁 —— 绕开的办法是把上一轮真的结束掉（见上面那一段）。
    """
    _put(seeded, _definition(seeded))

    first = run_once(seeded, at=T0)

    assert [row["outcome"] for row in first["fired"]] == ["run_admitted"], first
    assert _rows(seeded) == 1

    after_first = default_schedule_store(seeded)().get_definition("sched-0001", 2)
    assert after_first is not None, "一次点火该推进出一版声明"
    second_due = after_first.next_due_at
    assert second_due == T0 + timedelta(days=1), (
        "盘上没有留下下一期 —— 这条调度会在这一次之后停住",
        second_due,
    )
    assert after_first.enabled is True

    _end_the_round(seeded, first, at=T0)
    second = run_once(seeded, at=second_due)

    assert [row["outcome"] for row in second["fired"]] == ["run_admitted"], second
    assert _rows(seeded) == 2, "第二次点火没有开出第二轮 Run"

    after_second = default_schedule_store(seeded)().get_definition("sched-0001", 3)
    assert after_second is not None
    assert after_second.next_due_at == T0 + timedelta(days=2)
    assert after_second.enabled is True


def test_between_two_fires_the_schedule_is_not_due(seeded: Settings) -> None:
    """两次点火**之间**它不到期 —— 否则「继续」会退化成「每一轮都点一次」。

    与上一条是一对：上一条说「到点会再点一次」，这一条说「不到点不会」。少了这一条，
    一个把 ``next_due_at`` 写成「就是现在」的实现也绿 —— 而那正是那条 docstring 警告的
    「看得见的循环」（``schedule_revision`` 每轮加一、``next_due_at`` 一直等于现在）。
    """
    _put(seeded, _definition(seeded))
    assert run_once(seeded, at=T0)["fired"]

    between = run_once(seeded, at=T0 + timedelta(hours=1))

    assert between["fired"] == [], between
    assert between["remaining_due"] == 0, between
    assert _rows(seeded) == 1


# ----------------------------------------------------------------------
# 7 · 它不许自己做的事
# ----------------------------------------------------------------------
def _module_ast() -> ast.Module:
    path = (
        Path(__file__).resolve().parent.parent
        / "marketops"
        / "adapters"
        / "mcp_server"
        / "schedule_action.py"
    )
    return ast.parse(path.read_text(encoding="utf-8"))


def _imported_names(tree: ast.Module) -> set[str]:
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.update(alias.name.split(".")[-1] for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            found.update(alias.name for alias in node.names)
    return found


def _referenced_names(tree: ast.Module) -> set[str]:
    """**代码里**出现过的名字。docstring 不算 —— 散文里提到 ``claim_fire`` 恰恰是
    这一条要的（它解释那条纪律为什么成立），而判据要判的是它有没有被**调用**。"""
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute):
            found.add(node.attr)
        elif isinstance(node, ast.Name):
            found.add(node.id)
    return found


def test_the_action_never_touches_the_notification_store() -> None:
    """它**不发通知**：契约的返回类型里没有 ``NotificationEvent`` 的位置。

    源码扫描而不是行为判据 —— 一个「顺手发一条通知」的实现最可能是**多了一个形参**，
    而它在任何一次正常点火上都不留痕迹（事件照记，只是多了一条）。同形判据的先例
    是 ``tests/test_scheduling_vertical.py::test_the_delivery_half_cannot_reach_the_action``。
    """
    tree = _module_ast()
    reachable = _imported_names(tree) | _referenced_names(tree)

    for forbidden in ("NotificationStore", "NotificationEvent", "publish_event"):
        assert forbidden not in reachable, f"动作这一层碰了 {forbidden}"


def test_the_action_does_no_idempotency_of_its_own(action, seeded: Settings) -> None:
    """它**不问**「刚才跑过没有」—— 幂等是 ``schedule_fire`` 主键的事。

    ``ports/scheduling.py`` 逐字：「一个自己去查重试的实现会把幂等键变成两处各判一次
    的东西，而两处分歧的那天是两轮研究」。所以这一层不许**调用**那一族的任何一格
    （``ScheduleStore`` 也不作为形参出现在它的签名里）。
    """
    tree = _module_ast()
    reachable = _imported_names(tree) | _referenced_names(tree)

    for forbidden in ("ScheduleStore", "get_fire", "fires", "claim_fire", "settle_fire"):
        assert forbidden not in reachable, f"动作这一层自己去查了 {forbidden}"
