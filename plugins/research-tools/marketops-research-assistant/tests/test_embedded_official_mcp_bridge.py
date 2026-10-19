"""Embedded TinyFish/SerpApi MCP client: offline protocol and true Lite writeback.

No real provider network, account, OAuth flow, browser, or paid tool runs.
"""
from __future__ import annotations

import asyncio
import os
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch

BASE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE / "scripts"))
sys.path.insert(0, str(BASE.parent / "tests"))
import _s0_evidence_fixture as s0_fixture
import research_official_mcp as official
import research_official_mcp_auth as oauth
import research_provider_extensions as ext
import research_mcp as mcp
import research_lite_store as store


BRIEF = {"record_type": "research_brief", "target_kind": "venue",
         "goal": "pool", "require_named_person": False, "minimum_targets": None}


# The shared ledger refuses an unpriced capability rather than estimating one
# (research_usage_ledger: "A price is recorded, never assumed"). These tests are
# about other behaviour, so they install an offline catalog that says so.
import sys as _sys
import pathlib as _pathlib

_sys.path.insert(0, str(_pathlib.Path(__file__).resolve().parent))
import wave1_pricing_fixture

wave1_pricing_fixture.install()


def event_result(data):
    return types.SimpleNamespace(structuredContent=data, isError=False, content=[])


class OfficialMCPUnitTests(unittest.TestCase):
    def test_urls_and_allowlisted_tools_are_vendor_owned(self):
        self.assertEqual("https://agent.tinyfish.ai/mcp", official.ENDPOINTS["tinyfish"])
        self.assertEqual("https://mcp.serpapi.com/mcp", official.ENDPOINTS["serpapi"])
        self.assertEqual({"tinyfish", "serpapi"}, set(official.TOOLS))
        with self.assertRaisesRegex(ValueError, "OFFICIAL_MCP_TOOL_NOT_ALLOWLISTED"):
            official._validate("tinyfish", "create_profile", {"profile_id": "fake"})
        with self.assertRaisesRegex(ValueError, "OFFICIAL_MCP_TOOL_NOT_ALLOWLISTED"):
            official._validate("serpapi", "run_web_automation", {})

    def test_input_schemas_and_no_secret_in_arguments(self):
        self.assertEqual("google_scholar", official._validate("serpapi", "search", {
            "params": {"engine": "google_scholar", "q": "climate policy"},
            "mode": "compact"})["params"]["engine"])
        self.assertEqual("google_flights", official._validate("serpapi", "search", {
            "params": {"engine": "google_flights", "departure_id": "SIN", "arrival_id": "LAX"},
            "mode": "compact"})["params"]["engine"])
        for bad in [
            {"params": {"engine": "../unsafe", "q": "hello"}, "mode": "compact"},
            {"params": {"engine": "google", "api_key": "do-not-pass-secret", "q": "hi"}},
            {"params": {"engine": "google", "q": "hi"}, "mode": "complete"},
        ]:
            with self.subTest(bad=repr(bad)[:60]):
                with self.assertRaises(ValueError):
                    official._validate("serpapi", "search", bad)
        with self.assertRaisesRegex(ValueError, "TINYFISH_MCP_FETCH_ARGS_INVALID"):
            official._validate("tinyfish", "fetch_content", {"urls": []})
        with self.assertRaisesRegex(ValueError, "TINYFISH_AGENT_HARD_CAP_REQUIRED"):
            official._validate("tinyfish", "run_web_automation",
                               {"url": "https://example.org", "goal": "Find public library information"})

    def test_response_parsing_does_not_invent_business_verification(self):
        out = official._extract(event_result({"result": '{"data":[{"title":"Library"}]}'}))
        self.assertEqual("Library", out["tool_result"]["data"][0]["title"])
        self.assertFalse(out["business_evidence_verified"])
        error = types.SimpleNamespace(structuredContent={"result":"error"},isError=True)
        with self.assertRaisesRegex(ValueError, "UPSTREAM_MCP_TOOL_FAILED"):
            official._extract(error)
        safe = official._extract(event_result({"wallet_url":"https://secret.invalid",
                     "refresh_token":"a", "results":[{"cookie":"a","title":"OK"}]}))
        self.assertEqual("[REDACTED]",safe["tool_result"]["wallet_url"])
        self.assertEqual("[REDACTED]",safe["tool_result"]["results"][0]["cookie"])

    def test_wallet_blocks_unknown_negative_and_over_cap(self):
        valid = {"available_balance": "1.00", "rates": {"meters": [
            {"label": "TinyFish Agent", "unit_amount": "0.016", "per": "step"}]}}
        official._wallet_guard(event_result(valid), 10, "0.20")
        for wallet, cap in [({**valid, "available_balance": "-1"}, "1.00"),
                            (valid,"0.01"),
                            ({"available_balance": "1", "rates": {"meters": []}}, "0.50")]:
            with self.subTest(wallet=wallet, cap=cap):
                with self.assertRaises(ValueError):
                    official._wallet_guard(event_result(wallet), 10, cap)
        schema = {"properties": {"agent_config": {"$ref": "#/$defs/AgentConfig"}},
                  "$defs": {"AgentConfig": {"properties": {
                      "max_steps": {"type": "integer"},
                      "max_duration_seconds": {"type": "integer"}}}}}
        self.assertTrue(official._supports_agent_bounds(schema))
        self.assertFalse(official._supports_agent_bounds({
            "properties": {"agent_config": {
                "properties": {"max_steps": {"type": "integer"}}}}
        }))

    def test_no_paid_task_without_explicit_ceiling_and_op_in(self):
        args={"url":"https://example.org","goal":"Find available public opening hours",
              "agent_config":{"max_steps":5,"max_duration_seconds":45}}
        with patch.dict(os.environ, {"TINYFISH_API_KEY":"not-a-real-key",
                                     "MARKETOPS_FETCH_ALLOWED_DOMAINS":"example.org",
                                     "MARKETOPS_TINYFISH_AGENT_ALLOW_PAID":"0"}):
            with self.assertRaisesRegex(ValueError, "TINYFISH_AGENT_PAID_NOT_AUTHORIZED"):
                official.call("tinyfish","run_web_automation",args)
        with patch.dict(os.environ, {"TINYFISH_API_KEY":"not-a-real-key",
                                     "MARKETOPS_FETCH_ALLOWED_DOMAINS":"example.org",
                                     "MARKETOPS_TINYFISH_AGENT_ALLOW_PAID":"1"}):
            with self.assertRaisesRegex(ValueError, "TINYFISH_AGENT_MAX_CHARGE_REQUIRED"):
                official.call("tinyfish","run_web_automation",args)

    def test_direct_embedded_client_has_its_own_url_allowlist(self):
        with patch.dict(os.environ, {
                "MARKETOPS_FETCH_ALLOWED_DOMAINS": "example.org"}):
            self.assertEqual("https://example.org/venues",
                             official._approved_url("https://example.org/venues"))
            official._validate("tinyfish", "fetch_content",
                               {"urls": ["https://example.org/venues"]})
            for url in ("https://127.0.0.1/admin",
                        "http://example.org/unsafe",
                        "https://private.example.net/",
                        "https://example.org.evil.test/"):
                with self.subTest(url=url):
                    with self.assertRaisesRegex(ValueError,
                                                "OFFICIAL_MCP_TARGET_NOT_ALLOWED"):
                        official._validate("tinyfish", "fetch_content",
                                           {"urls": [url]})
        with patch.dict(os.environ, {"MARKETOPS_FETCH_ALLOWED_DOMAINS": ""}):
            with self.assertRaisesRegex(ValueError, "OFFICIAL_MCP_TARGET_NOT_ALLOWED"):
                official._validate("tinyfish", "run_web_automation", {
                    "url":"https://example.org", "goal":"Read public opening hours",
                    "agent_config":{"max_steps":4,"max_duration_seconds":30}})

    def test_sync_bridge_in_running_event_loop_fails_precisely_without_new_task(self):
        called=[]
        async def fake_runner(provider, tool, args, **kwargs):
            called.append(tool)
            return {"unexpected":True}
        async def inside_loop():
            with patch.dict(os.environ, {"SERPAPI_API_KEY":"fake-test-value"}):
                with self.assertRaisesRegex(ValueError,
                                             "OFFICIAL_MCP_SYNC_CALL_INSIDE_RUNNING_LOOP"):
                    official.call("serpapi", "search",
                                  {"params":{"engine":"google_scholar","q":"libraries"},
                                   "mode":"compact"}, runner=fake_runner)
        asyncio.run(inside_loop())
        self.assertEqual([],called)

    def test_credential_requires_client_account_and_does_not_log_in(self):
        with patch.dict(os.environ, {"TINYFISH_API_KEY":"",
                                     "TINYFISH_MCP_ACCESS_TOKEN":"",
                                     "SERPAPI_API_KEY":""}):
            with patch.object(oauth,"has_saved_tokens",return_value=False):
                with self.assertRaisesRegex(ValueError, "OFFICIAL_MCP_CREDENTIAL_REQUIRED"):
                    official._credential("tinyfish")
                with self.assertRaisesRegex(ValueError, "OFFICIAL_MCP_CREDENTIAL_REQUIRED"):
                    official._credential("serpapi")
        self.assertNotIn("token", official.embedded.__name__ if hasattr(official,"embedded") else "safe")

    def test_real_client_session_protocol_flow_mocked_not_http_request(self):
        events=[]
        class FakeHTTP:
            def __init__(self, **kwargs):
                events.append(("http_cfg",kwargs["follow_redirects"]))
                events.append(("bearer_set",bool(kwargs["headers"].get("Authorization"))))
            async def __aenter__(self): return self
            async def __aexit__(self,*args): return False
        class Stream:
            async def __aenter__(self):
                events.append(("connect","streamable_http"))
                return ("read","write")
            async def __aexit__(self,*args): return False
        def connect(url, *, http_client):
            events.append(("url", url))
            return Stream()
        class Session:
            def __init__(self,read,write): pass
            async def __aenter__(self): return self
            async def __aexit__(self,*args): return False
            async def initialize(self): events.append(("method","initialize"))
            async def list_tools(self):
                events.append(("method","list_tools"))
                return types.SimpleNamespace(tools=[
                    types.SimpleNamespace(name="search",input_schema={"type":"object"})])
            async def call_tool(self,name,args):
                events.append(("tool",name))
                events.append(("engine",args["params"]["engine"]))
                return event_result({"result":'{"results":[{"title":"Public venue"}]}'})
        import mcp as sdk
        import mcp.client.streamable_http as mcp_http
        import httpx2
        with patch.dict(os.environ, {"SERPAPI_API_KEY":"fake-local-test-key"}), (
             patch.object(sdk, "ClientSession", Session)), (
             patch.object(mcp_http, "streamable_http_client", connect)), (
             patch.object(httpx2, "AsyncClient", FakeHTTP)):
            result=asyncio.run(official._live_call("serpapi", "search", {
                "params":{"engine":"google_scholar", "q":"libraries"},
                "mode":"compact"}))
        self.assertEqual("Public venue", result["tool_result"]["results"][0]["title"])
        self.assertEqual(["initialize","list_tools"],
                         [v for k,v in events if k=="method"])
        self.assertEqual(["search"],[v for k,v in events if k=="tool"])
        self.assertEqual(["google_scholar"],[v for k,v in events if k=="engine"])
        self.assertEqual(["https://mcp.serpapi.com/mcp"],
                         [v for k,v in events if k=="url"])
        self.assertIn(("http_cfg",False),events)
        self.assertFalse(result["provider_charge_verified"])


class LiteBridgeWritebackTests(unittest.TestCase):
    def setUp(self):
        tmp=tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root=Path(tmp.name)/"research"
        env=patch.dict(os.environ,{
            "HOME":str(Path(tmp.name)/"home"),
            "XDG_CONFIG_HOME":str(Path(tmp.name)/"xdg"),
            "MARKETOPS_RESEARCH_DATA_ROOT":str(self.root),
            "MARKETOPS_RESEARCH_ALLOW_NETWORK":"1",
            "MARKETOPS_RESEARCH_MAX_EXTERNAL_CALLS":"4",
            "MARKETOPS_FETCH_ALLOWED_DOMAINS":"example.org",
            "MARKETOPS_TINYFISH_AGENT_ALLOW_PAID":"0",
            "TINYFISH_MCP_ACCESS_TOKEN":"",
            "TINYFISH_API_KEY":"fake-tinyfish-test-token",
            "SERPAPI_API_KEY":"fake-serpapi-test-token",
        })
        env.start()
        self.addCleanup(env.stop)
        mcp.start("embedded-run","Singapore","Public venue candidates","experimental",BRIEF)
        # This class is about what happens AFTER the deployment gate: the
        # paid-agent opt-in, the receipt shape, the writeback. Without an
        # S0-passing deployment the new outer gate would refuse first and
        # these guards would never be reached.
        s0_fixture.install(self.root)

    def test_official_mcp_search_and_fetch_are_stored_under_same_run(self):
        calls=[]
        def fake(provider,tool,arguments,**kwargs):
            calls.append((provider,tool))
            return {"provider":provider,"tool_result":{"matches":[{"title":"Public Library",
                        "url":"https://example.org/library"}]},
                    "transport":"OFFICIAL_STREAMABLE_HTTP_MCP",
                    "provider_charge_verified":False}
        with patch.object(ext.official_mcp,"call",side_effect=fake):
            found=mcp.invoke("tinyfish_mcp_search","embedded-run","tf1",query="public library")
            fetched=mcp.invoke("tinyfish_mcp_fetch","embedded-run","tf2",
                              url="https://example.org/library")
            multi=mcp.invoke("serpapi_mcp_search","embedded-run","sp1",
                             mcp_params={"engine":"google_scholar","q":"public libraries"})
        self.assertEqual([("tinyfish","search"),("tinyfish","fetch_content"),
                          ("serpapi","search")],calls)
        for result in (found,fetched,multi):
            self.assertEqual("SAVED_MATERIAL_NOT_VERIFIED",result["status"])
            self.assertEqual("EMBEDDED_OFFICIAL_MCP_HTTP",
                             result["invocation"]["marketops_to_provider"])
            self.assertIsNone(result["invocation"]["billing"]["actual_amount"])
            self.assertFalse(result["invocation"]["billing"]["charge_verified"])
            self.assertEqual("SAVED_NOT_BUSINESS_VERIFIED",
                             result["artifact"]["storage_status"])
        snapshot=store.list_run(self.root,"embedded-run",limit=50)
        self.assertEqual(4,snapshot["record_count"])  # brief + three source receipts
        events=mcp.provider_events("embedded-run")
        self.assertEqual("serpapi_mcp_search",events["latest_lite_provider_call"]["invocation"]["operation"])
        # A same-query renamed-ID retry still refuses before fake provider call.
        with patch.object(ext.official_mcp,"call") as outbound:
            with self.assertRaisesRegex(ValueError,"DUPLICATE_RESEARCH_ATTEMPT"):
                mcp.invoke("tinyfish_mcp_search","embedded-run","tf3",query="public library")
            outbound.assert_not_called()

    def test_paid_agent_is_refused_before_any_reservation(self):
        with patch.object(ext.official_mcp,"call") as outbound:
            with self.assertRaisesRegex(ValueError,"TINYFISH_AGENT_PAID_NOT_AUTHORIZED"):
                mcp.invoke("tinyfish_mcp_agent","embedded-run","agent1",
                          url="https://example.org/library",
                          question="Find opening hours without submissions",
                          mcp_params={"max_steps":6,"max_duration_seconds":50},
                          paid_limit_usd="0.50")
            outbound.assert_not_called()
        usage=mcp.capabilities()["budgets"]["provider"]
        self.assertEqual(0,usage["reserved"])

    def test_external_mcp_oauth_has_no_implicit_login(self):
        state=mcp.capabilities()["embedded_official_mcp"]
        self.assertEqual("IN_PROCESS_OFFICIAL_STREAMABLE_HTTP_CLIENT",
                         state["tinyfish"]["mode"])
        self.assertFalse(state["tinyfish"]["auth_verified"])
        self.assertFalse(state["serpapi"]["quota_verified"])


if __name__=="__main__":
    unittest.main()
