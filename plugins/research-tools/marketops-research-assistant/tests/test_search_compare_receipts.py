"""Offline, synthetic comparison tests; no external provider calls."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import compare_search_receipts as cmp


def fixture(provider: str, rows: list[dict], *, query: str = "Devon sports grounds") -> dict:
    return {
        "schema": "marketops.search.discovery.v1",
        "provider": provider,
        "query": query,
        "country": "GB", "language": "en",
        "mode": "maps", "limit": 10,
        "result_count": len(rows), "results": rows,
        "source_status": "discovered_not_verified",
        "business_evidence": False,
    }


class ComparisonTests(unittest.TestCase):
    def test_place_id_overlap_not_counted_as_verified_entity(self):
        a = fixture("serper", [{"place_id": "google-A", "url": ""},
                               {"url": "https://example.org/grounds/"}])
        b = fixture("serpapi", [{"place_id": "google-A"},
                                {"url": "https://example.org/grounds?from=maps"},
                                {"url": "", "place_id": "", "data_id": ""}])
        report = cmp.compare(a, b)
        self.assertEqual("DISCOVERY_COMPARISON_ONLY_NOT_QUALITY", report["status"])
        self.assertEqual(2, report["items_with_shared_url_or_place_id"]["serper"])
        self.assertEqual(2, report["items_with_shared_url_or_place_id"]["serpapi"])
        self.assertEqual(1, report["unmatchable_without_manual_review"]["serpapi"])

    def test_different_queries_are_not_comparable(self):
        with self.assertRaisesRegex(ValueError, "query differs"):
            cmp.compare(fixture("serper", [], query="A"),
                        fixture("serpapi", [], query="B"))

    def test_same_vendor_not_an_ab(self):
        with self.assertRaisesRegex(ValueError, "same provider"):
            cmp.compare(fixture("serpapi", []), fixture("serpapi", []))

    def test_empty_samples_are_not_success_claim(self):
        r = cmp.compare(fixture("serper", []), fixture("serpapi", []))
        self.assertEqual("DISCOVERY_COMPARISON_ONLY_NOT_QUALITY", r["status"])
        self.assertEqual(0, r["results"]["serper"])
        self.assertEqual(0, r["results"]["serpapi"])

if __name__ == "__main__":
    unittest.main()
