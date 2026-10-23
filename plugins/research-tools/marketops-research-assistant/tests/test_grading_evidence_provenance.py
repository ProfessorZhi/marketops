"""Wave-1 W-F: candidate evidence provenance, from the stored link to the export.

`artifact_ids` says an artifact was attached to a record. It does not say what part
of it was read, so a candidate could be supported by nothing more than "a file is
attached". These tests pin the stronger claim: the export names the span a candidate
was read from, flags a record that cites an artifact without recording a span, and
publishes the envelope with its export gate visible rather than assumed open.

All names/URLs are synthetic; no network call is made.
"""
from __future__ import annotations

import csv
import hashlib
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import research_delivery as delivery  # noqa: E402
import research_evidence_store as provenance  # noqa: E402
import research_lite_store as store  # noqa: E402

#: The envelope field set frozen by the Wave-1 shared interface contract §3.5,
#: transcribed here independently of the store so a drift in either place shows up
#: as a failing test rather than as two files that agree with each other.
CONTRACT_ENVELOPE_FIELDS = (
    "evidence_id", "run_id", "operation_id", "ledger_id", "provider", "capability_id",
    "source_url", "retrieved_at", "content_sha256", "raw_artifact_ref", "derived_refs",
    "extraction_method", "verified_state", "candidate_only", "egress_decision_ref",
    "caveats",
)

PAGE = (b"<html><p>Example Borough Council operates Example Sports Ground.</p>"
        b"<p>Grounds Management is the responsible department.</p></html>")
SPAN = "Example Borough Council operates Example Sports Ground."


class ProvenanceExportCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / "data"
        env = patch.dict(os.environ, {
            "MARKETOPS_RESEARCH_DATA_ROOT": str(self.root),
            "MARKETOPS_RESEARCH_ALLOW_NETWORK": "0",
            "MARKETOPS_SEARCH_ALLOW_NETWORK": "0",
        })
        env.start()
        self.addCleanup(env.stop)
        store.start_run(self.root, "run-prov", "GB-sports-grounds",
                        "Find operator organisations", "SOP-PENDING")
        store.save_artifact(self.root, "run-prov", "art-x", "fixture-public-html",
                            "https://example.org/x", PAGE, "raw_response")
        self.artifact_sha = store.artifact_sha256(self.root, "run-prov", "art-x")
        store.put_candidate(
            self.root, "run-prov", "org-1", "entities", "organization", "example-borough",
            {"name": "Example Borough Council"}, ["art-x"])

    def link(self, record_id="org-1", artifact_id="art-x", *, span=SPAN, locator="Council"):
        return provenance.link_evidence(
            self.root, "run-prov", record_id, artifact_id,
            content_sha256=self.artifact_sha,
            span_sha256=hashlib.sha256(span.encode("utf-8")).hexdigest(),
            locator_kind="query" if locator else "head", locator=locator,
            material_kind="raw_response")

    def delivery(self, delivery_id="delivery-001"):
        result = delivery.write_delivery(self.root, "run-prov", delivery_id)
        return result, Path(result["directory"])

    def sheet(self, directory: Path, name: str) -> list[dict]:
        with (directory / f"{name}.csv").open(encoding="utf-8-sig", newline="") as handle:
            return list(csv.DictReader(handle))


class EvidenceIndexProvenanceTests(ProvenanceExportCase):
    def test_the_evidence_index_names_the_span_each_artifact_was_read_from(self):
        self.link()
        _, directory = self.delivery()
        row = next(r for r in self.sheet(directory, "证据索引") if r["资料ID"] == "art-x")
        self.assertEqual("1", row["引用记录数"])
        self.assertEqual(hashlib.sha256(SPAN.encode("utf-8")).hexdigest(),
                         row["引用跨度SHA256"])

    def test_an_artifact_with_no_recorded_span_still_exports_and_says_so(self):
        _, directory = self.delivery()
        row = next(r for r in self.sheet(directory, "证据索引") if r["资料ID"] == "art-x")
        self.assertEqual("0", row["引用记录数"])
        self.assertEqual("NONE", row["引用跨度SHA256"])

    def test_two_spans_of_one_artifact_are_both_listed_and_deduplicated(self):
        self.link(locator="Council")
        store.put_candidate(self.root, "run-prov", "org-2", "entities", "organization",
                            "example-ground", {"name": "Example Sports Ground"}, ["art-x"])
        self.link(record_id="org-2", locator="Grounds Management",
                  span="Grounds Management is the responsible department.")
        _, directory = self.delivery()
        row = next(r for r in self.sheet(directory, "证据索引") if r["资料ID"] == "art-x")
        spans = row["引用跨度SHA256"].split("; ")
        self.assertEqual(2, len(spans))
        self.assertEqual(2, len(set(spans)))
        self.assertEqual("2", row["引用记录数"])


class SpanlessCitationTests(ProvenanceExportCase):
    def test_a_record_citing_an_artifact_without_a_span_is_flagged_for_review(self):
        """The gap the old export could not see: attached, but not locatable."""
        _, directory = self.delivery()
        issues = self.sheet(directory, "排除与待确认")
        flagged = [row for row in issues if row["类型"] == "来源跨度未记录"]
        self.assertEqual(1, len(flagged))
        self.assertEqual("org-1", flagged[0]["对象ID"])
        self.assertIn("art-x", flagged[0]["问题或原因"])

    def test_a_record_with_a_recorded_span_is_not_flagged(self):
        self.link()
        _, directory = self.delivery()
        issues = self.sheet(directory, "排除与待确认")
        self.assertEqual([], [row for row in issues if row["类型"] == "来源跨度未记录"])

    def test_the_flag_is_a_review_item_and_never_a_promotion_or_a_failure(self):
        result, directory = self.delivery()
        self.assertEqual("PARTIAL_CANDIDATE_ONLY_NOT_FORMAL_CONTACTS", result["status"])
        self.assertFalse(result["send_authorized"])
        self.assertEqual("NOT_PERFORMED", result["business_verification"])
        for row in self.sheet(directory, "客户总表"):
            self.assertEqual("CANDIDATE_NOT_APPROVED", row["研究状态"])


class ManifestEnvelopeTests(ProvenanceExportCase):
    def record(self, *, ledger_id=None, operation_id="op-0"):
        return provenance.record_envelope(
            self.root, "run-prov", "art-x", operation_id=operation_id, provider="jev",
            capability_id="jev.noul", extraction_method="server:research_evidence.excerpts",
            ledger_id=ledger_id)

    def test_the_manifest_publishes_one_envelope_per_artifact_with_the_frozen_fields(self):
        self.link(); self.record()
        result, directory = self.delivery()
        manifest = json.loads((directory / "MANIFEST.json").read_text(encoding="utf-8"))
        envelopes = manifest["evidence_envelopes"]
        self.assertEqual(1, len(envelopes))
        self.assertEqual(set(CONTRACT_ENVELOPE_FIELDS), set(envelopes[0]))
        self.assertEqual("art-x", envelopes[0]["raw_artifact_ref"])
        self.assertEqual(self.artifact_sha, envelopes[0]["content_sha256"])
        self.assertEqual("https://example.org/x", envelopes[0]["source_url"])
        row = next(r for r in self.sheet(directory, "证据索引") if r["资料ID"] == "art-x")
        self.assertEqual(envelopes[0]["evidence_id"], row["证据信封ID"])

    def test_the_envelope_gate_is_blocked_while_the_ledger_is_unwired(self):
        """§3.5: an envelope with no ledger row is not exportable. Say so, do not fill it."""
        self.link(); self.record()
        result, directory = self.delivery()
        manifest = json.loads((directory / "MANIFEST.json").read_text(encoding="utf-8"))
        self.assertIsNone(manifest["evidence_envelopes"][0]["ledger_id"])
        self.assertEqual("BLOCKED_LEDGER_NOT_WIRED", manifest["evidence_envelope_export_gate"])
        self.assertTrue(manifest["evidence_envelopes"][0]["candidate_only"])
        self.assertEqual(result["evidence_envelope_export_gate"],
                         manifest["evidence_envelope_export_gate"])

    def test_the_manifest_keeps_a_ledger_linked_envelope_exportable(self):
        """With a ledger row named, the same evidence stops being gated."""
        self.link(); self.record(ledger_id="led-1")
        result, directory = self.delivery()
        manifest = json.loads((directory / "MANIFEST.json").read_text(encoding="utf-8"))
        self.assertEqual("OPEN", manifest["evidence_envelope_export_gate"])
        self.assertEqual("led-1", manifest["evidence_envelopes"][0]["ledger_id"])

    def test_an_artifact_read_by_a_record_but_never_enveloped_is_reported_as_missing(self):
        """A span with no envelope is a visible gap, not a silently complete export."""
        self.link()
        result, directory = self.delivery()
        manifest = json.loads((directory / "MANIFEST.json").read_text(encoding="utf-8"))
        self.assertEqual([], manifest["evidence_envelopes"])
        self.assertEqual("NO_ENVELOPES", manifest["evidence_envelope_export_gate"])
        self.assertEqual(["art-x"], manifest["evidence_envelope_missing_artifacts"])
        self.assertEqual(1, manifest["evidence_link_count"])
        # The record does have a recorded span, so it is not a spanless citation;
        # the missing piece is the envelope, which is reported above.
        self.assertEqual(0, result["evidence_spanless_record_count"])


class ExportShapeTests(ProvenanceExportCase):
    def test_the_workbook_keeps_its_nine_sheets_and_its_candidate_boundary(self):
        self.link()
        result, directory = self.delivery()
        import zipfile
        with zipfile.ZipFile(directory / "Research_Result.xlsx") as archive:
            self.assertIsNone(archive.testzip())
            sheets = [n for n in archive.namelist()
                      if n.startswith("xl/worksheets/sheet") and n.endswith(".xml")]
        self.assertEqual(9, len(sheets))
        self.assertEqual("PARTIAL_CANDIDATE_ONLY_NOT_FORMAL_CONTACTS", result["status"])
        self.assertEqual("ALL_OK", result["evidence_hash_readback"])
        notes = {row["字段"]: row["值"] for row in self.sheet(directory, "本轮说明")}
        self.assertEqual("NOT_PERFORMED", notes["业务正式审查"])

    def test_a_link_that_cannot_be_read_back_blocks_the_export(self):
        """Provenance is derived from verified bytes; tampering must stop the workbook."""
        self.link()
        path = next((self.root / "artifacts" / "run-prov").glob("*.bin"))
        path.write_bytes(b"<html><p>tampered after the link was recorded</p></html>")
        with self.assertRaisesRegex(ValueError, "ARTIFACT_READBACK_HASH_MISMATCH"):
            delivery.write_delivery(self.root, "run-prov", "delivery-tampered")


if __name__ == "__main__":
    unittest.main()
