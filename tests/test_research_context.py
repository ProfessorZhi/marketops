"""B16 输入面的判据：一份声明讲清楚了没有，以及讲清楚之后它长什么样。

这一片的承重句子只有两句，两条判据各钉一句：

1. **置信度越不过硬那一半。** §B16 逐字：「模糊输入影响 hard scope 时明确
   hold/review；**不能由模型置信度越过**」。所以这里不是断言「高置信度也不放行」
   —— 是断言**把每一条候选的置信度从 0 改到 1，歧义清单逐字不变**。前者是一句
   关于某个数值的话，后者是一句关于「置信度根本不在输入里」的话。
2. **摘要盖住它声称盖住的那几格。** 十个字段逐个改一个字，摘要各换一次；换
   顺序、换重复值，摘要不变。少了后一半，「可复现」是一句没人执行的话；少了
   前一半，摘要可能在盖住一个与内容无关的东西。

## 这份文件里没有 provider

一条也没有 —— 这一层收不到端口（见模块 docstring）。所以这里不需要
``RecordingSearch`` 那类替身：B16 关闭条件 1 的「preview 零 provider 调用」在
这一层是**结构上成立**的，而它成立的方式由 ``test_the_module_cannot_reach_a_provider``
钉住（AST 扫描，不是运行期断言）。
"""

from __future__ import annotations

import ast
from itertools import product
from pathlib import Path

import pytest
from pydantic import ValidationError

from marketops.domain.profile import ProfileBundle
from marketops.domain.research_context import (
    _CREDIBLE_PROVENANCE,
    _ENFORCEABLE_DIMENSIONS,
    _HARD_AMBIGUITY_DIMENSIONS,
    _SOFT_AMBIGUITY_DIMENSIONS,
    Ambiguity,
    ContextDimension,
    HardConstraint,
    ResearchContext,
    ResearchEnvelope,
    ScenarioCandidate,
    ScenarioProvenance,
    ambiguities_of,
    ambiguity_is_hard,
    constraints_out_of_envelope,
    credible_scenario_readings,
    effective_scenario,
    envelope_of,
    fingerprint_of,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
MODULE = REPO_ROOT / "marketops" / "domain" / "research_context.py"

#: 一个**讲清楚了**的上下文。每条判据只改它要量的那一格。
#:
#: 取值刻意是市场中性的（``school`` / ``pitch`` / ``trust``）—— 这一层不得知道
#: 任何一个真实市场（``tests/test_no_hardcoded_market_knowledge.py`` 扫的是
#: ``marketops/``，本文件不被扫；但把市场词写进判据里会让它读起来像在测那个市场）。
def _context(**overrides: object) -> ResearchContext:
    base: dict[str, object] = {
        "geo": "GB",
        "entity_classes": ("school",),
        "asset_classes": ("pitch",),
        "organization_classes": ("trust",),
        "scenario_candidates": (_candidate("secondary school", "k12"),),
        "objective": "find accounts for the approved offer",
        "evidence_requirements": ("official_site",),
    }
    base.update(overrides)
    return ResearchContext(**base)  # type: ignore[arg-type]


def _candidate(
    name: str = "secondary school",
    reading: str = "k12",
    provenance: ScenarioProvenance = ScenarioProvenance.DECLARED,
    confidence: float = 0.0,
) -> ScenarioCandidate:
    return ScenarioCandidate(
        name=name, reading=reading, provenance=provenance, source="req-1", confidence=confidence
    )


def _ambiguity_key(context: ResearchContext) -> tuple[tuple[str, str], ...]:
    """歧义清单的**规范形状** —— 用来做逐字比较，不解析散文。

    ``detail`` 一并进比较：两条歧义的维度相同而说明不同，也是「变了」。
    """
    return tuple((a.dimension.value, a.detail) for a in ambiguities_of(context))


# --- 1. 闭集与分区 -----------------------------------------------------------


def test_the_two_ambiguity_tables_partition_the_closed_set() -> None:
    """硬软两张表的并集恰好是全部维度，且不相交。

    少了这条，加一个维度成员而忘了归类时它会**静默算成软的**（两张表都没说它是
    硬的），于是一条「换国家」的歧义会走进保守计划 —— 而保守计划不会拒绝编译。
    """
    every = set(ContextDimension)
    assert _HARD_AMBIGUITY_DIMENSIONS | _SOFT_AMBIGUITY_DIMENSIONS == every
    assert not (_HARD_AMBIGUITY_DIMENSIONS & _SOFT_AMBIGUITY_DIMENSIONS)


@pytest.mark.parametrize("dimension", sorted(ContextDimension, key=lambda d: d.value))
def test_every_dimension_gets_a_verdict(dimension: ContextDimension) -> None:
    """每一维都答得出「硬还是软」，没有一个掉进默认分支。"""
    assert ambiguity_is_hard(dimension) is (dimension in _HARD_AMBIGUITY_DIMENSIONS)


#: 「这一维可以判越界」这句话的依据是 **envelope 上有答它的格**。逐维点名，
#: 于是「加一维进 ``_ENFORCEABLE_DIMENSIONS`` 却没给 envelope 加字段」会红。
_ENVELOPE_FIELDS_FOR: dict[ContextDimension, tuple[str, ...]] = {
    ContextDimension.GEO: ("geo",),
    ContextDimension.JURISDICTION: ("jurisdictions",),
    ContextDimension.LOCALES: ("locales",),
}


def test_every_enforceable_dimension_has_something_on_the_envelope_to_answer_it() -> None:
    """能判越界的每一维，envelope 上都有一格在答它。

    这一条**不**声称「可判的维度必须是硬的」—— 那是错的，而且方向恰恰相反：
    ``LOCALES`` 可判（请求多加一种没声明过的语言会被拒），而它的歧义按软处置
    （§B16：「页面级语言检测只影响查询/解析/翻译策略」）。一句「越界就拒」与一句
    「歧义就停下来问人」是两件事，把它们写成同一个门槛会让一次收窄失败被当成
    一次需要人来裁决的模糊。
    """
    assert set(_ENVELOPE_FIELDS_FOR) == _ENFORCEABLE_DIMENSIONS
    for dimension, fields in _ENVELOPE_FIELDS_FOR.items():
        missing = [name for name in fields if name not in ResearchEnvelope.model_fields]
        assert not missing, f"{dimension.value} 声称可判，而 envelope 上没有这些格：{missing}"


def test_the_dimensions_that_can_produce_an_ambiguity_today_are_these() -> None:
    """今天真的会产出歧义的维度 —— 一个**读数**，不是一张愿望清单。

    全部留空时能产出歧义的是这几维；``JURISDICTION`` / ``LOCALES`` /
    ``PRODUCT_USE`` 不产出，各有各的理由写在 :class:`ResearchContext` 的类
    docstring 里（管辖空着退回 geo、语言空着退回 profile 的声明、产品用途是可选的）。

    这条与「没有生产者」那类判据同一个用途：把「这一维按软处置」与「这一维今天
    根本不出歧义」分开。少了它，一张把每一维都标了等级却没有任何生产者的表会
    读起来像一套在跑的机制。
    """
    context = ResearchContext()
    produced = {a.dimension for a in ambiguities_of(context)}
    assert produced == {
        ContextDimension.GEO,
        ContextDimension.ENTITY_CLASSES,
        ContextDimension.ASSET_CLASSES,
        ContextDimension.ORGANIZATION_CLASSES,
        ContextDimension.SCENARIO,
        ContextDimension.OBJECTIVE,
        ContextDimension.EVIDENCE_REQUIREMENTS,
    }
    silent = set(ContextDimension) - produced
    assert silent == {
        ContextDimension.JURISDICTION,
        ContextDimension.LOCALES,
        ContextDimension.PRODUCT_USE,
    }


# --- 2. 置信度越不过硬那一半 -------------------------------------------------


def test_a_high_confidence_guess_does_not_settle_the_scenario() -> None:
    """只有模型猜测时，置信度再高也不定场景 —— 而且它是一条**硬**歧义。

    这一条钉的是 §B16 那句「历史模型猜测不能自动扩大范围」。变异方向：让
    ``ambiguities_of`` 收下 ``MODEL_GUESS``（或按置信度取最高的一条），这一条
    会红在「期望硬歧义、却拿到了会编译的上下文」上。
    """
    context = _context(
        scenario_candidates=(
            _candidate("secondary school", "k12", ScenarioProvenance.MODEL_GUESS, confidence=1.0),
        )
    )
    scenario = [a for a in ambiguities_of(context) if a.dimension is ContextDimension.SCENARIO]
    assert len(scenario) == 1
    assert ambiguity_is_hard(ContextDimension.SCENARIO)
    with pytest.raises(ValueError, match="推不出指纹"):
        fingerprint_of(context)


def test_two_credible_readings_are_an_ambiguity_and_confidence_is_not_an_input() -> None:
    """两条可信读法 = 一条歧义；而且把**每一条**置信度从 0 改到 1，清单逐字不变。

    这是本片最承重的一条。它问的不是「某个高置信度被拦住了」，而是「置信度根本
    不在这个函数的输入里」—— 前者可以用一条 ``if confidence > 0.8: raise`` 满足，
    后者不能。变异方向：让 ``ambiguities_of`` 在两条读法里挑置信度高的那一条，
    下面这个矩阵里**每一个非零取值**都会红。
    """
    readings = ("k12", "further_education")
    baseline: tuple[tuple[str, str], ...] | None = None

    for first, second in product((0.0, 0.25, 0.5, 0.75, 1.0), repeat=2):
        context = _context(
            scenario_candidates=(
                _candidate("school", readings[0], ScenarioProvenance.DECLARED, confidence=first),
                _candidate("college", readings[1], ScenarioProvenance.POOL_FACT, confidence=second),
            )
        )
        key = _ambiguity_key(context)
        scenario = [a for a in ambiguities_of(context) if a.dimension is ContextDimension.SCENARIO]
        assert len(scenario) == 1, f"两条读法应当恰是一条歧义，得到 {scenario}"
        assert readings[0] in scenario[0].detail and readings[1] in scenario[0].detail
        if baseline is None:
            baseline = key
        else:
            assert key == baseline, (
                f"置信度取 ({first}, {second}) 时歧义清单变了 —— 说明它进了输入：\n"
                f"{key}\n{baseline}"
            )


def test_two_names_for_one_reading_are_not_an_ambiguity() -> None:
    """两条名字不同、读法相同的候选**不是**歧义。

    判据是 ``reading``（落到 profile 词表上的那一个值），不是 ``name``。合成一格
    时，「同一件事的两种说法」会看起来像「两件不同的事」，而拒绝编译的理由会指向
    一个其实不冲突的输入 —— 那时人会去改一份本来正确的声明。
    """
    context = _context(
        scenario_candidates=(
            _candidate("secondary school", "k12"),
            _candidate("high school", "k12", ScenarioProvenance.POOL_FACT),
        )
    )
    assert not [a for a in ambiguities_of(context) if a.dimension is ContextDimension.SCENARIO]
    assert fingerprint_of(context).scenario == "k12"


#: 「场景这一格怎么读」的全部形状。**期望值是判词，不是从实现里抄的**：说不出来
#: 就是 ``None``（那是一条硬歧义），说得出来就是那一条读法。
_SCENARIO_READINGS: tuple[tuple[str, tuple[ScenarioCandidate, ...], str | None], ...] = (
    ("没人说", (), None),
    ("只有模型猜的", (_candidate("secondary school", "k12", ScenarioProvenance.MODEL_GUESS),), None),
    ("恰好一条", (_candidate("secondary school", "k12"),), "k12"),
    (
        "同一件事的两种说法",
        (
            _candidate("secondary school", "k12"),
            _candidate("high school", "k12", ScenarioProvenance.POOL_FACT),
        ),
        "k12",
    ),
    (
        "两种读法",
        (_candidate("secondary school", "k12"), _candidate("golf club", "golf_course")),
        None,
    ),
    (
        "一条可信加一条猜的",
        (
            _candidate("secondary school", "k12"),
            _candidate("golf club", "golf_course", ScenarioProvenance.MODEL_GUESS),
        ),
        "k12",
    ),
)


@pytest.mark.parametrize(
    "label,candidates,expected",
    _SCENARIO_READINGS,
    ids=[case[0] for case in _SCENARIO_READINGS],
)
def test_the_two_readers_of_the_scenario_table_agree(
    label: str, candidates: tuple[ScenarioCandidate, ...], expected: str | None
) -> None:
    """``ambiguities_of`` 与 ``effective_scenario`` 必须对**同一张表**给出一致的答案。

    这一条钉的不是「某个输入落到某个值」，而是「两处读的是同一句判据」：
    ``effective_scenario`` 答得出来**当且仅当** ``ambiguities_of`` 没有在 SCENARIO
    上记一条歧义；而它答出来的那一条，必须正是后者数出来的那一条。

    为什么单列一条：**六组里五组的答案与判据是否被复制无关** —— 只有「同一件事的
    两种说法」那一组会分叉（把去重去掉的实现在那里说「有两条」，而就地取第一条的
    实现在那里看似答对、却在「只有一条模型猜的」那一组上给出一条本来不算数的读法）。
    把这两组拆开看，没有一条判据会红；合起来才是一句关于「同一处定义」的话。

    ``SCENARIO`` 是硬的（:func:`ambiguity_is_hard`），所以「说不出场景」的处置是
    停下来问人 —— 这一条同时断言 ``effective_scenario`` 在那几组上**抛**，而不是
    挑一条继续跑。
    """
    context = _context(scenario_candidates=candidates)
    recorded = [a for a in ambiguities_of(context) if a.dimension is ContextDimension.SCENARIO]
    if expected is None:
        assert recorded, f"{label}：说不出场景，歧义却没记下来"
        with pytest.raises(ValueError):
            effective_scenario(context)
        return
    assert not recorded, f"{label}：说得出来，歧义却记了下来（{recorded[0].detail}）"
    assert credible_scenario_readings(context) == (expected,)
    assert effective_scenario(context) == expected


# --- 3. 指纹只在讲清楚之后推得出来 -------------------------------------------


@pytest.mark.parametrize(
    "overrides",
    [
        {"geo": ""},
        {"entity_classes": ()},
        {"asset_classes": ()},
        {"organization_classes": ()},
        {"scenario_candidates": ()},
        {"objective": ""},
        {"product_use": "an offer", "scenario_candidates": ()},
    ],
    ids=["geo", "entity", "asset", "organization", "scenario", "objective", "inside-a-guess"],
)
def test_no_hard_ambiguity_survives_the_fingerprint_gate(overrides: dict[str, object]) -> None:
    """**每一条**硬歧义都拦得住指纹 —— 逐个维度打一遍，不是抽一条。

    抽一条时，「门只对那一维关着」与「门对所有硬维都关着」在判据上同形。
    """
    context = _context(**overrides)
    hard = [a for a in ambiguities_of(context) if ambiguity_is_hard(a.dimension)]
    assert hard, f"{overrides} 应当产出一条硬歧义"
    with pytest.raises(ValueError) as caught:
        fingerprint_of(context)
    message = str(caught.value)
    for ambiguity in hard:
        assert f"[{ambiguity.dimension.value}]" in message


def test_a_soft_ambiguity_does_not_block_the_fingerprint() -> None:
    """软歧义不拦 —— 那正是「保守计划 + 记下候选」的场景。

    与上一条配对：少了它，「门对所有歧义都关着」也满足上一条，而那会把
    §B16 的「可在批准 envelope 内编译保守计划并记录候选」变成「一律拒绝」。
    """
    context = _context(evidence_requirements=())
    soft = [a for a in ambiguities_of(context) if not ambiguity_is_hard(a.dimension)]
    assert [a.dimension for a in soft] == [ContextDimension.EVIDENCE_REQUIREMENTS]
    assert fingerprint_of(context).evidence_requirements == ()


# --- 4. 摘要的稳定性与敏感性 -------------------------------------------------


#: 十个字段各一个「改了它」的上下文。与 ``SceneFingerprint`` 的字段一一对应 ——
#: 少一个字段的用例，那个字段就可能整个不在摘要里而没人发现。
_DIGEST_SENSITIVITY: list[tuple[str, dict[str, object]]] = [
    ("geo", {"geo": "IE"}),
    ("jurisdiction", {"jurisdiction": "IE"}),
    ("locales", {"candidate_locales": ("en-GB",)}),
    ("entity_classes", {"entity_classes": ("school", "college")}),
    ("asset_classes", {"asset_classes": ("pitch", "track")}),
    ("organization_classes", {"organization_classes": ("trust", "council")}),
    ("scenario", {"scenario_candidates": (_candidate("college", "further_education"),)}),
    ("objective", {"objective": "find accounts for the approved offer."}),
    ("product_use", {"product_use": "approved-offer-v2"}),
    ("evidence_requirements", {"evidence_requirements": ("official_site", "registry")}),
]


@pytest.mark.parametrize("label,overrides", _DIGEST_SENSITIVITY, ids=[c[0] for c in _DIGEST_SENSITIVITY])
def test_every_fingerprint_field_is_in_the_digest(label: str, overrides: dict[str, object]) -> None:
    """改任何一格，摘要都换一个值。

    这一条问的是摘要**盖住了什么**。一个只散了一部分字段的摘要会在两份不同的声明
    上给出同一个值 —— 而「同一自然语言任务稳定编译可复现 plan digest」这句话
    正是靠这个值成立的，它错的时候两件事看起来完全一样。
    """
    assert fingerprint_of(_context()).digest != fingerprint_of(_context(**overrides)).digest, label


#: 每一对是**同一份声明**的两种写法：换个顺序、或者写重一个值。
#:
#: 逐对显式写出来而不是「基准 vs 覆盖」，是因为覆盖很容易顺手把**内容**也改了
#: —— 那时这一条会红在一个与顺序无关的地方，而红的样子和「顺序真的进摘要了」
#: 一模一样。
_SAME_DECLARATION_TWICE: list[tuple[str, dict[str, object], dict[str, object]]] = [
    (
        "entity_classes-reordered",
        {"entity_classes": ("school", "college")},
        {"entity_classes": ("college", "school")},
    ),
    (
        "asset_classes-repeated",
        {"asset_classes": ("pitch", "track")},
        {"asset_classes": ("track", "pitch", "pitch")},
    ),
    (
        "locales-reordered",
        {"candidate_locales": ("en-GB", "fr-FR")},
        {"candidate_locales": ("fr-FR", "en-GB")},
    ),
    (
        "evidence_requirements-repeated",
        {"evidence_requirements": ("official_site", "registry")},
        {"evidence_requirements": ("registry", "official_site", "official_site")},
    ),
]


@pytest.mark.parametrize(
    "left,right", [(a, b) for _, a, b in _SAME_DECLARATION_TWICE], ids=[c[0] for c in _SAME_DECLARATION_TWICE]
)
def test_set_like_fields_do_not_move_the_digest(
    left: dict[str, object], right: dict[str, object]
) -> None:
    """集合格换顺序、写重一个值，摘要不变。

    声明里换了个顺序不是「换了一份声明」。这一条与上一条配对：只有上一条时，
    一个把整份声明原样散掉的摘要也满足它；只有这一条时，一个不散任何东西的常量
    摘要也满足它。
    """
    assert fingerprint_of(_context(**left)).digest == fingerprint_of(_context(**right)).digest


def test_the_digest_is_stable_across_repeated_derivation() -> None:
    """同一份上下文推两次，逐字同一个摘要 —— 可复现的最小含义。"""
    context = _context()
    assert fingerprint_of(context).digest == fingerprint_of(context).digest


def test_an_empty_jurisdiction_and_one_naming_the_geo_are_the_same_declaration() -> None:
    """``jurisdiction`` 空着与写成 ``geo`` 是**同一份**声明，摘要也同一个。

    两处都做这条回退（:func:`fingerprint_of` 与 :func:`constraints_out_of_envelope`），
    所以这里判的是「回退真的发生了」：少了这一条，一个把空字符串原样散进摘要的
    实现能满足上面**每一条**摘要判据（换顺序那些仍绿、逐个字段那些仍绿），而在
    现场表现为「同一个部署的同一份声明，因为客人今天写没写那一格，得到两个计划
    摘要」。
    """
    implicit = fingerprint_of(_context(jurisdiction=""))
    explicit = fingerprint_of(_context(jurisdiction="GB"))
    assert implicit.jurisdiction == "GB"
    assert implicit.digest == explicit.digest


# --- 5. 越界 -----------------------------------------------------------------


def test_a_request_for_another_country_is_out_of_envelope(bundle: ProfileBundle) -> None:
    """换成另一个国家 → 一条 GEO 越界，回执里两个值都在。"""
    envelope = envelope_of(bundle)
    found = constraints_out_of_envelope(_context(geo="FR"), envelope)
    assert [f.dimension for f in found] == [ContextDimension.GEO]
    assert found[0].requested == ("FR",)
    assert found[0].allowed == (envelope.geo,)


def test_narrowing_the_locales_is_allowed_but_adding_one_is_not(
    bundle: ProfileBundle,
) -> None:
    """收窄合法、新增越界 —— 两个方向各打一次。

    只打越界那一半时，「这一条判据实际在判『请求必须覆盖 envelope 的全部语言』」
    也满足它，而那会把一次正当的收窄读成违规。
    """
    envelope = envelope_of(bundle)
    declared = envelope.locales[0]

    narrowed = constraints_out_of_envelope(_context(candidate_locales=(declared,)), envelope)
    assert not [f for f in narrowed if f.dimension is ContextDimension.LOCALES]

    added = constraints_out_of_envelope(_context(candidate_locales=(declared, "fr-FR")), envelope)
    locales = [f for f in added if f.dimension is ContextDimension.LOCALES]
    assert len(locales) == 1
    assert locales[0].requested == ("fr-FR",)


def test_an_exclusion_is_matched_by_id_and_not_by_substring(bundle: ProfileBundle) -> None:
    """点名一条被排除的 id → 越界；而一条**含有**该 id 的别的值 → 不越界。

    这是「逐字相等、不做子串匹配」的执行器。子串匹配会让 ``golf_club`` 撞上
    ``golf``（于是回执点名的是一条没被请求的排除），也会让 ``footgolf`` 与
    ``golf`` 这两条**本来就是不同排除**的 id 互相污染。变异方向：把
    ``_claimed_ids`` 那一段的相等换成 ``in``，第二半会红。
    """
    envelope = envelope_of(bundle)
    excluded = envelope.exclusions[0]

    hit = constraints_out_of_envelope(
        _context(scenario_candidates=(_candidate("anything", excluded),)), envelope
    )
    assert [f.dimension for f in hit] == [ContextDimension.SCENARIO]
    assert hit[0].requested == (excluded,)

    near_miss = constraints_out_of_envelope(
        _context(scenario_candidates=(_candidate("anything", f"{excluded}_club"),)), envelope
    )
    assert not [f for f in near_miss if f.dimension is ContextDimension.SCENARIO], (
        f"{excluded}_club 不是 {excluded} —— 这一条要是红了，说明判的是子串"
    )


def test_a_profile_that_declares_no_exclusions_has_no_exclusion_to_cross(
    bundle: ProfileBundle,
) -> None:
    """envelope 没有排除清单时，点名任何 id 都不越界。

    与语言那一条**方向相反**，而这条差别是有意的：语言那一格缺的是**收窄的依据**
    （没有依据就不能确认请求指定的几种在范围内），排除那一格缺的是**禁令**
    （没有禁令就没有可越的界）。把两者写成同一条，会让一份什么都没排除的 profile
    拒绝一切请求。
    """
    envelope = envelope_of(bundle).model_copy(update={"exclusions": ()})
    found = constraints_out_of_envelope(
        _context(scenario_candidates=(_candidate("anything", "not_excluded_anywhere"),)), envelope
    )
    assert not [f for f in found if f.dimension is ContextDimension.SCENARIO]


def test_an_undeclared_locale_set_is_reported_against_the_profile(
    bundle: ProfileBundle,
) -> None:
    """profile 没声明语言时，收窄请求指向 profile 的作者。"""
    envelope = envelope_of(bundle).model_copy(update={"locales": ()})
    found = constraints_out_of_envelope(_context(candidate_locales=("en-GB",)), envelope)
    locales = [f for f in found if f.dimension is ContextDimension.LOCALES]
    assert len(locales) == 1
    assert "search.locales" in locales[0].detail


def test_a_jurisdiction_that_differs_from_the_geo_is_still_checked(
    bundle: ProfileBundle,
) -> None:
    """管辖与 geo 不同时，判的是管辖那一格 —— 而且空着退回 geo。"""
    envelope = envelope_of(bundle)
    assert not [
        f
        for f in constraints_out_of_envelope(_context(), envelope)
        if f.dimension is ContextDimension.JURISDICTION
    ]
    found = constraints_out_of_envelope(_context(jurisdiction="IE"), envelope)
    assert [f.dimension for f in found] == [ContextDimension.JURISDICTION]


def test_a_fully_determined_request_inside_the_envelope_crosses_nothing(
    bundle: ProfileBundle,
) -> None:
    """正控：一份落在范围内的声明**一条越界都没有**。

    少了它，一个「永远返回四条越界」的实现能满足上面每一条 —— 而那是把判据
    改成迎合实现。
    """
    assert constraints_out_of_envelope(_context(), envelope_of(bundle)) == ()


# --- 6. 形状（失败关闭的那几格） ---------------------------------------------


@pytest.mark.parametrize("field", ["name", "reading", "source"])
def test_a_candidate_without_a_source_is_not_constructible(field: str) -> None:
    payload: dict[str, object] = {
        "name": "secondary school",
        "reading": "k12",
        "provenance": ScenarioProvenance.DECLARED,
        "source": "req-1",
    }
    payload[field] = "  "
    with pytest.raises(ValidationError):
        ScenarioCandidate(**payload)  # type: ignore[arg-type]


@pytest.mark.parametrize("value", [-0.01, 1.01])
def test_confidence_stays_inside_the_unit_interval(value: float) -> None:
    with pytest.raises(ValidationError):
        _candidate(confidence=value)


@pytest.mark.parametrize(
    "dimension",
    sorted(set(ContextDimension) - _ENFORCEABLE_DIMENSIONS, key=lambda d: d.value),
)
def test_a_hard_constraint_the_envelope_cannot_answer_is_not_constructible(
    dimension: ContextDimension,
) -> None:
    """在 envelope 答不上来的维度上写硬约束 → 构造不出来。

    一份没人能判的约束是一条**无论如何都会被遵守**的约束，而它给人的印象是
    「我说了就有用」。
    """
    with pytest.raises(ValidationError, match="envelope 答不上来这一维"):
        HardConstraint(dimension=dimension, values=("anything",))


def test_a_hard_constraint_must_name_at_least_one_value() -> None:
    with pytest.raises(ValidationError):
        HardConstraint(dimension=ContextDimension.GEO, values=())


def test_an_ambiguous_record_carries_no_confidence_of_its_own() -> None:
    """``Ambiguity`` 上**没有**置信度格 —— 等级由维度推出来。

    这是「等级不可标错」的结构依据：一条歧义想降级成为软歧义，只能改
    ``dimension``，而改维度改的是它描述的那件事。变异方向：给 ``Ambiguity`` 加一个
    可选的 ``is_hard`` 并让 ``ambiguity_is_hard`` 读它 —— 这一条会红。
    """
    assert "confidence" not in Ambiguity.model_fields
    assert "is_hard" not in Ambiguity.model_fields
    assert set(Ambiguity.model_fields) == {"dimension", "detail"}


def test_the_credible_provenance_set_has_no_room_for_a_guess() -> None:
    assert ScenarioProvenance.MODEL_GUESS not in _CREDIBLE_PROVENANCE
    assert set(ScenarioProvenance) - _CREDIBLE_PROVENANCE == {ScenarioProvenance.MODEL_GUESS}


# --- 7. 这一层够不到 provider（结构判据，不是运行期替身） ---------------------


def test_the_module_cannot_reach_a_provider() -> None:
    """AST 扫描：这个模块 import 的东西里没有一个住在 ``marketops/`` 的 adapter、
    port 或 application 层。

    B16 关闭条件 1 说「preview 零 provider 调用」。在这一层它不是一条纪律、也
    不是一个替身记出来的 0 —— 是**没有可以调用的东西**。一个需要注入检索端口
    才能算摘要的编译器，会在接线的那一天由「谁忘了传端口」决定 preview 花不花
    钱；而这条判据让那次接线先红。
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
        if name.startswith("marketops") or ".adapters" in name or ".ports" in name
        or ".application" in name
    )
    assert not forbidden, f"这一层不得够到 provider：{forbidden}"
    # 正控：扫描确实读到了东西（空 import 列表会让上面那条恒绿）。
    assert imported, "扫描没读到任何 import —— 判据在空转"


def test_the_fingerprint_is_only_constructed_by_its_own_derivation() -> None:
    """``SceneFingerprint(...)`` 全仓只有 ``research_context.py`` 一处。

    「derived, not identity」这句话的执行器：一个能在别处构造的指纹，迟早会被
    当成身份用（「同一个指纹就复用上次的计划」）。与 ``SearchQuery`` 那条守
    ``market_query`` 的判据同一个形状 —— 唯一的构造点就是唯一的推导规则。
    """
    offenders: list[str] = []
    for path in sorted((REPO_ROOT / "marketops").rglob("*.py")):
        if path == MODULE:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id == "SceneFingerprint"
            ):
                offenders.append(f"{path.relative_to(REPO_ROOT)}:{node.lineno}")
            elif (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "SceneFingerprint"
            ):
                offenders.append(f"{path.relative_to(REPO_ROOT)}:{node.lineno}")
    assert not offenders, f"指纹只该由 fingerprint_of 推出来，这些地方在自己造：{offenders}"
