"""RC5: a review may only supersede the same review_type.

A `review_type=note` carrying `facts.supersedes=<live HOLD disposition>` used to
become the live tip because kind/stage/entity_key matched. The HOLD disappeared
and a held venue moved from 0 to 1. These controls pin the equality rule, its
batch atomicity and the legal same-type revision chain.

Synthetic, offline material only.
"""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import research_delivery as delivery  # noqa: E402
import research_lite_store as store  # noqa: E402
import research_workflow as workflow  # noqa: E402

RUN = "rc5-review-type"
PAGE = b"<html><body><h1>Example Council</h1><p>Example Council operates Main Hall.</p></body></html>"


class ReviewTypeCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / "data"
        store.start_run(self.root, RUN, "synthetic", "venue pool", "rc5-rules")
        store.save_artifact(self.root, RUN, "page", "fixture",
                            "https://example.org", PAGE, "raw_response")
        store.put_candidate(self.root, RUN, "brief-1", "qualification", "observation",
                            "brief-1", {
                                "record_type": "research_brief",
                                "target_kind": "venue", "goal": "pool",
                                "minimum_targets": 1, "minimum_targets_unit": "venue",
                                "require_named_person": False,
                            })
        store.put_candidate(self.root, RUN, "org-1", "entities", "organization",
                            "example-council", {
                                "name": "Example Council",
                                "claims": [{"field": "identity", "artifact_id": "page",
                                            "quote": "Example Council"}],
                            }, ["page"])
        store.put_candidate(self.root, RUN, "venue-1", "entities", "venue", "main-hall", {
            "name": "Main Hall", "organization_id": "example-council",
            "claims": [{"field": "identity", "artifact_id": "page",
                        "quote": "Example Council operates Main Hall."}],
        }, ["page"])

    def review(self, record_id, review_type="target_disposition", *, supersedes=None,
               disposition="HOLD", target_key="main-hall", entity_key="disp-main"):
        facts = {"review_type": review_type, "reason": "synthetic review"}
        if review_type == "target_disposition":
            facts.update({"target_kind": "venue", "target_entity_key": target_key,
                          "disposition": disposition})
        if supersedes is not None:
            facts["supersedes"] = supersedes
        return store.put_candidate(self.root, RUN, record_id, "review", "review",
                                   entity_key, facts, ["page"])

    def counts(self):
        return delivery.build_delivery(self.root, RUN)[1]["counts"]

    def records(self):
        return store.list_run(self.root, RUN, limit=500)["records"]


class NoteCannotReplaceDisposition(ReviewTypeCase):
    def test_single_note_superseding_hold_is_refused_and_venue_stays_zero(self):
        self.review("disp-hold")
        self.assertEqual(0, self.counts()["场地与资产"])
        with self.assertRaises(ValueError) as ctx:
            self.review("note-1", review_type="note", supersedes="disp-hold")
        self.assertTrue(str(ctx.exception).startswith("SUPERSEDES_REVIEW_TYPE_MISMATCH:"))
        self.assertIn("reuse review_type 'target_disposition'", str(ctx.exception))
        self.assertNotIn("note-1", {row["record_id"] for row in self.records()})
        self.assertEqual(0, self.counts()["场地与资产"],
                         "a refused note must not release the held venue")

    def test_batch_note_superseding_hold_is_atomic(self):
        self.review("disp-hold")
        items = [
            {"record_id": "venue-extra", "stage": "entities", "kind": "venue",
             "entity_key": "extra-hall", "facts": {"name": "Extra Hall"}},
            {"record_id": "note-1", "stage": "review", "kind": "review",
             "entity_key": "disp-main",
             "facts": {"review_type": "note", "supersedes": "disp-hold",
                       "reason": "synthetic note"}},
        ]
        with self.assertRaises(ValueError) as ctx:
            store.put_candidates_batch(self.root, RUN, items)
        self.assertIn("SUPERSEDES_REVIEW_TYPE_MISMATCH", str(ctx.exception))
        ids = {row["record_id"] for row in self.records()}
        self.assertNotIn("note-1", ids)
        self.assertNotIn("venue-extra", ids, "the valid item must roll back with the bad one")
        self.assertEqual(0, self.counts()["场地与资产"])

    def test_a_review_without_review_type_cannot_replace_a_disposition(self):
        self.review("disp-hold")
        with self.assertRaises(ValueError) as ctx:
            store.put_candidate(self.root, RUN, "review-bare", "review", "review",
                                "disp-main", {"supersedes": "disp-hold"})
        self.assertIn("SUPERSEDES_REVIEW_TYPE_MISMATCH", str(ctx.exception))
        self.assertEqual(0, self.counts()["场地与资产"])

    def test_a_disposition_cannot_replace_a_note(self):
        self.review("note-0", review_type="note", entity_key="review-notes")
        with self.assertRaises(ValueError) as ctx:
            self.review("disp-1", supersedes="note-0", entity_key="review-notes")
        self.assertIn("SUPERSEDES_REVIEW_TYPE_MISMATCH", str(ctx.exception))


class SameTypeRevisionsPass(ReviewTypeCase):
    def test_same_review_type_notes_chain(self):
        self.review("note-1", review_type="note", entity_key="review-notes")
        self.review("note-2", review_type="note", supersedes="note-1",
                    entity_key="review-notes")
        active, conflicts = workflow.active_records(self.records())
        self.assertEqual([], conflicts)
        notes = [row["record_id"] for row in active
                 if row["kind"] == "review" and row["data"].get("review_type") == "note"]
        self.assertEqual(["note-2"], notes)

    def test_hold_to_researched_to_second_revision(self):
        self.review("disp-hold")
        self.assertEqual(0, self.counts()["场地与资产"])
        self.review("disp-researched", disposition="RESEARCHED_CANDIDATE",
                    supersedes="disp-hold")
        self.assertEqual(1, self.counts()["场地与资产"])
        self.review("disp-researched-2", disposition="RESEARCHED_CANDIDATE",
                    supersedes="disp-researched")
        self.assertEqual(1, self.counts()["场地与资产"])
        active, conflicts = workflow.active_records(self.records())
        self.assertEqual([], conflicts)
        dispositions = [row["record_id"] for row in active
                        if row["kind"] == "review"
                        and row["data"].get("review_type") == "target_disposition"]
        self.assertEqual(["disp-researched-2"], dispositions)
        audit = {row["record_id"] for row in self.records()}
        self.assertTrue({"disp-hold", "disp-researched", "disp-researched-2"} <= audit,
                        "the complete revision history stays in the audit")


if __name__ == "__main__":
    unittest.main()
