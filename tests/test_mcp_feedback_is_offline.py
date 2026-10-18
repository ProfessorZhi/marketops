"""「``preview_feedback_impact`` 零 provider、零写入」是一处**构造**，不是一句保证。

八条工具的目录行里，``preview_feedback_impact`` 那一行的最后一句逐字：

> **哪些改动在这一层根本不可用**。最后那一格是承重的：它把「做不到」与「没人想过」
> 分开。**零 provider、零写入**。

同一个否定句在 ``tests/test_mcp_launch_is_offline.py`` 里已经被拆开过一次
（B01 第五条关闭条件：「启动授权前零外部调用」），这一份是它在 B23 上的落点，
口径与那两份逐字同源：真的会对外发起请求、**或起一个外部进程**。

## 这一族比启动预览面离那条路更近一步

启动预览面绕开的是 ``tools.py``（它经 ``adapters.build_stamp`` 够得到 ``subprocess``）。
这一族绕开的**多一样东西**：``marketops.ports.acquisition``。

理由不是洁癖。一条反馈的处置写着「preview 影响后由上级批准」——
预览是**批准之前**那一份读数。一次预览如果自己会去取一页页面（「顺手确认一下这条
反馈说的那个页面现在长什么样」），那么：

* 「批准之前」这个时刻不存在了 —— 读数里混进了一次没有人批准过的外部观察；
* 它在断网时会失败，而失败的样子是「这条反馈预览不出来」，与「这条反馈写得不对」
  在回执上同形；
* 最要紧的一条：**它此后会被当成一次观测读回去**。零 provider 掉在地上不会报错，
  只会让一份「若被采纳会改到什么」的读数里多出几个没有来源的值。

所以这一族**一个端口都不许碰网络**：它持有的是 ``ports.feedback``（那一行反馈自己）、
``ports.review``（复核存储）、``ports.store``（证据读取）—— 三个都是本地的、
按工作区隔离的存储契约。

## 这一条判不到什么

与另外两份逐字同一条：它判不出「这一族调用了一个外部地址但用的不是清单里的名字」
（自己拼一个 ``os.system``、或者经 ``ctypes``）。白名单判据的共同上限写在
``test_the_network_ports_are_still_the_two_real_ones`` 旁边，不假装它是完备的。
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

#: 反馈这一族**生产路径上的入口**。
#:
#: 只列入口，不列被它们拉进来的 —— 闭包那一半自动覆盖，而多列一个会让「谁负责这一族
#: 的离线」有两个答案（``domain/feedback.py`` / ``ports/feedback.py`` /
#: ``not_reported.py`` 都在闭包里，判据已经算到它们）。
#:
#: 三项都必须在盘上 —— ``test_the_feedback_surface_is_on_disk`` 钉住这件事，否则清单
#: 缩水到空会让下面每一条判据一起变成恒真（一个不存在的模块当然不 import 任何东西）。
FEEDBACK_SURFACE: tuple[str, ...] = (
    "marketops/adapters/mcp_server/feedback.py",
    "marketops/application/feedback.py",
    "marketops/application/feedback_preview.py",
)

#: 这一族**必须**持有的那一个端口。
#:
#: 少了它，「零外部调用」可以靠一个什么都不读的空壳达成，而那一条判据会全绿 ——
#: 一次不读那一行反馈的预览当然不联网，那不叫「零 provider」，那叫没读数。
FEEDBACK_PORT = "marketops.ports.feedback"

#: 这一族与网络之间最近的那一条路。**按模块全名**判，不按能力 —— 它们自己不是网络
#: 能力，只是那三层门后面的东西。
FORBIDDEN_NEIGHBOURS: frozenset[str] = frozenset(
    {
        # 经 ``adapters.build_stamp`` 够得到 ``subprocess``。
        "marketops.adapters.mcp_server.tools",
        "marketops.adapters.mcp_server.product_loop",
        # 研究侧那两个处理器表：它们 import 的就是采集端口本身。
        "marketops.adapters.mcp_server.research",
        "marketops.adapters.mcp_server.acquisition",
    }
)


def test_the_feedback_surface_is_on_disk() -> None:
    """清单里的每一项都必须真的在盘上。

    没有这一条，清单里写错一个路径就会被读成「那个模块很干净」—— 一个不存在的模块
    当然不 import 任何东西。判据在这里**红**，而不是静默放行。
    """
    missing = [rel for rel in FEEDBACK_SURFACE if not (REPO_ROOT / rel).is_file()]
    assert not missing, (
        f"反馈面清单点名的模块不在盘上：{missing}。"
        "要么路径写错了，要么这一片还没落 —— 两种都要在这里显形。"
    )


def test_the_feedback_surface_never_reaches_the_network() -> None:
    """本体：反馈这一族到不了任何网络能力，直接或间接都到不了。"""
    sources, packages = _on_disk()
    start = {_module_name(REPO_ROOT / rel) for rel in FEEDBACK_SURFACE}
    # 前置：起点必须真的在模块图里。少了这一句，路径写错会让 `_network_reach` 拿到一个
    # 它不认识的起点**静默**返回空 —— 「没有违规」于是变成「没在判」。
    assert start <= set(sources), (
        f"反馈面清单解析出的模块名不在仓库模块图里：{sorted(start - set(sources))}"
    )
    chains = _network_reach(sources, packages=packages, start=start)
    assert not chains, (
        "反馈面能拿到网络能力，路径如下（最后一节是网络能力的名字）：\n"
        + "\n".join("  → ".join(chain) for chain in chains)
        + "\n``preview_feedback_impact`` 的目录行逐字要求「零 provider、零写入」——"
        "一份在批准**之前**取到的读数里，不许混进一次没有人批准过的外部观察。"
    )


def test_the_feedback_surface_holds_the_feedback_store() -> None:
    """反面：这一族必须**真的在读盘上的那一行反馈**。

    少了这一条，「网络不可达」可以靠一个什么都不做的空壳达成 —— 而上面那条判据会全绿。
    这里要求它直接持有反馈存储端口，于是「不联网」与「真的预览」两件事同时被钉住。

    ``ports.feedback`` 而不是 ``ports.store``：预览读的是**某一条已经写下的反馈**
    （它的类别、它自己说的那一格作用域、它此后被谁引用过），而那一行只住在反馈存储里。
    它读的另外两个端口（``ports.review`` / ``ports.store``）**不算**这条反面的依据 ——
    它们回答的是「它批准了没有」与「这条证据今天读不读得出来」，两条都是可选的读数，
    一份没接存储的部署照旧预览得出这条反馈会改到什么。
    """
    sources, _ = _on_disk()
    holders: list[str] = []
    for rel in FEEDBACK_SURFACE:
        path = REPO_ROOT / rel
        name = _module_name(path)
        if name not in sources:
            continue
        tree = ast.parse(sources[name], filename=name)
        # 每一份源码用它**自己**的包名解析相对 import —— 用清单里第一项的包名去解析
        # 第二项，会把 `..` 减到隔壁目录上，而解析错的表现是判据静默失效。
        if FEEDBACK_PORT in _imported_modules(tree, package=_package_name(path)):
            holders.append(name)
    assert holders, (
        f"反馈面没有任何一个模块点名 {FEEDBACK_PORT}。"
        "一次不读那一行反馈的预览当然不联网 —— 那不叫「零 provider」，那叫没读数。"
    )


def test_the_feedback_surface_never_names_a_forbidden_neighbour() -> None:
    """这一族**直接** import 的名字里没有那三层门后面的东西。

    判的是**直接 import**，与 ``test_the_feedback_surface_never_reaches_the_network``
    那个**传递闭包**是两个不同的问题，所以两条都要在：

    * 闭包那条判的是能力 —— 「够不够得到 ``subprocess`` / 采集端口」。它今天够不到，
      但那有可能是**上游某一天恰好改短了那条路**换来的；
    * 这一条判的是**形状** —— 这一族有没有把那条路接上。上面那条绿如果哪天因为
      `build_stamp` 改了实现而变绿得更容易，这一条照旧会红。

    要紧的地方在于：``tools.py`` 后面站着整台研究引擎（``application.dispatch`` /
    ``adapters.crawl4ai_fetcher`` / ``adapters.mineru_adapter``），而这一族存在的
    **全部理由**是「批准之前的一份读数」。

    这条绕路是 ``require_scene`` 与 ``store_failure`` 从 ``tools.py`` 搬到 ``._shared.py``
    之后，这一族从那里按名字引的原因（``_shared`` 的 docstring 上写着）。
    """
    sources, _ = _on_disk()
    for rel in FEEDBACK_SURFACE:
        path = REPO_ROOT / rel
        name = _module_name(path)
        assert name in sources, f"{rel} 不在仓库模块图里"
        touched = _imported_modules(
            ast.parse(sources[name], filename=name), package=_package_name(path)
        )
        named = sorted(touched & FORBIDDEN_NEIGHBOURS)
        assert not named, (
            f"{name} 直接 import 了 {named}。研究面经 ``adapters.build_stamp`` 够得到 "
            "``subprocess``，采集那一侧就是 ``ports.acquisition`` 本身 —— 所以这一族的"
            "离线会变成「那条路今天恰好有多远」的函数。"
            "``require_scene`` / ``store_failure`` 的定义在 ``._shared.py`` 里，按名字从那里引。"
        )
    # 前置：清单本身没被写成空集（空集会让上面那个循环一次都不跑，判据恒真）。
    assert len(FEEDBACK_SURFACE) == 3, f"反馈面清单变成了 {FEEDBACK_SURFACE}"


def test_the_network_ports_are_still_the_two_real_ones() -> None:
    """反缩水：这一条读的是**共享的**禁用清单，所以它要在这里被看第二眼。

    ``NETWORK_PORTS`` / ``NETWORK_SDKS`` 住在本文件之外，而它们是可以被改小的 ——
    改小之后上面那条闭包判据立刻全绿，而该联网的地方照旧在跑。

    ``ports.feedback`` / ``ports.review`` / ``ports.store`` **必须不在这份清单里**：
    这一族持有它们三个，把它们判成网络能力会让这条判据当场假红。禁用清单收的是
    **能力**，不是「端口」这个类别。
    """
    assert NETWORK_PORTS == {"marketops.ports.search", "marketops.ports.acquisition"}
    for held in (
        FEEDBACK_PORT,
        "marketops.ports.review",
        "marketops.ports.store",
    ):
        assert held not in NETWORK_PORTS
        assert held.split(".")[-1] not in NETWORK_SDKS, (
            f"{held} 的尾名进了厂商清单 —— 这一族 import 的是本仓的端口，"
            "而厂商清单按顶层名判，两条口径混起来会误伤"
        )


def test_the_scanner_catches_a_violation_from_this_start_set() -> None:
    """正对照：**这一族自己**的起点上 import 采集端口，必须被抓。

    ``tests/test_registration_is_offline.py`` 与 ``tests/test_mcp_launch_is_offline.py``
    已经各有一条正对照，但那些喂的起点是另外两族。量具是同一个，而
    **起点集是本文件的主张** —— 一条只测 ``_network_reach`` 的正对照，
    在本文件的 ``FEEDBACK_SURFACE`` 写错时仍然会绿。
    """
    chains = _network_reach(
        {
            "marketops.adapters.mcp_server.feedback": (
                "from ...ports.acquisition import Fetcher\n"
            ),
        },
        packages=(),
        start=("marketops.adapters.mcp_server.feedback",),
    )
    assert chains == [
        (
            "marketops.adapters.mcp_server.feedback",
            "marketops.ports.acquisition",
        )
    ], f"这一族的起点上，直接的违规没被抓到：{chains}"


def test_the_scanner_catches_an_indirect_violation_at_length_three() -> None:
    """正对照：经**应用层**绕进去，必须同样被抓。

    链长三而不是二，因为这一族真实的形状就是这个：``feedback.py`` →
    ``application.feedback_preview`` → 某个端口。只判直接 import 的写法会漏掉这一条，
    且漏得**静默**。
    """
    chains = _network_reach(
        {
            "marketops.adapters.mcp_server.feedback": (
                "from ...application.feedback_preview import feedback_impact\n"
            ),
            "marketops.application.feedback_preview": (
                "from ..ports.search import SearchProvider\n"
            ),
        },
        packages=(),
        start=("marketops.adapters.mcp_server.feedback",),
    )
    assert chains == [
        (
            "marketops.adapters.mcp_server.feedback",
            "marketops.application.feedback_preview",
            "marketops.ports.search",
        )
    ], f"经应用层绕进去的违规没被抓到：{chains}"


def test_a_clean_feedback_surface_is_not_flagged() -> None:
    """反转对照：真的干净的那一份不许报红。

    少了它，一条「见到什么都说违规」的判据也能通过上面两条正对照。这一份写的就是
    这一族真实的 import 面 —— 三条相对 import、一条厂商 ``mcp``，一个网络端口都不碰。
    """
    chains = _network_reach(
        {
            "marketops.adapters.mcp_server.feedback": (
                "from mcp.server.mcpserver.exceptions import ToolError\n"
                "from ...ports.feedback import FeedbackStore\n"
                "from ...ports.store import EvidenceStore\n"
                "from ._shared import Handler, readable, store_failure\n"
            ),
        },
        packages=(),
        start=("marketops.adapters.mcp_server.feedback",),
    )
    assert chains == [], f"干净的那一份被误判了：{chains}"


def test_the_two_network_ports_are_recognized_by_their_whole_name() -> None:
    """反向读一遍禁用口径：端口按**全名**判，厂商按**顶层名**判。

    这里只判这一族会碰到的写法 —— ``from ...ports import acquisition`` 这种「先 import
    包再取子模块」的写法绑定的东西与 ``from ...ports.acquisition import X`` 是同一个，
    两条都必须被抓。名字里带 ``feedback`` / ``search`` 的近邻不许被误伤：
    判的是全名，不是子串。
    """
    for source in (
        "from ...ports import acquisition\n",
        "from ...ports.acquisition import Fetcher\n",
    ):
        chains = _network_reach(
            {"marketops.adapters.mcp_server.feedback": source},
            packages=(),
            start=("marketops.adapters.mcp_server.feedback",),
        )
        assert len(chains) == 1, f"这一行没被判成违规：{source!r} → {chains}"

    #: 全名判据，不是子串判据：``marketops.ports.feedback`` 这类名字一个都不算。
    assert _network_names({"marketops.ports.feedback", "marketops.domain.feedback"}) == set()
    assert _network_names({"marketops.ports.search"}) == {"marketops.ports.search"}
