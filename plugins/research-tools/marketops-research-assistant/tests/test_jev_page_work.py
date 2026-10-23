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
    "Tennis Club Invalides • Stade Émile Anthoine • contact@tc-invalides.fr\n"
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
