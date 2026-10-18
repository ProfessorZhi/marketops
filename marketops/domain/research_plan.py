"""B16 的编译面：把一份讲清楚了的上下文，连同这份部署已批准的范围，编成一份
**能解释的**研究计划。

执行计划 §B16 给 ``EffectiveResearchPlan`` 列了八组内容（来源方法及其顺序、语言/
查询族、页面原型与链接优先级、角色/职能词表与优先级提示、获取方式与降级、
DecisionSpec 引用、预算/停止/充分性、以及**确切的 policy/module 版本 + digest**）。
这个模块装的是那一份计划，以及编出它所需的那些类型。

## 为什么「可解释」要落成结构，而不是一段写清楚的文档

计划 §B16 对策略模块有一句硬要求：编译优先级、来源、冲突与版本**必须由普通代码确定
并可解释**。把它读成「文档里讲明白」是不够的 —— 文档不会在有人接上一个越权的模块时
响。所以这里把三件事做成结构：

* 计划上的**每一格**都在 :class:`PlanField` 里有个名字（闭集），于是「这一格谁写的」
  是一个问得出来的问题，而不是一句要人去读代码的话；
* 每一格有**一个**归属模块（:data:`FIELD_OWNER`），少数几格另有一串**写明的**覆盖者
  （:data:`FIELD_OVERRIDERS`）—— 覆盖不是隐式的「后写的赢」，而是一次要留痕的决定；
* 每一格带着它的出处进 :class:`PolicyProvenance`，于是
  :meth:`EffectiveResearchPlan.provenance_for` 答得出「这条顺序是谁定的、按哪一版」。

## 两个方向相反的失败，各自有落点

计划 §B16 的动态识别规则里有两句方向相反的话，两边都要有落点：

* **「多个候选且差异只影响低风险搜索顺序：可在批准 envelope 内编译保守计划并记录
  候选」** —— 软歧义（:mod:`marketops.domain.research_context` 的软那一半）不拦编译，
  编出来的计划照常有一个 digest。
* **「Agent/Jev 可做闭集候选分类或提出候选，不可自行改硬排除、预算、产品 claim、
  访问权限或发送权限」** —— 这是硬的一侧：那九格（:data:`HARD_SCOPE_FIELDS`）只有
  声明层写得出来，一个策略模块碰它就是一次**拒绝**，而不是一次覆盖。

拒绝而不是静默取声明值，理由是这两种处置在现场长得不一样：静默取声明值的话，
一个「以为自己在扩大目标人口」的模块会安静地什么都没做，而回执上一切正常 ——
那正是 §B16 那句「历史模型猜测不能自动扩大范围」要防的形状。

## 这一层不做什么

不调用任何东西（计划 §B16 关闭条件 1 的「preview 零 provider 调用」在这里是**结构上**
成立的，不是纪律上成立的），不写盘，不判「哪一份计划更好」，不认识任何一个市场词
（``tests/test_no_hardcoded_market_knowledge.py`` 对 ``marketops/`` 做 AST 扫描）。
它只做一件事：把已经写在声明里、或已经由模块算出来的东西按一张写明的表拼起来，
并留下每一格的出处。

## 一个写下来的缺口

``ModuleRevision.revision`` 是**模块自己算出来的**内容摘要（读进来的那些声明），
不是一个手写的版本号 —— 理由见 :class:`ModuleRevision`。它保证的是「同一份声明
两次编译出同一份计划」。它**不**保证「模块的代码变了这一次就看得出来」：一个只改了
实现、没改读进来的声明的模块，revision 不动。这一条今天不是缺口（编译器要求模块是
纯函数，所以实现改了而**输出**没改就不算一次策略变更，输出改了则带着值一起进摘要），
但它是这一层依赖「纯」这个前提的地方，写在这里免得下一个人以为版号是自己盯着的。
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Protocol

from pydantic import field_validator, model_validator

from .base import StrictModel
from .errors import ConfigError
from .profile import ProfileBundle
from .research_context import (
    Ambiguity,
    ResearchContext,
    ResearchEnvelope,
    ambiguities_of,
    constraints_out_of_envelope,
    effective_jurisdiction,
    fingerprint_of,
)
from .util import SEP, sha256_text


class StrategyModuleId(str, Enum):
    """策略模块的**闭集** —— 六个，逐字取自计划 §B16 的 Strategy modules 那一行。

    ``ACCESS`` 的取值是 ``access_strategy`` 而不是 ``access_policy``，这是一个
    刻意的选择：``AccessPolicy`` 这个名字在本仓已经被占用
    （:class:`marketops.domain.access_governance.AccessPolicy`，重试/退避/熔断的
    节流策略），而 §B16 那个同名模块是另一件东西。两个不同的东西共用一个名字，
    读的人迟早会把「抓取那一层的退避参数」读成「这次研究被批准了哪些访问方式」——
    而这两件事的限制方向相反（前者是节流，后者是许可）。所以这里换名，
    并把它**持有**现有的 ``AccessPolicy`` 这件事写在该模块自己的 docstring 上。
    """

    GEO_LOCALE_SOURCE = "geo_locale_source_profile"
    SCENARIO = "scenario_profile"
    OBJECTIVE = "objective_policy"
    ACCESS = "access_strategy"
    #: 可选：``context.product_use`` 为空时它不参与编译。
    PRODUCT_OVERLAY = "product_overlay"
    #: 可选：今天没有 Workstream 声明，所以它也不参与编译 —— 见 :data:`MODULE_PRIORITY`。
    WORKSTREAM_OVERRIDE = "workstream_override"


#: 声明层在溯源里的名字。**它不是策略模块**：没有覆盖权、也编不出候选，
#: 它只把 envelope 与声明里已经写着的那一份照抄进计划，好让每一格都有一个出处 ——
#: 一份「有几格的出处是空白」的计划，读起来与「那几格没人管」一模一样。
DECLARED_SCOPE = "declared_scope"


#: 覆盖顺序：**越靠后越具体**。
#:
#: 这不是执行顺序（六个模块互不依赖），是「哪几格可以被谁改写」的先后。写成元组
#: 而不是集合是因为先后就是它的语义：``Workstream override`` 之所以叫 override，
#: 就是因为它比 Scenario 与 Product 更贴近这一次研究 —— 一个长期 Workstream 的
#: 声明比一次产品用途的透镜更具体，而两者都比场景层更具体。
#:
#: 两个可选模块（Product overlay / Workstream override）今天**没有声明来源**：
#: profile 里没有 product overlay 块，全仓也没有 Workstream 这个概念
#: （``docs/TARGET_ARCHITECTURE_GAP_MATRIX.md`` §2 记着它是零命中）。它们在这张表里
#: 有位置，是因为**覆盖权是一张表上的位置**，而不是一个「等有了再想」的问题 ——
#: 等到有来源的那一天再来决定谁赢，赢家就会由那次实现顺手定下来。
MODULE_PRIORITY: tuple[StrategyModuleId, ...] = (
    StrategyModuleId.GEO_LOCALE_SOURCE,
    StrategyModuleId.SCENARIO,
    StrategyModuleId.OBJECTIVE,
    StrategyModuleId.ACCESS,
    StrategyModuleId.PRODUCT_OVERLAY,
    StrategyModuleId.WORKSTREAM_OVERRIDE,
)


class PlanField(str, Enum):
    """计划上的每一格 —— **闭集**。

    分两组，而分组本身就是最要紧的那条规则：**上面九格只有声明层写得出来**
    （:data:`HARD_SCOPE_FIELDS`），下面八格才是策略模块的活。分组不是「重要性」
    排序，是「谁能写」——一个模块写了上面那组里的任何一格，编译就停下来。
    """

    # --- 声明层独有：客户定义、硬排除、预算、产品与权限 ---------------------
    #: 问哪一个市场。来自 ``ResearchEnvelope``（``profile.yaml`` 的 ``country``）。
    GEO = "geo"
    JURISDICTION = "jurisdiction"
    #: 逐条 id（``profile.yaml`` 的 ``exclusions``）—— **判定输入，不是关键词表**。
    EXCLUSIONS = "exclusions"
    #: 这次研究挂的产品用途（``context.product_use`` 原样）。空着 = 不挂。
    PRODUCT_USE = "product_use"
    #: 产品的**声明**身份（``profile.yaml`` 的 ``product`` 块）。它不许被改写，
    #: 所以「这次研究挂了产品」这件事只能由声明层写下来。
    PRODUCT_CLAIMS = "product_claims"
    #: ``profile.yaml`` 的 ``compliance`` 块**原样**渲染（``key=value``，排序）。
    #: 这一层不解读哪几条是「许可」、哪几条是「义务」—— 那属于抓取层。
    ACCESS_PERMISSIONS = "access_permissions"
    #: 今天恒为空，理由不是「还没做」而是 §7.3：本产品不发邮件。这一格存在的
    #: 理由是**拒绝**——一个想给自己添发送权限的模块在这里被拦下。
    SEND_PERMISSIONS = "send_permissions"
    SEARCH_BUDGET = "search_budget"
    FETCH_BUDGET = "fetch_budget"

    # --- 策略模块可写 -------------------------------------------------------
    LOCALES = "locales"
    QUERY_FAMILIES = "query_families"
    SOURCE_METHODS = "source_methods"
    ACQUISITION_MODES = "acquisition_modes"
    PAGE_ARCHETYPES = "page_archetypes"
    ROLE_FUNCTIONS = "role_functions"
    ROLE_PRIORITIES = "role_priorities"
    DECISION_REFS = "decision_refs"


#: 只有声明层写得出来的那九格，逐字对应计划 §B16 那句「不可自行改硬排除、预算、
#: 产品 claim、访问权限或发送权限」—— 外加客户定义本身（``GEO`` / ``JURISDICTION`` /
#: ``PRODUCT_USE``）：那句禁令的主语是 ``Agent/Jev``，而一个能自己选定市场的系统
#: 根本不需要再去改硬排除。
HARD_SCOPE_FIELDS: frozenset[PlanField] = frozenset(
    {
        PlanField.GEO,
        PlanField.JURISDICTION,
        PlanField.EXCLUSIONS,
        PlanField.PRODUCT_USE,
        PlanField.PRODUCT_CLAIMS,
        PlanField.ACCESS_PERMISSIONS,
        PlanField.SEND_PERMISSIONS,
        PlanField.SEARCH_BUDGET,
        PlanField.FETCH_BUDGET,
    }
)

#: 模块可写的那八格。与 :data:`HARD_SCOPE_FIELDS` 的并集恰是 :class:`PlanField` 的
#: **全部**成员，且两者不相交 —— 由 ``tests/test_research_plan.py`` 执行。
#:
#: 写成「减去硬的那九格」而不是再列一遍：漏掉一格时，漏掉的那一格会落进哪一组是
#: **算出来的**，而一个手写两遍的表在漏掉一格时不会响 —— 那一格从此谁也写不了，
#: 而计划上它是一个空值，看起来像「这一格不重要」。
MODULE_WRITABLE_FIELDS: frozenset[PlanField] = frozenset(PlanField) - HARD_SCOPE_FIELDS


#: 每一格的归属模块。八格各有一个写者 —— 没有「共同所有」这种格子，因为两处都能写
#: 同一格时，「这一格是谁定的」就不再是一句话能答的问题，而它正是这份计划要答的。
FIELD_OWNER: dict[PlanField, StrategyModuleId] = {
    PlanField.LOCALES: StrategyModuleId.GEO_LOCALE_SOURCE,
    PlanField.QUERY_FAMILIES: StrategyModuleId.GEO_LOCALE_SOURCE,
    PlanField.SOURCE_METHODS: StrategyModuleId.GEO_LOCALE_SOURCE,
    PlanField.ACQUISITION_MODES: StrategyModuleId.ACCESS,
    PlanField.PAGE_ARCHETYPES: StrategyModuleId.SCENARIO,
    PlanField.ROLE_FUNCTIONS: StrategyModuleId.SCENARIO,
    PlanField.ROLE_PRIORITIES: StrategyModuleId.SCENARIO,
    PlanField.DECISION_REFS: StrategyModuleId.OBJECTIVE,
}


#: 哪几格可以被更具体的模块改写，以及改写者的先后（越靠后越赢）。
#:
#: 只有两格在表里，这不是遗漏。其余六格（语言、查询族、来源方法、获取方式、
#: 角色词表、决策题面）今天**没有第二个合法写者**：一个场景层想改语言族，
#: 那要么是它越权，要么是这一层缺了一个「语言也随场景变」的规则 —— 两种情形都该在
#: 编译时响一声，而不是让它悄悄盖掉。等到真的有模块需要改写某一格，
#: 往这张表里加一行是一次要写理由的决定，那正是它该有的成本。
FIELD_OVERRIDERS: dict[PlanField, tuple[StrategyModuleId, ...]] = {
    PlanField.PAGE_ARCHETYPES: (
        StrategyModuleId.PRODUCT_OVERLAY,
        StrategyModuleId.WORKSTREAM_OVERRIDE,
    ),
    PlanField.ROLE_PRIORITIES: (
        StrategyModuleId.PRODUCT_OVERLAY,
        StrategyModuleId.WORKSTREAM_OVERRIDE,
    ),
}


class PageArchetype(StrictModel):
    """一档页面原型 —— 「先抓哪一类页面」。

    ``tier`` 是 profile 里 ``page_discovery_hints`` 的**键本身**，而键是一个**开放集**：
    profile 声明了 ``high`` / ``medium`` / ``low`` 三档，但装载器收任何字符串当键
    （``adapters/profile_loader.py``），另有消费者按声明的键集建闭集
    （``application/decision_specs.py`` 的 PAGE_TRIAGE 那一支）。所以这里**不定义**
    档位枚举：一个由本层定义的三档枚举，会在有人声明第四档时让那一档无处可去 ——
    而它在页面前沿那一侧今天是有位置的（``sitemap_frontier.py`` 按声明顺序遍历）。

    ``patterns`` 是这一档的 URL glob，**按声明顺序**。顺序是语义：它同时是前台
    （先抓哪一页）与报告（顺序是报告的一部分）的依据，见 ``application/site_crawl.py``
    的模块 docstring 那句「不跟随链接」。
    """

    tier: str
    patterns: tuple[str, ...]

    @field_validator("tier")
    @classmethod
    def _tier_is_not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError(
                "页面原型缺一个档位名。空档位名会让「声明了一档没写名字」与"
                "「这一档叫空串」在报告上同形，而两者在页面前沿那一侧要做的事不同。"
            )
        return value

    @field_validator("patterns")
    @classmethod
    def _patterns_are_not_empty(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if not value:
            raise ValueError(
                "页面原型必须至少有一条 glob。一条都没有的档位是**声明的空档**，"
                "它在计划里占了一个位置却什么都筛不出来 —— 而读计划的人会以为"
                "「这一档今天没有页面命中」，那是另一件事。"
            )
        return value


class PolicyContribution(StrictModel):
    """一个策略模块对计划上**一格**的贡献 —— 值、出处、以及为什么。

    ``values`` 与 ``archetypes`` **恰好一个非空**（与 ``ReviewIntake`` 的
    ``options``/``recommendation``/``producer``/``no_producer`` 同一个形状）。
    两个都空是「贡献了一格空气」，两个都填是「一格有两个值」—— 前者会让
    :data:`FIELD_OWNER` 那张表看不出这一格是不是真的被写了，后者要求读的人自己
    决定谁赢，而那份决定正该由 :data:`FIELD_OVERRIDERS` 写在表上。

    ``reason`` 必填且非空：这一层唯一的产品就是「可解释」，而一个说不出为什么的
    贡献在这里没有位置 —— 它可以在下面几层里存在（一个模块的内部实现细节），
    但不可以出现在计划的溯源里。
    """

    module_id: StrategyModuleId
    #: 这个模块**这一版**的内容摘要，见 :class:`ModuleRevision`。
    revision: str
    field: PlanField
    #: 取值型的一格（语言族、来源方法、角色词表……）。
    values: tuple[str, ...] = ()
    #: 结构型的那一格（页面原型有档位）。
    archetypes: tuple[PageArchetype, ...] = ()
    reason: str

    @field_validator("revision", "reason")
    @classmethod
    def _not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError(
                "策略贡献的出处/理由不能为空。空的出处会让这一格在溯源里长得像"
                "「声明层写的」，而空理由会让「为什么是这个顺序」重新变成一个"
                "只能去读代码的问题。"
            )
        return value

    @model_validator(mode="after")
    def _exactly_one_shape(self) -> PolicyContribution:
        filled = bool(self.values) + bool(self.archetypes)
        if filled != 1:
            raise ValueError(
                f"{self.field.value} 这一格的贡献必须恰好有一种取值形状"
                f"（values 或 archetypes 之一），现在是 {filled} 种。\n"
                "两个都空等于贡献了一格空气；两个都填等于一格有两个值 ——"
                "而谁赢这件事写在 FIELD_OVERRIDERS 上，不写在贡献里。"
            )
        if self.field in HARD_SCOPE_FIELDS:
            raise ValueError(
                f"{self.field.value} 属于**声明层独有**的那九格，策略模块"
                f"（这里是 {self.module_id.value}）写不了它。\n"
                "执行计划 §B16 逐字：Agent/Jev 可做闭集候选分类或提出候选，"
                "不可自行改硬排除、预算、产品 claim、访问权限或发送权限。\n"
                "这一条在这里是构造期判据，不只在编译器里 —— 一个越权的贡献"
                "连**造都造不出来**，于是没有任何一条路径能把它带进计划。"
            )
        return self


class ModuleRevision(StrictModel):
    """一个参与编译的模块，以及它**这一版**是什么。

    ``revision`` 是模块从**它读进来的那些声明**算出来的内容摘要，不是一个手写的
    版本号。理由是 ``domain/maintenance.py::RefreshPlan.policy_version`` 已经写过
    同一条：要回答的问题是「两次编译读到的是不是同一份策略」，而不是「这是第几版」。
    手写的号在这一问上两件事都答错 —— 有人改了声明却忘了号（看起来没变），
    两条不同的声明在两次部署里拿到同一个号（看起来一样）。

    **它不等于「已批准」。** 标定与否在声明自己那边（``freshness.calibrated``
    那一族），两者分开：一个说「是这几条规则」，一个说「有人认过这几条规则」。
    """

    module_id: StrategyModuleId
    revision: str

    @field_validator("revision")
    @classmethod
    def _revision_is_not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError(
                "模块版本不能为空。空版本与任何真版本都不相等，于是「计划 A 与计划 B "
                "是不是同一份」这个问题会因为没人填而恒答「不是」—— 而恒响的告警与"
                "恒不响的告警是同一类东西：没人再看它。"
            )
        return value


class PolicyProvenance(StrictModel):
    """计划上**一格**的出处：谁写的、按哪一版、为什么。

    每一格恰好一条（``tests/test_research_plan.py`` 执行）。声明层写的那几格
    ``module_id`` 是 :data:`DECLARED_SCOPE` —— 一个占位的名字，不是模块。
    """

    field: PlanField
    module_id: str
    revision: str
    reason: str

    @field_validator("module_id")
    @classmethod
    def _module_is_a_name_we_know(cls, value: str) -> str:
        known = {member.value for member in StrategyModuleId} | {DECLARED_SCOPE}
        if value not in known:
            raise ValueError(
                f"溯源里的 module_id {value!r} 不在闭集里。"
                f"闭集是 {sorted(known)}。\n"
                "一个自由字符串的 module_id 会让「这一格是谁写的」在报告上看起来"
                "总是有答案 —— 而答案是拼错的模块名，或者一个早就删掉的模块。"
            )
        return value

    @field_validator("revision", "reason")
    @classmethod
    def _not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("溯源里的版本与理由都不能为空（见 PolicyContribution）。")
        return value


class PolicyOverride(StrictModel):
    """一次**留痕的覆盖** —— 谁盖掉了谁，两边各是哪一版。

    被覆盖者的值与版本都记下来，而不是只记赢家。理由是「覆盖」这件事的现场问题
    是「原来那条规则去哪了」：只记赢家的话，一个模块停止参与编译（例如
    ``context.product_use`` 被清空）之后，计划无声地变了个样，而报告上看不出
    曾经有过另一条。
    """

    field: PlanField
    by_module: StrategyModuleId
    by_revision: str
    overridden_module: StrategyModuleId
    overridden_revision: str
    reason: str

    @field_validator("by_revision", "overridden_revision", "reason")
    @classmethod
    def _not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("覆盖记录里的版本与理由都不能为空（见 PolicyOverride）。")
        return value


class StrategyModule(Protocol):
    """一个策略模块的形状 —— 六个模块各实现一次，编译器只认识这两样。

    ``contribute`` 只收**已经批准**的输入：上下文（客户要什么）、envelope（批准了
    什么范围）、bundle（这份部署的声明）。模块**不读盘、不调用任何东西** ——
    计划 §B16 关闭条件 1 那句「preview 零 provider 调用」之所以在结构上成立，
    全靠这一条：模块能拿到的输入里没有任何一条通向外部世界。

    它也没有「把结果写回去」的口子：返回值是 :class:`PolicyContribution` 的元组，
    编译器负责把它们拼起来。一个能自己写进计划各格的模块，会让
    :data:`FIELD_OWNER` 那张表变成一句建议。

    ## ``revision`` 不在这个形状里（它曾经在）

    版本由 ``contribute`` 交出来的**每一条贡献自带**（``PolicyContribution`` 上就有
    这一栏），所以向模块再要一次版本是**第二次读同一份声明**：两条路各自算出来的号
    必须一直相等，而它们不等时计划上看不出来 —— 那一栏写的是第二次读出来的号，
    格子里的值来自第一次。把它留在协议里，等于要求每个模块都实现一条没人消费的第二
    读法。计划那一侧从贡献上取（:func:`_module_revisions`）。
    """

    @property
    def module_id(self) -> StrategyModuleId: ...

    def contribute(
        self,
        *,
        context: ResearchContext,
        envelope: ResearchEnvelope,
        bundle: ProfileBundle,
    ) -> tuple[PolicyContribution, ...]: ...


@dataclass(frozen=True)
class DeclaredScope:
    """声明层写下来的那九格，以及每一格的出处。

    它是个中间物，不是结论：编译器把它与模块交上来的贡献合成一份计划。
    之所以有一个类型而不是九个参数，是因为那九格**必须一起被写下** ——
    分别传的话，一个调用点漏掉 ``SEND_PERMISSIONS`` 只会在别处变成一个空元组，
    而空元组在计划上读作「这次研究没有任何发送权限」（对）与「没人管这一格」（也长一样）。
    """

    values: dict[PlanField, object]
    provenance: tuple[PolicyProvenance, ...]


def compliance_rendering(compliance: dict[str, object]) -> tuple[str, ...]:
    """把 ``compliance`` 块**原样**渲染成 ``key=value``，按 key 排序。

    渲染而不是解读：这一层不认识 ``public_pages_only`` 这个名字，也不判哪几条是
    「许可」、哪几条是「义务」。它只保证「计划上写着的就是声明里写着的」。
    排序是为了让两次编译逐行可比（``application/site_crawl.py`` 那句
    「顺序是报告的一部分」在计划这一层同样成立）。
    """
    return tuple(f"{key}={compliance[key]}" for key in sorted(compliance))


def declared_scope_of(
    bundle: ProfileBundle,
    context: ResearchContext,
    envelope: ResearchEnvelope,
) -> DeclaredScope:
    """从声明与 envelope 读出那九格。**任何策略模块都改不了它们。**

    逐格的出处写在理由里：一条「来自 profile 的哪个块、经谁读出来」的话。这一层
    不做判断（不判产品用得对不对、不判预算够不够），只做搬运与记账。
    """
    declared = bundle.profile
    if declared is None:
        raise ConfigError(
            f"profile {bundle.profile_id!r} 没有声明块，因此编不出计划："
            "硬范围（市场、硬排除、预算、产品与权限）全部来自声明，而它没有。\n"
            "这里**不**退回一组默认值：一份编出来的计划会让「这一次按什么规则跑」"
            "有一个看起来完整、实际上没有人认过的答案。"
        )

    product = declared.raw.get("product") or {}
    product_claims: tuple[str, ...] = ()
    if context.product_use:
        # 只有「这次研究挂了产品用途」时才带上产品身份。空着不是「没有产品」，
        # 是「这次研究不挂产品」—— 两者在报告上必须能分开，所以这里不是
        # 「有就填、没有就填个默认」。
        name = str(product.get("name", "")).strip()
        category = str(product.get("category", "")).strip()
        product_claims = tuple(value for value in (name, category) if value)

    values: dict[PlanField, object] = {
        PlanField.GEO: envelope.geo,
        PlanField.JURISDICTION: effective_jurisdiction(context),
        PlanField.EXCLUSIONS: envelope.exclusions,
        PlanField.PRODUCT_USE: context.product_use,
        PlanField.PRODUCT_CLAIMS: product_claims,
        PlanField.ACCESS_PERMISSIONS: compliance_rendering(declared.compliance),
        # 恒为空，而且是**声明**为空：§7.3 逐字「仍不做真实邮件发送、自动回复」。
        # 这一格不是待办，是一条边界 —— 一个有生产者的空集合与一个没人管的空集合
        # 在计划上同形，而它们要防的事完全相反。
        PlanField.SEND_PERMISSIONS: (),
        PlanField.SEARCH_BUDGET: declared.search_max_queries,
        PlanField.FETCH_BUDGET: declared.fetch_max_documents,
    }

    envelope_revision = sha256_text(f"envelope{SEP}{envelope.geo}")[:16]
    reasons: dict[PlanField, str] = {
        PlanField.GEO: "profile.yaml 的 country（经 ResearchEnvelope.geo）",
        PlanField.JURISDICTION: (
            "客户声明的 jurisdiction；空着时与 geo 同管辖（"
            "research_context.effective_jurisdiction 是这一条的唯一实现）"
        ),
        PlanField.EXCLUSIONS: "profile.yaml 的 exclusions（逐条 id，经 ResearchEnvelope）",
        PlanField.PRODUCT_USE: "客户声明的 product_use，原样",
        PlanField.PRODUCT_CLAIMS: (
            "profile.yaml 的 product 块（name/category）；product_use 空着时不挂"
        ),
        PlanField.ACCESS_PERMISSIONS: "profile.yaml 的 compliance 块，原样渲染",
        PlanField.SEND_PERMISSIONS: (
            "§7.3：本产品不发邮件，所以这一格声明为空；一个想添权限的模块会被拒"
        ),
        PlanField.SEARCH_BUDGET: "profile.yaml 的 search.max_queries（未声明时为 None）",
        PlanField.FETCH_BUDGET: "profile.yaml 的 fetch.max_documents（未声明时为 None）",
    }
    provenance = tuple(
        PolicyProvenance(
            field=field, module_id=DECLARED_SCOPE, revision=envelope_revision, reason=reasons[field]
        )
        for field in PlanField
        if field in HARD_SCOPE_FIELDS
    )
    return DeclaredScope(values=values, provenance=provenance)


#: 计划摘要逐格覆盖的字段顺序。它是**声明顺序**（:class:`PlanField` 里写的那个顺序），
#: 而 ``tests/test_research_plan.py`` 断言它恰是全部成员 —— 于是有人加了一格却忘了
#: 把它算进摘要时，那条断言会红。漏掉一格的后果是静默的：两格不同的计划会得到
#: 同一个摘要，而摘要正是「这两次研究按同一份规则跑吗」的答案。
_DIGEST_FIELDS: tuple[PlanField, ...] = tuple(PlanField)


class EffectiveResearchPlan(StrictModel):
    """这一次研究**要按什么跑** —— 计划 §B16 的 ``EffectiveResearchPlan``。

    ## 它是派生的，而且每一格带出处

    这个类型不产出策略，它只把已经写好的东西**编在一起**并留痕。要问「为什么先抓
    contact 页」，问的是 :meth:`provenance_for`；要问「两次编译一样吗」，问的是
    :attr:`digest`。

    ## ``digest`` 盖住什么

    盖上每一格的值、上下文指纹的摘要、场景版本、每个模块的版本、以及
    :attr:`ambiguities`。第一组让「策略改了」看得见，接下来两组让「profile 改了」
    与「模块读到的东西改了」看得见，最后那一格让「这次是在哪几处模糊之上编出来的」
    看得见 —— 两条模糊之处不同的输入编出同一份计划，会让「这份计划是在什么之上
    成立的」在摘要上消失。
    逐格先散一次、再拼定长块整体散一次 —— 与 ``SceneFingerprint.digest`` 同一个形状，
    理由也一样：直接拼成一长串会让 ``locales=("a","b")`` 与 ``locales=("a"),
    query_families=("b")`` 撞上同一个摘要。

    **digest 不含「哪一次研究」**：它是同一个 SHA 上、同一份声明与同一份上下文编出来的
    那一份计划的内容。两次不同的研究（不同的 subject）用同一份计划，这不是错误，
    这正是「同一份声明」的意思。

    ## 与 WorkItem 冻结的关系

    这一层不做冻结。计划 §B16 关闭条件 6（``EffectiveResearchPlan`` 随 WorkItem 冻结、
    中途 profile 更新不改正在跑的 attempt）要的是**存储与派发**那一侧的事；
    这一层能提供的是它的前提：一份带着摘要、可比较、可复现的计划。
    """

    # --- 声明层独有 ---------------------------------------------------------
    geo: str
    jurisdiction: str
    exclusions: tuple[str, ...]
    product_use: str
    product_claims: tuple[str, ...]
    access_permissions: tuple[str, ...]
    send_permissions: tuple[str, ...] = ()
    search_budget: int | None = None
    fetch_budget: int | None = None

    # --- 策略模块可写 -------------------------------------------------------
    locales: tuple[str, ...]
    query_families: tuple[str, ...]
    source_methods: tuple[str, ...]
    acquisition_modes: tuple[str, ...]
    page_archetypes: tuple[PageArchetype, ...]
    role_functions: tuple[str, ...]
    role_priorities: tuple[str, ...]
    decision_refs: tuple[str, ...]

    # --- 它是从哪一份东西编出来的 -------------------------------------------
    #: ``SceneFingerprint.digest`` —— 这份计划针对的**上下文**长什么样。
    fingerprint_digest: str
    #: ``ProfileBundle.scene_version`` —— 编它时盘上那一份声明。
    scene_version: str
    #: 参与了这次编译的模块与各自的版本，按 :data:`MODULE_PRIORITY` 的顺序。
    modules: tuple[ModuleRevision, ...]
    #: 每一格恰好一条。
    provenance: tuple[PolicyProvenance, ...]
    overrides: tuple[PolicyOverride, ...] = ()
    #: 编这份计划时**上下文里说不清的那几处**，逐条照记。
    #:
    #: 走到这一步它们按构造**全是软的** —— 硬的那几维会在 :func:`fingerprint_of`
    #: 那里把编译拦住（关闭条件 4）。记下来是因为 §B16 对软的那一半逐字要求
    #: 「可在批准 envelope 内编译保守计划**并记录候选**」：一份不说自己是在哪几处
    #: 模糊之上编出来的计划，会把「这次按保守取法多问了一遍」读成「这次本来就该
    #: 这么问」。它们也进 :attr:`digest`，于是两次模糊之处不同的输入不会撞成
    #: 「同一份计划」。
    ambiguities: tuple[Ambiguity, ...] = ()

    @field_validator("scene_version", "fingerprint_digest")
    @classmethod
    def _not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError(
                "计划必须说得清它是从哪一份声明、哪一份上下文编出来的 ——"
                "两者都是空串时，「这份计划是不是那一份」就恒答「是」。"
            )
        return value

    @model_validator(mode="after")
    def _every_field_has_exactly_one_origin(self) -> EffectiveResearchPlan:
        covered = [entry.field for entry in self.provenance]
        missing = sorted(
            field.value for field in PlanField if field not in set(covered)
        )
        duplicated = sorted(
            {field.value for field in covered if covered.count(field) > 1}
        )
        if missing or duplicated:
            raise ValueError(
                "每一格必须恰好有一条出处，现在是："
                f"没有出处的 {missing}、重复出处的 {duplicated}。\n"
                "没有出处的那几格在报告上读作「没人管」，而重复的那几格读作"
                "「两处都在管」—— 后者正是 FIELD_OWNER 那张表要消掉的东西。"
            )
        return self

    @property
    def digest(self) -> str:
        """这一份计划的内容摘要 —— 同一条推导规则，全仓只有这一处。"""
        blocks = [sha256_text(self._block(field)) for field in _DIGEST_FIELDS]
        blocks.append(sha256_text(self.fingerprint_digest))
        blocks.append(sha256_text(self.scene_version))
        blocks.append(
            sha256_text(
                SEP.join(f"{item.module_id.value}{SEP}{item.revision}" for item in self.modules)
            )
        )
        blocks.append(
            sha256_text(SEP.join(f"{item.dimension.value}{SEP}{item.detail}" for item in self.ambiguities))
        )
        return sha256_text(SEP.join(blocks))

    def _block(self, field: PlanField) -> str:
        """一格 → 一个不会与别格挤在一起的定长块。

        ``page_archetypes`` 是唯一一格嵌套的：档位与它自己的 glob 之间用 ``SEP`` 隔，
        档位之间也用 ``SEP`` 隔。它进摘要之前先被整个散一次，所以嵌套不会与别的
        格子混淆 —— 那正是「逐格先散一次」这个形状的作用。
        """
        if field is PlanField.PAGE_ARCHETYPES:
            return SEP.join(
                f"{archetype.tier}{SEP}{SEP.join(archetype.patterns)}"
                for archetype in self.page_archetypes
            )
        value = getattr(self, field.value)
        if value is None:
            # 未声明的额度。``None`` 与 ``0`` 必须散得不一样：一个是「没声明」，
            # 一个是「声明了零」—— 后者会让一次研究一次都不发。
            return "None"
        if isinstance(value, tuple):
            return SEP.join(value)
        return str(value)

    def provenance_for(self, field: PlanField) -> PolicyProvenance:
        """这一格的出处。每一格恰好一条，所以这里不需要「找不到时怎么办」。"""
        for entry in self.provenance:
            if entry.field is field:
                return entry
        raise ConfigError(  # pragma: no cover - 构造期判据已经堵住
            f"计划里没有 {field.value} 这一格的出处。这在构造期就该被拒"
            "（_every_field_has_exactly_one_origin），走到这里说明那条判据漏了。"
        )


@dataclass(frozen=True)
class _Written:
    """一格的**当前**写法：谁写的、哪一版、值、以及**它自己的**理由。

    ``reason`` 跟着赢家走，而不是「最后到来的那条贡献的理由」：理由写错的那份溯源
    比没有溯源更坏 —— 它读起来像一条解释，而它解释的是另一条贡献。
    """

    module: StrategyModuleId
    revision: str
    values: tuple[str, ...]
    archetypes: tuple[PageArchetype, ...]
    reason: str


def build_plan(
    *,
    declared: DeclaredScope,
    contributions: tuple[PolicyContribution, ...],
    fingerprint_digest: str,
    scene_version: str,
    modules: tuple[ModuleRevision, ...],
    ambiguities: tuple[Ambiguity, ...] = (),
) -> EffectiveResearchPlan:
    """把声明层那九格与模块交上来的贡献拼成一份计划。

    **这是拼装，不是决策。** 谁赢由 :data:`FIELD_OWNER` 与 :data:`FIELD_OVERRIDERS`
    这两张表决定，而这个函数只负责按表执行、把每一次覆盖记下来。所以它没有
    「如果没有模块写某一格怎么办」这个分支 —— 缺一格是一次编译失败，理由见下。

    Raises:
        ConfigError: 某一格没人写、一个模块写了不属于它的那一格、或同一个模块对
            同一格写了两次。三种都是**接线**问题（模块集或声明变了），不是数据问题，
            所以它们在这里抛而不是变成一个空的计划格子。

    为什么缺一格要抛而不是留空：留空的具体形态是「这一格的声明默认值」或者一个空
    元组，而两者在报告上都读作「这一格今天没有内容」。计划 §B16 关闭条件 2 要的恰恰
    是「不同 context 得到不同 source/page/role 策略」—— 一个可以在缺一格时照常产出
    的计划，会让「策略真的变了吗」这个问题永远答「变了」。

    同名模块写两次要拒，因为它与覆盖**长得一样**（同一格有第二个写法）而处置相反：
    覆盖是一次留痕的决定，重复是一次接线错误。让后者按「后写的赢」过去的话，
    胜者会随组合根里模块的排列而变 —— 那正是「由普通代码确定」要排除的东西。

    **胜负与到达顺序无关，方法是先分组再排序。** 逐条边收边比会有一个说不通的
    角落：更具体的先来、更笼统的后到，那时「新的比旧的笼统」该算覆盖失败还是
    该让旧的赢？两种处置都不对 —— 对的处置是**根本不按到达顺序比较**。所以这里
    先把贡献按格分组，再在每一格里按 :data:`MODULE_PRIORITY` 排一次，然后从最笼统
    折到最具体：每一步若更具体的那一个赢了，就记一条覆盖。于是同一个模块集无论以
    什么顺序接上，编出来的计划逐字相同（``tests/test_research_plan.py`` 里那条
    「换一个排列，计划逐字不变」）。
    """
    overrides: list[PolicyOverride] = []
    written: dict[PlanField, _Written] = {}

    by_field: dict[PlanField, list[PolicyContribution]] = {}
    for contribution in contributions:
        by_field.setdefault(contribution.field, []).append(contribution)

    for field, writers in by_field.items():
        owner = FIELD_OWNER.get(field)
        if owner is None:  # pragma: no cover - 构造期判据已经堵住
            raise ConfigError(
                f"{field.value} 没有归属模块，但它也不在声明层独有的那九格里。"
                "FIELD_OWNER 与 HARD_SCOPE_FIELDS 这两张表漏了一格。"
            )
        overriders = FIELD_OVERRIDERS.get(field, ())
        named = [writer.module_id for writer in writers]
        repeated = sorted({item.value for item in named if named.count(item) > 1})
        if repeated:
            raise ConfigError(
                f"这些模块对 {field.value} 这一格贡献了不止一次：{repeated}。\n"
                "这不是覆盖（覆盖是另一个模块的事），是一次接线错误 —— 同一个模块"
                "给的两种写法之间没有谁更具体可言，只能由排列决定谁赢，"
                "而那不是一条写下来的规则。"
            )
        for module_id in named:
            if module_id is not owner and module_id not in overriders:
                raise ConfigError(
                    f"{module_id.value} 写了 {field.value} 这一格，"
                    f"而它既不是归属模块（{owner.value}），也不在覆盖者名单"
                    f"（{[item.value for item in overriders] or '空'}）里。\n"
                    "拒绝而不是让它覆盖：一个越权的写者在这里安静地生效的话，"
                    "「这条顺序是谁定的」就会在报告上答出一个错的模块名 ——"
                    "而那份报告看起来完全正常。"
                )

        ordered = sorted(writers, key=lambda writer: MODULE_PRIORITY.index(writer.module_id))
        for previous, current in zip(ordered, ordered[1:]):
            # 每一条边都是一次「更具体的盖掉更笼统的」。留痕记的是**这一条边**，
            # 而不是「最终赢家 vs 归属模块」—— 后者会让排在中间的那一次改写
            # 在记录里消失，于是「原来那条去哪了」仍然没有答案。
            overrides.append(
                PolicyOverride(
                    field=field,
                    by_module=current.module_id,
                    by_revision=current.revision,
                    overridden_module=previous.module_id,
                    overridden_revision=previous.revision,
                    reason=current.reason,
                )
            )
        winner = ordered[-1]
        written[field] = _Written(
            module=winner.module_id,
            revision=winner.revision,
            values=winner.values,
            archetypes=winner.archetypes,
            reason=winner.reason,
        )

    missing = sorted(field.value for field in MODULE_WRITABLE_FIELDS if field not in written)
    if missing:
        raise ConfigError(
            f"这几格没有模块写：{missing}。\n"
            "缺一格不退回声明默认值：留空的具体形态是一个空元组，而它在计划上读作"
            "「这一格今天没有内容」—— 于是「不同 context 得到不同策略」这个问题"
            "会永远答「变了」，而没有任何一处红。"
        )

    # 出处与覆盖都按 :class:`PlanField` 的**声明顺序**排一次，不按到达顺序。
    # 到这里值已经定了，这一步只影响「读起来是什么次序」—— 而次序进不去摘要
    # （摘要逐格取，不逐格拼），所以它只影响一件事：两份由不同排列编出来的计划
    # 能不能逐字比较。能，才有「由表决定」这句话。
    order = {field: index for index, field in enumerate(PlanField)}
    overrides.sort(key=lambda record: order[record.field])
    provenance = list(declared.provenance)
    provenance.extend(
        PolicyProvenance(
            field=field,
            module_id=written[field].module.value,
            revision=written[field].revision,
            reason=written[field].reason,
        )
        for field in sorted(written, key=lambda item: order[item])
    )

    def cell(field: PlanField) -> _Written:
        return written[field]

    return EffectiveResearchPlan(
        geo=str(declared.values[PlanField.GEO]),
        jurisdiction=str(declared.values[PlanField.JURISDICTION]),
        exclusions=declared.values[PlanField.EXCLUSIONS],  # type: ignore[arg-type]
        product_use=str(declared.values[PlanField.PRODUCT_USE]),
        product_claims=declared.values[PlanField.PRODUCT_CLAIMS],  # type: ignore[arg-type]
        access_permissions=declared.values[PlanField.ACCESS_PERMISSIONS],  # type: ignore[arg-type]
        send_permissions=declared.values[PlanField.SEND_PERMISSIONS],  # type: ignore[arg-type]
        search_budget=declared.values[PlanField.SEARCH_BUDGET],  # type: ignore[arg-type]
        fetch_budget=declared.values[PlanField.FETCH_BUDGET],  # type: ignore[arg-type]
        locales=cell(PlanField.LOCALES).values,
        query_families=cell(PlanField.QUERY_FAMILIES).values,
        source_methods=cell(PlanField.SOURCE_METHODS).values,
        acquisition_modes=cell(PlanField.ACQUISITION_MODES).values,
        page_archetypes=cell(PlanField.PAGE_ARCHETYPES).archetypes,
        role_functions=cell(PlanField.ROLE_FUNCTIONS).values,
        role_priorities=cell(PlanField.ROLE_PRIORITIES).values,
        decision_refs=cell(PlanField.DECISION_REFS).values,
        fingerprint_digest=fingerprint_digest,
        scene_version=scene_version,
        modules=modules,
        provenance=tuple(provenance),
        overrides=tuple(overrides),
        ambiguities=ambiguities,
    )


def _module_revisions(
    wired: tuple[StrategyModule, ...], contributions: tuple[PolicyContribution, ...]
) -> tuple[ModuleRevision, ...]:
    """计划上那一栏：有哪几个模块参与、各是哪一版。

    ## 版本从**贡献**上取，不问模块要第二次

    一条贡献自己就带着「谁写的、哪一版」（``research_strategy._as_contribution`` 是唯一
    填那两栏的地方，而 ``contribute`` 是唯一读声明的入口）。再问模块要一次版本
    （曾经有过一个 ``module.revision`` 属性），就是**第二次读同一份声明**：两条路各自
    算出来的版本必须一直相等，而它们不等时计划上**看不出来** —— 那一栏写的是第二次读
    出来的号，格子里的值来自第一次。

    ## 排列按 ``MODULE_PRIORITY``，名单按**接线**

    那一栏的先后是表的事，不是组合根接线顺序的事：照抄排列会让同一份模块集因为接线
    顺序不同而编出两份不同的计划（``digest`` 也跟着分叉）。

    名单取自 ``wired`` 而不是贡献，并且**缺一个就抛**：一个接线里的模块一格子都没交上来
    是接线坏了，不是「这个模块这次不参与」。从贡献里悄悄少一个的话，计划上那一栏会
    少一行，而报告上看起来一切正常。

    Raises:
        ConfigError: 接线里有模块没交上任何一格。
    """
    seen: dict[StrategyModuleId, str] = {}
    for contribution in contributions:
        seen.setdefault(contribution.module_id, contribution.revision)
    ordered = sorted(wired, key=lambda item: MODULE_PRIORITY.index(item.module_id))
    missing = tuple(item.module_id.value for item in ordered if item.module_id not in seen)
    if missing:
        raise ConfigError(
            f"接线里的模块 {list(missing)} 一格子都没交上来，因此「这一次有哪几个模块"
            "参与、各是哪一版」这一栏没有依据。\n"
            "它的版本只能从它自己交出来的贡献上取（同一份声明只读一次），所以零格意味着"
            "要么它今天还没有声明来源，要么它的 ``contribute`` 与 ``cells`` 分叉了 ——"
            "两种都不该被读成「这个模块这次不参与」。"
        )
    return tuple(
        ModuleRevision(module_id=item.module_id, revision=seen[item.module_id]) for item in ordered
    )


@dataclass(frozen=True)
class StrategyCompiler:
    """把六个策略模块接起来的那一处。

    ``modules`` 是**组合根**给的一份元组，编译器不认识任何一个具体模块 —— 它只调用
    :meth:`StrategyModule.contribute` 并按表拼装。所以「换一个模块」与「改一条覆盖
    规则」是两件分开的事，各自改各自的地方。

    那份元组的**排列不进结果**：胜负、出处、覆盖记录、``plan.modules`` 那一栏都按
    :data:`MODULE_PRIORITY` 排过。把排列接进去的话，同一份模块集会在不同的接线顺序
    下编出两份计划 —— 而它们在报告上看起来都对，因为它们各自内部一致。

    这一句的执行器是 ``tests/test_research_strategy.py::test_the_wiring_order_does_not_reach_the_plan``：
    ``default_modules`` 今天本来就是表序，所以**不反着接一次**，这句话在测试上是空的。

    ## 它做三件判断，每一件都先于拼装

    1. **硬歧义不编。** 上下文讲不清硬的那一半时 :func:`fingerprint_of` 会抛
       （§B16 关闭条件 4：模糊输入影响 hard scope 时要 hold/review，不能由模型
       置信度越过）。这一层不接住那个异常 —— 它属于调用方要处置的情形。

       调用方那一侧的做法是**在调这一层之前先问同一张表**：
       ``application.plan_preview`` 按 :func:`ambiguity_is_hard` 把硬的那几条**逐条
       点名**，非空就根本不进来编。所以那条路上这个异常**不会响**；它响起来意味着
       两处对同一张表的读法分叉了，而那是判据该抓的错，不是一次 HOLD
       （``tests/test_plan_preview.py::test_the_two_readers_of_one_table_agree``）。
    2. **越界不编。** 请求落在批准范围之外时抛：那是治理问题，不是业务情形。
    3. **越权的贡献不编**（见 :func:`build_plan`）。

    三件判断都在**动手拼装之前**，因为一份编到一半的计划更危险 —— 它有 digest、
    有出处、读起来完整，而它描述的是没有人批准过的一次研究。
    """

    modules: tuple[StrategyModule, ...]

    def compile(
        self,
        *,
        bundle: ProfileBundle,
        context: ResearchContext,
        envelope: ResearchEnvelope,
    ) -> EffectiveResearchPlan:
        """编一份可复现、可解释的计划。

        ``envelope`` **没有缺省值**：它是批准那一侧，由调用方决定哪一份声明算批准
        （唯一的产出者是 :func:`marketops.domain.research_context.envelope_of`）。
        在这里替调用方推一份出来的话，「这一次按哪一份批准范围跑」就会有一个
        没人认过的答案。

        Raises:
            ValueError: 上下文有硬歧义（来自 :func:`fingerprint_of`）。**调用方应当在
                进来之前先按同一张表判一次**，并把那几条逐条报出去（见类 docstring
                第 1 条）：一个只在异常消息里存在的「哪几条」答不出 §253 要的那一问。
            ConfigError: 请求越界、接线有问题、或某一格没人写。
        """
        fingerprint = fingerprint_of(context)
        out_of_envelope = constraints_out_of_envelope(context, envelope)
        if out_of_envelope:
            details = "\n".join(
                f"  - [{item.dimension.value}] 请求 {item.requested!r}，"
                f"而批准的是 {item.allowed!r}（{item.detail}）"
                for item in out_of_envelope
            )
            raise ConfigError(
                "这份请求落在已批准的范围之外，因此编不出计划：\n"
                f"{details}\n"
                "这是治理问题（讲得太清楚，但讲到了边界外面），不是业务情形 ——"
                "处置是让人来批一次，不是把这一格改小再编。"
            )

        declared = declared_scope_of(bundle, context, envelope)
        contributions = tuple(
            contribution
            for module in self.modules
            for contribution in module.contribute(
                context=context, envelope=envelope, bundle=bundle
            )
        )
        return build_plan(
            declared=declared,
            contributions=contributions,
            fingerprint_digest=fingerprint.digest,
            scene_version=bundle.scene_version,
            # 版本从上面那一次 ``contribute`` 交出来的贡献上取 —— 不再问模块要第二次
            # （理由与排列口径见 :func:`_module_revisions`）。
            modules=_module_revisions(self.modules, contributions),
            # 走到这里它们按构造全是软的（硬的那几维在 fingerprint_of 那里拦住了）。
            # 照记，理由是关闭条件 4 的另一半：软的可以在批准范围内编一份保守计划，
            # 但要让人看得出来这次是在哪几处之上守的。
            ambiguities=ambiguities_of(context),
        )


__all__ = [
    "DECLARED_SCOPE",
    "FIELD_OVERRIDERS",
    "FIELD_OWNER",
    "HARD_SCOPE_FIELDS",
    "MODULE_PRIORITY",
    "MODULE_WRITABLE_FIELDS",
    "DeclaredScope",
    "EffectiveResearchPlan",
    "ModuleRevision",
    "PageArchetype",
    "PlanField",
    "PolicyContribution",
    "PolicyOverride",
    "PolicyProvenance",
    "StrategyCompiler",
    "StrategyModule",
    "StrategyModuleId",
    "build_plan",
    "compliance_rendering",
    "declared_scope_of",
]
