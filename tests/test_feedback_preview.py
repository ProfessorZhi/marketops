"""``preview_feedback_impact`` 的判据：**批准之前那一份读数**。

## 这一份读数里承重的三格

1. ``applied_at`` 的**缺省**。目录行逐字「自然语言永远不改全局策略」，而
   ``PRODUCT_LINE_AND_SCENE_MODEL.md:192`` 逐字「默认取**最窄合理 scope**，扩大
   scope 需要影响预览或批准」。缺省不是排版：一个把缺省填成工作区的实现，在盘上
   看起来只是「影响面更大」，而它此后会被当成一条**有人主张过的全局规则**读回去。
   所以 ``test_the_applied_scope_defaults_to_what_the_feedback_itself_said`` 是这
   个文件里最要紧的一条。

2. 两张清单**分开**：``not_usable``（有理由的做不到）与 ``not_reported``（这一层
   答不上来）。目录行逐字：「最后那一格是承重的：它把「做不到」与「没人想过」分开」。
   把前者折进后者的表现是：一条**能判的**边界读起来只是「信息不全」，而读的人会
   去找那份信息 —— 它根本不存在。

3. ``next_action`` 是五档，不是一个布尔。它是**下一步该走哪个动词**，所以
   ``workstream_policy`` 与 ``run_instruction`` 虽然同在快环上，答案必须不同。

## 零 provider 是构造

这一条不住在这里，住在 ``tests/test_mcp_feedback_is_offline.py``（它算 import 闭包）。
本文件判的是**这一层不读时钟**：``at`` 是签名上的必填参数，不是它自己去取的。
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from marketops.application.feedback import draft_feedback
from marketops.application.feedback_preview import (
    ImpactAction,
    feedback_impact,
    widening_between,
)
from marketops.domain.feedback import (
    POLICY_CELLS,
    PROTECTED_DIMENSIONS,
    FeedbackKind,
    Ring,
    Scope,
    ScopeKind,
)

AT = datetime(2026, 9, 29, 12, 0, tzinfo=timezone.utc)

#: 一条作用域窄到 ``run`` 的反馈 —— 这个文件里大多数判据拿它当主语。
_NARROW = Scope(kind=ScopeKind.RUN, ref="run-1")

#: 梯子最宽那一格。
_WIDE = Scope(kind=ScopeKind.WORKSPACE, ref="ws-1")


def _event(kind: FeedbackKind = FeedbackKind.WORKSTREAM_POLICY, **overrides: object):
    fields: dict[str, object] = {
        "workspace": "ws-1",
        "principal": "claude-control",
        "idempotency_key": f"key-{kind.value}",
        "kind": kind,
        "literal_feedback": "这一批的来源计划配得太宽了",
        "run_ref": "run-1",
        "at": AT,
    }
    fields.update(overrides)
    return draft_feedback(**fields)  # type: ignore[arg-type]


def _names(rows) -> tuple[str, ...]:
    """两张清单里那几项的**名字**。

    读的是那个 dataclass 的属性，不是 ``as_dict`` 出来的 dict —— 这两条路是同一个
    对象的两出口，而判据要判的是**这一层给出的那一份**（``as_dict`` 另外有它自己的
    形状判据，见 ``test_the_report_and_the_reading_are_the_same_object``）。
    """
    return tuple(row.item for row in rows)


def _reasons(rows) -> dict[str, str]:
    """名字 → 原因。同一条判据里两格要一起看时用。"""
    return {row.item: row.reason for row in rows}


# ================================================================== 一、作用域
def test_the_applied_scope_defaults_to_what_the_feedback_itself_said() -> None:
    """**承重点 1 在这里的落点：不给 applied_at 时，得到的是反馈自己说的那一格。**

    不是工作区、不是「最窄那一档」这个抽象值、也不是空。缺省值必须**是那条反馈自己
    主张过的那一格** —— 一个填出来的缺省无论填哪一档都是一次没有人做过的主张，
    而这一格里唯一有人主张过的值就在那行反馈上。

    变异靶子：把缺省改成 ``Scope(kind=WORKSPACE, ref=event.workspace)``。改了之后
    下面三条断言全红，而盘上一切照旧 —— 只有这条判据会响。
    """
    event = _event()
    assert event.proposed_scope == _NARROW

    impact = feedback_impact(event=event, at=AT)
    assert impact.applied_at == event.proposed_scope
    assert impact.applied_at == _NARROW
    assert impact.widened is False
    assert impact.widening == ()
    assert impact.as_dict()["wider_than_workspace"] is False


def test_a_narrower_feedback_never_gets_a_wider_default() -> None:
    """穷举一层：无论那条反馈自己说在哪一档，缺省都**照抄它**，不放大。"""
    for ref_field, kind in (
        ("target_ref", ScopeKind.OBJECT),
        ("run_ref", ScopeKind.RUN),
        ("workstream_ref", ScopeKind.WORKSTREAM),
    ):
        #: 先把 ``run_ref`` 压掉，再让这一轮的 ``ref_field`` 覆盖上去 —— 否则
        #: ``ref_field == "run_ref"`` 那一轮会拿到「同一个关键字给了两次」。
        fields: dict[str, object] = {
            "kind": FeedbackKind.EXPERIENCE_CANDIDATE,
            "run_ref": None,
        }
        fields[ref_field] = "x-1"
        event = _event(**fields)  # type: ignore[arg-type]
        assert event.proposed_scope.kind is kind, (
            f"这条反馈自己说在 {kind.value}，而它说的是 {event.proposed_scope.kind.value}"
        )
        impact = feedback_impact(event=event, at=AT)
        assert impact.applied_at.kind is kind
        assert impact.applied_at == event.proposed_scope


def test_widening_is_named_rung_by_rung_not_as_a_boolean() -> None:
    """扩大是**逐档点名**的，不是一个布尔。

    「跨了几档」这句话要能回答「从 Run 到工作区中间跨了哪几档」—— 一个布尔只说
    「跨了」，而读的人要知道的是跨过的那几档里有没有 ``scenario`` / ``geo`` 这种
    会碰业务边界的东西。
    """
    impact = feedback_impact(event=_event(), at=AT, applied_at=_WIDE)
    assert impact.widened is True
    assert impact.widening == (
        ScopeKind.WORKSTREAM,
        ScopeKind.SEGMENT,
        ScopeKind.SCENARIO,
        ScopeKind.GEO,
        ScopeKind.WORKSPACE,
    )
    assert impact.as_dict()["widening"] == [
        "workstream",
        "segment",
        "scenario",
        "geo",
        "workspace",
    ]


def test_widening_between_is_monotone() -> None:
    """收窄不是扩大；同一档也不是。**「哪边宽」是梯子上的位置，不是两个集合的差。**"""
    assert widening_between(_WIDE, _NARROW) == ()
    assert widening_between(_NARROW, _NARROW) == ()
    assert widening_between(_NARROW, _WIDE) != ()


def test_reaching_the_workspace_rung_is_reported_on_its_own_field() -> None:
    """梯子最宽那一档有一个自己的字段。

    它的名字（``wider_than_workspace``）在字面上有点别扭 —— 工作区并不比工作区更宽。
    它回答的是「这一份读数落没落到梯子最宽的那一格上」，而这一格是承重的：落到它上面
    的那份读数，此后就是一条**全局**规则。这里按它**实际**的语义判，不按名字的读法判。
    """
    reach = feedback_impact(event=_event(), at=AT, applied_at=_WIDE).as_dict()
    assert reach["wider_than_workspace"] is True
    assert reach["applied_at"]["kind"] == "workspace"
    assert feedback_impact(event=_event(), at=AT).as_dict()["wider_than_workspace"] is False


# ================================================================== 二、两张清单
def test_the_subject_of_a_reading_cannot_be_omitted() -> None:
    """``applied_at`` 可以被省略，但 ``event`` 省略不了 —— 而那条事件自己就有作用域。

    这条判据钉的是一个**边界**：这一层不替调用方编一个作用域，它只能拿走那条反馈
    已经主张过的那一格。所以「不知道作用在哪」这件事在这里**无法表达** —— 它在上游
    （``draft_feedback`` / ``resolve_scope``）就已经是一次拒绝了。少了这条前置，
    下面那条「缺省照抄」可以靠一个连主语都没有的空壳达成。
    """
    with pytest.raises(TypeError):
        feedback_impact(at=AT)  # type: ignore[call-arg]


def test_self_approval_is_refused_and_the_other_person_is_named() -> None:
    """**承重点 3 在这里的落点**：会产出版本的那三类，逐条写明「批准不在这里」。

    三条一起判：它点名了**既有的**那个函数、点名了角色、点名了**另一个人**。
    少任何一条，读的人都会以为下一步是「把这一版生效」。
    """
    for kind in (
        FeedbackKind.WORKSTREAM_POLICY,
        FeedbackKind.EXPERIENCE_CANDIDATE,
        FeedbackKind.BUSINESS_DEFINITION,
    ):
        impact = feedback_impact(event=_event(kind=kind), at=AT)
        rows = _reasons(impact.not_usable)
        assert "自己把这一版生效" in rows, f"{kind.value} 没有把「自己生效」标成做不到"
        reason = rows["自己把这一版生效"]
        assert "approve_policy_change" in reason
        assert "_AUTHORIZE" in reason
        assert "另一个人" in reason
        #: 而它一定不在「会重判的对象」清单里 —— 那两格里没有一条是「批准」。
        assert not any("批准" in row.judged_by for row in impact.in_range)


def test_the_workspace_rung_says_why_it_is_not_reachable_here() -> None:
    """**「自然语言永远不改全局策略」这句话在这里有一个落点。**

    落到工作区那一档时，``not_usable`` 要有一条点名那句话、并给出真正的下一步
    （一个人去开一件复核案）—— 而不是只报一个更宽的 ``applied_at``。
    """
    impact = feedback_impact(event=_event(), at=AT, applied_at=_WIDE)
    rows = _reasons(impact.not_usable)
    assert "在工作区这一层生效" in rows
    reason = rows["在工作区这一层生效"]
    assert "自然语言永远不改全局策略" in reason
    assert "approve_policy_change" in reason


def test_each_disposition_that_has_another_road_names_it() -> None:
    """两条**别的路**的类别各自点名自己那条路，而不是共用一句「不行」。"""
    fact = _names(
        feedback_impact(event=_event(kind=FeedbackKind.FACT_CORRECTION), at=AT).not_usable
    )
    assert "改用事实更正那条路" in fact

    run = _names(
        feedback_impact(event=_event(kind=FeedbackKind.RUN_INSTRUCTION), at=AT).not_usable
    )
    assert "改用眼前这一轮那条路" in run


def test_the_two_lists_do_not_hold_the_same_kind_of_sentence() -> None:
    """``not_usable`` 说的是「做不到」，``not_reported`` 说的是「答不上来」。

    判据的形状：**同一件事不许同时出现在两张表里**。一条同时出现在两处的边界读起来
    既像「它做不到」又像「它没查」，而两条处置相反（换一条路 / 去接线）。
    """
    impact = feedback_impact(event=_event(), at=AT, changes=("country",))
    unusable = set(_names(impact.not_usable))
    unreported = set(_names(impact.not_reported))
    assert not (unusable & unreported), (
        f"这些条目同时出现在两张清单里：{sorted(unusable & unreported)}"
    )
    #: 而且两张表都非空 —— 一张恒空的表会让上面那句话在真空上成立。
    assert unusable and unreported


def test_the_facts_layer_says_out_loud_that_it_cannot_count() -> None:
    """「射程里到底有几个 Run」这件事，这一层**明说它数不出来**。

    这是一个**答案**，不是一个缺口：目录行要求把「答不上来」与「没人想过」分开，
    而分开的方式是把答不上来的那几项逐条点名、连原因一起给。一条不点名的读数会让
    「射程里零个 Run」与「没查」在报告上同形。
    """
    impact = feedback_impact(event=_event(), at=AT)
    rows = _reasons(impact.not_reported)
    assert "射程里到底有几个 Run、几份草稿" in rows
    #: 原因必须说清**缺的是哪一样读法**，而不是一句「暂不支持」。
    assert "端口" in rows["射程里到底有几个 Run、几份草稿"]


def test_an_empty_scope_of_change_says_it_is_empty_not_that_nothing_changes() -> None:
    """没点名 ``changes`` 时，读数**不许**读成「它什么都不改」。

    空与「它什么都不改」同形 —— 而后者是一条结论。所以这一条也进 ``not_reported``。
    """
    quiet = feedback_impact(event=_event(), at=AT)
    #: 判的是**策略那一轴**为空，不是 ``in_range`` 整个为空：``in_range`` 还装着
    #: 「改了要重看哪些东西」，而失效的那一格（那条 Run）与点名了几格无关。
    assert not any(row.item.startswith("策略格子 ") for row in quiet.in_range)
    assert any(
        "没有点名任何策略格子" in item for item in _names(quiet.not_reported)
    ), "空射程没有被说明，读的人会把它读成「什么都不改」"


# ================================================================== 三、受保护那几格
def test_a_change_to_a_protected_cell_is_not_usable_and_names_every_one_of_them() -> None:
    """**承重点 2 在这里的落点：五格一个都不许自动扩大。**

    判两件事：那几格进的是 ``not_usable``（不是 ``in_range``），以及报错里**把撞到的
    格子名一个不少地列出来** —— 只报一个会让改了四格的人以为只有一格有问题。
    """
    impact = feedback_impact(event=_event(), at=AT, changes=("country", "customer_definition"))
    unusable = _names(impact.not_usable)
    hit = [item for item in unusable if item.startswith("改受保护的那几格")]
    assert len(hit) == 1, f"受保护那几格没有被单独报出来：{unusable}"
    assert "country" in hit[0] and "customer_definition" in hit[0]
    #: 它不进「会重判的对象」那一张表 —— 那一张表说的是「改了它要重看哪些东西」。
    assert not any("country" in row.item for row in impact.in_range)


def test_every_protected_cell_is_refused_when_changed() -> None:
    """**穷举**那五格 —— 一格的漏网就是一个静默的自动扩大。"""
    for cell in sorted(PROTECTED_DIMENSIONS):
        impact = feedback_impact(event=_event(), at=AT, changes=(cell,))
        assert any(
            row.item == f"改受保护的那几格：['{cell}']" for row in impact.not_usable
        ), f"{cell} 被改了，而这一层没有把它标成做不到"


def test_a_non_protected_cell_is_in_range_and_says_which_reader_to_use() -> None:
    """不受保护的那几格**能改**，而读数要说清拿哪一条读法去看 —— 不是一句「可以」。"""
    impact = feedback_impact(event=_event(), at=AT, changes=("budget", "scenario"))
    judged = {row.item: row.judged_by for row in impact.in_range}
    assert any(item == "策略格子 budget" for item in judged)
    assert any(item == "策略格子 scenario" for item in judged)
    #: 每一条都要点名一个**真实的读法**，而不是一句「自己去看」。
    for item, judged_by in judged.items():
        if item.startswith("策略格子 "):
            assert "get_scene_impact" in judged_by or "::" in judged_by


def test_a_cell_name_nobody_recognises_is_reported_as_such() -> None:
    """一个不认识的格子名既不受保护、也不是「能改」—— 它是**第三件事**。

    它进 ``not_usable``（这一层不能改一个不存在的格子），而报的是「这几格不认识」，
    不是「这几格受保护」。两者混起来会让一个拼错的格子名看起来像一条业务边界。
    """
    impact = feedback_impact(event=_event(), at=AT, changes=("zzz", "country"))
    unusable = _names(impact.not_usable)
    assert any(item.startswith("改这几格") and "zzz" in item for item in unusable)
    assert any(item.startswith("改受保护的那几格") for item in unusable)
    #: 「zzz」不许出现在受保护那一句里。
    protected_line = next(
        item for item in unusable if item.startswith("改受保护的那几格")
    )
    assert "zzz" not in protected_line


def test_every_policy_cell_is_answered_by_exactly_one_of_the_three_ways() -> None:
    """十一个格子逐个过一遍：受保护 / 有读法 / 不认识，**三选一，不重不漏**。"""
    from marketops.application.feedback_preview import CELL_READING

    for cell in POLICY_CELLS:
        impact = feedback_impact(event=_event(), at=AT, changes=(cell,))
        in_range = any(row.item == f"策略格子 {cell}" for row in impact.in_range)
        if cell in PROTECTED_DIMENSIONS:
            assert not in_range, f"{cell} 既受保护又被判成可改"
            assert f"改受保护的那几格：['{cell}']" in _names(impact.not_usable)
        else:
            assert cell in CELL_READING, f"{cell} 没有读法"
            assert in_range, f"{cell} 有读法却没被判成可改"


# ================================================================== 四、下一步是哪个动词
def test_the_next_action_is_one_of_five_and_follows_the_kind() -> None:
    """五档动词，逐类钉住。**它不是「有没有版本」的函数。**"""
    expected = {
        FeedbackKind.FACT_CORRECTION: ImpactAction.FACT_PATH,
        FeedbackKind.RUN_INSTRUCTION: ImpactAction.THIS_ROUND_ONLY,
        FeedbackKind.WORKSTREAM_POLICY: ImpactAction.PATCH_THEN_ANOTHER_PERSON,
        FeedbackKind.EXPERIENCE_CANDIDATE: ImpactAction.CANDIDATE_THEN_PATCH,
        FeedbackKind.BUSINESS_DEFINITION: ImpactAction.PATCH_THEN_ANOTHER_PERSON,
    }
    assert len(set(expected.values())) == 4
    for kind, action in expected.items():
        impact = feedback_impact(event=_event(kind=kind), at=AT)
        assert impact.next_action is action, (
            f"{kind.value} 的下一步报成了 {impact.next_action.value!r}，"
            f"而它该是 {action.value!r}"
        )


def test_the_next_action_does_not_turn_proposal_into_approval() -> None:
    """**会产出版本的三类，下一步里都含有「另一个人」** —— 一个都没有「生效」。"""
    for kind in (
        FeedbackKind.WORKSTREAM_POLICY,
        FeedbackKind.EXPERIENCE_CANDIDATE,
        FeedbackKind.BUSINESS_DEFINITION,
    ):
        action = feedback_impact(event=_event(kind=kind), at=AT).next_action
        assert action in {
            ImpactAction.PATCH_THEN_ANOTHER_PERSON,
            ImpactAction.CANDIDATE_THEN_PATCH,
        }
        assert "another_person" in action.value or "patch" in action.value
        assert "approve" not in action.value


def test_the_subject_is_the_feedback_itself() -> None:
    """这一份读数的主语是**那一条反馈**，不是那个候选项或那一版声明。"""
    event = _event()
    impact = feedback_impact(event=event, at=AT)
    assert impact.subject == f"feedback:{event.feedback_id}"
    assert impact.as_dict()["subject"] == impact.subject
    assert impact.feedback_id == event.feedback_id


def test_the_ring_is_reported_because_the_disposition_depends_on_it() -> None:
    """读数里带环，因为「下一步」在两类之间是不同的 —— 而两类同在快环上。"""
    fast = feedback_impact(event=_event(kind=FeedbackKind.RUN_INSTRUCTION), at=AT)
    also_fast = feedback_impact(event=_event(kind=FeedbackKind.WORKSTREAM_POLICY), at=AT)
    assert fast.ring is also_fast.ring is Ring.FAST
    assert fast.next_action is not also_fast.next_action


def test_the_preview_does_not_read_a_clock_of_its_own() -> None:
    """``at`` 是签名上的**必填**参数 —— 这一层不去取当前时刻。

    它一份读数里有好几处判定（跨了几档、哪几格受保护），各读一次时钟会让这些判定
    在跨秒的那一次运行里自相矛盾 —— 而那种不一致只在运行到那一秒时才出现。
    """
    import inspect

    signature = inspect.signature(feedback_impact)
    assert "at" in signature.parameters
    assert signature.parameters["at"].default is inspect.Parameter.empty
