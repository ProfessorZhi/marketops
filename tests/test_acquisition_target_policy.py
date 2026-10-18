"""抓取层真的执行了那条 URL 判据吗（§10）。

``tests/test_network_policy.py`` 证明判据本身会拒绝危险目标。本文件证明
**判据被接上了** —— 一条没有执行器的边界只是自觉，而抓取层此前正是那条
边界缺席的地方：``httpx`` 收得下就发。

## 为什么重定向要在这一层测，而不是在判据那一层

只检查起始 URL 是这个族保护里最常见的漏洞。研究场景里它一点也不罕见：

    公开站点 → 302 Location: http://169.254.169.254/latest/meta-data/

客户端跟过去，业务代码从头到尾只见过那个公开域名。所以「逐跳检查」必须
是一条**行为**，而不是「判据可以被逐跳调用」这一句可能性。

## 判据是两半，而第二半必须**绑到连接上**

第一半判 URL 里的字面量（``check_target``）。只有它时
``http://a-name-that-resolves-to-169.254.169.254/`` 会一路放行：名字是公开的，
而它实际通向哪里没人问过。所以每一跳还要 ``HostResolver.resolve`` **真的问一次**
DNS、``check_resolved`` 对**每一个**返回的地址点头，然后把这次连接**钉在**量到的
那个地址上。

三件事都要能测，而第三件最容易被写成一句声明：

* 判据本身（``tests/test_network_policy.py::check_resolved``）；
* 判据被接上了（本文件：解析到内网地址的名字**一次请求都不发**）；
* 而发出去的那一行**真的是那个地址**（本文件：`requests_seen` 记录的是
  **站点看到的**地址 = 协议 + ``Host`` 头 + 路径，`wire_seen` 记录线上那一行）。
  两者分开记，是因为钉住之后它们**本来就不同**：线上是地址，身份在 ``Host``
  头里。只记其中一个，就无法同时断言「连的是我判过的地址」与「站点看到的是它
  自己的域名」。

本文件用 ``httpx.MockTransport`` 造出真实的 ``httpx.Response``（含真实的
``.url`` / ``.headers``），并且**记录每一个真正被发出的请求**。断言里最
承重的那些是 ``requests_seen == []``：它证明被拒绝的那一跳**根本没有
离开进程**，而不是「离开了但结果被丢弃了」—— 后者对 SSRF 毫无意义。
"""

from __future__ import annotations

import ipaddress

import httpx
import pytest

from marketops.adapters.crawl4ai_fetcher import Crawl4AIBrowserFetcher
from marketops.adapters.http import HttpxFetcher, _pin
from marketops.domain.enums import BlockReason, FetchOutcome
from marketops.ports.acquisition import FetchRequest
from marketops.ports.dns import DnsFailureKind, DnsResolutionError, HostResolver
from marketops.security.network import MAX_REDIRECTS

PUBLIC = "https://example.co.uk/contact"
PUBLIC_SECOND = "https://example.co.uk/contact-us"
METADATA = "http://169.254.169.254/latest/meta-data/"
LOOPBACK = "http://127.0.0.1:8080/admin"
GONE = "https://gone.example.co.uk/contact"

#: 假解析器给出的地址。必须**真的是**全局可路由的：它不会被连上（传输是假的），
#: 但 ``check_resolved`` 要判它 —— 随手拿 ``192.0.2.1``（TEST-NET-1）会先得到一次
#: 与被测行为无关的拒绝，而那会把一条判据变成一次巧合。
PUBLIC_ADDRESS = "93.184.216.34"
SECOND_ADDRESS = "93.184.216.35"
PRIVATE_ADDRESS = "10.0.0.7"
METADATA_ADDRESS = "169.254.169.254"

_BODY = "<html><body><h1>Contact us</h1><p>Natural grass surfaces in Devon.</p></body></html>"

#: 「站点看到的」地址 = 协议 + ``Host`` 头 + 路径。钉住之后线上那一行是地址，
#: 主机身份在 ``Host`` 头里，所以这两件事必须分开记（见模块 docstring）。
_REQUESTS: list[str] = []
#: 真正发出去的那一行（``request.url``，主机名已换成解析出来的地址）。
_WIRE: list[str] = []
#: 每一跳 TLS 用的名字（``extensions["sni_hostname"]``）。
_SNI: list[str | None] = []


class FakeResolver(HostResolver):
    """一张字面表。**不问真实 DNS** —— §7 明令不向真实内网或云 metadata 探测。

    要造出「一个公开域名解析到 ``169.254.169.254``」这种局面，只能把一个假的
    解析器注进去：那件事在真实 DNS 上不可构造，而它正是这条判据存在的理由。
    记下 ``calls`` 是为了让「解析发生在哪一跳」也能被断言 —— 重定向之后又解析
    一次，是这一层与「只在入口查一次」之间的全部差别。
    """

    def __init__(
        self,
        mapping: dict[str, tuple[str, ...] | DnsResolutionError] | None = None,
        *,
        default: tuple[str, ...] | DnsResolutionError | None = None,
    ) -> None:
        self._mapping = dict(mapping or {})
        self._default = (PUBLIC_ADDRESS,) if default is None else default
        self.calls: list[tuple[str, int]] = []

    def resolve(self, host: str, port: int) -> tuple[str, ...]:
        self.calls.append((host, port))
        value = self._mapping.get(host, self._default)
        if isinstance(value, DnsResolutionError):
            raise value
        return value


def _nxdomain(host: str) -> DnsResolutionError:
    return DnsResolutionError(host, DnsFailureKind.NONEXISTENT, f"{host} 不存在")


@pytest.fixture
def requests_seen() -> list[str]:
    _REQUESTS.clear()
    _WIRE.clear()
    _SNI.clear()
    return _REQUESTS


@pytest.fixture
def wire_seen() -> list[str]:
    _REQUESTS.clear()
    _WIRE.clear()
    _SNI.clear()
    return _WIRE


def _logical(request: httpx.Request) -> str:
    """这一跳**站点看到的**地址。钉住之后它不等于 ``request.url``。"""
    authority = request.headers.get("host") or (request.url.host or "")
    query = f"?{request.url.query.decode('ascii')}" if request.url.query else ""
    return f"{request.url.scheme}://{authority}{request.url.path}{query}"


def _client(routes: dict[str, httpx.Response]) -> httpx.Client:
    """一个只会答 ``routes`` 里那些地址的 client；其余一律断言失败。

    未登记的地址会真的发出去 —— 那正是我们要捕捉的行为，所以让它抛。

    每一跳都断言两件事，而这两条断言是本节全部用例的共同前提：

    * 线上那一行的主机名**是一个 IP 字面量**（连接钉住了，客户端不会自己再解析
      一次）；
    * ``sni_hostname`` 是原来的名字（TLS 仍对着域名做证书校验）。

    少了这两条，一个「忘了钉」的实现会照样通过：那种情况下 ``request.url`` 就是
    域名，而按键查表也照样命中，整个文件读起来还是绿的。
    """

    def handler(request: httpx.Request) -> httpx.Response:
        wire = str(request.url)
        _WIRE.append(wire)
        _REQUESTS.append(_logical(request))
        _SNI.append(request.extensions.get("sni_hostname"))

        try:
            ipaddress.ip_address(request.url.host or "")
        except ValueError:
            raise AssertionError(
                f"这一跳的主机名是 {request.url.host!r}，不是地址 —— 连接没有钉在 "
                "DNS 量回来的结果上，客户端会自己再解析一次（TOCTOU / rebinding）"
            ) from None

        name = (request.headers.get("host") or "").rsplit(":", 1)[0]
        assert request.extensions.get("sni_hostname") == name, (
            f"TLS 的 sni_hostname 是 {request.extensions.get('sni_hostname')!r}，"
            f"不是 {name!r} —— 钉住地址会把 https 抓取变成一次证书不匹配"
        )

        response = routes.get(_REQUESTS[-1])
        if response is None:
            raise AssertionError(f"测试没有为 {_REQUESTS[-1]} 登记响应 —— 这一跳不该被发出")
        return response

    return httpx.Client(transport=httpx.MockTransport(handler))


def _ok(url: str) -> httpx.Response:
    return httpx.Response(
        200,
        headers={"content-type": "text/html; charset=utf-8"},
        content=_BODY.encode(),
        request=httpx.Request("GET", url),
    )


def _redirect(from_url: str, to_url: str, *, status: int = 302, location: str | None = None) -> httpx.Response:
    return httpx.Response(
        status,
        headers={"location": to_url if location is None else location},
        request=httpx.Request("GET", from_url),
    )


def _check(result, *, tier) -> None:  # noqa: ANN001 - 测试内小工具
    assert result.outcome is FetchOutcome.BLOCKED, f"结果应是 BLOCKED，实际 {result.outcome}"
    assert result.block_reason is BlockReason.UNSAFE_TARGET
    assert result.tier is tier
    # 被拒绝的目标不得留下镜像：留了就等于「抓到了但标成拒绝」。
    assert result.body_path is None
    assert result.reason, "拒绝必须留下理由，否则报告里只有一栏 BLOCKED"


# ----------------------------------------------------------------------
# 第一跳
# ----------------------------------------------------------------------
def test_an_unsafe_first_hop_never_leaves_the_process(settings, requests_seen: list[str]) -> None:
    """最承重的一条：拒绝发生在**任何**请求之前。"""
    fetcher = HttpxFetcher(settings, client=_client({}), resolver=FakeResolver())
    result = fetcher.fetch(FetchRequest(url=METADATA))

    _check(result, tier=fetcher.tier)
    assert requests_seen == [], f"被拒绝的目标仍然被请求了：{requests_seen}"


@pytest.mark.parametrize(
    "url",
    ["file:///etc/passwd", "javascript:alert(1)", "http://localhost:9000/", "http://10.1.2.3/"],
    ids=["file", "javascript", "localhost", "private-ip"],
)
def test_every_refusal_kind_blocks_before_the_network(
    settings, requests_seen: list[str], url: str
) -> None:
    fetcher = HttpxFetcher(settings, client=_client({}), resolver=FakeResolver())
    _check(fetcher.fetch(FetchRequest(url=url)), tier=fetcher.tier)
    assert requests_seen == []


def test_a_public_target_is_still_fetched(settings, requests_seen: list[str]) -> None:
    """对照：判据没有把正常抓取一起挡住。

    少了这一条，一个「永远拒绝」的实现能通过上面全部测试。
    """
    fetcher = HttpxFetcher(
        settings, client=_client({PUBLIC: _ok(PUBLIC)}), resolver=FakeResolver()
    )
    result = fetcher.fetch(FetchRequest(url=PUBLIC, scope="test-account-001"))

    assert result.outcome is not FetchOutcome.BLOCKED
    assert result.body_path is not None, "正常抓取应当留下镜像"
    assert requests_seen == [PUBLIC]
    assert result.final_url == PUBLIC, (
        "final_url 变成了线上那一行（地址）—— 证据的 source_url 会跟着指向一个 "
        "没有意义的地址，而站点身份是 example.co.uk"
    )


# ----------------------------------------------------------------------
# 重定向：逐跳
# ----------------------------------------------------------------------
def test_a_redirect_into_a_private_target_is_not_followed(
    settings, requests_seen: list[str]
) -> None:
    """公开站点把我们指向元数据服务 —— 第二跳不得发出。

    这是本文件存在的主要理由。第一跳是**公开** URL，所以「只检查起始 URL」
    的实现会放行它，然后由 httpx 自己跟过去。
    """
    fetcher = HttpxFetcher(
        settings, client=_client({PUBLIC: _redirect(PUBLIC, METADATA)}), resolver=FakeResolver()
    )
    _check(fetcher.fetch(FetchRequest(url=PUBLIC)), tier=fetcher.tier)

    assert requests_seen == [PUBLIC], (
        "第二跳被发出了 —— 说明重定向是 httpx 跟的，而不是我们逐跳判的"
    )


def test_a_redirect_into_loopback_is_not_followed(settings, requests_seen: list[str]) -> None:
    fetcher = HttpxFetcher(
        settings, client=_client({PUBLIC: _redirect(PUBLIC, LOOPBACK)}), resolver=FakeResolver()
    )
    _check(fetcher.fetch(FetchRequest(url=PUBLIC)), tier=fetcher.tier)
    assert requests_seen == [PUBLIC]


def test_a_redirect_whose_name_resolves_into_private_space_is_not_followed(
    settings, requests_seen: list[str]
) -> None:
    """第二跳的字面量是**公开域名**，只有解析出来的地址是内网 —— 仍然不得发出。

    这是解析那一半在重定向上的落点：``check_target`` 对第二跳会点头，所以
    「只在入口解析一次」的实现会在这里把请求发出去。
    """
    hop2 = "https://rebind.example.co.uk/landing"
    resolver = FakeResolver({"rebind.example.co.uk": (METADATA_ADDRESS,)})
    fetcher = HttpxFetcher(
        settings, client=_client({PUBLIC: _redirect(PUBLIC, hop2)}), resolver=resolver
    )
    result = fetcher.fetch(FetchRequest(url=PUBLIC))

    _check(result, tier=fetcher.tier)
    assert result.reason == "target_refused:resolves_to_private"
    assert requests_seen == [PUBLIC], "第二跳被发出了 —— 每一跳都必须重新解析并判断"
    assert resolver.calls == [("example.co.uk", 443), ("rebind.example.co.uk", 443)], (
        f"解析发生在这几跳上：{resolver.calls} —— 每一跳都要问一次"
    )
    assert result.redirect_trace == (PUBLIC,), (
        "被拒的那一跳不在链上（它没发生），但已经问过的那一跳要在 —— "
        "空链会把「问了之后被拒」读成「什么都没问就拒了」"
    )


def test_a_safe_redirect_chain_is_followed(settings, requests_seen: list[str]) -> None:
    """对照：正常重定向（http→https、补尾斜杠）必须照跟。

    这一条与上面两条合起来才说明「逐跳检查」是逐跳的，而不是「一见 3xx 就拒」。
    """
    hop1 = "http://example.co.uk/contact"
    fetcher = HttpxFetcher(
        settings,
        client=_client({hop1: _redirect(hop1, PUBLIC, status=301), PUBLIC: _ok(PUBLIC)}),
        resolver=FakeResolver(),
    )
    result = fetcher.fetch(FetchRequest(url=hop1))

    assert result.outcome is not FetchOutcome.BLOCKED
    assert requests_seen == [hop1, PUBLIC]
    assert result.final_url == PUBLIC, "final_url 必须是最后一跳，否则证据指向一个中间地址"
    assert result.redirect_trace == (hop1, PUBLIC), (
        "跟过的每一跳都要记下来：只留 final_url 答不出「中间经过谁」，"
        "而那正是审计「这个 URL 怎么变成那个域名」要看的东西"
    )


def test_a_relative_location_is_resolved_against_the_current_hop(
    settings, requests_seen: list[str]
) -> None:
    """``Location: /contact-us`` 是**相对**地址，要按当前这一跳解析成绝对地址。

    只按最初的 ``request.url`` 拼，会在第二跳之后就丢掉路径前缀（真实的症状是
    抓到一个 404 页并把它当成内容）。所以这个用例让第二跳**成功返回** —— 一个
    只断言「发出了请求」的版本无法区分「跟对了」与「跟到一个别的地址」。

    它同时钉住另一件事：基准不能是 ``response.url``。钉住地址之后那个读数是
    **地址**，拿它拼相对 Location 会拼出 ``https://93.184.216.34/contact-us``
    —— 一个能跑通、但把站点身份丢掉的结果。
    """
    hop1 = "https://example.co.uk/contact"
    hop2 = PUBLIC_SECOND
    fetcher = HttpxFetcher(
        settings,
        client=_client({hop1: _redirect(hop1, "", location="/contact-us"), hop2: _ok(hop2)}),
        resolver=FakeResolver(),
    )
    result = fetcher.fetch(FetchRequest(url=hop1))

    assert result.outcome is not FetchOutcome.BLOCKED
    assert requests_seen == [hop1, hop2], f"相对 Location 没有按当前地址解析：{requests_seen}"
    assert result.final_url == hop2


def test_a_redirect_loop_is_bounded(settings, requests_seen: list[str]) -> None:
    """无限重定向链必须有一个终点。

    没有上限时，这条链会一直占着一个 worker —— 而报告上只显示「还没结束」。
    """
    routes = {PUBLIC: _redirect(PUBLIC, PUBLIC)}
    fetcher = HttpxFetcher(settings, client=_client(routes), resolver=FakeResolver())
    result = fetcher.fetch(FetchRequest(url=PUBLIC))

    assert result.outcome is FetchOutcome.BLOCKED
    assert requests_seen == [PUBLIC] * (MAX_REDIRECTS + 1), (
        f"应恰好尝试 {MAX_REDIRECTS + 1} 次后放弃，实际 {len(requests_seen)} 次"
    )
    assert len(result.redirect_trace) == MAX_REDIRECTS + 1, (
        "每一次真的发出去的请求都要在链上 —— 环里少一项，报告上就少一跳"
    )


def test_a_redirect_without_location_is_not_treated_as_success(
    settings, requests_seen: list[str]
) -> None:
    """3xx 却没有 Location：不能当成页面拿到了。"""
    response = httpx.Response(302, request=httpx.Request("GET", PUBLIC))
    fetcher = HttpxFetcher(settings, client=_client({PUBLIC: response}), resolver=FakeResolver())
    result = fetcher.fetch(FetchRequest(url=PUBLIC))

    assert result.outcome is FetchOutcome.BLOCKED
    assert result.block_reason is BlockReason.UNSAFE_TARGET


def test_a_transport_failure_keeps_its_hop_on_the_chain(settings, requests_seen: list[str]) -> None:
    """超时或传输错误的那一跳**在**链上，与被拒的那一跳相反。

    判据不是「成功过的才记」，而是「真的发起过的才记」：这份回执说的就是那一跳，
    把它从链上抹掉会让「问过谁」少一个地址。两条合起来才说明链的边界画在哪。
    """

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectTimeout("连接超时")

    fetcher = HttpxFetcher(
        settings,
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        resolver=FakeResolver(),
    )
    result = fetcher.fetch(FetchRequest(url=PUBLIC))

    assert result.outcome is FetchOutcome.TIMEOUT
    assert result.redirect_trace == (PUBLIC,)


# ----------------------------------------------------------------------
# 浏览器侧：同一条判据，第二个实现。
#
# 浏览器比 HTTP 更危险：它带 cookie、localStorage 与一个真实的 JS 环境。
# 把它指向环回地址上的内部服务，等于让一个带状态的客户端去敲内网的门。
# 所以同一条判据在升级路径上必须**再执行一次** —— 两次之间的 URL 可能已经
# 不是同一个（升级发生在 HTTP 结果上，而结果是按起始 URL 记录的）。
# ----------------------------------------------------------------------
def test_browser_fetcher_refuses_unsafe_targets_before_starting_a_browser(
    settings, requests_seen: list[str]
) -> None:
    fetcher = Crawl4AIBrowserFetcher(settings)
    result = fetcher.fetch(FetchRequest(url=METADATA))

    _check(result, tier=fetcher.tier)
    assert requests_seen == []


def test_browser_fetcher_refuses_unsafe_targets_even_without_a_browser(settings) -> None:
    """判据在**可用性检查之前**执行。

    顺序是有意的：不安全目标与「本机没装浏览器」无关，它的拒绝理由不该
    随一台机器的依赖状态而变。否则同一份输入在两台机器上得到两种结论，
    而其中一种会把排查方向引向依赖安装。
    """
    fetcher = Crawl4AIBrowserFetcher(settings)
    result = fetcher.fetch(FetchRequest(url=LOOPBACK))
    assert result.outcome is FetchOutcome.BLOCKED


def test_browser_fetcher_refuses_a_name_that_resolves_into_private_space(settings) -> None:
    """同上，但字面量是**公开域名**：只有解析那一半能挡住它。

    浏览器比直连更值得在这里再判一次 —— 它带 cookie、localStorage 与一个真实的
    JS 环境，把它指向一个解析到内网的名字，等于让一个带状态的客户端去敲内网的门。
    """
    resolver = FakeResolver({"example.co.uk": (PRIVATE_ADDRESS,)})
    fetcher = Crawl4AIBrowserFetcher(settings, resolver=resolver)
    result = fetcher.fetch(FetchRequest(url=PUBLIC))

    _check(result, tier=fetcher.tier)
    assert result.reason == "target_refused:resolves_to_private"
    assert resolver.calls == [("example.co.uk", 443)]


def test_a_browser_side_dns_failure_is_an_error_and_keeps_its_subject(settings) -> None:
    """解析失败在浏览器那一层也是 ``ERROR``，而且回执指的还是**原来那份请求**。

    ``scope`` 会逐字成为 ``Evidence.account_id`` 与页面镜像的目录名。这一格此前
    照 URL 新造一份 ``FetchRequest``，于是 ``scope`` 与 ``purpose`` 静默回落到
    缺省值 —— 一次没解析成功的浏览器抓取会记在一个**没有归属的主体**上，而报告
    上它与「这次运行什么都没抓到」同形。
    """
    resolver = FakeResolver({"example.co.uk": _nxdomain("example.co.uk")})
    fetcher = Crawl4AIBrowserFetcher(settings, resolver=resolver)
    request = FetchRequest(url=PUBLIC, scope="test-account-001", purpose="contact")
    result = fetcher.fetch(request)

    assert result.outcome is FetchOutcome.ERROR, "「没问到」不是「不允许去」—— 不该是 BLOCKED"
    assert result.block_reason is None
    assert result.reason == "dns:nonexistent"
    assert result.request is request, "回执必须带着**原来那份**请求，而不是照 URL 新造一份"
    assert result.request.scope == "test-account-001"
    assert result.request.purpose == "contact"


# ----------------------------------------------------------------------
# 解析那一半：判完之后，连接必须钉在量到的那个地址上
# ----------------------------------------------------------------------
def test_a_name_that_resolves_to_metadata_is_never_requested(
    settings, requests_seen: list[str]
) -> None:
    """本条判据存在的理由：URL 里的名字是公开的，而它通向元数据服务。

    只判字面量的实现在这里会放行 —— 而那时请求已经离开了进程。断言
    ``requests_seen == []`` 而不是「结果是 BLOCKED」：后者对一次已经发出去的
    SSRF 也一样成立。
    """
    resolver = FakeResolver({"example.co.uk": (METADATA_ADDRESS,)})
    fetcher = HttpxFetcher(settings, client=_client({}), resolver=resolver)

    result = fetcher.fetch(FetchRequest(url=PUBLIC))

    _check(result, tier=fetcher.tier)
    assert result.reason == "target_refused:resolves_to_private"
    assert METADATA_ADDRESS in result.error, "报告里要写出**实测到**的那个地址"
    assert requests_seen == [], f"名字解析到 {METADATA_ADDRESS}，请求却仍然发出去了"
    assert result.redirect_trace == (), "这一跳没发出去，所以链上不该有它"


def test_one_private_address_among_public_ones_is_enough_to_refuse(
    settings, requests_seen: list[str]
) -> None:
    """判据对**每一个**返回的地址都点头，而不是「至少一个安全就放行」。

    地址顺序是**后置**的公开地址在前：一个只看 ``addresses[0]`` 的实现会通过
    上一条用例（那种情况下它拒的是第一个），在这里却会把一个公开地址钉上、
    然后在轮到那个内网地址时把它连上 —— 而这一次与上一次的判决长得一模一样。
    这正是 DNS rebinding 的形状。
    """
    resolver = FakeResolver({"example.co.uk": (PUBLIC_ADDRESS, PRIVATE_ADDRESS)})
    fetcher = HttpxFetcher(settings, client=_client({}), resolver=resolver)

    result = fetcher.fetch(FetchRequest(url=PUBLIC))

    _check(result, tier=fetcher.tier)
    assert requests_seen == []


def test_the_connection_is_pinned_to_the_first_resolved_address(
    settings, wire_seen: list[str]
) -> None:
    """发出去的那一行真的是那个地址 —— 这不是「判完之后客户端自己再解析一次」。

    两个都是公开地址时取第一个（解析器给的顺序），所以判决是确定的：同一个名字
    在两台机器上不会一次连 A、一次连 B。
    """
    resolver = FakeResolver({"example.co.uk": (PUBLIC_ADDRESS, SECOND_ADDRESS)})
    fetcher = HttpxFetcher(
        settings, client=_client({PUBLIC: _ok(PUBLIC)}), resolver=resolver
    )
    result = fetcher.fetch(FetchRequest(url=PUBLIC))

    assert result.outcome is not FetchOutcome.BLOCKED
    assert wire_seen == [f"https://{PUBLIC_ADDRESS}/contact"], (
        f"线上那一行是 {wire_seen} —— 主机名没有被换成解析出来的地址"
    )
    assert result.final_url == PUBLIC, "而报告上的地址仍是站点身份"


def test_pinning_keeps_the_name_in_the_host_header_and_the_tls_extension() -> None:
    """钉住的三处改写逐处钉住：URL 换成地址，``Host`` 与 ``sni_hostname`` 留名字。

    为什么 ``sni_hostname`` 是承重的：``httpcore`` 用它作为 TLS 的
    ``server_hostname``。少了它，一次 https 抓取会变成证书不匹配 —— 而它**只在
    真的连上时**才显形，本文件的假传输永远看不到。

    ``User-Agent`` 那一条同样不是细节：``httpx.Request(...)`` 不会合并客户端级的
    默认头，而 ``client.build_request`` 会。换掉之后站点看到的 UA 就变了，而这件事
    在测试里与生产上都不报错。
    """
    client = httpx.Client(
        transport=httpx.MockTransport(lambda request: httpx.Response(200, request=request)),
        headers={"User-Agent": "probe/1.0"},
    )

    plain = _pin(client, PUBLIC, PUBLIC_ADDRESS)
    assert str(plain.url) == f"https://{PUBLIC_ADDRESS}/contact"
    assert plain.headers["host"] == "example.co.uk"
    assert plain.extensions["sni_hostname"] == "example.co.uk"
    assert plain.headers["user-agent"] == "probe/1.0"

    # 非默认端口要写进 Host 头（有些虚拟主机按字面量匹配，少一个端口就得到一个
    # 404 而看不出原因），但不写进 sni_hostname。
    ported = _pin(client, "https://example.co.uk:8443/x", PUBLIC_ADDRESS)
    assert str(ported.url) == f"https://{PUBLIC_ADDRESS}:8443/x"
    assert ported.headers["host"] == "example.co.uk:8443"
    assert ported.extensions["sni_hostname"] == "example.co.uk"

    # IPv6 字面量必须带方括号，否则 ``urlunsplit`` 会把冒号读成端口分隔符。
    literal = "2606:2800:220:1:248:1893:25c8:1946"
    v6 = _pin(client, PUBLIC, literal)
    assert str(v6.url) == f"https://[{literal}]/contact"
    assert v6.headers["host"] == "example.co.uk"
    assert v6.extensions["sni_hostname"] == "example.co.uk"

    # 路径里的百分号编码原样保留：按解码后的路径重拼会把 ``%20`` 变成空格。
    encoded = _pin(client, "https://example.co.uk/cont%20act?q=1", PUBLIC_ADDRESS)
    assert str(encoded.url) == f"https://{PUBLIC_ADDRESS}/cont%20act?q=1"


@pytest.mark.parametrize(
    ("kind", "reason"),
    [
        (DnsFailureKind.NONEXISTENT, "dns:nonexistent"),
        (DnsFailureKind.TIMEOUT, "dns:timeout"),
        (DnsFailureKind.FAILURE, "dns:failure"),
    ],
)
def test_each_dns_failure_kind_keeps_its_own_reason(
    settings, requests_seen: list[str], kind: DnsFailureKind, reason: str
) -> None:
    """三种失败在下游的处置不同，所以回执上必须分得开。

    ``nonexistent`` 说的是**这个来源可能没了**（往 ``SOURCE_GONE`` 走），
    ``timeout`` / ``failure`` 说的是**我们这次没问到**（有界退避后再试）。
    合成一栏的后果不是少一点信息：一个已经消失的域名会被无限重试。
    """
    resolver = FakeResolver({"gone.example.co.uk": DnsResolutionError("gone.example.co.uk", kind)})
    fetcher = HttpxFetcher(settings, client=_client({}), resolver=resolver)

    result = fetcher.fetch(FetchRequest(url=GONE))

    assert result.outcome is FetchOutcome.ERROR, "解析失败不是终态拒绝 —— 它不该是 BLOCKED"
    assert result.block_reason is None
    assert result.reason == reason
    assert requests_seen == [], "没解析出地址就没有目标可发 —— 但仍然不该发出任何请求"


def test_a_dns_failure_does_not_leave_a_mirror(settings, requests_seen: list[str]) -> None:
    """没抓到的字节一个都不落盘，``body_sha256`` 也不是一个算出来的值。"""
    resolver = FakeResolver({"gone.example.co.uk": _nxdomain("gone.example.co.uk")})
    fetcher = HttpxFetcher(settings, client=_client({}), resolver=resolver)

    result = fetcher.fetch(FetchRequest(url=GONE, scope="test-account-001"))

    assert result.body_path is None
    assert result.body_sha256 == ""
    assert result.body_bytes == 0
