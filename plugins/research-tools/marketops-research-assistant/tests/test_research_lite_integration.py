"""Synthetic offline integration tests for the 5-provider Lite Research candidate.

Tests create customer-like data under tempfile; do NOT use real API keys or network.
"""
from __future__ import annotations

import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

BASE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE / "scripts"))
import research_mcp as mcp
import research_lite_store as store
import research_provider_extensions as providers
from research_event_journal import read_events


class FakeResponse:
    status = 200
    def __init__(self, payload):
        self.stream = io.BytesIO(json.dumps(payload).encode("utf-8"))
    def read(self, length):
        return self.stream.read(length)
    def __enter__(self):
        return self
    def __exit__(self, *args):
        self.stream.close()


class LiteTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "data"
        environment = patch.dict(os.environ, {
            "MARKETOPS_RESEARCH_DATA_ROOT": str(self.root),
            "MARKETOPS_SEARCH_ALLOW_NETWORK": "0",
            "MARKETOPS_SEARCH_MAX_CALLS": "0",
            "MARKETOPS_RESEARCH_ALLOW_NETWORK": "0",
            "MARKETOPS_RESEARCH_MAX_EXTERNAL_CALLS": "0",
            "MARKETOPS_FETCH_ALLOWED_DOMAINS": "example.org",
            "SERPAPI_API_KEY": "",
            "SERPER_API_KEY": "",
            "CRAWL4AI_API_KEY": "",
            "TINYFISH_API_KEY": "",
            "TYPESAFE_API_KEY": "",
        })
        environment.start()
        self.addCleanup(environment.stop)
        mcp.start("run-001", "GB-natural-grass", "Synthetic grounds research", "uk-policy-1")

    def test_capabilities_are_offline_and_secret_free(self):
        value = mcp.capabilities()
        self.assertFalse(value["limits"]["search_on"])
        self.assertFalse(value["limits"]["other_network_on"])
        self.assertFalse(value["providers"]["jev"]["key"])
        self.assertNotIn("API_KEY", json.dumps(value))

    def test_candidate_progression_store_and_replay(self):
        entity = mcp.propose(
            "run-001", "venue-001", "entities", "venue", "asset-001",
            {"name": "Fictional Pitch", "surface": "unknown"}, [])
        self.assertEqual("candidate_unverified", entity["status"])
        reread = mcp.store.list_run(self.root, "run-001")
        self.assertEqual(1, reread["record_count"])
        self.assertEqual("venue", reread["records"][0]["kind"])
        events = read_events(self.root, "run-001")
        self.assertEqual(2, len(events))
        self.assertEqual("entities", events[1]["stage"])
        with self.assertRaisesRegex(ValueError, "cannot be written"):
            mcp.propose("run-001", "route-001", "routes", "contact_route", "org-1",
                        {"ready": True, "email": "invented@example.org"})

    def test_export_restricted_and_candidate_only(self):
        mcp.propose("run-001", "venue-001", "entities", "venue", "asset-001", {"name": "Synthetic"})
        with self.assertRaisesRegex(ValueError, "EXPORT_MUST_BE_WITHIN"):
            mcp.export("run-001", str(Path(self.temp.name) / "wrong.csv"))
        target = self.root / "exports" / "run-001.csv"
        exported = mcp.export("run-001", str(target))
        self.assertEqual(1, exported["rows"])
        self.assertEqual("CANDIDATE_CSV_EXPORTED_NOT_FORMALLY_VERIFIED", exported["status"])
        self.assertTrue(target.read_bytes().startswith(b"\xef\xbb\xbf"))

    def test_approved_fetch_domain_is_mandatory(self):
        with self.assertRaisesRegex(ValueError, "HOST_NOT_APPROVED"):
            providers._public_https_url("https://private-unapproved.example.net/")
        with self.assertRaisesRegex(ValueError, "PUBLIC_HOSTNAME_REQUIRED"):
            providers._public_https_url("https://127.0.0.1/secrets")
        self.assertEqual("https://example.org/grounds",
                         providers._public_https_url("https://example.org/grounds"))

    def test_external_calls_require_operator_network_and_ceiling(self):
        with self.assertRaisesRegex(ValueError, "RESEARCH_NETWORK_DISABLED"):
            providers.invoke("tinyfish_fetch", run_id="run-001", operation_id="op-1",
                             url="https://example.org/team")
        self.assertFalse((self.root / "state" / "provider-reservations.sqlite3").exists())

    def test_tinyfish_cloud_fetch_saves_artifact_and_receipt(self):
        with patch.dict(os.environ, {
            "MARKETOPS_RESEARCH_ALLOW_NETWORK": "1",
            "MARKETOPS_RESEARCH_MAX_EXTERNAL_CALLS": "2",
            "TINYFISH_API_KEY": "synthetic-key-not-real",
        }):
            observed = []
            def response(request, timeout):
                observed.append((request.full_url, request.get_method()))
                return FakeResponse({"results": [{"url": "https://example.org/team",
                    "content": "Synthetic staff page."}]})
            result = providers.invoke("tinyfish_fetch", run_id="run-001",
                                      operation_id="tinyfish-fetch-1",
                                      url="https://example.org/team", opener=response)
            self.assertEqual("SAVED_MATERIAL_NOT_VERIFIED", result["status"])
            self.assertEqual("candidate_with_material", result["record"]["status"])
            self.assertIn("Synthetic staff page", result["preview"])
            self.assertEqual(("https://api.fetch.tinyfish.ai", "POST"), observed[0])
            with self.assertRaisesRegex(ValueError, "OPERATION_ALREADY_RESERVED"):
                providers.invoke("tinyfish_fetch", run_id="run-001",
                                 operation_id="tinyfish-fetch-1",
                                 url="https://example.org/team", opener=response)
            self.assertEqual(1, len(observed))

    def test_crawl4ai_cloud_mock_and_secret_redaction(self):
        with patch.dict(os.environ, {
            "MARKETOPS_RESEARCH_ALLOW_NETWORK": "1",
            "MARKETOPS_RESEARCH_MAX_EXTERNAL_CALLS": "2",
            "CRAWL4AI_API_KEY": "synthetic-secret-crawl4ai",
        }):
            def response(request, timeout):
                self.assertEqual("https://api.crawl4ai.com/scrape", request.full_url)
                return FakeResponse({"markdown": "Synthetic copied page", "echo": "synthetic-secret-crawl4ai"})
            result = providers.invoke("crawl4ai_cloud", run_id="run-001",
                                      operation_id="crawl-1",
                                      url="https://example.org/pitch", opener=response)
            self.assertEqual("SAVED_MATERIAL_NOT_VERIFIED", result["status"])
            self.assertNotIn("synthetic-secret-crawl4ai", result["preview"])
            materials = list((self.root / "artifacts").rglob("*.bin"))
            self.assertEqual(1, len(materials))
            self.assertNotIn("synthetic-secret-crawl4ai", materials[0].read_text())

    def test_jev_choice_with_mocked_sdk_preserves_judgment_boundary(self):
        with patch.dict(os.environ, {
            "MARKETOPS_RESEARCH_ALLOW_NETWORK": "1",
            "MARKETOPS_RESEARCH_MAX_EXTERNAL_CALLS": "2",
            "TYPESAFE_API_KEY": "synthetic-typesafe-key",
        }):
            with patch.object(providers, "_jev_choice", return_value={
                "chosen_option": "hold", "judgment_only": True, "model": "fake"
            }):
                answer = providers.invoke(
                    "jev_choice", run_id="run-001", operation_id="jev-1",
                    document="A synthetic document about sports-grounds operations.",
                    question="Does the document support this venue?",
                    options=["eligible", "hold"])
                self.assertEqual("SAVED_MATERIAL_NOT_VERIFIED", answer["status"])
                self.assertIn("hold", answer["preview"])
                self.assertTrue(answer["record"]["artifact_ids"])

    def test_experience_draft_is_not_approved(self):
        root = self.root / "experience" / "run-001"
        root.mkdir(parents=True)
        generated = mcp.draft_experience(self.root, run_id="run-001",
            scope="GB-natural-grass", rule_version="uk-policy-1",
            skill_version="0.4.0", output=root / "experience.json")
        self.assertEqual("CANDIDATE_DRAFT_NOT_VERIFIED", generated["result"])
        document = json.loads((root / "experience.json").read_text())
        self.assertEqual("candidate_only", document["promotion"])
        self.assertEqual([], document["lessons"])


if __name__ == "__main__":
    unittest.main()
