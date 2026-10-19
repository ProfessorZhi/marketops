"""Experience must reach a new/resumed task through the normal MCP functions."""
from __future__ import annotations

import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import research_mcp as mcp


class ExperienceMCPIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "data"
        self.env = patch.dict(os.environ, {
            "MARKETOPS_RESEARCH_DATA_ROOT": str(self.root),
            "MARKETOPS_RESEARCH_CONFIG": str(Path(self.temp.name) / "config.json"),
        })
        self.env.start()
        self.addCleanup(self.env.stop)

    def seed_note(self):
        mcp.start("earlier", "shared-scope", "Earlier task", "rules-v1")
        mcp.note("earlier", "method", "intake", "other", "Scope was ambiguous",
                 "Record the target unit before researching", "The target unit was recorded",
                 evidence_ref="run-earlier")

    def test_start_and_next_retrieve_prior_experience_and_resume(self):
        self.seed_note()
        created = mcp.start("later", "shared-scope", "A new task", "rules-v1")
        notes = [x for x in created["experience"]["experiences"]
                 if x["source_type"] == "prior_run_note"]
        self.assertEqual(1, len(notes))
        self.assertEqual("earlier", notes[0]["source_run_id"])
        self.assertEqual(notes[0]["experience_id"],
                         mcp.next_step("later")["experience"]["experiences"][0]["experience_id"])
        resumed = mcp.start("later", "shared-scope", "A new task", "rules-v1")
        self.assertEqual("EXISTS_NO_CHANGE", resumed["status"])
        self.assertIn("experience", resumed)

    def test_experience_failure_is_visible_without_losing_the_created_run(self):
        with patch("research_experience_recall.recall", side_effect=ValueError("BROKEN_INDEX")):
            result = mcp.start("durable", "scope", "Task", "rules")
        self.assertEqual("RUN_CREATED", result["status"])
        self.assertEqual("UNAVAILABLE", result["experience"]["status"])
        self.assertEqual("durable", mcp.status("durable")["run_id"])

    def test_experience_tools_are_in_the_actual_server(self):
        tools = mcp.build_server()._tool_manager._tools
        for name in ("marketops_experience_recall", "marketops_experience_feedback",
                     "marketops_experience_summary"):
            self.assertIn(name, tools)


if __name__ == "__main__":
    unittest.main()
