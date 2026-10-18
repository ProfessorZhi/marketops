"""领域层必须保持纯净 —— 这条纪律由本测试执行，不由文档自觉。

分层的价值全在「违规会被拦住」。一份写着「domain 不得 import I/O」的文档，
在有人 import 了 httpx 的那个提交里不会有任何反应。

## 两条判据，缺一不可

1. **不得触碰外界** —— 网络、浏览器、协议 SDK、决策 provider、存储、文件系统、
   进程、配置序列化、飞书。见 ``FORBIDDEN_MODULES``。
2. **不得依赖领域之外** —— 连本仓库的其他包也不许；领域层只能 import 自己。

第 2 条曾经是一条**白名单**（「除 util 之外都不许」），而不是「什么都不许」。
那时 ``marketops/util.py`` 是纯函数与文件助手的混合体，只能整块放行。把
``sha256_file`` 拆去 ``marketops/adapters/hashing.py`` 之后，白名单没有存在的
理由了 —— 而它本来是唯一一处「领域可以出去」的口子。

## 这条测试不证明什么

它做的是**直接 import** 的静态扫描，所以拦不住「domain 调用了 domain 里的
某个函数，而那个函数内部做了 I/O」。第 2 条判据把这条路收得很窄（被调的函数
必须住在 domain 内，因此它自己也要过第 1 条），但没堵死。本测试不声称覆盖它。
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
DOMAIN_ROOT = REPO_ROOT / "marketops" / "domain"

#: 领域层不得直接依赖的任何东西。左侧是需求逐条点名的，右侧是同一类里的其余成员
#: —— 一个能 import `requests` 的「纯」层，与能 import `httpx` 的没有区别。
#:
#: **条目按点分全名匹配**（见 :func:`_forbidden`），这不是格式上的讲究。
#: ``urllib`` 是一个**包**：``urllib.request`` 会开套接字，而 ``urllib.parse`` 是纯
#: 字符串解析 —— 与 ``re`` 同类，与「外界」无关。把整个包写成一个条目时，第一个
#: 需要在领域层看一眼 URL 的人只有两条路：手写一个 URL 解析器（而手写的那一个会在
#: ``http://[::1]:8080/x`` 这种形状上悄悄错，且错法看不出来），或者去改这张清单。
#: 所以这里禁的是那个**会开套接字**的子模块；``urllib.parse`` 的放行由
#: :func:`test_a_pure_parser_inside_a_banned_package_is_still_allowed` 单独钉住。
#:
#: 同一条推理**不**适用于 ``"http"``：它保持笼统是有意的。拆开一个今天没有任何领域
#: 模块需要的口子，等于按一份尚未写出的实现来裁剪边界 —— 而那份实现长什么样还没人
#: 知道。（端口那份清单里的 ``"urllib"`` 用的正是这一条理由，方向相反。）
FORBIDDEN_MODULES: frozenset[str] = frozenset(
    {
        # HTTP / 网络
        "httpx", "requests", "urllib.request", "urllib.robotparser", "urllib3",
        "socket", "aiohttp", "http",
        # 浏览器与采集。反检测那一族（`patchright`、`playwright_stealth`）**不在**
        # 本清单里：`tests/test_no_evasion.py` 已对整个 `marketops/` 禁掉它们，
        # 比本测试更宽；在这里重列只会撞上那条测试的行为针
        # （`stealth` 是它的针之一，而它只豁免 `#` 开头的行）。
        "crawl4ai", "playwright", "patchright", "selenium",
        # 协议与 SDK
        "mcp", "fastmcp", "fastapi", "starlette", "uvicorn",
        # 决策 provider
        "typesafe_sdk", "jev", "laya", "torch", "transformers", "onnxruntime",
        "openai", "anthropic", "google",
        # 存储
        "sqlite3", "psycopg", "psycopg2", "redis", "pymongo",
        # 文件系统 / 进程
        "os", "pathlib", "shutil", "tempfile", "io", "subprocess", "signal",
        # 序列化配置（读取即 I/O）
        "yaml", "toml", "configparser",
        # 飞书
        "lark_oapi", "feishu",
    }
)

#: 领域层唯一允许依赖的**包前缀**。不是一份白名单，而是一条边界：
#: 所有相对 import 解析之后都必须落在 ``marketops.domain`` 里面。
DOMAIN_PACKAGE = "marketops.domain"


def _domain_files() -> list[Path]:
    return sorted(p for p in DOMAIN_ROOT.rglob("*.py") if "__pycache__" not in p.parts)


def _absolute_imports(tree: ast.AST) -> set[str]:
    """绝对 import 的**点分全名**。

    截成第一段看着更省事，代价是 ``urllib.parse`` 与 ``urllib.request`` 坍成同一个
    名字 —— 于是这张清单**表达不出**「禁那个会开套接字的、放这个纯解析的」，而它想
    表达的意思只有这一条路可走。截断还有个更小的代价：违规报告里那个名字会变粗，
    看的人分不出踩线的是哪个子模块（``http.client`` 与 ``http.server`` 都报成
    ``http``），而「是哪一个」正是决定该改代码还是该改清单的那一条信息。
    """
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                names.add(alias.name)
        elif isinstance(node, ast.ImportFrom):
            # level > 0 是相对 import，由 _relative_imports 处理。
            if node.level == 0 and node.module:
                names.add(node.module)
    return names


def _forbidden(name: str) -> str | None:
    """命中的禁用条目，或 ``None``。**从长到短按点分前缀匹配。**

    从长到短是为了让 ``urllib.request`` 命中它自己那一条（条目是有意枚举的，
    越长越具体），而短条目（``os``）因此仍然盖得住 ``os.path`` —— 一条既精确
    又不漏的规则，比两条各管一半的规则难写错。
    """
    parts = name.split(".")
    for cut in range(len(parts), 0, -1):
        candidate = ".".join(parts[:cut])
        if candidate in FORBIDDEN_MODULES:
            return candidate
    return None


def _relative_targets(tree: ast.AST, module_path: Path) -> set[str]:
    """把相对 import 解析成 ``marketops.x.y`` 形式的绝对名。

    相对 import 的 ``level`` 是相对**当前模块所在的包**，不是相对仓库根 ——
    所以基址要把模块名本身去掉。``from .util import x`` 写在
    ``marketops/domain/temporal.py`` 里，指的是 ``marketops.domain.util``。
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


def test_domain_package_is_not_empty() -> None:
    """防止扫描范围漂了之后「没有违规」变成假通过。"""
    files = _domain_files()
    assert len(files) >= 1, f"{DOMAIN_ROOT} 下没有可扫描的模块 —— 扫描范围可能已失效"


@pytest.mark.parametrize("path", _domain_files(), ids=lambda p: p.name)
def test_domain_imports_nothing_impure(path: Path) -> None:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    hit = sorted(n for n in map(_forbidden, _absolute_imports(tree)) if n)
    assert not hit, (
        f"{path.relative_to(REPO_ROOT)} 依赖了领域层不得触碰的模块: {hit}。"
        "领域层要么把这件事表达成一个端口（ports/），要么把实现挪到 adapters/。"
    )


@pytest.mark.parametrize("path", _domain_files(), ids=lambda p: p.name)
def test_domain_depends_on_nothing_outside_itself(path: Path) -> None:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    outside = sorted(
        target
        for target in _relative_targets(tree, path)
        if not target.startswith(DOMAIN_PACKAGE)
    )
    assert not outside, (
        f"{path.relative_to(REPO_ROOT)} 依赖了 {outside}。"
        f"领域层只能 import {DOMAIN_PACKAGE} 里的东西。"
        "需要外面的能力时，先在 ports/ 定义它、在 adapters/ 实现它，"
        "由调用方注入 —— 直接 import 会把领域层重新钉死在某个实现上。"
    )


def test_the_scanner_catches_a_violation() -> None:
    """正对照：证明上面的判据真的会红，而不是恒真。

    走 :func:`_forbidden`，并且带一个**点分**的样例（``http.client`` 由笼统条目
    ``"http"`` 盖住）。原先这里取的是集合交集 —— 那是旧机制，一条测着旧机制的
    正对照在机制坏掉时照绿，而它绿的正是「判据活着」那句话。
    """
    tree = ast.parse("import httpx\nfrom sqlite3 import connect\nimport http.client\n")
    assert sorted(n for n in map(_forbidden, _absolute_imports(tree)) if n) == [
        "http",
        "httpx",
        "sqlite3",
    ]


def test_a_pure_parser_inside_a_banned_package_is_still_allowed() -> None:
    """放行必须是**窄**的：同一个包里的纯解析模块过、开套接字的不过。

    这条判据是 ``FORBIDDEN_MODULES`` 里那两个点分条目的钉子。少了它，把
    ``"urllib.request"`` 改回 ``"urllib"``（或反过来，把 ``_forbidden`` 改成永不命中）
    两种相反的错**都不会有任何判据转红** —— 一个判据只证明「违规会红」，
    证明不了「放行的那个恰好是该放行的那个」。
    """
    pure = ast.parse("from urllib.parse import urlsplit\n")
    assert [_forbidden(n) for n in _absolute_imports(pure)] == [None], (
        "urllib.parse 不与外界接触，与 re 同类；拦下它只会让人手写一个 URL 解析器"
        "（而手写的那个在 http://[::1]:8080/x 上会悄悄错）"
    )
    for source in (
        "import urllib.request\n",
        "from urllib.request import urlopen\n",
    ):
        tree = ast.parse(source)
        assert [_forbidden(n) for n in _absolute_imports(tree)] == ["urllib.request"], (
            f"{source!r} 开了套接字，必须被拦下"
        )


def test_the_relative_resolver_catches_a_violation(tmp_path: Path) -> None:
    """正对照：相对 import 的解析不能是空操作。"""
    probe = DOMAIN_ROOT / "sub" / "probe.py"
    tree = ast.parse("from ...adapters import serper\n")
    targets = _relative_targets(tree, probe)
    assert "marketops.adapters" in targets, targets
