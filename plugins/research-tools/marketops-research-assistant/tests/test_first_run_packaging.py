"""The first-use runtime must survive packaging: modules shipped, variables declared.

A bundled client that is missing `research_config.py` cannot even import its own
MCP entrypoint, so this is a packaging contract, not a nicety.
"""
from __future__ import annotations
import json
import py_compile
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import build_client_bundles as bundles  # noqa: E402
import preflight  # noqa: E402

RUNTIME_MODULES = ("research_config.py", "research_experience_summary.py")


class BundleTests(unittest.TestCase):
    def test_the_research_bundle_ships_the_runtime_config_modules(self):
        with tempfile.TemporaryDirectory() as tmp:
            receipt = bundles.build(Path(tmp) / "bundle", with_research_mcp=True)
            scripts = Path(receipt["directory"]) / "scripts"
            for name in RUNTIME_MODULES:
                shipped = scripts / name
                self.assertTrue(shipped.is_file(), f"{name} missing from the bundle")
                py_compile.compile(str(shipped), doraise=True)

    def test_the_skill_only_bundle_also_ships_them(self):
        # dual_search_mcp.py imports the journal, which imports research_config.
        with tempfile.TemporaryDirectory() as tmp:
            receipt = bundles.build(Path(tmp) / "bundle")
            scripts = Path(receipt["directory"]) / "scripts"
            for name in RUNTIME_MODULES:
                self.assertTrue((scripts / name).is_file(), f"{name} missing")

    def test_the_bundled_mcp_entrypoint_can_actually_import_its_config_module(self):
        with tempfile.TemporaryDirectory() as tmp:
            receipt = bundles.build(Path(tmp) / "bundle", with_search_mcp=True)
            scripts = Path(receipt["directory"]) / "scripts"
            self.assertTrue((scripts / "research_config.py").is_file())
            self.assertIn("import research_config",
                          (scripts / "research_event_journal.py").read_text(encoding="utf-8"))


class ManifestTests(unittest.TestCase):
    def test_both_manifests_declare_the_config_override_variable(self):
        for name in ("mcp.json", ".mcp.json"):
            env_vars = json.loads((ROOT / name).read_text(encoding="utf-8"))[
                "mcpServers"]["marketops-research"]["env_vars"]
            self.assertIn("MARKETOPS_RESEARCH_CONFIG", env_vars, name)

    def test_preflight_expects_exactly_the_declared_variables(self):
        self.assertIn("MARKETOPS_RESEARCH_CONFIG", preflight.EXPECTED_VARS)
        outcome = preflight.report()
        self.assertEqual([], outcome["errors"])
        self.assertEqual(13, outcome["skills"])


if __name__ == "__main__":
    unittest.main()
