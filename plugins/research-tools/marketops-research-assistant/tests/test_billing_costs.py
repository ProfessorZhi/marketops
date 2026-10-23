"""Money per call, per Run and per account, not only call counts."""
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import research_balances as balances  # noqa: E402
import research_costs as costs  # noqa: E402


def op(provider, capability, run_id="r1", units=1, when=None, actual=None):
    return SimpleNamespace(provider=provider, capability=capability, run_id=run_id,
                           quota_units=units, quota_unit="unit", counts_against_caps=True,
                           charge_state="SETTLED",
                           actual_micros=actual, actual_source="vendor" if actual else "",
                           created_at=(when or datetime.now(timezone.utc)).isoformat())


class FakeLedger:
    def __init__(self, rows):
        self.rows = rows

    def operations(self, project=None, run_id=None):
        return tuple(r for r in self.rows if run_id is None or r.run_id == run_id)


class BilledUnits(unittest.TestCase):
    def test_serper_bills_a_credit_per_ten_results(self):
        self.assertEqual(1, costs.billed_units("serper", "search", {"limit": 10}))
        self.assertEqual(2, costs.billed_units("serper", "search", {"limit": 20}))
        self.assertEqual(1, costs.billed_units("serper", "search", {}))
        self.assertEqual(1, costs.billed_units("serpapi", "search", {"num": 50}))


class Report(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def run_report(self, rows, **kw):
        with patch("research_usage_ledger.for_root", return_value=FakeLedger(rows)), \
             patch("research_usage_ledger.project_for_root", return_value="p"):
            return costs.report(self.root, **kw)

    def test_spend_per_provider_and_run_with_operator_rate(self):
        rows = [op("serper", "search", units=2), op("serper", "search", run_id="r2"),
                op("serpapi", "search"), op("crawl4ai", "render"), op("tinyfish", "fetch")]
        costs.set_rate(self.root, provider="serpapi", capability="search",
                       usd_per_unit="0.015", plan="Developer")
        out = self.run_report(rows)
        self.assertEqual("0.0030", out["by_provider"]["serper"]["estimated_usd"])
        self.assertEqual("0.0150", out["by_provider"]["serpapi"]["estimated_usd"])
        self.assertEqual("0.0000", out["by_provider"]["crawl4ai"]["estimated_usd"])
        self.assertEqual("0.0180", out["total_estimated_usd"])
        self.assertEqual(4, out["by_run"]["r1"]["calls"])
        self.assertEqual(1, out["by_run"]["r2"]["calls"])
        self.assertEqual("operator", out["rates"]["serpapi/search"]["origin"])

    def test_an_unknown_rate_is_unknown_not_zero(self):
        out = self.run_report([op("tinyfish", "agent_run", units=12)])
        self.assertIn("UNKNOWN", out["total_estimated_usd"])

    def test_free_allowances_count_this_month(self):
        last_month = datetime.now(timezone.utc) - timedelta(days=40)
        rows = [op("serpapi", "search"), op("serpapi", "search", when=last_month)]
        out = self.run_report(rows)
        self.assertEqual(1, out["free_allowances"]["serpapi/search"]["used"])
        self.assertEqual(249, out["free_allowances"]["serpapi/search"]["left"])


class Balances(unittest.TestCase):
    def test_a_manual_reading_minus_later_calls_is_the_estimate(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            balances.record_manual(root, provider="serper", left=100, unit="credit")
            later = datetime.now(timezone.utc) + timedelta(seconds=5)
            rows = [op("serper", "search", units=2, when=later), op("serper", "search", when=later)]
            with patch("research_usage_ledger.for_root", return_value=FakeLedger(rows)), \
                 patch("research_usage_ledger.project_for_root", return_value="p"):
                out = costs.balances(root, read=False)
            self.assertEqual(97, out["serper"]["estimated_left_now"])
            self.assertEqual(2, out["serper"]["calls_since_reading"])

    def test_manual_readings_are_validated(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(ValueError):
                balances.record_manual(Path(tmp), provider="serper", left=-1, unit="credit")
            with self.assertRaises(ValueError):
                balances.record_manual(Path(tmp), provider="nobody", left=1, unit="credit")


if __name__ == "__main__":
    unittest.main()
