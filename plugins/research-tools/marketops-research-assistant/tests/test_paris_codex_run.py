"""What the Paris Codex run (0.5.11-rc2) showed, as tests.

* the Data ES tail was only reachable as an export over 5,000 rows;
* 4,737 venues never reached contact research: operators are the route;
* the ladder ended after 20 brands while the inventory was untouched;
* site: queries returned unrelated pages.
"""
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import research_bulk as bulk  # noqa: E402
import research_lite_store as store  # noqa: E402
import research_methods as methods  # noqa: E402
import research_pool as pool  # noqa: E402


def facility(n):
    town = ["Créteil", "Nanterre", "Paris"][n % 3]
    return {"equip_numero": f"E{n}", "equip_nom": f"Gymnase {n}", "com_nom": town,
            "equip_gest_type": "Commune" if n % 4 else "Privé commercial",
            "gestionnaire": f"Ville de {town}" if n % 4 else "UrbanSoccer"}


class Inventory(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        store.start_run(self.root, "run-a", "Indoor venues", "Contacts at venue operators", "test")
        rows = [facility(n) for n in range(6000)]
        store.save_artifact(self.root, "run-a", "export", "crawl4ai",
                            "https://example.org/api/explore/v2.1/catalog/datasets/x/exports/json",
                            json.dumps(rows).encode(), "raw_response")
        self.mapping = {"name": "equip_nom", "city": "com_nom", "source_key": "equip_numero",
                        "operator": "gestionnaire", "operator_type": "equip_gest_type"}

    def load(self, count=60):
        for offset in range(0, count, 20):
            bulk.import_saved(self.root, run_id="run-a", artifact_id="export", pool_id="venues",
                              source_id="data-es", mapping=self.mapping, offset=offset, limit=20)

    def test_a_department_export_over_5000_rows_imports(self):
        preview = bulk.import_saved(self.root, run_id="run-a", artifact_id="export",
                                    pool_id="venues", source_id="data-es", preview=True)
        self.assertEqual(6000, preview["saved_rows"])
        self.assertIn("gestionnaire", preview["available_fields"])

    def test_venues_group_by_operator_and_become_targets(self):
        self.load()
        groups = bulk.venue_groups(self.root, run_id="run-a", by="operator")
        self.assertEqual(4, groups["groups_total"])
        self.assertEqual(60, groups["venues_total"])
        biggest = groups["groups"][0]
        self.assertGreater(biggest["venues"], 10)
        out = bulk.venue_groups(self.root, run_id="run-a", by="operator",
                                import_to_pool="operators", country_hint="France")
        self.assertEqual(4, out["imported"]["imported"])
        again = bulk.venue_groups(self.root, run_id="run-a", by="operator")
        self.assertEqual(0, again["groups_uncovered"])

    def test_municipal_venues_group_by_town(self):
        self.load()
        towns = bulk.venue_groups(self.root, run_id="run-a", by="city", operator_types=["Commune"])
        self.assertEqual({"Créteil municipality", "Nanterre municipality", "Paris municipality"},
                         {g["target_name"] for g in towns["groups"]})
        # Any language: the caller names a town's target the way the country does.
        local = bulk.venue_groups(self.root, run_id="run-a", by="city", operator_types=["Commune"],
                                  name_template="Ville de {group}")
        self.assertIn("Ville de Paris", {g["target_name"] for g in local["groups"]})
        with self.assertRaisesRegex(ValueError, "VENUE_GROUP_TEMPLATE_INVALID"):
            bulk.venue_groups(self.root, run_id="run-a", by="city", name_template="City hall")

    def test_the_ladder_does_not_end_while_operators_are_uncovered(self):
        import research_execution
        self.load()
        for name, _ in methods.LADDERS["contacts"]:
            methods.record(self.root, "run-a", name, "exhausted")
        state = research_execution.assess(self.root, "run-a", goal={"goal_satisfied": False})
        self.assertTrue(state["continuation_required"])
        self.assertEqual("OPERATOR", state["next_method"])
        self.assertEqual(4, state["venue_operator_groups"]["groups_uncovered"])
        bulk.venue_groups(self.root, run_id="run-a", import_to_pool="operators")
        with closing_pool(self.root) as db:
            db.execute("UPDATE pool_targets SET status='done' WHERE pool_id='operators'")
            db.execute("UPDATE pool_targets SET status='done' WHERE pool_id='venues'")
            db.commit()
        state = research_execution.assess(self.root, "run-a", goal={"goal_satisfied": False})
        self.assertEqual("FINAL_METHODS_EXHAUSTED", state["state"])


def closing_pool(root):
    from contextlib import closing
    return closing(pool._db(pool._root(root)))


class SiteQueries(unittest.TestCase):
    def test_off_site_hits_are_dropped(self):
        import research_mcp as mcp

        def search(provider, *args, **kwargs):
            return {"results": [{"url": "https://mail.google.com/mail/u/0/"},
                                {"url": "https://www.urbansoccer.fr/mentions-legales/"},
                                {"url": "https://blog.urbansoccer.fr/equipe"}]}

        with tempfile.TemporaryDirectory() as tmp, \
                patch.object(mcp, "root", return_value=Path(tmp)), \
                patch.object(mcp, "search", side_effect=search):
            answer = mcp._search_all("site:urbansoccer.fr (partenariats OR directeur) email",
                                     "FR", "fr", "r", "op", 10)
        self.assertEqual({"https://www.urbansoccer.fr/mentions-legales/",
                          "https://blog.urbansoccer.fr/equipe"},
                         {hit["url"] for hit in answer["results"]})
        self.assertEqual(1, answer["off_site_results_dropped"])


class WallsAreRecorded(unittest.TestCase):
    """Every kind of wall reaches the experience journal without the agent writing a note."""

    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        store.start_run(self.root, "run-a", "Any market", "Any goal", "test")

    def codes(self):
        import research_event_journal as journal
        from contextlib import closing
        with closing(journal._connect(journal._root(self.root))) as db:
            return [e["reason_code"] for e in journal.read_all_events(db)
                    if e["status"] in journal.BUMP_STATUSES]

    def test_a_refused_tool_call_is_journaled(self):
        import research_mcp as mcp

        def marketops_pool_import(run_id="", pool_id=""):
            raise ValueError("BULK_ROWS_INVALID: too many rows")

        guarded = mcp._guarded(marketops_pool_import)
        with patch.object(mcp, "root", return_value=self.root):
            with self.assertRaises(Exception):
                guarded(run_id="run-a", pool_id="p")
        self.assertIn("BULK_ROWS_INVALID", self.codes())

    def test_a_held_pool_target_is_journaled(self):
        pool.import_targets(self.root, pool_id="p", source_id="s", source_url="", provider="local",
                            listed_count=1, targets=[{"name": "Any Club", "address": "Town"}])
        claimed = pool.claim(self.root, pool_id="p", worker_id="w", count=1)
        key = claimed["targets"][0]["target_key"]
        pool.complete(self.root, pool_id="p", worker_id="w", results=[
            {"target_key": key, "outcome": "hold", "reason": "SITE_UNREACHABLE: timeout",
             "run_id": "run-a"}])
        self.assertIn("SITE_UNREACHABLE", self.codes())

    def test_an_ignored_site_operator_is_journaled(self):
        import research_mcp as mcp
        with patch.object(mcp, "root", return_value=self.root), \
                patch.object(mcp, "search", return_value={"results": [{"url": "https://other.example/"}]}):
            mcp._search_all("site:club.example coach", "GB", "en", "run-a", "op-1", 10)
        self.assertIn("SEARCH_SITE_OPERATOR_IGNORED", self.codes())

    def test_the_experience_file_maps_walls_by_cause_reader_and_website(self):
        import research_experience_summary as summary
        import research_walls as walls
        walls.record_chain(self.root, run_id="run-a", task_kind="known_public_url",
                           url="https://blocked.example/team", query="", selected=walls.CLOUD_RUNG,
                           attempts=[{"capability_id": walls.LOCAL_RUNG, "status": "FAILED",
                                      "reason_code": "SOURCE_ACCESS_BLOCKED", "ordinal": 1}])
        text = summary.render([], [], {}, walls=walls.summary(self.root))
        self.assertIn("碰壁地图", text)
        self.assertIn("SOURCE_ACCESS_BLOCKED", text)
        self.assertIn("blocked.example", text)
        self.assertNotIn("https://blocked.example/team", text)


if __name__ == "__main__":
    unittest.main()
