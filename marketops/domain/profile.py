"""一个 profile 的**业务规则值**。

具体市场的业务规则只进 ``profiles/``，不 hard-code 到通用引擎。本模块承载的是
那些规则**读进来之后**的形状：等级规则、账号准入规则、角色词表。

**这里没有 YAML。** 把 ``profiles/<id>/*.yaml`` 读成本模块这些对象的活，
在 ``marketops/adapters/profile_loader.py`` 里 —— 那是装载器，它要碰磁盘。分界线就画在这里，
而且它是有后果的：

    在拆分之前，``grading/engine.py``（今天的 ``application/grading.py``）
    从顶层装载器取 ``ProfileBundle``，于是 ``import marketops.grading.engine``
    会连带执行 ``import yaml`` 与
    ``import marketops.config``（后者是唯一读 ``os.environ`` 的模块）。
    「纯判定」因此只在单个文件内成立 —— 想给引擎写一个不需要磁盘的测试，
    会拉进一整条装载链。

现在引擎只依赖本模块，而本模块只依赖 ``domain`` 内部的东西。

装载出来的是**两个轴**，它们的区别是本模块最重要的概念：

===========================  =================================================
:attr:`ProfileBundle.rules`  **路线**等级规则（A/B/C/D/X）。引擎逐条求值，
                             只有它决定 ``ContactRoute.grade``。
:attr:`ProfileBundle.account_rules`  **账号**准入规则（``grading.yaml`` 的
                             ``x_rules``）。引擎求 value 出 ``AccountStatus``。
===========================  =================================================

两者**不互相决定**（SOP v1 L188 是两个状态机）。把它们合成一份规则，
「组织对、人不对」这种情形就无处安放。
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Any

from .domain_attribution import OfficialDomainVocabulary
from .enums import (
    AccountStatus,
    BusinessFunction,
    DecisionTask,
    Grade,
    RoleClass,
)
from .temporal import FreshnessPolicy


def as_text(value: object) -> str:
    """声明里的标量 → 去空白字符串。

    装载器（``marketops/adapters/profile_loader.py``）也用这一个 —— 它要把 YAML 里的标量
    读成字符串。一处实现而不是两处：这份「空值读成空串」的口径如果漂移，
    会以「某个字段在某个文件里是 ``None``、在另一个里是 ``""``」的形式出现，
    而那种差异在报告上看不出来。
    """
    return str(value).strip() if value is not None else ""


# --- 引擎契约 ---------------------------------------------------------------


@dataclass(frozen=True)
class GradingRule:
    """引擎契约里的单个**路线**等级。

    ``definition`` 与 ``requires`` 各有两种「空」，**不能混为一谈**：

    * ``None`` —— 引擎方言表达不了这条规则：规则未知，engine 必须拒绝判定。
    * ``()`` —— 显式的空条件列表：规则**已定义**且不附加任何条件。

    后者正是 ``X`` 需要的语义（「未判定」是收尾级，不依赖任何事实）。
    早先版本用 ``not self.requires`` 判未知，会把已定义的级别误报成未知 ——
    那不是保守，是把「规则齐全」和「规则缺失」在数据上抹平了。

    ``()`` **不是** ``D`` 的语义。D 在 SOP L128 里是一条正面定义（绑定账号的
    公开真实泛化官方邮箱），它有自己的条件；早先版本给 D 配 ``conditions: ['true']``
    当兜底，实际是一条「没被别人拿走就算 D」的规则，与 D 的定义无关，
    而且会让任何未被识别的页面冒充「低优先触达储备」。

    注意 ``None`` 的**来源**有两种，且必须区分：业务没声明这条规则，
    与业务声明了但引擎还求不了值。两者在数据上都是 ``None``（engine 的反应
    必须一样：拒绝判定），区别记在 :attr:`ProfileBundle.unknowns` 的措辞里。
    """

    grade: Grade
    definition: str | None = None
    requires: tuple[str, ...] | None = None
    #: 这条规则来自 SOP 的哪一处。**是定位信息，不是规则本身** ——
    #: SOP 正文只有一份，在 ``sources/`` 里；这里只记指针。
    sop_reference: str = ""
    raw: dict = field(default_factory=dict)

    @property
    def is_unknown(self) -> bool:
        return self.definition is None or self.requires is None


@dataclass(frozen=True)
class AccountRule:
    """一条**账号准入**规则（``grading.yaml`` 的 ``x_rules`` 条目）。

    ``requires is None`` 的含义是「**不可求值**，因此永不命中，并列进 unchecked」。
    这与 :class:`GradingRule` 的 ``requires == ()``（显式恒真）**不是**同一件事：

        一条恒真的排除规则会排除掉整个名单，而且它看起来完全正常。

    装载层因此拒绝「写了 requires 但是空列表」—— 要么给出可求值的子句，
    要么整个省略这个键。
    """

    id: str
    description: str
    status: AccountStatus
    requires: tuple[str, ...] | None = None
    #: SOP 原话（表达式方言）。**仅供审计** —— 引擎不求值它，也不假装求过。
    declared_when: str = ""


# --- SOP 声明层 -------------------------------------------------------------


@dataclass(frozen=True)
class DeclaredGrade:
    """SOP 声明 + 编译出的单个**路线**等级（``grading.yaml`` 的 ``grades.<G>``）。"""

    grade: Grade
    label: str = ""
    description: str = ""
    #: 引擎子句方言。``()`` 是显式恒真 —— 只有收尾级 ``X`` 用它。
    requires: tuple[str, ...] = ()
    sop_reference: str = ""


@dataclass(frozen=True)
class DeclaredGrading:
    """``grading.yaml``（``marketops/grading.v2``）装载结果。

    ``ungradeable`` 是**账号侧**声明与引擎之间的距离：它逐条列出**还求不了值**的
    规则（``requires is None``）的 ``when`` 表达式引用到的 ``account.<第一跳>``。
    它的用途是让「为什么账号还推不动」在数据里可读，而不是散在某一句提示里。
    **它说的是装载期**（「规则点名了引擎词表里没有的事实」），与
    **``AccountOutcome.unchecked`` 说的运行期**（「规则点名的事实今天没有值」）
    不是同一件事 —— 一个为空不代表另一个为空。

    帐号规则全部编译出 ``requires`` 之后它是**空**的（今天就是），而空**不等于**
    「这些条件都判得了」：引用候选事实的那五条，引擎手上的值仍是 ``None``，
    规则落进 ``AccountOutcome.unchecked``。这份清单回答的是「还有哪些规则没写完」，
    不是「还有哪些事实没有值」。**一个空的清单也不该被手工填回** ——
    往里塞一个「其实已经有了」的缺口，会让整份清单失去可信度，而带假阳性清单的
    下场是没人再看它。

    **这里没有 ``fallback_grade``。** 旧 profile 有一个 ``fallback_grade: D``，
    而 D 的 ``conditions`` 恰好是 ``["true"]`` —— 于是一条写在「信息不足」名下的
    兜底规则，实际效果是「凡是没被上面拿走的，都算 D」。SOP v1 L125–L129 的五个
    层级**每一条都是正面定义**（D 是「目标账户绑定的公开真实泛化官方邮箱」），
    里面没有兜底级。真正「信息不足」的情形（抓不到 / 403 / 空页 / 解析失败）
    按 L119 属于 ``blocked`` / ``no_route``，不是等级 —— 见 ``enums.BlockReason``。

    因此没有匹配上任何一级时，引擎返回 ``X`` + ``RULES_UNKNOWN``（fail closed），
    而不是替 SOP 造一个兜底级别。
    """

    schema: str
    profile: str
    subject: str
    x_rules: tuple[AccountRule, ...]
    grades: dict[Grade, DeclaredGrade]
    page_priorities: dict[str, str] = field(default_factory=dict)
    #: 邮箱 local part 里的业务词元 → 业务口径。C 级「官方业务入口」靠它识别。
    business_entry_locals: dict[str, BusinessFunction] = field(default_factory=dict)
    #: 泛化官方邮箱的 local part。D 级（SOP L128）靠它识别。
    generic_mailbox_locals: tuple[str, ...] = ()
    scoring: dict[str, Any] = field(default_factory=dict)
    evidence_rules: dict[str, Any] = field(default_factory=dict)
    ungradeable: tuple[str, ...] = ()


@dataclass(frozen=True)
class DeclaredRoleGroup:
    """一个角色组（``role-matrix.yaml`` 的 ``role_groups`` / ``excluded_roles`` 条目）。

    ``role_class`` 与 ``business_function`` 是**两组独立的业务事实**：
    前者说「这个岗位在路线里扮什么角色」，后者说「它落在哪个业务口径上」。
    grounds leadership 是 decision + grounds，venue manager 是 influencer + venue,
    finance 是 excluded + finance。压成一个字段会让 C 级「与场地业务口径绑定」
    这条规则没法表达。
    """

    id: str
    role_class: RoleClass
    weight: float
    match: tuple[str, ...]
    business_function: BusinessFunction = BusinessFunction.UNKNOWN
    rationale: str = ""


@dataclass(frozen=True)
class DeclaredRoleMatrix:
    """``role-matrix.yaml``（``marketops/role-matrix.v2``）。

    ``groups`` 与 ``excluded_groups`` **不合并**：前者的 ``match`` 进
    ``role_terms``，会被 ``extract.extract_roles`` 当作正面角色证据；
    后者表达的是「这一类岗位**不得进入有效路线**」（SOP v1 L129/L131：
    财务 / 招生 / HR / 教学 / 一般行政 / 无场地绑定的客服与预订）。
    合并会让整份排除词表反过来变成角色证据，同时让排除规则失去输入。
    """

    schema: str
    profile: str
    groups: tuple[DeclaredRoleGroup, ...]
    excluded_groups: tuple[DeclaredRoleGroup, ...] = ()
    normalization: dict[str, Any] = field(default_factory=dict)
    priority: dict[str, Any] = field(default_factory=dict)

    @property
    def classes(self) -> tuple[str, ...]:
        """声明过的 role class，保序去重。"""
        return tuple(dict.fromkeys(group.role_class.value for group in self.groups))

    @property
    def business_functions(self) -> tuple[str, ...]:
        """各组声明的业务口径，**按书写顺序**去重。

        ``unknown`` 不算一种口径（它答的是「这一组没写口径」），所以它不进这一份
        —— 把它当成一种口径写进计划，就是让「这份声明没说」变成「这次要去找
        认不出的岗位」。需要知道有几组没写的读 :attr:`undecided_groups`。

        这一份与 :attr:`classes` 是**两件事**：前者说「有哪些口径」，后者说
        「有哪些类」。压成一个字段会让「哪几类更靠前」与「找哪几种口径」
        在计划上同形，而它们答的是不同的问。

        定义放在这里而不是让每个读者各写一遍：口径的**成员资格**（谁算、谁不算）
        只有一个答案，而两份实现不一致时，不一致的那一处不会报错 ——
        它只会算出一份少一个口径的清单，而计划上看起来完全正常。
        """

        return tuple(
            dict.fromkeys(
                group.business_function.value
                for group in self.groups
                if group.business_function is not BusinessFunction.UNKNOWN
            )
        )

    @property
    def undecided_groups(self) -> int:
        """没写业务口径的组数（``unknown``）。它们不进 :attr:`business_functions`。"""
        return sum(
            1 for group in self.groups if group.business_function is BusinessFunction.UNKNOWN
        )


@dataclass(frozen=True)
class DeclaredScenarioBlock:
    """``scenario-matrix.yaml`` 里**一个场景**那一块：这个场景的收窄。

    ## 四格都是**收窄**，逐格必填

    每一格列的名字必须已经在声明里（``page_discovery_hints`` 的档位 /
    ``role-matrix.yaml`` 各组声明的业务口径 / 它声明的 ``class_order`` /
    ``domain/sources.py`` 那个注册表），并且**保持声明的次序** —— 也就是子序列。

    块能收窄的是「哪几种」，不是「谁先谁后」：``page_discovery_hints`` 的顺序同时
    是抓取前沿（见 ``application/research_strategy.py::_page_archetypes_cell``），
    而 ``priority.class_order`` 就是「哪一类更靠前」那句话本身。在块里改先后
    会让「这一份声明说的先后」有两个答案，而两个答案不一致时没有一处会红。

    **四格都必填、都非空。** 一格空着不读作「这一格不收窄」：那会让「按这个场景
    收窄」与「这个场景什么都没说」在计划上同形 —— 与 ``search.locales`` 空着
    不读作「不限语言」是同一条判据（见 ``_locales_cell``）。没写的场景不是靠
    留空表达，是靠**不给它这一块**。

    ## ``sources`` 装的是方法 id，不是声明块的标签

    ``page_discovery_hints`` 一个标签今天开出两条路（站内检索与站点抓取，见
    ``_SOURCE_ROUTES``），而一个场景可能只要其中一条。所以这一格列的是
    ``domain/sources.py`` 注册表里的 id。它说的是「已开的这几条里，这个场景留
    哪几条」——**不是**「这里新开了什么」：开不出一条声明没开的路，因为这一格
    只做交集。
    """

    #: 先看哪几档页面。逐条是 ``page_discovery_hints`` 的键。
    page_tiers: tuple[str, ...]
    #: 找哪几种业务口径的岗位。逐条是各 ``role_group`` 声明的 ``business_function``。
    business_functions: tuple[str, ...]
    #: 哪几类岗位更靠前。逐条是 ``priority.class_order`` 里的成员。
    class_order: tuple[str, ...]
    #: 留哪几条来源方法。逐条是 ``domain/sources.py`` 注册表里的 id。
    sources: tuple[str, ...]


@dataclass(frozen=True)
class DeclaredScenarioMatrix:
    """``scenario-matrix.yaml``（``marketops/scenario-matrix.v1``）。

    ## 这一份就是本 profile 的**场景词表**

    ``ScenarioCandidate.reading`` 的 docstring 说它是「它落到**这个 profile 的词表
    上**是哪一个值」，而在这一份出现之前，那句话没有承载者：读法是自由文本，
    谁也没说过哪些读法是这个 profile 认的。``blocks`` 的键就是那份词表 ——
    声明里写过块的场景，就是这份声明说过话的场景。

    ## 没有块的场景 = 整份声明，不是「这一格空着」

    ``blocks.get(reading)`` 取不到时，那四格按声明原样写进计划，**并且理由里
    写明用的是哪一份**（见 ``application/research_strategy.py::_scenario_block``）。
    这一条是**块一级**的回退，与上面「块内的格不得留空」不冲突：前者是「这份声明
    还没对这类场景说过话」，后者是「说了一半」。

    为什么不要求块覆盖声明里出现过的每一个场景：那会逼着作者为不打算收窄的场景
    抄一整份声明，而抄出来的那几行与真收窄的那几行在 diff 里长得一样 —— 收窄的
    那一次因此不容易被看见。代价也写下来：「作者想收窄但键名拼错了」与「作者
    没给这个场景写块」在计划上同形，两者的处置都是去看这一份声明。
    """

    schema: str
    profile: str
    #: 场景读法 → 那一块。同上：键就是这份声明的场景词表。
    blocks: dict[str, DeclaredScenarioBlock] = field(default_factory=dict)

    @property
    def scenarios(self) -> tuple[str, ...]:
        """这份声明说过话的场景，按书写顺序（``dict`` 保序，键本来就唯一）。"""
        return tuple(self.blocks)


# --- 分类词表（页面 → 资产事实）------------------------------------------------
#
# 「未确认」的**正式写法**是这里这一个字符串，而不是空串。
#
# 两者的差别是本仓最贵的一条：规则方言里 ``""`` 是**已确认的空**（「记了，是空」），
# 它会真的参与求值、会被取反、会让一条排除规则**确定地**不命中；而未确认在任何
# 极性下都**不决定**子句（规则进 ``unchecked``、判词 ``UNRESOLVED_FACTS``）。
# 把「还没查出来」写成 ``""``，等于替一个没做的判定签了字 —— 它在报告上与一条结论
# 长得一模一样（见 ``marketops/application/grading.py::evaluate_clause``）。
CLASSIFICATION_UNKNOWN = "unknown"

#: 一条分类轴的名字就是**事实名**（``marketops/domain/facts.py`` 的同名事实）。
#:
#: 逐字同名是刻意的：分类的产出直接进 ``candidate_facts`` 的投影，中间没有映射表 ——
#: 一张映射表漂移的那一次不会有任何症状，规则只是**永不命中**。
CLASSIFICATION_AXES: tuple[str, ...] = (
    "organisation_kind",
    "venue_environment",
    "surface_class",
    "country",
)


@dataclass(frozen=True)
class ClassificationValue:
    """一个轴上的一个取值，以及「什么算看到了它」。

    ``phrases`` 是**页面文字**上的判据，``host_suffixes`` 是**主机名**上的。
    两者分开而不是合成一张表：主机名不是页面内容（一台主机的后缀说的是注册地，
    不是这一页在说什么），而合成之后「这条证据来自哪里」在审计上就再也说不清。

    ``excludes`` 回指 ``profile.exclusions`` 里**已声明**的排除项 id。它挂在这里、
    不挂在轴上，因为「看到 3G 就命中 three_g」与「看到 hybrid 不命中任何东西」是
    两个不同的判断 —— 轴级的开关表达不了这种差别。

    ``value`` **不得**是 :data:`CLASSIFICATION_UNKNOWN`：那个字符串是「没有结论」的
    保留写法，把它当成一个取值，等于让「查出来是 unknown」与「没查出来」在数据上
    重新合流 —— 而这一层存在的全部理由就是不让它们合流。
    """

    value: str
    phrases: tuple[str, ...] = ()
    host_suffixes: tuple[str, ...] = ()
    excludes: tuple[str, ...] = ()


@dataclass(frozen=True)
class ClassificationAxis:
    """一条轴：它的名字（= 事实名）与取值表。

    取值**保序**：匹配是逐值跑的，声明顺序就是审计时读到的顺序，也是
    「哪一条先被看到」的答案。本层不靠顺序做取舍（见分类器的多值规则），
    但顺序仍然要稳定，否则同一份词表在两次运行里会给出不同的解释。
    """

    axis: str
    values: tuple[ClassificationValue, ...]

    @property
    def value_names(self) -> tuple[str, ...]:
        return tuple(value.value for value in self.values)


@dataclass(frozen=True)
class ClassificationVocabulary:
    """``profile.yaml`` 的 ``classification`` 块：页面 → 资产事实的**全部**知识。

    **取值是市场知识，所以住在 profile。** 核心代码不认识任何一个取值 ——
    ``tests/test_no_hardcoded_market_knowledge.py`` 对 ``marketops/`` 做 AST 扫描，
    标识符与字符串字面量一起查，把 ``natural_grass`` 写进分类器会立刻变红。
    分类器只认识 :data:`CLASSIFICATION_AXES` 这四条轴**的名字**（它们同时是事实名）。

    ``axes`` 允许是子集：一份 profile 可以先给一条轴词表、别的不给。没给的那条轴上
    分类器恒返回 ``unknown``，理由逐条写进读数 —— 而 ``unknown`` 不决定任何子句，
    所以「没给词表」与「给了词表但这一页没命中」在判词上同形（都是 hold），
    但在**为什么**上分得开。
    """

    axes: tuple[ClassificationAxis, ...]

    def axis(self, name: str) -> ClassificationAxis | None:
        for axis in self.axes:
            if axis.axis == name:
                return axis
        return None

    def value_names(self, name: str) -> tuple[str, ...]:
        axis = self.axis(name)
        return axis.value_names if axis is not None else ()

    def exclusion_ids(self) -> tuple[str, ...]:
        """词表里回指过的全部排除项 id，保序去重。"""
        return tuple(
            dict.fromkeys(
                excluded
                for axis in self.axes
                for value in axis.values
                for excluded in value.excludes
            )
        )


@dataclass(frozen=True)
class DeclaredProfile:
    """``profile.yaml``（``marketops/profile.v2``）。

    逐字段类型化的只有**代码真的会读**的那部分。``raw`` 保留原文，因为
    ``search.query_templates`` 这类是给人看的声明 —— 为它们各造一个 dataclass
    只会多一层需要同步维护的壳。

    注意 ``page_discovery_hints`` 与 ``grading.yaml`` 的 ``page_priorities``
    是**两件事**：前者是「先抓哪些 URL」，后者是「邮箱在记录块里怎么被绑定」。
    旧 profile 把两者都叫 P1/P2/P3 —— 那是这次修正掉的一个真实混淆。

    注：``search`` 里的查询模板目前**没有消费者**（``application/queries.py::build_queries``
    仍走通用回退）。这不是遗漏，是还没接线；在接线之前，谁也不能说
    「按 SOP 的模板检索」已经成立。

    同一块里 ``search.locales`` **已经**有消费者了（``search_locales``）：它进
    ``SearchQuery`` 的语言格。查询模板还留在 ``raw`` 里，理由是它至今没有人读的
    形状与 ``locales`` 不同 —— ``locales`` 现在是一个**必填**格的上游，不类型化
    就得让每个使用方各自去 ``raw`` 里翻，那正是「同一个声明被读成两处」的形状。
    """

    schema: str
    name: str
    display_name: str
    country: str
    exclusions: tuple[dict[str, Any], ...]
    compliance: dict[str, Any]
    #: 页面发现提示：抓取时先抓哪些 URL。**不是** P1–P4。
    page_discovery_hints: dict[str, tuple[str, ...]] = field(default_factory=dict)
    #: ``search.max_queries`` —— 一个账号的检索额度上限。``None`` 表示**没声明**。
    #:
    #: 它住在 profile 而不是 ``Settings`` 里，因为 §8 要求「调用方不得静默扩大
    #: 上限」，而环境变量正是调用方设的：一个 ``MARKETOPS_MAX_QUERIES=1000``
    #: 就能把上限扩大且不留痕。profile 是版本化的，改了会在 diff 里出现。
    #:
    #: ``None`` 与 ``0`` **不是一回事**：``None`` 是「没声明」，使用方必须
    #: 失败关闭（见 ``application/budget.py``）；``0`` 是「已声明为零额度」，
    #: 那是一次都跑不了，同样必须显性失败而不是静默什么也不做。
    search_max_queries: int | None = None
    #: ``search.locales`` —— 这个 profile 检索时该问**哪种语言**（BCP-47 标签）。
    #:
    #: 与 ``country`` 是**两个轴**（SOP 的「国家 ≠ 语言」）：``country`` 说问哪个
    #: 国家的索引，这一格说用哪种语言问。``en-GB`` 这种标签同时带着两者，所以
    #: 使用方只取它的**主语言子标签**（``domain/util.py::primary_language_subtag``），
    #: 地区部分由 ``country`` 负责 —— 详见 ``ports/search.py`` 的 ``SearchQuery``。
    #:
    #: ``()`` 表示**没声明**，而不是「无需语言」。使用方必须失败关闭
    #: （``application/queries.py::market_query`` 抛 ``ConfigError``）：填一个
    #: 看起来合理的缺省语言，会让「这次问的是英文索引」这句话在报告上没有任何
    #: 出处 —— 而它恰恰决定了哪些页面会被返回。
    search_locales: tuple[str, ...] = ()
    #: ``fetch.max_documents`` —— **一个主体在一次复核里**最多抓几份文档
    #: （``BudgetLine.DOCUMENT``）。``None`` 表示**没声明**，使用方必须失败关闭。
    #:
    #: 与 ``search_max_queries`` 同一条纪律：§8 要求「调用方不得静默扩大上限」，
    #: 而抓一份文档会花掉一次 HTTP 请求（浏览器那一级更贵），所以它同样得是一条
    #: 版本化的声明。**两个数是分开的**，因为它们量的不是一件事：一次检索是「去
    #: 找有没有」，一次抓取是「把这一页拿回来」，而 §8 把四类科目分开记账正是为了
    #: 让人能回答「这次超支是搜索跑多了还是模型跑多了」。
    #:
    #: 粒度是**主体**（与 ``search_max_queries`` 一样按 scope 的主语各记各的）：
    #: 一个主体的一次复核吃光额度，不影响另一个主体 —— 反过来（run 级）会让一个
    #: 页面特别多的主体把其余主体的复核额度一起吃掉，而报告上只显示「其余主体
    #: 没有产出」。
    fetch_max_documents: int | None = None
    #: 页面 → 资产事实的分类词表（``profile.yaml`` 的 ``classification`` 块）。
    #: ``None`` = 这个 profile 没声明词表，而那不是错误 —— 一份 profile 可以先只有
    #: 规则、后有分类。缺的那一侧在**运行时**显式报成 ``unknown``（不是造一张空
    #: 词表糊过去）：空词表会让「profile 没说怎么分类」与「这一页什么都没命中」
    #: 在读数上同形，而后者是这一层要分开的一件事。
    classification: ClassificationVocabulary | None = None
    #: 判定**官方域名归属**所需的参数（``profile.yaml`` 的 ``official_domain`` 块）。
    #: ``None`` = 这个 profile 没声明那一块，而那不是错误 —— 判据本身不认识任何
    #: 具体域名（它在 ``domain/domain_attribution.py``），这一块只给它的参数。
    #:
    #: 缺这一块的后果是**安全**的：没有第三方名单 ⇒ 没有任何主机因此被排除 ⇒
    #: 「读不出官网」的账号仍然停在 ``unknown``（它不决定任何子句）。反过来，
    #: 把它当成「缺省一切正常」去猜一份内建名单，才是这一层禁止的那件事 ——
    #: 一份看不见的名单会让「这台主机为什么被排掉」在报告上消失。
    official_domain: OfficialDomainVocabulary | None = None
    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def exclusion_ids(self) -> tuple[str, ...]:
        return tuple(as_text(item.get("id")) for item in self.exclusions if isinstance(item, dict))


# --- 决策题面 ---------------------------------------------------------------


@dataclass(frozen=True)
class DeclaredTaskSpec:
    """一个决策 task 的**声明题面**（``profile.yaml`` 的 ``decisions`` 块里的一条）。

    题面是**声明**，不是从别处推出来的。这一点值得写下来，因为「派生一个题面」
    听起来更省事，而它只有两条出路，两条都不成立：

    * 从 ``role-matrix`` 的 ``match`` 词派生选项描述 —— 那等于把匹配规则抄进题面，
      后端做的事就从「判断」退化成「复述规则」，而规则做不到的那部分（没见过的新
      写法）恰恰是这一族样本要量出来的东西；
    * 拿闭集取值本身当描述（``"x"`` → ``"x"``）—— 字段非空，却一个字的信息都没有，
      而 ``DecisionQuestion`` 的非空校验会以为它填过了。

    所以它住 profile（「词表住 profile」的同一个理由），而 ``labels`` 的键集由
    ``application/decision_specs.py`` 对齐到 profile 自己的闭集。那个对齐是一条
    **执行器**：闭集多出一个取值而这里没跟上，装载期就红，而不是等到某个账号的
    run 跑到一半、记录上写着一条闭集之外的取值。

    ``kind`` **不在这里**：三个 task 全是分类题（``QuestionKind.CHOICE``），而端口
    里另一个问法（``YES_NO``）是为别的判定准备的。声明一个恒等于同一个值的字段，
    比不声明它更坏 —— 它看起来像「这个 profile 在这两者之间选过」。
    """

    instructions: str
    labels: dict[str, str]


@dataclass(frozen=True)
class DeclaredDecisions:
    """``profile.yaml`` 的 ``decisions`` 块：task → 它的题面。

    外层用 ``None`` 表示**这个 profile 没声明决策题面**，而不是一个空映射：
    「没声明」与「声明了零个 task」在调用方看来必须是两件事 —— 后者是一份写坏了
    的声明，而它必须红。
    """

    tasks: dict[DecisionTask, DeclaredTaskSpec]


# --- 冻结的 Scene 内容 -------------------------------------------------------


#: 每文件摘要里「相对路径」与「文件内容」之间的分隔符。
#:
#: 一个不可能出现在路径里的字节：POSIX 文件名里被禁的只有 ``/`` 与它。没有它时
#: 路径 ``("a", "bc")`` 与 ``("ab", "c")`` 会给出同一个中间串 —— 两个不同的目录
#: 算出同一个版本，而版本唯一的作用就是「不是同一份就别相等」。
#:
#: 它住在这里而不是装载器里：算版本的**只有** :meth:`SceneFiles.version` 一处，
#: 而分隔符是那份算法的一部分。装载器 import 它，不重写它。
VERSION_SEP = b"\x00"


@dataclass(frozen=True)
class SceneFile:
    """Scene 目录里的一个文件：相对 posix 路径 + 它**当时**的字节。

    ``path`` 是相对 Scene 目录的 posix 路径，不是绝对路径 —— 绝对路径会把
    「这个 Scene 装在哪台机器上」写进一份本该可移植的规则里。
    """

    path: str
    content: bytes

    @property
    def sha256(self) -> str:
        """**内容**的摘要（不含路径）。

        不含路径是刻意的：这个值要回答的是「你手里这个文件是不是我当时读到的
        那几个字节」，而那是一个关于内容的问题。路径是查找的一部分（它在
        ``entries`` 里已经排好序），不是这个值要覆盖的东西。

        它与 :attr:`SceneFiles.version` 那一层不同：那一层算的是**整批**（含路径），
        因为版本号答的是「这整套规则换过没有」。
        """
        return hashlib.sha256(self.content).hexdigest()


@dataclass(frozen=True)
class SceneFiles:
    """一个 Scene 目录在**某一刻**的全部字节 —— 版本与内容住在同一个值里。

    ## 为什么两者必须在同一个值里

    ``scene_version`` 是对这些字节算的摘要。两样分开持有（一处读版本、另一处读
    内容）时，「这个版本」与「这些内容」可以来自**两次不同的读**，而两次读之间
    目录被人改过时没有任何一处会红：盘上钉的是一个版本号，附着的却是另一份内容。
    这套机制存在的唯一理由就是让「这件工作按哪一份规则在跑」可核，而那个失败
    把可核变成不可核，且不留痕迹。

    ## ``entries`` 的两条判据不是风格

    * **按 ``path`` 升序**：目录遍历顺序由文件系统给，不保证稳定 —— 不排序的版本
      会在没有任何改动时变来变去。这条判据有先例：装载器当初那个算版本的私有函数
      就写着它，而那个函数今天已经是 :meth:`version` 一处。
    * **``path`` 互不重复**：重复只可能来自一次拼装错误，而它的后果是同一个路径
      被算进版本两次 —— 版本仍然稳定、仍然唯一，只是它描述的不是目录的样子。

    ## 覆盖面是**目录**，不是「那三个 YAML」

    今天这个目录里只有三个文件，但 ScenePack 还会带**程序性指引**
    （``docs/SCENE_EVOLUTION.md`` 的 ``skill/SKILL.md``），而那种文件的名字今天还
    不存在。所以读的是整个目录，不是一份写死的文件清单 —— 清单的下场是新加的指引
    文件**不参与版本**，于是「指引换了、版本没动」，正是这套机制唯一要防的失败。
    同理**没有排除模式**（``.DS_Store`` / ``*~`` / ``__pycache__``）：排除表的代价
    是某天排除掉一个真的指引文件，而那一天没有任何信号；不排除的代价只是版本多动
    几次（一次假的「版本变了」）。前者不可见，后者可见 —— 选后者。

    读目录的活不在这里（本模块不碰文件系统，见文件头），在
    ``marketops/adapters/profile_loader.py::read_scene_files``。
    """

    entries: tuple[SceneFile, ...]

    def __post_init__(self) -> None:
        if not self.entries:
            raise ValueError(
                "SceneFiles 不能是空的。\n"
                "一个零文件的 Scene 目录算不出「这份规则是哪一个版本」—— 而一个算得出来"
                "的版本号会让一次**什么都没读到**的装载看起来像一次成功的装载。"
            )
        paths = [entry.path for entry in self.entries]
        if paths != sorted(paths):
            raise ValueError(
                "SceneFiles.entries 必须按 path 升序。\n"
                "目录遍历顺序由文件系统给，不保证稳定 —— 不排序的版本会在没有任何改动时"
                f"变来变去。收到的是 {paths!r}。"
            )
        if len(set(paths)) != len(paths):
            counts: dict[str, int] = {}
            for path in paths:
                counts[path] = counts.get(path, 0) + 1
            duplicated = sorted(path for path, count in counts.items() if count > 1)
            raise ValueError(
                f"SceneFiles.entries 里有重复 path：{duplicated!r}。\n"
                "同一个文件被算进版本两次，版本仍然稳定、仍然唯一，只是它描述的不再是"
                "这个目录的样子。"
            )
        for entry in self.entries:
            if not entry.path:
                raise ValueError("SceneFiles 里有一个空 path。")
            if entry.path.startswith("/") or ".." in entry.path.split("/"):
                raise ValueError(
                    f"SceneFiles 的 path 必须是**目录内**的相对路径，收到 {entry.path!r}。\n"
                    "绝对路径会把「这个 Scene 装在哪台机器上」写进一份本该可移植的规则里；"
                    "`..` 会让版本描述到目录外面去。"
                )

    @property
    def version(self) -> str:
        """这批字节的版本 —— **本仓算 scene_version 的唯一一处**。

        ## 逐文件先散一次，不把内容拼成一个大串

        文件名可以含换行，所以「名字 + 内容拼起来再散」是有歧义的（见
        :data:`VERSION_SEP`）。先算**定长**的每文件摘要、再拼这些定长块，
        歧义在结构上就不存在。

        ## 散的是字节，不是读出来的规则值

        YAML 里多个 BOM、缩进从两格改成四格，规则值逐位不变而版本会换。这是选
        「字节」的已知代价，方向安全：多一次假的「版本变了」，而不是少一次真的。
        反过来「解码后的值」也不是更好的口径 —— 那时版本描述的是一份被解释过的
        东西，而真正被部署的是盘上的字节。

        ## 装载器 import 它，不重写它

        两处各算一遍，漂移的那天会以「盘上钉的版本与手里的内容算出的版本不相等」
        的形式出现，而那时没有任何依据判断哪一边是对的。
        """
        entries: list[bytes] = []
        for entry in self.entries:
            digest = hashlib.sha256()
            digest.update(entry.path.encode("utf-8"))
            digest.update(VERSION_SEP)
            digest.update(entry.content)
            entries.append(digest.digest())
        return hashlib.sha256(b"".join(entries)).hexdigest()

    def as_mapping(self) -> dict[str, bytes]:
        """``path → bytes``。读的那一侧要按名字找文件时用。

        返回的是新字典：``entries`` 是元组，调用方改不动它，但让两次调用拿到同一个
        可变对象仍然是一处可以凭空改掉内容的入口。
        """
        return {entry.path: entry.content for entry in self.entries}


# --- bundle -----------------------------------------------------------------


@dataclass(frozen=True)
class ProfileBundle:
    """一个 profile 的完整业务规则：路线等级 + 账号准入 + 角色词表。

    **不含 profile 目录路径。** 它曾经带一个 ``profile_dir: Path``，而全仓库
    唯一读它的是一个测试。那个字段是纯规则层依赖 ``pathlib`` 的唯一理由，
    也是 ``import marketops.domain.profile`` 会拉进文件系统概念的入口。
    目录属于装载器的输入（``Settings.profile_dir()``），不属于规则。

    但 ``scene_version`` **是**规则的一部分：它不指向文件在哪，而是「你现在手里
    这份规则是哪一个版本」。前者是路径，后者是内容的性质 —— 一份能算出摘要的
    内容，本来就该带着自己的摘要走。
    """

    profile_id: str
    #: SOP 的 profile.yaml **不声明**生命周期状态（没有 DRAFT 之类标记）。
    #: 本模块不替它发明一个 —— 缺省就是 ``None``。
    status: str | None
    rules: dict[Grade, GradingRule]
    role_terms: tuple[str, ...]
    #: 装载**这一份** bundle 时，profile 目录里每个文件的字节算出的版本。
    #:
    #: 它不是 profile id，也不是 SOP 的版本号：目录里任何一个字节变了它就变，
    #: **包括今天还不存在、将来才会加进去的指引文件**（``docs/SCENE_EVOLUTION.md``
    #: 的 ``skill/SKILL.md``）。这正是要它做的事 —— 「生效的指引变了、版本却没动」
    #: 是这套机制唯一要防的失败。
    #:
    #: **没有缺省值。** 一个造出来却没人填的版本会读成空串，而空串与任何真版本
    #: 都不相等 —— 于是一次「钉住的版本 ≠ 当前版本」的告警会因为没人填而**恒响**，
    #: 而恒响的告警与恒不响的告警是同一类东西：没人再看它。
    scene_version: str
    #: **算出 ``scene_version`` 的那批字节本身。** 不是「顺便带上」——它是那个版本
    #: 号唯一的指称对象，见 :class:`SceneFiles` 的 docstring。
    #:
    #: 没有缺省值：一个造出来却没人填的 ``SceneFiles`` 只能是一个空目录，而空目录
    #: 算不出任何版本 —— 于是「这份 bundle 的版本」与「这份 bundle 的内容」之间的
    #: 对应关系从构造那一刻起就是假的。合成 bundle 的测试要显式给出这批字节
    #: （``SceneFiles`` 会拒绝空 entries），这不是摩擦，是那条对应关系的代价。
    scene_files: SceneFiles
    unknowns: tuple[str, ...] = ()
    declared: DeclaredGrading | None = None
    role_matrix: DeclaredRoleMatrix | None = None
    profile: DeclaredProfile | None = None
    #: 「同一个工作区里不同场景先看什么」那一份声明（``scenario-matrix.yaml``）。
    #: ``None`` = 这份 profile 还没有场景块，而那**不是**错误：条件 2 要的是
    #: 有一个**按场景分块**的入口，而不是逼每一份声明今天就分块。缺的那一侧在
    #: 编译时按整份声明写那四格，理由里写明「声明里没有这一块」。
    scenario_matrix: DeclaredScenarioMatrix | None = None
    #: 决策题面（``profile.yaml`` 的 ``decisions`` 块）。``None`` = 这个 profile
    #: 不做决策，而那不是错误 —— 一个 Scene 可以先只有规则、后有判定。缺的那一侧
    #: 在**调用时**显式失败（``application/decision_specs.compile_spec`` 抛
    #: ``ConfigError``），不在这里造一个空题面糊过去。
    decisions: DeclaredDecisions | None = None
    #: 判定新鲜度的时限（``profile.yaml`` 的 ``freshness`` 块）。``None`` = 这个
    #: profile 没声明时限，而那不是错误 —— 一份 profile 可以先只有规则、后有时限。
    #: 缺的那一侧在**调用时**显式失败（``application/freshness.py::policy_of`` 抛
    #: ``ConfigError``），不在这里造一组看起来合理的缺省天数顶上：那些数字没有
    #: 一个来自获批策略，而它们一旦作为缺省落在这里，每一行产出的 ``currentness``
    #: 都会带着一个**没人认过**的口径，且报告上完全看不出来（见
    #: :class:`~marketops.domain.temporal.FreshnessPolicy` 的 ``calibrated``）。
    freshness: FreshnessPolicy | None = None
    #: 账号准入规则。与 ``rules`` 是两个轴，见文件头。
    account_rules: tuple[AccountRule, ...] = ()
    #: 角色词 → 它在路线里扮的角色 / 业务口径。由 role matrix 编译而来。
    role_classes: dict[str, RoleClass] = field(default_factory=dict)
    business_functions: dict[str, BusinessFunction] = field(default_factory=dict)
    #: 不得进入有效路线的角色词（SOP v1 L129/L131）。
    excluded_terms: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        """``scene_version`` 必须**是** ``scene_files`` 的版本。

        ## 这条判据证明什么、不证明什么

        它证明的是**两者一致**：一个 bundle 不能一边声称版本是 A、一边拿着算出 B
        的字节。它**不**证明两者来自同一次读 —— 那件事没有类型能表达，只能由
        ``read_scene_files`` 一次遍历同时产出两样来保证（见
        ``marketops/adapters/profile_loader.py``）。判据的边界写在这里，是因为
        「一致」很容易被读成「同源」，而那是更强的一句话。

        一次读、一次散的分工仍然成立：``scene_version`` 是缓存下来的那个结果，
        这里核对它与字节算出来的相等。核对要重算一次摘要，代价是每个 bundle 一次
        sha256 —— 而它换掉的是一个只在被违反时才可见的不变量。

        ## 为什么不让它当默认值

        ``scene_version`` 曾经可能是 ``""``（没人填），而 ``""`` 与任何真版本都
        不相等 —— 于是「钉住的版本 ≠ 当前版本」会因为没人填而**恒响**。现在它不可能
        是任何一个真版本之外的串：写错就构造不出来，构造得出来就一定是这一份。
        """
        if self.scene_version != self.scene_files.version:
            raise ValueError(
                f"ProfileBundle 的 scene_version 与 scene_files 算出来的不一致："
                f"声称 {self.scene_version!r}，字节算出 {self.scene_files.version!r}。\n"
                "版本号唯一的指称对象就是这批字节；两者不一致时，盘上钉的那个版本"
                "描述的是一份谁也没读过的规则。"
            )

    def rule_for(self, grade: Grade) -> GradingRule | None:
        return self.rules.get(grade)

    @property
    def has_unknown_rules(self) -> bool:
        return bool(self.unknowns)

    @property
    def default_excluded_role_class(self) -> RoleClass:
        """未命中任何词表时角色的默认分类。中性值 —— 不是业务决定。"""
        return RoleClass.UNKNOWN

    def summary(self) -> dict[str, object]:
        return {
            "profile_id": self.profile_id,
            "status": self.status,
            "grades_defined": sorted(g.value for g, r in self.rules.items() if not r.is_unknown),
            "grades_unknown": sorted(g.value for g, r in self.rules.items() if r.is_unknown),
            "route_grade_subject": self.declared.subject if self.declared else None,
            "account_rules_total": len(self.account_rules),
            "account_rules_unchecked": [
                rule.id for rule in self.account_rules if rule.requires is None
            ],
            "role_terms_count": len(self.role_terms),
            "excluded_terms_count": len(self.excluded_terms),
            "unknowns": list(self.unknowns),
            # 「这一份 bundle 有没有可用的时限」是装载期报告要回答的问题之一。
            # 没声明时是 ``None``（不是一组假的天数）；声明了就把**标定状态与天数
            # 一起**报出来 —— 只报天数会让「有人批准过的」与「我们随手写的」
            # 在报告上逐字相同，而这两件事的处置完全不同。
            "freshness": (
                None
                if self.freshness is None
                else {
                    "policy_version": self.freshness.policy_version,
                    "current_within_days": self.freshness.current_within_days,
                    "historical_after_days": self.freshness.historical_after_days,
                    "calibrated": self.freshness.calibrated,
                }
            ),
        }


__all__ = [
    "AccountRule",
    "DeclaredDecisions",
    "DeclaredGrade",
    "DeclaredGrading",
    "DeclaredProfile",
    "DeclaredRoleGroup",
    "DeclaredRoleMatrix",
    "DeclaredTaskSpec",
    "GradingRule",
    "ProfileBundle",
    "SceneFile",
    "SceneFiles",
    "VERSION_SEP",
    "as_text",
]
