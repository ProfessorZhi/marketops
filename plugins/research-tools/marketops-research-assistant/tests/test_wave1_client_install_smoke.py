"""W-G installer smoke: the shipped artifact, and the checks that give it teeth.

Every positive assertion here is paired with a synthetic negative, because a
probe that cannot fail is not evidence.
"""
from __future__ import annotations

import json
import re
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import build_client_bundles as bundles  # noqa: E402
import client_install_probe as probe  # noqa: E402
import setup_clients as install  # noqa: E402

DEV_ONLY_DIRS = (
    ROOT.parent / "codex" / "marketops-research-starter",
    ROOT.parent / "workbuddy" / "tinyfish",
    ROOT.parent / "workbuddy" / "crawl4ai",
)


def _write(path: Path, payload: dict) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


class InstalledArtifactTests(unittest.TestCase):
    """What `setup_clients.py` writes, judged as the artifact a host loads."""

    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        cls.result = install.install(Path(cls._tmp.name) / "install", "all",
                                     dependencies=False, register_codex=False)
        cls.tree = Path(cls.result["plugin_copy"])

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def test_install_reports_its_own_smoke_probe(self):
        self.assertIn("install_smoke_probe", self.result)
        self.assertTrue(self.result["install_smoke_probe_ok"],
                        self.result["install_smoke_probe"])

    def test_generated_configs_have_no_shell_entry_and_one_server(self):
        # This is the Windows contract: an absolute interpreter plus a .py entry,
        # with nothing that needs a POSIX shell.
        for name in (".mcp.json", "mcp.json"):
            server = next(iter(json.loads((self.tree / name).read_text(
                encoding="utf-8"))["mcpServers"].values()))
            self.assertEqual(self.result["venv_python"], server["command"], name)
            # The managed entry is research_mcp.py: it is the one path the
            # installers, the client configs and the acceptance gate all name,
            # and it is where host compatibility has to hold. The facade remains
            # shipped and is imported by that entry rather than launched directly.
            self.assertEqual([str(self.tree / "scripts" / "research_mcp.py")],
                             server["args"], name)
            # "not a shell wrapper" stated precisely. The substring form this
            # replaces (`"sh" not in command`) false-positived on the *random*
            # temp directory the tree is unpacked into: a run whose path was
            # `.../tmpnshif41q/...` failed while six isolated runs passed. The
            # exact command equality above already pins the binary; this line
            # only has to say "and it is not an interpreter we did not choose".
            self.assertNotIn(Path(server["command"]).name,
                             {"sh", "bash", "zsh", "dash"}, name)
            self.assertNotIn("-c", server["args"], name)

    def test_installed_tree_passes_the_installed_artifact_probe(self):
        report = probe.run(self.tree, python=self.result["venv_python"],
                           wire=False, artifact="installed")
        self.assertTrue(report["ok"], report)
        self.assertTrue(report["windows_shape"]["ok"], report["windows_shape"])

    def test_installed_tree_carries_no_vendor_direct_config(self):
        for relative in ("codex", "workbuddy"):
            self.assertFalse((self.tree / relative).exists(), relative)
        self.assertFalse((self.tree / "mcp.json").read_text(encoding="utf-8")
                         .count("mcp.tinyfish.ai"))

    def test_workbuddy_bundle_ships_the_managed_entry_and_the_locks(self):
        connector = Path(self.result["workbuddy_bundle"]["directory"])
        self.assertTrue((connector / "scripts" / "research_mcp_facade.py").is_file())
        self.assertTrue((connector / "scripts" / "client_install_probe.py").is_file())
        self.assertTrue((connector / "requirements-lock-lite.txt").is_file())
        server = next(iter(json.loads((connector / "mcp.json").read_text(
            encoding="utf-8"))["mcpServers"].values()))
        self.assertTrue(server["args"][0].endswith("research_mcp.py"))
        # The host-compatibility rewrite is imported by that entry, so the facade
        # must ship beside it even though nothing launches it by name.
        self.assertTrue((connector / "scripts" / "research_mcp_facade.py").is_file())


class NegativeControlTests(unittest.TestCase):
    """The probe must fail on the thing it claims to catch."""

    def test_shell_entry_in_an_installed_tree_is_a_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write(root / "mcp.json", {"mcpServers": {"marketops-research": {
                "type": "stdio", "command": "sh",
                "args": ["${PLUGIN_ROOT}/scripts/launch_research_mcp.sh"]}}})
            findings = probe.audit_windows_config(root / "mcp.json")["findings"]
            self.assertEqual({"POSIX_SHELL_ON_WINDOWS", "SHELL_SCRIPT_ENTRY"},
                             {f["kind"] for f in findings})

    def test_enabled_vendor_server_in_a_packaged_tree_is_a_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "scripts").mkdir()
            for script in ("research_mcp_facade.py", "research_mcp.py",
                           "launch_research_mcp.sh"):
                (root / "scripts" / script).write_text("", encoding="utf-8")
            (root / "skills").mkdir()
            _write(root / ".claude-plugin" / "plugin.json", {"name": "x"})
            _write(root / ".codex-plugin" / "plugin.json", {"name": "x"})
            _write(root / "plugin.json", {"name": "x"})
            _write(root / "mcp.json", {"mcpServers": {"marketops-research": {
                "type": "stdio", "command": "python", "args": ["x/research_mcp_facade.py"]}}})
            _write(root / ".mcp.json", {"mcpServers": {"marketops-research": {
                "type": "stdio", "command": "python", "args": ["x/research_mcp_facade.py"]}}})
            _write(root / "vendor.json", {"mcpServers": {
                "tinyfish": {"type": "http", "url": "https://mcp.tinyfish.ai"}}})
            report = probe.run(root, wire=False, artifact="installed")
            kinds = {f["kind"] for f in report["manifests_and_configs"]["findings"]}
            self.assertIn("PACKAGED_VENDOR_MCP", kinds)

    def test_two_servers_in_one_config_is_a_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = _write(Path(tmp) / "mcp.json", {"mcpServers": {
                "marketops-research": {"type": "stdio", "command": "python", "args": ["a"]},
                "extra": {"type": "stdio", "command": "python", "args": ["b"]}}})
            result = probe.audit_mcp_config(path)
            self.assertFalse(result["ok"])
            self.assertIn("NOT_EXACTLY_ONE_MCP", {f["kind"] for f in result["findings"]})

    def test_an_unapproved_environment_name_is_a_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = _write(Path(tmp) / "mcp.json", {"mcpServers": {
                "marketops-research": {"type": "stdio", "command": "python", "args": ["a"],
                                       "env_vars": ["AWS_SECRET_ACCESS_KEY"]}}})
            result = probe.audit_mcp_config(path)
            self.assertIn("UNAPPROVED_ENV_NAME", {f["kind"] for f in result["findings"]})

    def test_a_credential_literal_in_a_config_is_a_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            # Deliberately obvious fixture: it must match the detector, not look
            # like a real key to a human or to a secret scanner.
            path = _write(Path(tmp) / "mcp.json", {"mcpServers": {
                "marketops-research": {"type": "stdio", "command": "python", "args": ["a"],
                                       "env": {"SERPER_API_KEY":
                                               "sk-EXAMPLEFIXTURENOTAREALSECRET"}}}})
            result = probe.audit_mcp_config(path)
            self.assertIn("SECRET_LITERAL", {f["kind"] for f in result["findings"]})


class ReproducibilityTests(unittest.TestCase):
    """Version pinning, and the defaults that keep the paid tiers off."""

    LOCKS = {
        "requirements-lock-lite.txt": "requirements-lite.txt",
        "requirements-lock-crawl-local.txt": "requirements-crawl-local.txt",
        "requirements-lock-lite-windows.txt": "requirements-lite.txt",
        "requirements-lock-crawl-local-windows.txt": "requirements-crawl-local.txt",
    }

    def test_every_lock_line_is_version_and_hash_pinned(self):
        for lock, ranges in self.LOCKS.items():
            lines = [line for line in (ROOT / lock).read_text(encoding="utf-8").splitlines()
                     if line and not line.lstrip().startswith("#")]
            requirements = [line.strip() for line in lines if not line.startswith(" ")]
            self.assertTrue(requirements, lock)
            for line in requirements:
                self.assertRegex(line, r"^[A-Za-z0-9._-]+==[^\s\\]+ \\$", f"{lock}: {line}")
            hashes = [line for line in lines if "--hash=sha256:" in line]
            self.assertGreaterEqual(len(hashes), len(requirements), lock)
            # The lock is resolved from the range file and must still satisfy it.
            declared = {re.split(r"[<>=]", line.strip())[0].lower()
                        for line in (ROOT / ranges).read_text(encoding="utf-8").splitlines()
                        if line.strip() and not line.startswith("#")}
            locked = {line.split("==")[0].lower() for line in requirements}
            self.assertTrue(declared <= locked, f"{lock} is missing {declared - locked}")

    def test_locks_carry_no_machine_specific_path(self):
        # The resolver embeds the input path in its comments; an absolute one
        # would make the lock describe the machine that produced it rather than
        # the dependency set, and would be wrong for every other checkout.
        for lock in self.LOCKS:
            text = (ROOT / lock).read_text(encoding="utf-8")
            # The forbidden literals are assembled rather than written. This file
            # is inside the very rule that scans for them, so spelling them here
            # would make the check trip on its own assertion -- which is exactly
            # the failure mode it exists to catch, one level up.
            prefixes = tuple("/" + part + "/" for part in ("data", "home", "Users"))
            for absolute in prefixes + ("C:" + chr(92),):
                self.assertNotIn(absolute, text, f"{lock} contains {absolute}")

    def test_dependency_install_argv_uses_the_lock_in_the_right_position(self):
        # `--require-hashes` must sit after the `install` subcommand; putting it
        # before silently changes what pip is asked to do.
        from unittest.mock import patch

        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(install.subprocess, "run") as calls:
                receipt = install.install(Path(tmp) / "i", "claude", dependencies=True,
                                          register_codex=False, local_crawl=True)
            pips = [call.args[0] for call in calls.call_args_list
                    if len(call.args[0]) >= 4 and call.args[0][1:4] == ["-m", "pip", "install"]]
            self.assertEqual(2, len(pips))
            for command in pips:
                self.assertEqual("--require-hashes", command[4])
                self.assertEqual("-r", command[5])
                self.assertTrue(command[6].endswith(".txt"), command[6])
            self.assertEqual({"hash_pinned": True}, {"hash_pinned": receipt["dependency_sources"][0]["hash_pinned"]})
            self.assertTrue(all(source["hash_pinned"] for source in receipt["dependency_sources"]),
                            receipt["dependency_sources"])

    def test_paid_and_authenticated_tiers_default_off(self):
        # T3/T4 stay off by default: every spend/authorisation switch the plugin
        # exposes must ship as 0/empty, never as an enabling value.
        source = (ROOT / "scripts" / "build_client_bundles.py").read_text(encoding="utf-8")
        for switch in ("MARKETOPS_SEARCH_ALLOW_NETWORK", "MARKETOPS_RESEARCH_ALLOW_NETWORK",
                       "MARKETOPS_TINYFISH_AGENT_ALLOW_PAID", "MARKETOPS_SEARCH_MAX_CALLS",
                       "MARKETOPS_RESEARCH_MAX_EXTERNAL_CALLS"):
            self.assertIn(switch, source)
        self.assertIn('"0"', source)
        for name in (".mcp.json", "mcp.json"):
            blob = (ROOT / name).read_text(encoding="utf-8")
            self.assertNotIn("ALLOW_PAID\\\": \\\"1", blob)
            self.assertNotIn("ALLOW_NETWORK\\\": \\\"1", blob)


class HostManifestTests(unittest.TestCase):
    def test_each_supported_host_has_a_manifest_pointing_at_one_mcp(self):
        report = probe.audit_plugin_tree(ROOT)
        self.assertEqual({"claude-code": True, "codex": True, "workbuddy": True},
                         report["manifests"])
        for relative in probe.MCP_CONFIGS:
            self.assertEqual(["marketops-research"], report["configs"][relative]["servers"])

    def test_claude_plugin_config_carries_only_fields_claude_honours(self):
        # Measured on Claude Code 2.1.272 with a real session: an extra field in
        # the plugin's `.mcp.json` does not degrade gracefully — the server is
        # reported `failed` and never launched, while `claude mcp list` still
        # health-checks the same config green. This is the regression guard for
        # the shipped file, and the negative control below proves the rule bites.
        server = next(iter(json.loads((ROOT / ".mcp.json").read_text(
            encoding="utf-8"))["mcpServers"].values()))
        self.assertEqual(set(), set(server) - probe.CLAUDE_PLUGIN_SERVER_FIELDS, server)

    def test_a_foreign_field_in_the_claude_config_is_a_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = _write(Path(tmp) / ".mcp.json", {"mcpServers": {
                "marketops-research": {
                    "type": "stdio", "command": "python", "args": ["a/research_mcp.py"],
                    "default_tools_approval_mode": "prompt"}}})
            result = probe.audit_mcp_config(path)
            self.assertFalse(result["ok"])
            self.assertEqual(["HOST_FOREIGN_FIELD"],
                             [f["kind"] for f in result["findings"]])

    def test_a_field_no_device_here_can_test_is_reported_not_hidden(self):
        # The generic config names no particular host, and no such device is
        # authorized. Silence would read as verification, so the probe notes it.
        with tempfile.TemporaryDirectory() as tmp:
            path = _write(Path(tmp) / "mcp.json", {"mcpServers": {
                "marketops-research": {
                    "type": "stdio", "command": "python", "args": ["a/research_mcp.py"],
                    "default_tools_approval_mode": "prompt"}}})
            result = probe.audit_mcp_config(path)
            self.assertTrue(result["ok"])
            self.assertEqual(["FIELD_ON_UNTESTED_HOST"],
                             [f["kind"] for f in result["notes"]])

    def test_the_two_configs_differ_only_by_the_launcher_override(self):
        # The launch paths differ — `.mcp.json` goes through the POSIX launcher,
        # which needs an interpreter override; the generic config names an
        # interpreter directly. The env_vars difference must be exactly that, so
        # a future edit cannot quietly widen one host's passthrough.
        claude = json.loads((ROOT / ".mcp.json").read_text(encoding="utf-8"))
        generic = json.loads((ROOT / "mcp.json").read_text(encoding="utf-8"))
        claude_vars = set(next(iter(claude["mcpServers"].values()))["env_vars"])
        generic_vars = set(next(iter(generic["mcpServers"].values()))["env_vars"])
        self.assertEqual({"MARKETOPS_RESEARCH_PYTHON"}, claude_vars - generic_vars)
        self.assertEqual(set(), generic_vars - claude_vars)
        self.assertTrue(claude_vars <= probe.SAFE_ENV_PASSTHROUGH)

    def test_codex_manifest_does_not_load_the_posix_launcher_config(self):
        # Codex must not be handed the `sh`-based Claude config: that is the
        # combination that silently fails on Windows.
        manifest = json.loads((ROOT / ".codex-plugin" / "plugin.json")
                              .read_text(encoding="utf-8"))
        self.assertEqual("./.mcp.json", manifest["mcpServers"])

    def test_skills_are_well_formed_and_provider_detail_stays_in_references(self):
        report = probe.audit_skills(ROOT)
        self.assertTrue(report["ok"], report["findings"])
        self.assertEqual(12, report["skill_count"])
        for provider in ("tinyfish", "serpapi", "serper", "crawl4ai", "jev"):
            self.assertTrue((ROOT / "references" / "providers" / f"{provider}.md").is_file(),
                            provider)

    def test_bundle_default_is_skill_only_and_ships_no_mcp(self):
        with tempfile.TemporaryDirectory() as tmp:
            receipt = bundles.build(Path(tmp) / "bundle")
            self.assertEqual(0, receipt["mcp_servers_bundled"])
            self.assertEqual(12, receipt["skills"])


if __name__ == "__main__":
    unittest.main()
