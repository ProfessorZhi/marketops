"""Explicit account re-observation and customer-owned crawler cache lifetime."""
from datetime import datetime, timedelta, timezone
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import research_runtime_setup as runtime
import research_runtime_transport as transport


class AccountRefreshTests(unittest.TestCase):
    def test_refresh_preserves_frozen_b0_usage_and_unknown_exposure(self):
        from test_delivery_runtime import DefaultDispatchTests
        case = DefaultDispatchTests("test_default_gateway_returns_and_persists_real_adapter_result")
        case.setUp()
        self.addCleanup(case.doCleanups)
        import research_usage_ledger as usage
        # This case needs one explicitly settled fixture operation and one
        # ambiguous execution; default credit-only dollars are tested elsewhere.
        pricing = usage.PricingCatalog.from_entries([{"provider": "serper", "capability": "search",
            "unit_micros": 1000, "quota_unit": "credits", "quota_per_call": 1,
            "source": "SYNTHETIC_TEST_RATE_NOT_VENDOR", "version": "test-1",
            "observed_at": runtime.now(), "verified": True}])
        prices = patch.object(usage, "_pricing_from_environment", return_value=pricing)
        prices.start(); self.addCleanup(prices.stop)
        runtime.configure(case.root, providers=["serper"], domains=["example.org"],
                          max_calls=5, task="public venues", confirm=True, amend=True)
        case.mcp.search("serper", "first venue", "US", "en", "run-a", "settled")
        with patch.object(transport, "serper_transport", side_effect=TimeoutError()):
            uncertain = case.mcp.managed_call("serper.search", {"query": "uncertain venue"}, "run-a", "unknown")
        self.assertEqual("UNKNOWN", uncertain["status"])
        old = (datetime.now(timezone.utc) - timedelta(days=2)).isoformat()
        with patch.object(runtime, "now", return_value=old):
            runtime.observe_account(case.root, "serper", units=100, meter="credits",
                                    source_url="https://serper.dev/dashboard", confirm=True)
        self.assertFalse(runtime.admission(case.root, "serper.search")["allowed"])
        before = runtime.usage_snapshot(case.root, "serper", "credits")
        runtime.observe_account(case.root, "serper", units=98, meter="credits",
                                source_url="https://serper.dev/dashboard", confirm=True, refresh=True)
        self.assertTrue(runtime.admission(case.root, "serper.search")["allowed"])
        self.assertEqual(before, runtime.usage_snapshot(case.root, "serper", "credits"))
        saved = runtime._read(runtime._path(case.root, "serper-account.json"))
        self.assertEqual(100, saved["units"])
        self.assertEqual(old, saved["observed_at"])
        self.assertEqual(98, saved["latest_observation"]["units"])
        ledger = runtime.ledger_for(case.root, "serper")
        self.assertEqual(20, ledger._campaign[("serper", "serper")].floor.floor_units)
        self.assertEqual(1, saved["external_spend_units"])
        self.assertTrue(runtime._path(case.root, "serper-account-history.jsonl").is_file())
        # A fresh top-up observation does not silently raise the frozen B0 or cap.
        runtime.observe_account(case.root, "serper", units=120, meter="credits",
                                source_url="https://serper.dev/dashboard", confirm=True, refresh=True)
        self.assertEqual(100, runtime.ledger_for(case.root, "serper")._campaign[("serper", "serper")].floor.baseline.units)
        self.assertEqual(5, runtime.config(case.root)["max_calls"])

    def test_refresh_requires_new_confirmed_same_unit_observation(self):
        with tempfile.TemporaryDirectory() as temp:
            runtime.observe_account(temp, "serper", units=100, meter="credits",
                                    source_url="https://serper.dev/dashboard", confirm=True)
            for overrides in ({"confirm": False}, {"meter": "usd_micros"}):
                with self.assertRaises(ValueError):
                    runtime.observe_account(temp, "serper", **{
                        "units": 90, "meter": "credits", "source_url": "https://serper.dev/dashboard",
                        "confirm": True, "refresh": True, **overrides})
            self.assertEqual(100, runtime._read(runtime._path(temp, "serper-account.json"))["units"])


class CrawlCacheTests(unittest.TestCase):
    def test_default_cache_is_inside_data_root_and_set_before_sdk_import(self):
        with tempfile.TemporaryDirectory() as temp, patch.dict(os.environ, {}, clear=True), \
                patch.dict(sys.modules, {"crawl4ai.async_database": None}):
            chosen = transport.prepare_crawl_cache(Path(temp))
            self.assertEqual(str(Path(temp) / "cache" / "crawl4ai"), chosen)
            self.assertEqual(chosen, os.environ["CRAWL4_AI_BASE_DIRECTORY"])
            self.assertTrue(Path(chosen).is_dir())

    def test_absolute_operator_override_is_preserved_and_relative_is_refused(self):
        with tempfile.TemporaryDirectory() as temp, patch.dict(sys.modules, {"crawl4ai.async_database": None}):
            chosen = Path(temp) / "operator-cache"
            with patch.dict(os.environ, {"CRAWL4_AI_BASE_DIRECTORY": str(chosen)}):
                self.assertEqual(str(chosen), transport.prepare_crawl_cache(Path(temp) / "data"))
            with patch.dict(os.environ, {"CRAWL4_AI_BASE_DIRECTORY": "./cwd-cache"}):
                with self.assertRaisesRegex(ValueError, "CRAWL_CACHE_OVERRIDE_MUST_BE_ABSOLUTE"):
                    transport.prepare_crawl_cache(Path(temp) / "data")

    def test_already_imported_sdk_cannot_keep_writing_another_root(self):
        from types import SimpleNamespace
        with tempfile.TemporaryDirectory() as temp, patch.dict(os.environ, {}, clear=True), \
                patch.dict(sys.modules, {"crawl4ai.async_database": SimpleNamespace(DB_PATH=str(Path(temp) / "old" / ".crawl4ai" / "crawl4ai.db"))}):
            with self.assertRaisesRegex(ValueError, "CRAWL_CACHE_RESTART_REQUIRED"):
                transport.prepare_crawl_cache(Path(temp) / "data")


class VerificationIsolationTests(unittest.TestCase):
    def test_verify_build_cli_never_loads_credentials(self):
        import io
        import research_credentials
        from contextlib import redirect_stdout
        with patch.object(research_credentials, "load_into_environment") as loader, \
                patch.object(runtime, "verify_build", return_value={"ok": True}), redirect_stdout(io.StringIO()):
            self.assertEqual(0, runtime.main(["verify-build", "--output", "/unused-output"]))
        loader.assert_not_called()

    def test_actual_child_test_process_has_no_inherited_provider_keys(self):
        import subprocess
        from types import SimpleNamespace
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            plugin, output = base / "fixture-plugin", base / "verification"
            (plugin / "tests").mkdir(parents=True)
            (plugin / "scripts").mkdir()
            (plugin / "scripts" / "fixture.py").write_text("# no provider code\n")
            (plugin / "tests" / "test_environment.py").write_text('''import os, pathlib, unittest
class Environment(unittest.TestCase):
 def test_clean(self):
  for name in ("SERPER_API_KEY", "SERPAPI_API_KEY", "TINYFISH_API_KEY", "TYPESAFE_API_KEY", "JEV_API_KEY", "CRAWL4AI_API_TOKEN", "MARKETOPS_TEST_SECRET"):
   self.assertNotIn(name, os.environ)
  self.assertEqual("0", os.environ["MARKETOPS_RESEARCH_ALLOW_NETWORK"])
  self.assertEqual("0", os.environ["MARKETOPS_SEARCH_ALLOW_NETWORK"])
  self.assertEqual("keyring.backends.null.Keyring", os.environ["PYTHON_KEYRING_BACKEND"])
  self.assertEqual("tmp", pathlib.Path(os.environ["TMPDIR"]).name)
  self.assertTrue(pathlib.Path(os.environ["TMPDIR"]).is_absolute())
''')
            real_run = subprocess.run
            def command(args, **kwargs):
                # Only revision discovery is synthetic. The child unittest
                # process above actually executes with verify_build's env.
                if args == ["git", "rev-parse", "HEAD"]:
                    return SimpleNamespace(stdout="a" * 40)
                return real_run(args, **kwargs)
            external = {key: "synthetic-not-a-real-key" for key in (
                "SERPER_API_KEY", "SERPAPI_API_KEY", "TINYFISH_API_KEY", "TYPESAFE_API_KEY",
                "JEV_API_KEY", "CRAWL4AI_API_TOKEN", "MARKETOPS_TEST_SECRET")}
            external["MARKETOPS_RESEARCH_ALLOW_NETWORK"] = "1"
            with patch.dict(os.environ, external), patch.object(runtime.subprocess, "run", command):
                result = runtime.verify_build(output, plugin=plugin)
            self.assertTrue(result["ok"], (output / "unittest.log").read_text())
            self.assertEqual(1, result["tests_run"])


if __name__ == "__main__":
    unittest.main()
