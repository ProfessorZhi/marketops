"""Credit metering cannot establish a USD zero; keep output and uncertainty."""
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import research_usage_ledger as usage


class CreditOnlyMoneyTests(unittest.TestCase):
    def setUp(self):
        from test_delivery_runtime import DefaultDispatchTests
        self.case = DefaultDispatchTests("test_default_gateway_returns_and_persists_real_adapter_result")
        self.case.setUp(); self.addCleanup(self.case.doCleanups)
        prices = patch.object(usage, "_pricing_from_environment", return_value=usage.PricingCatalog(()))
        prices.start(); self.addCleanup(prices.stop)

    def test_default_credit_success_keeps_material_but_holds_unknown_usd(self):
        answer = self.case.mcp.search("serper", "public venue", "US", "en", "run-a", "one")
        self.assertEqual("OK", answer["status"])
        self.assertTrue(answer["artifact_ids"])
        ledger = usage.for_root(self.case.root)
        row = ledger.get(answer["ledger_operation_id"])
        self.assertEqual("UNKNOWN", row.charge_state)
        self.assertIsNone(row.actual_micros)
        self.assertFalse(row.price_verified)
        self.assertEqual(1, ledger.account_totals("serper", "serper").calls)
        again = self.case.mcp.managed_call("serper.search", {"query": "public venue"}, "run-a", "renamed")
        self.assertEqual("DUPLICATE_OPERATION", again["reason_code"])
        self.assertEqual(1, len(self.case.calls))
        report = ledger.report(usage.project_for_root(self.case.root))
        self.assertEqual("UNKNOWN", report["money"]["actual_usd_status"])
        # Billing uncertainty does not turn an independently successful next
        # research operation into an execution timeout or make it unusable.
        import research_fallback as fallback
        import research_runtime_setup as runtime
        chain = fallback.research_step(self.case.root, run_id="run-a", operation_id="new-task",
            task_kind="source_discovery", query="another public venue", country="US", language="en",
            managed_call=self.case.mcp.managed_call,
            admission=lambda capability: runtime.admission(self.case.root, capability))
        self.assertEqual("SUCCESS_CANDIDATE", chain["status"])
        self.assertEqual(2, len(self.case.calls))
        self.assertEqual(1, chain["unknown_cost_attempts"])

    def test_historical_unverified_derived_zero_is_displayed_unknown_without_rewriting(self):
        answer = self.case.mcp.search("serper", "public venue", "US", "en", "run-a", "one")
        ledger = usage.for_root(self.case.root)
        operation = answer["ledger_operation_id"]
        # Reproduce an old build's historical row in an isolated test ledger.
        if ledger.get(operation).charge_state == "UNKNOWN":
            ledger.reconcile(operation, actual_micros=0, actual_source="DERIVED")
        before = ledger.get(operation)
        self.assertEqual(0, before.actual_micros)
        self.assertIsNone(usage.display_actual_micros(before))
        report = ledger.report(usage.project_for_root(self.case.root))
        self.assertEqual(1, report["money"]["legacy_unverified_zero_operations"])
        self.assertEqual("UNKNOWN", report["money"]["actual_usd_status"])
        self.assertIsNone(report["money"]["actual_total_micros"])
        self.assertEqual(before, ledger.get(operation))


if __name__ == "__main__": unittest.main()
