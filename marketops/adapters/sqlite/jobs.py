"""SQLite 版的持久作业记录。

## 它实现的是 ``ports.jobs.JobStore``

语义（受理幂等、CAS、写一次的映射、排他租约、不自动过期）写在那个模块里，
本文件只负责**怎么存**。返回值类型与 id 的推导在 ``domain/job.py`` —— 它们必须
跨实现一致，所以不在任何一个实现里。

## 每一条动词都是「读-判-写在同一个写事务里」

这是本模块唯一真正难的地方，也是 ``db.py`` 那个模块存在的理由。四个动词各自
有一个**读-判-写**的形状，而它们在单线程测试里全都正确：

* ``admit``  —— 读 ``(scene, key)`` 有没有、比 ``request_sha256``、没有才插；
* ``lease``  —— 读有没有活租约、判持有人、没有才插；
* ``map_execution`` —— 读有没有映射、判 task id、没有才插；
* ``settle`` —— 读 attempt 状态、读 work item 的理由、读作业状态、才写。

少了 ``BEGIN IMMEDIATE``，两个进程会在「读」与「写」之间都看到同一个旧值，
于是两个都通过。这个失败不抛异常、不留日志，只在报告上表现为「同一件事做了
两遍」—— 而 §8B 逐字禁止的正是它。

## 三处刻意留在应用层的约束

``db.py`` 的 DDL 里有 ``CHECK`` 与 ``UNIQUE``，但**排他租约**那条不在其中。
SQLite 的部分唯一索引（``WHERE state = 'leased'``）能表达它，可是内存那份实现
没有等价物 —— 而 ``tests/test_job_contract.py`` 会在两个实现上各跑一遍。约束
若只写在其中一个实现里，另一个会**静默地不守它**，而「两个实现都通过」就变成
了一句没有依据的话。所以那条判据住在两个实现都要满足的地方：写事务里的读-判-写。

同理，``admit`` 的冲突判据是 ``request_sha256`` 而不是逐字段比较：存储层不知道
「哪些字段算输入」，那是工具接口的知识。
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Sequence
from datetime import datetime
from pathlib import Path
from typing import Any

from ...domain.continuation import (
    BLOCKED_BACKOFF,
    BlockedOn,
    Continuation,
    ContinuationClaim,
    ContinuationKind,
    ContinuationState,
    after_failure,
    intents_for_admission,
    intents_for_settlement,
    retry_delay,
)
from ...domain.enums import JOB_TRANSITIONS, AttemptState, JobInputKind, JobState, StopReason
from ...domain.errors import (
    ContinuationError,
    IdempotencyConflictError,
    JobStateError,
    LeaseError,
)
from ...domain.findings import Findings, findings_payload, findings_from_payload
from ...domain.job import (
    ExecutionRef,
    JobAttempt,
    ResearchJob,
    Settlement,
    WorkItem,
    WorkItemSnapshot,
    attempt_id,
    job_id,
    work_item_id,
)
from ...domain.profile import SceneFile, SceneFiles
from ...domain.subject import SubjectDossier, dossier_from_payload
from ...domain.util import to_utc, utc_now
from ...ports.jobs import (
    JobStore,
    repin_refusal,
    resnapshot_refusal,
    settlement_outcome,
)
from .continuation_rows import (
    CONTINUATION_COLUMNS,
    flatten,
    owe,
)
from .db import ensure_schema, session, write_transaction


def _require_text(value: str, field: str) -> str:
    if not value or value != value.strip():
        raise ValueError(f"{field} 不能为空或首尾带空白")
    return value


def _row_to_continuation(row) -> Continuation:
    return Continuation(
        continuation_id=row["continuation_id"],
        scene_id=row["scene_id"],
        kind=ContinuationKind(row["kind"]),
        business_key=row["business_key"],
        subject_kind=row["subject_kind"],
        subject_ref=row["subject_ref"],
        why=row["why"],
        state=ContinuationState(row["state"]),
        due_at=row["due_at"],
        epoch=row["epoch"],
        holder=row["holder"],
        checkpoint=row["checkpoint"],
        attempts=row["attempts"],
        reason=row["reason"],
        blocked_on=None if row["blocked_on"] is None else BlockedOn(row["blocked_on"]),
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def scene_files_from_rows(rows: Sequence[Any]) -> SceneFiles | None:
    """``scene_snapshot`` 的行 → 那批字节；**空行集返回 ``None``**。

    空 = 本部署没有留下这一份内容，**不是**「与盘上现在那份相同」—— 后者是一个在
    报告上看起来完全正常的错误答案。调用方必须把 ``None`` 当成「读不到」来判。

    这一条**只有一处实现**，因为 ``scene_snapshot`` 有两个读者：作业存储按
    ``scene_version`` 直接取（:meth:`SqliteJobStore.scene_files`），判定存储按
    ``work_item_id`` 先取钉住的版本再取字节（``SqliteDecisionRecordStore.pinned_scene``）。
    两处各写一遍「怎么把行拼成值」，就是同一个事实两个来源 —— 而这里漂移的后果
    恰好是两者对「字节还在不在」答得不一样。
    """
    if not rows:
        return None
    return SceneFiles(
        entries=tuple(
            SceneFile(path=row["path"], content=bytes(row["content"])) for row in rows
        )
    )


def _marks(values: Sequence[str]) -> str:
    """``?, ?, ?`` —— 长度只由 ``len(values)`` 决定。**绝不用 f-string 拼值本身**。"""
    return ",".join("?" * len(values))


def _require_epoch(epoch: int) -> int:
    """``epoch`` 必须是从 1 起的正整数，与 ``domain/continuation.py`` 同一条理由。

    0 与「没有持有者」不可区分，而它们必须能分开 —— 一条 ``epoch=0`` 的 ``CLAIMED``
    行会让「谁领的」这个问题有一个看起来有答案的空答案。
    """
    if not isinstance(epoch, int) or isinstance(epoch, bool) or epoch < 1:
        raise ValueError(f"epoch 必须是从 1 起的正整数，收到 {epoch!r}。")
    return epoch


def _rewrite(current: Continuation, **changes) -> Continuation:
    """在 ``current`` 上改几格，**重新构造**而不是 ``model_copy(update=...)``。

    ``model_copy`` 不跑校验器（pydantic 的 ``update`` 是一次绕过）。这一层的每一格
    都有一条校验（``blocked_on`` 与 state 必须对得上、``CLAIMED`` 必须有 holder 与
    epoch），绕过它们等于把不变式的守卫留在「所有调用点都记得自己检查」上 ——
    而那正是它会被漏掉的地方。
    """
    payload = current.model_dump()
    payload.update(changes)
    return Continuation(**payload)


def _write_continuation(conn, row: Continuation) -> None:
    """整行覆盖写。**调用方必须先读** —— 覆盖写没有 CAS，CAS 在读那一步。

    与 ``job_attempt`` 那种「只增不改」的表不同：这张表的后半段（state / holder /
    epoch / checkpoint / attempts / due_at）**本来就是要被覆盖的执行记录**，而它的
    身份（``continuation_id``）与它说的是哪一次事件（``business_key``）从不改写。
    见 ``domain/continuation.py::Continuation``。
    """
    values = flatten(row)
    assignments = ", ".join(
        f"{name} = ?" for name in CONTINUATION_COLUMNS if name != "continuation_id"
    )
    conn.execute(
        f"UPDATE continuation SET {assignments} WHERE continuation_id = ?",
        (*values[1:], values[0]),
    )


def _row_to_job(row) -> ResearchJob:
    return ResearchJob(
        job_id=row["job_id"],
        scene_id=row["scene_id"],
        input_kind=JobInputKind(row["input_kind"]),
        input_ref=row["input_ref"],
        idempotency_key=row["idempotency_key"],
        request_sha256=row["request_sha256"],
        state=JobState(row["state"]),
        reason=row["reason"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def _row_to_work_item(row) -> WorkItem:
    raw = row["stop_reason"]
    return WorkItem(
        work_item_id=row["work_item_id"],
        job_id=row["job_id"],
        ordinal=int(row["ordinal"]),
        subject_ref=row["subject_ref"],
        stop_reason=None if raw is None else StopReason(raw),
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def _row_to_attempt(row) -> JobAttempt:
    return JobAttempt(
        attempt_id=row["attempt_id"],
        work_item_id=row["work_item_id"],
        ordinal=int(row["ordinal"]),
        holder=row["holder"],
        state=AttemptState(row["state"]),
        leased_at=row["leased_at"],
        settled_at=row["settled_at"],
        note=row["note"],
    )


class SqliteJobStore(JobStore):
    """基于 SQLite 的作业记录。

    构造参数是 ``db_path`` 而不是 ``Settings``：理由与 ``SqliteBudgetLedger``
    相同 —— 记录是一个纯状态机，不该需要知道自己是哪个 profile 跑出来的。
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
        *,
        scene_id: str,
        input_kind: JobInputKind,
        input_ref: str,
        idempotency_key: str,
        request_sha256: str,
    ) -> ResearchJob:
        _require_text(scene_id, "scene_id")
        _require_text(input_ref, "input_ref")
        _require_text(idempotency_key, "幂等键")
        if len(request_sha256) != 64 or any(
            ch not in "0123456789abcdef" for ch in request_sha256
        ):
            raise ValueError(
                "request_sha256 必须是 64 位小写十六进制。\n"
                "它唯一的用途是判断「同一个键是不是同一个请求」，所以一个形状不对的"
                "值不会报错 —— 它只会让**每一个**请求都与别的请求不同，于是重发被读成"
                "新请求。"
            )

        jid = job_id(scene_id, idempotency_key)
        at = utc_now()
        now = at.isoformat()
        with session(self._path) as conn, write_transaction(conn):
            existing = self._job(conn, jid)
            if existing is not None:
                if existing.request_sha256 != request_sha256:
                    raise IdempotencyConflictError(
                        f"幂等键 {idempotency_key!r} 在场景 {scene_id!r} 下已经用于另一个请求"
                        f"（{existing.request_sha256[:16]}… ≠ {request_sha256[:16]}…，"
                        f"作业 {jid}）。\n"
                        "同一个键的承诺是『重发不会受理两次』，不是『同一个键可以改内容』。"
                    )
                return existing  # 重发：不重复受理，**也不重新欠一遍**
            conn.execute(
                "INSERT INTO research_job (job_id, scene_id, input_kind, input_ref, "
                "idempotency_key, request_sha256, state, reason, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, '', ?, ?)",
                (
                    jid,
                    scene_id,
                    input_kind.value,
                    input_ref,
                    idempotency_key,
                    request_sha256,
                    JobState.QUEUED.value,
                    now,
                    now,
                ),
            )
            # 同一个写事务里欠下下一步：受理是那个业务事件，而它欠着「把这一批件交给
            # 执行器、并跟着它们走到完」。写在事务之外的话，进程在两次写之间死掉就是
            # 「受理成功、下一步永久丢失」—— 那正是 R2 那一行要消灭的东西。
            owe(conn, intents_for_admission(jid, scene_id), at=at)
            return self._job(conn, jid)

    def get(self, job_id: str) -> ResearchJob | None:
        with session(self._path) as conn:
            return self._job(conn, job_id)

    # ------------------------------------------------------------------
    # 状态（CAS）
    # ------------------------------------------------------------------
    def set_state(
        self, job_id: str, state: JobState, *, expected: JobState, reason: str = ""
    ) -> ResearchJob:
        if state in (JobState.PAUSED, JobState.BLOCKED, JobState.CANCELLED):
            if not reason.strip():
                raise JobStateError(
                    f"转进 {state.value} 必须写明理由。\n"
                    "§6 要求『Cancel reports what actually stopped』—— 一条没有理由的"
                    "取消无法回答那句话，而它和『跑完了』在报告里长得一样。"
                )
        elif reason.strip():
            raise JobStateError(
                f"转进 {state.value} 不接受理由（收到 {reason!r}）。\n"
                "一条恢复不出自任何人的判断；允许它带理由，就等于允许把一次恢复写成"
                "一次判断，而恢复是可以被随便按下很多次的那个动作。"
            )

        at = utc_now()
        now = at.isoformat()
        with session(self._path) as conn, write_transaction(conn):
            current = self._job(conn, job_id)
            if current is None:
                raise JobStateError(f"没有这个作业：{job_id}")
            if current.state is not expected:
                raise JobStateError(
                    f"CAS 不匹配：作业 {job_id} 期望的状态是 {expected.value!r}，"
                    f"实际是 {current.state.value!r} —— 另一个写入者先改了它；整次拒绝。\n"
                    "读到的状态与据此写下的状态必须在同一个写事务里。没有这一条，两个"
                    "控制端同时 pause / cancel 时最后一个写的赢，而**两边都以为自己成功了**。"
                )
            if state not in JOB_TRANSITIONS[current.state]:
                allowed = sorted(s.value for s in JOB_TRANSITIONS[current.state]) or ["（终态）"]
                raise JobStateError(
                    f"{current.state.value} → {state.value} 不是合法转移；"
                    f"从 {current.state.value} 可以去的只有 {allowed}。"
                )
            conn.execute(
                "UPDATE research_job SET state = ?, reason = ?, updated_at = ? WHERE job_id = ?",
                (state.value, reason.strip(), now, job_id),
            )
            # 同一个写事务里让**这一作业欠下的下一步**知道自己的处境。见
            # ``ports/jobs.py::set_state`` 那一节：暂停不只是一个新的作业状态，
            # 它必须让每一条欠着的下一步都知道自己为什么不动。
            self._rearm(conn, current, state, reason=reason.strip(), at=at)
            return self._job(conn, job_id)

    # ------------------------------------------------------------------
    # 编译
    # ------------------------------------------------------------------
    def compile_work_items(
        self, job_id: str, subjects: Sequence[SubjectDossier], *, scene: SceneFiles
    ) -> tuple[WorkItem, ...]:
        if not subjects:
            raise ValueError(
                "work item 清单不能为空。\n"
                "一个没有任何 work item 的作业在报告里长得像『已经做完』—— 而它其实是"
                "一次编译失败。"
            )
        for index, subject in enumerate(subjects):
            if not isinstance(subject, SubjectDossier):
                raise ValueError(
                    f"subjects[{index}] 应该是一个 SubjectDossier，"
                    f"拿到 {type(subject).__name__}。\n"
                    "这里**不接**裸的 account_id：一个只有 slug 的任务包正是这次要修"
                    "的那个缺口（worker 拿到 kent-003，拿不到 Example School One）。"
                )
        _require_text(scene.version, "scene.version")

        now = utc_now().isoformat()
        with session(self._path) as conn, write_transaction(conn):
            job = self._job(conn, job_id)
            if job is None:
                raise JobStateError(f"没有这个作业：{job_id}")
            if not job.accepts_results:
                raise JobStateError(
                    f"作业 {job_id} 已取消，不能再编译 work item。\n"
                    "取消是终态：此后受理的任何工作都不会有人验收。"
                )
            existing = self._work_items(conn, job_id)
            if existing:
                if tuple(item.subject_ref for item in existing) != tuple(
                    subject.account_id for subject in subjects
                ):
                    raise JobStateError(
                        f"作业 {job_id} 已经有一批不同的 work item（{len(existing)} 个）。\n"
                        "同一份输入必须编译出同一批，否则『多了一倍候选』与『多了一批工作』"
                        "在报告上不可区分。要改工作集，是另一个作业。"
                    )
                refusal = repin_refusal(
                    self._snapshot(conn, existing[0].work_item_id), scene.version, job_id
                ) or resnapshot_refusal(
                    [self._subject_json(conn, item.work_item_id) for item in existing],
                    subjects,
                    job_id,
                )
                if refusal:
                    raise JobStateError(refusal)
                return existing  # 重发：同一份输入、同一份规则，编译出同一批

            # 内容的版本与字节**一次写入**。上面那句 _require_text 保证了版本非空；
            # 这里同一批字节写第二次是幂等的（主键就是它们的摘要）。
            for entry in scene.entries:
                conn.execute(
                    "INSERT OR IGNORE INTO scene_snapshot (scene_version, path, content) "
                    "VALUES (?, ?, ?)",
                    (scene.version, entry.path, entry.content),
                )
            for ordinal, subject in enumerate(subjects):
                wid = work_item_id(job_id, ordinal)
                conn.execute(
                    "INSERT INTO work_item (work_item_id, job_id, ordinal, subject_ref, "
                    "stop_reason, created_at, updated_at) VALUES (?, ?, ?, ?, NULL, ?, ?)",
                    (wid, job_id, ordinal, subject.account_id, now, now),
                )
                # 与工作单元**同一个写事务**：一次编译之后「有没有这批工作」「它针对谁」
                # 「它按哪一份规则在跑」不可能分家，进程死在这几步中间也不会留下一批
                # 没有快照的工作单元 —— 而那批工作单元在回执里说不出自己是谁。
                conn.execute(
                    "INSERT INTO work_item_snapshot "
                    "(work_item_id, scene_version, pinned_at, subject_json) "
                    "VALUES (?, ?, ?, ?)",
                    (
                        wid,
                        scene.version,
                        now,
                        json.dumps(subject.payload(), ensure_ascii=False, sort_keys=True),
                    ),
                )
            return self._work_items(conn, job_id)

    def work_items(self, job_id: str) -> tuple[WorkItem, ...]:
        with session(self._path) as conn:
            return self._work_items(conn, job_id)

    def get_work_item(self, work_item_id: str) -> WorkItem | None:
        with session(self._path) as conn:
            return self._work_item(conn, work_item_id)

    # ------------------------------------------------------------------
    # 派发
    # ------------------------------------------------------------------
    def map_execution(self, work_item_id: str, *, dispatch_task_id: str) -> ExecutionRef:
        _require_text(dispatch_task_id, "dispatch_task_id")
        now = utc_now().isoformat()
        with session(self._path) as conn, write_transaction(conn):
            item = self._work_item(conn, work_item_id)
            if item is None:
                raise JobStateError(f"没有这个 work item：{work_item_id}")
            job = self._job(conn, item.job_id)
            assert job is not None  # 外键保证：work item 的 job 一定存在
            if not job.admits_dispatch:
                raise JobStateError(
                    f"作业 {job.job_id} 处于 {job.state.value}，不放行新的派发"
                    f"（理由：{job.reason or '—'}）。\n"
                    "§6：Pause normally prevents new dispatch and lets accepted in-flight "
                    "results settle。派发就是这里，没有第二个入口。"
                )
            existing = self._execution_ref(conn, work_item_id)
            if existing is not None:
                if existing.dispatch_task_id != dispatch_task_id:
                    raise JobStateError(
                        f"work item {work_item_id} 已经映射到 派发任务 "
                        f"{existing.dispatch_task_id!r}，不能再映射到 {dispatch_task_id!r}。\n"
                        "映射是写一次的：同一件昂贵的事被派发两遍时，**两边都会正常跑完**，"
                        "而报告上只会显示花了两次。"
                    )
                return existing  # 重发：拿回同一行
            conn.execute(
                "INSERT INTO execution_ref (work_item_id, dispatch_task_id, mapped_at) "
                "VALUES (?, ?, ?)",
                (work_item_id, dispatch_task_id, now),
            )
            ref = self._execution_ref(conn, work_item_id)
            assert ref is not None
            return ref

    def execution_ref(self, work_item_id: str) -> ExecutionRef | None:
        with session(self._path) as conn:
            return self._execution_ref(conn, work_item_id)

    def work_item_snapshot(self, work_item_id: str) -> WorkItemSnapshot | None:
        with session(self._path) as conn:
            return self._snapshot(conn, work_item_id)

    def scene_files(self, scene_version: str) -> SceneFiles | None:
        _require_text(scene_version, "scene_version")
        with session(self._path) as conn:
            rows = conn.execute(
                "SELECT path, content FROM scene_snapshot WHERE scene_version = ? ORDER BY path",
                (scene_version,),
            ).fetchall()
        # 行 → 值 的转换只有一处实现（见 :func:`scene_files_from_rows`）：空行集
        # 是 ``None``，不是「与盘上现在那份相同」。
        return scene_files_from_rows(rows)

    # ------------------------------------------------------------------
    # 租约
    # ------------------------------------------------------------------
    def lease(self, work_item_id: str, *, holder: str) -> JobAttempt:
        _require_text(holder, "holder")
        now = utc_now().isoformat()
        with session(self._path) as conn, write_transaction(conn):
            item = self._work_item(conn, work_item_id)
            if item is None:
                raise JobStateError(f"没有这个 work item：{work_item_id}")
            job = self._job(conn, item.job_id)
            assert job is not None
            if not job.accepts_results:
                raise JobStateError(
                    f"作业 {job.job_id} 已取消，不再出租 work item。\n"
                    "取消是终态：此后受理的任何工作都不会有人验收。"
                )
            if not job.admits_dispatch:
                raise LeaseError(
                    f"作业 {job.job_id} 处于 {job.state.value}，不放行新的租约"
                    f"（理由：{job.reason or '—'}）。"
                )
            if self._execution_ref(conn, work_item_id) is None:
                raise LeaseError(
                    f"work item {work_item_id} 还没有派发到执行器，不能出租。\n"
                    "没有 ExecutionRef 就没有租约 —— 这是 §6 那句「the controller cannot "
                    "substitute an outer-shell worker launch for dispatch」的可执行"
                    "形式：绕过派发 直接开一个 worker 在代码里写不出来。"
                )
            if item.is_stopped:
                raise LeaseError(
                    f"work item {work_item_id} 已经停止（{item.stop_reason.value}），不再出租。\n"
                    "停止理由是一次性写下的；重跑会覆盖它。"
                )
            live = self._live_attempt(conn, work_item_id)
            if live is not None:
                if live.holder == holder:
                    return live  # 重发：同一个 holder 拿回同一条
                raise LeaseError(
                    f"work item {work_item_id} 已经租给 {live.holder!r}"
                    f"（attempt {live.ordinal}，{live.leased_at}），不能再租给 {holder!r}。\n"
                    "排他性是 §8B『no duplicate expensive work』那一句的载体：两个 worker "
                    "同时跑同一件昂贵的事时，**两边都会以为自己是对的**。"
                )
            ordinal = self._next_attempt_ordinal(conn, work_item_id)
            conn.execute(
                "INSERT INTO job_attempt (attempt_id, work_item_id, ordinal, holder, state, "
                "leased_at, settled_at, note) VALUES (?, ?, ?, ?, 'leased', ?, NULL, '')",
                (attempt_id(work_item_id, ordinal), work_item_id, ordinal, holder, now),
            )
            return self._live_attempt(conn, work_item_id)

    def release(self, attempt_id: str, *, reason: str) -> JobAttempt:
        if not reason.strip():
            raise ValueError(
                "交还租约必须写明理由。\n"
                "这是唯一的收回路径（租约不按时钟自动失效），所以理由不是礼节：它是"
                "「为什么这件工作没有结果」在报告里唯一的那句话。"
            )
        return self._close(attempt_id, AttemptState.RELEASED, note=reason.strip())

    def settle(
        self, attempt_id: str, *, stop_reason: StopReason, findings: Findings
    ) -> Settlement:
        at = utc_now()
        now = at.isoformat()
        with session(self._path) as conn, write_transaction(conn):
            current = self._attempt(conn, attempt_id)
            if current is None:
                raise JobStateError(f"没有这条租约：{attempt_id}")
            item = self._work_item(conn, current.work_item_id)
            assert item is not None
            recorded = self._settlement(conn, item.work_item_id)
            # **幂等判定在作业状态之前。** 顺序反过来，一个「结算完 → 作业被取消 →
            # 重发同一个请求」的序列会拿到「已取消」，而 §6 要求这条重发是幂等的。
            already = (
                ""
                if recorded is None or item.stop_reason is None
                else settlement_outcome(item.stop_reason, recorded.findings)
            )
            if self._already_done(
                current,
                AttemptState.SETTLED,
                already=already,
                outcome=settlement_outcome(stop_reason, findings),
            ):
                assert recorded is not None
                return Settlement(
                    attempt=current,
                    findings=recorded.findings,
                    recorded_at=recorded.recorded_at,
                    replayed=True,
                )
            job = self._job(conn, item.job_id)
            assert job is not None
            if not job.accepts_results:
                raise JobStateError(
                    f"作业 {job.job_id} 已取消，不受理这次结果。\n"
                    "§6 要求取消报告『what actually stopped』；受理一个取消之后才到的 "
                    "findings，会让报告里多出一批没人授权的工作。"
                )
            conn.execute(
                "UPDATE job_attempt SET state = 'settled', settled_at = ? WHERE attempt_id = ?",
                (now, attempt_id),
            )
            conn.execute(
                "UPDATE work_item SET stop_reason = ?, updated_at = ? WHERE work_item_id = ?",
                (stop_reason.value, now, item.work_item_id),
            )
            conn.execute(
                "INSERT INTO job_settlement (work_item_id, attempt_id, payload, recorded_at) "
                "VALUES (?, ?, ?, ?)",
                (
                    item.work_item_id,
                    attempt_id,
                    json.dumps(findings_payload(findings), ensure_ascii=False, sort_keys=True),
                    now,
                ),
            )
            # 同一个写事务里欠下下一步。它在这三步**之后**只是读起来顺（先把结算写完，
            # 再记它欠了什么）；要紧的是它在这个事务**之内**。一次被拒绝的结算
            # （上面那句 ``raise``）根本走不到这里，所以盘上不会留下为「一件没发生的
            # 事」而存在的欠账 —— 那正是 ``tests/test_sqlite_continuation.py`` 钉的
            # 那条判据。
            snapshot = self._snapshot(conn, item.work_item_id)
            owe(
                conn,
                intents_for_settlement(
                    item.work_item_id,
                    job.scene_id,
                    subject=None if snapshot is None else snapshot.subject,
                ),
                at=at,
            )
            settled = self._attempt(conn, attempt_id)
            assert settled is not None
            return Settlement(attempt=settled, findings=findings, recorded_at=now, replayed=False)

    def settlement(self, work_item_id: str) -> Settlement | None:
        with session(self._path) as conn:
            return self._settlement(conn, work_item_id)

    def attempts(self, work_item_id: str) -> tuple[JobAttempt, ...]:
        with session(self._path) as conn:
            rows = conn.execute(
                "SELECT * FROM job_attempt WHERE work_item_id = ? ORDER BY ordinal",
                (work_item_id,),
            ).fetchall()
        return tuple(_row_to_attempt(row) for row in rows)

    # ------------------------------------------------------------------
    # 续跑意图（§10）
    # ------------------------------------------------------------------
    def continuations(
        self,
        *,
        scene_ids: Sequence[str],
        states: Iterable[ContinuationState] | None = None,
    ) -> tuple[Continuation, ...]:
        if not scene_ids:
            return ()
        where = f"scene_id IN ({_marks(scene_ids)})"
        params: list[str] = list(scene_ids)
        if states is not None:
            wanted = tuple(s.value for s in states)
            if not wanted:
                return ()
            where += f" AND state IN ({_marks(wanted)})"
            params += list(wanted)
        with session(self._path) as conn:
            rows = conn.execute(
                f"SELECT * FROM continuation WHERE {where} ORDER BY due_at, continuation_id",
                params,
            ).fetchall()
        return tuple(_row_to_continuation(row) for row in rows)

    def continuation(self, continuation_id: str) -> Continuation | None:
        with session(self._path) as conn:
            return self._continuation(conn, continuation_id)

    def continuation_counts(self, *, scene_ids: Sequence[str]) -> dict[ContinuationState, int]:
        counts = {state: 0 for state in ContinuationState}
        if not scene_ids:
            return counts
        with session(self._path) as conn:
            rows = conn.execute(
                "SELECT state, COUNT(*) AS n FROM continuation "
                f"WHERE scene_id IN ({_marks(scene_ids)}) GROUP BY state",
                list(scene_ids),
            ).fetchall()
        for row in rows:
            counts[ContinuationState(row["state"])] = int(row["n"])
        return counts

    def claim_continuation(
        self, continuation_id: str, *, holder: str, epoch: int, now: datetime
    ) -> ContinuationClaim:
        _require_text(holder, "holder")
        _require_epoch(epoch)
        at = to_utc(now)
        with session(self._path) as conn, write_transaction(conn):
            current = self._continuation(conn, continuation_id)
            if current is None:
                raise ContinuationError(f"没有这条续跑意图：{continuation_id}")
            claimable = current.state in (ContinuationState.PENDING, ContinuationState.BLOCKED)
            if not (claimable and current.is_due(at)):
                # **不是错误**：别人正在跑、已经跑完、或者还不到期。三者都在
                # ``current`` 里看得见（state / holder / due_at），所以这里不编一个原因码。
                return ContinuationClaim(continuation=current, won=False)
            claimed = _rewrite(
                current,
                state=ContinuationState.CLAIMED,
                holder=holder,
                epoch=epoch,
                # 领起来就不再有「被什么挡着」——它还挡着的话，这一次执行会重新记一遍
                # （``finish_continuation(BLOCKED)``），而那一次会带一个新的 due_at。
                blocked_on=None,
                reason="",
                due_at=at,
                updated_at=at,
            )
            _write_continuation(conn, claimed)
            return ContinuationClaim(continuation=claimed, won=True)

    def finish_continuation(
        self,
        continuation_id: str,
        *,
        state: ContinuationState,
        holder: str,
        epoch: int,
        now: datetime,
        checkpoint: str = "",
        reason: str = "",
        blocked_on: BlockedOn | None = None,
    ) -> Continuation:
        _require_text(holder, "holder")
        _require_epoch(epoch)
        at = to_utc(now)
        blocked = state is ContinuationState.BLOCKED
        if blocked != (blocked_on is not None):
            raise ValueError(
                f"state={state.value} 与 blocked_on={blocked_on} 对不上。\n"
                "被挡住时要说清是**哪一类**理由（暂停 / 缺容量 / 等人），没被挡住时不得"
                "留下一个 —— 分类读的正是这一格，而它对不上时报告只会说『没做成』。"
            )
        if state is ContinuationState.FAILED and not reason.strip():
            raise ValueError(
                "一次失败必须写明理由。\n"
                "报告里那一格就是这句话；说不出原因的失败在账上只剩『没做成』，"
                "而它的处置（人去看一眼、还是继续重试）完全取决于那句话。"
            )
        with session(self._path) as conn, write_transaction(conn):
            current = self._continuation(conn, continuation_id)
            if current is None:
                raise ContinuationError(f"没有这条续跑意图：{continuation_id}")
            if current.state is not ContinuationState.CLAIMED:
                raise ContinuationError(
                    f"续跑意图 {continuation_id} 不在 CLAIMED，而在 {current.state.value}："
                    "只有领到手的那一代可以结账。\n"
                    "认领是那一次 CAS；允许从别处直接结账，等于让一次执行不必先声明"
                    "自己是执行者。"
                )
            if (current.holder, current.epoch) != (holder, epoch):
                raise ContinuationError(
                    f"续跑意图 {continuation_id} 现在在 {current.holder!r} 的第 "
                    f"{current.epoch} 代手里，而调用方报的是 {holder!r} 的第 {epoch} 代。\n"
                    "一次换代之后回来的旧控制器唯一正确的动作是**停手**：它以为自己在做的"
                    "那一件已经归别人了，继续写下去会把它那一代的结果盖在别人的上面，"
                    "而两边都以为自己成功了。"
                )
            if state is not ContinuationState.CLAIMED and not current.may_move_to(state):
                raise ContinuationError(
                    f"{current.state.value} → {state.value} 不是合法转移。"
                )
            # ``CLAIMED`` 目标是一次**原地写**：长跑的执行器报进度，不必先离开再回来。
            attempts = current.attempts + (1 if state is ContinuationState.FAILED else 0)
            # 执行器报 ``FAILED`` 说的是「**这一次**失败了」，盘上记的是「**这件事**现在
            # 到哪儿了」—— 没过次数界限就退回 ``PENDING`` 等着重试。见 ``after_failure``。
            landed = after_failure(attempts) if state is ContinuationState.FAILED else state
            due_at = at
            if landed is ContinuationState.PENDING:
                due_at = at + retry_delay(attempts)
            elif blocked:
                due_at = at + BLOCKED_BACKOFF
            finished = _rewrite(
                current,
                state=landed,
                attempts=attempts,
                checkpoint=checkpoint or current.checkpoint,
                reason=reason.strip(),
                blocked_on=blocked_on if landed is ContinuationState.BLOCKED else None,
                due_at=due_at,
                updated_at=at,
            )
            _write_continuation(conn, finished)
            return finished

    # ------------------------------------------------------------------
    # 内部：读
    # ------------------------------------------------------------------
    @staticmethod
    def _job(conn, jid: str) -> ResearchJob | None:
        row = conn.execute("SELECT * FROM research_job WHERE job_id = ?", (jid,)).fetchone()
        return None if row is None else _row_to_job(row)

    @staticmethod
    def _work_item(conn, wid: str) -> WorkItem | None:
        row = conn.execute("SELECT * FROM work_item WHERE work_item_id = ?", (wid,)).fetchone()
        return None if row is None else _row_to_work_item(row)

    @staticmethod
    def _continuation(conn, cid: str) -> Continuation | None:
        row = conn.execute(
            "SELECT * FROM continuation WHERE continuation_id = ?", (cid,)
        ).fetchone()
        return None if row is None else _row_to_continuation(row)

    @staticmethod
    def _rearm(
        conn, job: ResearchJob, state: JobState, *, reason: str, at: datetime
    ) -> None:
        """作业状态换了，它欠下的**派发意图**跟着换。

        三步都不是新的业务规则，只是把 ``ports/jobs.py::set_state`` 那一节写下的
        处置落成 SQL：

        * ``PAUSED`` → 未决的派发意图记 ``BLOCKED(PAUSE)``；
        * ``QUEUED``（就是 resume）→ 被暂停挡住的派发意图重排回 ``PENDING``；
        * ``BLOCKED``（输入产不出来，见 ``tools.py``）→ 同上，挡的是 ``CAPACITY``
          ——等的是**别人补一样东西**（账号池、场景文件），不是等一个决定，所以不是
          ``HUMAN``；
        * ``CANCELLED`` → 未决或被挡的派发意图判 ``DONE``。

        ## 为什么只动 ``dispatch_batch``

        一条意图的 ``business_key`` 就是它属于谁：派发意图的 key 是 ``job_id``，
        维护意图的 key 是 ``work_item_id``。暂停一个作业说的是**这个作业**不要动了，
        而维护意图属于「某个主体欠一次复核」，它的处置由场景的暂停状态决定
        （控制器不领被暂停场景的行）—— 两者是不同的决定，用同一个键去动它们，
        会让「暂停了这个作业」顺手停掉另一个作业的复核。

        ## 为什么只动 ``pending`` / ``blocked``，不动 ``claimed``

        ``CLAIMED`` 的行在**一个控制器手里**：它可能已经在跑一次外部调用了。把它从
        别人手里改走，那两个写入者就都会以为自己是对的 —— §8B 逐字禁止的
        duplicate expensive work。认领者自己会在结账时看到作业停了（执行器问一次
        作业状态），那时它按同样的分类写 ``BLOCKED``，走的是同一条路。
        """
        # 三句 SQL 里的每一个字面量都从枚举取，不写字符串：一个手写的
        # ``'dispatch_batch'`` 与枚举改名之后会**安静地**不匹配任何行 ——
        # 表现是「暂停了但它欠的下一步还在跑」。
        kind = ContinuationKind.DISPATCH_BATCH.value
        stamp = at.isoformat()
        if state is JobState.QUEUED:
            conn.execute(
                "UPDATE continuation SET state = ?, blocked_on = NULL, reason = '', "
                "due_at = ?, updated_at = ? "
                "WHERE kind = ? AND business_key = ? AND state = ? AND blocked_on = ?",
                (
                    ContinuationState.PENDING.value,
                    stamp,
                    stamp,
                    kind,
                    job.job_id,
                    ContinuationState.BLOCKED.value,
                    BlockedOn.PAUSE.value,
                ),
            )
            return
        if state is JobState.CANCELLED:
            conn.execute(
                "UPDATE continuation SET state = ?, reason = ?, updated_at = ?, "
                "blocked_on = NULL "
                "WHERE kind = ? AND business_key = ? AND state IN (?, ?)",
                (
                    ContinuationState.DONE.value,
                    reason,
                    stamp,
                    kind,
                    job.job_id,
                    ContinuationState.PENDING.value,
                    ContinuationState.BLOCKED.value,
                ),
            )
            return
        blocked_on = BlockedOn.PAUSE if state is JobState.PAUSED else BlockedOn.CAPACITY
        conn.execute(
            "UPDATE continuation SET state = ?, blocked_on = ?, reason = ?, "
            "due_at = ?, updated_at = ? "
            "WHERE kind = ? AND business_key = ? AND state = ?",
            (
                ContinuationState.BLOCKED.value,
                blocked_on.value,
                reason,
                stamp,
                stamp,
                kind,
                job.job_id,
                ContinuationState.PENDING.value,
            ),
        )

    @staticmethod
    def _work_items(conn, jid: str) -> tuple[WorkItem, ...]:
        rows = conn.execute(
            "SELECT * FROM work_item WHERE job_id = ? ORDER BY ordinal", (jid,)
        ).fetchall()
        return tuple(_row_to_work_item(row) for row in rows)

    @staticmethod
    def _settlement(conn, wid: str) -> Settlement | None:
        row = conn.execute(
            "SELECT * FROM job_settlement WHERE work_item_id = ?", (wid,)
        ).fetchone()
        if row is None:
            return None
        attempt = SqliteJobStore._attempt(conn, row["attempt_id"])
        if attempt is None:
            raise JobStateError(
                f"work item {wid} 有一条结算，但它指向的租约 {row['attempt_id']} 不在。\n"
                "结算与租约在同一个写事务里产生，所以这个状态只可能来自被人手改过的库 —— "
                "而在那里猜一个租约，会让报告指着一件不存在的事说它做完了。"
            )
        return Settlement(
            attempt=attempt,
            findings=findings_from_payload(json.loads(row["payload"])),
            recorded_at=row["recorded_at"],
            replayed=False,
        )

    @staticmethod
    def _execution_ref(conn, wid: str) -> ExecutionRef | None:
        row = conn.execute(
            "SELECT * FROM execution_ref WHERE work_item_id = ?", (wid,)
        ).fetchone()
        if row is None:
            return None
        return ExecutionRef(
            work_item_id=row["work_item_id"],
            dispatch_task_id=row["dispatch_task_id"],
            mapped_at=row["mapped_at"],
        )

    @staticmethod
    def _snapshot(conn, wid: str) -> WorkItemSnapshot | None:
        """没有行 = 没有快照。**不编一个版本、也不编一个身份出来**（见 ``ports/jobs.py``）。

        ``subject_json`` 为 NULL 是**第二个**缺席状态，而且它与「没有行」要分开：
        那一行有真的版本（它确实按某一版规则跑过），只是没有记录过针对谁。
        两种都返回得出来，由读的那一侧各自处置。
        """
        row = conn.execute(
            "SELECT * FROM work_item_snapshot WHERE work_item_id = ?", (wid,)
        ).fetchone()
        if row is None:
            return None
        raw = row["subject_json"]
        return WorkItemSnapshot(
            work_item_id=row["work_item_id"],
            scene_version=row["scene_version"],
            pinned_at=row["pinned_at"],
            subject=None if raw is None else dossier_from_payload(json.loads(raw)),
        )

    @staticmethod
    def _subject_json(conn, wid: str) -> str | None:
        """快照里的身份载荷**原文**，给 ``resnapshot_refusal`` 比。

        比原文而不是比解析后的对象：这一处的用途是「一模一样吗」，而重新序列化
        一遍会让比较掺进序列化的稳定性 —— 那不是这里要问的问题。
        """
        row = conn.execute(
            "SELECT subject_json FROM work_item_snapshot WHERE work_item_id = ?", (wid,)
        ).fetchone()
        return None if row is None else row["subject_json"]

    @staticmethod
    def _attempt(conn, aid: str) -> JobAttempt | None:
        row = conn.execute("SELECT * FROM job_attempt WHERE attempt_id = ?", (aid,)).fetchone()
        return None if row is None else _row_to_attempt(row)

    @staticmethod
    def _live_attempt(conn, wid: str) -> JobAttempt | None:
        row = conn.execute(
            "SELECT * FROM job_attempt WHERE work_item_id = ? AND state = 'leased'", (wid,)
        ).fetchone()
        return None if row is None else _row_to_attempt(row)

    @staticmethod
    def _next_attempt_ordinal(conn, wid: str) -> int:
        row = conn.execute(
            "SELECT COALESCE(MAX(ordinal), -1) + 1 AS next FROM job_attempt WHERE work_item_id = ?",
            (wid,),
        ).fetchone()
        return int(row["next"])

    # ------------------------------------------------------------------
    # 内部：一条已经定局的租约上重复同一个决定
    # ------------------------------------------------------------------
    @staticmethod
    def _already_done(
        current: JobAttempt, target: AttemptState, *, already: str, outcome: str
    ) -> bool:
        """``True`` = 这条已经以**同一个**结局定局过，重发；``False`` = 它还是活的。

        一条规则覆盖 ``release`` 与 ``settle``：一条已经定局的租约上，重复同一个
        决定是幂等的，改内容是拒绝的。写成两条各自的分支，迟早会有一条先漂移 ——
        而漂移出来的现象是「同一个 attempt 结了两次，理由不一样」。

        ``already`` 是**调用方读出来的已记录结局**，不在这里现查：两个动词的记录
        不在同一张表里（交还理由在 attempt 行上，产出在 ``job_settlement`` 里），
        而把「从哪读」写进这个函数，会让它顺手也把「读不到时算什么」定下来 ——
        那正是两个动词唯一不同的一处。
        """
        if current.state is AttemptState.LEASED:
            return False
        if current.state is not target:
            raise JobStateError(
                f"租约 {current.attempt_id} 已经是 {current.state.value}，不能再"
                f"{'结算' if target is AttemptState.SETTLED else '交还'}。\n"
                "只有活租约能被结算或交还 —— 已经定局的那条不是「再来一次」，而是另一个"
                "决定，而它已经有结论了。"
            )
        if already != outcome:
            raise JobStateError(
                f"租约 {current.attempt_id} 已经以 {already!r} 定局，不能改成 {outcome!r}。\n"
                "同一个 attempt 的结局是一次性写下的：允许改写它，『重发』就成了一条"
                "改结论的旁路。"
            )
        return True

    def _close(self, aid: str, target: AttemptState, *, note: str) -> JobAttempt:
        with session(self._path) as conn, write_transaction(conn):
            current = self._attempt(conn, aid)
            if current is None:
                raise JobStateError(f"没有这条租约：{aid}")
            if self._already_done(current, target, already=current.note, outcome=note):
                return current
            conn.execute(
                "UPDATE job_attempt SET state = ?, settled_at = ?, note = ? WHERE attempt_id = ?",
                (target.value, utc_now().isoformat(), note, aid),
            )
            closed = self._attempt(conn, aid)
            assert closed is not None
            return closed
