"""``FeedbackStore`` 契约：**同一组判据，对着两个实现各跑一遍**。

与 ``tests/test_cohort_store_contract.py`` / ``tests/test_review_store_contract.py``
逐字同一条理由：端口本身不构成契约 —— 构成契约的是**同一组判据同时看着两个实现**。

* ``adapters/sqlite/feedback.py::SqliteFeedbackStore`` —— 主存。
* 本文件里的 ``_MemoryFeedbackStore`` —— 一个**测试内的**替身。

## 替身为什么住在这一份里，而不是 ``adapters/memory.py``

它不在 ``adapters/memory.py`` 里，因为那个文件**此刻正被另一条并行的工作流改**
（B24 在往它上面加视图与后续动作）。把替身写进那个文件会让两条工作流改同一段文本，
而那种冲突的代价落在**别人**身上。等 B24 落地之后，这个替身应当与它合并 —— 那是一件
接线的事，写在报告的 WIRING-REQUEST 里，不在这里自己动手。

住在测试里**不影响这一份的说服力**：判据判的是「换一个实现不该换掉任何一条判据」，
而替身是不是生产代码与那件事无关 —— 另外那两份契约文件里的内存实现同样只在测试
路径上被用到。

## 这一族里，两个实现真正不同的只有一件事

**锁。** 主存靠 ``BEGIN IMMEDIATE`` 把「读旧行」与「写新行」圈进同一个事务，内存实现
没有锁（它自己的 docstring 写了这件事）。所以下面那些判据一条都不判并发 —— 并发那
一半在 ``tests/test_sqlite_feedback.py`` 上（只对持久化实现成立）。

## 三件**只有主存**做得出来的事

1. 「写完重开一个 store 还能读到」；
2. 「别人的一行真的躺在盘上」（内存实现的存储单位是模型实例，摆不出一个绕过写路径的
   进程 —— 它那一半用**直接塞字典**摆，见 ``_smuggle``）；
3. 上面那两条的落点都在 ``tests/test_sqlite_feedback.py``。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from marketops.adapters.sqlite.feedback import SqliteFeedbackStore
from marketops.application.feedback import draft_candidate, draft_feedback, draft_patch
from marketops.domain.errors import IdempotencyConflictError, StoreError
from marketops.domain.feedback import (
    CandidateState,
    ExperienceCandidate,
    FeedbackEvent,
    FeedbackKind,
    PolicyPatch,
    PolicyPatchState,
    RiskClass,
    Scope,
    ScopeKind,
)
from marketops.ports.feedback import (
    CandidateAdmission,
    FeedbackAdmission,
    FeedbackPage,
    FeedbackStore,
    PatchAdmission,
)

UTC = timezone.utc
T0 = datetime(2026, 9, 29, 2, 0, tzinfo=UTC)
WORKSPACE = "uk-acme-natural-grass"
ELSEWHERE = "de-rasenmaeher-moll"

#: 一条窄到 ``run`` 的作用域的引用。这个文件里大多数条目挂在这一档上。
RUN_REF = "run-2026-09-29-a"


# ================================================================== 替身
class _MemoryFeedbackStore(FeedbackStore):
    """``FeedbackStore`` 的内存实现。**判据相同，实现不同。**

    ## 它刻意**不**做的那一件事

    它没有锁。两个连接同时动同一件的情况在它上面**构造不出来**（它没有连接这个
    概念），所以本文件一条并发判据都没有 —— 那一条只对持久化实现成立，住在
    ``tests/test_sqlite_feedback.py``。

    ## 「只增」在它上面是什么形状

    反馈：``dict[feedback_id, FeedbackEvent]``，重复写入先去重再比对。候选与补丁：
    ``dict[id, list[version]]``，后来的修订是**追加**一次，旧的那一版留在表里 ——
    「这条候选当初是怎么说的」在它上面与在主存上答的是同一句话。
    """

    def __init__(self) -> None:
        self._events: dict[str, FeedbackEvent] = {}
        self._candidates: dict[str, list[ExperienceCandidate]] = {}
        self._patches: dict[str, list[PolicyPatch]] = {}
        self._bound: str | None = None

    # ------------------------------------------------------------ 绑定那一半
    def _write_bind(self, workspace: str) -> None:
        if self._bound is None:
            self._bound = workspace
            return
        if self._bound != workspace:
            raise StoreError(
                f"这个库绑在 {self._bound!r}，而这一行属于 {workspace!r} —— "
                "一个库只能绑一个工作区（见 ``db.py::bind_workspace``）。"
            )

    def _owned(self, workspace: str, *, where: str) -> None:
        if self._bound is None or self._bound != workspace:
            raise StoreError(
                f"{where} 属于 {workspace!r}，而这个库绑在 {self._bound!r}。\n"
                "  跳过它的话，一个只装着别人那些行的库返回的是空清单 —— "
                "而它与「这个工作区确实没有反馈」在调用点长得完全一样。"
            )

    # ------------------------------------------------------------ 反馈
    def admit_feedback(self, event: FeedbackEvent) -> FeedbackAdmission:
        self._write_bind(event.workspace)
        stored = self._events.get(event.feedback_id)
        if stored is None:
            self._events[event.feedback_id] = event
            return FeedbackAdmission(event=event, admitted=True)
        if stored.content() != event.content():
            raise IdempotencyConflictError(
                f"幂等键 {event.idempotency_key!r} 已经用过，而它配的是另一句话。"
            )
        return FeedbackAdmission(event=stored, admitted=False)

    def get_feedback(self, feedback_id: str) -> FeedbackEvent | None:
        row = self._events.get(feedback_id)
        if row is None:
            return None
        self._owned(row.workspace, where=f"feedback_event[{feedback_id}]")
        return row

    def list_feedback(
        self,
        *,
        scope_kind: ScopeKind | None = None,
        scope_ref: str | None = None,
        limit: int | None = None,
    ) -> FeedbackPage:
        if (scope_kind is None) != (scope_ref is None):
            raise StoreError("作用域筛选必须同时给 kind 与 ref。")
        rows = [
            row
            for row in self._events.values()
            if scope_kind is None or row.proposed_scope.kind is scope_kind
        ]
        rows = [
            row
            for row in rows
            if scope_ref is None or row.proposed_scope.ref == scope_ref
        ]
        for row in rows:
            self._owned(row.workspace, where=f"feedback_event[{row.feedback_id}]")
        rows.sort(key=lambda row: row.created_at, reverse=True)
        rows.sort(key=lambda row: (row.created_at, row.feedback_id), reverse=True)
        matched = len(rows)
        page = rows if limit is None else rows[:limit]
        return FeedbackPage(matched=matched, events=tuple(page))

    # ------------------------------------------------------------ 候选
    def put_candidate(self, candidate: ExperienceCandidate) -> CandidateAdmission:
        self._write_bind(candidate.workspace)
        versions = self._candidates.setdefault(candidate.candidate_id, [])
        for index, stored in enumerate(versions):
            if stored.revision != candidate.revision:
                continue
            if stored.content() != candidate.content():
                raise StoreError(
                    f"experience_candidate[{candidate.candidate_id}@"
                    f"{candidate.revision}] 已经在盘上，而这一次的内容不同。"
                )
            return CandidateAdmission(candidate=stored, admitted=False)
        versions.append(candidate)
        versions.sort(key=lambda row: row.revision)
        return CandidateAdmission(candidate=candidate, admitted=True)

    def get_candidate(
        self, candidate_id: str, revision: int | None = None
    ) -> ExperienceCandidate | None:
        versions = self._candidates.get(candidate_id)
        if not versions:
            return None
        if revision is None:
            return versions[-1]
        for stored in versions:
            if stored.revision == revision:
                self._owned(
                    stored.workspace, where=f"experience_candidate[{candidate_id}]"
                )
                return stored
        return None

    def list_candidates(self) -> tuple[ExperienceCandidate, ...]:
        latest = [versions[-1] for versions in self._candidates.values()]
        for row in latest:
            self._owned(row.workspace, where=f"experience_candidate[{row.candidate_id}]")
        latest.sort(key=lambda row: (row.updated_at, row.candidate_id), reverse=True)
        return tuple(latest)

    # ------------------------------------------------------------ 补丁
    def put_patch(self, patch: PolicyPatch) -> PatchAdmission:
        self._write_bind(patch.workspace)
        versions = self._patches.setdefault(patch.patch_id, [])
        for stored in versions:
            if stored.revision != patch.revision:
                continue
            if stored.content() != patch.content():
                raise StoreError(
                    f"policy_patch[{patch.patch_id}@{patch.revision}] 已经在盘上，"
                    "而这一次的内容不同。"
                )
            return PatchAdmission(patch=stored, admitted=False)
        versions.append(patch)
        versions.sort(key=lambda row: row.revision)
        return PatchAdmission(patch=patch, admitted=True)

    def get_patch(self, patch_id: str, revision: int | None = None) -> PolicyPatch | None:
        versions = self._patches.get(patch_id)
        if not versions:
            return None
        if revision is None:
            return versions[-1]
        for stored in versions:
            if stored.revision == revision:
                self._owned(stored.workspace, where=f"policy_patch[{patch_id}]")
                return stored
        return None

    def list_patches(self) -> tuple[PolicyPatch, ...]:
        latest = [versions[-1] for versions in self._patches.values()]
        for row in latest:
            self._owned(row.workspace, where=f"policy_patch[{row.patch_id}]")
        latest.sort(key=lambda row: (row.updated_at, row.patch_id), reverse=True)
        return tuple(latest)


# ================================================================== 夹具
@pytest.fixture(params=["sqlite", "memory"])
def store(request, tmp_path: Path) -> FeedbackStore:
    """两个实现之一。参数名进测试 id —— 红了的时候一眼看得见是哪一个。"""
    if request.param == "sqlite":
        return SqliteFeedbackStore(tmp_path / "state" / "marketops.db")
    return _MemoryFeedbackStore()


# ================================================================== 造件
def make_event(
    *,
    idempotency_key: str = "key-1",
    principal: str = "claude-control",
    workspace: str = WORKSPACE,
    kind: FeedbackKind = FeedbackKind.WORKSTREAM_POLICY,
    words: str = "这一批的来源计划配得太宽了",
    run_ref: str | None = RUN_REF,
    workstream_ref: str | None = None,
    at: datetime = T0,
) -> FeedbackEvent:
    """一条形状正确的反馈。``feedback_id`` 由内容算出来 —— 手写一个会被模型拒掉。

    ``run_ref`` 给的是 ``RUN_REF`` 而不是 ``None``：一条反馈**必须**至少点名一个
    引用，否则 ``resolve_scope`` 拒绝（那是承重点 1 的负判据，在
    ``tests/test_feedback_domain.py`` 上）。这里给它一档，是为了让「幂等」与
    「跨工作区」那些判据判的是它们自己的事。
    """
    return draft_feedback(
        workspace=workspace,
        principal=principal,
        idempotency_key=idempotency_key,
        kind=kind,
        literal_feedback=words,
        run_ref=run_ref,
        workstream_ref=workstream_ref,
        at=at,
    )


def make_candidate(
    *,
    workspace: str = WORKSPACE,
    observation: str = "同一族的来源计划连续三次配得过宽",
    generalises_kind: ScopeKind = ScopeKind.SEGMENT,
    generalises_ref: str = "segment-1",
    source_feedback_ids: tuple[str, ...] = ("fb-0000000000000000",),
    at: datetime = T0,
) -> ExperienceCandidate:
    return draft_candidate(
        workspace=workspace,
        observation=observation,
        generalises=Scope(kind=generalises_kind, ref=generalises_ref),
        risk=RiskClass.LOW,
        proposed_by="claude-control",
        source_feedback_ids=source_feedback_ids,
        at=at,
    )


def make_patch(
    *,
    workspace: str = WORKSPACE,
    source_ref: str = "fb-0000000000000000",
    source_kind: str = "feedback",
    target_version: int = 4,
    changes: tuple[str, ...] = ("source_plan",),
    at: datetime = T0,
) -> PolicyPatch:
    return draft_patch(
        workspace=workspace,
        source_ref=f"{source_kind}:{source_ref}",
        changes=changes,
        target_version=target_version,
        monitoring="下一轮 Run 的来源计划条数",
        rollback="把这一版回退到第 3 版",
        risk=RiskClass.LOW,
        rationale="三次同族观察都指向同一个上限",
        proposed_by="claude-control",
        at=at,
    )


def _smuggle(store: FeedbackStore, event: FeedbackEvent) -> None:
    """把**别人的**一行放进这个存储，绕过它自己的写路径。

    绕过去是**刻意的**，也是这一组判据唯一可能的构造方式：两个实现的写路径都拒绝
    别人的行（那是同一个契约的另一半，见
    ``test_writing_an_object_of_another_workspace_is_refused``）。而真实世界里这种行
    不经过任何写入调用 —— 一次 ``cp -r state/``、一个拿错了 ``MARKETOPS_DATA_ROOT``
    的进程、一个从别的部署搬过来的库，都会造出它。

    两个实现之间的差别只有**往哪放**：主存是**直接** ``INSERT``（用不着我们的写
    事务 —— 写它的那个进程没有我们的代码），内存实现是塞进它自己的字典。
    """
    if isinstance(store, SqliteFeedbackStore):
        import json
        import sqlite3

        conn = sqlite3.connect(store._path)
        try:
            conn.execute(
                "INSERT INTO feedback_event "
                "(feedback_id, workspace, principal, idempotency_key, kind, "
                "scope_kind, scope_ref, created_at, payload) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    event.feedback_id,
                    event.workspace,
                    event.principal,
                    event.idempotency_key,
                    event.kind.value,
                    event.proposed_scope.kind.value,
                    event.proposed_scope.ref,
                    event.created_at.isoformat(),
                    json.dumps(event.model_dump(mode="json"), ensure_ascii=False),
                ),
            )
            conn.commit()
        finally:
            conn.close()
        return
    assert isinstance(store, _MemoryFeedbackStore)
    store._events[event.feedback_id] = event


# ================================================================== 一、幂等
def test_a_first_submission_is_reported_as_a_write(store: FeedbackStore) -> None:
    """第一次是**一次写入**，而这件事必须答得出来。

    只看 ``admission.event`` 读不出它与「它本来就在盘上」的区别 —— 而「今天新增了
    几条反馈」是一个要能回答的问题。
    """
    admission = store.admit_feedback(make_event())
    assert admission.admitted is True
    assert admission.replayed is False


def test_the_same_key_with_the_same_words_comes_back_as_the_same_row(
    store: FeedbackStore,
) -> None:
    """**幂等键存在的全部理由**：同一个人用同一把键说同一句话，拿回**同一行**。

    判三件：它是重放（``admitted=False``）、id 相同、以及**盘上那一行的时刻没被
    改写**。第三条是承重的：重发当然发生在更晚的时刻，如果存储拿新时刻覆盖旧行，
    那么「他第一次说这句话是什么时候」此后答不出来，而两次调用各自都正常。
    """
    first = make_event(at=T0)
    again = make_event(at=T0 + timedelta(hours=3))

    written = store.admit_feedback(first)
    replayed = store.admit_feedback(again)

    assert written.admitted is True
    assert replayed.admitted is False
    assert replayed.replayed is True
    assert replayed.event.feedback_id == written.event.feedback_id
    assert replayed.event.created_at == T0, (
        "重发把盘上那一行的时刻改写了 —— 此后「他第一次说这句话是什么时候」"
        "答不出来，而两次调用各自都正常"
    )
    assert store.get_feedback(first.feedback_id) == written.event


def test_the_same_key_with_different_words_is_a_conflict(store: FeedbackStore) -> None:
    """**同一把键配了不同的话，必须显性失败。**

    ``domain/errors.py`` 那一格逐字：「同一个幂等键配了不同的输入……后者必须显性
    失败」。静默保留先写的那一条，会让「我提交的那句话被记下了」与「盘上那条是别的
    什么」变成两件事。
    """
    store.admit_feedback(make_event(words="来源计划配得太宽了"))
    with pytest.raises(IdempotencyConflictError) as caught:
        store.admit_feedback(make_event(words="来源计划配得太窄了"))
    #: 判词里要点名**是哪一把键** —— 只报「冲突了」的话，读的人不知道该换哪一把。
    #: 至于「两句话都点名」，那一条是主存判词的形状，住在
    #: ``tests/test_sqlite_feedback.py`` 里（这一份对两个实现一起跑，判的是端口
    #: 保证的那几件事）。
    assert "key-1" in str(caught.value)


def test_a_conflict_leaves_the_first_row_untouched(store: FeedbackStore) -> None:
    """冲突**不改盘上的那一行**。它是拒绝，不是一次半成功的写入。"""
    first = make_event(words="来源计划配得太宽了")
    store.admit_feedback(first)
    with pytest.raises(IdempotencyConflictError):
        store.admit_feedback(make_event(words="来源计划配得太窄了"))
    stored = store.get_feedback(first.feedback_id)
    assert stored is not None and stored.literal_feedback == "来源计划配得太宽了"


def test_a_different_kind_under_the_same_key_is_also_a_conflict(
    store: FeedbackStore,
) -> None:
    """换了类别也是另一句话 —— 幂等键认的是**整条内容**，不是原话那一段。"""
    store.admit_feedback(make_event(kind=FeedbackKind.WORKSTREAM_POLICY))
    with pytest.raises(IdempotencyConflictError):
        store.admit_feedback(make_event(kind=FeedbackKind.FACT_CORRECTION))


def test_the_same_key_in_two_principals_is_two_feedbacks(store: FeedbackStore) -> None:
    """**「谁说的」进 id 的原像。** 同一把键被两个人用，是两条反馈。

    这不是一个边界情况：两个人各自的会话可以各起一个序号 1，而那一天把两条意见
    折成一条，会让第二个人说的那句话**消失**。
    """
    one = make_event(principal="claude-control")
    two = make_event(principal="member-a")
    assert one.feedback_id != two.feedback_id

    first = store.admit_feedback(one)
    second = store.admit_feedback(two)
    assert first.admitted is True and second.admitted is True
    assert store.list_feedback().matched == 2


def test_the_same_key_in_two_workspaces_is_two_feedbacks(tmp_path: Path) -> None:
    """**「哪个工作区」也进原像。** 两个部署各自的第一条反馈用的是同一把 ``key-1``。

    这一条**必须**用两个库来摆：一个库只装一个工作区，所以「同一把键在两个工作区
    里各有一条」这件事在盘上就是两个库各一行。
    """
    here = SqliteFeedbackStore(tmp_path / "a" / "marketops.db")
    there = SqliteFeedbackStore(tmp_path / "b" / "marketops.db")
    one = make_event()
    two = make_event(workspace=ELSEWHERE, run_ref=RUN_REF)
    assert one.feedback_id != two.feedback_id

    here.admit_feedback(one)
    there.admit_feedback(two)
    assert here.list_feedback().matched == 1
    assert there.list_feedback().matched == 1
    assert here.get_feedback(two.feedback_id) is None, (
        "另一个工作区的那一条不应该按 id 在这个库里读得到 —— 它是两条不同的反馈"
    )


# ================================================================== 二、一个库一个工作区
def test_writing_an_object_of_another_workspace_is_refused(
    store: FeedbackStore,
) -> None:
    """写这一侧：别人的对象**写不进去**，而且一个字都没落下。

    这是下面那几条读判据的另一半 —— 如果一个存储能被写进别人的行，那「读的时候
    核工作区」这条判据就永远碰不到真实的输入。
    """
    store.admit_feedback(make_event())
    with pytest.raises(StoreError):
        store.admit_feedback(make_event(workspace=ELSEWHERE))
    #: 本部署那一条照旧读得出来 —— 拒绝别人的行**不等于**这个存储坏了。
    assert store.list_feedback().matched == 1


def test_a_row_of_another_workspace_is_rejected_not_skipped(
    store: FeedbackStore,
) -> None:
    """同一个库里混着别人的行：**整次读报错**，不是把别人的那行跳过去。

    跳过之后，一个只装着别人那些行的工作区返回的是空清单 —— 而它与「这个工作区确实
    没有反馈」在调用点长得完全一样。越权读因此变成一次静默成功。
    """
    mine = make_event()
    store.admit_feedback(mine)
    _smuggle(store, make_event(workspace=ELSEWHERE, idempotency_key="sneaked-in"))

    with pytest.raises(StoreError):
        store.list_feedback()


def test_another_workspaces_row_is_not_reachable_by_id(store: FeedbackStore) -> None:
    """按 id 取也一样：别人的行是**报错**，不是 ``None``。

    折成 ``None`` 会让「没有这一条」与「有这一条但不给你看」同形，而前者会让人去
    重新提交一条已经存在的反馈。
    """
    mine = make_event()
    store.admit_feedback(mine)
    theirs = make_event(workspace=ELSEWHERE, idempotency_key="sneaked-in")
    _smuggle(store, theirs)

    with pytest.raises(StoreError):
        store.get_feedback(theirs.feedback_id)
    #: 正对照：本部署那一条照常读得出来（两次读在同一个连接上，拒绝是**按行**判的）。
    assert store.get_feedback(mine.feedback_id) == mine


def test_a_candidate_of_another_workspace_is_refused_too(store: FeedbackStore) -> None:
    """候选与补丁那两张表守的是同一条 —— 换一张表不该换掉一条判据。"""
    store.admit_feedback(make_event())
    with pytest.raises(StoreError):
        store.put_candidate(make_candidate(workspace=ELSEWHERE))
    with pytest.raises(StoreError):
        store.put_patch(make_patch(workspace=ELSEWHERE))


# ================================================================== 三、候选与补丁的只增
def test_the_same_candidate_version_written_twice_is_the_same_row(
    store: FeedbackStore,
) -> None:
    """同一版写两次：第二次是重放，不是一次覆盖。"""
    candidate = make_candidate()
    first = store.put_candidate(candidate)
    second = store.put_candidate(candidate)
    assert first.admitted is True
    assert second.admitted is False
    assert second.candidate == first.candidate
    assert store.list_candidates() == (candidate,)


def test_the_same_candidate_version_with_another_scope_is_a_rewrite(
    store: FeedbackStore,
) -> None:
    """**同一版配不同的内容**是一次真实的重复写入 —— 而这一格报的正是它。

    ``candidate_id`` 的原像里**没有** ``generalises``（``candidate_id_for`` 逐字）。
    那是刻意的：改了「想推广到哪」而 id 不变，才会撞主键、才会在这里响。把它放进
    原像的话，改了范围的那一次会**静静地变成另一条候选**，而原来那条还挂在盘上。
    """
    first = make_candidate(generalises_ref="segment-1")
    store.put_candidate(first)
    with pytest.raises(StoreError) as caught:
        store.put_candidate(make_candidate(generalises_ref="segment-2"))
    #: 判词要点名**是哪一个 id 的哪一版** —— 一条不点名的冲突读的人无处可查。
    assert first.candidate_id in str(caught.value)
    assert str(first.revision) in str(caught.value)


def test_a_newer_revision_does_not_overwrite_the_older_one(
    store: FeedbackStore,
) -> None:
    """候选的修订是**新的一行**，旧那一行留在盘上被读得到。

    「这条候选当初是怎么说的」只有那一行答得出来 —— 就地覆盖之后，改过主意这件事
    在盘上没有任何痕迹。
    """
    first = make_candidate()
    store.put_candidate(first)
    later = first.model_copy(
        update={
            "revision": 2,
            "state": CandidateState.PROPOSED,
            "generalises": Scope(kind=ScopeKind.SCENARIO, ref="scenario-1"),
            "updated_at": T0 + timedelta(days=1),
        }
    )
    admission = store.put_candidate(later)
    assert admission.admitted is True

    assert store.get_candidate(first.candidate_id, 1) == first, "旧那一版被覆盖了"
    assert store.get_candidate(first.candidate_id) == later, "缺省取的不是最新版"
    #: 而清单里**只有当前版** —— 把两版一起列出来会让「有几条候选」数两遍。
    assert store.list_candidates() == (later,)


def test_the_same_patch_version_written_twice_is_the_same_row(
    store: FeedbackStore,
) -> None:
    patch = make_patch()
    first = store.put_patch(patch)
    second = store.put_patch(patch)
    assert first.admitted is True
    assert second.admitted is False
    assert store.list_patches() == (patch,)


def test_the_same_patch_version_with_other_changes_is_a_rewrite(
    store: FeedbackStore,
) -> None:
    """``changes`` 与 ``generalises`` 逐字同一条：改了改哪几格，必须在这里响。"""
    first = make_patch(changes=("source_plan",))
    store.put_patch(first)
    with pytest.raises(StoreError) as caught:
        store.put_patch(make_patch(changes=("source_plan", "outreach")))
    assert first.patch_id in str(caught.value)


def test_a_patch_revision_does_not_overwrite_the_older_one(store: FeedbackStore) -> None:
    first = make_patch()
    store.put_patch(first)
    later = first.model_copy(
        update={
            "revision": 2,
            "state": PolicyPatchState.AWAITING_APPROVAL,
            "review_case_id": "rc-0000000000000000",
            "updated_at": T0 + timedelta(days=1),
        }
    )
    store.put_patch(later)
    assert store.get_patch(first.patch_id, 1) == first
    assert store.get_patch(first.patch_id) == later
    assert store.list_patches() == (later,)


def test_the_two_kinds_of_row_do_not_leak_into_each_other(
    store: FeedbackStore,
) -> None:
    """候选与补丁住在两张表里：往一边写不会让另一边多出一行。"""
    store.put_candidate(make_candidate())
    store.put_patch(make_patch())
    assert len(store.list_candidates()) == 1
    assert len(store.list_patches()) == 1


# ================================================================== 四、两个数一起给
def test_the_page_reports_matched_and_returned_together(store: FeedbackStore) -> None:
    """**匹配到的条数与返回的条数一起报。**

    冻结表与目录行逐字：「两个数分开给，是因为「只匹配到三条」与「匹配到三百条、
    这里显示三条」是两种完全不同的结论」。
    """
    for index in range(3):
        store.admit_feedback(
            make_event(
                idempotency_key=f"key-{index}",
                at=T0 + timedelta(minutes=index),
            )
        )
    page = store.list_feedback(limit=2)
    assert page.matched == 3, "matched 被 limit 影响了 —— 那是两个数之一被折掉了"
    assert page.returned == 2
    assert page.truncated is True
    #: 新的在前。
    assert page.events[0].idempotency_key == "key-2"


def test_a_page_that_is_not_truncated_says_so(store: FeedbackStore) -> None:
    """反转对照：没截断的时候 ``truncated`` 是 ``False``，不是「不知道」。

    少了它，一个恒报 ``True`` 的实现也能通过上面那一条 —— 而恒报截断会让人去翻
    一页并不存在的下一页。
    """
    store.admit_feedback(make_event())
    page = store.list_feedback(limit=10)
    assert page.matched == 1 and page.returned == 1
    assert page.truncated is False


def test_no_limit_returns_everything_matched(store: FeedbackStore) -> None:
    """``limit`` 缺省不封顶 —— 封多少是**调用点**的事，不是这一层的。"""
    for index in range(5):
        store.admit_feedback(
            make_event(idempotency_key=f"key-{index}", at=T0 + timedelta(minutes=index))
        )
    page = store.list_feedback()
    assert page.matched == 5 and page.returned == 5 and page.truncated is False


# ================================================================== 五、作用域筛选
def test_the_scope_filter_actually_filters(store: FeedbackStore) -> None:
    store.admit_feedback(make_event(idempotency_key="a", run_ref=RUN_REF))
    store.admit_feedback(
        make_event(idempotency_key="b", workstream_ref="ws-flow-1", run_ref=None)
    )
    page = store.list_feedback(scope_kind=ScopeKind.RUN, scope_ref=RUN_REF)
    assert page.matched == 1
    assert page.events[0].idempotency_key == "a"


def test_half_a_scope_filter_is_refused(store: FeedbackStore) -> None:
    """只给一半是一个**说不清的请求** —— 同一个引用在两层上都可能有意义。

    把它当成「按引用筛」放过去，会安静地多返回几行，而报告上看起来只是「反馈更多了」。
    """
    store.admit_feedback(make_event())
    with pytest.raises(StoreError):
        store.list_feedback(scope_ref=RUN_REF)
    with pytest.raises(StoreError):
        store.list_feedback(scope_kind=ScopeKind.RUN)


def test_an_empty_scope_filter_is_everything_not_nothing(
    store: FeedbackStore,
) -> None:
    """两个都不给是「这个工作区的全部反馈」，不是「一条都不匹配」。"""
    store.admit_feedback(make_event(idempotency_key="a"))
    store.admit_feedback(
        make_event(idempotency_key="b", workstream_ref="ws-flow-1", run_ref=None)
    )
    assert store.list_feedback().matched == 2


def test_a_filter_that_matches_nothing_is_zero_not_all(store: FeedbackStore) -> None:
    """反转对照：筛不到东西与不筛是两件事。"""
    store.admit_feedback(make_event())
    page = store.list_feedback(scope_kind=ScopeKind.GEO, scope_ref="uk")
    assert page.matched == 0 and page.events == ()


# ================================================================== 六、读不到就是 None
def test_reading_something_absent_is_none_not_an_error(store: FeedbackStore) -> None:
    """**读**不报错：``None`` 只有一个意思 —— 没有这个 id 的行。

    与写路径的「拒绝」是两件事：那一条说的是「这一行不能这么读或这么写」，
    这一条说的是「它不在」。
    """
    assert store.get_feedback("fb-0000000000000000") is None
    assert store.get_candidate("ec-0000000000000000") is None
    assert store.get_patch("pp-0000000000000000") is None
    assert store.get_candidate("ec-0000000000000000", 7) is None


def test_an_empty_store_lists_empty_rather_than_failing(store: FeedbackStore) -> None:
    """空库返回空，不是报错 —— 一个刚建出来的部署要能回答「还没有反馈」。"""
    assert store.list_feedback() == FeedbackPage(matched=0, events=())
    assert store.list_candidates() == ()
    assert store.list_patches() == ()


# ================================================================== 七、端口里没有「改」
def test_the_port_has_no_update_or_delete_verb(store: FeedbackStore) -> None:
    """三件对象都是**只增**的，所以端口里没有改的入口。

    一个不存在的入口比一个存在但写着注意事项的入口可靠（与 ``ports/budget.py`` 没有
    ``release`` 是同一条）。这一条判**端口本身** —— 一个实现上多出来的 ``update_*``
    不会被这条判据看见，而它也不该存在：那一半在 ``test_ports_are_contracts_only``
    的射程里。
    """
    from marketops.ports.feedback import FeedbackStore as Port

    verbs = [name for name in dir(Port) if not name.startswith("_")]
    for verb in verbs:
        assert not verb.startswith(("update_", "delete_", "remove_", "amend_")), (
            f"端口上出现了写入以外的动词 {verb!r} —— 三件对象都是只增的"
        )
    #: 前置：清单本身不是空的（空清单会让上面那个循环一次都不跑，判据恒真）。
    assert {"admit_feedback", "put_candidate", "put_patch"} <= set(verbs)
    assert not hasattr(store, "update_feedback")
    assert not hasattr(store, "delete_feedback")
