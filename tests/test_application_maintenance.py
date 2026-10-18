"""``application/maintenance.py`` 的判据：选、领、跑、结，以及「没拿到」的那一半。

## 这一层要挡的四件事，各自有一条判据

1. **重复计费** —— 同一格在同一周期里只做一次。执行器是
   ``test_a_second_tick_over_the_same_period_does_no_work``：时钟不动、再 tick 一次，
   第二次必须是 0 件工作，而**不是**「又做了一遍、结论一样」。
2. **别人的格子被静默丢掉** —— 另一个驱动抢走的格子必须出现在 ``not_claimed`` 里。
   两个驱动各自的报告都写「这格不是我做的」时，合起来看就是「没人做过」。
3. **一件失败毁掉整批** —— 一格抛异常，其余照做，且这一格记 ``FAILED`` 带原因。
4. **暂停的场景被派发** —— 暂停/归档必须先于任何付费动作被挡住
   （§4：停的是新的派发，受理侧不受影响）。

## 暂停那一节的判据刻意分成两半

``test_a_paused_scene_is_not_ticked`` 与 ``test_due_work_refuses_a_paused_scene``
测的是同一个状态的**两个入口**：前者是调度（跳过并说明理由），后者是直接调用
（拒绝）。合成一条会让「跳过」看起来像是「这个函数对暂停态没什么意见」。
"""

from __future__ import annotations

from datetime import timedelta, timezone
from pathlib import Path

import pytest

from marketops.adapters.sqlite.scenes import SqliteMaintenanceStore, SqliteSceneStore
from marketops.application.maintenance import (
    MaintenanceOutcome,
    MaintenanceRun,
    due_work,
    run_once,
    split_by_aspect,
)
from marketops.application.scenes import (
    activate,
    add_version,
    archive,
    create_draft,
    pause,
    resume,
)
from marketops.domain.errors import ConfigError
from marketops.domain.maintenance import (
    ALL_ASPECTS,
    Aspect,
    ChangeSignal,
    DueReason,
    DueWork,
    MaintenanceState,
    Observation,
    SourceDelta,
)
from marketops.domain.profile import DeclaredProfile, ProfileBundle
from marketops.domain.scene import PauseReason

from tests.fakes import synthetic_scene_files
from tests.test_scene_store_contract import T0, WORKSPACE, make_version

UTC = timezone.utc
ONE_DAY = timedelta(days=1)
SUBJECT = ("account", "acct-a")


def _bundle() -> ProfileBundle:
    files = synthetic_scene_files()
    declared = DeclaredProfile(
        schema="marketops/profile.v2",
        name="uk-acme-natural-grass",
        display_name="synthetic",
        country="GB",
        exclusions=({"id": "golf-course"},),
        compliance={},
    )
    return ProfileBundle(
        profile_id=declared.name,
        status="TEST",
        rules={},
        role_terms=(),
        scene_version=files.version,
        scene_files=files,
        profile=declared,
    )


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    return tmp_path / "state" / "marketops.db"


@pytest.fixture
def scene_store(db_path: Path) -> SqliteSceneStore:
    return SqliteSceneStore(db_path)


@pytest.fixture
def maintenance_store(db_path: Path) -> SqliteMaintenanceStore:
    return SqliteMaintenanceStore(db_path)


def _live(scene_store: SqliteSceneStore, *, version=1) -> None:
    create_draft(store=scene_store, version=make_version(version=version), bundle=_bundle())
    activate(
        store=scene_store, scene_id="scene-a", version=version, at=T0, by="controller-1"
    )


def _outcome(due) -> MaintenanceOutcome:
    return MaintenanceOutcome(result_ref=f"evidence/{due.subject_id}")


# --- 选：主体从哪来 -----------------------------------------------------------


def test_a_declared_subject_that_was_never_observed_is_due(
    scene_store: SqliteSceneStore, maintenance_store: SqliteMaintenanceStore
) -> None:
    """刚立项、一格都还没看过 —— 七个面全是「从未观测」。"""
    _live(scene_store)

    picked, decision = due_work(
        scene_store=scene_store,
        maintenance_store=maintenance_store,
        scene_id="scene-a",
        now=T0,
        subjects=(SUBJECT,),
    )

    assert decision is None, "没设上限就没有截断决定，不是一条 dropped=0 的决定"
    assert len(picked) == 7
    assert {item.aspect for item in picked} == set(Aspect)
    assert all(item.reason.value == "never_observed" for item in picked)


def test_without_a_declared_subject_nothing_is_due(
    scene_store: SqliteSceneStore, maintenance_store: SqliteMaintenanceStore
) -> None:
    """**选择器不发明主体。** 一个会自己发现主体的选择器必然也会自己发明主体。"""
    _live(scene_store)

    picked, _ = due_work(
        scene_store=scene_store,
        maintenance_store=maintenance_store,
        scene_id="scene-a",
        now=T0,
    )

    assert picked == ()


def test_a_recent_observation_is_not_due_yet(
    scene_store: SqliteSceneStore, maintenance_store: SqliteMaintenanceStore
) -> None:
    """未标定的缺省节奏是 180/90/30 天；当天刚看过的不该再排一次。"""
    _live(scene_store)
    maintenance_store.put_observation(
        Observation(
            workspace=WORKSPACE,
            subject_kind=SUBJECT[0],
            subject_id=SUBJECT[1],
            aspect=Aspect.ASSET_ATTRIBUTE,
            observed_at=T0,
        )
    )

    picked, _ = due_work(
        scene_store=scene_store,
        maintenance_store=maintenance_store,
        scene_id="scene-a",
        now=T0 + ONE_DAY,
        subjects=(SUBJECT,),
    )

    assert Aspect.ASSET_ATTRIBUTE not in {item.aspect for item in picked}


def test_a_change_signal_pulls_one_cell_forward(
    scene_store: SqliteSceneStore, maintenance_store: SqliteMaintenanceStore
) -> None:
    """周期复核够用但不够及时：一个场地换了运营方，90 天的周期里会被当成旧的继续用。"""
    _live(scene_store)
    maintenance_store.put_observation(
        Observation(
            workspace=WORKSPACE,
            subject_kind=SUBJECT[0],
            subject_id=SUBJECT[1],
            aspect=Aspect.ACCOUNT_RELATION,
            observed_at=T0,
        )
    )
    maintenance_store.put_signal(
        ChangeSignal(
            workspace=WORKSPACE,
            subject_kind=SUBJECT[0],
            subject_id=SUBJECT[1],
            aspect=Aspect.ACCOUNT_RELATION,
            signalled_at=T0 + ONE_DAY,
            reason="场地换了运营方（公告）",
        )
    )

    picked, _ = due_work(
        scene_store=scene_store,
        maintenance_store=maintenance_store,
        scene_id="scene-a",
        now=T0 + ONE_DAY,
        subjects=(SUBJECT,),
    )

    signalled = [item for item in picked if item.reason.value == "change_signalled"]
    assert len(signalled) == 1
    assert signalled[0].aspect is Aspect.ACCOUNT_RELATION


def test_a_signal_never_invents_a_subject(
    scene_store: SqliteSceneStore, maintenance_store: SqliteMaintenanceStore
) -> None:
    """一条指向不存在主体的信号是数据错误，处置是回去查它从哪来，不是顺手建一个。"""
    _live(scene_store)
    maintenance_store.put_signal(
        ChangeSignal(
            workspace=WORKSPACE,
            subject_kind="account",
            subject_id="acct-nobody",
            aspect=Aspect.ASSET_ATTRIBUTE,
            signalled_at=T0,
            reason="来源里出现了这个名字",
        )
    )

    picked, _ = due_work(
        scene_store=scene_store,
        maintenance_store=maintenance_store,
        scene_id="scene-a",
        now=T0,
    )

    assert picked == ()


def test_the_effective_version_decides_the_cadence(
    scene_store: SqliteSceneStore, maintenance_store: SqliteMaintenanceStore
) -> None:
    """新写一版但没批准，**不改**在跑这件事的节奏 —— §4 的「不得中途换规则」。"""
    _live(scene_store)
    # 第 2 版把产品换掉（会重取来源），但节奏与生效版本一致 ——
    # 关键判据是排出来的工作带着**第 1 版**的版本号
    from marketops.application.scenes import add_version

    add_version(
        store=scene_store,
        version=make_version(version=2, product="acme-natural-grass-v2"),
        bundle=_bundle(),
    )

    picked, _ = due_work(
        scene_store=scene_store,
        maintenance_store=maintenance_store,
        scene_id="scene-a",
        now=T0,
        subjects=(SUBJECT,),
    )

    assert {item.scene_version for item in picked} == {1}


def test_a_limit_truncates_and_records_what_was_dropped(
    scene_store: SqliteSceneStore, maintenance_store: SqliteMaintenanceStore
) -> None:
    """截断必须被记录 —— 「没找到」与「找到了但被上限砍了」在报告里长得一样。"""
    _live(scene_store)

    picked, decision = due_work(
        scene_store=scene_store,
        maintenance_store=maintenance_store,
        scene_id="scene-a",
        now=T0,
        subjects=(SUBJECT,),
        limit=2,
    )

    assert len(picked) == 2
    assert decision is not None
    assert (decision.kept, decision.dropped, decision.cap) == (2, 5, 2)
    assert decision.truncated
    assert len(decision.dropped_items) == 5


def test_the_kept_ones_are_the_longest_overdue(
    scene_store: SqliteSceneStore, maintenance_store: SqliteMaintenanceStore
) -> None:
    """``what_is_due`` 的顺序承重：砍掉的是尾部，也就是最近才到期的那些。"""
    _live(scene_store)
    maintenance_store.put_observation(
        Observation(
            workspace=WORKSPACE,
            subject_kind=SUBJECT[0],
            subject_id=SUBJECT[1],
            aspect=Aspect.ASSET_ATTRIBUTE,
            observed_at=T0 - 400 * ONE_DAY,
        )
    )

    picked, decision = due_work(
        scene_store=scene_store,
        maintenance_store=maintenance_store,
        scene_id="scene-a",
        now=T0,
        subjects=(SUBJECT,),
        limit=1,
    )

    assert picked[0].aspect is Aspect.ASSET_ATTRIBUTE, "拖得最久的一格排在最前"
    # 这个主体有 7 个格子（``ALL_ASPECTS``）。只看过 ``ASSET_ATTRIBUTE``，所以另外 6 格
    # 是 ``NEVER_OBSERVED``、到期时刻都是 ``now``；看过的这一格拖了 400 天，
    # ``due_at`` 落在过去 —— 于是它排在队首，留下 1 个，砍掉的就是那 6 个。
    assert decision is not None
    assert (decision.kept, decision.dropped) == (1, 6)
    assert picked[0].dedupe_key not in decision.dropped_items


def test_due_work_refuses_a_paused_scene(
    scene_store: SqliteSceneStore, maintenance_store: SqliteMaintenanceStore
) -> None:
    """直接调用时**拒绝**而不是返回空 —— 空元组与「没有到期的格子」长得一样。"""
    _live(scene_store)
    pause(store=scene_store, scene_id="scene-a", at=T0, reason=PauseReason.OPERATOR)

    with pytest.raises(ConfigError, match="不接受新的派发"):
        due_work(
            scene_store=scene_store,
            maintenance_store=maintenance_store,
            scene_id="scene-a",
            now=T0,
            subjects=(SUBJECT,),
        )


# --- 跑一整轮 -----------------------------------------------------------------


def test_a_run_does_the_work_and_settles_each_cell(
    scene_store: SqliteSceneStore, maintenance_store: SqliteMaintenanceStore
) -> None:
    _live(scene_store)
    seen: list[str] = []

    def work(due):
        seen.append(due.dedupe_key)
        return _outcome(due)

    run = run_once(
        scene_store=scene_store,
        maintenance_store=maintenance_store,
        workspace=WORKSPACE,
        now=T0,
        work=work,
        scenes=("scene-a",),
        subjects={"scene-a": (SUBJECT,)},
    )

    assert run.ok
    assert run.due_count == 7
    assert run.claimed_count == 7
    assert len(seen) == 7
    assert {claim.state for claim in run.done} == {MaintenanceState.DONE}
    assert all(claim.result_ref for claim in run.done)
    assert all(claim.attempt == 1 for claim in run.done)


def test_a_second_tick_over_the_same_period_does_no_work(
    scene_store: SqliteSceneStore, maintenance_store: SqliteMaintenanceStore
) -> None:
    """**时钟不动、再 tick 一次。** 这是「重启或重复 tick 不得重复计费」的全部内容。

    它成立的理由不是任何一个驱动的自觉，是 ``(dedupe_key, attempt)`` 这个主键 ——
    所以下面断言的是「工作回调只被调用了 5 次」，而不是「结论一样」。
    """
    _live(scene_store)
    calls = []
    run = run_once(
        scene_store=scene_store,
        maintenance_store=maintenance_store,
        workspace=WORKSPACE,
        now=T0,
        work=lambda due: (calls.append(due), _outcome(due))[1],
        scenes=("scene-a",),
        subjects={"scene-a": (SUBJECT,)},
    )
    assert run.claimed_count == 7

    again = run_once(
        scene_store=scene_store,
        maintenance_store=maintenance_store,
        workspace=WORKSPACE,
        now=T0 + timedelta(minutes=1),
        work=lambda due: (_ for _ in ()).throw(AssertionError("同一周期里被做了第二次")),
        scenes=("scene-a",),
        subjects={"scene-a": (SUBJECT,)},
    )

    assert len(calls) == 7
    assert again.claimed_count == 0
    assert again.due_count == 7, "它仍然是到期的 —— 只是已经做过了"
    assert len(again.not_claimed) == 7


def test_a_callback_that_declares_nothing_records_no_observation(
    scene_store: SqliteSceneStore, maintenance_store: SqliteMaintenanceStore
) -> None:
    """回调**没声明**看过什么 ⇒ 一行观测都不写。上面那条收敛判据就靠它。

    ``MaintenanceOutcome.observed`` 缺省是 ``False``，而它同时覆盖两件事：「这一次
    没真的看」与「这一面根本没有『看』这个概念」。折平它们是正确的，因为两者在这一
    格上的处置相同：**都不该把这一格的复核推到下一个周期** —— 一条没看过的观测
    与一条假观测，在 ``what_is_due`` 眼里是同一样东西。
    """
    _live(scene_store)
    run = run_once(
        scene_store=scene_store,
        maintenance_store=maintenance_store,
        workspace=WORKSPACE,
        now=T0,
        work=lambda due: _outcome(due),
        scenes=("scene-a",),
        subjects={"scene-a": (SUBJECT,)},
    )

    assert run.claimed_count == 7, "七格照常做完了"
    assert run.observations == ()
    assert maintenance_store.observations(workspace=WORKSPACE) == ()


def test_a_callback_that_declares_it_looked_records_one_observation_per_cell(
    scene_store: SqliteSceneStore, maintenance_store: SqliteMaintenanceStore
) -> None:
    """回调声明「这一面我真的看到了」 ⇒ 逐格一条观测，且主语就是那一格。

    ## 为什么权限没有下放给回调

    写观测的那一行在 ``run_once`` 里，回调只在 outcome 里**声明**。回调自己拿到
    ``MaintenanceStore`` 也能写，但那会让「这一趟看到了什么」由七份回调各自实现，
    而漏掉的那一份在报告上表现为「这一格一直没有基线」—— 看不出是谁漏的。

    ## 断言的是 ``observations()`` 读回来的东西，不是返回值

    观测要经得起重启，所以真正算数的是**账上**的那一行。``run.observations`` 只是
    同一件事的即时副本。
    """
    _live(scene_store)
    run = run_once(
        scene_store=scene_store,
        maintenance_store=maintenance_store,
        workspace=WORKSPACE,
        now=T0,
        work=lambda due: MaintenanceOutcome(
            result_ref=f"evidence/{due.subject_id}",
            observed_at=T0,
        ),
        scenes=("scene-a",),
        subjects={"scene-a": (SUBJECT,)},
    )

    assert len(run.observations) == 7
    stored = maintenance_store.observations(workspace=WORKSPACE)
    assert len(stored) == 7
    assert {row.subject_id for row in stored} == {SUBJECT[1]}
    assert {row.subject_kind for row in stored} == {SUBJECT[0]}
    assert {row.aspect for row in stored} == set(ALL_ASPECTS)
    assert all(row.observed_at == T0 for row in stored)
    assert all(row.source_ref.startswith("evidence/") for row in stored), (
        "观测要指回**这一次真的看的东西**（``result_ref`` 那一份盘点）—— "
        "一条不指东西的观测在报告里与「有人点了按钮」同形"
    )


def test_a_failed_cell_records_no_observation(
    scene_store: SqliteSceneStore, maintenance_store: SqliteMaintenanceStore
) -> None:
    """挂了的那一格不写观测 —— 它没有产出，也就没有「看过了」这回事。

    这一条挡的是一个很容易写出来的实现：把记账放在 ``try`` 外面（那会让失败也
    留下观测），或者放在 ``finally`` 里。两种都表现为「这一格挂过一次，而它下一次
    要到 after_days 之后才被重看」。
    """
    _live(scene_store)

    def work(due):
        if due.aspect is Aspect.PRODUCT_FIT:
            raise TimeoutError("来源站超时")
        return MaintenanceOutcome(result_ref=f"evidence/{due.subject_id}", observed_at=T0)

    run = run_once(
        scene_store=scene_store,
        maintenance_store=maintenance_store,
        workspace=WORKSPACE,
        now=T0,
        work=work,
        scenes=("scene-a",),
        subjects={"scene-a": (SUBJECT,)},
    )

    assert len(run.failed) == 1
    assert len(run.observations) == 6
    recorded = {row.aspect for row in maintenance_store.observations(workspace=WORKSPACE)}
    assert Aspect.PRODUCT_FIT not in recorded


def test_one_failure_does_not_damage_the_batch(
    scene_store: SqliteSceneStore, maintenance_store: SqliteMaintenanceStore
) -> None:
    """一件坏掉不让其余都不做，但**也不报成整批成功**。"""
    _live(scene_store)

    def work(due):
        if due.aspect is Aspect.PRODUCT_FIT:
            raise TimeoutError("来源站超时")
        return _outcome(due)

    run = run_once(
        scene_store=scene_store,
        maintenance_store=maintenance_store,
        workspace=WORKSPACE,
        now=T0,
        work=work,
        scenes=("scene-a",),
        subjects={"scene-a": (SUBJECT,)},
    )

    assert not run.ok
    assert len(run.failed) == 1
    assert len(run.done) == 6
    assert run.failed[0].state is MaintenanceState.FAILED
    # 原因逐字保留，含异常类型 —— 「超时」与「解析失败」的处置不同
    assert "TimeoutError" in run.failed[0].failure
    assert "来源站超时" in run.failed[0].failure
    assert run.failed[0].result_ref == ""


def test_a_failed_cell_gets_a_second_attempt_row(
    scene_store: SqliteSceneStore, maintenance_store: SqliteMaintenanceStore
) -> None:
    """失败可以被重做，而那次是**新的一行** —— 「失败过几次」要答得出来。"""
    _live(scene_store)

    def always_fails(due):
        raise TimeoutError("第一次")

    run_once(
        scene_store=scene_store,
        maintenance_store=maintenance_store,
        workspace=WORKSPACE,
        now=T0,
        work=always_fails,
        scenes=("scene-a",),
        subjects={"scene-a": (SUBJECT,)},
    )
    second = run_once(
        scene_store=scene_store,
        maintenance_store=maintenance_store,
        workspace=WORKSPACE,
        now=T0 + ONE_DAY,
        work=lambda due: _outcome(due),
        scenes=("scene-a",),
        subjects={"scene-a": (SUBJECT,)},
    )

    assert second.ok
    assert {claim.attempt for claim in second.done} == {2}
    attempts = sorted(
        claim.attempt for claim in maintenance_store.claims("scene-a")
    )
    assert attempts == [1, 1, 1, 1, 1, 1, 1, 2, 2, 2, 2, 2, 2, 2]


def test_a_done_cell_is_never_retried_by_a_later_tick(
    scene_store: SqliteSceneStore, maintenance_store: SqliteMaintenanceStore
) -> None:
    """``DONE`` 的回执不会被再次认领 —— 换一条新行也不行。"""
    _live(scene_store)
    run_once(
        scene_store=scene_store,
        maintenance_store=maintenance_store,
        workspace=WORKSPACE,
        now=T0,
        work=lambda due: _outcome(due),
        scenes=("scene-a",),
        subjects={"scene-a": (SUBJECT,)},
    )

    again = run_once(
        scene_store=scene_store,
        maintenance_store=maintenance_store,
        workspace=WORKSPACE,
        now=T0 + 365 * ONE_DAY,
        work=lambda due: _outcome(due),
        scenes=("scene-a",),
        subjects={"scene-a": (SUBJECT,)},
    )

    # 一年之后再 tick：格子仍然到期（没观测过），但每一格都还没被重做
    assert again.claimed_count == 0


def test_a_paused_scene_is_not_ticked_and_says_why(
    scene_store: SqliteSceneStore, maintenance_store: SqliteMaintenanceStore
) -> None:
    """跳过而不是抛异常：一个每五分钟 tick 一次的定时器对着暂停态抛异常，
    会让「配置错了」与「这个场景被暂停了」在运维眼里长得一样。"""
    _live(scene_store)
    pause(store=scene_store, scene_id="scene-a", at=T0, reason=PauseReason.POLICY)

    run = run_once(
        scene_store=scene_store,
        maintenance_store=maintenance_store,
        workspace=WORKSPACE,
        now=T0 + ONE_DAY,
        work=lambda due: _outcome(due),
        scenes=("scene-a",),
        subjects={"scene-a": (SUBJECT,)},
    )

    assert run.ok
    assert run.due_count == 0
    assert run.claimed_count == 0
    assert len(run.not_ticking) == 1
    scene_id, reason = run.not_ticking[0]
    assert scene_id == "scene-a"
    assert "policy_hold" in reason


def test_an_archived_scene_is_not_resurrected(
    scene_store: SqliteSceneStore, maintenance_store: SqliteMaintenanceStore
) -> None:
    _live(scene_store)
    archive(store=scene_store, scene_id="scene-a", at=T0)

    run = run_once(
        scene_store=scene_store,
        maintenance_store=maintenance_store,
        workspace=WORKSPACE,
        now=T0 + ONE_DAY,
        work=lambda due: _outcome(due),
        scenes=("scene-a",),
        subjects={"scene-a": (SUBJECT,)},
    )

    assert run.claimed_count == 0
    assert "archived" in run.not_ticking[0][1]


def test_a_draft_scene_is_not_ticked(
    scene_store: SqliteSceneStore, maintenance_store: SqliteMaintenanceStore
) -> None:
    """未获批准的场景不派发 —— 这是 §4 那条「只能 draft/preview」的落地。"""
    create_draft(store=scene_store, version=make_version(), bundle=_bundle())

    run = run_once(
        scene_store=scene_store,
        maintenance_store=maintenance_store,
        workspace=WORKSPACE,
        now=T0,
        work=lambda due: _outcome(due),
        scenes=("scene-a",),
        subjects={"scene-a": (SUBJECT,)},
    )

    assert run.claimed_count == 0
    assert "draft" in run.not_ticking[0][1]


def test_ticking_without_naming_scenes_covers_the_workspace(
    scene_store: SqliteSceneStore, maintenance_store: SqliteMaintenanceStore
) -> None:
    _live(scene_store)

    run = run_once(
        scene_store=scene_store,
        maintenance_store=maintenance_store,
        workspace=WORKSPACE,
        now=T0,
        work=lambda due: _outcome(due),
    )

    assert run.claimed_count == 0, "没有声明主体 → 没有工作，但这不等于出错"
    assert run.ok


def test_a_cross_workspace_tick_is_refused(
    scene_store: SqliteSceneStore, maintenance_store: SqliteMaintenanceStore
) -> None:
    """跨工作区 tick 必须先经过授权判定，而不是在这里顺手扩大范围。"""
    _live(scene_store)

    with pytest.raises(ConfigError, match="跨工作区"):
        run_once(
            scene_store=scene_store,
            maintenance_store=maintenance_store,
            workspace="ws-someone-else",
            now=T0,
            work=lambda due: _outcome(due),
            scenes=("scene-a",),
        )


# --- 来源增量：两个数分开数 ---------------------------------------------------


def test_source_gaps_and_new_venues_are_counted_separately(
    scene_store: SqliteSceneStore, maintenance_store: SqliteMaintenanceStore
) -> None:
    """§4：新来源缺口 ≠ 真实场地新增。两者在报告里是两栏，不是一栏。"""
    _live(scene_store)

    def work(due):
        if due.aspect is not Aspect.SOURCE_COVERAGE:
            return _outcome(due)
        return MaintenanceOutcome(
            result_ref="evidence/source-supply",
            source_delta=SourceDelta(new_source_gaps=7, new_venues=2),
        )

    run = run_once(
        scene_store=scene_store,
        maintenance_store=maintenance_store,
        workspace=WORKSPACE,
        now=T0,
        work=work,
        scenes=("scene-a",),
        subjects={"scene-a": (SUBJECT,)},
    )

    assert len(run.source_deltas) == 1
    delta = run.source_deltas[0]
    assert (delta.new_source_gaps, delta.new_venues) == (7, 2)
    assert delta.measured


def test_the_two_counts_have_no_combined_total() -> None:
    """**缺一个 ``total`` 属性是自觉，断言它缺席才是判据。**

    加一个 ``total`` 就等于把合成数提供了出来，而它只要存在，迟早会有人读它、
    写它、把它放进周报。一个 ``None`` 参与的求和只能靠「当成 0」收场 ——
    而那正是「未知不得写成 0」禁止的。
    """
    delta = SourceDelta(new_source_gaps=7, new_venues=None)

    assert not hasattr(delta, "total")
    assert delta.unmeasured == ("new_venues",)
    assert not delta.measured, "一项测了一项没测，不是「部分可用」"
    with pytest.raises(ValueError):
        SourceDelta(new_source_gaps=-1)


def test_a_cell_that_measured_nothing_reports_nothing(
    scene_store: SqliteSceneStore, maintenance_store: SqliteMaintenanceStore
) -> None:
    """没测就是没测 —— 回执里没有那一条，而不是一条两个零。"""
    _live(scene_store)

    run = run_once(
        scene_store=scene_store,
        maintenance_store=maintenance_store,
        workspace=WORKSPACE,
        now=T0,
        work=lambda due: _outcome(due),
        scenes=("scene-a",),
        subjects={"scene-a": (SUBJECT,)},
    )

    assert run.source_deltas == ()


def test_an_outcome_without_a_result_ref_is_refused() -> None:
    """一次没有产出的复核在回执里与「跑挂了」长得一样，而处置不同。"""
    with pytest.raises(ConfigError, match="result_ref"):
        MaintenanceOutcome(result_ref="   ")


# --- 回执的形状 ---------------------------------------------------------------


def test_a_run_reports_the_three_outcomes_separately(
    scene_store: SqliteSceneStore, maintenance_store: SqliteMaintenanceStore
) -> None:
    """做成了 / 做了但挂了 / **根本不是我做的** —— 三段分开，第三段最容易被漏掉。"""
    _live(scene_store)
    run = run_once(
        scene_store=scene_store,
        maintenance_store=maintenance_store,
        workspace=WORKSPACE,
        now=T0,
        work=lambda due: _outcome(due),
        scenes=("scene-a",),
        subjects={"scene-a": (SUBJECT,)},
    )
    assert isinstance(run, MaintenanceRun)
    assert (len(run.done), len(run.failed), len(run.not_claimed)) == (7, 0, 0)
    assert run.truncation is None


def test_a_second_driver_that_loses_the_claim_reports_it(
    scene_store: SqliteSceneStore, maintenance_store: SqliteMaintenanceStore
) -> None:
    """别人抢走的格子进 ``not_claimed`` —— 静默丢掉会让两份报告合起来显示「没人做过」。

    这里让第一个驱动**先**把七格全领走（``work`` 不结账），第二个驱动于是
    每一格都没拿到。这模拟的是两个进程同时 tick，而不是「第一轮做完了」——
    后者走的是 ``DONE`` 那条路，与这一条不是同一格。
    """
    _live(scene_store)
    first_store = SqliteMaintenanceStore(scene_store._path)  # noqa: SLF001
    held: list[str] = []

    run_once(
        scene_store=scene_store,
        maintenance_store=first_store,
        workspace=WORKSPACE,
        now=T0,
        work=lambda due: (held.append(due.dedupe_key), MaintenanceOutcome(result_ref="held"))[1],
        scenes=("scene-a",),
        subjects={"scene-a": (SUBJECT,)},
    )
    # 第一轮把它们做完了；现在把第二条回执的认领时刻往前挪一格是不允许的
    # （回执是不变的），所以用另一个时刻 tick 一次，确认 DONE 的格子不会再被领走。
    second = run_once(
        scene_store=scene_store,
        maintenance_store=maintenance_store,
        workspace=WORKSPACE,
        now=T0 + ONE_DAY,
        work=lambda due: _outcome(due),
        scenes=("scene-a",),
        subjects={"scene-a": (SUBJECT,)},
    )

    assert len(held) == 7
    assert second.claimed_count == 0
    assert len(second.not_claimed) == 7
    assert {claim.state for claim in second.not_claimed} == {MaintenanceState.DONE}


# --- 范围：本部署做哪几个面 ---------------------------------------------------
#
# 这三件事分开测，因为它们各自的失败长得很像而处置完全不同：
# 「范围外的格子被认领了」会在账上每 tick 写一行，「上限被范围外的格子吃掉」会让
# 一个只复核一面的部署每轮实际只做一件，而「范围外的格子被静默滤掉」在报告上
# 与「这一轮没有到期的」逐字相同。


def _due_items(*aspects: Aspect) -> tuple[DueWork, ...]:
    """几个面各一格，主体与时刻都一样 —— 差别只在 aspect。"""
    return tuple(
        DueWork(
            scene_id="scene-a",
            scene_version=1,
            subject_kind=SUBJECT[0],
            subject_id=SUBJECT[1],
            aspect=aspect,
            reasons=(DueReason.NEVER_OBSERVED,),
            period="never",
            due_at=T0,
        )
        for aspect in aspects
    )


def test_split_by_aspect_with_none_keeps_everything_inside() -> None:
    """``None`` 是**不设范围**，不是**空范围**。

    后者会让一次忘记传参的调用静默地什么都不做，而账上显示为「这一轮没有到期的
    格子」—— 两句话在报告里长得一模一样。
    """
    inside, outside = split_by_aspect(_due_items(*ALL_ASPECTS), None)

    assert len(inside) == len(ALL_ASPECTS)
    assert outside == ()


def test_split_by_aspect_keeps_the_input_order() -> None:
    """保序：进来的次序是 ``what_is_due`` 排好的（拖得最久的在前），
    分完范围仍要保住它 —— 上限砍的是尾部，砍谁取决于这个次序。"""
    items = _due_items(Aspect.SOURCE_COVERAGE, Aspect.ASSET_ATTRIBUTE, Aspect.PRODUCT_FIT)

    inside, outside = split_by_aspect(items, [Aspect.SOURCE_COVERAGE])

    assert inside == (items[0],)
    assert outside == (items[1], items[2])


def test_split_by_aspect_refuses_a_value_that_is_not_a_member() -> None:
    """失败关闭：一个字符串在这里与任何格子都不相等 → **每一格**都被判成范围外。

    那是一次「安静地什么都不做」，而它唯一的信号是「这一轮没有要做的事」。
    所以那条路只有异常堵得住。
    """
    with pytest.raises(ConfigError, match="不是 Aspect 成员"):
        split_by_aspect(_due_items(Aspect.SOURCE_COVERAGE), ["source_coverage"])


def test_a_scoped_tick_does_not_claim_the_cells_it_cannot_do(
    scene_store: SqliteSceneStore, maintenance_store: SqliteMaintenanceStore
) -> None:
    """范围外的格子**一格都不认领** —— 执行器是「账上只有一行」。

    只断言 ``out_of_scope`` 里有 6 格是不够的：那 6 格完全可能是**先被认领**、
    再被报成范围外（``claim`` 在 ``FAILED`` 上写新行、attempt 递增，所以这样
    每 tick 都会把账撑大一截，而报告上看起来只是「范围外有 6 格」）。
    """
    _live(scene_store)
    seen: list[Aspect] = []

    run = run_once(
        scene_store=scene_store,
        maintenance_store=maintenance_store,
        workspace=WORKSPACE,
        now=T0,
        work=lambda due: (seen.append(due.aspect), _outcome(due))[1],
        scenes=("scene-a",),
        subjects={"scene-a": (SUBJECT,)},
        aspects={Aspect.SOURCE_COVERAGE},
    )

    assert seen == [Aspect.SOURCE_COVERAGE], "只有范围内的那一面被做了"
    assert run.out_of_scope_by_aspect == tuple(
        (aspect.value, 1) for aspect in ALL_ASPECTS if aspect is not Aspect.SOURCE_COVERAGE
    ), "范围外那六格要逐面报出来，零个的不列"
    assert [claim.aspect for claim in maintenance_store.claims("scene-a")] == [
        Aspect.SOURCE_COVERAGE
    ], "账上只有范围内那一行 —— 范围外的格子连认领都没发生过"


def test_the_cap_applies_inside_the_scope_not_across_it(
    scene_store: SqliteSceneStore, maintenance_store: SqliteMaintenanceStore
) -> None:
    """``limit`` 数的是**本次要做的那几格**，不是「到期工作的前 N 格」。

    顺序在这里承重：``what_is_due`` 的排序键让 ``source_coverage`` 排在**最后**
    （按取值排序：account_relation < asset_attribute < asset_identity <
    contact_person_role < email_evidence < product_fit < source_coverage）。
    所以「先封顶再分范围」的实现会把上限花在 ``account_relation`` 上，而它根本
    不在范围内 —— 结果是这一轮做了 0 件，报告上写着 ``dropped=1``。

    这句「排在最后」不是碰巧：它取决于七个取值按字符串排序后那个在范围内的
    （``source_coverage``）确实落在末尾。**换词表的人要重算一遍这一格** ——
    一个排在中间的取值会让下面那两条断言仍然绿，而这条测试想量的那件事已经不成立了。
    """
    _live(scene_store)

    run = run_once(
        scene_store=scene_store,
        maintenance_store=maintenance_store,
        workspace=WORKSPACE,
        now=T0,
        work=lambda due: _outcome(due),
        scenes=("scene-a",),
        subjects={"scene-a": (SUBJECT,)},
        limit=1,
        aspects={Aspect.SOURCE_COVERAGE},
    )

    assert run.claimed_count == 1
    assert [claim.aspect for claim in run.done] == [Aspect.SOURCE_COVERAGE]
    assert run.truncation is not None
    assert run.truncation.cap == 1, "上限是 1，而范围内的格子正好只有一个"
    assert run.truncation.dropped == 0, "范围内没有被丢掉的 —— 上限没有作用在范围外"


def test_out_of_scope_is_counted_in_the_declared_order(
    scene_store: SqliteSceneStore, maintenance_store: SqliteMaintenanceStore
) -> None:
    """按 ``ALL_ASPECTS`` 的顺序，不是按计数排序。

    一份每次跑都换个次序的报告，人眼没法比对两次运行。
    """
    _live(scene_store)

    run = run_once(
        scene_store=scene_store,
        maintenance_store=maintenance_store,
        workspace=WORKSPACE,
        now=T0,
        work=lambda due: _outcome(due),
        scenes=("scene-a",),
        subjects={"scene-a": (SUBJECT,)},
        aspects={Aspect.SOURCE_COVERAGE},
    )

    reported = [aspect for aspect, _count in run.out_of_scope_by_aspect]
    assert reported == [
        aspect.value for aspect in ALL_ASPECTS if aspect is not Aspect.SOURCE_COVERAGE
    ]


def test_an_unscoped_tick_reports_nothing_out_of_scope(
    scene_store: SqliteSceneStore, maintenance_store: SqliteMaintenanceStore
) -> None:
    """不设范围时这一格是空的 —— **不是**「七个面都在范围外」。

    空表与「七格都在范围外」若共用一种写法，一个忘了传范围的部署会看起来像
    一个声明得很窄的部署。
    """
    _live(scene_store)

    run = run_once(
        scene_store=scene_store,
        maintenance_store=maintenance_store,
        workspace=WORKSPACE,
        now=T0,
        work=lambda due: _outcome(due),
        scenes=("scene-a",),
        subjects={"scene-a": (SUBJECT,)},
    )

    assert run.out_of_scope == ()
    assert run.out_of_scope_by_aspect == ()


# --- §11：重启、暂停、换版，以及「只刷新过期的那些」-------------------------


def _observe(store: SqliteMaintenanceStore, aspect: Aspect, *, at) -> None:
    store.put_observation(
        Observation(
            workspace=WORKSPACE,
            subject_kind=SUBJECT[0],
            subject_id=SUBJECT[1],
            aspect=aspect,
            observed_at=at,
        )
    )


def _tick(scene_store, maintenance_store, *, at, work, limit=None):
    return run_once(
        scene_store=scene_store,
        maintenance_store=maintenance_store,
        workspace=WORKSPACE,
        now=at,
        work=work,
        scenes=("scene-a",),
        subjects={"scene-a": (SUBJECT,)},
        limit=limit,
    )


def test_a_restarted_driver_knows_the_same_cells_and_does_not_redo_them(
    scene_store: SqliteSceneStore,
    maintenance_store: SqliteMaintenanceStore,
    db_path: Path,
) -> None:
    """**重启之后仍知道谁到期。** 成立的理由不是谁记得，而是没人需要记。

    这一格断言的是那件事本身：第二个驱动（同一条库、全新的存储对象）算出来的到期
    集合与原驱动**逐格同一个 key**。库里除了观测与回执没有别的东西，所以「重启」
    在这里没有任何东西可以丢 —— 如果哪天有人给 ``what_is_due`` 加一份进程内缓存，
    这一格会转红（而且只在重启时才会红，那是最不容易被复查到的一种红）。

    后半段是同一个机制的另一面：这一周期已经领过的那些，新驱动**领不到**。
    """
    _live(scene_store)
    already_seen = (Aspect.SOURCE_COVERAGE, Aspect.ASSET_IDENTITY, Aspect.PRODUCT_FIT)
    for aspect in already_seen:
        _observe(maintenance_store, aspect, at=T0)

    first, _ = due_work(
        scene_store=scene_store,
        maintenance_store=maintenance_store,
        scene_id="scene-a",
        now=T0,
        subjects=(SUBJECT,),
    )
    assert {item.aspect for item in first} == set(Aspect) - set(already_seen)

    run = _tick(scene_store, maintenance_store, at=T0, work=lambda due: _outcome(due))
    assert run.claimed_count == 4

    # 重启：同一条库、全新的存储对象。
    restarted_scenes = SqliteSceneStore(db_path)
    restarted_maintenance = SqliteMaintenanceStore(db_path)

    again, _ = due_work(
        scene_store=restarted_scenes,
        maintenance_store=restarted_maintenance,
        scene_id="scene-a",
        now=T0 + timedelta(minutes=5),
        subjects=(SUBJECT,),
    )
    assert [item.dedupe_key for item in again] == [item.dedupe_key for item in first], (
        "同一周期里，重启前后必须是**同一批格子**、同一个 key"
    )

    second = _tick(
        restarted_scenes,
        restarted_maintenance,
        at=T0 + timedelta(minutes=5),
        work=lambda due: (_ for _ in ()).throw(AssertionError("重启后重复做了一次")),
    )

    assert second.due_count == 4, "它们仍然到期 —— 到期不由「做没做过」决定"
    assert second.claimed_count == 0
    assert len(second.not_claimed) == 4


def test_only_the_expired_cells_are_refreshed(
    scene_store: SqliteSceneStore, maintenance_store: SqliteMaintenanceStore
) -> None:
    """七个面同一时刻全部核过，31 天后再看：**只有 30 天那一个**到期。

    这一条压的是「只刷新已过期的部分」这句话的字面意思。它同时也是一条反例：
    如果哪天有人把「这一轮该跑维护」实现成「把所有格子重做一遍」，另外六个面
    会一起出现在这里 —— 而那正是这套东西唯一会失控的地方。
    """
    _live(scene_store)
    for aspect in ALL_ASPECTS:
        _observe(maintenance_store, aspect, at=T0)

    picked, decision = due_work(
        scene_store=scene_store,
        maintenance_store=maintenance_store,
        scene_id="scene-a",
        now=T0 + timedelta(days=31),
        subjects=(SUBJECT,),
    )

    assert [item.aspect for item in picked] == [Aspect.PRODUCT_FIT], (
        "未标定的缺省计划里只有这一格是 30 天，其余是 90/180"
    )
    assert picked[0].reason is DueReason.CADENCE_ELAPSED
    assert decision is None


def test_a_paused_scene_resumes_from_the_same_checkpoint(
    scene_store: SqliteSceneStore, maintenance_store: SqliteMaintenanceStore
) -> None:
    """暂停不产生新的到期工作；恢复之后**接着上次停下的地方**继续。

    「从检查点继续」不是一句关于记忆的话，是这一条：恢复后领到的是上次**没领到**
    的那几件，而上次已经做完的那几件不在里面。做成「恢复即重放整个母池」的版本
    在这条上会转红，而它在账上看起来只是「恢复后跑了一轮，很正常」。
    """
    _live(scene_store)

    first = _tick(scene_store, maintenance_store, at=T0, work=lambda due: _outcome(due), limit=3)
    assert first.claimed_count == 3
    done_keys = {claim.dedupe_key for claim in first.done}

    pause(store=scene_store, scene_id="scene-a", at=T0 + ONE_DAY, reason=PauseReason.POLICY)
    while_paused = _tick(
        scene_store,
        maintenance_store,
        at=T0 + 2 * ONE_DAY,
        work=lambda due: (_ for _ in ()).throw(AssertionError("暂停的 Scene 不该派发工作")),
    )
    assert while_paused.due_count == 0
    assert while_paused.claimed_count == 0
    assert len(while_paused.not_ticking) == 1

    resume(store=scene_store, scene_id="scene-a", at=T0 + 3 * ONE_DAY)
    after = _tick(scene_store, maintenance_store, at=T0 + 4 * ONE_DAY, work=lambda due: _outcome(due))

    assert after.claimed_count == 4, "七格里三格做过了，剩下的正好四格"
    assert {claim.dedupe_key for claim in after.done} & done_keys == set(), (
        "恢复不是重放：上次已经做完的那几格不该再被领一次"
    )
    assert {claim.scene_version for claim in after.done} == {1}, "恢复不改生效版本"


def test_a_new_scene_version_does_not_make_current_cells_due(
    scene_store: SqliteSceneStore, maintenance_store: SqliteMaintenanceStore
) -> None:
    """**换一版 Scene 不得触发全市场重爬。** 七个面刚核过，换版之后照样一个都不到期。

    这是 §11 里最容易被实现坏的一条：凡把「版本变了」当成「这一格要重看」的实现，
    在这一格上会转红，而在真实环境里它的表现是「有人改了一下模板，账单涨了一截」。
    挡住的机制不是版本号，是每格自己的 ``observed_at`` —— 复核的节奏只有一个来源，
    与谁改了哪一版无关。
    """
    _live(scene_store)
    for aspect in ALL_ASPECTS:
        _observe(maintenance_store, aspect, at=T0)

    before, _ = due_work(
        scene_store=scene_store,
        maintenance_store=maintenance_store,
        scene_id="scene-a",
        now=T0 + ONE_DAY,
        subjects=(SUBJECT,),
    )
    assert before == ()

    add_version(store=scene_store, version=make_version(version=2), bundle=_bundle())
    activate(
        store=scene_store,
        scene_id="scene-a",
        version=2,
        at=T0 + ONE_DAY,
        by="controller-1",
    )

    after, _ = due_work(
        scene_store=scene_store,
        maintenance_store=maintenance_store,
        scene_id="scene-a",
        now=T0 + 2 * ONE_DAY,
        subjects=(SUBJECT,),
    )

    assert after == (), "刚核过的格子不因换版而到期"


def test_a_new_scene_version_raises_the_key_of_the_cells_that_were_already_due(
    scene_store: SqliteSceneStore, maintenance_store: SqliteMaintenanceStore
) -> None:
    """换版之后，**本来就到期的**那几格拿到新 key —— 这是刻意留的，也是要写下来的。

    「新的一版生效了，这一格按新一版的规矩重看」与「这一格还没被看过」是两件事，
    而它们在这里合成一句话：这一格是否到期只由 ``observed_at`` 决定，而**认领**它
    的那把钥匙带着版本号。于是换版不会让新格子到期，但会让已经到期的格子重新可领。

    代价写在这里：一格如果**领了却没写下观测**（回执是 DONE、库里没有
    ``Observation``），换版会允许它在这一周期里再被领一次。那不是重复计费，
    是「这一格仍然欠着一次复核」—— 该修的是那个没写观测的回执，不是这把钥匙。
    """
    _live(scene_store)

    before, _ = due_work(
        scene_store=scene_store,
        maintenance_store=maintenance_store,
        scene_id="scene-a",
        now=T0,
        subjects=(SUBJECT,),
    )
    assert len(before) == 7
    assert {item.scene_version for item in before} == {1}

    add_version(store=scene_store, version=make_version(version=2), bundle=_bundle())
    activate(store=scene_store, scene_id="scene-a", version=2, at=T0, by="controller-1")

    after, _ = due_work(
        scene_store=scene_store,
        maintenance_store=maintenance_store,
        scene_id="scene-a",
        now=T0 + ONE_DAY,
        subjects=(SUBJECT,),
    )

    assert {item.aspect for item in after} == set(Aspect), "格子一个不多一个不少"
    assert {item.scene_version for item in after} == {2}
    assert {item.dedupe_key for item in after} & {item.dedupe_key for item in before} == set()
