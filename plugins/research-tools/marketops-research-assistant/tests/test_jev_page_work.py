"""Jev on harvested pages: directory split, name fill, screening. Jev is faked."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import research_directory as directory  # noqa: E402
import research_jev_pages as pages  # noqa: E402

DIRECTORY = (
    "Associations sportives du 7e arrondissement\n"
    "Badminton Club Paris 7 - Président : Jean DUPONT - Tél 01 45 00 00 00 - bcp7@orange.fr\n"
    "Union Sportive du Gros-Caillou | Football | Contact : Marie Lefèvre, secrétaire - "
    "usgc.secretariat@gmail.com\n"
    "Tennis Club Invalides • Union Sportive Émile Anthoine • contact@tc-invalides.fr\n"
    "Mairie du 7e : sports7@paris.fr\n")
SITE = "https://mairie07.paris.fr/pages/associations-sportives"


def routes_of(text):
    import research_site_harvest as harvest
    return [r for r in harvest.extract_routes(text) if r["type"] == "email"]


class Fake:
    def __init__(self, answers):
        self.answers, self.asked = answers, []

    def __call__(self, artifact_id, locator, question, options):
        self.asked.append((locator, question, options))
        for key, answer in self.answers.items():
            if key in question:
                return answer
        return ("unknown", 0.5)


class DirectorySplit(unittest.TestCase):
    def test_each_address_gets_the_club_printed_with_it(self):
        routes = routes_of(DIRECTORY)
        self.assertTrue(directory.is_directory(SITE, [r["value"] for r in routes]))
        jev = Fake({"usgc.secretariat": ("org_1", 0.95), "contact@tc-invalides": ("org_1", 0.9)})
        split = {s["route"]["value"]: s for s in pages.split_directory(DIRECTORY, SITE, routes, "a1", jev)}
        self.assertEqual("Badminton Club Paris 7", split["bcp7@orange.fr"]["organization"]["name"])
        for item in split.values():
            self.assertIn(item["organization"]["name"], item["quote"])
            self.assertIn(item["route"]["value"], item["quote"])
            self.assertIn(item["quote"], " ".join(DIRECTORY.split()) if "\n" in item["quote"] else DIRECTORY)
        # The town's own inbox names no club before it on its line: it stays with the town.
        self.assertNotIn("sports7@paris.fr", split)

    def test_a_site_listing_its_own_people_is_not_a_directory(self):
        own = "Président : jean@club.fr ; Trésorier : paul@club.fr ; Info : info@club.fr"
        self.assertFalse(directory.is_directory("https://club.fr/", [
            r["value"] for r in routes_of(own)]))
        self.assertEqual([], pages.split_directory(own, "https://club.fr/", routes_of(own), "a", Fake({})))

    def test_an_unsure_jev_leaves_the_address_unassigned(self):
        routes = routes_of(DIRECTORY)
        jev = Fake({"contact@tc-invalides": ("org_1", 0.55)})
        split = {s["route"]["value"] for s in pages.split_directory(DIRECTORY, SITE, routes, "a1", jev)}
        self.assertNotIn("contact@tc-invalides.fr", split)
        # One body printed before the address: code settles it, Jev is not asked.
        self.assertIn("usgc.secretariat@gmail.com", split)
        self.assertEqual(["contact@tc-invalides.fr"], [a[0] for a in jev.asked])


class NameFill(unittest.TestCase):
    TEXT = ("Le bureau. Président : Jean DUPONT. Pour toute question écrire à "
            "contact.asm@orange.fr ou au trésorier Paul Martin : paul.martin@asm.fr")

    def test_jev_picks_among_printed_names_and_the_quote_spans_both(self):
        import research_grading as grading
        routes = routes_of(self.TEXT)
        jev = Fake({"contact.asm": ("name_1", 0.92)})

        def has_name(email, quote):
            return bool(grading._name_in_quote(email, [("a", quote)]))
        filled = pages.fill_names(self.TEXT, routes, "a1", jev, has_name=has_name)
        by = {r["value"]: r for r in routes}
        self.assertEqual(1, filled)
        self.assertEqual("Jean DUPONT", by["contact.asm@orange.fr"]["person_name"])
        self.assertIn("Jean DUPONT", by["contact.asm@orange.fr"]["quote"])
        # Code already reads "Paul Martin" from paul.martin@: Jev is not asked.
        self.assertNotIn("person_name", by["paul.martin@asm.fr"])
        self.assertEqual(1, len(jev.asked))
        self.assertEqual("contact.asm@orange.fr", jev.asked[0][0])

    def test_none_of_these_or_no_jev_changes_nothing(self):
        routes = routes_of(self.TEXT)
        pages.fill_names(self.TEXT, routes, "a1", Fake({"contact.asm": ("none_of_these", 0.99)}),
                         has_name=lambda e, q: e.startswith("paul"))
        self.assertFalse(any(r.get("person_name") for r in routes))

        def broken(*args):
            raise ValueError("JEV_CREDENTIAL_ABSENT")
        routes = routes_of(self.TEXT)
        pages.fill_names(self.TEXT, routes, "a1", broken, has_name=lambda e, q: False)
        self.assertFalse(any(r.get("person_name") for r in routes))

    def test_street_and_role_words_are_not_names(self):
        text = "Gymnase Rue Cler, Paris - Président Club - info@x.fr"
        self.assertEqual([], pages.name_candidates(text, "info@x.fr"))


class Screen(unittest.TestCase):
    def test_only_a_sure_out_of_scope_answer_stops(self):
        question = pages.screen_question({"screen_competitors": "artificial turf installers"},
                                         "Natural-grass pitch owners and operators in Paris")
        self.assertIn("Competitors", question)
        self.assertTrue(pages.screen("a", question, Fake({"Research": ("competitor", 0.95)}))["stop"])
        self.assertFalse(pages.screen("a", question, Fake({"Research": ("competitor", 0.7)}))["stop"])
        self.assertFalse(pages.screen("a", question, Fake({"Research": ("target", 0.99)}))["stop"])
        self.assertFalse(pages.screen("a", "", Fake({}))["stop"])


if __name__ == "__main__":
    unittest.main()


class HarvestWithJev(unittest.TestCase):
    """The page work inside a real harvest: staged records pass the evidence gate."""

    HOME = "https://mairie07.paris.fr/"
    PAGE = ("<html><body><h1>Mairie du 7e arrondissement</h1><p>Associations sportives</p>"
            + "".join(f"<p>{line}</p>" for line in DIRECTORY.splitlines()) + "</body></html>")

    def setUp(self):
        import tempfile
        import research_lite_store as store
        self.store = store
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name) / "data"
        store.start_run(self.root, "jev-run", "synthetic", "clubs", "rules")

    def fetch(self, url, operation_id):
        if url != self.HOME:
            return {"status": "HOLD", "reason_code": "SOURCE_ACCESS_BLOCKED"}
        artifact = "art-" + operation_id
        self.store.save_artifact(self.root, "jev-run", artifact, "crawl4ai", url,
                                 self.PAGE.encode(), "rendered_html")
        return {"status": "SUCCESS_CANDIDATE", "artifact_ids": [artifact],
                "primary_artifact_ids": [artifact]}

    def harvest(self, jev, question=""):
        import research_site_harvest as harvest
        return harvest.harvest(self.root, run_id="jev-run", operation_id="h1",
                               organization_name="Mairie du 7e arrondissement",
                               organization_key="org-mairie7", url=self.HOME, fetch=self.fetch,
                               max_pages=2, jev_ask=jev, screen_question=question)

    def test_clubs_on_a_town_list_become_their_own_organisations(self):
        jev = Fake({"contact@tc-invalides": ("org_1", 0.93)})
        result = self.harvest(jev)
        records = self.store.list_run(self.root, "jev-run")["records"]
        orgs = {r["data"]["name"]: r["entity_key"] for r in records if r["kind"] == "organization"}
        self.assertIn("Badminton Club Paris 7", orgs)
        self.assertIn("Tennis Club Invalides", orgs)
        contacts = {r["data"]["value"]: r["data"] for r in records if r["kind"] == "contact_point"}
        self.assertEqual(orgs["Badminton Club Paris 7"], contacts["bcp7@orange.fr"]["organization_id"])
        self.assertEqual("org-mairie7", contacts["sports7@paris.fr"]["organization_id"])
        self.assertTrue(all(d["admitted"] for d in harvest_admission(self.root, result)),
                        harvest_admission(self.root, result))

    def test_clubs_are_staged_when_the_site_name_is_not_on_the_page(self):
        import research_site_harvest as harvest
        jev = Fake({"contact@tc-invalides": ("org_1", 0.93)})
        result = harvest.harvest(self.root, run_id="jev-run", operation_id="h2",
                                 organization_name="Ville de Nulle Part", organization_key="org-nowhere",
                                 url=self.HOME, fetch=self.fetch, max_pages=1, jev_ask=jev)
        self.assertIsNone(result["identity"])
        records = self.store.list_run(self.root, "jev-run")["records"]
        orgs = {r["entity_key"] for r in records if r["kind"] == "organization"}
        self.assertNotIn("org-nowhere", orgs)
        values = {r["data"]["value"] for r in records if r["kind"] == "contact_point"}
        self.assertIn("bcp7@orange.fr", values)
        self.assertNotIn("sports7@paris.fr", values)  # the site's own route waits for its identity
        self.assertTrue(all(d["admitted"] for d in harvest_admission(self.root, result)))

    def test_a_sure_out_of_scope_site_is_not_staged(self):
        result = self.harvest(Fake({"Research target": ("out_of_scope", 0.97)}),
                              question="Research target: natural-grass pitch operators in Paris.")
        self.assertTrue(result["screen"]["stop"])
        self.assertEqual([], result["routes"])
        self.assertTrue(result["next_action"].startswith("SCREENED_OUT"))
        self.assertEqual([], self.store.list_run(self.root, "jev-run")["records"])


def harvest_admission(root, result):
    import research_site_harvest as harvest
    return harvest.admission_for(root, "jev-run", result["record_ids"])


class GuideNames(unittest.TestCase):
    """Real shapes from a town's association guide (Vincennes, 2025)."""

    def names(self, text):
        return [c["name"] for c in directory.candidates(text, text.split()[-1])]

    def test_names_are_trimmed_or_left_out_never_guessed(self):
        self.assertEqual(["ASSOCIATION DE DANSE IRITCHA"],
                         self.names("ASSOCIATION DE DANSE IRITCHA Cours de danse classique. a@b.fr"))
        self.assertEqual(["SPORT PICKLEBALL CLUB VINCENNES"],
                         self.names("PAGE 91 SPORT PICKLEBALL CLUB VINCENNES Activités : c@d.fr"))
        self.assertEqual(["The Irish Club"], self.names("Irlande. The Irish Club publie g@h.fr"))
        # A bare "Société"/"Comité" or a venue names no club: the address stays unassigned.
        self.assertEqual([], self.names("Société des amis de Vincennes. Restauration i@j.fr"))
        self.assertEqual([], self.names("Stade Léo Lagrange - route de la Pyramide k@l.fr"))

    def test_a_filled_name_keeps_the_club_in_the_quote(self):
        import research_grading as grading
        text = "PICKLEBALL CLUB VINCENNES Activités : gymnase. Burton Webb pickleballvincennes@gmail.com"
        route = {"type": "email", "value": "pickleballvincennes@gmail.com",
                 "quote": "PICKLEBALL CLUB VINCENNES Activités : gymnase. Burton Webb pickleballvincennes@gmail.com"[0:]}
        route["quote"] = text[:text.find(" Activités")] + text[text.find(" Activités"):]
        pages.fill_names(text, [route], "a", Fake({"pickleball": ("name_1", 0.95)}),
                         has_name=lambda e, q: bool(grading._name_in_quote(e, [("", q)])))
        self.assertEqual("Burton Webb", route["person_name"])
        self.assertIn("PICKLEBALL CLUB VINCENNES", route["quote"])
