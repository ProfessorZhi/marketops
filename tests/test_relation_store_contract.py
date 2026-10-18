"""``AccountVenueRelationStore`` 契约：**同一组判据，对着两个实现各跑一遍**。

## 为什么两个实现

与 ``tests/test_asset_store_contract.py`` 逐字同一条理由：``ports/pool.py`` 抽出端口的
理由是幂等边界不同（累积并修订，不是只增、也不是乐观并发），但端口本身不构成契约 ——
构成契约的是**同一组判据同时看着两个实现**。

* ``adapters/sqlite/pool.py::SqliteAccountVenueRelationStore`` —— 主存。**读旧行与写新行
  在同一个 ``BEGIN IMMEDIATE`` 事务里。**
* ``adapters/memory.py::InMemoryAccountVenueRelationStore`` —— 测试替身。**没有锁**，
  所以并发那一条不在共享契约里。

## 与资产池那一份的差别只有一处，而它是这一张表的重点

池子的行数是「这个市场有多少块场地」，关系表的行数是「这个市场有多少条**判定**」——
而§5 要求同一个 Asset 可以同时有 ``owner`` / ``operator`` / ``maintainer`` /
``booking_controller`` / ``purchaser`` / ``other_responsible_party``，**不强制单选**。
所以「同一个账号 × 同一块场地」的两种关系在这里必须是**两行**，而
「同一个账号 × 同一块场地 × 同一种关系」在两个工作区里也必须是**两行**。
这两条是这一份独有的一半判据（§5 的「不得因为同域名/近似名字/邻近地址就合并两个实体」
在存储这一层的形态）。

## 两条判据**刻意不在这里**（与资产池那一份同一条处置）

1. 「写完重开一个 store 还能读到」只对持久化实现成立，那条在 ``tests/test_sqlite_pool.py``。
2. **并发**：把并发判据写进共享契约，就得给内存那份加锁 —— 那不是补齐契约，是把一个
   测试替身伪装成并发存储。

## 这里测的是**契约的两半**，不是规则的细节

三条规则（``first_seen_at`` 只往早走 / ``last_verified_at`` 只往晚走 / ``evidence_ids``
取并集）与那一条 ``StoreError`` 在 ``tests/test_application_relations.py``。这一份只问
**存储有没有照规则办**，判据是一个**可辨认的**规则接进去之后还认不认得出来（见
``probe_merge``）。
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from marketops.adapters.memory import InMemoryAccountVenueRelationStore
from marketops.adapters.sqlite.payload import dump_payload
from marketops.adapters.sqlite.pool import SqliteAccountVenueRelationStore
from marketops.domain.entities import AccountVenueRelation
from marketops.domain.enums import VenueRelation
from marketops.domain.errors import StoreError
from marketops.ports.pool import AccountVenueRelationStore

UTC = timezone.utc
T0 = datetime(2026, 9, 24, 1, 0, tzinfo=UTC)
WORKSPACE = "uk-acme-natural-grass"
OTHER_WORKSPACE = "uk-something-else"

#: ``probe_merge`` 留下的痕迹。取这个名字是为了让一次「接错了规则」的失败在断言
#: 输出里自解释 —— 它出现在证据清单里，而不是一个凭空多出来的数字。
PROBE_MARK = "probe-fold"


def probe_merge(
    existing: AccountVenueRelation | None, incoming: AccountVenueRelation
) -> AccountVenueRelation:
    """一条**看得出来有没有被执行**的规则。

    ``evidence_ids`` 里插一段记号：折两次之后它必须在场，且排在第一次那一段的后面。
    一个「直接把 incoming 覆盖写进去」的实现在单次写入上看起来完全正确，只有拿这条
    记号才认得出来（资产池那一份用的是 ``notes`` 前缀累加，理由是同一件事：关系表上
    没有自由文本格，证据清单是这一行上唯一能留下痕迹的地方）。

    别的格子按「来行覆盖」处理，好让几条与规则无关的判据（多行、排序、工作区）不用
    去构造一份真规则的输入。
    """
    if existing is None:
        return incoming
    return AccountVenueRelation.model_validate(
        {
            **incoming.model_dump(),
            "evidence_ids": [*existing.evidence_ids, PROBE_MARK, *incoming.evidence_ids],
        }
    )


@pytest.fixture(params=["sqlite", "memory"])
def store(request, tmp_path: Path) -> AccountVenueRelationStore:
    """两个实现之一。名字进测试 id —— 红了的时候一眼看得见是哪一个。"""
    if request.param == "sqlite":
        return SqliteAccountVenueRelationStore(tmp_path / "state" / "marketops.db", probe_merge)
    return InMemoryAccountVenueRelationStore(probe_merge)


def make_relation(**overrides) -> AccountVenueRelation:
    payload = {
        "workspace": WORKSPACE,
        "account_id": "acct-1",
        "venue_asset_id": "v1",
        "relation": VenueRelation.MAINTAINS,
        "evidence_ids": ["ev-1"],
        "first_seen_at": T0,
    }
    payload.update(overrides)
    return AccountVenueRelation(**payload)


# --- 折 -----------------------------------------------------------------------


def test_folding_the_same_key_twice_leaves_one_row(store: AccountVenueRelationStore) -> None:
    """按四段键折，不按内容插 —— 行数就是「这个市场有多少条责任判定」。"""
    store.upsert_relation(make_relation(evidence_ids=["ev-1"]))
    store.upsert_relation(make_relation(evidence_ids=["ev-2"]))
    rows = store.list_relations()
    assert len(rows) == 1, "折两次多出一行：报告上会把同一条判定数成两条"
    assert rows[0].relation_key() == f"{WORKSPACE}/acct-1/v1/maintains"


def test_the_rule_the_store_was_built_with_is_the_one_that_runs(
    store: AccountVenueRelationStore,
) -> None:
    """存储必须**调用**接进来的规则，而不是自己折。"""
    store.upsert_relation(make_relation(evidence_ids=["ev-1"]))
    store.upsert_relation(make_relation(evidence_ids=["ev-2"]))
    assert store.relations_for_asset("v1")[0].evidence_ids == ["ev-1", PROBE_MARK, "ev-2"]


def test_upsert_returns_the_row_that_landed(store: AccountVenueRelationStore) -> None:
    """返回的是**落盘后的那一行**，调用方不需要（也不该）再读一遍。

    关系表上这一条比资产池更要紧一点：调用方刚交出的是一次判定的**凭据**，而它要拿
    回去做的是「这一次带进来的证据并成了什么」—— 再读一遍等于把那句答复建立在另一次
    快照上。
    """
    store.upsert_relation(make_relation(evidence_ids=["ev-1"]))
    returned = store.upsert_relation(make_relation(evidence_ids=["ev-2"]))
    assert returned.evidence_ids == ["ev-1", PROBE_MARK, "ev-2"]
    assert returned == store.relations_for_asset("v1")[0]


def test_a_single_fold_is_the_row_itself(store: AccountVenueRelationStore) -> None:
    """表里没有这一行时，折进去的就是它本身 —— 规则不该在第一次写入时改造它。"""
    relation = make_relation(relation=VenueRelation.UNRESOLVED)
    assert store.upsert_relation(relation) == relation


# --- 键的四段，逐段测 ----------------------------------------------------------


def test_two_relations_between_the_same_pair_are_two_rows(
    store: AccountVenueRelationStore,
) -> None:
    """**§5 的核心一条**：一个账号可以对同一块场地同时「拥有」与「维护」。

    把它读成「一次合并能遇到的问题」就等于强制单选 —— 而两块关系都要在，它们说的
    不是同一件事（谁出的钱与谁剪的草）。
    """
    store.upsert_relation(make_relation(relation=VenueRelation.OWNS))
    store.upsert_relation(make_relation(relation=VenueRelation.MAINTAINS))
    assert [row.relation.value for row in store.relations_for_asset("v1")] == [
        "maintains",
        "owns",
    ]


def test_one_relation_held_by_two_accounts_is_two_rows(
    store: AccountVenueRelationStore,
) -> None:
    """同一块场地上两个账号都「维护」也是两行 —— 键里有账号那一段。

    这是 §5 里最容易滑过去的一半：真实世界里一块场地常有多个维护方（俱乐部 + 地方
    议会），而按「场地 + 关系」折会把第二个维护方**静默吃掉**。
    """
    store.upsert_relation(make_relation(account_id="acct-1"))
    store.upsert_relation(make_relation(account_id="acct-2"))
    assert [row.account_id for row in store.relations_for_asset("v1")] == ["acct-1", "acct-2"]


def test_two_assets_do_not_touch_each_other(store: AccountVenueRelationStore) -> None:
    store.upsert_relation(make_relation(venue_asset_id="v1"))
    store.upsert_relation(make_relation(venue_asset_id="v2"))
    assert [row.venue_asset_id for row in store.relations_for_account("acct-1")] == ["v1", "v2"]


# --- 读：三个不同的问法 --------------------------------------------------------


def test_the_three_reads_answer_three_questions(store: AccountVenueRelationStore) -> None:
    """三个读不是同一个读的三个参数值 —— 各自给出的行集合**不同**。

    这一条钉的是「写成 ``list_relations(account_id=None, venue_asset_id=None)`` 也能
    跑」那个替代方案：在它上面，下面三行会两次拿到同一个全集。
    """
    store.upsert_relation(make_relation(account_id="acct-1", venue_asset_id="v1"))
    store.upsert_relation(make_relation(account_id="acct-2", venue_asset_id="v1"))
    store.upsert_relation(make_relation(account_id="acct-2", venue_asset_id="v2"))

    assert len(store.list_relations()) == 3
    assert [row.venue_asset_id for row in store.relations_for_account("acct-2")] == ["v1", "v2"]
    assert [row.account_id for row in store.relations_for_asset("v1")] == ["acct-1", "acct-2"]


def test_the_reads_are_ordered_by_the_relation_key_not_by_insertion(
    store: AccountVenueRelationStore,
) -> None:
    """顺序是承重的：报告里的截断丢的是尾部，顺序不定时 ``dropped_items`` 只是噪音。

    排序键只有一处定义（``AccountVenueRelation.relation_key()``），两个实现都不许把
    顺序留给 dict 的迭代序或 SQL 的返回序 —— 这一条正是拿那一点互相钉的。
    """
    store.upsert_relation(make_relation(account_id="acct-3", venue_asset_id="v3"))
    store.upsert_relation(make_relation(account_id="acct-1", venue_asset_id="v1"))
    store.upsert_relation(make_relation(account_id="acct-2", venue_asset_id="v2"))
    keys = [row.relation_key() for row in store.list_relations()]
    assert keys == sorted(keys)
    assert keys == [
        f"{WORKSPACE}/acct-1/v1/maintains",
        f"{WORKSPACE}/acct-2/v2/maintains",
        f"{WORKSPACE}/acct-3/v3/maintains",
    ]


def test_an_account_with_no_relation_is_an_empty_tuple(store: AccountVenueRelationStore) -> None:
    """**一个账号一块场地都不负责是一个正常的答案**，不是一个错误。

    它与「这个库坏了」必须分得开：名单里的组织确实可能一块场地都不负责
    （``ResponsibleEntity`` 的类文档）。空元组说的是「还没有人判过」，**不是**
    「没人负责」—— 后者要有凭据，本层今天表达不出来。
    """
    assert store.relations_for_account("nobody") == ()
    assert store.relations_for_asset("nowhere") == ()
    assert store.list_relations() == ()


# --- 工作区 -------------------------------------------------------------------


def test_a_second_workspace_is_refused_rather_than_mixed_in(
    store: AccountVenueRelationStore,
) -> None:
    """一个库只装一个工作区的产出（与资产池逐字同一条判据、同一条理由）。"""
    store.upsert_relation(make_relation())
    with pytest.raises(StoreError, match=WORKSPACE):
        store.upsert_relation(make_relation(workspace=OTHER_WORKSPACE))


def test_the_same_relation_in_two_workspaces_is_two_rows_not_a_merge(
    store: AccountVenueRelationStore,
) -> None:
    """§5·6 的「同一个账号 × 同一块场地 × 同一种关系」在两个 Scene 下是两条判定。

    这里只能测到存储这一半：第二个工作区被**拒绝**（一个库装一个工作区），所以这一条
    的真实形态是「两个库各一行」。写成一条判据是为了钉住**它没有被折成一行**的读法
    —— 折了的话，第二个 Scene 的判定会把第一个 Scene 的证据吃掉，而报告上它看起来
    只是「这条关系的证据变多了」。
    """
    store.upsert_relation(make_relation(evidence_ids=["ev-scene-a"]))
    with pytest.raises(StoreError, match=WORKSPACE):
        store.upsert_relation(make_relation(workspace=OTHER_WORKSPACE, evidence_ids=["ev-scene-b"]))
    assert store.list_relations()[0].evidence_ids == ["ev-scene-a"]


def test_a_row_from_another_workspace_is_an_error_even_if_the_key_matches(
    store: AccountVenueRelationStore,
) -> None:
    """**不属于本库的行报错，不是被跳过，也不是空元组。**

    跳过它会让「这一行不属于你」与「没有这一行」在调用点长成同一个空结果 —— 越权读
    因此变成一次静默成功。三个读入口都要挡（少挡一个就是一条绕行路）。
    """
    store.upsert_relation(make_relation())
    _smuggle_foreign_row(store, make_relation(workspace=OTHER_WORKSPACE))
    with pytest.raises(StoreError, match=OTHER_WORKSPACE):
        store.relations_for_account("acct-1")
    with pytest.raises(StoreError, match=OTHER_WORKSPACE):
        store.relations_for_asset("v1")
    with pytest.raises(StoreError, match=OTHER_WORKSPACE):
        store.list_relations()


def _smuggle_foreign_row(store: AccountVenueRelationStore, item: AccountVenueRelation) -> None:
    """把**别人的**一行放进这个存储的盘上，绕过它自己的写路径（同资产池那一份）。

    两个实现只有**往哪放**的差别：SQLite 是直接写那一行（写它的那个进程没有我们的
    代码），内存实现是把手里的 model 塞进它的字典。用 ``INSERT OR REPLACE`` 而不是
    ``INSERT``：要摆出的状态是「**同一个四段键**的那一行写着别人的工作区」，而那个键
    已经被本库自己写过一行了。
    """
    payload = dump_payload(item.model_dump(mode="json"))
    if isinstance(store, SqliteAccountVenueRelationStore):
        conn = sqlite3.connect(store._path)
        try:
            conn.execute(
                "INSERT OR REPLACE INTO account_venue_relation "
                "(workspace, account_id, venue_asset_id, relation, payload, stored_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (
                    item.workspace,
                    item.account_id,
                    item.venue_asset_id,
                    item.relation.value,
                    payload,
                    "2026-09-24T00:00:00+00:00",
                ),
            )
            conn.commit()
        finally:
            conn.close()
        return
    store._relations[item.relation_key_parts()] = item  # type: ignore[attr-defined]


# --- 时刻 ---------------------------------------------------------------------


def test_a_verification_time_survives_a_fold_and_an_offset_round_trip(
    store: AccountVenueRelationStore,
) -> None:
    """``last_verified_at`` 的 ``None`` 与一个非 UTC 偏移都要原样回来。

    前一半是这一张表特有的一格：``None`` 说的是「从那以后没再看过」，而它是**可复核
    与否**的判据 —— 存储把它读成时间零点，等于把一条从没复核过的关系读成刚刚复核过。
    后一半是正对照（同资产池那一份）：证明「时刻不被改写」不是因为两个实现恰好用了
    同一个时区才成立的。
    """
    plus_eight = timezone(timedelta(hours=8))
    at = datetime(2026, 9, 24, 9, 0, tzinfo=plus_eight)
    store.upsert_relation(make_relation())
    assert store.relations_for_asset("v1")[0].last_verified_at is None

    row = store.upsert_relation(make_relation(last_verified_at=at))
    assert row.last_verified_at == at
    assert store.relations_for_asset("v1")[0].last_verified_at == at
