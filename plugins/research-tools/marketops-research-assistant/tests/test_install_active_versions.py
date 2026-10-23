"""Append-only history must never duplicate customer-facing workbook rows."""
from __future__ import annotations

import csv
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import research_delivery as delivery
import research_lite_store as store
import research_workflow as workflow


class ActiveWorkbookTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / "data"
        self.run_id = "versions-test"
        store.start_run(self.root, self.run_id, "generic market", "venue inventory", "unknown")
        store.save_artifact(self.root, self.run_id, "art-1",
                            provider="fixture", origin_url="https://example.org/venues",
                            content=b"Sample Venue is located at One Lane.",
                            material_kind="raw_response")
        store.put_candidate(self.root, self.run_id, "brief-1", "qualification",
                            "observation", "research-brief", {
                                "record_type": "research_brief", "target_kind": "venue",
                                "goal": "pool", "require_named_person": False,
                                "minimum_targets": None,
                            })
        store.put_candidate(self.root, self.run_id, "venue-v1", "entities",
                            "venue", "sample-venue", {
                                "name": "Wrong Earlier Venue",
                            }, ["art-1"])

    def test_corrected_venue_is_one_business_row_but_two_audit_rows(self):
        store.put_candidate(self.root, self.run_id, "venue-v2", "entities",
                            "venue", "sample-venue", {
                                "name": "Sample Venue", "supersedes": "venue-v1",
                                "claims": [{"field": "identity", "artifact_id": "art-1",
                                            "quote": "Sample Venue is located at One Lane."}],
                            }, ["art-1"])
        result = delivery.write_delivery(self.root, self.run_id, "delivery-1")
        self.assertEqual(1, result["counts"]["场地与资产"])
        self.assertEqual(3, result["input_record_count"])
        self.assertEqual(2, result["active_record_count"])
        folder = Path(result["directory"])
        with (folder / "场地与资产.csv").open(encoding="utf-8-sig", newline="") as f:
            rows = list(csv.DictReader(f))
        self.assertEqual(1, len(rows))
        self.assertIn("Sample Venue", repr(rows))
        self.assertNotIn("Wrong Earlier Venue", repr(rows))
        with (folder / "候选数据审计.csv").open(encoding="utf-8-sig", newline="") as f:
            audit = list(csv.DictReader(f))
        ids = [r["记录ID"] for r in audit]
        self.assertIn("venue-v1", ids)
        self.assertIn("venue-v2", ids)
        with (folder / "排除与待确认.csv").open(encoding="utf-8-sig", newline="") as f:
            issues = list(csv.DictReader(f))
        self.assertFalse(any(row.get("对象ID") == "venue-v1" for row in issues))

    def _seed_historical_branch(self):
        """Seed a fork directly, as legacy data would have left it.

        New writes refuse this shape (``test_grading_supersedes_write_guard``); the
        export path must still keep an *existing* fork in the audit rather than
        picking a winner, which is what this test verifies.
        """
        import json
        from datetime import datetime, timezone
        with store._connection(self.root) as db:
            db.execute("INSERT INTO records VALUES (?,?,?,?,?,?,?,?,?)", (
                "venue-branch2", self.run_id, "entities", "venue",
                "candidate_with_material", "sample-venue",
                json.dumps({"name": "Branch Two", "supersedes": "venue-v1"},
                           sort_keys=True),
                json.dumps(["art-1"]),
                datetime.now(timezone.utc).isoformat()))

    def test_fork_kept_in_audit_but_neither_branch_exported(self):
        store.put_candidate(self.root, self.run_id, "venue-branch1", "entities",
                            "venue", "sample-venue", {
                                "name": "Branch One", "supersedes": "venue-v1",
                            }, ["art-1"])
        self._seed_historical_branch()
        _, records, _ = delivery._read_snapshot(self.root, self.run_id)
        active, conflicts = workflow.active_records(records)
        self.assertEqual(1, len(conflicts))
        self.assertFalse(any(row["kind"] == "venue" for row in active))
        result = delivery.write_delivery(self.root, self.run_id, "delivery-conflict")
        self.assertEqual(0, result["counts"]["场地与资产"])
        self.assertEqual(1, result["version_conflict_count"])
        folder = Path(result["directory"])
        with (folder / "排除与待确认.csv").open(encoding="utf-8-sig", newline="") as f:
            issues = list(csv.DictReader(f))
        self.assertIn("候选版本冲突", repr(issues))
        with (folder / "候选数据审计.csv").open(encoding="utf-8-sig", newline="") as f:
            audit = list(csv.DictReader(f))
        self.assertEqual(3, sum(r["对象类型"] == "venue" for r in audit))


if __name__ == "__main__":
    unittest.main()
