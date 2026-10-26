"""General reading fixes from the first Bruce benchmark (no task-specific words).

- A page's own names (title, og:site_name, JSON-LD) are readable evidence.
- Block boundaries survive, so a role on the next card is not this address's role.
- A role or organisation word is never a person's name.
- The brief's wanted roles and exclusions outrank a scene pack.
- A name with a reference number or several parts tries every part.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import research_evidence as evidence  # noqa: E402
import research_grading as grading  # noqa: E402
import research_scene as scene  # noqa: E402
import research_site_harvest as harvest  # noqa: E402

PAGE = """<html><head><title>Northfield Learning Trust | Home</title>
<meta property="og:site_name" content="Northfield Learning Trust">
<script type="application/ld+json">{"@type": "Organization", "name": "Northfield Trust"}</script>
</head><body><img alt="logo">
<div class="card"><h3>A. Smith</h3><p>Office Assistant</p><p>office.one@northfield.example</p></div>
<div class="card"><h3>B. Jones</h3><p>Estates Manager</p><p>b.jones@northfield.example</p></div>
</body></html>"""


class PageNamesTests(unittest.TestCase):
    def test_the_names_a_page_gives_itself_are_readable(self):
        text = evidence.readable_text("rendered_html", PAGE)
        self.assertIn("Northfield Learning Trust", text.splitlines()[0])
        self.assertIn("Northfield Trust", text)

    def test_blocks_stay_lines_and_quotes_still_match_across_them(self):
        text = evidence.readable_text("rendered_html", PAGE)
        self.assertIn("Office Assistant\noffice.one@northfield.example", text)
        self.assertEqual(evidence._norm(text), evidence._norm(text.replace("\n", " ")))


RULES = {"roles": {"decision": ["head of estates"], "operational": ["estates manager"],
                   "entry": ["office"], "excluded": ["finance"]}}


class LineBoundRolesTests(unittest.TestCase):
    def test_the_next_cards_role_is_not_this_addresss_role(self):
        text = evidence.readable_text("rendered_html", PAGE)
        cls, words, _ = scene.nearest_title(text, "office.one@northfield.example", RULES)
        self.assertNotEqual("operational", cls)
        cls, words, _ = scene.nearest_title(text, "b.jones@northfield.example", RULES)
        self.assertEqual(("operational", "estates manager"), (cls, words))


class NotANameTests(unittest.TestCase):
    def test_role_words_spelled_by_an_address_are_not_a_person(self):
        quote = [("a", "Site Manager site.manager@school.example")]
        self.assertEqual("", grading._name_in_quote("site.manager@school.example", quote))
        quote = [("a", "Contact Jane Doe jane.doe@school.example")]
        self.assertEqual("Jane Doe", grading._name_in_quote("jane.doe@school.example", quote))


class BriefOutranksPackTests(unittest.TestCase):
    def test_a_pack_title_the_user_did_not_ask_for_is_an_entry(self):
        rules = scene._the_users_words(
            {"source": "shipped:x", "scene": "x", "roles": {
                "decision": ["chief executive", "head of estates"],
                "operational": ["finance manager", "estates manager"],
                "entry": ["bookings"], "excluded": []}},
            ["estates", "caretaker"], ["safeguarding"])
        self.assertEqual("entry", scene.role_class("chief executive", rules))
        self.assertEqual("entry", scene.role_class("finance manager", rules))
        self.assertEqual("decision", scene.role_class("head of estates", rules))
        self.assertEqual("operational", scene.role_class("caretaker", rules))
        self.assertEqual("excluded", scene.role_class("safeguarding lead", rules))


class NamePartsTests(unittest.TestCase):
    def test_every_part_and_no_reference_number(self):
        variants = harvest.name_variants("0412 | Riverside Primary School")
        self.assertIn("Riverside Primary School", variants)
        self.assertIn("Oak Hill Academy", harvest.name_variants("12 Oak Hill Academy"))


class EmailShapeTests(unittest.TestCase):
    def test_glued_or_broken_domains_are_not_routes(self):
        values = [r["value"] for r in harvest.extract_routes(
            "Write to office@riverside-school.org, info@club..riverside.org or "
            "desk@-bad.riverside.org. Mail office@club.co.ukTel 01234 567890")]
        self.assertEqual(["office@riverside-school.org"], [v for v in values if "@" in v])


if __name__ == "__main__":
    unittest.main()
