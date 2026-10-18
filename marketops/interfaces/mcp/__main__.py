"""服务面的入口：``python -m marketops.interfaces.mcp [--check]``。

## 两道闸在起监听套接字之前

* **地址必须是字面量的环回地址。** 这台机器上默认绑 ``0.0.0.0`` 的东西不止一个
  （记忆 ``CloudCLI 默认绑 0.0.0.0 且首启未认证`` 是同一个形态）。这个服务面带着
  一条能读证据、能提交发现、将来能花钱的接口，绑到所有网卡上等于让同网段任何
  一台机器都拿到它。所以判据不是「记得绑 127.0.0.1」，而是**非环回一律起不来**。

  名字（``localhost``）也拒绝，理由与抓取层那条**相反**：那边 ``localhost`` 是
  环回**目标**（因此要拒绝抓取），这边它是**不确定的**绑定地址 —— 绑定发生在
  解析之后，而 ``/etc/hosts`` 与 DNS 是别人的东西。

* **令牌环缺失不是「开放」，是「谁都不许」。** 所以 ``--check`` 在无环时返回
  非零：一个能起来但谁都不许的服务，部署方必须在起之前就看见，而不是由第一个
  调用方发现。

## 配置只有一个来源

没有 ``--host`` / ``--port`` / ``--tokens`` 这些开关。所有取值来自环境变量，
经 ``config.load_settings`` 一处解析。多一个命令行开关就多一个能与之冲突的
来源，而冲突的那一次会表现为「检查的是 A，绑的是 B」——``--check`` 存在的全部
意义就是它跑的是**同一份**配置、**同一个**判据。

同一句话对**装配**也成立，而且是靠结构做到的：``--check`` 与 ``_serve()`` 都调
``_compose()``，两者唯一的差别是最后一步跑不跑 ``server.run(...)``。这条不是
风格问题 —— 自检曾经**只**做三件事（读配置、解析地址、装令牌环），完全不碰决策
后端与外部执行器的构造，于是一个 ``MARKETOPS_QUAY_*`` 配了一半的部署能拿到
「自检不通过：没有配置令牌环（退 1）」，然后在真正启动时退 2。同一个部署，两句
互相矛盾的话，而先跑到的、也是唯一会被贴进工单的，是自检那句。

## 状态库也在这道自检里（§15(c)）

``ensure_schema`` 的拒绝长在**写路径**上，而一个部署在那一刻之前什么都看不见：凡是
读写该库的工具**全部**失败，失败长成的样子是调用方上下文里一句没有 message 的
``Error executing tool``。那读起来像「工具坏了」，而事实是「这个库还没迁移」——
两者的处置不同（去查代码 vs 跑一条命令），而当时唯一分得出它们的东西是一句没有正文
的错误（``docs/PRODUCT_LOOP_ACCEPTANCE_2026-09-24.md`` §G2 是现场）。

所以 ``_compose`` 里多了一次**只读**的探问（``adapters/sqlite/readiness.py``），
``--check`` 与 ``_serve()`` 读的是**同一份**读数，而那句拒绝的原文只有一处
（``db.read_schema_state``）—— 两处各拼一遍，就会出现「自检说要跑 A 那条命令、
真正启动时说要跑 B」。它排在 ``settings.ensure_data_dirs()`` **之前**：那个调用会在
``database_path.parent`` 上跑 ``mkdir(parents=True, exist_ok=True)``，而一个会把目录树
建出来的自检，会在「目录还没准备好」这种真故障面前先把故障修掉一半
（``tests/test_mcp_entry.py`` 钉着当年那条断言）。

## 服务也报自己的构建（§15(b)）

``_serve()`` 在**开始服务之前**把自己加载的构建写到数据根里
（``state/server-build.json``，``adapters/server_build.py``）。那不是在给自己写日志：
研究 worker 在派发之前读它，用来核对「worker 加载的那份 ``marketops``」与「服务加载的
那一份」是不是同一份代码；对不上就**不派发**（退 2）。判据与措辞在
``adapters/build_stamp.compare_builds``。

写在 ``run()`` 之前是承重的：「文件在」这件事的含义就是「服务报过一次自己的构建」，
而反过来写会有一个窗口，其间服务已经接得住调用、worker 却因为读不到文件停下。
写不出来时本层退 2 —— 那时每一轮派发都会以「这个部署上还没有服务报过构建」停下，
而那句话说的是部署，不是这里的磁盘。

## 退出码

三个码回答的是**同一个问题** —— 这个进程会不会起来、起来之后有没有人调得动。
「原因属于哪一类」是消息里的事，不是码里的事：两个部署可以因为完全不同的原因落到
同一个码上，而调用方要问的那一问只有一个。

* ``0`` —— 自检通过：起得来，而且至少有一个主体调得动。
* ``1`` —— **起来，但谁都不许**：判据在凭据（无环 / 坏环）。服务照常绑定端口，
  只是没有任何工具可调。
* ``2`` —— **起不来**：地址、端口、数据根这类启动参数，一个**配错了**的后端或执行器，
  或者一个**这一版代码打不开的状态库**（版本对不上 / 有行却没有工作区绑定 / 不是一个
  数据库文件）。三者的处置各不相同，而它们对调用方的影响是同一种 —— 这个部署不服务。
"""

from __future__ import annotations

import argparse
import ipaddress
import json
import sys
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from functools import partial
from typing import TYPE_CHECKING, Any

from ... import RESEARCH_ONLY, SEND_AUTHORIZED
from ...adapters.build_stamp import LOADED_BUILD
from ...adapters.mcp_server import (
    build_server,
    loopback_transport_security,
    resource_identifier,
    tool_backing,
)
from ...adapters.mcp_server.tools import Handler
from ...adapters.profile_loader import load_profile
from ...adapters.server_build import publish_server_build, server_build_path
from ...adapters.sqlite.readiness import DatabaseReadiness, read_database_readiness
from ...application.budget import declared_document_ceiling, declared_search_ceiling
from ...config import Settings, load_settings
from ...domain.errors import AuthorizationError, ConfigError, MarketOpsError
from ...ports.decisions import DecisionProvider, DecisionRecordStore
from ...ports.jobs import JobStore
from ...security.catalog import TOOL_CATALOG
from ...security.tokens import TOKEN_RING_ENV, TokenRing, same_resource
from ...security.workspace import require_workspace

if TYPE_CHECKING:
    # 从**适配器**那里取这个名字，而不是直接 import 厂商包：后者会让
    # ``tests/test_adapters_hold_the_vendors.py`` 把本文件报成违规（厂商模块只能
    # 从 ``adapters/`` 里 import），而那条判据在本文件里没有例外，也不该有。
    # 这里只是取一个类型，取的是「谁来实现这个端口」，不是「我们要跟谁说话」。
    from ...adapters.mcp_server.server import MCPServer, ToolBacking

__all__ = [
    "PREFLIGHT_BLOCKED_CODE",
    "PREFLIGHT_BLOCKED_CONFIG",
    "PREFLIGHT_BLOCKED_EXTERNAL",
    "PREFLIGHT_READY",
    "PreflightReport",
    "PreflightRow",
    "main",
    "resolve_bind_host",
    "trial_preflight",
    "wire_decisions",
    "wire_research",
]

#: 退出码。见模块 docstring。
_EXIT_OK = 0
_EXIT_NOT_READY = 1
_EXIT_CANNOT_START = 2

_HOST_VARIABLE = "MARKETOPS_MCP_HOST"
#: 环在哪 —— 与 ``config.py``、``security/identity.py`` 同一个声明（见那里为什么
#: 不能各写一遍）。这一层只把它用在「起不来时点名」的那句话里。
_TOKENS_VARIABLE = TOKEN_RING_ENV


def resolve_bind_host(host: str) -> str:
    """校验并返回要绑定的地址。

    判据是**先解析成** ``ipaddress`` 对象、再看它是不是环回 —— 不是
    ``startswith("127.")``。后者会放过 ``127.0.0.1.example.com``：一个可以把
    A 记录指向任何地方的公网名字，而它看起来像环回地址。

    为什么要字面量：绑定发生在名字解析之后，而解析由 ``/etc/hosts``、DNS 与
    搜索域决定 —— 那些都不在这个仓库里，也不在任何一次评审里。一个能通过解析
    变成内网地址的名字，会让这条判据在部署机器上变成一句空话。

    Raises:
        ConfigError: 地址不是字面量 IP，或不是环回。
    """
    text = (host or "").strip()
    try:
        address = ipaddress.ip_address(text)
    except ValueError as exc:
        raise ConfigError(
            f"{_HOST_VARIABLE} 必须是一个**字面量** IP 地址，实际拿到 {host!r}。\n"
            "名字（localhost、example.com）也被拒绝：绑定发生在解析之后，"
            "而解析由 /etc/hosts 与 DNS 决定 —— 那些都不在评审范围里。"
        ) from exc
    if not address.is_loopback:
        raise ConfigError(
            f"{_HOST_VARIABLE} 只允许环回地址（127.0.0.0/8 或 ::1），实际拿到 {text!r}。\n"
            "本服务面带着一条能读证据、能提交发现的接口；把它绑到非环回地址上，"
            "等于让同网段的任何一台机器都拿到这条接口。"
            "确实需要对外开放时，请在前面放一个带认证的反向代理，而不是改这个变量。"
        )
    return text


def _payload(
    settings: Settings | None,
    *,
    host: str | None,
    ok: bool,
    blocker: str = "",
    database: DatabaseReadiness | None = None,
) -> dict[str, Any]:
    """自检输出。

    形状固定：即使配置装载失败也保持同一组键，只把取值降级为「无从得知」。
    一个会变形的输出会让解析它的脚本在**故障路径**上崩掉，而故障路径正是
    它被写出来的理由。

    刻意**不含令牌环路径**。这个输出会被贴进工单、日志与会话记录；路径本身
    不是凭据，但把它印出来会让「凭据在哪里」随输出扩散，而这条事实的价值只
    对不该看到它的人成立。

    ``build`` 与 ``get_capabilities`` 的那一格是**同一个对象**（``LOADED_BUILD``）：
    两个表面对同一个进程各报一份指纹，只会让其中一份是错的，而两份长得一样。

    ``resource`` 与 ``audiences`` 两格是**受众错配的唯一诊断面**。传输层把
    「令牌不对」与「受众不是这个服务」翻成同一个 401（``auth.py`` 的 docstring
    说了为什么不能在那条响应里区分），所以错配要靠这两格：一个是本进程认定的
    资源，一个是环里实际写着的受众。两边不一致时，这里是唯一不需要猜的地方。
    ``audiences`` 只在环装载成功时有值 —— 装载失败时留空数组而不是留一个猜测。
    ``resource`` 为 ``null`` 同理：那是「算不出来」，而**不是**「这个部署没有资源
    标识」—— 每个部署都有，只是配置装载失败或 ``MARKETOPS_MCP_RESOURCE`` 写坏了
    的时候，这个输出不能再抛一次：它自己的契约就是「形状固定，故障路径上也一样」，
    而一次未捕获的 ``ConfigError`` 会让故障路径变成一堆堆栈。写坏的那个值会在
    ``blocker`` 里被说出来。

    ``workspace`` 与 ``workspaces`` 是**工作区错配的唯一诊断面**，与上面那两格
    成对：``resource`` 答「发给哪个服务」，``workspace`` 答「本进程服务哪个
    Scene」；``audiences`` 与 ``workspaces`` 是环里实际写着的那些。调用面上那条
    拒绝**刻意不报出**本部署的 Scene（一条不属于这里的令牌不该从拒绝里学到这里的
    名字），所以「这条令牌准动哪里」只能从这里读。

    ``database`` 是**状态库那一道闸**，与 ``blocker`` 分成两格而不是合成一句：
    ``blocker`` 答的是「为什么谁都不许」（凭据），这一格答的是「这个库怎么了」
    （它的判据与措辞住在 ``db.read_schema_state``，这一层只是把它搬过来）。两格
    不合并的理由是它们可以**同时**成立 —— 合成一句之后，一个既没有环、库又没迁移
    的部署只会看见其中一条，而两条都要处置。``database`` 为 ``null`` 表示「没问到」
    （配置装载失败，或装配在那之前就抛了），**不是**「这个库没问题」。
    """
    return {
        "ok": ok,
        "build": LOADED_BUILD.payload(),
        "database": database.payload() if database is not None else None,
        "mcp": {
            "host": host if host is not None else (settings.mcp_host if settings else None),
            "port": settings.mcp_port if settings else None,
            "auth_configured": settings.mcp_auth_configured if settings else False,
            # 未设置 MARKETOPS_MCP_RESOURCE 时是从绑定地址推导出来的，而这里报的就是
            # **推导之后**的那个值：报「未设置」会让读的人自己去推，而推导规则在
            # server.resource_identifier 里，不该有两个实现。
            "resource": _resource_or_none(settings),
            # 本部署的工作区就是它的 Scene（``settings.profile``）。与 ``resource``
            # 同一取向：报**生效的那一个**，而不是「配置里写没写」—— 缺省值也是值，
            # 而这里问的是「这个进程实际服务哪个 Scene」。
            "workspace": settings.profile if settings else None,
            "audiences": [],
            "workspaces": [],
            "principals": [],
            "roles": [],
        },
        "blocker": blocker,
        "settings": settings.as_public_dict() if settings else None,
    }


def _resource_or_none(settings: Settings | None) -> str | None:
    """本进程认定的资源标识，算不出来时返回 ``None``。

    单独成函数是为了让 ``_payload`` 在**故障路径**上不可能抛：那个函数的存在
    理由就是「形状固定」，而它会在一处 ``except ConfigError`` 里被再次调用 ——
    那时抛出来的异常没有任何人接着，故障路径于是变成一堆堆栈，
    正是它要避免的东西。
    """
    if settings is None:
        return None
    try:
        return resource_identifier(settings)
    except ConfigError:
        return None


def _redacted(settings: Settings, text: str) -> str:
    """把令牌环的路径从一条消息里抹掉。

    ``TokenRing.from_file`` 的消息里带路径 —— 那对排障有用，但``--check`` 的输出
    会离开这个终端。抹掉的是路径，不是**原因**：装载失败仍然说得出它失败在
    哪一类（缺文件 / 不是 JSON / 版本不符 / 角色名不认识）。
    """
    path = settings.mcp_tokens_path
    if path is None:
        return text
    out = text
    for needle in (str(path), str(path.parent), path.name):
        if needle:
            out = out.replace(needle, "<令牌环>")
    return out


def _load_ring(settings: Settings) -> tuple[TokenRing | None, str]:
    """装载令牌环，返回 ``(环, 阻塞原因)``。

    不抛：这一层要把失败**讲出来**，而异常会让调用点只能选择打堆栈或吞掉。
    """
    if settings.mcp_tokens_path is None:
        return None, (
            f"没有配置令牌环（{_TOKENS_VARIABLE} 未设置）。服务面会正常启动，"
            "但**谁都不许**：没有主体就没有任何工具可调。"
        )
    try:
        return TokenRing.from_file(settings.mcp_tokens_path), ""
    except ConfigError as exc:
        return None, f"令牌环装载失败：{_redacted(settings, str(exc))}"


def _check() -> int:
    try:
        settings = load_settings()
    except ConfigError as exc:
        # 连配置都读不出来：起始参数这一类错误，进程起不来。
        print(json.dumps(_payload(None, host=None, ok=False, blocker=str(exc)), ensure_ascii=False))
        return _EXIT_CANNOT_START

    try:
        composed = _compose(settings)
    except ConfigError as exc:
        print(
            json.dumps(
                _payload(settings, host=settings.mcp_host, ok=False, blocker=str(exc)),
                ensure_ascii=False,
            )
        )
        return _EXIT_CANNOT_START

    payload = _payload(
        settings,
        host=composed.host,
        ok=composed.ring is not None and composed.database.startable,
        blocker=composed.blocker,
        database=composed.database,
    )
    if composed.ring is not None:
        payload["mcp"]["principals"] = list(composed.ring.principal_names)
        payload["mcp"]["roles"] = list(composed.ring.roles_present)
        payload["mcp"]["audiences"] = list(composed.ring.audiences)
        payload["mcp"]["workspaces"] = list(composed.ring.workspaces)
    print(json.dumps(payload, ensure_ascii=False))
    # 库先判：一个起不来的部署要拿到 2，哪怕它同时也「谁都不许」—— 两个问题里
    # 只有一个是调用方能感知到的（谁都调不动），而另一个要人动手才能解。
    if not composed.database.startable:
        return _EXIT_CANNOT_START
    return _EXIT_OK if composed.ring is not None else _EXIT_NOT_READY


# ----------------------------------------------------------------------
# 试运行自检（§26）
#
# 与上面 `_check()` 的差别是**问题不同，不是详略不同**。`--check` 问的是
# 「这个进程起不起来」——它有一格布尔与三个退出码。这一份问的是「能不能拿它开
# 一次真实的试运行」，而那个问题的答案不是布尔的：它要说得出**下一步该动什么**。
#
# §7.2 逐字：「一个**不执行真实付费请求**的 trial-preflight 命令/用例，能检查
# endpoint、auth、provider配置、预算是否 ready」。所以这一条路径上**没有任何
# 一次网络调用、没有任何一次计费调用**：它只读配置、读盘上的令牌环、读状态库的
# 一行版本记录、以及一次 `_compose`。最后那一样是安全的，理由与 `--check` 用
# 它是同一条 —— `_compose` 的契约就是「构造服务面需要的一切，**不绑端口、不建
# 运行目录**」。
# ----------------------------------------------------------------------

#: 判词。四值而不是布尔，因为**下一步要动的东西不是同一个**：``BLOCKED_CODE``
#: 要接线，``BLOCKED_CONFIG`` 要改这个部署的配置，``BLOCKED_EXTERNAL`` 要别人
#: 给访问权。一个只有「就绪 / 不就绪」的自检会把三件事压成同一句话，而那三句话
#: 的收件人不是同一个人。
PREFLIGHT_READY = "READY"
PREFLIGHT_BLOCKED_CONFIG = "BLOCKED_CONFIG"
PREFLIGHT_BLOCKED_EXTERNAL = "BLOCKED_EXTERNAL"
PREFLIGHT_BLOCKED_CODE = "BLOCKED_CODE"

#: 严重度。判词取**最重的那一行**，不是第一行、也不是最后一行的。
#:
#: 这个次序不是这里挑的，它是 §26 那句前提的推论：「``BLOCKED_EXTERNAL`` 的前提
#: 是代码侧已经全部做完」。所以同一份报告里既缺接线、又缺凭据时，判词必须是
#: ``BLOCKED_CODE`` —— 先修代码。配置排在外部之前同理：一个地址写错了的部署，
#: 它缺哪把钥匙这件事还问不出来（问出来也是白问，那一步先失败）。
_PREFLIGHT_SEVERITY: Mapping[str, int] = {
    PREFLIGHT_READY: 0,
    PREFLIGHT_BLOCKED_EXTERNAL: 1,
    PREFLIGHT_BLOCKED_CONFIG: 2,
    PREFLIGHT_BLOCKED_CODE: 3,
}

#: 判词各自的意思与下一步。**写在这里而不是散在渲染里**，是为了让「四值」这件事
#: 有一处完整的定义：少一格的话，加第五个判词时渲染会静默地少说一句。
_PREFLIGHT_MEANING: Mapping[str, str] = {
    PREFLIGHT_READY: "这个部署可以开一次真实试运行。",
    PREFLIGHT_BLOCKED_CONFIG: (
        "这个部署的配置还没就绪。要动的是配置或盘上的状态（地址、令牌环、"
        "状态库、profile 里的额度声明），不是代码，也不是别人给的访问权。"
    ),
    PREFLIGHT_BLOCKED_EXTERNAL: (
        "代码侧已经做完，但这个部署还缺一样**外面给的**东西（见 provider 那一行）。"
        "要动的是凭据或执行器本身，不是这个仓库里的接线。"
    ),
    PREFLIGHT_BLOCKED_CODE: (
        "这个构建里还有工具没接线（见 code 那一行）。先接线 —— 缺凭据这件事"
        "要等代码侧做完之后才谈得上，否则那句判词指向的是错的地方。"
    ),
}

#: 行的**名字与次序**。次序是承重的：装配在中途抛出时，前几行照答、从抛出那一行
#: 起标成「没问到」（``_not_reached``），于是「答到第几行」必须有唯一的定义 ——
#: 少了它，一次中途失败会被读成「后面几行都过了」。
_PREFLIGHT_CHECKS: tuple[str, ...] = (
    "config",
    "endpoint",
    "auth",
    "database",
    "provider",
    "budget",
    "code",
)


@dataclass(frozen=True)
class PreflightRow:
    """一行：一个被问到的问题、它的判词、以及**支撑这个判词的读数**。

    ``facts`` 只放**已经算出来的**读数，不放凭据 —— 这个报告会离开终端，与
    ``_payload`` 同一条纪律（那里刻意不含令牌环路径）。行与行之间不共享可变
    状态：``facts`` 是一个普通映射，渲染与 ``payload()`` 各读一次，谁都不改它。
    """

    check: str
    status: str
    detail: str
    facts: Mapping[str, Any]


@dataclass(frozen=True)
class PreflightReport:
    """一次试运行自检的全文：七行 + 由那七行算出来的一个判词。

    ``verdict`` 是**推出来的**，不是另存一格：一份能自己说自己就绪的报告与七行
    之间出现分歧时的形态，是「判词说 READY、而某一行写着某个东西缺失」——两份
    都长得正常，而读的人只会看判词。
    """

    rows: tuple[PreflightRow, ...]

    @property
    def verdict(self) -> str:
        return max((row.status for row in self.rows), key=_PREFLIGHT_SEVERITY.__getitem__)

    def payload(self) -> dict[str, Any]:
        return {
            "verdict": self.verdict,
            "rows": [
                {
                    "check": row.check,
                    "status": row.status,
                    "detail": row.detail,
                    "facts": dict(row.facts),
                }
                for row in self.rows
            ],
        }

    def render(self) -> str:
        """人读的那一份。**不打印任何凭据**，只打印存在性与计数。"""
        lines = ["== 试运行自检（不发起任何真实付费请求）==", ""]
        for row in self.rows:
            mark = "OK   " if row.status == PREFLIGHT_READY else "BLOCK"
            lines.append(f"[{mark}] {row.check:<9} {row.detail}")
            for key in sorted(row.facts):
                lines.append(f"{'':17}{key} = {_preflight_value(row.facts[key])}")
        lines += ["", "== 判词 ==", self.verdict, _PREFLIGHT_MEANING[self.verdict]]
        return "\n".join(lines)


def _preflight_value(value: Any) -> str:
    """一格读数的人读写法。字符串原样，其余走 JSON。

    逐字原样输出字符串是有意的：``detail`` 与路径这类东西里已经有标点，
    给它们套一层引号会让那些标点读起来像是取值的一部分。
    """
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def _not_reached(answered: int, reason: str) -> tuple[PreflightRow, ...]:
    """从第 ``answered`` 行开始填：**这一行**是失败的那一行，后面几行「没问到」。

    两者分开写而不是同一种措辞：失败的那一行有原因，后面几行没有 —— 把同一句
    原因复制到剩下的每一行里，会让读的人以为七处都有问题。这正是 ``_payload``
    那句「形状固定」在**故障路径**上的样子：行数不变，能答的照答。
    """
    head = PreflightRow(
        check=_PREFLIGHT_CHECKS[answered],
        status=PREFLIGHT_BLOCKED_CONFIG,
        detail=reason,
        facts={},
    )
    tail = tuple(
        PreflightRow(
            check=name,
            status=PREFLIGHT_BLOCKED_CONFIG,
            detail="装配在前面那一行就停下了，这一行没有问到。",
            facts={},
        )
        for name in _PREFLIGHT_CHECKS[answered + 1 :]
    )
    return (head, *tail)


def _row_config(settings: Settings) -> PreflightRow:
    """配置装载成功了。这一行的信息量在**故障路径**上，这里只记下装出来的是什么。

    ``research_only`` / ``send_authorized`` 报出来但**不驱动判词**：它们是这个模式
    的硬约束（``config.py`` 里刻意不做成环境变量），而试运行的结束条件块里
    ``PRODUCTION_AUTHORIZED`` 也是 ``NO``。把它们算成「不就绪」会把一条已经定下
    的模式读成一个待修的故障。
    """
    return PreflightRow(
        check="config",
        status=PREFLIGHT_READY,
        detail=f"配置装载成功，工作区是 {settings.profile!r}。",
        facts={
            "profile": settings.profile,
            "data_root": str(settings.data_root),
            "research_only": RESEARCH_ONLY,
            "send_authorized": SEND_AUTHORIZED,
        },
    )


def _row_endpoint(settings: Settings, host: str, resource: str) -> PreflightRow:
    """绑在哪、以及客户端**必须**拿哪个资源标识来要令牌。

    两格都要：``host`` 答「这个进程绑在哪个字面量地址上」（判据是解析之后的环回，
    见 ``resolve_bind_host``），``resource`` 答「令牌的受众要写什么」。后者在反向
    代理或隧道后面与前者**不同**，而那时的症状是全 401 而环看起来没问题 ——
    所以它必须自检里单独印出来。
    """
    return PreflightRow(
        check="endpoint",
        status=PREFLIGHT_READY,
        detail=f"服务面将绑在 {host}:{settings.mcp_port}，资源标识 {resource}。",
        facts={"host": host, "port": settings.mcp_port, "resource": resource},
    )


def _row_auth(settings: Settings, composed: _Composition, resource: str) -> PreflightRow:
    """环装得上、有主体、且**这个部署会用得上**。

    两个判定各有一条官方判据，这一行不自己另写一份：

    * 受众用 ``same_resource`` —— 与 ``auth.py`` 判定一条令牌属不属于本服务用的
      是**同一个函数**。写成 ``resource in ring.audiences`` 会在 scheme 大小写、
      默认端口、尾斜杠这几种写法上给出与真实认证相反的结论。
    * 工作区用 ``require_workspace``，与 ``RoleGuard`` 每一次 ``tools/call`` 用的
      是同一个。判的是**环里全部工作区的并集**：那是「有没有人能动这个部署」，
      而这一行问的正是这个。逐个主体地判会更细，但它的产物是「有的令牌能动、
      有的不能」—— 那是一条诊断，不是一道闸，而把它做成判词会让一份「至少有人
      能动」的部署看起来被堵死。
    """
    ring = composed.ring
    if ring is None:
        return PreflightRow(
            check="auth",
            status=PREFLIGHT_BLOCKED_CONFIG,
            detail=composed.blocker or "令牌环没有装载。",
            facts={"audiences": [], "principals": 0, "workspaces": []},
        )

    principals = list(ring.principal_names)
    audiences = list(ring.audiences)
    workspaces = list(ring.workspaces)
    facts: dict[str, Any] = {
        "principals": len(principals),
        "roles": list(ring.roles_present),
        "audiences": audiences,
        "workspaces": workspaces,
        "workspace": settings.profile,
    }
    if not principals:
        # 够不着的**后手**，不是一条活判据：``TokenRing.from_file`` 在装载期就拒绝
        # 零令牌的环（「空环与配错是同一件事」），所以环装上了而主体为空这个状态
        # 今天表达不出来 —— 它表达成 ``ring is None``。留着它是因为它守的那个状态
        # 本身还在（换一个不拒绝空环的装载器它就会活过来），而**它没有测试**这件事
        # 写在 ``tests/test_trial_preflight.py`` 那条判据的 docstring 里。
        return PreflightRow(
            check="auth",
            status=PREFLIGHT_BLOCKED_CONFIG,
            detail="令牌环装上了，但里面一个主体都没有 —— 谁都不许，服务面起了也调不动。",
            facts=facts,
        )
    if not any(same_resource(entry, resource) for entry in audiences):
        return PreflightRow(
            check="auth",
            status=PREFLIGHT_BLOCKED_CONFIG,
            detail=(
                f"环里没有一条令牌的受众是这个服务（{resource}）。"
                f"受众错配与令牌写错在传输层上是同一个 401，所以两者要靠这两行分开。"
            ),
            facts=facts,
        )
    try:
        require_workspace(
            granted=frozenset(workspaces),
            workspace=settings.profile,
            principal="(自检)",
            where="试运行自检的 auth 那一行",
        )
    except AuthorizationError as exc:
        return PreflightRow(
            check="auth",
            status=PREFLIGHT_BLOCKED_CONFIG,
            detail=str(exc),
            facts=facts,
        )
    return PreflightRow(
        check="auth",
        status=PREFLIGHT_READY,
        detail=(
            f"令牌环里有 {len(principals)} 个主体，受众与工作区都覆盖这个部署。"
        ),
        facts=facts,
    )


def _row_database(composed: _Composition) -> PreflightRow:
    """状态库那一道闸。判据与措辞都住在 ``db.read_schema_state``，这一行只是搬过来。

    升格成 BLOCKED_CONFIG 而不是让 ``_compose`` 抛：两者是同一件事的两种说法，
    而这一行还带得出 ``standing`` / ``recorded`` / ``expected`` 三个读数 ——
    「库比代码旧」与「库比代码新」要动的东西相反，合成一句就看不出是哪一种。
    """
    database = composed.database
    facts = {
        "path": str(database.path),
        "standing": database.standing,
        "recorded": database.recorded,
        "expected": database.expected,
        "headline": database.headline,
    }
    return PreflightRow(
        check="database",
        status=PREFLIGHT_READY if database.startable else PREFLIGHT_BLOCKED_CONFIG,
        detail=database.message if not database.startable else database.headline,
        facts=facts,
    )


def _row_provider(settings: Settings) -> PreflightRow:
    """本部署**有哪几样外部能力**。只报存在性，一个取值都不打印。

    ## 只有 serper 与 quay 驱动判词

    ``serper`` 是这一栈里**唯一一条按调用计费**的检索提供方，而缺了它
    ``search_web`` 仍然接线、仍然受理，只在调用点明确拒绝（``research.py`` 那段
    「这不是一次『检索过、没有结果』」）。``quay`` 是派发路径 —— 缺了它
    ``default_dispatcher`` 返回 ``None``，而这个部署就**没有执行器**：一次试运行
    连一件工作都派不出去。两样都是「外面给的」，所以缺了判 ``BLOCKED_EXTERNAL``。

    另外三样报出来但**不驱动判词**，各有各的书面理由，不是这里省事：

    * ``jev`` / ``laya`` 是辅助判定后端。缺凭据/权重时 ``ready`` 为假，而
      ``decide`` 照常答得上来 —— 它答的是一条带理由的 ``UNAVAILABLE``。
      ``composition.default_decisions`` 逐字：「缺凭据/checkpoint 是一个 blocker，
      **不是一次伪造的成功推理**」。所以它不堵试运行。
    * ``feishu`` §20 已经定过：真实凭据未授权，``BLOCKED_EXTERNAL`` 写在
      ``docs/DEPLOYMENT.md`` §2 的 ``Feishu`` 一节（那里是实测的缺项清单与解除条件），
      而它**不是**核心试运行的阻塞项。让它驱动判词会让这一条永远出不来 ``READY``。

    后一句话曾经指向一个不存在的地址（原文只写「写在别处」）：一份**指着别处**的
    docstring 只有在那一处真的写了的时候才算数，而这一句是 `--check` 与自检
    分家的理由之一，读的人会真的去找。地址现在写在这里，删哪一半都会少一句话。
    """
    flags = {
        "serper": settings.serper_enabled,
        "quay": settings.quay_enabled,
        "jev": settings.jev_enabled,
        "laya": settings.laya_enabled,
        "feishu": settings.feishu_enabled,
    }
    blocking = [name for name in ("serper", "quay") if not flags[name]]
    facts: dict[str, Any] = {**flags, "blocking": blocking}
    if blocking:
        return PreflightRow(
            check="provider",
            status=PREFLIGHT_BLOCKED_EXTERNAL,
            detail=(
                f"本部署缺这几样外部能力：{blocking}。"
                "这不是接线问题（code 那一行答的是那个），也不是配置写错了 —— "
                "要动的是凭据或执行器本身。"
            ),
            facts=facts,
        )
    return PreflightRow(
        check="provider",
        status=PREFLIGHT_READY,
        detail="检索提供方与执行器都在，另外三样按各自的存在性报在下面。",
        facts=facts,
    )


def _row_budget(settings: Settings) -> PreflightRow:
    """这个部署**说得出上限**。

    ## 为什么读的是两个函数而不是一个布尔

    ``declared_search_ceiling`` / ``declared_document_ceiling`` 是 ``submit_research``
    在受理时问的同一对函数（``application/budget.py`` 的 docstring 写了为什么它们
    必须是同一个实现）。两个数字各自缺的时候是两句不同的话，合成一个布尔之后
    「忘了声明检索额度」与「故意给它零」在报告上不可区分 —— 而这两件事的处置相反。

    ## 它答的不是「钱够不够」

    ``submit_run`` 的 ``budget`` 是**调用方**给的映射（``runs.py``），所以这一行
    能答的上界只到「这份部署说得出上限」。一个说得出 20 次检索的 profile 与一个
    说得出 2000 次的 profile，在这一行上都是 ``READY`` —— 这一行不替人选额度，
    §29 那条「不要替 Member-A 批准预算」在这里的形状就是这样。
    """
    bundle = load_profile(settings)
    document = declared_document_ceiling(bundle)
    search = declared_search_ceiling(bundle)
    return PreflightRow(
        check="budget",
        status=PREFLIGHT_READY,
        detail=(
            f"profile {bundle.profile_id!r} 声明了检索与抓取两条上限。"
            "（这一行只答『说得出上限』，不答『额度够不够』。）"
        ),
        facts={
            "profile_id": bundle.profile_id,
            "search_max_queries": search,
            "fetch_max_documents": document,
        },
    )


def _row_code(composed: _Composition) -> PreflightRow:
    """目录里有没有还落在「尚未接线」那一格的工具。

    ## 判据就是 ``_register`` 的第三种结局

    ``ToolBacking`` 的三格与 ``_register`` 的三段是同一份判定（它是从判定中间件
    读回来的，不是重算的）。``not_registered`` **不算**未接线：那是本部署明确
    裁掉的能力（今天只有外发那几条），它有自己的行、也有自己的处置。剩下那一格
    ``stubbed`` 才等于「这段代码还不存在」。

    这一行的存在理由是把 ``BLOCKED_CODE`` 与另外两条判词分开：一个还没接完线的
    构建，无论配置和凭据多齐，判词都不该落在另外两个名字上。
    """
    backing = composed.backing
    if backing is None:
        return PreflightRow(
            check="code",
            status=PREFLIGHT_BLOCKED_CODE,
            detail=(
                "这份装配里读不到判定中间件，所以「目录里每一条接线了没有」无从回答。"
                "这是一个装配故障，不是一次「都接上了」。"
            ),
            facts={},
        )
    phases: dict[str, int] = {}
    for name in sorted(backing.stubbed):
        phase = TOOL_CATALOG[name].phase
        phases[phase] = phases.get(phase, 0) + 1
    facts: dict[str, Any] = {
        "catalog_total": len(TOOL_CATALOG),
        "backed": len(backing.backed),
        "not_registered": len(backing.not_registered),
        "stubbed": len(backing.stubbed),
        "stubbed_phases": phases,
    }
    if backing.stubbed:
        breakdown = "、".join(f"Phase {phase} {count}" for phase, count in sorted(phases.items()))
        return PreflightRow(
            check="code",
            status=PREFLIGHT_BLOCKED_CODE,
            detail=(
                f"目录 {len(TOOL_CATALOG)} 条里有 {len(backing.stubbed)} 条还没接线"
                f"（{breakdown}）。"
            ),
            facts=facts,
        )
    return PreflightRow(
        check="code",
        status=PREFLIGHT_READY,
        detail=(
            f"目录 {len(TOOL_CATALOG)} 条全部落在「接了实现」"
            f"（{len(backing.backed)}）或「本部署裁掉」"
            f"（{len(backing.not_registered)}）两格里，没有一条还在等它的阶段。"
        ),
        facts=facts,
    )


def trial_preflight() -> PreflightReport:
    """``marketops trial preflight`` 的全部判据。**不发起任何真实付费请求。**

    七行各自答一个不同的问题，判词由这七行**最重的那一个**算出来
    （``_PREFLIGHT_SEVERITY``）。行的次序也是装配的次序：``_compose`` 在第三步，
    所以它抛的时候前三行已经答完了 —— ``_not_reached`` 从抛出那一行起标注，
    于是「装配停在哪一步」在报告上是一个位置，而不是一句笼统的失败。

    与 ``_check()`` 共用 ``_compose``，因此**同一条路径**：一个 ``--check`` 说
    「起得起来」的部署，在这里不会得到一句与它无关的话。
    """
    rows: list[PreflightRow] = []
    try:
        settings = load_settings()
        rows.append(_row_config(settings))
        host = resolve_bind_host(settings.mcp_host)
        resource = resource_identifier(settings)
        rows.append(_row_endpoint(settings, host, resource))
        composed = _compose(settings)
        rows.append(_row_auth(settings, composed, resource))
        rows.append(_row_database(composed))
        rows.append(_row_provider(settings))
        rows.append(_row_budget(settings))
        rows.append(_row_code(composed))
    except MarketOpsError as exc:
        # 一个 except 而不是几个：这一层要的是「停在第几行」，而三类的分别已经
        # 在那一行的**内容**里（``ConfigError`` 说地址，``BudgetNotConfiguredError``
        # 说额度）。分成几支只会让「还有第四种异常没接住」在第一次出现时变成一堆
        # 堆栈 —— 而这一份报告的契约正是「形状固定，故障路径上也一样」。
        rows.extend(_not_reached(len(rows), f"{type(exc).__name__}: {exc}"))
    return PreflightReport(rows=tuple(rows))


def wire_research(settings: Settings) -> Mapping[str, Handler]:
    """研究侧（Phase D/E）的**接线** —— 本层是全仓唯一能点名具体厂商 adapter 的两处之一。

    ``search_web`` 的检索提供方**没配 key 就不接**（这里传 ``None``）：构造
    ``SerperSearchProvider`` 需要一个 API key，而一个没配 key 的部署仍然要能
    起服务面 —— 今天它就能起，``get_capabilities`` 报 ``serper.configured: false``。

    这个判断（``settings.serper_enabled``）与 ``cli.py`` 起跑前那处是**同一条**，
    而两边的处置是**相反的**：CLI 缺 key 就拒绝运行（退 2，它没有「假装跑过」这
    个选项），服务面缺 key 照常起（它必须能被部署起来、然后如实回答「没接线检索」）。
    同一个谓词、两种处置，是因为两个入口的失败代价不同 —— 而把这条谓词抄成两份
    实现，会让它们在某一天对同一份配置给出相反的回答。

    账本用**工厂**而不是实例：``sqlite3`` 的连接默认不允许跨线程使用，而工具
    处理器可能在别的线程上跑。读一次开一次是本仓已有的先例（``tools.py``）。
    """
    from ...adapters.mcp_server.research import research_handlers
    from ...adapters.serper import SerperSearchProvider
    from ...adapters.sqlite.evidence_store import SqliteEvidenceStore
    from ...adapters.sqlite.ledger import SqliteBudgetLedger
    from ..composition import default_acquisition, default_crawl, default_router

    acquisition = default_acquisition(settings)

    return research_handlers(
        settings=settings,
        search=SerperSearchProvider(settings) if settings.serper_enabled else None,
        acquisition=acquisition,
        router=default_router(mineru_output_dir=settings.mirrors_dir / "mineru"),
        ledger_factory=lambda: SqliteBudgetLedger(settings.database_path),
        # 证据库用**工厂**：与账本同一条理由（``sqlite3`` 的连接不能跨线程）。
        # 它是必填的，不像 ``search`` 那样可以是 ``None`` —— 抓到的材料落成
        # 证据是这两个工具的正常产出，不是可选接线（见 ``research_handlers``）。
        evidence_store_factory=lambda: SqliteEvidenceStore(settings.database_path),
        crawl=default_crawl(acquisition),
    )


def wire_decisions(
    settings: Settings,
) -> tuple[
    Mapping[str, Handler],
    Mapping[str, DecisionProvider],
    DecisionProvider | None,
    Callable[[], DecisionRecordStore],
]:
    """决策侧（Phase F）的接线：处理器表、后端表、被选中的那一个、判定台账的工厂。

    **三样一起返回，因为它们必须来自同一次构造。** ``default_decisions`` 建的是
    ``LayaDecisionProvider``，而它每个实例各持一个 421M 参数的 Router —— 同一个
    checkpoint 装两遍不是慢，是 OOM（理由写在 ``composition.default_decisions``）。
    所以这个函数每次调用只构造一次，三个返回值共用那一批对象。

    ``advisory`` 为 ``None`` 表示本部署没指定辅助后端（``MARKETOPS_DECISION_PROVIDER``
    未设置）。那时 ``decide`` 照常接线，并明确回答「本部署没有指定辅助后端」——
    一个说得清理由的拒绝，好过一个「尚未实现」（后者要等，前者只要加一个环境变量）。
    指定了一个构造不出来的名字则是 ``ConfigError``：那是一次配置事故，而一个照常
    起来的服务会让运维以为 ``decide`` 在工作（与 ``_load_ring`` 同一条判据）。

    第四个返回值是**判定台账的工厂本身**，它同时交给 ``submit_findings`` 那一条
    （``_compose`` 把它一分为二传给两个表）：一次判定由 ``decide`` 写下来、由
    ``submit_findings`` 读回来核对，两条路必须落在同一个库的同一张表上 —— 而
    「同一个库」这件事只有一个地方说得出（这里是 ``settings.database_path``）。
    各建一份的形态是：研究员问的那次判定在 A 里、结算时读的是 B，于是每一条责任
    结论都得到「它指的判定解不开」，而两份记录**各自都正常**。
    """
    from ...adapters.mcp_server.decisions import decision_handlers
    from ...adapters.sqlite.decisions import SqliteDecisionRecordStore
    from ..composition import advisory_decision_provider, default_decisions

    providers = default_decisions(settings)
    advisory = advisory_decision_provider(settings, providers)
    # 判定记录的落点用**工厂**，与账本、证据库、作业存储同一条理由：``sqlite3``
    # 的连接默认不允许跨线程使用。它落的是**同一个库**的另一张表 —— 判定记录
    # 与它归属的那件工作单元必须在同一个库里，否则那条外键无法成立。
    records: Callable[[], DecisionRecordStore] = lambda: SqliteDecisionRecordStore(  # noqa: E731
        settings.database_path
    )
    return (
        decision_handlers(settings=settings, advisory=advisory, records=records),
        providers,
        advisory,
        records,
    )


@dataclass(frozen=True)
class _Composition:
    """服务面**起得来**所需的全部构造物。``--check`` 与 ``_serve()`` 共用这一处。

    两者唯一的差别是最后一步跑不跑 ``server.run(...)``。做成共用是因为自检曾经
    **只**读配置、解析地址、装令牌环 —— 它会说「可以启动」，而一个
    ``MARKETOPS_QUAY_*`` 配了一半的部署会在真正启动时退 2。同一个部署得出两句
    互相矛盾的话，而先被跑到的、也是唯一会被写进工单的那句，是自检那句。

    ``database`` 也在这里取，理由完全相同而多一条：状态库的判决是**一次读数**，
    两次读之间隔着一整份装配（决策后端要装一个 421M 的 Router）—— 那段时间足够
    让别的东西把这个库改掉，而两个表面各读一次就会出现「自检说 v3、启动时说 v2」。
    """

    settings: Settings
    host: str
    ring: TokenRing | None
    blocker: str
    database: DatabaseReadiness
    server: MCPServer

    @property
    def backing(self) -> ToolBacking | None:
        """这一份装配把目录的每一条做到了哪一步，**从 ``server`` 读回来**。

        不另存一格的理由与 ``database`` 那一格**相反**、而不是同一条：那个字段存在
        是因为它是**一次读数**（两次读之间隔着一整份装配，而两个表面各读一次就会
        出现「自检说 v3、启动时说 v2」）；这一格恰恰不是读数 —— 它是 ``server``
        **自身**的一个事实，而另存一份的形态是「某一条工具在自检里是接线的、
        在真正那个实例上是 stub」，两份都长得正常。

        ``None`` 表示这个 ``server`` 上没有判定中间件（手搓的实例），**不是**
        「一条都没接」—— 那两个结论相反，见
        :func:`~marketops.adapters.mcp_server.server.tool_backing`。
        """
        return tool_backing(self.server)


def _compose(settings: Settings) -> _Composition:
    """构造服务面需要的一切，**不绑端口、不建运行目录**。

    最后一条对这一次探问也是硬要求：``read_database_readiness`` 在库文件不存在时
    **不打开它**，所以既不会建库、也不会建 ``state/`` —— 一个会把目录树建出来的
    自检，会在「目录还没准备好」这种真故障面前先把故障修掉一半。

    Raises:
        ConfigError: 地址不是环回字面量，或某个后端/执行器是**配错了**（不是「没配」）。
    """
    from ...adapters.deterministic_writer import DeterministicDraftWriter
    from ...adapters.mcp_server.budget import budget_handlers
    from ...adapters.mcp_server.claim_withdrawal import claim_withdrawal_handlers
    from ...adapters.mcp_server.continuation import continuation_handlers
    from ...adapters.mcp_server.effective_scene import effective_scene_handlers
    from ...adapters.mcp_server.feedback import feedback_handlers
    from ...adapters.mcp_server.fetch_attempt import fetch_attempt_handlers
    from ...adapters.mcp_server.followups import followups_handlers
    from ...adapters.mcp_server.outreach import (
        outreach_handlers,
        unavailable_outreach_tools,
    )
    from ...adapters.mcp_server.launch import launch_handlers
    from ...adapters.mcp_server.product_catalog import product_catalog_handlers
    from ...adapters.mcp_server.product_readiness import product_readiness_handlers
    from ...adapters.mcp_server.product_loop import product_loop_handlers
    from ...adapters.mcp_server.review import review_handlers
    from ...adapters.mcp_server.runs import run_handlers
    from ...adapters.mcp_server.schedule_action import ResearchScheduleAction
    from ...adapters.mcp_server.scheduling import scheduling_handlers
    from ...adapters.mcp_server.views import views_handlers
    from ...adapters.mcp_server.workstream import workstream_handlers
    from ...adapters.quay_cli import read_worker_outcomes
    from ...adapters.reply_classifier import RuleBasedReplyClassifier
    from ...adapters.sqlite.jobs import SqliteJobStore
    from ...adapters.sqlite.ledger import SqliteBudgetLedger
    from ..composition import (
        default_account_cohort_store,
        default_account_pool_store,
        default_acquisition,
        default_action_item_store,
        default_asset_store,
        default_attempt_store,
        default_claim_withdrawal_store,
        default_correspondence_source,
        default_dispatcher,
        default_document_budget,
        default_effective_scene_store,
        default_evidence_reader,
        default_feedback_store,
        default_maintenance_store,
        default_notification_store,
        default_outreach_store,
        default_outreach_transport,
        default_product_catalog_store,
        default_relation_store,
        default_review_store,
        default_router,
        default_run_store,
        default_saved_view_store,
        default_scene_store,
        default_schedule_store,
        default_session_binding_store,
        default_wakeup_registry,
        default_watch_store,
        default_workstream_store,
        outbound_transport_reason,
    )

    host = resolve_bind_host(settings.mcp_host)
    # 决策后端**只在这里构造一次**：两个后端 + 被选中的那一个 + 处理器表。
    # 构造本身不碰网络、不 import 厂商 SDK，所以自检跑一遍它是安全的（§2）。
    decision_tools, decisions, advisory, decision_records = wire_decisions(settings)
    research = {**wire_research(settings), **decision_tools}
    # 产品环。``scenes`` 是**工厂**（理由写在 ``default_scene_store``），而且同一份
    # 工厂交给两个地方：产品环那三条工具用它读写场景，``get_scene`` 用它读回场景
    # 的**状态列**。两处若各建一份，「刚立项」与「读不到那个场景」就可能来自两个
    # 不同的库 —— 而这种分歧只会在有人已经依赖它之后才被发现。
    scenes = default_scene_store(settings)
    # 场地资产池与责任关系表各建**一次**，两个消费者共用同一个闭包：产品环那三条
    # （``import_motherpool`` / ``promote_receipt`` / ``record_relation``）与研究侧的
    # ``submit_findings``。同一个 ``Settings.database_path`` 上的两个闭包其实等价，
    # 但**让它们等价这件事只应该有一个地方说**（``default_*_store``），而在调用点
    # 各写一行 `default_asset_store(settings)` 是把它说第二遍 —— 那一行将来被改错的
    # 时候，两处会指向两个库，而两边的回执都正常。
    assets = default_asset_store(settings)
    relations = default_relation_store(settings)
    # 跟进池同样各建**一次**、**两个**消费者共用，理由与上面 ``assets`` 那段逐字同款：
    # 结束面（``close_run`` 把这一轮开出来的待办 id 写回 Delta 的 ``actions`` 那一格）
    # 与待办那一族工具的 ``items``。上一版 ``run_handlers`` 拿不到它，于是
    # ``adapters/mcp_server/runs.py`` 在**那里**又写了一行
    # ``SqliteActionItemStore(settings.database_path)`` —— 两句在同一个 ``database_path``
    # 上其实等价，而「它们等价」这件事当时有**两个**地方在说。
    action_items = default_action_item_store(settings)
    # 维护存储（观测、变更信号、复核回执）**与 scenes 分开构造，尽管同一个库文件**：
    # ``ports/scenes.py`` 把它们分成两个 ABC，而合成一个参数会让「这个部署有没有
    # 场景存储」与「它有没有维护存储」在签名上变成同一件事。两者的工厂都是惰性的，
    # 所以自检那一条路照样不建库（``default_scene_store`` 的 docstring 写了理由）。
    #
    # 这一份**同一个**工厂交给两处：产品环那三条维护工具（读观测、写观测、读到期），
    # 以及研究侧 ``submit_findings`` 走的 ``produce_routes``（结算真的落下新证据
    # 行时写变更信号）。两处各建一份的形态是「结算写了信号、维护 tick 永远看不见」，
    # 而那两边的报告各自都正常 —— 与 ``scenes`` / ``assets`` 同一条。
    maintenance = default_maintenance_store(settings)
    # 账号池与冻结成员名单（§6 / §B12）各建**一次**，两个消费者共用同一个闭包：
    # 产品环（``list_account_pool`` / ``remember_accounts`` / 那三条名单工具，
    # 以及维护那三条的主体来源）与外联那两条（``draft_outreach`` / ``prepare_wave``
    # 的收件人从路线来、路线从主体来）。与 ``assets`` / ``relations`` / ``maintenance``
    # 逐字同一条：同一个 ``Settings.database_path`` 上的两个闭包其实等价，但
    # **让它们等价这件事只应该有一个地方说**（``default_*_store``）；在调用点各写
    # 一行，那一行将来被改错时两处会指向两个库，而两边的回执都正常。
    accounts = default_account_pool_store(settings)
    cohorts = default_account_cohort_store(settings)
    # 收件箱（§12）的工厂，建**一次**、交给三处 —— 这是本文件里共用闭包那条纪律
    # 走到最多消费者的一个：``product_loop`` 的 ``control_scene`` 用它读那道闸门
    # （「这一版有没有一件待办在等批准」），控制面的 ``_resolve_and_compile`` 用它
    # 写生产者（受理时被身份判据拒掉的那几行落成待办），``review`` 那七条工具用它
    # 读写待办本身。三处各建一份的形态不是「多了两个对象」，而是三条各自都自洽、
    # 只在并排看时才矛盾的报告：受理说这几行开了待办、收件箱里没有它们、而闸门
    # 说没有一件在等 —— 三个回执都正常，问题只在没人能把它们对起来。
    #
    # 它是惰性的（``default_review_store`` 的 docstring 写了理由），所以自检那一条路
    # 照样不建库。
    review = default_review_store(settings)
    product_loop = product_loop_handlers(
        settings=settings,
        scenes=scenes,
        maintenance=maintenance,
        # 资产池（§4）的工厂，与 ``scenes`` / ``maintenance`` 分开，尽管它们指向同一个
        # 库文件 —— 理由写在 ``product_loop_handlers`` 的签名上。它是惰性的，所以自检
        # 那一条路照样不建库。
        #
        # 这几行原先各自写着一个序数（「第三个」「第四个」）。那句散文没有任何执行器，
        # 而每加一个工厂它就变旧一次 —— §6·2 在 ``composition.py`` 里已经删掉过一个同
        # 类的话。这里一并删掉：没有数可以变旧，与「本仓的计数散文必须有执行器」是同一
        # 条纪律的两个方向。
        assets=assets,
        # 责任关系表（§5）的工厂，与 ``assets`` 分开的**理由特别值得写下**：两者的行都在
        # 同一个库文件里，而且关系那一侧读的时候要拿 ``assets`` 去取场地的名字 ——
        # 但它们是两个端口、两条合并规则、两个生产者。「关系表接没接」与「资产池接没接」
        # 是两件可以各自缺席的接线，合成一个参数会让其中一件的缺席看起来像另一件。
        # 它也是惰性的。
        relations=relations,
        # 账号池（§6）的工厂，与前几个分开 —— 同一条理由，而这里还有一条自己的：
        # 「账号池接没接」与「清单文件读不读得到」是两件不同的事。前者是研究产出落盘的
        # 地方（``remember_accounts`` / ``list_account_pool``），后者是 operator 维护的
        # 那份 YAML（``list_accounts``）—— 两者都留着，而它们在名字上分得开。它也是惰性的。
        accounts=accounts,
        # 冻结成员名单（§B12）的工厂，与 ``accounts`` 分开 —— **同一条理由在这一族里
        # 已经被验过第三次**：两者指向同一个库文件，但一个是「研究产出落盘的账号」
        # （累积并修订、有合并规则），另一个是「某一次读池子冻下来的读数」（内容寻址
        # 的覆盖写、没有合并规则）。「账号池接没接」与「名单能不能冻」是两件可以各自
        # 缺席的接线。它也是惰性的。
        cohorts=cohorts,
        # 收件箱（§12）的工厂。``control_scene`` 那道闸门读它 —— 而它必须与
        # ``review_handlers`` 收到的是**同一个**：闸门问「这一版有没有一件待办在
        # 等批准」，收件箱答「有哪几件待办」，两处各建一份的形态是「激活被拒，
        # 而收件箱里查不到那件待办」—— 一次拒绝给了一个不存在的案号。
        review=review,
        # 抓取器、解析器、抓取额度账本（§B22 的页面复核要的最后三样）。
        #
        # ``acquisition`` 与 ``wire_research`` 那一份是**两个实例**，而这是刻意的：
        # 那边两条工具各自一拍一次抓取（一个调用方在等着），这一边还要跨格共用同一
        # 批页面（一趟 tick 里两个面看同一批 URL ⇒ 抓取恰好一次）。共用实例就等于
        # 让「一次 tick」与「一次工具调用」共享限速与缓存，而它们不是同一件事。
        #
        # ``router`` 的 MinerU 输出目录与那边逐字相同：解析缓存落在同一处，两次
        # 解析同一个页面不会各写一份镜像。这不是共享状态，是同一个**目录**约定。
        #
        # ``documents`` 是**函数**而不是实例，且每次调用现造一个
        # ``SqliteBudgetLedger`` —— ``sqlite3`` 的连接不能跨线程，与上面那组工厂同一
        # 条理由。它读 profile 的 ``fetch.max_documents`` 定这一趟的上限，没配就
        # 失败关闭（``BudgetNotConfiguredError``），不给缺省。
        acquisition=default_acquisition(settings),
        router=default_router(mineru_output_dir=settings.mirrors_dir / "mineru"),
        documents=default_document_budget(settings),
    )
    # 产品目录的**登记面**（B01 第四条关闭条件）：三条工具，成员关系不随部署变 ——
    # 与 ``review_tools`` / ``continuation`` / ``attempts`` / ``reservations`` / ``runs``
    # 同一条（接了就三条，没接就是空表），所以它不需要一个 ``unavailable`` 那样的
    # 第二份。它的店落在**同一个** ``database_path`` 上，理由写在
    # ``default_product_catalog_store`` 的 docstring 上：产品版本会被草稿回链、Offer
    # 会被 Scene 引用，而「回链解不解得开」只有在两边同一个库时才判得出来。
    #
    # 它是惰性的（``SqliteProductCatalog.__init__`` 只在**第一次真的构造**时才
    # ``ensure_schema``），所以自检那一条路照样不建库：``product_catalog_handlers``
    # 只是把工厂收进闭包，一个连接都不开。
    product_catalog = product_catalog_handlers(
        settings=settings,
        catalogs=default_product_catalog_store(settings),
    )
    # B01 的**查缺面**（PSM §10「启动前返回结构化 missing/invalid/awaiting_approval」）：
    # 两条只读工具，读的是上面那三条登记下去的那几版。两个工厂与登记面收到的是**同一
    # 组** ``settings`` —— 同一条纪律（落点由组合根说，不由这里替它猜）：
    #
    #   * ``catalogs`` 与登记面逐字同形（同一个 ``default_product_catalog_store``），
    #     而这不是顺手：查缺面要读的正是登记面写下去的那一版，「写进哪个库、从哪个库
    #     读回来」如果是两处各自算的，分家之后两边都正常。
    #   * ``claims`` 是已批准说法的读面（``default_outreach_store``）。方案那一版靠
    #     哪些说法，判它作不作数要拿这份读面 —— 与 ``outreach_handlers`` 收到的是
    #     同一个库文件上的工厂。
    product_readiness = product_readiness_handlers(
        settings=settings,
        catalogs=default_product_catalog_store(settings),
        claims=default_outreach_store(settings),
    )
    # B01 的**启动预览面**（切片 b01-d，第五条关闭条件逐字「启动授权前零外部调用」）：
    # 一条只读工具。三样工厂与 ``product_loop`` / ``product_readiness`` 收到的是
    # **同一组** —— 这不是顺手：
    #
    #   * ``scenes`` 是上面那一份 ``default_scene_store(settings)``。预览读的是**受理面
    #     要受理的那一份记录**，「预览说这一版可以启动、受理按另一个库判定」正是
    #     两个实例指两个库时的分家形态，而两边各自的回执都正常。
    #   * ``catalogs`` / ``claims`` 与查缺面逐字同形，理由写在查缺面那一段（被引的
    #     产品版本与已批准说法判作不作数，读的必须是登记面写下去的那一版）。
    launch = launch_handlers(
        settings=settings,
        scenes=scenes,
        catalogs=default_product_catalog_store(settings),
        claims=default_outreach_store(settings),
    )
    # 外部执行器：今天没有任何工具会调它，所以「没构造出来」不影响服务面起得来 ——
    # 但**配错了**要在这里停下。`default_dispatcher` 只在三样一个都没给时返回
    # None；配了一半、或路径不在盘上，都是 ConfigError，于是退 2（「原因在启动
    # 参数或环境」，见模块 docstring 的退出码一节）。
    quay = default_dispatcher(settings)
    # 外联那一段（§6/§7）。三样东西各自决定自己的那几条工具**存不存在**：
    #
    #   * ``transport``：没有它就没有 ``send_wave`` / ``reconcile_wave``。它是
    #     「这个部署能不能发信」的接线事实，**不是** ``settings.outbound_capable``
    #     —— 后者只回答「SMTP 那六样配齐没有」。用派生值决定注册，会让「配齐了但
    #     组合根没构造传输」变成一条注册了却调不动的工具。
    #   * ``source``：没有它就没有 ``ingest_replies``。
    #   * ``store`` / ``writer`` / ``classifier``：三样都无状态且总在，所以它们
    #     不参与「注册不注册」。
    transport = default_outreach_transport(settings)
    source = default_correspondence_source(settings)
    outreach = outreach_handlers(
        settings=settings,
        scenes=scenes,
        store=default_outreach_store(settings),
        # ``policy_version`` 不在这里给：它是**策略那一侧**的版本，而本部署今天
        # 没有任何一份带版本的写作策略（``docs/SCENE_EVOLUTION.md`` 把
        # ``writing-policy.yaml`` 列为未来）。缺省的空串就是「我不知道」，而那是
        # 实话；写一个像版本号的值会让「策略改了但键没变」这件事在报告上消失。
        writer=DeterministicDraftWriter(),
        classifier=RuleBasedReplyClassifier(),
        # 主体那两样（与产品环**共用同一组闭包**，见上面那一段）：收件人从路线来、
        # 路线从主体来，所以这两条工具也要能回答「这一批按哪一份名单选的」。
        accounts=accounts,
        cohorts=cohorts,
        transport=transport,
        source=source,
    )
    # 「缺了哪几条」由**同一组常量**从同样的两个 ``None`` 算出来，而不是在
    # ``outreach`` 那一侧顺手记一笔：分开算的话，「注册了却报成没有」与
    # 「没注册却不说为什么」各要一次人工比对才能发现。
    #
    # 两个理由串都**只在真的缺**的时候去算。``outbound_transport_reason`` 在
    # 不缺时会抛 ``ConfigError``（它没有话可说，而一句无条件拼出来的理由会在一台
    # **有**外发路径的机器上被写进某份报告里）；那句守卫与
    # ``default_outreach_transport`` 返回 ``None`` 的条件是同一个 ——
    # ``settings.outbound_missing`` 非空 —— 所以这里读的就是那个条件本身。
    missing_outbound = settings.outbound_missing
    unavailable = unavailable_outreach_tools(
        transport=transport,
        transport_reason=outbound_transport_reason(settings) if missing_outbound else "",
        source=source,
        source_reason=(
            ""
            if source is not None
            else "本部署没有配置来信来源（MARKETOPS_MAIL_CAPTURE_DIR）—— "
            "没有信箱可归属时，收进来的每一条都关联不上任何一次发送。"
        ),
    )
    # §12 收件箱那七条工具。与 ``product_loop`` / ``outreach`` 同一条：**这一份的
    # 成员关系不随部署变**（收件箱接了就是七条，没接就是空表），所以它不需要一个
    # ``unavailable`` 那样的第二份 —— ``review_handlers`` 在 ``review is None`` 时
    # 回空表，那七条于是落回 stub，而 ``get_capabilities`` 的 ``wired`` 那一格
    # 如实报它们不在（它读的就是这一份的键）。
    review_tools = review_handlers(settings=settings, scenes=scenes, review=review)
    # §B23 反馈环那八条工具。与 ``review_tools`` / ``continuation`` / ``attempts``
    # 同一条：一份处理器表，成员关系不随部署变（接了就八条，没接就是空表），所以它
    # 也不需要 ``unavailable`` 那样的第二份。
    #
    # 四样工厂里有三样**必须与别处同源**，而这三条是这一族仅有的引用：
    #
    #   * ``review`` 是上面那一份 ``default_review_store(settings)`` —— 也就是
    #     ``review_handlers`` 与 ``control_scene`` 那道闸门收到的那一个。一份补丁的
    #     批准读数从**既有的** ``ReviewCase`` 推（``standing_of``），而**批准本身不在
    #     这八条里**（仍只走 ``approve_policy_change``）。两个库时的形态是「提案说在
    #     等一个人」与「收件箱里没有这件案子」同时成立 —— 两个回执各自都正常，问题只
    #     在没人能把它们对起来，而那次红会指向这八条里每一条，唯独不指向这一处接线。
    #   * ``evidence`` 是 ``default_evidence_reader(settings)``，**不是**一行直接构造
    #     ``SqliteEvidenceStore`` 的 lambda：这一条读法要分得开「这个部署还没有证据库」
    #     与「这个工作区里没有这个 id 的行」，而折成一个空读会让一条候选的第二格永远
    #     读成一个像业务结论的 0。理由与那句异常正文写在那个函数的 docstring 上。
    #   * ``scenes`` 是上面那一份 ``default_scene_store(settings)`` —— 与
    #     ``review_handlers``（``:1207``）、产品环那三条工具共享的**同一个**工厂。它是
    #     **应用**那一档的落点：一份补丁被批准之后，要回答的「它想产生的那一版**在不在
    #     盘上、是不是当前的生效版**」只能去问这个场景存储（见
    #     ``application/feedback.py`` 的 ``_application_of``）。而 ``scene_id`` 本身
    #     是从 ``ReviewCase`` 拿的 —— 于是这条链的两端都落在这两样上：``review`` 给出
    #     「说的是哪个场景」，``scenes`` 给出「那一版到底有没有」。两个库时的形态同样是
    #     「批准了」与「盘上没有那一版」各自为真，而分开的写法会让它报成前者。
    #
    #     缺省**不是降级**：``scenes is None`` 时那一档如实报 ``unreadable``（不是
    #     ``not_produced`` —— 「没接线」与「还没产版」是两句话，见 ``feedback_handlers``
    #     的 docstring）。但今天这条线上它是接上的，所以生产上这一档报的是真的读数。
    #
    # 四样都是惰性的，所以自检那一条路照样不建库 —— 而证据那一格连「真的读到它」的
    # 那一次也不建：它是读面。
    feedback = feedback_handlers(
        settings=settings,
        feedback=default_feedback_store(settings),
        evidence=default_evidence_reader(settings),
        review=review,
        scenes=scenes,
    )
    # §B21 的续跑状态页。**只有一条工具，而它只读** —— 这一份的成员关系不随部署
    # 变（接了就有一条，没接就是空表），所以它也不需要 ``unavailable`` 那样的第二份。
    #
    # 归属注册表（``default_wakeup_registry``）是惰性的，而它的第一个消费者正是
    # 这一条只读工具：``SqliteWakeupRegistry.__init__`` 会 ``ensure_schema``，
    # 也就是**会建库**。于是「一次自检留下一个空库」这件事在两道地方被挡住 ——
    # 工厂挡住组合期，处理器里那道 ``database_path.is_file()`` 挡住「还没写过东西
    # 的部署」。两者缺一不可，理由逐字写在 ``continuation.py`` 的模块 docstring。
    wakeups = default_wakeup_registry(settings)

    # 作业记录的工厂。**一处写、两处用**（``get_job`` 那五条工具与这一条状态页）：
    # 两处各写一行 ``lambda: SqliteJobStore(...)`` 确实等价，但让它们等价这件事
    # 只应该说一遍 —— 那一行将来被改错时，状态页会说「一件都不欠」而 ``get_job``
    # 读得出作业，两份回执各自都正常，问题只在没人能把它们对起来。
    def jobs() -> JobStore:
        return SqliteJobStore(settings.database_path)

    continuation = continuation_handlers(
        settings=settings,
        jobs=jobs,
        registry=wakeups,
        # 与 ``product_loop`` / ``review`` 收到的是**同一个**工厂：控制器读的是
        # 那一份场景的放行状态（暂停挡着哪几件），而这一页若读另一份，就会出现
        # 「状态页说没有暂停挡着、控制器说场景暂停」。
        scenes=scenes,
    )
    # §B15 的访问尝试账读面。**只有一条工具，而它只读** —— 与上面那条状态页同一条：
    # 成员关系不随部署变（接了就有一条，没接就是空表），所以它也不需要 ``unavailable``
    # 那样的第二份。
    #
    # 这里交出去的必须是**工厂**，不是一本建好的账：``SqliteFetchAttemptStore.__init__``
    # 会 ``ensure_schema``，也就是**会建库**，而 ``_compose`` 的契约是「构造服务面需要的
    # 一切，不绑端口、不建运行目录」—— 自检走的就是这一条路。工厂挡住组合期，处理器里
    # 那道 ``database_path.is_file()`` 挡住「还没写过东西的部署」；两者缺一不可，理由逐字
    # 写在 ``fetch_attempt.py`` 的模块 docstring。
    #
    # 而它与**生产端**（``default_http`` / ``default_acquisition`` 收到的那个）是
    # **同一份** ``default_attempt_store(settings)``：读面若指向另一个库，写进去的那一行
    # 与读出来的那一行会来自两个地方，而两边各自都正常 —— 那种红会指向这一条工具，
    # 唯独不指向这处接线。
    attempts = fetch_attempt_handlers(
        settings=settings, attempts=default_attempt_store(settings)
    )
    # §8/§9 的对账面。**先把它提成一个具名工厂，再两处引用同一个对象** —— 与
    # ``attempts`` 上面那条逐字同一条理由：这一页读的账本与 ``get_job`` /
    # ``get_assignment`` 报用量时读的必须是同一本，而两处各写一句
    # ``lambda: SqliteBudgetLedger(...)`` 效果一样、读起来却像两本。工厂**惰性**
    # 是承重的：``SqliteBudgetLedger`` 一构造就 ``ensure_schema``，也就是会建库，
    # 而 ``_compose`` 的契约是「构造服务面需要的一切，不绑端口、不建运行目录」。
    budget_ledger = partial(SqliteBudgetLedger, settings.database_path)
    reservations = budget_handlers(settings=settings, ledger_factory=budget_ledger)
    # §B17 的 Run 受理面（切片 m1）。两样东西各由一个**工厂**交出去，而它们
    # 必须指向**同一个** ``settings.database_path``：
    #
    #   * ``scenes`` 是上面那一份 ``default_scene_store(settings)``（不是另建
    #     一句同形的 lambda）。受理要读的是**盘上那个场景的生效版本**，而
    #     ``get_scene`` 读的是同一个库里的状态列。
    #   * ``runs`` 是 ``default_run_store(settings)``，那一行 Run 落在这个库里。
    #
    # 两个库时，两边各自都正常，而写下去那一行的 ``workspace`` 会指向一个读不到
    # 的场景 —— 受理成功了，而它圈的范围此后没有任何一处答得出来。理由逐字写在
    # ``server.py`` 那条 ``runs:`` 参数上。
    #
    # 工厂而不是实例，理由与 ``attempts`` / ``budget_ledger`` 两处逐字相同：
    # ``SqliteRunStore.__init__`` 会 ``ensure_schema``，也就是**会建库**，而
    # ``_compose`` 的契约是「构造服务面需要的一切，不绑端口、不建运行目录」——
    # 自检走的就是这一条路。
    # ``maintenance`` 是上面那**同一个**工厂（:490 那一行），理由与 ``scenes``
    # 逐字同款，而它在这里多一层：``get_run_increment`` 读的观测必须是产品环那三条
    # 维护工具**写下去**的同一本账。各建一份的形态是「维护 tick 记了观测、增量面
    # 永远读不到」，而那两边的报告各自都正常 —— 一边说「这一轮全都要重取」，一边说
    # 「刚刚复核过」。
    # ``ledger`` 也是那**同一个** ``budget_ledger``（:673 那一行），理由同上加一层：
    # 条件 4 的「达累计预算」读的必须是**执行那一轮时扣款的那本账**。另建一句同形的
    # ``lambda: SqliteBudgetLedger(...)`` 在今天效果一样（同一个 ``database_path``），
    # 但它读起来像两本，而「像两本」的形态一旦哪天真成了两本，累计预算那一格会永远
    # 读到 0 —— 报告上它只是「预算还够」。
    runs = run_handlers(
        settings=settings,
        scenes=scenes,
        maintenance=maintenance,
        runs=default_run_store(settings),
        ledger=budget_ledger,
        # 上面那**同一个**池子闭包，第三个消费者：结束面按业务键逐条问「这一行在不在」
        # （§B17 条件 2 的方向一）。同一个 ``Settings.database_path`` 上的两个闭包其实
        # 等价，但**让它们等价这件事只应该有一个地方说** —— 两处各写一行、将来其中一行
        # 被改错时，「折进去了」与「池子里没有它」会指着两个库，而两边的回执都正常。
        assets=assets,
        # 上面那**同一个** ``action_items`` 闭包，第二个消费者 —— 结束面按业务键把这一
        # 轮开出来的待办写回 ``actions`` 那一格（§B24）。它与 ``followups_handlers``
        # 的 ``items`` 是同一份，理由与 ``assets`` 逐字同款。
        followups=action_items,
        # §B17 条件 5（切片 f）多出来的两条，与产品环那三处用的是**同一对**闭包：
        # ``submit_run`` 在 ``execution_mode='watch'`` 上要按 ``name`` / ``cohort``
        # 取出一批主体（那份到期判定是关于某一批主体说的）。两条都是惰性的，所以
        # 自检那一条路照样不建库。
        accounts=accounts,
        cohorts=cohorts,
    )
    # §B24 视图与跟进那八条（切片 n3）。两族各是一张**已经构造好的**表，成员关系
    # 不随部署变，所以它们与 ``runs`` / ``feedback`` 同一条：组合根交回 ``None`` 时
    # 落回 stub，``get_capabilities`` 的 ``wired`` 那一格如实报它们不在 ——
    # **不要给它们一个恒回空列表的替身**（一份「这个工作区还没有存过视图」与一份
    # 「这一族没接线」在报告上同形，而处置相反）。
    #
    # ``saved_views`` 先提成一个**具名工厂**，再两处引用同一个对象 —— 与
    # ``attempts`` / ``budget_ledger`` 两处逐字同一条理由：这两族共用一格，是因为
    # 「订阅哪一版」与「跑哪一版」是同一行。两处各写一句
    # ``default_saved_view_store(settings)`` 效果一样（同一个 ``database_path``），
    # 但读起来像两份；而「像两份」的形态一旦哪天真成了两份，一次 run 说「跑的是
    # 第 3 版」与监控按另一个库里的第 3 版到期会同时成立，两个回执各自都正常。
    #
    # 工厂而不是实例，理由与 ``attempts`` / ``budget_ledger`` / ``runs`` 三处逐字
    # 相同：``SqliteSavedViewStore.__init__`` 会 ``ensure_schema``，也就是**会建库**，
    # 而 ``_compose`` 的契约是「构造服务面需要的一切，不绑端口、不建运行目录」——
    # 自检走的就是这一条路。
    saved_views = default_saved_view_store(settings)
    views = views_handlers(
        settings=settings,
        views=saved_views,
        # 上面那**同一个**池子闭包，第四个消费者：跑一份视图要先按它的查询取出一批
        # 主体。同一个 ``Settings.database_path`` 上的两个闭包其实等价，但**让它们
        # 等价这件事只应该有一个地方说** —— 理由与 ``runs`` / ``outreach`` 两处逐字
        # 相同。
        pool=accounts,
    )
    # 监控与跟进那四条收五样，四样与别处同源：
    #
    #   * ``scenes`` 是产品环那**同一个**工厂 —— 「这条监控挂在哪个场景上」。
    #   * ``ledger_factory`` 是 ``budget_ledger`` —— 启用一条监控是一次**要预算的
    #     承诺**，而承诺写进哪一本账、对账面从哪一本读，是同一本。理由逐字写在
    #     ``server.py`` 的 ``followups:`` 那一段。
    #   * ``views`` 是上面那**同一份** ``saved_views``。
    #
    # 剩下两格（``watches`` / ``items``）是本族独有的两个名词 —— 声明 / 订阅 /
    # 待办三个词各指一件东西，合成一个存储会让「这条监控还在不在」与「这件待办
    # 结没结」在报告上同形。
    followups = followups_handlers(
        settings=settings,
        scenes=scenes,
        views=saved_views,
        watches=default_watch_store(settings),
        items=action_items,
        ledger_factory=budget_ledger,
    )
    # §B25 调度与通知那八条（切片 F）。这一族比前面每一族都**多一样东西**，而多出来
    # 的那一样不是存储：``scheduling_handlers`` 还收一个 ``action`` —— 到点了要做的那
    # 一件事。``server.py`` 的 ``scheduling:`` 那一段逐字写下了这一格的接线条件
    # （**照抄，不是转述**）：
    #
    #     **不要为了「看起来接上了」给它一个恒回 ``UNRESOLVED`` 或恒回 ``SKIPPED``
    #     的替身。** …… 等受理面落地、真的 ``ScheduleAction`` 接上时，这一格与它
    #     一起接 —— 两件事分两次做会造出一个「接了但一次研究都开不出来」的中间态，
    #     而那个中间态在 ``tools/list`` 上与接好的形态逐字同形。
    #
    # 那个条件**今天成立了**，而且不是按报告成立的：受理面（§B01/B02）已随
    # ``860ed79`` 落地，而 ``ResearchScheduleAction`` 已经用**本组合根这一批**工厂
    # 端到端跑通过一次 —— ``interfaces/trigger_driver.py`` 真的点出一行 Run。
    #
    # 七样**与别处同源**，逐条都有理由，理由与 ``workstream`` 那十样同一条
    # （``sqlite3`` 的连接不能跨线程，所以收工厂不收实例）：
    #
    #   * ``scenes`` 是产品环那**同一个**工厂 —— 动作读的是「某一版已批准的
    #     Scene」，而那个「批准」是产品环写下去的。另建一句的形态是「这条调度按
    #     另一库里的那一版开跑」，而两个回执各自都正常。
    #   * ``runs`` 是 ``default_run_store(settings)`` —— 那一轮 Run 落在哪个库。
    #   * ``maintenance`` 是上面那**同一份** —— 点火会留维护账。
    #   * ``ledger`` 是 ``budget_ledger`` —— 一轮要花预算，而「扣了哪一本账」与
    #     对账面读的必须是同一本（同 ``followups`` 那一格 ``ledger_factory``）。
    #   * ``accounts`` / ``cohorts`` 是上面那**同一对** —— 一轮研究按哪个池子取主体。
    #
    # ``sinks`` **不在这里**，而这是一个决定（逐字写在 ``scheduling_handlers`` 的
    # docstring 里）：八条工具里没有一条投递，投递是
    # :func:`~marketops.application.scheduling.tick` 那一半的事，而 ``tick`` 是驱动
    # 进程调的一步、**不是工具**。把 sink 表接进这一个工厂的收场是「它被悄悄忽略」
    # 或「某一条工具顺手投一次」，两种都坏。sink 表的接线点在驱动那一侧。
    schedule_action = ResearchScheduleAction(
        settings=settings,
        scenes=scenes,
        runs=default_run_store(settings),
        maintenance=maintenance,
        ledger=budget_ledger,
        accounts=accounts,
        cohorts=cohorts,
    )
    scheduling = scheduling_handlers(
        settings=settings,
        schedules=default_schedule_store(settings),
        notifications=default_notification_store(settings),
        bindings=default_session_binding_store(settings),
        action=schedule_action,
    )
    # §B01/B02 的启动面 / 有效场景面 / 撤回面（切片 B01B02）。三族各是一张**已经
    # 构造好的**表，成员关系不随部署变，所以它们与 ``runs`` / ``views`` /
    # ``followups`` 同一条：组合根交回 ``None`` 时落回 stub，``get_capabilities``
    # 的 ``wired`` 那一格如实报它们不在。
    #
    # ``effective_scene_store`` 先提成一个**具名工厂**，再两处引用同一个对象 ——
    # 这一段是按**对象同一性**承重的，不是「两句同形的调用也算」：启动面在
    # ``freeze()`` 里写它，有效场景面在 ``get_effective_scene`` 里读它，两边都要
    # 拿到同一份。两处各写一句 ``default_effective_scene_store(settings)`` 在今天
    # 效果一样（同一个 ``database_path``），但读起来像两份；而「像两份」的形态
    # 一旦哪天真成了两份，预览给你看的那一版与启动落库的那一版会是两份不同的
    # 东西，而两份都自洽 —— 判据都只是「没有这一版」，于是「刚冻结的那一版读不
    # 出来」与「它从来没被冻结过」同形。理由逐字写在 ``server.py`` 的
    # ``workstream:`` 那一段。
    #
    # 工厂而不是实例，理由与 ``attempts`` / ``budget_ledger`` / ``runs`` 三处逐字
    # 相同：``SqliteEffectiveSceneStore.__init__`` 会 ``ensure_schema``，也就是
    # **会建库**，而 ``_compose`` 的契约是「构造服务面需要的一切，不绑端口、不建
    # 运行目录」—— 自检走的就是这一条路。
    effective_scene_store = default_effective_scene_store(settings)
    effective_scene = effective_scene_handlers(
        settings=settings,
        # 产品环那**同一个**工厂：有效场景是按某一版已批准的 Scene 编译出来的。
        scenes=scenes,
        catalogs=default_product_catalog_store(settings),
        effective=effective_scene_store,
    )
    # 启动面那四条收十样，其中八样与别处**同源**，逐条都有理由：
    #
    #   * ``scenes`` 是产品环那**同一个**工厂 —— 它绑的是「某一版已批准的
    #     Scene」，而那个「批准」是产品环写下去的。
    #   * ``catalogs`` 与登记面 / 查缺面 / 启动预览面逐字同形（同一个
    #     ``default_product_catalog_store``）。
    #   * ``runs`` / ``maintenance`` / ``ledger`` / ``accounts`` / ``cohorts`` 与
    #     ``run_handlers`` 收到的是**同一组**闭包：``start_workstream`` 是唯一一条
    #     会自己开一轮 Run 的工具，而它走的是 ``application/run_admission.py``
    #     那条既有受理路径（``workstream_handlers`` 自己 import 它，这里没有注入
    #     点）—— 那一轮 Run 落在哪个库、预算从哪本账扣、主体从哪个池子取，必须与
    #     ``submit_run`` 逐字同源。
    #
    # ``effective`` 是上面那**同一个** ``effective_scene_store``，不是另建一句。
    workstream = workstream_handlers(
        settings=settings,
        scenes=scenes,
        catalogs=default_product_catalog_store(settings),
        workstreams=default_workstream_store(settings),
        effective=effective_scene_store,
        runs=default_run_store(settings),
        maintenance=maintenance,
        ledger=budget_ledger,
        accounts=accounts,
        cohorts=cohorts,
    )
    # 撤回面那一条收两样，而它们答的是**两个不同的问题**：``claims`` 答
    # 「``claim_id`` 指的是哪一条已批准说法」（读面），``withdrawals`` 答「这一家
    # 已经撤过哪几版」（撤回账）。合成一格会让「这条说法不存在」与「这一版从来
    # 没撤过」在报告上同形，而处置相反 —— 理由逐字写在 ``server.py`` 的
    # ``claim_withdrawal:`` 那一段。
    claim_withdrawal = claim_withdrawal_handlers(
        settings=settings,
        claims=default_outreach_store(settings),
        withdrawals=default_claim_withdrawal_store(settings),
    )
    ring, blocker = _load_ring(settings)
    return _Composition(
        settings=settings,
        host=host,
        ring=ring,
        blocker=blocker,
        # 状态库那一道闸。放在最后读、但**在 `_serve()` 里排在 `ensure_data_dirs()`
        # 之前用**：那个调用会把这个库的父目录建出来，而这次探问的价值恰恰在于
        # 「目录还没准备好」这件事被如实报出来、而不是被顺手修掉。
        # 它**不抛**（打不开、读不出来、版本对不上都是结论），所以这句放进
        # `_compose` 不会让自检在故障路径上变成一堆堆栈。
        database=read_database_readiness(settings.database_path),
        server=build_server(
            settings=settings,
            tokens=ring,
            research=research,
            product_loop=product_loop,
            # B01 的登记面（三条）。与 ``review_tools`` / ``runs`` 同一条：一份处理器
            # 表，成员关系不随部署变 —— 上面那一段写了它与 ``default_product_catalog_store``
            # 的关系。
            product_catalog=product_catalog,
            # B01 的查缺面（两条只读工具）。与登记面分开传的理由写在 ``server.py``
            # 的 ``product_readiness:`` 那一段：两族的接线条件不同（查缺面还要说法
            # 的读面）。
            product_readiness=product_readiness,
            # B01 的启动预览面（一条只读工具）。与查缺面分开传的理由写在 ``server.py``
            # 的 ``launch:`` 那一段：两族的接线条件不同（预览还要场景存储）。
            launch=launch,
            outreach=outreach,
            review=review_tools,
            review_store=review,
            # §B21 的续跑状态页。与 ``review_tools`` 同一条：一份处理器表，
            # 成员关系不随部署变。
            continuation=continuation,
            # §B15 的访问尝试账读面。与 ``review_tools`` / ``continuation`` 同一条：
            # 一份处理器表，成员关系不随部署变。
            attempts=attempts,
            # §8/§9 的对账面。同上一条。
            reservations=reservations,
            # §B17 的 Run 受理面（切片 m1）。与 ``review_tools`` / ``continuation``
            # / ``attempts`` 同一条：一份处理器表，成员关系不随部署变。而它与上面
            # 那一段里的 ``scenes`` **是同一个对象** —— 见那一处的注释。
            runs=runs,
            # §B23 反馈环那八条工具。与 ``runs`` / ``review_tools`` 同一条：一份
            # 处理器表，成员关系不随部署变。上面那一段写了它的三样工厂为什么必须
            # 与这里那几个同源。
            feedback=feedback,
            # §B24 视图与跟进那两族（切片 n3）。与 ``runs`` / ``feedback`` 同一条：
            # 两份处理器表，成员关系不随部署变。上面那一段写了 ``saved_views``
            # 为什么要先提成具名工厂、以及它为什么必须同时交给这两族。
            views=views,
            followups=followups,
            # §B25 调度与通知那八条（切片 F）。与 ``views`` / ``followups`` /
            # ``workstream`` 同一条：一份已经构造好的表，成员关系不随部署变 —— 接了
            # 就是八条，没接就是空表。上面那一段写了它多出来的那一样（``action``）
            # 为什么必须是真的、以及为什么 ``sinks`` 不在这条路上。
            scheduling=scheduling,
            # §B01/B02 的三族（切片 B01B02）。与 ``runs`` / ``views`` /
            # ``followups`` 同一条：三张已经构造好的表，成员关系不随部署变。
            # 上面那一段写了 ``workstream`` 那十样为什么必须与 ``run_handlers``
            # 同源、以及 ``effective_scene`` 与它为什么共用一个
            # ``effective_scene_store``（那不是顺手 —— 是这一族承重的一条）。
            workstream=workstream,
            effective_scene=effective_scene,
            claim_withdrawal=claim_withdrawal,
            unavailable=unavailable,
            scenes=scenes,
            decisions=decisions,
            advisory=advisory,
            quay=quay,
            # 与上面 ``continuation_handlers`` 收到的是**同一个**（见那一处的理由）。
            jobs=jobs,
            # 与上面 ``budget_handlers`` 收到的是**同一个对象**（不是另一句同形的
            # lambda）：对账面读的账本与这里报用量时读的必须是同一本，见那一处的注释。
            ledger_factory=budget_ledger,
            # §3 里 ``submit_findings`` 那一条多要的三样：责任结论落表时的场地与
            # 关系两个 store，以及**核对那一次判定**用的台账 —— 最后一个与研究侧
            # ``decide`` 是**同一个工厂**，理由写在 ``wire_decisions`` 的返回值上。
            assets=assets,
            relation_store=relations,
            records=decision_records,
            # 维护账，与上面 ``product_loop`` 收到的**是同一份**：研究侧
            # ``submit_findings`` 写进去的变更信号，正是产品环那三条维护工具读的
            # 那本账。两处各建一份的形态是「结算写了信号、维护 tick 永远看不见」。
            maintenance=maintenance,
            # 账号池与冻结成员名单，与上面 ``product_loop`` / ``outreach`` 收到的
            # **是同一组**（见那两处的理由）。控制面要它们只有一条路径：
            # ``submit_research(input_kind="cohort")``（§B12 的实现逐字要求「研究
            # 接受 Scene selector 或 durable cohort 引用」）。缺了这两格时那一类
            # 输入如实报「本部署没有接这两样」，而不是把一份冻好的名单读成空集。
            accounts=accounts,
            cohorts=cohorts,
            # 外部那本退出账的读器（§1）。注入的是一个**只读**的闭包 —— 报告路径
            # 不因此拿到任何写外部的权力。读不出来的情形由调用点如实报，不在这里
            # 兜底成空元组：那会让「外面判了没落地」与「外面什么都没说」同形。
            worker_outcomes=lambda task_id: read_worker_outcomes(settings.quay_root, task_id),
        ),
    )


def _serve() -> int:
    try:
        settings = load_settings()
        composed = _compose(settings)
    except ConfigError as exc:
        print(f"[marketops-mcp] 起不来：{exc}", file=sys.stderr)
        return _EXIT_CANNOT_START

    if not composed.database.startable:
        # 起不来，而且**在 `ensure_data_dirs()` 之前**停：那个调用会把 state/ 建出来，
        # 于是「目录还没准备好」这种故障会被这一次启动顺手修掉一半，下一次的读数就
        # 不再是它当初的样子。反过来，这个库打不开时把目录建出来也没有任何用处 ——
        # 这一版代码不会去动它。
        #
        # 说的那句话与 `--check` 的是**同一句**（`db.read_schema_state` 那一处措辞），
        # 含它点名的那条命令。这一层不自己拼一遍：两处各拼一遍，就会出现「自检说要跑
        # A、启动时说要跑 B」，而工单里贴的永远是先跑到的那句。
        print(f"[marketops-mcp] 起不来：{composed.database.render()}", file=sys.stderr)
        return _EXIT_CANNOT_START

    if composed.ring is None:
        # 启动，但把「谁都不许」说到耳朵边上。`--check` 已经返回非零，所以
        # 这不是它第一次被说出来；重复一次是因为这条日志会被留在服务器上，
        # 而 `--check` 的输出通常留在某个终端里。
        print(f"[marketops-mcp] 警告：{composed.blocker}", file=sys.stderr)

    settings.ensure_data_dirs()
    # §15(b)：**在开始服务之前**把自己加载的构建报到这个数据根上。worker 在派发
    # 之前会读它，用来核对两侧加载的是不是同一份代码（`adapters/server_build.py`）。
    #
    # 顺序是承重的：写在 `run()` 之前，所以「文件在」这件事的含义就是「服务报过一次
    # 自己的构建」。反过来写（先服务、后落盘）会有一个窗口，其间服务已经接得住调用
    # 而 worker 会因为读不到文件停下 —— 那时运维看到的是一句「还没有服务报过构建」，
    # 而服务明明在跑。
    #
    # 写不出来就退 2，不退 1：这不是「起来但谁都不许」，是**起不来**。理由在
    # `publish_server_build` 的 Raises 里 —— 写不出来之后每一个 worker 都会以
    # 「这个部署上还没有服务报过构建」停下，而那一句话会把一个磁盘问题说成一个
    # 部署事实，正是本仓最不想要的那种报告。
    try:
        published = publish_server_build(settings.data_root)
    except OSError as exc:
        print(
            f"[marketops-mcp] 起不来：构建回执写不出来"
            f"（{server_build_path(settings.data_root)}）："
            f"{type(exc).__name__}: {exc}\n"
            "服务不会在「worker 读不到它的构建」的状态下开始服务：那时每一轮派发都会以"
            "「这个部署上还没有服务报过构建」停下，而那句话说的是部署，不是这里的磁盘。",
            file=sys.stderr,
        )
        return _EXIT_CANNOT_START
    print(f"[marketops-mcp] 构建回执：{published}", file=sys.stderr)
    print(
        f"[marketops-mcp] 绑定 http://{composed.host}:{settings.mcp_port}/mcp（Streamable HTTP）",
        file=sys.stderr,
    )
    composed.server.run(
        transport="streamable-http",
        host=composed.host,
        port=settings.mcp_port,
        transport_security=loopback_transport_security(composed.host),
    )
    return _EXIT_OK


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m marketops.interfaces.mcp",
        description="MarketOps MCP 服务面（Streamable HTTP，只绑环回地址）。",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="只做启动前自检并把结果打成 JSON，不绑端口、不建运行目录。",
    )
    args = parser.parse_args(argv)
    return _check() if args.check else _serve()


if __name__ == "__main__":
    raise SystemExit(main())
