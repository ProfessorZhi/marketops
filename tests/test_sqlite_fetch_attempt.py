"""``adapters/sqlite/fetch_attempt.py`` 独有的判据：**落盘、免迁移自愈、列与 payload 一致**。

## 为什么这些不放进契约测试

与 ``tests/test_sqlite_review.py`` 同一个理由：契约测试
（``tests/test_fetch_attempt_store_contract.py``）跑的是「两个实现都该满足」的性质。
下面这些都只有落盘实现能回答，写进契约只会给它开一个「内存除外」的例外。

## 这一份里真正承重的两条

1. **列与 payload 必须同刻度。** 这张表把 ``scope`` / ``url`` / ``started_at`` **同时**
   写进三列（给筛子用）和一整份 payload（给读的人用）。两份东西同刻度这件事没有任何
   东西在机制上保证它 —— 而不一致的表现不是报错，是
   ``list_attempts(url=X)`` 说没有、``get_attempt`` 说有。排查的人拿到的是两个互相
   否认的答案，并且都出自同一个库。内存那一份没有这个风险（它只有一份），所以这条
   只能在这里判。

2. **同一个 ``attempt_id`` 同时写两遍时只有一个赢。** ``ON CONFLICT DO NOTHING`` 之后
   的那次读回比对，只有真的落在**数据库**上才成立 —— 内存那份的实现是同一个 dict 上
   的比较，与并发无关。所以这里两个写者各持一个 ``SqliteFetchAttemptStore``。
"""

from __future__ import annotations

import sqlite3
from datetime import timedelta
from pathlib import Path

import pytest

from marketops.adapters.sqlite.db import (
    OWNED_TABLES,
    SCHEMA_VERSION,
    SCHEMA_VERSION_KEY,
    WORKSPACE_META_KEY,
    connect,
    get_meta,
    write_transaction,
)
from marketops.adapters.sqlite.fetch_attempt import SqliteFetchAttemptStore
from marketops.adapters.sqlite.payload import load_payload
from marketops.domain.errors import StoreError
from marketops.domain.util import to_utc
from tests.test_fetch_attempt_store_contract import T0, WORKSPACE, make_attempt


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    return tmp_path / "state" / "marketops.db"


# --- 持久化 -------------------------------------------------------------------
def test_a_written_attempt_survives_a_new_store(db_path: Path) -> None:
    """整条活过一个**新的 store 对象**。

    刻意挑 ``response_headers``（这张表上唯一一个非标量字段）与 ``started_at``
    一起断言：一个把它 `str()` 之后存进去的实现，读回来是一串
    ``"{'retry-after': '120'}"`` 而**照样能过** ``is not None`` 那类判据 ——
    直到有人去查「它当时有没有说 Retry-After」。
    """
    row = make_attempt()
    SqliteFetchAttemptStore(db_path).put_attempt(row)

    back = SqliteFetchAttemptStore(db_path).get_attempt(row.attempt_id)
    assert back is not None
    assert back == row, "整条逐格相等，而不只是 id 对得上"
    assert back.response_headers == {"retry-after": "120", "content-type": "text/html"}
    assert back.started_at == T0, "时刻要带着时区活下来（不归一化成 naive）"


# --- 列与 payload 同刻度 -------------------------------------------------------
def test_the_columns_say_the_same_thing_as_the_payload(db_path: Path) -> None:
    """**三列都判**，因为它们各自是一个筛子的刻度。

    ``scope`` / ``url`` / ``started_at`` 在盘上有两份：可筛的那一列，与读的人拿到的
    payload。缺任何一份这个存储都做不了它该做的事，所以这不是冗余 —— 也就没有任何
    机制保证两份**一致**。不一致的表现不是报错，是 ``list_attempts(url=X)`` 说没有、
    而 ``get_attempt`` 说有：同一个库给出两个互相否认的答案，排查的人拿到的是两句
    都出自这个库的话。

    判据直接读列，不走 store 的任何入口 —— 走入口就只能读到那份 payload，而它恰好
    是这条判据要**对照**的东西。
    """
    row = make_attempt()
    SqliteFetchAttemptStore(db_path).put_attempt(row)

    conn = sqlite3.connect(db_path)
    try:
        found = conn.execute(
            "SELECT workspace, scope, url, started_at, payload FROM fetch_attempt "
            "WHERE attempt_id = ?",
            (row.attempt_id,),
        ).fetchone()
    finally:
        conn.close()
    assert found is not None

    workspace, scope, url, started_at, payload = found
    assert workspace == row.workspace
    assert scope == row.scope
    assert url == row.url, "筛的是**问过的**那个地址，见契约里那条 url 判据"
    assert started_at == to_utc(row.started_at).isoformat(), (
        "``since`` 在 SQL 里对着这一列比，所以它必须是与 ``to_utc`` 同一个刻度的字符串"
        "（``+08:00`` 与 ``Z`` 两个串按字典序比会给出一个像答案的结果）"
    )
    assert load_payload(payload, row.attempt_id) == row.model_dump(mode="json")


# --- 跨连接：同一个 id 的冲突 ---------------------------------------------------
def test_two_connections_writing_one_attempt_leave_exactly_one_row(
    db_path: Path,
) -> None:
    """两个写者各持一个 store 对象，对同一个 ``attempt_id`` 说两套话。

    第二个必须撞在「盘上已经有一条，而它说的不一样」上。这一条在**把比对放进内存**
    的实现里通不过（两个对象各比各的副本，两边都「对」），所以它只能在这里判。

    而它通过与否决定了「同一个 id 被两个人写」会不会变成一次**没人看得见的替换** ——
    不是报错，是后写的那个把先前那次尝试的读数整个吃掉，而两个人都以为自己记上了。

    ## 输的那一次选的差异是 ``observed_status``

    它必须是**同一格**上的两个不同值：换成一个会改 ``attempt_id`` 的差异（比如
    ``url``）就不再是「同一个 id 两套话」，而是一条**新**记录 —— 那条路本来就该成功，
    于是这条判据会变成在验「能不能写第二条」，而把比对整段删掉它照样绿。
    """
    first_row = make_attempt(observed_status=200, error_code="")
    SqliteFetchAttemptStore(db_path).put_attempt(first_row)

    second = SqliteFetchAttemptStore(db_path)
    with pytest.raises(StoreError) as conflict:
        second.put_attempt(make_attempt(observed_status=None, error_code="ConnectTimeout"))
    assert "attempt_id" in str(conflict.value), (
        "拒的理由必须是**这个 id 上已经有别的内容**，而不是别的什么"
    )

    after = SqliteFetchAttemptStore(db_path)
    assert len(after.list_attempts()) == 1, (
        "输的那一次**一行都没写** —— 两行同 id 的账在报告上是「这个 URL 被试过两次」，"
        "而那两次其实是同一句话的两个版本"
    )
    survivor = after.get_attempt(first_row.attempt_id)
    assert survivor is not None and survivor.observed_status == 200


# --- 免迁移自愈 ---------------------------------------------------------------
def test_dropping_the_table_is_healed_by_the_next_store(db_path: Path) -> None:
    """整份 DDL 每次 ``ensure_schema`` 都重放，所以「盘上还没有这张表」自愈。

    这正是「加表不改 ``SCHEMA_VERSION``」那条规则换来的东西：一个已经跑着的库不需要
    一次迁移就能得到这张表 —— 而它救的不只是升级那一刻，手工 ``DROP``、半途而废的
    迁移、从别的快照恢复都落到这里。

    ## 自愈救的是**表**，不是**行**

    掉表当然带走了行，所以这条判据判的是「接下来还能记账」，不是「行还在」——
    写成后者的话它会因为一次真实的丢失而变绿，而那正是判据最不该做的事。
    同时钉住**别的表一行不动**：自愈的是缺的表，不是一个会顺手重建库的开关。
    """
    SqliteFetchAttemptStore(db_path).put_attempt(make_attempt())

    conn = connect(db_path)
    try:
        with conn:
            conn.execute("DROP TABLE fetch_attempt")  # 索引随表一起走
        conn.execute("CREATE TABLE keep_me (n INTEGER NOT NULL)")
        conn.execute("INSERT INTO keep_me (n) VALUES (7)")
    finally:
        conn.close()

    healed = SqliteFetchAttemptStore(db_path)
    assert healed.list_attempts() == [], "表在，只是没有行"
    fresh = make_attempt(url="https://example.co.uk/about")
    healed.put_attempt(fresh)
    assert [row.attempt_id for row in healed.list_attempts()] == [fresh.attempt_id]

    conn = connect(db_path)
    try:
        kept = conn.execute("SELECT n FROM keep_me").fetchall()
    finally:
        conn.close()
    assert [row["n"] for row in kept] == [7], "自愈只建缺的表，不碰别的"


def test_self_healing_does_not_move_the_schema_version(db_path: Path) -> None:
    """自愈**不是**一次迁移，所以版本号一格不动。

    这是上面那条的另一半，挡的是一个具体的诱惑：既然新加了一张表，顺手在自愈那条
    路径上也盖一次章看起来更「整齐」。代价是所有已经在跑的库会在下一次启动时被一个
    不是代码期望值的版本判掉，而 ``replay_ddl`` 本来就已经把表补上了。

    ## 它判的不是「有没有人改过 ``SCHEMA_VERSION``」

    改常量这件事判据管不着 —— 前后取值相同，都是常量。它判的是**自愈路径没有写版本**：
    掉表重放之后这个库仍然是它原来的那一版。
    """
    SqliteFetchAttemptStore(db_path)
    conn = connect(db_path)
    try:
        before = get_meta(conn, SCHEMA_VERSION_KEY)
    finally:
        conn.close()
    assert before == str(SCHEMA_VERSION)

    conn = connect(db_path)
    try:
        with conn, write_transaction(conn):
            conn.execute("DROP TABLE fetch_attempt")
    finally:
        conn.close()

    SqliteFetchAttemptStore(db_path)  # 重放 DDL

    conn = connect(db_path)
    try:
        assert get_meta(conn, SCHEMA_VERSION_KEY) == before
    finally:
        conn.close()


def test_the_three_filter_columns_are_indexed(db_path: Path) -> None:
    """三个筛子各有索引，而**这条判据挡的是有人顺手删掉它们**。

    删索引不会让任何一条行为判据转红：结果一样，只是慢。而这张表是这仓里唯一一张
    按「每一次尝试一行」增长的账 —— 它会长到别的表不会到的量级，而那时再发现
    「``since`` 是全表扫」已经是在一台跑不动的机器上。
    """
    SqliteFetchAttemptStore(db_path)
    conn = sqlite3.connect(db_path)
    try:
        names = {
            row[1] for row in conn.execute("PRAGMA index_list('fetch_attempt')").fetchall()
        }
    finally:
        conn.close()
    assert {"fetch_attempt_by_time", "fetch_attempt_by_scope", "fetch_attempt_by_url"} <= names, (
        f"三个筛选维度各一条索引。盘上现在有：{sorted(names)}"
    )


def test_the_table_carries_exactly_the_workspace_it_was_written_with(
    db_path: Path,
) -> None:
    """一个库一个工作区，而**这一张表是这句话的执行器**。

    直接写 SQL 读这一列，是为了钉住「有人拿 sqlite3 命令行查这张表时，「这一行是谁的」
    与代码读路径的答案相同」—— ``owned()`` 用的是 payload 里的 ``workspace``，与这一列
    是两处。
    """
    SqliteFetchAttemptStore(db_path).put_attempt(make_attempt())

    conn = sqlite3.connect(db_path)
    try:
        rows = conn.execute("SELECT DISTINCT workspace FROM fetch_attempt").fetchall()
    finally:
        conn.close()
    assert [row[0] for row in rows] == [WORKSPACE]


def test_a_fresh_store_refuses_a_second_workspace(db_path: Path) -> None:
    """绑定写在 ``schema_meta`` 上，所以它跨 store 对象成立。

    与「只在内存里记着绑定的实现」的区别就在这里：那种实现在**同一个对象**上也会拒绝
    第二个工作区，看起来完全正确 —— 直到进程重启，两个工作区的行混进同一个库。
    """
    SqliteFetchAttemptStore(db_path).put_attempt(make_attempt())

    other = make_attempt(
        workspace="uk-something-else",
        url="https://example.co.uk/about",
        started_at=T0 + timedelta(minutes=1),
    )
    with pytest.raises(StoreError, match=WORKSPACE):
        SqliteFetchAttemptStore(db_path).put_attempt(other)


# --- 这一类表在 db.OWNED_TABLES 里那一格 ---------------------------------------
def test_the_attempt_table_is_a_table_the_binding_rule_looks_at() -> None:
    """``fetch_attempt`` 必须在 ``db.OWNED_TABLES`` 里。

    与 ``tests/test_sqlite_route_tables.py`` / ``tests/test_sqlite_pool.py`` 那几条
    逐字同形，而且是**各测各的**：清单里加表是逐张加的，一条判据只看它自己那张表
    在不在 —— 「路线观察那张表在清单里」对这一张表一个字都没说。

    ## 少列一张表的后果**不是**「读出错行」

    读侧的 ``owned()`` 在每一行上照样成立（这一份里那两条越权读判据照样绿）。少列的
    后果是：**一个只有这张表里有行、却没有绑定的库不会被 ``ensure_schema`` 拒绝**，
    它会一路走到第一次写入，然后由**那一行自己的** ``workspace`` 把绑定盖上去。
    一个装着两个部署的行的库因此能被静静地读成「这些行都是你的」。
    """
    assert "fetch_attempt" in OWNED_TABLES


def test_a_row_alone_in_the_attempt_table_refuses_the_database(db_path: Path) -> None:
    """只有这一张表里有行、却没有工作区绑定的库**不被认领** —— 在**打开时**就拒绝。

    ★ 这个状态写路径构造不出来（第一行写入就会绑，且绑定与那一行在同一个写事务里），
    所以下面手工造它：先让库成型并写下一行，再把绑定删掉。而 ``fetch_attempt`` 是这时
    唯一有行的表（``put_attempt`` 只写它），所以不需要像按表分的那一份那样清空别的表
    —— 少列这张表时，下面这次构造会**成功**，一个混装过的库从此可以被一路打开到
    第一次读。
    """
    SqliteFetchAttemptStore(db_path).put_attempt(make_attempt())

    conn = connect(db_path)
    try:
        with conn, write_transaction(conn):
            conn.execute("DELETE FROM schema_meta WHERE key = ?", (WORKSPACE_META_KEY,))
    finally:
        conn.close()

    with pytest.raises(StoreError, match="工作区绑定"):
        SqliteFetchAttemptStore(db_path)


def test_a_bound_database_with_the_same_row_opens_fine(db_path: Path) -> None:
    """**正对照**：同一行、绑定在的时候库照常打开。

    没有这一条，上一条在「任何库都打不开」时也是绿的 —— 而那会把一次装配错误读成
    「这道判据活着」。
    """
    row = make_attempt()
    SqliteFetchAttemptStore(db_path).put_attempt(row)

    assert SqliteFetchAttemptStore(db_path).get_attempt(row.attempt_id) == row
