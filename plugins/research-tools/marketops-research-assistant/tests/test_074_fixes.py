"""The fixes from the Paris 11e run and the Loir-et-Cher comparison run (0.7.4).

paris11-073: three workers read `open: 0` and went home with 310 organisations unread; a lead
sent one brief key and lost the goal; six clubs were pooled twice and counted twice; a district
PDF gave one club eight other clubs' addresses; 274 held addresses sat in the totals; a club got
a listings site as its website. fr41: a commune's /associations page gave the commune 76 other
associations' addresses, and a commune got its namesake's website in another département.

Run: python -m unittest tests.test_074_fixes -v
"""
from __future__ import annotations

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
import mo_providers  # noqa: E402
import mo_run  # noqa: E402
import mo_tasks  # noqa: E402
import mo_workspace as ws  # noqa: E402
import server  # noqa: E402

ws._dns_ok[:] = [False]

ASSOCIATIONS = "".join(
    f'<div class="asso"><h3>{n}</h3><p>Président : M. {n.split()[-1]}</p><p>{m}</p></div>'
    for n, m in [("Comité des fêtes", "cdf.list074@gmail.com"), ("Don du sang", "dds.list074@orange.fr"),
                 ("Amicale des pêcheurs", "peche.list074@sfr.fr"), ("Club de lecture", "lecture.list074@live.fr"),
                 ("Chorale Saint-Jean", "chorale.list074@gmail.com"), ("Yoga pour tous", "yoga.list074@gmail.com"),
                 ("Tennis de table loisir", "tt.list074@gmail.com"), ("Randonnée verte", "rando.list074@free.fr")])
BOARD = "".join(f"<div><p>{r}</p><p>{m}</p></div>" for r, m in [
    ("Président", "pres.board074@gmail.com"), ("Vice-président", "vp.board074@gmail.com"),
    ("Secrétaire", "sec.board074@gmail.com"), ("Trésorier", "tres.board074@gmail.com"),
    ("Responsable terrains", "terrains.board074@gmail.com"), ("Entraîneur", "coach.board074@gmail.com"),
    ("Arbitre", "arbitre.board074@gmail.com")])
TABLE = "".join(f"<tr><td>{n}</td><td>{m}</td></tr>" for n, m in [
    ("Escrime Lambda Onze", "lambda.tab074@gmail.com"), ("Judo Kappa", "kappa.tab074@gmail.com"),
    ("Gymnastique Rythmique Zetaville", "zeta.tab074@gmail.com"), ("Boxe Omicron", "omicron.tab074@gmail.com"),
    ("Danse Sigma", "sigma.tab074@gmail.com"), ("Karaté Tau", "tau.tab074@gmail.com"),
    ("Volley Upsilon", "upsilon.tab074@gmail.com")])
PAGES = {
    "https://www.mertest074.fr/associations": "<h1>Associations</h1>" + ASSOCIATIONS
                                              + "<footer><p>Mairie</p><p>mairie@mertest074.fr</p></footer>",
    "https://club-board074.test/bureau": "<h1>Le bureau</h1>" + BOARD,
    "https://www.mertest074.fr/repertoire-des-clubs-sportifs": "".join(
        f"<div><h3>{n}</h3><p>Stade, 41500 Mertest</p><p>Nous contacter {m}</p></div>"
        for n, m in [("US Mertest Football", "usm.town074@wanadoo.fr"), ("Judo Mertest", "judo.town074@gmail.com"),
                     ("Tennis Club", "tc.town074@gmail.com"), ("Rugby Loisir", "rugby.town074@gmail.com"),
                     ("Pétanque", "petanque.town074@orange.fr"), ("Cyclo", "cyclo.town074@free.fr")]),
    "https://www.mertest074.fr/organigramme": "".join(
        f"<div><h3>{s}</h3><p>{m}</p></div>" for s, m in [
            ("Direction générale", "dgs@comcom074.fr"), ("Ressources humaines", "rh@comcom074.fr"),
            ("Finances", "finances@comcom074.fr"), ("Urbanisme", "urba@comcom074.fr"),
            ("Marchés publics", "marches@comcom074.fr"), ("Informatique", "dsi@comcom074.fr"),
            ("Services techniques", "st@mertest074.fr")]),
    "https://district074.test/tableau": "<table>" + TABLE + "</table>",
}


def fake_read(url, readers=None, project_dir=None, **kw):
    html = PAGES.get(url)
    if html is None:
        return {"crawl4ai": {"error": "HTTP 404"}}
    return {"crawl4ai": {"final_url": url, "html": "<html><body>" + html + "</body></html>", "text": ""}}


class Base(unittest.TestCase):
    def setUp(self):
        self.patches = [mock.patch.object(mo_providers, "read", side_effect=fake_read),
                        mock.patch.object(mo_providers, "read_race", side_effect=fake_read),
                        mock.patch.object(mo_providers, "public_url", side_effect=lambda u: u),
                        mock.patch.object(mo_providers, "search", return_value={"results": [], "routes": {}})]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in reversed(self.patches):
            p.stop()

    def org(self, project: str, name: str, **extra) -> str:
        server.orgs(project, add=[{"name": name, **extra}], source="test-list")
        return next(o["id"] for o in ws.read_csv(ws.project_dir(project) / "orgs.csv") if o["name"] == name)


class BriefMerges(Base):
    def test_one_key_keeps_the_rest_and_null_removes(self):
        server.project("b074", brief={"goal": "people who run pitches", "country": "FR", "language": "fr",
                                      "region": "Loir-et-Cher (41)", "fresh": True})
        server.project("b074", brief={"max_per_source": 8})
        b = ws.brief(ws.project_dir("b074"))
        self.assertEqual(("people who run pitches", "Loir-et-Cher (41)", True, 8),
                         (b.get("goal"), b.get("region"), b.get("fresh"), b.get("max_per_source")))
        since = b["fresh_since"]
        server.project("b074", brief={"max_per_source": None})
        b = ws.brief(ws.project_dir("b074"))
        self.assertNotIn("max_per_source", b)
        self.assertEqual(since, b["fresh_since"])   # a later call never moves the fresh start
        self.assertFalse(any("no goal" in x for x in server.project("b074")["needs_you"]))


class ListPages(Base):
    def test_a_communes_associations_page_gives_the_commune_only_its_own_address(self):
        oid = self.org("l074a", "Commune de Mertest", website="https://www.mertest074.fr/", city="Mertest")
        got = server.fetch("l074a", "https://www.mertest074.fr/associations", org_id=oid, org="Commune de Mertest")
        mine = {c["value"] for c in ws.contacts_of(ws.project_dir("l074a"), oid)}
        self.assertEqual({"mairie@mertest074.fr"}, mine)
        self.assertEqual(8, got["list_page"]["addresses_of_others"])
        self.assertIn("sources(add=", got["list_page"]["note"])
        kept = ws.read_csv(ws.project_dir("l074a") / "unattributed.csv")
        self.assertEqual(8, len({r["value"] for r in kept if r["org_id"] == oid}))

    def test_the_town_in_every_entry_does_not_name_the_commune(self):
        oid = self.org("l074d", "Commune de Mertest", website="https://www.mertest074.fr/", city="Mertest")
        got = server.fetch("l074d", "https://www.mertest074.fr/repertoire-des-clubs-sportifs", org_id=oid)
        self.assertEqual(0, got["list_page"]["given_to_this_organisation"])
        self.assertEqual([], ws.contacts_of(ws.project_dir("l074d"), oid))

    def test_a_communes_organigram_keeps_its_shared_services(self):
        oid = self.org("l074e", "Commune de Mertest", website="https://www.mertest074.fr/", city="Mertest")
        got = server.fetch("l074e", "https://www.mertest074.fr/organigramme", org_id=oid)
        self.assertNotIn("list_page", got)
        self.assertEqual(7, len(ws.contacts_of(ws.project_dir("l074e"), oid)))

    def test_a_clubs_own_board_page_still_gives_it_its_board(self):
        oid = self.org("l074b", "Football Club Boardtest", website="https://club-board074.test/")
        got = server.fetch("l074b", "https://club-board074.test/bureau", org_id=oid, org="Football Club Boardtest")
        self.assertNotIn("list_page", got)
        self.assertEqual(7, len(ws.contacts_of(ws.project_dir("l074b"), oid)))

    def test_on_a_district_table_only_the_entry_naming_the_club_is_its(self):
        p = "l074c"
        for n in ("Escrime Lambda Onze", "Judo Kappa", "Boxe Omicron"):
            self.org(p, n)
        oid = self.org(p, "Gymnastique Rythmique Zetaville", website="https://www.paris.fr/zetaville")
        got = server.fetch(p, "https://district074.test/tableau", org_id=oid, org="Gymnastique Rythmique Zetaville")
        self.assertEqual({"zeta.tab074@gmail.com"}, {c["value"] for c in ws.contacts_of(ws.project_dir(p), oid)})
        self.assertEqual(1, got["list_page"]["given_to_this_organisation"])


class Duplicates(Base):
    def test_a_club_pooled_twice_is_a_duplicate_not_a_chain_and_merges(self):
        p, pdir = "d074", ws.project_dir("d074")
        a = self.org(p, "AS Aikido Onze", website="https://aikido-onze074.test/")
        b = self.org(p, "KI Aikido Association K2A")
        for oid in (a, b):
            ws.merge_contacts(pdir, [{"kind": "email", "value": "daniel.dup074@gmail.com", "mailbox": "named_or_role",
                                      "quote": "Président Daniel"}], oid, "x", "test")
        ws.update_contacts(pdir, [{"org_id": a, "value": "daniel.dup074@gmail.com", "grade": "A", "person": "Daniel"},
                                  {"org_id": b, "value": "daniel.dup074@gmail.com", "grade": "A", "person": "Daniel"}],
                           ["grade", "person"])
        ws.add_verdicts(pdir, [{"org_id": b, "verdict": "routes"}], "r")
        flow = server.project(p)["flow"]
        self.assertEqual([sorted([a, b])], [d["ids"] for d in flow["possible_duplicates"]])
        self.assertEqual([], flow["possible_chains"])
        self.assertTrue(any("pooled twice" in x for x in server.project(p)["needs_you"]))
        orgs = [o for o in ws.read_csv(pdir / "orgs.csv")]
        self.assertEqual(1, mo_export.ledgers(ws.read_contacts(pdir))["strict_routes"])   # one address, one route
        server.orgs(p, merge=[{"keep": a, "drop": b}])
        orgs = {o["id"]: o for o in ws.read_csv(pdir / "orgs.csv")}
        self.assertEqual(a, orgs[b]["merged_into"])
        self.assertEqual([], ws.contacts_of(pdir, b))
        self.assertEqual("A", ws.contacts_of(pdir, a)[0]["grade"])
        self.assertEqual({a}, {v["org_id"] for v in ws.read_csv(pdir / "verdicts.csv")})
        m = mo_export.metrics(list(orgs.values()), ws.read_contacts(pdir))
        self.assertEqual((1, 1), (m["orgs"], m["ab_orgs"]))
        self.assertEqual([], server.project(p)["flow"]["possible_duplicates"])
        self.assertTrue(server.project(p)["flow"]["reconcile"]["adds_up"])   # the dropped row still counts its source


class EmptyLanes(Base):
    def test_a_lane_capped_by_the_brief_says_so_and_is_not_finished(self):
        p, pdir = "e074", ws.project_dir("e074")
        server.project(p, brief={"goal": "g", "country": "FR", "language": "fr", "pilot": 2, "max_per_source": 2})
        ids = [self.org(p, f"Club Capped {c}") for c in "ABCDE"]
        for i in ids[:2]:   # the pilot found A/B: the source scales, up to the brief's own cap
            ws.merge_contacts(pdir, [{"kind": "email", "value": f"pres.{i}@club.test", "quote": "Président"}], i, "c", "t")
            ws.update_contacts(pdir, [{"org_id": i, "value": f"pres.{i}@club.test", "grade": "A"}], ["grade"])
        ws.add_verdicts(pdir, [{"org_id": i, "verdict": "routes"} for i in ids[:2]], "r")
        got = server.tasks(p, action="claim", kind="sweep", agent="r1")
        self.assertIsNone(got["task"])
        self.assertIn("max_per_source=2", got["why_empty"])
        self.assertFalse(got.get("lane_finished"))
        self.assertIn("tell the lead", got["next"])
        self.assertTrue(any("lane sweep empty" in x for x in server.project(p)["needs_you"]))
        st = server.tasks(p, action="status", kind="sweep")
        self.assertIn("why_empty", st)

    def test_a_lane_waits_while_sources_are_mined_and_ends_when_nothing_can_refill_it(self):
        p = "e074b"
        server.project(p, brief={"goal": "g", "country": "FR", "language": "fr"})
        server.sources(p, add=[{"source": "district list", "url": "https://district074.test/list"}])
        got = server.tasks(p, action="claim", kind="sweep", agent="r1")
        self.assertTrue(got["keep_waiting"])
        self.assertIn("do not stop", got["next"])
        server.sources(p, set=[{"source": "district list", "status": "dropped", "note": "dead"}])
        got = server.tasks(p, action="claim", kind="sweep", agent="r1")
        self.assertTrue(got["lane_finished"])

    def test_dig_waiting_counts_only_what_the_dig_lane_can_hand_out(self):
        p, pdir = "e074c", ws.project_dir("e074c")
        ids = [self.org(p, f"Club Staged {c}") for c in "AB"]
        for i in ids:
            ws.append_csv(pdir / "crawl.csv", ["org_id", "pass", "status"], {"org_id": i, "pass": "final", "status": "done"})
        mo_tasks.plan(pdir, "sweep", 1)   # none: both read; put one in a research package by hand
        mo_tasks.add(pdir, "x")
        rows = ws.read_csv(pdir / "tasks.csv")
        rows.append({"task_id": "sweep-x", "kind": "sweep", "title": "", "items": ids[0], "status": "done"})
        ws.write_csv(pdir / "tasks.csv", mo_tasks.TASK_COLS, rows)
        st = ws.stages(pdir)["dig"]
        self.assertEqual((1, 1), (st["waiting_for_an_agent"], st["in_packages_without_verdict"]))


class HeldAndCheck(Base):
    def test_held_rows_stay_out_of_the_totals_until_an_agent_clears_them(self):
        p, pdir = "h074", ws.project_dir("h074")
        oid = self.org(p, "Club Floodtest")
        rows = [{"kind": "email", "value": f"x{i}.held074@gmail.com", "quote": "q"} for i in range(5)]
        ws.merge_contacts(pdir, rows, oid, "Club Floodtest", "test")
        ws.update_contacts(pdir, [{**r, "org_id": oid, "graded_by": "held"} for r in rows], ["graded_by"])
        self.assertTrue(any("held back from directory pages" in x for x in server.project(p)["needs_you"]))
        out = mo_export.export(pdir)
        self.assertTrue(out)
        server.grade(p, held=[{"org_id": oid, "held": "X"}])
        self.assertEqual({"X"}, {c["grade"] for c in ws.contacts_of(pdir, oid)})
        self.assertFalse(any("held back" in x for x in server.project(p)["needs_you"]))

    def test_the_lead_is_asked_for_the_spot_check(self):
        p, pdir = "c074", ws.project_dir("c074")
        oid = self.org(p, "Club Checktest")
        rows = [{"kind": "email", "value": f"p{i}.chk074@club.test", "quote": "Président"} for i in range(10)]
        ws.merge_contacts(pdir, rows, oid, "Club Checktest", "test")
        ws.update_contacts(pdir, [{**r, "org_id": oid, "grade": "A"} for r in rows], ["grade"])
        self.assertTrue(any("No spot check yet" in x for x in server.project(p)["needs_you"]))
        server.grade(p, set=[{"value": "p0.chk074@club.test", "org_id": oid, "checked": "ok"}])
        self.assertFalse(any("No spot check yet" in x for x in server.project(p)["needs_you"]))


class Websites(Base):
    def test_a_namesake_in_another_departement_is_not_its_website(self):
        pdir = ws.project_dir("w074")
        server.project("w074", brief={"goal": "g", "country": "FR", "language": "fr", "region": "Loir-et-Cher (41)"})
        org = {"id": "o1", "name": "Commune de Verdigny", "city": "Verdigny"}
        self.assertTrue(mo_run._elsewhere(pdir, org, "Verdigny - Mairie, 18300 Verdigny, Cher"))
        self.assertFalse(mo_run._elsewhere(pdir, org, "Mairie de Verdigny 41100"))
        self.assertFalse(mo_run._elsewhere(pdir, org, "Mairie de Verdigny"))   # no postcode: no call
        hits = {"results": [{"url": "https://www.verdigny.fr/", "title": "Verdigny", "snippet": "18300 Verdigny"}]}
        with mock.patch.object(mo_providers, "search", return_value=hits):
            self.assertEqual("", mo_run._find_site(pdir, org))

    def test_a_listings_site_is_not_a_clubs_website(self):
        pdir = ws.project_dir("w074b")
        hits = {"results": [{"url": "https://www.sortiraparis.com/escrime-canne", "title": "Canne d'arme",
                             "snippet": "75011"}]}
        with mock.patch.object(mo_providers, "search", return_value=hits):
            self.assertEqual("", mo_run._find_site(pdir, {"id": "o2", "name": "Association Sportive Canne Arme Paris"}))


if __name__ == "__main__":
    unittest.main()
