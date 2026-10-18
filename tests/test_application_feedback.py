"""编排层（``application/feedback.py``）判据：**能拒的东西在这里拒，拒的话说得出口**。

## 这一份判的是哪三件事

1. **来源解析**：``source_ref`` 只有两种写法，而那两种里只有**提得起补丁**的那些能过。
   两处具体的拒绝（``fact_correction`` / ``run_instruction``）各自点名**换哪条路** ——
   一句「不行」会让人去改代码，而这两条路真的存在。
2. **归案**：一份补丁可以挂在一件复核案上，而那件案必须真的在盘上、**还没关**、
   而且**上面还没有别的提议在等批准**。三条都不是洁癖：``approve_policy_change``
   批的是**那一件上最后一条提议**。
3. **批准读数的四档**：``NOT_ATTACHED`` / ``WAITING`` / ``APPROVED`` / ``UNREADABLE``。
   承重的是第四档 —— 「没查」不许折成「还没批」，否则一个只是没接上存储的部署读起来
   像「这些补丁都在等着人」，而它此后会被当成一条真实的待办队列去催。

## 这一片**没有**第二条批准路径，而这一份文件钉住它

批准只走 **既有的** ``application/review.py::approve_policy_change``（角色
``_AUTHORIZE``，而且要**另一个人**）。这一片只**读**那条历史（
``_approval_on``），一行都不写。所以下面那条「换一个人批才算数」判的是**读法**，
不是一条新的写路径。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from marketops.adapters.sqlite.evidence_store import SqliteEvidenceStore
from marketops.adapters.sqlite.feedback import SqliteFeedbackStore
from marketops.adapters.sqlite.review import SqliteReviewCaseStore
from marketops.adapters.sqlite.scenes import SqliteSceneStore
from marketops.adapters.sqlite.workstreams import SqliteEffectiveSceneStore
from marketops.application.feedback import (
    MAX_FEEDBACK,
    PatchApplication,
    PatchStanding,
    draft_candidate,
    draft_feedback,
    draft_patch,
    list_candidates,
    list_feedback,
    list_patches,
    propose_candidate,
    propose_patch,
    read_feedback,
    standing_of,
    submit_feedback,
)
from marketops.domain.enums import BudgetLine
from marketops.domain.errors import ConfigError, StoreError
from marketops.domain.evidence import ContactField, Evidence, SourceKind
from marketops.domain.feedback import (
    POLICY_PATCHABLE_KINDS,
    PolicyPatchState,
    VERSION_PRODUCING_KINDS,
    FeedbackKind,
    RiskClass,
    Scope,
    ScopeKind,
    candidate_source,
    feedback_source,
)
from marketops.domain.maintenance import RefreshPlan
from marketops.domain.outreach import OutreachPolicyDecl
from marketops.domain.review import (
    INTAKE,
    HumanResolution,
    ResolutionOutcome,
    ReviewCase,
    ReviewReason,
    ReviewState,
    case_id_for,
)
from marketops.domain.scene import (
    SceneBudgetLine,
    SceneRecord,
    SceneState,
    SceneVersion,
)
from marketops.domain.workstream import EffectiveScene

UTC = timezone.utc
T0 = datetime(2026, 9, 29, 6, 0, tzinfo=UTC)
WORKSPACE = "uk-acme-natural-grass"
SUBJECT = "kent-003"
RUN_REF = "run-2026-09-29-c"


# ================================================================== 夹具
class _Wired:
    """五件东西挂在**同一块盘**上。"""

    def __init__(self, tmp_path: Path) -> None:
        db = tmp_path / "state" / "marketops.db"
        self.path = db
        self.feedback = SqliteFeedbackStore(db)
        self.review = SqliteReviewCaseStore(db)
        self.evidence = SqliteEvidenceStore(db)
        #: 场景存储 —— 「应用」那一档问的就是它。``standing_of`` 缺它时报 unreadable，
        #: 所以凡是走到 APPROVED 的判据都得把它接上，否则那些判据只证明得了「批了」。
        self.scenes = SqliteSceneStore(db)


def _scene_version(scene_id: str, version: int) -> SceneVersion:
    """某一版 Scene 的**全部**绑定。形状抄 ``tests/test_scene_store_contract.py``。

    ``supersedes`` 必须是 ``version - 1``（校验器钉住了它），而「如何撤回」读的正是
    这个字段 —— 那一版**自己**说的，不是提案里那句自由文本。
    """
    return SceneVersion(
        scene_id=scene_id,
        workspace=scene_id,
        version=version,
        region=("GB-England-Devon",),
        scenario="sports-surface-maintenance",
        product="acme-natural-grass",
        admissions=("sports-club",),
        exclusions=("golf-course",),
        role_policy="role-policy-v1",
        source_plan=("mother-pool-import",),
        budget=(SceneBudgetLine(line=BudgetLine.SEARCH, ceiling=20),),
        refresh=RefreshPlan.uncalibrated_default(),
        outreach=OutreachPolicyDecl.uncalibrated_default(),
        profile_id=scene_id,
        profile_version="2026-09-01",
        created_at=T0,
        supersedes=None if version == 1 else version - 1,
    )


def _scene_record(scene_id: str, latest_version: int, state: SceneState = SceneState.DRAFT) -> SceneRecord:
    return SceneRecord(
        scene_id=scene_id,
        workspace=scene_id,
        state=state,
        latest_version=latest_version,
        created_at=T0,
        updated_at=T0,
    )


@pytest.fixture
def wired(tmp_path: Path) -> _Wired:
    return _Wired(tmp_path)


def _case(
    *,
    state: ReviewState = ReviewState.OPEN,
    version: int = 1,
    subject_ref: str = SUBJECT,
) -> ReviewCase:
    """一件形状正确的待办。``case_id`` 由内容算出来 —— 手写一个会被模型拒掉。"""
    return ReviewCase(
        case_id=case_id_for(
            workspace=WORKSPACE,
            scene_id=WORKSPACE,
            reason=ReviewReason.IDENTITY_CONFLICT,
            subject_ref=subject_ref,
        ),
        workspace=WORKSPACE,
        scene_id=WORKSPACE,
        reason=ReviewReason.IDENTITY_CONFLICT,
        subject_ref=subject_ref,
        affected_refs=(subject_ref,),
        options=INTAKE[ReviewReason.IDENTITY_CONFLICT].options,
        state=state,
        version=version,
        opened_at=T0,
        updated_at=T0,
    )


def _answer(
    case: ReviewCase,
    outcome: ResolutionOutcome,
    *,
    by: str = "operator-b",
    n: int = 1,
    at: datetime | None = None,
    proposed_version: int | None = None,
) -> HumanResolution:
    return HumanResolution(
        resolution_id=f"res-{n}",
        case_id=case.case_id,
        workspace=case.workspace,
        outcome=outcome,
        answer="这件事这么处置",
        by=by,
        roles=("controller",),
        expected_version=case.version,
        at=T0 + timedelta(hours=n) if at is None else at,
        proposed_version=proposed_version,
    )


def _feedback(wired: _Wired, *, kind: FeedbackKind = FeedbackKind.WORKSTREAM_POLICY, key: str = "k1"):
    event = draft_feedback(
        workspace=WORKSPACE,
        principal="claude-control",
        idempotency_key=key,
        kind=kind,
        literal_feedback="这一批的来源计划配得太宽了",
        run_ref=RUN_REF,
        at=T0,
    )
    return submit_feedback(store=wired.feedback, event=event)


def _patch(
    *,
    source_ref: str,
    target_version: int = 4,
    proposed_by: str = "claude-control",
    changes: tuple[str, ...] = ("source_plan",),
):
    return draft_patch(
        workspace=WORKSPACE,
        source_ref=source_ref,
        changes=changes,
        target_version=target_version,
        monitoring="下一轮 Run 的来源计划条数",
        rollback="把这一版回退到第 3 版",
        risk=RiskClass.LOW,
        rationale="三次同族观察都指向同一个上限",
        proposed_by=proposed_by,
        at=T0,
    )


# ================================================================== 一、来源
def test_a_source_that_is_not_a_source_syntax_is_refused(wired: _Wired) -> None:
    """一个写得像来源却不是来源的字符串，会被读成一个自由文本字段。"""
    with pytest.raises(ConfigError) as caught:
        propose_patch(store=wired.feedback, patch=_patch(source_ref="这一批太宽了"))
    message = str(caught.value)
    assert "不是一种来源写法" in message
    #: 判词要把**两种**写法写出来 —— 只报「不认识」会让人去翻代码。
    assert "feedback:" in message and "candidate:" in message


def test_a_feedback_that_is_not_on_disk_is_refused(wired: _Wired) -> None:
    """一件来源取不到的提案，与一件来源正常的在清单里读起来一样。"""
    with pytest.raises(ConfigError) as caught:
        propose_patch(
            store=wired.feedback,
            patch=_patch(source_ref=feedback_source("fb-0000000000000000")),
        )
    assert "不在盘上" in str(caught.value)


def test_only_the_patchable_kinds_can_carry_a_patch(wired: _Wired) -> None:
    """**穷举五类** —— 这一片最要紧的一条边界。

    能过的是 :data:`POLICY_PATCHABLE_KINDS`（``VERSION_PRODUCING_KINDS``：它会不会
    产生一版），**不是**「慢环那几类」。两个集合在这一刻**恰好不同**（
    ``workstream_policy`` 在快环上却产生一版），所以这一条判据有一个真实的、非空的
    差集 —— 下面那条断言把那个差集钉住，否则这张表哪一天真的等于慢环了，这一条会
    静默地退化成「慢环那几类」。
    """
    for kind in FeedbackKind:
        _feedback(wired, kind=kind, key=f"key-{kind.value}")
        patch = _patch(source_ref=feedback_source(_last_id(wired, kind)), target_version=4)
        if kind in POLICY_PATCHABLE_KINDS:
            admission = propose_patch(store=wired.feedback, patch=patch)
            assert admission.admitted is True, f"{kind.value} 应当提得起补丁"
        else:
            with pytest.raises(ConfigError) as caught:
                propose_patch(store=wired.feedback, patch=patch)
            assert "提不起" in str(caught.value), f"{kind.value} 的拒绝理由不清楚"
        #: 每一类的补丁必须用**不同**的目标版本，否则它们会撞同一个 ``patch_id``
        #: （id 的原像是 ``(工作区, 来源, 目标版本)``，来源本来就不同，所以不撞）。
    assert POLICY_PATCHABLE_KINDS == VERSION_PRODUCING_KINDS
    assert VERSION_PRODUCING_KINDS != {
        kind for kind in FeedbackKind if kind.value in {"fact_correction"}
    }


def _last_id(wired: _Wired, kind: FeedbackKind) -> str:
    """刚刚那一条反馈的 id。**从清单里读回来**，不在这里重算一遍哈希。"""
    page = wired.feedback.list_feedback()
    for event in page.events:
        if event.kind is kind:
            return event.feedback_id
    raise AssertionError(f"清单里没有 {kind.value} 这一类")


def test_a_fact_correction_names_the_other_road(wired: _Wired) -> None:
    """拒绝要说出**出路**。这一类真的有一条别的路（事实/证据），而它必须被点名。"""
    _feedback(wired, kind=FeedbackKind.FACT_CORRECTION, key="fc")
    with pytest.raises(ConfigError) as caught:
        propose_patch(
            store=wired.feedback,
            patch=_patch(source_ref=feedback_source(_last_id(wired, FeedbackKind.FACT_CORRECTION))),
        )
    message = str(caught.value)
    assert "fact_correction" in message
    #: 「走哪条路」那句话由 ``_the_other_road`` 一处给出，两个提法共用 —— 所以它
    #: 一定不是一句空的「不行」。
    assert len(message.splitlines()) >= 2


def test_a_candidate_source_without_a_version_is_refused(wired: _Wired) -> None:
    """版本那一段不能省 —— 「这份提案立在那一版的观察上」此后必须答得出来。"""
    candidate = _draft_candidate_for(wired, evidence_refs=())
    propose_candidate(store=wired.feedback, candidate=candidate)
    with pytest.raises(ConfigError) as caught:
        propose_patch(
            store=wired.feedback,
            patch=_patch(source_ref=f"candidate:{candidate.candidate_id}"),
        )
    assert "哪一版" in str(caught.value)


def test_a_candidate_source_with_a_version_is_accepted(wired: _Wired) -> None:
    """正对照：写法完整、且那一版在盘上时，提案过得去。"""
    candidate = _draft_candidate_for(wired, evidence_refs=())
    propose_candidate(store=wired.feedback, candidate=candidate)
    admission = propose_patch(
        store=wired.feedback,
        patch=_patch(source_ref=candidate_source(candidate.candidate_id, candidate.revision)),
    )
    assert admission.admitted is True


def test_a_candidate_that_is_not_on_disk_is_refused(wired: _Wired) -> None:
    with pytest.raises(ConfigError) as caught:
        propose_patch(
            store=wired.feedback,
            patch=_patch(source_ref=candidate_source("ec-0000000000000000", 1)),
        )
    assert "不在盘上" in str(caught.value)


# ================================================================== 二、归案
def test_a_patch_without_a_case_is_accepted_and_stands_not_attached(wired: _Wired) -> None:
    """``review_case_id`` 可以不给 —— 而那时它在读数里是 ``NOT_ATTACHED``。

    「今天它连被批准的资格都没有」是一句要说出来的话，不是一次失败：这一片**不新开
    复核案**，所以一条刚提交的征求意见的反馈，本来就没有案可归。
    """
    _feedback(wired)
    patch = _patch(source_ref=feedback_source(_last_id(wired, FeedbackKind.WORKSTREAM_POLICY)))
    admission = propose_patch(store=wired.feedback, patch=patch)
    reading = standing_of(patch=admission.patch, review=wired.review)
    assert reading.standing is PatchStanding.NOT_ATTACHED
    #: 判词要点名**缺的是什么** —— 「没有案」与「案子读不到」是两件事。
    assert "复核案" in reading.standing_reason


def test_a_case_that_is_not_on_disk_is_refused(wired: _Wired) -> None:
    _feedback(wired)
    patch = _patch(source_ref=feedback_source(_last_id(wired, FeedbackKind.WORKSTREAM_POLICY)))
    attached = patch.model_copy(
        update={
            "review_case_id": "rc-0000000000000000",
            "state": PolicyPatchState.AWAITING_APPROVAL,
        }
    )
    with pytest.raises(ConfigError) as caught:
        propose_patch(store=wired.feedback, patch=attached, review=wired.review)
    assert "不在盘上" in str(caught.value)


def test_a_closed_case_is_refused(wired: _Wired) -> None:
    """一件关掉的案不会再有决定 —— 挂上去的补丁永远等不到那一条，而它只是一件挂着的事。"""
    _feedback(wired)
    case = _case()
    wired.review.open_case(case)
    wired.review.record_resolution(
        _answer(case, ResolutionOutcome.DISMISS), expected_version=case.version
    )
    patch = _patch(source_ref=feedback_source(_last_id(wired, FeedbackKind.WORKSTREAM_POLICY)))
    attached = patch.model_copy(
        update={
            "review_case_id": case.case_id,
            "state": PolicyPatchState.AWAITING_APPROVAL,
        }
    )
    with pytest.raises(ConfigError) as caught:
        propose_patch(store=wired.feedback, patch=attached, review=wired.review)
    assert "已经" in str(caught.value) and "关" in str(caught.value)


def test_a_second_patch_on_a_case_with_a_pending_proposal_is_refused(
    wired: _Wired,
) -> None:
    """**``approve_policy_change`` 批的是那一件上最后一条提议。**

    两份补丁挂在同一件上、各自声称产生了那一版，会让「这一版是谁提的」有两个答案 ——
    而那正是 ``pending_proposal`` 那条判据存在的理由，所以这里**读它**，不另写一套
    「谁先谁后」。
    """
    _feedback(wired, key="k1")
    feedback_id = _last_id(wired, FeedbackKind.WORKSTREAM_POLICY)
    case = _case()
    wired.review.open_case(case)

    first = _patch(source_ref=feedback_source(feedback_id)).model_copy(
        update={
            "review_case_id": case.case_id,
            "state": PolicyPatchState.AWAITING_APPROVAL,
        }
    )
    wired.review.record_resolution(
        _answer(
            case,
            ResolutionOutcome.PROPOSE_POLICY_CHANGE,
            proposed_version=first.target_version,
        ),
        expected_version=case.version,
    )
    #: 第一份**写进去**（它是在那条提议之前提的，所以这里绕过案上已有提议这件事 ——
    #: 真实的顺序就是：提案 → 提议 → 另一份提案想挂上来）。
    wired.feedback.put_patch(first)

    second = _patch(
        source_ref=feedback_source(feedback_id), target_version=5
    ).model_copy(update={
            "review_case_id": case.case_id,
            "state": PolicyPatchState.AWAITING_APPROVAL,
        })
    with pytest.raises(ConfigError) as caught:
        propose_patch(store=wired.feedback, patch=second, review=wired.review)
    message = str(caught.value)
    assert "已经有一条等着被批准的提议" in message
    #: 判词要点名**是谁提的哪一版**，否则读的人不知道等的是谁。而这里的「谁」是
    #: **历史表上那条提议**的作者（``operator-b``），不是这份被拒的补丁的
    #: ``proposed_by``（``claude-control``）—— 两份补丁的作者是同一个人，所以
    #: 这一条正好把「读历史」与「读手上这份补丁」分开：后者会把等的人读成自己。
    assert "operator-b" in message
    assert f"第 {first.target_version} 版" in message
    assert "claude-control" not in message


def test_the_case_is_not_judged_when_there_is_no_review_store(wired: _Wired) -> None:
    """``review`` 缺省 ``None``：**那不影响写**。

    这一层拿不到那个存储时唯一诚实的做法是**不假装核过** —— 给 ``None`` 时第三件事
    **不判**，而交付面必须把「批准了没有」报成 ``UNREADABLE``（下一条判据）。
    """
    _feedback(wired)
    patch = _patch(source_ref=feedback_source(_last_id(wired, FeedbackKind.WORKSTREAM_POLICY)))
    attached = patch.model_copy(
        update={
            "review_case_id": "rc-0000000000000000",
            "state": PolicyPatchState.AWAITING_APPROVAL,
        }
    )
    admission = propose_patch(store=wired.feedback, patch=attached, review=None)
    assert admission.admitted is True


# ================================================================== 三、四档读数
def test_the_standing_enum_has_four_members_and_not_two() -> None:
    """**四档，不是两档。** 少一档就是把两种不同的处境读成同一句话。"""
    assert {member.value for member in PatchStanding} == {
        "not_attached",
        "waiting",
        "approved",
        "unreadable",
    }


def test_a_patch_with_no_review_store_stands_unreadable_not_waiting(wired: _Wired) -> None:
    """**这一档是承重的**：把「没查」折进「还没批」，会让一个只是没接上存储的部署
    读起来像「这些补丁都在等着人」，而它此后会被当成一条真实的待办队列去催。
    """
    _feedback(wired)
    patch = _patch(source_ref=feedback_source(_last_id(wired, FeedbackKind.WORKSTREAM_POLICY)))
    attached = patch.model_copy(
        update={
            "review_case_id": "rc-0000000000000000",
            "state": PolicyPatchState.AWAITING_APPROVAL,
        }
    )
    admission = propose_patch(store=wired.feedback, patch=attached, review=None)

    reading = standing_of(patch=admission.patch, review=None)
    assert reading.standing is PatchStanding.UNREADABLE
    assert reading.standing is not PatchStanding.WAITING
    #: 判词要点名**缺的是哪一个存储**，否则「去接线」这件事没有落点。
    assert "ReviewCaseStore" in reading.standing_reason


def test_a_patch_whose_case_is_absent_stands_unreadable(wired: _Wired) -> None:
    """一件读不到的案与一件还没批的案，在报告上长得一样 —— 所以它是「答不上来」。"""
    _feedback(wired)
    patch = _patch(source_ref=feedback_source(_last_id(wired, FeedbackKind.WORKSTREAM_POLICY)))
    attached = patch.model_copy(
        update={
            "review_case_id": "rc-0000000000000000",
            "state": PolicyPatchState.AWAITING_APPROVAL,
        }
    )
    wired.feedback.put_patch(attached)
    reading = standing_of(patch=attached, review=wired.review)
    assert reading.standing is PatchStanding.UNREADABLE
    assert "不在盘上" in reading.standing_reason


def test_a_patch_on_a_pending_case_stands_waiting(wired: _Wired) -> None:
    """挂在一件**还没关**的案上、而那条批准决定还没出现 → ``WAITING``。

    判词要点名三件：哪一件案、它现在的状态、以及**批准走哪条路** —— 最后那一句是
    「这一片不新开第二条批准路径」在读数上的落点。
    """
    _feedback(wired)
    case = _case()
    wired.review.open_case(case)
    patch = _patch(source_ref=feedback_source(_last_id(wired, FeedbackKind.WORKSTREAM_POLICY)))
    attached = patch.model_copy(
        update={
            "review_case_id": case.case_id,
            "state": PolicyPatchState.AWAITING_APPROVAL,
        }
    )
    admission = propose_patch(store=wired.feedback, patch=attached, review=wired.review)

    reading = standing_of(patch=admission.patch, review=wired.review)
    assert reading.standing is PatchStanding.WAITING
    assert case.case_id in reading.standing_reason
    assert "approve_policy_change" in reading.standing_reason
    assert "_AUTHORIZE" in reading.standing_reason
    assert "另一个人" in reading.standing_reason
    assert reading.approved_by is None


def _approve(
    wired: _Wired,
    case: ReviewCase,
    *,
    by: str,
    version: int,
    approver_sees: int | None = None,
):
    """走**既有的**那条路：先提议挂起某一版，再由另一个人批准。返回批准后的案件。

    这个夹具**不写**任何批准逻辑 —— 它调的就是 ``record_resolution``，与
    ``application/review.py::approve_policy_change`` 落到的那个函数是同一个。
    """
    proposed = wired.review.record_resolution(
        _answer(
            case,
            ResolutionOutcome.PROPOSE_POLICY_CHANGE,
            proposed_version=version,
        ),
        expected_version=case.version,
    )
    return wired.review.record_resolution(
        _answer(
            proposed,
            ResolutionOutcome.APPROVE_POLICY_CHANGE,
            by=by,
            n=2,
        ),
        expected_version=proposed.version,
    )


def test_a_patch_is_approved_when_another_person_approves_it(wired: _Wired) -> None:
    """**第四档的正面落点。** 这是本文件里唯一一条会走到 ``APPROVED`` 的判据。

    它同时是一条**回归判据**：这一格曾经是坏的 —— ``_approval_on`` 比的是批准那一行
    自己的 ``proposed_version``，而 ``domain.review`` 的校验器**不许**一条批准携带版本号
    （``APPROVE_POLICY_CHANGE`` 不在 ``POLICY_PROPOSAL_OUTCOMES`` 里）。于是那条
    ``continue`` 吃掉了每一条批准，``APPROVED`` 是一个**永远不可达**的成员：一份已经
    被批过的补丁读出来是 ``WAITING``，而它与「还没批」在报告上是同一句话。

    所以这个文件不能只判前三档 —— 前三档在坏的实现上照旧全绿。
    """
    _feedback(wired)
    case = _case()
    wired.review.open_case(case)
    patch = _patch(source_ref=feedback_source(_last_id(wired, FeedbackKind.WORKSTREAM_POLICY)))
    attached = patch.model_copy(
        update={
            "review_case_id": case.case_id,
            "state": PolicyPatchState.AWAITING_APPROVAL,
        }
    )
    admission = propose_patch(store=wired.feedback, patch=attached, review=wired.review)

    _approve(wired, case, by="authorizer-2", version=attached.target_version)

    reading = standing_of(
        patch=admission.patch, review=wired.review, scenes=wired.scenes
    )
    assert reading.standing is PatchStanding.APPROVED
    assert reading.approved_by == "authorizer-2"
    #: **批准不产生任何一版。** 「批准」与「已生效」是两件事，而从前它们是一格：
    #: 一份从没被写下来的版本号会被读成「已经生效了」，监控与撤回路径也一起出现。
    #: 那时这一条判据是绿的 —— 它断言的是提案自己写下的那两个字符串。
    assert reading.application is PatchApplication.NOT_PRODUCED, (
        "批准之后那一版并不在盘上 —— 而应用那一档必须说得出这件事"
    )
    assert reading.produced_version is None, "还没产版的提案报出了一个生效版本号"
    assert reading.monitoring is None, "还没产版的提案报出了生效中的监控路径"
    assert reading.rollback is None
    assert reading.rollback_version is None
    #: 提案自己写的那两条仍在，只是换了名字：它们说的是「打算怎么盯、怎么退」。
    assert reading.as_dict()["declared_monitoring"] == attached.monitoring
    assert reading.as_dict()["declared_rollback"] == attached.rollback


def test_an_approved_patch_is_effective_only_after_a_version_is_written_and_activated(
    wired: _Wired,
) -> None:
    """**承重判据：批准 → 应用 → 新版本生效 → 旧版本可回滚。**

    B23 的关闭条件逐字要求系统能回答每条反馈「谁说的、作用在哪、从哪一 Run 生效、
    影响哪些对象、**如何撤回**」。前四问都有答案，第五问此前只落在 ``rollback``
    那句**自由文本**上（``None`` 之外没有任何机器判据）。这一条把第五问钉在一个
    既有对象上：``SceneVersion.supersedes``。

    走的是**既有**的两个动词，一个都不新开：写版是 ``SqliteSceneStore.put_version``
    （``add_scene_version`` 的落点），生效是 ``SceneRecord.activate``
    （``control_scene(activate)`` 的落点）。
    """
    _feedback(wired)
    case = _case()
    wired.review.open_case(case)
    patch = _patch(source_ref=feedback_source(_last_id(wired, FeedbackKind.WORKSTREAM_POLICY)))
    attached = patch.model_copy(
        update={
            "review_case_id": case.case_id,
            "state": PolicyPatchState.AWAITING_APPROVAL,
        }
    )
    admission = propose_patch(store=wired.feedback, patch=attached, review=wired.review)
    _approve(wired, case, by="authorizer-2", version=attached.target_version)

    # 1. 案在、版本不在 —— 「批了，而那一版在盘上不存在」。
    produced = standing_of(
        patch=admission.patch, review=wired.review, scenes=wired.scenes
    )
    assert produced.application is PatchApplication.NOT_PRODUCED
    assert "不存在" in produced.application_reason

    # 2. 写下第 4 版（声明版）—— 记录先在（它是状态，版本是历史）。第 3 版也写下来，
    #    因为「撤得回去」要求回头那一版**真的在盘上**。
    wired.scenes.put_record(_scene_record(WORKSPACE, 4), expected_revision=None)
    wired.scenes.put_version(_scene_version(WORKSPACE, 3))
    still = standing_of(
        patch=admission.patch, review=wired.review, scenes=wired.scenes
    )
    assert still.application is PatchApplication.NOT_PRODUCED

    wired.scenes.put_version(_scene_version(WORKSPACE, 4))
    produced = standing_of(
        patch=admission.patch, review=wired.review, scenes=wired.scenes
    )
    assert produced.application is PatchApplication.PRODUCED, (
        "那一版在盘上而当前生效的不是它 —— 这一档要与「已生效」分开"
    )
    assert produced.produced_version is None, "没生效的那一版不能报成生效版本"
    assert produced.monitoring is None

    # 3. 让它生效。
    record = wired.scenes.get_record(WORKSPACE)
    assert record is not None
    wired.scenes.put_record(
        record.activate(version=4, at=T0, by="authorizer-2"),
        expected_revision=record.revision,
    )

    live = standing_of(patch=admission.patch, review=wired.review, scenes=wired.scenes)
    assert live.application is PatchApplication.EFFECTIVE
    assert live.produced_version == 4, "「已生效的那一版」现在才该有值"
    assert live.monitoring == attached.monitoring, "已生效那一版带着的监控路径"
    assert live.rollback == attached.rollback

    # 4. **如何撤回**：机器可读的那一格，读的是那一版自己的 supersedes。
    assert live.rollback_version == 3
    assert live.rollback_possible is True
    #: 撤回的判词要指名道姓说出那一版 —— 一个只报 true 的布尔答不了「撤回哪一版」。
    assert live.as_dict()["rollback_version"] == 3

    # 5. 撤回**真的走通**：把它退回去，生效版就换人了，而第 4 版还在盘上
    #    （``docs/SCENE_EVOLUTION.md`` §5 Step 5：旧版本仍可重放与回滚）。
    back = wired.scenes.get_record(WORKSPACE)
    assert back is not None
    wired.scenes.put_record(
        back.activate(version=3, at=T0 + timedelta(hours=1), by="authorizer-2"),
        expected_revision=back.revision,
    )
    rolled = standing_of(patch=admission.patch, review=wired.review, scenes=wired.scenes)
    assert rolled.application is PatchApplication.PRODUCED, "退回去之后第 4 版就不再生效"
    assert wired.scenes.get_version(WORKSPACE, 4) is not None, "撤回不是删版本"


def test_an_in_flight_attempt_keeps_its_frozen_plan_across_a_new_effective_version(
    wired: _Wired,
) -> None:
    """**关闭条件 2 的后半句**：「下一 manual Run 使用新版，在途 attempt 保持原 plan」。

    承重的不是「新版写得进」，是「新版生效之后**旧的那一份没被改写**」。机制在两处，
    两处都不是这一片新加的：

    * 冻结点是 ``(scene_id, version)``（``effective_scene`` 的主键），而不是
      「当前那一份」。一次在途 attempt 握着自己的那一版，所以有人激活第 4 版
      **不构成**对它那一次的写入；
    * ``put_scene`` 第二次写同一个主键时**报错**而不是覆盖，
      而 ``EffectiveScene.digest`` 由内容散出来 —— 于是「同一 (scene, version)
      在两次部署之间成了两份不同的东西」在盘上做不到。

    把这两条合起来读：在途 attempt 的原 plan 不是「没人去改它」，是**改不动**。
    """
    effective = SqliteEffectiveSceneStore(wired.path)
    scene_id = WORKSPACE

    def frozen(version: int, note: str) -> EffectiveScene:
        return EffectiveScene(
            scene_id=scene_id,
            version=version,
            workspace=scene_id,
            compiled_from="0" * 64,
            at=T0,
            compiler="START_COMPILER",
            not_reported=(note,),
        )

    # 在途 attempt：第 3 版被冻结下来了，它此后就照它跑。
    inflight = frozen(3, "在途 attempt 编译那一刻的那一份")
    effective.put_scene(inflight)

    # 有人批准并激活了第 4 版（走的是上一条判据里那条链的两个动词）。
    wired.scenes.put_record(_scene_record(scene_id, 4), expected_revision=None)
    wired.scenes.put_version(_scene_version(scene_id, 3))
    wired.scenes.put_version(_scene_version(scene_id, 4))
    record = wired.scenes.get_record(scene_id)
    assert record is not None
    wired.scenes.put_record(
        record.activate(version=4, at=T0, by="authorizer-2"),
        expected_revision=record.revision,
    )

    # 下一 manual Run 用的是新版 —— 它编译的是**新的一格**，不动上面那一格。
    effective.put_scene(frozen(4, "下一次 Run 用的那一份"))

    kept = effective.get_scene(scene_id, 3)
    assert kept is not None, "在途 attempt 那一版被新版本挤掉了"
    assert kept.digest == inflight.digest, "在途 attempt 的原 plan 被改写了"

    # 而且它**改不动**：同一个 (scene, version) 换一份内容进来会被拒。
    with pytest.raises(StoreError) as caught:
        effective.put_scene(frozen(3, "另一次编译，内容不一样"))
    assert "不可变" in str(caught.value)
    assert effective.get_scene(scene_id, 3).digest == inflight.digest  # type: ignore[union-attr]


def test_a_scene_storage_that_is_not_wired_makes_application_unreadable(
    tmp_path: Path,
) -> None:
    """**没接场景存储与应用那一档是两件事。**

    把没查折进「还没产版」，会让一个只是没接上存储的部署读起来像「这些补丁都还没
    落地」—— 而两者的处置相反（前者去接线，后者去写那一版）。所以 ``scenes=None``
    时报 ``unreadable``，并且理由里要说明白缺的是哪一件东西。
    """
    wired = _Wired(tmp_path)
    _feedback(wired)
    case = _case()
    wired.review.open_case(case)
    patch = _patch(source_ref=feedback_source(_last_id(wired, FeedbackKind.WORKSTREAM_POLICY)))
    attached = patch.model_copy(
        update={
            "review_case_id": case.case_id,
            "state": PolicyPatchState.AWAITING_APPROVAL,
        }
    )
    admission = propose_patch(store=wired.feedback, patch=attached, review=wired.review)
    _approve(wired, case, by="authorizer-2", version=attached.target_version)

    reading = standing_of(patch=admission.patch, review=wired.review)
    assert reading.standing is PatchStanding.APPROVED, "批准那一档不依赖场景存储"
    assert reading.application is PatchApplication.UNREADABLE
    assert "SceneStore" in reading.application_reason
    assert reading.produced_version is None


def test_a_patch_that_is_not_approved_is_not_merely_not_produced(wired: _Wired) -> None:
    """**还没批**与**批了但没产版**是两件不同的事。

    折在一起，会让一份连批准都没走过的提案读起来像「批过了、只是还没落地」。
    """
    _feedback(wired)
    case = _case()
    wired.review.open_case(case)
    patch = _patch(source_ref=feedback_source(_last_id(wired, FeedbackKind.WORKSTREAM_POLICY)))
    attached = patch.model_copy(
        update={
            "review_case_id": case.case_id,
            "state": PolicyPatchState.AWAITING_APPROVAL,
        }
    )
    admission = propose_patch(store=wired.feedback, patch=attached, review=wired.review)
    reading = standing_of(patch=admission.patch, review=wired.review, scenes=wired.scenes)
    assert reading.standing is PatchStanding.WAITING
    assert reading.application is PatchApplication.NOT_APPROVED


def test_the_rollback_target_survives_a_listing(wired: _Wired) -> None:
    """清单那条路上同样给得出「如何撤回」—— 一条读数不能只在单查时有。

    ``list_policy_patches`` 是 Member-A 读反馈的那一面，而 ``standing_of`` 是它的单份
    版本；两处形状不同会让「同一条盘上事实，报告里说一套、判据里说另一套」。
    """
    _feedback(wired)
    case = _case()
    wired.review.open_case(case)
    patch = _patch(source_ref=feedback_source(_last_id(wired, FeedbackKind.WORKSTREAM_POLICY)))
    attached = patch.model_copy(
        update={
            "review_case_id": case.case_id,
            "state": PolicyPatchState.AWAITING_APPROVAL,
        }
    )
    propose_patch(store=wired.feedback, patch=attached, review=wired.review)
    _approve(wired, case, by="authorizer-2", version=attached.target_version)
    wired.scenes.put_record(_scene_record(WORKSPACE, 4), expected_revision=None)
    wired.scenes.put_version(_scene_version(WORKSPACE, 3))
    wired.scenes.put_version(_scene_version(WORKSPACE, 4))
    record = wired.scenes.get_record(WORKSPACE)
    assert record is not None
    wired.scenes.put_record(
        record.activate(version=4, at=T0, by="authorizer-2"),
        expected_revision=record.revision,
    )

    rows = list_patches(store=wired.feedback, review=wired.review, scenes=wired.scenes)
    assert len(rows) == 1
    row = rows[0].as_dict()
    assert row["application"] == "effective"
    assert row["rollback_version"] == 3
    assert row["rollback_possible"] is True
    assert row["monitoring"] == attached.monitoring


def test_an_approval_by_the_proposer_himself_is_not_an_approval(wired: _Wired) -> None:
    """一个人先提再批，那个位置当场变成他可以自己走到的。

    ``approve_policy_change`` 在**写**的时候已经拦了这条（角色与「另一个人」），这里
    是**读**的时候再核一遍：读法必须与写法在不同的地方各拒一次，否则一条绕过写入的
    旧行会在读数里变成一条合法批准。
    """
    _feedback(wired)
    case = _case()
    wired.review.open_case(case)
    patch = _patch(source_ref=feedback_source(_last_id(wired, FeedbackKind.WORKSTREAM_POLICY)))
    attached = patch.model_copy(
        update={
            "review_case_id": case.case_id,
            "state": PolicyPatchState.AWAITING_APPROVAL,
        }
    )
    admission = propose_patch(store=wired.feedback, patch=attached, review=wired.review)

    #: 绕开写入那一层的那条判据：**直接把盘上那一行改掉**（真实世界里它是别的部署
    #: 迁过来的一行，或一条绕过 approve_policy_change 的旧行）。
    proposed = wired.review.record_resolution(
        _answer(
            case,
            ResolutionOutcome.PROPOSE_POLICY_CHANGE,
            proposed_version=attached.target_version,
        ),
        expected_version=case.version,
    )
    self_approval = _answer(
        proposed,
        ResolutionOutcome.APPROVE_POLICY_CHANGE,
        #: 提议的人自己批 —— 读法必须把这一条挡掉。
        by=attached.proposed_by,
        n=2,
    )
    _smuggle_resolution(wired, self_approval)

    reading = standing_of(patch=admission.patch, review=wired.review)
    assert reading.standing is PatchStanding.WAITING, (
        "提议的人自己批被读成了一次批准 —— 读法没有与写法各拒一次"
    )
    assert reading.approved_by is None


def _smuggle_resolution(wired: _Wired, resolution: HumanResolution) -> None:
    """把一条决定**直接追加到历史表上**，绕过 ``record_resolution`` 的那几条判据。

    这是这一组判据唯一可能的构造方式：那一层的 ``may_decide`` 已经拒了「同一个人自己
    批」，所以真实的写入走不到这里。而读数那一侧要防的正是**绕过写入的旧行** ——
    一次迁移、一个别的部署搬过来的库，都会造出它。
    """
    import sqlite3

    from marketops.adapters.sqlite.payload import dump_payload

    conn = sqlite3.connect(wired.path)
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
                dump_payload(resolution.model_dump(mode="json")),
                resolution.at.isoformat(),
            ),
        )
        conn.commit()
    finally:
        conn.close()


def test_an_approval_of_another_version_is_not_this_patch_s_approval(
    wired: _Wired,
) -> None:
    """一件案上可能有别的提议 —— 把「那件案批过东西」读成「这份补丁生效了」会让一份
    还没批的提案**提前生效**。
    """
    _feedback(wired)
    case = _case()
    wired.review.open_case(case)
    patch = _patch(source_ref=feedback_source(_last_id(wired, FeedbackKind.WORKSTREAM_POLICY)))
    attached = patch.model_copy(
        update={
            "review_case_id": case.case_id,
            "state": PolicyPatchState.AWAITING_APPROVAL,
        }
    )
    admission = propose_patch(store=wired.feedback, patch=attached, review=wired.review)

    #: 批的是**第 9 版**，而这份补丁的目标是第 4 版。
    _approve(wired, case, by="authorizer-2", version=9)

    reading = standing_of(patch=admission.patch, review=wired.review)
    assert reading.standing is PatchStanding.WAITING
    assert reading.produced_version is None


def test_a_patch_that_is_not_approved_does_not_report_an_effective_version(
    wired: _Wired,
) -> None:
    """**没生效时那三格是 ``None``**，不是「提案打算带的」。

    后一种读法会把一份还没批的提案读成一版**带着回滚路径**的规则 —— 而回滚路径与版本
    一起报，是因为一个不能回滚的策略版本和一个能回滚的在部署那一刻是两件事。提案自己
    写的那两条在 ``declared_*`` 上，名字不同、含义不同。
    """
    _feedback(wired)
    patch = _patch(source_ref=feedback_source(_last_id(wired, FeedbackKind.WORKSTREAM_POLICY)))
    wired.feedback.put_patch(patch)
    row = list_patches(store=wired.feedback, review=None)[0].as_dict()
    assert row["produced_version"] is None
    assert row["monitoring"] is None and row["rollback"] is None
    assert row["declared_monitoring"] == patch.monitoring
    assert row["declared_rollback"] == patch.rollback


# ================================================================== 四、候选的证据读数
def _evidence_ref(wired: _Wired, *, evidence_id: str, account_id: str = "a1") -> str:
    wired.evidence.put_evidence(
        Evidence(
            workspace=WORKSPACE,
            evidence_id=evidence_id,
            account_id=account_id,
            source_url="https://example.co.uk/contact",
            source_kind=SourceKind.FETCHED_HTML,
            literal_value="grounds@example.co.uk",
            content_sha256="abc",
            fetched_at=T0,
            field=ContactField.EMAIL,
        )
    )
    return evidence_id


def _draft_candidate_for(wired: _Wired, *, evidence_refs: tuple[str, ...]):
    """一条候选，**来源反馈真的在盘上**。

    ``propose_candidate`` 会核每一条来源反馈 —— 一条立在一份取不到的反馈上的候选，
    在清单里读起来和一条来源正常的候选一样，而它此后会被当成「有人说过」去读。
    所以这个构造器先提交那条反馈，而不是写一个假的 id。
    """
    event = submit_feedback(
        store=wired.feedback,
        event=draft_feedback(
            workspace=WORKSPACE,
            principal="claude-control",
            idempotency_key="candidate-source",
            kind=FeedbackKind.EXPERIENCE_CANDIDATE,
            literal_feedback="同一族的来源计划连续三次配得过宽",
            run_ref=RUN_REF,
            at=T0,
        ),
    )
    return draft_candidate(
        workspace=WORKSPACE,
        observation="同一族的来源计划连续三次配得过宽",
        generalises=Scope(kind=ScopeKind.SEGMENT, ref="segment-1"),
        risk=RiskClass.LOW,
        proposed_by="claude-control",
        source_feedback_ids=(event.event.feedback_id,),
        evidence_refs=evidence_refs,
        at=T0,
    )


def _candidate_with(wired: _Wired, refs: tuple[str, ...]):
    candidate = _draft_candidate_for(wired, evidence_refs=refs)
    propose_candidate(store=wired.feedback, candidate=candidate)
    return candidate


def test_a_candidate_with_no_evidence_reports_zero_and_zero(wired: _Wired) -> None:
    """``evidence_total == 0`` 是「这条候选没立在任何证据上」—— 一个真实的读数。"""
    _candidate_with(wired, ())
    row = list_candidates(store=wired.feedback, evidence=wired.evidence)[0]
    assert row.evidence_total == 0
    assert row.evidence_read == 0


def test_an_unreadable_reference_is_not_counted_as_zero(wired: _Wired) -> None:
    """**两个数，不是一个。**

    ``evidence_read == 0`` 而 ``evidence_total == 3`` 的意思是「三条引用一条都取不到」，
    它与「这条候选没立在任何证据上」是两件完全不同的事 —— 而一个数会把它们压成一个 ``0``。
    """
    _candidate_with(wired, ("e-missing-1", "e-missing-2", "e-missing-3"))
    row = list_candidates(store=wired.feedback, evidence=wired.evidence)[0]
    assert row.evidence_total == 3
    assert row.evidence_read == 0
    assert len(row.unreadable) == 3


def test_the_reason_names_which_reference_is_missing(wired: _Wired) -> None:
    """读不出来**不算作零**，而是逐条报出来、连原因一起 —— 否则「取不到」与「没引」
    在报告上同形。"""
    _candidate_with(wired, ("e-missing-1",))
    row = list_candidates(store=wired.feedback, evidence=wired.evidence)[0]
    unreadable = row.unreadable[0]
    assert unreadable.ref == "e-missing-1"
    assert unreadable.reason, "读不出来的那一条没有给出原因"


def test_a_readable_reference_is_counted(wired: _Wired) -> None:
    """正对照：真的在那儿的证据读得出来 —— 少了它，一个恒报「取不到」的实现也能通过
    上面那两条。"""
    ref = _evidence_ref(wired, evidence_id="e-real-1")
    _candidate_with(wired, (ref, "e-missing-1"))
    row = list_candidates(store=wired.feedback, evidence=wired.evidence)[0]
    assert row.evidence_total == 2
    assert row.evidence_read == 1
    assert [item.ref for item in row.unreadable] == ["e-missing-1"]


# ================================================================== 五、读一条反馈
def test_the_original_words_come_back_verbatim(wired: _Wired) -> None:
    """**原话逐字回，不折成摘要** —— 折过的版本是这一侧的读法，而后来的人要判的是
    当初到底说了什么。所以 ``as_dict`` 里也没有 ``summary`` 那一格。"""
    submitted = _feedback(wired)
    reading = read_feedback(store=wired.feedback, feedback_id=submitted.event.feedback_id)
    assert reading is not None
    row = reading.as_dict()
    assert row["literal_feedback"] == "这一批的来源计划配得太宽了"
    assert "summary" not in row


def test_what_was_raised_on_it_is_reported(wired: _Wired) -> None:
    """「这条反馈后来怎么样了」要**两处**索引一起读：候选挂在
    ``source_feedback_ids`` 上，补丁挂在 ``source_ref`` 上。只读一处会让它在两种
    提法下给出不同的答案。"""
    submitted = _feedback(wired)
    feedback_id = submitted.event.feedback_id
    candidate = draft_candidate(
        workspace=WORKSPACE,
        observation="同一族的来源计划连续三次配得过宽",
        generalises=Scope(kind=ScopeKind.SEGMENT, ref="segment-1"),
        risk=RiskClass.LOW,
        proposed_by="claude-control",
        source_feedback_ids=(feedback_id,),
        at=T0,
    )
    propose_candidate(store=wired.feedback, candidate=candidate)
    patch = _patch(source_ref=feedback_source(feedback_id))
    propose_patch(store=wired.feedback, patch=patch)

    reading = read_feedback(store=wired.feedback, feedback_id=feedback_id)
    assert reading is not None
    assert [row.candidate_id for row in reading.candidates] == [candidate.candidate_id]
    assert [row.patch_id for row in reading.patches] == [patch.patch_id]
    raised = reading.as_dict()["raised_on_it"]
    assert raised == {
        "candidates": [candidate.candidate_id],
        "patches": [patch.patch_id],
    }


def test_reading_something_absent_is_none(wired: _Wired) -> None:
    """**读不报错**：``None`` 只有一个意思 —— 没有这个 id 的反馈。"""
    assert read_feedback(store=wired.feedback, feedback_id="fb-0000000000000000") is None


# ================================================================== 六、两个数一起给
def test_the_default_page_caps_and_says_it_did(wired: _Wired) -> None:
    """``list_feedback`` 的缺省是 ``MAX_FEEDBACK``，而**封了多少要说出来**。

    两个数分开给：``matched`` 是过滤之后的总数，``events`` 才受 ``limit`` 影响。
    """
    for index in range(3):
        event = draft_feedback(
            workspace=WORKSPACE,
            principal="claude-control",
            idempotency_key=f"page-{index}",
            kind=FeedbackKind.WORKSTREAM_POLICY,
            literal_feedback=f"第 {index} 条",
            run_ref=RUN_REF,
            at=T0 + timedelta(minutes=index),
        )
        submit_feedback(store=wired.feedback, event=event)

    page = list_feedback(store=wired.feedback, limit=2)
    assert page.matched == 3 and page.returned == 2 and page.truncated is True
    #: 缺省那一次不许把它自己封成 0（一个「缺省 limit=None」的实现会在这里红）。
    assert MAX_FEEDBACK > 0
    assert list_feedback(store=wired.feedback).matched == 3
