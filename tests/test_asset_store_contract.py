"""``VenueAssetStore`` 契约：**同一组判据，对着两个实现各跑一遍**。

## 为什么两个实现

``ports/pool.py`` 抽出端口的理由是幂等边界不同（累积并修订，不是只增、也不是乐观
并发），但端口本身不构成契约 —— 构成契约的是**同一组判据同时看着两个实现**。
先例是 ``tests/test_evidence_store_contract.py``（三个实现）与
``tests/test_scene_store_contract.py``（两个）与 ``tests/test_budget_contract.py``。
一个实现改了语义另一个不会红，才叫「这是一个契约」。

* ``adapters/sqlite/pool.py`` —— 主存。**读旧行与写新行在同一个
  ``BEGIN IMMEDIATE`` 事务里。**
* ``adapters/memory.py`` —— 测试替身。**没有锁**，所以并发那一条不在这里。

## 两条判据**刻意不在这里**

1. 「写完重开一个 store 还能读到」只对持久化实现成立（内存实现按定义做不到），
   与另外两份契约测试同一条处置。那条判据在 ``tests/test_sqlite_pool.py``。
2. **并发**：`test_scene_store_contract.py` 的「这里有一条刻意不写的判据」逐字
   适用 —— 把并发判据写进共享契约，就得给内存那份加锁，那不是补齐契约，
   是把一个测试替身伪装成并发存储。
3. **``upsert_asset(fold=...)` 那一格**：判它要**另一个 store 在场**（折入账住在
   Run 那一侧），所以这一份的夹具接不上。它连同两个实现的报比对在
   ``tests/test_run_fold_ledger.py`` —— 那一族同样是对着两个实现各跑一遍，只是
   接好的线更长。

## 这里测的是**契约的两半**，不是规则的细节

规则本身（哪一格赢、哪一格不许被抹掉、什么时候报错）在
``tests/test_application_pool.py``，那是 ``application/pool.py`` 的事。
这一份只问**存储有没有照规则办** —— 具体地说：

* 折两次不会多出一行（按 id，不按内容）；
* 折进来的那一行**是规则算出来的那一行**（存储不许自己另写一套合并）；
* 返回的就是落盘的那一行（不是「调用方自己再读一遍」）；
* 不属于本库的行**报错，不返回 ``None``**；
* ``list_assets`` 的顺序稳定。

最后一条需要一点说明：存储没有办法「自己」判断规则有没有被用上，所以这一份把
一个**可辨认的**规则接进去（前缀累加），再断言折出来的行带上了那个规则的痕迹。
接一个真规则去测，只能证明「这一次的结果对」，证明不了「存储调用了它」——
一个把 ``incoming`` 直接覆盖写进去的实现，在单次写入上看起来完全正确。
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from marketops.adapters.memory import InMemoryVenueAssetStore
from marketops.adapters.sqlite.payload import dump_payload
from marketops.adapters.sqlite.pool import SqliteVenueAssetStore
from marketops.domain.entities import VenueAsset
from marketops.domain.enums import DomainVerdict
from marketops.domain.errors import StoreError
from marketops.ports.pool import VenueAssetStore

UTC = timezone.utc
T0 = datetime(2026, 9, 24, 1, 0, tzinfo=UTC)
WORKSPACE = "uk-acme-natural-grass"
OTHER_WORKSPACE = "uk-something-else"


def probe_merge(existing: VenueAsset | None, incoming: VenueAsset) -> VenueAsset:
    """一条**看得出来有没有被执行**的规则。

    ``notes`` 上做前缀累加：折两次之后它必须有两段。一个「直接覆盖写 incoming」的
    实现会把它抹成一段 —— 而在单次写入上，那个实现与真实现的行为完全一样。

    别的格子按「来行覆盖」处理，好让几条与规则无关的判据（多行、排序、工作区）
    不用去构造一份真规则的输入。
    """
    if existing is None:
        return incoming
    return incoming.model_validate(
        {
            **incoming.model_dump(),
            "notes": f"{existing.notes}|{incoming.notes}" if existing.notes else incoming.notes,
            "first_seen_at": min(existing.first_seen_at, incoming.first_seen_at),
            "evidence_ids": [*existing.evidence_ids, *incoming.evidence_ids],
        }
    )


@pytest.fixture(params=["sqlite", "memory"])
def store(request, tmp_path: Path) -> VenueAssetStore:
    """两个实现之一。名字进测试 id —— 红了的时候一眼看得见是哪一个。"""
    if request.param == "sqlite":
        return SqliteVenueAssetStore(tmp_path / "state" / "marketops.db", probe_merge)
    return InMemoryVenueAssetStore(probe_merge)


def make_asset(venue_asset_id: str = "v1", **overrides) -> VenueAsset:
    payload = {
        "workspace": WORKSPACE,
        "venue_asset_id": venue_asset_id,
        "name": "Main Pitch",
        "first_seen_at": T0,
    }
    payload.update(overrides)
    return VenueAsset(**payload)


# --- 折 -----------------------------------------------------------------------


def test_folding_the_same_id_twice_leaves_one_row(store: VenueAssetStore) -> None:
    """按 id 折，不按内容插 —— 池子的行数就是「这个市场有多少块场地」。"""
    store.upsert_asset(make_asset(notes="first"))
    store.upsert_asset(make_asset(notes="second"))
    rows = store.list_assets()
    assert len(rows) == 1, "折两次多出一行：报告上会把同一块场地数成两块"
    assert rows[0].venue_asset_id == "v1"


def test_the_rule_the_store_was_built_with_is_the_one_that_runs(store: VenueAssetStore) -> None:
    """存储必须**调用**接进来的规则，而不是自己折。

    判据是 ``probe_merge`` 留下的痕迹（``notes`` 前缀累加）。一个把来行直接写下去的
    实现在上面那条判据上完全正确，只有这一条能把它认出来。
    """
    store.upsert_asset(make_asset(notes="first"))
    store.upsert_asset(make_asset(notes="second"))
    assert store.get_asset("v1").notes == "first|second"  # type: ignore[union-attr]


def test_upsert_returns_the_row_that_landed(store: VenueAssetStore) -> None:
    """返回的是**落盘后的那一行**，调用方不需要（也不该）再读一遍。

    再读一遍看起来等价，实际是两次快照：中间有人插进来时，调用方手上的那一行与
    刚写下去的那一行不是同一行 —— 而它会被当成「我写下去的东西」用下去。
    """
    store.upsert_asset(make_asset(notes="first"))
    returned = store.upsert_asset(make_asset(notes="second"))
    assert returned.notes == "first|second"
    assert returned == store.get_asset("v1")


def test_a_single_fold_is_the_row_itself(store: VenueAssetStore) -> None:
    """池子里没有这一行时，折进去的就是它本身 —— 规则不该在第一次写入时改造它。"""
    asset = make_asset(domain_verdict=DomainVerdict.UNKNOWN)
    assert store.upsert_asset(asset) == asset


def test_two_assets_do_not_touch_each_other(store: VenueAssetStore) -> None:
    store.upsert_asset(make_asset("v1", name="Main Pitch"))
    store.upsert_asset(make_asset("v2", name="Second Pitch"))
    assert [row.venue_asset_id for row in store.list_assets()] == ["v1", "v2"]
    assert store.get_asset("v1").name == "Main Pitch"  # type: ignore[union-attr]


# --- 读 -----------------------------------------------------------------------


def test_list_assets_is_ordered_by_id_not_by_insertion(store: VenueAssetStore) -> None:
    """顺序是承重的：报告里的截断丢的是尾部，顺序不定时 ``dropped_items`` 只是噪音。"""
    store.upsert_asset(make_asset("v3"))
    store.upsert_asset(make_asset("v1"))
    store.upsert_asset(make_asset("v2"))
    assert [row.venue_asset_id for row in store.list_assets()] == ["v1", "v2", "v3"]


def test_an_unknown_id_is_none_and_that_is_the_only_none(store: VenueAssetStore) -> None:
    """``None`` 只有一个意思：**没有这个 id 的行**。"""
    assert store.get_asset("nothing-here") is None


def test_an_empty_pool_is_an_empty_tuple_not_an_error(store: VenueAssetStore) -> None:
    assert store.list_assets() == ()


# --- 工作区 -------------------------------------------------------------------


def test_a_second_workspace_is_refused_rather_than_mixed_in(store: VenueAssetStore) -> None:
    """一个库只装一个工作区的产出。

    与 ``InMemoryEvidenceStore`` / ``SqliteEvidenceStore`` 逐字同一条判据、同一条
    理由：「按 id 取到的这一行是谁的」必须只有一个答案，而混装的表现不是报错，
    是两批行被静静地一起返回。
    """
    store.upsert_asset(make_asset())
    with pytest.raises(StoreError, match=WORKSPACE):
        store.upsert_asset(make_asset("v2", workspace=OTHER_WORKSPACE))


def test_a_row_from_another_workspace_is_an_error_even_if_the_id_matches(
    store: VenueAssetStore,
) -> None:
    """**不属于本库的行报错，不是 ``None``，也不是被跳过。**

    跳过它会让「这一行不属于你」与「没有这一行」在调用点长成同一个空结果 ——
    越权读因此变成一次静默成功。

    这一行只有一个来源：**绕过写路径进来的**（写路径拒绝别人的行 —— 那是同一个契约
    的另一半，上面那条测的就是它）。真实世界里它不经过任何写入调用：一次
    ``cp -r state/``、一个拿错了 ``MARKETOPS_DATA_ROOT`` 的进程、一个从别的部署搬过来
    的库，都会造出它。所以构造方式就是摆一个局外写入者，与
    ``test_evidence_store_contract.py::_smuggle`` 逐字同一条理由。
    """
    store.upsert_asset(make_asset())
    _smuggle_foreign_row(store, make_asset(workspace=OTHER_WORKSPACE))
    with pytest.raises(StoreError, match=OTHER_WORKSPACE):
        store.get_asset("v1")
    with pytest.raises(StoreError, match=OTHER_WORKSPACE):
        store.list_assets()


def _smuggle_foreign_row(store: VenueAssetStore, item: VenueAsset) -> None:
    """把**别人的**一行放进这个存储的盘上，绕过它自己的写路径。

    两个实现只有**往哪放**的差别：SQLite 是直接写那一行（用不着我们的写事务 ——
    写它的那个进程没有我们的代码），内存实现是把手里的 model 塞进它的字典
    （它没有盘上的形态，所以这一条只能在内存里摆）。

    用的是 ``INSERT OR REPLACE`` 而不是 ``INSERT``：要摆出的状态是「**同一个 id**
    的那一行写着别人的工作区」，而那个 id 已经被本库自己写过一行了。这一点也是
    这两种真实来源之间的差别：``cp -r state/`` 搬来的库会**多**出一行（id 撞不上），
    手工改过的库是**把那一行的血统改掉**。两条路到的是同一个状态，而后者能用
    **同一份构造**在内存实现上重演。
    """
    payload = dump_payload(item.model_dump(mode="json"))
    if isinstance(store, SqliteVenueAssetStore):
        conn = sqlite3.connect(store._path)
        try:
            conn.execute(
                "INSERT OR REPLACE INTO venue_asset "
                "(venue_asset_id, workspace, source_identity, payload, stored_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (
                    item.venue_asset_id,
                    item.workspace,
                    item.source_identity,
                    payload,
                    "2026-09-24T00:00:00+00:00",
                ),
            )
            conn.commit()
        finally:
            conn.close()
        return
    store._assets[item.venue_asset_id] = item  # type: ignore[attr-defined]


# --- 时刻 ---------------------------------------------------------------------


def test_the_earliest_first_seen_survives_a_later_fold(store: VenueAssetStore) -> None:
    """规则的产物原样落盘，不被存储改写。

    ``min`` 这一半在 ``test_application_pool.py`` 里对真规则测；这里只确认存储
    没有把时刻在写读之间挪动（``to_utc`` 之外不该有任何规范化）。
    """
    early = T0 - timedelta(days=30)
    store.upsert_asset(make_asset(first_seen_at=T0))
    row = store.upsert_asset(make_asset(first_seen_at=early))
    assert row.first_seen_at == early
    assert store.get_asset("v1").first_seen_at == early  # type: ignore[union-attr]


def test_a_non_utc_offset_survives_the_round_trip(store: VenueAssetStore) -> None:
    """写进去什么瞬间，读出来什么瞬间 —— 偏移量变不变成 UTC 不影响先后。

    它在 ``test_scene_store_contract.py`` 里是一条真判据（那边的排序按字符串走）；
    这里它是**正对照**：证明下面那条「时刻不被改写」不是因为两个实现都恰好用了
    同一个时区才成立的。
    """
    plus_eight = timezone(timedelta(hours=8))
    at = datetime(2026, 9, 24, 9, 0, tzinfo=plus_eight)
    store.upsert_asset(make_asset(first_seen_at=at))
    assert store.get_asset("v1").first_seen_at == at  # type: ignore[union-attr]


# --- 顺序：幂等键与来源 --------------------------------------------------------


def test_the_source_identity_is_a_column_not_an_identity(store: VenueAssetStore) -> None:
    """两行可以有**同一个** ``source_identity``，它们是两块场地。

    ``osm:node/123`` 是上游认这一行的方式（见 ``domain/entities.py`` 的类文档）：
    它认的是**场地**，不是账号。存储把它列成可查询的一列，但**不许**拿它当主键 ——
    那会把「同一个 OSM 节点下的两个对象」折成一行。
    """
    store.upsert_asset(make_asset("v1", source_identity="osm:node/123"))
    store.upsert_asset(make_asset("v2", source_identity="osm:node/123"))
    assert [row.venue_asset_id for row in store.list_assets()] == ["v1", "v2"]
    assert store.get_asset("v1").source_identity == "osm:node/123"  # type: ignore[union-attr]
