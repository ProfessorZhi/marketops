"""Wave-2 W-F: the independent re-check of a published scorecard.

A scorecard nobody can re-derive is a claim, not a measurement. This file holds the
re-check to three things:

* recomputing from the same inputs must reproduce the document byte-for-byte, so a
  published scorecard can be tied to the artefacts it was computed from;
* a doctored scorecard must be caught, and the *differing key* named rather than the
  whole document declared "wrong";
* the second pass must re-derive acceptance from the stored bytes rather than believe
  the scorecard's own `accepted` flag — that is the pass that catches a row which was
  accepted on a quotation that is not in the page.
"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "tests"))

import _wave2_quality_fixture as fixture  # noqa: E402
import research_quality_recheck as recheck  # noqa: E402
import research_quality_scorecard as scorer  # noqa: E402


class RecheckCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self._runs = 0

    def produce(self, **kwargs):
        self._runs += 1
        paths = fixture.write_run(Path(self.tmp.name) / f"run-{self._runs}", **kwargs)
        inputs = scorer.inputs_from_paths(paths)
        return paths, inputs, scorer.score(**inputs)


class AgreementTests(RecheckCase):
    def test_a_freshly_produced_scorecard_re_checks_clean(self):
        _paths, inputs, card = self.produce()
        verdict = recheck.recheck(scorecard=card, inputs=inputs)
        self.assertEqual("AGREE", verdict["status"])
        self.assertEqual([], verdict["differences"])
        self.assertEqual("PASS", verdict["pass2"]["status"])

    def test_the_second_pass_re_derives_every_accepted_row_from_the_bytes(self):
        _paths, inputs, card = self.produce()
        verdict = recheck.recheck(scorecard=card, inputs=inputs)
        self.assertEqual(sorted(r["row_ref"] for r in card["rows"] if r["accepted"]),
                         verdict["pass2"]["accepted_rederived"])
        self.assertEqual([], verdict["pass2"]["disagreements"])


class DriftTests(RecheckCase):
    def test_a_doctored_verdict_is_caught_and_the_key_is_named(self):
        _paths, inputs, card = self.produce()
        card["verdict"] = "PASS"
        card["zero_tolerance"]["failed"] = []
        verdict = recheck.recheck(scorecard=card, inputs=inputs)
        self.assertEqual("DRIFT", verdict["status"])
        self.assertIn("zero_tolerance.failed", verdict["differences"])
        self.assertIn("verdict", verdict["differences"])

    def test_a_doctored_accepted_flag_is_caught_by_the_second_pass(self):
        """The pass that does not believe the scorecard is the one that matters."""
        _paths, inputs, card = self.produce()
        target = next(r for r in card["rows"] if r["row_ref"] == "row-13")
        self.assertFalse(target["accepted"])
        target["accepted"] = True
        verdict = recheck.recheck(scorecard=card, inputs=inputs)
        self.assertEqual("DRIFT", verdict["status"])
        self.assertEqual(["row-13"], verdict["pass2"]["disagreements"])

    def test_a_scorecard_from_other_inputs_does_not_re_derive(self):
        _p1, inputs, _card = self.produce()
        _p2, other_inputs, other_card = self.produce(tamper="bytes:art-own-01")
        verdict = recheck.recheck(scorecard=other_card, inputs=inputs)
        self.assertEqual("DRIFT", verdict["status"])

    def test_a_claim_whose_quote_is_not_in_the_page_is_a_disagreement(self):
        paths, _inputs, _card = self.produce()
        rows = fixture.build_rows()
        rows[0]["claims"][0]["quote"] = "A sentence that is on no page at all."
        _rewrite(paths["rows"], rows)
        rederived = recheck.rederive_acceptance(scorer.inputs_from_paths(paths))
        self.assertNotIn("row-01", rederived["accepted"])
        self.assertIn("EXCERPT_NOT_IN_STORED_MATERIAL", rederived["reason_codes"]["row-01"])

    def test_a_row_missing_a_required_field_cannot_be_rederived_as_accepted(self):
        paths, _inputs, _card = self.produce()
        rederived = recheck.rederive_acceptance(scorer.inputs_from_paths(paths))
        self.assertNotIn("row-13", rederived["accepted"])
        self.assertIn("MISSING_FIELD:venue_address_public",
                      rederived["reason_codes"]["row-13"])

    def test_a_row_accepted_on_a_bogus_grade_is_a_second_pass_disagreement(self):
        paths, inputs, card = self.produce()
        rows = fixture.build_rows()
        rows[0]["claims"][0]["quote"] = "A sentence that is on no page at all."
        _rewrite(paths["rows"], rows)
        doctored = scorer.score(**scorer.inputs_from_paths(paths))
        next(r for r in doctored["rows"] if r["row_ref"] == "row-01")["accepted"] = True
        verdict = recheck.recheck(scorecard=doctored,
                                  inputs=scorer.inputs_from_paths(paths))
        self.assertEqual("DRIFT", verdict["status"])
        self.assertIn("row-01", verdict["pass2"]["disagreements"])


def _rewrite(path: Path, items: list[dict]) -> None:
    path.write_text("".join(json.dumps(i, ensure_ascii=False, sort_keys=True) + "\n"
                            for i in items), encoding="utf-8")


class RefusalTests(RecheckCase):
    def test_a_missing_artefact_directory_is_blocked_not_agreed(self):
        paths, inputs, card = self.produce()
        paths["artifacts_dir"] = Path(self.tmp.name) / "gone"
        verdict = recheck.recheck(scorecard=card,
                                  inputs=scorer.inputs_from_paths(paths))
        self.assertEqual("BLOCKED", verdict["status"])
        self.assertTrue(verdict["excuse"])


if __name__ == "__main__":
    unittest.main()
