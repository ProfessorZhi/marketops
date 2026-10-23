"""CD-03: one search identity both transports can produce.

The coordinator's CONTRACT-DECISION-DEDUP-IDENTITY.md splits the finding in two:

* **CD-01** — identity is ``provider + account + capability + canonical params``,
  never the transport, never the run. The ledger's ``route_shape`` already does
  this; it must not be weakened.
* **CD-03** — the *product* gap: the REST path hands the ledger
  ``{"query": "serpapi|search|pool|gb"}`` and the MCP path hands it
  ``{"action": "serpapi_mcp_search", "signature": "sig-0"}``. Neither shape can
  ever equal the other, so ``IL-2`` is unreachable no matter what any test says.

This module is the missing layer: one importable function that turns a search
request into the ``args`` mapping both adapters hand the ledger. The tests below
hold it to the three properties the decision names — same operation across
transports collapses, different parameters do not, and the two conservative
readings (UNKNOWN, legacy) are not turned into false blocks.

Everything runs against **B's real ledger** when the integration tree is on
``PYTHONPATH``; otherwise the ledger-backed cases skip with a reason rather than
pretending. No socket, no credential, no vendor.
"""
from __future__ import annotations

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

HAS_LEDGER = importlib.util.find_spec("research_usage_ledger") is not None


class Request:
    def __init__(self, capability_id, params=None, **overrides):
        self.capability_id = capability_id
        self.params = dict(params or {})
        self.run_id = overrides.get("run_id", "run-1")
        self.operation_id = overrides.get("operation_id", "op-1")
        self.account_ref = overrides.get("account_ref", "serpapi-main")
        self.intent_fingerprint = overrides.get("intent_fingerprint", "intent-1")
        self.grant_ref = overrides.get("grant_ref", "grant-1")


# ---------------------------------------------------------------------------
# 1. The shared args shape
# ---------------------------------------------------------------------------

class SharedArgsShapeTests(unittest.TestCase):
    def test_the_args_mapping_has_one_shape_and_three_keys(self):
        args = canonical.search_operation_args(
            query="devon surf club", country="GB", language="en")
        self.assertEqual({"capability", "query", "params"}, set(args))
        self.assertEqual("search", args["capability"])
        self.assertIsInstance(args["params"], dict)

    def test_the_stable_axes_are_always_present_and_materialised(self):
        # An omitted value and an explicitly-default value must not be two
        # operations: the second adapter would otherwise fork the identity just
        # by being explicit about a default.
        args = canonical.search_operation_args(
            query="devon surf club", country="GB", language="en")
        self.assertEqual(10, args["params"]["limit"])
        self.assertEqual(1, args["params"]["page"])
        explicit = canonical.search_operation_args(
            query="devon surf club", country="GB", language="en", limit=10, page=1)
        self.assertEqual(args, explicit)

    def test_query_normalisation_covers_whitespace_and_case(self):
        a = canonical.search_operation_args(query="  Devon   Surf   Club ",
                                            country="GB", language="en")
        b = canonical.search_operation_args(query="devon surf club",
                                            country="GB", language="en")
        self.assertEqual(a, b)
        self.assertEqual("devon surf club", b["query"])

    def test_locale_axes_are_normalised_not_passed_through(self):
        args = canonical.search_operation_args(
            query="q", country="gb", language="EN")
        self.assertEqual("GB", args["params"]["country"])
        self.assertEqual("en", args["params"]["language"])

    def test_every_real_parameter_moves_the_identity(self):
        base = canonical.search_operation_args(
            query="devon surf club", country="GB", language="en", mode="web")
        for overrides in ({"query": "cornwall surf club"},
                          {"country": "IE"},
                          {"language": "ga"},
                          {"limit": 20},
                          {"page": 2},
                          {"mode": "news"},
                          {"engine": "google_maps"}):
            with self.subTest(**{k: str(v) for k, v in overrides.items()}):
                changed = canonical.search_operation_args(**{**{
                    "query": "devon surf club", "country": "GB", "language": "en",
                    "mode": "web"}, **overrides})
                self.assertNotEqual(base, changed)

    def test_a_vendor_axis_that_does_not_exist_is_omitted_not_defaulted(self):
        # Serper has a category; SerpApi has an engine. A vendor that does not
        # have an axis must not acquire a fake value for it, or the two shapes
        # would differ in a field that means nothing.
        without = canonical.search_operation_args(
            query="q", country="GB", language="en")
        self.assertNotIn("mode", without["params"])
        self.assertNotIn("engine", without["params"])
        with_engine = canonical.search_operation_args(
            query="q", country="GB", language="en", engine="google_light")
        self.assertEqual("google_light", with_engine["params"]["engine"])

    def test_adapter_private_fields_are_refused_not_digested(self):
        # CD-03: `action` and `signature` belong to routing and receipts, not to
        # "what was bought". Letting them in silently would restore the exact
        # double-charge the decision exists to close.
        for name in ("action", "signature", "transport", "run_id", "adapter",
                     "tool", "tool_name", "endpoint", "url", "api_key", "key"):
            with self.subTest(field=name):
                with self.assertRaisesRegex(ValueError, "IDENTITY_FIELD_FORBIDDEN"):
                    canonical.search_operation_args(
                        query="q", country="GB", language="en", extra={name: "x"})

    def test_a_non_scalar_extra_is_refused(self):
        with self.assertRaisesRegex(ValueError, "IDENTITY_VALUE_NOT_SCALAR"):
            canonical.search_operation_args(
                query="q", country="GB", language="en",
                extra={"filters": {"nested": [1, 2]}})

    def test_an_empty_query_is_not_an_identity(self):
        for bad in ("", "   ", None, 7):
            with self.subTest(query=bad):
                with self.assertRaises(ValueError):
                    canonical.search_operation_args(
                        query=bad, country="GB", language="en")


class RouteIdentityTests(unittest.TestCase):
    def test_the_full_identity_is_the_ledgers_route_shape_inputs(self):
        identity = canonical.search_route_identity(
            provider="serpapi", account_ref="serpapi-main",
            query="devon surf club", country="GB", language="en",
            engine="google_light")
        self.assertEqual({"provider", "account_ref", "capability", "units",
                          "site", "args"}, set(identity))
        self.assertEqual("serpapi", identity["provider"])
        self.assertEqual(1, identity["units"])
        self.assertEqual("", identity["site"])

    def test_both_adapters_produce_the_same_structure(self):
        serper_identity = serper.SerperAdapter(api_key="k").route_identity(
            Request("serper.search", {"query": "devon surf club", "country": "GB",
                                      "language": "en"}, account_ref="serper-main"))
        serpapi_identity = serpapi.SerpApiAdapter().route_identity(
            Request("serpapi.search", {"query": "devon surf club", "country": "GB",
                                       "language": "en"}, account_ref="serpapi-main"))
        self.assertEqual(set(serper_identity), set(serpapi_identity))
        self.assertEqual(set(serper_identity["args"]), set(serpapi_identity["args"]))
        self.assertEqual(serper_identity["args"]["query"],
                         serpapi_identity["args"]["query"])
        self.assertEqual(serper_identity["args"]["capability"],
                         serpapi_identity["args"]["capability"])

    def test_the_adapter_identity_uses_the_vendors_own_axis(self):
        serper_identity = serper.SerperAdapter(api_key="k").route_identity(
            Request("serper.search", {"query": "q", "country": "GB",
                                      "language": "en", "mode": "news"}))
        serpapi_identity = serpapi.SerpApiAdapter().route_identity(
            Request("serpapi.search", {"query": "q", "country": "GB",
                                       "language": "en", "engine": "google_news"}))
        self.assertEqual("news", serper_identity["args"]["params"]["mode"])
        self.assertEqual("google_news", serpapi_identity["args"]["params"]["engine"])
        self.assertNotIn("engine", serper_identity["args"]["params"])
        self.assertNotIn("mode", serpapi_identity["args"]["params"])


# ---------------------------------------------------------------------------
# 2. Against B's real ledger
# ---------------------------------------------------------------------------

@unittest.skipUnless(HAS_LEDGER, "research_usage_ledger is B's module and is not on this branch")
class RealLedgerIdentityTests(unittest.TestCase):
    def setUp(self):
        import research_grants as grants
        import research_usage_ledger as usage
        self.grants = grants
        self.usage = usage
        self._dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._dir.cleanup)
        self.path = Path(self._dir.name) / "state" / "usage-ledger.sqlite3"
        from datetime import datetime, timedelta, timezone

        class Clock:
            def __init__(self):
                self.now = datetime(2026, 10, 18, 0, 0, tzinfo=timezone.utc)

            def __call__(self):
                return self.now

            def advance(self, **delta):
                self.now = self.now + timedelta(**delta)

        self.clock = Clock()
        registry = grants.CapabilityRegistry((
            grants.Capability("search", provider="serper", tier=grants.Tier.T2,
                              meter="search_query", effect="read_only"),
            grants.Capability("search", provider="serpapi", tier=grants.Tier.T2,
                              meter="search_query", effect="read_only"),
        ))
        pricing = usage.PricingCatalog((
            usage.Price(provider="serper", capability="search", unit_micros=750,
                        quota_unit="credits", quota_per_call=1, source="documented_rate",
                        version="2026-10-18", observed_at="2026-10-18T00:00:00+00:00"),
            usage.Price(provider="serpapi", capability="search", unit_micros=1000,
                        quota_unit="searches", quota_per_call=1, source="documented_rate",
                        version="2026-10-18", observed_at="2026-10-18T00:00:00+00:00"),
        ))
        self.ledger = usage.UsageLedger(self.path, pricing=pricing,
                                        registry=registry, clock=self.clock)
        self.open_caps()

    def open_caps(self):
        for kind, key in (("run", "devon/run-01"), ("run", "devon/run-02"),
                          ("run", "devon/run-03"), ("run", "devon/run-04"),
                          ("project", "devon"),
                          ("provider_account", "devon/serpapi/serpapi-main"),
                          ("provider_account", "devon/serper/serper-main")):
            self.ledger.set_cap(kind, key, ceiling_micros=5_000_000, ceiling_calls=20,
                                ceiling_seconds=3600, source="test-operator")

    def request(self, *, run_id="run-01", operation_id="op-01",
                transport="official_mcp", provider="serpapi", args=None,
                account_ref="serpapi-main"):
        return self.usage.OperationRequest(
            project="devon", run_id=run_id, operation_id=operation_id,
            provider=provider, capability="search", account_ref=account_ref,
            transport=transport, intent="find the official site of devon surf club",
            args=args or canonical.search_operation_args(
                query="devon surf club", country="GB", language="en",
                engine="google_light"))

    # -- the digest itself ---------------------------------------------------
    def test_one_operation_over_two_transports_is_one_route_shape(self):
        left = self.request(transport="rest_https")
        right = self.request(transport="official_mcp")
        self.assertEqual(self.usage.route_shape(left, left.args),
                         self.usage.route_shape(right, right.args),
                         "transport is how it is carried, not what is bought")

    def test_a_different_query_is_a_different_route_shape(self):
        left = self.request()
        right = self.request(args=canonical.search_operation_args(
            query="cornwall surf club", country="GB", language="en",
            engine="google_light"))
        self.assertNotEqual(self.usage.route_shape(left, left.args),
                            self.usage.route_shape(right, right.args))

    def test_a_different_engine_locale_or_depth_is_a_different_route_shape(self):
        base = self.request()
        base_shape = self.usage.route_shape(base, base.args)
        for args in (canonical.search_operation_args(
                         query="devon surf club", country="GB", language="en",
                         engine="google_maps"),
                     canonical.search_operation_args(
                         query="devon surf club", country="IE", language="en",
                         engine="google_light"),
                     canonical.search_operation_args(
                         query="devon surf club", country="GB", language="en",
                         engine="google_light", limit=20),
                     canonical.search_operation_args(
                         query="devon surf club", country="GB", language="en",
                         engine="google_light", page=2)):
            with self.subTest(args=json.dumps(args["params"], sort_keys=True)):
                other = self.request(args=args)
                self.assertNotEqual(base_shape, self.usage.route_shape(other, other.args))

    def test_a_real_route_shape_is_never_the_migration_default(self):
        # CD-05: rows that predate the column carry ''. A real shape is a 64-hex
        # digest, so it can never equal that placeholder — "we did not record it"
        # cannot become "we already bought it".
        shape = self.usage.route_shape(self.request(), self.request().args)
        self.assertRegex(shape, r"^[0-9a-f]{64}$")
        self.assertNotEqual("", shape)

    # -- the reserve path ----------------------------------------------------
    def test_the_same_operation_over_a_second_transport_is_refused(self):
        self.ledger.reserve(self.request(run_id="run-01", operation_id="op-01",
                                         transport="rest_https"))
        with self.assertRaises(self.usage.LedgerRefused) as caught:
            self.ledger.reserve(self.request(run_id="run-02", operation_id="op-02",
                                             transport="official_mcp"))
        self.assertEqual("DUPLICATE_OPERATION_SIGNATURE", caught.exception.code)

    def test_a_different_query_over_a_second_transport_is_allowed(self):
        # CD-02b, the anti-degradation half: "this project has touched this
        # provider" must not be the identity.
        self.ledger.reserve(self.request(run_id="run-01", operation_id="op-01",
                                         transport="rest_https"))
        other = self.request(
            run_id="run-02", operation_id="op-02", transport="official_mcp",
            args=canonical.search_operation_args(
                query="cornwall surf club", country="GB", language="en",
                engine="google_light"))
        reserved = self.ledger.reserve(other)
        self.assertEqual(self.usage.ChargeState.RESERVED, reserved.charge_state)

    def test_an_unknown_charge_does_not_block_a_different_query(self):
        # CD-04: UNKNOWN holds *this* operation's exposure; it is not a project-wide
        # "the vendor is closed" flag.
        first = self.ledger.reserve(self.request(run_id="run-01", operation_id="op-01",
                                                 transport="rest_https"))
        # B's ledger refuses `unknown` without a dispatch intent, and it is right
        # to: a reservation that never left is `prove_no_charge`, not `unknown`.
        self.ledger.mark_dispatch_intent("op-01")
        self.ledger.mark_unknown("op-01", reason="PROVIDER_TIMEOUT")
        self.assertEqual(self.usage.ChargeState.UNKNOWN,
                         self.ledger.get("op-01").charge_state)
        other = self.request(
            run_id="run-02", operation_id="op-02", transport="official_mcp",
            args=canonical.search_operation_args(
                query="cornwall surf club", country="GB", language="en",
                engine="google_light"))
        reserved = self.ledger.reserve(other)
        self.assertEqual(self.usage.ChargeState.RESERVED, reserved.charge_state)
        self.assertEqual("op-01", first.operation_id)

    def test_an_unknown_charge_still_holds_its_own_identity(self):
        self.ledger.reserve(self.request(run_id="run-01", operation_id="op-01",
                                         transport="rest_https"))
        self.ledger.mark_dispatch_intent("op-01")
        self.ledger.mark_unknown("op-01", reason="PROVIDER_TIMEOUT")
        with self.assertRaises(self.usage.LedgerRefused) as caught:
            self.ledger.reserve(self.request(run_id="run-02", operation_id="op-02",
                                             transport="official_mcp"))
        self.assertEqual("DUPLICATE_OPERATION_SIGNATURE", caught.exception.code)

    def test_a_legacy_row_does_not_block_a_new_identical_call(self):
        # CD-05: a row from before the column existed has route_shape '' and/or
        # origin 'legacy'; neither may be read as "this was already bought".
        self.ledger.reserve(self.request(run_id="run-01", operation_id="op-01",
                                         transport="rest_https"))
        with self.ledger._connect(write=True) as db:  # noqa: SLF001 - the migration state under test
            # ``_connect`` commits on exit; committing here would end the
            # transaction it is about to close.
            db.execute("UPDATE operations SET route_shape='', origin='legacy', "
                       "legacy_ref='legacy-1' WHERE operation_id='op-01'")
        reserved = self.ledger.reserve(
            self.request(run_id="run-02", operation_id="op-02",
                         transport="official_mcp"))
        self.assertEqual(self.usage.ChargeState.RESERVED, reserved.charge_state)


if __name__ == "__main__":
    unittest.main()
