"""``marketops/application/run_preview.py`` 的判据 —— 受理**之前**那一份读数。

这一族盯的四件事，没有一件是「会崩的失败」：

1. **这一层偷偷变得能花钱。**「Preview 本身零 provider 调用」（§B17 正文 ``:253``）
   如果只是靠人自觉，那么接线的那一天由「谁顺手加了一个客户端参数」决定它花不花
   钱 —— 所以这里按 AST 扫这一层的 import **白名单**、调用名、以及**类型注解里出现
   的名字**，各有一个正控。
2. **一次重发被链的阈值拦下。** 受理面的次序是「重放检查在窗口之前」，所以一条已经
   走到 stop 的链上重发同一个键**照旧拿回那一行**。把这两步的顺序反过来在报告上只
   表现为「这次提交会被拒」，而处置完全不同（一个是去看那一行，一个是去挪窗口）。
3. **账本没接上被读成「还没花过」。** ``spent is None`` ⇒ 拒（fail-closed）是
   ``window_refusal`` 的第四条，而这一层是它今天唯一一个会递 ``None`` 的调用方。
   读成 0 的后果是「一次部署漏接」与「一条还有满额度的链」在报告上同形。
4. **§253 那五项被静默省掉一项。** 省掉之后报告看起来只是短了一条，而读的人会以为
   「preview 报告了五件事」。所以减法的那三条名字逐字钉住，且与计划层那一批是
   **同一个对象**（不是重抄）。

真正的 Run 存储与账本在这里都用**不落盘**的那两个替身（``InMemoryRunStore`` /
``InMemoryBudgetLedger``）：这一族要判的是「读数对不对」，不是「SQLite 那一份落得
对不对」—— 后者是 ``tests/test_run_window_admission.py`` 与契约测试的地盘。
"""

from __future__ import annotations

import ast
import inspect
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest

from marketops.adapters.memory import InMemoryBudgetLedger, InMemoryRunStore
from marketops.application.plan_preview import (
    NOT_REPORTED,
    NotReported,
    PreviewOutcome,
    preview_plan,
)
from marketops.application.run_admission import spec_of
from marketops.application.run_increment import (
    IncrementCell,
    IncrementVerdict,
    counts_of,
    works_of,
)
from marketops.application.run_preview import (
    _ANSWERED_WHEN_READY,
    _EXPECTED_WHEN_HOLD,
    _EXPECTED_WHEN_READY,
    _HOLD_REASONS,
    _NOTE,
    IncrementReading,
    RunPreview,
    StopReadout,
    StopVerdict,
    held_report,
    run_preview,
)
from marketops.config import REPO_ROOT
from marketops.domain.enums import BudgetLine, ExecutionMode, RunStatus
from marketops.domain.errors import RunReconciliationError
from marketops.domain.maintenance import (
    ALL_ASPECTS,
    Aspect,
    Observation,
    RefreshPlan,
)
from marketops.domain.outreach import OutreachPolicyDecl
from marketops.domain.profile import ProfileBundle
from marketops.domain.research_context import ResearchContext
from marketops.domain.run import (
    AuthorizationWindow,
    RunBudgetLine,
    RunSpec,
    run_id,
    spec_fingerprint,
)
from marketops.domain.run_delta import DeltaCell, PoolEffect, RunDelta
from marketops.domain.scene import SceneBudgetLine, SceneVersion
from marketops.ports.runs import ChainProgress

MODULE = REPO_ROOT / "marketops" / "application" / "run_preview.py"
PEER = REPO_ROOT / "marketops" / "application" / "plan_preview.py"
LINKED = REPO_ROOT / "marketops" / "application" / "billed_search.py"

WORKSPACE = "uk-acme-natural-grass"
SCENE_ID = "uk-acme-natural-grass"
KEY = "run-2026-09-28-p"
KEY2 = "run-2026-09-28-p2"

T0 = datetime(2026, 9, 28, 9, 0, tzinfo=timezone.utc)
CUTOFF = T0 + timedelta(days=1)
PAST_CUTOFF = T0 - timedelta(days=1)
WINDOW_UNTIL = T0 + timedelta(days=30)
STOPPED_AT = T0 + timedelta(hours=2)
#: 上一轮看过、还没到任何一格的窗口（最短的维护窗口是 30 天）。
FRESH = T0 - timedelta(days=1)
#: 上一轮看过、已经超过最长的那一档窗口（180 天）。
STALE = T0 - timedelta(days=200)

#: 一份说得清楚的声明，逐字取自 ``tests/test_run_admission.py::TASK`` 的形状
#: —— 两处都走**真实的** ``ResearchContext`` 校验器。
CONTEXT: dict[str, Any] = {
    "geo": "GB",
    "entity_classes": ["school"],
    "asset_classes": ["pitch"],
    "organization_classes": ["trust"],
    "scenario_candidates": [
        {
            "name": "secondary school",
            "reading": "k12",
            "provenance": "declared",
            "source": "req-1",
        }
    ],
    "objective": "find accounts for the approved offer",
    "evidence_requirements": ["official_site"],
}

#: §253 那五项 —— 逐字引自 ``plan_preview.NOT_REPORTED``，**不在这里重写**。
#: 这一份只是给下面几条判据一个可数的分母（``len`` / 集合差）。
NOT_REPORTED_ITEMS = tuple(item.item for item in NOT_REPORTED)

#: 这一层允许 import 的模块，**精确相等**（不是「不在这张黑名单上」）。
#: 三样端口是**本地存储**（Run 记录 / 场景与维护账 / 预算账本）—— 少了它们答不出
#: §253 的前两项；而检索与抓取那一族**一个都不在**，所以「不花一次检索」在这里不是
#: 自觉，是它没有可以 import 的东西。
ALLOWED_IMPORTS = frozenset(
    {
        "__future__",
        "collections.abc",
        "dataclasses",
        "datetime",
        "enum",
        "typing",
        "marketops.domain.enums",
        "marketops.domain.maintenance",
        "marketops.domain.run",
        "marketops.domain.scene",
        "marketops.ports.budget",
        "marketops.ports.runs",
        "marketops.ports.scenes",
        "marketops.ports.store",
        "marketops.application.not_reported",
        "marketops.application.plan_preview",
        "marketops.application.run_admission",
        "marketops.application.run_increment",
    }
)

#: 内建那一边：只会以 ``open(...)`` / ``compile(src, ...)`` 这种**名字调用**的形状出现。
FORBIDDEN_BUILTIN_CALLS = frozenset({"open", "eval", "exec", "compile", "__import__"})

#: 属性那一边。**与 ``test_plan_preview.py`` 那一份逐字相同** —— 两张表的理由写在
#: 那个文件的常量旁边（``x.compile()`` 是对象自己的方法，不是内建 ``compile``）。
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

#: **注解里**不得出现的名字。这一条比调用名那条更强：一个拿了 ``SearchProvider`` 的
#: 层，调用它只是下一行的事；而一个**拿不到**它的层，那句「不花一次检索」是结构。
#: 名字取自 ``ports/search.py`` 与 ``ports/acquisition.py`` 的类名。
FORBIDDEN_ANNOTATION_NAMES = frozenset(
    {
        "SearchProvider",
        "SearchQuery",
        "SearchHit",
        "SearchResult",
        "Fetcher",
        "HttpFetcher",
        "BrowserFetcher",
        "FetchRequest",
        "FetchResult",
        "FetchMode",
        "DocumentFetch",
    }
)


# --- 夹具 ---------------------------------------------------------------------


def _scene() -> SceneVersion:
    """盘上那一版生效的 Scene，逐字取自 ``tests/test_mcp_runs.py::_scene``。"""
    return SceneVersion(
        scene_id=SCENE_ID,
        workspace=WORKSPACE,
        version=1,
        region=("GB-England-Devon",),
        scenario="sports-surface-maintenance",
        product="acme-natural-grass",
        admissions=("sports-club",),
        exclusions=("golf-course",),
        role_policy="role-policy-v1",
        source_plan=("mother-pool-import",),
        budget=(SceneBudgetLine(line=BudgetLine.SEARCH, ceiling=20),),
        refresh=RefreshPlan.uncalibrated_default(),
        outreach=OutreachPolicyDecl.uncalibrated_default(),
        profile_id=WORKSPACE,
        profile_version="2026-09-01",
        created_at=T0,
        supersedes=None,
    )


def _context(**overrides: Any) -> ResearchContext:
    return ResearchContext(**{**CONTEXT, **overrides})  # type: ignore[arg-type]


def _ready(bundle: ProfileBundle, **overrides: Any) -> tuple[ResearchContext, Any]:
    """一份编得出来的计划 —— 附一份**说得清楚的**上下文。

    ``HOLD`` 的那一份在 :func:`_held` 里，两者刻意分成两个函数：一份 `README` 式的
    夹具（「要一份 READY 就给这个」）在 ``HOLD`` 那几条判据里会被绕开，而它们要的
    正是「计划没编出来」那一刻的读数。
    """
    context = _context(**overrides)
    report = preview_plan(bundle, context)
    assert report.outcome is PreviewOutcome.READY, "夹具应当总是编得出来"
    assert report.plan is not None
    return context, report


def _held(bundle: ProfileBundle) -> Any:
    """计划编不出来的一份读数 —— 硬歧义（场景那一格没人定）。"""
    report = preview_plan(bundle, _context(scenario_candidates=[]))
    assert report.outcome is PreviewOutcome.HOLD
    assert report.plan is None
    return report


def _window(
    *, max_objects: int = 50, ceiling: int = 100, every_line: bool = False
) -> AuthorizationWindow:
    """一条授权链 —— ``digest`` 是一个内容摘要，所以改一个阈值就是**另一条链**。

    ``every_line`` 造一份**每个科目一格**的窗口。它不是默认：``RunSpec`` 的跨格
    校验器要求「这一轮的信封不得有窗口没有的科目」，所以一条信封里多一格、而窗口
    只提到 ``search`` 的规格**构造不出来** —— 那条规则本身有它自己的判据
    （``tests/test_run_admission.py``）。
    """
    return AuthorizationWindow(
        max_objects=max_objects,
        budget=tuple(
            RunBudgetLine(line=line, ceiling=ceiling if line is BudgetLine.SEARCH else 0)
            for line in (BudgetLine if every_line else (BudgetLine.SEARCH,))
        ),
        not_after=WINDOW_UNTIL,
    )


def _spec(
    context: ResearchContext,
    plan: Any,
    *,
    mode: ExecutionMode = ExecutionMode.BOUNDED_AUTO,
    window: AuthorizationWindow | None = None,
    cutoff: datetime = CUTOFF,
    key: str = KEY,
    budget: tuple[RunBudgetLine, ...] | None = None,
    scene: SceneVersion | None = None,
) -> RunSpec:
    """这一次会写下的那份规格 —— 走 ``spec_of``，本仓**唯一**的生产者。"""
    if mode is ExecutionMode.BOUNDED_AUTO and window is None:
        window = _window()
    return spec_of(
        scene=scene or _scene(),
        context=context,
        plan=plan,
        budget=budget or (RunBudgetLine(line=BudgetLine.SEARCH, ceiling=20),),
        cutoff=cutoff,
        idempotency_key=key,
        execution_mode=mode,
        window=window,
    )


def _delta(*, keys: tuple[str, ...] = ("v-1",), usage: tuple[str, ...] = ()) -> RunDelta:
    """一份有内容的 Delta。``usage`` 是**预留 id**，不是金额（见 ``RunDelta``）。"""
    return RunDelta(
        effects=tuple(
            PoolEffect(cell=DeltaCell.DISCOVERED, key=key, label=f"场地 {key}")
            for key in keys
        ),
        usage=usage,
    )


def _chain(
    context: ResearchContext,
    plan: Any,
    *,
    max_objects: int = 50,
    usage: tuple[str, ...] = (),
    keys: tuple[str, ...] = ("v-1",),
    window: AuthorizationWindow | None = None,
) -> tuple[InMemoryRunStore, AuthorizationWindow]:
    """受理一轮、结束它 —— 「这条链已经走过一轮」这个状态。

    走的是存储自己的两个方法（``admit`` / ``stop``），不是直接塞 ``Run``：链上的
    累计读的是**盘上那几行**，手造一批 ``Run`` 值给 ``chain_progress`` 会绕开
    「这一行真的在存储里」这件事，而重放检查读的正是它。
    """
    store = InMemoryRunStore()
    chosen = window or _window(max_objects=max_objects)
    spec = _spec(context, plan, window=chosen, key=KEY, scene=None)
    run = store.admit(
        spec, spec_sha256=spec_fingerprint(spec), at=T0, spent={BudgetLine.SEARCH: 0}
    )
    store.stop(
        run.run_id,
        delta=_delta(keys=keys, usage=usage),
        status=RunStatus.COMPLETED,
        at=STOPPED_AT,
    )
    return store, chosen


def _observation(
    aspect: Aspect, *, subject: str = "acct-1", kind: str = "account", at: datetime = FRESH
) -> Observation:
    return Observation(
        workspace=WORKSPACE,
        subject_kind=kind,
        subject_id=subject,
        aspect=aspect,
        observed_at=at,
        source_ref="run-previous",
    )


def _all_aspects(*, at: datetime = FRESH) -> tuple[Observation, ...]:
    return tuple(_observation(aspect, at=at) for aspect in ALL_ASPECTS)


# --- AST 那一族共用的量具（与 ``test_plan_preview.py`` 同形） ------------------


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
    """被**按属性**调用的那批（``x.read_text()``、``c.compile()``）。"""
    found: set[str] = set()
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            found.add(node.func.attr)
    return frozenset(found)


def _annotation_names(path: Path) -> frozenset[str]:
    """**类型注解里**出现过的名字（参数、返回值、以及带注解的赋值）。

    ``ast.Name`` 抓的是裸名字（``search: SearchProvider``），``ast.Attribute`` 抓的是
    带限定的一层（``search: ports.SearchProvider``）—— 两条都要，否则给端口加一层
    模块前缀就能绕过去。
    """
    found: set[str] = set()
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        annotation: ast.expr | None = None
        if isinstance(node, ast.arg):
            annotation = node.annotation
        elif isinstance(node, ast.AnnAssign):
            annotation = node.annotation
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            annotation = node.returns
        if annotation is None:
            continue
        for inner in ast.walk(annotation):
            if isinstance(inner, ast.Name):
                found.add(inner.id)
            elif isinstance(inner, ast.Attribute):
                found.add(inner.attr)
    return frozenset(found)


# --- 1 · 这一层拿不到花钱的东西 ----------------------------------------------


def test_the_scanner_reads_imports_at_all() -> None:
    """正控：同一把量具在一个**已知 import 了端口**的模块上必须读到东西。

    没有这一条，下面那条白名单判据会在「一个 import 都没扫到」的时候也是绿的 ——
    而那时它验的是它自己坏掉了。``plan_preview.py`` 与 ``billed_search.py`` 是两个
    已知的对照：前者 import 了 ``research_strategy``（本层的**邻居**，不在本层白名单
    上），后者 import 了检索端口。
    """
    assert "marketops.application.research_strategy" in _imported(PEER)
    assert "marketops.ports.search" in _imported(LINKED)
    assert "ast" in _imported(Path(__file__))


def test_the_layer_imports_exactly_what_it_needs() -> None:
    """import 白名单**精确相等**（「Preview 本身零 provider 调用」）。

    写成白名单而不是黑名单：一个 ``import socket`` 与一个 ``import httpx`` 都在黑名单
    之外，也在白名单之外。多一个 import 就是多一条通向外部世界的路，而这条路要么是
    刻意加上去的（那时改这一行），要么不该在。

    三样本地存储端口在这一层**必须**在：``ports.runs`` / ``ports.scenes`` /
    ``ports.budget`` / ``ports.store`` —— 少了它们 §253 的前两项答不出来。
    """
    found = _imported(MODULE)
    assert found == ALLOWED_IMPORTS, (
        f"多出来的：{sorted(found - ALLOWED_IMPORTS)}；"
        f"少掉的：{sorted(ALLOWED_IMPORTS - found)}"
    )
    assert not any("search" in name or "acquisition" in name for name in found)


def test_the_scanner_reads_calls_at_all() -> None:
    """正控：两把量具都真的读到了调用。

    这里点名的两个都是这一层**确实有**的调用：``window_refusal``（受理面第 4 步）与
    ``admission_refusal``（第 2 步）。
    """
    assert {"get", "chain_progress"} <= _attribute_calls(MODULE)
    assert {"admission_refusal", "window_refusal", "run_id"} <= _name_calls(MODULE)


def test_the_layer_calls_no_io_and_no_clock() -> None:
    """调用名里没有 I/O、没有时钟，也没有动态执行。

    **没有时钟**是这一层与 ``preview_plan`` 不同款的地方：那一层连存储都不收，所以
    「不读时钟」顺理成章；这一层读三本账，于是 ``at`` 必须是外面给的一个**参数**
    （与 ``admission_refusal`` 同款）—— 一次读数内部有三个判定都用它，各读一次时钟
    会让这份读数自相矛盾，而报告上看不出来。
    """
    assert _name_calls(MODULE) & FORBIDDEN_BUILTIN_CALLS == set()
    assert _attribute_calls(MODULE) & FORBIDDEN_ATTRIBUTE_CALLS == set()


def test_the_scanner_reads_annotations_at_all() -> None:
    """正控：注解量具在一个**已知注解了端口**的模块上必须读到东西。

    对照组 ``billed_search.py`` 的 ``__init__`` 逐字写着 ``search: SearchProvider``
    —— 本层的注解里不该有它。
    """
    assert "SearchProvider" in _annotation_names(LINKED)
    assert "BudgetLedger" in _annotation_names(MODULE)


def test_the_layer_cannot_even_name_a_search_or_fetch_port() -> None:
    """**类型注解**里不得出现检索/抓取端口的类名。

    这一条比调用名那条更强，也是「零 provider 调用」的**构造**那一半：拿得到端口，
    调用它只是下一行的事；而拿不到它的层没有可以调用的东西。所以一条 ``search:
    SearchProvider`` 的形参——哪怕这一层今天一次都不调它——就已经把那条判据从结构
    降成了一句自觉的话。

    端口那两族的类名逐字取自 ``ports/search.py`` 与 ``ports/acquisition.py``。
    """
    found = _annotation_names(MODULE)
    assert found & FORBIDDEN_ANNOTATION_NAMES == set(), (
        f"注解里出现了端口：{sorted(found & FORBIDDEN_ANNOTATION_NAMES)}"
    )


def test_the_layer_catches_nothing() -> None:
    """这一层里**一个 ``except`` 都没有**。

    三条判据都是「返回字符串而不抛」的（``due_refusal`` / ``admission_refusal`` /
    ``window_refusal``），所以这一层没有异常需要接。一旦有人把其中一条包进
    ``try/except``，那份「两处读的是同一个函数」的承诺就再也没人核对得起来了。
    """
    tree = ast.parse(MODULE.read_text(encoding="utf-8"))
    assert [n for n in ast.walk(tree) if isinstance(n, ast.Try)] == []


def test_the_layer_takes_storage_and_already_read_values_only() -> None:
    """签名就是那几样，**全是 keyword-only**，且没有一个是必填之外的第六个入口。

    两件事在这里被看见：

    * **全 keyword-only**：参数里有好几组同类的一批（格子读数的三样输入、三个存储
      句柄），位置传参会让「第 7 个参数是什么」只能回去数一遍。
    * **没有可以塞端口进来的格子**：每一个注解都是本仓的域对象或本地存储端口，
      没有一个能收下一个客户端。
    """
    signature = inspect.signature(run_preview)
    assert list(signature.parameters) == [
        "store",
        "scene",
        "plan_report",
        "spec",
        "at",
        "observations",
        "evidence_store",
        "ledger",
        "signals",
        "subjects",
        "scene_store",
        "maintenance",
    ]
    for parameter in signature.parameters.values():
        assert parameter.kind is inspect.Parameter.KEYWORD_ONLY, parameter.name


def test_held_report_takes_exactly_a_plan_report_and_a_moment() -> None:
    """``held_report`` 只收两样，都是 keyword-only —— 它**一个字都不读**。

    这一格的签名本身是判据：一份 HOLD 的读数没有规格、没有「这一次能不能受理」、
    也没有「按这份计划要补哪些格子」，所以每一个存储句柄在这里都是**用不到**的。
    给它加一个 ``store=`` 的那天，这一层就多了一条「HOLD 也去读盘」的路 —— 而那条
    路上读出来的东西没有一格能落进产物里（四样同时缺席）。
    """
    signature = inspect.signature(held_report)
    assert list(signature.parameters) == ["plan_report", "at"]
    for parameter in signature.parameters.values():
        assert parameter.kind is inspect.Parameter.KEYWORD_ONLY


# --- 2 · §253 那五项怎么落，减法是哪三条 -------------------------------------


def test_the_five_items_are_the_plan_layers_five() -> None:
    """本层引用的就是计划层那一份**同一个对象**，不是重抄的五条。

    「同一句话的第二份实现」写不出来，是这一层不合并进 ``plan_preview`` 而改用减法
    的全部理由（模块 docstring）。这里判的是引用的**身份**：两份常量在同一进程里
    指向的是同一批 ``NotReported``。
    """
    assert NOT_REPORTED_ITEMS == (
        "已有事实复用多少",
        "预计补哪些缺口",
        "哪一条来源方法由哪个 provider 执行",
        "停在哪（stop 条件）",
        "把 hold 的那几条落成待办",
    )
    assert len(NOT_REPORTED_ITEMS) == 5


def test_the_subtraction_takes_exactly_three_and_they_are_named_in_the_constant() -> None:
    """减法滤掉的那三条与 ``_ANSWERED_WHEN_READY`` 是同一份名字，两条**对照**判据。

    两条对照缺一不可：

    * ``set(_HOLD_REASONS) == set(_ANSWERED_WHEN_READY)`` —— ``HOLD`` 时那三条换理由，
      键集必须逐字相同。漂开之后多出来的一条会被 ``KeyError`` 撞出来（好），**少掉
      的那一条会静默地用计划层那句「要读池子才知道」**（坏：``HOLD`` 下池子没读是
      因为计划没编出来，不是因为要读池子）。
    * 两张期望集之差**恰好**是那三条 —— 这一条把「减法」与「期望集」分开算，于是
      两边同时漂开（例如把某一条加进两边）会被抓住。
    """
    assert set(_HOLD_REASONS) == set(_ANSWERED_WHEN_READY)
    assert len(_ANSWERED_WHEN_READY) == 3
    assert _EXPECTED_WHEN_READY == frozenset(NOT_REPORTED_ITEMS) - set(_ANSWERED_WHEN_READY)
    assert _EXPECTED_WHEN_HOLD == frozenset(NOT_REPORTED_ITEMS)
    assert _EXPECTED_WHEN_HOLD - _EXPECTED_WHEN_READY == set(_ANSWERED_WHEN_READY)


def test_a_ready_report_drops_exactly_the_three_answered_items() -> None:
    """``READY`` 时 ``not_reported`` 恰好是那两条 —— 而且与计划层是**同一个对象**。

    计划层那五条逐条带它自己的理由；这一层答得出来的三条被滤掉，留下来的两条**逐字
    引用同一个 ``NotReported`` 实例**（``is``，不是 ``==``）。这一格判的是「不重
    写一个字」那句承诺：改成重抄之后两份会在某次改动后分家，而报告上它们必须说的是
    同一件事。
    """
    kept = tuple(item for item in NOT_REPORTED if item.item not in _ANSWERED_WHEN_READY)
    assert [item.item for item in kept] == [
        "哪一条来源方法由哪个 provider 执行",
        "把 hold 的那几条落成待办",
    ]
    by_name = {item.item: item for item in NOT_REPORTED}
    for item in kept:
        assert item is by_name[item.item]


def test_the_note_says_where_each_of_the_five_landed() -> None:
    """报告上那段话**逐条点名**五项各落在哪个键上。

    这一条挡的是一种很难看出来的报告：读者看到一个写着 ``outcome: ready`` 的对象与
    十几个键，然后**自己猜**哪一格答的是「预算/stop」。五项里有三项的落点不在同名键
    上（provider 在 ``plan['sources']``、歧义在 ``plan['held']`` / ``plan['recorded']``
    —— 那两处是计划层的形状，见 ``plan_preview``），所以那个猜法最好也是猜对两次。
    """
    for landing in (
        "already_reused",
        "gaps",
        "plan['sources']",
        "budget_and_stop",
        "plan['held']",
        "plan['recorded']",
        "not_reported",
        "counts",
        "inputs",
    ):
        assert landing in _NOTE, f"{landing!r} 没有被点名"
    assert "不是一次受理" in _NOTE


# --- 3 · HOLD ---------------------------------------------------------------


def test_a_hold_reports_all_five_and_reads_nothing(bundle: ProfileBundle) -> None:
    """计划没编出来时：五项全在，四样全缺席，``run_id`` 是 ``None``。

    ``held_report`` 是那一支的**唯一**实现，而 ``run_preview`` 在 ``HOLD`` 上委托
    给它 —— 于是「一份 HOLD 的读数长什么样」不会有两处说法。
    """
    report = held_report(plan_report=_held(bundle), at=T0)
    assert report.outcome is PreviewOutcome.HOLD
    assert report.run_id is None
    assert report.spec is None
    assert report.stop is None
    assert report.increment is None
    assert {item.item for item in report.not_reported} == _EXPECTED_WHEN_HOLD
    body = report.as_dict()
    assert body["budget_and_stop"] is None
    assert body["already_reused"] is None
    assert body["gaps"] is None
    assert body["counts"] is None
    assert body["inputs"] is None
    assert body["run_id"] is None


def test_a_hold_swaps_three_reasons_and_keeps_the_other_two(
    bundle: ProfileBundle,
) -> None:
    """那三条换上本次的理由，另外两条**仍是同一个对象**。

    换措辞的那两条（provider 映射缺失、``ReviewReason`` 缺一个成员）在两层都成立，
    重写一遍只会造出同一句话的第二份实现。
    """
    held = held_report(plan_report=_held(bundle), at=T0)
    by_name = {item.item: item for item in held.not_reported}
    for name in _ANSWERED_WHEN_READY:
        assert by_name[name].reason == _HOLD_REASONS[name]
    for item in NOT_REPORTED:
        if item.item not in _HOLD_REASONS:
            assert by_name[item.item] is item


def test_run_preview_hands_a_hold_to_held_report(
    bundle: ProfileBundle, tmp_path: Path
) -> None:
    """``HOLD`` 那一支**一个字都不读** —— 给它一个空存储也照样报得出来。

    这一条判的是「委托」而不是「另写一遍」：两边都用 ``held_report``，所以把
    ``run_preview`` 的 HOLD 分支改写成先读一遍存储的那天，这一格会红在「读了一个不
    该读的东西」上（这里给的存储里什么都没有，读出来的东西没有一格能落进产物）。
    """
    held = _held(bundle)
    report = run_preview(
        store=InMemoryRunStore(),
        scene=_scene(),
        plan_report=held,
        spec=None,
        at=T0,
    )
    assert report.outcome is PreviewOutcome.HOLD
    assert report.not_reported == held_report(plan_report=held, at=T0).not_reported


def test_a_ready_report_handed_to_held_report_is_refused(bundle: ProfileBundle) -> None:
    """传一份**编出来了**的 ``plan_report`` 会被 ``RunPreview`` 的守卫拒。

    挡住它的是**三**条守卫里最靠前的那一条（四样同时有或同时无）：``held_report``
    造的是「计划在、规格不在」的那种形状，而这一份**计划确实在**。三条守卫的次序
    写死了「先说话的是这一条」，所以这一格判的是那一次序，不是随便哪个 ``ValueError``。

    另外两条守卫各有它自己的用例：
    :func:`test_the_answered_names_must_match_the_outcome` 与
    :func:`test_due_is_asked_exactly_on_watch`。
    """
    _, ready = _ready(bundle)
    with pytest.raises(ValueError, match="同时有或同时无"):
        held_report(plan_report=ready, at=T0)


# --- 4 · StopVerdict 是五档闭集，判读次序就是受理面的次序 ---------------------


def test_the_verdicts_are_a_closed_set_of_five() -> None:
    """五档，且**只有**五档。多一档就是受理面上多一步没人告诉过调用方。

    ``str`` 混入是为了报告：``as_dict()`` 里写出来的是 ``"replay"``，不是要读的人
    去别处查表的数字。
    """
    assert {member.value for member in StopVerdict} == {
        "not_due",
        "request_refused",
        "replay",
        "chain_refused",
        "open",
    }
    assert set(StopVerdict) == {
        StopVerdict.NOT_DUE,
        StopVerdict.REQUEST_REFUSED,
        StopVerdict.REPLAY,
        StopVerdict.CHAIN_REFUSED,
        StopVerdict.OPEN,
    }


def test_not_due_speaks_before_the_request_itself_is_refused(
    bundle: ProfileBundle,
) -> None:
    """一份 watch 的读数**同时**不成立两件事时，报的是 ``NOT_DUE``。

    这不是优先级声明，是 ``admit_run`` 里那两步的**位置**：到期核验在 ``spec_of``
    之后、``store.admit`` 之前 —— 也就是在 ``admission_refusal`` 之前。所以这里刻意
    造一份**两样都不成立**的规格（watch 判不了到期，且截止时刻已经过去），判读的结
    果必须是更靠前的那一档。

    一个把 ``NOT_DUE`` 排在后面的枚举会把它读成 ``REQUEST_REFUSED``，而处置完全不同
    （去换一批主体 / 去挪截止）—— 这正是设计记录第三节决定 2 要防的那件事。
    """
    context, ready = _ready(bundle)
    spec = _spec(
        context,
        ready.plan,
        mode=ExecutionMode.WATCH,
        window=None,
        cutoff=PAST_CUTOFF,
        key=KEY,
    )
    # 两个存储都不给：``due_refusal`` 自己会说「判不了」（它**不**把判不了读成
    # 「没有到期」，也不读成「有到期」）。
    report = run_preview(
        store=InMemoryRunStore(),
        scene=_scene(),
        plan_report=ready,
        spec=spec,
        at=T0,
    )
    assert report.stop is not None
    assert report.stop.verdict is StopVerdict.NOT_DUE
    assert report.stop.due_refusal
    assert report.stop.chain is None and report.stop.spent is None
    # 对照：同一份规格在**没有第 1 步**时会被第 2 步拦下。
    bounded = _spec(context, ready.plan, key=KEY, cutoff=PAST_CUTOFF)
    other = run_preview(
        store=InMemoryRunStore(),
        scene=_scene(),
        plan_report=ready,
        spec=bounded,
        at=T0,
    )
    assert other.stop is not None
    assert other.stop.verdict is StopVerdict.REQUEST_REFUSED


# --- 5 · 受理面四步：端到端 --------------------------------------------------


def test_a_fresh_chain_reads_as_open(bundle: ProfileBundle) -> None:
    """一条空链：四步都没话说 ⇒ ``OPEN``，且链上那几格**真的读了**。

    ``spent`` 逐科目都在（账本上没记过的科目是 0）—— 一条「这一格没人填」与一条
    「这条线没花过」在报告上必须分得开。
    """
    context, ready = _ready(bundle)
    report = run_preview(
        store=InMemoryRunStore(),
        scene=_scene(),
        plan_report=ready,
        spec=_spec(context, ready.plan),
        at=T0,
        ledger=InMemoryBudgetLedger(),
    )
    assert report.outcome is PreviewOutcome.READY
    assert report.stop is not None
    assert report.stop.verdict is StopVerdict.OPEN
    assert report.stop.chain is not None
    assert report.stop.chain.objects == 0
    assert report.stop.chain.runs == 0
    assert report.stop.spent == tuple((line, 0) for line in BudgetLine)


def test_a_resend_after_the_chain_stopped_still_reads_as_a_replay(
    bundle: ProfileBundle,
) -> None:
    """**这一族最承重的一条**：链已经到 stop，重发同一个键照旧是 ``REPLAY``。

    受理面那条路径的承诺是「拿回**同一行**」，而它**不再问窗口**。把这两步反过来
    （先问窗口）在报告上只表现为「这次提交会被拒」—— 处置却是去挪窗口，而真正该做
    的事是去看那一行。

    对照组是**另一个键**：它在同一条已经到 stop 的链上，报 ``CHAIN_REFUSED``。两
    条判据在同一份链上跑，所以「报了什么」这件事的变量只有那个键。
    """
    context, ready = _ready(bundle)
    # ``max_objects=1``：一轮处置过一个对象之后，这条链就到了对象数上限。
    store, window = _chain(context, ready.plan, max_objects=1)
    replay = run_preview(
        store=store,
        scene=_scene(),
        plan_report=ready,
        spec=_spec(context, ready.plan, window=window, key=KEY),
        at=T0,
        ledger=InMemoryBudgetLedger(),
    )
    assert replay.stop is not None
    assert replay.stop.verdict is StopVerdict.REPLAY
    assert replay.stop.existing is not None
    assert replay.stop.chain is None, "重放这一条路上根本没有人问窗口"
    assert replay.stop.chain_refusal is None
    assert replay.stop.spent is None

    fresh = run_preview(
        store=store,
        scene=_scene(),
        plan_report=ready,
        spec=_spec(context, ready.plan, window=window, key=KEY2),
        at=T0,
        ledger=InMemoryBudgetLedger(),
    )
    assert fresh.stop is not None
    assert fresh.stop.verdict is StopVerdict.CHAIN_REFUSED
    assert fresh.stop.existing is None
    assert fresh.stop.chain is not None
    assert fresh.stop.chain.objects == 1
    assert fresh.stop.chain_refusal


def test_the_ledger_being_unwired_is_refused_and_not_read_as_room_left(
    bundle: ProfileBundle,
) -> None:
    """``spent is None`` ⇒ **拒**，而不是「这一格跳过」（``window_refusal`` 第四条）。

    这一层是那个 ``None`` 今天**唯一**的来源（非自动模式或账本没接上）。两条读数在
    同一份链上跑，唯一的差别是账本接不接得上：

    * 接上（空账本）：这条链一轮走完、没有预留 ⇒ 累计全零 ⇒ ``OPEN``；
    * 没接上：累计**算不出来** ⇒ 拒。

    读成 0 的后果是「一次部署漏接」与「一条还有满额度的链」在报告上同形，而处置相反
    （去接账本 / 可以继续走）。
    """
    context, ready = _ready(bundle)
    store, window = _chain(context, ready.plan, max_objects=50)
    spec = _spec(context, ready.plan, window=window, key=KEY2)

    wired = run_preview(
        store=store,
        scene=_scene(),
        plan_report=ready,
        spec=spec,
        at=T0,
        ledger=InMemoryBudgetLedger(),
    )
    assert wired.stop is not None
    assert wired.stop.verdict is StopVerdict.OPEN
    assert wired.stop.spent == tuple((line, 0) for line in BudgetLine)

    unwired = run_preview(
        store=store, scene=_scene(), plan_report=ready, spec=spec, at=T0, ledger=None
    )
    assert unwired.stop is not None
    assert unwired.stop.verdict is StopVerdict.CHAIN_REFUSED
    assert unwired.stop.spent is None, "缺席要说得出是缺席，不是 0"
    assert unwired.stop.chain is not None


def test_a_reservation_the_ledger_cannot_find_is_an_incident_not_a_zero(
    bundle: ProfileBundle,
) -> None:
    """链上记着一笔账本读不到的预留 ⇒ **抛**，不返回 0（``spent_of``）。

    两件事各自都读得出来、只是读出来的不是同一件事（读错了账本 / 那一行丢了）。
    按 0 继续算的后果是这条链被多放行一轮，而报告上它读起来只是「预算还够」。
    """
    context, ready = _ready(bundle)
    store, window = _chain(
        context, ready.plan, max_objects=50, usage=("0" * 32,)
    )
    with pytest.raises(RunReconciliationError):
        run_preview(
            store=store,
            scene=_scene(),
            plan_report=ready,
            spec=_spec(context, ready.plan, window=window, key=KEY2),
            at=T0,
            ledger=InMemoryBudgetLedger(),
        )


def test_the_chain_ceiling_is_read_from_the_ledger_not_from_the_delta(
    bundle: ProfileBundle,
) -> None:
    """链的累计预算是**从账本读回来的**，一算出来就越过上限 ⇒ ``CHAIN_REFUSED``。

    金额只有一个权威（账本），``RunDelta.usage`` 里记的是预留 id。这一格走完整条
    路：预留写进账本、id 记进 Delta、这一层拿 id 读回金额、``window_refusal`` 拿它
    与窗口的上限比。
    """
    context, ready = _ready(bundle)
    ledger = InMemoryBudgetLedger()
    ledger.set_ceiling(WORKSPACE, BudgetLine.SEARCH, 100, source="测试")
    reservation = ledger.reserve(
        WORKSPACE, BudgetLine.SEARCH, 90, idempotency_key="spend-1"
    )
    store, window = _chain(
        context,
        ready.plan,
        max_objects=50,
        usage=(reservation.reservation_id,),
        window=_window(ceiling=90),
    )
    report = run_preview(
        store=store,
        scene=_scene(),
        plan_report=ready,
        spec=_spec(context, ready.plan, window=window, key=KEY2),
        at=T0,
        ledger=ledger,
    )
    assert report.stop is not None
    assert report.stop.verdict is StopVerdict.CHAIN_REFUSED
    assert report.stop.spent == tuple(
        (line, 90 if line is BudgetLine.SEARCH else 0) for line in BudgetLine
    )


# --- 6 · StopReadout 的不变量：只有直接构造才违反得成 ------------------------


#: 参数化那一条里的哨兵。``pytest.param`` 的实参在**导入期**求值，而 ``existing``
#: 那一格要的是一个真的 ``Run``（得先有一个存储）—— 所以表里放哨兵，用例体内换成
#: :func:`_stopped_run`。
_STOPPED = "__stopped-run__"


def _readout(**overrides: Any) -> StopReadout:
    """一份**四步都走了、都没说话**的读数（``OPEN`` 那一档）。"""
    window = _window()
    chain = ChainProgress(
        window_digest=window.digest,
        runs=1,
        open_runs=0,
        objects=0,
        reservations=(),
    )
    payload: dict[str, Any] = {
        "budget": (RunBudgetLine(line=BudgetLine.SEARCH, ceiling=20),),
        "cutoff": CUTOFF,
        "window": window,
        "due_refusal": None,
        "request_refusal": None,
        "existing": None,
        "spent": tuple((line, 0) for line in BudgetLine),
        "chain": chain,
        "chain_refusal": None,
    }
    payload.update(overrides)
    return StopReadout(**payload)


def test_the_plain_readout_is_writable_at_all() -> None:
    """正控：下面那八条判据判的都是**例外**，所以基线本身必须构造得出来。

    没有这一条，一个恒抛的 ``__post_init__`` 会让那八条全绿 —— 而那时这一族验的是
    它自己坏掉了。
    """
    assert _readout().verdict is StopVerdict.OPEN


@pytest.mark.parametrize(
    "overrides",
    [
        # 次序组（三条）：前面一步说了话 ⇒ 后面的读数没问。
        pytest.param(
            {"due_refusal": "这一批主体上没有到期", "request_refusal": "截止已过"},
            id="第 1 步说了话，第 2 步的读数还在",
        ),
        pytest.param(
            {"request_refusal": "截止已过", "chain_refusal": "对象数到顶"},
            id="第 2 步说了话，链的读数还在",
        ),
        pytest.param(
            {"existing": _STOPPED},
            id="重放命中，而链的读数还在",
        ),
        # 主语组（四条）+ 「缺席要说得出来」（一条）。
        pytest.param(
            {"window": None, "chain_refusal": "对象数到顶"},
            id="没有窗口，却有链的读数",
        ),
        pytest.param(
            {"chain": None, "chain_refusal": "对象数到顶"},
            id="链的拒词在，链的读数不在",
        ),
        pytest.param({"chain": None}, id="累计花费在，链的读数不在"),
        pytest.param(
            {
                "chain": ChainProgress(
                    window_digest="f" * 64,
                    runs=0,
                    open_runs=0,
                    objects=0,
                    reservations=(),
                )
            },
            id="链上那份累计是另一条链的",
        ),
        pytest.param(
            {"chain": None, "spent": None},
            id="窗口在场、一步都没说话、链一格都没读",
        ),
    ],
)
def test_the_readout_refuses_a_state_that_cannot_be_read(
    overrides: dict[str, Any],
) -> None:
    """八条不变量逐条构造一遍 —— 每一条都要抛 ``ValueError``。

    一个没人能违反的守卫与一个从不响的守卫，在报告上长得一样。这里逐条把**它挡的那
    件事**造出来，于是「守卫在判事」是被实测过的。

    三条最要紧的：

    * **重放命中 ⇒ 链的读数缺席**：受理面那条路径不再问窗口，在这里补一份链的读数会
      让一次重发在报告上看起来像一次新的受理。
    * **没有窗口 ⇒ 链的读数缺席**：「这条链累计走到哪了」对一条没有窗口的规格没有
      主语，读到了一份累计说明拿的是**另一条链**的读数。
    * **窗口在场、前四步都没说话、链一格都没读 ⇒ 抛**：这一份读数会在报告上长成
      ``OPEN``，而 ``OPEN`` 是一句「受理得下来」的话。
    """
    if overrides.get("existing") == _STOPPED:
        overrides = {**overrides, "existing": _stopped_run()}
    with pytest.raises(ValueError):
        _readout(**overrides)


def _stopped_run() -> Any:
    """一个真的在盘上结束过的 ``Run`` —— ``existing`` 那一格收的是**那一行**。

    规格在这里**直接构造**，不走 ``spec_of``：那个函数要一份编出来的计划，而
    「重放命中的那一行」与这一层对着哪份计划无关。手造一个 ``RunSpec`` 是在测试里
    做的（``tests/test_run_window_admission.py::_spec`` 同款），不是在生产代码里。
    """
    store = InMemoryRunStore()
    spec = RunSpec(
        workspace=WORKSPACE,
        objective="补一轮 Devon 的场地",
        selector=SCENE_ID,
        context_ref="a" * 64,
        plan_ref="b" * 64,
        execution_mode=ExecutionMode.BOUNDED_AUTO,
        window=_window(),
        budget=(RunBudgetLine(line=BudgetLine.SEARCH, ceiling=20),),
        cutoff=CUTOFF,
        policy_versions=("grading@1f2e3d",),
        idempotency_key=KEY,
    )
    # ``spent`` 必须给：带窗口的规格上「读不到账本」**是拒**（``window_refusal``
    # 的第四条），而这里要造的是「受理得下来、然后结束了」那一行。
    run = store.admit(
        spec, spec_sha256=spec_fingerprint(spec), at=T0, spent={BudgetLine.SEARCH: 0}
    )
    return store.stop(
        run.run_id, delta=_delta(), status=RunStatus.COMPLETED, at=STOPPED_AT
    )


def test_each_step_alone_reads_as_its_own_verdict() -> None:
    """每一档都能单独构造出来，且 ``verdict`` 取的是**最靠前**的那一格。

    ``due_refusal`` 为空串是一句**有依据的放行**（第 1 步问了、答案是走得通），所以
    它不改变判读 —— 这与 ``None``（没问）在报告上必须分得开，而两者的 ``verdict``
    都是往下走。
    """
    assert _readout(due_refusal="").verdict is StopVerdict.OPEN
    assert (
        _readout(
            due_refusal="这一批主体上没有到期", chain=None, spent=None
        ).verdict
        is StopVerdict.NOT_DUE
    )
    assert (
        _readout(request_refusal="截止已过", chain=None, spent=None).verdict
        is StopVerdict.REQUEST_REFUSED
    )
    assert (
        _readout(existing=_stopped_run(), chain=None, spent=None).verdict
        is StopVerdict.REPLAY
    )
    assert (
        _readout(chain_refusal="对象数到顶").verdict is StopVerdict.CHAIN_REFUSED
    )
    assert _readout().verdict is StopVerdict.OPEN


def test_the_readout_keeps_all_three_refusals_verbatim() -> None:
    """三条拒词**原样**写出来（``None`` 就是 ``None``）。

    它们是受理面那两条判据给的句子，这一层一个字都不改写 —— 改写的那天，同一个
    「这一次走不通」在预览与受理上会有两种说法，而调用方要照着其中一句去改东西。
    """
    body = _readout(chain_refusal="对象数到顶").as_dict()
    assert body["chain_refusal"] == "对象数到顶"
    assert body["request_refusal"] is None
    assert body["due_refusal"] is None
    assert body["verdict"] == "chain_refused"
    assert body["this_round"]["cutoff"] == CUTOFF.isoformat()
    assert body["chain"] is not None
    assert body["chain"]["digest"] == _window().digest
    assert body["progress"] is not None
    assert body["spent"] == {line.value: 0 for line in BudgetLine}


# --- 7 · RunPreview 的守卫 ---------------------------------------------------


def _preview_for_guards(bundle: ProfileBundle, **context_overrides: Any):
    """一份 ``READY`` 的 ``RunPreview`` —— 三条守卫的**基线**。"""
    context, ready = _ready(bundle, **context_overrides)
    spec = _spec(context, ready.plan)
    return RunPreview(
        at=T0,
        plan=ready,
        spec=spec,
        stop=_readout(),
        increment=IncrementReading(cells=(), observations=0, signals=0, subjects=0),
        not_reported=_not_reported_for_guard(ready=True),
    )


def _not_reported_for_guard(*, ready: bool) -> tuple[NotReported, ...]:
    """两份期望集各造一份 ``not_reported``（守卫的输入，不是它的判据）。"""
    wanted = _EXPECTED_WHEN_READY if ready else _EXPECTED_WHEN_HOLD
    return tuple(item for item in NOT_REPORTED if item.item in wanted)


def test_the_guard_baseline_is_writable_at_all(bundle: ProfileBundle) -> None:
    """正控：下面三条守卫判的都是**例外**。"""
    report = _preview_for_guards(bundle)
    assert report.outcome is PreviewOutcome.READY
    assert report.run_id == run_id(WORKSPACE, KEY)


def test_a_naive_moment_is_refused(bundle: ProfileBundle) -> None:
    """``at`` 带不带时区，必须被拒 —— 它去比的是「截止时刻在不在将来」。

    省掉时区的时刻会被按读它那台机器的本地时区解释，于是「这一轮能不能受理」取决于
    这份读数在哪里跑。
    """
    report = _preview_for_guards(bundle)
    with pytest.raises(ValueError, match="naive"):
        RunPreview(
            at=datetime(2026, 9, 28, 9, 0),
            plan=report.plan,
            spec=report.spec,
            stop=report.stop,
            increment=report.increment,
            not_reported=report.not_reported,
        )


@pytest.mark.parametrize("dropped", ["spec", "stop", "increment"])
def test_the_four_are_paired(bundle: ProfileBundle, dropped: str) -> None:
    """计划编出来了、有规格、有 stop 读数、有格子读数 —— 四样同时有或同时无。

    一份「计划编出来了却没有规格」的读数会让读者以为受理那一步已经被算过；而「没有
    规格却有格子读数」更坏 —— 格子读数是对着**那份计划**读的。
    """
    report = _preview_for_guards(bundle)
    payload: dict[str, Any] = {
        "at": T0,
        "plan": report.plan,
        "spec": report.spec,
        "stop": report.stop,
        "increment": report.increment,
        "not_reported": report.not_reported,
    }
    payload[dropped] = None
    with pytest.raises(ValueError, match="同时有或同时无"):
        RunPreview(**payload)


def test_the_answered_names_must_match_the_outcome(bundle: ProfileBundle) -> None:
    """名字集合**恰好**是常量给出的那一份 —— 静默滤掉一条会在这里红。

    那种报告看起来只是短了一条，而它少掉的正是「这一层答不上来」的那句交代。
    """
    report = _preview_for_guards(bundle)
    with pytest.raises(ValueError, match="对不上"):
        RunPreview(
            at=T0,
            plan=report.plan,
            spec=report.spec,
            stop=report.stop,
            increment=report.increment,
            not_reported=_not_reported_for_guard(ready=False),
        )


def test_due_is_asked_exactly_on_watch(bundle: ProfileBundle) -> None:
    """``due_refusal is not None`` ⟺ 模式是 ``watch``，两格必须一起动。

    一步没被问与一个模式不问它是同一件事。只改一处的哪天，一份 ``manual`` 的读数会
    带着一句「这一批主体上没有到期」—— 而那句话对它没有主语。
    """
    report = _preview_for_guards(bundle)
    assert report.spec is not None
    assert report.spec.execution_mode is ExecutionMode.BOUNDED_AUTO
    with pytest.raises(ValueError, match="到期核验"):
        RunPreview(
            at=T0,
            plan=report.plan,
            spec=report.spec,
            stop=_readout(
                due_refusal="这一批主体上没有到期", chain=None, spent=None
            ),
            increment=report.increment,
            not_reported=report.not_reported,
        )


def test_watch_reads_its_own_mode(bundle: ProfileBundle) -> None:
    """对照：同一份读数配一份 ``watch`` 的规格就成立 —— 上面那条判的是配对，不是
    「``due_refusal`` 不得有值」。"""
    context, ready = _ready(bundle)
    spec = _spec(context, ready.plan, mode=ExecutionMode.WATCH, window=None, key=KEY)
    report = RunPreview(
        at=T0,
        plan=ready,
        spec=spec,
        stop=_readout(
            window=None,
            due_refusal="这一批主体上没有到期",
            chain=None,
            spent=None,
        ),
        increment=IncrementReading(cells=(), observations=0, signals=0, subjects=0),
        not_reported=_not_reported_for_guard(ready=True),
    )
    assert report.stop is not None
    assert report.stop.verdict is StopVerdict.NOT_DUE
    assert report.run_id == run_id(WORKSPACE, KEY)


# --- 8 · 调用点接错线的那两条守卫 --------------------------------------------


def test_a_spec_pointing_at_another_scene_is_refused(bundle: ProfileBundle) -> None:
    """``spec.selector`` 与 ``scene.scene_id`` 不等 ⇒ 抛，而不是报一份自洽的错读数。

    规格里的工作区与对象范围**取自**那一版场景，而格子读数与到期核验都读这一版 ——
    两者对不上时报告上是一份自洽的错读数：它报的每一格都属于另一个场景，而没有一个
    键看得出来。
    """
    context, ready = _ready(bundle)
    elsewhere = _scene().model_copy(update={"scene_id": "scene-somewhere-else"})
    with pytest.raises(ValueError, match="场景"):
        run_preview(
            store=InMemoryRunStore(),
            scene=elsewhere,
            plan_report=ready,
            spec=_spec(context, ready.plan),
            at=T0,
        )


def test_a_compiled_plan_without_a_spec_is_refused(bundle: ProfileBundle) -> None:
    """计划编出来了而规格不在 ⇒ 抛。

    受理面是拿同一份计划现造规格的（``spec_of``），所以这一格的缺席只可能来自一个
    接错线的调用点 —— 而一份没有规格的读数会在报告上长成一个「什么都对」的形状。
    """
    _, ready = _ready(bundle)
    with pytest.raises(ValueError, match="规格"):
        run_preview(
            store=InMemoryRunStore(),
            scene=_scene(),
            plan_report=ready,
            spec=None,
            at=T0,
        )


# --- 9 · 格子读数 ------------------------------------------------------------


def _reading(*cells: IncrementCell, **counts: int) -> IncrementReading:
    return IncrementReading(
        cells=cells,
        observations=counts.get("observations", 0),
        signals=counts.get("signals", 0),
        subjects=counts.get("subjects", 0),
    )


def _cell(
    verdict: IncrementVerdict,
    *,
    subject: str = "acct-1",
    aspect: Aspect = Aspect.SOURCE_COVERAGE,
) -> IncrementCell:
    return IncrementCell(
        subject_kind="account",
        subject_id=subject,
        aspect=aspect,
        verdict=verdict,
        reason=f"这一格的处置是 {verdict.value}",
    )


def test_the_counts_are_a_closed_set_of_four_and_zero_shows_up() -> None:
    """四档**都在**，缺的那一档是 0 —— 「这一档是零」与「这一档没人填」不同形。"""
    reading = _reading(_cell(IncrementVerdict.NEW))
    assert set(reading.counts) == set(IncrementVerdict)
    assert reading.counts[IncrementVerdict.NEW] == 1
    assert reading.counts[IncrementVerdict.REUSE] == 0


def test_reuse_and_work_are_one_partition() -> None:
    """``reuse`` 与 ``work`` 是一份划分：不重不漏，且与共用函数报的是同一个数。

    「复用不是一件事：它是『什么都不做』」—— 判定住在 ``works_of`` 里，两个面
    （``RunIncrement`` 与这一份 ``IncrementReading``）调的是同一份，所以两个面报的
    复用数不可能是两个数。
    """
    cells = (
        _cell(IncrementVerdict.REUSE),
        _cell(IncrementVerdict.NEW, aspect=Aspect.ASSET_IDENTITY),
        _cell(IncrementVerdict.MISSING, aspect=Aspect.ASSET_ATTRIBUTE),
        _cell(IncrementVerdict.EXPIRED, aspect=Aspect.ACCOUNT_RELATION),
    )
    reading = _reading(*cells)
    assert reading.reuse + reading.work == cells
    assert set(reading.reuse) & set(reading.work) == set()
    assert reading.work == works_of(cells)
    assert reading.counts == counts_of(cells)
    assert len(reading.reuse) + len(reading.work) == len(cells)


def test_a_negative_input_size_is_refused() -> None:
    """三条计数各是一个条数，不得为负。

    一份负的输入规模会让「喂进去的是零条」与「喂进去的没数清」在报告上同形 —— 而
    前者的处置是去沉淀事实，后者是去查这一处读数。
    """
    for field in ("observations", "signals", "subjects"):
        with pytest.raises(ValueError, match="不得为负"):
            _reading(**{field: -1})


def test_two_cells_for_one_subject_and_aspect_are_refused() -> None:
    """同一格有两个结论 ⇒ 抛（``check_partition``）：

    重复的格子会让 ``counts`` 把那一格数两遍，于是「这一轮要补几格」在同一份读数里
    有两个答案。
    """
    one = _cell(IncrementVerdict.NEW)
    with pytest.raises(ValueError):
        _reading(one, one)


def test_a_reading_with_no_cells_is_an_honest_zero() -> None:
    """零格 + 零观测是一条**真**读数，不是「这一轮没有事要做」。

    它说的是「这里还没有任何沉淀下来的事实可复用」—— 处置是去沉淀事实。报告上它
    与「读过了、这一档是零」必须分得开：前者整块是 ``None``（``HOLD``），后者是
    ``counts`` 里四个零。
    """
    reading = _reading()
    assert reading.cells == ()
    assert reading.counts == dict.fromkeys(IncrementVerdict, 0)


def test_a_ready_report_counts_every_verdict(bundle: ProfileBundle) -> None:
    """端到端：``READY`` 时 ``counts`` 四档都在，且与 ``inputs`` 一起读。

    零格配零观测是「还没有事实可复用」，**不是**「这一轮没有事要做」—— 报告上这两件
    事必须分得开，而 ``inputs`` 就是那一格的依据。
    """
    context, ready = _ready(bundle)
    report = run_preview(
        store=InMemoryRunStore(),
        scene=_scene(),
        plan_report=ready,
        spec=_spec(context, ready.plan),
        at=T0,
    )
    body = report.as_dict()
    assert body["counts"] is not None
    assert set(body["counts"]) == {member.value for member in IncrementVerdict}
    assert body["inputs"] == {"observations": 0, "signals": 0, "subjects": 0}
    # ``READY`` 这一侧的名字集合也在**真的报告**上判一次。``HOLD`` 那一侧有这一句
    # （``test_a_hold_reports_all_five_and_reads_nothing``），这一侧原先只在常量表上
    # 判：``test_a_ready_report_drops_exactly_the_three_answered_items`` 自己把过滤器
    # 重算了一遍，**不经过** ``_not_reported``，所以那一格对「少减一条」是瞎的 ——
    # 变异实测：把 ``_ANSWERED_WHEN_READY`` 切成 ``[:2]`` 之后它照旧绿。这里造的是真
    # 报告，于是那条减法与 ``RunPreview`` 的名字集合守卫都有了一个执行器。
    assert {item.item for item in report.not_reported} == _EXPECTED_WHEN_READY


def test_a_fresh_subject_that_was_just_observed_is_reused_not_new(
    bundle: ProfileBundle,
) -> None:
    """喂进一批上一轮刚看过的观测 ⇒ 那些格子落在 ``REUSE``。

    这一条判的是**这一层没有第二处判定**：它读的是 ``aspect_states``，与
    ``plan_increment`` 那一份是同一个函数。两处各判一遍的那天，同一批观测会在两个面
    上报出不同的复用数，而报告上两边看起来都正常。
    """
    context, ready = _ready(bundle)
    report = run_preview(
        store=InMemoryRunStore(),
        scene=_scene(),
        plan_report=ready,
        spec=_spec(context, ready.plan),
        at=T0,
        observations=_all_aspects(),
    )
    assert report.increment is not None
    assert report.increment.observations == len(ALL_ASPECTS)
    assert report.increment.reuse, "上一轮刚看过的格子应当有可复用的东西"
    assert report.increment.work == works_of(report.increment.cells)
    assert report.increment.counts == counts_of(report.increment.cells)


def test_an_expired_subject_is_work_not_reuse(bundle: ProfileBundle) -> None:
    """超过最长窗口的那一批是 ``EXPIRED`` ——「复用」与「要重取」必须分得开。"""
    context, ready = _ready(bundle)
    report = run_preview(
        store=InMemoryRunStore(),
        scene=_scene(),
        plan_report=ready,
        spec=_spec(context, ready.plan),
        at=T0,
        observations=_all_aspects(at=STALE),
    )
    assert report.increment is not None
    assert report.increment.reuse == ()
    assert {cell.verdict for cell in report.increment.work} == {
        IncrementVerdict.EXPIRED
    }


def test_the_cells_are_still_computed_when_the_submission_would_be_refused(
    bundle: ProfileBundle,
) -> None:
    """这一次提交走不通时，格子读数**照算** —— 那正是它有用的时候。

    一份走不通的提交里「池子里已经有什么」仍是一句读得出来的话，而它读的是本地存储，
    一次 provider 调用都不花。
    """
    context, ready = _ready(bundle)
    store, window = _chain(context, ready.plan, max_objects=1)
    report = run_preview(
        store=store,
        scene=_scene(),
        plan_report=ready,
        spec=_spec(context, ready.plan, window=window, key=KEY2),
        at=T0,
        ledger=InMemoryBudgetLedger(),
    )
    assert report.stop is not None and report.stop.verdict is StopVerdict.CHAIN_REFUSED
    assert report.increment is not None
    assert report.as_dict()["counts"] is not None


# --- 10 · 报告的形状 --------------------------------------------------------


def test_the_report_is_json_serializable(bundle: ProfileBundle) -> None:
    """报告要能 ``json.dumps`` —— 它是 MCP 只读面交出去的那一份。"""
    context, ready = _ready(bundle)
    report = run_preview(
        store=InMemoryRunStore(),
        scene=_scene(),
        plan_report=ready,
        spec=_spec(context, ready.plan),
        at=T0,
        ledger=InMemoryBudgetLedger(),
    )
    body = json.loads(json.dumps(report.as_dict()))
    assert body["outcome"] == "ready"
    assert body["run_id"] == run_id(WORKSPACE, KEY)
    assert body["at"] == T0.isoformat()
    assert body["plan"]["not_reported"], "计划层那一份在这里整块带着"


def test_the_run_id_is_the_key_that_would_be_written(bundle: ProfileBundle) -> None:
    """``run_id`` 是内容寻址的那一个键，与 ``domain.run.run_id`` 逐字同款。

    这一格是**读数**不是判断：报告里报出它，是为了让读的人自己拿它去问「这个键此刻
    在不在盘上」—— 而那一问的答案在 ``budget_and_stop.existing``。
    """
    context, ready = _ready(bundle)
    spec = _spec(context, ready.plan)
    report = run_preview(
        store=InMemoryRunStore(),
        scene=_scene(),
        plan_report=ready,
        spec=spec,
        at=T0,
    )
    assert report.run_id == run_id(spec.workspace, spec.idempotency_key)
    assert report.as_dict()["run_id"] == report.run_id


def test_the_budget_cells_are_all_there_including_zero(bundle: ProfileBundle) -> None:
    """信封逐格都在，**包括额度为 0 的那一格**（0 与缺席不同）。

    ``RunSpec.budget`` 与 ``AuthorizationWindow.budget`` 都不得为空，所以「没声明」
    在这两处不是一种状态 —— 一份少了一格的信封会让读者以为那条线不受限。
    """
    context, ready = _ready(bundle)
    budget = tuple(
        RunBudgetLine(line=line, ceiling=7 if line is BudgetLine.SEARCH else 0)
        for line in BudgetLine
    )
    report = run_preview(
        store=InMemoryRunStore(),
        scene=_scene(),
        plan_report=ready,
        spec=_spec(
            context, ready.plan, budget=budget, window=_window(every_line=True)
        ),
        at=T0,
        ledger=InMemoryBudgetLedger(),
    )
    assert report.stop is not None
    lines = report.stop.as_dict()["this_round"]["budget"]
    assert [row["line"] for row in lines] == [line.value for line in BudgetLine]
    assert {row["line"]: row["ceiling"] for row in lines} == {
        line.value: (7 if line is BudgetLine.SEARCH else 0) for line in BudgetLine
    }
