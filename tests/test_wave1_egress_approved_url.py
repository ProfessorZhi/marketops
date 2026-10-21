"""``approved_url`` 这一格：厂商 URL 参数与出口**共用同一个判据**。

## 这一格为什么值得单独一个文件

适配器（`research_adapter_tinyfish`）从一张**声明表**里取 URL 守卫，第一项写的是
``("research_egress", "approved_url")``。在本 lane 补上它之前，那个名字不存在，
于是每一次都**静默地**落到第二项 —— 另一份实现。两份实现里先被修好的那一份不会
告诉另一份，而分叉的那一份看起来完全正常。

本文件判四层：

1. **接缝**：第一来源解析得到，而且**就是**本模块这一个函数（不是回退）。
2. **判据**：厂商 URL 与出口用的是**同一个** ``EgressGate`` —— 字面量 / 范围 /
   端口 / 解析后地址四条规则逐条相同；负向矩阵逐格有具名理由码。
3. **顺序**：拒绝发生在**任何**网络动作之前（`tools/call` / HTTP / WebSocket /
   Chromium 一次都不发生）。
4. **牙齿**：把守卫换成「全都放行」，同一个 metadata URL 就通过了 —— 证明上面
   那一组红不是因为别的东西在挡。

## 靶子

解析器是可注入的（判据自带这个缝），所以「一个名字解析到内网」在**不发一个包**的
前提下可构造。唯一用到 socket 的那一条（重定向）只连本机诱饵。

没有真实厂商网络、账户、凭据或余额。
"""

from __future__ import annotations

import inspect
import socket
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
PLUGIN_SCRIPTS = REPO_ROOT / "plugins" / "research-tools" / "marketops-research-assistant" / "scripts"
if str(PLUGIN_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(PLUGIN_SCRIPTS))

entry = pytest.importorskip("research_egress")
safe = entry.safe

SAFE_HOST = "safe.example.co.uk"
PUBLIC_ADDRESS = "93.184.216.34"
LOOPBACK = "127.0.0.1"


class _Resolver(safe.HostResolver):
    """一个说死了的解析器，**数自己被问了几次**（「有没有缓存」就是这一格）。"""

    def __init__(self, answers: dict[str, tuple[str, ...]] | list[tuple[str, ...]]) -> None:
        self._answers = answers
        self.calls: list[str] = []

    def resolve(self, host: str, port: int) -> tuple[str, ...]:
        self.calls.append(host)
        if isinstance(self._answers, list):
            index = min(len(self.calls) - 1, len(self._answers) - 1)
            return self._answers[index]
        if host not in self._answers:
            raise safe.DnsResolutionError(
                host, safe.DnsFailureKind.NONEXISTENT, "审计替身"
            )
        return self._answers[host]


class _Gone(safe.HostResolver):
    def resolve(self, host: str, port: int) -> tuple[str, ...]:
        raise safe.DnsResolutionError(host, safe.DnsFailureKind.NONEXISTENT, "审计替身")


@pytest.fixture
def allowlisted(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(entry.ALLOWED_DOMAINS_ENV, SAFE_HOST)


# ======================================================================
# 1. 接缝：第一来源**就是**这一格
# ======================================================================
def test_the_first_declared_source_resolves_to_this_function() -> None:
    """适配器表的第一项解析得到，且对象**就是** ``research_egress.approved_url``。

    只判「解析得到」是不够的：一个回退到第二实现的表也能解析得到。判 ``is`` 才
    说明「单一守卫在 W-A」这句话有落点。
    """
    tinyfish = pytest.importorskip("research_adapter_tinyfish")
    module_name, attribute = tinyfish.URL_GUARD_SOURCES[0]
    assert (module_name, attribute) == ("research_egress", "approved_url")

    resolved = tinyfish._import_guard(module_name, attribute)  # noqa: SLF001
    assert resolved is entry.approved_url, (
        "第一来源解析到的不是 research_egress.approved_url —— 那说明它仍在校验回退"
    )


def test_a_slice_without_the_port_policy_denies_instead_of_guessing(
    allowlisted: None, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """**半截集成**：模块带 ``approved_url``、切片还是旧版（没有端口判据）。

    这时唯一正确的答案是**拒**。一个「没有端口判据就当作端口都行」的版本，会把
    一次集成失误变成一条常开的旁路；而一个在 import 期炸掉的版本会让适配器拿到
    ``AttributeError``（``_import_guard`` 只捕 ``ImportError``），于是调用方看到的
    是一次崩溃而不是一句理由码。两条都不行，所以这一格是必测的。
    """
    monkeypatch.setattr(entry, "_SLICE_HAS_PORT_POLICY", False)

    with pytest.raises(ValueError) as caught:
        entry.approved_url("https://" + SAFE_HOST + "/page", resolve=False)

    assert str(caught.value).split(":", 1)[0] == entry.OFFICIAL_TARGET_REFUSAL
    assert "port policy" in str(caught.value)


def test_a_table_with_no_resolvable_source_is_a_governed_denial(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """**守卫缺失** ⇒ 受治理地拒，而不是「没有守卫所以放行」。

    这一格挡的是最坏的一种回退：把「守卫 import 不到」读成「没有规则」。
    """
    tinyfish = pytest.importorskip("research_adapter_tinyfish")
    monkeypatch.setattr(tinyfish, "_import_guard", lambda *a, **k: None)

    with pytest.raises(ValueError) as caught:
        tinyfish.approved_url("https://" + SAFE_HOST + "/page")

    assert "EGRESS_REFUSED" in str(caught.value), caught.value


# ======================================================================
# 2. 负向矩阵：逐格有具名理由码
# ======================================================================
_NEGATIVE = (
    ("host_not_allowlisted", "https://unadvertised.example.net/page",
     entry.OFFICIAL_TARGET_REFUSAL),
    ("http_scheme", "http://" + SAFE_HOST + "/page", entry.OFFICIAL_TARGET_REFUSAL),
    ("userinfo", "https://user:pw@" + SAFE_HOST + "/page", entry.OFFICIAL_TARGET_REFUSAL),
    ("literal_ip", "https://93.184.216.34/page", entry.OFFICIAL_TARGET_REFUSAL),
    ("reserved_name", "https://localhost/page", entry.OFFICIAL_TARGET_REFUSAL),
    ("numeric_host", "https://2130706433/page", entry.OFFICIAL_TARGET_REFUSAL),
    ("lookalike", "https://evil-safe.example.co.uk/page", entry.OFFICIAL_TARGET_REFUSAL),
    ("suffix_disguise", "https://safe.example.co.uk.evil.test/page",
     entry.OFFICIAL_TARGET_REFUSAL),
    ("port_22", "https://" + SAFE_HOST + ":22/page", entry.OFFICIAL_TARGET_REFUSAL),
    ("port_6379", "https://" + SAFE_HOST + ":6379/page", entry.OFFICIAL_TARGET_REFUSAL),
    ("port_8080", "https://" + SAFE_HOST + ":8080/page", entry.OFFICIAL_TARGET_REFUSAL),
    ("port_8443", "https://" + SAFE_HOST + ":8443/page", entry.OFFICIAL_TARGET_REFUSAL),
    ("port_out_of_range", "https://" + SAFE_HOST + ":65536/page",
     entry.OFFICIAL_TARGET_REFUSAL),
    ("empty", "", entry.OFFICIAL_TARGET_REFUSAL),
    ("too_long", "https://" + SAFE_HOST + "/" + "x" * 2000, entry.OFFICIAL_TARGET_REFUSAL),
)


@pytest.mark.parametrize(
    ("name", "url", "expected"), _NEGATIVE, ids=[row[0] for row in _NEGATIVE]
)
def test_the_guard_refuses_the_shape_and_scope_matrix(
    name: str, url: str, expected: str, allowlisted: None
) -> None:
    """形状 / 范围 / 端口：每一格都拒，且理由码是**那一个**（不是碰巧的另一个）。"""
    with pytest.raises(ValueError) as caught:
        entry.approved_url(url, resolve=False)

    assert str(caught.value).split(":", 1)[0] == expected, (name, caught.value)


def test_an_empty_allowlist_denies_everything(monkeypatch: pytest.MonkeyPatch) -> None:
    """空名单 = 谁都不许（与出口那一层同一句话）。"""
    monkeypatch.setenv(entry.ALLOWED_DOMAINS_ENV, "")
    with pytest.raises(ValueError) as caught:
        entry.approved_url("https://" + SAFE_HOST + "/page", resolve=False)
    assert str(caught.value).split(":", 1)[0] == entry.OFFICIAL_TARGET_REFUSAL


@pytest.mark.parametrize(
    ("address", "why"),
    [
        ("127.0.0.1", "环回"),
        ("::1", "IPv6 环回"),
        ("10.1.2.3", "私网"),
        ("169.254.169.254", "链路本地 / 云 metadata"),
        ("100.64.0.1", "CGNAT"),
        ("::ffff:127.0.0.1", "IPv4-mapped 环回"),
    ],
)
def test_a_name_that_resolves_inward_is_refused(
    address: str, why: str, allowlisted: None
) -> None:
    """名字被批准、解析到内网 ⇒ ``TARGET_RESOLVES_NON_GLOBAL``。"""
    resolver = _Resolver({SAFE_HOST: (address,)})

    with pytest.raises(ValueError) as caught:
        entry.approved_url("https://" + SAFE_HOST + "/page", resolver=resolver)

    assert str(caught.value).split(":", 1)[0] == entry.TARGET_RESOLVES_NON_GLOBAL, why
    assert resolver.calls == [SAFE_HOST], "这一格必须真的问过一次解析"


def test_a_name_that_does_not_resolve_is_a_different_reason(allowlisted: None) -> None:
    """解析不出来 ≠ 目标不被允许：一个是查拼写，一个是去要批准。"""
    with pytest.raises(ValueError) as caught:
        entry.approved_url("https://" + SAFE_HOST + "/page", resolver=_Gone())
    assert str(caught.value).split(":", 1)[0] == entry.TARGET_RESOLUTION_FAILED


# ======================================================================
# 3. 顺序：拒绝发生在任何网络动作之前
# ======================================================================
class _Sentinels:
    def __init__(self) -> None:
        self.events: list[str] = []

    def note(self, name: str) -> None:
        self.events.append(name)


@pytest.fixture
def side_effects(monkeypatch: pytest.MonkeyPatch) -> _Sentinels:
    """把**四条**出网面都换成记录器：HTTP、官方 MCP、辅助 HTTP、浏览器出口。"""
    seen = _Sentinels()

    def _hit(name: str):
        def _fn(*args, **kwargs):
            seen.note(name)
            return None

        return _fn

    for module_name, attribute, label in (
        ("research_provider_extensions", "_http_json", "http"),
        ("research_official_mcp", "_live_call", "official_mcp"),
        ("research_provider_extensions", "_crawl4ai_local", "chromium"),
    ):
        module = pytest.importorskip(module_name)
        if hasattr(module, attribute):
            monkeypatch.setattr(module, attribute, _hit(label))

    monkeypatch.setattr(safe.GuardedHttpClient, "get", _hit("auxiliary_http"))
    monkeypatch.setattr(safe.EgressProxy, "start", _hit("egress_proxy"))
    return seen


@pytest.mark.parametrize(
    "url",
    [
        "https://" + SAFE_HOST + ":6379/page",   # 不安全端口
        "https://unadvertised.example.net/p",    # 名单外
        "https://user:pw@" + SAFE_HOST + "/p",   # userinfo
    ],
)
def test_a_denial_touches_no_http_websocket_or_browser(
    url: str, allowlisted: None, side_effects: _Sentinels
) -> None:
    """判定被拒 ⇒ 四条出网面（HTTP / 官方 MCP / 辅助 HTTP / 浏览器出口）零触发。

    WebSocket 没有单独的工具：它不是这一层的入口，而这条判据判的是「这一层根本不
    发字节」—— 一个不发起 HTTP 的守卫也不会去开 ws。
    """
    with pytest.raises(ValueError):
        entry.approved_url(url, resolver=_Resolver({SAFE_HOST: (PUBLIC_ADDRESS,)}))

    assert side_effects.events == [], (
        f"被拒的判定之后仍然碰到了出网面：{side_effects.events}"
    )


def test_a_safe_url_does_not_open_any_socket(
    allowlisted: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """**正向对照**：合法地址通过，而且全过程**一个 socket 都没建**。

    判据只回答「允不允许」；连接是调用方的事。把这一格与上一条放在一起，
    「拒绝时没有出网」才不是一句空话。
    """
    def _boom(*args, **kwargs):  # pragma: no cover - 走到这里就是缺陷
        raise AssertionError("判据自己去连网了")

    monkeypatch.setattr(socket, "getaddrinfo", _boom)
    monkeypatch.setattr(socket, "create_connection", _boom)

    resolver = _Resolver({SAFE_HOST: (PUBLIC_ADDRESS,)})
    result = entry.approved_url(
        "https://" + SAFE_HOST + "/page?x=1", resolver=resolver
    )

    assert result == "https://" + SAFE_HOST + "/page?x=1"
    assert resolver.calls == [SAFE_HOST]


# ======================================================================
# 4. 陈旧 DNS / 每次重判
# ======================================================================
def test_a_changed_answer_is_rejudged_on_every_call(allowlisted: None) -> None:
    """第一次解析到公网、第二次解析到内网 ⇒ 第二次必须被拒。

    一个**缓存了判定结果**的守卫会在第二次放行 —— 而那正是 DNS rebinding 的形状：
    名字在两次判定之间换了答案。这一条要求解析器被问**两次**。
    """
    resolver = _Resolver([(PUBLIC_ADDRESS,), ("169.254.169.254",)])

    assert entry.approved_url("https://" + SAFE_HOST + "/page", resolver=resolver)
    with pytest.raises(ValueError) as caught:
        entry.approved_url("https://" + SAFE_HOST + "/page", resolver=resolver)

    assert str(caught.value).split(":", 1)[0] == entry.TARGET_RESOLVES_NON_GLOBAL
    assert resolver.calls == [SAFE_HOST, SAFE_HOST], "判定被缓存了 —— 第二次没有重判"


# ======================================================================
# 5. 重定向到 metadata：两半都要成立
# ======================================================================
def test_a_redirect_target_at_metadata_is_refused_by_the_same_gate(
    allowlisted: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """重定向的**下一跳**是 metadata 时，判据（与出口同一个）必须拒它。

    这一条判的是「下一跳也是一个 URL，也要过同一个判据」；「不跟随」那一半由
    ``test_wave1_egress_hardening.py`` 的辅助链判据覆盖（那里断言
    ``redirect_not_followed`` 且没有第二跳连接）。
    """
    gate = safe.EgressGate(
        _Resolver({SAFE_HOST: (PUBLIC_ADDRESS,)}),
        allowlist=frozenset({SAFE_HOST}),
        allowed_ports=safe.DEFAULT_EGRESS_PORTS,
    )

    verdict = gate.check("169.254.169.254", 443)

    assert not verdict.allowed
    assert verdict.refusal == "private_target", verdict.refusal

    with pytest.raises(ValueError):
        entry.approved_url("https://169.254.169.254/latest/meta-data/", resolve=False)


# ======================================================================
# 6. 未公布的 tools/list 端点
# ======================================================================
def test_an_unadvertised_tools_list_endpoint_is_refused(allowlisted: None) -> None:
    """不在批准名单上的端点：既过不了这一格，也过不了 bootstrap 那一格。"""
    with pytest.raises(ValueError):
        entry.approved_url("https://unadvertised.example.net/mcp", resolve=False)

    gate = pytest.importorskip("research_s0_preflight_gate")
    with pytest.raises(gate.BootstrapRefused) as caught:
        gate.bootstrap_probe_schema(
            probe=lambda **_: {"tools": []},
            provider="serper",
            endpoint="https://unadvertised.example.net/mcp",
            allowlist=[SAFE_HOST],
            egress_decision=lambda url: "egress:audit",
            audit=lambda record: None,
            registry_schema_sha256="a" * 64,
        )
    assert "BOOTSTRAP_ENDPOINT_NOT_ALLOWED" in str(caught.value)


# ======================================================================
# 7. 牙齿：把守卫换成「全都放行」，同一行就通过了
# ======================================================================
def test_removing_the_guard_lets_the_matrix_through(
    allowlisted: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """**intentional guard removal**：换成 ``lambda v, **k: v`` 之后，metadata 通过了。

    这一条证明上面那一组的红是**这道守卫**造成的，而不是因为适配器里还有第二个
    检查。它同时是一条警告：这一层是唯一挡在那里的一层。
    """
    tinyfish = pytest.importorskip("research_adapter_tinyfish")
    monkeypatch.setattr(entry, "approved_url", lambda value, **kwargs: value)

    target = "https://169.254.169.254/latest/meta-data/"
    assert tinyfish.approved_url(target) == target, (
        "守卫被摘掉之后适配器仍然拒绝了这个地址 —— 说明还有另一道检查，"
        "那么「守卫是承重的」这句话要重新写"
    )


# ======================================================================
# 8. 与被替换的那一份对拍（形状层）
# ======================================================================
_SHAPE_CORPUS = (
    "https://" + SAFE_HOST + "/a",
    "https://" + SAFE_HOST + "/deep/path?x=1",
    "https://" + SAFE_HOST.upper() + "/a",
    "https://" + SAFE_HOST + "./a",
    "http://" + SAFE_HOST + "/a",
    "https://" + SAFE_HOST + ":8443/a",
    "https://u:p@" + SAFE_HOST + "/a",
    "https://127.0.0.1/a",
    "https://localhost/a",
    "https://evil-" + SAFE_HOST + "/a",
    "https://unadvertised.example.net/a",
    "ftp://" + SAFE_HOST + "/a",
    "",
)


@pytest.mark.parametrize("value", _SHAPE_CORPUS, ids=[v[:34] for v in _SHAPE_CORPUS])
def test_the_shape_verdict_matches_the_replaced_implementation(
    value: str, allowlisted: None
) -> None:
    """同一批输入，两份实现给出**同一个判决与同一个理由码**。

    这是「切过来不是一次行为变更」的证据，也是「回退不是一条自由旁路」的证据：
    即使某天主来源丢了，回退那一份的判定与这一份逐条相同。
    """
    incumbent = pytest.importorskip("research_official_mcp")
    if "resolve" not in inspect.signature(incumbent._approved_url).parameters:  # noqa: SLF001
        pytest.skip("这一份 _approved_url 没有 resolve= —— 对拍要问真 DNS")

    def _outcome(callable_) -> tuple[bool, str]:
        try:
            callable_(value, resolve=False)
        except ValueError as exc:
            return False, str(exc).split(":", 1)[0]
        return True, ""

    assert _outcome(entry.approved_url) == _outcome(incumbent._approved_url)  # noqa: SLF001
