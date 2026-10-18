"""``adapters/sqlite/review.py`` 独有的判据：**持久化、跨连接 CAS，与没有迁移的自愈**。

## 为什么这些不放进契约测试

与 ``tests/test_sqlite_scenes.py`` 同一个理由：契约测试
（``tests/test_review_store_contract.py``）跑的是「两个实现都该满足」的性质。下面这些
只有落盘实现能回答，而写进契约只会给它开一个「内存除外」的例外。

## 这一份里真正承重的是**跨连接**那一条

「两个人同时裁决同一件，只有一个赢」在**同一进程、同一个 store 对象**上也能满足，而
满足得很廉价：一个只比较内存里那份副本的实现照样通过。它成立的真实条件是两个写者
**各持一个连接**、各自提交，而数据库层挡住第二个 —— 所以这里每个写者是一个独立的
``SqliteReviewCaseStore``，对着同一个文件。

## 第三块：加这两张表**不需要迁移**

``db.py`` 的「DDL 只增不改 + 每次 ``ensure_schema`` 重放」换来的那件事，在这两张新表上
必须真的成立。判据不是「读一遍代码觉得对」，而是：把一个**没有**这两张表的库（只有别的
表、版本号已经是当前版本）交给这个 store，它要**自己**把表建回来，而库里原有的行一行
不动。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from marketops.adapters.sqlite import review as review_module
from marketops.adapters.sqlite.db import connect, ensure_schema, session
from marketops.adapters.sqlite.review import SqliteReviewCaseStore
from marketops.domain.errors import ReviewConflictError
from marketops.domain.review import ResolutionOutcome
from tests.test_review_store_contract import T0, answer, make_case


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    return tmp_path / "state" / "marketops.db"


# --- 持久化 -------------------------------------------------------------------


def test_a_case_and_its_history_survive_a_new_store(db_path: Path) -> None:
    """两半都要活过重启，而**两半是两种写法**：一件是会被覆盖的当前投影，一条是
    只增不改的历史。只验其中一半的实现会让另一半点不上名 —— 而它们在报告上是两句话
    （「这一件现在等谁」与「这件案件被改过几次」）。
    """
    case = make_case()
    SqliteReviewCaseStore(db_path).open_case(case)
    SqliteReviewCaseStore(db_path).record_resolution(answer(case), expected_version=1)

    fresh = SqliteReviewCaseStore(db_path)
    loaded = fresh.get_case(case.case_id)
    assert loaded is not None
    assert loaded.state is not case.state, "裁决过的案件不再停在 OPEN 上"
    assert loaded.version == 2, "每一次状态迁移把 version 推进一格"

    rows = fresh.list_resolutions(case.case_id)
    assert len(rows) == 1
    assert rows[0].outcome is ResolutionOutcome.CORRECT_OBJECT
    assert rows[0].by == "member-a"
    assert rows[0].at == answer(case).at, "时刻要逐字活下来（带时区，不归一化成 naive）"


def test_a_claim_survives_a_new_store(db_path: Path) -> None:
    """认领是**当前状态**，不是历史 —— 所以它活下来的形态是那一行上的两个字段。"""
    case = make_case()
    SqliteReviewCaseStore(db_path).open_case(case)
    SqliteReviewCaseStore(db_path).claim(
        case.case_id, by="claude-control", at=T0, expected_version=1
    )

    loaded = SqliteReviewCaseStore(db_path).get_case(case.case_id)
    assert loaded is not None
    assert loaded.claimed_by == "claude-control"
    assert loaded.version == 2


# --- 跨连接：CAS --------------------------------------------------------------


def test_two_connections_racing_one_decision_leave_exactly_one_winner(
    db_path: Path,
) -> None:
    """两个控制端各持一个连接，同时读到第 1 版、同时决定裁决。

    第二个必须撞在「盘上已经不是你以为的那一版」上。这条判据在**把版本比对放进内存**
    的实现里是通不过的：两个 store 各拿一个对象、各比各的副本，两边都「对」。

    而它通过与否决定了「两个人同时点裁决」会不会变成一次**没人看得见的丢失** ——
    不是报错，是后写的那个把前一个整个吃掉，而两个人都以为自己成功了。

    ## 输的那一次选的是 ``REOPEN``，这是刻意的

    它必须是**对赢家留下的那个状态合法**的一条决定（``CORRECT_OBJECT`` 把案件关掉，
    而 ``REOPEN`` 在一件关掉的案件上是合法的）。换成一个不合法的（比如 ``DISMISS``），
    这条测试会在**领域**那条判据上就红/绿 —— 于是它看起来在验并发的版本比对，实际上
    验的是 ``may_decide``，而把版本比对整段删掉它照样绿（实测过：这就是第一版的形态）。
    """
    case = make_case()
    SqliteReviewCaseStore(db_path).open_case(case)

    first = SqliteReviewCaseStore(db_path)
    second = SqliteReviewCaseStore(db_path)
    assert first.get_case(case.case_id).version == 1

    first.record_resolution(
        answer(case, ResolutionOutcome.CORRECT_OBJECT, n=1), expected_version=1
    )
    with pytest.raises(ReviewConflictError) as stale:
        second.record_resolution(
            answer(case, ResolutionOutcome.REOPEN, n=2), expected_version=1
        )

    message = str(stale.value)
    assert "第 2 版" in message and "第 1 版" in message, (
        "拒绝的理由必须是**版本对不上**，而不是「这条决定不合法」—— 后者会让这条判据"
        "在把版本比对删掉之后照样绿。\n" + message
    )

    after = SqliteReviewCaseStore(db_path)
    assert after.get_case(case.case_id).version == 2, "赢的那一次只推进一格，不是两格"
    rows = after.list_resolutions(case.case_id)
    assert [row.resolution_id for row in rows] == ["res-1"], (
        "输的那一次的裁决**一个字都没写** —— 半条历史比没有历史更难查：它让「这件案件"
        "被改过几次」比「它现在是什么状态」多一次。"
    )


def test_two_connections_racing_a_claim_leave_exactly_one_holder(db_path: Path) -> None:
    """两个人同时领走一件：后一个被拒，而不是静默顶掉前一个的认领。

    顶掉之后前一个人的屏幕上还写着「我在做这一件」——而盘上已经不是了。

    ## 这一条钉的是**结局**，不是版本比对那一条

    认领只对 ``OPEN`` 成立，所以第一个人领走之后，第二个**无论读的是哪一版**都会被
    ``claim`` 自己那条「已经是别人的」拒掉 —— 这一条因此验不到版本比对。写在这里是
    因为它验的是另一件事，而那件事也要有人守：后一个被拒之后，**赢的那个人的名字
    还在行上**（不是「两个人都没领成」，也不是「后一个赢了」）。

    「版本比对真的挡住了并发」由上面那条裁决的竞态来钉 —— 那条的输家选的是一条对
    当前状态**合法**的决定，所以唯一能拒它的就是版本。
    """
    case = make_case()
    SqliteReviewCaseStore(db_path).open_case(case)

    first = SqliteReviewCaseStore(db_path)
    second = SqliteReviewCaseStore(db_path)
    first.claim(case.case_id, by="claude-control", at=T0, expected_version=1)

    with pytest.raises(ReviewConflictError):
        second.claim(case.case_id, by="claude-control-2", at=T0, expected_version=1)

    loaded = SqliteReviewCaseStore(db_path).get_case(case.case_id)
    assert loaded.claimed_by == "claude-control"
    assert loaded.version == 2


# --- 加这两张表不需要迁移 ------------------------------------------------------


def test_the_two_tables_come_back_on_a_database_that_never_had_them(db_path: Path) -> None:
    """一个**没有**这两张表的库（版本号已经是当前版本）交给这个 store，它自己把表建回来。

    这是「DDL 只增不改」那句承诺在这两张表上的执行器：``ensure_schema`` 每次重放整份
    DDL，所以「往后加表」既不动版本号，也不需要一次迁移。判据必须**真的把表丢掉**再
    构造一次 store —— 只是「新建一个库能用」验不到这件事，新建的库走的是另一条路。

    同时钉住**原有的行一行不动**：自愈的是**缺的表**，不是一个会顺手重建库的开关。
    """
    conn = connect(db_path)
    ensure_schema(conn)
    conn.execute("DROP TABLE human_resolution")
    conn.execute("DROP TABLE review_case")
    conn.execute("CREATE TABLE keep_me (n INTEGER NOT NULL)")
    conn.execute("INSERT INTO keep_me (n) VALUES (7)")
    conn.close()

    case = make_case()
    store = SqliteReviewCaseStore(db_path)
    store.open_case(case)
    store.record_resolution(answer(case), expected_version=1)

    assert store.get_case(case.case_id) is not None
    assert len(store.list_resolutions(case.case_id)) == 1

    conn = connect(db_path)
    try:
        kept = conn.execute("SELECT n FROM keep_me").fetchall()
    finally:
        conn.close()
    assert [row["n"] for row in kept] == [7], "自愈只建缺的表，不碰别的"


# --- 决定与它欠下的下一步：同生共死 ----------------------------------------------


def test_a_resolution_whose_owe_fails_takes_the_decision_with_it(
    db_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """**决定、案件、欠账是一个事务里的三样东西。**

    §9 逐字要求「裁决后自动重评受影响对象」，而 ``ports/review.py`` 把「写在事务之前 /
    之后」两种错法都点过名。这条判据是那两句话在**主存**这一侧唯一的执行器：故障是
    注入的（正常路径写不出「一半成功」），而注入的这一句就是那个把 ``owe`` 挪出事务的
    实现会红的来源。

    只判「``record_resolution`` 抛了」是不够的：一个先写决定、再在事务之外欠账的实现
    同样会抛，而它留下的盘面是**决定在盘上、重评永远不会发生** —— 那一件案件在报告里
    显示成「已裁决、等着什么」，而盘上没有任何东西在等。

    ``jobs`` 那一侧有形状相同的一条（``test_an_outbox_failure_takes_the_job_row_with_it``）；
    这一条是它的**另一个端口**，而两个端口是同一个写入口的两个调用方。

    内存侧判不了这条（没有事务可回滚），所以它只在这里。
    """
    store = SqliteReviewCaseStore(db_path)
    case = store.open_case(make_case()).case
    resolution = answer(case, n=7)

    def _boom(*args, **kwargs) -> None:
        raise RuntimeError("注入：欠下下一步那一步失败了")

    monkeypatch.setattr(review_module, "owe", _boom)

    with pytest.raises(RuntimeError):
        store.record_resolution(resolution, expected_version=case.version)

    assert store.list_resolutions(case.case_id) == ()
    back = store.get_case(case.case_id)
    assert back is not None
    assert (back.version, back.state) == (case.version, case.state), (
        "被回滚的那一次不许留下任何痕迹 —— 案件被推进了，就会让人以为它成功了"
    )
    with session(db_path) as conn:
        (left,) = conn.execute("SELECT COUNT(*) AS n FROM continuation").fetchone()
    assert left == 0, "欠账那一步失败了，盘上却还留着那一行"
