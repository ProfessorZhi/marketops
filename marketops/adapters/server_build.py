"""服务把自己加载的构建**报在数据根里**，worker 在派发之前读它（§15(b)）。

## 它答的是哪一问

§15(b) 逐字：「at worker startup, verify the worker's loaded MarketOps build is
compatible with the server build; mismatch ⇒ **fail closed** rather than continuing
research.」

两个进程各自从一个检出里 import 了一份 ``marketops``：worker 那一份由启动器的
``PYTHONPATH`` 决定，服务那一份由服务自己是怎么起的决定。两边**都不报错**，
而这一轮的 ``run.json`` 会写着「加载的 marketops：<worker 那棵树>」—— 一份关于
服务的、服务并不在跑的主张。现场见 ``docs/PRODUCT_LOOP_ACCEPTANCE_2026-09-24.md``
§G3：worker 的 ``PYTHONPATH`` 与服务器差 47 个提交。

判据本身不在这里，在 :func:`marketops.adapters.build_stamp.compare_builds`：这一层
只做两件事 —— **让服务把它那一份写到盘上**（:func:`publish_server_build`），
**让 worker 读回来**（:func:`read_server_build`）。

## 为什么是文件，不是一次网络往返

worker 当然可以拿 ``--mcp-config`` 里的 URL 与 ``MARKETOPS_MCP_TOKEN`` 去问服务
（``get_capabilities`` 的 ``build`` 那一格就是答案）。那会更准，但它会让 worker 的
启动依赖一次 MCP 会话：握手、会话 id、超时、以及一个「服务没起来时算不算不一致」
的问题 —— 而这个问题与本节要答的那一问不是同一件。

文件把这个问题留在原地：**服务在开始服务之前先报一次自己的构建**，所以「文件在
不在」与「服务报没报过」是同一个事实。file 的另一半代价是**陈旧**（服务被杀、文件
还在），这一层把它如实报出来（``read_at`` 与 ``pid`` 都在文件里），**不判**它 ——
「服务还活着没有」有自己的产物，把两件事合成一句会让「构建不一致」与「服务没起来」
在报告里长得一样。

## 为什么落在 ``state/`` 下

数据根是这台机器上**这一个部署**的运行态根（``MARKETOPS_DATA_ROOT``），服务的状态库
就在 ``state/`` 里 —— 也就是说，service 与 worker 已经**必须**对这一个根达成一致，
否则 worker 读到的是一批与它调用的服务无关的东西。把构建写进同一个根，是在用一条
已经承重的耦合，而不是新加一条。

## 它不答什么

* 不答「这个文件是哪一次服务启动写的」。文件里没有时间之外的排序依据，而时间来自
  写它的那个进程（``read_at``）。要判先后，去看 ``pid`` 还活着没有 —— 那是 `ps` 的活。
* 不答「服务现在接不接受连接」。这一层只读盘，一个字节都不发。
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path

from .build_stamp import LOADED_BUILD, BuildStamp

__all__ = [
    "SERVER_BUILD_FILENAME",
    "ServerBuildReading",
    "publish_server_build",
    "read_server_build",
    "server_build_path",
]

#: 文件名。**带 ``server-`` 前缀**：``state/`` 里这个文件要能与别的运行时文件一眼分开
#: —— 运维在一个目录里找「服务报的是哪一版」时，名字就是唯一的路标。
SERVER_BUILD_FILENAME = "server-build.json"


def server_build_path(data_root: Path) -> Path:
    """这个数据根上，服务报构建的那份文件在哪。

    单独成函数（而不是让两处各拼一次 ``data_root / "state" / "server-build.json"``）：
    写它的那一侧（``interfaces/mcp``）与读它的那一侧（``interfaces/research_worker``）
    是两个进程，路径拼错的症状是「worker 说这个部署上还没有服务报过构建」—— 一句
    看起来像部署事实、实际是拼写差异的话。
    """
    return data_root / "state" / SERVER_BUILD_FILENAME


def publish_server_build(data_root: Path, *, stamp: BuildStamp = LOADED_BUILD) -> Path:
    """服务在**开始服务之前**把自己加载的构建写下去。返回写成的路径。

    文件内容 = :meth:`BuildStamp.payload` 那六个键，**外加 ``pid``**。加它是因为
    读的人（运维、以及 worker 的回执）要能把这一份与 ``ps`` 里的某一行对上 ——
    一个不说自己是谁写的回执，在「服务重起过没有」这个问题上帮不上忙。

    写法是**先写临时文件再 ``os.replace``**：读的那一侧可能在服务正在写的那一刻
    打开它，而一份被读了一半的 JSON 会以「这个文件是坏的」的形状出现 —— 那是
    **误导**，因为这个文件没有坏，只是有人在写。``os.replace`` 在同一文件系统上
    是原子的，所以读的人要么看到上一份完整的，要么看到这一份完整的。

    Raises:
        OSError: 写不出来。**服务这里是失败关闭的**（调用点退 2）：写不出来意味着
            每一个 worker 都会以「这个部署上还没有服务报过构建」停下，而那句话
            会把一个磁盘问题说成一个部署事实。
    """
    target = server_build_path(data_root)
    target.parent.mkdir(parents=True, exist_ok=True)
    body = {**stamp.payload(), "pid": os.getpid()}
    # 临时文件名带 pid：同一台机器上两个服务实例（不同数据根时才会发生）不会撞名，
    # 而同一个进程连写两次时后一次覆盖前一次的临时文件 —— 两次都不会被读到半截。
    tmp = target.with_name(f"{target.name}.{os.getpid()}.tmp")
    try:
        tmp.write_text(
            json.dumps(body, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        os.replace(tmp, target)
    except OSError:
        # 清理**不再抛**：这里已经在处理一个失败，而清不掉临时文件（目录不存在、
        # 目录在写失败的同时被撤了权限）会让那句「为什么写不出来」被一个关于临时文件
        # 的错盖掉 —— 读那一行的人要修的是**原来那件事**。
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass
        raise
    return target


@dataclass(frozen=True)
class ServerBuildReading:
    """读一份「服务报的构建」的结果。

    ``stamp`` 为 ``None`` 表示**没读到**，理由在 ``problem`` 里。与 ``BuildStamp``
    本身一样，这里三值而不是布尔：「文件不在」与「文件在、但读不出来」是两件处置
    不同的事（前者问服务起没起过，后者问这个文件怎么了），而合成一句之后，一句
    「服务没报过构建」会把一个磁盘问题说成一个部署事实。

    ``pid`` 是写那一份文件的服务进程，读得出来时给出来（回执里用的是它）。
    """

    path: Path
    stamp: BuildStamp | None
    pid: int | None
    problem: str

    @property
    def readable(self) -> bool:
        return self.stamp is not None


def read_server_build(data_root: Path) -> ServerBuildReading:
    """读服务报下的那一份构建。**不抛**：读不出来是一个结论，理由回来。

    不抛的理由与 ``read_build_stamp`` 相同：这是一个诊断面，而一个抛出去的诊断
    只会让调用方选择打堆栈或吞掉 —— 两样都不是它被写出来的用途。
    """
    path = server_build_path(data_root)
    if not path.exists():
        return ServerBuildReading(
            path,
            None,
            None,
            f"{path} 不在 —— 这个数据根上还没有服务报过自己的构建。\n"
            "服务在开始服务之前会把它写在这里（`interfaces/mcp` 的 `_serve`）。"
            "文件不在有两种情形：服务没起来过，或者它起的时候用的是**另一个**数据根 ——"
            "后者的症状是两侧读写的 runtime 状态根本不是一个。",
        )
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as exc:
        return ServerBuildReading(
            path, None, None, f"读不了 {path}：{type(exc).__name__}: {exc}"
        )
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        return ServerBuildReading(
            path, None, None, f"{path} 不是一份 JSON：{type(exc).__name__}: {exc}"
        )
    if not isinstance(payload, dict):
        return ServerBuildReading(
            path, None, None, f"{path} 里不是一个对象，是 {type(payload).__name__}"
        )
    pid = payload.pop("pid", None)
    if not isinstance(pid, int) or isinstance(pid, bool):
        pid = None
    try:
        stamp = BuildStamp.from_payload(payload)
    except ValueError as exc:
        return ServerBuildReading(path, None, None, f"{path} 不是一份构建指纹：{exc}")
    return ServerBuildReading(path, stamp, pid, "")
