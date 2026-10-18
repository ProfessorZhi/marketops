"""``adapters/sqlite/scenes.py` 独有的判据：**持久化与跨连接**。

## 为什么这些不放进契约测试

契约测试（``tests/test_scene_store_contract.py``）跑的是「两个实现都该满足」的性质。
下面这些只有落盘实现能回答，而把它们写进契约就只会给它开一个「内存除外」的例外 ——
契约里出现第一个例外之后，第二个就不会有人再争论了。

## 这一份里真正承重的是**跨连接**那一条

CAS 与「不得重复计费」这两条判据在**同一进程、同一个 store 对象**上都能满足，而
满足得很廉价：一个只比较内存里那份副本的实现照样通过。它们成立的真实条件是两个
写者**各持一个连接**、各自提交，而数据库层挡住第二个 —— 所以这里的每个写者是一个
独立的 ``SqliteSceneStore`` / ``SqliteMaintenanceStore``，对着同一个文件。
"""

from __future__ import annotations

import sqlite3
from datetime import timedelta, timezone
from pathlib import Path

import pytest

from marketops.adapters.sqlite.db import connect, write_transaction
from marketops.adapters.sqlite.scenes import (
    SqliteMaintenanceStore,
    SqliteSceneStore,
)
from marketops.domain.errors import SceneConflictError, StoreError
from marketops.domain.maintenance import (
    MaintenanceState,
    RefreshPlan,
    claim_of,
    what_is_due,
)
from marketops.domain.scene import PauseReason, SceneState
from tests.test_scene_store_contract import (
    T0,
    WORKSPACE,
    make_observation,
    make_record,
    make_version,
)

UTC = timezone.utc


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    return tmp_path / "state" / "marketops.db"


# --- 持久化 -------------------------------------------------------------------


def test_versions_survive_a_new_store(db_path: Path) -> None:
    SqliteSceneStore(db_path).put_version(make_version())

    assert SqliteSceneStore(db_path).get_version("scene-a", 1) is not None


def test_records_survive_a_new_store(db_path: Path) -> None:
    SqliteSceneStore(db_path).put_record(make_record(), expected_revision=None)

    loaded = SqliteSceneStore(db_path).get_record("scene-a")
    assert loaded is not None
    assert loaded.workspace == WORKSPACE


def test_observations_survive_a_new_store(db_path: Path) -> None:
    SqliteMaintenanceStore(db_path).put_observation(make_observation())

    assert len(SqliteMaintenanceStore(db_path).observations(workspace=WORKSPACE)) == 1


def test_a_claim_survives_a_new_store(db_path: Path) -> None:
    """「重启不得重复计费」的一半：重启之后**看得见**那一行。"""
    claim = _claim(db_path)
    SqliteMaintenanceStore(db_path).claim(claim)

    assert len(SqliteMaintenanceStore(db_path).claims("scene-a")) == 1


# --- 跨连接：CAS --------------------------------------------------------------


def test_two_connections_racing_a_record_only_one_wins(db_path: Path) -> None:
    """两个控制端各持一个连接，同时读到第 0 版、同时决定写。

    第二个必须撞在 ``WHERE revision = 0`` 上 —— 这条判据在把比较放进内存的实现里
    是通不过的，而它通过与否决定了「两个人同时点 pause」会不会变成「两个人都成功」。
    """
    first = SqliteSceneStore(db_path)
    second = SqliteSceneStore(db_path)
    first.put_record(make_record(), expected_revision=None)

    paused = make_record(
        state=SceneState.PAUSED,
        pause_reason=PauseReason.OPERATOR,
        effective_version=1,
        revision=1,
    )
    archived = paused.model_copy(update={"state": SceneState.ARCHIVED})

    first.put_record(paused, expected_revision=0)
    with pytest.raises(SceneConflictError) as excinfo:
        second.put_record(archived, expected_revision=0)

    # 回执必须同时带**期望与实际**：只报「冲突了」的读的人无法判断是自己太慢
    # 还是对面在打架 —— 而这两种的处置不同（重读 vs 停机对账）。
    message = str(excinfo.value)
    assert "第 0 版" in message and "第 1 版" in message
    assert first.get_record("scene-a").state is paused.state


# --- 跨连接：回执 -------------------------------------------------------------


def test_two_connections_claiming_the_same_cell_write_one_row(db_path: Path) -> None:
    """两个驱动同时认领同一格：各自提交，只有一个写进去，且两次都读到同一行。

    这是「重启或重复 tick 不得重复计费」在**两个进程**上的样子 —— 而它成立的理由
    不是任何一方的自觉，是 ``(dedupe_key, attempt)`` 这个主键。

    两次调用带的是**同一个** ``T0``（``_claim`` 里写死的），这一条因此还钉住了
    「谁拿到了」的判据必须来自存储：比对 ``claimed_at`` 的判法在这里会给出两个
    ``True``，而那是两个执行者同时做同一格昂贵工作。
    """
    claim = _claim(db_path)
    first = SqliteMaintenanceStore(db_path)
    second = SqliteMaintenanceStore(db_path)

    got_first = first.claim(claim)
    got_second = second.claim(claim)

    assert got_first.won is True
    assert got_second.won is False
    assert got_first.claim.attempt == got_second.claim.attempt == 1
    assert got_first.claim.claimed_at == got_second.claim.claimed_at
    assert got_first.claim == got_second.claim
    assert len(second.claims("scene-a")) == 1


def test_a_retry_row_is_visible_from_another_connection(db_path: Path) -> None:
    """失败之后的第二次尝试是**新的一行**，而它在另一个连接里也数得出来。"""
    store = SqliteMaintenanceStore(db_path)
    claim = store.claim(_claim(db_path)).claim
    store.finish(
        claim.model_copy(
            update={
                "state": MaintenanceState.FAILED,
                "finished_at": T0 + timedelta(minutes=1),
                "failure": "抓取超时",
            }
        )
    )

    other = SqliteMaintenanceStore(db_path)
    outcome = other.claim(claim.model_copy(update={"claimed_at": T0 + timedelta(hours=1)}))
    assert outcome.won is True
    assert outcome.claim.attempt == 2
    assert [c.attempt for c in other.claims("scene-a")] == [1, 2]


# --- 列与 payload 的交叉校验 --------------------------------------------------


def test_a_tampered_record_column_is_refused(db_path: Path) -> None:
    """``revision`` 列是 CAS 的**判定依据**，不是索引。

    手工把列改成一个 payload 里没有的版本号，会让下一次乐观锁比较拿一个错的值去比
    —— 而它会**成功**。所以读回来时必须逐字段对一次，不一致就停机对账。
    """
    store = SqliteSceneStore(db_path)
    store.put_record(make_record(), expected_revision=None)

    with connect(db_path) as conn, write_transaction(conn):
        conn.execute("UPDATE scene SET revision = 9 WHERE scene_id = 'scene-a'")

    with pytest.raises(StoreError) as excinfo:
        store.get_record("scene-a")
    assert "revision" in str(excinfo.value)


def test_a_tampered_state_column_is_refused(db_path: Path) -> None:
    store = SqliteSceneStore(db_path)
    store.put_record(make_record(), expected_revision=None)

    with connect(db_path) as conn, write_transaction(conn):
        conn.execute("UPDATE scene SET state = 'active' WHERE scene_id = 'scene-a'")

    with pytest.raises(StoreError):
        store.get_record("scene-a")


def test_tampering_is_not_hidden_by_the_workspace_listing(db_path: Path) -> None:
    """``records()`` 与 ``get_record()`` 走的是同一个交叉校验 —— 否则「列一张表」
    会变成绕过检查的那条路。"""
    store = SqliteSceneStore(db_path)
    store.put_record(make_record(), expected_revision=None)

    with connect(db_path) as conn, write_transaction(conn):
        conn.execute("UPDATE scene SET latest_version = 4 WHERE scene_id = 'scene-a'")

    with pytest.raises(StoreError):
        store.records(workspace=WORKSPACE)


def test_a_corrupt_payload_is_refused_not_skipped(db_path: Path) -> None:
    """一行读不出来意味着库被外部改过或损坏。静默跳过会让「记录少了」看起来像
    「这个场景没找着」。"""
    store = SqliteSceneStore(db_path)
    store.put_record(make_record(), expected_revision=None)

    with connect(db_path) as conn, write_transaction(conn):
        conn.execute("UPDATE scene SET payload = '{ not json' WHERE scene_id = 'scene-a'")

    with pytest.raises(StoreError):
        store.get_record("scene-a")


def test_the_schema_version_mismatch_is_refused(tmp_path: Path) -> None:
    """比代码新的库不能被旧代码打开 —— 那会让旧代码按它不认识的形状读写。"""
    db_path = tmp_path / "state" / "marketops.db"
    SqliteSceneStore(db_path)  # 建库

    with connect(db_path) as conn, write_transaction(conn):
        conn.execute("UPDATE schema_meta SET value = '99' WHERE key = 'schema_version'")

    with pytest.raises(StoreError):
        SqliteSceneStore(db_path)


def test_the_claim_table_has_no_payload_column(db_path: Path) -> None:
    """回执表的**全部**字段都是列 —— 因为 CAS、去重与「失败过几次」都要按它们查。

    这条判据挡的是一个具体的诱惑：为了让读回来简单一点，再加一列 payload。那时同一
    条回执就有两个真相来源，而它们不一致时没有一处会红。
    """
    SqliteMaintenanceStore(db_path)  # 建库

    with connect(db_path) as conn:
        columns = {
            str(row["name"])
            for row in conn.execute("PRAGMA table_info(maintenance_claim)").fetchall()
        }
    assert "payload" not in columns
    assert {"dedupe_key", "attempt", "state", "claimed_at"} <= columns


# --- 工具 ---------------------------------------------------------------------


def _claim(db_path: Path):
    """走 ``what_is_due`` → ``claim_of`` 产生一条键是算出来的回执。

    ``db_path`` 不参与构造，留着是为了让每个调用点长得一样（它读的是同一个库）。
    """
    due = what_is_due(
        scene_id="scene-a",
        scene_version=1,
        plan=RefreshPlan.uncalibrated_default(),
        observations=(),
        signals=(),
        now=T0,
        subjects=(("asset", "asset-a"),),
    )[0]
    return claim_of(due, workspace=WORKSPACE, at=T0)


# sqlite3 的 OperationalError 必须在别处被翻译成 StoreError；这里断言一次，
# 免得上面那些 pytest.raises(StoreError) 因为「抛的是别的异常」而假绿。
def test_raw_sqlite_errors_are_not_what_we_assert_on(db_path: Path) -> None:
    assert not issubclass(StoreError, sqlite3.Error)
    assert not issubclass(sqlite3.Error, StoreError)
