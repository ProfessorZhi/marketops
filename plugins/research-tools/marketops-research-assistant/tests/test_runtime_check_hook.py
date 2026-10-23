"""SessionStart check: a marketplace install without the runtime says so instead of
silently having no MarketOps tools."""
import importlib.util
import json
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("runtime_check", ROOT / "hooks" / "runtime_check.py")
check = importlib.util.module_from_spec(spec)
spec.loader.exec_module(check)


class RuntimeCheck(unittest.TestCase):
    def test_silent_when_a_runtime_is_found(self):
        with patch.object(check, "runtime", return_value="/opt/venv/bin/python"), \
                patch.object(check.sys, "platform", "linux"):
            self.assertEqual("", check.message())

    def test_names_the_installer_when_no_runtime(self):
        with patch.object(check, "runtime", return_value=""), \
                patch.object(check.sys, "platform", "linux"):
            text = check.message()
        self.assertIn("install.sh", text)
        self.assertIn("Install.cmd", text)
        self.assertIn("Do not try to install packages yourself", text)

    def test_windows_marketplace_copy_points_to_the_installer(self):
        with patch.object(check.sys, "platform", "win32"), \
                patch.object(check, "_command", return_value="sh"):
            self.assertIn("Install.cmd -Client claude", check.message())

    def test_an_explicit_interpreter_must_be_absolute_and_ready(self):
        with patch.dict(check.os.environ, {"MARKETOPS_RESEARCH_PYTHON": "python"}):
            self.assertEqual("", check.runtime())

    def test_the_hook_is_registered_at_session_start(self):
        hooks = json.loads((ROOT / "hooks" / "hooks.json").read_text(encoding="utf-8"))["hooks"]
        command = hooks["SessionStart"][0]["hooks"][0]["command"]
        self.assertIn("runtime_check.py", command)
        self.assertIn("echo", command)  # no Python at all still gets a message


if __name__ == "__main__":
    unittest.main()
