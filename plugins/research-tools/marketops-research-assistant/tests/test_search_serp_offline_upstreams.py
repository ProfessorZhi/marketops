"""The wire contract with two fake upstreams, asserted on our bytes.

Two deterministic stand-ins, neither of which opens a socket:

* :class:`FakeOfficialMcpSession` answers ``resources/list``, ``resources/read``
  and ``call_tool("search", ...)`` — the exact MCP surface SerpApi's official
  server documents, without contacting it.
* :class:`FakeSerperRest` answers the allowlisted Serper endpoint and records the
  request that arrived.

The point is not "the happy path returns data". It is that the *shape we send* is
the shape we claim: the engine we ask for came from the catalog index, the tool
name is a constant, the Serper path comes from the declared registry, and no
free-form host, path or tool name can reach either upstream.

What these fakes deliberately cannot show: that the real vendor accepts these
bytes. That needs `UPSTREAM_SCHEMA_VERIFIED`, which this Wave has no
authorization for — so everything here is `OFFLINE_TESTED`, never higher.
"""
from __future__ import annotations

import asyncio
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import research_adapter_serpapi as serpapi
import research_adapter_serper as serper
import serpapi_engine_catalog as catalog_module
import serper_modes


class Request:
    def __init__(self, capability_id, params=None, **overrides):
        self.capability_id = capability_id
        self.params = dict(params or {})
        self.run_id = overrides.get("run_id", "run-1")
        self.operation_id = overrides.get("operation_id", "op-1")
        self.account_ref = overrides.get("account_ref", "acct-1")
        self.intent_fingerprint = overrides.get("intent_fingerprint", "intent-1")
        self.grant_ref = overrides.get("grant_ref", "grant-1")


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


class FakeOfficialMcpSession:
    """The three MCP methods this adapter depends on, and nothing else."""

    def __init__(self, *, engines=("google_light", "google_maps"),
                 search_payload=None, tool_is_error=False, tools=("search",)):
        self.tools = tuple(tools)
        self.engines = tuple(engines)
        self.schemas = {name: {"type": "object", "properties": {"q": {"type": "string"}}}
                        for name in engines}
        self.search_payload = search_payload if search_payload is not None else {
            "search_metadata": {"status": "Success"},
            "organic_results": [{"title": "Example Club", "link": "https://example.org",
                                 "snippet": "football pitch"}]}
        self.tool_is_error = tool_is_error
        self.calls = []

    async def list_tools(self):
        self.calls.append(("list_tools",))
        tools = [type("Tool", (), {"name": name})() for name in self.tools]
        return type("R", (), {"tools": tools})()

    async def list_resources(self):
        self.calls.append(("list_resources",))
        return type("R", (), {"resources": [
            FakeResource(catalog_module.INDEX_URI, name="index"),
            FakeResource(None, catalog_module.SCHEMA_URI_TEMPLATE, name="schema"),
        ]})()

    async def read_resource(self, uri):
        self.calls.append(("read_resource", uri))
        if uri == catalog_module.INDEX_URI:
            body = json.dumps({"engines": list(self.engines)})
        else:
            body = json.dumps(self.schemas[uri.rsplit("/", 1)[-1]])
        return type("R", (), {"contents": [FakeContent(uri, body)]})()

    async def call_tool(self, name, arguments):
        self.calls.append(("call_tool", name,
                           json.dumps(arguments, sort_keys=True, ensure_ascii=True)))
        result = type("Result", (), {})()
        result.isError = self.tool_is_error
        result.structuredContent = None
        result.content = [FakeContent("mcp://tool", json.dumps(self.search_payload))]
        return result


class FakeHttpResponse:
    """A real class, because ``with`` resolves ``__enter__`` on the type."""

    def __init__(self, raw: bytes, status: int = 200):
        import io
        self.status = status
        self._stream = io.BytesIO(raw)

    def read(self, n):
        return self._stream.read(n)

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self._stream.close()


class FakeSerperRest:
    """Records the request; answers from a canned body."""

    def __init__(self, payload=None, status=200, raw=None):
        self.requests = []
        self.payload = payload if payload is not None else {"organic": []}
        self.status = status
        self.raw = raw

    def __call__(self, req, timeout):
        self.requests.append({
            "url": req.full_url, "method": req.get_method(),
            "body": json.loads(req.data) if req.data else None,
            "api_key": req.get_header("X-api-key"),
            "content_type": req.get_header("Content-type"),
        })
        if self.status != 200:
            import urllib.error
            raise urllib.error.HTTPError(req.full_url, self.status, "e", None, None)
        raw = self.raw if self.raw is not None else json.dumps(self.payload).encode("utf-8")
        return FakeHttpResponse(raw, self.status)


class UpstreamFixture(unittest.TestCase):
    def setUp(self):
        self._dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._dir.cleanup)
        self.root = Path(self._dir.name)

    def loaded_adapter(self, session=None, engines=("google_light", "google_maps")):
        session = session or FakeOfficialMcpSession(engines=engines)
        catalog = catalog_module.EngineCatalog(self.root / "state" / "c.json")
        asyncio.run(catalog_module.discover_index(session, catalog=catalog))
        # `serpapi.search` requires the engine's reviewed schema, so a fixture
        # that means "search is reachable" must read one. Both reads are setup,
        # not what a test measures, so the log is cleared rather than mentally
        # subtracted.
        asyncio.run(catalog_module.discover_schema(session, engines[0], catalog=catalog))
        session.calls.clear()
        return serpapi.SerpApiAdapter(session=session, catalog=catalog), session


# ---------------------------------------------------------------------------
# SerpApi: engine resources are read lazily, and the search tool is a constant
# ---------------------------------------------------------------------------

class SerpApiResourceContractTests(UpstreamFixture):
    def test_reading_the_catalog_reads_the_index_once_and_no_engine_schema(self):
        adapter, session = self.loaded_adapter()
        result = adapter.execute(Request("serpapi.engine_catalog"))
        self.assertEqual("OK", result.outcome)
        reads = [c for c in session.calls if c[0] == "read_resource"]
        self.assertEqual(1, len(reads))
        self.assertEqual(catalog_module.INDEX_URI, reads[0][1])
        self.assertEqual(2, result.payload["engine_count"])

    def test_the_catalog_payload_carries_no_schema_body(self):
        adapter, _ = self.loaded_adapter()
        result = adapter.execute(Request("serpapi.engine_catalog"))
        blob = json.dumps(result.payload, ensure_ascii=False)
        self.assertNotIn("properties", blob)
        self.assertIn("google_light", blob)

    def test_one_engine_schema_is_read_once_and_then_served_from_cache(self):
        adapter, session = self.loaded_adapter()
        first = adapter.execute(Request("serpapi.engine_schema", {"engine": "google_light"}))
        self.assertEqual("OK", first.outcome)
        before = len([c for c in session.calls if c[0] == "read_resource"])
        second = adapter.execute(Request("serpapi.engine_schema", {"engine": "google_light"}))
        after = len([c for c in session.calls if c[0] == "read_resource"])
        self.assertEqual(before, after, "a cache hit must not re-read the vendor")
        self.assertEqual("CACHE_HIT", second.payload["source"])

    def test_an_engine_schema_for_an_unknown_engine_is_blocked(self):
        adapter, session = self.loaded_adapter(engines=("google_light",))
        result = adapter.execute(Request("serpapi.engine_schema", {"engine": "google_flights"}))
        self.assertEqual("BLOCKED", result.outcome)
        self.assertEqual("CAPABILITY_UNKNOWN", result.caveats[0])
        self.assertEqual([], session.calls)

    def test_refresh_is_a_boolean_or_it_is_not_a_refresh(self):
        adapter, _ = self.loaded_adapter()
        result = adapter.execute(Request("serpapi.engine_schema",
                                         {"engine": "google_light", "refresh": "yes"}))
        self.assertEqual("BLOCKED", result.outcome)
        self.assertEqual("CAPABILITY_PARAM_NOT_ALLOWED", result.caveats[0])


class SerpApiSearchContractTests(UpstreamFixture):
    def test_search_calls_the_declared_tool_with_compact_mode_and_the_catalog_engine(self):
        adapter, session = self.loaded_adapter()
        result = adapter.execute(Request("serpapi.search", {
            "engine": "google_light", "query": "public sports venues Devon",
            "country": "GB", "language": "en", "limit": 5}))
        self.assertEqual("OK", result.outcome)
        calls = [c for c in session.calls if c[0] == "call_tool"]
        self.assertEqual(1, len(calls))
        _, tool, raw = calls[0]
        self.assertEqual("search", tool, "the tool name is a constant, never a parameter")
        arguments = json.loads(raw)
        self.assertEqual({"params", "mode"}, set(arguments))
        self.assertEqual("compact", arguments["mode"])
        self.assertEqual("google_light", arguments["params"]["engine"])
        self.assertEqual("public sports venues Devon", arguments["params"]["q"])
        self.assertEqual("gb", arguments["params"]["gl"])
        self.assertEqual("en", arguments["params"]["hl"])

    def test_no_other_upstream_tool_is_reachable(self):
        adapter, session = self.loaded_adapter()
        adapter.execute(Request("serpapi.search", {"engine": "google_light", "query": "x y",
                                             "country": "GB", "language": "en"}))
        tools = {c[1] for c in session.calls if c[0] == "call_tool"}
        self.assertEqual({"search"}, tools)

    def test_an_engine_absent_from_the_index_never_reaches_the_session(self):
        adapter, session = self.loaded_adapter(engines=("google_light",))
        result = adapter.execute(Request("serpapi.search",
                                         {"engine": "google_maps", "query": "x y",
                                             "country": "GB", "language": "en"}))
        self.assertEqual("BLOCKED", result.outcome)
        self.assertEqual("CAPABILITY_UNKNOWN", result.caveats[0])
        self.assertEqual([], session.calls)

    def test_an_upstream_tool_error_is_a_failure_not_a_block(self):
        session = FakeOfficialMcpSession(tool_is_error=True)
        adapter, _ = self.loaded_adapter(session=session)
        result = adapter.execute(Request("serpapi.search",
                                         {"engine": "google_light", "query": "x y",
                                             "country": "GB", "language": "en"}))
        self.assertEqual("FAILED", result.outcome)
        self.assertIn(result.caveats[0], serpapi.POST_DISPATCH_CODES)

    def test_a_search_result_is_a_discovery_candidate_and_says_so(self):
        adapter, _ = self.loaded_adapter()
        result = adapter.execute(Request("serpapi.search",
                                         {"engine": "google_light", "query": "x y",
                                             "country": "GB", "language": "en"}))
        self.assertTrue(result.payload["business_evidence"] is False)
        self.assertEqual("discovered_not_verified", result.payload["source_status"])


# ---------------------------------------------------------------------------
# Serper: the REST shape comes from the declared registry
# ---------------------------------------------------------------------------

class SerperRestContractTests(unittest.TestCase):
    def _adapter(self, server):
        return serper.SerperAdapter(api_key="synthetic-key", transport=server)

    def test_the_default_request_is_the_pre_registry_shape(self):
        server = FakeSerperRest()
        self._adapter(server).execute(Request("serper.search", {
            "query": "public sports venues Devon", "country": "GB", "language": "en",
            "limit": 3, "mode": "web"}))
        request = server.requests[0]
        self.assertEqual("https://google.serper.dev/search", request["url"])
        self.assertEqual("POST", request["method"])
        self.assertEqual({"q", "gl", "hl", "num"}, set(request["body"]))
        self.assertEqual({"q": "public sports venues Devon", "gl": "gb", "hl": "en",
                          "num": 3}, request["body"])
        self.assertEqual("synthetic-key", request["api_key"])

    def test_every_declared_servable_mode_reaches_its_declared_path(self):
        for mode in serper_modes.ALL_MODES:
            spec = serper_modes.mode_spec(mode)
            if spec.dispatch != "OK":
                continue
            with self.subTest(mode=mode):
                server = FakeSerperRest()
                self._adapter(server).execute(Request("serper.search", {
                    "query": "venues", "country": "GB", "language": "en", "mode": mode}))
                self.assertEqual(serper_modes.HOST + spec.endpoint_path,
                                 server.requests[0]["url"])

    def test_page_two_adds_the_declared_parameter_and_page_one_does_not(self):
        server = FakeSerperRest()
        adapter = self._adapter(server)
        adapter.execute(Request("serper.search", {
            "query": "venues", "country": "GB", "language": "en", "page": 1}))
        self.assertNotIn("page", server.requests[0]["body"])
        adapter.execute(Request("serper.search", {
            "query": "venues", "country": "GB", "language": "en", "page": 2}))
        self.assertEqual(2, server.requests[1]["body"]["page"])

    def test_a_no_redirect_opener_is_installed_by_default(self):
        adapter = serper.SerperAdapter(api_key="k")
        opener = adapter._opener  # noqa: SLF001 - the boundary under test
        handlers = getattr(opener, "handlers", ())
        self.assertTrue(any(isinstance(h, serper.NoRedirect) for h in handlers),
                        "an unredirected POST is the only request this adapter may make")

    def test_the_adapter_refuses_to_follow_a_redirect_at_all(self):
        with self.assertRaisesRegex(ValueError, "PROVIDER_REDIRECT_BLOCKED"):
            serper.NoRedirect().redirect_request(None, None, 302, "Moved", {},
                                                 "https://untrusted.example/")

    def test_an_oversized_body_is_refused_rather_than_buffered(self):
        server = FakeSerperRest(raw=b"x" * (serper.MAX_BODY + 10))
        result = self._adapter(server).execute(Request("serper.search", {
            "query": "venues", "country": "GB", "language": "en"}))
        self.assertEqual("FAILED", result.outcome)
        self.assertIn("SERPER_BODY_TOO_LARGE", result.caveats[0])

    def test_a_non_json_body_is_a_failure_not_an_empty_result(self):
        server = FakeSerperRest(raw=b"<html>challenge</html>")
        result = self._adapter(server).execute(Request("serper.search", {
            "query": "venues", "country": "GB", "language": "en"}))
        self.assertEqual("FAILED", result.outcome)
        self.assertIn(result.caveats[0], serper.POST_DISPATCH_CODES)

    def test_the_receipt_carries_the_declared_pricing_source(self):
        server = FakeSerperRest({"organic": []})
        result = self._adapter(server).execute(Request("serper.search", {
            "query": "venues", "country": "GB", "language": "en", "mode": "images"}))
        self.assertEqual(serper_modes.PRICING_SOURCE, result.payload["pricing_source"])
        self.assertIsNone(result.payload["unit_price_usd"])


if __name__ == "__main__":
    unittest.main()
