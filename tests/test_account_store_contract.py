"""``AccountPoolStore`` 契约：**同一组判据，对着两个实现各跑一遍**。

## 为什么两个实现

与 ``tests/test_asset_store_contract.py`` / ``tests/test_relation_store_contract.py``
逐字同一条理由：``ports/pool.py`` 抽出端口的理由是幂等边界不同（累积并修订，不是只增、
也不是乐观并发），但端口本身不构成契约 —— 构成契约的是**同一组判据同时看着两个实现**。

* ``adapters/sqlite/pool.py::SqliteAccountPoolStore`` —— 主存。**读旧行与写新行在同一个
  ``BEGIN IMMEDIATE`` 事务里。**
* ``adapters/memory.py::InMemoryAccountPoolStore`` —— 测试替身。**没有锁**，所以并发那一条
  不在共享契约里。

## 这一份独有的一段：**没有任何一条路能按域名找到账号**

§6 明令不得因为同域名 / 同名 / 近似地址合并两个实体，而这条判据最容易的失守方式不是
某一行代码写错了，是**多了一个入口**：一个 ``find_by_domain`` 会让「这两个账号是不是
同一个」在下游变成一个可以顺手问的问题，而它没有诚实的答案。所以下面有两条：

* 一条数据面的：「两个账号同域名、同名字、同地址，仍然是两行」（存储不许拿它们去撞）；
* 一条结构面的：``AccountPoolStore`` 的抽象方法**只有那三个**（接口上没有第二个问法）。

两条都要有：只有数据面那一条，加一个 ``find_by_domain`` 不会红；只有结构面那一条，
一个在 ``upsert_account`` 里偷偷按域名撞行的实现不会红。

## 两条判据**刻意不在这里**（与另外两份同一条处置）

1. 「写完重开一个 store 还能读到」只对持久化实现成立，那条在 ``tests/test_sqlite_pool.py``。
2. **并发**：把并发判据写进共享契约，就得给内存那份加锁 —— 那不是补齐契约，是把一个
   测试替身伪装成并发存储。

## 这里测的是**契约**，不是规则的细节

五条规则与那一条 ``StoreError`` 在 ``tests/test_application_accounts.py``。这一份只问
**存储有没有照规则办**，判据是一个**可辨认的**规则接进去之后还认不认得出来（见
``probe_merge``）。
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from marketops.adapters.memory import InMemoryAccountPoolStore
from marketops.adapters.sqlite.payload import dump_payload
from marketops.adapters.sqlite.pool import SqliteAccountPoolStore
from marketops.domain.account import MarketAccount
from marketops.domain.errors import StoreError
from marketops.ports.pool import AccountPoolStore

UTC = timezone.utc
T0 = datetime(2026, 9, 24, 1, 0, tzinfo=UTC)
WORKSPACE = "uk-acme-natural-grass"
OTHER_WORKSPACE = "uk-something-else"

#: ``probe_merge`` 留下的痕迹。取这个名字是为了让一次「接错了规则」的失败在断言输出里
#: 自解释 —— 它出现在证据清单里，而不是一个凭空多出来的字符串。
PROBE_MARK = "probe-fold"


def probe_merge(
    existing: MarketAccount | None, incoming: MarketAccount
) -> MarketAccount:
    """一条**看得出来有没有被执行**的规则。

    ``evidence_ids`` 里插一段记号：折两次之后它必须在场，且排在第一次那一段的后面。
    一个「直接把 incoming 覆盖写进去」的实现在单次写入上看起来完全正确，只有拿这条
    记号才认得出来（资产池那一份用的是 ``notes`` 前缀累加，理由同一：账号行上能留下
    痕迹的清单格有几个，而证据那一格是其中在所有输入下都非空的一个 —— 「没给证据的
    行造不出来」是模型自己的判据）。

    别的格子按「来行覆盖」处理，好让几条与规则无关的判据（多行、排序、工作区）不用去
    构造一份真规则的输入。
    """
    if existing is None:
        return incoming
    return MarketAccount.model_validate(
        {
            **incoming.model_dump(),
            "evidence_ids": [*existing.evidence_ids, PROBE_MARK, *incoming.evidence_ids],
        }
    )


@pytest.fixture(params=["sqlite", "memory"])
def store(request, tmp_path: Path) -> AccountPoolStore:
    """两个实现之一。名字进测试 id —— 红了的时候一眼看得见是哪一个。"""
    if request.param == "sqlite":
        return SqliteAccountPoolStore(tmp_path / "state" / "marketops.db", probe_merge)
    return InMemoryAccountPoolStore(probe_merge)


def make_account(**overrides) -> MarketAccount:
    payload = {
        "workspace": WORKSPACE,
        "account_id": "acct-1",
        "organisation": "Example Parish Council",
        "evidence_ids": ["ev-1"],
        "first_seen_at": T0,
    }
    payload.update(overrides)
    return MarketAccount(**payload)


# --- 折 -----------------------------------------------------------------------


def test_folding_the_same_id_twice_leaves_one_row(store: AccountPoolStore) -> None:
    """按键折，不按内容插 —— 行数就是「这个市场有多少个账号」。"""
    store.upsert_account(make_account(evidence_ids=["ev-1"]))
    store.upsert_account(make_account(evidence_ids=["ev-2"]))
    rows = store.list_accounts()
    assert len(rows) == 1, "折两次多出一行：报告上会把同一个账号数成两个"
    assert rows[0].account_id == "acct-1"


def test_the_rule_the_store_was_built_with_is_the_one_that_runs(store: AccountPoolStore) -> None:
    """存储必须**调用**接进来的规则，而不是自己折。"""
    store.upsert_account(make_account(evidence_ids=["ev-1"]))
    store.upsert_account(make_account(evidence_ids=["ev-2"]))
    assert store.get_account("acct-1").evidence_ids == ["ev-1", PROBE_MARK, "ev-2"]  # type: ignore[union-attr]


def test_upsert_returns_the_row_that_landed(store: AccountPoolStore) -> None:
    """返回的是**落盘后的那一行**，调用方不需要（也不该）再读一遍。

    再读一遍等于把「这一次带进来的证据并成了什么」这句答复建立在另一次快照上，而中间
    可能有人插进来（§7 的运行形态正是两个 worker 重叠）。
    """
    store.upsert_account(make_account(evidence_ids=["ev-1"]))
    returned = store.upsert_account(make_account(evidence_ids=["ev-2"]))
    assert returned.evidence_ids == ["ev-1", PROBE_MARK, "ev-2"]
    assert returned == store.get_account("acct-1")


def test_a_single_fold_is_the_row_itself(store: AccountPoolStore) -> None:
    """表里没有这一行时，折进去的就是它本身 —— 规则不该在第一次写入时改造它。"""
    incoming = make_account(organisation="Example PC")
    assert store.upsert_account(incoming) == incoming


# --- 没有一条路按域名（或名字、地址）认人 ----------------------------------------


def test_two_accounts_that_look_alike_are_two_rows(store: AccountPoolStore) -> None:
    """**§6 的核心一条**：同域名、同名字、同地址的两个账号仍然是两行。

    把它们读成「一次合并能遇到的问题」就等于让存储按域名认人 —— 而英国基层俱乐部大量
    挂在聚合域名上，两个不同的组织同域名是**常态**；反过来，同一个组织改过名、搬过地址
    也是常态。两边的误判方向相反，而这一层没有任何一列说得清「这两个域名背后是不是
    同一个法人」。

    ★ 这一条钉的是一个**不会被别的判据发现**的改法：在 ``upsert_account`` 里加一句
    「先按域名查一下，查到就折进去」。那样单看任何一条「折同一个 id」的用例都还是绿的。
    """
    store.upsert_account(
        make_account(
            account_id="acct-1",
            organisation="Example Parish Council",
            domains=["client05.example.org"],
            addresses=["Main Road, Example Town One"],
        )
    )
    store.upsert_account(
        make_account(
            account_id="acct-2",
            organisation="Example Parish Council",
            domains=["client05.example.org"],
            addresses=["Main Road, Example Town One"],
        )
    )
    assert [row.account_id for row in store.list_accounts()] == ["acct-1", "acct-2"]


def test_the_port_has_no_second_way_to_ask_for_an_account() -> None:
    """接口上**只有**三个抽象方法：折一行、按 id 取一行、取全部。

    ★ 这一条是「没有按域名找账号的入口」在**结构层面**的执行器。数据面那一条
    （``test_two_accounts_that_look_alike_are_two_rows``）挡不住「多一个入口」：加一个
    ``find_by_domain`` 不会让任何一条已有用例转红，它只会让下一个人顺手用上它。

    判据直接从类上取（``__abstractmethods__``），所以它**不需要**人抄一份方法名清单
    —— 抄下来的那份清单不会因为接口长了新方法而变红，而这一条会。
    """
    assert AccountPoolStore.__abstractmethods__ == {
        "__init__",
        "upsert_account",
        "get_account",
        "list_accounts",
    }, (
        "账号池的端口上多了一个问法。§6 明令不得因为同域名 / 同名 / 近似地址合并两个"
        "实体，而这条判据最容易的失守方式就是多一个入口 —— 一个新的读方法请先回答"
        "「它按什么认人」这个问题。"
    )


# --- 读：只有两个问法 ----------------------------------------------------------


def test_an_account_that_is_not_there_is_none_and_an_empty_tuple(
    store: AccountPoolStore,
) -> None:
    """没有这个 id 的行是 ``None``；池子是空的时候是空元组。两者都不是错误。

    它与「这个库坏了」必须分得开：一个刚建立的池子里一个账号都没有，那是一个正常的
    答案（§6 的第一次运行正是这样）。
    """
    assert store.get_account("nobody") is None
    assert store.list_accounts() == ()


def test_the_reads_are_ordered_by_the_account_id_not_by_insertion(
    store: AccountPoolStore,
) -> None:
    """顺序是承重的：报告里的截断丢的是尾部，顺序不定时 ``dropped_items`` 只是噪音。

    这一条同时钉住两件事：Python 那一份不许把顺序留给 dict 的迭代序，SQLite 那一份
    不许把顺序留给 SQL 的返回序 —— 两个实现必须在同一批行上给出同一个顺序。
    """
    for account_id in ("acct-3", "acct-1", "acct-2"):
        store.upsert_account(make_account(account_id=account_id))
    assert [row.account_id for row in store.list_accounts()] == ["acct-1", "acct-2", "acct-3"]


# --- 工作区 -------------------------------------------------------------------


def test_a_second_workspace_is_refused_rather_than_mixed_in(store: AccountPoolStore) -> None:
    """一个库只装一个工作区的产出（与另外两张表逐字同一条判据、同一条理由）。"""
    store.upsert_account(make_account())
    with pytest.raises(StoreError, match=WORKSPACE):
        store.upsert_account(make_account(workspace=OTHER_WORKSPACE))


def test_the_same_account_in_two_scenes_is_two_rows_not_a_merge(store: AccountPoolStore) -> None:
    """§6 的「同一个账号在两个 Scene 下是两行」在存储这一层的形态。

    这里只能测到存储这一半：第二个工作区被**拒绝**（一个库装一个工作区），所以真实形态
    是「两个库各一行」。写成一条判据是为了钉住**它没有被折成一行**的读法 —— 折了的话，
    第二个 Scene 的判定会把第一个 Scene 的证据吃掉，而报告上它看起来只是「这个账号的
    证据变多了」。而 §6 的另一半（客观事实可以照抄）在那边是**另一次写入**，不是共用
    一行 —— 那正是「一个 Scene 的结论不得渗进另一个」在这条链上唯一需要成立的事。
    """
    store.upsert_account(make_account(evidence_ids=["ev-scene-a"]))
    with pytest.raises(StoreError, match=WORKSPACE):
        store.upsert_account(
            make_account(workspace=OTHER_WORKSPACE, evidence_ids=["ev-scene-b"])
        )
    assert store.list_accounts()[0].evidence_ids == ["ev-scene-a"]


def test_a_row_from_another_workspace_is_an_error_even_if_the_key_matches(
    store: AccountPoolStore,
) -> None:
    """**不属于本库的行报错，不是被跳过，也不是 ``None``/空元组。**

    跳过它会让「这一行不属于你」与「没有这一行」在调用点长成同一个空结果 —— 越权读
    因此变成一次静默成功。两个读入口都要挡（少挡一个就是一条绕行路），而这一张表上
    这一条比另外两张更要紧：§6 的「客观事实可以复用」最容易被实现成「读另一个 Scene
    的行」，而那条路正是从「跳过」开始的。
    """
    store.upsert_account(make_account())
    _smuggle_foreign_row(store, make_account(workspace=OTHER_WORKSPACE))
    with pytest.raises(StoreError, match=OTHER_WORKSPACE):
        store.get_account("acct-1")
    with pytest.raises(StoreError, match=OTHER_WORKSPACE):
        store.list_accounts()


def _smuggle_foreign_row(store: AccountPoolStore, item: MarketAccount) -> None:
    """把**别人的**一行放进这个存储的盘上，绕过它自己的写路径（同另外两份）。

    两个实现只有**往哪放**的差别：SQLite 是直接写那一行（写它的那个进程没有我们的
    代码），内存实现是把手里的 model 塞进它的字典。用 ``INSERT OR REPLACE`` 而不是
    ``INSERT``：要摆出的状态是「**同一个主键**的那一行写着别人的工作区」，而那个键已经
    被本库自己写过一行了。
    """
    payload = dump_payload(item.model_dump(mode="json"))
    if isinstance(store, SqliteAccountPoolStore):
        conn = sqlite3.connect(store._path)
        try:
            conn.execute(
                "INSERT OR REPLACE INTO market_account "
                "(account_id, workspace, payload, stored_at) VALUES (?, ?, ?, ?)",
                (
                    item.account_id,
                    item.workspace,
                    payload,
                    "2026-09-24T00:00:00+00:00",
                ),
            )
            conn.commit()
        finally:
            conn.close()
        return
    store._accounts[item.account_id] = item  # type: ignore[attr-defined]


# --- 时刻与那一格的 ``None`` ----------------------------------------------------


def test_a_verification_time_survives_a_fold_and_an_offset_round_trip(
    store: AccountPoolStore,
) -> None:
    """``last_verified_at`` 的 ``None`` 与一个非 UTC 偏移都要原样回来。

    前一半是这一张表特有的一格：``None`` 说的是「从那以后没再看过」，而它是**可复核
    与否**的判据 —— 存储把它读成时间零点，等于把一个从没复核过的账号读成刚刚复核过，
    而下一个维护 tick 会整批跳过它。后一半是正对照：证明「时刻不被改写」不是因为两个
    实现恰好用了同一个时区才成立的。
    """
    plus_eight = timezone(timedelta(hours=8))
    at = datetime(2026, 9, 24, 9, 0, tzinfo=plus_eight)
    store.upsert_account(make_account())
    assert store.get_account("acct-1").last_verified_at is None  # type: ignore[union-attr]

    row = store.upsert_account(make_account(last_verified_at=at))
    assert row.last_verified_at == at
    assert store.get_account("acct-1").last_verified_at == at  # type: ignore[union-attr]
