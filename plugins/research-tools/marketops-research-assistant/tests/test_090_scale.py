"""A team on a city-sized pool (paris-all on 0.8.4, 0.9.0).

Ten workers are threads of one server. On 2,000 organisations each of them recomputed the flow,
the duplicate groups and every contact file for itself: project() went from 1 s to 440 s, a write
waited 1,800 s behind them with no word, 1,562 addresses sat ungraded for 40 minutes, 1,844
organisations without a website read as "unread", and an empty dig plan said nothing.

Run: python -m unittest tests.test_090_scale -v
"""
from __future__ import annotations

import os
import sys
import tempfile
import threading
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

import mo_flow  # noqa: E402
import mo_oslock  # noqa: E402
import mo_providers  # noqa: E402
import mo_tasks  # noqa: E402
import mo_workspace as ws  # noqa: E402
import server  # noqa: E402

ws._dns_ok[:] = [False]
BRIEF = {"goal": "g", "country": "FR", "language": "fr"}


class Base(unittest.TestCase):
    def setUp(self):
        self.p = [mock.patch.object(mo_providers, "public_url", side_effect=lambda u: u),
                  mock.patch.object(mo_providers, "search", return_value={"results": [], "routes": {}})]
        for x in self.p:
            x.start()

    def tearDown(self):
        for x in reversed(self.p):
            x.stop()


class Shared(Base):
    def test_eleven_agents_asking_at_once_compute_once(self):
        p, pdir = "memo090", ws.project_dir("memo090")
        server.project(p, brief=BRIEF)
        server.orgs(p, add=[{"name": f"Club Partage {i}"} for i in range(30)], source="liste")
        calls = []
        real = mo_flow._flow

        def slow(d):
            calls.append(1)
            time.sleep(0.3)
            return real(d)
        with mock.patch.object(mo_flow, "_flow", side_effect=slow):
            ws._memo.clear()
            threads = [threading.Thread(target=mo_flow.flow, args=(pdir,)) for _ in range(11)]
            [t.start() for t in threads]
            [t.join() for t in threads]
        self.assertEqual(1, len(calls))

    def test_a_write_is_seen_by_the_next_question(self):
        p, pdir = "fresh090", ws.project_dir("fresh090")
        server.project(p, brief=BRIEF)
        server.orgs(p, add=[{"name": "Club Un"}], source="liste")
        before = mo_flow.flow(pdir)["pools"]["organisations"]["total"]
        server.orgs(p, add=[{"name": "Club Deux"}], source="liste")
        self.assertEqual(before + 1, mo_flow.flow(pdir)["pools"]["organisations"]["total"])

    def test_only_the_contact_files_that_changed_are_read_again(self):
        p, pdir = "inc090", ws.project_dir("inc090")
        server.project(p, brief=BRIEF)
        server.orgs(p, add=[{"name": f"Club Fichier {i}", "email": f"c{i}.inc090@gmail.com"} for i in range(20)],
                    source="liste")
        ws.read_contacts(pdir)
        oid = next(o["id"] for o in ws.read_csv(pdir / "orgs.csv"))
        ws.merge_contacts(pdir, [{"kind": "email", "value": "new.inc090@gmail.com", "quote": "q"}], oid, "Club", "t")
        real, seen = ws.read_csv, []
        with mock.patch.object(ws, "read_csv", side_effect=lambda f: seen.append(Path(f).name) or real(f)):
            rows = ws.read_contacts(pdir)
        self.assertIn("new.inc090@gmail.com", {r["value"] for r in rows})
        self.assertEqual(1, len([n for n in seen if n.endswith(".csv")]))


class Busy(unittest.TestCase):
    def test_a_write_held_too_long_answers_busy_instead_of_waiting(self):
        path = Path(tempfile.mkdtemp()) / "pools"
        held, done = threading.Event(), threading.Event()

        def hold():
            with ws._lock(path):
                held.set()
                done.wait(5)
        t = threading.Thread(target=hold)
        t.start()
        held.wait(5)
        with mock.patch.object(ws, "LOCK_WAIT_S", 0.2):
            t0 = time.time()
            with self.assertRaises(mo_oslock.Busy):
                with ws._lock(path):
                    pass
            self.assertLess(time.time() - t0, 2)
        done.set()
        t.join()


class Ledger(Base):
    def test_ungraded_addresses_are_the_leads_to_grade(self):
        p, pdir = "ungr090", ws.project_dir("ungr090")
        server.project(p, brief=BRIEF)
        server.orgs(p, add=[{"name": "Club Adresses"}], source="liste")
        oid = next(o["id"] for o in ws.read_csv(pdir / "orgs.csv"))
        with mock.patch.object(server, "UNGRADED_MAX", 3):
            ws.merge_contacts(pdir, [{"kind": "email", "value": f"p{i}.ungr090@gmail.com", "quote": "q"}
                                     for i in range(4)], oid, "Club Adresses", "t")
            items = " ".join(server.project(p).get("needs_you") or [])
        self.assertIn("addresses are not graded yet", items)

    def test_unread_with_a_website_is_apart_from_unread_without_one(self):
        p, pdir = "read090", ws.project_dir("read090")
        server.project(p, brief=BRIEF)
        server.orgs(p, add=[{"name": "Club Site", "website": "https://site090.test/"}, {"name": "Club Sans Site"}],
                    source="liste")
        fl = mo_flow.flow(pdir)
        org = fl["pools"]["organisations"]
        self.assertEqual((1, 1), (org["not_read_with_website"], org["not_read_no_website"]))
        self.assertEqual(1, fl["waiting_between_pools"]["organisations -> websites"])

    def test_an_empty_dig_plan_says_why(self):
        p, pdir = "dig090", ws.project_dir("dig090")
        server.project(p, brief=BRIEF)
        got = mo_tasks.plan(pdir, "dig")
        self.assertEqual(0, got["planned"])
        self.assertIn("sweep", got["why_empty"])


class Redaction(unittest.TestCase):
    """What the paris-all archive found left over, round after round."""

    def test_every_form_paris_all_found_is_redacted_and_the_check_finds_nothing(self):
        sys.path.insert(0, str(ROOT / "tools"))
        import archive_run
        for text in ("contact@ldb13.Com", "x@songlongparis20.Fr", "y@noos..fr", "g41@orange.frVICE PRESIDENT",
                     "Renseignements01 43 55 22 10", "tél.01 43 55 22 10", "Paris0143552210",
                     "Téléphone01 43 55 22 10Adresse", "01 43 55 22 10www.taic.fr", "0143552210mestr",
                     "jean.dupont@clu", "+33 1 43 55 22 10 99 88", "33143552210", "x@a.verylongtldexample",
                     "https://www.facebook.com/club.judo.paris/ et @judo_paris11"):
            out, _, _ = archive_run.redact(text)
            self.assertEqual([], archive_run.left_over(out), (text, out))
            self.assertNotRegex(out, r"\d{2}[ .]?\d{2}[ .]?\d{2}[ .]?\d{2}", text)
        for kept in ("run paris-all-084-20261029", "web/20230218063911"):
            self.assertEqual(kept, archive_run.redact(kept)[0])   # a run name and a date are not phones


if __name__ == "__main__":
    unittest.main()
