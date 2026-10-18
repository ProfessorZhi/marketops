"""判定引擎的测试。

本文件守的是**两个轴互不决定**这件事（SOP v1 L188 画的是两个状态机）：

* ``grade_route`` 回答「这条联系路线属于哪一级」；
* ``grade_account`` 回答「这个组织值不值得研究」。

把两者合成一个函数，「组织对、人不对」（账号 eligible、这条路线是 X）就
无处安放 —— 那是这类研究里最常见的结果之一。因此这里既逐轴测，也测它们
**不互相影响**。

第二个重点是**拒绝判定**的行为：规则求不了值时返回 X + 原因码，而不是猜一个。
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from marketops.domain.entities import ContactRoute
from marketops.domain.enums import (
    AccountStatus,
    BusinessFunction,
    Cohort,
    EligibilityReason,
    EntryKind,
    Grade,
    GradeReason,
    RecordBlockPriority,
    RoleClass,
    RouteBinding,
)
from marketops.domain.evidence import AccountRecord
from marketops.domain.errors import GradingError
from marketops.domain.facts import ACCOUNT_QUALIFICATION_FACT_NAMES, CANDIDATE_FACT_NAMES
from marketops.domain.profile import AccountRule, GradingRule, ProfileBundle
from marketops.domain.temporal import Currentness, Freshness, FreshnessReason
from marketops.application.grading import (
    FACT_NAMES,
    account_facts,
    account_outcome_from_facts,
    evaluate_clause,
    evaluate_requires,
    grade_account,
    grade_route,
    route_facts,
)
from tests.conftest import DEFAULT_PROFILE
from tests.fakes import synthetic_scene_files

#: 这一批构造用的工作区 = 测试用的那个 profile。模型上**没有**默认值；这里是测试
#: 构造器，说清它正是应该的（见 ``tests/test_evidence_store_contract.py`` 的说明）。
WORKSPACE = DEFAULT_PROFILE


def _bundle(*, account_rules: tuple[AccountRule, ...] = (), **rule_overrides) -> ProfileBundle:
    """构造一个规则可控的 bundle。默认全 UNKNOWN（与仓库早期状态一致）。

    刻意**合成**而不是读真实 profile：真实 profile 的规则是业务事实的一部分，
    把它当成测试输入会让「引擎行为」和「这份 SOP 写了什么」纠缠在一起。
    """
    rules = {grade: GradingRule(grade=grade, definition=None, requires=()) for grade in Grade}
    for name, (definition, requires) in rule_overrides.items():
        rules[Grade[name]] = GradingRule(
            grade=Grade[name], definition=definition, requires=tuple(requires)
        )
    unknowns = tuple(f"grading.{g.value}" for g, r in rules.items() if r.is_unknown)
    # 合成 bundle 没有目录，所以没有真版本可以钉 —— 但版本与内容仍然要对上
    # （``ProfileBundle`` 不再允许一个没有指称对象的版本号）。哨兵性质由字节承载：
    # 这段内容不来自任何 profile 目录，所以它算出的摘要不等于任何真 profile 的版本。
    scene_files = synthetic_scene_files()
    return ProfileBundle(
        profile_id="test",
        status="TEST",
        rules=rules,
        role_terms=(),
        scene_version=scene_files.version,
        scene_files=scene_files,
        unknowns=unknowns,
        account_rules=account_rules,
    )


def _route(**overrides) -> ContactRoute:
    payload = {
        "workspace": WORKSPACE,
        "account_id": "a1",
        "email": "grounds@example.co.uk",
        "email_domain": "example.co.uk",
        "evidence_ids": ["e1"],
    }
    payload.update(overrides)
    return ContactRoute(**payload)


def _account(**overrides) -> AccountRecord:
    payload = {
        "account_id": "a1",
        "organisation": "Example Trust",
        "cohort": Cohort.KNOWN_SUCCESS,
    }
    payload.update(overrides)
    return AccountRecord(**payload)


# --- 路线轴：规则未知 → 拒绝判定 ----------------------------------------------


def test_unknown_rules_return_x_with_reason() -> None:
    """规则全不可求值时：X + 原因码，且 ``detail`` 要点名缺的是哪几级。"""
    outcome = grade_route(_route(), _bundle())

    assert outcome.grade is Grade.X
    assert outcome.reason is GradeReason.RULES_UNKNOWN
    # 断言的是「缺失项被点名」，不是「detail 里出现了某个词」—— 后者会因为
    # 提示语改一个字而失效，前者才是这条测试要守的东西。
    assert "grading.A" in outcome.detail


def test_unknown_rules_never_guess_a_grade() -> None:
    """即使证据非常充分（有邮箱、有姓名、有角色、强绑定），规则未知也不给 A。"""
    route = _route(
        email="jane.doe@example.co.uk",
        person_name="Jane Doe",
        role_title="Grounds Manager",
        role_class=RoleClass.OPERATIONAL,
        binding=RouteBinding.STRONG,
    )
    assert grade_route(route, _bundle()).grade is Grade.X


# --- 路线轴：短路情形 ---------------------------------------------------------


def test_no_evidence_returns_x() -> None:
    outcome = grade_route(_route(evidence_ids=[]), _bundle(A=("x", ["has_literal_email"])))
    assert outcome.grade is Grade.X
    assert outcome.reason is GradeReason.NO_ELIGIBLE_EVIDENCE


def test_conflict_downgrades_to_x() -> None:
    """归属冲突优先于一切规则 —— 哪怕规则齐全也不能给等级。"""
    bundle = _bundle(A=("x", ["has_literal_email"]))
    outcome = grade_route(_route(has_conflict=True), bundle)

    assert outcome.grade is Grade.X
    assert outcome.reason is GradeReason.CONFLICTING_EVIDENCE


def test_excluded_entry_short_circuits_to_x() -> None:
    """排除类别不是「降权后仍可能落到 C/D」，是「不得成为任何一级」。

    这条与上一条一样是**短路**：规则表求的是「命中哪一级」，而这里否定的是
    「可以成为任何一级」。写成规则会让它取决于规则表的写法。
    """
    bundle = _bundle(A=("x", ["has_literal_email"]))
    outcome = grade_route(
        _route(
            role_title="Finance Director",
            role_class=RoleClass.EXCLUDED,
            business_function=BusinessFunction.FINANCE,
            entry_kind=EntryKind.EXCLUDED,
        ),
        bundle,
    )

    assert outcome.grade is Grade.X
    assert outcome.reason is GradeReason.EXCLUDED_ENTRY


# --- 路线轴：规则齐全时的行为 -------------------------------------------------


def test_defined_rule_matches_in_priority_order() -> None:
    bundle = _bundle(
        A=("has role", ["has_role"]),
        B=("has email", ["has_literal_email"]),
    )
    outcome = grade_route(_route(role_title="Grounds Manager"), bundle)

    assert outcome.grade is Grade.A
    assert outcome.reason is GradeReason.RULE_MATCHED


def test_falls_through_to_lower_grade() -> None:
    bundle = _bundle(
        A=("has role", ["has_role"]),
        B=("has email", ["has_literal_email"]),
    )
    assert grade_route(_route(), bundle).grade is Grade.B


def test_negation_clause() -> None:
    bundle = _bundle(C=("no conflict", ["has_literal_email", "!has_conflict"]))
    assert grade_route(_route(), bundle).grade is Grade.C


def test_in_clause() -> None:
    """``fact in [a, b]`` 是 C 级「业务口径属于某个集合」要用的形态。"""
    bundle = _bundle(
        C=("venue business function", ["business_function in [grounds, facilities, venue]"])
    )

    matched = _route(business_function=BusinessFunction.FACILITIES)
    assert grade_route(matched, bundle).grade is Grade.C

    unmatched = _route(business_function=BusinessFunction.FINANCE)
    assert grade_route(unmatched, bundle).grade is Grade.X


def test_in_clause_does_not_match_a_substring() -> None:
    """取值集合是按整个值比的，不是子串比 —— ``grounds`` 不该匹到 ``groundsmen``。"""
    bundle = _bundle(C=("venue business function", ["business_function in [grounds]"]))
    outcome = grade_route(
        _route(role_title="Grounds Manager", role_class=RoleClass.OPERATIONAL), bundle
    )
    assert outcome.grade is Grade.X


def test_unknown_fact_name_is_reported_not_silent() -> None:
    """grading.yaml 写错事实名时不能静默失效 —— 必须回报出来。

    静默失效的样子是「这条规则永远不命中」，而它看起来完全正常。
    """
    bundle = _bundle(D=("typo rule", ["has_emial"]))  # 故意拼错
    outcome = grade_route(_route(), bundle)

    assert outcome.grade is Grade.X
    assert "has_emial" in outcome.detail


# --- §9 的边界：定级不读时刻，也不读当前性 ------------------------------------


def test_the_grade_does_not_read_the_moments_or_the_currentness() -> None:
    """「这条路线是哪一级」与「它现在还算不算数」是两个问题，答案在两个字段里。

    把当前性折进等级看起来很省事，实际会让两件事互相改写：一条 A 级路线会因为
    材料旧了而降级，而正确的处置是**去重验它**；降级之后「它曾经是 A」以及
    「为什么不再是」都查不到了。所以 §9 的这两格只存、只露，不参与判定。

    这一格是**怎么进去的**不影响这条判据：这里用 ``model_copy`` 直接塞值，把
    「是不是校验挡住了谁」从问题里排除掉 —— 这一条问的是定级读不读它。
    """
    bundle = _bundle(A=("named contact with a literal email", ["has_literal_email"]))
    plain = _route()
    loaded = plain.model_copy(
        update={
            "first_seen_at": datetime(2026, 9, 15, 12, 0, tzinfo=timezone.utc),
            "last_verified_at": datetime(2026, 9, 20, 12, 0, tzinfo=timezone.utc),
            "source_published_at": datetime(2019, 5, 1, tzinfo=timezone.utc),
            "valid_from": datetime(2019, 4, 1, tzinfo=timezone.utc),
            "valid_to": datetime(2019, 6, 1, tzinfo=timezone.utc),
            "freshness": Freshness(
                assertion_id="0" * 32,
                currentness=Currentness.STALE,
                reason=FreshnessReason.BEYOND_POLICY_WINDOW,
                policy_version="uncalibrated-test",
                evaluated_at=datetime(2026, 9, 24, tzinfo=timezone.utc),
                age_days=1000,
            ),
        }
    )

    assert route_facts(loaded) == route_facts(plain)
    assert grade_route(loaded, bundle) == grade_route(plain, bundle)


# --- 事实投影 -----------------------------------------------------------------


def test_route_facts_keys_are_declared() -> None:
    """FACT_NAMES 与实际投影必须一致，否则装载期校验会放过来一条求不了值的规则。"""
    facts = route_facts(_route())
    assert set(facts) == set(FACT_NAMES)


def test_block_priority_none_projects_as_unknown() -> None:
    """``None`` 是「SOP 没定义这一级」，投影成 ``"unknown"``。

    若投影成空串，一条写 ``block_priority==P1`` 的规则仍然不会命中，但读审计的人
    看到的分不清「没这一级」和「没读到」。
    """
    assert route_facts(_route())["block_priority"] == "unknown"
    assert (
        route_facts(_route(block_priority=RecordBlockPriority.P2))["block_priority"] == "P2"
    )


def test_email_domain_matches_account(account) -> None:
    domains = (account.domain,)

    assert route_facts(_route(), domains)["email_domain_matches_account"] is True
    assert (
        route_facts(_route(email="sales@other.example", email_domain="other.example"), domains)[
            "email_domain_matches_account"
        ]
        is False
    )
    # 未确认的域（空集）不让子句成立 —— 与「域不符」判词相同、理由相同。
    assert route_facts(_route(), ())["email_domain_matches_account"] is False
    # 一个组织可以有多个域（主站 + 分区站），任一命中即算自己的。
    assert (
        route_facts(_route(), ("other.example", account.domain))[
            "email_domain_matches_account"
        ]
        is True
    )


def test_route_facts_require_evidence_flag() -> None:
    assert route_facts(_route(evidence_ids=[]))["has_evidence"] is False


# --- 账号轴 -------------------------------------------------------------------


def test_account_without_any_rule_stays_discovered() -> None:
    """没有规则命中 ≠ 被排除。账号停在 discovered，不据此推进状态。"""
    outcome = grade_account(_account(), _bundle())

    assert outcome.status is AccountStatus.DISCOVERED
    assert outcome.reason is EligibilityReason.NO_RULE_FIRED
    assert outcome.excludes is False


def test_account_rule_fires_on_a_evaluable_fact() -> None:
    bundle = _bundle(
        account_rules=(
            AccountRule(
                id="out_of_region",
                description="不在声明区域内",
                status=AccountStatus.IRRELEVANT,
                requires=("!country in [gb, uk]",),
                declared_when="account.country not in ['GB']",
            ),
        )
    )

    assert grade_account(_account(country="US"), bundle).status is AccountStatus.IRRELEVANT
    assert grade_account(_account(country="GB"), bundle).status is AccountStatus.DISCOVERED


def test_unevaluable_account_rule_never_fires_and_is_reported() -> None:
    """求不了值的排除规则**永不命中**，并进 ``unchecked``。

    一条被读成恒真的空排除规则会把整个名单一次清空，而且看不出是错的 ——
    所以这里刻意选择「留着它」，而不是「保守地排除」。
    """
    bundle = _bundle(
        account_rules=(
            AccountRule(
                id="artificial_surface_only",
                description="只有人造表面",
                status=AccountStatus.IRRELEVANT,
                requires=None,
                declared_when="account.attributes.primary_surface == 'artificial_grass'",
            ),
        )
    )
    outcome = grade_account(_account(), bundle)

    assert outcome.status is AccountStatus.DISCOVERED
    assert outcome.reason is EligibilityReason.UNRESOLVED_FACTS
    assert outcome.unchecked == ("artificial_surface_only",)
    assert "artificial_surface_only" in outcome.detail


def test_hold_is_not_an_exclusion() -> None:
    """``hold`` 是要人工看的，不是「排除」。把它算成排除会静默丢掉待判账号。"""
    bundle = _bundle(
        account_rules=(
            AccountRule(
                id="no_public_presence",
                description="没有任何公开页面",
                status=AccountStatus.HOLD,
                requires=("has_domain",),
            ),
        )
    )
    outcome = grade_account(_account(domain="example.co.uk"), bundle)

    assert outcome.status is AccountStatus.HOLD
    assert outcome.excludes is False


def test_account_facts_are_literal_only() -> None:
    """账号事实只做字面投影 —— 引擎不推断「country=GB 大概就在区域内」。

    候选事实（场地资产那一组）在**账号行**上没有生产者，因此一律 ``None``：
    ``None`` 的语义是「未确认」而不是「假」—— 它在这套方言里**不决定子句**
    （不像 ``""`` / ``[]`` / ``False`` 那样是证据），见 :func:`evaluate_clause`。

    断言的是**逐键**相等而不是整个字典：整个字典相等在 `**dict.fromkeys` 那种
    写法下会把「键在不在」和「值是什么」一起断言掉，改动一来就红得看不出是哪半边。
    键集由下一条测试单独钉。
    """
    facts = account_facts(_account(country="GB", domain="example.co.uk", notes="n"))
    assert {key: facts[key] for key in ("country", "has_domain", "has_notes")} == {
        "country": "GB",
        "has_domain": True,
        "has_notes": True,
    }
    for name in CANDIDATE_FACT_NAMES:
        assert facts[name] is None, f"{name} 在账号行上还没有生产者，只允许是 None"


def test_account_facts_keys_equal_the_qualification_vocabulary() -> None:
    """引擎产出的事实键集 = 装载期白名单。

    装载期问「这个词引擎认识吗」，运行期问「这个键现在产得出来吗」——
    两个检查是分开的，因此会漂移。漂移的后果是：一条语法完全合法的规则落进
    ``unchecked``，判词与「这条规则压根没写 requires」一模一样。这里把它钉住：
    今天两边一致，不一致就要显式地面对它（见下面的机制测试）。
    """
    assert set(account_facts(_account())) == set(ACCOUNT_QUALIFICATION_FACT_NAMES)


def test_clause_naming_a_fact_the_engine_cannot_produce_lands_in_unchecked() -> None:
    """``requires`` 语法合法、但引擎产不出那个事实键时，规则仍须落 ``unchecked``。

    与 ``test_unevaluable_account_rule_never_fires_and_is_reported`` 是一半对一半：
    那一条造的是 ``AccountRule(requires=None)``（规则压根没写子句），这一条造的是
    「写了子句、子句也合法，只是引擎的 ``account_facts`` 里没有这个键」。

    判词必须完全一样：``DISCOVERED`` + ``UNRESOLVED_FACTS``。这是**引擎侧契约**的
    测试 —— 装载器本来会拒掉 ``unresolvable`` 这个词（它不在装载期白名单里），
    两个检查是分开的、会漂移。

    **两类规则的判词与去向逐字相同**：``venue_is_indoor`` 引用的是一个**键存在、
    值为 ``None``** 的事实，``drifted_fact`` 引用的是一个**键不存在**的事实 ——
    而「未确认」正是这两件事合成的一件事（见 :func:`evaluate_clause` 与
    ``_require_the_qualification_key_set``）。所以它们一起落进 ``unchecked``。
    此前它们分得开（值 ``None`` ⇒ 求值为假 ⇒ ``NO_RULE_FIRED``；键缺失 ⇒
    ``UNRESOLVED_FACTS``），那个区分正是要取消的：它让「还没查」与「查了，是没有」
    看起来一样。

    它同时是**装载期白名单与运行期键集会漂移**这件事的唯一承载点：``unresolvable``
    这个词装载层会拒，但 ``account_facts`` 的键集是另一张表 —— 有人给词表加了新名字
    而没给 ``account_facts`` 加键，症状就是这条测试描述的那一行。
    """
    bundle = _bundle(
        account_rules=(
            AccountRule(
                id="venue_is_indoor",
                description="室内场馆",
                status=AccountStatus.IRRELEVANT,
                requires=("venue_environment==indoor",),
            ),
            AccountRule(
                id="drifted_fact",
                description="引用了引擎产不出的事实键 —— 装载层本会拒绝这个词",
                status=AccountStatus.IRRELEVANT,
                requires=("unresolvable",),
            ),
        )
    )
    outcome = grade_account(_account(), bundle)

    assert outcome.status is AccountStatus.DISCOVERED
    assert outcome.reason is EligibilityReason.UNRESOLVED_FACTS
    assert outcome.unchecked == ("venue_is_indoor", "drifted_fact"), (
        "键缺失与值 None 是同一件事（未确认），两条规则必须一起落 unchecked"
    )
    assert outcome.excludes is False


def test_a_facts_mapping_with_a_wrong_key_set_is_refused_not_graded() -> None:
    """键集不对时**抛**，不是照常判 —— 这是 ``account_outcome_from_facts`` 的守卫。

    守卫存在的理由只有一条，而且是**今天才有**的：``evaluate_clause`` 把「键缺失」
    与「值 ``None``」读成同一件事，所以一个拼错的键与「这条事实还没有生产者」在
    判词、在 ``unchecked``、在 ``detail`` 上逐字相同。两条路都走得通，但一条通向
    「我们在等生产者」，另一条通向「这里有个 bug，而它永远不会好」—— 而报告上分不开。

    键集相等把「未确认」的来源收缩成一个可枚举的词表（``ACCOUNT_QUALIFICATION_FACT_NAMES``
    里取值仍为 ``None`` 的那些），于是**没有键是「漏传的」**。所以这里必须抛。

    对照：``test_clause_naming_a_fact_the_engine_cannot_produce_lands_in_unchecked``
    走的是**键集正确、值产不出来**那条路，两条一起读才看得出守卫拦的是哪一种。
    """
    bundle = _bundle()
    with pytest.raises(GradingError) as caught:
        account_outcome_from_facts({"country": "GB"}, bundle)

    message = str(caught.value)
    assert "has_domain" in message and "has_notes" in message, (
        "报文要点名缺了哪些键，否则读的人只能自己去比对两张表"
    )


# --- 两个轴互不决定 -----------------------------------------------------------


def test_the_two_axes_do_not_decide_each_other() -> None:
    """账号被排除，不代表这条路线的等级要跟着变；反之亦然。

    「组织对、人不对」与「组织不对、人还行」都真实存在。合并两个轴就表达不了
    它们 —— 那不是简化，是删信息。
    """
    bundle = _bundle(
        A=("has role", ["has_role"]),
        account_rules=(
            AccountRule(
                id="out_of_region",
                description="不在声明区域内",
                status=AccountStatus.IRRELEVANT,
                requires=("!country in [gb, uk]",),
            ),
        ),
    )
    account = _account(country="US")
    route = _route(role_title="Grounds Manager")

    assert grade_account(account, bundle).status is AccountStatus.IRRELEVANT
    assert grade_route(route, bundle, account_domains=(account.domain,)).grade is Grade.A

    eligible_account = _account(country="GB")
    x_route = _route()
    assert grade_account(eligible_account, bundle).status is AccountStatus.DISCOVERED
    assert grade_route(x_route, bundle, account_domains=(eligible_account.domain,)).grade is Grade.X


@pytest.mark.parametrize("grade", [Grade.A, Grade.B, Grade.C, Grade.D, Grade.X])
def test_every_grade_can_be_looked_up(grade: Grade) -> None:
    """五级都必须在 bundle 里有一席之地 —— 缺一级是装载不完整，不是「没有规则」。"""
    assert _bundle().rule_for(grade) is not None


# ----------------------------------------------------------------------
# 未确认：**键缺失**与**值 ``None``** 是**一件事**，且**任何极性下都不决定**
#
# 这一组与上面几组的读法不同：上面问「规则命中哪一级」，这里问「求值原语在
# 没有证据时说什么」。答案必须是「什么都没说」—— 而它在返回值上长得像「假」，
# 所以分得开这两件事的是第二个返回值（未确认的事实名）。
# ----------------------------------------------------------------------
#: fact 名统一取 ``x``：这一组关心的是**有没有值**，不是哪个事实。
#: 六种形态覆盖方言的全部三种比较方式 × 两种极性 —— 漏掉一种就是在赌
#: 「取反那一条大概跟其它几条一样」。
CLAUSE_FORMS = (
    "x",
    "!x",
    "x==yes",
    "!x==yes",
    "x in [yes, no]",
    "!x in [yes, no]",
)

#: 已确认的**空值**。它们是**证据**（「记了，是没有 / 没有命中」），不是未确认。
CONFIRMED_EMPTY = ("", False, 0, [], ())


def test_a_missing_key_and_a_none_value_are_indistinguishable() -> None:
    """「还没查」与「查了，键不在」在判词上必须是同一件事。

    此前两者分得开：键缺失 ⇒ ``unchecked``；值 ``None`` ⇒ 求值为假 ⇒
    ``NO_RULE_FIRED``。**那个区分正是错的** —— 它让同一个事实因为生产者写
    ``None`` 还是干脆不写，得到两个不同的判词，而调用方看不出自己拿到的是哪一种。
    """
    for clause in CLAUSE_FORMS:
        missing = evaluate_clause(clause, {})
        explicit_none = evaluate_clause(clause, {"x": None})
        assert missing == explicit_none, f"{clause!r} 对两种「没有值」给出了两种说法"


@pytest.mark.parametrize("clause", CLAUSE_FORMS)
def test_an_unconfirmed_fact_does_not_decide_a_clause_in_either_polarity(clause: str) -> None:
    """没有值时，正着写与反着写**都不决定**，并且都点出是哪个事实没值。

    反着写的这一半是这次修复的入口：``!country in [gb, uk]`` 是一条**取反且没有
    正面守卫**的规则，而 ``country`` 在候选侧没有生产者。旧行为把「还没查区域」
    取反成「确认越界」，于是**每一行**候选都被判成 ``irrelevant`` —— 报告上看起来
    只是「候选都不合格」，看不出是错的。

    只断言「不命中」不够：那样一个把未确认吞掉、返回 ``(False, None)`` 的实现
    也会绿 —— 而它会让规则落进「核对过、不命中」，``unchecked`` 就再也分不开
    「判为假」与「没判」了。所以第二个返回值也要断言。
    """
    for facts in ({}, {"x": None}):
        satisfied, unconfirmed = evaluate_clause(clause, facts)
        assert satisfied is False, f"{clause!r} 在没有值时决定了子句（facts={facts}）"
        assert unconfirmed == "x", "未确认必须被点名，否则规则会被记成「判过、不命中」"


@pytest.mark.parametrize("empty", CONFIRMED_EMPTY)
def test_a_confirmed_empty_value_is_evidence_and_can_be_negated(empty) -> None:
    """``""`` / ``[]`` / ``False`` / ``0`` 是**证据**，取反是一次真的取反。

    把「记了是没有」与「还没查」合成一件事，是同一个错误的另一半：它会让一条
    确认不成立的排除规则变成永久静默 —— 而那种静默看起来与「这条规则本来就不该
    命中」逐字相同。

    核心对照：``!x in [gb, uk]`` 在 ``None`` 下**不命中**，在 ``""`` 下**命中**。
    两条一起才说明这是一条三值判据，而不是「把 None 当假」或者「把空值当 None」。
    """
    assert evaluate_clause("!x in [gb, uk]", {"x": empty}) == (True, None)
    assert evaluate_clause("x in [gb, uk]", {"x": empty}) == (False, None)
    assert evaluate_clause("!x", {"x": empty}) == (not bool(empty), None)
    # 对照：同一个子句在**没有值**时什么也不说。
    assert evaluate_clause("!x in [gb, uk]", {"x": None}) == (False, "x")


@pytest.mark.parametrize(
    ("clauses", "expected"),
    [
        (("x", "y"), {"x", "y"}),
        (("y", "x"), {"x", "y"}),
    ],
)
def test_the_unconfirmed_set_does_not_depend_on_clause_order(clauses, expected) -> None:
    """未确认不短路：把**全部**没值的事实收全，而不是停在第一条。

    在第一个未确认处停下也能得到「规则不命中」，但那时 ``unchecked`` 记的是
    「碰巧排在第一条的那个事实」—— 一个随子句顺序漂移的任意子集。这个列表是
    这一层唯一能分开「判为假」与「没判」的信号，它必须由**事实**决定，不能由
    **子句顺序**决定。两个顺序都给同一组名字，才是「由事实决定」。
    """
    satisfied, unconfirmed = evaluate_requires(clauses, {})
    assert satisfied is False
    assert set(unconfirmed) == expected


def test_a_confirmed_false_clause_decides_the_rule_so_nothing_is_unchecked() -> None:
    """一条**确认为假**的子句决定了整条合取 —— 此时不回报任何未确认的事实名。

    这不是丢信号：第二个返回值的唯一用途是让调用方把这条规则记进 ``unchecked``
    （「我们没拿证据核对过它」），而这条规则**判了**，判词是「不命中」。把它也
    记进 ``unchecked``，``unchecked`` 就不再是那个能分开「判为假」与「没判」的
    信号 —— 而那正是 ``EligibilityReason`` 两个成员的全部区别。

    这一条在修复前**随子句顺序变**：``("x", "has_domain")`` 把规则记成没判，
    ``("has_domain", "x")`` 记成判过。同一份规则因为写的顺序不同得到两个判词，
    是这一组里最贵的一种错误。
    """
    for clauses in (("x", "has_domain"), ("has_domain", "x")):
        satisfied, unconfirmed = evaluate_requires(clauses, {"has_domain": False})
        assert satisfied is False
        assert unconfirmed == [], f"{clauses} 把一条已判的规则记成了「没判」"


def test_a_negated_clause_naming_an_unknown_fact_does_not_match() -> None:
    """公开面上的一次：取反的未知事实名不得让规则命中。

    这是上面那组语义在 ``grade_route`` 上的落点，也是本次修复**唯一**通过公开
    接口可达的形态：``requires`` 由调用方直接构造时，写错一个事实名是可能的。
    修复前 ``D`` 级会照常给出 —— 一条拼错的规则把路线**升级**了。
    """
    bundle = _bundle(D=("typo rule", ["!has_emial"]))  # 故意拼错
    outcome = grade_route(_route(), bundle)

    assert outcome.grade is Grade.X
    assert outcome.reason is GradeReason.RULES_UNKNOWN
    assert "has_emial" in outcome.detail


def test_a_negated_unknown_rule_does_not_exclude_an_account() -> None:
    """账号侧的同一个形态。

    这一条在修复前**已经是绿的** —— 但绿的原因是 ``grade_account`` 循环里那句
    ``if bad_clauses: ... continue``（它先看未确认再看命中），而不是引擎判对了。
    同一个形态在路线侧是红的。写在这里是为了让「两条路对同一件事给出同一回答」
    有一条判据，而不是靠其中一条恰好有一道守卫。
    """
    bundle = _bundle(
        account_rules=(
            AccountRule(
                id="typo_rule",
                description="拼错事实名的排除规则",
                status=AccountStatus.IRRELEVANT,
                requires=("!no_such_fact",),
                declared_when="account.attributes.something",
            ),
        )
    )
    outcome = grade_account(_account(), bundle)

    assert outcome.status is AccountStatus.DISCOVERED, "拼错的规则排除了一个账号"
    assert outcome.reason is EligibilityReason.UNRESOLVED_FACTS
    assert outcome.unchecked == ("typo_rule",)


def test_a_confirmed_empty_account_fact_still_fires_a_negated_rule() -> None:
    """账号侧的正面控制：确认的空值照旧可判、可取反。

    没有这一条，上面整组都可以被一个「凡是没值就不命中」的实现满足 ——
    而那个实现会让「确认没有官网」的账号永远留在名单里（``has_domain`` 是
    ``bool("")``，确认的假），这正是排除规则本来要处理的情形。
    """
    bundle = _bundle(
        account_rules=(
            AccountRule(
                id="no_public_presence",
                description="没有任何公开页面",
                status=AccountStatus.IRRELEVANT,
                requires=("!has_domain",),
                declared_when="account.pages == []",
            ),
        )
    )
    outcome = grade_account(_account(domain=""), bundle)

    assert outcome.status is AccountStatus.IRRELEVANT
    assert outcome.reason is EligibilityReason.RULE_FIRED


def test_the_route_projection_produces_no_unconfirmed_value_today() -> None:
    """**今天**路线侧的投影仍不产出 ``None`` —— 把这件事继续变成机器可查的。

    这条断言的价值在于它**会红**：某天路线侧某个事实加上「没有就说 ``None``」的
    生产者，这里立刻红，而红的时候指向的语义已经在上面钉好了。

    它此前**同时**断言账号侧，而账号侧今天真的长出了这样一个生产者
    （``AccountRecord.country`` 的缺省从 ``"UK"`` 改成了 ``None``）—— 那次改动
    按「**换判据，不换代码**」把这条判据拆成两半：路线侧留在原处，账号侧换成
    下面两条**说得出「``None`` 是什么」**的判据。留着原来那一行只有两种下场：
    一直红着没人管，或者有人把那一行删掉 —— 两种都让路线侧一起失守。
    """
    assert all(value is not None for value in route_facts(_route()).values())


def test_an_unresearched_country_projects_as_none_and_a_declared_one_does_not() -> None:
    """账号侧：没写国家 = **还没查**，投影成 ``None``；写了就照字面透出。

    三个取值各有各的断言，缺一不可：

    * 没写 ⇒ ``None``（未确认，不决定任何子句 —— 包括 ``!country in [gb, uk]``）；
    * 写了 ⇒ 原样（字面投影，引擎不推断「``GB`` 大概就在区域内」）；
    * 显式空串 ⇒ **原样留下空串，不是 ``None``**。空串是**证据**
      （「查了，是没有」），它可以被取反；把它折成 ``None`` 会让一条「确认没有
      国家」的记录变成一条没人查过的记录，而这两件事在报告上长得一样。
    """
    assert account_facts(_account())["country"] is None
    assert account_facts(_account(country="GB"))["country"] == "GB"
    assert account_facts(_account(country=""))["country"] == ""


def test_a_missing_country_leaves_out_of_region_unchecked_rather_than_firing(bundle) -> None:
    """真实 profile 上的活体判据：``out_of_region`` 是**取反且没有正面守卫**的。

    ``grading.yaml`` 里那条规则是 ``requires: ["!country in [gb, uk]"]``。缺省
    ``"UK"`` 时，一条没写国家的记录会让子句求值为真 → 取反为假 → 闸门不满足 →
    规则**静默不触发**，报告读成「这个账号在区域内」。这不是假绿，是**假红缺席**：
    本该落在 ``unchecked`` 里的东西被记成了「核对过、没问题」。

    正对照是同一份 bundle 上的 ``country="US"``：它**必须**真的被判
    ``irrelevant``。没有这一半，下面那条断言可以被一个「永远不排除任何账号」的
    实现满足，而那个实现会让越界账号一直留在名单里。

    这条用 ``conftest`` 的**真实** profile fixture，与上面那些合成 bundle 不同：
    要证明的正是这份冻结 SOP 上的规则会怎么动。
    """
    assert account_facts(_account())["country"] is None

    unchecked = grade_account(_account(), bundle)
    assert unchecked.status is AccountStatus.DISCOVERED
    assert unchecked.reason is EligibilityReason.UNRESOLVED_FACTS
    assert "out_of_region" in unchecked.unchecked, (
        "没有国家的账号被这条取反规则静默放行了 —— 它必须落 unchecked，"
        "那才是「没人查过它的区域」在报告里该有的样子"
    )

    excluded = grade_account(_account(country="US"), bundle)
    assert excluded.status is AccountStatus.IRRELEVANT
    assert excluded.reason is EligibilityReason.RULE_FIRED
