"""``SqliteFeedbackStore`` 独有的一半：**盘上的形状**。

``tests/test_feedback_store_contract.py`` 那一份对两个实现一起跑，判的是端口保证的
那几件事。这一份判的全是**只有持久化实现做得出来**的：

1. 三张表的列与那一条唯一索引真的在盘上（**承重点 4 的落点**）；
2. 那一条唯一索引**自己会拒**，不只靠 Python 那一层判；
3. 一行写完、换一个 store 对象打开还在；
4. 候选的旧修订留在盘上（契约那一份判的是读得到，这里判的是**行数**）。

## 承重点 4 为什么要有第 2 条

``feedback_event`` 的 ``UNIQUE (workspace, principal, idempotency_key)`` 与
``PRIMARY KEY (feedback_id)`` 说的是同一件事 —— **只在这条原则成立时**：
``feedback_id`` 是那把键的哈希（``feedback_id_for`` 逐字）。有第 2 条在，那个
「同一件事」是一个可以被推翻的断言，而不是一句注释：一个手工造的 id 带着一把用过
的键会被**数据库**挡下来，而不是靠 Python 那一层去记得比对。
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from marketops.adapters.sqlite.feedback import SqliteFeedbackStore
from marketops.domain.errors import IdempotencyConflictError, StoreError
from marketops.domain.feedback import FeedbackEvent

UTC = timezone.utc
T0 = datetime(2026, 9, 29, 3, 0, tzinfo=UTC)
T1 = T0 + timedelta(hours=2)
WORKSPACE = "uk-acme-natural-grass"
RUN_REF = "run-2026-09-29-b"

#: 三张表各自欠着的那几列。**从端口读出来的，不是从 DDL 抄过来的** ——
#: 一个一个对过去，少一列就在这里红。
EVENT_COLUMNS = (
    "feedback_id",
    "workspace",
    "principal",
    "idempotency_key",
    "kind",
    "scope_kind",
    "scope_ref",
    "created_at",
    "payload",
)
CANDIDATE_COLUMNS = (
    "candidate_id",
    "revision",
    "workspace",
    "state",
    "updated_at",
    "payload",
)
PATCH_COLUMNS = (
    "patch_id",
    "revision",
    "workspace",
    "state",
    "review_case_id",
    "updated_at",
    "payload",
)


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    return tmp_path / "state" / "marketops.db"


@pytest.fixture
def store(db_path: Path) -> SqliteFeedbackStore:
    return SqliteFeedbackStore(db_path)


def _raw(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def _columns(db_path: Path, table: str) -> tuple[str, ...]:
    conn = _raw(db_path)
    try:
        return tuple(row["name"] for row in conn.execute(f"PRAGMA table_info({table})"))
    finally:
        conn.close()


def _event(
    *,
    feedback_id: str | None = None,
    idempotency_key: str = "key-1",
    words: str = "这一批的来源计划配得太宽了",
    at: datetime = T0,
) -> FeedbackEvent:
    from marketops.application.feedback import draft_feedback

    event = draft_feedback(
        workspace=WORKSPACE,
        principal="claude-control",
        idempotency_key=idempotency_key,
        kind="workstream_policy",  # type: ignore[arg-type]
        literal_feedback=words,
        run_ref=RUN_REF,
        at=at,
    )
    if feedback_id is None:
        return event
    #: 手工换一个 id —— 只有这一条测试会这么做，而它要的正是「绕过那条哈希」。
    return event.model_copy(update={"feedback_id": feedback_id})


# ================================================================== 一、盘上的形状
def test_the_feedback_table_carries_every_column_the_port_needs(db_path: Path) -> None:
    """缺一列就交回，不改 DDL —— 所以这一条在盘上把每一列都点一遍。"""
    SqliteFeedbackStore(db_path)
    missing = [name for name in EVENT_COLUMNS if name not in _columns(db_path, "feedback_event")]
    assert not missing, (
        f"feedback_event 缺这些列：{missing}。\n"
        "这三张表在 ``db.py`` 的 ``_TARGET_DDL`` 里，而那一份是 MAIN 独占的 —— "
        "缺列是交回，不是就地改。"
    )


def test_the_two_versioned_tables_carry_their_columns(db_path: Path) -> None:
    SqliteFeedbackStore(db_path)
    for table, expected in (
        ("experience_candidate", CANDIDATE_COLUMNS),
        ("policy_patch", PATCH_COLUMNS),
    ):
        missing = [name for name in expected if name not in _columns(db_path, table)]
        assert not missing, f"{table} 缺这些列：{missing}"


def test_the_idempotency_key_is_covered_by_a_unique_index(db_path: Path) -> None:
    """**承重点 4 的落点**：那一格是一个**唯一索引**，不是一段约定。

    这一条不是在读代码有没有写对，而是在读**盘上真的建了那条索引**：把
    ``UNIQUE (workspace, principal, idempotency_key)`` 从 DDL 里删掉，这一条会红，
    而其余每一条判据都照旧绿 —— 因为它们在 Python 那一层自己比对了内容。
    """
    SqliteFeedbackStore(db_path)
    conn = _raw(db_path)
    try:
        unique = [
            row["name"]
            for row in conn.execute("PRAGMA index_list(feedback_event)")
            if row["unique"]
        ]
        assert unique, "feedback_event 上一条唯一索引都没有 —— 幂等只剩 Python 那一层"
        covered = set()
        for index in unique:
            covered.update(
                row["name"]
                for row in conn.execute(f"PRAGMA index_info({index})")
            )
        assert {"workspace", "principal", "idempotency_key"} <= covered, (
            "唯一索引没有盖住 (workspace, principal, idempotency_key) 这三格 —— "
            f"它盖的是 {sorted(covered)}"
        )
    finally:
        conn.close()


def test_the_unique_index_refuses_a_second_id_for_the_same_key(db_path: Path) -> None:
    """**绕过哈希的那一刻，数据库自己会拒。**

    ``feedback_id`` 是那把键的哈希，所以正常情况下 ``PRIMARY KEY`` 与那条唯一索引是
    同一件事。这一条把那个前提拆掉：手工给一个**别的** id、带着**用过**的键。
    此时 ``INSERT ... ON CONFLICT DO NOTHING`` 什么都不做，而这一次调用**必须报出来**
    —— 静默返回「写成功了」会让调用方以为那句话记下了，而盘上那一行是别人的。

    （这正是「先读再写」与「写进去再读回来」两种写法的分界：后者在这里读得到
    ``None``，于是它报得出来；前者会在 ``IntegrityError`` 上把一个正常结局与一次
    真实冲突混成同一句错。）
    """
    store = SqliteFeedbackStore(db_path)
    mine = _event()
    store.admit_feedback(mine)

    smuggled = _event(feedback_id="fb-00000000deadbeef", at=T1)
    assert smuggled.feedback_id != mine.feedback_id
    with pytest.raises(StoreError):
        store.admit_feedback(smuggled)

    #: 而且一个字都没落下：那一把键仍然只有一行。
    conn = _raw(db_path)
    try:
        rows = conn.execute(
            "SELECT feedback_id FROM feedback_event "
            "WHERE workspace = ? AND principal = ? AND idempotency_key = ?",
            (WORKSPACE, "claude-control", "key-1"),
        ).fetchall()
    finally:
        conn.close()
    assert [row["feedback_id"] for row in rows] == [mine.feedback_id]


# ================================================================== 二、冲突的判词
def test_the_conflict_message_names_both_sentences(store: SqliteFeedbackStore) -> None:
    """主存那一侧的判词要点名**两句话** —— 读的人才知道该换哪一把键。

    这一条住在这一份而不是契约那一份里：判词的内容是主存的形状，不是端口保证的事。
    """
    store.admit_feedback(_event(words="来源计划配得太宽了"))
    with pytest.raises(IdempotencyConflictError) as caught:
        store.admit_feedback(_event(words="来源计划配得太窄了", at=T1))
    message = str(caught.value)
    assert "太宽" in message and "太窄" in message
    assert "key-1" in message
    assert "换一把幂等键" in message, "判词没有给出下一步"


# ================================================================== 三、跨 store 对象
def test_a_row_written_by_one_store_is_the_same_row_in_another(
    db_path: Path,
) -> None:
    """**持久化的全部理由**：幂等键在一次进程重启之后仍然认得出那条反馈。

    两个 ``SqliteFeedbackStore`` 对象对着**同一个** ``db_path`` —— 这正是两次工具
    调用之间真实发生的事。少了这一条，「重发拿回同一行」可以靠一个进程内的字典
    做到，而那种实现在重启之后会把同一条反馈记成第二条。
    """
    first = SqliteFeedbackStore(db_path)
    second = SqliteFeedbackStore(db_path)

    mine = _event()
    assert first.admit_feedback(mine).admitted is True

    replayed = second.admit_feedback(_event(at=T1))
    assert replayed.admitted is False
    assert replayed.event.created_at == T0, "重发把时刻改写了"
    assert second.list_feedback().matched == 1


def test_a_conflict_is_seen_across_store_objects_too(db_path: Path) -> None:
    """另一半：**冲突也在两个对象之间成立**（它靠的是盘上那条索引）。"""
    first = SqliteFeedbackStore(db_path)
    second = SqliteFeedbackStore(db_path)
    first.admit_feedback(_event(words="来源计划配得太宽了"))
    with pytest.raises(IdempotencyConflictError):
        second.admit_feedback(_event(words="来源计划配得太窄了", at=T1))


# ================================================================== 四、旧修订留在盘上
def test_the_older_revision_of_a_candidate_stays_on_disk(db_path: Path) -> None:
    """候选的旧修订是**另一行**，不是被覆盖的那一行。

    契约那一份判的是「按旧版读得到」；这一条判的是**它真的还在那三张表里**：
    ``revision`` 是主键的一半，所以「这条候选改过几次主意」是一句查得出来的话。
    """
    from marketops.application.feedback import draft_candidate
    from marketops.domain.feedback import RiskClass, Scope, ScopeKind

    store = SqliteFeedbackStore(db_path)
    first = draft_candidate(
        workspace=WORKSPACE,
        observation="同一族的来源计划连续三次配得过宽",
        generalises=Scope(kind=ScopeKind.SEGMENT, ref="segment-1"),
        risk=RiskClass.LOW,
        proposed_by="claude-control",
        source_feedback_ids=("fb-0000000000000000",),
        at=T0,
    )
    store.put_candidate(first)
    store.put_candidate(
        first.model_copy(
            update={
                "revision": 2,
                "generalises": Scope(kind=ScopeKind.SCENARIO, ref="scenario-1"),
                "updated_at": T1,
            }
        )
    )
    conn = _raw(db_path)
    try:
        rows = conn.execute(
            "SELECT revision FROM experience_candidate WHERE candidate_id = ? "
            "ORDER BY revision",
            (first.candidate_id,),
        ).fetchall()
    finally:
        conn.close()
    assert [row["revision"] for row in rows] == [1, 2]
