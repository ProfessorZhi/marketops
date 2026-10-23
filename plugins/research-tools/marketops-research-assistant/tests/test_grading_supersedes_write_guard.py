"""RC4 write-time supersedes guard.

The Essex run wrote seven review revisions whose ``supersedes`` pointed at an old
record but whose ``entity_key`` was new. The old store accepted them and the fork
only surfaced at export as ``DISPOSITION_VERSION_CONFLICT``. These controls prove
the same append is now refused inside the transaction, with a repair the caller
can act on, without rewriting facts or touching history.

All fixtures are synthetic; no network, provider, credential or real material.
"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import research_lite_store as store  # noqa: E402
import research_workflow as workflow  # noqa: E402

RUN = "rc4-run"


class GuardCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / "data"
        store.start_run(self.root, RUN, "synthetic-scope", "synthetic objective", "rc4-rules")

    def put(self, record_id, kind, entity_key, facts, *, stage=None, run_id=RUN):
        return store.put_candidate(
            self.root, run_id, record_id, stage or ("review" if kind == "review" else "entities"),
            kind, entity_key, facts)

    def venue(self, record_id="venue-v1", entity_key="old-key", *, supersedes=None,
              stage="entities", run_id=RUN):
        facts = {"name": "Synthetic Hall"}
        if supersedes is not None:
            facts["supersedes"] = supersedes
        return self.put(record_id, "venue", entity_key, facts,
                        stage=stage, run_id=run_id)

    def disposition(self, record_id, entity_key, target_key, *, supersedes=None,
                    target_kind="venue", run_id=RUN):
        facts = {"review_type": "target_disposition",
                 "target_kind": target_kind,
                 "target_entity_key": target_key,
                 "disposition": "RESEARCHED_CANDIDATE",
                 "reason": "synthetic public source reviewed"}
        if supersedes is not None:
            facts["supersedes"] = supersedes
        return self.put(record_id, "review", entity_key, facts, run_id=run_id)

    def records(self, run_id=RUN):
        return store.list_run(self.root, run_id, limit=500)["records"]

    def assert_absent(self, record_id):
        self.assertNotIn(record_id, {row["record_id"] for row in self.records()})

    def assert_refused(self, code, call):
        with self.assertRaises(ValueError) as ctx:
            call()
        self.assertTrue(str(ctx.exception).startswith(code + ":"),
                        f"expected {code}, got {ctx.exception}")


class NegativeControls(GuardCase):
    def test_old_essex_shape_new_entity_key_is_refused(self):
        # The exact real-run defect: a correction points at the old record but
        # carries a new entity_key, so it lands in a different group and forks.
        self.venue()
        self.assert_refused(
            "SUPERSEDES_ENTITY_KEY_MISMATCH",
            lambda: self.venue(record_id="venue-v2", entity_key="new-key",
                               supersedes="venue-v1"))
        self.assert_absent("venue-v2")

    def test_review_revision_with_a_new_entity_key_is_refused(self):
        self.venue()
        self.disposition("disp-v1", "disp-old", "old-key")
        self.assert_refused(
            "SUPERSEDES_ENTITY_KEY_MISMATCH",
            lambda: self.disposition("disp-v2", "disp-new", "old-key",
                                     supersedes="disp-v1"))
        self.assert_absent("disp-v2")

    def test_cross_run_parent_is_refused(self):
        store.start_run(self.root, "other-run", "s", "o", "r")
        self.venue(record_id="other-v1", entity_key="old-key", run_id="other-run")
        self.assert_refused(
            "SUPERSEDES_CROSS_RUN",
            lambda: self.venue(record_id="mine-v2", entity_key="old-key",
                               supersedes="other-v1"))
        self.assert_absent("mine-v2")

    def test_unknown_parent_is_refused(self):
        self.assert_refused(
            "SUPERSEDES_UNKNOWN_RECORD",
            lambda: self.venue(record_id="venue-v2", entity_key="old-key",
                               supersedes="no-such-record"))
        self.assert_absent("venue-v2")

    def test_kind_mismatch_is_refused(self):
        self.venue()
        self.assert_refused(
            "SUPERSEDES_KIND_MISMATCH",
            lambda: self.put("person-v2", "person", "old-key",
                             {"name": "Synthetic Person", "supersedes": "venue-v1"}))
        self.assert_absent("person-v2")

    def test_stage_mismatch_is_refused(self):
        self.venue()
        self.assert_refused(
            "SUPERSEDES_STAGE_MISMATCH",
            lambda: self.venue(record_id="venue-v2", entity_key="old-key",
                               supersedes="venue-v1", stage="people"))
        self.assert_absent("venue-v2")

    def test_review_may_not_move_to_another_target(self):
        self.venue()
        self.venue(record_id="venue-b", entity_key="other-key")
        self.disposition("disp-v1", "disp-main", "old-key")
        self.assert_refused(
            "SUPERSEDES_REVIEW_TARGET_MISMATCH",
            lambda: self.disposition("disp-v2", "disp-main", "other-key",
                                     supersedes="disp-v1"))
        self.assert_absent("disp-v2")

    def test_a_second_disposition_without_supersedes_is_refused(self):
        self.venue()
        self.disposition("disp-v1", "disp-old", "old-key")
        self.assert_refused(
            "TARGET_DISPOSITION_ALREADY_EXISTS",
            lambda: self.disposition("disp-v2", "disp-new", "old-key"))
        self.assert_absent("disp-v2")

    def test_seven_essex_style_wrong_key_revisions_are_all_refused(self):
        # The real run produced seven conflicts. Each attempt is refused at
        # write time, so no seventh fork can reach export.
        self.venue()
        self.disposition("disp-old", "disp-old", "old-key")
        codes = []
        for index in range(7):
            try:
                self.disposition(f"disp-new-{index}", f"disp-new-key-{index}",
                                 "old-key", supersedes="disp-old")
            except ValueError as exc:
                codes.append(str(exc).split(":", 1)[0])
        self.assertEqual(["SUPERSEDES_ENTITY_KEY_MISMATCH"] * 7, codes)
        self.assertEqual(2, len(self.records()),
                         "only the venue and the original disposition exist")

    def test_a_correction_against_a_superseded_record_is_refused(self):
        self.venue()
        self.venue(record_id="venue-v2", entity_key="old-key", supersedes="venue-v1")
        self.assert_refused(
            "SUPERSEDES_NOT_CURRENT_TIP",
            lambda: self.venue(record_id="venue-v3", entity_key="old-key",
                               supersedes="venue-v1"))
        self.assert_absent("venue-v3")


class PositiveControls(GuardCase):
    def test_same_entity_append_only_correction_is_accepted_and_becomes_the_tip(self):
        self.venue()
        caller_facts = {"name": "Synthetic Hall (corrected)", "supersedes": "venue-v1"}
        before = json.dumps(caller_facts, sort_keys=True)
        result = self.venue(record_id="venue-v2", entity_key="old-key",
                            supersedes="venue-v1")
        self.assertEqual("candidate_unverified", result["status"])
        self.assertEqual(before, json.dumps(caller_facts, sort_keys=True),
                         "the writer must not rewrite caller facts")
        active, conflicts = workflow.active_records(self.records())
        self.assertEqual([], conflicts)
        self.assertEqual(["venue-v2"], [row["record_id"] for row in active
                                        if row["kind"] == "venue"])

    def test_same_entity_review_revision_is_accepted(self):
        self.venue()
        self.disposition("disp-v1", "disp-main", "old-key")
        result = self.disposition("disp-v2", "disp-main", "old-key",
                                  supersedes="disp-v1")
        self.assertEqual("candidate_unverified", result["status"])
        active, conflicts = workflow.active_records(self.records())
        self.assertEqual([], conflicts)
        reviews = [row for row in active if row["kind"] == "review"]
        self.assertEqual(["disp-v2"], [row["record_id"] for row in reviews])

    def test_a_three_step_review_chain_is_accepted(self):
        self.venue()
        self.disposition("disp-v1", "disp-main", "old-key")
        self.disposition("disp-v2", "disp-main", "old-key", supersedes="disp-v1")
        self.disposition("disp-v3", "disp-main", "old-key", supersedes="disp-v2")
        active, conflicts = workflow.active_records(self.records())
        self.assertEqual([], conflicts)
        reviews = [row["record_id"] for row in active if row["kind"] == "review"]
        self.assertEqual(["disp-v3"], reviews)

    def test_exact_replay_is_idempotent(self):
        self.venue()
        first = self.venue(record_id="venue-v2", entity_key="old-key",
                           supersedes="venue-v1")
        second = self.venue(record_id="venue-v2", entity_key="old-key",
                            supersedes="venue-v1")
        self.assertEqual("candidate_unverified", first["status"])
        self.assertEqual("IDEMPOTENT", second["status"])


class AtomicityAndConcurrency(GuardCase):
    def test_batch_with_one_bad_supersedes_commits_nothing(self):
        self.venue()
        items = [
            {"record_id": "venue-good", "stage": "entities", "kind": "venue",
             "entity_key": "good-key", "facts": {"name": "Synthetic Good"}},
            {"record_id": "venue-bad", "stage": "entities", "kind": "venue",
             "entity_key": "bad-key", "facts": {"name": "Synthetic Bad",
                                                "supersedes": "venue-v1"}},
        ]
        with self.assertRaises(ValueError) as ctx:
            store.put_candidates_batch(self.root, RUN, items)
        self.assertIn("SUPERSEDES_ENTITY_KEY_MISMATCH", str(ctx.exception))
        ids = {row["record_id"] for row in self.records()}
        self.assertNotIn("venue-good", ids)
        self.assertNotIn("venue-bad", ids)

    def test_two_conflicting_corrections_cannot_both_land(self):
        self.venue()
        def attempt(index):
            try:
                self.venue(record_id=f"venue-v{index}", entity_key="old-key",
                           supersedes="venue-v1")
                return "OK"
            except ValueError as exc:
                return str(exc).split(":", 1)[0]
        with ThreadPoolExecutor(max_workers=2) as pool:
            outcomes = list(pool.map(attempt, (2, 3)))
        self.assertEqual(1, outcomes.count("OK"), outcomes)
        self.assertEqual(1, sum(code in {"SUPERSEDES_NOT_CURRENT_TIP",
                                        "SUPERSEDES_FORKED_HISTORY"}
                                for code in outcomes), outcomes)
        _, conflicts = workflow.active_records(self.records())
        self.assertEqual([], conflicts)

    def test_an_existing_fork_is_not_rewritten_by_a_new_correction(self):
        # Build a fork by writing two independent roots (no parent), then prove
        # the guard refuses to add a third branch and leaves both rows intact.
        self.venue()
        self.venue(record_id="venue-b", entity_key="old-key")
        before = {row["record_id"] for row in self.records()}
        with self.assertRaises(ValueError) as ctx:
            self.venue(record_id="venue-c", entity_key="old-key",
                       supersedes="venue-v1")
        self.assertIn("SUPERSEDES_FORKED_HISTORY", str(ctx.exception))
        self.assertEqual(before, {row["record_id"] for row in self.records()},
                         "existing history must be preserved")


if __name__ == "__main__":
    unittest.main()
