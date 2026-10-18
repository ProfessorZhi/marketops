"""Quay 派发的**入站那一端**：把一条外部任务变成一次研究 worker 的启动。

    python -m marketops.interfaces.research_worker <Quay 交给 launcher 的那一串 argv>

## 它接的是什么

Quay 的 worker driver 每轮把一件任务 spawn 成一个进程，argv 由 ``launchArgv`` 拼：

    [launcher, "--settings", <file|json>, …, "-n", <role>, "-p", <prompt>, "--session-id", <uuid>]

``launcher`` 是 ``.quay/profiles.yml`` 里 role 的**单 token**（实测：
``const argv = [resolved.launcher, …]``，不切空格），所以本模块就是那个 token 指到的
程序，而它拿到的 argv **只有一种含义**：Quay 拼的那一串。因此本层不做任何 argv
手术 —— 需要它知道的部署事实（跑哪个 delegate）从**环境**来，见下。

本层只做八件事：

1. 从 ``-p`` 里读出任务身份（``Task: <id>. Repo root: <root>.``）—— 那是 driver 交
   过来的**唯一**一份逐任务身份（``-n`` 是 role 级常量、``--session-id`` 每轮新造）；
2. 从工作区里的任务文件读出 ``marketops-item:`` label，反解出工作单元 id；
3. **核对这条 argv 会给子进程开出多大的工具面**（见下）；
4. **核对本层加载的 marketops 与服务加载的是不是同一份**（见下，§15(b)）；
5. **领一个研究员身份并从环里解出它**（见下，§15(a)），锁到本轮结束；
6. 建一个**本轮的隔离运行目录**（见下），把这一轮的记录写进去，把它当作 delegate 的 cwd；
7. 把 Quay 的其余 argv **原样原序**交给 delegate，只把 prompt 换成研究形状的那一份；
8. 把 delegate 的退出码原样交回去。

## 为什么 cwd 是隔离运行目录，而不是工作区根

driver 起本层时 cwd 是**工作区根**（实测 ``spawn(cmd, cmdArgs, {cwd: rootDir, stdio:
"inherit"})``），而工作区根里住着 ``.quay/``（看板控制、driver 的 in-flight pid）、
``tasks/``（**其他任务**的任务文件）、``.claude/launch.settings.json`` 与 ``.mcp.json``
（本部署的权限与工具配置）。子 claude 继承那个 cwd 就等于：它读得到本工作区里一切
管理面文件、读得到兄弟任务、也读得到上一轮探针留在那里的回执 —— 而提示词里那句
「不要动看板」的执行器是一个语言模型。

所以本层在启动 delegate **之前**新造一个本轮唯一的空目录，路径是

    <MARKETOPS_WORKER_ROOT>/<工作单元 id>/<任务 id>/<UTC 时刻>-<pid>/

写一份 ``run.json`` 进去，然后把**这个路径**作为 delegate 的 cwd 交给它（``os.chdir``
也能达到目的，但那是进程级副作用：本层会因此不可重入 —— 见 ``agent_runtime.py`` 的
``run_delegate``）。于是子 claude 的相对路径从这个目录解析：它看不见 ``.quay/``、
看不见任何别的任务、看不见部署配置，**要写也写在这里**（一次误写的落点是本轮的
scratch，而不是部署）。隔离是本轮**唯一**拥有那个目录的事实 —— 别的轮次、别的任务、
别的 worker 都不在这个路径上，所以「这一轮在盘上留下了什么」是一个目录就能答的问题。

目录名按**启动器真正持有的两个身份**排（工作单元 id、任务 id），不是 ``attempt_id``：
租约是 worker 第一次 ``get_assignment`` 时才铸造的，启动这一刻它还不存在 —— 用一格
猜出来的编号命名目录，会让「哪一轮是哪一轮」在事后变成一次推断。

## 三条不在本层的边界（都容易被读反）

* **stdio 不捕获、不改道。** driver 用 ``stdio: "inherit"`` 起本层，delegate 原样继承
  同样的两个流：运维要读的是 delegate 自己说的话。而 argv 那一侧**不许加旗标**（判据
  在 ``tests/test_research_worker.py``：转发出去的那一串必须恰好是 Quay 的加 ``-p``），
  所以「让 delegate 自己再写一份日志」这条路是不通的。这一轮的 delegate 日志因此不在
  这个目录里 —— 它在 ``~/.claude/projects/<这个目录的 slug>/`` 下，而**那个目录同样是
  本轮的**（session 存档按 cwd 归档，cwd 本轮唯一）。
* **不碰任务文件、不碰 git、不翻任何状态。** 关闭是市场侧的：delegate 走
  ``submit_findings`` → 结算 → 回传，然后外部看板才动。旧 Kent POC 的启动器自己
  ``git add`` + 改 status + push，那条路让外部看板与本地账各说各话而两边都不报错。
* **``--allowedTools`` 不是本层给的边界。** 工具面由这条 argv 拼出来，本层只核对它；
  子 claude 自己的内建工具（``Read``/``Bash``/…）由工作区 ``launch.settings.json`` 的
  权限块决定。``Bash`` 一类在 deploy 里是**整工具** deny（实测回执原文「No such tool
  available: Bash」）。

  而 ``Read``/``Grep``/``Glob`` 由**两条机制合起来**管，只讲其中一条就会讲错：

  * **相对路径**由上面那个 cwd 管 —— 这是这一格的主要机制，也是唯一一条能挡住
    「一次误读」的机制（工具面里没有哪条规则能替它）。
  * **绝对路径**由 ``launch.settings.json`` 的 ``Read`` deny 规则管。cwd 对绝对路径
    **什么也没说**：文件工具从文件系统根解析它，判它的是权限层。所以那三条
    （``//…/.secrets/**``、``//…/marketops-mvp-quay/.quay/**``、``//…/marketops-mvp-quay/tasks/**``）
    不是「加固」，是这一格缺的那一半。**2026-09-23 之前它们是缺的**：探针实测那两处
    当时也被拒，但拒的理由是 print 模式答不了权限询问，不是规则 —— 改一次
    ``defaultMode`` 就会静默打开，而探针的读数一模一样。

  两条语法事实值得记在这里（都来自 Claude Code 的权限文档，本机 2.1.272）：
  ``//`` 才是绝对路径（单个 ``/`` 锚在 settings 文件所属的项目根），而写给 ``Glob``
  自己的路径规则**被接受但永不被查询** —— 约束 Grep/Glob 的写法是写 ``Read`` 规则。
  同一条 ``Read`` deny 也挡 ``Edit``/``Write``，所以一个路径一条规则就够四个工具。

## 为什么「子 claude 看见哪些 MCP server」是本层的判据

因为第 4 条要「原样原序」把 argv 交出去，所以这一步没有别的地方可拦：argv 一旦转发，
工具面就定了。而这条链上有一段**失败开放**的代码（实测，0.11.0）：driver 的
``mcpConfigArgvSuffix`` 在排除名单为空时把 ``--strict-mcp-config`` 与 ``--mcp-config``
**一起省掉**，子 claude 于是退回「与宿主上一切可用来源合并」。读法详见
``adapters/quay_cli.py`` 的 ``worker_mcp_servers``。

判据是**恰好等于** ``REQUIRED_MCP_SERVERS``，不是「包含」。理由是本层不点名任何厂商
adapter 之外的 MCP —— 宿主上那几个 server 里有能开设备会话的、有能改看板任务状态的，
而这一轮的语言模型是被 prompt 要求「不要去动看板」的。让它**看不到**比让它「被要求
不要用」强：后者的执行器是一个提示词。多出来一个 server 是**拒绝**，不是警告。

## 为什么「本层加载的 marketops 是不是服务那一份」是第 4 件事（§15(b)）

这一层的记录里有一格 ``loaded_marketops``，写的是**本层**加载的那棵树。而这一轮真正
在干活的是 delegate：它通过 ``marketops`` MCP 工具去调**服务**，服务是一个**别的进程**，
它加载的可能是另一棵树（部署侧那一处是启动器里的 ``PYTHONPATH``；现场
``docs/PRODUCT_LOOP_ACCEPTANCE_2026-09-24.md`` §G3 实测与服务器差 47 个提交）。

两边都不报错。于是这一轮的每一条发现都由一份与服务不同的代码产生，而记录里写着的是
本层那一份 —— 报告主张了一件不成立的事，**没有任何一行日志说破它**。所以第 4 件是
**在启动 delegate 之前**核对，不一致就退 2、delegate 不启动（判据与措辞在
``adapters/build_stamp.compare_builds``）。服务那一份由**服务自己**在开始服务之前写到
数据根里（``state/server-build.json``，``adapters/server_build.py``）。

## 身份从环里来（§15(a)）

这一层**自己**领身份，不让启动器 export 一份令牌进来。原因不是洁癖：令牌的有效性
只写在环里（盘上只有摘要），所以「这份令牌还作数吗」是一个只有环能回答的问题；
而启动器原来那套遍历 vault 文件名的做法答不出来 —— 9-23 的格式 3 环替换了 9-22 铸的
两个令牌之后，它照旧把死令牌交出去，结果是委托方**完全没有** ``marketops`` 工具面
（服务一律 401），而报告上写着「marketops server 未被挂载」。

选身份、解主体、拿 ``flock`` 全部在 ``security/identity.py``（连同那几句拒绝的措辞）；
这一层负责的是**锁的寿命**：``flock`` 由本进程持有，本进程活到 delegate 结束，
所以「一个 worker 一个身份」在整个这一轮里成立 —— 启动器在 ``exec`` 之前拿锁也能
达到同样的效果，但那要求身份在 bash 里选，而 bash 里的选择没有执行器。

**锁的寿命在这一层有一条容易写错的推论**：那个 fd 不能被提前回收，但也不能不收。
``os.open`` 给的是非继承 fd，所以 delegate 不会带着它 —— 放锁的是**本进程**，不是
delegate。而「本进程」有一个更精确的时刻：``main`` 里那个 ``finally`` 显式
``os.close``（本轮结束），**不是**等进程退出。理由是实测出来的：fd 是一个整数，
``identity`` 出了作用域、被 GC 掉都不会关掉它，于是同一个进程里的第二次派发会撞上
自己的上一轮，读数形状是「1 个研究员身份全部被在飞的 worker 占着」。而进程被 kill
时内核照样放 —— 两条路径都不留「陈旧锁」这个状态。

## 本层的四处外部知识都住在 ``adapters/``，一处都不在这里

* argv 的形状与那句任务身份 —— ``adapters/quay_cli.py``（``split_worker_argv`` /
  ``worker_prompt_target``）；
* 任务文件的落盘格式 —— 同模块的 ``read_task_labels``（连同「为什么不问一次
  CLI」那条理由）；
* 「怎么起一个外部 agent runtime」—— ``adapters/agent_runtime.py``；
* 「服务把自己的构建报在哪、那份文件长什么样，以及两份指纹怎么比」——
  ``adapters/server_build.py`` 与 ``adapters/build_stamp.py``。

搬家的判据是 ``tests/test_adapters_hold_the_vendors.py``：``subprocess`` 在那份
``VENDOR_MODULES`` 里（与 ``httpx`` 同一条理由 —— Quay 没有 Python SDK，本仓与
外部执行器的全部界面就是一个子进程），而它只放行 ``marketops/adapters/``。本层被
授予的权力是**点名**具体厂商 adapter，不是 import 厂商 SDK —— 与 Phase C 把 MCP
SDK 的绑定从 ``interfaces/mcp/`` 搬进 ``adapters/mcp_server/`` 是同一个动作。

留在这一层的是**策略**：换哪一份 prompt、哪些情况拒绝、拒绝时给什么退出码。

## 为什么 delegate 从环境变量来，而不是一个 ``--delegate`` 参数

因为 argv 已经有一个唯一的意思（Quay 的）。要在它前面塞一个自己的参数，就得让本层
知道「我的参数占前两位、其余是对面的」，于是同一条 argv 有了两种读法 —— 而 driver
哪天多推一个旗标，那个前缀就换了个意思，且不报错。环境变量把这个决定挪到唯一能
表达它的地方：**工作区的启动器**（那个 shell 包装 export 它，然后 exec 本模块）。
还有一个次要理由：本层的 delegate 在 ``ps`` 里是否可见不重要 —— delegate 进程自己
起来之后就可见了，而 argv 的单一含义是每天都用得上的性质。

## 退出码

``0``/非 ``0`` 由 delegate 决定，本层照传（退出码即裁决）。本层自己的拒绝一律是
``EXIT_REFUSED``（2，与 ``interfaces/cli.py`` 的 ``ConfigError`` 同一个码），并且
**在启动 delegate 之前**发生 —— 所以一个 2 加上 stderr 里那句「未派发：…」读作
「这一轮没有开始」，而任何别的非零读作「delegate 跑过、结局是它给的」。
"""

from __future__ import annotations

import json
import os
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from marketops.adapters.agent_runtime import run_delegate
from marketops.adapters.build_stamp import LOADED_BUILD, BuildStamp, BuildVerdict, compare_builds
from marketops.adapters.quay_cli import (
    read_task_labels,
    split_worker_argv,
    worker_mcp_servers,
    worker_prompt_target,
)
from marketops.adapters.server_build import read_server_build
from marketops.application.dispatch import work_item_id_from_labels
from marketops.domain.errors import ConfigError
from marketops.domain.util import check_slug_safe
from marketops.security.identity import TOKEN_ENV, TOKEN_RING_ENV, Identity, claim_identity

__all__ = [
    "DATA_ROOT_ENV",
    "DELEGATE_ENV",
    "EXIT_REFUSED",
    "REQUIRED_MCP_SERVERS",
    "RESEARCH_WORKER_PROMPT",
    "WORKER_ROOT_ENV",
    "build_receipt",
    "main",
    "research_prompt",
]

#: 跑哪个 agent runtime 由**工作区**决定，所以它从环境来。没有缺省值：一个缺省的
#: delegate 会让「忘了配」表现成「跑起来了但谁也没干活」——两者都非零，而运维读到
#: 的是完全不同的两句话。
DELEGATE_ENV = "MARKETOPS_RESEARCH_DELEGATE"

#: 本轮的隔离运行目录建在哪个根下面（模块 docstring 讲了为什么需要它）。同样由**工作区**
#: 决定：只有部署侧知道 ``ARTIFACTS_ROOT`` 在哪，把它写死进本仓就等于让所有部署共用
#: 这一台机器的路径。没有缺省值的理由与 ``DELEGATE_ENV`` 同一条，只是后果更隐蔽 ——
#: 一个缺省的根会把本轮的 scratch 落进**部署工作区**，而那正是这一格要关掉的事，
#: 且它会「看起来成功」。
WORKER_ROOT_ENV = "MARKETOPS_WORKER_ROOT"

#: 这个部署的运行态根。**本层只读它一个东西**：服务在 ``state/server-build.json`` 里
#: 报下的那份构建（§15(b) 的核对，见 ``_require_build_consistency``）。
#:
#: 为什么这个变量是这一层可以读的：它与 ``MARKETOPS_WORKER_ROOT`` 指向**同一个根**
#: （启动器里后者就是 ``$MARKETOPS_DATA_ROOT/workers``），所以本层不是在向部署索取
#: 一件新事实，而是在读一条已经在用的耦合 —— 服务与 worker 本来就必须对「运行态在哪」
#: 达成一致，否则 worker 读到的是一批与它调用的服务无关的状态。
#:
#: 没有缺省值，理由与 ``DELEGATE_ENV`` / ``WORKER_ROOT_ENV`` 同一条：一个缺省的根会让
#: 本层去核对**另一个部署**的构建，而结论会是「一致」。
DATA_ROOT_ENV = "MARKETOPS_DATA_ROOT"

#: 本层拒绝时的退出码。与 ``interfaces/cli.py`` 同源：那边缺配置也是 2。
EXIT_REFUSED = 2

#: 子 claude 允许看见的 MCP server，**恰好**这些。多一个少一个都拒绝。
#:
#: 这里是**策略**而不是事实：事实（这条 argv 里那几个旗标拼出了什么）住在
#: ``adapters/quay_cli.py`` 的 ``worker_mcp_servers``，本常量是对它的裁决。分成两层的
#: 用处是：Quay 改了拼法那天，红的是 adapter 的那条判据（「读不出来了」），而不是这里
#: 被读成「工具面变宽了」—— 两句诊断指向两个不同的处置。
REQUIRED_MCP_SERVERS = ("marketops",)

#: delegate 收到的 prompt。**过程，不是内容** —— §2.14 H3a 逐字：「指引走
#: ``get_assignment``，**不**走 Quay 的 task 正文」。所以这里一个市场词都没有，
#: 也不含任何一条指引：它只说「去问谁、拿什么当证据、从哪里交、卡住了怎么报」。
RESEARCH_WORKER_PROMPT = """\
You are the MarketOps research worker for exactly one work item: {work_item_id}

Quay filed the task you were dispatched as; the market side owns your guidance and the
closing. Do not edit anything in this workspace (``tasks/``, git state, ``.quay/``) and
do not change any task's status — a worker that moves its own board entry makes the
board and the ledger disagree while both look healthy.

Work in this order:

1. Call the ``marketops`` MCP tool ``get_assignment`` with
   ``work_item_id="{work_item_id}"``. It returns the pinned guidance for this work item,
   the subject, the attempt you hold and your remaining budget. That guidance **is** your
   assignment; this prompt deliberately carries no research content.
2. Do the research with the ``marketops`` MCP tools: ``search_web``, ``fetch_document``,
   ``crawl_site``. Fetching a document is what produces durable evidence. A search result
   or a snippet is a lead, not evidence.
3. Every finding must cite evidence ids you actually produced, and you must be able to
   read each one back with ``get_evidence``. If you cannot produce evidence for a claim,
   leave the claim out.
4. Submit through ``submit_findings``. If you cannot reach an honest conclusion, call
   ``decide`` with the stop reason that matches what happened and submit no findings. A
   run that reports itself ``blocked`` is a correct outcome; a fabricated evidence_ref is
   not, and it is the one mistake that cannot be repaired later.
5. Your budget is enforced by the tools themselves. Do not count requests yourself, and
   do not continue past a budget refusal.

Your exit code is the verdict the caller trusts: exit 0 only if the work above finished
(including an honest ``blocked`` decision). Any other exit code is read as "this worker
did not finish".
"""


def research_prompt(work_item_id: str) -> str:
    """delegate 收到的 prompt —— 工作单元 id 填进那份固定形状里。"""
    return RESEARCH_WORKER_PROMPT.format(work_item_id=work_item_id)


def build_receipt(stamp: BuildStamp = LOADED_BUILD) -> str:
    """一行：「这一轮是从哪棵树、哪个 commit 起来的」。

    **它答的不是「工作目录在哪」。** 工作目录是 Quay 的工作区（``tasks/`` 在那里），
    而 marketops 的代码由 venv 解析 —— 两者可能落在**两个不同的检出**里（记忆
    `cwd 会遮住 .pth，让 venv 静默解析到错的 revision`：那时候测的是碰巧站着的
    那个 revision，而且不报错）。两件事都印出来，正是因为它们不一致时除了这一行
    没有别的地方会说。

    ``读不出来`` 与「干净」在渲染上**必须不同**：前者写「读不出来」并带上理由，
    后者才写「无」。一个把两者都印成「无」的实现，会让一次无法复现的派发在日志里
    看起来是从干净检出起来的。
    """
    if stamp.revision is None:
        revision = f"读不出来（{stamp.errors.get('revision', '没有给出理由')}）"
    else:
        revision = stamp.revision
    if stamp.dirty is None:
        dirty = f"读不出来（{stamp.errors.get('dirty', '没有给出理由')}）"
    else:
        dirty = "有" if stamp.dirty else "无"
    return (
        f"加载的 marketops：{stamp.source_tree} @ {revision}"
        f"（未提交改动：{dirty}）"
    )


def _delegate_program() -> str:
    """delegate 是哪个程序。没命名就拒绝，并说出来缺的是哪一个变量。"""
    value = os.environ.get(DELEGATE_ENV, "")
    if not value or not value.strip():
        raise ConfigError(
            f"{DELEGATE_ENV} 没有设 —— 本层不知道该把这一轮交给谁。工作区的启动器"
            "（.quay/profiles.yml 里 role 的 launcher）负责 export 它。"
        )
    return value


def _worker_root() -> Path:
    """隔离运行目录的根。没命名就拒绝，并说出来缺的是哪一个变量。

    **必须是绝对路径**，这一条与「不能为空」不是同一件事：driver 起本层时 cwd 是工作区
    根，所以一个相对值会解析到**部署工作区里面**（``<工作区>/workers/…``）—— 那正是这一
    格要关掉的地方，而且它看起来完全成功：目录建出来了、记录写进去了、delegate 也在跑。
    拒绝的理由因此不是「相对路径不正规」，是「这个值的意思取决于谁站在哪里」。
    """
    value = os.environ.get(WORKER_ROOT_ENV, "")
    if not value.strip():
        raise ConfigError(
            f"{WORKER_ROOT_ENV} 没有设 —— 本层不知道该把这一轮的隔离运行目录建在哪里。"
            "工作区的启动器（.quay/profiles.yml 里 role 的 launcher）负责 export 它。"
        )
    root = Path(value)
    if not root.is_absolute():
        raise ConfigError(
            f"{WORKER_ROOT_ENV} 是相对路径（{value!r}）—— 本层的 cwd 是**部署工作区根**，"
            "所以它会解析到工作区里面，而本层要的恰好是工作区外面。给绝对路径。"
        )
    return root


def _isolated_run_dir(
    worker_root: Path, *, task_id: str, work_item_id: str, now: datetime
) -> Path:
    """本轮那个目录：``<根>/<工作单元>/<任务>/<UTC 时刻>-<pid>/``。

    三个成分各挡一件事：

    * 两个 id 过 ``check_slug_safe`` —— 它们直接当路径成分用，而它们从**外面**来
      （任务 id 出自 driver 的 prompt、工作单元 id 出自工作区里的 label 文件）。一个
      带 ``/`` 的 id 会让目录跑到根外面去。再做一次 ``.``/``..`` 的检查：``check_slug_safe``
      拦的是「会让文件名变成路径」的字符，而 ``..`` 本身不含那些字符，却是一个能上跳
      的路径成分 —— 两件事，两个检查。

    **这就是全部**，没有第三条「拼出来再看一眼在不在根里面」的断言。它本来在这里，
    变异测试证明它是**到不了**的：上面两条挡住之后，那条断言在任何输入下都不会为假
    （关掉 `.`/`..` 那一条，它才醒过来）—— 而一个到不了的判据与一个永远绿灯的健康标志
    是同一种东西。成分都合法（绝对根 + 不含分隔符 + 不是 ``.``/``..`` 的非空成分）时，
    拼出来的路径**就是**根下面的 `root/id/id/stamp`，这句话是构造出来的，不是需要再量
    一次的。
    * 时刻用 UTC、带 ``Z``、带微秒：这一格会被人拿别的日志对时间，而本机同时存在两种
      时区的日志 —— 写一个没有时区的时刻比不写更坏。微秒那一格挡的是「同一个进程在同
      一微秒里被调两次」，而那**不是**假想的：测试就是那样调的（``main()`` 被同一个
      进程连调两次），生产里一条派发一个进程。
    * ``pid`` 是给读日志的人的一个交叉引用（本层的收据、``ps``、driver 的记录里都有它）。
    """
    check_slug_safe(work_item_id, "work_item_id")
    check_slug_safe(task_id, "quay task_id")
    for name, value in (("work_item_id", work_item_id), ("quay task_id", task_id)):
        if value in {".", ".."}:
            raise ValueError(f"{name} 是 {value!r} —— 那是一个能上跳的路径成分，不是 id")
    stamp = now.astimezone(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    return worker_root / work_item_id / task_id / f"{stamp}-{os.getpid()}"


def _write_run_record(
    run_dir: Path,
    *,
    task_id: str,
    work_item_id: str,
    workspace_root: Path,
    delegate: str,
    servers: Sequence[str],
    build: BuildCheck,
    identity: dict[str, object],
    now: datetime,
) -> None:
    """把「这一轮是什么」写进隔离目录里的 ``run.json``。

    它是本层**唯一**的产物。写在这里而不是工作区：一次派发不在工作区里留下任何东西
    （那条判据在 ``tests/test_research_worker.py``），而一份关于某次派发的记录留在
    部署工作区里，恰好就是那条判据要挡的形状。

    ``cwd`` 与 ``workspace_root`` 是两个字段而不是一个：前者是 delegate 真正站的地方，
    后者是 ``tasks/`` 与 label 的来源。它们不一致是**正常的**（那正是这一节干的事），
    所以不能合成一格 —— 合成之后，读的人无法分辨「隔离生效了」与「写错了根」。
    """
    record = {
        "quay_task_id": task_id,
        "work_item_id": work_item_id,
        "delegate": delegate,
        "started_at": now.astimezone(UTC).isoformat(),
        "cwd": str(run_dir),
        "workspace_root": str(workspace_root),
        "loaded_marketops": {
            "source_tree": LOADED_BUILD.source_tree,
            "revision": LOADED_BUILD.revision,
            "dirty": LOADED_BUILD.dirty,
            "errors": dict(LOADED_BUILD.errors),
        },
        # §15(b)：上面那一格说的是**本层**加载的代码。这一格说的是「它核对过服务那一份」
        # —— 两格并排放在这里，是因为一份没有核对过的回执与一份核对过且一致的回执，
        # 在事后看起来是同一份（`loaded_marketops` 那一格两次都写着同一个值）。
        "build_consistency": build.payload(),
        # §15(a)：这一轮用的是**哪一个身份**。这一格以前哪里都没有 —— 身份只出现在
        # 启动器往 stderr 打的一行字里，而那行字不属于任何产物。一份事后要回答
        # 「这一轮是哪个人在做事」的记录，不能只有一个「他去哪读的令牌文件」。
        "identity": identity,
        "mcp": {
            "source": "argv 上的 --mcp-config（内联 JSON，由 driver 从 .mcp.json 读出）",
            "strict": True,
            "servers": list(servers),
        },
        "why_no_attempt_id": (
            "租约（attempt）由 worker 第一次 get_assignment 铸造，启动这一刻还不存在；"
            "所以目录按启动器真正持有的两个身份命名，而不是按一个猜出来的编号。"
        ),
    }
    (run_dir / "run.json").write_text(
        json.dumps(record, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _workspace_root(named: str) -> Path:
    """driver 在 prompt 里说的根，必须就是我站着的这个地方。

    ``tasks/<id>.md`` 是从 cwd 读的，而同一个任务 id 在两棵树里都可能存在 ——
    不比对就会读到「碰巧站着的」那一棵里的同 id 任务，也就是把这一次派发绑到了错的
    工作单元上。误拒的代价是一次响亮的停摆，误放的代价是一次静默的错派。

    两边都 ``resolve()``：driver 用哪个拼法说这个路径不是我们能假设的事，而
    ``/a/b`` 与 ``/a/b/`` 指向同一个地方。
    """
    here = Path.cwd()
    if Path(named).resolve() != here.resolve():
        raise ConfigError(
            f"prompt 说的是 ``Repo root: {named}``，而本层在 {here} —— 两棵树里都"
            "可能有这个任务 id，所以这里停下，而不是读一份可能不属于这次派发的任务。"
        )
    return here


def _require_mcp_surface(forwarded: Sequence[str]) -> tuple[str, ...]:
    """转发给 delegate 的那一段 argv 必须恰好开出 ``REQUIRED_MCP_SERVERS``，并把它交回去。

    在**启动 delegate 之前**抛，所以一次工具面变宽的派发不是「跑起来再发现」：那一轮
    的后果是模型拿着多出来的工具去过一遍任务，而窗口里不会有任何一行日志说这件事。

    读不出来（``worker_mcp_servers`` 拒绝）与读出来不相等**都是拒绝**，但消息不同：
    前者是「这条 argv 换了形状」，后者是「形状没变、名单变了」—— 运维要能分开。

    返回值是**核对过的那一份**（不是从配置里再读一次的），所以写进 ``run.json`` 的那一格
    与刚才放行的依据是同一个读数 —— 分成两次读，某一天会出现「放行的是 A、记下来的是 B」。
    """
    try:
        servers = worker_mcp_servers(forwarded)
    except ValueError as exc:
        raise ConfigError(f"读不出这次派发的工具面：{exc}") from exc
    if servers != REQUIRED_MCP_SERVERS:
        extra = tuple(name for name in servers if name not in REQUIRED_MCP_SERVERS)
        missing = tuple(name for name in REQUIRED_MCP_SERVERS if name not in servers)
        raise ConfigError(
            f"这次派发给子 claude 开出的 MCP server 是 {list(servers)}，而本层只允许"
            f"{list(REQUIRED_MCP_SERVERS)}（多出 {list(extra)}、缺 {list(missing)}）。"
            "多出来的工具面是**在提示词之外**放宽的权限，所以这里停下，而不是让模型"
            "自己遵守。名单在 .quay/profiles.yml 的 role 上。"
        )
    return servers


@dataclass(frozen=True)
class BuildCheck:
    """§15(b) 那一次核对**通过之后**的读数：结论、服务报下的那一份、以及它是谁写的。

    **只有 ``agrees`` 的那条路能构造出它**，所以 ``stamp`` 与 ``path`` 都不是可选值
    —— 这是刻意的：一个「可能读不出来」的服务侧指纹，会让回执里那一行出现
    ``<读不出来>`` 这种分支，而那条分支**到不了**（读不出来时本层已经在核对里停下了）。
    一个到不了的分支与一句永远绿灯的健康标志是同一种东西，所以这里让类型把它挡掉。
    """

    verdict: BuildVerdict
    path: Path
    stamp: BuildStamp
    pid: int | None

    def payload(self) -> dict[str, Any]:
        """进 ``run.json`` 的那一格。``verdict.payload()`` 是措辞的唯一来源。"""
        return {
            **self.verdict.payload(),
            "server_build_path": str(self.path),
            "server_pid": self.pid,
            "server_read_at": self.stamp.read_at,
            "server_dirty": self.stamp.dirty,
        }

    def render(self) -> str:
        """给终端看的那一行：**报出「核对过」，以及核对的是哪一个进程**。

        报 ``pid`` 与它加载代码的时刻，理由与 ``server_build.publish_server_build``
        里那个 ``pid`` 相同：读日志的人要能把这一行与 ``ps`` 里的某一行对上，否则
        「我核对过」与「我核对的是另一个部署」在事后是同一行字。
        """
        loaded = f"{self.stamp.read_at} 加载"
        if self.stamp.dirty:
            # ``dirty`` 参与报告、不参与判定（``compare_builds`` 的 docstring 写了理由），
            # 所以它必须**出现在这一行上**：不写的话，「两侧是同一棵树上的未提交改动」
            # 这件事在回执里就消失了，而它正是「这一轮能不能被复现」的答案。
            loaded += "，有未提交改动"
        return (
            f"构建核对：与服务的构建一致（服务 pid {self.pid}，{loaded}；"
            f"{self.stamp.source_tree}）"
        )


def _data_root() -> Path:
    """这个部署的运行态根 —— 本层有两个读者，所以它单独成一个函数。

    一个是 §15(b) 的构建核对（服务在 ``state/server-build.json`` 里报下的那一份），
    另一个是 §15(a) 的身份锁（``state/identities/<主体名>.lock``）。两处各读一遍
    环境变量曾经各自带着一句「为什么必须有缺省值」的措辞，而那两句话说的是同一件
    事 —— 分成两处，出现的第一种漂移会是「一处拒绝了、另一处拿着空串往下走」。

    没有缺省值，理由与 ``DELEGATE_ENV`` / ``WORKER_ROOT_ENV`` 同一条：一个缺省的根
    会让本层去核对**另一个部署**的构建（结论会是「一致」），或者去锁**另一个部署**的
    身份（于是两个 worker 共用同一把锁而彼此都以为自己是独占的）。
    """
    named = os.environ.get(DATA_ROOT_ENV, "")
    if not named.strip():
        raise ConfigError(
            f"{DATA_ROOT_ENV} 没有设 —— 本层没法核对「本层加载的 marketops」与"
            "「服务加载的 marketops」是不是同一份，也没法知道身份锁该落在哪里。\n"
            "这一格是 §15(b) 的失败关闭点：不核对就派发的 worker 会在记录里主张一份"
            "服务并不在跑的代码。工作区的启动器负责 export 它 —— 它与 "
            f"{WORKER_ROOT_ENV} 指向同一个运行态根（后者就是它的 ``workers/``）。"
        )
    return Path(named)


def _claim_identity() -> Identity:
    """这一轮用哪个研究员身份 —— 从**服务会判的那一份环**里取（§15(a)）。

    读环、候选、锁与全部措辞都在 ``security/identity.py``：这一层只负责把「环在哪」
    说出来，以及把拿到的令牌放进环境。放进环境的动作在这里而不是在启动器，理由是
    **锁的寿命必须与这一轮一样长** —— 锁在谁手里，谁就得活到 delegate 结束；
    启动器 `exec` 成本层之后它已经不在了（那正是它原来用 fd 9 把锁带过来的原因，
    而那条路要求「锁在 exec 之前拿」，也就要求身份在 bash 里选）。

    启动器不再 export 令牌了。**它若仍然设了那份旧令牌，本层覆盖它并记一笔**：
    那份来自 vault 的文件遍历（§15(a) 要退役的机制），而环才是服务认的那一份。
    覆盖而不是拒绝，是因为「旧启动器 + 新 worker」是一个真实存在的中间状态，
    而它此刻本来就能做对的事；记一笔，是因为一个仍然在按文件名选身份的部署
    应当被看见，而不是被本层默默修好。
    """
    named = os.environ.get(TOKEN_RING_ENV, "")
    if not named.strip():
        raise ConfigError(
            f"{TOKEN_RING_ENV} 没有设 —— 本层不知道该从哪一份环里取研究员身份。\n"
            "这一个变量必须与服务读的**同一个**：环是令牌有效性的唯一来源"
            "（盘上只有摘要），从另一个环里挑出来的令牌，服务会一律回 401 ——"
            "而那正是 §15(a) 那个缺口的样子（现场 §G1）。"
        )
    return claim_identity(Path(named), lock_root=_data_root() / "state" / "identities")


def _require_build_consistency() -> BuildCheck:
    """本层加载的这份代码，与服务加载的那份，是不是同一份（§15(b)）。

    在**启动 delegate 之前**判。一条不一致的派发的后果很具体：这一轮的每一条发现都由
    一份「与服务不同的代码」产生，而 ``run.json`` 里写着的是本层那一份 —— 报告于是
    主张了一件不成立的事，而且**没有任何一行日志说破它**。现场（worker 的 ``PYTHONPATH``
    与服务器差 47 个提交）见 ``docs/PRODUCT_LOOP_ACCEPTANCE_2026-09-24.md`` §G3。

    判据与措辞都不在这里，在 ``adapters/build_stamp.compare_builds``：那一句拒绝会被
    本层原样印在 stderr 上，也会原样进 ``run.json`` —— 两处各拼一遍，就会出现
    「日志说 A、记录说 B」。

    三样东西读不出来（数据根没配 / 服务没报过 / 文件坏了）与「比下来不一致」**都是
    拒绝**，但消息不同：前者是部署没接好（先看服务起没起过、数据根指的是不是同一个），
    后者是两侧真的加载了两份代码。与 ``_require_mcp_surface`` 同一取向。

    Raises:
        ConfigError: 数据根没配、服务报的构建读不出来、或两侧比下来不一致。
    """
    named = os.environ.get(DATA_ROOT_ENV, "")
    reading = read_server_build(_data_root())
    if reading.stamp is None:
        raise ConfigError(f"核不了构建（{DATA_ROOT_ENV}={named}）：{reading.problem}")
    verdict = compare_builds(LOADED_BUILD, reading.stamp)
    if not verdict.agrees:
        raise ConfigError(verdict.message)
    return BuildCheck(verdict, reading.path, reading.stamp, reading.pid)


def main(argv: Sequence[str] | None = None) -> int:
    """跑一轮。返回值就是进程退出码。

    三段，顺序是承重的：**核对**（这一段一个字节都不写，任一条不过就退 2 且 delegate
    没被启动）→ **落位**（建本轮的隔离目录、写记录、chdir）→ **递出去**。第二段在两处
    之间，正是因为 delegate 一启动就会真的花预算、真的写数据：先把「这一轮站在哪里」
    定下来，再让它开始。
    """
    args = list(sys.argv[1:] if argv is None else argv)
    try:
        delegate = _delegate_program()
        forwarded, prompt = split_worker_argv(args)
        servers = _require_mcp_surface(forwarded)
        build = _require_build_consistency()
        target = worker_prompt_target(prompt)
        root = _workspace_root(target.repo_root)
        work_item_id = work_item_id_from_labels(read_task_labels(root, target.task_id))
        now = datetime.now(UTC)
        run_dir = _isolated_run_dir(
            _worker_root(), task_id=target.task_id, work_item_id=work_item_id, now=now
        )
        command = [delegate, *forwarded, "-p", research_prompt(work_item_id)]
        # 启动器可能仍然 export 了那份按文件名选的旧令牌（§15(a) 退役的机制）。
        # 先读、再覆盖：这个布尔进记录，于是「这一次是谁在做事」有两个可核对的读数。
        #
        # 领身份放在**这一段的最末尾**：它是这里唯一一件拿到手却要活到函数出口的东西，
        # 放最后就不必给前面那一串拒绝各写一次放锁（它们都在它之前，手上没有锁）。
        stale_token = bool(os.environ.get(TOKEN_ENV, "").strip())
        identity = _claim_identity()
        os.environ[TOKEN_ENV] = identity.token
    except (ConfigError, ValueError) as exc:
        print(f"未派发：{exc}", file=sys.stderr)
        return EXIT_REFUSED

    try:
        try:
            run_dir.mkdir(parents=True, exist_ok=False)
            _write_run_record(
                run_dir,
                task_id=target.task_id,
                work_item_id=work_item_id,
                workspace_root=root,
                delegate=delegate,
                servers=servers,
                build=build,
                identity=identity.payload() | {"launcher_token_replaced": stale_token},
                now=now,
            )
        except OSError as exc:
            # 只收 ``OSError``：``mkdir`` 另一种失败是 ``ValueError``（id 里嵌了 NUL 时的
            # "embedded null byte"），而实测那个形状到不了这里 —— label 里有 NUL 的任务文件
            # **在前言那一层就被拒了**（``read_task_labels``：``unacceptable character #x0000``）。
            # 写一个收 ``ValueError`` 的分支会是一条到不了的判据。
            print(f"未派发：隔离运行目录 {run_dir} 用不了：{exc}", file=sys.stderr)
            return EXIT_REFUSED

        override = (
            f"{TOKEN_ENV}：启动器设过一份令牌，已用环里这一个覆盖"
            "（那份来自按文件名选身份，那个机制已被取代 —— §15(a)）\n"
            if stale_token
            else ""
        )
        print(
            f"marketops research worker：任务 {target.task_id} → 工作单元 {work_item_id}"
            f"（delegate：{delegate}）\n"
            f"{build_receipt()}\n"
            f"{build.render()}\n"
            f"研究员身份：{identity.principal}（锁 {identity.lock_path} 由本进程持有到本轮结束；"
            f"令牌取自 {identity.token_file}，在环里解析得到 —— 不看文件名）\n"
            f"{override}"
            f"工作区根：{root}（= prompt 说的 Repo root，已比对；tasks/ 与 label 从这里读）\n"
            f"隔离运行目录：{run_dir}（交给 delegate 的 cwd 是这个，不是上面那个）",
            file=sys.stderr,
        )
        try:
            return run_delegate(command, cwd=run_dir)
        except OSError as exc:
            # delegate **起不来**和 delegate 跑完失败是两件事：前者是本层的配置错。
            print(f"未派发：delegate 起不来（{delegate}）：{exc}", file=sys.stderr)
            return EXIT_REFUSED
    finally:
        # 身份锁在本轮结束时放掉，**显式关，不靠进程退出**：`lock_fd` 是一个整数，
        # 出作用域、被 GC 掉都不会关掉它 —— 于是同一个进程里的第二次派发会撞上自己的
        # 上一轮（实测的形状：「1 个研究员身份全部被在飞的 worker 占着」）。进程被 kill
        # 时内核照样放 —— 两条路径都不留「陈旧锁」这个状态，因为它不是盘上的一份状态。
        os.close(identity.lock_fd)


if __name__ == "__main__":  # pragma: no cover
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("已中断。", file=sys.stderr)
        raise SystemExit(130) from None
