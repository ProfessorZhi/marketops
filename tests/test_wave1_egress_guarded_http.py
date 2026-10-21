"""辅助 HTTP 那条链（``adapters/guarded_http.py``）的**对抗**判据。

对应 Wave-1 契约 `INTERFACE-CONTRACT.md` §10 的 `IA-4`（浏览器与辅助 HTTP **全部**
经 EgressProxy；不经代理即失败关闭）与 `IA-5`（不跟随重定向），以及
`WORKER-DAG-AND-OWNERSHIP.md` §4 W-A 的完成判据（P1-01 对抗矩阵）。

## 这条链守的是浏览器代理守不到的那一半

``EgressProxy`` 接在 Chromium 的 ``--proxy-server`` 上，凡是浏览器发的请求都经过它。
而 Crawl4AI 的辅助操作**不是浏览器发的**：``async_url_seeder`` 从 ``robots.txt`` 走到
``sitemap.xml`` 再走到一串页面，CSS 抽取、深爬同理 —— 它们由库自己的 HTTP 栈发出，
**根本不经过那个代理**。官方 ``egress_policy.set_egress_proxy`` 的做法是设代理环境
变量，而那一招对「自己开套接字的实现」无效。

本文件判的就是这另一半：那些请求**也过同一个出口**，而且出口不可用时不退化。

## 靶子只能是本机的诱饵，而真判据会（正确地）拒绝它

测试唯一能安全立起来的 origin 在 ``127.0.0.1`` 上，而 ``check_resolved`` 拒绝一切
非全局可路由地址；§7 末句又明令禁止把真实内网或云 metadata 当靶子。两者合起来只剩
一条路：**把「判据说了什么」与「拿着判据的结论做了什么事」分开测**。

* 需要**判决是真的**的那些判据（名字解析到内网要拒、名单外要拒），用**真判据**，
  并且断言诱饵上**一次连接都没有**。后者比「回执写着拒绝」强得多：一个先连上去再
  回拒绝的实现会通过前者，而它已经发出了我们明令不许发出的那一个请求。
* 需要**诱饵够得着**的那些判据（预算、重定向、标记头），用一个只对诱饵那一个
  ``(host, port)`` 点头、其余一切走真判据的替身判据。

## 本文件不证明什么

它不证明 TLS 那一段（本机没有可构造的 HTTPS 靶子），也不证明 Chromium 的行为 ——
那两件事分别在 ``test_egress_proxy.py`` 与 ``test_browser_egress_live.py`` 上。
"""

from __future__ import annotations

import socket
import threading
from collections.abc import Callable, Iterator
from contextlib import contextmanager

import pytest

from marketops.adapters.egress_proxy import (
    PROXY_MARKER_HEADER,
    EgressGate,
    EgressProxy,
    EgressVerdict,
)
from marketops.adapters.guarded_http import (
    EgressBudget,
    EgressLimits,
    EgressRefusal,
    GuardedFetch,
    GuardedHttpClient,
    GuardedResponse,
    decision_ref,
)
from marketops.ports.dns import DnsFailureKind, DnsResolutionError, HostResolver
from marketops.security.network import ALLOWED_SCHEMES, TargetRefusal
from marketops.security.scope import ScopeRefusal

#: 诱饵那个「公开」名字。它不在任何保留名字空间里（``*.example`` 是保留后缀，
#: ``example.co.uk`` 不是），所以 ``check_target`` 会正常放行它 —— 这一格是刻意的：
#: 本文件要判的正是「名字看起来公开、解析出来是内网」那一种。
DECOY_HOST = "page.example.co.uk"
LOOPBACK_HOST = "127.0.0.1"
PUBLIC = "93.184.216.34"

Responder = Callable[[bytes, "_Origin"], bytes | None]


# ----------------------------------------------------------------------
# 受控诱饵
# ----------------------------------------------------------------------
class _Origin:
    """一个只在测试生命周期内存在的本机 origin。

    ``accepts`` 是**关键的那个计数器**：一次被拒绝的请求不能在这里留下任何一次
    连接。只断言「回执写着拒绝」是不够的 —— 一个先连上去再回拒绝的实现会通过那条
    断言，而它已经把我们应该挡住的请求发出去了。
    """

    def __init__(self, responder: Responder | None = None) -> None:
        self._responder: Responder = responder or _ok_responder
        self._socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._socket.bind((LOOPBACK_HOST, 0))
        self._socket.listen(16)
        self._socket.settimeout(0.5)
        self._stop = threading.Event()
        self.accepts = 0
        self.requests: list[bytes] = []
        self.port: int = self._socket.getsockname()[1]
        self._thread = threading.Thread(target=self._serve, daemon=True)
        self._thread.start()

    @property
    def address(self) -> str:
        return f"{LOOPBACK_HOST}:{self.port}"

    def _serve(self) -> None:
        while not self._stop.is_set():
            try:
                conn, _ = self._socket.accept()
            except TimeoutError:
                continue
            except OSError:
                return
            self.accepts += 1
            try:
                conn.settimeout(2.0)
                head = conn.recv(65536)
                self.requests.append(head)
                payload = self._responder(head, self)
                if payload:
                    conn.sendall(payload)
            except OSError:
                pass
            finally:
                conn.close()

    def close(self) -> None:
        self._stop.set()
        try:
            self._socket.close()
        except OSError:  # pragma: no cover - 关闭失败在这条链上没有下游
            pass
        self._thread.join(timeout=2)


def _ok_responder(_head: bytes, _origin: _Origin) -> bytes:
    return (
        b"HTTP/1.1 200 OK\r\n"
        b"Content-Type: text/plain; charset=utf-8\r\n"
        b"Content-Length: 2\r\n"
        b"Connection: close\r\n\r\nok"
    )


def _body_responder(size: int) -> Responder:
    def responder(_head: bytes, _origin: _Origin) -> bytes:
        return (
            b"HTTP/1.1 200 OK\r\n"
            b"Content-Type: application/octet-stream\r\n"
            + f"Content-Length: {size}\r\n".encode()
            + b"Connection: close\r\n\r\n"
            + b"x" * size
        )

    return responder


def _declaring_responder(declared: int, actual: int) -> Responder:
    def responder(_head: bytes, _origin: _Origin) -> bytes:
        return (
            b"HTTP/1.1 200 OK\r\n"
            b"Content-Type: application/octet-stream\r\n"
            + f"Content-Length: {declared}\r\n".encode()
            + b"Connection: close\r\n\r\n"
            + b"y" * actual
        )

    return responder


def _status_responder(status: int, *, marker: bool = False) -> Responder:
    def responder(_head: bytes, _origin: _Origin) -> bytes:
        extra = f"{PROXY_MARKER_HEADER}: {status}\r\n" if marker else ""
        return (
            f"HTTP/1.1 {status} Nope\r\n".encode()
            + extra.encode()
            + b"Content-Type: text/plain\r\nContent-Length: 5\r\nConnection: close\r\n\r\nnope!"
        )

    return responder


def _redirect_responder(location: Callable[[_Origin], str], status: int = 302) -> Responder:
    def responder(_head: bytes, origin: _Origin) -> bytes:
        return (
            f"HTTP/1.1 {status} Found\r\n".encode()
            + f"Location: {location(origin)}\r\n".encode()
            + b"Content-Length: 0\r\nConnection: close\r\n\r\n"
        )

    return responder


def _dangling_redirect_responder(_head: bytes, _origin: _Origin) -> bytes:
    return b"HTTP/1.1 302 Found\r\nContent-Length: 0\r\nConnection: close\r\n\r\n"


def _garbage_responder(_head: bytes, _origin: _Origin) -> bytes:
    return b"NOT-HTTP-AT-ALL\r\n\r\n"


@pytest.fixture
def served() -> Iterator[Callable[[Responder | None], _Origin]]:
    servers: list[_Origin] = []

    def factory(responder: Responder | None = None) -> _Origin:
        server = _Origin(responder)
        servers.append(server)
        return server

    yield factory
    for server in servers:
        server.close()


# ----------------------------------------------------------------------
# 判据与解析器替身
# ----------------------------------------------------------------------
class _FixedResolver(HostResolver):
    def __init__(self, mapping: dict[str, tuple[str, ...]]) -> None:
        self._mapping = mapping
        self.calls: list[tuple[str, int]] = []

    def resolve(self, host: str, port: int) -> tuple[str, ...]:
        self.calls.append((host, port))
        if host not in self._mapping:
            raise DnsResolutionError(host, DnsFailureKind.NONEXISTENT, "替身里没有这个名字")
        return self._mapping[host]


class _DecoyGate(EgressGate):
    """只对诱饵那一个 ``(host, port)`` 点头，**其余一切走真判据**。

    「只放行那一个端点」而不是「全部放行」，是这一格能与真判据共存的原因：任何别的
    目标（包括一个指向内网的诱饵名字）仍然由 ``check_target`` / ``check_domain`` /
    ``check_resolved`` 判，而那些判决是真的。
    """

    def __init__(self, resolver: HostResolver, allowlist: frozenset[str], decoy: _Origin) -> None:
        super().__init__(resolver, allowlist=allowlist)
        self._decoy_host = DECOY_HOST
        self._decoy_port = decoy.port
        self._decoy_address = LOOPBACK_HOST

    def check(self, host: str, port: int) -> EgressVerdict:
        if host == self._decoy_host and port == self._decoy_port:
            # 真判据会（正确地）拒绝 127.0.0.1 —— 而诱饵正是它。§7 末句禁止拿真实
            # 内网或云 metadata 当靶子，所以这一格是这些判据能跑起来的唯一方式。
            return EgressVerdict(allowed=True, address=self._decoy_address)
        return super().check(host, port)


class _FlipGate(EgressGate):
    """第一次问点头，第二次问拒绝。**DNS rebinding 的形状**，也是「两个判据」的形状。"""

    def __init__(self) -> None:
        super().__init__(_FixedResolver({DECOY_HOST: (LOOPBACK_HOST,)}))
        self.calls = 0

    def check(self, host: str, port: int) -> EgressVerdict:
        self.calls += 1
        if self.calls == 1:
            return EgressVerdict(allowed=True, address=LOOPBACK_HOST)
        return EgressVerdict(
            allowed=False, refusal="resolves_to_private", detail="第二次问，它解析到了内网"
        )


@contextmanager
def _client(
    *,
    allowlist: frozenset[str],
    resolver: HostResolver | None = None,
    gate: EgressGate | None = None,
    budget: EgressBudget | None = None,
    limits: EgressLimits | None = None,
    timeout: float = 5.0,
) -> Iterator[tuple[GuardedHttpClient, EgressProxy]]:
    """起一个真的出口 + 一个接在它上面的客户端。**出口是真的在监听的。**

    客户端与出口必须拿到**同一个判据对象** —— 两个判据对象会让同一跳得到两种判决，
    而那种不一致在报告上长得像一次网络异常（见 ``GuardedHttpClient.__init__``）。
    """
    actual = gate if gate is not None else EgressGate(resolver, allowlist=allowlist)
    proxy = EgressProxy(gate=actual)
    proxy.start()
    try:
        yield (
            GuardedHttpClient(
                proxy=proxy,
                gate=actual,
                budget=budget if budget is not None else EgressBudget(limits),
                timeout=timeout,
            ),
            proxy,
        )
    finally:
        proxy.stop()


# ======================================================================
# 1. 名字解析到内网/元数据：**连接之前**就拒，诱饵上零连接
# ======================================================================
@pytest.mark.parametrize(
    ("address", "why"),
    [
        ("127.0.0.1", "环回"),
        ("::1", "IPv6 环回"),
        ("10.1.2.3", "私网 A 段"),
        ("192.168.1.5", "私网 C 段"),
        ("169.254.169.254", "链路本地 / 云 metadata"),
        ("100.64.0.1", "CGNAT"),
        ("0.0.0.0", "未指定"),
        ("::ffff:127.0.0.1", "IPv4-mapped 环回"),
    ],
)
def test_a_public_looking_name_that_resolves_inward_is_refused_before_connecting(
    address: str, why: str, served: Callable[[Responder | None], _Origin]
) -> None:
    """判据是「解析出来的地址」，不是「名字看起来公开」。

    用**真判据**（不注入替身），并且断言诱饵上零连接 —— 后者才是要点：「回执写着
    拒绝」与「那个请求没有离开进程」是两件事，而第二件才是这条链存在的理由。
    """
    decoy = served()
    with _client(
        allowlist=frozenset({DECOY_HOST}),
        resolver=_FixedResolver({DECOY_HOST: (address,)}),
    ) as (client, _proxy):
        fetched = client.get(f"http://{DECOY_HOST}:{decoy.port}/robots.txt")

    assert not fetched.allowed, f"{address}（{why}）没有被拒"
    assert fetched.host_refusal is TargetRefusal.RESOLVES_TO_PRIVATE
    assert fetched.response is None, "一份被拒的取回不得带正文 —— 它不是证据"
    assert decoy.accepts == 0, "判据拒绝了，而请求已经发出去了 —— 那正是这条链要防的事"
    assert fetched.decision_ref.startswith("egress:")


def test_a_name_outside_the_allowlist_never_reaches_the_network(
    served: Callable[[Responder | None], _Origin]
) -> None:
    """范围闸在出口之前：名单外的域名一次连接都不会发生。

    这一条与「出口拒绝」是两件事 —— 出口那一层根本不知道「谁批准过这个来源」，
    所以它必须在这里被挡住，而不是「抓到了但不存」。
    """
    decoy = served()
    with _client(
        allowlist=frozenset({"approved.example.co.uk"}),
        resolver=_FixedResolver({DECOY_HOST: (PUBLIC,)}),
    ) as (client, _proxy):
        fetched = client.get(f"http://{DECOY_HOST}:{decoy.port}/")

    assert fetched.scope_refusal is ScopeRefusal.NOT_APPROVED
    assert fetched.reason == "scope:not_approved"
    assert fetched.response is None
    assert decoy.accepts == 0


def test_an_empty_allowlist_denies_everything() -> None:
    """空名单是**谁都不许**（装了闸，而名单是空的），不是「没有闸」。"""
    with _client(
        allowlist=frozenset(),
        resolver=_FixedResolver({DECOY_HOST: (PUBLIC,)}),
    ) as (client, _proxy):
        fetched = client.get(f"http://{DECOY_HOST}/")

    assert fetched.scope_refusal is ScopeRefusal.EMPTY_ALLOWLIST
    assert fetched.reason == "scope:empty_allowlist"


def test_an_unapproved_ip_literal_is_refused_by_scope_not_by_address() -> None:
    """一个**全局可路由**的 IP 字面量：地址判据放行它，范围判据拒绝它。

    这一条是两个判据各答各的那句话的证据：``93.184.216.34`` 是公开地址（安全），
    却不是一个被批准过的**名字**（不可审计）。
    """
    with _client(
        allowlist=frozenset({DECOY_HOST}),
        resolver=_FixedResolver({PUBLIC: (PUBLIC,)}),
    ) as (client, _proxy):
        fetched = client.get(f"http://{PUBLIC}/")

    assert fetched.host_refusal is None
    assert fetched.scope_refusal is ScopeRefusal.IP_LITERAL


# ======================================================================
# 2. 重定向：**不跟随**，且下一跳从来没有被连过
# ======================================================================
def test_a_redirect_to_loopback_is_not_followed(
    served: Callable[[Responder | None], _Origin]
) -> None:
    """诱饵发 302 指向自己（字面量 ``127.0.0.1``）：第 2 跳**从来没有发生**。

    计数因此是 ``accepts == 1``：只有第一跳。跟随的实现在这里会是 2。
    """
    decoy = served(_redirect_responder(lambda origin: f"http://{origin.address}/private"))
    resolver = _FixedResolver({DECOY_HOST: (LOOPBACK_HOST,)})
    with _client(
        allowlist=frozenset({DECOY_HOST}),
        resolver=resolver,
        gate=_DecoyGate(resolver, frozenset({DECOY_HOST}), decoy),
    ) as (client, _proxy):
        fetched = client.get(f"http://{DECOY_HOST}:{decoy.port}/robots.txt")

    assert not fetched.allowed
    assert fetched.egress_refusal is EgressRefusal.REDIRECT_NOT_FOLLOWED
    assert fetched.response is None
    assert fetched.redirect_to == f"http://{decoy.address}/private"
    assert decoy.accepts == 1, "重定向的第 2 跳连上去了 —— 本模块不跟随重定向"


def test_a_redirect_to_a_name_outside_the_allowlist_is_not_followed(
    served: Callable[[Responder | None], _Origin]
) -> None:
    """名单外的**公开**域名那一跳同样不发生（不是「跟随之后再判」）。"""
    decoy = served(_redirect_responder(lambda _origin: "http://elsewhere.example.net/"))
    resolver = _FixedResolver({DECOY_HOST: (LOOPBACK_HOST,), "elsewhere.example.net": (PUBLIC,)})
    with _client(
        allowlist=frozenset({DECOY_HOST}),
        resolver=resolver,
        gate=_DecoyGate(resolver, frozenset({DECOY_HOST}), decoy),
    ) as (client, _proxy):
        fetched = client.get(f"http://{DECOY_HOST}:{decoy.port}/")

    assert fetched.egress_refusal is EgressRefusal.REDIRECT_NOT_FOLLOWED
    assert fetched.redirect_to == "http://elsewhere.example.net/"
    assert decoy.accepts == 1
    # 下一跳那个名字**一次都没有被问过** —— 「先跟随、再判」的实现会在这里留下
    # 一次解析（然后才拒绝），而那时请求已经出去了。
    assert "elsewhere.example.net" not in [host for host, _port in resolver.calls]


def test_a_three_xx_without_a_location_is_still_not_content(
    served: Callable[[Responder | None], _Origin]
) -> None:
    """3xx 却没有 ``Location``：下一跳无法确定，所以这一份**不是**正文。"""
    decoy = served(_dangling_redirect_responder)
    with _client(
        allowlist=frozenset({DECOY_HOST}),
        resolver=_FixedResolver({DECOY_HOST: (LOOPBACK_HOST,)}),
        gate=_DecoyGate(_FixedResolver({DECOY_HOST: (LOOPBACK_HOST,)}),
                        frozenset({DECOY_HOST}), decoy),
    ) as (client, _proxy):
        fetched = client.get(f"http://{DECOY_HOST}:{decoy.port}/")

    assert fetched.egress_refusal is EgressRefusal.REDIRECT_NOT_FOLLOWED
    assert fetched.redirect_to == "", "没有 Location 时不该凭空造一个下一跳"
    assert fetched.response is None


# ======================================================================
# 3. 出口自己发的应答**不是**正文（标记头那一条）
# ======================================================================
def test_an_upstream_403_is_a_real_observation_not_a_proxy_refusal(
    served: Callable[[Responder | None], _Origin]
) -> None:
    """上游自己回的 403 是一条**可以入库的来源观测**，不是出口故障。

    这一条判的是 :data:`PROXY_MARKER_HEADER` 那个区分：没有它，客户端只能按状态码
    猜，而猜错的那一半会把一次出口故障变成一条关于某个站点的记录。
    """
    decoy = served(_status_responder(403))
    with _client(
        allowlist=frozenset({DECOY_HOST}),
        resolver=_FixedResolver({DECOY_HOST: (LOOPBACK_HOST,)}),
        gate=_DecoyGate(_FixedResolver({DECOY_HOST: (LOOPBACK_HOST,)}),
                        frozenset({DECOY_HOST}), decoy),
    ) as (client, _proxy):
        fetched = client.get(f"http://{DECOY_HOST}:{decoy.port}/")

    assert fetched.allowed, fetched.detail
    assert fetched.response is not None
    assert fetched.response.status == 403, "站点拒绝被读成了别的东西"
    assert fetched.egress_refusal is None


def test_the_exit_own_403_is_not_content(served: Callable[[Responder | None], _Origin]) -> None:
    """出口**自己**回的 403：既不是正文，也不是一次普通的网络失败。"""
    decoy = served()
    gate = _FlipGate()  # 第一次（客户端）点头，第二次（出口）拒绝
    with _client(
        allowlist=frozenset({DECOY_HOST}),
        resolver=_FixedResolver({DECOY_HOST: (LOOPBACK_HOST,)}),
        gate=gate,
    ) as (client, proxy):
        fetched = client.get(f"http://{DECOY_HOST}:{decoy.port}/robots.txt")

    assert not fetched.allowed
    assert fetched.egress_refusal is EgressRefusal.PROXY_REFUSED
    assert fetched.response is None, "出口的 403 被当成了一份正文"
    assert gate.calls == 2, "出口没有重判一次 —— 那这一条判的就不是它"
    assert proxy.refusals == (f"{DECOY_HOST}:{decoy.port}",)
    assert decoy.accepts == 0, "出口拒绝了，而目标收到了连接"


def test_the_exit_own_502_is_a_transport_refusal_not_a_body(
    served: Callable[[Responder | None], _Origin]
) -> None:
    """出口连不上上游（502）：那是传输失败，不是一份 502 正文。"""
    probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    probe.bind((LOOPBACK_HOST, 0))
    dead_port = probe.getsockname()[1]
    probe.close()

    with _client(
        allowlist=frozenset({DECOY_HOST}),
        resolver=_FixedResolver({DECOY_HOST: (LOOPBACK_HOST,)}),
        gate=_DecoyGate(_FixedResolver({DECOY_HOST: (LOOPBACK_HOST,)}),
                        frozenset({DECOY_HOST}), _OriginWithPort(dead_port)),
    ) as (client, _proxy):
        fetched = client.get(f"http://{DECOY_HOST}:{dead_port}/")

    assert fetched.egress_refusal is EgressRefusal.TRANSPORT
    assert fetched.response is None


class _OriginWithPort:
    """给 ``_DecoyGate`` 用的最小替身：它只读 ``port``。"""

    def __init__(self, port: int) -> None:
        self.port = port


def test_the_client_refuses_when_the_exit_is_not_up() -> None:
    """**不退化**那一句的落点：出口没起来时这一次取回不发生。

    这一条是 ``IA-4`` 的后半句 —— 「不经代理时返回 ``LOCAL_CRAWL_NOT_SAFE_TO_RUN``,
    而不是无管控回退」。在这一层它落在 ``EGRESS_UNAVAILABLE`` 上；从入口那一层
    （``research_egress.safe_browser_egress``）看则是 ``LOCAL_CRAWL_NOT_SAFE_TO_RUN``。
    """
    gate = EgressGate()
    proxy = EgressProxy(gate=gate)  # 起来之前就交给客户端
    client = GuardedHttpClient(proxy=proxy, gate=gate)

    fetched = client.get("https://example.com/")

    assert not fetched.allowed
    assert fetched.egress_refusal is EgressRefusal.EGRESS_UNAVAILABLE
    assert fetched.response is None


def test_proxy_environment_variables_do_not_create_a_second_route(
    served: Callable[[Responder | None], _Origin], monkeypatch: pytest.MonkeyPatch
) -> None:
    """``http_proxy`` 那一族环境变量**不会**把这一条链引到别处。

    「不用 ``urllib`` / ``requests`` / ``httpx``」那个选择的判据：那三个都会读这几个
    变量，于是「连到判过的那个地址」这句话会被一台机器上恰好设着的一个代理作废。
    """
    proxy_decoy = served()
    target = served()
    for name in ("HTTP_PROXY", "http_proxy", "HTTPS_PROXY", "https_proxy",
                 "ALL_PROXY", "all_proxy", "NO_PROXY", "no_proxy"):
        monkeypatch.setenv(name, f"http://{proxy_decoy.address}")
    with _client(
        allowlist=frozenset({DECOY_HOST}),
        resolver=_FixedResolver({DECOY_HOST: (LOOPBACK_HOST,)}),
        gate=_DecoyGate(_FixedResolver({DECOY_HOST: (LOOPBACK_HOST,)}),
                        frozenset({DECOY_HOST}), target),
    ) as (client, _proxy):
        fetched = client.get(f"http://{DECOY_HOST}:{target.port}/robots.txt")

    assert fetched.allowed, fetched.detail
    assert proxy_decoy.accepts == 0, "流量被环境变量里那个代理接走了"
    assert target.accepts == 1


# ======================================================================
# 4. 预算：请求数、字节、单份大小
# ======================================================================
def test_the_request_budget_stops_the_second_call_before_it_connects(
    served: Callable[[Responder | None], _Origin]
) -> None:
    """请求数是**在起飞之前**扣的：扣不动时那一次连接没有发生。"""
    decoy = served()
    budget = EgressBudget(EgressLimits(max_requests=1))
    with _client(
        allowlist=frozenset({DECOY_HOST}),
        resolver=_FixedResolver({DECOY_HOST: (LOOPBACK_HOST,)}),
        gate=_DecoyGate(_FixedResolver({DECOY_HOST: (LOOPBACK_HOST,)}),
                        frozenset({DECOY_HOST}), decoy),
        budget=budget,
    ) as (client, _proxy):
        first = client.get(f"http://{DECOY_HOST}:{decoy.port}/a")
        second = client.get(f"http://{DECOY_HOST}:{decoy.port}/b")

    assert first.allowed, first.detail
    assert second.egress_refusal is EgressRefusal.BUDGET_REQUESTS
    assert decoy.accepts == 1, "第二次请求发出去了 —— 预算没有挡在连接之前"
    assert budget.requests_used == 1


def test_a_body_larger_than_the_single_response_limit_is_refused(
    served: Callable[[Responder | None], _Origin]
) -> None:
    """单份上限：超了就是 ``RESPONSE_TOO_LARGE``，而不是把整份读进内存。"""
    decoy = served(_body_responder(4096))
    with _client(
        allowlist=frozenset({DECOY_HOST}),
        resolver=_FixedResolver({DECOY_HOST: (LOOPBACK_HOST,)}),
        gate=_DecoyGate(_FixedResolver({DECOY_HOST: (LOOPBACK_HOST,)}),
                        frozenset({DECOY_HOST}), decoy),
        limits=EgressLimits(max_response_bytes=1024, max_bytes=1_000_000),
    ) as (client, _proxy):
        fetched = client.get(f"http://{DECOY_HOST}:{decoy.port}/big")

    assert fetched.egress_refusal is EgressRefusal.RESPONSE_TOO_LARGE
    assert fetched.response is None


def test_a_declared_length_over_the_limit_is_refused_without_reading_the_body(
    served: Callable[[Responder | None], _Origin]
) -> None:
    """``Content-Length`` 已经说了超过上限 —— 正文**不读**。

    这一格与「读着读着超了」是两条不同的路，回同一个理由码：前者在第一个字节之前就停，
    后者在界线上停。两条都要有：一个只实现后者的版本会在一次声明了 10 GB 的响应上先
    把内存吃掉一半才发现。
    """
    decoy = served(_declaring_responder(declared=10_000_000, actual=10))
    with _client(
        allowlist=frozenset({DECOY_HOST}),
        resolver=_FixedResolver({DECOY_HOST: (LOOPBACK_HOST,)}),
        gate=_DecoyGate(_FixedResolver({DECOY_HOST: (LOOPBACK_HOST,)}),
                        frozenset({DECOY_HOST}), decoy),
        limits=EgressLimits(max_response_bytes=1024),
    ) as (client, _proxy):
        fetched = client.get(f"http://{DECOY_HOST}:{decoy.port}/huge")

    assert fetched.egress_refusal is EgressRefusal.RESPONSE_TOO_LARGE
    assert "10000000" in fetched.detail


def test_the_run_byte_budget_is_shared_across_calls(
    served: Callable[[Responder | None], _Origin]
) -> None:
    """字节账在**这一次 run** 上共享：第二次的界是**剩余**预算，不是单份上限。"""
    decoy = served(_body_responder(600))
    budget = EgressBudget(EgressLimits(max_bytes=1000, max_response_bytes=1000))
    with _client(
        allowlist=frozenset({DECOY_HOST}),
        resolver=_FixedResolver({DECOY_HOST: (LOOPBACK_HOST,)}),
        gate=_DecoyGate(_FixedResolver({DECOY_HOST: (LOOPBACK_HOST,)}),
                        frozenset({DECOY_HOST}), decoy),
        budget=budget,
    ) as (client, _proxy):
        first = client.get(f"http://{DECOY_HOST}:{decoy.port}/one")
        second = client.get(f"http://{DECOY_HOST}:{decoy.port}/two")

    assert first.allowed, first.detail
    assert second.egress_refusal is EgressRefusal.RESPONSE_TOO_LARGE
    assert "400" in second.detail, "第二次的界该是剩余预算（1000-600=400）"
    assert budget.bytes_used == len(first.response.body)  # type: ignore[union-attr]


# ======================================================================
# 5. 传输层的失败：有名字、有理由码，不折成「什么都没发生」
# ======================================================================
def test_an_unreadable_status_line_is_refused(
    served: Callable[[Responder | None], _Origin]
) -> None:
    """一个不是 HTTP 的对端不该被当成一次成功的取回。"""
    decoy = served(_garbage_responder)
    with _client(
        allowlist=frozenset({DECOY_HOST}),
        resolver=_FixedResolver({DECOY_HOST: (LOOPBACK_HOST,)}),
        gate=_DecoyGate(_FixedResolver({DECOY_HOST: (LOOPBACK_HOST,)}),
                        frozenset({DECOY_HOST}), decoy),
    ) as (client, _proxy):
        fetched = client.get(f"http://{DECOY_HOST}:{decoy.port}/")

    assert fetched.egress_refusal is EgressRefusal.UNREADABLE_RESPONSE
    assert fetched.response is None


def test_a_dns_failure_carries_which_kind_of_failure() -> None:
    """三种解析失败不是一个东西（``ports/dns.py``），理由码因此带 ``dns:`` 前缀。"""
    with _client(
        allowlist=frozenset({DECOY_HOST}),
        resolver=_FixedResolver({}),  # 什么都不解析
    ) as (client, _proxy):
        fetched = client.get(f"http://{DECOY_HOST}/")

    assert fetched.dns_failure is DnsFailureKind.NONEXISTENT
    assert fetched.reason == "dns:nonexistent"
    assert fetched.hops == (), "一次都没发出去，hops 就该是空的"


def test_a_charset_is_read_from_the_content_type() -> None:
    """编码按 ``charset`` 解；认不出来时退 utf-8 + ``replace``。

    不猜编码是**有意的**：一份 ``latin-1`` 的 sitemap 被当成 utf-8 读会静默产生替换
    字符，而下游拿它去匹配 URL 只会「找不到」。
    """
    response = GuardedResponse(
        url="http://x/",
        status=200,
        body="café".encode("latin-1"),
        content_type="text/plain; charset=latin-1",
        hops=(),
    )
    assert response.text == "café"


# ======================================================================
# 6. 判决与引用那两格的形状
# ======================================================================
def test_an_allowed_verdict_must_carry_a_response() -> None:
    with pytest.raises(ValueError, match="放行必须带一份响应"):
        GuardedFetch(allowed=True, url="http://x/")


def test_a_refusal_must_carry_exactly_one_reason() -> None:
    with pytest.raises(ValueError, match="恰好"):
        GuardedFetch(allowed=False, url="http://x/", detail="一个理由都没有")
    with pytest.raises(ValueError, match="恰好"):
        GuardedFetch(
            allowed=False,
            url="http://x/",
            host_refusal=TargetRefusal.PRIVATE_TARGET,
            scope_refusal=ScopeRefusal.NOT_APPROVED,
            detail="两个理由",
        )


def test_a_refusal_must_carry_a_human_sentence() -> None:
    with pytest.raises(ValueError, match="一句人话"):
        GuardedFetch(allowed=False, url="http://x/", host_refusal=TargetRefusal.PRIVATE_TARGET)


def test_a_refusal_must_not_carry_a_body() -> None:
    with pytest.raises(ValueError, match="不得带响应正文"):
        GuardedFetch(
            allowed=False,
            url="http://x/",
            host_refusal=TargetRefusal.PRIVATE_TARGET,
            detail="拒绝",
            response=GuardedResponse(
                url="http://x/", status=200, body=b"x", content_type="", hops=()
            ),
        )


def test_only_the_not_followed_refusal_may_carry_a_next_hop() -> None:
    with pytest.raises(ValueError, match="只有「不跟随重定向」那一格"):
        GuardedFetch(
            allowed=False,
            url="http://x/",
            egress_refusal=EgressRefusal.TRANSPORT,
            detail="拒绝",
            redirect_to="http://y/",
        )
    with pytest.raises(ValueError, match="放行不得带下一跳"):
        GuardedFetch(
            allowed=True,
            url="http://x/",
            response=GuardedResponse(
                url="http://x/", status=200, body=b"x", content_type="", hops=()
            ),
            redirect_to="http://y/",
        )


def test_limits_must_be_positive() -> None:
    for kwargs in (
        {"max_requests": 0},
        {"max_bytes": -1},
        {"max_response_bytes": 0},
    ):
        with pytest.raises(ValueError):
            EgressLimits(**kwargs)


def test_the_port_table_covers_exactly_the_allowed_schemes() -> None:
    """``_DEFAULT_PORT`` 的键集与「允许的协议」那一格相等。

    不相等时 ``_DEFAULT_PORT[scheme]`` 会 ``KeyError`` —— 那不是一次判决，是一次
    崩溃，而它出现在「判据刚刚说可以」的那个分支里。
    """
    from marketops.adapters.guarded_http import _DEFAULT_PORT

    assert set(_DEFAULT_PORT) == set(ALLOWED_SCHEMES)


def test_a_decision_ref_is_content_addressed() -> None:
    """同一个判定 → 同一个引用；不同的判定 → 不同的引用。

    这是 ``EvidenceEnvelope.egress_decision_ref`` 那一格能成立的全部理由：引用必须是
    **可复算**的（没有存储可查），而它回答的问题只有一个 —— 「这两份证据的出口判定
    是同一条吗」。
    """
    same = decision_ref("http://x/", reason="ok", detail="status=200")
    assert same == decision_ref("http://x/", reason="ok", detail="status=200")
    assert same.startswith("egress:")
    assert same != decision_ref("http://x/", reason="ok", detail="status=403")
    assert same != decision_ref("http://y/", reason="ok", detail="status=200")
    # 连过的地址属于判定的内容一部分：一个「放行了但没连」不该与「连到了某处」同引用。
    assert same != decision_ref("http://x/", reason="ok", detail="status=200", address=PUBLIC)
