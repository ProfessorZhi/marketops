"""``marketops/application/research_strategy.py`` 的判据 —— 纯函数，**不联网、不读凭据**。

这一族存在的理由只有一个：让 ``domain/research_plan.py`` 那张「每一格谁写的」的表
**真的有写者**。因此这里盯的失败都不是会崩的失败，而是**不会报错**的失败：

1. 一格的值不是从声明读来的，而是模块自己推出来的 —— 计划上于是有一个看起来有依据、
   实际上没有人写过的答案，而它决定这次研究去哪里找。
2. 模块的版本没盖住它读的**全部**声明 —— 有人改了声明，两次编译拿到同一个版本，
   于是「这一份计划是哪一版策略编的」答不出来。
3. 一格画不出来时返回空元组 —— 空的 ``locales`` 与「不限语言」、空的
   ``page_archetypes`` 与「今天没有页面命中」，在计划上同形。
4. 一个模块配上一份**不是它读的那一份**声明 —— 计划上写着 A 的版本、值来自 B。

探针 fixture 走**真实装载器**（照 ``tests/test_discovery_openweb.py`` 的形状：
``copytree`` + 改 YAML + ``Settings`` + ``load_profile``）：一个只在手搭的假 bundle
上成立的模块，接线到组合根上会碎 —— 而碎的那一次是在花了额度之后。

真实 profile 在这里也照跑（第 7 节）：那一份计划是「今天按什么跑」的基线，
它固化成取值之后，某次改动把某一格变成空就会当场红。

第 9 与第 10 节是这一族里不判「计划对不对」的两节：它们判的是**入口的有无**。第 9 节
判「分家在这条链上没有入口」（关闭条件 2 的后半 —— 池与证据仍按工作区共享），第 10 节
判关闭条件 5 的两半（排序没有运行期自由度；拆 Scene 没有自动路）。两节都是结构判据，
因为那些行为面要一次 Run 才能观察，而 Run 面属 B17 —— 今天的诚实答案是「做不到」与
「不需要做到」，不是「做到了」。
"""

from __future__ import annotations

import ast
import dataclasses
import inspect
import shutil
from pathlib import Path
from typing import Callable

import pytest
import yaml

from marketops.adapters.profile_loader import SOP_SCENARIO_MATRIX_SCHEMA, load_profile
from marketops.application.openweb import load_open_web_templates
from marketops.application.research_strategy import (
    AccessStrategy,
    GeoLocaleSourceProfile,
    ObjectivePolicy,
    ScenarioProfile,
    default_modules,
    plan_source_methods,
    research_compiler,
)
from marketops.config import REPO_ROOT, Settings
from marketops.domain.errors import ConfigError
from marketops.domain.profile import ProfileBundle
from marketops.domain.research_context import (
    ResearchContext,
    ResearchEnvelope,
    ScenarioCandidate,
    ScenarioProvenance,
    envelope_of,
)
from marketops.domain.research_plan import (
    FIELD_OWNER,
    FIELD_OVERRIDERS,
    MODULE_PRIORITY,
    MODULE_WRITABLE_FIELDS,
    PlanField,
    StrategyCompiler,
    StrategyModuleId,
    compliance_rendering,
)
from marketops.domain.sources import SOURCE_METHODS
from marketops.ports.acquisition import FetchMode

MODULE = REPO_ROOT / "marketops" / "application" / "research_strategy.py"
#: 计划链的另外两个模块（第 9 节扫的是**三个**）。计划是这三层一起写出来的，
#: 所以「顺手把池接进来」的入口有三个。
PLAN_MODULE = REPO_ROOT / "marketops" / "domain" / "research_plan.py"
PREVIEW_MODULE = REPO_ROOT / "marketops" / "application" / "plan_preview.py"
PROFILES_DIR = REPO_ROOT / "profiles"
DEFAULT_PROFILE = "uk-acme-natural-grass"

#: 真实 profile 编出来的那八格（第 7 节固化）。改这一份就是改「今天按什么跑」。
DECLARED_LOCALES = ("en-GB",)
DECLARED_TEMPLATES = (
    "{role} {venue_type} {region}",
    "{venue_type} {region} contact",
    "{venue_type} {region} staff",
    "{venue_type} {region} grounds team",
)
DECLARED_SOURCE_METHODS = ("search.engine.v1", "search.engine.site_scoped.v1", "crawl.site.v1")
DECLARED_ROLE_FUNCTIONS = (
    "grounds",
    "facilities",
    "maintenance",
    "operations",
    "bookings",
    "procurement",
)
DECLARED_ROLE_PRIORITIES = ("decision", "influencer", "operational", "gatekeeper")
DECLARED_DECISION_REFS = ("page_triage", "responsibility_relation", "role_function")


# --- 夹具 --------------------------------------------------------------------


def _set(path: tuple[str, ...], value: object) -> Callable[[dict], None]:
    """把 YAML 里的 ``path`` 设成 ``value``。"""

    def mutate(document: dict) -> None:
        node = document
        for key in path[:-1]:
            node = node[key]
        node[path[-1]] = value

    return mutate


def _drop(path: tuple[str, ...]) -> Callable[[dict], None]:
    """把 YAML 里的 ``path`` 删掉（键不存在也算成功）。"""

    def mutate(document: dict) -> None:
        node = document
        for key in path[:-1]:
            node = node[key]
        node.pop(path[-1], None)

    return mutate


def _bundle_with(
    root: Path,
    *,
    profile: Callable[[dict], None] | None = None,
    matrix: Callable[[dict], None] | None = None,
    scenario: Callable[[dict], None] | None = None,
) -> ProfileBundle:
    """复制真实 profile、按需改/加几块 YAML，再走**真实装载器**读回来。

    ``sort_keys=False`` 是**必须**的：``yaml.safe_dump`` 缺省按 key 排序，而
    ``page_discovery_hints`` 的档位顺序（high → medium → low）是**声明语义**
    （计划上那一格的顺序就是它）。缺省值会把夹具自己改成 high/low/medium ——
    于是「顺序是语义」这条判据验的是夹具，而不是实现。

    ``root`` 每调用一次要给一个新目录（两次装载同一份声明是两个不同的 bundle，
    见第 4 节 —— 那正是「同一份声明被装载两次」这条路径的形状）。
    """
    profiles_dir = root / "profiles"
    target = profiles_dir / DEFAULT_PROFILE
    profiles_dir.mkdir(parents=True, exist_ok=True)
    shutil.copytree(PROFILES_DIR / DEFAULT_PROFILE, target)

    def rewrite(name: str, mutate: Callable[[dict], None]) -> None:
        path = target / name
        document = yaml.safe_load(path.read_text(encoding="utf-8"))
        mutate(document)
        path.write_text(
            yaml.safe_dump(document, allow_unicode=True, sort_keys=False), encoding="utf-8"
        )

    if profile is not None:
        rewrite("profile.yaml", profile)
    if matrix is not None:
        rewrite("role-matrix.yaml", matrix)
    if scenario is not None:
        # ``scenario-matrix.yaml`` 是**新文件**，所以它不走 ``rewrite``（那只改写
        # 已存在的文件）。起手是一份合法的空矩阵，``scenario`` 往里写块 —— 一份
        # 写着零块的声明与「没有这份文件」是两件事（前者是入口开着、没人说话），
        # 所以起手的三个键都写全。
        document: dict = {
            "schema": SOP_SCENARIO_MATRIX_SCHEMA,
            "profile": DEFAULT_PROFILE,
            "blocks": {},
        }
        scenario(document)
        (target / "scenario-matrix.yaml").write_text(
            yaml.safe_dump(document, allow_unicode=True, sort_keys=False), encoding="utf-8"
        )

    settings = Settings(
        data_root=root / "runtime", profiles_dir=profiles_dir, profile=DEFAULT_PROFILE
    )
    return load_profile(settings)


def _context(bundle: ProfileBundle, **overrides: object) -> ResearchContext:
    """一个**讲清楚了**的上下文，``geo`` 跟着这份声明的国家走。

    取值刻意是市场中性的（与输入面那一份同样理由）。
    """
    declared = bundle.profile
    assert declared is not None, "夹具应当总是装载到声明块"
    base: dict[str, object] = {
        "geo": declared.country,
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


def _plan(
    bundle: ProfileBundle,
    *,
    context: ResearchContext | None = None,
    envelope: ResearchEnvelope | None = None,
):
    return research_compiler(bundle).compile(
        bundle=bundle,
        context=context if context is not None else _context(bundle),
        envelope=envelope if envelope is not None else envelope_of(bundle),
    )


def _value(plan, field: PlanField):
    """一格的值。``PlanField`` 的取值就是计划上的字段名（由 ``build_plan`` 保证）。"""
    return getattr(plan, field.value)


def _module(bundle: ProfileBundle, module_id: StrategyModuleId):
    for module in default_modules(bundle):
        if module.module_id is module_id:
            return module
    raise AssertionError(f"default_modules 里没有 {module_id.value}")


def _contribute(bundle: ProfileBundle, module_id: StrategyModuleId, **overrides: object):
    """**只调那一个模块**（不过编译器）。

    绕开编译器是有意的：编译器先跑 ``constraints_out_of_envelope``，于是「请求越界」
    这一类情形会在模块被调用之前就抛 —— 那样就分不清是这一层防住了，还是上一层。
    这一层自己的失败关闭，要在这一层里验。
    """
    context = _context(bundle, **overrides)
    return _module(bundle, module_id).contribute(
        context=context, envelope=envelope_of(bundle), bundle=bundle
    )


# --- 1. 接线与结构 -----------------------------------------------------------


def test_default_modules_are_exactly_the_four_with_a_declaration_source(
    bundle: ProfileBundle,
) -> None:
    """今天有声明来源的是哪四个 —— 点名，而不是「至少有四个」。

    两个没有声明来源的模块（``product_overlay`` / ``workstream_override``）**不在**
    名单里，理由写在 ``research_strategy`` 的模块 docstring 里：``product`` 块的三栏
    里前两栏已经是硬的 ``product_claims``，Workstream 这个概念全仓不存在。
    这条判据红的方式是「有人把其中某一个顺手补上」—— 那一刻它就得先有声明来源。
    """
    modules = default_modules(bundle)
    assert {module.module_id for module in modules} == {
        StrategyModuleId.GEO_LOCALE_SOURCE,
        StrategyModuleId.SCENARIO,
        StrategyModuleId.OBJECTIVE,
        StrategyModuleId.ACCESS,
    }
    # 类也是那四个：一个模块 id 变而类没变（或者反过来）会让「谁写的」与
    # 「读的是哪一份实现」在报告上分开走。
    assert {type(module) for module in modules} == {
        GeoLocaleSourceProfile,
        ScenarioProfile,
        ObjectivePolicy,
        AccessStrategy,
    }
    assert StrategyModuleId.PRODUCT_OVERLAY not in {
        module.module_id for module in modules
    }
    assert StrategyModuleId.WORKSTREAM_OVERRIDE not in {
        module.module_id for module in modules
    }


def test_the_four_modules_cover_every_writable_field_exactly_once(
    bundle: ProfileBundle,
) -> None:
    """八格可写的格子，每一格恰好一个写者 —— 表说谁来写，交上来的就是谁。

    这一条是「四个模块覆盖八格」这句话的执行器：少一格会在计划上变成缺格
    （``build_plan`` 也拦），多写一格会被 ``FIELD_OWNER`` 与 ``FIELD_OVERRIDERS``
    一起判成越权。写在这里是为了让「模块集合变了但没人重看那张表」当场红。
    """
    owner_of: dict[PlanField, list[StrategyModuleId]] = {}
    for module in default_modules(bundle):
        for contribution in module.contribute(
            context=_context(bundle), envelope=envelope_of(bundle), bundle=bundle
        ):
            owner_of.setdefault(contribution.field, []).append(module.module_id)

    assert set(owner_of) == MODULE_WRITABLE_FIELDS
    for field, owners in owner_of.items():
        assert owners == [FIELD_OWNER[field]], f"{field.value} 的写者与表不一致：{owners}"


def test_the_only_overriders_are_the_two_modules_that_do_not_exist_yet(
    bundle: ProfileBundle,
) -> None:
    """表上的覆盖者恰好是那两个**没有声明来源**的模块 —— 与「四个模块」绑在一起。

    覆盖权是表上的一个位置，不是「等有了再想」：``page_archetypes`` 与
    ``role_priorities`` 今天就写明了「更具体的可以改」，而那两个更具体的模块
    （产品叠加层、工作流覆盖层）还没有声明来源。这条判据把两句话对上：进
    :func:`default_modules` 的是**归属者**，不该同时是覆盖者；而覆盖者名单只有
    那两个名字。往名单里加第五个模块的那一天它会红 —— 那一刻要回答的正是
    「它的覆盖权是什么」。
    """
    overriders = {name for names in FIELD_OVERRIDERS.values() for name in names}
    assert overriders == {
        StrategyModuleId.PRODUCT_OVERLAY,
        StrategyModuleId.WORKSTREAM_OVERRIDE,
    }
    assert overriders.isdisjoint({module.module_id for module in default_modules(bundle)})


def test_nothing_overrides_anything_here(bundle: ProfileBundle) -> None:
    """这份声明的计划里没有一条覆盖记录 —— 因为两个覆盖者今天不存在。

    这不是「覆盖机制没生效」：``FIELD_OVERRIDERS`` 里那两格的覆盖者是
    ``product_overlay`` 与 ``workstream_override``，而它们不在
    :func:`default_modules` 里。等它们真的有了声明来源，这一条会跟着红，
    而那时要看的是「它们盖得对不对」。
    """
    assert _plan(bundle).overrides == ()


def test_building_a_module_needs_only_a_declaration(bundle: ProfileBundle) -> None:
    """构造一个模块**只需要一份声明** —— 没有 key、没有客户端、没有时钟。

    关闭条件 1 那句「preview 零 provider 调用」在这一层不是纪律、也不是替身记出来
    的 0：模块能拿到的输入里没有任何一条通向外部世界。这条判据按 dataclass 的字段
    扫，所以「顺手加一个 client 参数」这种改动会红在这里。
    """
    for module in default_modules(bundle):
        signature = inspect.signature(type(module).__init__)
        assert list(signature.parameters) == ["self", "bundle"], (
            f"{type(module).__name__} 的构造参数不止一份声明：{list(signature.parameters)}"
        )
        assert [field.name for field in dataclasses.fields(module)] == ["bundle"]


def test_the_wiring_order_does_not_reach_the_plan(bundle: ProfileBundle) -> None:
    """接线的**排列**不进计划：反着接，编出来的是逐字同一份计划。

    这是 ``StrategyCompiler`` 类 docstring 那句「那份元组的排列不进结果」的执行器。
    少了它，那句话在测试上是空的：``default_modules`` 今天本来就是
    :data:`MODULE_PRIORITY` 的序，「排过一次」与「照抄接线」给出同一份结果 ——
    谁把那次排序删掉都不会红。

    版本那一栏也在射程内，而且它今天更靠这一步：贡献是按 ``self.modules`` 的顺序
    收上来的，``domain/research_plan.py::_module_revisions`` 再从贡献上取版本 ——
    少了那一次排序，这一栏就是倒的，``digest`` 跟着分叉。
    """
    forward = _plan(bundle)
    backward = StrategyCompiler(modules=tuple(reversed(default_modules(bundle)))).compile(
        bundle=bundle, context=_context(bundle), envelope=envelope_of(bundle)
    )
    assert [item.module_id for item in backward.modules] == [
        item.module_id for item in forward.modules
    ], "计划上那一栏的先后是 MODULE_PRIORITY 的先后，不是接线的先后"
    assert backward.modules == forward.modules
    assert backward.digest == forward.digest
    assert backward == forward


def test_the_read_record_cannot_reach_the_bundle_or_the_disk() -> None:
    """格子的算法够不到声明，也够不到盘 —— 版本因此盖住了它读的每一样。

    :meth:`_Read.cells` 是**记录上的方法**，记录里没有 ``bundle``；而模块级的
    那几个 ``_*_cell`` 只收那份记录。这条判据按 AST 扫这两类函数体：里面出现
    ``.bundle`` 或任何读盘的调用就红。它防的是一个很安静的形状 —— 一个偷偷再读
    一块声明的实现，会把那一块**排除在版本之外**（改了那块声明，版本不变）。
    """
    tree = ast.parse(MODULE.read_text(encoding="utf-8"))
    bodies: dict[str, ast.AST] = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.ClassDef):
            continue
        for item in node.body:
            if (
                isinstance(item, ast.FunctionDef)
                and item.name == "cells"
                and any(
                    isinstance(base, ast.Name) and base.id == "_Read" for base in node.bases
                )
            ):
                bodies[f"{node.name}.cells"] = item
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name.endswith("_cell"):
            bodies[node.name] = node

    # 四条记录各一个 ``cells``，八格各一个 ``_*_cell`` —— 少一个都说明扫描在空转。
    assert len(bodies) == 12, f"扫描没读到十二个格子算法：{sorted(bodies)}"

    reaches_bundle: list[str] = []
    does_io: list[str] = []
    for name, node in bodies.items():
        for item in ast.walk(node):
            if isinstance(item, ast.Attribute) and item.attr == "bundle":
                reaches_bundle.append(f"{name}:{item.lineno}")
            if isinstance(item, ast.Call):
                called = item.func
                called_name = (
                    called.id if isinstance(called, ast.Name) else getattr(called, "attr", "")
                )
                if called_name in {"open", "read_text", "read_bytes", "glob", "rglob", "load_profile"}:
                    does_io.append(f"{name}:{item.lineno}")
    assert not reaches_bundle, f"格子的算法够到了声明：{reaches_bundle}"
    assert not does_io, f"格子的算法在读盘：{does_io}"


def test_this_layer_cannot_reach_an_adapter_or_a_socket() -> None:
    """AST 扫描：这个模块 import 的东西里没有一个住在 adapters，也没有一个能联网。

    ``ports`` 不在禁令里，而且这是有意的：``FetchMode`` 就住
    ``ports/acquisition.py``（那个模块的头自己写着「``FetchMode`` 住在本模块，
    因为它是被多方读的词汇」）。真正要防的是 **adapter** —— 一个模块能自己发一次
    抓取，计划就成了一份可以顺便花钱的东西。
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
        if ".adapters" in name
        or name.split(".")[0] in {"requests", "httpx", "socket", "urllib", "subprocess", "sqlite3"}
    )
    assert not forbidden, f"这一层不得够到 adapter 或网络：{forbidden}"
    assert any("ports" in name for name in imported), (
        "扫描没读到那一处 ports import —— 判据可能在空转"
    )


# --- 2. 逐格追到声明 ---------------------------------------------------------


def test_locales_come_from_the_declared_search_locales(tmp_path: Path) -> None:
    """语言那一格是 ``search.locales``，**按声明顺序**，原样标签。"""
    two = _bundle_with(
        tmp_path / "two", profile=_set(("search", "locales"), ["en-GB", "de-DE"])
    )
    assert _plan(two).locales == ("en-GB", "de-DE")

    reversed_order = _bundle_with(
        tmp_path / "reversed", profile=_set(("search", "locales"), ["de-DE", "en-GB"])
    )
    assert _plan(reversed_order).locales == ("de-DE", "en-GB")


def test_a_context_narrows_the_declared_locales(tmp_path: Path) -> None:
    """:attr:`ResearchContext.candidate_locales` 是**收窄**声明，不是新开一组。

    空着 = 用声明的那几种（``ResearchContext`` 的 docstring 逐字如此）：这一格的
    语义是收窄，收窄成空集没有意义。所以空着与「要全部」在这次编译里是同一个答案，
    而它们**不是**同一个计划上的两句话（计划上写的是收窄之后的那几种）。
    """
    bundle = _bundle_with(
        tmp_path, profile=_set(("search", "locales"), ["en-GB", "de-DE", "fr-FR"])
    )
    assert _plan(bundle).locales == ("en-GB", "de-DE", "fr-FR")
    # 点名一种：计划上只剩它，且**顺序跟着声明**而不是跟着请求。
    narrowed = _plan(bundle, context=_context(bundle, candidate_locales=("fr-FR", "en-GB")))
    assert narrowed.locales == ("en-GB", "fr-FR")


def test_query_families_are_the_declared_templates_verbatim(tmp_path: Path) -> None:
    """查询族是 ``search.query_templates`` 的原文 —— 占位符也原样保留。

    与 ``load_open_web_templates`` 逐字比对（**不是**自己再读一遍 YAML）：两个读法
    总有一天会分叉，而那时计划上写的模板与实际发出去的模板会是两句话。
    """
    bundle = _bundle_with(tmp_path)
    assert _plan(bundle).query_families == DECLARED_TEMPLATES
    assert _plan(bundle).query_families == load_open_web_templates(bundle)

    changed = _bundle_with(
        tmp_path / "changed",
        profile=_set(("search", "query_templates"), ["{venue_type} {region} a", "{role} b"]),
    )
    assert _plan(changed).query_families == ("{venue_type} {region} a", "{role} b")


def test_source_methods_are_the_routes_the_declaration_opens(bundle: ProfileBundle) -> None:
    """来源方法那一格 = 声明开出来的那几条路，按**注册表**顺序。

    ``import.register.v1``（导入一份名册）与 ``research.skill.v1``（研究件）今天
    没有任何声明给它们入口：前者要有一份别人交进来的导出，后者要一次研究作业 ——
    两者都不是「这一次按什么从开放网络找行」。它们出现在这一格上，会让计划声称这次
    要去做两件没人安排的事。
    """
    assert _plan(bundle).source_methods == DECLARED_SOURCE_METHODS
    assert "import.register.v1" not in _plan(bundle).source_methods
    assert "research.skill.v1" not in _plan(bundle).source_methods
    # 顺序是注册表的顺序，不是路线表的顺序。
    registry_order = [method for method in SOURCE_METHODS if method in DECLARED_SOURCE_METHODS]
    assert list(_plan(bundle).source_methods) == registry_order


def test_page_archetypes_are_the_declared_hints(tmp_path: Path) -> None:
    """页面原型：档位是 ``page_discovery_hints`` 的键、glob 是它的值，按声明顺序。"""
    plan = _plan(_bundle_with(tmp_path))
    assert [archetype.tier for archetype in plan.page_archetypes] == ["high", "medium", "low"]
    assert plan.page_archetypes[0].patterns[0] == "*/contact*"

    renamed = _bundle_with(
        tmp_path / "renamed",
        profile=_set(("page_discovery_hints",), {"first": ["*/a*"], "second": ["*/b*"]}),
    )
    assert [archetype.tier for archetype in _plan(renamed).page_archetypes] == [
        "first",
        "second",
    ]


def test_role_functions_are_the_declared_business_functions(bundle: ProfileBundle) -> None:
    """角色口径那一格 = ``role_groups`` 逐组声明的 ``business_function``，去重、按序。

    **只读 ``groups``，不读 ``excluded_groups``**：后者是一张「不得进入有效路线」的
    排除表（含 finance / admissions / ``unknown``），把它读进这一格等于把禁令反过来
    当成要找的口径。
    """
    assert _plan(bundle).role_functions == DECLARED_ROLE_FUNCTIONS
    assert "finance" not in _plan(bundle).role_functions
    assert "unknown" not in _plan(bundle).role_functions


def test_an_undecided_group_is_dropped_and_counted(tmp_path: Path) -> None:
    """没写业务口径的组**不进**这一格，但它的条数进**理由**。

    丢掉而不计数的话，「这个 profile 有一组没写口径」在计划上完全看不出来 ——
    而那是要回去改声明的事。丢掉之后剩下的那几组仍按**声明顺序**（``grounds``
    从第一位挪到第二位，因为第一位那一组今天没有口径）。
    """

    def first_group_undecided(document: dict) -> None:
        document["role_groups"][0]["business_function"] = "unknown"

    plan = _plan(_bundle_with(tmp_path, matrix=first_group_undecided))
    assert plan.role_functions == (
        "facilities",
        "grounds",
        "maintenance",
        "operations",
        "bookings",
        "procurement",
    )
    reason = next(
        item.reason for item in plan.provenance if item.field is PlanField.ROLE_FUNCTIONS
    )
    assert "1 组没写业务口径" in reason

    # 反控：真实 profile 一组都没缺，所以理由里不该出现那句话。
    base_reason = next(
        item.reason
        for item in _plan(_bundle_with(tmp_path / "base")).provenance
        if item.field is PlanField.ROLE_FUNCTIONS
    )
    assert "没写业务口径" not in base_reason


def test_role_priorities_are_the_declared_class_order(bundle: ProfileBundle) -> None:
    """先后那一格 = ``priority.class_order``，原样。

    同族的 ``weight`` **不进**这一格：它给的是每组一个权重，而这一格要答的是
    「哪一类更靠前」，把权重变成次序要再选一个比较办法 —— 那是一条没人认过的规则。
    """
    assert _plan(bundle).role_priorities == DECLARED_ROLE_PRIORITIES


def test_decision_refs_are_the_declared_task_names_not_the_prose(tmp_path: Path) -> None:
    """决策题那一格装的是**任务名**，不是题面。

    改一句题面不该让这一格变，也不该让这一格的模块版本变：题面自己有一版
    （``application/decision_specs.py::DecisionSpec.version``），这一格答的是
    「这次要判哪几件事」。
    """
    bundle = _bundle_with(tmp_path)
    assert _plan(bundle).decision_refs == DECLARED_DECISION_REFS

    def rewrite_prose(document: dict) -> None:
        for task in document["decisions"].values():
            if isinstance(task, dict):
                task["instructions"] = "完全另一段题面"

    rewritten = _bundle_with(tmp_path / "rewritten", profile=rewrite_prose)
    assert _plan(rewritten).decision_refs == DECLARED_DECISION_REFS
    assert _revisions(rewritten)[StrategyModuleId.OBJECTIVE.value] == _revisions(bundle)[
        StrategyModuleId.OBJECTIVE.value
    ]


def test_acquisition_modes_are_the_approved_fetch_modes(bundle: ProfileBundle) -> None:
    """获取方式那一格：HTTP 直连与浏览器（次序即降级次序），``auto`` 不在其中。

    反控是必要的：「``auto`` 不在这一格」在一个 ``auto`` 不存在的闭集上恒真。
    """
    assert _plan(bundle).acquisition_modes == (FetchMode.HTTP.value, FetchMode.BROWSER.value)
    assert FetchMode.AUTO.value not in _plan(bundle).acquisition_modes
    assert FetchMode.AUTO.value in {mode.value for mode in FetchMode}
    # 闭集里没有「登录之后取」这一级 —— 任何声明都批不出它来。
    assert not any("login" in mode.value for mode in FetchMode)


def test_the_access_module_and_the_declared_scope_share_one_rendering(
    tmp_path: Path,
) -> None:
    """``compliance`` 的渲染只有一处，所以硬格与访问层的理由逐字同源。

    两处各渲染一遍的话，排序规则总有一天会分叉，而计划上会出现同一块声明的两种写法。
    """
    bundle = _bundle_with(
        tmp_path,
        profile=_set(("compliance",), {"zz_last": True, "aa_first": 1}),
    )
    plan = _plan(bundle)
    assert plan.access_permissions == compliance_rendering(bundle.profile.compliance)
    access_reason = next(
        item.reason for item in plan.provenance if item.field is PlanField.ACQUISITION_MODES
    )
    assert "aa_first=1、zz_last=True" in access_reason


# --- 3. 版本 -----------------------------------------------------------------


def _revisions(bundle: ProfileBundle) -> dict[str, str]:
    """每个模块的版本 —— 从它**自己交出来的贡献**上取。

    ``contribute`` 是唯一的读入口（``_Declared`` 上没有 ``revision`` 属性，
    ``StrategyModule`` 协议里也没有）：一条贡献自己就带着「谁写的、哪一版」，所以
    「模块版本」不再是一次与格子无关的第二次阅读。助手仍然**逐模块**读（不过编译器，
    理由同 ``_contribute``），好让「改了它读的那一块 → 版本变」直接判在那一层。
    """
    context = _context(bundle)
    envelope = envelope_of(bundle)
    return {
        contribution.module_id.value: contribution.revision
        for module in default_modules(bundle)
        for contribution in module.contribute(context=context, envelope=envelope, bundle=bundle)
    }


def test_there_is_exactly_one_way_to_read_a_declaration() -> None:
    """读声明只有一处**实现**（``_Declared._read()``），版本只有一处**出口**（贡献）。

    C1 之前这里有过第二条路 —— ``_Declared.revision`` 属性：``plan.modules`` 那一栏
    走它、格子走 ``contribute``，两次阅读各算一个版本号，而它们在计划上**分不开**
    （那一栏写的是第二次读出来的号，格子里的值来自第一次）。属性删掉之后，这条纪律
    从「大家的自觉」变成结构 —— 但结构也要有执行器，否则它下一次会以另一种名字回来。

    ## 2026-09-29：第二个**入口**出现了，这条判据跟着改写（不是把 1 改成 2）

    ``plan_source_methods``（§B16 关闭条件 7）要回答「这份声明开出了哪几条路」，而它
    的两个读者（``get_assignment`` 交出去的那一步、三条研究工具与 ``decide`` 校验的
    那一步）**都拿不到 ``ResearchContext``** —— 那条路必须经过 ``contribute``，而
    ``contribute`` 要 context 与 envelope。虚构一份 envelope 去走它，比多读一次声明
    坏得多（那等于给一次没人批过的收窄发通行）。所以它自己 ``_read()`` 了一次。

    现在**唯一的入口是 ``contribute``**（第二个断言：``self._read()`` 只在那里被调
    一次），而 ``plan_source_methods`` **不是第二个版本**，因为版本压根不经过它：
    ``_Read.revision`` 唯一的出口是 ``contribute`` 里的 ``_as_contribution``（第五个
    断言按 AST 数它），第四个断言则钉住 ``plan_source_methods`` 的函数体碰不到那两者
    —— 它读的是同一份声明、走的是同一段 ``_read()``，但它手上只有格子（``values``），
    没有号。原判据要挡的那件事 ——「两次阅读各算一个版本号，而它们在计划上分不开」
    —— 在这一处因此是**结构上无从发生**，不是这一处的自觉。

    断言因此分成六条，每条要么是原来那条的一半、要么是它的加强：

    1. 四个模块各只有一个属性（``module_id``，没有 ``revision``）—— 原样；
    2. ``self._read()``（模块读自己）全文只有一处调用点，且在 ``contribute`` 里 ——
       「版本与格子出自同一次阅读」那条路只有一个入口；
    3. 模块**之外**的 ``_read()`` 只允许 ``plan_source_methods`` 里那一处 ——
       第二个入口是被点名的，不是「允许再来几个」；
    4. ``plan_source_methods`` 的函数体**不许碰** ``_as_contribution`` 与 ``.revision``
       —— 允许它读，不允许它把这次读取变成一条带版本的贡献；
    5. ``_as_contribution`` 全文只有一处调用点，也在 ``contribute`` 里 ——
       **版本只有一个出口**（这一条是这次改写新加的，它替下了原来那条计数的一半）；
    6. 扫描真的读到了那四个类（计数在空转时会静默通过）。

    宿主只认**一层**：模块级函数与类的一层方法。今天没有更深的嵌套，而真出现时
    下面那句「有调用点归属不到宿主函数」会先红，不是静默地把调用点挂到外层函数上。

    行为那一半在下面那条 ``test_the_step_list_handed_out_is_the_widest_reading_of_the_plan``：
    这条只说「第二个入口拿不到号」，那条说「它拿出来的东西就是计划上那一格」。
    """
    tree = ast.parse(MODULE.read_text(encoding="utf-8"))
    declarations = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.ClassDef)
        and any(isinstance(base, ast.Name) and base.id == "_Declared" for base in node.bases)
    ]
    assert len(declarations) == 4, f"扫描没读到四个模块：{[node.name for node in declarations]}"

    for node in declarations:
        properties = [
            item.name
            for item in node.body
            if isinstance(item, ast.FunctionDef)
            and any(
                isinstance(decorator, ast.Name) and decorator.id == "property"
                for decorator in item.decorator_list
            )
        ]
        assert properties == ["module_id"], (
            f"{node.name} 上的属性不是只有 module_id：{properties} ——"
            "``revision`` 又回到模块上的话，那就是第二条读法（见 _Declared 的 docstring）"
        )

    # 每个调用点的宿主函数。先建这张表，下面四条断言才能说「在**哪里**」，而不是
    # 只说「有几处」—— 计数答不出「这一处是版本那条路吗」。
    owners: dict[int, str] = {}
    hosts = [item for item in tree.body if isinstance(item, ast.FunctionDef)] + [
        method
        for node in ast.walk(tree)
        if isinstance(node, ast.ClassDef)
        for method in node.body
        if isinstance(method, ast.FunctionDef)
    ]
    for host in hosts:
        for inner in ast.walk(host):
            if isinstance(inner, ast.Call):
                owners.setdefault(inner.lineno, host.name)

    reads = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "_read"
    ]
    assert reads, "``_read()`` 一处调用点都没扫到 —— 空转的计数会静默通过"
    assert all(node.lineno in owners for node in reads), (
        f"有调用点归属不到宿主函数：{[node.lineno for node in reads if node.lineno not in owners]}"
        " —— 宿主表只认一层嵌套，这里出现了更深的一层"
    )

    self_reads = sorted(
        node.lineno
        for node in reads
        if isinstance(node.func.value, ast.Name) and node.func.value.id == "self"
    )
    assert [owners[line] for line in self_reads] == ["contribute"], (
        f"``self._read()`` 的调用点是 {[(line, owners[line]) for line in self_reads]} ——"
        "模块读自己这条路只允许 ``contribute`` 一处，"
        "否则「版本与格子出自同一次阅读」就有了第二个入口（见 _Declared 的 docstring）"
    )
    outer = sorted({node.lineno for node in reads} - set(self_reads))
    assert [owners[line] for line in outer] == ["plan_source_methods"], (
        f"模块之外的 ``_read()`` 出现在 {[(line, owners[line]) for line in outer]} ——"
        "只允许 ``plan_source_methods`` 里那一处（§B16 关闭条件 7 的那一次读取）"
    )

    handed = [
        item
        for item in tree.body
        if isinstance(item, ast.FunctionDef) and item.name == "plan_source_methods"
    ]
    assert len(handed) == 1, "``plan_source_methods`` 不在了，或者不止一个"
    touched = [
        node.lineno
        for node in ast.walk(handed[0])
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "_as_contribution"
        )
        or (isinstance(node, ast.Attribute) and node.attr == "revision")
    ]
    assert touched == [], (
        f"``plan_source_methods`` 碰了版本那一侧（行 {touched}）——"
        "允许它读一次声明，不允许那次读取变成一条带版本的贡献"
    )

    exits = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "_as_contribution"
    ]
    assert [owners.get(node.lineno) for node in exits] == ["contribute"], (
        f"``_as_contribution`` 的调用点是 {[(node.lineno, owners.get(node.lineno)) for node in exits]}"
        " —— 版本只有一个出口：``contribute`` 交出来的那些贡献"
        "（第二条出口意味着「计划上写着 A 的版本、格子里的值来自 B」）"
    )


def test_the_step_list_handed_out_is_the_widest_reading_of_the_plan(
    bundle: ProfileBundle, tmp_path: Path
) -> None:
    """第二个入口拿出来的东西，**就是计划上那一格**（收窄时是它的超集）。

    上一条判据是**结构**的：``plan_source_methods`` 只读声明、拿不到版本。结构答不出
    另一半 —— 它大可以读同一份声明，然后算出一个「看起来合理但和计划不一样」的集合。
    这一条从**行为**上钉住它：

    * 这份真实 profile 没有 ``scenario-matrix.yaml``（无场景收窄），所以
      ``plan_source_methods(bundle)`` 与 ``_plan(bundle).source_methods`` 必须**逐字
      相同**。这是「服务端交给 worker 的那一份步骤」与「计划里写的那一格」是同一次
      阅读这件事唯一能直接观测的地方。
    * 一份**写了块**的声明里，计划那一格被块收到只剩一条，而 ``plan_source_methods``
      仍是这份声明开出来的全部 —— 即 ``_source_methods_cell`` 的 docstring 里那句
      「它是**许可**」：一次具体的 Run 可以比它窄，比对因此只往严的一侧错，不会放行
      一个声明从没开过的步骤。方向反了（交出去的比计划窄）就会让 worker 照着服务端
      交出的那一步填、工具把它拒掉，而两边各自都「做完了」—— 这条判据的红就是它。

    反控在上面那条 ``== wide.source_methods[:1]``：块没有真的收到窄，下面那半会在
    原地成立（等号两边是同一个集合时，「交给 worker 的是这一格」是一句空话）。
    """
    assert plan_source_methods(bundle) == _plan(bundle).source_methods, (
        "无场景收窄时，交出去的步骤与计划上那一格不是同一个集合"
    )

    base = _bundle_with(tmp_path / "base")
    material = _narrowable(base)
    wide = _plan(base)
    assert len(wide.source_methods) > 1, "基线只剩一条路 —— 下面那条收窄是原地成立的"
    narrowed = _bundle_with(
        tmp_path / "scenario",
        scenario=_scenario({"k12": _block(material, sources=list(wide.source_methods[:1]))}),
    )
    narrow = _plan(narrowed)
    assert narrow.source_methods == wide.source_methods[:1], (
        "块没把这一格收到窄 —— 这条判据的反控，先红它"
    )
    handed = plan_source_methods(narrowed)
    assert handed == wide.source_methods, (
        f"交给 worker 的那一份是 {handed}，不是这份声明开出来的全部 "
        f"{wide.source_methods} —— 它是许可，不是这一轮的 Run："
        "跟着这一轮的 Run 一起收窄，就与「一次具体的 Run 可能比它更窄，"
        "拿宽的那一份去比对不会放行一个声明从没开过的步骤」这句话相反了"
    )


def test_each_module_revision_follows_exactly_what_it_reads(tmp_path: Path) -> None:
    """逐模块、逐块：改它读的 → 版本变；改别的 → 版本**不**变。

    两个方向都必要。只看「改了会变」的话，一个「把整份声明摘要一遍」的实现全绿 ——
    而它会让每一次无关的声明改动都改掉所有模块的版本，于是「这一次是哪一个模块换了」
    在计划上答不出来。只看「无关的不变」的话，一个常数版本全绿。

    第三个方向在最后单写：**读不出来的声明上，版本也问不出来** —— 版本住在贡献上，
    而贡献交不出格子就没有贡献。
    """
    base = _bundle_with(tmp_path / "base")

    cases = {
        "search.locales": (
            _set(("search", "locales"), ["de-DE"]),
            ("geo_locale_source_profile",),
        ),
        "search.query_templates": (
            _set(("search", "query_templates"), ["{role} {region} x"]),
            ("geo_locale_source_profile",),
        ),
        # 页面提示的**内容**改了 → 只有场景层动：地理/来源层读的是这一块**在不在**
        # （它只拿它当「有没有一条已知域名内的路」的信号），而内容归场景层。
        "page_discovery_hints 的内容": (
            _set(("page_discovery_hints",), {"high": ["*/a*"]}),
            ("scenario_profile",),
        ),
        "compliance": (
            _set(("compliance",), {"public_pages_only": False}),
            ("access_strategy",),
        ),
    }
    for label, (mutation, expected_movers) in cases.items():
        variant = _bundle_with(tmp_path / label.replace(".", "-"), profile=mutation)
        before, after = _revisions(base), _revisions(variant)
        moved = tuple(name for name in before if before[name] != after[name])
        assert moved == expected_movers, f"改 {label} 之后动的模块是 {moved}"

    # 「这一块整个没了」是**另一件事**，不能按上面那种写法判：少了 ``page_discovery_hints``
    # 的声明上，场景层交不出格子（页面原型那一格没有依据），因此它的版本也问不出来 ——
    # 读与写是同一个入口（``contribute``）：一个交不出格子的模块报不出自己的版本。
    # 这一条以前判得出来，是因为版本曾有一条与格子无关的独立读法（``module.revision``）。
    without_hints = _bundle_with(
        tmp_path / "no-hints", profile=_drop(("page_discovery_hints",))
    )
    geo_before = _contribute(base, StrategyModuleId.GEO_LOCALE_SOURCE)[0].revision
    geo_after = _contribute(without_hints, StrategyModuleId.GEO_LOCALE_SOURCE)[0].revision
    assert geo_before != geo_after, "地理/来源层读的是这一块在不在，少了它版本必须动"
    with pytest.raises(ConfigError, match="page_discovery_hints"):
        _revisions(without_hints)

    matrix_mutations = {
        "role_groups.business_function": (
            _set(
                ("role_groups",),
                [
                    {
                        "id": "only",
                        "class": "decision",
                        "weight": 1,
                        "business_function": "grounds",
                        # 一个不与真实词表互为子串的探针词：装载器会拒「两个分属不同
                        # 类的词互为子串」（子串匹配下谁赢取决于词表顺序）。
                        "match": ["zzzq probe"],
                    }
                ],
            ),
            ("scenario_profile",),
        ),
        "priority.class_order": (
            _set(("priority", "class_order"), ["influencer", "decision"]),
            ("scenario_profile",),
        ),
    }
    for label, (mutation, expected_movers) in matrix_mutations.items():
        variant = _bundle_with(tmp_path / label.replace(".", "-"), matrix=mutation)
        before, after = _revisions(base), _revisions(variant)
        moved = tuple(name for name in before if before[name] != after[name])
        assert moved == expected_movers, f"改 {label} 之后动的模块是 {moved}"

    decisions = _bundle_with(
        tmp_path / "decisions",
        profile=_set(
            ("decisions",),
            {"page_triage": {"instructions": "x", "options": {"yes": "y"}}},
        ),
    )
    before, after = _revisions(base), _revisions(decisions)
    assert tuple(name for name in before if before[name] != after[name]) == ("objective_policy",)


def test_a_revision_is_a_content_digest_not_a_path(tmp_path: Path) -> None:
    """同一份声明在两个目录里装载两次 → 同样的版本、同样的计划摘要。

    版本要答的是「两次编译读到的是不是同一份策略」。按路径或时刻算的话，同一份声明
    换个目录就不再相等 —— 而报告上会表现为「策略变了」，实际上什么都没变。
    """
    left = _bundle_with(tmp_path / "left")
    right = _bundle_with(tmp_path / "right")
    assert _revisions(left) == _revisions(right)
    assert _plan(left).fingerprint_digest == _plan(right).fingerprint_digest
    assert left.scene_version == right.scene_version


def test_the_plan_records_every_module_with_its_revision(bundle: ProfileBundle) -> None:
    """计划上那一栏：每个**接线的**模块一条，按 ``MODULE_PRIORITY`` 排，版本取自贡献。

    名单取自接线（不是从贡献里推）：一个接线里的模块交零格是接线坏了，那件事由
    ``domain/research_plan.py::_module_revisions`` 当场抛出来，而**不是**在这里少一行
    —— 少一行在报告上读作「这次参与的模块少了一个」。
    """
    plan = _plan(bundle)
    revisions = _revisions(bundle)
    wired = [module.module_id for module in default_modules(bundle)]
    assert wired == sorted(wired, key=MODULE_PRIORITY.index), (
        "夹具的前提：接线本来就是表序（反着接的对照在 test_the_wiring_order_does_not_reach_the_plan）"
    )
    assert [(item.module_id, item.revision) for item in plan.modules] == [
        (module_id, revisions[module_id.value]) for module_id in wired
    ]


def test_two_compiles_of_one_declaration_are_identical(bundle: ProfileBundle) -> None:
    """同一份声明、同一份上下文，两次编译逐字相同（关闭条件 1 的前半）。"""
    assert _plan(bundle) == _plan(bundle)


# --- 4. 模块与声明的身份 -----------------------------------------------------


def test_a_module_from_another_declaration_refuses_to_contribute(tmp_path: Path) -> None:
    """甲声明的模块配上乙声明的 bundle → 拒绝，且点名两份声明。"""
    mine = _bundle_with(tmp_path / "mine")
    other = _bundle_with(
        tmp_path / "other", profile=_set(("search", "locales"), ["de-DE", "fr-FR"])
    )
    assert mine.scene_version != other.scene_version, "两份声明应当不同（夹具的前提）"

    with pytest.raises(ConfigError) as caught:
        _module(mine, StrategyModuleId.GEO_LOCALE_SOURCE).contribute(
            context=_context(other), envelope=envelope_of(other), bundle=other
        )
    message = str(caught.value)
    assert mine.scene_version in message and other.scene_version in message


def test_loading_the_same_declaration_twice_is_not_a_mismatch(tmp_path: Path) -> None:
    """同一份声明装载两次（两个对象、两个目录）→ 正常编，**不**误报。

    这条是上一条的反面。判据按 ``(profile_id, scene_version)`` 比内容而不是比
    ``is``：两个调用点各自 ``load_profile`` 是完全正常的路径，把那种情形判成错误
    会让这条判据在一条正当路径上响 —— 而一条会误报的判据，迟早会被撞上它的人关掉。
    """
    left = _bundle_with(tmp_path / "left")
    right = _bundle_with(tmp_path / "right")
    assert left is not right
    plan = StrategyCompiler(modules=default_modules(left)).compile(
        bundle=right, context=_context(right), envelope=envelope_of(right)
    )
    assert plan.locales == DECLARED_LOCALES


# --- 5. 失败关闭（每一格画不出来时都抛，且点名叫哪一块） ---------------------


def _all_unknown(document: dict) -> None:
    for group in document["role_groups"]:
        group["business_function"] = "unknown"


FAILURE_CASES = {
    # 这里**没有**「``search.locales: []``」这一条：空表在**装载期**就被拒了
    # （``adapters/profile_loader.py`` 写成「空表不是『没声明』」）。这一层要防的是
    # **不写这个键**那一半 —— 它在盘上是合法的，而它让语言这一格画不出来。
    "search.locales 缺": (
        StrategyModuleId.GEO_LOCALE_SOURCE,
        _drop(("search", "locales")),
        None,
        "search.locales",
    ),
    "search.query_templates 缺": (
        StrategyModuleId.GEO_LOCALE_SOURCE,
        _drop(("search", "query_templates")),
        None,
        "query_templates",
    ),
    "search.query_templates 空": (
        StrategyModuleId.GEO_LOCALE_SOURCE,
        _set(("search", "query_templates"), []),
        None,
        "query_templates",
    ),
    "page_discovery_hints 缺": (
        StrategyModuleId.SCENARIO,
        _drop(("page_discovery_hints",)),
        None,
        "page_discovery_hints",
    ),
    "role_groups 全没写口径": (
        StrategyModuleId.SCENARIO,
        None,
        _all_unknown,
        "业务口径",
    ),
    "priority.class_order 缺": (
        StrategyModuleId.SCENARIO,
        None,
        _drop(("priority", "class_order")),
        "class_order",
    ),
    "priority.class_order 含 excluded": (
        StrategyModuleId.SCENARIO,
        None,
        _set(("priority", "class_order"), ["decision", "excluded"]),
        "excluded",
    ),
    "priority.class_order 含闭集外的名字": (
        StrategyModuleId.SCENARIO,
        None,
        _set(("priority", "class_order"), ["decider"]),
        "decider",
    ),
    "decisions 缺": (
        StrategyModuleId.OBJECTIVE,
        _drop(("decisions",)),
        None,
        "decisions",
    ),
    # 这里**没有**「``decisions: {}``」这一条：整块写成空映射在**装载期**就被拒了
    # （``adapters/profile_loader.py`` 写成「要么整个不写，要么是一个非空映射」），
    # 所以它构造不出一份能走到这一层的 bundle。拿一条装载期的判据冒充这一层的
    # 失败关闭，会让「这一层拒绝零个判定题」这句话从来没有被执行过。
}


@pytest.mark.parametrize(
    ("module_id", "profile", "matrix", "keyword"),
    FAILURE_CASES.values(),
    ids=FAILURE_CASES.keys(),
)
def test_a_declaration_that_cannot_fill_a_cell_is_refused(
    tmp_path: Path,
    module_id: StrategyModuleId,
    profile: Callable[[dict], None] | None,
    matrix: Callable[[dict], None] | None,
    keyword: str,
) -> None:
    """画不出来就抛，**不画空格**；而且抛的话要点名是哪一块声明。

    抛错但不说是哪一块，等于让读的人自己去翻声明 —— 而计划上那个空格子仍然在。
    """
    bundle = _bundle_with(tmp_path, profile=profile, matrix=matrix)
    with pytest.raises(ConfigError) as caught:
        _module(bundle, module_id).contribute(
            context=_context(bundle), envelope=envelope_of(bundle), bundle=bundle
        )
    assert keyword in str(caught.value), f"回执没点名 {keyword!r}"


def test_a_bundle_without_a_declaration_block_is_refused(bundle: ProfileBundle) -> None:
    """没有声明块 → 抛（不退回一组默认策略）。

    模块**必须按那份缺了声明块的 bundle 造**：``contribute`` 读的是它自己那一份
    （传进来的那一份只被核对身份），所以拿旧模块配新 bundle 验不到这一条。
    """
    stripped = dataclasses.replace(bundle, profile=None)
    module = GeoLocaleSourceProfile(stripped)
    with pytest.raises(ConfigError) as caught:
        module.contribute(
            context=_context(bundle), envelope=envelope_of(bundle), bundle=stripped
        )
    assert "声明块" in str(caught.value)


def test_a_bundle_without_a_role_matrix_is_refused(bundle: ProfileBundle) -> None:
    """没有 role-matrix → 抛（角色那两格与角色词表同源，缺了它不许退默认值）。"""
    stripped = dataclasses.replace(bundle, role_matrix=None)
    module = ScenarioProfile(stripped)
    with pytest.raises(ConfigError) as caught:
        module.contribute(
            context=_context(bundle), envelope=envelope_of(bundle), bundle=stripped
        )
    assert "role-matrix" in str(caught.value)


def test_a_request_for_an_undeclared_locale_is_refused(bundle: ProfileBundle) -> None:
    """请求点名了声明里没有的语言 → 抛。

    收窄只能收到**已声明**的那几种。悄悄把它丢掉会让「收窄成功」这句话成立，
    而实际发生的是「请求要的那一种没被满足」。编译器那一层先拦一次
    （``constraints_out_of_envelope``），这里是同一条判据的下游 —— 一个直接调
    ``contribute`` 的调用点同样不该拿到一份悄悄变小的语言格。
    """
    with pytest.raises(ConfigError) as caught:
        _contribute(bundle, StrategyModuleId.GEO_LOCALE_SOURCE, candidate_locales=("de-DE",))
    assert "de-DE" in str(caught.value)


def test_a_locale_outside_the_envelope_is_refused(bundle: ProfileBundle) -> None:
    """envelope 里没有这一格语言 → 抛。

    ``envelope.locales`` 空着**不是**「不限语言」，是「这一份批准没有语言那一栏」。
    """
    declared = bundle.profile
    assert declared is not None
    two = dataclasses.replace(
        declared,
        search_locales=("en-GB", "de-DE"),
    )
    widened = dataclasses.replace(bundle, profile=two)
    narrow_envelope = ResearchEnvelope(
        geo=envelope_of(bundle).geo, jurisdictions=envelope_of(bundle).jurisdictions, locales=()
    )
    with pytest.raises(ConfigError) as caught:
        _module(widened, StrategyModuleId.GEO_LOCALE_SOURCE).contribute(
            context=_context(widened), envelope=narrow_envelope, bundle=widened
        )
    assert "envelope.locales" in str(caught.value)


def test_a_misspelled_route_is_refused_at_assembly(
    bundle: ProfileBundle, monkeypatch: pytest.MonkeyPatch
) -> None:
    """路线表里拼错一个方法 id → 抛，且是在**读的时候**，不是事后。

    路线表是代码，写错一个 id 不会有任何提示：不解析的话，那一格会静默少一条路，
    而报告上看起来像「这一份声明没开那条路」。
    """
    from marketops.application import research_strategy

    monkeypatch.setattr(
        research_strategy,
        "_SOURCE_ROUTES",
        (("search.query_templates", "search.engine.v1"), ("search.query_templates", "typo.v1")),
    )
    with pytest.raises(ConfigError) as caught:
        _contribute(bundle, StrategyModuleId.GEO_LOCALE_SOURCE)
    assert "typo.v1" in str(caught.value)


# --- 6. 上下文的影响面（关闭条件 2 里今天真的成立的那一半） -----------------


def test_only_the_locale_cell_moves_with_the_context(tmp_path: Path) -> None:
    """**没有场景分块声明的**声明上：只有语言那一格随 context 动，其余七格逐字相同。

    这是关闭条件 2（同一 Workstream、不同 context → 不同策略）里与场景块无关的
    那一半：``candidate_locales`` 是本层唯一一个随请求变、又有声明依据的输入。
    三根轴（source / page / role）那一侧的入口**不在这条判据里** —— 这份声明没有写
    ``scenario-matrix.yaml``，于是走 ``scenario_matrix is None`` 那条分支，四格按整份
    声明写。入口本身、以及「一个场景真的选出一块并收窄那四格」由第 8 节钉住；
    没有第 8 节那一条，一个「场景块从不生效」的实现能在这条上全绿。
    """
    bundle = _bundle_with(
        tmp_path, profile=_set(("search", "locales"), ["en-GB", "de-DE", "fr-FR"])
    )
    wide = _plan(bundle)
    narrow = _plan(bundle, context=_context(bundle, candidate_locales=("de-DE",)))

    assert wide.locales != narrow.locales
    for field in sorted(MODULE_WRITABLE_FIELDS, key=lambda item: item.value):
        if field is PlanField.LOCALES:
            continue
        assert _value(wide, field) == _value(narrow, field), f"{field.value} 也动了"
    # 硬的那九格一个字都不该动：它们只从声明与 envelope 来。
    assert wide.geo == narrow.geo
    assert wide.search_budget == narrow.search_budget


def test_geo_and_locales_move_independently(tmp_path: Path) -> None:
    """改国家动 ``geo``、不动语言；改语言动 ``locales``、不动 ``geo``。

    这是关闭条件 3（国家与 language 分离）在本片能钉住的那一半。完整的分离
    （locale families）在 B16-c；这里证明的是**两个格子的来源不是同一块声明**。
    """
    bundle = _bundle_with(tmp_path / "base")
    other_country = _bundle_with(tmp_path / "country", profile=_set(("country",), "FR"))
    other_locales = _bundle_with(
        tmp_path / "locales", profile=_set(("search", "locales"), ["de-DE"])
    )

    base, by_country, by_locale = _plan(bundle), _plan(other_country), _plan(other_locales)
    assert (base.geo, base.locales) == ("GB", ("en-GB",))
    assert (by_country.geo, by_country.locales) == ("FR", ("en-GB",))
    assert (by_locale.geo, by_locale.locales) == ("GB", ("de-DE",))


def test_a_context_that_loses_languages_keeps_the_rest_of_the_declaration(
    bundle: ProfileBundle,
) -> None:
    """收窄语言不动**查询族**：模板里的 ``{role}`` 是占位符，展开才是另一层的事。"""
    plan = _plan(bundle, context=_context(bundle, candidate_locales=("en-GB",)))
    assert plan.query_families == DECLARED_TEMPLATES
    assert plan.source_methods == DECLARED_SOURCE_METHODS


# --- 7. 真实 profile 的那一份计划（基线） ------------------------------------


def test_the_real_profile_fills_the_eight_writable_cells(bundle: ProfileBundle) -> None:
    """今天按什么跑 —— 八格的取值。

    这一条是**基线**：它把真实 profile 编出来的那一份计划固化下来，于是某次改动
    把某一格变成空、改成别的来源、或者顺序变了，都会当场红。它不判「这些值对不对」
    —— 那是 profile 与 SOP 的事（``tests/test_profiles.py`` 管的是那一半）。
    """
    plan = _plan(bundle)
    assert plan.locales == DECLARED_LOCALES
    assert plan.query_families == DECLARED_TEMPLATES
    assert plan.source_methods == DECLARED_SOURCE_METHODS
    assert plan.acquisition_modes == ("http", "browser")
    assert [archetype.tier for archetype in plan.page_archetypes] == ["high", "medium", "low"]
    assert plan.role_functions == DECLARED_ROLE_FUNCTIONS
    assert plan.role_priorities == DECLARED_ROLE_PRIORITIES
    assert plan.decision_refs == DECLARED_DECISION_REFS
    # 硬的那九格里，发货那一格按 §7.3 恒空（本产品不发邮件）。
    assert plan.send_permissions == ()


# --- 8. 场景块（关闭条件 2：同一份声明、不同场景 → 不同的三根轴） -------------
#
# 第 6 节钉住的是随请求变的那一格（语言）在计划上的影响面。这一节钉住的是另外三根轴
# —— source / page / role —— 的**入口**：一份 ``scenario-matrix.yaml`` 把这三根轴按
# 读法分块，同一个 Workstream 里的两个 context 因此拿到不同的策略，而声明、池与证据
# 照旧共用。**后面那半句「照旧共用」不是这一节的判据**：这一节管的是前半（两个场景
# 真的拿到不同策略），共用那一半由第 9 节的三条结构判据钉住 —— 两半一起才是关闭
# 条件 2。分块的内容是市场侧的判断，所以这里造的块是**夹具**，不是声明：真实的
# profile 今天还没写那一份文件（它的三份必需声明之外没有第四份可选声明；那个读数在
# ``tests/test_profiles.py`` 里有判据）。
#
# 有一条分工贯穿这一节：**块是这一次请求的输入，进格子、不进版本**。谁把块塞进
# ``chunks``，同一份声明就会因为两个场景而有两个版本号 —— 而那一栏要答的是「这一次
# 编的是哪一份声明」。判据因此一半在值上、一半在 ``modules`` 上。


def _narrowable(bundle: ProfileBundle) -> dict[str, list[str]]:
    """声明里那四格**可以收窄**的名录，**从声明读**。

    四个格名（``page_tiers`` / ``business_functions`` / ``class_order`` / ``sources``）
    是 ``scenario-matrix.yaml`` 的契约，所以写死在这里；而**里面的名字**一律从声明取
    —— 把一个档位名或一个方法 id 写死，这一节的判据就依赖另一份声明，声明改名的那天
    红出来的会是一堆「找不到那一项」，而不是一条关于这一层的判据。
    """
    declared = bundle.profile
    matrix = bundle.role_matrix
    assert declared is not None and matrix is not None, "夹具应当总是装载到这两块声明"
    return {
        "page_tiers": list(declared.page_discovery_hints),
        "business_functions": list(matrix.business_functions),
        "class_order": [str(item) for item in matrix.priority["class_order"]],
        "sources": list(SOURCE_METHODS),
    }


def _block(material: dict[str, list[str]], **overrides: list[str]) -> dict[str, list[str]]:
    """一个合法的块：四格默认照声明原样写全（最宽的收窄），要收窄哪一格传哪一格。"""
    block = {name: list(values) for name, values in material.items()}
    block.update(overrides)
    return block


def _scenario(blocks: dict[str, dict[str, list[str]]]) -> Callable[[dict], None]:
    """一份写着这几块的场景声明。键就是这份声明的**场景词表**。"""

    def mutate(document: dict) -> None:
        document["blocks"] = blocks

    return mutate


def _candidate(
    reading: str,
    *,
    source: str = "req-1",
    provenance: ScenarioProvenance = ScenarioProvenance.DECLARED,
) -> ScenarioCandidate:
    """一条场景候选。``name`` 与 ``reading`` 不是同一样东西（见
    ``domain/research_context.py::ScenarioCandidate``），夹具里给同一个值就够了。"""
    return ScenarioCandidate(name=reading, reading=reading, provenance=provenance, source=source)


def _reasons(bundle: ProfileBundle, module_id: StrategyModuleId) -> dict[PlanField, str]:
    """一次贡献里每一格的理由 —— 判据读的是**报告上写给人的那句话**。"""
    return {item.field: item.reason for item in _contribute(bundle, module_id)}


def test_a_declaration_without_a_scenario_matrix_never_asks_which_scenario(
    bundle: ProfileBundle,
) -> None:
    """**没有** ``scenario-matrix.yaml`` 时：说不清的上下文照样编得出来，理由里一个字不加。

    两个方向都在这条判据里。一个方向是**问**：没有那份文件时也去调
    ``effective_scenario``，会把「这份声明还没有按场景分块的入口」变成一次编译失败
    —— 条件 2 要的是入口存在，不是逼每一份声明今天就分块。另一个方向是**说**：这一节
    引入的那几句理由必须只在有入口时出现，否则「计划在加这一份文件之前逐字不变」这句
    话就不成立（今天真实的那一份 profile 就是这一种）。反控在下面那条：同一个说不清的
    上下文，
    一旦这份声明写了入口就必须抛。
    """
    contributions = _contribute(bundle, StrategyModuleId.SCENARIO, scenario_candidates=())
    assert contributions
    for item in contributions:
        assert "scenario-matrix" not in item.reason, f"{item.field.value} 的理由里提到了入口"
    assert bundle.scenario_matrix is None


def test_a_scenario_block_narrows_the_four_cells_and_not_the_version(tmp_path: Path) -> None:
    """一个读法选出一块 → 那四格按块收窄，**版本一个字都不动**。

    四格就是 §B16 条件 2 的三根轴（source / page / role，其中 role 是两格）。块是
    **这一次请求**的输入，所以它进格子、不进版本 —— 判据的后半（``modules`` 逐条相同）
    打的是「谁把块塞进了 ``chunks``」：那样同一份声明会因为两个场景而有两个版本号。
    """
    base = _bundle_with(tmp_path / "base")
    material = _narrowable(base)
    wide = _plan(base)
    assert wide.source_methods, "基线一条路都没有 —— 这条判据会在空集上成立"
    narrowed = _bundle_with(
        tmp_path / "scenario",
        scenario=_scenario(
            {
                "k12": _block(
                    material,
                    page_tiers=material["page_tiers"][:1],
                    business_functions=material["business_functions"][:2],
                    class_order=material["class_order"][:2],
                    sources=list(wide.source_methods[:1]),
                )
            }
        ),
    )
    narrow = _plan(narrowed)

    assert [item.tier for item in narrow.page_archetypes] == [item.tier for item in wide.page_archetypes][:1]
    assert narrow.role_functions == wide.role_functions[:2]
    assert narrow.role_priorities == wide.role_priorities[:2]
    assert narrow.source_methods == wide.source_methods[:1]
    # 四格真的动了 —— 否则「按块收窄」是一句空话。
    assert narrow.role_functions != wide.role_functions

    narrowed_fields = {
        PlanField.SOURCE_METHODS,
        PlanField.PAGE_ARCHETYPES,
        PlanField.ROLE_FUNCTIONS,
        PlanField.ROLE_PRIORITIES,
    }
    for field in sorted(MODULE_WRITABLE_FIELDS, key=lambda item: item.value):
        if field in narrowed_fields:
            continue
        assert _value(wide, field) == _value(narrow, field), f"{field.value} 不该随场景动"
    assert narrow.modules == wide.modules, "版本随场景动了 —— 块不该进版本"
    assert narrow.digest != wide.digest, "两个场景编出了同一份计划"

    # 报告上要读得出来用的是哪一块：三格接一句「再按块收窄」，四格都接上来源那一句。
    scenario_reasons = _reasons(narrowed, StrategyModuleId.SCENARIO)
    for field in (PlanField.PAGE_ARCHETYPES, PlanField.ROLE_FUNCTIONS, PlanField.ROLE_PRIORITIES):
        assert "再按块收窄" in scenario_reasons[field], f"{field.value} 的理由没写收窄"
        assert "这一格按场景 'k12' 的块收窄（scenario-matrix.yaml）" in scenario_reasons[field]
    geo_reason = _reasons(narrowed, StrategyModuleId.GEO_LOCALE_SOURCE)[PlanField.SOURCE_METHODS]
    assert "取交集" in geo_reason
    assert "这一格按场景 'k12' 的块收窄（scenario-matrix.yaml）" in geo_reason


def test_a_scenario_with_no_block_keeps_the_whole_declaration(tmp_path: Path) -> None:
    """矩阵里**有别的块**、没有这一条读法的块 → 四格按整份声明，且理由里写明。

    这条打的是「取不到块时静默用另一块」：声明里真有一块（``golf_course``）、而这一次
    冲着 ``k12`` 去 —— 拿错的那一块，计划上看起来完全正常。所以期望值是**基线**那
    一份，不是矩阵里那一块收窄之后的那一份。
    """
    base = _bundle_with(tmp_path / "base")
    material = _narrowable(base)
    other = _bundle_with(
        tmp_path / "other",
        scenario=_scenario(
            {"golf_course": _block(material, page_tiers=material["page_tiers"][:1])}
        ),
    )
    assert other.scenario_matrix is not None
    assert other.scenario_matrix.scenarios == ("golf_course",)

    wide, plan = _plan(base), _plan(other)
    for field in sorted(MODULE_WRITABLE_FIELDS, key=lambda item: item.value):
        assert _value(wide, field) == _value(plan, field), f"{field.value} 不是整份声明那一份"
    assert plan.modules == wide.modules

    for item in _contribute(other, StrategyModuleId.SCENARIO):
        assert "没有给场景 'k12' 写块，这一格按整份声明写" in item.reason
    geo_reason = _reasons(other, StrategyModuleId.GEO_LOCALE_SOURCE)[PlanField.SOURCE_METHODS]
    assert "没有给场景 'k12' 写块，这一格按整份声明写" in geo_reason


#: 说不清「这一次是冲着哪一个场景」的每一种来路。(读法候选, 那句话里的判据)
UNCLEAR_SCENARIOS: dict[str, tuple[tuple[ScenarioCandidate, ...], str]] = {
    "一条候选都没有": ((), "一条都没有"),
    "只有模型猜的": (
        (_candidate("k12", provenance=ScenarioProvenance.MODEL_GUESS, source="guess-1"),),
        "一条都没有",
    ),
    "两条都可信": ((_candidate("k12"), _candidate("golf_course", source="req-2")), "golf_course"),
}


@pytest.mark.parametrize(
    ("candidates", "keyword"), UNCLEAR_SCENARIOS.values(), ids=UNCLEAR_SCENARIOS.keys()
)
def test_an_unclear_scenario_is_not_resolved_by_the_block(
    tmp_path: Path, candidates: tuple[ScenarioCandidate, ...], keyword: str
) -> None:
    """说不清这一次冲着哪一个场景 → **照抛**，不回退成「按整份声明跑」。

    回退看起来无害（整份声明本来就是一份合法的策略），而它把一次**说不清**读成一次
    「按最宽的那一份跑」—— 报告上完全正常，而差别恰恰是这一格存在的理由：按哪一块
    收窄。零条（含「只有模型猜的」，见 ``ScenarioProvenance``）与两条以上各自是一条
    **硬**歧义，处置是停下来问人。
    """
    base = _bundle_with(tmp_path / "base")
    bundle = _bundle_with(
        tmp_path / "matrix", scenario=_scenario({"k12": _block(_narrowable(base))})
    )
    with pytest.raises(ValueError) as caught:
        _contribute(bundle, StrategyModuleId.SCENARIO, scenario_candidates=candidates)
    assert keyword in str(caught.value)


def test_a_block_that_leaves_no_route_is_refused(tmp_path: Path) -> None:
    """块把今天开着的路**全收掉** → 抛，并点名块里写的与这份声明开着的那几条。

    声明那一侧这一格写不出空值（模板已经过 ``load_open_web_templates``），所以这条判据
    说的是块那一侧。这一格空着会让「这个场景一条路都不许」与「没人给这个场景写路」在
    计划上同形 —— 前者不是一条策略，后者要回去改声明，两者的处置不同。
    """
    base = _bundle_with(tmp_path / "base")
    material = _narrowable(base)
    opened = _plan(base).source_methods
    unopened = [name for name in material["sources"] if name not in opened]
    assert unopened, "注册表里没有一条没开的路 —— 这条判据构造不出来"

    bundle = _bundle_with(
        tmp_path / "blocked",
        scenario=_scenario({"k12": _block(material, sources=unopened)}),
    )
    with pytest.raises(ConfigError) as caught:
        _contribute(bundle, StrategyModuleId.GEO_LOCALE_SOURCE)
    message = str(caught.value)
    assert unopened[0] in message, "回执没点名块里写的那一条"
    assert opened[0] in message, "回执没点名这份声明开着的那几条"


def test_a_route_the_declaration_does_not_open_is_reported_not_written(
    tmp_path: Path,
) -> None:
    """块里点了一条登记过、而这份声明今天没给入口的路 → **不进这一格，但要写在理由里**。

    ``sources`` 那一格是**交集**（块开不出一条声明没开的路，见
    ``DeclaredScenarioBlock``）。交集本身不是问题，问题是它说了半句：报告上写「这个
    场景留了三条」而实际留了一条。所以被滤掉的名字要在理由里出现。
    """
    base = _bundle_with(tmp_path / "base")
    material = _narrowable(base)
    opened = _plan(base).source_methods
    unopened = [name for name in material["sources"] if name not in opened]
    assert unopened, "注册表里没有一条没开的路 —— 这条判据构造不出来"

    # 块里那几条要按**注册表顺序**写（子序列，见 ``_require_narrowing``）：把开着的三条
    # 接上未开的那一条会变成一个被重排过的块，而那是装载期另一条判据的事。
    block_sources = [
        name for name in material["sources"] if name in set(opened) or name == unopened[0]
    ]
    bundle = _bundle_with(
        tmp_path / "extra",
        scenario=_scenario({"k12": _block(material, sources=block_sources)}),
    )
    assert _plan(bundle).source_methods == opened, "被滤掉的那一条进了这一格"

    reason = _reasons(bundle, StrategyModuleId.GEO_LOCALE_SOURCE)[PlanField.SOURCE_METHODS]
    assert unopened[0] in reason, "理由里没写被滤掉的那一条"
    assert "今天没有声明给它们入口" in reason


#: 四条阅读记录 —— 每一条对应一格或几格。
READ_RECORDS = {"_GeoRead", "_ScenarioRead", "_ObjectiveRead", "_AccessRead"}


def test_every_read_record_takes_the_scenario_choice_and_one_place_picks_it() -> None:
    """四条记录的 ``cells`` 都收 ``scenario``；而**选块的地方只有一处**。

    前半是「混合计划在构造上不可能」：四个模块各写一份 ``cells``，其中一个不收这一格
    的话，那个模块按整份声明写、另一个按块写 —— 同一份计划里三根轴收窄了、有一格没
    收窄，而报告上看不出来。签名统一是这件事唯一的构造性保证。
    后半是「四个模块各自算一遍**同一段代码**」：第二处调用点就是一份分叉的选块逻辑，
    它与第一处分歧时不会报错 —— 只会让计划按另一个场景收窄。
    """
    tree = ast.parse(MODULE.read_text(encoding="utf-8"))
    records: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.ClassDef) or node.name not in READ_RECORDS:
            continue
        for item in node.body:
            if isinstance(item, ast.FunctionDef) and item.name == "cells":
                keywords = [arg.arg for arg in item.args.kwonlyargs]
                assert "scenario" in keywords, f"{node.name}.cells 不收 scenario：{keywords}"
                assert {"context", "envelope"} <= set(keywords), f"{node.name}.cells 少收了输入"
                records.add(node.name)
    assert records == READ_RECORDS, f"扫描没读到四条记录：{sorted(records)}"

    calls = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "_scenario_choice"
    ]
    assert len(calls) == 1, f"选块有 {len(calls)} 处调用点：{[node.lineno for node in calls]}"

    owner: list[int] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.ClassDef) or node.name != "_Declared":
            continue
        for item in node.body:
            if isinstance(item, ast.FunctionDef) and item.name == "contribute":
                owner.extend(
                    inner.lineno
                    for inner in ast.walk(item)
                    if isinstance(inner, ast.Call)
                    and isinstance(inner.func, ast.Name)
                    and inner.func.id == "_scenario_choice"
                )
    assert owner == [calls[0].lineno], f"那一处调用不在 _Declared.contribute 里：{owner}"


def test_two_readings_of_one_declaration_differ_only_on_the_three_axes(
    tmp_path: Path,
) -> None:
    """同一份声明、两个都写进词表的读法 → **只有那四格不同**，其余逐字相同。

    这是关闭条件 2 的正面：同一个 Workstream、不同 context → 不同的 source / page /
    role 策略。它划的是「条件 2 关掉了」与「条件 2 有个入口」的分界 —— 只有入口时，
    两个 context 编出来的是同一份计划。

    期望值全部来自**基线那一份计划**（没有矩阵的那棵树）：块里写的名字与它同源，所以
    两边一旦对不上，装载期就会拒（子序列），这也是这条判据的一部分。
    """
    base = _bundle_with(tmp_path / "base")
    material = _narrowable(base)
    wide = _plan(base)
    routes = wide.source_methods
    assert len(material["page_tiers"]) >= 2, "档位少于两档 —— 收窄不出两组不同的值"
    assert len(wide.role_functions) >= 3 and len(wide.role_priorities) >= 3
    assert len(routes) >= 2

    bundle = _bundle_with(
        tmp_path / "matrix",
        scenario=_scenario(
            {
                "k12": _block(
                    material,
                    page_tiers=material["page_tiers"][:1],
                    business_functions=material["business_functions"][:2],
                    class_order=material["class_order"][:2],
                    sources=list(routes[:1]),
                ),
                "golf_course": _block(
                    material,
                    page_tiers=material["page_tiers"][-1:],
                    business_functions=material["business_functions"][-2:],
                    class_order=material["class_order"][-2:],
                    sources=list(routes[-1:]),
                ),
            }
        ),
    )
    first = _plan(bundle, context=_context(bundle, scenario_candidates=(_candidate("k12"),)))
    second = _plan(
        bundle, context=_context(bundle, scenario_candidates=(_candidate("golf_course"),))
    )

    assert [item.tier for item in first.page_archetypes] == [
        item.tier for item in wide.page_archetypes
    ][:1]
    assert [item.tier for item in second.page_archetypes] == [
        item.tier for item in wide.page_archetypes
    ][-1:]
    assert first.role_functions == wide.role_functions[:2]
    assert second.role_functions == wide.role_functions[-2:]
    assert first.role_priorities == wide.role_priorities[:2]
    assert second.role_priorities == wide.role_priorities[-2:]
    assert first.source_methods == routes[:1]
    assert second.source_methods == routes[-1:]

    narrowed_fields = {
        PlanField.SOURCE_METHODS,
        PlanField.PAGE_ARCHETYPES,
        PlanField.ROLE_FUNCTIONS,
        PlanField.ROLE_PRIORITIES,
    }
    for field in sorted(narrowed_fields, key=lambda item: item.value):
        assert _value(first, field) != _value(second, field), f"{field.value} 两组读法同值"
    for field in sorted(MODULE_WRITABLE_FIELDS, key=lambda item: item.value):
        if field in narrowed_fields:
            continue
        assert _value(first, field) == _value(second, field), f"{field.value} 不该随场景动"

    # 池与证据照旧共用：两个人读的是同一份声明，版本与摘要的来源因此相同（摘要不同，
    # 因为写下来的策略不同 —— 那是这次比较要看的量，不是分家的证据）。
    assert first.modules == second.modules
    assert first.digest != second.digest


# --- 9. 共享那一半（关闭条件 2 的后半：池与证据不随场景分家） -----------------
#
# 第 6 与第 8 节钉的是**请求面**动起来之后计划怎么变。这一节钉的是另一件事，也是
# 这一整族里唯一一条不能用「换个 context 编两次」验的：**分家在这条链上没有入口**。
# 三份声明（profile / role-matrix / scenario-matrix）写下的都是**策略**；池与证据
# 不是策略，它们是工作区里已经存在的**事实**。``Evidence.workspace`` 的 docstring
# 逐字：「工作区是授权链的第一段。原始事实在一个已授权工作区内部可以跨 Scene 复用，
# 复用范围止于工作区边界」。
#
# 「仍按工作区共享」那一半的判据**不在这个文件里**，在
# ``tests/test_page_reuse.py::test_two_subjects_on_one_url_pay_once_a_tick``（同一个
# 工作区里两个主体只付一次取回；那个文件的 docstring 第 2 条还写着这一条为什么不能
# 靠一次比较执行、只能由构造方式成立）。这里钉的是它的另一半：**计划链够不到池与
# 证据，池与证据也够不到计划**。两半合起来才是「不随场景分家」—— 只有一半的时候，
# 一个「池按场景分家」的实现可以从另一半那一侧长出来。
#
# 三条都是结构判据（AST 与字段表）。这个条件的行为面要一次 Run 才能观察，而 Run 面
# 属 B17；今天的诚实答案因此是「做不到」，不是「做到了」—— 哪天有人把这条链接上，
# 要的不是把判据改绿，是先回答一个问题：同一个工作区里两个场景，池分不分家。


#: 池 / 证据 / 复用这一层的模块。按**模块**禁比按名字禁结实：名字可以改，
#: 「这一层的代码被计划链读到」这件事改不掉。
POOL_AND_EVIDENCE_MODULES = (
    "ports.pool",
    "ports.store",
    "ports.scenes",
    "domain.evidence",
    "application.pool",
    "application.accounts",
    "application.relations",
    "application.motherpool",
    "application.reuse",
)

#: 上面那些模块在 ``from ..ports import pool`` 这类相对 import 里剩下的那一截。
POOL_AND_EVIDENCE_LEAVES = (
    "pool",
    "store",
    "scenes",
    "evidence",
    "accounts",
    "relations",
    "motherpool",
    "reuse",
)

#: 这一层的**入口名**。读到任何一个，就有一个「这一份事实属于哪个工作区」的问题
#: 被挪进了编译期。
#:
#: ``workspace`` 刻意**不在**这里，尽管它是这一层的承重词：它太短，单独禁它会把
#: 任何局部变量名判成越界（这一层自己的文件里就有 11 处合法的 ``workspace``）。
#: 真正要防的是它落在 ``ResearchContext`` 上 —— 那一格由第 9 节的第二条判据钉住。
POOL_AND_EVIDENCE_NAMES = (
    "VenueAssetStore",
    "AccountVenueRelationStore",
    "AccountPoolStore",
    "AccountCohortStore",
    "EvidenceStore",
    "ReuseLedger",
    "ReuseDecision",
    "ReuseVerdict",
    "permission_range",
)

#: 计划链那三个模块的**末段**。共享层 import 它们的那一刻，这一层就知道有「计划」
#: 这回事了 —— 不管写的是全名还是 ``from .research_strategy import ...``。
PLAN_MODULE_LEAVES = (
    "research_context",
    "research_plan",
    "research_strategy",
    "plan_preview",
)

#: 计划链的词。``scene`` 不在里面，这不是遗漏：``ports/scenes.py`` 的 ``SceneStore``
#: 是**立项与持续经营**那一侧的存储，与 ``scenario-matrix.yaml`` 只是字面相近 ——
#: 按字段名说会把一个正当的邻居判成越界，按**行为**说才知道它们说的是两件事。
#:
#: 这一张表只放**写得出标识符**的词，所以它是直接可执行的：第三条判据的反控拿
#: ``_named_leaves`` 去扫计划链，表里任何一个词在那里读不到就当场红（一个拼错的词
#: 扫什么都绿）。两个只在散文里出现的名字因此不在这里，而且不是「不方便」：
#: ``plan_preview`` 的两种写法都被 ``PLAN_MODULE_LEAVES`` 覆盖（模块末段就是它），
#: ``SceneFingerprint`` 要用到就得先 import ``domain/research_context`` —— 那个
#: 模块的末段同样在 ``PLAN_MODULE_LEAVES`` 里。**更靠上的入口已经挡住了它们。**
PLAN_WORDS = (
    "ResearchContext",
    "ResearchEnvelope",
    "EffectiveResearchPlan",
    "StrategyCompiler",
    "StrategyModuleId",
    "PlanField",
    "ScenarioProfile",
    "GeoLocaleSourceProfile",
    "Ambiguity",
    "research_compiler",
    "research_strategy",
    "research_plan",
    "envelope_of",
    "ambiguities_of",
    "fingerprint_of",
    "scenario_matrix",
)


def _is_pool_or_evidence_module(name: str) -> bool:
    """这串（可能带前导点的）点分名字落在池 / 证据 / 复用那一层里吗。"""
    stripped = name.lstrip(".")
    return any(
        stripped == module or stripped.endswith(f".{module}")
        for module in POOL_AND_EVIDENCE_MODULES
    )


def _reaches_the_pool_or_the_evidence(tree: ast.AST) -> list[str]:
    """这一棵树里够到池 / 证据 / 复用的每一处，带行号。

    三个方向一起扫：**import**（合法做法必然从这里开始）、**标识符**（转手拿到之后
    用起来的样子）、**字符串字面量**（``import_module("marketops.ports.pool")`` 这类
    绕开 import 语句的写法）。

    字符串那一支只查**模块路径**，不查类名 —— 这一条是第一次跑出来的：``plan_preview.py``
    的 ``NOT_REPORTED`` 里逐字写着「持有它的是 §B17 的 Run preview 与
    ``application/reuse.py`` 的 ReuseDecision」。那一句说的正是「这一层读不到池」，
    是**同向**证据；按类名查会把它判成接线。点名不是接线。
    """
    found: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if _is_pool_or_evidence_module(alias.name):
                    found.append(f"{node.lineno}: import {alias.name}")
        elif isinstance(node, ast.ImportFrom):
            module = "." * node.level + (node.module or "")
            if node.module and _is_pool_or_evidence_module(module):
                found.append(f"{node.lineno}: from {module} import ...")
            for alias in node.names:
                if alias.name in POOL_AND_EVIDENCE_NAMES:
                    found.append(f"{node.lineno}: from {module} import {alias.name}")
                if node.level and alias.name in POOL_AND_EVIDENCE_LEAVES:
                    found.append(f"{node.lineno}: from {'.' * node.level} import {alias.name}")
        elif isinstance(node, ast.Name):
            if node.id in POOL_AND_EVIDENCE_NAMES:
                found.append(f"{node.lineno}: {node.id}")
        elif isinstance(node, ast.Attribute):
            if node.attr in POOL_AND_EVIDENCE_NAMES:
                found.append(f"{node.lineno}: .{node.attr}")
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            if _is_pool_or_evidence_module(node.value):
                found.append(f"{node.lineno}: 字符串里写着模块路径 {node.value!r}")
    return found


def _named_leaves(tree: ast.AST) -> list[tuple[int, str]]:
    """这一棵树里每一个点分名的**末段**，带行号。

    三种写法一起收：``import a.b.c`` / ``from a.b import c`` / 名字与属性。
    ``from ..domain.research_context import envelope_of`` 因此留下两个末段
    （``research_context`` 与 ``envelope_of``），``bundle.scenario_matrix`` 留下
    一个 —— 越界的那几种写法都不需要各自写一遍。
    """
    found: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        names: list[str] = []
        if isinstance(node, ast.Import):
            names = [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom):
            names = [*(node.module or "").split("."), *(alias.name for alias in node.names)]
        elif isinstance(node, ast.Name):
            names = [node.id]
        elif isinstance(node, ast.Attribute):
            names = [node.attr]
        lineno = getattr(node, "lineno", 0)
        found.extend((lineno, name.rsplit(".", 1)[-1]) for name in names)
    return found


def _mentions_the_plan(tree: ast.AST) -> list[str]:
    """这一棵树里提到计划链的每一处，带行号（第 9 节第三条用的反向扫描）。"""
    return [
        f"{lineno}: {leaf}"
        for lineno, leaf in _named_leaves(tree)
        if leaf in PLAN_WORDS or leaf in PLAN_MODULE_LEAVES
    ]


def test_the_plan_chain_cannot_reach_a_pool_or_an_evidence_store() -> None:
    """计划是**从声明编出来的**，不是从池里捞出来的：三个模块一处都够不到那两层。

    够得到的那一刻，「这一次研究去哪里找」就开始取决于工作区里**已经有什么**，
    于是同一份上下文、同一份声明，两个工作区编出两份计划 —— 而计划上那一栏版本
    （``modules``，见 C1）说的是「读的是哪一份声明」，两棵树里它都一样。报告上
    看不出来的那种分家，正是这一条要挡的。
    """
    scanned: dict[str, int] = {}
    reaching: list[str] = []
    for path in (PLAN_MODULE, MODULE, PREVIEW_MODULE):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        scanned[path.name] = sum(
            1 for node in ast.walk(tree) if isinstance(node, (ast.Import, ast.ImportFrom))
        )
        reaching.extend(
            f"{path.name}:{hit}" for hit in _reaches_the_pool_or_the_evidence(tree)
        )

    assert not reaching, f"计划链够到了池 / 证据 / 复用：{reaching}"
    # 反控之一：三个模块都得真的被读进来（一个零 import 的模块会让上面那条对空气成立）。
    assert all(count > 0 for count in scanned.values()), f"扫描没读到 import：{scanned}"
    # 反控之二：扫描器本身会红，且两条路各自都会红 —— 一段**已知越界**的源码必须
    # 被它点名（import 那一路，与绕开 import 语句的字符串那一路）。
    planted_import = ast.parse("from ..ports.pool import AccountPoolStore\n")
    assert _reaches_the_pool_or_the_evidence(planted_import), "import 那一路读不出已知越界"
    planted_string = ast.parse('import_module("..ports.store")\n')
    assert _reaches_the_pool_or_the_evidence(planted_string), "字符串那一路读不出已知越界"


def test_the_request_carries_no_cell_for_a_pool_or_a_workspace() -> None:
    """``ResearchContext`` 的十二格与 ``ResearchEnvelope`` 的四格 —— 逐字，逐序。

    这不是「今天长这样」的记录，是一条**闭集**：请求面上没有任何一格能说「这一次
    用哪口池」「这一份事实属于哪个工作区」。于是「池随场景分家」在这条链上不是被
    一条纪律禁止的，而是**没有表达它的地方** —— 要让它发生，得先改这张表，而改
    这张表是个要过评审的动作。

    ``SceneFingerprint`` 刻意**不在**这一条里：它的 ``scenario`` 是**摘要**的一格
    （这一次研究是什么），不同场景本来就该有不同的摘要。而计划上那一栏 ``modules``
    是**另一件事**（读的是哪一份声明），C1 起它与请求无关。两个都叫「版本」，
    所以这一条把话说明白：拿一个去判另一个，是错的。

    改这张表的那一天（Run 面要 context 带上工作区），要改的正是**这条判据**，并且
    那时必须重答一个今天问不出来的问题：同一个工作区里两个场景，池分不分家 ——
    在编译期这个问题没有答案，因为上面那第三条判据说这一层够不到池。
    """
    assert tuple(ResearchContext.model_fields) == (
        "geo",
        "jurisdiction",
        "candidate_locales",
        "entity_classes",
        "asset_classes",
        "organization_classes",
        "scenario_candidates",
        "objective",
        "product_use",
        "evidence_requirements",
        "declared_ambiguities",
        "hard_constraints",
    )
    assert tuple(ResearchEnvelope.model_fields) == (
        "geo",
        "jurisdictions",
        "locales",
        "exclusions",
    )
    # 闭集是**两半**合起来的：字段表说「多了哪一格」，``extra="forbid"`` 说「不许
    # 从旁边塞一格」。少任何一半，另一条路上都能再加一格。
    for model in (ResearchContext, ResearchEnvelope):
        assert model.model_config.get("extra") == "forbid", model.__name__


def test_the_pool_and_the_evidence_layers_do_not_know_about_the_plan() -> None:
    """反方向：池 / 证据 / 复用这一层里，没有一处知道有「研究计划」这回事。

    单向不可达不够。分家也可以由**下游**发起：池层自己去读 ``ResearchContext`` 挑
    一口池，或者按 ``ScenarioProfile`` 选一个工作区 —— 那样计划的编译链根本不用改，
    上面两条都会继续绿。

    ``scene`` 不在禁词里（见 ``PLAN_WORDS`` 上面那一段）：``ports/scenes.py`` 是
    **立项与持续经营**那一侧的存储，与研究场景只是字面相近。按字段名说会把它判成
    越界，按**行为**说才知道它们说的是两件事 —— 同一条纪律在这一片的验收矩阵里
    也用过一次。
    """
    files = sorted((REPO_ROOT / "marketops" / "ports").glob("*.py")) + [
        REPO_ROOT / "marketops" / "domain" / "evidence.py",
        REPO_ROOT / "marketops" / "application" / "pool.py",
        REPO_ROOT / "marketops" / "application" / "accounts.py",
        REPO_ROOT / "marketops" / "application" / "relations.py",
        REPO_ROOT / "marketops" / "application" / "motherpool.py",
        REPO_ROOT / "marketops" / "application" / "reuse.py",
    ]
    # 反控之一：这一层有二十来个模块，glob 抽空的话下面那条对空气成立。
    assert len(files) >= 20, f"扫到的文件太少，glob 可能在空转：{len(files)}"

    saying: list[str] = []
    for path in files:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        saying.extend(f"{path.name}:{hit}" for hit in _mentions_the_plan(tree))
    assert not saying, f"池 / 证据 / 复用这一层读到了计划：{saying}"

    # 反控之二：这一批词**经同一条提取器**在计划链里读得到 —— 一个拼错的词（或者
    # 一个提取器永远看不见的词）扫什么都绿，而它绿的正是「这一层读不到计划」这句
    # 假话。这一条一开始是**子串检查**，变异 N9 当场证明它太松：把 ``ResearchContext``
    # 拼成 ``ResearchContex`` 之后子串仍然命中，反控没红。
    readable = {
        leaf
        for path in (PLAN_MODULE, MODULE, PREVIEW_MODULE)
        for _, leaf in _named_leaves(ast.parse(path.read_text(encoding="utf-8")))
    }
    missing = [word for word in PLAN_WORDS if word not in readable]
    assert not missing, f"这些词提取器在计划链里读不到（词表在空转）：{missing}"


# --- 10. 条件 5 的两半（排序没有运行期自由度 / 拆 Scene 没有自动路） -----------
#
# 第 9 节钉的是「池与证据不随场景分家」。这一节钉的是关闭条件 5 剩下的两处缺口，
# 而它们在这条链上有**同一个形状**：行为面要一次 Run 才观察得到（前半要「运行中真的
# 有来源产出可比」，后半要「长期观察证明三种策略持续不同」），而 Run 面属 B17。
# 今天给得出的因此是结构判据 —— 但这两处比第 9 节那三条更难说清一件事：第 9 节
# 说的是「做不到」，这里说的前半是「**不需要做到**」。
#
# ## 前半（「运行中按四个量调排序」）今天不是缺口，是一句更硬的话
#
# 计划上 ``source_methods`` 那一格的值只能是 ``domain/sources.py`` 注册表顺序的
# **子序列**：编译只做准入收窄（哪条路开出来了），不做重排。于是「按 yield 把某条路
# 提到前面」这类实现**没有落点** —— 它一写出来就会打乱注册表顺序，而下面第一条判据
# 数的正是这个顺序。这一条同时是「批准范围内排序」那句话的答案：**今天排序不在
# 批准范围内，因为排序没有运行期自由度**；要让它在范围内，得先给它一个自由度，那一
# 刻判据红，改判据的人必须先把「哪些重排算在范围内」写下来。
#
# ## 后半（「低样本不会自动拆 Scene」）此前只有读数，现在有执行器
#
# 验收矩阵记过这一处缺口：三个入口「全部是 controller 角色人工调用的 MCP 工具」是
# 一个**读数**，而一个只靠「那条路还不存在」成立的约束不算被守住。下面第三、四条
# 把它变成闭集：**会写 Scene 记录的函数是派生出来的**（新写的写函数自动进闭集，不需要
# 有人记得回来改判据），它们的调用者只能是 MCP 工具；让一版生效的那一步必须记下
# 主体；激活闸门只拒绝、不创建。
#
# 这一节不判 ``SceneSplitCandidate`` 零命中 —— 那是「还没写」，判它等于把「今天没有」
# 当成「守住了」。它要读的那份观察（跨 Run 对比来源策略 / 责任模型 / 治理边界）
# 今天在盘上没有生产者，那件事属 B17。

#: 立项与持续经营那一侧的实现层：它的写函数是「谁能让一个 Scene 出现或改版」的全部。
SCENE_USE_CASES = REPO_ROOT / "marketops" / "application" / "scenes.py"
#: 这三个入口今天只有这一个调用者（``adapters/mcp_server`` 那一层）。
PRODUCT_LOOP = REPO_ROOT / "marketops" / "adapters" / "mcp_server" / "product_loop.py"
#: ``SceneStore`` 上会**改动落盘状态**的两个方法（其余都是读）。
SCENE_STORE_WRITES = ("put_record", "put_version")


def _top_level_functions(path: Path) -> dict[str, ast.FunctionDef]:
    """这个模块的顶层函数，按名字索引。"""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return {node.name: node for node in tree.body if isinstance(node, ast.FunctionDef)}


def _calls_named(node: ast.AST, names: set[str]) -> list[str]:
    """这棵树里对 ``names`` 里任一个名字的调用（``f(...)`` 与 ``x.f(...)`` 都算）。

    两种写法都要收：``scene_use_cases.activate(...)`` 是跨模块调用，而
    ``activate(...)``（同一个模块里）是**绕过实现层**的另一种写法 —— 只收属性那一半
    会让后一种从闭集边上溜过去。
    """
    found: list[str] = []
    for sub in ast.walk(node):
        if not isinstance(sub, ast.Call):
            continue
        func = sub.func
        if isinstance(func, ast.Name):
            name = func.id
        elif isinstance(func, ast.Attribute):
            name = func.attr
        else:
            continue
        if name in names:
            found.append(name)
    return found


def _scene_record_writers() -> tuple[str, ...]:
    """``application/scenes.py`` 里**会改动落盘状态**的顶层函数，**派生**出来的。

    判据不写一份名单：名单是「今天有这六个」的记录，而这一节要的是闭集 —— 明天
    有人加一个 ``split`` 写函数，它必须自动落进下面那条判据的射程，不需要谁记得
    回来改测试。派生用的判据是函数体里有没有 ``store.put_record`` / ``store.put_version``，
    而 ``validate`` / ``get_scene`` / ``list_scenes`` 三个只读函数因此落在名单外
    （下面有反控钉住这一点）。
    """
    return tuple(
        name
        for name, func in _top_level_functions(SCENE_USE_CASES).items()
        if set(_calls_named(func, set(SCENE_STORE_WRITES)))
    )


def _is_the_registry_order(values: tuple[str, ...]) -> bool:
    """这一格的值是不是注册表顺序**原样收窄**（而不是重排过）。

    判据是一个比较而不是一次扫描：把值换成「注册表里被选中的那些、按注册表顺序」
    之后必须逐字相同。任何按别的依据排序（产出、成本、命中率）的实现都会在这里
    给出不同的序列。
    """
    return list(values) == [method for method in SOURCE_METHODS if method in set(values)]


def _cell_constructions(tree: ast.AST) -> list[tuple[int, str]]:
    """这一棵树里每一处 ``_Cell(field=PlanField.X, ...)`` → ``(行号, "X")``。"""
    found: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if not isinstance(node.func, ast.Name) or node.func.id != "_Cell":
            continue
        for keyword in node.keywords:
            if keyword.arg == "field" and isinstance(keyword.value, ast.Attribute):
                found.append((node.lineno, keyword.value.attr))
    return found


def test_the_source_order_is_the_registry_order_narrowed_never_reordered(
    bundle: ProfileBundle, tmp_path: Path
) -> None:
    """来源那一格只有一种变化：**准入收窄** —— 没有第二种，也没有重排。

    两个输入形状都要过：真实 profile 那三条路（全宽），以及一个场景块把它收窄到
    前两条。收窄之后的值必须是**宽的那一份的前缀**（``wide[:2]``）—— 只断言「是子
    序列」会漏掉一种实现：把子集按别的顺序写出来，它仍然是子序列。

    反控三处。**第一处**：宽度至少两条（长度 0 或 1 的序列是任何序列的子序列，
    上面那条会在空集上成立）。**第二处**：注册表里有这一格没开出来的路
    （``len(SOURCE_METHODS) > len(wide)``）—— 否则「子序列」退化成「整表照抄」。
    **第三处**：谓词本身对一段**已知乱序**必须为假 —— 一个永远返回真的谓词，绿的
    正是「没有人重排过」这句假话。

    **与第 2 节、第 8 节那一处的重叠是刻意的，也说清楚。** 第 2 节的
    ``test_source_methods_are_the_routes_the_declaration_opens`` 判的是**这一份
    声明**编出来的取值与顺序；第 8 节那一处判的是块收窄之后**这一次**的值。这一条
    把同一件事写成**不变式**（一个可复用的谓词），并给出谓词自己会判事的证明 ——
    变异对照里把谓词改成恒真，**只有这一条**转红（第 2 与第 8 节那两条照样绿）。
    重叠那一半不是新的保护，增量是「任何声明形状」与那个反控。
    """
    wide = _value(_plan(bundle), PlanField.SOURCE_METHODS)
    assert _is_the_registry_order(wide), f"真实 profile 那一格不是注册表顺序：{wide}"
    # 反控一：这一格今天真的有多条。
    assert len(wide) >= 2, f"只有 {len(wide)} 条路，子序列判据在它上面是平凡的"
    # 反控二：注册表比这一格宽 —— 「收窄」这个词才有内容。
    assert len(SOURCE_METHODS) > len(wide), "注册表里没有这一格没开出来的路"

    material = _narrowable(bundle)
    narrowed = _value(
        _plan(
            _bundle_with(
                tmp_path,
                scenario=_scenario({"k12": _block(material, sources=list(wide[:2]))}),
            )
        ),
        PlanField.SOURCE_METHODS,
    )
    assert _is_the_registry_order(narrowed), f"块收窄之后不是注册表顺序：{narrowed}"
    assert narrowed == wide[:2], f"块收窄之后的值不是宽那一份的前缀：{narrowed}"
    # 反控三：谓词会红 —— 一段**已知乱序**不是注册表顺序。
    assert not _is_the_registry_order(tuple(reversed(DECLARED_SOURCE_METHODS))), (
        "谓词对已知乱序判成了真，它在空转"
    )


def test_the_source_cell_has_one_writer_and_it_takes_only_the_declaration_and_the_block() -> None:
    """这一格**只有一个写者**，而那个写者只有两样输入：声明，与这一次选中的块。

    写者的唯一性是把「排序可以有两个来源」这件事从结构上拿掉：全仓构造这一格的
    地方只有一处（``_Cell(field=PlanField.SOURCE_METHODS, ...)``），别处只能读
    ``build_plan`` 写好的值。第二半是签名本身：``(reading, *, scenario)`` —— 想
    让它按产出排的实现必须先给它第四样输入，而那一刻这条判据红。

    反控两处。**第一处**：``_Cell(...)`` 的构造式在这个模块里不止一处（另外两个格
    子各一处），所以提取器不是在一个空集合上数数。**第二处**：那个写者函数真的
    被上面这条提取器取到了（名字打错、或者函数被挪走，都要在这里响）。

    这一格「谁写的」归属（``FIELD_OWNER`` 那一行）是第 1 节的事，不在这里重复。
    """
    tree = ast.parse(MODULE.read_text(encoding="utf-8"))
    cells = _cell_constructions(tree)
    writers = [line for line, field in cells if field == "SOURCE_METHODS"]
    assert writers == [writers[0]] and len(writers) == 1, (
        f"这一格的构造处不是恰好一处：{[(line, field) for line, field in cells if field == 'SOURCE_METHODS']}"
    )
    # 反控一：提取器真的看得见 ``_Cell(...)``（不止这一格）。
    assert len(cells) > 1, f"提取器只看到 {len(cells)} 处构造，可能在空转"

    functions = _top_level_functions(MODULE)
    assert "_source_methods_cell" in functions, "写这个格子的函数不在了"
    cell = functions["_source_methods_cell"]
    # 反控二：那一处构造式真的在这个函数体里。
    inside = {node.lineno for node in ast.walk(cell) if hasattr(node, "lineno")}
    assert writers[0] in inside, f"第 {writers[0]} 行的构造式不在 _source_methods_cell 里"

    assert [arg.arg for arg in cell.args.args] == ["reading"]
    assert [arg.arg for arg in cell.args.kwonlyargs] == ["scenario"]
    assert cell.args.vararg is None, "它开始收 *args 了：输入不再是一个闭集"
    assert cell.args.kwarg is None, "它开始收 **kwargs 了：输入不再是一个闭集"
    assert not cell.args.posonlyargs


def test_only_the_mcp_tools_can_write_a_scene_record() -> None:
    """拆 / 改一个 Scene 的写函数**全部**只有一个调用者：``adapters/mcp_server``。

    这一条是「低样本不会自动拆 Scene」的执行器。此前它的依据是一句读数（三个入口
    都是人工调用的 MCP 工具），而读数挡不住将来：一条自动路径长出来的时候，没有任何
    判据会红。闭集是**派生**的（``_scene_record_writers``）：明天有人给实现层加一个
    ``split``，它自动进射程 —— 只要它的调用者不在 MCP 那一层，这条判据当场红。

    反控两处。**第一处**：派生出至少四个写函数（派生逻辑空转的话，下面那条对空气
    成立）。**第二处**：三个**只读**的函数**不在**名单里 —— 这一条钉的是派生真的按
    「写没写落盘状态」判，而不是「所有顶层函数」。**第三处**：六个写函数今天**各自
    至少被调用一次** —— 一个没人调用的写函数会让「调用者都在 MCP 层」这句话在它身上
    自动成立，而那种成立什么也没说。
    """
    writers = _scene_record_writers()
    assert len(writers) >= 4, f"只派生出 {len(writers)} 个写函数：{writers}"
    readers = {"validate", "get_scene", "list_scenes"}
    assert readers.isdisjoint(writers), f"只读函数被派生成了写函数：{set(writers) & readers}"

    outsiders: dict[str, list[str]] = {}
    reached: set[str] = set()
    for path in sorted((REPO_ROOT / "marketops").rglob("*.py")):
        if path == SCENE_USE_CASES:
            continue
        called = set(_calls_named(ast.parse(path.read_text(encoding="utf-8")), set(writers)))
        reached |= called
        if path != PRODUCT_LOOP:
            for name in sorted(called):
                outsiders.setdefault(name, []).append(str(path.relative_to(REPO_ROOT)))

    assert not outsiders, f"这一层之外有人在写 Scene 记录：{outsiders}"
    # 反控三：六个写函数今天各自有调用者（否则「调用者都在 MCP 层」是空话）。
    assert reached == set(writers), f"这些写函数今天没有任何调用者：{set(writers) - reached}"


def test_the_step_that_makes_a_version_effective_records_who_did_it() -> None:
    """让一版**生效**的那一步必须记下主体 —— 写一版不生效的版本不必。

    两句话不一样，所以这一条只判前者。``create_scene(activate=true)`` 与
    ``control_scene(activate)`` 是仅有的两处「规则开始生效」，两处都传
    ``by=_require_principal()`` —— 而 ``_require_principal`` 在传输层没有确立主体时
    直接抛。一条自动路径（定时任务、某个 tick 里的循环）通常没有主体，所以它要么
    在这里红，要么必须伪造一个主体，而后者是另一件要被解释的事。

    ``add_scene_version`` **不在这条判据里**，也是刻意的：它写下的那一版不生效
    （``application/scenes.py::add_version`` 的 docstring 逐字说它不激活），它的
    回执里也明说「那一步会记下批准人，而这里不会」。把它拉进来会变成一条要求
    「不生效的写入也要有主体」的新规则 —— 那不是今天的行为。
    """
    activations: list[tuple[Path, ast.Call]] = []
    for path in sorted((REPO_ROOT / "marketops").rglob("*.py")):
        if path == SCENE_USE_CASES:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            if isinstance(func, ast.Attribute) and func.attr == "activate":
                activations.append((path, node))

    # 反控：今天真的有两处（``create_scene(activate=true)`` 与 ``control_scene``）。
    assert len(activations) >= 2, f"只找到 {len(activations)} 处生效点，判据在空转"
    for path, node in activations:
        by = next((kw.value for kw in node.keywords if kw.arg == "by"), None)
        where = f"{path.relative_to(REPO_ROOT)}:{node.lineno}"
        assert isinstance(by, ast.Call) and isinstance(by.func, ast.Name), (
            f"{where} 让一版生效却没有记主体（by= 不是一次调用）"
        )
        assert by.func.id == "_require_principal", (
            f"{where} 的 by= 不是 _require_principal()（没有人被问过这次是谁在操作）"
        )


def test_the_activation_gate_refuses_and_never_creates() -> None:
    """激活闸门**只拒绝**：它读待办、判闸门、抛；它不写任何东西。

    §9 的「提议 → 批准 → 激活」里，闸门的位置是**拦**（``block_activation`` 有件
    待办在等就抛 ``ToolError``），不是「替人把那一版激活」。一条把闸门写成自动
    激活器的实现会让待办被自己解除 —— 报告上「批准后生效」与「等到期就生效」同形。

    反控三处。**第一处**：闸门真的读了两样东西（``block_activation`` 与
    ``pending_proposal``）—— 取到空函数体的话，上面那条对空气成立。**第二处**：
    函数体里真的有一个 ``raise`` —— 「只拒绝」得有拒绝。**第三处**：函数真的在
    ``product_loop.py`` 里被取到（名字写错就是一条永远绿的判据）。
    """
    functions = _top_level_functions(PRODUCT_LOOP)
    assert "_activation_gate" in functions, "_activation_gate 不在 product_loop.py 里"
    gate = functions["_activation_gate"]

    assert not _calls_named(gate, set(SCENE_STORE_WRITES)), "闸门自己写盘了"
    assert not _calls_named(gate, set(_scene_record_writers())), "闸门自己创建/改版了"
    # 反控一与二：它真的读了、也真的拒了。
    assert _calls_named(gate, {"block_activation", "pending_proposal"}), "闸门没有读待办"
    assert [node for node in ast.walk(gate) if isinstance(node, ast.Raise)], "闸门没有拒绝这一支"
