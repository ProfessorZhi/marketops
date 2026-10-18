"""``marketops/application/watch_runs.py`` 的判据 —— §17 那条链的**第四格**。

四格是：``SavedView -> enable Watch`` -> ``due object`` -> ``one watch Run``。前三格
在盘上已经有判据（``tests/test_views_application.py``），而**第四格今天只有这一份**：
到期的请求出来了，那个「谁把它送去受理」的问题在此之前没有任何执行器。

这一族盯的四件事（TASK-H §3 的四条承重点），每一条都有一个**看起来完全正常**的错法：

1. **幂等 / 重放**。一个在驱动这一层现编幂等键的实现，在单次调用上与正确实现逐字
   相同；它的表现是「同一份到期请求被醒来两次 ⇒ 盘上两行 Run」—— 而两行 Run 各自
   都正常，只是同一件事被做了两遍。
2. **同一个 Account 落在三份视图里**。去重**不在这一层**：它是维护账 due 格的性质
   （``domain/maintenance.py::due_dedupe_key`` 的分段不含 view、不含 watch）。这一族
   把这一句**量出来**，量的读数与 TASK-H §3.2 的期望不同 —— 见下面那条用例的
   docstring 与报告里的反例。
3. **不自动创建未来的 Run**。一次醒来只处理这一刻到期的；把「下次到期」的那几条也
   建成 Run 的实现在这一次醒来上完全正常。
4. **事实串 workspace**。工作区、那一版 Scene、幂等键、这一批主体、这一次的信封
   各来自哪里，是这一片唯一一处「同一条事实有两个出处就会分岔」的地方。前两样
   **只能**从盘上读，所以这里判签名与行为各一半。

真实 profile 在这里也照跑（照 ``tests/test_run_admission.py`` 的 ``_bundle`` /
``_context`` / ``_plan``）：受理面拿到的上下文与计划因此与生产上是同一批。那一族的
夹具**不复用**（它的 ``_scene_store`` docstring 逐字：跨文件复用会让某个文件为了
另一处的需要改一个共享的 helper），这里各写一份同形的。
"""

from __future__ import annotations

import ast
import inspect
import shutil
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

import pytest
import yaml

from marketops.adapters.memory import (
    InMemoryAccountPoolStore,
    InMemoryMaintenanceStore,
    InMemoryRunStore,
    InMemorySavedViewStore,
    InMemorySceneStore,
    InMemoryWatchStore,
)
from marketops.adapters.profile_loader import load_profile
from marketops.application import watch_runs
from marketops.application.accounts import merge_accounts
from marketops.application.plan_preview import PreviewOutcome, preview_plan
from marketops.application.views import (
    put_saved_view,
    put_watch,
    watch_due_requests,
)
from marketops.application.watch_runs import (
    WatchRunOutcome,
    WatchRunRow,
    run_due_watches,
)
from marketops.config import REPO_ROOT, Settings
from marketops.domain.account import MarketAccount
from marketops.domain.enums import AccountStatus, BudgetLine, ExecutionMode
from marketops.domain.errors import ConfigError, StoreError
from marketops.domain.maintenance import ALL_ASPECTS, Observation, RefreshPlan
from marketops.domain.outreach import OutreachPolicyDecl
from marketops.domain.profile import ProfileBundle
from marketops.domain.research_context import (
    ResearchContext,
    ScenarioCandidate,
    ScenarioProvenance,
)
from marketops.domain.research_plan import EffectiveResearchPlan
from marketops.domain.run import RunBudgetLine, run_id
from marketops.domain.saved_view import ViewVisibility
from marketops.domain.scene import (
    PauseReason,
    SceneBudgetLine,
    SceneRecord,
    SceneState,
    SceneVersion,
)
from marketops.domain.subject import SUBJECT_KIND_ACCOUNT
from marketops.domain.watch import NotifyOn, Watch, WatchCeiling, watch_due_key

MODULE = REPO_ROOT / "marketops" / "application" / "watch_runs.py"
#: 正控：一个**已知**进口了适配层的模块。扫描器坏掉时它会一起红 —— 拿一个只有反例的
#: 扫描器当判据，会在「它什么都没扫到」的时候看起来也是绿的。
CONTROL = REPO_ROOT / "marketops" / "interfaces" / "composition.py"

PROFILES_DIR = REPO_ROOT / "profiles"
DEFAULT_PROFILE = "uk-acme-natural-grass"

WORKSPACE = "uk-acme-natural-grass"
#: **另一个**工作区名。它存在只为一件事：把「工作区从盘上那一版 Scene 读」与
#: 「照这次装载的 profile 名猜」拆开 —— 今天这个部署里两者恰好同名，所以不换掉它，
#: 那两种做法在每一条用例上都绿。这个字符串是**部署**给一版场景起的名，与 profile
#: 目录名本来就不是同一件事（``SceneVersion`` 上有独立的 ``profile_id`` 那一格）。
ELSEWHERE = "ws-devon-review"
SCENE = "scene-devon"
OWNER = "member-b"
OTHER_OWNER = "member-d"
ACCOUNT = "acct-one"
T0 = datetime(2026, 9, 28, 9, 0, tzinfo=timezone.utc)
CUTOFF = T0 + timedelta(days=1)
#: 同一条链上的**下一轮**：``check_every_days`` 的缺省是 7，而 ``RefreshPlan`` 最短的
#: 一条节奏是 30 天 —— 8 天之后这一条 Watch 到期，而刚看过的格子仍然不到期。
LATER = T0 + timedelta(days=8)

TASK: dict[str, Any] = {
    "entity_classes": ("school",),
    "asset_classes": ("pitch",),
    "organization_classes": ("trust",),
    "scenario_candidates": (
        ScenarioCandidate(
            name="secondary school",
            reading="k12",
            provenance=ScenarioProvenance.DECLARED,
            source="req-1",
        ),
    ),
    "objective": "find accounts for the approved offer",
    "evidence_requirements": ("official_site",),
}


# --- 夹具 ---------------------------------------------------------------------


def _bundle_with(root: Path, *, profile: Callable[[dict], None] | None = None) -> ProfileBundle:
    """复制真实 profile、按需改 ``profile.yaml``，再走**真实装载器**读回来。"""
    profiles_dir = root / "profiles"
    target = profiles_dir / DEFAULT_PROFILE
    profiles_dir.mkdir(parents=True, exist_ok=True)
    shutil.copytree(PROFILES_DIR / DEFAULT_PROFILE, target)
    if profile is not None:
        path = target / "profile.yaml"
        document = yaml.safe_load(path.read_text(encoding="utf-8"))
        profile(document)
        path.write_text(
            yaml.safe_dump(document, allow_unicode=True, sort_keys=False), encoding="utf-8"
        )
    settings = Settings(
        data_root=root / "runtime", profiles_dir=profiles_dir, profile=DEFAULT_PROFILE
    )
    return load_profile(settings)


def _set(path: tuple[str, ...], value: Any) -> Callable[[dict], None]:
    def apply(document: dict) -> None:
        node = document
        for step in path[:-1]:
            node = node[step]
        node[path[-1]] = value

    return apply


def _context(bundle: ProfileBundle) -> ResearchContext:
    declared = bundle.profile
    assert declared is not None, "夹具应当总是装载到声明块"
    return ResearchContext(geo=declared.country, **TASK)  # type: ignore[arg-type]


@pytest.fixture
def world(tmp_path: Path):
    """一份真声明、一份真上下文、一份真编出来的计划。"""
    bundle = _bundle_with(tmp_path, profile=_set(("country",), "GB"))
    context = _context(bundle)
    preview = preview_plan(bundle, context)
    assert preview.outcome is PreviewOutcome.READY, preview.held
    plan: EffectiveResearchPlan | None = preview.plan
    assert plan is not None
    return bundle, context, plan


@dataclass
class Stage:
    """这一族用得到的那几本账。**全是测试替身**，但形状与端口逐格相同。"""

    scene_store: InMemorySceneStore
    views: InMemorySavedViewStore
    watches: InMemoryWatchStore
    pool: InMemoryAccountPoolStore
    maintenance: InMemoryMaintenanceStore
    runs: InMemoryRunStore


def _record(
    *,
    workspace: str = WORKSPACE,
    scene_id: str = SCENE,
    state: SceneState = SceneState.ACTIVE,
    latest_version: int = 1,
    effective_version: int | None = 1,
) -> SceneRecord:
    return SceneRecord(
        scene_id=scene_id,
        workspace=workspace,
        state=state,
        latest_version=latest_version,
        effective_version=effective_version,
        created_at=T0,
        updated_at=T0,
        pause_reason=PauseReason.OPERATOR if state is SceneState.PAUSED else None,
    )


def _scene(scene_id: str = SCENE, workspace: str = WORKSPACE) -> SceneVersion:
    return SceneVersion(
        scene_id=scene_id,
        workspace=workspace,
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
        profile_id=DEFAULT_PROFILE,
        profile_version="2026-09-01",
        created_at=T0,
        supersedes=None,
    )


def _account(account_id: str, *, workspace: str = WORKSPACE) -> MarketAccount:
    return MarketAccount(
        workspace=workspace,
        account_id=account_id,
        organisation=f"Org {account_id}",
        evidence_ids=("ev-1",),
        first_seen_at=T0,
        country="GB",
        status=AccountStatus.RESEARCHED,
    )


def _stage(
    *,
    workspace: str = WORKSPACE,
    record: SceneRecord | None = None,
    version: SceneVersion | None = None,
) -> Stage:
    """一本摆好的盘。``record`` / ``version`` 给了就照给的摆（负例要用它们造形状）。"""
    scene_store = InMemorySceneStore()
    scene_store.put_record(
        _record(workspace=workspace) if record is None else record, expected_revision=None
    )
    if version is not None:
        scene_store.put_version(version)
    elif record is None or record.effective_version is not None:
        scene_store.put_version(_scene(workspace=workspace))
    pool = InMemoryAccountPoolStore(merge_accounts)
    pool.upsert_account(_account(ACCOUNT, workspace=workspace))
    return Stage(
        scene_store=scene_store,
        views=InMemorySavedViewStore(),
        watches=InMemoryWatchStore(),
        pool=pool,
        maintenance=InMemoryMaintenanceStore(),
        runs=InMemoryRunStore(),
    )


@pytest.fixture
def stage() -> Stage:
    return _stage()


def _save(
    stage: Stage,
    view_id: str,
    *,
    owner: str = OWNER,
    visibility: ViewVisibility = ViewVisibility.SHARED,
    workspace: str = WORKSPACE,
) -> None:
    """存一份视图。查询空过滤器 = 「这个工作区的全部账号」，而池子里只有一个。"""
    put_saved_view(
        store=stage.views,
        view_id=view_id,
        workspace=workspace,
        owner=owner,
        name=f"视图 {view_id}",
        query={"filter": []},
        expected_revision=None,
        visibility=visibility,
        at=T0,
    )


def _watch(
    stage: Stage,
    watch_id: str,
    *,
    view_id: str | None = None,
    subjects: tuple[tuple[str, str], ...] = (),
    owner: str = OWNER,
    next_due_at: datetime | None = T0,
    enabled: bool = True,
    check_every_days: int = 7,
    units: int = 10,
    workspace: str = WORKSPACE,
) -> None:
    put_watch(
        store=stage.watches,
        scene_store=stage.scene_store,
        scene_id=SCENE,
        watch_id=watch_id,
        workspace=workspace,
        owner=owner,
        name=f"监控 {watch_id}",
        ceilings=(WatchCeiling(line=BudgetLine.SEARCH, units=units),),
        notify_on=(NotifyOn.CHANGED,),
        view_id=view_id,
        subjects=subjects,
        check_every_days=check_every_days,
        next_due_at=next_due_at,
        enabled=enabled,
        at=T0,
    )


def _drive(
    stage: Stage,
    world,
    *,
    at: datetime = T0,
    cutoff: datetime = CUTOFF,
    scene_id: str = SCENE,
    watches: Any = None,
    runs: Any = None,
):
    """醒一次。**这是这条链唯一那个「谁把到期请求送去受理」的入口。**"""
    _bundle, context, plan = world
    return run_due_watches(
        watches=stage.watches if watches is None else watches,
        views=stage.views,
        pool=stage.pool,
        runs=stage.runs if runs is None else runs,
        scene_store=stage.scene_store,
        maintenance=stage.maintenance,
        scene_id=scene_id,
        context=context,
        plan=plan,
        cutoff=cutoff,
        at=at,
    )


def _observe(stage: Stage, account_id: str, *, at: datetime = T0) -> None:
    """「这一批主体的工作真的落了地」—— 七个面各一条观测。"""
    for aspect in ALL_ASPECTS:
        stage.maintenance.put_observation(
            Observation(
                workspace=WORKSPACE,
                subject_kind=SUBJECT_KIND_ACCOUNT,
                subject_id=account_id,
                aspect=aspect,
                observed_at=at,
                source_ref="test",
            )
        )


def _outcomes(receipt) -> dict[str, WatchRunOutcome]:
    return {row.watch_id: row.outcome for row in receipt.rows}


def _runs_on_disc(store: InMemoryRunStore) -> int:
    """盘上有几行 Run。**这个数没有公开的门。**

    ``RunStore`` 只公开 ``get(run_id)`` 与 ``chain_progress``，而后者**按设计跳过**
    没有窗口的行（``ports/runs.py`` 逐字：手动与 watch 的 Run 不属于任何一条链）——
    于是「这一趟盘上多了一行 watch Run 没有」在今天的协议上**读不出来**。这是报告
    里如实交回的一条缺口，这里读私有那本字典，并且只读它一个数。
    """
    return len(store._runs)  # noqa: SLF001


# --- 1 · 回执的形状：三档是构造，不是送达时的自觉 ---------------------------------


def test_a_row_cannot_be_both_refused_and_admitted() -> None:
    """``run_id`` 有值 ⟺ 受理成了。两格同时错开会让「被拒」与「建了一行」同时为真。"""
    with pytest.raises(ValueError) as caught:
        WatchRunRow(
            watch_id="watch-a",
            watch_revision=1,
            view_id="view-a",
            outcome=WatchRunOutcome.REFUSED,
            run_id="run-x",
            reason="被拒了",
        )
    assert "run_id" in str(caught.value)

    with pytest.raises(ValueError):
        WatchRunRow(
            watch_id="watch-a",
            watch_revision=1,
            view_id="view-a",
            outcome=WatchRunOutcome.ADMITTED,
        )


def test_a_row_that_was_not_admitted_must_say_why() -> None:
    """一份没有理由的未受理行，在报告上与「什么事都没有」同形。"""
    with pytest.raises(ValueError) as caught:
        WatchRunRow(
            watch_id="watch-a",
            watch_revision=1,
            view_id="view-a",
            outcome=WatchRunOutcome.REFUSED,
        )
    assert "理由" in str(caught.value)

    # 反过来：一行建出了 Run 却带一句「理由」，读的人会去找那道不存在的闸门。
    with pytest.raises(ValueError):
        WatchRunRow(
            watch_id="watch-a",
            watch_revision=1,
            view_id="view-a",
            outcome=WatchRunOutcome.ADMITTED,
            run_id="run-x",
            reason="其实没事",
        )


def test_a_request_that_cannot_be_executed_carries_no_subjects() -> None:
    """执行不了的那一份**不许被补上主体** —— 建不出 Run 是构造，不是自觉。"""
    with pytest.raises(ValueError) as caught:
        WatchRunRow(
            watch_id="watch-a",
            watch_revision=1,
            view_id="view-a",
            outcome=WatchRunOutcome.BLOCKED,
            subjects=((SUBJECT_KIND_ACCOUNT, ACCOUNT),),
            reason="视图不在了",
        )
    assert "主体" in str(caught.value)


def test_attempted_and_advanced_read_their_own_columns() -> None:
    """两格各答各的问题：**试过没有**与**推走了没有**。执行不了的那一档两格都是假。"""
    blocked = WatchRunRow(
        watch_id="watch-a",
        watch_revision=1,
        view_id="view-a",
        outcome=WatchRunOutcome.BLOCKED,
        reason="视图不在了",
    )
    assert blocked.attempted is False and blocked.advanced is False

    refused = WatchRunRow(
        watch_id="watch-b",
        watch_revision=1,
        view_id="view-b",
        outcome=WatchRunOutcome.REFUSED,
        subjects=((SUBJECT_KIND_ACCOUNT, ACCOUNT),),
        reason="这一批主体上一条到期的都没有",
    )
    assert refused.attempted is True and refused.advanced is False

    admitted = WatchRunRow(
        watch_id="watch-c",
        watch_revision=1,
        view_id="view-c",
        outcome=WatchRunOutcome.ADMITTED,
        run_id="run-x",
        next_due_at=LATER,
    )
    assert admitted.attempted is True and admitted.advanced is True


# --- 2 · 承重点 1：幂等 / 重放 ---------------------------------------------------


class _StallingWatchStore:
    """一条**写不下去**的 Watch 存储：别的照旧，只有 ``put_watch`` 抛 ``StoreError``。

    这不是替身，是**一次失败**：CAS 撞车与库写不动都是这个形状（``record_watch_check``
    的两条拒绝）。它的用处是造出重放最自然的那个入口 —— 上一趟受理成功了而
    ``next_due_at`` 没推走，于是**下一次醒来看到的还是同一份到期请求**。
    """

    def __init__(self, inner: InMemoryWatchStore) -> None:
        self._inner = inner

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)

    def put_watch(self, watch: Watch, *, expected_revision: int | None):
        raise StoreError("这一趟写不动（用例故意造的）：next_due_at 没被推走")


def test_replaying_the_same_due_request_hands_back_the_same_run(stage, world) -> None:
    """**同一份到期请求醒来两次 ⇒ 盘上仍是一行 Run。**

    幂等键是推导属性（``WatchDueRequest.idempotency_key``，含 ``workspace`` /
    ``watch_id`` / ``watch_revision`` / ``due_at``），而这里判的是驱动**用它**、
    并且**不自己另编一个**：一次醒来里现编的键在单次调用上与正确实现逐字相同，
    只有在第二次醒来时才现形 —— 那时它建出第二行 Run，而两行各自都正常。

    ``next_due_at`` 推不走的两种情况必须分开报：``check_note`` 有话说，而
    ``next_due_at`` 留 ``None``（那一行 Run 是真的建出来了，把它报成失败会让人
    去重放；把它报成「推走了」会让下一次扫描读不到这真的到期）。
    """
    _save(stage, "view-a")
    _watch(stage, "watch-a", view_id="view-a")
    stalling = _StallingWatchStore(stage.watches)

    first = _drive(stage, world, watches=stalling)
    assert _outcomes(first) == {"watch-a": WatchRunOutcome.ADMITTED}
    row = first.rows[0]
    assert row.check_note, "推不走时没有一句话 —— 一次静默的失败"
    assert row.next_due_at is None, "推不走却报了一个时刻"
    assert _runs_on_disc(stage.runs) == 1

    # 键是**这条链**的那一个，不是驱动现编的：run_id 由 (workspace, key) 推出来。
    key = watch_due_key(
        workspace=WORKSPACE, watch_id="watch-a", watch_revision=1, due_at=T0
    )
    assert row.run_id == run_id(WORKSPACE, key)

    second = _drive(stage, world, watches=stalling)
    assert _outcomes(second) == {"watch-a": WatchRunOutcome.ADMITTED}
    assert second.rows[0].run_id == row.run_id, "重放拿回的不是同一行"
    assert _runs_on_disc(stage.runs) == 1, "重放之后盘上多了一行 Run"


def test_the_next_wake_at_the_same_instant_finds_nothing_due(stage, world) -> None:
    """常态那一支：推走了 ``next_due_at``，所以同一个时刻的下一次醒来**看不到它**。

    这一条与上一条是同一件事的两半：推走成功时防重复靠的是**时刻**，推走失败时
    靠的是**键**。少任何一半，重复受理的窗口都开着。
    """
    _save(stage, "view-a")
    _watch(stage, "watch-a", view_id="view-a")

    first = _drive(stage, world)
    assert _outcomes(first) == {"watch-a": WatchRunOutcome.ADMITTED}
    assert first.rows[0].next_due_at == T0 + timedelta(days=7)
    assert first.rows[0].check_note == ""

    second = _drive(stage, world)
    assert second.rows == ()
    assert second.servable_seen == 0 and second.blocked_seen == 0
    assert _runs_on_disc(stage.runs) == 1


# --- 3 · 承重点 2：同一个 Account 落在三份视图里 ---------------------------------


def test_three_views_over_one_account_are_one_account_of_work(stage, world) -> None:
    """三份视图盯同一个 Account：**实测的读数是「一次 sweep 建出三行」**。

    TASK-H §3.2 逐字期望「一次 sweep 之后**恰好一行** Run 被受理，另两条被拒」。
    实测不是这样，而且不该是这样：``admit_run`` 的 ``_require_due`` 读的是**观测与
    信号**，不读认领，所以同一批主体在同一时刻对**三道不同的闸门**都算「到期」
    （``domain/maintenance.py::what_is_due`` 逐字：它从不读 claims）。去重的位置是
    维护账的 due 格（``due_dedupe_key`` 的分段**不含 view、不含 watch**），而它
    生效的时机是**下一次**到期 —— §2.14 自己那句逐字的措辞就是「第一条**跑过**
    之后……后两条**再到期时**……一条都没有 ⇒ 拒」。

    所以这里量的是那条链真正的形状：**同一份工作被三个视图各要求了一次，而它
    只值一趟**。三行 Run 是三个视图各自的**要求**（它们各有各的幂等键与信封），
    而「这一趟该不该真的去刷」由 due 格在下一次说话 —— 第一趟的观测一落地，
    三条在同一个时刻被同一道闸门一起拒。

    这一条也是承重点 2 的**变异落点**：绕过 due 格（不再问 ``what_is_due``）的实现
    在第二次 sweep 上会照旧受理三行，于是下面那两个断言一起红。
    """
    names = ("view-a", "view-b", "view-c")
    watched = [f"watch-{name}" for name in names]
    for name in names:
        _save(stage, name)
        _watch(stage, f"watch-{name}", view_id=name)

    first = _drive(stage, world)
    assert _outcomes(first) == {watch_id: WatchRunOutcome.ADMITTED for watch_id in watched}
    # 三份视图展开出来的是**同一个**主体的同一个格子（视图里今天只有一个账号）。
    assert {row.subjects for row in first.rows} == {((SUBJECT_KIND_ACCOUNT, ACCOUNT),)}
    assert _runs_on_disc(stage.runs) == 3

    # 第一趟的工作真的落了地：那一个主体的七个面都看过了。
    _observe(stage, ACCOUNT)

    second = _drive(stage, world, at=LATER, cutoff=LATER + timedelta(days=1))
    assert _outcomes(second) == {watch_id: WatchRunOutcome.REFUSED for watch_id in watched}
    for row in second.rows:
        assert row.subjects == ((SUBJECT_KIND_ACCOUNT, ACCOUNT),)
        assert "到期或信号" in row.reason, "拒词没有说清它的来源是什么"
        assert "1 个主体" in row.reason, "拒词没有报出判了几个主体"
        # 拒是**走到了闸门**的那一档：这一趟看完了，下一次该往后推。
        assert row.advanced is True
    assert _runs_on_disc(stage.runs) == 3, "第二次 sweep 又建了 Run"


def test_a_watch_that_named_its_subjects_is_not_expanded(stage, world) -> None:
    """点过名的主体**原样送进去** —— §2.14 ② 的「驱动不做翻译，只做并集」。

    实现取的是那张表里更窄的一支：**只在没点名 subjects 时展开**。给一条既点名
    主体又指着视图的 Watch 补上视图里那些人，等于替它扩大了范围（而且是在一份
    已经写明范围的规则上加人）。
    """
    _save(stage, "view-a")
    named = ((SUBJECT_KIND_ACCOUNT, "acct-two"),)
    _watch(stage, "watch-a", view_id="view-a", subjects=named)

    receipt = _drive(stage, world)
    assert _outcomes(receipt) == {"watch-a": WatchRunOutcome.ADMITTED}
    assert receipt.rows[0].subjects == named, (
        "驱动把视图里那些人并进来了 —— 那不是在翻译，是在替这条 Watch 改范围"
    )


# --- 4 · 承重点 3：不自动创建未来的 Run ------------------------------------------


def test_one_wake_creates_only_what_is_due(stage, world) -> None:
    """**一次醒来只做这一刻到期的那几条。**

    盘上摆三条：一条到期、一条三十天后到期、一条关着。只有第一条该建 Run ——
    而另外两条**连一行读数都不该有**（它们不是「被拒」，是**没到期**：一条不到期的
    Watch 压根不产出一份请求，而把「没到期」写成一行 REFUSED 会让报告上多出一批
    看起来需要处置的行）。
    """
    _save(stage, "view-a")
    _watch(stage, "watch-due", view_id="view-a")
    _watch(stage, "watch-later", view_id="view-a", next_due_at=T0 + timedelta(days=30))
    _watch(stage, "watch-off", view_id="view-a", enabled=False)

    receipt = _drive(stage, world)
    assert _outcomes(receipt) == {"watch-due": WatchRunOutcome.ADMITTED}
    assert receipt.servable_seen == 1 and receipt.blocked_seen == 0
    assert receipt.every_request_is_answered
    assert _runs_on_disc(stage.runs) == 1


def test_a_wake_with_nothing_due_writes_nothing(stage, world) -> None:
    """一刻都没有到期的时候，这一趟**一个字节都不写**。

    这一条是**弱**的，如实写在这里：空扫时 ``every_request_is_answered`` 是真的，
    而它什么也没说（0 == 0 + 0）。真正承重的是下面那两个断言 —— 盘上有一条到期的
    Watch 而它不在这趟的名单里，Run 与 ``next_due_at`` 都**原样不动**：一次醒来
    不许因为「路过」就把谁的时刻推走。
    """
    _save(stage, "view-a")
    _watch(stage, "watch-later", view_id="view-a", next_due_at=T0 + timedelta(days=30))

    receipt = _drive(stage, world)
    assert receipt.rows == ()
    assert receipt.servable_seen == 0 and receipt.blocked_seen == 0
    assert _runs_on_disc(stage.runs) == 0
    assert stage.watches.get_watch("watch-later").next_due_at == T0 + timedelta(days=30)


# --- 5 · 承重点 4：事实串 workspace ---------------------------------------------


def test_the_run_is_built_on_the_workspace_the_scene_on_disc_says(world) -> None:
    """工作区、幂等键、这一版 Scene 三样都**从盘上读**，而且读的是同一处。

    ``run_due_watches`` 不接受 ``workspace`` / ``scene`` / ``version``（签名那一条在
    下面）：它按 ``scene_id`` 读记录与生效版本，工作区取**那一版**的，扫的是它，
    建出来的 Run 的 ``spec.workspace`` 也是它（``run_admission.py::spec_of`` 是
    ``RunSpec`` 的唯一生产者，而它那一格读的正是 ``scene.workspace``）。

    ``ELSEWHERE`` 这个工作区名是这一条的全部要点：它**不是**这次装载的 profile 名。
    """
    stage = _stage(workspace=ELSEWHERE)
    _save(stage, "view-a", workspace=ELSEWHERE)
    _watch(stage, "watch-a", view_id="view-a", workspace=ELSEWHERE)

    receipt = _drive(stage, world)
    assert receipt.workspace == ELSEWHERE
    assert receipt.scene_version == 1
    assert _outcomes(receipt) == {"watch-a": WatchRunOutcome.ADMITTED}

    row = receipt.rows[0]
    stored = stage.runs.get(row.run_id or "")
    assert stored is not None, "受理面说建了，盘上读不回来"
    assert stored.spec.workspace == ELSEWHERE
    assert stored.spec.selector == SCENE
    key = watch_due_key(
        workspace=ELSEWHERE, watch_id="watch-a", watch_revision=1, due_at=T0
    )
    assert row.run_id == run_id(ELSEWHERE, key)


def test_the_wake_uses_the_effective_version_not_the_latest_one(world) -> None:
    """「生效的是哪一版」读的是**记录上那一格**，不是「作者写到第几版」。

    ``latest_version`` 大于 ``effective_version`` 是一个**正常状态**：第 2 版写下来了
    但还没启用。把这两格混起来的实现，在只有一版的部署上与正确实现逐字相同 ——
    而它的表现是「一版刚写下来、还没批准的规则立刻开始管派发」。

    这一条也是那两格唯一的落点：版号**到不了** ``RunSpec``（``spec_of`` 只放
    ``selector=scene.scene_id``），所以回执上的 ``scene_version`` 是它唯一的读数。
    """
    newer = _scene().model_copy(
        update={"version": 2, "region": ("GB-England-Cornwall",)}
    )
    stage = _stage(record=_record(latest_version=2, effective_version=1))
    stage.scene_store.put_version(newer)
    _save(stage, "view-a")
    _watch(stage, "watch-a", view_id="view-a")

    receipt = _drive(stage, world)
    assert receipt.scene_version == 1, "这一趟用的是作者写到的那一版，不是生效的那一版"
    assert _outcomes(receipt) == {"watch-a": WatchRunOutcome.ADMITTED}


def test_the_caller_cannot_dictate_what_must_come_from_disc() -> None:
    """那几格**给不进来**：工作区、那一版 Scene、actor、幂等键、主体、信封、模式。

    这是本文件里最承重的一条签名判据，与 ``run_admission.py`` 的那条同款：把
    ``workspace`` 变成一个关键字参数，读起来只是「多了一个可选入参」；而它的含义
    是**由调用方自己挑一条授权链**。同一句话写进 docstring 没有执行器，写进签名
    才有 —— 所以这里判签名。

    ``scene_store`` / ``maintenance`` 必须是**必填**（没有缺省值）：一次「没接上」
    悄悄变成「判不了就当没有到期」会落下一行没有任何依据的 watch Run，而它在记录
    上与一次真的因到期而起的维护一模一样。
    """
    forbidden = {
        "workspace",
        "scene",
        "version",
        "scene_version",
        "actor",
        "owner",
        "idempotency_key",
        "subjects",
        "budget",
        "envelope",
        "execution_mode",
        "mode",
    }
    parameters = inspect.signature(run_due_watches).parameters
    assert not (set(parameters) & forbidden), (
        f"驱动的签名接受了这些本应由盘上决定的参数：{sorted(set(parameters) & forbidden)}"
    )

    for name in ("scene_store", "maintenance", "scene_id", "watches", "views", "pool",
                 "runs", "context", "plan", "cutoff", "at"):
        assert parameters[name].default is inspect.Parameter.empty, (
            f"{name} 有了缺省值 —— 一个可以省略的盘就是「没接上也照样跑」"
        )
        assert parameters[name].kind is inspect.Parameter.KEYWORD_ONLY, (
            f"{name} 不是关键字参数 —— 位置传参会让两张同形的表在调用点上互换"
        )


def test_the_module_does_not_read_a_clock_of_its_own() -> None:
    """这一层不读时钟、不睡、不重试 —— 什么时候醒来是外面那个驱动的事。

    ``at`` 是参数，与 ``application/scheduling.py`` 的 ``tick`` 同一条纪律。
    """
    called = _name_calls(MODULE) | _attribute_calls(MODULE)
    forbidden = {"now", "utc_now", "today", "time", "sleep", "monotonic"}
    assert not (called & forbidden), (
        f"watch_runs 自己读了时钟或睡了：{sorted(called & forbidden)}"
    )
    assert "utc_now" not in _imported(MODULE)


def test_the_module_cannot_reach_the_adapters() -> None:
    """这一层 import 不到 ``adapters/``。正控在场，免得扫描器坏了也报绿。"""
    assert any("adapters" in name for name in _imported(CONTROL)), (
        f"正控 {CONTROL} 没有进口适配层 —— 扫描器坏了，这条判据已经失去意义"
    )
    leaked = sorted(name for name in _imported(MODULE) if ".adapters" in name)
    assert leaked == [], f"驱动这一层进口了适配层：{leaked}"


def test_the_module_is_listed_in_the_application_table() -> None:
    """``application/__init__.py`` 那张穷举表里有它一行（只加自己那一行）。"""
    assert watch_runs.__name__ == "marketops.application.watch_runs"
    source = (REPO_ROOT / "marketops" / "application" / "__init__.py").read_text(
        encoding="utf-8"
    )
    assert "| ``watch_runs`` |" in source


# --- 6 · 分档，以及两条不许越的线 -----------------------------------------------


def test_a_request_whose_view_is_gone_is_blocked_and_never_given_subjects(
    stage, world
) -> None:
    """目标视图不在 ⇒ ``BLOCKED``：**不试受理、一个字都不给主体**。

    判据与措辞都取自域层（``Watch.due_request`` 给出的 ``blocked_reason``），这里
    逐字比一次：驱动在这一支上不许另造一句判词，也不许替它补一批主体 —— 一份执行
    不了的请求建不出 Run 是构造（``domain/watch.py:199-203``）。

    「视图被删掉」在这里用一条**从没有过**的 ``view_id`` 造：目标视图在不在**只有
    ``views.current`` 读得到**，而这两种情况对它是同一个答案（``SavedViewStore``
    上没有删除那一格，所以盘上造不出「删过」这个形状）。
    """
    _watch(stage, "watch-nowhere", view_id="view-never-existed")

    expected = watch_due_requests(
        store=stage.watches, views=stage.views, workspace=WORKSPACE, at=T0
    )
    assert len(expected.blocked) == 1 and expected.servable == ()

    receipt = _drive(stage, world)
    assert _outcomes(receipt) == {"watch-nowhere": WatchRunOutcome.BLOCKED}
    row = receipt.rows[0]
    assert row.subjects == (), "执行不了的那一份被补上了主体"
    assert row.attempted is False and row.advanced is False
    assert row.reason == expected.blocked[0].blocked_reason, (
        "驱动在这一支上另造了一句判词 —— 拒绝的理由只有一个出处"
    )
    assert _runs_on_disc(stage.runs) == 0


def test_the_actor_of_the_expansion_is_the_watch_owner(stage, world) -> None:
    """展开视图用的是**这条 Watch 自己写的 owner**，不是调用方，也不是某个常量。

    三条 Watch 各指一份私有视图，三种组合各答一次：

    * 别人的私有视图 ⇒ **拒**（而理由里两个人的名字都在）；
    * 自己的私有视图 ⇒ 展开；
    * **另一个 owner 的**自己的私有视图 ⇒ 也展开。

    第三条不是凑数：少了它，一个把 actor 写死成 ``OWNER`` 的实现在前两条上都绿
    —— 那正是这一条要挡的错法（替别人读他的私有图，或者反过来替自己读别人的）。
    私有视图的 owner 是它的一半身份，所以第三份**必须另存**：一份视图不可能同时
    属于两个人。
    """
    _save(stage, "view-of-other", owner=OTHER_OWNER, visibility=ViewVisibility.PRIVATE)
    _save(stage, "view-of-mine", owner=OWNER, visibility=ViewVisibility.PRIVATE)
    _save(stage, "view-of-theirs", owner=OTHER_OWNER, visibility=ViewVisibility.PRIVATE)
    _watch(stage, "watch-a", view_id="view-of-other", owner=OWNER)
    _watch(stage, "watch-b", view_id="view-of-mine", owner=OWNER)
    _watch(stage, "watch-c", view_id="view-of-theirs", owner=OTHER_OWNER)

    receipt = _drive(stage, world)
    assert _outcomes(receipt) == {
        "watch-a": WatchRunOutcome.REFUSED,
        "watch-b": WatchRunOutcome.ADMITTED,
        "watch-c": WatchRunOutcome.ADMITTED,
    }
    refused = {row.watch_id: row for row in receipt.rows}["watch-a"]
    assert "private" in refused.reason
    assert OWNER in refused.reason and OTHER_OWNER in refused.reason, (
        "拒词里没有把「谁的图、替谁读」这两个名字都写出来"
    )
    # 一次「视图读不到」**不改**这一条 Watch 的时刻：那一版还没被看过。
    assert refused.advanced is False
    assert stage.watches.get_watch("watch-a").next_due_at == T0
    for row in receipt.rows:
        if row.outcome is WatchRunOutcome.ADMITTED:
            assert row.subjects == ((SUBJECT_KIND_ACCOUNT, ACCOUNT),)
    assert _runs_on_disc(stage.runs) == 2


def test_every_request_the_sweep_saw_has_exactly_one_row(stage, world) -> None:
    """扫描看到的每一份请求都在回执里有一行 —— **这就是「拒 != 没跑」那一格**。

    三份请求：一份视图目标（可执行）、一份点名主体（可执行）、一份指着不存在的
    视图（执行不了）。行数、两个计数、以及逐条的 ``watch_id`` 三样一起对。
    """
    _save(stage, "view-a")
    _watch(stage, "watch-view", view_id="view-a")
    _watch(stage, "watch-named", subjects=((SUBJECT_KIND_ACCOUNT, ACCOUNT),))
    _watch(stage, "watch-nowhere", view_id="view-never-existed")

    sweep = watch_due_requests(
        store=stage.watches, views=stage.views, workspace=WORKSPACE, at=T0
    )
    receipt = _drive(stage, world)
    assert receipt.every_request_is_answered
    assert receipt.servable_seen == len(sweep.servable)
    assert receipt.blocked_seen == len(sweep.blocked)
    assert len(receipt.rows) == len(sweep.servable) + len(sweep.blocked)
    assert sorted(row.watch_id for row in receipt.rows) == sorted(
        [request.watch_id for request in sweep.servable]
        + [request.watch_id for request in sweep.blocked]
    )
    assert receipt.admitted == tuple(
        row for row in receipt.rows if row.watch_id != "watch-nowhere"
    )


class _MeddlingWatchStore:
    """在「扫描之后、受理之前」动手脚的 Watch 存储：**这不是替身，是一场竞态**。

    真的发生在这一刻的事只有两种：有人改了这条 Watch（下一次 CAS 就是那个形状），
    或者有人把它删了（``get_watch`` 回 ``None`` 就是这个形状）。两种都要在驱动这一
    层被挡住，因为手上那份请求描述的是**另一版规则**。
    """

    def __init__(
        self,
        inner: InMemoryWatchStore,
        *,
        after_sweep: Callable[[InMemoryWatchStore], None] | None = None,
        vanish: frozenset[str] = frozenset(),
    ) -> None:
        self._inner = inner
        self._after_sweep = after_sweep
        self._vanish = vanish

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)

    def due_watches(self, *, workspace: str, at: datetime) -> tuple[Watch, ...]:
        rows = self._inner.due_watches(workspace=workspace, at=at)
        if self._after_sweep is not None:
            self._after_sweep(self._inner)
        return rows

    def get_watch(self, watch_id: str) -> Watch | None:
        if watch_id in self._vanish:
            return None
        return self._inner.get_watch(watch_id)


def test_a_watch_that_changed_under_the_sweep_is_refused(stage, world) -> None:
    """扫描与受理之间有人改了这条 Watch ⇒ **拒**，并把两个版号都报出来。

    照旧版受理就是拿一批它自己已经不要的主体去刷一遍（可能正是刚被删掉的那批）。
    这一份请求**不推** ``next_due_at``：那一版还没被看过，推走它等于把一次要人处置的
    改动静默一周。
    """
    _save(stage, "view-a")
    _watch(stage, "watch-a", view_id="view-a")

    def rename(inner: InMemoryWatchStore) -> None:
        current = inner.get_watch("watch-a")
        assert current is not None
        inner.put_watch(
            current.next_revision(at=T0).model_copy(update={"name": "改过了"}),
            expected_revision=1,
        )

    receipt = _drive(stage, world, watches=_MeddlingWatchStore(stage.watches, after_sweep=rename))
    assert _outcomes(receipt) == {"watch-a": WatchRunOutcome.REFUSED}
    row = receipt.rows[0]
    assert "第 1 版" in row.reason and "第 2 版" in row.reason, row.reason
    assert row.advanced is False
    assert stage.watches.get_watch("watch-a").next_due_at == T0
    assert _runs_on_disc(stage.runs) == 0


def test_a_watch_deleted_under_the_sweep_is_refused(stage, world) -> None:
    """扫描与受理之间这条 Watch 被删了 ⇒ 拒，而不是**静默跳过**。

    跳过它会让这一份请求在回执上消失 —— 也就是「有人把它拿掉了，而这一份请求还
    举着它」这件事一条痕迹都不留。
    """
    _save(stage, "view-a")
    _watch(stage, "watch-a", view_id="view-a")

    receipt = _drive(
        stage,
        world,
        watches=_MeddlingWatchStore(stage.watches, vanish=frozenset({"watch-a"})),
    )
    assert _outcomes(receipt) == {"watch-a": WatchRunOutcome.REFUSED}
    assert "被删了" in receipt.rows[0].reason
    assert receipt.every_request_is_answered
    assert _runs_on_disc(stage.runs) == 0


def test_the_envelope_comes_from_the_watchs_own_ceilings(stage, world) -> None:
    """这一次允许用多少，取这条 Watch 自己声明的那一格。**没有第二个生产者。**

    ``admit_run`` 的 ``budget`` 逐字：「由调用方给 —— 上游还没有按窗口算本轮信封的
    生产者」。于是这一格里唯一能担这个角色的是盘上那条 Watch 的 ``ceilings`` ——
    一个在这里发一份常量信封的驱动，会让每一条 Watch 的预算声明在盘上读得出来、
    而花的钱按另一份算。
    """
    _save(stage, "view-a")
    _watch(stage, "watch-a", view_id="view-a", units=7)

    receipt = _drive(stage, world)
    assert _outcomes(receipt) == {"watch-a": WatchRunOutcome.ADMITTED}
    stored = stage.runs.get(receipt.rows[0].run_id or "")
    assert stored is not None
    assert stored.spec.budget == (RunBudgetLine(line=BudgetLine.SEARCH, ceiling=7),)
    assert stored.spec.execution_mode is ExecutionMode.WATCH


# --- 7 · 整批判不了的那三句（三句话的处置不同） ---------------------------------


def test_a_scene_that_is_not_on_disc_stops_the_whole_wake(stage, world) -> None:
    """``scene_id`` 不在盘上 ⇒ **整批**判不了，一个字节都不写。"""
    _save(stage, "view-a")
    _watch(stage, "watch-a", view_id="view-a")

    with pytest.raises(ConfigError) as caught:
        _drive(stage, world, scene_id="scene-nowhere")
    assert "不在盘上" in str(caught.value)
    assert _runs_on_disc(stage.runs) == 0


def test_a_scene_without_an_effective_version_is_its_own_message(world) -> None:
    """记录说「还没有生效版本」⇒ 它自己那一句（这个市场还没开张）。"""
    stage = _stage(record=_record(state=SceneState.DRAFT, effective_version=None))

    with pytest.raises(ConfigError) as caught:
        _drive(stage, world)
    assert "还没有生效版本" in str(caught.value)
    assert _runs_on_disc(stage.runs) == 0


def test_a_version_the_record_claims_but_the_store_lacks_is_its_own_message(world) -> None:
    """记录说生效版本是第 2 版，而库里只有第 1 版 ⇒ **另一句**（存储不自洽）。

    折成上一句的后果是报告上说「还没开张」，而它其实开着 —— 一个要人去查存储的
    问题被读成一句正常状态。
    """
    stage = _stage(
        record=_record(latest_version=2, effective_version=2),
        version=_scene(),
    )

    with pytest.raises(ConfigError) as caught:
        _drive(stage, world)
    message = str(caught.value)
    assert "读不到那一版" in message
    # 第二句会**引用**第一句来做对照，所以不能拿第一个短语来判重（它是这句的一部分）。
    # 能分开这两句的是它们各自的处置：草稿说「还没开张」，这一句说「存储不自洽」。
    assert "存储不自洽" in message
    assert "DRAFT" not in message, "两种情况被折成同一句话了"
    assert _runs_on_disc(stage.runs) == 0


# --- AST 那一族共用的量具（与 ``tests/test_run_admission.py`` 同形） --------------


def _imported(path: Path) -> frozenset[str]:
    """一个模块 import 了哪些**绝对模块名**（相对 import 按包名还原）。"""
    package = ".".join(path.relative_to(REPO_ROOT).with_suffix("").parts[:-1])
    found: set[str] = set()
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level == 0:
                found.add(node.module or "")
                continue
            parts = package.split(".")
            prefix = ".".join(parts[: len(parts) - (node.level - 1)])
            found.add(f"{prefix}.{node.module}" if node.module else prefix)
    return frozenset(found)


def _name_calls(path: Path) -> frozenset[str]:
    """被**按名字**调用的那批（``open(...)``、``compile(...)``）。"""
    found: set[str] = set()
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            found.add(node.func.id)
    return frozenset(found)


def _attribute_calls(path: Path) -> frozenset[str]:
    """被**按属性**调用的那批（``datetime.now()``、``x.read_text()``）。"""
    found: set[str] = set()
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            found.add(node.func.attr)
    return frozenset(found)
