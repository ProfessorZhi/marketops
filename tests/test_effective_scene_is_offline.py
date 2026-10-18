"""``application/effective_scene.py`` 的**构造成判据** —— 编译那一族零外部调用。

任务书把这一条写成一句话：「``preview_effective_scene`` / ``get_effective_scene``
是**读**（不写一行、不发一次检索）—— 而判据要判**构造**，不能判自觉」，并且点名
照 ``preview_launch`` 那一族与 ``tests/test_launch_preview_is_offline.py`` 的样子写。

## 「零 provider」为什么必须量在这里

一个恒真的合取项读不出因果关系。「编译这一族够不到检索与抓取」如果只是一句承诺，
那么它成立与否由**接线那一天**决定，而那一天没有人会去问这句话。所以量的是三件
结构事实，每一件都配一个**正控**：

1. **import 闭包精确相等**（白名单，不是黑名单）。多一个 import 就是多一条通向
   外部世界的路 —— 而这一条比"知道某个坏模块的名字"强：``socket`` / ``httpx`` /
   还没被写出来的那一个都在白名单之外。
2. **调用名**里没有 I/O、没有时钟、没有动态执行。**没有时钟**尤其：``at`` 是外面
   给的一个**参数**，一次编译里有好几处判定都用它 —— 各读一次时钟会让同一份编译
   结果自相矛盾，而报告上看不出来。
3. **注解里没有端口类名**。这一条比调用名强，也是「够不到」那一半：拿得到端口，
   调用它只是下一行的事；而拿不到它的层**没有可以调用的东西**。
   ``compile_effective_scene`` 更强一层 —— 它的签名里**一个端口都没有**。

## 冻结那一格为什么也在这一族里

``freeze_effective_scene`` 是**写**（编译 + ``put_scene``）。它在这一族里而不是在
启动那一族里，因为"编译"这件事只有一处定义；而**这一条判据对它没有放松**：
它拿到的仍然只有 ``ProductCatalog``（一个读端口）与 ``EffectiveSceneStore``
（一个存储），**没有**检索与抓取。判的是**能力**，而这一族今天没有那个能力。

## 正控是这一份文件的一半

每一把量具都配一条 ``test_the_probe_can_see_*`` / ``test_the_instrument_reads_*``：
把一段**故意违规**的源码（或一个已知违规的真模块）喂给同一把量具，它必须报出来。
没有它们，一个"什么都没扫到"的量具与一个真的量具在报告上同形 —— 而那正是
``tests/test_launch_preview_is_offline.py`` 那一族写下的理由。

三张表（import 白名单、禁用调用名、禁用注解名）与那份文件**逐字相同**，所以两族
的判据是同一把尺子量的；本文件多出的两格是本族独有的：``_imported`` 读**相对
import**（``from ..ports.search import ...`` 是绕开白名单最省事的一种写法），
以及签名判据（``compile_effective_scene`` 的九个入参全是已读出来的值，一个端口
都不收 —— 这一族里最强的一条）。
"""

from __future__ import annotations

import ast
import inspect
import textwrap
from pathlib import Path
from typing import get_type_hints

import pytest

from marketops.application.effective_scene import (
    compile_effective_scene,
    freeze_effective_scene,
    preview_effective_scene,
    read_effective_scene,
)
from marketops.config import REPO_ROOT
from marketops.ports.product_catalog import ProductCatalog
from marketops.ports.workstreams import EffectiveSceneStore

MODULE = REPO_ROOT / "marketops" / "application" / "effective_scene.py"

#: 两个**已知会红**的对照：一个 import 了检索端口，一个 import 了抓取端口。
LINKED = REPO_ROOT / "marketops" / "application" / "billed_search.py"
FETCHING = REPO_ROOT / "marketops" / "application" / "acquisition.py"

#: 这一族的 import 白名单，**精确相等**。
#:
#: 四样是这个模块**真的**需要的：``domain`` 那几个值对象、``ports.product_catalog``
#: （产品与方案是**声明**，不是检索结果 —— 一个读端口）、``ports.workstreams``
#: （``EffectiveSceneStore``：冻结落库与按主键读）。
#:
#: ``marketops.adapters.*`` **一个都不在**，而这不是巧合：``application/`` 不许
#: import ``adapters/``（分层纪律）。这一条判据因此同时钉住两件事 —— 够不到检索，
#: 也够不到任何具体实现。
ALLOWED_IMPORTS = frozenset(
    {
        "__future__",
        "datetime",
        "marketops.domain.errors",
        "marketops.domain.product",
        "marketops.domain.scene",
        "marketops.domain.util",
        "marketops.domain.workstream",
        "marketops.ports.product_catalog",
        "marketops.ports.workstreams",
    }
)

#: 与 ``tests/test_launch_preview_is_offline.py`` / ``test_run_preview.py``
#: **逐字相同**的三张表（那份文件写了每一格的由来）。
FORBIDDEN_BUILTIN_CALLS = frozenset({"open", "eval", "exec", "compile", "__import__"})

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

#: **注解里**不得出现的名字，取自 ``ports/search.py`` 与 ``ports/acquisition.py``。
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


# --- 量具（与 ``test_launch_preview_is_offline.py`` 同形，**按源码**而不是按文件） ---


def _parse(source: str) -> ast.Module:
    return ast.parse(textwrap.dedent(source))


def _imported(path: Path) -> frozenset[str]:
    """一个模块 import 了哪些**绝对模块名**（相对 import 按包名还原）。"""
    return _imported_source(
        path.read_text(encoding="utf-8"),
        package=".".join(path.relative_to(REPO_ROOT).with_suffix("").parts[:-1]),
    )


def _imported_source(source: str, *, package: str = "") -> frozenset[str]:
    found: set[str] = set()
    for node in ast.walk(_parse(source)):
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


def _name_calls_from(source: str) -> frozenset[str]:
    found: set[str] = set()
    for node in ast.walk(_parse(source)):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            found.add(node.func.id)
    return frozenset(found)


def _attribute_calls_from(source: str) -> frozenset[str]:
    found: set[str] = set()
    for node in ast.walk(_parse(source)):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            found.add(node.func.attr)
    return frozenset(found)


def _annotation_names_from(source: str) -> frozenset[str]:
    """**类型注解里**出现过的名字（``ast.Name`` 与 ``ast.Attribute`` 两条都抓 ——
    给端口加一层模块前缀不能绕过去）。"""
    found: set[str] = set()
    for node in ast.walk(_parse(source)):
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


def _name_calls(path: Path) -> frozenset[str]:
    return _name_calls_from(path.read_text(encoding="utf-8"))


def _attribute_calls(path: Path) -> frozenset[str]:
    return _attribute_calls_from(path.read_text(encoding="utf-8"))


def _annotation_names(path: Path) -> frozenset[str]:
    return _annotation_names_from(path.read_text(encoding="utf-8"))


def _tries(path: Path) -> list[ast.Try]:
    return [node for node in ast.walk(_parse(path.read_text(encoding="utf-8"))) if isinstance(node, ast.Try)]


# --- 1 · import 闭包 ------------------------------------------------------------


def test_the_scanner_reads_imports_at_all() -> None:
    """正控：同一把量具在一个**已知 import 了端口**的模块上必须读到东西。"""
    assert "marketops.ports.search" in _imported(LINKED)
    assert "marketops.ports.acquisition" in _imported(FETCHING)
    assert "ast" in _imported(Path(__file__))


def test_the_family_imports_exactly_what_it_needs() -> None:
    """白名单**精确相等**。多一个 import 就是多一条通向外部世界的路。"""
    found = _imported(MODULE)
    assert found == ALLOWED_IMPORTS, (
        f"多出来的：{sorted(found - ALLOWED_IMPORTS)}；"
        f"少掉的：{sorted(ALLOWED_IMPORTS - found)}"
    )


def test_the_family_cannot_reach_a_search_or_fetch_port_by_import() -> None:
    """两条**点名**的排除，与上面那条精确相等是两件事。

    精确相等挡的是"多了一个我不知道的 import"；这一条挡的是"白名单被顺手放宽成
    包含检索端口"—— 那时上面那条会绿，而这一条会红。两条都在，改错任何一条都有人
    看得见。
    """
    found = _imported(MODULE)
    assert not any("search" in name or "acquisition" in name for name in found)
    assert not any(name.startswith("marketops.adapters") for name in found)


# --- 2 · 调用名 -----------------------------------------------------------------


def test_the_instrument_reads_calls_at_all() -> None:
    """正控：两把量具都真的读到了调用。

    点名的两个是这一族**确实有**的：``sha256_text``（每一格的值摘要）与
    ``put_scene`` / ``get_scene``（冻结那一步的写与读回）。
    """
    assert "search" in _attribute_calls(LINKED) or "fetch" in _attribute_calls(LINKED)
    assert "sha256_text" in _name_calls(MODULE)
    assert {"put_scene", "get_scene"} <= _attribute_calls(MODULE)


def test_the_family_calls_no_io_and_no_clock() -> None:
    """没有 I/O、没有时钟、没有动态执行。

    **没有时钟**是「``at`` 由外面给」这句话的判据：``compile_effective_scene``
    收一个 ``at``，而它不自己去取一个"现在"。
    """
    assert _name_calls(MODULE) & FORBIDDEN_BUILTIN_CALLS == set()
    assert _attribute_calls(MODULE) & FORBIDDEN_ATTRIBUTE_CALLS == set()


def test_the_probe_can_see_a_call_it_should_see() -> None:
    """正控：喂一段**故意**有 ``open`` / ``now`` 的源码，两把量具都要报出来。"""
    bad = """
        def probe(path):
            handle = open(path)
            return datetime.now(), eval("1 + 1")
    """
    assert {"open", "eval"} <= _name_calls_from(bad)
    assert "now" in _attribute_calls_from(bad)
    assert _name_calls_from(bad) & FORBIDDEN_BUILTIN_CALLS == {"open", "eval"}
    assert _attribute_calls_from(bad) & FORBIDDEN_ATTRIBUTE_CALLS == {"now"}


# --- 3 · 注解 -------------------------------------------------------------------


def test_the_instrument_reads_annotations_at_all() -> None:
    """正控：注解量具在一个**已知注解了端口**的模块上读到东西，而在本族上读到的是
    它**读得动**的那两个（一个读端口 + 一个存储端口）。"""
    assert "SearchProvider" in _annotation_names(LINKED)
    assert {"ProductCatalog", "EffectiveSceneStore"} <= _annotation_names(MODULE)


def test_the_family_cannot_even_name_a_search_or_fetch_port() -> None:
    """**类型注解**里不得出现检索/抓取端口的类名。

    这是「零外部调用」的**构造**那一半：一条 ``search: SearchProvider`` 的形参
    —— 哪怕这一族今天一次都不调它 —— 就已经把那条判据从结构降成了一句自觉的话。
    """
    found = _annotation_names(MODULE)
    assert found & FORBIDDEN_ANNOTATION_NAMES == set(), (
        f"注解里出现了端口：{sorted(found & FORBIDDEN_ANNOTATION_NAMES)}"
    )


def test_the_probe_can_see_an_annotation_it_should_see() -> None:
    """正控：喂一段把端口注解上去的源码，量具必须报出来（两种写法都要）。"""
    for bad in (
        "def probe(search: SearchProvider) -> None: ...",
        "def probe(search: ports.SearchProvider) -> None: ...",
        "def probe() -> Fetcher: ...",
    ):
        assert _annotation_names_from(bad) & FORBIDDEN_ANNOTATION_NAMES, bad


# --- 4 · 签名就是那几样 ---------------------------------------------------------


def test_the_compile_step_is_a_pure_function_with_no_port_at_all() -> None:
    """**这一族最强的一条判据。** 编译本身不碰端口、不碰时钟、不碰存储 ——
    它的九个入参**全是已经读出来的值**。

    写成"参数名的精确相等"而不是"参数里没有端口名"：前者顺带钉住"没有多出一个"，
    而多出来的那一个恰恰是最容易被顺手加上的（"反正加个 store 就能顺便查一下"）。
    """
    assert list(inspect.signature(compile_effective_scene).parameters) == [
        "scene",
        "record",
        "workspace",
        "geo",
        "motion",
        "product",
        "offer",
        "at",
        "compiler",
    ]
    for name, parameter in inspect.signature(compile_effective_scene).parameters.items():
        assert parameter.kind is inspect.Parameter.KEYWORD_ONLY, name
    # 两个读法一个收目录、一个收存储 + 目录；两者都没有第三个能花钱的格子。
    assert list(inspect.signature(preview_effective_scene).parameters) == [
        "scene",
        "record",
        "workspace",
        "catalog",
        "at",
        "compiler",
        "geo",
        "motion",
        "product_id",
        "product_version",
        "offer_id",
        "offer_version",
    ]
    assert list(inspect.signature(freeze_effective_scene).parameters) == [
        "store",
        "scene",
        "record",
        "workspace",
        "catalog",
        "at",
        "compiler",
        "geo",
        "motion",
        "product_id",
        "product_version",
        "offer_id",
        "offer_version",
    ]
    assert list(inspect.signature(read_effective_scene).parameters) == [
        "store",
        "scene_id",
        "version",
        "workspace",
    ]


def test_the_annotated_ports_are_exactly_the_two_local_read_ones() -> None:
    """这一族注解到的端口**只有两个**：一个读目录、一个存储。两者都不花钱。

    冻结那一格是**写**，而它写的是 ``EffectiveSceneStore`` —— 一个本地存储。

    注：本模块有 ``from __future__ import annotations``，所以注解在**运行时**是
    字符串；``get_type_hints`` 是唯一能把它们解回类对象的读法（直接比较字符串
    会在有人加了 ``TYPE_CHECKING`` 分支或改了 import 形状时静默放过去）。
    """
    resolved = {
        name: get_type_hints(function)[name]
        for function, name in (
            (preview_effective_scene, "catalog"),
            (freeze_effective_scene, "catalog"),
            (freeze_effective_scene, "store"),
            (read_effective_scene, "store"),
        )
    }
    assert resolved["catalog"] is ProductCatalog
    assert resolved["store"] is EffectiveSceneStore
    # 三个函数合起来够得到的 ``ports.*`` 类**就这两个** —— 而 ``compile`` 一个都不收
    # （上一条判据）。按"来自哪个模块"问，而不是按名字列举：以后有人加一个
    # ``ports.search`` 里的类，这一条也会红，而它不需要有人记得来改这张表。
    reachable_ports = {
        hint
        for function in (preview_effective_scene, freeze_effective_scene, read_effective_scene)
        for hint in get_type_hints(function).values()
        if isinstance(hint, type) and hint.__module__.startswith("marketops.ports.")
    }
    assert reachable_ports == {ProductCatalog, EffectiveSceneStore}


# --- 5 · 别的两件结构事实 -------------------------------------------------------


def test_the_family_catches_nothing() -> None:
    """这一族里**一个 ``except`` 都没有**。

    三处拒绝（工作区对不上、指名了读不回来）都是**抛**，而调用方按类型接。一旦有人
    把某一处包进 ``try/except``，那份「读不回来是一次错误、没有产品是一次缺席」的
    区分就再也没人核对得起来了 —— 而它正是这一族存在的理由。
    """
    assert _tries(MODULE) == []


def test_the_family_reads_no_environment_variable() -> None:
    """没有 ``os`` / ``environ`` / ``getenv`` —— 一份编译结果不取决于"这台机器上
    设了什么"。它在两台机器上跑出不同结果时，报告上没有任何一格能说出为什么。"""
    source = MODULE.read_text(encoding="utf-8")
    assert "environ" not in source
    assert "getenv" not in source
    assert "import os" not in source


@pytest.mark.parametrize(
    "source",
    [
        # 一整个 import 了检索端口的模块 —— 白名单那一条必须红。
        "from marketops.ports.search import SearchProvider\n",
        # 相对 import 也要被还原成绝对名（否则加一层 ``..`` 就绕过去了）。
        "from ..ports.search import SearchProvider\n",
    ],
)
def test_the_probe_reads_imports_it_should_read(source: str) -> None:
    """正控：相对 import 那条路必须也读得出来 —— 它是绕开白名单最省事的一种写法。"""
    found = _imported_source(source, package="marketops.application")
    assert "marketops.ports.search" in found
    assert found - ALLOWED_IMPORTS, "白名单判据要能看见它"
