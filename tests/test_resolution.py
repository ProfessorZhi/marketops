"""``SystemResolver`` —— 默认解析器的三条承诺（去重、只问 TCP、按种类报错）。

它**不**被这些测试真的拿去问 DNS：``socket.getaddrinfo`` 被换成一个字面表。
理由与 ``tests/test_network_policy.py`` 里那个假解析器同一条 —— 判据要在**不向
真实内网或云 metadata 探测**的前提下被穷举（采集设计 §7）。这里换的是更下面
一层：换掉 libc 那一格，就能把「``EAI_AGAIN`` 该报成哪一种失败」这类问题问清楚，
而那件事在真实 DNS 上不可构造。

端口那一格也在这里钉住：签名收了 ``port``，就必须**真的**把它传下去 —— 一个
收了不用的参数会在日志里读成「按服务端口解析」而实际上没有。
"""

from __future__ import annotations

import socket

import pytest

from marketops.adapters.resolution import _kinds_for
from marketops.ports.dns import DnsFailureKind, DnsResolutionError, HostResolver


def _infos(*addresses: str) -> list[tuple]:
    return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (address, 443)) for address in addresses]


@pytest.fixture
def resolver() -> HostResolver:
    from marketops.adapters.resolution import SystemResolver

    return SystemResolver()


def test_the_implementation_is_the_port(resolver: HostResolver) -> None:
    """零参数可构造，且确实是那个端口 —— 端口上少实现一个方法这里就构造不出来。"""
    assert isinstance(resolver, HostResolver)


def test_addresses_are_deduplicated_and_kept_in_order(monkeypatch, resolver: HostResolver) -> None:
    """重复不改变判决，但会让「钉在第一个地址上」的日志读起来像有多个出口。"""
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda *args, **kwargs: _infos("93.184.216.34", "93.184.216.35", "93.184.216.34"),
    )

    assert resolver.resolve("example.co.uk", 443) == ("93.184.216.34", "93.184.216.35")


def test_only_tcp_stream_addresses_are_asked_for(monkeypatch, resolver: HostResolver) -> None:
    """不加这个约束时，``getaddrinfo`` 会为同一个名字回一串 UDP / SOCK_RAW 的
    地址族组合，于是「这个名字解析出几个地址」在一台机器上是 3 个、在另一台是 8 个
    —— 而判据要对每一个都点头，地址族噪声会把它变成一个取决于 libc 版本的判决。
    """
    seen: dict[str, object] = {}

    def fake(host: str, port: int, **kwargs: object) -> list[tuple]:
        seen.update({"host": host, "port": port, **kwargs})
        return _infos("93.184.216.34")

    monkeypatch.setattr(socket, "getaddrinfo", fake)
    resolver.resolve("example.co.uk", 8443)

    assert seen["type"] == socket.SOCK_STREAM
    assert seen["host"] == "example.co.uk"
    assert seen["port"] == 8443, "收了 port 就要真的传下去，否则日志会读成按服务端口解析"


@pytest.mark.parametrize(
    ("errno", "kind"),
    [
        (socket.EAI_NONAME, DnsFailureKind.NONEXISTENT),
        (socket.EAI_AGAIN, DnsFailureKind.TIMEOUT),
        (-12345, DnsFailureKind.FAILURE),
    ],
)
def test_a_gaierror_becomes_one_of_three_kinds(errno: int, kind: DnsFailureKind) -> None:
    """三种失败在下游的处置完全不同（来源没了 vs 这次没问到），所以必须分得开。"""
    assert _kinds_for(errno) is kind


def test_noname_and_nodata_are_the_same_answer() -> None:
    """``EAI_NODATA`` 在有些平台上与 ``EAI_NONAME`` **同值**。

    按元组判而不是按相等判，是为了让「两个名字指向同一个值」这一天不产生两种判决
    —— 任何一处按名字分支的写法都会漏掉另一个。
    """
    nodata = getattr(socket, "EAI_NODATA", None)
    if nodata is None:  # pragma: no cover - 取决于平台是否定义它
        pytest.skip("本平台没有 EAI_NODATA")
    assert _kinds_for(socket.EAI_NONAME) is _kinds_for(nodata)


def test_the_failure_carries_the_host_and_the_kind(monkeypatch, resolver: HostResolver) -> None:
    def fake(*args: object, **kwargs: object) -> list[tuple]:
        raise socket.gaierror(socket.EAI_NONAME, "Name or service not known")

    monkeypatch.setattr(socket, "getaddrinfo", fake)

    with pytest.raises(DnsResolutionError) as caught:
        resolver.resolve("gone.example.co.uk", 443)

    assert caught.value.host == "gone.example.co.uk"
    assert caught.value.kind is DnsFailureKind.NONEXISTENT
    assert "gone.example.co.uk" in str(caught.value), "报错信息里要有那个名字，否则报告上只有一栏失败"


def test_a_resolver_that_cannot_be_read_is_a_failure_not_a_missing_name(
    monkeypatch, resolver: HostResolver
) -> None:
    """``OSError`` 说的是「解析器本身坏了」（例如 ``/etc/resolv.conf``），
    不是「这个名字不存在」—— 后者会让一个好好的来源被记成已经消失。"""

    def fake(*args: object, **kwargs: object) -> list[tuple]:
        raise OSError("resolv.conf 打不开")

    monkeypatch.setattr(socket, "getaddrinfo", fake)

    with pytest.raises(DnsResolutionError) as caught:
        resolver.resolve("example.co.uk", 443)

    assert caught.value.kind is DnsFailureKind.FAILURE


def test_an_empty_answer_is_a_failure_not_an_empty_tuple(monkeypatch, resolver: HostResolver) -> None:
    """**空元组不是一种返回值。**

    返回 ``()`` 会让调用方把它读成「解析出零个地址」，而判据那一侧对此必须失败
    关闭 —— 一个「什么都没解析出来就放行」的实现，看起来与「解析成功」同形。
    """
    monkeypatch.setattr(socket, "getaddrinfo", lambda *args, **kwargs: [])

    with pytest.raises(DnsResolutionError) as caught:
        resolver.resolve("example.co.uk", 443)

    assert caught.value.kind is DnsFailureKind.NONEXISTENT
