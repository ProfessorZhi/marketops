"""SQLite 待办与裁决：**主存**（``ports/review.py`` 的落点）。

## 两张表，两种完全相反的写法，而这正是它们分两张的理由

* ``review_case`` 一行是**当前状态**：认领、裁决、合并都**覆盖**它，配一个乐观锁
  序号 ``version``。
* ``human_resolution`` 一行是**历史**：只增不改，一行都不许动。

把两者合成一张表（每次改动追加一行、读的时候取最后一行）会让「这件案件等了多少天」
与「它被改过几次」只能靠扫描历史算，而 :func:`inbox_flow` 的恒等式与「谁提议的、
谁批的」本来就分别住在这两半上。写成两半，每一半里那种写法的正确性只需要被论证一次。

## 判据不在这一层

:func:`apply_resolution` 里的三条判据（决定属于这件案件、工作区一致、它还没被合并、
结果在可选项里）是**关于那两个对象**的，与库无关 —— 所以它们住在 domain，而这一层
只做两件**只有它才能做**的事：

* 读旧行与写新行落在**同一个** ``BEGIN IMMEDIATE`` 事务里。两个人同时裁决同一件时，
  「先 SELECT 再 UPDATE」的两者都读到同一版、各自算出新状态，后写的那一次把前一次
  整个吃掉 —— 而它的表现不是报错，是**一次没人看得见的丢失**。
* ``expected_version`` 与盘上 ``version`` 的比对**在事务之内**。同一个库的另一个进程
  提交之后，这一层看到的版本已经不是调用方读到的那一版，于是整次拒绝（陈旧 attempt
  不覆盖新结果，``docs/ARCHITECTURE.md:51``）。

## 为什么裁决那一行是 ``INSERT`` 而不是 ``ON CONFLICT DO NOTHING``

证据那一半用 ``DO NOTHING`` 是对的：``evidence_id`` 是内容寻址的，同一个 id 配同一份
内容就是**同一件事**，重放不该报错（``evidence_store.py`` 的模块 docstring）。
``resolution_id`` 不是内容寻址的 —— 它是调用方给的一次事件 id，而这个端口**不去重**
（``ports/review.py`` 逐字：「它记的是一件已经发生的事」）。所以这里用裸 ``INSERT``：
撞主键是一次真实的重复写入，``sqlite3.IntegrityError`` 被翻成 ``StoreError`` 报出去，
而不是被静默吞成一次成功。吞掉它的后果是「同一件事被记成两条」这件事在盘上读不出来。

## ``scene_id`` 没有外键，``case_id`` 有

见 ``db.py`` 里那两张表的建表注释：一件待办在它的场景归档之后仍然是一句说得通的
问题（「这条规则当时该不该这样判」与场景的生死无关）；而一条**没有案件的裁决**会从
:func:`inbox_flow` 的分组里静默消失 —— 恒等式照样成立，因为那一行不进任何一格。
"""

from __future__ import annotations

import sqlite3
from datetime import datetime
from pathlib import Path

from ...domain.continuation import intents_for_resolution
from ...domain.errors import ReviewConflictError, StoreError
from ...domain.review import (
    PENDING_STATES,
    HumanResolution,
    ReviewCase,
    apply_resolution,
    claim,
)
from ...domain.util import utc_now
from ...ports.review import CaseAdmission, ReviewCaseStore
from .continuation_rows import owe
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

#: ``list_cases`` 的排序里那一格「是不是未决」。由 :data:`PENDING_STATES` **算出来**，
#: 不写成字面量：多写一遍状态名，会让「未决的是哪三个」在库里和代码里各有一份，
#: 而两者漂移的表现是报告的第一行换了一件 —— 没有人会去看那个。
_PENDING_FIRST = "CASE WHEN state IN ({}) THEN 0 ELSE 1 END".format(
    ", ".join("?" * len(PENDING_STATES))
)


class SqliteReviewCaseStore(ReviewCaseStore):
    """基于 SQLite 的 Review Inbox（§12、§B20）。

    构造参数只有 ``db_path``：与另外几张表同一个理由 —— 存储要能在没有配置、没有
    ``os.environ`` 的情况下对着一个临时文件被测出来。**没有合并规则**：这一族没有
    「同一把键配两份内容怎么折」的问题（``ports/review.py`` 逐字：``open_case`` 由问题
    驱动、``record_resolution`` 不做去重）。

    建表在 ``__init__`` 里做（``ensure_schema`` 幂等）。``CREATE TABLE IF NOT EXISTS``
    是自愈的：**加这两张表不需要动 schema 版本号，也不需要一次迁移** —— 这就是
    ``db.py`` 的「DDL 只增不改」换来的那件事。
    """

    def __init__(self, db_path: Path) -> None:
        self._path = db_path
        with session(self._path) as conn:
            ensure_schema(conn)

    # ------------------------------------------------------------ 开案
    def open_case(self, case: ReviewCase) -> CaseAdmission:
        """把一件待办放上盘。**同一个问题第二次调用不新开、不覆盖。**

        ``ON CONFLICT (case_id) DO NOTHING`` 之后的一次读取判定「这一次是不是它被开
        出来的那一次」—— 与 ``put_evidence`` 逐字同一个形状（``evidence_store.py``
        的模块 docstring 写了为什么不是「先 SELECT 再决定 INSERT」：那会让一个
        **别人抢先**的正常结局被报成 ``IntegrityError``）。

        读回来的那一行**不覆盖**：一件已经裁决过的案件不会被系统自己重开
        （``ports/review.py`` 那一格写了为什么）。所以 ``opened=False`` 时返回的是
        盘上那一件，而它的 ``state`` 与本次传入的那一件可能不同 —— 那个差别正是
        调用方要报出去的东西。
        """
        with session(self._path) as conn, write_transaction(conn):
            bind_workspace(conn, case.workspace, error=StoreError)
            bound = workspace_binding(conn)
            inserted = conn.execute(
                "INSERT INTO review_case "
                "(case_id, workspace, scene_id, reason, subject_ref, state, "
                "proposed_version, version, opened_at, updated_at, payload, stored_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT (case_id) DO NOTHING",
                (
                    case.case_id,
                    case.workspace,
                    case.scene_id,
                    case.reason.value,
                    case.subject_ref,
                    case.state.value,
                    case.proposed_version,
                    case.version,
                    case.opened_at.isoformat(),
                    case.updated_at.isoformat(),
                    _dump(case.model_dump(mode="json")),
                    utc_now().isoformat(),
                ),
            ).rowcount
            row = conn.execute(
                "SELECT payload FROM review_case WHERE case_id = ?",
                (case.case_id,),
            ).fetchone()
        assert row is not None  # 刚 INSERT 过（或它本来就在），这一行必然存在
        stored = ReviewCase.model_validate(
            owned(
                _load(row["payload"], case.case_id),
                where=f"review_case[{case.case_id}]",
                bound=bound,
            )
        )
        return CaseAdmission(case=stored, opened=bool(inserted))

    # ------------------------------------------------------------ 读
    def get_case(self, case_id: str) -> ReviewCase | None:
        """按 id 读一件。``None`` 只有一个意思：**没有这个 id 的案件**。"""
        with session(self._path) as conn:
            bound = workspace_binding(conn)
            row = conn.execute(
                "SELECT payload FROM review_case WHERE case_id = ?", (case_id,)
            ).fetchone()
        if row is None:
            return None
        return ReviewCase.model_validate(
            owned(
                _load(row["payload"], case_id),
                where=f"review_case[{case_id}]",
                bound=bound,
            )
        )

    def list_cases(self) -> tuple[ReviewCase, ...]:
        """**未决的在前、早的在前**（``ports/review.py`` 说那顺序是契约的一部分）。

        第二、三格（``opened_at``、``case_id``）都不省：``opened_at`` 是微秒级 ISO
        字符串，两件在同一微秒里开出来是可能的（一次 tick 里对两个主体各开一件），
        而 ``ORDER BY`` 在一个并列的键上给出的顺序**由实现决定**。第三格把它钉死 ——
        不然「现在最该看的那一件」会在两次调用之间换一个答案，而两份报告都自洽。
        """
        states = sorted(state.value for state in PENDING_STATES)
        with session(self._path) as conn:
            bound = workspace_binding(conn)
            rows = conn.execute(
                "SELECT case_id, payload FROM review_case "
                f"ORDER BY {_PENDING_FIRST}, opened_at, case_id",
                tuple(states),
            ).fetchall()
        return tuple(
            ReviewCase.model_validate(
                owned(
                    _load(row["payload"], row["case_id"]),
                    where=f"review_case[{row['case_id']}]",
                    bound=bound,
                )
            )
            for row in rows
        )

    def list_resolutions(self, case_id: str) -> tuple[HumanResolution, ...]:
        """一件案件的**全部**决定，早的在前（同刻按 ``resolution_id``）。

        同刻那一格的第二根键与 :func:`_events_by_case` 逐字相同 —— 两处不能各排各的：
        ``inbox_flow`` 读的是这里排好的序列，而它判「最后一个事件是什么」。
        """
        with session(self._path) as conn:
            bound = workspace_binding(conn)
            rows = conn.execute(
                "SELECT resolution_id, payload FROM human_resolution "
                "WHERE case_id = ? ORDER BY answered_at, resolution_id",
                (case_id,),
            ).fetchall()
        return tuple(
            HumanResolution.model_validate(
                owned(
                    _load(row["payload"], row["resolution_id"]),
                    where=f"human_resolution[{row['resolution_id']}]",
                    bound=bound,
                )
            )
            for row in rows
        )

    # ------------------------------------------------------------ 认领
    def claim(self, case_id: str, *, by: str, at: datetime, expected_version: int) -> ReviewCase:
        """认领一件。**与 ``record_resolution`` 逐字同一个形状**，只是落点不同。

        同一个 ``BEGIN IMMEDIATE`` 里读旧行、过 :func:`~marketops.domain.review.claim`
        的判据、把新行写回去。这里**不写历史表** —— 认领不是一次决定，它改的是当前
        状态（``ports/review.py`` 的模块 docstring 写了为什么）。

        **没有 ``bind_workspace``**：这一件已经在盘上了，而它落盘那次已经把库绑好了
        （``bind_workspace`` 是**第一行写入**建立绑定的地方）。这里要的判据是反向的
        那一条 —— 「这一行属不属于这个库」—— 由 :func:`owned` 给出（与 ``get_case``
        逐字同一条）。
        """
        with session(self._path) as conn, write_transaction(conn):
            bound = workspace_binding(conn)
            case = self._read_for_update(conn, case_id, bound, expected_version)
            changed = claim(case, by=by, at=at)
            self._write_case(conn, changed, expected_version)
        return changed

    # ------------------------------------------------------------ 裁决
    def record_resolution(
        self, resolution: HumanResolution, *, expected_version: int
    ) -> ReviewCase:
        """追加一条人的决定，并把案件推进到它的结果上。**一次写事务。**

        顺序是承重的：``BEGIN IMMEDIATE`` 在事务开始就拿写锁（``db.py`` 的模块
        docstring），所以下面那次 ``SELECT`` 与随后的两条写之间没有别人能插进来。

        ``version`` 的比对**在事务之内**，而 ``UPDATE`` 的 ``WHERE`` 里**仍然带着它**。
        后者不是多余的：在这个事务里它不可能失败（写锁已经拿到），而它失败时给出的
        是一句「事务边界丢了」——那是一次真正的重构事故（谁把 ``write_transaction``
        去掉，或把读挪到事务外），而它在那之前的表现是**一次静默的覆盖**。

        ## 这一步**同时**欠下下一步，而它们必须在同一个事务里

        架构 §9 逐字要求「**裁决后自动重评受影响对象**」，而这一行就是那个「裁决后」。
        欠什么由 ``domain/continuation.py::intents_for_resolution`` 决定（**策略在领域，
        事务在实现**），写在**这个**事务里由 ``ports/jobs.py`` 那段逐字文本决定：

        * 写在事务**之前** —— 一次被拒绝的裁决（上面 ``_read_for_update`` 里那两条
          ``ReviewConflictError``、或下面那句主键冲突）会给一件**没发生的事**留下一行
          欠账，于是永远有一次重评在等一个不存在的决定；
        * 写在事务**之后** —— 进程在这两句之间死掉时，决定在盘上而欠账不在，于是
          「这件裁决跑完之后还有一次重评」**永久丢失**，而流水账上完全看不出来。

        所以这里没有第二条路径：``self._write_case`` 与 ``owe`` 相邻，中间没有任何
        可能抛出的判定 —— 判定全在它们**之前**（同一个事务里，抛出去整件事一起回滚）。
        """
        with session(self._path) as conn, write_transaction(conn):
            bind_workspace(conn, resolution.workspace, error=StoreError)
            bound = workspace_binding(conn)
            case = self._read_for_update(
                conn, resolution.case_id, bound, expected_version
            )
            updated = apply_resolution(case, resolution)

            try:
                conn.execute(
                    "INSERT INTO human_resolution "
                    "(resolution_id, case_id, workspace, outcome, decided_by, "
                    "answered_at, payload, recorded_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        resolution.resolution_id,
                        resolution.case_id,
                        resolution.workspace,
                        resolution.outcome.value,
                        resolution.by,
                        resolution.at.isoformat(),
                        _dump(resolution.model_dump(mode="json")),
                        utc_now().isoformat(),
                    ),
                )
            except sqlite3.IntegrityError as exc:
                raise StoreError(
                    f"human_resolution[{resolution.resolution_id}] 已经在盘上了"
                    f"（{exc}）。\n"
                    "这个 id **不是**内容寻址的 —— 它是调用方给的一次事件 id，所以"
                    "撞主键是一次真实的重复写入，而不是一次重放。静默吞掉它，会让"
                    "「同一件事被记成两条」这件事在盘上读不出来：流水账上那一格会"
                    "多算一次，而两次写的内容可能已经不同了。"
                ) from exc
            self._write_case(conn, updated, expected_version)
            owed = intents_for_resolution(case, resolution)
            if owed:
                owe(conn, owed, at=utc_now())
        return updated

    # ------------------------------------------------------------ 两个动词共用的一半
    def _read_for_update(
        self, conn: sqlite3.Connection, case_id: str, bound: str | None, expected_version: int
    ) -> ReviewCase:
        """在**写事务之内**读这一件，并核它是不是调用方读到的那一版。

        提出来是因为认领与裁决走的是同一条并发判据（``expected_version``），而两处
        各写一遍的形态是「一边核了、一边忘了」——忘了的那一边的表现是**一次静默的
        丢失**，两份报告各自都自洽。
        """
        row = conn.execute(
            "SELECT payload FROM review_case WHERE case_id = ?", (case_id,)
        ).fetchone()
        if row is None:
            raise ReviewConflictError(
                f"{case_id!r} 不在盘上，本次调用以为它在第 {expected_version} 版。"
                "一个字都没写。"
            )
        case = ReviewCase.model_validate(
            owned(
                _load(row["payload"], case_id),
                where=f"review_case[{case_id}]",
                bound=bound,
            )
        )
        if case.version != expected_version:
            raise ReviewConflictError(
                f"{case.case_id!r} 现在是第 {case.version} 版（状态 "
                f"{case.state.value}），本次调用读到的是第 {expected_version} 版。"
                "一个字都没写。\n"
                "这一条挡的是两个人同时动同一件：最后写的那个赢，而**两边都以为"
                "自己成功了**。重读那一件，按它的当前状态重新决定。"
            )
        return case

    def _write_case(
        self, conn: sqlite3.Connection, case: ReviewCase, expected_version: int
    ) -> None:
        """把新的一行写回去。``WHERE`` 里带着版本，理由见 ``record_resolution``。"""
        changed = conn.execute(
            "UPDATE review_case SET workspace = ?, scene_id = ?, reason = ?, "
            "subject_ref = ?, state = ?, proposed_version = ?, version = ?, "
            "opened_at = ?, updated_at = ?, payload = ?, stored_at = ? "
            "WHERE case_id = ? AND version = ?",
            (
                case.workspace,
                case.scene_id,
                case.reason.value,
                case.subject_ref,
                case.state.value,
                case.proposed_version,
                case.version,
                case.opened_at.isoformat(),
                case.updated_at.isoformat(),
                _dump(case.model_dump(mode="json")),
                utc_now().isoformat(),
                case.case_id,
                expected_version,
            ),
        ).rowcount
        if changed != 1:
            raise StoreError(
                f"review_case[{case.case_id}] 在第 {expected_version} 版的条件下更新了 "
                f"{changed} 行 —— 而这次读与这次写**在同一个写事务里**，所以它不该"
                "发生。\n"
                "这只有一种成因：读旧行的那一次查询被挪到了事务外（或 "
                "write_transaction 被去掉了）。它的表现本来是一次静默的覆盖 —— "
                "两个人同时动同一件时，后写的那一次把前一次整个吃掉。"
            )


__all__ = ["SqliteReviewCaseStore"]
