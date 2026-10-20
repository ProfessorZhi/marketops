"""No-network installation and metadata conformance tests."""
from __future__ import annotations
import contextlib
import importlib.util
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import setup_clients as install
import build_client_bundles as bundles


class RefusalReportingTests(unittest.TestCase):
    """A refused install must say what to do next, and never quote a provider."""

    @staticmethod
    def _blocked(exc):
        buffer = io.StringIO()
        argv = ["setup_clients.py", "--destination", "/tmp/marketops-unused",
                "--client", "claude"]
        with mock.patch.object(install, "plan", side_effect=exc),                 mock.patch.object(sys, "argv", argv),                 contextlib.redirect_stderr(buffer):
            status = install.main()
        lines = [line for line in buffer.getvalue().splitlines() if line.startswith("{")]
        return status, json.loads(lines[-1]), buffer.getvalue()

    def test_a_missing_venv_component_names_the_component_to_install(self):
        # A stock Ubuntu 24.04 has python3.12 without ensurepip; the old output was
        # a bare code, which tells the customer nothing they can act on.
        status, report, text = self._blocked(
            ValueError("PYTHON_ENSUREPIP_UNAVAILABLE: install the Python 3.12 venv component"))
        self.assertEqual(2, status)
        self.assertEqual("PYTHON_ENSUREPIP_UNAVAILABLE", report["code"])
        self.assertIn("python3.12-venv", report["detail"])
        self.assertIn("python3.12-venv", text, "the console line must carry it too")

    def test_the_venv_check_raises_a_code_the_report_can_match(self):
        with tempfile.TemporaryDirectory() as tmp,                 mock.patch.object(importlib.util, "find_spec", return_value=None):
            with self.assertRaises(ValueError) as caught:
                install.plan(Path(tmp) / "app", "claude")
        code = str(caught.exception).split(":", 1)[0]
        self.assertEqual("PYTHON_ENSUREPIP_UNAVAILABLE", code)
        self.assertIn(code, install.REMEDIES)

    def test_an_occupied_destination_is_a_code_rather_than_a_sentence(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "app"
            target.mkdir()
            (target / "keep.txt").write_text("x", encoding="utf-8")
            with self.assertRaises(FileExistsError) as caught:
                install.plan(target, "claude")
        self.assertEqual("INSTALL_TARGET_NOT_OWNED", str(caught.exception))

    def test_a_message_that_is_not_a_code_is_not_reported_as_one(self):
        status, report, _ = self._blocked(ValueError("Requires Python 3.11"))
        self.assertEqual(2, status)
        self.assertEqual("INSTALL_CONFIGURATION_INVALID", report["code"])
        self.assertNotIn("detail", report)

    def test_an_unknown_code_carries_no_exception_text(self):
        status, report, text = self._blocked(ValueError("PROVIDER_SAID: token=sk-live-9f"))
        self.assertEqual(2, status)
        self.assertEqual("PROVIDER_SAID", report["code"])
        self.assertNotIn("sk-live-9f", text, "command and provider text is never echoed")
        self.assertNotIn("detail", report)


class StartHereGuideTests(unittest.TestCase):
    """The post-install guide must read as steps, whichever clients were set up."""

    @staticmethod
    def _numbers(text):
        return [line.split(".")[0][3:] for line in text.splitlines()
                if line.startswith("## ") and line[3:4].isdigit()]

    def _guide(self, **extra):
        result = {"customer_setup_argv": ["python", "customer_setup.py"],
                  "codex_next_argv": ["codex"], "codex_install_argv": ["codex", "plugin"]} | extra
        return install._start_here(result)

    def test_a_claude_only_install_does_not_skip_a_number(self):
        # A customer who installs for Claude Code alone has no Codex or WorkBuddy
        # block; the old literal headings went 1 -> 4.
        text = self._guide(claude_next_argv=["claude"])
        self.assertEqual(["1", "2"], self._numbers(text))
        self.assertIn("## 2. Claude Code", text)

    def test_b_every_client_present_is_numbered_in_order(self):
        text = self._guide(claude_next_argv=["claude"],
                           codex_marketplace={"root": "x"},
                           workbuddy_bundle={"directory": "/tmp/out"})
        self.assertEqual(["1", "2", "3", "4"], self._numbers(text))

    def test_c_a_codex_only_install_numbers_the_codex_step_two(self):
        text = self._guide(codex_marketplace={"root": "x"})
        self.assertEqual(["1", "2"], self._numbers(text))
        self.assertIn("## 2. Codex", text)


class LocalInstallTests(unittest.TestCase):
    def test_dry_run_creates_nothing(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "new-install"
            result = install.plan(target, "both")
            self.assertTrue(result["no_live_provider_calls"])
            self.assertIsNone(result["customer_data_root"])
            self.assertIsNone(result["experience_file"])
            self.assertEqual("USE_EXISTING_CONFIG_OR_ASK_TWO_PATHS_ON_FIRST_USE", result["storage_configuration"])
            self.assertFalse(target.exists())

    def test_install_prepared_mcp_without_keys(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "marketops-install"
            result = install.install(target, "both", dependencies=False, register_codex=False)
            self.assertEqual("SOURCE_INSTALLED_UNVERIFIED", result["status"])
            self.assertFalse(result["codex_marketplace_registered"])
            source = Path(result["plugin_copy"])
            self.assertTrue((source / "skills" / "marketops-setup" / "SKILL.md").exists())
            manifest = json.loads((source / "plugin.json").read_text(encoding="utf-8"))
            self.assertEqual("0.5.6-rc5", manifest["version"])
            for filename in ("mcp.json", ".mcp.json"):
                config = json.loads((source / filename).read_text(encoding="utf-8"))
                server = next(iter((config.get("mcpServers") or config.get("mcp_servers")).values()))
                self.assertEqual(result["venv_python"], server["command"])
                # Both host configs must land on the same managed entrypoint.
                self.assertEqual(str(source / "scripts" / "research_mcp.py"),
                                 server["args"][0])
                self.assertNotIn("cwd", server, "Codex cached plugin root must not inherit external cwd")
            wb = result["workbuddy_bundle"]
            self.assertEqual(1, wb["mcp_servers_bundled"])
            self.assertEqual(17, wb["skills"])
            connector = Path(wb["directory"])
            wmeta = json.loads((connector / "connector-meta.json").read_text(encoding="utf-8"))
            self.assertEqual("mcp", wmeta["type"])
            self.assertEqual("token", wmeta["auth_mode"])
            self.assertTrue((connector / "scripts" / "research_provider_policy.py").exists())
            self.assertTrue((connector / "scripts" / "research_mcp.py").exists())
            self.assertTrue((connector / "scripts" / "build_client_bundles.py").exists(),
                            "Packaged setup_clients must not reference an absent builder")
            token = json.loads((connector / "token-schema.json").read_text(encoding="utf-8"))
            keys = {x["key"] for x in token["fields"]}
            for key in ("SERPAPI_API_KEY", "SERPER_API_KEY", "CRAWL4AI_API_KEY",
                        "TINYFISH_API_KEY", "TYPESAFE_API_KEY"):
                self.assertIn(key, keys)
            self.assertFalse((source / "data").exists())
            self.assertFalse((target / "data").exists(), "Installer must not create an unchosen data root")
            self.assertIsNone(result["customer_data_root"])
            self.assertIsNone(result["experience_file"])
            with self.assertRaises(FileExistsError):
                install.plan(target, "both")

    def test_bundle_default_still_skill_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            receipt = bundles.build(Path(tmp) / "bundle")
            self.assertEqual(0, receipt["mcp_servers_bundled"])
            self.assertEqual(17, receipt["skills"])
            self.assertEqual(17, len(list((ROOT / "skills").glob("*/SKILL.md"))),
                             "bundle Skill count must track the plugin source Skills")


if __name__ == "__main__":
    unittest.main()
