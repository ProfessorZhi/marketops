"""「登记不产生网络调用」是一处**构造**，不是一句保证。

B01 的第四条关闭条件逐字（``docs/MEMBER_A_AUTOMATION_EXECUTION_PLAN.md:104``）：

> ProductLine/产品资料登记不产生网络调用

这句话的主语是**三个**并列的东西（ProductLine 的登记、产品资料的登记），而谓语是
一个**否定**。否定句不能靠「我们不调用」来兑现 —— 一处「不会发生」如果只是自觉，
它在第一万次修改里失效的方式是**静默**的：登记面某天为了「顺手校验一下官网」import
了搜索端口，行为上仍然正确（校验真的成功了），只是「登记」从此会花钱、会联网、会在
断网时失败，而没有任何一条判据会响。

所以这一条判的是**模块拿不拿得到网络能力**，不是它拿不到的时候表现如何。

## 为什么是闭包，不是这个文件自己的 import

``marketops.adapters.mcp_server.research`` 就在同一个目录里，它 import 了
``ports.search`` 与 ``ports.acquisition`` —— 那是它存在的**全部理由**。所以
「``adapters/mcp_server/`` 不许碰网络」这句话是**假的**，按目录判会当场假红。

同理，登记面也不会自己去 import 搜索端口。真正会发生的形态是**间接**的：登记面
import 了一个业务模块，那个模块再 import 搜索端口。只判直接 import 的写法会漏掉
链长大于 2 的那些，且漏得**静默** —— 判据以「没有违规」的样子全绿。
``test_the_scanner_catches_an_indirect_violation`` 是对照。

## 禁用清单为什么不是 ``VENDOR_MODULES``

``tests/test_adapters_hold_the_vendors.py`` 已经有一份很全的外部提供者清单，但它的
标准是「**外部**提供者」，不是「网络」。两份清单的粒度不同，合并会让其中一条失去
意义（那句话在那个文件的 ``:34-36`` 就写着）。具体的两处分歧：

* ``sqlite3`` 在那份清单里，而**登记必须能落盘** —— 拿它当禁用清单，
  ``SqliteProductCatalog`` 的闭包会当场假红。
* ``mcp`` 在那份清单里，而**登记面的 handler 必须能 import 它** ——
  既有 handler 都要抛 ``mcp.server.mcpserver.exceptions.ToolError``
  （``adapters/mcp_server/outreach.py:83``）。

所以这一份清单只收「真的会对外发起请求、或起一个外部进程」的那些。两个网络**端口**
按全名判（``ports.search`` / ``ports.acquisition``），厂商 SDK 按**顶层名**判 ——
口径不同是对的，理由与 ``_vendor_adapter_closure`` 那一处逐字相同：厂商是一个包名
（``from httpx import Client`` 碰的就是 httpx），而端口是本仓里一个具体的模块。

## 内存替身为什么不在这份清单里

``InMemoryProductCatalog`` 住在 ``adapters/memory.py``，而那份文件是 14 个内存替身的
集合。把它列进登记面，等于宣判「``memory.py`` 永远不许实现搜索端口的内存替身」——
而那是一件研究面迟早会想做的事。替身构造上不持有任何 I/O（它连文件都不碰），所以
「它会联网」这句话在物理上就不成立；判它没有信息量。

**已知的这一条判不到什么**：它判不出「登记面调用了一个外部地址但用的不是上面这些
名字」（比如自己拼一个 ``os.system``、或者经 ``ctypes``）。这是白名单判据的共同上限，
写在 ``test_the_forbidden_set_does_not_shrink`` 旁边，不假装它是完备的。
"""

from __future__ import annotations

import ast
from collections.abc import Collection, Mapping
from pathlib import Path

import pytest

from tests.test_adapters_hold_the_vendors import (
    _imported_modules,
    _module_name,
    _package_name,
    _scanned_files,
)

REPO_ROOT = Path(__file__).resolve().parents[1]

#: 登记这条**生产路径**上的每一个仓库内模块。
#:
#: 内存替身不在内，理由见模块 docstring。列表里每一项都必须在盘上 ——
#: ``test_the_registration_surface_is_on_disk`` 钉住这件事，否则清单缩水到空
#: 会让下面每一条判据一起变成恒真。
REGISTRATION_SURFACE: tuple[str, ...] = (
    "marketops/adapters/mcp_server/product_catalog.py",
    "marketops/adapters/sqlite/product_catalog.py",
)

#: 网络能力在本仓的两个入口。按**全名**匹配。
NETWORK_PORTS: frozenset[str] = frozenset(
    {
        "marketops.ports.search",
        "marketops.ports.acquisition",
    }
)

#: 会对外发请求、或起一个外部进程的厂商 SDK。按**顶层名**匹配。
#:
#: ``subprocess`` 在这里的理由逐字抄自 ``VENDOR_MODULES`` 的那一段：它是标准库，
#: 也是「怎么够到一个外部提供者」那条路本身。
NETWORK_SDKS: frozenset[str] = frozenset(
    {
        # 传输
        "httpx", "requests", "aiohttp", "urllib3", "httpcore", "socket",
        # 浏览器与采集
        "crawl4ai", "playwright", "patchright", "selenium", "pyppeteer",
        # 起外部进程
        "subprocess",
        # 外部 API 的 SDK：决策 provider 与飞书
        "jev", "laya", "openai", "anthropic", "typesafe_sdk", "lark_oapi", "feishu",
    }
)

#: 只针对**点分全名**的几条 —— ``urllib`` 整包收进来会误伤 ``urllib.parse``，
#: 而那是纯字符串处理，不是网络。
NETWORK_DOTTED: frozenset[str] = frozenset({"urllib.request", "http.client"})

#: 登记面**必须**持有的那一个端口。少了它，「登记面」这个说法就没有依据 ——
#: 一个什么都不碰的模块当然不联网。见 ``test_the_registration_surface_holds_the_catalog_port``。
CATALOG_PORT = "marketops.ports.product_catalog"


def _network_names(imported: set[str]) -> set[str]:
    """``imported`` 里哪些名字算「网络能力」。

    两个口径合起来用：端口按全名，厂商 SDK 按顶层名，另加几条点分全名。
    ``urllib.request`` 会同时以 ``urllib``（顶层）与 ``urllib.request``（全名）出现，
    只取全名那一格，所以 ``urllib`` 不在 ``NETWORK_SDKS`` 里。
    """
    by_root = {name for name in imported if name.split(".")[0] in NETWORK_SDKS}
    return by_root | {name for name in imported if name in NETWORK_DOTTED} | (imported & NETWORK_PORTS)


def _network_reach(
    sources: Mapping[str, str],
    *,
    packages: Collection[str],
    start: Collection[str],
) -> list[tuple[str, ...]]:
    """``起点模块 → 一条通往网络能力的见证链``。**判据的本体。**

    抽成一个吃 ``{模块名: 源码}`` 的纯函数（而不是藏在走目录的循环里），是为了让
    正对照打的是**判据**而不是解析器：喂一份假的模块图进去，含违规的那一份必须
    吐出来。一条只测 ``ast`` 的正对照，在判据恒假时仍然会绿。

    ``packages`` 是其中哪些名字是包 —— 相对 import 的基准。少一格会把每一个相对
    import 都解析到隔壁，而解析错的后果是判据**静默**失效，不是报错。

    链的最后一节是网络能力的名字，前面每一节都是仓库内的一次 import。判错时能看出
    是哪一步算错了，理由与 ``_vendor_adapter_closure`` 逐字相同。
    """
    imports: dict[str, set[str]] = {}
    for name, source in sources.items():
        package = name if name in packages else name.rpartition(".")[0]
        tree = ast.parse(source, filename=name)
        imports[name] = _imported_modules(tree, package=package)

    # 直接命中：这一节自己 import 了网络能力。
    witness: dict[str, tuple[str, ...]] = {}
    for name in sorted(imports):
        hits = sorted(_network_names(imports[name]))
        if hits:
            witness[name] = (name, hits[0])

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

    return [witness[name] for name in sorted(start) if name in witness]


def _on_disk() -> tuple[dict[str, str], set[str]]:
    """把真仓库装进 ``_network_reach`` 要的那份映射。"""
    sources: dict[str, str] = {}
    packages: set[str] = set()
    for path in _scanned_files():
        sources[_module_name(path)] = path.read_text(encoding="utf-8")
        if path.name == "__init__.py":
            packages.add(_module_name(path))
    return sources, packages


def test_the_registration_surface_is_on_disk() -> None:
    """清单里的每一项都必须真的在盘上。

    没有这一条，清单里写错一个路径就会被读成「那个模块很干净」—— 一个不存在的
    模块当然不 import 任何东西。判据在这里**红**，而不是静默放行。
    """
    missing = [rel for rel in REGISTRATION_SURFACE if not (REPO_ROOT / rel).is_file()]
    assert not missing, (
        f"登记面清单点名的模块不在盘上：{missing}。"
        "要么路径写错了，要么这一片还没落 —— 两种都要在这里显形。"
    )


def test_the_registration_surface_never_reaches_the_network() -> None:
    """本体：登记面到不了任何网络能力，直接或间接都到不了。"""
    sources, packages = _on_disk()
    start = {_module_name(REPO_ROOT / rel) for rel in REGISTRATION_SURFACE}
    # 前置：起点必须真的在模块图里。少了这一句，路径写错会让 `_network_reach`
    # 拿到一个它不认识的起点**静默**返回空 —— 「没有违规」于是变成「没在判」。
    assert start <= set(sources), (
        f"登记面清单解析出的模块名不在仓库模块图里：{sorted(start - set(sources))}"
    )
    chains = _network_reach(sources, packages=packages, start=start)
    assert not chains, (
        "登记面能拿到网络能力，路径如下（最后一节是网络能力的名字）：\n"
        + "\n".join("  → ".join(chain) for chain in chains)
        + "\nB01 :104 逐字要求「ProductLine/产品资料登记不产生网络调用」。"
    )


def test_the_registration_surface_holds_the_catalog_port() -> None:
    """反面：登记面**必须**真的在登记东西。

    少了这一条，「网络不可达」可以靠一个什么都不做的空壳达成 —— 而那一条判据会
    全绿。这里要求它直接持有产品目录端口，于是「不联网」与「真的登记」两件事
    同时被钉住。
    """
    sources, _ = _on_disk()
    holders: list[str] = []
    for rel in REGISTRATION_SURFACE:
        path = REPO_ROOT / rel
        name = _module_name(path)
        if name not in sources:
            continue
        tree = ast.parse(sources[name], filename=name)
        # 每一份源码用它**自己**的包名解析相对 import —— 用清单里第一项的包名
        # 去解析第二项，会把 `..` 减到隔壁目录上，而解析错的表现是判据静默失效。
        if CATALOG_PORT in _imported_modules(tree, package=_package_name(path)):
            holders.append(name)
    assert holders, (
        f"登记面没有任何一个模块点名 {CATALOG_PORT}。"
        "一个不碰产品目录的登记面当然不联网 —— 那不叫「不产生网络调用」，那叫没做事。"
    )


def test_the_forbidden_set_does_not_shrink() -> None:
    """反缩水：禁用清单本身也要有判据。

    ``NETWORK_SDKS`` 是一个可以被删掉的名字。删掉几个之后上面那条判据立刻全绿，
    而那些 SDK 该有的地方照旧在跑 —— 「没有违规」变成「清单缩水」的假象。

    **这一条不假装判据完备**：它判不出「用了一个不在清单里的方式联网」。
    那是白名单判据的共同上限，写在这里而不是留给读者自己发现。
    """
    assert NETWORK_PORTS == {"marketops.ports.search", "marketops.ports.acquisition"}, (
        "网络端口清单缩水了。B01 的登记面要防的正是这两个。"
    )
    #: 每一条分组都必须有人。分组整体被删掉与清单缩小是同一件事。
    for required in ("httpx", "crawl4ai", "socket", "subprocess"):
        assert required in NETWORK_SDKS, f"{required} 从网络 SDK 清单里掉了"
    assert "urllib.request" in NETWORK_DOTTED
    #: 这三样**必须不在**清单里 —— 登记面自己要用它们（理由见模块 docstring）。
    for allowed in ("sqlite3", "mcp", "pydantic"):
        assert allowed not in NETWORK_SDKS, (
            f"{allowed} 出现在了网络清单里 —— 登记面必须能 import 它，这会让判据假红"
        )


def test_the_scanner_catches_a_direct_violation() -> None:
    """正对照：登记面自己 import 搜索端口，必须被抓。"""
    chains = _network_reach(
        {
            "marketops.adapters.mcp_server.product_catalog": (
                "from ...ports.search import SearchProvider\n"
            ),
        },
        packages=(),
        start=("marketops.adapters.mcp_server.product_catalog",),
    )
    assert chains == [
        (
            "marketops.adapters.mcp_server.product_catalog",
            "marketops.ports.search",
        )
    ], f"直接的违规没被抓到：{chains}"


def test_the_scanner_catches_an_indirect_violation() -> None:
    """正对照：经一个中间模块绕进去，必须**同样**被抓。"""
    chains = _network_reach(
        {
            "marketops.adapters.mcp_server.product_catalog": (
                "from ...application import product\n"
            ),
            "marketops.application.product": "from ..ports.acquisition import Fetcher\n",
        },
        packages=(),
        start=("marketops.adapters.mcp_server.product_catalog",),
    )
    assert chains == [
        (
            "marketops.adapters.mcp_server.product_catalog",
            "marketops.application.product",
            "marketops.ports.acquisition",
        )
    ], f"间接的违规没被抓到：{chains}"


def test_the_scanner_accepts_a_clean_surface() -> None:
    """反转对照：真的干净的那一份不许报红。

    少了它，一条「见到什么都说违规」的判据也能通过上面两条正对照。
    """
    chains = _network_reach(
        {
            "marketops.adapters.mcp_server.product_catalog": (
                "from mcp.server.mcpserver.exceptions import ToolError\n"
                "from ...ports.product_catalog import ProductCatalog\n"
                "from .tools import Handler\n"
            ),
        },
        packages=(),
        start=("marketops.adapters.mcp_server.product_catalog",),
    )
    assert chains == [], f"干净的那一份被误判了：{chains}"


def test_the_two_network_ports_are_not_in_the_clean_surface_by_accident() -> None:
    """反向读一遍禁用清单：端口必须在**闭包**里判得到，而不是靠名字巧合。

    这两个名字在同一份源码里出现时必须是违规的 —— 包括 ``from ...ports import
    search`` 这种「先 import 包再取子模块」的写法，它绑定的东西与
    ``from ...ports.search import X`` 是同一个。
    """
    for source in (
        "from ...ports import search\n",
        "from ...ports.search import SearchProvider\n",
        "from ...ports import acquisition\n",
        "import marketops.ports.acquisition\n",
    ):
        chains = _network_reach(
            {"marketops.adapters.mcp_server.product_catalog": source},
            packages=(),
            start=("marketops.adapters.mcp_server.product_catalog",),
        )
        assert len(chains) == 1, f"这一行没被判成违规：{source!r} → {chains}"


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-q"]))
