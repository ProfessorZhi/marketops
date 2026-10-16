"""A resolved exclusion is not a failure; an unspecified quota is not invented."""
import base64
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import research_lite_store as store
import research_workflow as workflow
from test_workflow_adversarial import fixture, record


def add_disposed_target(rows, outcome):
    rows.append(record("o2", "organization", "org2", {"name": "Synthetic extra org"}))
    rows.append(record("d2", "review", "disposition2", {
        "review_type": "target_disposition", "target_kind": "organization",
        "target_entity_key": "org2", "disposition": outcome,
        "reason": "Synthetic source-supported disposition"}))


class GoalBoundaryTests(unittest.TestCase):
    def test_unspecified_quantity_does_not_force_an_invented_quota(self):
        for omit in (False, True):
            with self.subTest(omit=omit):
                rows = fixture()
                if omit:
                    rows[0]["data"].pop("minimum_targets")
                else:
                    rows[0]["data"]["minimum_targets"] = None
                result = workflow.assess_records("synthetic-run", rows)
                self.assertTrue(result["goal_satisfied"])
                self.assertEqual("UNSPECIFIED", result["quantity_target_status"])
                self.assertIsNone(result["minimum_targets"])
                self.assertFalse(result["coverage_complete"])

    def test_valid_exclusion_is_visible_but_not_a_goal_failure(self):
        rows = fixture()
        add_disposed_target(rows, "EXCLUDED")
        result = workflow.assess_records("synthetic-run", rows)
        self.assertTrue(result["goal_satisfied"])
        self.assertEqual(1, result["disposition_counts"]["EXCLUDED"])
        self.assertEqual(0, result["blocked_target_count"])

    def test_valid_duplicate_is_visible_but_not_a_goal_failure(self):
        rows = fixture()
        add_disposed_target(rows, "DUPLICATE")
        result = workflow.assess_records("synthetic-run", rows)
        self.assertTrue(result["goal_satisfied"])
        self.assertEqual(1, result["disposition_counts"]["DUPLICATE"])
        self.assertEqual(1, result["researched_target_count"])

    def test_exclusion_does_not_count_toward_explicit_quota(self):
        rows = fixture()
        rows[0]["data"]["minimum_targets"] = 2
        add_disposed_target(rows, "EXCLUDED")
        result = workflow.assess_records("synthetic-run", rows)
        self.assertFalse(result["goal_satisfied"])
        self.assertEqual(1, result["gap_counts"]["minimum_researched_targets_shortfall"])
        self.assertEqual("discovery", result["stage"])
        self.assertNotIn("target_hold", result["gap_counts"])

    def test_unresolved_hold_remains_a_gap(self):
        rows = fixture()
        add_disposed_target(rows, "HOLD")
        result = workflow.assess_records("synthetic-run", rows)
        self.assertEqual("STOPPED_WITH_GAPS", result["status"])
        self.assertFalse(result["goal_satisfied"])
        self.assertEqual(1, result["gap_counts"]["target_hold"])

    def test_all_excluded_unquantified_sample_is_not_false_green(self):
        rows = fixture()
        rows[0]["data"]["minimum_targets"] = None
        rows[-1]["data"]["disposition"] = "EXCLUDED"
        result = workflow.assess_records("synthetic-run", rows)
        self.assertEqual("STOPPED_WITH_GAPS", result["status"])
        self.assertEqual(1, result["gap_counts"]["no_researched_candidates"])
        self.assertFalse(result["coverage_complete"])

    def test_empty_unquantified_pool_still_requires_research(self):
        rows = fixture()[:1]
        rows[0]["data"]["minimum_targets"] = None
        result = workflow.assess_records("synthetic-run", rows)
        self.assertEqual("discovery", result["stage"])
        self.assertFalse(result["goal_satisfied"])

    def test_cursor_overflow_and_boolean_version_fail_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            store.start_run(root, "run", "synthetic", "synthetic", "UNKNOWN")
            for version, high in ((1, 2**100), (True, 0)):
                with self.subTest(version=version, high=high):
                    cursor = base64.urlsafe_b64encode(json.dumps({
                        "v": version, "binding": ["run", "", ""], "high": high, "after": 0
                    }).encode()).decode()
                    with self.assertRaisesRegex(ValueError, "PAGE_CURSOR"):
                        workflow.page(root, "run", cursor=cursor)


if __name__ == "__main__":
    unittest.main()
