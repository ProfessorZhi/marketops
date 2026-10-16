"""No-network installation and metadata conformance tests."""
from __future__ import annotations
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import setup_clients as install
import build_client_bundles as bundles


class LocalInstallTests(unittest.TestCase):
    def test_dry_run_creates_nothing(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "new-install"
            result = install.plan(target, "both")
            self.assertTrue(result["no_live_provider_calls"])
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
            self.assertEqual("0.4.0", manifest["version"])
            for filename in ("mcp.json", ".mcp.json"):
                config = json.loads((source / filename).read_text(encoding="utf-8"))
                server = next(iter((config.get("mcpServers") or config.get("mcp_servers")).values()))
                self.assertEqual(result["venv_python"], server["command"])
                self.assertEqual(str(source / "scripts" / "research_mcp.py"), server["args"][0])
                self.assertNotIn("cwd", server, "Codex cached plugin root must not inherit external cwd")
            wb = result["workbuddy_bundle"]
            self.assertEqual(1, wb["mcp_servers_bundled"])
            self.assertEqual(17, wb["skills"])
            connector = Path(wb["directory"])
            wmeta = json.loads((connector / "connector-meta.json").read_text(encoding="utf-8"))
            self.assertEqual("mcp", wmeta["type"])
            self.assertEqual("token", wmeta["auth_mode"])
            self.assertTrue((connector / "scripts" / "research_mcp.py").exists())
            token = json.loads((connector / "token-schema.json").read_text(encoding="utf-8"))
            keys = {x["key"] for x in token["fields"]}
            for key in ("SERPAPI_API_KEY", "SERPER_API_KEY", "CRAWL4AI_API_KEY",
                        "TINYFISH_API_KEY", "TYPESAFE_API_KEY"):
                self.assertIn(key, keys)
            self.assertFalse((source / "data").exists())
            self.assertTrue((target / "data").exists())
            with self.assertRaises(FileExistsError):
                install.plan(target, "both")

    def test_bundle_default_still_skill_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            receipt = bundles.build(Path(tmp) / "bundle")
            self.assertEqual(0, receipt["mcp_servers_bundled"])
            self.assertEqual(16, receipt["skills"])


if __name__ == "__main__":
    unittest.main()
