"""Default-path failures observed in the first native client run; no live I/O."""
import asyncio
import json
import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import research_mcp as mcp
import research_runtime_transport as transport
import _runtime_mcp_fixture as fixture


class NativePathRegressionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        env = patch.dict(os.environ, {"MARKETOPS_RESEARCH_DATA_ROOT": str(self.root),
            "MARKETOPS_RESEARCH_ALLOW_NETWORK": "1", "MARKETOPS_FETCH_ALLOWED_DOMAINS": "",
            "TINYFISH_API_KEY": "synthetic-key"})
        env.start(); self.addCleanup(env.stop)
        fixture.install(self, self.root, providers=("tinyfish",))
        self.calls = []
        fixture.bind(self, self.root, calls=self.calls, result=lambda p,t,a: {
            "results": [{"url": a["urls"][0], "html": "<p>Public venue source</p>"}]})
        from _tinyfish_egress_fixture import FakeResolver
        resolver = patch("research_egress.safe.SystemResolver", FakeResolver)
        resolver.start(); self.addCleanup(resolver.stop)
        mcp.start("run", "scope", "public venue", "rules", {
            "record_type": "research_brief", "target_kind": "venue", "goal": "pool",
            "require_named_person": False, "minimum_targets": None})

    def test_tinyfish_uses_json_scope_without_legacy_env_and_still_denies_other_domains(self):
        params = {"urls": ["https://example.org/venue"], "format": "html", "links": False,
                  "image_links": False, "iframe_links": False, "page_metadata": False}
        okay = mcp.managed_call("tinyfish.fetch_content", params, "run", "allowed")
        self.assertEqual("OK", okay["status"], okay)
        self.assertEqual(1, len(self.calls))
        denied = mcp.managed_call("tinyfish.fetch_content", {**params, "urls": ["https://unapproved.example.net/"]},
                                 "run", "unapproved")
        self.assertEqual("POLICY_DOMAIN_NOT_ALLOWED", denied["reason_code"])
        self.assertEqual(1, len(self.calls))
        self.assertEqual("", os.environ["MARKETOPS_FETCH_ALLOWED_DOMAINS"])
        from _tinyfish_egress_fixture import FakeResolver
        with patch("research_egress.safe.SystemResolver", lambda: FakeResolver("127.0.0.1")):
            inward = mcp.managed_call("tinyfish.fetch_content", {**params, "urls": ["https://example.org/inward"]},
                                      "run", "inward")
        self.assertEqual("EGRESS_REFUSED", inward["reason_code"])
        import research_usage_ledger as usage
        self.assertEqual("NO_CHARGE_PROVEN", usage.for_root(self.root).get(inward["ledger_operation_id"]).charge_state)
        self.assertEqual(1, len(self.calls))


class ResourceTemplateRegressionTests(unittest.TestCase):
    def test_engine_schema_template_is_discovered_from_its_real_separate_method(self):
        import serpapi_engine_catalog as catalog
        class Session:
            async def list_resources(self):
                return SimpleNamespace(resources=[SimpleNamespace(uri=catalog.INDEX_URI)])
            async def list_resource_templates(self):
                return SimpleNamespace(resource_templates=[SimpleNamespace(uri_template=catalog.SCHEMA_URI_TEMPLATE)])
            async def read_resource(self, uri):
                return SimpleNamespace(contents=[SimpleNamespace(uri=uri, text=json.dumps({
                    "type": "object", "properties": {"q": {"type": "string"}}}))])
        with tempfile.TemporaryDirectory() as temp:
            cache = catalog.EngineCatalog(Path(temp) / "engines.json")
            cache.record_index(["google_light"], source_uri=catalog.INDEX_URI)
            checked = transport.CheckedSerpApiSession(Session(), Path(temp))
            result = asyncio.run(catalog.discover_schema(checked, "google_light", catalog=cache))
            self.assertEqual("RESOURCE_READ", result["source"])
            self.assertIsNotNone(cache.cached_schema("google_light"))
            events = [json.loads(x) for x in (Path(temp) / "state/runtime-audit.jsonl").read_text().splitlines()]
            self.assertEqual(["intent", "result"], [x["phase"] for x in events if x["method"] == "resources/templates/list"])


class FailureReasonRegressionTests(unittest.TestCase):
    def test_managed_and_chain_receipts_keep_closed_provider_reason_and_do_not_switch(self):
        from test_delivery_runtime import DefaultDispatchTests
        import research_adapter_serper as serper
        import research_runtime_results as results
        import research_fallback as fallback
        from research_gateway import GatewayOutcome
        from research_s0_preflight_gate import GuardedOutcome
        case = DefaultDispatchTests("test_default_gateway_returns_and_persists_real_adapter_result")
        case.setUp(); self.addCleanup(case.doCleanups)
        raw = serper.SerperResult(outcome="FAILED", payload={"reason": "SOURCE_ACCESS_BLOCKED", "status_code": 403})
        outcome = GatewayOutcome(status="FAILED", reason_code="", capability_id="crawl4ai.fetch_page", provider="crawl4ai", result=raw)
        response = results.persist(case.root, "run-a", "failure", GuardedOutcome(
            status="FAILED", reason_code="", capability_id="crawl4ai.fetch_page", gateway_invoked=True, outcome=outcome),
            params={"url": "https://example.org/"})
        self.assertEqual("SOURCE_ACCESS_BLOCKED", response["reason_code"])
        self.assertIn("SOURCE_ACCESS_BLOCKED", response["provider_reason_codes"])
        calls = []
        def managed(**kw): calls.append(kw); return response
        chain = fallback.research_step(case.root, run_id="run-a", operation_id="chain", task_kind="known_public_url",
            url="https://example.org/", managed_call=managed, admission=lambda _: {"allowed":True})
        self.assertEqual(1, len(calls))
        self.assertIn("SOURCE_ACCESS_BLOCKED", chain["attempts"][0]["provider_reason_codes"])
        self.assertEqual("SOURCE_ACCESS_BLOCKED", chain["attempts"][0]["reason_code"])
        self.assertEqual("FALLBACK_SOURCE_OR_POLICY_REFUSED", chain["reason_code"])


if __name__ == "__main__": unittest.main()
