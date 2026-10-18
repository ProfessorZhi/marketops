"""``domain/workstream.py`` 自己的判据：**业务键的形状、两个模型的校验器、状态迁移**。

这里问的不是存储（那一份在 ``tests/test_workstream_store_contract.py``），也不是
接线（那一份在处理器测试里）。它问的是**这些对象在构造期挡下了什么**。

## 头两条判据是 2026-09-29 一次实测的回归

契约测试第一条用例在构造 ``LaunchRecord`` 时就红了，两个缺陷都在**模型自己的校验器**
上，而它们各自都长得像「存储那一层写错了」：

* ``workstream_id`` 走 ``check_canonical_id`` —— 而冻结表 §3.2 的业务键是
  ``<scene_id>#<ordinal>``，``slugify`` 会把 ``#`` 换成 ``-``，于是**每一个**
  Workstream 都构造不出来。
* ``launch_id_of`` 返回 ``"launch-" + 64 位摘要`` = 71 个字符，越过
  ``FILENAME_ID_MAX_LENGTH = 60`` —— 生产者造出来的 id 被它自己的模型拒了。

两条都不是「校验太严」，而是**判据用错了对象**（一个是引用，不是文件名；一个是
它的生产者与它不共用同一个界）。回归测试钉在这里，因为把它们改回去会让整条链
在第一步就炸 —— 而症状出现在另一个模块里。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from marketops.domain.enums import BudgetLine, ExecutionMode
from marketops.domain.run import RunBudgetLine, RunSpec, spec_fingerprint
from marketops.domain.util import check_canonical_id, slugify
from marketops.domain.workstream import (
    WORKSTREAM_TRANSITIONS,
    LaunchRecord,
    Workstream,
    WorkstreamAction,
    WorkstreamQueue,
    WorkstreamState,
    WorkstreamStateError,
    check_workstream_id,
    launch_id_of,
    launch_subject,
    workstream_id,
)

UTC = timezone.utc
T0 = datetime(2026, 9, 29, 1, 0, tzinfo=UTC)
T1 = T0 + timedelta(hours=1)
WORKSPACE = "ws-natural-grass"
SCENE = "scene-uk-natural-grass"
KEY = "launch-2026-09-29-natural-grass"


def _launch(**overrides) -> LaunchRecord:
    payload = {
        "workspace": WORKSPACE,
        "scene_id": SCENE,
        "scene_version": 1,
        "idempotency_key": KEY,
        "workstream_id": workstream_id(SCENE, 1),
        "request_digest": "a" * 64,
        "created_at": T0,
        "launched_by": "alice",
    }
    payload.update(overrides)
    if "launch_id" not in payload:
        # 显式给 ``launch_id`` 的用例（对不上、空键、版本 0）要能走到模型的校验器上 ——
        # 所以这里**不能**写成 ``setdefault``：那个参数是**先算出来**再判有没有的，
        # 于是生产者会替模型先报一次错，而用例测的就不是模型了。
        payload["launch_id"] = launch_id_of(
            workspace=payload["workspace"],
            scene_id=payload["scene_id"],
            scene_version=payload["scene_version"],
            idempotency_key=payload["idempotency_key"],
        )
    return LaunchRecord(**payload)


def _stream(**overrides) -> Workstream:
    payload = {
        "workspace": WORKSPACE,
        "scene_id": SCENE,
        "scene_version": 1,
        "ordinal": 1,
        "launch_id": _launch().launch_id,
        "state": WorkstreamState.RUNNING,
        "revision": 1,
        "queue": WorkstreamQueue.RUN_ADMITTED,
        "created_at": T0,
        "updated_at": T0,
    }
    payload.update(overrides)
    payload.setdefault("workstream_id", workstream_id(payload["scene_id"], payload["ordinal"]))
    return Workstream(**payload)


def _spec(**overrides) -> RunSpec:
    payload = {
        "workspace": WORKSPACE,
        "objective": "补一轮 Devon 的场地",
        "selector": SCENE,
        "context_ref": "a" * 64,
        "plan_ref": "b" * 64,
        "execution_mode": ExecutionMode.MANUAL,
        "window": None,
        "budget": (RunBudgetLine(line=BudgetLine.SEARCH, ceiling=20),),
        "cutoff": T0 + timedelta(days=1),
        "policy_versions": ("grading@1f2e3d",),
        "idempotency_key": KEY,
    }
    payload.update(overrides)
    return RunSpec(**payload)


# --- 业务键：生产者与它的读法成对 ----------------------------------------------


def test_the_id_says_which_scene_and_which_launch():
    assert workstream_id(SCENE, 1) == f"{SCENE}#1"
    assert workstream_id(SCENE, 12) == f"{SCENE}#12"


def test_the_id_must_be_a_canonical_scene_id():
    """``#`` 的唯一性靠这一条：规范 id 不含 ``#``（``slugify`` 的字符集里没有它）。"""
    with pytest.raises(ValueError):
        workstream_id("Scene UK Natural Grass", 1)
    with pytest.raises(ValueError):
        workstream_id("scene#uk", 1)


def test_ordinal_starts_at_one():
    with pytest.raises(ValueError) as caught:
        workstream_id(SCENE, 0)
    assert "ordinal" in str(caught.value)


@pytest.mark.parametrize(
    "value",
    [
        SCENE,  # 少了序号
        f"#{1}",  # 少了场景
        f"{SCENE}#",  # 少了序号
        f"{SCENE}#1#2",  # 多了一个 #
        f"{SCENE}#0",  # 序号从 1 起
        f"{SCENE}#-1",
        f"{SCENE}# 3",
        f"{SCENE}#0x2",
        f"{SCENE}#one",
        "",
    ],
)
def test_the_reader_refuses_everything_the_producer_cannot_build(value: str):
    """**逐条对齐生产者**：一个能通过的写法会在盘上表示一个生产者造不出来的工作流。"""
    with pytest.raises(ValueError):
        check_workstream_id(value)


def test_the_reader_is_the_inverse_of_the_producer():
    for ordinal in (1, 2, 41):
        built = workstream_id(SCENE, ordinal)
        assert check_workstream_id(built) == built
    assert check_workstream_id(f"{SCENE}#7", "别的字段名") == f"{SCENE}#7"


def test_the_business_key_is_a_reference_not_a_filename():
    """**这条是那次回归的判据本身。**

    ``check_canonical_id`` 是「id → 文件名」的判据，而业务键**没有落盘面**：它是
    ``RunSpec.workstream`` 装的那个引用。把两者混起来的后果是**每一个** Workstream
    都构造不出来，而报的是一句关于文件名的话（与 ``util.py`` 那条 docstring 自己
    点名的 ``account:acme`` / ``view:v1`` 是同一个误用）。

    所以这里钉住两半：``check_canonical_id`` **确实**会拒它（这就是当初的错因，
    改回去会立刻红），而两个模型**收**它。
    """
    built = workstream_id(SCENE, 1)
    with pytest.raises(ValueError) as caught:
        check_canonical_id(built, "workstream_id")
    assert "规范形式" in str(caught.value)

    assert slugify(built, max_length=60) != built, "它本来就不是 slugify 的不动点"
    assert _stream().workstream_id == built
    assert _launch().workstream_id == built


def test_the_launch_id_is_a_filename_and_its_producer_says_so():
    """``launch_id`` 是 **71 个字符**里的那一类：``"launch-" + 64 位摘要`` 会被
    ``slugify`` 截到 60，于是生产者造出来的 id 被它自己的模型拒（那次回归的另一半）。

    判据是「生产者吐出来的东西能过模型」，且**唯一的那个界**（
    ``FILENAME_ID_MAX_LENGTH``）在两处是同一个。
    """
    produced = launch_id_of(
        workspace=WORKSPACE, scene_id=SCENE, scene_version=1, idempotency_key=KEY
    )
    assert check_canonical_id(produced, "launch_id") == produced
    assert slugify(produced, max_length=60) == produced, "它必须是 slugify 的不动点"
    assert len(produced) <= 60
    assert _launch().launch_id == produced


def test_the_launch_id_is_content_addressed_and_has_no_clock():
    """同一个请求的第二次提交**不可能**拿到一个新 id —— 这是构造，不是自觉。"""
    key = dict(
        workspace=WORKSPACE, scene_id=SCENE, scene_version=1, idempotency_key=KEY
    )
    assert launch_id_of(**key) == launch_id_of(**key)
    # 四格里的每一格都进摘要：少判一格，两次不同的启动会拿到同一个 id。
    assert launch_id_of(**{**key, "workspace": "ws-other"}) != launch_id_of(**key)
    assert launch_id_of(**{**key, "scene_id": "scene-de-natural-grass"}) != launch_id_of(
        **key
    )
    assert launch_id_of(**{**key, "scene_version": 2}) != launch_id_of(**key)
    assert launch_id_of(**{**key, "idempotency_key": "another"}) != launch_id_of(**key)


def test_the_launch_id_producer_refuses_an_empty_key():
    with pytest.raises(ValueError):
        launch_id_of(workspace=WORKSPACE, scene_id=SCENE, scene_version=1, idempotency_key="  ")


def test_the_launch_subject_names_all_three():
    """主语要能自己说出「这一次是哪一版、拿哪个键提的」—— 少一个就与另一次同形。"""
    subject = launch_subject(SCENE, 1, KEY)
    assert SCENE in subject and "@1" in subject and KEY in subject


# --- LaunchRecord 的校验器 -----------------------------------------------------


def test_an_empty_idempotency_key_is_not_a_key():
    """空键会让**每一次**不带键的启动都撞在同一条 UNIQUE 上。"""
    produced = _launch().launch_id
    with pytest.raises(ValidationError):
        _launch(launch_id=produced, idempotency_key="  ")


def test_the_launch_id_must_be_the_one_the_key_produces():
    """``launch_id`` 由那四格推出：手填一个别的，重发就认不出是同一次。"""
    with pytest.raises(ValidationError) as caught:
        _launch(launch_id="launch-whatever")
    assert "对不上" in str(caught.value)


def test_a_launch_may_say_that_the_first_round_was_not_admitted():
    """空 ``run_id`` 是**说出来的**「那一轮没有被受理」，不是缺一格。"""
    assert _launch().run_id == ""
    assert _launch(run_id="run-abc").run_id == "run-abc"
    with pytest.raises(ValidationError):
        _launch(run_id="not a canonical id")


def test_the_business_key_property_is_the_four_columns():
    record = _launch()
    assert record.business_key == (WORKSPACE, SCENE, 1, KEY)


def test_a_launch_needs_someone_who_approved_it():
    with pytest.raises(ValidationError):
        _launch(launched_by="")


def test_the_version_starts_at_one():
    produced = _launch().launch_id
    with pytest.raises(ValidationError):
        _launch(launch_id=produced, scene_version=0)


# --- Workstream 的校验器 -------------------------------------------------------


def test_the_id_and_the_ordinal_field_say_the_same_thing():
    """两处说的是同一件事：id 说 ``#2`` 而 ``ordinal`` 是 1 的行，读的人信哪一个？"""
    with pytest.raises(ValidationError):
        _stream(ordinal=1, workstream_id=workstream_id(SCENE, 2))
    with pytest.raises(ValidationError):
        _stream(ordinal=2, workstream_id=workstream_id(SCENE, 1))
    assert _stream(ordinal=2, workstream_id=workstream_id(SCENE, 2)).ordinal == 2


def test_a_paused_workstream_says_why():
    with pytest.raises(ValidationError):
        _stream(state=WorkstreamState.PAUSED)
    paused = _stream(state=WorkstreamState.PAUSED, pause_reason="等账号配额")
    assert paused.pause_reason == "等账号配额"


def test_a_running_workstream_does_not_carry_a_reason():
    """一个跑着的工作流带一句暂停原因，读的人分不清它到底停没停。"""
    with pytest.raises(ValidationError):
        _stream(state=WorkstreamState.RUNNING, pause_reason="等账号配额")


def test_updated_is_never_before_created():
    with pytest.raises(ValidationError):
        _stream(updated_at=T0 - timedelta(seconds=1))
    assert _stream(updated_at=T1).updated_at == T1


def test_a_workstream_may_say_it_has_no_wake_up():
    """``None`` 是**说出来的**「没有人会来叫它」（手动那一轮），与「忘了填」不同形。"""
    assert _stream().next_wake_at is None
    assert _stream(next_wake_at=T1).next_wake_at == T1


def test_the_queue_has_exactly_two_states():
    """闭集是这一格能当判据的前提：多一个状态就要多一条迁移判据。"""
    assert {state.value for state in WorkstreamQueue} == {"idle", "run_admitted"}


def test_the_state_machine_has_exactly_two_states_and_they_are_mutually_reachable():
    assert set(WORKSTREAM_TRANSITIONS) == {WorkstreamState.RUNNING, WorkstreamState.PAUSED}
    assert WORKSTREAM_TRANSITIONS[WorkstreamState.RUNNING] == frozenset(
        {WorkstreamState.PAUSED}
    )
    assert WORKSTREAM_TRANSITIONS[WorkstreamState.PAUSED] == frozenset(
        {WorkstreamState.RUNNING}
    )


def test_only_pausing_needs_a_reason():
    """「带类型」的落点：三档各自要不要参数写在枚举上，而不是每个调用点记一遍。"""
    assert WorkstreamAction.PAUSE.requires_reason is True
    assert WorkstreamAction.RESUME.requires_reason is False
    assert WorkstreamAction.RENAME.requires_reason is False
    assert WorkstreamAction.PAUSE.target_state is WorkstreamState.PAUSED
    assert WorkstreamAction.RESUME.target_state is WorkstreamState.RUNNING
    assert WorkstreamAction.RENAME.target_state is None


def test_the_state_error_is_a_value_error():
    """**接口事实**：``WorkstreamStateError`` 是 ``ValueError`` 的子类，于是
    ``adapters/mcp_server/runs.py::_ADMISSION_REFUSALS`` 那一组**不**收它 ——
    处理器必须显式 catch，否则一次非法迁移会以「服务端崩了」的形状出去。"""
    assert issubclass(WorkstreamStateError, ValueError)


# --- 工作流那一格进了 RunSpec，也进了它的摘要 ----------------------------------


def test_a_run_spec_may_say_which_workstream_it_belongs_to():
    assert _spec().workstream is None, "缺省是 None = 这一轮不在任何启动之下"
    assert _spec(workstream=workstream_id(SCENE, 1)).workstream == workstream_id(SCENE, 1)


@pytest.mark.parametrize("bad", ["", SCENE, "#1", f"{SCENE}#0", f"{SCENE}#1#2"])
def test_a_run_spec_refuses_a_workstream_that_is_not_a_business_key(bad: str):
    with pytest.raises(ValidationError):
        _spec(workstream=bad)


def test_the_workstream_is_in_the_fingerprint():
    """同一个键配两个工作流是**两次不同的提交** —— 不进摘要，第二个工作流会以为
    自己有了一轮 Run（静默拿回第一次那一行）。"""
    without = _spec()
    first = _spec(workstream=workstream_id(SCENE, 1))
    second = _spec(workstream=workstream_id(SCENE, 2))

    assert spec_fingerprint(without) != spec_fingerprint(first)
    assert spec_fingerprint(first) != spec_fingerprint(second)
    assert spec_fingerprint(first) == spec_fingerprint(
        _spec(workstream=workstream_id(SCENE, 1))
    )
