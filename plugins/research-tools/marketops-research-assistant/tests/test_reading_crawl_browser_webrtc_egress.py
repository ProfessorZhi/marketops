"""Lane G: 受管 Chromium 的出站、DNS 与 WebRTC 非代理 UDP 边界。

这是**离线**判据：不启真浏览器、不访问公网站点、不调供应商、不改 OS
firewall。它判的是「我们写进 BrowserConfig 的那一份启动参数」与「出口不合格时
返回 BLOCKED 而不是直连」，**不是**「本机的 UDP 已经被隔离」。后者需要真实
Chromium + 抓包/OS 级观测，本 Lane 明确记为 ``NOT_RUN``（见
``BROWSER_SECURITY_BOUNDARY``），不得由本文件的绿色反推。

判据分四组：

1. 启动参数：WebRTC 非代理 UDP、DNS 本地解析、QUIC、回环绕过四条护栏都在，
   且单页与截图/PDF 两条启动路径共用同一份。
2. 拒绝覆盖：出口给的参数不得把上述护栏降级或换掉代理（降级即 BLOCKED）。
3. 失败关闭：出口被拒 / 配置缺失 / 非 http(s) scheme / URL 带凭据时，
   **一次浏览器都不许起来**。
4. 运行时边界：readiness 必须把「OS 级 UDP 隔离未实测」说成 ``UNPROVEN``，
   不把只检查 flags 冒充成网络实测安全。
"""
from __future__ import annotations

import sys
import unittest
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import research_adapter_crawl4ai as adapter  # noqa: E402
import research_runtime_transport as transport  # noqa: E402
from crawl4ai_fixtures import (  # noqa: E402
    FakeCrawl4AI,
    FakeCrawlerResult,
    Request,
    limits,
)

UNBOUND = adapter.LOCAL_CRAWL_NOT_SAFE_TO_RUN


@dataclass(frozen=True)
class StubEgress:
    """一个形状合格但**不监听端口**的出口替身。"""

    allowed: bool = True
    proxy_url: str = "http://127.0.0.1:38111"
    extra_args: tuple[str, ...] = (
        adapter.LOOPBACK_BYPASS_GUARD,
        "--disable-quic",
    )
    raw_kwargs: Mapping[str, object] | None = None
    implementation: str = "tests.browser_webrtc_stub"
    contract_sha256: str = "s" * 64

    def decisions(self):
        return ()

    def check(self, url: str):
        return adapter.EgressDecisionView(
            method="GET", target=url, port=80, allowed=self.allowed,
            refusal="" if self.allowed else "stub_refused",
            address="127.0.0.1" if self.allowed else "",
        )

    def browser_config_kwargs(self) -> Mapping[str, object]:
        if self.raw_kwargs is not None:
            return dict(self.raw_kwargs)
        return {"proxy_config": {"server": self.proxy_url},
                "extra_args": list(self.extra_args)}


def make_adapter(egress: StubEgress, fake: FakeCrawl4AI) -> adapter.Crawl4AIAdapter:
    return adapter.Crawl4AIAdapter(
        egress=egress, sdk_available=lambda: True, module_loader=lambda: fake)


def page_request(url: str, **overrides) -> Request:
    params = {
        "url": url,
        "limits": limits(),
        "extraction": {"mode": "markdown"},
        "mode": "render",
        "max_chars": 2000,
    }
    params.update(overrides)
    return Request("crawl4ai.fetch_page", params)


class BrowserLaunchArgTests(unittest.TestCase):
    """启动参数是这一层唯一能**写死**的东西；它本身不是网络实测。"""

    def test_browser_config_carries_webrtc_dns_quic_and_loopback_guards(self):
        fake = FakeCrawl4AI()
        config = adapter._build_browser_config(
            fake,
            egress_kwargs={
                "proxy_config": {"server": "http://127.0.0.1:38111"},
                "extra_args": [adapter.LOOPBACK_BYPASS_GUARD, "--disable-quic"],
            },
            user_agent="MarketOps-Research/test",
        )
        args = config["extra_args"]
        self.assertIn(adapter.LOOPBACK_BYPASS_GUARD, args)
        self.assertIn("--disable-quic", args)
        self.assertIn(adapter.WEBRTC_NON_PROXIED_UDP_GUARD, args)
        self.assertIn(adapter.DNS_REBINDING_GUARD, args)
        self.assertEqual(
            "--webrtc-ip-handling-policy=disable_non_proxied_udp",
            adapter.WEBRTC_NON_PROXIED_UDP_GUARD,
        )
        self.assertIn("MAP * ~NOTFOUND", adapter.DNS_REBINDING_GUARD)
        self.assertIn("EXCLUDE localhost", adapter.DNS_REBINDING_GUARD)
        self.assertIn("EXCLUDE 127.0.0.1", adapter.DNS_REBINDING_GUARD)
        # 代理仍然只从 proxy_config 来：--proxy-server 不在 extra_args 里
        # 再写一份，否则两处地址可以各说各话。
        self.assertEqual({"server": "http://127.0.0.1:38111"},
                         config["proxy_config"])
        self.assertNotIn("proxy", config)

    def test_egress_args_are_preserved_and_deduplicated(self):
        fake = FakeCrawl4AI()
        config = adapter._build_browser_config(
            fake,
            egress_kwargs={
                "proxy_config": {"server": "http://127.0.0.1:38111"},
                "extra_args": [
                    adapter.LOOPBACK_BYPASS_GUARD,
                    "--disable-quic",
                    "--disable-quic",
                    "--disable-background-networking",
                ],
            },
            user_agent="MarketOps-Research/test",
        )
        args = config["extra_args"]
        self.assertEqual(1, args.count("--disable-quic"))
        self.assertIn("--disable-background-networking", args)
        self.assertEqual(len(args), len(set(args)))

    def test_webrtc_policy_downgrade_from_egress_is_refused(self):
        with self.assertRaises(adapter.UpstreamRefusal) as ctx:
            adapter._build_browser_config(
                FakeCrawl4AI(),
                egress_kwargs={
                    "proxy_config": {"server": "http://127.0.0.1:38111"},
                    "extra_args": [
                        adapter.LOOPBACK_BYPASS_GUARD,
                        "--webrtc-ip-handling-policy=default",
                    ],
                },
                user_agent="MarketOps-Research/test",
            )
        self.assertEqual(UNBOUND, ctx.exception.code)

    def test_proxy_and_bypass_overrides_from_egress_are_refused(self):
        dangerous = (
            "--no-proxy-server",
            "--proxy-server",
            "--proxy-server=http://198.51.100.7:8080",
            "--proxy-bypass-list",
            "--proxy-bypass-list=*",
            "--enable-quic",
            "--enable-quic=false",
            "--host-resolver-rules",
            "--host-resolver-rules=MAP * 198.51.100.7",
            "--webrtc-ip-handling-policy",
        )
        for flag in dangerous:
            with self.subTest(flag=flag):
                with self.assertRaises(adapter.UpstreamRefusal) as ctx:
                    adapter._build_browser_config(
                        FakeCrawl4AI(),
                        egress_kwargs={
                            "proxy_config": {"server": "http://127.0.0.1:38111"},
                            "extra_args": [adapter.LOOPBACK_BYPASS_GUARD, flag],
                        },
                        user_agent="MarketOps-Research/test",
                    )
                self.assertEqual(UNBOUND, ctx.exception.code)

    def test_missing_proxy_config_is_refused_before_browser_construction(self):
        with self.assertRaises(adapter.UpstreamRefusal) as ctx:
            adapter._build_browser_config(
                FakeCrawl4AI(),
                egress_kwargs={"extra_args": [adapter.LOOPBACK_BYPASS_GUARD]},
                user_agent="MarketOps-Research/test",
            )
        self.assertEqual(UNBOUND, ctx.exception.code)


class BrowserLaunchPathTests(unittest.TestCase):
    """所有受管启动点共用同一份护栏；没有第二条「另拼一份」的路。"""

    def test_single_page_and_artifact_launches_share_the_hardening(self):
        fake = FakeCrawl4AI(default=FakeCrawlerResult(
            html="<html><body><h1>ok</h1></body></html>",
            url="https://a.example.org/",
            screenshot="aGVsbG8=",
        ))
        instance = make_adapter(StubEgress(), fake)
        single = instance.execute(page_request("https://a.example.org/"))
        self.assertEqual("OK", single.outcome)
        artifact = instance.execute(Request("crawl4ai.screenshot", {
            "url": "https://a.example.org/",
            "limits": limits(),
            "artifacts": {"screenshot": True, "max_bytes_per_artifact": 1024},
        }))
        self.assertEqual("OK", artifact.outcome)
        self.assertEqual(2, len(fake.browser_configs))
        for config in fake.browser_configs:
            self.assertIn(adapter.WEBRTC_NON_PROXIED_UDP_GUARD,
                          config["extra_args"])
            self.assertIn(adapter.DNS_REBINDING_GUARD, config["extra_args"])
            self.assertIn(adapter.LOOPBACK_BYPASS_GUARD, config["extra_args"])
            self.assertIn("--disable-quic", config["extra_args"])
            self.assertEqual({"server": "http://127.0.0.1:38111"},
                             config["proxy_config"])

    def test_normal_http_crawl_still_launches_exactly_once(self):
        fake = FakeCrawl4AI(default=FakeCrawlerResult(
            html="<html><body><h1>ok</h1></body></html>",
            url="https://a.example.org/",
        ))
        outcome = make_adapter(StubEgress(), fake).execute(
            page_request("https://a.example.org/"))
        self.assertEqual("OK", outcome.outcome)
        self.assertEqual(["https://a.example.org/"], fake.calls)
        self.assertEqual(1, len(fake.browser_configs))


class FailClosedTests(unittest.TestCase):
    """出口不合格、被拒、或目标不是 http(s) 时：BLOCKED，且浏览器一次都不起。"""

    def test_denied_proxy_preflight_returns_blocked_without_launch(self):
        fake = FakeCrawl4AI()
        outcome = make_adapter(StubEgress(allowed=False), fake).execute(
            page_request("https://a.example.org/"))
        self.assertEqual("BLOCKED", outcome.outcome)
        self.assertEqual("EGRESS_REFUSED", outcome.reason)
        self.assertEqual([], fake.browser_configs)
        self.assertEqual([], fake.calls)

    def test_missing_proxy_config_returns_blocked_without_launch(self):
        fake = FakeCrawl4AI()
        egress = StubEgress(raw_kwargs={"extra_args": [adapter.LOOPBACK_BYPASS_GUARD]})
        outcome = make_adapter(egress, fake).execute(
            page_request("https://a.example.org/"))
        self.assertEqual("BLOCKED", outcome.outcome)
        self.assertEqual(UNBOUND, outcome.reason)
        self.assertEqual([], fake.browser_configs)
        self.assertEqual([], fake.calls)

    def test_downgraded_webrtc_policy_returns_blocked_without_launch(self):
        fake = FakeCrawl4AI()
        egress = StubEgress(extra_args=(
            adapter.LOOPBACK_BYPASS_GUARD,
            "--webrtc-ip-handling-policy=default",
        ))
        outcome = make_adapter(egress, fake).execute(
            page_request("https://a.example.org/"))
        self.assertEqual("BLOCKED", outcome.outcome)
        self.assertEqual(UNBOUND, outcome.reason)
        self.assertEqual([], fake.browser_configs)

    def test_cross_scheme_url_is_blocked_before_launch(self):
        fake = FakeCrawl4AI()
        for url in ("file:///etc/passwd", "ftp://a.example.org/secret",
                    "data:text/html,<h1>x</h1>"):
            with self.subTest(url=url):
                fake.browser_configs.clear()
                fake.calls.clear()
                outcome = make_adapter(StubEgress(), fake).execute(page_request(url))
                self.assertEqual("BLOCKED", outcome.outcome)
                self.assertEqual("EGRESS_REFUSED", outcome.reason)
                self.assertEqual([], fake.browser_configs)
                self.assertEqual([], fake.calls)

    def test_url_with_userinfo_is_blocked_before_launch(self):
        fake = FakeCrawl4AI()
        outcome = make_adapter(StubEgress(), fake).execute(
            page_request("https://user:secret@a.example.org/"))
        self.assertEqual("BLOCKED", outcome.outcome)
        self.assertEqual("EGRESS_REFUSED", outcome.reason)
        self.assertEqual([], fake.browser_configs)
        self.assertEqual([], fake.calls)

    def test_unbounded_egress_binding_is_blocked_without_launch(self):
        fake = FakeCrawl4AI()
        outcome = adapter.Crawl4AIAdapter(
            sdk_available=lambda: True, module_loader=lambda: fake,
        ).execute(page_request("https://a.example.org/"))
        self.assertEqual("BLOCKED", outcome.outcome)
        self.assertEqual(UNBOUND, outcome.reason)
        self.assertEqual([], fake.browser_configs)


class RuntimeTransportBoundaryTests(unittest.TestCase):
    """runtime transport 自己也要在启动前判一次，而不是把判断全推给 adapter。"""

    def test_good_binding_has_no_launch_blockers(self):
        self.assertEqual((), transport.browser_launch_blockers(StubEgress()))

    def test_downgraded_webrtc_policy_is_a_launch_blocker(self):
        blockers = transport.browser_launch_blockers(StubEgress(extra_args=(
            adapter.LOOPBACK_BYPASS_GUARD,
            "--webrtc-ip-handling-policy=default",
        )))
        self.assertIn("browser_webrtc_policy_override", blockers)

    def test_missing_proxy_is_a_launch_blocker(self):
        blockers = transport.browser_launch_blockers(
            StubEgress(raw_kwargs={"extra_args": [adapter.LOOPBACK_BYPASS_GUARD]}))
        self.assertIn("browser_proxy_config_missing_server", blockers)

    def test_empty_or_invalid_allowlist_is_a_launch_blocker(self):
        self.assertIn("allowlist_empty", transport.allowlist_violations(frozenset()))
        self.assertIn("allowlist_entry_invalid",
                      transport.allowlist_violations(frozenset({""})))
        self.assertIn("allowlist_entry_invalid",
                      transport.allowlist_violations(frozenset({"a.example.org", 7})))
        self.assertEqual((), transport.allowlist_violations(
            frozenset({"a.example.org"})))


class RuntimeProofBoundaryTests(unittest.TestCase):
    """只检查 flags ≠ 网络实测安全。这一条必须是**可读的读数**，不是注释。"""

    def test_readiness_declares_the_os_udp_boundary_unproven(self):
        report = adapter.Crawl4AIAdapter().readiness()
        security = report["browser_security"]
        self.assertEqual("FLAG_ENFORCED", security["webrtc_non_proxied_udp"])
        self.assertEqual("FLAG_ENFORCED", security["local_dns_resolution"])
        self.assertEqual("FLAG_ENFORCED", security["quic_udp"])
        self.assertEqual("UNPROVEN", security["os_level_udp_isolation"])
        self.assertEqual("NOT_RUN", security["runtime_udp_measurement"])

    def test_the_boundary_constant_is_not_a_measurement(self):
        boundary = adapter.BROWSER_SECURITY_BOUNDARY
        self.assertEqual("UNPROVEN", boundary["os_level_udp_isolation"])
        self.assertEqual("NOT_RUN", boundary["runtime_udp_measurement"])
        self.assertNotIn("safe", boundary["os_level_udp_isolation"].lower())
        self.assertNotIn("verified", boundary["runtime_udp_measurement"].lower())


if __name__ == "__main__":
    unittest.main()
