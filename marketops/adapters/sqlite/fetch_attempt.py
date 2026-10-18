"""访问尝试账的 SQLite 落点：**主存**。

## 这一层与 ``adapters/http.py`` 的分工

抓取器**认得**这次尝试（它手里有响应对象、有诊断、有那一刻的钟），而认账的是这里。
两者分开的理由与 ``ports/fetch_attempt.py`` 那一节同源：抓取器的返回值是给编排层
当场做决定用的（``FetchResult``），而这一张表回答的是**三个月后**那句话。把落盘塞回
抓取器里，会让「一次抓取能不能被记下来」跟着某一次调用走 —— 而它应该在**每一次**
调用上都成立。

## 幂等与冲突的区别，与 ``evidence`` 那张表逐字同一个形状

    INSERT ... ON CONFLICT (attempt_id) DO NOTHING;
    SELECT payload WHERE attempt_id = ?;   -- 无论刚才是谁写的，现在这一行就是事实

用 ``DO NOTHING`` 而不是「先 SELECT 再决定 INSERT」是刻意的：后者在两个进程同时到达
时，两边都读到「不存在」，然后一边的 INSERT 撞主键抛 ``IntegrityError`` —— 而那是一个
**别人抢先的正常结局**被报成异常。``DO NOTHING`` 让它不需要被判定。

读到的那一行与据此下的判断包在同一个写事务里。按上面的推理即使不包也是正确的，包起来
是为了与 ``adapters/sqlite/evidence_store.py`` 保持同一个形状：读的人不需要分别判断
哪里可以松。

## 读的另一半：不属于这个工作区的行会被**拒绝**，而不是被跳过

``fetch_attempt`` 的每一行都带 ``workspace``，而库里另有一格部署绑定
（``schema_meta`` 的 ``workspace``）。两个值不一致时这一行不属于这个库，处置是抛
``StoreError`` —— 与 ``ports/pool.py`` 那一族逐字同一条（``get_attempt`` 的 ``None``
因此只有一个意思：**没有这个 id 的行**）。
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from ...domain.errors import StoreError
from ...domain.fetch_attempt import FetchAttempt
from ...domain.util import to_utc
from ...ports.fetch_attempt import FetchAttemptStore
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


class SqliteFetchAttemptStore(FetchAttemptStore):
    """基于 SQLite 的访问尝试账。

    构造参数是 ``db_path`` 而不是 ``Settings``：与 ``SqliteEvidenceStore`` /
    ``SqliteBudgetLedger`` 同一个理由 —— 存储必须能在没有配置、没有 ``os.environ``
    的情况下对着一个临时文件被测出来。

    建表在 ``__init__`` 里做（``ensure_schema`` 幂等），于是**构造即验证**：一个不可写
    或版本不匹配的库在第一个调用点就报错，而不是在跑了半小时之后。

    这一条与「只读工具不得建库」不冲突，两者管的是不同的事：那里说的是**一个查询**
    不该凭空空手建出一个库来（于是那次查询在构造存储**之前**就返回了）；这里说的是
    谁一旦决定要写，就该当场知道这个位置能不能写。组合根因此把构造推迟到第一次真的
    要记一笔的时候 —— 见 ``interfaces/composition.py`` 的 ``default_http``。
    """

    def __init__(self, db_path: Path) -> None:
        self._path = db_path
        with session(self._path) as conn:
            ensure_schema(conn)

    # ------------------------------------------------------------------
    def put_attempt(self, attempt: FetchAttempt) -> None:
        payload = attempt.model_dump(mode="json")
        with session(self._path) as conn, write_transaction(conn):
            # 池子那一侧按 ``StoreError`` 分支，所以绑定的拒绝也用同一个类型
            # （``bind_workspace`` 的 ``error`` 参数那一节：判词一份，类型归主语）。
            bind_workspace(conn, attempt.workspace, error=StoreError)
            conn.execute(
                "INSERT INTO fetch_attempt "
                "(attempt_id, workspace, scope, url, started_at, payload) "
                "VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT (attempt_id) DO NOTHING",
                (
                    attempt.attempt_id,
                    attempt.workspace,
                    attempt.scope,
                    attempt.url,
                    attempt.started_at.isoformat(),
                    _dump(payload),
                ),
            )
            row = conn.execute(
                "SELECT payload FROM fetch_attempt WHERE attempt_id = ?",
                (attempt.attempt_id,),
            ).fetchone()
            if _load(row["payload"], attempt.attempt_id) != payload:
                raise StoreError(
                    f"已有 attempt_id={attempt.attempt_id!r} 的记录，但内容与本次不同。\n"
                    "这个 id 算的是**这一次尝试**（谁、哪一跳、什么时候开始的），"
                    "所以同一个 id 配不同内容只可能意味着两个人对同一次尝试说了两套话"
                    "（例如一个说拿到了 200、另一个说超时）。静默保留先写的那条，会让"
                    "事后那句「这个 URL 那天到底怎么样了」有一个看不出来的错误答案。"
                )

    # ------------------------------------------------------------------
    def get_attempt(self, attempt_id: str) -> FetchAttempt | None:
        with session(self._path) as conn:
            bound = workspace_binding(conn)
            row = conn.execute(
                "SELECT payload FROM fetch_attempt WHERE attempt_id = ?", (attempt_id,)
            ).fetchone()
        if row is None:
            return None
        return FetchAttempt.model_validate(
            owned(
                _load(row["payload"], attempt_id),
                where=f"fetch_attempt[{attempt_id}]",
                bound=bound,
                error=StoreError,
            )
        )

    # ------------------------------------------------------------------
    def list_attempts(
        self,
        *,
        scope: str | None = None,
        url: str | None = None,
        since: datetime | None = None,
        limit: int | None = None,
    ) -> list[FetchAttempt]:
        """见 ``ports/fetch_attempt.py``。

        ## 筛选条件是按「有没有真的传」拼的，所以空串是一条真的筛选

        ``scope=""`` 是**一个值**（不属于任何主体的那些尝试），而 ``scope=None`` 是
        「不筛」。写成 ``if scope:`` 会把前者读成后者 —— 而它的表现是「这次运行什么
        都没抓到」与「不属于任何主体的抓取非常多」在报告上同形。

        ## ``since`` 与读回来的那一列是同一个刻度

        比较在 SQL 里做，对着一个**已经过 ``to_utc`` 归一**的参数（下面第一行）：
        ``started_at`` 那一列写进去的就是 ``to_utc(...).isoformat()``。两处都用 UTC，
        于是「按北京时间下午三点之后筛」这件事由调用方换算一次，而不是让字符串比较
        去猜两个时区的关系 —— ``+08:00`` 的串与 ``Z`` 的串按字典序比会给出一个
        看起来像答案的结果。
        """
        clauses: list[str] = []
        params: list[object] = []
        if scope is not None:
            clauses.append("scope = ?")
            params.append(scope)
        if url is not None:
            clauses.append("url = ?")
            params.append(url)
        if since is not None:
            clauses.append("started_at >= ?")
            params.append(to_utc(since).isoformat())

        sql = "SELECT payload FROM fetch_attempt"
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY started_at DESC, attempt_id DESC"
        if limit is not None:
            sql += " LIMIT ?"
            params.append(limit)

        with session(self._path) as conn:
            bound = workspace_binding(conn)
            rows = conn.execute(sql, tuple(params)).fetchall()
        return [
            FetchAttempt.model_validate(
                owned(
                    _load(row["payload"], "fetch_attempt"),
                    where="fetch_attempt",
                    bound=bound,
                    error=StoreError,
                )
            )
            for row in rows
        ]


__all__ = ["SqliteFetchAttemptStore"]
