"""真开一次浏览器，看它**没有**别的出口（§7）。

## 这个文件为什么必须真的开浏览器

``tests/test_egress_proxy.py`` 证明代理本身按判据做事。它证明不了这一层最关键的
那一句：**浏览器的网络动作真的从那里走**。那件事由配置决定 —— 而配置写错了不会
报错，只会让页面照常渲染，只不过它是直连的。

所以这里做一次真实渲染，靶子选在**只有出口真的接上了**时才成立的地方：页面里
放一个指向 ``http://127.0.0.1:<另一个本机端口>/steal.js`` 的 ``<script>``。
于是这条判据的两半各回答一件事：

* 页面渲染成功 —— 而且**只有**判据把请求钉到 ``127.0.0.1`` 才可能成功
  （``page.example.co.uk`` 在本机 DNS 里根本不存在，照名字连只会得到一次
  ``ERR_NAME_NOT_RESOLVED``）；
* 那个本机服务**一次连接都没有** —— 且它出现在 ``egress_refusals`` 里。

把 ``proxy_config`` 删掉，第一半立刻红（名字解析不出来）。这一条**实测过**。

## 这一格**不**证明的事：那两个 ``extra_args``

实测读数（Chromium 1243 + Playwright 的 ``--proxy-server``）：**不加**
``--proxy-bypass-list=<-loopback>``，上面那个回环子资源**仍然走代理、仍然被拒** ——
这一版 Chromium 本来就没有那条隐式规则。而 ``--proxy-bypass-list`` 本身确实被遵守：
显式把 ``localhost,127.0.0.1`` 写进绕过清单，同一个请求就**直连**到了本机服务，
出口上一条记录都没有（那一次 ``beacon.accepts == 1``、``proxy.refusals == ()``）。

两句都要留着，否则下一个人会照着这段注释去删那一格、等一条永远不会红的测试变红，
或者反过来，把它读成「回环绕过已经被挡住」的已证事实。``--disable-quic`` 更弱：
本机没有可构造的 HTTP/3 目标，它是**未实测**的防御。细节写在
``Crawl4AIBrowserFetcher._browser_config`` 上。

## 靶子是受控的本机服务，不是内网

§7 末句：「测试用隔离受控目标，不向真实内网或云 metadata 探测」。两个 server
都绑 ``127.0.0.1`` 且只活在这个文件的用例里。被拒的那个地址是**我们自己立起来
然后立刻关掉**的一个监听 —— 它证明的是「这个请求没有到达本机服务」，
而不是「某个真实内网主机没被访问」。

## 判据那一半用的是替身

``check_resolved`` 正确地拒绝一切非全局可路由地址，所以真的 ``EgressGate``
放行不了任何本机能立起来的靶子。这一点写在 ``tests/test_egress_proxy.py`` 的文件头；
这里沿用同一个处置：注入一个只说一两个词的判据，把「代理拿到结论之后做了什么」
与「判据说了什么」分开。

## 顺带钉住的第四格：这次渲染留下的账说它是自造材料

同一个局面（**靶子是我们自己立的**）还有第二个后果，而它落在账上：
``docs/MCP_SERVICE.md`` §8.C.2 把两类材料并列为合法输入，紧接着要求
「**Both kinds must be labeled.**」，并且「A controlled fixture … cannot count as
market yield or real-site improvement」。这一段靶子的正文由本文件顶上的常量给出、
页面由本文件的 ``_LocalServer`` 端出 —— 是一次**真实的 Chromium 渲染**，
而材料仍是我们造的。

``tests/test_material_provenance.py::test_a_replaced_browser_lands_as_synthetic``
钉的是同一个断言的**假库那一半**（crawl4ai 被替掉），两者缺一不可：假库证明的是
「转抄没漏一格」，而这里证明的是**一次真的渲染**也走同一条判定 —— 只要靶子是自己
立的，它就不会被算成现场覆盖。第四格因此不是重复，是同一句话的另一个证据面。

## 第二次渲染：交回来的是**渲染之后的 DOM**，不是一个占位壳

上面那一页的正文写在静态 HTML 里 —— 它能证明出口接对了，证明不了「脚本真的跑了」。
§11 第 206 行要的正是另一半，逐字是「正常 **JS-required** fixture … 完整正文/链接
不为占位」。

所以本文件第二个用例端出的那一页，正文与链接**都是内联脚本写进 DOM 的**，而脚本
执行之前，页面上只有一个占位串。于是那条判据的两个方向各钉一头：

* 脚本写进去的正文与 ``href`` **逐字在镜像里**；
* 脚本执行前那个占位串**不在镜像里** —— 它在原始响应里，在渲染后的 DOM 里已经被
  ``innerHTML`` 换掉了。

## 哪一半承重：量出来的，不是推出来的

上面两条**不是等价的**，而这一条是**实测**的（2026-09-28，本机）：

* 把 ``--blink-settings=scriptEnabled=false`` 加进 ``_browser_config`` 的
  ``extra_args``，那条判据转红 —— 红在**占位串**那一句上；
* 而同一轮里那两条「正文/链接在场」的断言**仍然是绿的**。原因是清楚的：脚本没跑
  时交回来的是原始响应，而正文与链接的字面值**就写在那个 ``<script>`` 的源码里**，
  于是「在场」这件事分不出「执行过的 DOM」与「没执行的源码」。

（顺带记一笔：先试的是 ``--disable-javascript``，它**不是** Chromium 认得的开关，
而 Chromium 对不认识的开关一声不响 —— 那一次判据照样绿，而那是**探针写错了**，
不是判据弱。用「开关加上去了它就该红」去判一条判据的死活之前，要先确认那个开关真的
被遵守。）

所以「不为占位」这一句才是这一格的承重墙，另外两句（正文在场、链接在场）是佐证：
它们说的是「正文与链接就是这一页真的那一份」，而不是「它被渲染过」。
真渲染过的那一份**长什么样**也量过
（探针在 ``artifacts/work-items/marketops/TARGET-CONVERGENCE-B15H/``）：镜像里
``<script`` 一个都没有、占位串一个都没有，``<h1>`` / ``<a href>`` 逐字在。

这一格同样不是现场覆盖：靶子与上面那一个同源（受控本机服务），而且这一页一个子资源
都不发 —— ``<script>`` 是内联的，``<a href>`` 不是一次请求。
"""

from __future__ import annotations

import socket
import threading
import time
from pathlib import Path

import pytest

from marketops.adapters.crawl4ai_fetcher import (
    Crawl4AIBrowserFetcher,
    crawl4ai_available,
    crawl4ai_unavailable_reason,
)
from marketops.adapters.egress_proxy import EgressProxy, EgressVerdict
from marketops.adapters.sqlite.fetch_attempt import SqliteFetchAttemptStore
from marketops.domain.enums import FetchOutcome
from marketops.domain.fetch_attempt import MaterialProvenance
from marketops.ports.acquisition import FetchRequest
from marketops.ports.dns import DnsFailureKind, DnsResolutionError, HostResolver

#: 页面那一跳的名字。**公开域名**，因为 ``check_target`` 在发请求之前会拒掉一切
#: 环回 / 保留名 —— 而本机 DNS 里它不存在，所以「渲染成功」这件事只能由出口解释。
PAGE_HOST = "page.example.co.uk"

#: 入口解析那一半交出来的地址。必须是全局可路由的，理由见
#: ``tests/test_acquisition_target_policy.py`` 的同名常量。
PUBLIC_ADDRESS = "93.184.216.34"

#: 被拒的请求在 ``egress_refusals`` 上留下的那一行（端口在用例里填）。
_LOOPBACK = "127.0.0.1"

#: 第二个用例那一页的四个串。前三个**只在脚本执行之后**才存在，最后一个**只在
#: 脚本执行之前**存在 —— 那条判据的反向读数就落在它们身上。
_PLACEHOLDER = "Loading, please wait"
_REAL_HEADING = "Devon grassland restoration: annual stewardship report"
_REAL_LINK = "https://devon.example.org/contact-us"
#: 脚本注入的正文。写得比 ``EMPTY_SHELL_TEXT_THRESHOLD`` 长，免得这一页被当成
#: 空壳判掉 —— 那个判定看的是可见正文字符数，而这一页的正文是脚本给的。
_REAL_PARAGRAPH = (
    "The stewardship group re-seeded fourteen hectares of species-rich floodplain "
    "meadow across three holdings in the Culm valley, and the annual report sets "
    "out the sward composition, the grazing regime agreed with each tenant, and "
    "the monitoring transects that are walked every June."
)

#: Playwright 没装浏览器时的错误文本。**窄匹配**：只跳过这一种，
#: 别的渲染失败照常红 —— 一个宽匹配的 skip 会把真正的缺陷一起吞掉。
_MISSING_BROWSER_MARKERS = (
    "Executable doesn't exist",
    "playwright install",
)

pytestmark = pytest.mark.skipif(
    not crawl4ai_available(),
    reason=f"crawl4ai 不可用（可选运行依赖）：{crawl4ai_unavailable_reason()}",
)


class _LocalServer:
    """一个受控的本机监听。``mode="page"`` 回一份 HTML，``mode="beacon"`` 只计数。

    ``accepts`` 是这里**唯一承重**的读数：被拒的那个请求如果在它上面留下了连接，
    就说明那次请求已经到达了本机服务 —— 无论报告上写的是什么。
    """

    def __init__(self, *, mode: str, body: bytes = b"") -> None:
        self._mode = mode
        self._body = body
        self._socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._socket.bind(("127.0.0.1", 0))
        self._socket.listen(8)
        self._socket.settimeout(0.5)
        self.port: int = self._socket.getsockname()[1]
        self.accepts = 0
        self.hosts: list[str] = []
        self._stop = threading.Event()
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
                head = conn.recv(65536).decode("iso-8859-1", "replace")
                for line in head.split("\r\n"):
                    if line.lower().startswith("host:"):
                        self.hosts.append(line.split(":", 1)[1].strip())
                if self._mode == "page":
                    conn.sendall(
                        b"HTTP/1.1 200 OK\r\n"
                        b"Content-Type: text/html; charset=utf-8\r\n"
                        b"Content-Length: "
                        + str(len(self._body)).encode()
                        + b"\r\nConnection: close\r\n\r\n"
                        + self._body
                    )
                else:
                    conn.sendall(
                        b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\n"
                        b"Connection: close\r\n\r\n{}"
                    )
            except OSError:
                pass
            finally:
                conn.close()

    def close(self) -> None:
        self._stop.set()
        self._socket.close()
        self._thread.join(timeout=2)


class _OneHost:
    """只放行一个主机名的判据，其余一律拒。**它是替身**，理由见文件头。"""

    def __init__(self, allowed_host: str, address: str) -> None:
        self._allowed = allowed_host
        self._address = address
        self.asked: list[tuple[str, int]] = []

    def check(self, host: str, port: int) -> EgressVerdict:
        self.asked.append((host, port))
        if host == self._allowed:
            return EgressVerdict(allowed=True, address=self._address)
        return EgressVerdict(
            allowed=False,
            refusal="private_target",
            detail=f"{host!r} 不是这一格允许的主机名",
        )


class _FakeResolver(HostResolver):
    """入口那一半的解析：只回答页面主机，其余当作不存在。"""

    def __init__(self, mapping: dict[str, tuple[str, ...]]) -> None:
        self._mapping = mapping
        self.calls: list[tuple[str, int]] = []

    def resolve(self, host: str, port: int) -> tuple[str, ...]:
        self.calls.append((host, port))
        if host not in self._mapping:
            raise DnsResolutionError(host, DnsFailureKind.NONEXISTENT, "测试里没有这个名字")
        return self._mapping[host]


def _wait_for(predicate, *, timeout: float = 5.0) -> bool:
    """等一个在**别的线程**里发生的观测。

    浏览器发子资源请求的时刻与 ``arun`` 返回的时刻没有先后保证：
    ``domcontentloaded`` 一触发，那一跳可能还在出口上。所以这一格等的不是
    「判据会不会成立」，而是「那件事有没有时间发生」—— 两者的区别决定了
    这个文件是不是一次会随机变红的测试。
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.05)
    return predicate()


def _skip_if_no_browser(result) -> None:
    """Playwright 浏览器没装时跳过这一格。**窄匹配**，只认那一种错误文本。

    别的渲染失败照常红：一个宽匹配的 skip 会把真正的缺陷一起吞掉。
    """
    if result.outcome is FetchOutcome.RENDER_FAILURE and any(
        marker in result.error for marker in _MISSING_BROWSER_MARKERS
    ):
        pytest.skip(f"本机没有安装 Playwright 浏览器：{result.error}")


def test_a_page_cannot_reach_a_local_service_through_the_browser(
    settings, tmp_path: Path
) -> None:
    beacon = _LocalServer(mode="beacon")
    page_body = (
        "<html><head>"
        f'<script src="http://{_LOOPBACK}:{beacon.port}/steal.js"></script>'
        "</head><body><h1>Natural grass in Devon</h1></body></html>"
    ).encode()
    page = _LocalServer(mode="page", body=page_body)
    gate = _OneHost(PAGE_HOST, _LOOPBACK)
    proxy = EgressProxy(gate=gate, idle_timeout=5.0)
    resolver = _FakeResolver({PAGE_HOST: (PUBLIC_ADDRESS,)})
    attempts = SqliteFetchAttemptStore(settings.database_path)
    fetcher = Crawl4AIBrowserFetcher(
        settings, resolver=resolver, egress=proxy, attempts=lambda: attempts
    )
    request = FetchRequest(url=f"http://{PAGE_HOST}:{page.port}/", scope="egress-live-001")

    proxy.start()
    try:
        result = fetcher.fetch(request)
    finally:
        proxy.stop()
        page.close()
        beacon.close()

    _skip_if_no_browser(result)

    # 1) 页面渲染出来了。它**只能**经由出口 —— 名字在本机 DNS 里不存在。
    assert result.outcome is FetchOutcome.OK, f"{result.reason} / {result.error}"
    assert result.body_path is not None, "渲染成功却没有落盘镜像"
    assert b"Natural grass in Devon" in result.body_path.read_bytes()
    assert page.accepts >= 1, "页面那一跳根本没到本机 service"
    assert page.hosts and page.hosts[0].startswith(PAGE_HOST), page.hosts

    # 2) 子资源那一跳被拒，而且**没有到达**本机服务。
    refused = f"{_LOOPBACK}:{beacon.port}"
    assert _wait_for(lambda: refused in proxy.refusals), (
        f"子资源没有被判据拦下；出口看到的是 {proxy.refusals}"
    )
    assert beacon.accepts == 0, (
        f"页面里的脚本请求到达了本机 {_LOOPBACK}:{beacon.port} —— "
        "出口没接上，或者不再覆盖这一跳"
    )

    # 3) 回执上那两格讲的是同一件事。``egress_refusals`` 是渲染结束那一刻的快照，
    #    所以它只能是``proxy.refusals`` 的子集 —— 反过来就是编造。
    assert set(result.egress_refusals) <= set(proxy.refusals)
    assert "egress_refused" in result.reason or not result.egress_refusals, (
        f"拒绝了 {len(result.egress_refusals)} 个目标，reason 上却没有任何痕迹："
        f"{result.reason!r}"
    )

    # 4) **这一次渲染是我们自己立的靶子**，所以它必须被标成自造，不是现场覆盖。
    #    这一条是 §8.C.2 那两句话的落点（「Both kinds must be labeled.」以及
    #    「A controlled fixture … cannot count as market yield」），而它以**一次
    #    真实的 Chromium 渲染**为证据 —— 上一格用的是假的 crawl4ai 库，
    #    两者各自能证明的东西不同：假库证明的是「转抄没漏」，这一格证明的是
    #    「一次真的渲染，只要靶子是我们自己立的，就不会被记成现场覆盖」。
    rows = attempts.list_attempts()
    assert len(rows) == 1, f"一次渲染留下了 {len(rows)} 行账"
    assert rows[0].material is MaterialProvenance.SYNTHETIC, (
        f"这一次的目标是本机 127.0.0.1 上我们自己起的监听，而账上写着 "
        f"{rows[0].material.value!r} —— 它会在一份覆盖率读数里算作现场覆盖"
    )


def test_a_scripted_page_arrives_rendered_and_not_as_its_placeholder(
    settings,
) -> None:
    """**JS-required** 那一半：正文与链接都由脚本写进 DOM，而镜像必须是它们。

    上面那一个证明「浏览器的网络动作真的从出口走」；这一个证明**拿到手的这一份是
    渲染之后的 DOM**，而不是原始响应、也不是一个占位壳。§11 第 206 行逐字要的是
    「完整正文/链接不为占位」，两个方向各钉一头（详细理由见文件 docstring 的第二次
    渲染那一节）：

    * 脚本注入的正文与 ``href`` 逐字在镜像里；
    * 脚本执行前的占位串**不在**镜像里。

    **承重的是后一句** —— 前者在前者上也是绿的：脚本没跑时交回来的是原始响应，
    而那两个字面值就写在 ``<script>`` 的源码里。这一条是量出来的
    （``scriptEnabled=false`` 那一轮：占位串那一句红，两条「在场」照绿），
    不是推出来的。
    """
    page_body = (
        "<!doctype html><html><head><title>field notes</title></head><body>"
        f"<p>{_PLACEHOLDER}</p>"
        "<script>"
        "document.body.innerHTML = "
        f"'<h1>{_REAL_HEADING}</h1>' + "
        f"'<p>{_REAL_PARAGRAPH}</p>' + "
        f"'<a href=\"{_REAL_LINK}\">Contact the stewardship team</a>';"
        "</script>"
        "</body></html>"
    ).encode()
    page = _LocalServer(mode="page", body=page_body)
    gate = _OneHost(PAGE_HOST, _LOOPBACK)
    proxy = EgressProxy(gate=gate, idle_timeout=5.0)
    resolver = _FakeResolver({PAGE_HOST: (PUBLIC_ADDRESS,)})
    fetcher = Crawl4AIBrowserFetcher(settings, resolver=resolver, egress=proxy)
    request = FetchRequest(url=f"http://{PAGE_HOST}:{page.port}/", scope="js-render-001")

    proxy.start()
    try:
        result = fetcher.fetch(request)
    finally:
        proxy.stop()
        page.close()

    _skip_if_no_browser(result)

    assert result.outcome is FetchOutcome.OK, f"{result.reason} / {result.error}"
    assert result.body_path is not None, "渲染成功却没有落盘镜像"
    assert page.accepts >= 1, "页面那一跳根本没到本机 service"

    text = result.body_path.read_bytes().decode("utf-8", "replace")
    assert _REAL_HEADING in text, (
        "脚本注入的正文不在镜像里 —— 页面上那一段正文只由 ``<script>`` 写出来，"
        "它在原始响应里一个字都没有"
    )
    assert _REAL_LINK in text, (
        f"脚本注入的链接 {_REAL_LINK!r} 不在镜像里 —— §11 第 206 行要求的"
        "「链接不为占位」指的是这一格"
    )
    # 这一句是**承重**的那一句 —— 上面那两句在「脚本没跑」时也会绿（字面值在
    # ``<script>`` 的源码里），只有这一句分得开「执行过的 DOM」与「没执行的源码」。
    assert _PLACEHOLDER not in text, (
        f"镜像是脚本执行**之前**的那一份：占位串 {_PLACEHOLDER!r} 还在。"
        "这一页的正文与链接都是脚本给的，占位串还在就说明交回来的不是渲染之后的 DOM"
    )
