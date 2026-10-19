#!/usr/bin/env python3
"""W-E 的**真实本地**端到端 harness：真 Chromium + A 的真受管出口 + 合成夹具。

## 它证明的是什么，不证明的是什么

**证明：** 本机的 crawl4ai + Chromium 在**经过唯一受管出口**的前提下，
真的能启动、真的执行 JS、真的截图、真的做结构化抽取；**并且**浏览器发起的
每一类网络动作（顶层导航、重定向、``<img>``、``<iframe>``、``Worker``、
``fetch``、``WebSocket``）在被出口拒绝时，诱饵服务上**一次连接都没有**。

**不证明：** 出口**判据本身**是对的（那在 W-A 的 ``tests/test_wave1_egress_*.py``）、
公开网站可达、厂商 Cloud、付费调用、真实账号。**本 harness 不碰公网**：
夹具服务与诱饵都在 ``127.0.0.1`` 上，合成 DNS 不向真实解析器提问。

## 三条不许（写在代码里，不写在文档里）

1. **浏览器永远有出口。** 这里**没有**「先起一个不带代理的浏览器看看」这条路径 ——
   本模块把 A 的 ``SafeEgressSession.browser_config_kwargs()`` 原样交给
   :class:`~research_adapter_crawl4ai.Crawl4AIAdapter`，而 adapter 里没有第二条路。
2. **不用环境变量关掉代理。** 正向用例走的是 A 自己提供的**可注入判据**
   （``open_session(gate=…)``），而不是把代理关掉：请求**仍然**经过代理，
   判据仍然被判。见 :class:`FixtureGate` 的说明。
3. **诱饵不在真实内网、也不是云 metadata。** 靶子是本进程自己起的回环服务，
   合成解析器对任何**不认识**的名字直接抛 ``NONEXISTENT``，不问系统解析器。

## 为什么正向用例需要一个「只对夹具点头」的判据

真判据会（**正确地**）拒绝 ``127.0.0.1``，而本机唯一能安全立起来的靶子就在那里；
§7 又禁止拿真实内网当靶子。所以「判据说了什么」与「拿着判据的结论做了什么事」
只能分开测：**拒绝**方向用**真判据**（那是承重的那一半），**放行**方向用
:class:`FixtureGate` —— 它只对一个 ``host:port`` 点头，其余每一个请求
（包括夹具页自己发起的每一个子资源）都**照走真判据**。这一格不是「放宽」，
它是一条**窄到可以读一遍就看完**的缝；判据自己仍然在链条里。

## 依赖状态：缺依赖不是产品缺陷

:func:`dependency_state` 把「能不能跑」拆成各格，并给一个**闭集**结论。
一个把「没装浏览器」报成「抓取失败」的 harness 会把部署问题读成站点问题 ——
这正是这一格存在的理由。
"""
from __future__ import annotations

import argparse
import hashlib
import http.server
import importlib
import importlib.util
import json
import os
import socketserver
import threading
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator, Mapping, Sequence
from urllib.parse import urlsplit

PROVIDER = "crawl4ai"

#: 真实本地 E2E 的**显式开关**。缺省关：一次浏览器启动要几秒，而共享机器上的
#: 并行全量测试会因为多了一个 subprocess 而彼此争抢 —— 那不是回归信号。
LOCAL_CRAWL_E2E_ENV = "MARKETOPS_LOCAL_CRAWL_E2E"

#: 指向 W-A 的 ``scripts/`` 目录（装 ``research_egress`` 与 ``marketops_safe_egress`` 的地方）。
#: **没有缺省值**：一个写死在代码里的兄弟 worktree 路径，只对写它的人所在的机器成立。
SAFE_EGRESS_SCRIPTS_ENV = "MARKETOPS_SAFE_EGRESS_SCRIPTS"

#: 夹具与诱饵用的合成名字。``.example`` 是 RFC 2606 保留的，永远不会被真实的
#: 分发给出去 —— 一个「万一合成解析器没接上」的请求也不会打到一台真机器上。
FIXTURE_HOST = "fixture.example"
DECOY_HOST = "decoy.example"

#: 依赖状态的**闭集**。每一格都对应一件不同的修复动作。
DEPENDENCY_VERDICTS: tuple[str, ...] = (
    "READY",
    "SAFE_EGRESS_NOT_IMPORTABLE",
    "CRAWL4AI_NOT_IMPORTABLE",
    "CHROMIUM_NOT_INSTALLED",
    "PLAYWRIGHT_NOT_IMPORTABLE",
)


# ---------------------------------------------------------------------------
# 依赖状态
# ---------------------------------------------------------------------------

def _module_available(name: str) -> bool:
    try:
        return importlib.util.find_spec(name) is not None
    except (ImportError, ValueError):
        return False


def chromium_state(playwright_module: Any | None = None) -> tuple[bool, str]:
    """本机有没有一个 Playwright 认识的 Chromium。**不启动它。**

    返回 ``(存在?, 路径或原因)``。用 ``playwright`` 自己的答案而不是我们猜一个
    缓存目录：crawl4ai 走的就是那条解析，猜出来的路径与它实际用的可以不同。
    """
    playwright = playwright_module
    if playwright is None:
        if not _module_available("playwright.sync_api"):
            return False, "playwright_not_importable"
        playwright = importlib.import_module("playwright.sync_api")
    try:
        with playwright.sync_playwright() as p:
            path = p.chromium.executable_path
    except Exception as exc:  # noqa: BLE001 - 任何启动期失败都等价于「拿不到」
        return False, f"playwright_error:{type(exc).__name__}"
    return (Path(path).is_file(), path)


def _ensure_safe_egress_path() -> None:
    """把 :data:`SAFE_EGRESS_SCRIPTS_ENV` 指的目录放进 ``sys.path``（幂等）。

    **为什么一个「报告」函数会改 ``sys.path``：** 因为不这样，报告与
    :func:`load_safe_egress` 会**用两个量具量同一件事** —— 报告说
    ``SAFE_EGRESS_NOT_IMPORTABLE``、加载器却成功。一份与真实执行不一致的
    状态报告比没有报告更坏：它会把一次能跑的验收读成环境缺失。
    """
    import sys

    extra = os.environ.get(SAFE_EGRESS_SCRIPTS_ENV, "")
    if extra and extra not in sys.path:
        sys.path.insert(0, extra)


def dependency_state(*, module_import=None, playwright_module: Any | None = None) -> dict:
    """跑得起来吗？**分开报每一格**，并给一个闭集结论与一句该做什么。

    ``module_import`` 可以被注入，用来判「结论是怎么算出来的」而不必真的卸掉依赖。
    """
    if module_import is None:
        _ensure_safe_egress_path()
    load = module_import or importlib.util.find_spec
    safe_ok = bool(load("marketops_safe_egress"))
    egress_ok = bool(load("research_egress"))
    crawl_ok = bool(load("crawl4ai"))

    chromium_ok, chromium_detail = (False, "not_checked")
    if crawl_ok:
        chromium_ok, chromium_detail = chromium_state(playwright_module)

    if not (safe_ok and egress_ok):
        verdict = "SAFE_EGRESS_NOT_IMPORTABLE"
        action = (f"把 W-A 的 scripts 目录指给 {SAFE_EGRESS_SCRIPTS_ENV}"
                  "（那里装着 research_egress 与 marketops_safe_egress）。")
    elif not crawl_ok:
        verdict = "CRAWL4AI_NOT_IMPORTABLE"
        action = "在这个解释器里装 crawl4ai（requirements-crawl-local.txt）。"
    elif not chromium_ok:
        verdict = "CHROMIUM_NOT_INSTALLED"
        action = f"装 Playwright 的 Chromium：playwright install chromium（当前解析到 {chromium_detail}）。"
    else:
        verdict = "READY"
        action = ""
    assert verdict in DEPENDENCY_VERDICTS

    return {
        "verdict": verdict,
        "action": action,
        "safe_egress_importable": safe_ok,
        "research_egress_importable": egress_ok,
        "crawl4ai_importable": crawl_ok,
        "chromium_present": chromium_ok,
        "chromium_path": chromium_detail,
        "opt_in": os.environ.get(LOCAL_CRAWL_E2E_ENV) == "1",
        "safe_egress_scripts_env": SAFE_EGRESS_SCRIPTS_ENV,
        "safe_egress_scripts_set": bool(os.environ.get(SAFE_EGRESS_SCRIPTS_ENV)),
    }


def load_safe_egress(*, path: str | None = None) -> tuple[Any, Any]:
    """取到 ``(marketops_safe_egress, research_egress)`` 两个模块。

    顺序：**先看 sys.path**（集成之后两者就在同一个目录里），再看
    :data:`SAFE_EGRESS_SCRIPTS_ENV` 指的那个目录。两条都不成立就抛 ——
    缺依赖必须是一句可以说出修复动作的话，不是一个 ``ImportError`` 回溯。
    """
    import sys

    def _try() -> tuple[Any, Any] | None:
        try:
            return (importlib.import_module("marketops_safe_egress"),
                    importlib.import_module("research_egress"))
        except ModuleNotFoundError:
            return None

    found = _try()
    if found:
        return found
    extra = path if path is not None else os.environ.get(SAFE_EGRESS_SCRIPTS_ENV, "")
    if extra:
        if extra not in sys.path:
            sys.path.insert(0, extra)
        found = _try()
        if found:
            return found
    raise ModuleNotFoundError(safe_egress_missing_hint(), name="research_egress")


def safe_egress_missing_hint() -> str:
    """取不到安全出口依赖时**那一句要人做的事**。

    单独成函数是为了让它**可以被判**：模块一旦进过 ``sys.modules``，
    「加载器会不会抛」就恒为假，于是负向判据只能判这句话本身。
    """
    return (
        f"research_egress / marketops_safe_egress 都取不到。设 {SAFE_EGRESS_SCRIPTS_ENV}"
        "为 W-A 的 scripts 目录（绝对路径），或把它们装在同一个 sys.path 上。"
    )


def chromium_version(executable: str | None = None) -> str:
    """`chrome --version` 的那一行。**不联网、不建浏览器上下文。**

    为什么要它：验收回执里「Chromium 1243」是**目录名**，而目录名不是版本
    （``build-revision-name-describes-head-not-content`` 那一类）。真正跑的是哪个
    二进制，要问那个二进制自己。失败返回空串，不抛 —— 版本读不到不该让一次
    验收变成一次异常。
    """
    import subprocess

    exe = executable
    if exe is None:
        ok, detail = chromium_state()
        exe = detail if ok else None
    if not exe:
        return ""
    try:
        out = subprocess.run([exe, "--version"], capture_output=True, text=True,
                             timeout=10, check=False)
    except (OSError, subprocess.SubprocessError):
        return ""
    return (out.stdout or out.stderr or "").strip()


class ConnectionCounter:
    """一个**只数连接**的 TCP listener。用来证「零连接」。

    它不实现任何协议：accept 一次记一次，然后立刻关掉。存在的理由只有一条 ——
    「出口拒绝了它」与「根本没有连过去」在报告上同形，而只有后者能由这个计数器
    与前者区分开。**它不响应任何请求**，所以它也不可能把一个被拒的请求变成成功。
    """

    def __init__(self) -> None:
        self.connections: list[str] = []
        self._sock: Any = None
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()

    def start(self) -> "ConnectionCounter":
        import socket

        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind(("127.0.0.1", 0))
        sock.listen(16)
        sock.settimeout(0.2)
        self._sock = sock
        self._thread = threading.Thread(target=self._accept_loop, daemon=True,
                                        name="crawl4ai-conn-counter")
        self._thread.start()
        return self

    @property
    def port(self) -> int:
        assert self._sock is not None, "counter 还没 start()"
        return int(self._sock.getsockname()[1])

    def _accept_loop(self) -> None:
        while not self._stop.is_set():
            try:
                conn, addr = self._sock.accept()
            except (TimeoutError, OSError):
                continue
            self.connections.append(f"{addr[0]}:{addr[1]}")
            try:
                conn.close()
            except OSError:
                pass

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=5)
            self._thread = None
        if self._sock is not None:
            try:
                self._sock.close()
            finally:
                self._sock = None

    def __enter__(self) -> "ConnectionCounter":
        return self.start()

    def __exit__(self, *exc_info: object) -> None:
        self.stop()


# ---------------------------------------------------------------------------
# 合成夹具
# ---------------------------------------------------------------------------

#: 夹具页。**够长**是必需的：crawl4ai 0.9.3 有一套内容启发式会把「页小、可见文本少、
#: 没有语义元素」判成被反爬挡下（见 :func:`research_adapter_crawl4ai._sdk_failure_code`），
#: 而那与「页面没取到」是两件事。真实页面自然满足，夹具必须**也**满足 ——
#: 否则这条 harness 会以自己的假页面为理由，把一个产品行为读成一次失败。
FIXTURE_BODY = """<h1 id="title">Synthetic Fixture Page</h1>
<p class="intro">This fixture exists so a real browser has a document with enough
visible text to be treated as a page rather than as a block shell. It carries several
paragraphs of ordinary prose, a small table, a contact card and a handful of links,
which is what an everyday business document looks like when it renders.</p>
<div id="org" data-id="42">Example Coatings Ltd</div>
<span class="contact">+44 20 7946 0958</span>
<a class="email" href="mailto:info@example.org">info@example.org</a>
<table><tr><th>Field</th><th>Value</th></tr><tr><td>region</td><td>uk</td></tr></table>
<ul class="items"><li>alpha item</li><li>beta item</li></ul>
<a href="/alpha">Alpha section</a>
<a href="/beta">Beta section</a>
<a href="/gamma">Gamma section</a>
<a href="/delta">Delta section</a>
<div id="render"></div>
<script>
  document.getElementById('render').textContent = 'JS_RENDER_MARK';
  // 每一个子资源都必须被出口拒绝。它们是**这个 fixture 的全部意义**：
  // 判据在浏览器发起的请求上有没有生效，只有在浏览器真的发起它们时才看得出来。
  try { var i = new Image(); i.src = 'http://DECOY/pixel.png'; document.body.appendChild(i); } catch (e) {}
  try { var f = document.createElement('iframe'); f.src = 'http://DECOY/frame'; document.body.appendChild(f); } catch (e) {}
  try { new Worker('http://DECOY/worker.js'); } catch (e) {}
  try { fetch('http://DECOY/api'); } catch (e) {}
  try { new WebSocket('ws://DECOY/socket'); } catch (e) {}
</script>
"""

#: 与 :data:`FIXTURE_BODY` 同形，但**不发起任何子资源**。它存在的理由：
#: 合法页面的 PDF 会把被拒绝的 iframe 一起渲染进去，于是产物里出现出口的拒绝说明 ——
#: 那是**准确的**（浏览器确实显示了它），但要把「页面自己的字」与「出口的字」
#: 分开量，就需要一个干净的对照页。
CLEAN_BODY = FIXTURE_BODY.split("<script>")[0] + """
<div id="render"></div>
<script>
  document.getElementById('render').textContent = 'JS_RENDER_MARK';
</script>
"""

REDIRECT_BODY = ('<!doctype html><html><head><meta http-equiv="refresh" '
                 'content="0;url=http://DECOY/after"></head><body>'
                 'This page asks the browser to go somewhere else, and it is long enough '
                 'that the document itself does not look like a block shell.</body></html>')


@dataclass
class LiveFixtureSite:
    """一个只存在于回环上的合成站点。**记录每一次真的到达它的请求。**

    ``hits`` 是这份 harness 里最重要的一个读数：一次「被拒」的说法只有在
    这个列表**没有长出来**的时候才算数。
    """

    host: str = FIXTURE_HOST
    decoy_host: str = DECOY_HOST

    def __post_init__(self) -> None:
        self.hits: list[str] = []
        self._server: socketserver.ThreadingTCPServer | None = None
        self._thread: threading.Thread | None = None

    # -- 生命周期 ---------------------------------------------------------
    def start(self) -> "LiveFixtureSite":
        site = self

        class Handler(http.server.BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.0"

            def log_message(self, *args: Any) -> None:  # noqa: D102 - 静音
                return

            def _send(self, body: str, status: int = 200,
                      headers: Sequence[tuple[str, str]] = ()) -> None:
                payload = body.encode("utf-8")
                self.send_response(status)
                for key, value in headers:
                    self.send_header(key, value)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler 的接口
                path = self.path.split("?", 1)[0]
                site.hits.append(path)
                if path == "/redir":
                    self._send("", 302, [("Location", f"http://{site.decoy_host}/after")])
                    return
                if path == "/forbidden":
                    # 一条**真**的上游 403：它让「未成功的那一页在账上留下什么」
                    # 可以在真浏览器上量出来，而不必靠假 SDK 演一遍。
                    self.send_response(403)
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
                if path == "/meta":
                    self._send(site.page_html(REDIRECT_BODY))
                    return
                if path == "/robots.txt":
                    body = site.robots_body.encode("utf-8")
                    self.send_response(200)
                    self.send_header("Content-Type", "text/plain; charset=utf-8")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                    return
                if path == "/sitemap.xml":
                    body = site.sitemap_body().encode("utf-8")
                    self.send_response(200)
                    self.send_header("Content-Type", "application/xml")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                    return
                if path == "/clean":
                    self._send(site.page_html(CLEAN_BODY))
                    return
                if path in ("/alpha", "/beta", "/gamma", "/delta"):
                    self._send(site.page_html(
                        f'<h1 id="title">{path.strip("/").title()}</h1>'
                        f'<p class="intro">Section {path.strip("/")} of the synthetic '
                        f'fixture. It carries enough prose to be read as a page rather '
                        f'than as a shell, which is what the deep-crawl cap judgement '
                        f'needs in order to fetch it at all.</p>'))
                    return
                self._send(site.page_html(FIXTURE_BODY))

        server = socketserver.ThreadingTCPServer(("127.0.0.1", 0), Handler)
        server.daemon_threads = True
        self._server = server
        self._thread = threading.Thread(target=server.serve_forever, daemon=True,
                                        name="crawl4ai-live-fixture")
        self._thread.start()
        return self

    #: sitemap 里那一条**不属于夹具**的条目。它让「跨域种子被拒」在真出口上
    #: 也可以量出来：``render`` 那一路的 allowlist 只含夹具主机。
    sitemap_foreign = "https://outside.invalid/elsewhere"

    #: robots.txt 的内容。**默认允许**：一个「什么都没写的 robots」按通行语义是
    #: 没有限制（``robots_allowed`` 的 ``saw_rule`` 为假那一支）；测试可以把这一格
    #: 改成 ``User-agent: *\nDisallow: /beta`` 来判拒绝那一路。
    robots_body: str = "User-agent: *\nAllow: /\n"

    #: sitemap 声明的路径。**比页数上限多** —— 限额是限额，不是「有几页就抓几页」。
    sitemap_paths: tuple[str, ...] = ("/alpha", "/beta", "/gamma", "/delta")

    def sitemap_body(self) -> str:
        entries = [f"<url><loc>{self.origin}{p}</loc></url>" for p in self.sitemap_paths]
        entries.append(f"<url><loc>{self.sitemap_foreign}</loc></url>")
        return "<urlset>" + "".join(entries) + "</urlset>"

    def page_html(self, body: str) -> str:
        """把诱饵名换成 ``host:port`` 的真实写法。端口只有起来之后才知道。"""
        return ("<!doctype html><html><head><title>Fixture</title></head><body>"
                + body.replace("DECOY", f"{self.decoy_host}:{self.port}")
                + "</body></html>")

    @property
    def port(self) -> int:
        assert self._server is not None, "夹具还没 start()"
        return int(self._server.server_address[1])

    @property
    def origin(self) -> str:
        return f"http://{self.host}:{self.port}"

    def url(self, path: str = "/page") -> str:
        return f"{self.origin}{path}"

    def stop(self) -> None:
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
            self._server = None
        if self._thread is not None:
            self._thread.join(timeout=5)
            self._thread = None

    def __enter__(self) -> "LiveFixtureSite":
        return self.start()

    def __exit__(self, *exc_info: object) -> None:
        self.stop()

    # -- 判据用的读数 -----------------------------------------------------
    def decoy_reached(self) -> bool:
        """诱饵名有没有出现在任何一次到达本服务的请求路径里。

        本 harness 里诱饵**没有**自己的服务：它只是夹具页引用的那些名字。
        所以这一条几乎总是假 —— 保留它是为了在有人后来真给诱饵起了一个 listener 时，
        判据仍然读得出来。真正承重的那一条是 :attr:`hits` 本身。
        """
        return any(self.decoy_host in hit for hit in self.hits)


# ---------------------------------------------------------------------------
# 合成 DNS 与窄判据
# ---------------------------------------------------------------------------

def synthetic_resolver_class(safe: Any) -> type:
    """``HostResolver`` 的实现类。**只回答夹具那一个名字。**

    对任何别的主机名抛 ``NONEXISTENT`` —— 于是「出口拒绝了一个陌生名字」这件事
    是可复现的，而且**不向真实解析器提问**（一次真实 DNS 查询既慢，又会在
    断网环境里变成一个超时，把一条判据变成一次环境测量）。
    """
    HostResolver = safe.HostResolver
    DnsResolutionError = safe.DnsResolutionError
    DnsFailureKind = safe.DnsFailureKind

    class SyntheticResolver(HostResolver):
        def __init__(self, mapping: Mapping[str, str]) -> None:
            self._mapping = dict(mapping)
            self.asked: list[str] = []

        def resolve(self, host: str, port: int) -> tuple[str, ...]:
            self.asked.append(host)
            if host in self._mapping:
                return (self._mapping[host],)
            raise DnsResolutionError(host, DnsFailureKind.NONEXISTENT,
                                     "synthetic resolver: this name is not the fixture")

    return SyntheticResolver


def fixture_gate_class(safe: Any) -> type:
    """``EgressGate`` 的子类：**只对夹具那一个端点点头**。

    其余每一个 ``(host, port)`` —— 包括夹具页自己发起的 ``<img>`` / ``<iframe>`` /
    ``Worker`` / ``fetch`` / ``WebSocket`` —— 都交给**真判据**（字面量 → 允许名单 →
    解析 → 地址）。所以放行方向是窄的，而拒绝方向仍然是真的。

    ``narrow_calls`` / ``delegated_calls`` 是给判据读的：一个「只对夹具点头」的
    说法如果不能证明它**确实**把别的请求交出去了，它读起来与「什么都放行」同形。
    """
    EgressGate = safe.EgressGate
    EgressVerdict = safe.EgressVerdict

    class FixtureGate(EgressGate):
        def __init__(self, resolver: Any, allowlist: frozenset[str],
                     fixture_host: str, fixture_port: int,
                     fixture_address: str = "127.0.0.1") -> None:
            super().__init__(resolver, allowlist=allowlist)
            self._fixture = (fixture_host, fixture_port, fixture_address)
            self.narrow_calls: list[str] = []
            self.delegated_calls: list[str] = []

        def check(self, host: str, port: int) -> Any:
            if (host, port) == self._fixture[:2]:
                self.narrow_calls.append(f"{host}:{port}")
                return EgressVerdict(allowed=True, address=self._fixture[2])
            self.delegated_calls.append(f"{host}:{port}")
            return super().check(host, port)

    return FixtureGate


# ---------------------------------------------------------------------------
# 会话与绑定
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class SessionEgressBinding:
    """把 A 的 :class:`SafeEgressSession` 适配成本 adapter 的出口接缝。

    **它不加任何一格配置。** ``browser_config_kwargs`` 原样转发会话给的那一份，
    ``proxy_url`` 取代理真正监听的地址，``decisions`` 取代理记下的判决。
    一个在这里「顺手补一格」的实现会让两处定义分叉，而分叉的那一天没有东西会红。

    ``check(url)`` 是**预检**：它问的是判据（``gate.check``），不是代理。
    两者不是一回事 —— 代理是连接被钉住的那个位置，预检只是让一次注定被拒的抓取
    不至于先把浏览器拉起来。
    """

    session: Any
    implementation: str
    contract_sha256: str

    @property
    def proxy_url(self) -> str:
        return str(self.session.proxy.url)

    def decisions(self) -> tuple[Any, ...]:
        return tuple(self.session.proxy.decisions)

    def check(self, url: str) -> Any:
        import research_adapter_crawl4ai as adapter

        parts = urlsplit(url)
        host = (parts.hostname or "").lower()
        port = parts.port or (443 if parts.scheme == "https" else 80)
        verdict = self.session.gate.check(host, port)
        return adapter.EgressDecisionView(
            method="GET", target=f"{host}:{port}", port=port,
            allowed=bool(verdict.allowed),
            refusal="" if verdict.allowed else str(verdict.refusal or ""),
            address=str(getattr(verdict, "address", "") or ""),
            detail=str(getattr(verdict, "detail", "") or ""),
        )

    def browser_config_kwargs(self) -> Mapping[str, object]:
        return self.session.browser_config_kwargs()

    # -- 判据用的读数 -----------------------------------------------------
    def refusal_targets(self) -> tuple[str, ...]:
        return tuple(d.target for d in self.session.proxy.decisions if not d.allowed)

    def allowed_targets(self) -> tuple[str, ...]:
        return tuple(d.target for d in self.session.proxy.decisions if d.allowed)

    def egress_ref(self) -> str:
        return str(self.session.browser_egress_ref())


def _contract_sha256(egress_module: Any) -> str:
    """A 那一层的**形状版本**摘要。

    取 ``SAFE_EGRESS_API_VERSION``（形状变了才变）+ 模块文件本身的字节 hash。
    没有这一格时，一个「同名但已经改了行为」的出口与它上一版长得一模一样，
    而 :func:`verify_egress_binding` 那一侧看不出区别。
    """
    version = str(getattr(egress_module, "SAFE_EGRESS_API_VERSION", "unknown"))
    path = getattr(egress_module, "__file__", "") or ""
    digest = hashlib.sha256()
    digest.update(version.encode("utf-8"))
    try:
        digest.update(Path(path).read_bytes())
    except OSError:
        digest.update(b"<unreadable>")
    return digest.hexdigest()


@contextmanager
def live_local_session(*, safe: Any, egress: Any, site: LiveFixtureSite,
                       max_depth: int = 1, max_pages: int = 8,
                       allowlist: frozenset[str] | None = None) -> Iterator[Any]:
    """起一次**真实受管**的本地会话：合成 DNS + 窄判据 + A 的真出口。

    ``egress.safe_browser_egress`` 会在出口起不来或 crawl4ai/Chromium 缺失时
    抛 ``LOCAL_CRAWL_NOT_SAFE_TO_RUN`` —— 这里**不接住它**：那正是「缺依赖不降级」
    的那一条，把它变成一个返回值就会让调用方有机会顺手直连。
    """
    entries = allowlist if allowlist is not None else frozenset({site.host})
    resolver = synthetic_resolver_class(safe)({site.host: "127.0.0.1"})
    gate = fixture_gate_class(safe)(resolver, entries, site.host, site.port)
    with egress.safe_browser_egress(allowlist=entries, resolver=resolver,
                                    gate=gate) as session:
        yield session


def binding_for(session: Any, egress_module: Any) -> SessionEgressBinding:
    return SessionEgressBinding(
        session=session,
        implementation="research_egress.SafeEgressSession",
        contract_sha256=_contract_sha256(egress_module),
    )


def adapter_for(binding: Any, *, crawl4ai_module: Any | None = None,
                artifact_root: str | None = None) -> Any:
    """把绑定接上本 lane 的 adapter，指向**真** crawl4ai。

    这是两段代码接起来的那一行；判据里没有别的装配点。
    """
    import research_adapter_crawl4ai as adapter

    module = crawl4ai_module if crawl4ai_module is not None else importlib.import_module("crawl4ai")
    return adapter.Crawl4AIAdapter(
        egress=binding,
        sdk_available=lambda: True,
        module_loader=lambda: module,
        artifact_root=artifact_root,
    )


#: 与本 harness 配套的、**不需要浏览器**的名词表：判据用它来断言
#: 「我们确实覆盖了这几类网络动作」，而不是让读者从 fixture 的 HTML 里数出来。
SUBRESOURCE_KINDS: tuple[str, ...] = ("image", "iframe", "worker", "fetch", "websocket")


def main(argv: list[str] | None = None) -> int:
    """Report whether this host can run the real local browser stack, and why not.

    The harness itself needs crawl4ai plus Chromium and an explicit opt-in, so this
    reports the *prerequisite* verdict instead of pretending to have run it. An
    operator asking "can this host do the local crawl" gets one closed-set answer
    and the exact command for whichever piece is missing — which is a different
    question from "does the product support local crawling", and the two were
    previously answered by the same silence.

    Exit codes: 0 READY, 2 any other verdict (a missing prerequisite is a result,
    not a tool error, and a caller scripting this needs to tell them apart).
    """
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--json", action="store_true",
                        help="machine-readable verdict instead of a summary line")
    args = parser.parse_args(argv)

    state = dependency_state()
    if args.json:
        print(json.dumps(state, ensure_ascii=False, indent=2))
    else:
        print(f"local browser readiness: {state['verdict']}")
        for field in ("safe_egress_importable", "research_egress_importable",
                      "crawl4ai_importable", "chromium_present"):
            print(f"  {field}: {state[field]}")
        if state["action"]:
            print(f"  action: {state['action']}")
    if state["verdict"] != "READY":
        return 2
    if os.environ.get(LOCAL_CRAWL_E2E_ENV) != "1":
        # The harness drives a real browser and stands up a local fixture site, so
        # it runs under its own test module; this verb answers the prerequisite
        # question and says where the run itself lives rather than pretending the
        # two are the same command.
        print(f"prerequisites are met; the harness run itself needs "
              f"{LOCAL_CRAWL_E2E_ENV}=1 and lives in "
              "tests/test_wave1_crawl4ai_live_local.py")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
