"""Source-bytes -> Lite SQLite candidates -> real XLSX/CSV -> independent readback.

All example.org names/emails are synthetic; this test makes no network calls.

The positive fixture is deliberately *complete*: every business record carries a
quote that exists in saved material and a recorded span that binds the quote. A
candidate without that chain is not a "minor gap" the workbook may print in a
client table — it is excluded from the client sheets and kept in 排除与待确认 and
候选数据审计. The negative-path tests below pin that boundary.
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


PAGE = (b"<html><body><h1>Example Borough Council</h1>"
        b"<h2>Example Sports Ground</h2>"
        b"<p>Grounds Management is the responsible department.</p>"
        b"<p>Contact grounds@example.org for grounds enquiries.</p></body></html>")

ORG_QUOTE = "Example Borough Council"
VENUE_QUOTE = "Example Sports Ground"
PERSON_QUOTE = "Grounds Management is the responsible department."
CONTACT_QUOTE = "Contact grounds@example.org for grounds enquiries."


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
        store.save_artifact(self.root, "run-test", "art-public",
                            "fixture-public-html", "https://example.org/grounds",
                            PAGE, "rendered_html")
        mcp.propose("run-test", "src-1", "discovery", "source", "source-council",
                    {"title": "Sports grounds", "url": "https://example.org/grounds",
                     "provider": "fixture"})
        mcp.propose("run-test", "org-1", "entities", "organization", "example-borough",
                    {"name": "Example Borough Council",
                     "claims": [{"field": "identity", "artifact_id": "art-public",
                                 "quote": ORG_QUOTE}]},
                    ["art-public"])
        mcp.propose("run-test", "venue-1", "entities", "venue", "example-sports-ground",
                    {"name": "Example Sports Ground", "organization_id": "example-borough",
                     "claims": [{"field": "identity", "artifact_id": "art-public",
                                 "quote": VENUE_QUOTE}]},
                    ["art-public"])
        mcp.propose("run-test", "person-dept", "people", "person", "grounds-dept",
                    {"department": "Grounds Management", "role_title": "Grounds management team",
                     "organization_id": "example-borough",
                     "claims": [{"field": "role_title", "artifact_id": "art-public",
                                 "quote": PERSON_QUOTE}]}, ["art-public"])
        mcp.propose("run-test", "contact-1", "routes", "contact_point", "grounds-contact",
                    {"organization_id": "example-borough", "type": "email",
                     "value": "grounds@example.org", "role_function": "Grounds Management",
                     "department": "Grounds Management",
                     "claims": [{"field": "email", "artifact_id": "art-public",
                                 "quote": CONTACT_QUOTE}]}, ["art-public"])

    def sheet(self, directory, name):
        with (Path(directory) / f"{name}.csv").open(encoding="utf-8-sig", newline="") as handle:
            return list(csv.DictReader(handle))

    def test_delivers_real_xlsx_with_9_sheets_and_evidence(self):
        result = delivery.write_delivery(self.root, "run-test", "delivery-001")
        self.assertEqual("PARTIAL_CANDIDATE_ONLY_NOT_FORMAL_CONTACTS", result["status"])
        self.assertEqual("ALL_OK", result["evidence_hash_readback"])
        self.assertFalse(result["send_authorized"])
        self.assertEqual(1, result["counts"]["客户总表"])
        self.assertEqual(1, result["counts"]["联系人路线"])
        self.assertEqual(1, result["counts"]["场地与资产"])
        self.assertEqual(1, result["counts"]["人员岗位"])
        # A quality sample is admitted, not emptied by the gate.
        self.assertEqual(4, result["admission"]["evaluated_record_count"])
        self.assertEqual(4, result["admission"]["admitted_record_count"])
        self.assertEqual(0, result["admission"]["rejected_record_count"])
        self.assertEqual("ALL_CLIENT_ROWS_ADMITTED", result["admission_status"])
        workbook = Path(result["xlsx_path"])
        self.assertTrue(workbook.exists())
        with zipfile.ZipFile(workbook) as z:
            self.assertIsNone(z.testzip())
            self.assertEqual(9, len([n for n in z.namelist()
                                     if n.startswith("xl/worksheets/sheet") and n.endswith(".xml")]))
        row = next(iter(self.sheet(workbook.parent, "联系人路线")))
        self.assertEqual("grounds@example.org", row["公开字面值"])
        self.assertEqual("RENDERED_EMAIL_LITERAL_NOT_BUSINESS_VERIFIED", row["来源字面校验"])
        self.assertEqual("RULES_UNKNOWN", row["研究评级"])
        self.assertEqual("CANDIDATE_NOT_APPROVED", row["业务状态"])
        receipt = json.loads((workbook.parent / "MANIFEST.json").read_text(encoding="utf-8"))
        self.assertIn("Research_Result.xlsx", receipt["files"])
        self.assertEqual(1, receipt["counts"]["客户总表"])
        self.assertTrue(receipt["client_business_tables_admitted_only"])
        self.assertEqual(receipt["admission"], result["admission"])
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
        # The held contact is not a second client row: it is excluded, visible to
        # review, and still preserved in the internal audit sheet.
        self.assertEqual(1, result["counts"]["联系人路线"])
        self.assertEqual(1, result["admission"]["rejected_record_count"])
        self.assertEqual(["contact-2"], result["admission"]["rejected_record_ids"])
        self.assertEqual("HELD_ROWS_EXCLUDED_FROM_CLIENT_TABLES", result["admission_status"])
        contact_rows = self.sheet(result["directory"], "联系人路线")
        self.assertNotIn("contact-2", [row["渠道ID"] for row in contact_rows])
        self.assertNotIn("guessed@example.org", repr(contact_rows))
        issues = self.sheet(result["directory"], "排除与待确认")
        held = next(row for row in issues if row["对象ID"] == "contact-2")
        self.assertIn("NO_EXCERPT", held["问题或原因"])
        audit = self.sheet(result["directory"], "候选数据审计")
        self.assertIn("contact-2", [row["记录ID"] for row in audit])


if __name__ == "__main__":
    unittest.main()
