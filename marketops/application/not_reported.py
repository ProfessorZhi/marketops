"""「这一层答不上来的一项，**以及为什么**」—— 一份**读数**的形状，自己一个模块。

``NotReported`` 是一个纯值对象：两个字符串，没有行为、没有存储、没有时刻。它在这里
只有一个理由 —— **它是四个出口共用的那一格**，而其中的两个出口正在为「零外部调用」
被量。

## 它为什么不在 ``plan_preview.py`` 里

它原来住在那里，只因为那里是它的**第一个**读者（§253 那句要求是编计划那一次提出来的）。
那是历史，不是结构。一处定义被四个出口读的时候，「放在第一个读者旁边」就开始有代价，
而这一次的代价被量了出来 —— ``tests/test_mcp_launch_is_offline.py`` 的第一跑给出这条链：

    marketops.adapters.mcp_server.launch
      → marketops.application.launch_preview
      → marketops.application.plan_preview
      → marketops.application.research_strategy
      → marketops.ports.acquisition

``plan_preview.py`` 为了编计划 import 了 ``research_strategy.research_compiler``，而那个
模块 import 了 ``FetchMode``（它 `:833` 逐字说那一格报的是「``FetchMode`` 这个闭集的
结构」）。于是**任何只是想给这一格起个名字的层**，都顺带把抓取契约拉进了自己的闭包。
对 ``product_readiness``（b01-c）与 ``launch_preview``（b01-d）这两层，那正好是它们存在
的**反面**：B01 ``:104`` 第五条关闭条件逐字「启动授权前零外部调用」，而这两层是它的
读数面。

搬出来的效果是两条边各断一处，而不是一处 —— 查缺面与启动面各自与 ``plan_preview``
的**唯一**一条边就是这一格（`command grep` 逐条看过）。计划那一边照旧从本模块引，
``NOT_REPORTED`` 那一份常量的内容与行号一个字没动。

## 这一条不是「把判据改松一点」

要说清楚，免得下一个读到这里的人以为这次搬家是为了让一条红的判据变绿：

* ``ports.acquisition`` 自己**不做 I/O**，它是一份契约与几个 ``Enum``。搬家的确没有挡掉
  任何一行会真的发出去的请求 —— 因为今天一行都没有；
* 它仍然要算「够得到网络能力」，口径与本仓既有那一份逐字相同
  （``tests/test_registration_is_offline.py``：字面是「真的会对外发起请求、**或起一个
  外部进程**」，而端口按**全名**判）：**拿得到端口，调用它只是下一行的事**。一条判据
  存在的意义是钉住「下一行」不发生，而它失效的方式是静默的 —— 某天 ``FetchMode`` 旁边
  多了一个 ``FETCHERS`` 表，四个出口就一起从「够不到」变成「够得到」；
* 所以处置是**断那条边**，不是放宽那条判据。放宽会让「启动授权前零外部调用」从一处
  构造退回成一句话，而那正是 ``tests/test_launch_preview_is_offline.py`` 的模块
  docstring 开头就拒过的事。

## 与 ``_shared.py`` 是同一族处置

``marketops/adapters/mcp_server/_shared.py`` 是同一个动作在适配层的版本（``require_scene``
因为「第三个调用者必须够不到 ``tools.py``」而搬过去）。一条纪律：**一处定义被多个出口读，
它就不该住在其中任何一个出口的核心逻辑旁边** —— 否则每加一个出口，就把那一个出口的核心
依赖也加给了别人。
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class NotReported:
    """§253 要求 preview 报告、而这一层答不上来的一项，**以及为什么**。

    ``reason`` 不是一句「暂不支持」：读的人的下一个问题永远是「那谁答得上」，
    所以每一句都点名**哪个模块或哪一片**持有那件事，或者点名**今天缺的是哪一个
    生产者**。
    """

    #: §253 里那一项的原文语汇（「已有事实复用多少」这种），不另起一套说法。
    item: str
    reason: str


__all__ = ["NotReported"]
