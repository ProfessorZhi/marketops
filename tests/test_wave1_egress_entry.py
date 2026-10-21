"""受管出口的**入口**（``scripts/research_egress.py``）+ **发行切片**。

契约位置：``WORKER-DAG-AND-OWNERSHIP.md`` §4 的 W-A 完成判据（P1-01 对抗矩阵的
8 条）与 ``INTERFACE-CONTRACT.md`` 的 ``IA-4``。

## 为什么这一份要对着**发行的那一份**跑

``tests/test_wave1_egress_guarded_http.py`` 判的是核心模块的行为；本文件把同一批
对抗判据指向 ``plugins/.../scripts/marketops_safe_egress``（客户机上真正会跑的那一份），
并且只在 ``scripts/``（切片 + 入口）与标准库上跑 —— 一个在核心树上绿、在发行包上
不存在的判据，对一个客户机上的包什么也没说。

## 靶子

本机诱饵（``127.0.0.1``），与核心那一份同一条理由：§7 末句禁止把真实内网或云
metadata 当靶子。诱饵自己带一个 ``accepts`` 计数器 —— 「回执写着拒绝」与「那一个
请求没有离开进程」是两件事，而后者才是这条链存在的理由。
"""

from __future__ import annotations

import contextlib
import io
import os
import socket
import sys
import threading
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
PLUGIN_ROOT = REPO_ROOT / "plugins" / "research-tools" / "marketops-research-assistant"
SCRIPTS_ROOT = PLUGIN_ROOT / "scripts"
SLICE_ROOT = SCRIPTS_ROOT / "marketops_safe_egress"

sys.path.insert(0, str(SCRIPTS_ROOT))

import marketops_safe_egress as safe  # noqa: E402
import research_egress as entry  # noqa: E402

DECOY_HOST = "page.example.co.uk"
LOOPBACK = "127.0.0.1"
PUBLIC = "93.184.216.34"


# ----------------------------------------------------------------------
# 诱饵与替身
# ----------------------------------------------------------------------
class _Origin:
    """一个只在测试生命周期内存在的本机 origin，带 ``accepts`` 计数器。"""

    def __init__(self, responder: Callable[[bytes, "_Origin"], bytes] | None = None) -> None:
        self._responder = responder or _ok_responder
        self._socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._socket.bind((LOOPBACK, 0))
        self._socket.listen(16)
        self._socket.settimeout(0.5)
        self._stop = threading.Event()
        self.accepts = 0
        self.headers: list[bytes] = []
        self.port: int = self._socket.getsockname()[1]
        self._thread = threading.Thread(target=self._serve, daemon=True)
        self._thread.start()

    @property
    def address(self) -> str:
        return f"{LOOPBACK}:{self.port}"

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
                head = conn.recv(65536)
                self.headers.append(head)
                conn.sendall(self._responder(head, self))
            except OSError:
                pass
            finally:
                conn.close()

    def close(self) -> None:
        self._stop.set()
        try:
            self._socket.close()
        except OSError:  # pragma: no cover
            pass
        self._thread.join(timeout=2)


def _ok_responder(_head: bytes, _origin: _Origin) -> bytes:
    return (
        b"HTTP/1.1 200 OK\r\n"
        b"Content-Type: text/plain; charset=utf-8\r\n"
        b"Content-Length: 2\r\n"
        b"Connection: close\r\n\r\nok"
    )


def _redirecting(_head: bytes, origin: _Origin) -> bytes:
    return (
        b"HTTP/1.1 302 Found\r\n"
        + f"Location: http://{origin.address}/private\r\n".encode()
        + b"Content-Length: 0\r\nConnection: close\r\n\r\n"
    )


class _FixedResolver(safe.HostResolver):
    def __init__(self, mapping: dict[str, tuple[str, ...]]) -> None:
        self._mapping = mapping
        self.calls: list[str] = []

    def resolve(self, host: str, port: int) -> tuple[str, ...]:
        self.calls.append(host)
        if host not in self._mapping:
            raise safe.DnsResolutionError(host, safe.DnsFailureKind.NONEXISTENT, "替身")
        return self._mapping[host]


class _DecoyGate(safe.EgressGate):
    """只对诱饵那一个 ``(host, port)`` 点头，其余一切走真判据。"""

    def __init__(self, resolver: safe.HostResolver, allowlist: frozenset[str], decoy: _Origin):
        super().__init__(resolver, allowlist=allowlist)
        self._decoy_port = decoy.port

    def check(self, host: str, port: int) -> safe.EgressVerdict:
        if host == DECOY_HOST and port == self._decoy_port:
            return safe.EgressVerdict(allowed=True, address=LOOPBACK)
        return super().check(host, port)


@pytest.fixture
def served() -> Iterator[Callable[..., _Origin]]:
    servers: list[_Origin] = []

    def factory(responder: Callable[[bytes, _Origin], bytes] | None = None) -> _Origin:
        server = _Origin(responder)
        servers.append(server)
        return server

    yield factory
    for server in servers:
        server.close()


# ======================================================================
# 1. 发行切片
# ======================================================================
class TestShippedSlice:
    def test_the_slice_declares_its_schema_and_carries_provenance(self) -> None:
        provenance = safe.slice_provenance()
        assert safe.SLICE_SCHEMA == "marketops.safe-egress-slice.v1"
        assert provenance["schema"] == safe.SLICE_SCHEMA
        assert provenance["rule"] == "verbatim"
        assert len(provenance["files"]) == 6
        for row in provenance["files"]:
            assert len(row["sha256"]) == 64, row
            assert row["canonical"].startswith("marketops/"), row

    def test_the_imported_policy_is_the_shipped_copy(self) -> None:
        """import 到的每一个切片模块，文件都落在**发行的那一个目录**里。

        这一条判「有没有别的来源」：一个把切片换成 ``import marketops`` 的改动会让这里
        红 —— 而不是在客户机上以 ``ImportError`` 的样子红。

        「核心包没有被 import」那一句由 ``tests/test_wave1_egress_bundle.py`` 在一个
        ``-I -S`` 的子进程里判：那是一句关于**进程**的话，把它写在同一个 pytest 进程里
        会判到邻居的状态上。
        """
        slice_dir = SLICE_ROOT.resolve()
        assert str(safe.__file__).startswith(str(slice_dir))
        loaded = sorted(n for n in sys.modules if n.startswith("marketops_safe_egress"))
        assert loaded, "一个切片模块都没加载 —— 这一条判不到任何东西"
        for name in loaded:
            path = getattr(sys.modules[name], "__file__", None)
            assert path is not None, f"{name} 没有 __file__"
            assert str(Path(path).resolve()).startswith(str(slice_dir)), (
                f"{name} 从 {path} 加载 —— 那不是发行的那一份"
            )

    def test_the_facade_exports_resolve(self) -> None:
        assert len(safe.__all__) == len(set(safe.__all__))
        assert [n for n in safe.__all__ if not hasattr(safe, n)] == []


# ======================================================================
# 2. 辅助 HTTP 的对抗判据，对着**发行的那一份**
# ======================================================================
class TestShippedAuxiliaryEgress:
    def test_a_name_that_resolves_inward_is_refused_before_connecting(
        self, served: Callable[..., _Origin]
    ) -> None:
        decoy = served()
        with entry.open_session(
            allowlist=frozenset({DECOY_HOST}),
            resolver=_FixedResolver({DECOY_HOST: (LOOPBACK,)}),
            allowed_ports=frozenset({decoy.port}),
        ) as session:
            fetched = session.fetch_auxiliary(f"http://{DECOY_HOST}:{decoy.port}/robots.txt", depth=0)

        assert not fetched.allowed
        assert fetched.host_refusal is safe.TargetRefusal.RESOLVES_TO_PRIVATE
        assert fetched.response is None
        assert decoy.accepts == 0, "拒绝了，而请求已经发出去了"

    def test_a_redirect_is_not_followed_and_the_next_hop_never_connects(
        self, served: Callable[..., _Origin]
    ) -> None:
        decoy = served(_redirecting)
        resolver = _FixedResolver({DECOY_HOST: (LOOPBACK,)})
        with entry.open_session(
            allowlist=frozenset({DECOY_HOST}),
            resolver=resolver,
            gate=_DecoyGate(resolver, frozenset({DECOY_HOST}), decoy),
            allowed_ports=frozenset({decoy.port}),
        ) as session:
            fetched = session.fetch_auxiliary(f"http://{DECOY_HOST}:{decoy.port}/robots.txt")

        assert fetched.egress_refusal is safe.EgressRefusal.REDIRECT_NOT_FOLLOWED
        assert fetched.redirect_to == f"http://{decoy.address}/private"
        assert decoy.accepts == 1, "重定向的第 2 跳连上去了"

    def test_proxy_environment_variables_do_not_create_a_second_route(
        self, served: Callable[..., _Origin], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        proxy_decoy = served()
        target = served()
        for name in ("HTTP_PROXY", "http_proxy", "HTTPS_PROXY", "https_proxy", "ALL_PROXY"):
            monkeypatch.setenv(name, f"http://{proxy_decoy.address}")
        resolver = _FixedResolver({DECOY_HOST: (LOOPBACK,)})
        with entry.open_session(
            allowlist=frozenset({DECOY_HOST}),
            resolver=resolver,
            gate=_DecoyGate(resolver, frozenset({DECOY_HOST}), target),
            allowed_ports=frozenset({proxy_decoy.port, target.port}),
        ) as session:
            fetched = session.fetch_auxiliary(f"http://{DECOY_HOST}:{target.port}/robots.txt")

        assert fetched.allowed, fetched.detail
        assert proxy_decoy.accepts == 0, "流量被环境变量里那个代理接走了"
        assert target.accepts == 1

    def test_the_request_budget_stops_before_connecting(
        self, served: Callable[..., _Origin]
    ) -> None:
        decoy = served()
        resolver = _FixedResolver({DECOY_HOST: (LOOPBACK,)})
        limits = safe.EgressLimits(max_requests=1)
        with entry.open_session(
            allowlist=frozenset({DECOY_HOST}),
            resolver=resolver,
            gate=_DecoyGate(resolver, frozenset({DECOY_HOST}), decoy),
            limits=limits,
            allowed_ports=frozenset({decoy.port}),
        ) as session:
            first = session.fetch_auxiliary(f"http://{DECOY_HOST}:{decoy.port}/a")
            second = session.fetch_auxiliary(f"http://{DECOY_HOST}:{decoy.port}/b")

        assert first.allowed, first.detail
        assert second.egress_refusal is safe.EgressRefusal.BUDGET_REQUESTS
        assert decoy.accepts == 1
        assert entry.reason_code(second) == "EGRESS_BUDGET_EXCEEDED"

    def test_a_refused_fetch_carries_no_body_to_store(self) -> None:
        """P1-01 第 7 条的后半句：**一份被拒的取回没有正文可存**。

        「拒绝的结果不得被存成已验证的业务证据」在实现上只有一个落点：被拒时没有正文。
        其余部分（不落盘、不当证据）由调用方守 —— 而调用方拿不到一份「看起来来路清白」
        的材料，是这一条能成立的前提。
        """
        with entry.open_session(
            allowlist=frozenset({DECOY_HOST}),
            resolver=_FixedResolver({DECOY_HOST: ("169.254.169.254",)}),
        ) as session:
            fetched = session.fetch_auxiliary(f"http://{DECOY_HOST}/")

        assert fetched.response is None
        with pytest.raises(ValueError):
            safe.GuardedFetch(
                allowed=False,
                url=f"http://{DECOY_HOST}/",
                host_refusal=safe.TargetRefusal.PRIVATE_TARGET,
                detail="拒绝，但带了一份正文",
                response=safe.GuardedResponse(
                    url="http://x/", status=200, body=b"x", content_type="", hops=()
                ),
            )


# ======================================================================
# 3. 入口：失败关闭
# ======================================================================
class TestFailClosedEntry:
    def test_an_empty_allowlist_stops_before_anything_else(self) -> None:
        with pytest.raises(entry.EgressDenied) as caught:
            with entry.safe_browser_egress(allowlist=frozenset()):
                pytest.fail("空名单不该开出一个会话")
        assert caught.value.code == "LOCAL_CRAWL_NOT_SAFE_TO_RUN"
        assert "DOMAIN_ALLOWLIST_EMPTY" in caught.value.detail

    def test_open_session_refuses_an_empty_allowlist_outright(self) -> None:
        """``open_session`` 与浏览器那一支同一条纪律：空名单就是谁都不许。"""
        with pytest.raises(entry.EgressDenied) as caught:
            with entry.open_session(allowlist=frozenset()):
                pytest.fail("空名单不该开出一个会话")
        assert caught.value.code == "DOMAIN_ALLOWLIST_EMPTY"

    def test_a_missing_crawl4ai_stops_rather_than_falling_back(self) -> None:
        """本机（以及任何没装 crawl4ai 的客户机）走的就是这一支。"""
        if entry.crawl4ai_ready():  # pragma: no cover - 取决于本机是否装了
            pytest.skip("本机装了 crawl4ai —— 这一条判的是没装的那一支")
        with pytest.raises(entry.EgressDenied) as caught:
            with entry.safe_browser_egress(allowlist=frozenset({"example.com"})):
                pytest.fail("crawl4ai 不可用时不该开出一个会话")
        assert caught.value.code == "LOCAL_CRAWL_NOT_SAFE_TO_RUN"
        assert "CRAWL4AI_UNAVAILABLE" in caught.value.detail

    def test_the_readiness_query_answers_with_a_closed_cause(self) -> None:
        ready, cause = entry.local_crawl_ready(frozenset())
        assert not ready
        assert cause == "DOMAIN_ALLOWLIST_EMPTY"
        assert cause in entry.READINESS_CAUSES
        _ready, second = entry.local_crawl_ready(frozenset({"example.com"}))
        assert second in entry.READINESS_CAUSES

    def test_the_probe_binary_reports_a_cause_and_a_nonzero_exit(self) -> None:
        # 探针把成因码打在 stdout 上，那正是它作为 CLI 的用处 —— 这里把它收掉，
        # 免得一条绿判据在套件输出里留下两行像是失败的东西。
        with contextlib.redirect_stdout(io.StringIO()) as captured:
            empty = entry.main(["--domains", ""])
            malformed = entry.main(["--domains", "not a domain"])
        assert empty == 1
        assert malformed == 1, "坏条目必须当场停，而不是被当成一条覆盖"
        assert "DOMAIN_ALLOWLIST_EMPTY" in captured.getvalue()
        assert "DOMAIN_ALLOWLIST_MALFORMED" in captured.getvalue()

    def test_there_is_no_unsafe_mode_switch(self) -> None:
        """**今天没有「不安全模式」这个参数，将来也不加。**

        这一条判的是**形状**：一个叫 ``allow_unmanaged`` / ``unsafe`` 之类的开关会在
        某一天被打开，而那一次打开会让整条链消失。判据是「构造签名里没有这样一格」，
        所以它比注释可靠。
        """
        import inspect

        for function in (entry.safe_browser_egress, entry.open_session):
            parameters = set(inspect.signature(function).parameters)
            assert not parameters & {
                "allow_unmanaged",
                "unsafe",
                "insecure",
                "disable_egress",
            }, f"{function.__name__} 上出现了降级开关：{parameters}"

    def test_the_entry_never_builds_a_gate_without_an_allowlist(self) -> None:
        """**范围闸永远装着。** ``EgressGate(allowlist=None)`` 是「这一层没有范围闸」
        那一支，而入口这一层没有那一档。

        判据是运行期的：把一个有记录的 ``EgressGate`` 替身换进去，走一次
        ``open_session``，看它收到了什么。这一条挡的是「有人为了让某个用例过，把
        ``allowlist`` 那一格删了」—— 那会让整条范围闸静默消失。
        """
        seen: list[object] = []
        original = entry.safe.EgressGate

        class _Recorder(original):  # type: ignore[misc, valid-type]
            def __init__(self, resolver=None, *, allowlist=None, **kwargs):
                seen.append(allowlist)
                super().__init__(resolver, allowlist=allowlist, **kwargs)

        entry.safe.EgressGate = _Recorder
        try:
            with entry.open_session(
                allowlist=frozenset({DECOY_HOST}),
                resolver=_FixedResolver({DECOY_HOST: (PUBLIC,)}),
                start_proxy=False,
            ):
                pass
        finally:
            entry.safe.EgressGate = original

        assert seen, "一次都没构造判据 —— 这一条判不到任何东西"
        assert all(entry is not None for entry in seen), (
            f"入口构造了一个不带范围闸的判据：{seen} —— 范围那一道会静默消失"
        )


# ======================================================================
# 4. 会话：出口、范围、深度
# ======================================================================
class TestSession:
    def test_browser_config_kwargs_refuses_before_the_proxy_is_listening(self) -> None:
        session = entry.SafeEgressSession(
            proxy=safe.EgressProxy(),
            gate=safe.EgressGate(),
            allowlist=frozenset({DECOY_HOST}),
            limits=safe.DEFAULT_LIMITS,
            scope=entry.CrawlScope(),
        )
        with pytest.raises(RuntimeError):
            session.browser_config_kwargs()

    def test_browser_config_kwargs_carries_the_proxy_and_both_defences(self) -> None:
        gate = safe.EgressGate(allowlist=frozenset({DECOY_HOST}))
        proxy = safe.EgressProxy(gate=gate)
        proxy.start()
        try:
            session = entry.SafeEgressSession(
                proxy=proxy,
                gate=gate,
                allowlist=frozenset({DECOY_HOST}),
                limits=safe.DEFAULT_LIMITS,
                scope=entry.CrawlScope(),
            )
            kwargs = session.browser_config_kwargs()
            # 地址要在 stop() **之前**读：`url` 只在监听期间存在，而那一格正是
            # 「配置指向一个没人听的端口」这件事唯一的防线。
            listening_url = proxy.url
            egress_ref = session.browser_egress_ref()
        finally:
            proxy.stop()

        assert kwargs["proxy_config"] == {"server": listening_url}
        assert kwargs["extra_args"] == list(safe.BROWSER_EGRESS_ARGUMENTS)
        assert listening_url.startswith("http://127.0.0.1:")
        # 只交出去那两格：headless / user_agent 与出口无关，不该被这一层定死。
        assert set(kwargs) == {"proxy_config", "extra_args"}
        assert egress_ref.startswith("egress:")

    def test_the_proxy_may_only_listen_on_loopback(self) -> None:
        with pytest.raises(ValueError):
            safe.EgressProxy(host="0.0.0.0")

    def test_depth_is_capped_before_anything_is_fetched(self) -> None:
        with pytest.raises(entry.EgressDenied) as caught:
            with entry.open_session(
                allowlist=frozenset({DECOY_HOST}),
                resolver=_FixedResolver({DECOY_HOST: (PUBLIC,)}),
                scope=entry.CrawlScope(max_depth=1),
                start_proxy=False,
            ) as session:
                session.claim_page(f"http://{DECOY_HOST}/deep", depth=2)
        assert caught.value.code == "DEPTH_EXCEEDED"

    def test_pages_are_deduplicated_before_the_page_budget(self) -> None:
        """同一页被 sitemap 链接二十遍，不该吃掉二十个名额。"""
        with entry.open_session(
            allowlist=frozenset({DECOY_HOST}),
            resolver=_FixedResolver({DECOY_HOST: (PUBLIC,)}),
            scope=entry.CrawlScope(max_pages=2),
            start_proxy=False,
        ) as session:
            for _ in range(5):
                session.claim_page(f"http://{DECOY_HOST}/same", depth=0)
            session.claim_page(f"http://{DECOY_HOST}/other", depth=0)
            assert len(session.pages) == 2
            with pytest.raises(entry.EgressDenied) as caught:
                session.claim_page(f"http://{DECOY_HOST}/third", depth=0)
        assert caught.value.code == "PAGE_BUDGET_EXCEEDED"

    def test_a_page_outside_the_allowlist_cannot_even_be_queued(self) -> None:
        """深爬**排不进**范围外的 URL —— 与在出口处拒掉它相比，少一次网络动作。"""
        with entry.open_session(
            allowlist=frozenset({DECOY_HOST}),
            resolver=_FixedResolver({DECOY_HOST: (PUBLIC,)}),
            start_proxy=False,
        ) as session:
            with pytest.raises(entry.EgressDenied) as caught:
                session.claim_page("http://elsewhere.example.net/", depth=0)
            pages = session.pages
            refusals = session.refusals
        assert caught.value.code == "DOMAIN_NOT_APPROVED"
        assert pages == ()
        assert refusals == ("DOMAIN_NOT_APPROVED:http://elsewhere.example.net/",)

    def test_an_unsafe_target_is_refused_and_recorded(self) -> None:
        with entry.open_session(
            allowlist=frozenset({DECOY_HOST}),
            resolver=_FixedResolver({DECOY_HOST: (PUBLIC,)}),
            start_proxy=False,
        ) as session:
            with pytest.raises(entry.EgressDenied) as caught:
                session.claim_page("http://169.254.169.254/latest/meta-data/", depth=0)
            refusals = session.refusals
        assert caught.value.code == "TARGET_REFUSED"
        assert refusals == ("TARGET_REFUSED:http://169.254.169.254/latest/meta-data/",)


# ======================================================================
# 5. 理由码：两个闭集 + 一处折叠
# ======================================================================
class TestReasonCodes:
    def test_an_unknown_code_cannot_be_raised(self) -> None:
        with pytest.raises(ValueError):
            entry.EgressDenied("SOMETHING_NEW", "新类别没有先加进 REASONS")

    def test_every_readiness_cause_is_in_its_own_closed_set(self) -> None:
        assert entry.READINESS_CAUSES == {
            "READY",
            "DOMAIN_ALLOWLIST_EMPTY",
            "CRAWL4AI_UNAVAILABLE",
            "EGRESS_PROXY_UNAVAILABLE",
        }
        # 两个集合是两句话：一个答「现在能不能开工」，一个答「这一次为什么没做成」。
        assert not entry.REASONS & {"READY", "CRAWL4AI_UNAVAILABLE"}

    def test_each_refusal_field_folds_to_its_own_code(self) -> None:
        cases = [
            (safe.GuardedFetch(allowed=False, url="u",
                               host_refusal=safe.TargetRefusal.PRIVATE_TARGET,
                               detail="x"), "TARGET_REFUSED"),
            (safe.GuardedFetch(allowed=False, url="u",
                               scope_refusal=safe.ScopeRefusal.NOT_APPROVED,
                               detail="x"), "DOMAIN_NOT_APPROVED"),
            (safe.GuardedFetch(allowed=False, url="u",
                               scope_refusal=safe.ScopeRefusal.EMPTY_ALLOWLIST,
                               detail="x"), "DOMAIN_ALLOWLIST_EMPTY"),
            (safe.GuardedFetch(allowed=False, url="u",
                               dns_failure=safe.DnsFailureKind.TIMEOUT,
                               detail="x"), "DNS_RESOLUTION_FAILED"),
            (safe.GuardedFetch(allowed=False, url="u",
                               egress_refusal=safe.EgressRefusal.BUDGET_REQUESTS,
                               detail="x"), "EGRESS_BUDGET_EXCEEDED"),
            (safe.GuardedFetch(allowed=False, url="u",
                               egress_refusal=safe.EgressRefusal.REDIRECT_NOT_FOLLOWED,
                               detail="x"), "REDIRECT_NOT_FOLLOWED"),
            (safe.GuardedFetch(allowed=False, url="u",
                               egress_refusal=safe.EgressRefusal.RESPONSE_TOO_LARGE,
                               detail="x"), "RESPONSE_TOO_LARGE"),
            (safe.GuardedFetch(allowed=False, url="u",
                               egress_refusal=safe.EgressRefusal.TRANSPORT,
                               detail="x"), "TRANSPORT_FAILED"),
            (safe.GuardedFetch(allowed=False, url="u",
                               egress_refusal=safe.EgressRefusal.EGRESS_UNAVAILABLE,
                               detail="x"), "EGRESS_PROXY_UNAVAILABLE"),
            (safe.GuardedFetch(allowed=False, url="u",
                               egress_refusal=safe.EgressRefusal.PROXY_REFUSED,
                               detail="x"), "PROXY_REFUSED"),
        ]
        for fetch, expected in cases:
            code = entry.reason_code(fetch)
            assert code == expected, fetch
            assert code in entry.REASONS

    def test_every_local_reason_folds_onto_a_contract_reason(self) -> None:
        """每个本地理由码都能折叠到契约 §5.3 的那一格。

        §5.3 写着「新增理由码必须走契约变更流程」「禁止回落到自由文本」，所以这一层
        不新增契约码 —— 它只把比自己细的那几个本地码**多对一**折过去。折叠表与闭集
        不一致（多一个、少一个）会让 ``PolicyDecision.reason_code`` 在集成时第一次
        取到一个契约里没有的值。
        """
        assert entry.CONTRACT_EGRESS_REASONS <= {
            "EGRESS_REFUSED",
            "POLICY_DOMAIN_NOT_ALLOWED",
            "CRAWL4AI_LOCAL_NOT_INSTALLED",
        }
        assert set(entry.CONTRACT_FOLD) == set(entry.REASONS)
        assert set(entry.CONTRACT_FOLD.values()) == set(entry.CONTRACT_EGRESS_REASONS)
        for code in entry.REASONS:
            assert entry.contract_reason_code(code) in entry.CONTRACT_EGRESS_REASONS
        assert entry.contract_reason_code("ok") == "ok"
        with pytest.raises(ValueError):
            entry.contract_reason_code("NOT_A_LOCAL_CODE")

    def test_the_gate_readiness_probe_uses_the_scope_gate_too(self) -> None:
        """就绪探针也装着范围闸：不带名单的探针会证明一件与真正要用的东西不同的事。"""
        assert entry._probe_egress(frozenset({"example.com"})) in {
            "READY",
            "EGRESS_PROXY_UNAVAILABLE",
        }


# ======================================================================
# 6. 允许名单的解析（环境变量那一格）
# ======================================================================
class TestAllowlistParsing:
    @pytest.mark.parametrize(
        "bad",
        ["*.example.com", "http://example.com", "example.com:443", "93.184.216.34",
         "someone@example.com", "localhost", "not a domain"],
    )
    def test_a_written_entry_that_cannot_be_a_domain_stops_immediately(self, bad: str) -> None:
        with pytest.raises(ValueError):
            safe.parse_domain_allowlist(bad)

    def test_a_subdomain_is_covered_but_a_lookalike_is_not(self) -> None:
        allowlist = safe.parse_domain_allowlist("example.com, other.example.net")
        assert safe.check_domain("example.com", allowlist=allowlist).allowed
        assert safe.check_domain("a.example.com", allowlist=allowlist).allowed
        assert not safe.check_domain("evil-example.com", allowlist=allowlist).allowed
        assert not safe.check_domain("example.com.evil.test", allowlist=allowlist).allowed

    def test_none_and_empty_read_as_the_empty_set(self) -> None:
        assert safe.parse_domain_allowlist(None) == frozenset()
        assert safe.parse_domain_allowlist("") == frozenset()

    def test_the_env_var_name_is_the_one_the_installer_declares(self) -> None:
        """入口读的那一格，与 ``build_client_bundles.py`` 声明给客户端的**同一个名字**。

        两个名字会让「我配了它」与「它读了它」变成两件不同的事。
        """
        declared = (PLUGIN_ROOT / "scripts" / "build_client_bundles.py").read_text(
            encoding="utf-8"
        )
        assert entry.ALLOWED_DOMAINS_ENV in declared
        assert os.environ.get("MARKETOPS_FETCH_ALLOWED_DOMAINS_UNDECLARED") is None


class TestNoEvasion:
    def test_the_user_agent_says_who_we_are(self) -> None:
        proxy = safe.EgressProxy()
        client = safe.GuardedHttpClient(proxy=proxy, gate=safe.EgressGate())
        lowered = client._user_agent.casefold()  # noqa: SLF001
        assert "marketops" in lowered
        for disguise in ("mozilla", "chrome", "safari", "webkit"):
            assert disguise not in lowered, "自报身份里混进了浏览器指纹"
