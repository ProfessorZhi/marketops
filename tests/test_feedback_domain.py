"""§B23 反馈环的领域判据。**五个类别、三个环、七档作用域、五格不许自动扩大。**

## 这个文件判的是什么，不判什么

判的是**表格之间的一致性**与**那几条被点名过的否定**。它不判「``fact_correction``
走事实路径合不合理」—— 那是文档的判据，代码抄得对不对由这里判。

## 为什么表格的一致性值得单独判

``RING_OF`` 与 ``DISPOSITION`` 是同一份文档里那两句话的两个投影（一句说这一类比属
哪个环，一句说这一类该怎么处置）。两个投影各写一份的表现**不是**不一致 —— 是
**「同一个类别在两处各有一个名字，而两处各自都读得通」**：一条
``workstream_policy`` 在环那一侧写着 FAST（因为它改眼前这一批），在处置那一侧写着
「preview 后由 Member-A 批准为 policy 新版」，两条都成立。所以「同键」这件事必须有一个
执行器，否则漏掉的那一格读起来只是「这一类没有处置」，而它是**静默**的。

``marketops/domain/feedback.py`` 里已经有一条 import 期的形状守卫。这里判的是**那条
守卫真的在守东西**：守卫可以在一次重构里被顺手删掉，而删掉之后没有任何东西会响 ——
这个文件就是那个「会响」。

## 五格不许自动扩大

``PROTECTED_DIMENSIONS`` 是承重点 2 的那五个名字：``country`` / ``customer
definition`` / ``hard exclusion`` / ``claim`` / ``permissions``。它们在领域里只是
一个 frozenset，**没有任何行为**；真正的落点在 ``feedback_preview``（一条改动碰到
它们时进 ``not_usable``，而不是进 ``in_range``）。所以要判两件事：这一格与文档那五
个名字逐字相同，以及**每一个受保护的名字都是一个真实的策略格子** —— 一个拼错的
名字会让那一格静默地保护不住任何东西。
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from marketops.application.feedback import draft_candidate, draft_feedback, draft_patch
from marketops.domain.errors import ConfigError
from marketops.domain.feedback import (
    DISPOSITION,
    POLICY_CELLS,
    POLICY_PATCHABLE_KINDS,
    PROTECTED_CELL,
    PROTECTED_DIMENSIONS,
    RING_OF,
    SCOPE_ORDER,
    VERSION_PRODUCING_KINDS,
    WIDEST_SCOPE,
    CandidateState,
    FeedbackEvent,
    FeedbackKind,
    PolicyPatch,
    PolicyPatchState,
    Ring,
    RiskClass,
    Scope,
    ScopeKind,
    candidate_id_for,
    candidate_source,
    feedback_id_for,
    feedback_source,
    patch_id_for,
    protected_of,
    resolve_scope,
    ring_of,
)

AT = datetime(2026, 9, 29, 12, 0, tzinfo=timezone.utc)

#: 文档里那五个类别，逐字（``docs/PRODUCT_LINE_AND_SCENE_MODEL.md:185``）。
_THE_FIVE_KINDS: frozenset[str] = frozenset(
    {
        "fact_correction",
        "run_instruction",
        "workstream_policy",
        "experience_candidate",
        "business_definition",
    }
)

#: 作用域那七档，逐字（``docs/PRODUCT_LINE_AND_SCENE_MODEL.md:192``）：
#: 「反馈 scope 至少支持 ``object/run/workstream/segment/scenario/geo/workspace``；
#: 不能只有 ``global=true/false``」。
_THE_SEVEN_SCOPES: tuple[str, ...] = (
    "object",
    "run",
    "workstream",
    "segment",
    "scenario",
    "geo",
    "workspace",
)

#: 承重点 2 那五格，逐字。
_THE_FIVE_PROTECTED: frozenset[str] = frozenset(
    {"country", "customer_definition", "hard_exclusion", "claim", "permissions"}
)


def _event(**overrides: object) -> FeedbackEvent:
    """一条说得通的反馈 —— 只有被测的那一格由调用方改。"""
    fields: dict[str, object] = {
        "workspace": "ws-1",
        "principal": "claude-control",
        "idempotency_key": "key-1",
        "kind": FeedbackKind.WORKSTREAM_POLICY,
        "literal_feedback": "这句原话",
        "run_ref": "run-1",
        "at": AT,
    }
    fields.update(overrides)
    return draft_feedback(**fields)  # type: ignore[arg-type]


# ================================================================== 一、三个环
def test_the_five_kinds_are_the_ones_the_plan_names() -> None:
    """五个类别逐字 —— 少一个、多一个、拼错一个都在这里红。"""
    assert {kind.value for kind in FeedbackKind} == _THE_FIVE_KINDS


def test_every_kind_has_exactly_one_ring_and_one_disposition() -> None:
    """那两个投影**同键**，而这条守卫真的在守东西。

    没有这一条，``RING_OF`` 里漏掉一类之后 ``ring_of`` 会抛 ``KeyError``（那还好），
    而 ``DISPOSITION`` 里漏掉一类会让这一类**没有处置** —— 一个字都不会响，
    读的人只会以为文档没写。
    """
    assert set(RING_OF) == set(DISPOSITION) == set(FeedbackKind)


def test_every_disposition_is_a_sentence_not_a_placeholder() -> None:
    """处置那一栏是一句话，不是一个空串、也不是类别名本身。

    一张「有那一行、但那一行什么都没说」的表，与一张缺行的表在读者那里同样坏 ——
    而它在 ``assert set(...) == set(...)`` 那条判据上**全绿**。
    """
    for kind, disposition in DISPOSITION.items():
        assert disposition.strip(), f"{kind.value} 的处置是空的"
        assert disposition != kind.value, f"{kind.value} 的处置只是把类别名抄了一遍"


def test_the_ring_of_a_kind_goes_through_the_table_not_through_a_copy() -> None:
    """``ring_of`` 与 ``RING_OF`` 说的是同一件事。"""
    for kind in FeedbackKind:
        assert ring_of(kind) is RING_OF[kind]


def test_the_three_rings_are_the_ones_the_plan_names() -> None:
    assert {ring.value for ring in Ring} == {
        "fact_correction",
        "fast_loop",
        "slow_loop",
    }


def test_a_feedback_event_reports_the_ring_of_its_kind() -> None:
    for kind in FeedbackKind:
        event = _event(kind=kind, idempotency_key=f"key-{kind.value}")
        assert event.ring is RING_OF[kind]


def test_the_fast_ring_holds_two_different_dispositions_and_that_is_the_point() -> None:
    """快环里是**两件不一样的事**，而这一点是这一片最容易写错的一格。

    ``run_instruction`` 只改本 Run/下一 Run 的计划，**不产生任何版本**；
    ``workstream_policy`` 在预览与批准之后**成为一版 scoped policy**。把两者放在同一个
    环上，是因为快环与慢环的分界**不是「有没有版本」**—— 按那种分法
    ``workstream_policy`` 会被判成慢环，而它恰恰是「快速用于下一批」那句要的那条路。
    真正的分界是**有没有 replay/evaluation**。

    这条判据钉的就是那句话的**推论**：同一环上的两类，处置**不一样**。
    一条把「快环」整段折成同一句话的实现会在这里红。
    """
    fast = {kind for kind, ring in RING_OF.items() if ring is Ring.FAST}
    assert fast == {FeedbackKind.RUN_INSTRUCTION, FeedbackKind.WORKSTREAM_POLICY}
    assert DISPOSITION[FeedbackKind.RUN_INSTRUCTION] != DISPOSITION[
        FeedbackKind.WORKSTREAM_POLICY
    ], "快环两类被折成了同一句话 —— 快慢的分界被写成了「有没有版本」"


def test_the_patchable_kinds_are_the_version_producing_ones() -> None:
    """提得起补丁的那几类 = 会产生版本的那几类。"""
    assert POLICY_PATCHABLE_KINDS == VERSION_PRODUCING_KINDS
    assert VERSION_PRODUCING_KINDS == {
        FeedbackKind.WORKSTREAM_POLICY,
        FeedbackKind.EXPERIENCE_CANDIDATE,
        FeedbackKind.BUSINESS_DEFINITION,
    }


def test_workstream_policy_is_patchable_even_though_it_is_the_fast_ring() -> None:
    """**变异靶子**：``POLICY_PATCHABLE_KINDS`` 不许被写成「慢环那几类」。

    目录行把 C 类写成「preview 影响后由上级批准为 Workstream/Segment policy
    新版」，也就是说这一类**产出版本**，因此它提得起一份策略补丁 —— 哪怕它的环是
    ``fast_loop``。

    一条按 ``RING_OF[...] is Ring.SLOW`` 算出来的实现，在盘上看起来完全正常
    （三类照样提得起补丁），只是 ``workstream_policy`` 那条路静默消失 ——
    而它是「快速用于下一批」那句话唯一的落点。这条判据就是那个「会响」。
    """
    assert FeedbackKind.WORKSTREAM_POLICY in POLICY_PATCHABLE_KINDS
    assert RING_OF[FeedbackKind.WORKSTREAM_POLICY] is Ring.FAST
    #: 反方向：两个不产出版本的类别一个都不许在里面。
    for kind in (FeedbackKind.FACT_CORRECTION, FeedbackKind.RUN_INSTRUCTION):
        assert kind not in POLICY_PATCHABLE_KINDS


# ================================================================== 二、作用域七档
def test_the_scope_ladder_is_the_seven_named_ones_in_order() -> None:
    assert tuple(kind.value for kind in SCOPE_ORDER) == _THE_SEVEN_SCOPES
    assert SCOPE_ORDER[0] is ScopeKind.OBJECT
    assert SCOPE_ORDER[-1] is WIDEST_SCOPE is ScopeKind.WORKSPACE


def test_the_width_is_the_position_on_the_ladder() -> None:
    """``width`` 是这一档在梯子上的位置 —— 它此后被用来判「跨了几档」。"""
    widths = [Scope(kind=kind, ref="x").width for kind in SCOPE_ORDER]
    assert widths == list(range(len(SCOPE_ORDER)))


def test_naming_nothing_is_refused_and_never_widened() -> None:
    """**承重点 1 的负向判据：不给 scope 时得到的不是最宽，是一次拒绝。**

    「默认值的宽度就是这条判据的全部内容」。一个把缺省填成 ``workspace`` 的实现，
    在盘上看起来完全正常 —— 它只是**每一次都替调用方主张了一次全局**，
    而那条主张此后会被当成「有人说过要改全局」读回去。

    所以缺省必须是**拒绝**，而不是一个更宽的格子：一个填出来的缺省无论填哪一档都是
    一次没有人做过的主张。
    """
    with pytest.raises(ConfigError) as raised:
        resolve_scope()
    text = str(raised.value)
    assert "作用域" in text
    #: 拒绝里必须把可选的几档列全 —— 否则读的人只知道「不行」，不知道「那给什么」。
    for name in _THE_SEVEN_SCOPES:
        assert name in text, f"拒绝里没列出 {name!r} 这一档"


def test_the_narrowest_thing_actually_said_wins() -> None:
    """点名了好几个引用时，取**最窄**的那一个（不是最后一个、也不是最宽的）。"""
    #: 传参顺序按梯子从宽到窄，答案仍然是最窄的那个 —— 排除「取最后一个」的实现。
    assert resolve_scope(
        workstream_ref="ws-1", run_ref="run-1"
    ).kind is ScopeKind.RUN
    #: 反过来传一遍，答案不变 —— 排除「取第一个」的实现。
    assert resolve_scope(
        run_ref="run-1", workstream_ref="ws-1"
    ).kind is ScopeKind.RUN
    assert resolve_scope(target_ref="obj-1", workstream_ref="ws-1").kind is ScopeKind.OBJECT


def test_the_three_scope_kinds_with_a_ref_field_get_the_right_one() -> None:
    """三个有对应引用字段的档，各自落到**自己那一格**。

    这条判据存在是因为报错文案与字段名曾经分过家：一次把 ``target_ref`` 改回
    ``subject_ref`` 的重命名会让 ``object`` / ``run`` / ``workstream`` 三格全错，
    而三条错法各自都读得通。
    """
    assert resolve_scope(target_ref="obj-1").ref == "obj-1"
    assert resolve_scope(run_ref="run-1").ref == "run-1"
    assert resolve_scope(workstream_ref="ws-1").ref == "ws-1"
    assert resolve_scope(target_ref="obj-1").as_dict()["label"] == "object:obj-1"
    assert resolve_scope(run_ref="run-1").as_dict()["label"] == "run:run-1"


def test_a_named_kind_without_a_ref_is_refused() -> None:
    """只有类别没有引用 —— 拒，不填一个空串。

    一个空引用此后会被当成一条「落在无名之物上」的建议读回去，而它与「还没定」在盘上
    同形。写进盘的是哪一件，读的人分不出来。
    """
    for kind in SCOPE_ORDER:
        with pytest.raises(ConfigError):
            resolve_scope(scope_kind=kind)


def test_a_ref_without_a_kind_is_refused_at_the_scope_layer() -> None:
    """``scope_kind`` 给了、``scope_ref`` 没给：这一层不猜。"""
    with pytest.raises(ConfigError):
        resolve_scope(scope_kind=ScopeKind.SEGMENT)


def test_the_four_wide_kinds_are_only_reachable_by_name() -> None:
    """``segment`` / ``scenario`` / ``geo`` / ``workspace`` 没有对应的引用字段。

    它们只能由调用方**点名**给出 —— 这不是遗漏，是对的：一条反馈没有任何一格能
    自行证明自己作用在整个场景或整个地球上，所以那几档必须是一次显式主张。
    """
    for kind in (ScopeKind.SEGMENT, ScopeKind.SCENARIO, ScopeKind.GEO, ScopeKind.WORKSPACE):
        assert resolve_scope(scope_kind=kind, scope_ref="x").kind is kind


def test_a_feedback_without_a_scope_refuses_at_the_draft_layer() -> None:
    """领域那条拒绝**真的**在 ``draft_feedback`` 这条路上生效。"""
    with pytest.raises(ConfigError):
        draft_feedback(
            workspace="ws-1",
            principal="p",
            idempotency_key="k",
            kind=FeedbackKind.WORKSTREAM_POLICY,
            literal_feedback="说了句话",
            at=AT,
        )


def test_a_feedback_with_neither_words_nor_an_artifact_is_refused() -> None:
    """一句话都没有、也没有产物引用的记录，与「他说了一句听不清的话」同形。"""
    with pytest.raises(ValidationError) as raised:
        draft_feedback(
            workspace="ws-1",
            principal="p",
            idempotency_key="k",
            kind=FeedbackKind.RUN_INSTRUCTION,
            run_ref="run-1",
            at=AT,
        )
    assert "什么都没说" in str(raised.value)


# ================================================================== 三、五格不许自动扩大
def test_the_protected_five_are_exactly_the_five_the_plan_names() -> None:
    assert PROTECTED_DIMENSIONS == _THE_FIVE_PROTECTED


def test_every_protected_name_is_a_real_policy_cell() -> None:
    """**每一个受保护的名字都必须是一个真实的格子。**

    一个拼错的名字（``hard_exclusions`` 多一个 s）保护不住任何东西：判据收的是一串
    名字，而那一格会静默地落进「没人保护」那一侧。这条判据就是那个「会响」。
    """
    assert PROTECTED_DIMENSIONS <= set(POLICY_CELLS)
    assert set(PROTECTED_CELL) == set(PROTECTED_DIMENSIONS)


def test_the_two_protected_readings_stay_in_step() -> None:
    """``PROTECTED_DIMENSIONS`` 与 ``PROTECTED_CELL`` 同键 —— 两份说法，一处定义。"""
    assert set(PROTECTED_CELL) == set(PROTECTED_DIMENSIONS)


def test_protected_of_reports_only_the_names_it_knows() -> None:
    """一个不认识的格子名**不算**受保护 —— 它由调用方另报（见 ``not_usable``）。

    顺序取**表里那一个**（``POLICY_CELLS``），不取入参的顺序：撞到哪几格是从两处
    汇起来的（调用方给的名字、以及反馈自己说的那一格），按入参顺序报会让同一件事
    在两条调用路径上给出两个排列 —— 而两条排列各自都是对的，所以没有一处会响。
    """
    assert protected_of(["country", "budget", "zzz"]) == ("country",)
    assert protected_of(["budget", "refresh"]) == ()
    #: 入参倒序，出来的仍然是表里那一个顺序。
    assert protected_of(["permissions", "claim"]) == ("claim", "permissions")


def test_every_policy_cell_is_either_protected_or_has_a_reading() -> None:
    """十一个格子一个都不许掉在地上。

    受保护的五格走 ``PROTECTED_CELL``，其余六格必须各自有一条「改它会动到哪一份
    声明、拿哪条读法去看」的说明。少了任何一条，那一格在影响预览里会变成一句
    「答不上来」—— 而它其实答得上。
    """
    from marketops.application.feedback_preview import CELL_READING

    assert set(CELL_READING) == set(POLICY_CELLS) - PROTECTED_DIMENSIONS
    assert not (set(CELL_READING) & set(PROTECTED_CELL))


# ================================================================== 四、幂等与内容寻址
def test_the_feedback_id_is_content_addressed_on_three_things() -> None:
    """``feedback_id`` 由 ``(工作区, principal, 幂等键)`` 算出来 —— 三格各改一格都换 id。

    与 ``feedback_event`` 上那条 ``UNIQUE (workspace, principal, idempotency_key)``
    **是同一件事的两种写法**：一条幂等键的含义是「这个人在这件事上的这一次提交」，
    跨工作区或跨主体都不再是同一次提交。
    """
    base = feedback_id_for(workspace="ws-1", principal="p", idempotency_key="k")
    assert base == feedback_id_for(workspace="ws-1", principal="p", idempotency_key="k")
    assert base != feedback_id_for(workspace="ws-2", principal="p", idempotency_key="k")
    assert base != feedback_id_for(workspace="ws-1", principal="q", idempotency_key="k")
    assert base != feedback_id_for(workspace="ws-1", principal="p", idempotency_key="j")


def test_the_patch_id_ignores_which_cells_it_changes() -> None:
    """改了「改哪几格」是**同一份提案的另一次修订**，不是另一份提案。

    原像里放 ``changes`` 的表现是：每一次改主意都新开一个 id，而旧的那份看起来像
    另一件事 —— 于是「这份提案改过几次主意」这件事没有答案。
    """
    first = patch_id_for(workspace="ws-1", source_ref="feedback:fb-1", target_version=3)
    second = patch_id_for(workspace="ws-1", source_ref="feedback:fb-1", target_version=3)
    assert first == second
    assert first != patch_id_for(
        workspace="ws-1", source_ref="feedback:fb-1", target_version=4
    )
    assert first != patch_id_for(
        workspace="ws-1", source_ref="feedback:fb-2", target_version=3
    )


def test_the_candidate_id_ignores_where_it_generalises_to() -> None:
    """同上：``generalises`` 不在原像里，「这个观察被提过几次」才答得出来。"""
    first = candidate_id_for(
        workspace="ws-1", source_feedback_ids=("fb-1",), observation="同一句观察"
    )
    assert first == candidate_id_for(
        workspace="ws-1", source_feedback_ids=("fb-1",), observation="同一句观察"
    )
    assert first != candidate_id_for(
        workspace="ws-1", source_feedback_ids=("fb-2",), observation="同一句观察"
    )


def test_the_two_source_spellings_have_one_definition_each() -> None:
    """``feedback:<id>`` 与 ``candidate:<id>@<rev>`` 各只有一处定义。"""
    assert feedback_source("fb-1") == "feedback:fb-1"
    assert candidate_source("ec-1", 2) == "candidate:ec-1@2"


def test_a_feedback_carries_its_principal_and_key_on_the_row() -> None:
    """两格都在行上 —— 它们是那条幂等约束的两半，不是可选的注解。"""
    event = _event(principal="claude-control")
    assert event.principal == "claude-control"
    assert event.idempotency_key == "key-1"


# ================================================================== 五、提案不是批准
def test_a_patch_has_no_approved_state_at_all() -> None:
    """**承重点 3 的结构面：这一层没有第二条批准路径 —— 它连那一格都没有。**

    判据不是「代码里没写 approved」，是**类型上没有这个成员**。写代码的人可以在
    任何地方加一个 ``if state == "approved"``，而它今天无处可来；反过来，一个
    ``PolicyPatchState.APPROVED`` 成员会让那条路**存在**，此后只需要一处赋值。

    所以这条判据的形状是：那个名字在这里**找不到**。
    """
    assert {state.value for state in PolicyPatchState} == {
        "proposed",
        "awaiting_approval",
    }
    assert not hasattr(PolicyPatchState, "APPROVED")


def test_a_candidate_state_has_no_approved_member_either() -> None:
    """候选那一侧同理 —— 「候选不是批准」是一个类型事实，不是一句注释。"""
    assert {state.value for state in CandidateState} == {
        "proposed",
        "superseded",
        "withdrawn",
    }
    assert not hasattr(CandidateState, "APPROVED")


def test_the_patch_state_follows_review_case_id_and_only_it() -> None:
    """挂了案 ⇒ ``awaiting_approval``；没挂 ⇒ ``proposed``。**没有第三种。**

    一个挂上了案却还写着「还没归案」的补丁，会让「它现在在等谁」有两个答案 ——
    而两个答案各自都读得通。
    """
    loose = draft_patch(
        workspace="ws-1",
        source_ref="feedback:fb-1",
        changes=("budget",),
        target_version=3,
        monitoring="看每周的预算消耗",
        rollback="把它改回上一版的数额",
        risk=RiskClass.LOW,
        rationale="因为这一批的额度用不完",
        proposed_by="claude-control",
        at=AT,
    )
    assert loose.state is PolicyPatchState.PROPOSED
    assert loose.review_case_id is None

    attached = draft_patch(
        workspace="ws-1",
        source_ref="feedback:fb-1",
        changes=("budget",),
        target_version=3,
        monitoring="看每周的预算消耗",
        rollback="把它改回上一版的数额",
        risk=RiskClass.LOW,
        rationale="因为这一批的额度用不完",
        proposed_by="claude-control",
        review_case_id="case-1",
        at=AT,
    )
    assert attached.state is PolicyPatchState.AWAITING_APPROVAL
    #: 挂没挂案不改变它是哪一份提案 —— id 只由 (工作区, 来源, 目标版本) 决定。
    assert attached.patch_id == loose.patch_id


def test_a_candidate_cannot_generalise_to_the_whole_workspace() -> None:
    """一条想推广到整个工作区的候选，是慢环里一步**做不到**的事 —— 那要一个**人**。

    这一条在领域里，而不是在处理器里：处理器那一侧的分支只挡得住经工具进来的调用，
    而这一条是**类型**上的。
    """
    with pytest.raises(ValidationError):
        draft_candidate(
            workspace="ws-1",
            observation="这批来源里有一类总是抓不到",
            generalises=Scope(kind=ScopeKind.WORKSPACE, ref="ws-1"),
            risk=RiskClass.LOW,
            proposed_by="claude-control",
            at=AT,
        )


def test_a_candidate_without_a_source_feedback_does_not_exist() -> None:
    """**候选是从反馈里看出来的** —— 没有来源的候选是一个想法，而想法没有入口。

    一条能无中生有的候选，此后会被当成「有依据的推广」读回去：它进的是慢环，
    而慢环的出口是一版策略。
    """
    with pytest.raises(ValidationError) as raised:
        draft_candidate(
            workspace="ws-1",
            observation="这批来源里有一类总是抓不到",
            generalises=Scope(kind=ScopeKind.SEGMENT, ref="seg-1"),
            risk=RiskClass.MEDIUM,
            proposed_by="claude-control",
            at=AT,
        )
    assert "没有来源反馈" in str(raised.value)


def test_a_candidate_drafted_through_the_application_layer_is_always_proposed() -> None:
    """``draft_candidate`` 里没有第二个可能的初值。"""
    candidate = draft_candidate(
        workspace="ws-1",
        observation="这批来源里有一类总是抓不到",
        generalises=Scope(kind=ScopeKind.SEGMENT, ref="seg-1"),
        risk=RiskClass.MEDIUM,
        proposed_by="claude-control",
        source_feedback_ids=("fb-1",),
        at=AT,
    )
    assert candidate.state is CandidateState.PROPOSED
    assert candidate.revision == 1


def test_a_patch_is_never_constructed_in_an_approved_state() -> None:
    """穷举一遍：``draft_patch`` 造出来的每一份，状态都只可能是那两档。"""
    for case_id in (None, "case-1"):
        patch = draft_patch(
            workspace="ws-1",
            source_ref="feedback:fb-1",
            changes=("refresh",),
            target_version=4,
            monitoring="看下一次复核的间隔",
            rollback="改回复核节奏的上一版",
            risk=RiskClass.LOW,
            rationale="因为这一批的复核节奏太密",
            proposed_by="claude-control",
            review_case_id=case_id,
            at=AT,
        )
        assert isinstance(patch, PolicyPatch)
        assert patch.state in {PolicyPatchState.PROPOSED, PolicyPatchState.AWAITING_APPROVAL}
