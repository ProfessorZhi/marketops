"""Customer setup behavior; never use real credentials or a real keyring."""
from __future__ import annotations
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import setup_clients
import build_client_bundles


class CredentialTests(unittest.TestCase):
    def test_hidden_entry_and_restart_loader_share_native_store(self):
        import research_credentials as c
        values = {}
        class Store:
            def set_password(self, service, name, value): values[(service, name)] = value
            def get_password(self, service, name): return values.get((service, name))
        with patch.object(c, "_backend", return_value=Store()), patch.object(c.sys.stdin, "isatty", return_value=True), patch.object(
                c.getpass, "getpass", side_effect=["test-serper-value", "", "", ""]):
            receipt = c.configure_keys()
            env = {}
            loaded = c.load_into_environment(env)
        self.assertEqual("test-serper-value", env["SERPER_API_KEY"])
        self.assertNotIn("test-serper-value", json.dumps([receipt, loaded]))

    def test_environment_wins_but_unexpanded_placeholders_do_not(self):
        import research_credentials as c
        class Store:
            def get_password(self, service, name): return "stored-value"
        env = {"SERPER_API_KEY": "explicit-value", "SERPAPI_API_KEY": "${SERPAPI_API_KEY}"}
        with patch.object(c, "_backend", return_value=Store()):
            c.load_into_environment(env)
        self.assertEqual("explicit-value", env["SERPER_API_KEY"])
        self.assertEqual("stored-value", env["SERPAPI_API_KEY"])
        self.assertNotIn("CRAWL4AI_API_KEY", env)

    def test_missing_secure_backend_does_not_prompt_or_write(self):
        import research_credentials as c
        with patch.object(c, "_backend", side_effect=ValueError("OS_KEYRING_SECURE_BACKEND_UNAVAILABLE")), patch.object(c.getpass, "getpass") as prompt:
            with self.assertRaisesRegex(ValueError, "OS_KEYRING"):
                c.configure_keys()
            self.assertEqual({}, c.load_into_environment({})["loaded"])
        prompt.assert_not_called()

    def test_redirected_input_cannot_fall_back_to_echoed_key_entry(self):
        import research_credentials as c
        with patch.object(c, "_backend", return_value=object()), patch.object(c.sys.stdin, "isatty", return_value=False), patch.object(c.getpass, "getpass") as prompt:
            with self.assertRaisesRegex(ValueError, "INTERACTIVE_TERMINAL_REQUIRED"):
                c.configure_keys()
        prompt.assert_not_called()


class DeliveryTests(unittest.TestCase):
    def test_existing_scope_amendment_displays_usage_and_requires_local_confirmation(self):
        import contextlib
        import io
        import customer_setup as wizard
        import research_runtime_setup as runtime
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            runtime.configure(root, providers=["serper"], domains=["example.org"], max_calls=20,
                              task="previous task", confirm=True)
            output = io.StringIO()
            observed = {"calls_used": 7, "unknown_operations": 1, "reserved_operations": 2,
                        "actual_spend_micros": None, "money_status": "UNKNOWN"}
            with patch.object(wizard, "usage_snapshot", return_value=observed), patch("builtins.input", return_value="no"), contextlib.redirect_stdout(output):
                self.assertFalse(wizard.configure_research(root, providers=["serper"], domains=["example.org"], max_calls=30, task="next task"))
            self.assertEqual(20, runtime.config(root)["max_calls"])
            with patch.object(wizard, "usage_snapshot", return_value=observed), patch("builtins.input", return_value="AMEND"), contextlib.redirect_stdout(output):
                self.assertTrue(wizard.configure_research(root, providers=["serper"], domains=["example.org"], max_calls=30, task="next task"))
            self.assertEqual(30, runtime.config(root)["max_calls"])
            self.assertEqual(2, runtime.config(root)["revision"])
            for expected in ("20", "30", "7", "UNKNOWN", "1"):
                self.assertIn(expected, output.getvalue())

    def test_source_archive_is_portable_and_contains_complete_install_inputs(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = build_client_bundles.build_source(Path(tmp))
            package = Path(result["directory"])
            for name in ("Install.cmd", "Install.ps1", "install.sh", "START_HERE.md", "scripts/research_credentials.py", "scripts/setup_clients.py", ".codex-plugin/plugin.json", ".mcp.json"):
                self.assertTrue((package / name).is_file(), name)
            self.assertTrue(result["portable"])
            self.assertFalse((package / "tests").exists())
            self.assertNotIn(str(ROOT), (package / "mcp.json").read_text())
            self.assertTrue((package / "references/experience/method-seeds.json").is_file())
            from research_runtime_setup import source_manifest
            self.assertEqual(source_manifest(ROOT), source_manifest(package))

    def test_unsupported_python_is_rejected_before_writing(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(setup_clients.sys, "version_info", (3, 11, 9)):
            with self.assertRaisesRegex(ValueError, "Python 3.12"):
                setup_clients.plan(Path(tmp) / "new", "codex")

    def test_owned_partial_install_can_resume_after_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "Research Setup 中文"
            with patch.object(setup_clients.venv.EnvBuilder, "create", side_effect=OSError("interrupted")):
                with self.assertRaises(OSError):
                    setup_clients.install(target, "codex", dependencies=False, register_codex=False)
            self.assertTrue((target / "INSTALL_STATE.json").exists())
            result = setup_clients.install(target, "codex", dependencies=False, register_codex=False, resume=True)
            self.assertTrue(result["install_smoke_probe_ok"])
            again = setup_clients.install(target, "codex", dependencies=False, register_codex=False, resume=True)
            self.assertEqual(result["venv_python"], again["venv_python"])

    def test_resume_refuses_unowned_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp)
            sentinel = target / "customer.txt"
            sentinel.write_text("keep")
            with self.assertRaises((ValueError, FileExistsError)):
                setup_clients.install(target, "codex", dependencies=False, register_codex=False, resume=True)
            self.assertEqual("keep", sentinel.read_text())

    def test_codex_registration_performs_actual_install_and_quotes_start_command(self):
        import subprocess
        commands = []
        real_run = subprocess.run
        def run(argv, **kwargs):
            if argv[0] == "codex":
                commands.append(argv)
                return subprocess.CompletedProcess(argv, 0, stdout="{}", stderr="")
            return real_run(argv, **kwargs)
        with tempfile.TemporaryDirectory() as tmp, patch.object(setup_clients.shutil, "which", return_value="codex"), patch.object(setup_clients.subprocess, "run", side_effect=run):
            target = Path(tmp) / "Space's Install"
            result = setup_clients.install(target, "codex", dependencies=False, register_codex=True)
            self.assertEqual("INSTALLED_RESTART_HOST_TO_VERIFY", result["codex_plugin_install_status"])
            self.assertIn(["codex", "plugin", "add", "marketops-research-assistant@marketops-local", "--json"], commands)
            self.assertIn(str(target), json.dumps(result["codex_next_argv"]))
            self.assertIn("plugin add", (target / "START_HERE.md").read_text())

    def test_windows_lock_contains_platform_keyring_dependency(self):
        lock = ROOT / "requirements-lock-lite-windows.txt"
        self.assertIn("pywin32-ctypes==", lock.read_text())

    def test_connector_probe_reads_resumed_receipt_and_refuses_escape(self):
        import client_install_probe as probe
        with tempfile.TemporaryDirectory() as tmp:
            dest = Path(tmp)
            result = build_client_bundles.build(dest / "connectors/attempt-2", with_research_mcp=True)
            receipt = dest / "WORKBUDDY_BUNDLE.json"
            receipt.write_text(json.dumps(result))
            self.assertTrue(probe.audit_workbuddy_package(dest)["ok"])
            receipt.write_text(json.dumps({"directory": str(dest.parent)}))
            report = probe.audit_workbuddy_package(dest)
            self.assertFalse(report["ok"])
            self.assertEqual("CONNECTOR_RECEIPT_INVALID", report["findings"][0]["kind"])


if __name__ == "__main__":
    unittest.main()
