"""``marketops/application/run_admission.py`` 的判据 —— §B17 的**受理面**。

这一族盯的都不是会崩的失败，而是**看起来完全正常**的那几种：

1. **十一格里该由盘上取的那几格变成了调用方给的。** 一份由请求参数决定的工作区，
   在记录上读起来与一份来自 Scene 的一模一样；而它意味着调用方可以自己挑一条
   授权链。所以这里判的是**签名**（:func:`test_the_caller_cannot_dictate_the_derived_cells`）
   —— 一句话写在 docstring 里没有执行器，写在签名里才有。
2. **计划与上下文配错了。** 一份从 A 上下文编出来的计划挂到一次基于 B 上下文的
   Run 上，此后每一处读回执的人都会照那份计划理解这一轮做过什么。这一族里
   :func:`test_the_plan_must_be_compiled_from_this_context` 判它当场抛。
3. **这一层偷偷变得能花钱、能派发。** 「受理不是执行」如果只靠自觉，那么接线的那
   一天由「谁顺手加了一个 dispatcher 参数」决定它派不派工。所以这里按 AST 扫这一
   层的 import（不许碰 ``adapters/``）与调用名（不许读时钟）。

真实 profile 在这里也照跑：声明与上下文都走**真实装载器与真实编译器**
（照 ``tests/test_plan_preview.py`` 的 ``_bundle_with``／``_context``），编出来的
计划再交给受理面。于是「受理这一层拿到的东西」与生产上拿到的是同一批。
"""

from __future__ import annotations

import ast
import inspect
import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

import pytest
import yaml

from marketops.adapters.memory import (
    InMemoryBudgetLedger,
    InMemoryMaintenanceStore,
    InMemoryRunStore,
    InMemorySceneStore,
)
from marketops.adapters.profile_loader import load_profile
from marketops.application import run_admission
from marketops.application.plan_preview import PreviewOutcome, preview_plan
from marketops.application.run_admission import admit_run
from marketops.config import REPO_ROOT, Settings
from marketops.domain.enums import BudgetLine, ExecutionMode, RunStatus
from marketops.domain.errors import RunReconciliationError, RunSpecError
from marketops.domain.maintenance import ALL_ASPECTS, Observation, RefreshPlan
from marketops.domain.outreach import OutreachPolicyDecl
from marketops.domain.profile import ProfileBundle
from marketops.domain.research_context import (
    ResearchContext,
    ScenarioCandidate,
    ScenarioProvenance,
    fingerprint_of,
)
from marketops.domain.research_plan import EffectiveResearchPlan
from marketops.domain.run_delta import DeltaCell, PoolEffect, RunDelta
from marketops.domain.run import (
    AuthorizationWindow,
    RunBudgetLine,
    RunSpec,
    policy_versions_of,
    run_id,
)
from marketops.domain.scene import (
    PauseReason,
    SceneBudgetLine,
    SceneRecord,
    SceneState,
    SceneVersion,
)
from marketops.ports.runs import admission_refusal

MODULE = REPO_ROOT / "marketops" / "application" / "run_admission.py"
#: 正控：一个**已知**进口了适配层的模块。扫描器坏掉时它会一起红 —— 拿一个只有
#: 反例的扫描器当判据，会在「它什么都没扫到」的时候看起来也是绿的。
CONTROL = REPO_ROOT / "marketops" / "interfaces" / "composition.py"

PROFILES_DIR = REPO_ROOT / "profiles"
DEFAULT_PROFILE = "uk-acme-natural-grass"

WORKSPACE = "uk-acme-natural-grass"
SCENE = "scene-devon"
KEY = "run-2026-09-28-a"
#: 同一条链上的**第二轮**。受理面的每一条累计判据都要有一个「下一轮」才问得出来 ——
#: 只有一个键的话，那些判据全都退化成「第一次受理成功了吗」。
KEY2 = "run-2026-09-28-b"
T0 = datetime(2026, 9, 28, 9, 0, tzinfo=timezone.utc)
CUTOFF = T0 + timedelta(days=1)

#: 同一份自然语言任务（照 ``tests/test_plan_preview.py`` 的 ``TASK``，逐字同一份）。
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


def _bundle(root: Path, *, country: str = "GB") -> ProfileBundle:
    return _bundle_with(root, profile=_set(("country",), country))


def _context(bundle: ProfileBundle, **overrides: Any) -> ResearchContext:
    declared = bundle.profile
    assert declared is not None, "夹具应当总是装载到声明块"
    base: dict[str, Any] = {"geo": declared.country, **TASK}
    base.update(overrides)
    return ResearchContext(**base)  # type: ignore[arg-type]


def _plan(bundle: ProfileBundle, context: ResearchContext) -> EffectiveResearchPlan:
    preview = preview_plan(bundle, context)
    assert preview.outcome is PreviewOutcome.READY, preview.held
    assert preview.plan is not None
    return preview.plan


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


def _envelope() -> tuple[RunBudgetLine, ...]:
    return (RunBudgetLine(line=BudgetLine.SEARCH, ceiling=20),)


#: 授权窗口的截止。**晚于** :data:`CUTOFF` —— 「本轮的截止超出窗口」是另一条校验器
#: 判的（``RunSpec`` 的跨格校验器），而这一族的用例问的不是它。
WINDOW_UNTIL = T0 + timedelta(days=30)


def _window() -> AuthorizationWindow:
    return AuthorizationWindow(max_objects=50, budget=_envelope(), not_after=WINDOW_UNTIL)


@pytest.fixture
def world(tmp_path: Path):
    """一份真声明、一份真上下文、一份真编出来的计划、一版真 Scene。"""
    bundle = _bundle(tmp_path)
    context = _context(bundle)
    return bundle, context, _plan(bundle, context), _scene()


# --- 1 · 十一格是怎么拼出来的 -------------------------------------------------


def test_the_spec_is_built_from_the_scene_and_the_context(world) -> None:
    """该从盘上与上下文取的那几格**确实是取来的**，不是重打的。

    逐格钉住取值来源：``workspace`` / ``selector`` 来自 Scene（授权链的第一段），
    ``objective`` 来自上下文，三个摘要各由它们自己的生产者给出。任何一格改成
    「调用方顺手给的」，在这里都会红 —— 而那正是这一族存在的理由。
    """
    bundle, context, plan, scene = world
    store = InMemoryRunStore()

    admitted = admit_run(
        store,
        scene=scene,
        context=context,
        plan=plan,
        budget=_envelope(),
        cutoff=CUTOFF,
        idempotency_key=KEY,
        at=T0,
        execution_mode=ExecutionMode.MANUAL,
    )

    spec = admitted.spec
    assert spec.workspace == scene.workspace == WORKSPACE
    assert spec.selector == scene.scene_id == SCENE
    assert spec.objective == context.objective
    assert spec.context_ref == fingerprint_of(context).digest
    assert spec.context_ref == plan.fingerprint_digest
    assert spec.plan_ref == plan.digest
    assert spec.policy_versions == policy_versions_of(plan)
    assert spec.policy_versions, "策略版本不得为空 —— 空的那一份读起来是「没有策略变更」"


def test_the_spec_digest_matches_the_one_the_store_will_compare(world) -> None:
    """受理写下的那一行，`spec_sha256` 就是这份规格的摘要。

    不一致的形态是「同一个键配另一份内容」的判据恒不触发（第二份被当成第一份的
    重发），而它不抛异常、不留日志。
    """
    bundle, context, plan, scene = world
    store = InMemoryRunStore()
    admitted = admit_run(
        store,
        scene=scene,
        context=context,
        plan=plan,
        budget=_envelope(),
        cutoff=CUTOFF,
        idempotency_key=KEY,
        at=T0,
        execution_mode=ExecutionMode.MANUAL,
    )

    read_back = store.get(admitted.run_id)
    assert read_back is not None
    assert read_back.spec_sha256 == admitted.spec_sha256
    assert read_back.spec == admitted.spec


def test_resubmitting_the_same_submission_returns_the_same_row(world) -> None:
    """受理面这一层**不新增**幂等语义：重发走的还是存储层那条主键。"""
    bundle, context, plan, scene = world
    store = InMemoryRunStore()
    kwargs = {
        "scene": scene,
        "context": context,
        "plan": plan,
        "budget": _envelope(),
        "cutoff": CUTOFF,
        "idempotency_key": KEY,
        "execution_mode": ExecutionMode.MANUAL,
    }

    first = admit_run(store, at=T0, **kwargs)
    second = admit_run(store, at=T0 + timedelta(hours=5), **kwargs)

    assert first.run_id == second.run_id == run_id(WORKSPACE, KEY)
    assert second.created_at == first.created_at


# --- 2 · 计划与上下文必须配得起来 ---------------------------------------------


def test_the_plan_must_be_compiled_from_this_context(tmp_path: Path) -> None:
    """计划与上下文配错时**当场抛**，而不是把两样都收下。

    这条判据要的是「抛出来的那句话里两个摘要都在」：只说「不匹配」的异常，会让人
    在两个都不会变的值之间反复试。
    """
    bundle = _bundle(tmp_path)
    context = _context(bundle)
    other = _context(bundle, objective="把目标换成另一件事")
    assert other.objective != context.objective

    store = InMemoryRunStore()
    with pytest.raises(ValueError) as caught:
        admit_run(
            store,
            scene=_scene(),
            context=context,
            plan=_plan(bundle, other),
            budget=_envelope(),
            cutoff=CUTOFF,
            idempotency_key=KEY,
            at=T0,
            execution_mode=ExecutionMode.MANUAL,
        )

    message = str(caught.value)
    assert fingerprint_of(other).digest in message
    assert fingerprint_of(context).digest in message
    assert store.get(run_id(WORKSPACE, KEY)) is None, "配错时一个字节都不该落盘"


def test_a_context_with_hard_ambiguities_cannot_be_admitted(tmp_path: Path) -> None:
    """讲不清的上下文推不出指纹，于是**受理不出来** —— 逐条点名是哪几维。

    这不是一次受理拒绝（处置不是「把规格改对再提交」，而是「先把那几条答了」），
    所以它走的是 ``fingerprint_of`` 自己的那条抛法，而那句话必须点得出来名。
    """
    bundle = _bundle(tmp_path)
    ambiguous = _context(bundle, entity_classes=())
    clean = _context(bundle)
    store = InMemoryRunStore()

    with pytest.raises(ValueError) as caught:
        admit_run(
            store,
            scene=_scene(),
            context=ambiguous,
            plan=_plan(bundle, clean),
            budget=_envelope(),
            cutoff=CUTOFF,
            idempotency_key=KEY,
            at=T0,
            execution_mode=ExecutionMode.MANUAL,
        )

    assert "[entity_classes]" in str(caught.value)


# --- 3 · 拒绝来自那一份共用判据，不是这一层的第二份实现 -----------------------


def test_the_refusal_is_verbatim_the_ports_judgement(world) -> None:
    """受理面拒下来的理由与 ``ports.runs.admission_refusal`` **逐字相同**。

    这一层要是自己拼一句「自动模式暂不支持」，盘上就有两处对「为什么拒」的说法；
    而它们漂移的那天，读的人会照工具那句话去改设置，而真正拦住他的是另一条规则。

    ## 表上只剩 ``bounded_auto``：``watch`` 从这张表上搬走了（切片 f）

    这一条问的是「这一层有没有自己拼一句话」，所以表上的每一个模式都必须**今天真的
    被拒**、且拒因确实落在 ``admission_refusal`` 里。``watch`` 两条都不再满足：它在
    切片 f 之后受理得下来（前提是这一批主体上有一条到期的格子），而那条判定**要读
    盘**，所以它住在 ``_require_due``（本文件第 4 节判它；工具面那一层在
    ``tests/test_mcp_runs.py`` 第 11 节）。留一个今天走得通的模式在这张表里，红的
    时候只会说「它没抛」，而那句话把下一个人送回一个不是问题的地方。

    ``bounded_auto`` 取 ``window=None``：e2 之前它必带窗口才进得了这一条（那时受理
    面对带不带窗口给的是同一个答案，而带窗口才走得到 ``admission_refusal`` 那一句）。
    **今天反过来了** —— 带窗口的 ``bounded_auto`` 在一条空链上会被**受理**（那正是
    e2 打开的那扇门），所以只剩不带窗口那一份还落在 ``admission_refusal`` 里。

    **另一条判据（那条链到了 stop 就该拒）不在这里**：它也有一条「逐字相同」的比法，
    但那条要读盘、要有一份真的累计读数。它住在
    ``tests/test_run_window_admission.py::test_the_window_refusal_is_verbatim_the_ports_judgement``。
    """
    bundle, context, plan, scene = world
    store = InMemoryRunStore()
    mode = ExecutionMode.BOUNDED_AUTO

    #: 拿**同一份内容**去问端口那条判据，再把它的回答与用例抛出来的那句话逐字比。
    #: 逐字比而不是「两边都非空」：漂移的那一天，两边各自都还是一句像样的话。
    reference = RunSpec(
        workspace=WORKSPACE,
        objective=context.objective,
        selector=SCENE,
        context_ref=fingerprint_of(context).digest,
        plan_ref=plan.digest,
        execution_mode=mode,
        window=None,
        budget=_envelope(),
        cutoff=CUTOFF,
        policy_versions=policy_versions_of(plan),
        idempotency_key=KEY,
    )
    expected = admission_refusal(reference, at=T0)
    assert expected, "这条判据的前提是那个模式今天确实被拒"

    with pytest.raises(RunSpecError) as caught:
        admit_run(
            store,
            scene=scene,
            context=context,
            plan=plan,
            budget=_envelope(),
            cutoff=CUTOFF,
            idempotency_key=KEY,
            at=T0,
            execution_mode=mode,
            window=None,
        )

    assert str(caught.value) == expected


def test_a_bounded_auto_with_a_window_is_admitted_on_an_empty_chain(world) -> None:
    """**反过来的一半**：带窗口的 ``bounded_auto`` 在空链上真的受理得下来。

    缺了它，上面那条判据在受理面**永远**拒自动模式时也是绿的 —— 而「永远拒」不是
    e2 要的状态，它是 e1 的状态。这一条钉的是「打开」这件事本身，§503 那句
    「只有类名/字段/接口的无消费者实现不算完成」在这里的落点：``AuthorizationWindow``
    到这一刻才有了消费者。

    **账本是必给的，而这一条判据本身就是那个证据**：第一版没有传 ``ledger``，拒词是
    「这条链的累计预算读不到：受理面没有拿到账本读数」。窗口的三个阈值里，预算那一格
    的读数只能从账本取（``RunStore`` 按设计读不到金额），所以「有窗口」与「窗口判得
    了」之间差一份账本 —— 而缺了它时**拒**，不是跳过。
    """
    bundle, context, plan, scene = world
    store = InMemoryRunStore()

    admitted = admit_run(
        store,
        scene=scene,
        context=context,
        plan=plan,
        budget=_envelope(),
        cutoff=CUTOFF,
        idempotency_key=KEY,
        at=T0,
        execution_mode=ExecutionMode.BOUNDED_AUTO,
        window=_window(),
        ledger=InMemoryBudgetLedger(),
    )

    assert admitted.spec.execution_mode is ExecutionMode.BOUNDED_AUTO
    assert admitted.spec.window == _window()
    assert admitted.stop_reason is None
    assert store.get(admitted.run_id) is not None


def test_a_window_without_a_ledger_is_refused_not_silently_skipped(world) -> None:
    """窗口在场而账本缺席 ⇒ **拒**，不是「这一格跳过」。

    这是本仓那条纪律在受理面上的落点（``domain/run_delta.py::UnaccountedUsage``
    也引了它：「算不出来就抛，不返回 0」）。少了这一条，`ledger=None` 与「账本说
    这条链还没花过钱」在受理面上同形 —— 而前者要去接账本、后者可以继续走。

    这一条**故意只判拒绝本身**，不判拒词的措辞：措辞由
    ``tests/test_run_window_admission.py`` 那条逐字比对钉住。
    """
    bundle, context, plan, scene = world
    store = InMemoryRunStore()

    with pytest.raises(RunSpecError) as caught:
        admit_run(
            store,
            scene=scene,
            context=context,
            plan=plan,
            budget=_envelope(),
            cutoff=CUTOFF,
            idempotency_key=KEY,
            at=T0,
            execution_mode=ExecutionMode.BOUNDED_AUTO,
            window=_window(),
            ledger=None,
        )

    assert "账本" in str(caught.value)
    assert store.get(run_id(WORKSPACE, KEY)) is None, "被拒时一个字节都不该落盘"


# --- 4 · watch 的到期核验：读盘的那一条判据住在这里 ---------------------------


def _scene_store(state: SceneState = SceneState.ACTIVE) -> InMemorySceneStore:
    """盘上那一版 Scene —— ``_require_due`` 要先读得到工作区，才谈得上观测量。

    与 ``tests/test_mcp_runs.py`` 那份夹具同形但**不复用**：那是工具面的夹具
    （它摆的是 SQLite 上那一行），这一份摆的是这一层真正用到的那两个接口。
    跨文件复用它的后果是某个文件为了另一处的需要改一个共享的 helper。
    """
    store = InMemorySceneStore()
    store.put_record(
        SceneRecord(
            scene_id=SCENE,
            workspace=WORKSPACE,
            state=state,
            latest_version=1,
            effective_version=None if state is SceneState.DRAFT else 1,
            created_at=T0,
            updated_at=T0,
            pause_reason=(
                PauseReason.OPERATOR if state is SceneState.PAUSED else None
            ),
        ),
        expected_revision=None,
    )
    store.put_version(_scene())
    return store


def _admit_watch(
    store: Any,
    world,
    *,
    scene_store: Any = None,
    maintenance: Any = None,
    subjects: tuple[tuple[str, str], ...] = (("account", "example-one"),),
):
    """按 ``WATCH`` 受理一次 —— 与 ``world`` 那一份逐格相同，只换模式与那三格。

    ``subjects`` 有缺省值而不是空：这一节的判据几乎都要有一个**具体**主体，
    而一个空的主体集合会让 ``what_is_due`` 的 known 集合空着（它的 docstring
    逐字：不知道世界上有谁），于是每一条判据都会因为「没有主体」而拒 ——
    那会让「有观测所以不到期」那条在一个恒拒的实现上也绿。
    """
    _bundle, context, plan, scene = world
    return admit_run(
        store,
        scene=scene,
        context=context,
        plan=plan,
        budget=_envelope(),
        cutoff=CUTOFF,
        idempotency_key=KEY,
        at=T0,
        execution_mode=ExecutionMode.WATCH,
        scene_store=scene_store,
        maintenance=maintenance,
        subjects=subjects,
    )


def test_a_subject_nobody_has_looked_at_yet_is_due(world) -> None:
    """只给主体、一格都没看过 ⇒ 受理得下来，盘上一行``WATCH``。

    这是这一节的**正控**，而它判的那件事很具体：``subjects`` 真的被读进去了。
    缺了它，下面那条「有观测就不受理」在一个把 ``subjects`` 丢掉、或者干脆恒拒
    的实现上也是绿的 —— 两种都让 known 集合空着，于是每一批主体都「一条都没有」。
    """
    store = InMemoryRunStore()

    run = _admit_watch(
        store,
        world,
        scene_store=_scene_store(),
        maintenance=InMemoryMaintenanceStore(),
    )

    assert run.spec.execution_mode is ExecutionMode.WATCH
    assert store.get(run_id(WORKSPACE, KEY)) is not None


def test_a_face_that_was_just_looked_at_is_not_due(world) -> None:
    """七个面刚刚都看过 ⇒ 拒，盘上零行，而拒词报出**判了几个主体**。

    「刚刚」在这个时刻是 ``T0`` 本身，而 ``RefreshPlan.uncalibrated_default()``
    最短的一条节奏是 30 天 —— 所以这一批主体上确实一条到期的都没有，拒的是那个
    正面事实，不是「名单读空了」。这正是报主体数的用处：两句话的处置不同（一个
    是等它到期，一个是去查那份名单），而它们在只有一句「没有到期」时同形。
    """
    store = InMemoryRunStore()
    maintenance = InMemoryMaintenanceStore()
    for aspect in ALL_ASPECTS:
        maintenance.put_observation(
            Observation(
                workspace=WORKSPACE,
                subject_kind="account",
                subject_id="example-one",
                aspect=aspect,
                observed_at=T0,
                source_ref=KEY,
            )
        )

    with pytest.raises(RunSpecError) as caught:
        _admit_watch(
            store, world, scene_store=_scene_store(), maintenance=maintenance
        )

    text = str(caught.value)
    assert "到期或信号" in text, "拒词没有说清它的来源是什么"
    assert "1 个主体" in text, "拒词没有报出判了几个主体"
    assert store.get(run_id(WORKSPACE, KEY)) is None, "被拒时一个字节都不该落盘"


def test_a_watch_that_cannot_be_judged_is_refused_not_admitted(world) -> None:
    """两个存储都没递进来 ⇒ **拒** —— 判不了不能当成「没有到期」放过。

    这一条是 ``_require_due`` 那三个分支里**唯一**一个在服务面上走不到的：服务面
    受理 watch 时两样都在场（``submit_run`` 里那两格），所以只有库调用点试得到。
    而那正是最要紧的一支 —— 「判不了 ⇒ 拒」与「判不了 ⇒ 当成没有到期 ⇒ 受理」
    在盘上的差别是一行**没有任何依据**的 watch Run，而它在记录上与一次真的因到期
    而起的维护一模一样。

    与 ``spent_of`` 的「算不出来就抛，不返回 0」是同一条纪律，只是这里落成一次
    受理拒绝而不是一次账目事故（前者改这一次请求，后者去查账）。
    """
    store = InMemoryRunStore()

    with pytest.raises(RunSpecError) as caught:
        _admit_watch(store, world)

    text = str(caught.value)
    assert "判不了" in text, "拒词没有说清这一次根本判不出来"
    assert "不能当成" in text, "拒词没有堵住「没有到期」那个读法"
    assert store.get(run_id(WORKSPACE, KEY)) is None, "判不了时一个字节都不该落盘"


def test_a_paused_scene_refuses_a_watch_run_in_the_other_layers_own_words(
    world,
) -> None:
    """暂停的场景 ⇒ 拒，而拒词是 ``due_work`` 的原文，只换了**类型**。

    ``ConfigError`` 说的是「部署/配置写错了」，而「这个场景今天暂停」是一个正常
    状态、是一次**正常的拒绝** —— 所以这一层把它换成 ``RunSpecError``。换的只是
    类型：措辞原样交出去（逐字比「不接受新的派发」与那个状态名），否则调用方读到
    一个「配置错误」的形状，处置被读成「去改部署」。

    工具面上有一条同名的判据（``tests/test_mcp_runs.py`` 第 11 节）：那一条判的是
    **服务面把这两样递进来了**（它多绕了一层接线），这一条判的是这一层的翻译。
    两条都在，因为它们红的原因不同。
    """
    store = InMemoryRunStore()

    with pytest.raises(RunSpecError) as caught:
        _admit_watch(
            store,
            world,
            scene_store=_scene_store(SceneState.PAUSED),
            maintenance=InMemoryMaintenanceStore(),
        )

    text = str(caught.value)
    assert "paused" in text, "拒词没有点出这个场景当前是什么状态"
    assert "不接受新的派发" in text, "拒词不再是 due_work 那一句原文"
    assert store.get(run_id(WORKSPACE, KEY)) is None, "被拒时一个字节都不该落盘"


# --- 5 · 累计预算的读法：从链上的预留 id 去**账本**读回来 -----------------------


def _chain_scope() -> str:
    """这条链的预算作用域。

    预算的 scope **既不是工作区也不是 Run**：§8 那一格是研究链按
    ``<profile>:<account_id>``、发现链按 ``<profile>:<发现范围名>``、维护链按
    ``<profile>:<主体 id>@<时刻>``。**这一族的判据要的只是一个作用域**，它怎么取
    是受理面上游那一格的事（那一格今天还没有生产者），所以这里直接写死一个形状
    对的值，不假装它已经算出来了。
    """
    return f"{DEFAULT_PROFILE}:{SCENE}"


def _settled(
    ledger: InMemoryBudgetLedger, *, units: tuple[int, ...], tag: str
) -> tuple[str, ...]:
    """预留并**结算**若干笔，返回它们的 id —— 这些 id 就是要写进 ``RunDelta.usage``
    的那几个。``units`` 故意取可变长，好让「件数」与「额度」在判据里不相等。"""
    scope = _chain_scope()
    ids = []
    for index, one in enumerate(units):
        held = ledger.reserve(scope, BudgetLine.SEARCH, one, idempotency_key=f"{tag}-{index}")
        ids.append(ledger.spend(held.reservation_id).reservation_id)
    return tuple(ids)


def _stop_with_usage(store, run, *, usage: tuple[str, ...]):
    return store.stop(
        run.run_id,
        delta=RunDelta(
            effects=(PoolEffect(cell=DeltaCell.DISCOVERED, key="v-1", label="场地 v-1"),),
            usage=usage,
        ),
        status=RunStatus.COMPLETED,
        at=T0 + timedelta(hours=1),
    )


def _auto_admission(store, world, *, key: str, ledger):
    """这一族共用的那一次受理（带窗口的 ``bounded_auto``）。"""
    bundle, context, plan, scene = world
    return admit_run(
        store,
        scene=scene,
        context=context,
        plan=plan,
        budget=_envelope(),
        cutoff=CUTOFF,
        idempotency_key=key,
        at=T0,
        execution_mode=ExecutionMode.BOUNDED_AUTO,
        window=_window(),
        ledger=ledger,
    )


def test_the_accumulated_budget_is_read_as_units_not_as_a_reservation_count(world) -> None:
    """累计预算是**从账本读回来的额度**，不是链上预留的**件数**。

    这一条判据的全部力量在 ``units=(7, 7, 6)`` 这个取法上：三笔、合起来 20，而窗口
    的上限正是 20。一个把 ``ChainProgress.reservations`` 的长度当累计用的实现，读到
    的是 3 —— 它不会拒、不会报错，只会让每一条链多开好几轮，而报告上那读起来像
    「这条链的额度还挺宽裕」。

    读数走上来的路是：这条链上**已经结束**的轮 → 它们的 ``delta.usage``（预留 id）
    → 逐笔 ``BudgetLedger.get`` → 求和。**金额只有一个权威**（``ports/budget.py``
    那一处 ``SUM``），所以 Run 记录里不存金额，受理面也不自己乘加。
    """
    bundle, context, plan, scene = world
    store, ledger = InMemoryRunStore(), InMemoryBudgetLedger()
    ledger.set_ceiling(_chain_scope(), BudgetLine.SEARCH, 20, source="declared:profile")
    ids = _settled(ledger, units=(7, 7, 6), tag="q")

    first = _auto_admission(store, world, key=KEY, ledger=ledger)
    _stop_with_usage(store, first, usage=ids)

    with pytest.raises(RunSpecError) as caught:
        _auto_admission(store, world, key=KEY2, ledger=ledger)

    assert "累计预算走到了窗口上限" in str(caught.value)
    assert "已 20、窗口 20" in str(caught.value), "两边的读数都要在拒词里"


def test_a_refunded_reservation_does_not_count_against_the_window(world) -> None:
    """退回的那一笔**不算**已经花掉 —— 而这一条是**边界**上的判据。

    ``BudgetUsage.held`` 逐字是 ``spent + reserved``，**不含** ``refunded``：退款的
    语义是「这笔额度要回来了」。所以 15 已结算 + 5 已退回、窗口 20 ⇒ **受理**。
    算上退款那一笔就成了 20 ⇒ 拒，而形态是这条链此后**永远开不出下一轮**，
    报上读起来像「额度用完了」—— 恰恰相反。

    §8 那句「只有这条路径能把额度要回来」在这里的落点：退款的处置与结算同权，
    所以它必须在这一格上同权。

    ## 那笔退款的 id **必须也写进** ``delta.usage``

    第一版只把已结算那笔写进链上，退款那笔留在账本里没上链 —— 于是这条判据在
    「退款算不算」上**什么都没判**（``spent_of`` 只看得见链上记着的那几笔）。
    这是变异控制抓出来的：把「跳过 REFUNDED」那一句删掉，这一条照样绿。所以两个
    id 都上链，而窗口上限正好卡在两者的和上 —— 15 + 5 = 20 —— 于是「算不算退款」
    这一问在结果上**当场分得开**。
    """
    bundle, context, plan, scene = world
    store, ledger = InMemoryRunStore(), InMemoryBudgetLedger()
    ledger.set_ceiling(_chain_scope(), BudgetLine.SEARCH, 20, source="declared:profile")
    ids = _settled(ledger, units=(15,), tag="q")
    back = ledger.reserve(_chain_scope(), BudgetLine.SEARCH, 5, idempotency_key="q-back")
    ledger.refund(back.reservation_id, reason="provider 未计费")

    first = _auto_admission(store, world, key=KEY, ledger=ledger)
    _stop_with_usage(store, first, usage=(*ids, back.reservation_id))

    admitted = _auto_admission(store, world, key=KEY2, ledger=ledger)

    assert admitted.spec.window == _window()
    assert store.get(admitted.run_id) is not None


def test_a_still_open_reservation_counts_against_the_window(world) -> None:
    """**停在 ``RESERVED``** 的那一笔算「已经承诺出去」，照样占着这一格。

    理由是 §8 明令**不自动退款**：那笔钱在有人显式退款之前都还在外面，把它读成
    「还没花」等于受理面自己替 provider 做了「这次不计费」的假设 —— 而那个假设错了
    只在账单上可见。这一条与上面那条成对：结算 / 在途都算，只有退款不算。
    """
    bundle, context, plan, scene = world
    store, ledger = InMemoryRunStore(), InMemoryBudgetLedger()
    ledger.set_ceiling(_chain_scope(), BudgetLine.SEARCH, 20, source="declared:profile")
    held = ledger.reserve(_chain_scope(), BudgetLine.SEARCH, 20, idempotency_key="q-open")

    first = _auto_admission(store, world, key=KEY, ledger=ledger)
    _stop_with_usage(store, first, usage=(held.reservation_id,))

    with pytest.raises(RunSpecError) as caught:
        _auto_admission(store, world, key=KEY2, ledger=ledger)

    assert "已 20、窗口 20" in str(caught.value)


def test_a_reservation_the_ledger_does_not_know_is_an_incident_not_a_refusal(world) -> None:
    """链上记着一笔账本**答不上来**的预留 ⇒ 抛 ``RunReconciliationError``。

    这条路径不是受理拒绝，形状也不同（一处是「这一轮不给开」，一处是「两边对不上」）。
    把它读成 0 的形态是：一次**记录与账本已经分家**的事故被读成「这条链还没花过钱」，
    于是受理面拿一个更小的数去判上限 —— 而它永远判不出「到了」。
    """
    bundle, context, plan, scene = world
    store, ledger = InMemoryRunStore(), InMemoryBudgetLedger()
    ledger.set_ceiling(_chain_scope(), BudgetLine.SEARCH, 20, source="declared:profile")

    first = _auto_admission(store, world, key=KEY, ledger=ledger)
    _stop_with_usage(store, first, usage=("rsv-nonexistent",))

    with pytest.raises(RunReconciliationError):
        _auto_admission(store, world, key=KEY2, ledger=ledger)


def test_a_cutoff_in_the_past_is_refused_through_the_use_case(world) -> None:
    """过期截止也走同一条路（受理面这一层没有再判一次）。"""
    bundle, context, plan, scene = world
    store = InMemoryRunStore()

    with pytest.raises(RunSpecError):
        admit_run(
            store,
            scene=scene,
            context=context,
            plan=plan,
            budget=_envelope(),
            cutoff=T0 - timedelta(days=1),
            idempotency_key=KEY,
            at=T0,
            execution_mode=ExecutionMode.MANUAL,
        )


def test_an_empty_envelope_cannot_be_admitted(world) -> None:
    """信封为空**受理不出来** —— 「没有上限」不是「随便用」。

    它走 ``RunSpec`` 的校验器（``ValueError``），而不是受理拒绝：处置是把这一次
    的信封写出来，不是等一个人回答。
    """
    bundle, context, plan, scene = world
    store = InMemoryRunStore()

    with pytest.raises(ValueError, match="budget 不得为空"):
        admit_run(
            store,
            scene=scene,
            context=context,
            plan=plan,
            budget=(),
            cutoff=CUTOFF,
            idempotency_key=KEY,
            at=T0,
            execution_mode=ExecutionMode.MANUAL,
        )


# --- 6 · 签名与 import 面上的三条结构判据 -------------------------------------


def test_the_caller_cannot_dictate_the_derived_cells() -> None:
    """调用方**给不了**那几格：``objective`` / ``workspace`` / ``selector`` /
    三个摘要 / ``spec_sha256``。

    这是本文件里最承重的一条。把 ``workspace`` 变成一个关键字参数，读起来只是
    「多了一个可选入参」；而它的含义是**由被授权的对象自己申报工作区**。同一句
    话写进 docstring 没有执行器，写进签名才有 —— 所以这里判签名。
    """
    forbidden = {
        "objective",
        "workspace",
        "selector",
        "context_ref",
        "plan_ref",
        "policy_versions",
        "spec_sha256",
        "site",
        "tenant",
    }
    params = set(inspect.signature(admit_run).parameters)
    assert not (params & forbidden), (
        f"受理用例接受了这些本应由盘上或摘要生产者给出的参数：{sorted(params & forbidden)}"
    )


def test_the_module_does_not_read_a_clock_of_its_own() -> None:
    """受理时刻从外面进来 —— 这一层不许读时钟。

    一个读自己时钟的实现，在测试里只能靠等待来触发「截止已过」，而在生产上
    它的**判定时刻与落盘时刻**会是两个值 —— 中间那段窗口里的差异没有任何一处
    看得见。
    """
    called = _name_calls(MODULE) | _attribute_calls(MODULE)
    forbidden = {"now", "utc_now", "today", "time"}
    assert not (called & forbidden), (
        f"run_admission 自己读了时钟：{sorted(called & forbidden)}。"
        "受理时刻由调用方给（``at``）—— 理由见模块 docstring。"
    )
    assert "utc_now" not in _imported(MODULE)


def test_the_module_cannot_reach_the_adapters_or_dispatch() -> None:
    """受理这一层 import 不到 ``adapters/``，签名里也没有执行器。

    「受理不是执行」在代码里的落点是：它**没有可以派发的东西**。这条有正控
    （``interfaces/composition.py`` 确实进口了适配层）—— 一个只会报绿的扫描器
    与一条真的判据在报告上长得一样。
    """
    assert any("adapters" in name for name in _imported(CONTROL)), (
        f"正控 {CONTROL} 没有进口适配层 —— 扫描器坏了，这条判据已经失去意义"
    )

    leaked = sorted(name for name in _imported(MODULE) if ".adapters" in name)
    assert leaked == [], f"受理这一层进口了适配层：{leaked}"

    params = set(inspect.signature(admit_run).parameters)
    assert not (params & {"dispatcher", "search", "acquisition", "router"}), params


def test_the_module_is_listed_in_the_application_table() -> None:
    """``application/__init__.py`` 那张穷举表里有它一行。

    这里重判一次不是为了替代 ``tests/test_application_module_table.py``（那条遍历
    整个目录），而是因为**这一族的夹具**会 import 它 —— 一个只在表里缺席的模块，
    在本地跑起来毫无异样。
    """
    assert run_admission.__name__ == "marketops.application.run_admission"
    source = (REPO_ROOT / "marketops" / "application" / "__init__.py").read_text(encoding="utf-8")
    assert "| ``run_admission`` |" in source


# --- AST 那一族共用的量具（与 ``tests/test_plan_preview.py`` 同形） -------------


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
