"""Scene 的两个问题：**哪一版在生效**，以及**换一版什么要重做**。

本文件守三件事，每一件都对应一种「报告上看起来一切正常」的失败：

1. **三个版本号各自回答一个问题。** ``latest_version`` 是作者写到第几版，
   ``effective_version`` 是哪一版在生效。合并它们之后，一次编辑就自动生效 ——
   在途的运行会在一半的时候换掉规则，而报告上只是「版本号变大了」。
2. **状态机只有一张表。** 「归档是终态」「暂停必须写理由」不是注释，是
   没有出边的记录与构造期的拒绝。少了这些，PAUSED 与 ARCHIVED 在代码里
   是同一格，而两者的处置完全不同（一个是暂时不看，一个是不再经营）。
3. **影响面要说得全。** 换一版必须回答哪些结论作废、哪些不用动。
   ``reusable`` 是**全部** :class:`ImpactScope` 减去前两类，不是「没提到的那些」——
   少了这一半，读的人无法判断「这次改动没碰到的面」是哪几个。

还有一个与「少写一版」无关的边界：``frozen_sha256`` 是授权门与 wave manifest
绑的那个值。它必须由**内容**决定，而不是由「这是哪个对象」决定 ——
否则重新装载一遍规则就会让所有在途工作被告知「规则变了」。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from marketops.domain.enums import BudgetLine
from marketops.domain.maintenance import ALL_ASPECTS, RefreshPlan, RefreshRule
from marketops.domain.outreach import OutreachPolicyDecl
from marketops.domain.scene import (
    SCENE_IMPACT_RULES,
    SCENE_TRANSITIONS,
    ImpactScope,
    PauseReason,
    SceneBudgetLine,
    SceneImpact,
    SceneRecord,
    SceneState,
    SceneStateError,
    SceneVersion,
    scene_impact,
)

NOW = datetime(2026, 9, 22, 12, 0, tzinfo=timezone.utc)
LATER = NOW + timedelta(minutes=1)
SCENE_ID = "probe-scene"
WORKSPACE = "probe-workspace"
APPROVER = "probe-approver"


# --- 夹具 -------------------------------------------------------------------


def _refresh(*, after_days: int = 90, calibrated: bool = False) -> RefreshPlan:
    return RefreshPlan(
        rules=tuple(
            RefreshRule(aspect=aspect, after_days=after_days, calibrated=calibrated)
            for aspect in ALL_ASPECTS
        )
    )


def _version(**overrides) -> SceneVersion:
    """一版 Scene。``supersedes`` 跟着 ``version`` 走 —— 两者由同一条规则绑定，
    每个调用点各写一遍只会让人以为它们可以不一致。
    """
    version = overrides.get("version", 1)
    base = dict(
        scene_id=SCENE_ID,
        workspace=WORKSPACE,
        version=version,
        region=("north",),
        scenario="probe-scenario",
        product="probe-product",
        admissions=("probe-admitted",),
        exclusions=("probe-excluded",),
        role_policy="probe-policy",
        source_plan=("probe-source",),
        budget=(SceneBudgetLine(line=BudgetLine.SEARCH, ceiling=100),),
        refresh=_refresh(),
        outreach=OutreachPolicyDecl.uncalibrated_default(),
        profile_id="probe-profile",
        profile_version="probe-v3",
        created_at=NOW,
        supersedes=None if version == 1 else version - 1,
    )
    base.update(overrides)
    return SceneVersion(**base)


def _record(**overrides) -> SceneRecord:
    base = dict(
        scene_id=SCENE_ID,
        workspace=WORKSPACE,
        state=SceneState.ACTIVE,
        latest_version=1,
        effective_version=1,
        created_at=NOW,
        updated_at=NOW,
    )
    base.update(overrides)
    return SceneRecord(**base)


def _record_in(state: SceneState) -> SceneRecord:
    """某个状态下一条**能存在**的记录 —— 状态机只能从真实存在的记录出发。

    DRAFT 不带生效版本、PAUSED 带理由：这两条构造期就拦住了，
    所以起点不能是「随便造一个」。
    """
    if state is SceneState.DRAFT:
        return _record(state=state, effective_version=None)
    if state is SceneState.PAUSED:
        return _record(state=state, pause_reason=PauseReason.OPERATOR)
    return _record(state=state)


def _impact(changes: dict) -> SceneImpact:
    """第 1 版与「同样内容 + 指定改动」的第 2 版之间的影响面。"""
    return scene_impact(_version(), _version(version=2, **changes))


#: 影响面表里每个字段的一个替代值。这张表的键与 ``SCENE_IMPACT_RULES`` 的键
#: 必须一一对应 —— 表里出现了新字段而这里没有，分区测试会先红（见那条测试）。
RULE_FIELD_REWRITES: dict[str, object] = {
    "region": ("south",),
    "scenario": "other-scenario",
    "product": "other-product",
    "admissions": ("probe-admitted", "probe-also-admitted"),
    "exclusions": ("probe-excluded", "probe-barred"),
    "role_policy": "other-policy",
    "source_plan": ("probe-source", "probe-other-source"),
    "profile_id": "other-profile",
    "profile_version": "probe-v4",
}

#: ``SceneVersion`` 上每一个**绑定**字段各自的一个替代值。
#: ``note`` / ``supersedes`` / ``created_at`` 不在其中 —— 它们不是绑定。
BOUND_FIELDS: dict[str, object] = {
    "scene_id": "other-scene",
    "workspace": "other-workspace",
    "version": 2,
    **RULE_FIELD_REWRITES,
    "budget": (SceneBudgetLine(line=BudgetLine.SEARCH, ceiling=101),),
    "refresh": _refresh(after_days=91),
}


# --- 一版 Scene 绑定了什么 ----------------------------------------------------


@pytest.mark.parametrize("field", ["scene_id", "workspace"])
def test_an_id_that_would_be_rewritten_on_disk_is_refused(field: str) -> None:
    """需要规范化的 id 与另一个 id 会落到同一个文件上 —— 两个场景静默合并。

    拒绝而不是替调用方改写：一个会改写的实现把一次错误变成了两个 id 的合并，
    而台账上只显示「证据更多了」。
    """
    with pytest.raises(ValueError, match=f"{field} 不是规范形式"):
        _version(**{field: "Probe Scene"})


def test_an_observation_scope_with_no_region_is_not_a_scope() -> None:
    """空列表在装载与派发两处都长得像「这个场景不做任何事」—— 而它只会是配置漏了。"""
    with pytest.raises(ValueError, match="region 不得为空"):
        _version(region=())


def test_a_scope_that_excludes_nothing_is_unbounded_and_refused() -> None:
    """一个什么都不排除的场景是一个**没有边界**的采集，不是一个更宽松的配置。

    装载层同样要求排除清单非空，所以放它过去只会构造出一个装不回来的东西。
    """
    with pytest.raises(ValueError, match="排除清单不得为空"):
        _version(exclusions=())


def test_a_scene_with_an_empty_source_plan_looks_like_it_collects_nothing() -> None:
    with pytest.raises(ValueError, match="source_plan 不得为空"):
        _version(source_plan=())


def test_the_same_region_listed_twice_is_a_configuration_slip_not_a_scope() -> None:
    with pytest.raises(ValueError, match="region 不得有重复项"):
        _version(region=("north", "north"))


@pytest.mark.parametrize(
    "field",
    ["scenario", "product", "role_policy", "profile_id", "profile_version"],
)
def test_a_blank_binding_is_refused(field: str) -> None:
    """绑定是空白的场景，在记录里没有答案供下游每一处判定去问。"""
    with pytest.raises(ValueError, match=f"{field} 不得为空"):
        _version(**{field: "   "})


def test_version_zero_would_be_read_as_never_written() -> None:
    """0 会被读成「还没写过」，与「第一版」撞车 —— 两个不同的东西共用一个编号。"""
    with pytest.raises(ValueError, match="版本号从 1 开始"):
        _version(version=0)


def test_a_second_version_must_say_which_version_it_replaces() -> None:
    """不写 supersedes 的第二版在历史里是一个凭空出现的版本。"""
    with pytest.raises(ValueError, match="必须写明它取代哪一版"):
        _version(version=2, supersedes=None)


def test_supersedes_must_be_the_version_immediately_before() -> None:
    """允许跳号意味着历史里存在一个「从未写过」的编号 —— 没人能回答它是丢了还是不存在。"""
    with pytest.raises(ValueError, match="supersedes 必须是上一版"):
        _version(version=3, supersedes=1)


def test_exclusions_cover_names_exactly_what_is_missing() -> None:
    """§4「不得借产品扩展撤销已被批准 profile 的排除」的执行器。

    做成返回缺失项而不是布尔，是因为拒绝时要说的恰恰是「缺了哪几条」——
    一个 False 无法告诉运营要补哪一条。顺序按调用方给的必排除清单，
    于是回执里的那一行与策略里那一行可以逐条对着读。
    """
    version = _version(exclusions=("probe-excluded", "probe-barred"))

    assert version.exclusions_cover(("probe-excluded", "probe-barred")) == ()
    assert version.exclusions_cover(
        ("probe-barred", "probe-missing", "probe-also-missing")
    ) == ("probe-missing", "probe-also-missing")


# --- 摘要：内容寻址，不是身份寻址 ---------------------------------------------


def test_the_frozen_digest_is_content_addressed_not_identity_addressed() -> None:
    """授权门与 wave manifest 绑的是它 —— 两件内容相同的规则必须给出同一个摘要。

    绑到一个只与对象身份有关的东西上，会让「同一个 Scene 重新装载一遍」
    看起来像换了一版规则，于是所有在途工作都被告知「规则变了」。
    """
    assert _version().frozen_sha256 == _version().frozen_sha256


def test_when_it_was_written_and_what_was_noted_are_not_part_of_the_rules() -> None:
    """``created_at`` 与 ``note`` 不是绑定字段，所以它们不进摘要。

    进了的话，一次重新装载（时间变了）或一句备注（人写的）都会让授权门看到
    「规则变了」—— 按旧摘要批准过的批次会全部要求重新批准，而一个字节都没改。
    """
    first = _version(created_at=NOW, note="probe note")
    second = _version(created_at=NOW + timedelta(days=3), note="另一个备注")

    assert first.frozen_sha256 == second.frozen_sha256


def test_every_bound_field_moves_the_frozen_digest() -> None:
    """绑定字段任一个变了，摘要就必须变 —— 这就是「绑定」这个词在这里的意思。

    这个性质是「这批东西是按哪份规则做的」这个问题的唯一答案。少一个字段，
    就有一类改动不产生新摘要，而授权门会继续放行按旧规则做的批次。
    """
    reference = _version()

    for field, alternative in BOUND_FIELDS.items():
        changed = _version(**{field: alternative})
        assert changed.frozen_sha256 != reference.frozen_sha256, (
            f"改了 {field} 而摘要没变 —— 这一类改动在授权门眼里不存在"
        )


def test_a_second_version_of_the_same_rules_is_still_a_different_digest() -> None:
    """只有版本号不同、其余一字未改，摘要**也**必须不同。

    摘要是身份，不是相似度：两版同内容的规则合并成同一个摘要之后，
    「第 2 版生效了」这件事在按摘要记账的地方没有留下任何可分辨的东西。
    """
    assert _version(version=1).frozen_sha256 != _version(version=2).frozen_sha256


# --- 当前状态与写入的版本必须自洽 ---------------------------------------------


def test_a_draft_that_carries_an_effective_version_is_refused() -> None:
    """一个从未被批准的场景若带着 effective_version，派发侧就会认为它能开工。"""
    with pytest.raises(ValueError, match="草稿态的 Scene 不得有生效版本"):
        _record(state=SceneState.DRAFT, effective_version=1)


def test_an_active_scene_without_an_effective_version_is_refused() -> None:
    """「生效的是哪一版」在 ACTIVE 上没有答案，等于没人知道按哪份规则在跑。"""
    with pytest.raises(ValueError, match="ACTIVE 的 Scene 必须有生效版本"):
        _record(effective_version=None)


def test_an_active_scene_that_carries_a_pause_reason_is_refused() -> None:
    with pytest.raises(ValueError, match="ACTIVE 的 Scene 不得带暂停理由"):
        _record(pause_reason=PauseReason.OPERATOR)


def test_a_pause_without_a_reason_is_indistinguishable_from_a_pressed_button() -> None:
    """``operator_pause`` 要自己 resume、``policy_hold`` 要等那条策略失效。

    缺理由的暂停让这两种处置在报告上无法区分。
    """
    with pytest.raises(ValueError, match="PAUSED 必须写明理由"):
        _record(state=SceneState.PAUSED, pause_reason=None)


def test_an_effective_version_outside_the_written_history_is_refused() -> None:
    """生效版本指向一版谁也没写过的东西，等于按一份不存在的规则在跑。"""
    with pytest.raises(ValueError, match=r"必须落在 1\.\.latest_version"):
        _record(effective_version=4)


def test_only_an_active_scene_may_dispatch_new_work() -> None:
    """**这个标志只用于派发前判定。** 受理侧（回信/退信/退订/投诉）不看它。

    一个暂停的场景不会因此停止收到别人的回信；把回信也一起停掉，等于在暂停
    期间静默攒下一批该抑制的地址。所以这里断的是「只有 ACTIVE 能派发」，
    而不是「非 ACTIVE 什么都不做」—— 后者会把受理一起关掉。
    """
    assert _record_in(SceneState.ACTIVE).accepts_new_work is True
    assert _record_in(SceneState.DRAFT).accepts_new_work is False
    assert _record_in(SceneState.PAUSED).accepts_new_work is False
    assert _record_in(SceneState.ARCHIVED).accepts_new_work is False


def test_the_refusal_says_why_exactly_when_dispatch_is_closed() -> None:
    """``dispatch_refusal`` 与 ``accepts_new_work`` 互为反面 —— 且拒绝里说了「为什么」。

    两格而不是一格，是因为一句话要有两个形态：判词用真值，回执要原文。这条判据
    钉的是它们**不许分家**：一个只改了 ``accepts_new_work`` 的实现会让闸门收到
    一句空话，而闸门读的就是这一句 —— 那时它会放行，而报告上一切正常。

    「为什么」那一半也逐条钉住：三个不允许派发的状态，各自的理由里必须有能指向
    「谁能让它重新开工」的那样东西（``operator_pause`` 要人点 resume、``draft``
    要人先批准、``archived`` 是一个终态）。丢掉它，这条拒绝就退化成一句「不行」。
    """
    for state in SceneState:
        record = _record_in(state)
        assert bool(record.dispatch_refusal) is not record.accepts_new_work, (
            f"{state.value}：拒绝文与派发判定分家了"
        )

    assert "尚未批准生效" in _record_in(SceneState.DRAFT).dispatch_refusal
    assert (
        PauseReason.OPERATOR.value
        in _record_in(SceneState.PAUSED).dispatch_refusal
    ), "暂停的理由要说出来：operator_pause 与 policy_hold 的解除方式不同"
    assert _record_in(SceneState.ARCHIVED).dispatch_refusal.startswith("archived")
    assert _record_in(SceneState.ACTIVE).dispatch_refusal == ""


# --- 写一版 ≠ 让一版生效 ------------------------------------------------------


def test_writing_a_new_version_does_not_put_it_into_effect() -> None:
    """本文件最重要的一条。

    写一版与让一版生效是两件事。合并它们之后，一次编辑就自动生效 ——
    在途的运行会在一半的时候换掉规则，而报告上看起来只是「版本号变大了」。
    所以写了第 2 版之后，生效的**仍然**是第 1 版，待批的是第 2 版。
    """
    edited = _record().with_version(_version(version=2), at=LATER)

    assert edited.latest_version == 2
    assert edited.pending_version == 2
    assert edited.effective_version == 1
    assert edited.state is SceneState.ACTIVE
    assert edited.updated_at == LATER
    assert edited.accepts_new_work is True, "写了一版不该让派发停下"


def test_activating_the_pending_version_is_what_makes_it_effective() -> None:
    """上一条的另一半：只有 ``activate`` 能把待批的那一版送上生效位。"""
    edited = _record().with_version(_version(version=2), at=LATER)
    approved = edited.activate(version=2, at=LATER, by=APPROVER)

    assert approved.effective_version == 2
    assert approved.pending_version is None
    assert approved.latest_version == 2


def test_a_draft_never_has_a_pending_version() -> None:
    """「待批」以「有一版在生效」为前提 —— 草稿没有任何一版在生效。

    把 pending_version 读成 latest_version，会让一个从未开工的 Scene
    在报告里显示「有一版待批」，而它真正缺的是一个批准人。
    """
    draft = _record_in(SceneState.DRAFT).with_version(_version(version=2), at=LATER)

    assert draft.latest_version == 2
    assert draft.pending_version is None


def test_a_version_from_another_scene_or_workspace_is_refused() -> None:
    """版本的归属必须与记录一致 —— 否则两个 Scene 的规则历史会串到一起。"""
    with pytest.raises(ValueError, match="不属于"):
        _record().with_version(_version(scene_id="other-scene", version=2), at=LATER)
    with pytest.raises(ValueError, match="不属于"):
        _record().with_version(_version(workspace="other-workspace", version=2), at=LATER)


def test_a_skipped_version_number_is_refused() -> None:
    """跳号会让历史里出现一个「从未写过」的编号 —— 没人能回答它是丢了还是不存在。"""
    with pytest.raises(ValueError, match="新版本号必须是 2"):
        _record().with_version(_version(version=3), at=LATER)


def test_a_paused_scene_can_still_be_edited_without_reviving_it() -> None:
    """暂停的场景当然可以被编辑 —— 编辑不会自动让它开工。

    把「可编辑」与「可开工」做成同一个开关，会让每一次改配置都重启一次外联。
    """
    edited = _record_in(SceneState.PAUSED).with_version(_version(version=2), at=LATER)

    assert edited.state is SceneState.PAUSED
    assert edited.pause_reason is PauseReason.OPERATOR
    assert edited.pending_version == 2
    assert edited.accepts_new_work is False


# --- 激活（= 批准这一版） -----------------------------------------------------


def test_activating_records_who_approved_which_version() -> None:
    """批准是一条**记录**，不是一条授权：它记的是「发生过」。

    没有它，「这一版是谁批的」在事后无法追问 —— 而那是唯一能回答
    「为什么这个场景会开工」的东西。
    """
    approved = _record(state=SceneState.DRAFT, effective_version=None).activate(
        version=1, at=NOW, by=APPROVER
    )

    assert approved.state is SceneState.ACTIVE
    assert approved.effective_version == 1
    assert approved.pause_reason is None
    assert [(a.version, a.by, a.at) for a in approved.approvals] == [(1, APPROVER, NOW)]


def test_activating_a_version_that_was_never_written_is_refused() -> None:
    """生效版本必须落在已写下的历史之内，否则它指向一版不存在的规则。"""
    with pytest.raises(SceneStateError, match=r"不在 1\.\.1 之内"):
        _record(state=SceneState.DRAFT, effective_version=None).activate(
            version=2, at=NOW, by=APPROVER
        )


def test_an_archived_scene_cannot_be_activated() -> None:
    """归档是「这个市场不再经营」，不是「暂时不看」。

    一个能被重新激活的归档会让「不再经营」这件事在报告上无法表达 ——
    而它的代价是重新对一批已经处理过的人外联。
    """
    with pytest.raises(SceneStateError, match="归档是终态"):
        _record_in(SceneState.ARCHIVED).activate(version=1, at=NOW, by=APPROVER)


@pytest.mark.parametrize("by", ["", "   "])
def test_activation_requires_a_named_approver(by: str) -> None:
    """一条没有主体的批准记录无法被追问。"""
    with pytest.raises(SceneStateError, match="激活必须写明主体"):
        _record(state=SceneState.DRAFT, effective_version=None).activate(
            version=1, at=NOW, by=by
        )


def test_activation_refuses_a_stale_precondition() -> None:
    """CAS：读到 PAUSED 并据此决定恢复，而中间有人把它改了，就必须整次拒绝。

    覆盖的代价是两条控制流各自都以为自己的决定生效了 —— 而记录上只看得到一条。
    """
    with pytest.raises(SceneStateError, match="状态已变"):
        _record().activate(version=1, at=NOW, by=APPROVER, from_state=SceneState.PAUSED)


def test_activation_approves_the_version_the_caller_named() -> None:
    """批准的是**调用方点名的那一版**，不是「最新那一版」。

    两者混同会让「批准第 1 版」在一次编辑之后变成「批准第 2 版」——
    而批准人从来没有看过第 2 版。
    """
    edited = _record().with_version(_version(version=2), at=LATER)
    approved = edited.activate(version=1, at=LATER, by=APPROVER)

    assert approved.effective_version == 1
    assert approved.pending_version == 2
    assert [a.version for a in approved.approvals] == [1]


# --- 状态机 ------------------------------------------------------------------


def test_the_transition_table_is_the_whole_state_machine() -> None:
    """遍历四态 × 四态，把 ``SCENE_TRANSITIONS`` 从一张表变成一条断言。

    这是本文件里唯一一处「表本身就是判据」的测试：表里加了一条边而代码没跟上，
    或者代码多认了一条表里没有的边，都会在这里红。
    """
    succeeded: set[tuple[SceneState, SceneState]] = set()
    for source in SceneState:
        for target in SceneState:
            reason = PauseReason.OPERATOR if target is SceneState.PAUSED else None
            try:
                _record_in(source).transition(target, at=LATER, pause_reason=reason)
            except SceneStateError:
                continue
            succeeded.add((source, target))

    legal = {
        (source, target) for source in SceneState for target in SCENE_TRANSITIONS[source]
    }
    # 表里唯一一条 ``transition`` 走不通的边：DRAFT → ACTIVE。见下一条测试。
    assert succeeded == legal - {(SceneState.DRAFT, SceneState.ACTIVE)}


def test_a_draft_is_started_by_an_approval_not_by_a_transition() -> None:
    """表上说草稿可以变成生效，而 ``transition`` 拒绝替它开工 —— 两句都要成立。

    开工是一个**要主体**的动作（``activate`` 要求批准人），而 ``transition``
    只负责「恢复一个已经生效过的场景」。让草稿从这条边上过去，会出现一个
    从来没有批准人的生效场景 —— 而它看起来与批准过的一模一样。
    """
    draft = _record_in(SceneState.DRAFT)

    assert SceneState.ACTIVE in SCENE_TRANSITIONS[SceneState.DRAFT]
    with pytest.raises(SceneStateError, match="从未激活过"):
        draft.transition(SceneState.ACTIVE, at=LATER)


@pytest.mark.parametrize("target", [SceneState.ACTIVE, SceneState.ARCHIVED])
def test_a_reason_belonging_to_a_pause_is_refused_anywhere_else(target: SceneState) -> None:
    """只有暂停带理由：一个带着理由的 ACTIVE 会让「它为什么停着」永远没有答案。"""
    with pytest.raises(SceneStateError, match="只有暂停带理由"):
        _record_in(SceneState.PAUSED).transition(
            target, at=LATER, pause_reason=PauseReason.POLICY
        )


def test_resuming_a_paused_scene_keeps_the_version_that_was_in_effect() -> None:
    """§4 逐字要求「恢复不得重发已发邮件或重吃整个母池」。

    那条的执行方式是「恢复不改任何游标」，而游标在版本之外 —— 所以这里只需
    保证 ``effective_version`` 不被重置。重置了的话，一个恢复动作会悄悄把
    在途工作钉住的规则换掉。
    """
    paused = _record_in(SceneState.PAUSED)
    resumed = paused.transition(SceneState.ACTIVE, at=LATER)

    assert resumed.state is SceneState.ACTIVE
    assert resumed.effective_version == paused.effective_version == 1
    assert resumed.pause_reason is None
    assert resumed.approvals == paused.approvals, "恢复不是一次批准"


def test_resuming_a_scene_that_was_never_activated_is_refused() -> None:
    """没有可恢复的生效版本 —— 首次开工请用 ``activate``（它要求写明批准人）。"""
    never = _record(
        state=SceneState.PAUSED,
        effective_version=None,
        pause_reason=PauseReason.OPERATOR,
    )
    with pytest.raises(SceneStateError, match="从未激活过"):
        never.transition(SceneState.ACTIVE, at=LATER)


def test_an_archived_scene_has_no_way_back() -> None:
    """终态是「没有边」，不是「有一条边但被拒绝」。

    ``ARCHIVED`` 的出边为空集 —— 一个能被恢复的归档会让「这个市场不再经营」
    在报告上无法表达。
    """
    archived = _record_in(SceneState.ARCHIVED)

    assert SCENE_TRANSITIONS[SceneState.ARCHIVED] == frozenset()
    for target in SceneState:
        with pytest.raises(SceneStateError, match="非法的 Scene 状态转移"):
            archived.transition(target, at=LATER)


def test_a_stale_precondition_makes_a_transition_refuse_rather_than_overwrite() -> None:
    with pytest.raises(SceneStateError, match="状态已变"):
        _record_in(SceneState.PAUSED).transition(
            SceneState.ACTIVE, at=LATER, from_state=SceneState.DRAFT
        )


def test_every_write_moves_the_revision_forward() -> None:
    """``revision`` 是存储层 CAS 的比较对象，不是历史。

    两个控制端同时读到 PAUSED、同时决定 resume，只有一个能写成 —— 判据是
    ``UPDATE ... WHERE revision = ?``。所以每一次会改变记录的动作都必须让它加一；
    漏掉一处，那一处的并发写就永远赢。
    """
    record = _record_in(SceneState.PAUSED)

    written = (
        record.with_version(_version(version=2), at=LATER),
        record.transition(SceneState.ACTIVE, at=LATER),
        record.activate(version=1, at=LATER, by=APPROVER),
    )
    for after in written:
        assert after.revision == record.revision + 1


# --- 影响面 ------------------------------------------------------------------


@pytest.mark.parametrize(
    "changes",
    [
        {},
        {"budget": (SceneBudgetLine(line=BudgetLine.SEARCH, ceiling=200),)},
        {"refresh": _refresh(after_days=91)},
    ],
    ids=["no-rule-rule-changed", "budget", "refresh"],
)
def test_a_change_that_touches_no_rule_invalidates_nothing(changes: dict) -> None:
    """``budget`` 与 ``refresh`` **不在**影响面表里，这是故意的。

    它们改变的是「以后做多少」与「什么时候再看一眼」，不是「已经看到的东西
    还成不成立」。把它们塞进表里，会让每一次调预算都作废一批已经做出来的
    草稿与 wave —— 而那纯粹是浪费，报告上只显示「重做了一批」。

    ``reusable`` 必须是**全部**九个 scope，而不是「没提到的那些」：
    只有它是全的，读的人才不需要知道表里有哪些字段。
    """
    impact = _impact(changes)

    assert impact.is_empty is True
    assert impact.refetch == ()
    assert impact.reassess == ()
    assert impact.reusable == tuple(ImpactScope)
    assert (impact.from_version, impact.to_version) == (1, 2)


def test_an_exclusions_change_reopens_admission_and_route_grades() -> None:
    """§4 的「可以加排除，不可以减」：加一条排除意味着有一批账号从此不该被采集、
    有一批已定级的路线站不住了 —— 两者都必须重判。

    而它们**不必**重取：场地还是那个场地。把重判写成重取会让一次配置收紧
    变成一整轮重新采集。
    """
    impact = _impact({"exclusions": ("probe-excluded", "probe-barred")})

    assert impact.refetch == ()
    assert set(impact.reassess) == {ImpactScope.ACCOUNT_ADMISSION, ImpactScope.ROUTE_GRADE}
    assert impact.is_empty is False


def test_a_source_plan_change_refetches_supply_and_does_not_rejudge_it() -> None:
    """改 ``source_plan`` 是「去看别的地方」，不是「原来的结论不成立」。

    它只进 ``refetch``：同一个 scope 在两栏里各出现一次，读的人会以为那是
    两件事，而实际的处置是一件。
    """
    impact = _impact({"source_plan": ("probe-source", "probe-other-source")})

    assert set(impact.refetch) == {ImpactScope.SOURCE_SUPPLY}
    assert ImpactScope.SOURCE_SUPPLY not in impact.reassess


def test_refetching_something_never_also_lists_it_as_reassess() -> None:
    """重取蕴含重判，所以重取过的 scope 不得再出现在 ``reassess`` 里。

    单改一个字段看不出这条：表里没有哪一格的重取与重判相交。它只在**多个字段
    一起改**时才成立 —— 区域与 profile 同时换，责任关系既被某一格列进重取、
    又被另一格列进重判。不减这一下，那个 scope 会在两栏里各出现一次，
    而读的人会以为那是两件事、两笔工。
    """
    impact = _impact({"region": ("south",), "profile_id": "other-profile"})

    # 前提：区域这一格的重判清单里确实有它。没有这一条，上面的断言会在
    # 表被改动之后静默变成空话。
    assert ImpactScope.RESPONSIBILITY in SCENE_IMPACT_RULES["region"][1]
    assert ImpactScope.RESPONSIBILITY in impact.refetch
    assert ImpactScope.RESPONSIBILITY not in impact.reassess


def test_the_three_impact_buckets_partition_every_scope() -> None:
    """``reusable`` 是**全部** :class:`ImpactScope` 减去前两类，三栏两两不相交。

    这不是一句关于实现的断言，是一句关于回执的断言：读的人不需要知道表里有
    哪些字段，就能从 ``reusable`` 看出「这次改动没碰到的面」是哪几个。
    少一个 scope，或者让一个 scope 出现在两栏里，这个回执就答不了它被造出来
    要回答的那个问题。

    判据对整个表逐格跑一遍，而不是手写一个例子 —— 手写只能证明那一格，
    而这张表的每一格都是一条独立的业务判断。
    """
    assert set(RULE_FIELD_REWRITES) == set(SCENE_IMPACT_RULES), (
        "影响面表里出现了没有替代值的字段 —— 那一类改动没有被本测试覆盖"
    )

    changes: list[dict] = [{}]
    changes += [{field: value} for field, value in RULE_FIELD_REWRITES.items()]
    changes += [
        {"region": ("south",), "profile_id": "other-profile"},
        dict(RULE_FIELD_REWRITES),
    ]

    for change in changes:
        impact = _impact(change)
        refetch, reassess, reusable = (
            set(impact.refetch),
            set(impact.reassess),
            set(impact.reusable),
        )

        assert len(refetch) == len(impact.refetch), f"重取栏里有重复项：{change}"
        assert len(reassess) == len(impact.reassess), f"重判栏里有重复项：{change}"
        assert len(reusable) == len(impact.reusable), f"不受影响栏里有重复项：{change}"
        assert not refetch & reassess, f"同一个 scope 同时被重取与重判：{change}"
        assert not refetch & reusable, f"同一个 scope 既被重取又不受影响：{change}"
        assert not reassess & reusable, f"同一个 scope 既被重判又不受影响：{change}"
        assert refetch | reassess | reusable == set(ImpactScope), (
            f"三栏没有盖住全部 ImpactScope：{change}"
        )
        assert impact.is_empty is (not refetch and not reassess)


def test_impact_across_two_scenes_is_refused() -> None:
    """两个场景的字段即使同名也不是同一件事 —— 比对它们会给出一个没有主语的影响面。"""
    with pytest.raises(ValueError, match="不能跨 Scene 比对影响面"):
        scene_impact(_version(), _version(scene_id="other-scene"))
