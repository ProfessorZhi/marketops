"""B16 的**输入面**：一份「要研究什么」的声明，以及从它推出来的那两样东西。

执行计划 §B16 的 ``ResearchContext`` 与 ``SceneFingerprint`` 住在这里，**编译产物
不在这里**（那是 ``StrategyCompiler`` 与 ``EffectiveResearchPlan`` 的事）。分开
是因为这个文件要回答的问题只有一个：**这份声明讲清楚了吗，讲清楚的话它长什么样。**

## 这个模块不调用任何东西

没有 provider、没有端口、没有 I/O。B16 关闭条件 1 要求「preview 零 provider 调用」，
而这里让它成立的方式不是一条「preview 不许调用外部的」的纪律 —— 是这一层**没有
可以调用的东西**。一个需要注入检索端口才能算出摘要的编译器，会在接线的那一天由
「谁忘了传端口」决定 preview 花不花钱；而一个连端口都收不到的编译器花不了钱。

## 两个方向相反的失败，各有各的落点

* **「说不清楚」→** :class:`Ambiguity`。硬的那一半（会改变目标人口 / 硬排除 /
  商业关系 / 产品权限）生成 ReviewCase，软的那一半进一份保守计划并把候选记下来。
  它**不是** ``ConfigError``：一份讲不清的输入是正常的业务情形，不是配置写错了。
* **「说清楚了，但超出已批准范围」→** :class:`OutOfEnvelope`。这是治理问题，
  不是「再问一次用户」能解决的 —— 用户再说一遍也不会让 ``FR`` 落进一份 ``GB``
  的 profile 里。

两者都会被调用方读成「这一次没能编译出计划」，但它们的**去向不同**：前者要开一张
待办给人答，后者只能改治理（换 profile / 换 Scene / 改批准范围）。合并成一个
「编译失败」会让「等一个人回答」与「等一次治理变更」在报告上同形。

## 为什么歧义按**维度**分类，而不是按记录分类

「这条歧义要不要停下来问人」是一条**按维度定死**的规则（:func:`ambiguity_is_hard`），
不是每条记录自己带一个 ``is_hard``。让记录自带等级，就会有一个入口可以把一条
「换个国家」的歧义标成低风险 —— 而那条歧义改的正是目标人口。
"""

from __future__ import annotations

from enum import Enum

from pydantic import Field, ValidationInfo, field_validator, model_validator

from .base import StrictModel
from .profile import ProfileBundle
from .util import (
    SEP,
    check_market_country,
    check_market_language,
    primary_language_subtag,
    sha256_text,
)


class ContextDimension(str, Enum):
    """一份研究声明能讲清楚的那几件事 —— **闭集**。

    闭集在这里是承重的：歧义与硬约束都按这一格分类，而「这一类歧义要不要停下来
    问人」是一条按这一格查表的规则。开放集合（任意字符串）会让「换一个维度名就
    让一条硬歧义掉进没人处理的分支」在类型上成立，而且不会红。

    成员是**计划正文逐个点名**的那几个：geo / jurisdiction / candidate locales /
    entity / asset / organization classes / scenario candidates / objective /
    optional product use / evidence requirements。少一个成员会让那份声明里真的有
    一格无处归类，多一个成员会让「这一维没人判」看起来像「这一维不需要判」。
    """

    GEO = "geo"
    JURISDICTION = "jurisdiction"
    LOCALES = "locales"
    ENTITY_CLASSES = "entity_classes"
    ASSET_CLASSES = "asset_classes"
    ORGANIZATION_CLASSES = "organization_classes"
    SCENARIO = "scenario"
    OBJECTIVE = "objective"
    PRODUCT_USE = "product_use"
    EVIDENCE_REQUIREMENTS = "evidence_requirements"


#: 歧义落在这几维上，**必须停下来问人**。
#:
#: 判据是「这一维变了，这次研究还是在为同一批人做同一件事吗」：
#:
#: * ``GEO`` / ``JURISDICTION`` / ``ENTITY_CLASSES`` / ``ASSET_CLASSES`` /
#:   ``ORGANIZATION_CLASSES`` —— 换掉任何一个都是**换目标人口**。
#: * ``SCENARIO`` —— 换掉就是换「这一页在说什么」的读法，而来源方法与责任模型都
#:   挂在它上面（§B16 的两个例子：K12 与大学、场地与维护商）。
#: * ``OBJECTIVE`` / ``PRODUCT_USE`` —— 换掉就是换商业关系与产品 claim，而
#:   §B16 逐字要求「Agent/Jev 不可自行改产品 claim」。
#:
#: 这一组**不看置信度**。一条 99% 置信度的模型猜测与一条 1% 的在这几维上同等
#: 不可用：置信度说的是「模型有多确定」，而这几位问的是「谁授权了这件事」。
_HARD_AMBIGUITY_DIMENSIONS: frozenset[ContextDimension] = frozenset(
    {
        ContextDimension.GEO,
        ContextDimension.JURISDICTION,
        ContextDimension.ENTITY_CLASSES,
        ContextDimension.ASSET_CLASSES,
        ContextDimension.ORGANIZATION_CLASSES,
        ContextDimension.SCENARIO,
        ContextDimension.OBJECTIVE,
        ContextDimension.PRODUCT_USE,
    }
)

#: 歧义落在这几维上，可以**在已批准的 envelope 里编译一份保守计划**并把候选记下来。
#:
#: * ``LOCALES`` —— §B16 逐字：「页面级语言检测只影响查询/解析/翻译策略」。它换的是
#:   去问哪种语言的索引，不是问谁。保守的取法是**并集**（每种已声明语言各问一遍，
#:   各记各的账），而并集只会多花已批准的额度，不会漏掉一个可能的答案。
#: * ``EVIDENCE_REQUIREMENTS`` —— 换的是「什么算数」而不是「研究谁」。保守的取法
#:   同样是并集（按最严的那一份要求收证据），而多收证据不改变任何人的资格。
_SOFT_AMBIGUITY_DIMENSIONS: frozenset[ContextDimension] = frozenset(
    {
        ContextDimension.LOCALES,
        ContextDimension.EVIDENCE_REQUIREMENTS,
    }
)


def ambiguity_is_hard(dimension: ContextDimension) -> bool:
    """这一维上的歧义要不要停下来问人。

    查表，不看置信度、不看候选、不看调用方。这两张表的并集必须恰好是
    :class:`ContextDimension` 的全部成员、且互不相交 —— 有执行器
    （``tests/test_research_context.py``）。少了这条执行器，加一个维度成员而忘了
    归类时，``ambiguity_is_hard`` 会把它算成软的（因为两张表都没说它是硬的），
    于是一条「换国家」的歧义会静默地走进保守计划。
    """
    return dimension in _HARD_AMBIGUITY_DIMENSIONS


class ScenarioProvenance(str, Enum):
    """一条场景候选**是从哪来的** —— 它决定这条候选能不能定下场景这一格。

    §B16 的输入段逐字：「共享池已有事实可作为上下文，但历史模型猜测不能自动
    扩大范围」。这一格就是那句话的类型化：``MODEL_GUESS`` 的候选**记下来**，
    但不定场景 —— 于是「模型猜的」不会变成一次自动扩大的研究范围。
    """

    #: Member-A/客户端在这份声明里明确给出的。
    DECLARED = "declared"
    #: 共享池里已经有的一条事实（它有出处，可以被读回来）。
    POOL_FACT = "pool_fact"
    #: 历史模型猜测。**不作为定场景的依据**，只作为待确认的候选记下来。
    MODEL_GUESS = "model_guess"


#: 哪些来路**算数**。``MODEL_GUESS`` 不在里面，理由见 :class:`ScenarioProvenance`。
_CREDIBLE_PROVENANCE: frozenset[ScenarioProvenance] = frozenset(
    {ScenarioProvenance.DECLARED, ScenarioProvenance.POOL_FACT}
)


class ScenarioCandidate(StrictModel):
    """一条「这次研究是冲着什么场景去的」候选。

    ``reading`` 与 ``name`` 是**两格**，不是同义反复：

    * ``name`` 是这条候选**自己**的说法（客户端给的措辞、池子里那条事实的措辞）。
    * ``reading`` 是它落到**这个 profile 的词表上**是哪一个值。

    两条名字不同、读法相同的候选（``"secondary school"`` 与 ``"high school"`` 都
    读成同一个值）**不构成歧义** —— 它们是同一个读法的两种说法。反过来，两个读法
    不同但名字很像的候选是真歧义。把两者合成一格时，「同一件事的两种说法」会看
    起来像「两件不同的事」，而拒绝编译的理由会指向一个其实不冲突的输入。

    ``confidence`` **只记录**，不参与「要不要停下来问人」的判定。它在这里是因为
    §B16 的输入段点名列了它，而且它对**软**那一半有用（保守计划要按置信度排序把
    候选记下来）。硬那一半读都不读它，见 :func:`ambiguity_is_hard`。
    """

    name: str
    reading: str
    provenance: ScenarioProvenance
    #: 这句话是谁说的 / 哪条事实 —— **必填非空**。
    #:
    #: 一条说不出出处的候选与一条模型猜的候选在回执上长得一样，而它们的处置
    #: 不同（前者要去找那个人补出处，后者是正常的「未确认」）。必填是为了让
    #: 「不知道哪来的」在代码里写不出来。
    source: str
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)

    @field_validator("name", "reading", "source")
    @classmethod
    def _not_blank(cls, value: str, info: ValidationInfo) -> str:
        if not value.strip():
            raise ValueError(
                f"ScenarioCandidate.{info.field_name} 不能为空。\n"
                "``name``/``reading`` 空着会让这条候选在报告里读成「有一条候选，"
                "内容没写」；``source`` 空着会让它读成一条正常的候选 —— 而"
                "「谁说的」正是它能不能定下场景那一格的依据。"
            )
        return value


class Ambiguity(StrictModel):
    """这份声明里**有一格说不清楚**。

    它只有两格：哪一维、以及说不清的是什么。**没有等级格、没有置信度格** ——
    等级由维度推出来（:func:`ambiguity_is_hard`），让记录自带等级就有了一处
    可以把硬歧义标软的地方。
    """

    dimension: ContextDimension
    detail: str

    @field_validator("detail")
    @classmethod
    def _detail_not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError(
                "Ambiguity.detail 不能为空。\n"
                "一条说不出「哪里说不清」的歧义在待办里读成「输入有点模糊，请确认」"
                "—— 而人要答的那个问题是具体的：换哪一国、哪一批主体、哪个目标。"
            )
        return value


#: 硬约束**只能**落在这一维上 —— 也就是 envelope 真的答得上来的那几维。
#:
#: 一份硬约束说的是「这一格被限定为这几个值」，而一个约束只有在**有人能判它
#: 越界了没有**的时候才有意义。envelope 今天持有 geo / jurisdiction / locales
#: 三格（见 :class:`ResearchEnvelope`），所以能判的只有这三格。
#:
#: 场景与主体类别**不在这里**，而那不是漏了：客户端说「只看 K12」走的是
#: ``scenario_candidates`` 与 ``entity_classes``，它们定义的是目标人口，被
#: envelope 的排除清单约束 —— 收进 ``HardConstraint`` 会造出第二个「这一格被
#: 限定为这几个值」的说法，而两份说法不一致时没有一处会红。
_ENFORCEABLE_DIMENSIONS: frozenset[ContextDimension] = frozenset(
    {ContextDimension.GEO, ContextDimension.JURISDICTION, ContextDimension.LOCALES}
)


class HardConstraint(StrictModel):
    """客户端对某一维给出的**硬边界**：这一格只能是这几个值。

    「硬」的意思是：越界不是缩小范围，而是**拒绝编译**。悄悄把超出的那部分切掉，
    会让一次「照你批准的跑」与一次「只跑了你批准的那部分」在回执上同形 —— 而
    后者少掉的那些对象不会出现在任何一份差集里。
    """

    dimension: ContextDimension
    values: tuple[str, ...]

    @field_validator("values")
    @classmethod
    def _values_not_empty(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if not value:
            raise ValueError(
                "HardConstraint.values 不能为空。\n"
                "空集的意思是「这一格一个值都不许」，那是一份**无法编译**的声明，"
                "而它与「没写这一条约束」在调用点长得一样。要表达「不研究任何"
                "对象」请别提交这份声明。"
            )
        return value

    @model_validator(mode="after")
    def _the_dimension_is_one_the_envelope_can_answer(self) -> HardConstraint:
        if self.dimension not in _ENFORCEABLE_DIMENSIONS:
            raise ValueError(
                f"HardConstraint.dimension 不能是 {self.dimension.value!r}："
                "envelope 答不上来这一维。\n"
                f"能判越界的只有 {'、'.join(sorted(d.value for d in _ENFORCEABLE_DIMENSIONS))}"
                " —— 一份没人能判的约束是一条**无论如何都会被遵守**的约束，"
                "而它给人的印象是「我说了就有用」。"
            )
        return self


class ResearchContext(StrictModel):
    """一次研究**要什么** —— B16 的输入，由 Member-A/客户端给出。

    这个名字下装的是**请求**，不是批准。批准那一半是 :class:`ResearchEnvelope`
    （从 profile 与 Scene 读出来的），两者的关系是「请求必须落在批准范围内」，
    而不是「后者由前者推出来」。

    ## 哪几格空着会变成一条硬歧义

    ``geo`` / ``entity_classes`` / ``asset_classes`` / ``organization_classes`` /
    ``objective`` 空着都**不是**「不限」，是「没说」。把它们读成「不限」正是
    §B16 要防的那件事（「历史模型猜测不能自动扩大范围」）：一份只写了 objective
    的声明会编译出一次「全英国所有组织」的研究，而回执上看起来一切正常。

    ## 哪几格空着是正常的

    * ``jurisdiction`` —— 空着 = 与 ``geo`` 同管辖。这一格只在两者**真的不同**
      （例如研究英国主体、但按另一辖区的合规口径收证据）时才写。
    * ``candidate_locales`` —— 空着 = 用 profile 声明的那几种语言。它不是「不限
      语言」：这一格的语义是**收窄** profile 的声明，收窄成空集没有意义，所以
      空着退回声明本身，而计划里的语言族从声明来。
    * ``product_use`` —— §B16 逐字说它是可选的。空着 = 这次研究不挂产品用途，
      编译出来的计划也就没有产品 overlay，而不是有一个默认 overlay。
    * ``scenario_candidates`` —— 空着会变成一条硬歧义（场景这一维没人定），
      不是「什么场景都行」。

    ## ``declared_ambiguities`` 与推出来的那几条是**并集**

    客户端可以自己声明一条已知的模糊（「我不确定该算 K12 还是 college」），
    :func:`ambiguities_of` 把声明的与推出来的并起来。分开命名是为了让读代码的人
    一眼看得出哪几条是**输入**、哪几条是**推导** —— 一个叫 ``ambiguities`` 的字段
    配一个叫 ``ambiguities_of`` 的函数会让人以为后者是前者的计算属性。
    """

    #: 问哪一个国家的索引。大写两位 ISO-3166 —— 与 ``SearchQuery.country`` 同一格量具。
    geo: str = ""
    #: 空着 = 与 ``geo`` 同管辖（见类 docstring）。
    jurisdiction: str = ""
    #: BCP-47 标签（``en-GB`` / ``fr-CA``），**可以多条**。见类 docstring。
    candidate_locales: tuple[str, ...] = ()
    entity_classes: tuple[str, ...] = ()
    asset_classes: tuple[str, ...] = ()
    organization_classes: tuple[str, ...] = ()
    scenario_candidates: tuple[ScenarioCandidate, ...] = ()
    #: 这次研究的目标 —— §B16 输入段里的「sales/research objective」。
    objective: str = ""
    #: 可选的 Product/Offer/use-case。空着 = 这次不挂产品用途。
    product_use: str = ""
    evidence_requirements: tuple[str, ...] = ()
    #: 客户端**自己**声明已知的模糊。推出来的那几条不在这里（见类 docstring）。
    declared_ambiguities: tuple[Ambiguity, ...] = ()
    hard_constraints: tuple[HardConstraint, ...] = ()

    @field_validator("geo")
    @classmethod
    def _geo_shape(cls, value: str) -> str:
        if not value:
            # 空着是合法的输入（它随后变成一条硬歧义）。但**非空**时必须过形状
            # 判据 —— ``check_market_country`` 与 ``SearchQuery.country`` 用的是
            # 同一个量具，两个轴在这里就分开了。
            return value
        check_market_country(value, "ResearchContext.geo")
        return value

    @field_validator("jurisdiction")
    @classmethod
    def _jurisdiction_shape(cls, value: str) -> str:
        if not value:
            return value
        check_market_country(value, "ResearchContext.jurisdiction")
        return value

    @field_validator("candidate_locales")
    @classmethod
    def _locales_carry_a_usable_language(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        for tag in value:
            if not tag.strip():
                raise ValueError(
                    "ResearchContext.candidate_locales 里有一条空标签。\n"
                    "空标签会让「这一格声明了三种语言」与「声明了两种、外加一个"
                    "打字错误」在报告上同形。"
                )
            # 只判**主语言子标签**：这一层唯一读的就是它（它进 ``SearchQuery.language``）。
            # 地区部分不在这里判，因为这一层不读它 —— 声称「校验了整条 BCP-47 标签」
            # 是一句没有人执行的话，而写下来只会让下一个读的人以为它被校验过。
            check_market_language(
                primary_language_subtag(tag), f"ResearchContext.candidate_locales[{tag!r}]"
            )
        return value


class ResearchEnvelope(StrictModel):
    """**已批准的范围** —— 请求必须落在它里面。

    它由 profile（与 Scene）读出来，不由请求推出来。四格各有各的持有者：

    ==================  ==========================================================
    ``geo``             ``profile.yaml`` 的 ``country`` —— 这个部署问哪个市场。
    ``jurisdictions``   今天 = ``{geo}``。profile 还没有独立的管辖声明，而**编一个**
                        会比空着更坏：一份声称「管辖就是本国」的默认值，会让一次
                        按另一辖区收证据的研究看起来正常通过。
    ``locales``         ``profile.yaml`` 的 ``search.locales``（BCP-47 原文）。
    ``exclusions``      profile 的排除清单**逐条 id**（``declared.exclusion_ids``）。
    ==================  ==========================================================

    ``locales`` 存的是**原始标签**而不是主语言子标签：收窄那一侧
    （:attr:`ResearchContext.candidate_locales`）也是原始标签，两侧同一格量具才
    比得起来。把它提前降成主语言，``en-GB`` 与 ``en-US`` 就会在这里合成一格，
    于是「只要英式英语的来源」这条收窄会静默地变成「英语都行」。
    """

    geo: str
    jurisdictions: tuple[str, ...] = ()
    locales: tuple[str, ...] = ()
    exclusions: tuple[str, ...] = ()


def effective_jurisdiction(context: ResearchContext) -> str:
    """这一次研究按哪个辖区收证据：``jurisdiction`` 空着时与 ``geo`` 同管辖。

    「空着 = 同管辖」是一条**回退**，不是一条默认值，所以它必须只有一处实现。
    三处读者依赖它 —— :func:`constraints_out_of_envelope`（越界判定）、
    :func:`fingerprint_of`（摘要）、以及 B16 的计划层（``research_plan.py`` 把
    它抄进计划的 ``jurisdiction`` 那一格）—— 各处各写一份的话，同一条声明会因为
    客人今天写没写那一格而得到两个值，而两个值都会各自看起来是对的。

    这一条有判据（``tests/test_research_context.py`` 里那条「空管辖与写明 geo
    是同一份声明」），它守的就是这个函数：变异掉这一行回退，那条判据转红。
    """
    return context.jurisdiction or context.geo


def envelope_of(bundle: ProfileBundle) -> ResearchEnvelope:
    """从**冻结的 profile** 读出这份部署已批准的范围。

    读不到就留空，不猜：``bundle.profile`` 缺失时 :class:`ResearchEnvelope` 的
    四格会有三个空着，而 ``geo`` 缺不了 —— 一个连国家都说不出的 profile 不能
    当 envelope 用。所以缺声明块时抛，点名的正是「这份 profile 说不出它批准了
    什么范围」。
    """
    declared = bundle.profile
    if declared is None:
        raise ValueError(
            f"profile {bundle.profile_id!r} 没有声明块，因此它说不出批准了什么范围。\n"
            "envelope 的 geo 从 ``profile.yaml`` 的 ``country`` 来，而它没有缺省值 ——"
            "一个回退到某国的默认值会让「这份 profile 没写国家」与「这份 profile"
            "就是这个国家」在编译结果上同形。"
        )
    country = declared.country.strip()
    if not country:
        raise ValueError(
            f"profile {bundle.profile_id!r} 的 country 是空的，因此它批准的范围"
            "没有地理边界。\n"
            "空着与「全世界」不是同一件事：把它读成后者会让一份写坏了的 profile"
            "批准最大范围的研究。"
        )
    check_market_country(country, "profile.country")
    return ResearchEnvelope(
        geo=country,
        jurisdictions=(country,),
        locales=tuple(declared.search_locales),
        exclusions=tuple(declared.exclusion_ids),
    )


class OutOfEnvelope(StrictModel):
    """请求超出了已批准范围 —— **拒绝编译**，且不是「问一次人」能解决的。

    ``requested`` / ``allowed`` 两格是机器可读的差集依据，不是给人看的散文：
    「哪几个值越界了」要能被逐条念出来，否则回执只能写成「范围不符」，而那是
    一句读的人无法据以行动的话。
    """

    dimension: ContextDimension
    requested: tuple[str, ...]
    allowed: tuple[str, ...]
    detail: str


def constraints_out_of_envelope(
    context: ResearchContext, envelope: ResearchEnvelope
) -> tuple[OutOfEnvelope, ...]:
    """找出请求里**越界**的那几处。

    四类，各有各的回执：

    1. ``geo`` 不在 envelope 的地理里 —— 换国家是 PolicyPatch，不能由一次请求
       静默做到。
    2. ``jurisdiction`` **写出来了**、且不在 envelope 的管辖里。空着不算越界：
       它跟着 ``geo`` 走，而 ``geo`` 那一条已经判过了 —— 同一个值报两次会让回执
       上有两条同源的话，读的人以为要改两处。
    3. ``candidate_locales`` 里有 envelope 没声明过的标签 —— 收窄只能收到已声明
       的语言上。**注意方向**：这里判的是「请求里多出来的」，不是「请求没覆盖
       envelope 的全部」。少要几种语言是收窄，是合法的。
    4. 请求点名的场景读法 / 主体类别与 envelope 的排除清单**逐字相等**。

    第 4 条只做**逐字相等**，不做子串匹配。理由是这份 profile 的排除清单本身就是
    一组互不为子串的 id：``golf`` 与 ``footgolf`` 是两条不同的排除，``golf`` 与
    ``artificial_grass`` 也是。子串匹配会把 ``footgolf`` 判成 ``golf``
    （于是回执点名的是一条没被请求的排除），也会让 ``golf club`` 这种措辞悄悄
    擦过 ``golf``。要按措辞判，就得先有一张措辞→id 的词表，而那张表今天住在
    ``grading.yaml`` 里、服务于页面分类，不是为了这一处。

    一份**空** envelope（``jurisdictions``/``locales``/``exclusions`` 全空）不是
    「无限」，是「关于这几维一个字都没说」。第 3 条因此有一条「profile 没声明
    ``search.locales``」的分支，回执指向 profile 的作者而不是请求的人；第 4 条
    反过来 —— 没有排除清单就**没有可越的界**，缺的是禁令而不是依据，所以那一支
    不报。两条的差别是有意的，见下。

    第 1 条没有「envelope 说不出地理」这一支：``geo`` 在 :class:`ResearchEnvelope`
    里是必填的（:func:`envelope_of` 缺它就抛），所以它永远答得上这一维。
    """
    found: list[OutOfEnvelope] = []

    if context.geo not in envelope.jurisdictions and context.geo != envelope.geo:
        found.append(
            OutOfEnvelope(
                dimension=ContextDimension.GEO,
                requested=(context.geo,),
                allowed=(envelope.geo,),
                detail=(
                    f"请求问 {context.geo!r} 的索引，而这份 profile 批准的地理是 "
                    f"{envelope.geo!r}。\n"
                    "换一个国家不是一次收窄 —— 它是换目标人口，只能作为 PolicyPatch "
                    "走治理，不能由一次请求静默做到（§B16 的「新增国家只能生成 "
                    "ExperienceCandidate/PolicyPatch」）。"
                ),
            )
        )

    # 空着时它跟着 ``geo`` 走（见 :func:`effective_jurisdiction`），而那时 ``geo``
    # 那一条已经报过了。再报一次会让回执上有两条同源的话，读的人以为要改两处 ——
    # 而其中一处是同一个值的另一个写法。
    jurisdiction = effective_jurisdiction(context)
    if context.jurisdiction and jurisdiction not in envelope.jurisdictions:
        found.append(
            OutOfEnvelope(
                dimension=ContextDimension.JURISDICTION,
                requested=(jurisdiction,),
                allowed=envelope.jurisdictions,
                detail=(
                    f"请求的管辖是 {jurisdiction!r}，而这份 profile 声明的管辖是 "
                    f"{'、'.join(envelope.jurisdictions) or '（没有声明）'}。\n"
                    "管辖与索引国家是两格：按另一辖区的合规口径收证据是合法的，"
                    "但它要先被声明过 —— 否则一次「按别处规矩收的证据」会看起来"
                    "像一次正常的本次研究。"
                ),
            )
        )

    if context.candidate_locales:
        if not envelope.locales:
            found.append(
                OutOfEnvelope(
                    dimension=ContextDimension.LOCALES,
                    requested=context.candidate_locales,
                    allowed=(),
                    detail=(
                        f"请求收窄到语言 {'、'.join(context.candidate_locales)}，"
                        "而这份 profile 没有声明 search.locales。\n"
                        "收窄的依据必须是**已声明**的那几种语言 —— profile 没声明"
                        "时，谁也不能确认请求指定的那几种在范围内。"
                    ),
                )
            )
        else:
            extra = tuple(tag for tag in context.candidate_locales if tag not in envelope.locales)
            if extra:
                found.append(
                    OutOfEnvelope(
                        dimension=ContextDimension.LOCALES,
                        requested=extra,
                        allowed=envelope.locales,
                        detail=(
                            f"请求的语言里 {'、'.join(extra)} 不在 profile 声明的 "
                            f"{'、'.join(envelope.locales)} 里。\n"
                            "收窄只能收到已声明的语言上；多出来的那几种是**新增**，"
                            "而新增语言意味着新增计费调用与新增来源，属于治理。"
                        ),
                    )
                )

    claimed = _claimed_ids(context)
    if claimed:
        if not envelope.exclusions:
            # 没有排除清单时**不报**：一份「什么都没排除」的 profile 不构成越界。
            # 这与上面两条不同 —— 上面两条里 envelope 缺的是**收窄的依据**，而
            # 这里缺的是**禁令**，没有禁令就没有可越的界。
            pass
        else:
            hit = tuple(sorted(set(claimed) & set(envelope.exclusions)))
            if hit:
                found.append(
                    OutOfEnvelope(
                        dimension=ContextDimension.SCENARIO,
                        requested=hit,
                        allowed=tuple(sorted(envelope.exclusions)),
                        detail=(
                            f"请求点名了 {'、'.join(hit)}，而它在 profile 的排除清单里。\n"
                            "排除是硬边界（§B16 的「硬排除」）：一次请求不能把它"
                            "拿回来。要拿回来只能改 profile 或 Scene，而那两次改动"
                            "都会在 diff 里出现。"
                        ),
                    )
                )

    return tuple(found)


def credible_scenario_readings(context: ResearchContext) -> tuple[str, ...]:
    """可信候选读出来的那几种场景 —— **去重、保序**。

    去重是因为两条措辞不同、读法相同的候选（``"secondary school"`` 与
    ``"high school"`` 都读成同一个值）**不是**两种场景；把两者算成两条会让一次
    其实没冲突的输入看起来像冲突，而拒绝编译的理由会指向一个不存在的分歧。

    ``MODEL_GUESS`` 的候选在这里**一条都不算**（:data:`_CREDIBLE_PROVENANCE`）：
    让猜测定场景就是让一次研究范围自动扩大，那是 §B16 输入段逐字要挡的事。

    **这一句判据在本模块只有这一处定义。** :func:`ambiguities_of` 读它判「说
    不清」，:func:`effective_scenario` 读它判「是哪一条」。两处各写一遍的话，
    将来给可信集合加一个来路时只有一处会跟着变 —— 另一处不会报错，它只会继续
    按旧集合算。
    """
    return tuple(
        dict.fromkeys(
            candidate.reading
            for candidate in context.scenario_candidates
            if candidate.provenance in _CREDIBLE_PROVENANCE
        )
    )


def effective_scenario(context: ResearchContext) -> str:
    """这次研究是冲着哪一个场景去的。

    **恰好一条**可信读法才算答得出来。零条（含「只有模型猜的」）与两条以上各自
    是一条**硬**歧义（:func:`ambiguity_is_hard`），处置是停下来问人 —— 在这里挑
    一条继续跑，就是拿模型的把握当授权。

    与 :func:`fingerprint_of` 的关系：那个函数在硬歧义上先抛，所以它走到这里时
    「恰好一条」是结构上的保证。这一层仍然自己判一次，而不是假设调用方已经判过：
    §B16 的条件 2 要按这一格去选场景块，而「是哪一条」这句话从此有了一个能**单独**
    问的入口。
    """
    readings = credible_scenario_readings(context)
    if len(readings) != 1:
        listed = "、".join(readings) if readings else "一条都没有"
        raise ValueError(
            f"这份上下文有 {len(readings)} 种可信的场景读法（{listed}），"
            "因此说不出这次研究是冲着哪一个场景去的。\n"
            "场景决定来源方法与责任模型（§B16 条件 2 与条件 7），所以这里不能替"
            "调用方挑一条：先按 ``ambiguities_of`` 把那一条记出来问人。"
        )
    return readings[0]


def _claimed_ids(context: ResearchContext) -> tuple[str, ...]:
    """请求**逐字点名**过的那几个 id：场景读法 + 三类主体/资产/组织类别。

    用的是 ``reading`` 而不是 ``name``：``reading`` 是落到这个 profile 词表上的
    那一个值，而排除清单里的 id 也是词表上的值。拿 ``name`` 去比会让「客户端
    用了另一套措辞」这件事变成一次静默的擦过 —— 而措辞→词表的翻译恰恰是
    ``reading`` 那一格存在的理由。
    """
    values: list[str] = [candidate.reading for candidate in context.scenario_candidates]
    values.extend(context.entity_classes)
    values.extend(context.asset_classes)
    values.extend(context.organization_classes)
    return tuple(values)


def ambiguities_of(context: ResearchContext) -> tuple[Ambiguity, ...]:
    """这份声明里所有说不清的地方：**客户端声明的 ∪ 推出来的**。

    推出来的那几条逐维看：

    * ``GEO`` 空着 —— 说不出问哪一国。
    * ``ENTITY_CLASSES`` / ``ASSET_CLASSES`` / ``ORGANIZATION_CLASSES`` 空着 ——
      说不出研究哪一批主体。空着**不是**「不限」，见 :class:`ResearchContext`。
    * ``SCENARIO`` —— **可信**候选（:data:`_CREDIBLE_PROVENANCE`）的读法去重之后
      不是恰好一条。零条是「没人说」（含「只有模型猜的」，那是 §B16 逐字要挡的
      那一句）；两条以上是「有两种读法」。
    * ``OBJECTIVE`` 空着 —— 说不出这次研究的目标。
    * ``EVIDENCE_REQUIREMENTS`` 空着 —— 软的那一条：保守计划按最严的要求收证据，
      并把「这一格是推的」记下来。

    ``JURISDICTION`` / ``LOCALES`` / ``PRODUCT_USE`` **不产出**歧义，理由写在
    :class:`ResearchContext` 的类 docstring 里；``ASSET_CLASSES`` 与
    ``ORGANIZATION_CLASSES`` 与 ``ENTITY_CLASSES`` 判据相同，分三条而不是一条，
    是因为它们的回执要各指各的那一格。

    这个函数**不看置信度**。``scenario_candidates`` 里 ``confidence=1.0`` 的一条
    猜测候选与 ``confidence=0.0`` 的在可信集合外面同样地不算数。
    """
    found: list[Ambiguity] = list(context.declared_ambiguities)

    if not context.geo:
        found.append(
            Ambiguity(
                dimension=ContextDimension.GEO,
                detail="这份声明没有写 geo —— 说不出这次研究问的是哪一个国家的索引。",
            )
        )

    for dimension, values, label in (
        (ContextDimension.ENTITY_CLASSES, context.entity_classes, "entity_classes"),
        (ContextDimension.ASSET_CLASSES, context.asset_classes, "asset_classes"),
        (
            ContextDimension.ORGANIZATION_CLASSES,
            context.organization_classes,
            "organization_classes",
        ),
    ):
        if not values:
            found.append(
                Ambiguity(
                    dimension=dimension,
                    detail=(
                        f"这份声明没有写 {label} —— 说不出这次研究冲着哪一批对象去。\n"
                        "空着不读作「不限」：那会让一份只写了目标的声明编译成一次"
                        "「范围内所有对象」的研究，而回执上看起来一切正常。"
                    ),
                )
            )

    readings = credible_scenario_readings(context)
    if not readings:
        guesses = "、".join(c.name for c in context.scenario_candidates) or "一条都没有"
        found.append(
            Ambiguity(
                dimension=ContextDimension.SCENARIO,
                detail=(
                    f"这份声明没有一条**可信**的场景候选（{guesses}）。\n"
                    "可信的来路只有 declared 与 pool_fact 两种：历史模型猜测"
                    "（model_guess）会被记下来，但它**不定场景** —— 让猜测定场景"
                    "就是让一次研究范围自动扩大（§B16 输入段逐字）。"
                ),
            )
        )
    elif len(readings) > 1:
        found.append(
            Ambiguity(
                dimension=ContextDimension.SCENARIO,
                detail=(
                    f"可信的候选读出了 {len(readings)} 种场景（{'、'.join(readings)}）"
                    "—— 换场景就是换来源方法与责任模型。\n"
                    "置信度不参与这一条：读完这些候选的置信度也一样是两种读法，"
                    "而挑一个高的来定场景就是拿模型的把握当授权。"
                ),
            )
        )

    if not context.objective.strip():
        found.append(
            Ambiguity(
                dimension=ContextDimension.OBJECTIVE,
                detail=(
                    "这份声明没有写 objective —— 说不出这次研究要回答什么问题。\n"
                    "目标决定的是「什么算做完了」，而一次没有目标的编译出来的计划"
                    "只能把额度花到上限为止。"
                ),
            )
        )

    if not context.evidence_requirements:
        found.append(
            Ambiguity(
                dimension=ContextDimension.EVIDENCE_REQUIREMENTS,
                detail=(
                    "这份声明没有写 evidence_requirements —— 保守计划按最严的要求"
                    "收证据（并集），并把这一格是推出来的记在计划里。"
                ),
            )
        )

    return tuple(found)


class SceneFingerprint(StrictModel):
    """上下文**规范化之后**的那一串维度 —— §B16 逐字：「derived, not identity」。

    ## 为什么它不是身份

    它可读、可比较、有摘要，但没有一处能拿它当主键：没有 ``fingerprint_id``、
    没有创建时间、没有状态。理由是 §B16 那半句括号 —— 一份会被当成身份的指纹
    迟早会有人对着它建表、对着它去重、对着它做「同一个指纹就复用上次的计划」，
    而**同一份上下文在两个时刻编译出的计划不必相同**（策略模块的版本会变）。
    指纹说的是「这两份声明讲的是不是同一件事」，不是「这两次研究是同一件事」。

    ## 只能从一份**讲清楚了**的上下文里推出来

    :func:`fingerprint_of` 在存在**硬**歧义时拒绝。这是有意的：摘要要能当
    「这份输入长什么样」的凭据用，而一份连目标人口都说不清的输入没有那个样子 ——
    硬歧义的那几维在指纹里会变成空串，于是两份完全不同的模糊声明得到同一个摘要，
    而摘要看起来还是很正常。软歧义不拦（那正是「保守计划 + 记下候选」的场景）。

    ## 规范化是**排序 + 去重**，不碰大小写

    声明里把三类主体换了个顺序不该换来一个新摘要，所以两个集合格排序去重。
    大小写与空白**原样保留**：``School`` 与 ``school`` 在两个不同的声明里是两个人
    打的两串字，把它们归一会让「同一批对象」这句话在回执上比输入说得更满。

    ## 摘要怎么算

    逐格先散一次、再把**定长**的每格摘要求摘要 —— 与 ``SceneFiles.version`` 同一
    个形状（那边是逐文件先散、再拼定长块）。直接把各格拼成一个长串是有歧义的：
    ``entity=("a","b")`` 与 ``entity=("a"), asset=("b")`` 会拼成同一串，于是两份
    不同的声明得到同一个摘要，而摘要正是「这两份声明一样吗」的答案。
    """

    geo: str
    jurisdiction: str
    locales: tuple[str, ...]
    entity_classes: tuple[str, ...]
    asset_classes: tuple[str, ...]
    organization_classes: tuple[str, ...]
    #: 唯一那条可信候选的 ``reading``。
    scenario: str
    objective: str
    product_use: str
    evidence_requirements: tuple[str, ...]

    @property
    def digest(self) -> str:
        """这一串维度的内容摘要 —— 同一条推导规则，全仓只有这一处。"""
        blocks = [
            sha256_text(self.geo),
            sha256_text(self.jurisdiction),
            sha256_text(SEP.join(self.locales)),
            sha256_text(SEP.join(self.entity_classes)),
            sha256_text(SEP.join(self.asset_classes)),
            sha256_text(SEP.join(self.organization_classes)),
            sha256_text(self.scenario),
            sha256_text(self.objective),
            sha256_text(self.product_use),
            sha256_text(SEP.join(self.evidence_requirements)),
        ]
        return sha256_text(SEP.join(blocks))


def _normalised(values: tuple[str, ...]) -> tuple[str, ...]:
    """声明里的集合格：去重 + 排序。见 :class:`SceneFingerprint` 的 docstring。"""
    return tuple(sorted(dict.fromkeys(values)))


def fingerprint_of(context: ResearchContext) -> SceneFingerprint:
    """把一份上下文规范化成指纹。**存在硬歧义时拒绝。**

    ``jurisdiction`` 空着时取 ``geo`` —— 那条回退只有一处实现
    （:func:`effective_jurisdiction`），越界判定与本函数都从那里取。

    ``scenario`` 取唯一那条可信读法。到这一步它必然唯一：两种读法会在
    :func:`ambiguities_of` 里落成一条**硬**歧义，而上面那道门已经拦住了。
    零条同理。所以这里不需要再判一次 —— 再判一次就等于承认那道门可能漏，
    而漏了的话静默取第一条正是最坏的处置。
    """
    hard = tuple(a for a in ambiguities_of(context) if ambiguity_is_hard(a.dimension))
    if hard:
        details = "\n".join(f"  - [{a.dimension.value}] {a.detail}" for a in hard)
        raise ValueError(
            "这份上下文有讲不清的地方，因此推不出指纹：\n"
            f"{details}\n"
            "指纹要能当「这份输入长什么样」的凭据用，而一份连目标人口都说不清的"
            "输入没有那个样子 —— 硬歧义的那几维会变成空串，于是两份不同的模糊声明"
            "得到同一个摘要。先把这几条答了（或让它们进 ReviewCase），再推指纹。"
        )

    # 恰好一条：上面那道硬门已经把 SCENARIO 的零条与多条都拒了（``SCENARIO`` 在
    # :data:`_HARD_AMBIGUITY_DIMENSIONS` 里）。这里仍然走 ``effective_scenario``
    # 而不是就地取第一条 —— 「是哪一条场景」这句话全仓只有一个入口。
    reading = effective_scenario(context)
    return SceneFingerprint(
        geo=context.geo,
        jurisdiction=effective_jurisdiction(context),
        locales=_normalised(context.candidate_locales),
        entity_classes=_normalised(context.entity_classes),
        asset_classes=_normalised(context.asset_classes),
        organization_classes=_normalised(context.organization_classes),
        scenario=reading,
        objective=context.objective,
        product_use=context.product_use,
        evidence_requirements=_normalised(context.evidence_requirements),
    )


__all__ = [
    "Ambiguity",
    "ContextDimension",
    "HardConstraint",
    "OutOfEnvelope",
    "ResearchContext",
    "ResearchEnvelope",
    "ScenarioCandidate",
    "ScenarioProvenance",
    "SceneFingerprint",
    "ambiguities_of",
    "ambiguity_is_hard",
    "constraints_out_of_envelope",
    "credible_scenario_readings",
    "effective_jurisdiction",
    "effective_scenario",
    "envelope_of",
    "fingerprint_of",
]
