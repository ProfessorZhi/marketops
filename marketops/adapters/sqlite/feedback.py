"""SQLite 反馈环：**主存**（``ports/feedback.py`` 的落点）。

## 三张表，三种**只增**的写法，而它们不共用一条去重规则

* ``feedback_event``：一行是**一句话**。它的 id 是 ``(工作区, principal, 幂等键)``
  的哈希，所以 ``PRIMARY KEY`` 与 ``UNIQUE (workspace, principal, idempotency_key)``
  **说的是同一件事** —— 重发撞的是同一行。
* ``experience_candidate``：主键 ``(candidate_id, revision)``。**后来的修订是新的一
  行**，旧那一行留着 —— 「这条候选当初是怎么说的」只有它答得出来。
* ``policy_patch``：主键 ``(patch_id, revision)``，形状同上。补丁**批准之后不可变**，
  所以它是「每一版一行」，与 ``outreach_family`` 那三张配置表同一条。

## 幂等写成 ``ON CONFLICT DO NOTHING`` + 读回 + 内容比对

与 ``evidence_store.py`` / ``review.py`` 逐字同一个形状，**不是**「先 SELECT 再决定
INSERT」：后者会让一个**别人抢先**的正常结局被报成 ``IntegrityError``（两个人同时
用同一把幂等键提交同一句话是完全正常的，而其中一个人什么都没做错）。

读回来之后比的是 ``content()`` —— **不含时刻**的那一份。比整行会把每一次正常的重发
都报成冲突（重发当然发生在更晚的时刻），而那只让幂等键失效：它会从「同一条拿回同一
行」退化成「每一条都报错」。比 ``content()`` 之后，剩下的差别只有一处 —— 同一把键
配了不同的话 —— 而那一处**必须显性失败**（``domain/errors.py`` 的
``IdempotencyConflictError`` 逐字：「同一个幂等键配了不同的输入……后者必须显性失败」）。

## 这一层不做判据

作用域怎么定、哪五格受保护、哪些 ``kind`` 提得起补丁 —— 全在 ``domain/feedback.py``
与 ``application/feedback.py`` 上，且都在**构造对象之前**。走到这一层时，对象要么
已经成立、要么根本构造不出来。这一层只做两件**只有它才能做**的事：

* 读旧行与写新行落在**同一个** ``BEGIN IMMEDIATE`` 事务里（``db.py`` 的写事务在
  事务开始就拿写锁），于是「先读再写」之间没有别人能插进来；
* ``bind_workspace`` / ``owned`` 这一对：**写**的时候绑工作区（由第一行写入建立），
  **读**的时候核每一行属不属于这个库。读路径不核工作区的实现会让跨工作区的行
  静默地进到报告里，而它在报告上与本地事实长得一样。
"""

from __future__ import annotations

from pathlib import Path

from ...domain.errors import IdempotencyConflictError, StoreError
from ...domain.feedback import (
    ExperienceCandidate,
    FeedbackEvent,
    PolicyPatch,
    ScopeKind,
)
from ...ports.feedback import (
    CandidateAdmission,
    FeedbackAdmission,
    FeedbackPage,
    FeedbackStore,
    PatchAdmission,
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

#: 排序里那一格「不是最新版的排后面」—— 由子查询算出来，不写第二份「当前版」判据。
#: 与 ``review.py`` 的 ``_PENDING_FIRST`` 同一条：多写一遍，会让「哪一版是当前版」
#: 在 SQL 里和 Python 里各有一份，而两者漂移的表现是清单第一行换了一条。
_LATEST_ONLY = (
    "SELECT candidate_id, MAX(revision) AS revision FROM experience_candidate "
    "GROUP BY candidate_id"
)


class SqliteFeedbackStore(FeedbackStore):
    """基于 SQLite 的反馈环（§B23）。

    构造参数只有 ``db_path``：与另外几张表同一个理由 —— 存储要能在没有配置、没有
    ``os.environ`` 的情况下对着一个临时文件被测出来。

    建表在 ``__init__`` 里做（``ensure_schema`` 幂等）。**加这三张表不需要动 schema
    版本号，也不需要一次迁移** —— 它们进的是 ``_TARGET_DDL`` 那份每次都整份重放的
    DDL（``db.py`` 里那条纪律逐字写了三遍）。
    """

    def __init__(self, db_path: Path) -> None:
        self._path = db_path
        with session(self._path) as conn:
            ensure_schema(conn)

    # ------------------------------------------------------------ 反馈
    def admit_feedback(self, event: FeedbackEvent) -> FeedbackAdmission:
        with session(self._path) as conn, write_transaction(conn):
            bind_workspace(conn, event.workspace, error=StoreError)
            bound = workspace_binding(conn)
            inserted = conn.execute(
                "INSERT INTO feedback_event "
                "(feedback_id, workspace, principal, idempotency_key, kind, "
                "scope_kind, scope_ref, created_at, payload) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT DO NOTHING",
                (
                    event.feedback_id,
                    event.workspace,
                    event.principal,
                    event.idempotency_key,
                    event.kind.value,
                    event.proposed_scope.kind.value,
                    event.proposed_scope.ref,
                    event.created_at.isoformat(),
                    _dump(event.model_dump(mode="json")),
                ),
            ).rowcount
            stored = self._read_event(conn, event.feedback_id, bound)
        if stored is None:  # pragma: no cover - 刚 INSERT 过或它本来就在，必然读得到
            raise StoreError(
                f"feedback_event[{event.feedback_id}] 刚写过却读不回来 —— "
                "读与写不在同一个事务里，或者事务被回滚了。"
            )
        if not inserted and stored.content() != event.content():
            raise IdempotencyConflictError(
                f"幂等键 {event.idempotency_key!r} 已经用过，而它配的是**另一句话**。\n"
                f"  盘上那一行：{stored.literal_feedback!r}（类别 {stored.kind.value}，"
                f"作用域 {stored.proposed_scope.label}）\n"
                f"  这一次提交：{event.literal_feedback!r}（类别 {event.kind.value}，"
                f"作用域 {event.proposed_scope.label}）\n"
                "这两条**不是同一件事**，所以它们必须是两行 —— 而它们是两行的话，"
                "其中一行会带着调用方以为已经换了的那把键。\n"
                "  换一把幂等键再提交（幂等键由调用方给：服务端生成的话，"
                "「同一条反馈被提交了两次」与「他真的说了两遍」在盘上无法区分）。"
            )
        return FeedbackAdmission(event=stored, admitted=bool(inserted))

    def get_feedback(self, feedback_id: str) -> FeedbackEvent | None:
        with session(self._path) as conn:
            bound = workspace_binding(conn)
            return self._read_event(conn, feedback_id, bound)

    def list_feedback(
        self,
        *,
        scope_kind: ScopeKind | None = None,
        scope_ref: str | None = None,
        limit: int | None = None,
    ) -> FeedbackPage:
        """按作用域筛，**新的在前**，并且**两个数一起给**。

        第二、三根排序键（``created_at`` 之后是 ``feedback_id``）都不省：两次提交
        落在同一微秒是可能的（一次工具调用里提交两条），而 ``ORDER BY`` 在一个并列
        的键上给出的顺序**由实现决定** —— 那会让「最近的那三条」在两次调用之间换
        一批，而两份报告都自洽。

        ``matched`` 与 ``events`` 走的是**同一个** ``WHERE``，而且在同一个连接上跑：
        两个数分头算的实现会在两次查询之间被一次写入插进来，于是报告里出现
        「匹配到 3 条，返回 7 条」这种读不出因果的读数。
        """
        where, params = _scope_where(scope_kind=scope_kind, scope_ref=scope_ref)
        query = (
            "SELECT feedback_id, payload FROM feedback_event"
            f"{where} ORDER BY created_at DESC, feedback_id"
        )
        with session(self._path) as conn:
            bound = workspace_binding(conn)
            matched = conn.execute(
                f"SELECT COUNT(*) FROM feedback_event{where}", params
            ).fetchone()[0]
            rows = conn.execute(
                query if limit is None else f"{query} LIMIT ?",
                params if limit is None else (*params, limit),
            ).fetchall()
            events = tuple(self._event(row, row["feedback_id"], bound) for row in rows)
        return FeedbackPage(matched=int(matched), events=events)

    # ------------------------------------------------------------ 候选
    def put_candidate(self, candidate: ExperienceCandidate) -> CandidateAdmission:
        with session(self._path) as conn, write_transaction(conn):
            bind_workspace(conn, candidate.workspace, error=StoreError)
            bound = workspace_binding(conn)
            inserted = conn.execute(
                "INSERT INTO experience_candidate "
                "(candidate_id, revision, workspace, state, updated_at, payload) "
                "VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT DO NOTHING",
                (
                    candidate.candidate_id,
                    candidate.revision,
                    candidate.workspace,
                    candidate.state.value,
                    candidate.updated_at.isoformat(),
                    _dump(candidate.model_dump(mode="json")),
                ),
            ).rowcount
            stored = self._read_candidate(conn, candidate.candidate_id, bound)
        if stored is None:  # pragma: no cover - 同 admit_feedback
            raise StoreError(
                f"experience_candidate[{candidate.candidate_id}] 刚写过却读不回来。"
            )
        if not inserted:
            # 只有**同一版**配不同内容才是一次真实的重复写入。盘上更新的那一版是
            # 一次正常的修订 —— 那时 ``_read_candidate`` 读回来的是更高的一版，
            # 而本次写入什么都不改（`ON CONFLICT DO NOTHING`）。
            if _version_of(stored) == _version_of(candidate) and (
                stored.content() != candidate.content()
            ):
                raise StoreError(_rewritten(stored, candidate))
        return CandidateAdmission(candidate=stored, admitted=bool(inserted))

    def get_candidate(
        self, candidate_id: str, revision: int | None = None
    ) -> ExperienceCandidate | None:
        with session(self._path) as conn:
            bound = workspace_binding(conn)
            row = conn.execute(
                "SELECT payload FROM experience_candidate WHERE candidate_id = ?"
                + ("" if revision is None else " AND revision = ?")
                + " ORDER BY revision DESC LIMIT 1",
                (candidate_id,)
                if revision is None
                else (candidate_id, revision),
            ).fetchone()
            if row is None:
                return None
            return ExperienceCandidate.model_validate(
                owned(
                    _load(row["payload"], candidate_id),
                    where=f"experience_candidate[{candidate_id}]",
                    bound=bound,
                )
            )

    def list_candidates(self) -> tuple[ExperienceCandidate, ...]:
        """**只列当前版**，新的在前。

        「当前版」由 ``MAX(revision)`` 算出来（见 :data:`_LATEST_ONLY`），不另写一份
        「它是不是最新版」的判据 —— 与 ``review.py`` 的 ``_PENDING_FIRST`` 同一条。
        """
        with session(self._path) as conn:
            bound = workspace_binding(conn)
            rows = conn.execute(
                "SELECT c.candidate_id, c.payload FROM experience_candidate c "
                f"JOIN ({_LATEST_ONLY}) latest "
                "ON c.candidate_id = latest.candidate_id "
                "AND c.revision = latest.revision "
                "ORDER BY c.updated_at DESC, c.candidate_id"
            ).fetchall()
        return tuple(
            ExperienceCandidate.model_validate(
                owned(
                    _load(row["payload"], row["candidate_id"]),
                    where=f"experience_candidate[{row['candidate_id']}]",
                    bound=bound,
                )
            )
            for row in rows
        )

    # ------------------------------------------------------------ 补丁
    def put_patch(self, patch: PolicyPatch) -> PatchAdmission:
        with session(self._path) as conn, write_transaction(conn):
            bind_workspace(conn, patch.workspace, error=StoreError)
            bound = workspace_binding(conn)
            inserted = conn.execute(
                "INSERT INTO policy_patch "
                "(patch_id, revision, workspace, state, review_case_id, "
                "updated_at, payload) VALUES (?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT DO NOTHING",
                (
                    patch.patch_id,
                    patch.revision,
                    patch.workspace,
                    patch.state.value,
                    patch.review_case_id,
                    patch.updated_at.isoformat(),
                    _dump(patch.model_dump(mode="json")),
                ),
            ).rowcount
            stored = self._read_patch(conn, patch.patch_id, bound)
        if stored is None:  # pragma: no cover - 同 admit_feedback
            raise StoreError(f"policy_patch[{patch.patch_id}] 刚写过却读不回来。")
        if not inserted:
            if _version_of(stored) == _version_of(patch) and (
                stored.content() != patch.content()
            ):
                raise StoreError(_rewritten(stored, patch))
        return PatchAdmission(patch=stored, admitted=bool(inserted))

    def get_patch(self, patch_id: str, revision: int | None = None) -> PolicyPatch | None:
        with session(self._path) as conn:
            bound = workspace_binding(conn)
            row = conn.execute(
                "SELECT payload FROM policy_patch WHERE patch_id = ?"
                + ("" if revision is None else " AND revision = ?")
                + " ORDER BY revision DESC LIMIT 1",
                (patch_id,) if revision is None else (patch_id, revision),
            ).fetchone()
            if row is None:
                return None
            return PolicyPatch.model_validate(
                owned(
                    _load(row["payload"], patch_id),
                    where=f"policy_patch[{patch_id}]",
                    bound=bound,
                )
            )

    def list_patches(self) -> tuple[PolicyPatch, ...]:
        """**只列当前版**，新的在前。同刻按 ``patch_id``（理由同 ``list_feedback``）。"""
        with session(self._path) as conn:
            bound = workspace_binding(conn)
            rows = conn.execute(
                "SELECT p.patch_id, p.payload FROM policy_patch p "
                "JOIN (SELECT patch_id, MAX(revision) AS revision FROM policy_patch "
                "GROUP BY patch_id) latest "
                "ON p.patch_id = latest.patch_id AND p.revision = latest.revision "
                "ORDER BY p.updated_at DESC, p.patch_id"
            ).fetchall()
        return tuple(
            PolicyPatch.model_validate(
                owned(
                    _load(row["payload"], row["patch_id"]),
                    where=f"policy_patch[{row['patch_id']}]",
                    bound=bound,
                )
            )
            for row in rows
        )

    # ------------------------------------------------------------ 三个读法共用的一半
    def _read_event(
        self, conn, feedback_id: str, bound: str | None
    ) -> FeedbackEvent | None:
        row = conn.execute(
            "SELECT payload FROM feedback_event WHERE feedback_id = ?", (feedback_id,)
        ).fetchone()
        if row is None:
            return None
        return self._event(row, feedback_id, bound)

    def _event(self, row, feedback_id: str, bound: str | None) -> FeedbackEvent:
        """一行事件。``feedback_id`` **由调用方给**，不从行里取。

        两个调用点的 ``SELECT`` 不一样：``list_feedback`` 挑了 ``feedback_id`` 那一列
        （它要拿它排序），而 ``_read_event`` 按主键取、只挑 ``payload``。让这个函数
        自己去行里取的那一版，在 ``_read_event`` 那条路上会 ``IndexError`` ——
        而 ``admit_feedback`` 与 ``get_feedback`` **两条**读路径都走那一条。
        """
        return FeedbackEvent.model_validate(
            owned(
                _load(row["payload"], feedback_id),
                where=f"feedback_event[{feedback_id}]",
                bound=bound,
            )
        )

    def _read_candidate(
        self, conn, candidate_id: str, bound: str | None
    ) -> ExperienceCandidate | None:
        row = conn.execute(
            "SELECT payload FROM experience_candidate WHERE candidate_id = ? "
            "ORDER BY revision DESC LIMIT 1",
            (candidate_id,),
        ).fetchone()
        if row is None:
            return None
        return ExperienceCandidate.model_validate(
            owned(
                _load(row["payload"], candidate_id),
                where=f"experience_candidate[{candidate_id}]",
                bound=bound,
            )
        )

    def _read_patch(self, conn, patch_id: str, bound: str | None) -> PolicyPatch | None:
        row = conn.execute(
            "SELECT payload FROM policy_patch WHERE patch_id = ? "
            "ORDER BY revision DESC LIMIT 1",
            (patch_id,),
        ).fetchone()
        if row is None:
            return None
        return PolicyPatch.model_validate(
            owned(
                _load(row["payload"], patch_id),
                where=f"policy_patch[{patch_id}]",
                bound=bound,
            )
        )


def _scope_where(
    *, scope_kind: ScopeKind | None, scope_ref: str | None
) -> tuple[str, tuple[str, ...]]:
    """作用域筛选那一段 ``WHERE``。**两个参数必须同进同退。**

    只给 ``scope_ref`` 而不给 ``scope_kind`` 是一个说不清的请求：同一个引用在两层上
    都可能有意义（一个场景 id 与一个工作流 id 可以同名）。所以半个筛选条件在这里
    报错，而不是被当成「按引用筛」放过去 —— 后者会安静地多返回几行。
    """
    if (scope_kind is None) != (scope_ref is None):
        raise StoreError(
            "作用域筛选必须同时给 kind 与 ref —— 只给一半的话，同一个引用在两层上"
            "都可能有意义，而按它筛出来的行会静默地多出几条。"
        )
    if scope_kind is None:
        return "", ()
    return " WHERE scope_kind = ? AND scope_ref = ?", (
        scope_kind.value,
        str(scope_ref),
    )


def _version_of(item: object) -> tuple[str, int]:
    """一条候选/补丁的 ``(id, revision)`` —— 它的主键那两格。"""
    ident = getattr(item, "candidate_id", None) or getattr(item, "patch_id", None)
    return str(ident), int(getattr(item, "revision"))


def _rewritten(stored: object, incoming: object) -> str:
    """同一版配不同内容时的那句话。**两边都点名。**"""
    table = "experience_candidate" if hasattr(incoming, "candidate_id") else "policy_patch"
    ident, revision = _version_of(incoming)
    return (
        f"{table}[{ident}@{revision}] 已经在盘上，而这一次的内容不同。\n"
        "  这个 id **是**内容寻址的（原像见 ``domain/feedback.py`` 的三个 "
        "``*_id_for``），所以撞主键配不同内容只可能意味着：原像里没算进去的那几格"
        "被改了（候选的 ``generalises``、补丁的 ``changes`` 与来源）。\n"
        "  那正是这一格要报出来的事：改了想推广到哪 / 改了哪几格，**不是**一次"
        "静默的第二次提交。要改它们，用一次新的提案（新的来源或新的目标版本），"
        "或者把这一版撤回。"
    )


__all__ = ["SqliteFeedbackStore"]
