"""First-run vendor enrollment stays self-contained, honest and offline.

No provider authentication, no paid APIs, no live HTTP or customer data.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import provider_onboarding as onboard
import setup_clients
import build_client_bundles


class ProviderOnboardingTests(unittest.TestCase):
    def test_canonical_five_provider_catalog_is_packaged_without_credentials(self):
        catalog = onboard.read_catalog()
        self.assertEqual("marketops.provider-setup.v1", catalog["schema"])
        rows = catalog["providers"]
        self.assertEqual({"tinyfish", "serpapi", "serper", "crawl4ai", "jev"},
                         {p["id"] for p in rows})
        self.assertTrue(all(p["account_url"].startswith("https://") for p in rows))
        self.assertEqual("NO_OFFICIAL_VENDOR_MCP_VERIFIED",
                         next(p for p in rows if p["id"] == "serper")[
                             "optional_official_mcp"]["state"])
        self.assertFalse(catalog["default_policy"]["paid_provider_calls_enabled"])
        self.assertFalse(catalog["default_policy"]["import_official_mcp"])
        self.assertTrue(all(p.get("embedded_actual_mcp_client") for p in rows
                            if p["id"] in {"tinyfish", "serpapi"}))

    def test_local_guide_never_echoes_secrets_and_does_not_claim_login(self):
        secret = "test-secret-value-PROVIDER-7b95c"
        env = {name: secret for name in (
            "SERPAPI_API_KEY", "SERPER_API_KEY", "TINYFISH_API_KEY",
            "CRAWL4AI_API_KEY", "TYPESAFE_API_KEY")}
        data = onboard.report(client="claude", environ=env)
        text = json.dumps(data, ensure_ascii=False) + onboard.format_text(data)
        self.assertNotIn(secret, text)
        self.assertEqual("GUIDE_ONLY_NO_NETWORK_OR_AUTH", data["status"])
        self.assertEqual(5, data["provider_count"])
        self.assertFalse(data["account_registration_performed"])
        self.assertFalse(data["host_mcp_registration_performed"])
        self.assertFalse(data["provider_fees_known"])
        for row in data["providers"]:
            self.assertEqual("CONFIGURED_NOT_AUTHENTICATED",
                             row["lite"]["credential_status"])
            self.assertEqual("AUTH_NOT_VERIFIED",
                             row["official_mcp"]["installed_and_authenticated"])
            self.assertFalse(row["official_mcp"]["billing_verified"])
        self.assertEqual("NOT_CONFIGURED", onboard._env_flag(
            "SERPAPI_API_KEY", {"SERPAPI_API_KEY": "${SERPAPI_API_KEY}"}))

    def test_client_specific_mcp_steps_and_subset(self):
        tinyfish = onboard.report(provider="tinyfish", client="claude")
        self.assertEqual(1, tinyfish["provider_count"])
        commands = tinyfish["providers"][0]["official_mcp"]["client_steps"]
        self.assertTrue(any("MarketOps" in s for s in commands))
        self.assertTrue(any("research_official_mcp_auth.py" in s for s in commands))
        codex = onboard.report(provider="serpapi", client="codex")
        self.assertTrue(any("SERPAPI_API_KEY" in s for s
                            in codex["providers"][0]["official_mcp"]["client_steps"]))
        wb = onboard.report(provider="serper", client="workbuddy")
        self.assertEqual([], wb["providers"][0]["official_mcp"]["client_steps"])
        self.assertFalse(wb["local_runtime"]["browser_launch_verified"])
        with self.assertRaisesRegex(ValueError, "UNKNOWN_PROVIDER"):
            onboard.report(provider="other-vendor")
        with self.assertRaisesRegex(ValueError, "UNSUPPORTED_SETUP_CLIENT"):
            onboard.report(client="unknown")

    def test_cli_stdout_is_useful_without_network_or_secret_values(self):
        secret = "test-secret-value-CLI-ec1ed"
        env = dict(os.environ)
        env["SERPER_API_KEY"] = secret
        run = subprocess.run(
            [sys.executable, str(ROOT / "scripts" / "provider_onboarding.py"),
             "--provider", "serper", "--client", "codex", "--json"],
            env=env, capture_output=True, text=True, check=True)
        data = json.loads(run.stdout)
        self.assertEqual(1, data["provider_count"])
        self.assertNotIn(secret, run.stdout)
        self.assertEqual("CONFIGURED_NOT_AUTHENTICATED",
                         data["providers"][0]["lite"]["credential_status"])

    def test_source_installer_writes_a_user_entry_and_preserves_no_auth(self):
        with tempfile.TemporaryDirectory() as tmp:
            dest = Path(tmp) / "marketops-install"
            result = setup_clients.install(dest, "claude", dependencies=False,
                                           register_codex=False)
            self.assertEqual("SOURCE_INSTALLED_UNVERIFIED", result["status"])
            self.assertTrue((dest / "START_HERE.md").is_file())
            self.assertTrue((dest / "plugins" / "marketops-research-assistant" /
                             "scripts" / "provider_onboarding.py").is_file())
            self.assertEqual("USER_START_REQUIRED_NOT_AUTOMATIC",
                             result["claude_host_registration"])
            self.assertEqual(0, result["paid_provider_calls"])
            self.assertFalse(result["provider_login_performed"])
            self.assertFalse(result["dependencies_installed"])
            self.assertFalse((dest / "data").exists())
            self.assertEqual("claude", result["provider_onboarding_command"][-1])

    def test_installation_permission_validation_precedes_file_creation(self):
        with tempfile.TemporaryDirectory() as tmp:
            dest = Path(tmp) / "untouched"
            with self.assertRaisesRegex(ValueError, "BROWSER_REQUIRES_LOCAL_CRAWL"):
                setup_clients.install(dest, "both", dependencies=True,
                                      register_codex=False, browser=True)
            with self.assertRaisesRegex(ValueError, "LOCAL_CRAWL_REQUIRES_RUNTIME_DEPENDENCIES"):
                setup_clients.install(dest, "both", dependencies=False,
                                      register_codex=False, local_crawl=True)
            self.assertFalse(dest.exists())
        with self.assertRaisesRegex(ValueError, "UNSUPPORTED_SETUP_CLIENT"):
            setup_clients.plan(Path(tempfile.gettempdir()) / "zz", "not-a-client")

    def test_bootstrap_runtime_cannot_download_anything_in_a_dry_run(self):
        with tempfile.TemporaryDirectory() as tmp:
            dest = Path(tmp) / "still-empty"
            result = subprocess.run(
                [sys.executable, str(ROOT / "scripts" / "setup_clients.py"),
                 "--destination", str(dest), "--client", "all", "--bootstrap-runtime"],
                text=True, capture_output=True)
            self.assertNotEqual(0, result.returncode)
            self.assertFalse(dest.exists(), "Missing --apply must not create files")
            self.assertIn("require --apply", result.stderr)

    def test_all_clients_receive_combined_setup_without_vendor_login(self):
        with tempfile.TemporaryDirectory() as tmp:
            dest = Path(tmp) / "all-clients"
            receipt = setup_clients.install(dest, "all", dependencies=False,
                                            register_codex=False)
            self.assertIn("claude_next_argv", receipt)
            self.assertIn("codex_marketplace", receipt)
            self.assertIn("workbuddy_bundle", receipt)
            self.assertEqual({"claude", "codex", "workbuddy"},
                             set(receipt["provider_onboarding_commands"]))
            self.assertFalse(receipt["external_official_mcp_registered"])
            self.assertFalse(receipt["provider_login_performed"])
            self.assertTrue((dest / "START_HERE.md").is_file())
            self.assertTrue(Path(receipt["workbuddy_bundle"]["directory"]).is_dir())
            self.assertEqual("PENDING_HOST_IMPORT", receipt["workbuddy_import_status"])

    def test_private_browser_cache_is_bound_in_source_and_workbuddy_without_download(self):
        with tempfile.TemporaryDirectory() as tmp:
            dest = Path(tmp) / "browser-install"
            # User explicitly requested bootstrap, but fake subprocess prevents
            # all downloads: verify exact intended args and env only.
            with patch.object(setup_clients.subprocess, "run") as calls:
                receipt = setup_clients.install(
                    dest, "claude", dependencies=True, register_codex=False,
                    local_crawl=True, browser=True)
            self.assertTrue(receipt["chromium_download_requested"])
            self.assertFalse(receipt["browser_launch_verified"])
            browser = str(dest / "browsers")
            self.assertEqual(browser, receipt["browser_cache"])
            # Python venv.EnvBuilder(with_pip=True) may itself invoke
            # subprocess.run(..., '-m', 'ensurepip', ...). That is runtime
            # bootstrap, not an additional provider/package download. Count
            # our three explicit calls by argv instead of a global mock count.
            commands = [call.args[0] for call in calls.call_args_list]
            explicit_pip = [cmd for cmd in commands if
                            len(cmd) >= 4 and cmd[1:4] == ["-m", "pip", "install"]]
            chromium_calls = [call for call in calls.call_args_list
                              if len(call.args[0]) >= 4 and
                              call.args[0][1:4] == ["-m", "playwright", "install"]]
            self.assertEqual(2, len(explicit_pip))
            self.assertEqual(1, len(chromium_calls))
            chromium_call = chromium_calls[0]
            self.assertEqual(browser, chromium_call.kwargs["env"]["PLAYWRIGHT_BROWSERS_PATH"])
            plugin = Path(receipt["plugin_copy"])
            for name in ("mcp.json", ".mcp.json"):
                manifest = json.loads((plugin / name).read_text(encoding="utf-8"))
                server = manifest["mcpServers"]["marketops-research"]
                self.assertEqual(browser, server["env"]["PLAYWRIGHT_BROWSERS_PATH"])

            bundle = build_client_bundles.build(
                Path(tmp) / "bundle", with_research_mcp=True,
                browser_cache=dest / "browsers")
            self.assertTrue(bundle["browser_cache_configured"])
            w = Path(bundle["directory"])
            mcp = json.loads((w / "mcp.json").read_text(encoding="utf-8"))
            server = mcp["mcpServers"]["marketops-research"]
            self.assertEqual(browser, server["env"]["PLAYWRIGHT_BROWSERS_PATH"])

    def test_browser_cache_inside_plugin_source_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(ValueError, "BROWSER_CACHE_MUST_BE_OUTSIDE"):
                build_client_bundles.build(Path(tmp) / "bundle",
                    with_research_mcp=True, browser_cache=ROOT / ".cache" / "playwright")
            self.assertFalse((Path(tmp) / "bundle").exists())

    def test_workbuddy_bundle_contains_provider_setup_runtime_and_official_refs(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = build_client_bundles.build(
                Path(tmp) / "out", with_research_mcp=True)
            self.assertEqual(12, result["skills"])
            self.assertEqual(1, result["mcp_servers_bundled"])
            self.assertTrue(result["provider_onboarding_script_bundled"])
            self.assertFalse(result["all_vendor_sdks_physically_bundled"])
            folder = Path(result["directory"])
            for name in ("scripts/provider_onboarding.py",
                         "references/PROVIDER_CONNECTIONS.json",
                         "references/CUSTOMER_QUICKSTART.md",
                         "references/PROVIDER_TRANSPORT_BILLING.md",
                         "requirements-lite.txt",
                         "requirements-crawl-local.txt"):
                self.assertTrue((folder / name).is_file(), name)
            with zipfile.ZipFile(result["zip"]) as z:
                self.assertIsNone(z.testzip())
                for name in ("references/PROVIDER_CONNECTIONS.json",
                             "references/CUSTOMER_QUICKSTART.md",
                             "scripts/provider_onboarding.py"):
                    self.assertTrue(any(e.endswith("/" + name) for e in z.namelist()),
                                    name)


if __name__ == "__main__":
    unittest.main()
