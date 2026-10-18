"""§B17 条件 2 的**账**那一半：这一轮折了哪几行，是盘上的权威。

## 它挡的是哪一条假账

``close_run`` 收一份 ``folded``，而那是**调用方递上来的一份主张**。在 e3 之前盘上
没有第二处能佐证它，于是这条走得通的假账成立了：**Run 真的往池子里折了一行，结束
时却报一份空的 ``folded``** —— 对账按「主张里有的键」逐条问池子，主张是空的，于是
一条差异都算不出来，账是平的，``stop_reason`` 是 ``completed``，而池子已经变大了。
那正是这一层唯一要发现的那件事。

修法不是把那条路径堵上，是**给主张一个对手**：折入账。这一族判的就是那个对手。

## 账本的两个状态必须可分辨

* **有账、里面零行** —— 「这一轮一行都没折」。这是 ``admit`` 当场写下的那份空账，
  一份**真读数**，照常停表。
* **账本不在** —— 「没有人记过这一轮折了什么」。它由写路径构造不出来（受理在同一
  个事务里开账），所以只可能来自库外的手或者一个还没有账本的版本。它**不是零**，
  处置是停下来查。

两者在盘上分得开，是因为「有」与「有什么」是**两件事**：``run_fold_ledger`` 一行
说前者（受理时落下），``run_fold`` 若干行说后者。把它们合成一张表的那天，「折了却
没记」与「什么都没折」会读成同一件事，而池子变大了。

## 折入与池子那一行是同一次写

``upsert_asset(..., fold=...)`` 里，池子那一行与这一笔账**同生共死**：SQLite 靠
``BEGIN IMMEDIATE`` 那一个写事务，内存实现靠「会失败的全问完再动状态」。这一族里
有一条判据专门钉这件事的**四个拒词**：任何一条之下，池子与账本都停在调用前的样子。

## 这一族不判什么

对账那三个方向（Delta 与池子、Delta 与主张）在 ``test_run_reconciliation.py`` 里。
这里判的是**账本身**：它什么时候存在、写什么、读回来是什么、被谁写坏。
"""

from __future__ import annotations

import sqlite3
import threading
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from marketops.adapters.memory import (
    InMemoryActionItemStore,
    InMemoryRunStore,
    InMemoryVenueAssetStore,
)
from marketops.adapters.sqlite.pool import SqliteVenueAssetStore
from marketops.adapters.sqlite.runs import SqliteRunStore
from marketops.application.pool import merge_assets
from marketops.application.run_reconciliation import close_run
from marketops.domain.entities import VenueAsset
from marketops.domain.enums import BudgetLine, ExecutionMode, RunStatus
from marketops.domain.errors import (
    RunFoldLedgerError,
    RunReconciliationError,
    RunStateError,
    StoreError,
)
from marketops.domain.fold import FoldAttribution
from marketops.domain.run import RunBudgetLine, RunSpec, run_id, spec_fingerprint
from marketops.domain.run_delta import DeltaCell, PoolEffect, RunDelta
from marketops.ports.pool import VenueAssetStore
from marketops.ports.runs import RunStore
from marketops.ports.views import ActionItemStore

WORKSPACE = "uk-acme-natural-grass"
SCENE = "scene-devon"
KEY = "run-2026-09-28-a"
KEY_B = "run-2026-09-28-b"
T0 = datetime(2026, 9, 28, 9, 0, tzinfo=timezone.utc)
CUTOFF = T0 + timedelta(days=1)
STOPPED_AT = T0 + timedelta(hours=2)
RID = run_id(WORKSPACE, KEY)
RID_B = run_id(WORKSPACE, KEY_B)
V1 = f"src-{'1' * 16}"
V2 = f"src-{'2' * 16}"
RECEIPT = "rcpt-devon-motherpool"


# --- 接好的那一条链 -----------------------------------------------------------


@dataclass(frozen=True)
class Wiring:
    """一条接了线的链：这一轮的记录、池子，以及**库在哪里**。

    ``db_path`` 是第三样东西，而 ``test_run_reconciliation.py`` 那一对夹具不要它 ——
    那一族判的是对账（拿两个模型互相问），这一族判的是**盘上的字**：账本要被读回来、
    要被库外的手动过、要被两个连接同时写，所以库的位置本身是输入的一部分。
    """

    runs: RunStore
    assets: VenueAssetStore
    db_path: Path


@pytest.fixture(params=["sqlite", "memory"])
def wired(request: pytest.FixtureRequest, tmp_path: Path) -> Wiring:
    """两个后端各一条。名字进测试 id —— 红了的时候一眼看得见是哪一个。

    两个后端**同进同退**（池子与 Run 记录必须是同一个后端）：一次折入是池子那一行
    加上记在那一轮上的同一笔，跨后端时它们根本不在同一处，测的就是一台配错了的机器。
    """
    db_path = tmp_path / "state" / "marketops.db"
    if request.param == "sqlite":
        return Wiring(
            SqliteRunStore(db_path),
            SqliteVenueAssetStore(db_path, merge_assets),
            db_path,
        )
    runs = InMemoryRunStore()
    return Wiring(runs, InMemoryVenueAssetStore(merge_assets, ledger=runs), db_path)


def _spec(key: str = KEY) -> RunSpec:
    return RunSpec(
        workspace=WORKSPACE,
        objective="补一轮 Devon 的场地",
        selector=SCENE,
        context_ref="a" * 64,
        plan_ref="b" * 64,
        execution_mode=ExecutionMode.MANUAL,
        window=None,
        budget=(RunBudgetLine(line=BudgetLine.SEARCH, ceiling=20),),
        cutoff=CUTOFF,
        policy_versions=("grading@1f2e3d",),
        idempotency_key=key,
    )


def _admit(wired: Wiring, key: str = KEY) -> str:
    spec = _spec(key)
    return wired.runs.admit(spec, spec_sha256=spec_fingerprint(spec), at=T0).run_id


def _asset(venue_asset_id: str) -> VenueAsset:
    return VenueAsset(
        workspace=WORKSPACE,
        venue_asset_id=venue_asset_id,
        name=f"{venue_asset_id} 的场地",
        first_seen_at=T0,
    )


def _fold(venue_asset_id: str, *, ordinal: int = 0, run: str = RID) -> FoldAttribution:
    """「这一块场地是那一轮折进池子的」—— ``upsert_asset`` 的第二个参数。

    ``ordinal`` 只在**同一份回执里的两行**之间才该变：它进重放判据，而重放判据的
    形状是「同一份回执的同一行同一个句柄 = 重放」。随手换一个序号会让一次重放被读成
    第二笔，于是判据看起来是绿的。
    """
    return FoldAttribution(run_id=run, receipt_id=RECEIPT, ordinal=ordinal)


def _effect(cell: DeltaCell, key: str) -> PoolEffect:
    return PoolEffect(cell=cell, key=key, label=f"{key} 那块场地", why=f"{key} 的原因")


def _close(
    wired: Wiring,
    delta: RunDelta,
    folded=(),
    *,
    run: str = RID,
    items: ActionItemStore | None = None,
):
    """``items`` 缺省给一只空的内存跟进池：这一族判的是**账本**，而 ``close_run`` 现在
    还会往那只池子里开待办（``delta.routes`` 非空时）。"""
    return close_run(
        runs=wired.runs,
        assets=wired.assets,
        items=InMemoryActionItemStore() if items is None else items,
        run_id=run,
        delta=delta,
        status=RunStatus.COMPLETED,
        folded=folded,
        at=STOPPED_AT,
    )


#: 这里原来有一个 ``_written``（造一条 ``asset`` 非空的 ``PromotionOutcome``），
#: 给 ``close_run`` 的 ``folded`` 用。e4 之后 ``folded`` 收的是**句柄**，所以它没有
#: 消费者了 —— 删掉，而不是留着一个不再被任何判据读到的构造点（主张的取法换了，
#: 而「哪些行真的落了盘」现在由账本答，不由那个过滤器答）。
# --- 账本什么时候存在 ---------------------------------------------------------


def test_an_admitted_run_opens_an_empty_ledger(wired: Wiring) -> None:
    """受理顺手开出的那份空账，是「真正的零折入」这一读数**唯一**的来源。

    断言的是「读得出来、且是空的」，不是「读不到」—— 两者在这一族里是相反的两件事，
    而只有前一个是零折入。
    """
    rid = _admit(wired)

    assert wired.runs.folds_of(rid) == ()


def test_a_run_that_folded_nothing_still_closes_with_an_empty_claim(
    wired: Wiring,
) -> None:
    """真正零折入 + 空主张 ⇒ 照常停表。

    这一条与下面那条（账上有而主张说没有）是同一句话的两半：把两者合成一条判据的
    实现，会在**两个方向**里至少错一个 —— 要么把零折入也拒掉，要么把假账放过去。
    """
    _admit(wired)
    delta = RunDelta(effects=(_effect(DeltaCell.HELD, V1),))

    stopped = _close(wired, delta)

    assert stopped.stop_reason is RunStatus.COMPLETED
    assert wired.runs.folds_of(RID) == ()


def test_a_ledger_that_was_never_opened_is_not_an_empty_ledger(wired: Wiring) -> None:
    """「没有这一轮」与「这一轮一行都没折」不是同一句话。

    两个状态的**下一步不同**：前者是一次拿错了 id（或者一次没做成的受理），后者是
    一份真读数。所以这一条与上面那条必须给出不同的报文 —— 都由
    ``fold_ledger_refusal`` 那一个函数分开说。
    """
    _admit(wired)
    unknown = run_id(WORKSPACE, "run-2026-09-28-nobody")

    with pytest.raises(RunFoldLedgerError) as caught:
        wired.runs.folds_of(unknown)

    message = str(caught.value)
    assert unknown in message
    assert "没有这一轮 Run" in message
    assert "这不是「这一轮一行都没折」" in message


# --- 主张与账：两个方向 -------------------------------------------------------


def test_a_claim_of_nothing_when_the_ledger_names_a_row_is_refused(
    wired: Wiring,
) -> None:
    """**e3 要关掉的那条假账，在结束这一层的样子。**

    这一轮真的折了 V1：池子那一行落盘了、账上记了一笔。而结束时报一份**空的**
    ``folded`` —— 修之前这样一份主张会把一条差异都算不出来（对账按主张里的键逐条
    问池子），于是 ``completed`` 停表、账是平的，而池子已经变大了。

    所以这一条断言的是三件事，缺一不可：结束被拒、报文点出那个键、**那一轮仍然在跑**。
    """
    _admit(wired)
    wired.assets.upsert_asset(_asset(V1), fold=_fold(V1))
    delta = RunDelta(effects=(_effect(DeltaCell.PROMOTED, V1),))

    with pytest.raises(RunReconciliationError) as caught:
        _close(wired, delta, ())

    message = str(caught.value)
    assert RID in message
    assert V1 in message
    assert "真的折进去了" in message and "主张里没有" in message
    # 这一句是这条假账的名字 —— 报文点的是它，不是一句「对不上」。
    assert "池子变大了而报告说没变" in message
    # 池子那一行**真的**落盘了 —— 这一条判据的全部意义就在这一句上。
    assert wired.assets.get_asset(V1) is not None
    after = wired.runs.get(RID)
    assert after is not None
    assert after.stop_reason is None


def test_a_claim_that_names_one_key_too_many_is_refused(wired: Wiring) -> None:
    """反方向：主张里多出一个账上没有的键。

    多报的那一个键正是「一次**没折成**的折入被当成折了」—— 与漏报相反的那一半。
    两个方向都必须在同一道闸上被拒，否则「主张与账一致」这句话只在一边成立。
    """
    _admit(wired)
    wired.assets.upsert_asset(_asset(V1), fold=_fold(V1))
    delta = RunDelta(effects=(_effect(DeltaCell.PROMOTED, V1),))

    with pytest.raises(RunReconciliationError) as caught:
        _close(wired, delta, (V1, V2))

    message = str(caught.value)
    assert V2 in message
    assert "主张说折进去了、而账上没有" in message
    assert "要看的是**为什么没落盘**" in message


# --- 账本读不出来时不许读成零 -------------------------------------------------


def test_a_ledger_that_is_gone_is_refused_and_never_read_as_zero(
    wired: Wiring,
) -> None:
    """**账本不在 ⇒ 抛，绝不返回 ``()``。**

    这是整个 e3 的形状所在：一条返回零行的实现会让「折了却没记」与「什么都没折」在
    账上同形，而两者的处置相反（一个照常停表，一个停下来查）。所以这里钉两件事：
    读不出来要抛；抛出来的那句话**逐字**说明它不是零折入。

    这条状态**只有绕过写路径才构造得出来**（受理在同一笔里开账），真实来源是库外的手
    或者一个还没有账本的版本 —— 所以构造方式就是一个局外动过手的库，与
    ``test_asset_store_contract.py::_smuggle_foreign_row`` 同一条理由。
    """
    _admit(wired)
    wired.assets.upsert_asset(_asset(V1), fold=_fold(V1))
    _smuggle_the_ledger_away(wired, RID)

    with pytest.raises(RunFoldLedgerError) as caught:
        wired.runs.folds_of(RID)
    assert "不按零折入读" in str(caught.value)

    # 结束那一层照旧读不出来 —— 而且**空主张也过不去**：一份读不出来的账不能被当成
    # 一份空账，否则漏记的那一轮会在次日读起来像是一次正常结束。
    with pytest.raises(RunFoldLedgerError):
        _close(wired, RunDelta(effects=(_effect(DeltaCell.HELD, V1),)), ())

    after = wired.runs.get(RID)
    assert after is not None
    assert after.stop_reason is None


# --- 同一笔写两遍 -----------------------------------------------------------------


def test_the_same_receipt_row_folded_twice_is_one_entry(wired: Wiring) -> None:
    """同一个 ``(run_id, receipt_id, ordinal)`` 配**同一个**句柄 = 重放，不是第二笔。

    池子那一行本来就是覆盖写，也幂等；所以这一条要判的是**账**没有多出一笔 ——
    这是「重放 trigger 不重复创建」在账本这一侧的形态。
    """
    _admit(wired)
    first = wired.assets.upsert_asset(_asset(V1), fold=_fold(V1))

    second = wired.assets.upsert_asset(_asset(V1), fold=_fold(V1))

    receipts = wired.runs.folds_of(RID)
    assert len(receipts) == 1
    assert receipts[0].venue_asset_id == V1
    assert receipts[0].receipt_id == RECEIPT
    assert receipts[0].ordinal == 0
    # 覆盖写：回来的是同一行，不是第二行。
    assert second == first


def test_the_same_receipt_row_folded_into_another_row_is_refused(wired: Wiring) -> None:
    """同一个键配**另一个句柄**是一次完整性冲突，不是重放。

    重放与冲突的分别就是「配的是不是同一个句柄」。接受它会让「这一轮折了什么」有两个
    答案，而账本的全部价值就是它只有一个。拒的时候**两边都不许动**：账上仍然只有
    原先那一笔，而第二次要写的那个句柄一个字节都不落。
    """
    _admit(wired)
    wired.assets.upsert_asset(_asset(V1), fold=_fold(V1))

    with pytest.raises(StoreError) as caught:
        wired.assets.upsert_asset(_asset(V2), fold=_fold(V2))

    message = str(caught.value)
    assert V1 in message and V2 in message
    assert "折进了另一个句柄" in message
    assert wired.assets.get_asset(V2) is None
    assert [one.venue_asset_id for one in wired.runs.folds_of(RID)] == [V1]


def test_the_ledger_reads_back_in_the_order_the_rows_were_folded(
    wired: Wiring,
) -> None:
    """读回来按**落笔次序**，不是按句柄、也不是按回执行号。

    两份回执行号各自从 0 起，一份回执里两行可以折进同一块场地 —— 按那个顺序排的实现
    会在真实数据上把两笔换位，而报告上只显示「顺序不一样」。
    """
    _admit(wired)
    wired.assets.upsert_asset(_asset(V2), fold=_fold(V2, ordinal=1))
    wired.assets.upsert_asset(_asset(V1), fold=_fold(V1, ordinal=0))

    assert [one.venue_asset_id for one in wired.runs.folds_of(RID)] == [V2, V1]


def test_two_connections_folding_the_same_receipt_row_write_one_entry(
    tmp_path: Path,
) -> None:
    """两个写入者同时折同一行 ⇒ 一笔账，一行池子。

    「检查再写」分开做的那天，两个写入者会在中间都看到「还没记过」，于是同一个键落下
    两笔 —— 而两笔账在报告上读起来像两份折入。SQLite 这一侧挡它的是
    ``BEGIN IMMEDIATE`` 在事务**开头**就拿写锁（见 ``db.py`` 的模块 docstring），
    所以第二次的重放判定发生在第一次提交**之后**。

    这一条只对 SQLite 说得通：内存那份是单线程的替身（它的并发安全由「先问后写」
    保证，而「先问后写」在没有锁的进程里本来就不成立）。
    """
    db_path = tmp_path / "state" / "marketops.db"
    runs = SqliteRunStore(db_path)
    pool = SqliteVenueAssetStore(db_path, merge_assets)
    wired = Wiring(runs, pool, db_path)
    _admit(wired)
    start = threading.Barrier(2)
    errors: list[BaseException] = []

    def fold() -> None:
        try:
            start.wait(timeout=10)
            pool.upsert_asset(_asset(V1), fold=_fold(V1))
        except BaseException as exc:  # noqa: BLE001 - 线程里的异常要带回主线程
            errors.append(exc)

    threads = [threading.Thread(target=fold) for _ in range(2)]
    for one in threads:
        one.start()
    for one in threads:
        one.join(timeout=30)

    assert errors == []
    assert [one.venue_asset_id for one in runs.folds_of(RID)] == [V1]
    assert pool.get_asset(V1) is not None


# --- 两个 Run 用同一块场地 ----------------------------------------------------


def test_two_runs_folding_the_same_row_do_not_share_a_ledger(wired: Wiring) -> None:
    """同一个句柄被两轮各折一次 ⇒ 两份账各记各的，谁都不多出对方的键。

    账本的作用域是**那一轮**：把归属按句柄记账（或者干脆记在池子那一行上）的实现，
    会让第二轮的重放判定撞上第一轮的账 —— 于是 B 的第一笔被读成重放，而 A 的账里
    多出一笔不属于它的折入。
    """
    _admit(wired, KEY)
    _admit(wired, KEY_B)
    wired.assets.upsert_asset(_asset(V1), fold=_fold(V1, run=RID))
    wired.assets.upsert_asset(_asset(V1), fold=_fold(V1, run=RID_B))

    first = wired.runs.folds_of(RID)
    second = wired.runs.folds_of(RID_B)

    assert [one.run_id for one in first] == [RID]
    assert [one.run_id for one in second] == [RID_B]
    assert len(first) == 1 and len(second) == 1
    # 池子那边落下的是**一行**：两轮折的是同一块场地。
    assert wired.assets.get_asset(V1) is not None


# --- 折入与池子那一行同生共死 -------------------------------------------------


@pytest.mark.parametrize("cause", ["no-such-run", "already-stopped"])
def test_a_fold_that_cannot_be_recorded_leaves_neither_side(
    wired: Wiring, cause: str
) -> None:
    """记账那一侧拒了 ⇒ **池子那一行也不许落盘**。

    ``upsert_asset`` 里池子那条 INSERT 排在记账之前（只有一个调用点、判词一份），
    所以「先写过、再回滚」是这条路径上真实存在的中间状态 —— 它靠同一个写事务
    （SQLite）或者「会失败的全在写之前」（内存）收拾掉。少了任何一半，盘上就会留下
    一行**没人认领**的池子行：它不在任何一轮的账上，于是下一次结束对账时，
    一份空主张会把这一行读成「从来没折过」。

    两种成因各判一次：这一轮不在册（拿错了 id / 受理没做成），与这一轮已经结束了
    （时序错乱）。
    """
    if cause == "no-such-run":
        _admit(wired)
        bad = _fold(V1, run=run_id(WORKSPACE, "run-2026-09-28-nobody"))
        expected: type[BaseException] = RunFoldLedgerError
    else:
        _admit(wired)
        _close(wired, RunDelta(effects=(_effect(DeltaCell.HELD, V1),)))
        bad = _fold(V1)
        expected = RunStateError

    with pytest.raises(expected):
        wired.assets.upsert_asset(_asset(V1), fold=bad)

    assert wired.assets.get_asset(V1) is None
    assert wired.runs.folds_of(RID) == ()


def test_a_memory_pool_that_has_no_ledger_refuses_rather_than_dropping_it() -> None:
    """内存池没接账本而来了一个折入 ⇒ 拒，不是静默丢掉那一笔归属。

    静默丢掉就是那条假账最舒服的写法：池子那一行照旧落盘，账上没有，``close_run``
    读这一轮的账时得到一份**空账** —— 于是「这次没记」与「这个功能还没造」在盘上
    同形，而两者的处置相反。所以这里断言两件事：抛，且池子里一个字节都没写。

    只对内存说得通：SQLite 那一侧的账本与池子本来就在同一个 ``db_path`` 上，
    「没接账本」这个状态不存在。
    """
    store = InMemoryVenueAssetStore(merge_assets)
    runs = InMemoryRunStore()
    rid = runs.admit(_spec(), spec_sha256=spec_fingerprint(_spec()), at=T0).run_id

    with pytest.raises(StoreError, match="没有接折入账本"):
        store.upsert_asset(_asset(V1), fold=_fold(V1, run=rid))

    assert store.get_asset(V1) is None
    assert runs.folds_of(rid) == ()


# --- 同一句话的两个实现 -------------------------------------------------------


@pytest.mark.parametrize("cause", ["unknown-run", "ended-run", "ledger-gone"])
def test_the_two_implementations_refuse_in_the_same_words(
    tmp_path: Path, cause: str
) -> None:
    """同一个输入在两个实现上给出**同一条**报文。

    拒词住 ``ports/runs.py``（``fold_ledger_refusal`` / ``fold_after_end_refusal``），
    但判定本身是两个实现各写一份 —— 与 ``_already_ended`` 那一对同一种处置。分家的
    形态是契约测试里那一条比对面报出来：一个实现说「这一轮已经结束了」而另一个说
    「没有这一轮」，读的人会照着其中一句去查一件没发生的事。
    """
    sqlite_path = tmp_path / "sqlite" / "marketops.db"
    sqlite_runs = SqliteRunStore(sqlite_path)
    sqlite_pool = SqliteVenueAssetStore(sqlite_path, merge_assets)
    memory_runs = InMemoryRunStore()
    memory_pool = InMemoryVenueAssetStore(merge_assets, ledger=memory_runs)
    messages: list[str] = []
    for runs, pool in ((sqlite_runs, sqlite_pool), (memory_runs, memory_pool)):
        wired = Wiring(runs, pool, sqlite_path)
        _admit(wired)
        if cause == "unknown-run":

            def call() -> object:
                return runs.folds_of(run_id(WORKSPACE, "run-2026-09-28-nobody"))

        elif cause == "ended-run":
            _close(wired, RunDelta(effects=(_effect(DeltaCell.HELD, V1),)))

            def call() -> object:
                return pool.upsert_asset(_asset(V1), fold=_fold(V1))

        else:
            _smuggle_the_ledger_away(wired, RID)

            def call() -> object:
                return runs.folds_of(RID)

        with pytest.raises((RunFoldLedgerError, RunStateError)) as caught:
            call()
        messages.append(str(caught.value))

    assert messages[0] == messages[1]


# --- 局外动过手的库 -----------------------------------------------------------


def _smuggle_the_ledger_away(wired: Wiring, rid: str) -> None:
    """把这一轮的账本**从盘上拿掉**，绕过写路径。

    两个实现只有「往哪拿」的差别，理由与 ``_smuggle_foreign_row`` 逐字相同：写路径
    构造不出这个状态（``admit`` 在同一笔里开账），真实来源是库外的手或者一个还没有
    账本的版本。``run_fold`` 的 FK 指着账本那一行，所以先删子表。

    内存那份没有盘上的形态，所以它删的是替身手边的那本字典里的一格 —— 与
    ``_smuggle_foreign_row`` 的「内存实现是把手里的 model 塞进它的字典」对称。
    """
    if isinstance(wired.runs, SqliteRunStore):
        conn = sqlite3.connect(wired.db_path)
        try:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute("DELETE FROM run_fold WHERE run_id = ?", (rid,))
            conn.execute("DELETE FROM run_fold_ledger WHERE run_id = ?", (rid,))
            conn.commit()
        finally:
            conn.close()
        return
    assert isinstance(wired.runs, InMemoryRunStore)
    del wired.runs._folds[rid]
