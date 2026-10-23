"""The doctor must answer about the interpreter the server uses, not the one running it.

Every case here is a failure that actually happened or is one setting away from
happening: a configured interpreter without ``crawl4ai`` (page reads fall through
to a paid route), a cached "WORKING" left by a *different* venv (the machine looks
ready because another interpreter was), no config at all (nowhere to write), and a
key that is absent but optional (a warning, not a stop).
"""
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import research_doctor as doctor  # noqa: E402


class Interpreter(unittest.TestCase):
    def test_a_missing_mcp_is_a_blocker_naming_that_interpreter(self):
        with patch.object(doctor, "module_available", return_value=False):
            lines = doctor.interpreter_findings(sys.executable)
        by_check = {line.check: line for line in lines}
        self.assertEqual(doctor.BLOCKER, by_check["interpreter.mcp"].state)
        self.assertIn(sys.executable, by_check["interpreter.mcp"].detail)

    def test_a_missing_reader_is_a_warning_not_a_stop(self):
        states = {"mcp": True, "crawl4ai": False, "playwright": True, "pip": True}
        with patch.object(doctor, "module_available", side_effect=lambda p, m: states[m]):
            lines = doctor.interpreter_findings(sys.executable)
        by_check = {line.check: line for line in lines}
        self.assertEqual(doctor.WARN, by_check["interpreter.crawl4ai"].state)
        self.assertNotIn("interpreter.mcp", by_check)

    def test_the_interpreter_to_probe_comes_from_the_same_variable_as_the_launcher(self):
        self.assertEqual("/x/python", doctor.configured_interpreter({doctor.INTERPRETER_ENV: "/x/python"}))
        self.assertEqual(sys.executable, doctor.configured_interpreter({}))
        self.assertEqual(sys.executable, doctor.configured_interpreter({doctor.INTERPRETER_ENV: "  "}))


class Installing(unittest.TestCase):
    """A repair command must be one the target interpreter can actually run."""

    def test_a_venv_without_pip_is_offered_uv_not_a_command_that_cannot_exist(self):
        # Measured: the configured interpreter here was uv-created and had no
        # pip at all, so `python -m pip install` was never going to work.
        states = {"mcp": True, "crawl4ai": False, "playwright": True, "pip": False}
        with patch.object(doctor, "module_available", side_effect=lambda p, m: states[m]), \
                patch.object(doctor.shutil, "which", return_value="/usr/bin/uv"):
            lines = doctor.interpreter_findings(sys.executable)
        crawl = next(line for line in lines if line.check == "interpreter.crawl4ai")
        self.assertIn("uv pip install", crawl.fix)
        self.assertNotIn("-m pip", crawl.fix)
        self.assertEqual("install", crawl.repair)

    def test_no_pip_and_no_uv_means_no_promise_to_install(self):
        with patch.object(doctor, "module_available", return_value=False), \
                patch.object(doctor.shutil, "which", return_value=None):
            lines = doctor.interpreter_findings(sys.executable)
        crawl = next(line for line in lines if line.check == "interpreter.crawl4ai")
        self.assertIsNone(crawl.repair, "不能承诺一个跑不通的修复")
        self.assertIn("既没有 pip", crawl.fix)

    def test_installing_playwright_does_not_claim_the_browser_came_with_it(self):
        with patch.object(doctor, "module_available", return_value=True):
            done = doctor._pip_install(sys.executable, [])
        self.assertFalse(any("playwright install chromium" in line for line in done))
        with patch.object(doctor, "module_available", return_value=True), \
                patch.object(doctor.subprocess, "run") as run:
            run.return_value.returncode = 0
            done = doctor._pip_install(sys.executable, ["playwright"])
        self.assertTrue(any("playwright install chromium" in line for line in done))


class Keys(unittest.TestCase):
    def test_key_names_are_reported_and_values_never_are(self):
        secret = "sk-do-not-print-me-123456"
        lines = doctor.key_findings({"SERPER_API_KEY": secret})
        body = " ".join(line.detail + line.fix for line in lines)
        self.assertIn("serper", body)
        self.assertNotIn(secret, body)

    def test_no_keys_is_a_warning(self):
        lines = doctor.key_findings({})
        self.assertTrue(all(line.state == doctor.WARN for line in lines))


class Config(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)

    def test_an_unconfigured_machine_is_a_blocker_pointing_at_setup(self):
        with patch("research_config.status", return_value={
                "configured": False, "config_file": str(self.root / "config.json")}):
            lines = doctor.config_findings({})
        self.assertEqual(doctor.BLOCKER, lines[0].state)
        self.assertIn("marketops", lines[0].fix)

    def test_a_missing_data_root_asks_for_a_safe_repair(self):
        with patch("research_config.status", return_value={
                "configured": True, "config_file": str(self.root / "config.json"),
                "data_root": str(self.root / "data")}):
            lines = doctor.config_findings({})
        self.assertEqual("create_data_root", lines[0].repair)
        self.assertFalse((self.root / "data").exists(), "读的时候不写盘")


class Reader(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)

    def test_a_cached_working_from_another_interpreter_is_not_reported_as_ready(self):
        """The trap: the cache lives in the data root, the verdict is per-venv."""
        with patch("research_crawl_doctor.cached",
                   return_value={"status": "WORKING", "page_read_verified": True}):
            lines = doctor.reader_findings(str(self.root), reader_available=False)
        self.assertEqual(doctor.WARN, lines[0].state)
        self.assertIn("另一个", lines[0].detail)

    def test_a_cached_working_in_the_same_interpreter_is_ok(self):
        with patch("research_crawl_doctor.cached",
                   return_value={"status": "WORKING", "page_read_verified": True}):
            lines = doctor.reader_findings(str(self.root), reader_available=True)
        self.assertEqual(doctor.OK, lines[0].state)


class Report(unittest.TestCase):
    def test_blockers_drive_the_state_and_the_exit_code(self):
        report = doctor.Report(interpreter="/x/python")
        report.add(doctor.Finding("a", doctor.OK, ""), doctor.Finding("b", doctor.BLOCKER, ""))
        self.assertEqual("BLOCKED", report.payload()["state"])
        self.assertEqual(["b"], report.payload()["blockers"])

    def test_repair_without_install_names_the_command_instead_of_running_it(self):
        report = doctor.Report(interpreter="/x/python")
        report.add(doctor.Finding("interpreter.crawl4ai", doctor.WARN, "", repair="install"))
        with patch.object(doctor, "module_available", return_value=False), \
                patch.object(doctor, "_pip_install") as pip:
            done = doctor.repair(report, install=False)
        pip.assert_not_called()
        self.assertTrue(any("--install" in line for line in done))


if __name__ == "__main__":
    unittest.main()
