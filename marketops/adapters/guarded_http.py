"""辅助 HTTP：**浏览器之外**的那条链，也走同一个出口。

## 为什么这条链存在，以及为什么它也经代理

``adapters/egress_proxy.py`` 接在浏览器的 ``--proxy-server`` 上，凡是 Chromium 发的
请求都经过它。而 Crawl4AI 的辅助操作**不是浏览器发的**：

* ``async_url_seeder`` 从 ``robots.txt`` 走到 ``sitemap.xml`` 再走到一串页面；
* CSS 抽取的取回；
* 深度爬取自己决定「下一批抓哪些」。

这些请求由库自己的 HTTP 栈发出，于是它们**根本不经过那个代理**。官方
``crawl4ai/egress_policy.py`` 提供 ``set_egress_proxy`` 正是为此 —— 而它的实现是
「把代理环境变量设上」，那对 ``urllib`` 有效，对一个自己开套接字的实现无效，对
「变量被别的东西覆盖」也无从察觉。

本模块因此**自己发这些请求，但走同一个出口**：一个已经起来的
:class:`~marketops.adapters.egress_proxy.EgressProxy`。于是「全部出网都过那一层」
这句话对两条链都成立，而两条链用的是**同一个判据对象**（:class:`EgressGate`），
不是同一份判据的两个副本。

出口起不来时这一层**拒绝**（``EGRESS_UNAVAILABLE``），不退化成一个直连 ——
一个「出口不可用就先直连吧」的分支就是这条链存在的理由被抹掉的那个失败。

## 为什么不用 ``urllib.request`` / ``requests`` / ``httpx``

那三个都会读 ``http_proxy`` / ``https_proxy`` / ``no_proxy``，也都会自己再
``getaddrinfo`` 一次。两件事都让「这一次请求真的过出口」这句话失效：一次环境变量
注入就能把流量引到别处，而出口上留下的仍然是一条「放行」的记录。所以这里用
``socket`` + ``ssl`` + 手写的请求行。

## 不跟随重定向（``IA-5``）

3xx 一律**不跟随**：回到调用方的是一个 ``REDIRECT_NOT_FOLLOWED`` 的拒绝，以及下一跳
那个绝对 URL（:attr:`GuardedFetch.redirect_to`）。

理由不是「省事」，是**每一跳都要重新判一次**：跟随的实现必须把第 2 跳的目标重新送进
判据与预算，而那正是最容易漏掉的一格 —— 漏掉之后的形状是「重定向到 ``127.0.0.1``
成功了」，而报告上一切正常。把这件决定权交回调用方，它就与「第一次请求」走**同一条**
经过检查的路（``research_egress.SafeEgressSession.fetch_auxiliary`` 的认领与判据）。

## 预算

``EgressBudget`` 是一次 run 的**有状态**账：请求数与字节数。它在**发出去之前**扣
（与 ``ports/budget.py`` 的额度账同一条纪律：一笔支出在起飞前记下，而不是回来后
补记）。扣不动就拒绝，且拒绝发生在连接之前。

## 一次判定的形状

四个理由字段**各自是闭集**、恰好一个被填：地址（``TargetRefusal``）、范围
（``ScopeRefusal``）、DNS（``DnsFailureKind``）、出口（``EgressRefusal``）。
压成一个字符串会让「该去找谁」丢掉 —— ``not_approved`` 要找批域名的人，
``empty_allowlist`` 要找配环境变量的人，``resolves_to_private`` 要换来源，
``budget_requests`` 要看这一次 run 是不是爬得太宽。

## 它没有宣称做到的事

一次「出口放行」不是「这份内容可信」。https 在这一层同样是端到端握手的隧道
（代理只做 CONNECT），出口看得见连了哪个 host、看不见隧道里的正文有没有
prompt injection。来源可信与内容可信是两件事，处置在别处。
"""

from __future__ import annotations

import hashlib
import socket
import ssl
from dataclasses import dataclass
from enum import Enum
from urllib.parse import urljoin, urlsplit

from ..security.network import TargetRefusal
from ..security.scope import ScopeRefusal
from ..ports.dns import DnsFailureKind
from .egress_proxy import PROXY_MARKER_HEADER, EgressGate, EgressProxy, EgressVerdict

__all__ = [
    "BROWSER_EGRESS_ARGUMENTS",
    "DEFAULT_LIMITS",
    "EgressBudget",
    "EgressBudgetExceeded",
    "EgressLimits",
    "EgressRefusal",
    "GuardedFetch",
    "GuardedHttpClient",
    "GuardedResponse",
    "browser_egress_arguments",
    "browser_proxy_config",
    "decision_ref",
]

#: 缺省端口。**键集与 ``security/network.ALLOWED_SCHEMES`` 必须相等**，这件事由
#: ``tests/test_wave1_egress_guarded_http.py`` 执行 —— 两处不一致时，一个被允许的
#: 协议会在这里 ``KeyError``（那不是一次判决，是一次崩溃）。
_DEFAULT_PORT: dict[str, int] = {"http": 80, "https": 443}

#: 会被当成「一次改向」而不是正文的状态码。``304`` 不在此列：它是缓存协商。
_REDIRECT_STATUS = frozenset({301, 302, 303, 307, 308})

#: 我们愿意自己发的请求方法。辅助链只读 —— 不是「没实现写」，是**不提供**。
_METHOD = "GET"

#: 浏览器那一层钉的两个参数。**在这里定义一次**，``crawl4ai_fetcher`` 与独立插件
#: 都读它 —— 两处各写一份字符串，改一处的那天另一处不会红。
#:
#: * ``--proxy-bypass-list=<-loopback>``：关掉 Chromium 对回环地址的**隐式**绕过。
#:   实测（Chromium 1243）这一版本来就没有那条规则，而 ``--proxy-bypass-list``
#:   本身**确实被遵守**（显式写 ``localhost,127.0.0.1`` 时同一个请求直连本机服务、
#:   出口上一条记录都没有）。所以它是**防御**，不是本机读数所必需的那一格。
#: * ``--disable-quic``：UDP 那条路（HTTP/3）。**未实测** —— 本机没有可构造的
#:   HTTP/3 目标。它是一条「不靠上游默认值」的防御，任何人都不得据此宣称
#:   HTTP/3 已经被挡住。
BROWSER_EGRESS_ARGUMENTS: tuple[str, ...] = (
    "--proxy-bypass-list=<-loopback>",
    "--disable-quic",
)


def browser_proxy_config(proxy_url: str) -> dict[str, str]:
    """给 ``BrowserConfig(proxy_config=…)`` 的那一格。

    用 ``proxy_config`` 而不是 ``proxy``：实测（crawl4ai 0.9.3）后者已被标为
    deprecated 并在一条路径上被忽略，照抄字段名会得到一个「配了但没接上」的部署，
    而那个部署看起来完全正常。
    """
    return {"server": proxy_url}


def browser_egress_arguments() -> list[str]:
    """浏览器启动参数。返回**新列表** —— 调用方往里面塞别的东西时不该改到这一格。"""
    return list(BROWSER_EGRESS_ARGUMENTS)


class EgressRefusal(str, Enum):
    """**非地址、非范围、非 DNS** 的拒绝理由。闭集，理由与 ``TargetRefusal`` 同：
    开放字符串的第一个拼错的名字会静静开出一条新类别。"""

    EGRESS_UNAVAILABLE = "egress_unavailable"
    """受管出口没有起来。**这一格就是「不退化」那一句的落点**：出口不可用时这一次
    取回不发生，而不是变成一次直连。"""

    REDIRECT_NOT_FOLLOWED = "redirect_not_followed"
    """3xx。**刻意不跟随**（``IA-5``）：下一跳由调用方重新走一遍认领与判据。"""

    PROXY_REFUSED = "proxy_refused"
    """出口**自己**把这一跳拒了，而这一层刚刚判它通过。

    它不该发生在「同一个判据对象」的前提下 —— 所以它出现时说的是一件要紧的事：
    名字在两次判定之间换了答案（DNS rebinding 的典型形状），或者有人把两个不同的
    判据接在了同一条链上。两种都不该被读成一次普通的网络失败。"""

    BUDGET_REQUESTS = "budget_requests"
    """这一次 run 的请求数用完了。"""

    BUDGET_BYTES = "budget_bytes"
    """这一次 run 的字节预算用完了。"""

    RESPONSE_TOO_LARGE = "response_too_large"
    """单个响应超过了单次上限。**与 ``BUDGET_BYTES`` 分开**：前者是「这一份太大」
    （改单页上限），后者是「这一次 run 爬得太宽」（缩范围）—— 处置不同。"""

    TRANSPORT = "transport"
    """连上了但这一次会话坏了（对端先关、TLS 握手失败、头部读不出来）。"""

    UNREADABLE_RESPONSE = "unreadable_response"
    """状态行读不出来。它的典型成因是一个不是 HTTP 的对端 —— 那不该被当成一次
    成功的取回。"""


@dataclass(frozen=True)
class EgressLimits:
    """一次 run 的上限。冻结的**规格**；有状态的那个是 :class:`EgressBudget`。"""

    max_requests: int = 32
    max_bytes: int = 8_000_000
    max_response_bytes: int = 4_000_000

    def __post_init__(self) -> None:
        for name in ("max_requests", "max_bytes", "max_response_bytes"):
            if getattr(self, name) <= 0:
                raise ValueError(
                    f"{name} 必须为正 —— 一个 0 或负的上限会让每一次调用都失败，"
                    "而那个失败看起来像一次网络问题"
                )


#: 缺省上限。刻意保守：辅助链是**零边际费用**的那一档（T1），它的成本是本地算力
#: 与站点侧压力。偏小的一侧错一次只让人去调配置；偏大的一侧错一次没人知道。
DEFAULT_LIMITS = EgressLimits()


class EgressBudgetExceeded(Exception):
    """预算扣不动。带 :attr:`refusal`（闭集）与一句人话。"""

    def __init__(self, refusal: EgressRefusal, detail: str) -> None:
        super().__init__(detail)
        self.refusal = refusal
        self.detail = detail


class EgressBudget:
    """一次 run 的请求数与字节数账。**在发出去之前扣。**

    与 ``ports/budget.py`` 那条纪律同源：一笔支出在起飞前记下。回来后补记的账在
    一次超时或一次崩溃上会少记一笔，而少记的那一笔正是「这一次到底发了多少」。
    """

    def __init__(self, limits: EgressLimits | None = None) -> None:
        self._limits = limits if limits is not None else DEFAULT_LIMITS
        self._requests = 0
        self._bytes = 0

    @property
    def limits(self) -> EgressLimits:
        return self._limits

    @property
    def requests_used(self) -> int:
        return self._requests

    @property
    def bytes_used(self) -> int:
        return self._bytes

    @property
    def requests_left(self) -> int:
        return max(0, self._limits.max_requests - self._requests)

    @property
    def bytes_left(self) -> int:
        return max(0, self._limits.max_bytes - self._bytes)

    def spend_request(self) -> None:
        """记一次即将发出的请求。扣不动时抛，**且调用方还没有连出去**。"""
        if self._requests + 1 > self._limits.max_requests:
            raise EgressBudgetExceeded(
                EgressRefusal.BUDGET_REQUESTS,
                f"这一次 run 的请求数用完了（上限 {self._limits.max_requests}）——"
                "这一次请求没有发出去。",
            )
        self._requests += 1

    def spend_bytes(self, count: int) -> None:
        """记已读到的字节。扣不动时抛 —— 调用方应当停止读这一份。"""
        if count < 0:
            raise ValueError("字节数不得为负")
        if self._bytes + count > self._limits.max_bytes:
            raise EgressBudgetExceeded(
                EgressRefusal.BUDGET_BYTES,
                f"这一次 run 的字节预算用完了（上限 {self._limits.max_bytes}）——"
                "读到这一份为止。",
            )
        self._bytes += count


@dataclass(frozen=True)
class GuardedResponse:
    """一次**成功**的辅助取回。

    ``hops`` 里只有**这一跳**（本模块不跟随重定向），但保留成一个元组：
    ``ports/acquisition.py`` 的 ``redirect_trace`` 是同一个形状，而「这一跳」与
    「整条链」在报告上应当是同一格的两个取值，不是两种数据类型。
    """

    url: str
    status: int
    body: bytes
    content_type: str
    hops: tuple[str, ...]

    @property
    def text(self) -> str:
        """按 ``charset`` 解出来的正文；认不出来时退 utf-8 + ``replace``。

        不猜编码是**有意的**：一份 ``latin-1`` 的 sitemap 被当成 utf-8 读会静默
        产生替换字符，而下游拿它去匹配 URL 只会「找不到」，不会报错。
        """
        return self.body.decode(_charset_of(self.content_type), errors="replace")


@dataclass(frozen=True)
class GuardedFetch:
    """一次辅助取回的判决。四个理由字段**恰好一个**被填。

    :attr:`decision_ref` 是给 ``EvidenceEnvelope.egress_decision_ref`` 用的那一格：
    一个**内容寻址**的稳定引用（见 :func:`decision_ref`）—— 不需要一个存储，
    而同样的判定在两次运行里得到同一个引用。
    """

    allowed: bool
    url: str
    response: GuardedResponse | None = None
    host_refusal: TargetRefusal | None = None
    scope_refusal: ScopeRefusal | None = None
    dns_failure: DnsFailureKind | None = None
    egress_refusal: EgressRefusal | None = None
    detail: str = ""
    hops: tuple[str, ...] = ()
    redirect_to: str = ""
    decision_ref: str = ""

    def __post_init__(self) -> None:
        refusals = self._refusals()
        if self.allowed:
            if self.response is None:
                raise ValueError("放行必须带一份响应 —— 没有它就没有「取回了什么」")
            if refusals:
                raise ValueError("放行的判决不得同时带理由码")
            if self.redirect_to:
                raise ValueError("放行不得带下一跳 —— 3xx 不是正文")
            return
        if self.response is not None:
            raise ValueError("拒绝不得带响应正文 —— 一份被拒的取回不是证据")
        if len(refusals) != 1:
            raise ValueError(
                "拒绝必须**恰好**带一个理由：四个理由字段各自指向一个不同的处置，"
                f"收到 {len(refusals)} 个。"
            )
        if not self.detail:
            raise ValueError("拒绝必须带一句人话 —— 只有理由码的报告没人能处置")
        if self.redirect_to and self.egress_refusal is not EgressRefusal.REDIRECT_NOT_FOLLOWED:
            raise ValueError("只有「不跟随重定向」那一格才带下一跳")

    def _refusals(self) -> list[object]:
        return [
            value
            for value in (
                self.host_refusal,
                self.scope_refusal,
                self.dns_failure,
                self.egress_refusal,
            )
            if value is not None
        ]

    @property
    def reason(self) -> str:
        """一格可上报的理由码。四个闭集各自的取值**不重叠**（``EgressVerdict`` 那一节
        记着 ``no_host`` 撞车那条理由），所以范围那一格带上了 ``scope:`` 前缀、
        DNS 那一族带 ``dns:``。

        ``dns`` 那一个前缀是必要的：``timeout`` 单独出现时读起来像一个 HTTP 超时，
        而它是「这一次我们没问到那个名字」—— ``egress_proxy`` 那一条也是这么写的。
        """
        if self.allowed:
            return "ok"
        if self.host_refusal is not None:
            return self.host_refusal.value
        if self.scope_refusal is not None:
            return f"scope:{self.scope_refusal.value}"
        if self.dns_failure is not None:
            return f"dns:{self.dns_failure.value}"
        assert self.egress_refusal is not None, "拒绝恰好有一个理由"
        return self.egress_refusal.value


def decision_ref(url: str, *, reason: str, detail: str, address: str = "") -> str:
    """一条**内容寻址**的出口判定引用。

    ``EvidenceEnvelope.egress_decision_ref`` 要的是一个指向「那一次判定」的稳定引用。
    这里给的是它的内容摘要，而不是一个需要维护的存储里的行号：

    * 它不需要一个新的持久化面（本 Wave 不新建存储）；
    * 同样的判定在两次运行里得到**同一个**引用 —— 而那正是「引用」要回答的问题
      （「这两份证据的出口判定是同一条吗」）；
    * 代价要写明：**同一个 URL 上两次同样的判定共用一个引用**。要区分「第几次」，
      看的是它旁边那一格（``path`` / ``retrieved_at``），不是这一格。

    ``address`` 只在它真的被连过时才进摘要（浏览器那一层）。辅助链经代理走，
    所以这一格留空 —— 一个「放行了但不知道连到哪」的判定不该假装知道。
    """
    material = "\x1f".join((url, reason, detail, address)).encode("utf-8")
    return "egress:" + hashlib.sha256(material).hexdigest()[:32]


class GuardedHttpClient:
    """自己发、自己判、**经同一个出口**走的辅助 HTTP 客户端。

    Args:
        proxy: 一个**已经起来**的受管出口。没有它就取不回任何东西 —— 这一格不给
            缺省值，理由见模块 docstring 里「不退化」那一句。
        gate: 判据。**必须是这个代理用的那一个**（同一个 ``EgressGate`` 对象）——
            两个判据对象会让同一跳得到两种判决，而那种不一致在报告上长得像一次
            网络异常。
        budget: 这一次 run 的账。``None`` 表示用缺省上限新建一本 —— **不是**不记账。
        timeout: 单次连接与读取的超时（秒）。
        user_agent: 自报身份。**不伪装成浏览器** —— 见 ``tests/test_no_evasion.py``
            对全仓的那条禁令；这里给的是「我们是谁」，不是「我们像谁」。
    """

    def __init__(
        self,
        *,
        proxy: EgressProxy,
        gate: EgressGate,
        budget: EgressBudget | None = None,
        timeout: float = 10.0,
        user_agent: str = "MarketOps-Research/0.4 (+https://marketops.invalid/bot)",
    ) -> None:
        self._proxy = proxy
        self._gate = gate
        self._budget = budget if budget is not None else EgressBudget()
        self._timeout = timeout
        self._user_agent = user_agent

    @property
    def budget(self) -> EgressBudget:
        return self._budget

    @property
    def proxy(self) -> EgressProxy:
        return self._proxy

    @property
    def gate(self) -> EgressGate:
        return self._gate

    # ------------------------------------------------------------------
    def get(self, url: str) -> GuardedFetch:
        """取一份资源。**一跳，不跟随重定向；失败关闭，永不退化。**"""
        parts = urlsplit(url)
        scheme = parts.scheme.lower()
        # 协议不再复查：``EgressGate`` 刚刚保证了它属于 ``ALLOWED_SCHEMES``，而
        # ``_DEFAULT_PORT`` 的键集与之相等（由测试钉住）。一个「再检查一次」的分支
        # 在这里永远为假，而它会在下一次有人改那个集合时悄悄变成真的。
        host = (parts.hostname or "").lower()
        try:
            port = parts.port or _DEFAULT_PORT[scheme]
        except ValueError as exc:
            # ``urlsplit`` 对越界的端口（65536 / -1）抛 ValueError。让它逃出去，
            # 调用方拿到的是一次**崩溃**而不是一个理由码 —— 而这条链上每一个
            # 「这一次没取回」都必须是命名的。端口读不出来与端口不被允许是同一
            # 处置（改目标或改配置），所以共用那一格。
            return GuardedFetch(
                allowed=False,
                url=url,
                host_refusal=TargetRefusal.PORT_NOT_ALLOWED,
                detail=f"{url} 的端口读不出来：{exc}",
            )

        verdict = self._gate.check(host, port)
        if not verdict.allowed:
            return self._refuse_verdict(url, verdict)

        try:
            self._budget.spend_request()
        except EgressBudgetExceeded as exc:
            return self._refuse_egress(url, exc.refusal, exc.detail)

        return self._through_proxy(url, scheme, host, port)

    # ------------------------------------------------------------------
    def _through_proxy(self, url: str, scheme: str, host: str, port: int) -> GuardedFetch:
        """连到**代理**（不是连到目标），由代理去连它判过的那个地址。"""
        try:
            proxy_url = self._proxy.url
        except RuntimeError as exc:
            return self._refuse_egress(url, EgressRefusal.EGRESS_UNAVAILABLE, str(exc))
        proxy_parts = urlsplit(proxy_url)
        proxy_host = proxy_parts.hostname or "127.0.0.1"
        proxy_port = proxy_parts.port or 80

        hops = [url]
        try:
            raw = socket.create_connection((proxy_host, proxy_port), timeout=self._timeout)
        except OSError as exc:
            return self._refuse_egress(
                url,
                EgressRefusal.EGRESS_UNAVAILABLE,
                f"连不上本机出口 {proxy_host}:{proxy_port}：{exc} —— 这一次取回没有发生。",
                hops=hops,
            )
        connection = raw
        try:
            if scheme == "https":
                tunnelled = self._open_tunnel(raw, host, port, url)
                if tunnelled is not None:
                    return tunnelled
                connection = ssl.create_default_context().wrap_socket(
                    raw, server_hostname=host
                )
                connection.sendall(self._origin_form_request(url, host, port, scheme))
            else:
                connection.sendall(self._absolute_form_request(url, host, port))
            return self._read_response(connection, url, hops)
        except (OSError, TimeoutError, ssl.SSLError) as exc:
            return self._refuse_egress(
                url,
                EgressRefusal.TRANSPORT,
                f"与出口/目标的这一会话坏了：{type(exc).__name__}: {exc}",
                hops=hops,
            )
        finally:
            try:
                connection.close()
            except OSError:  # pragma: no cover - 关闭失败在这条链上没有下游
                pass

    def _open_tunnel(
        self, raw: socket.socket, host: str, port: int, url: str
    ) -> GuardedFetch | None:
        """https 走 CONNECT：出口只看得见 ``host:port``，隧道里是端到端 TLS。

        返回 ``None`` 表示隧道建立了；返回一份拒绝表示它没建立。
        """
        authority = host if port == 443 else f"{host}:{port}"
        raw.sendall(
            f"CONNECT {authority} HTTP/1.1\r\nHost: {authority}\r\n\r\n".encode("iso-8859-1")
        )
        reader = _HeadReader(raw, timeout=self._timeout)
        status_line = reader.line()
        parts = status_line.split(" ", 2)
        if len(parts) < 2 or not parts[1].isdigit():
            return self._refuse_egress(
                url,
                EgressRefusal.UNREADABLE_RESPONSE,
                f"出口的 CONNECT 应答读不出来：{status_line[:120]!r}",
                hops=[url],
            )
        while reader.line():
            pass
        if int(parts[1]) != 200:
            return self._refuse_egress(
                url,
                EgressRefusal.PROXY_REFUSED,
                f"出口拒绝了这一次 CONNECT（{status_line.strip()}）—— "
                f"{self._last_proxy_detail()}",
                hops=[url],
            )
        return None

    def _absolute_form_request(self, url: str, host: str, port: int) -> bytes:
        """http 走绝对形式：出口看到完整的 URL，自己判、自己钉、自己改成 origin-form。"""
        authority = host if port == 80 else f"{host}:{port}"
        return (
            f"{_METHOD} {url} HTTP/1.1\r\n"
            f"Host: {authority}\r\n"
            f"User-Agent: {self._user_agent}\r\n"
            "Accept: text/html,application/xhtml+xml,application/xml,text/plain,*/*\r\n"
            # 这一层没有解压那一半：一份被 gzip 过的正文读出来是二进制垃圾，
            # 而下游拿它去匹配 URL 只会「找不到」，不会报错。
            "Accept-Encoding: identity\r\n"
            "Connection: close\r\n\r\n"
        ).encode("iso-8859-1")

    def _origin_form_request(self, url: str, host: str, port: int, scheme: str) -> bytes:
        """隧道里的 origin-form。``Host`` 在非缺省端口时带端口（RFC 7230 §5.4）——
        少了它会被虚拟主机路由到另一个站上，而判据判的是这个端口。"""
        parts = urlsplit(url)
        target = parts.path or "/"
        if parts.query:
            target += f"?{parts.query}"
        authority = host if port == _DEFAULT_PORT[scheme] else f"{host}:{port}"
        return (
            f"{_METHOD} {target} HTTP/1.1\r\n"
            f"Host: {authority}\r\n"
            f"User-Agent: {self._user_agent}\r\n"
            "Accept: text/html,application/xhtml+xml,application/xml,text/plain,*/*\r\n"
            "Accept-Encoding: identity\r\n"
            "Connection: close\r\n\r\n"
        ).encode("iso-8859-1")

    # ------------------------------------------------------------------
    def _read_response(
        self, connection: socket.socket, url: str, hops: list[str]
    ) -> GuardedFetch:
        """读到状态行与头部，然后**有界地**读正文。

        正文那一格的界是两层里更小的那个：单次上限与 run 的剩余字节数。两件事
        （「这一份太大」与「这一次爬得太宽」）的理由码因此不同。
        """
        reader = _HeadReader(connection, timeout=self._timeout)
        try:
            status_line = reader.line()
            parts = status_line.split(" ", 2)
            if len(parts) < 2 or not parts[1].isdigit():
                return self._refuse_egress(
                    url,
                    EgressRefusal.UNREADABLE_RESPONSE,
                    f"{url} 的状态行读不出来：{status_line[:120]!r}",
                    hops=hops,
                )
            status = int(parts[1])
            headers = reader.headers()
        except (OSError, TimeoutError) as exc:
            return self._refuse_egress(
                url,
                EgressRefusal.TRANSPORT,
                f"读 {url} 的响应头失败：{type(exc).__name__}: {exc}",
                hops=hops,
            )

        # **先看那个标记头**：出口自己发的应答与上游回的应答在状态码上同形，
        # 而它们说的是两件相反的事（见 ``PROXY_MARKER_HEADER`` 那一节）。
        # 这一格必须排在重定向与 403 之前 —— 一个由出口发出的 403 不是
        # 「站点拒绝了我们」，一个由出口发出的 502 也不是一份可以用作证据的正文。
        marker = headers.get(PROXY_MARKER_HEADER.lower(), "")
        if marker:
            return self._proxy_answer(url, status, hops)

        if status in _REDIRECT_STATUS:
            location = headers.get("location", "")
            return GuardedFetch(
                allowed=False,
                url=url,
                egress_refusal=EgressRefusal.REDIRECT_NOT_FOLLOWED,
                detail=f"{url} 回了 {status}"
                + (f"，指向 {location}" if location else "（没有 Location）")
                + "。本模块不跟随重定向：下一跳要由调用方重新认领、重新过判据与预算。",
                hops=tuple(hops),
                # 拼成绝对 URL 只是为了调用方**免于自己拼**；它仍然要
                # 重新过一次认领与判据（见模块 docstring）。
                redirect_to=urljoin(url, location) if location else "",
                decision_ref=decision_ref(
                    url, reason=EgressRefusal.REDIRECT_NOT_FOLLOWED.value, detail=location
                ),
            )

        ceiling = min(self._budget.limits.max_response_bytes, self._budget.bytes_left)
        declared = _content_length(headers)
        if declared is not None and declared > ceiling:
            return self._refuse_egress(
                url,
                EgressRefusal.RESPONSE_TOO_LARGE,
                f"{url} 声明了 {declared} 字节，超过这一次允许读的上限 {ceiling}"
                "（单次上限与 run 剩余预算里更小的那个）—— 正文没有读。",
                hops=hops,
            )
        try:
            body = reader.drain(ceiling)
        except _TooLarge as exc:
            return self._refuse_egress(url, EgressRefusal.RESPONSE_TOO_LARGE, str(exc), hops=hops)
        except (OSError, TimeoutError) as exc:
            return self._refuse_egress(
                url,
                EgressRefusal.TRANSPORT,
                f"读 {url} 的正文失败：{type(exc).__name__}: {exc}",
                hops=hops,
            )
        try:
            self._budget.spend_bytes(len(body))
        except EgressBudgetExceeded as exc:
            return self._refuse_egress(url, exc.refusal, exc.detail, hops=hops)

        return GuardedFetch(
            allowed=True,
            url=url,
            response=GuardedResponse(
                url=url,
                status=status,
                body=body,
                content_type=headers.get("content-type", ""),
                hops=tuple(hops),
            ),
            hops=tuple(hops),
            decision_ref=decision_ref(url, reason="ok", detail=f"status={status}"),
        )

    # ------------------------------------------------------------------
    def _proxy_answer(self, url: str, status: int, hops: list[str]) -> GuardedFetch:
        """出口**自己**回的应答。三种状态码各归各的理由码。

        * ``403`` —— 出口拒绝了这一跳。**同一个判据对象刚刚说了「可以」**，所以这一格
          不该发生；它出现时说的事写在 :attr:`EgressRefusal.PROXY_REFUSED` 上。
        * ``502`` —— 出口连不上上游。那是传输失败，不是一份 502 正文。
        * 其余（``400`` 等）—— 出口读不懂这一条请求（例如绝对形式的 ``https://``）。
          一次「我们发错了形式」不该被读成一次成功的取回。
        """
        table = {
            403: EgressRefusal.PROXY_REFUSED,
            502: EgressRefusal.TRANSPORT,
        }
        refusal = table.get(status, EgressRefusal.UNREADABLE_RESPONSE)
        return self._refuse_egress(
            url,
            refusal,
            f"{url}：出口自己回了 {status}（判据刚刚放行）—— {self._last_proxy_detail()}",
            hops=hops,
        )

    def _last_proxy_detail(self) -> str:
        """出口上最后一条决定。**只用于人读的报文**，不用来判什么。"""
        decisions = self._proxy.decisions
        if not decisions:
            return "出口上没有留下任何决定（这一格本身就是一个异常）"
        last = decisions[-1]
        return f"出口最后一条：{last.target} → {last.refusal or 'allowed'}（{last.detail}）"

    def _refuse_verdict(self, url: str, verdict: EgressVerdict) -> GuardedFetch:
        """判据自己的拒绝。**一条连接都没有建立。**

        四路分开还原成**类型**（而不是让调用方去解析一个字符串）的理由写在
        ``EgressVerdict`` 的 docstring 里：``no_host`` 在两个闭集里都存在。
        """
        refusal = verdict.refusal
        if verdict.scope_refusal is not None:
            return GuardedFetch(
                allowed=False,
                url=url,
                scope_refusal=verdict.scope_refusal,
                detail=verdict.detail,
                decision_ref=decision_ref(url, reason=refusal, detail=verdict.detail),
            )
        if refusal.startswith("dns:"):
            return GuardedFetch(
                allowed=False,
                url=url,
                dns_failure=DnsFailureKind(refusal.split(":", 1)[1]),
                detail=verdict.detail,
                decision_ref=decision_ref(url, reason=refusal, detail=verdict.detail),
            )
        return GuardedFetch(
            allowed=False,
            url=url,
            host_refusal=TargetRefusal(refusal),
            detail=verdict.detail,
            decision_ref=decision_ref(url, reason=refusal, detail=verdict.detail),
        )

    def _refuse_egress(
        self,
        url: str,
        refusal: EgressRefusal,
        detail: str,
        *,
        hops: list[str] | None = None,
    ) -> GuardedFetch:
        return GuardedFetch(
            allowed=False,
            url=url,
            egress_refusal=refusal,
            detail=detail,
            hops=tuple(hops or ()),
            decision_ref=decision_ref(url, reason=refusal.value, detail=detail),
        )


class _TooLarge(Exception):
    """正文超过了这一次允许读的上限。内部信号 —— 由调用点折成理由码。"""


class _HeadReader:
    """一个有界的头部读取器。

    ``MAX_HEAD_BYTES`` 与 ``egress_proxy`` 那一格同值同理由：这个客户端对一个不受控
    的对端读字节，而不设上限的头部读取等于让对端决定我们分配多少内存。
    """

    MAX_HEAD_BYTES = 65536
    _CHUNK = 65536

    def __init__(self, connection: socket.socket, *, timeout: float) -> None:
        self._connection = connection
        self._connection.settimeout(timeout)
        self._buffer = b""
        self._eof = False

    def _fill(self) -> bool:
        """保证缓冲区里有一个 CRLF。返回是否还读得到行。"""
        while b"\r\n" not in self._buffer and not self._eof:
            chunk = self._connection.recv(self._CHUNK)
            if not chunk:
                self._eof = True
                break
            self._buffer += chunk
            if len(self._buffer) > self.MAX_HEAD_BYTES:
                raise OSError(f"响应头超过 {self.MAX_HEAD_BYTES} 字节")
        return b"\r\n" in self._buffer

    def line(self) -> str:
        """读一行（不含 CRLF）。对端先关掉时抛 ``OSError``。"""
        if not self._fill():
            raise OSError("对端在这一行之前就关了")
        head, _, tail = self._buffer.partition(b"\r\n")
        self._buffer = tail
        return head.decode("iso-8859-1")

    def headers(self) -> dict[str, str]:
        """读到空行为止。键小写 —— 与 ``crawl4ai_fetcher`` 实测到的那一点一致。"""
        collected: dict[str, str] = {}
        while True:
            line = self.line()
            if not line:
                break
            name, separator, value = line.partition(":")
            if separator:
                collected[name.strip().lower()] = value.strip()
        return collected

    def drain(self, ceiling: int) -> bytes:
        """把剩下的字节读到 EOF，或读到 ``ceiling`` 为止。

        ``ceiling`` 那一格是**硬界**：多读到的那一个字节就足以让这一次判定从「放行」
        变成 ``RESPONSE_TOO_LARGE``，所以循环按「还能读多少 + 1」去取 —— 一个读到
        刚好 ``ceiling`` 就停的实现分不出「刚好这么大」与「还有更多」。
        """
        body = bytearray(self._buffer)
        self._buffer = b""
        if len(body) > ceiling:
            raise _TooLarge(self._too_large(ceiling))
        while not self._eof:
            chunk = self._connection.recv(min(self._CHUNK, ceiling - len(body) + 1))
            if not chunk:
                self._eof = True
                break
            body += chunk
            if len(body) > ceiling:
                raise _TooLarge(self._too_large(ceiling))
        return bytes(body)

    def _too_large(self, ceiling: int) -> str:
        return (
            f"正文超过这一次允许读的上限 {ceiling} 字节"
            "（单次上限与 run 剩余预算里更小的那个）—— 读到界线为止。"
        )


def _content_length(headers: dict[str, str]) -> int | None:
    """声明了 ``Content-Length`` 时读出它。读不出整数时返回 ``None``。

    返回 ``None`` 而不是抛：一个形状不对的 ``Content-Length`` 不该让一次取回变成
    一次崩溃，而「没声明」在这一格有确切含义（按 EOF 读到上限为止）。
    """
    value = headers.get("content-length", "").strip()
    return int(value) if value.isdecimal() else None


def _charset_of(content_type: str) -> str:
    for part in content_type.split(";")[1:]:
        name, separator, value = part.partition("=")
        if separator and name.strip().lower() == "charset":
            candidate = value.strip().strip('"').lower()
            if candidate:
                return candidate
    return "utf-8"
