"""候选资格判定 —— **共享的**判定引擎，两支臂跑的是同一个。

上游的 Account Discovery 冒烟实验比较臂 B（确定性查询计划器）与臂 C
（Claude Code + 研究 Skill），报告里输出 ``C - B``。用户规格 §3 的硬要求是：
Skill 可以重写查询、发现新来源族、识别新术语、决定继续或停止，**但不得改变最终
Account 资格判定规则** —— 两支臂跑同一个判定引擎。

这条要求在代码上的形态就是本文件守住的东西：

1. **一份求值循环**（``marketops/application/grading.py::account_outcome_from_facts``），
   账号路径与候选路径都调它。复制一份出来必然漂移，漂移之后就是「同一份
   ``grading.yaml``、两个判定结果」，而那时比较的不再是发现能力，是判定能力 ——
   恰好是这一轮要测的量的反面。
2. **两条路的事实键集相同**（都等于 ``ACCOUNT_QUALIFICATION_FACT_NAMES``）。
   ``evaluate_clause`` 把**键缺失**与**值 ``None``** 读成同一件事（未确认 ⇒
   不决定子句 ⇒ 规则进 ``unchecked``），所以漏一个键不会有自己的症状 ——
   键集相等是**唯一**能让「调用方漏了键」与「这条事实没有生产者」分得开的东西。
3. **候选侧不新增、不修改任何规则** —— 规则仍然只来自 ``profiles/*/grading.yaml``。

**未确认不决定任何子句，在任何极性下都不。** 这句话是判定语义的核心，也是
本文件里最容易被读漏的一句：``None`` 不是「假」，因此取反也翻不动它
（``!country in [gb, uk]`` 在 ``None`` 上**不命中**，在已确认的 ``""`` 上**命中**）。
未确认的规则**不命中并且落进** ``outcome.unchecked``，判词是 ``UNRESOLVED_FACTS``；
已确认的假（``""`` / ``[]`` / ``False``）仍然照旧求值：规则**不命中**但**判了**，
判词是 ``NO_RULE_FIRED``。两者结论相同（都停在 ``discovered``、都不排除），
差别全在「有没有核对过」—— 这是 ``unchecked`` 存在的唯一理由。

**本文件测出来的两件「差异，但不是漂移」**（读判词前先读这两条）：

* 那五条引用场地资产事实的规则在**两条路**上今天都不判：``AccountRecord`` 上
  没有那些字段，而候选行上有字段、发现期却还没有证据（值为 ``None`` 或字段
  默认值）。候选侧**能真的触发**这些规则 —— 但只有在候选**带上证据**时才触发
  （那是 ``candidate_that_fires`` 干的事）。账号侧的成对断言在
  ``tests/test_profiles.py::test_every_account_rule_is_evaluable``。
* 两处 ``country`` **读法不同**的地方，都来自**取值**而不是来自引擎：账号行有
  一个**字段**承载区域（``AccountRecord.country``，缺省 ``None`` —— 它曾经是
  ``"UK"``，那台机器把缺省当成了一次没人做过的判定），而候选行**没有任何字段**
  携带区域（``None``，未确认）。同一个 ``None`` 在两条路上读法逐字相同
  （:func:`test_the_same_none_is_read_the_same_way_in_both_projections`）。
  候选侧的形状见 :func:`test_the_region_fact_has_no_candidate_side_producer`。
"""

from __future__ import annotations

import ast
from dataclasses import replace
from pathlib import Path

import pytest

from marketops.application import grading as engine
from marketops.application.grading import (
    AccountOutcome,
    account_facts,
    account_outcome_from_facts,
    evaluate_clause,
    grade_account,
)
from marketops.application.qualify import (
    CandidateQualification,
    candidate_facts,
    qualify_candidate,
)
from marketops.domain.candidate import AccountCandidate, new_candidate
from marketops.domain.enums import AccountStatus, Cohort, EligibilityReason, Grade
from marketops.domain.errors import ConfigError, GradingError
from marketops.domain.evidence import AccountRecord
from marketops.domain.facts import (
    ACCOUNT_QUALIFICATION_FACT_NAMES,
    CANDIDATE_FACT_NAMES,
    parse_clause,
)
from marketops.domain.profile import AccountRule, GradingRule, ProfileBundle
from marketops.domain.sources import SOURCE_METHODS, SourceMethod
from tests.fakes import synthetic_scene_files

REPO_ROOT = Path(__file__).resolve().parents[1]
CORE_PACKAGE = REPO_ROOT / "marketops"

#: 一个中性主机名。**不是**真实站点 —— 真实站点不进仓库。
HOST = "probe.example.org"

#: 账号侧结构上**判不了**的那五条（成对断言在 ``tests/test_profiles.py``）。
#: 这里只在最后做一次交叉核对，判定仍然逐条测量。
#: 注意措辞：它们不是「恒为假」，是**没有值可判** —— 判词 ``UNRESOLVED_FACTS``。
INERT_ON_THE_ACCOUNT_SIDE = (
    "excluded_segment",
    "venue_is_indoor",
    "venue_is_muga",
    "artificial_surface_only",
    "no_public_presence",
)

#: 逐键比对两条路的读法时用的子句形态。三种比较方式各来一条，外加取反。
#: ``probe`` 不是市场取值，只是「某个不等于被投影值的词」。
CLAUSE_FORMS = ("{fact}", "!{fact}", "{fact}==probe", "{fact} in [probe, other]")


# --- 夹具 ---------------------------------------------------------------------


def candidate(**overrides) -> AccountCandidate:
    """一条最小可构造的候选。

    走 ``new_candidate``（注册表成员）而不是裸构造 —— 生产者路径长什么样，
    测试就长什么样：``source_method_id`` 由成员派生，写不出未注册的 id。
    """
    return new_candidate(
        SOURCE_METHODS["search.engine.v1"],
        candidate_id="probe-candidate-001",
        organisation="Example Sports Trust",
        venue_name="Example Recreation Ground",
        **overrides,
    )


def _account(**overrides) -> AccountRecord:
    """一条账号行。**默认带一个已记录的国家** —— 见下。

    ``country`` 缺省是 ``None``（「没人查过」），而本文件里那个缺省用一个
    **有值**的账号行来对照（:func:`test_the_region_fact_has_no_candidate_side_producer`
    两种都测）。这里默认给值，是因为这组夹具的角色是「账号侧那条路」——
    它的区域**有生产者**，而候选侧没有。给一个 ``None`` 会让「这条规则在账号侧
    结构上判得了」这件事在夹具里就看不见了。
    """
    payload = {
        "account_id": "probe-account-001",
        "organisation": "Example Sports Trust",
        "cohort": Cohort.KNOWN_SUCCESS,
        "country": "UK",
    }
    payload.update(overrides)
    return AccountRecord(**payload)


def _bundle(*, account_rules: tuple[AccountRule, ...] = ()) -> ProfileBundle:
    """一个规则可控的 bundle（形状照 ``tests/test_grading.py`` 的先例）。

    刻意**合成**：真实 profile 的规则是业务事实的一部分，把它当测试输入会让
    「引擎行为」与「这份 SOP 写了什么」纠缠在一起。真实 profile 上的判定由
    下面那些读 ``bundle`` 夹具的测试来测。
    """
    rules = {grade: GradingRule(grade=grade, definition=None, requires=()) for grade in Grade}
    # 合成 bundle 没有目录，所以没有真版本可以钉 —— 但版本与内容仍然要对上
    # （``ProfileBundle`` 不再允许一个没有指称对象的版本号）。哨兵性质由字节承载。
    scene_files = synthetic_scene_files()
    return ProfileBundle(
        profile_id="probe",
        status="PROBE",
        rules=rules,
        role_terms=(),
        scene_version=scene_files.version,
        scene_files=scene_files,
        unknowns=tuple(f"grading.{grade.value}" for grade in Grade),
        account_rules=account_rules,
    )


def _facts_referenced(rule: AccountRule) -> set[str]:
    """这条规则引用了哪些事实名。``requires is None`` 的规则引用不了任何东西。"""
    assert rule.requires is not None, f"{rule.id} 没有可求值的子句"
    names = set()
    for clause in rule.requires:
        parsed = parse_clause(clause)
        assert parsed is not None, f"{rule.id} 的子句不合法：{clause!r}"
        names.add(parsed.fact)
    return names


def _feedable_rules(bundle: ProfileBundle) -> tuple[AccountRule, ...]:
    """**候选层喂得动**的规则：它的子句只引用候选事实。

    ``out_of_region`` 不在里面 —— 它的子句引用 ``country``，而候选行没有这个字段
    （见 :func:`test_the_region_fact_has_no_candidate_side_producer`）。
    过滤条件是**推导出来的**，不是写死一份 id 清单。
    """
    return tuple(
        rule
        for rule in bundle.account_rules
        if _facts_referenced(rule) <= set(CANDIDATE_FACT_NAMES)
    )


def feedable(bundle: ProfileBundle) -> ProfileBundle:
    """只留下候选层喂得动的那几条规则的 bundle（见 :func:`_feedable_rules`）。

    用它来测「候选侧能触发什么」而不是直接测整份 bundle，理由只有一个：
    ``out_of_region`` 排在 ``no_public_presence`` **前面**，而它由 ``country``
    决定，于是整份 bundle 上最后那条规则永远轮不到（区域那条见
    :func:`test_the_region_fact_has_no_candidate_side_producer`）。
    """
    return replace(bundle, account_rules=_feedable_rules(bundle))


#: 事实名 → 候选行上承载它的字段名。只在这两个计数上不同名：字段记的是「几个」，
#: 事实说的是「有没有」。
_FACT_FIELD = {"any_page_fetched": "pages_fetched", "any_page_blocked": "pages_blocked"}

#: 没有取值的子句（``fact`` / ``!fact``）该给什么值。这两种形态都只出现在
#: 「有没有」这一类事实上，所以取值是「一个非空列表 / 一个非零计数」。
_TRUTHY_VALUE: dict[str, object] = {
    "exclusions_hit": ["probe-exclusion"],
    "any_page_fetched": 1,
    "any_page_blocked": 1,
}
_FALSY_VALUE: dict[str, object] = {"any_page_fetched": 0, "any_page_blocked": 0}


def _clause_driven_value(fact: str, raw: str) -> object:
    """子句里的取值 → 候选行上的取值。**只有一处**需要转换。

    ``surface_evidence_confirmed == false`` 在方言里比的是 ``str(值).lower()``，
    而候选字段是**三值**的 ``bool``：候选行上的 ``False`` 读作「查过，没有」，
    正是这条规则要说的事（``None`` = 「还没查」不能代替它，见 ``grading.yaml``
    该条上面的注释）。别的取值逐字给 —— 它们是市场词，测试不认识它们，
    也不该认识。
    """
    if fact == "surface_evidence_confirmed":
        return {"false": False, "true": True}[raw]
    return raw


def candidate_that_fires(rule: AccountRule) -> AccountCandidate:
    """构造一条能命中这条规则的候选：每个取值都从规则**自己的子句**里读出来。

    不是「猜一个能过的值」：见证由子句的形状生成（真值子句 → 取真值，
    取反子句 → 取假值，带取值的子句 → 取子句里那个值），所以它同时是
    「profile 要求什么、候选就该带什么」这句话的机器可读形式 ——
    测试里没有一处市场词，也没有一处写死的期望状态。
    """
    fields: dict[str, object] = {}
    for clause in rule.requires or ():
        parsed = parse_clause(clause)
        assert parsed is not None, f"{rule.id} 的子句不合法：{clause!r}"
        field = _FACT_FIELD.get(parsed.fact, parsed.fact)
        assert field in AccountCandidate.model_fields, (
            f"{rule.id} 引用了候选行没有的字段 {field!r} —— 它不该出现在这条路上"
        )
        if parsed.negate:
            assert not parsed.values, f"{rule.id} 的取反子句带取值：{clause!r}"
            fields[field] = _FALSY_VALUE[parsed.fact]
        elif parsed.values:
            fields[field] = _clause_driven_value(parsed.fact, parsed.values[0])
        else:
            fields[field] = _TRUTHY_VALUE[parsed.fact]
    return candidate(**fields)


def _inert_on_the_account_side(bundle: ProfileBundle) -> tuple[str, ...]:
    """账号侧**结构上判不了**的规则 id（谓词与 ``tests/test_profiles.py`` 同一条）。

    一条规则在其**全部**子句的事实都未确认（``None``）时不可能命中 ——
    是结构性「没判」，不是被核对过。

    谓词曾经还要 ``any(not p.negate)``（「至少有一条非取反子句」），因为那时
    ``!fact`` 在 ``None`` 上是**真**，一条只剩取反子句的规则会恒真命中。
    未确认不再决定任何子句（任何极性）之后，那个附加条件既不再需要，也不再
    成立 —— 留着它会让人以为取反还有一条特殊路径。
    """
    facts = account_facts(_account())
    inert: list[str] = []
    for rule in bundle.account_rules:
        assert rule.requires is not None
        parsed = [parse_clause(clause) for clause in rule.requires]
        unconfirmed = [p for p in parsed if p is not None and facts[p.fact] is None]
        if len(unconfirmed) == len(parsed):
            inert.append(rule.id)
    return tuple(inert)


# --- 1. 两条路共用**一份**求值循环 --------------------------------------------


def test_both_paths_call_the_one_shared_loop(monkeypatch, bundle: ProfileBundle) -> None:
    """两条路都走 ``account_outcome_from_facts`` —— 不是各写一遍。

    这条测的是**形状**，不是取值：复制出来的第二份循环在**今天**可能与第一份
    给出相同结果，所以只比结果不够 —— 得看它到底调没调那一个函数。
    """
    import marketops.application.qualify as qualify_module

    seen: list[tuple[str, ...]] = []
    original = engine.account_outcome_from_facts

    def spy(facts, incoming_bundle):
        seen.append(tuple(sorted(facts)))
        return original(facts, incoming_bundle)

    monkeypatch.setattr(engine, "account_outcome_from_facts", spy)
    monkeypatch.setattr(qualify_module, "account_outcome_from_facts", spy)

    grade_account(_account(), bundle)
    qualify_candidate(candidate(), bundle)

    assert len(seen) == 2, "有一条路没有走共享的求值循环 —— 那就是第二份循环"
    assert seen[0] == seen[1] == tuple(sorted(ACCOUNT_QUALIFICATION_FACT_NAMES))


def test_both_paths_give_the_same_outcome_on_the_same_facts(bundle: ProfileBundle) -> None:
    """**同一组事实**交给两条路 → 同型且同值。两支臂同引擎的机器可读表达。

    「同型」：候选侧结果里包的就是 ``AccountOutcome`` 本身，不是它的复制品。
    「同值」：``==`` 逐字段相等（frozen dataclass），包括 ``detail`` 那句理由串。

    两条路各自**只是把投影喂给共享循环**：所以两条路的结果都必须与「把同一组事实
    直接交给 ``account_outcome_from_facts``」逐字相等。任何一条路在循环之后动过
    结果（改写判词、补一个字段、把未判算成不命中），这一对断言就会红。

    注意这条**不比**「一条账号行与一条候选行给出同一个结论」：那是**取值**问题，
    不是引擎问题 —— 两条路给不给得出区域，是各自投影的事。同一个 ``None`` 的
    读法由 :func:`test_the_same_none_is_read_the_same_way_in_both_projections`
    单独钉住。
    """
    row = candidate()
    facts = candidate_facts(row)
    result = qualify_candidate(row, bundle)
    assert isinstance(result.outcome, AccountOutcome), "候选侧的结果必须是账号侧那个类型"
    assert result.outcome == account_outcome_from_facts(facts, bundle), (
        "候选路径在共享循环之外动了结果"
    )

    account = _account(domain=HOST)
    assert grade_account(account, bundle) == account_outcome_from_facts(
        account_facts(account), bundle
    ), "账号路径在共享循环之外动了结果"


def test_both_paths_agree_on_a_rule_the_discovery_layer_has_nothing_to_do_with() -> None:
    """合成 bundle 上的一对：两条路都命中同一条规则、同一个词。

    这一条刻意避开真实 profile 的全部规则 —— 它测的是循环本身，不是那份 SOP。
    """
    bundle = _bundle(
        account_rules=(
            AccountRule(
                id="no_domain",
                description="没有官方域名",
                status=AccountStatus.HOLD,
                requires=("!has_domain",),
            ),
        )
    )
    result = qualify_candidate(candidate(official_domain=""), bundle)

    assert result.status is AccountStatus.HOLD
    assert result.status is not AccountStatus.DISCOVERED
    assert result.excludes is False, "hold 不算排除"
    assert result.outcome == grade_account(_account(domain="", country=""), bundle)


def test_neither_path_invents_rules() -> None:
    """bundle 里没有账号规则时，两条路都停在 discovered —— 谁也不自带规则。"""
    bundle = _bundle(account_rules=())
    account = _account(domain="", country="")

    result = qualify_candidate(candidate(), bundle)
    assert result.status is AccountStatus.DISCOVERED
    assert result.outcome.reason is EligibilityReason.NO_RULE_FIRED
    assert result.outcome == grade_account(account, bundle)


#: 两条投影今天**取值**不同的键。差异的形状只有一种：一边是未确认（``None``），
#: 另一边是一个**已确认**的值（``""`` / ``[]`` / ``False`` / 一个字段值）。
#: 未确认不再同判于已确认的空值之后，这份清单是承重的：多一个键、少一个键
#: 都会让下面那条测试红。
#:
#: **这份清单从六格缩到三格，是本轮分类器接线之后的事**，值得写下来，
#: 免得下一个人以为那三格是被「对齐」掉的：
#:
#: * ``venue_environment`` / ``organisation_kind`` / ``surface_class`` 退出，
#:   因为候选侧现在**有**生产者（``application/classification.py``），而这条测试
#:   用的最小候选在这三格上读出来是 ``unknown`` ⇒ 投影成 ``None`` ⇒ 与账号侧
#:   那一侧的 ``None`` **同值**。它们不再「差异」，是因为两边都是未确认 ——
#:   不是因为候选侧被填了一个值。**一个 ``unknown`` 不等于一条判定**，
#:   这正是这一层最容易被读错的一格。
#: * ``country`` 仍在：工厂里的候选读不出区域（``unknown`` ⇒ ``None``），
#:   而账号夹具那一行**记了**一个国家。差异的形状照旧是「一边未确认、一边已确认」。
#: * ``exclusions_hit`` 仍在：候选侧是 ``[]``（分类器**查过**，一个取值都没命中
#:   ⇒ 一条已确认的「没有」），账号侧那七格恒为 ``None``（账号这一侧根本没有这些
#:   字段）。
_DIFFERING_KEYS = frozenset(
    {
        "country",
        "has_notes",
        "exclusions_hit",
    }
)


@pytest.mark.parametrize("fact", ACCOUNT_QUALIFICATION_FACT_NAMES)
def test_the_same_none_is_read_the_same_way_in_both_projections(fact: str) -> None:
    """**同一个 ``None``** 放进两条路的事实字典，四个子句形态读出来逐字相同。

    真正该被钉住的不变量是：给定**同一个值**，读法不取决于这个字典从哪条路来。
    它替代了原来的「逐键逐形态比对两条投影」—— 那条的前提是「两条投影的差异都在
    方言里同判」，而未确认不再同判于已确认的空值之后，那个前提不成立了，
    于是它会把**取值差异**读成**路径差异**。
    """
    account_view = account_facts(_account(domain="", country="", notes=""))
    candidate_view = candidate_facts(candidate())

    for form in CLAUSE_FORMS:
        clause = form.format(fact=fact)
        assert evaluate_clause(clause, {**account_view, fact: None}) == evaluate_clause(
            clause, {**candidate_view, fact: None}
        ), f"两条路对同一个 None 在 {clause!r} 上的读法不同"


def test_the_two_projections_differ_in_value_not_in_reading() -> None:
    """两条投影今天**不等**，而且差在取值上：一边未确认、一边已确认。

    这条把「差异」本身钉成机器可读的：差异键的清单是显式的，差异的**形状**也被
    断言（恰好一边是 ``None``）—— 于是「两条路今天不一样」不会退化成一句散文，
    而且哪天多出一个差异键，这里会红。

    两边都取同一个值的键，读法必须逐字相同（那也是上面那条测的）。
    """
    account_view = account_facts(_account(domain="", country="", notes=""))
    candidate_view = candidate_facts(candidate())

    differing = {key for key in account_view if account_view[key] != candidate_view[key]}
    assert differing == set(_DIFFERING_KEYS), f"两条投影的差异键变了：{sorted(differing)}"

    for fact in sorted(differing):
        unconfirmed = [side[fact] is None for side in (account_view, candidate_view)]
        assert sorted(unconfirmed) == [False, True], (
            f"{fact} 的差异不是「一边未确认、一边已确认」："
            f"账号侧={account_view[fact]!r}、候选侧={candidate_view[fact]!r}"
        )

    for fact in sorted(set(account_view) - set(_DIFFERING_KEYS)):
        for form in CLAUSE_FORMS:
            clause = form.format(fact=fact)
            assert evaluate_clause(clause, account_view) == evaluate_clause(
                clause, candidate_view
            ), f"取值相同、读法却不同：{clause!r}"


# --- 2. 事实键集必须相同 -------------------------------------------------------


def test_candidate_facts_keys_equal_the_qualification_vocabulary() -> None:
    """候选侧投影产出的键集 **等于** ``ACCOUNT_QUALIFICATION_FACT_NAMES``。

    与账号侧的 ``tests/test_grading.py::test_account_facts_keys_equal_the_qualification_vocabulary``
    是同一条断言的两个方向：那一处钉「账号行产得出词表里的每一个键」，这里钉
    「候选行也产得出来，而且不多不少」。
    """
    assert set(candidate_facts(candidate())) == set(ACCOUNT_QUALIFICATION_FACT_NAMES)


def test_the_shared_loop_refuses_a_facts_dictionary_that_is_not_the_vocabulary(
    bundle: ProfileBundle,
) -> None:
    """键集不是词表时**硬失败**，而不是「少几个键就少判几条」。

    这条断言今天守的东西和它立起来时不同（写清楚，免得它被当成一条形式检查）：
    **键缺失与值 ``None`` 现在同判** —— 都读成「未确认」，都让规则不命中、落进
    ``unchecked``、判词 ``UNRESOLVED_FACTS``。所以一个漏了键的字典**不会**有
    自己的症状；它看起来与「这条事实的生产者还没落地」逐字相同，而后者是我们
    在等的状态。键集相等因此是唯一能把两者分开的东西，它必须在这里抛。

    对照（下半段）也变了：词表原样的那一份今天**不再**是被排除的 ——
    候选行给不出 ``country``，``out_of_region`` 不判，状态停在 ``discovered``。
    """
    full = candidate_facts(candidate())

    with pytest.raises(GradingError):
        account_outcome_from_facts({k: v for k, v in full.items() if k != "country"}, bundle)

    with pytest.raises(GradingError):
        account_outcome_from_facts({**full, "not_a_fact": 1}, bundle)

    # 对照：词表原样的那一份必须能过 —— 过得了，而且判词是「没判」而不是「不命中」。
    outcome = account_outcome_from_facts(full, bundle)
    assert outcome.status is AccountStatus.DISCOVERED
    assert outcome.reason is EligibilityReason.UNRESOLVED_FACTS
    assert "out_of_region" in outcome.unchecked, (
        "候选行给不出区域 —— 这条规则今天必须报成「没判」，而不是由缺省命中"
    )


# --- 3. 候选侧：那五条规则现在真的能触发 ---------------------------------------


def test_the_rules_the_candidate_layer_can_feed_are_the_five_inert_on_an_account(
    bundle: ProfileBundle,
) -> None:
    """候选层喂得动的规则，恰好是账号侧结构上**判不了**的那五条。

    这是「同一条规则、两种事实供给」的机器可读形式：「判不了」来自账号行**没有
    那些字段**（于是值恒为 ``None`` ⇒ 未确认 ⇒ 规则不决定），不来自规则本身 ——
    同一批规则在候选行上**能真的命中**（见下一条测试）。成对的账号侧测试是
    ``tests/test_profiles.py::test_every_account_rule_is_evaluable``。
    """
    feedable_rules = {rule.id for rule in _feedable_rules(bundle)}

    assert feedable_rules == set(INERT_ON_THE_ACCOUNT_SIDE)
    assert feedable_rules == set(_inert_on_the_account_side(bundle))
    assert {rule.id for rule in bundle.account_rules} - feedable_rules == {"out_of_region"}


@pytest.mark.parametrize("rule_id", INERT_ON_THE_ACCOUNT_SIDE)
def test_every_feedable_rule_fires_on_a_candidate_that_carries_its_evidence(
    bundle: ProfileBundle, rule_id: str
) -> None:
    """逐条**测**它触发，而不是论证「现在应该能触发了」。

    见证由规则自己的子句生成（见 :func:`candidate_that_fires`），状态取自
    ``grading.yaml`` 的那条规则 —— 测试里不写市场词，也不写期望状态。
    """
    rule = next(rule for rule in bundle.account_rules if rule.id == rule_id)
    result = qualify_candidate(candidate_that_fires(rule), feedable(bundle))

    assert result.outcome.reason is EligibilityReason.RULE_FIRED
    assert result.status is rule.status, f"{rule_id} 命中了，但状态不是规则自己声明的那个"
    assert rule_id in result.outcome.detail, "判词要点名是哪条规则命中的"


def test_hold_is_not_an_exclusion_on_the_candidate_side(bundle: ProfileBundle) -> None:
    """``hold`` 是要人工看的，不是排除 —— 候选侧与账号侧同一条语义。

    与 ``tests/test_grading.py::test_hold_is_not_an_exclusion`` 相对：那一条测
    账号行，这一条测候选行，两条走的是同一份循环。
    """
    rule = next(rule for rule in bundle.account_rules if rule.id == "no_public_presence")
    result = qualify_candidate(candidate_that_fires(rule), feedable(bundle))

    assert result.status is AccountStatus.HOLD
    assert result.excludes is False


# --- 4. 没有证据不排除（保守方向从上一提交继承） -------------------------------


def test_a_candidate_without_evidence_is_not_excluded_by_the_rules_it_can_feed(
    bundle: ProfileBundle,
) -> None:
    """没有证据的候选**不被排除**，而且它今天连「不命中」都算不上 —— 是「没判」。

    两件事一起钉住：

    * ``excludes is False`` —— 没有证据就排除候选，比留着它危险得多；
    * 判词是 ``DISCOVERED`` + **``UNRESOLVED_FACTS``** + 非空的 ``unchecked``。

    第二条曾经断言的是 ``NO_RULE_FIRED`` + ``unchecked == ()``：那表示「逐条
    核对过、全部通过」，而这个候选**一条都没被核对**（它的事实全是 ``None``）
    —— 两者的结论相同、含义相反。``unchecked`` 是这一层唯一能把它们分开的信号，
    所以它必须非空。

    测的是「候选层喂得动的那组规则」（``feedable``）：``out_of_region`` 不在这里，
    它由 ``country`` 决定，单独测：
    :func:`test_the_region_fact_has_no_candidate_side_producer`。
    """
    result = qualify_candidate(candidate(), feedable(bundle))

    assert result.status is AccountStatus.DISCOVERED
    assert result.outcome.reason is EligibilityReason.UNRESOLVED_FACTS, (
        "没有证据的候选不是「核对过、不命中」—— 它是「没判」"
    )
    assert result.outcome.unchecked, "没判的规则必须被点名，否则 unchecked 分不开两者"
    assert set(result.outcome.unchecked) <= {rule.id for rule in feedable(bundle).account_rules}
    assert result.excludes is False, "没有证据就排除候选，比留着它危险得多"


# --- 5. 测出来的**不等价**：区域 ------------------------------------------------


def test_the_region_fact_has_a_candidate_side_producer(
    bundle: ProfileBundle, account: AccountRecord
) -> None:
    """候选行的区域**有生产者了** —— 而「读不出来」仍然是「没判」，不是「不越界」。

    **这条测试换过两次判据，两次都写下来。**

    它立起来时钉的是「每一行候选都由**缺省**命中 ``out_of_region``」：``country``
    在候选侧没有生产者，而 ``!country in [gb, uk]`` 是**取反**且无正面守卫的子句
    —— 未确认在取反下为真，于是发现层把 **100% 的候选**判成 ``irrelevant``。
    它当初就是为那一天写的：「它红掉的那天，就是这条缝被堵上的那天」。

    第一次换，堵上它的是**未确认不再决定任何子句**（不是给 ``country`` 找生产者）：
    同一个 ``None``，在取反下也不为真，于是这条规则的输出从「命中」变成「**没判**」。

    这一次换，是**生产者真的接上了**：候选行的 ``country`` 由
    ``application/classification.py`` 从页面正文与主机名读出来，词表住 profile。
    所以下面钉的是**第三**种行为，四件事分开断言：

    1. **生产者存在**：候选行上有 ``country`` 字段，而 ``CANDIDATE_FACT_NAMES``
       里没有它 —— 它的名字与账号侧那条事实**同名**（事实名共用，字段各在各的行上）。
    2. **读出来就是在区域内**：把一个**词表声明过的取值**填进字段，``out_of_region``
       **判得了**，而且不命中、**也不进** ``unchecked`` —— 那是「核对过，不是越界」，
       一条结论。取值从 profile 的词表里读，测试里没有一处市场词。
    3. **读不出来仍然不判**：最小候选那一行没有分类读数（``unknown`` ⇒ 投影 ``None``）
       ⇒ 候选**不**被判 ``irrelevant``；区域这条规则进 ``unchecked``，判词
       ``UNRESOLVED_FACTS``。**``unknown`` 不等于「不是越界」**，这一条是本次最容易被
       读错的一格。
    4. **已确认的空值仍然可判**：把 ``country`` 置为 ``""``（一个**已确认**的值 ——
       「记了，是空」），同一条规则**命中**，候选被排除。``None`` 不命中、``""`` 命中，
       这条对照与生产者接没接上无关，所以留着。
    """
    in_region = bundle.profile.classification.value_names("country")
    assert in_region, "profile 的分类词表里没有区域取值 —— 生产者接上了却没有词表"
    # 词表里只声明在范围内的取值，所以任何一个都是「在区域内」的正面读数。
    recorded_candidate = candidate(country=in_region[0])
    assert candidate_facts(recorded_candidate)["country"] == in_region[0]
    assert "country" in AccountCandidate.model_fields
    assert "country" not in CANDIDATE_FACT_NAMES, (
        "事实名与账号侧那条同名 —— 名字共用，字段各在各的行上"
    )

    decided = qualify_candidate(recorded_candidate, bundle)
    assert "out_of_region" not in decided.outcome.unchecked, (
        "读出在范围内是一条结论 —— 这条规则必须离开 unchecked"
    )

    # 而「这一页没有说过词表认得的说法」是**另一件事**：读数 ``unknown``，
    # 投影成未确认，规则不判。最小候选正是这种（它没有分类读数）。
    unrecorded = candidate_facts(candidate())
    assert unrecorded["country"] is None, "读不出来就得是未确认 —— 不能折成「在区域内」"

    result = qualify_candidate(candidate(), bundle)
    assert result.status is AccountStatus.DISCOVERED, "未确认不得被取反翻成「越界」"
    assert result.outcome.reason is EligibilityReason.UNRESOLVED_FACTS
    assert "out_of_region" in result.outcome.unchecked
    assert result.excludes is False

    # 已确认的空值：同一条规则**判了**，而且命中（「不在 [gb, uk] 里」）。
    confirmed_empty = {**candidate_facts(candidate()), "country": ""}
    fired = account_outcome_from_facts(confirmed_empty, bundle)
    assert fired.status is AccountStatus.IRRELEVANT
    assert fired.reason is EligibilityReason.RULE_FIRED
    assert "out_of_region" in fired.detail

    # 而**缺省**不是「在区域内」：``AccountRecord.country`` 的缺省是 ``None``
    # （它曾经是 ``"UK"``，那台机器把一次没人做过的判定当成了证据），于是没写
    # 国家的账号行落进 ``unchecked`` —— 与候选侧读不出来时逐字相同。
    defaulted = grade_account(account, bundle)
    assert defaulted.status is AccountStatus.DISCOVERED
    assert defaulted.reason is EligibilityReason.UNRESOLVED_FACTS
    assert "out_of_region" in defaulted.unchecked, (
        "缺省不是「在区域内」—— 缺省是「没人查过」，必须报成没判"
    )

    # 写出来的值就是值：记了国家的账号行不命中这条规则，也不进 ``unchecked``。
    recorded = grade_account(_account(country="UK"), bundle)
    assert "out_of_region" not in recorded.unchecked, "有值就是有值"


# --- 6. 排除结果要回得到证据 ---------------------------------------------------


def test_an_exclusion_carries_the_evidence_pointers(bundle: ProfileBundle) -> None:
    """排除结果带得出**理由**，也带得出**回到证据的指针** —— 不是一句散文。

    人的下一条问题永远是「凭什么」：``detail`` 回答「哪条规则」，指针回答
    「去看哪几条证据」。两个都留着，是因为合成一个字段就再也没有第二个人能复核。
    """
    witness = candidate_that_fires(
        next(rule for rule in bundle.account_rules if rule.id == "artificial_surface_only")
    )
    row = candidate(
        surface_class=witness.surface_class,
        surface_evidence_confirmed=False,
        surface_evidence_ids=["ev-surface-1", "ev-surface-2"],
        evidence_ids=["ev-row-1"],
        domain_evidence_ids=["ev-domain-1"],
    )
    result = qualify_candidate(row, bundle)

    assert isinstance(result, CandidateQualification)
    assert result.candidate_id == row.candidate_id
    assert result.status is AccountStatus.IRRELEVANT
    assert result.excludes is True
    assert "artificial_surface_only" in result.outcome.detail
    assert result.surface_evidence_ids == ("ev-surface-1", "ev-surface-2")
    assert result.evidence_ids == ("ev-row-1",)
    assert result.domain_evidence_ids == ("ev-domain-1",)


# --- 7. 注册表是**结构性**强制，不是文档纪律 -----------------------------------


def test_a_registered_member_is_the_only_way_to_build_a_candidate() -> None:
    """入口收注册表成员，由它派生 id —— 生产者不必、也不该自己写 id。"""
    row = new_candidate(
        SOURCE_METHODS["search.engine.v1"],
        candidate_id="probe-candidate-001",
        organisation="Example Sports Trust",
        venue_name="Example Recreation Ground",
    )
    assert row.source_method_id == "search.engine.v1"


@pytest.mark.parametrize(
    "bad", ["search.serper.v1", "", "  ", "search.engine.v2", "SEARCH.ENGINE.V1"]
)
def test_an_unregistered_id_cannot_be_used_to_build_a_candidate(bad: str) -> None:
    """未注册的 id 从入口进不来 —— 失败关闭，与 ``resolve_source_method`` 同一条纪律。"""
    with pytest.raises(ConfigError):
        new_candidate(
            bad,
            candidate_id="probe-candidate-001",
            organisation="Example Sports Trust",
            venue_name="Example Recreation Ground",
        )


def test_a_forged_registry_member_is_refused_even_with_a_registered_id() -> None:
    """**身份**也要对：自己 new 一个 id 相同的 ``SourceMethod`` 不算数。

    只查 id 会把注册表降级成一张词表；查身份让「注册表里的那个成员」成为唯一
    可用的值 —— 加一个来源因此必须改 ``domain/sources.py``，那是一次进了版本控制的
    决定。
    """
    forged = SourceMethod(id="search.engine.v1", family="public_search", answers="probe")
    with pytest.raises(ConfigError):
        new_candidate(
            forged,
            candidate_id="probe-candidate-001",
            organisation="Example Sports Trust",
            venue_name="Example Recreation Ground",
        )


def test_the_entry_derives_the_id_so_it_cannot_be_passed_twice() -> None:
    """``source_method_id`` 由成员派生 —— 再传一遍是矛盾，直接报错。"""
    with pytest.raises(ConfigError):
        new_candidate(
            SOURCE_METHODS["search.engine.v1"],
            candidate_id="probe-candidate-001",
            organisation="Example Sports Trust",
            venue_name="Example Recreation Ground",
            source_method_id="search.engine.v1",
        )


#: ``source_method_id`` 的字面量形态。绕过注册表只剩这一种写法。
LITERAL_KEYWORD = "source_method_id"


def _literal_source_method_ids(path: Path, *, root: Path = REPO_ROOT) -> list[str]:
    """这个文件里有没有**用字面量**给候选的 ``source_method_id`` 赋值。

    查的是 AST，不是文本：``source_method_id=method.id`` 这种派生是**对的**，
    只有 ``source_method_id="……"``（或 ``**{"source_method_id": "……"}``）才是
    「把一个没经过注册表的 id 写进代码」。注释与 docstring 里的说明不算。
    """
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    try:
        label = path.relative_to(root).as_posix()
    except ValueError:
        label = str(path)

    offenders: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        for keyword in node.keywords:
            if keyword.arg == LITERAL_KEYWORD and _is_literal(keyword.value):
                offenders.append(f"{label}:{node.lineno}")
            if keyword.arg is None and isinstance(keyword.value, ast.Dict):
                for key, value in zip(keyword.value.keys, keyword.value.values):
                    if _is_literal(key) and getattr(key, "value", None) == LITERAL_KEYWORD:
                        if _is_literal(value):
                            offenders.append(f"{label}:{node.lineno}")
    return offenders


def _is_literal(node: ast.expr | None) -> bool:
    return isinstance(node, ast.Constant) and isinstance(node.value, str)


def test_the_scanner_catches_a_literal_id_but_not_a_derived_one(tmp_path: Path) -> None:
    """对照：扫描器抓得到违规，且不误判**派生**的那一种。"""
    probe = tmp_path / "probe.py"
    probe.write_text(
        "from marketops.domain.candidate import AccountCandidate, new_candidate\n"
        "AccountCandidate(source_method_id='search.engine.v2')\n"
        "new_candidate(method, **{'source_method_id': 'search.engine.v2'})\n"
        "new_candidate(method)\n"
        "AccountCandidate(source_method_id=method.id)\n",
        encoding="utf-8",
    )
    assert len(_literal_source_method_ids(probe, root=tmp_path)) == 2


def test_no_module_hands_a_literal_source_method_id_to_the_contract() -> None:
    """``marketops/`` 里没有一处用字面量给候选的 ``source_method_id`` 赋值。

    这是把「构造候选要走注册表」从纪律变成**可执行检查**的那一半：类型上写不出
    未注册的 id（入口收 ``SourceMethod``），而绕过入口、直接给契约塞一个字面量
    id 会被这条抓住。扫描范围是整包 —— 发现期的其它模块也在这条的范围里。
    """
    offenders: list[str] = []
    for path in sorted(CORE_PACKAGE.rglob("*.py")):
        offenders.extend(_literal_source_method_ids(path))

    assert not offenders, (
        "候选的来源必须来自 marketops/domain/sources.py 的注册表：\n  "
        + "\n  ".join(offenders)
    )


# --- 8. 端到端：真 profile、**整份** bundle -----------------------------------
#
# 上面各节用筛过的规则集或合成 bundle 测「哪条规则怎么判」。这一节不筛任何规则，
# 直接拿真 ``grading.yaml`` 上的候选池问一遍：**排除是不是仍然由证据驱动**。


def test_end_to_end_only_evidence_excludes_a_candidate(bundle: ProfileBundle) -> None:
    """没有证据的候选**不再**被判 ``irrelevant``；有证据的**仍然**被判掉。

    这是未确认语义修好之后在发现层上的实际效果，逐条测出来（不是论证）：

    ========================================  ==============  ==================
    候选形状                                   状态 / 判词      原因
    ========================================  ==============  ==================
    无任何证据                                 discovered /     ``out_of_region``
                                              unresolved       ``venue_is_indoor``
                                                              等规则**没判**
    抓过但被挡（``pages_*`` 有值）             discovered /    同上
                                              unresolved
    有官网、无证据                             discovered /    同上
                                              unresolved
    确认室内（``venue_environment=indoor``）   irrelevant /    规则命中
                                              rule_fired
    人工草 + 已确认无真草证据                  irrelevant /    规则命中
                                              rule_fired
    ========================================  ==============  ==================

    上面三行是那台机器修的那件事：它们此前**全部**由 ``out_of_region`` 的
    **缺省**命中被判成 ``irrelevant`` —— 一条取反且无正面守卫的子句，把一个
    「还没有值」读成了「越界」。上游实验要输出
    ``C - B = Skill 净新增合格 Account``，在那个状态下会是 ``0 - 0``，
    而报告上看起来像「两支臂都没找到东西」。

    做法**不是**放宽规则，也不是给候选编一个区域：规则一个字没动，
    「确认室内」与「人工草 + 已确认无真草证据」这两行仍然被同一条规则判掉。
    """
    # 1-3：没有证据 ⇒ 不排除。判词是「没判」，不是「核对过、不命中」。
    for label, row in (
        ("无任何证据", candidate()),
        ("抓过但被挡", candidate(pages_fetched=4, pages_blocked=4)),
        ("有官网、无证据", candidate(official_domain=HOST)),
    ):
        result = qualify_candidate(row, bundle)
        assert result.status is AccountStatus.DISCOVERED, label
        assert result.outcome.reason is EligibilityReason.UNRESOLVED_FACTS, label
        assert result.excludes is False, f"{label}：没有证据不得排除"

    # 4-5：证据在 ⇒ 同一条规则照旧判掉，一个字没放宽。
    indoor = qualify_candidate(candidate(venue_environment="indoor"), bundle)
    assert indoor.status is AccountStatus.IRRELEVANT
    assert indoor.outcome.reason is EligibilityReason.RULE_FIRED
    assert "venue_is_indoor" in indoor.outcome.detail
    assert indoor.excludes is True

    artificial = qualify_candidate(
        candidate(surface_class="artificial_grass", surface_evidence_confirmed=False), bundle
    )
    assert artificial.status is AccountStatus.IRRELEVANT
    assert artificial.outcome.reason is EligibilityReason.RULE_FIRED
    assert "artificial_surface_only" in artificial.outcome.detail
    assert artificial.excludes is True

    # 而「人工草，但**没有**已确认为假的真草证据」不排除：三值的那个 None 是
    # 「还没查」，不是「查过，是没有」（`== false` 要的是后者）。
    unconfirmed_surface = qualify_candidate(candidate(surface_class="artificial_grass"), bundle)
    assert unconfirmed_surface.excludes is False
    assert unconfirmed_surface.outcome.reason is EligibilityReason.UNRESOLVED_FACTS
    assert "artificial_surface_only" in unconfirmed_surface.outcome.unchecked, (
        "「还没查」必须报成「没判」，而不是被读成「确认没有真草证据」"
    )
