"""Source-bytes -> Lite SQLite candidates -> real XLSX/CSV -> independent readback.

All example.org names/emails are synthetic; this test makes no network calls.
"""
from __future__ import annotations

import csv
import json
import os
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import research_mcp as mcp  # noqa: E402
import research_delivery as delivery  # noqa: E402
import research_lite_store as store  # noqa: E402


class CandidateDeliveryE2E(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / "data"
        env = patch.dict(os.environ, {"MARKETOPS_RESEARCH_DATA_ROOT": str(self.root),
                      "MARKETOPS_RESEARCH_ALLOW_NETWORK": "0", "MARKETOPS_SEARCH_ALLOW_NETWORK": "0"})
        env.start()
        self.addCleanup(env.stop)
        mcp.start("run-test", "GB-sports-grounds", "Find sports grounds and contacts", "SOP-PENDING")
        body = (b"<html><h1>Example Borough sports ground</h1>"
                b"<p>Example Borough Council, Grounds Management</p>"
                b"<p>Contact grounds@example.org</p></html>")
        store.save_artifact(self.root, "run-test", "art-public",
                            "fixture-public-html", "https://example.org/grounds",
                            body, "raw_response")
        mcp.propose("run-test", "src-1", "discovery", "source", "source-council",
                    {"title": "Sports grounds", "url": "https://example.org/grounds",
                     "provider": "fixture"})
        mcp.propose("run-test", "org-1", "entities", "organization", "example-borough",
                    {"name": "Example Borough Council", "official_domain": "example.org"},
                    ["art-public"])
        mcp.propose("run-test", "venue-1", "entities", "venue", "example-sports-ground",
                    {"name": "Example Sports Ground", "organization_id": "example-borough"},
                    ["art-public"])
        mcp.propose("run-test", "person-dept", "people", "person", "grounds-dept",
                    {"department": "Grounds Management", "role_title": "Grounds management team",
                     "organization_id": "example-borough"}, ["art-public"])
        mcp.propose("run-test", "contact-1", "routes", "contact_point", "grounds-contact",
                    {"organization_id": "example-borough", "type": "email",
                     "value": "grounds@example.org", "role_function": "Grounds Management",
                     "department": "Grounds Management"}, ["art-public"])

    def test_delivers_real_xlsx_with_9_sheets_and_evidence(self):
        result = delivery.write_delivery(self.root, "run-test", "delivery-001")
        self.assertEqual("PARTIAL_CANDIDATE_ONLY_NOT_FORMAL_CONTACTS", result["status"])
        self.assertEqual("ALL_OK", result["evidence_hash_readback"])
        self.assertFalse(result["send_authorized"])
        self.assertEqual(1, result["counts"]["客户总表"])
        self.assertEqual(1, result["counts"]["联系人路线"])
        workbook = Path(result["xlsx_path"])
        self.assertTrue(workbook.exists())
        with zipfile.ZipFile(workbook) as z:
            self.assertIsNone(z.testzip())
            self.assertEqual(9, len([n for n in z.namelist()
                                     if n.startswith("xl/worksheets/sheet") and n.endswith(".xml")]))
        with (workbook.parent / "联系人路线.csv").open(encoding="utf-8-sig", newline="") as f:
            row = next(csv.DictReader(f))
        self.assertEqual("grounds@example.org", row["公开字面值"])
        self.assertEqual("LITERAL_IN_SAVED_MATERIAL", row["来源字面校验"])
        self.assertEqual("RULES_UNKNOWN", row["研究评级"])
        self.assertEqual("CANDIDATE_NOT_APPROVED", row["业务状态"])
        receipt = json.loads((workbook.parent / "MANIFEST.json").read_text(encoding="utf-8"))
        self.assertIn("Research_Result.xlsx", receipt["files"])
        self.assertEqual(1, receipt["counts"]["客户总表"])
        with self.assertRaises(FileExistsError):
            delivery.write_delivery(self.root, "run-test", "delivery-001")

    def test_modified_source_bytes_block_delivery(self):
        p = next((self.root / "artifacts" / "run-test").glob("*.bin"))
        p.write_bytes(b"<html>tampered</html>")
        with self.assertRaisesRegex(ValueError, "ARTIFACT_READBACK_HASH_MISMATCH"):
            delivery.write_delivery(self.root, "run-test", "delivery-bad")
        self.assertFalse((self.root / "exports" / "run-test" / "delivery-bad").exists())

    def test_missing_public_email_is_hold_not_formal_contact(self):
        mcp.propose("run-test", "contact-2", "routes", "contact_point", "nonliteral",
                    {"organization_id": "example-borough", "type": "email",
                     "value": "guessed@example.org"}, ["art-public"])
        result = delivery.write_delivery(self.root, "run-test", "delivery-hold")
        self.assertEqual(2, result["counts"]["联系人路线"])
        self.assertGreaterEqual(result["counts"]["排除与待确认"], 1)
        with (Path(result["directory"]) / "联系人路线.csv").open(encoding="utf-8-sig", newline="") as f:
            rows = list(csv.DictReader(f))
        second = next(x for x in rows if x["渠道ID"] == "contact-2")
        self.assertEqual("HOLD_NOT_LITERAL", second["来源字面校验"])
        self.assertEqual("RULES_UNKNOWN", second["研究评级"])


if __name__ == "__main__":
    unittest.main()
