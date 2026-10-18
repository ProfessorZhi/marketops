"""``route_observation`` / ``route_rejection`` 各自在 ``db.OWNED_TABLES`` 里那一格。

## 为什么这两条**不能**并进 ``tests/test_sqlite_pool.py``

那里已经有三条同形的判据（``venue_asset`` / ``account_venue_relation`` /
``market_account``），而它们用的是**账号池那一族**的存储（``adapters/sqlite/pool.py``）。
这两张表在 ``adapters/sqlite/evidence_store.py`` 里，是**另一族**存储。

把这个文件**按表**分成两条而不是一条，理由与那里逐字相同、也是 ``db.py`` 那段注释
自己的话：清单里加表是逐张加的，一条判据只看它自己那张表在不在 —— 「路线拒收那张表
在清单里」这一件事对路线观察一个字都没说。

## 它守的是什么

``ensure_schema`` 对「有行却是谁的没答案」的库当场拒绝（``_has_lineage_rows`` 遍历
``OWNED_TABLES``）。少列一张表的后果**不是**「读出错行」—— ``owned`` 那道判据在每一
行上照样成立 —— 而是：**一个只有这张表里有行、却没有绑定的库不会被打开时拒绝**，
它会一路走到第一次写入，然后由**那一行自己的** ``workspace`` 把绑定盖上去。一个装着
两个部署的行的库因此能被静静地读成「这些行都是你的」。

这两张表是**后补进** ``OWNED_TABLES`` 的（v5 与 §B12 落地时漏了列）。所以下面每一条
都是**正反两面**：清单里有它（正面），以及少了这个表项时那条拒绝真的会发生（反面）。
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from marketops.adapters.sqlite.db import (
    OWNED_TABLES,
    WORKSPACE_META_KEY,
    bind_workspace,
    session,
    write_transaction,
)
from marketops.adapters.sqlite.evidence_store import SqliteEvidenceStore
from marketops.domain.errors import StoreError

UTC = timezone.utc
T0 = datetime(2026, 9, 26, 1, 0, tzinfo=UTC)
WORKSPACE = "uk-acme-natural-grass"

#: 每一张表一行**形状正确**的原始行 + 它的表名。
#:
#: 这两行不用真的模型构造：要触发的是 ``ensure_schema`` 的「有行却没绑定」，而它只问
#: ``SELECT 1 FROM <表> LIMIT 1``。用模型的构造器反而会把这两条判据与「那个模型的字段
#: 改了」绑在一起 —— 那是别的文件在管的事。
TABLE_ROWS: dict[str, tuple[str, tuple[str, ...]]] = {
    "route_observation": (
        "INSERT INTO route_observation "
        "(observation_id, canonical_key, account_id, workspace, observed_at, payload, stored_at)"
        " VALUES (?, ?, ?, ?, ?, ?, ?)",
        (
            "obs-0000000000000001",
            "route-0000000000000001",
            "acct-0000000000000001",
            WORKSPACE,
            T0.isoformat(),
            json.dumps({"observation_id": "obs-0000000000000001"}),
            T0.isoformat(),
        ),
    ),
    "route_rejection": (
        "INSERT INTO route_rejection "
        "(rejection_id, account_id, workspace, code, occurred_at, payload, stored_at)"
        " VALUES (?, ?, ?, ?, ?, ?, ?)",
        (
            "rej-0000000000000001",
            "acct-0000000000000001",
            WORKSPACE,
            "entry_elsewhere",
            T0.isoformat(),
            json.dumps({"rejection_id": "rej-0000000000000001"}),
            T0.isoformat(),
        ),
    ),
}


@pytest.mark.parametrize("table", sorted(TABLE_ROWS))
def test_the_route_table_is_a_table_the_binding_rule_looks_at(table: str) -> None:
    """这张表必须在 ``OWNED_TABLES`` 里。

    与 ``tests/test_sqlite_pool.py`` 那三条逐字同形，而且同样是**各测各的**。
    """
    assert table in OWNED_TABLES


@pytest.mark.parametrize("table", sorted(TABLE_ROWS))
def test_a_row_alone_in_the_route_table_refuses_the_database(tmp_path: Path, table: str) -> None:
    """只有这张表里有行、却没有工作区绑定的库**不被认领** —— 在**打开时**就拒绝。

    这个状态写路径构造不出来（第一行写入就会绑，且绑定与那一行在同一个写事务里），
    所以下面手工造它：先让库成型，删掉绑定，再把**另外那些表**清空、只留目标表这一行。
    ``ensure_schema`` 遍历 ``OWNED_TABLES``，所以它一定会在这一格上停住 —— 而清单里
    少列这张表时，下面这次构造会**成功**，一个混装过的库从此可以被一路打开到第一次读。

    ★ 反面是「清空别的表」这一步：不清的话，即使目标表没进清单，``evidence`` 或
    ``contact_route`` 里可能正好也有行，于是拒绝照样发生 —— 而这一条就测不出它要测
    的那一格了。所以下面显式把另外那几张 ``OWNED_TABLES`` 清空。
    """
    db_path = tmp_path / "state" / "marketops.db"
    # 先让 schema 成型：``session`` 自己**不建表**，而下面要写的是真表。
    SqliteEvidenceStore(db_path)
    with session(db_path) as conn, write_transaction(conn):
        statement, values = TABLE_ROWS[table]
        conn.execute(statement, values)
        for other in OWNED_TABLES:
            if other != table:
                conn.execute(f"DELETE FROM {other}")
        conn.execute("DELETE FROM schema_meta WHERE key = ?", (WORKSPACE_META_KEY,))

    with pytest.raises(StoreError, match="没有工作区绑定"):
        SqliteEvidenceStore(db_path)


@pytest.mark.parametrize("table", sorted(TABLE_ROWS))
def test_a_bound_database_with_the_same_row_opens_fine(tmp_path: Path, table: str) -> None:
    """**正对照**：同一行、绑定在的时候库照常打开。

    没有这一条，上一条在「任何库都打不开」时也是绿的 —— 而那会把一次装配错误读成
    「这道判据活着」。
    """
    db_path = tmp_path / "state" / "marketops.db"
    SqliteEvidenceStore(db_path)
    with session(db_path) as conn, write_transaction(conn):
        # 绑定要显式盖：它由**第一行写入**建立，而下面那一行是手工写进去的，走不到
        # 那条路径。绑定在的时候 ``ensure_schema`` 的那一支必须过得去。
        bind_workspace(conn, WORKSPACE, error=StoreError)
        statement, values = TABLE_ROWS[table]
        conn.execute(statement, values)

    store = SqliteEvidenceStore(db_path)
    assert store is not None
