"""§12 的两条承重句各有一个执行器：**谁进 Inbox**，以及**流水账对不对得上**。

## 这一条为什么必须存在

``marketops/domain/review.py`` 的两个断言都不是「代码看起来对不对」能判的，它们各自
是一个**全域**的主张：

* 「不是每个 unknown 都找人」说的是 ``GradeReason`` 的**每一个**成员的去向。少写一个
  成员时，代码不会红 —— 它会静默地落进「不问人」，于是一个本该问人的原因永远问不到。
* §12 那个恒等式说的是**任意一段窗口**都成立。它成立与否取决于窗口边界的约定，而边界
  写错一个符号的表现是「相邻两段各自都对得上账、合起来少了一件」。

两条都是「反面没有被枚举就验不出来」的形状，所以下面每一个断言都同时钉正反两面。

## 为什么流水账的判据要跨窗口，而且还要**专门**有一条边界用例

只验「一段窗口对得上」是弱的：把 ``opened`` 记成 ``reopened`` 也照样对得上。所以这里有
一条**相邻两段**的用例 —— 同一批事件切两半，前一段的 ``pending_end`` 必须等于后一段的
``pending_start``，且各段自己的账都要平。

但**跨窗口本身也验不了边界**，这一点是实测出来的：第一版写完之后，把
``_state_before`` 的 ``<`` 改成 ``<=``、把开案判定的 ``< end`` 改成 ``<= end``，整套
用例**全绿** —— 因为每一个事件与开案都落在窗口**内部**，没有任何一个**落在端点上**。
边界只有在边界上真的有东西的时候才被验到，所以另有一条
``test_an_event_landing_exactly_on_a_boundary_belongs_to_the_later_window``，
它的三件案子分别把事件压在起点、压在终点、以及让第三件**在终点开**。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

import pytest
from pydantic import ValidationError

from marketops.domain.enums import GradeReason
from marketops.domain.errors import ReviewConflictError
from marketops.domain.review import (
    CLOSED_STATES,
    NOT_A_HUMAN_QUESTION,
    PENDING_STATES,
    HumanResolution,
    ResolutionOutcome,
    ReviewCase,
    ReviewReason,
    ReviewState,
    apply_resolution,
    case_id_for,
    dedup_key_for,
    inbox_flow,
    is_pending,
    may_decide,
    review_reason_for,
)

WORKSPACE = "ws"
SCENE = "uk-acme-natural-grass"
T0 = datetime(2026, 9, 20, 0, 0, tzinfo=timezone.utc)


def _at(days: float) -> datetime:
    return T0 + timedelta(days=days)


# ─────────────────────────────────────────────────────────────────────────────
# 门槛：不是每个 unknown 都找人
# ─────────────────────────────────────────────────────────────────────────────


def test_every_grade_reason_has_exactly_one_disposition() -> None:
    """``GradeReason`` 的每一个成员**要么**开口、**要么**有一句写下来的理由。

    「要么」不是「或者」—— 两个都占也是错的：一个既开口又在「为什么不问人」表里有一句
    话的原因，读起来会像「它其实被排除过」，而那句话不会被执行。

    这一条是全域的，所以新加一个 ``GradeReason`` 成员而没想它的去向时它会红 —— 这正是
    §12 那半句「可低成本补证的交研究，无公开资料的保留 no-route」唯一的落点。
    """
    for reason in GradeReason:
        opened = review_reason_for(reason)
        has_sentence = reason in NOT_A_HUMAN_QUESTION
        assert opened is not None or has_sentence, (
            f"{reason.value!r} 既不开待办，也没有一句「为什么不问人」—— "
            "新加了一个判定原因而没想它的去向。"
        )
        assert not (opened is not None and has_sentence), (
            f"{reason.value!r} 两头都占：它既开口，又在 NOT_A_HUMAN_QUESTION 里有一句"
            "「为什么不问人」。两处会随一次改动分家，而读代码的人只能猜哪一处是真的。"
        )
        if opened is not None:
            assert isinstance(opened, ReviewReason)
        else:
            assert NOT_A_HUMAN_QUESTION[reason].strip(), (
                f"{reason.value!r} 的理由是空的 —— 一条空理由与「没人想过」在报告上同形。"
            )


def test_the_reasons_that_open_a_case_are_exactly_the_conflicts() -> None:
    """开口的**只有** ``CONFLICTING_EVIDENCE`` —— 逐字对着 §12 那句「才入 Inbox」。

    按集合比而不是按个数：个数对了而成员错了，表现是「一个本该交研究的原因在问人」，
    而报告上只显示「开口的还是一类」。
    """
    opened = {reason for reason in GradeReason if review_reason_for(reason) is not None}
    assert opened == {GradeReason.CONFLICTING_EVIDENCE}


def test_a_reason_missing_from_the_table_is_refused_rather_than_excluded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """表里少一行时**抛**，不是返回 ``None`` —— 这一条钉的是「没有兜底分支」。

    没有它，上面那条「要么开口要么有理由」可以由一个 ``else: return None`` 的实现满足 ——
    而那个实现会让**任何**新成员静默落进「不问人」。那与「想过之后决定不问」在报告上
    完全同形，区别只在有没有人记得。
    """
    victim = GradeReason.RULE_MATCHED
    monkeypatch.delitem(NOT_A_HUMAN_QUESTION, victim)
    with pytest.raises(KeyError) as refused:
        review_reason_for(victim)
    assert victim.value in str(refused.value), (
        "错误信息里没有点名那个成员 —— 读它的人会以为是查表写错了，"
        "而不是「有一个成员的归属还没被想过」。"
    )
    assert "NOT_A_HUMAN_QUESTION" in str(refused.value)
    # 而它**不是** ``None``：这一句才是「没有兜底」的判据。
    assert review_reason_for(GradeReason.CONFLICTING_EVIDENCE) is ReviewReason.EVIDENCE_CONFLICT


def test_the_two_sets_of_states_partition_the_enum() -> None:
    """未决与已关闭是 ``ReviewState`` 的一个**划分** —— 不重不漏。

    漏一个的表现是 :func:`is_pending` 落进 ``False``，于是一件真正未决的案件不计入
    pending；重一个的表现是同一件两个都算或都不算，取决于读的顺序。
    """
    assert PENDING_STATES & CLOSED_STATES == frozenset()
    assert PENDING_STATES | CLOSED_STATES == frozenset(ReviewState)
    for state in ReviewState:
        assert is_pending(state) == (state in PENDING_STATES), (
            f"{state.value!r} 的 is_pending 与两张表不一致 —— 而报告里的 pending 数、"
            "流水账的 pending_end 读的都是 is_pending。"
        )


# ─────────────────────────────────────────────────────────────────────────────
# 去重键：四维原像，每一维都要真的动
# ─────────────────────────────────────────────────────────────────────────────


def _key(**overrides: Any) -> str:
    parts = {
        "workspace": WORKSPACE,
        "scene_id": SCENE,
        "reason": ReviewReason.IDENTITY_CONFLICT,
        "subject_ref": "kent-003",
    }
    parts.update(overrides)
    return dedup_key_for(**parts)


def test_each_of_the_four_origin_parts_changes_the_key() -> None:
    """原像的**四维各自**都要动键 —— 这是 ``dedup_key_for`` docstring 那四条的执行器。

    「去重有效」只验「同一个键算出来一样」是不够的：一个把四维里的三维丢掉的实现照样
    满足它，而后果是**两件不同的事被读成同一件**（一个工作区的名字被另一个工作区的
    答案解释掉）。所以每一维都要有一条独立的正面用例。
    """
    base = _key()
    assert _key() == base, "同一组输入算出了两个键 —— 重放会写出第二件待办"

    perturbations = {
        "workspace": {"workspace": "ws-other"},
        "scene_id": {"scene_id": "de-acme-natural-grass"},
        "reason": {"reason": ReviewReason.EVIDENCE_CONFLICT},
        "subject_ref": {"subject_ref": "kent-004"},
    }
    for dimension, override in perturbations.items():
        assert _key(**override) != base, (
            f"改了 {dimension!r} 而键没变 —— 这一维没有进原像，于是「{dimension} 不同」"
            "的两件不同的事会被读成同一件，而报告上只显示「有一件待办」。"
        )

    # 而键**不含**措辞与时刻：正面的那一半（同一件事问两次是同一个键）已经在上面。
    assert _key(subject_ref="kent-003") == base


def test_the_case_id_is_the_hash_of_the_dedup_key_and_the_model_enforces_it() -> None:
    """``case_id`` 必须是算出来的。手写一个的表现是「同一件事开出两件待办」。

    反面那一半（手写的 id 被拒）是这一条的重点：内容寻址的 id 里，手工指定的那一格
    与内容不匹配时**没有任何一处会红** —— 两件待办内容相同、id 不同，
    「同一个问题不重复问」在那之后就静默不成立了。
    """
    case_id = case_id_for(
        workspace=WORKSPACE, scene_id=SCENE, reason=ReviewReason.IDENTITY_CONFLICT,
        subject_ref="kent-003",
    )
    assert case_id.startswith("rc-")
    assert len(case_id) == len("rc-") + 16
    assert case_id == case_id_for(
        workspace=WORKSPACE, scene_id=SCENE, reason=ReviewReason.IDENTITY_CONFLICT,
        subject_ref="kent-003",
    )

    _case = _make(case_id=case_id)
    assert _case.case_id == case_id

    with pytest.raises(ValidationError) as refused:
        _make(case_id="rc-0000000000000000")
    assert "case_id" in str(refused.value)


def test_a_subject_ref_that_is_not_canonical_is_refused_not_normalised() -> None:
    """带空格/大写的 ``subject_ref`` 被拒 —— 「拒绝，不规范化」的先例。

    规范化的后果不是「宽松一点」：同一个主体在两次调用里一次原文、一次被 slugify，
    会算出两个 ``dedup_key``，而重放就写下了第二件待办。一次静默的重复比一次拒绝贵。
    """
    with pytest.raises(ValueError) as refused:
        dedup_key_for(
            workspace=WORKSPACE, scene_id=SCENE, reason=ReviewReason.IDENTITY_CONFLICT,
            subject_ref="Kent 003",
        )
    assert "subject_ref" in str(refused.value)


# ─────────────────────────────────────────────────────────────────────────────
# 两个对象的校验器
# ─────────────────────────────────────────────────────────────────────────────


def _make(case_id: str | None = None, **overrides: Any) -> ReviewCase:
    """一件最小的合法案件，按需覆盖。``case_id`` 缺省时**算**出来。"""
    fields: dict[str, Any] = {
        "workspace": WORKSPACE,
        "scene_id": SCENE,
        "reason": ReviewReason.IDENTITY_CONFLICT,
        "subject_ref": "kent-003",
        "affected_refs": ("kent-003",),
        "options": (ResolutionOutcome.CORRECT_OBJECT, ResolutionOutcome.DISMISS),
        "state": ReviewState.OPEN,
        "version": 1,
        "opened_at": T0,
        "updated_at": T0,
    }
    fields.update(overrides)
    if case_id is None:
        case_id = case_id_for(
            workspace=fields["workspace"],
            scene_id=fields["scene_id"],
            reason=fields["reason"],
            subject_ref=fields["subject_ref"],
        )
    return ReviewCase(case_id=case_id, **fields)


def _answer(case: ReviewCase, outcome: ResolutionOutcome, *, at: datetime, n: int,
            by: str = "member-a", proposed_version: int | None = None) -> HumanResolution:
    """一条已写下的决定。``n`` 只用来让 ``resolution_id`` 互不相同（同刻稳定排序）。

    ``proposed_version`` **没有缺省成「案件当前那一版」**：一条提议挂着哪一版是它自己的
    内容（``HumanResolution`` 的校验器就挡着「提议却不说是哪一版」），由调用点显式给出，
    夹具不替它猜。
    """
    return HumanResolution(
        resolution_id=f"res-{n}", case_id=case.case_id, workspace=case.workspace,
        outcome=outcome, answer="因为名录上写着 kent-003 就是那家俱乐部",
        by=by, roles=("controller",), expected_version=case.version, at=at,
        proposed_version=proposed_version,
    )


def _closed(case: ReviewCase, *, at: datetime, state: ReviewState) -> ReviewCase:
    """把一件案件**手写**成关闭态。刻意不由代码算 —— 夹具不能拿被测的那套推导出来。"""
    return case.model_copy(
        update={
            "state": state,
            "closed_at": at,
            "updated_at": at,
            "version": case.version + 1,
            "claimed_by": "",
        }
    )


def test_a_case_offers_a_real_choice() -> None:
    """一条选项不够就不是待办；重复项与「建议不在选项里」都要拒。"""
    with pytest.raises(ValidationError) as one:
        _make(options=(ResolutionOutcome.DISMISS,))
    assert "options" in str(one.value)

    with pytest.raises(ValidationError) as dup:
        _make(options=(ResolutionOutcome.DISMISS, ResolutionOutcome.DISMISS))
    assert "重复" in str(dup.value)

    with pytest.raises(ValidationError) as outside:
        _make(recommendation=ResolutionOutcome.REOPEN)
    assert "不在可选项" in str(outside.value)

    assert _make(recommendation=ResolutionOutcome.DISMISS).recommendation is (
        ResolutionOutcome.DISMISS
    )


def test_closed_and_closed_at_move_together() -> None:
    """关闭态与 ``closed_at`` 同进同退；关闭之后不许还挂着认领者。"""
    with pytest.raises(ValidationError) as missing:
        _make(state=ReviewState.RESOLVED)
    assert "closed_at" in str(missing.value)

    with pytest.raises(ValidationError) as early:
        _make(closed_at=T0)
    assert "closed_at" in str(early.value)

    with pytest.raises(ValidationError) as claimed:
        _make(state=ReviewState.RESOLVED, closed_at=T0, claimed_by="member-a")
    assert "claimed_by" in str(claimed.value)

    assert _make(state=ReviewState.RESOLVED, closed_at=T0).closed_at == T0


def test_awaiting_approval_and_the_proposed_version_move_together() -> None:
    """两格同进同退。少一格的表现都是**看起来正常**的：提议自动生效，或静默地等。

    正面那一条与反面那两条都要有，否则「同进同退」只在两个方向各验了一半。
    """
    with pytest.raises(ValidationError) as state_only:
        _make(state=ReviewState.AWAITING_APPROVAL)
    assert "proposed_version" in str(state_only.value)

    with pytest.raises(ValidationError) as version_only:
        _make(proposed_version=13)
    assert "proposed_version" in str(version_only.value)
    assert "同进同退" in str(version_only.value)

    both = _make(state=ReviewState.AWAITING_APPROVAL, proposed_version=13)
    assert both.proposed_version == 13
    assert is_pending(both.state), (
        "等批准的状态必须是**未决**的 —— 它不是待办的话，一份还没生效的提议会让"
        "整件案件从报告里消失。"
    )


def test_a_merge_carries_both_the_destination_and_the_moment() -> None:
    """``merged_into`` 与 ``merged_at`` 同进同退（缺时刻就算不出它何时退出待办）。"""
    with pytest.raises(ValidationError) as dest_only:
        _make(merged_into="rc-0000000000000000")
    assert "merged_at" in str(dest_only.value)

    with pytest.raises(ValidationError) as moment_only:
        _make(merged_at=T0)
    assert "merged_into" in str(moment_only.value)

    with pytest.raises(ValidationError):
        _make(merged_into=None, merged_at=T0)


def test_a_resolution_must_say_who_decided_and_what_they_decided() -> None:
    """空答案、空 ``by``、空 ``roles`` 三样都拒 —— 三样各自是一个不同的洞。"""
    case = _make()
    assert _answer(case, ResolutionOutcome.DISMISS, at=_at(1), n=9).answer

    with pytest.raises(ValidationError) as empty_answer:
        HumanResolution(
            resolution_id="res-x", case_id=case.case_id, workspace=WORKSPACE,
            outcome=ResolutionOutcome.DISMISS, answer="  ", by="member-a",
            roles=("controller",), expected_version=1, at=_at(1),
        )
    assert "answer" in str(empty_answer.value)

    with pytest.raises(ValidationError) as no_by:
        HumanResolution(
            resolution_id="res-y", case_id=case.case_id, workspace=WORKSPACE,
            outcome=ResolutionOutcome.DISMISS, answer="不值得处理", by=" ",
            roles=("controller",), expected_version=1, at=_at(1),
        )
    assert "by" in str(no_by.value)

    with pytest.raises(ValidationError) as no_roles:
        HumanResolution(
            resolution_id="res-z", case_id=case.case_id, workspace=WORKSPACE,
            outcome=ResolutionOutcome.DISMISS, answer="不值得处理", by="member-a",
            roles=(), expected_version=1, at=_at(1),
        )
    assert "roles" in str(no_roles.value)


def test_a_proposal_says_which_version_it_suspends_and_nothing_else_does() -> None:
    """``PROPOSE_POLICY_CHANGE`` 与 ``proposed_version`` 同进同退，**两个方向都拒**。

    两个方向是两个不同的洞，所以必须各有一半：

    * 提议而不说挂的是哪一版 —— ``control_scene(activate)`` 的闸门要按版本号比，
      没有版本号的提议让那句「等批准」没有宾语；
    * 不提议却带着一个版本号 —— 那一版**永远等不到批准**（没有任何一件待办在等它），
      而它在盘上看起来只是一次普通的规则变更。

    两半都必须是 ``ValidationError``（构造期），不是裁决时才发现：一条说不出话的决定
    一旦写进只增不改的那张表，就没有地方能补。
    """
    case = _make()

    with pytest.raises(ValidationError) as no_version:
        _answer(case, ResolutionOutcome.PROPOSE_POLICY_CHANGE, at=_at(1), n=1)
    assert "proposed_version" in str(no_version.value)

    with pytest.raises(ValidationError) as stray_version:
        _answer(case, ResolutionOutcome.DISMISS, at=_at(1), n=1, proposed_version=13)
    assert "proposed_version" in str(stray_version.value)

    # 而**批准**不带版本号：它批准的是案件当前挂着的那一版，而那一版由
    # ``expected_version`` 的 CAS 钉住 —— 见 ``approve_policy_change``。
    assert _answer(case, ResolutionOutcome.APPROVE_POLICY_CHANGE, at=_at(1), n=1).outcome


# ─────────────────────────────────────────────────────────────────────────────
# 流水账：§12 那个恒等式
# ─────────────────────────────────────────────────────────────────────────────


def _flow(cases: tuple[ReviewCase, ...], resolutions: tuple[HumanResolution, ...],
          *, start: float, end: float):
    return inbox_flow(
        cases=cases, resolutions=resolutions, window_start=_at(start), window_end=_at(end)
    )


def _assert_reconciles(flow: Any) -> None:
    assert flow.reconciles, (
        f"§12 的恒等式对不上：{flow.equation}。\n"
        "pending_start + opened + reopened - resolved - dismissed - merged = pending_end "
        "在任何一段窗口上都成立，是「待办是累计状态而不是每轮覆盖的窗口」这句话的"
        "全部内容。"
    )


def test_a_case_opened_inside_the_window_is_opened_not_pending_start() -> None:
    """窗口**内**开的案件算 ``opened``，不算 ``pending_start`` —— 它就是边界那一条。

    边界写错一个符号（``<=`` 而不是 ``<``）的表现正是这里：同一件案件同时被算进两个数，
    而恒等式**照样成立** —— 两边各多一个。所以这一条要同时钉两个数，不能只钉
    ``reconciles``。
    """
    case = _make(opened_at=_at(1), updated_at=_at(1))
    flow = _flow((case,), (), start=0, end=2)
    _assert_reconciles(flow)
    assert (flow.pending_start, flow.opened, flow.pending_end) == (0, 1, 1)

    # 而窗口**之前**开的那一件正好相反。
    older = _make(subject_ref="kent-004", opened_at=_at(-1), updated_at=_at(-1))
    flow = _flow((older,), (), start=0, end=2)
    _assert_reconciles(flow)
    assert (flow.pending_start, flow.opened, flow.pending_end) == (1, 0, 1)


def test_a_resolved_case_leaves_pending_and_a_reopen_brings_it_back() -> None:
    """关掉再重开：``reopened`` 与 ``opened`` 是两个数，快照法在这里会一起丢掉它们。

    这正是一条**必须**从事件序列算的用例：案件最终是未决的（``closed_at`` 被清成
    ``None``），所以「它一开始是未决的吗」只能看事件。这也是为什么这一段的第一条是
    一条**单窗口**的用例 —— 重开的两段各看一遍。
    """
    case = _make(opened_at=_at(0), updated_at=_at(0))
    resolved = _answer(case, ResolutionOutcome.CORRECT_OBJECT, at=_at(1), n=1)
    closed = _closed(case, at=_at(1), state=ReviewState.RESOLVED)
    reopened = _answer(case, ResolutionOutcome.REOPEN, at=_at(2), n=2)
    live = _closed(case, at=_at(1), state=ReviewState.RESOLVED).model_copy(
        update={"state": ReviewState.OPEN, "closed_at": None, "updated_at": _at(2)}
    )

    # 第一段：开着 → 关掉。
    #
    # ``pending_start`` 是 0 而不是 1：这一件**刚好在窗口起点开**，而窗口是左闭右开的，
    # 所以它属于这一段 —— 算 ``opened``，不算「一开始就未决」。两个数各差 1 才是对的，
    # 而恒等式在两边各多一个的情况下**照样成立**；这就是为什么这一条要同时钉两个数。
    first = _flow((closed,), (resolved,), start=0, end=1.5)
    _assert_reconciles(first)
    assert (first.pending_start, first.opened, first.resolved, first.pending_end) == (
        0, 1, 1, 0,
    )

    # 第二段：关着 → 重开。
    second = _flow((live,), (resolved, reopened), start=1.5, end=3)
    _assert_reconciles(second)
    assert (
        second.pending_start,
        second.reopened,
        second.pending_end,
    ) == (0, 1, 1), (
        f"重开没有被读成一个独立的事件：{second.equation}。\n"
        "关掉又重开的案件最终状态是未决的（closed_at 是 None），所以只看当前状态"
        "会把它读成「从来没关过」，opened 与 reopened 两个数一起丢掉。"
    )

    # 两段的接缝：前一段的 pending_end 必须等于后一段的 pending_start。
    assert first.pending_end == second.pending_start
    assert first.opened + second.opened == 1, "同一件案件的「开」被数了两次或漏了"


def test_a_policy_proposal_keeps_the_case_pending_until_it_is_approved() -> None:
    """提议**不**让案件离开待办；批准才让它离开。

    这是「规则提议不自动生效」在流水账上的样子：提议之后案件仍是未决的一个
    （``AWAITING_APPROVAL``），所以 ``resolved`` 不动、``pending_end`` 也不动。
    若提议被算成一次 ``resolved``，那件案子会在报告里变成「人已经处理完了」——
    而事实是一条还没生效的规则。
    """
    case = _make(opened_at=_at(-1), updated_at=_at(-1))
    proposed = _answer(
        case, ResolutionOutcome.PROPOSE_POLICY_CHANGE, at=_at(1), n=1, proposed_version=13
    )
    awaiting = _make(
        opened_at=_at(-1), updated_at=_at(1), state=ReviewState.AWAITING_APPROVAL,
        proposed_version=13,
    )

    proposal = _flow((awaiting,), (proposed,), start=0, end=2)
    _assert_reconciles(proposal)
    assert (
        proposal.pending_start,
        proposal.resolved,
        proposal.dismissed,
        proposal.pending_end,
    ) == (1, 0, 0, 1), "提议把一个未决案件换成了另一个未决案件，账面上不该有任何变化"

    approved = _answer(
        case, ResolutionOutcome.APPROVE_POLICY_CHANGE, at=_at(2), n=2, by="dana"
    )
    done = _closed(case, at=_at(2), state=ReviewState.RESOLVED)
    decided = _flow((done,), (proposed, approved), start=1.5, end=3)
    _assert_reconciles(decided)
    assert (decided.pending_start, decided.resolved, decided.pending_end) == (1, 1, 0)

    # 而两次决定是**两个不同的人**写下的 —— 这是 §3 那条承重不变量能被验的地方。
    assert proposed.by != approved.by


def test_a_merge_removes_one_pending_case_and_says_when() -> None:
    """合并让一件待办退出，且**只在它当时确实未决时**记账。

    后半句是这一条的重点：把一件早就关掉的案件合并进去是一次数据修复，它没有让任何
    待办消失。给它记一次 ``merged`` 会让恒等式少一个数，而两边看上去都「只差 1」。
    """
    host = _make(subject_ref="kent-003", opened_at=_at(-1), updated_at=_at(-1))
    dup = _make(subject_ref="kent-004", opened_at=_at(-1), updated_at=_at(-1))
    merged = dup.model_copy(
        update={
            "merged_into": host.case_id,
            "merged_at": _at(1),
            "updated_at": _at(1),
        }
    )

    flow = _flow((host, merged), (), start=0, end=2)
    _assert_reconciles(flow)
    assert (flow.pending_start, flow.merged, flow.pending_end) == (2, 1, 1), (
        f"{flow.equation} —— 合并必须让待办**少一件**，否则报告会显示两件其实是同一件"
        "的事。"
    )

    # 反面：合并的是一件早就关掉的案件 —— 待办一件都没少，所以不记 merged。
    dead = _closed(dup, at=_at(-1), state=ReviewState.DISMISSED)
    repaired = dead.model_copy(
        update={"merged_into": host.case_id, "merged_at": _at(1), "updated_at": _at(1)}
    )
    repair_flow = _flow((host, repaired), (), start=0, end=2)
    _assert_reconciles(repair_flow)
    assert repair_flow.merged == 0, (
        "把一件已关闭的案件合并进去被记成了一次合并 —— 那件事没有让任何待办消失，"
        "而恒等式会因此少一个数。"
    )


def test_transfer_has_no_producer_so_it_is_zero_and_says_so() -> None:
    """``transferred`` 本切片恒为 0 —— 一个恒为 0 的格子必须被写明，不能留给人猜。

    §12 的措辞是「另记合并/转移」。转移在这一片没有生产者（没有哪一格能算出它），
    所以它留在恒等式里、值恒为 0。这一条钉的是那件事**被写下来了** —— 否则一个恒为 0
    的格子读起来像「量过了，是零」。
    """
    case = _make()
    flow = _flow((case,), (), start=0, end=2)
    _assert_reconciles(flow)
    assert flow.transferred == 0


def test_adjacent_windows_reconcile_and_join_up() -> None:
    """同一批事件切两半：各段自己的账要平，接缝处还要对得上。

    「一段窗口对得上」是弱的：把 ``opened`` 记成 ``reopened`` 也照样对得上。跨窗口才
    钉得住「一件事没有在接缝上被漏掉或被数两遍」—— 那一条是 ``first.pending_end ==
    second.pending_start`` 加上各分项求和。

    而**边界上那一个属于谁**，由下面那一条专门的用例验；这一条里没有任何事件落在端点
    上，所以它验不了那个约定（这一点是实测出来的：把边界判定的 ``<`` 改成 ``<=`` 时，
    这一条全绿）。
    """
    a = _make(subject_ref="kent-003", opened_at=_at(-2), updated_at=_at(-2))
    b = _make(subject_ref="kent-004", opened_at=_at(0.5), updated_at=_at(0.5))

    rows = (
        _answer(a, ResolutionOutcome.CORRECT_OBJECT, at=_at(0.25), n=1),
        _answer(b, ResolutionOutcome.DISMISS, at=_at(1.75), n=2),
        _answer(a, ResolutionOutcome.REOPEN, at=_at(3), n=3),
    )
    cases = (
        _closed(a, at=_at(0.25), state=ReviewState.RESOLVED).model_copy(
            update={"state": ReviewState.OPEN, "closed_at": None, "updated_at": _at(3)}
        ),
        _closed(b, at=_at(1.75), state=ReviewState.DISMISSED),
    )

    middle = 2.0
    first = _flow(cases, rows, start=-3, end=middle)
    second = _flow(cases, rows, start=middle, end=4)

    _assert_reconciles(first)
    _assert_reconciles(second)
    assert first.pending_end == second.pending_start, (
        f"接缝对不上：前一段结束时 {first.pending_end} 件未决，后一段开始时 "
        f"{second.pending_start} 件。\n"
        "窗口是左闭右开的 [start, end)：一个刚好落在边界上的事件属于后一段。写反的表现"
        "正是这里 —— 两段各自都对得上账，而合起来少了一件或多了两件。"
    )

    # 而各段的分项合起来等于总数（没有一件事被两段都数一遍）。
    assert first.opened + second.opened == 2, "两件都是在第一段里开的"
    assert first.reopened + second.reopened == 1
    assert first.resolved + second.resolved == 1
    assert first.dismissed + second.dismissed == 1
    assert first.pending_start == 0, "两件都在窗口开始之后才存在，所以一开始没有未决的"
    assert second.pending_end == 1, "第二段结束时，重开过的 kent-003 又是一件未决"


def test_an_event_landing_exactly_on_a_boundary_belongs_to_the_later_window() -> None:
    """**边界上的那一个到底属于谁** —— 左闭右开这个约定的执行器。

    ## 为什么必须专门有这一条（它是实测出来的，不是想出来的）

    上面那些用例里没有任何事件或开案**落在**窗口的端点上（都在窗口内部）。于是把
    ``_state_before`` 的 ``<`` 改成 ``<=``、或把开案判定的 ``< end`` 改成 ``<= end``，
    上面整套流水账的判据**全绿** —— 而那两个改动各自都会让恒等式在真实的窗口上不成立
    （把一件在接缝上关掉的案件同时算作「未决」与「已解决」）。

    边界只有在**边界上真的有东西**的时候才被验到。所以这里三件案子各钉一个端点：

    * **A** 被一条**刚好落在窗口起点**的事件关掉：它在这一段里记一次 ``resolved``，
      而在起点**之前**仍是未决 —— 所以 ``pending_start`` 也要算它。
    * **B** 被一条**刚好落在窗口终点**的事件关掉：这一段不算（它会算进下一段），
      而它在终点**之前**仍是未决，所以 ``pending_end`` 要算它。
    * **C** **刚好在窗口终点开**：这一段不算开案，下一段算。
    """
    a = _make(subject_ref="kent-001", opened_at=_at(-1), updated_at=_at(-1))
    b = _make(subject_ref="kent-002", opened_at=_at(-1), updated_at=_at(-1))
    c = _make(subject_ref="kent-003", opened_at=_at(2), updated_at=_at(2))

    rows = (
        _answer(a, ResolutionOutcome.CORRECT_OBJECT, at=_at(0), n=1),  # 刚好在起点
        _answer(b, ResolutionOutcome.CORRECT_OBJECT, at=_at(2), n=2),  # 刚好在终点
    )
    cases = (
        _closed(a, at=_at(0), state=ReviewState.RESOLVED),
        _closed(b, at=_at(2), state=ReviewState.RESOLVED),
        c,
    )

    first = _flow(cases, rows, start=0, end=2)
    second = _flow(cases, rows, start=2, end=4)

    _assert_reconciles(first)
    _assert_reconciles(second)

    assert (first.pending_start, first.resolved, first.pending_end) == (2, 1, 1), (
        f"起点上的那条事件没有被读对：{first.equation}。\n"
        "A 在起点**之前**是未决的（那条关掉它的事件正好落在起点上，属于**这一段**），"
        "所以 pending_start 要算 A 与 B 两件、resolved 算一条、pending_end 只剩 B。"
    )
    assert first.opened == 0, "C 是在终点开的，不属于这一段"
    assert (second.pending_start, second.opened, second.resolved, second.pending_end) == (
        1, 1, 1, 1,
    ), (
        f"终点上的那两件事没有被读对：{second.equation}。\n"
        "B 被终点上的事件关掉（算进这一段），C 在终点开（也属于这一段）。"
    )

    # 接缝：前一段结束时的未决数与后一段开始时的未决数必须相等。
    assert first.pending_end == second.pending_start
    # 而三件案子各自被数到恰好一次，一件都没漏。
    assert first.opened + second.opened == 1, "只有 C 是「开」的"
    assert first.resolved + second.resolved == 2, "A 与 B 各被关掉一次"


# ─────────────────────────────────────────────────────────────────────────────
# 折进一件案件：``apply_resolution`` 的判据
# ─────────────────────────────────────────────────────────────────────────────
#
# 这一族住在 domain 而不是存储契约测试里，因为这几条判据**在端口上够不着**：
# ``record_resolution`` 是按 ``resolution.case_id`` 去取那一件的，所以「决定的
# case_id 与案件对不上」在端口上构造不出来；工作区那一条也会先被 ``bind_workspace``
# 拦住；而被合并走的那一件在端口上没有写入路径（见流水账那一节）。判据仍然必须被
# 执行 —— 一个够不着也没被验的分支，正是下一次重构里第一个被删掉的。


def test_a_decision_must_actually_change_the_state() -> None:
    """**同一条决定连做两次被拒** —— 这一条是实测出来的，不是洁癖。

    最小复现：开一件、放下两次、取一段覆盖两个时刻的窗口 —— 常数项当场不成立
    （``1 + 0 + 0 - 0 - 2 - 0 = 0`` 为假），而两份报告各自看上去都自洽。
    流水账那一层**无法**自己发现：它只看得见事件，「这两次放下是同一件事」这个信息
    只存在于状态里。

    所以判据的形式是「结果状态必须与当前状态不同」，而不是逐成员枚举「同一件不许
    放两次」—— 前者对新加的成员自动成立，而后者会漏掉 ``CORRECT_OBJECT`` 那一格。
    """
    case = _make()
    dismissed = apply_resolution(case, _answer(case, ResolutionOutcome.DISMISS, at=_at(1), n=1))

    with pytest.raises(ReviewConflictError) as again:
        apply_resolution(dismissed, _answer(dismissed, ResolutionOutcome.DISMISS, at=_at(2), n=2))
    assert "dismissed" in str(again.value)
    assert "REOPEN" in str(again.value), (
        "拒绝里必须说出**出路**（先重开）—— 只报「不能这样做」的回执会让人去改代码。"
    )

    # 而反面那一半：连做两次**不同**的决定仍然放行 —— 只钉「重复被拒」而不钉
    # 「正常的不重复仍然放行」，会把一个全拒的实现读成绿的。这里选的是「提议 → 改
    # 对象」这一对：两条决定的结果状态不同（``AWAITING_APPROVAL`` → ``RESOLVED``），
    # 而案件**只离开待办一次**（提议把一件未决的换成了另一件未决的，账面上什么都没
    # 变）。换成「改对象 → 放下」那一对就不行 —— 那是同一件离开两次，见下一条。
    proposing = _make(
        options=(
            ResolutionOutcome.PROPOSE_POLICY_CHANGE,
            ResolutionOutcome.CORRECT_OBJECT,
            ResolutionOutcome.DISMISS,
        )
    )
    proposed = apply_resolution(
        proposing,
        _answer(
            proposing, ResolutionOutcome.PROPOSE_POLICY_CHANGE, at=_at(1), n=3, proposed_version=2
        ),
    )
    assert proposed.state is ReviewState.AWAITING_APPROVAL
    assert apply_resolution(
        proposed, _answer(proposed, ResolutionOutcome.CORRECT_OBJECT, at=_at(2), n=4)
    ).state is ReviewState.RESOLVED


def test_a_closed_case_takes_only_a_reopen() -> None:
    """一件已经关掉的案件**只收 REOPEN** —— 换一个关闭结果重关它不是一条路。

    这一条与上面那条是同一个缺陷的另一个实例，也是实测出来的。上面那条挡的是**同一
    条**决定放两次（``DISMISS`` → ``DISMISS``，结果状态与当前状态相同）；这一条挡的
    是**两条不同的关闭决定**（``RESOLVED`` → ``DISMISSED``）—— 后者对「结果状态必须
    与当前状态不同」那条判据是**成立的**，所以它会一路放行到盘上，而流水账当场对不上：

    ``CORRECT_OBJECT`` 与 ``DISMISS`` 各记一次 ⇒ 一件案件离开待办**两次**，而它只开了
    一次、也没重开过，于是

        pending_start + opened + reopened - resolved - dismissed - merged = 0

    左边比 ``pending_end`` 少一（实测 ``1 + 0 + 0 - 1 - 1 - 0 = 0`` 为假）。这件事流水账
    那一层**无法**自己发现：它只看得见「关过一次、又关过一次」，而两次用的都是合法结果。

    ``REOPEN`` 之后重关一次是对得上的 —— 那正是那条恒等式允许的循环：一件回来过，
    所以它离开两次也成立。
    """
    case = _make()
    resolved = apply_resolution(
        case, _answer(case, ResolutionOutcome.CORRECT_OBJECT, at=_at(1), n=1)
    )
    assert resolved.state is ReviewState.RESOLVED

    with pytest.raises(ReviewConflictError) as again:
        apply_resolution(resolved, _answer(resolved, ResolutionOutcome.DISMISS, at=_at(2), n=2))
    assert "dismiss" in str(again.value)
    assert "REOPEN" in str(again.value), (
        "关掉的那一件与开着的那一件**出路不同**（重开），拒绝里必须说出那一条 —— "
        "只报「不能这样做」的回执会让人去改代码。"
    )

    # 出路就是那唯一被允许的决定：重开之后这一件又能收下决定，而两笔账对得上。
    reopened = apply_resolution(
        resolved, _answer(resolved, ResolutionOutcome.REOPEN, at=_at(2), n=3)
    )
    assert reopened.state is ReviewState.OPEN
    dismissed = apply_resolution(
        reopened, _answer(reopened, ResolutionOutcome.DISMISS, at=_at(3), n=4)
    )
    assert dismissed.state is ReviewState.DISMISSED

    flow = _flow(
        (dismissed,),
        (
            _answer(case, ResolutionOutcome.CORRECT_OBJECT, at=_at(1), n=1),
            _answer(resolved, ResolutionOutcome.REOPEN, at=_at(2), n=3),
            _answer(reopened, ResolutionOutcome.DISMISS, at=_at(3), n=4),
        ),
        start=0.5,
        end=4,
    )
    _assert_reconciles(flow)
    assert (flow.pending_start, flow.reopened, flow.resolved, flow.dismissed) == (1, 1, 1, 1), (
        f"关一次、重开一次、再关一次，账上应当是各记一笔：{flow.equation}"
    )
    assert flow.pending_end == 0


def test_reopen_is_available_without_being_declared_in_the_options() -> None:
    """``REOPEN`` **不在选项表里也能选**，而它只对关闭态成立。

    它是这一族里唯一的例外，理由写在 :func:`may_decide`：把它塞进 ``options`` 会让
    每件待办的作者都要记得加一项，而漏加的表现是**这条路走不通** —— ``REOPEN`` 被
    成员判据拒掉，于是 §B20 的「重启不丢」在盘上永远发生不了，而报告上只看得出
    「没有人重开过」。

    正面（关闭态可选）与反面（开着不能选）都要钉：只钉正面的话，一个「REOPEN 永远
    放行」的实现会让一件开着的待办被「重开」成一个新版本而状态不变。
    """
    case = _make()
    assert ResolutionOutcome.REOPEN not in case.options, (
        "这一条的前提是 REOPEN **不在**缺省的选项表里；它若被加进去了，"
        "这一条就不再验「例外」那件事了。"
    )
    assert not may_decide(case, ResolutionOutcome.REOPEN), "开着的东西没有「重新打开」"

    dismissed = apply_resolution(case, _answer(case, ResolutionOutcome.DISMISS, at=_at(1), n=1))
    assert may_decide(dismissed, ResolutionOutcome.REOPEN)

    reopened = apply_resolution(
        dismissed, _answer(dismissed, ResolutionOutcome.REOPEN, at=_at(2), n=2)
    )
    assert reopened.state is ReviewState.OPEN
    assert reopened.closed_at is None, "一件未决的案件带着一个关闭时刻，「它等了多久」就答错了"


def test_approval_is_available_without_being_declared_in_the_options() -> None:
    """``APPROVE_POLICY_CHANGE`` **不在选项表里也能选**，而它只对 ``AWAITING_APPROVAL`` 成立。

    与上一条是同一个理由的第二次应用（理由写在 :func:`may_decide`），而它的漏加后果
    **实测过一次**：一条提议写下去之后**谁也批不了** —— ``approve_policy_change`` 在存储
    那一层落到 ``may_decide`` 被拒，而报告上只显示「没有人批准过」。一条死路与一件没人
    做过的事在那一格上是同一个样子，所以这一条必须由域层自己钉住，而不是靠一条 MCP
    测试从外面观测。

    正面（等批准的那一件可选）与反面（还开着的不行、已经关了的不行）都要钉：只钉正面
    的话，一个「批准永远放行」的实现会让一件普通的未决待办被「批准」成一个关闭结果，
    而那次调用看上去完全正常。

    顺带钉住**提议不重写选项表**：``options`` 那张表是案件作者写给**第一个人**看的
    （``INTAKE`` 按原因给的那几个答案）。它若是被提议重写，批准那一格就会"看起来"
    在选项里 —— 而那是夹具才会造出来的形态，生产路径上从来没有过。
    """
    proposing = _make(
        options=(
            ResolutionOutcome.PROPOSE_POLICY_CHANGE,
            ResolutionOutcome.CORRECT_OBJECT,
            ResolutionOutcome.DISMISS,
        )
    )
    assert ResolutionOutcome.APPROVE_POLICY_CHANGE not in proposing.options, (
        "这一条的前提是批准**不在**选项表里；它若被加进去了，这一条就不再验「例外」那件事。"
    )
    assert not may_decide(proposing, ResolutionOutcome.APPROVE_POLICY_CHANGE), (
        "一件还开着的待办上没有「批准」这个动作 —— 它等的不是另一个人的批准，是一个人。"
    )

    awaiting = apply_resolution(
        proposing,
        _answer(
            proposing, ResolutionOutcome.PROPOSE_POLICY_CHANGE, at=_at(1), n=1, proposed_version=2
        ),
    )
    assert awaiting.state is ReviewState.AWAITING_APPROVAL
    assert awaiting.options == proposing.options, (
        "提议不重写选项表 —— 它把案件换了一个状态，没有换过那张给人看的表。"
    )
    assert awaiting.proposed_version == 2
    assert may_decide(awaiting, ResolutionOutcome.APPROVE_POLICY_CHANGE), (
        "一条已经写在盘上的提议必须有人能批 —— 这一格是「提议 → 审批」那条链的出口。"
    )

    # 关掉之后也不行：一件已经裁决过的案件，唯一的动作是重开。
    closed = apply_resolution(
        awaiting, _answer(awaiting, ResolutionOutcome.DISMISS, at=_at(2), n=2)
    )
    assert closed.state is ReviewState.DISMISSED
    assert not may_decide(closed, ResolutionOutcome.APPROVE_POLICY_CHANGE)
    assert may_decide(closed, ResolutionOutcome.REOPEN)


def test_a_reopen_and_a_second_dismiss_still_reconcile() -> None:
    """关掉 → 重开 → 再关掉，**整条链**在流水账上对得上。

    这是上面那条修复的端到端形态：``pending_start + opened + reopened - resolved
    - dismissed = pending_end`` 必须对任意一段窗口成立，而链里必须**真的**有两个
    ``dismissed``（否则这一条退化成一个不重开的普通案件，什么都验不到）。
    """
    case = _make()
    first = _answer(case, ResolutionOutcome.DISMISS, at=_at(1), n=1)
    dismissed = apply_resolution(case, first)
    back = _answer(dismissed, ResolutionOutcome.REOPEN, at=_at(2), n=2)
    reopened = apply_resolution(dismissed, back)
    second = _answer(reopened, ResolutionOutcome.DISMISS, at=_at(3), n=3)
    final = apply_resolution(reopened, second)

    flow = _flow((final,), (first, back, second), start=0.5, end=4)

    _assert_reconciles(flow)
    assert (
        flow.pending_start,
        flow.reopened,
        flow.dismissed,
        flow.pending_end,
    ) == (1, 1, 2, 0), f"重开没有被记成一次 reopened：{flow.equation}"


def test_a_decision_for_another_case_is_refused() -> None:
    """一条写错了案件的决定被拒 —— 不然它会追加进另一件的历史。

    后果是**静默**的：流水账按 ``case_id`` 分组，那一行既不进 opened 也不进 resolved，
    恒等式照样成立，而那一件案件的历史里多了一句不属于它的话。
    """
    one = _make()
    other = _make(subject_ref="kent-004")
    # 关键在于这条决定是给 ``one`` 的，而它被折进的是 ``other`` —— 不是把 id 改对。
    stray = _answer(one, ResolutionOutcome.DISMISS, at=_at(1), n=1)

    with pytest.raises(ReviewConflictError) as refused:
        apply_resolution(other, stray)
    assert one.case_id in str(refused.value) and other.case_id in str(refused.value), (
        "回执里要同时有点名的两边 —— 只说「对不上」读的人无法判断该改哪一边"
    )


def test_a_decision_from_another_workspace_is_refused() -> None:
    """一个工作区的答案不能被另一个工作区的案件读到。"""
    case = _make()
    foreign = _answer(case, ResolutionOutcome.DISMISS, at=_at(1), n=1).model_copy(
        update={"workspace": "ws-other"}
    )

    with pytest.raises(ReviewConflictError) as refused:
        apply_resolution(case, foreign)
    assert "ws-other" in str(refused.value)


def test_a_case_merged_away_can_no_longer_be_decided() -> None:
    """一件已经交出去的待办不能再被单独裁决：它的答案会落在一件报告上不再显示的案件里。

    这个分支今天在端口上**没有生产者**（``ReviewCase.merged_into`` 的写入路径不在
    本切片里 —— §12 的原文是「另记合并/转移」，而 :func:`inbox_flow` 已经按它算账）。
    它仍然必须被验：一个够不着也没被验的分支，是下一次重构里第一个被删掉的东西，
    而删掉它的后果是流水账上那一格静默地少算一件。
    """
    case = _make()
    merged = case.model_copy(
        update={
            "merged_into": "rc-0000000000000001",
            "merged_at": _at(1),
            "updated_at": _at(1),
            "version": 2,
        }
    )

    with pytest.raises(ReviewConflictError) as gone:
        apply_resolution(merged, _answer(merged, ResolutionOutcome.DISMISS, at=_at(2), n=1))
    assert "并入" in str(gone.value)
