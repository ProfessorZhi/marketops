"""SQLite 会话绑定存储（``ports/session.py::SessionBindingStore`` 的落点，**主存**）。

## 这一个是唯一一个「写 = 就地更新」的表，而理由是对象本身

``session_binding`` 的主键是 ``binding_id``，**没有版本列** —— 它与
``saved_view`` / ``watch`` / ``schedule_definition`` / ``notification_subscription``
那四张只增版本的表刻意不同。理由在 ``domain/session.py::SessionBinding.refocused``
上逐字：这条绑定是一个便利指针，不是一次授权，而给一个不是权威的东西一版一行，
会让「我换了一下在看哪个视图」在盘上留下一条需要运维清理的审计痕迹。

于是本模块里有两条 ``UPDATE``（``focus_refs`` / ``report_cursor`` / ``expires_at`` /
``last_seen``），而那四张表的存储里一条都没有。**判据在写入之前**：先按
``binding_id`` 找、再按会话四格找（``put_binding`` 的两步，见下面那段）。

## 没有一条查询在解析 ``focus_refs``

这一层不 JOIN 任何东西、不查任何一张别的表来「看看那个视图在不在」——
``focus_refs`` 出库时是字符串，进判断时也是字符串。一个来找「这些 ref 指的东西
还在不在」的人在这份代码里找不到那条路：它没有写。这正是「不是 Account/Job 的
外键」这句话在存储层的形态（那条纪律的可执行形态在端口上，这里只是不发明它）。

## ``UNIQUE (workspace, principal, client_kind, client_session_ref)``

SQLite 把 ``NULL`` 当作互不相等，所以两条 ``client_session_ref IS NULL`` 的绑定
各自成立 —— 这是**想要的**（同一个人的两个无 id 会话是两条，``db.py`` 那一格逐字）。
这条约束挡的是「同一个会话被写成两条」，而那种情形下两条绑定会在「这个会话上次在
看什么」上给出两个答案。
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from ...domain.errors import StoreError
from ...domain.session import SessionBinding
from ...domain.util import to_utc
from ...ports.session import BindingWrite, SessionBindingStore
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

class SqliteSessionBindingStore(SessionBindingStore):
    """基于 SQLite 的会话绑定存储。构造参数只有 ``db_path``。"""

    def __init__(self, db_path: Path) -> None:
        self._path = db_path
        with session(self._path) as conn:
            ensure_schema(conn)

    def put_binding(self, binding: SessionBinding) -> BindingWrite:
        """写入或就地更新一条绑定。两步判据与理由见 ``ports/session.py::put_binding``。"""
        where = f"session_binding[{binding.binding_id}]"
        with session(self._path) as conn, write_transaction(conn):
            bind_workspace(conn, binding.workspace, error=StoreError)
            bound = workspace_binding(conn)
            self._require_id_free(conn, binding, bound)
            existing = self._by_session(conn, binding, bound)
            if existing is not None:
                # 只 SET 三格：``expires_at`` / ``last_seen`` / ``payload``。
                # ``focus_refs`` 与 ``report_cursor`` **没有列**（DDL 上只有一段
                # ``payload``），所以它们换的时候走的是那一整段 —— 而这也是这一族
                # 「整块存、只摊要查的字段」那条规矩的形态：没有一处 SQL 按焦点过滤，
                # 于是它不该有一列。
                #
                # **``binding_id`` 取盘上那一行的**，不是本次交上来的：那一行是
                # 主键，而主键不 UPDATE（``WHERE binding_id = ?`` 用的就是它）。
                # 把交上来的 id 写进 payload，会让这一行「主键说 A、JSON 说 B」——
                # 而按 B 查不到这一行（``get_binding(B)`` 是 ``None``），于是调用方
                # 拿着一个「刚刚被回给它」的 id 去做下一次写入，那次写入会**新插一行**。
                kept = binding.model_copy(update={"binding_id": existing.binding_id})
                conn.execute(
                    "UPDATE session_binding SET expires_at = ?, last_seen = ?, "
                    "payload = ? WHERE binding_id = ?",
                    (
                        None
                        if kept.expires_at is None
                        else to_utc(kept.expires_at).isoformat(),
                        to_utc(kept.last_seen).isoformat(),
                        _dump(kept.model_dump(mode="json")),
                        existing.binding_id,
                    ),
                )
                row = conn.execute(
                    "SELECT payload FROM session_binding WHERE binding_id = ?",
                    (existing.binding_id,),
                ).fetchone()
                assert row is not None  # 刚 UPDATE 成功
                stored = SessionBinding.model_validate(
                    owned(
                        _load(row["payload"], f"session_binding[{existing.binding_id}]"),
                        where=f"session_binding[{existing.binding_id}]",
                        bound=bound,
                    )
                )
                return BindingWrite(binding=stored, created=False)
            try:
                conn.execute(
                    "INSERT INTO session_binding "
                    "(binding_id, workspace, principal, client_kind, client_session_ref, "
                    "expires_at, last_seen, payload) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        binding.binding_id,
                        binding.workspace,
                        binding.principal,
                        binding.client_kind,
                        binding.client_session_ref,
                        None
                        if binding.expires_at is None
                        else to_utc(binding.expires_at).isoformat(),
                        to_utc(binding.last_seen).isoformat(),
                        _dump(binding.model_dump(mode="json")),
                    ),
                )
            except sqlite3.IntegrityError as exc:
                # UNIQUE 会话四格。**在写事务里读得到那一行**，所以这条拒绝说得清
                # 是谁先写的 —— 而它与「同一个会话又动了一下焦点」是两件事，
                # 后者走的是上面那条 UPDATE（它按会话找，不按 id 找）。
                raise StoreError(
                    f"会话 ({binding.workspace!r}, {binding.principal!r}, "
                    f"{binding.client_kind!r}, {binding.client_session_ref!r}) 上已经"
                    f"有一条绑定了，而本次要写的 binding_id={binding.binding_id!r} "
                    f"是另一个（{exc}）。\n"
                    "同一个会话被写成两条，会在「这个会话上次在看什么」上给出两个"
                    "答案 —— 而两条各自都是一份合法的记录，谁也不会报错。"
                ) from exc
            row = conn.execute(
                "SELECT payload FROM session_binding WHERE binding_id = ?",
                (binding.binding_id,),
            ).fetchone()
        assert row is not None  # 刚 INSERT 成功
        stored = SessionBinding.model_validate(
            owned(_load(row["payload"], where), where=where, bound=bound)
        )
        return BindingWrite(binding=stored, created=True)

    def get_binding(self, binding_id: str) -> SessionBinding | None:
        where = f"session_binding[{binding_id}]"
        with session(self._path) as conn:
            bound = workspace_binding(conn)
            row = conn.execute(
                "SELECT payload FROM session_binding WHERE binding_id = ?", (binding_id,)
            ).fetchone()
        if row is None:
            return None
        return SessionBinding.model_validate(
            owned(_load(row["payload"], where), where=where, bound=bound)
        )

    def bindings(
        self, *, workspace: str, principal: str | None = None
    ) -> tuple[SessionBinding, ...]:
        """本工作区的绑定，按 ``(principal, client_kind, binding_id)`` 升序。"""
        sql = "SELECT binding_id, payload FROM session_binding WHERE workspace = ?"
        params: list[object] = [workspace]
        if principal is not None:
            sql += " AND principal = ?"
            params.append(principal)
        sql += " ORDER BY principal, client_kind, binding_id"
        with session(self._path) as conn:
            bound = workspace_binding(conn)
            rows = conn.execute(sql, tuple(params)).fetchall()
        return tuple(
            SessionBinding.model_validate(
                owned(
                    _load(row["payload"], f"session_binding[{row['binding_id']}]"),
                    where=f"session_binding[{row['binding_id']}]",
                    bound=bound,
                )
            )
            for row in rows
        )

    # -------------------------------------------------------------- 内部
    def _require_id_free(
        self, conn: sqlite3.Connection, binding: SessionBinding, bound: str | None
    ) -> None:
        """第 1 步：这个 ``binding_id`` 若已经在盘上，它必须是**同一个会话**的。

        ``client_session_ref`` 的比较用 ``IS``：两端都可能为 ``NULL``，而
        ``NULL = NULL`` 在 SQL 里是 ``NULL``（不是真）—— 用 ``=`` 会让两条无 id
        的绑定被当成同一会话，而它们各自是一条（``db.py`` 那一格逐字）。
        """
        row = conn.execute(
            "SELECT workspace, principal, client_kind, client_session_ref "
            "FROM session_binding WHERE binding_id = ?",
            (binding.binding_id,),
        ).fetchone()
        if row is None:
            return
        same = (
            row["workspace"] == binding.workspace
            and row["principal"] == binding.principal
            and row["client_kind"] == binding.client_kind
            and row["client_session_ref"] == binding.client_session_ref
        )
        if same:
            return
        raise StoreError(
            f"binding_id={binding.binding_id!r} 在盘上属于另一个会话：盘上是 "
            f"({row['workspace']!r}, {row['principal']!r}, {row['client_kind']!r}, "
            f"{row['client_session_ref']!r})，本次是 ({binding.workspace!r}, "
            f"{binding.principal!r}, {binding.client_kind!r}, "
            f"{binding.client_session_ref!r})。\n"
            "规范 id 是由会话推出来的（domain/session.py::session_binding_id），"
            "所以这一格只可能是调用方自己造了一个撞车的 id。静默覆盖会让**前一个"
            "会话**的焦点凭空变成另一个人的。"
        )

    def _by_session(
        self, conn: sqlite3.Connection, binding: SessionBinding, bound: str | None
    ) -> SessionBinding | None:
        """第 2 步：按**会话四格**找那一行（不是按 id 找）。

        按 id 找的实现会让「同一个会话带着一个新铸的 id 写第二次」撞在那条 UNIQUE
        上 —— 而那句报错读起来像「有人先写了」，实际发生的事是「同一个会话又动了
        一下焦点」，处置完全不同（一个要重读，一个什么都不用做）。

        ## ``client_session_ref IS NULL`` 的那一支**不按会话找**

        没有稳定会话引用时，那四格里有一格是没有内容的，而 ``NULL = NULL`` 在
        SQLite 里不成立 —— ``client_session_ref IS ?`` 会把两个**没有 id 的会话**
        读成同一个。那不是它们的关系：两个都没有 id 的客户端是两个人各自的一次会话，
        而合成一行之后，B 读到的是 A 的焦点（这条纪律的上游形态就是 §7.1 那句
        「服务端不能保存一个全局的 current_scene」）。

        这一支的身份因此是**调用方给的那个 ``binding_id``**
        （``domain/session.py::session_binding_id`` 在 ref 为 ``None`` 时拒绝推导 id，
        正是为了让这件事显式）：不同 id ⇒ 两行（那条 UNIQUE 在 NULL 上是放行的），
        同一个 id 再写一次 ⇒ 还是那一行的就地更新。
        """
        if binding.client_session_ref is None:
            row = conn.execute(
                "SELECT binding_id, payload FROM session_binding WHERE workspace = ? "
                "AND principal = ? AND client_kind = ? AND client_session_ref IS NULL "
                "AND binding_id = ?",
                (
                    binding.workspace,
                    binding.principal,
                    binding.client_kind,
                    binding.binding_id,
                ),
            ).fetchone()
        else:
            row = conn.execute(
                "SELECT binding_id, payload FROM session_binding WHERE workspace = ? "
                "AND principal = ? AND client_kind = ? AND client_session_ref IS ?",
                (
                    binding.workspace,
                    binding.principal,
                    binding.client_kind,
                    binding.client_session_ref,
                ),
            ).fetchone()
        if row is None:
            return None
        return SessionBinding.model_validate(
            owned(
                _load(row["payload"], f"session_binding[{row['binding_id']}]"),
                where=f"session_binding[{row['binding_id']}]",
                bound=bound,
            )
        )
