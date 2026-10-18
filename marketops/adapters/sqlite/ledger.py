"""预算账本：**预留 → 结算**，而不是「花完再加」。

## 它替代的是什么

在它之前，预算是一个 JSON 文件加一句 ``used += 1``。那个做法有两个独立的毛病，
而且都不报错：

1. **读-改-写不是原子的。** 两个 worker 都读到 ``used = 3``，都写回 ``4``，
   于是四次调用只记了三次。多进程下这是必然，不是偶发。
2. **它只记「已经花了」。** 一次正在途中的调用不占任何额度，所以「上限 20 次」
   允许 20 个调用同时起飞 —— 而它们全部会花钱。

所以这里的模型是**两段式**：调用之前先 ``reserve``，拿到结果之后再 ``spend``。
额度在**起飞前**就被扣住，而不是落地后才记上。上限因此约束的是「同时在途 +
已经花掉」，那才是真正会让账单超支的那个量。

## 「超时了怎么办」—— 这个问题的答案是不写代码

§8 逐字：「在超时或 provider-unknown 时，不要默认释放预算」。这里**没有**任何
超时处理。一次超时的调用，它的预留就停在 ``RESERVED``，一直扣着额度，直到有人
拿证据来对账。

这不是「还没实现」，这是设计：超时之后**你并不知道对方是否已经计费**。自动退款
等于替 provider 做了「这次不算钱」的假设，而这个假设错误时的现象是「账单比
账本多」—— 一个在本地永远观察不到的现象。

要把它写错，你得显式调用 ``refund(..., reason=...)`` 并写明理由。理由是必填的
非空字符串：让「我什么都没查，先把钱要回来」这件事在代码里**写不出来**，
比在文档里禁止它可靠。

## 并发正确性靠 ``BEGIN IMMEDIATE``

上限检查与写入在同一个写事务里（见 ``db.py``）。测试
``tests/test_budget_ledger.py::test_the_race_control_shows_what_the_transaction_prevents``
用确定的读-改-写顺序演示了没有事务时会超支 —— 那条正对照存在的理由，是让
「原子」这个词在仓库里对应一个**会红的测试**，而不是一句形容词。

## 它实现的是 ``ports.budget.BudgetLedger``

语义（两段式、不自动退款、上限要留名）写在那个模块里，本文件只负责**怎么存**。
返回值类型与 id 的推导在 ``domain/budget.py`` —— 它们必须跨实现一致，所以不在
这里。与 ``adapters/memory.py`` 的 ``InMemoryBudgetLedger`` 同一组判据见
``tests/test_budget_contract.py``；本文件之外的那条（并发）见
``tests/test_budget_ledger.py``。
"""

from __future__ import annotations

from pathlib import Path

from ...domain.budget import BudgetUsage, Reservation, reservation_id
from ...domain.enums import BudgetLine, ReservationState
from ...domain.errors import (
    BudgetExhaustedError,
    BudgetNotConfiguredError,
    IdempotencyConflictError,
    ReservationStateError,
)
from ...domain.util import utc_now
from ...ports.budget import BudgetLedger
from .db import ensure_schema, session, write_transaction


def _row_to_reservation(row) -> Reservation:
    return Reservation(
        reservation_id=row["reservation_id"],
        scope=row["scope"],
        line=BudgetLine(row["line"]),
        units=int(row["units"]),
        state=ReservationState(row["state"]),
        idempotency_key=row["idempotency_key"],
        created_at=row["created_at"],
        settled_at=row["settled_at"],
        note=row["note"],
    )


class SqliteBudgetLedger(BudgetLedger):
    """基于 SQLite 的预算账本。

    构造参数是 ``db_path`` 而不是 ``Settings``：账本必须能在没有配置、没有
    ``os.environ`` 的情况下对着一个临时文件被测出来。用 ``Settings`` 会让每条
    账本测试都先构造一份完整配置，而那正是「纯单元测试」要避免的负担。
    """

    def __init__(self, db_path: Path) -> None:
        self._path = db_path
        with session(self._path) as conn:
            ensure_schema(conn)

    # ------------------------------------------------------------------
    # 上限：服务端事实
    # ------------------------------------------------------------------
    def set_ceiling(
        self, scope: str, line: BudgetLine, units: int, *, source: str
    ) -> None:
        """设定（或调整）一个科目的上限。

        ``source`` 必填：上限是谁定的，必须能从账本本身回答。§8
        「the caller cannot silently expand them」—— 调用方不能悄悄扩大上限，
        而「悄悄」的反面不是「不许改」，是「改了要留名」。

        调低到**低于已占用**会失败：那会让账本立刻处于「已经超支」的状态，
        而超支之后 ``remaining`` 恒为 0，所有后续预留都被拒 —— 那是个看起来
        像 bug 的合法状态。拒绝它，让操作者显式处理在途的那几笔。
        """
        if units < 0:
            raise ValueError("上限不能为负")
        if not source.strip():
            raise ValueError("设定上限必须写明来源（source）—— 见 §8：调用方不得静默扩大上限")
        with session(self._path) as conn, write_transaction(conn):
            held = self._held(conn, scope, line)
            if units < held:
                raise BudgetExhaustedError(
                    f"{scope} / {line.value} 已占用 {held}，不能把上限设为 {units}。\n"
                    "先处置在途的预留（结算或退款），再调低上限。"
                )
            conn.execute(
                "INSERT INTO budget_ceiling (scope, line, units, source) VALUES (?, ?, ?, ?) "
                "ON CONFLICT (scope, line) DO UPDATE SET units = excluded.units, source = excluded.source",
                (scope, line.value, units, source),
            )

    # ------------------------------------------------------------------
    # 两段式：预留 → 结算
    # ------------------------------------------------------------------
    def reserve(
        self, scope: str, line: BudgetLine, units: int, *, idempotency_key: str
    ) -> Reservation:
        """在**起飞前**扣住额度。

        Raises:
            BudgetNotConfiguredError: 该科目没有上限。未配置不等于无限。
            BudgetExhaustedError: 预留会越过上限。
            IdempotencyConflictError: 同一个键配了不同的 (line, units)。
        """
        if units <= 0:
            raise ValueError("预留必须是正数 —— 0 单位不占额度，等于没有预留")
        if not idempotency_key.strip():
            raise ValueError("幂等键不能为空：没有键就无法回答『这是重试还是新请求』")
        rid = reservation_id(scope, line, idempotency_key)

        with session(self._path) as conn, write_transaction(conn):
            existing = self._get(conn, rid)
            if existing is not None:
                if existing.line is not line or existing.units != units:
                    raise IdempotencyConflictError(
                        f"幂等键 {idempotency_key!r} 已经用于 "
                        f"{existing.line.value}/{existing.units}，现在被用于 {line.value}/{units}。\n"
                        "同一个键的承诺是『重发不会执行两次』，不是『同一个键可以改内容』。"
                    )
                return existing  # 重发：不重复扣

            ceiling = self._ceiling(conn, scope, line)
            if ceiling is None:
                raise BudgetNotConfiguredError(
                    f"{scope} / {line.value} 没有配置上限。\n"
                    "未配置**不等于**无限：把缺省当无限，会让『忘写配置』与『故意放开』"
                    "在行为上不可区分。先 set_ceiling（要写明 source），再用。"
                )
            held = self._held(conn, scope, line)
            if held + units > ceiling:
                raise BudgetExhaustedError(
                    f"{scope} / {line.value} 上限 {ceiling}，已占用 {held}（含在途），"
                    f"本次还要 {units}。\n"
                    "已占用**包含尚未结算的预留** —— 一次超时未对账的尝试会一直扣着它的额度。"
                )
            conn.execute(
                "INSERT INTO budget_reservation "
                "(reservation_id, scope, line, units, state, idempotency_key, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    rid,
                    scope,
                    line.value,
                    units,
                    ReservationState.RESERVED.value,
                    idempotency_key,
                    utc_now().isoformat(),
                ),
            )
            return self._get(conn, rid)

    def spend(self, reservation_id: str) -> Reservation:
        """把预留落成实际花费。幂等 —— 重复结算不重复计。"""
        return self._settle(reservation_id, ReservationState.SPENT, note=None)

    def refund(self, reservation_id: str, *, reason: str) -> Reservation:
        """把预留退回。``reason`` 必填非空 —— 见模块 docstring。

        **只有**这条路径能把额度要回来。它需要一个人写出理由，所以没有哪条
        超时分支能顺手替它做决定。
        """
        if not reason.strip():
            raise ValueError(
                "退款必须写明理由。\n"
                "这是刻意的：超时之后并不确定对方是否已经计费，自动退款等于替 provider "
                "做了『这次不算钱』的假设，而它错误时只在账单上可见。"
            )
        return self._settle(reservation_id, ReservationState.REFUNDED, note=reason.strip())

    # ------------------------------------------------------------------
    # 对账与查询
    # ------------------------------------------------------------------
    def usage(self, scope: str) -> tuple[BudgetUsage, ...]:
        """``scope`` 下每个科目的用量分解，按科目名排序。"""
        with session(self._path) as conn:
            rows = conn.execute(
                "SELECT c.line AS line, c.units AS ceiling, "
                "  COALESCE(SUM(CASE WHEN r.state = 'spent'    THEN r.units END), 0) AS spent, "
                "  COALESCE(SUM(CASE WHEN r.state = 'reserved' THEN r.units END), 0) AS reserved, "
                "  COALESCE(SUM(CASE WHEN r.state = 'refunded' THEN r.units END), 0) AS refunded "
                "FROM budget_ceiling c LEFT JOIN budget_reservation r "
                "  ON r.scope = c.scope AND r.line = c.line "
                "WHERE c.scope = ? GROUP BY c.line, c.units ORDER BY c.line",
                (scope,),
            ).fetchall()
        return tuple(
            BudgetUsage(
                line=BudgetLine(row["line"]),
                ceiling=int(row["ceiling"]),
                spent=int(row["spent"]),
                reserved=int(row["reserved"]),
                refunded=int(row["refunded"]),
            )
            for row in rows
        )

    def remaining(self, scope: str, line: BudgetLine) -> int:
        """还剩多少额度。

        **没有上限时抛，不返回 0。** 返回 0 会让「这个科目没配置」与「这个科目
        已经用完」变成同一个数字 —— 而调用方看到 0 的正常反应是「停下来报告
        预算耗尽」，于是忘写配置会伪装成一次正常的预算耗尽。同一个 ``reserve``
        在这里抛 ``BudgetNotConfiguredError``，两个方法对同一件事必须给出同一个
        答复。
        """
        for entry in self.usage(scope):
            if entry.line is line:
                return entry.remaining
        raise BudgetNotConfiguredError(
            f"{scope} / {line.value} 没有配置上限。"
            "不要把它读成 0 —— 0 的意思是「用完了」，而这两件事的处置不同。"
        )

    def open_reservations(self, scope: str | None = None) -> tuple[Reservation, ...]:
        """还没结算的预留 —— **对账的入口**。

        这个方法是「超时不自动退款」这条设计的配套：不自动退款意味着一定会有
        停在 ``RESERVED`` 的行，那么必须有一个地方能把它们列出来，否则「有多少
        额度悬着」就只能靠翻库。按创建时间排序，最老的先看。
        """
        sql = (
            "SELECT * FROM budget_reservation WHERE state = 'reserved' "
            + ("" if scope is None else "AND scope = ? ")
            + "ORDER BY created_at, reservation_id"
        )
        params = () if scope is None else (scope,)
        with session(self._path) as conn:
            rows = conn.execute(sql, params).fetchall()
        return tuple(_row_to_reservation(row) for row in rows)

    def get(self, reservation_id: str) -> Reservation | None:
        with session(self._path) as conn:
            return self._get(conn, reservation_id)

    # ------------------------------------------------------------------
    # 内部
    # ------------------------------------------------------------------
    def _settle(
        self, reservation_id: str, target: ReservationState, *, note: str | None
    ) -> Reservation:
        with session(self._path) as conn, write_transaction(conn):
            current = self._get(conn, reservation_id)
            if current is None:
                raise ReservationStateError(f"没有这条预留：{reservation_id}")
            if current.state is target:
                return current  # 幂等：重复结算不重复计
            if current.state is not ReservationState.RESERVED:
                raise ReservationStateError(
                    f"预留 {reservation_id} 当前是 {current.state.value}，不能变成 {target.value}。\n"
                    + (
                        "已经花掉的钱不能退款 —— 那不是记账，是改写用量。"
                        if target is ReservationState.REFUNDED
                        else "已经退款的预留不能再结算 —— 额度已经还回去了。"
                    )
                )
            conn.execute(
                "UPDATE budget_reservation SET state = ?, settled_at = ?, "
                "note = COALESCE(?, note) WHERE reservation_id = ?",
                (target.value, utc_now().isoformat(), note, reservation_id),
            )
            return self._get(conn, reservation_id)

    @staticmethod
    def _get(conn, reservation_id: str) -> Reservation | None:
        row = conn.execute(
            "SELECT * FROM budget_reservation WHERE reservation_id = ?", (reservation_id,)
        ).fetchone()
        return None if row is None else _row_to_reservation(row)

    @staticmethod
    def _ceiling(conn, scope: str, line: BudgetLine) -> int | None:
        row = conn.execute(
            "SELECT units FROM budget_ceiling WHERE scope = ? AND line = ?",
            (scope, line.value),
        ).fetchone()
        return None if row is None else int(row["units"])

    @staticmethod
    def _held(conn, scope: str, line: BudgetLine) -> int:
        """已占用 = spent + reserved。``refunded`` **不计**。

        这个 ``WHERE`` 子句是 §8 那条规则的落点：把 ``reserved`` 算进来，一次
        超时未对账的尝试就自动继续占着额度；把它去掉，超时等于自动退款。
        """
        row = conn.execute(
            "SELECT COALESCE(SUM(units), 0) AS held FROM budget_reservation "
            "WHERE scope = ? AND line = ? AND state IN ('spent', 'reserved')",
            (scope, line.value),
        ).fetchone()
        return int(row["held"])

__all__ = ["SqliteBudgetLedger"]
