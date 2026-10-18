"""SQLite 调度存储（``ports/scheduling.py::ScheduleStore`` 的落点，**主存**）。

## 两张表，两种写路径，而它们的载体不是同一种东西

* ``schedule_definition`` 是**只增版本**：当前版是 ``max(schedule_revision)``，
  写入是一次 ``INSERT``，而 CAS 就是那条主键（``(schedule_id, schedule_revision)``）——
  两个写者拿同一个基准版本号时，只有一个人的号落得进去。写路径与
  ``sqlite/views.py::SqliteSavedViewStore.put_view`` 逐字同形。
* ``schedule_fire`` 是 **write-once 加一次 CAS**：``claim_fire`` 是一条
  ``INSERT ... ON CONFLICT DO NOTHING``，而**它的 ``rowcount`` 就是「这一次抢到了」**
  —— 幂等来自插入失败，不来自任何一次先读。``settle_fire`` 是一条带
  ``AND outcome = 'pending'`` 的 ``UPDATE``，``rowcount`` 再一次是判据。

## 为什么 ``claim_fire`` 不先查一查

「先查，没有就插」在单线程下与这里逐字同形，而它在两个驱动同时醒来时会让**两个
调用方都查到「没有」**，然后两条都去跑动作：两轮研究，而两份报告各自都写着
「这一次是我点的火」。那个窗口不是理论上的：§8 逐字说外部 cron / Hosting /
ServerOS **以后都只能作为 trigger driver** —— 也就是说，同一条调度被两个驱动
同时叫醒是这条路**设计上**允许的情形。``ON CONFLICT DO NOTHING`` 把「谁去跑」
交给唯一键来裁决，而那是单线程与多线程下**同一条**判据。

## ``settle_fire`` 的 CAS 为什么必须有

一次 fire 的结论只写一次。一个无条件的 ``UPDATE`` 会让第二次写入把第一次的结果
抹掉，而「重放同一次 fire 回同一个结果」这句话在那一刻起就只是一句注释 ——
重放读到的会是**最后一次改写**的结果，而那正是它声称不会发生的事。
"""

from __future__ import annotations

import sqlite3
from datetime import datetime
from pathlib import Path

from ...domain.errors import (
    IdempotencyConflictError,
    ScheduleConflictError,
    StoreError,
)
from ...domain.scheduling import ScheduleDefinition, ScheduleFire
from ...domain.util import to_utc
from ...ports.scheduling import FireWrite, ScheduleStore, ScheduleWrite
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
#: ``schedule_definition`` 的键是 ``schedule_id`` 而不是 ``view_id``，而漏掉它一次
#: 的后果与那边逐字相同：一个历史版本混进结果，而它与当前版长得一模一样。
_CURRENT = (
    "t.schedule_revision = (SELECT MAX(t2.schedule_revision) FROM schedule_definition t2 "
    "WHERE t2.schedule_id = t.schedule_id)"
)


def _stamp(moment: datetime) -> str:
    """存进 TEXT 列的那一刻。**先规范到 UTC 再格式化。**

    两件事都要：``to_utc`` 让两个不同偏移量的写法落成同一个字符串（``+09:00`` 与
    ``+00:00`` 的同一个瞬间按字符串排会在两个位置上），而 ``isoformat`` 的字典序
    等于时刻序 —— ``due`` 那条查询的 ``next_due_at <= ?`` 就建立在这上面。
    一个存本地时刻的实现会让「到期了没有」在跨时区部署里取决于字符串写法。
    """
    return to_utc(moment).isoformat()


class SqliteScheduleStore(ScheduleStore):
    """基于 SQLite 的调度存储。构造参数只有 ``db_path``：与其余几张表同一个理由
    （存储要能在没有配置、没有 ``os.environ`` 的情况下对着一个临时文件被测出来）。

    建表在 ``__init__`` 里做（``ensure_schema`` 幂等）—— 加这两张表**不需要**动
    ``SCHEMA_VERSION``，也不需要一次迁移：``_TARGET_DDL`` 每次整份重放。
    """

    def __init__(self, db_path: Path) -> None:
        self._path = db_path
        with session(self._path) as conn:
            ensure_schema(conn)

    # -------------------------------------------------------------- 声明
    def put_definition(
        self, definition: ScheduleDefinition, *, expected_revision: int | None
    ) -> ScheduleWrite:
        """写下一版声明。判据与拒绝见 ``ports/scheduling.py::put_definition``。"""
        if definition.schedule_revision != (expected_revision or 0) + 1:
            raise StoreError(
                f"schedule_id={definition.schedule_id!r} 的写入版本必须是 "
                f"{(expected_revision or 0) + 1}（基准 {expected_revision} 加一），"
                f"本次是 {definition.schedule_revision}。\n"
                "这一条挡的是「读完之后跳了一版直接写回」：那会留下一个空洞，"
                "而在那个空洞里，一个拿着旧版本的写者会成功。"
            )
        payload = _dump(definition.model_dump(mode="json"))
        with session(self._path) as conn, write_transaction(conn):
            bind_workspace(conn, definition.workspace, error=StoreError)
            bound = workspace_binding(conn)
            current = self._current(conn, definition.schedule_id, bound)
            self._require_base(definition, current, expected_revision)
            if current is not None and current.content_digest == definition.content_digest:
                return ScheduleWrite(definition=current, revision_written=None)
            try:
                conn.execute(
                    "INSERT INTO schedule_definition "
                    "(schedule_id, schedule_revision, workspace, enabled, next_due_at, "
                    "updated_at, payload) VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (
                        definition.schedule_id,
                        definition.schedule_revision,
                        definition.workspace,
                        1 if definition.enabled else 0,
                        None if definition.next_due_at is None else _stamp(definition.next_due_at),
                        _stamp(definition.updated_at),
                        payload,
                    ),
                )
            except sqlite3.IntegrityError as exc:
                # 主键。**在写事务里读得到当前版**，所以这条拒绝说得清是谁先写了。
                raise ScheduleConflictError(
                    f"schedule_id={definition.schedule_id!r} 的第 "
                    f"{definition.schedule_revision} 版写不进去：已经有一条同版本号的"
                    f"行了（{exc}）。\n"
                    "另一个写者刚刚写过这一条。请重读它的当前版，再决定写哪一版 —— "
                    "这一次写一个字节都没落盘。"
                ) from exc
            row = conn.execute(
                "SELECT payload FROM schedule_definition "
                "WHERE schedule_id = ? AND schedule_revision = ?",
                (definition.schedule_id, definition.schedule_revision),
            ).fetchone()
        assert row is not None  # 刚 INSERT 成功
        stored = ScheduleDefinition.model_validate(
            owned(
                _load(row["payload"], self._where(definition.schedule_id, definition.schedule_revision)),
                where=self._where(definition.schedule_id, definition.schedule_revision),
                bound=bound,
            )
        )
        return ScheduleWrite(definition=stored, revision_written=definition.schedule_revision)

    # -------------------------------------------------------------- 读
    def get_definition(
        self, schedule_id: str, schedule_revision: int
    ) -> ScheduleDefinition | None:
        with session(self._path) as conn:
            bound = workspace_binding(conn)
            row = conn.execute(
                "SELECT payload FROM schedule_definition "
                "WHERE schedule_id = ? AND schedule_revision = ?",
                (schedule_id, schedule_revision),
            ).fetchone()
        if row is None:
            return None
        return ScheduleDefinition.model_validate(
            owned(
                _load(row["payload"], self._where(schedule_id, schedule_revision)),
                where=self._where(schedule_id, schedule_revision),
                bound=bound,
            )
        )

    def definitions(
        self, *, workspace: str | None = None
    ) -> tuple[ScheduleDefinition, ...]:
        """每一条调度的**当前那一版**，按 ``schedule_id`` 升序。

        那句 ``_CURRENT`` 子查询防的是：一条改过范围的调度会留下两行，而
        「这个部署现在有哪些调度」把两版都算一遍 —— 报告上显示「调度多了一些」。
        """
        sql = (
            "SELECT schedule_id, schedule_revision, payload FROM schedule_definition t "
            f"WHERE {_CURRENT}"
        )
        params: list[object] = []
        if workspace is not None:
            sql += " AND t.workspace = ?"
            params.append(workspace)
        sql += " ORDER BY t.schedule_id"
        with session(self._path) as conn:
            bound = workspace_binding(conn)
            rows = conn.execute(sql, tuple(params)).fetchall()
        return tuple(
            ScheduleDefinition.model_validate(
                owned(
                    _load(
                        row["payload"],
                        self._where(row["schedule_id"], row["schedule_revision"]),
                    ),
                    where=self._where(row["schedule_id"], row["schedule_revision"]),
                    bound=bound,
                )
            )
            for row in rows
        )

    def due(
        self, *, at: datetime, workspace: str | None = None
    ) -> tuple[ScheduleDefinition, ...]:
        """这一刻真的该点火的那些（当前那一版），按 ``(next_due_at, schedule_id)`` 升序。

        三格判据与 :meth:`ScheduleDefinition.due` **逐字同一条**（``enabled``、
        ``next_due_at`` 非空、``<= at``），而它们全部进 WHERE：这个方法是
        ``schedule_definition_by_due`` 那条索引存在的理由，让调用方去
        ``definitions()`` 里自己筛会把「低成本 due check」变成一次全表读 ——
        而那是驱动每一次醒来的第一步。
        """
        sql = (
            "SELECT schedule_id, schedule_revision, payload FROM schedule_definition t "
            f"WHERE {_CURRENT} AND t.enabled = 1 AND t.next_due_at IS NOT NULL "
            "AND t.next_due_at <= ?"
        )
        params: list[object] = [_stamp(at)]
        if workspace is not None:
            sql += " AND t.workspace = ?"
            params.append(workspace)
        sql += " ORDER BY t.next_due_at, t.schedule_id"
        with session(self._path) as conn:
            bound = workspace_binding(conn)
            rows = conn.execute(sql, tuple(params)).fetchall()
        return tuple(
            ScheduleDefinition.model_validate(
                owned(
                    _load(
                        row["payload"],
                        self._where(row["schedule_id"], row["schedule_revision"]),
                    ),
                    where=self._where(row["schedule_id"], row["schedule_revision"]),
                    bound=bound,
                )
            )
            for row in rows
        )

    # -------------------------------------------------------------- fire
    def claim_fire(self, fire: ScheduleFire) -> FireWrite:
        """**抢下一次 fire。** 判据是 ``ON CONFLICT DO NOTHING`` 的 ``rowcount``。

        三条出口（见 ``ports/scheduling.py::claim_fire``）。第二条与第三条在这里
        是**同一次读**的两半：读回来的那一行如果与本次交上来的占位不是同一次 fire
        （``workspace`` / ``fired_at`` 不同），那就不是「有人先写了同一件」，
        而是「有人拿同一个 id 写了另一件」—— 前者什么都不用做，后者要停线。
        """
        where = self._fire_where(fire.schedule_id, fire.schedule_revision, fire.fire_id)
        with session(self._path) as conn, write_transaction(conn):
            bind_workspace(conn, fire.workspace, error=StoreError)
            bound = workspace_binding(conn)
            cursor = conn.execute(
                "INSERT INTO schedule_fire "
                "(schedule_id, schedule_revision, fire_id, workspace, fired_at, "
                "outcome, payload) VALUES (?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT DO NOTHING",
                (
                    fire.schedule_id,
                    fire.schedule_revision,
                    fire.fire_id,
                    fire.workspace,
                    _stamp(fire.fired_at),
                    fire.outcome.value,
                    _dump(fire.model_dump(mode="json")),
                ),
            )
            if cursor.rowcount == 1:
                return FireWrite(fire=fire, recorded=True)
            row = conn.execute(
                "SELECT workspace, fired_at, payload FROM schedule_fire "
                "WHERE schedule_id = ? AND schedule_revision = ? AND fire_id = ?",
                (fire.schedule_id, fire.schedule_revision, fire.fire_id),
            ).fetchone()
        assert row is not None  # 插不进去就说明它已经在
        stored = self._fire(row, where, bound)
        if stored.workspace != fire.workspace or stored.fired_at != fire.fired_at:
            raise IdempotencyConflictError(
                f"fire_id={fire.fire_id!r}（schedule_id={fire.schedule_id!r} 第 "
                f"{fire.schedule_revision} 版）在盘上已经有一次 fire，而它属于另一次"
                f"：盘上是 workspace={stored.workspace!r} / fired_at="
                f"{stored.fired_at.isoformat()}，本次是 workspace={fire.workspace!r} / "
                f"fired_at={fire.fired_at.isoformat()}。\n"
                "同一个 fire_id 配两条不同的 fire 只可能是有人改过 id，而安静地留下"
                "第一条会让「重放回同一个结果」变成「回第一次那个」—— 那是两件不同的"
                "事，只是报告上写着同一句话。"
            )
        return FireWrite(fire=stored, recorded=False)

    def settle_fire(self, fire: ScheduleFire) -> FireWrite:
        """把结论写上。**CAS**：``WHERE outcome = 'pending'`` 加 ``rowcount``。"""
        if fire.pending:
            raise ValueError(
                f"{fire.schedule_id!r}@{fire.fire_id!r} 交上来的 ``outcome`` 是 "
                "``pending``：占位不是一个结论，而这条写入会把一次还在跑的动作"
                "钉死成占位。结论只有四个（SETTLED_OUTCOMES）。"
            )
        where = self._fire_where(fire.schedule_id, fire.schedule_revision, fire.fire_id)
        with session(self._path) as conn, write_transaction(conn):
            bind_workspace(conn, fire.workspace, error=StoreError)
            bound = workspace_binding(conn)
            cursor = conn.execute(
                "UPDATE schedule_fire SET outcome = ?, payload = ? "
                "WHERE schedule_id = ? AND schedule_revision = ? AND fire_id = ? "
                "AND outcome = 'pending'",
                (
                    fire.outcome.value,
                    _dump(fire.model_dump(mode="json")),
                    fire.schedule_id,
                    fire.schedule_revision,
                    fire.fire_id,
                ),
            )
            if cursor.rowcount == 1:
                return FireWrite(fire=fire, recorded=True)
            row = conn.execute(
                "SELECT workspace, fired_at, payload FROM schedule_fire "
                "WHERE schedule_id = ? AND schedule_revision = ? AND fire_id = ?",
                (fire.schedule_id, fire.schedule_revision, fire.fire_id),
            ).fetchone()
        if row is None:
            raise StoreError(
                f"fire_id={fire.fire_id!r}（schedule_id={fire.schedule_id!r} 第 "
                f"{fire.schedule_revision} 版）不在盘上，而本次调用要写上它的结论。\n"
                "结论只能写在一个**已经被抢下**的占位上（先 claim_fire 再 settle_fire）"
                "—— 一个直接写结论的实现没有经过那道闸门，于是「谁跑了动作」这件事"
                "没有任何载体。"
            )
        stored = self._fire(row, where, bound)
        return FireWrite(fire=stored, recorded=False)

    def get_fire(
        self, schedule_id: str, schedule_revision: int, fire_id: str
    ) -> ScheduleFire | None:
        """**三格缺一不可** —— 理由见 ``ports/scheduling.py::get_fire``。"""
        where = self._fire_where(schedule_id, schedule_revision, fire_id)
        with session(self._path) as conn:
            bound = workspace_binding(conn)
            row = conn.execute(
                "SELECT workspace, fired_at, payload FROM schedule_fire "
                "WHERE schedule_id = ? AND schedule_revision = ? AND fire_id = ?",
                (schedule_id, schedule_revision, fire_id),
            ).fetchone()
        if row is None:
            return None
        return self._fire(row, where, bound)

    def fires(
        self, *, workspace: str, schedule_id: str | None = None
    ) -> tuple[ScheduleFire, ...]:
        """按 ``(fired_at, schedule_id, fire_id)`` 升序列出 fire 记录。"""
        sql = (
            "SELECT schedule_id, schedule_revision, fire_id, workspace, fired_at, payload "
            "FROM schedule_fire WHERE workspace = ?"
        )
        params: list[object] = [workspace]
        if schedule_id is not None:
            sql += " AND schedule_id = ?"
            params.append(schedule_id)
        sql += " ORDER BY fired_at, schedule_id, fire_id"
        with session(self._path) as conn:
            bound = workspace_binding(conn)
            rows = conn.execute(sql, tuple(params)).fetchall()
        return tuple(
            self._fire(
                row,
                self._fire_where(row["schedule_id"], row["schedule_revision"], row["fire_id"]),
                bound,
            )
            for row in rows
        )

    # -------------------------------------------------------------- 内部
    @staticmethod
    def _where(schedule_id: str, schedule_revision: int) -> str:
        return f"schedule_definition[{schedule_id}@{schedule_revision}]"

    @staticmethod
    def _fire_where(schedule_id: str, schedule_revision: int, fire_id: str) -> str:
        return f"schedule_fire[{schedule_id}@{schedule_revision}#{fire_id}]"

    def _fire(self, row: sqlite3.Row, where: str, bound: str | None) -> ScheduleFire:
        return ScheduleFire.model_validate(
            owned(_load(row["payload"], where), where=where, bound=bound)
        )

    def _current(
        self, conn: sqlite3.Connection, schedule_id: str, bound: str | None
    ) -> ScheduleDefinition | None:
        row = conn.execute(
            "SELECT schedule_revision, payload FROM schedule_definition "
            "WHERE schedule_id = ? ORDER BY schedule_revision DESC LIMIT 1",
            (schedule_id,),
        ).fetchone()
        if row is None:
            return None
        return ScheduleDefinition.model_validate(
            owned(
                _load(
                    row["payload"],
                    self._where(schedule_id, row["schedule_revision"]),
                ),
                where=self._where(schedule_id, row["schedule_revision"]),
                bound=bound,
            )
        )

    def _require_base(
        self,
        definition: ScheduleDefinition,
        current: ScheduleDefinition | None,
        expected_revision: int | None,
    ) -> None:
        """基准版本判据。两条各自的报文分得开（``SceneStore.put_record`` 同一条）。"""
        if expected_revision is None:
            if current is not None:
                raise ScheduleConflictError(
                    f"schedule_id={definition.schedule_id!r} 已经在了（当前第 "
                    f"{current.schedule_revision} 版，原因是 {current.revision_reason!r}），"
                    "本次调用声称它是首次声明。\n"
                    "要改它，就把 schedule_revision 写成当前版加一（新的一次改动是"
                    "一次新版本，不是覆盖）；要新建一条，就换一个 schedule_id。"
                )
            return
        if current is None:
            raise ScheduleConflictError(
                f"schedule_id={definition.schedule_id!r} 在盘上没有任何一版，而本次"
                f"调用说它读到的是第 {expected_revision} 版。\n"
                "去看这个 id 是不是抄错了 —— 这一次写一个字节都没落盘。"
            )
        if current.schedule_revision != expected_revision:
            raise ScheduleConflictError(
                f"schedule_id={definition.schedule_id!r} 手上的基准是第 "
                f"{expected_revision} 版，而盘上当前是第 {current.schedule_revision} 版"
                f"（原因是 {current.revision_reason!r}）—— 有人先写了。"
                "请重读它的当前版再决定写哪一版。"
            )
