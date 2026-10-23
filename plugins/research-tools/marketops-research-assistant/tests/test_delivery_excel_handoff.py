"""Ensure spreadsheet-bound research text is escaped and file handoff is safe."""
from __future__ import annotations
import csv
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts import research_pack as rp  # noqa: E402
from scripts import export_excel_safe_csv as ex  # noqa: E402

class ExcelHandoffTests(unittest.TestCase):
    def test_formula_injection_escaped_and_ids_preserved(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            pack = root / "pack"
            rp.init_pack(pack, "demo-xlsx-01", "GB", "test only")
            with (pack / "stages" / "sources.csv").open("a", newline="", encoding="utf-8") as f:
                writer = csv.DictWriter(f, rp.TABLES["sources"], lineterminator="\n")
                writer.writerow({"source_id": "s001", "query": '=HYPERLINK("https://bad.example","click")',
                                "url": "https://example.org", "kind": "web", "discovered_at": "2026-10-15T00:00:00Z",
                                "status": "discovered"})
            out = root / "output"
            report = ex.export_pack(pack, out)
            self.assertEqual("CSV_HANDOFF_CREATED_NOT_XLSX", report["result"])
            with (out / "sources.csv").open(newline="", encoding="utf-8-sig") as f:
                row = next(csv.DictReader(f))
            self.assertEqual("s001", row["source_id"])
            self.assertTrue(row["query"].startswith("'=HYPERLINK"))
            with self.assertRaises(FileExistsError):
                ex.export_pack(pack, out)

    def test_prefix_controls(self):
        for value in ("=1+1", " +SUM(1,2)", "-3+1", "@SUM(1)", "\t=1"):
            self.assertTrue(ex.literal_cell(value).startswith("'"), value)
        self.assertEqual("Example Corporation", ex.literal_cell("Example Corporation"))

if __name__ == "__main__":
    unittest.main()
