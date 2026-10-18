"""「启动授权前零外部调用」是一处**构造**，不是一句保证。

B01 第五条关闭条件逐字（``docs/MEMBER_A_AUTOMATION_EXECUTION_PLAN.md:104``）：

> 启动授权前零外部调用

八个字，谓语是一个**否定**，而主语是一个**时刻**（「启动授权前」）。两件事都要在
同一处兑现，缺一件这句话就没有内容：一次预览如果自己会往外发请求，那它就不是
「授权前」的读数；一次预览如果什么都不读，那「零外部调用」是空的。

否定句不能靠「我们不调用」来兑现 —— 一处「不会发生」如果只是自觉，它在第一万次
修改里失效的方式是**静默**的：启动预览某天为了「顺手确认一下这家厂商的官网还在不在」
import 了采集端口，行为上仍然正确（确认真的成功了），只是「预览」从此会花钱、会联网、
会在断网时失败，而没有任何一条判据会响。所以这一条判的是**模块拿不拿得到网络能力**，
不是它拿不到的时候表现如何。口径与 ``tests/test_registration_is_offline.py`` 逐字同源
（那一份是 B01 的第四条关闭条件）：真的会对外发起请求、**或起一个外部进程**。

## 为什么这一族比登记面更容易破

登记面（``product_catalog.py``）与这一族都在 ``adapters/mcp_server/`` 下，而同一个
目录里的 ``research.py`` 就 import 了 ``ports.search`` 与 ``ports.acquisition`` ——
那是它存在的全部理由。所以「``adapters/mcp_server/`` 不许碰网络」这句话是假的，按目录
判会当场假红；判据算的必须是**这一族的 import 闭包**。

而这一族有一条比登记面更近的旁路：``tools.py``。它经 ``adapters.build_stamp`` 够得到
``subprocess``，而 ``subprocess`` 在禁用清单里（理由逐字抄自 ``VENDOR_MODULES``：它是
标准库，也是「怎么够到一个外部提供者」那条路本身）。这一族因此**宁可绕路也不 import
那一族**：``require_scene`` 从 ``._shared`` 引（那里现在是**定义**所在），
``require_bundle`` 一次都不用。这条绕路的理由写在
``marketops/adapters/mcp_server/launch.py`` 的模块 docstring 上，而它是不是真的成立，
由下面 ``test_the_launch_surface_never_names_the_research_face`` 直接判 —— 判的是
**直接 import**，与上面那条**传递闭包**是两个不同的问题，理由见那一条的 docstring。

## 这一条判不到什么

与 ``tests/test_registration_is_offline.py`` 逐字同一条：它判不出「这一族调用了一个
外部地址但用的不是清单里的名字」（自己拼一个 ``os.system``、或者经 ``ctypes``）。
白名单判据的共同上限写在 ``test_the_network_ports_are_still_the_two_real_ones``
旁边，不假装它是完备的。
"""

from __future__ import annotations

import ast
from pathlib import Path

from tests.test_adapters_hold_the_vendors import (
    _imported_modules,
    _module_name,
    _package_name,
)
from tests.test_registration_is_offline import (
    NETWORK_PORTS,
    NETWORK_SDKS,
    _network_names,
    _network_reach,
    _on_disk,
)

REPO_ROOT = Path(__file__).resolve().parents[1]

#: 启动这一族**生产路径上的入口**。
#:
#: 只列入口，不列被它们拉进来的 —— 闭包那一半自动覆盖，而多列一个会让「谁负责这一族
#: 的离线」有两个答案（``application/plan_preview.py`` / ``product_readiness.py`` /
#: ``scene_receipt.py`` 都在 ``launch_preview.py`` 的闭包里，判据已经算到它们）。
#:
#: 两项都必须在盘上 —— ``test_the_launch_surface_is_on_disk`` 钉住这件事，否则清单
#: 缩水到空会让下面每一条判据一起变成恒真（一个不存在的模块当然不 import 任何东西）。
LAUNCH_SURFACE: tuple[str, ...] = (
    "marketops/adapters/mcp_server/launch.py",
    "marketops/application/launch_preview.py",
)

#: 这一族**必须**持有的那一个端口。少了它，「零外部调用」可以靠一个什么都不读的空壳
#: 达成，而那一条判据会全绿。见 ``test_the_launch_surface_holds_the_scene_store``。
SCENE_PORT = "marketops.ports.scenes"

#: 研究面与产品环的两个模块。**按模块全名**判，而不是按能力 —— 它们自己不是网络能力，
#: 只是这一族与网络之间最近的那一条路。见
#: ``test_the_launch_surface_never_names_the_research_face``。
RESEARCH_FACE: frozenset[str] = frozenset(
    {
        "marketops.adapters.mcp_server.tools",
        "marketops.adapters.mcp_server.product_loop",
    }
)


def test_the_launch_surface_is_on_disk() -> None:
    """清单里的每一项都必须真的在盘上。

    没有这一条，清单里写错一个路径就会被读成「那个模块很干净」—— 一个不存在的模块
    当然不 import 任何东西。判据在这里**红**，而不是静默放行。
    """
    missing = [rel for rel in LAUNCH_SURFACE if not (REPO_ROOT / rel).is_file()]
    assert not missing, (
        f"启动面清单点名的模块不在盘上：{missing}。"
        "要么路径写错了，要么这一片还没落 —— 两种都要在这里显形。"
    )


def test_the_launch_surface_never_reaches_the_network() -> None:
    """本体：启动这一族到不了任何网络能力，直接或间接都到不了。"""
    sources, packages = _on_disk()
    start = {_module_name(REPO_ROOT / rel) for rel in LAUNCH_SURFACE}
    # 前置：起点必须真的在模块图里。少了这一句，路径写错会让 `_network_reach` 拿到一个
    # 它不认识的起点**静默**返回空 —— 「没有违规」于是变成「没在判」。
    assert start <= set(sources), (
        f"启动面清单解析出的模块名不在仓库模块图里：{sorted(start - set(sources))}"
    )
    chains = _network_reach(sources, packages=packages, start=start)
    assert not chains, (
        "启动面能拿到网络能力，路径如下（最后一节是网络能力的名字）：\n"
        + "\n".join("  → ".join(chain) for chain in chains)
        + "\nB01 :104 逐字要求「启动授权前零外部调用」—— "
        "被预览的那个动作必须还没发生，而预览自己必须是一次读数。"
    )


def test_the_launch_surface_holds_the_scene_store() -> None:
    """反面：这一族必须**真的在读盘上的那一版声明**。

    少了这一条，「网络不可达」可以靠一个什么都不做的空壳达成 —— 而上面那条判据会全绿。
    这里要求它直接持有场景存储端口，于是「不联网」与「真的预览」两件事同时被钉住。

    ``ports.scenes`` 而不是别的：启动预览读的是**某一版已经写下的声明**（研究目标 /
    GeoScope / Scenario / 预算与周期），而那一版只住在场景存储里。它读的另外两个端口
    （``ports.product_catalog`` / ``ports.outreach``）**不算**这条反面的依据 ——
    「点名要卖什么」是可选的四格，一份没点名产品与方案的声明照旧是一次完整的预览。
    """
    sources, _ = _on_disk()
    holders: list[str] = []
    for rel in LAUNCH_SURFACE:
        path = REPO_ROOT / rel
        name = _module_name(path)
        if name not in sources:
            continue
        tree = ast.parse(sources[name], filename=name)
        # 每一份源码用它**自己**的包名解析相对 import —— 用清单里第一项的包名去解析
        # 第二项，会把 `..` 减到隔壁目录上，而解析错的表现是判据静默失效。
        if SCENE_PORT in _imported_modules(tree, package=_package_name(path)):
            holders.append(name)
    assert holders, (
        f"启动面没有任何一个模块点名 {SCENE_PORT}。"
        "一次不读那一版声明的预览当然不联网 —— 那不叫「零外部调用」，那叫没读数。"
    )


def test_the_launch_surface_never_names_the_research_face() -> None:
    """这一族**直接** import 的名字里没有研究面与产品环那两个模块。

    判的是**直接 import**，与 ``test_the_launch_surface_never_reaches_the_network``
    那个**传递闭包**是两个不同的问题，所以两条都要在：

    * 闭包那条判的是能力 —— 「够不够得到 ``subprocess``」。它今天够不到，但它是**经
      ``tools.py`` 够到**的，也就是说这条绿有一半是 ``adapters/build_stamp.py``
      今天恰好用了 ``subprocess`` 换来的；
    * 这一条判的是**形状** —— 这一族有没有把那条路接上。上面那条绿如果哪天因为
      `build_stamp` 改了实现而变绿得更容易，这一条照旧会红。

    要紧的地方在于：``tools.py`` 后面站着的是 ``application.dispatch`` /
    ``adapters.crawl4ai_fetcher`` / ``adapters.mineru_adapter`` —— 整台研究引擎只隔
    一层 import。而这一族存在的**全部理由**是「一次读数」，所以它连那一层都不要。
    这条绕路是 ``require_scene`` 从 ``tools.py`` 搬到 ``._shared.py`` 的唯一理由
    （那个模块的 docstring 上写着）。
    """
    sources, packages = _on_disk()
    for rel in LAUNCH_SURFACE:
        path = REPO_ROOT / rel
        name = _module_name(path)
        assert name in sources, f"{rel} 不在仓库模块图里"
        touched = _imported_modules(
            ast.parse(sources[name], filename=name), package=_package_name(path)
        )
        named = sorted(touched & RESEARCH_FACE)
        assert not named, (
            f"{name} 直接 import 了 {named}。研究面经 ``adapters.build_stamp`` 够得到 "
            "``subprocess``，所以这一族的离线就成了「那条路今天恰好有多远」的函数 —— "
            "而它要的是**够不到**，不是**今天够不着**。"
            "``require_scene`` 的定义在 ``._shared.py`` 里，按名字从那里引。"
        )
    # 前置：清单本身没被写成空集（空集会让上面那个循环一次都不跑，判据恒真）。
    assert len(LAUNCH_SURFACE) == 2, f"启动面清单变成了 {LAUNCH_SURFACE}"


def test_the_network_ports_are_still_the_two_real_ones() -> None:
    """反缩水：这一条读的是**共享的**禁用清单，所以它要在这里被看第二眼。

    ``NETWORK_PORTS`` / ``NETWORK_SDKS`` 住在本文件之外，而它们是可以被改小的 ——
    改小之后上面那条闭包判据立刻全绿，而该联网的地方照旧在跑。

    ``ports.product_catalog`` 与 ``ports.outreach`` **必须不在这份清单里**：这一族
    持有它们两个（被引的产品版本与已批准说法），把它们判成网络能力会让这条判据当场
    假红。禁用清单收的是**能力**，不是「端口」这个类别。

    ``test_registration_is_offline.test_the_forbidden_set_does_not_shrink`` 已经钉住了
    这份清单本身的形状，所以这里只判**与这一族有关的那两格**，不重抄那一份。
    """
    assert NETWORK_PORTS == {"marketops.ports.search", "marketops.ports.acquisition"}
    #: 这一族真的持有的两个端口，一个都不许被算成网络。
    for held in ("marketops.ports.product_catalog", "marketops.ports.outreach", SCENE_PORT):
        assert held not in NETWORK_PORTS
        assert held.split(".")[-1] not in NETWORK_SDKS, (
            f"{held} 的尾名进了厂商清单 —— 这一族 import 的是本仓的端口，"
            "而厂商清单按顶层名判，两条口径混起来会误伤"
        )


def test_the_scanner_catches_a_violation_from_this_start_set() -> None:
    """正对照：**这一族自己**的起点上 import 采集端口，必须被抓。

    ``tests/test_registration_is_offline.py`` 已经有一条正对照，但那条喂的起点是登记面。
    量具是同一个，而**起点集是本文件的主张** —— 一条只测 ``_network_reach`` 的正对照，
    在本文件的 ``LAUNCH_SURFACE`` 写错时仍然会绿。
    """
    chains = _network_reach(
        {
            "marketops.adapters.mcp_server.launch": (
                "from ...ports.acquisition import Fetcher\n"
            ),
        },
        packages=(),
        start=("marketops.adapters.mcp_server.launch",),
    )
    assert chains == [
        (
            "marketops.adapters.mcp_server.launch",
            "marketops.ports.acquisition",
        )
    ], f"这一族的起点上，直接的违规没被抓到：{chains}"


def test_the_scanner_catches_an_indirect_violation_at_length_three() -> None:
    """正对照：经**应用层**绕进去，必须同样被抓。

    链长三而不是二，因为这一族真实的形状就是这个：``launch.py`` →
    ``application.launch_preview`` → 某个端口。只判直接 import 的写法会漏掉这一条，
    且漏得**静默**。
    """
    chains = _network_reach(
        {
            "marketops.adapters.mcp_server.launch": (
                "from ...application.launch_preview import launch_preview\n"
            ),
            "marketops.application.launch_preview": "from ..ports.search import SearchProvider\n",
        },
        packages=(),
        start=("marketops.adapters.mcp_server.launch",),
    )
    assert chains == [
        (
            "marketops.adapters.mcp_server.launch",
            "marketops.application.launch_preview",
            "marketops.ports.search",
        )
    ], f"经应用层绕进去的违规没被抓到：{chains}"


def test_a_clean_launch_surface_is_not_flagged() -> None:
    """反转对照：真的干净的那一份不许报红。

    少了它，一条「见到什么都说违规」的判据也能通过上面两条正对照。这一份写的就是
    这一族真实的 import 面 —— 相对 import 三条、厂商 ``mcp`` 一条，一个端口都不碰。
    """
    chains = _network_reach(
        {
            "marketops.adapters.mcp_server.launch": (
                "from mcp.server.mcpserver.exceptions import ToolError\n"
                "from ...ports.product_catalog import ProductCatalog\n"
                "from ...ports.scenes import SceneStore\n"
                "from ._shared import Handler, require_scene, store_failure\n"
            ),
        },
        packages=(),
        start=("marketops.adapters.mcp_server.launch",),
    )
    assert chains == [], f"干净的那一份被误判了：{chains}"


def test_the_two_network_ports_are_recognized_by_their_whole_name() -> None:
    """反向读一遍禁用口径：端口按**全名**判，厂商按**顶层名**判。

    这里只判这一族会碰到的写法 —— ``from ...ports import search`` 这种「先 import 包
    再取子模块」的写法绑定的东西与 ``from ...ports.search import X`` 是同一个，两条
    都必须被抓。``marketops.models.scenes`` 这类**名字里带 search/scenes 的近邻**不许
    被误伤：判的是全名，不是子串。
    """
    for source in (
        "from ...ports import search\n",
        "from ...ports.search import SearchProvider\n",
    ):
        chains = _network_reach(
            {"marketops.adapters.mcp_server.launch": source},
            packages=(),
            start=("marketops.adapters.mcp_server.launch",),
        )
        assert len(chains) == 1, f"这一行没被判成违规：{source!r} → {chains}"

    #: 全名判据，不是子串判据：``marketops.ports.search`` 之外的名字一个都不算。
    assert _network_names({"marketops.ports.scenes", "marketops.domain.scene"}) == set()
    assert _network_names({"marketops.ports.search"}) == {"marketops.ports.search"}
