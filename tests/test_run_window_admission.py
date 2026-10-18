"""§B17 条件 4 的判据 —— 一条授权链走到 stop 之后**不创建下一轮**。

条件 4 逐字：「``bounded_auto`` 在达到对象数/累计预算/截止时间**任一** stop 后不创建
下一 Run；重放 trigger 不重复创建。」

## 这一族判的是「打开之后装上限」，不是「打开」

切片 e1 造出了 ``AuthorizationWindow`` 的形状，而受理面对**每一个**非 ``manual`` 模式
都拒（逐字：「窗口有形状了，但还没有一处读过它」）。切片 e2 打开了受理，所以这一族必
须一起判两件事 —— 只有「打开了」而判据只判「不再拒」，盘上就会多出一轮又一轮什么都
齐全、却没有任何一处问过它累计走到哪了的自动 Run：

* **打开**：带窗口的 ``bounded_auto`` 在一条空链上真的受理得下来；
* **上限**：这条链走到任一条 stop 之后，下一轮开不出来。

两条判据在同一个文件里，是因为**它们之间那个状态是不该存在的**：一个只判前者的实现
在「永远受理」时也是绿的，一个只判后者的实现在「永远拒」时也是绿的，而两者都不是 e2。

## 三条 stop 里只有两条在这里判 —— 第三条在域层

条件 4 写的是三个 stop，而 ``window_refusal`` 里只有对象数与累计预算。**截止时间那一条
已经由域层承担**：``RunSpec._window_agrees_with_the_rest`` 拒绝 ``not_after < cutoff``，
``admission_refusal`` 拒绝 ``cutoff <= at``，两条合起来就是 ``at < not_after``。所以
窗口到期这件事在受理面上有一条**蕴含**而不是一条分支，而
:func:`test_an_admitted_auto_run_always_starts_before_its_window_ends` 钉的正是那一条
蕴含。在 ``window_refusal`` 里再写一遍是一条永远不触发的分支 —— 这一族不替它开绿灯，
也不让它假装活着。

## 两个实现，同一份判据

``chain_progress`` 与 ``window_refusal`` 都住在 ``ports/runs.py``，两个实现都调它们。
这一族里凡是拿得出两边的，都在 :func:`store` 那个 fixture 上各跑一遍 —— 分家的形态是
其中一个**静默地多开一轮**，而它在报告上只是「这一批比预计的多跑了一轮」。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from marketops.adapters.memory import InMemoryRunStore
from marketops.adapters.sqlite.runs import SqliteRunStore
from marketops.domain.enums import BudgetLine, ExecutionMode, RunStatus
from marketops.domain.errors import RunSpecError
from marketops.domain.run import (
    AuthorizationWindow,
    Run,
    RunBudgetLine,
    RunSpec,
    run_id,
    spec_fingerprint,
)
from marketops.domain.run_delta import DeltaCell, PoolEffect, RunDelta
from marketops.ports.runs import ChainProgress, progress_of, window_refusal

WORKSPACE = "uk-acme-natural-grass"
SCENE = "scene-devon"
KEY = "run-2026-09-28-w"
KEY2 = "run-2026-09-28-w2"
T0 = datetime(2026, 9, 28, 9, 0, tzinfo=timezone.utc)
CUTOFF = T0 + timedelta(days=1)
WINDOW_UNTIL = T0 + timedelta(days=30)
STOPPED_AT = T0 + timedelta(hours=2)


# --- 构造器 -------------------------------------------------------------------


def _window(
    *,
    max_objects: int = 50,
    ceiling: int = 100,
    not_after: datetime = WINDOW_UNTIL,
) -> AuthorizationWindow:
    """一条授权链。**每一条都是一个内容摘要**（``AuthorizationWindow.digest``），
    所以改一个阈值就是**另一条链** —— 这一族里好几条判据靠的就是这个差别。"""
    return AuthorizationWindow(
        max_objects=max_objects,
        budget=(RunBudgetLine(line=BudgetLine.SEARCH, ceiling=ceiling),),
        not_after=not_after,
    )


def _spec(**overrides: object) -> RunSpec:
    payload: dict[str, object] = {
        "workspace": WORKSPACE,
        "objective": "补一轮 Devon 的场地",
        "selector": SCENE,
        "context_ref": "a" * 64,
        "plan_ref": "b" * 64,
        "execution_mode": ExecutionMode.BOUNDED_AUTO,
        "window": _window(),
        "budget": (RunBudgetLine(line=BudgetLine.SEARCH, ceiling=20),),
        "cutoff": CUTOFF,
        "policy_versions": ("grading@1f2e3d",),
        "idempotency_key": KEY,
    }
    payload.update(overrides)
    return RunSpec(**payload)  # type: ignore[arg-type]


def _delta(*, keys: tuple[str, ...] = ("v-1",), usage: tuple[str, ...] = ()) -> RunDelta:
    """一份有内容的 Delta。``usage`` 是**预留 id**，不是金额（见 ``RunDelta``）。"""
    return RunDelta(
        effects=tuple(
            PoolEffect(cell=DeltaCell.DISCOVERED, key=key, label=f"场地 {key}")
            for key in keys
        ),
        usage=usage,
    )


def _admit(store, spec: RunSpec, *, at: datetime = T0, spent=None):
    return store.admit(spec, spec_sha256=spec_fingerprint(spec), at=at, spent=spent)


def _stop(store, run: Run, *, delta: RunDelta | None = None, at: datetime = STOPPED_AT):
    return store.stop(
        run.run_id, delta=delta or _delta(), status=RunStatus.COMPLETED, at=at
    )


def _run(
    spec: RunSpec,
    *,
    stopped_with: RunDelta | None = None,
    rid: str | None = None,
) -> Run:
    """一个**已经不在存储里**的 Run 值 —— 给 ``progress_of`` 的纯函数判据用。

    不经存储造它，是因为 ``progress_of`` 要判的那几条筛选（工作区、窗口摘要）在
    一个 store 上**造不出来**：``bind_workspace`` 拒绝第二个工作区（那正是它存在的
    理由）。判据的输入是一批 ``Run``，那就直接构造一批。
    """
    return Run(
        run_id=rid or run_id(spec.workspace, spec.idempotency_key),
        spec=spec,
        spec_sha256=spec_fingerprint(spec),
        created_at=T0.isoformat(),
        updated_at=T0.isoformat(),
        stop_reason=None if stopped_with is None else RunStatus.COMPLETED,
        delta=stopped_with,
        stopped_at=None if stopped_with is None else STOPPED_AT.isoformat(),
    )


@pytest.fixture(params=["sqlite", "memory"])
def store(request: pytest.FixtureRequest, tmp_path: Path):
    """两个实现之一。名字进测试 id —— 红了的时候一眼看得见是哪一个。"""
    if request.param == "sqlite":
        return SqliteRunStore(tmp_path / "state" / "marketops.db")
    return InMemoryRunStore()


@pytest.fixture
def both(tmp_path: Path) -> list:
    """两个实现**同时**在场，喂同一批行。用于逐字段比对那一条。"""
    return [SqliteRunStore(tmp_path / "state" / "marketops.db"), InMemoryRunStore()]


# --- 1 · `progress_of`：一条链走到哪了（纯函数，不碰存储） ---------------------


def test_an_empty_chain_reads_as_zero() -> None:
    """没有这一条链时是**零值**，不是 ``None``。

    一个 ``None`` 会让每个调用点各判一次「有没有」，而那些判断里漏掉的那一处会把
    「这条链第一次受理」读成「读不到」—— 而两者处置相反（一个可以继续，一个要去查
    存储）。零值让「第一次」与「读到了但什么都没跑」是同一句话，而那正是事实。
    """
    progress = progress_of(WORKSPACE, _window().digest, [])

    assert progress == ChainProgress(
        window_digest=_window().digest, runs=0, open_runs=0, objects=0, reservations=()
    )


def test_only_the_named_chain_is_counted() -> None:
    """另一条窗口摘要的轮**一个都不算**。

    窗口的摘要是这条链的身份。把它读松（比如只按工作区数），同一份授权下的两批
    自动 Run 会互相吃掉对方的额度 —— 而报告上两边都只是「比预计的少跑了几轮」。
    """
    mine = _run(_spec(window=_window(max_objects=3)), stopped_with=_delta(keys=("a",)))
    other = _run(
        _spec(window=_window(max_objects=9), idempotency_key=KEY2),
        stopped_with=_delta(keys=("b", "c", "d")),
    )

    progress = progress_of(WORKSPACE, _window(max_objects=3).digest, [mine, other])

    assert progress.runs == 1
    assert progress.objects == 1


def test_only_the_named_workspace_is_counted() -> None:
    """另一个工作区的行不算 —— 窗口的摘要没有工作区在里面，两个部署可以各有一条
    阈值相同的链。"""
    here = _run(_spec(window=_window(max_objects=3)), stopped_with=_delta(keys=("a",)))
    there = _run(
        _spec(workspace="uk-acme-other", window=_window(max_objects=3)),
        stopped_with=_delta(keys=("b",)),
    )

    progress = progress_of(WORKSPACE, _window(max_objects=3).digest, [here, there])

    assert progress.runs == 1
    assert progress.objects == 1


def test_a_running_run_is_open_and_contributes_nothing_yet() -> None:
    """没结束的那一轮进 ``open_runs``，而它的对象**还没进** ``objects``。

    两件事分开是因为它们的处置相反：``open_runs`` 说的是「等它」（那条链上还有一轮
    在飞），``objects`` 说的是「这条链已经覆盖了多广」。把在飞的那一轮也算进对象数，
    会让这条链在**它自己那轮还在跑**的时候就被判成到了上限。
    """
    running = _run(_spec(window=_window(max_objects=3)))
    done = _run(
        _spec(window=_window(max_objects=3), idempotency_key=KEY2),
        stopped_with=_delta(keys=("a",)),
    )

    progress = progress_of(WORKSPACE, _window(max_objects=3).digest, [running, done])

    assert progress.open_runs == 1
    assert progress.objects == 1
    # ``runs`` 数的是**已经结束**的轮（``ChainProgress`` 的字段注）。两格分开而不是
    # 一格「这条链上有几轮」：受理面要问的是「还有没有在飞的」，而那个数一旦含上
    # 在飞的，它就恒非零、于是恒拒。
    assert progress.runs == 1


def test_objects_are_deduplicated_across_runs() -> None:
    """同一个对象在两轮里各被处置一次，仍然是**一个**对象。

    口径是「这条链覆盖了多广」，而重复走同一批对象是**成本**问题 —— 那一条归窗口的
    ``budget`` 管。不去重的后果是一条维护链（每轮都走同一批对象）永远到不了上限，
    而它恰恰是最需要上限的那一种。
    """
    first = _run(_spec(window=_window(max_objects=3)), stopped_with=_delta(keys=("a", "b")))
    second = _run(
        _spec(window=_window(max_objects=3), idempotency_key=KEY2),
        stopped_with=_delta(keys=("b", "c")),
    )

    progress = progress_of(WORKSPACE, _window(max_objects=3).digest, [first, second])

    assert progress.objects == 3, "去重后的键是 {a, b, c}"


def test_every_cell_counts_not_only_the_new_ones() -> None:
    """**九格都算**，不只是「新增」那一格 —— 这一条是本族里最承重的一条。

    只数新增的口径下，一条**每轮全部 unchanged** 的维护链的对象数恒为 0，
    于是「达到对象数就停」对它永远不成立 —— 而它正是最需要那条上限的链：它每一轮
    都在做实事（每一格都是一次处置），只是没有哪一件是新的。
    """
    maintenance = _run(
        _spec(window=_window(max_objects=3)),
        stopped_with=RunDelta(
            effects=(
                PoolEffect(cell=DeltaCell.UNCHANGED, key="v-1", label="场地 v-1"),
                PoolEffect(
                    cell=DeltaCell.HELD,
                    key="v-2",
                    label="场地 v-2",
                    why="等来源那边确认草坪类型",
                ),
            )
        ),
    )

    progress = progress_of(WORKSPACE, _window(max_objects=3).digest, [maintenance])

    assert progress.objects == 2


def test_reservations_come_only_from_stopped_runs() -> None:
    """没结束的那一轮上还没有 ``usage`` —— 它的预留记在它自己的 Delta 里，而那份
    Delta 还没写下来过。"""
    running = _run(_spec(window=_window(max_objects=3)))
    done = _run(
        _spec(window=_window(max_objects=3), idempotency_key=KEY2),
        stopped_with=_delta(usage=("r-2", "r-1")),
    )

    progress = progress_of(WORKSPACE, _window(max_objects=3).digest, [running, done])

    assert progress.reservations == ("r-1", "r-2"), "排序 —— 读它的人不该依赖输入次序"


# --- 2 · `chain_progress`：两个实现逐字段相同 ---------------------------------


def test_the_two_implementations_agree_field_by_field(both) -> None:
    """同一批行喂进两个实现，``chain_progress`` 的**每一个字段**都必须相同。

    判据为什么要有这一条：SQLite 那份真的多写了一次 ``WHERE workspace = ?``（为了
    走 ``research_run_by_workspace`` 那条索引），而那一句是**多余的一次筛选**。两个
    实现的筛选一旦分家，形态是受理面拿一份**少算了的累计**去判「到没到 stop」——
    它不抛异常、不留日志，只是多开了一轮。
    """
    window = _window(max_objects=4)
    for one in both:
        first = _admit(one, _spec(window=window), spent={BudgetLine.SEARCH: 0})
        _stop(one, first, delta=_delta(keys=("a", "b"), usage=("r-1",)))
        second = _admit(
            one, _spec(window=window, idempotency_key=KEY2), spent={BudgetLine.SEARCH: 0}
        )
        _stop(one, second, delta=_delta(keys=("b", "c"), usage=("r-2",)))

    readings = [one.chain_progress(workspace=WORKSPACE, window_digest=window.digest) for one in both]

    assert readings[0] == readings[1]
    assert readings[0].runs == 2
    assert readings[0].objects == 3
    assert readings[0].open_runs == 0
    assert readings[0].reservations == ("r-1", "r-2")


def test_chain_progress_on_an_empty_store_is_zero(store) -> None:
    """**一个还没受理过任何东西的 store** 答的是零值，不抛。

    这一条与上面那条问的不是同一件事：上面比的是「两个实现一致」，这里比的是
    「一个空的存储也答得出来」—— 而它正是第一次受理时会拿到的那个答案。
    """
    progress = store.chain_progress(workspace=WORKSPACE, window_digest=_window().digest)

    assert progress.runs == 0
    assert progress.objects == 0
    assert progress.open_runs == 0


# --- 3 · 打开：带窗口的自动模式真的受理得下来 ---------------------------------


def test_a_bounded_auto_with_a_window_is_admitted(store) -> None:
    """**打开**这一半。缺了它，本族所有「拒绝」的判据在「永远拒」时也全绿 ——
    而「永远拒」不是 e2 的状态，它是 e1 的状态。"""
    admitted = _admit(store, _spec(window=_window()), spent={BudgetLine.SEARCH: 0})

    assert admitted.spec.execution_mode is ExecutionMode.BOUNDED_AUTO
    assert admitted.spec.window == _window()
    assert admitted.stop_reason is None
    assert store.chain_progress(
        workspace=WORKSPACE, window_digest=_window().digest
    ).open_runs == 1


# --- 4 · 上限：三条 stop 里的两条（第三条在域层，见文件头） -------------------


def test_no_next_run_while_one_is_still_open(store) -> None:
    """这条链上还有一轮没结束 ⇒ 下一轮开不出来。

    依据是 §B17 的「创建**后续** Run」与「一轮一轮长大」：后续是「上一轮之后」，
    不是「同时」。放它过去，这条链的累计在两次受理里各读到一个**还没算上对方**的
    数，于是两个都过、合起来超 —— 而那正是「上限」这一格要防的事。
    """
    _admit(store, _spec(window=_window()), spent={BudgetLine.SEARCH: 0})

    with pytest.raises(RunSpecError) as caught:
        _admit(store, _spec(window=_window(), idempotency_key=KEY2), spent={BudgetLine.SEARCH: 0})

    assert "还有 1 轮没有结束" in str(caught.value)


def test_the_object_count_is_the_second_stop(store) -> None:
    """对象数走到窗口上限 ⇒ 下一轮开不出来；**差一个**则开得出来。"""
    window = _window(max_objects=2)
    first = _admit(store, _spec(window=window), spent={BudgetLine.SEARCH: 0})
    _stop(store, first, delta=_delta(keys=("a", "b")))

    with pytest.raises(RunSpecError) as caught:
        _admit(store, _spec(window=window, idempotency_key=KEY2), spent={BudgetLine.SEARCH: 0})

    assert "2 个对象" in str(caught.value)
    assert "达到了它的授权窗口" in str(caught.value)


def test_the_object_count_just_below_the_ceiling_still_admits(store) -> None:
    """**边界**：已处置 2 个、窗口是 3 个 ⇒ 受理。

    缺了这条，一个把判据写成 ``>`` 的实现（走 3 个对象时才拒）在本族里是绿的 ——
    而它让每一条链都多走一个对象。与上面那条成对：一条判「到了就拒」，一条判
    「没到就放」。
    """
    window = _window(max_objects=3)
    first = _admit(store, _spec(window=window), spent={BudgetLine.SEARCH: 0})
    _stop(store, first, delta=_delta(keys=("a", "b")))

    admitted = _admit(
        store, _spec(window=window, idempotency_key=KEY2), spent={BudgetLine.SEARCH: 0}
    )

    assert admitted.spec.window == window


def test_the_accumulated_budget_is_the_third_stop(store) -> None:
    """累计预算走到窗口上限 ⇒ 拒；**差一个**则受理。

    读数是 ``spent`` 这个映射 —— 它由调用方拿链上各轮的预留 id 去账本读回来
    （``Run 记录读不到金额``）。这一条判的是「受理面拿它判」，不是「怎么读回来的」：
    读法那一条在 ``tests/test_run_admission.py`` 里端到端判。
    """
    window = _window(max_objects=50, ceiling=10)
    envelope = (RunBudgetLine(line=BudgetLine.SEARCH, ceiling=10),)
    first = _admit(
        store, _spec(window=window, budget=envelope), spent={BudgetLine.SEARCH: 0}
    )
    _stop(store, first, delta=_delta(keys=("a",)))

    with pytest.raises(RunSpecError) as caught:
        _admit(
            store,
            _spec(window=window, budget=envelope, idempotency_key=KEY2),
            spent={BudgetLine.SEARCH: 10},
        )
    message = str(caught.value)
    assert "累计预算走到了窗口上限" in message
    # 拒词里带**两个读数**：窗口的上限与账本读到的累计。只带一个的话，读的人没法
    # 判断「超了多少」—— 而这一格的处置（再要一次授权 vs 调大窗口）正是由它决定的。
    assert BudgetLine.SEARCH.value in message
    assert "已 10、窗口 10" in message

    admitted = _admit(
        store,
        _spec(window=window, budget=envelope, idempotency_key=KEY2),
        spent={BudgetLine.SEARCH: 9},
    )
    assert admitted.spec.window == window


def test_a_missing_ledger_reading_is_refused_not_skipped(store) -> None:
    """``spent is None`` ⇒ **拒**，不是「这一格跳过」。

    少了这一条，「没有账本读数」与「账本说这条链还没花过钱」在受理面上同形 ——
    而两者处置相反（一个去接账本，一个可以继续走）。这与 ``RunDelta`` 那句
    「算不出来就抛，不返回 0」是同一条纪律，只是主语换成了这一格。
    """
    with pytest.raises(RunSpecError) as caught:
        _admit(store, _spec(window=_window()))

    assert "累计预算读不到" in str(caught.value)


def test_a_reading_from_another_chain_is_refused() -> None:
    """``progress`` 的窗口摘要与规格的不等 ⇒ 拒。

    这一条**只在纯函数上判得出来**：存储那两个实现自己按规格的摘要去读，读不错。
    所以它钉的是**调用点**——而这正是它存在的理由：一份读错了链的读数会给出一个
    看起来完全正常的数（轮数、对象数都有），于是这一条链会拿着另一条链的累计去判。
    """
    stranger = progress_of(WORKSPACE, _window(max_objects=99).digest, [])
    assert stranger.objects == 0, "前提：这份读数本身是合法的，只是属于另一条链"

    refusal = window_refusal(_spec(window=_window()), progress=stranger, spent={})

    assert "另一条链" in refusal
    assert _window().digest in refusal
    assert _window(max_objects=99).digest in refusal


def test_the_window_refusal_is_verbatim_the_ports_judgement(store) -> None:
    """受理面拒下来的理由与 ``ports.runs.window_refusal`` **逐字相同**。

    与 ``tests/test_run_admission.py::test_the_refusal_is_verbatim_the_ports_judgement``
    是一对：那一条比的是 ``admission_refusal``，这一条比的是 ``window_refusal``。两条都
    要有 —— 只比一条的话，另一条判据被抄进实现里（于是开始漂移）时没有任何一处会红。

    **名字带 ``window_``**，不是与那条同名：两条判据判的是两个不同的函数，同名会让
    一份红名单读不出红的是哪一条（而这一族存在的理由正是「红名单要说得清是哪一条」）。

    **逐字比而不是「两边都非空」**：漂移的那一天，两边各自都还是一句像样的话。
    """
    window = _window(max_objects=1)
    first = _admit(store, _spec(window=window), spent={BudgetLine.SEARCH: 0})
    _stop(store, first, delta=_delta(keys=("a",)))

    spec = _spec(window=window, idempotency_key=KEY2)
    expected = window_refusal(
        spec,
        progress=store.chain_progress(workspace=WORKSPACE, window_digest=window.digest),
        spent={BudgetLine.SEARCH: 0},
    )
    assert expected, "这条判据的前提是这条链确实到了 stop"

    with pytest.raises(RunSpecError) as caught:
        _admit(store, spec, spent={BudgetLine.SEARCH: 0})

    assert str(caught.value) == expected


# --- 5 · 次序：重放**不问窗口** -----------------------------------------------


def test_a_replay_returns_the_row_even_after_the_chain_stopped(store) -> None:
    """一条链走到 stop 之后，**同一份规格的重发照样拿回那一行**。

    这是 ``RunStore.admit`` 里那条次序（重放检查在窗口判定**之前**）唯一的判据。
    反过来写的形态是：调用方把一个**已经被受理过**的请求读成「这次没被受理」——
    而盘上那一行还在，于是同一次提交在两边是两个答案。

    重发的承诺是「拿回同一行」（那一格的 docstring 逐字），而链的累计是**会变**的；
    让一个会变的东西决定一句不变的承诺，就是把幂等交给出货顺序。
    """
    window = _window(max_objects=1)
    original = _admit(store, _spec(window=window), spent={BudgetLine.SEARCH: 0})
    _stop(store, original, delta=_delta(keys=("a",)))
    # 前提到此为止：这条链已经到了一条 stop（下面第一句断言它）。
    with pytest.raises(RunSpecError):
        _admit(store, _spec(window=window, idempotency_key=KEY2), spent={BudgetLine.SEARCH: 0})

    replayed = _admit(store, _spec(window=window), spent={BudgetLine.SEARCH: 0})

    assert replayed.run_id == original.run_id
    assert replayed.spec_sha256 == original.spec_sha256
    assert replayed.created_at == original.created_at
    assert replayed.stop_reason is RunStatus.COMPLETED, "重放没有把它改成「还没结束」"


def test_a_replay_does_not_create_a_second_run(store) -> None:
    """「重放 trigger 不重复创建」—— 条件 4 的后半句，**计数**上的形态。

    上面那条判的是「拿回同一行」，这一条判的是「盘上没有第二行」：两件事分开，因为
    一个「返回了旧行、却又插了一行」的实现在前一条上是绿的。
    """
    window = _window(max_objects=1)
    _admit(store, _spec(window=window), spent={BudgetLine.SEARCH: 0})
    for _ in range(3):
        _admit(store, _spec(window=window), spent={BudgetLine.SEARCH: 0})

    progress = store.chain_progress(workspace=WORKSPACE, window_digest=window.digest)

    # 三次重放之后：**零条已结束**（没停过表）、**一条在飞**。``runs`` 与 ``open_runs``
    # 在这里分开判，正是因为它们答的不是同一个问题 —— 拿 ``runs`` 当「盘上有几行」读
    # 的人会在这里得到 0，然后去开第四轮。
    assert progress.runs == 0
    assert progress.open_runs == 1


def test_a_refused_auto_admission_writes_nothing(store) -> None:
    """被拒的那一次**一个字节都不落** —— 不是「写下了但标记为拒绝」。

    一个先落盘再判的实现，会让这条链的累计在**每一次被拒之后**都变大一圈：多出来的
    那一行没有跑过，却占着 ``open_runs``（于是后面每一轮都被拒）—— 而报告上它读起来
    只是「这条链一直在忙」。
    """
    window = _window(max_objects=1)
    first = _admit(store, _spec(window=window), spent={BudgetLine.SEARCH: 0})
    _stop(store, first, delta=_delta(keys=("a",)))

    with pytest.raises(RunSpecError):
        _admit(store, _spec(window=window, idempotency_key=KEY2), spent={BudgetLine.SEARCH: 0})

    progress = store.chain_progress(workspace=WORKSPACE, window_digest=window.digest)
    assert progress.runs == 1
    assert progress.objects == 1


# --- 6 · 第三个阈值（截止时间）：一条**蕴含**，不是一条分支 -------------------


def test_an_admitted_auto_run_always_starts_before_its_window_ends() -> None:
    """条件 4 的「截止时间」那一项由**域层**承担，而这里钉住那条蕴含。

    两条已经存在的判定把它串起来：

    * ``RunSpec._window_agrees_with_the_rest`` 拒绝 ``window.not_after < cutoff``；
    * ``admission_refusal`` 拒绝 ``cutoff <= at``。

    合起来是 ``at < cutoff <= not_after``，即 ``at < not_after``：**任何一份走得通受理面
    的规格，它的窗口都还没到期。** 所以在 ``window_refusal`` 里再写一句
    ``window.not_after < at`` 是一条**永远不触发**的分支 —— 同一句话的第二份实现，
    而这一份连红都不会红。

    ## 这一条判的是三件事，缺一不可

    1. 一条**真的被受理**的自动 Run，它的 ``not_after`` 恒在受理时刻之后；
    2. 一条 ``cutoff`` 超出窗口的规格**构造都构造不出来**（不是「受理时被拒」）；
    3. 一条 ``cutoff`` 不在将来的规格由 ``admission_refusal`` 拒，而**拒词里提到的是
       那个截止**，不是窗口 —— 窗口那一层的判据（``window_refusal``）根本没被问到。

    第 3 条是这一条判据的**关键**：它同时说明了「窗口到期之后开不出下一轮」这件事在
    盘上是成立的，以及它不是由 ``window_refusal`` 答的。少了它，上面两条只在
    「两条校验器都在」时绿，而「窗口到期该拒谁」这句话没有执行器。
    """
    window = _window(max_objects=50, not_after=T0 + timedelta(days=3))
    admitted = _admit(InMemoryRunStore(), _spec(window=window), spent={BudgetLine.SEARCH: 0})
    assert admitted.spec.window is not None
    assert T0 < admitted.spec.window.not_after

    with pytest.raises(ValueError, match="超出了它的授权窗口"):
        _spec(window=window, cutoff=T0 + timedelta(days=4))

    late = _spec(window=window, cutoff=window.not_after)
    store = InMemoryRunStore()
    with pytest.raises(RunSpecError) as caught:
        _admit(store, late, at=window.not_after + timedelta(seconds=1), spent={})
    assert "不在将来" in str(caught.value)
    assert "授权窗口" not in str(caught.value), "这一句不是窗口那一条判据说的"


def test_the_window_refusal_takes_no_clock() -> None:
    """``window_refusal`` **不收 ``at``** —— 这一条是签名上的判据。

    收下一个时刻却不比它，会让下一个读的人以为「窗口到期是这里判的」，而那句话的
    出处其实在域层那条跨格校验器。一句话写在 docstring 里没有执行器，写在签名里才有。
    """
    import inspect

    assert "at" not in inspect.signature(window_refusal).parameters
