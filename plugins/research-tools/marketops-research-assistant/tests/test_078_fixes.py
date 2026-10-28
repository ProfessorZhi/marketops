"""The fixes from the Paris 11e run on 0.7.7 (paris11-077, 0.7.8).

A researcher made 14 empty sweep claims while 50 read organisations waited in a dig lane nobody
had planned; three tries on one unreachable PDF cost 367 s; 1,778 of 1,893 "phones" were record
numbers; a dojo's 13 instructor mailboxes could not be cleared at once; 31 duplicate groups were
left after a merge; a miner's own CSV could not be pooled; the richest source read "dropped".

Run: python -m unittest tests.test_078_fixes -v
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

import httpx  # noqa: E402

import mo_pages  # noqa: E402
import mo_providers  # noqa: E402
import mo_tasks  # noqa: E402
import mo_workspace as ws  # noqa: E402
import server  # noqa: E402

ws._dns_ok[:] = [False]


class Base(unittest.TestCase):
    def setUp(self):
        self.patches = [mock.patch.object(mo_providers, "public_url", side_effect=lambda u: u),
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


class ResearchLanes(Base):
    def test_an_empty_sweep_hands_out_dig(self):
        p, pdir = "r078", ws.project_dir("r078")
        ids = [self.org(p, f"Club Lu {c}") for c in "ABC"]
        for i in ids:   # read by a whole-project run: out of sweep, waiting for an agent in dig
            ws.append_csv(pdir / "crawl.csv", ["org_id", "pass", "status"], {"org_id": i, "pass": "final", "status": "done"})
        got = server.tasks(p, action="claim", kind="sweep", agent="r1")
        self.assertEqual("dig", got["task"]["kind"])
        self.assertEqual(set(ids), set(got["task"]["items"].split("|")))


class DeadHosts(unittest.TestCase):
    def test_a_host_that_does_not_connect_fails_fast_and_is_not_asked_again(self):
        mo_providers._DEAD_HOSTS.clear()
        calls = []

        def refuse(*a, **k):
            calls.append(k.get("timeout"))
            raise httpx.ConnectTimeout("timed out")
        with mock.patch.object(httpx, "stream", side_effect=refuse):
            with self.assertRaises(mo_providers.ProviderError):
                mo_providers.download("https://dead078.test/a.pdf")
            with self.assertRaises(mo_providers.ProviderError) as again:
                mo_providers.download("https://dead078.test/b.pdf")
        self.assertEqual(1, len(calls))   # the second one never tried
        self.assertEqual(mo_providers.CONNECT_S, calls[0].connect)
        self.assertIn("did not answer", str(again.exception))
        mo_providers._DEAD_HOSTS.clear()


class Phones(Base):
    def test_record_numbers_are_not_phones_and_one_page_cannot_flood(self):
        found = [c["value"] for c in mo_pages.contacts(
            ["Tél 01 43 55 22 10 - SIRET 12345678901234 - SIREN 123456789 - +33 6 12 34 56 78"], "https://x.test/")
            if c["kind"] == "phone"]
        self.assertEqual(["01 43 55 22 10", "+33 6 12 34 56 78"], found)
        self.assertFalse(mo_pages.phone_fits("06 12 34 56 7", "FR"))
        self.assertTrue(mo_pages.phone_fits("06 12 34 56 7", "DE"))   # no plan: kept
        p, pdir = "ph078", ws.project_dir("ph078")
        server.project(p, brief={"goal": "g", "country": "FR", "language": "fr"})
        oid = self.org(p, "Club Annuaire")
        ws.merge_contacts(pdir, [{"kind": "phone", "value": f"01 43 55 {i // 100:02d} {i % 100:02d}", "quote": "q"}
                                 for i in range(60)], oid, "Club Annuaire", "t")
        self.assertEqual(ws.PHONES_PER_ORG, sum(r["org_id"] == oid for r in ws.read_csv(pdir / "phones.csv")))


class Bulk(Base):
    def test_one_call_grades_an_organisations_every_address(self):
        p, pdir = "b078", ws.project_dir("b078")
        oid = self.org(p, "Dojo Roster")
        ws.merge_contacts(pdir, [{"kind": "email", "value": f"coach{i}.b078@gmail.com", "quote": f"Coach {i}"}
                                 for i in range(13)], oid, "Dojo Roster", "t")
        server.grade(p, set=[{"value": "coach0.b078@gmail.com", "org_id": oid, "grade": "A", "person": "Coach Zero",
                              "role": "Président", "confirm": True}])
        server.grade(p, set=[{"org_id": oid, "all": "X"}])
        grades = {c["value"]: c["grade"] for c in ws.contacts_of(pdir, oid)}
        self.assertEqual("A", grades.pop("coach0.b078@gmail.com"))   # an agent's own grade stays
        self.assertEqual({"X"}, set(grades.values()))


class MergeAll(Base):
    def test_every_suggested_group_merges_in_one_call_except_those_kept_apart(self):
        p, pdir = "m078", ws.project_dir("m078")
        a = self.org(p, "UNION SPORTIVE PARIS XI (US PARIS XI)", city="Paris")
        self.org(p, "Union Sportive Paris 11")
        c = self.org(p, "MACCABI", city="Paris")
        self.org(p, "SPORTING CLUB MACCABI de PARIS (SCMP)")
        out = server.orgs(p, merge="suggested", keep_apart=[c])
        self.assertEqual(1, len(out["merged"]))
        self.assertEqual((0, [c]), (out["groups_left"], out["kept_apart"]))   # nothing left but the group kept apart
        live = [o for o in ws.read_csv(pdir / "orgs.csv") if not o.get("merged_into")]
        self.assertEqual(3, len(live))
        self.assertIn(a, {m["keep"] for m in out["merged"]} | {m["drop"] for m in out["merged"]})


class PoolOwnFile(Base):
    def test_a_workers_own_file_is_copied_in_and_pooled(self):
        p, pdir = "f078", ws.project_dir("f078")
        own = Path(tempfile.mkdtemp()) / "creneaux-078.csv"
        own.write_text("name;email\nClub Fichier A;a.f078@gmail.com\nClub Fichier B;b.f078@gmail.com\n", encoding="utf-8")
        out = server.pool(p, file=str(own), into="orgs", source="own list")
        self.assertEqual(2, out["orgs_added"])
        self.assertTrue((pdir / "pages" / "inbox" / own.name).exists())


class Sources(Base):
    def test_a_dropped_source_that_gave_organisations_reads_stopped(self):
        p, pdir = "s078", ws.project_dir("s078")
        server.sources(p, add=[{"source": "yearbook", "url": "https://y078.test/a.pdf"}])
        server.orgs(p, add=[{"name": "Club Annuaire Un"}, {"name": "Club Annuaire Deux"}], source="yearbook")
        server.sources(p, set=[{"source": "yearbook", "status": "dropped", "note": "site unreachable"}])
        self.assertEqual("stopped", mo_tasks.source_yield(pdir)["yearbook"]["state"])
        self.assertEqual(1, server.project(p)["flow"]["pools"]["sources"]["stopped"])


class Maps(unittest.TestCase):
    def test_serper_places_takes_a_centre(self):
        sent = {}

        class R:
            status_code = 200
            headers: dict = {}

            def json(self):
                return {"places": []}

        def post(url, json=None, headers=None, timeout=None):
            sent.update(url=url, body=json)
            return R()
        with mock.patch.object(httpx, "post", side_effect=post), mock.patch.object(mo_providers.mo_config, "key",
                                                                                   return_value="k"):
            mo_providers._serper("gymnase", "fr", "fr", 20, True, near="48.86,2.38,14z")
        self.assertTrue(sent["url"].endswith("/places"))
        self.assertEqual("@48.86,2.38,14z", sent["body"]["ll"])


if __name__ == "__main__":
    unittest.main()
