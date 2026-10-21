"""浏览器那一层的受管出口（``adapters/egress_proxy.py``）。

## 这里测的是一件**已经发生**的事，不是一件打算做的事

采集设计 §7 逐字：「所有URL（初始、重定向、浏览器主导航、iframe/worker 请求和子资源）
在网络动作前检查……DNS 解析与实际连接绑定」，并且点明了「不能只保留『最终URL不合规就不存』
宣称已安全」。``Crawl4AIBrowserFetcher`` 现在把浏览器的 ``--proxy-server`` 指到
:class:`~marketops.adapters.egress_proxy.EgressProxy`，于是那个「在网络动作前」与
「绑定」由这个文件里的判据回答 —— 而不是由一段注释回答。

## 为什么代理那一半用一个替身判据，而不是真的 ``EgressGate``

因为**真的判据会（正确地）拒绝这些测试唯一能安全立起来的目标**：测试的本机 origin 在
``127.0.0.1`` 上，而 ``check_resolved`` 拒绝一切非全局可路由地址；§7 末句又明确禁止把
真实内网或云 metadata 当靶子（「测试用隔离受控目标，不向真实内网或云 metadata 探测」）。
两者合起来只剩一条路：**代理那几格注入一个替身判据**，把「判据说了什么」与
「代理拿着判据的结论做了什么事」分开测。

于是本文件分成两半，各自回答一个问题：

* ``EgressGate`` —— 判据**自己**的判据（字面量优先于解析、解析到内网要拒、
  DNS 失败有自己的理由码）。全程零套接字，注入的解析器说它解析出了什么。
* ``EgressProxy`` —— 拿到判据的结论之后**做了什么**：拒了就不建连接、放行就连到
  判过的那个地址、隧道与转发两条路都过判据、判决被按顺序记下来。

这一半的替身判据**不是**为了绕开真判据，它把这个事实摆明：真判据的最后一句是
「地址必须全局可路由」，而本机测试恰好造不出这样一个地址。
"""

from __future__ import annotations

import socket
import threading

import pytest

from marketops.adapters.egress_proxy import (
    MAX_HEAD_BYTES,
    EgressGate,
    EgressProxy,
    EgressVerdict,
)
from marketops.ports.dns import DnsFailureKind, DnsResolutionError, HostResolver

#: 一次测试用的公开地址。``93.184.216.34`` 是 ``example.com`` 的真实地址，
#: 也是 ``tests/test_network_policy.py`` 用的那个正控 —— 换一个值会让两个文件
#: 对「什么叫全局可路由」有两种说法。
_PUBLIC = "93.184.216.34"


# ----------------------------------------------------------------------
# 替身判据与受控 origin
# ----------------------------------------------------------------------
class _StubGate:
    """一个说死了的判据。它存在的理由是让「代理照判据的结论做了什么」可观测。"""

    upstream = None

    def __init__(self, verdict: EgressVerdict) -> None:
        self._verdict = verdict
        self.asked: list[tuple[str, int]] = []

    def check(self, host: str, port: int) -> EgressVerdict:
        self.asked.append((host, port))
        return self._verdict


def _allow_at(address: str = "127.0.0.1") -> _StubGate:
    return _StubGate(EgressVerdict(allowed=True, address=address))


def _refuse_with(refusal: str = "private_target", detail: str = "目标是内网地址") -> _StubGate:
    return _StubGate(EgressVerdict(allowed=False, refusal=refusal, detail=detail))


class _Origin:
    """一个受控的本机 origin：只绑回环，只在测试生命周期内存在。

    ``mode="http"`` 读一次请求头并回一段固定响应；``mode="echo"`` 把读到的字节原样
    发回去（隧道那一格要的正是这个 —— 它证明字节真的过去了，而不是我们只回了
    一个 ``200 Connection Established`` 就关掉）。

    ``accepts`` 是**关键的那个计数器**：被拒绝的请求不能在这里留下任何一次连接。
    只断言「浏览器拿到 403」是不够的 —— 一个先连上去再回 403 的实现会通过那条断言，
    而它已经发出了我们明令不许发出的那个请求。
    """

    def __init__(self, *, mode: str = "http") -> None:
        self._mode = mode
        self._socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._socket.bind(("127.0.0.1", 0))
        self._socket.listen(8)
        self._socket.settimeout(0.5)
        self._stop = threading.Event()
        self.accepts = 0
        self.requests: list[bytes] = []
        self.port: int = self._socket.getsockname()[1]
        self._thread = threading.Thread(target=self._serve, daemon=True)
        self._thread.start()

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
                data = conn.recv(65536)
                self.requests.append(data)
                if self._mode == "echo":
                    conn.sendall(data)
                else:
                    conn.sendall(
                        b"HTTP/1.1 200 OK\r\n"
                        b"Content-Type: text/plain\r\n"
                        b"Content-Length: 2\r\n"
                        b"Connection: close\r\n\r\nok"
                    )
            except OSError:
                pass
            finally:
                conn.close()

    def close(self) -> None:
        self._stop.set()
        self._socket.close()
        self._thread.join(timeout=2)


@pytest.fixture
def origin() -> object:
    server = _Origin()
    yield server
    server.close()


@pytest.fixture
def echo_origin() -> object:
    server = _Origin(mode="echo")
    yield server
    server.close()


# ----------------------------------------------------------------------
# 客户端那一侧的两个小动作。写出来是因为「读到 EOF」与「读到超时」是两件事，
# 而这里的判据大多要的是前者。
# ----------------------------------------------------------------------
def _read_all(sock: socket.socket, *, limit: int = 65536) -> bytes:
    chunks: list[bytes] = []
    total = 0
    while total < limit:
        chunk = sock.recv(4096)
        if not chunk:
            break
        chunks.append(chunk)
        total += len(chunk)
    return b"".join(chunks)


def _dial(proxy: EgressProxy) -> socket.socket:
    sock = socket.create_connection(("127.0.0.1", int(proxy.url.rsplit(":", 1)[1])))
    sock.settimeout(5.0)
    return sock


# ======================================================================
# 第一半：EgressGate —— 判据自己
# ======================================================================
class _FakeResolver(HostResolver):
    def __init__(
        self,
        *,
        addresses: tuple[str, ...] = (),
        error: DnsResolutionError | None = None,
    ) -> None:
        self._addresses = addresses
        self._error = error
        self.calls: list[tuple[str, int]] = []

    def resolve(self, host: str, port: int) -> tuple[str, ...]:
        self.calls.append((host, port))
        if self._error is not None:
            raise self._error
        return self._addresses


def test_the_gate_judges_the_literal_before_it_asks_dns() -> None:
    """字面量那一关先过，**过了才去问解析**。

    顺序不是风格问题：一个先解析再判字面量的实现，会对
    ``169.254.169.254`` 发出一次 DNS 查询 —— 一次我们明确不该发出的网络动作，
    而它不产生任何判决，只在解析器的日志里留下痕迹。
    """
    resolver = _FakeResolver(addresses=(_PUBLIC,))
    gate = EgressGate(resolver=resolver)

    verdict = gate.check("127.0.0.1", 80)

    assert not verdict.allowed
    assert verdict.refusal == "private_target"
    assert resolver.calls == [], "字面量已经拒了，不该再去问 DNS"


def test_the_gate_refuses_an_ipv6_literal_without_asking_dns() -> None:
    """``::1`` 走的是同一条路。顺带钉住 ``_probe_url`` 的方括号处理 ——
    少了它，``http://::1/`` 会被 ``urlsplit`` 解析成一个没有主机的 URL，
    于是这条判据从一个「内网地址」的拒绝变成一次「没有主机」的拒绝。"""
    resolver = _FakeResolver(addresses=(_PUBLIC,))
    gate = EgressGate(resolver=resolver)

    verdict = gate.check("::1", 443)

    assert not verdict.allowed
    assert verdict.refusal == "private_target"
    assert resolver.calls == []


def test_a_name_resolving_into_private_space_is_refused() -> None:
    gate = EgressGate(resolver=_FakeResolver(addresses=("169.254.169.254",)))

    verdict = gate.check("client27.example.org", 443)

    assert not verdict.allowed
    assert verdict.refusal == "resolves_to_private"
    assert "169.254.169.254" in verdict.detail, "报告里要写出**实测到**的那个地址"


def test_one_bad_address_among_good_ones_refuses_the_request() -> None:
    """判据是「全部」而不是「至少一个」。两个顺序都测：只看第一个地址的实现
    会在第二种顺序上放行，然后在轮到那个地址时把它连上。"""
    for addresses in (("169.254.169.254", _PUBLIC), (_PUBLIC, "10.0.0.7")):
        verdict = EgressGate(resolver=_FakeResolver(addresses=addresses)).check(
            "client27.example.org", 443
        )
        assert not verdict.allowed, f"{addresses} 里有一个不是全局可路由地址，却放行了"
        assert verdict.refusal == "resolves_to_private"


def test_an_allowed_name_pins_the_first_address() -> None:
    """放行必须带地址，而且带的是**第一个** —— 它就是之后要连的那个。
    一个「放行了但没说连哪」的返回值会让调用方自己再解析一次，
    而那正是「解析与实际连接绑定」被绕开的地方。"""
    verdict = EgressGate(resolver=_FakeResolver(addresses=(_PUBLIC, "93.184.216.35"))).check(
        "client27.example.org", 443
    )

    assert verdict.allowed
    assert verdict.address == _PUBLIC


def test_a_dns_failure_is_its_own_reason_code() -> None:
    """「这个名字不存在」与「我们这次没问到」不是一件事（``ports/dns.py``），
    而两者都**不是** §7 的拒绝 —— 所以理由码带上 ``dns:`` 前缀，让它与
    ``resolves_to_private`` 那种判决在报告上分得开。"""
    resolver = _FakeResolver(
        error=DnsResolutionError("gone.co.uk", DnsFailureKind.NONEXISTENT, "NXDOMAIN")
    )
    gate = EgressGate(resolver=resolver)

    verdict = gate.check("gone.co.uk", 443)

    assert not verdict.allowed
    assert verdict.refusal == "dns:nonexistent"
    assert verdict.detail
    assert resolver.calls == [("gone.co.uk", 443)]


def test_a_verdict_without_an_address_is_a_programming_error() -> None:
    """``EgressVerdict`` 的两条不变量。它们是这个模块唯一一处**能自己发现**
    判据写错了的地方：一个「放行但没说连哪」的判决在下游表现成一次无关的网络错误。"""
    with pytest.raises(ValueError, match="地址"):
        EgressVerdict(allowed=True)
    with pytest.raises(ValueError, match="理由码"):
        EgressVerdict(allowed=False)


# ======================================================================
# 第二半：EgressProxy —— 拿到结论之后做了什么
# ======================================================================
def test_the_proxy_refuses_to_bind_anything_but_loopback() -> None:
    """绑在非环回地址上等于开了一个匿名中继，所以它在构造时就失败。"""
    for host in ("0.0.0.0", "::", "192.168.1.5", "10.0.0.1", "example.com"):
        with pytest.raises(ValueError, match="环回"):
            EgressProxy(host=host)


def test_the_url_only_exists_after_start() -> None:
    """没起来就没有地址。一个「还没监听就先给出地址」的属性会让浏览器配置指向
    一个没人听的端口，而那个失败在浏览器那里长得像一次网络错误。"""
    proxy = EgressProxy()

    with pytest.raises(RuntimeError, match="start"):
        _ = proxy.url

    proxy.start()
    try:
        assert proxy.url.startswith("http://127.0.0.1:")
    finally:
        proxy.stop()


def test_a_refused_target_gets_a_403_and_never_reaches_the_origin(origin: _Origin) -> None:
    """§7 的核心那一句：**在网络动作前**。断言分两层 —— 浏览器看到 403，
    而 origin 上**一次连接都没有**。只测前者会让一个「先连上再回 403」的实现
    通过，而那个实现已经发出了我们不许发出的那个请求。

    靶子是一个**真的连得上的**本机服务（``127.0.0.1:<origin 的端口>``），
    不是编出来的名字 —— 这一点是这一格的承重处：在编出来的名字上，「先连上再回
    403」会撞一次 gaierror，看起来也「没有连上去」，于是 ``accepts == 0`` 就成了一句
    恰好成立的话。在这里它会**真的连到** origin，``accepts`` 变成 1。
    """
    gate = _refuse_with("private_target", "目标是内网地址")
    proxy = EgressProxy(gate=gate, idle_timeout=2.0)
    proxy.start()
    try:
        sock = _dial(proxy)
        sock.sendall(
            f"GET http://127.0.0.1:{origin.port}/page HTTP/1.1\r\n"
            f"Host: 127.0.0.1:{origin.port}\r\n\r\n".encode()
        )
        response = _read_all(sock)
        sock.close()
    finally:
        proxy.stop()

    assert response.startswith(b"HTTP/1.1 403"), response[:120]
    assert origin.accepts == 0, "被拒的请求在 origin 上留下了连接 —— 它已经发出去了"
    assert origin.requests == []
    assert gate.asked == [("127.0.0.1", origin.port)]


def test_a_refusal_is_recorded_with_its_target_and_its_reason() -> None:
    """「这一页想做而没做成的事」是诊断的一部分，所以被拒的那些也要留痕 ——
    而且留的是 ``host:port``：少了端口，``x.co.uk:8443`` 与 ``x.co.uk:443``
    在拒绝清单上长得一样。"""
    proxy = EgressProxy(gate=_refuse_with("resolves_to_private"), idle_timeout=2.0)
    proxy.start()
    try:
        sock = _dial(proxy)
        sock.sendall(b"CONNECT x.co.uk:8443 HTTP/1.1\r\nHost: x.co.uk:8443\r\n\r\n")
        _read_all(sock)
        sock.close()
    finally:
        proxy.stop()

    assert proxy.refusals == ("x.co.uk:8443",)
    decision = proxy.decisions[0]
    assert decision.method == "CONNECT"
    assert decision.port == 8443
    assert decision.refusal == "resolves_to_private"
    assert decision.address == "", "没放行就没有地址"


def test_an_allowed_request_reaches_the_address_the_gate_gave(origin: _Origin) -> None:
    """**钉住**的那一格。请求行里的主机名是本机解析不出来的（``.invalid``），
    所以这一次真的连上了 origin 这件事本身，就说明连的是判据给的那个地址 ——
    照着名字解析只会得到一次 gaierror 与一个 502。"""
    gate = _allow_at("127.0.0.1")
    proxy = EgressProxy(gate=gate, idle_timeout=2.0)
    proxy.start()
    try:
        sock = _dial(proxy)
        sock.sendall(
            f"GET http://origin.invalid:{origin.port}/page?x=1 HTTP/1.1\r\n"
            f"Host: origin.invalid:{origin.port}\r\n"
            "Accept: text/html\r\n\r\n".encode()
        )
        response = _read_all(sock)
        sock.close()
    finally:
        proxy.stop()

    assert response.startswith(b"HTTP/1.1 200"), response[:120]
    assert origin.requests, "origin 没收到任何请求"
    head = origin.requests[0]
    # 请求行被改写成 origin-form，而 ``Host`` 原样保留 —— 上游看到的仍然是它被
    # 请求的那个名字。少了这一条，一个虚拟主机站点会回错站点的内容，或者更糟：
    # 回一份「正常」的内容，而它属于另一个站。
    assert head.startswith(b"GET /page?x=1 HTTP/1.1\r\n"), head[:120]
    assert f"Host: origin.invalid:{origin.port}\r\n".encode() in head
    assert gate.asked == [("origin.invalid", origin.port)]
    assert proxy.decisions[0].address == "127.0.0.1"


def test_a_connect_request_becomes_a_tunnel(echo_origin: _Origin) -> None:
    """https 那条路：CONNECT 过判据、建隧道、字节双向转。
    这里要证明的是**字节真的过去了**，而不是我们回了一句
    ``200 Connection Established`` 就把两端都关掉。"""
    proxy = EgressProxy(gate=_allow_at("127.0.0.1"), idle_timeout=2.0)
    proxy.start()
    try:
        sock = _dial(proxy)
        sock.sendall(
            f"CONNECT origin.invalid:{echo_origin.port} HTTP/1.1\r\n\r\n".encode()
        )
        head = sock.recv(1024)
        assert head.startswith(b"HTTP/1.1 200"), head[:120]
        sock.sendall(b"hello-through-the-tunnel")
        assert sock.recv(1024) == b"hello-through-the-tunnel"
        sock.close()
    finally:
        proxy.stop()

    assert proxy.decisions[0].allowed


def test_a_connect_without_a_port_defaults_to_443() -> None:
    """``CONNECT host`` 不带端口是合法的，缺省是 443。判据为此要一个端口
    （``HostResolver.resolve`` 的签名里有它），所以这条路必须走通，
    而不是把 ``None`` 或 ``0`` 递进判据。"""
    gate = _refuse_with("private_target")
    proxy = EgressProxy(gate=gate, idle_timeout=2.0)
    proxy.start()
    try:
        sock = _dial(proxy)
        sock.sendall(b"CONNECT x.co.uk HTTP/1.1\r\n\r\n")
        _read_all(sock)
        sock.close()
    finally:
        proxy.stop()

    assert gate.asked == [("x.co.uk", 443)]
    assert proxy.decisions[0].target == "x.co.uk:443"


def test_an_ipv6_authority_keeps_its_brackets() -> None:
    """``[::1]:443`` 要**连着方括号**进报告：``::1:443`` 是一个读不出主机与端口的
    字符串，而拒绝清单上那一行正是给下一班人看的。"""
    proxy = EgressProxy(gate=_refuse_with("private_target"), idle_timeout=2.0)
    proxy.start()
    try:
        sock = _dial(proxy)
        sock.sendall(b"CONNECT [::1]:443 HTTP/1.1\r\n\r\n")
        _read_all(sock)
        sock.close()
    finally:
        proxy.stop()

    assert proxy.decisions[0].target == "[::1]:443"


def test_absolute_form_https_is_refused_and_never_reaches_the_origin(origin: _Origin) -> None:
    """浏览器不会这么发（https 走 CONNECT）。真出现说明有人在手工构造，
    而我们没有实现「自己起 TLS 再转」那一半 —— 明确拒绝，不假装支持。"""
    proxy = EgressProxy(gate=_allow_at("127.0.0.1"), idle_timeout=2.0)
    proxy.start()
    try:
        sock = _dial(proxy)
        sock.sendall(b"GET https://origin.invalid/ HTTP/1.1\r\nHost: origin.invalid\r\n\r\n")
        response = _read_all(sock)
        sock.close()
    finally:
        proxy.stop()

    assert response.startswith(b"HTTP/1.1 400"), response[:120]
    assert origin.accepts == 0
    assert proxy.decisions == (), "这一条根本没进判据，不该留下判决"


def test_an_unparsable_request_line_is_refused_not_judged() -> None:
    proxy = EgressProxy(gate=_allow_at(), idle_timeout=2.0)
    proxy.start()
    try:
        sock = _dial(proxy)
        sock.sendall(b"NOT-A-REQUEST-LINE\r\n\r\n")
        response = _read_all(sock)
        sock.close()
    finally:
        proxy.stop()

    assert response.startswith(b"HTTP/1.1 400")
    assert proxy.decisions == ()


def test_an_oversized_head_is_dropped_without_a_verdict() -> None:
    """头部有上限，超了就直接丢连接。一个不设上限的头部读取等于让对端决定我们
    分配多少内存 —— 而这个代理监听在任何本机进程都连得到的地方。

    判决是空的：这一条连接上**没有**一个可判定的请求，编一条判决出来会让
    「有人塞了 64 KB 的请求头」在报告上长得像一次被拒的抓取。
    """
    proxy = EgressProxy(gate=_allow_at(), idle_timeout=2.0)
    proxy.start()
    try:
        sock = _dial(proxy)
        blob = b"X-Pad: " + b"a" * MAX_HEAD_BYTES
        try:
            sock.sendall(blob)
        except OSError:
            pass  # 代理先关了连接，sendall 可能撞上 —— 这本身是预期的
        assert _read_all(sock) == b""
        sock.close()
    finally:
        proxy.stop()

    assert proxy.decisions == ()


def test_decisions_keep_the_order_they_happened_in() -> None:
    """顺序是内容的一部分：先放行后拒绝与先拒绝后放行讲的是两个不同的故事，
    而一份排过序的清单会把这个差别抹掉。"""
    gate = _StubGate(EgressVerdict(allowed=False, refusal="private_target"))
    proxy = EgressProxy(gate=gate, idle_timeout=2.0)
    proxy.start()
    try:
        for target in ("a.co.uk:443", "b.co.uk:8443", "c.co.uk:80"):
            sock = _dial(proxy)
            sock.sendall(f"CONNECT {target} HTTP/1.1\r\n\r\n".encode())
            _read_all(sock)
            sock.close()
    finally:
        proxy.stop()

    assert [d.target for d in proxy.decisions] == ["a.co.uk:443", "b.co.uk:8443", "c.co.uk:80"]
    assert proxy.refusals == ("a.co.uk:443", "b.co.uk:8443", "c.co.uk:80")


def test_the_listening_port_is_closed_after_stop() -> None:
    """``stop()`` 之后端口必须真的不通。一次抓取起一个代理，一个没关掉的监听
    会把「本机任何进程都能连的出口」留在那里 —— 而它属于一次早就结束的抓取。"""
    proxy = EgressProxy(gate=_allow_at(), idle_timeout=2.0)
    proxy.start()
    port = int(proxy.url.rsplit(":", 1)[1])
    proxy.stop()

    with pytest.raises(OSError):
        socket.create_connection(("127.0.0.1", port), timeout=2.0).close()
