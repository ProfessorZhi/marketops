"""RC6: an Agent is told what to fix and cannot quietly bypass the tools.

A zero-cost acceptance run spent 43 of 51 turns reading plugin code to work out
why a quote was refused. The write caveat now shows the page's closest text,
the skills state the rule, and the Claude Code hook refuses the bypass.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import research_lite_store as store  # noqa: E402

PAGE = b"<p>Bookings Secretary: Jane Smith, hire@alphahall.example or call 01245 111222.</p>"


def hook(tool, tool_input, plugin_root=str(ROOT)):
    env = {**os.environ, "CLAUDE_PLUGIN_ROOT": plugin_root}
    return subprocess.run([sys.executable, str(ROOT / "hooks" / "guard.py")],
                          input=json.dumps({"tool_name": tool, "tool_input": tool_input}),
                          capture_output=True, text=True, env=env)


class QuoteFeedbackTests(unittest.TestCase):
    def test_missed_quote_returns_the_closest_page_text(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            store.start_run(root, "r", "s", "objective", "v1")
            store.save_artifact(root, "r", "a1", "crawl4ai", "https://alphahall.example/", PAGE,
                                "rendered_html")
            result = store.put_candidate(root, "r", "c1", "routes", "contact_point", "c1", {
                "organization_id": "org", "type": "email", "value": "hire@alphahall.example",
                "claims": [{"field": "value", "artifact_id": "a1",
                            "quote": "Bookings secretary Jane Smith hire@alphahall.example"}]},
                ["a1"])
        caveat = " ".join(result["caveats"])
        self.assertIn("QUOTE_NOT_IN_ARTIFACT", caveat)
        self.assertIn("jane smith, hire@alphahall.example", caveat)
        self.assertIn("Do not read plugin source", caveat)


class DispositionFeedbackTests(unittest.TestCase):
    def test_unknown_disposition_names_the_legal_values(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            store.start_run(root, "r", "s", "objective", "v1")
            result = store.put_candidates_batch(root, "r", [{
                "record_id": "rev-1", "stage": "review", "kind": "review", "entity_key": "rev-org",
                "artifact_ids": [], "facts": {"review_type": "target_disposition",
                                              "target_kind": "organization",
                                              "target_entity_key": "org", "disposition": "ADMITTED",
                                              "reason": "routes found"}}])
        self.assertIn("RESEARCHED_CANDIDATE", " ".join(result["caveats"]))


class HookTests(unittest.TestCase):
    def test_reading_plugin_source_is_blocked(self):
        result = hook("Bash", {"command": f"sed -n 1,80p {ROOT}/scripts/research_delivery.py"})
        self.assertEqual(2, result.returncode)
        self.assertIn("marketops_handoff", result.stderr)

    def test_python_on_internals_and_databases_and_workbooks_are_blocked(self):
        self.assertEqual(2, hook("Bash", {"command": "python3 -c 'import research_evidence'"}).returncode)
        self.assertEqual(2, hook("Read", {"file_path": "/data/state/research-lite.sqlite3"}).returncode)
        self.assertEqual(2, hook("Bash", {"command": "python3 -c 'import openpyxl'"}).returncode)
        self.assertEqual(2, hook("Write", {"file_path": "/tmp/out/Result.xlsx", "content": "x"}).returncode)

    def test_unrelated_work_is_untouched(self):
        self.assertEqual(0, hook("Bash", {"command": "ls ~/projects"}).returncode)
        self.assertEqual(0, hook("Read", {"file_path": "/home/me/notes.md"}).returncode)


if __name__ == "__main__":
    unittest.main()
