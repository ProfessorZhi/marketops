"""B24 那一族的**构造面**：零 provider / 零 Run、签名两侧不对称、一个定时器都没有。

这个文件判的不是行为，是**这一族拿得到什么**。行为判据住在
``tests/test_mcp_views.py``（工具面）与 ``tests/test_views_application.py``
（编排面）；这里判的是那两份判据**够不着**的那三件事 —— 一份行为正确的实现可以在
这三格上全是错的，而错的方式是静默的。

## 三条，逐条对应一个承重点

**一、``run_saved_view`` 的零 provider / 零预算 / 零 Run 是一处构造。**
不是为了省一次调用，是这一族的**全部理由**：一份视图是一次查询，它读的是**已经在
内存里的**一批账号。所以判据不能在「它今天没调用搜索」上 —— 那是一个会随实现变远的
距离。判的是这一族到不到得了那个端口。口径与 ``tests/test_mcp_launch_is_offline.py``
逐字同源（那一份是 B01 第四条关闭条件）：网络能力 = 真的会对外发起请求的两条端口，
**或起一个外部进程**的厂商 SDK。

**二、保存视图那一侧拿不到账本。** §5.3 逐字：「启用 Watch 是写操作，需要权限和预算；
**保存 View 不是**」。这条承重点的**签名**那一半住在 ``tests/test_mcp_views.py``
（两张形参表逐格相等），这里判的是**构造**那一半：``views`` 那一族的 import 闭包里
没有 ``marketops.ports.budget``。两条判的是不同的问题 —— 一个把账本经**第三个形参**
（比如某个 ``services`` 袋子）递进来的实现会从签名那条底下钻过去，但从闭包这条钻不过去。

**三、这一片一个定时器都没有。** Watch 有自己的 ``next_due_at``，而它**不是**一个
调度器：全仓唯一的 trigger driver 是调度那一片的 ``schedule_definition`` /
``fire_schedule``（``db.py`` 的 ``schedule_fire`` 表注释逐字：「外部 cron、Hosting、
ServerOS 以后都只能作为触发者，不是真相来源」）。一个自己「每天看一眼」的 Watch
在行为上与一个被驱动的 Watch **读起来一模一样**，直到有一天两个驱动同时醒着 ——
所以这一条判的是**有没有能力起一个定时器**（``time.sleep`` / ``threading`` /
``asyncio`` / ``sched`` …），不是「它今天有没有睡」。

## 反面：这三条都必须各自带一个「真的读了盘」的要求

三条都是**否定**（到不了 / 拿不到 / 没有），而否定句可以靠一个什么都不做的空壳达成。
所以每一条都配一条反面：闭包里**必须真的有** ``ports.views``（一份视图得从盘上读
那一版声明），followups 那一族**必须真的有** ``ports.budget`` 与 ``ports.scenes``。
少了反面，「离线」与「没读数」在判据上同形 —— 这正是 ``test_mcp_launch_is_offline``
里 ``test_the_launch_surface_holds_the_scene_store`` 存在的理由。

## 这一条判不到什么

与 ``tests/test_registration_is_offline.py`` 逐字同一条上限：判不出「这一族用了一个
不在清单里的方式联网」（自己拼 ``os.system``、或者经 ``ctypes``）。另外**这一条的
白名单只盖两层**：

* 网络那条走的是**传递闭包** —— 真的到不了就是到不了；
* 「恰好这几个端口」那条只对 ``marketops.ports.*`` 精确相等，**不是**对整个闭包精确
  相等。理由是这一族的闭包里有 ``marketops.security.catalog`` / ``marketops.config``
  / ``marketops.domain.*``，而其中几个是 MAIN 独占的文件（本片**不能改**）。对整个闭包
  精确相等会让这一片的绿变成「MAIN 今天恰好没加 import」的函数 —— 一条红在那边、
  而这一边修不了的判据，比一条有界的判据更坏。端口那一层是这一族真正的主张所在，
  也是它能自己负责的那一层。
* 定时器那条只扫**这一片点名的模块**（下面 ``TIMER_FREE_SURFACE``），不扫闭包。
  同一条理由。
"""

from __future__ import annotations

import ast
from collections.abc import Collection, Mapping
from pathlib import Path

from tests.test_adapters_hold_the_vendors import (
    _imported_modules,
    _module_name,
    _package_name,
)
from tests.test_registration_is_offline import (
    NETWORK_PORTS,
    NETWORK_SDKS,
    _network_reach,
    _on_disk,
)

REPO_ROOT = Path(__file__).resolve().parents[1]

#: 视图那一族（前四条工具）**生产路径上的入口**。
#:
#: 只列入口，不列被它们拉进来的 —— 闭包那一半自动覆盖。``application/views.py`` 是
#: 这一片四个文件的共同编排层，两个清单里都有它：它既在 ``run_saved_view`` 的路上，
#: 也在 ``put_watch`` 的路上，而承重点二（只有一半要账本）落点就在它里面。
VIEWS_SURFACE: tuple[str, ...] = (
    "marketops/adapters/mcp_server/views.py",
    "marketops/application/views.py",
)

#: 监控与跟进那一族（后四条工具）的入口。两个族共用编排层，差别在适配器那一层 ——
#: 而 ``test_the_two_surfaces_differ_by_exactly_the_ledger`` 判的就是这个差别。
FOLLOWUPS_SURFACE: tuple[str, ...] = (
    "marketops/adapters/mcp_server/followups.py",
    "marketops/application/views.py",
)

#: 承重点三判的是**这一片自己写的模块**里有没有定时器，所以它扫的清单与上面两个不同：
#: 加上两份领域对象（Watch 与 ActionItem 是这一片的两条记录）。闭包那一层不扫，
#: 理由在模块 docstring 的最后一段。
TIMER_FREE_SURFACE: tuple[str, ...] = (
    "marketops/adapters/mcp_server/followups.py",
    "marketops/adapters/mcp_server/views.py",
    "marketops/application/views.py",
    "marketops/domain/watch.py",
    "marketops/domain/action_item.py",
)

#: 这一族**必须**持有的端口，逐族写出。**精确相等**，不是「至少包含」。
#:
#: 视图那一族三个：``ports.views``（那一版声明本身）、``ports.pool``（内存里的那批
#: 账号）、``ports.scenes``（编排层判「这条 Watch 挂的场景能不能写」要读它）。
#: followups 那一族在这三个之上多一个 ``ports.budget`` —— 那正是下面
#: ``test_the_two_surfaces_differ_by_exactly_the_ledger`` 的那一格。
#:
#: ``ports.runs`` **不在**任何一份里，而这是零 Run 那句的落点：跑一份视图连一个
#: ``RunSpec`` 都构造不出来。``ports.search`` / ``ports.acquisition`` 同理（零 provider，
#: 这一格由上面那条闭包判据再判一遍，两条判的是「能力」与「恰好这几个」两件事）。
VIEWS_PORTS: frozenset[str] = frozenset(
    {
        "marketops.ports.pool",
        "marketops.ports.scenes",
        "marketops.ports.views",
    }
)
FOLLOWUPS_PORTS: frozenset[str] = VIEWS_PORTS | {"marketops.ports.budget"}

#: 这一族**唯一**允许多出来的那一个端口：账本。承重点一的那条界线。
LEDGER_PORT = "marketops.ports.budget"

#: 会起一个定时器 / 一个后台线程的模块，按**顶层名**判。
#:
#: ``time`` 排第一不是因为它可疑，是因为 ``time.sleep`` 是「每天看一眼」最短的那条路；
#: ``signal`` / ``select`` 是同一件事的两条不常见但仍然好走的路（``alarm`` /
#: ``setitimer`` 起的就是定时器）。``datetime`` **必须不在**这份清单里 —— 这一族真的
#: 要用它（``next_due_at`` 是一列时间，不是一个闹钟），判成定时器会让判据当场假红。
TIMER_MODULES: frozenset[str] = frozenset(
    {
        "time",
        "threading",
        "asyncio",
        "sched",
        "concurrent",
        "multiprocessing",
        "signal",
        "select",
        "selectors",
        "apscheduler",
        "celery",
        "dramatiq",
        "rq",
        "huey",
    }
)

#: 定时器的**名字**。按全名判（``node.attr`` / ``node.id``），不是子串 ——
#: ``Event`` 在 ``domain.action_item`` 里是一个状态名，不是 ``threading.Event``，
#: 所以这一份只收 ``sleep`` / ``Timer`` / ``setitimer`` 这种**没有第二种读法**的名字。
TIMER_NAMES: frozenset[str] = frozenset(
    {
        "sleep",
        "Timer",
        "setitimer",
        "alarm",
        "run_forever",
        "call_later",
        "call_at",
        "create_task",
        "ensure_future",
    }
)

#: 第二个 driver 的三个入口。这一族**一个都不许** import：Watch 的到期是**被问**出来的
#: （``list_watches`` 报 ``due``），不是被这一族自己算出来再拉起的。
DRIVER_MODULES: frozenset[str] = frozenset(
    {
        "marketops.ports.scheduling",
        "marketops.application.scheduling",
        "marketops.adapters.mcp_server.scheduling",
    }
)


# --------------------------------------------------------------------------- 量具


def _reachable(
    sources: Mapping[str, str],
    *,
    packages: Collection[str],
    start: Collection[str],
) -> set[str]:
    """从 ``start`` 出发，沿仓库内 import 走到的**全部模块**（含起点）。

    与 ``tests/test_registration_is_offline._network_reach`` 同一个不动点，只是它停在
    网络能力上，这一条不收手。抽成纯函数（而不是藏在走目录的循环里）是为了让正反对照
    打的是**判据**而不是解析器 —— 喂一份假的模块图进去，含违规的那一份必须吐出来。
    一条只测 ``ast`` 的正对照，在判据恒假时仍然会绿。
    """
    imports: dict[str, set[str]] = {}
    for name, source in sources.items():
        package = name if name in packages else name.rpartition(".")[0]
        imports[name] = _imported_modules(
            ast.parse(source, filename=name), package=package
        )
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
    """闭包里那些 ``marketops.ports.*`` 的全名。

    **只取端口这一层**：``marketops.ports`` 这个包自己不收（它是一个包，不是一个
    端口），``marketops.ports.views.X`` 这种「端口模块里的一个对象」也不收。
    理由是同一条：判的是**这一族拿到了哪个端口**，别的层次的名字进来会让这条判据
    变成一份会随端口内部结构变动的清单。
    """
    return frozenset(
        name
        for name in closure
        if name.startswith("marketops.ports.")
        and name.count(".") == 2
    )


def _timers_in(name: str, source: str) -> tuple[list[str], list[str]]:
    """一份源码里的定时器证据：``(模块, 名字)``。

    两把尺子合起来用：**import 到的模块**（``import time`` / ``from time import sleep``
    / ``import sched``）与**出现的名字**（``time.sleep(60)`` / ``threading.Timer`` /
    ``signal.setitimer``）。只判前者会漏掉 ``from time import sleep as _nap``；只判
    后者会漏掉一个 ``import threading`` 之后经 ``getattr`` 取的东西。

    函数内的 import 也算 —— ``_imported_modules`` 走的是整棵 AST，而延迟导入**恰恰**
    是最容易被顺手挪进业务代码的形态（那句话逐字抄自 ``VENDOR_MODULES`` 那段）。
    """
    tree = ast.parse(source, filename=name)
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.split(".")[0] in TIMER_MODULES:
                    modules.add(alias.name)
        elif isinstance(node, ast.ImportFrom):
            if node.level == 0 and node.module:
                if node.module.split(".")[0] in TIMER_MODULES:
                    modules.add(node.module)
            for alias in node.names:
                if alias.name in TIMER_NAMES:
                    modules.add(f"{node.module or ''}.{alias.name}")
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and node.attr in TIMER_NAMES:
            names.add(node.attr)
        elif isinstance(node, ast.Name) and node.id in TIMER_NAMES:
            names.add(node.id)
    return sorted(modules), sorted(names)


# --------------------------------------------------------------------------- 清单


def test_the_work_pool_surfaces_are_on_disk() -> None:
    """三份清单里的每一项都必须真的在盘上。

    没有这一条，路径写错一个字符就会被读成「那个模块很干净」—— 一个不存在的模块
    当然不 import 任何东西，也当然没有定时器。判据在这里**红**，而不是静默放行。
    """
    listed = set(VIEWS_SURFACE) | set(FOLLOWUPS_SURFACE) | set(TIMER_FREE_SURFACE)
    missing = sorted(rel for rel in listed if not (REPO_ROOT / rel).is_file())
    assert not missing, (
        f"工作池这一片点名的模块不在盘上：{missing}。"
        "要么路径写错了，要么这一片还没落 —— 两种都要在这里显形。"
    )
    #: 空清单会让下面每一条一起变成恒真（一个不存在的起点当然到不了任何地方）。
    assert len(VIEWS_SURFACE) == 2, f"视图面清单变成了 {VIEWS_SURFACE}"
    assert len(FOLLOWUPS_SURFACE) == 2, f"followups 面清单变成了 {FOLLOWUPS_SURFACE}"
    assert len(TIMER_FREE_SURFACE) >= 3, f"定时器面清单缩水了：{TIMER_FREE_SURFACE}"


# ------------------------------------------------------- 一、零 provider / 零 Run


def test_the_work_pool_never_reaches_the_network() -> None:
    """本体：两个族都到不了任何网络能力，直接或间接都到不了。

    收的是**能力**（两条端口 + 那批会起外部进程或对外发请求的 SDK），不是「联网」
    这个词 —— 口径逐字同源 ``tests/test_registration_is_offline.py``。
    """
    sources, packages = _on_disk()
    start = {
        _module_name(REPO_ROOT / rel)
        for rel in set(VIEWS_SURFACE) | set(FOLLOWUPS_SURFACE)
    }
    # 前置：起点必须真的在模块图里。少了这一句，路径写错会让 `_network_reach` 拿到一个
    # 它不认识的起点**静默**返回空 —— 「没有违规」于是变成「没在判」。
    assert start <= set(sources), (
        f"工作池清单解析出的模块名不在仓库模块图里：{sorted(start - set(sources))}"
    )
    chains = _network_reach(sources, packages=packages, start=start)
    assert not chains, (
        "工作池这一族能拿到网络能力，路径如下（最后一节是网络能力的名字）：\n"
        + "\n".join("  → ".join(chain) for chain in chains)
        + "\n一份视图读的是**已经在内存里的**那批账号；一次 ``run_saved_view`` "
        "不许花一次钱、不许联网、不许在断网时失败。"
    )


def test_the_views_surface_holds_exactly_these_ports() -> None:
    """承重点一之半边 + 承重点二：视图那一族拿到的端口**恰好**是这三个。

    「恰好」而不是「至少」是这一条的**全部**力量：零 provider（``ports.search`` /
    ``ports.acquisition``）、零预算（``ports.budget``）、零 Run（``ports.runs``）
    三句话在这里是同一条断言 —— 三个名字一个都不在这份集合里，而三个名字在闭包里
    都会**立刻显形**（一条新增的 import 就够）。

    反面那一半在同一个集合里：``ports.views`` 与 ``ports.pool`` 必须**在**。
    少了反面，一个什么都不读的空壳能通过「到不了网络」那条判据 —— 那不叫离线，
    那叫没读数。
    """
    sources, packages = _on_disk()
    start = {_module_name(REPO_ROOT / rel) for rel in VIEWS_SURFACE}
    closure = _reachable(sources, packages=packages, start=start)
    held = _ports_in(closure)
    assert held == VIEWS_PORTS, (
        f"视图那一族的端口集合变了。\n  盘上：{sorted(held)}\n  判据：{sorted(VIEWS_PORTS)}\n"
        "多出来的那几个要看是不是 ``ports.search`` / ``ports.acquisition``（零 provider）、"
        "``ports.budget``（这一族**不许**有账本，§5.3 逐字）、``ports.runs``（零 Run）；"
        "少掉的那几个要看这一族是不是还能真的读到那一版声明与那批账号。"
    )


def test_the_followups_surface_holds_exactly_these_ports() -> None:
    """另一半：监控那一族**要**账本，而且它也读场景与视图。

    反面在这里比上面那条更要紧：一张「没有账本」的监控是可以建的 —— 它只是从不预留
    预算。所以 ``ports.budget`` 必须**在**这份集合里，它的缺席与 ``ports.runs``
    的缺席是两件完全不同的事，而两份集合的**差**正是下面那一条判据。
    """
    sources, packages = _on_disk()
    start = {_module_name(REPO_ROOT / rel) for rel in FOLLOWUPS_SURFACE}
    closure = _reachable(sources, packages=packages, start=start)
    held = _ports_in(closure)
    assert held == FOLLOWUPS_PORTS, (
        f"followups 那一族的端口集合变了。\n  盘上：{sorted(held)}\n"
        f"  判据：{sorted(FOLLOWUPS_PORTS)}\n"
        "``ports.budget`` 少了 = 启用 Watch 不再要预算（§5.3 逐字要求它要）；"
        "``ports.scenes`` 少了 = 「这条 Watch 挂的场景还是活的吗」没有读者。"
    )


def test_the_two_surfaces_differ_by_exactly_the_ledger() -> None:
    """承重点一：两个族的端口集合**只差账本那一格**。

    这是 §5.3 那两句话在 import 层的落点，而它比两条独立的「恰好相等」更接近那句话
    的形状 —— 那句话说的正是**两份签名的不同**（「保存 View 不是」），不是两份各自
    长什么样。一个把账本同时给两个族的实现会让上面两条判据**一起变绿**吗？不会：
    上面两条各自钉死了自己的集合，这一条钉的是**差**，三者冗余而不重复。

    ``set()`` 那一半同样承重：``VIEWS_PORTS - FOLLOWUPS_PORTS`` 非空意味着视图那一族
    拿到了监控那一族没有的东西 —— 那是一个「保存视图顺手做了一件额外的事」的形状。
    """
    assert FOLLOWUPS_PORTS - VIEWS_PORTS == {LEDGER_PORT}, (
        "两个族的端口差不再是账本那一格："
        f"{sorted(FOLLOWUPS_PORTS - VIEWS_PORTS)}"
    )
    assert VIEWS_PORTS - FOLLOWUPS_PORTS == frozenset(), (
        "视图那一族拿到了监控那一族没有的端口："
        f"{sorted(VIEWS_PORTS - FOLLOWUPS_PORTS)}。保存一份视图比开一条监控读得**更少**，"
        "不是更多。"
    )


def test_the_views_surface_never_names_the_ledger() -> None:
    """同上，但判的是**直接**的写法：两份源码里连 ``BudgetLedger`` 这个名字都不出现。

    与 ``test_the_views_surface_holds_exactly_these_ports`` 是两个不同的问题，两条都
    要在：上面那条判的是**闭包**（够不够得到那个模块），这一条判的是**形状**（有没有
    把那条路接上）。一份 ``from ...ports import budget`` 两条都会红；一份只在注解里
    写了个 ``"BudgetLedger"`` 字符串的会让上面那条绿、这一条红。

    这条绕路不是洁癖：一个拿得到账本的 ``put_saved_view`` 在**读取路径**上多了一格
    可以顺手 ``set_ceiling`` 的位置，而那件事发生的时候没有一条行为判据会响 ——
    它只是一次多出来的写入。
    """
    sources, _ = _on_disk()
    for rel in VIEWS_SURFACE:
        name = _module_name(REPO_ROOT / rel)
        assert name in sources, f"{rel} 不在仓库模块图里"
        tree = ast.parse(sources[name], filename=name)
        named = sorted(
            {
                node.attr if isinstance(node, ast.Attribute) else node.id
                for node in ast.walk(tree)
                if (
                    isinstance(node, ast.Attribute)
                    and node.attr in {"BudgetLedger", "budget"}
                )
                or (
                    isinstance(node, ast.Name)
                    and node.id in {"BudgetLedger", "budget"}
                )
            }
        )
        assert not named, (
            f"{name} 里出现了 {named}。保存一份视图**不要预算**（§5.3 逐字："
            "「保存 View 不是」）—— 这一族唯一的读者是 ``saved_view`` 与账号池，"
            "账本在隔壁那一族（``adapters/mcp_server/followups.py``）。"
        )


# ------------------------------------------------------------------ 三、没有定时器


def test_the_followup_surface_has_no_timer() -> None:
    """承重点三：这一片的模块里没有任何一条起定时器 / 后台线程的路。

    Watch 有 ``next_due_at``，而它**不是**一个闹钟：那个字段是**被别人问**的
    （``list_watches`` 把到期的报成 ``due``），这一族自己从不睡、从不醒。
    ``db.py`` 的 ``schedule_fire`` 表注释逐字：「外部 cron、Hosting、ServerOS 以后都
    只能作为触发者，不是真相来源」—— 真相是那一列时间，驱动是调度那一片。

    一个自己「每天看一眼」的实现与一个被驱动的实现在**行为上**读起来一模一样，
    直到两个驱动同时醒着。所以这一条判的是**能力**，不是「它今天有没有睡」。
    """
    sources, _ = _on_disk()
    offenders: dict[str, tuple[list[str], list[str]]] = {}
    for rel in TIMER_FREE_SURFACE:
        name = _module_name(REPO_ROOT / rel)
        assert name in sources, f"{rel} 不在仓库模块图里"
        modules, names = _timers_in(name, sources[name])
        if modules or names:
            offenders[name] = (modules, names)
    assert not offenders, (
        "这一片里出现了定时器：\n"
        + "\n".join(
            f"  {name}：import {mods}，名字 {names}"
            for name, (mods, names) in sorted(offenders.items())
        )
        + "\n到期的判定在 ``marketops/domain/watch.py``（纯函数），驱动在调度那一片的 "
        "``schedule_definition`` / ``fire_schedule``。这一族只**报**到期，不**叫醒**自己。"
    )
    # 前置：清单本身没被缩到只剩一个文件（那样上面那个循环近似恒真）。
    assert len(TIMER_FREE_SURFACE) >= 3, f"定时器面清单缩水了：{TIMER_FREE_SURFACE}"


def test_the_followup_surface_does_not_import_a_second_driver() -> None:
    """同一个承重点的另一半：这一族**直接** import 的名字里没有调度那一片。

    上面那条判的是「有没有能力自己起一个定时器」，这一条判的是「有没有把别人的
    driver 接进来 —— 或者更坏，自己长一个」。两条不同：一个 ``import
    marketops.application.scheduling`` 的实现**不一定**会用到 ``time``（它调的可能是
    别人写好的 ``fire_due``），但它已经把这一族的到期判定绑到了第二个 driver 上。
    """
    sources, _ = _on_disk()
    for rel in TIMER_FREE_SURFACE:
        name = _module_name(REPO_ROOT / rel)
        tree = ast.parse(sources[name], filename=name)
        touched = _imported_modules(tree, package=_package_name(REPO_ROOT / rel))
        named = sorted(touched & DRIVER_MODULES)
        assert not named, (
            f"{name} 直接 import 了 {named}。全仓唯一的 trigger driver 是调度那一片的 "
            "``schedule_definition`` / ``fire_schedule``；Watch 的到期在这一族里是一个"
            "**被问出来的读数**，不是一次被这一族发起的触发。"
        )


# ------------------------------------------------------------------------- 反缩水


def test_the_shared_forbidden_sets_do_not_shrink() -> None:
    """这一条读的是**共享的**禁用清单，所以它要在这里被看第二眼。

    ``NETWORK_PORTS`` / ``NETWORK_SDKS`` 住在本文件之外（``tests/test_registration_is_offline.py``），
    ``TIMER_MODULES`` 住在本文件里 —— 三者都可以被改小，而改小之后上面每一条判据
    立刻全绿，该联网 / 该睡的代码照旧在跑。

    ``test_registration_is_offline.test_the_forbidden_set_does_not_shrink`` 已经钉住了
    网络那份清单本身的形状，所以这里只判**与这一族有关的那几格**，不重抄那一份。
    """
    assert NETWORK_PORTS == {"marketops.ports.search", "marketops.ports.acquisition"}, (
        "网络端口清单缩水了 —— 这一族的「零 provider」正是靠它来判的。"
    )
    for required in ("httpx", "subprocess", "playwright"):
        assert required in NETWORK_SDKS, f"{required} 从网络 SDK 清单里掉了"
    #: 这一族真的持有的端口，一个都不许被算成网络 —— 混起来会让判据当场假红，
    #: 而假红的处置通常是去改判据，于是真的一条也没了。
    for held in sorted(VIEWS_PORTS | FOLLOWUPS_PORTS):
        assert held not in NETWORK_PORTS, f"{held} 被算成了网络端口"
    #: 定时器清单的每一组都必须有人：``time``/``threading``/``asyncio``/``sched`` 是
    #: 四条不同的路，删掉一条，走那条路的实现就再也判不出来。
    for required in ("time", "threading", "asyncio", "sched"):
        assert required in TIMER_MODULES, f"{required} 从定时器清单里掉了"
    #: ``datetime`` **必须不在**：这一族真的要它（``next_due_at`` / ``updated_at``）。
    assert "datetime" not in TIMER_MODULES, (
        "``datetime`` 进了定时器清单 —— 一列时间不是一个闹钟，"
        "这条判据会从此再也过不去（而它判的正是这一片的时间字段）。"
    )
    assert "marketops.ports.scheduling" in DRIVER_MODULES


# ------------------------------------------------------------------- 正反对照


def test_the_network_criterion_catches_a_violation_from_these_start_sets() -> None:
    """正对照：**这一族自己**的起点上 import 采集端口，必须被抓。

    ``tests/test_registration_is_offline.py`` 已经有一条正对照，但那条喂的起点是登记面。
    量具是同一个，而**起点集是本文件的主张** —— 一条只测 ``_network_reach`` 的正对照，
    在本文件的清单写错时仍然会绿。
    """
    chains = _network_reach(
        {
            "marketops.adapters.mcp_server.followups": (
                "from ...ports.acquisition import Fetcher\n"
            ),
        },
        packages=(),
        start=("marketops.adapters.mcp_server.followups",),
    )
    assert chains == [
        (
            "marketops.adapters.mcp_server.followups",
            "marketops.ports.acquisition",
        )
    ], f"这一族的起点上，直接的违规没被抓到：{chains}"


def test_the_network_criterion_catches_an_indirect_violation_at_length_three() -> None:
    """正对照：经**应用层**绕进去，必须同样被抓。

    链长三而不是二，因为这一族真实的形状就是这个：``followups.py`` →
    ``application.views`` → 某个端口。只判直接 import 的写法会漏掉这一条，
    且漏得**静默**。
    """
    chains = _network_reach(
        {
            "marketops.adapters.mcp_server.followups": (
                "from ...application.views import put_watch\n"
            ),
            "marketops.application.views": "from ..ports.search import SearchProvider\n",
        },
        packages=(),
        start=("marketops.adapters.mcp_server.followups",),
    )
    assert chains == [
        (
            "marketops.adapters.mcp_server.followups",
            "marketops.application.views",
            "marketops.ports.search",
        )
    ], f"经应用层绕进去的违规没被抓到：{chains}"


def test_the_port_criterion_catches_one_extra_port_and_one_missing_one() -> None:
    """正对照：``_reachable`` + ``_ports_in`` 对一个**多一格**与一个**少一格**都要响。

    上一条正对照打的是 ``_network_reach``（共享量具）。这一条打的是本文件自己写的那
    两件：一条只测共享量具的正对照，在 ``_reachable`` 恒返回 ``start`` 或者
    ``_ports_in`` 恒返回空集时**照样绿** —— 而那两件一坏，「恰好这几个端口」就变成了
    一句恒真的话。
    """
    graph = {
        "marketops.adapters.mcp_server.views": (
            "from ._shared import Handler\n"
            "from ...application.views import put_saved_view\n"
        ),
        "marketops.adapters.mcp_server._shared": "",
        "marketops.application.views": "from ..ports.views import SavedViewStore\n",
        "marketops.ports.views": "",
        "marketops.ports.budget": "",
    }
    packages = {"marketops", "marketops.adapters", "marketops.ports"}
    clean = _reachable(
        graph,
        packages=packages,
        start=("marketops.adapters.mcp_server.views",),
    )
    assert _ports_in(clean) == {"marketops.ports.views"}, (
        f"干净的那一份被判错了：{sorted(_ports_in(clean))}"
    )
    #: 多一格：账本经应用层递进来，必须显形。
    graph["marketops.application.views"] = (
        "from ..ports.views import SavedViewStore\nfrom ..ports.budget import BudgetLedger\n"
    )
    dirty = _reachable(
        graph,
        packages=packages,
        start=("marketops.adapters.mcp_server.views",),
    )
    assert _ports_in(dirty) == {"marketops.ports.views", "marketops.ports.budget"}, (
        f"多出来的那一格没被看见：{sorted(_ports_in(dirty))}"
    )
    #: 少一格：一个闭包里只有起点的图必须回空集，而不是回起点自己 ——
    #: 「没在判」与「没有端口」必须是两条不同的读数。
    empty = _reachable({}, packages=packages, start=("marketops.application.views",))
    assert empty == set()
    assert _ports_in(empty) == frozenset()


def test_the_timer_criterion_catches_each_way_of_getting_a_timer() -> None:
    """正对照：定时器那三把尺子各自的典型写法，一个都不许漏。

    只测 ``import time`` 的正对照会让 ``from time import sleep as _nap`` 与
    ``threading.Timer(...)`` 两条路静默漏掉 —— 而后者恰好是「每天看一眼」最自然的写法。
    """
    caught = (
        "import time\n",
        "from time import sleep\n",
        "from time import sleep as _nap\n",
        "import threading\n",
        "from threading import Timer\n",
        "import asyncio\n",
        "import sched\n",
        "from apscheduler.schedulers.background import BackgroundScheduler\n",
        "def tick():\n    time.sleep(60)\n",
        "def tick():\n    alarm(60)\n",
    )
    for source in caught:
        modules, names = _timers_in("fake", source)
        assert modules or names, f"这一条没被判成定时器：{source!r}"
    #: 函数体里的 import 也算 —— 延迟导入恰恰最容易被顺手挪进业务代码。
    modules, _ = _timers_in("fake", "def tick():\n    import sched\n")
    assert modules, "函数体内的 ``import sched`` 漏掉了"


def test_a_clean_work_pool_source_is_not_flagged() -> None:
    """反转对照：真的干净的那一份不许报红。

    少了它，一条「见到什么都说违规」的判据也能通过上面三条正对照。这里写的是这一族
    真实的形状 —— ``datetime`` / ``ToolError`` / 端口 / 领域对象，一个定时器都不碰。
    """
    clean = (
        "from __future__ import annotations\n"
        "from datetime import datetime\n"
        "from mcp.server.mcpserver.exceptions import ToolError\n"
        "from ...domain.watch import Watch, watch_budget_scope\n"
        "from ...ports.budget import BudgetLedger\n"
        "from ...ports.views import WatchStore\n"
        "from ._shared import Handler, readable\n"
        "def put(**kwargs: object) -> None:\n"
        "    due = kwargs.get('next_due_at')\n"
        "    if isinstance(due, datetime):\n"
        "        return None\n"
        "    raise ToolError('no')\n"
    )
    modules, names = _timers_in("fake", clean)
    assert (modules, names) == ([], []), (
        f"干净的那一份被误判了：import {modules}，名字 {names}"
    )
    #: ``datetime`` 真的要用它自己，而它不在清单里 —— 判的是全名，不是子串。
    assert "datetime" not in TIMER_MODULES
