"""A provider out of credit is skipped at once, remembered, and reported."""
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import research_balances as balances  # noqa: E402
import research_fallback as fallback  # noqa: E402
import research_provider_health as health  # noqa: E402


def failed(detail, caveats=()):
    return {"status": "FAILED", "reason_code": "SERPER_UPSTREAM_FAILED",
            "payload": {"detail": detail}, "caveats": list(caveats)}


class Classify(unittest.TestCase):
    def test_vendor_messages(self):
        self.assertEqual("QUOTA_EXHAUSTED", health.classify(failed("HTTP 400: Not enough credits")))
        self.assertEqual("QUOTA_EXHAUSTED", health.classify(failed("HTTP 402")))
        self.assertEqual("QUOTA_EXHAUSTED", health.classify(
            failed("", caveats=["UPSTREAM_QUOTA_OR_PAYMENT"])))
        self.assertEqual("RATE_LIMITED", health.classify(failed("HTTP 429: Too Many Requests")))
        self.assertEqual("", health.classify(failed("HTTP 403: Unauthorized")))
        self.assertEqual("", health.classify({"status": "OK", "payload": {"detail": "quota"}}))


class Remember(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def test_exhausted_is_remembered_and_answered_locally(self):
        health.record(self.root, "serper", "QUOTA_EXHAUSTED", detail="Not enough credits")
        entry = health.blocked(self.root, "serper")
        self.assertIsNotNone(entry)
        answer = health.refusal("serper", "serper.search", entry)
        self.assertEqual("BLOCKED", answer["status"])
        self.assertFalse(answer["gateway_invoked"])
        self.assertEqual(("NEXT", "BUDGET_PROVIDER_CEILING"),
                         fallback._decision(answer, {"task_kind": "source_discovery"}))

    def test_serpapi_waits_for_next_month(self):
        entry = health.record(self.root, "serpapi", "QUOTA_EXHAUSTED")
        self.assertTrue(entry["until"].endswith("T00:00:00+00:00"))
        self.assertTrue(entry["until"][8:10] == "01")

    def test_a_balance_above_zero_lifts_the_mark(self):
        health.record(self.root, "serper", "QUOTA_EXHAUSTED")
        balances.record_manual(self.root, provider="serper", left=5000, unit="credit")
        self.assertIsNone(health.blocked(self.root, "serper"))
        balances.record_manual(self.root, provider="serper", left=0, unit="credit")
        self.assertIsNotNone(health.blocked(self.root, "serper"))

    def test_low_balances_warn(self):
        balances.record_manual(self.root, provider="serper", left=50, unit="credit")
        self.assertTrue(any(w.startswith("serper") for w in balances.warnings(self.root)))


class Chain(unittest.TestCase):
    def test_the_first_exhausted_answer_moves_the_chain_on(self):
        answer = {**failed("HTTP 400: Not enough credits"),
                  "provider_health": {"provider": "serper", "state": "QUOTA_EXHAUSTED"}}
        self.assertEqual(("NEXT", "PROVIDER_QUOTA_EXHAUSTED"),
                         fallback._decision(answer, {"task_kind": "source_discovery"}))


if __name__ == "__main__":
    unittest.main()
