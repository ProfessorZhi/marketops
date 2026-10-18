"""``WakeupRegistry`` 的 SQLite 实现：一个工作区一行唤醒归属。

判据全在 ``ports/continuation.py``，这里只做「**在同一个写事务里读-判-写**」这一件
存储引擎才会的事。三个动词的判定都必须这样，理由与 ``SqliteJobStore`` 的 ``set_state``
逐字相同：读到的行与据此写下的行若不在同一个事务里，两个写入者最后一个写的赢，而
**两边都以为自己成功了**。

## 归属是**一行**，不是一份名单

``continuation_owner`` 的主键是 ``workspace``。一个工作区在任一时刻最多一个持有者 ——
这一条不需要靠调用方的自觉，它是主键约束本身。若做成一张历史表（谁什么时候持有过），
「现在的持有者是谁」就要靠一次 ``ORDER BY`` 加一句读代码的人自己相信的排序来回答，
而那个答案会随并发插入漂移。

## 时序：``acquired_at`` 在被顶掉时**重置**

一条被顶掉的行的 ``acquired_at`` 换成这一代的起点，不是留着上一代的。留着它会让
``heartbeat_at >= acquired_at`` 这条不变式在**第一次心跳之后**仍然成立、但读起来
「这一代从上一代开始就在跳」，而报告里那个时刻会被读成「他什么时候接手的」。
"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path

from ...domain.continuation import WakeupOwner
from ...domain.util import to_utc
from ...ports.continuation import WakeupRegistry
from .db import ensure_schema, session, write_transaction


def _require_text(value: str, field: str) -> str:
    if not value or value != value.strip():
        raise ValueError(f"{field} 不能为空或首尾带空白")
    return value


def _row_to_owner(row) -> WakeupOwner:
    return WakeupOwner(
        workspace=row["workspace"],
        holder=row["holder"],
        epoch=row["epoch"],
        acquired_at=row["acquired_at"],
        heartbeat_at=row["heartbeat_at"],
    )


class SqliteWakeupRegistry(WakeupRegistry):
    """构造参数是 ``db_path``，与 ``SqliteJobStore`` 同一条理由：记录是一个纯状态机，
    不需要知道自己是哪个 profile 跑出来的。"""

    def __init__(self, db_path: Path) -> None:
        self._path = db_path
        with session(self._path) as conn:
            ensure_schema(conn)

    def acquire(self, workspace: str, *, holder: str, now: datetime) -> WakeupOwner | None:
        _require_text(workspace, "workspace")
        _require_text(holder, "holder")
        at = to_utc(now)
        with session(self._path) as conn, write_transaction(conn):
            row = conn.execute(
                "SELECT * FROM continuation_owner WHERE workspace = ?", (workspace,)
            ).fetchone()
            if row is None:
                owner = WakeupOwner(
                    workspace=workspace,
                    holder=holder,
                    epoch=1,
                    acquired_at=at,
                    heartbeat_at=at,
                )
                conn.execute(
                    "INSERT INTO continuation_owner "
                    "(workspace, holder, epoch, acquired_at, heartbeat_at) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (
                        owner.workspace,
                        owner.holder,
                        owner.epoch,
                        owner.acquired_at.isoformat(),
                        owner.heartbeat_at.isoformat(),
                    ),
                )
                return owner
            current = _row_to_owner(row)
            if not current.is_stale(at):
                # 别人活着。**不顶掉、也不当自己** —— 见 ``WakeupRegistry.acquire``。
                return None
            owner = WakeupOwner(
                workspace=workspace,
                holder=holder,
                epoch=current.epoch + 1,
                acquired_at=at,
                heartbeat_at=at,
            )
            conn.execute(
                "UPDATE continuation_owner SET holder = ?, epoch = ?, acquired_at = ?, "
                "heartbeat_at = ? WHERE workspace = ?",
                (
                    owner.holder,
                    owner.epoch,
                    owner.acquired_at.isoformat(),
                    owner.heartbeat_at.isoformat(),
                    workspace,
                ),
            )
            return owner

    def heartbeat(self, workspace: str, *, holder: str, epoch: int, now: datetime) -> bool:
        _require_text(workspace, "workspace")
        _require_text(holder, "holder")
        at = to_utc(now)
        with session(self._path) as conn, write_transaction(conn):
            # 三格全对才算续期。少了 epoch，一次换代之后回来的旧持有者会把心跳写回去
            # —— 他的 ``holder`` 与新一代可以逐字相同。
            cursor = conn.execute(
                "UPDATE continuation_owner SET heartbeat_at = ? "
                "WHERE workspace = ? AND holder = ? AND epoch = ?",
                (at.isoformat(), workspace, holder, epoch),
            )
            return cursor.rowcount == 1

    def release(self, workspace: str, *, holder: str, epoch: int) -> bool:
        _require_text(workspace, "workspace")
        _require_text(holder, "holder")
        with session(self._path) as conn, write_transaction(conn):
            cursor = conn.execute(
                "DELETE FROM continuation_owner WHERE workspace = ? AND holder = ? AND epoch = ?",
                (workspace, holder, epoch),
            )
            return cursor.rowcount == 1

    def current(self, workspace: str) -> WakeupOwner | None:
        with session(self._path) as conn:
            row = conn.execute(
                "SELECT * FROM continuation_owner WHERE workspace = ?", (workspace,)
            ).fetchone()
        return None if row is None else _row_to_owner(row)


__all__ = ["SqliteWakeupRegistry"]
