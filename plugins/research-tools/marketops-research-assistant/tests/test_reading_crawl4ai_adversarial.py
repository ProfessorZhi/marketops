"""W-E 的对抗判据：真的走一遍 socket，真的读「上游被连了几次」。

这个文件与 ``test_reading_crawl4ai_adapter.py`` 的分工：

* 那边判**形状与拒绝码**（用不监听端口的替身，快、可穷举）。
* 这边判**管道**：请求真的经了代理、被拒时夹具站点上**一次连接都没有**、
  重定向真的没被跟随、超上限的连接真的被掐断。

**这两件事都不能被读成「出口的判据是对的」。** 夹具代理刻意放行回环地址
（它的目标站点就在回环上），而真出口的判据会拒回环、拒私网、把连接钉在
判过的地址上 —— 那一堆住在 W-A 的 ``tests/test_wave1_egress_*.py``。
"""
from __future__ import annotations

import sys
import unittest
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import research_adapter_crawl4ai as adapter  # noqa: E402
from crawl4ai_fixtures import (  # noqa: E402
    RECORDING_ALLOW_ALL,
    FakeCrawl4AI,
    FakeCrawlerResult,
    RecordingProxy,
    Request,
    SyntheticSite,
    TestEgress,
    limits,
)


def pages_via_egress(egress):
    """一个「取一页」的可调用体，**经出口**。给假 SDK 用。"""

    def fetch(url: str) -> FakeCrawlerResult:
        response = adapter.auxiliary_http_get(egress, url, timeout_s=5,
                                              max_bytes=200_000)
        return FakeCrawlerResult(html=response.body, status_code=response.status,
                                 response_headers=dict(response.headers), url=url)

    return fetch


class NetworkPathTests(unittest.TestCase):
    """IA-4 的正向那一半：放行时请求**真的**经了代理到达站点。"""

    def setUp(self):
        self.site = SyntheticSite(
            sitemap_paths=["/alpha", "/beta"],
            sitemap_outside="https://outside.example.org/x",
        ).start()
        self.addCleanup(self.site.stop)
        self.proxy = RecordingProxy(gate=RECORDING_ALLOW_ALL).start()
        self.addCleanup(self.proxy.stop)
        self.egress = TestEgress(proxy=self.proxy)

    def test_allowed_request_traverses_the_proxy_and_reaches_the_site(self):
        response = adapter.auxiliary_http_get(self.egress, self.site.url("/"))
        self.assertEqual(200, response.status)
        self.assertIn("Fixture Index", response.body)
        # 两条独立读数：站点真的收到了，代理真的记了那次判决。
        self.assertEqual(["/"], self.site.hits)
        self.assertEqual(1, len(self.proxy.decisions))
        self.assertTrue(self.proxy.decisions[0]["allowed"])

    def test_single_page_fetch_goes_through_the_proxy(self):
        instance = adapter.Crawl4AIAdapter(
            egress=self.egress, sdk_available=lambda: True,
            module_loader=lambda: FakeCrawl4AI(fetcher=pages_via_egress(self.egress)))
        outcome = instance.execute(Request("crawl4ai.fetch_page", {
            "url": self.site.url("/alpha"), "limits": limits(),
            "extraction": {"mode": "text"}}))
        self.assertEqual("OK", outcome.outcome)
        self.assertEqual(["/alpha"], self.site.hits)
        row = outcome.payload["manifest"][0]
        self.assertIsNotNone(row["egress_decision_ref"])

    def test_sitemap_seeder_fetches_through_the_proxy(self):
        instance = adapter.Crawl4AIAdapter(
            egress=self.egress, sdk_available=lambda: True,
            module_loader=lambda: FakeCrawl4AI())
        outcome = instance.execute(Request("crawl4ai.sitemap_seed", {
            "sitemap_urls": [self.site.url("/sitemap.xml")],
            "allowed_domains": ["127.0.0.1"],
            "limits": limits(max_pages=5)}))
        self.assertEqual("OK", outcome.outcome)
        # sitemap 那一跳 + 每个 origin 一次 robots —— 后者是站点级的一份文件，
        # 不是每页一份，所以永远只多这一次。
        self.assertEqual(["/sitemap.xml", "/robots.txt"], self.site.hits)
        seeds = outcome.payload["seeds"]
        self.assertEqual(2, len(seeds))
        self.assertTrue(all("outside.example.org" not in item for item in seeds))
        refused = {item["url"] for item in outcome.payload["refused"]}
        self.assertTrue(any("outside.example.org" in item for item in refused), refused)


class NoProxyCannotBypassTheEgress(unittest.TestCase):
    """A host the machine's NO_PROXY lists still goes through the egress proxy."""

    def test_the_site_is_reached_only_through_the_proxy(self):
        from unittest.mock import patch
        site = SyntheticSite().start()
        self.addCleanup(site.stop)
        proxy = RecordingProxy(gate=RECORDING_ALLOW_ALL).start()
        self.addCleanup(proxy.stop)
        with patch.dict("os.environ", {"NO_PROXY": "127.0.0.1,localhost",
                                       "no_proxy": "127.0.0.1,localhost"}):
            response = adapter.auxiliary_http_get(TestEgress(proxy=proxy), site.url("/"))
        self.assertEqual(200, response.status)
        self.assertEqual(1, len(proxy.decisions))  # judged by the proxy, not sent around it


class RefusalEvidenceTests(unittest.TestCase):
    """IA-4 的反向那一半：被拒时上游**一次连接都没有**。"""

    def setUp(self):
        self.site = SyntheticSite().start()
        self.addCleanup(self.site.stop)

    def test_no_egress_means_no_connection_at_all(self):
        instance = adapter.Crawl4AIAdapter(
            egress=adapter.UNBOUND_EGRESS, sdk_available=lambda: True,
            module_loader=lambda: FakeCrawl4AI())
        outcome = instance.execute(Request("crawl4ai.fetch_page", {
            "url": self.site.url("/"), "limits": limits()}))
        self.assertEqual("BLOCKED", outcome.outcome)
        self.assertEqual("LOCAL_CRAWL_NOT_SAFE_TO_RUN", outcome.reason)
        self.assertEqual([], self.site.hits)

    def test_refusing_gate_means_the_site_is_never_connected(self):
        proxy = RecordingProxy().start()  # 缺省 gate = 全拒
        self.addCleanup(proxy.stop)
        egress = TestEgress(proxy=proxy)
        with self.assertRaises(adapter.UpstreamRefusal) as ctx:
            adapter.auxiliary_http_get(egress, self.site.url("/"))
        self.assertEqual("EGRESS_REFUSED", ctx.exception.code)
        # 这一条是这个文件里最重要的那个数：站点上一次连接都没有。
        self.assertEqual([], self.site.hits)
        # 而且**连代理都没被碰过**：预检在构造 opener 之前就把这次调用挡下了，
        # 所以代理上一行判决都没有。比「代理拒了」更强的一句是「代理没收到」。
        self.assertEqual([], proxy.decisions)

    def test_a_reaching_request_is_refused_by_the_proxy_itself(self):
        """预检没挡住的请求到了代理，代理那一层也必须拒，且**不连上游**。"""
        proxy = RecordingProxy().start()  # 缺省 gate = 全拒
        self.addCleanup(proxy.stop)
        from research_always_proxy import AlwaysProxyHandler
        opener = urllib.request.build_opener(AlwaysProxyHandler({"http": proxy.url}))
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            opener.open(self.site.url("/"), timeout=5)
        self.assertEqual(403, ctx.exception.code)
        self.assertEqual([], self.site.hits)
        self.assertEqual(1, len(proxy.decisions))
        self.assertFalse(proxy.decisions[0]["allowed"])

    def test_a_refused_page_is_not_retried_and_is_recorded_once(self):
        proxy = RecordingProxy().start()
        self.addCleanup(proxy.stop)
        egress = TestEgress(proxy=proxy)
        instance = adapter.Crawl4AIAdapter(
            egress=egress, sdk_available=lambda: True,
            module_loader=lambda: FakeCrawl4AI(fetcher=pages_via_egress(egress)))
        outcome = instance.execute(Request("crawl4ai.deep_crawl", {
            "seeds": [self.site.url("/")], "allowed_domains": ["127.0.0.1"],
            "limits": limits(max_pages=3)}))
        self.assertEqual("OK", outcome.outcome)
        self.assertEqual([], self.site.hits)
        page_rows = outcome.payload["pages"]
        self.assertEqual(1, len(page_rows))
        self.assertEqual("BLOCKED", page_rows[0]["outcome"])
        self.assertEqual("EGRESS_REFUSED", page_rows[0]["reason"])
        # 一次拒绝 = 一次出口预检，而不是一次重试风暴；代理连一次都没被碰过。
        self.assertEqual([], proxy.decisions)


class RedirectTests(unittest.TestCase):
    """IA-5：不跟随重定向。"""

    def setUp(self):
        self.site = SyntheticSite().start()
        self.addCleanup(self.site.stop)
        self.proxy = RecordingProxy(gate=RECORDING_ALLOW_ALL).start()
        self.addCleanup(self.proxy.stop)
        self.egress = TestEgress(proxy=self.proxy)

    def test_redirect_is_refused_and_the_target_is_never_requested(self):
        with self.assertRaises(adapter.UpstreamRefusal) as ctx:
            adapter.auxiliary_http_get(self.egress, self.site.url("/redirect"))
        self.assertEqual("CRAWL_REDIRECT_REFUSED", ctx.exception.code)
        # 站点只见过 ``/redirect``：``/beta`` 那一次**没有发生**。
        self.assertEqual(["/redirect"], self.site.hits)

    def test_redirect_refusal_surfaces_as_a_closed_code_through_execute(self):
        instance = adapter.Crawl4AIAdapter(
            egress=self.egress, sdk_available=lambda: True,
            module_loader=lambda: FakeCrawl4AI(fetcher=pages_via_egress(self.egress)))
        outcome = instance.execute(Request("crawl4ai.fetch_page", {
            "url": self.site.url("/redirect"), "limits": limits()}))
        # 3xx 是**收到了的应答**：请求出去过，所以是 FAILED，不是「没触达上游」。
        self.assertEqual("FAILED", outcome.outcome)
        self.assertEqual("CRAWL_REDIRECT_REFUSED", outcome.reason)
        self.assertEqual(["/redirect"], self.site.hits)


class BoundedReadTests(unittest.TestCase):
    """资源上限是 T1 的全部意义所在（CONVDEC §4）。"""

    def setUp(self):
        self.site = SyntheticSite(big_bytes=200_000).start()
        self.addCleanup(self.site.stop)
        self.proxy = RecordingProxy(gate=RECORDING_ALLOW_ALL).start()
        self.addCleanup(self.proxy.stop)
        self.egress = TestEgress(proxy=self.proxy)

    def test_oversized_response_is_cut_off_with_its_own_code(self):
        with self.assertRaises(adapter.CrawlRefusalError) as ctx:
            adapter.auxiliary_http_get(self.egress, self.site.url("/big"),
                                       max_bytes=1024)
        self.assertEqual(adapter.CrawlRefusal.CRAWL_BYTE_CAP_EXCEEDED,
                         ctx.exception.refusal)
        self.assertIn("CRAWL_BYTE_CAP_EXCEEDED", str(ctx.exception))

    def test_byte_budget_is_consumed_and_stops_the_run(self):
        instance = adapter.Crawl4AIAdapter(
            egress=self.egress, sdk_available=lambda: True,
            module_loader=lambda: FakeCrawl4AI(fetcher=pages_via_egress(self.egress)))
        outcome = instance.execute(Request("crawl4ai.fetch_page", {
            "url": self.site.url("/big"),
            "limits": limits(max_bytes=512, max_pages=1)}))
        self.assertEqual("BLOCKED", outcome.outcome)
        self.assertEqual("CRAWL_BYTE_CAP_EXCEEDED", outcome.reason)

    def test_page_cap_stops_the_depth_crawl_before_the_extra_page(self):
        site = SyntheticSite().start()
        self.addCleanup(site.stop)
        instance = adapter.Crawl4AIAdapter(
            egress=self.egress, sdk_available=lambda: True,
            module_loader=lambda: FakeCrawl4AI(fetcher=pages_via_egress(self.egress)))
        outcome = instance.execute(Request("crawl4ai.deep_crawl", {
            "seeds": [site.url("/")], "allowed_domains": ["127.0.0.1"],
            "limits": limits(max_pages=2, max_depth=2)}))
        self.assertEqual("OK", outcome.outcome)
        fetched = [row["url"] for row in outcome.payload["pages"]]
        self.assertEqual(2, len(fetched))
        self.assertEqual("page_cap_reached", outcome.payload["stop_reason"])
        # 上限在**进入抓取之前**判：站点只见过那两页。
        self.assertEqual(["/", "/alpha"], site.hits)
        refused = {item["reason"] for item in outcome.payload["refused_at_plan"]}
        self.assertIn("CRAWL_PAGE_CAP_EXCEEDED", refused)

    def test_depth_zero_fetches_only_the_seed(self):
        site = SyntheticSite().start()
        self.addCleanup(site.stop)
        instance = adapter.Crawl4AIAdapter(
            egress=self.egress, sdk_available=lambda: True,
            module_loader=lambda: FakeCrawl4AI(fetcher=pages_via_egress(self.egress)))
        outcome = instance.execute(Request("crawl4ai.deep_crawl", {
            "seeds": [site.url("/")], "allowed_domains": ["127.0.0.1"],
            "limits": limits(max_pages=5, max_depth=0)}))
        self.assertEqual([site.url("/")],
                         [row["url"] for row in outcome.payload["pages"]])
        self.assertEqual(["/"], site.hits)


class DeepCrawlBoundaryTests(unittest.TestCase):
    """域名边界与链接过滤：深爬**只**在一个域集合内游走。"""

    def setUp(self):
        self.site = SyntheticSite().start()
        self.addCleanup(self.site.stop)
        self.proxy = RecordingProxy(gate=RECORDING_ALLOW_ALL).start()
        self.addCleanup(self.proxy.stop)
        self.egress = TestEgress(proxy=self.proxy)

    def _deep(self, **limit_overrides):
        instance = adapter.Crawl4AIAdapter(
            egress=self.egress, sdk_available=lambda: True,
            module_loader=lambda: FakeCrawl4AI(fetcher=pages_via_egress(self.egress)))
        return instance.execute(Request("crawl4ai.deep_crawl", {
            "seeds": [self.site.url("/")], "allowed_domains": ["127.0.0.1"],
            "limits": limits(**limit_overrides)}))

    def test_off_domain_links_are_refused_and_never_fetched(self):
        outcome = self._deep(max_pages=10)
        refused = {item["url"]: item["reason"]
                   for item in outcome.payload["refused_at_plan"]}
        outside = [url for url in refused if "outside.example.org" in url]
        self.assertTrue(outside)
        self.assertEqual({"OFF_DOMAIN_SEED_REFUSED"}, {refused[url] for url in outside})
        self.assertFalse(any("outside.example.org" in hit for hit in self.site.hits))

    def test_javascript_and_mailto_links_are_never_queued(self):
        outcome = self._deep(max_pages=10)
        everything = " ".join(
            [row["url"] for row in outcome.payload["pages"]]
            + [item["url"] for item in outcome.payload["refused_at_plan"]])
        self.assertNotIn("javascript:", everything)
        self.assertNotIn("mailto:", everything)

    def test_relative_links_are_resolved_against_the_page(self):
        outcome = self._deep(max_pages=10)
        urls = [row["url"] for row in outcome.payload["pages"]]
        self.assertIn(self.site.url("/alpha"), urls)
        self.assertIn(self.site.url("/beta"), urls)

    def test_every_fetched_page_has_its_own_manifest_row(self):
        outcome = self._deep(max_pages=10)
        rows = outcome.payload["manifest"]
        self.assertEqual(len(outcome.payload["pages"]), len(rows))
        for row in rows:
            self.assertEqual(64, len(row["content_sha256"]))
            self.assertEqual("crawl4ai", row["provider"])


class ChallengeAndBlockTests(unittest.TestCase):
    """挑战页与权限墙的正文**不落盘**、不作为材料返回。"""

    def setUp(self):
        self.site = SyntheticSite().start()
        self.addCleanup(self.site.stop)
        self.proxy = RecordingProxy(gate=RECORDING_ALLOW_ALL).start()
        self.addCleanup(self.proxy.stop)
        self.egress = TestEgress(proxy=self.proxy)

    def test_challenge_page_over_the_wire_is_refused(self):
        with self.assertRaises(adapter.UpstreamRefusal) as ctx:
            adapter.auxiliary_http_get(self.egress, self.site.url("/challenge"))
        self.assertEqual("SOURCE_CHALLENGE_PAGE", ctx.exception.code)
        # 命中的是**指纹名**而不是原文：诊断里出现整段挑战页正文，
        # 等于把那张页面的内容抄进了收据。
        self.assertEqual("body:just a moment...", ctx.exception.detail)

    def test_challenge_body_never_reaches_the_payload(self):
        instance = adapter.Crawl4AIAdapter(
            egress=self.egress, sdk_available=lambda: True,
            module_loader=lambda: FakeCrawl4AI(
                results={self.site.url("/challenge"): FakeCrawlerResult(
                    html="<html><body>Just a moment...</body></html>",
                    url=self.site.url("/challenge"))}))
        outcome = instance.execute(Request("crawl4ai.fetch_page", {
            "url": self.site.url("/challenge"), "limits": limits()}))
        self.assertEqual("SOURCE_CHALLENGE_PAGE", outcome.reason)
        self.assertNotIn("Just a moment",
                         adapter.render_outcome_json(outcome))

    def test_403_is_reported_as_source_access_blocked(self):
        with self.assertRaises(adapter.UpstreamRefusal) as ctx:
            adapter.auxiliary_http_get(self.egress, self.site.url("/blocked"))
        self.assertEqual("SOURCE_ACCESS_BLOCKED", ctx.exception.code)


class SecretLeakTests(unittest.TestCase):
    """IA-1：收据、诊断与异常里都不出现凭据形态的东西。

    只判「payload 里没有凭据」是空的：这一层**根本不会**把响应头放进 payload。
    真正会漏的两条路是**异常文本**（上游的报错常带 token）与**诊断字段**
    （挑战页那一格若抄了头值就会带出 ``Set-Cookie``）。这一组判的就是那两条。
    """

    def setUp(self):
        self.site = SyntheticSite().start()
        self.addCleanup(self.site.stop)
        self.proxy = RecordingProxy(gate=RECORDING_ALLOW_ALL).start()
        self.addCleanup(self.proxy.stop)
        self.egress = TestEgress(proxy=self.proxy)

    def test_upstream_exception_text_never_reaches_the_receipt(self):
        class LeakySdk(FakeCrawl4AI):
            def AsyncWebCrawler(self, *, config=None):  # noqa: N802
                class Ctx:
                    async def __aenter__(self_inner):
                        return self_inner

                    async def __aexit__(self_inner, *exc):
                        return None

                    async def arun(self_inner, *, url, config=None):
                        raise RuntimeError(
                            "auth failed: Authorization: Bearer sk-live-TOP-SECRET")

                return Ctx()

        instance = adapter.Crawl4AIAdapter(
            egress=self.egress, sdk_available=lambda: True,
            module_loader=lambda: LeakySdk())
        outcome = instance.execute(Request("crawl4ai.fetch_page", {
            "url": self.site.url("/"), "limits": limits()}))
        self.assertEqual("LOCAL_CRAWL_RUNTIME_FAILED", outcome.reason)
        rendered = adapter.render_outcome_json(outcome)
        for secret in ("TOP-SECRET", "Bearer sk-", "Authorization"):
            self.assertNotIn(secret, rendered, secret)

    def test_challenge_diagnostic_carries_the_marker_not_the_header_values(self):
        instance = adapter.Crawl4AIAdapter(
            egress=self.egress, sdk_available=lambda: True,
            module_loader=lambda: FakeCrawl4AI(default=FakeCrawlerResult(
                html="<html><body>Just a moment...</body></html>",
                response_headers={"cf-mitigated": "challenge",
                                  "Set-Cookie": "session=SUPERSECRET",
                                  "Authorization": "Bearer sk-live-TOP-SECRET"},
                url=self.site.url("/"))))
        outcome = instance.execute(Request("crawl4ai.fetch_page", {
            "url": self.site.url("/"), "limits": limits()}))
        self.assertEqual("SOURCE_CHALLENGE_PAGE", outcome.reason)
        rendered = adapter.render_outcome_json(outcome)
        for secret in ("SUPERSECRET", "TOP-SECRET", "Bearer sk-"):
            self.assertNotIn(secret, rendered, secret)

    def test_the_fixture_site_only_ever_records_paths(self):
        adapter.auxiliary_http_get(self.egress, self.site.url("/alpha"))
        self.assertEqual(["/alpha"], self.site.hits)


if __name__ == "__main__":
    unittest.main()
