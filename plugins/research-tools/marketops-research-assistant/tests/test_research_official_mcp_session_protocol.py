"""Exercise the real embedded MCP ClientSession handshake with synthetic transport.

The official HTTPS URL is asserted, but NO socket, account, wallet or paid
provider operation occurs. Proves MarketOps invokes MCP protocol, not REST.
"""
from __future__ import annotations

import contextlib
import importlib.util
import json
import os
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/"scripts"))
import research_official_mcp as bridge


# --- Wave-1 integration fixtures -------------------------------------------
# Two contract changes now sit in front of every outbound call, and neither is
# what these unit tests are about: an unbound local browser is refused
# (INTERFACE-CONTRACT IA-4) and a synthetic documentation name cannot resolve,
# which the SSRF guard treats as a refusal rather than as permission
# (research_target_guard). The adversarial matrix in tests/qa_gate owns both
# questions; here they are supplied so the behaviour under test stays reachable.
class _BoundEgress:
    """A bound egress: enough for BrowserConfig(**kwargs) without a browser."""

    def browser_config_kwargs(self):
        return {"proxy_config": {"server": "http://127.0.0.1:1"},
                "extra_args": ["--disable-quic"]}


_REAL_RESOLVE_CHECKED = None


def setUpModule(module=None):
    """Stub name resolution for *this module only*.

    The previous form assigned at import time, which permanently replaced the
    real resolver for the whole pytest process: every later module that
    exercises the guard saw a lambda answering a public address, so a missing
    guard and a working one became indistinguishable. Stub on entry, restore on
    exit, and leave the process as it was found.
    """
    global _REAL_RESOLVE_CHECKED
    import research_target_guard as _guard

    _REAL_RESOLVE_CHECKED = _guard.resolve_checked
    _guard.resolve_checked = lambda hostname, port=443: ("203.0.113.10",)


def tearDownModule(module=None):
    import research_target_guard as _guard

    if _REAL_RESOLVE_CHECKED is not None:
        _guard.resolve_checked = _REAL_RESOLVE_CHECKED


class FakeHTTP:
    observations=[]
    def __init__(self, *, headers=None, auth=None, follow_redirects=None, timeout=None):
        FakeHTTP.observations.append({"headers":dict(headers or {}),
            "auth":auth, "redirects":follow_redirects})
    async def __aenter__(self): return self
    async def __aexit__(self,*args): return False


class FakeSession:
    names=("search","fetch_content","run_web_automation","get_wallet","wait_for_run")
    calls=[]
    initializations=0
    def __init__(self,*args): pass
    async def __aenter__(self): return self
    async def __aexit__(self,*args): return False
    async def initialize(self): FakeSession.initializations += 1
    async def list_tools(self):
        return types.SimpleNamespace(tools=[
            types.SimpleNamespace(name=n,input_schema={
                "properties":{"agent_config":{"properties":{
                    "max_steps":{"type":"integer"},
                    "max_duration_seconds":{"type":"integer"}}}}})
            for n in self.names])
    async def call_tool(self,name,arguments):
        FakeSession.calls.append((name,arguments))
        if name=="get_wallet":
            data={"available_balance":"1","rates":{"meters":[
                {"label":"TinyFish Agent","unit_amount":"0.016"}]}}
        elif name=="run_web_automation":
            data={"runId":"run-synthetic-mcp-123","status":"pending"}
        elif name=="wait_for_run":
            data={"status":"running"}
        else:
            data={"results":[{"title":"Sample public listing",
                              "url":"https://public.example.org"}]}
        return types.SimpleNamespace(isError=False,
            structuredContent={"result":json.dumps(data)})


@contextlib.asynccontextmanager
async def fake_transport(url,*,http_client):
    assert url in bridge.ENDPOINTS.values()
    assert isinstance(http_client, FakeHTTP)
    yield (object(),object())


@unittest.skipUnless(importlib.util.find_spec("mcp") is not None,
                     "MCP SDK not installed in this bare interpreter")
def _t3_grant():
    """The owner-side authorization these tests assume is already in place."""
    from datetime import datetime, timedelta, timezone

    from research_tier_authorization import TierGrant

    now = datetime.now(timezone.utc)
    return TierGrant(factor="OS_ISOLATED_BROKER_GRANT", grant_ref="test-fixture",
                     issued_by="test", tier="T3", signature="test-fixture-signature",
                     issued_at=now, expires_at=now + timedelta(hours=1))


class EmbeddedSessionProtocolTests(unittest.TestCase):
    def setUp(self):
        FakeHTTP.observations=[]
        FakeSession.calls=[]
        FakeSession.initializations=0
        env=patch.dict(os.environ,{
            "SERPAPI_API_KEY":"synthetic-serpapi-key-not-for-network",
            "TINYFISH_API_KEY":"synthetic-tinyfish-key-not-for-network",
            "TINYFISH_MCP_ACCESS_TOKEN":"",
            "MARKETOPS_FETCH_ALLOWED_DOMAINS":"public.example.org",
            "MARKETOPS_TINYFISH_AGENT_ALLOW_PAID":"0"})
        env.start()
        self.addCleanup(env.stop)

    @contextlib.contextmanager
    def fake_sdk(self):
        import mcp
        import mcp.client.streamable_http as stream
        import httpx2
        with patch.object(mcp,"ClientSession",FakeSession):
            with patch.object(stream,"streamable_http_client",fake_transport):
                with patch.object(httpx2,"AsyncClient",FakeHTTP):
                    yield

    def test_real_session_handshake_and_tinyfish_search_tool_call(self):
        with self.fake_sdk():
            out=bridge.call("tinyfish","search",{"query":"public venue details"})
        self.assertEqual(1,FakeSession.initializations)
        self.assertEqual([("search",{"query":"public venue details"})],
                         FakeSession.calls)
        self.assertEqual("OFFICIAL_STREAMABLE_HTTP_MCP",out["transport"])
        self.assertFalse(out["provider_charge_verified"])
        self.assertNotIn("synthetic-tinyfish-key",json.dumps(out))
        self.assertFalse(FakeHTTP.observations[0]["redirects"])
        self.assertEqual("Bearer synthetic-tinyfish-key-not-for-network",
                         FakeHTTP.observations[0]["headers"]["Authorization"])

    def test_serpapi_official_multi_engine_without_google_query(self):
        data={"params":{"engine":"google_flights","departure_id":"SIN",
                        "arrival_id":"NRT","outbound_date":"2026-11-01"},
              "mode":"compact"}
        with self.fake_sdk():
            out=bridge.call("serpapi","search",data)
        self.assertEqual("serpapi",out["provider"])
        self.assertEqual([("search",data)],FakeSession.calls)
        self.assertEqual(1,FakeSession.initializations)
        self.assertNotIn("synthetic-serpapi-key",json.dumps(out))

    def test_paid_agent_requires_wallet_guard_and_returns_original_run_id(self):
        args={"url":"https://public.example.org/venues",
              "goal":"Read a publicly accessible venue listing",
              "agent_config":{"max_steps":4,"max_duration_seconds":60}}
        with patch.dict(os.environ,{"MARKETOPS_TINYFISH_AGENT_ALLOW_PAID":"1"}):
            with self.fake_sdk():
                out=bridge.call("tinyfish","run_web_automation",args,
                                max_paid_usd="0.10", tier_grant=_t3_grant())
        self.assertEqual(["get_wallet","run_web_automation"],
                         [c[0] for c in FakeSession.calls])
        self.assertEqual("run-synthetic-mcp-123",out["agent_run_id"])
        self.assertEqual("submitted_not_terminal",out["agent_lifecycle"])
        self.assertIsNone(out["actual_charge_usd"])

    def test_taskgroup_wrap_preserves_only_recognized_safe_error_codes(self):
        async def known(_provider,_tool,_args,*,max_paid_usd=""):
            raise ExceptionGroup("internal task group", [
                ValueError("TINYFISH_AGENT_WALLET_TOOL_REQUIRED")])
        with self.assertRaisesRegex(ValueError,"TINYFISH_AGENT_WALLET_TOOL_REQUIRED"):
            bridge.call("tinyfish","search",{"query":"public libraries"},
                        runner=known)

        async def unknown(_provider,_tool,_args,*,max_paid_usd=""):
            raise ExceptionGroup("untrusted", [
                ValueError("secret-token-must-not-echo")])
        with self.assertRaises(ValueError) as err:
            bridge.call("tinyfish","search",{"query":"public libraries"},
                        runner=unknown)
        self.assertEqual("UPSTREAM_MCP_TRANSPORT_OR_SESSION_FAILED",str(err.exception))
        self.assertNotIn("secret-token",str(err.exception))

    def test_agent_pydantic_definitions_references_are_resolved_safely(self):
        definitions = {"properties":{"max_steps":{"type":"integer"},
                                     "max_duration_seconds":{"type":"integer"}}}
        schema = {
            "$defs":{"AgentConfig":definitions},
            "properties":{"agent_config":{"anyOf":[
                {"$ref":"#/$defs/AgentConfig"},{"type":"null"}]}}
        }
        self.assertTrue(bridge._supports_agent_bounds(schema))
        self.assertTrue(bridge._supports_agent_bounds({
            "properties":{"agent_config":definitions}}))
        self.assertFalse(bridge._supports_agent_bounds({
            "properties":{"agent_config":{"$ref":"https://evil.example/schema"}}}))
        self.assertFalse(bridge._supports_agent_bounds({
            "properties":{"agent_config":{"$ref":"#/$defs/Missing"}}}))
        self.assertFalse(bridge._supports_agent_bounds({
            "$defs":{"AgentConfig":{"properties":{"max_steps":{"type":"integer"}}}},
            "properties":{"agent_config":{"$ref":"#/$defs/AgentConfig"}}}))

    def test_no_wallet_means_no_agent_submission(self):
        args={"url":"https://public.example.org/venues",
              "goal":"Read a publicly accessible venue listing",
              "agent_config":{"max_steps":4,"max_duration_seconds":60}}
        FakeSession.names=("search","run_web_automation")
        self.addCleanup(setattr,FakeSession,"names",(
            "search","fetch_content","run_web_automation","get_wallet","wait_for_run"))
        with patch.dict(os.environ,{"MARKETOPS_TINYFISH_AGENT_ALLOW_PAID":"1"}):
            with self.fake_sdk():
                with self.assertRaisesRegex(ValueError,
                                            "TINYFISH_AGENT_WALLET_TOOL_REQUIRED"):
                    bridge.call("tinyfish","run_web_automation",args,
                                max_paid_usd="0.10", tier_grant=_t3_grant())
        self.assertEqual([],FakeSession.calls)


if __name__=="__main__":
    unittest.main()
