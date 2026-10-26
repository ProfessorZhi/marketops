"""The PreToolUse guard has to exit 2: that is the only status Claude Code reads
as a block. The previous `python3 ... || python ...` line threw that status away
whenever the first interpreter exited 2 and the second one was missing (127), so
the hook printed BLOCKED and the command ran anyway.
"""
import json
import os
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HOOKS = json.loads((ROOT / "hooks" / "hooks.json").read_text(encoding="utf-8"))["hooks"]
COMMAND = HOOKS["PreToolUse"][0]["hooks"][0]["command"]


@unittest.skipIf(sys.platform == "win32", "the hook command is a POSIX shell line")
class GuardHookExitStatus(unittest.TestCase):
    def run_hook(self, payload):
        env = dict(os.environ, CLAUDE_PLUGIN_ROOT=str(ROOT))
        return subprocess.run(COMMAND, shell=True, env=env, input=json.dumps(payload),
                              capture_output=True, text=True)

    def test_a_research_database_read_is_blocked_with_status_two(self):
        out = self.run_hook({"tool_name": "Bash",
                             "tool_input": {"command": "sqlite3 research-pool.sqlite3"}})
        self.assertEqual(2, out.returncode, out.stderr)
        self.assertIn("BLOCKED", out.stderr)

    def test_reading_plugin_source_is_blocked_with_status_two(self):
        out = self.run_hook({"tool_name": "Read",
                             "tool_input": {"file_path": str(ROOT / "scripts" / "research_pool.py")}})
        self.assertEqual(2, out.returncode, out.stderr)
        self.assertIn("reading plugin source", out.stderr)

    def test_an_ordinary_command_is_allowed(self):
        out = self.run_hook({"tool_name": "Bash", "tool_input": {"command": "ls -la"}})
        self.assertEqual(0, out.returncode, out.stderr)


if __name__ == "__main__":
    unittest.main()
