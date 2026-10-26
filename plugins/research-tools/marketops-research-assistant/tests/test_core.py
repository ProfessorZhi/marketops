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
        self.assertEqual(50.0, out["ab_orgs_per_100"])
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
        self.assertEqual(["project", "search", "fetch", "crawl", "browse", "judge", "grade", "export"],
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
