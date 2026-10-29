"""The fixes from the Paris 11e run on 0.7.5 (paris11-075, 0.7.7).

A yearbook PDF's presidents, kept aside as a list's entries, could not be attached to their
clubs; PDFs took minutes through remote readers and "name@yahoo .fr" was not read; a filter bar
and a sidebar on 15 and 14 of 40 harvested rows passed the chrome bar; a paris.fr mailbox came
through as ROT13 ".se"; a list paused at 0 of 5 gave 9 A/B of 62 once scaled; duplicates on two
websites were paired and the list stopped at 40; the 对账 line counted chain stores; a worker
polled an empty lane for minutes.

Run: python -m unittest tests.test_077_fixes -v
"""
from __future__ import annotations

import codecs
import os
import sys
import tempfile
import time
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
import mo_flow  # noqa: E402
import mo_pages  # noqa: E402
import mo_providers  # noqa: E402
import mo_tasks  # noqa: E402
import mo_workspace as ws  # noqa: E402
import server  # noqa: E402

ws._dns_ok[:] = [False]

YEARBOOK = "https://oms077.test/annuaire-2026.pdf"
YEARBOOK_TEXT = "\n".join(
    f"{club}\nPrésident : {p}\n{mail}\n" for club, p, mail in [
        ("Escrime Alpha", "M. Jean ALPHA", "j.alpha077@gmail.com"), ("Judo Beta", "Mme Anne BETA", "anne.beta077@yahoo .fr"),
        ("Boxe Gamma", "M. Paul GAMMA", "p.gamma077@orange.fr"), ("Danse Delta", "Mme Lea DELTA", "lea.delta077@gmail.com"),
        ("Volley Epsilon", "M. Marc EPS", "marc.eps077@free.fr"), ("Tennis Zeta", "Mme Iris ZETA", "iris.zeta077@sfr.fr"),
        ("Rugby Eta", "M. Hugo ETA", "hugo.eta077@gmail.com")]) * 2


def no_remote(url, readers=None, project_dir=None, **kw):
    return {"crawl4ai": {"error": "HTTP 404"}}


class Base(unittest.TestCase):
    def setUp(self):
        self.patches = [mock.patch.object(mo_providers, "read", side_effect=no_remote),
                        mock.patch.object(mo_providers, "read_race", side_effect=no_remote),
                        mock.patch.object(mo_providers, "public_url", side_effect=lambda u: u),
                        mock.patch.object(mo_providers, "pdf_text",
                                          side_effect=lambda u: {"final_url": u, "text": YEARBOOK_TEXT,
                                                                 "bytes": b"%PDF-1.4 test"}),
                        mock.patch.object(mo_providers, "search", return_value={"results": [], "routes": {}})]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in reversed(self.patches):
            p.stop()

    def org(self, project: str, name: str, **extra) -> str:
        server.orgs(project, add=[{"name": name, **extra}], source="test-list")
        return next(o["id"] for o in ws.read_csv(ws.project_dir(project) / "orgs.csv")
                    if o["name"] == name and not o.get("merged_into"))


class Yearbook(Base):
    def test_a_pdf_is_read_here_kept_and_its_entries_attached_by_the_agent(self):
        p, pdir = "y077", ws.project_dir("y077")
        beta = self.org(p, "Judo Beta")
        other = self.org(p, "Escrime Alpha")
        got = server.fetch(p, YEARBOOK, org_id=other)
        self.assertTrue(Path(got["pdf_file"]).exists())                 # the file, for an agent that reads PDFs
        self.assertIn("pdf", got["readers"])
        self.assertIn("anne.beta077@yahoo.fr", {c["value"] for c in got["contacts"]})   # the space is closed
        self.assertNotIn("anne.beta077@yahoo.fr", {c["value"] for c in ws.contacts_of(pdir, beta)})
        out = server.grade(p, set=[{"value": "anne.beta077@yahoo.fr", "org_id": beta, "grade": "A",
                                    "person": "Anne BETA", "role": "Présidente"}])
        self.assertEqual(["anne.beta077@yahoo.fr"], out["attached"])
        row = ws.contacts_of(pdir, beta)[0]
        self.assertEqual(("A", YEARBOOK), (row["grade"], row["page_url"]))
        nowhere = server.grade(p, set=[{"value": "made.up077@gmail.com", "org_id": beta, "grade": "A"}])
        self.assertEqual(["made.up077@gmail.com"], nowhere["not_on_a_page"])   # never from nowhere


class Addresses(unittest.TestCase):
    def test_rot13_is_decoded_toward_the_pages_own_ending(self):
        hidden = codecs.encode("sport-asso@paris.fr", "rot13")
        got = mo_pages.contacts([f"Ecrire : {hidden} ou club.x077@gmail.com"], "https://www.paris.fr/activites/1")
        self.assertEqual(["sport-asso@paris.fr", "club.x077@gmail.com"], [c["value"] for c in got])
        self.assertEqual(["info@club077.se"], [c["value"] for c in mo_pages.contacts(["info@club077.se"],
                                                                                         "https://club077.se/")])


class HarvestRepeats(unittest.TestCase):
    def test_a_name_on_three_entries_is_chrome_even_under_half(self):
        rows = [{"name": f"Club {n}", "_names": [f"Club {n}"], "emails": ""} for n in range(20)]
        for r in rows[:4]:
            r["name"], r["_names"] = "Tous les sports", ["Tous les sports", "Amicale Sidebar", f"Real {r['name']}"]
        for r in rows[4:8]:
            r["name"], r["_names"] = "Amicale Sidebar", ["Amicale Sidebar", f"Real {r['name']}"]
        note = mo_flow._drop_chrome(rows)
        self.assertEqual({f"Real Club {n}" for n in range(8)}, {r["name"] for r in rows[:8]})
        self.assertIn("tous les sports", note["chrome_removed"]["names"])


class Lanes(Base):
    def test_an_empty_lane_ends_when_nothing_moves(self):
        p, pdir = "q077", ws.project_dir("q077")
        server.project(p, brief={"goal": "g", "country": "FR", "language": "fr"})
        server.sources(p, add=[{"source": "stuck list", "url": "https://stuck077.test/"}])
        server.tasks(p, action="claim", kind="source", agent="m")   # a miner on it: organisations are coming
        self.assertTrue(server.tasks(p, action="claim", kind="sweep", agent="r")["keep_waiting"])
        old = time.time() - mo_tasks.QUIET_S - 5
        for f in pdir.iterdir():
            os.utime(f, (old, old))
        got = server.tasks(p, action="claim", kind="sweep", agent="r")
        self.assertTrue(got["lane_finished"])
        self.assertIn("nothing has moved", got["why_empty"])


class Duplicates(Base):
    def test_two_websites_on_two_domains_are_two_organisations(self):
        p, pdir = "d077", ws.project_dir("d077")
        self.org(p, "Paris Vo Thuat Club", website="https://vothuat077.paris/")
        self.org(p, "AS Vo Thuat", website="https://vothuat077.com/")
        self.org(p, "Viet Vo Dao Paris", website="https://vvd077.fr/")
        self.org(p, "VIET VO DAO PARIS CLUB", city="Paris")
        found = [set(d["names"]) for d in mo_tasks.possible_duplicates(pdir)]
        self.assertEqual([{"Viet Vo Dao Paris", "VIET VO DAO PARIS CLUB"}], found)


class Reconcile(unittest.TestCase):
    def test_stores_under_a_chain_are_in_neither_side(self):
        rows = [{"最佳等级": "A 关键岗位"}, {"最佳等级": "D 通用入口"}, {"最佳等级": "未找到", "抓取状态": "官网已读"}]
        self.assertIn("对得上", mo_export._reconcile(rows, 3))
        stores = rows + [{"最佳等级": "D 通用入口", "part_of": "o1"}]
        self.assertIn("对得上", mo_export._reconcile([r for r in stores if ws.counted(r)], 3))


if __name__ == "__main__":
    unittest.main()
