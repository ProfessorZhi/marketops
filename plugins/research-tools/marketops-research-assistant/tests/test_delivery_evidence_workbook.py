"""Wave-7 W-F: evidence -> candidate -> review -> Excel + ledger receipts, assembled.

Everything asserted here is read back out of the **saved workbook cells**, not out of
the objects that built it: a workbook whose in-memory rows are right and whose cells
are wrong is the failure this file exists to catch.

Three properties carry the weight:

* a claim is final only through a recorded decision, and an unsupported claim can
  never become final even when a reviewer accepts it;
* the `evidence` sheet copies `evidence_id`/`ledger_id` from the rows the store
  recorded — the export never rebuilds an id, so the workbook cannot describe a link
  nobody wrote;
* the ledger's own states are copied verbatim: an `UNKNOWN` charge stays `UNKNOWN`
  with its actual empty, and nothing is summed into a settled figure.

Skips where B's `research_usage_ledger` is not importable (the worker branch); runs
where the integration tree is present. Offline only: no network, no credential, no
provider, no model call, no customer data.
"""
from __future__ import annotations

import hashlib
import json
import re
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "tests"))

import _wave7_evidence_fixture as fixture  # noqa: E402
import research_delivery as delivery  # noqa: E402
import research_evidence_store as provenance  # noqa: E402

NS = {"x": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}


def read_workbook(path: Path) -> dict[str, list[list[str]]]:
    """Every cell of every sheet, keyed by sheet name, straight from the file.

    The serializer writes `inlineStr` cells, so the text is in `<is><t>`; a numeric
    cell carries `<v>`. Reading both is what lets a test assert on what a person
    opening the workbook would actually see.
    """
    names = [name for name, _ in delivery.EVIDENCE_TABLES]
    with zipfile.ZipFile(path) as archive:
        sheets = sorted(
            (n for n in archive.namelist()
             if re.fullmatch(r"xl/worksheets/sheet\d+\.xml", n)),
            key=lambda n: int(re.findall(r"\d+", n)[0]))
        out = {}
        for index, sheet_path in enumerate(sheets):
            root = ET.fromstring(archive.read(sheet_path))
            rows = []
            for row in root.findall(".//x:sheetData/x:row", NS):
                values = []
                for cell in row.findall("x:c", NS):
                    text = cell.find(".//x:t", NS)
                    number = cell.find("x:v", NS)
                    # An empty cell is written as `<c .../>` with neither `<t>` nor
                    # `<v>`; that is an empty string to a reader, not the Python None
                    # the XML lookup happens to return.
                    value = text.text if text is not None else (
                        number.text if number is not None else "")
                    values.append(value if value is not None else "")
                rows.append(values)
            out[names[index]] = rows
    return out


def as_records(rows: list[list[str]]) -> list[dict[str, str]]:
    header, *body = rows
    return [dict(zip(header, row)) for row in body]


class WorkbookCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self._n = 0

    def assemble(self, shape="default"):
        self._n += 1
        built = fixture.build(Path(self.tmp.name) / f"{shape}-{self._n}", shape=shape)
        result = delivery.write_evidence_workbook(
            built["root"], fixture.RUN, f"delivery-{shape}-{self._n}",
            ledger=built["ledger"], project=built["project"], reviews=built["reviews"])
        sheets = read_workbook(Path(result["xlsx_path"]))
        return built, result, sheets


class WorkbookShapeTests(WorkbookCase):
    def test_the_workbook_has_exactly_the_five_named_sheets_in_order(self):
        _built, _result, sheets = self.assemble()
        self.assertEqual([name for name, _ in delivery.EVIDENCE_TABLES], list(sheets))

    def test_every_sheet_header_is_the_declared_columns(self):
        _built, _result, sheets = self.assemble()
        for name, fields in delivery.EVIDENCE_TABLES:
            with self.subTest(sheet=name):
                self.assertEqual(list(fields), sheets[name][0])

    def test_the_manifest_matches_the_file_it_describes(self):
        _built, result, _sheets = self.assemble()
        manifest = json.loads((Path(result["directory"]) / "MANIFEST.json")
                              .read_text(encoding="utf-8"))
        self.assertEqual(manifest["files"]["Evidence_Chain.xlsx"]["sha256"],
                         hashlib.sha256(Path(result["xlsx_path"]).read_bytes()).hexdigest())
        self.assertEqual("EVIDENCE_CHAIN_CANDIDATE_ONLY", manifest["status"])


class SourceTests(WorkbookCase):
    def test_three_sources_carry_url_title_time_and_hash(self):
        built, _result, sheets = self.assemble()
        rows = as_records(sheets["sources"])
        self.assertEqual(3, len(rows))
        for row, institution in zip(rows, fixture.INSTITUTIONS):
            with self.subTest(source=institution.key):
                self.assertEqual(f"https://{institution.key}.example.org{institution.path}",
                                 row["url"])
                self.assertEqual(institution.title, row["title"])
                self.assertRegex(row["content_sha256"], r"^[0-9a-f]{64}$")
                self.assertTrue(row["retrieved_at"])
                self.assertEqual("raw_response", row["material_kind"])

    def test_the_hash_is_the_saved_bytes_hash(self):
        built, _result, sheets = self.assemble()
        rows = as_records(sheets["sources"])
        for row in rows:
            with self.subTest(artifact=row["artifact_id"]):
                self.assertEqual(
                    fixture.store.artifact_sha256(built["root"], fixture.RUN,
                                                  row["artifact_id"]),
                    row["content_sha256"])


class ClaimGatingTests(WorkbookCase):
    def test_a_supported_but_unreviewed_claim_is_blocked(self):
        _built, _result, sheets = self.assemble()
        claims = as_records(sheets["claims"])
        self.assertEqual(3, len(claims))
        for claim in claims:
            with self.subTest(claim=claim["claim_id"]):
                self.assertEqual("SUPPORTED", claim["support"])
                self.assertEqual("BLOCKED_NOT_REVIEWED", claim["final_status"])

    def test_only_the_reviewed_claim_becomes_final(self):
        _built, _result, sheets = self.assemble("approved")
        by_id = {claim["claim_id"]: claim for claim in as_records(sheets["claims"])}
        self.assertEqual("APPROVED", by_id["claim-00"]["final_status"])
        self.assertEqual("BLOCKED_NOT_REVIEWED", by_id["claim-01"]["final_status"])
        self.assertEqual("BLOCKED_NOT_REVIEWED", by_id["claim-02"]["final_status"])

    def test_a_review_cannot_make_an_unsupported_claim_final(self):
        _built, _result, sheets = self.assemble("unsupported")
        by_id = {claim["claim_id"]: claim for claim in as_records(sheets["claims"])}
        self.assertEqual("UNSUPPORTED", by_id["claim-02"]["support"])
        self.assertEqual("BLOCKED_UNSUPPORTED", by_id["claim-02"]["final_status"])
        # The review row is still recorded; the claim simply cannot be final.
        self.assertEqual(2, len(as_records(sheets["review"])))

    def test_the_manifest_counts_what_was_blocked(self):
        _built, result, _sheets = self.assemble("unsupported")
        self.assertEqual(2, result["claims_blocked"])
        self.assertEqual(1, result["claims_approved"])


class EvidenceTests(WorkbookCase):
    def test_the_evidence_sheet_copies_the_stored_ids_verbatim(self):
        built, _result, sheets = self.assemble()
        stored = provenance.stored_envelopes(built["root"], fixture.RUN)
        self.assertEqual(sorted(e["evidence_id"] for e in stored),
                         sorted(r["evidence_id"] for r in as_records(sheets["evidence"])))

    def test_every_envelope_names_the_ledger_row_it_came_from(self):
        built, _result, sheets = self.assemble()
        rows = as_records(sheets["evidence"])
        self.assertEqual(3, len(rows))
        for row in rows:
            with self.subTest(evidence=row["evidence_id"]):
                self.assertIn(row["ledger_id"], fixture.OPERATIONS)
                self.assertEqual(row["operation_id"], row["ledger_id"])
                self.assertEqual("1", row["candidate_only"])
                self.assertEqual("1", row["exportable"])

    def test_an_envelope_without_a_ledger_row_is_not_exportable(self):
        built = fixture.build(Path(self.tmp.name) / "unlinked")
        provenance.record_envelope(built["root"], fixture.RUN, "art-00",
                                   operation_id="op-unlinked", provider="typesafe_jev",
                                   capability_id="jev.noul",
                                   extraction_method="server:fixture")
        result = delivery.write_evidence_workbook(
            built["root"], fixture.RUN, "delivery-unlinked", ledger=built["ledger"],
            project=built["project"], reviews=[])
        rows = as_records(read_workbook(Path(result["xlsx_path"]))["evidence"])
        unlinked = next(r for r in rows if r["operation_id"] == "op-unlinked")
        self.assertEqual("", unlinked["ledger_id"])
        self.assertEqual("0", unlinked["exportable"])
        self.assertIn("LEDGER_NOT_WIRED", unlinked["caveats"])


class LedgerReceiptTests(WorkbookCase):
    def test_the_states_are_copied_verbatim_from_the_ledger(self):
        built, _result, sheets = self.assemble()
        rows = as_records(sheets["ledger_receipts"])
        expected = {op.operation_id: op.charge_state
                    for op in built["ledger"].operations(project=built["project"],
                                                         run_id=fixture.RUN)}
        self.assertEqual(expected, {row["ledger_id"]: row["charge_state"] for row in rows})

    def test_an_unreconciled_charge_stays_unknown_with_no_actual(self):
        _built, _result, sheets = self.assemble()
        rows = {row["ledger_id"]: row for row in as_records(sheets["ledger_receipts"])}
        self.assertEqual("UNKNOWN", rows["op-unknown"]["charge_state"])
        self.assertEqual("", rows["op-unknown"]["actual_usd"])
        self.assertEqual("NONE", rows["op-unknown"]["actual_source"])

    def test_a_proven_no_charge_is_not_reported_as_settled(self):
        _built, _result, sheets = self.assemble()
        rows = {row["ledger_id"]: row for row in as_records(sheets["ledger_receipts"])}
        self.assertEqual("NO_CHARGE_PROVEN", rows["op-no-charge"]["charge_state"])
        self.assertNotEqual("SETTLED", rows["op-no-charge"]["charge_state"])

    def test_no_ledger_row_appears_twice(self):
        _built, _result, sheets = self.assemble()
        ids = [row["ledger_id"] for row in as_records(sheets["ledger_receipts"])]
        self.assertEqual(len(ids), len(set(ids)))

    def test_the_manifest_counts_states_without_promoting_them(self):
        _built, result, _sheets = self.assemble()
        self.assertEqual({"NO_CHARGE_PROVEN": 1, "SETTLED": 1, "UNKNOWN": 1},
                         result["ledger_states"])


class SafetyTests(WorkbookCase):
    def test_the_workbook_carries_no_credential_or_account_reference(self):
        _built, result, _sheets = self.assemble()
        blob = Path(result["xlsx_path"]).read_bytes().decode("latin-1")
        text = " ".join(path.read_text(encoding="utf-8-sig")
                        for path in Path(result["directory"]).glob("*.csv"))
        for haystack in (blob, text):
            self.assertNotIn("acct-fixture", haystack)
            self.assertIsNone(re.search(r"\bsk-[A-Za-z0-9]{16,}\b", haystack))
            self.assertIsNone(re.search(r"(?i)\bbearer\s+\S{20,}", haystack))

    def test_an_export_is_immutable(self):
        built, _result, _sheets = self.assemble()
        with self.assertRaises(FileExistsError):
            delivery.write_evidence_workbook(
                built["root"], fixture.RUN, f"delivery-default-{self._n}",
                ledger=built["ledger"], project=built["project"], reviews=[])

    def test_the_existing_nine_sheet_delivery_still_works(self):
        """The new workbook is additive; the business export must not regress."""
        built = fixture.build(Path(self.tmp.name) / "regression")
        import research_delivery as module
        result = module.write_delivery(built["root"], fixture.RUN, "delivery-business")
        self.assertEqual(9, len(module.TABLES))
        self.assertEqual("PARTIAL_CANDIDATE_ONLY_NOT_FORMAL_CONTACTS", result["status"])


if __name__ == "__main__":
    unittest.main()
