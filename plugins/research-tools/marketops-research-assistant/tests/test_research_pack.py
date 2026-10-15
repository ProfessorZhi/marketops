"""Offline contract tests. No network, CC, provider key, or real customer data."""
from __future__ import annotations

import csv
import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts import research_pack as rp  # noqa: E402
from scripts import build_client_bundles as cb  # noqa: E402

def put(folder: Path, table: str, *records: dict[str, str]) -> None:
    with (folder / "stages" / f"{table}.csv").open("a", newline="", encoding="utf-8") as f:
        wr = csv.DictWriter(f, fieldnames=rp.TABLES[table], lineterminator="\n")
        for rec in records:
            wr.writerow(rec)

class ResearchPackTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name) / "pack"
        rp.init_pack(self.folder, "test-001", "GB", "Find public grounds", "gb-grounds")

    def test_init_is_structurally_valid_not_business_pass(self):
        report = rp.verify(self.folder)
        self.assertEqual("STRUCTURALLY_VALID_EMPTY", report["result"])
        self.assertEqual("NOT_TESTED", report["business_approval"])
        self.assertEqual(len(rp.TABLES), len(list((self.folder / "stages").glob("*.csv"))))

    def test_never_overwrite_existing_pack(self):
        with self.assertRaises(FileExistsError):
            rp.init_pack(self.folder, "test-001", "GB", "retry")

    def test_private_local_source_url_rejected(self):
        put(self.folder, "sources", dict(source_id="s1", query="x",
            url="http://127.0.0.1/private", kind="web", discovered_at="2026-10-15T00:00:00+00:00", status="discovered"))
        self.assertEqual("INVALID", rp.verify(self.folder)["result"])

    def test_duplicate_key_rejected(self):
        row = dict(source_id="s1", query="x", url="https://example.org",
                   kind="web", discovered_at="2026-10-15T00:00:00+00:00", status="discovered")
        put(self.folder, "sources", row, row)
        self.assertIn("duplicate ID", " ".join(rp.verify(self.folder)["errors"]))

    def test_ready_route_with_literal_source_and_scene(self):
        # Synthetic structural fixture; this is not UK SOP grade adjudication.
        source_url = "https://example.org/team"
        excerpt = "Jane Doe, Grounds Manager; jane@example.org"
        put(self.folder, "sources", dict(source_id="s1", query="staff", url=source_url, kind="web",
            discovered_at="2026-10-15T00:00:00+00:00", status="reviewed"))
        put(self.folder, "evidence", dict(evidence_id="e1", source_id="s1", url=source_url,
            observed_at="2026-10-15T00:00:00+00:00", content_sha256=hashlib.sha256(excerpt.encode()).hexdigest(),
            excerpt=excerpt, status="usable"))
        put(self.folder, "organizations", dict(organization_id="o1", name="Example Club",
            official_domain="example.org", evidence_id="e1", status="verified"))
        put(self.folder, "account_assessments", dict(assessment_id="a1", organization_id="o1",
            scene="gb-grounds", decision="eligible", reason="example fixture", evidence_id="e1"))
        put(self.folder, "people", dict(person_id="p1", organization_id="o1", name="Jane Doe",
            department="", role_title="Grounds Manager", evidence_id="e1", status="verified"))
        put(self.folder, "contact_points", dict(point_id="c1", organization_id="o1", person_id="p1",
            type="email", literal_value="jane@example.org", evidence_id="e1", status="verified"))
        put(self.folder, "contact_routes", dict(route_id="r1", organization_id="o1",
            scene="gb-grounds", person_id="p1", point_id="c1", role_function="grounds management",
            grade="A", status="ready", evidence_id="e1", reviewed_by="human-reviewer"))
        report = rp.verify(self.folder)
        self.assertEqual("STRUCTURALLY_VALID_NOT_BUSINESS_APPROVED", report["result"], report["errors"])
        self.assertEqual(1, report["statuses"]["contact_routes"]["ready"])
        self.assertEqual("NOT_TESTED", report["business_approval"])

    def test_fake_verified_contact_cannot_be_promoted(self):
        put(self.folder, "contact_routes", dict(route_id="r1", organization_id="nonexistent", scene="gb-grounds",
            person_id="", point_id="missing", role_function="buyer", grade="A",
            status="ready", evidence_id="missing", reviewed_by=""))
        report = rp.verify(self.folder)
        self.assertEqual("INVALID", report["result"])
        self.assertTrue(any("ready route" in err for err in report["errors"]))

    def test_review_queue_requires_real_object(self):
        put(self.folder, "review_queue", dict(review_id="review1", object_type="venue",
            object_id="missing", reason="conflict", status="open", reviewer=""))
        self.assertEqual("INVALID", rp.verify(self.folder)["result"])

class WorkBuddyBundleTests(unittest.TestCase):
    def test_generated_skill_only_bundle_is_repeatable(self):
        with tempfile.TemporaryDirectory() as tmp:
            out1, out2 = Path(tmp) / "one", Path(tmp) / "two"
            build1, build2 = cb.build(out1), cb.build(out2)
            self.assertEqual(build1["zip_sha256"], build2["zip_sha256"])
            pkg = Path(build1["directory"])
            meta = json.loads((pkg / "connector-meta.json").read_text(encoding="utf-8"))
            self.assertEqual("skill-only", meta["type"])
            self.assertFalse((pkg / "mcp.json").exists())
            self.assertEqual(14, build1["skills"])
            self.assertTrue((pkg / "scripts" / "experience_pack.py").exists())
            self.assertTrue((pkg / "scripts" / "export_excel_safe_csv.py").exists())

if __name__ == "__main__":
    unittest.main()
