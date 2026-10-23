"""派发的**入站那一端**：把任务板上的一件活变成一次研究 worker 的启动。

    python -m marketops.interfaces.research_worker --work-item <id> --task <external id>

## 它接的是什么

一次派发在本地任务板上留下一个外部任务 id 与一组 ``marketops-item:`` label
（``adapters/local_board.py``）。本模块把「那一行」变成「一轮研究」：领一个研究员
身份、建一个本轮唯一的隔离运行目录、把研究 prompt 交给部署指定的 delegate，然后
把 delegate 的退出码原样交回去。

**参数是显式的两个 id，不是一串要猜的 argv。** 它曾经只能从某个外部驱动拼的
``-p`` / ``-n`` / ``--session-id`` 里反解身份（Issue #2 之后那一路已经移除）。
反过来读别人的 argv 有一个我们已经付过代价的性质：**同一串参数有两种读法**，
而对面多推一个旗标就会静默改变第一种读法的边界。显式参数没有这个问题，而
「这两个 id 指的是哪件工作」是可校验的（``check_slug_safe`` 加存在性）。

## 为什么 cwd 是隔离运行目录，而不是部署根

一轮 worker 会读文件、会写东西、会开工具。让它站在部署根上，它读得到管理面
配置、读得到兄弟任务的记录、也读得到上一轮留下的回执。所以本层在启动 delegate
**之前**新造一个本轮唯一的空目录，路径是

    <MARKETOPS_WORKER_ROOT>/<工作单元 id>/<任务 id>/<UTC 时刻>-<pid>/

写一份 ``run.json`` 进去，然后把**这个路径**作为 delegate 的 cwd 交给它
（``agent_runtime.run_delegate``）。于是子进程的相对路径从这个目录解析，一次误写
的落点是本轮的 scratch，而不是部署。

隔离目录按启动器真正持有的两个身份排，不是 ``attempt_id``：租约是 worker 第一次
``get_assignment`` 时才铸造的，启动这一刻它还不存在 —— 用一格猜出来的编号命名
目录，会让「哪一轮是哪一轮」在事后变成一次推断。

## 三条不在本层的边界

* **stdio 不捕获、不改道。** delegate 原样继承这两个流：运维要读的是它自己说的话。
* **不碰任务板、不碰 git。** 结算走市场侧的 ``submit_findings``；板面的收敛由
  ``application/dispatch.py`` 的 ``reconcile`` 做，不由这一层做。
* **工具面由部署的 delegate 参数拼出来，本层只核对它**（见
  :func:`_require_mcp_surface`）：除了 ``marketops`` 之外的 MCP server 一律拒绝，
  因为让子进程**看不到**那些工具比让它「被要求不要用」强 —— 后者的执行器是一个
  提示词。

## 为什么「本层加载的 marketops 是不是服务那一份」是一道闸（§15(b)）

这一层的记录里有一格 ``loaded_marketops``，写的是**本层**加载的那棵树；而真正
在干活的是 delegate，它通过 ``marketops`` MCP 工具去调**服务**，服务是一个别的
进程，它加载的可能是另一棵树。两边都不报错，于是这一轮的每一条发现都由一份与
服务不同的代码产生，而记录里写着的是本层那一份。所以本层**在启动 delegate 之前**
核对（``adapters/build_stamp.compare_builds``），不一致就退 2。

## 身份从环里来（§15(a)）

本层自己领身份，不让启动器 export 一份令牌进来：令牌的有效性只写在环里（盘上
只有摘要），而「这份令牌还作数吗」是一个只有环能回答的问题。``flock`` 由本进程
持有到 delegate 结束（``security/identity.py``），也就是「一个 worker 一个身份」
在整个这一轮里成立。

## 退出码

``0``/非 ``0`` 由 delegate 决定，本层照传（退出码即裁决）。本层自己的拒绝一律是
``EXIT_REFUSED``（2），并且**在启动 delegate 之前**发生 —— 所以一个 2 加上
stderr 里那句「未派发：…」读作「这一轮没有开始」，而任何别的非零读作
「delegate 跑过、结局是它给的」。
"""

from __future__ import annotations

import argparse
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
from marketops.adapters.server_build import read_server_build
from marketops.domain.errors import ConfigError
from marketops.domain.util import check_slug_safe
from marketops.security.identity import TOKEN_ENV, TOKEN_RING_ENV, Identity, claim_identity

__all__ = [
    "DATA_ROOT_ENV",
    "DELEGATE_ARGS_ENV",
    "DELEGATE_ENV",
    "EXIT_REFUSED",
    "REQUIRED_MCP_SERVERS",
    "RESEARCH_WORKER_PROMPT",
    "WORKER_ROOT_ENV",
    "BuildCheck",
    "build_receipt",
    "main",
    "research_prompt",
    "run_round",
]

#: 跑哪个 agent runtime 由**部署**决定，所以它从环境来。没有缺省值：一个缺省的
#: delegate 会让「忘了配」表现成「跑起来了但谁也没干活」——两者都非零，而运维读到
#: 的是完全不同的两句话。
DELEGATE_ENV = "MARKETOPS_RESEARCH_DELEGATE"

#: delegate 的**其余参数**，一个 JSON 数组（``["-p"]`` 不算，prompt 由本层追加）。
#:
#: 为什么是 JSON 数组而不是一串靠空格切的字符串：后者在带空格的路径上有一整类
#: 静默错法，而这里的每一个元素都会真的变成一次进程启动的参数。
DELEGATE_ARGS_ENV = "MARKETOPS_RESEARCH_DELEGATE_ARGS"

#: 本轮的隔离运行目录建在哪个根下面。由**部署**决定：只有部署侧知道
#: ``ARTIFACTS_ROOT`` 在哪，把它写死进本仓就等于让所有部署共用这一台机器的路径。
WORKER_ROOT_ENV = "MARKETOPS_WORKER_ROOT"

#: 这个部署的运行态根。本层读它两个东西：服务报下的那份构建
#: （``state/server-build.json``，§15(b) 的核对）与身份锁（``state/identities``）。
DATA_ROOT_ENV = "MARKETOPS_DATA_ROOT"

#: 本层拒绝时的退出码。与 ``interfaces/cli.py`` 同源：那边缺配置也是 2。
EXIT_REFUSED = 2

#: 子 agent 允许看见的 MCP server，**恰好**这些。多一个少一个都拒绝。
REQUIRED_MCP_SERVERS = ("marketops",)

#: delegate 收到的 prompt。**过程，不是内容** —— §2.14 H3a 逐字：「指引走
#: ``get_assignment``，**不**走任何外部任务的正文」。所以这里一个市场词都没有，
#: 也不含任何一条指引：它只说「去问谁、拿什么当证据、从哪里交、卡住了怎么报」。
RESEARCH_WORKER_PROMPT = """\
You are the MarketOps research worker for exactly one work item: {work_item_id}

The market side filed this work item and owns your guidance and the closing. Do not
edit anything in this workspace and do not change any task's status — a worker that
moves its own board entry makes the board and the ledger disagree while both look
healthy.

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
            f"{DELEGATE_ENV} 没有设 —— 本层不知道该把这一轮交给谁。部署侧负责 "
            "export 它（本机是 ``claude``）。"
        )
    return value


def _delegate_args() -> list[str]:
    """delegate 的其余参数，一个 JSON 数组。没给 = 空（但那时工具面核对会拒）。"""
    raw = os.environ.get(DELEGATE_ARGS_ENV, "").strip()
    if not raw:
        return []
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ConfigError(f"{DELEGATE_ARGS_ENV} 不是合法 JSON：{exc}") from exc
    if not isinstance(parsed, list) or not all(isinstance(x, str) for x in parsed):
        raise ConfigError(f"{DELEGATE_ARGS_ENV} 必须是一个字符串数组，收到 {parsed!r}")
    return list(parsed)


def _worker_root() -> Path:
    """隔离运行目录的根。没命名就拒绝，并说出来缺的是哪一个变量。

    **必须是绝对路径**：一个相对值会解析到调用方站的那棵树里 —— 那正是这一格要
    关掉的地方，而且它看起来完全成功：目录建出来了、记录写进去了、delegate 也在跑。
    """
    value = os.environ.get(WORKER_ROOT_ENV, "")
    if not value.strip():
        raise ConfigError(
            f"{WORKER_ROOT_ENV} 没有设 —— 本层不知道该把这一轮的隔离运行目录建在哪里。"
        )
    root = Path(value)
    if not root.is_absolute():
        raise ConfigError(
            f"{WORKER_ROOT_ENV} 是相对路径（{value!r}）—— 本层要的恰好是调用方树外面"
            "的一个绝对位置。"
        )
    return root


def _data_root() -> Path:
    """这个部署的运行态根 —— 本层有两个读者，所以它单独成一个函数。"""
    named = os.environ.get(DATA_ROOT_ENV, "")
    if not named.strip():
        raise ConfigError(
            f"{DATA_ROOT_ENV} 没有设 —— 本层没法核对「本层加载的 marketops」与"
            "「服务加载的 marketops」是不是同一份，也没法知道身份锁该落在哪里。"
        )
    return Path(named)


def _isolated_run_dir(
    worker_root: Path, *, task_id: str, work_item_id: str, now: datetime
) -> Path:
    """本轮那个目录：``<根>/<工作单元>/<任务>/<UTC 时刻>-<pid>/``。

    两个 id 过 ``check_slug_safe`` 并拒绝 ``.``/``..`` —— 它们直接当路径成分用。
    时刻用 UTC、带 ``Z``、带微秒：这一格会被人拿别的日志对时间，而本机同时存在两种
    时区的日志；微秒挡的是「同一个进程在同一微秒里被调两次」。
    """
    check_slug_safe(work_item_id, "work_item_id")
    check_slug_safe(task_id, "task_id")
    for name, value in (("work_item_id", work_item_id), ("task_id", task_id)):
        if value in {".", ".."}:
            raise ValueError(f"{name} 是 {value!r} —— 那是一个能上跳的路径成分，不是 id")
        if any(ord(ch) < 0x20 or ord(ch) == 0x7F for ch in value):
            # ``check_slug_safe`` 拦的是路径分隔符与保留字符，控制字符不在它那一
            # 张表里（那里校验的是「会被用作文件名」的 id，而不是「会被拿来拼目录的
            # 外部任务 id」）。这里必须自己拦：NUL 让 ``mkdir`` 抛的是 ``ValueError``
            # 而不是 ``OSError``，于是它会**穿过**下面那段 ``except OSError`` 变成一段
            # 抛栈 —— 而 runner 把它读成「这一轮崩溃了」，不是「这个 id 不能当路径」。
            raise ValueError(
                f"{name} 含控制字符 —— 它不能当路径成分，"
                "而且 NUL 会让建目录抛的不是 OSError"
            )
    stamp = now.astimezone(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    return worker_root / work_item_id / task_id / f"{stamp}-{os.getpid()}"


def _read_mcp_servers(args: Sequence[str]) -> tuple[str, ...]:
    """从 delegate 参数里的 ``--mcp-config`` 读出它会看见哪些 MCP server。

    读不出来（没有这个旗标、文件不在、JSON 读不懂）**都是拒绝**：这一层被授予的
    权力是「核对工具面」，而不是「假设部署已经核对过」。
    """
    try:
        index = list(args).index("--mcp-config")
    except ValueError as exc:
        raise ConfigError(
            f"delegate 参数里没有 ``--mcp-config``（{DELEGATE_ARGS_ENV}）—— 本层核对不了"
            "这一轮的工具面。一个不写明工具面的子 agent 会与宿主上一切可用来源合并，"
            "而多出来的工具是**在提示词之外**放宽的权限。"
        ) from exc
    if index + 1 >= len(args):
        raise ConfigError("``--mcp-config`` 后面没有路径")
    path = Path(args[index + 1])
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ConfigError(f"读不出 MCP 配置 {path}：{exc}") from exc
    servers = payload.get("mcpServers")
    if not isinstance(servers, dict):
        raise ConfigError(f"{path} 里没有 ``mcpServers`` 对象")
    return tuple(sorted(servers))


def _require_mcp_surface(args: Sequence[str]) -> tuple[str, ...]:
    """delegate 的参数必须恰好开出 ``REQUIRED_MCP_SERVERS``，并把它交回去。

    在**启动 delegate 之前**抛，所以一次工具面变宽的派发不是「跑起来再发现」。
    返回值是**核对过的那一份**，写进 ``run.json`` 的那一格与刚才放行的依据是同一个
    读数 —— 分成两次读，某一天会出现「放行的是 A、记下来的是 B」。
    """
    servers = _read_mcp_servers(args)
    if tuple(servers) != tuple(sorted(REQUIRED_MCP_SERVERS)):
        extra = [name for name in servers if name not in REQUIRED_MCP_SERVERS]
        missing = [name for name in REQUIRED_MCP_SERVERS if name not in servers]
        raise ConfigError(
            f"这次派发给子 agent 开出的 MCP server 是 {list(servers)}，而本层只允许"
            f"{list(REQUIRED_MCP_SERVERS)}（多出 {extra}、缺 {missing}）。"
            "多出来的工具面是**在提示词之外**放宽的权限，所以这里停下。"
        )
    return servers


@dataclass(frozen=True)
class BuildCheck:
    """§15(b) 那一次核对**通过之后**的读数：结论、服务报下的那一份、以及它是谁写的。

    **只有 ``agrees`` 的那条路能构造出它**，所以 ``stamp`` 与 ``path`` 都不是可选值
    —— 一个「可能读不出来」的服务侧指纹会让回执里出现一条**到不了**的分支。
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
        """给终端看的那一行：**报出「核对过」，以及核对的是哪一个进程**。"""
        loaded = f"{self.stamp.read_at} 加载"
        if self.stamp.dirty:
            loaded += "，有未提交改动"
        return (
            f"构建核对：与服务的构建一致（服务 pid {self.pid}，{loaded}；"
            f"{self.stamp.source_tree}）"
        )


def _claim_identity() -> Identity:
    """这一轮用哪个研究员身份 —— 从**服务会判的那一份环**里取（§15(a)）。"""
    named = os.environ.get(TOKEN_RING_ENV, "")
    if not named.strip():
        raise ConfigError(
            f"{TOKEN_RING_ENV} 没有设 —— 本层不知道该从哪一份环里取研究员身份。"
            "这一个变量必须与服务读的**同一个**：环是令牌有效性的唯一来源。"
        )
    return claim_identity(Path(named), lock_root=_data_root() / "state" / "identities")


def _require_build_consistency() -> BuildCheck:
    """本层加载的这份代码，与服务加载的那份，是不是同一份（§15(b)）。"""
    named = os.environ.get(DATA_ROOT_ENV, "")
    reading = read_server_build(_data_root())
    if reading.stamp is None:
        raise ConfigError(f"核不了构建（{DATA_ROOT_ENV}={named}）：{reading.problem}")
    verdict = compare_builds(LOADED_BUILD, reading.stamp)
    if not verdict.agrees:
        raise ConfigError(verdict.message)
    return BuildCheck(verdict, reading.path, reading.stamp, reading.pid)


def _write_run_record(
    run_dir: Path,
    *,
    task_id: str,
    work_item_id: str,
    delegate: str,
    servers: Sequence[str],
    build: BuildCheck,
    identity: dict[str, object],
    now: datetime,
) -> None:
    """把「这一轮是什么」写进隔离目录里的 ``run.json`` —— 本层**唯一**的产物。"""
    record = {
        "task_id": task_id,
        "work_item_id": work_item_id,
        "delegate": delegate,
        "started_at": now.astimezone(UTC).isoformat(),
        "cwd": str(run_dir),
        "loaded_marketops": {
            "source_tree": LOADED_BUILD.source_tree,
            "revision": LOADED_BUILD.revision,
            "dirty": LOADED_BUILD.dirty,
            "errors": dict(LOADED_BUILD.errors),
        },
        "build_consistency": build.payload(),
        "identity": identity,
        "mcp": {"source": "--mcp-config（由部署给出，本层核对）", "strict": True,
                "servers": list(servers)},
        "why_no_attempt_id": (
            "租约（attempt）由 worker 第一次 get_assignment 铸造，启动这一刻还不存在；"
            "所以目录按真正持有的两个身份命名，而不是按一个猜出来的编号。"
        ),
    }
    (run_dir / "run.json").write_text(
        json.dumps(record, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def run_round(work_item_id: str, *, task_id: str) -> int:
    """跑一轮。返回值就是进程退出码。**这是本模块唯一被复用的入口**。

    ``dispatch_runner``（本机任务板那次循环）与 ``main``（一次显式调用）都走这里：
    两处各写一遍「核对 → 落位 → 递出去」的话，漂移的那一天会出现「runner 起的那一轮
    不核对构建」而这种形状在报告上完全看不出来。

    三段，顺序是承重的：**核对**（这一段一个字节都不写，任一条不过就退 2 且 delegate
    没被启动）→ **落位**（建本轮的隔离目录、写记录）→ **递出去**。
    """
    try:
        delegate = _delegate_program()
        delegate_args = _delegate_args()
        servers = _require_mcp_surface(delegate_args)
        build = _require_build_consistency()
        now = datetime.now(UTC)
        run_dir = _isolated_run_dir(
            _worker_root(), task_id=task_id, work_item_id=work_item_id, now=now
        )
        command = [delegate, *delegate_args, "-p", research_prompt(work_item_id)]
        # 领身份放在**这一段的最末尾**：它是这里唯一一件拿到手却要活到函数出口的
        # 东西，放最后就不必给前面那一串拒绝各写一次放锁。
        stale_token = bool(os.environ.get(TOKEN_ENV, "").strip())
        prior_token = os.environ.get(TOKEN_ENV)
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
                task_id=task_id,
                work_item_id=work_item_id,
                delegate=delegate,
                servers=servers,
                build=build,
                identity=identity.payload() | {"launcher_token_replaced": stale_token},
                now=now,
            )
        except (OSError, ValueError) as exc:
            # ``ValueError`` 不是多余的：一个含 NUL 的 id 会让 ``mkdir`` 抛它而不是
            # ``OSError``。上面那条检查拦住了今天已知的那一种，这里兜的是「路径成分
            # 还有别的形状让 CPython 抛 ValueError」—— 那仍然是一个不能用的目录，
            # 不是一次崩溃。
            print(f"未派发：隔离运行目录 {run_dir} 用不了：{exc}", file=sys.stderr)
            return EXIT_REFUSED

        print(
            f"marketops research worker：任务 {task_id} → 工作单元 {work_item_id}"
            f"（delegate：{delegate}）\n"
            f"{build_receipt()}\n"
            f"{build.render()}\n"
            f"研究员身份：{identity.principal}（锁 {identity.lock_path} 由本进程持有到"
            f"本轮结束；令牌取自 {identity.token_file}，在环里解析得到 —— 不看文件名）\n"
            f"隔离运行目录：{run_dir}（交给 delegate 的 cwd 是这个）",
            file=sys.stderr,
        )
        try:
            return run_delegate(command, cwd=run_dir)
        except OSError as exc:
            print(f"未派发：delegate 起不来（{delegate}）：{exc}", file=sys.stderr)
            return EXIT_REFUSED
    finally:
        # 身份锁在本轮结束时放掉，**显式关，不靠进程退出**：``lock_fd`` 是一个整数，
        # 出作用域、被 GC 掉都不会关掉它 —— 于是同一个进程里的第二次派发会撞上自己
        # 的上一轮。进程被 kill 时内核照样放。
        os.close(identity.lock_fd)
        # 令牌也放回去。同一条理由的另一个形状：这一行是第一轮写进环境的，而
        # ``dispatch_runner`` 在**同一个进程**里连跑多轮 —— 不还原的话，第二轮看到的
        # 非空 ``TOKEN_ENV`` 会让它把 `launcher_token_replaced` 记成 true（那是部署
        # 顺序问题的信号，不是本层自己留下的痕迹），而且那份令牌会一直留在这个进程的
        # 环境里，任何之后起的子进程都会静默继承它。
        if prior_token is None:
            os.environ.pop(TOKEN_ENV, None)
        else:
            os.environ[TOKEN_ENV] = prior_token


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--work-item", required=True, help="已编译的工作单元 id")
    parser.add_argument("--task", required=True, help="任务板上那个外部任务 id")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """一次显式调用。参数与 ``--work-item`` / ``--task`` 一一对应，不做前缀猜读。"""
    args = _parser().parse_args(list(sys.argv[1:] if argv is None else argv))
    return run_round(args.work_item, task_id=args.task)


if __name__ == "__main__":  # pragma: no cover
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("已中断。", file=sys.stderr)
        raise SystemExit(130) from None
