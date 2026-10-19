"""W-E 的离线夹具：合成站点、一个**记录型**正向代理、一个可注入的假 Crawl4AI SDK。

## 这个文件为什么不是 ``test_*``

``unittest discover`` 的默认 pattern 是 ``test*.py``，所以这个模块不会被当成一个
测试文件收集。它只提供夹具，不提供判据。

## 它刻意做到了什么地步

**真的起一个 HTTP 服务，真的起一个正向代理，真的走一遍 socket。**
理由是这一层要判的那条不变量是「被拒 ⇒ 上游**一次连接都没有**」——
它只有在真有一条网络路径时才可能被判伪。一个把 ``urllib`` 打桩掉的做法，
会把「我们的 opener 真的把请求交给了代理」这条也一起打掉，而那正是 IA-4
里最容易悄悄坏掉的一格。

## 这个代理与真出口的关系：**它不是一个出口**

真出口是 W-A 的 ``marketops/adapters/egress_proxy.py``，它的判据会拒回环地址、
拒私网、把连接钉在判过的地址上。这里这个代理**刻意**允许回环 —— 因为夹具站点就
在回环上。所以：

* 这些测试证明的是**管道**：每一个网络动作都经过代理、被拒时不发生上游连接、
  重定向被拒、有界读取真的掐断。
* 这些测试**不**证明、也不能被读成「出口的判据是对的」。那一堆在 W-A 的
  ``tests/test_wave1_egress_*.py`` 里，主语不同。
"""
from __future__ import annotations

import http.server
import json
import socketserver
import threading
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Callable, Mapping, Sequence

# ---------------------------------------------------------------------------
# 合成 HTML
# ---------------------------------------------------------------------------

INDEX_HTML = """<!doctype html>
<html><head><title>Fixture Index</title>
<script>window.__leak = "should-never-be-extracted";</script>
<style>.x{color:red}</style></head>
<body>
<h1 id="title">Fixture Index</h1>
<p class="intro">A synthetic page for offline Crawl4AI adapter tests.</p>
<div id="org" data-id="42">Example Coatings Ltd</div>
<span class="contact">+44 20 7946 0958</span>
<a class="email" href="mailto:info@example.org">info@example.org</a>
<ul><li>alpha item</li><li>beta item</li></ul>
<a href="/alpha">Alpha</a>
<a href="/beta">Beta</a>
<a href="https://outside.example.org/x">Outside</a>
<a href="javascript:void(0)">Nope</a>
<a href="mailto:sales@example.org">Mail us</a>
</body></html>
"""

ALPHA_HTML = """<!doctype html>
<html><head><title>Alpha</title></head><body>
<h1 id="title">Alpha</h1><p>Alpha body.</p>
<a href="/beta">Beta</a><a href="https://outside.example.org/y">Outside</a>
</body></html>
"""

BETA_HTML = """<!doctype html>
<html><head><title>Beta</title></head><body>
<h1 id="title">Beta</h1><p>Beta body.</p>
</body></html>
"""

CHALLENGE_HTML = """<!doctype html>
<html><head><title>Just a moment...</title></head>
<body><h1>Just a moment...</h1><p>Checking your browser before accessing.</p></body></html>
"""


class _Handler(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.0"
    server_version = "MarketOpsFixture/1.0"

    def log_message(self, *args: Any) -> None:  # noqa: D102 - 静音，判据读自 hits
        return

    def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler 的接口
        site: "SyntheticSite" = self.server.site  # type: ignore[attr-defined]
        path = self.path.split("?", 1)[0]
        site.hits.append(path)
        if path == "/redirect":
            self.send_response(302)
            self.send_header("Location", "/beta")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        if path == "/challenge":
            body = CHALLENGE_HTML.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if path == "/blocked":
            self.send_response(403)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        if path == "/big":
            size = int(site.big_bytes)
            body = (b"x" * size)
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if path == "/robots.txt":
            # **必须存在**：seeder 现在会对每个 origin 取一次 robots，而
            # 「取不到 ⇒ 证不出允许 ⇒ 这个 origin 的种子全拒」。夹具不提供它，
            # 那条判据就会以「种子是空的」的样子红 —— 而红的原因不在被测物上。
            body = getattr(site, "robots_body", "User-agent: *\nAllow: /\n").encode()
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
        routes = {
            "/": INDEX_HTML,
            "/index.html": INDEX_HTML,
            "/alpha": ALPHA_HTML,
            "/beta": BETA_HTML,
        }
        if path not in routes:
            self.send_response(404)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        body = routes[path].encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


class SyntheticSite:
    """一个只存在于回环上的合成站点。**记录每一次真的到达它的请求。**"""

    def __init__(self, *, big_bytes: int = 4096,
                 sitemap_paths: Sequence[str] = (),
                 sitemap_outside: str | None = None) -> None:
        self.hits: list[str] = []
        self.big_bytes = big_bytes
        #: sitemap 用**路径**而不是完整 URL 描述：端口只有 ``start()`` 之后才知道，
        #: 而把端口写死在构造参数里会让夹具要么先猜一个端口、要么要求调用方
        #: 先起再改 —— 后者在报告上就是「这台机器上恰好跑着那个端口」。
        self.sitemap_paths = tuple(sitemap_paths)
        self.sitemap_outside = sitemap_outside
        self._server: socketserver.ThreadingTCPServer | None = None
        self._thread: threading.Thread | None = None

    def sitemap_body(self) -> str:
        entries = [f"<url><loc>{self.url(path)}</loc></url>" for path in self.sitemap_paths]
        if self.sitemap_outside:
            entries.append(f"<url><loc>{self.sitemap_outside}</loc></url>")
        return ("<urlset>" + "".join(entries) + "</urlset>") if entries else "<urlset></urlset>"

    def start(self) -> "SyntheticSite":
        server = socketserver.ThreadingTCPServer(("127.0.0.1", 0), _Handler)
        server.daemon_threads = True
        server.site = self  # type: ignore[attr-defined]
        self._server = server
        self._thread = threading.Thread(target=server.serve_forever, daemon=True,
                                        name="crawl4ai-fixture-site")
        self._thread.start()
        return self

    @property
    def port(self) -> int:
        assert self._server is not None, "site 还没 start()"
        return int(self._server.server_address[1])

    def url(self, path: str = "/") -> str:
        return f"http://127.0.0.1:{self.port}{path}"

    def stop(self) -> None:
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
            self._server = None
        if self._thread is not None:
            self._thread.join(timeout=5)
            self._thread = None

    def __enter__(self) -> "SyntheticSite":
        return self.start()

    def __exit__(self, *exc_info: object) -> None:
        self.stop()


# ---------------------------------------------------------------------------
# 记录型正向代理
# ---------------------------------------------------------------------------

class _NoFollow(urllib.request.HTTPRedirectHandler):
    """夹具代理自己**也不跟随重定向**。

    这一格是必需的，而且它修的是一个真缺陷：代理若跟随 302，它会把 ``/beta``
    的正文当 ``/redirect`` 的结果回给调用方，于是 adapter 那一侧的
    ``_NoRedirect`` 根本没有机会被触发 —— 判据会在一个「看起来正常」的 200 上
    通过，而它要判的那件事（不跟随）一次都没发生过。
    """

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ANN001
        raise urllib.error.HTTPError(req.full_url, code, "fixture-proxy-no-follow",
                                     headers, fp)


class _ProxyHandler(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.0"

    def log_message(self, *args: Any) -> None:
        return

    def do_GET(self) -> None:  # noqa: N802
        proxy: "RecordingProxy" = self.server.proxy  # type: ignore[attr-defined]
        target = self.path  # 绝对形式：http://host:port/path
        decision = proxy.decide("GET", target)
        proxy.decisions.append(decision)
        if not decision["allowed"]:
            body = json.dumps({"refused": decision["refusal"]}).encode()
            self.send_response(403)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoFollow())
        try:
            with opener.open(target, timeout=5) as response:
                payload = response.read()
                self.send_response(int(getattr(response, "status", 200) or 200))
                for key, value in response.headers.items():
                    if key.casefold() in {"transfer-encoding", "connection"}:
                        continue
                    self.send_header(key, value)
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)
        except urllib.error.HTTPError as exc:
            payload = exc.read() if hasattr(exc, "read") else b""
            self.send_response(exc.code)
            # ``Location`` **必须**原样转出去：少了它，一个 302 在客户端那一侧
            # 变成「一个没有下一个地址的 3xx」，于是要判的那条（不跟随）
            # 会以另一个理由码出现 —— 而那条判据就再也判不到真的东西了。
            for key, value in (exc.headers or {}).items():
                if key.casefold() in {"transfer-encoding", "connection",
                                      "content-length"}:
                    continue
                self.send_header(key, value)
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
        except Exception as exc:  # noqa: BLE001 - 夹具：把失败变成一个可读的 502
            body = f"proxy failure: {type(exc).__name__}".encode()
            self.send_response(502)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)


@dataclass
class RecordingProxy:
    """一个回环正向代理。``gate`` 决定放不放行；放行才真的连上游。

    ``gate`` 的缺省是**全拒**：一次被拒绝的请求在夹具站点上**不留任何痕迹**，
    而「零连接」正是那些对抗用例要读的那个数。
    """

    gate: Callable[[str], bool] | None = None
    decisions: list[dict[str, Any]] = field(default_factory=list)
    _server: socketserver.ThreadingTCPServer | None = None
    _thread: threading.Thread | None = None

    def decide(self, method: str, target: str) -> dict[str, Any]:
        allowed = bool(self.gate(target)) if self.gate else False
        return {"method": method, "target": target, "allowed": allowed,
                "refusal": "" if allowed else "test_gate_refused"}

    def start(self) -> "RecordingProxy":
        server = socketserver.ThreadingTCPServer(("127.0.0.1", 0), _ProxyHandler)
        server.daemon_threads = True
        server.proxy = self  # type: ignore[attr-defined]
        self._server = server
        self._thread = threading.Thread(target=server.serve_forever, daemon=True,
                                        name="crawl4ai-fixture-proxy")
        self._thread.start()
        return self

    @property
    def url(self) -> str:
        assert self._server is not None, "proxy 还没 start()"
        return f"http://127.0.0.1:{int(self._server.server_address[1])}"

    def stop(self) -> None:
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
            self._server = None
        if self._thread is not None:
            self._thread.join(timeout=5)
            self._thread = None

    def __enter__(self) -> "RecordingProxy":
        return self.start()

    def __exit__(self, *exc_info: object) -> None:
        self.stop()


@dataclass(frozen=True)
class TestEgress:
    """一个**形状合格**的出口绑定，背后是 :class:`RecordingProxy`。

    它**只**适合测试：``contract_sha256`` 是一个占位值，而代理的判据允许回环。
    真出口在 W-A；这个类存在的意义是让 ``verify_egress_binding`` 判「合格」，
    从而把「绑定不合格」与「绑定合格但出口拒绝」分成两个可区分的读数。
    """

    proxy: RecordingProxy
    implementation: str = "tests.crawl4ai_fixtures.RecordingProxy"
    contract_sha256: str = "e" * 64
    refusal: str = "test_gate_refused"

    @property
    def proxy_url(self) -> str:
        return self.proxy.url

    def decisions(self) -> tuple[Any, ...]:
        return tuple(self.proxy.decisions)

    def check(self, url: str) -> Any:
        # 延迟 import：夹具模块不能反过来要求被测物先被 import 过。
        import research_adapter_crawl4ai as adapter

        allowed = bool(self.proxy.gate(url)) if self.proxy.gate else False
        return adapter.EgressDecisionView(
            method="GET", target=url, port=80, allowed=allowed,
            refusal="" if allowed else self.refusal,
            address="127.0.0.1" if allowed else "",
        )

    def browser_config_kwargs(self) -> dict[str, object]:
        """出口给的那一份浏览器配置。

        **值照抄 A 的 ``guarded_http``**（``proxy_config`` + 那两个 extra_args）——
        因为适配层要判的正是「出口给的这一份有没有被原样交下去」，
        而不是「我们在这里另拼一份对不对」。回环绕过护栏必须在，
        否则 ``verify_egress_binding`` 会（正确地）判这一份不合格。
        """
        return {"proxy_config": {"server": self.proxy_url},
                "extra_args": ["--proxy-bypass-list=<-loopback>", "--disable-quic"]}


# ---------------------------------------------------------------------------
# 假 Crawl4AI SDK
# ---------------------------------------------------------------------------

@dataclass
class FakeCrawlerResult:
    html: str = ""
    success: bool = True
    status_code: int | None = 200
    response_headers: Mapping[str, str] = field(default_factory=dict)
    url: str = ""
    screenshot: Any = None
    pdf: Any = None
    #: 浏览器**自己去别处了**（crawl4ai 0.9.3 的真实字段名）。默认 None。
    #: 它是一格显式字段而不是事后挂上去的，因为「这一份文档是不是我们要的那一份」
    #: 是这一层最承重的一条判据，而挂在 dataclass 外面的属性在别处复制时会丢。
    redirected_url: str | None = None
    error_message: str = ""


class FakeCrawl4AI:
    """一个**只够判接线**的假 SDK。

    它不解析 HTML、不连网络。它存在的唯一目的是让判据能够回答两个问题：
    「浏览器配置里那个出口地址是我们给的吗」与「run config 上那三格是我们写的那三个值吗」。
    真 SDK 的行为只有装了真库才判得了 —— 那一条在本 Wave 里是
    ``EXTERNAL-DEPENDENCY BLOCKED``，写进 status，不写成「已验证」。
    """

    def __init__(self, results: Mapping[str, FakeCrawlerResult] | None = None,
                 default: FakeCrawlerResult | None = None,
                 fetcher: Callable[[str], FakeCrawlerResult] | None = None) -> None:
        self.results = dict(results or {})
        self.default = default or FakeCrawlerResult(
            html="<html><body><h1>fake</h1></body></html>", url="")
        #: 给了 ``fetcher`` 时，未登记 URL 的页面由它取 —— 判据把
        #: ``adapter.auxiliary_http_get`` 包一层塞进来，于是深爬/单页那一整条路
        #: **真的**经过夹具代理与夹具站点。这不是「假装 SDK 会联网」：
        #: 它判的是 adapter 在 SDK 之外还要负责的那一半（出口、边界、拒绝），
        #: 而 SDK 自身的行为在本 Wave 里没有装真库，判不了，也不冒充判过。
        self.fetcher = fetcher
        self.browser_configs: list[dict[str, Any]] = []
        self.run_configs: list[dict[str, Any]] = []
        self.base_directories: list[str | None] = []
        self.calls: list[str] = []
        self._outer = self

    # -- 被 adapter 当作模块使用的两个名字 ---------------------------------
    def BrowserConfig(self, **kwargs: Any) -> dict[str, Any]:  # noqa: N802
        self.browser_configs.append(kwargs)
        return dict(kwargs)

    def CrawlerRunConfig(self, **kwargs: Any) -> dict[str, Any]:  # noqa: N802
        self.run_configs.append(kwargs)
        return dict(kwargs)

    def AsyncWebCrawler(self, *, config: Any = None, base_directory=None) -> "_FakeCrawlerCtx":  # noqa: N802
        self.base_directories.append(base_directory)
        return _FakeCrawlerCtx(self, config)


class _FakeCrawlerCtx:
    def __init__(self, sdk: FakeCrawl4AI, config: Any) -> None:
        self.sdk = sdk
        self.config = config

    async def __aenter__(self) -> "_FakeCrawlerCtx":
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        return None

    async def arun(self, *, url: str, config: Any = None) -> FakeCrawlerResult:
        self.sdk.calls.append(url)
        if url in self.sdk.results:
            result = self.sdk.results[url]
        elif self.sdk.fetcher is not None:
            result = self.sdk.fetcher(url)
        else:
            result = self.sdk.default
        if not result.url:
            result = FakeCrawlerResult(**{**result.__dict__, "url": url})
        return result


# ---------------------------------------------------------------------------
# 便利构造
# ---------------------------------------------------------------------------

#: 夹具代理的「全放行」判据。**只适合测试** —— 真出口的判据会拒回环地址，
#: 而夹具站点就在回环上。写成一个具名常量，是为了让每一处使用它的地方都
#: 看起来就是「一次刻意的放行」，而不是一个默认值。
def RECORDING_ALLOW_ALL(url: str) -> bool:  # noqa: N802 - 常量式命名，读起来像开关
    return True


def budget(**overrides: Any) -> Any:
    """一个小的、合法的上限集。**每一格都显式给**，与生产路径同形。"""
    import research_crawl4ai_features as features

    values = {
        "max_pages": 5, "max_depth": 2, "max_domains": 1,
        "max_bytes": 1_000_000, "max_seconds": 30.0,
        "max_browser_seconds": 30.0, "max_artifacts": 4,
    }
    values.update(overrides)
    return features.CrawlResourceBudget(**values)


def limits(**overrides: Any) -> dict[str, Any]:
    """``budget()`` 的映射形态：adapter 从 ``params["limits"]`` 读它。"""
    values = {
        "max_pages": 5, "max_depth": 2, "max_domains": 1,
        "max_bytes": 1_000_000, "max_seconds": 30.0,
        "max_browser_seconds": 30.0, "max_artifacts": 4,
    }
    values.update(overrides)
    return values


class Request:
    """契约 §3.1 ``CapabilityRequest`` 的 duck-typed 替身。

    **刻意不 import 那个共享类型**：B 拥有它（``research_contract.py``），
    而 adapter 只需要四个只读属性。这个替身让「adapter 真的只读这四个」成为一条判据 ——
    它如果多读了什么，这里会 ``AttributeError``。
    """

    def __init__(self, capability_id: str, params: Mapping[str, Any],
                 *, run_id: str = "run-fixture", operation_id: str = "op-fixture",
                 account_ref: str = "local", intent_fingerprint: str = "fp",
                 grant_ref: str | None = None) -> None:
        self.capability_id = capability_id
        self.params = dict(params)
        self.run_id = run_id
        self.operation_id = operation_id
        self.account_ref = account_ref
        self.intent_fingerprint = intent_fingerprint
        self.grant_ref = grant_ref
