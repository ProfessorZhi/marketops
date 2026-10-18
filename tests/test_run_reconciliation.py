"""§B17 条件 2 的后半句：**业务键双向对账**，以及「账不平就不结束」。

## 这一族要钉住的是一件事：两份记录说的是不是同一件事

一份 Delta 是**这一轮自己的主张**，一份**折入句柄**（``fold_claim`` 收下的那些）
是**这一轮主张自己折进池子的那些行** —— 两份都不是推导出来的。对账是拿它们互相问：

* 方向一：Delta 说池子里有那一行（九格里的五格），池子读得出来吗；
* 方向二：这一轮往池子里写了那些键，Delta 记了吗、记成了什么。

两侧都由同一次计算产出的实现（「折了什么就记什么」）永远对得上 —— 而它永远
发现不了这一层唯一要发现的那件事：**池子变大了而报告说没变**。所以本文件里有一条
专门判「按计数对账读不出来」的判据（``test_the_reconciliation_compares_key_sets_
not_counts``）：纠错与 currentness 改变对「新增了几行」是零。

## 三个不变量各守一半

| 守哪一半 | 落在哪 |
|---|---|
| 哪几格主张池子里有那一行 | ``domain/run_delta.py`` 的第四节 + 本文件第一条判据 |
| 五个键表两两不相交、折进去的键必须有归宿 | ``RunDeltaReconciliation`` 的校验器 + 本文件那三条 |
| 账不平不许结束 | ``close_run`` + 本文件那一族 |

## 一条与存储无关的断言，以及它为什么在这里

**被拒的结束一个字节都不落。** 账不平的时候那一轮必须仍然**没有结束过**，而不是
「写下了但标记为拒绝」—— 与 ``tests/test_run_ending_contract.py`` 里那条同形：
先落盘再判的实现会让一个从没算平的 Run 在次日读起来像是一次正常结束。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from pydantic import ValidationError

from marketops.adapters.memory import (
    InMemoryActionItemStore,
    InMemoryRunStore,
    InMemoryVenueAssetStore,
)
from marketops.adapters.sqlite.pool import SqliteVenueAssetStore
from marketops.adapters.sqlite.runs import SqliteRunStore
from marketops.application.pool import merge_assets
from marketops.application.run_reconciliation import (
    MissingPoolRow,
    RunDeltaReconciliation,
    close_run,
    fold_claim,
    reconcile_run_delta,
)
from marketops.domain.entities import VenueAsset
from marketops.domain.fold import FoldAttribution
from marketops.domain.enums import BudgetLine, ExecutionMode, RunStatus
from marketops.domain.errors import (
    RunReconciliationError,
    RunSpecError,
    StoreError,
)
from marketops.domain.run import RunBudgetLine, RunSpec, run_id, spec_fingerprint
from marketops.domain.run_delta import DeltaCell, PoolEffect, RunDelta
from marketops.ports.pool import VenueAssetStore
from marketops.ports.runs import RunStore
from marketops.ports.views import ActionItemStore
from tests.test_asset_store_contract import _smuggle_foreign_row

WORKSPACE = "uk-acme-natural-grass"
OTHER_WORKSPACE = "uk-something-else"
SCENE = "scene-devon"
KEY = "run-2026-09-28-c"
T0 = datetime(2026, 9, 28, 9, 0, tzinfo=timezone.utc)
CUTOFF = T0 + timedelta(days=1)
STOPPED_AT = T0 + timedelta(hours=2)
RID = run_id(WORKSPACE, KEY)

V1 = f"src-{'1' * 16}"
V2 = f"src-{'2' * 16}"
V3 = f"src-{'3' * 16}"


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


def _effect(cell: DeltaCell, key: str) -> PoolEffect:
    """一条处置。``label`` 与 ``why`` 都给上：九格里六格要前者、三格要后者，
    逐条判据各自去想「这一格要哪一个」是噪音，而两格都给在类型上恒合法。"""
    return PoolEffect(cell=cell, key=key, label=f"{key} 那块场地", why=f"{key} 的原因")


def _asset(venue_asset_id: str, *, workspace: str = WORKSPACE) -> VenueAsset:
    return VenueAsset(
        workspace=workspace,
        venue_asset_id=venue_asset_id,
        name=f"{venue_asset_id} 的场地",
        first_seen_at=T0,
    )


RECEIPT_ID = "rcpt-devon-motherpool"


def _fold(venue_asset_id: str, *, ordinal: int = 0) -> FoldAttribution:
    """「这一块场地是**这一轮**折进池子的」—— ``upsert_asset`` 的第二个参数。

    池子那一行的写与这一笔账在**同一个事务**里（§B17 条件 2），所以这一族的
    fixture 里两者必须成对出现：只写池子、不写账，``close_run`` 的第一道闸就会把
    那份 ``folded`` 拒掉 —— 那是**判据在判事**，不是 fixture 少了一步。
    """
    return FoldAttribution(run_id=RID, receipt_id=RECEIPT_ID, ordinal=ordinal)


#: ``_written`` / ``_refused`` 两个构造点（一个 ``asset`` 非空的 ``PromotionOutcome``、
#: 一个留空的）在 e4 之前住在这里，用来给 ``folded`` 造一份主张。**它们被删掉了**，
#: 而不是留着不用：主张的取法换成了句柄，「回执里哪些行真的落了盘」这一格的判据也就
#: 不再是那个过滤器 —— 是**账本**（``RunStore.folds_of``，它记的就是池子那一行落了盘）。
#: 过滤器弱于账本：它只知道 ``asset`` 是不是空的，账本知道那一行**在不在**。
#: 判「多报了」的那一条住在本文件最后一节（``test_an_over_claim_is_refused``）。
@pytest.fixture(params=["sqlite", "memory"])
def runs(request: pytest.FixtureRequest, tmp_path: Path) -> RunStore:
    """这一轮的存储，两个实现之一。名字进测试 id —— 红了的时候一眼看得见是哪一个。"""
    if request.param == "sqlite":
        return SqliteRunStore(tmp_path / "state" / "marketops.db")
    return InMemoryRunStore()


@pytest.fixture
def assets(runs: RunStore, tmp_path: Path) -> VenueAssetStore:
    """池子 —— **与 ``runs`` 同一个后端**，参数化由 ``runs`` 带。

    两个后端必须同进同退，因为一次折入是**两件事**：池子那一行，与记在那一轮账上
    的同一笔（§B17 条件 2）。跨后端时它们根本不在同一处 —— SQLite 池子写的是库里的
    ``run_fold`` 表，而内存的 Run 替身读的是它自己的字典 —— 于是 ``[sqlite-memory]``
    那几格测的是一台配错了的机器，而不是这一层要判的事。

    内存这一侧接上账本正是 ``ledger=`` 那个参数存在的理由：折入带了归属而池子没有
    账本时它**拒绝**（见 ``InMemoryVenueAssetStore.upsert_asset``）—— 那条拒词是
    fail closed 的那一半，不是 fixture 的麻烦。
    """
    if isinstance(runs, SqliteRunStore):
        return SqliteVenueAssetStore(tmp_path / "state" / "marketops.db", merge_assets)
    return InMemoryVenueAssetStore(merge_assets, ledger=runs)


@pytest.fixture
def elsewhere(runs: RunStore, tmp_path: Path) -> VenueAssetStore:
    """**另一个**池子：同一个后端、空的、与 ``assets`` 不是同一份存储。

    存在的理由见 ``test_the_refusal_says_the_run_had_folded_it_when_the_pool_no_longer_
    reads_it``：那是「账上有而池子里没有」今天唯一造得出来的路。它不接账本 —— 这一族
    只拿它当**读的那一侧**，一次折入都不会写进去。
    """
    if isinstance(runs, SqliteRunStore):
        return SqliteVenueAssetStore(tmp_path / "elsewhere" / "marketops.db", merge_assets)
    return InMemoryVenueAssetStore(merge_assets)


def _running(runs: RunStore):
    spec = _spec()
    return runs.admit(spec, spec_sha256=spec_fingerprint(spec), at=T0)


# --- 主张的**形状**：升序去重，由一处归一化 ------------------------------------


def test_the_claim_is_sorted_and_deduplicated() -> None:
    """一份主张的规范化只有一处（``fold_claim``）。三个方向都是承重的：

    * **去重**：一批里两行可能折进同一块场地（没有来源编号、名字与地点又相同的那
      两行），池子那边落的是**一行** —— 不去重的话，一份只点了这个键一次的 Delta
      会被读成「漏记了一次」。
    * **升序**：报文里那一句「前五条」砍掉的是哪几条，不能只是噪音（与
      ``list_assets`` 那条同一条理由）。
    * **``()`` 是一个真的读数**：「这一轮一行都没折」与「这个功能还没造」在盘上分得
      开，而这里收的是前者。空主张不是缺席，是一个可以被账本当场驳回的说法。
    """
    assert fold_claim(()) == ()
    assert fold_claim((V2, V1, V2)) == (V1, V2)
    # 一个生成器也收得下：调用点常常是从一批对象上取句柄。
    assert fold_claim(handle for handle in (V3, V1)) == (V1, V3)


# --- 哪几格主张「池子里有那一行」 --------------------------------------------


def test_the_five_cells_that_claim_a_pool_row() -> None:
    """五格说池子里**已有的那一行**，四格不。

    两组的成员在测试里**重打一遍**，不从源码导那个私有常量 —— 抄一遍的话，
    有人把 ``updated`` 挪进另一组时这条判据跟着一起动，它就不再是判据了
    （与 ``tests/test_run_delta.py::test_the_two_lists_are_complements`` 同一条纪律）。
    """
    claims_a_row = {
        DeltaCell.PROMOTED,
        DeltaCell.UPDATED,
        DeltaCell.SUPERSEDED,
        DeltaCell.WITHDRAWN,
        DeltaCell.UNCHANGED,
    }
    other = {
        DeltaCell.DISCOVERED,
        DeltaCell.EXCLUDED,
        DeltaCell.HELD,
        DeltaCell.BLOCKED,
    }

    assert claims_a_row | other == set(DeltaCell)
    assert claims_a_row & other == set()
    assert len(DeltaCell) == 9

    for cell in claims_a_row:
        assert _effect(cell, V1).requires_an_existing_row, cell
    for cell in other:
        assert not _effect(cell, V1).requires_an_existing_row, cell


# --- 方向一：Delta 说池子里有它 ----------------------------------------------


def test_a_claim_on_a_row_the_pool_never_had(assets: VenueAssetStore) -> None:
    """一条 ``updated`` 指着一个池子里从来没有过的键。

    报告上它与一条真的纠正逐字相同（都写着「池子里已有的那一行被改写了」），
    而它下面什么都没有 —— 这是这一层唯一说得出来的那一类错。
    """
    delta = RunDelta(effects=(_effect(DeltaCell.UPDATED, V1),))

    books = reconcile_run_delta(delta, folded=(), assets=assets)

    assert books.missing_rows == (MissingPoolRow(key=V1, cell=DeltaCell.UPDATED),)
    assert books.agreed == ()
    assert not books.is_reconciled


def test_a_claim_the_pool_can_back_agrees(assets: VenueAssetStore) -> None:
    assets.upsert_asset(_asset(V1))
    delta = RunDelta(effects=(_effect(DeltaCell.UNCHANGED, V1),))

    books = reconcile_run_delta(delta, folded=(), assets=assets)

    assert books.agreed == (V1,)
    assert books.missing_rows == ()
    assert books.is_reconciled


@pytest.mark.parametrize(
    "cell",
    [DeltaCell.DISCOVERED, DeltaCell.EXCLUDED, DeltaCell.HELD, DeltaCell.BLOCKED],
)
def test_the_four_cells_are_not_asked_about_the_pool(
    cell: DeltaCell, assets: VenueAssetStore
) -> None:
    """另外四格说的是「它**没**进池子」或「刚第一次看到」，不对池子主张任何事。

    拿它们去问池子，会让一个正常的 ``held``（缺东西、停在待办）被读成事故 ——
    而 ``held`` 正是 §B17 条件 2 里「合法零新增」的三种收法之一。
    """
    delta = RunDelta(effects=(_effect(cell, V1),))

    books = reconcile_run_delta(delta, folded=(), assets=assets)

    assert books.named_without_a_pool_claim == (V1,)
    assert books.missing_rows == ()
    assert books.is_reconciled


def test_a_key_that_was_folded_and_cannot_be_read_back_is_still_a_reading(
    assets: VenueAssetStore,
) -> None:
    """**折进去过、而池子读不回来**：两条方向说的是同一个键，读数照旧出得来。

    这一格曾经被不变量挡掉过（「折进去的键要么记了、要么没记」），而它是第三种：
    Delta 记了（``updated``）、这一轮也真的写了（账上有它）、池子读不出来。
    它不是构造错误，而是**最该有人去看的那一种** —— 池子那一侧的写没落盘，而报告上
    这一轮与一次正常的改写逐字相同。把可能发生的输入判成异常的不变量，比不写它更坏：
    它会让人以为这种状态不存在。
    """
    delta = RunDelta(effects=(_effect(DeltaCell.UPDATED, V1),))

    books = reconcile_run_delta(delta, folded=(V1,), assets=assets)

    assert books.folded == (V1,)
    assert books.missing_rows == (MissingPoolRow(key=V1, cell=DeltaCell.UPDATED),)
    assert books.unclaimed_folds == ()
    assert not books.is_reconciled


def test_the_refusal_says_the_run_had_not_folded_it(
    runs: RunStore, assets: VenueAssetStore
) -> None:
    """Delta 记了一条没发生的事：**这一轮从没折过它**。

    处置是去看那份 Delta 为什么记了一条没发生的事 —— 与下面那条（折过、而池子读不
    出来）说的是同一个键、同一个格，而**要去做的事相反**。读数里两者都摆着
    （``folded`` 与 ``missing_rows``），所以报文那一行是读出来的，不是猜的。
    """
    _running(runs)
    delta = RunDelta(effects=(_effect(DeltaCell.UPDATED, V1),))

    with pytest.raises(RunReconciliationError) as never_folded:
        _close(runs, assets, delta)

    assert "这一轮没折过它" in str(never_folded.value)
    assert runs.get(RID).is_stopped is False  # type: ignore[union-attr]


def test_the_refusal_says_the_run_had_folded_it_when_the_pool_no_longer_reads_it(
    runs: RunStore, assets: VenueAssetStore, elsewhere: VenueAssetStore
) -> None:
    """同一个键、同一个格，另一头：**这一轮折过它**，而池子读不出来。

    处置是去看池子那一侧的写。今天这条状态只有一条造得出来的路：**结束时报的那个
    池子不是折入时写的那个**（``elsewhere``）—— 写路径构造不出它，因为池子那一行与
    那一笔账同生共死（§B17 条件 2，``tests/test_run_fold_ledger.py`` 里那几条负例
    钉的就是这个）。而读错一个库是真实会发生的：``MARKETOPS_DATA_ROOT`` 指错、
    从别处搬来的一份 ``state/``、两个部署共用一个 Run 记录。

    这一条因此还兼着一个断言：**账与主张对得上**（否则拒词会是「主张与账对不上」，
    而那是另一件事）。两句话都摆在 ``_refusal`` 里，分得开。
    """
    _running(runs)
    assets.upsert_asset(_asset(V1), fold=_fold(V1))
    delta = RunDelta(effects=(_effect(DeltaCell.UPDATED, V1),))

    with pytest.raises(RunReconciliationError) as folded_but_gone:
        _close(runs, elsewhere, delta, (V1,))

    message = str(folded_but_gone.value)
    assert "这一轮折过它，池子那一侧的写没落盘" in message
    assert "主张与它自己的折入账对不上" not in message
    assert runs.get(RID).is_stopped is False  # type: ignore[union-attr]


def test_a_row_that_belongs_to_another_workspace_is_not_read_as_absent(
    assets: VenueAssetStore,
) -> None:
    """**跨工作区不是「没有那一行」。**

    ``get_asset`` 对别人的那一行抛错而不是返回 ``None``（``ports/pool.py`` 那一处
    的定义）。这里照旧让它穿出去 —— 把它读成 ``missing_rows`` 会让一次授权链上的
    越界长得像一个正常的池子读数：报告上那一格写着「池子里没有这一行」，而真相是
    「有一行，不是你的」。与 ``application/promotion.py::_discovery_evidence``
    那一条逐字同一条纪律。

    那一行只有一个来源：**绕过写路径进来的**（写路径拒绝别人的行）。所以构造方式
    就是摆一个局外写入者 —— 用的是 ``test_asset_store_contract.py`` 里那一个
    helper，**不在这里再写一遍**：那是同一句话的第二个定义。
    """
    assets.upsert_asset(_asset(V1))
    _smuggle_foreign_row(assets, _asset(V1, workspace=OTHER_WORKSPACE))
    delta = RunDelta(effects=(_effect(DeltaCell.UNCHANGED, V1),))

    with pytest.raises(StoreError, match=OTHER_WORKSPACE):
        reconcile_run_delta(delta, folded=(), assets=assets)


# --- 方向二：这一轮真的写了池子 ----------------------------------------------


def test_a_fold_the_delta_never_mentions_is_reported(assets: VenueAssetStore) -> None:
    """池子折进去两行，Delta 只记了一行 —— 少的那一行正是「报告说没变」。"""
    assets.upsert_asset(_asset(V1))
    assets.upsert_asset(_asset(V2))
    delta = RunDelta(effects=(_effect(DeltaCell.PROMOTED, V1),))

    books = reconcile_run_delta(delta, folded=(V1, V2), assets=assets)

    assert books.unclaimed_folds == (V2,)
    assert books.folded == (V1, V2)
    assert not books.is_reconciled


def test_a_fold_the_delta_records_as_not_getting_in_is_reported(
    assets: VenueAssetStore,
) -> None:
    """Delta 提了这个键，但把它记成了「它没进池子」。

    与「没记」分开报：一个要**补**一行处置，一个要**改**那一行已经写下的处置 ——
    合成一格的话，读的人分不出该补还是该改。
    """
    delta = RunDelta(effects=(_effect(DeltaCell.HELD, V1),))
    assets.upsert_asset(_asset(V1))

    books = reconcile_run_delta(delta, folded=(V1,), assets=assets)

    assert books.contradicted_folds == (V1,)
    assert books.unclaimed_folds == ()
    assert books.named_without_a_pool_claim == (V1,)
    assert not books.is_reconciled


def test_a_row_that_never_landed_is_not_in_the_claim(assets: VenueAssetStore) -> None:
    """**回执不是「折进去了什么」的读数。**

    ``promote_receipt`` 的回执里含**没有落盘**的行（跨工作区、证据取不回来）—— 那些
    行的 ``asset`` 是空的，一个字节都没写。主张是**句柄**（``close_run`` 交上来的
    那些），一份把回执原样转发的转发会把它们的句柄也算进去。这一层读的是主张，所以
    这里判的是另一半：**不报**这些行时账是平的。报了的后果在 ``close_run`` 那一侧
    （``test_a_claim_that_names_a_row_that_never_landed_is_refused``）。
    """
    delta = RunDelta(effects=(_effect(DeltaCell.EXCLUDED, V1),))

    books = reconcile_run_delta(delta, folded=(), assets=assets)

    assert books.folded == ()
    assert books.unclaimed_folds == ()
    assert books.contradicted_folds == ()
    assert books.is_reconciled


def test_two_source_rows_folding_into_one_venue_are_one_key(
    assets: VenueAssetStore,
) -> None:
    """一批里两行可能折进**同一块**场地（没有来源编号、名字与地点又相同的那两行）。

    池子那边落的是**一行**。不去重的话，一份只点了这个键一次的 Delta 会被读成
    「漏记了一次」—— 而「漏记」与「同一件事被两行说过」是两回事。
    """
    delta = RunDelta(effects=(_effect(DeltaCell.PROMOTED, V1),))
    folded = (V1, V1)
    assets.upsert_asset(_asset(V1))

    books = reconcile_run_delta(delta, folded=folded, assets=assets)

    assert books.folded == (V1,)
    assert books.is_reconciled


def test_the_reading_is_the_same_words_whatever_order_the_folds_arrived_in(
    assets: VenueAssetStore,
) -> None:
    """顺序是承重的：这份读数是给人照着改那一条的，顺序不确定时
    「前五条」砍掉的是哪几条就只是噪音（与 ``list_assets`` 那条同一条理由）。

    **两侧都打乱**：``folded`` 的到达顺序，以及 ``delta.effects`` 自己的元组顺序
    （它不保证升序 —— 写它的那些代码按发现顺序追加）。只打乱一侧的判据是空的：
    实现里 ``folded`` 本来就排序，那一侧打乱了也读不出来。
    """
    cells = {V1: DeltaCell.PROMOTED, V2: DeltaCell.HELD, V3: DeltaCell.UPDATED}
    for venue_asset_id in (V1, V2, V3):
        assets.upsert_asset(_asset(venue_asset_id))
    orders = (
        ((V1, V2, V3), (V1, V2, V3)),
        # Delta 点名 V1 与 V3（主张池子里有它）、V2 是四格。三次重排里 ``effects``
        # 都不是升序的，所以「按输入的到达顺序写进键表」的实现会在这里露出来。
        ((V3, V1, V2), (V2, V3, V1)),
        ((V2, V3, V1), (V3, V1, V2)),
    )

    readings = [
        reconcile_run_delta(
            RunDelta(effects=tuple(_effect(cells[key], key) for key in effects_order)),
            folded=fold_order,
            assets=assets,
        ).model_dump_json()
        for effects_order, fold_order in orders
    ]

    assert readings[0] == readings[1] == readings[2]


def test_the_reconciliation_compares_key_sets_not_counts(assets: VenueAssetStore) -> None:
    """**这一条是本片的正面判据。**

    Delta 点名两个键、这一轮折进去一行 —— 而它们是**不同的**键。按计数对账的实现
    （「Delta 里有 1 条池子处置、这一轮折了 1 行 → 平」）在这里读到的是账平，
    而真实情况是：池子里多出来的那一行没人记，Delta 记的那个键这一轮没被碰过。

    §B17 逐字：「'池子变大'包括纠错和 currentness 改变，不只计算新增行」——
    一次改写落在**已有的那一行**上，对计数是零；而它就是在这里露出来的。
    """
    assets.upsert_asset(_asset(V1))
    assets.upsert_asset(_asset(V2))
    delta = RunDelta(effects=(_effect(DeltaCell.UNCHANGED, V1),))

    books = reconcile_run_delta(delta, folded=(V2,), assets=assets)

    # 两侧**计数相等**：Delta 记了一条池子处置、这一轮折了一行。按计数对账的实现在
    # 这里读到的是账平 —— 而把键摆出来，两个数说的是两件不同的事。
    assert len(books.agreed) == len(books.folded) == 1
    assert books.unclaimed_folds == (V2,)
    assert books.agreed == (V1,)
    assert not books.is_reconciled


# --- 读数的五个键表：不变量 ---------------------------------------------------


def _books(**overrides: object) -> RunDeltaReconciliation:
    payload: dict[str, object] = {
        "folded": (),
        "agreed": (),
        "missing_rows": (),
        "named_without_a_pool_claim": (),
        "unclaimed_folds": (),
        "contradicted_folds": (),
    }
    payload.update(overrides)
    return RunDeltaReconciliation(**payload)  # type: ignore[arg-type]


def test_a_key_in_two_groups_cannot_be_constructed() -> None:
    """一个键在两组里 = 两种读法都成立。**两组判据，两个方向各一条。**

    读得通的错读数比读不出来的更坏：这份读数的**全部用途**就是让人照着它去改那
    一条，而它说着两件不能同时为真的事。
    """
    # 方向一内部：一个键不可能既是「对上了」又是「它没进池子」。
    with pytest.raises(ValidationError, match="两两不相交"):
        _books(agreed=(V1,), named_without_a_pool_claim=(V1,))
    # 方向一与方向二之间：不可能既「Delta 里连这个键都没有」又「Delta 记了它」。
    with pytest.raises(ValidationError, match="是 Delta 点过名的"):
        _books(agreed=(V1,), unclaimed_folds=(V1,))


def test_a_key_marked_as_contradicted_must_be_a_four_cell_key() -> None:
    """``contradicted_folds`` 只收四格点名的键。

    一个漏了那道过滤器的实现（``contradicted = folded − agreed``）会在这里写下
    「Delta 把它记成了没进池子」，而那一格其实是 ``promoted`` —— 一句关于这份 Delta
    的假话。它比漏报更坏：读的人会照着它去**改**一条本来就对的处置。
    """
    with pytest.raises(ValidationError, match="不是四格点名的"):
        _books(folded=(V1,), contradicted_folds=(V1,))


def test_an_unsorted_key_table_cannot_be_constructed() -> None:
    with pytest.raises(ValidationError, match="升序去重"):
        _books(agreed=(V2, V1))


def test_a_folded_key_with_no_home_cannot_be_constructed() -> None:
    """折进去的每一个键都要有归宿：记了、记反了、或者没记。"""
    with pytest.raises(ValidationError, match="没有归宿"):
        _books(folded=(V1,))


def test_the_named_property_is_derived() -> None:
    """``named`` 是**推导**的，不是存下来的 —— 与 ``RunDelta.counts`` 同一条纪律：
    存下来的那一份与键表分家的那天，两处都读得通，而没有一处会红。"""
    books = _books(
        agreed=(V3,),
        missing_rows=(MissingPoolRow(key=V1, cell=DeltaCell.UPDATED),),
        named_without_a_pool_claim=(V2,),
    )

    assert "named" not in RunDeltaReconciliation.model_fields
    assert books.named == (V1, V2, V3)


# --- 结束：账平了才停表 -------------------------------------------------------


def _close(
    runs: RunStore,
    assets: VenueAssetStore,
    delta: RunDelta,
    folded=(),
    *,
    items: ActionItemStore | None = None,
):
    """这一次结束用的跟进池。缺省给一只**空的内存池**：这一族判的是账，而
    ``close_run`` 现在还会往那只池子里开待办（``delta.routes`` 非空时）—— 每一次
    调用给一只新的，免得两条判据之间通过那只池子串味。"""
    return close_run(
        runs=runs,
        assets=assets,
        items=InMemoryActionItemStore() if items is None else items,
        run_id=RID,
        delta=delta,
        status=RunStatus.COMPLETED,
        folded=folded,
        at=STOPPED_AT,
    )


def test_a_close_with_the_books_out_of_balance_leaves_the_run_running(
    runs: RunStore, assets: VenueAssetStore
) -> None:
    """**被拒的结束一个字节都不落。**

    那一行必须仍然没有结束过 —— 不是「写下了但标记为拒绝」。先落盘再判的实现会让
    一个从没算平的 Run 在次日读起来像是一次正常结束。
    """
    _running(runs)
    delta = RunDelta(effects=(_effect(DeltaCell.UPDATED, V1),))

    with pytest.raises(RunReconciliationError):
        _close(runs, assets, delta)

    after = runs.get(RID)
    assert after is not None
    assert after.stop_reason is None
    assert after.delta is None
    assert after.stopped_at is None


def test_a_balanced_close_lands_the_three_cells(
    runs: RunStore, assets: VenueAssetStore
) -> None:
    _running(runs)
    assets.upsert_asset(_asset(V1), fold=_fold(V1))
    delta = RunDelta(
        effects=(_effect(DeltaCell.PROMOTED, V1),),
        recommendations=("下一轮看 Exeter",),
    )

    stopped = _close(runs, assets, delta, (V1,))

    assert stopped.stop_reason is RunStatus.COMPLETED
    assert stopped.stopped_at == STOPPED_AT.isoformat()
    assert stopped.delta is not None
    assert stopped.delta.model_dump_json() == delta.model_dump_json()


def test_the_refusal_names_the_keys_and_the_direction(
    runs: RunStore, assets: VenueAssetStore
) -> None:
    """报文要能让人直接去改那一条 —— 只说「对不上」等于让人从头读那一整轮。

    两个方向**同时**出现：一个键被记成了「没进池子」（要**改**那一行），另一个键
    连记都没记（要**补**一行）。两条处置写在同一份报文里而各自说得清，正是这两格
    分开的理由。
    """
    _running(runs)
    delta = RunDelta(effects=(_effect(DeltaCell.HELD, V1),))
    assets.upsert_asset(_asset(V1), fold=_fold(V1))
    assets.upsert_asset(_asset(V2), fold=_fold(V2, ordinal=1))

    with pytest.raises(RunReconciliationError) as caught:
        _close(runs, assets, delta, (V1, V2))

    message = str(caught.value)
    assert RID in message
    assert V1 in message and V2 in message
    # 方向二之二：改那一行。
    assert "记成了「没进池子」" in message
    # 方向二之一：补一行。**这一句是本片那句判据的正面** —— 按计数对账读不出它。
    assert "Delta 里一个字" in message
    assert "池子变大了而报告说没变" in message
    # 两个方向都指向同一条处置：不结束。
    assert "账平之前这一轮不结束" in message


def test_the_refusal_lists_at_most_five_keys_and_counts_the_rest(
    runs: RunStore, assets: VenueAssetStore
) -> None:
    """一份写了三百条差异的报文，读的人只会去读头两行。"""
    _running(runs)
    keys = [f"src-{index:016x}" for index in range(7)]
    delta = RunDelta(effects=tuple(_effect(DeltaCell.PROMOTED, key) for key in keys))

    with pytest.raises(RunReconciliationError) as caught:
        _close(runs, assets, delta)

    message = str(caught.value)
    assert message.count("  - ") == 5
    assert "…另有 2 条" in message


def test_an_over_claim_is_refused(runs: RunStore, assets: VenueAssetStore) -> None:
    """主张点了一个**账上没有**的句柄：拒绝，且这一轮不结束。

    「回执里哪些行真的落了盘」这一格的主语换过一次：e4 之前它是主张那一侧的过滤器
    （``PromotionOutcome.asset is not None``，由当时那个 ``claimed_folds`` 执行），
    之后是**账本**（``RunStore.folds_of``）。两者读的不是同一件事，而账本那一侧强 ——
    它记的是池子那一行**在不在**，不是「回执里那一格是不是空的」。

    报文也分得开：这一条走的是第一道闸（主张与账），不是第二道（Delta 与主张）。
    两句都在同一个异常类型上，所以「是哪一道先开」只能从措辞上读出来 —— 而它承重：
    第一道不过时，连「拿哪一份 Delta 对账」都还没有答案。
    """
    _running(runs)
    delta = RunDelta(effects=(_effect(DeltaCell.EXCLUDED, V1),))

    with pytest.raises(RunReconciliationError) as caught:
        _close(runs, assets, delta, (V1,))

    message = str(caught.value)
    assert V1 in message
    assert "主张说折进去了、而账上没有" in message
    assert "的账对不上" not in message, "这一条该由第一道闸拦下，而不是第二道"
    assert runs.get(RID).is_stopped is False  # type: ignore[union-attr]


def test_closing_twice_returns_the_same_row(
    runs: RunStore, assets: VenueAssetStore
) -> None:
    """``close_run`` 不是「结束」的第二个入口，所以 ``RunStore.stop`` 那条幂等
    在这里原样成立：账平之后重放一次，对账照旧平，拿回的是**同一行**。"""
    _running(runs)
    delta = RunDelta(effects=(_effect(DeltaCell.UNCHANGED, V1),))
    assets.upsert_asset(_asset(V1))

    first = _close(runs, assets, delta)
    second = _close(runs, assets, delta)

    # ``Run`` 是 frozen dataclass，逐字段相等就是「同一行」：重放记的是**第一次**
    # 停表的那个时刻，第二次调用交的是同一个 ``at``，所以这里也是在证明
    # 「重放不会重新停一次表」。
    assert first == second
    assert second.stopped_at == STOPPED_AT.isoformat()


def test_the_runs_own_ending_refusal_still_answers(
    runs: RunStore, assets: VenueAssetStore
) -> None:
    """对账**没有**把 ``ports/runs.py::ending_refusal`` 吞掉。

    账平而结局不成立（``COMPLETED`` 配一份每一格都空的 Delta）时，答话的仍然是那一份
    判据 —— 它是两个实现共用的，不该在这一层再判一次（同一句话的第二份实现漂移的
    那天，正是「两个实现都通过」这句话开始没有依据的那天）。
    """
    _running(runs)

    with pytest.raises(RunSpecError, match="每一格都空"):
        _close(runs, assets, RunDelta())


def test_replaying_the_same_folds_gives_the_same_reading(
    assets: VenueAssetStore,
) -> None:
    """同一份 Delta 与同一批折入再对一次，读数**逐字**相同。"""
    delta = RunDelta(effects=(_effect(DeltaCell.PROMOTED, V1),))
    folded = (V1,)
    assets.upsert_asset(_asset(V1))

    first = reconcile_run_delta(delta, folded=folded, assets=assets)
    second = reconcile_run_delta(delta, folded=folded, assets=assets)

    assert first.model_dump_json() == second.model_dump_json()


__all__ = [
    "OTHER_WORKSPACE",
    "T0",
    "V1",
    "V2",
    "V3",
    "_asset",
    "_effect",
]
