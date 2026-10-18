"""``marketops/application/plan_preview.py`` 的判据 —— 纯函数，**不联网、不读凭据**。

这一族盯的三件事，都不是会崩的失败：

1. **该停下来问人的那几条被当成一次正常编译。** 一份说不出目标人口的声明编出来的
   计划，在报告上与一份讲清楚了的一模一样（有摘要、有出处、有来源方法），而它决定
   这次研究去哪里找、找谁。
2. **这一层偷偷变得能调用外部。**「preview 零 provider 调用」（关闭条件 1 的后半）
   如果只是靠人自觉，那么接线的那一天由「谁顺手加了一个客户端参数」决定它花不花
   钱 —— 所以这里按 AST 扫这一层的 import **白名单**与调用名，并拿一个已知进口了
   端口的模块当正控。
3. **§253 那五件事被静默省掉。** 省掉一项之后报告看起来只是短了一点，而读的人会
   以为「preview 报告了五件事」。所以每一条答不上来的都带着一句**可核对**的理由，
   而这里逐条核对那句话的前提（``ReviewReason`` 的成员、``SourceMethod`` 的字段、
   ``PlanField`` 的名单）还成立。

真实 profile 在这里也照跑：GB / FR / DE / 多语地区四份 fixture 都走**真实装载器**
（照 ``tests/test_research_strategy.py`` 的 ``_bundle_with``：``copytree`` + 改 YAML +
``Settings`` + ``load_profile``），而 GB 那一份就是盘上那一份 —— 逐格固化的取值某天
变了会当场红。

一处刻意**不**在这里判的：``envelope_of`` 读不出批准范围时的抛法（profile 缺
``country`` 那一支）—— 那是 ``tests/test_research_context.py`` 的地盘，这里只判
这一层有没有**改**它的去向（照原样抛，不渲染成一次 HOLD）。
"""

from __future__ import annotations

import ast
import dataclasses
import inspect
import itertools
import json
import re
import shutil
from pathlib import Path
from typing import Any, Callable

import pytest
import yaml

from marketops.adapters.profile_loader import load_profile
from marketops.application import plan_preview
from marketops.application.plan_preview import (
    NOT_REPORTED,
    PlanPreview,
    PreviewOutcome,
    preview_plan,
)
from marketops.application.research_strategy import research_compiler
from marketops.config import REPO_ROOT, Settings
from marketops.domain.errors import ConfigError
from marketops.domain.profile import ProfileBundle
from marketops.domain.research_context import (
    Ambiguity,
    ContextDimension,
    ResearchContext,
    ScenarioCandidate,
    ScenarioProvenance,
    ambiguities_of,
    ambiguity_is_hard,
    envelope_of,
)
from marketops.domain.research_plan import MODULE_PRIORITY, PlanField
from marketops.domain.review import ReviewReason
from marketops.domain.sources import SOURCE_FAMILIES, SOURCE_METHODS, SourceMethod

MODULE = REPO_ROOT / "marketops" / "application" / "plan_preview.py"
PEER = REPO_ROOT / "marketops" / "application" / "research_strategy.py"
PROFILES_DIR = REPO_ROOT / "profiles"
DEFAULT_PROFILE = "uk-acme-natural-grass"

#: **同一自然语言任务**：四个市场用的是同一份（只有 ``geo`` 跟着声明走）。
#: 改这里就是改这一片声称的「同一个任务」。
TASK: dict[str, Any] = {
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

#: 四个市场：一个多语地区是**必须**的（关闭条件 1 逐字：GB/FR/DE + 至少一个多语
#: 地区 fixture）。它刻意是「一个国家、三种语言」——「一个国家一种语言」那种形状
#: 在报告上看不出多语地区与单语国家的区别。
MARKETS: dict[str, tuple[str, tuple[str, ...]]] = {
    "gb": ("GB", ("en-GB",)),
    "fr": ("FR", ("fr-FR",)),
    "de": ("DE", ("de-DE",)),
    "ch": ("CH", ("de-CH", "fr-CH", "it-CH")),
}

#: 硬歧义的每一种来路，以及这一层的回执该点名的那几维。
#:
#: 前六条来自**推导**（``ambiguities_of`` 逐维看出来的），后两条来自**客户端声明**
#: —— 后两条是刻意留的：``JURISDICTION`` 与 ``PRODUCT_USE`` 是硬的，而 ``ambiguities_of``
#: 的 docstring 明说它**不产出**这两维（推导永远不产出它们，只有声明能落到那两维上）。
#: 一份只在推导上成立的判据会把「客户端自己说的那条硬歧义」整条路漏掉。
HARD_CASES: dict[str, tuple[dict[str, Any], tuple[str, ...]]] = {
    "geo 空着": ({"geo": ""}, ("geo",)),
    "主体类别空着": ({"entity_classes": ()}, ("entity_classes",)),
    "资产类别空着": ({"asset_classes": ()}, ("asset_classes",)),
    "组织类别空着": ({"organization_classes": ()}, ("organization_classes",)),
    "场景没人定": ({"scenario_candidates": ()}, ("scenario",)),
    "目标空着": ({"objective": ""}, ("objective",)),
    "声明的管辖歧义": (
        {
            "declared_ambiguities": (
                Ambiguity(
                    dimension=ContextDimension.JURISDICTION,
                    detail="按哪一辖区的合规口径收证据？",
                ),
            )
        },
        ("jurisdiction",),
    ),
    "声明的产品口径歧义": (
        {
            "declared_ambiguities": (
                Ambiguity(
                    dimension=ContextDimension.PRODUCT_USE,
                    detail="这一批挂哪一个产品口径？",
                ),
            )
        },
        ("product_use",),
    ),
}

#: 上面那八条覆盖到的维度 —— 下面那条判据要的是与硬的那一半**集合相等**。
HARD_CASE_DIMENSIONS = frozenset(
    ContextDimension(item) for _, dimensions in HARD_CASES.values() for item in dimensions
)

#: §253 要求 preview 报告、而这一层答不上来的那一组。**逐条钉住**：省掉一条就是
#: 省掉一个交代，而省掉之后报告看起来只是短了一点。
NOT_REPORTED_ITEMS = (
    "已有事实复用多少",
    "预计补哪些缺口",
    "哪一条来源方法由哪个 provider 执行",
    "停在哪（stop 条件）",
    "把 hold 的那几条落成待办",
)

#: 这一层允许 import 的模块，**精确相等**（不是「不在这张黑名单上」）。
#: 一个白名单会把「顺手 import 一个客户端」变成一个必须刻意改这一行的动作。
ALLOWED_IMPORTS = frozenset(
    {
        "__future__",
        "dataclasses",
        "enum",
        "typing",
        "marketops.domain.errors",
        "marketops.domain.profile",
        "marketops.domain.research_context",
        "marketops.domain.research_plan",
        "marketops.domain.sources",
        "marketops.application.not_reported",
        "marketops.application.research_strategy",
    }
)

#: 内建那一边：只会以 ``open(...)`` / ``compile(src, ...)`` 这种**名字调用**的形状出现，
#: 而 import 白名单挡不住它们。
FORBIDDEN_BUILTIN_CALLS = frozenset({"open", "eval", "exec", "compile", "__import__"})

#: 属性那一边：``x.read_text()`` / ``io.open()`` 这种。**``x.compile()`` 不在这张表里**
#: —— 那是某个对象自己的方法（``StrategyCompiler.compile`` 就是这一层唯一那个），
#: 与内建 ``compile(src, ...)`` 是两件事。两者合成一张表时，「这一层不许动态执行
#: 代码」这条判据会红在一个完全正当的调用上，而它红了之后最省事的处置是把那条判据
#: 删掉 —— 所以两把量具分开，且各自有一个正控（见 `test_the_scanner_reads_calls_at_all`）。
FORBIDDEN_ATTRIBUTE_CALLS = frozenset(
    {
        "open",
        "read_text",
        "write_text",
        "read_bytes",
        "write_bytes",
        "urlopen",
        "connect",
        "popen",
        "sleep",
        "now",
        "utc_now",
        "fetch",
        "search",
        "read",
        "write",
    }
)


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


def _compose(*changes: Callable[[dict], None]) -> Callable[[dict], None]:
    """把几个 mutator 串成一个。"""

    def mutate(document: dict) -> None:
        for change in changes:
            change(document)

    return mutate


def _bundle_with(
    root: Path,
    *,
    profile: Callable[[dict], None] | None = None,
) -> ProfileBundle:
    """复制真实 profile、按需改 ``profile.yaml``，再走**真实装载器**读回来。

    ``sort_keys=False`` 与 ``tests/test_research_strategy.py`` 同一理由：
    ``page_discovery_hints`` 的档位顺序是**声明语义**（计划上那一格的顺序就是它）。
    """
    profiles_dir = root / "profiles"
    target = profiles_dir / DEFAULT_PROFILE
    profiles_dir.mkdir(parents=True, exist_ok=True)
    shutil.copytree(PROFILES_DIR / DEFAULT_PROFILE, target)

    if profile is not None:
        path = target / "profile.yaml"
        document = yaml.safe_load(path.read_text(encoding="utf-8"))
        profile(document)
        path.write_text(
            yaml.safe_dump(document, allow_unicode=True, sort_keys=False), encoding="utf-8"
        )

    settings = Settings(
        data_root=root / "runtime", profiles_dir=profiles_dir, profile=DEFAULT_PROFILE
    )
    return load_profile(settings)


def _market(root: Path, key: str) -> ProfileBundle:
    """一个市场的声明：**同一份真 profile**，只把国家与语言换成那个市场的。

    这不是「新发一份市场声明」（那是另一件事，要有人批准）。四份 fixture 的差别只有
    ``country`` 与 ``search.locales`` 两格，于是「同一个任务在四个市场上编出四份计划」
    这件事的变量只有市场本身。
    """
    country, locales = MARKETS[key]
    return _bundle_with(
        root,
        profile=_compose(
            _set(("country",), country),
            _set(("search", "locales"), list(locales)),
        ),
    )


def _context(bundle: ProfileBundle, **overrides: Any) -> ResearchContext:
    """同一份自然语言任务，``geo`` 跟着这份声明的国家走。"""
    declared = bundle.profile
    assert declared is not None, "夹具应当总是装载到声明块"
    base: dict[str, Any] = {"geo": declared.country, **TASK}
    base.update(overrides)
    return ResearchContext(**base)  # type: ignore[arg-type]


# --- AST 那一族共用的量具 ----------------------------------------------------


def _imported(path: Path) -> frozenset[str]:
    """一个模块 import 了哪些**绝对模块名**（相对 import 按包名还原）。"""
    package = ".".join(path.relative_to(REPO_ROOT).with_suffix("").parts[:-1])
    found: set[str] = set()
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level == 0:
                found.add(node.module or "")
                continue
            parts = package.split(".")
            prefix = ".".join(parts[: len(parts) - (node.level - 1)])
            found.add(f"{prefix}.{node.module}" if node.module else prefix)
    return frozenset(found)


def _name_calls(path: Path) -> frozenset[str]:
    """被**按名字**调用的那批（``open(...)``、``compile(...)``）—— 内建在这里。"""
    found: set[str] = set()
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            found.add(node.func.id)
    return frozenset(found)


def _attribute_calls(path: Path) -> frozenset[str]:
    """被**按属性**调用的那批（``x.read_text()``、``io.open()``、``c.compile()``）。"""
    found: set[str] = set()
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            found.add(node.func.attr)
    return frozenset(found)


# --- 1 · 四个市场，同一个任务 -------------------------------------------------


def test_four_markets_compile_the_same_task_into_four_plans(tmp_path: Path) -> None:
    """GB/FR/DE + 一个多语地区：**同一个任务**各自编出一份计划（关闭条件 1 的前半）。

    多语地区那一份特别要看的不是「编出来了」，而是 ``locales`` 那一格**一条不少、
    按声明序**：一个国家三种语言的地区如果只留下一两种，报告上看不出它少了 ——
    而这一格正是查询用什么语言问的依据（关闭条件 3 的「多语国家不会被强塞单一
    locale」在计划这一侧的读数）。
    """
    for key, (country, locales) in MARKETS.items():
        bundle = _market(tmp_path / key, key)
        preview = preview_plan(bundle, _context(bundle))

        assert preview.outcome is PreviewOutcome.READY, key
        plan = preview.plan
        assert plan is not None
        assert plan.geo == country, key
        assert plan.jurisdiction == country, key
        assert plan.locales == locales, f"{key}：语言那一格不是声明的全部、或不是声明序"


def test_the_same_market_and_the_same_task_compile_to_the_same_plan(tmp_path: Path) -> None:
    """同一份声明 + 同一份任务 → **逐字**同一份计划（关闭条件 1 的「可复现 digest」）。

    比的是 ``as_dict()`` 而不是只比 digest：一份摘要相同、报告不同的实现，会让
    「可复现」停在摘要那一格上，而人读的是报告。
    """
    for key in MARKETS:
        bundle = _market(tmp_path / key, key)
        task = _context(bundle)
        first = preview_plan(bundle, task)
        second = preview_plan(bundle, task)

        assert first.plan is not None and second.plan is not None, key
        assert first.plan.digest == second.plan.digest, key
        assert first.as_dict() == second.as_dict(), key


def test_a_different_market_is_a_different_plan(tmp_path: Path) -> None:
    """四个市场两两不同 —— 否则「同一份声明两次编译可复现」会在一份**忽略了市场**的
    实现上照样成立（它每次都很稳定地编出同一份东西）。"""
    digests: dict[str, str] = {}
    for key in MARKETS:
        bundle = _market(tmp_path / key, key)
        plan = preview_plan(bundle, _context(bundle)).plan
        assert plan is not None, key
        digests[key] = plan.digest

    for left, right in itertools.combinations(sorted(digests), 2):
        assert digests[left] != digests[right], f"{left} 与 {right} 编出了同一份计划"


def test_the_multilingual_market_keeps_all_of_its_locales(tmp_path: Path) -> None:
    """多语地区的语言那一格**三条都在**，且按声明序。

    单独写出来是因为它有两个不同的失败形状：少留几条（收窄过头），以及顺序变了
    （多语地区里「先问哪一种」是有依据的，不是排版）。
    """
    bundle = _market(tmp_path / "ch", "ch")
    preview = preview_plan(bundle, _context(bundle))

    assert preview.plan is not None
    assert preview.plan.locales == ("de-CH", "fr-CH", "it-CH")
    assert preview.plan.geo == "CH"


# --- 2 · 该停下来问人的那几维 ------------------------------------------------


@pytest.mark.parametrize(
    ("overrides", "dimensions"),
    list(HARD_CASES.values()),
    ids=list(HARD_CASES),
)
def test_a_hard_ambiguity_holds_instead_of_reporting_a_plan(
    tmp_path: Path, overrides: dict[str, Any], dimensions: tuple[str, ...]
) -> None:
    """硬歧义 → ``HOLD``、``plan is None``、**逐条点名**（关闭条件 4）。

    它**不是** ``ConfigError``：一份讲不清的输入是正常的业务情形，不是配置写错了。
    两条路都被调用方读成「这一次没有计划」，而去向不同 —— 前者要人回答，后者要换
    一份批准。
    """
    bundle = _bundle_with(tmp_path)
    preview = preview_plan(bundle, _context(bundle, **overrides))

    assert preview.outcome is PreviewOutcome.HOLD
    assert preview.plan is None, "HOLD 的那条路上不该有计划 —— 编出来的任何东西都会读起来完整"
    assert {item.dimension.value for item in preview.held} == set(dimensions)
    assert all(item.detail.strip() for item in preview.held), "回执里有一条说不出「哪里说不清」"


@pytest.mark.parametrize(
    ("overrides", "dimensions"),
    list(HARD_CASES.values()),
    ids=list(HARD_CASES),
)
def test_the_two_readers_of_one_table_agree(
    tmp_path: Path, overrides: dict[str, Any], dimensions: tuple[str, ...]
) -> None:
    """这一层判 HOLD 的那几维，**恰好**是编译器拒绝的那几维。

    ``fingerprint_of`` 与本模块读的是**同一张表**（``ambiguity_is_hard``）。真到了
    两处不一致的那一天，那不是一次 HOLD，那是这一层的判据该抓的错：一个把内部不
    一致渲染成「请人回答」的实现，会让那种不一致**永远**只表现为「最近老是让人
    确认」，而没有任何一处红。

    两处**各自算一遍**，谁也不调用谁：编译器那边从它抛出的消息里把那几维读回来
    （那行文本是 ``fingerprint_of`` 自己渲染的），这一层读的是 ``held``。
    """
    bundle = _bundle_with(tmp_path)
    context = _context(bundle, **overrides)
    expected = set(dimensions)

    with pytest.raises(ValueError) as caught:
        research_compiler(bundle).compile(
            bundle=bundle, context=context, envelope=envelope_of(bundle)
        )
    refused = set(re.findall(r"^\s+- \[([a-z_]+)\]", str(caught.value), flags=re.MULTILINE))

    preview = preview_plan(bundle, context)
    assert preview.outcome is PreviewOutcome.HOLD
    assert refused == expected, "编译器拒的那几维与这张表钉的不是同一批"
    assert {item.dimension.value for item in preview.held} == refused


def test_a_hold_never_reaches_the_compiler(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """HOLD 是**在编之前**算出来的，不是接住一个异常。

    把一个会炸的编译器换进去：HOLD 的那条路上它一次都不该被碰到。这条判据钉的是一个
    很容易退回去的形状 ——「先试一遍，抛了就报 HOLD」。那样写会让「同一张表两处读法
    不一致」这件事变得看不出来（见上一条），而且异常里那几行是给人读的散文，答不出
    §253 要的「哪几条」。
    """
    touched: list[ProfileBundle] = []

    def bomb(bundle: ProfileBundle) -> Any:
        touched.append(bundle)
        raise AssertionError("HOLD 的那条路上不该有编译器")

    monkeypatch.setattr(plan_preview, "research_compiler", bomb)
    bundle = _bundle_with(tmp_path)
    preview = preview_plan(bundle, _context(bundle, geo=""))

    assert preview.outcome is PreviewOutcome.HOLD
    assert touched == []


def test_soft_ambiguity_is_recorded_and_the_plan_still_compiles(tmp_path: Path) -> None:
    """软歧义不拦路（关闭条件 4 的另一半）：照编，但**记下来**。

    记下来这件事不是礼节：一份不说自己是在哪几处模糊之上编出来的计划，会把「这次
    按保守取法多问了一遍」读成「这次本来就该这么问」。
    """
    bundle = _bundle_with(tmp_path)
    declared = Ambiguity(dimension=ContextDimension.LOCALES, detail="要不要同时用两种语言问？")
    context = _context(bundle, evidence_requirements=(), declared_ambiguities=(declared,))

    preview = preview_plan(bundle, context)

    assert preview.outcome is PreviewOutcome.READY
    assert preview.plan is not None
    assert preview.held == ()
    assert {item.dimension for item in preview.recorded} == {
        ContextDimension.LOCALES,
        ContextDimension.EVIDENCE_REQUIREMENTS,
    }
    # 计划上那一栏与这一层报的是同一批 —— 计划自己记了一份、报告里再算一遍的话，
    # 两处会分叉，而分叉的那天没有一处会红。
    assert preview.plan.ambiguities == preview.recorded


def test_the_two_columns_are_the_whole_context_and_keep_its_order(tmp_path: Path) -> None:
    """``held`` 与 ``recorded`` 合起来就是 ``ambiguities_of`` 的全部，且保持顺序。

    分栏分流不能顺手丢掉一条：一条既不硬也不软的歧义（今天没有这种成员，下一个成员
    会造出来）在分栏的时候最容易掉在地上，而它掉下去之后报告仍然完整。
    """
    bundle = _bundle_with(tmp_path)
    context = _context(bundle, geo="", evidence_requirements=())
    preview = preview_plan(bundle, context)

    assert preview.held + preview.recorded == ambiguities_of(context)
    assert [item.dimension.value for item in preview.held] == ["geo"]
    assert [item.dimension.value for item in preview.recorded] == ["evidence_requirements"]


def test_a_request_outside_the_envelope_is_refused_not_held(tmp_path: Path) -> None:
    """越界**不**落成 HOLD：那是治理问题，不是「再问一次用户」能解决的。

    用户再说一遍也不会让 ``FR`` 落进一份 ``GB`` 的 profile 里。两个都读成「这一次
    没编出计划」的话，「等一个人回答」与「等一次治理变更」在报告上同形。
    """
    bundle = _bundle_with(tmp_path)

    with pytest.raises(ConfigError):
        preview_plan(bundle, _context(bundle, geo="FR"))

    with pytest.raises(ConfigError):
        preview_plan(bundle, _context(bundle, candidate_locales=("fr-FR",)))


def test_a_profile_that_cannot_say_what_is_approved_is_a_failure_not_a_hold(
    tmp_path: Path,
) -> None:
    """读不出批准范围 → 抛，**不**退成一次 HOLD。

    一份连国家都说不出的 profile 是写坏了，不是「客户还没说清」。退成 HOLD 的话，
    一次接线错误会伪装成一个等客户回答的待办，而它永远不会被回答。
    """
    bundle = _bundle_with(tmp_path)
    stripped = dataclasses.replace(bundle, profile=None)

    with pytest.raises(ValueError) as caught:
        preview_plan(stripped, _context(bundle))

    assert "声明块" in str(caught.value)


# --- 3 · 报告的形状 ----------------------------------------------------------


def test_the_report_is_json_serializable(tmp_path: Path) -> None:
    """``as_dict()`` 能原样出线 —— 它是 MCP 只读面要交出去的那一份。

    两种结局都判：只有 READY 那一份能序列化的话，**失败**的那一次会在线路上炸，
    而失败的那一次恰好是人最需要读到它的时候。
    """
    bundle = _bundle_with(tmp_path)
    for context in (_context(bundle), _context(bundle, geo="")):
        report = preview_plan(bundle, context).as_dict()
        assert json.loads(json.dumps(report)) == report


def test_the_report_keeps_every_cell_and_its_origin(tmp_path: Path) -> None:
    """报告里**十七格各有一条出处**，模块按优先序，摘要与计划一致。

    这一条防的是「预览是一份重新渲染的计划」：报告另算一遍值或出处的话，两处会分叉，
    而分叉的那天报告读起来仍然完整。
    """
    bundle = _bundle_with(tmp_path)
    preview = preview_plan(bundle, _context(bundle))
    plan = preview.plan
    assert plan is not None

    block = preview.as_dict()["plan"]
    assert block["digest"] == plan.digest
    assert block["scene_version"] == plan.scene_version
    assert block["fingerprint_digest"] == plan.fingerprint_digest
    assert {entry["field"] for entry in block["provenance"]} == {
        field.value for field in PlanField
    }
    assert [entry["module_id"] for entry in block["modules"]] == [
        item.module_id.value for item in plan.modules
    ]
    positions = [list(MODULE_PRIORITY).index(item.module_id) for item in plan.modules]
    assert positions == sorted(positions), "模块那一栏不是按 MODULE_PRIORITY 排的"


def test_the_report_is_the_plan_cell_by_cell(tmp_path: Path) -> None:
    """报告里那十七格的值**逐格**等于计划上的值，且一格不多、一格不少。

    上一条判的是「每一格都有出处」，这一条判的是「报告里的值就是计划里的值」。两份
    各写一遍的渲染会在某次改动里分叉，而分叉之后报告仍然完整、摘要仍然一致 ——
    它是人读到的那一份，也是 MCP 只读面要交出去的那一份。
    """
    bundle = _bundle_with(tmp_path)
    preview = preview_plan(bundle, _context(bundle))
    plan = preview.plan
    assert plan is not None

    block = preview.as_dict()["plan"]
    rendered = {
        **block["declared"],
        **block["strategy"],
        **{name: block["budget"][name] for name in ("search_budget", "fetch_budget")},
    }

    assert set(rendered) == {field.value for field in PlanField}
    for name, value in rendered.items():
        expected = getattr(plan, name)
        if name == "page_archetypes":
            assert value == [
                {"tier": item.tier, "patterns": list(item.patterns)} for item in expected
            ], name
        elif isinstance(expected, tuple):
            assert value == list(expected), name
        else:
            assert value == expected, name


def test_the_budget_cells_come_from_the_declaration(tmp_path: Path) -> None:
    """两格额度从声明读出来，**没声明时报 ``None`` 而不是 0**。

    ``0`` 是「已声明为零额度」（一次都跑不了，而那是必须显性失败的）；``None`` 是
    「没声明」。两者在报告上合成一个数字的话，「这份 profile 忘了写预算」会读成
    「这次预算很小」。
    """
    bundle = _bundle_with(tmp_path)
    declared = bundle.profile
    assert declared is not None

    block = preview_plan(bundle, _context(bundle)).as_dict()["plan"]["budget"]
    assert block["search_budget"] == declared.search_max_queries
    assert block["fetch_budget"] == declared.fetch_max_documents
    assert block["undeclared"] == []

    dropped = _bundle_with(
        tmp_path / "dropped",
        profile=_compose(_drop(("search", "max_queries")), _drop(("fetch", "max_documents"))),
    )
    missing = preview_plan(dropped, _context(dropped)).as_dict()["plan"]["budget"]
    assert missing["search_budget"] is None
    assert missing["fetch_budget"] is None
    assert missing["undeclared"] == ["search_budget", "fetch_budget"]


def test_the_sources_carry_their_family_and_what_they_answer(tmp_path: Path) -> None:
    """来源那一格逐条带上族与「它能回答什么」，顺序与计划一致。

    一个只有 id 的报告里，``search.engine.v1`` 对读的人是装饰；而族是「来源族多样性」
    那个指标唯一的依据。
    """
    bundle = _bundle_with(tmp_path)
    preview = preview_plan(bundle, _context(bundle))
    plan = preview.plan
    assert plan is not None

    assert tuple(item.id for item in preview.sources) == plan.source_methods
    for item in preview.sources:
        assert item is SOURCE_METHODS[item.id]
        assert item.family in SOURCE_FAMILIES
        assert item.answers.strip()

    rendered = preview.as_dict()["sources"]
    assert [entry["id"] for entry in rendered] == list(plan.source_methods)
    assert [entry["family"] for entry in rendered] == [item.family for item in preview.sources]


@dataclasses.dataclass(frozen=True)
class _PlanShell:
    """只为 ``_methods_of`` 用的壳子：那个帮手只读 ``source_methods`` 一格。"""

    source_methods: tuple[str, ...]


def test_a_source_method_outside_the_registry_is_refused(tmp_path: Path) -> None:
    """注册表外的 id → 抛，**不留一个空的族**。

    **这条判据只能直接调用那个帮手来跑**：``preview_plan`` 走不到这里，因为来源那一格
    的值由 ``research_strategy`` 在装配期解析一次，拼错的 id 到不了计划上。写成直接调用
    是为了让这条失败关闭**被执行过一次** —— 一个从不响的分支与一个没有的分支在代码里
    长得一样，而它哪一天真的响起来，正是「那一格的来源不再由那个解析器过滤」的那一刻。
    """
    shell = _PlanShell(source_methods=("search.engine.v9",))

    with pytest.raises(ConfigError) as caught:
        plan_preview._methods_of(shell)  # type: ignore[arg-type]

    assert "search.engine.v9" in str(caught.value)
    assert "search.engine.v1" in str(caught.value), "回执要连注册表一起报出来"


# --- 4 · 这一层够不到外部 ----------------------------------------------------


def test_the_scanner_reads_imports_at_all() -> None:
    """正控：同一把量具在一个**已知进口了端口**的模块上必须读到东西。

    没有这一条，下面那条白名单判据会在「一个 import 都没扫到」的时候也是绿的 ——
    而那时它验的是它自己坏掉了。
    """
    assert "marketops.ports.acquisition" in _imported(PEER)
    assert "shutil" in _imported(Path(__file__))
    assert "marketops.application.plan_preview" in _imported(Path(__file__))


def test_the_layer_imports_exactly_what_it_needs() -> None:
    """import 白名单**精确相等**（关闭条件 1 后半：preview 零 provider 调用）。

    写成白名单而不是黑名单：一个 ``import socket`` 与一个 ``import requests`` 都在
    黑名单之外，而在白名单之外。多一个 import 就是多一条通向外部世界的路，而这条路
    要么是刻意加上去的（那时改这一行），要么不该在。

    这条同时是两个 ``not_reported`` 条目的前提：复用与缺口要读池子，而池子那一族的
    模块连 import 都不在这张名单上。
    """
    found = _imported(MODULE)
    assert found == ALLOWED_IMPORTS, f"多出来的：{sorted(found - ALLOWED_IMPORTS)}"
    assert not any("pool" in name or "reuse" in name for name in found)


def test_the_scanner_reads_calls_at_all() -> None:
    """正控：两把量具都真的读到了调用。

    没有这一条，「调用名里没有 I/O」这条判据会在扫描器什么都不返回的时候也是绿的 ——
    而那时它验的是它自己坏掉了。这里点名的两个都是这一层**确实有**的调用：
    ``SOURCE_METHODS.get``（取值）与 ``StrategyCompiler.compile``（编）。
    """
    assert {"get", "compile"} <= _attribute_calls(MODULE)
    assert "ConfigError" in _name_calls(MODULE)


def test_the_layer_calls_no_io_and_no_clock() -> None:
    """调用名里没有 I/O、没有时钟，也没有动态执行。

    内建与属性分成两张表：``x.compile()`` 是 ``StrategyCompiler`` 自己的方法，不是
    内建 ``compile(src, ...)`` —— 合成一张表时这条判据会红在一个正当的调用上，而它
    红了之后最省事的处置是把判据删掉。
    """
    assert _name_calls(MODULE) & FORBIDDEN_BUILTIN_CALLS == set()
    assert _attribute_calls(MODULE) & FORBIDDEN_ATTRIBUTE_CALLS == set()


def test_the_layer_takes_exactly_a_declaration_and_a_context() -> None:
    """签名就是那两样，**没有第三个入口**。

    多一个参数就是一个可以传端口进来的口子；而少一个（例如自己推一份 envelope）就是
    替调用方认一次批准 —— 见模块 docstring。两个都要在这里被看见。
    """
    signature = inspect.signature(preview_plan)
    assert list(signature.parameters) == ["bundle", "context"]
    for parameter in signature.parameters.values():
        assert parameter.kind is inspect.Parameter.POSITIONAL_OR_KEYWORD
        assert parameter.default is inspect.Parameter.empty


def test_the_layer_catches_nothing() -> None:
    """这一层里**一个 ``except`` 都没有**。

    这不是风格：HOLD 在编之前算出来，所以没有任何异常需要在这里被接住。一旦有人把
    ``compile`` 包进 ``try/except``，那两处对同一张表的读法就再也没人核对得起来了
    （见 ``test_the_two_readers_of_one_table_agree``）。
    """
    tree = ast.parse(MODULE.read_text(encoding="utf-8"))
    handlers = [node for node in ast.walk(tree) if isinstance(node, ast.Try)]
    assert handlers == [], "这一层不该接住任何东西 —— HOLD 是在编之前算出来的"


# --- 5 · 答不上来的那几件事 --------------------------------------------------


def test_not_reported_is_pinned_and_every_entry_says_why(tmp_path: Path) -> None:
    """§253 那几项**逐条**在报告里，每条带一句为什么。

    **改这一份就是改这一层对外声称答不上来的东西** —— 所以它逐字钉住，而不是判个非空。
    """
    assert tuple(item.item for item in NOT_REPORTED) == NOT_REPORTED_ITEMS
    for item in NOT_REPORTED:
        assert item.reason.strip(), item.item

    bundle = _bundle_with(tmp_path)
    for context in (_context(bundle), _context(bundle, geo="")):
        report = preview_plan(bundle, context).as_dict()
        assert tuple(entry["item"] for entry in report["not_reported"]) == NOT_REPORTED_ITEMS


def test_every_gap_reason_still_has_its_premise() -> None:
    """每一条「答不上来」的理由，**前提**在这里被核对一遍。

    一句话写下的理由会在它不再成立的那天继续读起来像真的。这里逐条核三个前提：

    * 「把 hold 落成待办」缺一个原因成员 —— ``ReviewReason`` 至今只有两个定级冲突；
    * 「哪条方法由哪个 provider 执行」没有映射 —— ``SourceMethod`` 上**没有** provider
      字段（一张三字段的表里塞不下那个映射）；
    * stop 条件不在计划上 —— 十七格里没有一格叫这个。
    """
    assert {reason.value for reason in ReviewReason} == {
        "identity_conflict",
        "evidence_conflict",
    }
    assert [field.name for field in dataclasses.fields(SourceMethod)] == [
        "id",
        "family",
        "answers",
    ]
    names = [field.value for field in PlanField]
    assert len(names) == 17, names
    assert not any("stop" in name or "qualified" in name for name in names), names


def test_the_three_other_gap_items_really_are_reported(tmp_path: Path) -> None:
    """答得上来的那三件真的在报告里。

    「五件事」不能静默地变成「两件 + 三句抱歉」：一个把 ``sources`` 或 ``plan`` 留空的
    实现，配上那份诚实的 ``not_reported``，读起来仍然像一份交代完整的预览。
    """
    bundle = _bundle_with(tmp_path)
    report = preview_plan(bundle, _context(bundle)).as_dict()

    assert report["plan"] is not None
    assert report["plan"]["budget"] is not None
    assert report["sources"]


# --- 6 · 自相矛盾的两格写不出来 ----------------------------------------------


def test_a_preview_that_contradicts_itself_cannot_be_written_by_hand(tmp_path: Path) -> None:
    """三条「两个字段各写一遍就会有一个自洽的假答案」的组合，构造期就拒。

    直接构造是为了让这三条守卫**被执行过一次**：它们只能被直接构造违反，而「一个没人
    能违反的守卫与一个从不响的守卫在报告上长得一样」正是它们存在的理由。
    """
    bundle = _bundle_with(tmp_path)
    ready: PlanPreview = preview_plan(bundle, _context(bundle))
    held: PlanPreview = preview_plan(bundle, _context(bundle, geo=""))
    assert ready.plan is not None and held.held

    with pytest.raises(ValueError) as no_plan:
        dataclasses.replace(ready, plan=None)
    assert "HOLD" in str(no_plan.value)

    with pytest.raises(ValueError) as plan_for_hold:
        dataclasses.replace(held, outcome=PreviewOutcome.READY)
    assert "HOLD" in str(plan_for_hold.value)

    with pytest.raises(ValueError) as silent_hold:
        dataclasses.replace(held, held=())
    assert "点名" in str(silent_hold.value)

    with pytest.raises(ValueError) as sourceless:
        dataclasses.replace(ready, sources=())
    assert "来源" in str(sourceless.value)


def test_the_two_outcomes_are_a_closed_set() -> None:
    """结局是闭集的两个成员，且值写成小写字符串（报告里读到的就是它）。

    第三个成员迟早会来（「编得出来，但计划上有一格今天没有生产者」是一个候选），而它
    来时这里要红 —— 一个叫 ``ok`` 的布尔在那个时刻会把它读成 ``False``。
    """
    assert [member.value for member in PreviewOutcome] == ["ready", "hold"]


def test_the_report_says_which_ambiguities_are_soft(tmp_path: Path) -> None:
    """软的那几条在报告里也有一位（``recorded``），且**两栏都写出来**。

    只写非空那一栏的话，读者分不清「这一次没有硬歧义」与「这一栏没人想过要填」。
    """
    bundle = _bundle_with(tmp_path)

    ready = preview_plan(bundle, _context(bundle, evidence_requirements=())).as_dict()
    assert ready["held"] == []
    assert [item["dimension"] for item in ready["recorded"]] == ["evidence_requirements"]

    stopped = preview_plan(bundle, _context(bundle, geo="", evidence_requirements=())).as_dict()
    assert [item["dimension"] for item in stopped["held"]] == ["geo"]
    assert [item["dimension"] for item in stopped["recorded"]] == ["evidence_requirements"]


# --- 7 · 这一层的分流与硬软那张表 --------------------------------------------


def test_the_hard_half_of_the_table_is_the_eight_dimensions() -> None:
    """硬的那一半逐格钉住。**这张表在动，这一层的分流就该被人看一眼。**

    这不是 ``tests/test_research_context.py`` 的判据的复读：那一族判的是「表与推导
    一致」，这里判的是「这一层按哪一半分流」—— 表多一维软、少一维硬，两次分栏都会
    跟着变，而报告上两栏都仍然写得满满的。
    """
    assert {item for item in ContextDimension if ambiguity_is_hard(item)} == {
        ContextDimension.GEO,
        ContextDimension.JURISDICTION,
        ContextDimension.ENTITY_CLASSES,
        ContextDimension.ASSET_CLASSES,
        ContextDimension.ORGANIZATION_CLASSES,
        ContextDimension.SCENARIO,
        ContextDimension.OBJECTIVE,
        ContextDimension.PRODUCT_USE,
    }


def test_the_hand_written_cases_cover_every_hard_dimension() -> None:
    """上面那八条手工的上下文覆盖**每一个**硬维度 —— 少一维就是少一条路。

    ``JURISDICTION`` 与 ``PRODUCT_USE`` 尤其要紧：``ambiguities_of`` 明说它**不产出**
    那两维，所以它们**只能**由客户端声明产生，而它们是硬的。
    """
    assert HARD_CASE_DIMENSIONS == {
        ContextDimension.GEO,
        ContextDimension.JURISDICTION,
        ContextDimension.ENTITY_CLASSES,
        ContextDimension.ASSET_CLASSES,
        ContextDimension.ORGANIZATION_CLASSES,
        ContextDimension.SCENARIO,
        ContextDimension.OBJECTIVE,
        ContextDimension.PRODUCT_USE,
    }
