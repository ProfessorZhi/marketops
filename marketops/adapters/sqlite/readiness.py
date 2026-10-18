"""部署前那一次**只读**的探问：这个状态库现在能不能被这一版代码打开。

## 它答的是哪一问

``ensure_schema`` 已经答过这一问 —— 在**写路径**上，抛 ``StoreError``。而在那之前，
一个部署什么都看不见：2026-09-23 的现场里，一个 v1 的库让**每一条**读写该库的工具
都失败，而失败长成的样子是调用方模型上下文里一句没有 message 的
``Error executing tool``（详见 ``docs/PRODUCT_LOOP_ACCEPTANCE_2026-09-24.md`` §G2）。
「工具坏了」与「库还没迁移」是两件事，处置也不同（去查代码 vs 跑一条命令），
而当时唯一分得出它们的是一句没有正文的错误 —— 那不该是唯一的诊断面。

所以这一层把同一个判据搬到**起服务之前**：``--check`` 与 ``_serve()`` 共用这一份
读数（``interfaces/mcp/__main__`` 的 ``_Composition.database``）。判据本身与三句
拒绝的原文都不在这里，在 :func:`~marketops.adapters.sqlite.db.read_schema_state`；
这一层只做两件事：**只读地拿到它**，以及把它翻成一句运维照着能做的事。

## 「只读」的确切含义

* **打不开就不建。** 库文件不在时**不打开它**，于是不建库、也不建它的父目录 ——
  ``--check`` 是只读的自检，``tests/test_mcp_entry.py`` 钉着这一条（一个会把目录
  树建出来的自检，会在「目录还没准备好」这种真正的故障面前先把故障修掉一半）。
* **用 ``mode=ro`` 打开。** 实测（2026-09-24，本机 3.12）：``immutable=1`` 在这里
  **不能用** —— WAL 模式下数据可能还只在 ``-wal`` 里，而 ``immutable=1`` 不看它，
  于是「表还不在」会被读成「这个库是空的」（实测原文：``no such table: schema_meta``）。
  那一读法在备份文件上是对的（那边就是要读主文件那一刻的字节），在这里会给出一个
  **过期**的版本号 —— 迁移刚提交、还没 checkpoint 时，它会说「这个库还是 v1」。
* **一处副作用要写下来。** ``mode=ro`` 连一个 WAL 的库时会**建出** ``-wal`` /
  ``-shm`` 两个伴生文件（实测）。所以「只读」指的是**不改库的内容**，不是「盘上
  一个字节都不多」；在一个目录不可写的部署里这一步会失败，而那句原话
  （``attempt to write a readonly database``）会被如实报成 :attr:`UNREADABLE`，
  不会变成一个猜测的结论。

## 它不答什么

它不答「这个库里的数据对不对」，也不答「迁移会不会成功」—— 那是迁移自己的预检
（它在任何一次写入之前把每一行证一遍）。这里只答版本与绑定这两格。
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .db import SCHEMA_VERSION, SchemaStanding, read_schema_state

__all__ = ["DatabaseReadiness", "read_database_readiness"]


@dataclass(frozen=True)
class DatabaseReadiness:
    """一个部署的状态库现在处在哪一格，以及要不要先做点什么。

    字段（``payload()`` 恒为这七个键）：

    ``path``
        这个库在盘上的绝对路径。它同时是 ``message`` 里点名的那一个。

    ``standing``
        :class:`~marketops.adapters.sqlite.db.SchemaStanding` 的取值。

    ``recorded`` / ``expected``
        库上写着的版本与这一版代码期望的版本。**读不出来时是 ``None``**，不是 0 ——
        一个把读不出来报成 0 的实现，会让「这个库没有版本号」与「这个库是 v0」
        在报告里长得一样。

    ``headline`` / ``message``
        ``headline`` 是一行摘要（给人扫的），``message`` 是**要说给运维的那整句**
        （含那条要跑的命令），可以什么都没有 —— 空串表示这一格没什么要说的，
        而不是「没什么要说的所以省了」。

    ``startable``
        这个部署**现在能不能起服务**。由 ``standing`` 算出来的属性，不是一个
        另填的字段：两处各写一遍，会出现「报告说可以起、启动时拒绝」。

    ``absent`` 与 ``uninitialized`` 都是**起得来**的：它们答的是「还没有历史」，
    而第一次启动本来就要把库建出来。
    """

    path: str
    standing: SchemaStanding
    recorded: int | None
    expected: int
    message: str

    @property
    def startable(self) -> bool:
        return self.standing in _STARTABLE

    @property
    def headline(self) -> str:
        """一行：这个库现在是什么处境。细节在 ``message`` 里。"""
        if self.standing is SchemaStanding.ABSENT:
            return "还没有状态库（第一次写入会建它）"
        if self.standing is SchemaStanding.UNINITIALIZED:
            return f"状态库还没有版本那一行（第一次写入会盖章到 v{self.expected}）"
        if self.standing is SchemaStanding.CURRENT:
            return f"schema v{self.expected}，代码期望 {self.expected} —— 一致"
        if self.standing is SchemaStanding.BEHIND:
            return f"schema v{self.recorded}，代码期望 {self.expected} —— 要迁移"
        if self.standing is SchemaStanding.AHEAD:
            return f"schema v{self.recorded}，代码期望 {self.expected} —— 比代码新"
        if self.standing is SchemaStanding.UNKNOWN:
            return "schema 版本那一格不是一个版本号"
        if self.standing is SchemaStanding.UNBOUND:
            return f"schema v{self.expected}，但有行却没有工作区绑定"
        return "打不开这个库"

    def payload(self) -> dict[str, Any]:
        """规范形状。``--check`` 与 ``_serve()`` 共用这一份 —— 两个表面对同一个进程
        各序列化一遍，漂移的那天同一格会在两处长得不一样。"""
        return {
            "path": self.path,
            "standing": self.standing.value,
            "recorded": self.recorded,
            "expected": self.expected,
            "startable": self.startable,
            "headline": self.headline,
            "message": self.message,
        }

    def render(self) -> str:
        """给终端看的那一段：一行摘要，之后（有的话）是整句与它点名的命令。"""
        if not self.message:
            return f"状态库 {self.path}：{self.headline}"
        return f"状态库 {self.path}：{self.headline}\n{self.message}"


#: 起得来的那三种。与 ``db._STARTABLE_STANDINGS`` 是**同一个集合的两处写法**吗？
#: 不是 —— 那一份是「这一版代码能不能打开它」（``ensure_schema`` 的判据），
#: 这一份是「这个部署能不能起服务」。今天两者的成员恰好相同，而它们回答的是两个
#: 问题：``ABSENT`` 那一格根本不在前者的作用域里（没有库就没有连接），它在这里
#: 却是实打实的「可以起」。
_STARTABLE: frozenset[SchemaStanding] = frozenset(
    {SchemaStanding.ABSENT, SchemaStanding.UNINITIALIZED, SchemaStanding.CURRENT}
)


def _has_schema_meta(conn: sqlite3.Connection) -> bool:
    """这个库里有没有 ``schema_meta`` 这张表。

    没有它有两种情形，而这一层**不区分它们**：本服务还没写过的空文件，与一个
    什么都没有的 ``sqlite3`` 空库。理由与 ``read_schema_state`` 对「没有版本那一行」
    的处置是同一条 —— ``ensure_schema`` 对这两者是同一个动作（重放 DDL、盖章），
    而自检若在这里说得比它更细，就会出现「自检说这个库不行、启动时它照常盖了章」。
    自检的每一句话都必须是**启动时真的会发生的那件事**。
    """
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'schema_meta' LIMIT 1"
    ).fetchone()
    return row is not None


def read_database_readiness(db_path: Path) -> DatabaseReadiness:
    """只读地问一次：这个库现在能不能被这一版代码打开。

    Args:
        db_path: 状态库的路径。**允许它不存在**（那是 ``ABSENT``，不是错误）。

    Returns:
        一份 :class:`DatabaseReadiness`。打不开、读不出来、版本对不上都是**结论**
        而不是异常 —— 一次部署前的自检抛出去，调用方只能选择打堆栈或吞掉，
        而两样都不是它被写出来的用途。
    """
    path = str(db_path)
    if not db_path.exists():
        return DatabaseReadiness(path, SchemaStanding.ABSENT, None, SCHEMA_VERSION, "")
    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    except sqlite3.Error as exc:
        return DatabaseReadiness(
            path,
            SchemaStanding.UNREADABLE,
            None,
            SCHEMA_VERSION,
            f"打不开状态库 {path}：{type(exc).__name__}: {exc}",
        )
    try:
        conn.row_factory = sqlite3.Row
        if not _has_schema_meta(conn):
            return DatabaseReadiness(
                path, SchemaStanding.UNINITIALIZED, None, SCHEMA_VERSION, ""
            )
        state = read_schema_state(conn, where=path)
        return DatabaseReadiness(
            path, state.standing, state.recorded, state.expected, state.message
        )
    except sqlite3.Error as exc:
        # 文件在、但不是一个数据库（``file is not a database``）、或者某一格读不出来。
        # 那句原话照报：它是唯一能区分「路径指错了」与「这个文件坏了」的东西。
        return DatabaseReadiness(
            path,
            SchemaStanding.UNREADABLE,
            None,
            SCHEMA_VERSION,
            f"读不了状态库 {path}：{type(exc).__name__}: {exc}",
        )
    finally:
        conn.close()
