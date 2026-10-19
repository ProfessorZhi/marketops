"""Regression tests for real store state, bounded routing and honest goal gaps."""
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import research_lite_store as store
import research_workflow as workflow
from research_event_journal import read_events


class WorkflowTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        store.start_run(self.root, "run-a", "synthetic scope", "named research", "UNKNOWN")
        store.save_artifact(self.root, "run-a", "a1", "fixture", "https://example.invalid",
                            b"Synthetic test source; not a real contact.", "raw_response")

    def put(self, rid, kind, key, facts=None, refs=None):
        return store.put_candidate(self.root, "run-a", rid, "review", kind, key,
                                   facts or {}, refs or [])

    def brief(self, named=True, goal="contacts", minimum=1, kind="organization"):
        self.put("brief", "observation", "brief", {
            "record_type": "research_brief", "target_kind": kind, "goal": goal,
            "minimum_targets": minimum, "minimum_targets_unit": kind,
            "require_named_person": named})

    def organization(self):
        self.put("org-r", "organization", "org", {"name": "Synthetic Organization"}, ["a1"])

    def named_route(self):
        self.put("person-r", "person", "person", {"name": "Synthetic Person", "organization_id": "org"}, ["a1"])
        self.put("role-r", "role", "role", {"organization_id": "org", "person_id": "person", "role_title": "Synthetic role"}, ["a1"])
        self.put("contact-r", "contact_point", "contact", {"organization_id": "org", "person_id": "person", "type": "form", "value": "Synthetic public form"}, ["a1"])

    def disposition(self, outcome, rid="review-r", **extra):
        self.put(rid, "review", "org-review", {"review_type": "target_disposition",
            "target_kind": "organization", "target_entity_key": "org",
            "disposition": outcome, "reason": "Synthetic observed result", **extra})

    def test_unknown_goal_never_inferred_from_prose(self):
        self.organization()
        result = workflow.next_step(self.root, "run-a")
        self.assertEqual("BRIEF_REQUIRED", result["status"])
        self.assertFalse(result["goal_satisfied"])

    def test_empty_valid_run_selects_discovery(self):
        self.brief()
        self.assertEqual("discovery", workflow.next_step(self.root, "run-a")["stage"])

    def test_existing_source_selects_entity_extraction(self):
        self.brief()
        self.put("src", "source", "src", {"title": "Synthetic"})
        self.assertEqual("entities", workflow.next_step(self.root, "run-a")["stage"])

    def test_known_organization_does_not_restart_discovery(self):
        self.brief()
        self.organization()
        result = workflow.next_step(self.root, "run-a")
        self.assertEqual("marketops-person-research", result["recommended_skill"])
        self.assertEqual(["org"], result["target_ids"])

    def test_missing_entity_material_selects_evidence(self):
        self.brief()
        self.put("org-r", "organization", "org", {"name": "Synthetic"})
        self.assertEqual("acquisition", workflow.next_step(self.root, "run-a")["stage"])

    def test_department_mailbox_cannot_satisfy_named_person_goal(self):
        self.brief()
        self.organization()
        self.put("role-r", "role", "role", {"organization_id": "org", "role_title": "Department"}, ["a1"])
        self.put("contact-r", "contact_point", "contact", {"organization_id": "org", "type": "email", "value": "department@example.invalid"}, ["a1"])
        self.disposition("RESEARCHED_CANDIDATE")
        result = workflow.next_step(self.root, "run-a")
        self.assertFalse(result["goal_satisfied"])
        self.assertIn("same_person_linked_contact_candidate_with_source", result["gap_counts"])

    def test_unrelated_person_contact_not_joined_to_role(self):
        self.brief()
        self.organization()
        self.named_route()
        self.put("person2-r", "person", "person2", {"name": "Second Synthetic Person", "organization_id": "org"}, ["a1"])
        # A separate snapshot tests the same-person join without changing stored facts.
        records = workflow.page(self.root, "run-a")["records"]
        next(r for r in records if r["kind"] == "contact_point")["data"]["person_id"] = "person2"
        result = workflow.assess_records("run-a", records)
        self.assertIn("same_person_linked_contact_candidate_with_source", result["gap_counts"])

    def test_disposition_cannot_hide_missing_facts(self):
        self.brief()
        self.organization()
        self.disposition("RESEARCHED_CANDIDATE")
        self.assertEqual("people", workflow.next_step(self.root, "run-a")["stage"])

    def test_complete_candidate_is_never_business_verified(self):
        self.brief()
        self.organization()
        self.named_route()
        self.disposition("RESEARCHED_CANDIDATE")
        result = workflow.next_step(self.root, "run-a")
        self.assertEqual("READY_FOR_CANDIDATE_REVIEW", result["status"])
        self.assertTrue(result["goal_satisfied"])
        for field in ("business_verified", "send_authorized", "coverage_complete"):
            self.assertIs(False, result[field])

    def test_hold_is_explicit_stop_not_success(self):
        self.brief()
        self.organization()
        self.disposition("HOLD")
        result = workflow.next_step(self.root, "run-a")
        self.assertEqual("STOPPED_WITH_GAPS", result["status"])
        self.assertEqual(1, result["blocked_target_count"])
        self.assertFalse(result["goal_satisfied"])

    def test_hold_can_be_corrected_without_overwriting_history(self):
        self.brief()
        self.organization()
        self.disposition("HOLD")
        self.named_route()
        self.disposition("RESEARCHED_CANDIDATE", rid="review-r2", supersedes="review-r")
        self.assertTrue(workflow.next_step(self.root, "run-a")["goal_satisfied"])

    def test_forked_dispositions_are_conflicts(self):
        self.brief()
        self.organization()
        self.disposition("HOLD")
        self.disposition("EXCLUDED", rid="review-r2")
        result = workflow.next_step(self.root, "run-a")
        self.assertIn("disposition_version_conflict", result["gap_counts"])

    def test_invalid_brief_and_boolean_integer_rejected(self):
        self.brief(minimum=True)
        self.assertEqual("BRIEF_INVALID", workflow.next_step(self.root, "run-a")["status"])

    def test_venue_pool_does_not_require_people(self):
        self.brief(named=False, goal="pool", kind="venue")
        self.put("v1", "venue", "venue", {"name": "Synthetic Venue"}, ["a1"])
        result = workflow.next_step(self.root, "run-a")
        self.assertEqual("review", result["stage"])
        self.assertEqual(["target_disposition"], result["targets"][0]["missing_facts"])

    def test_work_batch_bounded(self):
        self.brief()
        for i in range(40):
            self.put(f"org-{i}", "organization", f"org-{i}", {"name": "Synthetic"}, ["a1"])
        result = workflow.next_step(self.root, "run-a", limit=7)
        self.assertEqual(40, result["pending_target_count"])
        self.assertEqual(7, result["returned_targets"])

    def test_more_than_500_rows_paged_without_new_appends(self):
        for i in range(503):
            self.put(f"row-{i}", "observation", f"row-{i}", {"value": i})
        first = workflow.page(self.root, "run-a", limit=100)
        self.put("late-row", "observation", "late-row", {"value": "late"})
        seen = [r["record_id"] for r in first["records"]]
        cursor = first["next_cursor"]
        while cursor:
            page = workflow.page(self.root, "run-a", limit=100, cursor=cursor)
            self.assertEqual(503, page["record_count"])
            seen.extend(r["record_id"] for r in page["records"])
            cursor = page["next_cursor"]
        self.assertEqual(503, len(set(seen)))
        self.assertNotIn("late-row", seen)
        self.assertEqual(504, workflow.page(self.root, "run-a")["record_count"])

    def test_cursor_bound_to_run_and_filters(self):
        self.brief()
        self.organization()
        cursor = workflow.page(self.root, "run-a", limit=1)["next_cursor"]
        with self.assertRaisesRegex(ValueError, "MISMATCHED_PAGE_CURSOR"):
            workflow.page(self.root, "run-a", cursor=cursor, kind="organization")
        store.start_run(self.root, "run-b", "scope", "objective", "UNKNOWN")
        with self.assertRaisesRegex(ValueError, "MISMATCHED_PAGE_CURSOR"):
            workflow.page(self.root, "run-b", cursor=cursor)

    def test_page_filters_and_invalid_cursor(self):
        self.brief()
        self.organization()
        page = workflow.page(self.root, "run-a", kind="organization", entity_key="org")
        self.assertEqual(1, page["record_count"])
        with self.assertRaisesRegex(ValueError, "PAGE_CURSOR"):
            workflow.page(self.root, "run-a", cursor="not-base64")

    def test_next_does_not_create_a_journal(self):
        self.brief()
        workflow.next_step(self.root, "run-a")
        self.assertFalse((self.root / "state" / "research-events.sqlite3").exists())

    def test_goal_gap_recording_is_idempotent(self):
        self.brief()
        self.organization()
        assessment = workflow.next_step(self.root, "run-a")
        workflow.record_goal_gaps(self.root, "run-a", assessment)
        workflow.record_goal_gaps(self.root, "run-a", assessment)
        events = read_events(self.root, "run-a")
        self.assertEqual(1, len(events))
        self.assertEqual("hold", events[0]["status"])

    def test_server_side_row_budget_is_explicit(self):
        with self.assertRaisesRegex(ValueError, "TOO_MANY_RECORDS"):
            workflow.assess_records("run-a", [{}] * (workflow.MAX_ROWS + 1))


if __name__ == "__main__":
    unittest.main()
