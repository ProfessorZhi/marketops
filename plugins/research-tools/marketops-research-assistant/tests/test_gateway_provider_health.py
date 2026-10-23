"""A provider out of credit is skipped at once, remembered, and reported."""
import sys
import json
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
        self.assertEqual("", health.classify(failed("HTTP 403: Insufficient permissions")))
        self.assertEqual("QUOTA_EXHAUSTED", health.classify(failed("Insufficient credits")))
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

    def test_concurrent_provider_failures_do_not_lose_each_others_marks(self):
        from concurrent.futures import ThreadPoolExecutor

        providers = [f"provider-{i}" for i in range(30)]
        with ThreadPoolExecutor(max_workers=8) as pool:
            list(pool.map(lambda p: health.record(self.root, p, "QUOTA_EXHAUSTED"), providers))
        self.assertEqual(set(providers), set(health.status(self.root)))

    def test_legacy_json_is_migrated_once_and_a_cleared_mark_stays_cleared(self):
        health.record(self.root, "serper", "QUOTA_EXHAUSTED")
        legacy = health.status(self.root)
        database = self.root / "state/provider-health.sqlite3"
        database.unlink()
        health._path(self.root).write_text(json.dumps(legacy), encoding="utf-8")
        health.clear(self.root, "serper")
        health.record(self.root, "jev", "RATE_LIMITED")
        self.assertEqual({"jev"}, set(health.status(self.root)))


class Chain(unittest.TestCase):
    def test_discovery_credits_the_substitute_and_keeps_the_healthy_provider(self):
        from unittest.mock import patch
        import research_mcp as mcp

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            health.record(root, "serper", "QUOTA_EXHAUSTED")

            def search(provider, *args, **kwargs):
                if provider == "serper":
                    raise ValueError("BUDGET_PROVIDER_CEILING")
                return {"results": [{"url": f"https://{provider}.example/"}]}

            # SerpApi stands in for Serper only where it is not a route of its own.
            with patch.object(mcp, "root", return_value=root), \
                    patch.object(mcp, "_search_routes", return_value=("serper", "tinyfish")), \
                    patch.object(mcp, "search", side_effect=search) as called:
                answer = mcp._search_all("clubs", "us", "en", "r", "op", 10)
            self.assertEqual({"serper", "serpapi", "tinyfish"},
                             {call.args[0] for call in called.call_args_list})
            self.assertEqual(["serpapi", "tinyfish"], [h["found_by"] for h in answer["results"]])
            self.assertEqual({"serper": "serpapi"}, answer["substituted"])

    def test_the_first_exhausted_answer_moves_the_chain_on(self):
        answer = {**failed("HTTP 400: Not enough credits"),
                  "provider_health": {"provider": "serper", "state": "QUOTA_EXHAUSTED"}}
        self.assertEqual(("NEXT", "PROVIDER_QUOTA_EXHAUSTED"),
                         fallback._decision(answer, {"task_kind": "source_discovery"}))


if __name__ == "__main__":
    unittest.main()
