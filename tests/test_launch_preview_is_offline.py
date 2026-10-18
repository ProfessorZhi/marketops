"""``marketops/application/launch_preview.py`` 的**构造成判据** —— 启动授权前零外部调用。

B01 第五条关闭条件逐字八个字（``docs/MEMBER_A_AUTOMATION_EXECUTION_PLAN.md:104`` 第 5 项）：

    启动授权前零外部调用

而 ``docs/MEMBER_A_AUTOMATION_EXECUTION_PLAN.md:102`` 那条启动用例逐字要求
「启动用例先 preview……有批准才持久受理 Scene/Workstream 及 Job」。两句话合起来是同一
件事的两面：**被 preview 的那个动作必须还没发生**，而 preview 自己必须是一次**读数**。

## 为什么这一条要在这里量，而不是靠自觉

一个恒真的合取项读不出因果关系（``ProductReadiness.usable`` 的 docstring 对同一件事
论证过一次）。「零外部调用」如果只是一句承诺，那么接线的那一天由「谁顺手加了一个客户端
参数」决定它花不花钱 —— 而那不是假想的：本仓已经有一处量具在防同一件事
（``tests/test_registration_is_offline.py`` 量的是登记面的 import 闭包，
``tests/test_run_preview.py`` 量的是 Run preview 那一层）。

## 与 B16/B17 那两处量具的关系：同形，不同款

* ``plan_preview`` **拿不到端口**（它连存储都不收），所以「零 provider 调用」在那里是
  签名短；
* ``run_preview``（§B17-g）**必须读三本本地账**，于是构造换成「签名只收存储与已经读出来
  的值 + 不读时钟 + 注解里没有端口类名」。

这一层与第二款同类：它要读**三本本地的账**（场景记录、产品目录、已批准说法）才答得出
「这一版如果现在启动缺什么」，所以判据照 ``tests/test_run_preview.py`` 那一族的 AST 量具
逐条搬过来，白名单**明写**本地存储端口，**明禁** ``ports.search`` / ``ports.acquisition``
那一族（它们花钱）。

## 三件量具盯的事

1. **import 白名单精确相等**：多一个 import 就是多一条通向外部世界的路。
2. **调用名**：没有 I/O（``open``/``read_text``/``fetch``…），没有时钟（``now``/
   ``utc_now``），没有动态执行（``eval``/``compile``）。**没有时钟**尤其：这一层是
   「读的是当下」的一份读数，``at`` 由外面给（与 ``admission_refusal`` 同款）——
   一次读数内部各读一次时钟，会让同一次调用里的几个判定各用一个时刻而看不出来。
3. **类型注解**：不得出现检索/抓取端口的类名。拿得到端口，调用它只是下一行的事；
   而拿不到它的层没有可以调用的东西 —— 这是「零外部调用」的**构造**那一半。

三条各配一个**正控**（在同一把量具上量一个已知会红的对象），理由是
``tests/test_run_preview.py`` 那一族写过的：没有正控，白名单判据在「一个 import 都没
扫到」的时候也是绿的，而那时它验的是它自己坏掉了。
"""

from __future__ import annotations

import ast
import inspect
from pathlib import Path

from marketops.application.launch_preview import launch_preview
from marketops.config import REPO_ROOT

MODULE = REPO_ROOT / "marketops" / "application" / "launch_preview.py"

#: 两个**已知会红**的对照：一个 import 了检索端口，一个 import 了本层的邻居。
LINKED = REPO_ROOT / "marketops" / "application" / "billed_search.py"
PEER = REPO_ROOT / "marketops" / "application" / "run_preview.py"

#: 这一层的 import 白名单，**精确相等**。
#:
#: 两本本地的账在这一层**必须**在：``ports.product_catalog``（产品资料与方案）与
#: ``ports.outreach``（已批准说法）—— 待引对象的读数由
#: ``application.product_readiness`` 的两个读法算，它们的 ``catalog`` / ``claims``
#: 是必填参数（见那个模块的 docstring）。
#:
#: **``ports.scenes`` 不在这一份上**，而这是刻意的：那一本账（立项记录与版本）在这一层
#: 是**已经读出来**的两个入参（``scene`` / ``record``）。理由与 ``run_preview`` 逐字
#: 同款 —— 「主语那一版是不是别人的」是一次**拒绝整次读数**的判定，它属于调用方那一步
#: （那一侧才有「这一次是谁在问」），在读了之后再由这一层重判一遍，只会让同一条守卫
#: 有两个时刻、两份判词。少一本账不等于少一条判据：它由 ``launch_preview`` 的
#: ``workspace`` 入参承载（见那个函数的 docstring）。
#:
#: ``not_reported`` 换掉了 ``plan_preview``，而那一换**不是一个技术细节**：这一层要用
#: 的只有 ``NotReported`` 一个值对象，而 ``plan_preview`` 为了编计划 import 了
#: ``research_strategy``（进而 ``ports.acquisition``）。这一条白名单曾经放行过
#: ``plan_preview``，也就是说这张表当时**允许**这一层间接拿到抓取契约 —— 而放行的理由
#: 只是「它提供这一格」。``NotReported`` 因此搬到自己的模块里（那个模块的 docstring
#: 记了完整的那条链，量出它的是 ``tests/test_mcp_launch_is_offline.py``），而这一行
#: 从此钉住「借这一格不必顺带把抓取契约也借来」。
ALLOWED_IMPORTS = frozenset(
    {
        "__future__",
        "collections.abc",
        "dataclasses",
        "datetime",
        "enum",
        "typing",
        "marketops.domain.product",
        "marketops.domain.scene",
        "marketops.ports.outreach",
        "marketops.ports.product_catalog",
        "marketops.application.not_reported",
        "marketops.application.product_readiness",
        "marketops.application.scene_receipt",
    }
)

#: 内建那一边：只会以 ``open(...)`` / ``compile(src, ...)`` 这种**名字调用**的形状出现。
FORBIDDEN_BUILTIN_CALLS = frozenset({"open", "eval", "exec", "compile", "__import__"})

#: 属性那一边。**与 ``test_run_preview.py`` / ``test_plan_preview.py`` 那一份逐字相同**
#: —— 三张表的理由写在那个文件的常量旁边（``x.compile()`` 是对象自己的方法，不是内建
#: ``compile``）。
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

#: **注解里**不得出现的名字。名字取自 ``ports/search.py`` 与 ``ports/acquisition.py`` 的
#: 类名，与 ``tests/test_run_preview.py`` 那一份逐字相同。
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


# --- AST 那一族共用的量具（与 ``test_run_preview.py`` 同形） ------------------


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
    而那时它验的是它自己坏掉了。``billed_search.py`` import 了检索端口（那是它存在的
    理由），``run_preview.py`` import 了另外三本本地账（那是**同类**的一层，不在本层
    白名单上：本层不读 Run 的账）。
    """
    assert "marketops.ports.search" in _imported(LINKED)
    assert "marketops.ports.runs" in _imported(PEER)
    assert "ast" in _imported(Path(__file__))


def test_the_layer_imports_exactly_what_it_needs() -> None:
    """import 白名单**精确相等**（「启动授权前零外部调用」）。

    写成白名单而不是黑名单：一个 ``import socket`` 与一个 ``import httpx`` 都在黑名单
    之外，也在白名单之外。多一个 import 就是多一条通向外部世界的路，而这条路要么是
    刻意加上去的（那时改这一行），要么不该在。
    """
    found = _imported(MODULE)
    assert found == ALLOWED_IMPORTS, (
        f"多出来的：{sorted(found - ALLOWED_IMPORTS)}；"
        f"少掉的：{sorted(ALLOWED_IMPORTS - found)}"
    )
    assert not any("search" in name or "acquisition" in name for name in found)


def test_the_scanner_reads_calls_at_all() -> None:
    """正控：两把量具都真的读到了调用。

    这里点名的两个都是这一层**确实有**的调用：``version_receipt``（一版 Scene 的回执，
    与 ``create_scene`` 交出去的是同一份）与 ``offer_version_readiness``（待引对象的
    读数，b01-c 那一层算）。
    """
    assert "applies_to" in _attribute_calls(LINKED) or "search" in _attribute_calls(LINKED)
    assert {"version_receipt", "offer_version_readiness"} <= _name_calls(MODULE)


def test_the_layer_calls_no_io_and_no_clock() -> None:
    """调用名里没有 I/O、没有时钟，也没有动态执行。

    **没有时钟**是这一层与 ``preview_plan`` 不同款的地方：那一层连存储都不收，所以
    「不读时钟」顺理成章；这一层读三本账，于是 ``at`` 必须是外面给的一个**参数**
    （与 ``admission_refusal`` 同款）—— 一次读数里有好几处判定都用它，各读一次时钟
    会让这份读数自相矛盾，而报告上看不出来。
    """
    assert _name_calls(MODULE) & FORBIDDEN_BUILTIN_CALLS == set()
    assert _attribute_calls(MODULE) & FORBIDDEN_ATTRIBUTE_CALLS == set()


def test_the_scanner_reads_annotations_at_all() -> None:
    """正控：注解量具在一个**已知注解了端口**的模块上必须读到东西。

    对照组 ``billed_search.py`` 的 ``__init__`` 逐字写着 ``search: SearchProvider``
    —— 本层的注解里不该有它；本层注解里该有的是它**读得动**的那两个端口。
    """
    assert "SearchProvider" in _annotation_names(LINKED)
    assert {"ProductCatalog", "OutreachStore"} <= _annotation_names(MODULE)


def test_the_layer_cannot_even_name_a_search_or_fetch_port() -> None:
    """**类型注解**里不得出现检索/抓取端口的类名。

    这一条比调用名那条更强，也是「零外部调用」的**构造**那一半：拿得到端口，调用它
    只是下一行的事；而拿不到它的层没有可以调用的东西。所以一条 ``search:
    SearchProvider`` 的形参 —— 哪怕这一层今天一次都不调它 —— 就已经把那条判据从结构
    降成了一句自觉的话。

    端口那两族的类名逐字取自 ``ports/search.py`` 与 ``ports/acquisition.py``。
    """
    found = _annotation_names(MODULE)
    assert found & FORBIDDEN_ANNOTATION_NAMES == set(), (
        f"注解里出现了端口：{sorted(found & FORBIDDEN_ANNOTATION_NAMES)}"
    )


def test_the_layer_catches_nothing() -> None:
    """这一层里**一个 ``except`` 都没有**。

    三张清单都是**返回行而不抛**的（``ReadinessGap`` 是一个值），所以这一层没有异常
    需要接。一旦有人把某一处包进 ``try/except``，那份「盘上读不到就是一条缺口，不是
    一次崩溃」的承诺就再也没人核对得起来了 —— 而它正是「不让模型读一段 traceback 猜」
    （PSM ``:229``）在本层的全部内容。
    """
    tree = ast.parse(MODULE.read_text(encoding="utf-8"))
    assert [node for node in ast.walk(tree) if isinstance(node, ast.Try)] == []


def test_the_layer_takes_a_version_and_already_read_values_only() -> None:
    """签名就是那几样，**全是 keyword-only**，且没有一个是能塞进客户端的格子。

    三件事在这里被看见：

    * **全 keyword-only**：参数里有四组同类的一对（两对引用、两个存储、…），位置传参
      会让「第 5 个参数是什么」只能回去数一遍。
    * **没有 ``at`` 之外的第二个时刻格**：``at`` 由调用方给（见上面那条），这一层
      自己不许再取一个「现在」。
    * **没有可以塞端口进来的格子**：每一个注解都是本仓的域对象或本地存储端口。
    """
    signature = inspect.signature(launch_preview)
    assert list(signature.parameters) == [
        "scene",
        "record",
        "workspace",
        "at",
        "catalog",
        "claims",
        "product_id",
        "product_version",
        "offer_id",
        "offer_version",
    ]
    for parameter in signature.parameters.values():
        assert parameter.kind is inspect.Parameter.KEYWORD_ONLY, parameter.name
