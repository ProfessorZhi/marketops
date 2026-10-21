"""Crawl4AI broken on this computer is found, said, and repairable; never a silent switch."""
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

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
