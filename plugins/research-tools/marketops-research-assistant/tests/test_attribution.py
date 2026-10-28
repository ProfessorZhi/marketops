"""Same-block attribution: a page that lists several organisations must not hand every
address on it to each of them.

Run: python -m unittest tests.test_attribution -v
"""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import mo_workspace as ws  # noqa: E402


def contact(value: str, quote: str, *, email_on_org: str = "no", email_site: str = "other.example",
            kind: str = "email") -> dict:
    return {"kind": kind, "value": value, "quote": quote, "page_url": "https://portal.example/list",
            "email_site": email_site, "email_on_org": email_on_org, "found_by": "crawl4ai"}


class AttributableTest(unittest.TestCase):
    def test_own_site_keeps_every_address(self):
        keep, held = ws.split_attribution(
            [contact("a@alpha.example", ""), contact("info@alpha.example", "")],
            org="Club Alpha", org_site="alpha.example", on_org="yes")
        self.assertEqual((len(keep), len(held)), (2, 0))

    def test_portal_page_keeps_only_the_organisation_named_next_to_the_address(self):
        rows = [
            contact("president@alpha.example", "Club Alpha / President / president@alpha.example"),
            contact("president@beta.example", "Club Beta / President / president@beta.example"),
            contact("contact@portal.example", "Nous contacter / contact@portal.example"),
        ]
        keep, held = ws.split_attribution(rows, org="Club Alpha", org_site="alpha.example", on_org="no")
        self.assertEqual([c["value"] for c in keep], ["president@alpha.example"])
        self.assertEqual({c["value"] for c in held},
                         {"president@beta.example", "contact@portal.example"})

    def test_address_on_the_organisation_domain_is_kept_even_on_another_page(self):
        rows = [contact("direction@alpha.example", "Federation / Club Alpha / direction",
                        email_on_org="yes", email_site="alpha.example")]
        keep, held = ws.split_attribution(rows, org="Club Alpha", org_site="alpha.example", on_org="no")
        self.assertEqual((len(keep), len(held)), (1, 0))

    def test_a_quote_naming_another_organisation_is_held(self):
        rows = [contact("president@beta.example", "Club Beta / President / president@beta.example")]
        keep, held = ws.split_attribution(rows, org="Club Alpha", org_site="alpha.example", on_org="no")
        self.assertEqual((len(keep), len(held)), (0, 1))

    def test_phone_without_the_organisation_near_it_is_held(self):
        rows = [contact("01 02 03 04 05", "Standard / 01 02 03 04 05", kind="phone")]
        keep, held = ws.split_attribution(rows, org="Club Alpha", org_site="alpha.example", on_org="no")
        self.assertEqual((len(keep), len(held)), (0, 1))

    def test_same_name_in_another_city_is_kept_but_the_domain_is_not_the_organisations(self):
        # the portal fix cannot tell two same-named institutions apart; the address is kept
        # only when the quote names it, and email_on_org stays "no" so grading reviews it.
        rows = [contact("secretariat@charlemagne.example",
                        "College Charlemagne / Secretariat / secretariat@charlemagne.example")]
        keep, _held = ws.split_attribution(rows, org="College Charlemagne",
                                           org_site="pia.example", on_org="no")
        self.assertEqual(len(keep), 1)
        self.assertEqual(keep[0]["email_on_org"], "no")


class SaveUnattributedTest(unittest.TestCase):
    def test_rows_are_kept_once_per_organisation_and_address(self):
        with tempfile.TemporaryDirectory() as tmp:
            pdir = Path(tmp)
            rows = [contact("x@beta.example", "Club Beta"), contact("x@beta.example", "Club Beta")]
            first = ws.save_unattributed(pdir, rows, "org-1", "Club Alpha", "deep1")
            second = ws.save_unattributed(pdir, rows, "org-1", "Club Alpha", "deep1")
            self.assertEqual(first, 1)
            self.assertEqual(second, 0)
            saved = ws.read_csv(pdir / "unattributed.csv")
            self.assertEqual(len(saved), 1)
            self.assertEqual(saved[0]["org_id"], "org-1")
            self.assertIn("does not name it", saved[0]["why"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
