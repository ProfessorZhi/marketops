"""SQLite 账本**独有**的判据：并发、版本、以及一条把「会超支」复现出来的正对照。

契约判据（预留占额度、不自动退款、幂等键、科目与 scope 隔离……）在
``tests/test_budget_contract.py``，两个实现都要跑过。**不要**把那些搬回来：
搬回来意味着内存实现不再被检查，而「两个实现同一个契约」这句话就没有执行器了。

这里留下的三件事都只对**库文件**成立：

* 上限真的守得住 —— 靠 ``BEGIN IMMEDIATE``，靠的是 SQLite 的性质；
* 一份读-改-写**真的会超支** —— 上面那条的正对照；
* ``schema_version`` 不匹配时拒绝而不是自动迁移。

``InMemoryBudgetLedger`` 没有锁，也不打算有 —— 它不替并发正确性背书。
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from marketops.adapters.sqlite.db import connect
from marketops.adapters.sqlite.ledger import SqliteBudgetLedger
from marketops.domain.enums import BudgetLine
from marketops.domain.errors import BudgetExhaustedError, StoreError
from tests.test_budget_contract import SCOPE, SOURCE


@pytest.fixture
def ledger(tmp_path: Path) -> SqliteBudgetLedger:
    return SqliteBudgetLedger(tmp_path / "marketops.db")


# ----------------------------------------------------------------------
# 并发：上限真的守得住
# ----------------------------------------------------------------------
def test_concurrent_reservations_cannot_exceed_the_ceiling(tmp_path: Path) -> None:
    """真正的判据：20 个 worker 抢 5 个额度，恰好 5 个成功。

    每个线程用一个**自己的** ledger 实例 —— 没有共享的 Python 对象，状态只在
    文件里。这正是多 worker 的形状，也让「靠进程内缓存侥幸正确」不可能发生。
    """
    db_path = tmp_path / "marketops.db"
    SqliteBudgetLedger(db_path).set_ceiling(SCOPE, BudgetLine.SEARCH, 5, source=SOURCE)

    def attempt(index: int) -> bool:
        local = SqliteBudgetLedger(db_path)
        try:
            local.reserve(SCOPE, BudgetLine.SEARCH, 1, idempotency_key=f"w{index}")
        except BudgetExhaustedError:
            return False
        return True

    with ThreadPoolExecutor(max_workers=20) as pool:
        granted = sum(pool.map(attempt, range(20)))

    assert granted == 5, f"20 个 worker 抢 5 个额度，成功了 {granted} 个"
    assert ledger_held(db_path) == 5


def ledger_held(db_path: Path) -> int:
    """直接读库，绕开账本自己的算术 —— 免得「账本自认为没超」被当成没超。"""
    with connect(db_path) as conn:
        row = conn.execute(
            "SELECT COALESCE(SUM(units), 0) AS held FROM budget_reservation "
            "WHERE state IN ('spent', 'reserved')"
        ).fetchone()
    return int(row["held"])


def test_the_race_control_shows_what_the_transaction_prevents(tmp_path: Path) -> None:
    """正对照：证明「读-改-写」**真的**会超支，所以上面那条不是空跑。

    这是 ``db.py`` docstring 里那三行的忠实复现，只是把交错**确定化** ——
    不用线程去赌时序，而是按顺序摆出两个连接各自看到的世界：

    1. 连接 A 读到 ``held = 0``（不拿写锁，因为还没写）；
    2. 正常路径把上限用满（5）；
    3. 连接 A 按它手里那个过期的 ``held`` 把行写下去。

    结果 6 > 5。一个只断言「账本拒绝超支」的测试无法区分「因为原子」与
    「因为测试里没有并发」；这条能。
    """
    db_path = tmp_path / "marketops.db"
    ledger = SqliteBudgetLedger(db_path)
    ledger.set_ceiling(SCOPE, BudgetLine.SEARCH, 5, source=SOURCE)

    stale = connect(db_path)
    stale_held = stale.execute(
        "SELECT COALESCE(SUM(units), 0) AS held FROM budget_reservation "
        "WHERE scope = ? AND state IN ('spent', 'reserved')",
        (SCOPE,),
    ).fetchone()["held"]
    assert stale_held == 0

    for index in range(5):
        ledger.reserve(SCOPE, BudgetLine.SEARCH, 1, idempotency_key=f"ok-{index}")

    stale.execute(
        "INSERT INTO budget_reservation "
        "(reservation_id, scope, line, units, state, idempotency_key, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        ("f" * 32, SCOPE, BudgetLine.SEARCH.value, 1, "reserved", "stale", "2026-01-01T00:00:00+00:00"),
    )
    stale.close()

    assert ledger_held(db_path) == 6, "读-改-写本该超支；没超支说明这条对照没在测它想测的东西"
    assert ledger.remaining(SCOPE, BudgetLine.SEARCH) == 0


# ----------------------------------------------------------------------
# 版本与迁移
# ----------------------------------------------------------------------
def test_a_schema_version_mismatch_is_refused_not_migrated(tmp_path: Path) -> None:
    """不做自动迁移是决定，不是遗漏 —— 迁移方向、旧库去留是人的决定。"""
    db_path = tmp_path / "marketops.db"
    SqliteBudgetLedger(db_path)

    with connect(db_path) as conn:
        conn.execute("UPDATE schema_meta SET value = '999' WHERE key = 'schema_version'")

    with pytest.raises(StoreError):
        SqliteBudgetLedger(db_path)
