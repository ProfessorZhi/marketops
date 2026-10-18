"""``SqliteProductCatalog`` 的**落盘**判据 —— 跨连接、跨实例的那一半。

## 为什么另开一个文件，而不是加进 ``test_product_catalog_contract.py``

那份契约判据在**两个实现**上跑同一份（sqlite 与内存替身），所以它有意只问两个实现
都答得出来的事：幂等边界、冲突拒词、读回。而「再打开一次这个文件会怎样」是 sqlite
独有的问题 —— 内存替身没有文件，一个「重新构造」的用例在那里只能测到它自己的
``__init__``。

代价是真实的，而且已经收过一次账：契约文件的夹具**只构造一个实例**并用到底，于是
「第二次打开一个已经有行的库」这条路一条判据也没有。``offer_version`` 与
``product_line`` 的写入路径当时没有把库绑到工作区上（见
``adapters/sqlite/product_catalog.py::_insert_and_compare`` 那段），后果是**一个
Offer 写得进去、第二个写不进去**：第二个实例一构造就撞上 ``UNBOUND``（「有研究产出
却没有工作区绑定，只可能来自篡改」）—— 而那句话说的是一个与调用方做的事无关的故事。
发现它的是 ``tests/test_mcp_product_catalog.py``（那里每个工具调用都过一次组合根的
工厂，也就是真的「再打开一次」），而这一条被补在这里，因为它是**存储层**的问题。

## 判据分成两半：绑定该发生的、与绑定不该发生的

``product_version`` 那一半是 B01 ``:104`` 的：产品资料**不属于任何工作区**，所以
只写它不绑库。两半必须都在 —— 只判「Offer 绑上了」的话，一个把三张表一视同仁地绑
的实现会全绿，而它会拒绝「产品资料被两个工作区共用」那条路。
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import pytest

from marketops.adapters.sqlite.db import (
    SchemaStanding,
    ensure_schema,
    read_schema_state,
    session,
    workspace_binding,
)
from marketops.adapters.sqlite.product_catalog import SqliteProductCatalog
from marketops.domain.errors import StoreError
from marketops.domain.product import OfferVersion, ProductLine, ProductVersion

#: 两个工作区。**不是**真实 profile —— 这一层管的是「一个库一个主」，与哪个部署无关。
WORKSPACE = "probe-workspace"
OTHER_WORKSPACE = "probe-other-workspace"


def _offer(version: int = 1, *, offer_id: str = "probe-offer", workspace: str = WORKSPACE):
    return OfferVersion(
        offer_id=offer_id,
        version=version,
        workspace=workspace,
        name="探针方案",
        purpose="launch",
        owner="probe-owner",
    )


def _line(line_id: str = "probe-line", *, workspace: str = WORKSPACE) -> ProductLine:
    return ProductLine(line_id=line_id, workspace=workspace, name="探针产品线", owner="probe-owner")


def _version(version: int = 1, *, product_id: str = "probe-product") -> ProductVersion:
    return ProductVersion(
        product_id=product_id,
        version=version,
        display_name="探针产品",
        owner="probe-owner",
    )


@contextmanager
def _open(db_path: Path) -> Iterator[object]:
    """按**生产的那道门**打开这个库：先 ``ensure_schema``，再读。

    裸 ``session()`` 读不了这几格：``schema_meta`` 是 ``ensure_schema`` 建的，而在
    它建出来之前读会撞上 sqlite 的 ``no such table`` —— 那与「这个库没有绑定」是
    两件不同的事，而把两者读成同一件，正是这一层在别处已经拒绝过一次的形状。

    这也不掩盖这里要抓的那个缺陷：那一次故障报在**构造**上
    （``SqliteProductCatalog(db_path)`` 里的 ``ensure_schema`` 判 ``UNBOUND``），
    不在这几个读辅助里。
    """
    with session(db_path) as conn:
        ensure_schema(conn)
        yield conn


def _count(db_path: Path, table: str) -> int:
    with _open(db_path) as conn:
        row = conn.execute(f"SELECT COUNT(*) AS n FROM {table}").fetchone()
    return int(row["n"])


def _standing(db_path: Path) -> SchemaStanding:
    with _open(db_path) as conn:
        return read_schema_state(conn, where=str(db_path)).standing


def _binding(db_path: Path) -> str | None:
    with _open(db_path) as conn:
        return workspace_binding(conn)


def test_a_second_instance_over_the_same_file_can_keep_writing(tmp_path: Path) -> None:
    """第一个实例写下的那些行，**第二个实例**读得到、也还能接着写。

    这是「一个 Offer 写得进去、第二个写不进去」那条故障的正面判据。它同时断言两件
    事：第二个 ``(offer_id, version)`` 真的进去了（不是被静默吞掉），而第一个还在
    那里（幂等的读回不依赖那一次写入是否同源）。
    """
    db_path = tmp_path / "marketops.db"
    first = SqliteProductCatalog(db_path)
    first.put_offer(_offer(1))

    second = SqliteProductCatalog(db_path)
    second.put_offer(_offer(2))

    assert second.get_offer("probe-offer", 1) is not None, "第一个实例写的那一版还在"
    assert second.get_offer("probe-offer", 2) is not None
    assert _count(db_path, "offer_version") == 2


def test_the_first_owned_write_binds_the_file_and_leaves_it_current(tmp_path: Path) -> None:
    """第一行**有主**的写入把库绑到那个工作区，库的处境随之是 ``CURRENT``。

    断言 ``SchemaStanding`` 而不是只看绑定那一格：``UNBOUND`` 正是那次故障报出来的
    东西，而它是「有行 + 没绑定」两格合起来判的 —— 只判其中一格的话，一个把绑定写进
    去、行却没落的实现会绿。
    """
    db_path = tmp_path / "marketops.db"
    assert _binding(db_path) is None, "新库没有绑定"

    SqliteProductCatalog(db_path).put_line(_line())

    assert _binding(db_path) == WORKSPACE
    assert _standing(db_path) is SchemaStanding.CURRENT


def test_an_offer_of_another_workspace_is_refused_and_writes_nothing(tmp_path: Path) -> None:
    """库绑在 ``A`` 之后，属于 ``B`` 的那一行**进不去**，且一行都没多。

    「一行都没多」是这条判据的另一半：绑定与插入在同一个写事务里，绑不上时那一行
    还没写。分两次写（先插入再绑、或先绑再另起一个事务）时，这一格会留下半份产出
    —— 而它在报告上只是「多了一行」。
    """
    db_path = tmp_path / "marketops.db"
    catalog = SqliteProductCatalog(db_path)
    catalog.put_offer(_offer(1))

    with pytest.raises(StoreError) as caught:
        catalog.put_offer(_offer(1, offer_id="probe-other", workspace=OTHER_WORKSPACE))

    assert WORKSPACE in str(caught.value), "拒词要点名这个库绑在谁身上"
    assert OTHER_WORKSPACE in str(caught.value), "拒词要点名这一次写入属于谁"
    assert _count(db_path, "offer_version") == 1, "绑不上时那一行不许落"


def test_a_product_version_does_not_bind_the_file(tmp_path: Path) -> None:
    """只写产品资料**不绑库** —— 它不属于任何工作区（B01 ``:104``）。

    这条判据的反面就是那次故障：把三张表一视同仁地绑，会让「一个获批产品版本被两个
    Scene 共用」在盘上表达不出来 —— 第二个工作区的登记会撞上一条与它无关的绑定冲突，
    而那条冲突读起来像「这个库不是你的」。
    """
    db_path = tmp_path / "marketops.db"
    SqliteProductCatalog(db_path).put_version(_version())

    assert _count(db_path, "product_version") == 1
    assert _binding(db_path) is None, "产品资料没有工作区，所以它不建立绑定"
    assert _standing(db_path) is SchemaStanding.CURRENT, (
        "「有行却没绑定」只对有主的表成立 —— 产品资料那一张不在 OWNED_TABLES 里，"
        "所以这个库的处境仍然是 CURRENT，而不是 UNBOUND"
    )

    # 绑定的建立权仍然在**有主**的那一次写入手里，而不是被产品资料那次占掉。
    SqliteProductCatalog(db_path).put_offer(_offer())
    assert _binding(db_path) == WORKSPACE
