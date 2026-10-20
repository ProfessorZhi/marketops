"""What the rc7 field runs (Dutch golf clubs, Antwerp municipalities) broke, fixed."""
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import research_batch  # noqa: E402
import research_mcp  # noqa: E402
import research_pool as pool  # noqa: E402
import research_site_harvest as harvest  # noqa: E402


class NamesAndNumbers(unittest.TestCase):
    def test_a_body_word_name_binds_only_with_the_host(self):
        self.assertEqual("Rumst", harvest.host_bound_name("Gemeente Rumst", "https://www.rumst.be/"))
        self.assertEqual("", harvest.host_bound_name("Gemeente Rumst", "https://www.other.be/"))
        self.assertEqual("", harvest.host_bound_name("Harlow Cricket Club", "https://harlow.cc/"))

    def test_registration_numbers_and_coordinates_are_not_phones(self):
        text = ("Ondernemingsnummer 0207.512.345 voor de gemeente. Coördinaten 51.1923456, "
                "4.5612345 van de sporthal. Bel ons op +44 1279 434 567 tijdens de uren.")
        self.assertEqual(["+44 1279 434 567"],
                         [r["value"] for r in harvest.extract_routes(text) if r["type"] == "phone"])

    def test_links_naming_the_task_rank_first(self):
        html = ('<a href="/contact">Contact</a><a href="/vrije-tijd/sport">Sportdienst</a>'
                '<a href="/over-ons">Over ons</a>')
        links = harvest.candidate_links("https://www.rumst.be/", html, 3,
                                        harvest.focus_words("sportdienst diensthoofd sport"))
        self.assertEqual("https://www.rumst.be/vrije-tijd/sport", links[0])
        self.assertIn("https://www.rumst.be/over-ons", links)


class SameOrganisation(unittest.TestCase):
    def test_same_name_on_the_same_site_reuses_the_existing_key(self):
        import research_lite_store as store
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            store.start_run(root, "r", "municipalities", "objective", "v1")
            store.save_artifact(root, "r", "art-1", "crawl4ai", "https://www.dessel.be/",
                                b"<p>Welkom bij Gemeente Dessel, uw gemeente.</p>", "rendered_html")
            store.put_candidates_batch(root, "r", [{
                "record_id": "org-1", "stage": "entities", "kind": "organization",
                "entity_key": "org-pool-key", "artifact_ids": ["art-1"],
                "facts": {"name": "Gemeente Dessel", "website": "https://www.dessel.be/",
                          "claims": [{"field": "identity", "artifact_id": "art-1",
                                      "quote": "Welkom bij Gemeente Dessel, uw gemeente."}]}}])
            self.assertEqual("org-pool-key", harvest._same_organization(
                root, "r", "Gemeente Dessel", "https://dessel.be/sport", "gemeente-dessel"))
            self.assertEqual("", harvest._same_organization(
                root, "r", "Gemeente Dessel", "https://www.other.be/", "gemeente-dessel"))


class OfficialSite(unittest.TestCase):
    def test_the_host_naming_the_organisation_wins_over_social_and_wiki(self):
        answer = {"results": [
            {"url": "https://www.facebook.com/GemeenteAartselaar/"},
            {"url": "https://nl.wikipedia.org/wiki/Aartselaar"},
            {"url": "https://www.vlaanderen.be/organisaties/gemeente-aartselaar"},
            {"url": "https://www.aartselaar.be/nieuws"}]}
        with patch.object(research_mcp, "_search_all", return_value=answer):
            site = research_mcp.find_official_site("run", "op", {"name": "Gemeente Aartselaar"},
                                                   "be", "nl")
        self.assertEqual("https://www.aartselaar.be/", site)


class PoolTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        pool.import_targets(self.root, pool_id="p", source_id="s", source_url="https://x.org/list",
                            provider="manual", listed_count=2,
                            targets=[{"name": "Gemeente Aartselaar", "address": "Antwerpen"},
                                     {"name": "Gemeente Boechout", "address": "Antwerpen"}])

    def test_name_only_targets_get_a_site_instead_of_an_instant_hold(self):
        seen = []

        def find_site(target, op):
            seen.append(target["name"])
            return ""
        result = research_batch.harvest_batch(
            self.root, pool_id="p", worker_id="w", run_id="r", fetch=lambda *_: {},
            count=2, find_site=find_site)
        self.assertEqual(2, len(seen))
        reasons = {item["reason"] for item in result.get("results", result.get("summary", []))
                   if isinstance(item, dict) and "reason" in item}
        self.assertNotIn("NO_OFFICIAL_SITE_IN_POOL", reasons)

    def test_hold_targets_can_be_reopened_by_reason(self):
        claimed = pool.claim(self.root, pool_id="p", worker_id="w", count=2)
        pool.complete(self.root, pool_id="p", worker_id="w", results=[
            {"target_key": t["target_key"], "outcome": "hold", "reason": "NO_OFFICIAL_SITE_IN_POOL"}
            for t in claimed["targets"]])
        self.assertEqual(0, pool.reopen(self.root, pool_id="p", reason="SOMETHING_ELSE")["reopened"])
        self.assertEqual(2, pool.reopen(self.root, pool_id="p", reason="NO_OFFICIAL")["reopened"])
        self.assertEqual(2, len(pool.claim(self.root, pool_id="p", worker_id="w2", count=5)["targets"]))


if __name__ == "__main__":
    unittest.main()
