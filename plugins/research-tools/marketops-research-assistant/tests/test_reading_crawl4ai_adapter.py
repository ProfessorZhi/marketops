"""W-E CRAWL4AI adapter 判据（离线；无网络、无凭据、无付费、无真 SDK）。

判据按契约的不变量组织：``IR-1..IR-3``（Registry 那一侧落到 adapter 的三条）、
``IA-1..IA-6``（adapter 六条）、``IF-5``（拒绝是闭集码），外加 §3.4 的字段形状。
"""
from __future__ import annotations

import json
import sys
import unittest
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import research_adapter_crawl4ai as adapter  # noqa: E402
import research_crawl4ai_features as features  # noqa: E402
from crawl4ai_fixtures import (  # noqa: E402
    FakeCrawl4AI,
    FakeCrawlerResult,
    Request,
    limits,
)

UNBOUND = "LOCAL_CRAWL_NOT_SAFE_TO_RUN"


@dataclass(frozen=True)
class StubEgress:
    """一个**不监听任何端口**的出口替身。

    它只用来判「绑定形状合格 / 不合格」与「adapter 有没有把出口地址接到浏览器上」。
    真正的网络路径由 ``test_reading_crawl4ai_adversarial.py`` 里的夹具代理负责 ——
    这两件事是不同的判据，刻意放在两个文件里。
    """

    allowed: bool = True
    proxy_url: str = "http://127.0.0.1:9"
    refusal: str = "stub_refused"
    implementation: str = "tests.stub"
    contract_sha256: str = "s" * 64

    def decisions(self):
        return ()

    def check(self, url: str):
        return adapter.EgressDecisionView(
            method="GET", target=url, port=80, allowed=self.allowed,
            refusal="" if self.allowed else self.refusal,
            address="127.0.0.1" if self.allowed else "",
        )

    def browser_config_kwargs(self):
        return {"proxy_config": {"server": self.proxy_url},
                "extra_args": [adapter.LOOPBACK_BYPASS_GUARD, "--disable-quic"]}


def sdk(**kwargs):
    return FakeCrawl4AI(**kwargs)


def make_adapter(egress=None, fake_sdk=None, **kwargs):
    if fake_sdk is None:
        return adapter.Crawl4AIAdapter(egress=egress, sdk_available=lambda: False,
                                       **kwargs)
    return adapter.Crawl4AIAdapter(egress=egress, sdk_available=lambda: True,
                                   module_loader=lambda: fake_sdk, **kwargs)


class ProtocolSurfaceTests(unittest.TestCase):
    """契约 §6：``ProviderAdapter`` 的四个方法，与 Registry 的交叉一致。"""

    def test_provider_and_capabilities(self):
        instance = adapter.Crawl4AIAdapter()
        self.assertEqual("crawl4ai", instance.provider)
        caps = instance.capabilities()
        self.assertTrue(caps)
        self.assertEqual(len(caps), len(set(caps)))
        for cap in caps:
            self.assertTrue(cap.startswith("crawl4ai."), cap)

    def test_the_four_protocol_methods_exist(self):
        instance = adapter.Crawl4AIAdapter()
        for name in ("capabilities", "probe_schema", "execute", "cancel"):
            self.assertTrue(callable(getattr(instance, name)), name)

    def test_transport_matches_the_frozen_contract(self):
        # 契约 §6 的五家 transport 表：Crawl4AI = local_sdk，不得自行更改。
        self.assertEqual("local_sdk", adapter.TRANSPORT)
        self.assertEqual("local_sdk", features.TRANSPORT)


class IntegrationVocabularyTests(unittest.TestCase):
    """adapter 说的名字与 registry 审核过的名字，**必须有一张完整的对应表**。

    本机对着集成 `d88b176` 实测：它的 `REVIEWED_CAPABILITY` 只覆盖
    `crawl4ai.fetch_page`，其余五个抛 `WiringIncomplete`；而它表里另有两条
    （`crawl4ai.crawl_local` / `crawl4ai.crawl_cloud`）指向本 adapter **不提供**的名字。
    E 侧能做的是把「完整的那张表」交出去，并让**自己这一侧**的缺口不可能悄悄存在。
    """

    def test_every_capability_has_a_reviewed_hint(self):
        missing = set(adapter.CAPABILITIES) - set(adapter.REVIEWED_CAPABILITY_HINTS)
        self.assertEqual(set(), missing, f"没有 reviewed 落点的能力：{sorted(missing)}")

    def test_hints_only_name_reviewed_crawl4ai_capabilities(self):
        # registry 里 crawl4ai 只有这两格；给别的名字等于在 adapter 里造一张授权表。
        self.assertEqual({"render", "extract"},
                         set(adapter.REVIEWED_CAPABILITY_HINTS.values()))

    def test_hints_do_not_invent_capabilities_outside_the_adapter(self):
        self.assertEqual(set(adapter.CAPABILITIES),
                         set(adapter.REVIEWED_CAPABILITY_HINTS))


class ReadinessProbeTests(unittest.TestCase):
    """「构造成功」不等于「能干活」。集成侧不传出口时，这一格要说得出来。"""

    def test_a_default_adapter_reports_egress_not_bound(self):
        report = adapter.Crawl4AIAdapter().readiness()
        self.assertEqual("EGRESS_NOT_BOUND", report["verdict"])
        self.assertIn(report["verdict"], adapter.READINESS_VERDICTS)
        self.assertFalse(report["egress_bound"])
        self.assertIn("EgressBinding", report["action"])
        # 它把缺口逐条列出来，而不是只说「不合格」。
        self.assertTrue(report["egress_violations"])

    def test_bound_but_missing_sdk_is_its_own_verdict(self):
        report = adapter.Crawl4AIAdapter(egress=StubEgress(),
                                         sdk_available=lambda: False).readiness()
        self.assertEqual("SDK_NOT_INSTALLED", report["verdict"])
        self.assertIn("crawl4ai", report["action"])

    def test_ready_when_both_are_present(self):
        report = adapter.Crawl4AIAdapter(egress=StubEgress(),
                                         sdk_available=lambda: True).readiness()
        self.assertEqual("READY", report["verdict"])
        self.assertEqual("", report["action"])

    def test_the_probe_does_not_execute_anything(self):
        """判据：``check`` 一被调用就炸，而 readiness 仍然要回答。"""

        class Exploding(StubEgress):
            def check(self, url):
                raise AssertionError("readiness 不该执行任何预检")

            def decisions(self):
                raise AssertionError("readiness 不该读判决")

        report = adapter.Crawl4AIAdapter(egress=Exploding(),
                                         sdk_available=lambda: True).readiness()
        self.assertEqual("READY", report["verdict"])

    def test_the_probe_carries_the_vocabulary_the_integrator_needs(self):
        report = adapter.Crawl4AIAdapter().readiness()
        self.assertEqual(list(adapter.CAPABILITIES), report["capabilities"])
        self.assertEqual(dict(adapter.REVIEWED_CAPABILITY_HINTS),
                         report["reviewed_capability_hints"])


class ReasonCodeClosureTests(unittest.TestCase):
    """契约 §5.3：「禁止回落到自由文本」。"""

    def test_contract_reason_codes_are_transcribed_verbatim(self):
        expected = {
            "POLICY_TIER_NOT_GRANTED", "POLICY_DOMAIN_NOT_ALLOWED", "POLICY_GRANT_EXPIRED",
            "POLICY_GRANT_REVOKED", "BUDGET_RUN_CEILING", "BUDGET_PROVIDER_CEILING",
            "BUDGET_PROJECT_CEILING", "BUDGET_OPERATION_CEILING", "PRICE_UNKNOWN_FAIL_CLOSED",
            "PRICING_SOURCE_MISSING", "DUPLICATE_OPERATION", "OPERATION_ID_COLLISION",
            "HOST_PREVIOUSLY_BLOCKED", "CAPABILITY_UNKNOWN", "CAPABILITY_PARAM_NOT_ALLOWED",
            "CAPABILITY_SCHEMA_DRIFT", "CAPABILITY_NOT_AVAILABLE", "EGRESS_REFUSED",
            "CRAWL4AI_LOCAL_NOT_INSTALLED", "UNKNOWN_CHARGE_OUTSTANDING",
            "RESEARCH_NETWORK_DISABLED", "WALLET_PRECONDITION_FAILED",
            "GRANT_SIGNATURE_INVALID",
        }
        self.assertEqual(expected, set(adapter.CONTRACT_REASON_CODES))

    def test_local_codes_are_disjoint_from_contract_codes(self):
        local = adapter.ALL_REASON_CODES - adapter.CONTRACT_REASON_CODES
        self.assertTrue(local)
        self.assertEqual(set(), local & adapter.CONTRACT_REASON_CODES)

    def test_unknown_reason_code_asserts_at_development_time(self):
        with self.assertRaises(AssertionError):
            adapter._reason("TOTALLY_MADE_UP_CODE")

    def test_blocked_outcomes_only_carry_closed_set_codes(self):
        instance = make_adapter()
        cases = [
            Request("crawl4ai.nope", {}),
            Request("crawl4ai.fetch_page", {"url": "https://a.example.org/", "bogus": 1}),
            Request("crawl4ai.fetch_page", {"url": "https://a.example.org/", "js_code": "x"}),
            Request("crawl4ai.fetch_page", {"url": "https://a.example.org/"}),
        ]
        for request in cases:
            with self.subTest(capability=request.capability_id, params=request.params):
                outcome = instance.execute(request)
                self.assertEqual("BLOCKED", outcome.outcome)
                self.assertIn(outcome.reason, adapter.ALL_REASON_CODES)


class ResultShapeTests(unittest.TestCase):
    """契约 §3.4：字段名逐格对齐，且能直接 ``AdapterResult(**payload)``。"""

    def test_field_set_is_exactly_the_contract_set(self):
        outcome = make_adapter().execute(Request("crawl4ai.nope", {}))
        fields = adapter.to_adapter_result_fields(outcome)
        self.assertEqual(set(adapter.ADAPTER_RESULT_FIELDS), set(fields))
        self.assertEqual(
            {"outcome", "payload", "artifacts", "upstream_ref", "actual_usd",
             "actual_source", "quota_units_used", "transport", "schema_verdict",
             "caveats"}, set(fields))

    def test_reason_goes_into_payload_not_a_new_key(self):
        outcome = make_adapter().execute(Request("crawl4ai.nope", {}))
        fields = adapter.to_adapter_result_fields(outcome)
        self.assertEqual("CAPABILITY_UNKNOWN", fields["payload"]["reason"])
        self.assertNotIn("reason", fields)

    def test_local_outcomes_never_claim_a_usd_actual(self):
        """契约 §4：不得把一次成功的本地调用等同于 $0 实际计费。"""
        outcome = make_adapter().execute(Request("crawl4ai.extract", {
            "html": "<h1>T</h1>",
            "extraction": {"mode": "structured", "fields": [
                {"name": "t", "kind": "css", "selector": "h1"}]},
            "limits": limits(),
        }))
        self.assertEqual("OK", outcome.outcome)
        self.assertIsNone(outcome.actual_usd)
        self.assertEqual("NONE", outcome.actual_source)
        self.assertIsInstance(outcome.quota_units_used, Decimal)

    def test_schema_verdict_is_always_unverified_without_authorized_handshake(self):
        for outcome in (
            make_adapter().execute(Request("crawl4ai.nope", {})),
            make_adapter().execute(Request("crawl4ai.extract", {
                "html": "<h1>T</h1>",
                "extraction": {"mode": "text"}, "limits": limits()})),
        ):
            self.assertEqual("UNVERIFIED", outcome.schema_verdict)


class EgressBindingTests(unittest.TestCase):
    """IA-4：不经代理返回 ``LOCAL_CRAWL_NOT_SAFE_TO_RUN``，而不是无管控回退。"""

    def test_unbound_egress_fails_the_shape_check(self):
        violations = adapter.verify_egress_binding(adapter.UNBOUND_EGRESS)
        self.assertIn("not_a_nonempty_string:proxy_url", violations)
        self.assertIn("not_a_nonempty_string:contract_sha256", violations)

    def test_a_well_formed_binding_passes(self):
        self.assertEqual((), adapter.verify_egress_binding(StubEgress()))

    def test_non_loopback_proxy_is_a_violation(self):
        violations = adapter.verify_egress_binding(StubEgress(proxy_url="http://10.0.0.1:8080"))
        self.assertTrue(any("proxy_not_loopback" in item for item in violations),
                        violations)

    def test_missing_attribute_is_a_violation(self):
        class Incomplete:
            implementation = "x"
            contract_sha256 = "y"
            proxy_url = "http://127.0.0.1:1"

        violations = adapter.verify_egress_binding(Incomplete())
        self.assertIn("missing:decisions", violations)
        self.assertIn("missing:check", violations)

    def test_execute_without_egress_is_blocked_and_touches_nothing(self):
        instance = make_adapter(egress=adapter.UNBOUND_EGRESS, fake_sdk=sdk())
        outcome = instance.execute(Request("crawl4ai.fetch_page", {
            "url": "https://a.example.org/", "limits": limits()}))
        self.assertEqual("BLOCKED", outcome.outcome)
        self.assertEqual(UNBOUND, outcome.reason)
        self.assertEqual([], instance._module_loader().calls)

    def test_egress_refusal_is_reported_with_the_gates_own_reason(self):
        instance = make_adapter(egress=StubEgress(allowed=False, refusal="dns:rebinding"),
                                fake_sdk=sdk())
        outcome = instance.execute(Request("crawl4ai.fetch_page", {
            "url": "https://a.example.org/", "limits": limits()}))
        self.assertEqual("BLOCKED", outcome.outcome)
        self.assertEqual("EGRESS_REFUSED", outcome.reason)
        self.assertIn("dns:rebinding", outcome.payload["detail"])

    def test_probe_schema_distinguishes_the_three_states_and_never_fabricates(self):
        cases = {
            "no_sdk": make_adapter(egress=StubEgress()),
            "unbound": adapter.Crawl4AIAdapter(egress=adapter.UNBOUND_EGRESS,
                                               sdk_available=lambda: True,
                                               module_loader=lambda: sdk()),
            "unverified": make_adapter(egress=StubEgress(), fake_sdk=sdk()),
        }
        for name, instance in cases.items():
            with self.subTest(state=name):
                probe = instance.probe_schema()
                self.assertEqual("AUTH_REQUIRED", probe["status"])
                self.assertIsNone(probe["upstream_tools_list"])
                self.assertEqual("UNVERIFIED", probe["schema_verdict"])
        self.assertEqual("CRAWL4AI_LOCAL_NOT_INSTALLED",
                         cases["no_sdk"].probe_schema()["detail"])
        self.assertEqual(UNBOUND, cases["unbound"].probe_schema()["detail"])
        self.assertEqual("DECISION-04_NOT_AUTHORIZED",
                         cases["unverified"].probe_schema()["detail"])


class RegistryInvariantTests(unittest.TestCase):
    """IR-1 / IR-2 / IR-3。"""

    def test_ir1_unknown_capability_is_blocked_locally(self):
        instance = make_adapter(egress=StubEgress(), fake_sdk=sdk())
        outcome = instance.execute(Request("crawl4ai.does_not_exist", {}))
        self.assertEqual("BLOCKED", outcome.outcome)
        self.assertEqual("CAPABILITY_UNKNOWN", outcome.reason)
        self.assertEqual([], instance._module_loader().calls)

    def test_ir2_unknown_param_keys_are_blocked(self):
        instance = make_adapter(egress=StubEgress(), fake_sdk=sdk())
        for params in (
            {"url": "https://a.example.org/", "limits": limits(), "endpoint": "http://x"},
            {"url": "https://a.example.org/", "limits": limits(), "tier": "T0"},
            {"url": "https://a.example.org/", "limits": limits(), "tool_name": "run"},
            {"url": "https://a.example.org/", "limits": limits(), "host": "evil"},
        ):
            with self.subTest(params=sorted(params)):
                outcome = instance.execute(Request("crawl4ai.fetch_page", params))
                self.assertEqual("BLOCKED", outcome.outcome)
                self.assertEqual("CAPABILITY_PARAM_NOT_ALLOWED", outcome.reason)

    def test_ir3_agent_supplied_tier_and_endpoint_are_ignored_not_honoured(self):
        """越权键不是「被读之后忽略」，而是**根本不进** adapter。"""
        instance = make_adapter(egress=StubEgress(), fake_sdk=sdk())
        outcome = instance.execute(Request("crawl4ai.fetch_page", {
            "url": "https://a.example.org/", "limits": limits(),
            "tier": "T0", "effect_class": "READ_ONLY", "endpoint": "https://evil.example.net/",
        }))
        self.assertEqual("BLOCKED", outcome.outcome)
        self.assertEqual("CAPABILITY_PARAM_NOT_ALLOWED", outcome.reason)
        # 没有任何一次网络动作发生过。
        self.assertEqual([], instance._module_loader().calls)

    def test_forbidden_intent_is_blocked_before_anything_else(self):
        instance = make_adapter(egress=StubEgress(), fake_sdk=sdk())
        cases = {
            "ARBITRARY_JS_FORBIDDEN": {"url": "https://a.example.org/", "limits": limits(),
                                       "js_code": "fetch('/x')"},
            "SITE_LOGIN_FORBIDDEN": {"url": "https://a.example.org/", "limits": limits(),
                                     "profile": "work"},
        }
        for code, params in cases.items():
            with self.subTest(code=code):
                outcome = instance.execute(Request("crawl4ai.fetch_page", params))
                self.assertEqual("BLOCKED", outcome.outcome)
                self.assertEqual(code, outcome.reason)
                # 禁止的键不在 allowed_params 里，所以两者都会先撞上 IR-2；
                # 这里判的是**理由**：禁止的意图优先。
                self.assertEqual(code, outcome.payload["reason"])


class BudgetParamTests(unittest.TestCase):
    def test_missing_limits_is_refused(self):
        instance = make_adapter(egress=StubEgress(), fake_sdk=sdk())
        outcome = instance.execute(Request("crawl4ai.fetch_page",
                                           {"url": "https://a.example.org/"}))
        self.assertEqual("BLOCKED", outcome.outcome)
        self.assertEqual("UNBOUNDED_CRAWL_REFUSED", outcome.reason)

    def test_partial_limits_are_refused_not_defaulted(self):
        instance = make_adapter(egress=StubEgress(), fake_sdk=sdk())
        partial = {"max_pages": 3}
        outcome = instance.execute(Request("crawl4ai.fetch_page",
                                           {"url": "https://a.example.org/",
                                            "limits": partial}))
        self.assertEqual("BLOCKED", outcome.outcome)
        self.assertEqual("UNBOUNDED_CRAWL_REFUSED", outcome.reason)
        self.assertIn("缺", outcome.payload["detail"])

    def test_over_hard_ceiling_is_refused(self):
        instance = make_adapter(egress=StubEgress(), fake_sdk=sdk())
        outcome = instance.execute(Request("crawl4ai.fetch_page", {
            "url": "https://a.example.org/", "limits": limits(max_pages=100_000)}))
        self.assertEqual("BLOCKED", outcome.outcome)
        self.assertEqual("UNBOUNDED_CRAWL_REFUSED", outcome.reason)

    def test_unknown_limit_key_is_refused(self):
        instance = make_adapter(egress=StubEgress(), fake_sdk=sdk())
        outcome = instance.execute(Request("crawl4ai.fetch_page", {
            "url": "https://a.example.org/", "limits": limits(max_steps=10)}))
        self.assertEqual("BLOCKED", outcome.outcome)
        self.assertEqual("UNBOUNDED_CRAWL_REFUSED", outcome.reason)


class AdapterInvariantTests(unittest.TestCase):
    """IA-1 / IA-2 / IA-3 / IA-5 / IA-6。"""

    def test_ia2_adapter_does_not_read_tier_or_policy(self):
        """IA-2：adapter 不做政策判定。判据：它的源码里没有政策面。"""
        source = Path(adapter.__file__).read_text(encoding="utf-8")
        for token in ("research_policy", "UsageLedger", "reserve(", "headroom(",
                      "PolicyDecision", "effective_tier"):
            self.assertNotIn(token, source, f"adapter 不该出现政策面记号 {token!r}")

    def test_ia3_no_internal_retry_on_failure(self):
        class Exploding(FakeCrawl4AI):
            async def _explode(self):
                raise RuntimeError("boom")

        class Boom(FakeCrawl4AI):
            def AsyncWebCrawler(self, *, config=None):  # noqa: N802
                outer = self

                class Ctx:
                    async def __aenter__(self_inner):
                        return self_inner

                    async def __aexit__(self_inner, *exc):
                        return None

                    async def arun(self_inner, *, url, config=None):
                        outer.calls.append(url)
                        raise RuntimeError("browser exploded")

                return Ctx()

        fake = Boom()
        instance = make_adapter(egress=StubEgress(), fake_sdk=fake)
        outcome = instance.execute(Request("crawl4ai.fetch_page", {
            "url": "https://a.example.org/", "limits": limits()}))
        # **FAILED 而不是 BLOCKED**：这一次请求出去过（浏览器起过、崩了）。
        self.assertEqual("FAILED", outcome.outcome)
        self.assertEqual("LOCAL_CRAWL_RUNTIME_FAILED", outcome.reason)
        # **恰好一次**：adapter 不重试。
        self.assertEqual(["https://a.example.org/"], fake.calls)

    def test_ia3_missing_browser_is_its_own_code(self):
        class NoBrowser(FakeCrawl4AI):
            def AsyncWebCrawler(self, *, config=None):  # noqa: N802
                class Ctx:
                    async def __aenter__(self_inner):
                        return self_inner

                    async def __aexit__(self_inner, *exc):
                        return None

                    async def arun(self_inner, *, url, config=None):
                        raise RuntimeError(
                            "Executable doesn't exist at /root/.cache/ms-playwright/chromium")

                return Ctx()

        instance = make_adapter(egress=StubEgress(), fake_sdk=NoBrowser())
        outcome = instance.execute(Request("crawl4ai.fetch_page", {
            "url": "https://a.example.org/", "limits": limits()}))
        self.assertEqual("BLOCKED", outcome.outcome)
        self.assertEqual("LOCAL_BROWSER_NOT_INSTALLED", outcome.reason)

    def test_ia1_no_credential_ever_appears_in_output(self):
        fake = sdk(default=FakeCrawlerResult(
            html="<h1>ok</h1>",
            response_headers={"Authorization": "Bearer sk-live-DEADBEEF",
                              "Set-Cookie": "sid=abcdef"},
            url="https://a.example.org/"))
        instance = make_adapter(egress=StubEgress(), fake_sdk=fake)
        outcome = instance.execute(Request("crawl4ai.fetch_page", {
            "url": "https://a.example.org/", "limits": limits()}))
        rendered = adapter.render_outcome_json(outcome)
        self.assertNotIn("DEADBEEF", rendered)
        self.assertNotIn("sid=abcdef", rendered)
        self.assertNotIn("Bearer", rendered)

    def test_ia6_untrusted_upstream_shapes_are_cleaned(self):
        cleaned = adapter._clean({
            "api_key": "sk-1",
            "nested": {"session_token": "t", "ok": "value"},
            "list": [{"password": "p"}],
            "long": "x" * 30_000,
        })
        self.assertEqual("<redacted>", cleaned["api_key"])
        self.assertEqual("<redacted>", cleaned["nested"]["session_token"])
        self.assertEqual("value", cleaned["nested"]["ok"])
        self.assertEqual("<redacted>", cleaned["list"][0]["password"])
        self.assertIn("<truncated:", cleaned["long"])

    def test_ia6_deep_structure_cannot_blow_the_cleaner(self):
        node: object = {"a": 1}
        for _ in range(40):
            node = {"nested": node}
        cleaned = adapter._clean(node)
        depth = 0
        while isinstance(cleaned, dict) and "nested" in cleaned:
            cleaned = cleaned["nested"]
            depth += 1
        self.assertEqual("<depth-limit>", cleaned)
        self.assertLessEqual(depth, 13)

    def test_ia5_redirect_handler_refuses(self):
        import urllib.request

        handler = adapter._NoRedirect()
        request = urllib.request.Request("https://a.example.org/")
        with self.assertRaises(Exception) as ctx:
            handler.redirect_request(request, None, 302, "Found", {},
                                     "http://127.0.0.1:6379/")
        self.assertIn("CRAWL_REDIRECT_REFUSED", str(ctx.exception))


class SdkWiringTests(unittest.TestCase):
    """真 SDK 装不上，但「我们写了哪些决定」可以在假模块上判死。"""

    def test_browser_config_carries_the_only_egress(self):
        fake = sdk(default=FakeCrawlerResult(html="<h1>ok</h1>",
                                             url="https://a.example.org/"))
        egress = StubEgress(proxy_url="http://127.0.0.1:38111")
        instance = make_adapter(egress=egress, fake_sdk=fake)
        instance.execute(Request("crawl4ai.fetch_page", {
            "url": "https://a.example.org/", "limits": limits()}))
        self.assertEqual(1, len(fake.browser_configs))
        config = fake.browser_configs[0]
        self.assertEqual({"server": "http://127.0.0.1:38111"}, config["proxy_config"])
        # 两格防御与 core 的 crawl4ai_fetcher 逐字一致。
        self.assertEqual(list(adapter.BROWSER_EXTRA_ARGS), config["extra_args"])
        self.assertIn("--proxy-bypass-list=<-loopback>", config["extra_args"])
        self.assertIn("--disable-quic", config["extra_args"])
        # 用 proxy_config 而不是已废弃的 proxy：配了但没接上的那一种是静默的。
        self.assertNotIn("proxy", config)

    def test_run_config_pins_the_three_decisions(self):
        fake = sdk(default=FakeCrawlerResult(html="<h1>ok</h1>",
                                             url="https://a.example.org/"))
        instance = make_adapter(egress=StubEgress(), fake_sdk=fake)
        instance.execute(Request("crawl4ai.fetch_page", {
            "url": "https://a.example.org/", "limits": limits(max_seconds=7)}))
        run_config = fake.run_configs[0]
        self.assertEqual(0, run_config["max_retries"])
        self.assertEqual("BYPASS", run_config["cache_mode"])
        self.assertEqual(7000, run_config["page_timeout"])

    def test_challenge_page_is_refused_by_body_fingerprint(self):
        fake = sdk(default=FakeCrawlerResult(
            html="<html><body>Just a moment... checking your browser</body></html>",
            success=True, status_code=200, url="https://a.example.org/"))
        instance = make_adapter(egress=StubEgress(), fake_sdk=fake)
        outcome = instance.execute(Request("crawl4ai.fetch_page", {
            "url": "https://a.example.org/", "limits": limits()}))
        # 挑战页是**取回之后**才认出来的：请求出去过，所以是 FAILED。
        self.assertEqual("FAILED", outcome.outcome)
        self.assertEqual("SOURCE_CHALLENGE_PAGE", outcome.reason)
        # 挑战页的正文不进入 payload。
        self.assertNotIn("Just a moment", json.dumps(outcome.payload, ensure_ascii=False))

    def test_challenge_header_is_refused_too(self):
        fake = sdk(default=FakeCrawlerResult(
            html="<html><body>normal</body></html>",
            response_headers={"cf-mitigated": "challenge"},
            url="https://a.example.org/"))
        instance = make_adapter(egress=StubEgress(), fake_sdk=fake)
        outcome = instance.execute(Request("crawl4ai.fetch_page", {
            "url": "https://a.example.org/", "limits": limits()}))
        self.assertEqual("FAILED", outcome.outcome)
        self.assertEqual("SOURCE_CHALLENGE_PAGE", outcome.reason)

    def test_access_blocked_status_is_its_own_code(self):
        for status in (401, 403, 429):
            with self.subTest(status=status):
                fake = sdk(default=FakeCrawlerResult(
                    html="<html><body>x</body></html>", status_code=status,
                    url="https://a.example.org/"))
                instance = make_adapter(egress=StubEgress(), fake_sdk=fake)
                outcome = instance.execute(Request("crawl4ai.fetch_page", {
                    "url": "https://a.example.org/", "limits": limits()}))
                self.assertEqual("SOURCE_ACCESS_BLOCKED", outcome.reason)

    def test_successful_fetch_reports_quota_units_and_a_manifest_row(self):
        fake = sdk(default=FakeCrawlerResult(
            html="<html><body><h1>Hello</h1></body></html>",
            url="https://a.example.org/"))
        instance = make_adapter(egress=StubEgress(), fake_sdk=fake)
        outcome = instance.execute(Request("crawl4ai.fetch_page", {
            "url": "https://a.example.org/", "limits": limits(),
            "extraction": {"mode": "text"}}))
        self.assertEqual("OK", outcome.outcome)
        self.assertEqual(Decimal(1), outcome.quota_units_used)
        payload = outcome.payload
        self.assertEqual(1, payload["meter"]["pages"])
        self.assertEqual("pages|bytes|seconds|artifacts", payload["meter"]["units"])
        row = payload["manifest"][0]
        self.assertEqual("crawl4ai", row["provider"])
        self.assertEqual(len(row["content_sha256"]), 64)
        # 证据在没有 ledger 链接之前不可导出（契约 §3.5）—— 这里只是**产出行**，
        # 绑定由 B 的 UsageLedger 在预约之后回填。
        self.assertIsNone(row["ledger_id"])

    def test_screenshot_and_pdf_are_off_by_default(self):
        fake = sdk(default=FakeCrawlerResult(
            html="<html></html>", screenshot=b"PNG", pdf=b"%PDF",
            url="https://a.example.org/"))
        instance = make_adapter(egress=StubEgress(), fake_sdk=fake)
        for capability in ("crawl4ai.screenshot", "crawl4ai.pdf"):
            with self.subTest(capability=capability):
                outcome = instance.execute(Request(capability, {
                    "url": "https://a.example.org/", "limits": limits()}))
                self.assertEqual("BLOCKED", outcome.outcome)
                self.assertEqual("ARTIFACT_CAP_EXCEEDED", outcome.reason)

    def test_screenshot_when_enabled_returns_a_ref_and_hash_not_content(self):
        fake = sdk(default=FakeCrawlerResult(
            html="<html></html>", screenshot=b"PNGDATA" * 10,
            url="https://a.example.org/"))
        instance = make_adapter(egress=StubEgress(), fake_sdk=fake)
        outcome = instance.execute(Request("crawl4ai.screenshot", {
            "url": "https://a.example.org/", "limits": limits(),
            "artifacts": {"screenshot": True}}))
        self.assertEqual("OK", outcome.outcome)
        self.assertEqual(1, len(outcome.artifacts))
        ref = outcome.artifacts[0]
        self.assertEqual("screenshot", ref.kind)
        self.assertEqual(64, len(ref.sha256))
        self.assertNotIn("PNGDATA", adapter.render_outcome_json(outcome))

    def test_oversized_artifact_is_refused(self):
        fake = sdk(default=FakeCrawlerResult(
            html="<html></html>", pdf=b"P" * 5000, url="https://a.example.org/"))
        instance = make_adapter(egress=StubEgress(), fake_sdk=fake)
        outcome = instance.execute(Request("crawl4ai.pdf", {
            "url": "https://a.example.org/", "limits": limits(),
            "artifacts": {"pdf": True, "max_bytes_per_artifact": 100}}))
        self.assertEqual("BLOCKED", outcome.outcome)
        self.assertEqual("ARTIFACT_CAP_EXCEEDED", outcome.reason)

    def test_missing_sdk_is_reported_with_the_contract_code(self):
        instance = make_adapter(egress=StubEgress(), fake_sdk=None)
        outcome = instance.execute(Request("crawl4ai.fetch_page", {
            "url": "https://a.example.org/", "limits": limits()}))
        self.assertEqual("BLOCKED", outcome.outcome)
        self.assertEqual("CRAWL4AI_LOCAL_NOT_INSTALLED", outcome.reason)


class LocalExtractTests(unittest.TestCase):
    """``crawl4ai.extract`` 的两条子路径：给字节（T0 本地）与给 URL（T1）。"""

    def test_inline_html_needs_neither_sdk_nor_egress(self):
        instance = make_adapter(egress=adapter.UNBOUND_EGRESS, fake_sdk=None)
        outcome = instance.execute(Request("crawl4ai.extract", {
            "html": "<h1 id='t'>Title</h1><p class='b'>Body</p>",
            "extraction": {"mode": "structured", "fields": [
                {"name": "title", "kind": "css", "selector": "#t"},
                {"name": "body", "kind": "css", "selector": ".b"}]},
            "limits": limits(),
        }))
        self.assertEqual("OK", outcome.outcome)
        self.assertEqual({"title": "Title", "body": "Body"}, outcome.payload["fields"])
        # 证据行自己说清了「这份字节不是我们取回的」—— 那一句在 manifest 行上，
        # 不在 adapter 的 caveats 上。
        row = outcome.payload["manifest"][0]
        self.assertIsNone(row["url"])
        self.assertIsNone(row["egress_decision_ref"])
        self.assertIn("没有 egress 决定", " ".join(row["caveats"]))

    def test_unsupported_selector_is_a_blocked_with_the_closed_code(self):
        instance = make_adapter()
        outcome = instance.execute(Request("crawl4ai.extract", {
            "html": "<h1>T</h1>",
            "extraction": {"mode": "structured", "fields": [
                {"name": "t", "kind": "css", "selector": "h1:has(a)"}]},
            "limits": limits(),
        }))
        self.assertEqual("BLOCKED", outcome.outcome)
        self.assertEqual("SELECTOR_UNSUPPORTED", outcome.reason)

    def test_url_form_requires_egress(self):
        instance = make_adapter(egress=adapter.UNBOUND_EGRESS, fake_sdk=sdk())
        outcome = instance.execute(Request("crawl4ai.extract", {
            "url": "https://a.example.org/", "limits": limits()}))
        self.assertEqual("BLOCKED", outcome.outcome)
        self.assertEqual(UNBOUND, outcome.reason)


class OutcomeSplitTests(unittest.TestCase):
    """``BLOCKED``（本地拒绝，未触达上游）与 ``FAILED``（上游明确失败）不能混。

    §2 把这两件事分开，而它们在下游的处置不同：``BLOCKED`` 是
    ``NO_CHARGE_PROVEN``，``FAILED`` 是一次真的出去过。把一次 403 记成
    ``BLOCKED`` 会让账上写着「我们没出去」。

    这一组还是**集成判据**：集成侧的网关（``research_gateway.py``）只在
    ``outcome == "BLOCKED"`` 时要求 ``caveats`` 里有一个 §5.3 闭集成员，
    否则抛 ``ContractViolation``。所以每一个 BLOCKED 都必须能说出
    「契约里这件事叫什么」。
    """

    def _blocked_outcomes(self):
        """``(说明, 出口, 请求)``。**出口是按用例给的**：

        出口检查排在预算检查**之前**（先问「这条路安不安全」，再问「这一次多大」），
        所以用 ``UNBOUND_EGRESS`` 跑全部用例会让每一个都返回
        ``LOCAL_CRAWL_NOT_SAFE_TO_RUN``，而预算那几条其实一次都没被执行到 ——
        那种「测试通过是因为没走到那一格」正是本文件要避免的。
        """
        cases = [
            ("unknown capability", adapter.UNBOUND_EGRESS,
             Request("crawl4ai.nope", {})),
            ("surplus param", adapter.UNBOUND_EGRESS,
             Request("crawl4ai.fetch_page",
                     {"url": "https://a.example.org/", "endpoint": "x"})),
            ("forbidden js", adapter.UNBOUND_EGRESS,
             Request("crawl4ai.fetch_page",
                     {"url": "https://a.example.org/", "js_code": "x"})),
            ("forbidden login", adapter.UNBOUND_EGRESS,
             Request("crawl4ai.fetch_page",
                     {"url": "https://a.example.org/", "profile": "p"})),
            ("no egress", adapter.UNBOUND_EGRESS,
             Request("crawl4ai.fetch_page",
                     {"url": "https://a.example.org/", "limits": limits()})),
            ("no limits", StubEgress(),
             Request("crawl4ai.fetch_page", {"url": "https://a.example.org/"})),
            ("partial limits", StubEgress(),
             Request("crawl4ai.fetch_page",
                     {"url": "https://a.example.org/", "limits": {"max_pages": 3}})),
            ("over ceiling", StubEgress(),
             Request("crawl4ai.fetch_page", {
                 "url": "https://a.example.org/",
                 "limits": limits(max_pages=10_000)})),
            ("selector unsupported", adapter.UNBOUND_EGRESS,
             Request("crawl4ai.extract", {
                 "html": "<h1>T</h1>",
                 "extraction": {"mode": "structured", "fields": [
                     {"name": "t", "kind": "css", "selector": "h1:has(a)"}]},
                 "limits": limits()})),
        ]
        for label, egress, request in cases:
            yield label, make_adapter(egress=egress).execute(request)

    def test_pre_dispatch_refusals_are_blocked(self):
        for label, outcome in self._blocked_outcomes():
            with self.subTest(case=label):
                self.assertEqual("BLOCKED", outcome.outcome, label)
                self.assertIn(outcome.reason, adapter.ALL_REASON_CODES)

    def test_every_blocked_outcome_names_a_contract_code_in_caveats(self):
        """网关读的是 ``caveats`` 里的**元素**，所以它必须是裸的闭集成员。"""
        for label, outcome in self._blocked_outcomes():
            with self.subTest(case=label):
                self.assertTrue(outcome.caveats, label)
                self.assertIn(outcome.caveats[0], adapter.CONTRACT_REASON_CODES, label)
                self.assertEqual(adapter.CONTRACT_ANALOGUE.get(outcome.reason,
                                                               outcome.reason),
                                 outcome.caveats[0], label)

    def test_payload_keeps_the_precise_local_code(self):
        """翻译是给下游读的；精确码仍然要留下，否则两类上限就分不开了。"""
        outcomes = dict(self._blocked_outcomes())
        self.assertEqual("UNBOUNDED_CRAWL_REFUSED",
                         outcomes["no limits"].payload["reason"])
        self.assertEqual("BUDGET_OPERATION_CEILING",
                         outcomes["no limits"].payload["contract_reason_code"])
        self.assertEqual("LOCAL_CRAWL_NOT_SAFE_TO_RUN",
                         outcomes["no egress"].payload["reason"])
        self.assertEqual("RESEARCH_NETWORK_DISABLED",
                         outcomes["no egress"].payload["contract_reason_code"])
        # 两个条件若**翻成同一格**，精确码就是唯一还分得开它们的东西。
        # 「没有上限」与「选择器不支持」在契约里落进两格，所以它们本来就分得开；
        # 这一条判的是**分类真的发生了**，不是「随便挑了两个字符串比较」。
        self.assertEqual("BUDGET_OPERATION_CEILING",
                         outcomes["no limits"].payload["contract_reason_code"])
        self.assertEqual("CAPABILITY_PARAM_NOT_ALLOWED",
                         outcomes["selector unsupported"].payload["contract_reason_code"])
        self.assertNotEqual(outcomes["no limits"].payload["reason"],
                            outcomes["selector unsupported"].payload["reason"])

        # 「没给上限」与「上限超过硬顶」共享同一个精确码（两者都是「这次请求没有
        # 可接受的上界」），分开它们的是 detail 那一格 —— 所以那一格必须还在。
        self.assertEqual(outcomes["no limits"].payload["reason"],
                         outcomes["over ceiling"].payload["reason"])
        self.assertIn("超过硬顶", str(outcomes["over ceiling"].payload["detail"]))
        self.assertIn("缺少 limits", str(outcomes["no limits"].payload["detail"]))

    def test_every_analogue_points_into_the_contract_set(self):
        for local, contract in adapter.CONTRACT_ANALOGUE.items():
            with self.subTest(local=local):
                self.assertIn(local, adapter.ALL_REASON_CODES)
                self.assertNotIn(local, adapter.CONTRACT_REASON_CODES)
                self.assertIn(contract, adapter.CONTRACT_REASON_CODES)

    def test_unmapped_local_code_asserts_instead_of_inventing_a_translation(self):
        with self.assertRaises(AssertionError) as ctx:
            adapter._contract_code("SOURCE_CHALLENGE_PAGE_UNMAPPED")
        self.assertIn("CONTRACT_ANALOGUE", str(ctx.exception))

    def test_post_dispatch_failures_are_failed_and_are_marked_dispatched(self):
        fake = sdk(default=FakeCrawlerResult(
            html="<html><body>Just a moment...</body></html>",
            url="https://a.example.org/"))
        outcome = make_adapter(egress=StubEgress(), fake_sdk=fake).execute(
            Request("crawl4ai.fetch_page", {"url": "https://a.example.org/",
                                           "limits": limits()}))
        self.assertEqual("FAILED", outcome.outcome)
        self.assertIn("已经发出", " ".join(outcome.caveats))

    def test_blocked_and_failed_sets_are_disjoint(self):
        self.assertEqual(set(),
                         adapter._POST_DISPATCH_CODES & set(adapter.CONTRACT_ANALOGUE))
        for code in adapter._POST_DISPATCH_CODES:
            with self.subTest(code=code):
                self.assertIn(code, adapter.ALL_REASON_CODES)
                self.assertNotIn(code, adapter.CONTRACT_REASON_CODES)

    def test_failure_helper_refuses_a_pre_dispatch_code(self):
        with self.assertRaises(AssertionError):
            adapter.Crawl4AIAdapter()._failure("CAPABILITY_UNKNOWN", "x")

    def test_refusal_helper_refuses_a_post_dispatch_code(self):
        with self.assertRaises(AssertionError) as ctx:
            adapter.Crawl4AIAdapter()._refusal("SOURCE_ACCESS_BLOCKED", "x")
        self.assertIn("_failure", str(ctx.exception))


class PageProvenanceTests(unittest.TestCase):
    """**这一份文档是不是我们要的那一份。**（离线版；真浏览器版在 live 文件里）

    这几条来自本机实测（crawl4ai 0.9.3 + 真 Chromium）：一次 HTTP 302 **和**一次
    ``<meta http-equiv=refresh>`` 都会让 Chromium 自己跟过去，而库报回来的
    ``status_code`` 是**第一个**响应的、``html`` 是**最终**文档的。被出口拒绝的
    那一跳，最终文档就是出口的拒绝说明 —— 只看状态码会把它读成来源材料。
    """

    def test_redirected_away_compares_ignoring_fragment(self):
        self.assertEqual("", adapter._redirected_away({}, "https://a.example.org/x"))
        self.assertEqual("", adapter._redirected_away(
            {"redirected_url": None}, "https://a.example.org/x"))
        self.assertEqual("", adapter._redirected_away(
            {"redirected_url": "https://a.example.org/x#frag"}, "https://a.example.org/x"))
        self.assertEqual("http://evil.example.org/y", adapter._redirected_away(
            {"redirected_url": "http://evil.example.org/y"}, "https://a.example.org/x"))

    def test_proxy_marker_is_case_insensitive(self):
        self.assertTrue(adapter._proxy_marker_present(
            {adapter.PROXY_MARKER_HEADER: "403"}))
        self.assertTrue(adapter._proxy_marker_present(
            {adapter.PROXY_MARKER_HEADER.casefold(): "403"}))
        self.assertFalse(adapter._proxy_marker_present({"content-type": "text/html"}))

    def test_adapter_refuses_when_the_browser_was_carried_elsewhere(self):
        fake = sdk(default=FakeCrawlerResult(
            html="<html><body>refusal text rendered as a document</body></html>",
            url="https://a.example.org/"))
        fake.default.redirected_url = "http://decoy.example/after"
        outcome = make_adapter(egress=StubEgress(), fake_sdk=fake).execute(
            Request("crawl4ai.fetch_page", {"url": "https://a.example.org/",
                                           "limits": limits()}))
        self.assertEqual("FAILED", outcome.outcome)
        self.assertEqual("CRAWL_REDIRECT_REFUSED", outcome.reason)
        # 那份文档**没有**变成材料。
        self.assertIsNone(outcome.payload.get("text"))
        self.assertNotIn("refusal text rendered", adapter.render_outcome_json(outcome))

    def test_adapter_refuses_a_3xx_even_without_a_redirected_url(self):
        fake = sdk(default=FakeCrawlerResult(
            html="<html><body>moved</body></html>", status_code=302,
            url="https://a.example.org/"))
        outcome = make_adapter(egress=StubEgress(), fake_sdk=fake).execute(
            Request("crawl4ai.fetch_page", {"url": "https://a.example.org/",
                                           "limits": limits()}))
        self.assertEqual("CRAWL_REDIRECT_REFUSED", outcome.reason)

    def test_adapter_refuses_a_page_carrying_the_proxy_marker(self):
        fake = sdk(default=FakeCrawlerResult(
            html="<html><body>x</body></html>",
            response_headers={adapter.PROXY_MARKER_HEADER: "403"},
            url="https://a.example.org/"))
        outcome = make_adapter(egress=StubEgress(), fake_sdk=fake).execute(
            Request("crawl4ai.fetch_page", {"url": "https://a.example.org/",
                                           "limits": limits()}))
        self.assertEqual("EGRESS_REFUSED", outcome.reason)

    def test_a_normal_page_is_not_refused_by_the_provenance_guard(self):
        fake = sdk(default=FakeCrawlerResult(
            html="<html><body><h1>ok</h1></body></html>", url="https://a.example.org/"))
        outcome = make_adapter(egress=StubEgress(), fake_sdk=fake).execute(
            Request("crawl4ai.fetch_page", {"url": "https://a.example.org/",
                                           "limits": limits(),
                                           "extraction": {"mode": "text"}}))
        self.assertEqual("OK", outcome.outcome, outcome.payload)

    def test_same_url_with_a_fragment_is_not_a_redirect(self):
        fake = sdk(default=FakeCrawlerResult(
            html="<html><body><h1>ok</h1></body></html>", url="https://a.example.org/"))
        fake.default.redirected_url = "https://a.example.org/#section"
        outcome = make_adapter(egress=StubEgress(), fake_sdk=fake).execute(
            Request("crawl4ai.fetch_page", {"url": "https://a.example.org/",
                                           "limits": limits(),
                                           "extraction": {"mode": "text"}}))
        self.assertEqual("OK", outcome.outcome, outcome.payload)


class RobotsReasonScopeTests(unittest.TestCase):
    """``ROBOTS_DISALLOWED`` 是**逐种子**的理由，不是一次顶层拒绝的理由。"""

    def test_robots_disallowed_is_a_known_local_code(self):
        self.assertIn("ROBOTS_DISALLOWED", adapter.ALL_REASON_CODES)
        self.assertNotIn("ROBOTS_DISALLOWED", adapter.CONTRACT_REASON_CODES)

    def test_it_never_reaches_the_top_level_refusal_helper(self):
        """顶层 BLOCKED 要求一个 §5.3 对应码；robots 这一格没有，也不该有。

        它只出现在 ``sitemap_seed`` 的 ``refused`` 行里（逐种子），
        所以 ``_refusal()`` 不该接受它 —— 那条断言会在开发期把误用打出来。
        """
        with self.assertRaises(AssertionError):
            adapter.Crawl4AIAdapter()._refusal("ROBOTS_DISALLOWED", "x")


class ArtifactAttributionTests(unittest.TestCase):
    """产物那一格要能回答「这是**哪一份文档**的产物」——请求 URL / 最终 URL / 状态。

    只报请求 URL 会让一份**别的文档**（比如浏览器被带去的出口拒绝页）看起来像是
    我们请求的那一页的产物。本机实测过：一次 302 之后两者不是同一个 URL。
    """

    def _pdf(self, effect, **kwargs):
        fake = sdk(default=effect)
        return make_adapter(egress=StubEgress(), fake_sdk=fake,
                            **kwargs).execute(
            Request("crawl4ai.pdf", {"url": "https://a.example.org/page",
                                     "limits": limits(),
                                     "artifacts": {"pdf": True}}))

    def test_a_normal_page_attributes_request_and_final_url_the_same(self):
        outcome = self._pdf(FakeCrawlerResult(
            html="<html><body>ok</body></html>", pdf=b"%PDF-1.4 x",
            url="https://a.example.org/page"))
        self.assertEqual("https://a.example.org/page",
                         outcome.payload["requested_url"])
        self.assertEqual("https://a.example.org/page", outcome.payload["final_url"])
        self.assertFalse(outcome.payload["redirected"])
        self.assertIsNone(outcome.payload["redirected_to"])
        self.assertEqual(200, outcome.payload["status_code"])

    def test_a_content_receipt_carries_the_same_attribution(self):
        fake = sdk(default=FakeCrawlerResult(
            html="<html><body><h1>ok</h1></body></html>",
            url="https://a.example.org/x"))
        outcome = make_adapter(egress=StubEgress(), fake_sdk=fake).execute(
            Request("crawl4ai.fetch_page", {"url": "https://a.example.org/x",
                                            "limits": limits(),
                                            "extraction": {"mode": "text"}}))
        self.assertEqual("https://a.example.org/x", outcome.payload["requested_url"])
        self.assertEqual("https://a.example.org/x", outcome.payload["final_url"])
        self.assertFalse(outcome.payload["redirected"])

    def test_artifact_is_persisted_under_the_root_and_points_at_that_file(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            outcome = self._pdf(FakeCrawlerResult(
                html="<html><body>ok</body></html>", pdf=b"%PDF-1.4 payload",
                url="https://a.example.org/page"), artifact_root=tmp)
            self.assertTrue(outcome.payload["artifact_persisted"])
            path = outcome.artifacts[0].ref
            self.assertTrue(Path(path).resolve().is_relative_to(Path(tmp).resolve()))
            with open(path, "rb") as handle:
                self.assertEqual(b"%PDF-1.4 payload", handle.read())
            # 文件名只由 hash 与 kind 决定，不掺调用方给的字符串。
            self.assertTrue(path.endswith(".pdf"))
            self.assertIn(outcome.artifacts[0].sha256, path)

    def test_without_an_artifact_root_nothing_is_claimed_to_be_on_disk(self):
        outcome = self._pdf(FakeCrawlerResult(
            html="<html><body>ok</body></html>", pdf=b"%PDF-1.4 payload",
            url="https://a.example.org/page"))
        self.assertFalse(outcome.payload["artifact_persisted"])
        self.assertNotIn("/", outcome.artifacts[0].ref.split(":")[0])

    def test_a_relative_artifact_root_is_refused(self):
        outcome = self._pdf(FakeCrawlerResult(
            html="<html><body>ok</body></html>", pdf=b"%PDF-1.4 payload",
            url="https://a.example.org/page"), artifact_root="relative/path")
        self.assertEqual("BLOCKED", outcome.outcome)
        self.assertEqual("EXTRACTION_SPEC_INVALID", outcome.reason)


class ArtifactProvenanceTests(unittest.TestCase):
    """**产物也要过页面来源判据。**（离线版；真浏览器版在 live 文件里）

    本机实测：在加这道守卫之前，``crawl4ai.pdf`` 打在一个 302 到诱饵的页上会
    返回 ``OK`` + 一份 38,573 字节的 PDF，而 ``pypdf`` 抽出来的正文**就是出口自己的
    拒绝说明**。也就是说「被拒的结果不得落成证据」那条规则在正文那条路上装着，
    在产物这条路上**漏着**。
    """

    def _artifact(self, capability, effect):
        fake = sdk(default=effect)
        return make_adapter(egress=StubEgress(), fake_sdk=fake).execute(
            Request(capability, {"url": "https://a.example.org/", "limits": limits(),
                                 "artifacts": {"pdf": True, "screenshot": True}}))

    def test_pdf_of_a_redirected_away_page_is_refused_with_no_artifact(self):
        outcome = self._artifact("crawl4ai.pdf", FakeCrawlerResult(
            html="<html><body>egress refusal text</body></html>",
            pdf=b"%PDF-1.4 payload", url="https://a.example.org/",
            redirected_url="http://decoy.example/after"))
        self.assertEqual("FAILED", outcome.outcome)
        self.assertEqual("CRAWL_REDIRECT_REFUSED", outcome.reason)
        self.assertEqual((), outcome.artifacts)
        self.assertNotIn("egress refusal text",
                         adapter.render_outcome_json(outcome))

    def test_screenshot_of_a_403_page_is_refused_with_no_artifact(self):
        outcome = self._artifact("crawl4ai.screenshot", FakeCrawlerResult(
            html="<html><body>forbidden</body></html>",
            screenshot=b"PNGDATA", status_code=403, url="https://a.example.org/"))
        self.assertEqual("FAILED", outcome.outcome)
        self.assertEqual("SOURCE_ACCESS_BLOCKED", outcome.reason)
        self.assertEqual((), outcome.artifacts)

    def test_pdf_of_a_page_the_sdk_doubts_is_refused(self):
        outcome = self._artifact("crawl4ai.pdf", FakeCrawlerResult(
            html="<html><body>tiny</body></html>", pdf=b"%PDF-1.4 payload",
            success=False, url="https://a.example.org/",
            error_message=adapter.ANTIBOT_ERROR_PREFIX + ": Structural: minimal_text"))
        self.assertEqual("SOURCE_ANTIBOT_HEURISTIC", outcome.reason)
        self.assertEqual((), outcome.artifacts)

    def test_artifact_carrying_the_proxy_marker_is_refused(self):
        outcome = self._artifact("crawl4ai.pdf", FakeCrawlerResult(
            html="<html><body>x</body></html>", pdf=b"%PDF-1.4 payload",
            response_headers={adapter.PROXY_MARKER_HEADER: "403"},
            url="https://a.example.org/"))
        self.assertEqual("EGRESS_REFUSED", outcome.reason)
        self.assertEqual((), outcome.artifacts)

    def test_a_normal_page_still_yields_its_artifact(self):
        outcome = self._artifact("crawl4ai.pdf", FakeCrawlerResult(
            html="<html><body><h1>ok</h1></body></html>", pdf=b"%PDF-1.4 payload",
            url="https://a.example.org/"))
        self.assertEqual("OK", outcome.outcome, outcome.payload)
        self.assertEqual(1, len(outcome.artifacts))

    def test_a_refused_artifact_records_no_bytes_in_the_receipt(self):
        """被拒时**产物内容**不出现：收据上只有引用与 hash，而这一次连引用都没有。"""
        outcome = self._artifact("crawl4ai.pdf", FakeCrawlerResult(
            html="<html><body>x</body></html>", pdf=b"%PDF-SECRETSENTINEL",
            redirected_url="http://decoy.example/after", url="https://a.example.org/"))
        rendered = adapter.render_outcome_json(outcome)
        self.assertNotIn("SECRETSENTINEL", rendered)
        self.assertNotIn("PDF-SECRETSENTINEL", rendered)
        self.assertIsNone(outcome.payload.get("artifact"))

    def test_a_refused_artifact_still_keeps_its_quota_reading(self):
        outcome = self._artifact("crawl4ai.pdf", FakeCrawlerResult(
            html="<html><body>x</body></html>", pdf=b"%PDF-1.4 payload",
            redirected_url="http://decoy.example/after", url="https://a.example.org/"))
        self.assertEqual(Decimal(1), outcome.quota_units_used)
        self.assertEqual(1, outcome.payload["meter"]["pages"])


class QuotaReceiptTests(unittest.TestCase):
    """**一次失败的抓取也烧掉了配额**，而那一格不能是空的。

    本机实测（对着候选 cf8ff6e 的真浏览器）：一页 403 之后，适配层报
    ``quota_units_used: null``，而出口上明明记着那一跳 —— 一个浏览器秒、
    一页配额被花掉，账上却写着「什么都没消耗」。``null`` 与「0」在下游同形，
    而只有对账的人才会发现它不对。
    """

    def test_failed_page_keeps_its_quota_reading(self):
        fake = sdk(default=FakeCrawlerResult(
            html="<html><body>x</body></html>", status_code=403,
            url="https://a.example.org/"))
        outcome = make_adapter(egress=StubEgress(), fake_sdk=fake).execute(
            Request("crawl4ai.fetch_page", {"url": "https://a.example.org/",
                                            "limits": limits()}))
        self.assertEqual("FAILED", outcome.outcome)
        self.assertEqual("SOURCE_ACCESS_BLOCKED", outcome.reason)
        self.assertEqual(Decimal(1), outcome.quota_units_used)
        self.assertEqual(1, outcome.payload["meter"]["pages"])
        self.assertEqual("pages|bytes|seconds|artifacts",
                         outcome.payload["meter"]["units"])

    def test_redirected_away_page_keeps_its_quota_reading_too(self):
        fake = sdk(default=FakeCrawlerResult(
            html="<html><body>x</body></html>", url="https://a.example.org/"))
        fake.default.redirected_url = "http://decoy.example/after"
        outcome = make_adapter(egress=StubEgress(), fake_sdk=fake).execute(
            Request("crawl4ai.fetch_page", {"url": "https://a.example.org/",
                                            "limits": limits()}))
        self.assertEqual("CRAWL_REDIRECT_REFUSED", outcome.reason)
        self.assertEqual(Decimal(1), outcome.quota_units_used)

    def test_pre_dispatch_refusal_reports_zero_not_none(self):
        """派发前被拒：配额是 **0**，而 0 是一个读数 —— 空的不是。"""
        instance = make_adapter(egress=StubEgress(allowed=False, refusal="private_target"),
                                fake_sdk=sdk())
        outcome = instance.execute(Request("crawl4ai.fetch_page", {
            "url": "https://a.example.org/", "limits": limits()}))
        self.assertEqual("BLOCKED", outcome.outcome)
        self.assertEqual("EGRESS_REFUSED", outcome.reason)
        self.assertEqual(Decimal(0), outcome.quota_units_used)
        self.assertEqual(0, outcome.payload["meter"]["pages"])

    def test_no_meter_was_ever_built_stays_none(self):
        """连计量表都没建起来的那几条（未知能力、越权参数）仍然是 ``None``。

        ``None`` 与 ``0`` 的区别在这里是承重的：前者是「这一次没有可归因的消耗」，
        后者是「消耗了，是零」。
        """
        for request in (Request("crawl4ai.nope", {}),
                        Request("crawl4ai.fetch_page", {"url": "https://a.example.org/",
                                                        "bogus": 1})):
            with self.subTest(capability=request.capability_id):
                outcome = make_adapter(egress=StubEgress()).execute(request)
                self.assertEqual("BLOCKED", outcome.outcome)
                self.assertIsNone(outcome.quota_units_used)
                self.assertNotIn("meter", outcome.payload or {})

    def test_failed_page_without_a_meter_still_works(self):
        # _route_refusal 的 meter 是可选参数：没有它时退化成原来的形状，不炸。
        outcome = adapter.Crawl4AIAdapter()._route_refusal(
            "SOURCE_ACCESS_BLOCKED", "x", "crawl4ai.fetch_page")
        self.assertEqual("FAILED", outcome.outcome)
        self.assertIsNone(outcome.quota_units_used)


class ArtifactHonestyTests(unittest.TestCase):
    """**一个 0 字节的产物不是一次成功。**"""

    def test_missing_artifact_is_refused_not_recorded_as_an_empty_success(self):
        fake = sdk(default=FakeCrawlerResult(html="<html></html>", screenshot=None,
                                             url="https://a.example.org/"))
        outcome = make_adapter(egress=StubEgress(), fake_sdk=fake).execute(
            Request("crawl4ai.screenshot", {"url": "https://a.example.org/",
                                            "limits": limits(),
                                            "artifacts": {"screenshot": True}}))
        self.assertEqual("FAILED", outcome.outcome)
        self.assertEqual("ARTIFACT_EMPTY_FROM_SDK", outcome.reason)
        self.assertEqual((), outcome.artifacts)

    def test_base64_string_screenshot_is_decoded(self):
        import base64

        png = b"\x89PNG\r\n\x1a\n" + b"payload"
        fake = sdk(default=FakeCrawlerResult(
            html="<html></html>",
            screenshot=base64.b64encode(png).decode("ascii"),
            url="https://a.example.org/"))
        outcome = make_adapter(egress=StubEgress(), fake_sdk=fake).execute(
            Request("crawl4ai.screenshot", {"url": "https://a.example.org/",
                                            "limits": limits(),
                                            "artifacts": {"screenshot": True}}))
        self.assertEqual("OK", outcome.outcome, outcome.payload)
        self.assertEqual(adapter.content_sha256(png), outcome.artifacts[0].sha256)

    def test_garbage_artifact_value_is_refused(self):
        fake = sdk(default=FakeCrawlerResult(html="<html></html>", pdf=12345,
                                             url="https://a.example.org/"))
        outcome = make_adapter(egress=StubEgress(), fake_sdk=fake).execute(
            Request("crawl4ai.pdf", {"url": "https://a.example.org/", "limits": limits(),
                                     "artifacts": {"pdf": True}}))
        self.assertEqual("ARTIFACT_EMPTY_FROM_SDK", outcome.reason)

    def test_artifact_run_config_asks_for_the_artifact(self):
        fake = sdk(default=FakeCrawlerResult(html="<html></html>", screenshot=b"PNG",
                                             url="https://a.example.org/"))
        make_adapter(egress=StubEgress(), fake_sdk=fake).execute(
            Request("crawl4ai.screenshot", {"url": "https://a.example.org/",
                                            "limits": limits(),
                                            "artifacts": {"screenshot": True}}))
        self.assertTrue(fake.run_configs[-1].get("screenshot"))
        self.assertEqual(0, fake.run_configs[-1].get("max_retries"))

    def test_new_post_dispatch_codes_do_not_need_a_contract_analogue(self):
        # 反爬启发式与空产物都是**派发之后**的结论：它们走 FAILED，不走 BLOCKED，
        # 因此网关不会要求它们出现在 §5.3 的闭集里（它只在 BLOCKED 上要求）。
        for code in ("SOURCE_ANTIBOT_HEURISTIC", "ARTIFACT_EMPTY_FROM_SDK"):
            with self.subTest(code=code):
                self.assertIn(code, adapter._POST_DISPATCH_CODES)
                self.assertNotIn(code, adapter.CONTRACT_ANALOGUE)


class CancelTests(unittest.TestCase):
    def test_cancel_is_idempotent_and_honest(self):
        instance = adapter.Crawl4AIAdapter()
        first = instance.cancel("some-ref")
        second = instance.cancel("some-ref")
        self.assertEqual("BLOCKED", first.outcome)
        self.assertEqual("CAPABILITY_NOT_AVAILABLE", first.reason)
        self.assertEqual(first, second)
        self.assertIn("没有可取消的上游操作", first.payload["explanation"])


class AuxiliaryHttpTests(unittest.TestCase):
    def test_unbound_egress_refuses_before_building_an_opener(self):
        with self.assertRaises(adapter.UpstreamRefusal) as ctx:
            adapter.auxiliary_http_get(adapter.UNBOUND_EGRESS, "https://a.example.org/")
        self.assertEqual(UNBOUND, ctx.exception.code)

    def test_refusing_gate_is_reported_as_egress_refused(self):
        with self.assertRaises(adapter.UpstreamRefusal) as ctx:
            adapter.auxiliary_http_get(StubEgress(allowed=False, refusal="private_target"),
                                       "https://a.example.org/")
        self.assertEqual("EGRESS_REFUSED", ctx.exception.code)
        self.assertIn("private_target", ctx.exception.detail)


if __name__ == "__main__":
    unittest.main()
