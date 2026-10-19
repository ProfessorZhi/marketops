"""W-D's two adapters, held to the frozen contract and to adversarial injection.

These tests are deterministic and offline: no socket, no credential, no vendor.
They answer four questions the Wave-1 contract asks of an adapter:

1. §6 ``ProviderAdapter`` — does it expose ``capabilities``/``probe_schema``/
   ``execute``/``cancel``, and does ``execute`` return exactly §3.4's field set?
2. §7 IF-5 and §5.3 — is every refusal a closed-set code, or a code that maps to
   one with a written reason, rather than prose?
3. IA-1..IA-6 — no credential in any output, no policy/tier decision inside the
   adapter, no retry after dispatch, no redirect followed, upstream text cleaned.
4. The injection surface the owner brief names — a host must not be able to
   choose an endpoint, a tool, a transport, a tier, a provider or a raw engine.

The Gateway itself is exercised when ``research_gateway`` is importable on this
branch; on the branch that owns only these adapters it is not, so that class
skips with an explicit reason instead of pretending to have run.
"""
from __future__ import annotations

import importlib.util
import os
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import research_adapter_serpapi as serpapi
import research_adapter_serper as serper
import search_canonical as canonical
import serpapi_engine_catalog as catalog_module

#: The §5.3 closed set, transcribed from INTERFACE-CONTRACT.md v1.0. A local copy
#: rather than an import: `research_gateway` owns the runtime gate, and this test
#: must be able to fail if either side drifts away from the contract text.
CONTRACT_REASON_CODES = frozenset({
    "POLICY_TIER_NOT_GRANTED", "POLICY_DOMAIN_NOT_ALLOWED", "POLICY_GRANT_EXPIRED",
    "POLICY_GRANT_REVOKED", "BUDGET_RUN_CEILING", "BUDGET_PROVIDER_CEILING",
    "BUDGET_PROJECT_CEILING", "BUDGET_OPERATION_CEILING", "PRICE_UNKNOWN_FAIL_CLOSED",
    "PRICING_SOURCE_MISSING", "DUPLICATE_OPERATION", "OPERATION_ID_COLLISION",
    "HOST_PREVIOUSLY_BLOCKED", "CAPABILITY_UNKNOWN", "CAPABILITY_PARAM_NOT_ALLOWED",
    "CAPABILITY_SCHEMA_DRIFT", "CAPABILITY_NOT_AVAILABLE", "EGRESS_REFUSED",
    "CRAWL4AI_LOCAL_NOT_INSTALLED", "UNKNOWN_CHARGE_OUTSTANDING",
    "RESEARCH_NETWORK_DISABLED", "WALLET_PRECONDITION_FAILED", "GRANT_SIGNATURE_INVALID",
})

#: §3.4, field for field.
ADAPTER_RESULT_FIELDS = ("outcome", "payload", "artifacts", "upstream_ref",
                         "actual_usd", "actual_source", "quota_units_used",
                         "transport", "schema_verdict", "caveats")


class Request:
    """Duck-typed §3.1 ``CapabilityRequest``. The adapters read four attributes."""

    def __init__(self, capability_id, params=None, *, run_id="run-1",
                 operation_id="op-1", account_ref="acct-1",
                 intent_fingerprint="intent-1", grant_ref="grant-1"):
        self.capability_id = capability_id
        self.params = dict(params or {})
        self.run_id = run_id
        self.operation_id = operation_id
        self.account_ref = account_ref
        self.intent_fingerprint = intent_fingerprint
        self.grant_ref = grant_ref


# ---------------------------------------------------------------------------
# Fake upstreams. Both are transport-boundary fakes: deterministic, no socket,
# and they record the exact request shape so the assertions are on our bytes.
# ---------------------------------------------------------------------------

class FakeHttpResponse:
    def __init__(self, payload, status=200):
        import io
        import json
        self.status = status
        self._stream = io.BytesIO(json.dumps(payload).encode("utf-8"))

    def read(self, n):
        return self._stream.read(n)

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self._stream.close()


class FakeSerperServer:
    """A Serper REST stand-in: records every request, answers deterministically."""

    def __init__(self, payload=None, status=200, raises=None):
        self.requests = []
        self.payload = payload if payload is not None else {"organic": []}
        self.status = status
        self.raises = raises

    def __call__(self, req, timeout):
        import json
        self.requests.append({
            "url": req.full_url, "method": req.get_method(),
            "body": json.loads(req.data) if req.data else None,
            "headers": {k.lower(): v for k, v in req.header_items()},
        })
        if self.raises is not None:
            raise self.raises
        if isinstance(self.status, int) and self.status != 200:
            import urllib.error
            raise urllib.error.HTTPError(req.full_url, self.status, "err", None, None)
        return FakeHttpResponse(self.payload, self.status)


class FakeResource:
    def __init__(self, uri, uri_template=None, name=""):
        self.uri = uri
        self.uriTemplate = uri_template
        self.name = name


class FakeContent:
    def __init__(self, uri, text):
        self.uri = uri
        self.mimeType = "application/json"
        self.text = text


class FakeToolResult:
    def __init__(self, text):
        self.isError = False
        self.structuredContent = None
        self.content = [FakeContent("mcp://tool", text)]


class FakeMcpSession:
    """A fake SerpApi official MCP session: the four methods we depend on.

    It records the MCP method surface rather than an HTTP shape, because the
    contract is with the MCP protocol, not with the socket underneath it.
    """

    def __init__(self, *, engines=("google_light", "google_maps"), schemas=None,
                 search_payload=None, tools=("search",)):
        self.engines = tuple(engines)
        self.schemas = schemas or {name: {"type": "object"} for name in engines}
        self.search_payload = search_payload if search_payload is not None else {
            "organic_results": [{"title": "Example Club", "link": "https://example.org"}]}
        self.tools = tuple(tools)
        self.calls = []

    async def list_tools(self):
        self.calls.append(("list_tools",))
        return type("R", (), {"tools": [type("T", (), {"name": n})() for n in self.tools]})()

    async def list_resources(self):
        self.calls.append(("list_resources",))
        return type("R", (), {"resources": [
            FakeResource(catalog_module.INDEX_URI, name="Engine index"),
            FakeResource(None, catalog_module.SCHEMA_URI_TEMPLATE, name="Engine schema"),
        ]})()

    async def read_resource(self, uri):
        import json
        self.calls.append(("read_resource", uri))
        if uri == catalog_module.INDEX_URI:
            return type("R", (), {"contents": [
                FakeContent(uri, json.dumps({"engines": list(self.engines)}))]})()
        name = uri.rsplit("/", 1)[-1]
        return type("R", (), {"contents": [
            FakeContent(uri, json.dumps(self.schemas[name]))]})()

    async def call_tool(self, name, arguments):
        import json
        self.calls.append(("call_tool", name, json.dumps(arguments, sort_keys=True)))
        return FakeToolResult(json.dumps(self.search_payload))

    def method_names(self):
        return [call[0] for call in self.calls]


def loaded_catalog(tmp_root, engines=("google_light", "google_maps")):
    """A catalog whose index has been read, without touching any vendor."""
    import asyncio
    catalog = catalog_module.EngineCatalog(Path(tmp_root) / "state" / "engine-cache.json")
    asyncio.run(catalog_module.discover_index(FakeMcpSession(engines=engines), catalog=catalog))
    return catalog


# ---------------------------------------------------------------------------
# A. Registry-facing surface
# ---------------------------------------------------------------------------

class CapabilitySurfaceTests(unittest.TestCase):
    def test_serper_declares_one_capability_and_says_which_tier(self):
        self.assertEqual("serper", serper.PROVIDER)
        self.assertEqual("rest", serper.TRANSPORT)
        self.assertEqual(("serper.search",), serper.CAPABILITIES)
        self.assertEqual({"serper.search": "T2"}, serper.TIER_BY_CAPABILITY)

    def test_serpapi_declares_search_plus_the_two_resource_reads(self):
        self.assertEqual("serpapi", serpapi.PROVIDER)
        self.assertEqual("official_mcp", serpapi.TRANSPORT)
        self.assertEqual(("serpapi.engine_catalog", "serpapi.engine_schema",
                          "serpapi.search"), serpapi.CAPABILITIES)
        for capability_id in serpapi.CAPABILITIES:
            with self.subTest(capability_id=capability_id):
                self.assertIn(serpapi.TIER_BY_CAPABILITY[capability_id], ("T1", "T2"))

    def test_the_serpapi_resource_reads_stay_behind_the_t2_switch(self):
        # Reading a resource is remote and credentialed. The convergence review
        # says a remote read-only call is not thereby local or free, and no
        # account price was ever read here — so these fail closed at T2 rather
        # than being promoted to a cheaper rung on an assumption.
        self.assertEqual("T2", serpapi.TIER_BY_CAPABILITY["serpapi.engine_catalog"])
        self.assertEqual("T2", serpapi.TIER_BY_CAPABILITY["serpapi.engine_schema"])

    def test_the_scrape_host_is_visible_and_unsupported_not_absent(self):
        declared = serper.DECLARED_NOT_SERVED
        self.assertIn("serper.scrape", declared)
        row = declared["serper.scrape"]
        self.assertEqual("UNSUPPORTED", row["availability"])
        self.assertIn("scrape.serper.dev", row["reason"])
        self.assertIn("host", row["reason"])
        # It must not be smuggled into the served set to reach a count.
        self.assertNotIn("serper.scrape", serper.CAPABILITIES)

    def test_the_legacy_serpapi_rest_path_is_visible_and_disabled(self):
        row = serpapi.DECLARED_NOT_SERVED["serpapi.rest_search"]
        self.assertEqual("DISABLED", row["availability"])
        self.assertIn("MARKETOPS_SERPAPI_REST_ENABLE", row["reason"])
        self.assertNotIn("serpapi.rest_search", serpapi.CAPABILITIES)

    def test_describe_capabilities_is_registry_material(self):
        for module in (serper, serpapi):
            with self.subTest(provider=module.PROVIDER):
                rows = module.describe_capabilities()
                self.assertEqual(set(module.CAPABILITIES), {r["capability_id"] for r in rows})
                for row in rows:
                    required = {"capability_id", "provider", "transport", "tier",
                                "effect_class", "allowed_params", "availability",
                                "readiness", "notes"}
                    self.assertLessEqual(required, set(row))
                    if module is serpapi:
                        self.assertIs(row["resources_reviewed"], False,
                                      "engine resources must report unreviewed")
                    self.assertEqual(module.PROVIDER, row["provider"])
                    self.assertIsInstance(row["allowed_params"], tuple)
                    self.assertEqual("ENABLED", row["availability"])
                    self.assertEqual("CODED", row["readiness"])


# ---------------------------------------------------------------------------
# B. Reason codes: closed set, or a written mapping into it
# ---------------------------------------------------------------------------

class ReasonCodeDisciplineTests(unittest.TestCase):
    def test_every_adapter_local_code_maps_into_the_contract_closed_set(self):
        for module in (serper, serpapi):
            with self.subTest(provider=module.PROVIDER):
                self.assertTrue(module.CONTRACT_REASON_CODES <= CONTRACT_REASON_CODES,
                                "a local copy may not invent contract codes")
                for code in module.ADAPTER_LOCAL_REASONS:
                    if code in module.POST_DISPATCH_CODES:
                        # A failure that happened has no policy analogue, and
                        # giving it one is how it gets quietly reported as one
                        # that never left. Its absence is the discipline.
                        self.assertNotIn(code, module.CONTRACT_ANALOGUE)
                        continue
                    mapped = module.CONTRACT_ANALOGUE[code]
                    self.assertIn(mapped, CONTRACT_REASON_CODES,
                                  f"{code} maps to {mapped}, which is not in §5.3")

    def test_a_local_code_without_a_contract_analogue_is_a_development_error(self):
        for module in (serper, serpapi):
            with self.subTest(provider=module.PROVIDER):
                with self.assertRaises(AssertionError):
                    module._contract_code("NOT_A_REAL_CODE")

    def test_post_dispatch_codes_are_never_reported_as_blocked(self):
        # §2: BLOCKED means "refused locally, upstream never reached". A code that
        # only arises after the request went out must not be recorded as BLOCKED.
        for module in (serper, serpapi):
            with self.subTest(provider=module.PROVIDER):
                self.assertFalse(module.POST_DISPATCH_CODES & module.CONTRACT_REASON_CODES,
                                 "a post-dispatch code must be adapter-local, not a policy code")
                for code in module.POST_DISPATCH_CODES:
                    self.assertIn(code, module.ADAPTER_LOCAL_REASONS)


# ---------------------------------------------------------------------------
# C. §6 Protocol shape
# ---------------------------------------------------------------------------

class AdapterProtocolTests(unittest.TestCase):
    def test_both_adapters_expose_the_four_protocol_members(self):
        for adapter in (serper.SerperAdapter(), serpapi.SerpApiAdapter()):
            with self.subTest(provider=adapter.provider):
                for name in ("capabilities", "probe_schema", "execute", "cancel"):
                    self.assertTrue(callable(getattr(adapter, name, None)), name)

    def test_capabilities_matches_the_declared_tuple(self):
        self.assertEqual(serper.CAPABILITIES, serper.SerperAdapter().capabilities())
        self.assertEqual(serpapi.CAPABILITIES, serpapi.SerpApiAdapter().capabilities())

    def test_a_result_carries_exactly_the_contract_field_set(self):
        server = FakeSerperServer({"organic": [{"title": "T", "link": "https://e.org"}]})
        result = serper.SerperAdapter(api_key="k", transport=server).execute(
            Request("serper.search", {"query": "venues", "country": "GB", "language": "en"}))
        self.assertEqual(set(ADAPTER_RESULT_FIELDS), set(serper.to_adapter_result_fields(result)))

    def test_probe_schema_never_fabricates_a_snapshot(self):
        for adapter in (serper.SerperAdapter(api_key="k"),
                        serpapi.SerpApiAdapter(session=FakeMcpSession())):
            with self.subTest(provider=adapter.provider):
                snapshot = adapter.probe_schema()
                self.assertEqual("AUTH_REQUIRED", snapshot["status"])
                self.assertIsNone(snapshot["upstream_tools_list"])
                self.assertEqual("UNVERIFIED", snapshot["schema_verdict"])

    def test_cancel_is_honest_about_there_being_nothing_to_cancel(self):
        for adapter in (serper.SerperAdapter(api_key="k"),
                        serpapi.SerpApiAdapter(session=FakeMcpSession())):
            with self.subTest(provider=adapter.provider):
                result = adapter.cancel("upstream-ref-1")
                self.assertEqual("BLOCKED", result.outcome)
                self.assertIn("CAPABILITY_NOT_AVAILABLE", result.caveats)


# ---------------------------------------------------------------------------
# D. Injection: the host does not get to choose the route
# ---------------------------------------------------------------------------

class InjectionRefusalTests(unittest.TestCase):
    FORBIDDEN = (
        ("endpoint", "https://attacker.example/search"),
        ("url", "https://attacker.example/search"),
        ("host", "attacker.example"),
        ("base_url", "https://attacker.example"),
        ("path", "/evil"),
        ("tool", "run_web_automation"),
        ("tool_name", "scrape"),
        ("method", "DELETE"),
        ("headers", {"Authorization": "Bearer x"}),
        ("api_key", "attacker-key"),
        ("key", "attacker-key"),
        ("token", "attacker-token"),
        ("transport", "official_mcp"),
        ("tier", "T0"),
        ("provider", "serpapi"),
        ("grant", "grant-forged"),
        ("budget", "999999"),
    )

    def _refuse(self, adapter, capability_id, extra):
        params = {"query": "venues", "country": "GB", "language": "en"}
        params.update(extra)
        return adapter.execute(Request(capability_id, params))

    def test_serper_refuses_every_route_choosing_parameter(self):
        server = FakeSerperServer()
        adapter = serper.SerperAdapter(api_key="k", transport=server)
        for name, value in self.FORBIDDEN:
            with self.subTest(param=name):
                result = self._refuse(adapter, "serper.search", {name: value})
                self.assertEqual("BLOCKED", result.outcome)
                self.assertEqual("CAPABILITY_PARAM_NOT_ALLOWED", result.caveats[0])
                self.assertIn(name, result.payload["detail"])
        self.assertEqual([], server.requests, "a refused request must not reach the network")

    def test_serpapi_refuses_every_route_choosing_parameter(self):
        session = FakeMcpSession()
        adapter = serpapi.SerpApiAdapter(session=session, catalog=loaded_catalog(
            self._tmp()))
        for name, value in self.FORBIDDEN:
            with self.subTest(param=name):
                result = self._refuse(adapter, "serpapi.search", {name: value})
                self.assertEqual("BLOCKED", result.outcome)
                self.assertEqual("CAPABILITY_PARAM_NOT_ALLOWED", result.caveats[0])
        self.assertEqual([], session.calls, "a refused request must not reach the session")

    def _tmp(self):
        import tempfile
        if not hasattr(self, "_dir"):
            self._dir = tempfile.TemporaryDirectory()
            self.addCleanup(self._dir.cleanup)
        return self._dir.name

    def test_an_engine_outside_the_catalog_is_blocked_locally(self):
        session = FakeMcpSession(engines=("google_light",))
        catalog = loaded_catalog(self._tmp(), engines=("google_light",))
        adapter = serpapi.SerpApiAdapter(session=session, catalog=catalog)
        result = adapter.execute(Request("serpapi.search", {
            "engine": "google_flights", "query": "flights",
            "country": "GB", "language": "en"}))
        self.assertEqual("BLOCKED", result.outcome)
        self.assertEqual("CAPABILITY_UNKNOWN", result.caveats[0])
        self.assertEqual("INDEX_READ", result.payload["index_state"])
        self.assertEqual([], session.calls)

    def test_an_unread_engine_index_does_not_read_as_a_missing_engine(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            empty = catalog_module.EngineCatalog(Path(tmp) / "state" / "c.json")
            session = FakeMcpSession(engines=("google_light",))
            adapter = serpapi.SerpApiAdapter(session=session, catalog=empty)
            result = adapter.execute(Request("serpapi.search", {
                "engine": "google_light", "query": "x y",
                "country": "GB", "language": "en"}))
            self.assertEqual("CAPABILITY_UNKNOWN", result.caveats[0])
            self.assertEqual("INDEX_NOT_READ", result.payload["index_state"])

    def test_an_unservable_serper_category_is_refused_by_name(self):
        server = FakeSerperServer()
        adapter = serper.SerperAdapter(api_key="k", transport=server)
        result = adapter.execute(Request("serper.search",
                                         {"query": "venues", "mode": "reviews"}))
        self.assertEqual("BLOCKED", result.outcome)
        self.assertEqual("CAPABILITY_PARAM_NOT_ALLOWED", result.caveats[0])
        self.assertIn("SERPER_MODE_REQUIRES_UNSENT_PARAM", result.payload["detail"])
        self.assertEqual([], server.requests)


# ---------------------------------------------------------------------------
# E. Parameter fuzz
# ---------------------------------------------------------------------------

class ParameterFuzzTests(unittest.TestCase):
    def test_unknown_keys_are_refused_by_name_and_never_dispatch(self):
        server = FakeSerperServer()
        adapter = serper.SerperAdapter(api_key="k", transport=server)
        for key in ("nonsense", "q", "Query", "query ", "__proto__", "0", ""):
            with self.subTest(key=key):
                result = adapter.execute(Request("serper.search",
                                                 {"query": "venues", key: "v"}))
                self.assertEqual("BLOCKED", result.outcome)
                self.assertEqual("CAPABILITY_PARAM_NOT_ALLOWED", result.caveats[0])
        self.assertEqual([], server.requests)

    def test_oversized_and_structured_values_are_refused(self):
        server = FakeSerperServer()
        adapter = serper.SerperAdapter(api_key="k", transport=server)
        for params in ({"query": "x" * 5000, "country": "GB", "language": "en"},
                       {"query": {"nested": {"deep": [1, 2, 3]}}},
                       {"query": ["a", "b"]},
                       {"query": "venues", "limit": "ten"},
                       {"query": "venues", "limit": 10 ** 9},
                       {"query": "venues", "page": 0},
                       {"query": "venues", "mode": "web", "page": 99}):
            with self.subTest(params=str(params)[:60]):
                result = adapter.execute(Request("serper.search", params))
                self.assertEqual("BLOCKED", result.outcome)
                self.assertIn(result.caveats[0], CONTRACT_REASON_CODES)
        self.assertEqual([], server.requests)

    def test_an_unknown_capability_is_blocked_without_touching_transport(self):
        server = FakeSerperServer()
        adapter = serper.SerperAdapter(api_key="k", transport=server)
        result = adapter.execute(Request("serper.everything", {"query": "venues"}))
        self.assertEqual("BLOCKED", result.outcome)
        self.assertEqual("CAPABILITY_UNKNOWN", result.caveats[0])
        self.assertEqual([], server.requests)

    def test_params_that_are_not_a_mapping_are_refused(self):
        adapter = serper.SerperAdapter(api_key="k", transport=FakeSerperServer())
        request = Request("serper.search")
        request.params = ["not", "a", "mapping"]
        result = adapter.execute(request)
        self.assertEqual("BLOCKED", result.outcome)
        self.assertEqual("CAPABILITY_PARAM_NOT_ALLOWED", result.caveats[0])


# ---------------------------------------------------------------------------
# F. Prerequisites, not policy
# ---------------------------------------------------------------------------

class PrerequisiteTests(unittest.TestCase):
    def test_an_unbound_transport_refuses_instead_of_reaching_the_network(self):
        # The analogue of E's UNBOUND_EGRESS: the adapter never opens a socket on
        # its own, so an unbound adapter cannot be an accidental new paid path.
        result = serper.SerperAdapter().execute(
            Request("serper.search", {"query": "venues", "country": "GB", "language": "en"}))
        self.assertEqual("BLOCKED", result.outcome)
        self.assertEqual("CAPABILITY_NOT_AVAILABLE", result.caveats[0])

    def test_a_missing_credential_is_not_read_from_the_environment(self):
        os.environ["SERPER_API_KEY"] = "must-not-be-read"
        self.addCleanup(os.environ.pop, "SERPER_API_KEY", None)
        result = serper.SerperAdapter(transport=FakeSerperServer()).execute(
            Request("serper.search", {"query": "venues", "country": "GB", "language": "en"}))
        self.assertEqual("BLOCKED", result.outcome)
        self.assertEqual("CAPABILITY_NOT_AVAILABLE", result.caveats[0])

    def test_an_unbound_serpapi_session_refuses(self):
        result = serpapi.SerpApiAdapter().execute(
            Request("serpapi.search", {"engine": "google_light", "query": "x y"}))
        self.assertEqual("BLOCKED", result.outcome)
        self.assertEqual("CAPABILITY_NOT_AVAILABLE", result.caveats[0])

    def test_the_adapter_does_not_decide_tier_or_budget(self):
        # IA-2: an adapter that re-checked budget would be a second policy source.
        for module in (serper, serpapi):
            with self.subTest(provider=module.PROVIDER):
                source = (ROOT / "scripts" / f"research_adapter_{module.PROVIDER}.py").read_text(
                    encoding="utf-8")
                for forbidden in ("MARKETOPS_RESEARCH_MAX_EXTERNAL_CALLS",
                                  "MARKETOPS_SEARCH_MAX_CALLS", "MARKETOPS_RESEARCH_ALLOW_NETWORK",
                                  "MARKETOPS_SEARCH_ALLOW_NETWORK"):
                    self.assertNotIn(forbidden, source)


# ---------------------------------------------------------------------------
# G. IA-1, IA-3, IA-5, IA-6 and the single logical key
# ---------------------------------------------------------------------------

class DispatchDisciplineTests(unittest.TestCase):
    def test_a_dispatch_sends_exactly_one_request_and_never_retries(self):
        server = FakeSerperServer({"organic": []})
        adapter = serper.SerperAdapter(api_key="k", transport=server)
        adapter.execute(Request("serper.search",
                                {"query": "venues", "country": "GB", "language": "en"}))
        self.assertEqual(1, len(server.requests))

    def test_a_provider_error_is_not_retried_and_not_reported_as_blocked(self):
        server = FakeSerperServer(status=503)
        adapter = serper.SerperAdapter(api_key="k", transport=server)
        result = adapter.execute(Request("serper.search",
                                         {"query": "venues", "country": "GB", "language": "en"}))
        self.assertEqual(1, len(server.requests), "one dispatch means one request")
        self.assertEqual("FAILED", result.outcome)
        self.assertIn(result.caveats[0], serper.POST_DISPATCH_CODES)

    def test_the_credential_never_appears_in_the_result(self):
        server = FakeSerperServer({"organic": [{"title": "T", "link": "https://e.org"}]})
        adapter = serper.SerperAdapter(api_key="super-secret-key", transport=server)
        result = adapter.execute(Request("serper.search",
                                         {"query": "venues", "country": "GB", "language": "en"}))
        blob = repr(serper.to_adapter_result_fields(result))
        self.assertNotIn("super-secret-key", blob)
        self.assertIn("x-api-key", {k for r in server.requests for k in r["headers"]})

    def test_upstream_text_is_cleaned_before_it_enters_the_payload(self):
        server = FakeSerperServer({"organic": [
            {"title": "T", "link": "https://e.org", "snippet": "s",
             "api_key": "leaked-by-the-vendor", "authorization": "Bearer x"}]})
        adapter = serper.SerperAdapter(api_key="k", transport=server)
        result = adapter.execute(Request("serper.search",
                                         {"query": "venues", "country": "GB", "language": "en"}))
        blob = repr(result.payload)
        self.assertNotIn("leaked-by-the-vendor", blob)
        self.assertNotIn("Bearer x", blob)

    def test_one_logical_operation_has_one_key_across_transports(self):
        # The same provider, the same canonical arguments, two transports: one
        # key. This is the adapter's half of IL-2 — the ledger owns the other.
        #
        # The padded query is *deliberately the same operation*, so it cannot be
        # dispatched a second time any more (the guard refuses it). The property
        # is therefore asserted on the key itself rather than on a second receipt.
        server = FakeSerperServer({"organic": []})
        adapter = serper.SerperAdapter(api_key="k", transport=server)
        first = adapter.execute(Request("serper.search",
                                        {"query": "venues", "country": "GB", "language": "en"}))
        padded = canonical.operation_signature(canonical.canonical_arguments(
            provider="serper", mode="web", query="  venues  ", country="GB",
            language="en", limit=10, page=1, engine=None, account="acct-1",
            transport="REST_HTTPS"))
        self.assertEqual(first.payload["logical_operation_key"], padded)
        other_country = canonical.operation_signature(
            canonical.canonical_arguments(
                provider="serper", mode="web", query="venues", country="IE",
                language="en", limit=10, page=1, engine=None, account="acct-1",
                transport="REST_HTTPS"))
        self.assertNotEqual(first.payload["logical_operation_key"], other_country,
                            "a different market is a different spend opportunity")

    def test_the_logical_key_does_not_carry_a_transport(self):
        for module in (serper, serpapi):
            with self.subTest(provider=module.PROVIDER):
                source = (ROOT / "scripts" / f"research_adapter_{module.PROVIDER}.py").read_text(
                    encoding="utf-8")
                self.assertIn("operation_signature", source)
                # The key comes from search_canonical, which excludes transport.
                self.assertNotIn('"transport"] =', source)

    def test_a_redirect_is_not_followed(self):
        serper_source = (ROOT / "scripts" / "research_adapter_serper.py").read_text(
            encoding="utf-8")
        self.assertIn("NoRedirect", serper_source, "IA-5: the REST adapter sets its own opener")
        # The SerpApi adapter never opens a socket: its transport is the injected
        # official-MCP session, so a redirect is not a shape it can be handed.
        serpapi_source = (ROOT / "scripts" / "research_adapter_serpapi.py").read_text(
            encoding="utf-8")
        self.assertNotIn("urllib.request", serpapi_source)


# ---------------------------------------------------------------------------
# H. The real Gateway, when this branch can see it
# ---------------------------------------------------------------------------

class _Contract:
    """Minimal injected stand-in for `research_contract`. Test-only by design."""

    class OutcomeCode:
        OK = "OK"
        BLOCKED = "BLOCKED"
        FAILED = "FAILED"
        UNKNOWN = "UNKNOWN"

    class AdapterResult:
        def __init__(self, outcome, caveats=(), **kwargs):
            self.outcome = outcome
            self.caveats = tuple(caveats)
            for key, value in kwargs.items():
                setattr(self, key, value)

    class CapabilityRequest:
        def __init__(self, **kwargs):
            self.__dict__.update(kwargs)


class _Entry:
    def __init__(self, capability_id, provider, transport, tier, allowed_params,
                 availability="ENABLED"):
        self.capability_id = capability_id
        self.provider = provider
        self.transport = transport
        self.tier = tier
        self.allowed_params = frozenset(allowed_params)
        self.availability = availability


class _Registry:
    def __init__(self, entries):
        self._entries = {e.capability_id: e for e in entries}

    def get(self, capability_id):
        return self._entries.get(capability_id)


class _Policy:
    def evaluate(self, request):
        return type("D", (), {"verdict": "ALLOW", "reason_code": ""})()


class _Ledger:
    """Records the ledger verbs the *current* Gateway calls, because ordering is
    the property under test.

    The Gateway's ledger interface moved during this Wave: terminal writes now use
    the replacement ledger's verbs (``prove_no_charge`` / ``mark_unknown(reason=)``
    / ``settle(operation_id, actual_micros=...)``) rather than
    ``settle(id, result)``. Mirroring those verbs is what makes this fake a
    conformance check against the live seam instead of against a memory of it.
    """

    def __init__(self):
        self.reserved = []
        self.settled = []
        self.unknowns = []
        self.no_charge = []
        self.intents = []

    def reserve(self, request, decision=None):
        # Older revisions passed (request, decision); current ones pass the
        # translated request alone. Accepting both keeps this fake about ordering.
        self.reserved.append(getattr(request, "capability_id", "?"))
        return type("R", (), {"ledger_id": "lg-1", "operation_id": "lg-1"})()

    def get(self, operation_id):
        return None

    def mark_dispatch_intent(self, operation_id):
        self.intents.append(operation_id)

    def prove_no_charge(self, operation_id, *, reason=""):
        self.no_charge.append((operation_id, reason))

    def mark_unknown(self, operation_id, *, reason="", **kwargs):
        self.unknowns.append((operation_id, reason))

    def settle(self, operation_id, *, actual_micros=None, actual_source=None,
               upstream_ref="", quota_units=None):
        self.settled.append((operation_id, actual_micros))


@unittest.skipUnless(importlib.util.find_spec("research_gateway") is not None,
                     "research_gateway is not on this branch — the Gateway-level run "
                     "happens at integration; the properties it asserts are covered "
                     "in-process by the classes above")
class GatewayConformanceTests(unittest.TestCase):
    def _gateway(self, adapters, entries):
        import research_gateway
        self.ledger = _Ledger()
        return research_gateway.CapabilityGateway(
            registry=_Registry(entries), policy=_Policy(), ledger=self.ledger,
            adapters=adapters, contract=_Contract)

    def test_the_gateway_completes_a_serper_search_through_this_adapter(self):
        server = FakeSerperServer({"organic": [{"title": "T", "link": "https://e.org"}]})
        gateway = self._gateway(
            {"serper": serper.SerperAdapter(api_key="k", transport=server)},
            [_Entry("serper.search", "serper", "rest", "T2",
                    {"query", "country", "language", "limit", "page", "mode"})])
        outcome = gateway.invoke(
            capability_id="serper.search",
            params={"query": "venues", "country": "GB", "language": "en"},
            run_id="run-1", operation_id="op-1", account_ref="acct-1",
            intent_fingerprint="intent-1")
        self.assertEqual("OK", outcome.status)
        self.assertEqual(["serper.search"], self.ledger.reserved)
        self.assertEqual(1, len(server.requests))

    def test_the_gateway_surfaces_an_adapter_refusal_as_a_closed_set_code(self):
        gateway = self._gateway(
            {"serper": serper.SerperAdapter(api_key="k", transport=FakeSerperServer())},
            [_Entry("serper.search", "serper", "rest", "T2", {"query", "country", "language"})])
        outcome = gateway.invoke(
            capability_id="serper.search", params={"query": "venues", "url": "https://evil"},
            run_id="run-1", operation_id="op-1", account_ref="a", intent_fingerprint="i")
        self.assertEqual("BLOCKED", outcome.status)
        self.assertEqual("CAPABILITY_PARAM_NOT_ALLOWED", outcome.reason_code)

    def test_an_ambiguous_timeout_holds_the_exposure_instead_of_settling_it(self):
        """The one accounting outcome this branch can prove without a ledger.

        A request that left with no answer is an unresolved charge. The Gateway
        must hold it — `mark_unknown` — and must not `settle` it, because a settle
        writes a financial state and we have none. A fake ledger is enough to
        assert which of the two verbs was called; the durable record itself is
        W-B's and is not faked into existence here.
        """
        import socket
        server = FakeSerperServer(raises=socket.timeout("read timed out"))
        gateway = self._gateway(
            {"serper": serper.SerperAdapter(api_key="k", transport=server)},
            [_Entry("serper.search", "serper", "rest", "T2",
                    {"query", "country", "language"})])
        outcome = gateway.invoke(
            capability_id="serper.search",
            params={"query": "venues", "country": "GB", "language": "en"},
            run_id="run-1", operation_id="op-1", account_ref="acct-1",
            intent_fingerprint="intent-1")
        self.assertEqual("UNKNOWN", outcome.status)
        self.assertEqual("UNKNOWN_CHARGE_OUTSTANDING", outcome.reason_code)
        self.assertEqual(1, len(self.ledger.reserved))
        self.assertEqual([], self.ledger.settled,
                         "nothing may settle a charge that was never answered")
        self.assertEqual([], self.ledger.no_charge,
                         "and nothing may claim no charge was incurred")
        self.assertEqual(1, len(self.ledger.unknowns))
        self.assertEqual(1, len(server.requests), "one attempt, one request")

    def test_the_gateway_never_dispatches_without_a_reservation(self):
        gateway = self._gateway(
            {"serper": serper.SerperAdapter(api_key="k", transport=FakeSerperServer())},
            [_Entry("serper.search", "serper", "rest", "T2", {"query"}, availability="DISABLED")])
        outcome = gateway.invoke(
            capability_id="serper.search", params={"query": "venues"},
            run_id="run-1", operation_id="op-1", account_ref="a", intent_fingerprint="i")
        self.assertEqual("BLOCKED", outcome.status)
        self.assertEqual("CAPABILITY_NOT_AVAILABLE", outcome.reason_code)
        self.assertEqual([], self.ledger.reserved)

    # -- the Wave-2 deliverable, exercised through the real Gateway ----------
    def _serpapi_adapter(self, tmp):
        import asyncio
        session = FakeMcpSession(engines=("google_light", "google_maps"))
        catalog = catalog_module.EngineCatalog(Path(tmp) / "state" / "c.json")
        asyncio.run(catalog_module.discover_index(session, catalog=catalog))
        asyncio.run(catalog_module.discover_schema(session, "google_light", catalog=catalog))
        session.calls.clear()
        return serpapi.SerpApiAdapter(session=session, catalog=catalog), session

    def test_the_gateway_completes_a_serpapi_engine_catalog_read(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            adapter, session = self._serpapi_adapter(tmp)
            gateway = self._gateway(
                {"serpapi": adapter},
                [_Entry("serpapi.engine_catalog", "serpapi", "official_mcp", "T2", set())])
            outcome = gateway.invoke(
                capability_id="serpapi.engine_catalog", params={},
                run_id="run-1", operation_id="op-1", account_ref="acct-1",
                intent_fingerprint="intent-1")
        self.assertEqual("OK", outcome.status)
        self.assertEqual(["serpapi.engine_catalog"], self.ledger.reserved)
        reads = [c for c in session.calls if c[0] == "read_resource"]
        self.assertEqual([catalog_module.INDEX_URI], [c[1] for c in reads],
                         "discovery reads the index and nothing else")

    def test_the_gateway_completes_a_serpapi_search(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            adapter, session = self._serpapi_adapter(tmp)
            gateway = self._gateway(
                {"serpapi": adapter},
                [_Entry("serpapi.search", "serpapi", "official_mcp", "T2",
                        {"engine", "query", "country", "language", "limit"})])
            outcome = gateway.invoke(
                capability_id="serpapi.search",
                params={"engine": "google_light", "query": "public sports venues Devon",
                        "country": "GB", "language": "en", "limit": 3},
                run_id="run-1", operation_id="op-1", account_ref="acct-1",
                intent_fingerprint="intent-1")
        self.assertEqual("OK", outcome.status)
        tools = [c[1] for c in session.calls if c[0] == "call_tool"]
        self.assertEqual(["search"], tools)

    def test_a_route_parameter_is_refused_by_the_adapter_and_settled_as_no_charge(self):
        """Defence in depth, plus the ordering the Gateway documents.

        ``endpoint`` is listed in this entry's ``allowed_params`` on purpose, so
        the Registry's own subset check does **not** catch it — the adapter has to.
        That is the case worth testing: an entry reviewed too loosely must not
        become a route the host chose.

        The Gateway reserves before dispatching, by construction. So the property
        to assert is not "nothing was reserved" but "the reservation did not stay
        open": a provably-refused call that left its exposure held would be a
        phantom against the run's ceiling.
        """
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            adapter, session = self._serpapi_adapter(tmp)
            gateway = self._gateway(
                {"serpapi": adapter},
                [_Entry("serpapi.search", "serpapi", "official_mcp", "T2",
                        {"engine", "query", "country", "language", "endpoint"})])
            outcome = gateway.invoke(
                capability_id="serpapi.search",
                params={"engine": "google_light", "query": "x y", "country": "GB",
                        "language": "en", "endpoint": "https://attacker.example/mcp"},
                run_id="run-1", operation_id="op-1", account_ref="acct-1",
                intent_fingerprint="intent-1")
        self.assertEqual("BLOCKED", outcome.status)
        self.assertEqual("CAPABILITY_PARAM_NOT_ALLOWED", outcome.reason_code)
        self.assertEqual([], session.calls, "the session was never asked anything")
        self.assertEqual(["serpapi.search"], self.ledger.reserved,
                         "the Gateway reserves before dispatch, by construction")
        self.assertEqual(1, len(self.ledger.no_charge),
                         "a proven pre-dispatch refusal is settled as no-charge")
        self.assertEqual([], self.ledger.settled,
                         "and it is not booked as a spend")


if __name__ == "__main__":
    unittest.main()
