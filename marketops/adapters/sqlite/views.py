"""SQLite 工作池：视图 / 监控 / 跟进池（``ports/views.py`` 的落点，**主存**）。

## 三张表长得几乎一样，而它们的写路径有两种相反的写法

* ``saved_view`` / ``watch`` 是**只增版本**：改一次产生一**行**，当前版是
  ``max(revision)``。
* ``action_item`` **不是**：处置就地更新那一行并把 ``revision`` 加一。理由在
  ``domain/action_item.py`` 的模块 docstring 里（那条 partial unique index 与
  ``(action_id, revision)`` 两条约束推出来的唯一解）。

一个「反正都是只增版本」的实现会让每一件被处置过的待办**永远留在未结状态**里
（索引盖的是 ``state``，旧的 ``open`` 行还在），于是同一主体上的下一件再也建不出来
—— 而报告上只显示「这个账号的待办一直是那一条」。

## 当前版：每一个「按 id 读」的查询都要带那句子查询

``revision = (SELECT MAX(revision) FROM <表> t2 WHERE t2.<id> = t.<id>)``

少写一次就多出一条**历史版本**混在结果里，而它与当前版长得一模一样（同样的
``view_id`` / ``watch_id``）。这条子查询在本模块里出现五次，每一处都注明了它防的是
哪一种读错 —— 它是这一族里最容易漏、也最难在报告上看出来的东西。

## 只增版本的表上，主键就是那条 CAS

``INSERT`` 一个新版本行；两个写者拿同一个基准版本号时，只有一个人的号落得进去。
那份 ``sqlite3.IntegrityError`` 被翻成一条**说得清主语**的拒绝（有人先写了，请重读），
而不是 ``StoreError`` —— 处置相反（``ports/views.py::SavedViewStore.put_view`` 逐字）。
"""

from __future__ import annotations

import sqlite3
from datetime import datetime
from pathlib import Path

from ...domain.action_item import (
    OPEN_STATES,
    ActionItem,
    ActionItemState,
    require_creatable,
)
from ...domain.errors import (
    ActionItemConflictError,
    OpenActionItemExistsError,
    SavedViewConflictError,
    StoreError,
)
from ...domain.saved_view import SavedView
from ...domain.util import to_utc
from ...domain.watch import Watch
from ...ports.views import (
    ActionItemStore,
    ActionItemWrite,
    SavedViewStore,
    ViewWrite,
    WatchStore,
    WatchWrite,
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

#: 「这一行是当前版」的子查询。**一处定义**，五处使用 —— 五处各写一遍的那天，
#: 漏掉一处就是一个「历史版本混进结果」的读数错误，而它与「当前版」长得一模一样。
_CURRENT = "t.revision = (SELECT MAX(t2.revision) FROM {table} t2 WHERE t2.{key} = t.{key})"


def _current_sql(table: str, key: str) -> str:
    return _CURRENT.format(table=table, key=key)


#: 比较「同一个 ``action_id`` 上的两份内容是不是同一件」时**不比**的两格。
#: 重发一次本来就是在新的一刻发生的（``ActionItem.opened`` 的 ``at`` 缺省是
#: ``utc_now()``），所以带上它们会让每一次重发都长得像另一件不同的事 ——
#: 而那两个时刻**不**影响「他写的是不是同一件工作」。其余每一格都进比较：
#: ``action_type`` / ``scope_ref`` / ``reason`` / ``owner`` / ``due_at`` 中任意一格
#: 不同，就是有人拿同一个 id 写了别的一件事。
_VOLATILE_ON_RESEND = ("created_at", "updated_at")


def _same_opening(left: ActionItem, right: ActionItem) -> bool:
    def shape(item: ActionItem) -> dict:
        payload = item.model_dump(mode="json")
        for key in _VOLATILE_ON_RESEND:
            payload.pop(key, None)
        return payload

    return shape(left) == shape(right)


class SqliteSavedViewStore(SavedViewStore):
    """基于 SQLite 的视图存储。构造参数只有 ``db_path``：与其余几张表同一个理由
    （存储要能在没有配置、没有 ``os.environ`` 的情况下对着一个临时文件被测出来）。

    建表在 ``__init__`` 里做（``ensure_schema`` 幂等）—— 加这三张表**不需要**动
    ``SCHEMA_VERSION``，也不需要一次迁移：``_TARGET_DDL`` 每次整份重放。
    """

    def __init__(self, db_path: Path) -> None:
        self._path = db_path
        with session(self._path) as conn:
            ensure_schema(conn)

    # ------------------------------------------------------------ 写
    def put_view(
        self, view: SavedView, *, expected_revision: int | None
    ) -> ViewWrite:
        """写一份视图。判据与拒绝见 ``ports/views.py::SavedViewStore.put_view``。"""
        if view.revision != (expected_revision or 0) + 1:
            raise StoreError(
                f"view_id={view.view_id!r} 的写入版本必须是 "
                f"{(expected_revision or 0) + 1}（基准 {expected_revision} 加一），"
                f"本次是 {view.revision}。\n"
                "这一条挡的是「读完之后跳了一版直接写回」：那会留下一个空洞，"
                "而在那个空洞里，一个拿着旧版本的写者会成功。"
            )
        payload = _dump(view.model_dump(mode="json"))
        with session(self._path) as conn, write_transaction(conn):
            bind_workspace(conn, view.workspace, error=StoreError)
            bound = workspace_binding(conn)
            current = self._current(conn, view.view_id, bound)
            self._require_base(view, current, expected_revision)
            if current is not None and current.content_digest == view.content_digest:
                return ViewWrite(view=current, revision_written=None)
            self._require_name_free(conn, view)
            try:
                conn.execute(
                    "INSERT INTO saved_view "
                    "(view_id, revision, workspace, owner, name, updated_at, payload) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (
                        view.view_id,
                        view.revision,
                        view.workspace,
                        view.owner,
                        view.name,
                        view.updated_at.isoformat(),
                        payload,
                    ),
                )
            except sqlite3.IntegrityError as exc:
                # 主键。**在写事务里读得到当前版**，所以这条拒绝说得清是谁先写了。
                raise SavedViewConflictError(
                    f"view_id={view.view_id!r} 的第 {view.revision} 版写不进去："
                    f"已经有一条同版本号的行了（{exc}）。\n"
                    "另一个写者刚刚写过这一条。请重读它的当前版，再决定写哪一版 —— "
                    "这一次写一个字节都没落盘。"
                ) from exc
            row = conn.execute(
                "SELECT payload FROM saved_view WHERE view_id = ? AND revision = ?",
                (view.view_id, view.revision),
            ).fetchone()
        assert row is not None  # 刚 INSERT 成功
        stored = SavedView.model_validate(
            owned(
                _load(row["payload"], f"saved_view[{view.view_id}@{view.revision}]"),
                where=f"saved_view[{view.view_id}@{view.revision}]",
                bound=bound,
            )
        )
        return ViewWrite(view=stored, revision_written=view.revision)

    # ------------------------------------------------------------ 读
    def current(self, view_id: str) -> SavedView | None:
        with session(self._path) as conn:
            bound = workspace_binding(conn)
            return self._current(conn, view_id, bound)

    def views(
        self, *, workspace: str, owner: str | None = None
    ) -> tuple[SavedView, ...]:
        """本工作区的全部**当前版**，按 ``(owner, name, view_id)`` 升序。

        那句 ``_CURRENT`` 子查询在这里防的是：一条改过名字的视图会留下两行，
        而「这个人现在有哪几份」把两个名字都算一遍 —— 报告上显示「他多了一份视图」。
        ``owner`` 那一格进 WHERE 而不是在 Python 里筛：它是这一族唯一的过滤条件，
        而在 Python 里筛的实现在 ``list_saved_views`` 之外还有别的消费者时会各筛各的。
        """
        sql = (
            "SELECT view_id, payload FROM saved_view t "
            f"WHERE t.workspace = ? AND {_current_sql('saved_view', 'view_id')}"
        )
        params: list[object] = [workspace]
        if owner is not None:
            sql += " AND t.owner = ?"
            params.append(owner)
        sql += " ORDER BY t.owner, t.name, t.view_id"
        with session(self._path) as conn:
            bound = workspace_binding(conn)
            rows = conn.execute(sql, tuple(params)).fetchall()
        return tuple(
            SavedView.model_validate(
                owned(
                    _load(row["payload"], f"saved_view[{row['view_id']}]"),
                    where=f"saved_view[{row['view_id']}]",
                    bound=bound,
                )
            )
            for row in rows
        )

    # ------------------------------------------------------------ 内部
    def _current(
        self, conn: sqlite3.Connection, view_id: str, bound: str | None
    ) -> SavedView | None:
        row = conn.execute(
            "SELECT revision, payload FROM saved_view WHERE view_id = ? "
            "ORDER BY revision DESC LIMIT 1",
            (view_id,),
        ).fetchone()
        if row is None:
            return None
        return SavedView.model_validate(
            owned(
                _load(row["payload"], f"saved_view[{view_id}@{row['revision']}]"),
                where=f"saved_view[{view_id}]",
                bound=bound,
            )
        )

    def _require_base(
        self,
        view: SavedView,
        current: SavedView | None,
        expected_revision: int | None,
    ) -> None:
        """基准版本判据。两条各自的报文分得开（``SceneStore.put_record`` 同一条）。"""
        if expected_revision is None:
            if current is not None:
                raise SavedViewConflictError(
                    f"view_id={view.view_id!r} 已经在了（当前第 {current.revision} 版，"
                    f"名字 {current.name!r}），本次调用声称它是首次创建。\n"
                    "要改它，就把 expected_revision 写成你读到的那一版；"
                    "要新建一条，就换一个 view_id。"
                )
            return
        if current is None:
            raise SavedViewConflictError(
                f"view_id={view.view_id!r} 在盘上没有任何一版，而本次调用说它读到的是"
                f"第 {expected_revision} 版。\n"
                "去看这个 id 是不是抄错了 —— 这一次写一个字节都没落盘。"
            )
        if current.revision != expected_revision:
            raise SavedViewConflictError(
                f"view_id={view.view_id!r} 手上的基准是第 {expected_revision} 版，"
                f"而盘上当前是第 {current.revision} 版（名字 {current.name!r}）—— "
                "有人先写了。请重读它的当前版再决定。"
            )

    def _require_name_free(self, conn: sqlite3.Connection, view: SavedView) -> None:
        """``(workspace, owner, name)`` 上不得有**另一条当前版**。

        两句限定词都在 SQL 里，缺一不可（``ports/views.py`` 逐字）：

        * ``view_id <> ?`` —— 同一条视图改名字当然会与自己重名。
        * ``revision = MAX(revision)`` —— 历史版本会重名，改名**正是**产生新版的理由。
          这里就是「判据不是一条 DDL 唯一索引」那句话的落点：一条普通的唯一索引会把
          历史版本一起挡掉，于是改名这件事在库上做不成。
        """
        row = conn.execute(
            "SELECT t.view_id, t.revision, t.owner FROM saved_view t "
            "WHERE t.workspace = ? AND t.owner = ? AND t.name = ? AND t.view_id <> ? "
            f"AND {_current_sql('saved_view', 'view_id')} "
            "LIMIT 1",
            (view.workspace, view.owner, view.name, view.view_id),
        ).fetchone()
        if row is None:
            return
        raise SavedViewConflictError(
            f"({view.workspace!r}, {view.owner!r}, {view.name!r}) 这个名字下已经有一条"
            f"当前版：view_id={row['view_id']!r} 第 {row['revision']} 版。\n"
            "要改**那一条**，就用它的 view_id 写下一版（它会保留自己的历史，"
            "而名字由你这一次的 name 决定）；要新建一条，就换一个名字。\n"
            "这一次写一个字节都没落盘 —— 一条会静默盖掉同名视图的实现，"
            "会让「他有两份视图」在报告上变成「他有一份」。"
        )


class SqliteWatchStore(WatchStore):
    """基于 SQLite 的监控存储。"""

    def __init__(self, db_path: Path) -> None:
        self._path = db_path
        with session(self._path) as conn:
            ensure_schema(conn)

    # ------------------------------------------------------------ 写
    def put_watch(
        self, watch: Watch, *, expected_revision: int | None
    ) -> WatchWrite:
        if watch.revision != (expected_revision or 0) + 1:
            raise StoreError(
                f"watch_id={watch.watch_id!r} 的写入版本必须是 "
                f"{(expected_revision or 0) + 1}（基准 {expected_revision} 加一），"
                f"本次是 {watch.revision}。"
            )
        payload = _dump(watch.model_dump(mode="json"))
        with session(self._path) as conn, write_transaction(conn):
            bind_workspace(conn, watch.workspace, error=StoreError)
            bound = workspace_binding(conn)
            current = self._current(conn, watch.watch_id, bound)
            self._require_base(watch, current, expected_revision)
            if current is not None and current.content_digest == watch.content_digest:
                return WatchWrite(watch=current, revision_written=None)
            try:
                conn.execute(
                    "INSERT INTO watch "
                    "(watch_id, revision, workspace, view_id, enabled, next_due_at, "
                    "updated_at, payload) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        watch.watch_id,
                        watch.revision,
                        watch.workspace,
                        watch.view_id,
                        1 if watch.enabled else 0,
                        None
                        if watch.next_due_at is None
                        else watch.next_due_at.isoformat(),
                        watch.updated_at.isoformat(),
                        payload,
                    ),
                )
            except sqlite3.IntegrityError as exc:
                raise StoreError(
                    f"watch_id={watch.watch_id!r} 的第 {watch.revision} 版写不进去："
                    f"已经有一条同版本号的行了（{exc}）。\n"
                    "另一个写者刚刚写过这一条。请重读它的当前版再决定 —— "
                    "这一次写一个字节都没落盘。"
                ) from exc
            row = conn.execute(
                "SELECT payload FROM watch WHERE watch_id = ? AND revision = ?",
                (watch.watch_id, watch.revision),
            ).fetchone()
        assert row is not None
        stored = Watch.model_validate(
            owned(
                _load(row["payload"], f"watch[{watch.watch_id}@{watch.revision}]"),
                where=f"watch[{watch.watch_id}@{watch.revision}]",
                bound=bound,
            )
        )
        return WatchWrite(watch=stored, revision_written=watch.revision)

    # ------------------------------------------------------------ 读
    def get_watch(self, watch_id: str) -> Watch | None:
        with session(self._path) as conn:
            bound = workspace_binding(conn)
            return self._current(conn, watch_id, bound)

    def watches(
        self, *, workspace: str, enabled: bool | None = None
    ) -> tuple[Watch, ...]:
        sql = (
            "SELECT watch_id, payload FROM watch t "
            f"WHERE t.workspace = ? AND {_current_sql('watch', 'watch_id')}"
        )
        params: list[object] = [workspace]
        if enabled is not None:
            sql += " AND t.enabled = ?"
            params.append(1 if enabled else 0)
        # 只按 watch_id：`name` 不是扁平列（`db.py` 那一格的注释逐字：扁平列只给
        # 「要用它查」的字段），SQL 排不了它。理由写在 ``ports/views.py::WatchStore.watches``。
        sql += " ORDER BY t.watch_id"
        with session(self._path) as conn:
            bound = workspace_binding(conn)
            rows = conn.execute(sql, tuple(params)).fetchall()
        return tuple(
            Watch.model_validate(
                owned(
                    _load(row["payload"], f"watch[{row['watch_id']}]"),
                    where=f"watch[{row['watch_id']}]",
                    bound=bound,
                )
            )
            for row in rows
        )

    def due_watches(self, *, workspace: str, at: datetime) -> tuple[Watch, ...]:
        """到期的那几条**当前版**。

        两个扁平列与那条索引在这里全部用上（``db.py`` 的注释逐字：「``next_due_at``
        是扁平列，因为「有哪些到期了」是唯一会全表扫的那个问题」）。

        ``next_due_at`` 的比较是**字符串**比较，而这里它是对的：写下去的时刻都过了
        ``to_utc``（``domain/util.py`` 的那条纪律：全项目禁止 naive datetime），
        于是它们带同一个偏移量、格式统一，而同一偏移量下 ISO-8601 的字典序等于时刻
        先后。查询参数也要过一遍 ``to_utc``，否则带着 ``+08:00`` 的入参会与盘上
        ``+00:00`` 的那些比出错误的先后 —— 而它错得**只在跨时区时**才现形。
        """
        sql = (
            "SELECT watch_id, payload FROM watch t "
            "WHERE t.workspace = ? AND t.enabled = 1 AND t.next_due_at IS NOT NULL "
            f"AND t.next_due_at <= ? AND {_current_sql('watch', 'watch_id')} "
            "ORDER BY t.next_due_at, t.watch_id"
        )
        with session(self._path) as conn:
            bound = workspace_binding(conn)
            rows = conn.execute(
                sql, (workspace, to_utc(at).isoformat())
            ).fetchall()
        return tuple(
            Watch.model_validate(
                owned(
                    _load(row["payload"], f"watch[{row['watch_id']}]"),
                    where=f"watch[{row['watch_id']}]",
                    bound=bound,
                )
            )
            for row in rows
        )

    # ------------------------------------------------------------ 内部
    def _current(
        self, conn: sqlite3.Connection, watch_id: str, bound: str | None
    ) -> Watch | None:
        row = conn.execute(
            "SELECT revision, payload FROM watch WHERE watch_id = ? "
            "ORDER BY revision DESC LIMIT 1",
            (watch_id,),
        ).fetchone()
        if row is None:
            return None
        return Watch.model_validate(
            owned(
                _load(row["payload"], f"watch[{watch_id}@{row['revision']}]"),
                where=f"watch[{watch_id}]",
                bound=bound,
            )
        )

    def _require_base(
        self, watch: Watch, current: Watch | None, expected_revision: int | None
    ) -> None:
        if expected_revision is None:
            if current is not None:
                raise StoreError(
                    f"watch_id={watch.watch_id!r} 已经在了（当前第 {current.revision} 版，"
                    f"名字 {current.name!r}），本次调用声称它是首次创建。\n"
                    "要改它，就把 expected_revision 写成你读到的那一版。"
                )
            return
        if current is None:
            raise StoreError(
                f"watch_id={watch.watch_id!r} 在盘上没有任何一版，而本次调用说它读到的"
                f"是第 {expected_revision} 版。"
            )
        if current.revision != expected_revision:
            raise StoreError(
                f"watch_id={watch.watch_id!r} 手上的基准是第 {expected_revision} 版，"
                f"而盘上当前是第 {current.revision} 版 —— 有人先写了。"
                "**不覆盖**：一次「把下一次检查推走」撞上「有人刚改了这条规则」时，"
                "覆盖会让那次改动静默消失（比如刚被关掉的它又亮起来）。请重读再决定。"
            )


class SqliteActionItemStore(ActionItemStore):
    """基于 SQLite 的跟进池。"""

    def __init__(self, db_path: Path) -> None:
        self._path = db_path
        with session(self._path) as conn:
            ensure_schema(conn)

    # ------------------------------------------------------------ 创建
    def create(self, item: ActionItem) -> ActionItemWrite:
        """把一件新待办放进去。重复创建是**插入失败**（``ports/views.py`` 逐字）。"""
        require_creatable(item)
        payload = _dump(item.model_dump(mode="json"))
        with session(self._path) as conn, write_transaction(conn):
            bind_workspace(conn, item.workspace, error=StoreError)
            bound = workspace_binding(conn)
            existing = self._get(conn, item.action_id, bound)
            if existing is not None:
                # 同一个 id 再写一次：内容一字不差 = 重发；否则是一次真冲突。
                # 比较住在这里（一个函数），不写成两处条件 —— 两处的那天，
                # 「重发」与「同一个 id 配两份内容」会各判一次而判据不同。
                if _same_opening(existing, item):
                    return ActionItemWrite(item=existing, revision_written=None)
                raise StoreError(
                    f"action_id={item.action_id!r} 已经有一版，而它与本次内容不同"
                    f"（盘上第 {existing.revision} 版，类型 {existing.action_type.value}，"
                    f"主语 {existing.scope_ref!r}）。\n"
                    "同一个 id 配两份内容是一次真的冲突（有人拿同一个 id 写了别的一件"
                    "事），不是重发 —— 停下来查那个 id 是从哪来的。"
                )
            try:
                conn.execute(
                    "INSERT INTO action_item "
                    "(action_id, revision, workspace, scope_ref, action_type, owner, "
                    "state, due_at, expected_version, updated_at, payload) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        item.action_id,
                        item.revision,
                        item.workspace,
                        item.scope_ref,
                        item.action_type.value,
                        item.owner,
                        item.state.value,
                        None if item.due_at is None else item.due_at.isoformat(),
                        item.expected_version,
                        item.updated_at.isoformat(),
                        payload,
                    ),
                )
            except sqlite3.IntegrityError as exc:
                raise self._duplicate_failure(conn, item, exc, bound) from exc
            row = conn.execute(
                "SELECT payload FROM action_item WHERE action_id = ? AND revision = ?",
                (item.action_id, item.revision),
            ).fetchone()
        assert row is not None
        stored = ActionItem.model_validate(
            owned(
                _load(row["payload"], f"action_item[{item.action_id}@{item.revision}]"),
                where=f"action_item[{item.action_id}@{item.revision}]",
                bound=bound,
            )
        )
        return ActionItemWrite(item=stored, revision_written=item.revision)

    # ------------------------------------------------------------ 处置
    def resolve(
        self, item: ActionItem, *, expected_version: int
    ) -> ActionItemWrite:
        """就地更新并把 ``revision`` 加一。判据与三条出口见 ``ports/views.py``。"""
        if item.workspace is None or not item.workspace:  # pragma: no cover —— 模型已保证
            raise StoreError("action_item 必须带 workspace")
        with session(self._path) as conn, write_transaction(conn):
            bound = workspace_binding(conn)
            current = self._get(conn, item.action_id, bound)
            if current is None:
                raise ActionItemConflictError(
                    f"action_id={item.action_id!r} 在盘上不存在。\n"
                    "去看这个 id 是不是抄错了 —— 这一条与「你手上的版本过期了」"
                    "是两件事，而它们的处置不同（一个是查 id，一个是重读再决定）。"
                )
            if current.workspace != item.workspace:
                raise StoreError(
                    f"action_id={item.action_id!r} 属于工作区 {current.workspace!r}，"
                    f"本次写入声称的是 {item.workspace!r}。"
                )
            if current.revision != expected_version:
                if (
                    current.expected_version == expected_version
                    and current.disposition_digest == item.disposition_digest
                ):
                    # 重发：盘上那一条**就是**这一次处置的结果。两个半边都要有，理由见
                    # ``ports/views.py::ActionItemStore.resolve``。
                    return ActionItemWrite(item=current, revision_written=None)
                raise ActionItemConflictError(
                    f"action_id={item.action_id!r} 手上的版本是 {expected_version}，"
                    f"而盘上当前是第 {current.revision} 版（状态 {current.state.value}）"
                    "—— 有人先处置过它了。请重读这一件再决定。\n"
                    "**不覆盖**：陈旧的那一次盖上去，会让前一个人的处置在盘上消失，"
                    "而他的屏幕上还写着「我做过了」。"
                )
            if item.revision != expected_version + 1:
                raise StoreError(
                    f"处置 action_id={item.action_id!r} 时新版本必须是 "
                    f"{expected_version + 1}，本次是 {item.revision}。"
                )
            changed = conn.execute(
                "UPDATE action_item SET revision = ?, owner = ?, state = ?, due_at = ?, "
                "expected_version = ?, updated_at = ?, payload = ? "
                "WHERE action_id = ? AND revision = ?",
                (
                    item.revision,
                    item.owner,
                    item.state.value,
                    None if item.due_at is None else item.due_at.isoformat(),
                    item.expected_version,
                    item.updated_at.isoformat(),
                    _dump(item.model_dump(mode="json")),
                    item.action_id,
                    expected_version,
                ),
            ).rowcount
            if changed != 1:
                # 写事务之下读得到的就是最新的，所以这一支理论上到不了；写下来是因为
                # 「到了这一步却没改到一行」必须报出来，而不是当成一次成功
                # （``SceneStore._update_record`` 同一条）。
                raise ActionItemConflictError(
                    f"action_id={item.action_id!r} 的处置没有落到盘上（rowcount=0）："
                    "盘上那一版与本次的基准已经不同了。请重读这一件。"
                )
            row = conn.execute(
                "SELECT payload FROM action_item WHERE action_id = ?",
                (item.action_id,),
            ).fetchone()
        assert row is not None
        stored = ActionItem.model_validate(
            owned(
                _load(row["payload"], f"action_item[{item.action_id}]"),
                where=f"action_item[{item.action_id}]",
                bound=bound,
            )
        )
        return ActionItemWrite(item=stored, revision_written=item.revision)

    # ------------------------------------------------------------ 读
    def get(self, action_id: str) -> ActionItem | None:
        with session(self._path) as conn:
            bound = workspace_binding(conn)
            return self._get(conn, action_id, bound)

    def items(
        self,
        *,
        workspace: str,
        state: ActionItemState | None = None,
        owner: str | None = None,
        open_only: bool = False,
    ) -> tuple[ActionItem, ...]:
        """未结的在前；同组内 ``(due_at 空值往后, due_at, action_id)``。

        `_CURRENT` 那句子查询在这里防的是：一件处置过的待办**不是**新版行（这一族是
        就地更新），所以「按 workspace 读全部」读到的每一条本来就是当前版 ——
        但这一句仍然要写，因为**一条将来会只增版本的行**（比如把处置历史落到另一张
        表）会让这里的每一件出现两次，而那时的表现是「待办多了一倍」。

        ``open_only`` 与 ``state`` 的差别见端口那一格；两者给的 ``?`` 都由
        :data:`~marketops.domain.action_item.OPEN_STATES` **算出来**，
        不写成字面量 —— 多写一遍状态名，会让「未结的是哪三个」在库里和代码里各有一份。

        ``owner`` 是 ``action_item_by_owner(workspace, owner, state)`` 那条索引存在的
        理由，而它**必须真的进 WHERE**：收了参数而不筛的实现在报告上与筛过的一模一样，
        而「这个人的待办」与「所有人的待办」是两句不同的话
        （``catalog.py`` 的 ``list_action_items`` 逐字「可按负责人与状态筛」）。
        ``owner=None`` = **不过滤**；没有主人的那几件要按 ``owner IS NULL`` 才筛得出来，
        而「不过滤」与「筛没有主人的」是两件事，所以这里不把 ``None`` 当条件。
        """
        if state is not None:
            where_state = " AND t.state = ?"
            state_params: list[object] = [state.value]
        elif open_only:
            where_state = " AND t.state IN ({})".format(", ".join("?" * len(OPEN_STATES)))
            state_params = [item.value for item in OPEN_STATES]
        else:
            where_state = ""
            state_params = []
        where_owner = " AND t.owner = ?" if owner is not None else ""
        owner_params: list[object] = [] if owner is None else [owner]
        sql = (
            "SELECT action_id, payload FROM action_item t "
            "WHERE t.workspace = ?" + where_state + where_owner + " AND "
            + _current_sql("action_item", "action_id")
            + " ORDER BY CASE WHEN t.state IN ({}) THEN 0 ELSE 1 END, ".format(
                ", ".join("?" * len(OPEN_STATES))
            )
            + "CASE WHEN t.due_at IS NULL THEN 1 ELSE 0 END, "
            "t.due_at, t.action_id"
        )
        params = (
            [workspace]
            + state_params
            + owner_params
            + [item.value for item in OPEN_STATES]
        )
        with session(self._path) as conn:
            bound = workspace_binding(conn)
            rows = conn.execute(sql, tuple(params)).fetchall()
        return tuple(
            ActionItem.model_validate(
                owned(
                    _load(row["payload"], f"action_item[{row['action_id']}]"),
                    where=f"action_item[{row['action_id']}]",
                    bound=bound,
                )
            )
            for row in rows
        )

    # ------------------------------------------------------------ 内部
    def _get(
        self, conn: sqlite3.Connection, action_id: str, bound: str | None
    ) -> ActionItem | None:
        row = conn.execute(
            "SELECT revision, payload FROM action_item WHERE action_id = ? "
            "ORDER BY revision DESC LIMIT 1",
            (action_id,),
        ).fetchone()
        if row is None:
            return None
        return ActionItem.model_validate(
            owned(
                _load(row["payload"], f"action_item[{action_id}@{row['revision']}]"),
                where=f"action_item[{action_id}]",
                bound=bound,
            )
        )

    def _duplicate_failure(
        self,
        conn: sqlite3.Connection,
        item: ActionItem,
        exc: sqlite3.IntegrityError,
        bound: str | None,
    ) -> Exception:
        """插入失败之后的**诊断**：把话说明白，而判定已经发生过了。

        判据是上面那次 ``INSERT`` 被拒 —— 这一条读回来的东西只进消息，不参与判定
        （``ports/views.py::ActionItemStore.create`` 逐字）。所以它**不是**「先查后插」：
        一个先查后插的实现在这里根本走不到（它在 INSERT 之前就返回了），而它在两个
        写者之间会放行两条。

        两种失败要分得开：主键（同一个 ``action_id`` 又被写了一次，而内容不同 ——
        上一步已经处理过内容相同的那一支）与那条 partial unique index
        （同一主体同一类已经有一条未结的）。
        """
        row = conn.execute(
            "SELECT action_id FROM action_item WHERE action_id = ?",
            (item.action_id,),
        ).fetchone()
        if row is not None:
            return StoreError(
                f"action_id={item.action_id!r} 的第 1 版已经在了，而本次内容与它不同"
                f"（{exc}）。同一个 id 配两份内容是一次真的冲突。"
            )
        existing = conn.execute(
            "SELECT t.action_id, t.state FROM action_item t "
            "WHERE t.workspace = ? AND t.action_type = ? AND t.scope_ref = ? "
            "AND t.state IN ({}) LIMIT 1".format(
                ", ".join("?" * len(OPEN_STATES))
            ),
            (
                item.workspace,
                item.action_type.value,
                item.scope_ref,
                *[state.value for state in OPEN_STATES],
            ),
        ).fetchone()
        named = (
            "盘上没有找到那一条 —— 这条插入失败来自别的约束，请原样报出去。"
            if existing is None
            else (
                f"盘上那一条是 action_id={existing['action_id']!r}"
                f"（状态 {existing['state']}）"
            )
        )
        return OpenActionItemExistsError(
            f"({item.workspace!r}, {item.action_type.value!r}, {item.scope_ref!r}) 上"
            "已经有一条**未结**的待办了，而这是同一主体上的同一类工作的第二条。\n"
            f"{named}。\n"
            "同一主体上的同一类未结待办只应有一条 —— 没有这条约束，每个 Run 都会新增"
            "一条「核实转介联系人」，而报告上看起来只是「待办多了一些」。\n"
            "正确动作是去处置那一条（或者在它上面加证据），不是再建一条。"
            "**已结的可以有任意多条**：它做完之后，同一主体上的下一件就建得出来了。"
        )
