"""Crawl4AI broken on this computer is found, said, and repairable; never a silent switch."""
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import research_crawl_doctor as doctor  # noqa: E402
import research_crawl4ai_live_local as live_local  # noqa: E402
import research_fallback as fallback  # noqa: E402
import research_provider_health as health  # noqa: E402

READY = {"crawl4ai_importable": True, "safe_egress_importable": True,
         "research_egress_importable": True, "chromium_present": True, "chromium_path": "/x"}


class Check(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)

    def run_check(self, state=READY, launch=(True, ""), page_read=None):
        with patch.object(live_local, "dependency_state", return_value=state), \
                patch.object(doctor, "_launch_probe", return_value=launch):
            return doctor.check(self.root, page_read=page_read)

    def test_missing_packages_and_browser_name_the_step_and_the_repair(self):
        result = self.run_check({**READY, "crawl4ai_importable": False})
        self.assertEqual(("NOT_WORKING", "PACKAGES_MISSING"), (result["status"], result["failed_step"]))
        self.assertIn("repair=True", result["repair"])
        result = self.run_check({**READY, "chromium_present": False})
        self.assertEqual("BROWSER_MISSING", result["failed_step"])

    def test_a_browser_that_will_not_start_marks_crawl4ai_down_for_the_computer(self):
        result = self.run_check(launch=(False, "spawn EACCES"))
        self.assertEqual("BROWSER_WONT_START", result["failed_step"])
        entry = health.blocked(self.root, "crawl4ai")
        self.assertEqual("LOCAL_DOWN", entry["state"])
        answer = health.refusal("crawl4ai", "crawl4ai.fetch_page", entry)
        self.assertEqual("CRAWL4AI_LOCAL_NOT_INSTALLED", answer["reason_code"])
        self.assertIn("marketops_crawl4ai_check", answer["payload"]["detail"])
        self.assertEqual("NEXT", fallback._decision(answer, {"task_kind": "fetch"})[0])

    def test_working_clears_the_mark_and_is_cached(self):
        self.run_check(launch=(False, "x"))
        result = self.run_check(page_read=lambda: (True, ""))
        self.assertEqual("WORKING", result["status"])
        self.assertIsNone(health.blocked(self.root, "crawl4ai"))
        self.assertEqual("WORKING", doctor.cached(self.root)["status"])

    def test_a_failed_test_page_is_its_own_step(self):
        result = self.run_check(page_read=lambda: (False, "LOCAL_CRAWL_RUNTIME_FAILED"))
        self.assertEqual("PAGE_READ_FAILED", result["failed_step"])

    def test_browser_start_alone_does_not_claim_page_read_verified(self):
        result = self.run_check()
        self.assertTrue(result["browser_verified"])
        self.assertFalse(result["page_read_verified"])
        self.assertEqual("NOT_REQUESTED", result["page_read_status"])

    def test_initialized_wrong_sdk_cache_requires_restart_not_browser_reinstall(self):
        fake = SimpleNamespace(DB_PATH=str(self.root / "wrong" / "crawl4ai.db"))
        with patch.dict(sys.modules, {"crawl4ai.async_database": fake}):
            result = self.run_check()
        self.assertEqual("CACHE_RESTART_REQUIRED", result["failed_step"])
        self.assertFalse(result["browser_verified"])
        self.assertFalse(result["page_read_verified"])
        self.assertIn("Restart", result["fix"])
        self.assertNotIn("repair=True", result["repair"])

    def test_policy_skipped_page_is_explicit_and_does_not_mark_browser_down(self):
        result = self.run_check(page_read=lambda: None)
        self.assertEqual("WORKING", result["status"])
        self.assertEqual("SKIPPED_POLICY", result["page_read_status"])
        self.assertFalse(result["page_read_verified"])
        self.assertIsNone(health.blocked(self.root, "crawl4ai"))

    def test_mcp_page_unavailable_is_a_failed_page_check(self):
        import research_mcp as mcp
        with patch.object(mcp, "root", return_value=self.root), \
                patch.object(mcp, "managed_call", return_value={"status": "BLOCKED", "reason_code": "CAPABILITY_NOT_AVAILABLE"}), \
                patch.object(live_local, "dependency_state", return_value=READY), \
                patch.object(doctor, "_launch_probe", return_value=(True, "")):
            result = mcp.crawl4ai_check(run_id="synthetic")
        self.assertEqual("NOT_WORKING", result["status"])
        self.assertEqual("PAGE_READ_FAILED", result["failed_step"])
        self.assertFalse(result["page_read_verified"])

    def test_status_does_not_import_sdk_or_create_cache_files(self):
        import research_mcp as mcp
        with patch.object(mcp, "root", return_value=self.root), \
                patch("research_credentials.load_into_environment"), \
                patch("research_egress.crawl4ai_ready", side_effect=AssertionError("premature SDK import")), \
                patch.object(mcp.importlib.util, "find_spec", return_value=SimpleNamespace()):
            result = mcp.provider_readiness()
        self.assertEqual("INSTALLED_NOT_CHECKED", result["providers"]["crawl4ai"])
        self.assertEqual([], list(self.root.iterdir()))

    def test_browser_only_cached_check_is_not_reported_as_full_ready(self):
        import research_mcp as mcp
        self.run_check()
        with patch.object(mcp, "root", return_value=self.root), \
                patch("research_credentials.load_into_environment"), \
                patch.object(mcp.importlib.util, "find_spec", return_value=SimpleNamespace()):
            result = mcp.provider_readiness()
        self.assertEqual("BROWSER_READY_PAGE_UNCHECKED", result["providers"]["crawl4ai"])

    def test_another_process_success_cannot_hide_this_process_cache_conflict(self):
        import research_mcp as mcp
        self.run_check(page_read=lambda: (True, ""))
        fake = SimpleNamespace(DB_PATH=str(self.root / "old" / "crawl4ai.db"))
        with patch.object(mcp, "root", return_value=self.root), \
                patch("research_credentials.load_into_environment"), \
                patch.object(mcp.importlib.util, "find_spec", return_value=SimpleNamespace()), \
                patch.dict(sys.modules, {"crawl4ai.async_database": fake}):
            result = mcp.provider_readiness()
        self.assertEqual("RESTART_REQUIRED", result["providers"]["crawl4ai"])


class Counter(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)

    def note(self, code, launch=(False, "dead")):
        with patch.object(live_local, "dependency_state", return_value=READY), \
                patch.object(doctor, "_launch_probe", return_value=launch):
            return doctor.note_local_result(self.root, ok=False, reason_code=code)

    def test_three_runtime_failures_in_a_row_with_a_dead_browser_mark_it_down(self):
        self.assertFalse(self.note("LOCAL_CRAWL_RUNTIME_FAILED"))
        self.assertFalse(self.note("LOCAL_CRAWL_RUNTIME_FAILED"))
        self.assertTrue(self.note("LOCAL_CRAWL_RUNTIME_FAILED"))
        self.assertIsNotNone(health.blocked(self.root, "crawl4ai"))

    def test_site_refusals_and_successes_do_not_count(self):
        for _ in range(5):
            self.assertFalse(self.note("SOURCE_ACCESS_BLOCKED"))
        self.note("LOCAL_CRAWL_RUNTIME_FAILED")
        self.note("LOCAL_CRAWL_RUNTIME_FAILED")
        doctor.note_local_result(self.root, ok=True)
        self.assertFalse(self.note("LOCAL_CRAWL_RUNTIME_FAILED"))
        self.assertIsNone(health.blocked(self.root, "crawl4ai"))

    def test_bad_sites_with_a_working_browser_do_not_mark_it_down(self):
        for _ in range(4):
            self.assertFalse(self.note("LOCAL_CRAWL_RUNTIME_FAILED", launch=(True, "")))
        self.assertIsNone(health.blocked(self.root, "crawl4ai"))


class Entrypoints(unittest.TestCase):
    def test_repair_scripts_sit_next_to_update(self):
        import release_layout
        with tempfile.TemporaryDirectory() as tmp:
            written = release_layout.write_update_entrypoints(Path(tmp), client="codex",
                                                              register_codex=False)
            names = {Path(p).name for p in written}
            self.assertTrue({"Repair.cmd", "repair.sh"} <= names)
            self.assertIn("research_crawl_doctor.py\" repair",
                          (Path(tmp) / "Repair.cmd").read_text(encoding="utf-8"))

    def test_an_update_adds_repair_to_an_older_install(self):
        import release_layout
        import update_marketops
        source = Path(update_marketops.__file__).read_text(encoding="utf-8")
        self.assertIn("release_layout.write_repair_entrypoints(root)", source)
        with tempfile.TemporaryDirectory() as tmp:
            release_layout.write_repair_entrypoints(Path(tmp))
            self.assertTrue((Path(tmp) / "repair.sh").is_file())


class Server(unittest.TestCase):
    def test_every_provider_with_a_key_is_also_reported(self):
        """The key table and the reported table must name the same providers.

        They were maintained apart, so DeepSeek could have a key variable and no
        line on the panel: a customer who never set ``DEEPSEEK_API_KEY`` saw
        nothing about it, and a missing route is indistinguishable from a working
        one that simply found nothing.
        """
        import research_mcp

        keyed = set(research_mcp.PROVIDER_KEYS)
        reported = set(research_mcp.READINESS_PROVIDERS)
        self.assertEqual(set(), keyed - reported, "有 key 的 provider 必须有读数")
        self.assertEqual({"crawl4ai"}, reported - keyed, "只有 crawl4ai 是不要 key 的那一个")

    def test_a_missing_deepseek_key_is_reported_rather_than_omitted(self):
        import research_mcp
        with tempfile.TemporaryDirectory() as tmp, \
                patch.object(research_mcp, "root", return_value=Path(tmp)), \
                patch("research_runtime_setup.config", return_value={}), \
                patch("research_credentials.load_into_environment"), \
                patch("research_balances.warnings", return_value=[]), \
                patch("research_provider_health.status", return_value={}), \
                patch.object(research_mcp, "configured", side_effect=lambda name: False):
            result = research_mcp.provider_readiness()
        self.assertIn("deepseek", result["providers"])
        self.assertEqual("KEY_MISSING", result["providers"]["deepseek"])

    def test_a_present_deepseek_key_follows_the_same_enablement_rule(self):
        import research_mcp
        with tempfile.TemporaryDirectory() as tmp, \
                patch.object(research_mcp, "root", return_value=Path(tmp)), \
                patch("research_runtime_setup.config", return_value={"providers": []}), \
                patch("research_credentials.load_into_environment"), \
                patch("research_balances.warnings", return_value=[]), \
                patch("research_provider_health.status", return_value={}), \
                patch.object(research_mcp, "configured", side_effect=lambda name: True):
            result = research_mcp.provider_readiness()
        # 钥匙在，但这份项目没有开任何一路：与其它 provider 同一条规则。
        self.assertEqual("NOT_ENABLED_FOR_PROJECT", result["providers"]["deepseek"])

    def test_readiness_shows_exhausted_local_budget_separately_from_provider_keys(self):
        import research_mcp
        with tempfile.TemporaryDirectory() as tmp, \
                patch.object(research_mcp, "root", return_value=Path(tmp)), \
                patch("research_runtime_setup.config", return_value={"max_calls": 200}), \
                patch("research_runtime_status.usage", return_value={"limit": 200, "calls_used": 200, "remaining": 0}), \
                patch("research_credentials.load_into_environment"), \
                patch("research_balances.warnings", return_value=[]), \
                patch("research_provider_health.status", return_value={}):
            result = research_mcp.provider_readiness()
        self.assertEqual(0, result["shared_request_budget"]["remaining"])
        self.assertEqual("LOCAL_REQUEST_LIMIT_EXHAUSTED", result["request_budget_warning"])

    def test_the_server_finds_the_browser_installed_beside_it(self):
        import research_mcp
        with tempfile.TemporaryDirectory() as tmp:
            browsers = Path(tmp) / "browsers"
            (browsers / "chromium-1187").mkdir(parents=True)
            fake = Path(tmp) / "plugins/p/scripts/research_mcp.py"
            with patch.object(research_mcp, "__file__", str(fake)), \
                    patch.dict("os.environ", {"PLAYWRIGHT_BROWSERS_PATH": "${PLAYWRIGHT_BROWSERS_PATH}"}):
                research_mcp._use_installed_browser()
                import os
                self.assertEqual(str(browsers.resolve()), os.environ["PLAYWRIGHT_BROWSERS_PATH"])


if __name__ == "__main__":
    unittest.main()
