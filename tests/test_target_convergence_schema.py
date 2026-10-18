"""目标架构收敛那十五张表：**建出来了、归工作区管、而那几条约束真的在拦人**。

## 为什么这些必须在这里判，而不是在各切片自己的 store 测试里

各切片会写自己的 store contract test，但那些测的是「某个 store 会不会**先去查一查**
再决定」。这一份测的是更下面一层：**就算那个 store 忘了查，数据库会不会拦。**
两者的差别是承重的 —— 一组「先查后写」的实现各自都对，合起来仍然允许两个写者
同时通过那道查；而 ``_TARGET_DDL`` 里那几条 UNIQUE / partial index / 复合主键的
全部价值，就在于它们不依赖任何一处实现记得去查。

所以这一份里的每一条改动（把某个 UNIQUE 去掉、把 partial index 的 ``WHERE`` 拿掉、
把 ``attempt`` 从主键里删掉）都应该**只**让这一份转红。若它还让别处转红，说明那条
约束在两个层面被表达了两遍，而两遍里的任何一遍被删掉都会留下一个静默的窗口。

## 十五张表为什么全在 ``OWNED_TABLES`` 里

那一份清单喂给 ``_has_lineage_rows``：一个只有这张表里有行、却没有工作区绑定的库
会被 ``ensure_schema`` 拒绝。而这十五张**每一张都写着 ``workspace``**，所以「这些行
属于谁」对它们每一张都答得出 —— 也就都该进那份清单。少列一张的后果是具体的：
一个只被这一张表写过的库会被一路打开到第一次读。
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from marketops.adapters.sqlite.db import (
    OWNED_TABLES,
    SCHEMA_VERSION,
    SCHEMA_VERSION_KEY,
    bind_workspace,
    connect,
    ensure_schema,
    get_meta,
    workspace_binding,
    write_transaction,
)
from marketops.domain.errors import StoreError

#: 目标架构收敛这一轮新增的十五张表。
#:
#: 写成字面量而不是从 ``_TARGET_DDL`` 里正则抠出来：这一份判据要问的正是
#: 「那一份常量里到底有没有这十五个名字」，而一个从它自己派生的期望答不了这个问题。
#: 加表时这里要跟着加 —— 而加它就是一次要说理由的编辑。
NEW_TABLES: tuple[str, ...] = (
    "workstream",
    "launch_record",
    "effective_scene",
    "feedback_event",
    "experience_candidate",
    "policy_patch",
    "saved_view",
    "watch",
    "action_item",
    "schedule_definition",
    "schedule_fire",
    "notification_event",
    "notification_subscription",
    "delivery_receipt",
    "session_binding",
)

#: 建库之前就在、用来证明「重放 DDL 不碰别的表」的那一张。
#:
#: 挑 ``venue_asset`` 有两个理由：它在最早的 ``_DDL`` 里（不属于这一轮），
#: 且它的四列除 ``payload`` 外都 NOT NULL —— 一行插得进去就说明那几列真的都写了。
PREEXISTING_TABLE = "venue_asset"
PREEXISTING_ROW = {
    "venue_asset_id": "v1",
    "workspace": "w1",
    "source_identity": "src-1",
    "payload": "{}",
    "stored_at": "2026-09-29T00:00:00+00:00",
}


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    return tmp_path / "state" / "marketops.db"


def _open(db_path: Path) -> sqlite3.Connection:
    conn = connect(db_path)
    ensure_schema(conn)
    return conn


def _table_names(conn: sqlite3.Connection) -> set[str]:
    return {
        row["name"]
        for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
    }


def _insert(conn: sqlite3.Connection, table: str, **columns: object) -> None:
    """写一行，**顺带把工作区绑上**。

    绑定不是这一份判据要测的东西，但不绑就写不进去 —— ``ensure_schema`` 对一个
    「有行却没有绑定」的库当场拒绝（那个拒绝本身有自己的判据，在
    ``tests/test_sqlite_pool.py``）。所以这里走真实的写路径：
    :func:`bind_workspace` 与那一行在**同一个写事务**里，这正是生产路径做的事。
    """
    names = ", ".join(columns)
    marks = ", ".join("?" for _ in columns)
    with write_transaction(conn):
        workspace = columns.get("workspace")
        if isinstance(workspace, str) and workspace_binding(conn) is None:
            bind_workspace(conn, workspace, error=StoreError)
        conn.execute(
            f"INSERT INTO {table} ({names}) VALUES ({marks})",  # noqa: S608 —— 表名与列名都是本文件里的字面量
            tuple(columns.values()),
        )


# --- 建出来了 -----------------------------------------------------------------
def test_all_fifteen_target_tables_are_created(db_path: Path) -> None:
    """一次 ``ensure_schema`` 就该把十五张全建出来，不需要迁移、不需要版本号。"""
    conn = _open(db_path)
    try:
        present = _table_names(conn)
    finally:
        conn.close()
    assert set(NEW_TABLES) <= present, f"没建出来的：{sorted(set(NEW_TABLES) - present)}"


def test_loading_these_tables_does_not_move_the_schema_version(db_path: Path) -> None:
    """加表**不改版本号** —— 这正是「DDL 只增不改」那条规则换来的东西。

    这一条与 ``test_sqlite_fetch_attempt.py`` 里那条同形，但问的是同一件事的另一半：
    那条问的是「自愈不盖章」，这条问的是「这一轮的十五张表本身也不盖章」。
    盖章的代价是所有已经在跑的库会在下一次启动时被一个不是代码期望值的版本判掉，
    而 ``replay_ddl`` 本来就已经把表补上了。
    """
    conn = _open(db_path)
    try:
        assert get_meta(conn, SCHEMA_VERSION_KEY) == str(SCHEMA_VERSION)
    finally:
        conn.close()


def test_an_existing_database_grows_them_without_losing_what_it_had(db_path: Path) -> None:
    """**数据丢失那一类**：一个这一轮之前建的库，重放一次 DDL 就长出新表，旧行不动。

    构造方式刻意是「先建库、写一行、手工把新表全删掉、再打开一次」—— 那正是这一轮
    之前那个库的形状（十五张表都不存在，别的表里有真的行）。判据有两半，而两半都不
    可省：

    * 十五张表回来了（少一半的话，「旧库升级」这件事只做了一半）；
    * 旧行还在（只测前一半的话，一个 ``DROP TABLE`` 之后重建的实现能过 ——
      而那正是这条判据要挡的形态）。
    """
    conn = _open(db_path)
    try:
        _insert(conn, PREEXISTING_TABLE, **PREEXISTING_ROW)
        for table in NEW_TABLES:
            with write_transaction(conn):
                conn.execute(f"DROP TABLE {table}")  # noqa: S608
    finally:
        conn.close()

    conn = _open(db_path)
    try:
        assert set(NEW_TABLES) <= _table_names(conn), "旧库长不出新表 —— 那要一次真迁移"
        kept = [
            row["venue_asset_id"]
            for row in conn.execute(f"SELECT venue_asset_id FROM {PREEXISTING_TABLE}")  # noqa: S608
        ]
        assert kept == ["v1"], "重放 DDL 把别的表里的行弄丢了"
    finally:
        conn.close()


@pytest.mark.parametrize("table", NEW_TABLES)
def test_every_new_table_is_a_table_the_binding_rule_looks_at(table: str) -> None:
    """十五张**每一张**都在 ``OWNED_TABLES`` 里，因为每一张都写着 ``workspace``。

    这一条钉的是**清单本身**。漏列一张的后果是具体的：一个只被这一张表写过的库
    「这些行属于谁」就答不出来，而被一路打开到第一次读。

    ``product_version`` **刻意不在**那份清单里，因为它没有 ``workspace`` 列
    （一个获批产品版本可被两个 Scene 共用）。所以这一条不是「新表都该进去」的
    同义反复：它是「这十五张都带了工作区」这句话的执行器。
    """
    assert table in OWNED_TABLES, (
        f"{table} 不在 OWNED_TABLES 里 —— 它要么少了一列 workspace，"
        "要么这份清单漏了它；两种都要说清是哪种"
    )


@pytest.mark.parametrize("table", NEW_TABLES)
def test_every_new_table_really_has_a_workspace_column(db_path: Path, table: str) -> None:
    """清单说它们归工作区管，这一条问的是**表自己也这么说**。

    上面那条读的是那份清单（一份字面量），这一条读的是 ``PRAGMA table_info`` ——
    两半都要有。只有上面那条时，清单里写着一个名字而表没有那一列的话，
    ``_has_lineage_rows`` 会在**真正要读它**的时候才发现，而那已经是下一次部署了。
    """
    conn = _open(db_path)
    try:
        columns = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}  # noqa: S608
    finally:
        conn.close()
    assert "workspace" in columns, f"{table} 没有 workspace 列，却在 OWNED_TABLES 里"


# --- 那几条约束真的在拦人 ------------------------------------------------------
def test_a_launch_is_idempotent_at_the_database_not_at_the_caller(db_path: Path) -> None:
    """``launch_record`` 的 UNIQUE **就是**「重复同一请求回同一 launch，不重置预算」。

    判据不测任何一处实现有没有先去查 —— 它直接插两次。两半都要：

    * 同一个 ``(workspace, scene_id, scene_version, idempotency_key)`` 第二次**插不进去**；
    * 换一版声明（``scene_version`` 从 1 到 2）**插得进去** ——
      「换了一版再启动一次」是**另一次**启动，不是重复提交。
    """
    conn = _open(db_path)
    row = {
        "launch_id": "L1",
        "workspace": "w1",
        "scene_id": "s1",
        "scene_version": 1,
        "idempotency_key": "req-1",
        "created_at": "2026-09-29T00:00:00+00:00",
        "payload": "{}",
    }
    try:
        _insert(conn, "launch_record", **row)
        with pytest.raises(sqlite3.IntegrityError):
            _insert(conn, "launch_record", **{**row, "launch_id": "L2"})
        _insert(conn, "launch_record", **{**row, "launch_id": "L3", "scene_version": 2})
    finally:
        conn.close()


def test_an_action_item_is_unique_only_while_it_is_open(db_path: Path) -> None:
    """``action_item`` 的那条 partial index：**未结的只有一条，已结的可以任意多条**。

    没有它，每个 Run 都会新增一条「核实转介联系人」，而报告上看起来只是
    「待办多了一些」。这条判据同时钉住两个方向，因为只钉前一半的话，
    一个把状态过滤去掉的实现会把「同一个账号上做过三次」也变成重复 ——
    那样修出来的「修好」是把历史读成冲突。
    """
    conn = _open(db_path)
    base = {
        "revision": 1,
        "workspace": "w1",
        "scope_ref": "account:acme",
        "action_type": "verify_referral",
        "owner": None,
        "state": "open",
        "due_at": None,
        "expected_version": 0,
        "updated_at": "2026-09-29T00:00:00+00:00",
        "payload": "{}",
    }
    try:
        _insert(conn, "action_item", action_id="a1", **base)
        with pytest.raises(sqlite3.IntegrityError):
            _insert(conn, "action_item", action_id="a2", **base)

        # 换一个主体就插得进去 —— 索引按 (workspace, action_type, scope_ref) 算。
        _insert(conn, "action_item", action_id="a3", **{**base, "scope_ref": "account:other"})

        # 把第一条结掉，同一个主体上就可以再开一条。
        with write_transaction(conn):
            conn.execute("UPDATE action_item SET state = 'done' WHERE action_id = 'a1'")
        _insert(conn, "action_item", action_id="a4", **base)

        # 已结的可以有任意多条。
        with write_transaction(conn):
            conn.execute("UPDATE action_item SET state = 'done' WHERE action_id = 'a4'")
        _insert(conn, "action_item", action_id="a5", **base)
    finally:
        conn.close()


def test_a_fire_is_idempotent_per_schedule_revision(db_path: Path) -> None:
    """``schedule_fire`` 的复合主键 —— 两半，而第二半是容易漏的那一半。

    * 同一 ``(schedule_id, schedule_revision, fire_id)`` 插第二次失败 ⇒ 重复 fire 幂等；
    * 同一个 ``fire_id`` 换一版声明（``schedule_revision`` +1）插得进去 ⇒
      「改了声明再重放」不能被上一版那一行挡掉。

    少了第二半的实现在「改完调度第一次 fire」上会静默什么都不做，而报告上写的是
    「已经点过火了」。
    """
    conn = _open(db_path)
    row = {
        "schedule_id": "sch1",
        "schedule_revision": 1,
        "fire_id": "2026-09-29T06:00",
        "workspace": "w1",
        "fired_at": "2026-09-29T06:00:00+00:00",
        "outcome": "admitted",
        "payload": "{}",
    }
    try:
        _insert(conn, "schedule_fire", **row)
        with pytest.raises(sqlite3.IntegrityError):
            _insert(conn, "schedule_fire", **row)
        _insert(conn, "schedule_fire", **{**row, "schedule_revision": 2})
    finally:
        conn.close()


def test_one_business_change_is_one_event_at_the_database(db_path: Path) -> None:
    """``notification_event`` 的 ``UNIQUE (workspace, dedupe_key)``。

    「一个业务变化只生成一次；投给 N 个 sink 是 N 个 receipt，不是 N 次事件」——
    这条 UNIQUE 让「N 次业务事件」那种写法**根本落不了盘**，不管通知层怎么想。
    """
    conn = _open(db_path)
    row = {
        "workspace": "w1",
        "dedupe_key": "claim-withdrawn:claim-7",
        "kind": "claim_withdrawn",
        "severity": "warning",
        "occurred_at": "2026-09-29T00:00:00+00:00",
        "payload": "{}",
    }
    try:
        _insert(conn, "notification_event", event_id="e1", **row)
        with pytest.raises(sqlite3.IntegrityError):
            _insert(conn, "notification_event", event_id="e2", **row)
        _insert(conn, "notification_event", event_id="e3", **{**row, "dedupe_key": "other"})
    finally:
        conn.close()


def test_a_retry_is_a_new_receipt_row_and_never_an_overwrite(db_path: Path) -> None:
    """``delivery_receipt`` 的主键带 ``attempt``：重试**新增行**，不覆盖。

    覆盖写会让「第一次失败、第二次成功」在盘上只剩一个事实，而重试策略要读的
    恰恰是那个事实。所以这条判据问的是「两次尝试的行**同时**在」，不是
    「第二次写成功」—— 后者一个 upsert 也过。
    """
    conn = _open(db_path)
    row = {
        "event_id": "e1",
        "subscription_id": "sub1",
        "sink": "codex",
        "workspace": "w1",
        "recorded_at": "2026-09-29T00:00:00+00:00",
        "payload": "{}",
    }
    try:
        _insert(conn, "delivery_receipt", attempt=1, **row)
        _insert(conn, "delivery_receipt", attempt=2, **row)
        with pytest.raises(sqlite3.IntegrityError):
            _insert(conn, "delivery_receipt", attempt=2, **row)
        attempts = [
            r["attempt"]
            for r in conn.execute(
                "SELECT attempt FROM delivery_receipt WHERE event_id = 'e1' ORDER BY attempt"
            )
        ]
    finally:
        conn.close()
    assert attempts == [1, 2], "两次尝试必须同时留在盘上 —— 覆盖掉的那个是一次数据丢失"


def test_an_effective_scene_version_cannot_be_written_twice(db_path: Path) -> None:
    """``effective_scene`` 的主键 ``(scene_id, version)`` 是**不可变**的第一道载体。

    就地改写会让同一 ``(scene_id, version)`` 在两次部署之间是两份不同的东西，
    而引用它的 ``policy_versions`` 那些格子**一处都看不出来**。
    """
    conn = _open(db_path)
    row = {
        "scene_id": "s1",
        "version": 1,
        "workspace": "w1",
        "digest": "sha256:aaa",
        "created_at": "2026-09-29T00:00:00+00:00",
        "payload": "{}",
    }
    try:
        _insert(conn, "effective_scene", **row)
        with pytest.raises(sqlite3.IntegrityError):
            _insert(conn, "effective_scene", **{**row, "digest": "sha256:bbb"})
        _insert(conn, "effective_scene", **{**row, "version": 2})
    finally:
        conn.close()
