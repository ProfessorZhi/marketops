"""SQLite 的 ``ProductCatalog``。

## 这里唯一有判断的地方是三个 ``put_*``

``get_*`` 与 ``versions`` 是「一行 SELECT 再 ``model_validate``」，没有分支。
三个 ``put_*`` 有，而它们判的是同一件事：**同一个键再写一次，内容变了没有**。
形状与 ``scenes.py::put_version`` 逐字同源 —— ``ON CONFLICT DO NOTHING`` 加一次
读回比内容，**不是** ``INSERT OR REPLACE``：覆盖会让「有人改了已经发出去的那一版」
这件事成功，而它该失败。

## 为什么比整块 payload，不像 ``scene_version`` 那样比摘要

``SceneVersion`` 自带 ``frozen_sha256``，所以那边比摘要、报错时贴两个摘要前缀。
``ProductVersion`` / ``OfferVersion`` / ``ProductLine`` 都没有摘要字段，而给它们加
一个要动 b01-a 已经落盘的领域形状（那会连带领域判据一起改）。整块比在这里的代价是
一次 dict 比较，而报错时能直接列出**哪几格**不同 —— 比一个摘要前缀更有用。

## 为什么 ``product_version`` 的语句与另外两张不同

它没有 ``workspace`` 列。见 ``db.py`` 里 ``_PRODUCT_DDL`` 上那段：一个获批产品版本
可被两个工作区共用（B01 ``:104``），所以它不属于任何一个。三张表的写入因此共用同一个
helper（``_insert_and_compare``），只有列清单与主键在调用点说。

## 拼进 SQL 的表名与列名

它们是本模块里的字面量，没有一处来自调用方；值一律走占位符。
"""

from __future__ import annotations

from pathlib import Path

from ...domain.errors import StoreError
from ...domain.product import OfferVersion, ProductLine, ProductVersion
from ...ports.product_catalog import ProductCatalog
from .db import bind_workspace, ensure_schema, session, write_transaction
from .payload import dump_payload as _dump
from .payload import load_payload as _load


def _differences(stored: dict, incoming: dict) -> tuple[str, ...]:
    """两份 payload 里值不同的字段名，按名字排序。

    「哪几格不同」是这条报错唯一说得清的事。只说「冲突了」的回执，读的人手上没有
    任何线索 —— 而他要判断的是「是不是我自己改了什么」。
    """
    keys = set(stored) | set(incoming)
    return tuple(sorted(key for key in keys if stored.get(key) != incoming.get(key)))


def _conflict(*, table: str, subject: str, differing: tuple[str, ...]) -> StoreError:
    """同键不同内容的那条拒词。

    消息里必须点名 ``subject``（``product_id`` / ``offer_id`` / ``line_id`` 与版号）：
    这个异常类型是 :class:`~marketops.domain.errors.StoreError`，而它也承载「这个库
    打不开」。一条不分主语的冲突回执让两件处置相反的事共用一个名字 —— 判据正是靠
    「点名了没有」把两者分开的（``tests/test_product_catalog_contract.py`` 的三条
    冲突判据都断言主语在消息里）。
    """
    return StoreError(
        f"{table} 里 {subject} 已经有一份不同的内容，不同的格："
        f"{'、'.join(differing) or '（无法比对）'}。\n"
        "版号一经写下就不再变：每一处引用都只写 (id, 2)，而『第 2 版』在两次部署"
        "之间变成两份不同的东西时，**没有任何一处调用点**能看出来。不覆盖 —— "
        "要改内容就写下一个版号。"
    )


class SqliteProductCatalog(ProductCatalog):
    """产品版本、Offer 与产品线的 SQLite 实现。构造参数是 ``db_path``。

    与其余 SQLite 适配器同一个理由：存储必须能在没有配置、没有 ``os.environ`` 的
    情况下对着一个临时文件被测出来。建表在 ``__init__`` 里做（``ensure_schema``
    幂等）—— 构造即验证，一个不可写或版本不匹配的库在第一个调用点就报错。
    """

    def __init__(self, db_path: Path) -> None:
        self._path = db_path
        with session(self._path) as conn:
            ensure_schema(conn)

    # ------------------------------------------------------------ 共用的那一段

    def _insert_and_compare(
        self,
        *,
        table: str,
        columns: tuple[str, ...],
        values: tuple[object, ...],
        conflict_on: tuple[str, ...],
        where: str,
        where_values: tuple[object, ...],
        subject: str,
        payload: dict,
        workspace: str | None,
    ) -> None:
        """插入（同键不动），再读回来比内容，不同就报冲突。

        ``where`` 与 ``subject`` 是同一个键的两种说法：前者给 SQL，后者给报错。分开
        传是刻意的 —— 一个「用 SQL 片段当报错定位串」的写法在本仓已经出过一次
        （``payload.load_payload`` 的 ``where`` 参数是个定位串，不是 SQL）。

        ## ``workspace``：有主的那两张表要**顺手把库绑上**

        ``offer_version`` 与 ``product_line`` 的每一行都写着 ``workspace``，所以它们
        在 :data:`~marketops.adapters.sqlite.db.OWNED_TABLES` 里；``product_version``
        不在（它没有那一列）。有主 = 这个库是谁的状态有一个答案，而那个答案记在
        ``schema_meta.workspace`` 上，由**第一行写入**建立（``bind_workspace`` 的
        docstring 论证过为什么可以由第一行建立）。

        不在这里绑会发生什么（**实测过一次，就是这条判据把它抓出来的**）：行写进去了，
        绑定没有 —— 而这个库从此对每一次 ``ensure_schema`` 都答 ``UNBOUND``
        （「有研究产出却没有工作区绑定，只可能来自篡改」）。调用方看到的现象是
        **一个 Offer 写得进去、第二个写不进去**，而报错说的是一个与它做的事无关的
        故事。这里修的就是这一处：绑在写事务里、插在行之前 —— 绑不上（库属于别的
        工作区）时那一行还没进去。
        """
        placeholders = ", ".join("?" for _ in values)
        with session(self._path) as conn, write_transaction(conn):
            if workspace is not None:
                bind_workspace(conn, workspace, error=StoreError)
            conn.execute(
                f"INSERT INTO {table} ({', '.join(columns)}) VALUES ({placeholders}) "
                f"ON CONFLICT ({', '.join(conflict_on)}) DO NOTHING",
                values,
            )
            row = conn.execute(f"SELECT payload FROM {table} WHERE {where}", where_values).fetchone()
            stored = _load(row["payload"], subject)
        differing = _differences(stored, payload)
        if differing:
            raise _conflict(table=table, subject=subject, differing=differing)

    def _read_one(self, *, table: str, where: str, values: tuple[object, ...]) -> dict | None:
        with session(self._path) as conn:
            row = conn.execute(f"SELECT payload FROM {table} WHERE {where}", values).fetchone()
        if row is None:
            return None
        return _load(row["payload"], where)

    # ------------------------------------------------------------ 产品资料

    def put_version(self, version: ProductVersion) -> None:
        payload = version.model_dump(mode="json")
        self._insert_and_compare(
            table="product_version",
            columns=("product_id", "version", "payload"),
            values=(version.product_id, version.version, _dump(payload)),
            conflict_on=("product_id", "version"),
            where="product_id = ? AND version = ?",
            where_values=(version.product_id, version.version),
            subject=f"product_id={version.product_id!r} version={version.version}",
            payload=payload,
            # 产品资料没有工作区（也不需要库绑谁）—— 见 ``_insert_and_compare`` 那段。
            workspace=None,
        )

    def get_version(self, product_id: str, version: int) -> ProductVersion | None:
        raw = self._read_one(
            table="product_version",
            where="product_id = ? AND version = ?",
            values=(product_id, version),
        )
        if raw is None:
            return None
        return ProductVersion.model_validate(raw)

    def versions(self, product_id: str) -> tuple[ProductVersion, ...]:
        with session(self._path) as conn:
            rows = conn.execute(
                "SELECT version, payload FROM product_version WHERE product_id = ? "
                "ORDER BY version",
                (product_id,),
            ).fetchall()
        return tuple(
            ProductVersion.model_validate(_load(row["payload"], f"product_id={product_id!r}"))
            for row in rows
        )

    # ------------------------------------------------------------ Offer

    def put_offer(self, offer: OfferVersion) -> None:
        payload = offer.model_dump(mode="json")
        self._insert_and_compare(
            table="offer_version",
            columns=("offer_id", "version", "workspace", "payload"),
            values=(offer.offer_id, offer.version, offer.workspace, _dump(payload)),
            conflict_on=("offer_id", "version"),
            where="offer_id = ? AND version = ?",
            where_values=(offer.offer_id, offer.version),
            subject=f"offer_id={offer.offer_id!r} version={offer.version}",
            payload=payload,
            workspace=offer.workspace,
        )

    def get_offer(self, offer_id: str, version: int) -> OfferVersion | None:
        raw = self._read_one(
            table="offer_version",
            where="offer_id = ? AND version = ?",
            values=(offer_id, version),
        )
        if raw is None:
            return None
        return OfferVersion.model_validate(raw)

    # ------------------------------------------------------------ 产品线

    def put_line(self, line: ProductLine) -> None:
        payload = line.model_dump(mode="json")
        self._insert_and_compare(
            table="product_line",
            columns=("line_id", "workspace", "payload"),
            values=(line.line_id, line.workspace, _dump(payload)),
            conflict_on=("line_id",),
            where="line_id = ?",
            where_values=(line.line_id,),
            subject=f"line_id={line.line_id!r}",
            payload=payload,
            workspace=line.workspace,
        )

    def get_line(self, line_id: str) -> ProductLine | None:
        raw = self._read_one(table="product_line", where="line_id = ?", values=(line_id,))
        if raw is None:
            return None
        return ProductLine.model_validate(raw)


__all__ = ["SqliteProductCatalog"]
