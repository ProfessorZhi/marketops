"""一串**真的** ``httpx`` 响应 + 假的 DNS：抓取器那一层判据的共用架子。

## 为什么替身必须在这一层停住

``tests/fixtures/http_fakes.py`` 的替身替掉的是**抓取这件事**（它自己按一张表
回答 ``FetchResult``）。用它去测治理层、诊断层、尝试账，测出来的是替身自己的
实现 —— 而这三样恰好都是「夹在传输与回执之间」的规则。

所以这里的替身只替两样：**网络**（``httpx.MockTransport``，真的构造
``httpx.Response``）与 **DNS**（``FakeResolver``，一张字面表）。抓取器本身、
它的重定向循环、它的诊断、它的记账全是真的。最承重的那几条断言因此可以写成
``len(REQUESTS) == ...`` 这样的**计数**：它们证明某一跳**根本没有离开进程**，
而不是「离开了但结果被丢弃了」—— 后者对采集设计 §5 那些要求毫无意义。

``FakeResolver`` 不问真实 DNS 是硬要求：§7 明令不向真实内网或云 metadata 探测，
而「这个名字解析到 169.254.169.254」那条判据只能靠一个会说这句话的解析器走到。

## 为什么它住在 ``fixtures/`` 而不是某一个测试文件里

它现在有两个消费者：``tests/test_http_access_governance.py``（治理层接在请求路径
上吗）与 ``tests/test_http_fetch_attempts.py``（每一次尝试都留一行账吗）。
抄第二份的代价不是多几行，而是**两份架子会分叉** —— 一份修了「未登记的地址该
断言失败」，另一份没修，于是两个文件对同一件事给出不同答案，而两边都是绿的。
``tests/fixtures/http_fakes.py`` 的模块 docstring 里写着同一条理由。

## 记录器是模块级的，而清理在夹具里

``REQUESTS``（站点看到的地址）与 ``WIRE``（线上那一行，钉住之后主机名是地址）
是对**真的发生过什么**的记录，所以它们是模块级的可变列表；清理由 ``conftest.py``
的 ``requests_seen`` 夹具在每个用例开始时做。两个列表分开记的理由见
``tests/test_acquisition_target_policy.py`` 的模块 docstring。
"""

from __future__ import annotations

import ipaddress
from collections.abc import Callable
from pathlib import Path

import httpx

from marketops.adapters.http import HttpxFetcher
from marketops.config import Settings
from marketops.domain.access_governance import AccessGovernor, AccessPolicy
from marketops.domain.fetch_attempt import MaterialProvenance
from marketops.ports.dns import DnsFailureKind, DnsResolutionError, HostResolver
from marketops.ports.fetch_attempt import FetchAttemptStore

__all__ = [
    "CHALLENGE_BODY",
    "PAGE",
    "PLAIN_BODY",
    "PUBLIC_ADDRESS",
    "REQUESTS",
    "SECOND",
    "SITE",
    "WIRE",
    "FailingResolver",
    "FakeResolver",
    "Route",
    "client",
    "logical",
    "make_fetcher",
    "mirrored",
    "page",
    "redirect",
    "reset",
]

SITE = "https://example.co.uk"
PAGE = f"{SITE}/contact"
SECOND = f"{SITE}/contact-us"

#: 假解析器给出的地址。必须**真的**全局可路由（``check_resolved`` 要判它），
#: 理由见 ``tests/test_acquisition_target_policy.py`` 里同一格的说明。
PUBLIC_ADDRESS = "93.184.216.34"

#: 一个正常页面：正文里没有任何挑战指纹、也没有登录/付费墙词。
PLAIN_BODY = (
    "<html><body><h1>Devon county grounds</h1>"
    "<p>Natural grass pitch maintenance for clubs across Devon.</p></body></html>"
)
#: 一个挑战页：``just a moment`` / ``checking your browser`` 都在两边的指纹表里。
CHALLENGE_BODY = (
    "<html><body><h1>Just a moment...</h1>"
    "<p>Checking your browser before accessing example.co.uk.</p></body></html>"
)

#: 真的**发出**了的那几跳，按顺序（站点看到的地址）。
REQUESTS: list[str] = []
#: 线上那一行（钉住之后主机名是地址）。与上面分开记，理由同
#: ``tests/test_acquisition_target_policy.py`` 的模块 docstring。
WIRE: list[str] = []


def reset() -> None:
    """把两条记录清空。由 ``conftest.py`` 的 ``requests_seen`` 夹具调用。"""
    REQUESTS.clear()
    WIRE.clear()


class FakeResolver(HostResolver):
    """一张字面表。**不问真实 DNS** —— §7 明令不向真实内网或云 metadata 探测。

    ``calls`` 只为让「解析发生在哪一跳」可被断言。
    """

    def __init__(self, address: str = PUBLIC_ADDRESS) -> None:
        self._address = address
        self.calls: list[str] = []

    def resolve(self, host: str, port: int) -> tuple[str, ...]:
        self.calls.append(host)
        return (self._address,)


class FailingResolver(HostResolver):
    """一个**答不出来**的解析器。``kind`` 决定是哪一种答不出来。

    三种失败在 ``ports/dns.py`` 里是两句话（``NONEXISTENT`` 是「这个来源可能没了」，
    另外两个是「我们这次没问到」），而后者又分成可重试与不可重试以外的区别 ——
    所以造局面的时候必须说清是哪一种，而不是笼统的「解析失败」。
    """

    def __init__(self, kind: DnsFailureKind) -> None:
        self._kind = kind
        self.calls: list[str] = []

    def resolve(self, host: str, port: int) -> tuple[str, ...]:
        self.calls.append(host)
        raise DnsResolutionError(host, self._kind, f"测试造的 {self._kind.value}")


#: 一个登记在册的答案：一次响应，**或**一个异常实例（＝一次传输层失败）。
Route = httpx.Response | Exception


def logical(request: httpx.Request) -> str:
    """这一跳**站点看到的**地址 = 协议 + ``Host`` 头 + 路径。"""
    authority = request.headers.get("host") or (request.url.host or "")
    query = f"?{request.url.query.decode('ascii')}" if request.url.query else ""
    return f"{request.url.scheme}://{authority}{request.url.path}{query}"


def client(routes: dict[str, Route]) -> httpx.Client:
    """一个只会答 ``routes`` 里那些地址的 client；未登记的一律断言失败。

    每一跳都断言两件事，与 ``tests/test_acquisition_target_policy.py`` 那一对同源
    （理由见那边的 docstring）：线上那一行的主机名**是一个 IP 字面量**，且
    ``sni_hostname`` 仍是原来的域名。少了它们，一个「忘了钉」的实现会照样全绿。

    ``routes`` 的值可以是一个**异常实例** —— 那就是一次传输层失败：请求真的发出去了
    （已记进 ``WIRE``），而没有回执。
    """

    def handler(request: httpx.Request) -> httpx.Response:
        seen = logical(request)
        WIRE.append(str(request.url))
        REQUESTS.append(seen)

        try:
            ipaddress.ip_address(request.url.host or "")
        except ValueError:
            raise AssertionError(
                f"这一跳的主机名是 {request.url.host!r}，不是地址 —— 连接没有钉在 "
                "DNS 量回来的结果上"
            ) from None
        name = (request.headers.get("host") or "").rsplit(":", 1)[0]
        assert request.extensions.get("sni_hostname") == name, (
            f"TLS 的 sni_hostname 是 {request.extensions.get('sni_hostname')!r}，"
            f"不是 {name!r}"
        )

        route = routes.get(seen)
        if route is None:
            raise AssertionError(f"测试没有为 {seen} 登记响应 —— 这一跳不该被发出")
        if isinstance(route, Exception):
            raise route
        return route

    return httpx.Client(transport=httpx.MockTransport(handler))


def page(
    body: str = PLAIN_BODY,
    *,
    status: int = 200,
    headers: dict[str, str] | None = None,
    url: str = PAGE,
) -> httpx.Response:
    merged = {"content-type": "text/html; charset=utf-8"}
    merged.update(headers or {})
    return httpx.Response(
        status,
        headers=merged,
        content=body.encode(),
        request=httpx.Request("GET", url),
    )


def redirect(from_url: str, to_url: str) -> httpx.Response:
    return httpx.Response(
        302,
        headers={"location": to_url},
        request=httpx.Request("GET", from_url),
    )


def make_fetcher(
    settings: Settings,
    routes: dict[str, Route],
    *,
    governor: AccessGovernor | None = None,
    policy: AccessPolicy | None = None,
    resolver: HostResolver | None = None,
    attempts: Callable[[], FetchAttemptStore] | None = None,
    material: MaterialProvenance = MaterialProvenance.SYNTHETIC,
) -> HttpxFetcher:
    """一个真的抓取器：假传输 + 假 DNS + 注入的那本账。

    **不叫** ``fetcher`` 是因为每一个用它的用例都写 ``fetcher = ...``：同名之后
    ``fetcher = fetcher(settings, ...)`` 里的那个名字是局部变量，右边那次调用会
    撞上 ``UnboundLocalError`` —— 而现场读起来像「这个函数不存在」。

    ``policy`` 单独一格而不是让调用方自己 ``AccessGovernor(policy=...)``：本仓里
    「一次策略只影响这一次抓取」的写法到处都是，收在一处之后，改签名时不会漏掉
    某一个调用点（``governor`` 显式传入时 ``policy`` 被忽略 —— 一本已经建好的账
    不换策略）。

    ``policy=None`` 走 ``AccessGovernor()`` 的缺省构造，而**不是**
    ``AccessGovernor(policy=None)``：那个字段是 ``field(default_factory=...)``，
    显式传 ``None`` 会把策略整个置空，而现场是后面每一处 ``self.policy`` 上的
    ``AttributeError`` —— 与「用缺省值」看起来差得很远。

    ``resolver=None`` 走那个答全局可路由地址的 :class:`FakeResolver`。要造
    「这个名字解析到内网」或「这个名字答不出来」时必须换一个 —— 而那种局面在真实
    DNS 上不可构造（§7 明令不向真实内网或云 metadata 探测），所以这一格是可注入的。

    ``material`` 缺省取**收回声明**的那一个成员，所以缺省调用造出来的是一个
    「没有声明过」的抓取器 —— 那正是这一格绝大多数消费者要的（这个架子替掉了网络，
    拿到的就是它自己造的响应），而「测试替掉的网络该不该算现场覆盖」这条问题由
    ``tests/test_material_provenance.py`` 判。要造一次**声明过**的抓取（例如验
    「声明真的到了盘上」）才传它。
    """
    if governor is None:
        governor = AccessGovernor() if policy is None else AccessGovernor(policy=policy)
    return HttpxFetcher(
        settings,
        client=client(routes),
        resolver=FakeResolver() if resolver is None else resolver,
        governor=governor,
        attempts=attempts,
        material=material,
    )


def mirrored(settings: Settings) -> list[Path]:
    """盘上所有镜像文件。**数文件**而不是问抓取器 —— 后者只会重复它自己的说法。"""
    root = settings.mirrors_dir
    if not root.is_dir():
        return []
    return sorted(path for path in root.rglob("*") if path.is_file())
