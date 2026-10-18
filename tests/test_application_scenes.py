"""``application/scenes.py`` 的判据：立项、改版、批准、暂停、恢复、归档。

## 这一层唯一真正承重的东西是**读 → 改 → CAS 写**

``SceneRecord`` 的三个变更方法是纯的，落盘要靠 ``put_record(..., expected_revision=)``。
所以这里的每一条写用例都在问同一件事：**那个 expected_revision 是从哪来的**。
写成 ``changed.revision - 1``（自我推导）也能跑通全部正常路径 —— 只在两个控制端
同时动一个场景时才红。所以下面有一组「两条独立读 → 两条写」的用例，它们才是
这一层的执行器，其余的正常路径用例都只是它的陪衬。

## 排除清单的核对在**这一层**

``SceneVersion.exclusions_cover`` 只回答「我给的这个清单覆不覆盖你给的那些词」，
它不知道 profile 是谁。§4 那条「不得用产品扩展绕开已批准 profile 的排除清单」
需要一个同时握着两份东西的位置，那就是 ``validate``。下面那几条用例是它的执行器。
"""

from __future__ import annotations

from datetime import timedelta, timezone
from pathlib import Path

import pytest

from marketops.adapters.sqlite.scenes import SqliteSceneStore
from marketops.application.scenes import (
    activate,
    add_version,
    archive,
    create_draft,
    impact_between,
    list_scenes,
    pause,
    resume,
    validate,
)
from marketops.domain.enums import BudgetLine
from marketops.domain.errors import ConfigError, SceneConflictError, StoreError
from marketops.domain.profile import DeclaredProfile, ProfileBundle
from marketops.domain.scene import (
    ImpactScope,
    PauseReason,
    SceneBudgetLine,
    SceneRecord,
    SceneState,
    SceneStateError,
)

from tests.fakes import synthetic_scene_files
from tests.test_scene_store_contract import T0, WORKSPACE, make_version

UTC = timezone.utc
ONE_DAY = timedelta(days=1)


def _declared(
    *exclusions: str, profile_id: str = "uk-acme-natural-grass"
) -> DeclaredProfile:
    """一个只带排除清单的 profile 声明。

    刻意**合成**而不是读真实 profile：真实排除清单的内容是业务事实，
    把它当测试输入会让「核对的逻辑对不对」和「这份 SOP 写了什么」纠缠在一起。
    """
    return DeclaredProfile(
        schema="marketops/profile.v2",
        name=profile_id,
        display_name="synthetic",
        country="GB",
        exclusions=tuple({"id": item} for item in exclusions),
        compliance={},
    )


def _bundle(*exclusions: str, profile_id: str = "uk-acme-natural-grass") -> ProfileBundle:
    """一个规则全空的 bundle。

    ``rules`` 与 ``role_terms`` 是必填的，所以这里显式给空的 —— 本文件考的
    全是 Scene 与 profile 之间的**绑定关系**（排除清单、profile_id），
    与定级规则无关。给空的比给一组编造的规则更诚实：编造的规则会让
    「这个 bundle 的规则是什么」变成一个没人知道答案的问题。
    """
    files = synthetic_scene_files()
    return ProfileBundle(
        profile_id=profile_id,
        status="TEST",
        rules={},
        role_terms=(),
        scene_version=files.version,
        scene_files=files,
        profile=_declared(*exclusions, profile_id=profile_id),
    )


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    return tmp_path / "state" / "marketops.db"


@pytest.fixture
def store(db_path: Path) -> SqliteSceneStore:
    return SqliteSceneStore(db_path)


def _go_live(store: SqliteSceneStore) -> SceneRecord:
    """立项并批准第 1 版。下面一半用例的起跑线。"""
    create_draft(store=store, version=make_version(), bundle=_bundle())
    return activate(store=store, scene_id="scene-a", version=1, at=T0, by="controller-1")


# --- 核对 ---------------------------------------------------------------------


def test_a_version_covering_the_profile_exclusions_passes() -> None:
    """覆盖 = 场景的清单是 profile 那一份的**超集**。"""
    report = validate(make_version(), _bundle("golf-course", "bowling-green"))

    assert report.ok, report.problems
    assert report.scene_id == "scene-a"
    assert report.version == 1


def test_adding_exclusions_is_allowed() -> None:
    """新场景、新产品可以**加**排除，不可以减。"""
    report = validate(
        make_version(exclusions=("golf-course", "bowling-green", "cemetery")),
        _bundle("golf-course"),
    )

    assert report.ok, report.problems


def test_dropping_one_exclusion_is_refused_and_names_it() -> None:
    """少一条要**说出是哪一条** —— 拒绝的理由必须能被转述给写配置的人。"""
    report = validate(make_version(exclusions=("golf-course",)), _bundle("golf-course", "bowling-green"))

    assert not report.ok
    assert len(report.problems) == 1
    assert "bowling-green" in report.problems[0]


def test_a_profile_mismatch_is_refused_before_anything_else() -> None:
    """声称的 profile 与装载进来的不是同一份 —— 核对下去毫无意义。"""
    report = validate(make_version(), _bundle(profile_id="some-other-profile"))

    assert not report.ok
    assert any("profile" in problem for problem in report.problems)


def test_an_empty_budget_is_refused() -> None:
    """未声明不等于无限 —— 两种含义在行为上必须分得开。"""
    report = validate(make_version(budget=()), _bundle())

    assert not report.ok
    assert any("预算" in problem for problem in report.problems)


def test_a_zero_ceiling_is_refused_before_the_first_call() -> None:
    """零额度是一次都跑不了，必须在装载期就红，而不是等第一次调用撞上去。"""
    report = validate(
        make_version(budget=(SceneBudgetLine(line=BudgetLine.SEARCH, ceiling=0),)), _bundle()
    )

    assert not report.ok
    assert any("零" in problem for problem in report.problems)


def test_uncalibrated_refresh_is_reported_but_does_not_block() -> None:
    """§4 允许有效期来自「已批准策略」**或**被显式标注的未校准缺省值。

    把它算成 problem 会让每一个尚未校准的场景都装不出来 —— 而那会把
    「未校准」推成「不要写这个字段」，正是要防的那件事。
    """
    report = validate(make_version(), _bundle())

    assert report.ok, report.problems
    assert len(report.uncalibrated_aspects) == 7


# --- 立项 ---------------------------------------------------------------------


def test_a_first_version_becomes_a_draft(store: SqliteSceneStore) -> None:
    record = create_draft(store=store, version=make_version(), bundle=_bundle())

    assert record.state is SceneState.DRAFT
    assert record.latest_version == 1
    # 草稿**没有**生效版本 —— 「未获批准的新场景只能 draft/preview」在类型上的样子
    assert record.effective_version is None
    assert not record.accepts_new_work
    assert store.get_version("scene-a", 1) is not None


def test_the_first_version_cannot_be_number_three(store: SqliteSceneStore) -> None:
    with pytest.raises(ConfigError, match="第 1 版"):
        create_draft(store=store, version=make_version(version=3), bundle=_bundle())


def test_the_first_version_cannot_claim_a_predecessor() -> None:
    """``supersedes=0`` 曾被判为合法：``0 == 1 - 1``。

    第 0 版不是一个版本号，是「还没写过」的同义词（``version`` 的校验器逐字
    拒绝了它）。这条判据放在这里，是因为 ``create_draft`` 直接依赖这条成立。
    """
    with pytest.raises(ValueError, match="supersedes"):
        make_version(version=1, supersedes=0)


def test_a_refused_version_writes_nothing(store: SqliteSceneStore) -> None:
    """拒绝必须在**写之前**发生 —— 半写进去的版本比不写更糟。"""
    with pytest.raises(ConfigError):
        create_draft(store=store, version=make_version(), bundle=_bundle("something-else"))

    assert store.get_record("scene-a") is None
    assert store.versions("scene-a") == ()


def test_creating_the_same_scene_twice_is_a_conflict(store: SqliteSceneStore) -> None:
    """``expected_revision=None`` 的意思是「必须不存在」，不是「随便写」。"""
    create_draft(store=store, version=make_version(), bundle=_bundle())

    with pytest.raises(SceneConflictError):
        create_draft(store=store, version=make_version(), bundle=_bundle())


# --- 改版与影响 ---------------------------------------------------------------


def test_a_new_version_is_registered_without_going_live(store: SqliteSceneStore) -> None:
    _go_live(store)
    second = make_version(version=2, product="acme-natural-grass-v2")

    record, impact = add_version(store=store, version=second, bundle=_bundle())

    assert record.latest_version == 2
    assert record.effective_version == 1, "新写一版不生效，在跑的仍是第 1 版"
    assert record.pending_version == 2
    assert (impact.from_version, impact.to_version) == (1, 2)


def test_a_never_activated_scene_has_no_pending_version(store: SqliteSceneStore) -> None:
    """``pending_version`` 回答的是「有一版写了但没生效」，而不是「这版还没生效」。

    一个从未激活过的场景里**每一版**都还没生效，所以那个字段没有可回答的东西 ——
    它返回 ``None``。把这两件事混起来，草稿态会显示出一个「待生效的第 1 版」，
    而它其实连批准都还没有过。
    """
    create_draft(store=store, version=make_version(), bundle=_bundle())

    assert store.get_record("scene-a").pending_version is None


def test_the_impact_says_what_must_be_redone(store: SqliteSceneStore) -> None:
    """§4 逐字要求「变更必须说明影响」。改产品 → 重取来源、重判产品适配。"""
    create_draft(store=store, version=make_version(), bundle=_bundle())
    second = make_version(version=2, product="acme-natural-grass-v2")

    _, impact = add_version(store=store, version=second, bundle=_bundle())

    assert ImpactScope.SOURCE_SUPPLY in impact.refetch
    assert ImpactScope.PRODUCT_FIT in impact.reassess
    assert not impact.is_empty
    # ``reusable`` 是**全部**减去前两类 —— 回执里那一栏永远是全的，
    # 读的人不需要知道表里有哪些字段，就能看出「这次没碰到的面」是哪几个。
    untouched = set(impact.refetch) | set(impact.reassess)
    assert set(impact.reusable) == set(ImpactScope) - untouched


def test_a_change_that_invalidates_nothing_says_so(store: SqliteSceneStore) -> None:
    """改备注与改预算只改「以后做多少」，不改「已经看到的东西还成不成立」。

    ``budget`` 与 ``refresh`` **不在** ``SCENE_IMPACT_RULES`` 里，这是故意的：
    一份把改预算也报成「要重判全部路线」的影响表，会让真正要重做的那一次
    淹没在噪音里。
    """
    create_draft(store=store, version=make_version(), bundle=_bundle())

    _, impact = add_version(
        store=store,
        version=make_version(version=2, note="只改了备注"),
        bundle=_bundle(),
    )

    assert impact.is_empty
    assert len(impact.reusable) == len(list(ImpactScope))


def test_a_version_that_skips_a_number_is_refused() -> None:
    """跳号会在历史里留下一个「从未写过」的编号，而没人能回答它是丢了还是不存在。"""
    with pytest.raises(ValueError, match="supersedes"):
        make_version(version=3, supersedes=1)


def test_a_record_pointing_at_a_version_nobody_can_read_is_refused(
    store: SqliteSceneStore,
) -> None:
    """记录说最新是第 2 版、而库里只有第 1 版 —— 这是**算不出来**，不是「没有影响」。

    这里手工把记录推到那个状态（``put_record`` 是公开入口，模型也允许
    ``latest_version=2`` 而版本表里空着），因为版本表是 append-only、没有删除入口：
    一个读不到版本的状态只可能来自库被外部改过。
    """
    record = create_draft(store=store, version=make_version(), bundle=_bundle())
    store.put_record(
        record.model_copy(update={"latest_version": 2, "revision": 1}), expected_revision=0
    )

    with pytest.raises(ConfigError, match="读不到"):
        add_version(store=store, version=make_version(version=3, supersedes=2), bundle=_bundle())


# --- 批准 / 暂停 / 恢复 / 归档 ------------------------------------------------


def test_activate_is_the_approval(store: SqliteSceneStore) -> None:
    create_draft(store=store, version=make_version(), bundle=_bundle())

    record = activate(store=store, scene_id="scene-a", version=1, at=T0, by="controller-1")

    assert record.state is SceneState.ACTIVE
    assert record.effective_version == 1
    assert record.accepts_new_work
    assert record.approvals[-1].by == "controller-1"


def test_activation_refuses_an_anonymous_approver(store: SqliteSceneStore) -> None:
    """一条没有主体的批准记录无法被追问。"""
    create_draft(store=store, version=make_version(), bundle=_bundle())

    with pytest.raises(SceneStateError, match="主体"):
        activate(store=store, scene_id="scene-a", version=1, at=T0, by="   ")


def test_activation_cannot_reach_past_the_written_versions(store: SqliteSceneStore) -> None:
    create_draft(store=store, version=make_version(), bundle=_bundle())

    with pytest.raises(SceneStateError, match="不在 1"):
        activate(store=store, scene_id="scene-a", version=2, at=T0, by="controller-1")


def test_pause_keeps_the_effective_version(store: SqliteSceneStore) -> None:
    """暂停不改「生效的是哪一版」—— 否则恢复时得有人再猜一次。"""
    _go_live(store)

    paused = pause(store=store, scene_id="scene-a", at=T0 + ONE_DAY, reason=PauseReason.OPERATOR)

    assert paused.state is SceneState.PAUSED
    assert paused.effective_version == 1
    assert not paused.accepts_new_work
    assert paused.pause_reason is PauseReason.OPERATOR


def test_resume_does_not_reset_the_version(store: SqliteSceneStore) -> None:
    """§4：「恢复不得重发已发邮件或重吃整个母池」—— 执行方式就是恢复不动任何游标。"""
    _go_live(store)
    pause(store=store, scene_id="scene-a", at=T0, reason=PauseReason.POLICY)

    resumed = resume(store=store, scene_id="scene-a", at=T0 + ONE_DAY)

    assert resumed.state is SceneState.ACTIVE
    assert resumed.effective_version == 1
    assert resumed.pause_reason is None


def test_a_scene_that_never_went_live_cannot_be_resumed(store: SqliteSceneStore) -> None:
    """没有可恢复的生效版本时，「恢复」与「首次开工」是两件事。"""
    create_draft(store=store, version=make_version(), bundle=_bundle())

    with pytest.raises(SceneStateError, match="activate"):
        resume(store=store, scene_id="scene-a", at=T0)


def test_archive_is_terminal(store: SqliteSceneStore) -> None:
    """归档是「这个市场不再经营」，不是「暂时不看」—— 后者是 PAUSED。"""
    _go_live(store)
    archived = archive(store=store, scene_id="scene-a", at=T0 + 2 * ONE_DAY)

    assert archived.state is SceneState.ARCHIVED
    with pytest.raises(SceneStateError, match="归档"):
        activate(store=store, scene_id="scene-a", version=1, at=T0, by="controller-1")


# --- 乐观锁：这一层的执行器 ---------------------------------------------------


def test_two_controllers_reading_then_writing_only_one_wins(db_path: Path) -> None:
    """两个控制端各自读到同一版、各自决定写 —— 第二个必须整次拒绝。

    这是本层唯一不能靠「正常路径跑通」证明的判据：把 ``expected_revision``
    写成 ``changed.revision - 1``（从将要写的那条记录自我推导）时，上面每一条
    用例都还是绿的，只有这一条会红。两个 store 各持一个连接、各自提交，
    数据库层挡住第二个。
    """
    first = SqliteSceneStore(db_path)
    second = SqliteSceneStore(db_path)
    create_draft(store=first, version=make_version(), bundle=_bundle())
    activate(store=first, scene_id="scene-a", version=1, at=T0, by="controller-1")

    read_by_both = first.get_record("scene-a")
    assert read_by_both is not None

    pause(store=first, scene_id="scene-a", at=T0 + ONE_DAY, reason=PauseReason.OPERATOR)

    with pytest.raises(SceneConflictError):
        second.put_record(
            read_by_both.transition(SceneState.ARCHIVED, at=T0 + 2 * ONE_DAY),
            expected_revision=read_by_both.revision,
        )

    assert first.get_record("scene-a").state is SceneState.PAUSED


def test_a_self_derived_expected_revision_is_refused_before_the_cas(db_path: Path) -> None:
    """「改完对象直接写回」与「拿着读到的版本写」在库层是**两条不同的路径**。

    上面那条证明 CAS 挡得住一个**过期**的写者；这一条证明一个**自我推导**的
    ``expected_revision`` 根本到不了 CAS —— 它在 ``revision != expected + 1``
    上就红了。两件事都要有：少了第二条，把 ``expected_revision`` 写成
    ``record.revision - 1`` 会让每一次「跳过自增直接写回」都静默成功，
    而 CAS 一次都不会触发。
    """
    store = SqliteSceneStore(db_path)
    record = create_draft(store=store, version=make_version(), bundle=_bundle())

    with pytest.raises(StoreError, match="读到的 0 加一"):
        store.put_record(record.model_copy(update={"revision": 5}), expected_revision=0)

    assert store.get_record("scene-a").revision == record.revision


# --- 列举与影响查询 -----------------------------------------------------------


def test_listing_is_scoped_by_workspace(store: SqliteSceneStore) -> None:
    create_draft(store=store, version=make_version(), bundle=_bundle())
    create_draft(store=store, version=make_version("scene-b", workspace="ws-other"), bundle=_bundle())

    assert [r.scene_id for r in list_scenes(store, workspace=WORKSPACE)] == ["scene-a"]
    assert [r.scene_id for r in list_scenes(store)] == ["scene-a", "scene-b"]


def test_impact_between_any_two_versions(store: SqliteSceneStore) -> None:
    create_draft(store=store, version=make_version(), bundle=_bundle())
    add_version(
        store=store, version=make_version(version=2, region=("GB-Scotland",)), bundle=_bundle()
    )

    impact = impact_between(store, scene_id="scene-a", from_version=1, to_version=2)

    assert impact.from_version == 1
    assert impact.refetch, "改了地区就必须重取来源与资产事实"


def test_a_missing_version_is_not_an_empty_impact(store: SqliteSceneStore) -> None:
    """一次算不出来的影响不是一次「没有影响」—— 后者会让「改了但没记录」看起来像「没改」。"""
    create_draft(store=store, version=make_version(), bundle=_bundle())

    with pytest.raises(ConfigError, match="读不到"):
        impact_between(store, scene_id="scene-a", from_version=1, to_version=7)


def test_an_unknown_scene_is_refused_not_created(store: SqliteSceneStore) -> None:
    with pytest.raises(ConfigError, match="scene-missing"):
        archive(store=store, scene_id="scene-missing", at=T0)
