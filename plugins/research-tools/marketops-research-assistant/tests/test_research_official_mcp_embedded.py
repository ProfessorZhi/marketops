"""Offline real MCP-client bridge contract. No account, network or paid call."""
from __future__ import annotations
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT.parent / "tests"))
import _s0_evidence_fixture as s0_fixture
import research_official_mcp as official
import research_provider_extensions as extra
import research_mcp as mcp

BRIEF = {"record_type": "research_brief", "target_kind": "venue",
         "goal": "pool", "require_named_person": False, "minimum_targets": None}


# --- Wave-1 integration fixtures -------------------------------------------
# Two contract changes now sit in front of every outbound call, and neither is
# what these unit tests are about: an unbound local browser is refused
# (INTERFACE-CONTRACT IA-4) and a synthetic documentation name cannot resolve,
# which the SSRF guard treats as a refusal rather than as permission
# (research_target_guard). The adversarial matrix in tests/qa_gate owns both
# questions; here they are supplied so the behaviour under test stays reachable.
# The shared ledger refuses an unpriced capability rather than estimating one
# (research_usage_ledger: "A price is recorded, never assumed"). These tests are
# about other behaviour, so they install an offline catalog that says so.
import sys as _sys
import pathlib as _pathlib

_sys.path.insert(0, str(_pathlib.Path(__file__).resolve().parent))
import wave1_grant_fixture
import wave1_pricing_fixture

wave1_pricing_fixture.install()


class _BoundEgress:
    """A bound egress: enough for BrowserConfig(**kwargs) without a browser."""

    def browser_config_kwargs(self):
        return {"proxy_config": {"server": "http://127.0.0.1:1"},
                "extra_args": ["--disable-quic"]}


def _stub_resolution():
    import research_target_guard as _guard

    _guard.resolve_checked = lambda hostname, port=443: ("203.0.113.10",)


_stub_resolution()


class EmbeddedMCPTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / "data"
        env = patch.dict(os.environ, {
            "MARKETOPS_RESEARCH_DATA_ROOT": str(self.root),
            "MARKETOPS_RESEARCH_ALLOW_NETWORK": "1",
            "MARKETOPS_RESEARCH_MAX_EXTERNAL_CALLS": "10",
            "MARKETOPS_FETCH_ALLOWED_DOMAINS": "public.example.org",
            "MARKETOPS_TINYFISH_AGENT_ALLOW_PAID": "0",
            "TINYFISH_MCP_ACCESS_TOKEN": "",
            "TINYFISH_API_KEY": "synthetic-tinyfish-example-credential",
            "SERPAPI_API_KEY": "synthetic-serpapi-example-credential",
        })
        env.start()
        self.addCleanup(env.stop)
        mcp.start("embedded-run","example","test official clients","unverified",BRIEF)
        s0_fixture.install(self.root)

    def test_endpoints_are_actual_official_mcp_and_no_arbitrary_url(self):
        self.assertEqual("https://agent.tinyfish.ai/mcp",official.ENDPOINTS["tinyfish"])
        self.assertEqual("https://mcp.serpapi.com/mcp",official.ENDPOINTS["serpapi"])
        self.assertEqual("TINYFISH_API_KEY",official._credential("tinyfish")[0])
        self.assertEqual("SERPAPI_API_KEY",official._credential("serpapi")[0])
        with self.assertRaisesRegex(ValueError,"OFFICIAL_MCP_PROVIDER_UNSUPPORTED"):
            official._credential("untrusted.example.org")

    def test_reject_unsafe_or_paid_arguments_without_consuming_a_provider_call(self):
        bad=[
            ("tinyfish","search",{"query":"a","access_token":"secret"}),
            ("tinyfish","fetch_content",{"urls":["https://public.example.org/p"],
                                         "use_vault":True}),
            ("serpapi","search",{"params":{"engine":"google","q":"museum",
                                           "api_key":"must-not-log"}}),
            ("serpapi","search",{"params":{"engine":"garbage/engine","q":"museum"}}),
            ("tinyfish","run_web_automation",{"url":"https://public.example.org/p",
                          "goal":"extract public records","agent_config":{"max_steps":151,
                                                      "max_duration_seconds":100}}),
        ]
        for provider, tool, args in bad:
            with self.subTest(provider=provider,tool=tool):
                with self.assertRaises(ValueError):
                    official._validate(provider,tool,args)
        with self.assertRaisesRegex(ValueError,"TINYFISH_AGENT_PAID_NOT_AUTHORIZED"):
            official.call("tinyfish","run_web_automation",{
                "url":"https://public.example.org/p","goal":"Read a public page safely",
                "agent_config":{"max_steps":3,"max_duration_seconds":30}})
        self.assertFalse((self.root / "state" / "provider-reservations.sqlite3").exists())

    def test_mocked_but_real_marketops_provider_adapter_uses_mcp_and_saves_artifact(self):
        response={"tool_result":{"title":"Official Public Venue",
                                 "url":"https://public.example.org/p",
                                 "summary":"A public venue sample"},
                  "provider":"tinyfish", "mcp_tool":"search",
                  "transport":"OFFICIAL_STREAMABLE_HTTP_MCP",
                  "auth_mode":"PROVIDER_BEARER_KEY",
                  "actual_charge_usd":None,"provider_charge_verified":False}
        with patch.object(extra.official_mcp,"call",return_value=response) as fn:
            item=mcp.invoke("tinyfish_mcp_search","embedded-run","mcp-s1",
                            query="public venue")
        fn.assert_called_once()
        self.assertEqual("SAVED_MATERIAL_NOT_VERIFIED",item["status"])
        self.assertEqual("EMBEDDED_OFFICIAL_MCP_HTTP",
                         item["invocation"]["marketops_to_provider"])
        self.assertFalse(item["invocation"]["billing"]["charge_verified"])
        self.assertEqual("SAVED_NOT_BUSINESS_VERIFIED",item["artifact"]["storage_status"])
        self.assertEqual("tinyfish_mcp_search",item["provider"])

    def test_serpapi_generic_engine_is_validated_and_preserved_as_material(self):
        payload={"params":{"engine":"google_scholar","q":"public venue policy",
                           "num":3},"mode":"compact"}
        official._validate("serpapi","search",payload)
        response={"tool_result":{"organic_results":[
                    {"title":"A policy","link":"https://public.example.org/policy"}]},
                  "provider":"serpapi","mcp_tool":"search",
                  "transport":"OFFICIAL_STREAMABLE_HTTP_MCP"}
        with patch.object(extra.official_mcp,"call",return_value=response) as fn:
            result=mcp.invoke("serpapi_mcp_search","embedded-run","mcp-sa1",
                              mcp_params=payload["params"])
        self.assertEqual("SAVED_MATERIAL_NOT_VERIFIED",result["status"])
        self.assertEqual(payload["params"],fn.call_args.args[2]["params"])

    def test_paid_agent_submission_requires_optin_and_wallet_capped_by_provider(self):
        args={"url":"https://public.example.org/p","goal":"Review the published website",
              "agent_config":{"max_steps":4,"max_duration_seconds":45}}
        with self.assertRaisesRegex(ValueError,"TINYFISH_AGENT_PAID_NOT_AUTHORIZED"):
            mcp.invoke("tinyfish_mcp_agent","embedded-run","agent1",
                       url=args["url"],question=args["goal"],
                       mcp_params=args["agent_config"],paid_limit_usd="0.1")
        self.assertFalse((self.root / "state" / "provider-reservations.sqlite3").exists())
        # The ledger closes T3 unless a grant verifies against an out-of-band key;
        # this test is about the code *behind* that gate, so it presents one.
        grant = wave1_grant_fixture.mint_for(
            self.root, run_id="embedded-run", provider="tinyfish",
            capability="agent_run", account_ref="tinyfish")
        with patch.dict(os.environ,{"MARKETOPS_TINYFISH_AGENT_ALLOW_PAID":"1"}):
            with patch.object(extra.official_mcp,"call",return_value={
                      "tool_result":{"runId":"run-demo-id"},"agent_run_id":"run-demo-id",
                      "provider":"tinyfish","mcp_tool":"run_web_automation"}):
                result=mcp.invoke("tinyfish_mcp_agent","embedded-run","agent2",
                                  url=args["url"],question=args["goal"],
                                  mcp_params=args["agent_config"],paid_limit_usd="0.10",
                                  ledger_grant=grant)
            self.assertEqual("MCP_AGENT_SUBMITTED_NOT_TERMINAL",result["status"])
            self.assertEqual("run-demo-id",
                             json.loads(Path(self.root / "artifacts" / "embedded-run" /
                                  (extra.store._safe_name(result["artifact"]["artifact_id"])+".bin")
                             ).read_text(encoding="utf-8"))["agent_run_id"])
            self.assertTrue(extra._owns_submitted_agent_run(
                self.root,"embedded-run","agent2","run-demo-id"))
            with patch.object(extra.official_mcp,"call",return_value={
                    "tool_result":{"status":"running"},
                    "agent_lifecycle":"running",
                    "provider":"tinyfish","mcp_tool":"wait_for_run"}) as q:
                output=mcp.invoke("tinyfish_mcp_wait","embedded-run","poll-1",
                                  mcp_params={"agent_run_id":"run-demo-id",
                                              "origin_operation_id":"agent2","poll_index":1})
            self.assertEqual("MCP_AGENT_RUNNING_NOT_BUSINESS_VERIFIED",output["status"])
            # W-C measured the vendor's catalog: the run-status tool is
            # `get_run(id)`, not `wait_for_run(run_id)`. This assertion used to
            # pin the bridge's own invented verb; patch 1 corrected the bridge,
            # so the expectation moves with it.
            q.assert_called_once_with("tinyfish","get_run",{"id":"run-demo-id"})
        with self.assertRaisesRegex(ValueError,"TINYFISH_AGENT_NOT_OWNED_BY_THIS_RESEARCH_RUN"):
            mcp.invoke("tinyfish_mcp_wait","embedded-run","poll-2",
                       mcp_params={"agent_run_id":"someone-elses-run",
                                   "origin_operation_id":"agent2","poll_index":2})

    def test_serpapi_multi_engine_flight_params_do_not_need_google_q(self):
        flight = {"params": {"engine": "google_flights", "departure_id": "SIN",
                              "arrival_id": "NRT", "outbound_date": "2026-11-01"},
                  "mode": "compact"}
        self.assertEqual(flight, official._validate("serpapi", "search", flight))
        with self.assertRaisesRegex(ValueError, "SERPAPI_MCP_ENGINE_INPUT_REQUIRED"):
            official._validate("serpapi", "search",
                               {"params": {"engine": "google_flights"}, "mode": "compact"})

    def test_agent_lifecycle_uses_actual_provider_status(self):
        cases = [("pending", "running"), ("running", "running"),
                 ("completed", "completed"), ("failed", "failed"),
                 ("cancelled", "failed"), ("some_new_status", "unknown")]
        for raw, expected in cases:
            self.assertEqual(expected, official._agent_status(
                {"tool_result": {"status": raw}}))
        self.assertEqual("unknown", official._agent_status(
            {"tool_result": "unstructured reply"}))

    def test_invalid_agent_usd_ceiling_refused_before_attempt_reservation(self):
        with patch.dict(os.environ, {"MARKETOPS_TINYFISH_AGENT_ALLOW_PAID": "1"}):
            with self.assertRaisesRegex(ValueError, "TINYFISH_AGENT_MAX_CHARGE_INVALID"):
                mcp.invoke("tinyfish_mcp_agent", "embedded-run", "invalid-money",
                           url="https://public.example.org/p",
                           question="Review a public website carefully",
                           mcp_params={"max_steps": 4, "max_duration_seconds": 40},
                           paid_limit_usd="NaN")
        self.assertFalse((self.root / "state" / "provider-reservations.sqlite3").exists())

    def test_wallet_and_response_parser_never_treat_errors_as_success(self):
        class Resp:
            isError=False
            structuredContent={"result":json.dumps({"organic_results":[{"title":"X"}]})}
        p=official._extract(Resp())
        self.assertEqual("X",p["tool_result"]["organic_results"][0]["title"])
        class Err:
            isError=True
            structuredContent={"result":"token-secret"}
        with self.assertRaisesRegex(ValueError,"UPSTREAM_MCP_TOOL_FAILED"):
            official._extract(Err())
        wallet=type("R",(),{"isError":False,"structuredContent":{
             "result":json.dumps({"available_balance":"1.00",
                                   "rates":{"meters":[{"label":"TinyFish Agent",
                                                       "unit_amount":"0.016"}]}})}})()
        official._wallet_guard(wallet,4,"0.10")
        with self.assertRaisesRegex(ValueError,"TINYFISH_AGENT_SPEND_CAP_EXCEEDED"):
            official._wallet_guard(wallet,40,"0.10")

    def test_client_mapping_is_exposed_not_claimed_connected(self):
        caps=mcp.capabilities()
        embedded=caps["embedded_official_mcp"]
        self.assertEqual({"tinyfish","serpapi"},set(embedded))
        self.assertEqual("IN_PROCESS_OFFICIAL_STREAMABLE_HTTP_CLIENT",
                         embedded["tinyfish"]["mode"])
        self.assertFalse(embedded["serpapi"]["auth_verified"])
        self.assertFalse(embedded["tinyfish"]["quota_verified"])
        tools=mcp.build_server()._tool_manager._tools
        self.assertIn("marketops_serpapi_mcp_search",tools)
        self.assertIn("marketops_tinyfish_agent",tools)
        self.assertIn("marketops_tinyfish_agent_wait",tools)
        self.assertEqual(25,len(tools))


if __name__=="__main__":
    unittest.main()
