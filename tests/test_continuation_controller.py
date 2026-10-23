"""§10 的控制器：**一轮 tick 做了什么、为什么没做、以及那六个数从哪来**。

## 这一层要挡的六件事，各自有一条判据

1. **同一件被两个驱动各推一遍** —— §8B 逐字禁止的 duplicate expensive work。挡住
   它的不是「控制器只启动一个」，而是**存储层那次 CAS**：``test_a_row_another_driver_holds_is_not_claimed``
   与两条 ``_reclaim`` 的判据守着它。两个驱动各自的报告都写「这件不是我做的」时，
   合起来读就是「没人做过」。
2. **上一代留下的在途件永远挂着** —— 一条 ``CLAIMED`` 的行在崩溃之后没人收，
   分类把它读成「正在研究」，而没有任何东西在动（一盏恒绿的灯）。
3. **一轮的病把整轮停掉** —— 一件抛异常、一件被顶掉，其余照做，且各进自己那一段。
4. **暂停的场景被推着走** —— 闸门必须先于任何执行被读，且**理由要说得出来**。
5. **上限截断之后报告说「做完了」** —— 那三个数（``done``/``due_now``/``truncation``）
   分开存在就是为了这一条。
6. **状态页上的判词与盘不一致** —— 六个数是**从这一轮之后的盘上数出来的**，
   不是从这一轮的账上推的：``test_the_state_is_read_from_the_disk_after_the_round``。

## 为什么这里也要跑两个实现

控制器是应用层，它只碰端口。两个 ``JobStore`` 实现的差别（``BEGIN IMMEDIATE`` 与
一把不存在的锁）不在这一层，但**它们给出的行**在不在这一层：一条被收回的行落在
``PENDING`` 还是别处、``due_at`` 排到哪一刻，是存储层的规则。跑两个实现，红了的时候
一眼看得见是谁的错。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from marketops.adapters.memory import (
    InMemoryJobStore,
    InMemorySceneStore,
)
from marketops.adapters.sqlite.jobs import SqliteJobStore
from marketops.adapters.sqlite.scenes import SqliteSceneStore
from marketops.application.continuation import (
    ContinuationController,
    ContinuationExecutor,
    ContinuationOutcome,
    survey,
)
from marketops.application.scenes import activate, create_draft, pause
from marketops.domain.continuation import (
    OPEN_STATES,
    BlockedOn,
    Continuation,
    ContinuationKind,
    ContinuationState,
    ControllerState,
    WakeupOwner,
    continuation_id,
    retry_delay,
)
from marketops.domain.enums import JobInputKind, JobState, StopReason
from marketops.domain.errors import ConfigError
from marketops.domain.findings import Finding, Findings
from marketops.domain.job import ResearchJob, WorkItem
from marketops.domain.profile import DeclaredProfile, ProfileBundle
from marketops.domain.scene import PauseReason
from marketops.domain.subject import SUBJECT_KIND_ACCOUNT
from marketops.domain.util import sha256_text, utc_now
from marketops.ports.jobs import JobStore
from marketops.ports.scenes import SceneStore
from tests.fakes import synthetic_dossiers, synthetic_scene_files
from tests.test_scene_store_contract import WORKSPACE, make_version

UTC = timezone.utc
SCENE = "scene-a"
OTHER = "scene-b"
KEY = "discovery-v1-arm-b"
SUBJECTS = ("acct-0001", "acct-0002")
DOSSIERS = synthetic_dossiers(*SUBJECTS)
SCENE_FILES = synthetic_scene_files()
HOLDER = "controller-a"

FOUND = Findings(
    observations=(Finding(statement="这一轮确认了天然草", evidence_refs=(sha256_text("p")[:32],)),),
)

#: 一个**早于一切**的时刻：模块导入时取一次，所以任何一轮的归属时刻都不晚于测试里
#: 写下的任何一行。用它构造的 owner **不会**收回任何东西 —— 收回那一条由
#: ``_controller(acquired_at=...)`` 单独造。
_EPOCH_START = utc_now()


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


@pytest.fixture(params=["sqlite", "memory"])
def env(request: pytest.FixtureRequest, tmp_path) -> tuple[JobStore, SceneStore]:
    """一个作业存储 + 一个场景存储，**同一个实现**（sqlite 那两个共用一份库）。"""
    if request.param == "sqlite":
        db = tmp_path / "marketops.db"
        return SqliteJobStore(db), SqliteSceneStore(db)
    return InMemoryJobStore(), InMemorySceneStore()


def _live(scenes: SceneStore, *, scene_id: str = SCENE) -> None:
    create_draft(
        store=scenes, version=make_version(scene_id=scene_id), bundle=_bundle()
    )
    activate(
        store=scenes, scene_id=scene_id, version=1, at=_EPOCH_START, by="operator-1"
    )


def _admitted(jobs: JobStore, *, scene_id: str = SCENE, tag: str = "r1") -> ResearchJob:
    """受理一个作业。``tag`` 同时进幂等键 —— 想要**第二个**作业时必须换 tag，
    换别的都只是得到同一件（幂等键才是那个身份）。"""
    return jobs.admit(
        scene_id=scene_id,
        input_kind=JobInputKind.DISCOVERY_SCOPE,
        input_ref="devon-2026-09",
        idempotency_key=f"{KEY}-{tag}",
        request_sha256=sha256_text(tag),
    )


def _settled(jobs: JobStore, *, scene_id: str = SCENE) -> WorkItem:
    """一件真的跑完的活 —— 它的结算欠下一次维护复核。"""
    job = _admitted(jobs, scene_id=scene_id)
    items = jobs.compile_work_items(job.job_id, DOSSIERS, scene=SCENE_FILES)
    jobs.map_execution(items[0].work_item_id, dispatch_task_id="TASK-001")
    attempt = jobs.lease(items[0].work_item_id, holder="worker-a")
    jobs.settle(attempt.attempt_id, stop_reason=StopReason.GOAL_MET, findings=FOUND)
    return items[0]


def _dispatch_id(jobs: JobStore, job: ResearchJob) -> str:
    return continuation_id(job.scene_id, ContinuationKind.DISPATCH_BATCH, job.job_id)


class FakeExecutor(ContinuationExecutor):
    """记账的执行器：每一次调用都记下来，结果由一个队列给出。

    ``results`` 里放 ``ContinuationOutcome`` 就返回它，放**异常实例**就抛它 ——
    后者是「执行器会去碰网络、碰模型」那一半的替身。
    """

    def __init__(self, results=(), *, on_call=None) -> None:
        self.calls: list[dict] = []
        self._results = list(results)
        self._on_call = on_call

    def dispatch_batch(self, *, scene_id, job_id, checkpoint, now):
        return self._record(
            dict(
                verb="dispatch_batch",
                scene_id=scene_id,
                job_id=job_id,
                checkpoint=checkpoint,
                now=now,
            )
        )

    def maintenance_tick(self, *, scene_id, subject_kind, subject_ref, checkpoint, now):
        return self._record(
            dict(
                verb="maintenance_tick",
                scene_id=scene_id,
                subject_kind=subject_kind,
                subject_ref=subject_ref,
                checkpoint=checkpoint,
                now=now,
            )
        )

    def _record(self, call: dict) -> ContinuationOutcome:
        self.calls.append(call)
        if self._on_call is not None:
            self._on_call(call)
        if self._results:
            result = self._results.pop(0)
            if isinstance(result, BaseException):
                raise result
            return result
        return ContinuationOutcome.done()

    @property
    def verbs(self) -> list[str]:
        return [call["verb"] for call in self.calls]


def _controller(
    jobs: JobStore,
    scenes: SceneStore,
    executor: ContinuationExecutor,
    *,
    holder: str = HOLDER,
    epoch: int = 1,
    acquired_at: datetime | None = None,
) -> ContinuationController:
    when = _EPOCH_START if acquired_at is None else acquired_at
    owner = WakeupOwner(
        workspace=WORKSPACE,
        holder=holder,
        epoch=epoch,
        acquired_at=when,
        heartbeat_at=when,
    )
    return ContinuationController(
        jobs=jobs, scenes=scenes, executor=executor, owner=owner
    )


class Interleaved:
    """一个在**读与领之间**插一次写的存储代理 —— 那就是 ``not_claimed`` 的来源。

    单线程测不出两个进程的竞争，而 ``not_claimed`` 说的正是竞争的那一格：我以为我
    读到一件可以领的，而领的时候它已经不是了。所以这里把那个并发的写者插在
    ``continuations`` 与 ``claim_continuation`` **之间** —— 那就是它的窗口。

    只在**读可领的那一次**触发（``states`` 里有 ``PENDING``）：``_reclaim`` 的那一次
    读也会经过这里，而它先发生。
    """

    def __init__(self, jobs: JobStore, hook) -> None:
        self._inner = jobs
        self._hook = hook
        self._fired = False

    def __getattr__(self, name: str):
        return getattr(self._inner, name)

    def continuations(self, *, scene_ids, states):
        rows = self._inner.continuations(scene_ids=scene_ids, states=states)
        if not self._fired and ContinuationState.PENDING in states:
            self._fired = True
            self._hook()
        return rows


def _row(jobs: JobStore, cid: str) -> Continuation:
    row = jobs.continuation(cid)
    assert row is not None, f"{cid} 应当还在盘上"
    return row


def _now() -> datetime:
    """这一轮的「现在」。**不能是常量**：受理写下的 ``due_at`` 是那一刻的墙钟，
    而一个早于它的常量会让每一行都「还没到期」—— 那正是这些判据要区分的东西。"""
    return utc_now()


# ======================================================================
# 一、一轮做了什么
# ======================================================================
def test_a_tick_drains_the_due_intent_and_hands_it_the_job_id(env) -> None:
    """``business_key`` 就是执行器要的 ``job_id`` —— 那条意图的两个名字是同一个东西，
    而分派点只有控制器里那一处。"""
    jobs, scenes = env
    _live(scenes)
    job = _admitted(jobs)
    executor = FakeExecutor()

    run = _controller(jobs, scenes, executor).tick(_now())

    assert executor.verbs == ["dispatch_batch"]
    call = executor.calls[0]
    assert (call["scene_id"], call["job_id"]) == (SCENE, job.job_id)
    assert call["checkpoint"] == ""
    assert len(run.done) == 1
    assert run.done[0].state is ContinuationState.DONE
    assert (run.touched, run.ok) == (1, True)
    assert _row(jobs, _dispatch_id(jobs, job)).attempts == 0, "做完不算一次尝试"


def test_a_done_row_does_not_get_done_twice(env) -> None:
    """**这一条是「不重复昂贵工作」最外面的一层。** 时钟不动、再 tick 一次，
    执行器必须一次都不被调到 —— 不是「又做了一遍、结论一样」。"""
    jobs, scenes = env
    _live(scenes)
    _admitted(jobs)
    executor = FakeExecutor()
    controller = _controller(jobs, scenes, executor)

    controller.tick(_now())
    second = controller.tick(_now())

    assert len(executor.calls) == 1
    assert second.done == () and second.touched == 0
    assert second.state is ControllerState.COMPLETE


def test_the_executor_gets_back_the_checkpoint_the_row_carries(env) -> None:
    """``checkpoint`` 是不透明字符串：上一次走到哪儿了。它**只**在存储层与执行器
    之间来回，控制器不去读它说了什么。"""
    jobs, scenes = env
    _live(scenes)
    job = _admitted(jobs)
    cid = _dispatch_id(jobs, job)
    first = FakeExecutor(
        [ContinuationOutcome.blocked(reason="等件", on=BlockedOn.HUMAN, checkpoint="到第 3 件")]
    )
    _controller(jobs, scenes, first).tick(_now())
    assert _row(jobs, cid).checkpoint == "到第 3 件"

    # 退避之后再看一眼：这一刻执行器必须原样拿到那句话。
    later = _row(jobs, cid).due_at + timedelta(seconds=1)
    second = FakeExecutor()
    _controller(jobs, scenes, second).tick(later)

    assert second.calls[0]["checkpoint"] == "到第 3 件"


def test_an_empty_workspace_is_a_successful_round(env) -> None:
    """**空的一轮是成功的。** 没有到期的活不是故障 —— 把它报成失败会让每一次
    正常的空转都有人去看一眼。而 ``NO_WORK`` 与 ``COMPLETE`` 的差别只有历史。"""
    jobs, scenes = env
    _live(scenes)
    executor = FakeExecutor()

    run = _controller(jobs, scenes, executor).tick(_now())

    assert executor.calls == []
    assert (run.state, run.touched, run.ok) == (ControllerState.NO_WORK, 0, True)
    assert run.not_ticking == () and run.truncation is None


def test_the_state_is_read_from_the_disk_after_the_round(env) -> None:
    """判词读的是**这一轮之后**的盘，不是这一轮的账：账上「做完了 0 件、挂了 1 件」
    推不出「还有 1 件在等下一次」。两个来源漂移的形态就是这一条要抓的
    （账上说做完了、盘上还欠着）。"""
    jobs, scenes = env
    _live(scenes)
    _admitted(jobs)
    executor = FakeExecutor([RuntimeError("执行器起不来")])
    controller = _controller(jobs, scenes, executor)

    at = _now()
    run = controller.tick(at)

    assert len(run.retrying) == 1
    assert run.inputs.waiting_due == 1, "已排下一次、还没到期"
    assert run.state is ControllerState.WAITING_DUE
    # 同一份判定，另一条路（只读那一页）走的也是它。
    assert survey(jobs=jobs, scenes=scenes, workspace=WORKSPACE, now=at) == run.status


def test_every_kind_has_a_verb_and_both_verbs_are_reachable(env) -> None:
    """**穷尽性。** ``ContinuationKind`` 的每一个成员都必须真的能被送到执行器，
    而这条判据是**从枚举推出来的** —— 加第三个成员而控制器没跟上，两次集合比较
    都会红（``_execute`` 会抛那句 ``ConfigError``）。"""
    jobs, scenes = env
    _live(scenes)
    _admitted(jobs)
    _settled(jobs)
    executor = FakeExecutor()

    run = _controller(jobs, scenes, executor).tick(_now())

    assert {kind.value for kind in ContinuationKind} == {
        "dispatch_batch",
        "maintenance_tick",
    }, "枚举变了：这条判据要跟着重写，而不是跟着改数字"
    assert set(executor.verbs) == {kind.value for kind in ContinuationKind}
    assert run.touched == 2


def test_the_maintenance_tick_carries_the_subject_the_settlement_named(env) -> None:
    """§B21「仅相关对象等人」的输入侧：一条复核意图只带**它自己**的主体，
    所以它旁边那些主体的意图照常跑。"""
    jobs, scenes = env
    _live(scenes)
    item = _settled(jobs)
    executor = FakeExecutor()

    _controller(jobs, scenes, executor).tick(_now())

    # 受理与结算各欠一件，所以这一轮有两个动词 —— 这里只读后者的那一趟。
    call = next(c for c in executor.calls if c["verb"] == "maintenance_tick")
    assert (call["subject_kind"], call["subject_ref"]) == (SUBJECT_KIND_ACCOUNT, SUBJECTS[0])
    assert item.work_item_id not in call["subject_ref"], (
        "主体是**账号**，不是工作单元 —— 一条只带工作单元的复核会把"
        "「仅相关对象等人」退化成「这一批一起等」"
    )


def test_an_unknown_kind_is_a_refusal_not_a_skip(env) -> None:
    """**这一支在正常情况下走不到**，所以它是本文件里唯一一条直接调私有的判据。

    走到那里说明领域层新增了一个种类而控制器没跟上。静默跳过它，会让那一条意图在
    账上不存在（它已经被领走了、落点却没有），而盘上明明欠着 —— 所以那里抛，
    而这条判据保证那句抛**真的存在**。
    """
    jobs, scenes = env
    _live(scenes)
    row = Continuation.model_construct(
        continuation_id="f" * 32,
        scene_id=SCENE,
        kind="something_new",
        business_key="b" * 32,
        due_at=_EPOCH_START,
        created_at=_EPOCH_START,
        updated_at=_EPOCH_START,
    )
    controller = _controller(jobs, scenes, FakeExecutor())

    with pytest.raises(ConfigError, match="不认识的续跑种类"):
        controller._execute(row, _EPOCH_START)


# ======================================================================
# 二、结账与分段
# ======================================================================
def test_a_failure_goes_to_retrying_until_the_attempts_run_out(env) -> None:
    """``retrying`` 与 ``failed`` 分开，因为处置相反：一个什么都不要做，一个要人看
    一眼。而**执行器报 FAILED，盘上不一定落 FAILED** —— 分段读的是盘上那一行。"""
    jobs, scenes = env
    _live(scenes)
    _admitted(jobs)
    executor = FakeExecutor(
        [RuntimeError("挂了"), RuntimeError("又挂了"), RuntimeError("还是挂了")]
    )
    controller = _controller(jobs, scenes, executor)

    first = controller.tick(_now())
    assert len(first.retrying) == 1 and first.failed == ()
    assert first.retrying[0].attempts == 1
    assert first.ok is True, "挂了一次、已经排上下一次 —— 这不是一件要人看的事"
    assert first.retrying[0].due_at > first.at

    second = controller.tick(first.retrying[0].due_at)
    assert len(second.retrying) == 1 and second.retrying[0].attempts == 2

    third = controller.tick(second.retrying[0].due_at)
    assert third.retrying == ()
    assert len(third.failed) == 1
    assert third.failed[0].state is ContinuationState.FAILED
    assert third.ok is False, "次数用尽要人看一眼"


def test_a_crash_in_the_executor_is_recorded_with_its_type(env) -> None:
    """执行器会去碰网络、碰模型。**一个没想到的异常类型让整轮在中间断掉的代价**，
    是前面那些已经做完的工作在这一轮的账上消失 —— 所以这里接住一切，
    并且带上类型名：``KeyError('x')`` 与 ``ValueError('x')`` 是两件事。"""
    jobs, scenes = env
    _live(scenes)
    _admitted(jobs)
    executor = FakeExecutor([KeyError("executor")])

    run = _controller(jobs, scenes, executor).tick(_now())

    assert "KeyError" in run.retrying[0].reason
    assert "executor" in run.retrying[0].reason


def test_an_exception_in_one_row_does_not_stop_the_rest(env) -> None:
    """一件挂了，其余照做 —— 而这一条只有**两件都在盘上**的时候才有意义。"""
    jobs, scenes = env
    _live(scenes)
    _admitted(jobs)
    _admitted(jobs, tag="r2")
    executor = FakeExecutor([RuntimeError("第一件挂了")])

    run = _controller(jobs, scenes, executor).tick(_now())

    assert len(executor.calls) == 2
    assert len(run.retrying) == 1
    assert len(run.done) == 1


def test_being_blocked_does_not_burn_an_attempt_and_says_which_kind(env) -> None:
    """一次「门关着」不是一次尝试。少了这一条，一个暂停了一周的场景会因为每 60 秒
    tick 一次而被判成重试了一万次 —— 而它会**因此**永远走不到人手里（次数用尽 → 终态）。"""
    jobs, scenes = env
    _live(scenes)
    _admitted(jobs)
    executor = FakeExecutor(
        [ContinuationOutcome.blocked(reason="这个部署没有装执行器", on=BlockedOn.CAPACITY)]
    )
    controller = _controller(jobs, scenes, executor)

    run = controller.tick(_now())

    assert len(run.blocked) == 1
    assert run.blocked[0].attempts == 0
    assert run.blocked[0].blocked_on is BlockedOn.CAPACITY
    assert run.ok is True, "缺一根线要运维去补，不是「这一轮坏了」"


def test_each_kind_of_blocking_lands_in_its_own_count(env) -> None:
    """三种「等人」的理由各对应一种处置，所以它们**不进同一格**。合成一句散文时，
    「要人按一个按钮」与「要运维补接线」在状态页上长得一样。"""
    jobs, scenes = env
    _live(scenes)
    _admitted(jobs)
    executor = FakeExecutor(
        [ContinuationOutcome.blocked(reason="缺执行器", on=BlockedOn.CAPACITY)]
    )
    controller = _controller(jobs, scenes, executor)

    first = controller.tick(_now())
    assert first.inputs.blocked_capacity == 1
    assert first.state is ControllerState.MISSING_CAPACITY

    second = _controller(
        jobs, scenes, FakeExecutor([ContinuationOutcome.blocked(reason="等人裁决", on=BlockedOn.HUMAN)])
    ).tick(first.blocked[0].due_at)
    assert second.inputs.blocked_human == 1
    assert second.state is ControllerState.WAITING_ON_HUMAN


# ======================================================================
# 三、闸门
# ======================================================================
def test_a_paused_scene_is_not_ticked_and_the_run_says_why(env) -> None:
    """暂停是**已经做出的决定**。它必须先于任何执行被读到，且理由要说得出来 ——
    一句话逐字来自 ``SceneRecord.dispatch_refusal``，与研究派发侧问的是同一句。"""
    jobs, scenes = env
    _live(scenes)
    _admitted(jobs)
    pause(store=scenes, scene_id=SCENE, at=_now(), reason=PauseReason.OPERATOR)
    executor = FakeExecutor()

    run = _controller(jobs, scenes, executor).tick(_now())

    assert executor.calls == [], "暂停的场景一件都不许推"
    assert run.done == () and run.touched == 0
    assert len(run.not_ticking) == 1
    scene_id, why = run.not_ticking[0]
    assert scene_id == SCENE
    assert "operator_pause" in why, "理由里要有「谁能让它重新开工」的线索"
    assert run.state is ControllerState.PAUSED


def test_a_paused_scene_still_shows_up_in_the_counts(env) -> None:
    """判词只说最先要处理的那一格，**六个数一并报出** —— 所以「暂停挡着 1 件」这件事
    一个字都没丢。"""
    jobs, scenes = env
    _live(scenes)
    _admitted(jobs)
    pause(store=scenes, scene_id=SCENE, at=_now(), reason=PauseReason.OPERATOR)

    run = _controller(jobs, scenes, FakeExecutor()).tick(_now())

    assert run.inputs.blocked_paused == 1
    assert run.inputs.open_total == 1


def test_a_scene_outside_this_workspace_is_invisible(env) -> None:
    """这一轮要看的场景集合是**这个工作区**的。把别的场景的行也算进来，会让
    ``open_total`` 把不属于这一代的欠账算到自己头上。"""
    jobs, scenes = env
    _live(scenes)
    _admitted(jobs)

    run = _controller(jobs, scenes, FakeExecutor()).tick(_now())
    assert run.done, "本工作区的那一件照做"

    other = survey(jobs=jobs, scenes=scenes, workspace="ws-elsewhere", now=_now())
    assert (other.state, other.inputs.open_total) == (ControllerState.NO_WORK, 0)


# ======================================================================
# 四、认领与收回
# ======================================================================
def test_a_row_taken_between_the_read_and_the_claim_is_not_claimed(env) -> None:
    """**两个驱动各自的报告都写「这件不是我做的」时，合起来读就是「没人做过」。**
    所以它必须出现在 ``not_claimed`` 里，且**不是**一个失败。

    写者是**事件**（一次取消）：``set_state`` 在同一个写事务里把那条派发意图判成
    ``DONE``（受理的反面：已经没什么可做的）。这正是 §10 那句「业务事件受理与
    continuation intent 原子落账」的另一半 —— 事件可以在任何一刻落下。
    """
    jobs, scenes = env
    _live(scenes)
    job = _admitted(jobs)
    executor = FakeExecutor()

    def cancel() -> None:
        jobs.set_state(
            job.job_id, JobState.CANCELLED, expected=JobState.QUEUED, reason="ops"
        )

    run = _controller(Interleaved(jobs, cancel), scenes, executor).tick(_now())

    assert executor.calls == []
    assert len(run.not_claimed) == 1
    assert run.not_claimed[0].state is ContinuationState.DONE, "盘上那一行说的是实话"
    assert run.done == () and run.ok is True, "没拿到不是失败"
    assert run.state is ControllerState.COMPLETE, "那个作业已经结了，而它确实做过"


@pytest.mark.parametrize("cell", ["holder", "epoch", "updated_at"])
def test_a_previous_generation_is_recognised_by_any_of_the_three_cells(env, cell) -> None:
    """``_reclaim`` 的三格各自都要能单独判出「这不是我」。少了任何一格，
    那一行的失败形态都是**静默的**：它永远挂在 ``CLAIMED`` 上，分类读成
    「正在研究」，而没有任何东西在动。

    ``updated_at`` 那一格不是多余的：``WakeupRegistry.release`` 是删行，于是下一代
    ``acquire`` 拿到的 ``epoch`` 又是 1，前两格与上一代**逐字相同**（同一个部署、
    同一个 owner id）。只有时刻分得开它们。
    """
    jobs, scenes = env
    _live(scenes)
    job = _admitted(jobs)
    cid = _dispatch_id(jobs, job)
    due = _row(jobs, cid).due_at
    assert jobs.claim_continuation(cid, holder="controller-a", epoch=1, now=due).won

    holder = "controller-b" if cell == "holder" else HOLDER
    epoch = 2 if cell == "epoch" else 1
    acquired_at = (
        due + timedelta(seconds=1) if cell == "updated_at" else due - timedelta(seconds=1)
    )

    run = _controller(
        jobs, scenes, FakeExecutor(), holder=holder, epoch=epoch, acquired_at=acquired_at
    ).tick(due + timedelta(seconds=2))

    assert len(run.reclaimed) == 1
    assert run.reclaimed[0].continuation_id == cid
    assert run.reclaimed[0].holder == "controller-a", "收回来的是它被放回时的样子"
    assert run.reclaimed[0].epoch == 1


def test_the_current_generation_does_not_reclaim_its_own_work(env) -> None:
    """三格**全对**时那一行是我的在途件：收它等于把一件正在做的事重做一遍。"""
    jobs, scenes = env
    _live(scenes)
    job = _admitted(jobs)
    cid = _dispatch_id(jobs, job)
    due = _row(jobs, cid).due_at
    assert jobs.claim_continuation(cid, holder=HOLDER, epoch=1, now=due).won

    run = _controller(jobs, scenes, FakeExecutor(), acquired_at=due - timedelta(seconds=1)).tick(
        due + timedelta(seconds=1)
    )

    assert run.reclaimed == ()
    assert _row(jobs, cid).state is ContinuationState.CLAIMED
    assert run.state is ControllerState.RESEARCHING


def test_a_reclaimed_row_goes_back_to_the_queue_and_says_who_left_it(env) -> None:
    """收回**不是重跑**，也不猜上一代做到哪儿了：它只是把那一行放回 ``PENDING``，
    并且把「是谁留下的」写进理由 —— 那一句话在事后只能从这里读。"""
    jobs, scenes = env
    _live(scenes)
    job = _admitted(jobs)
    cid = _dispatch_id(jobs, job)
    due = _row(jobs, cid).due_at
    assert jobs.claim_continuation(cid, holder="controller-old", epoch=3, now=due).won

    run = _controller(jobs, scenes, FakeExecutor(), acquired_at=due + timedelta(seconds=1)).tick(
        due + timedelta(seconds=2)
    )

    assert len(run.reclaimed) == 1
    row = _row(jobs, cid)
    assert row.state is ContinuationState.PENDING
    assert "controller-old" in row.reason and "3" in row.reason


def test_the_reclaimed_row_is_not_run_in_the_same_round(env) -> None:
    """收回的行按 ``now + retry_delay`` 排下一次 —— **不在这一轮**被跑掉。

    刻意的：一次崩溃之后把所有在途件立刻重跑一遍，是在用「刚重启」这件事制造一次
    尖峰，而尖峰正好落在最不需要它的时刻（刚重启、缓存全冷）。
    """
    jobs, scenes = env
    _live(scenes)
    job = _admitted(jobs)
    cid = _dispatch_id(jobs, job)
    due = _row(jobs, cid).due_at
    assert jobs.claim_continuation(cid, holder="controller-old", epoch=1, now=due).won
    executor = FakeExecutor()
    at = due + timedelta(seconds=1)

    run = _controller(jobs, scenes, executor, acquired_at=at).tick(at)

    assert len(run.reclaimed) == 1
    assert executor.calls == [], "收回不带重跑"
    assert run.done == ()
    assert _row(jobs, cid).due_at == at + retry_delay(0)
    assert run.inputs.waiting_due == 1
    assert run.state is ControllerState.WAITING_DUE


def test_a_row_taken_while_i_was_working_lands_in_superseded(env) -> None:
    """**这一段的处置与其余六段都不同：立刻停手。** 继续做下去就是两个触发器在推
    同一批资源 —— 而那正是 ``epoch`` 存在的理由。

    场景：另一个驱动在我执行到一半时收回了这一行（它那边的 ``_reclaim`` 把
    ``CLAIMED`` 写回 ``PENDING``）。我结账时 CAS 就会失败。
    """
    jobs, scenes = env
    _live(scenes)
    job = _admitted(jobs)
    cid = _dispatch_id(jobs, job)

    def steal(call: dict) -> None:
        if call["job_id"] != job.job_id:
            return
        jobs.finish_continuation(
            cid,
            state=ContinuationState.PENDING,
            holder=HOLDER,
            epoch=1,
            now=call["now"],
            reason="另一代收回了它",
        )

    run = _controller(jobs, scenes, FakeExecutor([ContinuationOutcome.done()], on_call=steal)).tick(
        _now()
    )

    assert run.done == ()
    assert len(run.superseded) == 1
    row, why = run.superseded[0]
    assert row.continuation_id == cid
    assert why, "被顶掉要带上一句要读的话"
    assert run.ok is False, "两个触发器同时在跑是**要立刻停手**的那一类"


def test_a_superseded_row_does_not_stop_the_rest_of_the_round(env) -> None:
    """一次被顶掉不该让整轮停在那里 —— 已经领到手的其余几件照做。"""
    jobs, scenes = env
    _live(scenes)
    first = _admitted(jobs)
    _admitted(jobs, tag="r2")
    cid = _dispatch_id(jobs, first)

    def steal(call: dict) -> None:
        if call["job_id"] != first.job_id:
            return
        jobs.finish_continuation(
            cid,
            state=ContinuationState.PENDING,
            holder=HOLDER,
            epoch=1,
            now=call["now"],
            reason="另一代收回了它",
        )

    run = _controller(
        jobs, scenes, FakeExecutor([ContinuationOutcome.done(), ContinuationOutcome.done()], on_call=steal)
    ).tick(_now())

    assert len(run.superseded) == 1
    assert len(run.done) == 1


# ======================================================================
# 五、上限
# ======================================================================
def test_the_cap_applies_to_the_whole_round_not_to_each_scene(env) -> None:
    """上限挡的是「**这一轮**花多少」。按场景算的上限会让 K 个场景各做 ``limit`` 件
    —— 那正是它想挡的东西。而截断**必须被记下来**：报告里「没找到」与「找到了但被
    上限砍了」长得一模一样。"""
    jobs, scenes = env
    _live(scenes)
    _live(scenes, scene_id=OTHER)
    _admitted(jobs)
    _admitted(jobs, scene_id=OTHER, tag="r2")
    executor = FakeExecutor()

    run = _controller(jobs, scenes, executor).tick(_now(), limit=1)

    assert len(executor.calls) == 1
    assert len(run.done) == 1
    assert run.truncation is not None
    assert (run.truncation.cap, run.truncation.kept, run.truncation.dropped) == (1, 1, 1)
    assert run.truncation.truncated is True
    assert len(run.truncation.dropped_items) == 1
    assert "/dispatch_batch/" in run.truncation.dropped_items[0]
    # 尾巴上那一件**到期了而没做掉** —— 判词读的是「还有到期的」，不是「做完了」。
    assert run.inputs.due_now == 1
    assert run.state is ControllerState.WAITING_DUE


def test_a_cap_that_fits_reports_no_truncation(env) -> None:
    """恰好装得下时 ``truncated`` 是 ``False``，而 ``cap`` 仍然在 —— 报告要能答
    「这一轮本来允许做几件」。"""
    jobs, scenes = env
    _live(scenes)
    _admitted(jobs)

    run = _controller(jobs, scenes, FakeExecutor()).tick(_now(), limit=10)

    assert run.truncation is not None
    assert (run.truncation.cap, run.truncation.dropped, run.truncation.truncated) == (
        10,
        0,
        False,
    )


def test_without_a_cap_there_is_no_truncation_decision(env) -> None:
    """没设上限就**没有**截断决定，不是一条 ``dropped=0`` 的决定 —— 与
    ``application/maintenance.py::due_work`` 逐字同一条。"""
    jobs, scenes = env
    _live(scenes)
    _admitted(jobs)

    run = _controller(jobs, scenes, FakeExecutor()).tick(_now())

    assert run.truncation is None
    assert run.done


def test_a_cap_of_zero_does_nothing_and_says_it_dropped_everything(env) -> None:
    """``0`` 是一个合法的上限（「这一轮先不花」），与 ``None`` 不同。"""
    jobs, scenes = env
    _live(scenes)
    _admitted(jobs)
    executor = FakeExecutor()

    run = _controller(jobs, scenes, executor).tick(_now(), limit=0)

    assert executor.calls == []
    assert run.truncation is not None and run.truncation.dropped == 1
    assert run.inputs.due_now == 1


# ======================================================================
# 六、只读那一页
# ======================================================================
def test_survey_never_pushes_a_round(env) -> None:
    """模块级的 ``survey`` **没有执行器这个参数** —— 所以「这一页会不会顺手推一轮」
    这个问题在签名上就答完了。"""
    jobs, scenes = env
    _live(scenes)
    _admitted(jobs)

    at = _now()
    before = survey(jobs=jobs, scenes=scenes, workspace=WORKSPACE, now=at)
    after = survey(jobs=jobs, scenes=scenes, workspace=WORKSPACE, now=at)

    assert before == after
    assert before.state is ControllerState.WAITING_DUE
    assert before.inputs.due_now == 1


def test_the_summary_is_the_same_from_both_doors(env) -> None:
    """``ContinuationController.survey`` 与模块级的 ``survey`` 是**同一份判定**
    （一处写）。两处各写一份的漂移形态是「控制器说在做、状态页说没事做」。"""
    jobs, scenes = env
    _live(scenes)
    _admitted(jobs)
    controller = _controller(jobs, scenes, FakeExecutor())

    at = _now()
    assert controller.survey(at) == survey(
        jobs=jobs, scenes=scenes, workspace=WORKSPACE, now=at
    )


def test_a_naive_now_is_refused_by_both_doors(env) -> None:
    """``now`` 必须带时区：到期判定正是拿它减 ``due_at``，naive 时间会让「到期了没有」
    有一个看起来有答案的空答案。"""
    jobs, scenes = env
    _live(scenes)
    naive = datetime(2026, 9, 23, 1, 0)

    with pytest.raises(ValueError, match="时区"):
        survey(jobs=jobs, scenes=scenes, workspace=WORKSPACE, now=naive)
    with pytest.raises(ValueError, match="时区"):
        _controller(jobs, scenes, FakeExecutor()).tick(naive)


def test_the_open_states_are_what_the_controller_reads(env) -> None:
    """控制器读的行是 ``OPEN_STATES`` 那三个 —— 一条从这个集合里漏掉的终态行会让
    每一轮都去领一件已经做完的事。"""
    assert OPEN_STATES == {
        ContinuationState.PENDING,
        ContinuationState.CLAIMED,
        ContinuationState.BLOCKED,
    }
