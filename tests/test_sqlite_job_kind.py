"""``research_job.input_kind`` 那一格的词表与 :class:`JobInputKind` 是同一份东西。

## 这一条为什么必须存在（它是 ``db.JOB_TABLE_DDL`` 自己点名的）

那一格的 CHECK 里列的就是枚举的成员值，一个字都不多、一个字都不少。**这句话没有任何
执行器**的时候，「加一个成员」与「库认不认得那个成员」是两件要靠人记得的事 —— 而忘了
的代价恰恰是一次 CHECK 撞车：按新成员提交研究撞在一句 ``IntegrityError`` 上，报出来
像存储坏了，而事实是那张表不认识这一版代码的枚举。v5 → v6 那一步（``migrate.py``）就是
那次遗忘的补课：它的判据是「盘上的词表 ≠ ``JOB_TABLE_DDL`` 这一份」，而**这一条保证
「``JOB_TABLE_DDL`` 这一份 = 枚举」**。两半合起来才是那句「词表与枚举一起长」。

## 两个方向都要有判据，否则「相等」是空话

* **正向**：解析出来的词表 == 枚举的值，按序。
* **反向**：同一段解析跑在一份**少一个成员**的建表语句上，必须给出少一个的那个答案。
  少了这一半，一个恒返回枚举的解析器照样让上面那条绿 —— 而它会同时让 v5 → v6 那一步
  的判据变成「永远相等、永远不重建」。

## 为什么还要往真库里插一次

字符串相等只证明「这一段文本里写着这几个词」，不证明 **SQLite 读出来的也是这几个**。
所以这里把 ``JOB_TABLE_DDL`` 那一句**真的执行一次**，然后按枚举逐个插一行（都要成），
再插一个枚举里没有的（必须被拒）。判据是那两句话的行为，不是我对它们的复述。

## 解析器**只有一份**

``_admitted_kinds`` 住在 ``migrate.py`` 里（那一步用同一个函数读盘上的词表）。这里 import
一个下划线名是刻意的 —— 与 ``tests/test_sqlite_migrate.py`` 里 ``_VERSION_NOTES`` 那条
同一个理由：再写一份的话，两份会在某次改了 DDL 的写法之后分家，而分家的表现是「迁移
永远不重建，而这一条永远绿」。
"""

from __future__ import annotations

import sqlite3

import pytest

from marketops.adapters.sqlite.db import JOB_TABLE_BY_NAME, JOB_KEY_COLUMNS
from marketops.adapters.sqlite.migrate import _JOB_TABLE, _admitted_kinds
from marketops.domain.enums import JobInputKind

#: 这一版代码认得的那一份，按枚举声明的顺序。
DECLARED = tuple(kind.value for kind in JobInputKind)

#: 表名 → 建表语句。这一格的唯一一份（``db.JOB_TABLE_DDL``）。
DDL = JOB_TABLE_BY_NAME[_JOB_TABLE]

#: 每插一行要给的其余几列。**从建表语句里读出来**，不在这里照抄 —— 抄一份的话，
#: 某天加一列 NOT NULL 会让这一条以「NOT NULL 约束失败」红在一个与被测行为无关的理由上，
#: 而它要证的只是那一格词表。
_NOT_THE_VOCABULARY = (
    "job_id",  # 主键，每行不同
    "scene_id",
    "input_ref",
    "idempotency_key",
    "request_sha256",
    "state",
    "reason",
    "created_at",
    "updated_at",
)


def _columns(ddl: str) -> tuple[str, ...]:
    """这段建表语句里的列名，按写下来的顺序。

    用 SQLite 自己读，不写一个正则：这一句的执行结果才是判据（下面那几条要往这个形状里
    插行），而正则只是我的复述。
    """
    conn = sqlite3.connect(":memory:")
    try:
        conn.executescript(ddl)
        return tuple(
            str(row[1]) for row in conn.execute(f"PRAGMA table_info({_JOB_TABLE})").fetchall()
        )
    finally:
        conn.close()


def _insert(conn: sqlite3.Connection, *, key: str, input_kind: str) -> None:
    """按列名插一行。缺的列由 SQLite 自己报 —— 见 ``_NOT_THE_VOCABULARY`` 上一句。"""
    conn.execute(
        f"INSERT INTO {_JOB_TABLE} (job_id, scene_id, input_kind, input_ref,"  # noqa: S608
        " idempotency_key, request_sha256, state, reason, created_at, updated_at)"
        " VALUES (?, 'a-scene', ?, 'a-ref', ?, ?, 'queued', '',"
        " '2026-09-15T00:00:00+00:00', '2026-09-15T00:00:00+00:00')",
        (key, input_kind, f"key-{key}", "0" * 64),
    )


def test_the_declared_vocabulary_is_exactly_the_enum() -> None:
    """``JOB_TABLE_DDL`` 那一格列的词 == :class:`JobInputKind` 的值，按序，一个不多一个不少。

    按序比而不是按集合比：那一份字符串是要被人逐字读的，而「同一个集合、两种顺序」会让
    每一次读它的人重新排一遍 —— 读出来的东西与盘上的不同，是这一格最容易出的事。
    """
    assert _admitted_kinds(DDL) == DECLARED, (
        "``research_job.input_kind`` 那一格的词表与 JobInputKind 不一致。\n"
        "两者必须一起长：加了枚举成员就要改 db.JOB_TABLE_DDL，**并且**补一步迁移"
        "（``ALTER TABLE`` 改不了 CHECK，v5 → v6 走的就是「建新的 → 搬行 → 丢旧的 →"
        " 改名」）。只改这一边的话，按新成员提交研究会撞在一句 CHECK 上。"
    )


def test_the_parser_gives_the_same_answer_about_a_narrower_vocabulary() -> None:
    """同一段解析跑在一份**少一个成员**的词表上，必须给出少一个的那个答案。

    ## 为什么这一条是上面那一条的另一半

    没有它，「解析结果 == 枚举」可以由一个**恒返回枚举**的实现满足 —— 而那个实现会让
    v5 → v6 那一步的判据（「盘上的词 ≠ 这一份」）永远为假：迁移从此不再重建，而报告里
    每一次都写着「词表已经一致」。这条用例钉的是那个解析器**真的在读那张表**。

    v5 那一份就是今天这一次真实的遗忘：三个成员。所以这里的反面样本不是编的。

    ## 两句话都要钉：解析器**读成什么**，和那张表**收不收**

    只钉前一截的话，「v5 写不进 cohort」是一句我推出来的话；钉了后一截，它是这张表
    在 SQLite 里的行为。``tests/test_sqlite_migrate.py`` 里指向这一条的那句承诺正是后一截。
    """
    v5 = DDL.replace(
        "'source_receipt', 'known_accounts', 'discovery_scope',\n                         'cohort'",
        "'source_receipt', 'known_accounts', 'discovery_scope'",
    )
    assert v5 != DDL, "反面样本没有真的改动那句话 —— 这一条会变成一次同义反复"

    assert _admitted_kinds(v5) == (
        "source_receipt",
        "known_accounts",
        "discovery_scope",
    ), "少了一个成员的词表被读成了原来那一份"
    assert _admitted_kinds(v5) != DECLARED

    # 而这段建表语句**执行出来**的表，按 cohort 插不进去 —— 这就是 v5 那次事故的形状。
    conn = sqlite3.connect(":memory:")
    try:
        conn.executescript(v5)
        for ordinal, kind in enumerate(JobInputKind):
            if kind.value in _admitted_kinds(v5):
                _insert(conn, key=f"v5-{ordinal}", input_kind=kind.value)
            else:
                with pytest.raises(sqlite3.IntegrityError):
                    _insert(conn, key=f"v5-{ordinal}", input_kind=kind.value)
    finally:
        conn.close()

    # 而一段**没有**那一格 CHECK 的建表语句要读成「读不出来」，不是「读成空的」：
    # 后者会让「没有约束」与「约束里一个词都没有」不可区分，而两者在迁移那一步的
    # 处置完全不同（前者整份拒绝，后者是一次不必做的重建）。
    assert _admitted_kinds("CREATE TABLE research_job (job_id TEXT PRIMARY KEY)") is None


def test_the_declared_ddl_builds_a_table_that_takes_every_member_and_only_those() -> None:
    """把那一句**真的执行一次**：每一个成员都要插得进去，枚举里没有的必须被拒。

    字符串相等只说明这一段文本里写着这几个词；SQLite 读出来的那一份才是这张表的形状，
    而两者分家的表现是「词表看上去对、写进去还是撞 CHECK」。所以这一条走的是执行结果。
    """
    columns = _columns(DDL)
    assert set(columns) >= set(_NOT_THE_VOCABULARY) | {"input_kind"}, (
        f"这一格的列变了（现在有 {columns!r}）—— 插行那一段要跟着改。"
        "它是这一条自己的夹具，而不是被测行为。"
    )

    conn = sqlite3.connect(":memory:")
    try:
        conn.executescript(DDL)
        for ordinal, kind in enumerate(JobInputKind):
            _insert(conn, key=f"job-{ordinal}", input_kind=kind.value)
        assert conn.execute(
            f"SELECT COUNT(*) FROM {_JOB_TABLE}"  # noqa: S608
        ).fetchone()[0] == len(JobInputKind), "枚举的成员没有各插进去一行"

        # 枚举里没有的取值必须被拒 —— 而拒它的是**那一格 CHECK**，不是别的什么。
        with pytest.raises(sqlite3.IntegrityError) as refused:
            _insert(conn, key="job-nobody", input_kind="manual_domains")
        assert "CHECK" in str(refused.value).upper()
    finally:
        conn.close()


def test_the_key_column_the_migration_counts_rows_by_is_the_primary_key() -> None:
    """``JOB_KEY_COLUMNS`` 那一格是主键 —— 迁移按它逐行对账（「每一行都还在」）。

    这一条不在上面两条的射程里：那两条说的是 ``input_kind``，而重建唯一会丢的东西是**行**，
    「按哪一格数」写错了的表现是「核过了、没问题」—— 数的是另一列，而它每一行都不重样。
    所以它要么等于主键，要么这一格就是错的。
    """
    assert JOB_KEY_COLUMNS[_JOB_TABLE] == "job_id"
    assert _columns(DDL)[0] == JOB_KEY_COLUMNS[_JOB_TABLE], "主键不是第一列，或它换名字了"
