"""Pure-state adversarial tests; fixtures are synthetic, not source verification."""
import copy
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import research_workflow as workflow


def record(rid, kind, key, data, refs=True):
    return {"record_id": rid, "stage": "review", "kind": kind,
            "status": "candidate_with_material" if refs else "candidate_unverified",
            "entity_key": key, "data": data, "artifact_ids": ["synthetic-source"] if refs else []}


def fixture():
    return [
        record("b", "observation", "brief", {"record_type": "research_brief", "target_kind": "organization",
            "goal": "contacts", "minimum_targets": 1, "minimum_targets_unit": "organization",
            "require_named_person": True}, False),
        record("o", "organization", "org", {"name": "Synthetic Org"}),
        record("p", "person", "person", {"organization_id": "org", "name": "Synthetic Person"}),
        record("r", "role", "role", {"organization_id": "org", "person_id": "person", "role_title": "Synthetic Role"}),
        record("c", "contact_point", "contact", {"organization_id": "org", "person_id": "person", "type": "form", "value": "Synthetic Contact"}),
        record("d", "review", "disposition", {"review_type": "target_disposition", "target_kind": "organization",
            "target_entity_key": "org", "disposition": "RESEARCHED_CANDIDATE", "reason": "Synthetic observed result"}),
    ]


class AdversarialWorkflowTests(unittest.TestCase):
    def test_valid_control_is_structurally_ready(self):
        result = workflow.assess_records("synthetic-run", fixture())
        self.assertTrue(result["goal_satisfied"])
        self.assertFalse(result["business_verified"])

    def test_unhashable_brief_fields_return_invalid_not_crash(self):
        for field, value in (("target_kind", []), ("goal", {}), ("minimum_targets", []), ("require_named_person", {})):
            with self.subTest(field=field):
                rows = fixture()
                rows[0]["data"][field] = value
                self.assertEqual("BRIEF_INVALID", workflow.assess_records("synthetic-run", rows)["status"])

    def test_unhashable_supersedes_is_conflict_not_crash(self):
        for value in ([], {}, 1, False):
            with self.subTest(value=value):
                rows = fixture()
                rows[0]["data"]["supersedes"] = value
                self.assertEqual("BRIEF_CONFLICT", workflow.assess_records("synthetic-run", rows)["status"])

    def test_brief_cannot_supersede_different_entity(self):
        rows = fixture()
        replacement = copy.deepcopy(rows[0])
        replacement.update(record_id="b2", entity_key="different-brief")
        replacement["data"]["supersedes"] = "b"
        rows.append(replacement)
        self.assertEqual("BRIEF_CONFLICT", workflow.assess_records("synthetic-run", rows)["status"])

    def test_malformed_disposition_never_crashes_or_passes(self):
        for value in ([], {}, 1, None):
            with self.subTest(value=value):
                rows = fixture()
                rows[-1]["data"]["disposition"] = value
                result = workflow.assess_records("synthetic-run", rows)
                self.assertFalse(result["goal_satisfied"])
                self.assertIn("invalid_disposition", result["gap_counts"])

    def test_superseded_sourced_contact_does_not_hide_missing_new_material(self):
        rows = fixture()
        replacement = copy.deepcopy(rows[4])
        replacement["record_id"] = "c2"
        replacement["data"]["supersedes"] = "c"
        replacement["artifact_ids"] = []
        rows.append(replacement)
        result = workflow.assess_records("synthetic-run", rows)
        self.assertFalse(result["goal_satisfied"])
        self.assertIn("same_person_linked_contact_candidate_with_source", result["gap_counts"])

    def test_superseded_role_cannot_contribute_obsolete_person_join(self):
        rows = fixture()
        replacement = copy.deepcopy(rows[3])
        replacement["record_id"] = "r2"
        replacement["data"].update(supersedes="r", person_id="unrelated-person")
        rows.append(replacement)
        result = workflow.assess_records("synthetic-run", rows)
        self.assertIn("person_linked_role_candidate_with_source", result["gap_counts"])

    def test_a_duplicated_related_reading_resolves_instead_of_blocking(self):
        rows = fixture()
        duplicate = copy.deepcopy(rows[4])
        duplicate["record_id"] = "c2"
        rows.append(duplicate)
        result = workflow.assess_records("synthetic-run", rows)
        self.assertNotIn("related_entity_version_conflict", result["gap_counts"])

    def test_a_cycle_is_still_an_unresolved_conflict(self):
        rows = [{"record_id": "a", "kind": "venue", "entity_key": "v", "artifact_ids": [],
                 "data": {"supersedes": "b"}},
                {"record_id": "b", "kind": "venue", "entity_key": "v", "artifact_ids": [],
                 "data": {"supersedes": "a"}}]
        active, conflicts = workflow.active_records(rows)
        self.assertEqual([], active)
        self.assertNotIn("resolved_to", conflicts[0])

    def test_the_reading_with_more_quoted_claims_wins_a_fork(self):
        thin = {"record_id": "thin", "kind": "organization", "entity_key": "o",
                "artifact_ids": ["p"], "data": {"name": "Club", "claims": [{"field": "identity"}]}}
        rich = {"record_id": "rich", "kind": "organization", "entity_key": "o",
                "artifact_ids": ["p"], "data": {"name": "Club", "claims": [
                    {"field": "identity"}, {"field": "address"}]}}
        for order in ([thin, rich], [rich, thin]):
            active, _ = workflow.active_records(order)
            self.assertEqual("rich", active[0]["record_id"])

    def test_unknown_name_is_not_a_named_person(self):
        rows = fixture()
        rows[2]["data"]["name"] = "UNKNOWN"
        result = workflow.assess_records("synthetic-run", rows)
        self.assertIn("named_person_candidate_with_source", result["gap_counts"])

    def test_snapshot_hash_independent_of_sql_order(self):
        rows = fixture()
        first = workflow.assess_records("synthetic-run", rows)
        second = workflow.assess_records("synthetic-run", list(reversed(rows)))
        self.assertEqual(first["snapshot_id"], second["snapshot_id"])
        self.assertEqual(first["gap_counts"], second["gap_counts"])

    def test_readonly_assessment_does_not_mutate_records(self):
        rows = fixture()
        before = copy.deepcopy(rows)
        workflow.assess_records("synthetic-run", rows)
        self.assertEqual(before, rows)

    def test_cycles_never_auto_resolve(self):
        rows = fixture()
        replacement = copy.deepcopy(rows[-1])
        replacement["record_id"] = "d2"
        replacement["data"]["supersedes"] = "d"
        rows[-1]["data"]["supersedes"] = "d2"
        rows.append(replacement)
        result = workflow.assess_records("synthetic-run", rows)
        self.assertIn("disposition_version_conflict", result["gap_counts"])


if __name__ == "__main__":
    unittest.main()
