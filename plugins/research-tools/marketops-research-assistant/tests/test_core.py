"""Offline checks of the whole flow: page -> contacts -> grades -> Excel. No network."""
import json
import os
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
TMP = tempfile.mkdtemp()
os.environ.update(MARKETOPS_HOME=TMP + "/home", MARKETOPS_PROJECTS=TMP + "/projects",
                  MARKETOPS_HEALTH="0", MARKETOPS_KNOWLEDGE="0")   # shared state: only their own tests turn them on
for k in ("SERPER_API_KEY", "SERPAPI_API_KEY", "DEEPSEEK_API_KEY", "TINYFISH_API_KEY", "TYPESAFE_API_KEY"):
    os.environ.pop(k, None)

import mo_budget  # noqa: E402
import mo_export  # noqa: E402
import mo_pages  # noqa: E402
from mo_pool import norm as norm_name  # noqa: E402
import mo_providers  # noqa: E402
import mo_run  # noqa: E402
import mo_workspace as ws  # noqa: E402
real_race = mo_providers.read_race
ws._dns_ok[:] = [False]   # test domains are made up: no DNS pre-check unless a test asks for it
import server  # noqa: E402

CLUB = """<html><head><title>FC Beispiel</title><script>var x='a@b.ch'</script></head><body>
<nav><a href="/vorstand">Vorstand</a> <a href="/kontakt">Kontakt</a> <a href="https://other.ch/x">x</a></nav>
<div class="card"><p>Präsident</p><p>Hans Muster</p><p><a href="mailto:praesident@fc-beispiel.ch">E-Mail</a></p></div>
<div class="card"><p>Platzwart</p><p>Eva Rasen</p><p>platz [at] fc-beispiel.ch</p><p>Tel. 079 123 45 67</p></div>
<p>Training 09.22-17.07.26 und 07.11.2026 10</p>
<p>Webdesign: Studio X, hello@studio-x.ch</p>
<p><span data-cfemail="0a636469644a6c69276f6363697a636f66247f69"></span></p>
<p>info@fc-beispiel.ch</p></body></html>"""


def fake_read(url, readers=None, project_dir=None):
    if "vorstand" in url:
        return {"crawl4ai": {"final_url": url, "html": "<p>Sekretär</p><p>Ida Brief</p><p>sekretariat@fc-beispiel.ch</p>", "text": ""},
                "tinyfish": {"error": "HTTP 500"}}
    return {"crawl4ai": {"final_url": url, "html": CLUB, "text": ""},
            "tinyfish": {"final_url": url, "html": "", "text": "Präsident Hans Muster [E-Mail](mailto:praesident@fc-beispiel.ch)\nTeam [x](tel:+41%2079%20123%2045%2067)"}}


class Pages(unittest.TestCase):
    def test_contacts_keep_their_own_lines(self):
        page = mo_pages.from_html(CLUB, "https://fc-beispiel.ch/")
        found = {c["value"]: c for c in mo_pages.contacts(page["lines"], "https://fc-beispiel.ch/")}
        self.assertIn("Hans Muster", found["praesident@fc-beispiel.ch"]["quote"])
        self.assertIn("Platzwart", found["platz@fc-beispiel.ch"]["quote"])
        self.assertNotIn("Präsident", found["platz@fc-beispiel.ch"]["quote"].split("[next")[0])
        self.assertEqual("no", found["hello@studio-x.ch"]["same_site"])
        self.assertEqual("generic", found["info@fc-beispiel.ch"]["mailbox"])
        self.assertNotIn("a@b.ch", found)  # script text is not page text
        phones = [c["value"] for c in mo_pages.contacts(page["lines"], "https://fc-beispiel.ch/") if c["kind"] == "phone"]
        self.assertEqual(["079 123 45 67"], phones)  # dates are not phones

    def test_contact_links_stay_on_site(self):
        page = mo_pages.from_html(CLUB, "https://fc-beispiel.ch/")
        links = mo_pages.contact_links(page["links"], "https://fc-beispiel.ch/")
        self.assertEqual({"https://fc-beispiel.ch/vorstand", "https://fc-beispiel.ch/kontakt"}, set(links))

    def test_registrable_site(self):
        self.assertEqual("leeds.sch.uk", mo_pages.site_of("https://www.abbey.leeds.sch.uk/x"))
        self.assertEqual("example.co.uk", mo_pages.site_of("https://a.example.co.uk"))


class Flow(unittest.TestCase):
    def setUp(self):
        self.p = mock.patch.object(mo_providers, "read", side_effect=fake_read)
        self.p.start()
        self.pub = mock.patch.object(mo_providers, "public_url", side_effect=lambda u: u)
        self.pub.start()

    def tearDown(self):
        self.p.stop()
        self.pub.stop()

    def test_fetch_merges_readers_and_saves_pages(self):
        out = server.fetch("t1", "https://fc-beispiel.ch/", org_id="o1", org="FC Beispiel")
        self.assertEqual({"crawl4ai", "tinyfish"}, set(out["readers"]))
        by = {c["value"]: c for c in out["contacts"]}
        self.assertEqual("crawl4ai+tinyfish", by["praesident@fc-beispiel.ch"]["found_by"])
        rows = ws.read_contacts(ws.project_dir("t1"))
        self.assertEqual(1, sum(r["value"] == "praesident@fc-beispiel.ch" for r in rows))
        self.assertEqual("D", next(r for r in rows if r["value"] == "info@fc-beispiel.ch")["grade"])
        self.assertGreaterEqual(len(ws.read_csv(ws.project_dir("t1") / "pages.csv")), 2)

    def test_site_grade_export_end_to_end(self):
        pdir = ws.project_dir("t2")
        (pdir / "orgs.csv").write_text("id,name,website\no1,FC Beispiel,fc-beispiel.ch\no2,No Site,\n", encoding="utf-8")
        for org in ws.read_csv(pdir / "orgs.csv"):
            ws.append_csv(pdir / "crawl.csv", ws.CRAWL_COLS, ws._crawl_one(pdir, org, 15, None, None, "site"))
        crawl = {r["org_id"]: r for r in ws.read_csv(pdir / "crawl.csv")}
        self.assertEqual("done", crawl["o1"]["status"])
        self.assertEqual("no_website", crawl["o2"]["status"])
        values = {r["value"] for r in ws.read_contacts(pdir)}
        self.assertIn("sekretariat@fc-beispiel.ch", values)  # found on the linked Vorstand page
        self.assertIn("no usable Jev key", server.grade("t2")["note"])
        self.assertEqual(1, server.grade("t2", set=[{"value": "praesident@fc-beispiel.ch", "grade": "A",
                                                     "person": "Hans Muster", "role": "Präsident"}])["updated"])
        self.assertEqual(1, server.grade("t2", set=[{"value": "hello@studio-x.ch", "grade": "X"}])["updated"])
        out = server.export("t2")
        self.assertEqual(50.0, out["ab_routes_per_100_orgs"])
        self.assertTrue(Path(out["routes_csv"]).read_text(encoding="utf-8-sig").startswith("account_id,account_name"))
        with zipfile.ZipFile(out["file"]) as z:
            names = z.namelist()
            book = z.read("xl/workbook.xml").decode()
            self.assertIn("联系人", book)
            self.assertIn("排除", book)
            self.assertTrue(any(n.startswith("xl/worksheets/") for n in names))

    def test_judge_without_key_says_so(self):
        pdir = ws.project_dir("t3")
        (pdir / "venues.csv").write_text("id,name\nv1,Rasen\n", encoding="utf-8")
        self.assertIn("no Jev key", server.judge("t3", "venues.csv", "in scope?", {"yes": "y", "no": "n"}, "scope")["note"])


class Reach(unittest.TestCase):
    def test_cards_get_their_own_name_and_role(self):
        html = ("<div><div class=c><a href='mailto:anna@x.ch'>Email</a><h3>Anna A</h3><p>Head of Grounds</p></div>"
                "<div class=c><a href='mailto:ben@x.ch'>Email</a><h3>Ben B</h3><p>Welfare Officer</p></div>"
                "<div class=c><h3>Cara</h3><a href='#'>Email</a></div></div>")
        page = mo_pages.from_html(html, "https://x.ch/")
        q = {c["value"]: c["quote"] for c in mo_pages.contacts(page["lines"], "https://x.ch/", blocks=page["blocks"])}
        self.assertIn("Welfare", q["ben@x.ch"])
        self.assertNotIn("Grounds", q["ben@x.ch"])
        self.assertEqual(1, page["hidden_email_buttons"])

    def test_saved_pages_are_not_fetched_again(self):
        with mock.patch.object(mo_providers, "read", side_effect=fake_read) as read, \
                mock.patch.object(mo_providers, "public_url", side_effect=lambda u: u):
            server.fetch("t4", "https://fc-beispiel.ch/", org_id="o1", org="FC")
            again = server.fetch("t4", "https://fc-beispiel.ch/", org_id="o1", org="FC")
        self.assertLessEqual(read.call_count, 1)  # 0 when an earlier test already put it in the library
        self.assertTrue(all(v.get("cached") for v in again["readers"].values()))

    def test_a_new_project_reuses_the_library(self):
        with mock.patch.object(mo_providers, "read", side_effect=fake_read) as read, \
                mock.patch.object(mo_providers, "public_url", side_effect=lambda u: u):
            server.fetch("lib-a", "https://club-lib.ch/kontakt", org_id="o1", org="Club")
            out = server.fetch("lib-b", "https://club-lib.ch/kontakt", org_id="x9", org="Club")
        self.assertEqual(1, read.call_count)                       # the second project read nothing
        self.assertTrue(all(v.get("cached") == "library" for v in out["readers"].values()))
        rows = ws.read_contacts(ws.project_dir("lib-b"))
        self.assertTrue(rows and all(r["last_verified"] for r in rows))  # dated from the page read

    def test_search_answers_are_reused(self):
        os.environ["TINYFISH_API_KEY"] = "k" * 10
        try:
            calls = []
            with mock.patch.dict(mo_providers._SEARCH, {"tinyfish": lambda *a: calls.append(a) or
                                                        [{"url": "https://a.ch/", "title": "A"}]}), \
                    mock.patch("mo_budget.allowed", return_value=(True, "")):
                mo_providers.search("unique query 42", ["tinyfish"])
                mo_providers.search("unique query 42", ["tinyfish"])
            self.assertEqual(1, len(calls))
        finally:
            os.environ.pop("TINYFISH_API_KEY")

    def test_run_digs_the_gaps_and_exports(self):
        pdir = ws.project_dir("t5")
        (pdir / "orgs.csv").write_text("id,name,website,batch\no1,FC Beispiel,fc-beispiel.ch,b1\n"
                                       "o2,SC Leer,sc-leer.ch,b2\n", encoding="utf-8")

        def read(url, readers=None, project_dir=None):
            if "leer" in url and "vorstand" not in url:
                return {"crawl4ai": {"final_url": url, "html": "<p>Willkommen</p>", "text": ""}}
            if "sc-leer.ch/vorstand" in url:
                return {"crawl4ai": {"final_url": url, "html": "<div><p>Platzwart</p><p>platz@sc-leer.ch</p></div>", "text": ""}}
            return fake_read(url)
        hits = {"results": [{"url": "https://sc-leer.ch/vorstand", "title": "SC Leer Vorstand", "found_by": ["serper"]}],
                "routes": {}}

        def fake_grade(p, **k):
            rows = [r for r in ws.read_contacts(p) if not r.get("grade")]
            for r in rows:
                r["grade"] = {"praesident@fc-beispiel.ch": "A", "platz@sc-leer.ch": "B"}.get(r["value"], "C")
                r["graded_by"] = "jev"
            ws.update_contacts(p, rows, ["grade", "graded_by"])   # like the real grader: per-org, locked
            return {}
        with mock.patch.object(mo_providers, "read", side_effect=read), \
                mock.patch.object(mo_providers, "public_url", side_effect=lambda u: u), \
                mock.patch.object(mo_providers, "search", return_value=hits), \
                mock.patch("mo_grade.auto", side_effect=fake_grade):
            state = mo_run.run_sync(pdir, rounds=2, workers=2)
            self.assertNotIn("platz@sc-leer.ch", {r["value"] for r in ws.read_contacts(pdir)})   # the run never digs
            state = mo_run.run_sync(pdir, rounds=2, workers=2, job={"id": "dig", "orgs": ["o1", "o2"], "again": True})
            state["export"] = mo_export.export(pdir)
        values = {r["value"] for r in ws.read_contacts(pdir)}
        self.assertIn("platz@sc-leer.ch", values)  # found only by digging, for the agent's package
        self.assertTrue(state["done"])
        self.assertEqual(100.0, state["result"]["ab_org_share"])
        self.assertEqual(2, state["progress"]["flow"]["done"])
        self.assertTrue(Path(state["export"]["file"]).exists())
        self.assertEqual({"o1", "o2"}, {r["org_id"] for r in ws.read_csv(pdir / "crawl.csv") if r.get("pass") == "final"})
        with mock.patch("mo_run._Grader.grade_now"):  # resume: finished organisations are skipped
            again = mo_run.run_sync(pdir, rounds=2, workers=2)
        self.assertEqual(2, again["progress"]["flow"]["done"])

    def test_evidence_scores_but_never_drops(self):
        import mo_evidence
        strong = {"quote_from": "block", "page_on_org_site": "yes", "email_site": "fc.ch", "person": "A B",
                  "role": "Präsident", "found_by": "crawl4ai+tinyfish", "grade_reason": "jev A conf 0.90"}
        weak = {"quote_from": "lines", "page_on_org_site": "no", "email_site": "gmail.com", "found_by": "deepseek"}
        self.assertEqual("strong", mo_evidence.score(strong, "fc.ch", 0)[1])
        pts, level, why = mo_evidence.score(weak, "fc.ch", 2)
        self.assertEqual("weak", level)
        self.assertIn("other organisations", why)
        pdir = ws.project_dir("t11")
        (pdir / "contacts").mkdir(exist_ok=True)
        ws.write_csv(ws._cfile(pdir, "o1"), ws.CONTACT_COLS, [{"org_id": "o1", "kind": "email", "value": "x@gmail.com",
                                                                "grade": "B", **weak}])
        out = server.export("t11")
        self.assertEqual(1, out["contacts"])  # the weak row is delivered, marked weak

    def test_glued_phone_digits_are_cut(self):
        page = mo_pages.from_html("<p>Tel 079 123 83 10praesident@fc.ch</p><p>2go@fc.ch</p>", "https://fc.ch/")
        values = [c["value"] for c in mo_pages.contacts(page["lines"], "https://fc.ch/", mailtos=page["mailtos"])
                  if c["kind"] == "email"]
        self.assertEqual(["praesident@fc.ch", "2go@fc.ch"], values)

    def test_pool_builds_venues_and_orgs_from_a_data_file(self):
        pdir = ws.project_dir("t8")
        (pdir / "pages" / "res.json").write_text(json.dumps({"results": [
            {"equip_numero": "1", "equip_nom": "Gymnase A", "dep": "75", "gest": "Ville de Paris", "com": "Paris"},
            {"equip_numero": "2", "equip_nom": "Gymnase B", "dep": "75", "gest": "Ville de Paris", "com": "Paris"},
            {"equip_numero": "3", "equip_nom": "Salle C", "dep": "75", "gest": "Club C", "com": "Paris"},
            {"equip_numero": "4", "equip_nom": "Salle D", "dep": "92", "gest": "Club D", "com": "Nanterre"}]}),
            encoding="utf-8")
        out = server.pool("t8", "pages/res.json", filters={"dep": "75"},
                          map={"id": "equip_numero", "name": "equip_nom", "operator": "gest"},
                          group_by="gest", org_fields={"city": "com"})
        self.assertEqual((4, 3, 3, 2), (out["read"], out["kept"], out["added"], out["orgs_added"]))
        orgs = {o["name"]: o for o in ws.read_csv(pdir / "orgs.csv")}
        self.assertEqual("2", orgs["Ville de Paris"]["venues"])
        self.assertEqual("Paris", orgs["Club C"]["city"])
        self.assertEqual(0, server.pool("t8", "pages/res.json", filters={"dep": "75"},
                                        map={"id": "equip_numero", "name": "equip_nom"}, group_by="gest")["added"])

    def test_pools_are_the_union_of_sources(self):
        pdir = ws.project_dir("t12")
        (pdir / "pages" / "a.json").write_text(json.dumps([
            {"n": "Gymnase Élisabeth", "cp": "75014", "op": "Ville de Paris"},
            {"n": "Salle Pierre", "cp": "75011", "op": "Club Pierre"}]), encoding="utf-8")
        (pdir / "pages" / "b.csv").write_text("nom;code;gestion;site\nGYMNASE ELISABETH;75014;Ville de Paris;\n"
                                              "Escalade Bloc;75019;Bloc SAS;https://bloc.fr\n", encoding="utf-8")
        server.pool("t12", "pages/a.json", map={"name": "n", "postcode": "cp", "operator": "op"}, group_by="op")
        out = server.pool("t12", "pages/b.csv", map={"name": "nom", "postcode": "code", "operator": "gestion",
                                                      "website": "site"}, group_by="gestion")
        self.assertEqual((1, 1), (out["added"], out["merged_with_existing"]))   # Élisabeth = ELISABETH
        venues = ws.read_csv(pdir / "venues.csv")
        self.assertEqual(3, len(venues))
        eli = next(v for v in venues if "lisabeth" in v["name"])
        self.assertEqual("pages/a.json|pages/b.csv", eli["sources"])
        orgs = {o["name"]: o for o in ws.read_csv(pdir / "orgs.csv")}
        self.assertEqual(3, len(orgs))                                           # Ville de Paris once
        (pdir / "pages" / "c.csv").write_text("name,website,city\nBLOC SAS,,Paris\nClub Pierre,https://pierre.fr,\n",
                                              encoding="utf-8")
        server.pool("t12", "pages/c.csv", into="orgs")
        orgs = {o["name"]: o for o in ws.read_csv(pdir / "orgs.csv")}
        self.assertEqual("https://pierre.fr", orgs["Club Pierre"]["website"])  # filled by the new source
        self.assertEqual(3, len(orgs))                                           # BLOC SAS = Bloc SAS
        self.assertEqual("Paris", next(o for o in orgs.values() if norm_name(o["name"]) == "bloc sas")["city"])

    def test_organisations_merge_on_name_and_place_not_on_domain(self):
        import mo_pool
        pdir = ws.project_dir("t22")
        mo_pool.add_orgs(pdir, [{"name": "Gymnase Jean Jaurès", "website": "https://www.paris.fr/lieux/gymnase-jean-jaures-1"},
                                {"name": "Club Hugo", "website": "https://clubhugo.clubeo.com/"},
                                {"name": "Stade Municipal", "city": "Lyon", "website": "https://lyon.fr/stade-a"}], "s1", "maps")
        out = mo_pool.add_orgs(pdir, [
            {"name": "Gymnase Pasteur", "website": "https://www.paris.fr/lieux/gymnase-pasteur-2"},   # same portal
            {"name": "Club Zola", "website": "https://clubzola.clubeo.com/"},                      # same platform
            {"name": "Stade Municipal", "city": "Lyon", "website": "https://lyon.fr/stade-b"},     # same name, other site
            {"name": "Gymnase Jean-Jaures", "website": "https://paris.fr/lieux/gymnase-jean-jaures-1/"},  # same org
            {"name": "Club Hugo", "website": "https://clubhugo.clubeo.com/page/contact"}],          # same org, inner page
            "s2", "list page")
        self.assertEqual((3, 2), (out["orgs_added"], out["orgs_merged"]))
        orgs = ws.read_csv(pdir / "orgs.csv")
        self.assertEqual(6, len(orgs))
        self.assertEqual(6, len({o["id"] for o in orgs}))                    # every organisation its own id

    def test_stop_without_a_run_says_so(self):
        self.assertFalse(server.run("t9", stop=True)["stopped"])

    def test_one_person_two_addresses_is_one_route(self):
        import mo_export
        emails = [{"org_id": "o1", "grade": "A", "person": "Ann Lee", "value": "a@x.ch"},
                  {"org_id": "o1", "grade": "A", "person": "ann  lee", "value": "ann@x.ch"},
                  {"org_id": "o1", "grade": "B", "person": "", "value": "platz@x.ch"},
                  {"org_id": "o1", "grade": "B", "person": "", "value": "v@x.ch", "review": "yes"}]
        m = mo_export.metrics([{"id": "o1"}, {"id": "o2"}], emails)
        self.assertEqual((2, 1, 50.0, 100.0), (m["ab_routes"], m["ab_to_review"], m["ab_org_share"],
                                               m["ab_routes_per_100_orgs"]))

    def test_workers_share_a_queue(self):
        pdir = ws.project_dir("t10")
        (pdir / "orgs.csv").write_text("id,name,website\n" + "".join(f"o{i},Org {i},o{i}.ch\n" for i in range(5)),
                                       encoding="utf-8")
        for i in range(5):   # dig packages hold only organisations the pipeline has finished
            ws.append_csv(pdir / "crawl.csv", ws.CRAWL_COLS, {"org_id": f"o{i}", "pass": "final", "status": "done"})
        self.assertEqual(3, server.tasks("t10", action="plan", kind="dig", size=2)["planned"])
        self.assertEqual(0, server.tasks("t10", action="plan", kind="dig", size=2)["planned"])  # no double booking
        a = server.tasks("t10", action="claim", agent="w1")
        b = server.tasks("t10", action="claim", agent="w2")
        self.assertNotEqual(a["task"]["task_id"], b["task"]["task_id"])
        self.assertEqual(["o0", "o1"], [i["org_id"] for i in a["items"]])
        server.tasks("t10", action="done", task_id=a["task"]["task_id"], note="ok")
        server.tasks("t10", action="done", task_id=b["task"]["task_id"], release=True)
        q = server.tasks("t10")["queue"]["dig"]
        self.assertEqual((2, 0, 1), (q["open"], q["claimed"], q["done"]))
        server.tasks("t10", action="add", title="pool: federation list district 15")
        self.assertEqual(1, server.tasks("t10")["queue"]["custom"]["open"])

    def test_shards_split_the_pool_without_overlap(self):
        orgs = [{"id": f"o{i}"} for i in range(7)]
        parts = [mo_run._mine(orgs, (k, 3)) for k in range(3)]
        self.assertEqual(7, sum(map(len, parts)))
        self.assertFalse(set(o["id"] for o in parts[0]) & set(o["id"] for o in parts[1]))

    def test_weights_hold_providers_back_but_never_all(self):
        now, held = mo_providers.pick("q", ["deepseek", "serpapi"], {"deepseek": 1.0, "serpapi": 0.0})
        self.assertEqual((["deepseek"], ["serpapi"]), (now, held))
        now, held = mo_providers.pick("q", ["serpapi"], {"serpapi": 0.0})
        self.assertEqual(["serpapi"], now)
        now, _ = mo_providers.pick("u", ["crawl4ai", "deepseek"], {"crawl4ai": 0.0, "deepseek": 0.0},
                                   always=("crawl4ai",))
        self.assertIn("crawl4ai", now)

    def test_contact_files_are_per_organisation_and_merge_safely(self):
        pdir = ws.project_dir("t13")
        ws.merge_contacts(pdir, [{"kind": "email", "value": "a@x.ch", "quote": "q", "page_url": "u"}], "o1", "X", "site")
        ws.merge_contacts(pdir, [{"kind": "email", "value": "b@y.ch", "quote": "q", "page_url": "u"}], "o2", "Y", "deep1")
        self.assertEqual(2, len(list((pdir / "contacts").glob("*.csv"))))
        rows = ws.read_contacts(pdir)
        rows[0]["grade"] = "B"
        ws.update_contacts(pdir, rows[:1], ["grade"])
        ws.merge_contacts(pdir, [{"kind": "email", "value": "c@x.ch", "quote": "q", "page_url": "u"}], "o1", "X", "site")
        got = {r["value"]: r for r in ws.read_contacts(pdir)}
        self.assertEqual(3, len(got))                      # nothing lost between the two writers
        self.assertEqual("B", got[rows[0]["value"]]["grade"])
        self.assertEqual("deep1", got["b@y.ch"]["found_in"])

    def test_pipeline_starts_from_nothing_and_flows(self):
        pdir = ws.project_dir("t15")
        (pdir / "brief.json").write_text(json.dumps({"goal": "indoor sports", "ablate": ["dig", "evidence"],
                                                     "focus": {"find": ["gymnase"], "areas": ["Paris 1er"]}}),
                                         encoding="utf-8")
        maps = {"results": [{"title": "FC Beispiel", "url": "https://fc-beispiel.ch/"}], "routes": {}}
        with mock.patch.object(mo_providers, "search", return_value=maps), \
                mock.patch.object(mo_providers, "read", side_effect=fake_read), \
                mock.patch.object(mo_providers, "public_url", side_effect=lambda u: u), \
                mock.patch("mo_grade.auto", side_effect=lambda p, **k: ws.update_contacts(
                    p, [{**r, "grade": "A"} for r in ws.read_contacts(p) if r["value"].startswith("praesident")],
                    ["grade"])):
            quiet = mo_run.run_sync(pdir, rounds=2, workers=2)   # building pools is agent work: no discovery
            self.assertEqual(0, quiet["progress"]["flow"]["done"])
            state = mo_run.run_sync(pdir, rounds=2, workers=2, discover=True)   # a finder asked for it
        self.assertEqual(1, state["progress"]["flow"]["done"])          # found by discovery, then worked
        self.assertIn("praesident@fc-beispiel.ch", {r["value"] for r in ws.read_contacts(pdir)})
        self.assertFalse(any(r.get("pass", "").startswith("deep") for r in ws.read_csv(pdir / "crawl.csv")))
        book = zipfile.ZipFile(state["export"]["file"]).read("xl/workbook.xml").decode()
        self.assertIn("数据源", book)
        self.assertIn("贡献", book)
        self.assertIn("过程", book)

    def test_a_shared_portal_is_not_crawled_as_the_orgs_own_site(self):
        pdir = ws.project_dir("t17")
        ws.write_csv(pdir / "orgs.csv", ["id", "name", "website"],
                     [{"id": f"o{i}", "name": f"Gymnase {i}", "website": f"https://portal.fr/lieux/g{i}"} for i in range(3)])
        page = ('<html><body><a href="/lieux/g0/contact">Contact</a> <a href="/lieux/g1/contact">Contact</a>'
                ' <a href="/contact">Contact</a> <a href="/equipe">Equipe contact</a></body></html>')
        seen = []

        def read(url, readers=None, project_dir=None):
            seen.append(url)
            return {"crawl4ai": {"final_url": url, "html": page, "text": ""}}
        with mock.patch.object(mo_providers, "read", side_effect=read), \
                mock.patch.object(mo_providers, "public_url", side_effect=lambda u: u):
            row = ws._crawl_one(pdir, {"id": "o0", "name": "Gymnase 0", "website": "https://portal.fr/lieux/g0"}, 10, None)
        self.assertEqual({"https://portal.fr/lieux/g0", "https://portal.fr/lieux/g0/contact"}, set(seen))
        self.assertIn("shared portal", row["note"])

    def test_same_address_same_text_is_graded_once_per_org_and_rules_need_no_jev(self):
        import mo_grade
        pdir = ws.project_dir("t18")
        (pdir / "brief.json").write_text(json.dumps({"address_rules": {"info": "X"}}), encoding="utf-8")
        for oid in ("o1", "o2", "o3"):
            ws.merge_contacts(pdir, [{"kind": "email", "value": "dsp@city.fr", "quote": "Direction des sports",
                                      "page_url": "u"}, {"kind": "email", "value": "info@city.fr",
                                                         "quote": "q", "page_url": "u"}], oid, oid, "site")
        sent = []

        class Pick:
            choice, probabilities, noul = "team_inbox", {"team_inbox": 0.9}, 0.1

        def jev(state, questions):
            sent.append(json.dumps(questions, default=str, ensure_ascii=False))
            return type("R", (), {"answers": {q: Pick() for q in questions}})()
        sdk = type(sys)("typesafe_sdk")
        sdk.Choice = lambda **kw: kw
        sdk.Noul = lambda **kw: kw
        with mock.patch.dict(sys.modules, {"typesafe_sdk": sdk}), \
                mock.patch("mo_config.key", return_value="k"), mock.patch("mo_budget.allowed", return_value=(True, "")), \
                mock.patch.object(mo_grade, "_jev", side_effect=jev), mock.patch.object(mo_grade, "fill_people", return_value=0), \
                mock.patch("mo_evidence.rescore", return_value={}):
            mo_grade.auto(pdir)
        self.assertEqual(1, len(sent))
        self.assertEqual(6, len(json.loads(sent[0])))  # one grade + one name question per organisation, not one for all
        grades = {(r["org_id"], r["value"]): (r["grade"], r["graded_by"]) for r in ws.read_contacts(pdir)}
        self.assertEqual({("C", "jev")}, {v for (o, e), v in grades.items() if e.startswith("dsp")})
        self.assertEqual({("X", "rule")}, {v for (o, e), v in grades.items() if e.startswith("info")})
        self.assertNotIn("info@city.fr", sent[0])                     # graded on arrival, never asked

    def test_an_address_under_two_orgs_is_graded_for_each_and_reviewed_when_neither_is_named(self):
        import mo_grade
        pdir = ws.project_dir("t44")
        ws.write_csv(pdir / "orgs.csv", ["id", "name", "website"],
                     [{"id": "o1", "name": "Stade Alpha", "website": "https://alpha.fr"},
                      {"id": "o2", "name": "Gymnase Beta", "website": "https://beta.fr"}])
        for oid, name in (("o1", "Stade Alpha"), ("o2", "Gymnase Beta")):
            ws.merge_contacts(pdir, [{"kind": "email", "value": "chef.secteur@city.fr", "quote": "Chef de secteur, Jean Ex",
                                      "page_url": "https://city.fr/p"}], oid, name, "site")
        seen = []

        class Pick:
            choice, probabilities, noul = "operator_or_forwarder", {"operator_or_forwarder": 0.9}, 0.9

        def jev(state, questions):
            seen.extend(str(q) for q in questions.values())
            return type("R", (), {"answers": {q: Pick() for q in questions}})()
        sdk = type(sys)("typesafe_sdk")
        sdk.Choice = lambda **kw: kw
        sdk.Noul = lambda **kw: kw
        with mock.patch.dict(sys.modules, {"typesafe_sdk": sdk}), \
                mock.patch("mo_config.key", return_value="k"), mock.patch("mo_budget.allowed", return_value=(True, "")), \
                mock.patch.object(mo_grade, "_jev", side_effect=jev), mock.patch.object(mo_grade, "fill_people", return_value=0), \
                mock.patch("mo_evidence.rescore", return_value={}):
            mo_grade.auto(pdir)
        self.assertTrue(any("Stade Alpha" in q for q in seen) and any("Gymnase Beta" in q for q in seen))
        rows = {r["org_id"]: r for r in ws.read_contacts(pdir)}
        self.assertEqual({"yes"}, {rows[o]["review"] for o in ("o1", "o2")})
        self.assertIn("another organisation", rows["o1"]["review_reason"])

    def test_a_page_is_as_fast_as_its_fastest_reader_and_late_readers_still_count(self):
        import time
        pdir = ws.project_dir("t20")
        body = "<p>Contact</p>" + " " * 300

        def read(url, readers=None, project_dir=None):
            r = (readers or ["crawl4ai"])[0]
            if r == "deepseek":
                time.sleep(1.5)
                return {r: {"final_url": url, "html": body + "<p>Président Jean Ex</p><p>president@club-y.fr</p>", "text": ""}}
            return {r: {"final_url": url, "html": body + "<p>info@club-y.fr</p>", "text": ""}}
        with mock.patch.object(mo_providers, "read", side_effect=read), \
                mock.patch.object(mo_providers, "public_url", side_effect=lambda u: u), \
                mock.patch("mo_config.key", side_effect=lambda p: "k" if p in ("tinyfish", "deepseek") else ""), \
                mock.patch.object(mo_providers, "read_race", side_effect=lambda *a, **k: real_race(*a, **{**k, "grace": 0.1})):
            t0 = time.time()
            ws.fetch(pdir, "https://club-y.fr/contact", None, "o1", "Club Y", "club-y.fr")
            self.assertLess(time.time() - t0, 1.2)                     # did not wait for the slow reader
            time.sleep(2)
        self.assertIn("president@club-y.fr", {r["value"] for r in ws.read_contacts(pdir)})   # merged when it came

    def test_a_club_without_website_gets_one_and_its_federation_page_counts(self):
        pdir = ws.project_dir("t21")
        ws.write_csv(pdir / "orgs.csv", ["id", "name", "city"], [{"id": "c1", "name": "Union Sportive Jean-Jaurès", "city": "Lyon"}])
        hits = {"results": [{"title": "Annuaire des clubs", "url": "https://annuaire.example/club/88"},
                            {"title": "US Jean Jaurès - Accueil", "url": "https://usjj.fr/"}], "routes": {}}
        with mock.patch.object(mo_providers, "search", return_value=hits):
            self.assertEqual("https://usjj.fr/", mo_run._find_site(pdir, ws.read_csv(pdir / "orgs.csv")[0]))
        self.assertEqual("search", ws.read_csv(pdir / "orgs.csv")[0]["website_from"])
        self.assertTrue(mo_run._mentions("Union Sportive Jean-Jaurès", "UNION SPORTIVE JEAN JAURES — Président"))
        self.assertFalse(mo_run._mentions("Union Sportive Jean-Jaurès", "Stade Pasteur"))

    def test_people_pages_first_and_hosted_club_pages_followed(self):
        links = [("https://fc-x.fr/", "Accueil"), ("https://fc-x.fr/contact", "Contact"),
                 ("https://fc-x.fr/le-bureau", "Le bureau du club"), ("https://fc-x.fr/terrains", "Nos terrains sport"),
                 ("https://fcx.clubeo.com/page/comite", "Comité"), ("https://other-club.fr/contact", "Contact")]
        ranked = mo_pages.contact_links(links, "https://fc-x.fr/")
        self.assertEqual("https://fc-x.fr/le-bureau", ranked[0])
        self.assertLess(ranked.index("https://fc-x.fr/contact"), ranked.index("https://fc-x.fr/terrains"))
        self.assertIn("https://fcx.clubeo.com/page/comite", ranked)            # club site on a hosting platform
        self.assertNotIn("https://other-club.fr/contact", ranked)               # another organisation's site

    def test_a_source_earns_or_loses_turns_by_its_own_yield(self):
        self.assertEqual(1, mo_run._turns(None))
        self.assertEqual(1, mo_run._turns((10, 0)))         # no sample yet
        self.assertEqual(2, mo_run._turns((30, 4)))         # >= 10%
        self.assertEqual(1, mo_run._turns((40, 3)))         # 5-10%
        self.assertEqual(0, mo_run._turns((40, 1)))         # < 5%: waits until the others are empty

    def test_role_mailboxes_go_to_jev_not_to_d(self):
        lines = ["Secrétariat du club", "secretariat@fc-x.fr", "Service des sports", "service.sports@ville.fr",
                 "info@fc-x.fr"]
        kinds = {c["value"]: c["mailbox"] for c in mo_pages.contacts(lines, "https://fc-x.fr/contact", "fc-x.fr")
                 if c["kind"] == "email"}
        self.assertEqual("named_or_role", kinds["secretariat@fc-x.fr"])
        self.assertEqual("named_or_role", kinds["service.sports@ville.fr"])
        self.assertEqual("generic", kinds["info@fc-x.fr"])

    def test_sources_take_turns_and_every_org_leaves_a_trace(self):
        pdir = ws.project_dir("t19")
        (pdir / "brief.json").write_text(json.dumps({"ablate": ["dig", "evidence", "reverify"]}), encoding="utf-8")
        orgs = [{"id": f"r{i}", "name": f"R{i}", "sources": "register"} for i in range(6)] + \
               [{"id": "c1", "name": "C1", "sources": "clubs"}]
        ws.write_csv(pdir / "orgs.csv", ["id", "name", "sources"], orgs)
        mo_run.run_sync(pdir, rounds=1, workers=1)
        traces = [json.loads(x) for x in (pdir / "trace.jsonl").read_text(encoding="utf-8").splitlines()]
        self.assertEqual(7, len(traces))
        self.assertIn("clubs", [t["source"] for t in traces[:2]])    # the late source is not last in line
        self.assertIn("site", traces[0]["seconds"])

    def test_discovery_adds_organisations_and_logs_sources(self):
        import mo_discover
        pdir = ws.project_dir("t14")
        (pdir / "brief.json").write_text(json.dumps({"goal": "indoor sports", "focus": {
            "find": ["gymnase"], "areas": ["Paris 15e"]}}), encoding="utf-8")
        maps = {"results": [{"title": "Gymnase Alpha", "url": "https://alpha.fr", "address": "1 rue",
                             "snippet": "Gym"}], "routes": {}}
        listing = ("<header><a href='https://bcorp.fr/'>B Corp</a></header><ul>"
                   + "".join(f"<li><a href='https://club-{n}.fr/x'>Club {n.title()}</a></li>"
                             for n in ("beta", "gamma", "delta", "epsilon", "zeta"))
                   + "<li><a href='/in'>In</a></li></ul>")
        with mock.patch.object(mo_providers, "search", return_value=maps), \
                mock.patch.object(mo_providers, "read", return_value={
                    "crawl4ai": {"final_url": "https://annuaire.fr/", "html": listing, "text": ""}}), \
                mock.patch.object(mo_providers, "public_url", side_effect=lambda u: u):
            out = mo_discover.discover(pdir, lambda: False)
        names = {o["name"] for o in ws.read_csv(pdir / "orgs.csv")}
        self.assertEqual({"Gymnase Alpha", "Club Beta", "Club Gamma", "Club Delta", "Club Epsilon", "Club Zeta"},
                         names)                                     # the header link is not an organisation
        self.assertEqual(6, out["new_orgs"])
        kinds = {r["kind"] for r in ws.read_csv(pdir / "sources.csv")}
        self.assertEqual({"maps", "list page"}, kinds)
        self.assertEqual(0, mo_discover.discover(pdir, lambda: False)["new_orgs"])  # each combination once


class Search(unittest.TestCase):
    def test_one_engine_failing_never_fails_the_search(self):
        os.environ["SERPER_API_KEY"] = os.environ["TINYFISH_API_KEY"] = "k" * 10
        try:
            def boom(*a):
                raise mo_providers.ProviderError("HTTP 500")
            with mock.patch.dict(mo_providers._SEARCH, {
                    "serper": lambda *a: [{"url": "https://www.a.ch/", "title": "A"}],
                    "tinyfish": boom}), \
                    mock.patch("mo_budget.allowed", return_value=(True, "")):
                out = mo_providers.search("q")
            self.assertEqual({"results": 1}, out["routes"]["serper"])
            self.assertIn("skipped", out["routes"]["tinyfish"])
            self.assertEqual(["serper"], out["results"][0]["found_by"])
        finally:
            os.environ.pop("SERPER_API_KEY")
            os.environ.pop("TINYFISH_API_KEY")


class Supervision(unittest.TestCase):
    def test_a_shard_that_dies_is_restarted_and_finishes(self):
        pdir = ws.project_dir("t40")
        marker = pdir / "died-once"
        script = ("import json,pathlib,sys; p=pathlib.Path(sys.argv[1]); m=p/'died-once'\n"
                  "if not m.exists(): m.write_text('x'); sys.exit(1)\n"
                  "(p/'run.json').write_text(json.dumps({'file':'run.json','done':True,'stage':'done'}))")
        with mock.patch.object(mo_run, "_shard_cmd", side_effect=lambda *a: [sys.executable, "-c", script, str(pdir)]):
            out = mo_run.supervise(pdir, 1, {}, poll=0.2)
        self.assertTrue(marker.exists())
        self.assertGreaterEqual(out["restarts"][0], 1)
        self.assertTrue(json.loads((pdir / "run.json").read_text())["done"])

    def test_no_restart_after_a_stop(self):
        pdir = ws.project_dir("t41")
        (pdir / "STOP").write_text("now")   # a stop asked while the run goes (a stale one is cleared at start)
        with mock.patch.object(mo_run, "_shard_cmd", side_effect=lambda *a: [sys.executable, "-c", "import sys; sys.exit(1)"]):
            out = mo_run.supervise(pdir, 1, {}, poll=0.2, clear_stop=False)
        self.assertEqual(0, out["restarts"][0])

    def test_open_decisions_come_as_one_numbered_list(self):
        pdir = ws.project_dir("t42")
        (pdir / "brief.json").write_text(json.dumps({"goal": "x"}), encoding="utf-8")
        ws.merge_contacts(pdir, [{"kind": "email", "value": "a@x.fr", "quote": "q", "page_url": "u"}], "o1", "X", "site")
        ws.update_contacts(pdir, [{"org_id": "o1", "value": "a@x.fr", "grade": "A", "review": "yes"}], ["grade", "review"])
        items = server.needs_you(pdir, {"accounts": {}})
        self.assertTrue(items[0].startswith("1. Set country and language"))
        self.assertTrue(any("1 A/B rows are marked review" in i for i in items))
        items = server.needs_you(pdir, {"accounts": {"serper": {"balance": {"left": -28.0, "unit": "credits"},
                                                                 "red_line": 250.0, "skipped": None},
                                                      "deepseek": {"balance": {"left": 40.0}, "red_line": 1.0}}})
        self.assertTrue(any(i.split(". ", 1)[1].startswith("serper is out of credit") for i in items))
        self.assertFalse(any("deepseek" in i for i in items))


class DeadSites(unittest.TestCase):
    def test_a_domain_that_does_not_resolve_is_not_read(self):
        import socket
        pdir = ws.project_dir("t43")
        ws._dns_ok[:] = [True]
        try:
            with mock.patch.object(socket, "getaddrinfo", side_effect=OSError("no such host")), \
                 mock.patch("time.sleep"), \
                 mock.patch.object(mo_providers, "read_race", side_effect=AssertionError("read a dead site")):
                row = ws._crawl_one(pdir, {"id": "o1", "name": "X", "website": "https://gone.example"}, 4, None, None, "site")
        finally:
            ws._dns_ok[:] = [False]
        self.assertEqual("no_dns", row["status"])


class Locks(unittest.TestCase):
    def test_an_org_an_agent_holds_waits_and_dig_packages_only_finished_orgs(self):
        import mo_tasks
        pdir = ws.project_dir("t47")
        ws.write_csv(pdir / "orgs.csv", ["id", "name", "website"],
                     [{"id": "o1", "name": "A", "website": ""}, {"id": "o2", "name": "B", "website": ""}])
        ws.append_csv(pdir / "crawl.csv", ws.CRAWL_COLS, {"org_id": "o1", "pass": "final", "status": "done"})
        out = mo_tasks.plan(pdir, "dig")
        self.assertEqual(1, out["items"])                       # o2 is still in the pipeline
        task = mo_tasks.claim(pdir, "w1", "dig")["task"]
        self.assertEqual({"o1"}, mo_tasks.held_orgs(pdir))
        mo_tasks.done(pdir, task["task_id"], "found one")
        self.assertEqual(set(), mo_tasks.held_orgs(pdir))
        out = mo_tasks.plan(pdir, "sweep")   # o2 has no site pass yet: an agent can sweep it itself
        self.assertEqual(1, out["items"])
        claimed = mo_tasks.claim(pdir, "w2", "sweep")
        self.assertEqual(["o2"], [i["org_id"] for i in claimed["items"]])
        self.assertEqual({"o2"}, mo_tasks.held_orgs(pdir))


class AgentParts(unittest.TestCase):
    def test_playbook_merges_and_applies_by_segment(self):
        pdir = ws.project_dir("t48")
        ws.update_playbook(pdir, {"Commune": {"paths": ["/annuaire"], "queries": ['"{name}" {city} chef']}})
        ws.update_playbook(pdir, {"commune": {"paths": ["/annuaire", "/services/sports"], "roles": ["chef"]},
                                  "*": {"paths": ["/contact"]}})
        pb = ws.playbook_for(pdir, {"type": "Commune"})
        self.assertEqual(["/contact", "/annuaire", "/services/sports"], pb["paths"])
        self.assertEqual(["chef"], pb["roles"])
        self.assertEqual([('"Stade X" Lyon chef', False)],
                         mo_run._playbook_queries({"name": "Stade X", "city": "Lyon"}, pb["queries"]))

    def test_playbook_pages_come_first_and_a_site_is_read_while_pages_bring_new_addresses(self):
        pdir = ws.project_dir("t49")
        order = []

        def fetch(pdir_, url, readers, oid, org, site, via="site", **kw):
            order.append(url)
            n = len(order)
            links = [(f"https://club.fr/bureau?page={n}", 10)]   # a directory over many pages
            return {"readers": {"crawl4ai": {}}, "contacts": [], "contacts_added": 1 if n <= 20 else 0,
                    "email_buttons_without_address": 0, "scored_links": links}
        with mock.patch.object(ws, "fetch", side_effect=fetch):
            row = ws._crawl_one(pdir, {"id": "o1", "name": "Club", "website": "https://club.fr"}, 5, None,
                                hint_paths=["/bureau"])
        self.assertEqual("https://club.fr/bureau", order[1])           # the playbook's page right after home
        self.assertGreater(row["pages"], 20)                           # read on past max_pages while new addresses came
        self.assertLessEqual(row["pages"], 20 + ws.DRY_PAGES_STOP + ws.PAGE_PARALLEL)

    def test_a_batch_job_sweeps_only_its_organisations(self):
        pdir = ws.project_dir("t50")
        (pdir / "brief.json").write_text(json.dumps({"goal": "x", "ablate": ["dig", "evidence", "findsite"]}),
                                         encoding="utf-8")
        ws.write_csv(pdir / "orgs.csv", ["id", "name", "website"],
                     [{"id": f"o{i}", "name": f"Org {i}", "website": ""} for i in range(4)])
        with mock.patch("mo_grade.auto", return_value={}):
            state = mo_run.run_sync(pdir, rounds=1, workers=2, job={"id": "b1", "orgs": ["o1", "o3"]})
        self.assertEqual("batch.b1.json", state["file"])
        self.assertEqual({"o1", "o3"}, {r["org_id"] for r in ws.read_csv(pdir / "crawl.csv") if r.get("pass") == "final"})
        self.assertEqual({}, state["export"])

    def test_org_summaries_and_verdicts_tell_an_agent_what_is_left(self):
        pdir = ws.project_dir("t51")
        ws.write_csv(pdir / "orgs.csv", ["id", "name", "website", "type"],
                     [{"id": "o1", "name": "A", "website": "a.fr", "type": "Club"},
                      {"id": "o2", "name": "B", "website": "b.fr", "type": "Club"}])
        for oid in ("o1", "o2"):
            ws.append_csv(pdir / "crawl.csv", ws.CRAWL_COLS, {"org_id": oid, "pass": "final", "status": "done"})
        ws.merge_contacts(pdir, [{"kind": "email", "value": "p@a.fr", "quote": "Président", "page_url": "u"}], "o1", "A", "site")
        ws.update_contacts(pdir, [{"org_id": "o1", "value": "p@a.fr", "grade": "A"}], ["grade"])
        self.assertEqual(["o2"], [o["org_id"] for o in server.orgs("t51", stage="no_ab")["orgs"]])
        server.tasks("t51", action="verdict", agent="w1",
                     verdicts=[{"org_id": "o2", "verdict": "none", "found": "-", "where": "site, federation", "why": "no names"}])
        self.assertEqual(0, server.orgs("t51", stage="no_ab")["total"])
        self.assertEqual({"updated": 1}, server.orgs("t51", set=[{"org_id": "o2", "website": "https://b.fr/",
                                                                  "segment": "club", "name": "ignored"}]))
        row = {r["id"]: r for r in ws.read_csv(pdir / "orgs.csv")}["o2"]
        self.assertEqual(("https://b.fr/", "club", "B"), (row["website"], row["segment"], row["name"]))
        out = server.orgs("t51", add=[{"name": "SIVOM 4", "city": "Lyon", "website": "https://sivom4.fr"},
                                      {"name": "B", "website": "https://b.fr/"}], agent="r1")
        self.assertEqual((1, 1), (out["orgs_added"], out["orgs_merged"]))   # B is the same organisation
        self.assertEqual(1, ws.stages(pdir)["dig"]["agent_verdicts"])

    def test_spot_checks_give_the_precision(self):
        pdir = ws.project_dir("t52")
        ws.merge_contacts(pdir, [{"kind": "email", "value": f"p{k}@a.fr", "quote": "q", "page_url": "u"} for k in range(3)],
                          "o1", "A", "site")
        ws.update_contacts(pdir, [{"org_id": "o1", "value": f"p{k}@a.fr", "grade": "B"} for k in range(3)], ["grade"])
        server.grade("t52", set=[{"value": "p0@a.fr", "org_id": "o1", "checked": "ok"},
                                 {"value": "p1@a.fr", "org_id": "o1", "checked": "wrong"}])
        rows = {r["value"]: r for r in ws.read_contacts(pdir)}
        self.assertEqual("", rows["p0@a.fr"].get("graded_by", ""))     # a check alone does not take the row over
        self.assertEqual("50.0%（1/2）", mo_export._precision(list(rows.values())))
        self.assertEqual(3, len(mo_export._check_sample(list(rows.values()))))


class SharedPages(unittest.TestCase):
    def test_a_page_read_for_one_org_is_read_again_for_another_and_credits_it(self):
        pdir = ws.project_dir("t53")
        html = ("<div><p>SIVOM 4 - communes membres : Stade Alpha, Stade Beta</p><p>Responsable</p>"
                "<p>Anne Ex</p><p>resp@sivom4.fr</p></div>" + " " * 300)
        calls = []

        def read(url, readers=None, project_dir=None):
            calls.append(url)
            return {"crawl4ai": {"final_url": url, "html": html, "text": ""}}
        with mock.patch.object(mo_providers, "read", side_effect=read), \
                mock.patch.object(mo_providers, "public_url", side_effect=lambda u: u):
            ws.fetch(pdir, "https://sivom4.fr/equipe", ["crawl4ai"], "o1", "Stade Alpha", "")
            self.assertNotIn("https://sivom4.fr/equipe", ws.read_for(pdir, "o2"))      # still a lead for o2
            ws.fetch(pdir, "https://sivom4.fr/equipe", ["crawl4ai"], "o2", "Stade Beta", "")
        self.assertEqual(1, len(calls))                                               # second read: no new call
        self.assertIn("https://sivom4.fr/equipe", ws.read_for(pdir, "o2"))
        self.assertEqual({"o1", "o2"}, {r["org_id"] for r in ws.read_contacts(pdir) if r["value"] == "resp@sivom4.fr"})

    def test_a_refused_tinyfish_key_is_an_error_not_a_crash(self):
        fake = {n: type(sys)(n) for n in ("mcp", "mcp.client", "mcp.client.streamable_http")}
        fake["mcp"].ClientSession = object
        fake["mcp.client.streamable_http"].streamable_http_client = object   # CI installs no mcp package
        with mock.patch.dict(sys.modules, fake), mock.patch("mo_config.key", return_value="bad"), \
                mock.patch("asyncio.run", side_effect=RuntimeError("Unauthorized: Valid OAuth Bearer token required")):
            with self.assertRaises(mo_providers.ProviderError) as e:
                mo_providers.tinyfish_mcp("wallet", {}, timeout=1)
        self.assertIn("refused the key", str(e.exception))


class SiteHealth(unittest.TestCase):
    def setUp(self):
        import mo_health
        self.h = mo_health
        os.environ["MARKETOPS_HEALTH"] = "1"
        os.environ["MARKETOPS_LIBRARY"] = tempfile.mkdtemp()
        mo_health.reset_for_tests()

    def tearDown(self):
        os.environ["MARKETOPS_HEALTH"] = "0"
        os.environ.pop("MARKETOPS_LIBRARY", None)
        self.h.reset_for_tests()

    def test_one_failure_sets_nothing_aside_two_readers_do(self):
        H = self.h.HEALTH
        H.record("https://dead.fr/a", "crawl4ai", False, "target_unreachable")
        self.assertEqual("", H.skip("https://dead.fr/b", "tinyfish"))       # one failure is a hint
        H.record("https://dead.fr/a", "tinyfish", False, "ConnectError: Name or service not known")
        self.assertIn("site set aside", H.skip("https://dead.fr/b", "crawl4ai"))
        self.assertLessEqual(self.h.HEALTH.status("https://dead.fr")["reach"], 50)

    def test_a_404_marks_the_page_not_the_site(self):
        H = self.h.HEALTH
        for r in ("crawl4ai", "deepseek"):
            H.record("https://club.fr/old", r, False, "HTTP 404")
        self.assertIn("page set aside", H.skip("https://club.fr/old", "tinyfish"))
        self.assertEqual("", H.skip("https://club.fr/bureau", "tinyfish"))

    def test_a_block_is_for_one_reader_and_our_own_errors_do_not_count(self):
        H = self.h.HEALTH
        H.record("https://cf.fr/", "crawl4ai", False, "Cloudflare JS challenge")
        H.record("https://cf.fr/x", "crawl4ai", False, "Cloudflare JS challenge")
        self.assertIn("blocks crawl4ai", H.skip("https://cf.fr/y", "crawl4ai"))
        self.assertEqual("", H.skip("https://cf.fr/y", "tinyfish"))
        for r in ("crawl4ai", "tinyfish", "deepseek"):
            self.assertEqual("ignored", H.record("https://ok.fr/", r, False, "ProxyError: proxy_error"))
        self.assertEqual("", H.skip("https://ok.fr/", "crawl4ai"))

    def test_a_success_restores_and_many_sites_failing_at_once_is_our_network(self):
        H = self.h.HEALTH
        for r in ("crawl4ai", "tinyfish"):
            H.record("https://back.fr/", r, False, "target_unreachable")
        H.record("https://back.fr/", "deepseek", True)
        self.assertEqual("", H.skip("https://back.fr/", "crawl4ai"))
        self.assertEqual(100, H.status("https://back.fr")["reach"])
        for i in range(25):   # 25 different sites fail within a minute: not their fault
            H.record(f"https://s{i}.fr/", "crawl4ai", False, "ConnectError")
        H.record("https://victim.fr/", "crawl4ai", False, "target_unreachable")
        self.assertEqual("ignored", H.record("https://victim.fr/", "tinyfish", False, "target_unreachable"))
        self.assertEqual("", H.skip("https://victim.fr/", "crawl4ai"))

    def test_other_processes_see_the_same_health(self):
        self.h.HEALTH.record("https://gone.fr/p", "crawl4ai", False, "HTTP 404")
        self.h.HEALTH.record("https://gone.fr/p", "tinyfish", False, "page_not_found")
        other = self.h._Health()          # a second process reading the same file
        other._catch_up(force=True)
        self.assertIn("page set aside", other.skip("https://gone.fr/p", "deepseek"))


class Journal(unittest.TestCase):
    def test_agents_and_the_plugin_write_the_journal_and_export_writes_a_masked_retro(self):
        import mo_journal
        pdir = ws.project_dir("t54")
        ws.write_csv(pdir / "orgs.csv", ["id", "name", "website"], [{"id": "o1", "name": "A", "website": "a.fr"}])
        server.project("t54", journal=[{"type": "blocker", "text": "mairie04.paris.fr is dead; asked jean.ex@paris.fr",
                                        "evidence": "+33 1 23 45 67 89"},
                                       {"type": "smooth", "text": "review caught 9 misattached addresses"}], agent="dig-1")
        self.assertTrue(mo_journal.auto(pdir, "fail:tinyfish", "tinyfish failing"))
        self.assertFalse(mo_journal.auto(pdir, "fail:tinyfish", "tinyfish failing"))   # once an hour
        for k in range(40):
            mo_budget.record(pdir, "tinyfish", "fetch", k < 5, "TinyFishError: bot_blocked | https://x.fr")
        f = mo_journal.failures(pdir)["tinyfish"]
        self.assertEqual((40, 35, {"blocked": 35}), (f["calls"], f["failed"], f["by_kind"]))
        out = mo_export.export(pdir)
        text = Path(out["retro"]).read_text(encoding="utf-8")
        self.assertIn("## Blockers", text)
        self.assertIn("j***@paris.fr", text)
        self.assertNotIn("jean.ex@paris.fr", text)
        self.assertNotIn("45 67 89", text)
        self.assertIn("| tinyfish | 40 | 35 | blocked 35 |", text)


class Knowledge(unittest.TestCase):
    def setUp(self):
        import mo_knowledge
        self.k = mo_knowledge
        os.environ["MARKETOPS_KNOWLEDGE"] = "1"
        os.environ["MARKETOPS_LIBRARY"] = tempfile.mkdtemp()
        mo_knowledge.reset_for_tests()

    def tearDown(self):
        os.environ["MARKETOPS_KNOWLEDGE"] = "0"
        os.environ.pop("MARKETOPS_LIBRARY", None)
        self.k.reset_for_tests()

    def test_untested_kinds_first_then_the_one_that_finds_people(self):
        K = self.k.KNOWLEDGE
        self.assertEqual(list(self.k.DIG_KINDS), K.dig_order("FR", "communes"))
        for kind, hits in (("terms", 3), ("site_pdf", 18), ("directories", 0), ("email", 9)):
            for n in range(29):
                K.record_dig("FR", "communes", kind, n < hits)
        self.assertEqual(list(self.k.DIG_KINDS), K.dig_order("FR", "communes"))   # 29 tries: not enough evidence
        for kind, hits in (("terms", 0), ("site_pdf", 1), ("directories", 0), ("email", 0)):
            K.record_dig("FR", "communes", kind, bool(hits))
        self.assertEqual(["site_pdf", "email", "terms", "directories"], K.dig_order("FR", "communes"))
        self.assertEqual(list(self.k.DIG_KINDS), K.dig_order("DE", "communes"))   # another market starts fresh
        q = mo_run._queries({"name": "Stade X", "website": "https://x.fr"}, 1, ["chef"], ["annuaire"],
                            K.dig_order("FR", "communes"))
        self.assertTrue(q[0][0].startswith("site:x.fr"))

    def test_a_playbook_carries_to_the_next_project_until_it_stops_working(self):
        p1, p2 = ws.project_dir("t55"), ws.project_dir("t56")
        for p in (p1, p2):
            (p / "brief.json").write_text(json.dumps({"country": "FR"}), encoding="utf-8")
        ws.update_playbook(p1, {"communes": {"paths": ["/services/sports"], "queries": ['"{name}" syndicat']}})
        pb = ws.playbook_for(p2, {"segment": "communes"})
        self.assertEqual(["/services/sports"], pb["paths"])
        self.assertIn("earlier projects", pb["learned"])
        for _ in range(12):
            self.k.KNOWLEDGE.record_use("FR", "communes", False)
        self.assertEqual([], ws.playbook_for(p2, {"segment": "communes"})["paths"])
        other = self.k._Knowledge()   # another process reads the same file
        self.assertEqual({}, other.playbook("FR", "communes"))


class Slots(unittest.TestCase):
    def setUp(self):
        self.home = tempfile.mkdtemp()
        self.env = mock.patch.dict(os.environ, {"MARKETOPS_HOME": self.home}, clear=False)
        self.env.start()
        for n in ("TYPESAFE_API_KEY", "TYPESAFE_API_KEY_2", "MARKETOPS_KEYS_FILE"):
            os.environ.pop(n, None)

    def tearDown(self):
        self.env.stop()

    def test_a_used_up_key_hands_over_to_the_next_and_a_new_key_needs_no_restart(self):
        import mo_budget
        import mo_config
        (Path(self.home) / "keys.env").write_text("TYPESAFE_API_KEY=key-one-aaaa\nTYPESAFE_API_KEY_2=key-two-bbbb\n")
        self.assertEqual("key-one-aaaa", mo_config.key("jev"))
        mo_budget.mark_exhausted("jev", "Jev refused for money")
        self.assertEqual("key-two-bbbb", mo_config.key("jev"))
        self.assertTrue(mo_budget.allowed("jev")[0])
        mo_budget.mark_exhausted("jev", "Jev refused for money")
        self.assertEqual("", mo_config.key("jev"))
        self.assertFalse(mo_budget.allowed("jev")[0])
        states = {s["state"] for s in mo_config.slot_status("jev")}
        self.assertEqual({"set aside"}, states)
        import time as _t
        _t.sleep(0.01)
        vault = Path(self.home) / "vault.env"     # a new key in a vault file, while running
        vault.write_text("export TYPESAFE_API_KEY_3=key-three-cccc\n")
        os.environ["MARKETOPS_KEYS_FILE"] = str(vault)
        self.assertEqual("key-three-cccc", mo_config.key("jev"))
        self.assertTrue(mo_budget.allowed("jev")[0])
        self.assertNotIn("key-three", json.dumps(mo_config.slot_status("jev")))   # fingerprints only

    def test_several_keys_in_one_value(self):
        import mo_config
        os.environ["TYPESAFE_API_KEY"] = "k-aaaaaaaa, k-bbbbbbbb"
        self.assertEqual(["k-aaaaaaaa", "k-bbbbbbbb"], mo_config.slots("jev"))

    def test_slots_have_no_upper_bound(self):
        import mo_config
        names = ["TYPESAFE_API_KEY"] + [f"TYPESAFE_API_KEY_{n}" for n in range(2, 26)]
        for n, name in enumerate(names, start=1):
            os.environ[name] = f"k-{n:08d}"
        try:
            got = mo_config.slots("jev")
        finally:
            for name in names:
                os.environ.pop(name, None)
        self.assertEqual([f"k-{n:08d}" for n in range(1, 26)], got)   # _10 comes after _9

    def test_a_generic_page_read_for_many_is_not_credited_to_one_it_does_not_name(self):
        pdir = ws.project_dir("t57")
        html = "<p>Réserver un créneau</p><p>reservation@paris.fr</p>" + " " * 300
        with mock.patch.object(mo_providers, "read", return_value={"crawl4ai": {"final_url": "u", "html": html, "text": ""}}), \
                mock.patch.object(mo_providers, "public_url", side_effect=lambda u: u):
            for oid, name in (("o1", "Gymnase Alpha"), ("o2", "Gymnase Beta"), ("o3", "Gymnase Gamma")):
                ws.fetch(pdir, "https://paris.fr/reserver", ["crawl4ai"], oid, name, "paris.fr")
        holders = {r["org_id"] for r in ws.read_contacts(pdir) if r["value"] == "reservation@paris.fr"}
        self.assertEqual({"o1", "o2"}, holders)   # the third reader of a page that names none of them adds nothing


class StepDown(unittest.TestCase):
    def test_with_nothing_set_up_the_plugin_steps_down_not_out(self):
        with mock.patch("mo_config.key", return_value=""), \
                mock.patch.object(mo_providers, "crawl4ai_installed", return_value=False):
            cap = mo_providers.capabilities()
            self.assertEqual("minimal", cap["level"])
            self.assertEqual(["http"], mo_providers.available_readers())
            self.assertIn("host's own web search", mo_providers.search("stade lyon")["error"])
            self.assertEqual("rules + agents", cap["grade"])
        with mock.patch("mo_config.key", side_effect=lambda p: "k" if p in ("serper", "deepseek", "jev", "tinyfish") else ""), \
                mock.patch("mo_budget.allowed", return_value=(True, "")), \
                mock.patch.object(mo_providers, "crawl4ai_installed", return_value=True):
            self.assertEqual("full", mo_providers.capabilities()["level"])

    def test_rules_grade_without_jev_and_spare_it_the_obvious_rows(self):
        import mo_grade
        pdir = ws.project_dir("t58")
        ws.merge_contacts(pdir, [
            {"kind": "email", "value": "info@club-a.fr", "quote": "Contact", "page_url": "https://club-a.fr/",
             "mailbox": "generic", "email_on_org": "yes", "page_on_org_site": "yes", "email_site": "club-a.fr"},
            {"kind": "email", "value": "jobs@agency.com", "quote": "Offre d'emploi", "page_url": "https://jobs.example/1",
             "mailbox": "named_or_role", "email_on_org": "no", "page_on_org_site": "no", "email_site": "agency.com"},
            {"kind": "email", "value": "president@club-a.fr", "quote": "Président Jean Ex", "page_url": "https://club-a.fr/b",
             "mailbox": "named_or_role", "email_on_org": "yes", "page_on_org_site": "yes", "email_site": "club-a.fr"}],
            "o1", "Club A", "site")
        with mock.patch("mo_config.key", return_value=""):
            out = mo_grade.auto(pdir)
        rows = {r["value"]: r for r in ws.read_contacts(pdir)}
        self.assertEqual("D", rows["info@club-a.fr"]["grade"])
        self.assertTrue(rows["info@club-a.fr"]["graded_by"].startswith("rule"))
        self.assertEqual("X", rows["jobs@agency.com"]["grade"])
        self.assertEqual("", rows["president@club-a.fr"].get("grade", ""))   # judgement: waits for Jev or an agent
        self.assertEqual(1, out["waiting_for_an_agent"])


class Reconcile(unittest.TestCase):
    def test_every_org_counted_once_under_its_best_grade(self):
        rows = [{"最佳等级": "A 关键岗位"}, {"最佳等级": "B 相关操作人（待复核）"}, {"最佳等级": "D 通用入口"},
                {"最佳等级": "未找到", "抓取状态": "未抓取"}]
        self.assertEqual("1 + 0 + 1 + 0 + 1 + 1（1） = 4 / 4，对得上", mo_export._reconcile(rows, 4))
        self.assertTrue(mo_export._reconcile(rows, 5).endswith("对不上，请检查"))

    def test_parallel_writers_never_take_each_others_temp_file(self):
        import threading
        path = Path(TMP) / "race.csv"
        errors = []

        def write(k):
            try:
                for _ in range(30):
                    ws.write_csv(path, ["a"], [{"a": str(k)}])
            except OSError as exc:
                errors.append(exc)
        threads = [threading.Thread(target=write, args=(k,)) for k in range(6)]
        [t.start() for t in threads]
        [t.join() for t in threads]
        self.assertEqual([], errors)
        self.assertEqual(1, len(ws.read_csv(path)))


class Ownership(unittest.TestCase):
    def test_another_venues_page_on_a_city_portal_is_not_this_venues_own(self):
        pdir = ws.project_dir("t45")
        ws.write_csv(pdir / "orgs.csv", ["id", "name", "website"],
                     [{"id": f"o{k}", "name": f"Gymnase {n}", "website": f"https://www.paris.fr/lieux/g{k}"}
                      for k, n in enumerate(["Alpha", "Beta", "Gamma"])])
        html = "<p>Chef de circonscription</p><p>Jean Ex</p><p>jean.ex@paris.fr</p>" + " " * 300
        with mock.patch.object(mo_providers, "read", return_value={"crawl4ai": {"final_url": "u", "html": html, "text": ""}}):
            own = ws.fetch(pdir, "https://www.paris.fr/lieux/g0/contact", readers=["crawl4ai"], org_id="o0",
                           org="Gymnase Alpha", org_site="paris.fr")
            other = ws.fetch(pdir, "https://www.paris.fr/lieux/g1", readers=["crawl4ai"], org_id="o0",
                             org="Gymnase Alpha", org_site="paris.fr")
        self.assertEqual("yes", own["contacts"][0]["page_on_org_site"])
        self.assertEqual("no", other["contacts"][0]["page_on_org_site"])
        self.assertEqual("unknown", other["contacts"][0]["email_on_org"])   # the portal's domain is nobody's

    def test_venues_on_one_portal_sharing_an_address_are_three_holders_not_one(self):
        import mo_grade
        pdir = ws.project_dir("t46")
        names = ["Gymnase Alpha", "Gymnase Beta", "Gymnase Gamma"]
        ws.write_csv(pdir / "orgs.csv", ["id", "name", "website"],
                     [{"id": f"o{k}", "name": n, "website": f"https://www.paris.fr/lieux/g{k}"} for k, n in enumerate(names)])
        for k, n in enumerate(names):
            ws.merge_contacts(pdir, [{"kind": "email", "value": "chef@paris.fr", "quote": "Chef de circonscription, Jean Ex",
                                      "page_url": "https://www.paris.fr/c"}], f"o{k}", n, "site")

        class Pick:
            choice, probabilities, noul = "decision_maker", {"decision_maker": 0.9}, 0.9

        sdk = type(sys)("typesafe_sdk")
        sdk.Choice = lambda **kw: kw
        sdk.Noul = lambda **kw: kw
        with mock.patch.dict(sys.modules, {"typesafe_sdk": sdk}), \
                mock.patch("mo_config.key", return_value="k"), mock.patch("mo_budget.allowed", return_value=(True, "")), \
                mock.patch.object(mo_grade, "_jev", side_effect=lambda st, q: type("R", (), {"answers": {k: Pick() for k in q}})()), \
                mock.patch.object(mo_grade, "fill_people", return_value=0), mock.patch("mo_evidence.rescore", return_value={}):
            mo_grade.auto(pdir)
        rows = ws.read_contacts(pdir)
        self.assertEqual({"yes"}, {r["review"] for r in rows})
        self.assertTrue(all("2 other" in r["grade_reason"] for r in rows))
        self.assertEqual({"B"}, {r["grade"] for r in rows})   # no name read for the A after the look-up -> B

    def test_a_name_counts_only_with_its_rare_word(self):
        from mo_pool import common_words, mentions
        counts = common_words(["HealthCity Paris Saint Germain"] + [f"Centre sportif Paris {k}" for k in range(9)]
                              + [f"Gymnase Saint Germain {k}" for k in range(9)])
        self.assertFalse(mentions("HealthCity Paris Saint Germain", "club Paris Saint-Germain, contact", counts))
        self.assertTrue(mentions("HealthCity Paris Saint Germain", "HealthCity Saint-Germain, manager", counts))





class Providers(unittest.TestCase):
    def test_searches_carry_the_projects_country_language_and_goal(self):
        pdir = ws.project_dir("t30")
        (pdir / "brief.json").write_text(json.dumps({"goal": "grass pitches", "country": "FR", "language": "fr"}),
                                         encoding="utf-8")
        sent = {}

        class R:
            status_code = 200

            def json(self):
                return {"results": [{"title": "t", "url": "https://x.fr/", "snippet": "s", "summary": "long"}]}

        def get(url, params=None, headers=None, timeout=None):
            sent["tinyfish"] = params
            return R()

        def post(url, json=None, headers=None, timeout=None):
            sent["deepseek"] = json
            return R()
        with mock.patch("mo_config.key", return_value="k"), mock.patch("mo_budget.allowed", return_value=(True, "")), \
                mock.patch.object(mo_providers.httpx, "get", side_effect=get), \
                mock.patch.object(mo_providers.httpx, "post", side_effect=post):
            mo_providers.search("stade municipal", ["tinyfish", "deepseek"], project_dir=pdir)
        self.assertEqual(("FR", "fr", "grass pitches"),
                         (sent["tinyfish"]["location"], sent["tinyfish"]["language"], sent["tinyfish"]["purpose"]))
        self.assertTrue(sent["deepseek"]["query"].endswith("lang:fr"))
        self.assertEqual("grass pitches", sent["deepseek"]["ori_question"])

    def test_a_reported_rate_limit_is_adopted(self):
        rate = mo_providers._Rate()

        class R:
            def json(self):
                return {"error": {"code": "RATE_LIMIT_EXCEEDED", "details": {"limit": 60, "window_seconds": 60}}}
        rate.learn("tinyfish:fetch", R())
        self.assertEqual(60, rate._update("tinyfish:fetch", lambda st: st.get("per_min")))

    def test_two_processes_share_one_pace(self):
        first, second = mo_providers._Rate(), mo_providers._Rate()   # separate memory, like two processes
        first._update("tinyfish:search", lambda st: st.update(per_min=60.0, next_at=0.0))
        slept = []
        with mock.patch("time.sleep", side_effect=slept.append):
            first.wait("tinyfish:search")
            second.wait("tinyfish:search")
        self.assertEqual(1, len(slept))            # the second caller waits for the first one's slot
        self.assertAlmostEqual(1.0, slept[0], delta=0.2)

    def test_transient_errors_are_tried_once_more(self):
        calls = []

        def flaky():
            calls.append(1)
            if len(calls) == 1:
                raise mo_providers.ProviderError("HTTP 429")
            return "ok"
        with mock.patch("time.sleep"):
            self.assertEqual("ok", mo_providers._again(flaky))
        self.assertEqual(2, len(calls))


class Package(unittest.TestCase):
    def test_tools_and_manifest(self):
        self.assertEqual(["project", "sources", "harvest", "search", "pool", "fetch", "browse", "judge", "grade", "run", "tasks", "orgs", "export"],
                         [f.__name__ for f in server.TOOLS])
        manifest = json.loads((ROOT / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["version"], server.VERSION)
        mcp = json.loads((ROOT / ".mcp.json").read_text(encoding="utf-8"))
        self.assertIn("run.sh", mcp["mcpServers"]["marketops"]["args"][0])
        for skill in (ROOT / "skills").glob("*/SKILL.md"):
            self.assertTrue(skill.read_text(encoding="utf-8").startswith("---\nname:"))

    def test_private_hosts_refused(self):
        for url in ("http://localhost/x", "http://127.0.0.1/", "file:///etc/passwd", "https://a b.ch/"):
            with self.assertRaises(mo_providers.ProviderError):
                mo_providers.public_url(url)


class SourceSaturation(unittest.TestCase):
    def test_one_source_is_never_saturated_and_three_repeating_sources_are(self):
        import mo_pool
        pdir = Path(TMP) / "projects" / "sat"
        pdir.mkdir(parents=True, exist_ok=True)
        ws.write_csv(pdir / "orgs.csv", ["id", "name"], [{"id": "o1", "name": "A"}])
        mo_pool.log_source(pdir, "list-1", "file", {"orgs_added": 900, "orgs_merged": 0})
        st = ws.source_saturation(pdir)
        self.assertFalse(st["saturated"])
        self.assertEqual(st["orgs_without_logged_source"], 1)      # an inherited list counts as unlogged
        self.assertTrue(any("not saturated" in x for x in server.needs_you(pdir, {})))
        for n, (added, merged) in enumerate(((3, 300), (1, 200), (40, 60)), 2):
            mo_pool.log_source(pdir, f"list-{n}", "file", {"orgs_added": added, "orgs_merged": merged})
        self.assertFalse(ws.source_saturation(pdir)["saturated"])   # the last one still brought 40% new
        for n in (5, 6, 7):
            mo_pool.log_source(pdir, f"list-{n}", "file", {"orgs_added": 2, "orgs_merged": 98})
        st = ws.stages(pdir)["sources"]
        self.assertTrue(st["saturated"], st)
        self.assertFalse(any("not saturated" in x for x in server.needs_you(pdir, {})))


LISTING = """<html><head><title>Clubs - Ligue</title></head><body>
<a href="/club/1">FC Alpha</a> <a href="/club/2">AS Beta</a> <a href="/clubs?page=2">2</a>
<a href="https://www.facebook.com/ligue">fb</a></body></html>"""
LISTING2 = """<html><body><a href="/club/3">US Gamma</a></body></html>"""


def club_page(n, name, site):
    return (f"<html><head><title>{name} | Ligue</title></head><body><h1>{name}</h1>"
            f"<p>Stade municipal, 1 rue du Stade</p><p>7500{n} Paris</p>"
            f"<p>Président Jean Test{n}</p><p>president{n}@{site}</p>"
            f"<a href='https://{site}/'>Site du club</a> <a href='https://sponsor-shop.test/'>Sponsor</a>"
            f"<a href='https://www.instagram.com/x'>insta</a></body></html>")


def fake_ligue(url, readers=None, project_dir=None, **kw):
    pages = {"https://ligue.test/clubs": LISTING, "https://ligue.test/clubs?page=2": LISTING2,
             "https://ligue.test/club/1": club_page(1, "FC Alpha", "fcalpha.test"),
             "https://ligue.test/club/2": club_page(2, "AS Beta", "asbeta-paris.test"),
             "https://ligue.test/club/3": club_page(3, "US Gamma", "usgamma.test")}
    return {"crawl4ai": {"final_url": url, "html": pages.get(url, "<p>nothing</p>"), "text": ""}}


class SuperPipeline(unittest.TestCase):
    def test_harvest_mines_a_source_into_rows_and_pooling_binds_their_addresses(self):
        import mo_flow
        import mo_tasks
        pdir = ws.project_dir("flow")
        with mock.patch.object(mo_providers, "read_race", side_effect=fake_ligue), \
                mock.patch.object(mo_providers, "read", side_effect=fake_ligue):
            # a finder registers the list; a miner claims it from its own lane
            server.sources("flow", add=[{"source": "ligue", "url": "https://ligue.test/clubs", "kind": "federation",
                                          "row_is": "club"}], agent="finder-1")
            got = mo_tasks.claim(pdir, "miner-1", "source")
            self.assertEqual("ligue", got["items"][0]["source"])
            self.assertEqual("mining", mo_flow.source_pool(pdir)[0]["status"])
            out = server.harvest("flow", "ligue", start="https://ligue.test/clubs", follow=r"/club/\d+",
                                 next=r"page=\d+", agent="miner-1")
            self.assertEqual(0, out["still_queued"], out)
            self.assertEqual(3, out["rows_total"])
            rows = {r["name"]: r for r in ws.read_csv(pdir / out["file"])}
            self.assertEqual("https://asbeta-paris.test", rows["AS Beta"]["website"])   # no Jev: the candidate with the name
            self.assertIn("sponsor-shop.test", rows["AS Beta"]["website_candidates"])
            self.assertNotIn("instagram", rows["AS Beta"]["website_candidates"])
            self.assertEqual("75002", rows["AS Beta"]["postcode"])
            self.assertIn("president2@asbeta-paris.test", rows["AS Beta"]["emails"])
            self.assertTrue(any("never pooled" in x for x in server.needs_you(pdir, server.project("flow"))))
            res = server.pool("flow", out["file"], into="orgs", source="ligue",
                              map={"name": "name", "website": "website", "city": "city", "page_url": "page_url"})
            self.assertEqual(3, res["orgs_added"])
            self.assertGreaterEqual(res["contacts_from_saved_pages"], 3)   # read from the saved copies
        self.assertEqual("pooled", mo_flow.source_pool(pdir)[0]["status"])
        values = {c["value"] for c in ws.read_contacts(pdir)}
        self.assertIn("president1@fcalpha.test", values)
        fl = mo_flow.flow(pdir)
        self.assertTrue(fl["reconcile"]["adds_up"], fl["reconcile"])
        self.assertEqual([], fl["pools"]["pages"]["harvest_files_not_pooled"])
        self.assertEqual(1, fl["pools"]["sources"]["pooled"])
        # the research lane refills itself from the new organisations: nobody waits for a plan
        sweep = mo_tasks.claim(pdir, "researcher-1", "sweep")
        self.assertEqual(3, len(sweep["items"]))

    def test_harvest_lets_jev_pick_the_site_among_the_links_and_steps_down_without_it(self):
        import types
        import mo_flow
        import mo_grade
        pdir = ws.project_dir("flow3")

        def fake_jev(state, qs):
            ans = {}
            for k, page in state["pages"].items():
                ans[f"{k}_kind"] = types.SimpleNamespace(choice="club_or_association")
                ans[f"{k}_own"] = types.SimpleNamespace(noul=0.9)
                ans[f"{k}_site"] = types.SimpleNamespace(choice=[c for c in page["links_off_the_site"]
                                                                 if "sponsor" not in c][0])
            return types.SimpleNamespace(answers=ans)
        with mock.patch.object(mo_providers, "read_race", side_effect=fake_ligue), \
                mock.patch.object(mo_providers, "read", side_effect=fake_ligue), \
                mock.patch.dict(os.environ, {"TYPESAFE_API_KEY": "test-key"}), \
                mock.patch.object(mo_grade, "_jev", side_effect=fake_jev):
            out = mo_flow.harvest(pdir, "ligue", urls=["https://ligue.test/club/1", "https://ligue.test/club/3"])
        self.assertEqual(2, out["judged_by_jev"], out)
        rows = {r["name"]: r for r in ws.read_csv(pdir / out["file"])}
        self.assertEqual("https://usgamma.test", rows["US Gamma"]["website"])
        self.assertEqual("club_or_association", rows["US Gamma"]["kind"])
        # a page Jev reads as a list of several entries gets no website, whatever it picked
        listing = [{"title": "Clubs", "lines": "FC A, AS B", "_cands": ["https://a.test", "https://b.test"], "name": "x"}]

        def list_jev(state, qs):
            return types.SimpleNamespace(answers={"p1_kind": types.SimpleNamespace(choice="list_or_other"),
                                                  "p1_own": types.SimpleNamespace(noul=0.8),
                                                  "p1_site": types.SimpleNamespace(choice="https://a.test")})
        with mock.patch.dict(os.environ, {"TYPESAFE_API_KEY": "test-key"}), \
                mock.patch.object(mo_grade, "_jev", side_effect=list_jev):
            mo_flow._jev_rows(pdir, listing)
        self.assertFalse(listing[0].get("website"))

    def test_keyless_readers_are_always_allowed(self):
        # the minimal level: no keys, no browser - plain HTTP must still read (0.7.1 crashed here)
        import mo_budget
        import mo_config
        self.assertEqual([], mo_config.slots("http"))
        self.assertEqual((True, ""), mo_budget.allowed("http", "fetch"))
        self.assertEqual((True, ""), mo_budget.allowed("download", "fetch"))

    def test_a_jev_key_without_the_sdk_steps_down_instead_of_failing(self):
        import mo_budget
        import mo_flow
        rows = [{"title": "Club", "lines": "x", "_cands": ["https://a.test"], "name": "Club"}]
        with mock.patch.dict(os.environ, {"TYPESAFE_API_KEY": "test-key"}), mock.patch.object(mo_budget, "_SDK", [False]):
            self.assertFalse(mo_budget.jev_ready())
            self.assertEqual(0, mo_flow._jev_rows(ws.project_dir("nosdk"), rows))
            self.assertEqual("rules + agents", mo_providers.capabilities()["grade"])

    def test_jev_orders_dig_leads_when_ready(self):
        import types
        import mo_budget
        import mo_grade
        hits = [{"title": "News", "url": "https://c.test/news"}, {"title": "Comité", "url": "https://c.test/comite"}]

        def fake_jev(state, qs):
            return types.SimpleNamespace(answers={"r1": types.SimpleNamespace(choice="unlikely", confidence=0.9),
                                                  "r2": types.SimpleNamespace(choice="likely", confidence=0.9)})
        with mock.patch.object(mo_budget, "jev_ready", return_value=True), \
                mock.patch.object(mo_grade, "_jev", side_effect=fake_jev):
            ranked = mo_run._rank(ws.project_dir("rank"), {"name": "Club"}, hits)
        self.assertEqual("https://c.test/comite", ranked[0]["url"])

    def test_pages_a_dead_harvest_left_reading_come_back(self):
        import mo_flow
        pdir = ws.project_dir("lease")
        mo_flow.queue_pages(pdir, [{"url": "https://l.test/1", "source": "s"}, {"url": "https://l.test/2", "source": "s"}])
        self.assertEqual(2, len(mo_flow._take(pdir, "s", 5)))      # claimed, then the process dies
        self.assertEqual([], mo_flow._take(pdir, "s", 5))           # still leased
        rows = ws.read_csv(pdir / "frontier.csv")
        for r in rows:
            r["time"] = "2000-01-01T00:00:00+00:00"
        ws.write_csv(pdir / "frontier.csv", mo_flow.FRONTIER_COLS, rows)
        self.assertEqual(2, len(mo_flow._take(pdir, "s", 5)))      # the lease ran out: back in the queue

    def test_found_sources_without_a_miner_are_flagged(self):
        pdir = ws.project_dir("flow2")
        server.sources("flow2", add=[{"source": "census", "url": "https://data.test/x.csv"}], agent="f")
        server.sources("flow2", add=[{"url": "https://data.test/x.csv"}], agent="g")   # same list, second finder
        st = server.sources("flow2")
        self.assertEqual(1, len(st["sources"]))
        self.assertIn("g", st["sources"][0]["found_by"])
        self.assertTrue(any("nobody mines them" in x for x in server.needs_you(pdir, server.project("flow2"))))


class Ledgers(unittest.TestCase):
    def test_two_ledgers_are_counted_apart(self):
        rows = [{"org_id": "o1", "grade": "A", "person": "P One"}, {"org_id": "o1", "grade": "B"},
                {"org_id": "o2", "grade": "A"}, {"org_id": "o2", "grade": "D"},
                {"org_id": "o3", "grade": "C"}, {"org_id": "o3", "grade": "B", "review": "yes"},
                {"org_id": "o4", "grade": "X"}]
        ab = mo_export.ledgers(rows)
        self.assertEqual((5, 3, "2 / 1 / 1 / 1"), (ab["valid_routes"], ab["valid_orgs"], ab["valid_by_grade"]))
        self.assertEqual((3, 2), (ab["strict_routes"], ab["strict_orgs"]))
        named = mo_export.ledgers(rows, "named_A")
        self.assertEqual((1, 1), (named["strict_routes"], named["strict_orgs"]))
        self.assertIn("admissions", __import__("mo_grade").DEFAULT_GRADES["X"])


class OfficialUsage(unittest.TestCase):
    """The six providers used the way their official skills and docs ask."""

    class Resp:
        def __init__(self, data, status=200):
            self._d, self.status_code, self.text, self.headers = data, status, json.dumps(data), {}

        def json(self):
            return self._d

    def test_serpapi_maps_pages_and_centres(self):
        seen = []

        def get(url, params=None, **kw):
            seen.append(dict(params))
            return self.Resp({"local_results": [{"title": f"P{params.get('start', 0)}", "website": ""}]})
        with mock.patch.dict(os.environ, {"SERPAPI_API_KEY": "k" * 12}), mock.patch("httpx.get", side_effect=get):
            mo_providers._serpapi("stade", "fr", "fr", 10, True, page=3, near="48.85,2.35,14z")
        self.assertEqual(40, seen[0]["start"])
        self.assertEqual("@48.85,2.35,14z", seen[0]["ll"])
        self.assertEqual("google_maps", seen[0]["engine"])

    def test_tinyfish_reads_ten_pages_in_one_request_and_harvest_reuses_them(self):
        import mo_flow
        posts = []

        def post(url, json=None, **kw):
            posts.append(json)
            return self.Resp({"results": [{"url": u, "final_url": u, "text": fake_ligue(u)["crawl4ai"]["html"]}
                                          for u in json["urls"]]})
        pdir = ws.project_dir("prefetch")
        urls = [f"https://ligue.test/club/{n}" for n in (1, 2, 3)]
        with mock.patch.dict(os.environ, {"TINYFISH_API_KEY": "k" * 12}), mock.patch("httpx.post", side_effect=post), \
                mock.patch.object(mo_providers, "read_race", side_effect=AssertionError("no per-page read")), \
                mock.patch.object(mo_providers, "read", side_effect=AssertionError("no per-page read")), \
                mock.patch("mo_budget.allowed", return_value=(True, "")):
            out = mo_flow.harvest(pdir, "ligue-batch", urls=urls)
        self.assertEqual(1, len(posts))
        self.assertEqual(3, len(posts[0]["urls"]))
        self.assertEqual(3, out["rows_new"], out)

    def test_facebook_page_read_through_serpapi(self):
        def get(url, params=None, **kw):
            self.assertEqual(("facebook_profile", "fcexample"), (params["engine"], params["profile_id"]))
            return self.Resp({"profile_results": {"name": "FC Example", "email": "contact@fcexample.test",
                                                  "phone": "+33 1 00 00 00 00", "website": "fcexample.test"}})
        with mock.patch.dict(os.environ, {"SERPAPI_API_KEY": "k" * 12}), mock.patch("httpx.get", side_effect=get):
            res = mo_providers._read_facebook("https://www.facebook.com/fcexample/")
        self.assertIn("email: contact@fcexample.test", res["text"])
        with self.assertRaises(mo_providers.ProviderError):
            mo_providers._read_facebook("https://www.facebook.com/groups/123")

    def test_agent_runs_capped_with_schema_and_local_exit(self):
        sent = []

        def post(url, json=None, **kw):
            sent.append((url, json))
            return self.Resp({"run_id": "run-1", "error": None})
        with mock.patch.dict(os.environ, {"TINYFISH_API_KEY": "k" * 12}), mock.patch("httpx.post", side_effect=post), \
                mock.patch("mo_budget.allowed", return_value=(True, "")):
            self.assertEqual("run-1", mo_providers.agent_submit("https://club.test/team", "goal", country="fr"))
            mo_providers.agent_submit("https://club.test/team", "goal", country="ch", stealth=True)
        url, body = sent[0]
        self.assertTrue(url.endswith("/v1/automation/run-async"))
        self.assertEqual({"enabled": True, "country_code": "FR"}, body["proxy_config"])
        self.assertEqual(40, body["agent_config"]["max_steps"])
        self.assertIn("contacts", body["output_schema"]["properties"])
        self.assertNotIn("proxy_config", sent[1][1])          # Switzerland is not a TinyFish exit country
        self.assertEqual("stealth", sent[1][1]["browser_profile"])


if __name__ == "__main__":
    unittest.main()
