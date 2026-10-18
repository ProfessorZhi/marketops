"""Profile 装载的测试。

本文件锁三件事：

1. **SOP 声明层**（``bundle.declared`` / ``role_matrix`` / ``profile``）——
   业务到底说了什么。这一层必须逐条可断言，否则「规则接上了」只是句口号。
2. **两个轴在声明层就已经分开**：``grades`` 定的是路线等级，
   ``x_rules`` 定的是账号准入。它们混在一个文件里，但不是一个东西。
3. **装载必须 fail-closed**：schema 不认、锚点缺失、排除项为空、词表歧义这类
   情况要显式失败，而不是装载出一份看起来正常的规则。

第 3 条尤其重要，因为这些坏法的共同点是**不会让输出变难看** ——
装载出来的东西结构完好，区别只在于它是错的。
"""

from __future__ import annotations

import ast
import os
import re
import shutil
from dataclasses import fields
from pathlib import Path

import pytest
import yaml

from marketops.config import Settings
from marketops.domain.enums import (
    AccountStatus,
    BusinessFunction,
    Grade,
    RecordBlockPriority,
    RoleClass,
)
from marketops.domain.errors import ConfigError
from marketops.domain.facts import (
    ACCOUNT_FACT_NAMES,
    ACCOUNT_QUALIFICATION_FACT_NAMES,
    CANDIDATE_FACT_NAMES,
    ROUTE_FACT_NAMES,
    clause_is_well_formed,
    parse_clause,
)
from marketops.domain.profile import (
    CLASSIFICATION_AXES,
    CLASSIFICATION_UNKNOWN,
    DeclaredScenarioBlock,
)
from marketops.domain.sources import SOURCE_METHODS
from marketops.adapters.profile_loader import (
    CONTACT_ROUTE_SUBJECT,
    GRADING_FILE,
    PROFILE_FILE,
    ROLE_MATRIX_FILE,
    SCENARIO_MATRIX_FILE,
    SOP_GRADING_SCHEMA,
    SOP_PROFILE_SCHEMA,
    SOP_ROLE_MATRIX_SCHEMA,
    SOP_SCENARIO_MATRIX_SCHEMA,
    AccountRule,
    DeclaredGrading,
    ProfileBundle,
    _SCENARIO_BLOCK_FIELDS,
    _unknowns,
    load_profile,
    load_profile_from_scene_files,
    read_scene_files,
)
from tests.fakes import synthetic_scene_files

REAL_PROFILE_ID = "uk-acme-natural-grass"


def _real_profile_dir(repo_root: Path) -> Path:
    return repo_root / "profiles" / REAL_PROFILE_ID


def _requires_of(bundle, grade: Grade) -> tuple[str, ...]:
    rule = bundle.rule_for(grade)
    assert rule is not None and rule.requires is not None, f"{grade.value} 没有可求值的条件"
    return rule.requires


# --- 基本装载 ---------------------------------------------------------------


def test_profile_loads(bundle) -> None:
    assert bundle.profile_id == REAL_PROFILE_ID
    # 这里原本断言 ``bundle.profile_dir.is_dir()``。``profile_dir`` 是纯规则层
    # 依赖 ``pathlib`` 的唯一理由，而它唯一的读者就是这一行 —— 已移除；目录是
    # **装载器的输入**（``Settings.profile_dir()``），不是规则的一部分。
    # 目录是否存在由 ``test_profile_files_are_all_present`` 与
    # ``test_missing_profile_directory_raises`` 覆盖，那才是它该被测的地方。
    # 这一行改成断言**内容**确实来自那份声明：``profile.name`` 是从 profile.yaml
    # 的 ``name`` 键读出来的，而不是 ``settings.profile`` 的兜底值 ——
    # 后者在 profile.yaml 写了个别的名字时会静默顶替，而加载照样成功。
    assert bundle.profile is not None
    assert bundle.profile.name == REAL_PROFILE_ID


def test_profile_declares_the_sop_schema(bundle) -> None:
    """三个文件的 schema 名必须被真的校验过，不是被忽略。

    这条与 ``test_loader_fails_closed`` 的 schema 用例是一对：一个证明读到的是
    声明的那一版，一个证明读错版本会失败。
    """
    assert bundle.profile.schema == SOP_PROFILE_SCHEMA
    assert bundle.role_matrix.schema == SOP_ROLE_MATRIX_SCHEMA
    assert bundle.declared.schema == SOP_GRADING_SCHEMA


def test_profile_files_are_all_present(repo_root) -> None:
    directory = _real_profile_dir(repo_root)
    for name in (PROFILE_FILE, ROLE_MATRIX_FILE, GRADING_FILE):
        assert (directory / name).is_file(), f"缺少 {name}"


def test_status_is_absent_because_the_sop_does_not_declare_one(bundle) -> None:
    """SOP 的 profile.yaml 不声明生命周期状态 —— 装载层不替它发明一个。"""
    assert bundle.status is None


# --- 定级的主语：路线，不是账号 ----------------------------------------------


def test_grading_subject_is_the_contact_route(bundle) -> None:
    """``subject: contact-route`` 是这次收敛最重要的一处修正。

    等级挂在**一条路线**上（Account + Person/Department + Role + literal email
    + Evidence），不挂在账号上。挂在账号上时，同一个账号的多条路线共享一个等级，
    「组织对、人不对」就无处安放 —— 而那是这类研究里最常见的结果之一。
    """
    assert bundle.declared.subject == CONTACT_ROUTE_SUBJECT
    assert bundle.summary()["route_grade_subject"] == CONTACT_ROUTE_SUBJECT


def test_bad_subject_is_rejected(repo_root, tmp_path) -> None:
    broken = _mutated_settings(tmp_path, repo_root, "subject_is_account")
    with pytest.raises(ConfigError, match="只支持"):
        load_profile(broken)


# --- 路线等级：SOP L125–L129 逐条 -------------------------------------------------


def test_all_five_grades_are_declared(bundle) -> None:
    """A/B/C/D/X 五级齐全。缺一级不是「那一级没有规则」，是 SOP 没被装载完整。"""
    assert set(bundle.declared.grades) == set(Grade)
    for grade in Grade:
        entry = bundle.declared.grades[grade]
        assert entry.label, f"{grade.value} 缺 label"
        assert entry.description, f"{grade.value} 缺 description"
        assert entry.sop_reference, f"{grade.value} 缺 SOP 出处"


def test_a_and_b_both_require_a_literal_email(bundle) -> None:
    """SOP L125 与 L126 的原文都要求「真实公开业务邮箱」。

    旧 profile 把 B 读成「有具名角色但没有邮箱」，那是一条**错的**规则：
    它会让没有邮箱的岗位也拿到等级，「拿到路线的比例」看起来比实际高，
    而没有任何一处会报错。A/B 都要 literal 邮箱，是这次收敛里第二处关键修正。
    """
    for grade in (Grade.A, Grade.B):
        assert "has_literal_email" in _requires_of(bundle, grade), f"{grade.value} 没有要求 literal 邮箱"


def test_a_and_b_both_require_a_strong_binding(bundle) -> None:
    """强绑定 = 邮箱与人/岗位在同一个记录块里。没有它，等级只是在给域名打分。"""
    for grade in (Grade.A, Grade.B):
        assert "binding==strong" in _requires_of(bundle, grade)


def test_a_is_the_decision_and_demo_function(bundle) -> None:
    clause = [c for c in _requires_of(bundle, Grade.A) if c.startswith("role_class in")]
    assert clause == ["role_class in [decision, influencer]"]


def test_b_is_the_operational_band(bundle) -> None:
    assert "role_class==operational" in _requires_of(bundle, Grade.B)


def test_c_requires_an_official_business_entry(bundle) -> None:
    """C 是「官方**业务**入口」，不是「任何官方邮箱」。"""
    requires = _requires_of(bundle, Grade.C)
    assert "entry_kind==official_business_entry" in requires
    assert "binding==account" in requires

    clause = [c for c in requires if c.startswith("business_function in")]
    assert clause, "C 必须把业务口径钉在某个取值集合上"
    # 取值集合逐字取自 SOP L127 —— 这里断言的是「没有多出别的口径」。
    assert "procurement" not in clause[0], "采购归口不在 L127 的口径列表里，不得替 SOP 扩权"


def test_d_requires_a_generic_official_mailbox(bundle) -> None:
    """D 是「绑定账号的公开真实泛化官方邮箱」，与 C 的区别就是入口性质。"""
    requires = _requires_of(bundle, Grade.D)
    assert "entry_kind==generic_official" in requires
    assert "binding==account" in requires


def test_x_is_the_only_grade_with_no_conditions(bundle) -> None:
    """X 是收尾级，语义固定，不依赖任何事实 —— 这正是 ``requires == ()`` 的含义。

    同时断言 A/B/C/D **都不是**空条件：四级都要有正面定义。给某一级配上
    恒真会造出一个「没被别人拿走就算它」的兜底级，与那一级的定义无关。
    """
    assert _requires_of(bundle, Grade.X) == ()
    for grade in (Grade.A, Grade.B, Grade.C, Grade.D):
        assert _requires_of(bundle, grade), f"{grade.value} 的条件是空的 —— 那它不是一条正面定义"


def test_fallback_grade_is_rejected(repo_root, tmp_path) -> None:
    """``fallback_grade`` 已从 profile 里移除，且装载层显式拒绝它回来。

    旧 profile 的 ``fallback_grade: D`` 配着 D 的 ``conditions: ['true']``，
    实际是一条「没被别人拿走就算 D」的规则 —— 它会让任何未被识别的页面
    冒充「低优先触达储备」。没匹配上任何一级时应当返回 X + RULES_UNKNOWN。
    """
    broken = _mutated_settings(tmp_path, repo_root, "fallback_grade")
    with pytest.raises(ConfigError, match="不得声明 fallback_grade"):
        load_profile(broken)


def test_no_route_rule_mentions_blocked(bundle) -> None:
    """抓不到**不是** X（SOP L119）—— 而且这条约束在规则文本里看得见。

    把 blocked 写进任何一级的条件里，都会让「我们没看到」变成对目标的判断。
    """
    conditions = [c for grade in Grade for c in _requires_of(bundle, grade)]
    offenders = [c for c in conditions if "block" in c.lower()]
    assert not offenders, f"等级条件里出现了 blocked：{offenders}"


# --- 账号准入：与等级正交的那条链路 ------------------------------------------


def test_account_rules_are_separate_from_route_rules(bundle) -> None:
    """``x_rules`` 定的是**账号**，不是路线等级 —— 两者在同一个文件里，不是一个东西。

    旧 profile 把两者混在 x_rules 里，于是「账号不合格」和「路线是 X」成了
    同一件事。分开之后，装载出来的对象也是两个：``account_rules`` 与 ``rules``。
    """
    assert bundle.account_rules, "SOP 没有声明任何账号准入规则"
    assert len(bundle.account_rules) == len(bundle.declared.x_rules)
    assert {rule.status for rule in bundle.account_rules} <= {
        AccountStatus.IRRELEVANT,
        AccountStatus.HOLD,
    }


def test_account_rule_ids_are_unique(bundle) -> None:
    ids = [rule.id for rule in bundle.account_rules]
    assert len(set(ids)) == len(ids), f"账号规则 id 重复：{ids}"
    assert all(ids), "账号规则存在空 id"


def test_unevaluable_account_rules_are_reported_not_silently_dropped() -> None:
    """还没有事实生产者的排除规则**永不命中**，但必须被记下来。

    把它们当成恒真会一次清空整个名单；当成不存在则会让审计以为「排除规则都已生效」。
    两种都比「留着并写明」危险。

    这条测试**刻意不读真实 profile**：收敛之后真实 profile 一条欠账都没有
    （见 ``test_every_account_rule_is_evaluable``），拿它来测这个机制会退化成
    ``[] == []`` —— 看起来仍然通过，实际上什么都没测。这里构造一份
    「一条求不了值、一条能求值」的 bundle，直接钉住 ``summary()`` 的记账行为。
    """
    scene_files = synthetic_scene_files()
    bundle = ProfileBundle(
        profile_id="probe",
        status=None,
        rules={},
        role_terms=(),
        scene_version=scene_files.version,
        scene_files=scene_files,
        account_rules=(
            AccountRule(
                id="unevaluable",
                description="引用的是还没有生产者的事实",
                status=AccountStatus.IRRELEVANT,
                requires=None,
                declared_when="account.attributes.environment == 'indoor'",
            ),
            AccountRule(
                id="evaluable",
                description="引用账号自带的字段",
                status=AccountStatus.IRRELEVANT,
                requires=("!country in [gb, uk]",),
            ),
        ),
    )
    unchecked = [rule.id for rule in bundle.account_rules if rule.requires is None]

    assert unchecked == ["unevaluable"], "求不了值的规则没有被识别出来"
    assert set(unchecked) <= {rule.id for rule in bundle.account_rules}

    summary = bundle.summary()
    assert summary["account_rules_total"] == len(bundle.account_rules)
    assert summary["account_rules_unchecked"] == unchecked, (
        "求不了值的规则必须出现在摘要里 —— 漏报它等于让审计以为排除规则都已生效"
    )


def test_every_account_rule_is_evaluable(bundle) -> None:
    """收敛后的真实状态：账号规则的子句**全部**可求值，没有欠账。

    可求值**不等于**判得了。**装载期的**可求值说的是「子句语法合法、事实名在
    白名单里」（这份清单回答的就是它，所以它是空的）；**运行期的**判不判得了
    说的是「那个事实现在有值吗」—— 引用候选事实的那些子句手上是 ``None``
    （未确认），因此它们**不决定**子句，规则落进 ``outcome.unchecked``。

    两个问题由两个东西回答，而且**不互相代替**：本清单（``ungradeable`` /
    ``account_rules_unchecked``）答装载期，``outcome.unchecked`` 答运行期。
    """
    missing = [rule.id for rule in bundle.account_rules if rule.requires is None]
    assert not missing, f"这些规则还没有可求值的子句：{missing}"
    assert bundle.summary()["account_rules_unchecked"] == []


def test_account_rule_facts_are_declared_at_load_time(bundle) -> None:
    """账号规则引用的事实分两类，且**都**在装载期白名单里。

    * ``ACCOUNT_FACT_NAMES`` —— ``AccountRecord`` 自己的字段，已经有生产者；
    * ``CANDIDATE_FACT_NAMES`` —— 场地资产属性（环境 / 组织形态 / 表面 / 抓取统计），
      还没有生产者。

    两者共用同一个白名单（``ACCOUNT_QUALIFICATION_FACT_NAMES`` 是并集），但**不合并
    成一张词表**：混在一起会让「账号字段缺一个」和「场地资产还没被抽取」在数据上
    长得一样，而它们的修法完全不同。这条同时断言两类都还在用 —— 只剩一类时，
    要么是候选事实那批规则丢了，要么是区域那条唯一可判的规则丢了。
    """
    used: set[str] = set()
    for rule in bundle.account_rules:
        assert rule.requires is not None, f"{rule.id} 没有可求值的子句"
        for clause in rule.requires:
            assert clause_is_well_formed(clause, ACCOUNT_QUALIFICATION_FACT_NAMES) is None
            parsed = parse_clause(clause)
            assert parsed is not None
            used.add(parsed.fact)

    assert used & set(ACCOUNT_FACT_NAMES), "没有任何规则在用账号自带的字段"
    assert used & set(CANDIDATE_FACT_NAMES), "没有任何规则在等候选事实的生产者"


def test_ungradeable_is_empty_once_every_rule_has_requires(bundle) -> None:
    """``ungradeable`` 只统计**还求不了值**的规则引用了什么 —— 现在没有这样的规则。

    把已经编译出 ``requires`` 的规则也算进来，这份清单会报出一批「其实已经有了」的
    缺口，而一份带假阳性缺口清单的下场是没人再看它。

    空**不等于**「这些条件都判得了」：引用候选事实的那几条，引擎手上的值仍是
    ``None``。这份清单回答的是「还有哪些规则没写完」，不是「还有哪些事实没有值」——
    后者由 ``CANDIDATE_FACT_NAMES`` 回答（那是「谁该生产它」的清单）。
    """
    assert bundle.declared.ungradeable == ()
    assert all(rule.requires is not None for rule in bundle.account_rules)


# --- 角色词表 ---------------------------------------------------------------


def test_role_terms_come_from_the_sop_role_matrix(bundle) -> None:
    """角色词直接来自 SOP 的 ``role_groups[*].match``。

    SOP 声明 ``match`` 是「子串匹配、大小写不敏感」，与 ``extract_roles`` 的语义
    逐字相同，所以这里可以直取 —— 这一条同时证明了词表**真的进了**绑定的输入。
    """
    assert bundle.role_terms, "SOP 声明了角色组，但词表是空的"
    assert "grounds manager" in bundle.role_terms
    assert len(set(bundle.role_terms)) == len(bundle.role_terms), "角色词重复"


def test_every_role_term_carries_a_class_and_a_business_function(bundle) -> None:
    """每个词都要能被归到一个 class 与一个业务口径。

    class 回答「这个岗位在路线里扮什么角色」，business_function 回答
    「它落在哪个业务口径上」—— 两个独立事实。压成一个字段，
    C 级「与场地业务口径绑定」这条规则就没法表达。
    """
    for term in bundle.role_terms:
        assert term in bundle.role_classes, f"{term!r} 没有分类"
        assert bundle.role_classes[term] is not RoleClass.UNKNOWN, f"{term!r} 的分类是 unknown"
        assert term in bundle.business_functions


def test_excluded_terms_are_not_role_terms(bundle) -> None:
    """负面词表**不得**混进 role_terms。

    正面词表产出角色证据，负面词表表达「这个岗位说明不了什么」。合并之后，
    ``marketing`` / ``catering`` 这类词会反过来变成「这是个角色」的正面证据 ——
    一次静默的语义反转。
    """
    assert bundle.excluded_terms, "SOP 声明了排除类别，但没有装载到词"
    assert not set(bundle.excluded_terms) & set(bundle.role_terms)


def test_excluded_terms_are_classified_as_excluded(bundle) -> None:
    for term in bundle.excluded_terms:
        assert bundle.role_classes[term] is RoleClass.EXCLUDED


def test_finance_is_excluded_not_a_procurement_role(bundle) -> None:
    """SOP L129 把财务列入 X；SOP L79 把 Procurement 列为优先角色。两回事。

    旧 profile 把 ``finance director`` 放在 procurement 组里 —— 一条把「财务」
    读成「采购归口」的**正面**规则。这种错误不会报错，只会让财务部门持续收到
    场地划线产品的联系请求。
    """
    assert "finance" in bundle.excluded_terms
    assert bundle.role_classes["finance"] is RoleClass.EXCLUDED
    assert bundle.business_functions["finance"] is BusinessFunction.FINANCE

    for term in ("procurement", "purchasing"):
        assert term in bundle.role_terms
        assert bundle.role_classes[term] is not RoleClass.EXCLUDED


def test_no_positive_group_is_a_finance_business_function(bundle) -> None:
    """正面组里不得有任何一组的业务口径是 finance。"""
    offenders = [
        group.id
        for group in bundle.role_matrix.groups
        if group.business_function is BusinessFunction.FINANCE
    ]
    assert not offenders, f"这些正面角色组的业务口径是 finance：{offenders}"


def test_admin_and_customer_service_are_excluded(bundle) -> None:
    """SOP L129/L131：一般行政、无场地关联的客服与预订都不得进入有效主池。

    L131 补了一句要害的话 —— 行政即使公开也**不能**因「可能转发」进入主池。
    """
    for term in ("administrator", "receptionist", "customer service", "catering", "box office"):
        assert term in bundle.excluded_terms, f"{term!r} 应当被排除"


def test_bookings_are_only_included_with_a_venue_context(bundle) -> None:
    """SOP L131：Bookings 只有在**明确是场地预约/场馆运营**时进 C。

    因此排除表里的是票务与会员（``box office`` / ``membership secretary``），
    正面词表里的是场地租赁（``venue hire`` / ``pitch hire``）。
    """
    assert "venue hire" in bundle.role_terms
    assert "pitch hire" in bundle.role_terms
    assert "membership secretary" in bundle.excluded_terms
    assert bundle.role_classes["venue hire"] is not RoleClass.EXCLUDED
    assert bundle.business_functions["venue hire"] is BusinessFunction.BOOKINGS


# --- P1–P4 是记录块语义，不是 URL 路径分级 -----------------------------------


def test_page_priorities_are_the_record_block_semantics(bundle) -> None:
    """SOP L105 的 P1–P4 说的是「邮箱与角色是否在同一 record block 内」。

    旧 profile 把一张 URL glob 表也叫作 P1/P2/P3，于是「先抓 contact 页」
    被读成「contact 页出来的邮箱就是 P1」—— 两件不同的事共用一个名字。
    """
    priorities = bundle.declared.page_priorities
    assert set(priorities) == {priority.value for priority in RecordBlockPriority}
    assert all(text for text in priorities.values())


def test_p3_is_documented_as_cannot_be_a_route(bundle) -> None:
    """P3 的定义（有场地/责任关系证据但没有邮箱）与「路线必须有邮箱」互斥。"""
    assert "RouteOpportunity" in bundle.declared.page_priorities["P3"]


def test_url_hints_are_not_called_p1_p2_p3(bundle) -> None:
    """URL 侧的表叫 ``page_discovery_hints``，它的优先级名**不是** P1/P2/P3。"""
    hints = bundle.profile.page_discovery_hints
    assert hints, "profile 没有声明页面发现提示"
    assert not {key for key in hints} & {priority.value for priority in RecordBlockPriority}


# --- 邮箱 local part 词表 -------------------------------------------------------


def test_business_entry_locals_are_tokens_not_addresses(bundle) -> None:
    """这张表按**词元**匹配（local part 先按 . _ - + 切分）。

    写成整段 local part 或带上 ``@`` 都会让匹配永远不命中 —— 装载层因此拒绝它。
    """
    table = bundle.declared.business_entry_locals
    assert table, "business_entry_locals 为空会让 C 级永不命中"
    for token in table:
        assert not any(ch in token for ch in " @._-+"), f"{token!r} 不是单个词元"
        assert token == token.lower()


def test_generic_mailbox_locals_are_whole_locals(bundle) -> None:
    """这张表写的是**整个** local part（``info`` / ``enquiries``），不是词元。"""
    locals_ = bundle.declared.generic_mailbox_locals
    assert locals_, "generic_mailbox_locals 为空会让 D 级永不命中"
    assert "info" in locals_
    assert all(token == token.lower() for token in locals_)


# --- 合规与其他声明 -----------------------------------------------------------


def test_compliance_flags_stay_closed(bundle) -> None:
    """合规开关是**硬边界**，不是可调参数 —— 这条守的是「不得绕过访问控制」。

    判据刻意写成「除这两个必须为真的开关，**其余一律必须为假**」，而不是逐个
    列出那几个权限开关的名字：一来那几个名字在源码里逐字出现会被
    ``tests/test_no_evasion.py`` 的禁词扫描判成「在做规避」，二来「其余全假」
    更强 —— SOP 将来新增一个权限开关，它默认也必须是关的。
    """
    compliance = bundle.profile.compliance
    assert compliance.get("public_pages_only") is True
    assert compliance.get("respect_robots_txt") is True

    allowed_true = ("public_pages_only", "respect_robots_txt")
    others = {key: value for key, value in compliance.items() if key not in allowed_true}
    assert others, "compliance 段没有其余开关 —— 这条断言会假通过"
    opened = sorted(key for key, value in others.items() if value)
    assert not opened, f"compliance 里有被打开的权限开关：{opened}"


def test_exclusions_are_loaded(bundle) -> None:
    exclusions = bundle.profile.exclusions
    assert exclusions, "profile 没有声明任何排除项"
    ids = bundle.profile.exclusion_ids
    assert len(set(ids)) == len(ids), f"排除项 id 重复：{ids}"
    assert all(ids), "排除项存在空 id"


def test_scoring_is_declared_and_does_not_participate_in_grading(bundle) -> None:
    """连续分只在排序与人工复核里用。它的权重必须是一份完整的分配。

    权重和不为 1 说明分数不是 0..1 的，那么任何「0.6 分以上」的人工复核阈值
    都会失去含义。这类错误在分数曲线里看不出来，只有在权重表上看得出来。
    """
    scoring = bundle.declared.scoring
    weights = scoring.get("weights")
    assert isinstance(weights, dict) and weights, "SOP 没有声明打分权重"
    assert abs(sum(float(value) for value in weights.values()) - 1.0) < 1e-9

    penalties = scoring.get("penalties")
    assert isinstance(penalties, dict) and penalties, "SOP 没有声明惩罚项"
    assert all(float(value) <= 0 for value in penalties.values()), "惩罚项不应为正"

    # 分数不得出现在任何一级的条件里 —— 定级是规则的，不是阈值的。
    for grade in Grade:
        for clause in _requires_of(bundle, grade):
            assert "score" not in clause.lower(), f"{grade.value} 的条件引用了分数"


# --- 摘要的可序列化 -----------------------------------------------------------


def test_summary_is_serialisable(bundle) -> None:
    summary = bundle.summary()
    assert summary["profile_id"] == REAL_PROFILE_ID
    # 五级全部已定义：SOP 的 A/B/C/D 编译成了可求值子句，X 的语义不依赖配置。
    assert summary["grades_defined"] == ["A", "B", "C", "D", "X"]
    assert summary["grades_unknown"] == []
    assert summary["role_terms_count"] > 0
    assert summary["excluded_terms_count"] > 0
    assert isinstance(summary["unknowns"], list)
    assert summary["unknowns"] == list(bundle.unknowns)


# --- 时限声明（§9·2b）--------------------------------------------------------
#
# 时限是**业务口径**，所以它住在 profile 里、与 ``search.max_queries`` 同一个位置。
# 这一节钉两件事：真实 profile 声明了它、且**这些数字没有标定过**（``calibrated``
# 为假，而版本号自己也这么说）；以及**整块缺失是允许的** —— 缺的那一侧在调用时
# 显式失败，不在装载期补一组缺省天数。


def test_the_real_profile_declares_a_freshness_policy(bundle) -> None:
    """真实 profile 有时限声明，且它自报**未标定**。

    数字本身不在这里钉：180 / 730 是业务判断，钉住它们会让一次合法的口径调整
    变成一次红。钉住的是**标定状态**——它决定每一行产出的 ``currentness`` 该怎么读，
    而验收报告要说的正是「上面那些 currentness 出自一份未标定的口径」。
    """
    policy = bundle.freshness
    assert policy is not None, "真实 profile 没有声明时限 —— 那么一次运行会失败关闭"
    assert policy.calibrated is False
    assert "uncalibrated" in policy.policy_version, (
        "版本号必须与标定状态一起变：批准那组天数时要换一个不再自称未标定的版本号"
    )


def test_summary_reports_calibration_alongside_the_numbers(bundle) -> None:
    """摘要要**同时**报出天数与标定状态。

    只报天数会让「有人批准过的」与「我们随手写的」在报告上逐字相同。
    """
    reported = bundle.summary()["freshness"]

    assert reported is not None
    assert reported["calibrated"] is False
    assert reported["policy_version"] == bundle.freshness.policy_version
    assert reported["current_within_days"] == bundle.freshness.current_within_days
    assert reported["historical_after_days"] == bundle.freshness.historical_after_days


def test_freshness_block_may_be_absent(repo_root, tmp_path) -> None:
    """整块不写 ⇒ 装载**成功**，``freshness`` 为 ``None``，摘要把这一格报成 ``None``。

    与「写坏了」分开：一份 profile 可以先只有规则、后有时限（与 ``decisions`` 同一条），
    而**缺的那一侧在调用时失败**（``policy_of`` 抛 ``ConfigError``，见
    ``tests/test_application_freshness.py``）。
    """
    probe = _mutated_settings(tmp_path, repo_root, "freshness_block_absent")

    loaded = load_profile(probe)

    assert loaded.freshness is None
    assert loaded.summary()["freshness"] is None


def test_every_axis_has_a_declared_classification_vocabulary(bundle) -> None:
    """**四条轴都各有词表。** 这是 §10 那句「四个事实要有真实生产者」的装载侧判据。

    词表就是资产事实那一侧的知识来源：少一条轴，那条轴上的读数恒为 ``unknown``
    （理由会说「profile 没有为这条轴声明词表」），而 ``unknown`` 不决定任何子句 ——
    于是点它的规则**永远**停在 ``unchecked``。报告上那与「证据不足」逐字相同，
    而两者要的处置完全不同（一个去补 profile，一个去再查一轮）。
    """
    vocabulary = bundle.profile.classification
    assert vocabulary is not None, "真实 profile 没有分类词表 —— 四条轴就没有生产者"
    assert {axis.axis for axis in vocabulary.axes} == set(CLASSIFICATION_AXES)

    # 取值不得是那个保留写法 —— 装载器判过一次，这里是它在真实 profile 上的读数：
    # 「查出来是 unknown」与「没查出来」一旦合流，这一层就没有存在的理由了。
    for axis in vocabulary.axes:
        assert CLASSIFICATION_UNKNOWN not in axis.value_names, axis.axis
        for value in axis.values:
            assert value.phrases or value.host_suffixes, f"{axis.axis}.{value.value} 没有判据"

    declared = set(bundle.profile.exclusion_ids)
    assert declared, "profile 一条排除项都没声明 —— 词表的 excludes 就无处可指"
    for excluded in vocabulary.exclusion_ids():
        assert excluded in declared, f"词表回指了未声明的排除项 {excluded!r}"


def test_classification_block_may_be_absent(repo_root, tmp_path) -> None:
    """整块不写 ⇒ 装载**成功**，``classification`` 为 ``None``。

    与「写坏了」分开（与 ``decisions`` / ``freshness`` 同一条）：一份 profile 可以
    先只有规则、后有分类。缺的那一侧在**运行时**显式报成 ``unknown``，
    理由说的是「profile 没有为这条轴声明词表」—— 不是造一张空词表糊过去，
    那样「profile 没说怎么分类」与「这一页什么都没命中」就再也分不开了。
    """
    probe = _mutated_settings(tmp_path, repo_root, "classification_block_absent")

    loaded = load_profile(probe)

    assert loaded.profile is not None
    assert loaded.profile.classification is None


def test_search_locales_may_be_absent(repo_root, tmp_path) -> None:
    """``search.locales`` 不写 ⇒ 装载**成功**，``search_locales`` 是 ``()``。

    与「写坏了」分开（与 ``freshness`` / ``classification`` 同一条）：缺这一格不是
    一份坏 profile，是一份**说不清问的是哪个市场**的 profile。失败发生在检索那一步
    （``application/queries.py::market_locale`` 抛 ``ConfigError`` 点名这一格），
    而不是装载那一步 —— 装载层在这里造一个看起来合理的缺省语言才是危险的：
    那份缺省没有任何出处，而每一次检索都会照它发出。
    """
    probe = _mutated_settings(tmp_path, repo_root, "locales_absent")

    loaded = load_profile(probe)

    assert loaded.profile is not None
    assert loaded.profile.search_locales == ()


def test_the_declared_locales_reach_the_typed_field(bundle) -> None:
    """真实 profile 里那两条声明**真的**被读进了类型化的字段。

    这一条是上面那几条负控的正控：只写「坏声明会被拒」的话，一个**永不解析**这一格
    的装载器（``search_locales`` 恒为 ``()``）会让它们全绿，而缺省的失败关闭根本
    不会被触发 —— 那时每一次真检索都会因为「没声明语言」而失败，而判据说的是
    「装载层守住了」。
    """
    assert bundle.profile is not None
    assert bundle.profile.search_locales == ("en-GB",)

    # 国家那一格与语言那一格是**两格**：``country`` 是两位大写（不是从 locale 里
    # 拆出来的），locale 是完整的 BCP-47 标签（地区部分留着，供读的人核对）。
    assert bundle.profile.country == "GB"


def test_the_real_profile_has_no_unknowns(bundle) -> None:
    """真实 profile 不再有欠账：所有规则都编译成了可求值子句。

    ``has_unknown_rules`` 从这里起是 ``False`` —— 它问的是「引擎还判不了吗」，
    **不是**「有证据吗」。两者现在分开了：判得了（子句齐），但没有证据
    （候选事实为 ``None``），账号因此仍然停在 ``discovered``。
    """
    assert bundle.unknowns == ()
    assert bundle.has_unknown_rules is False


def test_unknowns_name_the_account_side_not_a_silent_business() -> None:
    """``unknowns`` 记的是**引擎为什么还判不了**。

    路线侧已经没有缺口，剩下的全部来自账号侧 —— 排除规则引用了还没有生产者的
    研究事实。措辞必须指向那里：写「业务没给规则」会把人送去业务那边，
    而正确地修法是写事实生产者。

    真实 profile 现在一条欠账都没有（见 ``test_the_real_profile_has_no_unknowns``），
    所以这条测试**构造**一份有欠账的声明层来钉住措辞。用真实 profile 测的话，
    清单清空之日就是这条测试失去意义之时，而它看起来仍然通过。
    """
    declared = DeclaredGrading(
        schema=SOP_GRADING_SCHEMA,
        profile=REAL_PROFILE_ID,
        subject=CONTACT_ROUTE_SUBJECT,
        x_rules=(
            AccountRule(
                id="venue_is_indoor",
                description="室内场馆",
                status=AccountStatus.IRRELEVANT,
                requires=None,
                declared_when="account.attributes.environment == 'indoor'",
            ),
        ),
        grades={},
        ungradeable=("account.attributes", "account.pages"),
    )
    unknowns = _unknowns(declared)

    assert all(
        item.startswith("account.eligibility.") or item.startswith("待生产的事实：")
        for item in unknowns
    )
    assert any(item.startswith("account.eligibility.venue_is_indoor") for item in unknowns), (
        "求不了值的规则必须以规则 id 指名道姓地列出来"
    )
    assert "待生产的事实：account.attributes、account.pages" in unknowns


# --- 引擎方言的边界：结构性证据 --------------------------------------------

#: 引擎子句方言的独立复述（见 ``application/grading.py`` 与 ``facts.py``）：
#: ``fact`` / ``!fact`` / ``fact==value`` / ``fact in [a, b, c]``，事实名取自
#: ``ROUTE_FACT_NAMES``。刻意在测试里重新写一遍，而不是 import 引擎的私有函数 ——
#: 独立实现才能真的检查「两个方言不重合」这件事。
_ENGINE_CLAUSE_RE = re.compile(r"^!?\s*([A-Za-z_][A-Za-z0-9_]*)\s*(?:(?:==|in)\s*.+)?$")


def _is_engine_clause(condition: str) -> bool:
    match = _ENGINE_CLAUSE_RE.match(condition.strip())
    return bool(match) and match.group(1) in ROUTE_FACT_NAMES


def test_engine_clause_grammar_probe_is_exact() -> None:
    """阳性对照 —— 上面那个复述必须能认出真正的引擎子句。

    没有这一条，「没有一条 SOP 条件能被引擎求值」可能只是因为
    ``_is_engine_clause`` 永远返回 False。
    """
    assert _is_engine_clause("has_literal_email")
    assert _is_engine_clause("!has_conflict")
    assert _is_engine_clause("binding==strong")
    assert _is_engine_clause("role_class in [decision, influencer]")
    # 负数对照：形状相近但不是引擎子句的，不能算数。
    assert not _is_engine_clause("account.has_literal_email")
    assert not _is_engine_clause("has_literal_email and binding==strong")
    assert not _is_engine_clause("account.target_match.score >= 0.75")


def test_every_grade_condition_is_an_engine_clause(bundle) -> None:
    """A/B/C/D/X 的条件**全部**落在引擎方言内 —— 这是「SOP 已编译」的证据。

    收敛之前这里断言的是反面（「一条都求不了值」）。现在反过来：某天某条条件
    掉出方言，装载层会先失败（``clause_is_well_formed``），这条测试是第二道。
    """
    conditions = [c for grade in Grade for c in _requires_of(bundle, grade)]
    assert conditions, "没有装载到任何等级条件"
    outside = [c for c in conditions if not _is_engine_clause(c)]
    assert not outside, f"这些等级条件不在引擎方言内：{outside}"


def test_sop_original_expressions_are_not_engine_clauses(bundle) -> None:
    """``x_rules[*].when`` 是 SOP **原话**，保留给审计看，引擎不求值它。

    它们**不得**恰好落在引擎方言里：那会让人以为引擎正在求值 SOP 原文，
    而实际上引擎求的是旁边那条 ``requires``。两份东西长得一样时，
    其中一份被改掉是不会有人发现的。
    """
    expressions = [rule.declared_when for rule in bundle.declared.x_rules]
    assert expressions, "没有装载到任何 SOP 原话表达式"
    expressible = [e for e in expressions if _is_engine_clause(e)]
    assert not expressible, f"这些 SOP 原话恰好落在引擎方言内，两者会长得像同一份东西：{expressible}"


# --- 装载必须 fail-closed ---------------------------------------------------

_MUTATIONS: tuple[tuple[str, str], ...] = (
    ("profile_schema", "只支持"),
    ("role_schema", "只支持"),
    ("grading_schema", "只支持"),
    ("subject_is_account", "只支持"),
    ("profile_name", "缺少 name"),
    ("exclusions_empty", "exclusions 必须是非空列表"),
    ("role_groups_empty", "role_groups 不得为空"),
    ("role_group_match_empty", "match 为空"),
    ("x_rules_empty", "x_rules 不得为空"),
    ("fallback_grade", "不得声明 fallback_grade"),
    ("x_rule_empty_requires", "空列表"),
    ("x_rule_without_anything", "没有任何内容"),
    ("page_priorities_missing_p4", "缺少"),
    ("business_entry_locals_empty", "business_entry_locals"),
    ("generic_mailbox_locals_empty", "generic_mailbox_locals"),
    ("role_group_unknown_class", "不得是 unknown"),
    ("role_term_cross_class_substring", "互为子串"),
    ("role_term_in_both_tables", "同时出现在"),
    # 决策题面（profile.yaml 的 decisions 块）。每一条都对应一类「改坏了但看起来
    # 很正常」的声明 —— 一道没有 instructions 的题、一个没有描述的选项，装载出来
    # 之后都长得像一份完好的题面，而它们的后果要到某一次判定才知道。
    ("decisions_empty", "decisions 要么整个不写"),
    ("decisions_unknown_task", "decisions 的键 非法"),
    ("decisions_not_a_mapping", "必须是映射"),
    ("decisions_without_instructions", "没有 instructions"),
    ("decisions_options_empty", "options 必须是非空映射"),
    ("decisions_option_without_label", "缺 key 或描述"),
    # 时限声明（profile.yaml 的 freshness 块）。**整块缺失是允许的**（见
    # ``test_freshness_block_may_be_absent``），但这三条不是「缺失」而是「写坏了」：
    # 一个不是映射的块、少一个阈值、两个阈值先后颠倒 —— 每一条装载出来都是一份
    # 结构完好的策略，而它的后果要到每一行产出的 currentness 上才看得见。
    ("freshness_not_a_mapping", "freshness 必须是映射"),
    ("freshness_missing_a_threshold", "historical_after_days"),
    ("freshness_windows_inverted", "必须大于"),
    # 分类词表（profile.yaml 的 classification 块）。**整块缺失是允许的**
    # （见 ``test_classification_block_may_be_absent``），下面这些不是「缺失」而是
    # 「写坏了」，而每一条装载出来都是一份结构完好的词表：
    # 轴名写错 ⇒ 那条轴的读数永远不被任何规则看见；取值叫 unknown ⇒ 「查出来是
    # unknown」与「没查出来」重新合流；取值没有判据 ⇒ 它永远匹配不上，而挂在
    # 词表里会让读的人以为这一轴查过了；排除项 id 写错 ⇒ 它只是永不生效，
    # 而那条排除本来是要把一个账号挡在外面的。
    ("classification_not_a_mapping", "必须是映射"),
    ("classification_empty_axes", "非空的 axes 映射"),
    ("classification_unknown_axis", "未知的轴"),
    ("classification_empty_axis", "是空的"),
    ("classification_value_named_unknown", "不得叫"),
    ("classification_value_without_criteria", "没有任何判据"),
    ("classification_duplicate_value", "出现了两次"),
    ("classification_unknown_exclusion", "未声明的排除项"),
    # 检索市场（profile.yaml 的 search.locales）。**整块缺失是允许的**
    # （``test_search_locales_may_be_absent`` —— 缺了它装载得出，只是每次检索都
    # 会在 ``application/queries.py::market_locale`` 那里失败关闭）。下面这几条不是
    # 「缺失」而是「写坏了」，而每一条装载出来都会让检索照常发出、回执逐字同形：
    # 语言这一格收下一条写坏的标签 ⇒ 它永不生效，问的还是 provider 的缺省语言；
    # 收下两条只差地区子标签的标签 ⇒ 它们在下游合成一条，而写下来的时候看着是两条。
    ("locales_not_a_list", "search.locales 必须是列表"),
    ("locales_empty", "是空的"),
    ("locales_malformed_tag", "不是一条 BCP-47 语言标签"),
    ("locales_not_a_string", "不是字符串"),
    ("locales_collapse_to_one_language", "是同一个语言"),
)


def _copy_profile(tmp_path: Path, repo_root: Path, name: str = "probe") -> Path:
    """把真实 profile **逐字节**复制进 ``tmp_path``，返回目标目录。

    逐字节而不是「读成 YAML 再写回去」：``scene_version`` 有一条判据是「同样的字节
    → 同样的版本」，而重新序列化过的 YAML 已经不是同样的字节了 —— 用它做输入，
    那条判据测的是 PyYAML 的 dump 是否稳定，不是版本是否稳定。

    ``profile.yaml`` 的 ``name`` 那一行**改写成 ``name``**（一次针对该行的文本替换，
    不是一次 YAML 往返）。理由是本片新增的一条不变量：一份 profile 的 ``name`` 必须
    逐字等于 ``settings.profile``，而 ``settings.profile`` 就是目录名 —— 一个临时
    部署叫 ``probe`` 时，它复制过来的那份 profile.yaml 若仍自称
    ``uk-acme-natural-grass``，装载层会拒绝它（见
    ``adapters/profile_loader.py::_require_declared_profile_id_matches_workspace``），
    而这条复制出来的 profile 本来就是一个**不同的部署**：它有别的 data_root、
    别的状态库、别的工作区。改这一行是让副本成为它自称的那个部署。

    只复制普通文件，不递归：真实 profile 目录今天只有三个 YAML，而
    ``read_scene_files`` 的递归行为由 ``test_a_guidance_file_enters_the_version``
    自己造目录去测。
    """
    target = tmp_path / "profiles" / name
    target.mkdir(parents=True)
    for path in sorted(_real_profile_dir(repo_root).iterdir()):
        if path.is_file():
            shutil.copyfile(path, target / path.name)
    _rewrite_declared_name(target / PROFILE_FILE, name)
    return target


#: ``profile.yaml`` 里声明名字的那一行。锚在行首，``replace(..., 1)`` 只换第一处 ——
#: 一次整文件替换会把 ``name`` 出现在别处（比如某条规则的描述里）的那些地方也改掉。
_DECLARED_NAME_LINE = re.compile(r"^name:.*$", re.MULTILINE)
_NAME_LINE_COMMENT = "# profile.yaml 的 name 声明（见 _copy_profile）"
_NAME_LINE_TEMPLATE = "name: {name}  {comment}"


def _rewrite_declared_name(path: Path, name: str) -> None:
    """把 ``path`` 第一行 ``name:`` 的值换成 ``name``。**其余字节一个不动。**

    换不动（那一行不在了）时**不报错**：``_mutated_settings`` 里有一个 mutation
    正是 pop 掉 ``name``，那条用例要的就是「装载层因为缺少 name 而拒绝」，构造一个
    名字出来反而会让它测别的东西。
    """
    original = path.read_text(encoding="utf-8")
    replaced, count = _DECLARED_NAME_LINE.subn(
        _NAME_LINE_TEMPLATE.format(name=name, comment=_NAME_LINE_COMMENT).rstrip(), original, count=1
    )
    if count:
        path.write_text(replaced, encoding="utf-8")


def _first_classification_value(profile_doc: dict) -> dict:
    """``classification.axes`` 里第一条轴上的第一个取值。

    取「第一条」而不是按名字找：**测试里不出现市场词**。按名字找就得写出一个
    取值名，而那个名字属于 profile —— 它改名的当天这条用例会变成一条
    「找不到那一项」的 AttributeError，而不是一条关于装载层的判据。
    """
    axes = profile_doc["classification"]["axes"]
    return axes[next(iter(axes))][0]


def _mutated_settings(tmp_path: Path, repo_root: Path, mutation: str) -> Settings:
    """把真实 profile 改坏一处，写进 tmp_path，返回指向它的 Settings。"""
    target = _copy_profile(tmp_path, repo_root)
    documents = {
        name: yaml.safe_load((target / name).read_text(encoding="utf-8"))
        for name in (PROFILE_FILE, ROLE_MATRIX_FILE, GRADING_FILE)
    }
    profile_doc, role_doc, grading_doc = (
        documents[PROFILE_FILE],
        documents[ROLE_MATRIX_FILE],
        documents[GRADING_FILE],
    )

    if mutation == "profile_schema":
        profile_doc["schema"] = "marketops/profile.v0"
    elif mutation == "role_schema":
        role_doc["schema"] = "marketops/profile.v1"  # 串了版本
    elif mutation == "grading_schema":
        grading_doc["schema"] = "marketops/grading.v1"
    elif mutation == "subject_is_account":
        grading_doc["subject"] = "account"
    elif mutation == "profile_name":
        profile_doc.pop("name", None)
    elif mutation == "exclusions_empty":
        profile_doc["exclusions"] = []
    elif mutation == "role_groups_empty":
        role_doc["role_groups"] = []
    elif mutation == "role_group_match_empty":
        role_doc["role_groups"][0]["match"] = []
    elif mutation == "x_rules_empty":
        grading_doc["x_rules"] = []
    elif mutation == "fallback_grade":
        grading_doc["fallback_grade"] = "D"
    elif mutation == "x_rule_empty_requires":
        grading_doc["x_rules"][0]["requires"] = []
    elif mutation == "x_rule_without_anything":
        # 两个键都要 pop：「什么都没有」= 既没有 SOP 原话、也没有可求值子句。
        # 只 pop 一个的话，规则仍然有内容（另一条路径），装载层不该失败。
        grading_doc["x_rules"][0].pop("when", None)
        grading_doc["x_rules"][0].pop("requires", None)
    elif mutation == "page_priorities_missing_p4":
        grading_doc["page_priorities"].pop("P4", None)
    elif mutation == "business_entry_locals_empty":
        grading_doc["business_entry_locals"] = {}
    elif mutation == "generic_mailbox_locals_empty":
        grading_doc["generic_mailbox_locals"] = []
    elif mutation == "role_group_unknown_class":
        role_doc["role_groups"][0]["class"] = "unknown"
    elif mutation == "role_term_cross_class_substring":
        # "manager" 是 "grounds manager"（operational）的子串，塞进 gatekeeper 组
        role_doc["role_groups"][-1]["match"].append("manager")
    elif mutation == "role_term_in_both_tables":
        # 同一个词同时进正面组与排除组
        role_doc["excluded_roles"][0]["match"].append(role_doc["role_groups"][0]["match"][0])
    elif mutation == "decisions_empty":
        profile_doc["decisions"] = {}
    elif mutation == "decisions_unknown_task":
        profile_doc["decisions"]["triage"] = profile_doc["decisions"].pop("page_triage")
    elif mutation == "decisions_not_a_mapping":
        profile_doc["decisions"]["page_triage"] = ["不是映射"]
    elif mutation == "decisions_without_instructions":
        profile_doc["decisions"]["page_triage"].pop("instructions", None)
    elif mutation == "decisions_options_empty":
        profile_doc["decisions"]["page_triage"]["options"] = {}
    elif mutation == "decisions_option_without_label":
        key = sorted(profile_doc["decisions"]["page_triage"]["options"])[0]
        profile_doc["decisions"]["page_triage"]["options"][key] = ""
    elif mutation == "freshness_block_absent":
        profile_doc.pop("freshness", None)
    elif mutation == "freshness_not_a_mapping":
        profile_doc["freshness"] = ["不是映射"]
    elif mutation == "freshness_missing_a_threshold":
        profile_doc["freshness"].pop("historical_after_days", None)
    elif mutation == "freshness_windows_inverted":
        # 把「还能用」的窗口拉到比「需要重验」的线还远 —— 两档窗口于是不可区分。
        profile_doc["freshness"]["current_within_days"] = 900
    elif mutation == "classification_block_absent":
        profile_doc.pop("classification", None)
    elif mutation == "classification_not_a_mapping":
        profile_doc["classification"] = ["不是映射"]
    elif mutation == "classification_empty_axes":
        profile_doc["classification"]["axes"] = {}
    elif mutation == "classification_unknown_axis":
        # 把第一条轴**改名**成一条不是事实名的轴：读数会照出，只是永远不会被任何
        # 规则看见 —— 而报告上它与「这一页没命中」逐字相同。
        axes = profile_doc["classification"]["axes"]
        axes["not_a_fact"] = axes.pop(next(iter(axes)))
    elif mutation == "classification_empty_axis":
        axes = profile_doc["classification"]["axes"]
        axes[next(iter(axes))] = []
    elif mutation == "classification_value_named_unknown":
        _first_classification_value(profile_doc)["value"] = "unknown"
    elif mutation == "classification_value_without_criteria":
        value = _first_classification_value(profile_doc)
        # 两个键都要清：只清一个的话，取值仍然有判据，装载层不该失败。
        value["phrases"] = []
        value.pop("host_suffixes", None)
    elif mutation == "classification_duplicate_value":
        axes = profile_doc["classification"]["axes"]
        entries = axes[next(iter(axes))]
        entries.append(dict(entries[0]))
    elif mutation == "classification_unknown_exclusion":
        _first_classification_value(profile_doc)["excludes"] = ["not_declared"]
    elif mutation == "locales_absent":
        # **不是坏声明，是没声明** —— 所以装载层必须放它过去（见
        # ``test_search_locales_may_be_absent``），失败发生在检索那一步。
        profile_doc["search"].pop("locales", None)
    elif mutation == "locales_not_a_list":
        # 一条标量而不是一张表。``_as_list`` 拦它 —— 收下的话这一格会变成
        # 「把字符串按字符拆开」，而 ``["e", "n", "-", "G", "B"]`` 里前两段是
        # 合法的语言标签。
        profile_doc["search"]["locales"] = "en-GB"
    elif mutation == "locales_empty":
        profile_doc["search"]["locales"] = []
    elif mutation == "locales_malformed_tag":
        # 下划线：BCP-47 的分隔符是连字符。写坏了不会报错，它只是永不生效 ——
        # 检索照发，按 provider 的缺省语言。
        profile_doc["search"]["locales"] = ["en_GB"]
    elif mutation == "locales_not_a_string":
        profile_doc["search"]["locales"] = [123]
    elif mutation == "locales_collapse_to_one_language":
        # 两条只差地区子标签的标签。作者想说的多半是「两个市场」，而语言这一格
        # 只到主语言子标签为止 —— 它们会合成一条查询。
        profile_doc["search"]["locales"] = ["en-GB", "en-US"]
    # 下面三条**装载得出来**，失败发生在检索那一步（``application/queries.py::
    # market_locale``）。它们与上面那一组的区别就是「哪一步该红」：市场那两格不是
    # 装载层的判据，而是「说出这次问的是哪个市场」这条纪律的判据。放进
    # ``_MUTATIONS`` 会把它们读成装载层的用例，而它们要证的是装载层**放它们过去**
    # 之后，第一次真检索会停下来。
    elif mutation == "country_empty":
        profile_doc["country"] = ""
    elif mutation == "country_not_iso":
        # 小写。写它的人多半想的是「大小写无所谓」—— 而这一格进幂等键与镜像文件名，
        # 所以 ``gb`` 与 ``GB`` 是两个值、两条账。
        profile_doc["country"] = "gb"
    elif mutation == "locales_two_languages":
        # 两种语言。**这是一份诚实的声明**（这个 profile 真的要问两种语言），
        # 而今天的检索一次只问得了一种 —— 所以它必须在检索那一步停下，
        # 而不是取第一条、也不是悄悄展开成两条计费调用。
        profile_doc["search"]["locales"] = ["en", "fr"]
    else:  # pragma: no cover - 参数表写错时立刻失败
        raise AssertionError(f"未知的 mutation：{mutation}")

    target = tmp_path / "profiles" / "probe"
    for name, document in documents.items():
        (target / name).write_text(
            yaml.safe_dump(document, allow_unicode=True, sort_keys=False), encoding="utf-8"
        )

    return Settings(
        data_root=tmp_path / "runtime", profiles_dir=tmp_path / "profiles", profile="probe"
    )


@pytest.mark.parametrize(("mutation", "match"), _MUTATIONS)
def test_loader_fails_closed(repo_root, tmp_path, mutation, match) -> None:
    """装载层遇到「读不懂」或「空含义」时必须停，不能装载出一份看似正常的规则。

    这些用例的共同点是：**改坏的那一处不会让输出变难看**。schema 串了版本、
    exclusions 为空、x_rules 为空、某条规则的 requires 是空列表 —— 每一条都还
    装载得出一份结构完好的规则，区别只在于它是错的。
    静默的错规则比显式的失败危险得多。
    """
    broken = _mutated_settings(tmp_path, repo_root, mutation)
    with pytest.raises(ConfigError, match=match):
        load_profile(broken)


def test_missing_profile_directory_raises(tmp_path) -> None:
    broken = Settings(
        data_root=tmp_path, profiles_dir=tmp_path / "nope", profile="does-not-exist"
    )
    with pytest.raises(ConfigError, match="profile 目录不存在"):
        load_profile(broken)


# --- 两条装载路线：同一批字节，同一条校验序列 --------------------------------
#
# 从盘上的目录装载与从一个被钉住的 Scene 版本装载今天合成一条尾巴
# （``profile_loader._bundle_from_documents``）。合成一条的理由是「两边各有一份校验
# 序列，漂移的那一次会让同一批字节装载出两个 bundle，而两边都『没坏』」——
# 下面两条判据量的就是那句话在两件具体的事上成立：
#
#   1. 报错**点名是哪一份文件**（``_parse_yaml`` / ``_require_schema`` 收 ``where``
#      的唯一理由），而两条路线上那个名字的形状不同：盘上是那个目录下的完整路径，
#      快照里是相对路径。
#   2. 同一批字节**装载出同一个 bundle** —— 不论它是从哪儿读来的。

_SCHEMA_LINE = re.compile(r"^schema:.*$", re.MULTILINE)


def _with_a_bogus_schema(text: str) -> str:
    """把 ``schema:`` 那一行的值换成一个 loader 不认识的版本。其余字节不动。

    替换的是**行**而不是做一次 YAML 往返：这一条要测的是「报错点名了哪一份文件」，
    而一次往返会把三份文件全都重写一遍 —— 那时红的理由可能与被测的行为无关。
    """
    replaced, count = _SCHEMA_LINE.subn("schema: marketops/not-this-version", text, count=1)
    assert count == 1, "这份声明里应当正好有一行 schema:"
    return replaced


@pytest.mark.parametrize("name", (PROFILE_FILE, ROLE_MATRIX_FILE, GRADING_FILE))
@pytest.mark.parametrize("source", ("directory", "scene"))
def test_a_broken_declaration_names_the_file_it_came_from(
    repo_root, tmp_path, name: str, source: str
) -> None:
    """装载失败时，报文必须点名**是哪一份声明** —— 而且只点那一份。

    这**不是**一条关于措辞的判据。装载层手里有三份文件、报的却是一句「schema 不支持」
    时，处置的人要自己把三份都打开看一遍；而更坏的一种是**点错了**名字：它会把一份
    完好的声明指成肇事者。

    两条路线都断，因为那个名字的**形状**不一样，而形状是这里唯一会静默退化的东西：
    从盘上装载时它是那个目录下的完整路径（处置的第一步就是去看那个文件），从一个
    Scene 快照装载时它只能是快照里的相对路径 —— 那时没有目录可说，而**编一个出来
    比一个名字更坏**：它会把读者指到一台可能根本没有这份 profile 的机器上。所以盘上
    那一条另外断「这个名字在 ``profiles/`` 下面」。

    内层那一条断言（「另外两份的名字一个都不许出现」）防的是「把三份都念一遍」——
    那种报文读起来像详尽，实际什么也没说。
    """
    target = _copy_profile(tmp_path, repo_root)
    broken = target / name
    broken.write_text(
        _with_a_bogus_schema(broken.read_text(encoding="utf-8")), encoding="utf-8"
    )
    settings = _probe_settings(tmp_path)

    with pytest.raises(ConfigError) as caught:
        if source == "directory":
            load_profile(settings)
        else:
            load_profile_from_scene_files(read_scene_files(target), settings=settings)

    message = str(caught.value)
    assert "只支持" in message, "这应当是一条 schema 版本的拒绝，不是别的失败"
    assert name in message, "报错必须点名是哪一份声明 —— 那正是它收一个 where 的理由"
    for other in (PROFILE_FILE, ROLE_MATRIX_FILE, GRADING_FILE):
        if other != name:
            assert other not in message, f"报文把 {other} 也念了一遍，那是三份都说了"
    if source == "directory":
        assert str(target) in message, (
            "从盘上装载时要点名的是**那个目录下的那个文件** —— 处置的第一步是去打开它"
        )
    else:
        assert str(target) not in message, (
            "从一批字节装载时没有目录可说：编一个路径出来会把读者指到一台可能根本"
            "没有这份 profile 的机器上"
        )


def test_both_loading_paths_build_the_same_bundle_from_the_same_bytes(
    repo_root, tmp_path
) -> None:
    """同一批字节，两条路装载出**同一个** bundle。

    「合成一条尾巴」这句话的可观测内容就是这一条：两份校验序列一旦漂移（某天只在
    一条路上补了一条对账判据），同一批字节会装载出两个 ``ProfileBundle``，而两边
    都「没坏」—— 报告上没有任何一处会红。判据是 ``==`` 而不是挑几个字段比：挑字段
    比的那一份清单本身就会漂移，而漂移掉的那一格正是没人看的那一格。
    """
    target = _copy_profile(tmp_path, repo_root)
    settings = _probe_settings(tmp_path)

    from_directory = load_profile(settings)
    from_scene = load_profile_from_scene_files(read_scene_files(target), settings=settings)

    assert from_scene.scene_files == from_directory.scene_files, (
        "两条路的 scene_files 必须是同一批字节（``read_scene_files`` 是唯一的读法）"
    )
    assert from_scene == from_directory


# --- scene_version：本部署生效的版本 ----------------------------------------
#
# §2 要 ``get_scene`` 报 "effective policy/guidance version"，§6 要「运行中的作业
# 保留它钉住的指引」（钉住是下一片）。两条都要求**有一个版本可以比**，而这些用例
# 只钉这一件事：版本由目录里的字节决定，别的什么都不影响它。


def _probe_settings(tmp_path: Path, profile: str = "probe") -> Settings:
    return Settings(
        data_root=tmp_path / "runtime", profiles_dir=tmp_path / "profiles", profile=profile
    )


def test_the_version_is_a_digest_the_same_bytes_always_give(repo_root, tmp_path) -> None:
    """同一份字节，两次装载给出同一个摘要。

    这条看着像废话，它是其余每一条的前提：版本只有在**由内容决定**时才可用。
    一个带时间戳或随机的版本会让「钉住的版本 ≠ 当前版本」每次都响 ——
    而每次都响的告警与每次都静的告警一样，没人再看它。
    """
    _copy_profile(tmp_path, repo_root)
    settings = _probe_settings(tmp_path)

    version = load_profile(settings).scene_version
    assert re.fullmatch(r"[0-9a-f]{64}", version), version
    assert load_profile(settings).scene_version == version


def test_a_guidance_file_enters_the_version(repo_root, tmp_path) -> None:
    """目录里加一个**新的**文件（不是那三个 YAML 之一）就要换版本。

    这是本片最重要的一条，因为它把「覆盖整个目录」与「覆盖那三个 YAML」分开。
    §6 要保留的指引正文今天还不存在（``docs/SCENE_EVOLUTION.md`` 的
    ``skill/SKILL.md``），但**机制**必须现在就对：版本若走的是一份写死的文件清单，
    第一个被加进去的指引文件就会**不参与版本** —— 于是「指引换了、版本没动」，
    而那正是这套机制唯一要防的失败。加进这个文件的人不会收到任何信号。

    递归也在这里被走到：新文件在一个**子目录**里。
    """
    directory = _copy_profile(tmp_path, repo_root)
    settings = _probe_settings(tmp_path)
    before = load_profile(settings).scene_version

    (directory / "skill").mkdir()
    (directory / "skill" / "SKILL.md").write_text("# 程序性指引\n", encoding="utf-8")

    assert load_profile(settings).scene_version != before


def test_one_byte_of_one_file_changes_the_version(repo_root, tmp_path) -> None:
    """改一个字节就换版本 —— 即使规则值逐位不变。

    ``# 一个字节`` 是一行 YAML 注释：读出来的规则完全一样，而版本会换。这是散
    「字节」而不是「读出来的值」的**已知代价**，方向是安全的那一侧（多一次假的
    「版本变了」，而不是少一次真的）。这条用例把这个取舍钉住，免得它日后被当成
    bug「修」成散规则值。
    """
    directory = _copy_profile(tmp_path, repo_root)
    settings = _probe_settings(tmp_path)
    before = load_profile(settings).scene_version

    target = directory / PROFILE_FILE
    target.write_text(
        target.read_text(encoding="utf-8") + "\n# 一个字节\n", encoding="utf-8"
    )

    assert load_profile(settings).scene_version != before


def test_the_version_does_not_follow_the_order_the_filesystem_lists(repo_root, tmp_path) -> None:
    """两份**文件集相同**的目录，版本必须相等 —— 无论目录以什么顺序列出它们。

    这条判据是**单向的**，写在这里是为了不把它说成它做不到的事：如果本机的文件系统
    对两个目录恰好按同一个顺序列出（ext4 的 htree、XFS 的短格式目录都会让新建的
    小目录与创建顺序有关或无关），那么一个**不排序**的实现也会通过。所以文件系统
    不合作时这条用例跳过，而不是假装验证过。

    两个目录**同名同内容**（各自在**自己的** profiles 根下叫 ``probe``，逐字节相同），
    只有创建顺序相反：``settings.profile`` 认的是目录名，而本片起这个目录名必须与
    ``profile.yaml`` 的 ``name`` 逐字相同（见 ``_copy_profile``）—— 「两份同内容、
    名字不同的部署」这个形状因此构造不出来，而它本来也不该存在。
    """
    first = _copy_profile(tmp_path / "first", repo_root)
    names = sorted(path.name for path in first.iterdir())
    second = tmp_path / "second" / "profiles" / "probe"
    second.mkdir(parents=True)
    for name in reversed(names):
        shutil.copyfile(first / name, second / name)

    if os.listdir(first) == os.listdir(second):
        pytest.skip("本机的目录列出顺序与创建顺序一致，这条判据没有输入")

    assert (
        load_profile(_probe_settings(tmp_path / "first")).scene_version
        == load_profile(_probe_settings(tmp_path / "second")).scene_version
    )


# --- 分层：业务词不得进通用 engine ------------------------------------------

BUSINESS_TOKENS: tuple[str, ...] = ("acme", "natural-grass", "member-a")

#: 唯一允许出现 profile id 的模块。它持有的是**默认指针**，不是业务规则。
POINTER_MODULE = "marketops/config.py"


def _code_string_literals(path: Path) -> list[tuple[int, str]]:
    """模块里**非文档字符串**的字符串字面量。

    早期版本直接对整份源码做子串搜索，于是连「业务规则只进 profiles/」这种
    **解释这条约束的 docstring** 也算违规 —— 结果是要么删掉解释，要么放宽规则，
    两个都不可接受。用 AST 区分「说明文字」与「代码里的字面量」才是准确的：
    真正危险的是后者（一个嵌进逻辑的业务词表），不是前者。
    """
    tree = ast.parse(path.read_text(encoding="utf-8"))

    docstrings: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            body = node.body
            if (
                body
                and isinstance(body[0], ast.Expr)
                and isinstance(body[0].value, ast.Constant)
                and isinstance(body[0].value.value, str)
            ):
                docstrings.add(id(body[0].value))

    return [
        (node.lineno, node.value)
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and id(node) not in docstrings
    ]


def test_business_rules_are_not_hardcoded_in_engine(repo_root) -> None:
    """业务规则不得以**代码字面量**的形式出现在通用 engine 里 —— 分层的核心约束。"""
    offenders: list[str] = []
    for path in sorted((repo_root / "marketops").rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        relative = path.relative_to(repo_root).as_posix()
        if relative == POINTER_MODULE:
            continue
        for lineno, value in _code_string_literals(path):
            lowered = value.lower()
            for token in BUSINESS_TOKENS:
                if token in lowered:
                    offenders.append(f"{relative}:{lineno} 含业务词 {token!r}：{value!r}")

    assert not offenders, (
        "业务词汇出现在通用 engine 的代码字面量里（应改由 profiles/ 提供）：\n  "
        + "\n  ".join(offenders)
    )


def test_profile_pointer_is_confined_to_config(repo_root) -> None:
    """默认 profile id 是**配置指针**，不是业务规则 —— 它只应出现在 config.py。

    与上一条互为补充：上一条把 config.py 排除掉了，这一条保证那个排除项
    没有被滥用成「随便哪里写死 profile 都行」的后门。
    """
    hits: list[str] = []
    for path in sorted((repo_root / "marketops").rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        relative = path.relative_to(repo_root).as_posix()
        for lineno, value in _code_string_literals(path):
            if "uk-acme" in value.lower():
                hits.append(f"{relative}:{lineno}")

    assert hits, "找不到默认 profile 指针 —— 测试可能已失效，请检查扫描范围"
    assert all(hit.startswith(f"{POINTER_MODULE}:") for hit in hits), (
        f"默认 profile id 只应出现在 {POINTER_MODULE}，实际出现在：{hits}"
    )


# --- 场景块：同一个工作区里不同场景先看什么 -------------------------------------
#
# 条件 2 要的是：**同一个工作区**里，学校 / 高尔夫 / 维护商这些不同的 context
# 拿到不同的 **source / page / role** 策略，而共享的事实与工件照旧复用。在这份
# `scenario-matrix.yaml` 之前，这三根轴只有一套声明 —— 于是「不同场景看同一份
# 策略」在代码里读不出差别：不是策略相同，是**没有分块的入口**。这一节锁的就是
# 那个入口，以及「块里的名字必须来自这份声明」这条纪律。
#
# 块键一律用市场中性的读法（与 `tests/test_research_strategy.py::_context` 同例）。
# 块键是**读法**，不是资产事实 —— 那一条本身有判据（见本节末尾）。


def _narrowable(bundle: ProfileBundle) -> dict[str, list[str]]:
    """声明里那四格可以收窄的名录，**从声明读**。

    档位名、口径名、类的次序、方法 id 都是 profile 侧的内容。把 ``high`` 或
    ``grounds`` 写死在测试里，这一节的判据就会依赖**另一份**声明：那些名字改掉的
    那天，失败会是一堆「找不到那一项」的 ``KeyError``，而不是一条关于装载层的
    判据 —— 而这一节要证的恰恰是「块里的名字必须来自声明」。
    """
    return {
        "page_tiers": list(bundle.profile.page_discovery_hints),
        "business_functions": list(bundle.role_matrix.business_functions),
        "class_order": [str(item) for item in bundle.role_matrix.priority["class_order"]],
        "sources": list(SOURCE_METHODS),
    }


def test_a_group_without_a_business_function_is_dropped_and_counted(repo_root, tmp_path) -> None:
    """业务口径的名录 = 各组声明的 ``business_function``，保序去重，**``unknown`` 不算一种口径**。

    这是 ``DeclaredRoleMatrix.business_functions`` / ``undecided_groups`` 那两句的性质。
    它们今天已经有一份消费者（``_check_scenario_blocks`` 拿它当 ``sources`` 那一格的
    左边），所以它必须有自己的执行器：一个没有判据的「哪些名字算数」会在将来第二处
    实现里漂移，而漂移的那一处不会报错 —— 它只会算出一份少一个口径的名录，
    计划上看起来完全正常。

    期望值从**被改写后的那份文档**逐组算出来，不是从被测属性抄一遍：把口径写死成
    一个字面量会让这条判据在声明改名的那天变成一堆「找不到那一项」。
    """
    target = _copy_profile(tmp_path, repo_root)
    role_path = target / ROLE_MATRIX_FILE
    role_doc = yaml.safe_load(role_path.read_text(encoding="utf-8"))
    groups = role_doc["role_groups"]
    # 没写口径的组按 ``unknown`` 读（装载层就是这么读的：``item.get("business_function",
    # BusinessFunction.UNKNOWN.value)``）—— 期望值因此不能直接索引那一格。
    written = [group.get("business_function", BusinessFunction.UNKNOWN.value) for group in groups]
    groups[0]["business_function"] = BusinessFunction.UNKNOWN.value
    role_path.write_text(
        yaml.safe_dump(role_doc, allow_unicode=True, sort_keys=False), encoding="utf-8"
    )

    loaded = load_profile(
        Settings(data_root=tmp_path / "runtime", profiles_dir=tmp_path / "profiles", profile="probe")
    )
    expected = tuple(
        dict.fromkeys(
            function for function in written[1:] if function != BusinessFunction.UNKNOWN.value
        )
    )
    assert loaded.role_matrix.business_functions == expected
    assert BusinessFunction.UNKNOWN.value not in loaded.role_matrix.business_functions
    assert loaded.role_matrix.undecided_groups == 1

    # 反控：真实 profile 一组都没缺，所以那两句话在它上面都不该有内容。
    real = load_profile(
        Settings(
            data_root=tmp_path / "runtime-real",
            profiles_dir=repo_root / "profiles",
            profile=REAL_PROFILE_ID,
        )
    )
    assert real.role_matrix.undecided_groups == 0
    assert real.role_matrix.business_functions
    assert BusinessFunction.UNKNOWN.value not in real.role_matrix.business_functions


def _block(material: dict[str, list[str]], **overrides: list[str]) -> dict[str, list[str]]:
    """一个合法的块：四格默认照声明**原样写全**（最宽的收窄），要收窄哪一格传哪一格。"""
    block = {name: list(values) for name, values in material.items()}
    block.update(overrides)
    return block


def _matrix(blocks: dict[str, dict[str, list[str]]]) -> dict:
    return {"schema": SOP_SCENARIO_MATRIX_SCHEMA, "profile": "probe", "blocks": blocks}


def _scenario_settings(
    tmp_path: Path, repo_root: Path, document: dict, *, name: str = "probe"
) -> Settings:
    """复制真实 profile，再往里放一份 ``scenario-matrix.yaml``，返回指向它的 Settings。

    走真实装载器、真的读盘（照 ``_copy_profile`` 的用法）：这一节的每一条判据都是
    「装载期该不该停下」，而绕过装载器去调内部函数恰好会把那个问题换成一个别的。
    """
    target = _copy_profile(tmp_path, repo_root, name)
    (target / SCENARIO_MATRIX_FILE).write_text(
        yaml.safe_dump(document, allow_unicode=True, sort_keys=False), encoding="utf-8"
    )
    return Settings(
        data_root=tmp_path / "runtime", profiles_dir=tmp_path / "profiles", profile=name
    )


def test_the_real_profile_declares_no_scenario_blocks(bundle) -> None:
    """真实 profile 今天没有这份文件 —— 而缺的是那个**入口**，不是可收窄的材料。

    这不是「还没做」的托词，是一条读数：SOP 对这一族工作说了「责任实体是谁」
    （L27）与「多类批量来源」（L80：全国/区域地图目录、协会/联赛资源、市政 GIS、
    学校/大学场地目录、俱乐部与公园网络、公开注册或 API），**没有任何按形态分支的
    page / role 策略**。分块的内容是市场侧的判断，只有 SOP 所有者能认；替它编一份
    出来，就是让计划上出现一条**没人认过**的规则。

    所以本片交付的是入口与那四格的纪律，`scenario_matrix is None` 是今天正确的读数，
    矩阵那一行按 ``PARTIAL`` 如实记。这条判据在真实 profile 真的拿到一份分场景声明
    的那天转红 —— 那时该改的是它，以及 `docs/B16_ACCEPTANCE_MATRIX_2026-09-28.md`
    条件 2 那一行，而不是把它删掉。
    """
    assert bundle.scenario_matrix is None
    # 三格都有内容可收窄 —— 缺的不是材料
    assert tuple(bundle.profile.page_discovery_hints)
    assert bundle.role_matrix.business_functions
    assert bundle.role_matrix.priority["class_order"]


def test_a_scenario_block_narrows_the_declaration(bundle, repo_root, tmp_path) -> None:
    """一份分场景声明装得进来，四格按块读回来，且**保书写顺序**。"""
    material = _narrowable(bundle)
    document = _matrix(
        {
            "k12": _block(
                material,
                page_tiers=material["page_tiers"][:1],
                business_functions=material["business_functions"][:2],
                class_order=material["class_order"][:2],
                sources=material["sources"][:2],
            ),
            "golf_course": _block(material),
        }
    )
    loaded = load_profile(_scenario_settings(tmp_path, repo_root, document))

    assert loaded.scenario_matrix is not None
    matrix = loaded.scenario_matrix
    assert matrix.schema == SOP_SCENARIO_MATRIX_SCHEMA
    assert matrix.profile == "probe"
    # 书写顺序，**不是字典序**：按字典序读会给出 ("golf_course", "k12")。
    # 这一条重要是因为 ``scenarios`` 是这份 profile 的场景词表 —— 一份自己排过序的
    # 词表会让「作者写下来的顺序」与「计划里读到的顺序」不一致，而两者不一致时
    # 没有一处会红。
    assert matrix.scenarios == ("k12", "golf_course")

    narrowed = matrix.blocks["k12"]
    assert narrowed.page_tiers == tuple(material["page_tiers"][:1])
    assert narrowed.business_functions == tuple(material["business_functions"][:2])
    assert narrowed.class_order == tuple(material["class_order"][:2])
    assert narrowed.sources == tuple(material["sources"][:2])

    # 收窄到「一条不删」也是收窄：它说的是「这一格照声明走」，只是写在了块里。
    full = matrix.blocks["golf_course"]
    assert full.page_tiers == tuple(material["page_tiers"])
    assert full.class_order == tuple(material["class_order"])


def test_a_scenario_reading_need_not_be_an_asset_fact(bundle, repo_root, tmp_path) -> None:
    """块键是**读法**，不是资产事实 —— 它不必是分类轴词表里的取值。

    两件事问的不同：``classification`` 说「这一页 / 这块地是什么」，块键说「这次研究
    冲着哪一个场景去」。压成一个词表，会让「这次要找什么」与「这页是什么」在计划上
    同形。这一份的键就是 ``ScenarioCandidate.reading`` 那句「它落到**这个 profile 的
    词表上**是哪一个值」一直缺的承载者 —— 词表是声明写下来的，不是分类词表派生的。

    **它同时是一条判据**：哪天有 profile 真的把一个这样的读法写进分类轴，这条会红，
    而那时该做的是给这个测试换一个键，不是让两个词表悄悄合并。
    """
    facts = {
        value
        for axis in bundle.profile.classification.axes
        for value in axis.value_names
    }
    reading = "k12"
    assert reading not in facts, "这个读法已经成了分类取值，请换一个键再读这条判据"

    loaded = load_profile(_scenario_settings(tmp_path, repo_root, _matrix({reading: _block(_narrowable(bundle))})))
    assert loaded.scenario_matrix.scenarios == (reading,)


def test_a_source_method_with_no_open_route_may_still_be_named(bundle, repo_root, tmp_path) -> None:
    """``sources`` 那一格的名录是**注册表**，不是「今天开着的那几条路」。

    装载期判的是「这个 id 是本仓认得的方法吗」（``domain/sources.py`` 的注册表），
    不判「这条路今天开着吗」（``application/research_strategy.py::_SOURCE_ROUTES``
    只开三条）。两份名录答的是两个问题，而这一格说的是「这个场景**留**哪几条」——
    一份声明还没给某个方法入口，不是「这一块写错了」。

    这条路走不通的那一半在**编译**期：块与已开的路取交集，一个都没留下时停。
    两个判据各判各的，所以这里只钉装载期这一半。
    """
    material = _narrowable(bundle)
    unused = "import.register.v1"
    assert unused in material["sources"]
    document = _matrix({"k12": _block(material, sources=[unused])})
    loaded = load_profile(_scenario_settings(tmp_path, repo_root, document))
    assert loaded.scenario_matrix.blocks["k12"].sources == (unused,)


#: 装载期必须停下的每一种坏法。(名字, 那句话里的判据)
_SCENARIO_MUTATIONS: tuple[tuple[str, str], ...] = (
    ("schema", "只支持"),
    ("blocks_absent", "缺少 blocks 映射"),
    ("blocks_not_a_mapping", "缺少 blocks 映射"),
    ("blank_scenario", "空键"),
    ("block_not_a_mapping", "必须是映射"),
    ("unknown_field", "不认识的键"),
    ("missing_field", "少了"),
    ("empty_cell", "是空的"),
    ("blank_entry", "空串"),
    ("duplicate_entry", "不止一次"),
    ("page_tier_not_declared", "声明里没有的名字"),
    ("business_function_not_declared", "声明里没有的名字"),
    ("class_order_reordered", "改了声明的次序"),
    ("source_not_registered", "声明里没有的名字"),
)


def _mutated_matrix(material: dict[str, list[str]], mutation: str) -> dict:
    """把一份合法声明改坏一处。坏法按「会静默走样的那一格」逐条枚举。"""
    block = _block(material)
    document = _matrix({"k12": block})

    if mutation == "schema":
        document["schema"] = "marketops/scenario-matrix.v0"
    elif mutation == "blocks_absent":
        document.pop("blocks")
    elif mutation == "blocks_not_a_mapping":
        document["blocks"] = ["不是映射"]
    elif mutation == "blank_scenario":
        document["blocks"] = {"": block}
    elif mutation == "block_not_a_mapping":
        document["blocks"] = {"k12": "不是映射"}
    elif mutation == "unknown_field":
        # 拼错的格名。收下它，这一格会静默地按整份声明走 —— 块看起来是写过的。
        block["page_tier"] = material["page_tiers"][:1]
    elif mutation == "missing_field":
        block.pop("sources")
    elif mutation == "empty_cell":
        block["page_tiers"] = []
    elif mutation == "blank_entry":
        block["page_tiers"] = [""]
    elif mutation == "duplicate_entry":
        block["page_tiers"] = [material["page_tiers"][0]] * 2
    elif mutation == "page_tier_not_declared":
        block["page_tiers"] = ["not_a_declared_tier"]
    elif mutation == "business_function_not_declared":
        block["business_functions"] = ["not_a_declared_function"]
    elif mutation == "class_order_reordered":
        # 块改了声明的先后：收窄可以，改次序不行 —— 先后是声明的口径。
        block["class_order"] = list(reversed(material["class_order"]))
    elif mutation == "source_not_registered":
        block["sources"] = ["search.engine.v2"]
    else:  # pragma: no cover - 参数表写错时立刻失败
        raise AssertionError(f"未知的 mutation：{mutation}")
    return document


@pytest.mark.parametrize(("mutation", "match"), _SCENARIO_MUTATIONS)
def test_scenario_matrix_fails_closed(bundle, repo_root, tmp_path, mutation, match) -> None:
    """场景块的坏法必须**在装载期**停下，不能等到那个场景真的来临。

    这些坏法的共同点是：**它们都不会让计划变难看**。拼错的档位名、空着的一格、
    写坏了一次的类次序 —— 每一条都还产出结构完好的计划，区别只在于它是错的。
    而判在装载期而不是编译期，理由与 ``profile.exclusions`` 的 ``declared_ids``
    逐字相同：一个拼错的名字在编译期只在**那个场景真的来临时**才红，而一份声明里
    最不容易被读到的恰恰是那几个还没被用过的块。
    """
    document = _mutated_matrix(_narrowable(bundle), mutation)
    with pytest.raises(ConfigError, match=match):
        load_profile(_scenario_settings(tmp_path, repo_root, document))


def test_the_block_field_whitelist_is_the_blocks_type() -> None:
    """闭集 ``_SCENARIO_BLOCK_FIELDS`` 必须逐字等于那个类型的字段名。

    两者分叉时，多出来的那个键会被 ``_scenario_block`` 判成「不认识的键」——
    而它明明是这个类型的一个字段：作者照类型写，装载层说他不认识。少一个键则相反：
    那一格永远不会被读到，而块看起来是写过的。
    """
    assert set(_SCENARIO_BLOCK_FIELDS) == {field.name for field in fields(DeclaredScenarioBlock)}
    assert len(_SCENARIO_BLOCK_FIELDS) == len(set(_SCENARIO_BLOCK_FIELDS))
