"""Orchestration fault-injection tests, not a replacement for real stdio E2E."""
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import research_mcp as mcp


class HandoffReceiptTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.trace = []
        self.workbook = {
            "input_record_count": 502, "saved_artifact_count": 3,
            "xlsx_path": str(self.root / "exports" / "Research_Result.xlsx"),
            "directory": str(self.root / "exports"),
            "delivery_completeness": "COMPLETE", "partial_reasons": [],
            "goal_assessment": {"goal_satisfied": False},
            "goal_gap_record": {"status": "GOAL_GAP_RECORDED"},
            "experience_file": str(self.root / "Research_Experience.md"),
            "experience_refresh": {"status": "REFRESHED"},
        }
        self.final_refresh = {"status": "REFRESHED", "experience_file": self.workbook["experience_file"]}
        self.draft_error = None
        self.journal_error = None
        for target, kwargs in (
            ("root", {"return_value": self.root}),
            ("workflow.page", {"return_value": {"scope": "synthetic", "rule_version": "UNKNOWN", "record_count": 1, "artifact_count": 1}}),
            ("delivery.write_delivery", {"side_effect": lambda *a, **k: self.workbook}),
            ("draft_experience", {"side_effect": self.draft}),
            ("append_event", {"side_effect": self.event}),
            ("refresh_summary", {"side_effect": self.refresh}),
        ):
            mocker = patch("research_mcp." + target, **kwargs)
            mocker.start()
            self.addCleanup(mocker.stop)

    def draft(self, *args, **kwargs):
        self.trace.append("draft")
        if self.draft_error:
            raise self.draft_error
        return {"incidents": 1}

    def event(self, *args, **kwargs):
        self.trace.append("event:" + kwargs["status"])
        if self.journal_error:
            raise self.journal_error
        return {"status": "recorded"}

    def refresh(self, *args, **kwargs):
        self.trace.append("refresh")
        return self.final_refresh

    def test_counts_come_from_delivered_snapshot_not_precheck(self):
        receipt = mcp.handoff("run", "delivery")
        self.assertEqual(502, receipt["records"])
        self.assertEqual(3, receipt["artifacts"])
        self.assertEqual("COMPLETE", receipt["delivery_completeness"])
        self.assertFalse(receipt["goal_assessment"]["goal_satisfied"])

    def test_child_goal_note_failure_remains_partial_even_after_refresh(self):
        self.workbook["delivery_completeness"] = "PARTIAL"
        self.workbook["partial_reasons"] = ["GOAL_GAP_RECORD_FAILED"]
        self.workbook["goal_gap_record"] = {"status": "FAILED"}
        receipt = mcp.handoff("run", "delivery")
        self.assertEqual("PARTIAL", receipt["delivery_completeness"])
        self.assertIn("GOAL_GAP_RECORD_FAILED", receipt["partial_reasons"])
        self.assertTrue(receipt["status"].startswith("PARTIAL_HANDOFF"))
        self.assertEqual(self.workbook["xlsx_path"], receipt["workbook_xlsx"])

    def test_summary_refresh_follows_final_handoff_event(self):
        mcp.handoff("run", "delivery")
        self.assertEqual(["draft", "event:succeeded", "refresh"], self.trace)

    def test_late_summary_failure_cannot_reuse_earlier_success(self):
        self.final_refresh = {"status": "FAILED", "reason_code": "SYNTHETIC_FAILURE"}
        receipt = mcp.handoff("run", "delivery")
        self.assertEqual("FAILED", receipt["experience_refresh"]["status"])
        self.assertEqual("REFRESHED", receipt["workbook_experience_refresh"]["status"])
        self.assertEqual("PARTIAL", receipt["delivery_completeness"])
        self.assertIn("event:hold", self.trace)

    def test_draft_failure_keeps_real_workbook_location(self):
        self.draft_error = OSError("Synthetic draft failure")
        receipt = mcp.handoff("run", "delivery")
        self.assertIsNone(receipt["experience_candidate"])
        self.assertEqual("PARTIAL", receipt["delivery_completeness"])
        self.assertEqual(self.workbook["xlsx_path"], receipt["workbook_xlsx"])
        self.assertIn("EXPERIENCE_DRAFT_FAILED_OSError", receipt["partial_reasons"])

    def test_journal_failure_is_visible_without_losing_workbook(self):
        self.journal_error = OSError("Synthetic event failure")
        receipt = mcp.handoff("run", "delivery")
        self.assertEqual("PARTIAL", receipt["delivery_completeness"])
        self.assertEqual(self.workbook["xlsx_path"], receipt["workbook_xlsx"])
        self.assertIn("HANDOFF_EVENT_FAILED_OSError", receipt["partial_reasons"])


if __name__ == "__main__":
    unittest.main()
