"""MarketOps MCP 服务面的**入口**（§6 / §7 / §21 Phase C）。

本包今天只有两个文件：``__init__.py``（这份说明）与 ``__main__.py``
（``python -m marketops.interfaces.mcp``）。服务面本身 —— 认证、判定、十五个工具的
注册与回答 —— 住在 ``marketops/adapters/mcp_server/``。

## 为什么分开：本层可以点名 adapter，不可以 import 厂商 SDK

``interfaces/`` 拿到的授权是**点名具体厂商 adapter**（``from marketops.adapters.serper
import SerperSearchProvider`` —— 本仓的模块）。厂商 **SDK** 只有 ``adapters/`` 可以
碰，而官方 MCP SDK 在 ``tests/test_adapters_hold_the_vendors.py`` 的
``VENDOR_MODULES`` 里 —— 一份 **B4** 就写好、那时本仓还没有一行 MCP 代码的清单。
完整的理由写在 ``marketops/adapters/mcp_server/__init__.py``。

于是本包剩下的正是入口层该有的东西：**从外面按一个名字叫它，以及它绑在哪个地址上**。

## 本包与计划的一处偏差，写在明处

``docs/MVP_MIGRATION_PLAN.md`` 的 Phase C 列出的是
``{__init__, auth, guard, control, server, __main__}``。落地时 ``control`` 改名为
``tools.py``（现在在 ``adapters/mcp_server/`` 里）。

* 改名：``control.py`` 会被读成「只有 control 侧的工具」，而 ``get_evidence``
  同时属于两个角色、且研究侧的工具也要注册进来。名字叫 ``tools``
  才不会在第一个研究侧工具落地时诱导人另开一个文件、然后把注册逻辑写两遍。
* ``research.py`` 在 **Phase D** 落地时出现（``adapters/mcp_server/research.py``）。
  Phase C 当时**没有**建它，理由是「研究侧一个工具都没有实现，现在写出来的会是
  一份按想象编的接口」——与本计划对 ``jobs.py`` 用的是同一条规矩。那条理由到
  Phase D 就到期了：``search_web`` 与 ``fetch_document`` 各有了真实的消费者。
  **到期就写，是那条规矩的另一半**；只记住「不许提前写」会把它变成「永远不写」。

## 目录里的十五个，注册十五次

``security/catalog.py`` 的 ``TOOL_CATALOG`` 有十五条，**全部注册**，默认组合里
也**全部**有真实实现：Phase C 的五个控制面、Phase D 的 ``search_web`` /
``fetch_document``、Phase E 的 ``crawl_site``、Phase F 的 ``decide`` /
``get_decision``、Phase G 的五条作业工具。

这不是「先把架子搭起来」。两种替代方案都更差：

* **只注册五个。** ``tools/list`` 就与「这个角色看得见的目录」不再相等，
  隔离判据（``tests/test_mcp_isolation.py``）测的东西会悄悄地变成另一件
  更小的事。
* **不注册 stub，让调用方拿到「Unknown tool」。** 那句话是一个**错误的事实**：
  ``submit_research`` 存在，只是没有实现。而「不存在」与「还没实现」的排查
  方向完全不同 —— 前者要改调用方，后者要改构建。

所以 ``_stub`` 各自回一句带阶段号的话，并且**角色判定在它之前**：不属于你的工具，
答案与不存在逐字相同。

「有真实实现」是**实例**事实，不是构建事实，上面那句「十五条全部有实现」说的是
**默认组合**：``build_server`` 收的是一张处理器表，所以它可以在不接研究侧的情况下
被组装（控制面单独部署是合法的一种）。那时 ``search_web`` / ``fetch_document`` /
``crawl_site`` 落回 stub，而 ``get_capabilities`` 的 ``wired`` 那一格如实报 false。
组装研究侧的是 ``__main__.py::wire_research``，判定侧是同一个文件里的
``wire_decisions`` —— 后者**总是**返回 ``decide`` 与 ``get_decision``：没指定辅助
后端时 ``decide`` 照常接线、只是明确回答「本部署没有指定辅助后端」。一个说得清
理由的拒绝，好过一个「尚未实现」。本仓唯一的组合根 ``_compose`` 把两张表合起来
一起交给 ``build_server``。

## 三条边界，三条都在别处有执行器

* 主体从 ``Authorization: Bearer`` 来，别处都不来 —— ``adapters/mcp_server/auth.py``。
* 拿到主体之后，谁看得见什么、谁调得动什么 —— ``adapters/mcp_server/guard.py``。
* 绑定地址必须是一个**字面量**环回地址，否则进程起不来 —— ``__main__.py``。

第三条是唯一还住在本包里的：它管的是**这个进程怎么起**，与 MCP 协议无关 ——
``resolve_bind_host`` 一行 SDK 都不 import，``--check`` 也跑得起来。
"""

from __future__ import annotations

__all__: list[str] = []
