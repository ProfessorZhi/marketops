"""``ports.runs.RunStore.stop`` 的契约 —— 两个实现跑同一份判据（§B17 条件 2）。

## 这一族要钉住的是一件事：**一次结束**

结局（``stop_reason``）、产出（``delta``）、停表（``stopped_at``）三者同时有或同时
无。分成两步（先写结局、再补产出）会造出一个「已结束但产出还没写」的窗口 ——
那个窗口里「它什么都没产出」与「产出丢了」长得一样，而这两件事的处置相反。

三处各守一半，缺一不可：

* ``domain/run.py::Run.__post_init__`` 管住**本进程里的构造点**；
* ``research_run`` 上的 ``CHECK`` 管住**别的进程**（任何绕过这个端口的写入）；
* 本文件把两处都判一遍 —— 前者是对着一个真的构造点，后者是对着一条真的 ``INSERT``。

## 三条与存储无关的断言，以及它们为什么在这里

1. **重放与冲突的分界是结局与产出，不是调用次数。** 同一个 ``(status, delta)``
   再来一次要**拿回同一行、一个字节都不改**（含 ``stopped_at``）；换一套就是
   ``RunStateError``。判重放的指纹住在 ``ports/runs.py::stopping_outcome`` ——
   两个实现各写一次的话，分家的形态是其中一个接受了另一个拒绝的重放，而两边都
   返回成功。
2. **两个实现给出的报文逐字相同。** 抄进实现里的话，两份会各自漂移，而漂移的
   形态是其中一份把「没有这一轮」说成「这一轮已经结束了」—— 那会把一次写错 id
   读成一次重放。一条只断言「抛了 ``RunStateError``」的判据在那天是绿的。
3. **被拒的结束一个字节都不落。** 那一行必须仍然**没有结束过** —— 不是「写下了
   但标记为拒绝」。一个先落盘再判的实现，会让一个从没跑完的 Run 在次日读起来
   像是一次正常结束。
"""

from __future__ import annotations

import itertools
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from marketops.adapters.memory import InMemoryRunStore
from marketops.adapters.sqlite.runs import SqliteRunStore
from marketops.domain.enums import BudgetLine, ExecutionMode, RunStatus
from marketops.domain.errors import RunSpecError, RunStateError
from marketops.domain.run import RunBudgetLine, RunSpec, run_id, spec_fingerprint
from marketops.domain.run_delta import DeltaCell, PoolEffect, RunDelta

WORKSPACE = "uk-acme-natural-grass"
SCENE = "scene-devon"
KEY = "run-2026-09-28-b"
T0 = datetime(2026, 9, 28, 9, 0, tzinfo=timezone.utc)
CUTOFF = T0 + timedelta(days=1)
STOPPED_AT = T0 + timedelta(hours=2)


def _spec(**overrides: object) -> RunSpec:
    payload: dict[str, object] = {
        "workspace": WORKSPACE,
        "objective": "补一轮 Devon 的场地",
        "selector": SCENE,
        "context_ref": "a" * 64,
        "plan_ref": "b" * 64,
        "execution_mode": ExecutionMode.MANUAL,
        "window": None,
        "budget": (RunBudgetLine(line=BudgetLine.SEARCH, ceiling=20),),
        "cutoff": CUTOFF,
        "policy_versions": ("grading@1f2e3d",),
        "idempotency_key": KEY,
    }
    payload.update(overrides)
    return RunSpec(**payload)  # type: ignore[arg-type]


def _delta(**overrides: object) -> RunDelta:
    """一份有内容的 Delta。``COMPLETED`` 要求它非空（``ending_refusal``）。"""
    payload: dict[str, object] = {
        "effects": (
            PoolEffect(cell=DeltaCell.DISCOVERED, key="v-1", label="Devon 球场"),
            PoolEffect(cell=DeltaCell.UNCHANGED, key="v-2", label="Exeter 球场"),
        ),
    }
    payload.update(overrides)
    return RunDelta(**payload)  # type: ignore[arg-type]


def _running(store):
    """受理一轮，返回「还没结束」的那一行。"""
    spec = _spec()
    return store.admit(spec, spec_sha256=spec_fingerprint(spec), at=T0)


@pytest.fixture(params=["sqlite", "memory"])
def store(request: pytest.FixtureRequest, tmp_path: Path):
    """两个实现之一。名字进测试 id —— 红了的时候一眼看得见是哪一个。"""
    if request.param == "sqlite":
        return SqliteRunStore(tmp_path / "state" / "marketops.db")
    return InMemoryRunStore()


# --- 一次结束 -----------------------------------------------------------------


def test_stopping_lands_all_three_cells_at_once(store) -> None:
    """结局、产出、停表在**同一次调用**里全都有了。"""
    running = _running(store)
    delta = _delta()

    stopped = store.stop(
        running.run_id, delta=delta, status=RunStatus.COMPLETED, at=STOPPED_AT
    )

    assert stopped.stop_reason is RunStatus.COMPLETED
    assert stopped.stopped_at == STOPPED_AT.isoformat()
    assert stopped.updated_at == STOPPED_AT.isoformat()
    assert stopped.delta is not None
    assert stopped.is_stopped is True


def test_the_delta_reads_back_unchanged(store) -> None:
    """Delta **原样**吐回 —— 存的是它自己的 JSON，不是重算的。

    重算的那一份会在形状或校验器改动之后与当时那一份悄悄分家，而报告上它们都叫
    「这一轮的产出」。
    """
    running = _running(store)
    delta = _delta(routes=("r-1",), evidence=("e-1", "e-2"), usage=("res-1",))
    store.stop(running.run_id, delta=delta, status=RunStatus.COMPLETED, at=STOPPED_AT)

    read_back = store.get(running.run_id)
    assert read_back is not None and read_back.delta is not None
    assert read_back.delta.model_dump_json() == delta.model_dump_json()
    assert read_back.delta.counts == delta.counts


def test_stopping_does_not_move_the_admission_clock(store) -> None:
    """``created_at`` 是**受理**那一刻，``stop`` 不动它。

    一个把两者写成一格的实现，会让「这一轮是什么时候受理的」在一次结束之后变成
    另一个时刻 —— 而报告上它看起来完全正常。
    """
    running = _running(store)
    store.stop(
        running.run_id, delta=_delta(), status=RunStatus.COMPLETED, at=STOPPED_AT
    )

    read_back = store.get(running.run_id)
    assert read_back is not None
    assert read_back.created_at == running.created_at == T0.isoformat()


def test_a_stopped_run_is_not_resurrected_by_re_admission(store) -> None:
    """结束之后再拿同一份规格受理，拿回的是**结束过的那一行**，不是新的一轮。"""
    running = _running(store)
    store.stop(
        running.run_id, delta=_delta(), status=RunStatus.COMPLETED, at=STOPPED_AT
    )

    again = _running(store)
    assert again.run_id == running.run_id
    assert again.stop_reason is RunStatus.COMPLETED
    assert again.stopped_at == STOPPED_AT.isoformat()


# --- 重放与冲突 ---------------------------------------------------------------


def test_replaying_the_same_ending_is_idempotent(store) -> None:
    """同一套 ``(status, delta)`` 再来一次：拿回同一行，``stopped_at`` **不变**。

    判重放的依据是这一套结局与产出，不是这个方法被调过几次 —— 一次网络重试不该
    把这一轮结束的时间改到第二个时刻。``at`` 因此不比。
    """
    running = _running(store)
    delta = _delta()
    first = store.stop(
        running.run_id, delta=delta, status=RunStatus.COMPLETED, at=STOPPED_AT
    )
    second = store.stop(
        running.run_id,
        delta=delta,
        status=RunStatus.COMPLETED,
        at=STOPPED_AT + timedelta(hours=5),
    )

    assert second.stopped_at == first.stopped_at == STOPPED_AT.isoformat()
    assert second.updated_at == first.updated_at


def test_a_different_delta_for_the_same_run_is_refused(store) -> None:
    """**两份对不上的产出争同一个 Run** —— 抛，不覆盖，也不当成重放。

    静默接受第二次，会让先落盘的那一份悄悄输掉，而它输掉这件事**没有任何一处
    记着**。报文里两个结局词都要在。
    """
    running = _running(store)
    store.stop(
        running.run_id, delta=_delta(), status=RunStatus.COMPLETED, at=STOPPED_AT
    )

    with pytest.raises(RunStateError) as caught:
        store.stop(
            running.run_id,
            delta=_delta(routes=("r-9",)),
            status=RunStatus.COMPLETED,
            at=STOPPED_AT,
        )

    assert running.run_id in str(caught.value)
    assert "completed" in str(caught.value)


def test_a_different_status_for_the_same_delta_is_refused(store) -> None:
    """产出一样、结局词不一样也是冲突。

    只比产出的实现会把它当成重放接受 —— 于是「同一轮以两种结局结束过」这件事
    在盘上只剩一个词，而另一个词是从哪来的没有任何答案。
    """
    running = _running(store)
    delta = _delta()
    store.stop(running.run_id, delta=delta, status=RunStatus.PARTIAL, at=STOPPED_AT)

    with pytest.raises(RunStateError):
        store.stop(running.run_id, delta=delta, status=RunStatus.FAILED, at=STOPPED_AT)


def test_stopping_an_unknown_run_is_refused(store) -> None:
    """没有这一轮 —— 抛，而不是**新写一行**。

    一个「找不到就建一行」的实现会让一次写错 id 变成一次真实的结束记录。
    """
    unknown = run_id(WORKSPACE, "run-never-admitted")
    with pytest.raises(RunStateError) as caught:
        store.stop(
            unknown, delta=_delta(), status=RunStatus.COMPLETED, at=STOPPED_AT
        )

    assert unknown in str(caught.value)
    assert store.get(unknown) is None


def test_a_run_that_never_started_is_told_apart_from_one_that_ended(store) -> None:
    """「没有这一轮」与「这一轮已经结束了」是**两句话**。

    合成一句会把一次写错 id 读成一次重放 —— 而前者要人去查是谁给错了 id，后者
    什么都不用做。这里把两句读出来比一次，断言它们不是同一句。
    """
    running = _running(store)
    store.stop(
        running.run_id, delta=_delta(), status=RunStatus.COMPLETED, at=STOPPED_AT
    )

    with pytest.raises(RunStateError) as missing:
        store.stop(
            run_id(WORKSPACE, "nope"), delta=_delta(), status=RunStatus.COMPLETED, at=STOPPED_AT
        )
    with pytest.raises(RunStateError) as conflict:
        store.stop(
            running.run_id,
            delta=_delta(routes=("r-9",)),
            status=RunStatus.COMPLETED,
            at=STOPPED_AT,
        )

    assert str(missing.value) != str(conflict.value)
    assert "没有这一轮" in str(missing.value)
    assert "已经结束了（" in str(conflict.value)
    assert "没有这一轮" not in str(conflict.value)


# --- 结局判据的拒绝 -----------------------------------------------------------


def test_a_completed_run_cannot_be_empty(store) -> None:
    """``COMPLETED`` 配一份每一格都空的 Delta → ``RunSpecError``。

    §B17 条件 2 要求「**合法零新增**仍能以 unchanged/held/blocked 结束」——
    零新增要写下来（那一批处置行就是它的归宿），零**产出**不行：「跑完了什么都没
    碰」与「压根没跑」在回执上必须不同。
    """
    running = _running(store)
    with pytest.raises(RunSpecError) as caught:
        store.stop(
            running.run_id, delta=RunDelta(), status=RunStatus.COMPLETED, at=STOPPED_AT
        )

    assert "completed" in str(caught.value)


def test_a_refused_ending_leaves_the_run_running(store) -> None:
    """被拒的结束**一个字节都不落**：那一行仍然没有结束过。

    一个先写结局再判的实现，会让一个从没跑完的 Run 在次日读起来像一次正常结束。
    """
    running = _running(store)
    with pytest.raises(RunSpecError):
        store.stop(
            running.run_id, delta=RunDelta(), status=RunStatus.COMPLETED, at=STOPPED_AT
        )

    read_back = store.get(running.run_id)
    assert read_back is not None
    assert read_back.stop_reason is None
    assert read_back.delta is None
    assert read_back.stopped_at is None
    assert read_back.is_stopped is False


# --- 条件 2 的后半句，正面 ------------------------------------------------------


@pytest.mark.parametrize("cell", [DeltaCell.UNCHANGED, DeltaCell.HELD, DeltaCell.BLOCKED])
def test_a_zero_gain_run_ends_cleanly(store, cell: DeltaCell) -> None:
    """**「合法零新增仍能以 unchanged/held/blocked 结束」。**

    三种收法各判一次：没有一条 ``discovered`` / ``promoted``，而这一轮确实走完了
    —— ``COMPLETED`` 是允许的，因为每一行都有归宿。
    """
    running = _running(store)
    extra = "来源那边 502" if cell is not DeltaCell.UNCHANGED else ""
    delta = RunDelta(
        effects=(PoolEffect(cell=cell, key="v-1", label="Devon 球场", why=extra),)
    )

    stopped = store.stop(
        running.run_id, delta=delta, status=RunStatus.COMPLETED, at=STOPPED_AT
    )

    assert stopped.delta is not None
    assert stopped.delta.counts[DeltaCell.DISCOVERED] == 0
    assert stopped.delta.counts[DeltaCell.PROMOTED] == 0
    assert stopped.delta.counts[cell] == 1


def test_a_run_that_never_got_going_may_stop_with_nothing(store) -> None:
    """``FAILED`` 可以是空的 —— 一轮在跑起来之前就断掉，产出确实是零。"""
    running = _running(store)
    stopped = store.stop(
        running.run_id, delta=RunDelta(), status=RunStatus.FAILED, at=STOPPED_AT
    )

    assert stopped.stop_reason is RunStatus.FAILED
    assert stopped.delta is not None and stopped.delta.is_empty is True


# --- 两个实现是同一句话 --------------------------------------------------------


def test_the_two_implementations_speak_the_same_words(tmp_path: Path) -> None:
    """**本文件最有分量的一条。** 两边把同一件事说成同一句话。

    抄进实现里的话，两份会各自漂移，而漂移的形态是其中一份把「没有这一轮」说成
    「这一轮已经结束了」—— 那会把一次写错 id 读成一次重放。一条只断言「抛了
    ``RunStateError``」的判据在那天是绿的。
    """
    sqlite = SqliteRunStore(tmp_path / "state" / "marketops.db")
    memory = InMemoryRunStore()

    for store in (sqlite, memory):
        running = _running(store)
        store.stop(
            running.run_id, delta=_delta(), status=RunStatus.COMPLETED, at=STOPPED_AT
        )

    cases = {
        "找不到": lambda s: s.stop(
            run_id(WORKSPACE, "nope"),
            delta=_delta(),
            status=RunStatus.COMPLETED,
            at=STOPPED_AT,
        ),
        "产出不同": lambda s: s.stop(
            run_id(WORKSPACE, KEY),
            delta=_delta(routes=("r-9",)),
            status=RunStatus.COMPLETED,
            at=STOPPED_AT,
        ),
        "结局不同": lambda s: s.stop(
            run_id(WORKSPACE, KEY),
            delta=_delta(),
            status=RunStatus.PARTIAL,
            at=STOPPED_AT,
        ),
    }
    for name, call in cases.items():
        with pytest.raises(RunStateError) as left:
            call(sqlite)
        with pytest.raises(RunStateError) as right:
            call(memory)
        assert str(left.value) == str(right.value), name


# --- 盘上的那条约束（存储层的另一半） ------------------------------------------


def _next_rid(counter: "itertools.count[int]") -> str:
    """每次调用一个新的 32 位十六进制 id（形状与 ``domain/run.py::run_id`` 同一格）。"""
    return f"{next(counter):032x}"


def _raw_insert(
    conn: sqlite3.Connection, rid: str, *, stop: object, delta: object, at: object
) -> None:
    """一条绕过端口的 ``INSERT``，**不改行就改 id**（主键唯一，重复 id 会先撞主键）。

    每一次调用的 ``rid`` 必须不同，否则测的就不是要测的那条约束 —— 撞主键也是
    ``IntegrityError``，与「三格配对」撞出来的那一种长得一样。
    """
    conn.execute(
        "INSERT INTO research_run (run_id, workspace, spec_sha256, spec, stop_reason, "
        "delta, stopped_at, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (rid, WORKSPACE, "f" * 64, "{}", stop, delta, at, T0.isoformat(), T0.isoformat()),
    )


def test_the_table_refuses_a_half_finished_run(tmp_path: Path) -> None:
    """**盘上**那条 ``CHECK``：三格同时有或同时无。

    这一条判的是 ``Run.__post_init__`` 管不到的那一半 —— 任何**别的进程**直接写
    这个库的路径。所以它用一条真的 ``INSERT``，不是从源码里读一遍约束文本：
    「这条约束存在」与「这条约束真的在拦」是两件事。
    """
    store = SqliteRunStore(tmp_path / "state" / "marketops.db")  # 只为建表
    assert isinstance(store, SqliteRunStore)
    conn = sqlite3.connect(tmp_path / "state" / "marketops.db")
    counter = itertools.count()
    try:
        # 三者全无：受理那一刻的正常形状。
        _raw_insert(conn, _next_rid(counter), stop=None, delta=None, at=None)
        # 三者齐全：一次正常结束。
        _raw_insert(
            conn,
            _next_rid(counter),
            stop="completed",
            delta=RunDelta(routes=("r-1",)).model_dump_json(),
            at=STOPPED_AT.isoformat(),
        )
        for stop, delta, at in [
            ("completed", None, None),  # 有结局、没产出：那个「产出丢了」的窗口
            (None, "{}", None),  # 有产出、没结局
            (None, None, STOPPED_AT.isoformat()),  # 只有停表
            ("completed", "{}", None),  # 有产出，但停表没写
            ("看了又看", "{}", STOPPED_AT.isoformat()),  # 词表外的结局词
        ]:
            with pytest.raises(sqlite3.IntegrityError):
                _raw_insert(conn, _next_rid(counter), stop=stop, delta=delta, at=at)
    finally:
        conn.close()


def test_the_run_ids_come_from_one_place() -> None:
    """``admit`` 与 ``stop`` 收的是**同一个** id 空间里的 id。

    两个端口上的 ``run_id`` 参数若来自两处推导，它们会各自漂移 —— 而漂移的表现是
    「这一轮受理了但停不下来」。这里断言本模块用到的 id 就是 ``domain/run.py`` 那条
    推导，不是别处那个同名的记账标签（``_derived_run_id()``，形状 ``run-<时刻>``）。
    """
    rid = run_id(WORKSPACE, KEY)

    assert len(rid) == 32
    assert rid != f"run-{KEY}"
    assert _spec().idempotency_key == KEY
