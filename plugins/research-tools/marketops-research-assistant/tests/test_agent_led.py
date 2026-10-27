"""Agents lead, tools follow (0.7.2, from the Paris 071 audit).

071 let the whole-project run dig six rounds alone for six hours: 15 organisations with A/B for
105 organisation-hours, 58% of addresses shared by three or more organisations, 4,094 of 4,096
threads, then a memory stall. These tests hold the fixes, and simulate the chain end to end
offline: a whole-project run reading ahead, researcher agents taking the sweep and dig lanes,
judging, digging a district directory themselves, writing verdicts.

Run: python -m unittest tests.test_agent_led -v
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

import mo_pool  # noqa: E402
import mo_providers  # noqa: E402
import mo_run  # noqa: E402
import mo_tasks  # noqa: E402
import mo_workspace as ws  # noqa: E402
import server  # noqa: E402

ws._dns_ok[:] = [False]

NAMES = {1: "Alpha", 2: "Beta", 3: "Gamma", 4: "Delta", 5: "Epsilon", 6: "Zeta", 7: "Eta", 8: "Theta"}
WEB: dict[str, str] = {}


def page(body: str) -> str:
    return f"<html><body>{body}</body></html>"


def build_web() -> None:
    for n, name in NAMES.items():
        WEB[f"https://chainclub{n}.test/"] = page(f'<p>Club Sportif {name}</p><a href="/equipe">Équipe</a>')
        # clubs 1-5 print their president; 6-8 print no one on their own site
        WEB[f"https://chainclub{n}.test/equipe"] = page(
            f'<div class="card"><p>Président</p><p>Jean Test{n}</p><p>president@chainclub{n}.test</p></div>'
            if n <= 5 else "<p>Nos équipes jouent le samedi.</p>")
    # a district directory: one block per club, addresses off the clubs' own domains, plus the
    # district's own inbox - each club may take only its own block
    WEB["https://district.chain.test/annuaire"] = page(
        "".join(f'<div class="card"><p>Club Sportif {NAMES[n]}</p><p>Secrétaire</p><p>Anne Test{n}</p>'
                f"<p>{NAMES[n].lower()}.secretaire@mail.test</p></div>" for n in (6, 7, 8))
        + "<p>Contact du district : district@mail.test</p>")


def fake_read(url, readers=None, project_dir=None, **kw):
    html = WEB.get(url) or WEB.get(url.rstrip("/") + "/") or WEB.get(url.rstrip("/"))
    if html is None:
        return {"crawl4ai": {"error": "HTTP 404"}}
    return {"crawl4ai": {"final_url": url, "html": html, "text": ""}}


def write_orgs(pdir: Path, ns) -> None:
    pdir.mkdir(parents=True, exist_ok=True)
    (pdir / "orgs.csv").write_text("id,name,website,city\n" + "".join(
        f"c{n},Club Sportif {NAMES[n]},https://chainclub{n}.test/,Paris\n" for n in ns), encoding="utf-8")


def providers():
    return [mock.patch.object(mo_providers, "read", side_effect=fake_read),
            mock.patch.object(mo_providers, "read_race", side_effect=fake_read),
            mock.patch.object(mo_providers, "public_url", side_effect=lambda u: u),
            mock.patch.object(mo_providers, "search", return_value={"results": [], "routes": {}})]


class Patched(unittest.TestCase):
    def setUp(self):
        build_web()
        self.patches = providers() + [mock.patch.object(mo_run, "PACE_CHECK_S", 0)]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in reversed(self.patches):
            p.stop()


# ------------------------------------------------------------------ the chain
class AgentLedChain(Patched):
    def researcher(self, me: str, pdir: Path, run_over: threading.Event, log: list, errors: list) -> None:
        """A researcher agent: sweep first, then dig what the tools read; judge, dig, verdict."""
        time.sleep(1.5)   # the lead starts the tools first; researchers join a moment later
        try:
            while True:
                got = mo_tasks.claim(pdir, me, "sweep")
                if not got.get("task"):
                    got = mo_tasks.claim(pdir, me, "dig")
                task = got.get("task")
                if not task:
                    if run_over.is_set():
                        return
                    time.sleep(0.2)
                    continue
                ids = [i for i in task["items"].split("|") if i]
                log.append((me, task["kind"], ids))
                if task["kind"] == "sweep":   # the tools read the package for the agent
                    mo_run.run_sync(pdir, workers=4, job={"id": task["task_id"], "orgs": ids})
                names = {o["id"]: o["name"] for o in ws.read_csv(pdir / "orgs.csv")}
                for i in ids:
                    if not any(c.get("person") for c in ws.contacts_of(pdir, i)):
                        # the agent's own dig: a directory it found, read for this organisation
                        server.fetch(pdir.name, "https://district.chain.test/annuaire", org_id=i, org=names[i])
                sets = []
                for c in ws.read_contacts(pdir):
                    if c.get("org_id") in ids and c.get("kind") == "email" and not c.get("graded_by", "").startswith("agent"):
                        person = next((w.strip() for w in c.get("quote", "").split(" / ") if "Test" in w), "")
                        role = "Président" if "Président" in c["quote"] else "Secrétaire" if "Secrétaire" in c["quote"] else ""
                        sets.append({"value": c["value"], "org_id": c["org_id"], "grade": "A" if person else "D",
                                     "person": person, "role": role})
                if sets:
                    server.grade(pdir.name, set=sets)
                have = {c["org_id"] for c in ws.read_contacts(pdir) if c.get("grade") == "A"}
                server.tasks(pdir.name, action="verdict", agent=me, verdicts=[
                    {"org_id": i, "verdict": "routes" if i in have else "none", "found": "", "where": "",
                     "why": "simulated"} for i in ids])
                mo_tasks.done(pdir, task["task_id"], note="done")
        except Exception as exc:  # noqa: BLE001
            errors.append((me, repr(exc)))

    def test_a_whole_project_run_reads_ahead_and_agents_research(self):
        pdir = ws.project_dir("chain-072")
        write_orgs(pdir, NAMES)
        run_over, log, errors, result = threading.Event(), [], [], {}

        def lead_run():   # the lead starts the tools reading the pool ahead of the researchers
            try:
                result.update(mo_run.run_sync(pdir, rounds=6, workers=2))   # rounds above the cap
            finally:
                run_over.set()
        with mock.patch.object(mo_run, "AHEAD", 3):
            threads = [threading.Thread(target=lead_run)] + [
                threading.Thread(target=self.researcher, args=(f"researcher-{k}", pdir, run_over, log, errors))
                for k in (1, 2)]
            for t in threads:
                t.start()
            for t in threads:
                t.join(240)
        self.assertEqual([], errors)
        self.assertTrue(result.get("done"), result.get("stopped_because"))
        self.assertEqual("every organisation went through the pipeline", result["stopped_because"])
        self.assertEqual(0, result["settings"]["rounds"])   # the cap: whole-project runs never dig
        crawl = ws.read_csv(pdir / "crawl.csv")
        self.assertFalse([r for r in crawl if r.get("pass", "").startswith("deep")])   # no code digging
        flow = result["progress"]["flow"]
        self.assertEqual(0, flow["failed"])
        self.assertEqual(0, sum(flow[k] for k in ("queued", "site", "grading", "digging", "evidence")))
        # both lanes were worked by agents: the run's reads went to `dig`, untouched ones to `sweep`
        kinds = {k for _me, k, _ids in log}
        self.assertEqual({"dig", "sweep"}, kinds, log)
        # every organisation: an agent verdict, never handed out twice
        verdicts = [v["org_id"] for v in ws.read_csv(pdir / "verdicts.csv")]
        self.assertEqual({f"c{n}" for n in NAMES}, set(verdicts))
        handed = [i for _me, _k, ids in log for i in ids]
        self.assertEqual(len(handed), len(set(handed)), log)
        # each club's person, and the directory's addresses only where their block names the club
        contacts = ws.read_contacts(pdir)
        named = {c["org_id"]: c["value"] for c in contacts if c.get("grade") == "A"}
        self.assertEqual({f"c{n}" for n in NAMES}, set(named), named)
        for n in (6, 7, 8):
            self.assertEqual(f"{NAMES[n].lower()}.secretaire@mail.test", named[f"c{n}"])
        owners: dict[str, set] = {}
        for c in contacts:
            owners.setdefault(c["value"], set()).add(c["org_id"])
        self.assertEqual({}, {v: o for v, o in owners.items() if len(o) > 1})   # no address shared
        self.assertNotIn("district@mail.test", owners)
        held = {r["value"] for r in ws.read_csv(pdir / "unattributed.csv")}
        self.assertIn("district@mail.test", held)

    def test_without_agents_the_tools_stop_after_reading_a_little_ahead(self):
        pdir = ws.project_dir("idle-072")
        write_orgs(pdir, NAMES)
        with mock.patch.object(mo_run, "AHEAD", 2), mock.patch.object(mo_run, "AGENT_IDLE_MIN", 0):
            state = mo_run.run_sync(pdir, workers=1)
        self.assertIn("no agent took work", state["stopped_because"])
        finals = [r for r in ws.read_csv(pdir / "crawl.csv") if r.get("pass") == "final"]
        self.assertLessEqual(len(finals), 3, finals)        # read ahead, then waited, then ended
        self.assertFalse((pdir / "STOP").exists())          # the next run is not blocked
        flags = " ".join(server.needs_you(pdir, server.project(pdir.name)))
        self.assertIn("wait for researchers", flags)


# ------------------------------------------------------------------ the pieces
class Pieces(Patched):
    def test_rounds_are_capped_and_a_dry_round_ends_the_dig(self):
        pdir = ws.project_dir("dig-072")
        write_orgs(pdir, [6])
        searches = []

        def search(q, *a, **k):
            searches.append(q)
            return {"results": [], "routes": {}}
        with mock.patch.object(mo_providers, "search", side_effect=search):
            state = mo_run.run_sync(pdir, rounds=6, workers=1, job={"id": "j", "orgs": ["c6"]})
        self.assertEqual(mo_run.MAX_ROUNDS, state["settings"]["rounds"])
        trace = ws.read_csv(pdir / "crawl.csv")
        self.assertTrue(trace)
        self.assertLessEqual(len(searches), 6)   # round 1 found nothing new: no round 2

    def test_a_failing_organisation_leaves_the_in_flight_counts(self):
        pdir = ws.project_dir("fail-072")
        write_orgs(pdir, [1, 2])
        real = ws._crawl_one

        def crawl(p, t, *a, **k):
            if t.get("id") == "c1":
                raise RuntimeError("can't start new thread")
            return real(p, t, *a, **k)
        with mock.patch.object(ws, "_crawl_one", side_effect=crawl):
            state = mo_run.run_sync(pdir, workers=2, job={"id": "f", "orgs": ["c1", "c2"]})
        flow = state["progress"]["flow"]
        self.assertEqual((1, 1), (flow["failed"], flow["done"]), flow)
        self.assertEqual(0, flow["site"] + flow["queued"])

    def test_a_url_every_reader_failed_on_twice_is_not_read_again_until_it_succeeds(self):
        pdir = ws.project_dir("bad-072")
        pdir.mkdir(parents=True, exist_ok=True)
        calls = []

        def read(url, *a, **k):
            calls.append(url)
            return {"crawl4ai": {"error": "HTTP 500"}}
        with mock.patch.object(mo_providers, "read", side_effect=read), \
                mock.patch.object(mo_providers, "read_race", side_effect=read):
            for _ in range(4):
                got = ws.fetch(pdir, "https://dead.chain.test/x", ["crawl4ai"])
            self.assertEqual(2, len(calls))
            self.assertIn("known bad URL", got["readers"]["skipped"]["error"])
            self.assertEqual(0, got["email_buttons_without_address"])
        ws.fetch(pdir, "https://dead.chain.test/x", ["crawl4ai"], refresh=True)   # a forced read may still try
        WEB["https://dead.chain.test/x"] = page("<p>back@dead.chain.test</p>")
        ws.fetch(pdir, "https://dead.chain.test/x", ["crawl4ai"], refresh=True)
        self.assertEqual(0, ws.known_bad(pdir, "https://dead.chain.test/x"))   # one success clears it

    def test_a_data_file_read_has_every_key_a_page_read_has(self):
        pdir = ws.project_dir("dl-072")
        pdir.mkdir(parents=True, exist_ok=True)
        with mock.patch.object(mo_providers, "download", side_effect=mo_providers.ProviderError("HTTP 404")):
            got = ws.fetch(pdir, "https://data.test/x.csv")
        page_keys = set(ws._no_page("u", {}))
        self.assertTrue(page_keys <= set(got))

    def test_the_machine_budget_bounds_every_pool(self):
        b = mo_providers.machine_budget()
        self.assertTrue(2 <= b["browser_tabs"] <= 12 and 2 <= b["orgs_in_flight"] <= 24 and 4 <= b["reader_threads"] <= 16, b)
        with mock.patch.object(mo_providers, "MAX_ORGS_IN_FLIGHT", 8), \
                mock.patch("subprocess.Popen") as popen:
            popen.return_value.pid = 999999
            pdir = ws.project_dir("cap-072")
            pdir.mkdir(parents=True, exist_ok=True)
            out = mo_run.run(pdir, workers=24, shards=4)
        self.assertEqual(1, out["processes"])   # 8 organisations at once: one browser process
        opts = popen.call_args[0][0]
        self.assertEqual(8, __import__("json").loads(opts[opts.index("--opts") + 1])["workers"])
        (pdir / "run.pid").unlink(missing_ok=True)

    def test_the_dig_lane_refills_but_never_hands_an_organisation_out_twice(self):
        pdir = ws.project_dir("lane-072")
        write_orgs(pdir, [6, 7])
        for i in ("c6", "c7"):
            ws.append_csv(pdir / "crawl.csv", ws.CRAWL_COLS, {"org_id": i, "pass": "final", "status": "done"})
        first = mo_tasks.claim(pdir, "r1", "dig")
        self.assertEqual({"c6", "c7"}, set(first["task"]["items"].split("|")))
        mo_tasks.done(pdir, first["task"]["task_id"], note="nothing public")
        self.assertIsNone(mo_tasks.claim(pdir, "r2", "dig").get("task"))


class Attribution(unittest.TestCase):
    def c(self, value, quote):
        return {"kind": "email", "value": value, "quote": quote, "email_site": "mail.test", "email_on_org": "no"}

    def test_the_next_entry_of_a_list_does_not_name_the_owner(self):
        row = self.c("alpha@mail.test", "Club Alpha / alpha@mail.test [next: Club Beta]")
        keep, _ = ws.split_attribution([row], "Club Beta", "beta.test", "no")
        self.assertEqual([], keep)

    def test_the_rarest_word_of_the_name_must_be_in_the_quote(self):
        counts = mo_pool.common_words(["Gymnase Paris Rousseau", "Gymnase Paris Voltaire", "Gymnase Paris Hugo"])
        row = self.c("hugo@mail.test", "Gymnase Paris / Hugo / hugo@mail.test")
        keep, _ = ws.split_attribution([row], "Gymnase Paris Voltaire", "", "no", counts)
        self.assertEqual([], keep)
        keep, _ = ws.split_attribution([row], "Gymnase Paris Hugo", "", "no", counts)
        self.assertEqual(1, len(keep))

    def test_a_name_word_inside_another_word_is_not_a_mention(self):
        self.assertFalse(mo_pool.mentions("Club Sportif Eta", "Club Sportif Zeta", {"eta": 1, "sportif": 8}))
        self.assertTrue(mo_pool.mentions("Club Sportif Eta", "Clubs Sportifs Eta"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
