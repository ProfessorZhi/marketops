"""B16 编译面的判据：一份计划是**由哪张表**拼出来的，以及谁碰不了哪一格。

这一片的承重句子有四句，各有一组判据：

1. **「编译优先级、来源、冲突与版本必须由普通代码确定并可解释」**（§B16 对策略
   模块的硬要求）。执行它的是第 1 组那几条表判据：两张写死的表必须分区、每一格
   恰好一个写者、每一个写者都在优先级表里、每一个在优先级表里的模块都真的管着
   某一格。少了任何一条，「可解释」就退回成一句读代码的活。
2. **「Agent/Jev 不可自行改硬排除、预算、产品 claim、访问权限或发送权限」**。
   第 4 组逐格打那九格：一个越权的贡献**连造都造不出来**（构造期判据），因此没有
   任何一条路径能把它带进计划。
3. **摘要盖住它声称盖住的那几格**。第 2 组逐格改一个值，摘要各换一次；跨格搬一个
   值，摘要也要换（``SEP`` 拼接的经典失败）；同一份声明两次编译，逐字相同。
4. **preview 零 provider 调用**（关闭条件 1 的后半）在这一层同样是**结构上**成立的，
   由第 6 组的两条 AST 判据钉住：这一层没有可以调用的东西，编译器也不认识任何一个
   具体模块。

## 这一片的替身是真的替身

第 6 组以外没有 provider，也不需要 ``RecordingSearch`` 那一类东西：策略模块在本片
里是**测试写的**双份（``_Module``），它们唯一能拿到的三样东西是 context / envelope /
bundle —— 而那个签名的三个参数由一条判据守着（``test_the_compiler_hands_the_modules_
exactly_three_inputs``），于是「模块够不到外面」不是写下来的纪律，是调用形状。

## 这一片之后

真正的六个模块（``Geo/LocaleSourceProfile`` + ``ScenarioProfile`` + ``ObjectivePolicy``
+ ``AccessStrategy`` + 可选 Product overlay / Workstream override）与 preview 面
（MCP 只读工具）是 B16-b 的（2/3）与（3/3）。本片**没有生产消费者** —— 唯一的调用者
是这份判据，与 ``tests/test_research_context.py`` 同一个处境，理由见那一份的开头。
"""

from __future__ import annotations

import ast
from dataclasses import replace
from pathlib import Path

import pytest
from pydantic import ValidationError

from marketops.domain.errors import ConfigError
from marketops.domain.profile import ProfileBundle
from marketops.domain.research_context import (
    Ambiguity,
    ContextDimension,
    ResearchContext,
    ResearchEnvelope,
    ScenarioCandidate,
    ScenarioProvenance,
    ambiguity_is_hard,
    effective_jurisdiction,
    envelope_of,
)
from marketops.domain.research_plan import (
    _DIGEST_FIELDS,
    DECLARED_SCOPE,
    FIELD_OVERRIDERS,
    FIELD_OWNER,
    HARD_SCOPE_FIELDS,
    MODULE_PRIORITY,
    MODULE_WRITABLE_FIELDS,
    EffectiveResearchPlan,
    PageArchetype,
    PlanField,
    PolicyContribution,
    PolicyProvenance,
    StrategyCompiler,
    StrategyModuleId,
    declared_scope_of,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
MODULE = REPO_ROOT / "marketops" / "domain" / "research_plan.py"

#: 上下文之外的元数据格 —— 它们不是 :class:`PlanField`，但也在摘要里。
_META_FIELDS = ("fingerprint_digest", "scene_version", "modules", "provenance", "overrides", "ambiguities")


def _context(**overrides: object) -> ResearchContext:
    """一个**讲清楚了**的上下文。取值刻意是市场中性的（与输入面那一份同样理由）。"""
    base: dict[str, object] = {
        "geo": "GB",
        "entity_classes": ("school",),
        "asset_classes": ("pitch",),
        "organization_classes": ("trust",),
        "scenario_candidates": (
            ScenarioCandidate(
                name="secondary school",
                reading="k12",
                provenance=ScenarioProvenance.DECLARED,
                source="req-1",
            ),
        ),
        "objective": "find accounts for the approved offer",
        "evidence_requirements": ("official_site",),
    }
    base.update(overrides)
    return ResearchContext(**base)  # type: ignore[arg-type]


def _contribution(
    field: PlanField,
    *,
    values: tuple[str, ...] = (),
    archetypes: tuple[PageArchetype, ...] = (),
    reason: str = "测试里的理由",
) -> PolicyContribution:
    """一条贡献的模板。

    ``module_id`` / ``revision`` 这里是占位，:class:`_Module` 交上来之前会给它们
    盖上自己的身份 —— 于是判据写的是「哪一格上放了什么」，而「这是谁写的」只有
    一处（模块自己）。
    """
    return PolicyContribution(
        module_id=StrategyModuleId.GEO_LOCALE_SOURCE,
        revision="placeholder",
        field=field,
        values=values,
        archetypes=archetypes,
        reason=reason,
    )


#: 每一个模块**默认**写的那些格。四个基础模块合起来恰好覆盖八格 ——
#: 由 ``test_the_default_module_set_covers_every_writable_field`` 执行。
def _default_cells(module_id: StrategyModuleId) -> tuple[PolicyContribution, ...]:
    if module_id is StrategyModuleId.GEO_LOCALE_SOURCE:
        return (
            _contribution(PlanField.LOCALES, values=("en-GB",), reason="按地理声明取语言"),
            _contribution(
                PlanField.QUERY_FAMILIES, values=("venue",), reason="这一族按场地词展开"
            ),
            _contribution(
                PlanField.SOURCE_METHODS, values=("search",), reason="先检索，再按页抓取"
            ),
        )
    if module_id is StrategyModuleId.SCENARIO:
        return (
            _contribution(
                PlanField.PAGE_ARCHETYPES,
                archetypes=(
                    PageArchetype(tier="high", patterns=("*/contact*",)),
                    PageArchetype(tier="low", patterns=("*/about*",)),
                ),
                reason="按声明的档位排页面",
            ),
            _contribution(PlanField.ROLE_FUNCTIONS, values=("operations",), reason="这一族相关的职能"),
            _contribution(
                PlanField.ROLE_PRIORITIES, values=("operations",), reason="先看操作岗"
            ),
        )
    if module_id is StrategyModuleId.OBJECTIVE:
        return (
            _contribution(
                PlanField.DECISION_REFS, values=("page_triage",), reason="这一族要答的题面"
            ),
        )
    if module_id is StrategyModuleId.ACCESS:
        return (
            _contribution(
                PlanField.ACQUISITION_MODES, values=("http",), reason="公开页面直接取"
            ),
        )
    # PRODUCT_OVERLAY / WORKSTREAM_OVERRIDE 默认**不写任何格**（它们只在覆盖时出现）
    return ()


class _Module:
    """一个测试用的策略模块。

    它同时是一台**记录仪**：``seen`` 里逐次记下编译器交给它的三样东西。判据因此
    能问「编译器有没有在拼装之前就把越界的请求拦住」（``seen`` 为空），以及
    「它交给模块的是不是调用方给的那一份 envelope」（同一个对象，不是重推的）。
    """

    def __init__(
        self,
        module_id: StrategyModuleId,
        contributions: tuple[PolicyContribution, ...] | None = None,
        revision: str | None = None,
    ) -> None:
        self.module_id = module_id
        self.revision = revision or f"rev-{module_id.value}"
        self._contributions = (
            _default_cells(module_id) if contributions is None else contributions
        )
        self.seen: list[dict[str, object]] = []

    def contribute(
        self,
        *,
        context: ResearchContext,
        envelope: ResearchEnvelope,
        bundle: ProfileBundle,
    ) -> tuple[PolicyContribution, ...]:
        self.seen.append({"context": context, "envelope": envelope, "bundle": bundle})
        return tuple(
            item.model_copy(update={"module_id": self.module_id, "revision": self.revision})
            for item in self._contributions
        )


def _modules(*, extra: tuple[_Module, ...] = (), replace: dict | None = None) -> tuple[_Module, ...]:
    """四个基础模块（按 :data:`MODULE_PRIORITY` 的顺序），后接 ``extra``。"""
    replace = replace or {}
    base = tuple(
        replace.get(module_id) or _Module(module_id)
        for module_id in (
            StrategyModuleId.GEO_LOCALE_SOURCE,
            StrategyModuleId.SCENARIO,
            StrategyModuleId.OBJECTIVE,
            StrategyModuleId.ACCESS,
        )
    )
    return base + extra


def _compiler(*, extra: tuple[_Module, ...] = (), replace: dict | None = None) -> StrategyCompiler:
    return StrategyCompiler(modules=_modules(extra=extra, replace=replace))


def _compile(
    bundle: ProfileBundle,
    context: ResearchContext | None = None,
    *,
    extra: tuple[_Module, ...] = (),
    replace: dict | None = None,
) -> EffectiveResearchPlan:
    return _compiler(extra=extra, replace=replace).compile(
        bundle=bundle, context=context or _context(), envelope=envelope_of(bundle)
    )


def _with_profile(bundle: ProfileBundle, **changes: object) -> ProfileBundle:
    """换掉声明块上的几格。

    ``ProfileBundle`` 与 ``DeclaredProfile`` 都是 frozen dataclass（不是 pydantic
    模型），所以换一格走 ``dataclasses.replace``，而不是 ``model_copy`` ——
    那一个根本不存在，而一个不存在的 API 在判据里会让这条「声明改了会怎样」
    的问题没人答得出来。
    """
    assert bundle.profile is not None
    return replace(bundle, profile=replace(bundle.profile, **changes))


def _without_profile(bundle: ProfileBundle) -> ProfileBundle:
    return replace(bundle, profile=None)


def _perturbed(plan: EffectiveResearchPlan, field: PlanField) -> EffectiveResearchPlan:
    """把一格换成一个**确实不同**的值，其余逐字不动。

    逐格的换法由这一格的形状决定（字符串、元组、整数或 ``None``、页面原型）。
    ``None`` 换成 ``0`` 而不是一个「空一点」的值：``None`` 是「没声明」、
    ``0`` 是「声明了零」，后者会让一次研究一次都不发 —— 两者散出同一个摘要，
    正是这条判据要排除的东西。
    """
    if field is PlanField.PAGE_ARCHETYPES:
        return plan.model_copy(
            update={field.value: (PageArchetype(tier="other", patterns=("*/other*",)),)}
        )
    value = getattr(plan, field.value)
    if value is None:
        return plan.model_copy(update={field.value: 0})
    if isinstance(value, int):
        return plan.model_copy(update={field.value: value + 1})
    if isinstance(value, str):
        return plan.model_copy(update={field.value: value + "-other"})
    assert isinstance(value, tuple)
    return plan.model_copy(update={field.value: (*value, "other")})


# --- 1. 两张表：谁写得出来，谁改得了谁 ----------------------------------------


def test_the_two_field_groups_partition_the_closed_set() -> None:
    """硬的那九格与可写的八格并起来是全部，且不相交。

    少了这条，加一格而忘了归类时它会**静默算成可写的**（两张表都没说它是硬的），
    于是「禁止 Agent 改预算」这一类边界会在一格上消失 —— 而那一格看起来正常：
    它有值、有出处、有一个看起来合理的模块在写它。
    """
    every = set(PlanField)
    assert HARD_SCOPE_FIELDS | MODULE_WRITABLE_FIELDS == every
    assert not (HARD_SCOPE_FIELDS & MODULE_WRITABLE_FIELDS)


def test_every_module_writable_field_has_exactly_one_owner() -> None:
    """八格各有一个**唯一**的写者。

    两处都能写同一格时，「这一格是谁定的」不再是一句话能答的问题 ——
    而它正是这份计划的全部产品。
    """
    assert set(FIELD_OWNER) == MODULE_WRITABLE_FIELDS


def test_every_owner_and_overrider_is_a_module_in_the_priority_table() -> None:
    """表里出现的每个模块名都在 :data:`MODULE_PRIORITY` 里。

    少了这条，一个写进 ``FIELD_OWNER`` 却漏进优先级表的模块会在
    ``MODULE_PRIORITY.index(...)`` 那里抛 ``ValueError`` —— 那是一次编译期的
    崩溃，而不是一条说得清的回执。更要紧的是它会**只在有人真的覆盖那一格时**
    才响，于是「表漏了一行」会在很久以后以一次线上失败的形式出现。
    """
    named = set(FIELD_OWNER.values())
    for overriders in FIELD_OVERRIDERS.values():
        named.update(overriders)
    assert named <= set(MODULE_PRIORITY)


def test_every_module_in_the_priority_table_does_something() -> None:
    """优先级表里的每个模块都真的管着某一格。

    表里挂着一个什么都不写的模块，读的人会以为它在编译里参与了某一格 ——
    而它只是被调了一次、返回空元组。两个可选模块（Product overlay /
    Workstream override）今天**没有声明来源**，所以它们在这里的身份是覆盖者。
    """
    active = set(FIELD_OWNER.values())
    for overriders in FIELD_OVERRIDERS.values():
        active.update(overriders)
    assert active == set(MODULE_PRIORITY)


def test_an_overrider_is_more_specific_than_the_owner_it_overrides() -> None:
    """每一对覆盖关系的先后都必须让**覆盖者在后**。

    这是 ``build_plan`` 里那条 ``MODULE_PRIORITY.index`` 判据的守卫：那一支
    今天是不可达的（每条覆盖都排得更靠后），而它不可达的**理由**就在这条判据上。
    少了它，把 ``WORKSTREAM_OVERRIDE`` 挪到 ``SCENARIO`` 前面会让「更具体的赢」
    变成「更笼统的赢」，而两者在报告上都写着「某某覆盖了某某」。
    """
    for field, overriders in FIELD_OVERRIDERS.items():
        owner = MODULE_PRIORITY.index(FIELD_OWNER[field])
        for overrider in overriders:
            assert MODULE_PRIORITY.index(overrider) > owner, (
                f"{overrider.value} 声称覆盖 {field.value}，而它在 MODULE_PRIORITY "
                f"里排在归属模块（{FIELD_OWNER[field].value}）之前 —— "
                "覆盖者必须比归属者更具体。"
            )


def test_a_hard_scope_field_is_never_overridable() -> None:
    """硬的那九格不允许有任何覆盖者 —— 它们连归属模块都没有。"""
    assert not (set(FIELD_OVERRIDERS) & HARD_SCOPE_FIELDS)


def test_every_plan_field_names_a_field_on_the_plan() -> None:
    """每一格的名字就是计划上的字段名。

    ``_block`` 用 ``getattr(self, field.value)`` 取那一格，所以一个名字写错的
    ``PlanField`` 会在**算摘要时**炸 —— 而那已经是编译的最后一步，回执上是一句
    属性错误。这条判据把它提前成一句表与模型的比对。
    """
    assert {field.value for field in PlanField} == set(EffectiveResearchPlan.model_fields) - set(
        _META_FIELDS
    )


def test_the_digest_covers_every_field_not_a_hand_written_subset() -> None:
    """摘要盖的字段集合**就是**全部成员。

    这条盯的不是 ``_DIGEST_FIELDS`` 那一行（它由 :class:`PlanField` 推出来），
    而是「有没有人把它换成一个手写的子集」：换掉之后，漏掉的那一格改了值而摘要
    不动，于是「这两次研究按同一份规则跑吗」会答「是」。
    """
    assert set(_DIGEST_FIELDS) == set(PlanField)
    assert len(_DIGEST_FIELDS) == len(set(_DIGEST_FIELDS))


# --- 2. 摘要 ----------------------------------------------------------------


def test_the_default_module_set_covers_every_writable_field(bundle: ProfileBundle) -> None:
    """四个基础模块合起来恰好写满可写的那八格 —— 判据自己的前提。

    少了这条，后面每一条用 ``_compile`` 的判据都可能在一个**缺格**的编译上
    转红，而红的原因是判据自己少接了一个模块。
    """
    plan = _compile(bundle)
    written = {entry.field for entry in plan.provenance} & MODULE_WRITABLE_FIELDS
    assert written == MODULE_WRITABLE_FIELDS


@pytest.mark.parametrize("field", sorted(PlanField, key=lambda item: item.value))
def test_every_field_changes_the_digest(bundle: ProfileBundle, field: PlanField) -> None:
    """逐格改一个值，摘要各换一次。

    这是「摘要盖住它声称盖住的那几格」的执行器。一条判据只改一格，所以红在哪一格
    就说明漏的是哪一格 —— 比一条「摘要非空」的断言多出来的正是这个定位。
    """
    plan = _compile(bundle)
    assert _perturbed(plan, field).digest != plan.digest


@pytest.mark.parametrize(
    "change",
    [
        {"fingerprint_digest": "0" * 64},
        {"scene_version": "other-scene"},
        {"modules": ()},
        {"ambiguities": (Ambiguity(dimension=ContextDimension.LOCALES, detail="另一种语言"),)},
    ],
    ids=["fingerprint", "scene-version", "modules", "ambiguities"],
)
def test_the_metadata_blocks_change_the_digest(bundle: ProfileBundle, change: dict) -> None:
    """那几格元数据也在摘要里：指纹、场景版本、模块版本、以及模糊之处。

    前两者分别让「profile 改了」与「这一份上下文另一份」看得见；模块版本让
    「模块读到的东西改了」看得见；模糊之处让「这次是在哪几处之上守的」看得见。
    少了最后一格，两次模糊之处不同的输入会编出同一个摘要。
    """
    plan = _compile(bundle)
    assert plan.model_copy(update=change).digest != plan.digest


def test_moving_a_value_across_fields_changes_the_digest(bundle: ProfileBundle) -> None:
    """同样的字符换到另一格上，是另一份计划。

    把各格的值拼成一长串的实现会让 ``locales=("en","fr")`` 与 ``locales=("en",)``
    + ``query_families=("fr",)`` 撞上同一个摘要 —— 而这两份计划在现场做的事不同
    （前者问两种语言，后者问一种语言里的一族查询词）。逐格先散一次正是为了
    让「值搬了家」看得见。
    """
    plan = _compile(bundle)
    split = plan.model_copy(update={"locales": ("en", "fr"), "query_families": ()})
    joined = plan.model_copy(update={"locales": ("en",), "query_families": ("fr",)})
    assert split.locales != joined.locales
    assert split.digest != joined.digest

    # 一半：搬到**空着**的那一侧。这一半不够 —— 空的那一格仍然占一个分隔符，
    # 所以「把各格的值拼成一长串」的实现也能分得开这两份计划。留着它是为了让
    # 下一半的功劳说清楚：真正让长串实现失败的是**两格都有成员**时搬一名成员。
    left = plan.model_copy(update={"locales": ("en", "fr"), "query_families": ("venue",)})
    right = plan.model_copy(update={"locales": ("en",), "query_families": ("fr", "venue")})
    assert left.locales != right.locales
    assert left.digest != right.digest


def test_the_same_declaration_twice_is_the_same_plan(bundle: ProfileBundle) -> None:
    """同一份声明、同一份上下文，两次编译逐字相同。

    这是关闭条件 1 里「同一自然语言任务稳定编译可复现 plan digest」那一半的
    执行器：它不证明「换一份声明会变」（那由下面那条答），只证明**这一次编译
    没有掺进此刻的什么东西** —— 时间、字典遍历顺序、对象身份。
    """
    first = _compile(bundle)
    second = _compile(bundle)
    assert first.digest == second.digest
    assert first == second


def test_a_changed_declaration_gives_another_plan(bundle: ProfileBundle) -> None:
    """声明改了一格，计划的摘要就换一个。

    与上一条成对：那一条只说「两次一样」，这一条只说「改了会变」。少了后者，
    一个恒返回同一个摘要的实现在上一条里全绿 —— 而它会让「这一次按哪一份规则跑」
    变成一个没有答案的问题。
    """
    profile = bundle.profile
    assert profile is not None
    changed = _with_profile(bundle, compliance=profile.compliance | {"login": True})
    assert changed.profile is not None
    assert changed.profile.compliance != profile.compliance
    assert _compile(changed).digest != _compile(bundle).digest


# --- 3. 声明层那九格 --------------------------------------------------------


def test_the_declared_nine_come_from_the_declaration_and_nowhere_else(
    bundle: ProfileBundle,
) -> None:
    """那九格逐格来自声明，且出处一律写着声明层。

    判据拿 bundle 自己算期望值，不写字面量：一个把 ``search.max_queries`` 换成
    别处的实现在这里会红，而 profile 的值改了这条判据不用跟着改（它比的是
    「计划上那一格 == 声明里那一格」，不是某个数）。
    """
    profile = bundle.profile
    assert profile is not None
    context = _context()
    envelope = envelope_of(bundle)
    plan = _compile(bundle, context)

    assert plan.geo == envelope.geo
    assert plan.jurisdiction == effective_jurisdiction(context) == envelope.geo
    assert plan.exclusions == envelope.exclusions
    assert plan.access_permissions == tuple(
        f"{key}={profile.compliance[key]}" for key in sorted(profile.compliance)
    )
    assert plan.send_permissions == ()
    assert plan.search_budget == profile.search_max_queries
    assert plan.fetch_budget == profile.fetch_max_documents
    # 这一份上下文没挂产品用途，于是产品身份也不挂。
    assert plan.product_use == ""
    assert plan.product_claims == ()
    for field in HARD_SCOPE_FIELDS:
        assert plan.provenance_for(field).module_id == DECLARED_SCOPE


def test_product_claims_are_attached_only_when_a_product_use_is_named(
    bundle: ProfileBundle,
) -> None:
    """挂了产品用途才带产品身份，而且那一份身份来自声明。

    空着不是「没有产品」，是「这次研究不挂产品」—— 两者在报告上必须分得开，
    否则一次挂着产品的研究与一次不挂的会得到同一个计划摘要。
    """
    profile = bundle.profile
    assert profile is not None
    declared_name = str((profile.raw.get("product") or {}).get("name", ""))
    assert declared_name, "这一份 profile 没有 product 块，判据的前提不成立"

    plan = _compile(bundle, _context(product_use="the approved offer"))
    assert plan.product_use == "the approved offer"
    assert declared_name in plan.product_claims


def test_an_undeclared_budget_stays_none_and_a_declared_zero_stays_zero(
    bundle: ProfileBundle,
) -> None:
    """额度没声明时是 ``None``，声明为零时是 ``0``，两者不合并。

    ``None`` 是「没声明」，使用方必须失败关闭；``0`` 是「已声明为零额度」，
    一次都跑不了。把两者都读成 ``None``（或都读成 ``0``）会让一次「声明了不花」
    看起来像一次「没填」，而这两种情形要处置的事不同。
    """
    profile = bundle.profile
    assert profile is not None
    assert profile.search_max_queries, "这一份 profile 没声明额度，判据的前提不成立"
    assert _compile(_with_profile(bundle, search_max_queries=None)).search_budget is None
    assert _compile(_with_profile(bundle, search_max_queries=0)).search_budget == 0


def test_a_profile_without_a_declaration_block_is_refused_on_both_routes(
    bundle: ProfileBundle,
) -> None:
    """没有声明块：读批准范围那一侧先拒，编译器那一侧也拒。

    两条路都写下来，因为它们答的是两个不同的问题。``envelope_of`` 拒的是「这份
    profile 说不出它批准了什么范围」—— 连批准的边界都读不出来。编译器拒的是
    「边界有了（它由调用方带来），但计划上那九格硬范围没有来源」。少了后者，
    一个拿着别处批准的 envelope、去编一份没有声明的 profile 的调用方，会得到一份
    **只有模块那八格、硬范围全空**的计划 —— 而它带着摘要与出处，看起来正常。
    """
    without = _without_profile(bundle)
    with pytest.raises(ValueError, match="没有声明块"):
        envelope_of(without)
    with pytest.raises(ConfigError, match="没有声明块"):
        _compiler().compile(
            bundle=without, context=_context(), envelope=envelope_of(bundle)
        )


def test_the_jurisdiction_cell_falls_back_to_geo_and_otherwise_keeps_what_was_written(
    bundle: ProfileBundle,
) -> None:
    """管辖那一格：空着时与 ``geo`` 同管辖，写明时就是写的那个值。

    判据从**两个方向**量同一条回退（``effective_jurisdiction`` 是它的唯一实现）。
    少了任一半，一个把这一格写死的实现会在另一半上绿；而两半都绿的时候，这一格
    与输入面、越界判定三处读到的值必然一致 —— 「同一份声明两个摘要」那一类失败
    就从这一处被堵住。
    """
    envelope = envelope_of(bundle)
    empty = declared_scope_of(bundle, _context(), envelope)
    named = declared_scope_of(bundle, _context(jurisdiction="IE"), envelope)
    assert empty.values[PlanField.JURISDICTION] == envelope.geo
    assert named.values[PlanField.JURISDICTION] == "IE"


# --- 4. 越权、重复、缺格 ----------------------------------------------------


@pytest.mark.parametrize("field", sorted(HARD_SCOPE_FIELDS, key=lambda item: item.value))
def test_a_module_cannot_write_a_hard_scope_field(field: PlanField) -> None:
    """那九格：策略模块**造都造不出**一条写它的贡献。

    判据打在构造期而不是编译期。理由是可诊断性：编译期拦下的越权贡献，与一条
    合法贡献在类型上是同一个东西 —— 它可能已经过了别处几道手。构造期拦下意味着
    没有任何一条路径能把它带进计划，包括还没写的那些路径。

    ``SEND_PERMISSIONS`` 是这九格里最要紧的一格（§7.3：本产品不发邮件）：
    它今天恒为空，而「恒为空」与「一个模块给它添了一条」在这里是同一条判据的两面。
    """
    with pytest.raises(ValidationError, match="声明层独有"):
        _contribution(field, values=("anything",))


def test_a_module_that_is_neither_owner_nor_overrider_cannot_write_a_field(
    bundle: ProfileBundle,
) -> None:
    """不是归属模块、也不在覆盖者名单里的模块写一格 → 拒绝，且回执点出两边。

    拒绝而不是让它覆盖：一个越权的写者安静地生效的话，「这条顺序是谁定的」会在
    报告上答出一个错的模块名 —— 而那份报告看起来完全正常。
    """
    intruder = _Module(
        StrategyModuleId.ACCESS,
        contributions=(
            _contribution(PlanField.LOCALES, values=("fr-FR",), reason="我认为该问法语"),
        ),
    )
    # ACCESS 是 ACQUISITION_MODES 的归属模块，但它不是 LOCALES 的写者。
    with pytest.raises(ConfigError, match="既不是归属模块") as caught:
        _compile(bundle, replace={StrategyModuleId.ACCESS: intruder})
    assert StrategyModuleId.GEO_LOCALE_SOURCE.value in str(caught.value)


def test_an_overrider_wins_and_both_sides_are_recorded(bundle: ProfileBundle) -> None:
    """覆盖者赢，而且**两边**都留在记录里（谁被盖掉、各是哪一版）。

    只记赢家的话，一个模块停止参与编译（例如 ``product_use`` 被清空）之后计划
    无声地变了个样，而报告上看不出曾经有过另一条 —— 那次变更的现场问题是
    「原来那条规则去哪了」。
    """
    override = _Module(
        StrategyModuleId.PRODUCT_OVERLAY,
        contributions=(
            _contribution(
                PlanField.PAGE_ARCHETYPES,
                archetypes=(PageArchetype(tier="high", patterns=("*/stockists*",)),),
                reason="挂了产品用途时先看进货页",
            ),
        ),
        revision="overlay-v1",
    )
    plan = _compile(bundle, _context(product_use="the approved offer"), extra=(override,))

    assert plan.page_archetypes == (PageArchetype(tier="high", patterns=("*/stockists*",)),)
    assert len(plan.overrides) == 1
    record = plan.overrides[0]
    assert record.field is PlanField.PAGE_ARCHETYPES
    assert record.by_module is StrategyModuleId.PRODUCT_OVERLAY
    assert record.by_revision == "overlay-v1"
    assert record.overridden_module is StrategyModuleId.SCENARIO
    assert record.overridden_revision
    assert plan.provenance_for(PlanField.PAGE_ARCHETYPES).revision == "overlay-v1"


def test_an_override_does_not_depend_on_the_order_it_arrived_in(bundle: ProfileBundle) -> None:
    """谁赢由**表**决定，不由贡献传来的先后决定。

    判据把同一组贡献按相反的顺序接上，要求计划逐字相同。少了这条，「更具体的
    赢」会变成「后写的赢」，而胜者会随组合根里模块的排列而变 —— 那不是一条
    写下来的规则，也就不是一件可解释的事。

    覆盖者**必须真的写那一格**，否则这条判据量不到东西：一个不贡献任何格的模块
    无论排在哪，两份计划当然逐字相同。它这里故意不看上下文（判据要量的是接线，
    不是「什么条件下覆盖者才开口」—— 那是具体模块那一层的判据）。
    """
    overlay = _Module(
        StrategyModuleId.PRODUCT_OVERLAY,
        contributions=(
            _contribution(
                PlanField.PAGE_ARCHETYPES,
                archetypes=(PageArchetype(tier="high", patterns=("*/stockists*",)),),
                reason="挂了产品用途时先看进货页",
            ),
        ),
    )
    forward = StrategyCompiler(modules=(*_modules(), overlay))
    # 把覆盖模块接到**最前面**：它与其余模块的先后由表决定，与这份元组的排列无关。
    backward = StrategyCompiler(modules=(overlay, *_modules()))
    assert forward.modules != backward.modules
    first = forward.compile(bundle=bundle, context=_context(), envelope=envelope_of(bundle))
    second = backward.compile(bundle=bundle, context=_context(), envelope=envelope_of(bundle))
    # 先钉住「更具体的那个真的赢了」：少了这一条，两份计划一起没赢也会逐字相同。
    assert first.page_archetypes == (PageArchetype(tier="high", patterns=("*/stockists*",)),)
    assert first == second


def test_the_provenance_reason_belongs_to_the_winner(bundle: ProfileBundle) -> None:
    """覆盖之后，那一格的出处写的是**赢家**的理由与版本。

    一条没被采用的贡献的理由留在溯源里，比没有溯源更坏：它读起来像一条解释，
    而它解释的是另一条贡献。
    """
    override = _Module(
        StrategyModuleId.WORKSTREAM_OVERRIDE,
        contributions=(
            _contribution(
                PlanField.ROLE_PRIORITIES,
                values=("procurement",),
                reason="这个工作流先看采购",
            ),
        ),
    )
    plan = _compile(bundle, extra=(override,))
    entry = plan.provenance_for(PlanField.ROLE_PRIORITIES)
    assert entry.module_id == StrategyModuleId.WORKSTREAM_OVERRIDE.value
    assert entry.reason == "这个工作流先看采购"
    assert plan.role_priorities == ("procurement",)


def test_the_same_module_writing_one_field_twice_is_a_wiring_error(
    bundle: ProfileBundle,
) -> None:
    """同一个模块对同一格贡献两次 → 拒，不按「后写的赢」过去。

    这与覆盖长得一样（同一格有第二个写法）而处置相反：覆盖是一次留痕的决定，
    重复是一次接线错误。让后者按传来的先后决胜，赢家会随组合根的排列而变。
    """
    twice = _Module(
        StrategyModuleId.SCENARIO,
        contributions=(
            *tuple(
                item
                for item in _default_cells(StrategyModuleId.SCENARIO)
                if item.field is not PlanField.ROLE_FUNCTIONS
            ),
            _contribution(PlanField.ROLE_FUNCTIONS, values=("operations",), reason="先这样写"),
            _contribution(PlanField.ROLE_FUNCTIONS, values=("finance",), reason="又想了想"),
        ),
    )
    with pytest.raises(ConfigError, match="贡献了不止一次"):
        _compile(bundle, replace={StrategyModuleId.SCENARIO: twice})


def test_a_missing_field_is_a_wiring_error_and_names_the_missing_cells(
    bundle: ProfileBundle,
) -> None:
    """缺一格 → 抛，且回执点出缺的是哪几格。

    留空不退回声明默认值：留空的具体形态是一个空元组，而它在计划上读作
    「这一格今天没有内容」—— 于是「不同 context 得到不同策略」这个问题会永远
    答「变了」，而没有任何一处红。判据刻意只接一个模块，让缺的那几格是**算出来**的。
    """
    only_scenario = StrategyCompiler(modules=(_Module(StrategyModuleId.SCENARIO),))
    with pytest.raises(ConfigError, match="没有模块写") as caught:
        only_scenario.compile(bundle=bundle, context=_context(), envelope=envelope_of(bundle))
    message = str(caught.value)
    assert PlanField.LOCALES.value in message
    assert PlanField.DECISION_REFS.value in message


def test_an_empty_module_set_cannot_compile_anything(bundle: ProfileBundle) -> None:
    """一个模块都不接，八格全缺 → 抛（同一条判据的极端情形）。"""
    with pytest.raises(ConfigError, match="没有模块写"):
        StrategyCompiler(modules=()).compile(
            bundle=bundle, context=_context(), envelope=envelope_of(bundle)
        )


def test_a_wired_module_that_contributes_nothing_is_refused(bundle: ProfileBundle) -> None:
    """接了一个**一格子都不交**的模块 → 抛，点它的名。

    它的版本只能从它自己交出来的贡献上取（同一份声明只读一次，
    ``marketops/domain/research_plan.py::_module_revisions``），所以零格意味着它在这一栏
    上无话可说。悄悄少一行的话，报告上读作「这次参与的模块少了一个」—— 而那两种情况
    （这个模块今天还没有声明来源 / 它的 ``contribute`` 与 ``cells`` 分叉了）都要人去看，
    不是一次可以照记的读数。

    ``PRODUCT_OVERLAY`` 正好是这个形状：它是一个真的模块 id，今天**没有声明来源**，
    所以默认格集就是空的（``_default_cells`` 那句注释）。反控在下面：同一个模块只要交
    一格出来，这一栏就有它。
    """
    silent = _Module(StrategyModuleId.PRODUCT_OVERLAY)
    assert silent.contribute(
        context=_context(), envelope=envelope_of(bundle), bundle=bundle
    ) == (), "夹具的前提：这个模块默认交零格"

    with pytest.raises(ConfigError, match=r"\['product_overlay'\]"):
        _compile(bundle, extra=(silent,))

    speaking = _Module(
        StrategyModuleId.PRODUCT_OVERLAY,
        contributions=(
            _contribution(
                PlanField.PAGE_ARCHETYPES,
                archetypes=(PageArchetype(tier="high", patterns=("*/product*",)),),
                reason="产品叠加层只看这一档",
            ),
        ),
    )
    plan = _compile(bundle, extra=(speaking,))
    assert [item.module_id for item in plan.modules][-1] is StrategyModuleId.PRODUCT_OVERLAY


def test_a_contribution_must_carry_exactly_one_shape() -> None:
    """``values`` 与 ``archetypes`` 恰好一个非空。

    两个都空是「贡献了一格空气」（那一格看着像没人写），两个都填是「一格有两个
    值」—— 后者要求读的人自己决定谁赢，而那份决定该写在 ``FIELD_OVERRIDERS`` 上。
    """
    with pytest.raises(ValidationError, match="恰好有一种取值形状"):
        _contribution(PlanField.ROLE_FUNCTIONS)
    with pytest.raises(ValidationError, match="恰好有一种取值形状"):
        _contribution(
            PlanField.PAGE_ARCHETYPES,
            values=("operations",),
            archetypes=(PageArchetype(tier="high", patterns=("*/a*",)),),
        )


def test_a_contribution_without_a_reason_or_a_revision_cannot_be_built() -> None:
    """出处与理由都必须填 —— 这一层唯一的产品就是「可解释」。"""
    with pytest.raises(ValidationError):
        _contribution(PlanField.ROLE_FUNCTIONS, values=("operations",), reason="   ")
    with pytest.raises(ValidationError):
        PolicyContribution(
            module_id=StrategyModuleId.SCENARIO,
            revision="",
            field=PlanField.ROLE_FUNCTIONS,
            values=("operations",),
            reason="有理由",
        )


def test_a_provenance_entry_cannot_name_a_module_that_does_not_exist() -> None:
    """溯源里的 ``module_id`` 是闭集（外加声明层那一个名字）。

    一个自由字符串的 ``module_id`` 会让「这一格是谁写的」在报告上看起来总是有
    答案 —— 而答案是拼错的模块名，或者一个早就删掉的模块。
    """
    with pytest.raises(ValidationError, match="不在闭集里"):
        PolicyProvenance(
            field=PlanField.LOCALES,
            module_id="some_module",
            revision="r1",
            reason="有理由",
        )
    # 正控：声明层那个名字是允许的（它不是一个模块，但它必须能出现在溯源里）。
    assert (
        PolicyProvenance(
            field=PlanField.GEO,
            module_id=DECLARED_SCOPE,
            revision="r1",
            reason="有理由",
        ).module_id
        == DECLARED_SCOPE
    )


def _revalidated(plan: EffectiveResearchPlan, **update: object) -> EffectiveResearchPlan:
    """把一份**已经编好的**计划改几格再验一遍。

    走 :meth:`model_validate` 而不是 ``model_copy`` —— 后者刻意不重跑判据，
    而这一组判据要问的正是「构造期肯不肯放它过去」。
    """
    return EffectiveResearchPlan.model_validate({**plan.model_dump(), **update})


def test_a_plan_needs_exactly_one_origin_per_field(bundle: ProfileBundle) -> None:
    """每一格恰好一条出处 —— 多一条与少一条都拒。

    少一条读作「没人管」，多一条读作「两处都在管」。两者都会让
    ``provenance_for`` 退化成一个「取第一条」的函数，而它取到的是哪一条取决于
    构造顺序 —— 那是这一层要消掉的东西在最后一步又长回来。
    """
    plan = _compile(bundle)
    assert len(plan.provenance) == len(PlanField)
    with pytest.raises(ValidationError, match="没有出处"):
        _revalidated(
            plan,
            provenance=tuple(
                entry for entry in plan.provenance if entry.field is not PlanField.GEO
            ),
        )
    with pytest.raises(ValidationError, match="重复出处"):
        _revalidated(plan, provenance=(*plan.provenance, plan.provenance[0]))


# --- 5. 三道编译门（都在拼装之前） ------------------------------------------


def test_a_hard_ambiguity_stops_the_compilation(bundle: ProfileBundle) -> None:
    """硬歧义不编 —— 而且**在模块被调用之前**就停。

    关闭条件 4：模糊输入影响 hard scope 时明确 hold/review，不能由模型置信度
    越过。判据看两件事：它抛，以及那四个模块一次都没被调用过（``seen`` 为空）——
    后半是「三件判断都在动手拼装之前」的执行器。一份编到一半的计划更危险：
    它有摘要、有出处、读起来完整，而它描述的是没有人批准过的一次研究。
    """
    modules = _modules()
    compiler = StrategyCompiler(modules=modules)
    ambiguous = _context(
        scenario_candidates=(
            ScenarioCandidate(
                name="a",
                reading="k12",
                provenance=ScenarioProvenance.DECLARED,
                source="req-1",
            ),
            ScenarioCandidate(
                name="b",
                reading="higher_ed",
                provenance=ScenarioProvenance.POOL_FACT,
                source="pool-9",
            ),
        )
    )
    with pytest.raises(ValueError, match="讲不清"):
        compiler.compile(bundle=bundle, context=ambiguous, envelope=envelope_of(bundle))
    assert all(module.seen == [] for module in modules)


def test_a_soft_ambiguity_compiles_and_is_recorded_on_the_plan(
    bundle: ProfileBundle,
) -> None:
    """软歧义照编，而且**记在计划上**。

    §B16 逐字：「多个候选且差异只影响低风险搜索顺序：可在批准 envelope 内编译
    保守计划并记录候选」。记下来是因为这份计划会随 WorkItem 冻结：一份不说自己
    是在哪几处模糊之上编出来的计划，会把「这次按保守取法多问了一遍」读成
    「这次本来就该这么问」。
    """
    named = Ambiguity(dimension=ContextDimension.LOCALES, detail="两种语言都说得通")
    plan = _compile(bundle, _context(declared_ambiguities=(named,)))
    assert plan.ambiguities == (named,)
    assert plan.provenance_for(PlanField.LOCALES).module_id != DECLARED_SCOPE


def test_every_ambiguity_on_a_compiled_plan_is_soft(bundle: ProfileBundle) -> None:
    """编出来的计划上不会留着一条硬歧义。

    硬的那几维按构造会把编译拦住，所以这条判据红的方式只有一种：门漏了。
    写下来是因为「计划上带着一条硬歧义」在现场的样子完全正常 —— 它只是一条
    记录，而那份计划已经跑起来了。
    """
    plan = _compile(
        bundle,
        _context(
            declared_ambiguities=(
                Ambiguity(dimension=ContextDimension.LOCALES, detail="软的一条"),
                Ambiguity(dimension=ContextDimension.EVIDENCE_REQUIREMENTS, detail="软的又一条"),
            )
        ),
    )
    assert plan.ambiguities
    assert not any(ambiguity_is_hard(item.dimension) for item in plan.ambiguities)


def test_an_out_of_envelope_request_is_refused_and_not_narrowed(
    bundle: ProfileBundle,
) -> None:
    """越界不编、也不「改小一点再编」，而且同样在模块被调用之前停。

    这是与歧义方向相反的失败：讲得太清楚，但讲到了批准范围的外面。处置是让人
    批一次，不是把那一格改小 —— 改小之后的计划看起来一切正常，而它研究的东西
    不是客户要的东西。
    """
    modules = _modules()
    compiler = StrategyCompiler(modules=modules)
    with pytest.raises(ConfigError, match="已批准的范围之外") as caught:
        compiler.compile(
            bundle=bundle, context=_context(geo="DE"), envelope=envelope_of(bundle)
        )
    assert "治理" in str(caught.value)
    assert all(module.seen == [] for module in modules)


def test_a_clean_request_compiles_into_a_plan_with_no_overrides(
    bundle: ProfileBundle,
) -> None:
    """一个干净的请求编出一份计划：每一格有出处、没有被覆盖的、摘要非空。"""
    plan = _compile(bundle)
    assert plan.overrides == ()
    assert plan.modules
    assert plan.digest
    assert {entry.field for entry in plan.provenance} == set(PlanField)


def test_the_compiler_hands_the_modules_exactly_three_inputs(bundle: ProfileBundle) -> None:
    """模块只能拿到 context / envelope / bundle —— 而且是**调用方那一份**。

    后半是这一条真正的力气所在：编译器若在内部重推一份 envelope（例如从 bundle
    再读一次），模块看到的是「此刻盘上的声明」而不是「这次编译批准的那一份」——
    两者的差别只在有人刚刚改了盘上的声明时才出现，而那正是关闭条件 6 要防的场景。
    这条判据用对象身份钉住它，所以它不依赖任何时序。
    """
    context = _context()
    envelope = envelope_of(bundle)
    modules = _modules()
    StrategyCompiler(modules=modules).compile(bundle=bundle, context=context, envelope=envelope)

    for module in modules:
        assert len(module.seen) == 1, f"{module.module_id.value} 被调用了 {len(module.seen)} 次"
        seen = module.seen[0]
        assert set(seen) == {"context", "envelope", "bundle"}, "模块拿到了第三个输入"
        assert seen["context"] is context
        assert seen["envelope"] is envelope
        assert seen["bundle"] is bundle


# --- 6. 这一层够不到 provider，也不认识任何具体模块（结构判据） --------------


def test_the_module_cannot_reach_a_provider() -> None:
    """AST 扫描：这个模块 import 的东西里没有一个住在 adapters / ports / application。

    关闭条件 1 那句「preview 零 provider 调用」在这一层不是纪律、也不是替身记出来
    的 0 —— 是**没有可以调用的东西**。一个需要注入端口才能编计划的编译器，会在
    接线那一天由「谁忘了传端口」决定 preview 花不花钱。
    """
    tree = ast.parse(MODULE.read_text(encoding="utf-8"))
    imported: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.append("." * node.level + node.module)

    forbidden = tuple(
        name
        for name in imported
        if name.startswith("marketops")
        or ".adapters" in name
        or ".ports" in name
        or ".application" in name
    )
    assert not forbidden, f"这一层不得够到 provider：{forbidden}"
    # 正控：扫描确实读到了东西（空 import 列表会让上面那条恒绿）。
    assert imported, "扫描没读到任何 import —— 判据在空转"


def test_this_module_defines_no_concrete_strategy_module() -> None:
    """这一片里没有任何一个具体的策略模块 —— 定义 ``contribute`` 的类只有协议自己。

    编译器只认识 :class:`StrategyModule` 这个形状，六个真模块在另一片里。这条判据
    按类扫：有 ``contribute`` 方法定义的类只能有一个，就是那个协议。它红的方式是
    「有人把一个具体模块顺手写在编译器旁边」—— 那一刻 ``modules`` 这个参数就从
    「组合根接什么就编什么」变成了「反正有默认的那几个」。
    """
    tree = ast.parse(MODULE.read_text(encoding="utf-8"))
    definers = {
        node.name
        for node in ast.walk(tree)
        if isinstance(node, ast.ClassDef)
        and any(
            isinstance(item, ast.FunctionDef | ast.AsyncFunctionDef) and item.name == "contribute"
            for item in node.body
        )
    }
    assert definers == {"StrategyModule"}, f"这一层不该有具体策略模块，却扫到：{definers}"


def test_the_plan_is_only_constructed_by_its_own_builder() -> None:
    """``EffectiveResearchPlan(...)`` 全仓只有 ``research_plan.py`` 一处。

    与 ``SceneFingerprint`` 那条同一个形状：一个能在别处构造的计划，迟早会被
    当成「我也可以拼一份」的接口 —— 而绕过 :data:`FIELD_OWNER` 拼出来的计划
    没有出处，于是「这条顺序是谁定的」在它上面没有答案，但它有摘要、有场景版本，
    看起来与一份真的计划一模一样。
    """
    offenders: list[str] = []
    for path in sorted((REPO_ROOT / "marketops").rglob("*.py")):
        if path == MODULE:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            name = func.id if isinstance(func, ast.Name) else getattr(func, "attr", "")
            if name == "EffectiveResearchPlan":
                offenders.append(f"{path.relative_to(REPO_ROOT)}:{node.lineno}")
    assert not offenders, f"计划只该由 build_plan 拼出来，这些地方在自己造：{offenders}"
