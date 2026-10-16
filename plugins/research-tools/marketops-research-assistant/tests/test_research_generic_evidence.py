"""Scene-neutral evidence gates, bounded context and batch staging — offline only."""
from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import research_evidence as evidence
import research_lite_store as store
import research_workflow as workflow
import research_provider_extensions as provider


def candidate(record_id, kind, facts, ids=None, entity_key=None):
    return {"record_id": record_id, "kind": kind, "stage": "entities",
            "status": "candidate_with_material" if ids else "candidate_unverified",
            "entity_key": entity_key or record_id, "data": facts,
            "artifact_ids": ids or []}


def source(artifact_id, text, kind="provider_extracted"):
    return {artifact_id: {"artifact_id": artifact_id,
                          "kind": kind, "text": text}}


class EvidenceTests(unittest.TestCase):
    def test_no_scene_specific_business_words_are_necessary(self):
        brief = candidate("brief", "observation", {
            "record_type": "research_brief", "target_kind": "organization",
            "goal": "pool", "require_named_person": False,
            "evidence_requirements": {"organization": ["identity", "regulatory_license"]}})
        org = candidate("org1", "organization", {"name": "Acme Facility",
            "claims": [
                {"field": "identity", "artifact_id": "html1",
                 "quote": "Acme Facility manufactures precision gauges."},
                {"field": "regulatory_license", "artifact_id": "html1",
                 "quote": "Acme Facility holds certification Q-123."}]}, ["html1"])
        artifacts = source("html1", "<p>Acme Facility manufactures precision gauges.</p>\n"
                                  "<p>Acme Facility holds certification Q-123.</p>", "rendered_html")
        report = evidence.audit_records([brief, org], artifacts)
        self.assertEqual({}, report["gap_counts"])
        self.assertEqual("QUOTES_PRESENT_NOT_FACT_VERIFIED", report["status"])
        self.assertFalse(report["facts_verified"])

    def test_unrelated_page_attached_is_not_sufficient(self):
        v = candidate("v1", "venue", {"name": "Riverside Field"}, ["a1"])
        report = evidence.audit_records([v], source("a1", '{"markdown":"Another park has grass."}'))
        self.assertIn("MISSING_REQUIRED_IDENTITY", report["gap_counts"])
        self.assertIn("NO_EXCERPT", report["gap_counts"])

    def test_brief_requirements_cover_arbitrary_fields(self):
        brief = candidate("br", "observation", {
            "record_type": "research_brief", "target_kind": "venue", "goal": "pool",
            "require_named_person": False, "evidence_requirements": {"venue": ["surface", "location"]}})
        venue = candidate("v", "venue", {"name": "Another Venue",
            "claims": [{"field": "identity", "artifact_id": "art",
                         "quote": "Another Venue is open to visitors."}]}, ["art"])
        report = evidence.audit_records([brief, venue], source("art", "Another Venue is open to visitors.", "rendered_html"))
        self.assertIn("MISSING_REQUIRED_SURFACE", report["gap_counts"])
        self.assertIn("MISSING_REQUIRED_LOCATION", report["gap_counts"])

    def test_hard_exclusion_needs_excerpt(self):
        rev = candidate("exclude", "review", {"disposition": "EXCLUDED",
                                               "reason": "not within client scope"}, ["a1"])
        report = evidence.audit_records([rev], source("a1", "A valid, unrelated directory.", "rendered_html"))
        self.assertIn("MISSING_REQUIRED_EXCLUSION", report["gap_counts"])

    def test_typed_relation_requires_excerpt_not_just_id(self):
        rel = candidate("rel", "relationship", {"venue_id": "venue1",
             "organization_id": "org1", "relationship_type": "operates"}, ["a1"])
        result = evidence.audit_records([rel], source("a1", "Club contact listing", "rendered_html"))
        self.assertIn("MISSING_REQUIRED_RELATIONSHIP", result["gap_counts"])

    def test_markdown_can_invent_a_plausible_email_by_joining_words(self):
        point = candidate("cp", "contact_point", {
            "type": "email", "value": "onevents@club.example",
            "claims": [{"field": "email", "artifact_id": "derived",
                        "quote": "Contact Member-A onevents@club.example"}]},
            ["derived", "rendered"])
        artifacts = {**source("derived", '{"markdown":"Contact Member-A onevents@club.example"}'),
                     **source("rendered", "<span>Contact Member-A on</span><a "
                             "href='mailto:events@club.example'>events@club.example</a>", "rendered_html")}
        report = evidence.audit_records([point], artifacts)
        self.assertIn("EMAIL_NOT_IN_RENDERED_HTML", report["gap_counts"])
        self.assertIn("DERIVED_EXCERPT_ONLY", report["gap_counts"])

    def test_correct_rendered_email_still_is_not_approval(self):
        point = candidate("cp", "contact_point", {
            "type": "email", "value": "events@club.example",
            "claims": [{"field": "email", "artifact_id": "rendered",
                        "quote": "Contact Member-A on events@club.example"}]},
            ["rendered"])
        artifacts = source("rendered", "<p>Contact Member-A on events@club.example</p>", "rendered_html")
        report = evidence.audit_records([point], artifacts)
        self.assertNotIn("EMAIL_NOT_IN_RENDERED_HTML", report["gap_counts"])
        self.assertFalse(report["formal_sop_approved"])

    def test_decision_is_not_an_authoritative_source(self):
        org = candidate("org", "organization", {"name": "Target Ltd",
            "claims": [{"field": "identity", "artifact_id": "a",
                        "quote": "Target Ltd manufactures products"}]}, ["a"])
        report = evidence.audit_records([org], source("a", "Target Ltd manufactures products", "decision_response"))
        self.assertIn("DECISION_NOT_EVIDENCE", report["gap_counts"])

    def test_invalid_scene_keys_fail_closed(self):
        brief = candidate("br", "observation", {
            "record_type": "research_brief", "target_kind": "venue",
            "goal": "pool", "require_named_person": False,
            "evidence_requirements": {"venue": ["unsupported/field"]}})
        report = evidence.audit_records([brief], {})
        self.assertTrue(report["invalid_brief_evidence_requirements"])


class BatchAndExcerptTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / "research"
        store.start_run(self.root, "run-1", "region-neutral", "generic industry research", "rules-unknown")
        store.save_artifact(self.root, "run-1", "art-1", "local",
                            "https://public.example.org/source",
                            b"<p>Acme Facility holds certification Q-123.</p>",
                            "rendered_html")

    def item(self, rec="org-1", facts=None):
        return {"record_id": rec, "stage": "entities", "kind": "organization",
                "entity_key": rec, "facts": facts or {"name": "Acme Facility"},
                "artifact_ids": ["art-1"]}

    def test_experience_note_keeps_required_opaque_evidence_ref(self):
        from research_event_journal import append_note
        args = dict(run_id="run-1", note_id="note-1", stage="review",
                    category="evidence_missing", problem="Published claim missing",
                    action_taken="Requested source citation",
                    observed_result="Pending manual source inspection")
        with self.assertRaisesRegex(ValueError, "evidence_ref"):
            append_note(self.root, **args)
        result = append_note(self.root, **args, evidence_ref="art-1")
        self.assertEqual("RECORDED", result["result"])
        replay = append_note(self.root, **args, evidence_ref="art-1")
        self.assertEqual("IDEMPOTENT", replay["result"])

    def test_batch_rolls_back_everything_on_invalid_second_record(self):
        with self.assertRaises(ValueError):
            store.put_candidates_batch(self.root, "run-1",
                [self.item("good"), self.item("bad", {"approved": True})])
        self.assertEqual(0, store.list_run(self.root, "run-1", limit=1)["record_count"])

    def test_batch_idempotent_and_bounded(self):
        first = store.put_candidates_batch(self.root, "run-1", [self.item("a"), self.item("b")])
        self.assertEqual(2, first["created"])
        again = store.put_candidates_batch(self.root, "run-1", [self.item("a"), self.item("b")])
        self.assertEqual(2, again["idempotent"])
        self.assertEqual(2, store.list_run(self.root, "run-1", limit=4)["record_count"])
        with self.assertRaisesRegex(ValueError, "BATCH_SIZE"):
            store.put_candidates_batch(self.root, "run-1", [self.item(str(i)) for i in range(21)])

    def test_excerpt_is_small_and_sha_checked(self):
        receipt = evidence.excerpts(self.root, "run-1", ["art-1"], ["certification"])
        self.assertEqual("rendered_html", receipt["results"][0]["material_kind"])
        self.assertLessEqual(sum(len(s) for row in receipt["results"] for s in row["snippets"]), 1200)
        state = self.root / "artifacts" / "run-1"
        next(state.glob("*.bin")).write_bytes(b"tampered")
        with self.assertRaisesRegex(ValueError, "HASH_MISMATCH"):
            evidence.excerpts(self.root, "run-1", ["art-1"])

    def test_explicit_count_unit_is_required(self):
        def assess(unit):
            b = candidate("brief", "observation", {
                "record_type": "research_brief", "target_kind": "organization",
                "goal": "contacts", "require_named_person": True,
                "minimum_targets": 10, **({"minimum_targets_unit": unit} if unit else {})})
            return workflow.assess_records("run-1", [b])
        self.assertEqual("BRIEF_UNIT_REVIEW_REQUIRED", assess(None)["status"])
        self.assertEqual("BRIEF_INVALID", assess("venue")["status"])
        self.assertNotEqual("BRIEF_INVALID", assess("organization")["status"])

    def test_crawl_result_keeps_rendered_html_but_not_in_preview(self):
        with patch.object(provider, "_crawl4ai_local", return_value={
                "url": "https://public.example.org/source",
                "markdown": "Acme Facility holds certification Q-123.",
                "_rendered_html": "<html><p>Acme Facility holds certification Q-123.</p></html>",
                "material_kind": "provider_extracted"}):
            with patch.dict(os.environ, {
                "MARKETOPS_RESEARCH_DATA_ROOT": str(self.root),
                "MARKETOPS_RESEARCH_ALLOW_NETWORK": "1",
                "MARKETOPS_RESEARCH_MAX_EXTERNAL_CALLS": "2",
                "MARKETOPS_FETCH_ALLOWED_DOMAINS": "public.example.org"}, clear=False):
                # This unit test never installs or launches a browser; the
                # injected crawler result is the controlled source fixture.
                with patch.object(provider.importlib.util, "find_spec", return_value=object()):
                    receipt = provider.invoke("crawl4ai_local", run_id="run-1",
                                              operation_id="op-1",
                                              url="https://public.example.org/source")
        self.assertEqual("rendered_html", receipt["rendered_artifact"]["material_kind"])
        self.assertNotIn("<html>", receipt["preview"])
        self.assertEqual(2, len(receipt["record"]["artifact_ids"]))


if __name__ == "__main__":
    unittest.main()
