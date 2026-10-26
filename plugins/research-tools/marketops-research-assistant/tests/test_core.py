"""Offline checks of the whole flow: page -> contacts -> grades -> Excel. No network."""
import json
import os
import sys
import tempfile
import time
import unittest
import zipfile
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
TMP = tempfile.mkdtemp()
os.environ.update(MARKETOPS_HOME=TMP + "/home", MARKETOPS_PROJECTS=TMP + "/projects")
for k in ("SERPER_API_KEY", "SERPAPI_API_KEY", "DEEPSEEK_API_KEY", "TINYFISH_API_KEY", "TYPESAFE_API_KEY"):
    os.environ.pop(k, None)

import mo_pages  # noqa: E402
import mo_providers  # noqa: E402
import mo_run  # noqa: E402
import mo_workspace as ws  # noqa: E402
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
        rows = ws.read_csv(ws.project_dir("t1") / "contacts.csv")
        self.assertEqual(1, sum(r["value"] == "praesident@fc-beispiel.ch" for r in rows))
        self.assertEqual("D", next(r for r in rows if r["value"] == "info@fc-beispiel.ch")["grade"])
        self.assertEqual(2, len(list((ws.project_dir("t1") / "pages").iterdir())))

    def test_crawl_grade_export_end_to_end(self):
        pdir = ws.project_dir("t2")
        (pdir / "orgs.csv").write_text("id,name,website\no1,FC Beispiel,fc-beispiel.ch\no2,No Site,\n", encoding="utf-8")
        job = server.crawl("t2")
        self.assertEqual(2, job["queued"])
        for _ in range(100):
            if not server.project("t2")["crawl"]["jobs"][-1]["running"]:
                break
            time.sleep(0.05)
        crawl = {r["org_id"]: r for r in ws.read_csv(pdir / "crawl.csv")}
        self.assertEqual("done", crawl["o1"]["status"])
        self.assertEqual("no_website", crawl["o2"]["status"])
        self.assertEqual(0, server.crawl("t2")["queued"])  # resumes, never repeats
        values = {r["value"] for r in ws.read_csv(pdir / "contacts.csv")}
        self.assertIn("sekretariat@fc-beispiel.ch", values)  # found on the linked Vorstand page
        self.assertIn("no Jev key", server.grade("t2")["note"])
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
        self.assertEqual(1, read.call_count)
        self.assertTrue(all(v.get("cached") for v in again["readers"].values()))

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
        with mock.patch.object(mo_providers, "read", side_effect=read), \
                mock.patch.object(mo_providers, "public_url", side_effect=lambda u: u), \
                mock.patch.object(mo_providers, "search", return_value=hits), \
                mock.patch("mo_grade.auto", side_effect=lambda p, **k: server.grade(
                    "t5", set=[{"value": "praesident@fc-beispiel.ch", "grade": "A"},
                               {"value": "platz@sc-leer.ch", "grade": "B"}]) if (p / "contacts.csv").exists() else {}):
            state = mo_run.run_sync(pdir, rounds=2, workers=2)
        values = {r["value"] for r in ws.read_csv(pdir / "contacts.csv")}
        self.assertIn("platz@sc-leer.ch", values)  # found only by the deep round
        self.assertTrue(state["done"])
        self.assertEqual(100.0, state["result"]["ab_org_share"])
        self.assertIn("every organisation", state["stopped_because"])
        self.assertTrue(Path(state["export"]["file"]).exists())

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

    def test_jobs_stay_in_their_project(self):
        ws.new_job(ws.project_dir("t6"), "crawl")
        self.assertEqual([], server.project("t7")["crawl"]["jobs"])


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


class Package(unittest.TestCase):
    def test_tools_and_manifest(self):
        self.assertEqual(["project", "search", "pool", "fetch", "crawl", "browse", "judge", "grade", "run", "tasks",
                          "export"],
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


if __name__ == "__main__":
    unittest.main()
