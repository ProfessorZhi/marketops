"""**入口层**：把这一栈东西接上电，让外面的人能按一个名字叫它。

§2 的目标树把这一层与 ``domain/`` ``application/`` ``ports/`` ``adapters/`` 并列，
而它的成员只有一类东西：**入口**。今天是 ``cli.py`` 与 ``composition.py``
（后者严格说来不是入口，而是入口依赖的那一小块接线 —— 见下），Phase C 之后是
``mcp/``（control 与 research 两套目录）。它们共享一个性质 —— 从外面看，
这一层是 MarketOps 的**全部表面**；从里面看，它几乎不含逻辑。

## 唯一允许**点名具体厂商 adapter** 的地方

这是本层存在的第二个、也是更严格的那个理由。其余每一层都只能 import 端口
（``ports/``），由端口决定「我需要什么」；而**总要有人**回答「谁来给」——
``SerperSearchProvider`` 这个名字必须出现在某处。B4 记下的组合根规则是：

    只有 ``interfaces/`` 与 ``adapters/`` 自己可以点名某个具体厂商 adapter。

``cli.py`` 是这条规则的第一个落点（它 ``from marketops.adapters.serper import
SerperSearchProvider`` —— 那是对的，也是**只有这里**对）。

``composition.py`` 是第二个落点，也是**这条规则为什么需要一层**的证明：
``default_router()`` 必须 import ``pypdf`` 与 MinerU 的具体 adapter，所以它不能
住在 ``application/``（那是纯策略层）；它也不能住在 ``adapters/`` 然后反向
import ``application/``（方向反了）。它需要一个"允许点名厂商、但自己不承载业务
逻辑"的位置，那就是这里。

判据正在分两步接上：``tests/test_adapters_hold_the_vendors.py`` 今天已经在执行
**前半句** —— 它从磁盘上算出哪些模块直接 import 了厂商 SDK，再问「除了
``adapters/`` 自己，还有谁碰得到」。**后半句（组合根被单独放行）的执行器还没接上**
（B7a-3）：等它落地时，``interfaces/`` 这两个文件是仅有的两个被放行的位置，
``tests/test_adapters_hold_the_vendors.py`` 里那句「组合根规则还没有覆盖」会同时
改写成现在时。在那之前，「``interfaces/`` 可以点名厂商」是一句**还没有执行器的话** ——
按本仓的规矩，这种话要写出来，不能写成已经成立。

## 它从哪里搬来

``services/worker/cli.py``。搬的理由不是整理目录：§2 的目标树里 ``interfaces/``
是 ``marketops`` 包内的一层，而 ``services/`` 是一个平级的、与 ``marketops``
没有依赖关系的目录 —— 组合根站在包外，就没法用「包内分层」这条判据约束它，
只能靠自觉。搬进来之后，「谁可以点名厂商 adapter」是一句**可以查的话**。

``services/`` 本身已经删掉。它最后剩下的是 ``api/`` 与 ``feishu_bot/`` 两个
**刻意留空**的目录 —— 没有一行可运行的代码，只有「为什么现在不建」的推理。
处置写在原来的 docstring 里就是这一段：目录清理时再决定「删掉，还是把那段推理搬进
``docs/``」。两件都做了 —— 目录删掉，推理搬进 ``docs/NOT_BUILT.md``，
因为它们唯一的价值就是那段推理，而一个只为承载一段说明而存在的目录是噪声。
"""

from __future__ import annotations

__all__: list[str] = []
