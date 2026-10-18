"""SQLite 版的持久 Run 记录。

## 它实现的是 ``ports.runs.RunStore``

语义（受理幂等、冲突判据、授权窗口）写在那个模块里，本文件只负责**怎么存**。
返回值类型与 id 的推导在 ``domain/run.py`` —— 它们必须跨实现一致，所以不在任何
一个实现里。

## 受理是一次「读-判-写」，所以它必须在 ``BEGIN IMMEDIATE`` 里

``admit`` 读 ``(workspace, 键)`` 有没有、比 ``spec_sha256``、没有才插。少了那个
写事务，两个进程会在「读」与「写」之间都看到同一个空结果，于是两个都通过 ——
而那个失败不抛异常、不留日志，只在报告上表现为「同一轮跑了两遍」。与
``adapters/sqlite/jobs.py`` 的 ``admit`` 是同一处教训。

``stop`` 同形，而且是**同一处教训里更要紧的那个**：它读当前那一行、判「有没有
结束过」，没结束才写。少了写事务，两个写入者会在中间都看到「还没结束」，于是
**后写的那一份悄悄取代先落盘的那一份** —— 而先落盘那一份的产出没有任何一处记着
它丢过（那一行最终的 ``delta`` 只有一个，两个调用者都会拿到成功）。

**判据不在两个实现里各写一份**：``admission_refusal`` 与 ``window_refusal`` 都住在
``ports/runs.py``，两个实现都调它们。抄进实现的话，分家的表现是其中一个静默地受理
一个另一个拒绝的规格，而「两个实现都通过」这句话就没有依据了。

两条判据落地的**位置不同**，这是它们的输入不同决定的：``admission_refusal`` 只读
``spec`` 与 ``at``，所以在写事务之外、拿写锁**之前**；``window_refusal`` 要读盘上这
条链走到哪了，所以在 ``BEGIN IMMEDIATE`` **之内**、重放检查**之后**。次序的完整理由
在 ``ports/runs.py::RunStore.admit``。
"""

from __future__ import annotations

import sqlite3
from collections.abc import Mapping
from datetime import datetime
from pathlib import Path

from ...domain.enums import BudgetLine, RunStatus
from ...domain.errors import (
    IdempotencyConflictError,
    RunFoldLedgerError,
    RunSpecError,
    RunStateError,
    StoreError,
)
from ...domain.fold import FoldAttribution, FoldReceipt
from ...domain.run import Run, RunSpec, run_id
from ...domain.run_delta import RunDelta
from ...ports.runs import (
    ChainProgress,
    RunStore,
    admission_refusal,
    ending_refusal,
    fold_after_end_refusal,
    fold_ledger_refusal,
    fold_replay_conflict,
    progress_of,
    stopping_outcome,
    unknown_run_refusal,
    window_refusal,
)
from .db import bind_workspace, ensure_schema, session, write_transaction


def _require_sha256(value: str) -> str:
    """``spec_sha256`` 的形状判据。与 ``adapters/sqlite/jobs.py`` 那一段同款。

    它唯一的用途是判断「同一个键是不是同一个请求」，所以一个形状不对的值不会
    报错 —— 它只会让**每一次**提交都与别的不同，于是重发被读成新的。
    """
    if len(value) != 64 or any(ch not in "0123456789abcdef" for ch in value):
        raise ValueError(
            "spec_sha256 必须是 64 位小写十六进制。\n"
            "它是「同一个键配的是不是同一份规格」的唯一依据 —— 形状不对时那个判断"
            "会恒答「不是」，于是重发被受理成第二轮。"
        )
    return value


def record_fold(
    conn: sqlite3.Connection,
    fold: FoldAttribution,
    *,
    venue_asset_id: str,
    at: datetime,
) -> None:
    """在**一笔已经在跑的事务里**记下「这一轮折了池子这一行」。**不自己开事务。**

    调用点是 ``adapters/sqlite/pool.py::SqliteVenueAssetStore.upsert_asset``，位置就在
    它写 ``venue_asset`` 那一行的同一个写事务里 —— §B17 条件 2 的原子性就是这一件事：
    **池子的写与这一笔账同生共死**。所以这个函数刻意**不**接受「要不要开事务」这种
    参数，也不自己 ``session``：一个能自己开事务的版本会让两者可以分开提交，而那正好
    是要排除的形态。

    三个判定，次序有理由：

    1. **账本在不在** —— 不在就抛 :class:`~marketops.domain.errors.RunFoldLedgerError`
       （报文见 ``ports/runs.py::fold_ledger_refusal``）。**绝不静默跳过这一笔**：跳过
       会让「这一次没记」与「这个功能还没造」在盘上同形，而池子那一行已经落下去了。
    2. **这一轮结束了没有** —— 结束了就抛 ``RunStateError``。理由见
       ``ports/runs.py::fold_after_end_refusal``（一句话：结束那一刻的账已经结过了，
       之后落的这一笔没有任何一次核对会看见）。
    3. **重放** —— 同一个 ``(run_id, receipt_id, ordinal)`` 再来一次：句柄相同就是
       重放，**不写第二笔**（池子那一行本来就是覆盖写，也幂等）；句柄不同是一次完整性
       冲突，抛 ``StoreError``。为什么键是这三格而不是句柄，见 ``db.py`` 那两张表的
       DDL 注释。

    把这三条写在这里而不是抄进内存那份，是因为它们必须跨实现一致 —— 与
    ``admission_refusal`` / ``window_refusal`` 同一条分工。内存那份调用的是同一个
    ``fold_ledger_refusal``，只是它没有 SQL。
    """
    ledger = conn.execute(
        "SELECT 1 FROM run_fold_ledger WHERE run_id = ?", (fold.run_id,)
    ).fetchone()
    if ledger is None:
        known = conn.execute(
            "SELECT 1 FROM research_run WHERE run_id = ?", (fold.run_id,)
        ).fetchone()
        raise RunFoldLedgerError(
            fold_ledger_refusal(fold.run_id, run_exists=known is not None)
        )
    current = conn.execute(
        "SELECT stop_reason FROM research_run WHERE run_id = ?", (fold.run_id,)
    ).fetchone()
    # 账本在册而 Run 行不在，是一个由外键堵死的状态（账本行的 FK 指着那一行）。
    # 真出现了那是库被动过，与「账本不在」同一句拒词 —— 不另编一句话。
    if current is None:
        raise RunFoldLedgerError(fold_ledger_refusal(fold.run_id, run_exists=False))
    if current["stop_reason"] is not None:
        raise RunStateError(
            fold_after_end_refusal(fold.run_id, stop_reason=str(current["stop_reason"]))
        )
    existing = conn.execute(
        "SELECT venue_asset_id FROM run_fold "
        "WHERE run_id = ? AND receipt_id = ? AND ordinal = ?",
        (fold.run_id, fold.receipt_id, fold.ordinal),
    ).fetchone()
    if existing is not None:
        if str(existing["venue_asset_id"]) == venue_asset_id:
            return  # 重放：同一笔，不写第二遍
        raise StoreError(
            fold_replay_conflict(
                fold.run_id,
                fold.receipt_id,
                fold.ordinal,
                recorded=str(existing["venue_asset_id"]),
                incoming=venue_asset_id,
            )
        )
    # ``seq`` 在这一笔里自己数。``BEGIN IMMEDIATE`` 之下没有别人在写同一轮的账，
    # 所以「读最大值再加一」不是一次竞态。它显式落盘的理由见 db.py 的 DDL 注释（不挂
    # rowid：那是实现细节，一次 VACUUM 之后会重排而不报错）。
    next_seq = conn.execute(
        "SELECT COALESCE(MAX(seq), -1) + 1 FROM run_fold WHERE run_id = ?",
        (fold.run_id,),
    ).fetchone()[0]
    conn.execute(
        "INSERT INTO run_fold (run_id, seq, receipt_id, ordinal, venue_asset_id, "
        "recorded_at) VALUES (?, ?, ?, ?, ?, ?)",
        (
            fold.run_id,
            next_seq,
            fold.receipt_id,
            fold.ordinal,
            venue_asset_id,
            at.isoformat(),
        ),
    )


def fold_receipts(conn: sqlite3.Connection, rid: str) -> tuple[FoldReceipt, ...]:
    """这一轮的账。**不自己开事务**（两个调用点：``folds_of`` 一条新连接、
    ``adapters/sqlite/pool.py`` 那条已经在跑的写事务）。

    「账本不在」抛而不是返回空元组 —— 那是 ``folds_of`` 的全部要点，见
    ``ports/runs.py::RunStore.folds_of``。
    """
    ledger = conn.execute(
        "SELECT opened_at FROM run_fold_ledger WHERE run_id = ?", (rid,)
    ).fetchone()
    if ledger is None:
        known = conn.execute(
            "SELECT 1 FROM research_run WHERE run_id = ?", (rid,)
        ).fetchone()
        raise RunFoldLedgerError(fold_ledger_refusal(rid, run_exists=known is not None))
    rows = conn.execute(
        "SELECT receipt_id, ordinal, venue_asset_id, recorded_at FROM run_fold "
        "WHERE run_id = ? ORDER BY seq",
        (rid,),
    ).fetchall()
    return tuple(
        FoldReceipt(
            run_id=rid,
            receipt_id=str(row["receipt_id"]),
            ordinal=int(row["ordinal"]),
            venue_asset_id=str(row["venue_asset_id"]),
            at=datetime.fromisoformat(str(row["recorded_at"])),
        )
        for row in rows
    )


class SqliteRunStore(RunStore):
    """基于 SQLite 的 Run 记录。

    构造参数是 ``db_path`` 而不是 ``Settings``：理由与 ``SqliteJobStore`` 相同 ——
    记录是一个纯状态机，不该需要知道自己是哪个 profile 跑出来的。
    """

    def __init__(self, db_path: Path) -> None:
        self._path = db_path
        with session(self._path) as conn:
            ensure_schema(conn)

    # ------------------------------------------------------------------
    # 受理
    # ------------------------------------------------------------------
    def admit(
        self,
        spec: RunSpec,
        *,
        spec_sha256: str,
        at: datetime,
        spent: Mapping[BudgetLine, int] | None = None,
    ) -> Run:
        _require_sha256(spec_sha256)
        refusal = admission_refusal(spec, at=at)
        if refusal:
            raise RunSpecError(refusal)

        rid = run_id(spec.workspace, spec.idempotency_key)
        now = at.isoformat()
        with session(self._path) as conn, write_transaction(conn):
            existing = self._run(conn, rid)
            if existing is not None:
                if existing.spec_sha256 != spec_sha256:
                    raise IdempotencyConflictError(
                        f"幂等键 {spec.idempotency_key!r} 在工作区 {spec.workspace!r} 下"
                        f"已经用于另一份规格（{existing.spec_sha256[:16]}… ≠ "
                        f"{spec_sha256[:16]}…，Run {rid}）。\n"
                        "同一个键的承诺是「重发不会受理两次」，不是「同一个键可以改内容」。"
                        "要改目标、信封或截止，那是**另一个 Run** —— 改这一份会让"
                        "「这一轮当时是按什么跑起来的」再也读不回来。"
                    )
                return existing
            # 窗口那三条判定**在写事务里**（另一条路径在它前面：上面那句
            # ``return existing``）。放进来的理由是它在事务外读不到并发：两个同时
            # 提交的自动受理会各自看到 ``open_runs=0``、于是**两个都过**，而这条链的
            # 累计在两次受理里各少算对方一圈。放在重放那一支**之后**的理由在
            # ``ports/runs.py::RunStore.admit`` 的次序一段：重放的承诺是「拿回同一
            # 行」，不能因为链后来走到 stop 就变成拒绝。
            if spec.window is not None:
                # 这一句不是第二份判据，是**算参数**：``progress`` 要按这条链的摘要读，
                # 而无窗口的规格没有摘要。见 ``adapters/memory.py`` 同款那一句。
                #
                # 这里喂给 ``progress_of`` 的是**写事务这条连接**上的行，而不是调
                # ``self.chain_progress(...)`` —— 那一处会自己再开一次 ``session``，
                # 也就是**看不见本事务里还没提交的行**，而这一条判据要读的恰恰是
                # 「这条链现在有几行」。两个调用点只是把行喂给同一份折叠。
                #
                # 于是这里多读一次（同一段折叠走两遍的机会都没有：折叠只有
                # ``progress_of`` 那一份，判据只有 ``window_refusal`` 那一份）。
                refusal = window_refusal(
                    spec,
                    progress=progress_of(
                        spec.workspace,
                        spec.window.digest,
                        self._rows_of(conn, spec.workspace),
                    ),
                    spent=spent,
                )
                if refusal:
                    raise RunSpecError(refusal)
            # 绑定与那一行 INSERT **同生共死**：``bind_workspace`` 自己的 docstring
            # 要求它在写事务里被调用，而这里也是它唯一的调用点（``stop`` 那条路径
            # 不调 —— 它改的是一行已经在盘上的 Run，而那一行落盘那次已经把库绑好了，
            # 与 ``adapters/sqlite/review.py`` 对 ``resolve`` 那一处的说法逐字同一条
            # 理由）。
            #
            # 这一句为什么承重：``research_run`` 在 ``db.py::OWNED_TABLES`` 里（那一
            # 份的注释写明了理由 —— 它的每一行都写着 ``workspace``）。而
            # ``ensure_schema`` 有反向的那一条判据：**有带所有者的行却没有绑定 ⇒
            # 当场拒绝打开**，因为那一批行属于谁没有答案。少了这一句，写路径就会亲手
            # 造出那个它自己声称造不出来的状态：这一次受理成功，而**下一次**打开这个
            # 库 —— 任何 store 的构造、部署前的那次自检、以及 ``get_run`` 的每一次
            # 调用（组合根交给 ``run_handlers`` 的是 ``default_run_store(settings)``
            # 这个**工厂**，所以每次调用都新建一个 ``SqliteRunStore``）—— 都退
            # ``StoreError``。而回执上说这一轮被受理了，盘上也真的有一行。
            #
            # 类型是 ``StoreError`` 而不是 ``bind_workspace`` 的缺省 ``EvidenceError``：
            # 与 ``adapters/sqlite/fetch_attempt.py`` / ``pool.py`` 两处逐字同一条
            # 理由 —— 判词一份，类型归主语。
            bind_workspace(conn, spec.workspace, error=StoreError)
            conn.execute(
                "INSERT INTO research_run (run_id, workspace, spec_sha256, spec, "
                "stop_reason, delta, stopped_at, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, NULL, NULL, NULL, ?, ?)",
                (rid, spec.workspace, spec_sha256, spec.model_dump_json(), now, now),
            )
            # 这一轮的**折入账**在同一笔里开成空的：有账、里面零行。「这一轮一行都没
            # 折」与「没有人记过这一轮折了什么」因此是两个可分辨的状态 —— 只留后者
            # 读不出来，漏记就会长成零，而两者的处置相反（``ports/runs.py`` 的
            # ``folds_of``）。放在这一笔里的第二个理由：重放那一支在上面**就 return
            # 了**，所以一份账永远不会被重开 —— 重开会把已记下的折入擦掉。
            conn.execute(
                "INSERT INTO run_fold_ledger (run_id, opened_at) VALUES (?, ?)",
                (rid, now),
            )
            return self._run(conn, rid)

    def get(self, run_id: str) -> Run | None:  # noqa: A002 - 与端口签名逐字一致
        with session(self._path) as conn:
            return self._run(conn, run_id)

    # ------------------------------------------------------------------
    # 一次结束
    # ------------------------------------------------------------------
    def stop(
        self,
        run_id: str,  # noqa: A002 - 与端口签名逐字一致
        *,
        delta: RunDelta,
        status: RunStatus,
        at: datetime,
    ) -> Run:
        now = at.isoformat()
        with session(self._path) as conn, write_transaction(conn):
            current = self._run(conn, run_id)
            if current is None:
                raise RunStateError(unknown_run_refusal(run_id))
            # 读-判-写全在那个写事务里：少了它，两个写入者会在中间都看到「还没结束」，
            # 于是后写的那一份悄悄取代先落盘的那一份（模块 docstring 里那一段）。
            if self._already_ended(current, run_id=run_id, status=status, delta=delta):
                return current
            # 「这一套结局**能不能**成立」在「它是不是已经写过的那一套」之后问 ——
            # 两条路径的顺序在内存那份里逐字相同（一次已经结束过的重放该得到的是
            # 「与先前那一套不同」，不是「这个结局本身不合法」）。
            refusal = ending_refusal(status, delta)
            if refusal:
                raise RunSpecError(refusal)
            # 结局、产出、停表在**同一条 UPDATE** 里落下：表的 CHECK 要求三者同时有
            # 或同时无，所以拆成两句的那一半中间就会撞上约束（那正是我们要的）。
            conn.execute(
                "UPDATE research_run SET stop_reason = ?, delta = ?, stopped_at = ?, "
                "updated_at = ? WHERE run_id = ?",
                (status.value, delta.model_dump_json(), now, now, run_id),
            )
            stopped = self._run(conn, run_id)
            assert stopped is not None
            return stopped

    # ------------------------------------------------------------------
    # 这一轮折过哪几行
    # ------------------------------------------------------------------
    def folds_of(self, run_id: str) -> tuple[FoldReceipt, ...]:  # noqa: A002
        """见 ``ports/runs.py::RunStore.folds_of``。

        「账本不在」抛 ``RunFoldLedgerError`` 而不是返回空元组，是这一条的全部要点。
        SQL 在 :func:`fold_receipts` 里 —— 池子那一侧写账时读的是同一个函数，
        所以「写的时候看到的账」与「结束的时候读到的账」不可能是两张表。
        """
        with session(self._path) as conn:
            return fold_receipts(conn, run_id)

    @staticmethod
    def _already_ended(
        current: Run, *, run_id: str, status: RunStatus, delta: RunDelta
    ) -> bool:
        """``True`` = 这一轮已经以**同一套**结局与产出结束过，这是重放；否则抛。

        与 ``InMemoryRunStore._already_ended`` 逐条同义（也与 ``_already_done`` 那一对
        同形）：判据见 ``tests/test_run_ending_contract.py``，其中一条比对两个实现
        给出的报文**逐字相同**。

        ``False`` 在调用点只有一个意思：这一轮**还没结束**。比的是
        :func:`~marketops.ports.runs.stopping_outcome` 那个指纹 —— 内存那份把理由讲全了
        （比模型相等依赖 ``__eq__``，而「怎么算同一套结局」是端口那一层的定义）。
        """
        if not current.is_stopped:
            return False
        if stopping_outcome(current.stop_reason, current.delta) == stopping_outcome(
            status, delta
        ):
            return True
        raise RunStateError(
            f"Run {run_id} 已经结束了（{current.stop_reason.value}，"
            f"停表于 {current.stopped_at}），而这一次带上来的结局是 {status.value}、"
            "产出与先前那一份不同。\n"
            "一个 Run 只有一份结局与一份产出。**不覆盖** —— 覆盖会让后到的那一份悄悄"
            "取代先落盘的那一份，而先落盘那一份的产出**没有任何一处记着它丢过**。\n"
            "如果这是同一轮的两个不同结果，那是两个 Run 而不是一个。"
        )

    # ------------------------------------------------------------------
    # 读
    # ------------------------------------------------------------------
    def chain_progress(self, *, workspace: str, window_digest: str) -> ChainProgress:
        """见 ``ports/runs.py::RunStore.chain_progress``。

        ``WHERE workspace = ?`` 走 ``research_run_by_workspace`` 那条索引（它的注释
        逐字写着「这个工作区受理了哪几轮是唯一被真实问过的筛选」）。**这是多余的
        一次筛选**，不是判据：筛选与折叠的真正出处在共用的 ``progress_of`` 里。
        留着它的理由是它省一次全表扫描，代价是两处筛选可能分家 —— 契约测试里
        「两个实现逐字段相同」那一条就是为这个可能要的。

        **不加窗口列、不做迁移。** 窗口在 ``spec`` 这一份 payload 里逐字节存着，
        加一列会把「窗口是什么形状」摊进 DDL，而形状一改就是一次迁移
        （见 ``domain/run.py::spec_fingerprint``：窗口进散列，所以它进内容的程度与
        ``cutoff`` 一样）。折叠在 Python 里做，代价是这个工作区的每一行都要读回一次
        ``spec`` —— 今天一个工作区的行数就是它开过的轮数。
        """
        with session(self._path) as conn:
            runs = self._rows_of(conn, workspace)
        return progress_of(workspace, window_digest, runs)

    @staticmethod
    def _rows_of(conn: sqlite3.Connection, workspace: str) -> list[Run]:
        """这个工作区的全部行。**两个调用点**：``chain_progress``（自己开连接）与
        ``admit``（在写事务的那条连接上，见那里）。

        抽出来的是「按工作区取行」这一段，不是判据 —— 筛选与折叠都在
        ``progress_of`` 里。不抽的话，两处各写一遍 SQL，漂开的形态是**受理面读的
        那一批**与**``chain_progress`` 答的那一批**不是同一批，而两边看起来都正常：
        受理面会拿一份少算了的累计去判「到没到 stop」。
        """
        rows = conn.execute(
            "SELECT run_id, spec, spec_sha256, stop_reason, delta, stopped_at, "
            "created_at, updated_at FROM research_run WHERE workspace = ?",
            (workspace,),
        ).fetchall()
        return [SqliteRunStore._from_row(row) for row in rows]

    def _run(self, conn: sqlite3.Connection, rid: str) -> Run | None:
        """从一行读回一轮。**规格与 Delta 都原样吐回** —— 存的是它们自己的 JSON。"""
        row = conn.execute(
            "SELECT run_id, spec, spec_sha256, stop_reason, delta, stopped_at, "
            "created_at, updated_at FROM research_run WHERE run_id = ?",
            (rid,),
        ).fetchone()
        if row is None:
            return None
        return self._from_row(row)

    @staticmethod
    def _from_row(row: sqlite3.Row) -> Run:
        """一行 → 一轮。**两条读路径共用这一份**（``_run`` 与 ``chain_progress``）。

        分开写两份的形态是：某一列在一条路径上被读错了而另一条没有，于是
        「按 id 取回来的那一轮」与「按工作区取回来的同一轮」不是同一个对象 ——
        而两边看起来都正常。它不是 ``_run`` 的一行内联，正是为了不给第二个
        读路径留一次抄写的机会。
        """
        stopped = row["stop_reason"]
        raw_delta = row["delta"]
        return Run(
            run_id=str(row["run_id"]),
            spec=RunSpec.model_validate_json(str(row["spec"])),
            spec_sha256=str(row["spec_sha256"]),
            created_at=str(row["created_at"]),
            updated_at=str(row["updated_at"]),
            stop_reason=None if stopped is None else RunStatus(str(stopped)),
            delta=None if raw_delta is None else RunDelta.model_validate_json(str(raw_delta)),
            stopped_at=None if row["stopped_at"] is None else str(row["stopped_at"]),
        )


__all__ = ["SqliteRunStore", "fold_receipts", "record_fold"]
