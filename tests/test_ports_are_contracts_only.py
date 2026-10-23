"""端口必须是**契约**，不能是任何一个实现 —— 由本测试执行。

`tests/test_domain_is_pure.py` 守的是领域层「不得触碰外界」。「端口不得被实现
污染」是**另一条**判据，而且它守的是一个不同的失败：

    一个 import 了 httpx 的端口看起来完全正常 —— 它照样能被 ABC 描述、
    照样能被注入、``isinstance`` 照样通过。它只是**偷偷变成了唯一实现**
    （retry 策略、超时语义、连接池都写进了契约层），
    于是第二个实现（Crawl4AI、Jev、Laya）落地时才发现这份契约是按第一个
    实现的形状长出来的。

## 三条判据

1. **不得触碰厂商** —— 网络、浏览器、协议 SDK、存储、决策 provider。见
   ``FORBIDDEN_MODULES``。
2. **仓库内只准依赖 ``marketops.domain`` 与 ``marketops.ports``** —— 端口依赖
   领域类型是正当的（``FetchResult`` 里就有 ``BlockReason``）；端口依赖
   ``marketops.adapters.serper`` 这类实现包，就是把契约挂回了某一个实现。
3. **扫描范围不为空** —— 防止扫描失效之后「没有违规」变成假通过。

## 与领域层那份清单**刻意不同**

``test_domain_is_pure.FORBIDDEN_MODULES`` 禁掉了 ``pathlib``；本测试不禁。
这不是疏漏：``FetchResult.body_path`` / ``ParsedDocument.source_path`` 描述的是
「产物落在哪里」，端口有权把它表达出来。两份清单如果合并，要么端口被无理由地
禁掉 ``pathlib``，要么领域层被放行 I/O —— 两个方向都是把一条真实的边界抹平。

第二处不同是**粒度**，而且它是有意的：本清单里的 ``"urllib"`` 仍然是一个笼统的包名，
领域层那份已经拆成了 ``"urllib.request"`` / ``"urllib.robotparser"``。理由不是「端口
可以随便用 urllib」——判据 1 在 ``_absolute_modules`` 上取第一段（``roots``），所以
这里拦的是整个包。理由是**今天没有一个端口需要解析 URL**：为了尚不存在的需求先把
放行口开好，等于把一条边界按一份尚未写出的实现来裁剪，而那份实现长什么样还没人知道。
真出现需求时照领域侧那次做：拆成点分条目、并补一条钉住「放行是窄的」的判据
（``test_a_pure_parser_inside_a_banned_package_is_still_allowed``）。

## 这一条测试**不**覆盖的东西

§2 还有另一半约束：「业务代码不得直接依赖 Serper / Jev / Laya / Crawl4AI 或任何执行器
的 API」。那是**另一条**判据 —— 本测试问的是「端口有没有被某一个实现污染」，
那一条问的是「厂商模块除了 ``adapters/`` 还有谁 import 得到」。

两者容易混为一谈，因为今天它们都指向 ``adapters/``；但一个端口即使一行 vendor
都不 import，业务层照样可以绕过它直接 ``import httpx``。那一半由
``tests/test_adapters_hold_the_vendors.py`` 执行（B4 落地）。
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
PORTS_ROOT = REPO_ROOT / "marketops" / "ports"

#: 端口不得直接依赖的任何东西。
FORBIDDEN_MODULES: frozenset[str] = frozenset(
    {
        # 网络 / HTTP
        "httpx", "requests", "urllib", "urllib3", "socket", "aiohttp", "http",
        # 浏览器与采集
        "crawl4ai", "playwright", "patchright", "selenium",
        # 协议与 SDK
        "mcp", "fastmcp", "fastapi", "starlette", "uvicorn",
        # 决策 provider
        "typesafe_sdk", "jev", "laya", "torch", "transformers", "onnxruntime",
        "openai", "anthropic", "google",
        # 存储
        "sqlite3", "psycopg", "psycopg2", "redis", "pymongo",
        # 序列化配置（读取即 I/O）
        "yaml", "toml", "configparser",
        # 飞书
        "lark_oapi", "feishu",
    }
)

#: 端口唯一允许依赖的**仓库内**包前缀。端口依赖领域类型是正当的；
#: 依赖任何实现包都是端口塌陷。
ALLOWED_REPO_PACKAGES: tuple[str, ...] = ("marketops.domain", "marketops.ports")


def _port_files() -> list[Path]:
    return sorted(p for p in PORTS_ROOT.rglob("*.py") if "__pycache__" not in p.parts)


def _absolute_modules(tree: ast.AST) -> set[str]:
    """绝对 import 的完整点分名（不截断到第一段 —— 判据 2 要看完整前缀）。"""
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                names.add(alias.name)
        elif isinstance(node, ast.ImportFrom):
            if node.level == 0 and node.module:
                names.add(node.module)
    return names


def _relative_targets(tree: ast.AST, module_path: Path) -> set[str]:
    """把相对 import 解析成 ``marketops.x.y`` 形式的绝对名。

    与 ``test_domain_is_pure`` 的同名函数是同一套算术：``level`` 相对**当前模块
    所在的包**，所以基址先去掉模块名本身。
    """
    parts = list(module_path.relative_to(REPO_ROOT).with_suffix("").parts)
    package = parts[:-1]
    targets: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.level > 0:
            base = package[: len(package) - (node.level - 1)]
            if node.module:
                base = [*base, *node.module.split(".")]
            targets.add(".".join(base))
    return targets


def test_the_port_package_is_not_empty() -> None:
    """防止扫描范围漂了之后「没有违规」变成假通过。"""
    files = _port_files()
    assert files, f"{PORTS_ROOT} 下没有可扫描的模块 —— 扫描范围可能已失效"
    assert len(files) >= 4, (
        f"只扫到 {len(files)} 个模块：{[p.name for p in files]}。"
        "端口包缩水时这条测试必须响 —— 端口被删掉不等于违规消失了。"
    )


@pytest.mark.parametrize("path", _port_files(), ids=lambda p: p.name)
def test_ports_touch_no_vendor(path: Path) -> None:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    roots = {name.split(".")[0] for name in _absolute_modules(tree)}
    hit = sorted(roots & FORBIDDEN_MODULES)
    assert not hit, (
        f"{path.relative_to(REPO_ROOT)} 依赖了端口层不得触碰的模块: {hit}。"
        "厂商实现属于 adapters/：端口里出现它，第二个实现落地时会发现这份契约"
        "是按第一个实现的形状长出来的。"
    )


def _outside_repo_packages(names: set[str]) -> list[str]:
    """仓库内、但不在 ``ALLOWED_REPO_PACKAGES`` 里的依赖名 —— 判据 2 的唯一实现。

    抽成函数是为了让**正对照**能证明真正的判据（而不是只证明解析器）会红：
    一条只测解析器的正对照，在判据本身恒假时仍然会绿。
    """
    return sorted(
        name
        for name in names
        if name.startswith("marketops.")
        and not any(
            name == allowed or name.startswith(allowed + ".")
            for allowed in ALLOWED_REPO_PACKAGES
        )
    )


@pytest.mark.parametrize("path", _port_files(), ids=lambda p: p.name)
def test_ports_depend_on_nothing_but_domain(path: Path) -> None:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    outside = _outside_repo_packages(_absolute_modules(tree) | _relative_targets(tree, path))
    assert not outside, (
        f"{path.relative_to(REPO_ROOT)} 依赖了 {outside}。"
        f"端口只能 import {ALLOWED_REPO_PACKAGES} 里的东西。"
        "端口依赖实现包，等于把契约挂回某一个实现 —— 换实现时契约也要跟着改。"
    )


def test_the_scanner_catches_a_violation() -> None:
    """正对照：证明判据 1 真的会红，而不是恒真。"""
    tree = ast.parse("import httpx\nfrom sqlite3 import connect\n")
    roots = {name.split(".")[0] for name in _absolute_modules(tree)}
    assert roots & FORBIDDEN_MODULES == {"httpx", "sqlite3"}


def test_the_repo_prefix_check_catches_a_violation() -> None:
    """正对照：判据 2 不能因为「都 startswith marketops」而恒真。"""
    tree = ast.parse("from marketops.application.grading import grade_route\n")
    assert _outside_repo_packages(_absolute_modules(tree)) == ["marketops.application.grading"]


def test_the_repo_prefix_check_accepts_what_it_should() -> None:
    """正对照的另一半：判据 2 也不能**什么都拒**（否则它是恒真的反面）。"""
    tree = ast.parse(
        "from marketops.domain.enums import Grade\n"
        "from marketops.ports.search import SearchProvider\n"
    )
    assert _outside_repo_packages(_absolute_modules(tree)) == []


def test_the_relative_resolver_catches_a_violation() -> None:
    """正对照：相对 import 的解析不能是空操作，**且它的产物真的会被拒**。

    三级点不是笔误：``marketops/ports/sub/probe.py`` 里的 ``...`` 指向
    ``marketops``，所以 ``from ...application.grading import ...`` 指的是
    **另一个非端口包** ``marketops.application.grading``。写成两级点会解析成
    ``marketops.ports.application.grading`` —— 那是个不存在的模块，而前缀判据会
    放行它，正对照就成了假绿。
    """
    probe = PORTS_ROOT / "sub" / "probe.py"
    tree = ast.parse("from ...application.grading import grade_route\n")
    targets = _relative_targets(tree, probe)
    assert "marketops.application.grading" in targets, targets
    assert _outside_repo_packages(targets) == ["marketops.application.grading"], (
        f"正对照的目标必须被前缀判据**拒绝**，实际被放行了：{targets}"
    )

