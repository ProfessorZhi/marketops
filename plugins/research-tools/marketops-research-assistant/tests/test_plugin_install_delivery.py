"""Customer setup behavior; never use real credentials or a real keyring."""
from __future__ import annotations
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from contextlib import ExitStack
from unittest.mock import patch

try:
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
except ImportError:  # pragma: no cover - the e2e venv ships cryptography
    Ed25519PrivateKey = None

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

    def test_agent_supplied_keys_reach_the_native_store_without_a_terminal(self):
        import research_credentials as c
        stored = {}

        class Store:
            def set_password(self, service, name, value): stored[(service, name)] = value
            def get_password(self, service, name): return stored.get((service, name))

        # Empty and absent entries must stay untouched: a user may have only some accounts.
        with patch.object(c, "_backend", return_value=Store()):
            receipt = c.configure_keys_from_values(
                {"serper": "test-serper-value", "serpapi": "", "tinyfish": None})
            env = {}
            loaded = c.load_into_environment(env)
        self.assertEqual({(c.KEYRING_SERVICE, "SERPER_API_KEY"): "test-serper-value"}, stored)
        self.assertEqual("UNCHANGED", receipt["providers"]["serpapi"])
        self.assertEqual("UNCHANGED", receipt["providers"]["typesafe"])
        self.assertEqual("test-serper-value", env["SERPER_API_KEY"])
        self.assertNotIn("test-serper-value", json.dumps([receipt, loaded]))
        self.assertFalse(receipt["secret_values_returned"])

    def test_agent_supplied_keys_reject_placeholders_and_control_characters(self):
        import research_credentials as c

        class Store:
            def set_password(self, *args): raise AssertionError("must not write an invalid key")

        with patch.object(c, "_backend", return_value=Store()):
            newlines = "line1" + chr(10) + "line2"
            nul = "zero" + chr(0) + "byte"
            for bad in ("   ", "${SERPER_API_KEY}", newlines, nul, 42, ["x"]):
                with self.assertRaisesRegex(ValueError, "INVALID_API_KEY_INPUT"):
                    c.configure_keys_from_values({"serper": bad}, ["serper"])

    def test_stdin_mode_keeps_every_key_out_of_the_command_line(self):
        import research_credentials as c
        stored = {}

        class Store:
            def set_password(self, service, name, value): stored[name] = value

        out = io.StringIO()
        with ExitStack() as stack:
            stack.enter_context(patch.object(c, "_backend", return_value=Store()))
            stack.enter_context(patch.object(c.sys, "stdin", io.StringIO('{"serper": "test-serper-value"}')))
            stack.enter_context(patch.object(c.sys, "stdout", out))
            code = c.main(["configure", "--stdin"])
        self.assertEqual(0, code)
        self.assertEqual({"SERPER_API_KEY": "test-serper-value"}, stored)
        self.assertNotIn("test-serper-value", out.getvalue())

    def test_stdin_mode_rejects_anything_that_is_not_a_json_object(self):
        import research_credentials as c

        class Store:
            def set_password(self, *args): raise AssertionError("must not write")

        for payload in ("", "not json", '["serper"]'):
            with ExitStack() as stack:
                stack.enter_context(patch.object(c, "_backend", return_value=Store()))
                stack.enter_context(patch.object(c.sys, "stdin", io.StringIO(payload)))
                stack.enter_context(patch.object(c.sys, "stderr", io.StringIO()))
                self.assertEqual(2, c.main(["configure", "--stdin"]))


class DeliveryTests(unittest.TestCase):
    def test_source_package_verifies_external_qa_receipt_before_creating_archive(self):
        import research_runtime_setup as runtime
        # Synthetic fixture exercises the hash contract only. It is never
        # published or presented as actual release QA evidence.
        receipt = {"schema": "marketops.build-qa.v1", "source_revision": "a" * 40,
                   "files": runtime.source_manifest(ROOT), "unit_test_fixture": True,
                   "test": {"returncode": 0, "tests_run": 1, "suite": "plugin-unittest-discovery",
                            "log_sha256": "b" * 64}}
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            qa = base / "external-qa.json"
            qa.write_text(json.dumps(receipt), encoding="utf-8")
            result = build_client_bundles.build_source(base / "valid", qa_receipt=qa)
            # Matching the bytes is not an independent QA pass. A receipt the
            # same tree can generate stays a preview until an external signer
            # attests it.
            self.assertFalse(result["qa_verified"])
            self.assertEqual("SOURCE_PACKAGE_PREVIEW_BUILT", result["status"])
            self.assertEqual("INDEPENDENT_QA_ATTESTATION_MISSING",
                             result["qa_attestation"]["reason"])
            copied = Path(result["directory"]) / "references/BUILD_QA.json"
            self.assertEqual(qa.read_bytes(), copied.read_bytes())
            self.assertTrue(runtime.verify_build_receipt(Path(result["directory"]))["ok"])

            if Ed25519PrivateKey is not None:
                key = Ed25519PrivateKey.generate()
                block = {"schema": "marketops.independent-qa.v1",
                         "signed_by": "synthetic-independent-reviewer",
                         "source_revision": "a" * 40,
                         "log_sha256": "b" * 64, "tests_run": 1}
                payload = json.dumps(block, sort_keys=True, separators=(",", ":"),
                                     ensure_ascii=True).encode("utf-8")
                block["signature"] = key.sign(payload).hex()
                receipt["independent_qa"] = block
                qa.write_text(json.dumps(receipt), encoding="utf-8")
                public_hex = key.public_key().public_bytes(
                    Encoding.Raw, PublicFormat.Raw).hex()
                with patch.dict(os.environ, {"MARKETOPS_QA_PUBLIC_KEY": public_hex}):
                    signed = build_client_bundles.build_source(base / "signed",
                                                               qa_receipt=qa)
                self.assertTrue(signed["qa_verified"])
                self.assertTrue(signed["qa_attestation"]["verified"])
                self.assertEqual("synthetic-independent-reviewer",
                                 signed["qa_attestation"]["signed_by"])
                # Restore the unsigned fixture for the stale/invalid cases below.
                receipt.pop("independent_qa", None)
                qa.write_text(json.dumps(receipt), encoding="utf-8")
            receipt["files"][next(iter(receipt["files"]))] = "0" * 64
            qa.write_text(json.dumps(receipt), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "BUILD_QA_STALE_OR_INVALID"):
                build_client_bundles.build_source(base / "stale", qa_receipt=qa)
            self.assertFalse(list((base / "stale").glob("*.zip")))
            receipt["schema"] = "wrong"
            qa.write_text(json.dumps(receipt), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "BUILD_QA_STALE_OR_INVALID"):
                build_client_bundles.build_source(base / "wrong", qa_receipt=qa)
            self.assertFalse(list((base / "wrong").glob("*.zip")))

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
            self.assertFalse(result["qa_verified"])
            self.assertEqual("SOURCE_PACKAGE_PREVIEW_BUILT", result["status"])
            self.assertFalse((package / "references/BUILD_QA.json").exists())
            self.assertFalse((package / "tests").exists())
            self.assertNotIn(str(ROOT), (package / "mcp.json").read_text())
            self.assertTrue((package / "references/experience/method-seeds.json").is_file())
            from research_runtime_setup import source_manifest
            self.assertEqual(source_manifest(ROOT), source_manifest(package))

    def test_unsupported_python_is_rejected_before_writing(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(setup_clients.sys, "version_info", (3, 11, 9)):
            with self.assertRaisesRegex(ValueError, "PYTHON_312_64BIT_REQUIRED") as caught:
                setup_clients.plan(Path(tmp) / "new", "codex")
        self.assertIn("PYTHON_312_64BIT_REQUIRED", setup_clients.REMEDIES)
        self.assertIn("Python 3.12", setup_clients.REMEDIES["PYTHON_312_64BIT_REQUIRED"])

    def test_an_overlapping_destination_is_a_code_the_report_can_carry(self):
        with self.assertRaisesRegex(ValueError, "DESTINATION_OVERLAPS_SOURCE"):
            setup_clients.plan(setup_clients.SOURCE / "app", "codex")

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
