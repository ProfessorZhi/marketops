"""RC6: one call harvests an organisation's own site into gate-checked candidates.

The fetch callback stands in for the managed known-public-URL step; it saves
real rendered HTML into the Run exactly as the fallback chain does.

Synthetic, offline material only.
"""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import research_lite_store as store  # noqa: E402
import research_site_harvest as harvest  # noqa: E402

RUN = "rc6-harvest"
HOME = "https://www.alphahall.example/"
SITE = {
    HOME: ('<html><body><h1>Welcome</h1><p>Alpha Hall is run by the Alpha Hall Trust, '
           'a registered charity.</p><a href="/hire-rates">Hire &amp; rates</a> '
           '<a href="/contact-us">Contact us</a> <a href="/gallery">Gallery</a> '
           '<a href="https://facebook.com/alpha">Facebook</a></body></html>'),
    HOME + "hire-rates": "<html><body><p>Main Hall: &pound;16.00 per hour.</p></body></html>",
    HOME + "contact-us": ("<html><body><p>Bookings Secretary: Jane Smith, "
                          "bookings@alphahall.example or call 01245 223456.</p>"
                          "<p>Chair of Trustees: chair@alphahall.example</p></body></html>"),
}


class SiteHarvestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / "data"
        store.start_run(self.root, RUN, "synthetic", "venue pool", "rc6-rules")
        self.site, self.calls = dict(SITE), []

    def fetch(self, url, operation_id):
        self.calls.append(url)
        if url not in self.site:
            return {"status": "HOLD", "reason_code": "SOURCE_ACCESS_BLOCKED"}
        artifact = "art-" + operation_id
        store.save_artifact(self.root, RUN, artifact, "crawl4ai", url,
                            self.site[url].encode(), "rendered_html")
        return {"status": "SUCCESS_CANDIDATE", "artifact_ids": [artifact],
                "primary_artifact_ids": [artifact]}

    def run_harvest(self, **overrides):
        args = dict(run_id=RUN, operation_id="h1", organization_name="Alpha Hall Trust",
                    organization_key="org-alpha", url=HOME, fetch=self.fetch, max_pages=4)
        args.update(overrides)
        return harvest.harvest(self.root, **args)

    def test_own_site_routes_are_quoted_staged_and_admitted(self):
        result = self.run_harvest()
        self.assertEqual([HOME, HOME + "hire-rates", HOME + "contact-us"], self.calls)
        self.assertEqual({("email", "bookings@alphahall.example"),
                          ("email", "chair@alphahall.example"), ("phone", "01245 223456")},
                         {(r["type"], r["value"]) for r in result["routes"]})
        self.assertTrue(all(r["value"] in r["quote"] for r in result["routes"]))
        self.assertIn("Bookings Secretary", result["routes"][0]["context"])
        self.assertEqual(4, len(result["admission"]))
        self.assertTrue(all(d["admitted"] for d in result["admission"]))
        self.assertEqual("ASSIGN_ROLE_AND_GRADE_FROM_CONTEXT", result["next_action"])

    def test_rerun_is_idempotent(self):
        first = self.run_harvest()
        second = self.run_harvest(operation_id="h2")
        self.assertEqual(4, len(first["written"]))
        self.assertEqual([], second["written"])
        self.assertEqual(3, len(second["admission"]))
        records = store.list_run(self.root, RUN)["records"]
        self.assertEqual(4, len(records))

    def test_unreachable_home_page_stops_without_wandering(self):
        result = self.run_harvest(url="https://www.alphahall.example/missing")
        self.assertEqual(1, len(self.calls))
        self.assertEqual([], result["written"])
        self.assertIsNone(result["identity"])
        self.assertTrue(result["next_action"].startswith("SITE_UNREACHABLE: SOURCE_ACCESS_BLOCKED"))

    def test_name_absent_from_site_writes_nothing(self):
        result = self.run_harvest(organization_name="Beta Hall Committee")
        self.assertIsNone(result["identity"])
        self.assertEqual([], result["written"])
        self.assertTrue(result["next_action"].startswith("NAME_NOT_FOUND_ON_SITE"))
        self.assertTrue(result["routes"])  # still reported for the Agent to read

    def test_write_false_only_reports(self):
        result = self.run_harvest(write=False)
        self.assertEqual([], result["written"])
        self.assertEqual([], store.list_run(self.root, RUN)["records"])

    def test_links_stay_on_host_and_skip_assets(self):
        html = ('<a href="/contact">Contact</a><a href="https://other.example/contact">x</a>'
                '<a href="/logo.png">team</a><a href="mailto:a@b.example">mail</a>'
                '<a href="/staff">Our staff</a><a href="/news">News</a>')
        self.assertEqual([HOME + "contact", HOME + "staff"],
                         harvest.candidate_links(HOME, html, 5))

    def test_mailto_link_address_is_quoted_and_obfuscated_is_not_decoded(self):
        self.site[HOME + "contact-us"] = (
            '<p>Write to us: <a href="mailto:secretary@alphahall.example">email the secretary</a>'
            ' or <span class="__cf_email__" data-cfemail="ab1234">[email protected]</span></p>')
        result = self.run_harvest()
        routes = {r["value"]: r for r in result["routes"]}
        self.assertIn("secretary@alphahall.example", routes)
        self.assertIn("email the secretary", routes["secretary@alphahall.example"]["quote"])
        self.assertEqual(1, result["obfuscated_emails_not_decoded"])
        admitted = {d["record_id"]: d["admitted"] for d in result["admission"]}
        self.assertTrue(all(admitted.values()), result["admission"])

    def test_short_numbers_are_not_phones(self):
        routes = harvest.extract_routes("Registered charity 300123, founded 1921, call 01245 223456 today.")
        self.assertEqual([("phone", "01245 223456")], [(r["type"], r["value"]) for r in routes])

    def test_a_site_builders_sample_address_is_not_a_route(self):
        # Wix prints "E-mail info@monsite.fr" on a finished contact page; the
        # association's real address follows it.
        routes = harvest.extract_routes(
            "E-mail info@monsite.fr ecrire@actisce.org Tel : 01 45 81 13 13")
        self.assertEqual([("email", "ecrire@actisce.org"), ("phone", "01 45 81 13 13")],
                         [(r["type"], r["value"]) for r in routes])

    def test_http_site_is_fetched_over_https(self):
        result = self.run_harvest(url="http://www.alphahall.example/")
        self.assertEqual(HOME, self.calls[0])
        self.assertTrue(result["identity"])

    def test_a_site_without_a_certificate_is_read_as_published(self):
        # oms1paris.free.fr answers 200 on :80 and refuses :443. Lifting the
        # published http URL to https made the whole site unreachable.
        http_home = "http://www.alphahall.example/"
        self.site = {http_home: SITE[HOME]}
        result = self.run_harvest(url=http_home)
        self.assertEqual([HOME, http_home], self.calls[:2])
        self.assertEqual(http_home, result["pages"][0]["url"])
        self.assertTrue(result["identity"])

    def test_links_out_of_an_http_page_stay_on_http(self):
        http_home = "http://www.alphahall.example/"
        html = ('<a href="/contact">Contact</a>'
                '<a href="https://www.alphahall.example/staff">Staff</a>')
        self.assertEqual([http_home + "contact", "https://www.alphahall.example/staff"],
                         harvest.candidate_links(http_home, html, 5))

    def test_bounds_are_refused_with_a_fix(self):
        with self.assertRaisesRegex(ValueError, "HARVEST_MAX_PAGES_INVALID"):
            self.run_harvest(max_pages=0)
        with self.assertRaisesRegex(ValueError, "HARVEST_URL_INVALID"):
            self.run_harvest(url="ftp://alphahall.example")


if __name__ == "__main__":
    unittest.main()
