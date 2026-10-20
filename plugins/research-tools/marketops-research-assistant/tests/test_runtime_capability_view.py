"""The advice shown to an agent must agree with the actual admitted runtime."""
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import research_mcp as mcp
import research_runtime_setup as runtime


class RuntimeCapabilityViewTests(unittest.TestCase):
    def test_local_consent_is_not_misreported_as_old_environment_disabled(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "data"
            with patch.dict(os.environ, {"MARKETOPS_RESEARCH_DATA_ROOT": str(root),
                                         "MARKETOPS_RESEARCH_ALLOW_NETWORK": "1",
                                         "SERPER_API_KEY": "synthetic-key"}):
                runtime.configure(root, providers=["serper"], domains=[], max_calls=5,
                                  task="Synthetic scoped task", confirm=True)
                with patch.object(runtime, "verify_build_receipt", return_value={"ok": True}):
                    result = mcp.capabilities(task_kind="source_discovery")
                self.assertEqual("REQUEST_COUNT", result["runtime"]["budget_mode"])
                item = result["capability_matrix"]["serper.search"]
                self.assertTrue(item["authorization_verified"])
                self.assertNotEqual("DISABLED_BY_PROJECT_OR_PROCESS_POLICY", item["status"])
                self.assertFalse(item["credits_verified"])
                self.assertEqual(5, result["budgets"]["managed"]["remaining"])
                self.assertFalse((root / "state" / "usage-ledger.sqlite3").exists())

    def test_a_missing_build_receipt_does_not_disable_research(self):
        # Option A (user decision 2026-10-20): the receipt is reported, never a gate.
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "data"
            with patch.dict(os.environ, {"MARKETOPS_RESEARCH_DATA_ROOT": str(root),
                                         "MARKETOPS_RESEARCH_ALLOW_NETWORK": "1",
                                         "SERPER_API_KEY": "synthetic-key"}):
                runtime.configure(root, providers=["serper"], domains=[], max_calls=5,
                                  task="Synthetic scoped task", confirm=True)
                with patch.object(runtime, "verify_build_receipt", return_value={"ok": False}):
                    result = mcp.capabilities()
                item = result["capability_matrix"]["serper.search"]
                self.assertTrue(item["authorization_verified"])
                with patch.object(runtime, "verify_build_receipt", return_value={"ok": False}):
                    self.assertFalse(mcp.setup_status()["build"]["tested_release"])


if __name__ == "__main__":
    unittest.main()
