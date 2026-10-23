"""RC6: a contact on an organisation's own site binds to it without repeating its name.

The P0 binding rule asked for one quote carrying both the organisation's full
name and the contact literal. A hall's "Bookings: x@y or call 01245 ..." block
almost never repeats the trust's name, so every real booking address on the
organisation's own pages was rejected and the Essex trial delivered 0/3.

These controls pin the third binding shape (same non-directory host on which
only this organisation has an admitted identity quote), keep the directory and
shared-host negatives, count venues joined by an admitted relationship row, and
let a fresh Brief state a minimum without the legacy unit review.

Synthetic, offline material only.
"""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import research_delivery as delivery  # noqa: E402
import research_lite_store as store  # noqa: E402

RUN = "rc6-site"
ABOUT = "<p>Alpha Hall is managed by the Alpha Hall Trust, registered charity 300123.</p>"
CONTACT = "<p>Bookings: alphahallbookings@gmail.com or call 01245 223456.</p>"
DIRECTORY = ("<p>Alpha Hall Trust runs Alpha Hall. Beta Hall Committee runs Beta Hall.</p>"
             "<p>Enquiries: hire@betahall.example or 01245 000111</p>")


def org(record_id, key, name, artifact, quote):
    return (record_id, "entities", "organization", key,
            {"name": name, "claims": [{"field": "identity", "artifact_id": artifact,
                                       "quote": quote}]}, [artifact])


def contact(record_id, key, org_key, value, artifact, quote, channel="email"):
    return (record_id, "routes", "contact_point", key,
            {"organization_id": org_key, "type": channel, "value": value,
             "claims": [{"field": "value", "artifact_id": artifact, "quote": quote}]},
            [artifact])


class SameSiteBindingCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / "data"
        store.start_run(self.root, RUN, "synthetic", "venue pool", "rc6-rules")

    def build(self, pages, rows):
        for artifact, (url, html) in pages.items():
            store.save_artifact(self.root, RUN, artifact, "fixture", url,
                                html.encode(), "rendered_html")
        for row in rows:
            store.put_candidate(self.root, RUN, *row)
        sheets, _manifest = delivery.build_delivery(self.root, RUN)
        return {sheet.name: sheet.rows for sheet in sheets}

    def contact_values(self, sheets):
        return [(row[1], row[5]) for row in sheets["联系人路线"]]

    def test_contact_page_on_the_organisations_own_host_binds(self):
        sheets = self.build(
            {"about": ("https://www.alphahall.example/about", ABOUT),
             "contact": ("https://alphahall.example/contact", CONTACT)},
            [org("o1", "org-a", "Alpha Hall Trust", "about",
                 "Alpha Hall is managed by the Alpha Hall Trust"),
             contact("c1", "cp-e", "org-a", "alphahallbookings@gmail.com", "contact",
                     "Bookings: alphahallbookings@gmail.com or call 01245 223456."),
             contact("c2", "cp-p", "org-a", "01245 223456", "contact",
                     "Bookings: alphahallbookings@gmail.com or call 01245 223456.",
                     channel="phone")])
        self.assertEqual(sorted(self.contact_values(sheets)),
                         [("org-a", "01245 223456"), ("org-a", "alphahallbookings@gmail.com")])
        self.assertEqual(sheets["客户总表"][0][5], 2)

    def test_host_shared_by_two_organisations_does_not_bind(self):
        sheets = self.build(
            {"page": ("https://halls.example.org/essex", DIRECTORY)},
            [org("o1", "org-a", "Alpha Hall Trust", "page", "Alpha Hall Trust runs Alpha Hall."),
             org("o2", "org-b", "Beta Hall Committee", "page",
                 "Beta Hall Committee runs Beta Hall."),
             contact("c1", "cp-1", "org-a", "hire@betahall.example", "page",
                     "Enquiries: hire@betahall.example or 01245 000111")])
        self.assertEqual(self.contact_values(sheets), [])

    def test_directory_shaped_path_does_not_bind(self):
        sheets = self.build(
            {"page": ("https://halls.example.org/directory/essex", DIRECTORY)},
            [org("o1", "org-a", "Alpha Hall Trust", "page", "Alpha Hall Trust runs Alpha Hall."),
             contact("c1", "cp-1", "org-a", "hire@betahall.example", "page",
                     "Enquiries: hire@betahall.example or 01245 000111")])
        self.assertEqual(self.contact_values(sheets), [])

    def test_contact_from_another_host_does_not_bind(self):
        sheets = self.build(
            {"about": ("https://alphahall.example/about", ABOUT),
             "other": ("https://elsewhere.example/page", DIRECTORY)},
            [org("o1", "org-a", "Alpha Hall Trust", "about",
                 "Alpha Hall is managed by the Alpha Hall Trust"),
             contact("c1", "cp-1", "org-a", "hire@betahall.example", "other",
                     "Enquiries: hire@betahall.example or 01245 000111")])
        self.assertEqual(self.contact_values(sheets), [])

    def test_contact_literal_must_still_be_quoted(self):
        sheets = self.build(
            {"about": ("https://alphahall.example/about", ABOUT),
             "contact": ("https://alphahall.example/contact", CONTACT)},
            [org("o1", "org-a", "Alpha Hall Trust", "about",
                 "Alpha Hall is managed by the Alpha Hall Trust"),
             contact("c1", "cp-1", "org-a", "ceo@alphahall.example", "contact",
                     "Bookings: alphahallbookings@gmail.com or call 01245 223456.")])
        self.assertEqual(self.contact_values(sheets), [])

    def test_venue_joined_by_relationship_is_counted(self):
        quote = "Alpha Hall is managed by the Alpha Hall Trust"
        sheets = self.build(
            {"about": ("https://alphahall.example/about", ABOUT)},
            [org("o1", "org-a", "Alpha Hall Trust", "about", quote),
             ("v1", "entities", "venue", "venue-a",
              {"name": "Alpha Hall", "claims": [{"field": "identity", "artifact_id": "about",
                                                 "quote": quote}]}, ["about"]),
             ("r1", "entities", "relationship", "rel-a",
              {"organization_id": "org-a", "venue_id": "venue-a",
               "relationship_type": "operator",
               "claims": [{"field": "relationship", "artifact_id": "about",
                           "quote": quote}]}, ["about"])])
        self.assertEqual(sheets["客户总表"][0][4], 1)
        self.assertEqual(sheets["场地与资产"][0][2], "org-a")


class FreshBriefMinimumCase(unittest.TestCase):
    def test_fresh_brief_minimum_takes_its_target_unit(self):
        import os
        import research_mcp
        with tempfile.TemporaryDirectory() as tmp:
            previous = os.environ.get("MARKETOPS_RESEARCH_DATA_ROOT")
            os.environ["MARKETOPS_RESEARCH_DATA_ROOT"] = tmp
            try:
                result = research_mcp.start(
                    "rc6-brief", "synthetic", "three venues", "rc6-rules",
                    {"record_type": "research_brief", "target_kind": "venue",
                     "goal": "pool", "require_named_person": False,
                     "minimum_targets": 3,
                     "evidence_requirements": {"venue": ["identity"]}})
            finally:
                if previous is None:
                    os.environ.pop("MARKETOPS_RESEARCH_DATA_ROOT", None)
                else:
                    os.environ["MARKETOPS_RESEARCH_DATA_ROOT"] = previous
        self.assertEqual(result["status"], "RUN_CREATED")
        self.assertEqual(result["next_status"], "MORE_RESEARCH_REQUIRED")


if __name__ == "__main__":
    unittest.main()
