"""MCP 服务面对官方 SDK 的绑定（§6）。

## 为什么它在 ``adapters/`` 而不是 ``interfaces/``

这一包**看起来**该住在 ``interfaces/``：它装着一个服务端，而那一层的定义就是
「把这一栈东西接上电」。它也确实曾经住在那儿。搬过来的理由是仓库自己早就写下
的一条判据，而不是「测试红了所以挪一挪」：

``tests/test_adapters_hold_the_vendors.py`` 的 ``VENDOR_MODULES`` 把 ``mcp``
列在**「协议 / 服务端 SDK」**一组里（与 ``fastapi`` / ``starlette`` / ``uvicorn``
同组），并且那条判据只有唯一一个允许的位置 —— ``marketops/adapters/``。这份清单
是 **B4** 写下的，那时本仓一行 MCP 代码都还没有：它是一条**预先声明的规则**，
而不是事后对既成事实的描述。Phase C 落地的这一刻，正是它要管的时刻。

判据另有更硬的一半：``interfaces/`` 被明确授予的权力是**点名具体厂商 adapter**
（``from marketops.adapters.serper import SerperSearchProvider`` —— 本仓的模块），
不是 import 厂商 **SDK**。``interfaces/composition.py`` 里那句「``default_router()``
必须 import ``pypdf`` 与 MinerU 的具体 adapter」说的也正是这个区别：它点的是
``adapters/text_pdf.py``，不是 ``pypdf``。两件事都以 ``interfaces/__init__.py``
的「唯一允许点名具体厂商 adapter 的地方」为准。

**没有把它做成「端口 + 适配器」两半。** 那条判据给的补救办法是「给它一个端口」，
但这里没有端口可给：服务面没有第二个实现，也没有一条**别的**路径能进到同一个能力
（端口那条判据防的是「调用方绕过端口直接拿了能力」，而这里的调用方只有
``interfaces/mcp/__main__.py`` 一个入口）。造一个端口，出来的会是一条只为满足扫描器
而存在的接缝 —— 「一条边界没有执行器就只是自觉」的反面，同样是废的。

## 包里的东西

| 模块 | 它绑的是什么 | 与它对应的策略住在哪 |
|---|---|---|
| ``auth.py`` | ``TokenVerifier``：Bearer 令牌 → 主体 | ``security/tokens.py``（令牌环） |
| ``guard.py`` | ``ServerMiddleware``：谁看得见什么、谁调得动什么 | ``security/catalog.py``（目录与角色） |
| ``server.py`` | ``MCPServer`` 的组装、传输层安全设置 | ``interfaces/mcp/__main__.py``（绑哪个地址） |
| ``tools.py`` | ``ToolError``：§2 控制目录全部，加上 §3 里与作业存储同源的那两条（``get_assignment`` / ``submit_findings``） | 各自的领域与 adapter |
| ``research.py`` | 同上，§3 的 ``search_web`` / ``fetch_document`` / ``crawl_site`` | ``application/billed_search.py``、``application/acquisition.py``、``application/site_crawl.py`` |
| ``decisions.py`` | 同上，§3 的 ``decide`` | ``application/decision_specs.py``（题面）、``ports/decisions.py``（判定） |
| ``product_loop.py`` | 同上，产品环（``docs/MCP_PRODUCT_LOOP.md``）里 Scene 的那三条 | ``application/scenes.py``（立项/改版/生命周期）、``ports/scenes.py``（持久化） |

``tools.py`` / ``research.py`` / ``decisions.py`` 是这一包里不吃「以 SDK 为中心」
那条的模块 —— 它们装的是工具的**回答**。留在这儿的理由只有一条，但那条很硬：
``ToolError`` 是 SDK 的类型，而工具抛它就是在告诉 SDK「把这条变成 ``is_error``」。
把业务写入留在 ``interfaces/``、再在绑定层翻译一次异常，会造出一个只为搬家而存在
的中间层，而它翻的还是**错误文本**—— ``tests/test_mcp_isolation.py`` 逐字钉住
「不属于你的工具」与「不存在的工具」必须同形，多一层转述就多一处能让它俩分家的
地方。

三个文件而不是一个，分界是**谁的知识**：``tools.py`` 读的是本机配置与磁盘上的产物
（能力表、场景、证据、报告），``research.py`` 花的是钱与网络，``decisions.py``
不花钱也不抓取 —— 它把一段**已经准备好的**文本交给一个后端做一次有界判断。三者的
依赖形状各不相同，而注入的东西一个都不重叠：``research.py`` 要
``search`` / ``acquisition`` / ``router`` / ``ledger_factory``，``decisions.py``
只要那一**个**被选中的后端。全部由组合根**注入**，所以它们一条厂商 import 都没有，
能在零网络零凭据下跑完整条判据。

``decisions.py`` 从 ``research.py`` import 一个私有助手（``_load_bundle``）：装载
冻结 profile、把失败折成 ``ToolError``，是两边逐字相同的四行。抄一份在这里的代价
不是那四行，是**装载失败的措辞**从此有两份 —— 而「同一份 profile 坏了，两个工具
说了两句不同的话」正是这类模块最不该有的分歧。

## 那一份助手现在是 ``tools.require_bundle``

上面那段说的是「不要抄」，而它当时自己就抄了两份：``research.py`` 的
``_load_bundle`` 与 ``tools._scene`` 里的同一段内联。第三个调用者（``product_loop.py``）
出现时，把第三份写下去是**记账最省的那一边**，也正是这段话警告的事。所以
2026-09-24 把它合并成一处 —— ``tools.require_bundle``，与它的兄弟
``tools.require_scene`` 并排（两者合起来回答「这次调用说的是哪个场景」的两半：
名字对不对、那个名字底下的规则装不装得出来）。``decisions.py`` 那条跨模块的私有
import 因此也消失了：它当时是在「有两个私有实现」里挑了一个，而不是在两个里挑对的。

``product_loop.py`` 与 ``tools.py`` / ``research.py`` / ``decisions.py`` 并列，是
这一包里的第四个「装工具的回答」的模块。它与 ``tools.py`` 的分界不是阶段，是
**主语**：``tools.py`` 的十条问的是**一次研究**（受理一个作业、领走一件工作、
读一份档案），``product_loop.py`` 问的是**一个市场**（按什么规则经营、哪一版
规则在生效、改一版之后什么要重做）。两者的存储端口不重叠、失败的处置不同、
角色也不一样 —— 理由写在第 55 行那一节与那个模块自己的 docstring 里。
"""

from __future__ import annotations

from .server import (
    ToolBacking,
    build_server,
    loopback_transport_security,
    mcp_url,
    resource_identifier,
    tool_backing,
)

__all__ = [
    "ToolBacking",
    "build_server",
    "loopback_transport_security",
    "mcp_url",
    "resource_identifier",
    "tool_backing",
]
