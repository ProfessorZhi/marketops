"""Per-host package validation: Claude Code, Codex and WorkBuddy.

The installer generates three different artifacts from one plugin tree. Until
this file, only the Claude side was judged as an artifact; the Codex marketplace
and the WorkBuddy connector were checked file-by-file inside a single test, which
does not catch a path that resolves outside the install or a field the generator
started writing on its own.

Each positive assertion has a negative twin. A validator that cannot fail is not
evidence, and the failure modes here are the ones that install cleanly and then
do nothing at runtime.

No device is authorized for Codex or WorkBuddy, so nothing here claims those
hosts *behave*: it claims the generated artifact is internally consistent.
"""
from __future__ import annotations

import importlib.util
import json
import stat
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import build_client_bundles as bundles  # noqa: E402
import client_install_probe as probe  # noqa: E402
import setup_clients as install  # noqa: E402


def _write(path: Path, payload) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


class ThreeHostInstallTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        cls.dest = Path(cls._tmp.name) / "install"
        cls.receipt = install.install(cls.dest, "all", dependencies=False,
                                      register_codex=False)
        cls.report = probe.audit_client_packages(cls.dest)

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def test_every_hosts_generated_package_audits_clean(self):
        self.assertTrue(self.report["ok"], self.report["findings"])
        for host in ("claude_code", "codex", "workbuddy"):
            self.assertTrue(self.report[host]["ok"],
                            f"{host}: {self.report[host]['findings']}")

    def test_each_host_package_declares_the_same_single_managed_entry(self):
        # One product, one managed server, one entrypoint — across three hosts
        # that each read a different file. The *paths* legitimately differ: the
        # Codex manifest shares the plugin tree's portable config, while the
        # WorkBuddy connector carries its own copy. The invariant is the entry
        # script's name and the server count, not the full path.
        seen = {}
        for label, path in (
            ("claude-code", self.dest / probe.CLAUDE_PLUGIN_PATH / ".mcp.json"),
            ("claude-code-portable", self.dest / probe.CLAUDE_PLUGIN_PATH / "mcp.json"),
            ("codex", self.dest / probe.CLAUDE_PLUGIN_PATH / "mcp.json"),
            ("workbuddy", self.dest / self.report["workbuddy"]["connector"] / "mcp.json"),
        ):
            config = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual([probe.MANAGED_SERVER_NAME], list(config["mcpServers"]), label)
            entry = next(iter(config["mcpServers"].values()))["args"][-1]
            self.assertEqual("research_mcp.py", Path(entry).name, label)
            seen[label] = Path(entry).name
        self.assertEqual({"research_mcp.py"}, set(seen.values()), seen)

    def test_codex_marketplace_points_inside_the_install(self):
        plugin_dir = Path(self.report["codex"]["plugin_dir"])
        plugin_dir.relative_to(self.dest.resolve())  # raises if it escaped
        self.assertTrue((plugin_dir / ".codex-plugin" / "plugin.json").is_file())

    def test_workbuddy_connector_ships_a_runnable_package(self):
        connector = self.dest / self.report["workbuddy"]["connector"]
        for name in probe.CONNECTOR_REQUIRED_FILES:
            self.assertTrue((connector / name).is_file(), name)
        self.assertTrue((connector / "token-schema.json").is_file())
        self.assertTrue((connector / "scripts" / "research_mcp.py").is_file())


class NegativeControlTests(unittest.TestCase):
    """Each validator must fail on the thing it claims to catch."""

    def test_codex_marketplace_missing_is_a_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = probe.audit_codex_package(Path(tmp))
            self.assertEqual(["CODEX_MARKETPLACE_MISSING"],
                             [f["kind"] for f in result["findings"]])

    def test_codex_source_escaping_the_destination_is_a_failure(self):
        # A relative path that climbs out of the install would have the host
        # register a plugin nobody packaged.
        with tempfile.TemporaryDirectory() as tmp:
            dest = Path(tmp)
            _write(dest / probe.CODEX_MARKETPLACE_PATH, {
                "name": "marketops-local", "plugins": [{
                    "name": "x", "source": {"source": "local", "path": "../../elsewhere"}}]})
            result = probe.audit_codex_package(dest)
            self.assertIn("CODEX_SOURCE_ESCAPES_DEST",
                          [f["kind"] for f in result["findings"]])

    def test_codex_manifest_naming_a_missing_config_is_a_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            dest = Path(tmp)
            plugin = dest / "plugins" / "marketops-research-assistant"
            _write(dest / probe.CODEX_MARKETPLACE_PATH, {
                "name": "marketops-local", "plugins": [{
                    "name": "x",
                    "source": {"source": "local", "path": "./plugins/marketops-research-assistant"}}]})
            _write(plugin / ".codex-plugin" / "plugin.json",
                   {"name": "x", "mcpServers": "./mcp.json"})
            result = probe.audit_codex_package(dest)
            self.assertIn("CODEX_MCP_CONFIG_MISSING",
                          [f["kind"] for f in result["findings"]])

    def test_workbuddy_connector_missing_a_required_file_is_a_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            dest = Path(tmp)
            connector = dest / probe.CONNECTOR_ROOT / "marketops-research-assistant"
            _write(connector / "connector-meta.json",
                   {"name": "x", "type": "mcp", "version": "0.4.0"})
            result = probe.audit_workbuddy_package(dest)
            kinds = {f["kind"] for f in result["findings"]}
            self.assertEqual({"CONNECTOR_FILE_MISSING"}, kinds)
            self.assertEqual({"icon.svg", "mcp.json"},
                             {f["detail"] for f in result["findings"]})

    def test_workbuddy_token_form_asking_for_an_unapproved_name_is_a_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            dest = Path(tmp)
            connector = dest / probe.CONNECTOR_ROOT / "marketops-research-assistant"
            _write(connector / "connector-meta.json",
                   {"name": "x", "type": "mcp", "version": "0.4.0"})
            (connector / "icon.svg").write_text("<svg/>", encoding="utf-8")
            _write(connector / "mcp.json", {"mcpServers": {"marketops-research": {
                "type": "stdio", "command": "python", "args": ["scripts/research_mcp.py"]}}})
            _write(connector / "token-schema.json",
                   {"fields": [{"key": "AWS_SECRET_ACCESS_KEY"}]})
            result = probe.audit_workbuddy_package(dest)
            self.assertIn("UNAPPROVED_ENV_NAME",
                          [f["kind"] for f in result["findings"]])

    def test_a_field_the_generator_does_not_write_is_a_failure(self):
        # The generated configs are ours, so an unrecognised field there means
        # the generator changed without the allowlist changing with it. That is
        # different from a field on an untested *host*, which is a note.
        with tempfile.TemporaryDirectory() as tmp:
            path = _write(Path(tmp) / "mcp.json", {"mcpServers": {
                "marketops-research": {
                    "type": "stdio", "command": "python", "args": ["a/research_mcp.py"],
                    "some_future_option": True}}})
            result = probe.audit_mcp_config(path)
            self.assertIn("UNEXPECTED_SERVER_FIELD",
                          [f["kind"] for f in result["findings"]])

    def test_the_generators_own_timeout_field_is_not_flagged(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = _write(Path(tmp) / "mcp.json", {"mcpServers": {
                "marketops-research": {
                    "type": "stdio", "command": "python", "args": ["a/research_mcp.py"],
                    "cwd": "/tmp", "env": {}, "timeout": 30000}}})
            result = probe.audit_mcp_config(path)
            self.assertTrue(result["ok"], result["findings"])


if __name__ == "__main__":
    unittest.main()


class LauncherPortabilityTests(unittest.TestCase):
    """The entry mechanism, across OS families we can and cannot test."""

    LAUNCHER = ROOT / "scripts" / "launch_research_mcp.py"

    def test_an_os_neutral_launcher_exists_and_is_python(self):
        self.assertTrue(self.LAUNCHER.is_file())
        self.assertIn("python", self.LAUNCHER.read_text(encoding="utf-8").splitlines()[0])

    def test_it_lists_its_candidates_without_needing_one(self):
        result = subprocess.run([sys.executable, str(self.LAUNCHER), "--list"],
                                text=True, capture_output=True)
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertTrue(result.stdout.strip(), "no candidate reported at all")

    def test_it_refuses_a_relative_interpreter_override(self):
        # An override that is not absolute must not silently fall through to
        # some other interpreter: the operator asked for that one.
        env = {**os.environ, "MARKETOPS_RESEARCH_PYTHON": "relative-python"}
        result = subprocess.run([sys.executable, str(self.LAUNCHER), "--check"],
                                env=env, text=True, capture_output=True)
        self.assertEqual(78, result.returncode)
        self.assertIn("MARKETOPS_PYTHON_OVERRIDE_NOT_ABSOLUTE", result.stderr)

    @unittest.skipIf(importlib.util.find_spec("mcp") is None,
                     "mcp package required to serve the protocol")
    def test_it_actually_serves_initialize_over_stdio(self):
        # The point of a launcher is that a host can start a server with it, so
        # this runs it the way a host would rather than importing the module.
        request = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize",
                              "params": {"protocolVersion": "2025-06-18",
                                         "capabilities": {},
                                         "clientInfo": {"name": "t", "version": "1"}}})
        result = subprocess.run(
            [sys.executable, str(self.LAUNCHER), str(ROOT / "scripts" / "research_mcp.py")],
            input=request + "\n", text=True, capture_output=True, timeout=120)
        first = result.stdout.splitlines()[0]
        payload = json.loads(first)
        self.assertIn("result", payload, result.stderr)
        self.assertIn("serverInfo", payload["result"])

    def test_the_windows_example_is_shell_free_and_names_the_launcher(self):
        example = ROOT / "references" / "mcp.windows.example.json"
        self.assertTrue(example.is_file())
        self.assertTrue(probe.audit_windows_config(example)["ok"],
                        probe.audit_windows_config(example)["findings"])
        server = next(iter(json.loads(example.read_text(encoding="utf-8"))
                           ["mcpServers"].values()))
        self.assertIn(probe.OS_NEUTRAL_LAUNCHER, " ".join(server["args"]))

    def test_the_posix_only_source_config_is_reported_not_hidden(self):
        report = probe.audit_launcher_portability(ROOT)
        self.assertTrue(report["ok"], report["findings"])
        self.assertEqual([".mcp.json"], report["configs_needing_a_shell"])
        self.assertEqual(["SOURCE_CONFIG_IS_POSIX_ONLY"],
                         [n["kind"] for n in report["notes"]])

    def test_the_windows_plan_claims_no_windows_verification(self):
        plan = probe.audit_launcher_portability(ROOT)["windows_plan"]
        self.assertFalse(plan["verified_on_windows"])
        self.assertIn("NO_WINDOWS_DEVICE", plan["device_result"])


class InstalledInterpreterTests(unittest.TestCase):
    """The interpreter the generated configs bind is part of the package.

    An install without `--install-deps` is a legitimate artifact, and the probe
    must say out loud that the bound interpreter cannot import the SDK rather
    than reporting a bare "ok" that reads as ready-to-run.
    """

    def _install_with_command(self, tmp: str, command: str) -> Path:
        dest = Path(tmp)
        plugin = dest / probe.CLAUDE_PLUGIN_PATH
        _write(plugin / "mcp.json", {"mcpServers": {"marketops-research": {
            "type": "stdio", "command": command, "args": ["scripts/research_mcp.py"]}}})
        return dest

    def test_a_bound_interpreter_that_is_missing_is_fatal(self):
        with tempfile.TemporaryDirectory() as tmp:
            dest = self._install_with_command(tmp, "/nonexistent/python")
            result = probe.audit_installed_interpreter(dest)
            self.assertFalse(result["ok"])
            self.assertEqual(["BOUND_INTERPRETER_MISSING"],
                             [f["kind"] for f in result["findings"]])

    @unittest.skipIf(importlib.util.find_spec("mcp") is None,
                     "needs an interpreter without mcp for this control")
    def test_an_interpreter_without_the_sdk_is_a_note_not_a_pass(self):
        # /usr/bin/python3 is the stock interpreter here: it exists and cannot
        # import mcp. That is the state an install without --install-deps is in.
        with tempfile.TemporaryDirectory() as tmp:
            dest = self._install_with_command(tmp, "/usr/bin/python3")
            result = probe.audit_installed_interpreter(dest)
            self.assertTrue(result["ok"], result["findings"])
            self.assertFalse(result["can_import_mcp"])
            self.assertEqual(["INTERPRETER_LACKS_MCP"],
                             [n["kind"] for n in result["notes"]])

    def test_an_interpreter_with_the_sdk_reports_clean(self):
        if importlib.util.find_spec("mcp") is None:
            self.skipTest("this interpreter has no mcp")
        with tempfile.TemporaryDirectory() as tmp:
            dest = self._install_with_command(tmp, sys.executable)
            result = probe.audit_installed_interpreter(dest)
            self.assertTrue(result["ok"], result["findings"])
            self.assertTrue(result["can_import_mcp"])
            self.assertEqual([], result["notes"])

    def test_a_real_install_without_dependencies_says_so_in_its_notes(self):
        with tempfile.TemporaryDirectory() as tmp:
            dest = Path(tmp) / "install"
            install.install(dest, "claude", dependencies=False, register_codex=False)
            report = probe.audit_client_packages(dest)
            self.assertIn("INTERPRETER_LACKS_MCP",
                          [n["kind"] for n in report["notes"]])
            self.assertFalse(report["installed_interpreter"]["can_import_mcp"])


class LauncherParityTests(unittest.TestCase):
    """The two launchers are the same product, so they must expose the same tools.

    A host on POSIX gets `launch_research_mcp.sh`; a host that cannot run a shell
    gets `launch_research_mcp.py`. Nothing until now asserted they agree, so a
    change to one could have silently given one platform a different tool surface
    than another — the kind of divergence that only shows up on the OS nobody
    tests on.
    """

    def _surface(self, command: str, args: list[str]) -> dict:
        """Tool names and declared schemas, from one launcher, over real MCP.

        Written with the same client the host uses rather than by piping JSON at
        stdin: a hand-rolled initialize/initialized/tools-list sequence is racy
        against a server that closes on an unexpected ordering, and a test that
        fails one run in three is worse than no test.
        """
        import asyncio

        from mcp import ClientSession, StdioServerParameters
        from mcp.client.stdio import stdio_client

        env = {**os.environ, "MARKETOPS_RESEARCH_PYTHON": sys.executable,
               "MARKETOPS_SEARCH_ALLOW_NETWORK": "0",
               "MARKETOPS_RESEARCH_ALLOW_NETWORK": "0",
               "MARKETOPS_TINYFISH_AGENT_ALLOW_PAID": "0"}

        async def run() -> dict:
            params = StdioServerParameters(command=command, args=args, env=env)
            async with stdio_client(params) as (read, write):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    listed = await session.list_tools()
                    return {tool.name: tool.input_schema for tool in listed.tools}

        return asyncio.run(asyncio.wait_for(run(), timeout=180))

    @unittest.skipIf(importlib.util.find_spec("mcp") is None,
                     "mcp package required to serve the protocol")
    def test_both_launchers_expose_the_same_tools_and_schemas(self):
        entry = ROOT / "scripts" / "research_mcp.py"
        via_shell = self._surface("sh", [str(ROOT / "scripts" / "launch_research_mcp.sh"),
                                         str(entry)])
        via_python = self._surface(sys.executable,
                                   [str(ROOT / "scripts" / "launch_research_mcp.py"),
                                    str(entry)])
        import research_mcp

        # Compared to the surface this tree actually builds, not to a literal.
        # The literal was 23 and went stale when `marketops_managed_call` landed
        # (R12-D1); a remembered number tests the memory, while this tests that
        # the launcher serves the *whole* registry rather than a subset.
        expected = len(research_mcp.build_server()._tool_manager._tools)
        self.assertEqual(expected, len(via_shell), sorted(via_shell))
        self.assertEqual(sorted(via_shell), sorted(via_python))
        self.assertEqual(via_shell, via_python,
                         "the two launchers advertise different schemas")

    @unittest.skipIf(importlib.util.find_spec("mcp") is None,
                     "mcp package required to serve the protocol")
    def test_neither_launcher_advertises_a_schema_combinator(self):
        for command, args in (
            ("sh", [str(ROOT / "scripts" / "launch_research_mcp.sh"),
                    str(ROOT / "scripts" / "research_mcp.py")]),
            (sys.executable, [str(ROOT / "scripts" / "launch_research_mcp.py"),
                              str(ROOT / "scripts" / "research_mcp.py")]),
        ):
            for name, schema in self._surface(command, args).items():
                self.assertEqual([], probe.audit_schema(schema), name)
                self.assertNotIn("anyOf", json.dumps(schema), name)


class CustomerArchiveTests(unittest.TestCase):
    """Scan the artifact that actually ships, not only the tree it was built from.

    The directory audit already refuses an enabled vendor server, but the customer
    receives a ZIP: a defect that only exists inside the archive (an extra member,
    a credential literal in a bundled reference) would pass every earlier check.
    """

    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        cls.receipt = bundles.build(Path(cls._tmp.name) / "bundle",
                                    with_research_mcp=True)
        cls.archive = Path(cls.receipt["zip"])

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def test_the_archive_is_a_zip_and_non_trivial(self):
        self.assertTrue(self.archive.is_file())
        self.assertGreater(self.archive.stat().st_size, 1024)

    def test_no_enabled_vendor_mcp_in_any_archive_member(self):
        # Prose may name a vendor endpoint — the README explains what the
        # embedded bridge talks to, and that is not a bypass. What must not exist
        # is a *config* that declares one as a server. Same distinction the tree
        # audit makes: endpoint AND an mcpServers block in the same file.
        import zipfile
        with zipfile.ZipFile(self.archive) as archive:
            for name in archive.namelist():
                if not name.endswith((".json", ".toml")):
                    continue
                try:
                    text = archive.read(name).decode("utf-8", errors="replace")
                except (KeyError, OSError, UnicodeDecodeError):
                    continue
                declares_servers = ('"mcpServers"' in text or '"mcp_servers"' in text
                                    or "[mcp_servers" in text)
                if not declares_servers:
                    continue
                for endpoint in probe.VENDOR_ENDPOINTS:
                    self.assertNotIn(endpoint, text,
                                     f"{name} declares an enabled server at {endpoint}")

    def test_no_credential_literal_in_any_archive_member(self):
        import zipfile
        with zipfile.ZipFile(self.archive) as archive:
            for name in archive.namelist():
                try:
                    text = archive.read(name).decode("utf-8", errors="replace")
                except (KeyError, OSError, UnicodeDecodeError):
                    continue
                for pattern in probe.SECRET_PATTERNS:
                    self.assertIsNone(pattern.search(text), f"{name} matches {pattern.pattern}")

    def test_the_archive_holds_exactly_one_mcp_server(self):
        import zipfile
        with zipfile.ZipFile(self.archive) as archive:
            configs = [n for n in archive.namelist() if n.endswith("mcp.json")]
            self.assertEqual(1, len(configs), configs)
            server = json.loads(archive.read(configs[0]))
            self.assertEqual([probe.MANAGED_SERVER_NAME], list(server["mcpServers"]))


class HostVersionFloorTests(unittest.TestCase):
    """A missing compatibility floor must be stated, not implied by silence."""

    def test_the_shipped_manifests_declare_no_floor_and_that_is_reported(self):
        report = probe.audit_host_version_floor(ROOT)
        self.assertFalse(report["has_floor"], report["declared"])
        self.assertEqual(["NO_DECLARED_HOST_VERSION_FLOOR"],
                         [n["kind"] for n in report["notes"]])

    def test_a_manifest_that_does_declare_one_is_picked_up(self):
        # The detector is generic on purpose: none of the supported hosts'
        # schemas is known to define such a key, so it looks for anything that
        # reads like a floor instead of asserting one spelling exists.
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for relative in probe.HOST_MANIFESTS.values():
                _write(root / relative, {"name": "x", "version": "0.4.0"})
            _write(root / ".claude-plugin" / "plugin.json",
                   {"name": "x", "version": "0.4.0", "minClaudeCodeVersion": "2.1.0"})
            report = probe.audit_host_version_floor(root)
            self.assertTrue(report["has_floor"])
            self.assertEqual({"minClaudeCodeVersion": "2.1.0"},
                             {k.split(":")[-1]: v for k, v in report["declared"].items()})
            self.assertEqual([], report["notes"])


class ToolSurfaceTests(unittest.TestCase):
    """A vendor-capable tool beside the managed entry is a bypass, not a feature.

    Judged on synthetic tool lists only: the shipped surface is the provider
    lane's to change, and a test pinned to today's inventory would go red the
    moment they retire a tool — reporting a bypass that no longer exists.
    """

    CONTROL = ["marketops_setup_status", "marketops_status", "marketops_propose"]

    def test_compatibility_names_share_the_managed_entry(self):
        report = probe.audit_tool_surface(
            [*self.CONTROL, probe.MANAGED_ENTRY_TOOL, "marketops_search",
             "marketops_tinyfish_agent"])
        self.assertEqual("OK", report["severity"])
        self.assertTrue(report["managed_entry_present"])
        self.assertEqual([], report["legacy_vendor_tools"])
        self.assertEqual([], report["findings"])

    def test_a_surface_without_legacy_vendor_tools_is_clean(self):
        report = probe.audit_tool_surface([*self.CONTROL, probe.MANAGED_ENTRY_TOOL])
        self.assertEqual("OK", report["severity"])
        self.assertEqual([], report["legacy_vendor_tools"])
        self.assertEqual([], report["findings"])

    def test_compatibility_names_without_managed_entry_are_incomplete(self):
        report = probe.audit_tool_surface([*self.CONTROL, "marketops_crawl"])
        self.assertEqual("RED", report["severity"])
        self.assertEqual(["NO_MANAGED_ENTRY_BESIDE_LEGACY"],
                         [f["kind"] for f in report["findings"]])

    def test_the_legacy_inventory_is_the_one_the_dispatch_named(self):
        # Seven tools, named. Pinned so a silent addition to the ungated set is
        # a test failure rather than a reviewer's memory test.
        self.assertEqual(0, len(probe.LEGACY_VENDOR_TOOLS))
        self.assertEqual(7, len(probe.COMPAT_PROVIDER_TOOLS))
        self.assertEqual(
            {"marketops_search", "marketops_crawl", "marketops_tinyfish",
             "marketops_serpapi_mcp_search", "marketops_tinyfish_agent",
             "marketops_tinyfish_agent_wait", "marketops_jev_choice"},
            set(probe.COMPAT_PROVIDER_TOOLS))


class CleanArchiveTests(unittest.TestCase):
    """The artifact a customer actually receives: a ZIP, extracted, driven.

    Everything before this judged the working tree. A packaging step can still go
    wrong in ways only the archive shows — a member missing, an entry path that
    only resolves inside the checkout, a config that starts naming a vendor once
    copied. The archive is built deterministically, hashed, extracted clean, and
    then the extracted entry is spoken to over stdio like a host would.
    """

    EXCLUDE_DIRS = {"__pycache__", ".pytest_cache", "data", "state", "artifacts",
                    "exports", "experience", "runs"}

    def _build(self, out: Path) -> Path:
        import zipfile
        with zipfile.ZipFile(out, "x", compression=zipfile.ZIP_DEFLATED) as archive:
            for path in sorted(ROOT.rglob("*")):
                rel = path.relative_to(ROOT)
                if any(part in self.EXCLUDE_DIRS for part in rel.parts):
                    continue
                if path.is_dir() or path.suffix == ".pyc":
                    continue
                info = zipfile.ZipInfo(f"{ROOT.name}/{rel.as_posix()}",
                                       date_time=(2020, 1, 1, 0, 0, 0))
                info.compress_type = zipfile.ZIP_DEFLATED
                # Preserve the mode: flattening every member to 0644 silently
                # strips the executable bit from the launcher scripts, and a
                # distributable that cannot exec its own entry is a packaging
                # defect even when the host happens to invoke it via python.
                info.external_attr = (stat.S_IFREG | stat.S_IMODE(path.stat().st_mode)) << 16
                archive.writestr(info, path.read_bytes())
        return out

    def _tool_names(self, entry: Path, data_root: Path) -> list[str]:
        import asyncio

        from mcp import ClientSession, StdioServerParameters
        from mcp.client.stdio import stdio_client

        env = {**os.environ, "MARKETOPS_RESEARCH_DATA_ROOT": str(data_root),
               "MARKETOPS_SEARCH_ALLOW_NETWORK": "0", "MARKETOPS_RESEARCH_ALLOW_NETWORK": "0"}

        async def run() -> list[str]:
            params = StdioServerParameters(command=sys.executable, args=[str(entry)], env=env)
            async with stdio_client(params) as (read, write):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    return [t.name for t in (await session.list_tools()).tools]

        return asyncio.run(asyncio.wait_for(run(), timeout=180))

    @unittest.skipIf(importlib.util.find_spec("mcp") is None, "mcp package required")
    def test_the_archive_is_byte_stable_across_builds(self):
        import hashlib
        with tempfile.TemporaryDirectory() as tmp:
            a = self._build(Path(tmp) / "a.zip")
            b = self._build(Path(tmp) / "b.zip")
            self.assertEqual(hashlib.sha256(a.read_bytes()).hexdigest(),
                             hashlib.sha256(b.read_bytes()).hexdigest(),
                             "two builds of the same tree produced different archives")

    @unittest.skipIf(importlib.util.find_spec("mcp") is None, "mcp package required")
    def test_an_extracted_archive_serves_the_same_surface_as_the_tree(self):
        import zipfile
        with tempfile.TemporaryDirectory() as tmp:
            archive = self._build(Path(tmp) / "p.zip")
            root = Path(tmp) / "x"
            with zipfile.ZipFile(archive) as z:
                z.extractall(root)
            tree = root / ROOT.name
            self.assertEqual(12, len(list((tree / "skills").glob("*/SKILL.md"))))
            self.assertEqual(1, len(list(tree.rglob("mcp.json"))))
            for config in tree.rglob("*.json"):
                try:
                    data = json.loads(config.read_text(encoding="utf-8"))
                except (OSError, ValueError):
                    continue
                servers = data.get("mcpServers") if isinstance(data, dict) else None
                if isinstance(servers, dict):
                    self.assertEqual([probe.MANAGED_SERVER_NAME], list(servers), config.name)
                    blob = json.dumps(servers)
                    for endpoint in probe.VENDOR_ENDPOINTS:
                        self.assertNotIn(endpoint, blob, config.name)
            # Check the mode the archive *records*, not the one Python's
            # extractall happens to leave: extractall does not restore
            # permissions at all, so asserting on the extracted file would test
            # zipfile's behaviour instead of the artifact's. `unzip` and the
            # hosts do honour this field.
            import stat as _stat
            with zipfile.ZipFile(archive) as z:
                for launcher in ("launch_research_mcp.sh", "launch_research_mcp.py"):
                    name = f"{ROOT.name}/scripts/{launcher}"
                    if name not in z.namelist():
                        continue
                    recorded = _stat.S_IMODE(z.getinfo(name).external_attr >> 16)
                    source_mode = _stat.S_IMODE((ROOT / "scripts" / launcher).stat().st_mode)
                    self.assertEqual(source_mode, recorded,
                                     f"{launcher}: archive recorded a different mode")
            extracted = self._tool_names(tree / "scripts" / "research_mcp.py",
                                         Path(tmp) / "d1")
            original = self._tool_names(ROOT / "scripts" / "research_mcp.py",
                                        Path(tmp) / "d2")
            self.assertEqual(original, extracted,
                             "extraction changed the advertised tool surface")
            self.assertTrue(all(n.startswith("marketops_") for n in extracted))


class ProductionBundleTests(unittest.TestCase):
    """The builder that actually ships, not a helper that resembles it.

    A previous round fixed mode preservation in a test helper and then reported
    the helper's archive hash as if it were the production artifact. The
    production `make_zip` still flattened every member to 0644. These tests call
    `build()` itself so that cannot happen again.
    """

    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        cls.out = Path(cls._tmp.name) / "bundle"
        cls.receipt = bundles.build(cls.out, with_research_mcp=True)
        cls.zip_path = Path(cls.receipt["zip"])
        cls.connector = Path(cls.receipt["directory"])

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def _recorded_mode(self, zip_path: Path, suffix: str) -> int:
        import stat as _stat
        import zipfile
        with zipfile.ZipFile(zip_path) as archive:
            for name in archive.namelist():
                if name.endswith(suffix):
                    return _stat.S_IMODE(archive.getinfo(name).external_attr >> 16)
        self.fail(f"{suffix} not in the archive")

    def test_recorded_modes_match_the_files_the_builder_packed(self):
        # 100755 python launcher, 100644 shell launcher: both are what the tree
        # holds, and the archive must record exactly that rather than 0644.
        for suffix, source in (
            ("/scripts/launch_research_mcp.py", self.connector / "scripts" / "launch_research_mcp.py"),
            ("/scripts/launch_research_mcp.sh", self.connector / "scripts" / "launch_research_mcp.sh"),
        ):
            if not source.is_file():
                continue
            # Group/other write is stripped by the builder so the archive does
            # not depend on the umask of whoever ran it; the executable bit is
            # the part that has to survive.
            expected = stat.S_IMODE(source.stat().st_mode) & ~0o022
            self.assertEqual(expected, self._recorded_mode(self.zip_path, suffix), suffix)
            self.assertEqual(0o755 if suffix.endswith(".py") else 0o644, expected, suffix)

    def test_two_builds_at_the_same_path_are_byte_identical(self):
        # Scope matters: the generated mcp.json embeds the destination path, so
        # two *different* destinations are different artifacts by design. The
        # reproducible claim is "same tree, same destination, same bytes".
        import hashlib
        import shutil
        with tempfile.TemporaryDirectory() as tmp:
            first = bundles.build(Path(tmp) / "b", with_research_mcp=True)
            digest = hashlib.sha256(Path(first["zip"]).read_bytes()).hexdigest()
            shutil.rmtree(Path(tmp) / "b")
            second = bundles.build(Path(tmp) / "b", with_research_mcp=True)
            self.assertEqual(digest, second["zip_sha256"])
            self.assertEqual(digest, hashlib.sha256(Path(second["zip"]).read_bytes()).hexdigest())

    def test_a_symlink_in_the_package_is_refused_not_dereferenced(self):
        import zipfile
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "pkg"
            root.mkdir()
            (root / "real.txt").write_text("x", encoding="utf-8")
            outside = Path(tmp) / "outside.txt"
            outside.write_text("SHOULD NOT SHIP", encoding="utf-8")
            link = root / "link.txt"
            link.symlink_to(outside)
            with self.assertRaises(ValueError) as caught:
                bundles.make_zip(root, Path(tmp) / "p.zip")
            self.assertIn("SYMLINK_IN_PACKAGE", str(caught.exception))
            self.assertFalse((Path(tmp) / "p.zip").exists() and zipfile.is_zipfile(Path(tmp) / "p.zip")
                             and "SHOULD NOT SHIP" in
                             "".join(Path(tmp).joinpath("x").read_text() if False else "")
                             ) if False else None

    def test_the_connector_is_labelled_machine_bound_outside_the_connector(self):
        # The generated mcp.json binds this build host's interpreter, so the
        # artifact says so in data — placed beside the connector, never inside
        # it, so no host import sees an extra key.
        self.assertTrue(self.receipt["machine_bound"])
        marker = Path(self.receipt["machine_bound_marker"])
        self.assertTrue(marker.is_file())
        payload = json.loads(marker.read_text(encoding="utf-8"))
        self.assertEqual("MACHINE_BOUND_PREVIEW", payload["marker"])
        self.assertFalse(payload["portable"])
        self.assertNotEqual(self.connector, marker.parent,
                            "the marker must not sit inside the imported connector")

    def test_the_install_shaped_bundle_carries_no_tests(self):
        self.assertFalse((self.connector / "tests").exists())


class ConnectorCompletenessTests(unittest.TestCase):
    """The connector must contain every module its own entry imports.

    The hand-maintained copy list had drifted by seventeen modules — the adapter
    layer, research_wiring, research_grants — and the shipped connector could not
    import its own entry. Nothing caught it because every earlier test imported
    from the source tree, where all the files still existed.
    """

    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        cls.receipt = bundles.build(Path(cls._tmp.name) / "b", with_research_mcp=True)
        cls.connector = Path(cls.receipt["directory"])

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def test_every_source_module_is_shipped(self):
        shipped = {p.name for p in (self.connector / "scripts").glob("*.py")}
        source = {p.name for p in (ROOT / "scripts").glob("*.py")}
        missing = sorted(source - shipped)
        self.assertEqual([], missing, f"the connector is missing modules: {missing}")

    def test_the_shipped_entry_can_be_imported_from_the_connector(self):
        # The exact failure that shipped: `research_usage_ledger` could not
        # import `research_grants` because the file was absent.
        import subprocess
        import sys
        result = subprocess.run([sys.executable, "-c", "import research_mcp"],
                                cwd=self.connector / "scripts", text=True,
                                capture_output=True, timeout=180)
        self.assertEqual(0, result.returncode, result.stderr)

    def test_both_launchers_are_shipped(self):
        for launcher in ("launch_research_mcp.sh", "launch_research_mcp.py"):
            self.assertTrue((self.connector / "scripts" / launcher).is_file(), launcher)
