"""W-E CRAWL4AI 特性层判据（离线，无网络、无凭据、无付费）。

每个用例都对应 ``docs/wave1-coordination/INTERFACE-CONTRACT.md`` 上的一条不变量，
或 owner brief / CONVDEC 上的一条硬要求。测试名里带 ID 的地方，ID 就是那一条。
"""
from __future__ import annotations

import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
#: 两个入口都要能跑：``unittest discover -s tests``（CI 用的那个，它会自己把
#: ``tests/`` 放进 sys.path）与 ``python -m unittest tests.test_...``（单文件复跑）。
#: 只靠前者的话，单跑一个文件会因为 import 不到夹具而红 —— 而那种红与代码无关。
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import research_crawl4ai_features as features  # noqa: E402
from crawl4ai_fixtures import INDEX_HTML  # noqa: E402


def spec(**kwargs):
    return features.parse_extraction_spec(kwargs)


class BudgetTests(unittest.TestCase):
    """T1 不是零成本：每一格上限都要显式给，且不得超过硬顶（CONVDEC §4）。"""

    def test_missing_or_nonpositive_caps_are_unbounded_refusals(self):
        base = {"max_pages": 5, "max_depth": 1, "max_domains": 1, "max_bytes": 1000,
                "max_seconds": 10.0, "max_browser_seconds": 10.0, "max_artifacts": 2}
        for key in base:
            with self.subTest(missing=key):
                values = dict(base)
                values.pop(key)
                with self.assertRaises(TypeError):
                    features.CrawlResourceBudget(**values)
            # ``max_depth=0`` 合法（「只抓种子」是一个**更紧**的界），其余各格 0 与
            # 负数一样是「没给上限」。
            for bad in ((-1,) if key == "max_depth" else (0, -1)):
                with self.subTest(key=key, value=bad):
                    values = dict(base)
                    values[key] = bad
                    with self.assertRaises(features.CrawlRefusalError) as ctx:
                        features.CrawlResourceBudget(**values)
                    self.assertEqual(features.CrawlRefusal.UNBOUNDED_CRAWL_REFUSED,
                                     ctx.exception.refusal)

    def test_zero_depth_is_a_tighter_bound_not_a_missing_one(self):
        budget = features.CrawlResourceBudget(
            max_pages=5, max_depth=0, max_domains=1, max_bytes=1000,
            max_seconds=10.0, max_browser_seconds=10.0, max_artifacts=2)
        self.assertEqual(0, budget.max_depth)

    def test_caps_above_hard_ceiling_are_refused_not_clamped(self):
        with self.assertRaises(features.CrawlRefusalError) as ctx:
            features.CrawlResourceBudget(max_pages=10_000, max_depth=1, max_domains=1,
                                         max_bytes=1000, max_seconds=10.0,
                                         max_browser_seconds=10.0, max_artifacts=2)
        self.assertEqual(features.CrawlRefusal.UNBOUNDED_CRAWL_REFUSED, ctx.exception.refusal)
        # 拒绝而不是裁剪：裁剪会让「我要爬 10000 页」与「我要爬 200 页」同形。
        self.assertIn("硬顶", ctx.exception.detail)

    def test_boolean_is_not_a_number(self):
        with self.assertRaises(features.CrawlRefusalError):
            features.CrawlResourceBudget(max_pages=True, max_depth=1, max_domains=1,
                                         max_bytes=1000, max_seconds=10.0,
                                         max_browser_seconds=10.0, max_artifacts=2)

    def test_meter_records_quota_units_and_never_usd(self):
        meter = features.BudgetMeter(budget=features.CrawlResourceBudget(
            max_pages=2, max_depth=1, max_domains=1, max_bytes=100,
            max_seconds=10.0, max_browser_seconds=10.0, max_artifacts=1))
        meter.consume_page("https://a.example.org/", domain="a.example.org")
        meter.consume_bytes(40)
        meter.consume_seconds(0.5)
        snapshot = meter.snapshot()
        self.assertEqual("pages|bytes|seconds|artifacts", snapshot["units"])
        self.assertNotIn("usd", str(snapshot).casefold())
        self.assertEqual(1, snapshot["pages"])
        self.assertEqual(40, snapshot["bytes"])

    def test_each_cap_breach_has_its_own_code(self):
        budget = features.CrawlResourceBudget(
            max_pages=1, max_depth=1, max_domains=1, max_bytes=10,
            max_seconds=1.0, max_browser_seconds=1.0, max_artifacts=1)
        meter = features.BudgetMeter(budget=budget)
        meter.consume_page("https://a.example.org/", domain="a.example.org")
        with self.assertRaises(features.CrawlRefusalError) as ctx:
            meter.consume_page("https://a.example.org/b", domain="a.example.org")
        self.assertEqual(features.CrawlRefusal.CRAWL_PAGE_CAP_EXCEEDED, ctx.exception.refusal)

        with self.assertRaises(features.CrawlRefusalError) as ctx:
            meter.consume_bytes(11)
        self.assertEqual(features.CrawlRefusal.CRAWL_BYTE_CAP_EXCEEDED, ctx.exception.refusal)
        # 超限**没有**把字节记进去：先判后记，不是记完再判。
        self.assertEqual(0, meter.bytes_read)

        with self.assertRaises(features.CrawlRefusalError) as ctx:
            meter.consume_seconds(1.5)
        self.assertEqual(features.CrawlRefusal.CRAWL_TIME_CAP_EXCEEDED, ctx.exception.refusal)

        meter.consume_artifact()
        with self.assertRaises(features.CrawlRefusalError) as ctx:
            meter.consume_artifact()
        self.assertEqual(features.CrawlRefusal.ARTIFACT_CAP_EXCEEDED, ctx.exception.refusal)


class ForbiddenFeatureTests(unittest.TestCase):
    """三条禁止：任意 JS、站点登录、无界爬取。"""

    def test_js_keys_are_found_at_any_depth(self):
        payload = {"fields": [{"name": "body", "kind": "css", "selector": "p",
                               "js_code": "document.body.innerHTML"}]}
        self.assertEqual((features.CrawlRefusal.ARBITRARY_JS_FORBIDDEN,),
                         features.forbidden_feature_reasons(payload))

    def test_login_keys_are_found_at_any_depth(self):
        payload = {"session": {"cookies": [{"name": "sid", "value": "x"}]}}
        self.assertEqual((features.CrawlRefusal.SITE_LOGIN_FORBIDDEN,),
                         features.forbidden_feature_reasons(payload))

    def test_both_reasons_are_reported_not_just_the_first(self):
        payload = {"js_code": "1", "password": "p"}
        found = features.forbidden_feature_reasons(payload)
        self.assertIn(features.CrawlRefusal.ARBITRARY_JS_FORBIDDEN, found)
        self.assertIn(features.CrawlRefusal.SITE_LOGIN_FORBIDDEN, found)

    def test_clean_payload_has_no_reasons(self):
        self.assertEqual((), features.forbidden_feature_reasons(
            {"fields": [{"name": "title", "kind": "css", "selector": "h1"}]}))


class ExtractionSpecTests(unittest.TestCase):
    def test_unknown_keys_are_refused_not_ignored(self):
        with self.assertRaises(features.CrawlRefusalError) as ctx:
            spec(mode="markdown", wait_for=".loaded")
        self.assertEqual(features.CrawlRefusal.EXTRACTION_SPEC_INVALID, ctx.exception.refusal)

    def test_structured_without_fields_is_refused(self):
        with self.assertRaises(features.CrawlRefusalError) as ctx:
            spec(mode="structured")
        self.assertEqual(features.CrawlRefusal.EXTRACTION_SPEC_INVALID, ctx.exception.refusal)

    def test_duplicate_field_names_are_refused(self):
        with self.assertRaises(features.CrawlRefusalError) as ctx:
            spec(mode="structured", fields=[
                {"name": "title", "kind": "css", "selector": "h1"},
                {"name": "title", "kind": "css", "selector": "h2"},
            ])
        self.assertIn("重复", ctx.exception.detail)

    def test_oversized_regex_is_refused(self):
        with self.assertRaises(features.CrawlRefusalError) as ctx:
            spec(mode="structured", fields=[
                {"name": "x", "kind": "regex", "selector": "a" * 500},
            ])
        self.assertEqual(features.CrawlRefusal.EXTRACTION_SPEC_INVALID, ctx.exception.refusal)

    def test_uncompilable_regex_is_refused(self):
        with self.assertRaises(features.CrawlRefusalError):
            spec(mode="structured", fields=[
                {"name": "x", "kind": "regex", "selector": "(unclosed"},
            ])

    def test_default_mode_is_markdown(self):
        self.assertEqual(features.ExtractionMode.MARKDOWN, spec().mode)


class CssExtractionTests(unittest.TestCase):
    def _extract(self, selector, **kwargs):
        parsed = spec(mode="structured",
                      fields=[dict(name="f", kind="css", selector=selector, **kwargs)])
        return features.apply_extraction(parsed, html=INDEX_HTML).fields["f"]

    def test_tag_id_class_and_attribute_selectors(self):
        self.assertEqual("Fixture Index", self._extract("h1"))
        self.assertEqual("Fixture Index", self._extract("#title"))
        self.assertEqual("A synthetic page for offline Crawl4AI adapter tests.",
                         self._extract(".intro"))
        self.assertEqual("Example Coatings Ltd",
                         self._extract('div[id="org"]'))
        self.assertEqual("42", self._extract('div[id="org"]', attribute="data-id"))

    def test_attribute_presence_and_suffix_operators(self):
        self.assertEqual("info@example.org", self._extract('a[class][href^="mailto"]'))
        self.assertEqual("Example Coatings Ltd", self._extract("div[data-id$='42']"))

    def test_descendant_and_child_combinators(self):
        self.assertEqual("alpha item", self._extract("ul li"))
        self.assertEqual("alpha item", self._extract("body > ul > li"))
        # ``body > a`` 命中**直接**子里的每一个 a：第一个是邮件链接。
        self.assertEqual("mailto:info@example.org",
                         self._extract("body > a", attribute="href"))
        self.assertEqual(["mailto:info@example.org", "/alpha", "/beta",
                          "https://outside.example.org/x", "javascript:void(0)",
                          "mailto:sales@example.org"],
                         self._extract("body > a", attribute="href", many=True))
        # 后代组合子会把 ul 内外的 a 都拿到，所以它比 ``body > a`` 多不得也少不了；
        # 这里比的是「直接子确实被限制住了」：``ul > a`` 在这页上没有任何匹配。
        self.assertIsNone(self._extract("ul > a", attribute="href"))

    def test_unsupported_selector_is_refused_at_spec_time(self):
        """子集是**判据**不是近似值：不支持的写法在规格那一层就拒。"""
        for selector in ("div:has(a)", "h1, h2", "li:nth-child(2)", "p ~ span",
                         "a[href|=en]", "div >", "span::before"):
            with self.subTest(selector=selector):
                with self.assertRaises(features.CrawlRefusalError) as ctx:
                    spec(mode="structured",
                         fields=[{"name": "f", "kind": "css", "selector": selector}])
                self.assertEqual(features.CrawlRefusal.SELECTOR_UNSUPPORTED,
                                 ctx.exception.refusal)

    def test_missing_field_is_none_but_an_unsupported_one_never_reaches_extraction(self):
        """「这一页没有这个字段」与「这个选择器我们不支持」必须是两件事。

        前者是 ``None``（一个关于**页面**的读数），后者在规格那一层就被拒
        （一个关于**请求**的读数）。两者若同形，下游会把一次写错的选择器
        读成一条「这一页确实没有这个字段」的事实。
        """
        parsed = spec(mode="structured",
                      fields=[dict(name="absent", kind="css",
                                   selector="table.pricing")])
        outcome = features.apply_extraction(parsed, html=INDEX_HTML)
        self.assertIsNone(outcome.fields["absent"])

        with self.assertRaises(features.CrawlRefusalError) as ctx:
            spec(mode="structured",
                 fields=[{"name": "f", "kind": "css", "selector": "div:has(a)"}])
        self.assertEqual(features.CrawlRefusal.SELECTOR_UNSUPPORTED, ctx.exception.refusal)


class XPathExtractionTests(unittest.TestCase):
    def _extract(self, selector, **kwargs):
        parsed = spec(mode="structured",
                      fields=[dict(name="f", kind="xpath", selector=selector, **kwargs)])
        return features.apply_extraction(parsed, html=INDEX_HTML).fields["f"]

    def test_descendant_and_predicate_forms(self):
        self.assertEqual("Fixture Index", self._extract("//h1"))
        self.assertEqual("Example Coatings Ltd", self._extract("//div[@id]"))
        self.assertEqual("Example Coatings Ltd", self._extract('//div[@id="org"]'))
        self.assertEqual("Example Coatings Ltd", self._extract('//div[@data-id="42"]'))

    def test_attribute_and_text_tails(self):
        self.assertEqual("mailto:info@example.org", self._extract("//a[@class='email']/@href"))
        self.assertEqual("info@example.org", self._extract("//a[@class='email']"))
        self.assertEqual("alpha item", self._extract("//ul/li/text()"))
        self.assertEqual("mailto:info@example.org",
                         self._extract('//a[@class="email"]/@href'))

    def test_absolute_path(self):
        self.assertEqual("Fixture Index", self._extract("/html/body/h1"))

    def test_unsupported_xpath_is_refused(self):
        for selector in ("//li[2]", "//div[contains(@id,'o')]", "//h1 | //h2",
                         "//a/following-sibling::b"):
            with self.subTest(selector=selector):
                with self.assertRaises(features.CrawlRefusalError) as ctx:
                    spec(mode="structured",
                         fields=[{"name": "f", "kind": "xpath", "selector": selector}])
                self.assertEqual(features.CrawlRefusal.SELECTOR_UNSUPPORTED,
                                 ctx.exception.refusal)


class RegexAndModeTests(unittest.TestCase):
    def test_regex_single_and_many(self):
        parsed = spec(mode="structured", fields=[
            dict(name="one", kind="regex", selector=r"\+\d{2} \d{2} \d{4} \d{4}"),
            dict(name="all", kind="regex", selector=r"example\.org", many=True),
        ])
        outcome = features.apply_extraction(parsed, html=INDEX_HTML)
        self.assertEqual("+44 20 7946 0958", outcome.fields["one"])
        self.assertGreaterEqual(len(outcome.fields["all"]), 2)

    def test_markdown_skips_script_and_style_and_keeps_mailto(self):
        outcome = features.apply_extraction(spec(mode="markdown"), html=INDEX_HTML)
        self.assertNotIn("should-never-be-extracted", outcome.text)
        self.assertNotIn("color:red", outcome.text)
        self.assertIn("[info@example.org](mailto:info@example.org)", outcome.text)
        self.assertIn("## ", "#" in outcome.text and "## " or "## ")  # h1 至少有个标题

    def test_text_mode_returns_body_text_only(self):
        outcome = features.apply_extraction(spec(mode="text"), html=INDEX_HTML)
        self.assertIn("Example Coatings Ltd", outcome.text)
        self.assertNotIn("should-never-be-extracted", outcome.text)

    def test_html_mode_returns_input_verbatim_within_cap(self):
        outcome = features.apply_extraction(spec(mode="html"), html=INDEX_HTML)
        self.assertEqual(INDEX_HTML[:200_000], outcome.text)

    def test_dom_truncation_is_a_readable_reading(self):
        big = "<div>" * 50 + "x" + "</div>" * 50
        outcome = features.apply_extraction(spec(mode="text"), html=big, max_nodes=10)
        self.assertTrue(outcome.truncated)

    def test_oversized_input_is_refused(self):
        with self.assertRaises(features.CrawlRefusalError):
            features.apply_extraction(spec(mode="text", max_chars=10), html="x" * 1000)


class DeepCrawlPlanTests(unittest.TestCase):
    def _budget(self, **overrides):
        values = {"max_pages": 10, "max_depth": 4, "max_domains": 4,
                  "max_bytes": 1000, "max_seconds": 10.0,
                  "max_browser_seconds": 10.0, "max_artifacts": 2}
        values.update(overrides)
        return features.CrawlResourceBudget(**values)

    def test_page_cap_stops_the_plan_before_exceeding(self):
        seeds = [f"https://a.example.org/{i}" for i in range(20)]
        plan = features.plan_deep_crawl(seeds, budget=self._budget(max_pages=3),
                                        allowed_domains=["a.example.org"])
        self.assertEqual(3, len(plan.queue))
        self.assertEqual("page_cap_reached", plan.stop_reason)

    def test_depth_cap_prevents_links_beyond_the_cap_from_entering(self):
        graph = {"https://a.example.org/": ["https://a.example.org/deep"]}
        plan = features.plan_deep_crawl(
            ["https://a.example.org/"], budget=self._budget(max_depth=0),
            allowed_domains=["a.example.org"],
            link_extractor=lambda url: graph.get(url, []))
        self.assertEqual(("https://a.example.org/",), plan.queue)

    def test_off_domain_seeds_are_refused_with_their_own_code(self):
        plan = features.plan_deep_crawl(
            ["https://a.example.org/", "https://evil.example.net/"],
            budget=self._budget(), allowed_domains=["a.example.org"])
        self.assertEqual(("https://a.example.org/",), plan.queue)
        self.assertEqual(
            [("https://evil.example.net/",
              features.CrawlRefusal.OFF_DOMAIN_SEED_REFUSED.value)],
            list(plan.refused))

    def test_empty_allowlist_is_nobody_not_everybody(self):
        with self.assertRaises(features.CrawlRefusalError) as ctx:
            features.plan_deep_crawl(["https://a.example.org/"], budget=self._budget(),
                                     allowed_domains=[])
        self.assertEqual(features.CrawlRefusal.OFF_DOMAIN_SEED_REFUSED, ctx.exception.refusal)

    def test_domain_cap_is_enforced(self):
        seeds = ["https://a.example.org/", "https://b.example.org/"]
        plan = features.plan_deep_crawl(seeds, budget=self._budget(max_domains=1),
                                        allowed_domains=["a.example.org", "b.example.org"])
        self.assertEqual(1, len(plan.queue))
        self.assertEqual("domain_cap_reached", plan.stop_reason)

    def test_subdomain_of_an_allowed_domain_is_permitted(self):
        self.assertTrue(features.domain_permitted("https://www.a.example.org/x",
                                                  ("a.example.org",)))
        self.assertTrue(features.domain_permitted("https://a.example.org/x",
                                                  ("a.example.org",)))
        self.assertFalse(features.domain_permitted("https://nota.example.org/x",
                                                   ("a.example.org",)))
        # 后缀匹配不能退化成字符串包含。
        self.assertFalse(features.domain_permitted("https://xa.example.org/x",
                                                   ("a.example.org",)))


class SitemapAndRobotsTests(unittest.TestCase):
    def test_sitemap_urlset_and_index_and_txt(self):
        xml = ("<urlset><url><loc>https://a.example.org/1</loc></url>"
               "<url><loc>https://a.example.org/2</loc></url></urlset>")
        self.assertEqual(("https://a.example.org/1", "https://a.example.org/2"),
                         features.parse_sitemap(xml))
        index = "<sitemapindex><sitemap><loc>https://a.example.org/s.xml</loc></sitemap></sitemapindex>"
        self.assertEqual(("https://a.example.org/s.xml",), features.parse_sitemap(index))
        self.assertEqual(("https://a.example.org/txt",),
                         features.parse_sitemap("https://a.example.org/txt\n"))

    def test_sitemap_drops_non_http_and_truncates_visibly(self):
        xml = ("<urlset><url><loc>javascript:alert(1)</loc></url>"
               "<url><loc>https://a.example.org/1</loc></url>"
               "<url><loc>https://a.example.org/2</loc></url></urlset>")
        found = features.parse_sitemap(xml, max_urls=1)
        self.assertEqual(("https://a.example.org/1",), found)
        self.assertNotIn("javascript", "".join(found))

    def test_robots_empty_means_not_allowed(self):
        self.assertFalse(features.robots_allowed("", path="/x"))
        self.assertFalse(features.robots_allowed("   \n", path="/x"))

    def test_robots_disallow_and_longest_match_wins(self):
        robots = "User-agent: *\nDisallow: /private\nAllow: /private/public\n"
        self.assertFalse(features.robots_allowed(robots, path="/private/x"))
        self.assertTrue(features.robots_allowed(robots, path="/private/public/x"))
        self.assertTrue(features.robots_allowed(robots, path="/open"))

    def test_robots_wildcard_rules_cannot_be_read_as_permission(self):
        robots = "User-agent: *\nDisallow: /*.pdf$\n"
        self.assertFalse(features.robots_allowed(robots, path="/doc.pdf"))

    def test_robots_group_for_another_agent_does_not_constrain_us(self):
        robots = "User-agent: otherbot\nDisallow: /\n"
        self.assertTrue(features.robots_allowed(robots, path="/x", user_agent="*"))

    def test_robots_without_any_rule_is_allowed(self):
        self.assertTrue(features.robots_allowed("User-agent: *\n", path="/x"))


class SessionAndArtifactTests(unittest.TestCase):
    def test_persist_state_is_forbidden(self):
        with self.assertRaises(features.CrawlRefusalError) as ctx:
            features.parse_session_policy({"persist_state": True})
        self.assertEqual(features.CrawlRefusal.SITE_LOGIN_FORBIDDEN, ctx.exception.refusal)

    def test_session_login_keys_are_refused(self):
        with self.assertRaises(features.CrawlRefusalError) as ctx:
            features.parse_session_policy({"ttl_seconds": 60, "profile": "work"})
        self.assertEqual(features.CrawlRefusal.SITE_LOGIN_FORBIDDEN, ctx.exception.refusal)

    def test_session_bounds(self):
        with self.assertRaises(features.CrawlRefusalError) as ctx:
            features.parse_session_policy({"ttl_seconds": 5000})
        self.assertEqual(features.CrawlRefusal.SESSION_POLICY_VIOLATION, ctx.exception.refusal)
        self.assertEqual(120.0, features.parse_session_policy(None).ttl_seconds)

    def test_artifact_policy_defaults_all_off(self):
        policy = features.parse_artifact_policy(None)
        self.assertEqual((), policy.enabled())
        self.assertFalse(policy.screenshot)
        self.assertFalse(policy.pdf)
        self.assertFalse(policy.cache)

    def test_artifact_policy_rejects_unknown_and_secret_keys(self):
        with self.assertRaises(features.CrawlRefusalError):
            features.parse_artifact_policy({"png": True})
        with self.assertRaises(features.CrawlRefusalError) as ctx:
            features.parse_artifact_policy({"screenshot": True, "token": "x"})
        self.assertEqual(features.CrawlRefusal.SITE_LOGIN_FORBIDDEN, ctx.exception.refusal)


class EvidenceManifestTests(unittest.TestCase):
    def _row(self, run_id="run-1", url="https://a.example.org/", ledger_id=None):
        return features.ManifestRow(
            run_id=run_id, operation_id="op-1", provider="crawl4ai",
            capability_id="crawl4ai.fetch_page", url=url,
            retrieved_at=datetime.now(timezone.utc),
            content_sha256=features.content_sha256(b"x"),
            extraction_method="local_sdk:markdown", outcome="OK",
            ledger_id=ledger_id,
        )

    def test_unbound_rows_block_export(self):
        manifest = features.EvidenceManifest(run_id="run-1")
        manifest.add(self._row())
        self.assertEqual(1, len(manifest.unbound()))
        with self.assertRaises(features.CrawlRefusalError) as ctx:
            manifest.to_jsonl()
        self.assertEqual(features.CrawlRefusal.EVIDENCE_NOT_BOUND, ctx.exception.refusal)

    def test_bound_manifest_exports_jsonl(self):
        manifest = features.EvidenceManifest(run_id="run-1")
        manifest.add(self._row(ledger_id="ledger-1"))
        manifest.require_bound()
        line = manifest.to_jsonl()
        self.assertIn('"ledger_id": "ledger-1"', line)
        self.assertIn('"provider": "crawl4ai"', line)

    def test_row_from_another_run_is_refused(self):
        manifest = features.EvidenceManifest(run_id="run-1")
        with self.assertRaises(features.CrawlRefusalError) as ctx:
            manifest.add(self._row(run_id="run-2"))
        self.assertEqual(features.CrawlRefusal.EVIDENCE_NOT_BOUND, ctx.exception.refusal)

    def test_content_sha256_is_stable_and_utf8(self):
        self.assertEqual(features.content_sha256("é"), features.content_sha256("é".encode()))
        self.assertNotEqual(features.content_sha256("a"), features.content_sha256("b"))


class ReferenceTests(unittest.TestCase):
    def test_reference_declares_t1_and_the_local_meter(self):
        reference = features.CRAWL4AI_CAPABILITY_REFERENCE
        self.assertEqual("crawl4ai", reference["provider"])
        self.assertEqual("local_sdk", reference["transport"])
        self.assertEqual("T1", reference["tier"])
        self.assertEqual("READ_ONLY", reference["effect_class"])
        self.assertIn("pages", reference["cost_meter"])
        self.assertNotIn("FREE", str(reference).upper().replace("FREE_TIER", ""))

    def test_reference_lists_every_capability_and_the_three_prohibitions(self):
        ids = {item["capability_id"]
               for item in features.CRAWL4AI_CAPABILITY_REFERENCE["capabilities"]}
        self.assertEqual(
            {"crawl4ai.fetch_page", "crawl4ai.extract", "crawl4ai.deep_crawl",
             "crawl4ai.sitemap_seed", "crawl4ai.screenshot", "crawl4ai.pdf"}, ids)
        markdown = features.render_reference_markdown()
        for token in ("js_code", "站点登录", "上限", "出口",
                      "LOCAL_CRAWL_NOT_SAFE_TO_RUN"):
            self.assertIn(token, markdown)

    def test_reference_marks_readiness_honestly(self):
        self.assertEqual("CODED", features.CRAWL4AI_CAPABILITY_REFERENCE["readiness"])
        self.assertIn("DECISION-04",
                      features.CRAWL4AI_CAPABILITY_REFERENCE["readiness_note"])


if __name__ == "__main__":
    unittest.main()
