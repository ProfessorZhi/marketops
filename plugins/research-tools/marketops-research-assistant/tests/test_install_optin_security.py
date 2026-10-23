"""Installer consent, recovery and path-handling contracts.

Every test here is offline. Command execution is simulated: the shell entry is
run against a fake interpreter that records its argv, and the Windows entry is
judged as text because PowerShell is not part of the release runtime. Nothing
in this module installs a package, calls winget, pip or a real Codex CLI.
"""
from __future__ import annotations

import contextlib
import io
import json
import os
import re
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import setup_clients as install  # noqa: E402

INSTALL_SH = ROOT / "install.sh"
INSTALL_PS1 = ROOT / "Install.ps1"
INSTALL_CMD = ROOT / "Install.cmd"

#: Commands the fake shell environment needs. /usr/bin is not on that PATH: it
#: also carries a real python3.12 on this host, which would defeat the probe.
CORE_COMMANDS = ("sh", "dirname", "pwd", "mkdir", "cat", "chmod", "grep",
                 "tail", "mktemp", "rm", "sed", "printf")


def _executable(path: Path, text: str) -> Path:
    path.write_text(text, encoding="utf-8", newline="\n")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)
    return path


def _fake_python(path: Path, exit_code: int = 0, leak: str = "") -> Path:
    """A real interpreter that pretends to be Python 3.12 for install.sh.

    Its -c probe succeeds and its path-normalization call prints a resolved
    path, so the shell entry can be exercised end to end without running the
    installer. Argument lists are appended to $FAKE_ARGV_LOG.
    """
    script = f"""#!{sys.executable}
import json, os, sys
argv = sys.argv[1:]
with open(os.environ["FAKE_ARGV_LOG"], "a", encoding="utf-8") as log:
    log.write(json.dumps(argv) + "\\n")
if argv[:1] == ["-c"]:
    if len(argv) > 2 and "expanduser" in argv[1]:
        from pathlib import Path
        print(Path(argv[2]).expanduser().resolve())
    sys.exit(0)
if "{leak}":
    print("{leak} token=test-only-value", file=sys.stderr)
    sys.exit({exit_code})
destination = ""
for index, item in enumerate(argv):
    if item == "--destination" and index + 1 < len(argv):
        destination = argv[index + 1]
if destination:
    venv = os.path.join(destination, ".venv", "bin")
    plugin = os.path.join(destination, "plugins", "marketops-research-assistant", "scripts")
    os.makedirs(venv, exist_ok=True)
    os.makedirs(plugin, exist_ok=True)
    venv_python = os.path.join(venv, "python")
    with open(venv_python, "w", encoding="utf-8") as handle:
        handle.write("#!/bin/sh\\nprintf '%s\\\\n' \\"$*\\" >> \\"$FAKE_VENV_LOG\\"\\nexit 0\\n")
    os.chmod(venv_python, 0o755)
    open(os.path.join(plugin, "customer_setup.py"), "w", encoding="utf-8").close()
sys.exit({exit_code})
"""
    return _executable(path, script)


def _core_bin(root: Path) -> Path:
    core = root / "core"
    core.mkdir()
    for name in CORE_COMMANDS:
        source = Path("/usr/bin") / name
        if source.exists():
            (core / name).symlink_to(source)
    return core


class ShellEnvironment:
    """A temporary PATH with only the fakes this test asked for."""

    def __init__(self, root: Path, *, python: bool = True, uv: bool = False,
                 codex: bool = True, python_exit: int = 0, leak: str = ""):
        self.root = root
        self.bin = root / "bin"
        self.bin.mkdir()
        self.core = _core_bin(root)
        self.argv_log = root / "argv.jsonl"
        self.venv_log = root / "venv.jsonl"
        self.home = root / "home"
        self.home.mkdir()
        if python:
            _fake_python(self.bin / "python3.12", python_exit, leak)
        if uv:
            _executable(self.bin / "uv", f"""#!{sys.executable}
import sys
if sys.argv[1:] == ["python", "find", "3.12"]:
    print({str(self.bin / "python3.12")!r})
    sys.exit(0)
sys.exit(2)
""")
        if codex:
            _executable(self.bin / "codex", "#!/bin/sh\nexit 0\n")
        self.env = {
            "PATH": f"{self.bin}:{self.core}",
            "HOME": str(self.home),
            "FAKE_ARGV_LOG": str(self.argv_log),
            "FAKE_VENV_LOG": str(self.venv_log),
            "TMPDIR": str(root / "tmp"),
        }
        (root / "tmp").mkdir()

    def calls(self):
        if not self.argv_log.exists():
            return []
        return [json.loads(line) for line in self.argv_log.read_text(encoding="utf-8").splitlines()]

    def run(self, *args, stdin=subprocess.DEVNULL):
        return subprocess.run(["/bin/sh", str(INSTALL_SH), *map(str, args)],
                              env=self.env, stdin=stdin, capture_output=True,
                              text=True, timeout=60)


class LinuxConsentTests(unittest.TestCase):
    def test_codex_registration_is_not_a_side_effect_of_the_default_run(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = ShellEnvironment(Path(tmp))
            target = Path(tmp) / "install"
            result = env.run(target, "both")
            self.assertEqual(0, result.returncode, result.stdout + result.stderr)
            calls = env.calls()
            setup = [call for call in calls if any("setup_clients.py" in item for item in call)]
            self.assertEqual(1, len(setup), calls)
            self.assertNotIn("--register-codex", setup[0])
            self.assertIn("codex plugin marketplace add", result.stdout + result.stderr,
                          "the skipped global change must name the one step that adds it")

    def test_codex_registration_runs_only_when_the_customer_asks_for_it(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = ShellEnvironment(Path(tmp))
            result = env.run(Path(tmp) / "install", "codex", "--register-codex")
            self.assertEqual(0, result.returncode, result.stdout + result.stderr)
            setup = [call for call in env.calls()
                     if any("setup_clients.py" in item for item in call)][0]
            self.assertIn("--register-codex", setup)

    def test_registration_flag_is_refused_for_a_client_that_cannot_use_it(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = ShellEnvironment(Path(tmp))
            result = env.run(Path(tmp) / "install", "claude", "--register-codex")
            self.assertEqual(2, result.returncode)
            self.assertIn("INSTALL_BLOCKED", result.stderr)
            self.assertIn("--client", result.stderr)
            self.assertEqual([], env.calls(), "a refused invocation must not start the installer")

    def test_a_missing_destination_fails_closed_without_prompting(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = ShellEnvironment(Path(tmp))
            result = env.run()
            self.assertEqual(2, result.returncode)
            self.assertIn("INSTALL_DESTINATION_REQUIRED", result.stderr)
            self.assertIn("install.sh", result.stderr)
            self.assertEqual([], env.calls())

    def test_home_and_relative_destinations_reach_the_installer_resolved(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = ShellEnvironment(Path(tmp))
            result = env.run("~/Market Ops", "claude")
            self.assertEqual(0, result.returncode, result.stdout + result.stderr)
            setup = [call for call in env.calls()
                     if any("setup_clients.py" in item for item in call)][0]
            destination = setup[setup.index("--destination") + 1]
            self.assertEqual(str(env.home / "Market Ops"), destination)
            self.assertTrue(Path(destination).is_absolute())

            other = Path(tmp) / "cwd"
            other.mkdir()
            process = subprocess.run(["/bin/sh", str(INSTALL_SH), "relative-install", "claude"],
                                     cwd=other, env=env.env, stdin=subprocess.DEVNULL,
                                     capture_output=True, text=True, timeout=60)
            self.assertEqual(0, process.returncode, process.stdout + process.stderr)
            setup = [call for call in env.calls()
                     if any("setup_clients.py" in item for item in call)][-1]
            destination = setup[setup.index("--destination") + 1]
            self.assertEqual(str((other / "relative-install").resolve()), destination)

    def test_uv_python_is_used_when_no_python_is_on_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = ShellEnvironment(Path(tmp), python=True, uv=True)
            # Remove the PATH entry by renaming: uv still points at the fake.
            (env.bin / "python3.12").rename(env.root / "uv-python")
            uv_script = env.bin / "uv"
            uv_script.write_text(uv_script.read_text(encoding="utf-8").replace(
                str(env.bin / "python3.12"), str(env.root / "uv-python")), encoding="utf-8")
            result = env.run(Path(tmp) / "install", "claude")
            self.assertEqual(0, result.returncode, result.stdout + result.stderr)
            setup = [call for call in env.calls()
                     if any("setup_clients.py" in item for item in call)]
            self.assertEqual(1, len(setup), env.calls())

    def test_child_output_is_not_echoed_when_the_installer_stops(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = ShellEnvironment(Path(tmp), python_exit=1, leak="provider said")
            result = env.run(Path(tmp) / "install", "claude", "--no-register-codex")
            self.assertEqual(1, result.returncode)
            combined = result.stdout + result.stderr
            self.assertNotIn("test-only-value", combined)
            self.assertIn("INSTALL_STEP_FAILED", combined)
            self.assertEqual(1, len([line for line in combined.splitlines()
                                     if line.startswith("INSTALL_BLOCKED:")]))

    def test_non_interactive_first_use_is_not_started(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = ShellEnvironment(Path(tmp))
            result = env.run(Path(tmp) / "install", "claude")
            self.assertEqual(0, result.returncode, result.stdout + result.stderr)
            self.assertFalse(env.venv_log.exists(),
                             "customer_setup prompts require a terminal; it must be left to the user")


class WindowsConsentContractTests(unittest.TestCase):
    """PowerShell is not installed in the release runtime, so this is a text contract."""

    @classmethod
    def setUpClass(cls):
        cls.script = INSTALL_PS1.read_text(encoding="utf-8")

    def test_python_installation_needs_an_explicit_switch_or_answer(self):
        self.assertIn("[switch]$InstallPython", self.script)
        self.assertIn("IsInputRedirected", self.script)
        # Exactly one winget install, and it sits inside the consent branch.
        self.assertEqual(1, self.script.count("winget.exe install"))
        self.assertRegex(self.script, r"if \(\$consent\) \{\s*& winget\.exe install")

    def test_no_python_and_no_consent_is_a_one_line_recovery(self):
        self.assertRegex(self.script, r"PYTHON_312_64BIT_REQUIRED")
        self.assertRegex(self.script, r"python\.org/downloads/windows")
        # The refusal is a single Stop-MarketOpsInstall line, never a throw
        # whose stack trace would quote local paths.
        self.assertNotRegex(self.script, r"throw 'Install Python")

    def test_codex_registration_is_guarded_by_its_own_switch(self):
        self.assertIn("[switch]$RegisterCodex", self.script)
        self.assertEqual(1, self.script.count("'--register-codex'"))
        self.assertRegex(self.script, r"if \(\$register\) \{\s*\$installArgs \+= '--register-codex'")

    def test_non_interactive_run_does_not_open_a_browser_or_prompt(self):
        self.assertIn("[switch]$NonInteractive", self.script)
        self.assertIn("if (-not $SkipFirstUse -and $script:Interactive)", self.script)
        self.assertRegex(self.script, r"if \(\$script:Interactive\) \{\s*Start-Process")
        # Every prompt (destination and the two consents) is behind a guard.
        self.assertIn("if (-not $script:Interactive) {\n        Stop-MarketOpsInstall 'INSTALL_DESTINATION_REQUIRED'",
                      self.script)
        self.assertIn("if (-not $consent -and $script:Interactive)", self.script)
        self.assertIn("if ($script:Interactive) {\n        $answer = Read-Host 'Add this install",
                      self.script)

    def test_install_cmd_can_be_used_by_automation_without_pausing(self):
        cmd = INSTALL_CMD.read_text(encoding="utf-8")
        self.assertIn("MARKETOPS_NO_PAUSE", cmd)
        self.assertLess(cmd.index("MARKETOPS_NO_PAUSE"), cmd.index("pause"))


class RefusalCoverageTests(unittest.TestCase):
    def test_every_installer_owned_refusal_has_an_actionable_remedy(self):
        source = (ROOT / "scripts" / "setup_clients.py").read_text(encoding="utf-8")
        codes = set(re.findall(r'raise (?:ValueError|FileExistsError)\("([A-Z][A-Z0-9_]{2,80})', source))
        self.assertGreater(len(codes), 10)
        missing = sorted(code for code in codes if code not in install.REMEDIES)
        self.assertEqual([], missing, "a refusal without a remedy is a dead end")

    def test_an_unexpected_exception_is_not_echoed(self):
        buffer = io.StringIO()
        argv = ["setup_clients.py", "--destination", "/tmp/marketops-unused",
                "--client", "claude"]
        with mock.patch.object(install, "plan", side_effect=RuntimeError("token=test-only-value-x")), \
                mock.patch.object(sys, "argv", argv), contextlib.redirect_stderr(buffer):
            status = install.main()
        text = buffer.getvalue()
        self.assertEqual(2, status)
        self.assertNotIn("test-only-value-x", text)
        self.assertIn("INSTALL_STEP_FAILED", text)
        self.assertEqual(1, len([line for line in text.splitlines()
                                 if line.startswith("INSTALL_BLOCKED:")]))

    def test_a_provider_command_failure_does_not_echo_its_captured_output(self):
        buffer = io.StringIO()
        argv = ["setup_clients.py", "--destination", "/tmp/marketops-unused",
                "--client", "codex", "--apply"]
        failure = subprocess.CalledProcessError(1, ["pip"], output="test-only-value-y",
                                                stderr="test-only-value-y")
        with mock.patch.object(install, "install", side_effect=failure), \
                mock.patch.object(sys, "argv", argv), contextlib.redirect_stderr(buffer):
            status = install.main()
        text = buffer.getvalue()
        self.assertEqual(2, status)
        self.assertNotIn("test-only-value-y", text)
        self.assertIn("INSTALL_STEP_FAILED", text)


class DestinationHandlingTests(unittest.TestCase):
    def test_home_paths_expand(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "home"
            home.mkdir()
            with mock.patch.dict(os.environ, {"HOME": str(home)}):
                result = install.plan("~/Market Ops", "claude")
            self.assertEqual(str(home / "Market Ops"), result["destination"])

    def test_relative_paths_resolve_against_the_invocation_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            cwd = Path(tmp) / "work"
            cwd.mkdir()
            with contextlib.chdir(cwd):
                result = install.plan("relative-install", "claude")
            self.assertEqual(str(cwd / "relative-install"), result["destination"])

    def test_an_empty_destination_is_refused_with_its_own_code(self):
        with self.assertRaises(ValueError) as caught:
            install.plan("   ", "claude")
        self.assertEqual("DESTINATION_REQUIRED", str(caught.exception))
        self.assertIn("DESTINATION_REQUIRED", install.REMEDIES)

    def test_a_destination_that_is_a_file_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "not-a-directory"
            target.write_text("keep", encoding="utf-8")
            with self.assertRaises(ValueError) as caught:
                install.plan(target, "claude")
            self.assertEqual("INSTALL_TARGET_NOT_DIRECTORY", str(caught.exception))
            self.assertEqual("keep", target.read_text(encoding="utf-8"))

    def test_consent_to_register_can_be_added_after_a_complete_install(self):
        import subprocess as sp
        commands = []
        real_run = sp.run

        def run(argv, **kwargs):
            if argv[0] == "codex":
                commands.append(argv)
                return sp.CompletedProcess(argv, 0, stdout="{}", stderr="")
            return real_run(argv, **kwargs)

        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "install"
            first = install.install(target, "codex", dependencies=False, register_codex=False)
            self.assertFalse(first["codex_marketplace_registered"])
            notes = target / "customer-notes.txt"
            notes.write_text("keep me", encoding="utf-8")
            plugin = Path(first["plugin_copy"])
            user_file = plugin / "USER_LOCAL_NOTE.txt"
            user_file.write_text("mine", encoding="utf-8")
            with mock.patch.object(install.shutil, "which", return_value="codex"), \
                    mock.patch.object(install.subprocess, "run", side_effect=run):
                second = install.install(target, "codex", dependencies=False,
                                         register_codex=True, resume=True)
            self.assertTrue(second["codex_marketplace_registered"])
            self.assertEqual(["codex", "plugin", "marketplace", "add", str(target)],
                             commands[0])
            self.assertEqual("keep me", notes.read_text(encoding="utf-8"))
            self.assertEqual("mine", user_file.read_text(encoding="utf-8"))
            receipt = json.loads((target / "INSTALL_RECEIPT.json").read_text(encoding="utf-8"))
            self.assertTrue(receipt["codex_marketplace_registered"])


if __name__ == "__main__":
    unittest.main()
