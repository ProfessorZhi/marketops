"""有效场景这一族在**接线面**上是「读」—— 一处构造，不是一句自觉。

冻结表 §3.2 把这一族写成两格：「``preview_effective_scene`` 编译（纯函数、零 provider
调用）」与「``start_workstream`` 冻结落库」。前一半的判据分两层：

* ``tests/test_effective_scene_is_offline.py`` 算**应用层**那一半（``compile_effective_scene``
  连一个端口都不收）；
* 本文件算**接线层**这一半，起点是处理器模块自己 —— 它才是「一次预览会不会花钱、
  会不会联网、断网时会不会失败」真正取决的地方。应用层干净而接线层递了一把检索端口
  进去，那个组合在任何一份应用层判据上都是绿的。

口径与 ``tests/test_mcp_launch_is_offline.py`` / ``tests/test_registration_is_offline.py``
**逐字同源**：真的会对外发起请求、**或起一个外部进程**（``subprocess`` 在禁用清单里，
理由抄自 ``VENDOR_MODULES``）。四张禁止表（网络端口、厂商 SDK、点分全名、注解名）
与那两份共享，本文件不重抄它们。

## 四条判据，各挡一种错法

1. **够不到网络**（传递闭包）。挡的是「预览顺手确认一下这家厂商的官网还在不在」——
   行为上仍然正确，只是预览从此会花钱、会联网。
2. **持有 ``ports.workstreams``**（反面）。少了它，「零外部调用」可以靠一个什么都不读的
   空壳达成，而上面那条判据会全绿。这一族读的是**冻结在盘上的那一版读数**。
3. **端口闭包精确等于四个**（多一个少一个都红）。挡的是「顺手再拿一个端口」——
   而 ``ports.budget`` 这类**不花钱**的端口也在精确相等之内，因为它们进来之后
   「这一族读了什么」就没有答案了。四格里有**一格是复用换来的**
   （``ports.outreach``，经 ``launch.py``），它照旧写在表上，理由在那张表旁边。
4. **``effective`` 这个端口被用的次数是一次，且那一次在 ``get_effective_scene`` 里**。
   这是「两条只读」里**读得动**的那一半的形状判据：``EffectiveSceneStore`` 读写两面
   同住一个端口（见 ``ports/workstreams.py``），所以「预览那一条不写」不能靠「它不拿
   那个端口」达成 —— 它拿得到那个端口而不去碰它，这一条属的是那个「不去碰」。
   同一条判据也钉住 ``utc_now()`` 只被读**一次**：一次预览的三个 ``at`` 取自同一个
   瞬间，各读一次时钟会让这份读数自相矛盾而报告上看不出来。

## 这一族**不**包含 ``workstream.py``

``marketops/adapters/mcp_server/workstream.py`` 够得到 ``ports.acquisition``，路径是
``workstream.py`` → ``runs.py`` → ``product_loop.py`` → ``application.acquisition``
（下面 ``test_the_write_half_is_not_reachable_from_the_read_face`` 之外的
``test_the_probe_still_sees_the_write_half_reach_the_network`` 把这一条**量出来**，
不当作没看见）。那不是缺陷：``start_workstream`` 是**唯一**允许创建 Run 的那条路
（承重点 5），而 Run 的受理走的就是 ``runs.py`` 那一套。把这一族算进离线面会让这条
判据当场假红，而假红的下场是这个文件被删掉。

所以这一族真正的形状判据是**方向**：读的那一面够不到写的那一面
（``EFFECTIVE_SCENE_SURFACE`` 里没有、也不许出现 ``workstream.py``）。这条写在
``FORBIDDEN_NEIGHBOURS`` 上，而不是靠「清单里恰好没写它」。

## 这一条判不到什么

与另外两份逐字同一条：它判不出「这一族调用了一个外部地址但用的不是清单里的名字」
（自己拼一个 ``os.system``、或者经 ``ctypes``）。白名单判据的共同上限写在
``test_the_network_ports_are_still_the_two_real_ones`` 旁边，不假装它是完备的。
"""

from __future__ import annotations

import ast
import textwrap
from collections.abc import Collection, Iterator, Mapping
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

#: 这一族**生产路径上的入口**。
#:
#: 只列入口，不列被它们拉进来的 —— 闭包那一半自动覆盖（``launch.py`` / ``_shared.py`` /
#: ``config`` / ``domain`` / ``ports`` 都在闭包里，判据已经算到它们）。
#:
#: 两项都必须在盘上 —— ``test_the_effective_scene_surface_is_on_disk`` 钉住这件事，
#: 否则清单缩水到空会让下面每一条判据一起变成恒真（一个不存在的模块当然不 import
#: 任何东西）。
EFFECTIVE_SCENE_SURFACE: tuple[str, ...] = (
    "marketops/adapters/mcp_server/effective_scene.py",
    "marketops/application/effective_scene.py",
)

#: 这一族**必须**持有的那一个端口。少了它，「零外部调用」可以靠一个什么都不读的空壳
#: 达成，而那一条判据会全绿。见 ``test_the_surface_holds_the_effective_scene_store``。
EFFECTIVE_PORT = "marketops.ports.workstreams"

#: 这一族的端口闭包，**精确相等**。
#:
#: * ``ports.product_catalog``：编译要读被指名的那一版产品与方案 —— 它是**声明**，
#:   不是检索结果；
#: * ``ports.scenes``：那两条「读哪一版 / 盘上有什么」的拒绝与启动预览同一份实现，
#:   而它们读的是场景存储；
#: * ``ports.workstreams``：冻结那一版的读回（``EffectiveSceneStore``）；
#: * ``ports.outreach``：**不是这一族读的**。它经 ``launch.py`` 进来 —— 这一族复用
#:   了它的 ``_require_record`` / ``_require_version``（那两条拒绝答的是同一个问题，
#:   第二份实现漂移的那天两条工具会在同一个请求上给出两句不同的话），而 ``launch.py``
#:   自己持有 ``ports.outreach``（被引的产品版本与已批准说法，启动预览要点名卖什么）。
#:
#: 第四格是**复用换来的**，而它照旧要在这张表上：不写它，「这一族读了什么」就少了一格；
#: 写了它，有人把 ``launch.py`` 的端口面扩大时这一条会红 —— 而那正是「复用一处实现」
#: 在离线判据上的代价，代价要看得见，不该藏在一个不精确的集合里。
#:
#: 四个都不花钱。多一个就是多一条「这一族读了什么」答不上来的路。
EFFECTIVE_SCENE_PORTS: frozenset[str] = frozenset(
    {
        "marketops.ports.outreach",
        "marketops.ports.product_catalog",
        "marketops.ports.scenes",
        EFFECTIVE_PORT,
    }
)

#: 这一族**直接** import 的名字里不许出现的模块。
#:
#: ``tools`` 经 ``adapters.build_stamp`` 够得到 ``subprocess``；``product_loop`` 与
#: ``runs`` 够得到 ``ports.acquisition``（后者过一手前者）；``workstream`` 是**写**的
#: 那一半 —— 它经 ``runs`` 拿到同一批东西，而且它持有 ``start_workstream``，
#: 也就是**唯一**允许创建 Run 的那条路（承重点 5）。
#:
#: 判的是**直接 import**，与闭包那条是两个不同的问题，所以两条都要在：闭包判的是
#: 能力（今天够不够得到），这一条判的是**形状**（有没有把那条路接上）。今天那条闭包
#: 是空的，而它之所以是空的有一半是 ``_shared.py`` 那次搬家换来的 —— 这一条在搬家
#: 被撤销的那天照旧会响。
FORBIDDEN_NEIGHBOURS: frozenset[str] = frozenset(
    {
        "marketops.adapters.mcp_server.tools",
        "marketops.adapters.mcp_server.product_loop",
        "marketops.adapters.mcp_server.runs",
        "marketops.adapters.mcp_server.workstream",
    }
)

#: 读的那一半与写的那一半。见 ``test_the_write_half_is_not_reachable_from_the_read_face``。
READ_FACE_MODULE = "marketops.adapters.mcp_server.effective_scene"
WRITE_FACE_MODULE = "marketops.adapters.mcp_server.workstream"


# --- 量具（吃源码的纯函数，正反对照打的是判据而不是解析器） ---------------------


def _reachable(
    sources: Mapping[str, str], *, packages: Collection[str], start: Collection[str]
) -> set[str]:
    """从 ``start`` 出发沿仓库内 import 走到的**全部模块**（含起点）。

    与 ``tests/test_mcp_views_are_offline._reachable`` 同一个不动点。
    """
    imports: dict[str, set[str]] = {}
    for name, source in sources.items():
        package = name if name in packages else name.rpartition(".")[0]
        imports[name] = _imported_modules(ast.parse(source, filename=name), package=package)
    seen: set[str] = set()
    frontier = [name for name in start if name in sources]
    while frontier:
        name = frontier.pop()
        if name in seen:
            continue
        seen.add(name)
        frontier.extend(dep for dep in imports.get(name, ()) if dep in sources)
    return seen


def _ports_in(closure: Collection[str]) -> frozenset[str]:
    """闭包里那些 ``marketops.ports.*`` 的**全名**（不含 ``marketops.ports`` 这个包，
    也不含端口模块里的某个对象 —— 判的是这一族拿到了哪个端口）。"""
    return frozenset(
        name
        for name in closure
        if name.startswith("marketops.ports.") and name.count(".") == 2
    )


def _own_nodes(function: ast.AST) -> Iterator[ast.AST]:
    """一个函数**自己**的节点，不含它内嵌函数里的那些。

    少了这一条，``effective()`` 在哪个函数体里就没有答案：``ast.walk`` 会先把外层的
    名字写上，于是嵌套 def 里的调用会被记到外层函数的账上 —— 而这一族的形状恰好是
    「两个工具都是工厂里的嵌套 def」。
    """
    stack = list(ast.iter_child_nodes(function))
    while stack:
        node = stack.pop()
        yield node
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
            continue
        stack.extend(ast.iter_child_nodes(node))


def _callers_of(source: str, name: str) -> list[str]:
    """源码里对 ``name`` 的调用，各落在哪一个（最内层）函数体里，按名字排序。

    **两种写法都算**：裸名 ``put_scene(...)`` 与属性 ``store.put_scene(...)``。
    只认裸名的写法在应用层会数出零 —— 那里的写是 ``store.put_scene(...)`` ——
    而零个调用与「没有人写」在一条 ``== []`` 的判据上同形。

    上限：``getattr(store, "put_scene")(...)`` 与 ``self.__dict__[...]`` 这一路抓不到
    （与白名单判据的共同上限同一条纪律，不假装完备）。
    """
    tree = ast.parse(textwrap.dedent(source))
    found: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for inner in _own_nodes(node):
            if not isinstance(inner, ast.Call):
                continue
            func = inner.func
            if (isinstance(func, ast.Name) and func.id == name) or (
                isinstance(func, ast.Attribute) and func.attr == name
            ):
                found.add(node.name)
    return sorted(found)


def _imported_names(path: Path) -> set[str]:
    return _imported_modules(
        ast.parse(path.read_text(encoding="utf-8")), package=_package_name(path)
    )


# --- 清单 ---------------------------------------------------------------------


def test_the_effective_scene_surface_is_on_disk() -> None:
    """清单里的每一项都必须真的在盘上。

    没有这一条，清单里写错一个路径就会被读成「那个模块很干净」—— 一个不存在的模块
    当然不 import 任何东西。判据在这里**红**，而不是静默放行。
    """
    missing = [rel for rel in EFFECTIVE_SCENE_SURFACE if not (REPO_ROOT / rel).is_file()]
    assert not missing, (
        f"有效场景面清单点名的模块不在盘上：{missing}。"
        "要么路径写错了，要么这一片还没落 —— 两种都要在这里显形。"
    )
    #: 空清单会让下面每一条一起变成恒真（一个不存在的起点当然到不了任何地方）。
    assert len(EFFECTIVE_SCENE_SURFACE) == 2, f"清单变成了 {EFFECTIVE_SCENE_SURFACE}"


# --- 1 · 够不到网络 ------------------------------------------------------------


def test_the_effective_scene_surface_never_reaches_the_network() -> None:
    """本体：这一族到不了任何网络能力，直接或间接都到不了。"""
    sources, packages = _on_disk()
    start = {_module_name(REPO_ROOT / rel) for rel in EFFECTIVE_SCENE_SURFACE}
    # 前置：起点必须真的在模块图里。少了这一句，路径写错会让 `_network_reach` 拿到一个
    # 它不认识的起点**静默**返回空 —— 「没有违规」于是变成「没在判」。
    assert start <= set(sources), (
        f"清单解析出的模块名不在仓库模块图里：{sorted(start - set(sources))}"
    )
    chains = _network_reach(sources, packages=packages, start=start)
    assert not chains, (
        "有效场景这一族能拿到网络能力，路径如下（最后一节是网络能力的名字）：\n"
        + "\n".join("  → ".join(chain) for chain in chains)
        + "\n冻结表 §3.2 逐字要求「``preview_effective_scene`` 编译（纯函数、"
        "零 provider 调用）」—— 一次预览必须是一次读数。"
    )


# --- 2 · 反面：真的在读盘上那一版 -----------------------------------------------


def test_the_surface_holds_the_effective_scene_store() -> None:
    """反面：这一族必须**真的持有**冻结那一版的存储端口。

    少了这一条，「网络不可达」可以靠一个什么都不做的空壳达成 —— 而上面那条判据会全绿。
    这里要求它点名持有 ``ports.workstreams``，于是「不联网」与「真的读得到冻结的那一版」
    两件事同时被钉住。

    ``ports.workstreams`` 而不是别的：``get_effective_scene`` 读的是**某一版已经冻结
    的有效场景**，而那一版只住在 ``EffectiveSceneStore`` 里。它读的另外两个端口
    （``ports.product_catalog`` / ``ports.scenes``）**不算**这条反面的依据 ——
    「点名要卖什么」是可选的，而「读哪一版声明」是启动预览那一族已经在做的事。
    """
    sources, _ = _on_disk()
    holders: list[str] = []
    for rel in EFFECTIVE_SCENE_SURFACE:
        path = REPO_ROOT / rel
        name = _module_name(path)
        if name not in sources:
            continue
        # 每一份源码用它**自己**的包名解析相对 import —— 用清单里第一项的包名去解析
        # 第二项，会把 `..` 减到隔壁目录上，而解析错的表现是判据静默失效。
        if EFFECTIVE_PORT in _imported_names(path):
            holders.append(name)
    assert holders, (
        f"有效场景面没有任何一个模块点名 {EFFECTIVE_PORT}。"
        "一次不读那一版冻结读数的「预览」当然不联网 —— 那不叫零外部调用，那叫没读数。"
    )


# --- 3 · 端口闭包精确相等 -------------------------------------------------------


def test_the_surface_holds_exactly_these_ports() -> None:
    """**精确相等**，多一个少一个都红。

    挡的是「顺手再拿一个端口」：``ports.outreach`` / ``ports.budget`` 这类端口**不花钱**，
    所以上面那条网络判据对它们是全绿的 —— 而它们进来之后，「这一族读了什么」就没有
    答案了。这一族读的只有三样，三样都在这张表上。

    它同时是**反缩水**的：有人删掉一条 import 让网络判据更容易绿的时候，这里会红。
    """
    sources, packages = _on_disk()
    start = {_module_name(REPO_ROOT / rel) for rel in EFFECTIVE_SCENE_SURFACE}
    closure = _reachable(sources, packages=packages, start=start)
    ports = _ports_in(closure)
    assert ports == EFFECTIVE_SCENE_PORTS, (
        f"多出来的：{sorted(ports - EFFECTIVE_SCENE_PORTS)}；"
        f"少掉的：{sorted(EFFECTIVE_SCENE_PORTS - ports)}"
    )


# --- 4 · 读的那一面不碰写的那一份 -----------------------------------------------


def test_the_preview_path_never_touches_the_effective_scene_store() -> None:
    """``effective`` 这个端口在处理器模块里被用的次数**是一次**，且在 ``get_effective_scene`` 里。

    ``EffectiveSceneStore`` 读写两面同住一个端口（``ports/workstreams.py``：``put_scene``
    与 ``get_scene`` 在一个 ABC 上），所以「预览那一条不写」**不能**靠「它不拿那个端口」
    达成 —— 它拿得到，而它不去碰。这一条属的就是那个「不去碰」：

    * 有人把 ``effective()`` 挪进 ``_preview``（「反正顺手读一下上一版」）⇒ 这条红；
    * 有人删掉读的那一条 ⇒ 这条红；
    * 只有「恰好一次、且在读的那一条里」是绿的。
    """
    callers = _callers_of(
        (REPO_ROOT / "marketops/adapters/mcp_server/effective_scene.py").read_text(
            encoding="utf-8"
        ),
        "effective",
    )
    assert callers == ["get_effective_scene"], (
        f"``effective`` 被这些函数体用了：{callers}。"
        "它只该在读的那一条里被用一次 —— 冻结那一半在 ``start_workstream``。"
    )


def test_the_freeze_path_is_the_only_writer_at_the_application_layer() -> None:
    """应用层同一条：``put_scene`` 只出现在 ``freeze_effective_scene`` 里。

    处理器那一侧看不全：真正的写发生在应用层，而 ``preview_effective_scene`` 与
    ``freeze_effective_scene`` 是**同一份编译**的两个出口。有人把写挪进编译那一层，
    接线面上的调用计数照旧是一次 —— 而这一条会红。
    """
    source = (REPO_ROOT / "marketops/application/effective_scene.py").read_text(
        encoding="utf-8"
    )
    assert _callers_of(source, "put_scene") == ["freeze_effective_scene"]
    # 反面：读那一半真的在读 —— 少了一条就没人知道它读没读。
    assert _callers_of(source, "get_scene") != []


def test_the_clock_is_read_once_for_the_whole_reading() -> None:
    """``utc_now()`` 在整个处理器模块里**只被调一次**，在 ``_preview`` 里。

    一次预览的三个 ``at``（裁决、``GeoScope``、``SalesMotion``）取自**同一个**瞬间。
    各读一次时钟的后果不是文案不一致：一份读数里两个判定用了两个时刻，而它在报告上
    与「同一个时刻」**同形**。多读一次的实现会在这里红。
    """
    source = (REPO_ROOT / "marketops/adapters/mcp_server/effective_scene.py").read_text(
        encoding="utf-8"
    )
    assert _callers_of(source, "utc_now") == ["_preview"]


def test_the_write_half_is_not_reachable_from_the_read_face() -> None:
    """读的那一面够不到写的那一面 —— 这一族的**方向**判据。

    ``workstream.py`` 持有 ``start_workstream``，也就是**唯一**允许创建 Run 的那条路
    （承重点 5）。读的这一族要「零 provider」而不要「能顺手立一个 Run」：两条都要，
    所以写的那一半不许在读的那一份的直接 import 面上。

    与闭包那条是两个不同的问题：闭包今天对 ``workstream.py`` 也是红的（它经 ``runs.py``
    够得到 ``ports.acquisition``），但那条红有一半是 ``runs.py`` 今天的形状换来的 ——
    而这一条判的是**有没有把写的那一半接上**。
    """
    sources, packages = _on_disk()
    start = {READ_FACE_MODULE}
    closure = _reachable(sources, packages=packages, start=start)
    assert WRITE_FACE_MODULE not in closure, (
        f"{READ_FACE_MODULE} 的闭包里有 {WRITE_FACE_MODULE}。"
        "读的那一面（两条只读工具）不该够得到冻结那一半 —— 冻结是一次写入，"
        "它的入口是 ``start_workstream``。"
    )
    # 前置：起点真的在模块图里（否则上面那句恒真）。
    assert start <= set(sources), f"{READ_FACE_MODULE} 不在模块图里"


# --- 共享判据的两次复核 ---------------------------------------------------------


def test_the_surface_never_names_a_forbidden_neighbour() -> None:
    """**直接** import 的名单里没有那四个模块。理由见 ``FORBIDDEN_NEIGHBOURS``。"""
    for rel in EFFECTIVE_SCENE_SURFACE:
        path = REPO_ROOT / rel
        named = sorted(_imported_names(path) & FORBIDDEN_NEIGHBOURS)
        assert not named, (
            f"{rel} 直接 import 了 {named}。"
            "``tools`` 经 ``adapters.build_stamp`` 够得到 ``subprocess``；``product_loop`` "
            "与 ``runs`` 够得到 ``ports.acquisition``；``workstream`` 是写的那一半。"
            "这一族存在的全部理由是一次读数，所以它连那一层都不要。"
        )


def test_the_probe_still_sees_the_write_half_reach_the_network() -> None:
    """把上一条的**反面**也量出来：写的那一半**今天真的够得到**网络。

    这不是一条关于本族的判据，是一条关于**量具**的判据，也是一句诚实的话：
    ``workstream.py`` 经 ``runs.py`` → ``product_loop.py`` → ``application.acquisition``
    够得到 ``ports.acquisition``。它**不是**缺陷（``start_workstream`` 要受理一个 Run，
    而 Run 的受理走的就是那一套），但它必须被**量出来**而不是被「清单里没写它」盖住 ——
    那样读这份文件的人会以为整个 B02 都是离线的，而其中一半不是。

    少了这一条，``FORBIDDEN_NEIGHBOURS`` 与闭包判据谁也没说过写的那一半是什么形状。
    """
    sources, packages = _on_disk()
    chains = _network_reach(sources, packages=packages, start={WRITE_FACE_MODULE})
    assert chains, (
        "写的那一半如今够不到网络了。要么 ``runs.py`` 那一族被改干净了（那是好消息，"
        "把这一条与本文件的目录说明一起改掉），要么 ``_network_reach`` 坏了 —— "
        "两种都要有人看一眼，而不是让一条恒真的判据留在这里。"
    )
    #: 链的最后一节是网络能力；第一节是起点。
    assert chains[0][0] == WRITE_FACE_MODULE
    assert chains[0][-1] == "marketops.ports.acquisition"


def test_the_network_ports_are_still_the_two_real_ones() -> None:
    """反缩水：这一条读的是**共享的**禁用清单，所以它要在这里被看第二眼。

    ``NETWORK_PORTS`` / ``NETWORK_SDKS`` 住在本文件之外，而它们是可以被改小的 ——
    改小之后上面那条闭包判据立刻全绿，而该联网的地方照旧在跑。

    这一族持有的三个端口**一个都不许**在这份清单里：它们是本仓的端口，不是网络能力。
    """
    assert NETWORK_PORTS == {"marketops.ports.search", "marketops.ports.acquisition"}
    for held in EFFECTIVE_SCENE_PORTS:
        assert held not in NETWORK_PORTS
        assert held.split(".")[-1] not in NETWORK_SDKS, (
            f"{held} 的尾名进了厂商清单 —— 这一族 import 的是本仓的端口，"
            "而厂商清单按顶层名判，两条口径混起来会误伤"
        )


def test_the_two_network_ports_are_recognized_by_their_whole_name() -> None:
    """反向读一遍禁用口径：端口按**全名**判。

    ``marketops.ports.workstreams`` 这类**名字里不带 search/acquisition** 的近邻不许被
    误伤；而 ``from ...ports import search`` 这种「先 import 包再取子模块」的写法绑定的
    东西与 ``from ...ports.search import X`` 是同一个，两条都必须被抓。
    """
    for source in (
        "from ...ports import search\n",
        "from ...ports.acquisition import Fetcher\n",
    ):
        chains = _network_reach(
            {"marketops.adapters.mcp_server.effective_scene": source},
            packages=(),
            start={READ_FACE_MODULE},
        )
        assert len(chains) == 1, f"这一行没被判成违规：{source!r} → {chains}"
    assert _network_names({"marketops.ports.workstreams", "marketops.ports.scenes"}) == set()


# --- 正反对照：打的是判据，不是解析器 -------------------------------------------


def test_the_network_criterion_catches_a_violation_from_this_start_set() -> None:
    """正对照：**这一族自己**的起点上 import 采集端口，必须被抓。

    ``tests/test_registration_is_offline.py`` 已经有一条正对照，但那条喂的起点是登记面。
    量具是同一个，而**起点集是本文件的主张** —— 一条只测 ``_network_reach`` 的正对照，
    在本文件的清单写错时仍然会绿。
    """
    chains = _network_reach(
        {READ_FACE_MODULE: "from ...ports.acquisition import Fetcher\n"},
        packages=(),
        start=(READ_FACE_MODULE,),
    )
    assert chains == [(READ_FACE_MODULE, "marketops.ports.acquisition")], (
        f"这一族的起点上，直接的违规没被抓到：{chains}"
    )


def test_the_network_criterion_catches_an_indirect_violation_at_length_three() -> None:
    """正对照：经**应用层**绕进去，必须同样被抓。

    链长三而不是二，因为这一族真实的形状就是这个：``effective_scene.py``（接线）→
    ``application.effective_scene`` → 某个端口。只判直接 import 的写法会漏掉这一条，
    且漏得**静默**。
    """
    chains = _network_reach(
        {
            READ_FACE_MODULE: "from ...application.effective_scene import preview_effective_scene\n",
            "marketops.application.effective_scene": "from ..ports.search import SearchProvider\n",
        },
        packages=(),
        start=(READ_FACE_MODULE,),
    )
    assert chains == [
        (
            READ_FACE_MODULE,
            "marketops.application.effective_scene",
            "marketops.ports.search",
        )
    ], f"经应用层绕进去的违规没被抓到：{chains}"


def test_a_clean_effective_scene_surface_is_not_flagged() -> None:
    """反转对照：真的干净的那一份不许报红。

    少了它，一条「见到什么都说违规」的判据也能通过上面两条正对照。这一份写的就是
    这一族真实的 import 面 —— 相对 import 四条、厂商 ``mcp`` 一条、``pydantic`` 一条，
    一个网络端口都不碰。
    """
    chains = _network_reach(
        {
            READ_FACE_MODULE: (
                "from mcp.server.mcpserver.exceptions import ToolError\n"
                "from pydantic import ValidationError\n"
                "from ...ports.product_catalog import ProductCatalog\n"
                "from ...ports.scenes import SceneStore\n"
                "from ...ports.workstreams import EffectiveSceneStore\n"
                "from ._shared import Handler, readable, require_scene, store_failure\n"
                "from .launch import _require_record, _require_version\n"
            ),
        },
        packages=(),
        start=(READ_FACE_MODULE,),
    )
    assert chains == [], f"干净的那一份被误判了：{chains}"


def test_the_port_criterion_catches_one_extra_port_and_one_missing_one() -> None:
    """正对照：端口闭包那一条**两个方向**都要会红。

    只喂「多一个端口」会漏掉另一种错法 —— 有人删掉一条 import 让端口表变小，
    于是「精确相等」在一张同样变小的表上仍然成立。所以两边各喂一次。
    """
    clean = {name: "" for name in EFFECTIVE_SCENE_PORTS}
    assert _ports_in(clean) == EFFECTIVE_SCENE_PORTS

    extra = {**clean, "marketops.ports.budget": ""}
    assert _ports_in(extra) - EFFECTIVE_SCENE_PORTS == {"marketops.ports.budget"}

    missing = {name: "" for name in clean if name != EFFECTIVE_PORT}
    assert EFFECTIVE_SCENE_PORTS - _ports_in(missing) == {EFFECTIVE_PORT}
    # 端口内的对象与 ``marketops.ports`` 这个包自己都不算一个端口。
    assert _ports_in({"marketops.ports", "marketops.ports.scenes.SceneStore"}) == frozenset()


def test_the_call_counting_probe_catches_a_write_in_the_preview() -> None:
    """正对照：`_preview` 里多一次 ``effective()`` 必须被数出来。

    喂的是这一族真实的形状（一个工厂里两个嵌套 def），因为「嵌套 def 里的调用算谁的」
    正是 `_own_nodes` 存在的原因 —— 一条只在外层函数上试过的正对照，会在嵌套那一层
    悄悄数错。
    """
    source = """
        def effective_scene_handlers(*, effective):
            def preview_effective_scene():
                return effective()

            def get_effective_scene(version):
                return effective().get_scene(version)

            return {"preview_effective_scene": preview_effective_scene}
    """
    assert _callers_of(source, "effective") == ["get_effective_scene", "preview_effective_scene"]
    assert _callers_of(source, "utc_now") == []
    # 干净的那一份：只读的那一条用一次（**逐字**另写一份，而不是把上一段锯掉一截 ——
    # 锯出来的那一份是个空函数体，它自己不合法，而一条喂不进去源码的正对照什么都没测）。
    clean = """
        def effective_scene_handlers(*, effective):
            def preview_effective_scene():
                return None

            def get_effective_scene(version):
                return effective().get_scene(version)

            return {"preview_effective_scene": preview_effective_scene}
    """
    assert _callers_of(clean, "effective") == ["get_effective_scene"]
