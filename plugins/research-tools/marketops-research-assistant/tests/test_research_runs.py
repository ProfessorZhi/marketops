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

    def test_material_discovery_after_restart_exposes_primary_sources_without_cross_run_rows(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "data"
            for run_id in ("one", "other"):
                store.start_run(root, run_id, "scope", "Task", "rules")
            store.save_artifact(root, "one", "html-one", "crawl4ai", "https://example.org/one",
                                b"<p>Original synthetic public material</p>", "rendered_html")
            store.save_artifact(root, "one", "json-one", "crawl4ai", "https://example.org/one",
                                b'{"text":"Synthetic extraction"}', "provider_extracted")
            store.save_artifact(root, "other", "foreign", "crawl4ai", "https://example.org/other",
                                b"<p>Different task</p>", "rendered_html")
            first = store.list_materials(root, "one", limit=1)
            self.assertEqual("html-one", first["artifacts"][0]["artifact_id"])
            self.assertEqual("rendered_html", first["artifacts"][0]["material_kind"])
            self.assertNotIn("content", first["artifacts"][0])
            second = store.list_materials(root, "one", limit=1, cursor=first["next_cursor"])
            self.assertEqual(["json-one"], [x["artifact_id"] for x in second["artifacts"]])
            self.assertIsNone(second["next_cursor"])
            with self.assertRaisesRegex(ValueError, "MATERIAL_CURSOR"):
                store.list_materials(root, "other", cursor=first["next_cursor"])


if __name__ == "__main__":
    unittest.main()
