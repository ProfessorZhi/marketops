"""The fixes from the Paris 11e run on 0.7.4 (paris11-074, 0.7.5).

harvest named 181 of 188 rows "Haut" and gave each the directory's own mailbox; pooled rows
from one page shared all its addresses, and a saved page made pooling skip a row's own; a
merged club came back when pooled again; a researcher was told lane_finished at minute three;
fifteen near-duplicates were merged by hand; two workers waited on a lead's call for a source
under 5%; a namesake in another town and a federation's home page became websites; a site's
ROT13 addresses were graded as fake domains; a whole "Président : NAME, address" line landed in
person and role; and the workbook went out without one spot check.

Run: python -m unittest tests.test_075_fixes -v
"""
from __future__ import annotations

import codecs
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
if "MARKETOPS_PROJECTS" not in os.environ:   # alone; under discover test_core set these already
    TMP = tempfile.mkdtemp()
    os.environ.update(MARKETOPS_HOME=TMP + "/home", MARKETOPS_PROJECTS=TMP + "/projects",
                      MARKETOPS_HEALTH="0", MARKETOPS_KNOWLEDGE="0")
for k in ("SERPER_API_KEY", "SERPAPI_API_KEY", "DEEPSEEK_API_KEY", "TINYFISH_API_KEY", "TYPESAFE_API_KEY"):
    os.environ.pop(k, None)

import mo_export  # noqa: E402
import mo_pages  # noqa: E402
import mo_providers  # noqa: E402
import mo_run  # noqa: E402
import mo_tasks  # noqa: E402
import mo_workspace as ws  # noqa: E402
import server  # noqa: E402

ws._dns_ok[:] = [False]

FICHES = {f"https://annu075.test/club/{n}": (
    f'<html><body><a href="#top">Haut</a><ul><li>Les sports</li><li>Tous les clubs</li><li>Equipements</li>'
    f'<li>La mairie</li></ul><p>{name}</p><p>Président : M. {name.split()[-1]}</p>'
    f'<p>Tél 06 11 22 33 4{n}</p><footer><p>Office des sports</p><p>office@annu075.test</p></footer></body></html>')
    for n, name in enumerate(["Judo Club Alpha", "Escrime Beta", "Volley Gamma", "Boxe Delta", "Danse Epsilon"], 1)}
FICHES["https://annu075.test/clubs"] = "<html><body>" + "".join(
    f'<a href="/club/{n}">club {n}</a>' for n in range(1, 6)) + "</body></html>"
PDF_LIST = "https://annu075.test/tableau.pdf"
FICHES[PDF_LIST] = "<html><body>" + "".join(
    f"<p>{n} - {m}</p>" for n, m in [("Club Un", "un.tab075@gmail.com"), ("Club Deux", "deux.tab075@gmail.com"),
                                    ("Club Trois", "trois.tab075@gmail.com")]) + "</body></html>"


def fake_read(url, readers=None, project_dir=None, **kw):
    html = FICHES.get(url)
    if html is None:
        return {"crawl4ai": {"error": "HTTP 404"}}
    return {"crawl4ai": {"final_url": url, "html": html, "text": ""}}


class Base(unittest.TestCase):
    def setUp(self):
        self.patches = [mock.patch.object(mo_providers, "read", side_effect=fake_read),
                        mock.patch.object(mo_providers, "read_race", side_effect=fake_read),
                        mock.patch.object(mo_providers, "public_url", side_effect=lambda u: u),
                        mock.patch.object(mo_providers, "search", return_value={"results": [], "routes": {}}),
                        mock.patch.object(mo_providers, "pdf_text",   # offline: the page readers above serve the PDF
                                          side_effect=mo_providers.ProviderError("offline test"))]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in reversed(self.patches):
            p.stop()

    def org(self, project: str, name: str, **extra) -> str:
        server.orgs(project, add=[{"name": name, **extra}], source="test-list")
        return next(o["id"] for o in ws.read_csv(ws.project_dir(project) / "orgs.csv")
                    if o["name"] == name and not o.get("merged_into"))


class Harvest(Base):
    def test_the_directorys_chrome_is_not_its_entries(self):
        p = "h075"
        server.sources(p, add=[{"source": "annuaire", "url": "https://annu075.test/clubs"}])
        out = server.harvest(p, "annuaire", start="https://annu075.test/clubs", follow=r"/club/\d+")
        rows = ws.read_csv(ws.project_dir(p) / out["file"])
        self.assertEqual({"Judo Club Alpha", "Escrime Beta", "Volley Gamma", "Boxe Delta", "Danse Epsilon"},
                         {r["name"] for r in rows})
        self.assertFalse(any("office@annu075.test" in r["emails"] for r in rows))
        self.assertIn("office@annu075.test", out["chrome_removed"]["addresses"])

    def test_a_listing_with_no_entry_is_a_warning(self):
        out = server.harvest("h075b", "annuaire", start="https://annu075.test/clubs", follow=r"/fiche/\d+")
        self.assertIn("matched no link", out["warning"])


class PoolRows(Base):
    def test_rows_from_one_page_keep_their_own_address_only(self):
        p, pdir = "p075", ws.project_dir("p075")
        server.fetch(p, PDF_LIST)   # the page is saved before the rows are pooled
        server.pool(p, rows=[{"name": n, "email": m, "page_url": PDF_LIST}
                             for n, m in [("Club Un", "un.tab075@gmail.com"), ("Club Deux", "deux.tab075@gmail.com")]],
                    into="orgs", source="tableau")
        got = {o["name"]: {c["value"] for c in ws.contacts_of(pdir, o["id"])} for o in ws.read_csv(pdir / "orgs.csv")}
        self.assertEqual({"Club Un": {"un.tab075@gmail.com"}, "Club Deux": {"deux.tab075@gmail.com"}}, got)
        c = ws.contacts_of(pdir, next(o["id"] for o in ws.read_csv(pdir / "orgs.csv") if o["name"] == "Club Un"))[0]
        self.assertIn("Club Un", c["quote"])   # the grader reads the quote: it names the club

    def test_a_merged_organisation_does_not_come_back(self):
        p, pdir = "m075", ws.project_dir("m075")
        keep = self.org(p, "AS Bouvines Onze", city="Paris")
        drop = self.org(p, "Association Sportive Bouvines Loisirs", city="Paris")
        server.orgs(p, merge=[{"keep": keep, "drop": drop}])
        server.orgs(p, add=[{"name": "Association Sportive Bouvines Loisirs", "city": "Paris",
                             "email": "pres.m075@gmail.com", "quote": "Président"}], source="again")
        live = [o for o in ws.read_csv(pdir / "orgs.csv") if not o.get("merged_into")]
        self.assertEqual([keep], [o["id"] for o in live])
        self.assertEqual({"pres.m075@gmail.com"}, {c["value"] for c in ws.contacts_of(pdir, keep)})
        self.assertEqual([], ws.contacts_of(pdir, drop))

    def test_a_directorys_home_is_not_its_fiches_website(self):
        p, pdir = "w075", ws.project_dir("w075")
        server.pool(p, rows=[{"name": "Judo Club Fiche", "website": "https://www.ffjudo075.test/",
                              "page_url": "https://www.ffjudo075.test/club/123"}], into="orgs", source="fed")
        self.assertEqual("", ws.read_csv(pdir / "orgs.csv")[0]["website"])


class Duplicates(Base):
    def test_names_written_two_ways_are_found(self):
        p, pdir = "d075", ws.project_dir("d075")
        pairs = [("UNION SPORTIVE PARIS XI (US PARIS XI)", "Union Sportive Paris 11 - (U S Paris 11 )"),
                 ("Pétanque Club Jean Aicard", "PÉTANQUE CLUB JEAN AICART"),
                 ("MACCABI", "SPORTING CLUB MACCABI de PARIS (SCMP)"),
                 ("ÉCOLE de KARATE SHOTOKAN Paris XI (EKS Paris XI)", "EKS Paris 11ème")]
        for a, b in pairs:
            self.org(p, a, city="Paris")
            self.org(p, b)
        for n in ("Judo Club Paris", "Tennis Club Paris", "Charonne Skate Club", "Cercle Shoto CPS"):
            self.org(p, n, city="Paris")
        found = [set(d["names"]) for d in mo_tasks.possible_duplicates(pdir)]
        for a, b in pairs:
            self.assertIn({a[:40], b[:40]}, found)
        self.assertEqual(len(pairs), len(found), found)   # initials alike (CSC) are not one club


class Lanes(Base):
    def test_a_new_project_keeps_workers_waiting(self):
        server.project("l075", brief={"goal": "g", "country": "FR", "language": "fr"})
        got = server.tasks("l075", action="claim", kind="sweep", agent="r1")
        self.assertTrue(got["keep_waiting"])
        self.assertNotIn("lane_finished", got)

    @mock.patch.object(mo_tasks, "AUTO_FIND", 0)   # the lane's own end, without the find it now offers
    def test_a_source_under_the_pilot_bar_is_paused_not_waited_for(self, *_):
        p, pdir = "l075b", ws.project_dir("l075b")
        server.project(p, brief={"goal": "g", "country": "FR", "language": "fr", "pilot": 2})
        ids = [self.org(p, f"Club Pause {c}") for c in "ABCDEFGHIJKL"]
        ws.add_verdicts(pdir, [{"org_id": i, "verdict": "none"} for i in ids[:2]], "r")
        self.assertIn("task", server.tasks(p, action="claim", kind="sweep", agent="r0"))   # 0/2: too few to pause
        ws.add_verdicts(pdir, [{"org_id": i, "verdict": "none"} for i in ids[2:10]], "r")
        mo_tasks.done(pdir, ws.read_csv(pdir / "tasks.csv")[-1]["task_id"])
        got = server.tasks(p, action="claim", kind="sweep", agent="r1")
        self.assertTrue(got["lane_finished"])
        self.assertIn("paused by the pilot rule", got["why_empty"])
        self.assertTrue(any("paused by the pilot" in x for x in server.project(p)["needs_you"]))
        server.sources(p, add=[{"source": "test-list", "url": "https://x075.test/"}])
        server.sources(p, set=[{"source": "test-list", "status": "scale"}])
        self.assertIsNotNone(server.tasks(p, action="claim", kind="sweep", agent="r1")["task"])


class Websites(Base):
    def test_a_namesake_elsewhere_is_not_its_site(self):
        pdir = ws.project_dir("s075")
        org = {"id": "o1", "name": "Bien Etre Sport Sante Travail", "city": "Paris"}
        hits = {"results": [{"url": "https://www.brest-sport-sante.fr/", "title": "Brest sport santé",
                             "snippet": "Association brestoise"}]}
        with mock.patch.object(mo_providers, "search", return_value=hits):
            self.assertEqual("", mo_run._find_site(pdir, org))
        hits = {"results": [{"url": "https://www.bien-etre-sport-sante.fr/", "title": "BESST", "snippet": ""}]}
        with mock.patch.object(mo_providers, "search", return_value=hits):
            self.assertEqual("https://www.bien-etre-sport-sante.fr/", mo_run._find_site(pdir, org))

    def test_a_site_without_its_name_is_flagged_for_a_look(self):
        p = "s075b"
        oid = self.org(p, "Club Omega Nage", website="https://www.federation075.test/")
        summary = server.orgs(p, ids=[oid])["orgs"][0]
        self.assertIn("website_check", summary)
        oid2 = self.org(p, "Club Sigma Natation", website="https://www.sigma-natation075.test/")
        self.assertNotIn("website_check", server.orgs(p, ids=[oid2])["orgs"][0])


class Fields(unittest.TestCase):
    def test_rot13_addresses_are_decoded(self):
        hidden = codecs.encode("maison.asso.11@gmail.com", "rot13")
        self.assertEqual("maison.asso.11@gmail.com", mo_pages.contacts([f"Mail : {hidden}"], "https://x.test/")[0]["value"])
        self.assertEqual("contact@club.se", mo_pages.unrot("contact@club.se"))
        self.assertEqual("x@y.tv", mo_pages.unrot("x@y.tv"))

    def test_person_and_role_are_fields_not_lines(self):
        self.assertEqual(("Daniel DERRIEN", "Président"),
                         mo_pages.person_role("Président : Daniel DERRIEN, dd075@gmail.com",
                                              "Président : Daniel DERRIEN, dd075@gmail.com"))
        self.assertEqual(("Jean-Pierre Martin", "Responsable des terrains"),
                         mo_pages.person_role("Jean-Pierre Martin", "Responsable des terrains - 06 12 34 56 78"))


class Delivery(unittest.TestCase):
    def test_no_delivery_without_the_spot_check(self):
        rows = [{"org_id": f"o{i}", "value": f"p{i}@c.test", "grade": "A"} for i in range(25)]
        self.assertFalse(mo_export.delivery_gate(rows)["ready"])
        for r in rows[:20]:
            r["checked"] = "ok"
        self.assertTrue(mo_export.delivery_gate(rows)["ready"])
        for r in rows[:3]:
            r["checked"] = "wrong"
        self.assertFalse(mo_export.delivery_gate(rows)["ready"])   # 85% < 90%


if __name__ == "__main__":
    unittest.main()
