"""URL 安全判据：一个**不允许**的 URL 长什么样（§10 / ``MCP_SERVICE.md`` §5）。

## 为什么这必须是一个可执行的判据

G 阶段之前，抓取层对 URL 的唯一态度是「httpx 收得下就发」。这条态度在单机
实验里看不出问题，因为发起人是我。而 MCP 落地之后，URL 的来源变成了
**调用方传进来的字符串** —— 那时「我们只抓公开页面」就只是一句话，
而 ``http://169.254.169.254/latest/meta-data/`` 会照发不误。

本文件把这句话变成一组会变红的事实。它与 ``tests/test_no_evasion.py`` 分工不同：
那条管的是「不去绕开站点的访问控制」，本条管的是「不去访问本不该被访问的目标」。

## 判据的形状：先说**允许**，再说拒绝

每一条规则都有正反两侧。只测拒绝的规则集是无法与「这个函数恒返回拒绝」
区分的 —— 而恒拒绝的实现会通过全部拒绝测试，同时让产品一行都抓不到。
所以 ``_ALLOWED`` 那一组与 ``_REFUSED`` 那一组**一样重要**。
"""

from __future__ import annotations

import pytest

from marketops.security.network import (
    ALLOWED_SCHEMES,
    MAX_REDIRECTS,
    TargetRefusal,
    check_resolved,
    check_target,
    is_loopback_host,
)

# ----------------------------------------------------------------------
# 允许：这些必须过去。
#
# 一个只会说「不」的判据不是判据。这一组的每一条都是一次**正常**的公开研究
# 抓取，若它们被拒绝，产品就没有数据。
# ----------------------------------------------------------------------
_ALLOWED: tuple[str, ...] = (
    "https://example.co.uk/",
    "http://example.co.uk/",
    "https://example.co.uk/contact-us?ref=footer",
    "https://www.example.co.uk/paints/natural-grass.pdf",
    "https://example.co.uk:443/",
    "http://example.co.uk:80/",
    "https://example.co.uk:8443/",
    "http://93.184.216.34/",
    "https://[2606:4700:4700::1111]/",
)


@pytest.mark.parametrize("url", _ALLOWED)
def test_ordinary_public_targets_are_allowed(url: str) -> None:
    verdict = check_target(url)
    assert verdict.allowed, f"{url} 被判为 {verdict.refusal}: {verdict.detail}"


# ----------------------------------------------------------------------
# 拒绝：每一组一个理由码。
#
# 参数化的第二列是**理由码**而不是「拒绝」二字 —— 因为「被拒绝」这个事实
# 无法回答「为什么」，而下一个读代码的人需要的正是那个答案。
# 把理由钉死也顺带证明：这些拒绝不是碰巧撞上某条更宽泛的规则。
# ----------------------------------------------------------------------
_REFUSED: tuple[tuple[str, TargetRefusal], ...] = (
    # -- 协议：只有 http/https 是被批准的网络访问方式 --
    ("file:///etc/passwd", TargetRefusal.SCHEME),
    ("ftp://example.co.uk/pub/", TargetRefusal.SCHEME),
    ("gopher://example.co.uk/", TargetRefusal.SCHEME),
    ("data:text/html,<h1>x</h1>", TargetRefusal.SCHEME),
    ("javascript:alert(1)", TargetRefusal.SCHEME),
    ("chrome://settings", TargetRefusal.SCHEME),
    # -- 没有协议：相对 URL 在抓取层没有「基准」，只能拒绝 --
    ("//example.co.uk/path", TargetRefusal.SCHEME),
    ("example.co.uk", TargetRefusal.SCHEME),
    ("", TargetRefusal.SCHEME),
    # -- 凭据：URL 里带 userinfo 是典型的 SSRF 混淆手法 --
    ("https://trusted.example.com@evil.example.com/", TargetRefusal.USERINFO),
    ("https://user:pass@example.co.uk/", TargetRefusal.USERINFO),
    # -- 内网 / 环回 / 链路本地 / CGNAT：一律不属于「公开页面」 --
    ("http://127.0.0.1/", TargetRefusal.PRIVATE_TARGET),
    ("http://127.0.0.1:80/admin", TargetRefusal.PRIVATE_TARGET),
    ("http://10.0.0.5/", TargetRefusal.PRIVATE_TARGET),
    ("http://192.168.1.1/", TargetRefusal.PRIVATE_TARGET),
    ("http://172.16.9.9/", TargetRefusal.PRIVATE_TARGET),
    ("http://169.254.169.254/latest/meta-data/", TargetRefusal.PRIVATE_TARGET),
    ("http://100.64.0.1/", TargetRefusal.PRIVATE_TARGET),
    ("http://0.0.0.0/", TargetRefusal.PRIVATE_TARGET),
    ("http://[::1]/", TargetRefusal.PRIVATE_TARGET),
    ("http://[fc00::1]/", TargetRefusal.PRIVATE_TARGET),
    ("http://[fe80::1]/", TargetRefusal.PRIVATE_TARGET),
    ("http://[::ffff:127.0.0.1]/", TargetRefusal.PRIVATE_TARGET),
    # -- 保留域名：这些名字**结构上**不可能是公开站点 --
    ("http://localhost/", TargetRefusal.RESERVED_HOST),
    ("http://LOCALHOST/", TargetRefusal.RESERVED_HOST),
    ("http://foo.localhost/", TargetRefusal.RESERVED_HOST),
    ("http://printer.local/", TargetRefusal.RESERVED_HOST),
    ("http://svc.internal/", TargetRefusal.RESERVED_HOST),
    ("http://thing.home.arpa/", TargetRefusal.RESERVED_HOST),
    ("http://x.test/", TargetRefusal.RESERVED_HOST),
    ("http://x.invalid/", TargetRefusal.RESERVED_HOST),
    ("http://x.example/", TargetRefusal.RESERVED_HOST),
    # -- 数字 / 十六进制形态的主机名：``ipaddress`` 解析不了的 IP 写法 --
    ("http://2130706433/", TargetRefusal.NUMERIC_HOST),
    ("http://0x7f000001/", TargetRefusal.NUMERIC_HOST),
    ("http://0177.0.0.1/", TargetRefusal.NUMERIC_HOST),
    # -- 没有主机 --
    ("https://", TargetRefusal.NO_HOST),
    ("https:///path", TargetRefusal.NO_HOST),
)


@pytest.mark.parametrize(("url", "expected"), _REFUSED, ids=[u for u, _ in _REFUSED])
def test_dangerous_targets_are_refused_with_a_reason(url: str, expected: TargetRefusal) -> None:
    verdict = check_target(url)
    assert not verdict.allowed, f"{url} 被放行了 —— 它必须被拒绝"
    assert verdict.refusal is expected, f"{url} 的拒绝理由应是 {expected}，实际 {verdict.refusal}"
    assert verdict.detail, "拒绝必须带一句人话说明，否则调用方只能看到理由码"


def test_refusal_vocabulary_is_closed() -> None:
    """拒绝理由是一个**闭集**。

    开放字符串的第一个拼错的名字会静静开出一条新的拒绝类别，而下游按类别
    分支的地方会落进 else。这条与 ``domain/enums.py::BudgetLine`` 同一条纪律。
    """
    assert {member.value for member in TargetRefusal} == {
        "scheme",
        "userinfo",
        "no_host",
        "private_target",
        "reserved_host",
        "numeric_host",
        "resolves_to_private",
        "redirect_limit",
        "redirect_without_location",
    }


def test_allowed_schemes_are_exactly_http_and_https() -> None:
    assert ALLOWED_SCHEMES == frozenset({"http", "https"})


def test_case_and_whitespace_do_not_open_a_hole() -> None:
    """大小写与首尾空白不得改变判定。

    浏览器与 URL 解析器对 ``HTTP://LOCALHOST/`` 的处理与 ``http://localhost/``
    相同，因此判据也必须相同 —— 否则「加一个空格」就是一条绕过路径。
    """
    for url in ("HTTP://LOCALHOST/", "  http://localhost/  ", "HtTp://127.0.0.1/"):
        assert not check_target(url).allowed, f"{url} 绕过了判定"


# ----------------------------------------------------------------------
# 重新定向：每一跳都要过同一条判据。
#
# 只检查**起始** URL 是这一族保护里最常见的漏洞：一个公开站点回一个
# ``302 Location: http://169.254.169.254/``，客户端跟过去，业务代码从头到尾
# 只见过那个公开域名。所以判据必须能被逐跳调用 —— 这条测的是它**能做到**
# 这件事，而不是它已经这么做了（后者在 ``test_acquisition_target_policy.py``）。
# ----------------------------------------------------------------------
def test_redirect_targets_are_judged_by_the_same_predicate() -> None:
    assert check_target("https://example.co.uk/moved").allowed
    assert not check_target("http://169.254.169.254/").allowed


def test_redirect_limit_is_finite() -> None:
    assert 0 < MAX_REDIRECTS <= 10, "无限重定向链是一台被拖住的 worker"


# ----------------------------------------------------------------------
# 判据的另一半：URL 里的名字是公开的，而它**实际通向哪里**。
#
# 只有 ``check_target`` 时，``http://a-name-that-resolves-to-169.254.169.254/``
# 会一路放行 —— 名字是公开的，而它解析出来的地址没人问过。这一半判的是 DNS
# 量回来的事实，所以它与上面那组共用同一条纪律：先说允许，再说拒绝。
#
# 用 ``93.184.216.34`` 而不是 ``192.0.2.x``：后者（TEST-NET-1）**不是**全局
# 可路由地址，拿它当正控会得到一个与被测行为无关的拒绝。
# ----------------------------------------------------------------------
_PUBLIC_ADDRESS = "93.184.216.34"


def test_a_name_resolving_only_to_public_addresses_is_allowed() -> None:
    """正控。少了它，一个「见名字就拒」的实现能通过下面全部用例。"""
    assert check_resolved("example.co.uk", (_PUBLIC_ADDRESS,)).allowed


def test_a_name_that_resolves_into_private_space_is_refused() -> None:
    """公开域名解析到内网地址（DNS 劫持 / rebinding 的典型形状）。"""
    verdict = check_resolved("example.co.uk", ("169.254.169.254",))

    assert not verdict.allowed
    assert verdict.refusal is TargetRefusal.RESOLVES_TO_PRIVATE
    assert "169.254.169.254" in verdict.detail, "报告里要写出**实测到**的那个地址"


def test_one_bad_address_among_good_ones_is_enough_to_refuse() -> None:
    """判据是「全部」而不是「至少一个」。

    两种顺序都要测：一个只看第一个地址的实现会在「坏的在后面」那一格放行，
    然后在轮到那个地址时把它连上 —— 而这一次与上一次的判决长得一模一样。
    这正是 DNS rebinding 的形状。
    """
    for addresses in (("169.254.169.254", _PUBLIC_ADDRESS), (_PUBLIC_ADDRESS, "10.0.0.7")):
        verdict = check_resolved("example.co.uk", addresses)
        assert not verdict.allowed, f"{addresses} 里有一个不是全局可路由地址，却放行了"
        assert verdict.refusal is TargetRefusal.RESOLVES_TO_PRIVATE


def test_the_resolved_refusal_is_a_different_sentence_than_the_literal_one() -> None:
    """「有人把内网地址写进了 URL」与「这个公开域名指向内网」是两句不同的话。

    后者要记下来（它是一个必须被看见的现象），所以两者不能共用一个理由码。
    """
    literal = check_target("http://169.254.169.254/")
    resolved = check_resolved("example.co.uk", ("169.254.169.254",))

    assert literal.refusal is TargetRefusal.PRIVATE_TARGET
    assert resolved.refusal is TargetRefusal.RESOLVES_TO_PRIVATE


def test_an_empty_address_list_is_a_programming_error_not_a_verdict() -> None:
    """空地址列表是**编程错误**，不是一次判决。

    它意味着调用方把一次解析失败当成了一次测量结果。让它在这里静默通过，
    等于留了一条「没量到就没问题」的路 —— 而那条路看起来与「解析成功」同形。
    """
    with pytest.raises(ValueError, match="空地址"):
        check_resolved("example.co.uk", ())


# ----------------------------------------------------------------------
# 绑定地址：MCP 服务面只允许 loopback（§6 + 本机安全约束）。
#
# 这条判据服务的是**服务端**，与上面「抓取目标」的判据方向相反：上面的
# ``127.0.0.1`` 是禁止访问的目标，这里的 ``127.0.0.1`` 是唯一允许的绑定地址。
# 两个方向共用一个模块，是因为它们是同一条纪律的两面：本机服务不出网、
# 不出网的服务不抓本机。
# ----------------------------------------------------------------------
_LOOPBACK: tuple[str, ...] = ("127.0.0.1", "127.0.0.5", "::1", "localhost", "[::1]")
_NOT_LOOPBACK: tuple[str, ...] = (
    "0.0.0.0",
    "::",
    "192.168.1.5",
    "10.0.0.1",
    "example.com",
    "169.254.169.254",
)


@pytest.mark.parametrize("host", _LOOPBACK)
def test_loopback_hosts_are_recognised(host: str) -> None:
    assert is_loopback_host(host), f"{host} 应被认作 loopback"


@pytest.mark.parametrize("host", _NOT_LOOPBACK)
def test_non_loopback_hosts_are_not(host: str) -> None:
    assert not is_loopback_host(host), f"{host} 不是 loopback，不得被当成 loopback"
