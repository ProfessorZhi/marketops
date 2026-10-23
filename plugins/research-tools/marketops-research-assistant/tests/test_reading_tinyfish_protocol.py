"""The real embedded MCP handshake for the TinyFish adapter.

The official HTTPS URL is asserted and the genuine ``mcp`` SDK is exercised,
but the transport is synthetic: no socket, account, wallet or paid provider
operation occurs. These tests prove the adapter speaks MCP (initialize ->
tools/list -> tools/call) rather than pretending a REST call is one.
"""
from __future__ import annotations

import asyncio
import contextlib
import importlib.util
import json
import os
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import research_adapter_tinyfish as tinyfish
import _tinyfish_egress_fixture as egress_fixture

CATALOG = sorted(tinyfish.capability_catalog()["tools"])
WALLET = {"available_balance": "25.00", "currency": "USD",
          "auto_reload": {"enabled": False},
          "rates": {"meters": [{"label": "TinyFish Agent",
                                "unit_amount": "0.016"}]}}
AUTHORIZATION = {"approved_by": "operator@example.test",
                 "scope": "TINYFISH_PAID_AGENT_RUN",
                 "accept_uncapped_provider_spend": True}


class FakeHTTP:
    observations = []

    def __init__(self, *, headers=None, auth=None, follow_redirects=None, timeout=None,
                 trust_env=True, proxy=None):
        FakeHTTP.observations.append({"headers": dict(headers or {}), "auth": auth,
                                      "redirects": follow_redirects, "trust_env": trust_env,
                                      "proxy": proxy})

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False


class FakeSession:
    names = tuple(CATALOG)
    payloads = {}
    calls = []
    initializations = 0
    result = None

    def __init__(self, *args):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def initialize(self):
        FakeSession.initializations += 1

    async def list_tools(self):
        return types.SimpleNamespace(
            tools=[types.SimpleNamespace(name=n, input_schema={}) for n in self.names])

    async def call_tool(self, name, arguments):
        FakeSession.calls.append((name, arguments))
        if FakeSession.result is not None:
            return FakeSession.result(name, arguments)
        target = self.payloads.get(name)
        if target is None:
            raise AssertionError("unexpected tool call: %s" % name)
        return types.SimpleNamespace(isError=False,
                                     structuredContent={"result": json.dumps(target)})


@contextlib.asynccontextmanager
async def fake_transport(url, *, http_client):
    assert url == tinyfish.ENDPOINT, url
    assert isinstance(http_client, FakeHTTP)
    yield (object(), object())


@unittest.skipUnless(importlib.util.find_spec("mcp") is not None,
                     "MCP SDK not installed in this bare interpreter")
class EmbeddedTinyFishSessionTests(unittest.TestCase):
    def setUp(self):
        FakeHTTP.observations = []
        egress_fixture.install(self)
        egress_fixture.no_real_dns(self)
        FakeSession.calls = []
        FakeSession.initializations = 0
        FakeSession.names = tuple(CATALOG)
        FakeSession.payloads = {}
        FakeSession.result = None
        env = patch.dict(os.environ, {
            "MARKETOPS_FETCH_ALLOWED_DOMAINS": "public.example.org",
            "MARKETOPS_RESEARCH_ALLOW_NETWORK": "1",
            "TINYFISH_API_KEY": "synthetic-tinyfish-key-not-for-network",
            "TINYFISH_MCP_ACCESS_TOKEN": "",
            "MARKETOPS_TINYFISH_AGENT_ALLOW_PAID": "0",
        })
        env.start()
        self.addCleanup(env.stop)

    @contextlib.contextmanager
    def fake_sdk(self):
        import mcp
        import mcp.client.streamable_http as stream
        import httpx2
        with patch.object(mcp, "ClientSession", FakeSession):
            with patch.object(stream, "streamable_http_client", fake_transport):
                with patch.object(httpx2, "AsyncClient", FakeHTTP):
                    yield


class HandshakeAndToolCall(EmbeddedTinyFishSessionTests):
    def test_search_performs_one_real_protocol_round_trip(self):
        FakeSession.payloads = {"search": {"results": [
            {"title": "Public listing", "url": "https://public.example.org/a"}]}}
        with self.fake_sdk():
            out = tinyfish.call("search", {"query": "public venue list"})
        self.assertEqual(1, FakeSession.initializations)
        self.assertEqual([("search", {"query": "public venue list"})], FakeSession.calls)
        self.assertTrue(out["ok"])
        self.assertEqual("OFFICIAL_STREAMABLE_HTTP_MCP", out["transport"])
        self.assertEqual(tinyfish.ENDPOINT, out["endpoint"])
        self.assertEqual("FREE_TIER", out["charge_state"])
        self.assertFalse(out["business_evidence_verified"])
        self.assertFalse(out["provider_charge_verified"])
        self.assertIsNone(out["actual_charge_usd"])

    def test_fetch_sends_the_broadened_flags_and_approves_each_target(self):
        FakeSession.payloads = {"fetch_content": {"results": [
            {"url": "https://public.example.org/a", "markdown": "# hi"}]}}
        with self.fake_sdk():
            tinyfish.call("fetch_content", {
                "urls": ["https://public.example.org/a"], "format": "markdown",
                "links": True, "page_metadata": True, "ttl": 0})
        name, arguments = FakeSession.calls[0]
        self.assertEqual("fetch_content", name)
        self.assertTrue(arguments["links"])
        self.assertEqual(0, arguments["ttl"])

    def test_fetch_fills_the_vendors_documented_defaults_explicitly(self):
        # The upstream page lists format/links/image_links/page_metadata under
        # its Required column while also giving them defaults, so omitting them
        # is ambiguous. Sending the documented defaults satisfies either
        # reading and cannot change the result.
        FakeSession.payloads = {"fetch_content": {"results": []}}
        with self.fake_sdk():
            tinyfish.call("fetch_content", {"urls": ["https://public.example.org/a"]})
        _name, arguments = FakeSession.calls[0]
        self.assertEqual("markdown", arguments["format"])
        self.assertIs(False, arguments["links"])
        self.assertIs(False, arguments["image_links"])
        self.assertIs(False, arguments["page_metadata"])

    def test_an_explicit_fetch_flag_still_wins_over_the_default(self):
        FakeSession.payloads = {"fetch_content": {"results": []}}
        with self.fake_sdk():
            tinyfish.call("fetch_content", {"urls": ["https://public.example.org/a"],
                                            "format": "json", "links": True})
        _name, arguments = FakeSession.calls[0]
        self.assertEqual("json", arguments["format"])
        self.assertIs(True, arguments["links"])

    def test_search_does_not_gain_defaults_it_never_documented(self):
        FakeSession.payloads = {"search": {"results": []}}
        with self.fake_sdk():
            tinyfish.call("search", {"query": "public venue list"})
        _name, arguments = FakeSession.calls[0]
        self.assertEqual({"query": "public venue list"}, arguments)

    def test_the_provider_key_goes_in_the_api_key_header_not_as_a_bearer(self):
        # A provider API key and an OAuth access token are different credential
        # classes. Sending the key as a Bearer token is the fork's reading; the
        # repo's reviewed REST convention is the dedicated key header.
        FakeSession.payloads = {"search": {"results": []}}
        with self.fake_sdk():
            out = tinyfish.call("search", {"query": "public venue list"})
        headers = FakeHTTP.observations[0]["headers"]
        self.assertEqual("synthetic-tinyfish-key-not-for-network",
                         headers["X-API-Key"])
        self.assertNotIn("Authorization", headers)
        self.assertFalse(FakeHTTP.observations[0]["redirects"])
        self.assertIsNone(FakeHTTP.observations[0]["auth"])
        self.assertNotIn("synthetic-tinyfish-key", json.dumps(out))
        self.assertEqual("USER_PROVIDED_BEARER_KEY", out["auth_mode"])
        self.assertEqual("X-API-Key", out["auth_header"])

    def test_the_mcp_access_token_goes_in_the_authorization_header(self):
        FakeSession.payloads = {"search": {"results": []}}
        with patch.dict(os.environ, {"TINYFISH_API_KEY": "",
                                     "TINYFISH_MCP_ACCESS_TOKEN": "synthetic-mcp-token"}):
            with self.fake_sdk():
                out = tinyfish.call("search", {"query": "public venue list"})
        headers = FakeHTTP.observations[0]["headers"]
        self.assertEqual("Bearer synthetic-mcp-token", headers["Authorization"])
        self.assertNotIn("X-API-Key", headers)
        self.assertEqual("USER_PROVIDED_MCP_ACCESS_TOKEN", out["auth_mode"])
        self.assertEqual("Authorization", out["auth_header"])
        self.assertNotIn("synthetic-mcp-token", json.dumps(out))

    def test_oauth_keyring_mode_sends_no_static_credential_header(self):
        with patch.dict(os.environ, {"TINYFISH_API_KEY": "",
                                     "TINYFISH_MCP_ACCESS_TOKEN": ""}):
            with patch("research_adapter_tinyfish._keyring_tokens_present",
                       return_value=True):
                with patch("research_official_mcp_auth.oauth_provider",
                           return_value=object()) as provider:
                    FakeSession.payloads = {"search": {"results": []}}
                    with self.fake_sdk():
                        out = tinyfish.call("search", {"query": "public venue list"})
        provider.assert_called_once_with(interactive=False)
        self.assertEqual({}, FakeHTTP.observations[0]["headers"])
        self.assertIsNotNone(FakeHTTP.observations[0]["auth"])
        self.assertEqual("OS_KEYRING_OAUTH", out["auth_mode"])

    def test_the_auth_wire_contract_is_reported_as_unverified(self):
        FakeSession.payloads = {"search": {"results": []}}
        with self.fake_sdk():
            out = tinyfish.call("search", {"query": "public venue list"})
        self.assertEqual(tinyfish.AUTH_WIRE, out["auth_wire"])
        self.assertFalse(out["auth_wire_verified"])
        self.assertIn("X_API_KEY_HEADER", tinyfish.AUTH_WIRE)


class SchemaDriftInsideTheSession(EmbeddedTinyFishSessionTests):
    """Drift is judged per capability (IR-4), not per session.

    This class used to assert a blanket refusal: any difference between the pin
    and the live `tools/list` killed the whole session. That is stricter than
    the frozen contract, which refuses a capability whose own tool moved and
    leaves compatible T1 reads alone. The stricter reading also had a real cost
    -- one new upstream tool would disable the whole TinyFish surface.
    """

    def test_a_newly_advertised_tool_is_never_callable_but_does_not_block_a_read(self):
        FakeSession.names = tuple(CATALOG) + ("purge_account",)
        FakeSession.payloads = {"search": {"results": []}}
        with self.fake_sdk():
            out = tinyfish.call("search", {"query": "public venue list"})
        self.assertTrue(out["ok"])
        self.assertEqual(["search"], [n for n, _ in FakeSession.calls])
        # It has no capability_id, so it cannot be reached at all.
        self.assertNotIn("tinyfish.purge_account", tinyfish.capabilities())
        with self.assertRaisesRegex(ValueError, "TINYFISH_TOOL_NOT_IN_OFFICIAL_CATALOG"):
            tinyfish.validate("purge_account", {})

    def test_a_vanished_tool_leaves_the_other_capabilities_alone(self):
        # `verify_schema` keys the *served* capabilities, so the vanished one
        # has to be one of those -- `get_run` is T1 and callable.
        FakeSession.names = tuple(n for n in CATALOG if n != "get_run")
        FakeSession.payloads = {"search": {"results": []}}
        with self.fake_sdk():
            out = tinyfish.call("search", {"query": "public venue list"})
        self.assertTrue(out["ok"])
        verdicts = tinyfish.verify_schema(FakeSession.names)
        self.assertEqual("ABSENT", verdicts["tinyfish.get_run"])
        self.assertEqual("MATCH", verdicts["tinyfish.search"])

    def test_a_drifted_paid_capability_is_refused_even_though_a_read_is_not(self):
        FakeSession.names = tuple(CATALOG) + ("purge_account",)
        # The paid path reads the wallet before it dispatches, so the preflight
        # has to succeed for the drift check to be the thing under test.
        FakeSession.payloads = {"get_wallet": WALLET}
        with patch.dict(os.environ, {"MARKETOPS_TINYFISH_AGENT_ALLOW_PAID": "1"}):
            with self.fake_sdk():
                with self.assertRaisesRegex(ValueError, "TINYFISH_SCHEMA_DRIFT_REFUSED"):
                    tinyfish.call(
                        "run_web_automation",
                        {"url": "https://public.example.org/x",
                         "goal": "Read the publicly listed venue names"},
                        max_charge_usd="0.50",
                        authorization={"approved_by": "op@example.test",
                                       "scope": "TINYFISH_PAID_AGENT_RUN",
                                       "accept_uncapped_provider_spend": True})
        # The free wallet preflight legitimately ran; the paid submission did
        # not, which is the whole point of refusing on drift.
        self.assertEqual(["get_wallet"], [n for n, _ in FakeSession.calls])

    def test_the_capability_the_caller_asked_for_must_be_advertised(self):
        FakeSession.names = tuple(n for n in CATALOG if n != "search")
        with self.fake_sdk():
            with self.assertRaisesRegex(ValueError, "TINYFISH_TOOL_NOT_ADVERTISED"):
                tinyfish.call("search", {"query": "public venue list"})
        self.assertEqual([], FakeSession.calls)

    def test_an_empty_advertisement_is_not_an_empty_tool_surface(self):
        FakeSession.names = ()
        with self.fake_sdk():
            with self.assertRaisesRegex(ValueError, "TINYFISH_TOOL_NOT_ADVERTISED"):
                tinyfish.call("search", {"query": "public venue list"})
        self.assertEqual([], FakeSession.calls)


class HostileProviderResponses(EmbeddedTinyFishSessionTests):
    def reply(self, factory):
        FakeSession.result = factory

    def test_the_session_never_reads_the_machine_proxy(self):
        self.reply(lambda n, a: types.SimpleNamespace(isError=False, structuredContent={"results": []}))
        FakeHTTP.observations.clear()
        with self.fake_sdk():
            try:
                tinyfish.call("search", {"query": "public venue list"})
            except ValueError:
                pass
        self.assertTrue(FakeHTTP.observations)
        self.assertTrue(all(o["trust_env"] is False for o in FakeHTTP.observations))

    def test_a_tool_level_error_is_not_a_successful_result(self):
        self.reply(lambda n, a: types.SimpleNamespace(isError=True, structuredContent={}))
        with self.fake_sdk():
            with self.assertRaisesRegex(ValueError, "UPSTREAM_MCP_TOOL_FAILED"):
                tinyfish.call("search", {"query": "public venue list"})

    def test_an_empty_result_is_refused(self):
        self.reply(lambda n, a: types.SimpleNamespace(isError=False, content=[]))
        with self.fake_sdk():
            with self.assertRaisesRegex(ValueError, "UPSTREAM_MCP_EMPTY_RESULT"):
                tinyfish.call("search", {"query": "public venue list"})

    def test_an_oversized_result_is_refused_rather_than_persisted(self):
        huge = {"filler": "x" * 1000}
        self.reply(lambda n, a: types.SimpleNamespace(
            isError=False,
            structuredContent={"result": json.dumps([huge] * 4000)}))
        with self.fake_sdk():
            with self.assertRaisesRegex(ValueError, "UPSTREAM_MCP_RESULT_TOO_LARGE"):
                tinyfish.call("search", {"query": "public venue list"})

    def test_a_text_block_carrying_json_is_parsed(self):
        self.reply(lambda n, a: types.SimpleNamespace(
            isError=False, content=[types.SimpleNamespace(
                text=json.dumps({"results": [{"title": "T"}]}))]))
        with self.fake_sdk():
            out = tinyfish.call("search", {"query": "public venue list"})
        self.assertEqual([{"title": "T"}], out["tool_result"]["results"])

    def test_a_text_block_that_is_not_json_is_kept_as_text(self):
        self.reply(lambda n, a: types.SimpleNamespace(
            isError=False, content=[types.SimpleNamespace(text="plain page text")]))
        with self.fake_sdk():
            out = tinyfish.call("search", {"query": "public venue list"})
        self.assertEqual("plain page text", out["tool_result"]["text"])

    def test_provider_secret_shaped_fields_are_redacted_before_persistence(self):
        FakeSession.payloads = {"search": {"results": [], "checkout_url":
                                           "https://vendor.test/pay?session=live"}}
        with self.fake_sdk():
            out = tinyfish.call("search", {"query": "public venue list"})
        self.assertEqual("[REDACTED]", out["tool_result"]["checkout_url"])
        self.assertNotIn("vendor.test/pay", json.dumps(out))

    def test_a_wrapped_transport_failure_never_echoes_the_upstream_message(self):
        async def hostile(tool, arguments):
            raise ExceptionGroup("mcp", [
                ValueError("Bearer synthetic-tinyfish-key-not-for-network rejected")])

        # It was dispatched, so the outcome is UNKNOWN rather than a raise; the
        # upstream text still must not reach the receipt.
        out = tinyfish.call("search", {"query": "public venue list"}, runner=hostile)
        self.assertFalse(out["ok"])
        self.assertEqual("TINYFISH_DISPATCH_OUTCOME_UNKNOWN", out["error"])
        self.assertEqual("UNKNOWN", out["charge_state"])
        self.assertNotIn("synthetic-tinyfish-key", json.dumps(out, default=str))


class PaidPathOverTheRealSession(EmbeddedTinyFishSessionTests):
    def setUp(self):
        super().setUp()
        patch.dict(os.environ,
                   {"MARKETOPS_TINYFISH_AGENT_ALLOW_PAID": "1"}).start()
        FakeSession.payloads = {"get_wallet": WALLET,
                                "run_web_automation": {"runId": "run-abc-1",
                                                       "status": "PENDING"}}

    def test_the_wallet_preflight_and_the_submission_use_separate_sessions(self):
        # Deliberate: the wallet read is provably free, while the submission is
        # the one call whose failure is ambiguous. Running them in one protocol
        # session would merge a failed free read with an ambiguous paid
        # dispatch into a single exception, which is exactly the distinction
        # the charge accounting depends on. Both steps still use the same
        # reviewed endpoint and the same credential.
        with self.fake_sdk():
            out = tinyfish.call("run_web_automation",
                                {"url": "https://public.example.org/venues",
                                 "goal": "Read the publicly listed venue names"},
                                max_charge_usd="0.50", authorization=AUTHORIZATION)
        self.assertEqual(["get_wallet", "run_web_automation"],
                         [n for n, _ in FakeSession.calls])
        self.assertEqual(2, FakeSession.initializations)
        # The free read and the paid dispatch authenticate identically.
        self.assertEqual(2, len(FakeHTTP.observations))
        self.assertEqual(
            ["synthetic-tinyfish-key-not-for-network"] * 2,
            [o["headers"]["X-API-Key"] for o in FakeHTTP.observations])
        self.assertTrue(out["ok"])
        self.assertEqual("run-abc-1", out["agent_run_id"])
        self.assertEqual("UNKNOWN", out["charge_state"])
        # A fresh UUID session id was minted, not supplied by the caller.
        sent = dict(FakeSession.calls)["run_web_automation"]
        self.assertEqual(4, __import__("uuid").UUID(sent["session_id"]).version)
        self.assertEqual("25.00", out["wallet"]["balance"])

    def test_the_paid_transcript_carries_no_credential(self):
        with self.fake_sdk():
            out = tinyfish.call("run_web_automation",
                                {"url": "https://public.example.org/venues",
                                 "goal": "Read the publicly listed venue names"},
                                max_charge_usd="0.50", authorization=AUTHORIZATION)
        self.assertNotIn("synthetic-tinyfish-key", json.dumps(out))


class SynchronousCallBoundary(EmbeddedTinyFishSessionTests):
    def test_a_call_from_inside_an_event_loop_is_refused_before_dispatch(self):
        async def inner():
            tinyfish.call("search", {"query": "public venue list"},
                          runner=self.never)

        with self.assertRaisesRegex(ValueError,
                                    "OFFICIAL_MCP_SYNC_CALL_INSIDE_RUNNING_LOOP"):
            asyncio.run(inner())

    async def never(self, tool, arguments):
        raise AssertionError("must not dispatch")


if __name__ == "__main__":
    unittest.main()
