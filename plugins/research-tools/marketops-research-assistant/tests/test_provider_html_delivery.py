"""Official-fetch transport fixture -> real gateway/store -> claims -> XLSX.

No external network or credentials. Vendor-cleaned HTML is never labelled an
original response, browser DOM, business approval or independently verified fact.
"""
import csv
import json
import os
from pathlib import Path
import sys
import tempfile
import sqlite3
import unittest
from unittest.mock import patch
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import _runtime_mcp_fixture as wire_fixture
import research_mcp as mcp
import research_lite_store as store
import research_delivery as delivery


URL = "https://example.org/sports"
HTML = ("<main><h1>Example School</h1><p>Example School operates the Sports Ground.</p>"
        "<p>Grounds manager: Alex Sample. Public contact grounds@example.org</p></main>")


class ProviderHtmlTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / "data"
        env = patch.dict(os.environ, {"MARKETOPS_RESEARCH_DATA_ROOT": str(self.root),
            "MARKETOPS_RESEARCH_ALLOW_NETWORK": "1", "TINYFISH_API_KEY": "fixture-key",
            "MARKETOPS_FETCH_ALLOWED_DOMAINS": "example.org"})
        env.start()
        self.addCleanup(env.stop)
        wire_fixture.install(self, self.root, providers=("tinyfish",))
        brief = {"record_type": "research_brief", "target_kind": "organization", "goal": "contacts",
                 "minimum_targets": 1, "minimum_targets_unit": "organization", "require_named_person": False}
        mcp.start("run", "school-test", "Find sourced contact route", "v1", brief)
        self.calls = []
        self.payload = {"results": [{"url": URL, "html": HTML}]}
        wire_fixture.bind(self, self.root, calls=self.calls, result=lambda *args: self.payload)

    def fetch(self, operation="fetch-1", fmt="html", urls=None):
        return mcp.managed_call("tinyfish.fetch_content", {"urls": urls or [URL], "format": fmt,
            "links": False, "image_links": False, "iframe_links": False, "page_metadata": False}, "run", operation)

    def test_fetch_html_to_literal_claim_and_workbook_preserves_vendor_boundary(self):
        result = self.fetch()
        self.assertEqual("OK", result["status"], result)
        self.assertEqual(1, len(self.calls))
        self.assertEqual(1, len(result["provider_html_artifact_ids"]))
        self.assertEqual([], result["primary_artifact_ids"])
        aid = result["provider_html_artifact_ids"][0]
        material = store.read_artifact(self.root, "run", aid)
        self.assertEqual("provider_html", material["material_kind"])
        self.assertEqual(URL, material["origin_url"])
        self.assertEqual(HTML.encode(), material["content"])
        self.assertTrue(result["source_materials"][0]["vendor_mediated"])
        self.assertFalse(result["source_materials"][0]["raw_http_verified"])
        self.assertEqual("provider_extracted", store.read_artifact(self.root, "run", result["artifact"]["artifact_id"])["material_kind"])
        mcp.propose("run", "org", "entities", "organization", "school",
            {"name": "Example School", "claims": [{"field": "identity", "artifact_id": aid,
              "quote": "Example School operates the Sports Ground."}]}, [aid])
        mcp.propose("run", "contact", "routes", "contact_point", "grounds",
            {"organization_id": "school", "type": "email", "value": "grounds@example.org", "claims": [
                {"field": "email", "artifact_id": aid, "quote": "Public contact grounds@example.org"}]}, [aid])
        result = delivery.write_delivery(self.root, "run", "delivery")
        self.assertEqual(2, result["evidence_link_count"])
        self.assertEqual(0, result["evidence_spanless_record_count"])
        self.assertEqual("OPEN", result["evidence_envelope_export_gate"])
        self.assertEqual([], result["evidence_envelope_missing_artifacts"])
        self.assertTrue(result["evidence_envelopes"])
        import research_usage_ledger as usage
        with sqlite3.connect(usage.default_path(self.root)) as db:
            real_operations = {row[0] for row in db.execute("SELECT operation_id FROM operations WHERE run_id='run'")}
        self.assertTrue(all(e["ledger_id"] in real_operations for e in result["evidence_envelopes"]))
        self.assertNotIn("EMAIL_NOT_IN_PRIMARY_MATERIAL", result["evidence_assessment"]["gap_counts"])
        self.assertNotIn("DERIVED_EXCERPT_ONLY", result["evidence_assessment"]["gap_counts"])
        self.assertGreater(result["evidence_assessment"]["vendor_html_quote_matches"], 0)
        with (Path(result["directory"]) / "联系人路线.csv").open(encoding="utf-8-sig", newline="") as handle:
            row = next(csv.DictReader(handle))
        self.assertEqual("VENDOR_HTML_EMAIL_LITERAL_NOT_BUSINESS_VERIFIED", row["来源字面校验"])
        self.assertEqual("CANDIDATE_NOT_APPROVED", row["业务状态"])
        with zipfile.ZipFile(result["xlsx_path"]) as workbook:
            self.assertIsNone(workbook.testzip())

    def test_markdown_stays_derived_and_cannot_claim_primary_email(self):
        self.payload = {"results": [{"url": URL, "markdown": "Public contact grounds@example.org"}]}
        result = self.fetch(fmt="markdown")
        self.assertEqual("OK", result["status"])
        self.assertEqual([], result["provider_html_artifact_ids"])
        aid = result["artifact_ids"][0]
        mcp.propose("run", "contact", "routes", "contact_point", "grounds",
            {"type": "email", "value": "grounds@example.org", "claims": [
                {"field": "email", "artifact_id": aid, "quote": "Public contact grounds@example.org"}]}, [aid])
        _, report = delivery.build_delivery(self.root, "run")
        self.assertIn("EMAIL_NOT_IN_PRIMARY_MATERIAL", report["evidence_assessment"]["gap_counts"])

    def test_challenge_empty_wrong_url_or_partial_batch_never_becomes_source_html(self):
        cases = [
            ({"results": [{"url": URL, "html": "<p>Verify you are human</p>"}]}, [URL]),
            ({"results": [{"url": URL, "html": "<main> </main>"}]}, [URL]),
            ({"results": [{"url": "https://other.example.org/", "html": HTML}]}, [URL]),
            ({"results": [{"url": URL, "html": HTML, "status_code": 403}]}, [URL]),
            ({"results": [{"url": URL, "html": HTML}]}, [URL, "https://example.org/other"]),
            ({"results": [{"url": URL, "html": HTML}, {"url": URL, "html": HTML}]}, [URL]),
        ]
        for i, (payload, requested) in enumerate(cases):
            # A distinct current page for each case keeps the actual duplicate
            # guard active without making later assertions vacuous.
            substitutions = {url: url + "?case=" + str(i) for url in requested}
            self.payload = json.loads(json.dumps(payload))
            for row in self.payload.get("results", []):
                row["url"] = substitutions.get(row.get("url"), row.get("url"))
            result = self.fetch(operation="refuse-" + str(i), urls=[substitutions[x] for x in requested])
            self.assertEqual("FAILED", result["status"], result)
            self.assertEqual([], result["provider_html_artifact_ids"])
            self.assertIsNotNone(result["artifact"], "retain provider envelope for diagnosis")
            self.assertEqual([], result["stored_record_ids"])

    def test_html_request_cannot_relabel_markdown_or_json_wrapper_as_html(self):
        self.payload = {"results": [{"url": URL, "content": "# Public contact grounds@example.org"}]}
        result = self.fetch()
        self.assertEqual("FAILED", result["status"])
        self.assertEqual([], result["provider_html_artifact_ids"])

    def test_invalid_quote_stays_hold_and_batch_rollback_includes_links(self):
        import research_evidence_store as provenance
        result = self.fetch()
        aid = result["provider_html_artifact_ids"][0]
        good = {"record_id": "good", "stage": "entities", "kind": "organization", "entity_key": "school",
                "facts": {"name": "Example School", "claims": [{"field": "identity", "artifact_id": aid,
                    "quote": "Example School operates the Sports Ground."}]}, "artifact_ids": [aid]}
        bad = {**good, "record_id": "bad", "artifact_ids": ["missing-material"]}
        with self.assertRaises(ValueError):
            mcp.propose_batch("run", [good, bad])
        self.assertEqual([], provenance.evidence_links(self.root, "run", record_id="good"))
        self.assertNotIn("good", [r["record_id"] for r in store.list_run(self.root, "run")["records"]])
        mcp.propose_batch("run", [good])
        mcp.propose_batch("run", [good])
        self.assertEqual(1, len(provenance.evidence_links(self.root, "run", record_id="good")))
        mcp.propose("run", "untrue", "entities", "organization", "different-school",
            {"name": "Invented", "claims": [{"field": "identity", "artifact_id": aid,
                "quote": "An invented organization not appearing on the source."}]}, [aid])
        self.assertEqual([], provenance.evidence_links(self.root, "run", record_id="untrue"))
        _, report = delivery.build_delivery(self.root, "run")
        self.assertIn("EXCERPT_NOT_FOUND", report["evidence_assessment"]["gap_counts"])
        self.assertEqual(1, report["evidence_spanless_record_count"])


if __name__ == "__main__":
    unittest.main()
