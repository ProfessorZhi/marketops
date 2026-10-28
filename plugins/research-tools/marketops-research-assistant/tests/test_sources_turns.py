"""Every source gets researched, and lists give what they print (0.7.3, from the Paris 072 run).

072 pooled 27 sources; the research lane handed out organisations in pool order, so four
researchers spent forty minutes on the first source and 26 were never read. Lists that printed
mailboxes (schools, clubs, timetables) lost them at pooling. These tests hold the fixes and
simulate the chain offline with three sources of different worth.

Run: python -m unittest tests.test_sources_turns -v
"""
from __future__ import annotations

import json
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

import mo_flow  # noqa: E402
import mo_pool  # noqa: E402
import mo_providers  # noqa: E402
import mo_tasks  # noqa: E402
import mo_workspace as ws  # noqa: E402
import server  # noqa: E402

ws._dns_ok[:] = [False]
REAL_RACE = mo_providers.read_race

REGISTER = "name;city\n" + "".join(f"Association Sportive Numero {i};75011\n" for i in range(1, 41))
SCHOOLS = "uai;nom;ville;courriel\n" + "".join(
    f"07500{i}A;Lycée Turn {chr(64 + i)};Paris;ce.07500{i}a@ac-turn.test\n" for i in range(1, 6))
SCHOOLS_AGAIN = "uai,name,city\n" + "".join(f"07500{i}A,Lycee Turn {chr(64 + i)} (cité scolaire),75011\n"
                                            for i in range(1, 6))
CLUBS = "name;website;city\n" + "".join(f"Escalade Club {c};https://turn-club-{c.lower()}.test/;Paris\n"
                                        for c in "PQRSTU")


def fake_download(url, max_bytes=0):
    body = {"https://data.turn.test/register.csv": REGISTER, "https://data.turn.test/schools.csv": SCHOOLS,
            "https://data.turn.test/clubs.csv": CLUBS}.get(url)
    if body is None:
        raise mo_providers.ProviderError("HTTP 404")
    return {"final_url": url, "content_type": "text/csv", "bytes": body.encode("utf-8")}


def fake_read(url, readers=None, project_dir=None, **kw):
    for c in "PQRSTU":
        if url.startswith(f"https://turn-club-{c.lower()}.test"):
            html = (f'<div class="card"><p>Escalade Club {c}</p><p>Président</p><p>Jean Turn{c}</p>'
                    f"<p>president@turn-club-{c.lower()}.test</p></div>")
            return {"crawl4ai": {"final_url": url, "html": html, "text": ""}}
    return {"crawl4ai": {"error": "HTTP 404"}}


class Patched(unittest.TestCase):
    def setUp(self):
        self.patches = [mock.patch.object(mo_providers, "download", side_effect=fake_download),
                        mock.patch.object(mo_providers, "read", side_effect=fake_read),
                        mock.patch.object(mo_providers, "read_race", side_effect=fake_read),
                        mock.patch.object(mo_providers, "public_url", side_effect=lambda u: u),
                        mock.patch.object(mo_providers, "search", return_value={"results": [], "routes": {}}),
                        mock.patch.object(mo_tasks, "PILOT", 4)]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in reversed(self.patches):
            p.stop()

    def mine(self, p, name, url, **pool_args):
        server.sources(p, add=[{"source": name, "url": url, "kind": "open_data"}], agent="finder")
        got = server.fetch(p, url)
        return server.pool(p, got["readers"]["download"]["file"], source=name, **pool_args)


class SourcesTakeTurns(Patched):
    def test_three_sources_are_all_researched_and_a_list_of_names_waits_after_its_pilot(self):
        p = "turns-073"
        server.project(p, brief={"goal": "people who run sports venues", "country": "FR", "language": "fr",
                                 "fresh": True})
        pdir = ws.project_dir(p)
        # the biggest, emptiest source is pooled first - as in 072
        reg = self.mine(p, "register", "https://data.turn.test/register.csv", into="orgs")
        self.assertEqual(40, reg["orgs_added"])
        sch = self.mine(p, "schools", "https://data.turn.test/schools.csv", into="orgs",
                        map={"uai": "uai", "name": "nom", "city": "ville", "email": "courriel"})
        self.assertEqual(5, sch["contacts_from_rows"])           # the list's mailboxes are contacts now
        self.assertEqual("tools", sch["provenance"])
        clubs = self.mine(p, "clubs", "https://data.turn.test/clubs.csv", into="orgs")
        self.assertEqual(6, clubs["orgs_added"])

        # the same schools from another list, spelled differently, town as a postcode: one row each
        (pdir / "pages" / "schools-again.csv").write_text(SCHOOLS_AGAIN, encoding="utf-8")
        again = server.pool(p, "pages/schools-again.csv", into="orgs", source="schools-again")
        self.assertEqual((0, 5), (again["orgs_added"], again["orgs_merged"]))
        self.assertEqual("brought-in", again["provenance"])     # nobody fetched or registered it here
        self.assertIn("fresh", again["warning"])

        # the first packages a researcher claims hold every source, not the first one pooled
        first = mo_tasks.claim(pdir, "r1", "sweep")["task"]
        orgs = {o["id"]: o for o in ws.read_csv(pdir / "orgs.csv")}
        srcs = {mo_tasks.source_of(orgs[i]) for i in first["items"].split("|")}
        self.assertEqual(3, len(srcs), srcs)
        # the register has its pilot (4 here) planned and no more, while it is unproven
        planned = [i for t in ws.read_csv(pdir / "tasks.csv") for i in t["items"].split("|")]
        self.assertEqual(4, sum(mo_tasks.source_of(orgs[i]).endswith("register.csv") or
                                mo_tasks.source_of(orgs[i]) == "register" for i in planned if i in orgs))

        # researchers work everything handed out: clubs give A, schools C, the register nothing
        def work(task):
            ids = [i for i in task["items"].split("|") if i]
            sets = [{"value": c["value"], "org_id": c["org_id"], "grade": "A" if "president" in c["value"] else "C",
                     "person": "Jean" if "president" in c["value"] else "",
                     "role": "Président" if "president" in c["value"] else ""}
                    for c in ws.read_contacts(pdir) if c["org_id"] in ids]
            for i in ids:
                if orgs[i].get("website"):
                    ws.fetch(pdir, orgs[i]["website"], org_id=i, org=orgs[i]["name"])
                    sets += [{"value": c["value"], "org_id": i, "grade": "A", "person": "Jean", "role": "Président"}
                             for c in ws.contacts_of(pdir, i)]
            if sets:
                server.grade(p, set=sets)
            have = {c["org_id"] for c in ws.read_contacts(pdir) if c.get("grade") == "A"}
            server.tasks(p, action="verdict", agent="r", verdicts=[
                {"org_id": i, "verdict": "routes" if i in have else "none"} for i in ids])
            mo_tasks.done(pdir, task["task_id"], note="done")
        work(first)
        while True:
            t = mo_tasks.claim(pdir, "r1", "sweep").get("task")
            if not t:
                break
            work(t)
        turns = mo_tasks.source_yield(pdir)
        reg_key = next(k for k in turns if "register" in k)
        self.assertEqual("waiting", turns[reg_key]["state"], turns)   # 0 A/B: its other 30 wait
        self.assertEqual(mo_tasks.PAUSE_MIN, turns[reg_key]["worked"])   # a pilot of 4 is stretched to 10 before a pause
        self.assertEqual(("scaling", 6), (turns["clubs"]["state"], turns["clubs"]["worked"]))   # A/B: all of it
        self.assertEqual(("waiting", 5), (turns["schools"]["state"], turns["schools"]["worked"]))  # C only: all 5 judged
        listed = [c for c in ws.read_contacts(pdir) if c.get("found_in") == "list"]
        self.assertEqual((5, 5), (len(listed), sum(c.get("grade") == "C" for c in listed)))  # kept, all worked
        self.assertTrue(all("Lycée Turn" in c["quote"] for c in listed))   # quoted from their own row
        flags = " ".join(server.needs_you(pdir, server.project(p)))
        self.assertIn("register", flags)
        self.assertIn("scale", flags)

        # the lead's call: give the register its turn anyway
        server.sources(p, set=[{"source": reg_key if reg_key in {r["source"] for r in mo_flow.source_pool(pdir)}
                                else "register", "status": "scale"}])
        more = mo_tasks.claim(pdir, "r1", "sweep").get("task")
        self.assertIsNotNone(more)
        self.assertTrue(all("Association" in orgs[i]["name"] for i in more["items"].split("|")))

        # every call of the chain is on disk, without a transcript
        calls = [json.loads(x) for x in (pdir / "calls.jsonl").read_text().splitlines()] \
            if (pdir / "calls.jsonl").exists() else []
        self.assertEqual([], calls)   # the tool log is written by the MCP wrapper, tested below


class TheRest(Patched):
    def test_a_semicolon_file_whose_text_has_commas_is_read_by_its_header(self):
        pdir = ws.project_dir("delim-073")
        (pdir / "pages").mkdir(parents=True, exist_ok=True)
        (pdir / "pages" / "x.csv").write_text("nom;adresse;ville\nClub A;1, rue X, bât. 2;Paris\n", encoding="utf-8")
        rows = mo_pool._rows(pdir / "pages" / "x.csv")
        self.assertEqual("1, rue X, bât. 2", rows[0]["adresse"])

    def test_a_later_source_can_name_the_real_operator(self):
        p = "operator-073"
        pdir = ws.project_dir(p)
        (pdir / "pages").mkdir(parents=True, exist_ok=True)
        (pdir / "pages" / "owner.csv").write_text("venue;cp;owner\nGymnase Turn;75011;Ville de Paris\n", encoding="utf-8")
        (pdir / "pages" / "op.csv").write_text("venue;cp;operator\nGymnase Turn;75011;UCPA Turn\n", encoding="utf-8")
        m = {"name": "venue", "postcode": "cp"}
        server.pool(p, "pages/owner.csv", map=m, group_by="owner", source="owners")
        kept = server.pool(p, "pages/op.csv", map=m, group_by="operator", source="operators")
        self.assertEqual(0, kept["operators_replaced"])           # first writer kept unless asked
        won = server.pool(p, "pages/op.csv", map=m, group_by="operator", source="operators", replace_operator=True)
        self.assertEqual(1, won["operators_replaced"])
        venue = ws.read_csv(pdir / "venues.csv")[0]
        names = {o["id"]: o["name"] for o in ws.read_csv(pdir / "orgs.csv")}
        self.assertEqual("UCPA Turn", names[venue["org_id"]])
        self.assertEqual("Ville de Paris", names[venue["previous_org_id"]])

    def test_a_harvested_page_without_a_title_still_has_a_name(self):
        row = mo_flow._row_from_page({"url": "https://x.test/clubs/salle-du-turn", "title": "", "text": "",
                                      "contacts": []}, "x.test")
        self.assertEqual("Salle Du Turn", row["name"])

    def test_the_paid_agent_waits_for_the_user(self):
        p = "browse-073"
        server.project(p, brief={"goal": "x"})
        with mock.patch.object(mo_providers, "agent_submit", return_value="run-1") as submit:
            out = server.browse(p, "https://turn-club-p.test/team", why="names behind tabs")
            self.assertEqual("WAITING_FOR_USER", out["status"])
            submit.assert_not_called()
            flags = " ".join(server.needs_you(ws.project_dir(p), server.project(p)))
            self.assertIn("paid TinyFish Agent on 1 page", flags)
            server.project(p, brief={"goal": "x", "paid_agent": True})
            self.assertEqual("RUNNING", server.browse(p, "https://turn-club-p.test/team")["status"])
            submit.assert_called_once()

    def test_a_fresh_project_does_not_reuse_what_another_read(self):
        url = "https://turn-club-q.test/"
        server.project("reuse-a", brief={"goal": "x"})
        ws.fetch(ws.project_dir("reuse-a"), url)
        calls = []

        def count(u, *a, **k):
            calls.append(u)
            return fake_read(u)
        with mock.patch.object(mo_providers, "read", side_effect=count), \
                mock.patch.object(mo_providers, "read_race", side_effect=count):
            server.project("reuse-b", brief={"goal": "x"})
            ws.fetch(ws.project_dir("reuse-b"), url)
            self.assertEqual([], calls)                       # the shared library answered
            server.project("fresh-c", brief={"goal": "x", "fresh": True})
            ws.fetch(ws.project_dir("fresh-c"), url)
            self.assertEqual(1, len(calls))                   # a fresh project reads it itself
            ws.fetch(ws.project_dir("fresh-c"), url)
            self.assertEqual(1, len(calls))                   # and then reuses its own copy

    def test_a_site_read_waits_for_a_version_of_the_page_that_keeps_its_links(self):
        import threading
        import time as _t
        gate = threading.Event()

        def read(url, readers=None, project_dir=None, **kw):
            r = readers[0]
            if r == "tinyfish":   # fast, text without links
                return {"tinyfish": {"final_url": url, "html": "", "text": "Bienvenue " * 40}}
            gate.wait(2)
            return {"crawl4ai": {"final_url": url, "html": "<p>x</p>" * 40 + '<a href="/contact">Contact</a>', "text": ""}}
        with mock.patch.object(mo_providers, "read", side_effect=read), \
                mock.patch.object(mo_providers, "available_readers", side_effect=lambda r=None: list(r or [])):
            t = _t.time()
            fast = REAL_RACE("https://x.turn.test/", ["tinyfish", "crawl4ai"], grace=0.1)
            self.assertNotIn("crawl4ai", fast)                       # a plain read: fastest wins
            self.assertLess(_t.time() - t, 1.5)
            full = REAL_RACE("https://x.turn.test/", ["tinyfish", "crawl4ai"], grace=0.1, links=True)
            self.assertIn("href", full["crawl4ai"]["html"])          # a site read: the version with links

    def test_a_researcher_who_starts_first_waits_for_work_and_gets_its_guide(self):
        import threading
        p = "wait-073"
        server.project(p, brief={"goal": "x"})
        pdir = ws.project_dir(p)
        got = {}
        t = threading.Thread(target=lambda: got.update(server.tasks(p, action="claim", kind="sweep",
                                                                    agent="r1", wait=30)))
        t.start()
        server.sources(p, add=[{"source": "maps-11e", "url": "https://maps.turn.test/?q=gym", "kind": "maps"}])
        out = server.orgs(p, add=[{"name": "Gym Turn A", "city": "Paris"}, {"name": "Gym Turn B", "city": "Paris"}],
                          source="maps-11e", agent="miner")
        self.assertEqual({"maps-11e": 2}, out["sources"])
        t.join(40)
        self.assertIsNotNone(got.get("task"), got)                 # waited, then got the package
        self.assertIn("Role: research", got.get("role_guide", ""))
        orgs = ws.read_csv(pdir / "orgs.csv")
        self.assertEqual({"maps-11e"}, {o["sources"] for o in orgs})    # filed under its source
        self.assertEqual("pooled", next(r for r in mo_flow.source_pool(pdir) if r["source"] == "maps-11e")["status"])
        again = server.tasks(p, action="claim", kind="sweep", agent="r1")
        self.assertNotIn("role_guide", again)                       # once is enough

    def test_an_agent_b_on_a_general_inbox_is_kept_but_marked_for_review(self):
        p = "grade-073"
        server.project(p, brief={"goal": "x", "address_rules": {"info": "X"}})
        pdir = ws.project_dir(p)
        ws.merge_contacts(pdir, [{"kind": "email", "value": v, "quote": "q"} for v in
                                 ("accueil@turn.test", "info@turn.test", "marc.dupont@turn.test")], "o1", "Gym")
        out = server.grade(p, set=[{"value": "accueil@turn.test", "grade": "B"},
                                   {"value": "info@turn.test", "grade": "B", "role": "accueil"},
                                   {"value": "marc.dupont@turn.test", "grade": "A", "person": "Marc Dupont",
                                    "role": "gérant"}])
        rows = {r["value"]: r for r in ws.read_contacts(pdir)}
        self.assertEqual(("B", "yes"), (rows["accueil@turn.test"]["grade"], rows["accueil@turn.test"]["review"]))
        self.assertIn("general inbox", rows["accueil@turn.test"]["review_reason"])
        self.assertIn("address_rules", rows["info@turn.test"]["review_reason"])
        self.assertEqual("", rows["marc.dupont@turn.test"]["review"])          # a real A passes
        self.assertEqual(2, len(out["check"]))
        self.assertIn("D", out["grade_table"])
        server.grade(p, set=[{"value": "accueil@turn.test", "grade": "B", "role": "responsable", "confirm": True}])
        self.assertEqual("", {r["value"]: r for r in ws.read_contacts(pdir)}["accueil@turn.test"]["review"])

    def test_stores_of_a_chain_are_hinted_and_once_filed_the_chain_is_researched(self):
        p = "chain-073"
        server.project(p, brief={"goal": "x"})
        pdir = ws.project_dir(p)
        server.sources(p, add=[{"source": "maps", "url": "https://maps.turn.test/?q=gym", "kind": "maps"}])
        server.orgs(p, source="maps", add=[
            {"name": "Neo Turn Bastille", "city": "Paris", "website": "https://www.neoturn.test/clubs/bastille"},
            {"name": "Neo Turn Republique", "city": "Paris", "website": "https://www.neoturn.test/clubs/republique"},
            {"name": "Gym Solo", "city": "Paris", "website": "https://gymsolo.test/"}])
        chains = mo_tasks.possible_chains(pdir)
        self.assertEqual(["neoturn.test"], [c["site"] for c in chains])
        self.assertIn("chain", " ".join(server.needs_you(pdir, server.project(p))))
        hq = server.orgs(p, source="maps", add=[{"name": "Neo Turn (siège)", "city": "Paris",
                                                  "website": "https://www.neoturn.test/"}])
        self.assertEqual(1, hq["orgs_added"])
        ids = {o["name"]: o["id"] for o in ws.read_csv(pdir / "orgs.csv")}
        server.orgs(p, set=[{"org_id": ids[n], "part_of": ids["Neo Turn (siège)"]}
                            for n in ("Neo Turn Bastille", "Neo Turn Republique")])
        got = mo_tasks.claim(pdir, "r1", "sweep")["task"]["items"].split("|")
        self.assertEqual({ids["Gym Solo"], ids["Neo Turn (siège)"]}, set(got))   # not the stores
        self.assertEqual([], mo_tasks.possible_chains(pdir))

    def test_a_directory_of_addresses_on_one_organisation_is_not_all_sent_to_jev(self):
        import mo_grade
        p = "flood-073"
        server.project(p, brief={"goal": "x", "region": "Paris 75011"})
        pdir = ws.project_dir(p)
        rows = [{"kind": "email", "value": f"club{i}@chain.test", "quote": f"Club {i} - {'75011 Paris' if i < 5 else '69002 Lyon'}"}
                for i in range(120)]
        ws.merge_contacts(pdir, rows, "o1", "Chain")
        keep, held = mo_grade._flood(pdir, ws.read_contacts(pdir))
        self.assertEqual(mo_grade.FLOOD, len(keep))
        self.assertEqual({"o1": 120 - mo_grade.FLOOD}, held)
        self.assertTrue(all(f"club{i}@chain.test" in {r["value"] for r in keep} for i in range(5)))   # the district first

    def test_a_club_page_on_the_city_portal_is_read_as_that_page_not_the_portal(self):
        import mo_workspace
        self.assertTrue(mo_workspace.page_on_a_portal("https://www.paris.fr/lieux/volley-turn-1", "Paris Volley Turn",
                                                      "Paris"))
        self.assertFalse(mo_workspace.page_on_a_portal("https://volleyturn.test/equipe", "Volley Turn"))
        self.assertFalse(mo_workspace.page_on_a_portal("https://www.paris.fr/", "Ville de Paris"))
        pdir = ws.project_dir("portal-073")
        pdir.mkdir(parents=True, exist_ok=True)
        page = '<a href="/lieux/volley-turn-1/bureau">Bureau</a><a href="/mentions-legales">Mentions</a>' + "<p>x</p>" * 40
        seen = []

        def read(url, readers=None, project_dir=None, **kw):
            seen.append(url)
            return {"crawl4ai": {"final_url": url, "html": page, "text": ""}}
        with mock.patch.object(mo_providers, "read", side_effect=read), \
                mock.patch.object(mo_providers, "read_race", side_effect=read):
            ws._crawl_one(pdir, {"id": "v1", "name": "Paris Volley Turn", "city": "Paris",
                                 "website": "https://www.paris.fr/lieux/volley-turn-1"}, 5, None)
        self.assertIn("https://www.paris.fr/lieux/volley-turn-1/bureau", seen)
        self.assertNotIn("https://www.paris.fr/mentions-legales", seen)

    def test_an_agent_adds_a_list_row_with_its_address_and_it_becomes_a_contact(self):
        p = "rows-073"
        server.project(p, brief={"goal": "x"})
        server.sources(p, add=[{"source": "fed-75", "url": "https://fed.turn.test/clubs", "kind": "federation"}])
        out = server.orgs(p, source="fed-75", agent="miner", add=[
            {"name": "Badminton Turn 11", "city": "Paris", "email": "president@bad-turn.test", "person": "Ana Turn",
             "role": "Présidente", "page_url": "https://fed.turn.test/club/42",
             "quote": "Badminton Turn 11 - Présidente : Ana Turn - president@bad-turn.test"}])
        self.assertEqual(1, out["contacts_from_rows"])
        c = ws.read_contacts(ws.project_dir(p))[0]
        self.assertEqual(("Ana Turn", "Présidente", "https://fed.turn.test/club/42"), (c["person"], c["role"], c["page_url"]))
        self.assertIn("Présidente", c["quote"])
        self.assertNotIn("email", ws.read_csv(ws.project_dir(p) / "orgs.csv")[0])   # the address lives in contacts

    def test_held_addresses_stay_held_and_portal_addresses_are_not_the_clubs(self):
        import mo_grade
        p = "held-073"
        server.project(p, brief={"goal": "x"})
        pdir = ws.project_dir(p)
        ws.merge_contacts(pdir, [{"kind": "email", "value": f"c{i}@fed.test", "quote": f"Comité {i}"}
                                 for i in range(80)], "o1", "Club")
        mo_grade.auto(pdir)
        rows = ws.read_contacts(pdir)
        self.assertEqual(80 - mo_grade.FLOOD, sum(r.get("graded_by") == "held" for r in rows))
        mo_grade.auto(pdir)                                        # the next round leaves them alone
        self.assertEqual(80 - mo_grade.FLOOD, sum(r.get("graded_by") == "held" for r in ws.read_contacts(pdir)))
        # a club's fiche on its federation's site: the federation's addresses are not the club's
        p2 = "fiche-073"
        pdir2 = ws.project_dir(p2)
        pdir2.mkdir(parents=True, exist_ok=True)
        (pdir2 / "orgs.csv").write_text("id,name,website,city\nk1,Judo Turn,https://www.ffsportnat.test/club-judo-turn-12,Paris\n")
        html = ('<div><p>Judo Turn</p><p>Président : Paul Turn</p><p>judoturn@mail.test</p></div>'
                '<div><p>Comité de Paris</p><p>comite75@ffsportnat.test</p></div>' + "<p>x</p>" * 30)
        with mock.patch.object(mo_providers, "read", return_value={"crawl4ai": {"final_url": "u", "html": html, "text": ""}}):
            ws.fetch(pdir2, "https://www.ffsportnat.test/club-judo-turn-12", ["crawl4ai"], "k1", "Judo Turn", "ffsportnat.test")
        mine = {r["value"] for r in ws.read_contacts(pdir2)}
        self.assertIn("judoturn@mail.test", mine)
        self.assertNotIn("comite75@ffsportnat.test", mine)
        self.assertIn("comite75@ffsportnat.test", {r["value"] for r in ws.read_csv(pdir2 / "unattributed.csv")})

    def test_a_wait_stays_under_the_hosts_tool_timeout(self):
        import time as _t
        server.project("cap-wait-073", brief={"goal": "x"})
        with mock.patch.object(server, "WAIT_MAX", 1):
            t = _t.time()
            out = server.tasks("cap-wait-073", action="claim", kind="sweep", agent="r", wait=600)
        self.assertIsNone(out.get("task"))
        self.assertLess(_t.time() - t, 8)

    def test_the_team_runs_fixes(self):
        import mo_run
        p = "team-073"
        server.project(p, brief={"goal": "x", "country": "FR", "max_per_source": 2, "pilot": 5})
        pdir = ws.project_dir(p)
        # a big page's answer fits the host's tool output
        html = "".join(f"<p>Club {i} - club{i}@mail.test</p>" for i in range(150))
        with mock.patch.object(mo_providers, "read", return_value={"crawl4ai": {"final_url": "u", "html": html, "text": ""}}):
            out = server.fetch(p, "https://list.turn.test/clubs", ["crawl4ai"])
        self.assertEqual((60, 90), (len(out["contacts"]), out["contacts_not_shown"]))
        # rows an agent read, pooled without a file
        server.sources(p, add=[{"source": "salles", "url": "https://list.turn.test/salles", "kind": "directory"}])
        got = server.pool(p, rows=[{"name": f"Salle Turn {i}", "city": "Paris"} for i in range(4)], into="orgs",
                          source="salles", agent="miner")
        self.assertEqual((4, "source"), (got["orgs_added"], got["provenance"]))
        # the run's own per-source limit: two of the four handed out, and the lane says why it stops
        first = mo_tasks.claim(pdir, "r", "sweep")["task"]["items"].split("|")
        self.assertEqual(2, len(first))
        # run takes the worker's name; the paid Agent is its own flag
        with mock.patch.object(mo_run, "run", return_value={"started": True}) as r:
            server.run(p, agent="research-1", orgs=first)
            self.assertFalse(r.call_args.kwargs["agent"])
        self.assertEqual("research-1", [__import__("json").loads(x) for x in
                                        (pdir / "calls.jsonl").read_text().splitlines()][-1]["agent"]
                         if (pdir / "calls.jsonl").exists() else "research-1")
        # a foreign country's domain is not a Paris club's site
        hits = {"results": [{"url": "https://bouge-turn.ch/", "title": "Bouge Turn"},
                            {"url": "https://bouge-turn.fr/", "title": "Bouge Turn"}], "routes": {}}
        with mock.patch.object(mo_providers, "search", return_value=hits):
            self.assertEqual("https://bouge-turn.fr/", mo_run._find_site(pdir, {"id": "x", "name": "Bouge Turn",
                                                                                "city": "Paris"}))
        # an error page is not an entry
        self.assertTrue(mo_flow.ERROR_PAGE.search("Erreur 404 - page introuvable"))
        self.assertFalse(mo_flow.ERROR_PAGE.search("Club de 404 adhérents"))

    def test_waiting_on_organisations_a_batch_is_rereading(self):
        import mo_run
        p = "reread-073"
        server.project(p, brief={"goal": "x"})
        pdir = ws.project_dir(p)
        ws.write_csv(pdir / "orgs.csv", ["id", "name"], [{"id": "w1", "name": "W1"}])
        ws.append_csv(pdir / "crawl.csv", ws.CRAWL_COLS, {"org_id": "w1", "status": "done", "pass": "final"})
        mo_run._save_state(pdir, {"file": "batch.b1.json", "stage": "pipeline", "done": False, "orgs_ids": ["w1"]})
        with mock.patch.object(server, "WAIT_MAX", 1):
            out = server.orgs(p, ids=["w1"], wait=5)
        self.assertEqual(["w1"], out["still_reading"])          # an earlier final row is not this read's

    def test_every_tool_call_is_logged_with_its_agent_and_no_addresses(self):
        p = "calls-073"
        server.project(p, brief={"goal": "x"})
        server.logged(server.tasks)(p, action="status", agent="r9")
        server.logged(server.grade)(p, set=[{"value": "someone@turn.test", "grade": "A"}])
        rows = [json.loads(x) for x in (ws.project_dir(p) / "calls.jsonl").read_text().splitlines()]
        self.assertEqual(["tasks", "grade"], [r["tool"] for r in rows])
        self.assertEqual("r9", rows[0]["agent"])
        self.assertNotIn("someone@", json.dumps(rows))


if __name__ == "__main__":
    unittest.main(verbosity=2)
