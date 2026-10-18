"""§B17 条件 7 的负例 —— 并发写的两个写者、迟到的旧写、以及一次 CAS 的等价物。

条件 7 逐字（``docs/MEMBER_A_AUTOMATION_EXECUTION_PLAN.md:262``）：

> RunDelta 合并对并发/重放/CAS有负例；旧 session 不能用旧 snapshot 覆盖新事实。

## 这一族判的是**并发**那一半，重放那一半不重写

重放那一半盘上已经齐了，逐条住在别处，这一族一个字都不重抄：

* ``tests/test_run_ending_contract.py``（``:165`` 重放同一套结局拿回同一行、``:187``
  第二份不同产出 ⇒ 拒、``:210`` 产出同结局词不同 ⇒ 拒、``:224`` 指不到 Run ⇒ 拒、
  ``:239`` 没开始过与已结束分得开、``:287`` 被拒的结束一个字节都不落）；
* ``tests/test_run_contract.py``（``:125`` 同一个键拿回同一个 Run、``:148`` 同键不同
  规格 ⇒ 冲突且旧行不被改）；
* ``tests/test_run_reconciliation.py``（``:518`` 账不平不结束、``:602`` 结束两次拿回同
  一行）；
* ``tests/test_run_fold_ledger.py``（``:315`` 同一个回执行折两次 ⇒ 一笔、``:335`` 同一
  个回执行折进另一个句柄 ⇒ 拒、``:355`` 按落笔次序读回来、``:412`` 两个 Run 折同一块
  场地 ⇒ 各记各的）。

**并发那一半盘上已经有一条**，也在上面那个文件里：
``tests/test_run_fold_ledger.py::test_two_connections_folding_the_same_receipt_row_write_one_entry``
（``:370``）—— 两个线程同时折同一行 ⇒ 一笔账、一行池子。这一族**不重写它**，只在下面
留一处交叉引用：折入那一侧的原子性由它钉住，本族钉的是**受理面**与**结束面**。

## 只跑 ``SqliteRunStore``：内存替身自己声明了它没有并发

``marketops/adapters/memory.py:2602-2606`` 逐字：

> 这里多一句：这个替身里没有并发，所以次序在这里不挡任何东西 —— 它被照着写，是因为
> 契约测试拿两个实现的**报文**逐字对比：次序一分家，同一份规格在两边会得到**不同的
> 拒绝理由**。

所以这一族不参数化后端：**判据的形状是「两个写入者争同一处盘上状态」**，而在一个没有
锁的进程里那件事靠 Python 对象根本造不出来 —— 一个跑在内存替身上的版本会在「实现没有
并发保护」时照样绿，也就是会**朝一切正常撒谎**。这与
``test_run_fold_ledger.py::test_two_connections_folding_the_same_receipt_row_write_one_entry``
刻意不用双后端夹具是同一条理由。

**每个 worker 拿同一批 store 句柄**（不是各建一个）：这些实现本身无状态、状态全在库文件
里（``tests/test_budget_ledger.py:37-59`` 那份先例是「每个线程建自己的实例」，差别在于
那一族判的是**账本**、而这里判的是**同一批写路径**；两种形状都在造同一个局面：没有共享
的 Python 对象，只有共享的文件）。

## 红的形态是什么

这一族的全部价值在于：**把每一条判据配上一个能让它红的实现改动**。本片要做的那一条是
把那一批行挪到 ``BEGIN IMMEDIATE`` **之前**读（``adapters/sqlite/runs.py:246-273`` 那段
注释逐字写了理由）—— 它一挪，:func:`test_only_one_auto_run_can_open_this_chain_at_once`
就会看到八个成功。「只换一条连接读」不够：那种形状实测**是绿的**，理由逐字写在那一
条的 docstring 里。
"""

from __future__ import annotations

import contextlib
import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from marketops.adapters.sqlite.pool import SqliteVenueAssetStore
from marketops.adapters.sqlite.runs import SqliteRunStore
from marketops.application.pool import merge_assets
from marketops.domain.entities import VenueAsset
from marketops.domain.enums import BudgetLine, DomainVerdict, ExecutionMode, RunStatus
from marketops.domain.errors import (
    AssetConflictError,
    IdempotencyConflictError,
    RunSpecError,
    RunStateError,
    StoreError,
)
from marketops.domain.fold import FoldAttribution
from marketops.domain.run import (
    AuthorizationWindow,
    Run,
    RunBudgetLine,
    RunSpec,
    run_id,
    spec_fingerprint,
)
from marketops.domain.run_delta import DeltaCell, PoolEffect, RunDelta

WORKSPACE = "uk-acme-natural-grass"
SCENE = "scene-devon"
KEY = "run-2026-09-28-c"
T0 = datetime(2026, 9, 28, 9, 0, tzinfo=timezone.utc)
CUTOFF = T0 + timedelta(days=1)
WINDOW_UNTIL = T0 + timedelta(days=30)
STOPPED_AT = T0 + timedelta(hours=2)
RECEIPT = "rcpt-devon-motherpool"
#: 窗口在场时 ``spent`` 必须是一份**读到的账本读数**：``window_refusal`` 的第四条
#: 把 ``None`` 读成一条拒绝（「没有账本读数」与「账本说没花」在受理面上同形，而两者
#: 处置相反）。这一族判的是**别的**那三条，所以每一处都把它给足。
SPENT = {BudgetLine.SEARCH: 0}


# --- 构造器（形状照 tests/test_run_window_admission.py） -----------------------


def _window(*, max_objects: int = 50, ceiling: int = 100) -> AuthorizationWindow:
    """一条授权链。**每一条都是一个内容摘要**，所以改一个阈值就是**另一条链**。"""
    return AuthorizationWindow(
        max_objects=max_objects,
        budget=(RunBudgetLine(line=BudgetLine.SEARCH, ceiling=ceiling),),
        not_after=WINDOW_UNTIL,
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


def _delta(*, keys: tuple[str, ...] = ("v-1",)) -> RunDelta:
    """一份有内容的 Delta。``keys`` 不同就是**另一份产出**（指纹不同）。"""
    return RunDelta(
        effects=tuple(
            PoolEffect(cell=DeltaCell.DISCOVERED, key=key, label=f"场地 {key}")
            for key in keys
        )
    )


def _asset(venue_asset_id: str, *, evidence: tuple[str, ...] = ()) -> VenueAsset:
    return VenueAsset(
        workspace=WORKSPACE,
        venue_asset_id=venue_asset_id,
        name=f"{venue_asset_id} 的场地",
        evidence_ids=list(evidence),
        first_seen_at=T0,
    )


def _admit(store: SqliteRunStore, spec: RunSpec, *, spent: dict | None = SPENT) -> Run:
    return store.admit(spec, spec_sha256=spec_fingerprint(spec), at=T0, spent=spent)


def _stop(
    store: SqliteRunStore, run: Run, *, delta: RunDelta | None = None, at: datetime = STOPPED_AT
) -> Run:
    return store.stop(
        run.run_id, delta=delta if delta is not None else _delta(), status=RunStatus.COMPLETED, at=at
    )


def _runs(tmp_path: Path, name: str = "marketops.db") -> SqliteRunStore:
    return SqliteRunStore(tmp_path / "state" / name)


def _wired(tmp_path: Path) -> tuple[SqliteRunStore, SqliteVenueAssetStore, Path]:
    """两个面**必须在同一个库**上：一次折入是池子那一行加上记在那一轮上的同一笔，
    跨库时它们根本不在同一处，而这一族判的正是它们**同生共死**那一条。"""
    db_path = tmp_path / "state" / "marketops.db"
    return SqliteRunStore(db_path), SqliteVenueAssetStore(db_path, merge_assets), db_path


# --- 起跑器 -------------------------------------------------------------------


def _race(count: int, work) -> tuple[list, list]:
    """``count`` 个 worker 用 ``Barrier`` 对齐起跑；结果与异常**按 worker 序号**返回。

    按序号对齐是必要的：判据要问「**哪一个**赢了」，而不只是「有一个赢了」——
    后者在一份把先落的写盖掉的实现上照样成立。``count`` 必须等于线程数，否则
    ``Barrier`` 会把先到的那些按在那里等到超时。
    """
    start = threading.Barrier(count)
    results: list = [None] * count
    errors: list[BaseException | None] = [None] * count

    def one(index: int) -> None:
        try:
            start.wait(timeout=10)
            results[index] = work(index)
        except BaseException as exc:  # noqa: BLE001 - 线程里的异常要带回主线程
            errors[index] = exc

    with ThreadPoolExecutor(max_workers=count) as pool:
        list(pool.map(one, range(count)))
    return results, errors


def _split(results: list, errors: list) -> tuple[list, list]:
    """过的那几个、抛出来的那几个，各自**保持 worker 序号**。"""
    ok = [value for value, exc in zip(results, errors) if exc is None]
    bad = [exc for exc in errors if exc is not None]
    assert len(ok) + len(bad) == len(errors)
    return ok, bad


# --- 1 · 受理面：同一条链上开不出两轮 ------------------------------------------


def test_only_one_auto_run_can_open_this_chain_at_once(tmp_path: Path) -> None:
    """八个自动受理同时打同一条链 ⇒ **恰好一个**成功，其余被 ``open_runs`` 拒。

    这一条是条件 4「不创建下一 Run」的**并发形态**，也是
    ``adapters/sqlite/runs.py:246-273`` 那段注释逐字点名的那件事：

    > 放进来的理由是它在事务外读不到并发：两个同时提交的自动受理会各自看到
    > ``open_runs=0``、于是**两个都过**，而这条链的累计在两次受理里各少算对方一圈。

    **红的形态是把那一批行挪到 ``BEGIN IMMEDIATE`` 之前读。** 这一句是实测出来的，两种
    形状都在 ``h/M1-DIAG.py`` 里跑过：

    * 只把那一批行换成**另一条连接**读、判定仍在写事务里 ⇒ **绿**（``1 passed``）。
      写锁已经把八个 worker 排成一列，后一个的那次读发生在前一个**提交之后** —— 它读得到
      那一行。所以承重的是**取锁的时刻**，不是用哪条连接读。
    * 把读挪到拿锁**之前** ⇒ ``assert 8 == 1``，八个全过（每个都在拿锁前看到
      ``open_runs=0``）。

    八不是二：两个 worker 靠调度也有很大几率串行跑完而全绿，八个让「都在拿锁前下结论」
    这件事几乎必然暴露 —— 判据不该只在某次调度下才红。

    拒绝的报文取自 ``ports/runs.py::window_refusal`` 第二条，**不在这里重抄一份**：
    它说的是「还有 1 轮没有结束」，而「1」正是那个**并发下才读得到**的数。
    """
    runs = _runs(tmp_path)
    window = _window()

    def admit(index: int) -> Run:
        return _admit(
            runs,
            _spec(window=window, idempotency_key=f"{KEY}-c1-{index}"),
        )

    results, errors = _race(8, admit)
    ok, bad = _split(results, errors)

    assert len(ok) == 1
    assert len(bad) == 7
    for exc in bad:
        assert isinstance(exc, RunSpecError)
        assert "还有 1 轮没有结束" in str(exc)

    progress = runs.chain_progress(workspace=WORKSPACE, window_digest=window.digest)
    assert progress.open_runs == 1
    assert progress.runs == 0
    assert runs.get(ok[0].run_id) is not None


def test_the_same_key_submitted_at_once_admits_one_run(tmp_path: Path) -> None:
    """同一个幂等键**同一份规格**同时提交八次 ⇒ 同一个 Run、一本**空账**。

    这一条判两样，第二样才是要点：

    * 同一份规格的八次提交拿回**同一个 ``run_id``**（受理是幂等的，条件 1）；
    * 那个 Run 的折入账**存在且为空**。``folds_of`` 读不出来时抛
      ``RunFoldLedgerError``　而不是返回 ``()``（``ports/runs.py::RunStore.folds_of``
      三种状态那一节），所以这条断言同时钉住了「受理当场开了账」与「**重放那一支不
      重开账**」：一个在重放路径上也顺手开账的实现会把它已经记下的折入擦掉，而那种
      实现在这里绿 —— 所以这一条只判得出「有账且是空的」，擦账那一半归
      ``tests/test_run_fold_ledger.py::test_an_admitted_run_opens_an_empty_ledger``。

    盘上只有一行这一点由 ``chain_progress`` 读：``open_runs == 1`` 与 ``runs == 0``
    合起来就是「这一条链上恰好一行、而且它还在跑」。
    """
    runs = _runs(tmp_path)
    spec = _spec(idempotency_key=f"{KEY}-c2", window=_window())

    results, errors = _race(8, lambda index: _admit(runs, spec))
    ok, bad = _split(results, errors)

    assert bad == []
    assert {one.run_id for one in ok} == {run_id(WORKSPACE, spec.idempotency_key)}
    rid = ok[0].run_id
    assert runs.folds_of(rid) == ()

    progress = runs.chain_progress(workspace=WORKSPACE, window_digest=spec.window.digest)
    assert (progress.runs, progress.open_runs) == (0, 1)


def test_the_same_key_with_two_specs_admits_exactly_one(tmp_path: Path) -> None:
    """同一个幂等键配**两份不同的规格**同时提交 ⇒ 只有一份规格赢，另一份全被拒。

    判的是「同一个键的承诺是『重发不会受理两次』，不是『同一个键可以改内容』」在
    并发下的形状。八次提交里四份是规格 A、四份是规格 B（**同一个键**）：

    * 拿到**赢的那一份**的四次是**重放** —— 拿回同一个 ``run_id``，成功；
    * 拿到**另一份**的四次是冲突 —— ``IdempotencyConflictError``，一个都不许过。

    两半都要，因为它们的反面是两份不同的错实现：一份「键冲突时返回值而不是抛」的实现
    会让 ``bad`` 变空；一份「键相同就取新规格覆写」的实现会让盘上那一行说到**输的**
    那份规格头上 —— 所以这里不只查异常类型，还查**盘上那一行是哪一个指纹**
    （``spec_sha256`` 逐字相等），以及**输的那些 worker 拿的确实是另一个指纹**。

    赢家是 A 还是 B 取决于调度，所以断言写成「恰好一个指纹赢了」，不钉死 A。
    """
    runs = _runs(tmp_path)
    specs = [
        _spec(idempotency_key=f"{KEY}-c3", objective=f"第 {index} 份目标")
        for index in range(2)
    ]
    fingerprints = [spec_fingerprint(spec) for spec in specs]

    def submit(index: int) -> tuple[RunSpec, Run]:
        spec = specs[index % 2]
        return spec, _admit(runs, spec)

    results, errors = _race(8, submit)
    winners, bad = _split(results, errors)

    assert len(bad) == 4
    for exc in bad:
        assert isinstance(exc, IdempotencyConflictError)

    won = {spec_fingerprint(spec) for spec, _ in winners}
    assert len(won) == 1
    assert {one.run_id for _, one in winners} == {run_id(WORKSPACE, f"{KEY}-c3")}

    lost = {
        fingerprints[index % 2] for index, exc in enumerate(errors) if exc is not None
    }
    assert lost == set(fingerprints) - won

    landed = runs.get(winners[0][1].run_id)
    assert landed is not None
    assert landed.spec_sha256 == next(iter(won))
    assert landed.spec.objective == next(
        spec.objective for spec in specs if spec_fingerprint(spec) in won
    )


# --- 2 · 结束面：一个 Run 只有一份结局与一份产出 ------------------------------


def test_two_different_endings_arriving_at_once_leave_one_on_disk(tmp_path: Path) -> None:
    """两套**不同**结局同时到达 ⇒ 恰好一个成功，另一个 ``RunStateError``。

    ``adapters/sqlite/runs.py:327-350`` 的读-判-写全在一条 ``BEGIN IMMEDIATE`` 里，
    而那条 ``UPDATE`` 的 ``WHERE`` **只有主键、没有版本列**：挡住迟到写者的不是一次
    比较并交换，是「同一笔事务里重读那一行 + 比内容指纹」。这一条判的就是那个等价物
    成立 —— **红的形态是把读与写拆开**（先 ``_run``、再另开事务 ``UPDATE``），那时
    两个写者都在中间看到「还没结束」，于是**两个都返回成功**，而盘上留的是后写的
    那一份，先落盘那一份的产出没有任何一处记着它丢过。

    断言盘上那一行说的是**赢的那一个**的产出，而不是「有一个产出了一份」：后者在一份
    「两个都写、最后一个赢」的实现上照样成立。
    """
    runs = _runs(tmp_path)
    run = _admit(runs, _spec(idempotency_key=f"{KEY}-c4"))
    deltas = [_delta(keys=("v-a",)), _delta(keys=("v-b",))]

    def finish(index: int) -> Run:
        return _stop(runs, run, delta=deltas[index % 2])

    results, errors = _race(2, finish)
    ok, bad = _split(results, errors)

    assert len(ok) == 1
    assert len(bad) == 1
    assert isinstance(bad[0], RunStateError)
    assert "不覆盖" in str(bad[0])

    landed = runs.get(run.run_id)
    assert landed is not None
    assert landed.delta == ok[0].delta
    assert landed.stopped_at == STOPPED_AT.isoformat()


def test_two_identical_endings_arriving_at_once_are_one_replay(tmp_path: Path) -> None:
    """两套**相同**结局同时到达 ⇒ 两边都成功、同一行、``stopped_at`` 不变。

    这是上一条的**反面**，而且必须两条都在：一个「第二个写者一律拒」的实现会让上一条
    绿、这一条红，而它错在把**重放**读成了**第二次结束** —— 重放的承诺是拿回同一行，
    条件 7 的「重放不重复创建」靠的就是它。

    ``at`` 在这一族里给两边是同一个读数，所以这一条判不了「重放记的是第一次停表的那个
    时刻」—— 那一句由 ``tests/test_run_ending_contract.py`` 的重放判据钉着；这里判的
    只是**两个写者都拿到了那一行**，且它没有因为第二个写者而被改写。
    """
    runs = _runs(tmp_path)
    run = _admit(runs, _spec(idempotency_key=f"{KEY}-c5"))
    delta = _delta()

    def finish(index: int) -> Run:
        return _stop(runs, run, delta=delta)

    results, errors = _race(2, finish)
    ok, bad = _split(results, errors)

    assert bad == []
    assert len(ok) == 2
    assert {one.stopped_at for one in ok} == {STOPPED_AT.isoformat()}

    landed = runs.get(run.run_id)
    assert landed is not None
    assert landed.delta == delta
    assert landed.stopped_at == STOPPED_AT.isoformat()


def test_a_snapshot_taken_before_the_end_cannot_overwrite_it(tmp_path: Path) -> None:
    """一个**结束之前**取到的快照，不能把它的旧产出盖到已经落盘的新事实上。

    条件 7 第三半逐字是「旧 session 不能用旧 snapshot 覆盖新事实」。Run 这一层没有
    session 这个对象（最近的操作单位是 Run），所以它在这一层读作：**一个持有旧
    ``Run`` 值的写者**。这一条不需要线程 —— 那个快照的**年龄**就是被判的东西，
    而在两个线程里造它反而会让判据看起来像在判调度。

    两半都要：拿**旧产出**来 ⇒ 拒（``RunStateError``，报文里那句「不覆盖」）；拿
    **同一套**产出再来 ⇒ 重放，拿回同一行、``stopped_at`` 不动。只有前一半的实现会
    把重放也拒掉，只有后一半的实现会把旧写收下。
    """
    runs = _runs(tmp_path)
    run = _admit(runs, _spec(idempotency_key=f"{KEY}-c6"))
    stale = runs.get(run.run_id)
    assert stale is not None
    assert (stale.delta, stale.stopped_at) == (None, None)

    landed_delta = _delta(keys=("v-landed",))
    _stop(runs, run, delta=landed_delta)

    later_delta = _delta(keys=("v-later",))
    with pytest.raises(RunStateError) as caught:
        _stop(runs, run, delta=later_delta, at=STOPPED_AT + timedelta(hours=1))
    assert "不覆盖" in str(caught.value)

    after = runs.get(run.run_id)
    assert after is not None
    assert after.delta == landed_delta
    assert after.stopped_at == STOPPED_AT.isoformat()

    # 同一套产出再来一次是重放：拿回同一行，一个字节都不改。
    again = _stop(runs, run, delta=landed_delta, at=STOPPED_AT + timedelta(hours=5))
    assert again == after


# --- 3 · 折入面：池子那一行与那一笔账同生共死 --------------------------------


def test_a_fold_that_arrives_after_the_end_writes_neither_side(tmp_path: Path) -> None:
    """结束之后再折 ⇒ 拒，而且**池子一个字节都不写**。

    ``record_fold`` 的第二条判定（``ports/runs.py::fold_after_end_refusal``）挡的是
    时序：结束那一刻的账是按「到那时为止折了什么」结的，之后再落一笔，那一笔不在任何
    一份账里，也没有任何一次核对会看见它（那一轮不会结束第二次）。

    **两样都不许变**这一半才是要点。``upsert_asset`` 把池子的写与这一笔账放在同一条写
    事务里（``adapters/sqlite/pool.py:96-167``），所以这一笔被拒时池子那一行也**回滚**
    掉了 —— 判据直接查那个**新句柄取不到**，而不是查「账上没多一笔」。只查账的实现
    会漏掉「池子变了而账平」那一条，而它正是 §B17 条件 2 唯一要发现的事。

    （折入那一侧的并发判据不住在这里，住在
    ``tests/test_run_fold_ledger.py::test_two_connections_folding_the_same_receipt_row_write_one_entry``。）
    """
    runs, pool, _ = _wired(tmp_path)
    run = _admit(runs, _spec(idempotency_key=f"{KEY}-c7"))
    _stop(runs, run)

    before = runs.folds_of(run.run_id)
    assert before == ()

    late = _asset("v-late")
    with pytest.raises(RunStateError) as caught:
        pool.upsert_asset(
            late, fold=FoldAttribution(run_id=run.run_id, receipt_id=RECEIPT, ordinal=0)
        )
    assert "不能再往它上面记折入" in str(caught.value)

    assert pool.get_asset("v-late") is None
    assert runs.folds_of(run.run_id) == before


def test_folds_arriving_at_once_all_get_their_own_seq(tmp_path: Path) -> None:
    """八笔折入同时到 ⇒ 八笔都在，``seq`` 不重不漏且**由事务自己数**。

    ``record_fold`` 的 ``seq`` 是「读最大值再加一」，而它不是一次竞态 ——
    ``adapters/sqlite/db.py:858`` 逐字：「``BEGIN IMMEDIATE`` 之下没有别人在写同一轮的
    账」。这一条判的就是那一句：红的形态是把 ``seq`` 挪到事务外算（或者挂 rowid），
    那时两笔会拿到同一个序号，而**读出来只是少了一笔** —— 报告上它看起来像「折入比
    预计的少」，看不出是重号。

    所以除了 ``folds_of`` 那一次读，还直接读一遍那张表的 ``seq``：它显式落盘的理由
    就是「不挂 rowid —— 那是实现细节，一次 ``VACUUM`` 之后会重排而不报错」。
    """
    runs, pool, db_path = _wired(tmp_path)
    run = _admit(runs, _spec(idempotency_key=f"{KEY}-c8"))
    count = 8

    def fold(index: int) -> VenueAsset:
        return pool.upsert_asset(
            _asset(f"v-{index}"),
            fold=FoldAttribution(run_id=run.run_id, receipt_id=RECEIPT, ordinal=index),
        )

    results, errors = _race(count, fold)
    ok, bad = _split(results, errors)
    assert bad == []
    assert len(ok) == count

    receipts = runs.folds_of(run.run_id)
    assert {(one.receipt_id, one.ordinal) for one in receipts} == {
        (RECEIPT, index) for index in range(count)
    }

    with contextlib.closing(sqlite3.connect(db_path)) as conn:
        seqs = [
            int(row[0])
            for row in conn.execute(
                "SELECT seq FROM run_fold WHERE run_id = ?", (run.run_id,)
            )
        ]
    assert sorted(seqs) == list(range(count))


def test_two_handles_for_one_receipt_row_leave_one_entry(tmp_path: Path) -> None:
    """同一份回执的同一行配上**两个不同句柄** ⇒ 恰好一笔，其余全拒。

    账本按 ``(run_id, receipt_id, ordinal)`` 判重放，而**句柄不能当那个键**：一份回执
    里两行可以折进同一块场地（没有来源编号、名字与地点又相同的那两行）。所以同一个键
    配两个句柄不是重放，是一次完整性冲突（``ports/runs.py::fold_replay_conflict``）——
    要么这一笔被记了两次而第二次折的是别的行，要么有人动了库。

    这一条也是这一层**最接近 CAS** 的地方：它没有版本列，判的是「同一个键必须配同一个
    值」。红的形态是把重放判定改成按句柄比 —— 那时四个 worker 会各自插一笔（四个不同
    的键），账上四笔而池子四行，报告上读起来像「折入比预计的多」。

    落败的那些 worker 的池子行**一个都不该在**：账被拒时整笔回滚。这一半与
    :func:`test_a_fold_that_arrives_after_the_end_writes_neither_side` 判的是同一条
    原子性，只是理由不同（这里是冲突、那里是时序）—— 两条都留着，因为回滚的触发点
    在实现里不是同一处。
    """
    runs, pool, _ = _wired(tmp_path)
    run = _admit(runs, _spec(idempotency_key=f"{KEY}-c10"))
    count = 4

    def fold(index: int) -> VenueAsset:
        return pool.upsert_asset(
            _asset(f"v-{index}"),
            fold=FoldAttribution(run_id=run.run_id, receipt_id=RECEIPT, ordinal=0),
        )

    results, errors = _race(count, fold)
    ok, bad = _split(results, errors)

    assert len(ok) == 1
    assert len(bad) == count - 1
    for exc in bad:
        assert isinstance(exc, StoreError)

    receipts = runs.folds_of(run.run_id)
    assert len(receipts) == 1
    winner = receipts[0].venue_asset_id
    assert winner == ok[0].venue_asset_id

    for index in range(count):
        handle = f"v-{index}"
        present = pool.get_asset(handle) is not None
        assert present == (handle == winner)


# --- 4 · 合并：一个观察都不丢，两条判定不许静默并掉一条 ------------------------


def test_two_writers_on_one_row_keep_both_observations(tmp_path: Path) -> None:
    """六个写者同时往**同一块场地**上各带一条证据 ⇒ 六条证据全在。

    ``application/pool.py`` 的合并规则里，``evidence_ids`` 是**指针**、取并集保序
    （``ordered_union``）而不是「后写覆盖」。这一条判的是那一条规则在并发下依然成立：
    六个写者被 ``BEGIN IMMEDIATE`` 排成一列，每一个都在**读到前一个的结果之后**合并。

    红的形态是把 ``upsert_asset`` 的读-合并-写拆开（先读到旧行、再另开事务写），那时
    两个写者都基于同一个旧行合并，后写的那一份把先写那份带来的证据**丢掉** ——
    而报告上它看起来只是「这块场地的证据少了一条」，看不出是并发造成的。

    断言用**集合相等**（六条都在、不多不少），不是「数量对得上」：一个把同一批 id 记
    两遍的实现也会给出一个像样的数量。
    """
    _, pool, _ = _wired(tmp_path)
    count = 6

    def upsert(index: int) -> VenueAsset:
        return pool.upsert_asset(_asset("v-shared", evidence=(f"ev-{index}",)))

    results, errors = _race(count, upsert)
    ok, bad = _split(results, errors)

    assert bad == []
    assert len(ok) == count

    landed = pool.get_asset("v-shared")
    assert landed is not None
    assert sorted(landed.evidence_ids) == [f"ev-{index}" for index in range(count)]


def test_two_verdicts_on_one_row_are_refused_rather_than_merged(tmp_path: Path) -> None:
    """六个写者各报一个**不同的**官网判定 ⇒ 恰好一个落盘，其余全抛，且没人的证据被并进去。

    这是池子这一侧唯一会抛的合并规则（``_merge_domain``）：两边都做过判定且判得不一样
    ⇒ ``AssetConflictError``，因为「本层没有『谁的判定更权威』这条规则」。它同时是条件 7
    第三半在这个仓里**唯一有主语**的那一处 —— 一个旧快照的判定不能把已经确认的官网
    静默作废。

    两半断言：落盘那一行的域名是**某一个** worker 报的（不是拼接出来的），以及它的
    ``domain_evidence_ids`` **恰好只有一条** —— 那是「两条都不落」的读法：落败的写者
    整笔回滚，所以它们连证据都没并进去。只判异常类型的实现，在一份「抛之前先把池子写
    一半」的实现上照样绿。
    """
    _, pool, _ = _wired(tmp_path)
    count = 6

    def upsert(index: int) -> VenueAsset:
        domain = f"site-{index}.example"
        return pool.upsert_asset(
            VenueAsset(
                workspace=WORKSPACE,
                venue_asset_id="v-verdict",
                name="Devon 场地",
                verified_official_domain=domain,
                verified_official_domains=[domain],
                domain_verdict=DomainVerdict.CONFIRMED,
                domain_evidence_ids=[f"ev-{index}"],
                first_seen_at=T0,
            )
        )

    results, errors = _race(count, upsert)
    ok, bad = _split(results, errors)

    assert len(ok) == 1
    assert len(bad) == count - 1
    for exc in bad:
        assert isinstance(exc, AssetConflictError)

    landed = pool.get_asset("v-verdict")
    assert landed is not None
    assert landed.domain_verdict is DomainVerdict.CONFIRMED
    assert landed.verified_official_domains == [ok[0].verified_official_domain]
    assert len(landed.domain_evidence_ids) == 1


# --- 5 · 每一条链的上限只属于它自己 -------------------------------------------


def test_two_chains_admitting_at_once_do_not_block_each_other(tmp_path: Path) -> None:
    """八条**不同的**授权链各自的自动受理同时到 ⇒ 八个都过得去。

    受理面的上限是**每一条链自己**的：另一条链上有一轮在跑，不该让这一条被拒。这一条
    判的就是那个「自己」在并发下依然成立 —— 八条链各来一次全部成功，而且每一条读自己
    的累计都看到恰好一行在跑（八行都落在同一个库、同一个工作区里，差别只有窗口摘要）。

    红的形态是 ``ports/runs.py::progress_of`` 里那句筛选没了
    （``window is None or window.digest != window_digest ⇒ continue``）：那时八条链互相
    看见对方的行，第一条之后的每一个都会读到 ``open_runs=1`` 而被 ``window_refusal``
    拒掉 —— 而报告上它看起来只是「这一批只开出了一轮」，看不出是读错了链。

    纯函数那一侧已经有 ``tests/test_run_window_admission.py::test_only_the_named_chain_is_counted``
    钉住同一句判据，这一条**不重写它**，判的是**适配器喂进去的行**：
    ``adapters/sqlite/runs.py`` 按 ``workspace`` 取行（不按摘要），筛选全靠那一个函数 ——
    一个把筛选挪进实现（或者把摘要参数接错）的版本，在纯函数判据上是绿的。
    """
    runs = _runs(tmp_path)
    count = 8
    windows = [_window(max_objects=10 + index) for index in range(count)]

    def admit(index: int) -> Run:
        return _admit(
            runs, _spec(window=windows[index], idempotency_key=f"{KEY}-c12-{index}")
        )

    results, errors = _race(count, admit)
    ok, bad = _split(results, errors)

    assert bad == []
    assert len({one.run_id for one in ok}) == count

    for window in windows:
        progress = runs.chain_progress(workspace=WORKSPACE, window_digest=window.digest)
        assert (progress.runs, progress.open_runs) == (0, 1)
