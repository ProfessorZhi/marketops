"""把一轮工作交给外部的 **agent runtime**：起一个子进程、等它结束、交回退出码。

## 为什么这一处 ``subprocess`` 在 ``adapters/``，而不是 worker 入口里

``tests/test_adapters_hold_the_vendors.py`` 的 ``VENDOR_MODULES`` 里有
``subprocess``，与 ``httpx`` 同一条理由（那条注释逐字）：**Quay 没有 Python SDK，
本仓与它的全部界面就是一个子进程**。所以「谁 import 得到 subprocess」与「谁
import 得到 httpx」问的是同一件事，只是换了一个外部提供者。

而那条判据**没有给 ``interfaces/`` 开过口子**：那一层被授予的权力是「点名具体厂商
adapter」，不是 import 厂商 SDK。研究 worker 的入口住在 ``interfaces/``，所以这一处
绑定搬到 ``adapters/`` —— 与 Phase C 把 MCP SDK 的绑定从 ``interfaces/mcp/`` 搬进
``adapters/mcp_server/`` 是同一个动作，理由也逐字相同。

## 为什么没有给它造一个端口

``adapters/mcp_server/__init__.py`` 记过这条判断：判据给的补救办法是「给它一个
端口」，但端口那条纪律防的是「调用方绕过端口直接拿了能力」—— 而这里有**一个**
消费者（``interfaces/research_worker.py``）、**一个**实现，没有第二条路径能进到同一个
能力。造出来的会是一条只为满足扫描器而存在的接缝。真出现第二个实现（比如某一轮
不再起进程、而在同进程里把 argv 交给一个内嵌 runtime）时再抽，那时接缝的位置由
两个实现共同决定，而不是由今天这一个猜出来。

## 它**不是** launcher

Quay 的词汇里 ``launcher`` 指的是 ``.quay/profiles.yml`` 里那个单 token，也就是
``interfaces/research_worker.py`` 自己。本模块是**它对面**那一个：launcher 把一轮
递给谁。两个名字都要留着，所以这里不叫 ``launch``。
"""

from __future__ import annotations

import subprocess
from collections.abc import Sequence
from pathlib import Path

__all__ = ["run_delegate"]


def run_delegate(argv: Sequence[str], *, cwd: Path | None = None) -> int:
    """跑起来、等到它结束、把**它的**退出码交回去。

    **不捕获 stdout / stderr。** 子进程直接写这个进程的那两个流：运维要读到的是
    delegate 自己说的话，而不是本层转述的一行「delegate 退 3」。捕获之后只有两种
    处置，两种都更坏 —— 丢掉（现场就没了），或者原样转发（等价于不捕获，只是多一份
    缓冲，而缓冲会让长跑的输出在崩溃那一刻整段消失）。

    **``cwd`` 只在「调用方刚刚自己造出这个目录」时才给。** 默认 ``None`` =
    继承调用方的 cwd。这条规矩管的是「凭什么把这个目录交给子进程」：造出来的那个，
    证明就是那次创建本身；继承来的那个，证明是调用方对自己 cwd 的核对。两种都不许
    **转述** —— 即从别处听来一个路径、自己没做过那次核对就交出去，那样子进程会站在
    一个调用方以为它不在的地方，而两边都不报错。

    ``interfaces/research_worker.py`` 给的是前者：它按逐轮唯一的路径 **mkdir 出一个
    隔离目录**（模块 docstring 讲了为什么不是工作区根），把记录写进去，然后把那个路径
    交给本函数。

    **为什么是参数而不是调用方 ``os.chdir``。** 改自己的 cwd 是一次进程级副作用：它让
    ``main()`` 不可重入（第二次调用时「我在哪」已经变了，而 ``_workspace_root`` 那条
    判据正是拿 cwd 当输入），也让「delegate 站在哪里」变成一件要看调用顺序才知道的事。
    一个参数把这件事限制在这**一次**调用里。

    **不做超时、不重试、不解释退出码。** 退出码即裁决（理由在
    ``interfaces/research_worker.py`` 的模块 docstring）；超时是部署的事，而一个写在
    这里的超时会把某一次部署的取值变成所有部署的前提。

    Raises:
        OSError: 程序起不来（不在盘上、没有执行位、解释器不对）。**刻意不折成一个
            退出码**：这一层分不出「起不来」与「跑完但失败」，而两者的处置相反 ——
            前者是本层的配置错（要停下来改部署），后者是一次真实的失败（要如实
            记进外部那本账）。归成同一个数字就是把这次分类挪到一个看不见的地方。
    """
    return subprocess.run(argv, check=False, cwd=cwd).returncode
