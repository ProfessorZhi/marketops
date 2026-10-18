"""**触发驱动**的一次醒来：把到期的东西推进到「受理了一轮 Run」，然后退出。

    python -m marketops.interfaces.trigger_driver [--at <ISO 8601>] [--limit N]

## 为什么需要一个进程，而不是一条工具

G2 那一节逐字给了一条链，中间有这一句：

    断开全部控制客户端 → 下一批/Watch按Schedule自动执行 → 重启控制器 → 重放事件 → …

**「断开全部控制客户端」+「自动执行」= 那一刻没有任何 MCP 客户端在调用。**
于是「由某个客户端顺手调一次 `tick`」**在构造上就不可能是**这条链的承载 ——
不是不优雅，是那句话根本做不到。所以驱动必须是一个**进程**，而它就是这一个。

这不是本模块自己的发明，是架构建模里已经写着的东西：

* ``adapters/mcp_server/scheduling.py:15`` 逐字：「``tick``（唯一的触发驱动的一步）
  **根本没有工具面，它是驱动进程调的**。」
* ``:10-13`` 逐字：每一条工具「都还有一个**非 MCP 的调用方**」，而 ``fire_schedule``
  的那几个「是**外部 cron / Hosting / ServerOS**……**它们不走 MCP**」。
* ``domain/scheduling.py`` 的 :func:`~marketops.domain.scheduling.due_fire_id` 逐字：
  §8 ``:320``「**外部 cron、Hosting、ServerOS 以后都只能作为 trigger driver**……
  重复 fire 幂等」。

**那个驱动被设计在仓库之外**（cron / Hosting / ServerOS 各看各的表）。本模块是它在
**本仓里**的那一半：一个可以被它们中的任何一个调起来的、一次性的、说得出自己做了
什么的进程。**它不是第二套调度器，它就是 ``application.scheduling.tick`` 的入口。**

## 为什么是**一次性**的，为什么这里没有循环

**本仓不许有定时器**，而这条纪律有执行器：``tests/test_scheduling_mcp.py::
test_no_timer_lives_in_the_scheduling_surface`` 扫的就是
``application/scheduling.py`` 与那个适配器的 import 与源码。本模块遵守同一条 ——
不 import ``threading`` / ``asyncio`` / ``sched`` / ``signal`` / ``time``，
没有一处 ``sleep``，**醒来一次就退出**。

节奏属于**外面**那个驱动：它一分钟调一次，本模块就一分钟醒一次；它不调，就没有
任何东西在跑。把节奏放进本模块会造出第二条「谁保证它活着」的运维事实，而架构里
已经有了一条（那条由部署方持有）。

## 维护面（Watch 到期 → due work）**不在**这里，也不该在这里

G2 那一句「断开全部控制客户端 → 下一批/Watch 按 Schedule 自动执行」有**两个面**，
本模块只接**一个**：

* **调度面**（`Schedule` 到期 → 一行 `RESEARCH_RUN`）—— 就是本模块。一次性的：
  醒来、点火、退出。
* **维护面**（`Watch` 到期/信号 → 认领那一格 → 做工作）—— **不在本模块**。承载者是
  ``interfaces/controller.py`` 的**常驻控制器**（``run_resident``，它自己就是一个
  循环；``tests/test_scheduling_mcp.py`` 逐字把常驻控制器判为「**合法**」用循环的
  那一处）。

把维护面挪进来会造出**第二条**「谁保证它活着」的运维事实 —— 正是上面那一节逐字
警告的事。维护面要的答案是**部署态**，而那个答案今天已经被常驻控制器持有；再造
一个，运维上会有两个东西都声称自己在跑维护。

**这一条今天成立且可查**：``interfaces/controller.py`` 的
``LiveContinuationExecutor.maintenance_tick``（``:224``）调
``application.maintenance.run_once``（``:292``），后者逐格
``claim_of`` → ``store.claim`` → ``work(due)``（``application/maintenance.py``）。
那条路**不需要** ``ResearchContext`` —— 它只要一个 ``work`` 回调，而回调由控制器
自己装配。

## 一条**接不上**的路，记在这里免得下一个人重走

``application/watch_runs.py::run_due_watches``（§17 的第四格）走的是**另一条**：
到期的 ``WatchDueRequest`` → ``admit_run(execution_mode=WATCH, ...)`` →
**一行 ``watch`` Run**。它比维护面**高一层** —— 维护面只是「做那一格」，它要把
那一格记成一行 Run 账。

它今天**没有生产入口**，而缺的那一样**不是存储、也不是场景号**（六个存储工厂都在
``composition.py`` 里，``scene_id`` 也能从 ``SceneStore.records(workspace=...)``
定序拿到）：

``admit_run`` 要一份 ``ResearchContext`` 与一份 ``EffectiveResearchPlan``。而
``ResearchContext`` 的 docstring 逐字说它装的是「一次研究**要什么**」——
「**由 Member-A/客户端给出**」，且 ``geo`` / ``entity_classes`` / ``asset_classes`` /
``organization_classes`` / ``objective`` **空着不是「不限」，是「没说」**；把它们
读成「不限」正是 §B16 逐字要防的「历史模型猜测不能自动扩大范围」（「一份只写了
objective 的声明会编译出一次『全英国所有组织』的研究，而回执上看起来一切正常」）。

而这一层**拿不到**那份请求：``Watch`` 上的字段是「看谁」（``subjects``）、
「看哪些面」（``aspects``）、「花多少」（``ceilings``）、「通知谁」
（``notify_on``）—— **没有一个是「要什么」**；它绑的 ``SavedView.query`` 是一个
**不透明的 ``dict[str, Any]``**，从它推 ``context`` 那几格是**猜**。

所以本模块**不编** —— 编一份「看起来像请求」的 ``context`` 会让一次无人值守的 Run
自动扩大范围，而它的回执上一切正常。**这一格要的是产品决定**（要么让 ``Watch`` /
``SavedView`` 承载一份指名或内联的意图，要么让那条路只跑到维护面那一层为止），
不是在这里补一个默认值。

## 为什么幂等**不在**这一层

``fire_id`` 由**盘上那条调度说的到期时刻**推出来（``due_fire_id``），不是由本模块
的墙上时钟推 —— 所以两个驱动**同时**醒来也只会点一次火，而它们不需要商量。
锁、租约、去重表在这里都是多余的：那件事已经由 `claim_fire` 那条主键做掉了。

于是本模块**不做任何判断**：它把工厂拼成一次 ``tick``，把回执摊平，然后退出。

## 唯一的那个出口接在**这一侧**，而这是那张接线请求的执行

``adapters/mcp_server/scheduling.py:185`` 用一整节把「``sinks`` 不进那个工具工厂」
论证成一个**决定**，并在末句逐字写下它的去向：

    所以 sink 表的接线点是**驱动那一侧**。

本模块就是那一侧。所以这里注册**一个**出口：本机收件箱
（``adapters/notification.py::InboxSink``），落点是
``<data_root>/inbox/<workspace>/inbox.jsonl``（:attr:`~marketops.config.Settings.
notification_inbox_dir`）。接线由 :func:`deployment_sinks` 一处给出，表键取自 sink
自己的 ``name``。

**为什么接的是它**：``InboxSink`` 的 docstring 逐字说它是「唯一一个 ``accepted``
证明得出来的出口」—— 它收下的判据不是一句话，而是那一行**此刻在盘上、并且读得
回来**。没有这一份，「投递成功」在整个仓库里就只是一句声明；``§13`` ``:412``
「一个 sink 投递失败只重试通知，不重跑研究」也就只有 ``tests/`` 里接得上、
**生产路径上取不到读数**。

**服务面那一半仍然没有 sink，而两处不是同一件事。** ``_compose`` 装配的八条工具
没有一条投递（``scheduling_handlers`` 的 docstring 逐字），而投递是 ``tick`` 那一
半的事 —— ``tick`` **不是工具**，它是本进程调的一步。所以「MCP 那侧零个 sink」与
「这个部署零个出口」在接线之后是两句不同的话，不要照着前一句把这一处再改回去。

**名字对不上仍然是一条记在案的 ``unknown``。** 订阅上写的名字在这张表里找不到时
（例如 ``§20`` 定为 ``BLOCKED_EXTERNAL`` 的 Feishu），端口记一条 ``unknown``
（理由「这个部署没有注册这个 sink」）并**照样写一行收据**，不是静默跳过；那一行会
出现在本模块的回执里。

**目录不存在不是一格要人先准备的状态。** ``InboxSink.deliver`` 自己按需
``mkdir``；根**没有**进 ``Settings.ensure_data_dirs()``，理由逐字写在
``Settings.notification_inbox_dir`` 的 docstring 里（那是一个共享函数，一个 sink 的
落点不该参与回答「这个部署能不能起来」）。

## ``--at`` 不是调试开关

G2 逐字要求「长周期语义用**受控时钟**测，不等待一个月」，而
``application/scheduling.py`` 逐字说 ``at`` 是参数、「本函数不读时钟之外的时间」。
``--at`` 因此是那条要求的**执行器**：给一个时刻，这一轮的「到期」就是拿它算的。
不给时由 ``tick`` 自己读一次系统时钟 —— **而且只读一次**，这是 ``tick`` 的性质，
不是本模块的：本模块**一次都不读**时钟，它只把 ``--at`` 原样递下去。
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from functools import partial
from typing import Any

from ..adapters.notification import InboxSink
from ..adapters.sqlite.ledger import SqliteBudgetLedger
from ..adapters.mcp_server.schedule_action import ResearchScheduleAction
from ..application.scheduling import SUGGESTED_LIMIT
from ..application.scheduling import tick as _tick
from ..config import Settings, load_settings
from ..domain.errors import ConfigError
from ..domain.util import to_utc
from ..ports.notification import NotificationSink
from .composition import (
    default_account_cohort_store,
    default_account_pool_store,
    default_maintenance_store,
    default_notification_store,
    default_run_store,
    default_scene_store,
    default_schedule_store,
)

#: 起不来（配置错、库打不开）时的退出码。**与 ``mcp/__main__.py`` 的那个不同值**：
#: 那个是服务面，这个是驱动，两边的失败不该在脚本里长得一样。
_EXIT_CANNOT_START = 2
_EXIT_OK = 0


def deployment_sinks(settings: Settings) -> dict[str, NotificationSink]:
    """这个部署注册了哪些投递出口 —— 按名字索引的那张表。**全仓唯一的接线点。**

    ``adapters/mcp_server/scheduling.py:185`` 的那一节把这张表逐字指派给「驱动那一
    侧」（论证与服务面为什么不接它，在模块 docstring 里），而本函数就是那一侧。

    今天恰好一个成员：本机收件箱。它落成 ``<root>/<workspace>/inbox.jsonl``，而
    路径的根是 :attr:`~marketops.config.Settings.notification_inbox_dir` ——
    **由配置给出**，不在这里拼字符串（本模块对「跑在哪个盘上」一无所知）。

    表键取自 sink 自己的 ``name``，**不再写一遍字面量**：端口要求「表键与
    ``sink.name`` 逐字相同」，而两处各写一遍的那天，一次改名会让订阅上写的名字在
    表里找不到 —— 症状是「这条投递失败」，不是「名字对不上」，读的人会去查网络。

    这是本模块**只投递、不跑研究**的那一半的入口：它给出去的是一张
    ``{名字: sink}``，里面没有任何能让研究重开的格子（``NotificationSink`` 只认识
    事件与订阅）。所以「投递失败只重试投递」在生产路径上第一次有读数，而它**不是**
    靠纪律维持的 —— 这张表里根本没有第二条路。
    """
    sink = InboxSink(settings.notification_inbox_dir)
    return {sink.name: sink}


def run_once(
    settings: Settings, *, at: datetime | None = None, limit: int = SUGGESTED_LIMIT
) -> dict[str, Any]:
    """醒来一次：点火，再投递。返回**可 JSON 的回执**（不是一份摘要）。

    这是一个普通函数而不是只有 ``main`` 能走的路：判据要能在**不 fork 进程**的
    前提下把一个受控时刻喂进来（G2 的「受控时钟」），而 ``main`` 只负责把它打成
    一行 JSON。

    六本工厂 + 预算账本**与 ``_compose`` 逐字同一批**（``composition.py`` 的
    ``default_*`` 与 ``partial(SqliteBudgetLedger, ...)``）。**收工厂不收实例**，
    理由写在 ``ResearchScheduleAction`` 的 docstring 里：``sqlite3`` 的连接不能跨
    线程，而这一族会被驱动在它自己的线程上调用。

    ``sinks`` 取自 :func:`deployment_sinks` —— 也就是本部署真的注册了的那个出口。
    它给的是**表**，而这一层与 ``tick`` 都只把它按名字递下去；投递那一半怎么用它，
    一字不差地写在 ``application/notification.py`` 里。为什么接线点在这里、为什么
    服务面那一侧不接，见模块 docstring。

    **本函数只跑调度面。** 维护面（``Watch`` 到期/信号 → due work）**不在**这里，
    也**不该**在这里 —— 承载者是 ``interfaces/controller.py`` 的常驻控制器；
    另一条「到期 → 一行 ``watch`` Run」的路今天**没有生产入口**，缺的那一样与
    为什么本模块不许替它编，都逐字写在模块 docstring 的后两节里。
    """
    action = ResearchScheduleAction(
        settings=settings,
        scenes=default_scene_store(settings),
        runs=default_run_store(settings),
        maintenance=default_maintenance_store(settings),
        ledger=partial(SqliteBudgetLedger, settings.database_path),
        accounts=default_account_pool_store(settings),
        cohorts=default_account_cohort_store(settings),
    )
    report = _tick(
        at=at,
        workspace=settings.profile,
        schedules=default_schedule_store(settings)(),
        notifications=default_notification_store(settings)(),
        sinks=deployment_sinks(settings),
        action=action,
        limit=limit,
    )
    return _receipt(report, limit=limit)


def _receipt(report: Any, *, limit: int) -> dict[str, Any]:
    """把 :class:`~marketops.application.scheduling.TickReport` 摊成一行 JSON。

    ## 三格不能省，省掉哪一格都会让回执撒谎

    * **``fired[].status`` 与 ``fired[].replayed`` 分开。** 端口逐字讲了为什么
      （``FireReceipt`` 的 docstring）：``ADMITTED + replayed=False`` 是「真的点了
      火」、``ADMITTED + replayed=True`` 是「同一个 ``fire_id`` 的第二次调用」、
      ``NOT_DUE`` / ``DISABLED`` 是「没点火，也没写任何行」。合成一格之后，
      「重复 fire 幂等」与「这次点火成功了」会互相冒充 —— 而它们的处置一个是
      「什么都不用做」、一个是「去看那一轮 Run」。
    * **``remaining_due`` / ``remaining_events`` 必须报。** 一轮是**有界**的
      （:data:`SUGGESTED_LIMIT`）；只报「处理了 N 条」会让一次落后的追赶读起来
      像「都处理完了」。**这是「拒 ≠ 没跑」在回执上的形态。**
    * **``limit`` 自己也要报。** 不然读的人拿不到分母，``remaining_*`` 是个没有
      量纲的数。

    ``fire`` 那一格带出**三个**格：``outcome`` / ``message`` / ``detail``。

    * ``outcome`` —— 一次 ``ADMITTED`` 而 ``outcome=pending`` 的点火**不是失败也
      不是成功**：有人接下了，结果不知道。把它报成失败会让人去重放，而重放从来不
      重跑动作（那正是占位行的用处）；把它报成成功会让人以为研究跑过了。
    * ``message`` / ``detail`` —— **拒词在这里，不在别处**。这台驱动是「**客户端全断了**
      也会跑的那一个入口**」，它的回执常常是那一刻**唯一**还看得见的收据。
      只报 ``outcome=failed`` 的话，读的人知道「没开出来」，却读不到**为什么**
      （``application/scheduling.py`` 把拒词写在 :attr:`ScheduleFire.message`：
      「手上的是第 1 版，而盘上当前是第 2 版（原因是……）」那一类）。
      一个只报「失败了」的收据会让人去**重放**，而重放恰恰是这一族里最不该做的事。

    （2026-09-29：这两格是 Agent J 在 §G2' 复核时点出来的 —— 上面那句原先写的是
    「``fire`` 那一格**整块带出来**」，而当时的代码只吐 ``outcome``：散文与代码
    不一致，且缺的正是拒词。判据见
    ``tests/test_trigger_driver.py::test_a_refused_fire_carries_its_reason_on_the_receipt``。）
    """
    return {
        "at": report.at.isoformat(),
        "workspace": report.workspace,
        "limit": limit,
        "fired": [
            {
                "schedule_id": item.schedule_id,
                "schedule_revision": item.schedule_revision,
                "fire_id": item.fire_id,
                "status": item.status.value,
                "replayed": item.replayed,
                "advanced_to": item.advanced_to,
                "outcome": (
                    None if item.fire is None else item.fire.outcome.value
                ),
                "message": (None if item.fire is None else item.fire.message),
                "detail": ({} if item.fire is None else item.fire.detail),
            }
            for item in report.fired
        ],
        "delivered": [
            {
                "event_id": item.event_id,
                "attempted": len(item.attempted),
                "pending": len(item.pending),
                "outcomes": [
                    {
                        "subscription_id": outcome.subscription_id,
                        "sink": outcome.sink,
                        "state": outcome.state.value,
                        "attempt": outcome.attempt,
                        # **``note`` 承重**，不是装饰：端口逐字说
                        # ``DEFERRED`` / ``UNKNOWN_SINK`` / ``EXHAUSTED`` 三种
                        # 「这一轮没投出去」必须分得开，**而它们的分开只活在这句话里**。
                        # 一个写着本部署**没有注册**的名字的订阅，在这里读到的就是
                        # 那句「这个部署没有注册这个 sink=…」（``ACCEPTED`` 那一格
                        # ``note`` 是 ``None``，因为 ``note`` 取自 ``receipt.failure``）。
                        "note": outcome.note,
                    }
                    for outcome in item.outcomes
                ],
            }
            for item in report.delivered
        ],
        "remaining_due": report.remaining_due,
        "remaining_events": report.remaining_events,
    }


def _moment(text: str) -> datetime:
    """把 ``--at`` 解成一个带时区的时刻。

    **不带时区的输入要拒绝**，不是补一个。一个不带时区的字符串按本机时区解出来，
    在跨时区的部署上会让「到期」与「记下的时刻」差几个小时，而两份报告各自都正常
    —— 与 ``db.py`` 里那几处「两个库互相看不见」是同一种失败。
    """
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"--at 不是 ISO 8601：{text!r}（{exc}）") from exc
    if parsed.tzinfo is None:
        raise argparse.ArgumentTypeError(
            f"--at 必须带时区（例如 2026-09-29T15:00:00+08:00）：{text!r}"
        )
    return to_utc(parsed)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m marketops.interfaces.trigger_driver",
        description=(
            "触发驱动醒来一次：点火到期调度、投递待发事件，然后退出。"
            "本进程**不带定时器** —— 节奏由外部 cron / Hosting / ServerOS 持有。"
        ),
    )
    parser.add_argument(
        "--at",
        type=_moment,
        default=None,
        metavar="ISO8601",
        help=(
            "把「现在」钉在哪个时刻（必须带时区）。"
            "不给则读一次系统时钟。G2 要求长周期语义用受控时钟测，这是它的入口。"
        ),
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=SUGGESTED_LIMIT,
        metavar="N",
        help=(
            f"这一轮最多做几件事（到期调度与待发事件各算一件）。"
            f"缺省 {SUGGESTED_LIMIT}；两边共用一个上限是 tick 的性质，不是本模块的。"
        ),
    )
    args = parser.parse_args(argv)

    try:
        settings = load_settings()
    except ConfigError as exc:
        print(f"[trigger-driver] 起不来：{exc}", file=sys.stderr)
        return _EXIT_CANNOT_START

    try:
        receipt = run_once(settings, at=args.at, limit=args.limit)
    except Exception as exc:  # noqa: BLE001 —— 见下面那段
        # **不吞异常，也不重试。** 一次醒来失败与「没有到期的东西」是两件事，而
        # 把它们报成同一件会让「调度是不是在跑」这个问题的答案永远是「是」。
        # 重试不属于这里：下一次醒来是外部驱动的事，而它下一次读到的到期时刻是
        # **盘上那条调度说的**（`due_fire_id`），所以重试天然是幂等的 ——
        # 在这一层加一个循环只会把一次可诊断的失败变成一次长时间的沉默。
        print(
            f"[trigger-driver] 这一轮失败：{type(exc).__name__}: {exc}",
            file=sys.stderr,
        )
        raise
    print(json.dumps(receipt, ensure_ascii=False, indent=2))
    return _EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
