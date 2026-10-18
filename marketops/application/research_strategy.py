"""B16-b（2/3）：**今天有声明来源**的那四个策略模块，以及它们共用的那一半。

B16-b（1/3）交付的是骨架：``domain/research_plan.py`` 定下一格只能有一个写者、
写谁由表决定、越权的贡献连造都造不出来。这一片把那张表**填上内容** —— 八格可写的
格子，逐格交给一个从**声明**里把它读出来的模块。

## 四个模块，逐格对账

===============  ==============================  ==========================================
模块             它写的格                        从哪儿读
===============  ==============================  ==========================================
``Geo…``         ``locales`` / ``query_families``  ``search.locales``、``search.query_templates``、
                 / ``source_methods``            ``page_discovery_hints`` 在不在
``Scenario…``    ``page_archetypes`` /            ``page_discovery_hints`` 的档位与 glob、
                 ``role_functions`` /             ``role-matrix.yaml`` 的 ``role_groups`` 与
                 ``role_priorities``             ``priority.class_order``
``Objective…``   ``decision_refs``                ``profile.yaml`` 的 ``decisions`` 块的**键**
``Access…``      ``acquisition_modes``            ``compliance`` 块，原样读入
===============  ==============================  ==========================================

## 为什么是四个，而不是 §B16 列的那六个

:class:`~marketops.domain.research_plan.StrategyModuleId` 是六格的闭集，而今天**只有
四个模块有声明来源**。另外两个不写，理由是执行计划 §6 逐字那句：「只有类名/字段/
接口的无消费者实现不算完成」。

* ``product_overlay`` —— ``profile.yaml`` 的 ``product`` 块只有 ``name`` /
  ``category`` / ``value_proposition``：前两样**已经**由声明层写进 ``product_claims``
  那一格（而且它是硬的，模块改不了），第三样是给检索词与相关性判断读的散文。
  一个把 ``product_claims`` 再抄一遍的模块不是判断，是同一件事写两遍 —— 而它会让
  「产品叠加层真的在做事」这句话在报告上看起来成立。
* ``workstream_override`` —— 全仓没有 Workstream 这个概念（``command grep -r
  Workstream marketops/`` 只命中 ``research_plan.py`` 里为它留的那个枚举成员与注释）。
  没有声明来源就写，等于**造一个概念**，而它的覆盖权（比场景层更具体）会由那一次
  实现顺手定下来。

这两个名字在 ``MODULE_PRIORITY`` 与 ``FIELD_OVERRIDERS`` 里**有位置**，那是刻意的：
覆盖权是表上的一个位置，不是「等有了再想」的问题。它们今天不进计划，由
``tests/test_research_strategy.py`` 的一条判据钉住（``default_modules`` 交出来的
恰好是那四个）。

## 一条纪律：格子的来源**只有**一次阅读

每个模块的 ``_read()`` 返回一份不可变的阅读记录，而 ``cells`` 是**记录上的方法**，
不是模块上的：记录里没有 ``bundle``、没有 ``self``，所以它算格子时唯一能用的输入
就是「这一次读到了什么」。模块的版本是那条记录 ``chunks`` 的摘要，于是「版本盖住了
它读的每一样声明」不再是一句纪律 —— 一个偷偷再去读一块的实现，会把那一块**排除在
版本之外**，而那正是「有人改了声明，两次编译却拿到同一个版本」的形状。

## 模块与声明是绑定的

:func:`default_modules` **按每一份 bundle 造一套**，而 ``contribute`` 会核对传进来
的 bundle 就是它读的那一份。理由是版本那一格：既然版本是「我读进来的是哪一份声明」
的摘要，那么拿 A 的模块配 B 的 bundle，就会编出一份写着 A 的版本、值来自 B 的计划。

## 这一片关上了什么、还关不上什么

* **关闭条件 1**（可复现 digest、preview 零 provider 调用）：前半在这一片可以量了
  ——同一份声明、同一份上下文两次编译逐字相同，而且四个模块的构造**只需要一份
  bundle**（没有 key、没有客户端、没有时钟，有一条判据钉住）。后半（preview 面）
  在 B16-b（3/3）。
* **关闭条件 2**（同一 Workstream 不同 context → 不同策略）：两个来路各有一格真的
  随请求变。``locales`` 随**请求点名了哪几种语言**收窄（``ResearchContext`` 的
  docstring 逐字如此）；source / page / role 那三根轴随**这一次是哪一个场景**收窄
  （:func:`_scenario_choice` 取哪一块，四格再按块取子序列 —— 见
  ``tests/test_research_strategy.py`` 第 8 节）。后者要求那份声明写过
  ``scenario-matrix.yaml``：**真实的那一份 profile 今天没写**，所以条件 2 在它上面
  仍是 PARTIAL —— 入口有了，内容还没有，`docs/B16_ACCEPTANCE_MATRIX_2026-09-28.md`
  按这个读数记。
* **关闭条件 3**（国家与语言分离）：``geo`` 与 ``locales`` 来自两块不同的声明，有一条
  判据从两个方向打（改 country 不动语言、改语言不动 country）。完整的分离
  （locale families）在 B16-c。
* 关闭条件 4 的上游在输入面（B16-a 已关），4 的下游（硬那九格模块写不了）在
  B16-b（1/3）；5 / 6 / 7 各自的落点见执行计划，不在这一片。

## 失败关闭的清单

一句话：**哪一格今天画不出来，都抛，不画空格。** 空的 ``locales`` 与「不限语言」、
空的 ``page_archetypes`` 与「今天没有页面命中」，在计划上同形 —— 而计划正是拿来
回答「这一次按什么跑」的。
"""

from __future__ import annotations

from dataclasses import dataclass

from ..domain.enums import RoleClass
from ..domain.errors import ConfigError
from ..domain.profile import DeclaredProfile, DeclaredScenarioBlock, ProfileBundle
from ..domain.research_context import ResearchContext, ResearchEnvelope, effective_scenario
from ..domain.research_plan import (
    PageArchetype,
    PlanField,
    PolicyContribution,
    StrategyCompiler,
    StrategyModule,
    StrategyModuleId,
    compliance_rendering,
)
from ..domain.sources import SOURCE_METHODS, resolve_source_method
from ..domain.util import SEP, sha256_text
from ..ports.acquisition import FetchMode
from .openweb import load_open_web_templates


def _revision_of(*chunks: str) -> str:
    """模块版本的算法 —— 全仓只有这一处。

    要答的问题是「两次编译读到的是不是同一份策略」，所以版本是**内容的摘要**，
    不是一个手写的号：手写的号在「有人改了声明却忘了升号」与「两条不同的声明在两次
    部署里拿到同一个号」这两件事上都会答反（同一条纪律见
    ``domain/research_plan.py::ModuleRevision``）。
    """
    return sha256_text(SEP.join(chunks))[:16]


def _read_line(label: str, *values: str) -> str:
    """阅读记录里的一行：读的是**哪一块**、读到了什么。

    标签让 ``_read().chunks`` 自描述 —— 一个来查「这一版盖住了哪些声明」的人，看一行
    就知道它是哪一块，不必回头数每份记录里各块的先后。

    它**不是**一条判据，这里要说实话：它本来是想挡住「两块声明的文本恰好拼成同一串」，
    而今天这条路撞不上，并且不是靠这里——两个装载器各自把它挡住了
    （``search.locales`` 每条都要是 BCP-47 标签，``search.query_templates`` 每条都要
    含占位符），于是任何一块的值都冒充不了另一块的值。所以这一行是余量，不是防线：
    **本模块不复核那两条形状**，哪一天某一块开始收自由文本，挡住碰撞的就只剩这个标签。
    """
    return SEP.join((label, *values))


@dataclass(frozen=True)
class _Cell:
    """一格的值与理由（本模块内部的形状）。

    形状与 :class:`~marketops.domain.research_plan.PolicyContribution` 差两个字段
    （``module_id`` / ``revision``）—— 那两个由 :func:`_as_contribution` 统一填，
    于是「谁写的、哪一版」不可能在某个模块里漏填，也不可能填成别人的。
    """

    field: PlanField
    reason: str
    values: tuple[str, ...] = ()
    archetypes: tuple[PageArchetype, ...] = ()


def _as_contribution(module_id: StrategyModuleId, revision: str, cell: _Cell) -> PolicyContribution:
    """一格 → 一条贡献。**只有这一处**填 ``module_id`` 与 ``revision``。"""
    return PolicyContribution(
        module_id=module_id,
        revision=revision,
        field=cell.field,
        values=cell.values,
        archetypes=cell.archetypes,
        reason=cell.reason,
    )


def _expect_the_declared_bundle(declared: ProfileBundle, given: ProfileBundle) -> None:
    """核对「编这一版用的是不是它读的那一份声明」。

    判据是 ``profile_id`` 与 ``scene_version`` 相符，而**不是** ``is``：同一份没变过
    的声明被装载两次是完全正常的（两个调用点各自 ``load_profile``），把那种情形判成
    错误会让这条判据在一条正当路径上响 —— 而一条会误报的判据，迟早会被撞上它的人
    关掉。``scene_version`` 是那份 Scene 的内容摘要，所以「相符」在这里就是
    「读的是同一份内容」。
    """
    if (given.profile_id, given.scene_version) == (declared.profile_id, declared.scene_version):
        return
    raise ConfigError(
        f"这一版模块是按 profile {declared.profile_id!r}"
        f"（Scene {declared.scene_version}）的声明造的，却拿 profile "
        f"{given.profile_id!r}（Scene {given.scene_version}）的 bundle 来编计划。\n"
        "模块的版本是「我读进来的是哪一份声明」的摘要，所以它只对那一份成立 ——"
        "允许这样混用的话，计划上会写着 A 的版本号，而各格的值来自 B。"
    )


def _declared_profile(bundle: ProfileBundle) -> DeclaredProfile:
    """这份 bundle 的声明块；没有就抛。

    **不退回一组默认值**：计划上可写的那八格全部来自声明，一组编出来的默认策略会让
    「这一次按什么规则跑」有一个看起来完整、实际上没有人认过的答案。
    """
    if bundle.profile is None:
        raise ConfigError(
            f"profile {bundle.profile_id!r} 没有声明块，因此策略模块读不到任何东西。\n"
            "计划上可写的那八格全部从 profile.yaml 的各块读出来；缺了声明块，"
            "「这一次按什么跑」就只能靠猜，而猜出来的那一份会被读成批准过的一份。"
        )
    return bundle.profile


@dataclass(frozen=True)
class _ScenarioChoice:
    """这一次按场景矩阵里的**哪一块**收窄，以及四个格子理由里要接的那一句。

    ``block is None`` 有两个来路，处置相同而**理由不同**，所以理由也跟着走：
    这份声明根本没有场景入口时 ``note`` 是空串 —— 一个字都不加，于是「声明里没有这一
    份文件」在计划上与加这一份之前**逐字相同**（今天真实的那一份 profile 就是这一种）；有入口
    却没给这一条读法写块时 ``note`` 写明用的是整份声明。两个来路都在报告上读得出来，
    而不是只在一个布尔上分叉。

    ``note`` 是一整句、以 ``；`` 开头，由 :func:`_scenario_choice` 一处写下，四个会
    随场景收窄的格子各自把它接在自己的理由后面 —— 一句话的定义只有一处。
    """

    block: DeclaredScenarioBlock | None
    note: str


def _scenario_choice(bundle: ProfileBundle, context: ResearchContext) -> _ScenarioChoice:
    """这一次按哪一块收窄 —— §B16 条件 2 那三根轴的入口。

    **只有这份声明写过场景块时才去问「这一次是哪一个场景」。** 没有那一份文件时问它
    是白问，而且会把「这份声明还没有按场景说话的入口」变成一次编译失败 —— 条件 2 要
    的是有一个按场景分块的入口，不是逼每一份声明今天就分块。

    有入口时，:func:`effective_scenario` 说不清就**照抛**（零条与两条以上可信读法各自
    是一条硬歧义）。那一步静默回退成整份声明的话，一次说不清的场景会读成一次「按整份
    声明跑」，而后者在计划上看起来完全正常 —— 差别恰恰是这一格要答的问题：按哪一
    块收窄。

    它是**这一次请求**的输入，所以它进格子、不进版本（见 :meth:`_Declared.contribute`）。
    """
    matrix = bundle.scenario_matrix
    if matrix is None:
        return _ScenarioChoice(block=None, note="")
    reading = effective_scenario(context)
    block = matrix.blocks.get(reading)
    if block is None:
        return _ScenarioChoice(
            block=None,
            note=f"；scenario-matrix.yaml 没有给场景 {reading!r} 写块，这一格按整份声明写",
        )
    return _ScenarioChoice(
        block=block, note=f"；这一格按场景 {reading!r} 的块收窄（scenario-matrix.yaml）"
    )


@dataclass(frozen=True)
class _Read:
    """一次阅读：读了哪几行（进版本），以及**从这次阅读里**算出格子。

    ``chunks`` 逐条对应「这次真的读了哪一块声明」：多写一条会让两次无差别的编译看起来
    不是同一份，少写一条会让一次真的改动在版本上消失 —— 两个方向都是坏的，而后者
    更坏（它安静）。

    ``cells`` 是**记录上的方法**，不是模块上的：记录里没有 bundle、没有盘、也没有
    ``self``，所以「格子是从这次阅读算出来的」不是纪律，是构造。

    ``cells`` 收的三个输入（上下文、信封、选中的场景块）**都是这一次请求**的输入，
    而版本一样都不收 —— 它只从 ``chunks`` 算。这条分工是 C1 定下的：版本答「读的是
    哪一份声明」，请求答「这一次按哪一块收窄」；把后者塞进版本，同一份声明在两个场景
    下就会有两个版本号，而「这一次编的是哪一份声明」正是那一栏要答的问题。
    """

    chunks: tuple[str, ...]

    @property
    def revision(self) -> str:
        """这一版模块 —— 从它读进来的那几行声明算出来。"""
        return _revision_of(*self.chunks)

    def cells(
        self,
        *,
        context: ResearchContext,
        envelope: ResearchEnvelope,
        scenario: _ScenarioChoice,
    ) -> tuple[_Cell, ...]:
        raise NotImplementedError  # 子类实现；基类不是一份可用的阅读记录


class _Declared:
    """四个模块共用的那一半：读、贡献、以及「编的是哪一份声明」的核对。

    **它不是 dataclass**：``bundle`` 由各模块自己声明成字段，于是构造一个模块只需要
    一份声明 —— 没有 key、没有客户端、没有时钟（有一条判据钉住这件事）。

    它也不认识任何一块具体声明：读什么、算什么都留在子类的 ``_read()`` 与那份记录里。

    ## 它**没有** ``revision`` 那一格（曾经有过）

    一个 ``module.revision`` 属性读起来无害，而它是**第二次读同一份声明**：
    :meth:`contribute` 里已经读过一次，并把版本填进了它交出来的每一条贡献；属性再读
    一次，是另一条独立的路径。两条路各自算出来的版本必须一直相等 —— 而它们不相等时
    计划上**看不出来**：那一栏写的是第二次读出来的那个号，格子里的值来自第一次。

    所以版本的唯一出口是 :meth:`contribute` 交出来的那些贡献（:func:`_as_contribution`
    是唯一填 ``module_id`` 与 ``revision`` 的地方），计划那一侧从贡献上取
    （``domain/research_plan.py::_module_revisions``）。
    """

    #: 各模块自己声明的字段。这一行是给读的人和类型检查器的，不产生字段。
    bundle: ProfileBundle

    @property
    def module_id(self) -> StrategyModuleId:
        raise NotImplementedError  # 子类实现

    def _read(self) -> _Read:
        raise NotImplementedError  # 子类实现

    def contribute(
        self,
        *,
        context: ResearchContext,
        envelope: ResearchEnvelope,
        bundle: ProfileBundle,
    ) -> tuple[PolicyContribution, ...]:
        """这一次研究要写的格子。形状与 :class:`StrategyModule` 逐字相同。

        ``_read()`` **只调一次**：版本与格子必须出自同一次阅读，否则「版本盖住了哪些
        声明」要靠两处实现互相对齐。

        **这里是唯一读声明的地方**：本类没有 ``revision`` 属性（理由见类 docstring），
        所以「同一份声明被读两次」在这条路上不存在 —— 不是靠纪律，是没有第二个入口。

        场景块在 ``_read()`` **之后**才选，而且是四个模块各自算一遍同一段代码
        （:func:`_scenario_choice`）：它读的是「这份声明写过哪几块」与「这一次是哪一个
        场景」，两样都不是本模块读进来的声明内容。它因此**不进版本**，只进格子 ——
        版本答「读的是哪一份声明」，请求答「这一次按哪一块收窄」。
        """
        _expect_the_declared_bundle(self.bundle, bundle)
        read = self._read()
        scenario = _scenario_choice(bundle, context)
        return tuple(
            _as_contribution(self.module_id, read.revision, cell)
            for cell in read.cells(context=context, envelope=envelope, scenario=scenario)
        )


#: 每一块声明 → 它让哪一个来源方法进入这一次计划。
#:
#: **有声明才有资格**：一个方法不在这张表里，或者它那一块声明今天没写，它就不进计划
#: —— 而不是「默认都可以」。这张表是「谁能扩池、谁只能补内容」那句话在计划上的落点
#: （族的意思见 ``domain/sources.py``：``public_search`` 扩池、``known_domain`` 只补内容）。
#:
#: ``page_discovery_hints`` 同时开出两条已知域名内的路（站内检索与站点抓取）：它声明的
#: 是「在这个组织的站点上先看哪些 URL」，而这两条路是找那些 URL 的两种办法 ——
#: 展开成 ``site:`` 检索，或者顺着站点结构走。
_SOURCE_ROUTES: tuple[tuple[str, str], ...] = (
    ("search.query_templates", "search.engine.v1"),
    ("page_discovery_hints", "search.engine.site_scoped.v1"),
    ("page_discovery_hints", "crawl.site.v1"),
)


def _locales_cell(
    reading: _GeoRead, *, context: ResearchContext, envelope: ResearchEnvelope
) -> _Cell:
    """这一次按哪几种语言检索。

    三件事各自是一条失败关闭的判据：声明为空（没有依据）、请求点名了声明里没有的语言
    （收窄只能收到已声明的那几种）、以及计划要写的语言超出了已批准的那几格
    （计划不许写一份没人批过的范围）。
    """
    if not reading.locales:
        raise ConfigError(
            "profile 的 search.locales 没声明 —— 这一次按哪种语言检索答不出来。\n"
            "填一个看起来合理的缺省语言，会让「这次问的是英文索引」这句话在计划上"
            "没有任何出处，而它恰恰决定了哪些页面会被返回。"
        )
    unclaimed = tuple(tag for tag in context.candidate_locales if tag not in reading.locales)
    if unclaimed:
        raise ConfigError(
            f"请求把这一次研究收窄到 {list(unclaimed)}，"
            f"而 profile 声明的检索语言是 {list(reading.locales)}。\n"
            "收窄只能收到已声明的那几种（``constraints_out_of_envelope`` 那一侧先拦"
            "一次，这里是同一条判据的下游）。把没声明过的语言悄悄丢掉，看起来像"
            "「收窄成功」，而实际发生的是「请求要的那几种没被满足」。"
        )
    # 空着 = 用声明的那几种（``ResearchContext`` 的 docstring 逐字如此）：这一格的语义
    # 是**收窄**声明，收窄成空集没有意义。
    enacted = context.candidate_locales or reading.locales
    values = tuple(tag for tag in dict.fromkeys(reading.locales) if tag in set(enacted))
    unapproved = tuple(tag for tag in values if tag not in set(envelope.locales))
    if unapproved:
        raise ConfigError(
            f"计划要写进语言这一格的是 {list(unapproved)}，而已批准的范围里没有它们"
            f"（envelope.locales = {list(envelope.locales)}）。\n"
            "envelope 那一格空着**不是**「不限语言」，是「这一份批准没有语言那一栏」。"
            "把它读成前者，等于让一次没被批过的检索看起来在范围之内。"
        )
    narrowed = "；这一次按请求收窄到它点名的那几种" if context.candidate_locales else ""
    return _Cell(
        field=PlanField.LOCALES,
        values=values,
        reason=(
            f"profile.yaml 的 search.locales，原样标签{narrowed}"
            "。国家那一轴是 geo 那一格（来自 profile 的 country），两格不合并 ——"
            "en-GB 这种标签同时带着两者，而这一格只装语言"
        ),
    )


def _query_families_cell(reading: _GeoRead) -> _Cell:
    """按哪几句去问。模板是匿名的单行文本，所以这一格装的就是那几句原文。"""
    return _Cell(
        field=PlanField.QUERY_FAMILIES,
        values=reading.templates,
        reason=(
            "profile.yaml 的 search.query_templates，按声明顺序原样"
            "（经 application/openweb.py::load_open_web_templates 校验：空/缺失败关闭）。"
            "占位符原样保留 —— 展开是那一层的事，这一格只说「按哪几句问」"
        ),
    )


def _source_methods_cell(reading: _GeoRead, *, scenario: _ScenarioChoice) -> _Cell:
    """这次可以从哪儿拿行 —— 按声明开出来的那几条路，再按场景块**取交集**。

    声明那一侧**这一格写不出空值**，而且不是靠一句纪律：模板在
    :meth:`GeoLocaleSourceProfile._read` 里已经过 ``load_open_web_templates``，缺/空
    都在那里抛了，所以那条「至少开一条路」的路线（``search.query_templates``）到这里
    必然成立 —— 那一段因此**不留**判据，它构造不出来，而一条永远不响的判据会让读的人
    以为这一层已经防住了某件事。

    **场景块那一侧会响**：块把三条路全收掉是写得出来的，而这一格空着会让「这个场景
    一条路都不许」与「没人给这个场景写路」在计划上同形 —— 前者不是一条策略，后者要
    回去改声明。所以这一段单写一条判据（见下面的 ``if not values``）。

    交集是刻意的（块开不出一条声明没开的路，见 :class:`DeclaredScenarioBlock`），所以
    块里点了、而这份声明今天没给入口的名字**不进这一格** —— 但它要在理由里说出来：
    不说的话，报告上写「留了三条」而实际留了一条。
    """
    opened = {
        "search.query_templates": bool(reading.templates),
        "page_discovery_hints": bool(reading.hint_tiers),
    }
    routes = tuple(route for route in _SOURCE_ROUTES if opened[route[0]])
    for _, method_id in routes:
        # 装配期就解析一次：路线表里拼错一个 id，不该等到报告上才看出来。
        resolve_source_method(method_id)
    route_ids = {method_id for _, method_id in routes}
    # 按注册表的**声明顺序**，不按路线表的顺序：同一份声明两次编译要逐字相同，
    # 而「哪一条路先被写进代码」不是声明的一部分。
    declared = tuple(method_id for method_id in SOURCE_METHODS if method_id in route_ids)
    kept = scenario.block.sources if scenario.block is not None else declared
    values = tuple(method_id for method_id in declared if method_id in set(kept))
    dropped = tuple(method_id for method_id in kept if method_id not in route_ids)
    if not values:
        # 没有块时到不了这里（上面那一段：声明至少开一条路），所以这一条说的是块。
        raise ConfigError(
            f"场景块收窄之后一条路都没留下：块里写的是 {list(kept)}，而这份声明今天"
            f"开着的是 {list(declared)}。\n"
            "这一格是「允许从哪儿拿行」的许可，空着会让这次研究一条路都没有，而报告上"
            "「这个场景一条路都不许」与「没人给这个场景写路」同形。两条处置：块里点一条"
            "开着的路，或者去看这份声明为什么没给那块条目入口。"
        )
    undeclared = tuple(method_id for method_id in SOURCE_METHODS if method_id not in route_ids)
    reason = (
        "按声明开出来的路："
        + "、".join(f"{label} → {method_id}" for label, method_id in routes)
        + "；顺序按 domain/sources.py 注册表的声明顺序。"
        f"注册表里另外 {len(undeclared)} 条（{'、'.join(undeclared)}）"
        "今天没有任何声明给它们入口"
    )
    if scenario.block is not None:
        reason += f"；块里点的 {list(kept)} 与上面这几条取交集，留下来的是 {list(values)}"
        if dropped:
            reason += f"，其中 {list(dropped)} 今天没有声明给它们入口"
    return _Cell(field=PlanField.SOURCE_METHODS, values=values, reason=reason + scenario.note)


@dataclass(frozen=True)
class _GeoRead(_Read):
    """读了：声明的检索语言、开放网络检索模板、以及站点发现提示在不在。"""

    locales: tuple[str, ...]
    templates: tuple[str, ...]
    hint_tiers: tuple[str, ...]

    def cells(
        self,
        *,
        context: ResearchContext,
        envelope: ResearchEnvelope,
        scenario: _ScenarioChoice,
    ) -> tuple[_Cell, ...]:
        return (
            _locales_cell(self, context=context, envelope=envelope),
            _query_families_cell(self),
            _source_methods_cell(self, scenario=scenario),
        )


@dataclass(frozen=True)
class GeoLocaleSourceProfile(_Declared):
    """地理 / 语言 / 来源层 —— 最笼统的那一层，也是「这一次从哪儿开始找」那一层。

    ## 国家与语言是两个轴，这一格只装语言

    ``country``（经 envelope）说**问哪个国家的索引**，``search.locales`` 说**用哪种
    语言问**。``en-GB`` 这种标签同时带着两者，而计划上是两格：``geo`` 是前者、
    ``locales`` 是后者。这里**不做**「降成主语言子标签」那一步 ——
    ``ResearchEnvelope`` 的 docstring 写过为什么：提前降会让 ``en-GB`` 与 ``en-US``
    合成一格，于是「只要英式英语的来源」这条收窄会静默地变成「英语都行」。真正需要
    主语言的是检索那一侧（``ports/search.py::SearchQuery``），它在自己的边界上做。

    ## 来源方法这一格答的是「允许从哪儿拿行」

    它是一份**许可**，不是一份计划：这一次可以走开放检索（扩池）、可以限定在已知域名
    内检索与抓站点（补内容）。哪一条都没被批准时它抛，而不是留一个空格子。
    """

    bundle: ProfileBundle

    @property
    def module_id(self) -> StrategyModuleId:
        return StrategyModuleId.GEO_LOCALE_SOURCE

    def _read(self) -> _GeoRead:
        declared = _declared_profile(self.bundle)
        # 模板经那一族自己的校验器读：它保序、空/缺失败关闭，而两族的声明形状刻意
        # 不共用（共用一个校验器正是 ``{domain}`` 变成合法写法的那条缝）。
        templates = load_open_web_templates(self.bundle)
        hints = declared.page_discovery_hints
        return _GeoRead(
            chunks=(
                _read_line("search.locales", *declared.search_locales),
                _read_line("search.query_templates", *templates),
                # 这一块我读的是**在不在**，所以进版本的是在不在 —— 把 glob 的内容
                # 也塞进版本，会让改一条 glob 在版本上多出一次与这一格无关的变化
                # （那一块的内容改了，场景层那一格会如实动）。
                _read_line("page_discovery_hints", "declared" if hints else "absent"),
            ),
            locales=tuple(declared.search_locales),
            templates=templates,
            hint_tiers=tuple(hints),
        )


def _check_class_order(order: tuple[str, ...], *, profile_id: str) -> None:
    """``priority.class_order`` 的形状：空着、拼错、或者把被排除的类排进来，都抛。"""
    if not order:
        raise ConfigError(
            f"profile {profile_id!r} 的 role-matrix.yaml 没有声明 priority.class_order，"
            "因此「角色类别的先后」这一格没有依据。\n"
            "退回 role_groups 的书写顺序是**另造一条规则**：那一栏说的是「有哪些组」，"
            "不是「哪一类更靠前」。"
        )
    known = {member.value for member in RoleClass}
    unknown = tuple(name for name in order if name not in known)
    if unknown:
        raise ConfigError(
            f"priority.class_order 里有闭集之外的名字 {list(unknown)}；"
            f"RoleClass 的成员是 {sorted(known)}。\n"
            "一个拼错的名字会让这一格少一类而报告上看不出来 —— 次序少一位，"
            "与「这一类今天不参与」在计划上同形。"
        )
    forbidden = tuple(
        name
        for name in order
        if name in {RoleClass.EXCLUDED.value, RoleClass.UNKNOWN.value}
    )
    if forbidden:
        raise ConfigError(
            f"priority.class_order 里有 {list(forbidden)}。\n"
            "``excluded`` 不是「排最后」，是「不得进入有效路线」；``unknown`` 是"
            "「这一组没写业务口径」—— 两者都不是先后里的一个位置：把它们排进来，"
            "一条禁令就会读成一个次序，而报告上看起来一切正常。"
        )


def _page_archetypes_cell(reading: _ScenarioRead, *, scenario: _ScenarioChoice) -> _Cell:
    if not reading.hints:
        raise ConfigError(
            "profile 没有声明 page_discovery_hints，因此「先抓哪一类页面」这一格"
            "没有依据。\n"
            "一档都没有，与「今天没有页面命中」在计划上同形，而两者要做的事不同："
            "一个要去看声明，一个要去看抓取。"
        )
    hints = reading.hints
    if scenario.block is not None:
        # 块里点的档位是声明档位的**子序列**（装载期已判，见 ``_require_narrowing``），
        # 所以收窄之后不可能空 —— 这里不摆一条不会响的判据。
        kept = set(scenario.block.page_tiers)
        hints = tuple(item for item in hints if item[0] in kept)
    reason = (
        "profile.yaml 的 page_discovery_hints：档位是它的键、glob 是它的值，"
        "都按声明顺序（顺序是语义 —— 它同时是抓取前沿与报告的依据）"
    )
    if scenario.block is not None:
        reason += f"；这一格再按块收窄到档位 {[tier for tier, _ in hints]}"
    return _Cell(
        field=PlanField.PAGE_ARCHETYPES,
        archetypes=tuple(PageArchetype(tier=tier, patterns=patterns) for tier, patterns in hints),
        reason=reason + scenario.note,
    )


def _role_functions_cell(reading: _ScenarioRead, *, scenario: _ScenarioChoice) -> _Cell:
    if not reading.functions:
        raise ConfigError(
            f"role-matrix 的 groups 里没有一个组声明了业务口径（{reading.undecided} 组都"
            "是 unknown），因此「找哪几种口径的岗位」这一格画不出来。\n"
            "把 unknown 当成一种口径写进计划，会让「这份声明没说」变成「这次要去找"
            "认不出的岗位」—— 而这一格是要拿去筛人的。"
        )
    functions = reading.functions
    if scenario.block is not None:
        # 同上一格：块里点的是声明名录的子序列，所以收窄之后不可能空。
        kept = set(scenario.block.business_functions)
        functions = tuple(name for name in functions if name in kept)
    reason = "role-matrix.yaml 的 role_groups 逐组声明的 business_function，按声明顺序去重"
    if scenario.block is not None:
        reason += f"；这一格再按块收窄到 {list(functions)}"
    if reading.undecided:
        reason += (
            f"；另有 {reading.undecided} 组没写业务口径（unknown 不是一种口径，没写进来）"
        )
    return _Cell(
        field=PlanField.ROLE_FUNCTIONS,
        values=functions,
        reason=reason + scenario.note,
    )


def _role_priorities_cell(reading: _ScenarioRead, *, scenario: _ScenarioChoice) -> _Cell:
    order = reading.order
    reason = (
        "role-matrix.yaml 的 priority.class_order，原样。同族的 weight 那一栏"
        "没有进这一格：它给的是每组一个权重，而这一格要答的是「哪一类更靠前」——"
        "把权重变成次序要再选一个比较办法，那是一条没有人认过的规则"
    )
    if scenario.block is not None:
        # 同前两格：块里的次序是声明次序的子序列，所以收窄之后不可能空，而且**次序
        # 仍按声明**（块改不了先后，这正是装载期那条对账挡下的事）。
        kept = set(scenario.block.class_order)
        order = tuple(name for name in order if name in kept)
        reason += f"；这一格再按块收窄到 {list(order)}"
    return _Cell(
        field=PlanField.ROLE_PRIORITIES, values=order, reason=reason + scenario.note
    )


@dataclass(frozen=True)
class _ScenarioRead(_Read):
    """读了：页面提示的档位与 glob、各组的业务口径、以及类别的先后。"""

    hints: tuple[tuple[str, tuple[str, ...]], ...]
    functions: tuple[str, ...]
    undecided: int
    order: tuple[str, ...]

    def cells(
        self,
        *,
        context: ResearchContext,
        envelope: ResearchEnvelope,
        scenario: _ScenarioChoice,
    ) -> tuple[_Cell, ...]:
        return (
            _page_archetypes_cell(self, scenario=scenario),
            _role_functions_cell(self, scenario=scenario),
            _role_priorities_cell(self, scenario=scenario),
        )


@dataclass(frozen=True)
class ScenarioProfile(_Declared):
    """场景层：先看哪一类页面、找哪几种业务口径的岗位、以及它们的先后。

    ## 这三格都从**声明**读，不推

    页面那一格是 ``page_discovery_hints`` 的键与值；角色口径那一格是各组的
    ``business_function``；先后那一格是 ``priority.class_order``。三者都不是「按资产
    类别算出来的」—— 今天声明里没有那条按类别分支的线索，而**编一条**出来会让
    「这次先看哪一页」有一个看起来有依据、实际上没有人写过的答案。

    ## 只读 ``groups``，不读 ``excluded_groups``

    后者说的是「这一类岗位**不得进入有效路线**」（财务 / 招生 / HR / 教学 / 一般行政 /
    无场地绑定的客服与预订）。把它读进业务口径那一格，等于把一张排除表反过来当成
    要去找的口径 —— 而那正是 ``DeclaredRoleMatrix`` 的 docstring 记着的那个混淆。
    """

    bundle: ProfileBundle

    @property
    def module_id(self) -> StrategyModuleId:
        return StrategyModuleId.SCENARIO

    def _read(self) -> _ScenarioRead:
        declared = _declared_profile(self.bundle)
        matrix = self.bundle.role_matrix
        if matrix is None:
            raise ConfigError(
                f"profile {self.bundle.profile_id!r} 没有装载 role-matrix，因此"
                "「找哪几种业务口径的岗位」与「角色类别的先后」两格答不出来。\n"
                "这两格与角色词表同源（``bundle.role_terms`` 由它来），而缺了它，"
                "那一份不许退回一组默认角色。"
            )
        hints = tuple(
            (tier, tuple(patterns)) for tier, patterns in declared.page_discovery_hints.items()
        )
        # 「哪些口径算数」这条规则住在类型上（``DeclaredRoleMatrix``），这里是它的
        # 消费者。本模块**不再自带一份循环**：两份实现会漂移，而漂移的那一份不报错
        # —— 它只会算出一份少一个口径的名录，计划上看起来完全正常。
        functions = matrix.business_functions
        undecided = matrix.undecided_groups
        order = tuple(str(item) for item in matrix.priority.get("class_order", ()))
        _check_class_order(order, profile_id=self.bundle.profile_id)
        return _ScenarioRead(
            chunks=(
                _read_line(
                    "page_discovery_hints",
                    *(f"{tier}{SEP}{SEP.join(patterns)}" for tier, patterns in hints),
                ),
                _read_line("role_groups.business_function", *functions),
                _read_line("priority.class_order", *order),
            ),
            hints=hints,
            functions=tuple(functions),
            undecided=undecided,
            order=order,
        )


def _decision_refs_cell(reading: _ObjectiveRead) -> _Cell:
    return _Cell(
        field=PlanField.DECISION_REFS,
        values=reading.tasks,
        reason=(
            "profile.yaml 的 decisions 块的键，按声明顺序（题面本身不在这里 ——"
            "它住 application/decision_specs.py，自己有一版；这一格只说「这次要判"
            "哪几件事」，改一句描述不该让这一格变）"
        ),
    )


@dataclass(frozen=True)
class _ObjectiveRead(_Read):
    """读了：声明了哪几道判定题（**键**，不是题面）。"""

    tasks: tuple[str, ...]

    def cells(
        self,
        *,
        context: ResearchContext,
        envelope: ResearchEnvelope,
        scenario: _ScenarioChoice,
    ) -> tuple[_Cell, ...]:
        # 场景块与这一格无关：§B16 条件 2 的三根轴是 source / page / role，而这一格
        # 是「这次要判哪几件事」—— 它问的是声明里的题名，不随场景收窄。签名跟着协议
        # 走，所以这一格也收下这个输入；收下而不读，是有意的。
        return (_decision_refs_cell(self),)


@dataclass(frozen=True)
class ObjectivePolicy(_Declared):
    """目标层：这一次要问哪几道判定题。

    ``decision_refs`` 装的是**任务的名字**，不是题面。题面（选项与描述）住在
    ``application/decision_specs.py``，而且它自己有一版（``DecisionSpec.version``，
    由题面文本与选项算出）。两件事分开记，是因为它们回答不同的问题：这一格说「这次
    要判哪几件事」，那一版说「判的时候用的是哪一版题面」—— 改一句描述不该让计划摘要
    变，也不该让「这一次要判什么」变。

    零个 task 与「这一次不做判定」不是同一件事：前者是一份写坏了的声明
    （``DeclaredDecisions`` 的 docstring 逐字如此），后者要由调用方自己说。
    """

    bundle: ProfileBundle

    @property
    def module_id(self) -> StrategyModuleId:
        return StrategyModuleId.OBJECTIVE

    def _read(self) -> _ObjectiveRead:
        decisions = self.bundle.decisions
        if decisions is None or not decisions.tasks:
            raise ConfigError(
                f"profile {self.bundle.profile_id!r} 没有声明 decisions 块"
                "（或者声明了零个 task），因此「这一次要问哪几道判定题」这一格"
                "没有依据。\n"
                "空着与「这次不做判定」不是同一件事，而它们在计划上同形 ——"
                "所以这里抛，让调用方自己说清是哪一种。"
            )
        tasks = tuple(task.value for task in decisions.tasks)
        return _ObjectiveRead(chunks=(_read_line("decisions.tasks", *tasks),), tasks=tasks)


def _acquisition_modes_cell(reading: _AccessRead) -> _Cell:
    return _Cell(
        field=PlanField.ACQUISITION_MODES,
        values=(FetchMode.HTTP.value, FetchMode.BROWSER.value),
        reason=(
            "先 HTTP 直连、必要时降级到浏览器（浏览器那一级比直连贵，所以这里的次序"
            "就是降级次序）；``auto`` 不进这一格 —— 它答的是「让升级判据去挑」，"
            "列进来会让「批准了浏览器」与「按情况可能开浏览器」在计划上同形。"
            "依据 compliance 原样读入（不解读哪几条是许可、哪几条是义务）："
            + "、".join(reading.compliance)
        ),
    )


@dataclass(frozen=True)
class _AccessRead(_Read):
    """读了：``compliance`` 块**原样**（``key=value``，排序）。"""

    compliance: tuple[str, ...]

    def cells(
        self,
        *,
        context: ResearchContext,
        envelope: ResearchEnvelope,
        scenario: _ScenarioChoice,
    ) -> tuple[_Cell, ...]:
        # 同上：批准哪几级获取方式不随场景变 —— 它是 ``compliance`` 块原样，而那是
        # 一份**声明**，不是一次场景判断。收下而不读，与 ``_ObjectiveRead`` 同一个理由。
        return (_acquisition_modes_cell(self),)


@dataclass(frozen=True)
class AccessStrategy(_Declared):
    """访问层：这一次批准了哪几级获取方式。

    ## 它持有的不是节流参数

    ``domain/access_governance.py::AccessPolicy``（重试 / 退避 / 熔断）是另一件东西，
    而两者的限制方向相反：前者是节流，这一格是**许可**。名字的裁定见
    :class:`~marketops.domain.research_plan.StrategyModuleId` 的 ``ACCESS`` 那一条。

    ## 这一格答的是「批准了哪几级」

    所以 ``auto`` 不进这一格（见 :func:`_acquisition_modes_cell`）。反过来，这个闭集
    里**没有**「登录之后取」这一级，于是任何声明也批不出它来 —— 那是
    ``FetchMode`` 这个闭集的结构，不是这里的一条纪律：一个想给自己添一级的模块，
    得先去改 ``domain`` 的端口定义，而那是一次会被看见的改动。

    ## 声明在这一格里进的是**理由**，不是值

    值是从 ``FetchMode`` 这个闭集与降级次序来的（所以它对每一份声明都相同），而
    声明的角色是**依据**：``compliance`` 原样进理由与模块版本，于是改一条合规声明会
    让这一版的版本变、计划摘要跟着变 —— 「这一次按什么取」这件事因此仍然可查。
    """

    bundle: ProfileBundle

    @property
    def module_id(self) -> StrategyModuleId:
        return StrategyModuleId.ACCESS

    def _read(self) -> _AccessRead:
        declared = _declared_profile(self.bundle)
        # 与声明层那一格**同一个渲染器**（``compliance_rendering``）：两处渲染同一块
        # 声明各写一遍的话，两边的排序规则总有一天会分叉，而计划上会出现两个写法。
        compliance = compliance_rendering(declared.compliance)
        return _AccessRead(
            chunks=(_read_line("compliance", *compliance),),
            compliance=compliance,
        )


def default_modules(bundle: ProfileBundle) -> tuple[StrategyModule, ...]:
    """这一份声明今天编得出来的那四个模块，按 ``MODULE_PRIORITY`` 的顺序。

    **每份 bundle 一套**（每调一次造一套）：模块的版本是「我读进来的是哪一份声明」的
    摘要，所以它只对那一份声明成立 —— 一套模块配两份 bundle，会编出一份写着 A 的
    版本、值来自 B 的计划（``contribute`` 会拦下它）。

    两个可选模块（``product_overlay`` / ``workstream_override``）**不在这一份名单
    里**，因为它们今天没有声明来源 —— 理由见模块 docstring。
    """
    return (
        GeoLocaleSourceProfile(bundle),
        ScenarioProfile(bundle),
        ObjectivePolicy(bundle),
        AccessStrategy(bundle),
    )


def research_compiler(bundle: ProfileBundle) -> StrategyCompiler:
    """把这一份声明的四个模块接成一个编译器。

    编译器不认识具体模块（它只按表拼装），所以这一句是**唯一**的接线点：换一套模块、
    或者往这一份名单里加一个，都不需要动 ``domain/``。
    """
    return StrategyCompiler(modules=default_modules(bundle))


def plan_source_methods(bundle: ProfileBundle) -> tuple[str, ...]:
    """这一份声明**开出来的那几条路** —— 计划里 ``strategy.source_methods`` 那一格
    在没有场景块收窄时的取值。

    ## 为什么它在这里，而不是让两个调用点各读一遍声明

    「这一次允许从哪儿拿行」这句话今天只有一个定义（:func:`_source_methods_cell`），
    而它有两个读者：

    * ``get_assignment`` —— 把这一格的取值交给 worker（``guidance.plan_steps``）；
    * ``search_web`` / ``fetch_document`` / ``crawl_site`` 与 ``decide`` ——
      拿它校验调用方声称的那一步。

    两处各读一遍声明的后果**不是文案不一致**，是交接的那一份与被校验的那一份不是
    同一个集合 —— 而那种分歧在报告上看不出来：worker 照着服务端交出的那一步填，
    工具把它拒了，两边各自都「做完了」。

    ## 它是**许可**，不是「这一轮的 Run 用的是哪一份计划」

    这一格还会按 ``ResearchContext`` 点名的场景块再取一次交集
    （:func:`_scenario_choice`，``scenario-matrix.yaml`` 存在时）。而
    ``get_assignment`` 与那几条工具**都拿不到** ``ResearchContext`` —— 它是受理那一刻
    由调用方声明的（``adapters/mcp_server/runs.py`` 的模块 docstring：计划由服务端
    现编，不由调用方交一份；而现编所需的上下文只存在于受理那一刻）。所以这一格是
    **无场景收窄**时的取值，也就是这份 Scene 允许的最宽的那一份。

    方向因此是**单向**的，而且这一层只往严的一侧错：一次具体的 Run 可能比它更窄
    （交集只会变小），拿宽的那一份去比对**不会**放行一个声明从没开过的步骤。反过来
    那一半 —— 「比这一轮的 Run 更宽」—— 这一层今天答不出来，它写在
    ``get_assignment`` 的 ``notes`` 里，不假装答过。

    Raises:
        ConfigError: 这一份声明开不出一条路（``load_open_web_templates`` 的空/缺）。
            **失败关闭**，调用点必须把它翻成一次拒绝 —— 读不到这一格时退回
            「都可以」，会让这一格从「计划里的一步」退化成「一个拼写正确的字符串」。
    """
    reading = GeoLocaleSourceProfile(bundle)._read()
    return _source_methods_cell(
        reading, scenario=_ScenarioChoice(block=None, note="")
    ).values


__all__ = [
    "AccessStrategy",
    "GeoLocaleSourceProfile",
    "ObjectivePolicy",
    "ScenarioProfile",
    "default_modules",
    "plan_source_methods",
    "research_compiler",
]
