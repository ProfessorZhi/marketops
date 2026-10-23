"""Wave-4: the quota meter reaches the campaign floor, and drift fails closed.

Two things a search adapter can get right in isolation and wrong in the product:

1. **A credit meter that only speaks USD.** B's ledger encodes "nobody has a
   verified dollar rate" as ``unit_micros == 0, free_tier == False`` — not as
   free. A quota reading that never reaches a ``Price`` leaves the campaign floor
   judging a meter it cannot see, so a doubled-credit call moves nothing. The
   meter has to be expressible in the vendor's own unit *and* the floor has to be
   computable in that unit, with the dollar side honestly unknown.

2. **A schema that drifted and kept being used.** SerpApi publishes engine
   parameter schemas as resources; an engine whose schema changed after we
   accepted it is an engine whose required parameters we no longer know. At T2
   that must fail closed — not "carry on with a caveat".

Plus the two defaults the dispatch names: engine resources are **not reviewed**
(absurd to claim otherwise without a live handshake), and an Agent cannot name a
tool, a path or a URI through a category or engine string.

No socket, no credential, no vendor.
"""
from __future__ import annotations

import asyncio
import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import research_adapter_serpapi as serpapi
import research_adapter_serper as serper
import search_canonical as canonical
import serpapi_engine_catalog as catalog_module
import serper_modes

HAS_LEDGER = importlib.util.find_spec("research_usage_ledger") is not None


class Request:
    def __init__(self, capability_id, params=None, **overrides):
        self.capability_id = capability_id
        self.params = dict(params or {})
        self.run_id = overrides.get("run_id", "run-1")
        self.operation_id = overrides.get("operation_id", "op-1")
        self.account_ref = overrides.get("account_ref", "acct-1")
        self.intent_fingerprint = overrides.get("intent_fingerprint", "intent-1")
        self.grant_ref = overrides.get("grant_ref", "grant-1")


# ---------------------------------------------------------------------------
# 1. The quota meter, in B's Price shape
# ---------------------------------------------------------------------------

class QuotaMeterTests(unittest.TestCase):
    def test_the_estimate_speaks_the_vendor_unit_not_only_dollars(self):
        estimate = serper_modes.credit_estimate("images", num=100)
        self.assertEqual("credits", estimate["quota_unit"])
        self.assertEqual(2, estimate["quota_per_call"])
        self.assertEqual(0, estimate["unit_micros"], "no verified dollar rate")
        self.assertIs(estimate["free_tier"], False, "unknown is not free")
        self.assertIs(estimate["verified"], False)

    def test_a_call_inside_the_rule_costs_one_unit(self):
        for num in (1, 10, None):
            with self.subTest(num=num):
                self.assertEqual(1, serper_modes.credit_estimate(
                    "images", num=num)["quota_per_call"])

    def test_the_price_fields_are_exactly_the_ledgers_kwargs(self):
        fields = serper_modes.price_fields("images", num=100)
        self.assertEqual(
            {"provider", "capability", "unit_micros", "quota_unit", "quota_per_call",
             "source", "version", "observed_at", "free_tier", "verified"}, set(fields))
        self.assertEqual("serper", fields["provider"])
        self.assertEqual("serper.search", fields["capability"])
        self.assertIn(fields["quota_unit"], ("credits",))

    def test_the_observation_time_is_a_timezone_carrying_timestamp(self):
        # B's Price rejects a bare date; the reading is date-precision, so it is
        # encoded at the start of that day and labelled as such rather than
        # quietly acquiring an hour nobody observed.
        fields = serper_modes.price_fields("images", num=100)
        from datetime import datetime
        parsed = datetime.fromisoformat(fields["observed_at"])
        self.assertIsNotNone(parsed.tzinfo)
        self.assertTrue(fields["observed_at"].startswith(serper_modes.PRICED_AT))
        self.assertIn("DATE_PRECISION", serper_modes.PRICING_PRECISION)

    @unittest.skipUnless(HAS_LEDGER, "research_usage_ledger is B's module and is not on this branch")
    def test_the_ledger_accepts_the_fields_and_calls_the_dollar_side_unknown(self):
        import research_usage_ledger as ledger
        price = ledger.Price(**serper_modes.price_fields("images", num=100))
        self.assertFalse(ledger._usd_price_is_known(price),
                         "unknown USD must not be read as a zero price")
        self.assertEqual(2, price.quota_per_call)

    @unittest.skipUnless(HAS_LEDGER, "research_usage_ledger is B's module and is not on this branch")
    def test_the_campaign_floor_moves_on_credits_while_usd_is_unknown(self):
        import research_usage_ledger as ledger
        baseline = ledger.CampaignBaseline(
            provider="serper", account_ref="acct-1", meter="credits", units=100,
            observed_at="2026-10-17T00:00:00+00:00",
            source=ledger.MeterReadingSource.VENDOR_ACCOUNT_READ,
            campaign_id="campaign-1")
        floor = ledger.CampaignFloor(baseline=baseline)
        self.assertEqual(20, floor.floor_units)
        self.assertEqual(80, floor.spendable_units)

        # One doubled-credit call consumes two of the spendable units. The point
        # is that this is visible *without* a dollar price: before the meter was
        # expressible in credits, the same call moved a USD floor by nothing.
        price = ledger.Price(**serper_modes.price_fields("images", num=100))
        self.assertEqual(2, price.quota_per_call)
        self.assertFalse(ledger._usd_price_is_known(price))
        self.assertEqual(78, floor.spendable_units - price.quota_per_call)

    def test_the_receipt_carries_the_same_reading(self):
        class FakeResponse:
            status = 200

            def __init__(self):
                import io
                self._s = io.BytesIO(b'{"images": []}')

            def read(self, n):
                return self._s.read(n)

            def __enter__(self):
                return self

            def __exit__(self, *a):
                self._s.close()

        server = lambda req, timeout: FakeResponse()
        result = serper.SerperAdapter(api_key="k", transport=server).execute(
            Request("serper.search", {"query": "venues", "country": "GB",
                                      "language": "en", "mode": "images", "limit": 20}))
        self.assertEqual(2, result.payload["credit_estimate"]["quota_per_call"])
        self.assertEqual("credits", result.payload["credit_estimate"]["quota_unit"])
        self.assertEqual(serper_modes.price_fields("images", num=20),
                         result.payload["price_fields"])


# ---------------------------------------------------------------------------
# 2. Schema drift fails closed at T2
# ---------------------------------------------------------------------------

class FakeContent:
    def __init__(self, uri, text):
        self.uri = uri
        self.mimeType = "application/json"
        self.text = text


class FakeResource:
    def __init__(self, uri=None, uri_template=None, name=""):
        self.uri = uri
        self.uriTemplate = uri_template
        self.name = name


class FakeSession:
    def __init__(self, engines=("google_light",), schemas=None, tools=("search",)):
        self.engines = tuple(engines)
        self.schemas = schemas or {name: {"type": "object"} for name in engines}
        self.tools = tuple(tools)
        self.calls = []

    async def list_tools(self):
        self.calls.append(("list_tools",))
        return type("R", (), {"tools": [type("T", (), {"name": n})() for n in self.tools]})()

    async def list_resources(self):
        self.calls.append(("list_resources",))
        return type("R", (), {"resources": [
            FakeResource(catalog_module.INDEX_URI, name="index"),
            FakeResource(None, catalog_module.SCHEMA_URI_TEMPLATE, name="schema")]})()

    async def read_resource(self, uri):
        self.calls.append(("read_resource", uri))
        if uri == catalog_module.INDEX_URI:
            body = json.dumps({"engines": list(self.engines)})
        else:
            body = json.dumps(self.schemas[uri.rsplit("/", 1)[-1]])
        return type("R", (), {"contents": [FakeContent(uri, body)]})()

    async def call_tool(self, name, arguments):
        self.calls.append(("call_tool", name))
        result = type("Result", (), {})()
        result.isError = False
        result.structuredContent = None
        result.content = [FakeContent("mcp://tool", json.dumps({"organic_results": []}))]
        return result


class SchemaDriftTests(unittest.TestCase):
    def setUp(self):
        self._dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._dir.cleanup)
        self.path = Path(self._dir.name) / "state" / "c.json"

    def _catalog(self, schema=None, engines=("google_light",)):
        session = FakeSession(engines=engines,
                              schemas={"google_light": schema or {"type": "object"}})
        catalog = catalog_module.EngineCatalog(self.path)
        asyncio.run(catalog_module.discover_index(session, catalog=catalog))
        return catalog, session

    def test_a_first_read_is_unverified_because_nothing_was_compared(self):
        catalog, _ = self._catalog()
        self.assertEqual("UNVERIFIED", catalog.schema_verdict(
            "google_light", json.dumps({"type": "object"})))

    def test_a_matching_read_is_a_match(self):
        catalog, session = self._catalog()
        asyncio.run(catalog_module.discover_schema(session, "google_light", catalog=catalog))
        self.assertEqual("MATCH", catalog.schema_verdict(
            "google_light", json.dumps({"type": "object"})))

    def test_a_changed_read_is_drift(self):
        catalog, session = self._catalog()
        asyncio.run(catalog_module.discover_schema(session, "google_light", catalog=catalog))
        self.assertEqual("DRIFT", catalog.schema_verdict(
            "google_light", json.dumps({"type": "object", "required": ["q"]})))

    def test_an_unreadable_read_is_absent_not_drift(self):
        catalog, session = self._catalog()
        asyncio.run(catalog_module.discover_schema(session, "google_light", catalog=catalog))
        self.assertEqual("ABSENT", catalog.schema_verdict("google_light", "{not json"))

    def test_a_drifting_refresh_refuses_and_keeps_the_accepted_pin(self):
        catalog, session = self._catalog()
        first = asyncio.run(catalog_module.discover_schema(
            session, "google_light", catalog=catalog))
        session.schemas["google_light"] = {"type": "object", "required": ["q"]}
        drifted = asyncio.run(catalog_module.discover_schema(
            session, "google_light", catalog=catalog, refresh=True))
        self.assertEqual("DRIFT", drifted["schema_verdict"])
        self.assertTrue(catalog.drifted("google_light"))
        # The accepted pin is untouched: a drifted body must not become the thing
        # we validate the next call against.
        self.assertEqual(first["sha256"],
                         (catalog.cached_schema("google_light") or {}).get("sha256"))

    def test_drift_survives_a_reload(self):
        catalog, session = self._catalog()
        asyncio.run(catalog_module.discover_schema(session, "google_light", catalog=catalog))
        session.schemas["google_light"] = {"type": "object", "required": ["q"]}
        asyncio.run(catalog_module.discover_schema(
            session, "google_light", catalog=catalog, refresh=True))
        reloaded = catalog_module.EngineCatalog(self.path)
        self.assertTrue(reloaded.drifted("google_light"))

    def test_drift_is_cleared_only_deliberately(self):
        catalog, session = self._catalog()
        asyncio.run(catalog_module.discover_schema(session, "google_light", catalog=catalog))
        session.schemas["google_light"] = {"type": "object", "required": ["q"]}
        asyncio.run(catalog_module.discover_schema(
            session, "google_light", catalog=catalog, refresh=True))
        catalog.clear_drift("google_light")
        self.assertFalse(catalog.drifted("google_light"))

    def test_a_search_on_a_drifted_engine_is_blocked_before_dispatch(self):
        catalog, session = self._catalog()
        asyncio.run(catalog_module.discover_schema(session, "google_light", catalog=catalog))
        session.schemas["google_light"] = {"type": "object", "required": ["q"]}
        adapter_session = FakeSession(engines=("google_light",),
                                      schemas=session.schemas)
        adapter = serpapi.SerpApiAdapter(session=adapter_session, catalog=catalog)
        adapter_session.calls.clear()
        # Provoke the drift through the adapter's own refresh path.
        asyncio.run(catalog_module.discover_schema(
            adapter_session, "google_light", catalog=catalog, refresh=True))
        adapter_session.calls.clear()
        result = adapter.execute(Request("serpapi.search", {
            "engine": "google_light", "query": "x y",
            "country": "GB", "language": "en"}))
        self.assertEqual("BLOCKED", result.outcome)
        self.assertEqual("CAPABILITY_SCHEMA_DRIFT", result.caveats[0])
        self.assertEqual([], adapter_session.calls, "fail closed before any dispatch")

    def test_a_drifting_schema_refresh_reports_both_hashes_and_refuses(self):
        catalog, session = self._catalog()
        adapter = serpapi.SerpApiAdapter(session=session, catalog=catalog)
        session.calls.clear()
        accepted = adapter.execute(Request("serpapi.engine_schema",
                                           {"engine": "google_light"}))
        self.assertEqual("OK", accepted.outcome)
        session.schemas["google_light"] = {"type": "object", "required": ["q"]}
        drifted = adapter.execute(Request("serpapi.engine_schema",
                                          {"engine": "google_light", "refresh": True}))
        self.assertEqual("BLOCKED", drifted.outcome)
        self.assertEqual("CAPABILITY_SCHEMA_DRIFT", drifted.caveats[0])
        self.assertNotEqual(drifted.payload["accepted_sha256"],
                            drifted.payload["observed_sha256"])


# ---------------------------------------------------------------------------
# 3. Reviewed defaults false, and cannot be claimed
# ---------------------------------------------------------------------------

class ReviewedResourcesTests(unittest.TestCase):
    def test_the_module_does_not_claim_the_resources_were_reviewed(self):
        self.assertIs(catalog_module.REVIEWED_ENGINE_RESOURCES, False)
        self.assertIn("NOT_REVIEWED", catalog_module.RESOURCE_REVIEW_NOTE)

    def test_the_adapter_reports_unreviewed_everywhere_it_speaks(self):
        adapter = serpapi.SerpApiAdapter(session=FakeSession())
        self.assertIs(adapter.probe_schema()["resources_reviewed"], False)
        for row in serpapi.describe_capabilities():
            self.assertIs(row["resources_reviewed"], False)

    def test_the_catalog_summary_says_unreviewed_and_lists_drift(self):
        with tempfile.TemporaryDirectory() as tmp:
            catalog = catalog_module.EngineCatalog(Path(tmp) / "state" / "c.json")
            summary = catalog.summary()
        self.assertIs(summary["reviewed"], False)
        self.assertEqual([], summary["drift"])
        self.assertIsNone(summary["engine_count"] or None)


# ---------------------------------------------------------------------------
# 4. A category or engine string is not a route
# ---------------------------------------------------------------------------

class RouteThroughValueTests(unittest.TestCase):
    HOSTILE_MODES = ("/search", "../search", "search?x=1", "https://x.example",
                     "places/reviews", "", "SEARCH")

    HOSTILE_ENGINES = ("google/light", "../engines", "https://mcp.serpapi.com/mcp",
                       "google_light?x=1", "GOOGLE_LIGHT", "")

    def test_a_mode_that_looks_like_a_path_is_not_a_category(self):
        for mode in self.HOSTILE_MODES:
            with self.subTest(mode=mode):
                with self.assertRaises(ValueError):
                    serper_modes.mode_spec(mode)

    def test_an_engine_that_looks_like_a_uri_is_not_an_engine(self):
        with tempfile.TemporaryDirectory() as tmp:
            catalog = catalog_module.EngineCatalog(Path(tmp) / "state" / "c.json")
            catalog.record_index(("google_light",))
            for engine in self.HOSTILE_ENGINES:
                with self.subTest(engine=engine):
                    decision = catalog_module.resolve_engine(engine, catalog=catalog)
                    self.assertEqual("BLOCKED", decision["status"])
                    self.assertEqual("CAPABILITY_PARAM_NOT_ALLOWED", decision["reason"])


# ---------------------------------------------------------------------------
# 5. The Wave-3 properties still hold
# ---------------------------------------------------------------------------

class PreservedPropertiesTests(unittest.TestCase):
    def test_one_query_is_one_key_across_both_serpapi_transports(self):
        def key(transport):
            return canonical.operation_signature(canonical.canonical_arguments(
                provider="serpapi", mode="web", query="public sports venues",
                country="GB", language="en", limit=10, page=1,
                engine="google_light", account="acct-1", transport=transport))
        self.assertEqual(key("REST_HTTPS"), key("EMBEDDED_OFFICIAL_MCP_HTTP"))

    def test_the_guard_holds_a_timeout_open(self):
        guard = canonical.DispatchGuard()
        guard.begin("k" * 64)
        guard.resolve("k" * 64, canonical.DISPATCH_UNKNOWN)
        self.assertEqual({"k" * 64: "UNKNOWN"}, guard.unresolved())
        with self.assertRaisesRegex(ValueError, "DUPLICATE_OPERATION"):
            guard.begin("k" * 64)


if __name__ == "__main__":
    unittest.main()
