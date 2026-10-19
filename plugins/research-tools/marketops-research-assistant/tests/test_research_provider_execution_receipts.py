"""Show real Lite API/SDK call path, auth mechanism and billing METER, never invent $.

All tests run with synthetic local Run and mocked providers; no HTTP request
is made. No vendor account or external official MCP is modified.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/"scripts"))
sys.path.insert(0,str(ROOT/"tests"))
import _s0_evidence_fixture as s0_fixture

import research_mcp as mcp
import research_provider_extensions as ext
import research_provider_policy as policy


BRIEF={"record_type":"research_brief","target_kind":"venue","goal":"pool",
       "require_named_person":False,"minimum_targets":None}


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


class RouteReceiptTests(unittest.TestCase):
    def setUp(self):
        tmp=tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root=Path(tmp.name)/"data"
        env=patch.dict(os.environ,{
            "MARKETOPS_RESEARCH_DATA_ROOT":str(self.root),
            "MARKETOPS_RESEARCH_ALLOW_NETWORK":"1",
            "MARKETOPS_RESEARCH_MAX_EXTERNAL_CALLS":"5",
            "MARKETOPS_FETCH_ALLOWED_DOMAINS":"public.example.org",
            "MARKETOPS_SEARCH_ALLOW_NETWORK":"0",
            "MARKETOPS_SEARCH_MAX_CALLS":"0",
            "SERPAPI_API_KEY":"", "SERPER_API_KEY":"",
            "CRAWL4AI_API_KEY":"", "TINYFISH_API_KEY":"",
            "TYPESAFE_API_KEY":"",
        })
        env.start()
        self.addCleanup(env.stop)
        mcp.start("route-run","generic region","venue research","unknown",BRIEF)
        s0_fixture.install(self.root)

    def test_lite_uses_mcp_outer_and_actual_vendor_rest_or_sdk_inner(self):
        scenarios={
            "serper":("REST_HTTPS","API_KEY_ENV","SEARCH_QUERY_OR_ACCOUNT_CREDIT"),
            "serpapi":("REST_HTTPS","API_KEY_ENV","SEARCH_QUERY_OR_ACCOUNT_CREDIT"),
            "crawl4ai_local":("LOCAL_PYTHON_SDK","LOCAL_POLICY_NO_PROVIDER_KEY",
                               "LOCAL_COMPUTE_AND_BROWSER"),
            "crawl4ai_cloud":("REST_HTTPS_CLOUD","API_KEY_ENV","CLOUD_PROVIDER_CREDITS"),
            "tinyfish_search":("REST_HTTPS","API_KEY_ENV","SEARCH_QUERY_ACCOUNT_RATE"),
            "tinyfish_fetch":("REST_HTTPS","API_KEY_ENV","FETCH_URL_ACCOUNT_RATE"),
            "jev_choice":("PYTHON_SDK_REMOTE_API","API_KEY_ENV","MODEL_PROVIDER_USAGE_UNKNOWN"),
        }
        for name,(transport,auth,meter) in scenarios.items():
            with self.subTest(provider=name):
                item=policy.invocation_receipt(name)
                self.assertEqual("MARKETOPS_MCP_STDIO_SURFACE",item["client_to_marketops"])
                self.assertEqual(transport,item["marketops_to_provider"])
                self.assertEqual(auth,item["authentication"]["method"])
                self.assertEqual(meter,item["billing"]["unit"])
                self.assertIsNone(item["billing"]["actual_amount"])
                self.assertFalse(item["billing"]["charge_verified"])
                self.assertNotIn("secret",str(item).lower())

    def test_external_official_mcp_not_falsely_treated_as_lite_usage(self):
        for name in ("tinyfish","serpapi","crawl4ai"):
            route=policy.official_mcp_route(name)
            self.assertEqual("OFFICIAL_PROVIDER_MCP_HOST_CONNECTION",
                             route["client_to_provider"])
            self.assertFalse(route["tracked_in_marketops_lite_attempt_ledger"])
            self.assertEqual("NOT_VERIFIED",route["connected"])
        for name in ("some-app","serper","typesafe_jev"):
            with self.assertRaisesRegex(ValueError,"UNKNOWN_OFFICIAL_MCP_PROVIDER"):
                policy.official_mcp_route(name)
        caps=mcp.capabilities()
        self.assertEqual(12,len(caps["lite_invocation_channels"]))
        self.assertEqual({"crawl4ai"},
                         set(caps["official_mcp_channels"]))
        self.assertEqual({"tinyfish","serpapi"},
                         set(caps["embedded_official_mcp"]))
        self.assertEqual("X_API_KEY_HTTP_HEADER",
                         caps["lite_invocation_channels"]["serper"]["credential_delivery"] )
        self.assertEqual("API_KEY_HTTPS_QUERY_PARAMETER",
                         caps["lite_invocation_channels"]["serpapi"]["credential_delivery"])
        self.assertEqual("EMBEDDED_TINYFISH_SERPAPI_MCP_CLIENT_SHIPPED_AUTH_NOT_VERIFIED",
                         caps["connection_state"])

    def test_real_marketops_search_wrapper_receipt_is_not_invoice(self):
        with patch.object(mcp.dual_search_mcp,"search",return_value={
            "status":"DISCOVERED_NOT_VERIFIED","results":[],
            "calls_reserved":1}) as method:
            result=mcp.search("serper","test example","SG","en",
                              "route-run","search1")
        method.assert_called_once()
        info=result["invocation"]
        self.assertEqual("serper",info["provider"])
        self.assertEqual("REST_HTTPS",info["marketops_to_provider"])
        self.assertEqual("ONE_ATTEMPT_RESERVED_NOT_BILL_VERIFIED",
                         info["billing"]["reservation"])
        self.assertIsNone(info["billing"]["actual_amount"])

    def test_local_crawler_receipt_and_event_show_sdk_not_cloud_api(self):
        fake={"url":"https://public.example.org/about",
              "markdown":"Public Example Museum operates this facility.",
              "_rendered_html":"<main>Public Example Museum operates this facility.</main>",
              "material_kind":"provider_extracted"}
        with patch.object(ext,"_crawl4ai_local",return_value=fake):
            with patch.object(ext.importlib.util,"find_spec",return_value=object()):
                result=mcp.invoke("crawl4ai_local","route-run","crawl1",
                                  url="https://public.example.org/about")
        self.assertEqual("SAVED_MATERIAL_NOT_VERIFIED",result["status"])
        self.assertEqual("LOCAL_PYTHON_SDK",
                         result["invocation"]["marketops_to_provider"])
        self.assertEqual("LOCAL_COMPUTE_AND_BROWSER",
                         result["invocation"]["billing"]["unit"])
        self.assertIsNone(result["invocation"]["billing"]["actual_amount"])
        report=mcp.provider_events("route-run")
        self.assertEqual("NOT_MONITORED_BY_LITE",report["external_official_mcp_activity"])
        latest=report["latest_lite_provider_call"]
        self.assertEqual("crawl4ai_local",latest["invocation"]["operation"])
        self.assertEqual("LOCAL_PYTHON_SDK",
                         latest["invocation"]["marketops_to_provider"])
        self.assertEqual("succeeded",latest["status"])
        self.assertFalse(latest["invocation"]["billing"]["charge_verified"])
        self.assertTrue(any("invocation_route" in e for e in report["events"]
                            if e.get("provider")=="crawl4ai_local"))

    def test_blocked_tool_response_retains_selected_meter_but_unknown_reservation(self):
        from mcp.server.mcpserver.exceptions import ToolError
        @mcp._guarded
        def marketops_tinyfish(run_id, operation_id, task="search"):
            raise ValueError("UPSTREAM_INSUFFICIENT_CREDITS")
        with self.assertRaises(ToolError) as err:
            marketops_tinyfish("route-run","declined",task="fetch")
        response=json.loads(str(err.exception))
        self.assertEqual("BLOCKED",response["status"])
        self.assertEqual("UPSTREAM_INSUFFICIENT_CREDITS",response["reason"])
        self.assertEqual("FETCH_URL_ACCOUNT_RATE",
                         response["invocation"]["billing"]["unit"])
        self.assertEqual("UNKNOWN_BLOCKED_BEFORE_OR_AFTER_PROVIDER_ATTEMPT",
                         response["invocation"]["billing"]["reservation"])
        self.assertIsNone(response["invocation"]["billing"]["actual_amount"])


if __name__=="__main__":
    unittest.main()
