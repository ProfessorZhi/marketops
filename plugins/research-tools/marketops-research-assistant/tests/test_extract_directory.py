"""A saved list page becomes records in one local read, with quotable evidence.

A synthetic annuaire only: no network, no provider, no credentials.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import research_extract as extract  # noqa: E402
import research_lite_store as store  # noqa: E402
import research_mcp as mcp  # noqa: E402
from research_evidence import _text  # noqa: E402

PAGE = """<html><body>
<h1>Annuaire des associations sportives du 13e</h1>
<div class="entry"><h3>Club de Judo Paris 13</h3>
<p>Discipline(s) pratiquée(s) : Judo</p>
<p>Équipement(s) utilisé(s) : Gymnase Caillaux (3, rue Caillaux)</p>
<p>Nom de la personne à contacter : Mme Christine BILLEBAUD</p>
<p>Adresse mail : christine.billebaud@laposte.net</p>
<p>Numéro de téléphone : 01 45 65 15 86</p></div>
<div class="entry"><h3>Association Sportive et Culturelle du 13ème arrondissement (A.S.C. 13)</h3>
<p>Discipline(s) pratiquée(s) : Aïkido</p>
<p>Équipement(s) utilisé(s) : Gymnase Charcot (88, rue du Chevaleret)</p>
<p>Nom de la personne à contacter : M. Gilles BOUCARD</p>
<p>Adresse mail : aikido.asc13@gmail.com</p>
<p>Site internet : www.asc13.fr</p></div>
<div class="entry"><h3>ASJJ</h3>
<p>Discipline(s) pratiquée(s) : Judo</p>
<p>Équipement(s) utilisé(s) : Dojo Dunois</p>
<p>Nom de la personne à contacter : M. Jean DUPONT</p>
<p>Adresse mail : jean.dupont@orange.fr</p>
<p>Numéro de téléphone : 06 12 34 56 78</p></div>
</body></html>""".encode("utf-8")

PROSE = ("<html><body><p>Le club ouvre ses portes le samedi. Contact : voir le "
         "secrétariat. Nous organisons des stages toute l'année.</p></body></html>").encode("utf-8")


def page_text() -> str:
    return extract.page_text("rendered_html", PAGE)


class ExtractPageTests(unittest.TestCase):
    def test_records_carry_names_people_and_addresses(self):
        result = extract.extract(page_text())
        self.assertEqual(3, len(result["records"]))
        names = [record["organization"] for record in result["records"]]
        self.assertEqual(["Club de Judo Paris 13",
                          "Association Sportive et Culturelle du 13ème arrondissement",
                          "ASJJ"], names)
        self.assertEqual("Mme Christine BILLEBAUD", result["records"][0]["person"])
        self.assertEqual("christine.billebaud@laposte.net", result["records"][0]["email"])
        self.assertEqual("www.asc13.fr", result["records"][1]["website"])
        self.assertEqual(3, sum(1 for record in result["records"] if record["email"]))

    def test_every_quote_is_a_span_of_the_page_the_gate_reads(self):
        normalized = _text({"kind": "rendered_html", "text": PAGE.decode("utf-8")})
        bundles = extract.records_to_bundles(page_text(), extract.extract(page_text())["records"],
                                             "art-page")
        quotes = [claim for bundle in bundles for item in bundle
                  for claim in item["facts"].get("claims", [])]
        self.assertTrue(quotes)
        for claim in quotes:
            self.assertTrue(12 <= len(claim["quote"]) <= 600, claim)
            self.assertIn(" ".join(claim["quote"].casefold().split()), normalized, claim)

    def test_a_record_is_staged_as_organisation_person_and_address(self):
        bundles = extract.records_to_bundles(page_text(), extract.extract(page_text())["records"],
                                             "art-page")
        kinds = [[item["kind"] for item in bundle] for bundle in bundles]
        self.assertEqual([["organization", "person", "contact_point"]] * 3, kinds)
        organization, person, contact = bundles[0]
        self.assertEqual(organization["entity_key"], contact["facts"]["organization_id"])
        self.assertEqual(person["entity_key"], contact["facts"]["person_id"])
        self.assertEqual("email", contact["facts"]["type"])

    def test_each_organisation_carries_an_identity_claim_the_gate_accepts(self):
        from research_delivery import _quote_supports
        bundles = extract.records_to_bundles(
            page_text(), extract.extract(page_text())["records"], "art-page")
        for bundle in bundles:
            organization = bundle[0]
            name, claims = organization["facts"]["name"], organization["facts"].get("claims", [])
            identity = [claim for claim in claims if claim["field"] == "identity"]
            if len(name.split()) == 1 and len(name) < 6:
                # "ASJJ" is an alias, not an identity: the gate refuses a single
                # short token, so no quote on the page can carry the decision.
                self.assertEqual(["name"], [claim["field"] for claim in claims], claims)
                continue
            # field=name alone is refused at delivery with
            # MISSING_REQUIRED_IDENTITY; the annuaire already printed the name.
            self.assertTrue(identity, claims)
            self.assertTrue(_quote_supports(identity[0]["quote"], name, distinctive=True),
                            identity[0]["quote"])

    def test_a_person_and_address_quote_carry_the_club_name(self):
        # A shared annuaire page: delivery refuses a person or address whose own
        # quote does not also name the club (ORGANIZATION_PROVENANCE_MISMATCH).
        bundles = extract.records_to_bundles(
            page_text(), extract.extract(page_text())["records"], "art-page")
        for bundle in bundles:
            organization, person, contact = bundle[:3]
            name = organization["facts"]["name"]
            for item, value in ((person, person["facts"]["name"]),
                                (contact, contact["facts"]["value"])):
                quotes = [claim["quote"] for claim in item["facts"]["claims"]]
                self.assertTrue(any(name in quote and value in quote for quote in quotes),
                                (name, item["kind"], quotes))

    def test_prose_is_refused_with_the_reason(self):
        from research_evidence import decode_material, readable_text
        result = extract.extract(readable_text("rendered_html", decode_material(PROSE)))
        self.assertEqual([], result["records"])
        self.assertTrue(any(warning.startswith("DIRECTORY_NO_REPEATING_LABEL")
                            for warning in result["warnings"]))

    def test_fields_narrow_the_output(self):
        result = extract.extract(page_text(), labels=["organization", "email"])
        self.assertTrue(all(not record.get("person") for record in result["records"]))
        with self.assertRaises(ValueError):
            extract.extract(page_text(), labels=["kind"])


class CorrespondentLayoutTests(unittest.TestCase):
    """A club finder that prints "Correspondant :" and "Lieu des cours :".

    The mairie 14 annuaire names the club on its own line, then the venue it
    uses, then the person to write to. Read as an address-first page, the club
    name came out as "rue du Moulin Vert".
    """

    PAGE = """<html><body>
<p>ARMORIQUE</p><p>Lieu des cours : 5, rue du Moulin Vert</p>
<p>Correspondant : Jacqueline GOUJON</p><p>Adresse postale : 5, rue du Moulin Vert</p>
<p>Tél : 01 45 40 43 90</p><p>Adresse du site : www.armoriquesports.free.fr</p>
<p>COEGF</p><p>Lieu des cours : Institut du Judo 21-23 avenue Porte de Châtillon</p>
<p>Correspondant : Florian Mégard</p><p>Adresse postale : 3 rue des jonquilles 75014 PARIS</p>
<p>Tél : 06 61 66 66 17</p><p>Adresse du site : www.coegf.fr</p>
<p>WTEO PARIS 14</p><p>Lieu des cours : Centres Paris Anim’ Vercingétorix</p>
<p>Correspondant : Olivier Rullé</p><p>Adresse postale : 20, avenue Marc Sangnier</p>
<p>Tél : 01 45 41 00 96</p><p>Adresse du site : www.wteoparis14.org</p>
</body></html>""".encode("utf-8")

    def records(self):
        return extract.extract(extract.page_text("rendered_html", self.PAGE))["records"]

    def test_the_club_name_excludes_the_address_of_the_person(self):
        self.assertEqual(["ARMORIQUE", "COEGF", "WTEO PARIS 14"],
                         [record["organization"] for record in self.records()])

    def test_the_person_venue_and_website_are_read_from_their_own_labels(self):
        first = self.records()[0]
        self.assertEqual("Jacqueline GOUJON", first["person"])
        self.assertEqual("5, rue du Moulin Vert", first["venue"])
        self.assertEqual("5, rue du Moulin Vert", first["address"])
        self.assertEqual("www.armoriquesports.free.fr", first["website"])
        self.assertEqual("01 45 40 43 90", first["phone"])

    def test_a_street_is_never_taken_for_a_name(self):
        for record in self.records():
            self.assertFalse(record["organization"].lower().startswith(
                ("rue", "avenue", "place", "boulevard", "chemin")), record)


class DirectoryToolTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / "data"
        env = patch.dict(os.environ, {"MARKETOPS_RESEARCH_DATA_ROOT": str(self.root),
                                      "MARKETOPS_RESEARCH_ALLOW_NETWORK": "0"})
        env.start()
        self.addCleanup(env.stop)
        mcp.start("run-dir", "Paris indoor venues", "Named contacts for Paris venues", "test")
        store.save_artifact(self.root, "run-dir", "art-page", "fixture",
                            "https://example.org/annuaire", PAGE, "rendered_html")

    def test_one_call_returns_records_and_saves_the_extraction(self):
        reply = mcp.directory_extract("run-dir", "art-page")
        self.assertEqual("DIRECTORY_EXTRACTED_NOT_VERIFIED", reply["status"])
        self.assertEqual(3, reply["records"])
        self.assertEqual(3, reply["with_email"])
        self.assertEqual(9, reply["items_total"])
        self.assertIsNone(reply["next_offset"])
        saved = store.read_artifact(self.root, "run-dir", reply["artifact_id"])
        records = json.loads(saved["content"])["records"]
        self.assertEqual(3, len(records))
        self.assertEqual("ASJJ", records[2]["organization"])
        self.assertEqual(reply["artifact_id"],
                         mcp.directory_extract("run-dir", "art-page")["artifact_id"])

    def test_stage_writes_gradeable_candidates(self):
        reply = mcp.directory_extract("run-dir", "art-page", stage=True)
        self.assertEqual(9, reply["staged"]["created"])
        listed = store.list_run(self.root, "run-dir", limit=50)
        kinds = [record["kind"] for record in listed["records"]]
        self.assertEqual(3, kinds.count("organization"))
        self.assertEqual(3, kinds.count("person"))
        self.assertEqual(3, kinds.count("contact_point"))

    def test_pagination_never_breaks_a_record_apart(self):
        first = mcp.directory_extract("run-dir", "art-page", offset=0, limit=3)
        self.assertEqual(3, first["returned"])
        self.assertEqual(1, first["next_offset"])
        second = mcp.directory_extract("run-dir", "art-page", offset=first["next_offset"], limit=6)
        self.assertEqual(6, second["returned"])
        self.assertIsNone(second["next_offset"])

    def test_an_image_artifact_is_refused(self):
        store.save_artifact(self.root, "run-dir", "art-shot", "fixture",
                            "https://example.org/shot", b"\x89PNG\r\n\x1a\nfake", "image")
        with self.assertRaises(ValueError) as caught:
            mcp.directory_extract("run-dir", "art-shot")
        self.assertIn("DIRECTORY_ARTIFACT_IS_IMAGE", str(caught.exception))


if __name__ == "__main__":
    unittest.main()
