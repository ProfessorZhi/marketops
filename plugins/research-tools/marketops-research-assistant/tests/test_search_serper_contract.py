"""Serper dispatch is governed by the declared registry, and one query is one key.

Three properties, none of which the legacy dual-search tests could see:

1. A page the registry cannot describe is refused *before* a reservation, so a
   paid attempt is never spent discovering that a category has no pages.
2. The Lite ledger keys on ``search_canonical``'s transport-independent
   signature, so the same query reached over REST and over an embedded MCP is
   one reservation rather than two charges.
3. ``marketops_search_capabilities`` reports declared coverage and its
   verification state instead of implying nine verified categories.
"""
from __future__ import annotations

import io
import json
import os
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import dual_search_mcp as dual
import search_canonical as canonical
import serper_modes


# The shared ledger refuses an unpriced capability rather than estimating one
# (research_usage_ledger: "A price is recorded, never assumed"). These tests are
# about other behaviour, so they install an offline catalog that says so.
import sys as _sys
import pathlib as _pathlib

_sys.path.insert(0, str(_pathlib.Path(__file__).resolve().parent))
import wave1_pricing_fixture

wave1_pricing_fixture.install()


class Response:
    status = 200

    def __init__(self, payload: dict):
        self.stream = io.BytesIO(json.dumps(payload).encode("utf-8"))

    def read(self, n: int) -> bytes:
        return self.stream.read(n)

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.stream.close()


class PaginationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "private"
        context = patch.dict(os.environ, {
            "MARKETOPS_RESEARCH_DATA_ROOT": str(self.root),
            "MARKETOPS_SEARCH_ALLOW_NETWORK": "1",
            "MARKETOPS_SEARCH_MAX_CALLS": "5",
            "SERPER_API_KEY": "synthetic-serper-secret",
            "SERPAPI_API_KEY": "synthetic-serpapi-secret",
        })
        context.start()
        self.addCleanup(context.stop)

    def call(self, **overrides):
        options = dict(provider="serper", query="public sports venues Devon",
                       country="GB", language="en", run_id="run-01",
                       operation_id="op-01", mode="web", limit=3)
        options.update(overrides)
        return dual.search(opener=self.fake, **options)

    def fake(self, req, timeout):
        self.seen.append(req)
        return Response({"organic": []})

    def setUpCapture(self):
        self.seen = []

    def test_first_page_sends_no_page_field_and_second_page_sends_one(self):
        self.setUpCapture()
        self.call(page=1)
        self.assertNotIn("page", json.loads(self.seen[0].data))
        self.call(operation_id="op-02", page=2)
        self.assertEqual(2, json.loads(self.seen[1].data)["page"])

    def test_a_category_without_pages_is_refused_before_any_reservation(self):
        for mode, reason in (("autocomplete", "SERPER_MODE_NOT_PAGINATED"),
                             ("maps", "SERPER_MODE_PAGINATION_UNVERIFIED")):
            with self.subTest(mode=mode):
                with self.assertRaisesRegex(ValueError, reason):
                    self.call(mode=mode, page=2)
        self.assertFalse((self.root / "state" / "dual-search-ledger.sqlite3").exists(),
                         "a refused page must not consume a paid attempt")

    def test_page_bounds_are_the_registrys_bounds(self):
        with self.assertRaisesRegex(ValueError, "SERPER_PAGE_INVALID"):
            self.call(page=0)
        with self.assertRaisesRegex(ValueError, "SERPER_PAGE_INVALID"):
            self.call(page=serper_modes.mode_spec("web").declared_max_page + 1)

    def test_the_mode_vocabulary_is_the_declared_registry(self):
        self.assertEqual(set(serper_modes.ALL_MODES), set(dual.SERPER_MODES))
        for mode in dual.SERPER_MODES:
            with self.subTest(mode=mode):
                self.assertEqual(
                    "https://google.serper.dev" + serper_modes.mode_spec(mode).endpoint_path,
                    dual._serper_endpoint(mode),
                )

    def test_default_requests_keep_their_pre_change_shape(self):
        # Back-compat is a claim worth a test: a first-page, no-account call
        # must serialise exactly what this adapter sent before the registry and
        # the canonical key existed, or every stored receipt is orphaned.
        self.setUpCapture()
        self.call(page=1)
        body = json.loads(self.seen[0].data)
        self.assertEqual({"q", "gl", "hl", "num"}, set(body))
        self.assertEqual({"q": "public sports venues Devon", "gl": "gb",
                          "hl": "en", "num": 3}, body)

    def test_serpapi_rest_paging_is_refused_rather_than_sent(self):
        with self.assertRaisesRegex(ValueError, "SERPAPI_PAGINATION_UNVERIFIED"):
            self.call(provider="serpapi", page=2)
        self.assertFalse((self.root / "state" / "dual-search-ledger.sqlite3").exists())


class CanonicalKeyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "private"
        context = patch.dict(os.environ, {
            "MARKETOPS_RESEARCH_DATA_ROOT": str(self.root),
            "MARKETOPS_SEARCH_ALLOW_NETWORK": "1",
            "MARKETOPS_SEARCH_MAX_CALLS": "5",
            "SERPER_API_KEY": "synthetic-serper-secret",
        })
        context.start()
        self.addCleanup(context.stop)

    def call(self, **overrides):
        options = dict(provider="serper", query="public sports venues Devon",
                       country="GB", language="en", run_id="run-01",
                       operation_id="op-01", mode="web", limit=3)
        options.update(overrides)
        return dual.search(opener=lambda req, timeout: Response({"organic": []}),
                           **options)

    def stored_fingerprints(self):
        path = self.root / "state" / "dual-search-ledger.sqlite3"
        with sqlite3.connect(path) as db:
            return [row[0] for row in db.execute(
                "SELECT fingerprint FROM search_attempts ORDER BY operation_id")]

    def test_the_ledger_stores_the_canonical_provider_operation_key(self):
        self.call()
        expected = canonical.operation_signature(canonical.canonical_arguments(
            provider="serper", mode="web", query="public sports venues Devon",
            country="GB", language="en", limit=3, page=1, account="",
            transport="REST_HTTPS",
        ))
        self.assertEqual([expected], self.stored_fingerprints())

    def test_the_same_query_over_the_mcp_transport_is_not_a_second_charge(self):
        self.call(operation_id="op-01", transport="REST_HTTPS")
        with self.assertRaisesRegex(ValueError, "DUPLICATE_SEARCH_SAME_RUN"):
            self.call(operation_id="op-02", transport="EMBEDDED_OFFICIAL_MCP_HTTP")
        self.assertEqual(1, len(self.stored_fingerprints()))

    def test_a_different_account_is_a_different_spend_opportunity(self):
        self.call(operation_id="op-01", account="acct-1")
        self.call(operation_id="op-02", account="acct-2")
        self.assertEqual(2, len(self.stored_fingerprints()))


class SerpApiRestAvailabilityTests(unittest.TestCase):
    """D-2: the SerpApi REST path is DISABLED, and enabling it takes a decision.

    It is not deleted — the product brief asks for it to be kept until the
    official MCP contract has been validated — but a default install must not
    have a second, unmanaged route to the same paid vendor.
    """

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "private"
        context = patch.dict(os.environ, {
            "MARKETOPS_RESEARCH_DATA_ROOT": str(self.root),
            "MARKETOPS_SEARCH_ALLOW_NETWORK": "1",
            "MARKETOPS_SEARCH_MAX_CALLS": "5",
            "SERPAPI_API_KEY": "synthetic-serpapi-secret",
            "SERPER_API_KEY": "synthetic-serper-secret",
        }, clear=False)
        context.start()
        self.addCleanup(context.stop)
        patch.dict(os.environ, {"MARKETOPS_SERPAPI_REST_ENABLE": "0"}).start()

    def call(self, **overrides):
        options = dict(provider="serpapi", query="public sports venues Devon",
                       country="GB", language="en", run_id="run-01",
                       operation_id="op-01", mode="maps", limit=3)
        options.update(overrides)
        return dual.search(opener=lambda req, timeout: self.fail("network touched"),
                           **options)

    def test_the_rest_path_is_disabled_by_default_and_costs_nothing_to_refuse(self):
        with self.assertRaisesRegex(ValueError, "SERPAPI_REST_DISABLED"):
            self.call()
        self.assertFalse((self.root / "state" / "dual-search-ledger.sqlite3").exists())

    def test_an_operator_can_enable_it_explicitly_and_then_it_works(self):
        with patch.dict(os.environ, {"MARKETOPS_SERPAPI_REST_ENABLE": "1"}):
            urls = []

            class Response:
                status = 200

                def __init__(self):
                    self.stream = io.BytesIO(b'{"organic_results": []}')

                def read(self, n):
                    return self.stream.read(n)

                def __enter__(self):
                    return self

                def __exit__(self, *a):
                    pass

            def fake(req, timeout):
                urls.append(req.full_url)
                return Response()

            result = dual.search(provider="serpapi", query="public sports venues Devon",
                                 country="GB", language="en", run_id="run-01",
                                 operation_id="op-01", mode="maps", limit=3, opener=fake)
        self.assertEqual("DISCOVERED_NOT_VERIFIED", result["status"])
        self.assertIn("engine=google_maps", urls[0])
        self.assertIn("synthetic-serpapi-secret", urls[0])

    def test_availability_is_reported_rather_than_implied(self):
        reported = dual._configuration()
        self.assertEqual("DISABLED", reported["serpapi"]["availability"])
        self.assertEqual("ENABLED", reported["serper"]["availability"])
        self.assertEqual("MARKETOPS_SERPAPI_REST_ENABLE",
                         reported["serpapi"]["enable_with"])
        self.assertEqual("https://google.serper.dev/search",
                         reported["serper"]["endpoint_sample"])


class BundledSearchMCPTests(unittest.TestCase):
    """A module that ships without its imports is a tool that does not start.

    ``build_client_bundles`` copies an explicit allowlist of scripts. Adding an
    import to ``dual_search_mcp`` without adding the target there produces a
    bundle that passes every source-tree test and fails on the customer's
    machine with ``ModuleNotFoundError`` — so this test walks the import graph
    of the bundled entrypoint instead of naming today's three files.
    """

    def test_every_local_import_of_the_bundled_search_mcp_is_copied(self):
        import ast
        import build_client_bundles as bundle

        source_scripts = {p.stem for p in (ROOT / "scripts").glob("*.py")}
        tree = ast.parse((ROOT / "scripts" / "dual_search_mcp.py").read_text(encoding="utf-8"))
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                imported.add(node.module.split(".")[0])
        local_imports = imported & source_scripts
        self.assertIn("search_canonical", local_imports)

        with tempfile.TemporaryDirectory() as tmp:
            info = bundle.build(Path(tmp) / "client-package", with_search_mcp=True)
            bundled = {p.name for p in (Path(info["directory"]) / "scripts").glob("*.py")}
        missing = sorted(name + ".py" for name in local_imports if name + ".py" not in bundled)
        self.assertEqual([], missing, "bundle would fail at import time on the customer machine")


class CapabilityCoverageTests(unittest.TestCase):
    def test_capabilities_report_declared_coverage_not_verified_coverage(self):
        with patch.dict(os.environ, {"SERPER_API_KEY": "fake",
                                     "SERPAPI_API_KEY": "fake-key"}):
            reported = dual._configuration()
        coverage = reported["serper_coverage"]
        self.assertEqual("marketops.serper.coverage.v1", coverage["schema"])
        self.assertFalse(coverage["officially_verified_live"])
        self.assertEqual(0, coverage["live_verified_modes"])
        self.assertEqual(len(serper_modes.ALL_MODES), coverage["declared_modes"])
        self.assertIn("provider_calls_made", reported)
        self.assertEqual(0, reported["provider_calls_made"])
        self.assertFalse(reported["serpapi_mcp"]["engine_catalog_loaded"])
        self.assertIn("serpapi://engines", reported["serpapi_mcp"]["engine_index_uri"])


if __name__ == "__main__":
    unittest.main()
