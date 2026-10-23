"""厂商模块只准从 ``adapters/`` 里 import —— 由本测试执行。

§2 逐字：「业务代码不得直接依赖 Serper / Jev / Laya / Crawl4AI 或任何执行器的 API」。
``tests/test_ports_are_contracts_only.py`` 守的是**另一半** —— 端口有没有被某一个
实现污染。两条判据容易混为一谈（今天都指向 ``adapters/``），但它们守的是不同的
失败：

    一个端口可以一行 vendor 都不 import，业务层照样能绕过它直接 ``import httpx``。
    这个失败**不会**让任何一张测试变红 —— 端口还在、ABC 还在、``isinstance``
    还在，只是从此没有人再从它走。契约成了摆设，而调用方拿到了真正的能力。

所以这条判据的形态与端口那条不同：它不问「这个模块依赖了什么」，它问
**「厂商模块除了 ``adapters/`` 还有谁 import 得到」**。

## 为什么 ``classify`` 在 ``adapters/`` 里

``marketops/adapters/classify.py`` 是纯函数，一行 vendor 都不 import，看上去
更该进 ``domain/``。它留在这里的理由是**它的判据读的是 HTTP 响应才有的形状**
（状态码、``content-type``、正文长度），而它的唯一消费者是 ``http`` 与
``crawl4ai_fetcher``。放进 ``domain/``，领域层就开始为「一个 HTTP 响应长什么样」
积累指纹 —— 两张测试依然全绿，而「domain = 业务事实」这个模型已经失效。

这条不是猜的：它正是本测试要防的那种「改完之后没有任何执行器会响」的改动。

## 刻意**不在**这份清单里的东西

``pydantic`` / ``yaml`` / ``bs4`` 等**不是**厂商。判据是「这个 import 会不会把
业务代码绑到一个**外部提供者**身上」，不是「它是不是第三方」：

* ``pydantic`` 在 ``domain/base.py``、``domain/evidence.py``、``domain/entities.py``
  里 —— 它做校验，不碰网络、不认厂商，是领域类型自己的形状。禁掉它等于把领域层
  的建模工具一起禁掉。
* ``yaml`` 是**文件格式**，不是提供者。它在 ``adapters/profile_loader.py``（装载器）与
  ``application/cohorts.py``（读账号清单）里。注意这与端口层那份清单**故意不同**：
  ``test_ports_are_contracts_only.FORBIDDEN_MODULES`` 禁 ``yaml``，因为端口不得做
  I/O。两条判据的清单不一样不是疏漏 —— 合并它们会让其中一条失去意义。

## 扫描范围为什么不含 ``tests/``

本测试问的是**业务代码**的边界。测试需要能构造替身与本地服务器，那是它的职责，
不是放弃边界。``tests/test_no_evasion.py`` 覆盖 ``tests/``，但那是另一条判据
（不得引入规避能力），与「谁 import 得到厂商」是两件事。

## 另一半：**组合根**规则（B7a-3 接上）

上面那条判据问的是「厂商 **SDK** 还有谁 import 得到」，它只看**绝对** import。
但 §2 还有一条更里层的规则：

    只有 ``interfaces/`` 与 ``adapters/`` 自己可以**点名**某个具体厂商 adapter。

``from marketops.adapters.serper import SerperSearchProvider`` 里的 ``serper``
不是厂商 SDK，它是**本仓的模块** —— 上面那条判据对它一个字都说不出。而它同样是
一次真实的绑定：写下这一行的人，从此刻起就依赖"搜索是 Serper 给的"。

这条规则的执行器比上面那条难写，难在两处，两处都不是风格问题：

1. **判据的本体是传递闭包，不是直接 import。** ``sqlite/evidence_store.py`` 自己
   一行厂商都不 import，它 ``from .db import ...``，而 ``sqlite3`` 在 ``db.py`` 里。
   只判直接 import 会把它判成干净的 —— 判据于是以"没有违规"的样子全绿，而它碰的是
   磁盘。``_vendor_adapter_closure`` 把这条路算出来，并**留一条见证链**：判决旁边
   放着理由，判错的时候能看出是哪一步算错了。
2. **放行范围必须是写下来的、可被证明非空的。** 一条"谁都不准点名厂商 adapter"的
   判据同样永远全绿，也同样是废的。所以这里有两组对照：一组证明业务层的位置会被判红，
   一组证明 ``interfaces/`` 里**真的**在点名、且真的被放行。

``feishu/`` 是这条规则的一个现成例子 —— 而且它**已经翻面过一次**。原先它**不是**
vendor adapter（``client.py`` 是 ``interface_only``，一行 ``lark_oapi`` 都没有）；
2026-09-24 那条写入路径落地，用的是 ``httpx`` 而不是 ``lark_oapi``，于是闭包自动把
``adapters/feishu/client.py`` 收了进来，而 ``interfaces/`` 之外任何点名它的位置会
当场变红 —— 不需要有人记得回来改这份清单。这正是上一段说的那件事：判据算的是
**今天的 import 图**，不是一份需要跟着改的名单（`lark_oapi` 至今仍然一行都没有，
所以它出现在这里不代表有人在用）。

## 一份预先声明的清单，在 Phase C 被真的用上

``VENDOR_MODULES`` 里的 ``mcp`` 是 **B4** 写下的，那时本仓一行 MCP 代码都没有 ——
它当时属于「协议 / 服务端 SDK」那一组（与 ``fastapi`` / ``starlette`` / ``uvicorn``
一起），是一条**预先声明的规则**，不是对既成事实的描述。Phase C 落地 MCP 服务面时，
这份预先声明果然生效了：服务面的四个模块最初写在 ``interfaces/mcp/`` 下，主判据
把它们逐条报了出来，于是 SDK 绑定搬进了 ``marketops/adapters/mcp_server/``。

搬家的理由与代价写在 ``marketops/adapters/mcp_server/__init__.py``。这里只记一件
与本文件有关的事：**没有给 ``interfaces/`` 开一条 vendor SDK 的口子。** 本层被授予
的权力是「点名具体厂商 adapter」（上面那条组合根规则），不是「import 厂商 SDK」——
``interfaces/composition.py`` 是这句话的现成反例：它 import ``pypdf`` 的 **adapter**，
不是 ``pypdf`` 本人。放宽这一条会让 ``interfaces/`` 里的任何文件都能直接 ``import
httpx``，而那正是本文件第一段要防的那件事。
"""

from __future__ import annotations

import ast
from collections.abc import Collection
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

#: 唯一允许出现厂商 import 的地方（含子包）。
VENDOR_HOME = REPO_ROOT / "marketops" / "adapters"

#: 扫描范围。``tests/`` 刻意不在其中 —— 理由见模块 docstring。
#: ``services/`` 曾在这里，本轮随它的两个占位目录一起删掉（``docs/NOT_BUILT.md``）。
SCANNED_ROOTS: tuple[str, ...] = ("marketops",)

#: 厂商模块（按顶层模块名匹配）。清单的标准是**外部提供者**，不是「第三方」。
VENDOR_MODULES: frozenset[str] = frozenset(
    {
        # 网络 / HTTP 客户端
        "httpx", "requests", "aiohttp", "urllib3", "httpcore", "socket",
        # 起外部进程。与 ``socket`` 同一条理由进这份清单：它是**标准库**，也是
        # 「怎么够到一个外部提供者」那条路本身。受限 worker 是外部程序，没有
        # Python SDK，本仓与它的全部界面就是一个子进程
        # （``adapters/agent_runtime.py``），所以「谁 import 得到 subprocess」与
        # 「谁 import 得到 httpx」问的是同一件事 —— 只是换了一个外部提供者。不加
        # 这一条的话，``agent_runtime`` 会落到**闭包之外**（它一个厂商 SDK 都不
        # import），于是组合根那条规则对它说不出一个字：``application/`` 可以直接
        # import 它而没有任何一条判据会响。
        "subprocess",
        # 浏览器与采集
        "crawl4ai", "playwright", "patchright", "selenium", "pyppeteer",
        # 文档 / 模型推理
        "pypdf", "mineru", "torch", "transformers", "onnxruntime",
        # 决策 provider
        "typesafe_sdk", "jev", "laya", "openai", "anthropic",
        # 协议 / 服务端 SDK
        "mcp", "fastmcp", "fastapi", "starlette", "uvicorn",
        # 存储引擎
        "sqlite3", "psycopg", "psycopg2", "redis", "pymongo",
        # 云侧
        "lark_oapi", "feishu",
    }
)

#: **必须**被 ``VENDOR_MODULES`` 覆盖、且在 ``adapters/`` 里**真的**被用到的厂商。
#:
#: 存在的理由只有一条：``VENDOR_MODULES`` 是一个可以被人删掉的名字。删掉
#: ``httpx`` 之后违规扫描立刻全绿，而 ``httpx`` 还在 ``http.py`` 里跑着 ——
#: 「没有违规」变成「清单缩水」的假象。这条对照把那份清单钉在**实际用法**上：
#: 名单里少了任何一个当前真在用的厂商，它就红。
#: ``laya`` 与 ``typesafe_sdk`` 是 Phase F 补进来的。它们在 **B4** 就已经写进了
#: ``VENDOR_MODULES``（那一段注释里叫「决策 provider」），但那时一行决策代码都没有 ——
#: 所以那份清单当时只是一条预先声明，这一行才是把声明接到**实际用法**上的那一步。
#: 两件事分开做是有意的：合并的话，「清单里预先写了但没人用」与「有人在用但清单里
#: 没有」会以同一条红出现，而它们的处置恰好相反。
MUST_BE_PRESENT: tuple[str, ...] = (
    "httpx",
    "crawl4ai",
    "pypdf",
    "mineru",
    "mcp",
    "laya",
    "typesafe_sdk",
    "subprocess",
)

#: **组合根**：唯一允许点名具体厂商 adapter 的地方（``adapters/`` 自己除外）。
COMPOSITION_ROOTS: tuple[str, ...] = ("marketops/interfaces",)

#: 反缩水对照，与 ``MUST_BE_PRESENT`` 同一个理由：一个可以被人删空的清单。
#: 这几条都是**直接** import 厂商的 adapter。
MUST_BE_VENDOR_ADAPTERS: tuple[str, ...] = (
    "marketops.adapters.agent_runtime",
    "marketops.adapters.crawl4ai_fetcher",
    "marketops.adapters.http",
    "marketops.adapters.jev",
    "marketops.adapters.laya",
    "marketops.adapters.mcp_server.server",
    "marketops.adapters.mineru_adapter",
    "marketops.adapters.serper",
    "marketops.adapters.sqlite.db",
    "marketops.adapters.text_pdf",
)

#: 只做**直接** import 判定时会漏掉、必须靠传递闭包才推得出来的两个。
#: 它们在闭包里的存在本身就是「判据是闭包而不是扫描」这句话的执行器。
MUST_BE_FOUND_ONLY_BY_CLOSURE: tuple[str, ...] = (
    "marketops.adapters.sqlite.evidence_store",
    "marketops.adapters.sqlite.ledger",
)


def _scanned_files() -> list[Path]:
    files: list[Path] = []
    for root_name in SCANNED_ROOTS:
        root = REPO_ROOT / root_name
        if not root.is_dir():
            continue
        for path in sorted(root.rglob("*.py")):
            if "__pycache__" in path.parts:
                continue
            files.append(path)
    return files


def _module_name(path: Path) -> str:
    """``a/b/c.py`` → ``"a.b.c"``；``a/b/__init__.py`` → ``"a.b"``。

    包自己的名字**不含** ``__init__`` —— 这是 ``import a.b`` 实际绑定的那个名字，
    也是 ``_package_name`` 与 ``_resolve_relative`` 唯一的基准。
    """
    rel = path.resolve().relative_to(REPO_ROOT).with_suffix("")
    parts = list(rel.parts)
    if parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts)


def _package_name(path: Path) -> str:
    """这份源码所在的**包** —— 相对 import 的基准。"""
    name = _module_name(path)
    return name if path.name == "__init__.py" else name.rpartition(".")[0]


def _resolve_relative(level: int, module: str | None, package: str) -> str:
    """``from ..adapters.http import X``（在 ``marketops.interfaces`` 里）→
    ``"marketops.adapters.http"``。

    ``level=1`` 是**当前包**，``level=2`` 是父包 —— 少减这一格会把每一个相对
    import 都解析到隔壁，而解析错的后果是判据静默失效，不是报错。
    """
    if level == 0:
        return module or ""
    parts = package.split(".") if package else []
    up = level - 1
    base = parts[: len(parts) - up] if up else parts
    return ".".join([*base, *([module] if module else [])])


def _imported_modules(tree: ast.AST, *, package: str | None) -> set[str]:
    """这份 AST import 到的**仓库内模块全名**集合。

    ``package`` 是这份源码所在的包；``None`` 表示「相对 import 一律丢弃」——
    那正是 ``_imported_roots`` 需要的语义（见下）。

    两处容易漏，两处都是真的洞：

    * ``import a.b.c`` 会同时记下 ``a`` / ``a.b`` / ``a.b.c``：这条语句真的把
      三层名字都绑了进来，只记最后一个会漏掉以中间层为目标的判据。
    * ``from a.b import c`` 会记下 ``a.b`` **和** ``a.b.c``。少了后一条，
      ``from marketops.adapters import serper`` 就从组合根判据底下钻过去了 ——
      而它与 ``from marketops.adapters.serper import ...`` 是同一件事。

    函数内的 import 也算 —— ``mineru`` 与 ``crawl4ai`` 都在函数 / ``try`` 块里
    延迟导入，而延迟导入**恰恰**是最容易被顺手挪进业务代码的形态。
    """
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                bits = alias.name.split(".")
                names.update(".".join(bits[: i + 1]) for i in range(len(bits)))
        elif isinstance(node, ast.ImportFrom):
            if node.level and package is None:
                continue  # 调用方只要厂商包，不要仓库内解析
            base = _resolve_relative(node.level, node.module, package or "")
            if not base:
                continue
            names.add(base)
            names.update(f"{base}.{alias.name}" for alias in node.names)
    return names


def _imported_roots(tree: ast.AST) -> set[str]:
    """顶层模块名集合 —— 上面那条**厂商 SDK** 判据用的形状。

    只看**绝对** import：相对 import（``from ..adapters.http import ...``）指的是
    仓库内模块，不是厂商包，由组合根规则管辖（见模块 docstring）。
    """
    return {name.split(".")[0] for name in _imported_modules(tree, package=None)}


def _violations(paths: list[Path]) -> list[str]:
    """在 ``paths`` 里找出「不该出现厂商 import 却出现了」的位置。

    抽成函数是为了让正对照能证明**真正的判据**会红，而不是只证明解析器会红 ——
    一条只测 ``ast`` 的正对照，在判据恒假时仍然会绿。
    """
    offenders: list[str] = []
    for path in paths:
        if path.is_relative_to(VENDOR_HOME):
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for vendor in sorted(_imported_roots(tree) & VENDOR_MODULES):
            offenders.append(f"{path.relative_to(REPO_ROOT).as_posix()} import {vendor}")
    return offenders


def _vendor_roots(imported: set[str]) -> set[str]:
    """``imported``（模块全名集合）里哪些**顶层名**是厂商。

    ``VENDOR_MODULES`` 是按顶层名匹配的清单（见它的注释），所以
    ``from mineru.cli.common import do_parse`` 碰的是 MinerU —— 拿全名去交集会
    一个都匹配不上，而后果同样是**静默**的：``mineru_adapter`` 会被判成干净的。
    """
    return {name.split(".")[0] for name in imported} & VENDOR_MODULES


def _adapter_files() -> list[Path]:
    return [path for path in _scanned_files() if path.is_relative_to(VENDOR_HOME)]


def _vendor_adapter_closure() -> dict[str, tuple[str, ...]]:
    """``adapter 模块名 → 一条通往厂商的见证链``。

    链的最后一节是**厂商 SDK 的名字**，前一节是真正 import 它的那个 adapter，
    中间每一节都是 ``from .x import ...`` 这种仓库内的相对 import。所以
    ``evidence_store`` 的链是 ``(evidence_store, sqlite.db, sqlite3)`` ——
    判它「碰厂商」的理由就在判决旁边，而判错时能看出是哪一步算错了。

    只判**直接** import 的写法会漏掉链长大于 2 的那些，且漏得**静默**：判据以
    「没有违规」的样子全绿。``MUST_BE_FOUND_ONLY_BY_CLOSURE`` 是对照。

    「谁碰了厂商」按**顶层模块名**判（``VENDOR_MODULES`` 自己的口径），而「点名了
    哪个 vendor adapter」按**完整模块名**判。两者口径不同是对的：厂商是一个包名
    （``from mineru.cli.common import ...`` 碰的就是 MinerU），而 vendor adapter 是
    本仓里一个具体的模块文件。
    """
    imports: dict[str, set[str]] = {}
    direct: dict[str, str] = {}
    for path in sorted(_adapter_files()):
        name = _module_name(path)
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        imported = _imported_modules(tree, package=_package_name(path))
        imports[name] = imported
        vendors = sorted(_vendor_roots(imported))
        if vendors:
            direct[name] = vendors[0]

    witness: dict[str, tuple[str, ...]] = {name: (name, vendor) for name, vendor in direct.items()}
    changed = True
    while changed:  # 不动点：一轮加一层，直到没有新的模块被拉进来
        changed = False
        for name in sorted(imports):
            if name in witness:
                continue
            reached = sorted(imports[name] & witness.keys())
            if reached:
                witness[name] = (name, *witness[reached[0]])
                changed = True
    return witness


def _names_vendor_adapters(
    source: str, *, module_name: str, vendor_adapters: Collection[str]
) -> list[str]:
    """这份源码点名了哪些 vendor adapter。**组合根判据的本体**。

    独立成函数（而不是藏在走目录的循环里）是为了让正对照打的是**判据**：
    给一段真在业务层里写的 ``from ..adapters.serper import ...``，它必须吐出来。
    ``module_name`` 是这段源码"如果落在仓库里"会有的模块名 —— 相对 import 靠它
    解析，所以正对照给的名字不同，同一行源码的判决就不同（这正是判据要测量的东西）。
    """
    tree = ast.parse(source)
    imported = _imported_modules(tree, package=module_name.rpartition(".")[0])
    return sorted(imported & set(vendor_adapters))


def _vendor_adapter_import_sites(
    paths: list[Path], vendor_adapters: Collection[str]
) -> list[tuple[Path, str]]:
    """``paths`` 里每一处「点名了某个 vendor adapter」的位置，**含被放行的那些**。

    故意不在这里筛放行范围：先拿到全集，才能用「集合非空」证明扫描真的看见了东西
    （见 ``test_the_criterion_sees_the_composition_roots``）。只返回违规者的函数
    没法区分「没问题」与「没看见」。
    """
    sites: list[tuple[Path, str]] = []
    for path in paths:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        imported = _imported_modules(tree, package=_package_name(path))
        sites.extend((path, name) for name in sorted(imported & set(vendor_adapters)))
    return sites


def _is_composition_root(path: Path) -> bool:
    return any(path.is_relative_to(REPO_ROOT / root) for root in COMPOSITION_ROOTS)


def test_the_scan_is_not_empty() -> None:
    """防止扫描范围漂了之后「没有违规」变成假通过。"""
    files = _scanned_files()
    assert len(files) >= 40, (
        f"只扫到 {len(files)} 个模块 —— 扫描范围可能已失效（目录改名？）"
    )
    under_home = [p for p in files if p.is_relative_to(VENDOR_HOME)]
    assert len(under_home) >= 8, (
        f"``{VENDOR_HOME.relative_to(REPO_ROOT)}`` 下只扫到 {len(under_home)} 个模块。"
        "豁免范围缩水时「没有违规」同样不可信。"
    )


def test_the_vendor_list_covers_what_adapters_actually_use() -> None:
    """反缩水对照：``VENDOR_MODULES`` 少写一个真在用的厂商，这条就红。"""
    used: set[str] = set()
    for path in _scanned_files():
        if not path.is_relative_to(VENDOR_HOME):
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        used |= _imported_roots(tree)
    missing = sorted(set(MUST_BE_PRESENT) - used)
    assert not missing, (
        f"``adapters/`` 里找不到 {missing} —— 要么真的删掉了（那就从 "
        "``MUST_BE_PRESENT`` 里去掉，并说明搬到哪了），要么扫描范围坏了。"
    )
    uncovered = sorted(set(MUST_BE_PRESENT) - VENDOR_MODULES)
    assert not uncovered, (
        f"{uncovered} 真在被用，却不在 ``VENDOR_MODULES`` 里 —— "
        "这份清单被删空之后，违规扫描会以「没有违规」的样子全绿。"
    )


def test_only_adapters_import_vendors() -> None:
    """判据本体。"""
    offenders = _violations(_scanned_files())
    assert not offenders, (
        "厂商模块只能从 ``marketops/adapters/`` 里 import，实际出现在：\n  "
        + "\n  ".join(offenders)
        + "\n\n正确做法是给它一个端口：``marketops/ports/`` 定义「我需要什么」，"
        "adapter 提供「谁来做」。直接 import 会让业务代码绑死在一个提供者上，"
        "换实现时改的不是 adapter 而是调用方。"
    )


def test_the_scanner_catches_a_violation() -> None:
    """正对照：判据真的会红，而不是恒真。

    用 ``marketops/application/pipeline.py``（真实存在、且确实不在 ``adapters/``）
    当靶子 —— 不想象一个路径，免得正对照测的是一个不存在的布局。
    """
    probe = REPO_ROOT / "marketops" / "application" / "pipeline.py"
    assert probe.is_file(), "正对照的靶子不见了，这条测试已经失去意义"
    tree = ast.parse("import httpx\n")
    assert _imported_roots(tree) & VENDOR_MODULES == {"httpx"}
    assert not probe.is_relative_to(VENDOR_HOME)


def test_the_scanner_accepts_the_adapter_itself() -> None:
    """正对照的另一半：判据不能**什么都拒**（否则它是恒真的反面）。

    ``http.py`` 真的 import 了 ``httpx``，而它**必须**被放行 —— 否则「厂商只准
    从 adapters/ import」这句话就变成了「谁也不准用厂商」，两个都成立的话，
    这条判据就把自己取消了。
    """
    assert list(VENDOR_HOME.rglob("http.py")), "adapters/http.py 不见了"
    assert _imported_roots(
        ast.parse((VENDOR_HOME / "http.py").read_text(encoding="utf-8"))
    ) & VENDOR_MODULES == {"httpx"}


def test_the_domains_own_dependencies_are_not_vendors() -> None:
    """``pydantic`` 是领域类型的建模工具，不是厂商 —— 禁掉它会把领域层一起禁掉。"""
    tree = ast.parse("from pydantic import BaseModel\nimport yaml\n")
    assert _imported_roots(tree) & VENDOR_MODULES == set()


# --------------------------------------------------------------------------
# 组合根规则：只有 ``interfaces/`` 与 ``adapters/`` 自己可以点名具体厂商 adapter
# --------------------------------------------------------------------------


def test_the_vendor_adapter_set_does_not_shrink() -> None:
    """反缩水对照：闭包算出来的集合少了谁，这条就红。

    一个可以被删空的集合会把主判据变成恒真 —— 与 ``MUST_BE_PRESENT`` 同一个理由。
    """
    closure = _vendor_adapter_closure()
    missing = sorted(set(MUST_BE_VENDOR_ADAPTERS) - closure.keys())
    assert not missing, (
        f"闭包里找不到 {missing} —— 要么它们真的不碰厂商了（那就从 "
        "``MUST_BE_VENDOR_ADAPTERS`` 里去掉并说明为什么），要么闭包算错了。"
    )


def test_the_closure_finds_what_a_direct_scan_misses() -> None:
    """判据必须是**传递闭包**，不是直接 import 扫描 —— 这条是那句话的执行器。

    ``sqlite/evidence_store.py`` 与 ``sqlite/ledger.py`` 自己一行厂商都不 import，
    但它们 ``from .db import ...``，``sqlite3`` 在 ``db.py`` 里。只判直接 import 的
    版本会把它们判成干净的，且**不报错** —— 主判据会以「没有违规」的样子全绿。

    所以这里同时断两件事：它们在闭包里，且不在直接集合里。只断前者的话，
    将来有人把 ``sqlite3`` 挪进 ``evidence_store.py``，这条测试照样绿，
    而它守的东西（"闭包真的在做事"）已经消失了。
    """
    closure = _vendor_adapter_closure()
    direct = {name for name, chain in closure.items() if len(chain) == 2}
    for name in MUST_BE_FOUND_ONLY_BY_CLOSURE:
        assert name in closure, f"{name} 不在闭包里 —— 它 ``from .db import ...``，链条断了"
        assert name not in direct, (
            f"{name} 现在自己 import 厂商了（链长 {len(closure[name])}）。这条对照已经"
            "失去意义：请换一个仍需靠闭包才推得出来的模块，或说明闭包不再必要。"
        )
        assert closure[name][-1] in VENDOR_MODULES, (
            f"{name} 的见证链 {closure[name]} 没有终止在一个厂商模块上"
        )


def test_the_criterion_sees_the_composition_roots() -> None:
    """证明扫描**看见了东西**，而不是把「没看见」报成「没问题」。

    ``interfaces/cli.py`` / ``interfaces/composition.py`` /
    ``interfaces/mcp/__main__.py`` / ``interfaces/research_worker.py`` 真的在点名具体
    厂商 adapter。如果它们不出现在这里，那说明解析器坏了 —— 而一个坏掉的解析器会让
    主判据永远全绿。

    ``mcp/__main__.py`` 是 Phase C 新增的第三个组合根：它 ``from
    marketops.adapters.mcp_server import build_server``。它**只**点名本仓的
    adapter —— 官方 MCP SDK 由那个 adapter 自己去 import，见
    ``marketops/adapters/mcp_server/__init__.py``。这条清单因此同时是那件事的
    执行器：哪天 SDK 的 import 从 adapter 挪回入口层，上面那条判据会当场红。

    ``research_worker.py`` 是 **Stage B** 新增的第四个：它 ``from
    marketops.adapters.agent_runtime import run_delegate``。这个文件**曾经**自己
    ``import subprocess``，被上面那条判据报了出来 —— 于是那处绑定搬进
    ``adapters/agent_runtime.py``，与 Phase C 那次是同一个动作。这条清单是那件事的
    执行器：哪天 ``subprocess`` 回到入口层，它会当场红。

    ``controller.py`` 是 **§B21** 新增的第五个：它 ``from
    marketops.adapters.sqlite.jobs import SqliteJobStore`` 与 ``from
    marketops.adapters.sqlite.wakeup import SqliteWakeupRegistry``。它为什么必须是
    组合根而不是 ``application/`` 里的一段，写在那个模块的 docstring（进程模型、
    信号、间隔是**这个部署**的性质）。而它**不** import
    ``adapters/mcp_server/``：控制器要能在没装 MCP SDK 的部署里跑起来，所以入口层
    对 SDK 的那处分界在它这里也成立。

    ``trigger_driver.py`` 是 **§B25** 新增的第六个：它 ``from
    marketops.adapters.sqlite.ledger import SqliteBudgetLedger``（见证链
    ``-> adapters.sqlite.db -> sqlite3``）与 ``from
    marketops.adapters.mcp_server.schedule_action import ResearchScheduleAction``
    （见证链 ``-> mcp``）。它为什么必须是组合根而不是 ``application/`` 里的一段，
    写在那个模块的 docstring：``application/scheduling.py`` 那一格是 ``tick``，
    而这一格是**它的入口** —— 进程模型（一次醒来、不带定时器、节奏由外部 cron /
    Hosting / ServerOS 持有）是**这个部署**的性质。它与 ``controller.py`` 有一处
    **不同而且是有意的**：它**确实**点名了 ``adapters/mcp_server/``。那不是把 SDK
    的 import 挪到了入口层 —— 上面那条见证链逐字显示 ``mcp`` 仍然在
    ``adapters/mcp_server/schedule_action.py`` 里，与本文件第一段要守的那条分界
    逐字同一条；点名的理由是「一次受理」那一趟的实现本身就住在适配层
    （``adapters/mcp_server/runs.py::_preamble``），入口层点它，与
    ``mcp/__main__.py`` 点 ``build_server`` 是同一个动作。

    ``dispatch_runner.py`` 是第七个：它 ``from marketops.adapters.local_board import
    LocalBoardDispatcher``（见证链 ``-> adapters.sqlite.db -> sqlite3``）、
    ``adapters/dispatch_outcomes`` 与 ``adapters.sqlite.jobs``。与 ``controller.py``
    同一条理由 —— 什么时候醒、跑几轮、间隔多少是**这个部署**的性质，不是业务规则；
    而它读的是与派发完全同一张板，所以那处点名不能搬到 ``application/`` 里，
    否则「谁在领」会有两个答案。
    """
    sites = _vendor_adapter_import_sites(_scanned_files(), _vendor_adapter_closure())
    named = {path.relative_to(REPO_ROOT).as_posix() for path, _ in sites}
    assert "marketops/interfaces/cli.py" in named, (
        "扫描没看见 interfaces/cli.py 点名 adapter —— 解析器坏了，主判据是假绿"
    )
    assert "marketops/interfaces/composition.py" in named
    assert "marketops/interfaces/mcp/__main__.py" in named
    assert "marketops/interfaces/research_worker.py" in named
    assert "marketops/interfaces/controller.py" in named
    assert "marketops/interfaces/trigger_driver.py" in named
    assert "marketops/interfaces/dispatch_runner.py" in named
    outside = {name for name in named if not name.startswith("marketops/adapters/")}
    assert outside == {
        "marketops/interfaces/cli.py",
        "marketops/interfaces/composition.py",
        "marketops/interfaces/controller.py",
        "marketops/interfaces/dispatch_runner.py",
        "marketops/interfaces/mcp/__main__.py",
        "marketops/interfaces/research_worker.py",
        "marketops/interfaces/trigger_driver.py",
    }, f"``adapters/`` 之外的点名位置变了：{sorted(outside)}"


def test_only_composition_roots_name_vendor_adapters() -> None:
    """判据本体：``adapters/`` 与 ``interfaces/`` 之外，谁都不准点名厂商 adapter。"""
    vendor_adapters = _vendor_adapter_closure()
    offenders = [
        (path, name)
        for path, name in _vendor_adapter_import_sites(_scanned_files(), vendor_adapters)
        if not path.is_relative_to(VENDOR_HOME) and not _is_composition_root(path)
    ]
    assert not offenders, (
        "只有 ``interfaces/`` 与 ``adapters/`` 自己可以点名具体厂商 adapter，"
        "实际出现在：\n  "
        + "\n  ".join(
            f"{path.relative_to(REPO_ROOT).as_posix()} 点名了 {name}"
            f"（见证链：{' -> '.join(vendor_adapters[name])}）"
            for path, name in offenders
        )
        + "\n\n正确做法是只 import 端口（``marketops/ports/``），由组合根回答「谁来给」。"
    )


def test_a_module_outside_the_roots_would_be_caught() -> None:
    """正对照：同一行 import 落在业务层，判据必须吐出来。

    打的是 ``_names_vendor_adapters`` —— **主判据用的那个解析器**。只测 ``ast``
    的正对照在判据恒假时仍然会绿，等于没测。
    """
    vendor_adapters = _vendor_adapter_closure()
    source = "from ..adapters.serper import SerperSearchProvider\n"
    caught = _names_vendor_adapters(
        source, module_name="marketops.application.pipeline", vendor_adapters=vendor_adapters
    )
    assert caught == ["marketops.adapters.serper"], (
        f"业务层里点名 ``serper`` 没被认出来（认出来的是 {caught}）"
    )
    assert not _is_composition_root(REPO_ROOT / "marketops" / "application" / "pipeline.py")


def test_the_composition_roots_are_allowed_the_same_import() -> None:
    """反面：同一行 import 落在组合根里是**对的** —— 否则这条判据只是「谁也不准用」。

    两条一起才说明判据测的是**位置**，不是「点名」这个动作本身。
    """
    vendor_adapters = _vendor_adapter_closure()
    source = "from ..adapters.serper import SerperSearchProvider\n"
    caught = _names_vendor_adapters(
        source, module_name="marketops.interfaces.cli", vendor_adapters=vendor_adapters
    )
    assert caught == ["marketops.adapters.serper"]
    assert _is_composition_root(REPO_ROOT / "marketops" / "interfaces" / "cli.py")
    # 同一份源码、同一个解析器，只有「落在哪个包」不同 —— 判据分辨的就是这个。
    assert not _is_composition_root(REPO_ROOT / "marketops" / "adapters" / "serper.py")
    assert _is_composition_root(REPO_ROOT / "marketops" / "interfaces" / "composition.py")


def test_from_package_import_submodule_is_not_a_way_around() -> None:
    """``from marketops.adapters import serper`` 与 ``from ...serper import X`` 同一件事。

    只记 ``ImportFrom`` 的 ``base`` 会漏掉前一种写法，而它是完全正常的 Python ——
    上一条判据会从它底下钻过去。
    """
    vendor_adapters = _vendor_adapter_closure()
    for source in (
        "from marketops.adapters import serper\n",
        "from marketops.adapters.serper import SerperSearchProvider\n",
    ):
        assert (
            _names_vendor_adapters(
                source, module_name="marketops.application.pipeline", vendor_adapters=vendor_adapters
            )
            == ["marketops.adapters.serper"]
        ), source
