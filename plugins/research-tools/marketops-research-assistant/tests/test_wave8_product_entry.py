"""I-W9: the evidence workbook and the packaging surface, from the product entry.

W-F's `write_evidence_workbook` and W-G's client packaging were each verified on
their own branches against their own fixtures. What this file adds is the join
the integrator owns: the workbook is reachable from the product entry
(`research_mcp`), and it binds the **shipped** `UsageLedger` for the customer data
root rather than a stand-in. A workbook that looked right over a fixture ledger
would prove nothing about the product.

The ledger states are asserted verbatim, including the one that matters most: an
UNKNOWN charge must stay UNKNOWN with an empty actual amount. A totals column
that quietly summed it would turn an unresolved charge into a settled one.

Offline: temporary roots, the real ledger and store, no provider, no credential.
"""
from __future__ import annotations

import csv
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import _wave7_evidence_fixture as fixture  # noqa: E402
import research_mcp as mcp  # noqa: E402
import research_usage_ledger as ledger_module  # noqa: E402

SHEETS = ("sources", "claims", "evidence", "ledger_receipts", "review")


def _rows(directory: str, sheet: str) -> list[dict[str, str]]:
    with open(Path(directory) / f"{sheet}.csv", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


class WorkbookProductEntryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.built = fixture.build(Path(self.tmp.name))
        self.root = self.built["root"]
        # The ledger books under an operator-named project when one is named, and
        # the fixture names `w7-evidence`. Naming it here is the same resolution
        # the product performs; without it the workbook is correct but looks at a
        # different project's (empty) receipts.
        env = patch.dict(os.environ, {
            "MARKETOPS_RESEARCH_DATA_ROOT": str(self.root),
            "MARKETOPS_RESEARCH_PROJECT": fixture.PROJECT})
        env.start()
        self.addCleanup(env.stop)

    def test_the_product_entry_writes_the_five_named_sheets(self):
        result = mcp.evidence_workbook(fixture.RUN, "delivery-product-1")
        self.assertEqual("EVIDENCE_CHAIN_CANDIDATE_ONLY", result["status"])
        self.assertEqual(list(SHEETS), list(result["sheets"]))
        self.assertTrue(Path(result["xlsx_path"]).is_file())
        self.assertEqual(5, len(result["sheets"]))
        for sheet in SHEETS:
            self.assertTrue((Path(result["directory"]) / f"{sheet}.csv").is_file(), sheet)

    def test_the_receipts_sheet_binds_the_shipped_ledger_row_for_row(self):
        """Not a copy of a fixture: the same rows `UsageLedger` holds."""
        result = mcp.evidence_workbook(fixture.RUN, "delivery-product-2")
        ledger = ledger_module.for_root(self.root)
        expected = {op.operation_id: op for op in ledger.operations(
            project=self.built["project"], run_id=fixture.RUN)}
        written = {row["ledger_id"]: row
                   for row in _rows(result["directory"], "ledger_receipts")}
        self.assertEqual(set(expected), set(written))
        for operation_id, operation in expected.items():
            self.assertEqual(operation.charge_state, written[operation_id]["charge_state"],
                             operation_id)

    def test_an_unknown_charge_is_never_exported_as_settled(self):
        result = mcp.evidence_workbook(fixture.RUN, "delivery-product-3")
        written = {row["operation_id"]: row
                   for row in _rows(result["directory"], "ledger_receipts")}
        # The ledger's own spelling, copied verbatim. `UNKNOWN_CHARGE_OUTSTANDING`
        # is the *bridge's* name for the same fact on the QA cost-ledger schema;
        # rewriting it here would make the workbook disagree with the row it cites.
        unknown = next((row for row in written.values()
                        if row["charge_state"] == "UNKNOWN"), None)
        self.assertIsNotNone(unknown, sorted(written))
        self.assertEqual("", unknown["actual_usd"],
                         "an unknown amount was written as if it were known")
        self.assertIn("UNKNOWN", result["ledger_states"])
        self.assertNotIn("SETTLED", {row["charge_state"] for row in written.values()
                                     if row["operation_id"] == "op-unknown"})

    def test_the_workbook_is_reachable_as_a_customer_tool(self):
        # The customer reaches the workbook through the one delivery tool.
        server = mcp.build_server()
        tools = server._tool_manager._tools
        self.assertIn("marketops_handoff", tools)
        self.assertIn("run_id", tools["marketops_handoff"].parameters.get("properties", {}))

    def test_a_second_delivery_with_the_same_id_is_refused(self):
        """Exports are immutable; a re-run must pick a new delivery id."""
        mcp.evidence_workbook(fixture.RUN, "delivery-product-4")
        with self.assertRaises(FileExistsError):
            mcp.evidence_workbook(fixture.RUN, "delivery-product-4")


if __name__ == "__main__":
    unittest.main()
