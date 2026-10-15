"""Synthetic dual-vendor search tests. Never make a paid API request or use real keys."""
from __future__ import annotations

import hashlib
import io
import json
import os
import sys
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import dual_search_mcp as dual


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


class TestDualSearch(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "private"
        context = patch.dict(
            os.environ, {
                "MARKETOPS_RESEARCH_DATA_ROOT": str(self.root),
                "MARKETOPS_SEARCH_ALLOW_NETWORK": "1",
                "MARKETOPS_SEARCH_MAX_CALLS": "2",
                "SERPAPI_API_KEY": "synthetic-serpapi-secret",
                "SERPER_API_KEY": "synthetic-serper-secret",
            },
        )
        context.start()
        self.addCleanup(context.stop)

    def call(self, provider="serper", operation="op-01", opener=None, mode="web"):
        return dual.search(
            provider=provider, query="public sports venues Devon", country="GB",
            language="en", run_id="run-01", operation_id=operation,
            mode=mode, limit=3, opener=opener,
        )

    def test_serper_official_api_and_normalized_receipt(self):
        requested = []
        def fake(req, timeout):
            requested.append((req.full_url, req.get_method(), req.get_header("X-api-key")))
            return Response({"organic": [
                {"title": "Example Club", "link": "https://example.org", "snippet": "football pitch"},
                {"title": "Example Council", "link": "https://example.net", "snippet": "sports"}
            ]})
        result = self.call(opener=fake)
        self.assertEqual("DISCOVERED_NOT_VERIFIED", result["status"])
        self.assertEqual(2, result["result_count"])
        self.assertEqual(("https://google.serper.dev/search", "POST", "synthetic-serper-secret"), requested[0])
        self.assertFalse(result["results"][0]["status"] == "verified")
        stored = "\n".join(p.read_text() for p in self.root.rglob("*.json"))
        self.assertNotIn("synthetic-serper-secret", stored)
        self.assertNotIn("synthetic-serpapi-secret", stored)
        from research_event_journal import read_events
        self.assertEqual(["started", "succeeded"], [e["status"] for e in read_events(self.root, "run-01")])

    def test_serpapi_api_uses_different_key_and_preserves_place_id(self):
        seen = []
        def fake(req, timeout):
            seen.append((req.get_method(), req.full_url))
            return Response({"search_metadata": {"status": "Success"},
                "local_results": [{
                    "title": "Devon Public Pitch", "place_id": "ChIJ-synthetic",
                    "data_id": "0x001:0x002", "address": "Devon",
                    "gps_coordinates": {"latitude": 50.7, "longitude": -3.5},
                }]})
        result = self.call("serpapi", "op-02", opener=fake, mode="maps")
        self.assertEqual("GET", seen[0][0])
        self.assertTrue(seen[0][1].startswith("https://serpapi.com/search.json?"))
        self.assertIn("engine=google_maps", seen[0][1])
        self.assertIn("synthetic-serpapi-secret", seen[0][1])
        self.assertEqual("ChIJ-synthetic", result["results"][0]["place_id"])
        self.assertEqual(-3.5, result["results"][0]["longitude"])
        self.assertEqual("", result["results"][0]["url"])
        self.assertNotIn("synthetic-serpapi-secret",
                         "\n".join(p.read_text() for p in self.root.rglob("*.json")))

    def test_persistent_budget_cap_across_replay(self):
        fake = lambda req, timeout: Response({"organic": []})
        self.call(operation="op-01", opener=fake)
        self.call(operation="op-02", opener=fake)
        with self.assertRaisesRegex(ValueError, "SEARCH_BUDGET_EXHAUSTED"):
            self.call(operation="op-03", opener=fake)
        with self.assertRaisesRegex(ValueError, "ALREADY_RESERVED"):
            self.call(operation="op-01", opener=fake)

    def test_no_auto_provider_fallback(self):
        with self.assertRaisesRegex(ValueError, "provider must be"):
            self.call(provider="auto", opener=lambda req, timeout: Response({}))

    def test_network_is_off_by_default(self):
        with patch.dict(os.environ, {"MARKETOPS_SEARCH_ALLOW_NETWORK": "0"}):
            with self.assertRaisesRegex(ValueError, "SEARCH_DISABLED"):
                self.call(opener=lambda req, timeout: self.fail("network touched"))
        self.assertFalse((self.root / "state" / "dual-search-ledger.sqlite3").exists())

    def test_missing_vendor_key_blocks_before_reservation(self):
        with patch.dict(os.environ, {"SERPAPI_API_KEY": ""}):
            with self.assertRaisesRegex(ValueError, "PROVIDER_KEY_MISSING"):
                self.call(provider="serpapi", opener=lambda req, timeout: self.fail("network touched"))
        self.assertFalse((self.root / "state" / "dual-search-ledger.sqlite3").exists())

    def test_unexpanded_workbuddy_key_placeholder_rejected(self):
        with patch.dict(os.environ, {"SERPAPI_API_KEY": "${SERPAPI_API_KEY}"}):
            self.assertFalse(dual._configuration()["serpapi"]["configured"])
            with self.assertRaisesRegex(ValueError, "PROVIDER_KEY_MISSING"):
                self.call(provider="serpapi", opener=lambda req, timeout: self.fail("unexpected request"))
        self.assertFalse((self.root / "state" / "dual-search-ledger.sqlite3").exists())
    def test_vendor_http_error_redacts_url_and_key(self):
        def fail(req, timeout):
            raise urllib.error.HTTPError(
                req.full_url, 401, "Unauthorized", hdrs=None, fp=None
            )
        with self.assertRaises(ValueError) as check:
            self.call(provider="serpapi", opener=fail)
        self.assertIn("PROVIDER_HTTP_ERROR_401", str(check.exception))
        self.assertNotIn("synthetic-serpapi-secret", str(check.exception))
        from research_event_journal import read_events
        statuses = [e["status"] for e in read_events(self.root, "run-01")]
        self.assertIn("unknown", statuses)

    def test_only_discovery_leads_not_contact_facts(self):
        def fake(req, timeout):
            return Response({"organic": [{
                "title": "Staff", "link": "https://example.org/staff",
                "snippet": "john@example.org"}
            ]})
        result = self.call(opener=fake)
        self.assertFalse("contact_route" in result)
        self.assertEqual(False, json.loads(next((self.root / "search-receipts").rglob("*.json")).read_text())
                         ["business_evidence"])

    def test_no_cross_provider_implicit_second_call(self):
        called = []
        def fake(req, timeout):
            called.append(req.full_url)
            return Response({"organic": []})
        result = self.call(opener=fake)
        self.assertEqual(1, len(called))
        self.assertEqual(1, result["calls_reserved"])

    def test_query_args_validated_before_call(self):
        with self.assertRaisesRegex(ValueError, "country"):
            dual.search("serper", "grounds", "UK-DEVON", "en", "run-01", "op-01",
                        opener=lambda req, timeout: self.fail("called"))

    def test_redirect_is_explicitly_refused(self):
        with self.assertRaisesRegex(ValueError, "PROVIDER_REDIRECT_BLOCKED"):
            dual.NoRedirect().redirect_request(
                None, None, 302, "Moved", {}, "https://untrusted.example/"
            )

    def test_workbuddy_opt_in_one_mcp_and_no_embedded_keys(self):
        import build_client_bundles as bundle
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "client-package"
            info = bundle.build(output, with_search_mcp=True)
            self.assertEqual(1, info["mcp_servers_bundled"])
            self.assertEqual(16, info["skills"])
            target = Path(info["directory"])
            mcp = json.loads((target / "mcp.json").read_text(encoding="utf-8"))
            schema = json.loads((target / "token-schema.json").read_text(encoding="utf-8"))
            self.assertEqual(["marketops-dual-search"], list(mcp["mcpServers"]))
            self.assertEqual("token", json.loads(
                (target / "connector-meta.json").read_text(encoding="utf-8")
            )["auth_mode"])
            keys = {x["key"] for x in schema["fields"]}
            self.assertIn("SERPAPI_API_KEY", keys)
            self.assertIn("SERPER_API_KEY", keys)
            self.assertNotIn("synthetic-serpapi-secret", (target / "mcp.json").read_text())
            self.assertIn("$" + "{SERPAPI_API_KEY}", json.dumps(mcp))

if __name__ == "__main__":
    unittest.main()
