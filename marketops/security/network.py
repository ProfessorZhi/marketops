"""URL 判据：**这个地址允不允许被访问**，以及**这个监听地址允不允许被绑定**。

## 为什么它住在 ``security/`` 而不是抓取层

一条判据住在用它的人旁边，就会长出第二个副本 —— 浏览器抓取器与 HTTP 抓取器
各自实现一遍，然后其中一份先被修好。这里只有一份，两个抓取器都调它。

## 两个方向，一条纪律

* **出**：抓取目标必须是一个公开地址。``127.0.0.1`` 在这里是**禁止的目标**。
* **入**：服务面只允许绑在 ``127.0.0.1``。同一个地址在这里是**唯一允许的**值。

放在同一个模块里不是巧合 —— 它们是同一句话的两面：本机服务不出网，不出网的
服务不抓本机。分成两个文件之后，读的人会以为这是两条独立的偏好。

## 判据的形状

``check_target`` 对一个 URL 给出「允许 / 拒绝 + 理由码 + 一句人话」。理由码是
**闭集**（``TargetRefusal``），因为开放字符串的第一个拼错的名字会静静开出一条
新类别，而下游按类别分支的地方会落进 else。

判据必须是**可逐跳调用的纯函数**：重定向保护的全部意义在于每一跳都过一遍它，
而一个「自己跟完重定向再判断结果」的实现在请求已经发出去之后才发现目标不安全
—— 那时该发生的事已经发生了。

## 两半：写下来的目标，与它实际通向哪里

``check_target`` 判的是 URL 里的字面量（协议、凭据、名字、字面 IP）；
``check_resolved`` 判的是 **DNS 量回来的地址**。两者都是纯函数 —— 网络那一次
测量由 ``ports/dns.py::HostResolver`` 做，本模块只负责对量回来的事实下判断。

只有前一半时，``http://a-name-that-resolves-to-169.254.169.254/`` 会一路放行。
而**判完之后**把连接钉在量到的那个地址上，是调用方（``adapters/http.py``）的事：
判据回答「允不允许」，不回答「连到哪」—— 后者需要一次真实的连接，而本模块
刻意不碰网络。
"""

from __future__ import annotations

import ipaddress
import re
from collections.abc import Sequence
from dataclasses import dataclass
from enum import Enum
from urllib.parse import urlsplit

__all__ = [
    "ALLOWED_SCHEMES",
    "MAX_REDIRECTS",
    "RESERVED_HOSTS",
    "RESERVED_HOST_SUFFIXES",
    "TargetRefusal",
    "TargetVerdict",
    "check_resolved",
    "check_target",
    "is_loopback_host",
]

#: 本进程愿意发起的惟二两种协议。
#:
#: ``file`` / ``gopher`` / ``data`` / ``javascript`` 不是「另一种抓取方式」，
#: 而是「另一类资源」—— 允许它们等于把抓取器变成一个通用的本地文件读取原语。
ALLOWED_SCHEMES: frozenset[str] = frozenset({"http", "https"})

#: 一条链上最多跟几次。取 5 而不是 20：正常站点的重定向链是 0–2 跳，
#: 一条 5 跳还没到终点的链在实践里就是环，继续跟只是在拖住一个 worker。
MAX_REDIRECTS = 5

#: 结构上不可能是公开站点的**完整**主机名。
RESERVED_HOSTS: frozenset[str] = frozenset({"localhost"})

#: 结构上不可能是公开站点的**后缀**。这些是 RFC 6761/6762 保留给本地与
#: 测试用途的名字空间，公网 DNS 里不该有它们 —— 因此也不该被当作公开来源。
#:
#: 刻意**不含** ``example.com`` / ``example.net`` / ``example.org``：它们虽然
#: 也被保留作文档用途，但拒绝它们会让测试与示例里的 URL 变成非法目标，
#: 而「示例不可用」的代价会落在每一个写文档的人身上。
RESERVED_HOST_SUFFIXES: tuple[str, ...] = (
    ".localhost",
    ".local",
    ".internal",
    ".home.arpa",
    ".test",
    ".invalid",
    ".example",
)


class TargetRefusal(str, Enum):
    """拒绝理由的闭集。"""

    SCHEME = "scheme"
    """不是 http(s)，或者根本没写协议。"""

    USERINFO = "userinfo"
    """URL 里带了 ``user:pass@`` —— 典型的混淆手法（可信域名写在 ``@`` 前面）。"""

    NO_HOST = "no_host"
    """解析之后没有主机名。"""

    PRIVATE_TARGET = "private_target"
    """解析出来是 IP，且不是全局可路由地址（私网 / 环回 / 链路本地 / CGNAT …）。"""

    RESERVED_HOST = "reserved_host"
    """主机名属于保留名字空间（``localhost`` / ``*.local`` / ``*.test`` …）。"""

    NUMERIC_HOST = "numeric_host"
    """主机名写成数字的某种变体（十进制大整数、``0x`` 十六进制、带前导零的点分）。"""

    RESOLVES_TO_PRIVATE = "resolves_to_private"
    """名字本身公开，**解析出来的地址**里至少有一个不是全局可路由。

    与 :attr:`PRIVATE_TARGET` 分开，因为两者读起来是两句不同的话：那一句是
    「有人把一个内网地址写进了 URL」，这一句是「这个公开域名指向内网」——
    后者的典型成因是 DNS 劫持与 rebinding，处置也不同（前者是一次配置错误，
    后者是一个必须记下来的现象）。实测的那一个地址会写进 ``detail``。"""

    REDIRECT_LIMIT = "redirect_limit"
    """重定向链超过了 :data:`MAX_REDIRECTS` —— 实践里就是环。"""

    REDIRECT_WITHOUT_LOCATION = "redirect_without_location"
    """3xx 却没有 ``Location``：下一跳无法确定，所以不能当成「页面拿到了」。"""


@dataclass(frozen=True)
class TargetVerdict:
    """一次判定的结果。

    拒绝时 :attr:`detail` 必须非空 —— 只有理由码的报告需要读代码才能解释，
    而读代码的人不是收到报告的人。
    """

    allowed: bool
    refusal: TargetRefusal | None = None
    detail: str = ""


#: 一个「数字形态的主机名」的单个标签：十进制整数，或 ``0x`` 十六进制。
#: 带前导零的十进制（``0177``）落在这里 —— ``ipaddress`` 会拒绝它，
#: 而历史的 ``inet_aton`` 会把它读成八进制。
_NUMERIC_LABEL = re.compile(r"^(?:0x[0-9a-f]+|[0-9]+)$")


def _looks_numeric(host: str) -> bool:
    """主机名是不是「数字的另一种写法」。

    需要这一条，是因为 ``ipaddress`` **解析失败**并不等于这个主机名是安全的：
    ``http://2130706433/`` 会被多数解析器读成 ``127.0.0.1``，而在这里它只是
    一个解析不出 IP 的字符串。判据必须自己认出它。

    判据刻意**不是**「全部字符都在 ``0-9a-fx.`` 里」——那会把真实域名
    （``a1.de``、``face.b00k.de``）误判成数字。要求是每个标签**整体**是数字
    的某种写法：这样 ``1.2.3.4.5`` 与 ``0177.0.0.1`` 命中，而 ``a1.de`` 不命中。
    """
    if _NUMERIC_LABEL.match(host):
        return True
    labels = host.split(".")
    return len(labels) >= 2 and all(_NUMERIC_LABEL.match(label) for label in labels)


def _is_global(host: str) -> bool:
    """主机名解析成 IP 时，它是不是一个全局可路由地址。

    非 IP 的主机名返回 ``False`` —— 那种情况由别的规则处置（保留名字空间），
    这里只回答「它是一个 IP 吗，是的话是不是公开的」。
    """
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return False
    # ``::ffff:127.0.0.1`` 是一个 IPv4-mapped 地址：``is_global`` 对它会看
    # IPv4 那一半，因此这里不必单独处理 —— 这一条由测试钉住。
    return address.is_global


def check_target(url: str) -> TargetVerdict:
    """判断一个 URL 允不允许被访问。

    判定顺序是有讲究的：**先协议，再凭据，再主机**。协议不合法时连主机都不解析
    （``javascript:`` 的「主机」没有意义）；带 userinfo 的 URL 直接拒绝，不去看
    ``@`` 后面那个真实主机 —— 那是这类混淆手法的全部目的。
    """
    raw = url.strip()
    if not raw:
        return TargetVerdict(False, TargetRefusal.SCHEME, "空 URL")

    parts = urlsplit(raw)

    if parts.scheme.lower() not in ALLOWED_SCHEMES:
        return TargetVerdict(
            False,
            TargetRefusal.SCHEME,
            f"协议 {parts.scheme!r} 不在允许范围内（只允许 "
            f"{sorted(ALLOWED_SCHEMES)}）；没有协议的相对 URL 在抓取层没有基准",
        )

    if parts.username is not None or parts.password is not None:
        return TargetVerdict(
            False,
            TargetRefusal.USERINFO,
            "URL 带 userinfo（``user@host``）—— ``@`` 前面的域名常被用来伪装真实目标",
        )

    host = (parts.hostname or "").strip().lower()
    # FQDN 末尾的根点不改变解析结果，去掉它，免得 ``127.0.0.1.`` 与
    # ``127.0.0.1`` 得到两个判定。
    host = host[:-1] if host.endswith(".") and len(host) > 1 else host

    if not host:
        return TargetVerdict(False, TargetRefusal.NO_HOST, "URL 里没有主机名")

    if host in RESERVED_HOSTS or host.endswith(RESERVED_HOST_SUFFIXES):
        return TargetVerdict(
            False,
            TargetRefusal.RESERVED_HOST,
            f"{host!r} 属于保留名字空间（``localhost`` / ``*.local`` / ``*.test`` …），"
            "公网 DNS 里不会有它",
        )

    try:
        ipaddress.ip_address(host)
    except ValueError:
        if _looks_numeric(host):
            return TargetVerdict(
                False,
                TargetRefusal.NUMERIC_HOST,
                f"{host!r} 是数字形态的主机名（十进制大整数 / 十六进制 / 前导零），"
                "多数解析器会把它读成一个 IP",
            )
    else:
        if not _is_global(host):
            return TargetVerdict(
                False,
                TargetRefusal.PRIVATE_TARGET,
                f"{host!r} 不是全局可路由地址（私网 / 环回 / 链路本地 / CGNAT / 未指定）",
            )

    return TargetVerdict(True)


def check_resolved(host: str, addresses: Sequence[str]) -> TargetVerdict:
    """这个名字**解析出来的**那些地址，允不允许被连上去。

    ``check_target`` 判的是 URL 里的字面量，本函数判的是 DNS 量回来的事实，
    两者是同一句话的两半：前者答「你写的是个什么目标」，后者答「它实际通向哪里」。
    只有前者时，``http://a-name-that-resolves-to-169.254.169.254/`` 会一路放行 ——
    而那时请求已经离开了进程。

    ## 判据是「全部」而不是「至少一个」

    一个名字返回多个地址时，只要**有一个**不全局可路由就整条拒绝。

    可以想到的另一种做法是「挑一个安全的地址连上去」，它同样挡得住这次连接。
    拒绝它的理由是它**把一次现象改写成了一次路由**：一个公开域名回一个内网地址
    是必须被记下来的事（DNS 劫持 / rebinding 的典型形状），而「绕开它往上连」
    会让这件事在报告上不留痕迹。何况挑哪一个由应答顺序决定，而顺序由解析器
    决定 —— 于是同一次抓取在两台机器上连的可能是两个不同的地址，而判决一模一样。
    这正是 DNS rebinding 的形状。

    ## 空地址是**编程错误**，不是一次判决

    本函数要求 ``addresses`` 非空并因此抛 ``ValueError``：空元组意味着调用方
    把一次解析失败（``ports/dns.py::DnsResolutionError``）当成了测量结果。
    让它在这里静默通过，等于留了一条「没量到就没问题」的路。

    ``detail`` 里写的是**实测到的那个地址**。地址不是凭据，可以进报告；
    而出网身份的名字（``EgressProfile``）才是报告里该用的那一格。
    """
    if not addresses:
        raise ValueError(
            "check_resolved 收到空地址列表 —— 解析不出来是 DnsResolutionError，"
            "不是「解析出零个地址」；把它折成空元组会让这里静默放行"
        )

    for address in addresses:
        if not _is_global(address):
            return TargetVerdict(
                False,
                TargetRefusal.RESOLVES_TO_PRIVATE,
                f"{host!r} 解析到 {address!r}，那不是全局可路由地址 —— "
                "公开域名指向内网（DNS 劫持 / rebinding 的典型形状），"
                "或者名字被写错了",
            )
    return TargetVerdict(True)


def is_loopback_host(host: str) -> bool:
    """这个主机名是不是一个环回地址。

    这里 :func:`check_target` 之外的第二个方向：抓取层用它来**拒绝**，服务面
    用它来**接受**。刻意处理 ``localhost`` 这个名字：它是环回，但它是**名字**，
    因此在绑定判据那一侧还要再被拒一次（见 ``interfaces/mcp/__main__.py``）。
    """
    candidate = host.strip().lower()
    if candidate.startswith("[") and candidate.endswith("]"):
        candidate = candidate[1:-1]
    if candidate in RESERVED_HOSTS or candidate.endswith(".localhost"):
        return True
    try:
        return ipaddress.ip_address(candidate).is_loopback
    except ValueError:
        return False
