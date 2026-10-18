"""浏览器那一层唯一的出口：本机回环上的正向代理。

## 为什么浏览器需要一个出口，而直连那一层不需要

``HttpxFetcher`` 的每一跳都是**我们自己**发的：先判字面量、再真的问一次 DNS、
然后把连接钉在量到的那个地址上（``http.py`` 的文件头）。浏览器没有这条链 ——
请求是 Chromium 发的：它自己解析、自己跟随重定向、自己发 iframe / worker /
子资源。逐跳插判据的做法（Playwright 的 ``context.route``）能补上「判」那一半，
补不上「钉」那一半：名字在我们判完之后、Chromium 真正连上去之前还可以换一个
答案，两次各自通过自己那一关。§7 要的是「DNS 解析与实际连接绑定」，对浏览器
只有一个位置能同时给出这两半 —— **请求的出口**。

## 它做什么

浏览器**没有**别的出口。``Crawl4AIBrowserFetcher`` 把浏览器的代理指到这里，并且
显式关掉 Chromium 对回环地址的隐式绕过（``--proxy-bypass-list=<-loopback>``）。
少了后一格，一个页面可以直接连 ``127.0.0.1:6379`` 这类本机服务，而那条路
**不会**在判据里留下任何痕迹 —— 它绕过的正是这里。

于是每一次网络动作都落到两个分支之一：

* ``CONNECT host:port``（https、wss）：先判定，再连到**解析出来的那个地址**，
  然后把字节双向转过去。TLS 是端到端握手的，本代理看不见内容。
* 绝对形式的 ``http://…`` 请求：同一个判定，连到钉住的地址，把请求行改写成
  origin-form 转过去 —— ``Host`` 头原样保留。

判定不过就**不建立那个连接**，浏览器拿到一个 403。判定由 :class:`EgressGate`
做：``check_target`` 判字面量、``HostResolver`` 真的问一次、``check_resolved``
对**每一个**地址点头（判据是「全部」而不是「至少一个」，理由见那一节）。

## 它不做什么

* **不做 TLS 中间人。** https 是隧道，代理既看不见也改不了内容。这既是能力边界
  也是取舍：一个能解密的上游会把「匿名公开浏览」变成「可读改内容」。
* **不做缓存、不做重写、不做限流。** 本模块只回答「允不允许、连到哪」。
  §5 的来源级限流与熔断住在别处，而它们必须在**出口之外**仍然共享 ——
  一次抓取起一个代理，若额度记在代理上，同一 origin 的额度会随并发数放大，
  而那正是 §5 明令不许的（「20 个账号不得对同一挑战页各烧一次」）。
* **不是通用代理。** 只认 ``http`` / ``https``，只绑回环，只被浏览器抓取器使用。
  绝对形式的 ``https://`` 请求直接拒 —— 浏览器不会那么发（它用 CONNECT），
  真出现了说明有人在手工构造，而我们没有实现 TLS 包装那一半。

## 记录

每一次判定都留下一条 :class:`EgressDecision`，**包括被拒的那些**：「这一页想做
而没做成的事」是诊断的一部分（§3 的 ``FetchAttempt`` 要能回答「这次渲染都试过
什么」）。它们只记目标与判决，不记内容、不记 cookie、不记任何请求头 ——
出口的日志里出现 cookie 的同一个理由（``ports/acquisition.py`` 的
``FetchResult`` 那一节）在这里同样成立。
"""

from __future__ import annotations

import select
import socket
import threading
from dataclasses import dataclass
from urllib.parse import urlsplit

from ..ports.dns import DnsResolutionError, HostResolver
from ..security.network import (
    TargetVerdict,
    check_resolved,
    check_target,
    is_loopback_host,
)
from .resolution import SystemResolver

__all__ = ["EgressDecision", "EgressGate", "EgressProxy", "EgressVerdict"]

#: 头部最多读到多少字节。**有意的上限**：一个不设上限的头部读取等于让对端
#: 决定我们分配多少内存，而这个代理监听在一个任何本机进程都连得到的地方。
MAX_HEAD_BYTES = 65536

#: 连上游的超时。
CONNECT_TIMEOUT_SECONDS = 10.0

#: 一条隧道在两个方向上都静默超过这么久就关掉。浏览器自己的 ``page_timeout``
#: 是更粗的那一道；这一道要挡的是「页面已经走了、而隧道还挂着」。
IDLE_TIMEOUT_SECONDS = 30.0

_BUFFER_BYTES = 65536

#: 缺省端口。CONNECT 的 authority 通常带端口，但不保证。
_DEFAULT_PORT = {"http": 80, "https": 443}


@dataclass(frozen=True)
class EgressVerdict:
    """一次出口判定的结果。

    放行必须带**地址**：没有它就没有「钉住」这回事，而一个「放行了但没说要连哪」
    的返回值会让调用方自己去解析 —— 那正是本模块存在的理由被绕开的地方。
    """

    allowed: bool
    address: str = ""
    refusal: str = ""
    detail: str = ""

    def __post_init__(self) -> None:
        if self.allowed and not self.address:
            raise ValueError("放行必须带一个地址 —— 没有它就没有「钉住」这回事")
        if not self.allowed and not self.refusal:
            raise ValueError("拒绝必须带一个理由码：只有「没通过」这三个字的报告没人能处置")


@dataclass(frozen=True)
class EgressDecision:
    """代理对**一次请求**的判决。``target`` 是 ``host:port``，不是 URL。"""

    method: str
    target: str
    port: int
    allowed: bool
    refusal: str = ""
    detail: str = ""
    address: str = ""


class EgressGate:
    """一次请求的判据：字面量 + **解析出来的每一个地址**。

    与 ``adapters/http.py`` 每一跳那三步是同一句话，只是这里没有 URL、
    也没有跳 —— 代理看到的是一个 ``(host, port)`` 对。

    ``check_target`` 要一个 URL，所以这里按 ``http`` 拼一个给它：协议在代理这一层
    由分支决定（CONNECT 只可能是 TLS，绝对形式只处理 http），而 ``check_target``
    对协议之外的判断（凭据、保留名、字面 IP、数字形态主机名）与 URL 里的其它部分
    无关。**不要**把它读成「代理允许 http」—— 那是调用方的事。
    """

    def __init__(self, resolver: HostResolver | None = None) -> None:
        self._resolver = resolver or SystemResolver()

    def check(self, host: str, port: int) -> EgressVerdict:
        literal = check_target(_probe_url(host))
        if not literal.allowed:
            return _refused(literal)

        try:
            addresses = self._resolver.resolve(host, port)
        except DnsResolutionError as exc:
            # 三种失败各有名字，而且都不是「拒绝」：``nonexistent`` 说的是这个
            # 来源可能没了，``timeout`` / ``failure`` 说的是我们这次没问到。
            # 理由码带上 ``dns:`` 前缀，这样报告上它与 §7 的拒绝码分得开。
            return EgressVerdict(
                allowed=False, refusal=f"dns:{exc.kind.value}", detail=str(exc)
            )

        resolved = check_resolved(host, addresses)
        if not resolved.allowed:
            return _refused(resolved)
        return EgressVerdict(allowed=True, address=addresses[0])


def _refused(verdict: TargetVerdict) -> EgressVerdict:
    assert verdict.refusal is not None, "拒绝必须有理由码"
    return EgressVerdict(
        allowed=False, refusal=verdict.refusal.value, detail=verdict.detail
    )


def _probe_url(host: str) -> str:
    """把 ``(host)`` 拼成一个能过 ``urllib.parse`` 的 URL。IPv6 要加方括号。"""
    literal = f"[{host}]" if ":" in host else host
    return f"http://{literal}/"


class EgressProxy:
    """把判定与连接绑在一起的那一层。绑在回环上，只被浏览器抓取器使用。

    Args:
        gate: 判据。``None`` 表示用系统解析器造一个。
        host: 绑在哪。只能是环回 —— 别的值直接抛，理由见 :func:`__init__` 里那段。
        port: 0 表示由内核挑一个（测试用），否则是固定端口。
        idle_timeout: 一条隧道两个方向都静默多久就关掉。

    Raises:
        ValueError: ``host`` 不是环回地址。

    ## 为什么非环回直接抛，而不是启动后记一条警告

    ``security/network.py`` 的 docstring 把两个方向写在一起：出网的目标必须公开，
    入网的监听必须环回。一个绑在 ``0.0.0.0`` 上的代理是**对全网开放的转发器** ——
    它会替任何连上来的人做事，而且判据里看到的 IP 是那个人的。这不是「配置不理想」，
    是一个匿名中继被打开了，所以它在构造时就失败。
    """

    def __init__(
        self,
        *,
        gate: EgressGate | None = None,
        host: str = "127.0.0.1",
        port: int = 0,
        idle_timeout: float = IDLE_TIMEOUT_SECONDS,
        connect_timeout: float = CONNECT_TIMEOUT_SECONDS,
    ) -> None:
        if not is_loopback_host(host):
            raise ValueError(
                f"出口代理只能绑在环回地址上，收到的是 {host!r}。\n"
                "  绑在别的地址上等于开了一个匿名中继：它会替任何连上来的人转发，"
                "而判据看到的来源是那个人的地址。"
            )
        self._gate = gate or EgressGate()
        self._host = host
        self._port = port
        self._idle_timeout = idle_timeout
        self._connect_timeout = connect_timeout
        self._lock = threading.Lock()
        self._decisions: list[EgressDecision] = []
        self._listener: socket.socket | None = None
        self._acceptor: threading.Thread | None = None
        self._connections: list[threading.Thread] = []
        self._stop = threading.Event()

    # ------------------------------------------------------------------
    def __enter__(self) -> EgressProxy:
        self.start()
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.stop()

    def start(self) -> None:
        family = socket.AF_INET6 if ":" in self._host else socket.AF_INET
        listener = socket.socket(family, socket.SOCK_STREAM)
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        listener.bind((self._host, self._port))
        # 0.5 秒的 accept 超时是**为了停止**：阻塞在 accept 上的线程看不见
        # ``stop()`` 设的那个标志，而 join 一个永远不返回的线程会挂住一次抓取。
        listener.settimeout(0.5)
        listener.listen(64)
        self._listener = listener
        self._port = listener.getsockname()[1]
        self._acceptor = threading.Thread(
            target=self._accept_loop, name="egress-proxy", daemon=True
        )
        self._acceptor.start()

    def stop(self) -> None:
        self._stop.set()
        if self._acceptor is not None:
            self._acceptor.join(timeout=5)
            self._acceptor = None
        if self._listener is not None:
            self._listener.close()
            self._listener = None
        with self._lock:
            pending = list(self._connections)
            self._connections.clear()
        for thread in pending:
            thread.join(timeout=1)

    # ------------------------------------------------------------------
    @property
    def url(self) -> str:
        """给浏览器配置用的那个 ``http://127.0.0.1:PORT``。"""
        if self._listener is None:
            raise RuntimeError(
                "代理还没起来 —— url 只在 start() 之后存在。\n"
                "  一个「还没监听就先给出地址」的属性会让配置指向一个没人听的端口，"
                "而那个失败在浏览器那里长得像一次网络错误。"
            )
        return f"http://{self._host}:{self._port}"

    @property
    def decisions(self) -> tuple[EgressDecision, ...]:
        """按发生顺序记录的判决，**包括被拒的那些**。"""
        with self._lock:
            return tuple(self._decisions)

    @property
    def refusals(self) -> tuple[str, ...]:
        """被拒的目标（``host:port``），按发生顺序。"""
        return tuple(d.target for d in self.decisions if not d.allowed)

    # ------------------------------------------------------------------
    def _accept_loop(self) -> None:
        listener = self._listener
        assert listener is not None, "监听套接字必须在起线程之前就绪"
        while not self._stop.is_set():
            try:
                conn, _peer = listener.accept()
            except TimeoutError:
                continue
            except OSError:
                # 监听套接字被 stop() 关掉了 —— 这是正常的退出路径。
                break
            thread = threading.Thread(
                target=self._handle_connection,
                args=(conn,),
                name="egress-proxy-conn",
                daemon=True,
            )
            with self._lock:
                self._connections.append(thread)
            thread.start()

    def _handle_connection(self, conn: socket.socket) -> None:
        try:
            conn.settimeout(self._idle_timeout)
            head, rest = _read_head(conn)
            if head is None:
                return
            request_line = head.split(b"\r\n", 1)[0]
            try:
                method, target, version = request_line.decode("iso-8859-1").split(" ", 2)
            except ValueError:
                _respond(conn, 400, "Bad Request", detail="请求行读不出来")
                return
            if method.upper() == "CONNECT":
                self._tunnel(conn, method, target)
            else:
                self._forward(conn, method, target, version, head, rest)
        except (OSError, TimeoutError):
            # 一条连接上的任何 I/O 失败都不是这一次抓取的结果：浏览器会看到
            # 它自己那一侧的错误（连接被关掉 / 502 / 403），而那才是要分类的东西。
            return
        finally:
            conn.close()

    def _tunnel(self, conn: socket.socket, method: str, authority: str) -> None:
        """``CONNECT host:port``：判定、连到钉住的地址、双向转字节。"""
        host, port = _split_authority(authority, default_port=443)
        decision = self._decide(method, host, port)
        if not decision.allowed:
            _respond(conn, 403, "Forbidden", detail=decision.detail)
            return
        upstream = self._connect_upstream(conn, decision)
        if upstream is None:
            return
        try:
            conn.sendall(b"HTTP/1.1 200 Connection Established\r\n\r\n")
            _pump(conn, upstream, self._idle_timeout)
        finally:
            upstream.close()

    def _forward(
        self,
        conn: socket.socket,
        method: str,
        target: str,
        version: str,
        head: bytes,
        rest: bytes,
    ) -> None:
        """绝对形式的 ``http://…``：同一个判定，转成 origin-form 发出去。"""
        parts = urlsplit(target)
        scheme = parts.scheme.lower()
        if scheme not in _DEFAULT_PORT:
            _respond(conn, 400, "Bad Request", detail=f"不认识的请求形式：{target!r}")
            return
        if scheme == "https":
            # 浏览器不会这么发（https 走 CONNECT）。真出现说明有人在手工构造，
            # 而我们没有实现「自己起 TLS 再转」那一半 —— 明确拒绝，不假装支持。
            _respond(
                conn,
                400,
                "Bad Request",
                detail="绝对形式的 https 请求不支持：https 走 CONNECT 隧道",
            )
            return
        host = (parts.hostname or "").lower()
        port = parts.port or _DEFAULT_PORT[scheme]
        decision = self._decide(method, host, port)
        if not decision.allowed:
            _respond(conn, 403, "Forbidden", detail=decision.detail)
            return
        upstream = self._connect_upstream(conn, decision)
        if upstream is None:
            return
        try:
            origin_form = parts.path or "/"
            if parts.query:
                origin_form += f"?{parts.query}"
            upstream.sendall(_rewritten(head, method, origin_form, version) + rest)
            _pump(conn, upstream, self._idle_timeout)
        finally:
            upstream.close()

    # ------------------------------------------------------------------
    def _connect_upstream(
        self, conn: socket.socket, decision: EgressDecision
    ) -> socket.socket | None:
        """连到**判过的那个地址**上。连不上时给浏览器一个 502 并返回 ``None``。"""
        try:
            upstream = socket.create_connection(
                (decision.address, decision.port), timeout=self._connect_timeout
            )
        except OSError as exc:
            _respond(
                conn,
                502,
                "Bad Gateway",
                detail=f"连不上 {decision.address}:{decision.port}：{exc}",
            )
            return None
        upstream.settimeout(self._idle_timeout)
        return upstream

    def _decide(self, method: str, host: str, port: int) -> EgressDecision:
        verdict = self._gate.check(host, port)
        decision = EgressDecision(
            method=method.upper(),
            target=_authority(host, port),
            port=port,
            allowed=verdict.allowed,
            refusal=verdict.refusal,
            detail=verdict.detail,
            address=verdict.address,
        )
        with self._lock:
            self._decisions.append(decision)
        return decision


# ----------------------------------------------------------------------
def _authority(host: str, port: int) -> str:
    """报告里那一格。端口一直写出来 —— 少了它，``example.co.uk:8443`` 与
    ``example.co.uk:443`` 在拒绝清单上长得一样。"""
    literal = f"[{host}]" if ":" in host else host
    return f"{literal}:{port}"


def _split_authority(authority: str, *, default_port: int) -> tuple[str, int]:
    """``host:port`` → ``(host, port)``。IPv6 字面量是 ``[::1]:443``。"""
    text = authority.strip()
    if text.startswith("["):
        host, _, tail = text[1:].partition("]")
        port = int(tail[1:]) if tail.startswith(":") and tail[1:].isdigit() else default_port
        return host.lower(), port
    host, separator, tail = text.rpartition(":")
    if not separator or not tail.isdigit():
        return text.lower(), default_port
    return host.lower(), int(tail)


def _rewritten(head: bytes, method: str, origin_form: str, version: str) -> bytes:
    """把绝对形式的请求改写成 origin-form，并把连接语义定成 ``close``。

    ``Connection: close`` 是**有意的**：响应体按 EOF 定界，于是这一层不必实现
    chunked 与 ``Content-Length`` 的两套定界。代价是每个请求一条上游连接 ——
    对一个抓取器可以接受，而「复用到上游」属于性能，不属于判据。
    """
    lines = head.split(b"\r\n")
    forwarded = [f"{method} {origin_form} {version}".encode("iso-8859-1")]
    for line in lines[1:]:
        if not line:
            continue
        name = line.split(b":", 1)[0].strip().lower()
        if name in (b"connection", b"proxy-connection", b"keep-alive"):
            continue
        forwarded.append(line)
    forwarded.append(b"Connection: close")
    return b"\r\n".join(forwarded) + b"\r\n\r\n"


def _read_head(conn: socket.socket) -> tuple[bytes | None, bytes]:
    """读到头部结束（**两对 CRLF**，也就是一个空行），返回 ``(头部, 头部之后已读到的字节)``。

    超长或对端先关掉都返回 ``None`` —— 两者都意味着这条连接上没有可判定的请求。
    """
    buffer = b""
    while True:
        chunk = conn.recv(_BUFFER_BYTES)
        if not chunk:
            return None, b""
        buffer += chunk
        end = buffer.find(b"\r\n\r\n")
        if end >= 0:
            return buffer[: end + 4], buffer[end + 4 :]
        if len(buffer) > MAX_HEAD_BYTES:
            return None, b""


def _respond(conn: socket.socket, status: int, reason: str, *, detail: str = "") -> None:
    body = (detail or reason).encode("utf-8")
    head = (
        f"HTTP/1.1 {status} {reason}\r\n"
        "Content-Type: text/plain; charset=utf-8\r\n"
        f"Content-Length: {len(body)}\r\n"
        "Connection: close\r\n\r\n"
    ).encode("iso-8859-1")
    try:
        conn.sendall(head + body)
    except OSError:
        # 对端已经不听了（浏览器取消了一个请求，很常见）。这不是一次失败，
        # 判决已经记下来了，而「报告没送到」在这里没有下游。
        return


def _pump(left: socket.socket, right: socket.socket, idle_timeout: float) -> None:
    """双向转发，直到两边都关了，或**两个方向上一段时间都没有字节**。

    半关是正常的：一端发完 ``FIN`` 之后另一个方向可能还有数据（HTTP/1.0 的
    响应就是这样）。所以一边读到 EOF 时只把对端**写方向**关掉，继续转另一边。
    """
    open_sockets: set[socket.socket] = {left, right}
    while open_sockets:
        readable, _, _ = select.select(list(open_sockets), [], [], idle_timeout)
        if not readable:
            return
        for source in readable:
            sink = right if source is left else left
            try:
                chunk = source.recv(_BUFFER_BYTES)
            except (OSError, TimeoutError):
                return
            if not chunk:
                open_sockets.discard(source)
                try:
                    sink.shutdown(socket.SHUT_WR)
                except OSError:
                    return
                continue
            try:
                sink.sendall(chunk)
            except OSError:
                return
