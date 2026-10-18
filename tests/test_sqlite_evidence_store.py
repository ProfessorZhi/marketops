"""SQLite 证据存储**自己**的性质 —— 契约测不到的那些。

共享判据在 ``tests/test_evidence_store_contract.py``。留在这里的两条都跟「同一个
文件里还住着别人」有关：这是本实现与另外两个实现最大的结构差别，也是它唯一需要
额外被钉住的地方。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from marketops.adapters.sqlite.db import session
from marketops.adapters.sqlite.evidence_store import SqliteEvidenceStore
from marketops.adapters.sqlite.ledger import SqliteBudgetLedger
from marketops.adapters.sqlite.payload import dump_payload
from marketops.domain.enums import BudgetLine
from marketops.domain.errors import StoreError
from tests.test_evidence_store_contract import make_evidence

SCOPE = "acct-0001:run-a"


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    return tmp_path / "state" / "marketops.db"


def test_a_corrupted_payload_is_refused_not_skipped(db_path: Path) -> None:
    """一行读不出来时报警，而不是当作那条不存在。

    这个表只有本模块写，所以一行坏 JSON 意味着库被外部改过或损坏了。静默跳过会
    让「证据少了」表现为「这条流程没找到东西」—— 一个方向相反的结论。
    """
    store = SqliteEvidenceStore(db_path)
    store.put_evidence(make_evidence())

    with session(db_path) as conn:
        conn.execute("UPDATE evidence SET payload = ?", ("{ not json }",))

    with pytest.raises(StoreError, match="不是合法 JSON"):
        store.list_evidence("a1")


def test_a_payload_that_is_not_an_object_is_refused(db_path: Path) -> None:
    """合法 JSON 但形状不对，同样是坏数据 —— 直接交给 pydantic 会报得含糊得多。"""
    store = SqliteEvidenceStore(db_path)
    store.put_evidence(make_evidence())

    with session(db_path) as conn:
        conn.execute("UPDATE evidence SET payload = ?", (json.dumps([1, 2, 3]),))

    with pytest.raises(StoreError, match="不是对象"):
        store.list_evidence("a1")


def test_the_ledger_and_the_store_share_one_database_file(db_path: Path) -> None:
    """账本与证据**住在同一个文件里**（``Settings.database_path``）。

    这是刻意的：两者都是「重启之后还要接着用」的状态，分成两个文件会让「这次运行
    花了多少」与「它产出了什么」需要一个跨文件的事务才能一起成立。

    所以这里要钉住的是那个唯一的风险点 —— ``ensure_schema`` 每次都重放整份 DDL，
    而两个模块都会调它。重放必须是**纯追加**的：后进场的那个不能把先进场的表
    清掉或改掉。这条测试就是「它没有」的证据。
    """
    ledger = SqliteBudgetLedger(db_path)
    ledger.set_ceiling(SCOPE, BudgetLine.SEARCH, 4, source="frozen-spec")
    reserved = ledger.reserve(
        SCOPE, BudgetLine.SEARCH, 1, idempotency_key="q-001"
    )

    # 后进场：store 的构造会再跑一次 ensure_schema。
    store = SqliteEvidenceStore(db_path)
    store.put_evidence(make_evidence())

    assert [r.reservation_id for r in ledger.open_reservations(SCOPE)] == [
        reserved.reservation_id
    ]
    assert ledger.usage(SCOPE)[0].ceiling == 4
    assert len(store.list_evidence("a1")) == 1


def test_the_store_works_on_a_database_that_already_has_ledger_rows(db_path: Path) -> None:
    """反过来的方向：先进的是账本，后进的 store 也照样能建表、能写。"""
    SqliteBudgetLedger(db_path).set_ceiling(SCOPE, BudgetLine.SEARCH, 4, source="frozen-spec")

    store = SqliteEvidenceStore(db_path)
    store.put_evidence(make_evidence())

    assert len(store.list_evidence("a1")) == 1


def test_a_row_written_before_the_publication_time_existed_still_loads(
    db_path: Path,
) -> None:
    """§9·1 那一格**不是一次迁移**，而这条测试就是那句话的执行器。

    ``evidence`` 表只有四个列，契约字段全住 ``payload`` 那一列 JSON 里（见
    ``adapters/sqlite/db.py`` 的「为什么 payload 是一列 JSON 文本」）。所以
    「加一个字段」在存储层的样子是「新写的 payload 多一个键」，不是
    ``ALTER TABLE``，也不需要动 ``SCHEMA_VERSION``。

    上一版代码写出来的那一行就是**同一个 payload 去掉这个键**（当时的 model
    上没有它，``model_dump`` 自然不写）。这里把那一行原样放回去再读出来：必须
    读得出来，且新字段是明确的 ``None`` —— 而不是让「昨天的库今天读不动」等到
    部署那天才被发现。
    """
    store = SqliteEvidenceStore(db_path)
    evidence = make_evidence()
    store.put_evidence(evidence)

    old_payload = evidence.model_dump(mode="json")
    assert "source_published_at" in old_payload
    old_payload.pop("source_published_at")
    with session(db_path) as conn:
        conn.execute("UPDATE evidence SET payload = ?", (dump_payload(old_payload),))

    rows = store.list_evidence("a1")
    assert len(rows) == 1
    assert rows[0].source_published_at is None
    assert rows[0].literal_value == evidence.literal_value
