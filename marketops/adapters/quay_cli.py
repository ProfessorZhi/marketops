"""``Dispatcher`` 的一个实现：把外部执行器当作**一个子进程**来用。

## 本模块的每一条设计都来自一次实测，不是来自它的帮助文档

下面的事实全部在 ``2026-09-22`` 于本机跑出来。**构建身份是事实的一部分**，
所以两处都标出来 —— 因为其中一条（退出码）在两个构建上**不一样**，而那是本
适配器最重要的一条设计约束的直接证据。

* 本文件的探针构建：``artifacts/quay-cli-build/quay/packages/quay/bin/quay.js``，
  ``--version`` 报 ``0.4.0``。
* ``docs/MVP_MIGRATION_PLAN.md`` §2.9 的探针构建：插件缓存里的 ``0.11.0``。

| # | 实测 | 对适配器的后果 |
|---|---|---|
| M1 | 每次调用都往 stderr 打一行横幅（``quay-native mcp: serving tasks from …``），**成功时也有** | **stderr 非空不是失败信号**。判据只能是 stdout 上有没有一份可读的 payload |
| M2 | CAS 冲突：退 1，**stdout 是空的**，报文 + 一个 Node 栈在 stderr 上 | 失败路径要解析 stderr，且不能指望有一条 payload |
| M3 | 门失败（``task check``）：本构建退 **1** 而 stdout 上**仍是一份合法 JSON**；§2.9 在 0.11.0 上量到的是退 **0** | **退出码连跨构建都不稳定。** 同一个门的同一个裁决，两个构建两个码 —— 所以「不读退出码」不是为了绕开某个怪癖，是因为那个数字**不承载**判断 |
| M4 | 对一个不存在的 id 带 ``--expect-status`` 去 edit：退 1，``… does not exist yet; creating a new task requires --title`` | **任务没有被建出来。** 但下面 M11 说明那句「requires --title」有一半是误导 |
| M11 | 同一发，**再带上** ``--title``：退 1，报文变成 ``CAS conflict … actual current status is "null"`` | **任务仍然没有被建出来** —— 所以 ``--title`` 不是一把复活钥匙。它降级的是**诊断**：见 ``_transition_argv`` |
| M5 | 对一个已存在的 id **create**：退 0，且 ``title`` 被**改掉** | create 是 upsert。**它不能当存在性判据用** —— 所以 ``create`` 之后必须回读 |
| M6 | 不带 ``--labels`` 的 ``edit``：已有 label **保留** | 状态迁移不会把关联抹掉，这是第 4、5 条能成立的前提 |
| M7 | 列表查询的过滤旗标是 **``--label``（单数、可重复、AND）**；``--labels``（**复数**，建/改任务那个拼法）在列表上是**静默忽略**的：退 0，**返回全表** | 这是整条边界上最贵的一个陷阱：「按 label 查，没有就建」的循环如果写错拼法，会把「全表非空」读成「已经建过了」。判据钉在 ``_find_argv`` 上，且假 CLI 会**照着真行为**忽略复数拼法 |
| M8 | ``--labels`` 的值**只按逗号切**，``.filter(Boolean)`` 丢空项；带空格的 label 原样保留 | 含逗号的 label 写不进去（会被切成两个），所以它必须**被拒绝**而不是被写坏 |
| M9 | 任务 id 拒绝 ``/`` 与空字节（``path-traversal guard, ADV-004``），但**接受**小写与 ``:`` | 我们生成的 id 是十六进制加连字符 —— 是本仓库 ``check_canonical_id`` 的严格子集，两个判据都过 |
| M10 | ``--root`` 指向一个没有 ``.quay/config.yml`` 的目录：退 1，报文点明它**向上找过** | 工作区根不是一个可以猜的东西；``--root`` 失败**关闭**，不回落当前目录（与源码里那条同向） |

## 由此得到的两条纪律

**一、只读 payload，不读退出码。** ``_invoke`` 明确 ``check=False`` 并把那个数字
原样带回去 —— 它**进消息**（运维要看到它），但它**不参与任何判断**。判断只有一次：
stdout 上那份文本能不能解析成 JSON。

**二、四个动词的 argv 是四个**纯函数**。** ``_find_argv`` / ``_create_argv`` /
``_transition_argv`` 都是模块级的纯函数，判据直接断言它们产出的列表。M7 与 M4 这
两个陷阱都不是「实现里记得小心」能守住的：它们的错法**退 0**，而一个退 0 的错法
在集成测试里长得像成功。把 argv 拿出来当判据，是让那两个错法在**单元测试**里就红。
"""

from __future__ import annotations

import json
import re
import subprocess
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from ..application.dispatch import external_task_id
from ..domain.errors import ConfigError, DispatchError, ExternalStateError
from ..ports.dispatch import Dispatcher, ExecutionUnit, ExternalTask, WorkerExitRecord

__all__ = [
    "TASKS_DIRNAME",
    "WORKER_MCP_CONFIG_FLAG",
    "WORKER_OUTCOME_REL",
    "WORKER_PROMPT_FLAG",
    "WORKER_STRICT_MCP_FLAG",
    "QuayCliDispatcher",
    "WorkerPromptTarget",
    "read_task_labels",
    "read_worker_outcomes",
    "split_worker_argv",
    "worker_mcp_servers",
    "worker_prompt_target",
]

#: 每次调用都会打的那行横幅的开头（M1）。分类 stderr 时要把它滤掉，否则
#: 「stderr 上有一行东西」会被读成「出事了」。
_BANNER = "quay-native mcp:"

#: CAS 冲突报文里那两个状态。**解析它只是为了让消息更好读** —— 分类靠的是行首
#: 那半句 ``CAS conflict``。匹配不上时仍然必须抛 ``ExternalStateError``：
#: 一个正则没命中就把一次 CAS 冲突降级成一次基础设施故障，是这类适配器最典型的
#: 说谎方式（调用方会去重启执行器，而正确的处置是重读状态）。
_CAS_STATUSES = re.compile(
    r'expected status "(?P<expected>[^"]*)" but actual current status is "(?P<actual>[^"]*)"'
)

#: 任务在外部不存在时，对面回的两种措辞（M4 与 ``view`` 各一种）。**两种都要认**：
#: 只认一种会让另一种落进「基础设施故障」，而它们的处置是同一件事。
_MISSING_MARKERS = ("no such task:", "does not exist yet")


def _check_label(label: str) -> str:
    """一个 label 必须是能**原样写、原样查回来**的。不满足就抛，不修。

    四条禁令，各自对应一次实测：

    * 空 —— ``--labels ""`` 会被 ``.filter(Boolean)`` 丢掉（M8），于是它以
      「写下去了」的样子什么也没写；
    * 含逗号 —— 值只按逗号切（M8），``a,b`` 会**静默变成两个 label**，而其中
      哪一个都不是调用方要的那个；
    * 首尾有空白 —— 写进去的是 ``" a"``，查询要按 ``" a"`` 才找得到它，而任何
      一个人手写的 ``a`` 都找不到。这是「需要规范化的输入」，本仓库对它的处置一律
      是拒绝（见 ``domain/util.py::check_canonical_id`` 的那段说明）；
    * 含换行 —— 它会把一个值变成两行，而对面按行读。

    **不做归一化**：一个会替调用方修剪空白的实现更友好，但它把一次错误变成两个
    不同的 label 静默合并 —— 而 label 是这条边界上唯一的关联。
    """
    if not label:
        raise ValueError("label 不能为空 —— 空 label 会被对面静默丢掉，什么也查不回来")
    if "," in label:
        raise ValueError(
            f"label 不能含逗号：{label!r}。对面按逗号切分，于是它会静默变成两个 label，"
            "而两个都不是你要的那个。"
        )
    if label != label.strip():
        raise ValueError(
            f"label 首尾不能有空白：{label!r}。写进去的是带空白的那一个，"
            "而别人手写的不带空白 —— 两串永远匹配不上。"
        )
    if "\n" in label or "\r" in label:
        raise ValueError(f"label 不能含换行：{label!r}")
    return label


def _checked_labels(labels: Sequence[str]) -> tuple[str, ...]:
    """一组 label：逐个校验，**并拒绝空的那一组**。

    空的那一组不是「全都要」，是「不加过滤」—— 在外部那张混着别人工作的表上，
    它是一句「把外面的东西全读进来」。所以它不是缺省，是一个错误。
    """
    if not labels:
        raise ValueError(
            "至少要一个 label。一次不按 label 收窄的查询在外部那张表上会把别人的"
            "工作也读进来 —— 那不是「查全部」，那是一次越界读。"
        )
    return tuple(_check_label(label) for label in labels)


# ----------------------------------------------------------------------
# 四个 argv：纯函数，判据直接钉在它们上面
# ----------------------------------------------------------------------
def _common_args(cli_root: Path) -> list[str]:
    """每条命令共有的尾巴。``--json`` 是我们唯一读得懂的输出形态。"""
    return ["--json", "--root", str(cli_root)]


def _find_argv(labels: Sequence[str], *, cli_root: Path) -> list[str]:
    """按 label 查。**``--label`` 单数、重复一次一个**（M7）。

    写成 ``--labels`` 不会报错 —— 它会**静默忽略过滤并返回全表**。那正是
    「按 label 查、没有就建」这个循环会被它毁掉的方式：全表非空 ⇒ 读成
    「已经建过了」⇒ 那句话对每一个单元都为真。
    """
    argv = ["task", "list"]
    for label in labels:
        argv += ["--label", label]
    return argv + _common_args(cli_root)


def _create_argv(
    task_id: str, unit: ExecutionUnit, *, labels: Sequence[str], status: str, cli_root: Path
) -> list[str]:
    """建一个任务。``--labels`` 在这里是**复数**，值是逗号拼起来的一串（M8）。

    与 ``_find_argv`` 的拼法**刻意不同**，因为对面的两个子命令收的旗标名不同
    （M7）。这不是笔误，两处都有一条实测钉着。

    ``--body`` 是**逐字实测存在**的旗标（``task create <id> --title <t>
    --body <text>``；它与 ``--body-file`` 互斥，照抄的是对面自己那句
    ``--body and --body-file are mutually exclusive``）。这一段是调用方给的，
    适配器**不渲染、不截断、不重排**：它不知道这件事是什么，而一个在这里加工的
    适配器会让「本仓写下去的」与「外部读到的」变成两份不同的东西 —— 那正是这条
    边界最不该有的一种偏差。
    """
    return [
        "task",
        "create",
        task_id,
        "--title",
        unit.title,
        "--body",
        unit.body,
        "--status",
        status,
        "--labels",
        ",".join(labels),
        *_common_args(cli_root),
    ]


def _transition_argv(
    task_id: str, *, expected: str, status: str, cli_root: Path
) -> list[str]:
    """一次 CAS 状态迁移。

    ## 这里**没有** ``--title``，但理由不是「不加它会复活任务」

    第一版这里写的是「``--title`` 是一把把迁移变成创建的钥匙」。**那句话是错的**，
    而且是一条被变异测试抓出来的错话：把它删掉之后，子进程那一层的判据一条都没红。

    去量了一次（M11），真实行为是这样：

    * ``--expect-status X`` + 任务不存在 + **无** ``--title`` ⇒ 退 1，
      ``… does not exist yet; creating a new task requires --title``；
    * ``--expect-status X`` + 任务不存在 + **有** ``--title`` ⇒ 退 1，
      ``CAS conflict … expected status "X" but actual current status is "null"``；
    * **两种情况下任务都没有被建出来。**

    所以 ``--expect-status`` 自己就挡住了 upsert，``--title`` 不是钥匙。那为什么
    仍然不带它？因为它**降级诊断**，而降级的方向恰好坏在那条最要紧的路径上：

    本地那条映射指向的外部任务被删掉之后，我们要说给调用方听的一句话是
    **「那个任务不在了，去查是谁删的」**。带上 ``--title`` 之后，同一件事会被报成
    **「有别人先写了，现在读到的状态是 〝null〞」** —— 一句**并发写**的话。

    而这两句话的处置相反（前者：停下重查映射；后者：按新状态重新决定）。把一个
    被删掉的任务报成一次并发写，正是 ``ExternalStateError`` 的 docstring 逐字禁止
    的那件事 —— 而那是一条**只在带 ``--title`` 时才出现**的说谎路径，不加它就没有。

    这也不只是「消息更好看」：``_MISSING_MARKERS`` 认的是 ``does not exist yet``，
    于是带着 ``--title`` 的那一发会落进 CAS 那一支，而那支回的消息里两个状态之一
    是 ``null`` —— 一个调用方无法据以行动的字符串。

    这里也**没有** ``--labels``：实测 ``edit`` 不带它时已有 label 保留（M6），
    带上它则是**整体替换**（源码里那句 ``--labels … (replaces existing labels)``）
    —— 一次迁移顺手重写关联，等于每次状态变化都在赌自己记得全那一组 label。
    """
    return [
        "task",
        "edit",
        task_id,
        "--expect-status",
        expected,
        "--status",
        status,
        *_common_args(cli_root),
    ]


# ----------------------------------------------------------------------
# 入站的 argv：Quay 的 driver 交给 launcher 的那一份（M12）
# ----------------------------------------------------------------------
# 上面四个 ``_*_argv`` 的方向是**本仓 → 外部**；这一节的方向相反。它存在的理由是
# §6 那条链的最后一跳：Quay 的 worker-driver 会 ``spawn`` 一个 worker 进程，而那个
# 进程是**研究 worker 的入口**（``.quay/profiles.yml`` 里 ``task-worker`` 的
# ``launcher``）。它拿到的 argv 就是 ``launchArgv`` 拼的那一串，形如::
#
#     <launcher> --settings <file> --exclude-dynamic-system-prompt-sections
#                --prompt-suggestions false [--strict-mcp-config --mcp-config <json>]
#                [-n <name>] -p "<driver prompt>" [--session-id <uuid>]
#
# 实测构建：0.11.0 的 ``scripts/dist/worker-driver.js``（``launchArgv`` /
# ``pickSettingsFile`` / ``mcpConfigArgvSuffix``）与 ``scripts/dist/driver-runtime.js``
# （``launchSettingsArg``）。**本仓不修改 Quay**，所以这一节的每一个字都是对面给的
# 事实，不是我们争取来的接口。
#
# ## 任务身份**只有一处**来源
#
# driver 拼进 ``-p`` 的那段 prompt 里，唯一能被读成「你是为哪条任务被派发的」的
# 是这一句::
#
#     Task: <id>. Repo root: <root>.
#
# ``buildWorkerPrompt`` 与 ``buildContinueWorkerPrompt`` 各有一句，逐字相同
# （0.11.0 的 :53275 与 :53783）。别处没有：``-n`` 那个名字是 **role 级**的常量，
# ``--session-id`` 是一次会话的 uuid，``--settings`` 指向工作区，``cwd`` 是工作区根
# ——**没有一样是逐任务的**。所以这一句不是「一种方便的来源」，它是唯一的那一种，
# 而这也是为什么它读起来要严：读宽一点会在 Quay 换句话的那天**静默读错**。

#: worker prompt 所在的那个旗标。`-p` 是 Claude Code 的 print 模式旗标，Quay 把
#: prompt 作为它的**值**交给 launcher。
WORKER_PROMPT_FLAG = "-p"

#: 那句任务身份的话。两半都要求，且**只有一句**。
#:
#: * ``(?:^|\s)Task:`` —— 前面必须是空白或开头。放宽到「任意位置」会让一句引文
#:   （``他的话是「Task: …」``）被读成真的任务行；
#: * id 的首字符必须是字母或数字 —— ``Task: . Repo root: …``（少了一个 id）在
#:   ``\S*`` 的读法下会得到 ``.``，一个非空、看着像 id、在对面查不到的字符串；
#: * 两半之间要求 ``.`` 与空白，``Repo root:`` 的值取到下一个空白为止，**再剥掉
#:   一个尾句号**（``${root}.`` 里那个句号是本句的，不是路径的一部分 —— 实测那两个
#:   builder 都是这么拼的）。
_WORKER_PROMPT_SENTENCE = re.compile(
    r"(?:^|\s)Task:\s*(?P<task_id>[A-Za-z0-9][A-Za-z0-9_-]*)\.\s+"
    r"Repo root:\s*(?P<repo_root>\S+)"
)


@dataclass(frozen=True)
class WorkerPromptTarget:
    """这句 prompt 指向的东西：哪条外部任务，以及它所在的工作区根。

    两个字段**一起**读出来，而不是各读一遍：分开解析会让「这条 prompt 说的是
    **另一个**工作区的任务」变成一件看不出来的事 —— id 读出来了、根也读出来了，
    而两者不是一对。两个一起给，调用方就能拿根去比自己的工作区。
    """

    task_id: str
    repo_root: str


def split_worker_argv(argv: Sequence[str]) -> tuple[tuple[str, ...], str]:
    """把 launcher 收到的 argv 拆成 ``(转发给 delegate 的, worker prompt)``。

    **扫得动，猜不动。** 下面找出的是「值等于 ``-p`` 的那些 token」——argv 本身
    分不出旗标和恰好取了同一个值的参数，要分得知道前面每个旗标的元数，而那不是
    这条边界能假设的事。所以当这样的 token 不止一个时，回答是**拒绝**而不是挑
    一个。

    Raises:
        ValueError: 没有 ``-p``、有两处 ``-p``、或 ``-p`` 后面没有值。三种都拒绝，
            不回落：``-p`` 是可执行身份的唯一来源，猜一个的结果是 worker 去领一件
            不属于这条任务的工作，而两次调用各自的日志都正常。
    """
    found = [index for index, token in enumerate(argv) if token == WORKER_PROMPT_FLAG]
    if not found:
        raise ValueError(
            f"argv 里没有 {WORKER_PROMPT_FLAG!r} —— Quay 的 driver 把任务身份放在这个"
            f"旗标的值里，所以没有它就没有「我是为哪条任务被派发的」。argv={list(argv)!r}"
        )
    if len(found) > 1:
        raise ValueError(
            f"argv 里有 {len(found)} 处 {WORKER_PROMPT_FLAG!r}（下标 {found}）—— 挑一处"
            "就是猜，而两处指的是两条不同的任务。argv=" + repr(list(argv))
        )
    at = found[0]
    if at + 1 >= len(argv):
        raise ValueError(
            f"{WORKER_PROMPT_FLAG!r} 后面没有值 —— 它不是「空 prompt」，是一次残缺的"
            f"调用。argv={list(argv)!r}"
        )
    return tuple(argv[:at] + argv[at + 2 :]), argv[at + 1]


def worker_prompt_target(prompt: str) -> WorkerPromptTarget:
    """从 driver 的 prompt 里读出 ``Task: <id>. Repo root: <root>.``。

    Raises:
        ValueError: 那句话一次都没出现，或者出现了不止一次。**两种情况都拒绝**，
            理由相反但同一条：没出现是「这句话换了形状」（读宽一点会在那天读出
            一个假的 id），出现两次是「两条工作单元都在里面」（挑一句就是猜）。
    """
    matched = list(_WORKER_PROMPT_SENTENCE.finditer(prompt or ""))
    if not matched:
        head = (prompt or "")[:200]
        raise ValueError(
            "prompt 里没有那句任务身份的话（形如 ``Task: <id>. Repo root: <root>.``）"
            f"—— 它是 driver 交过来的**唯一**一份逐任务的身份。prompt 开头：{head!r}"
        )
    if len(matched) > 1:
        raise ValueError(
            f"prompt 里有 {len(matched)} 句任务身份的话 —— 挑第几句都是猜，而两条工作"
            "单元是两件不同的工作。"
        )
    found = matched[0]
    repo_root = found.group("repo_root")
    if repo_root.endswith("."):
        # 剥掉**一个**尾句号：``Repo root: <root>.`` 里那个句号是本句的句号，
        # 不是路径的一部分。只剥一个 —— 剥到干净为止会把一个真以句号结尾的
        # 目录名改掉（目录名以句号结尾在 POSIX 上是合法的）。
        repo_root = repo_root[:-1]
    if not repo_root:
        raise ValueError("``Repo root:`` 后面是空的 —— 半句话不是一句话。")
    return WorkerPromptTarget(task_id=found.group("task_id"), repo_root=repo_root)


# ----------------------------------------------------------------------
# 入站 argv 的 MCP 面：这份 argv 会给子 claude 开出多大的工具面
# ----------------------------------------------------------------------
# 上面两节读的是**同一条 argv** 的另一件事 —— 「它是为哪条任务被派发的」。这一节
# 读的是它会让子进程看见哪些 MCP server。
#
# 为什么不看 ``.quay/profiles.yml``：那份声明与最终 argv 之间隔着 driver 的三段
# 代码，而其中两段是**失败开放**的（实测，0.11.0）：
#
# * ``mcpConfigArgvSuffix``：``blacklist.length === 0`` ⇒ 直接返回 ``[]``，于是
#   ``--strict-mcp-config`` 与 ``--mcp-config`` **一起消失**。子 claude 退回它自己的
#   合并（用户级 ``~/.claude.json`` + 项目的 ``.mcp.json`` + 每个已装的插件）——
#   于是「没配置」与「子进程能看见宿主上的一切」在 argv 上是**同一个形状**；
# * ``resolveMcpConfig`` 解析失败时同样返回 ``[]``。
#
# 所以「配置文件里写了名单」不是判据：那句声明在失败开放的那天照样成立。判据只能是
# **这一份 argv 里最后拼出来的那个东西** —— 也就是这一节读的两个旗标。
#
# 顺带记一个形状事实，它解释了为什么上面 ``split_worker_argv`` 返回的那一段**不以
# prompt 结尾**：``launchArgv`` 拼到 ``-p <prompt>`` 就结束了，而 ``runOneWorker``
# 随后追加一对 ``("--session-id", <uuid>)``::
#
#     const argv = injectSessionId ? [...workerArgv, "--session-id", sessionId] : workerArgv;
#
# （``injectSessionId: workerCmdOpts.exact == null``，生产路径恒真；``--worker-cmd-exact``
# 是测试旁路）。所以 launcher 收到的 argv 尾巴是 ``-p <prompt> --session-id <uuid>``，
# 而那一对是**给 delegate 的**旗标，转发的就得是它。

#: 打开「只用 ``--mcp-config``，不要再去合并别的来源」的那个旗标。**它是无值旗标**，
#: 所以下面查的是「在不在」，不是「值是什么」。
WORKER_STRICT_MCP_FLAG = "--strict-mcp-config"

#: 承载 server 清单的那个旗标。值是**一段内联 JSON**（实测：driver 传的是文本，不是
#: 路径），所以解析失败是拒绝而不是「读不到东西」。
WORKER_MCP_CONFIG_FLAG = "--mcp-config"


def worker_mcp_servers(argv: Sequence[str]) -> tuple[str, ...]:
    """读出这份 argv 会让子 claude 看见的 MCP server 名（已排序的元组）。

    **没有 ``--strict-mcp-config`` 就拒绝回答。** 少了它，子进程看见的是「这份
    ``--mcp-config`` 与宿主上一切可用来源的合并」—— 那个集合不在这条 argv 里，
    所以本函数对那个问题没有答案。返回一个空元组会把「答不上来」说成「一个都没有」
    （失败开放反过来），而两者在调用方那里的处置完全相反。

    Raises:
        ValueError: 缺 ``--strict-mcp-config``、缺 ``--mcp-config``、有两处
            ``--mcp-config``、它后面没有值、值不是 JSON、或 ``mcpServers`` 不是对象。
            **六种都拒绝**，不回落成空 —— 判据是「恰好剩下哪几个 server」，而一个读不
            出清单的 argv 与一个清单里什么都没有的 argv，在「恰好等于」这条判据下
            必须给出不同的答复。

    ``mcpServers`` 键**不存在**时不算拒绝，读成空元组：那是「这份配置里一个 server
    也没配」，是一句真话。它与上面六种的区别是「有没有答案」。
    """
    if WORKER_STRICT_MCP_FLAG not in argv:
        raise ValueError(
            f"argv 里没有 {WORKER_STRICT_MCP_FLAG!r} —— 没有它，子 claude 会把这份 "
            f"--mcp-config 与宿主上其余来源**合并**，而那些来源不在这条 argv 里。"
            f"实测：driver 的 ``mcpConfigArgvSuffix`` 在排除名单为空时把这两个旗标"
            f"一起省掉（fail-open），所以缺它就说明「名单是空的」而不是「没配置」。"
            f"argv={list(argv)!r}"
        )
    found = [
        index for index, token in enumerate(argv) if token == WORKER_MCP_CONFIG_FLAG
    ]
    if not found:
        raise ValueError(
            f"argv 里有 {WORKER_STRICT_MCP_FLAG!r} 却没有 {WORKER_MCP_CONFIG_FLAG!r}"
            f" —— 严格模式配一份不存在的清单，结果是子进程一个 server 都看不到。"
            f"argv={list(argv)!r}"
        )
    if len(found) > 1:
        raise ValueError(
            f"argv 里有 {len(found)} 处 {WORKER_MCP_CONFIG_FLAG!r}（下标 {found}）"
            f"—— 挑一处就是猜，而两份清单指的是两个不同的工具面。argv={list(argv)!r}"
        )
    at = found[0]
    if at + 1 >= len(argv):
        raise ValueError(
            f"{WORKER_MCP_CONFIG_FLAG!r} 后面没有值 —— 它不是「空清单」，是一次残缺的"
            f"调用。argv={list(argv)!r}"
        )
    try:
        parsed = json.loads(argv[at + 1])
    except json.JSONDecodeError as exc:
        raise ValueError(
            f"{WORKER_MCP_CONFIG_FLAG!r} 的值不是 JSON（{exc}）—— 实测 driver 传的是"
            f"**内联 JSON**（不是文件路径），所以解析不了就是这条 argv 换了形状。"
            f"值={argv[at + 1]!r}"
        ) from exc
    if not isinstance(parsed, dict):
        raise ValueError(
            f"{WORKER_MCP_CONFIG_FLAG!r} 的值是一个 {type(parsed).__name__}，不是对象"
            f" —— 这里要的是形如 ``{{\"mcpServers\": {{…}}}}`` 的那一份。值={parsed!r}"
        )
    servers = parsed.get("mcpServers", {})
    if not isinstance(servers, dict):
        raise ValueError(
            f"``mcpServers`` 是一个 {type(servers).__name__}，不是对象 —— 名字读不出来。"
            f"值={servers!r}"
        )
    # 排序：判据是「恰好是哪几个」，而 JSON 的键序是拼出这段文本的那一侧的选择，
    # 不该让一次无害的键序变化表现成一次判据变化。
    return tuple(sorted(servers))


# ----------------------------------------------------------------------
# 入站的任务文件：driver 落盘的那一份（M12 的另一半）
# ----------------------------------------------------------------------
# 上一节读的是 driver 交给 launcher 的 argv；这一节读的是 driver 写在工作区里的
# 那份任务。**两处都要读**，因为 argv 里只有任务 id，而工作单元 id 只在文件的
# label 里（``marketops-item:<id>``，关联合约见 ``application/dispatch.py``）。
#
# 为什么不问一次 CLI（``quay task view <id> --json`` 是受支持的读法）：**这一台机器上
# 至少有三代 Quay**（``PATH`` 上的那个是插件缓存里的 0.11.0，而工作区的
# ``.quay/config.yml`` 指向另一个构建）。从 ``PATH`` 摸一个 CLI，摸到的是「碰巧站着
# 的那一代」，而它读的可能是**另一个** store —— 症状是任务读得到、label 却是别人的。
# 工作区里那个文件没有这个问题：它是 driver 正在服务的那个 store 自己写的。
#
# 代价写在这里：这是对 provider **落盘格式**的一处依赖（实测形状，0.11.0 的
# ``marketops-mvp-quay/tasks/*.md``）::
#
#     ---
#     id: MOPS-1b1b7f82-6d783321
#     title: "one-subject"
#     status: ready
#     labels:
#       - marketops-job:1b1b7f8209c19491e1a26c34ad2207a8
#       - marketops-item:6d783321a9df9b2f2c6bf4ace0cd9b26
#     parent: null
#     children: []
#     extra: {}
#     ---
#
# 与我们和 Quay 之间的契约（CLI）相比，这是一处更靠里的依赖，所以读得**很窄**：
# 只要 ``labels`` 一个键，形状不对就**拒绝**（不是读成空）。

#: 任务文件相对工作区根的位置。
TASKS_DIRNAME = "tasks"

#: 外部那本「每次 worker 退出的结局」的账，相对工作区根。driver 每收一次尾追加一行。
WORKER_OUTCOME_REL = ".quay/worker-outcome.jsonl"

#: 那本账里，本层读得懂的字段与它的类型。**读不出来的行整行拒绝** —— 见下。
_OUTCOME_FIELDS: Mapping[str, type] = {
    "ts": str,
    "task": str,
    "final_state": str,
}


def read_worker_outcomes(root: Path, task_id: str) -> tuple[WorkerExitRecord, ...]:
    """外部那本账里关于**一个任务**的行，按写下的顺序。

    ## 为什么读它

    因为它是外部对**同一次退出**的自述，而本仓的完成判据不是同一个。两者不一致
    时报告要把两边都印出来（``application/research_terminal.py`` 把这一份标成
    N/A）—— 不读它，那句「外面说没落地」在报告里就没有痕迹，而它确实发生过。

    ## 为什么这一个函数是**宽容**的，而 ``read_task_labels`` 是严的

    两处读的是不同性质的东西。任务文件的 ``labels`` 决定**这件事该由谁做**，
    读错就派错人，所以形状不对必须拒绝。而这一本账**只是证据**：它的读者是报告，
    报告需要的是「外面说过什么」，读不出来的那一行不会让任何人做错事 ——
    但它**必须**让这一格变成「读不出来」，不能变成「没有记录」。

    所以：文件不在 → ``()``（一次都没跑过，是**一个事实**）；文件在而某一行不是
    合法 JSON、不是一个对象、或缺了 ``ts``/``task``/``final_state`` 里任何一个 →
    **抛** ``ConfigError``。三条缺一不可 —— 把坏行读成「没有记录」会让「外面判了
    没落地」与「外面什么都没说」长得一样，而这两件事的处置相反。

    其余字段（``failure_reason`` / ``exit_code`` / ``wall_clock_ms``）**可以缺**：
    正常结局里外部本来就不写前两个。

    Raises:
        ConfigError: 账本存在但读不成上面那三条里的任何一条。
    """
    path = root / WORKER_OUTCOME_REL
    if not path.is_file():
        return ()
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ConfigError(f"读不出外部那本退出账 {path}：{exc}") from exc

    records: list[WorkerExitRecord] = []
    for number, line in enumerate(text.splitlines(), start=1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ConfigError(
                f"{path} 第 {number} 行不是合法 JSON：{exc}。\n"
                "读不出来的行不能当成「没有这一行」—— 那会让「外面判了没落地」"
                "与「外面什么都没说」在报告里长得一样。"
            ) from exc
        if not isinstance(row, dict):
            raise ConfigError(f"{path} 第 {number} 行不是一个对象：{type(row).__name__}")
        missing = sorted(key for key in _OUTCOME_FIELDS if key not in row)
        if missing:
            raise ConfigError(
                f"{path} 第 {number} 行缺字段 {missing} —— 那三个是这一行的主语"
                "（什么时候、哪件事、判成什么），缺一个就读不出这行是谁的。"
            )
        if row.get("task") != task_id:
            continue
        records.append(
            WorkerExitRecord(
                task_id=task_id,
                recorded_at=str(row["ts"]),
                final_state=str(row["final_state"]),
                reason=_optional_text(row.get("failure_reason")),
                exit_code=_optional_int(row.get("exit_code")),
                wall_clock_ms=_optional_int(row.get("wall_clock_ms")),
            )
        )
    return tuple(records)


def _optional_text(value: object) -> str | None:
    """一个**可以缺失**的字符串。``None`` 是「外部没写」，不是空串。"""
    if value is None:
        return None
    if not isinstance(value, str):
        raise ConfigError(f"外部那本退出账里这一格应当是一个字符串，读到 {type(value).__name__}")
    return value


def _optional_int(value: object) -> int | None:
    """一个**可以缺失**的整数。``bool`` 不是整数（它是 ``int`` 的子类，会静默通过）。"""
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise ConfigError(f"外部那本退出账里这一格应当是一个整数，读到 {value!r}")
    return value


def read_task_labels(root: Path, task_id: str) -> tuple[str, ...]:
    """读工作区里那条任务文件的前言块中的 ``labels``。**只要这一个键。**

    读进来的是**原样的字符串**：不做 strip、不做去重、不挑「看起来像的」那一条。
    这一层的职责是「文件里写了什么就说什么」，而「哪一条才是工作单元」是
    ``application.dispatch.work_item_id_from_labels`` 的事 —— 在这一层顺手挑一条，
    读者就再也分不出「文件里只有作业 label」与「文件里那一条被这一层吃掉了」。

    Raises:
        ConfigError: 文件不在、没有前言块、前言块没有结束的 ``---``、前言不是合法
            YAML、前言不是一个映射、``labels`` 不是一个字符串列表。六种都拒绝 ——
            一个读成空列表的失败会在下游表现成「这条任务不是我们派的」（听起来
            完全合理的一句话），而真相是**文件换了形状**：同一件事、两种修法，
            一个去查谁派的、一个去查 provider 的落盘格式变没变。
    """
    path = root / TASKS_DIRNAME / f"{task_id}.md"
    if not path.is_file():
        raise ConfigError(
            f"找不到任务文件 {path}。driver 派发的任务应当在这个工作区的 "
            f"{TASKS_DIRNAME}/ 下；不在的话，读不出这件事该由谁做。"
        )
    text = path.read_text(encoding="utf-8")
    try:
        document = yaml.safe_load(_frontmatter(text, path))
    except yaml.YAMLError as exc:
        raise ConfigError(f"{path} 的前言块不是合法 YAML：{exc}") from exc
    if not isinstance(document, dict):
        raise ConfigError(f"{path} 的前言块不是一个映射（读到 {type(document).__name__}）。")
    labels = document.get("labels")
    if not isinstance(labels, list) or not all(isinstance(item, str) for item in labels):
        raise ConfigError(
            f"{path} 的 labels 不是一个字符串列表（读到 {labels!r}）—— 形状不对就拒绝，"
            "不猜：把它读成空的一组，下游会报「这条任务不是我们派的」。"
        )
    return tuple(labels)


def _frontmatter(text: str, path: Path) -> str:
    """取出 ``---`` 前言块的内容（不含那两条分隔线）。"""
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        raise ConfigError(f"{path} 不是以 ``---`` 前言块开头的 —— 形状不对，拒绝。")
    for index in range(1, len(lines)):
        if lines[index].strip() == "---":
            return "\n".join(lines[1:index])
    raise ConfigError(f"{path} 的前言块没有结束的 ``---``。")


# ----------------------------------------------------------------------
# stderr 的分类
# ----------------------------------------------------------------------
def _meaningful_lines(stderr: str) -> list[str]:
    """stderr 里**承载意义**的那几行：滤掉横幅与 Node 的栈帧。

    滤掉不等于丢掉 —— 原始 stderr 一律整段进消息。这里只是决定**按哪一行分类**，
    而那件事必须发生在滤掉横幅之后：横幅在**成功时也有**（M1）。
    """
    kept: list[str] = []
    for raw in stderr.splitlines():
        line = raw.strip()
        if not line or line.startswith(_BANNER):
            continue
        if line.startswith("at ") or line.startswith("(node:"):
            continue  # 栈帧：它是「在哪一行抛的」，不是「发生了什么」
        kept.append(line)
    return kept


def _parse_stdout(text: str) -> tuple[bool, Any]:
    """stdout 上有没有一份可读的 payload。**这是全模块唯一的成败判据。**"""
    stripped = text.strip()
    if not stripped:
        return False, None
    try:
        return True, json.loads(stripped)
    except json.JSONDecodeError:
        return False, None


class QuayCliDispatcher(Dispatcher):
    """经子进程调外部执行器。三个入参都要求**显式**给，且都要求绝对路径。"""

    def __init__(
        self,
        *,
        cli_path: Path,
        node_path: Path,
        root: Path,
        timeout_seconds: float = 60.0,
        env: Mapping[str, str] | None = None,
    ) -> None:
        """
        Args:
            cli_path: 入口脚本。**不回落 PATH** —— 见 ``Settings.quay_cli_path``。
            node_path: 解释器。同上。
            root: 外部工作区的根（含它自己的 ``.quay/config.yml``）。
            timeout_seconds: 一次调用的上限。超时是 ``DispatchError``：它不是
                「对面说不行」，是「我们没有听到答案」。
            env: **在最小环境之上补充**的变量，不是整份替换。刻意**不**整份继承
                父进程的环境：那会让这条边界的行为取决于调用方的 shell，而「同一份
                代码在两台机器上跑出两种结果」正是要避免的那类不确定性 —— 何况继承
                会把父进程手里的凭据一并无偿交给一个子进程。补充而不是替换，是为了
                让「加一个变量」这件事不必先知道最小环境里有什么。

        Raises:
            ConfigError: 三样里缺一样，或路径不是绝对路径 / 不存在。**在构造时抛，
                不在第一次调用时抛**：一个「建得起来、派发时才炸」的执行器在部署
                自检里是完全隐形的。
        """
        missing = [
            name
            for name, value in (
                ("cli_path", cli_path),
                ("node_path", node_path),
                ("root", root),
            )
            if value is None
        ]
        if missing:
            raise ConfigError(
                f"外部执行器缺 {'、'.join(missing)}。三样（入口脚本、解释器、工作区根）"
                "缺一不可，且都没有缺省值 —— 每一项的缺省都会是「我猜你想跑的其实是……」，"
                "而这条路径上猜错一次就是一次真的派发。"
            )
        for name, value in (
            ("cli_path", cli_path),
            ("node_path", node_path),
            ("root", root),
        ):
            if not value.is_absolute():
                raise ConfigError(f"{name} 必须是绝对路径，收到 {value!s}")
            if not value.exists():
                raise ConfigError(f"{name} 不存在：{value!s}")
        self._cli_path = cli_path
        self._node_path = node_path
        self._root = root
        self._timeout = timeout_seconds
        self._env = {**self._minimal_env(), **dict(env or {})}

    def _minimal_env(self) -> dict[str, str]:
        """子进程的环境，逐项写出来。

        解释器所在的那个目录进 ``PATH``：本适配器自己用绝对路径起 node，但对面
        内部**可能再起一个 node**（0.11.0 的构建就是这么做的，而它在本机报了
        ``spawn node ENOENT``）—— 那件事我们控制不了，但至少能让它在 PATH 里找到。
        """
        return {
            "PATH": f"{self._node_path.parent}:/usr/local/bin:/usr/bin:/bin",
            "HOME": str(Path.home()),
            "LANG": "C.UTF-8",
            "LC_ALL": "C.UTF-8",
        }

    # ------------------------------------------------------------------
    # 唯一的执行点
    # ------------------------------------------------------------------
    def _invoke(self, argv: Sequence[str]) -> tuple[int, str, str]:
        """跑一次，回 ``(退出码, stdout, stderr)``。**退出码只被返回，不被判断。**

        ``check=False`` 是显式的：那个参数默认就是 ``False``，写出来是因为这一行
        是整条纪律的落点 —— 一个人在这里加 ``check=True`` 会把「门说不行」变成
        一个异常（M3），而那是把一个**裁决**读成一次**故障**。
        """
        full = [str(self._node_path), str(self._cli_path), *argv]
        try:
            done = subprocess.run(  # noqa: S603 -- argv 是列表，不经过 shell
                full,
                capture_output=True,
                text=True,
                encoding="utf-8",
                timeout=self._timeout,
                check=False,
                env=self._env,
                cwd=None,
            )
        except subprocess.TimeoutExpired as expired:
            raise DispatchError(
                f"外部执行器 {self._timeout:g} 秒没有返回：{' '.join(full)}。"
                "这不是「对面说不行」，是「我们没有听到答案」—— 所以处置是去查那个"
                "进程，而不是把这次调用当成一次拒绝。"
            ) from expired
        except UnicodeDecodeError as broken:
            raise DispatchError(
                f"外部执行器的输出不是 UTF-8：{' '.join(full)}。"
                "读不懂的输出与「对面报错」是两件事，而按后者处置会去重读状态。"
            ) from broken
        except OSError as failed:
            raise DispatchError(f"起不动外部执行器：{' '.join(full)}：{failed}") from failed
        return done.returncode, done.stdout, done.stderr

    def _payload(self, argv: Sequence[str], *, what: str) -> Any:
        """跑一次并**只**认 stdout 上那份 JSON。失败时按 stderr 分类抛。"""
        rc, out, err = self._invoke(argv)
        ok, body = _parse_stdout(out)
        if ok:
            return body
        lines = _meaningful_lines(err)
        detail = lines[0] if lines else "（stderr 上没有可读的一行）"
        if any(marker in detail for marker in _MISSING_MARKERS):
            raise ExternalStateError(
                f"{what}：外部那边没有这个任务。对面回的是：{detail}\n"
                "本地那条映射指向的东西不见了 —— 处置是停下重读那张映射，不是重试。"
            )
        if detail.startswith("Error: CAS conflict") or "CAS conflict on" in detail:
            found = _CAS_STATUSES.search(detail)
            if found:
                raise ExternalStateError(
                    f"{what}：有别人先写了。你按 {found['expected']!r} 读的，"
                    f"而对面现在是 {found['actual']!r}。\n对面逐字回的是：{detail}"
                )
            raise ExternalStateError(
                f"{what}：一次 CAS 冲突（报文里那两个状态没解析出来，所以原样附上）：{detail}"
            )
        if "no .quay/config.yml found under --root" in detail:
            raise DispatchError(
                f"{what}：工作区根不对。{detail}\n"
                "这条路径**失败关闭**，不会回落到当前目录 —— 跑在别人的工作区上是"
                "比跑不起来更坏的结局。"
            )
        raise DispatchError(
            f"{what}：外部执行器没能给出一份可读的答案。\n"
            f"  命令：{' '.join(argv)}\n"
            f"  退出码：{rc}（**仅供参考** —— 它不参与判断，见模块 docstring M3）\n"
            f"  stdout：{out.strip()!r}\n"
            f"  stderr：{err.strip()!r}"
        )

    # ------------------------------------------------------------------
    # Dispatcher
    # ------------------------------------------------------------------
    def find(self, *, labels: Sequence[str]) -> tuple[ExternalTask, ...]:
        wanted = _checked_labels(labels)
        body = self._payload(
            _find_argv(wanted, cli_root=self._root),
            what=f"按 {list(wanted)!r} 查任务",
        )
        if not isinstance(body, list):
            raise DispatchError(
                f"列表查询回的不是一个数组，而是 {type(body).__name__}：{body!r}。"
                "把「读不懂的答案」当零条会让调用方去建一个已经存在的任务。"
            )
        return tuple(_task_from(entry) for entry in body)

    def create(
        self, unit: ExecutionUnit, *, labels: Sequence[str], status: str
    ) -> ExternalTask:
        wanted = _checked_labels(labels)
        if not status:
            raise ValueError("status 不能为空 —— 对面会静默落回它自己的缺省，而那是它选的，不是你要的")
        if not unit.title:
            raise ValueError("unit.title 不能为空 —— 一个没有标题的任务在外部界面上是一行无法辨认的东西")
        if not unit.body:
            # 对面**不会**为空正文报错（它照建），所以放它过去就是一次成功的派发
            # 加上一行没人读得懂的看板条目 —— 而标题那一格已经检查过了，正文是
            # **另一件**事：标题给人扫，正文给人读。
            raise ValueError("unit.body 不能为空 —— 一个没有正文的任务在外部界面上是一行不知道要干什么的东西")
        # 这条 id **不是**本模块算的：它来自 ``marketops.application.dispatch``，
        # 与派发器写进任务正文那一节的路径是**同一个函数**算出来的。理由见那个
        # 函数的 docstring —— 简言之，正文里那个文件名与外面真建出来的这个 id
        # 一旦分家，两边各自都正常，而 ``## Touches`` 那条声明就变成了一句假话。
        task_id = external_task_id(unit.job_id, unit.unit_id)
        created = _task_from(
            self._payload(
                _create_argv(task_id, unit, labels=wanted, status=status, cli_root=self._root),
                what=f"建任务 {task_id}",
            )
        )
        if created.external_id != task_id:
            # 对面把我们给的 id 换掉了（规范化 / 截断 / 加前缀）。**必须现在抛**：
            # 本地那条映射要在下一步按 `task_id` 写下去，而它指向的东西不存在。
            raise DispatchError(
                f"要建的 id 是 {task_id!r}，对面建出来的是 {created.external_id!r}。"
                "对面改写了我们给的 id —— 再往下走就会写一条指向别处的映射。"
            )

        # 回读。**这不是保险，这是判据**：create 是 upsert（M5），所以「它说它建好了」
        # 与「按同一组 label 查得到它」是两句不同的话，而只有后一句是关联成立。
        found = self.find(labels=wanted)
        if len(found) != 1:
            raise DispatchError(
                f"任务 {task_id} 写下去了，但按 {list(wanted)!r} 查回来的是 {len(found)} 条："
                f"{[task.external_id for task in found]!r}。\n"
                "零条 ⇒ label 没落盘（那个任务从查询这边看等于不存在）；多条 ⇒ 这一组 "
                "label 下的任务不止一个（同一件事被派发了两遍）。两种都必须抛。"
            )
        (read_back,) = found
        if read_back.external_id != task_id:
            raise DispatchError(
                f"按 {list(wanted)!r} 查回来的是 {read_back.external_id!r}，不是刚建的 {task_id!r}。\n"
                "两个不同的单元派生出了同一个外部 id —— 也就是这一组 label 已经被"
                "另一个单元的任务占着了。"
            )
        missing_labels = [label for label in wanted if label not in read_back.labels]
        if missing_labels:
            raise DispatchError(
                f"任务 {task_id} 少了这些 label：{missing_labels!r}（它现在带的是 "
                f"{list(read_back.labels)!r}）。label 是这条边界上唯一的关联，"
                "少一个就意味着有一个方向走不回去。"
            )
        # 回**回读的那一份**，不回 create 那一份：回读发生在后，它才是当下的状态。
        # 两份的 ``status`` 不一致是一个正常的事实（外部界面上有人改过它），
        # 不需要报错 —— 但那时若返回 create 的那一份，调用方拿到的是一个**过去**的
        # 状态，而它会据此做下一个决定。
        return read_back

    def transition(self, external_id: str, *, expected: str, status: str) -> ExternalTask:
        if not expected or not status:
            raise ValueError("expected 与 status 都不能为空 —— 一次不写明两端的迁移不是一次 CAS")
        return _task_from(
            self._payload(
                _transition_argv(external_id, expected=expected, status=status, cli_root=self._root),
                what=f"把 {external_id} 从 {expected!r} 迁到 {status!r}",
            )
        )


def _task_from(payload: Any) -> ExternalTask:
    """把对面那份 JSON 收成一个 ``ExternalTask``。形状不对就抛，**不猜**。

    一个缺字段的 payload 被补上缺省之后，``labels`` 会变成空的那个 —— 而那与
    「这个任务没有 label」是同一件事，于是「关联丢了」会以「关联本来就没有」的
    样子通过。所以缺字段一律抛。
    """
    if not isinstance(payload, dict):
        raise DispatchError(f"外部回了一个不是对象的任务：{payload!r}")
    try:
        external_id = payload["id"]
        status = payload["status"]
        raw_labels = payload["labels"]
    except KeyError as absent:
        raise DispatchError(
            f"外部回的这份任务缺字段 {absent.args[0]!r}：{payload!r}。"
            "缺字段**不补缺省** —— 补出来的 labels 是空的那一组，而它读起来与"
            "「这个任务没有 label」一模一样。"
        ) from absent
    if not isinstance(external_id, str) or not isinstance(status, str):
        raise DispatchError(f"外部回的 id / status 不是字符串：{payload!r}")
    if not isinstance(raw_labels, list) or not all(isinstance(x, str) for x in raw_labels):
        raise DispatchError(f"外部回的 labels 不是一个字符串数组：{payload!r}")
    return ExternalTask(
        external_id=external_id, status=status, labels=tuple(raw_labels)
    )
