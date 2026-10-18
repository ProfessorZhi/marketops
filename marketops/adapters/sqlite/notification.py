"""SQLite 通知存储（``ports/notification.py`` 的落点，**主存**）。

## 三张表，三种写路径，而它们守的是三件不同的失败

* ``notification_event`` —— **只增、不可变、只生成一次**。判据是
  ``UNIQUE (workspace, dedupe_key)``，而那个索引不在这里被解释：``dedupe_key``
  由业务处置点算好交上来，这一层只是**用它**。第二次写同一个键时本层做一次
  内容比较：一字不差 ⇒ 一次 no-op（``recorded=False``），不同 ⇒ ``StoreError``
  —— 同一个去重键配两份内容意味着两个不同的业务变化共用了同一个键，
  而静默留下第一条会让第二条**消失**。
* ``notification_subscription`` —— **只增版本**，形状与 ``saved_view`` / ``watch``
  逐字相同（当前版是 ``max(revision)``，CAS 是那条主键）。
* ``delivery_receipt`` —— **只追加**。主键带 ``attempt``，所以重试是 ``INSERT``
  一行新行；**本模块里没有一条 ``UPDATE delivery_receipt``**，这是「重试不覆盖」
  这句话唯一需要的东西。

## 这张表里没有「已投递」

``payload`` 里的 ``outcome`` 只有 ``accepted`` / ``unknown`` 两个取值
（``domain/notification.py::DeliveryOutcome``），而这一层不合成第三个：
它存的就是领域对象交上来的那一格。一个「顺手在成功时写个 true」的实现在这里
写不出来 —— 没有任何一列叫那个名字。
"""

from __future__ import annotations

import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any

from ...domain.errors import (
    StoreError,
    SubscriptionConflictError,
)
from ...domain.notification import (
    DeliveryReceipt,
    NotificationEvent,
    NotificationSeverity,
    NotificationSubscription,
)
from ...domain.util import to_utc
from ...ports.notification import (
    EventWrite,
    NotificationStore,
    ReceiptWrite,
    SubscriptionWrite,
)
from .db import (
    bind_workspace,
    ensure_schema,
    owned,
    session,
    workspace_binding,
    write_transaction,
)
from .payload import dump_payload as _dump
from .payload import load_payload as _load

#: 「这一行是当前版」的子查询（``sqlite/views.py::_CURRENT`` 的兄弟）。
_CURRENT = (
    "t.revision = (SELECT MAX(t2.revision) FROM notification_subscription t2 "
    "WHERE t2.subscription_id = t.subscription_id)"
)


def _stamp(moment: datetime) -> str:
    """存进 TEXT 列的那一刻。先规范到 UTC 再格式化 —— 理由与
    ``sqlite/scheduling.py::_stamp`` 逐字相同（事件按 ``occurred_at`` 排序，
    而那两个不同偏移量的写法必须落成同一串字节）。"""
    return to_utc(moment).isoformat()


class SqliteNotificationStore(NotificationStore):
    """基于 SQLite 的通知存储。构造参数只有 ``db_path``。"""

    def __init__(self, db_path: Path) -> None:
        self._path = db_path
        with session(self._path) as conn:
            ensure_schema(conn)

    # -------------------------------------------------------------- 事件
    def put_event(self, event: NotificationEvent) -> EventWrite:
        """记下一条业务变化事件。判据与拒绝见 ``ports/notification.py::put_event``。"""
        where = f"notification_event[{event.event_id}]"
        with session(self._path) as conn, write_transaction(conn):
            bind_workspace(conn, event.workspace, error=StoreError)
            bound = workspace_binding(conn)
            try:
                conn.execute(
                    "INSERT INTO notification_event "
                    "(event_id, workspace, dedupe_key, kind, severity, occurred_at, payload) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (
                        event.event_id,
                        event.workspace,
                        event.dedupe_key,
                        event.kind,
                        event.severity.value,
                        _stamp(event.occurred_at),
                        _dump(event.model_dump(mode="json")),
                    ),
                )
            except sqlite3.IntegrityError as exc:
                return self._resettle_event(conn, event, where, bound, exc)
        return EventWrite(event=event, recorded=True)

    def _resettle_event(
        self,
        conn: sqlite3.Connection,
        event: NotificationEvent,
        where: str,
        bound: str | None,
        exc: sqlite3.IntegrityError,
    ) -> EventWrite:
        """写不进去的那一次：按去重键读回来，判「同一件」还是「撞车」。

        两条约束都可能在这里响，而它们的处置完全不同：

        * ``UNIQUE (workspace, dedupe_key)`` —— 同一次业务变化被记了两次。
          内容一字不差就是**幂等命中**（两个处置点各自跑到同一件事），不同则是
          两个业务变化共用一个键。
        * ``PRIMARY KEY (event_id)`` —— 同一个 id 配了另一个去重键。那说明
          ``event_id`` 不是从键推出来的（``book_event`` 的校验器会先拦一次，
          这一条是它背后的兜底）。
        """
        row = conn.execute(
            "SELECT payload FROM notification_event WHERE workspace = ? AND dedupe_key = ?",
            (event.workspace, event.dedupe_key),
        ).fetchone()
        if row is None:
            raise StoreError(
                f"事件 {event.event_id!r} 写不进去（{exc}），而 "
                f"(workspace={event.workspace!r}, dedupe_key={event.dedupe_key!r}) "
                "上没有行。\n"
                "只可能是这个 event_id 已经被另一条事件用了 —— 而 event_id 必须是"
                "从 (workspace, dedupe_key) 推出来的（domain/notification.py::"
                "notification_event_id）。手写的 id 会让同一次业务变化写成两条事件，"
                "而两条各有各的 id，去重键那一格挡不住它们。"
            ) from exc
        stored = NotificationEvent.model_validate(
            owned(_load(row["payload"], where), where=where, bound=bound)
        )
        if stored.model_dump(mode="json") != event.model_dump(mode="json"):
            raise StoreError(
                f"dedupe_key={event.dedupe_key!r} 上已经有一条事件"
                f"（event_id={stored.event_id!r}），而它与本次的内容不同："
                f"{_diff(stored, event)}。\n"
                "同一个去重键配两份内容意味着两个不同的业务变化共用了同一个键 —— "
                "静默留下第一条会让第二条消失，而它在报告上与「这一次没有变化」同形。"
            ) from exc
        return EventWrite(event=stored, recorded=False)

    def get_event(self, event_id: str) -> NotificationEvent | None:
        with session(self._path) as conn:
            bound = workspace_binding(conn)
            row = conn.execute(
                "SELECT payload FROM notification_event WHERE event_id = ?", (event_id,)
            ).fetchone()
        if row is None:
            return None
        return NotificationEvent.model_validate(
            owned(
                _load(row["payload"], f"notification_event[{event_id}]"),
                where=f"notification_event[{event_id}]",
                bound=bound,
            )
        )

    def events(
        self,
        *,
        workspace: str,
        kind: str | None = None,
        severity: NotificationSeverity | None = None,
        limit: int | None = None,
    ) -> tuple[NotificationEvent, ...]:
        """按 ``(occurred_at, event_id)`` **升序** —— 理由见端口上的那一句。"""
        sql = "SELECT payload, event_id FROM notification_event WHERE workspace = ?"
        params: list[object] = [workspace]
        if kind is not None:
            sql += " AND kind = ?"
            params.append(kind)
        if severity is not None:
            sql += " AND severity = ?"
            params.append(severity.value)
        sql += " ORDER BY occurred_at, event_id"
        if limit is not None:
            sql += " LIMIT ?"
            params.append(limit)
        with session(self._path) as conn:
            bound = workspace_binding(conn)
            rows = conn.execute(sql, tuple(params)).fetchall()
        return tuple(
            NotificationEvent.model_validate(
                owned(
                    _load(row["payload"], f"notification_event[{row['event_id']}]"),
                    where=f"notification_event[{row['event_id']}]",
                    bound=bound,
                )
            )
            for row in rows
        )

    # -------------------------------------------------------------- 订阅
    def put_subscription(
        self, subscription: NotificationSubscription, *, expected_revision: int | None
    ) -> SubscriptionWrite:
        """写一版订阅。判据与拒绝见 ``ports/notification.py::put_subscription``。"""
        if subscription.revision != (expected_revision or 0) + 1:
            raise StoreError(
                f"subscription_id={subscription.subscription_id!r} 的写入版本必须是 "
                f"{(expected_revision or 0) + 1}（基准 {expected_revision} 加一），"
                f"本次是 {subscription.revision}。"
            )
        payload = _dump(subscription.model_dump(mode="json"))
        where = f"notification_subscription[{subscription.subscription_id}]"
        with session(self._path) as conn, write_transaction(conn):
            bind_workspace(conn, subscription.workspace, error=StoreError)
            bound = workspace_binding(conn)
            current = self._current(conn, subscription.subscription_id, bound)
            self._require_base(subscription, current, expected_revision)
            if current is not None and current.content_digest == subscription.content_digest:
                return SubscriptionWrite(subscription=current, revision_written=None)
            try:
                conn.execute(
                    "INSERT INTO notification_subscription "
                    "(subscription_id, revision, workspace, principal, sink, updated_at, "
                    "payload) VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (
                        subscription.subscription_id,
                        subscription.revision,
                        subscription.workspace,
                        subscription.principal,
                        subscription.sink,
                        _stamp(subscription.updated_at),
                        payload,
                    ),
                )
            except sqlite3.IntegrityError as exc:
                raise SubscriptionConflictError(
                    f"subscription_id={subscription.subscription_id!r} 的第 "
                    f"{subscription.revision} 版写不进去：已经有一条同版本号的行了"
                    f"（{exc}）。\n"
                    "另一个写者刚刚写过这一条。请重读它的当前版，再决定写哪一版 —— "
                    "这一次写一个字节都没落盘。"
                ) from exc
            row = conn.execute(
                "SELECT payload FROM notification_subscription "
                "WHERE subscription_id = ? AND revision = ?",
                (subscription.subscription_id, subscription.revision),
            ).fetchone()
        assert row is not None  # 刚 INSERT 成功
        stored = NotificationSubscription.model_validate(
            owned(_load(row["payload"], where), where=where, bound=bound)
        )
        return SubscriptionWrite(
            subscription=stored, revision_written=subscription.revision
        )

    def get_subscription(
        self, subscription_id: str, revision: int
    ) -> NotificationSubscription | None:
        where = f"notification_subscription[{subscription_id}@{revision}]"
        with session(self._path) as conn:
            bound = workspace_binding(conn)
            row = conn.execute(
                "SELECT payload FROM notification_subscription "
                "WHERE subscription_id = ? AND revision = ?",
                (subscription_id, revision),
            ).fetchone()
        if row is None:
            return None
        return NotificationSubscription.model_validate(
            owned(_load(row["payload"], where), where=where, bound=bound)
        )

    def subscriptions(
        self, *, workspace: str, principal: str | None = None
    ) -> tuple[NotificationSubscription, ...]:
        """本工作区的**全部当前版**订阅，按 ``subscription_id`` 升序。"""
        sql = (
            "SELECT subscription_id, revision, payload FROM notification_subscription t "
            f"WHERE t.workspace = ? AND {_CURRENT}"
        )
        params: list[object] = [workspace]
        if principal is not None:
            sql += " AND t.principal = ?"
            params.append(principal)
        sql += " ORDER BY t.subscription_id"
        with session(self._path) as conn:
            bound = workspace_binding(conn)
            rows = conn.execute(sql, tuple(params)).fetchall()
        return tuple(
            NotificationSubscription.model_validate(
                owned(
                    _load(
                        row["payload"],
                        f"notification_subscription[{row['subscription_id']}@{row['revision']}]",
                    ),
                    where=(
                        f"notification_subscription[{row['subscription_id']}@{row['revision']}]"
                    ),
                    bound=bound,
                )
            )
            for row in rows
        )

    # -------------------------------------------------------------- 收据
    def record_receipt(self, receipt: DeliveryReceipt) -> ReceiptWrite:
        """记下一次逐 sink 的投递尝试。**只追加** —— 本类里没有 ``UPDATE`` 这一张表。"""
        where = self._receipt_where(receipt)
        with session(self._path) as conn, write_transaction(conn):
            bind_workspace(conn, receipt.workspace, error=StoreError)
            bound = workspace_binding(conn)
            try:
                conn.execute(
                    "INSERT INTO delivery_receipt "
                    "(event_id, subscription_id, sink, attempt, workspace, recorded_at, "
                    "payload) VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (
                        receipt.event_id,
                        receipt.subscription_id,
                        receipt.sink,
                        receipt.attempt,
                        receipt.workspace,
                        _stamp(receipt.recorded_at),
                        _dump(receipt.model_dump(mode="json")),
                    ),
                )
            except sqlite3.IntegrityError as exc:
                row = conn.execute(
                    "SELECT payload FROM delivery_receipt WHERE event_id = ? AND "
                    "subscription_id = ? AND sink = ? AND attempt = ?",
                    (
                        receipt.event_id,
                        receipt.subscription_id,
                        receipt.sink,
                        receipt.attempt,
                    ),
                ).fetchone()
                assert row is not None  # 插不进去就说明它已经在
                stored = DeliveryReceipt.model_validate(
                    owned(_load(row["payload"], where), where=where, bound=bound)
                )
                if stored.model_dump(mode="json") != receipt.model_dump(mode="json"):
                    raise StoreError(
                        f"{where} 上已经有另一个结果了：{_diff(stored, receipt)}。\n"
                        "同一个 (事件, 订阅, sink, 第几次) 配两份结果意味着覆盖 —— "
                        "而覆盖会让「投了三次都没成」在盘上变成「投了一次」。"
                    ) from exc
                return ReceiptWrite(receipt=stored, recorded=False)
        return ReceiptWrite(receipt=receipt, recorded=True)

    def receipts(
        self,
        *,
        workspace: str,
        event_id: str | None = None,
        subscription_id: str | None = None,
    ) -> tuple[DeliveryReceipt, ...]:
        """收据，按 ``(event_id, subscription_id, sink, attempt)`` 升序。"""
        sql = (
            "SELECT event_id, subscription_id, sink, attempt, payload "
            "FROM delivery_receipt WHERE workspace = ?"
        )
        params: list[object] = [workspace]
        if event_id is not None:
            sql += " AND event_id = ?"
            params.append(event_id)
        if subscription_id is not None:
            sql += " AND subscription_id = ?"
            params.append(subscription_id)
        sql += " ORDER BY event_id, subscription_id, sink, attempt"
        with session(self._path) as conn:
            bound = workspace_binding(conn)
            rows = conn.execute(sql, tuple(params)).fetchall()
        return tuple(
            DeliveryReceipt.model_validate(
                owned(
                    _load(row["payload"], self._receipt_where_row(row)),
                    where=self._receipt_where_row(row),
                    bound=bound,
                )
            )
            for row in rows
        )

    def attempts(self, *, event_id: str, subscription_id: str, sink: str) -> int:
        """这三格上已经记过几次（``MAX(attempt)``）。

        ``COALESCE(MAX(attempt), 0)`` 而不是 ``COUNT(*)``：这一格是**主键的下一格**
        （调用方 ``+1`` 之后再去插），而 ``MAX`` 才是那个「下一个空位」。今天两者
        相等（attempt 从 1 起、每次 +1，没有洞），而它们分家的那天 ``COUNT(*)`` 会
        给出一个**已经被占过**的号 —— 那是一次 ``IntegrityError``，读起来像存储事故。
        """
        with session(self._path) as conn:
            row = conn.execute(
                "SELECT COALESCE(MAX(attempt), 0) AS used FROM delivery_receipt "
                "WHERE event_id = ? AND subscription_id = ? AND sink = ?",
                (event_id, subscription_id, sink),
            ).fetchone()
        return int(row["used"])

    # -------------------------------------------------------------- 内部
    @staticmethod
    def _receipt_where(receipt: DeliveryReceipt) -> str:
        return (
            f"delivery_receipt[{receipt.event_id}/"
            f"{receipt.subscription_id}/{receipt.sink}#{receipt.attempt}]"
        )

    @staticmethod
    def _receipt_where_row(row: sqlite3.Row) -> str:
        return (
            f"delivery_receipt[{row['event_id']}/"
            f"{row['subscription_id']}/{row['sink']}#{row['attempt']}]"
        )

    def _current(
        self, conn: sqlite3.Connection, subscription_id: str, bound: str | None
    ) -> NotificationSubscription | None:
        row = conn.execute(
            "SELECT revision, payload FROM notification_subscription "
            "WHERE subscription_id = ? ORDER BY revision DESC LIMIT 1",
            (subscription_id,),
        ).fetchone()
        if row is None:
            return None
        where = f"notification_subscription[{subscription_id}@{row['revision']}]"
        return NotificationSubscription.model_validate(
            owned(_load(row["payload"], where), where=where, bound=bound)
        )

    def _require_base(
        self,
        subscription: NotificationSubscription,
        current: NotificationSubscription | None,
        expected_revision: int | None,
    ) -> None:
        """基准版本判据。两条各自的报文分得开（``SceneStore.put_record`` 同一条）。"""
        if expected_revision is None:
            if current is not None:
                raise SubscriptionConflictError(
                    f"subscription_id={subscription.subscription_id!r} 已经在了（当前第 "
                    f"{current.revision} 版，sink={current.sink!r}），本次调用声称它是"
                    "首次创建。\n"
                    "要改它，就把 revision 写成当前版加一（改一条订阅是一次新版本，"
                    "不是覆盖）；要新建一条，就换一个 subscription_id。"
                )
            return
        if current is None:
            raise SubscriptionConflictError(
                f"subscription_id={subscription.subscription_id!r} 在盘上没有任何一版，"
                f"而本次调用说它读到的是第 {expected_revision} 版。\n"
                "去看这个 id 是不是抄错了 —— 这一次写一个字节都没落盘。"
            )
        if current.revision != expected_revision:
            raise SubscriptionConflictError(
                f"subscription_id={subscription.subscription_id!r} 手上的基准是第 "
                f"{expected_revision} 版，而盘上当前是第 {current.revision} 版"
                f"（sink={current.sink!r}）—— 有人先写了。请重读它的当前版再决定。"
            )


def _diff(left: Any, right: Any) -> str:
    """两份模型哪几格不同。**报出字段名与两边的值**，而不是一句「内容不同」。

    一句话的拒绝在运维那里不是一个可执行的动作：``dedupe_key`` 撞车要人判断的正是
    「这两份哪里不一样」，而「内容不同」把那个判断还给了读的人。
    """
    left_dump = left.model_dump(mode="json")
    right_dump = right.model_dump(mode="json")
    parts = [
        f"{key}: 盘上={left_dump.get(key)!r} 本次={right_dump.get(key)!r}"
        for key in sorted(set(left_dump) | set(right_dump))
        if left_dump.get(key) != right_dump.get(key)
    ]
    return "；".join(parts) if parts else "（两份内容的字段完全一致，差异在类型上）"
