"""The fixes from the Paris 11e run on 0.7.9 (paris11-079, 0.8.0).

Nothing stopped the team (078 ran three hours), workers ended their turn after one package and
nothing showed it; an idle researcher waited instead of looking for more lists; a page frame
was cached and hid the real page; "secretariat@club.com.Pour" was graded; a village directory
became three clubs' website; a register paused at 1.7% A/B kept the pool "growing"; a sports
office's inbox in every fiche footer went to every club; the archive passed 64 service numbers
and an address glued to the next word.

Run: python -m unittest tests.test_080_fixes -v
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "tools"))
if "MARKETOPS_PROJECTS" not in os.environ:   # alone; under discover test_core set these already
    TMP = tempfile.mkdtemp()
    os.environ.update(MARKETOPS_HOME=TMP + "/home", MARKETOPS_PROJECTS=TMP + "/projects",
                      MARKETOPS_HEALTH="0", MARKETOPS_KNOWLEDGE="0")
for k in ("SERPER_API_KEY", "SERPAPI_API_KEY", "DEEPSEEK_API_KEY", "TINYFISH_API_KEY", "TYPESAFE_API_KEY"):
    os.environ.pop(k, None)

import archive_run  # noqa: E402
import mo_pages  # noqa: E402
import mo_providers  # noqa: E402
import mo_run  # noqa: E402
import mo_tasks  # noqa: E402
import mo_workspace as ws  # noqa: E402
import server  # noqa: E402

ws._dns_ok[:] = [False]
BRIEF = {"goal": "g", "country": "FR", "language": "fr", "region": "Paris 11e"}


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


class TeamStop(Base):
    def test_the_lead_stops_the_team_and_every_claim_says_stop(self):
        p, pdir = "s080", ws.project_dir("s080")
        server.project(p, brief=BRIEF)
        self.org(p, "Club Arret")
        out = server.tasks(p, action="stop", note="enough for the report")
        self.assertTrue(out["stopped"])
        got = server.tasks(p, action="claim", kind="sweep", agent="r1")
        self.assertTrue(got["stop"])
        self.assertIn("enough for the report", got["why_empty"])
        server.tasks(p, action="resume")
        self.assertEqual("", ws.team_stop(pdir))

    def test_the_briefs_minutes_stop_the_team_when_they_are_up(self):
        p, pdir = "m080", ws.project_dir("m080")
        server.project(p, brief={**BRIEF, "minutes": 35})
        self.assertGreater(server.project(p)["time_left_min"], 34)
        b = ws.brief(pdir)
        b["started"] = (datetime.now(timezone.utc) - timedelta(minutes=36)).isoformat()
        (pdir / "brief.json").write_text(json.dumps(b), encoding="utf-8")
        self.assertIn("35 minutes are up", ws.team_stop(pdir))
        self.assertTrue(server.tasks(p, action="claim", kind="sweep", agent="r1")["stop"])


class KeepRunning(Base):
    def test_done_says_claim_the_next_package(self):
        p = "d080"
        server.project(p, brief=BRIEF)
        self.org(p, "Club Suivant")
        got = server.tasks(p, action="claim", kind="sweep", agent="r1")
        out = server.tasks(p, action="done", task_id=got["task"]["task_id"], note="ok")
        self.assertIn("claim your next package", out["next"])

    def test_a_worker_silent_while_packages_are_open_is_named_to_the_lead(self):
        p, pdir = "a080", ws.project_dir("a080")
        server.project(p, brief=BRIEF)
        self.org(p, "Club Ouvert")
        mo_tasks.plan(pdir, "sweep", 5)
        old = (datetime.now() - timedelta(minutes=8)).isoformat(timespec="seconds")
        (pdir / "calls.jsonl").write_text(json.dumps({"agent": "r2", "tool": "tasks", "time": old}) + "\n",
                                          encoding="utf-8")
        self.assertGreaterEqual(server.agents_seen(pdir)["r2"]["silent_s"], 400)
        items = " ".join(server.project(p).get("needs_you") or [])
        self.assertIn("r2", items)

    @mock.patch.object(mo_tasks, "outlook", return_value={"lane_finished": True, "other_lanes": {},
                                                          "why_empty": "every organisation is through"})
    def test_an_idle_researcher_is_handed_a_find_at_most_once_a_quarter_hour(self, _):
        p = "f080"
        server.project(p, brief=BRIEF)
        got = server.tasks(p, action="claim", kind="sweep", agent="r1")
        self.assertEqual("find", got["task"]["kind"])
        self.assertTrue(got["task"]["title"].startswith(mo_tasks.AUTO_FIND_TITLE))
        server.tasks(p, action="done", task_id=got["task"]["task_id"], note="nothing new")
        again = server.tasks(p, action="claim", kind="sweep", agent="r1")
        self.assertIsNone(again.get("task"))   # the search that found nothing is not repeated at once


class Pages(unittest.TestCase):
    def test_a_page_frame_is_not_cached(self):
        self.assertTrue(ws.is_shell({"lines": ["Chargement"]}, 5000))
        self.assertFalse(ws.is_shell({"lines": ["x" * 400]}, 5000))
        self.assertFalse(ws.is_shell({"lines": ["court"]}, 800))

    def test_the_next_sentence_is_cut_off_an_address(self):
        self.assertEqual("secretariat@club080.com", mo_pages._trim_tail("secretariat@club080.com.Pour"))
        self.assertEqual("a@mairie.paris.fr", mo_pages._trim_tail("a@mairie.paris.fr"))
        got = [c["value"] for c in mo_pages.contacts(["Ecrire à secretariat@club080.com.Pour toute question"],
                                                       "https://x080.test/")]
        self.assertEqual(["secretariat@club080.com"], got)

    def test_only_a_short_plain_path_is_tried_on_every_site(self):
        self.assertTrue(ws.site_path_hint("/bureau"))
        self.assertTrue(ws.site_path_hint("/le-club/contact"))
        for bad in ("/FR-17-*.html", "https://x.fr/contact", "www.x.fr/contact",
                    "/annuaire/associations/sport/fiche-club-de-judo-du-onzieme-arrondissement-2024"):
            self.assertFalse(ws.site_path_hint(bad), bad)


class Websites(Base):
    def test_a_domain_another_club_has_and_a_one_word_title_are_not_its_website(self):
        p, pdir = "w080", ws.project_dir("w080")
        server.project(p, brief=BRIEF)
        self.org(p, "Tennis Club Village", website="https://village080.fr/")
        hits = {"results": [{"url": "https://village080.fr/", "title": "Judo Club Village", "snippet": "Paris"}]}
        with mock.patch.object(mo_providers, "search", return_value=hits):
            self.assertEqual("", mo_run._find_site(pdir, {"id": "o9", "name": "Judo Club Village"}))
        hits = {"results": [{"url": "https://sigles080.fr/", "title": "SOLID", "snippet": "acronyme"}]}
        with mock.patch.object(mo_providers, "search", return_value=hits):
            self.assertEqual("", mo_run._find_site(pdir, {"id": "o8", "name": "SOLID"}))


class Saturation(Base):
    def test_a_source_the_pilot_paused_adds_nothing_to_growth(self):
        p, pdir = "g080", ws.project_dir("g080")
        server.project(p, brief=BRIEF)
        with mock.patch.object(mo_tasks, "source_yield", return_value={
                "register": {"state": "waiting", "worked": 20, "with_ab": 0, "orgs": 200}}):
            for n, src in enumerate(["a", "b", "register", "register2"]):
                ws.append_csv(pdir / "sources.csv", ["source", "orgs_added", "orgs_merged"],
                              {"source": src if src != "register2" else "register", "orgs_added": 1 if n < 2 else 90,
                               "orgs_merged": 20 if n < 2 else 0})
            self.assertTrue(all(x < ws.SATURATION_NEW for x in ws.source_saturation(pdir)["new_share_last"]))


class Footer(Base):
    def test_a_sites_inbox_on_every_fiche_is_held_but_one_shared_page_credits_both(self):
        p, pdir = "h080", ws.project_dir("h080")
        a, b = self.org(p, "Club Fiche A"), self.org(p, "Club Fiche B")

        def page(n):
            html = (f"<div><h1>Club Fiche {n}</h1><p>club.fiche{n.lower()}080@gmail.com</p>"
                    "<footer>Office des sports : contact@oms080.fr</footer></div>" + " " * 300)
            return lambda url, readers=None, project_dir=None: {"crawl4ai": {"final_url": url, "html": html, "text": ""}}
        with mock.patch.object(mo_providers, "read", side_effect=page("A")):
            ws.fetch(pdir, "https://oms080.fr/club/a", ["crawl4ai"], a, "Club Fiche A", "")
        with mock.patch.object(mo_providers, "read", side_effect=page("B")):
            ws.fetch(pdir, "https://oms080.fr/club/b", ["crawl4ai"], b, "Club Fiche B", "")
        mine = {r["value"] for r in ws.read_contacts(pdir) if r["org_id"] == b}
        self.assertIn("club.ficheb080@gmail.com", mine)
        self.assertNotIn("contact@oms080.fr", mine)


class Archive(unittest.TestCase):
    def test_service_numbers_escaped_and_glued_addresses_are_redacted(self):
        text, emails, phones = archive_run.redact(
            "N° vert 0 800 123 456, Infos 0 825 12 34 56, jean\\@club080\\.fr, a.b080@orange.frVICE PRESIDENT")
        self.assertEqual((2, 2), (emails, phones), text)
        self.assertEqual([], archive_run.left_over(text))
        self.assertIn("VICE PRESIDENT", text)

    def test_a_key_in_a_word_is_not_taken_for_a_phone(self):
        text, _, phones = archive_run.redact("api_key=abcdef0123456789abcdef")
        self.assertEqual(0, phones)
        self.assertTrue(archive_run.left_over(text))   # so the check refuses it

    def test_the_tool_refuses_what_it_could_not_redact(self):
        proj, repo = Path(tempfile.mkdtemp()), Path(tempfile.mkdtemp())
        (proj / "journal.jsonl").write_text('{"t": "password=hunter2hunter2"}\n', encoding="utf-8")
        bad = subprocess.run([sys.executable, str(ROOT / "tools" / "archive_run.py"), str(proj), str(repo), "r080"],
                             capture_output=True, text=True)
        self.assertEqual(1, bad.returncode)
        self.assertFalse((repo / "runs" / "r080").exists())


if __name__ == "__main__":
    unittest.main()
