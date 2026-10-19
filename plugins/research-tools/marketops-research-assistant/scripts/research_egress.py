#!/usr/bin/env python3
"""受管出口的**失败关闭**入口：本地浏览器与辅助 HTTP 的唯一开工方式。

契约位置：`INTERFACE-CONTRACT.md` 里 W-A SECURITY 的交付物；装什么由
`DECISION-01`（默认口径「vendoring + hash 钉死 + 单一维护源」）决定。

## 它替代的是什么

``research_provider_extensions.py::_crawl4ai_local`` 今天直接
``AsyncWebCrawler().arun(url)``：浏览器自己解析、自己跟随重定向、自己发 iframe /
worker / 子资源，而**没有任何出口**。同一个文件里的 ``_public_https_url`` 只判字面量
（协议、端口、``localhost`` 后缀、是不是 IP），既没有真的解析一次，也没有把连接钉在
解析出来的地址上，更管不到重定向与子资源 —— 而浏览器一旦起来，后面那些请求根本不
经过它。

本模块是那条路的**替代**，不是它旁边的一个可选加强：调用方要么拿到一个已经起来的
受管出口，要么拿到一个理由码。**没有第三条路** —— 一个「出口起不来就先直连吧」的
分支就是这条链存在的理由被抹掉的那个失败。

## 上层 API（W-E / W-G / INTEGRATOR 要接的就是这几个名字）

.. code-block:: python

    from research_egress import safe_browser_egress, open_session, reason_code

    # ① 浏览器那一跳：出口起不来就抛，绝不返回一个「配了但没接上」的配置
    with safe_browser_egress() as session:
        kwargs = session.browser_config_kwargs()
        async with AsyncWebCrawler(config=BrowserConfig(**kwargs, headless=True)) as crawler:
            result = await crawler.arun(url=url, config=run_config)
        egress_ref = session.browser_egress_ref()      # → EvidenceEnvelope.egress_decision_ref

    # ② 辅助 HTTP（robots / sitemap / 深爬 / CSS 抽取）：也过同一个出口
    with open_session() as session:
        fetched = session.fetch_auxiliary("https://example.com/robots.txt", depth=0)
        if not fetched.allowed:
            raise ValueError(reason_code(fetched))
        rules = fetched.response.text
        # fetched.decision_ref → EvidenceEnvelope.egress_decision_ref

    # ③ 单独问一句「现在能不能安全地开本地浏览器」
    ready, cause = local_crawl_ready()

``SAFE_EGRESS_API_VERSION`` 是这几个名字的**形状**版本：加一个参数、换一个返回值
才变；实现内部变化不变。

## 三件它替调用方决定的事

1. **出口必须是起来的。** :meth:`SafeEgressSession.browser_config_kwargs` 在代理没有
   监听时**抛** —— 一个「还没监听就先给出地址」的配置会让浏览器指向一个没人听的端口，
   而那个失败在浏览器那里长得像一次网络错误（核心 ``egress_proxy`` 的 ``url`` 属性上
   写着同一条）。
2. **范围闸永远装着。** 本模块**从不**构造 ``allowlist=None`` 的判据（那是「这一层
   没有范围闸」那一支），空名单就是**谁都不许**；这一条由
   ``tests/test_wave1_egress_entry.py`` 执行。名单来自
   ``MARKETOPS_FETCH_ALLOWED_DOMAINS``，解析走 ``security/scope.py``（写坏的条目
   当场抛，不留到下一个人）。
3. **深爬有界。** :meth:`SafeEgressSession.claim_page` 记下去重的页面数并判深度 ——
   seeder 与深爬「下一批抓哪些」由它回答，而不是由上游库的缺省值回答。
4. **端口也是判据。** 缺省只放 80 / 443（:data:`DEFAULT_EGRESS_PORTS`）——
   一个被批准的主机名不等于它的每一个端口都被批准。要非标准端口，显式传
   ``allowed_ports``；本模块**不**读环境变量放宽它。

## 它**没有**宣称做到的事

* 出口放行 **不等于** 内容可信。TLS 是端到端握手的隧道，本模块看不见隧道里的正文
  有没有 prompt injection。正文可信那件事的判据在别处（``domain/access.py`` 与 §4）。
  这一句是 P1-01 明令必须显式声明的。
* 一次被拒的取回**不得**落成一份业务证据。:class:`GuardedFetch` 在被拒时没有正文
  （``response`` 恒为 ``None``，由它自己的 ``__post_init__`` 保证），所以调用方拿不到
  一份「看起来来路清白」的材料。
* **Windows 上的实测未做**（本机 Linux）。本模块没有平台分支，但「没有分支」不等于
  「测过」—— 见状态文件的 SKIP 记录。
"""

from __future__ import annotations

import argparse
import os
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Iterator
from urllib.parse import urlsplit

import marketops_safe_egress as safe

SAFE_EGRESS_API_VERSION = "marketops.safe-egress-api.v2"

#: 从环境变量读批准名单的那一格。与 ``build_client_bundles.py`` 声明的变量名一致，
#: 而**不是**在这里另起一个 —— 两个名字会让客户机上「我配了它」与「它读了它」变成
#: 两件不同的事。
ALLOWED_DOMAINS_ENV = "MARKETOPS_FETCH_ALLOWED_DOMAINS"

DEFAULT_CRAWL_SCOPE_DEPTH = 2
DEFAULT_CRAWL_SCOPE_PAGES = 16

#: 这一层允许的端口。**缺省只有两个 web 端口**（与
#: ``security.network.DEFAULT_EGRESS_PORTS`` 是同一个值，不另写一份）。
#:
#: 采集设计 §7 把「端口」与协议、解析、私网网段、来源策略并列：一个被批准的
#: **主机名**不等于它的**每一个端口**都被批准。少了这一格，页面可以把浏览器当成
#: 端口扫描器去敲 ``approved.example:6379``，而出口上留下的是一条放行记录。
#:
#: 需要非标准端口的部署**显式**传一个集合给 ``open_session`` / ``safe_browser_egress``
#: —— 刻意不做一个环境变量开关：一个默认放宽的口子会在没人看的时候变成常开的旁路。
#: 切片**应当**带着端口策略（``security.network.DEFAULT_EGRESS_PORTS``）。用
#: ``getattr`` 取它，是因为「**模块对、切片旧**」这种半截集成必须**import 得起来**：
#: 一个在家引处炸掉的模块会让适配器的 ``_import_guard`` 拿到 ``AttributeError``
#: （它只捕 ``ImportError``），于是调用方看到的是一次崩溃，而不是一句理由码。
#: 真正的失败关闭在 :func:`approved_url` 里 —— 切片没有端口策略时它**拒**。
DEFAULT_EGRESS_PORTS = getattr(safe, "DEFAULT_EGRESS_PORTS", frozenset({80, 443}))

#: 这一份切片有没有端口判据。没有时 :func:`approved_url` 只能拒 —— 它不能自己
#: 实现一遍「端口允不允许」，那就是本模块存在的理由被绕开的样子。
_SLICE_HAS_PORT_POLICY = hasattr(safe, "check_port") and hasattr(safe, "DEFAULT_EGRESS_PORTS")

#: :class:`EgressDenied` 可能带上的全部理由码。**闭集**：它们会被写进回执与日志，
#: 而下游按取值分支 —— 一个拼错的名字会静静开出一条新类别，落进所有 ``else`` 里。
REASONS: frozenset[str] = frozenset(
    {
        "LOCAL_CRAWL_NOT_SAFE_TO_RUN",
        "EGRESS_PROXY_UNAVAILABLE",
        "DOMAIN_ALLOWLIST_EMPTY",
        "DOMAIN_NOT_APPROVED",
        "TARGET_REFUSED",
        "PORT_NOT_ALLOWED",
        "DNS_RESOLUTION_FAILED",
        "EGRESS_BUDGET_EXCEEDED",
        "RESPONSE_TOO_LARGE",
        "PAGE_BUDGET_EXCEEDED",
        "DEPTH_EXCEEDED",
        "TRANSPORT_FAILED",
        "REDIRECT_NOT_FOLLOWED",
        "PROXY_REFUSED",
    }
)

#: :func:`local_crawl_ready` 的第二个返回值。**与 :data:`REASONS` 是两个集合**：
#: 前者回答「现在能不能开工」，后者回答「这一次为什么没做成」。合成一个集合会让
#: ``LOCAL_CRAWL_NOT_SAFE_TO_RUN``（一个结论）与 ``CRAWL4AI_UNAVAILABLE``（一个成因）
#: 在同一张表里争同一个位置。
READINESS_CAUSES: frozenset[str] = frozenset(
    {
        "READY",
        "DOMAIN_ALLOWLIST_EMPTY",
        "CRAWL4AI_UNAVAILABLE",
        "EGRESS_PROXY_UNAVAILABLE",
    }
)

#: 契约 §5.3 的理由码闭集里与出口有关的那几个。**本模块不新增契约码** ——
#: 一个本地码要成为 ``PolicyDecision.reason_code``，只能落在下面这三格里。
CONTRACT_EGRESS_REASONS: frozenset[str] = frozenset(
    {
        "EGRESS_REFUSED",
        "POLICY_DOMAIN_NOT_ALLOWED",
        "CRAWL4AI_LOCAL_NOT_INSTALLED",
    }
)

#: 本地码 → 契约码的**折叠**。多对一是刻意的：出口这一层的判断比契约那一层的
#: 理由码细（一个要说清「该去找谁」），而契约那一层要的是可审计的类别。
#: 折叠之后**不再细分**，所以每一格都要写明它为什么落在这里。
CONTRACT_FOLD: dict[str, str] = {
    # 出口不存在 / 起不来 —— 本地根本没有可跑的受管路径。
    "LOCAL_CRAWL_NOT_SAFE_TO_RUN": "CRAWL4AI_LOCAL_NOT_INSTALLED",
    "EGRESS_PROXY_UNAVAILABLE": "CRAWL4AI_LOCAL_NOT_INSTALLED",
    # 范围那一路：域名不在批准集合里，或者根本没配。
    "DOMAIN_ALLOWLIST_EMPTY": "POLICY_DOMAIN_NOT_ALLOWED",
    "DOMAIN_NOT_APPROVED": "POLICY_DOMAIN_NOT_ALLOWED",
    # 其余全部是「这一跳被出口拒了」的细分：地址不安全、解析失败、预算、
    # 不跟随重定向、出口自己拒绝、传输坏掉 —— 契约那一层对它们的处置是同一条
    # （这一跳没有产出证据），而细的那一格留在本地的 ``detail`` 里。
    "TARGET_REFUSED": "EGRESS_REFUSED",
    "PORT_NOT_ALLOWED": "EGRESS_REFUSED",
    "DNS_RESOLUTION_FAILED": "EGRESS_REFUSED",
    "EGRESS_BUDGET_EXCEEDED": "EGRESS_REFUSED",
    "RESPONSE_TOO_LARGE": "EGRESS_REFUSED",
    "PAGE_BUDGET_EXCEEDED": "EGRESS_REFUSED",
    "DEPTH_EXCEEDED": "EGRESS_REFUSED",
    "TRANSPORT_FAILED": "EGRESS_REFUSED",
    "REDIRECT_NOT_FOLLOWED": "EGRESS_REFUSED",
    "PROXY_REFUSED": "EGRESS_REFUSED",
}


class EgressDenied(Exception):
    """这一条链**没有**按请求发生，而且不会退化成一个不受管的方式。"""

    def __init__(self, code: str, detail: str) -> None:
        if code not in REASONS:
            raise ValueError(f"{code!r} 不在理由码闭集里 —— 新类别要先加进 REASONS")
        super().__init__(f"{code}: {detail}")
        self.code = code
        self.detail = detail


@dataclass(frozen=True)
class CrawlScope:
    """深爬/种子的范围。**与 HTTP 预算分开**：一个说「这一次爬得多深、铺多宽」，
    另一个说「这一次发了多少个请求、读了多少字节」。两者的处置不同 —— 前者改的是
    这一次的研究计划，后者改的是配置里的上限。"""

    max_depth: int = DEFAULT_CRAWL_SCOPE_DEPTH
    max_pages: int = DEFAULT_CRAWL_SCOPE_PAGES

    def __post_init__(self) -> None:
        if self.max_depth < 0:
            raise ValueError("max_depth 不得为负")
        if self.max_pages <= 0:
            raise ValueError(
                "max_pages 必须为正 —— 一个 0 的页数上限会让每一次辅助取回都失败，"
                "而那个失败看起来像一次网络问题"
            )


def allowed_domains_from_env(value: str | None = None) -> frozenset[str]:
    """读 ``MARKETOPS_FETCH_ALLOWED_DOMAINS``。**写坏的条目当场抛。**"""
    raw = os.environ.get(ALLOWED_DOMAINS_ENV, "") if value is None else value
    return safe.parse_domain_allowlist(raw)


def crawl4ai_ready() -> bool:
    """本机能不能 import crawl4ai。**不启动浏览器，不读凭据。**"""
    try:
        import crawl4ai  # noqa: F401
    except Exception:  # noqa: BLE001 - 任何 import 期失败都等价于「不可用」
        return False
    return True


def _probe_egress(allowlist: frozenset[str]) -> str:
    """起一次真的出口再停掉。**返回成因码**（``READY`` 表示起来了）。

    一个只检查「EgressProxy 类 import 得到」的实现会让绑定失败（回环不可用、
    端口耗尽）在第一次真抓取时才现形，而那一次会以「浏览器网络错误」的样子出现 ——
    排查方向直接错掉。

    探针的判据**也装着范围闸**：一个不带名单的探针会证明一件与真正要用的东西不同的事。
    """
    try:
        probe = safe.EgressProxy(
            gate=safe.EgressGate(allowlist=allowlist, allowed_ports=DEFAULT_EGRESS_PORTS)
        )
        probe.start()
        try:
            _ = probe.url
        finally:
            probe.stop()
    except (OSError, ValueError, RuntimeError):
        return "EGRESS_PROXY_UNAVAILABLE"
    return "READY"


def local_crawl_ready(allowlist: frozenset[str] | None = None) -> tuple[bool, str]:
    """现在能不能安全地开一次本地浏览器。**返回结论，不抛**。

    三条判据各自独立，缺一条就不能开：

    * 批准名单为空 —— 谁都不许（``security/scope.py``：空名单与没配是同一件事）；
    * crawl4ai 装不上 —— 没有受控的渲染路径；
    * 出口代理起不来 —— **这一条最要紧**：一个起不来的出口如果被读成「先直连吧」，
      整条链就没有任何意义。
    """
    entries = allowed_domains_from_env() if allowlist is None else allowlist
    if not entries:
        return False, "DOMAIN_ALLOWLIST_EMPTY"
    if not crawl4ai_ready():
        return False, "CRAWL4AI_UNAVAILABLE"
    cause = _probe_egress(entries)
    return (cause == "READY"), cause


#: 给**厂商侧** URL 参数用的那一格。与 ``research_official_mcp._approved_url``
#: 同一套语义、同一个理由码 —— 换成别的名字会让适配器按字符串分支的地方落进 else。
OFFICIAL_TARGET_REFUSAL = "OFFICIAL_MCP_TARGET_NOT_ALLOWED"

#: 厂商 URL 参数的解析失败码。与 ``research_target_guard.REASONS`` 同名，理由同上：
#: 一个已经在上游被写下来的理由码，不该因为换了实现就改名。
TARGET_RESOLUTION_FAILED = "TARGET_RESOLUTION_FAILED"
TARGET_RESOLVES_NON_GLOBAL = "TARGET_RESOLVES_NON_GLOBAL"

#: 厂商 URL 参数只允许这一个端口。与 ``_approved_url`` 的 ``{None, 443}`` 相同：
#: ``None`` 在 ``urlsplit`` 里就是 443，所以判据只需要放行 443 一个值。
APPROVED_URL_PORTS: frozenset[int] = frozenset({443})

#: 这一格的**唯一实现**标识。适配器与判据可以拿它对自己手上的那一份。
APPROVED_URL_SOURCE = "research_egress.approved_url"


def approved_url(value: object, *, resolve: bool = True,
                 resolver: safe.HostResolver | None = None,
                 allowlist: frozenset[str] | None = None) -> str:
    """**厂商适配器唯一该用的 URL 判据**：这个地址能不能交给上游去取。

    ## 它为什么必须在这里，而不是在适配器里

    ``research_adapter_tinyfish`` 的 ``URL_GUARD_SOURCES`` 把
    ``("research_egress", "approved_url")`` 列为**第一**来源 —— 那句话的意思是
    「单一守卫在 W-A 这一层」。而在此之前这个名字**不存在**，于是它静默地落到第二
    来源（``research_official_mcp._approved_url``），也就是**另一份实现**。
    两份实现里先被修好的那一份不会告诉另一份。

    ## 为什么它必须**就是代理用的那个判据**

    这一格不重新实现一遍规则，而是构造**浏览器与辅助 HTTP 用的同一个**
    :class:`~marketops.adapters.egress_proxy.EgressGate`（``allowlist`` 取自
    ``MARKETOPS_FETCH_ALLOWED_DOMAINS``，``allowed_ports={443}``），然后调它的
    ``check``。于是「厂商 URL 参数的判据」与「出口那一层的判据」在**代码上**是同一
    个对象、同一份规则：

    * 主机字面量（userinfo / 字面 IP / 保留名 / 数字形态）—— ``check_target``；
    * 范围（空名单 = 谁都不许）—— ``check_domain``；
    * 端口（只 443）—— ``check_port``；
    * **解析出来的每一个地址**（非全局即拒）—— ``check_resolved``，而且
      ``EgressGate`` 把放行地址一起交出来，与代理「连到判过的那个地址」是同一句话。

    一个自己写一遍 ``endswith`` / ``ipaddress`` 的守卫，与代理那一层分叉的那天不会
    有任何东西报错。

    ## ``resolve=False`` 只跳过解析

    与 ``_approved_url`` 同一条理由：解析要问一次网络，而不该在「这次调用是否被授权」
    之前问 —— 否则一个解析不出的名字会以**错误的理由**拒掉一次本来因别的原因被拒的
    调用，而操作员读到的是错的成因。这一支用**同一批纯函数**（``check_target`` /
    ``check_domain`` / ``check_port``）走完形状那三格，规则与 ``resolve=True`` 相同
    （对拍判据在 ``tests/test_wave1_egress_approved_url.py``）。

    ## 拒绝发生在**任何网络动作之前**

    本函数只做判定，不建连接、不起代理、不开浏览器。被拒的调用方拿到的是一句
    ``ValueError``（仓库声明的拒绝通道），而 ``tools/call`` / HTTP / WebSocket /
    Chromium 一次都不会发生 —— 由调用点的顺序保证（适配器在参数校验期调用它），
    并由本文件的判据钉住「拒绝时副作用面零触发」。
    """
    if not _SLICE_HAS_PORT_POLICY:
        # 半截集成：模块比切片新。**拒**，而不是退回一条更弱的规则 ——
        # 「没有端口判据」不是「端口都可以」。
        raise ValueError(
            f"{OFFICIAL_TARGET_REFUSAL}: egress slice lacks the port policy "
            "(security.network.check_port) — refusing rather than guessing"
        )
    if not isinstance(value, str) or not 1 <= len(value) <= 2000:
        raise ValueError(OFFICIAL_TARGET_REFUSAL)
    try:
        parts = urlsplit(value)
        host = safe.normalise_host(parts.hostname or "")
        port = parts.port
    except ValueError:
        raise ValueError(OFFICIAL_TARGET_REFUSAL) from None

    if parts.scheme != "https" or parts.username is not None or parts.password is not None:
        raise ValueError(OFFICIAL_TARGET_REFUSAL)
    if port not in (None, 443) or not host:
        raise ValueError(OFFICIAL_TARGET_REFUSAL)

    if not resolve:
        # 形状那三格：与 ``EgressGate`` 内部用的是同一批函数，不是另一份规则。
        literal = safe.check_target(value)
        if not literal.allowed:
            raise ValueError(OFFICIAL_TARGET_REFUSAL)
        scoped = safe.check_domain(host, allowlist=(allowed_domains_from_env()
                                                   if allowlist is None else allowlist))
        if not scoped.allowed:
            raise ValueError(OFFICIAL_TARGET_REFUSAL)
        ported = safe.check_port(443, allowed=APPROVED_URL_PORTS)
        if not ported.allowed:
            raise ValueError(OFFICIAL_TARGET_REFUSAL)
        return value

    gate = safe.EgressGate(
        resolver if resolver is not None else safe.SystemResolver(),
        allowlist=allowed_domains_from_env() if allowlist is None else allowlist,
        allowed_ports=APPROVED_URL_PORTS,
    )
    verdict = gate.check(host, 443)
    if verdict.allowed:
        return value

    refusal = verdict.refusal
    if verdict.scope_refusal is not None:
        raise ValueError(OFFICIAL_TARGET_REFUSAL)
    if refusal.startswith("dns:"):
        # 解析不出来**不是**「这个目标不被允许」：两种处置不同，理由码也不同。
        raise ValueError(f"{TARGET_RESOLUTION_FAILED}: {verdict.detail}")
    if refusal == "resolves_to_private":
        raise ValueError(f"{TARGET_RESOLVES_NON_GLOBAL}: {verdict.detail}")
    raise ValueError(OFFICIAL_TARGET_REFUSAL)


def reason_code(fetch: safe.GuardedFetch) -> str:
    """把一个 :class:`GuardedFetch` 的判决折成**本模块那一格**理由码。

    ``marketops_safe_egress`` 有四个各自闭集的理由字段（地址、范围、DNS、出口），
    因为它们各指向一个不同的处置；而插件的回执只有一格 ``reason``。折的过程因此是
    一处**政策**，它必须写在明处、只写一次，而不是散在各个调用点的 ``if`` 里。
    """
    if fetch.allowed:
        return "ok"
    if fetch.host_refusal is safe.TargetRefusal.PORT_NOT_ALLOWED:
        # 端口这一格单独出来：它要改的是**端口配置**，不是「换一个来源」。
        # 两者都折到契约的 ``EGRESS_REFUSED``，但本地这一格决定工单派给谁。
        return "PORT_NOT_ALLOWED"
    if fetch.host_refusal is not None:
        return "TARGET_REFUSED"
    if fetch.scope_refusal is not None:
        if fetch.scope_refusal is safe.ScopeRefusal.EMPTY_ALLOWLIST:
            return "DOMAIN_ALLOWLIST_EMPTY"
        return "DOMAIN_NOT_APPROVED"
    if fetch.dns_failure is not None:
        return "DNS_RESOLUTION_FAILED"
    refusal = fetch.egress_refusal
    assert refusal is not None, "拒绝恰好有一个理由"
    table = {
        safe.EgressRefusal.REDIRECT_NOT_FOLLOWED: "REDIRECT_NOT_FOLLOWED",
        safe.EgressRefusal.RESPONSE_TOO_LARGE: "RESPONSE_TOO_LARGE",
        safe.EgressRefusal.BUDGET_REQUESTS: "EGRESS_BUDGET_EXCEEDED",
        safe.EgressRefusal.BUDGET_BYTES: "EGRESS_BUDGET_EXCEEDED",
        safe.EgressRefusal.PROXY_REFUSED: "PROXY_REFUSED",
        safe.EgressRefusal.EGRESS_UNAVAILABLE: "EGRESS_PROXY_UNAVAILABLE",
        safe.EgressRefusal.TRANSPORT: "TRANSPORT_FAILED",
        safe.EgressRefusal.UNREADABLE_RESPONSE: "TRANSPORT_FAILED",
    }
    return table[refusal]


def contract_reason_code(code: str) -> str:
    """把本地理由码折成契约 §5.3 的那一格。**多对一**，见 :data:`CONTRACT_FOLD`。

    集成时 ``PolicyDecision.reason_code`` 只能取契约闭集里的值。给一张手写的折叠表
    而不是在调用点各写一个 ``if``：一处折叠，一处可复核。
    """
    if code == "ok":
        return "ok"
    if code not in CONTRACT_FOLD:
        raise ValueError(f"{code!r} 没有折叠到契约 §5.3 的闭集里 —— 先加一条映射")
    return CONTRACT_FOLD[code]


@dataclass
class SafeEgressSession:
    """一次受管取回的会话：一个出口、一个判据、一本预算、一个范围。**不要自己构造。**

    用 :func:`safe_browser_egress` 或 :func:`open_session`。类本身不是上下文管理器 ——
    起停那两行住在工厂里，因为「谁起的谁停」必须只有一个答案，而一个 ``__enter__``
    会让调用方以为自己可以随便进。
    """

    proxy: safe.EgressProxy
    gate: safe.EgressGate
    allowlist: frozenset[str]
    limits: safe.EgressLimits
    scope: CrawlScope
    #: 会话级的端口策略。``claim_page`` 用它把非 web 端口挡在计划之外，
    #: 而出口那一层用**同一个集合**在每一跳上再判一次。
    allowed_ports: frozenset[int] = field(default_factory=lambda: DEFAULT_EGRESS_PORTS)
    budget: safe.EgressBudget = field(default_factory=safe.EgressBudget)
    user_agent: str = "MarketOps-Research/0.4 (+https://marketops.invalid/bot)"
    _pages: dict[str, int] = field(default_factory=dict)
    _refusals: list[str] = field(default_factory=list)

    # ------------------------------------------------------------------
    def browser_config_kwargs(self) -> dict[str, object]:
        """给 ``BrowserConfig(**kwargs)`` 的那两格。**出口没起来就抛。**

        返回 ``proxy_config`` 与 ``extra_args``，而不是一个构造好的 ``BrowserConfig``：
        本模块不认识 crawl4ai（它是可选依赖），而一个「帮你构造好了」的返回值会把
        ``headless`` / ``user_agent`` 这类与出口无关的决定也一起定死。

        那两个键值与核心 ``crawl4ai_fetcher`` 读的是**同一个定义**
        （``adapters/guarded_http.py``）—— 两处各写一份字符串的那天，改一处的那一处
        不会红，而失败的样子是「出口配了但没接上」。
        """
        return {
            "proxy_config": safe.browser_proxy_config(self.proxy.url),
            "extra_args": safe.browser_egress_arguments(),
        }

    def browser_egress_ref(self) -> str:
        """这一次浏览器抓取的出口判定引用 → ``EvidenceEnvelope.egress_decision_ref``。

        内容寻址（见 ``decision_ref``）：出口上记下的每一条判定（目标 + 判决 + 详情）
        按顺序进摘要。**一条判定都没有时也回一个引用** —— 那说的是「这一次抓取在出口上
        没有留下任何判定」，而那与「有一个空的引用」是两件事。
        """
        material = "\x1f".join(
            f"{d.target}|{d.refusal or 'allowed'}|{d.detail}" for d in self.proxy.decisions
        )
        return safe.decision_ref(
            f"browser:{len(self.proxy.decisions)}", reason="browser", detail=material
        )

    # ------------------------------------------------------------------
    def claim_page(self, url: str, *, depth: int) -> None:
        """认领一个要抓的页面。**越界就抛，且抛在抓之前。**

        三件事各判一次：深度、字面量与批准名单、去重后的页面数。

        ## 认领只用**纯**判据的一半，另一半留给出口

        ``check_target``（字面量）与 ``check_domain``（名单）都是**纯函数**：不解析、
        不连网。所以这一步可以把一个范围外的 URL 挡在计划之外，而不产生任何网络动作。

        名字解析成什么地址**不在这一层判**（那要问一次 DNS）—— 它由出口那一层判，
        并且以一份 :class:`GuardedFetch` 的形式回到调用方。两个理由：其一，认领是
        **计划**上的事，它不该为了排一个 URL 就去解析；其二，调用的形状因此只有两种
        清清楚楚的：**计划违例抛异常，网络判决返回值**。
        """
        if depth < 0:
            raise EgressDenied("DEPTH_EXCEEDED", f"深度不得为负，收到 {depth}")
        if depth > self.scope.max_depth:
            raise EgressDenied(
                "DEPTH_EXCEEDED",
                f"深度 {depth} 超过了这一次允许的 {self.scope.max_depth} —— 这一页没有抓。",
            )
        literal = safe.check_target(url)
        if not literal.allowed:
            assert literal.refusal is not None, "拒绝必须有理由码"
            self._record("TARGET_REFUSED", url)
            raise EgressDenied(
                "TARGET_REFUSED", f"{url}：{literal.refusal.value} —— {literal.detail}"
            )
        scoped = safe.check_domain(
            safe.normalise_host(urlsplit(url).hostname or ""), allowlist=self.allowlist
        )
        if not scoped.allowed:
            assert scoped.refusal is not None, "拒绝必须有理由码"
            code = self._code_for(scoped.refusal)
            self._record(code, url)
            raise EgressDenied(code, f"{url}：{scoped.refusal.value} —— {scoped.detail}")
        parts = urlsplit(url)
        try:
            port = parts.port or (443 if parts.scheme.lower() == "https" else 80)
        except ValueError as exc:
            # 与出口那一层同一条：越界端口不是一次崩溃，是一条命名的拒绝。
            self._record("PORT_NOT_ALLOWED", url)
            raise EgressDenied(
                "PORT_NOT_ALLOWED", f"{url} 的端口读不出来：{exc}"
            ) from None
        ported = safe.check_port(port, allowed=self.allowed_ports)
        if not ported.allowed:
            assert ported.refusal is not None, "拒绝必须有理由码"
            self._record("PORT_NOT_ALLOWED", url)
            raise EgressDenied(
                "PORT_NOT_ALLOWED", f"{url}：{ported.refusal.value} —— {ported.detail}"
            )
        if url in self._pages:
            # 同一个 URL 认领两次不是一次新的页面。去重发生在**预算之前** ——
            # 否则一个把自己首页链接了二十遍的 sitemap 会吃掉整个页数上限。
            return
        if len(self._pages) + 1 > self.scope.max_pages:
            raise EgressDenied(
                "PAGE_BUDGET_EXCEEDED",
                f"这一次已经认领了 {len(self._pages)} 个页面，上限 {self.scope.max_pages}"
                " —— 这一页没有认领。",
            )
        self._pages[url] = depth

    @staticmethod
    def _code_for(refusal: safe.ScopeRefusal) -> str:
        """范围判据的拒绝 → 本模块的理由码。**与** :func:`reason_code` **同一张映射**：
        两处各折一次的那天，同一种拒绝会在两条路径上得到两个名字。"""
        if refusal is safe.ScopeRefusal.EMPTY_ALLOWLIST:
            return "DOMAIN_ALLOWLIST_EMPTY"
        return "DOMAIN_NOT_APPROVED"

    # ------------------------------------------------------------------
    def fetch_auxiliary(self, url: str, *, depth: int = 0) -> safe.GuardedFetch:
        """辅助取回一次。**先认领（范围），再取（判据 + 经出口）。**

        两件事都做，而不是只做后一件：出口那一层看不到「这一次 run 想铺多宽」，
        而范围那一层看不到「这一跳实际连到了哪里」。合成一个调用点，才不会出现
        「某一条路径只过了其中一道」。

        返回一份拒绝（包括 ``REDIRECT_NOT_FOLLOWED``）时**不做任何自动跟随**：
        调用方若要跟下一跳，就再调一次这个方法（``fetch.redirect_to``），于是那一跳
        与第一跳走同一条经过认领与判据的路。
        """
        self.claim_page(url, depth=depth)
        client = safe.GuardedHttpClient(
            proxy=self.proxy,
            gate=self.gate,
            budget=self.budget,
            user_agent=self.user_agent,
        )
        return client.get(url)

    # ------------------------------------------------------------------
    @property
    def pages(self) -> tuple[str, ...]:
        """已认领的页面，按认领顺序。"""
        return tuple(self._pages)

    @property
    def refusals(self) -> tuple[str, ...]:
        """被拒的目标与理由码，按发生顺序 —— **包括那些抛出去的**。"""
        return tuple(self._refusals)

    def egress_refusals(self) -> tuple[str, ...]:
        """出口代理自己记下的被拒目标（``host:port``）。"""
        return self.proxy.refusals

    def _record(self, code: str, target: str) -> None:
        self._refusals.append(f"{code}:{target}")


@contextmanager
def open_session(
    *,
    allowlist: frozenset[str] | None = None,
    limits: safe.EgressLimits | None = None,
    scope: CrawlScope | None = None,
    resolver: safe.HostResolver | None = None,
    gate: safe.EgressGate | None = None,
    allowed_ports: frozenset[int] | None = None,
    start_proxy: bool = True,
) -> Iterator[SafeEgressSession]:
    """起一个受管会话。**出口起不来就抛，不会退化。**

    ``start_proxy=False`` 与 ``gate=`` 都是给**测试**用的，理由同一条：本机测试唯一能
    安全立起来的靶子在 ``127.0.0.1`` 上，而 ``check_resolved`` 会（正确地）拒绝它；
    §7 末句又禁止拿真实内网或云 metadata 当靶子。于是「判据说了什么」与「拿着判据的
    结论做了什么事」只能分开测 —— 一个是让出口先别起，一个是换一个只对诱饵那一个端点
    点头、其余一切照走真判据的判据。

    生产路径上这两格永远是缺省：一个把「起不起代理」「用哪个判据」交给调用方决定的
    口子，迟早会被用来绕开它们。**注入的判据必须自带同一份名单** —— ``session.allowlist``
    仍然取自 ``entries``，而认领那一路（``claim_page``）用的是它，不是注入的那个对象。

    **判据永远装着范围闸**：这里不做 ``allowlist is None`` 的分支 —— 这一层没有
    「不装范围闸」这一档。空集是「谁都不许」，不是「不设限」。
    """
    entries = allowed_domains_from_env() if allowlist is None else allowlist
    if not entries:
        raise EgressDenied(
            "DOMAIN_ALLOWLIST_EMPTY",
            f"没有批准任何域名 —— 设置 {ALLOWED_DOMAINS_ENV}（逗号分隔的公开域名）。"
            "空名单与没配是同一件事，而方向刻意选在「停」这一边。",
        )
    resolved_limits = limits if limits is not None else safe.DEFAULT_LIMITS
    resolved_ports = DEFAULT_EGRESS_PORTS if allowed_ports is None else allowed_ports
    actual_gate = gate if gate is not None else safe.EgressGate(
        resolver, allowlist=entries, allowed_ports=resolved_ports,
    )
    proxy = safe.EgressProxy(gate=actual_gate)
    if start_proxy:
        try:
            proxy.start()
        except (OSError, ValueError, RuntimeError) as exc:
            raise EgressDenied(
                "EGRESS_PROXY_UNAVAILABLE",
                f"出口代理起不来：{type(exc).__name__}: {exc} —— 这一次取回没有发生。",
            ) from exc
    try:
        yield SafeEgressSession(
            proxy=proxy,
            gate=actual_gate,
            allowlist=entries,
            allowed_ports=resolved_ports,
            limits=resolved_limits,
            scope=scope if scope is not None else CrawlScope(),
            budget=safe.EgressBudget(resolved_limits),
        )
    finally:
        if start_proxy:
            proxy.stop()


@contextmanager
def safe_browser_egress(
    *,
    allowlist: frozenset[str] | None = None,
    limits: safe.EgressLimits | None = None,
    scope: CrawlScope | None = None,
    resolver: safe.HostResolver | None = None,
    gate: safe.EgressGate | None = None,
    allowed_ports: frozenset[int] | None = None,
) -> Iterator[SafeEgressSession]:
    """浏览器那一跳的入口。**前置判据不过就抛 ``LOCAL_CRAWL_NOT_SAFE_TO_RUN``。**

    与 :func:`open_session` 的差别只有一格：这里先问 :func:`local_crawl_ready`，
    因为「开浏览器」比「取一份 robots.txt」多一个前置条件（crawl4ai 与 Chromium）。
    少问这一格的结果是一次渲染失败被读成「这个站点有问题」。

    一条要写下来的形状：**今天没有「不安全模式」这个参数，将来也不加。** 未受管的
    本地浏览器不是一个降级档，它是这条链要防的那件事本身。
    """
    entries = allowed_domains_from_env() if allowlist is None else allowlist
    ready, cause = local_crawl_ready(entries)
    if not ready:
        assert cause in READINESS_CAUSES, "就绪判据必须从闭集里给出一格"
        raise EgressDenied(
            "LOCAL_CRAWL_NOT_SAFE_TO_RUN",
            f"{cause} —— 受管出口不可用时不降级：未受管的本地浏览器"
            "是这条链要防的那件事本身。",
        )
    with open_session(
        allowlist=entries, limits=limits, scope=scope, resolver=resolver, gate=gate,
        allowed_ports=allowed_ports,
    ) as session:
        yield session


def main(argv: list[str] | None = None) -> int:
    """命令行探针：只回答「现在能不能安全地开本地浏览器」，**不开**。

    它是给 preflight 与运维用的：一个能跑在客户机上、不联网、不建目录、不读凭据的
    检查。退出码 0 = 可以，1 = 不可以（成因码打在 stdout）。
    """
    parser = argparse.ArgumentParser(description="MarketOps 受管出口就绪探针")
    parser.add_argument("--domains", default=None,
                        help=f"覆盖 {ALLOWED_DOMAINS_ENV}（逗号分隔）")
    args = parser.parse_args(argv)
    try:
        entries = allowed_domains_from_env(args.domains)
    except ValueError as exc:
        print(f"DOMAIN_ALLOWLIST_MALFORMED: {exc}")
        return 1
    ready, cause = local_crawl_ready(entries)
    assert cause in READINESS_CAUSES, "就绪判据必须从闭集里给出一格"
    print(cause)
    return 0 if ready else 1


if __name__ == "__main__":
    raise SystemExit(main())
