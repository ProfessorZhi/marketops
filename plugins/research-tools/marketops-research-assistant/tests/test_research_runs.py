"""Cross-session discovery must read durable Runs without creating a store."""
import tempfile
import unittest
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import research_lite_store as store


class ResearchRunsTests(unittest.TestCase):
    def test_uninitialized_root_is_empty_without_creating_it(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "not-created"
            self.assertEqual([], store.list_runs(root)["runs"])
            self.assertFalse(root.exists())

    def test_pages_return_stable_scope_rules_and_actual_record_counts(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "data"
            store.start_run(root, "old", "sports", "First batch", "rules-v1")
            store.put_candidate(root, "old", "old-target", "entities", "venue",
                                "one-venue", {"name": "Synthetic venue"}, [])
            store.start_run(root, "other", "culture", "Different scope", "rules-v2")
            store.start_run(root, "new", "sports", "Second batch", "rules-v1")
            first = store.list_runs(root, limit=1, scope="sports")
            self.assertEqual("new", first["runs"][0]["run_id"])
            self.assertEqual("rules-v1", first["runs"][0]["rule_version"])
            second = store.list_runs(root, limit=1, scope="sports", cursor=first["next_cursor"])
            self.assertEqual("old", second["runs"][0]["run_id"])
            self.assertEqual(1, second["runs"][0]["record_count"])
            self.assertIsNone(second["next_cursor"])

    def test_invalid_limits_and_unknown_cursor_are_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "data"
            store.start_run(root, "one", "scope", "Task", "rules")
            for limit in (0, 51, True):
                with self.assertRaises(ValueError):
                    store.list_runs(root, limit=limit)
            with self.assertRaisesRegex(ValueError, "RUN_CURSOR_UNKNOWN"):
                store.list_runs(root, cursor="missing")


if __name__ == "__main__":
    unittest.main()
