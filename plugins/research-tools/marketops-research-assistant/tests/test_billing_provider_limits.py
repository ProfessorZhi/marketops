"""Per-provider local consent is enforced by the same cumulative usage ledger."""
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import research_runtime_setup as runtime
import _runtime_mcp_fixture as fixture


class ProviderLimitTests(unittest.TestCase):
    def setUp(self):
        from test_delivery_runtime import DefaultDispatchTests
        self.case = DefaultDispatchTests("test_default_gateway_returns_and_persists_real_adapter_result")
        self.case.setUp()
        self.addCleanup(self.case.doCleanups)
        self.root, self.mcp = self.case.root, self.case.mcp
        self.calls = []
        fixture.bind(self, self.root, calls=self.calls)
        env = patch.dict(os.environ, {"SERPAPI_API_KEY": "fixture-key"})
        env.start(); self.addCleanup(env.stop)

    def configure(self, **kwargs):
        return runtime.configure(self.root, providers=["serper", "serpapi"],
            domains=["example.org"], max_calls=5, task="provider bounded research",
            confirm=True, amend=True, **kwargs)

    def call(self, provider, query, operation, run="run-a"):
        return self.mcp.managed_call(provider + ".search", {"query": query}, run, operation)

    def test_different_provider_caps_exhaust_independently_before_project_cap(self):
        self.configure(provider_limits={"serper": 1, "serpapi": 2})
        import io
        from contextlib import redirect_stdout
        with patch("research_credentials.load_into_environment"), redirect_stdout(io.StringIO()):
            self.assertEqual(0, runtime.main(["configure", "--data-root", str(self.root),
                "--provider", "serper", "--provider", "serpapi", "--domains", "example.org",
                "--max-calls", "5", "--task", "provider bounded research", "--confirm",
                "--provider-limit", "serper=1", "--provider-limit", "serpapi=2"]))
        self.assertEqual("OK", self.call("serper", "venue one", "serper-one")["status"])
        denied = self.call("serper", "venue two", "serper-two")
        self.assertEqual("BUDGET_PROVIDER_CEILING", denied["reason_code"])
        for i in range(2):
            self.assertEqual("OK", self.call("serpapi", "other venue " + str(i), "serp-" + str(i))["status"])
        denied = self.call("serpapi", "third other venue", "serp-three")
        self.assertEqual("BUDGET_PROVIDER_CEILING", denied["reason_code"])
        self.assertEqual(1, len(self.case.calls))
        self.assertEqual(2, len(self.calls))

    def test_resume_preserves_provider_usage_and_amend_cannot_lower_below_spent(self):
        self.configure(provider_limits={"serper": 1, "serpapi": 2})
        self.call("serper", "venue one", "serper-one")
        self.call("serpapi", "other venue one", "serp-one")
        self.call("serpapi", "other venue two", "serp-two")
        before = runtime.config(self.root)
        self.assertEqual(before, self.configure())
        brief = {"record_type": "research_brief", "target_kind": "venue", "goal": "pool",
                 "minimum_targets": None, "require_named_person": False}
        self.mcp.start("run-b", "test-scope", "new run same allowance", "rules-v1", brief)
        self.assertEqual("BUDGET_PROVIDER_CEILING",
                         self.call("serper", "fresh run venue", "new-run", run="run-b")["reason_code"])
        with self.assertRaisesRegex(ValueError, "CAP_BELOW_HELD"):
            self.configure(provider_limits={"serper": 1, "serpapi": 1})
        self.assertEqual(before, runtime.config(self.root))
        for limits in ({"tinyfish": 1}, {"serper": 0}, {"serper": 6}, {"serper": True}):
            with self.assertRaises(ValueError):
                self.configure(provider_limits=limits)
        self.assertEqual(1, len(self.case.calls))
        self.assertEqual(2, len(self.calls))


if __name__ == "__main__":
    unittest.main()
