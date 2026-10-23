"""Offline positive/negative checks; all observations are synthetic, not Member-A/customer data."""
import contextlib
import copy
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts import experience_pack as ep


def sample():
    return {
        "schema_version": ep.VERSION, "run_id": "synthetic-run-001",
        "scope": "synthetic public staff-page extraction; not an active business policy",
        "store_ref": "synthetic-pack-001", "rule_version": "synthetic-policy-v1", "skill_version": "0.2.1",
        "privacy": "sanitized_candidate", "promotion": "candidate_only",
        "evidence_manifest": [
            {"ref": "receipt-1", "kind": "synthetic", "locator": "fixtures/receipt-1.json", "sha256": None},
            {"ref": "train-1", "kind": "synthetic", "locator": "fixtures/train-1.html", "sha256": None},
            {"ref": "holdout-1", "kind": "synthetic", "locator": "fixtures/holdout-1.html", "sha256": None},
            {"ref": "result-1", "kind": "test_result", "locator": "fixtures/test-result-1.json", "sha256": None},
        ],
        "incidents": [{
            "id": "I-001", "stage": "acquisition", "category": "extraction", "operation_ref": "op-001",
            "expected": "Extract visible work-email links from the supplied synthetic HTML",
            "observation": "Synthetic receipt reports the material is present but extracted contact rows are empty",
            "impact": "Contacts remain candidates; no verified route promoted",
            "hypothesis": "Text cleanup may discard mailto attributes", "root_cause_status": "hypothesis",
            "evidence_refs": ["receipt-1"], "validation_refs": [],
            "next_action": "Inspect the existing HTML before making another provider request",
        }],
        "lessons": [{
            "id": "L-001", "status": "draft", "when": "Material is available but expected fields disappeared",
            "do": "Compare material and extraction output before spending on another browser fetch",
            "avoid": "Assume every empty extraction is a site-access failure",
            "scope": "Same parser and declared HTML layout; not all websites",
            "limitations": "Synthetic example only; no measured real-customer impact",
            "proposed_change": "Evaluate a link-preserving parser path, not a policy relaxation",
            "rollback": "Keep current parser and original data until representative tests pass",
            "incident_ids": ["I-001"], "evidence_refs": ["receipt-1"], "counterexample_refs": [],
            "replay": {"baseline": "Existing extraction behavior", "change": "Preserve link attributes",
                       "acceptance": "Recover literal fields without crossing staff-record boundaries",
                       "status": "not_run", "training_cases": ["train-1"], "holdout_cases": ["holdout-1"],
                       "result_refs": []},
        }],
        "metrics": [{"name": "provider_spend", "value": None, "denominator": None,
                     "unit": "currency_unknown", "population": "this synthetic run", "evidence_refs": []}],
    }


class ExperienceTests(unittest.TestCase):
    def assert_invalid(self, data):
        self.assertEqual("INVALID", ep.validate(data)["status"])

    def test_positive_is_candidate_only(self):
        result = ep.validate(sample())
        self.assertEqual("VALID_CANDIDATE_NOT_VERIFIED", result["status"], result)
        self.assertFalse(result["business_verified"])
        self.assertFalse(result["cause_verified"])
        self.assertFalse(result["privacy_verified"])

    def test_empty_is_not_evaluated(self):
        data = sample(); data["incidents"] = []; data["lessons"] = []
        self.assertEqual("EMPTY_NOT_EVALUATED", ep.validate(data)["status"])

    def test_cannot_self_approve(self):
        data = sample(); data["promotion"] = "approved"; self.assert_invalid(data)
        data = sample(); data["lessons"][0]["status"] = "approved"; self.assert_invalid(data)

    def test_no_confirmed_root_cause_without_validation(self):
        data = sample(); data["incidents"][0]["root_cause_status"] = "confirmed"
        self.assert_invalid(data)

    def test_tested_requires_test_results(self):
        data = sample(); data["lessons"][0]["status"] = "tested"
        self.assert_invalid(data)

    def test_passed_replay_requires_holdout_and_receipt(self):
        data = sample(); data["lessons"][0]["replay"]["status"] = "passed"
        self.assert_invalid(data)

    def test_leaked_holdout_is_rejected(self):
        data = sample(); data["lessons"][0]["replay"]["holdout_cases"] = ["train-1"]
        self.assert_invalid(data)

    def test_unknown_and_duplicate_references(self):
        data = sample(); data["incidents"][0]["evidence_refs"] = ["not-present"]
        self.assert_invalid(data)
        data = sample(); data["incidents"].append(copy.deepcopy(data["incidents"][0]))
        self.assert_invalid(data)

    def test_metrics_require_units_and_evidence(self):
        data = sample(); data["metrics"][0]["value"] = 0; self.assert_invalid(data)
        data = sample(); data["metrics"][0]["denominator"] = 0; self.assert_invalid(data)
        data = sample(); data["metrics"][0]["value"] = float("nan"); self.assert_invalid(data)

    def test_json_shapes_fail_without_crashing(self):
        for value in (None, [], "text", {"incidents": [False]}, {"lessons": [None]}):
            self.assert_invalid(value)

    def test_render_does_not_overwrite_or_run_links(self):
        with tempfile.TemporaryDirectory() as tmp:
            data = sample(); data["incidents"][0]["observation"] += " <script>bad()</script> [link](javascript:x)"
            source = Path(tmp) / "experience.json"; out = Path(tmp) / "review.md"
            source.write_text(json.dumps(data), encoding="utf-8")
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(0, ep.main(["render", str(source), "--output", str(out)]))
                self.assertEqual(2, ep.main(["render", str(source), "--output", str(out)]))
            self.assertNotIn("<script>", out.read_text())
            self.assertNotIn("[link]", out.read_text())

    def test_duplicate_json_keys_and_missing_file_fail(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "bad.json"
            source.write_text('{"run_id":"first","run_id":"other"}')
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(2, ep.main(["validate", str(source)]))
                self.assertEqual(2, ep.main(["validate", str(source.with_name("absent.json"))]))


if __name__ == "__main__":
    unittest.main()
