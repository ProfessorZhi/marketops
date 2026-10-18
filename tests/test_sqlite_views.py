"""``adapters/sqlite/views.py`` 独有的判据：**持久化、跨连接 CAS、判据住在索引上**。

## 为什么这些不放进契约测试

与 ``tests/test_sqlite_review.py`` 同一个理由：契约测试
（``tests/test_views_store_contract.py``）跑的是「两个实现都该满足」的性质。下面这些
只有落盘实现能回答，而写进契约只会给它开一个「内存除外」的例外。

## 这一份里承重的是**第三条**：判据住在索引上，不在应用代码里

``ports/views.py::ActionItemStore.create`` 逐字：「更要紧的是判据住在哪。一条写在应用
代码里的『先查一下』禁用的东西比它写出来的少得多 —— 它挡不住另一条路径、另一个进程、
或者一次手写的 SQL 在同一个主体上再插一条。约束挡得住所有四条路。」

所以这里**绕开 store**、用一条手写的 ``INSERT`` 去撞那条索引。内存替身那一份**故意
不做**这条判据 —— 它自己类文档里写了那条替换的代价，而它只能复现**答案**。

## 视图的名字判据**反过来**：它不在索引上

``db.py`` 那一格的注释逐字：「『当前版不重名』是 store 的判据，不是这里的一条唯一
索引」。所以这一份里有一条**否定**的判据：``saved_view`` 上没有一条盖住
``(workspace, owner, name)`` 的唯一索引。一条普通唯一索引会把**历史版本**一起挡掉，
于是改名在库上做不成 —— 而 ``tests/test_views_store_contract.py`` 里那条改名用例
会的红法看起来像「改名这条功能没实现」。
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from marketops.adapters.sqlite.db import (
    SCHEMA_VERSION,
    SchemaStanding,
    read_schema_state,
    session,
)
from marketops.adapters.sqlite.views import (
    SqliteActionItemStore,
    SqliteSavedViewStore,
    SqliteWatchStore,
)
from marketops.domain.action_item import (
    ActionItem,
    ActionItemState,
    ActionType,
    ScopeKind,
    scope_ref,
)
from marketops.domain.errors import SavedViewConflictError, StoreError
from marketops.domain.saved_view import ViewVisibility
from marketops.domain.watch import NotifyOn
from tests.test_views_store_contract import (
    OWNER,
    T0,
    WORKSPACE,
    make_view,
    make_watch,
)

UTC = timezone.utc
T_ITEM = datetime(2026, 9, 27, 1, 0, tzinfo=UTC)


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    return tmp_path / "state" / "marketops.db"


def _item(*, action_id: str) -> ActionItem:
    """一件形状正确的待办。**从域那一侧构造**（``ActionItem.opened``），
    不在这里手拼那一行 —— 手拼的 payload 与扁平列会在某一次改动后分家。"""
    return ActionItem.opened(
        action_id=action_id,
        workspace=WORKSPACE,
        scope_ref_value=scope_ref(ScopeKind.ACCOUNT, "test-account-001"),
        action_type=ActionType.VERIFY_REFERRAL_CONTACT,
        reason="这个账号的推荐联系人还没核实过。",
        owner=OWNER,
        at=T_ITEM,
    )


def _indexes(db_path: Path, table: str) -> list[tuple[str, bool, tuple[str, ...]]]:
    """``(索引名, 是不是唯一, 盖住哪几列)`` —— 读的是库自己说的话（``PRAGMA``）。

    列名要一起读：``PRIMARY KEY`` 会造一条自己长出来的索引（``sqlite_autoindex_*``），
    它也是唯一的 —— 一条只看「有没有唯一索引」的判据会被它骗过去。
    """
    with session(db_path) as conn:
        return [
            (
                row[1],
                bool(row[2]),
                tuple(
                    info[2]
                    for info in conn.execute(f"PRAGMA index_info({row[1]})").fetchall()
                ),
            )
            for row in conn.execute(f"PRAGMA index_list({table})").fetchall()
        ]


# --- 持久化 -------------------------------------------------------------------
def test_a_view_and_its_history_survive_a_new_store(db_path: Path) -> None:
    """两版都要活过重启，而**当前版是 max(revision)**，不是最后写进去的那一行。"""
    SqliteSavedViewStore(db_path).put_view(make_view(name="老名字"), expected_revision=None)
    SqliteSavedViewStore(db_path).put_view(
        make_view(name="新名字", revision=2, at=T0 + timedelta(minutes=1)),
        expected_revision=1,
    )
    fresh = SqliteSavedViewStore(db_path)
    current = fresh.current("view-accounts")
    assert current is not None
    assert (current.revision, current.name) == (2, "新名字")
    # 历史版本还在盘上（改名之后旧名字仍然可查）—— 而清单只回当前版。
    assert [v.revision for v in fresh.views(workspace=WORKSPACE)] == [2]


def test_a_watch_survives_and_due_reads_the_flat_columns(db_path: Path) -> None:
    SqliteWatchStore(db_path).put_watch(
        make_watch(next_due_at=T0), expected_revision=None
    )
    fresh = SqliteWatchStore(db_path)
    loaded = fresh.get_watch("watch-weekly")
    assert loaded is not None
    assert loaded.next_due_at == T0
    assert loaded.notify_on == (NotifyOn.CHANGED,)
    # 到期那一问走的是扁平列与 ``watch_by_due``，不是把每一行读出来在 Python 里筛。
    assert [w.watch_id for w in fresh.due_watches(workspace=WORKSPACE, at=T0)] == [
        "watch-weekly"
    ]


def test_a_resolved_item_survives_as_one_row(db_path: Path) -> None:
    """处置**就地更新**：重启之后那一件仍然只有一行，而且状态是处置后的那一个。

    追加版本的实现会让这里读出**两行**（旧的 ``open`` 与新的 ``done``），而
    ``items`` 只回当前版时又只剩一行 —— 于是这条判据的另一半（那条未结的槽还占着）
    只在**写**的时候现形：下一次 ``create`` 会吃到一个约束冲突。
    """
    store = SqliteActionItemStore(db_path)
    created = store.create(
        _item(action_id="act-1")
    )
    current = store.get("act-1")
    assert current is not None
    store.resolve(
        current.resolve(
            at=T0 + timedelta(minutes=1),
            state=ActionItemState.DONE,
            by=OWNER,
            expected_version=1,
            resolution="办理完毕。",
        ),
        expected_version=1,
    )

    fresh = SqliteActionItemStore(db_path)
    loaded = fresh.get("act-1")
    assert loaded is not None
    assert (loaded.revision, loaded.state) == (2, ActionItemState.DONE)
    with session(db_path) as conn:
        rows = conn.execute(
            "SELECT COUNT(*) FROM action_item WHERE action_id = ?", ("act-1",)
        ).fetchone()
    assert rows[0] == 1, "处置是就地更新，不是追加一版"

    # 而那个槽**空了出来**：同一主体上的下一件建得出来。
    assert fresh.create(_item(action_id="act-2")).revision_written == 1
    assert created.revision_written == 1


# --- 判据住在索引上 -----------------------------------------------------------
def test_the_open_slot_is_enforced_by_a_partial_unique_index(db_path: Path) -> None:
    """**绕开 store**，用一条手写的 ``INSERT`` 去撞那条索引。

    这条判据在应用代码里是写不出来的：一个「先查一下」的实现挡不住另一条路径、
    另一个进程、或者这一次手写的 SQL。看到它红了不要在这一层加一次查询 ——
    ``ports/views.py::ActionItemStore.create`` 逐字写了为什么判据必须住在约束上。
    """
    SqliteActionItemStore(db_path).create(_item(action_id="act-first"))

    with pytest.raises(sqlite3.IntegrityError) as exc:
        with session(db_path) as conn:
            conn.execute(
                "INSERT INTO action_item (action_id, revision, workspace, scope_ref, "
                "action_type, owner, state, due_at, expected_version, updated_at, payload) "
                "VALUES (?, 1, ?, ?, ?, ?, 'open', NULL, 0, ?, '{}')",
                (
                    "act-handwritten",
                    WORKSPACE,
                    scope_ref(ScopeKind.ACCOUNT, "test-account-001"),
                    ActionType.VERIFY_REFERRAL_CONTACT.value,
                    OWNER,
                    T0.isoformat(),
                ),
            )
    # SQLite 报的是**撞了哪几列**（不是索引名），而这三列**只可能**来自那条索引 ——
    # 另一条唯一约束是主键 ``(action_id, revision)``，那两列在这里完全不同。
    message = str(exc.value)
    assert "action_item.workspace" in message
    assert "action_item.action_type" in message
    assert "action_item.scope_ref" in message


def test_that_index_is_declared_partial(db_path: Path) -> None:
    """``PRAGMA index_list`` 的 ``partial`` 那一列要说它是**部分**索引。

    这条不是上面那一条的复述：一个**不带** ``WHERE`` 的普通唯一索引也能让上面那条
    ``IntegrityError`` 成立，而它会让同一个账号上做过三次的「核实转介联系人」再也
    建不出第四次 —— 那时上面那条判据照样绿（撞索引的动作没变）。
    """
    SqliteActionItemStore(db_path)  # 建表
    with session(db_path) as conn:
        row = next(
            r
            for r in conn.execute("PRAGMA index_list(action_item)").fetchall()
            if r[1] == "action_item_open_by_subject"
        )
    # PRAGMA index_list 的列：seq, name, unique, origin, partial
    assert row[2] == 1, "它必须是唯一的"
    assert row[4] == 1, "它必须是**部分**的（带 WHERE）"


def test_terminal_rows_may_repeat_the_subject(db_path: Path) -> None:
    """**已结的可以有任意多条** —— 这一条也要绕开 store 验。

    ``WHERE`` 被去掉的变异会在这里红：那条手写的第二条 ``INSERT`` 会撞上约束。
    红了要去看的是 ``db.py`` 里那条 ``WHERE``（MAIN 独占的文件），
    **不是**在这一层加别的判据。
    """
    SqliteActionItemStore(db_path)
    statement = (
        "INSERT INTO action_item (action_id, revision, workspace, scope_ref, "
        "action_type, owner, state, due_at, expected_version, updated_at, payload) "
        "VALUES (?, 1, ?, ?, ?, ?, 'done', NULL, 0, ?, '{}')"
    )
    params = (
        WORKSPACE,
        scope_ref(ScopeKind.ACCOUNT, "test-account-001"),
        ActionType.VERIFY_REFERRAL_CONTACT.value,
        OWNER,
        T0.isoformat(),
    )
    with session(db_path) as conn:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute(statement, ("act-done-1",) + params)
        conn.execute(statement, ("act-done-2",) + params)
        conn.execute("COMMIT")
        rows = conn.execute(
            "SELECT COUNT(*) FROM action_item WHERE state = 'done'"
        ).fetchone()
    assert rows[0] == 2


def test_the_view_name_rule_is_not_a_unique_index(db_path: Path) -> None:
    """**否定**的判据：``saved_view`` 上没有盖住名字的唯一索引。

    名字判据住在 ``SqliteSavedViewStore._require_name_free``（一条带 ``max(revision)``
    子查询的 ``SELECT``），因为它的粒度是**当前版**。一条普通唯一索引会把历史版本一起
    挡掉，于是改名在库上做不成 —— 而那时 ``tests/test_views_store_contract.py``
    里那条改名用例的红法看起来像「改名这条功能没实现」。
    """
    SqliteSavedViewStore(db_path)
    name_columns = {"workspace", "owner", "name"}
    covering = [
        (name, unique, columns)
        for name, unique, columns in _indexes(db_path, "saved_view")
        if unique and name_columns <= set(columns)
    ]
    assert covering == [], (
        f"``saved_view`` 上出现了盖住 {sorted(name_columns)} 的唯一索引：{covering}。\n"
        "那条索引会把**历史版本**一起挡掉，于是改名在库上做不成 —— 而那时"
        " ``tests/test_views_store_contract.py`` 里那条改名用例的红法看起来像"
        "「改名这条功能没实现」。名字判据的粒度是**当前版**，它住在 store 里。"
    )
    # 而视图确实改得了名字（在库上真的做了那件事）。
    store = SqliteSavedViewStore(db_path)
    store.put_view(make_view(name="老名字"), expected_revision=None)
    assert (
        store.put_view(
            make_view(name="新名字", revision=2, at=T0 + timedelta(minutes=1)),
            expected_revision=1,
        ).view.name
        == "新名字"
    )


# --- 跨连接 CAS ---------------------------------------------------------------
def test_two_connections_racing_on_the_same_view_version_only_one_wins(
    db_path: Path,
) -> None:
    """两个写者**各持一个 store**（各持一个连接），都拿着基准 1 —— 只有一个能写第 2 版。

    同一进程、同一个 store 对象上也能满足这一条，而满足得很廉价：一个只比较内存里
    那份副本的实现照样通过。它成立的真实条件是两个写者各持一个连接、各自提交，
    而数据库层挡住第二个（``ports/views.py::SavedViewStore.put_view`` 逐字）。
    """
    SqliteSavedViewStore(db_path).put_view(make_view(), expected_revision=None)
    first = SqliteSavedViewStore(db_path)
    second = SqliteSavedViewStore(db_path)

    first.put_view(
        make_view(name="甲写的", revision=2, at=T0 + timedelta(minutes=1)),
        expected_revision=1,
    )
    with pytest.raises(SavedViewConflictError):
        second.put_view(
            make_view(name="乙写的", revision=2, at=T0 + timedelta(minutes=2)),
            expected_revision=1,
        )
    current = SqliteSavedViewStore(db_path).current("view-accounts")
    assert current is not None and current.name == "甲写的"


# --- 不需要迁移 ---------------------------------------------------------------
def test_a_dropped_table_heals_without_touching_the_others(db_path: Path) -> None:
    """``ensure_schema`` 每次重放整份 DDL —— 所以这三张表**不需要迁移**。

    判据是：把 ``saved_view`` 从库里拿掉，一个新 store 要自己把它建回来，
    而**别的表里的行一行不动**（``action_item`` 那一行还在）。
    """
    SqliteActionItemStore(db_path).create(_item(action_id="act-1"))
    SqliteSavedViewStore(db_path).put_view(make_view(), expected_revision=None)

    with session(db_path) as conn:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute("DROP TABLE saved_view")
        conn.execute("COMMIT")
        with pytest.raises(sqlite3.OperationalError):
            conn.execute("SELECT COUNT(*) FROM saved_view")

    healed = SqliteSavedViewStore(db_path)  # ``__init__`` 里 ensure_schema
    assert healed.views(workspace=WORKSPACE) == ()
    assert healed.put_view(make_view(), expected_revision=None).revision_written == 1
    assert SqliteActionItemStore(db_path).get("act-1") is not None


def test_one_database_serves_one_workspace(db_path: Path) -> None:
    """一个库只装一个工作区 —— 第二条工作区的写**拒绝**，而不是静静地混进去。"""
    store = SqliteSavedViewStore(db_path)
    store.put_view(make_view(), expected_revision=None)
    with pytest.raises(StoreError) as exc:
        SqliteSavedViewStore(db_path).put_view(
            make_view(view_id="view-other", workspace="uk-something-else"),
            expected_revision=None,
        )
    assert WORKSPACE in str(exc.value)
    # 而**读**另一条工作区是空，不是错：这不是一次写入事故。
    assert SqliteSavedViewStore(db_path).views(workspace="uk-something-else") == ()


def test_a_private_view_is_stored_and_read_back_as_private(db_path: Path) -> None:
    """可见性**存在盘上**（它在 payload 里，不是扁平列）—— 而读它的那一处是
    ``application/views.py::_run_one``（本文件不判那一条，只判它活过了重启）。"""
    SqliteSavedViewStore(db_path).put_view(
        make_view(visibility=ViewVisibility.PRIVATE), expected_revision=None
    )
    current = SqliteSavedViewStore(db_path).current("view-accounts")
    assert current is not None
    assert current.visibility is ViewVisibility.PRIVATE
    assert current.owner == OWNER


def test_the_followup_tables_do_not_need_a_schema_version_bump(db_path: Path) -> None:
    """这三张表加进来时 ``SCHEMA_VERSION`` **没有动** —— 判据是库自己说的话。

    一条动过版本号的历史版本会被旧代码判成「比代码新的库」（``ensure_schema``
    逐字：版本不做自动迁移），于是升级到这一版的人会打不开自己的库。
    """
    SqliteWatchStore(db_path)
    with session(db_path) as conn:
        state = read_schema_state(conn, where=str(db_path))
    assert state.standing is SchemaStanding.CURRENT
    assert state.recorded == SCHEMA_VERSION
