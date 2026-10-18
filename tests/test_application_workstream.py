"""``application/workstream.py`` 自己的判据：**七步启动、三档控制、两处读数**。

存储那一份在 ``tests/test_workstream_store_contract.py``，域那一份在
``tests/test_domain_workstream.py``。这里问的是这一层**判**了什么、按什么次序做了什么
—— 而那两件事只有在把写记录下来之后才看得见。

## 三条各挡一种错法

1. **重发一行都不写。** 「再交一次什么都不发生」这句话在盘上要是字面成立的：
   一次重发连一次幂等的 ``put_scene`` / ``put_launch`` 都不做。判据是**写次数**，
   不是回执里的 ``replayed=True``（一个先写再判的实现也会回 ``replayed=True``）。
2. **冻结在受理之前。** 每一次启动绑的是**冻结了的那一版**，所以先有那一版再有
   它之下的第一轮；反过来会让一轮 Run 引用一份还没有落盘的编译结果。判据是
   一个**次序表**（``freeze`` 与 ``admit`` 各记一行），而不是两个断言各判一次。
3. **顺序在受理之前读出来**（承重点 5 的落点）。``admit`` 收到的那两个参数里，
   第二个是这一行工作流的业务键 —— 它既落成 ``Workstream.workstream_id``，
   也是第一轮 Run 的 ``RunSpec.workstream``。受理之后再读一次会让"这一轮属于
   哪一次启动"多出一个可能的值。

## 一处 2026-09-29 实测的缺陷，回归钉在这里

``control_workstream`` 原来把 ``pause_reason`` 写成
``reason if action.requires_reason else ""`` —— 一个布尔管三档，而三档里有两档的
``requires_reason`` 都是 ``False``，于是**改名**会顺手把一个暂停中的工作流的
``pause_reason`` 清成空串。两个实现给出两种错：

* **SQLite**：``put_workstream`` 会读回那一行重建对象，而
  ``Workstream._paused_says_why`` 要求"暂停必须写明原因" —— 于是**写这一步就抛**，
  抛的还是 ``pydantic.ValidationError``（不是本仓的错误类型）。"暂停中的工作流
  改个名字"在后端根本做不成，而出去的形状是"服务端崩了"。
* **内存替身**：它把对象原样存着，于是写成功了 —— 改名**静默地**清掉暂停原因。

判据是 ``test_renaming_a_paused_workstream_keeps_its_reason``，它在**两个存储
实现上都跑**：只跑内存那一份的话，SQLite 那条"写就抛"的路一次都不会被执行到，
而两份分岔正是这条缺陷的全部内容。三档现在逐档写出来，RENAME 那一档说的是
"不动它"。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from marketops.adapters.memory import InMemorySceneStore, InMemoryWorkstreamStore
from marketops.application.workstream import (
    NOTHING_IN_THIS_WORKSPACE,
    FirstRun,
    StartResult,
    _dispatch_read,  # noqa: F401  —— 它是这一层的私有读，用例直接判它的两半
    control_workstream,
    first_run_key,
    get_workstream,
    list_workstreams,
    request_digest_of,
    start_workstream,
)
from marketops.domain.enums import BudgetLine
from marketops.domain.errors import ConfigError, EvidenceError, StoreError
from marketops.domain.maintenance import RefreshPlan
from marketops.domain.outreach import OutreachPolicyDecl
from marketops.domain.run import RunBudgetLine, RunSpec, spec_fingerprint
from marketops.domain.scene import (
    PauseReason,
    SceneBudgetLine,
    SceneRecord,
    SceneState,
    SceneVersion,
)
from marketops.domain.workstream import (
    LaunchRecord,
    Workstream,
    WorkstreamAction,
    WorkstreamQueue,
    WorkstreamState,
    WorkstreamStateError,
    launch_id_of,
    launch_subject,
    workstream_id,
)

T0 = datetime(2026, 9, 29, 1, 0, tzinfo=timezone.utc)
T1 = T0 + timedelta(hours=1)
WORKSPACE = "ws-natural-grass"
OTHER_WORKSPACE = "ws-rye-blend"
SCENE = "scene-uk-natural-grass"
OTHER_SCENE = "scene-se-rye-blend"
KEY = "launch-2026-09-29-natural-grass"
NAME = "九月的天然草那一轮"
BY = "alice"


# --- 夹具 ----------------------------------------------------------------------


def _scene(version: int = 1, **overrides: object) -> SceneVersion:
    fields: dict[str, object] = {
        "scene_id": SCENE,
        "workspace": WORKSPACE,
        "version": version,
        "region": ("GB-England-Devon",),
        "scenario": "sports-surface-maintenance",
        "product": "acme-natural-grass",
        "admissions": ("sports-club",),
        "exclusions": ("golf-course", "bowling-green"),
        "role_policy": "role-policy-v1",
        "source_plan": ("mother-pool-import",),
        "budget": (SceneBudgetLine(line=BudgetLine.SEARCH, ceiling=20),),
        "refresh": RefreshPlan.uncalibrated_default(),
        "outreach": OutreachPolicyDecl.uncalibrated_default(),
        "profile_id": WORKSPACE,
        "profile_version": "2026-09-01",
        "created_at": T0,
        "supersedes": version - 1 if version > 1 else None,
    }
    fields.update(overrides)
    return SceneVersion(**fields)  # type: ignore[arg-type]


def _record(**overrides: object) -> SceneRecord:
    fields: dict[str, object] = {
        "scene_id": SCENE,
        "workspace": WORKSPACE,
        "state": SceneState.ACTIVE,
        "latest_version": 1,
        "effective_version": 1,
        "created_at": T0,
        "updated_at": T0,
        "pause_reason": None,
    }
    fields.update(overrides)
    return SceneRecord(**fields)  # type: ignore[arg-type]


def _scenes(record: SceneRecord | None = None, *, version: SceneVersion | None = None):
    """一个盘上状态：**那一版**（由记录推）与**那一条记录**。

    版本从记录上推（``scene_id`` 与 ``latest_version``）—— 让调用点各写一遍会造出
    "记录说的是 A 场景而版本写的是 B 场景"的夹具，而那时用例判的就不是它想判的了。
    """
    store = InMemorySceneStore()
    if record is not None:
        store.put_version(
            version
            if version is not None
            else _scene(version=record.latest_version, scene_id=record.scene_id,
                        workspace=record.workspace)
        )
        store.put_record(record, expected_revision=None)
    return store


class RecordingWorkstreamStore(InMemoryWorkstreamStore):
    """真的那一个，**外加一本写账**。

    「重发一行都不写」这句话只能靠写次数判：一个先写再判的实现也会回
    ``replayed=True``，而两条在只读回执的判据下长得一样。
    """

    def __init__(self) -> None:
        super().__init__()
        self.launch_writes: list[LaunchRecord] = []
        self.stream_writes: list[tuple[Workstream, int | None]] = []

    def put_launch(self, record: LaunchRecord) -> LaunchRecord:
        self.launch_writes.append(record)
        return super().put_launch(record)

    def put_workstream(self, stream: Workstream, *, expected_revision: int | None) -> None:
        self.stream_writes.append((stream, expected_revision))
        super().put_workstream(stream, expected_revision=expected_revision)

    @property
    def writes(self) -> int:
        return len(self.launch_writes) + len(self.stream_writes)


class Trace:
    """``freeze`` 与 ``admit`` 各记一行，**次序**因此是可判的。

    ``admit`` 收到的两个参数原样留下来：第二个（工作流业务键）是承重点 5 的落点。
    """

    def __init__(self, *, queue: WorkstreamQueue = WorkstreamQueue.RUN_ADMITTED, why: str = ""):
        self.events: list[str] = []
        self.admitted: list[tuple[str, str]] = []
        self.queue = queue
        self.why = why

    def freeze(self) -> object:
        self.events.append("freeze")
        return object()

    def admit(self, run_key: str, workstream_id_: str) -> FirstRun:
        assert "freeze" in self.events or True  # 次序由 events 判，不在这里
        self.events.append("admit")
        self.admitted.append((run_key, workstream_id_))
        if self.queue is WorkstreamQueue.RUN_ADMITTED:
            return FirstRun(
                run_id=f"run-{len(self.admitted):04d}",
                queue=self.queue,
                next_wake_at=None,
            )
        return FirstRun(run_id="", queue=self.queue, next_wake_at=T1, why=self.why or "容量排满了")


def _start(store, scenes, trace, **overrides):
    kwargs = {
        "workspace": WORKSPACE,
        "scene_id": SCENE,
        "scene_version": 1,
        "idempotency_key": KEY,
        "name": NAME,
        "at": T0,
        "by": BY,
        "store": store,
        "scenes": scenes,
        "admit": trace.admit,
        "freeze": trace.freeze,
    }
    kwargs.update(overrides)
    return start_workstream(**kwargs)


def _booted(**overrides):
    """一次成功的启动，连夹具一起交出来。"""
    record = overrides.pop("record", _record())
    store = RecordingWorkstreamStore()
    scenes = _scenes(record)
    trace = Trace()
    result = _start(store, scenes, trace, **overrides)
    return store, scenes, trace, result


# --- 1. 新起：绑、受理、落两行 --------------------------------------------------


def test_a_first_start_writes_one_launch_and_one_workstream():
    store, _, trace, result = _booted()

    assert len(store.launch_writes) == 1
    assert len(store.stream_writes) == 1
    assert store.stream_writes[0][1] is None, "首次创建走的是 expected_revision=None"

    assert result.replayed is False
    assert result.launch.launched_by == BY
    assert result.workstream.state is WorkstreamState.RUNNING
    assert result.workstream.revision == 1
    assert result.workstream.ordinal == 1
    assert result.workstream.workstream_id == workstream_id(SCENE, 1)
    assert result.workstream.launch_id == result.launch.launch_id
    assert result.workstream.scene_version == 1
    assert result.run_id == result.launch.run_id


def test_the_run_id_on_the_launch_is_the_one_admission_handed_back():
    store, _, trace, result = _booted()

    run_key, wid = trace.admitted[0]
    assert result.launch.run_id == "run-0001"
    assert result.workstream.run_id == "run-0001"
    assert result.workstream.queue is WorkstreamQueue.RUN_ADMITTED
    assert run_key == first_run_key(
        workspace=WORKSPACE, scene_id=SCENE, scene_version=1, idempotency_key=KEY
    )
    assert wid == workstream_id(SCENE, 1)


def test_the_first_run_key_is_derived_from_the_launch_key_not_given_twice():
    """让调用方**另给**一个 Run 键，等于允许"同一个启动业务键配两个不同的 Run 键"：
    重发同一份启动会开出第二轮 Run，而两条记录各自自洽。"""
    left = first_run_key(
        workspace=WORKSPACE, scene_id=SCENE, scene_version=1, idempotency_key=KEY
    )
    right = first_run_key(
        workspace=WORKSPACE, scene_id=SCENE, scene_version=1, idempotency_key=KEY
    )
    assert left == right
    assert launch_subject(SCENE, 1, KEY) in left
    for changed in (
        {"scene_version": 2},
        {"idempotency_key": "another"},
        {"scene_id": OTHER_SCENE},
    ):
        assert (
            first_run_key(
                workspace=WORKSPACE,
                scene_id=changed.get("scene_id", SCENE),
                scene_version=changed.get("scene_version", 1),
                idempotency_key=changed.get("idempotency_key", KEY),
            )
            != left
        )


def test_each_start_of_the_same_scene_takes_the_next_ordinal():
    record = _record(latest_version=2, effective_version=2)
    store = InMemoryWorkstreamStore()
    scenes = _scenes(record)
    trace = Trace()

    first = _start(store, scenes, trace, scene_version=2)
    second = _start(
        store, scenes, trace, scene_version=2, idempotency_key="launch-second"
    )

    assert first.workstream.ordinal == 1
    assert second.workstream.ordinal == 2
    assert second.workstream.workstream_id == workstream_id(SCENE, 2)
    assert trace.admitted[1][1] == workstream_id(SCENE, 2), (
        "第一轮 Run 拿到的是**这一行**的业务键 —— 承重点 5 的落点"
    )


def test_two_scenes_count_their_own_ordinals():
    """``next_ordinal`` 按 ``scene_id`` 收口：另一个场景的第一个工作流也是 ``#1``。

    两个场景各自有一条记录、各自有一版声明 —— 共用一条记录会让"它读的是哪一条"
    在用例里看不出来。
    """
    store = InMemoryWorkstreamStore()
    scenes = _scenes(_record())
    scenes.put_version(_scene(scene_id=OTHER_SCENE, workspace=WORKSPACE))
    scenes.put_record(_record(scene_id=OTHER_SCENE), expected_revision=None)
    trace = Trace()

    first = _start(store, scenes, trace)
    other = _start(
        store, scenes, trace, scene_id=OTHER_SCENE, idempotency_key="launch-other"
    )

    assert first.workstream.ordinal == 1
    assert other.workstream.ordinal == 1
    assert other.workstream.workstream_id == workstream_id(OTHER_SCENE, 1)


def test_the_workstream_carries_the_name_the_caller_gave():
    _, _, _, result = _booted()
    assert result.workstream.name == NAME


def test_a_first_round_that_was_not_admitted_is_said_out_loud():
    """``queue=IDLE`` ⇒ 没有 ``run_id`` 而有一句 ``why``。三条是同一件事的三个说法。"""
    record = _record()
    store = RecordingWorkstreamStore()
    scenes = _scenes(record)
    trace = Trace(queue=WorkstreamQueue.IDLE)

    result = _start(store, scenes, trace)

    assert result.launch.run_id == ""
    assert result.workstream.queue is WorkstreamQueue.IDLE
    assert result.workstream.next_wake_at == T1
    assert result.why.strip(), "没有被受理就必须有一句原因"
    assert result.run_id == ""


def test_the_run_spec_takes_the_workstream_business_key():
    """``RunSpec.workspace`` 那个重载（承重点 5）在这一层的落点：第一轮 Run 能说出
    "我在哪一次启动之下"，而那个键在这一层之外没有生产者。"""
    _, _, trace, result = _booted(record=_record())
    _, wid = trace.admitted[0]
    spec = RunSpec(
        workspace=WORKSPACE,
        objective="补一轮 Devon 的场地",
        selector=SCENE,
        context_ref="a" * 64,
        plan_ref="b" * 64,
        execution_mode="manual",
        window=None,
        budget=(RunBudgetLine(line=BudgetLine.SEARCH, ceiling=20),),
        cutoff=T1,
        policy_versions=("grading@1f2e3d",),
        idempotency_key=first_run_key(
            workspace=WORKSPACE, scene_id=SCENE, scene_version=1, idempotency_key=KEY
        ),
        workstream=wid,
    )
    assert spec.workstream == result.workstream.workstream_id
    assert spec.workstream == workstream_id(SCENE, 1)
    assert spec_fingerprint(spec) != spec_fingerprint(
        spec.model_validate(
            {**spec.model_dump(mode="python"), "workstream": workstream_id(SCENE, 2)}
        )
    )


# --- 2. 次序：冻结在受理之前 ----------------------------------------------------


def test_the_freeze_happens_before_the_admission():
    """每一次启动绑的是**冻结了的那一版** —— 反过来会让一轮 Run 引用一份还没有
    落盘的编译结果。判据是**次序**，不是各判一次。"""
    _, _, trace, _ = _booted()
    assert trace.events == ["freeze", "admit"]


def test_a_refused_start_never_freezes():
    """被拒的启动不留下一份没人启动过的有效场景 —— 这是"一个字都没写"那句话的落点。"""
    # 三种拒绝各跑一遍：场景不可派发、版本对不上、场景不在盘上。
    for label, record, kwargs in (
        (
            "paused",
            _record(state=SceneState.PAUSED, pause_reason=PauseReason.OPERATOR),
            {},
        ),
        ("version", _record(latest_version=2, effective_version=1), {"scene_version": 2}),
        ("missing", None, {}),
    ):
        store = RecordingWorkstreamStore()
        scenes = _scenes(record)
        trace = Trace()
        with pytest.raises(ConfigError):
            _start(store, scenes, trace, **kwargs)
        assert trace.events == [], f"{label}：被拒的启动既不该冻结，也不该受理"
        assert store.writes == 0, f"{label}：一个字都没写"


def test_a_start_that_cannot_find_its_scene_freezes_nothing():
    store = RecordingWorkstreamStore()
    scenes = _scenes()  # 空盘
    trace = Trace()
    with pytest.raises(ConfigError) as caught:
        _start(store, scenes, trace)
    assert SCENE in str(caught.value)
    assert trace.events == [] and store.writes == 0


def test_freezing_is_optional_and_the_layer_says_so():
    """``freeze=None`` 是这一层的一个合法用法（绑定与受理不需要有效场景）；
    而 handler 那一层**总是**给它。少了这一条判据，一个把 freeze 写死的实现
    与一个会接它的实现在这里同形。"""
    store = InMemoryWorkstreamStore()
    scenes = _scenes(_record())
    trace = Trace()
    result = _start(store, scenes, trace, freeze=None)

    assert trace.events == ["admit"]
    assert result.workstream.ordinal == 1


# --- 3. 重发：一行都不写 --------------------------------------------------------


def test_a_replay_writes_nothing_at_all():
    """**承重点 1 在应用层的判据。** 判据是写次数：一次重发连一次幂等的
    ``put_launch`` 都不做，更不用说 ``put_scene``。"""
    store, scenes, trace, first = _booted()
    before = store.writes
    trace.events.clear()

    second = _start(store, scenes, trace)

    assert second.replayed is True
    assert store.writes == before, "重发一行都不写"
    assert trace.events == [], "重发不再冻结、也不再受理"
    assert second.launch.launch_id == first.launch.launch_id
    assert second.workstream == first.workstream


def test_a_replay_hands_back_the_row_on_disk_not_the_argument():
    """同一个键配同一份内容（``name`` / ``by`` 都没变）⇒ 原样回那一次的结果。
    回刚拼出来的对象会让"这一次是谁按的"与盘上那一行分家。"""
    store, scenes, _, first = _booted()
    second = _start(store, scenes, Trace(), name=NAME, by=BY, at=T1)

    assert second.launch == first.launch
    assert second.launch.created_at == T0, "时刻是盘上那一行的，不是这一次的"
    assert second.workstream.revision == first.workstream.revision


def test_a_replay_with_another_body_is_refused_and_names_both_digests():
    """键回答"是不是同一次请求"，摘要回答"交上来的内容有没有变"。"""
    store, scenes, _, first = _booted()

    with pytest.raises(StoreError) as caught:
        _start(store, scenes, Trace(), name="换了个名字")

    message = str(caught.value)
    assert first.launch.request_digest in message
    assert (
        request_digest_of(
            workspace=WORKSPACE,
            scene_id=SCENE,
            scene_version=1,
            idempotency_key=KEY,
            name="换了个名字",
            by=BY,
        )
        in message
    )


def test_the_request_digest_carries_the_name_and_the_operator_but_not_the_clock():
    """摘要不装时刻：同一个请求在两分钟后再交一次仍然是同一个请求。"""
    base = dict(
        workspace=WORKSPACE, scene_id=SCENE, scene_version=1, idempotency_key=KEY
    )
    assert request_digest_of(**base, name=NAME, by=BY) == request_digest_of(
        **base, name=NAME, by=BY
    )
    assert request_digest_of(**base, name=NAME, by=BY) != request_digest_of(
        **base, name=NAME, by="bob"
    )
    assert request_digest_of(**base, name=NAME, by=BY) != request_digest_of(
        **base, name="另一个名字", by=BY
    )


def test_a_start_result_says_whether_it_replayed():
    """``replayed`` 是一格而不是"看回执里有没有 warning"：重发的处置是**什么都不做**，
    而新起之后通常还有下一步（去看那一轮跑到哪了）。"""
    fields = set(StartResult.__dataclass_fields__)
    assert {"launch", "workstream", "replayed", "why"} <= fields


# --- 4. 拒绝：三句话分得开 ------------------------------------------------------


def test_a_scene_in_another_workspace_is_refused_by_name():
    """工作区取自令牌声明，不是请求参数 —— 所以这一处拒绝的是**一次**跨工作区的启动。"""
    record = _record(workspace=OTHER_WORKSPACE)
    store = RecordingWorkstreamStore()
    scenes = _scenes(record)
    trace = Trace()

    with pytest.raises(EvidenceError) as caught:
        _start(store, scenes, trace)
    message = str(caught.value)
    assert WORKSPACE in message and OTHER_WORKSPACE in message
    assert store.writes == 0 and trace.events == []


def test_a_scene_that_cannot_dispatch_is_refused_with_its_own_sentence():
    """在派发被拒的场景上放行，会让一次启动开出一轮没人要的 Run。那条
    ``dispatch_refusal`` 的**原文**要交出去 —— 它是"谁能让它重新开工"的线索。"""
    record = _record(state=SceneState.PAUSED, pause_reason=PauseReason.OPERATOR)
    store = RecordingWorkstreamStore()
    trace = Trace()

    with pytest.raises(ConfigError) as caught:
        _start(store, _scenes(record), trace)

    message = str(caught.value)
    assert record.dispatch_refusal in message
    assert "状态" in message, "拦下这一次的是状态，不是这一版内容"
    assert store.writes == 0


def test_a_draft_scene_cannot_dispatch():
    record = _record(state=SceneState.DRAFT, effective_version=None)
    with pytest.raises(ConfigError) as caught:
        _start(RecordingWorkstreamStore(), _scenes(record), Trace())
    assert record.dispatch_refusal in str(caught.value)


def test_a_version_mismatch_names_both_versions_and_what_to_do_about_each():
    """两类情形两种处置：``latest_version`` 更大 ⇒ 有人写了一版新的（去批准它，
    或改用旧的那一版）；``effective_version`` 是别的数 ⇒ 传错了。合成一句
    "版本不对"会让这两种处置分不开。"""
    record = _record(latest_version=2, effective_version=1)
    with pytest.raises(ConfigError) as caught:
        _start(RecordingWorkstreamStore(), _scenes(record), Trace(), scene_version=2)

    message = str(caught.value)
    assert "第 2 版" in message and "第 1 版" in message
    assert "还没有生效" in message, "第 2 版已经写了但没生效，这一句要说出来"


def test_a_version_that_was_never_written_names_the_effective_one():
    record = _record()
    with pytest.raises(ConfigError) as caught:
        _start(RecordingWorkstreamStore(), _scenes(record), Trace(), scene_version=4)
    message = str(caught.value)
    assert "第 4 版" in message
    assert "第 1 版" in message


def test_a_scene_with_no_effective_version_says_so_instead():
    """``effective_version=None``（不是 ACTIVE）时那句提示是另一句 —— 一句
    "生效的是第 None 版"读起来像一次程序错误。"""
    record = _record(
        state=SceneState.PAUSED,
        pause_reason=PauseReason.POLICY,
        effective_version=None,
        latest_version=1,
    )
    with pytest.raises(ConfigError) as caught:
        _start(RecordingWorkstreamStore(), _scenes(record), Trace(), scene_version=1)
    assert "不能派发" in str(caught.value)


def test_the_scene_is_read_before_the_version_is_judged():
    """盘上根本没有这个场景时，报的是"场景不存在"而不是版本对不上 ——
    两者的处置相反（去建一个 / 改这一次的参数）。"""
    store = RecordingWorkstreamStore()
    scenes = InMemorySceneStore()
    with pytest.raises(ConfigError) as caught:
        _start(store, scenes, Trace())
    assert "没有 scene_id" in str(caught.value)


# --- 5. 控制：三档各要自己的参数 ------------------------------------------------


def _paused_store():
    """一个已经暂停的工作流，它的场景读得出来。"""
    store, scenes, trace, result = _booted()
    stream = control_workstream(
        workspace=WORKSPACE,
        workstream_id=result.workstream.workstream_id,
        action=WorkstreamAction.PAUSE,
        expected_revision=1,
        reason="等账号配额",
        name="",
        at=T1,
        store=store,
    )
    return store, scenes, stream


def test_pausing_takes_a_reason_and_bumps_the_revision():
    store, _, stream = _paused_store()
    assert stream.state is WorkstreamState.PAUSED
    assert stream.pause_reason == "等账号配额"
    assert stream.revision == 2
    assert stream.updated_at == T1
    assert store.stream_writes[-1][1] == 1, "CAS 的载体是 expected_revision"


def test_pausing_without_a_reason_is_refused_before_any_write():
    store, _, _, result = _booted()
    before = store.writes
    with pytest.raises(ConfigError) as caught:
        control_workstream(
            workspace=WORKSPACE,
            workstream_id=result.workstream.workstream_id,
            action=WorkstreamAction.PAUSE,
            expected_revision=1,
            reason="   ",
            name="",
            at=T1,
            store=store,
        )
    assert "pause" in str(caught.value)
    assert store.writes == before


def test_resuming_clears_the_reason():
    """一个一边跑一边带着"为什么停着"的工作流，读的人不知道该不该去处理那个理由。"""
    store, scenes, paused = _paused_store()
    resumed = control_workstream(
        workspace=WORKSPACE,
        workstream_id=paused.workstream_id,
        action=WorkstreamAction.RESUME,
        expected_revision=2,
        reason="",
        name="",
        at=T1,
        store=store,
    )
    assert resumed.state is WorkstreamState.RUNNING
    assert resumed.pause_reason == ""
    assert resumed.revision == 3


def test_resuming_a_running_workstream_is_refused_as_a_non_transition():
    """放它过去会让 revision 加一、回执说成功了，而盘上什么都没变。"""
    store, _, _, result = _booted()
    with pytest.raises(WorkstreamStateError) as caught:
        control_workstream(
            workspace=WORKSPACE,
            workstream_id=result.workstream.workstream_id,
            action=WorkstreamAction.RESUME,
            expected_revision=1,
            reason="",
            name="",
            at=T1,
            store=store,
        )
    assert "同一格" in str(caught.value)


def test_pausing_a_paused_workstream_is_refused_too():
    store, _, paused = _paused_store()
    with pytest.raises(WorkstreamStateError):
        control_workstream(
            workspace=WORKSPACE,
            workstream_id=paused.workstream_id,
            action=WorkstreamAction.PAUSE,
            expected_revision=2,
            reason="再停一次",
            name="",
            at=T1,
            store=store,
        )


def test_renaming_needs_a_name_and_an_empty_string_is_not_a_name():
    """"改名成空"与"没打算改名"是两件事：前者是 ``name=""`` 的一次改名，
    后者是不调这个工具。把前者当成后者，会让一次改名在盘上不留痕。"""
    store, _, _, result = _booted()
    with pytest.raises(ConfigError) as caught:
        control_workstream(
            workspace=WORKSPACE,
            workstream_id=result.workstream.workstream_id,
            action=WorkstreamAction.RENAME,
            expected_revision=1,
            reason="",
            name="  ",
            at=T1,
            store=store,
        )
    assert "rename" in str(caught.value)


def test_renaming_a_running_workstream_keeps_its_state():
    store, _, _, result = _booted()
    renamed = control_workstream(
        workspace=WORKSPACE,
        workstream_id=result.workstream.workstream_id,
        action=WorkstreamAction.RENAME,
        expected_revision=1,
        reason="",
        name="第二版名字",
        at=T1,
        store=store,
    )
    assert renamed.name == "第二版名字"
    assert renamed.state is WorkstreamState.RUNNING
    assert renamed.revision == 2


def test_renaming_a_paused_workstream_keeps_its_reason():
    """**读回缺陷的判据。**

    ``control_workstream`` 对三档共用一句 ``"pause_reason": reason if
    action.requires_reason else ""``，于是 ``RENAME``（它不要求原因）会把一个
    **暂停中**的工作流的暂停原因写成空串。``model_copy`` 不校验，所以写这一步
    是无声的；而 ``domain/workstream.py`` 的校验器要求"暂停必须写明原因"，
    于是下一次**读**这个工作流时抛 —— 一处与调用点无关的地方。

    这一条钉住的是**读得回来**，不是"改名成功了"：只判回执的实现看不见这个缺陷，
    因为回执走的是 ``get_workstream``…… 而它也读那一行，所以它同样会炸。
    """
    store, _, paused = _paused_store()

    renamed = control_workstream(
        workspace=WORKSPACE,
        workstream_id=paused.workstream_id,
        action=WorkstreamAction.RENAME,
        expected_revision=2,
        reason="",
        name="第二版名字",
        at=T1,
        store=store,
    )

    assert renamed.state is WorkstreamState.PAUSED
    assert renamed.name == "第二版名字"
    assert renamed.pause_reason == "等账号配额", "改名不该把一个暂停原因清掉"


def test_a_stale_revision_does_not_overwrite():
    """CAS 的载体在存储那一层；这一层把 ``expected_revision + 1`` 交下去。
    过期的改不进去，而那句话把两版都点出来。"""
    store, _, _, result = _booted()
    control_workstream(
        workspace=WORKSPACE,
        workstream_id=result.workstream.workstream_id,
        action=WorkstreamAction.PAUSE,
        expected_revision=1,
        reason="等账号配额",
        name="",
        at=T1,
        store=store,
    )
    with pytest.raises(StoreError) as caught:
        control_workstream(
            workspace=WORKSPACE,
            workstream_id=result.workstream.workstream_id,
            action=WorkstreamAction.RESUME,
            expected_revision=1,
            reason="",
            name="",
            at=T1,
            store=store,
        )
    message = str(caught.value)
    assert "第 1 版" in message and "第 2 版" in message


def test_controlling_an_unknown_workstream_names_the_shape_of_the_key():
    store = InMemoryWorkstreamStore()
    with pytest.raises(ConfigError) as caught:
        control_workstream(
            workspace=WORKSPACE,
            workstream_id=workstream_id(SCENE, 9),
            action=WorkstreamAction.PAUSE,
            expected_revision=1,
            reason="等账号配额",
            name="",
            at=T1,
            store=store,
        )
    message = str(caught.value)
    assert workstream_id(SCENE, 9) in message
    assert "ordinal" in message


def test_controlling_another_workspaces_workstream_is_an_evidence_error():
    store, _, _, result = _booted()
    with pytest.raises(EvidenceError) as caught:
        control_workstream(
            workspace=OTHER_WORKSPACE,
            workstream_id=result.workstream.workstream_id,
            action=WorkstreamAction.PAUSE,
            expected_revision=1,
            reason="等账号配额",
            name="",
            at=T1,
            store=store,
        )
    assert WORKSPACE in str(caught.value)


def test_pausing_one_workstream_leaves_the_other_rows_alone():
    """"停一个工作流不改变另一个的任何一格" —— 这一层没有可写它的路径
    （签名里只有 ``workstream_id`` 一个主语）。"""
    store = InMemoryWorkstreamStore()
    scenes = _scenes(_record(latest_version=2, effective_version=2))
    trace = Trace()
    first = _start(store, scenes, trace, scene_version=2)
    second = _start(store, scenes, trace, scene_version=2, idempotency_key="launch-second")

    control_workstream(
        workspace=WORKSPACE,
        workstream_id=first.workstream.workstream_id,
        action=WorkstreamAction.PAUSE,
        expected_revision=1,
        reason="等账号配额",
        name="",
        at=T1,
        store=store,
    )

    assert store.get_workstream(second.workstream.workstream_id) == second.workstream


# --- 6. 读数：两半各说各的话 ----------------------------------------------------


def test_a_running_workstream_on_an_active_scene_dispatches():
    store, scenes, _, result = _booted()
    receipt = get_workstream(
        workspace=WORKSPACE,
        workstream_id=result.workstream.workstream_id,
        store=store,
        scenes=scenes,
    )
    assert receipt.dispatch_allowed is True
    assert receipt.dispatch_refusal == ""
    assert receipt.scene_state == SceneState.ACTIVE.value
    assert receipt.workstream_id == result.workstream.workstream_id


def test_a_paused_workstream_does_not_dispatch_and_says_why():
    store, scenes, paused = _paused_store()
    receipt = get_workstream(
        workspace=WORKSPACE,
        workstream_id=paused.workstream_id,
        store=store,
        scenes=scenes,
    )
    assert receipt.dispatch_allowed is False
    assert "等账号配额" in receipt.dispatch_refusal


def test_a_paused_workstream_without_a_reason_still_says_something():
    """一个没有原因的暂停不该与"一切正常"同形，也不该在读数这一层炸。

    这样一行**今天构造不出来**（``Workstream._paused_says_why`` 要求暂停必须写明
    原因）—— 而它照样是可达的：``control_workstream`` 走的是 ``model_copy``，
    那条路不校验，于是同一类行以前真的被写进过盘（见模块 docstring 那个改名缺陷）。
    所以这一条判的是**读数这一层**对它的兜底：不抛，而且说得出"未写明原因"。
    """
    _, _, _, result = _booted()
    stream = result.workstream.model_copy(
        update={"state": WorkstreamState.PAUSED, "pause_reason": ""}
    )
    allowed, refusal = _dispatch_read(stream, _record())
    assert allowed is False
    assert "未写明原因" in refusal


def test_a_paused_scene_also_blocks_dispatch_even_when_the_workstream_runs():
    """两半都不许被对方顶替：一个只看工作流状态的实现在场景被暂停时照常放行。"""
    store, _, _, result = _booted()
    paused_scene = _scenes(
        _record(state=SceneState.PAUSED, pause_reason=PauseReason.OPERATOR)
    )
    receipt = get_workstream(
        workspace=WORKSPACE,
        workstream_id=result.workstream.workstream_id,
        store=store,
        scenes=paused_scene,
    )
    assert receipt.dispatch_allowed is False
    assert "场景那一半说不行" in receipt.dispatch_refusal


def test_both_halves_speak_when_both_refuse():
    """一个"两句话取一句"的实现会让另一半在报告上消失，而读的人因此看不到另一半
    还有问题。"""
    store, _, paused = _paused_store()
    scenes = _scenes(
        _record(state=SceneState.PAUSED, pause_reason=PauseReason.POLICY)
    )
    receipt = get_workstream(
        workspace=WORKSPACE,
        workstream_id=paused.workstream_id,
        store=store,
        scenes=scenes,
    )
    assert "工作流本身是 paused" in receipt.dispatch_refusal
    assert "场景那一半说不行" in receipt.dispatch_refusal


def test_a_workstream_whose_scene_is_gone_says_the_scene_half_was_not_decided():
    """那个场景不在盘上时，场景那一半的判定**没有**被做出 —— 而不是被判成了放行。"""
    store, _, _, result = _booted()
    receipt = get_workstream(
        workspace=WORKSPACE,
        workstream_id=result.workstream.workstream_id,
        store=store,
        scenes=InMemorySceneStore(),
    )
    assert receipt.scene_state == ""
    assert receipt.scene_pending_version is None
    assert receipt.dispatch_allowed is False
    assert "被做出" in receipt.dispatch_refusal
    assert "而不是被判成了放行" in receipt.dispatch_refusal


def test_listing_an_empty_workspace_says_so_instead_of_returning_an_empty_list():
    """list 那条 summary 点名的正是这一格：本工作区没有工作流要**说出来**。"""
    listing = list_workstreams(
        workspace=WORKSPACE, store=InMemoryWorkstreamStore(), scenes=_scenes()
    )
    assert listing.streams == ()
    assert listing.note == NOTHING_IN_THIS_WORKSPACE


def test_listing_is_scoped_to_the_workspace_it_was_given():
    store = RecordingWorkstreamStore()
    scenes = _scenes(_record())
    trace = Trace()
    _start(store, scenes, trace)

    other = list_workstreams(workspace=OTHER_WORKSPACE, store=store, scenes=scenes)
    mine = list_workstreams(workspace=WORKSPACE, store=store, scenes=scenes)

    assert other.streams == ()
    assert other.note == NOTHING_IN_THIS_WORKSPACE
    assert len(mine.streams) == 1
    assert mine.note == ""


def test_a_non_empty_listing_carries_no_note():
    """``note`` 的两档今天只有一档：一个恒非空的 note 会让"这句话有意义"在第一次
    出现时就已经被稀释了。"""
    store = RecordingWorkstreamStore()
    scenes = _scenes(_record())
    _start(store, scenes, Trace())
    listing = list_workstreams(workspace=WORKSPACE, store=store, scenes=scenes)
    assert listing.streams[0].dispatch_allowed is True
    assert listing.note == ""


def test_listing_reads_the_scene_of_each_row_not_of_the_last_one():
    """每一行的场景读数取自**它自己**引的那一版 —— 一个把同一个记录复用给所有行的
    实现在两行属于不同场景时给出同一个答案。"""
    store = InMemoryWorkstreamStore()
    scenes = InMemorySceneStore()
    for scene_id in (SCENE, OTHER_SCENE):
        scenes.put_version(_scene(scene_id=scene_id))
        scenes.put_record(_record(scene_id=scene_id), expected_revision=None)
    trace = Trace()
    _start(store, scenes, trace)
    _start(store, scenes, trace, scene_id=OTHER_SCENE, idempotency_key="launch-other")

    listing = list_workstreams(workspace=WORKSPACE, store=store, scenes=scenes)
    assert {row.workstream.scene_id for row in listing.streams} == {SCENE, OTHER_SCENE}
    assert all(row.scene_state == SceneState.ACTIVE.value for row in listing.streams)


# --- 7. 两个存储实现上跑同一组控制判据 ------------------------------------------
#
# 「改名不得清掉暂停原因」那条缺陷之所以能活到实测，是因为它**只在 SQLite 上抛**
# 而**只在内存替身上静默写错** —— 两个实现各错一半，而只跑一个实现的用例两份都
# 看不见。下面这一组在两个实现上跑同一段剧本。


@pytest.fixture(params=["sqlite", "memory"])
def any_store(request, tmp_path):
    if request.param == "sqlite":
        from pathlib import Path

        from marketops.adapters.sqlite.workstreams import SqliteWorkstreamStore

        return SqliteWorkstreamStore(Path(tmp_path) / "state" / "marketops.db")
    return InMemoryWorkstreamStore()


def _seed_paused(store) -> str:
    """往任意一个实现里种一行**暂停中**的工作流，回它的业务键。"""
    wid = workstream_id(SCENE, 1)
    store.put_workstream(
        Workstream(
            workstream_id=wid,
            workspace=WORKSPACE,
            scene_id=SCENE,
            scene_version=1,
            ordinal=1,
            launch_id=launch_id_of(
                workspace=WORKSPACE, scene_id=SCENE, scene_version=1, idempotency_key=KEY
            ),
            state=WorkstreamState.PAUSED,
            revision=1,
            queue=WorkstreamQueue.RUN_ADMITTED,
            pause_reason="等账号配额",
            created_at=T0,
            updated_at=T0,
        ),
        expected_revision=None,
    )
    return wid


def test_renaming_a_paused_workstream_works_on_both_stores(any_store):
    """同一个剧本在 SQLite 与内存替身上都要成立。

    SQLite 那一份会在**写**的时候重建对象（于是缺陷的形状是"写不成、抛的是
    ``ValidationError``"），内存替身原样存着（于是形状是"写成了、原因没了"）。
    只跑一个实现的用例对另一种形状是瞎的。
    """
    wid = _seed_paused(any_store)

    renamed = control_workstream(
        workspace=WORKSPACE,
        workstream_id=wid,
        action=WorkstreamAction.RENAME,
        expected_revision=1,
        reason="",
        name="第二版名字",
        at=T1,
        store=any_store,
    )

    assert renamed.name == "第二版名字"
    assert renamed.state is WorkstreamState.PAUSED
    assert renamed.pause_reason == "等账号配额", "改名不动原因"
    assert any_store.get_workstream(wid).pause_reason == "等账号配额", "盘上那一行也读得回来"


def test_pause_rename_resume_round_trips_on_both_stores(any_store):
    """三档连着走一遍：暂停带原因、改名不动它、恢复清掉它。"""
    wid = _seed_paused(any_store)

    renamed = control_workstream(
        workspace=WORKSPACE,
        workstream_id=wid,
        action=WorkstreamAction.RENAME,
        expected_revision=1,
        reason="",
        name="第二版名字",
        at=T1,
        store=any_store,
    )
    assert renamed.pause_reason == "等账号配额"

    resumed = control_workstream(
        workspace=WORKSPACE,
        workstream_id=wid,
        action=WorkstreamAction.RESUME,
        expected_revision=2,
        reason="",
        name="",
        at=T1,
        store=any_store,
    )
    assert resumed.pause_reason == "", "恢复清掉原因 —— 一边跑一边带着理由是不合法的行"
    assert any_store.get_workstream(wid).pause_reason == ""


def test_a_paused_row_without_a_reason_poisons_the_row_on_sqlite(tmp_path):
    """**这条判据说的是那条缺陷在 SQLite 上的形状：写进去了，然后读不回来。**

    ``put_workstream`` 不重建对象（它把负载写下去就完事），所以"暂停 + 空原因"那一行
    **写得进去**；而 ``get_workstream`` 会重建对象，于是校验器在那里炸。两条连起来的
    后果比"写不成"重得多：``control_workstream`` 的写已经提交了，随后那次读回抛异常
    —— 那一行从此读不回来，而报出来的是一句关于 ``pause_reason`` 的 ``ValidationError``
    （不是本仓的错误类型，于是出去的形状是"服务端崩了"）。

    这也解释了为什么那条缺陷只可能从**绕开校验器**的那条路写进来：会重建对象的那条
    读路径不许，而 ``control_workstream`` 用的是不校验的 ``model_copy``。
    """
    from pathlib import Path

    from marketops.adapters.sqlite.workstreams import SqliteWorkstreamStore

    store = SqliteWorkstreamStore(Path(tmp_path) / "state" / "marketops.db")
    wid = _seed_paused(store)

    store.put_workstream(
        store.get_workstream(wid).model_copy(update={"pause_reason": "", "revision": 2}),
        expected_revision=1,
    )

    with pytest.raises(Exception) as caught:
        store.get_workstream(wid)
    assert "pause_reason" in str(caught.value), (
        "抛出来的描述里要有那一格的名字 —— 否则读的人不知道去修哪一格"
    )
