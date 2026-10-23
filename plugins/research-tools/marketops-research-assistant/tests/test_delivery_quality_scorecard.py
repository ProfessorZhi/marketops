"""Wave-2 W-F: the quality scorecard — rubric, zero-tolerance, and honest unknowns.

The owner's acceptance document (§3) asks for a two-pass quality evaluation over a
blind, independently checkable subset, with a specific list of measurements, and it
makes "unknown actual cost" a label rather than a zero. W-QA's fixtures already fix
the rubric (grades A..F), the twelve zero-tolerance checks and the cost-ledger shape;
this is the scorer that applies them.

Two rules the tests below exist to hold:

* **Mechanically derivable facts are derived, not believed.** The row's declared grade
  is cross-checked against the domain class of the page it actually cites, and the
  challenge guard reads the stored bytes rather than a flag that could be cleared.
* **A measurement that cannot be evaluated is BLOCKED or UNKNOWN, never 0.** The
  fixture's ledger carries a dispatched charge whose actual is unreconciled, so
  cost-per-accepted-row has to say so instead of reporting the confirmed part alone.
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
import research_quality_scorecard as scorer  # noqa: E402


class ScorecardCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self._runs = 0

    def run_fixture(self, **kwargs):
        # A fresh directory per call: two runs in one directory would let the first
        # run's files satisfy the second's optional inputs, which is how an "absent
        # input" test silently measures the present one.
        self._runs += 1
        paths = fixture.write_run(Path(self.tmp.name) / f"run-{self._runs}", **kwargs)
        return scorer.score(**scorer.inputs_from_paths(paths))

    def measurement(self, card, name):
        self.assertIn(name, card["measurements"])
        return card["measurements"][name]


class ZeroToleranceTests(ScorecardCase):
    def test_a_p0_failure_forces_a_fail_verdict_whatever_else_measured(self):
        card = self.run_fixture()
        self.assertEqual("FAIL", card["verdict"])
        self.assertTrue(set(card["zero_tolerance"]["failed"]) & {"AF-1", "AF-2", "AF-3", "AF-12"})

    def test_an_unevaluable_check_is_blocked_and_blocks_a_pass(self):
        """The check list names artefacts this offline run does not have."""
        card = self.run_fixture()
        self.assertEqual(sorted(["AF-11", "AF-4", "AF-7", "AF-8"]),
                         card["zero_tolerance"]["blocked"])
        for entry in card["zero_tolerance"]["checks"]:
            if entry["state"] == "BLOCKED":
                self.assertTrue(entry["excuse"], entry["id"])

    def test_the_checks_that_the_fixture_satisfies_still_pass(self):
        card = self.run_fixture()
        states = {c["id"]: c["state"] for c in card["zero_tolerance"]["checks"]}
        # AF-7 is deliberately absent: QA's ledger schema has no op_signature, so the
        # duplicate-charge check cannot be evaluated and reports BLOCKED, not PASS.
        for check in ("AF-5", "AF-6", "AF-9", "AF-10"):
            with self.subTest(check=check):
                self.assertEqual("PASS", states[check])

    def test_an_invented_contact_fails_af1_and_names_only_the_row(self):
        card = self.run_fixture()
        issue = next(i for i in card["issues"] if i["check"] == "AF-1")
        self.assertEqual("P0", issue["severity"])
        self.assertEqual("row-12", issue["subject_ref"])
        self.assertNotIn("not.on.the.page@example.org", json.dumps(card))

    def test_a_snippet_with_no_retrieved_page_fails_af2(self):
        card = self.run_fixture()
        issue = next(i for i in card["issues"] if i["check"] == "AF-2")
        self.assertEqual("row-10", issue["subject_ref"])
        self.assertIn("NO_RETRIEVED_PAGE", issue["reason_codes"])

    def test_a_challenge_page_is_caught_from_its_bytes_not_from_a_flag(self):
        """Clearing `challenge_suspected` must not hide it: the markers are in the bytes."""
        card = self.run_fixture(tamper="challenge_flag")
        issue = next(i for i in card["issues"] if i["check"] == "AF-3")
        self.assertEqual("art-challenge-01", issue["subject_ref"])
        self.assertIn("CHALLENGE_MARKERS_IN_STORED_BYTES", issue["reason_codes"])

    def test_a_charge_left_neither_settled_nor_held_fails_af6(self):
        """A check that can never fail would pass for the wrong reason."""
        card = self.run_fixture(tamper="untracked_charge")
        states = {c["id"]: c for c in card["zero_tolerance"]["checks"]}
        self.assertEqual("FAIL", states["AF-6"]["state"])
        self.assertIn("AF-6", card["zero_tolerance"]["failed"])

    def test_a_receipt_with_no_ledger_row_fails_af5(self):
        card = self.run_fixture(tamper="receipt_without_ledger")
        states = {c["id"]: c for c in card["zero_tolerance"]["checks"]}
        self.assertEqual("FAIL", states["AF-5"]["state"])
        self.assertIn("RECEIPT_WITHOUT_LEDGER_ROW", states["AF-5"]["reason_codes"])

    def test_tampered_artifact_bytes_fail_the_lineage_check(self):
        card = self.run_fixture(tamper="bytes:art-own-01")
        issue = next(i for i in card["issues"] if i["check"] == "AF-9")
        self.assertEqual("art-own-01", issue["subject_ref"])
        self.assertIn("CONTENT_SHA256_MISMATCH", issue["reason_codes"])

    def test_a_clean_fixture_is_reachable_when_the_p0_issues_are_absent(self):
        """The verdict must be able to improve, or FAIL would carry no information."""
        paths = fixture.write_run(Path(self.tmp.name) / "run")
        rows = [r for r in fixture.build_rows() if r["row_id"] not in
                {"row-10", "row-11", "row-12"}]
        _rewrite_jsonl(paths["rows"], rows)
        card = scorer.score(**scorer.inputs_from_paths(paths))
        self.assertEqual([], card["zero_tolerance"]["failed"])
        # Still not a PASS: three checks are unevaluable offline and that is the point.
        self.assertEqual("BLOCKED", card["verdict"])


class GradeDerivationTests(ScorecardCase):
    def test_domain_class_is_derived_from_the_page_the_row_cites(self):
        card = self.run_fixture()
        by_id = {r["row_ref"]: r for r in card["rows"]}
        self.assertEqual("SELF", by_id["row-01"]["domain_class"])
        self.assertEqual("PARENT_BODY", by_id["row-05"]["domain_class"])
        self.assertEqual("DIRECTORY", by_id["row-08"]["domain_class"])
        self.assertEqual("CHALLENGE", by_id["row-11"]["domain_class"])
        self.assertEqual("NO_RETRIEVED_PAGE", by_id["row-10"]["domain_class"])

    def test_claiming_evidence_grade_for_a_directory_page_is_an_overclaim(self):
        card = self.run_fixture()
        by_id = {r["row_ref"]: r for r in card["rows"]}
        self.assertEqual("OVERCLAIM", by_id["row-09"]["grade_agreement"])
        self.assertEqual("OVERCLAIM", by_id["row-11"]["grade_agreement"])
        self.assertEqual("AGREE", by_id["row-08"]["grade_agreement"])
        self.assertFalse(by_id["row-09"]["accepted"])

    def test_a_row_is_accepted_only_when_the_excerpt_really_contains_the_value(self):
        card = self.run_fixture()
        by_id = {r["row_ref"]: r for r in card["rows"]}
        self.assertTrue(by_id["row-01"]["accepted"])
        self.assertFalse(by_id["row-13"]["accepted"])
        self.assertIn("MISSING_REQUIRED_FIELD:venue_address_public",
                      by_id["row-13"]["reason_codes"])

    def test_an_absent_claim_artifact_cannot_be_accepted(self):
        card = self.run_fixture()
        by_id = {r["row_ref"]: r for r in card["rows"]}
        self.assertIn("CLAIM_ARTIFACT_NOT_IN_LINEAGE", by_id["row-10"]["reason_codes"])


class MeasurementTests(ScorecardCase):
    def test_the_rubric_s_ten_measurements_are_all_present(self):
        card = self.run_fixture()
        self.assertEqual(
            sorted(["accepted_valid_rows", "credits_per_accepted_row",
                    "duplicates_per_accepted_entity", "failure_causes",
                    "false_positives", "manual_minutes_per_accepted_row",
                    "missed_required_fields", "official_domain_accuracy",
                    "traceable_fact_ratio", "usd_per_accepted_row"]),
            sorted(card["measurements"]))

    def test_the_measured_values_match_the_fixture(self):
        card = self.run_fixture()
        expected = {
            "official_domain_accuracy": "0.625000",
            "traceable_fact_ratio": "0.923077",
            "accepted_valid_rows": "0.461538",
            "duplicates_per_accepted_entity": "0.142857",
            "manual_minutes_per_accepted_row": "2.833333",
            "credits_per_accepted_row": "0.166667",
        }
        for name, value in expected.items():
            with self.subTest(measurement=name):
                self.assertEqual(value, self.measurement(card, name)["value"])
                self.assertEqual("MEASURED", self.measurement(card, name)["state"])

    def test_missed_required_fields_counts_each_absent_field(self):
        card = self.run_fixture()
        measured = self.measurement(card, "missed_required_fields")
        self.assertEqual(1, measured["total"])
        self.assertEqual({"venue_address_public": 1}, measured["by_field"])

    def test_false_positives_needs_the_reviewer_and_says_so_when_absent(self):
        card = self.run_fixture()
        self.assertEqual(1, self.measurement(card, "false_positives")["count"])
        without = self.run_fixture(with_reviews=False)
        blocked = self.measurement(without, "false_positives")
        self.assertEqual("BLOCKED", blocked["state"])
        self.assertIsNone(blocked["value"])
        self.assertTrue(blocked["excuse"])

    def test_failure_causes_are_counted_by_closed_code_not_by_prose(self):
        card = self.run_fixture()
        causes = self.measurement(card, "failure_causes")
        self.assertEqual({"BLOCKED": 1, "NO_CHARGE_PROVEN": 1, "SETTLED": 1,
                          "UNKNOWN_CHARGE_OUTSTANDING": 1}, causes["by_state"])
        self.assertEqual({"POLICY_TIER_NOT_GRANTED": 1}, causes["by_reason_code"])


class CostHonestyTests(ScorecardCase):
    def test_an_unreconciled_charge_makes_cost_per_row_partial_not_final(self):
        card = self.run_fixture()
        measured = self.measurement(card, "usd_per_accepted_row")
        self.assertEqual("PARTIAL_UNKNOWN", measured["state"])
        self.assertEqual("0.000167", measured["value"])
        self.assertEqual("0.000500", measured["unknown_exposure_usd"])
        # The all-in figure counts the unreconciled charge too, and is reported
        # separately precisely so it is never mistaken for the confirmed one.
        self.assertEqual("0.000250", measured["all_in_usd"])

    def test_no_confirmed_charge_at_all_is_unknown_not_zero(self):
        card = self.run_fixture(with_ledger=False)
        measured = self.measurement(card, "usd_per_accepted_row")
        self.assertEqual("BLOCKED", measured["state"])
        self.assertIsNone(measured["value"])
        self.assertNotEqual("0", str(measured["value"]))

    def test_a_fully_reconciled_ledger_measures_cleanly(self):
        card = self.run_fixture(with_unknown_charge=False)
        measured = self.measurement(card, "usd_per_accepted_row")
        self.assertEqual("MEASURED", measured["state"])
        self.assertEqual("0.000250", measured["value"])

    def test_cost_per_row_is_null_when_nothing_was_accepted(self):
        paths = fixture.write_run(Path(self.tmp.name) / "none")
        _rewrite_jsonl(paths["rows"], fixture.build_rows()[:1])
        _rewrite_jsonl(paths["reviews"],
                       [{"row_id": "row-01", "decision": "REJECT",
                         "reviewer_minutes": "1"}])
        card = scorer.score(**scorer.inputs_from_paths(paths))
        measured = self.measurement(card, "usd_per_accepted_row")
        self.assertIsNone(measured["value"])
        self.assertNotEqual("0", str(measured["value"]))

    def test_usd_and_credits_are_kept_in_separate_meters(self):
        card = self.run_fixture()
        self.assertIn("usd", self.measurement(card, "usd_per_accepted_row")["meter"])
        self.assertEqual("credits", self.measurement(card, "credits_per_accepted_row")["meter"])


class SanitisationTests(ScorecardCase):
    def test_the_scorecard_carries_no_contact_value_from_the_rows(self):
        card = self.run_fixture()
        blob = json.dumps(card, ensure_ascii=False, sort_keys=True)
        for leaked in ("enquiries@example.org", "not.on.the.page@example.org",
                       "A. Person", "B. Invented", "1 Example Street"):
            with self.subTest(leaked=leaked):
                self.assertNotIn(leaked, blob)

    def test_the_scorecard_carries_no_query_string_or_credential_shaped_value(self):
        card = self.run_fixture()
        blob = json.dumps(card, ensure_ascii=False, sort_keys=True)
        self.assertNotIn("?q=", blob)
        self.assertNotIn("api_key", blob)
        self.assertEqual("PASS", card["sanitization"]["scan"])

    def test_a_credential_in_an_input_is_reported_as_a_failure_not_echoed(self):
        """AF-10 has to be able to fail, and must fail without printing the secret."""
        paths = fixture.write_run(Path(self.tmp.name) / "leak")
        rows = fixture.build_rows()
        rows[0]["leak_probe"] = "sk-" + "a" * 32
        _rewrite_jsonl(paths["rows"], rows)
        card = scorer.score(**scorer.inputs_from_paths(paths))
        blob = json.dumps(card, ensure_ascii=False, sort_keys=True)
        self.assertNotIn("sk-" + "a" * 32, blob)
        self.assertIn("AF-10", card["zero_tolerance"]["failed"])


class IssueIdTests(ScorecardCase):
    def test_issue_ids_are_unique_so_a_defect_can_be_quoted(self):
        card = self.run_fixture()
        ids = [i["issue_id"] for i in card["issues"]]
        self.assertTrue(ids)
        self.assertEqual(len(ids), len(set(ids)))

    def test_every_issue_carries_a_code_and_a_subject(self):
        card = self.run_fixture()
        for issue in card["issues"]:
            with self.subTest(issue=issue["issue_id"]):
                self.assertEqual("P0", issue["severity"])
                self.assertTrue(issue["subject_ref"])
                self.assertTrue(issue["reason_codes"])


class PublishedSchemaTests(ScorecardCase):
    """The document must satisfy the schema that ships with it."""

    SCHEMA_PATH = ROOT.parents[2] / "docs" / "quality-scorecard" / "scorecard.schema.json"

    def test_the_schema_file_is_present_and_names_this_schema(self):
        self.assertTrue(self.SCHEMA_PATH.is_file())
        self.assertEqual("marketops.quality.scorecard.v1",
                         json.loads(self.SCHEMA_PATH.read_text(encoding="utf-8"))["$id"])

    def test_the_produced_document_satisfies_the_published_schema(self):
        card = self.run_fixture()
        schema = json.loads(self.SCHEMA_PATH.read_text(encoding="utf-8"))
        problems = _conformance(card, schema, schema)
        self.assertEqual([], problems)

    def test_the_validator_would_notice_a_missing_required_key(self):
        """A conformance check that cannot fail is decoration."""
        card = self.run_fixture()
        del card["measurements"]["traceable_fact_ratio"]
        schema = json.loads(self.SCHEMA_PATH.read_text(encoding="utf-8"))
        self.assertTrue(_conformance(card, schema, schema))


def _conformance(document, node, root, path="$"):
    """Structural checks only: required keys present, declared enums respected.

    Not a full JSON Schema implementation. It is the subset that matters here — the
    shapes a careless edit would break — and it needs no dependency.
    """
    problems = []
    if not isinstance(node, dict):
        return problems
    if "$ref" in node:
        ref = node["$ref"].split("/")[-1]
        return _conformance(document, root["$defs"][ref], root, path)
    kind = node.get("type")
    if kind == "object" or "properties" in node:
        if not isinstance(document, dict):
            return [f"{path}: expected object"]
        for key in node.get("required", []):
            if key not in document:
                problems.append(f"{path}.{key}: required key missing")
        for key, child in (node.get("properties") or {}).items():
            if key in document:
                problems += _conformance(document[key], child, root, f"{path}.{key}")
    elif kind == "array":
        if not isinstance(document, list):
            return [f"{path}: expected array"]
        for index, item in enumerate(document):
            problems += _conformance(item, node.get("items", {}), root, f"{path}[{index}]")
    if "enum" in node and document not in node["enum"]:
        problems.append(f"{path}: {document!r} outside {node['enum']}")
    return problems


class DeterminismTests(ScorecardCase):
    def test_the_same_inputs_produce_byte_identical_output(self):
        first = self.run_fixture()
        second = self.run_fixture()
        self.assertEqual(json.dumps(first, sort_keys=True),
                         json.dumps(second, sort_keys=True))

    def test_the_written_json_and_csv_round_trip(self):
        paths = fixture.write_run(Path(self.tmp.name) / "out")
        card = scorer.score(**scorer.inputs_from_paths(paths))
        written = scorer.write_scorecard(card, Path(self.tmp.name) / "out" / "card")
        self.assertTrue(Path(written["json"]).is_file())
        self.assertTrue(Path(written["csv"]).is_file())
        self.assertEqual(card, json.loads(Path(written["json"]).read_text(encoding="utf-8")))
        header = Path(written["csv"]).read_text(encoding="utf-8").splitlines()[0]
        self.assertIn("measurement", header)
        self.assertIn("state", header)

    def test_input_hashes_are_recorded_so_a_reader_can_pin_the_run(self):
        card = self.run_fixture()
        for name in ("brief", "rubric", "checks", "rows", "lineage"):
            with self.subTest(input=name):
                self.assertRegex(card["inputs"]["sha256"][name], r"^[0-9a-f]{64}$")


def _rewrite_jsonl(path: Path, items: list[dict]) -> None:
    path.write_text("".join(json.dumps(i, ensure_ascii=False, sort_keys=True) + "\n"
                            for i in items), encoding="utf-8")


if __name__ == "__main__":
    unittest.main()
