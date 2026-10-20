"""Client view: admission refusal, formula safety, replay and manifest readback.

All names/emails/URLs are synthetic (example.org).  No network, no provider,
no real client material.
"""
from __future__ import annotations

import csv
import hashlib
import json
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import research_client_view as view  # noqa: E402
from _xlsx import Sheet  # noqa: E402

NS = {"x": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
STAMP = "2026-10-20T00:00:00+00:00"
EVIDENCE_HEADERS = ("资料ID", "来源URL", "提供者", "资料类型",
                    "取得时间", "字节数", "SHA-256", "引用状态")
ORG_HEADERS = ("组织ID", "组织名称", "研究范围", "研究状态", "原始资料引用")
CONTACT_HEADERS = ("渠道ID", "组织ID", "联系人或部门", "职能", "渠道类型",
                   "公开字面值", "来源字面校验", "研究评级", "业务状态", "资料引用")


def evidence_sheet(count: int = 3) -> Sheet:
    rows = tuple(
        (f"art-{i}", f"https://example.org/{i}", "fixture", "raw_response",
         "2026-10-20T00:00:00Z", 10,
         hashlib.sha256(str(i).encode()).hexdigest(),
         "READBACK_HASH_OK_NOT_FACT_VERIFIED")
        for i in range(1, count + 1)
    )
    return Sheet("证据索引", EVIDENCE_HEADERS, rows)


def organization_sheet(names: list[str], refs: list[str] | None = None,
                       headers: tuple[str, ...] = ORG_HEADERS,
                       extra: tuple[str, ...] = ()) -> Sheet:
    refs = refs or [f"art-{i}" for i in range(1, len(names) + 1)]
    rows = tuple(
        (f"org-{i}", name, "GB-sports", "CANDIDATE_NOT_APPROVED", ref, *extra)
        for i, (name, ref) in enumerate(zip(names, refs), start=1)
    )
    return Sheet("客户总表", headers, rows)


def contact_sheet(count: int = 3, statuses: list[str] | None = None,
                  refs: list[str] | None = None,
                  business_statuses: list[str] | None = None) -> Sheet:
    statuses = statuses or ["LITERAL_IN_SAVED_MATERIAL"] * count
    refs = refs or [f"art-{i}" for i in range(1, count + 1)]
    business_statuses = business_statuses or ["CANDIDATE_NOT_APPROVED"] * count
    rows = tuple(
        (f"channel-{i}", f"org-{i}", "Grounds Management", "Grounds",
         "email", f"grounds{i}@example.org", status, "RULES_UNKNOWN",
         business_status, ref)
        for i, (status, business_status, ref) in enumerate(
            zip(statuses, business_statuses, refs), start=1)
    )
    return Sheet("联系人路线", CONTACT_HEADERS, rows)


def positive_sheets() -> list[Sheet]:
    return [organization_sheet(["Example Org A", "Example Org B", "Example Org C"]),
            contact_sheet(3), evidence_sheet(3)]


def read_csv(path: Path) -> list[list[str]]:
    with path.open(encoding="utf-8-sig", newline="") as handle:
        return list(csv.reader(handle))


def read_xlsx(path: Path) -> dict[str, list[list[str]]]:
    out: dict[str, list[list[str]]] = {}
    with zipfile.ZipFile(path) as archive:
        workbook = ET.fromstring(archive.read("xl/workbook.xml"))
        names = [node.get("name") for node in workbook.findall(".//x:sheet", NS)]
        for index, name in enumerate(names, start=1):
            root = ET.fromstring(archive.read(f"xl/worksheets/sheet{index}.xml"))
            rows: list[list[str]] = []
            for row in root.findall(".//x:sheetData/x:row", NS):
                cells: list[str] = []
                for cell in row.findall("x:c", NS):
                    if cell.get("t") == "inlineStr":
                        node = cell.find(".//x:t", NS)
                        cells.append("" if node is None else (node.text or ""))
                    else:
                        node = cell.find("x:v", NS)
                        cells.append("" if node is None else (node.text or ""))
                rows.append(cells)
            out[name] = rows
    return out


class ClientViewGeneration(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)

    def _write(self, name: str = "client-view", sheets=None, **kwargs) -> tuple[Path, dict]:
        destination = self.base / name
        result = view.write_client_view(
            destination, sheets if sheets is not None else positive_sheets(),
            label="synthetic-client", generated_at=STAMP, **kwargs)
        return destination, result

    # -- positive path -----------------------------------------------------
    def test_three_admitted_rows_generate_receipt_and_manifest(self):
        destination, result = self._write()
        self.assertEqual("CANDIDATE_NOT_APPROVED", result["status"])
        self.assertIs(False, result["send_authorized"])
        self.assertIs(False, result["formal_sop_claimed"])
        self.assertEqual({"客户总表": 3, "联系人路线": 3, "证据索引": 3},
                         result["counts"])
        expected = {"Client_View.xlsx", "MANIFEST.json", "客户视图说明.csv",
                    "来源索引.csv", "客户总表.csv", "联系人路线.csv", "证据索引.csv"}
        self.assertEqual(expected, set(result["files"]) | {"MANIFEST.json"})
        for name, meta in result["files"].items():
            payload = (destination / name).read_bytes()
            self.assertEqual(len(payload), meta["bytes"])
            self.assertEqual(hashlib.sha256(payload).hexdigest(), meta["sha256"])
        self.assertEqual("VERIFIED_CANDIDATE_NOT_APPROVED",
                         view.verify_client_view(destination)["status"])
        notes = dict(read_csv(destination / "客户视图说明.csv")[1:])
        self.assertEqual("CANDIDATE_NOT_APPROVED", notes["数据状态"])
        self.assertEqual("FALSE", notes["发送授权"])
        self.assertEqual("FALSE_NOT_CLAIMED", notes["正式 SOP"])
        for path in destination.glob("*.csv"):
            for row in read_csv(path):
                for cell in row:
                    self.assertNotIn("HOLD_", cell)

    def test_xlsx_and_csv_projection_are_identical(self):
        destination, result = self._write()
        workbook = read_xlsx(destination / "Client_View.xlsx")
        self.assertEqual(result["output_sheets"], list(workbook))
        for sheet_name in result["output_sheets"]:
            self.assertEqual(read_csv(destination / f"{sheet_name}.csv"),
                             workbook[sheet_name])
        self.assertEqual(len(workbook["客户总表"]), 4)  # header + 3 rows

    # -- refusal: holds, conflicts, missing citations ----------------------
    def test_three_positive_and_three_hold_rows_are_refused_without_output(self):
        statuses = ["LITERAL_IN_SAVED_MATERIAL"] * 3 + [
            "HOLD_NOT_LITERAL", "HOLD_NO_SOURCE", "HOLD_EMAIL_QUOTE_MISMATCH"]
        sheets = [organization_sheet(["A", "B", "C"]),
                  contact_sheet(6, statuses=statuses), evidence_sheet(6)]
        destination = self.base / "client-view-hold"
        with self.assertRaises(view.ClientViewError) as ctx:
            view.write_client_view(destination, sheets, label="synthetic-client",
                                   generated_at=STAMP)
        self.assertEqual("HOLD_ROW_REJECTED", ctx.exception.code)
        self.assertFalse(destination.exists())

    def test_conflict_marker_and_duplicate_key_are_refused(self):
        headers = ORG_HEADERS + ("未解决事项",)
        conflict = organization_sheet(["A"], headers=headers, extra=("组织合并冲突",))
        destination = self.base / "client-view-conflict"
        with self.assertRaises(view.ClientViewError) as ctx:
            view.write_client_view(destination, [conflict, evidence_sheet(1)],
                                   label="synthetic-client", generated_at=STAMP)
        self.assertEqual("CONFLICT_MARKER", ctx.exception.code)
        self.assertFalse(destination.exists())

        duplicate = Sheet("客户总表", ORG_HEADERS, (
            ("org-1", "A", "GB-sports", "CANDIDATE_NOT_APPROVED", "art-1"),
            ("org-1", "B", "GB-sports", "CANDIDATE_NOT_APPROVED", "art-2"),
        ))
        with self.assertRaises(view.ClientViewError) as ctx:
            view.write_client_view(self.base / "client-view-dup",
                                   [duplicate, evidence_sheet(2)],
                                   label="synthetic-client", generated_at=STAMP)
        self.assertEqual("DUPLICATE_RECORD_KEY", ctx.exception.code)

    def test_approval_claim_is_refused(self):
        sheets = [organization_sheet(["A"]),
                  contact_sheet(1, business_statuses=["APPROVED"]), evidence_sheet(1)]
        with self.assertRaises(view.ClientViewError) as ctx:
            view.write_client_view(self.base / "client-view-approved", sheets,
                                   label="synthetic-client", generated_at=STAMP)
        self.assertEqual("APPROVAL_CLAIM_REJECTED", ctx.exception.code)

    def test_missing_citation_produces_no_output(self):
        for ref, code in (("NONE", "PROVENANCE_MISSING"), ("", "PROVENANCE_MISSING"),
                          ("art-999", "PROVENANCE_UNKNOWN_REF")):
            with self.subTest(ref=ref):
                destination = self.base / f"client-view-missing-{code}-{ref or 'empty'}"
                sheets = [organization_sheet(["A"], refs=[ref]), evidence_sheet(1)]
                with self.assertRaises(view.ClientViewError) as ctx:
                    view.write_client_view(destination, sheets,
                                           label="synthetic-client", generated_at=STAMP)
                self.assertEqual(code, ctx.exception.code)
                self.assertFalse(destination.exists())

    def test_private_key_material_is_refused(self):
        sheets = [organization_sheet(
                      ["-----BEGIN RSA PRIVATE KEY-----\nMIIBsynthetic"]),
                  evidence_sheet(1)]
        with self.assertRaises(view.ClientViewError) as ctx:
            view.write_client_view(self.base / "client-view-key", sheets,
                                   label="synthetic-client", generated_at=STAMP)
        self.assertEqual("PRIVATE_KEY_MATERIAL", ctx.exception.code)
        self.assertFalse((self.base / "client-view-key").exists())

    # -- spreadsheet safety ------------------------------------------------
    def test_formula_injection_is_written_as_literal_text(self):
        names = ["=1+1", " +HYPERLINK(\"https://evil.example\")",
                 "@SUM(A1:A2)", "-2+3"]
        sheets = [organization_sheet(names), evidence_sheet(4)]
        destination, _ = self._write("client-view-formula", sheets)
        csv_rows = read_csv(destination / "客户总表.csv")[1:]
        xlsx_rows = read_xlsx(destination / "Client_View.xlsx")["客户总表"][1:]
        for index, name in enumerate(names):
            escaped = "'" + name
            self.assertEqual(escaped, csv_rows[index][1])
            self.assertEqual(escaped, xlsx_rows[index][1])
        with zipfile.ZipFile(destination / "Client_View.xlsx") as archive:
            for name in archive.namelist():
                if name.startswith("xl/worksheets/sheet"):
                    xml = archive.read(name)
                    self.assertNotIn(b"<f>", xml)
                    self.assertNotIn(b"<f ", xml)

    # -- replay and immutability ------------------------------------------
    def test_replay_is_byte_identical(self):
        first_dir, first = self._write("client-view-replay-a")
        # A pre-created empty client-view directory must be publishable.
        (self.base / "client-view-replay-b").mkdir()
        second_dir, second = self._write("client-view-replay-b")
        self.assertEqual(first["files"], second["files"])
        for name in first["files"]:
            self.assertEqual((first_dir / name).read_bytes(),
                             (second_dir / name).read_bytes())
        self.assertEqual((first_dir / "MANIFEST.json").read_bytes(),
                         (second_dir / "MANIFEST.json").read_bytes())

        again = view.write_client_view(first_dir, positive_sheets(),
                                       label="synthetic-client", generated_at=STAMP)
        self.assertTrue(again["replayed"])
        self.assertEqual(first["files"], again["files"])

    def test_existing_different_destination_is_not_overwritten(self):
        destination, _ = self._write("client-view-conflict-dest")
        csv_path = destination / "客户总表.csv"
        original = csv_path.read_bytes()
        tampered = original.replace(b"org-1", b"org-X")
        csv_path.write_bytes(tampered)
        with self.assertRaises(view.ClientViewError) as ctx:
            view.write_client_view(destination, positive_sheets(),
                                   label="synthetic-client", generated_at=STAMP)
        self.assertEqual("DESTINATION_CONFLICT", ctx.exception.code)
        self.assertEqual(tampered, csv_path.read_bytes())

    # -- manifest readback -------------------------------------------------
    def test_manifest_hash_readback_detects_tampering(self):
        destination, result = self._write("client-view-verify")
        manifest = json.loads((destination / "MANIFEST.json").read_text(encoding="utf-8"))
        self.assertEqual(result["files"], manifest["files"])
        csv_path = destination / "客户总表.csv"
        payload = csv_path.read_bytes().replace(b"org-1", b"org-X")
        self.assertEqual(len(payload), len(csv_path.read_bytes()))
        csv_path.write_bytes(payload)
        with self.assertRaises(view.ClientViewError) as ctx:
            view.verify_client_view(destination)
        self.assertEqual("MANIFEST_HASH_MISMATCH", ctx.exception.code)


if __name__ == "__main__":
    unittest.main()
