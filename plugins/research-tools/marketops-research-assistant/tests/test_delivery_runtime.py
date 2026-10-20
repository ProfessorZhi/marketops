"""Product composition tests: real registry/policy/ledger, wire boundary replaced."""
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
import io
import asyncio
from types import SimpleNamespace
from contextlib import contextmanager, asynccontextmanager

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import research_runtime_setup as runtime
import research_wiring as wiring


class RuntimeSetupTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def configure(self, **kwargs):
        return runtime.configure(self.root, providers=["serper"], domains=["example.org"],
                                 max_calls=3, task="public venue research", confirm=True,
                                 **kwargs)

    def test_configuration_requires_explicit_finite_local_consent(self):
        with self.assertRaises(ValueError):
            runtime.configure(self.root, providers=["serper"], domains=[], max_calls=0,
                              task="test", confirm=False)
        self.assertFalse((self.root / "state").exists())

    def test_configuration_is_idempotent_but_cannot_reset_or_expand_budget(self):
        first = self.configure()
        self.assertEqual(first, self.configure())
        with self.assertRaises(ValueError):
            runtime.configure(self.root, providers=["serper"], domains=["example.org"],
                              max_calls=30, task="public venue research", confirm=True)

    def test_unknown_account_balance_is_not_fabricated(self):
        self.configure()
        state = runtime.status(self.root)
        self.assertEqual("REQUEST_COUNT", state["budget_mode"])
        self.assertIsNone(state["providers"]["serper"]["balance"])
        self.assertFalse(state["dollar_limit_enforced"])

    def test_schema_capture_must_match_reviewed_wire_contract(self):
        good = {"tools": [{"name": "search", "inputSchema": {
            "type": "object", "properties": {"params": {"type": "object"},
            "mode": {"type": "string"}}, "required": ["params"]}}]}
        accepted = runtime.review_schema("serpapi", good)
        self.assertIn("serpapi.search", accepted)
        good["tools"][0]["inputSchema"]["required"].append("secret_instruction")
        self.assertEqual([], runtime.review_schema("serpapi", good))

    def test_nonselected_provider_does_not_block_selected_provider(self):
        self.configure()
        with patch.object(runtime, "verify_build_receipt", return_value={"ok": True}), \
                patch.dict(os.environ, {"SERPER_API_KEY": "fixture-key"}, clear=True):
            self.assertTrue(runtime.admission(self.root, "serper.search")["allowed"])
            self.assertFalse(runtime.admission(self.root, "tinyfish.search")["allowed"])

    def test_qa_receipt_must_match_current_runtime_bytes(self):
        self.configure()
        self.assertFalse(runtime.verify_build_receipt()["ok"])

    def test_default_factory_binds_credentials_and_real_transports(self):
        self.configure()
        with patch.dict(os.environ, {"SERPER_API_KEY": "fixture-key",
                                    "SERPAPI_API_KEY": "fixture-key",
                                    "TYPESAFE_API_KEY": "fixture-key"}):
            adapters = wiring.assemble_adapters(root=self.root)
        self.assertTrue(callable(adapters["serper"]._transport))
        self.assertIsNotNone(adapters["serper"]._api_key)
        self.assertIsNotNone(adapters["serpapi"]._session_factory)
        self.assertIsNotNone(adapters["typesafe_jev"]._api_key)


class DefaultDispatchTests(unittest.TestCase):
    def setUp(self):
        import research_mcp as mcp
        self.mcp = mcp
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.env = patch.dict(os.environ, {"MARKETOPS_RESEARCH_DATA_ROOT": str(self.root),
            "MARKETOPS_RESEARCH_ALLOW_NETWORK": "1", "SERPER_API_KEY": "fixture-key"})
        self.env.start()
        self.addCleanup(self.env.stop)
        runtime.configure(self.root, providers=["serper"], domains=["example.org"],
                          max_calls=2, task="public venues", confirm=True)
        self.qa = patch.object(runtime, "verify_build_receipt", return_value={"ok": True})
        self.qa.start()
        self.addCleanup(self.qa.stop)
        brief = {"record_type": "research_brief", "target_kind": "venue", "goal": "pool",
                 "minimum_targets": None, "require_named_person": False,
                 "evidence_requirements": {"venue": ["identity", "location"]}}
        self.mcp.start("run-a", "test-scope", "public venue research", "rules-v1", brief)
        self.calls = []
        @contextmanager
        def wire(request, timeout):
            self.calls.append(request.full_url)
            response = io.BytesIO(json.dumps({"organic": [{"title": "Venue",
                "link": "https://example.org/venue", "snippet": "Public venue source"}]}).encode())
            response.status = 200
            yield response
        self.wire = patch("research_runtime_transport.serper_transport", wire)
        self.wire.start()
        self.addCleanup(self.wire.stop)

    def test_default_gateway_returns_and_persists_real_adapter_result(self):
        result = self.mcp.search("serper", "public venues", "US", "en", "run-a", "op-1")
        self.assertEqual("OK", result["status"])
        self.assertEqual(1, len(self.calls))
        self.assertTrue(result["artifact_ids"])
        self.assertTrue(result["stored_record_ids"])
        import research_lite_store as store
        saved = store.read_artifact(self.root, "run-a", result["artifact_ids"][0])
        self.assertIn(b"Public venue source", saved["content"])

    def test_explicit_network_off_refuses_even_with_existing_local_consent(self):
        with patch.dict(os.environ, {"MARKETOPS_RESEARCH_ALLOW_NETWORK": "0"}):
            result = self.mcp.managed_call("serper.search", {"query": "public venues"},
                                         "run-a", "disabled")
        self.assertEqual("RESEARCH_NETWORK_DISABLED", result["reason_code"])
        self.assertFalse(result["gateway_invoked"])
        self.assertEqual([], self.calls)
        self.assertFalse((self.root / "state" / "usage-ledger.sqlite3").exists())

    def test_renamed_managed_entry_cannot_repeat_legacy_purchase(self):
        self.mcp.search("serper", "public venues", "US", "en", "run-a", "op-1")
        result = self.mcp.managed_call("serper.search", {"query": "public venues"},
                                       "run-a", "renamed-operation")
        self.assertEqual("BLOCKED", result["status"])
        self.assertEqual("DUPLICATE_OPERATION", result["reason_code"])
        self.assertEqual(1, len(self.calls))

    def test_new_gateway_and_run_cannot_replenish_project_allowance(self):
        for i in range(2):
            self.mcp.search("serper", "venue query " + str(i), "US", "en", "run-a", "op-" + str(i))
        result = self.mcp.managed_call("serper.search", {"query": "different venues"},
                                       "run-a", "op-last")
        self.assertEqual("BLOCKED", result["status"])
        self.assertIn(result["reason_code"], {"BUDGET_RUN_CEILING", "BUDGET_PROJECT_CEILING", "BUDGET_PROVIDER_CEILING"})
        self.assertEqual(2, len(self.calls))

    def test_explicit_renewal_keeps_two_runs_material_and_consumed_allowance(self):
        first = self.mcp.search("serper", "venue first", "US", "en", "run-a", "one")
        runtime.configure(self.root, providers=["serper"], domains=["example.org"],
            max_calls=3, task="second public research task", confirm=True, amend=True)
        brief = {"record_type": "research_brief", "target_kind": "venue", "goal": "pool",
                 "minimum_targets": None, "require_named_person": False}
        self.mcp.start("run-b", "test-scope", "another venue", "rules-v1", brief)
        for i in range(2):
            self.mcp.search("serper", "new venue " + str(i), "US", "en", "run-b", "next-" + str(i))
        final = self.mcp.managed_call("serper.search", {"query": "one too many"}, "run-b", "last")
        self.assertEqual("BLOCKED", final["status"])
        self.assertEqual(3, len(self.calls))
        import research_lite_store as store
        self.assertTrue(store.read_artifact(self.root, "run-a", first["artifact_ids"][0]))
        self.assertEqual(2, runtime.config(self.root)["revision"])
        self.assertTrue(runtime._path(self.root, "runtime-consent-history.jsonl").is_file())

    def test_observed_account_floor_is_additional_and_never_rebaselined(self):
        runtime.observe_account(self.root, "serper", units=2, meter="credits",
                                source_url="https://serper.dev/dashboard", confirm=True)
        self.mcp.search("serper", "one venue", "US", "en", "run-a", "one")
        refused = self.mcp.managed_call("serper.search", {"query": "another venue"}, "run-a", "two")
        self.assertEqual("BLOCKED", refused["status"])
        self.assertEqual("BUDGET_PROVIDER_CEILING", refused["reason_code"])
        self.assertEqual(1, len(self.calls))
        with self.assertRaisesRegex(ValueError, "ACCOUNT_BASELINE_ALREADY_FROZEN"):
            runtime.observe_account(self.root, "serper", units=1, meter="credits",
                                    source_url="https://serper.dev/dashboard", confirm=True)


class EmbeddedSessionTests(unittest.TestCase):
    """Synthetic protocol service; only the lowest MCP connection is replaced."""
    def test_serpapi_empty_cache_discovers_once_on_one_event_loop(self):
        from research_adapter_serpapi import SerpApiAdapter
        from research_contract import CapabilityRequest
        from serpapi_engine_catalog import EngineCatalog, INDEX_URI, SCHEMA_URI_TEMPLATE
        from research_runtime_transport import serpapi_session, tools_payload
        from research_s0_preflight_gate import snapshot_digest
        loops, reads, searches = set(), [], []
        schema = {"type": "object", "properties": {"params": {"type": "object"},
                   "mode": {"type": "string"}}, "required": ["params"]}
        listed = SimpleNamespace(tools=[SimpleNamespace(name="search", description=None,
                                                        input_schema=schema)])
        class Session:
            async def list_tools(self):
                loops.add(id(asyncio.get_running_loop()))
                return listed
            async def list_resources(self):
                loops.add(id(asyncio.get_running_loop()))
                return SimpleNamespace(resources=[SimpleNamespace(uri=INDEX_URI, uriTemplate=None),
                    SimpleNamespace(uri=None, uriTemplate=SCHEMA_URI_TEMPLATE)])
            async def read_resource(self, uri):
                loops.add(id(asyncio.get_running_loop()))
                reads.append(str(uri))
                value = {"engines": ["google", "google_light"]} if str(uri) == INDEX_URI else {
                    "type": "object", "properties": {"q": {"type": "string"}}}
                return SimpleNamespace(contents=[SimpleNamespace(text=json.dumps(value),
                                                                  uri=uri)])
            async def call_tool(self, name, args):
                loops.add(id(asyncio.get_running_loop()))
                searches.append(args)
                return SimpleNamespace(isError=False, structuredContent={"organic_results": [
                    {"title": "Venue", "link": "https://example.org/venue"}]})
        @asynccontextmanager
        async def wire(*args, **kwargs):
            yield Session()
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            runtime._write(runtime._path(root, "serpapi-schema.json"), {
                "schema_sha256": snapshot_digest(tools_payload(listed))})
            adapter = SerpApiAdapter(session_factory=lambda: serpapi_session(root),
                                    catalog=EngineCatalog(root / "state" / "engines.json"))
            req = CapabilityRequest(capability_id="serpapi.search", run_id="run-a",
                operation_id="one", account_ref="serpapi", intent_fingerprint="search",
                params={"query": "public venues", "country": "US", "language": "en", "limit": 3})
            with patch("research_runtime_transport.mcp_session", wire):
                result = adapter.execute(req)
            self.assertEqual("OK", result.outcome, result)
            self.assertEqual(1, len(loops))
            self.assertEqual(2, len(reads))
            self.assertEqual(1, len(searches))
            self.assertEqual("compact", searches[0]["mode"])
            loops.clear()
            second = CapabilityRequest(capability_id="serpapi.search", run_id="run-a",
                operation_id="two", account_ref="serpapi", intent_fingerprint="search-two",
                params={"query": "new venues", "country": "US", "language": "en", "limit": 3})
            with patch("research_runtime_transport.mcp_session", wire):
                again = adapter.execute(second)
            self.assertEqual("OK", again.outcome, again)
            self.assertEqual(2, len(reads), "cached engine resources must not be re-read")
            self.assertEqual(2, len(searches))


class CrawlPrimaryMaterialTests(unittest.TestCase):
    def test_validated_html_survives_extraction_and_supports_literal_email_in_delivery(self):
        import research_adapter_crawl4ai as adapter
        import research_lite_store as store
        import research_delivery as delivery
        from test_wave1_crawl4ai_adapter import StubEgress
        from crawl4ai_fixtures import FakeCrawl4AI, FakeCrawlerResult, Request, limits
        html = "<html><body><h1>Public Venue</h1><p>Contact the venue at team@example.org</p></body></html>"
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            store.start_run(root, "crawl-run", "test", "venue", "rules")
            writes = []
            def writer(run_id, operation_id, url, body):
                writes.append(body)
                store.save_artifact(root, run_id, "primary-page", "crawl4ai", url, body, "rendered_html")
                return ("primary-page",)
            crawler = adapter.Crawl4AIAdapter(egress=StubEgress(), sdk_available=lambda: True,
                module_loader=lambda: FakeCrawl4AI(default=FakeCrawlerResult(html=html)),
                material_writer=writer)
            result = crawler.execute(Request("crawl4ai.fetch_page", {"url": "https://example.org/", "limits": limits()},
                                             run_id="crawl-run", operation_id="fetch"))
            self.assertEqual("OK", result.outcome, result)
            self.assertEqual([html.encode()], writes)
            self.assertEqual(("primary-page",), result.artifacts)
            self.assertNotIn("html", result.payload, "raw HTML belongs in the store, not the MCP payload")
            store.put_candidate(root, "crawl-run", "contact", "routes", "contact_point", "venue-email",
                {"type": "email", "value": "team@example.org", "claims": [
                    {"field": "email", "artifact_id": "primary-page", "quote": "Contact the venue at team@example.org"}]},
                ["primary-page"])
            sheets, report = delivery.build_delivery(root, "crawl-run")
            self.assertNotIn("HOLD_EMAIL_NOT_IN_PRIMARY_MATERIAL", str(sheets))
            self.assertIn("RENDERED_EMAIL_LITERAL_NOT_BUSINESS_VERIFIED", str(sheets))


class JevDefaultFactoryTests(unittest.TestCase):
    def test_saved_source_to_jev_returns_only_run_owned_artifact_ids(self):
        import research_mcp as mcp
        import research_lite_store as store
        from test_wave1_jev_adapter import FakeSDK
        with tempfile.TemporaryDirectory() as temp, patch.dict(os.environ, {
                "MARKETOPS_RESEARCH_DATA_ROOT": temp, "MARKETOPS_RESEARCH_ALLOW_NETWORK": "1",
                "TYPESAFE_API_KEY": "fixture-key"}):
            root = Path(temp)
            runtime.configure(root, providers=["typesafe_jev"], domains=["example.org"],
                              max_calls=2, task="judge saved evidence", confirm=True)
            brief = {"record_type": "research_brief", "target_kind": "venue", "goal": "pool",
                     "minimum_targets": None, "require_named_person": False}
            mcp.start("jev-run", "test", "judge saved material", "rules", brief)
            store.save_artifact(root, "jev-run", "source", "crawl4ai", "https://example.org/",
                b"Example Council operates Example Sports Ground. Contact its operator department.", "rendered_html")
            sdk = FakeSDK(answer="operator")
            with patch.dict(sys.modules, {"typesafe_sdk": sdk.build()}), \
                    patch.object(runtime, "verify_build_receipt", return_value={"ok": True}):
                answer = mcp.managed_call("jev.choice", {"artifact_id": "source",
                    "question": "Which role does Example Council have?", "options": ["operator", "other"]},
                    "jev-run", "judge")
            self.assertEqual("OK", answer["status"], answer)
            self.assertEqual(1, len(sdk.calls))
            self.assertIn("Example Council", sdk.calls[0]["state"]["source_excerpt"])
            for identifier in answer["artifact_ids"]:
                self.assertIsInstance(identifier, str)
                store.read_artifact(root, "jev-run", identifier)
            self.assertTrue(answer["payload"]["judgment_only"])


if __name__ == "__main__":
    unittest.main()
