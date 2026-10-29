"""End-to-end simulation of a whole job, offline: a fake web with four overlapping sources and a
team of agents (finder, two miners, three researchers) working the lanes at the same time, then
delivery. It checks the things that went wrong in real runs and must never come back: a pool
from one source, venues without an organisation, organisations nobody worked, a pool that does
not add up, a key refused mid-call that was not replaced at once."""
import json
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

import mo_config  # noqa: E402
import mo_flow  # noqa: E402
import mo_providers  # noqa: E402
import mo_run  # noqa: E402
import mo_tasks  # noqa: E402
import mo_workspace as ws  # noqa: E402
import server  # noqa: E402

ws._dns_ok[:] = [False]

# ------------------------------------------------------------------ the fake web
CLUBS = {n: f"Club Sportif {name}" for n, name in enumerate(
    ["Alpha", "Beta", "Gamma", "Delta", "Epsilon", "Zeta", "Eta", "Theta", "Iota", "Kappa", "Lambda", "Mu"], 1)}
TOWNS = {"a": "Ville de Arcy", "b": "Ville de Brou", "c": "Ville de Cany"}
WEB: dict[str, str] = {}


def page(body: str, title: str = "") -> str:
    return f"<html><head><title>{title}</title></head><body>{body}</body></html>"


def build_web():
    # source 1: a federation finder, two listing pages, one page per club
    WEB["https://fed.test/clubs"] = page("".join(f'<a href="/club/{n}">{CLUBS[n]}</a> ' for n in range(1, 7))
                                         + '<a href="/clubs?page=2">2</a>', "Clubs")
    WEB["https://fed.test/clubs?page=2"] = page("".join(f'<a href="/club/{n}">{CLUBS[n]}</a> ' for n in range(7, 11)))
    for n in range(1, 13):
        WEB[f"https://fed.test/club/{n}"] = page(
            f"<h1>{CLUBS[n]}</h1><p>Stade municipal</p><p>7500{n % 10} Paris</p>"
            f'<a href="https://club{n}.test/">Site du club</a> <a href="https://sponsor.test/">Sponsor</a>',
            f"{CLUBS[n]} | Fédération")
        WEB[f"https://club{n}.test/"] = page(f'<p>Bienvenue au {CLUBS[n]}</p><a href="/equipe">Équipe</a>'
                                             f'<a href="/contact">Contact</a>')
        WEB[f"https://club{n}.test/equipe"] = page(
            f'<div class="card"><p>Président</p><p>Jean Test{n}</p><p>president@club{n}.test</p></div>')
        WEB[f"https://club{n}.test/contact"] = page(f"<p>contact@club{n}.test</p>")
    for k, town in TOWNS.items():
        WEB[f"https://ville-{k}.test/"] = page(f'<p>{town}</p><a href="/annuaire">Annuaire des services</a>')
        WEB[f"https://ville-{k}.test/annuaire"] = page(
            f'<div class="card"><p>Responsable du service des sports</p><p>Marie Test{k}</p>'
            f"<p>sports@ville-{k}.test</p></div><p>accueil@ville-{k}.test</p>")
    # source 3: a city portal linking out to clubs, mostly known ones, one new
    WEB["https://portal.test/annuaire"] = page("".join(f'<a href="https://club{n}.test/">{CLUBS[n]}</a> '
                                                       for n in (1, 2, 3, 4, 11)))
    # source 4: a regional list that repeats what is pooled
    WEB["https://region.test/clubs"] = page("".join(f'<a href="https://club{n}.test/">{CLUBS[n]}</a> '
                                                    for n in (1, 2, 3, 5, 6, 7, 8)))


# source 2: the national facility census - venues with the organisation that runs them
CENSUS = "\n".join(["equip_nom;commune;gestionnaire;site_gestionnaire"] + [
    f"Stade {i} {TOWNS[k]};{TOWNS[k].split()[-1]};{TOWNS[k]};https://ville-{k}.test/"
    for i, k in enumerate("aabbcc", 1)] + [
    f"Terrain du {CLUBS[n]};Paris;{CLUBS[n]};https://club{n}.test/" for n in (1, 2, 12)])


def fake_read(url, readers=None, project_dir=None, **kw):
    html = WEB.get(url) or WEB.get(url.rstrip("/") + "/") or WEB.get(url.rstrip("/"))
    if html is None:
        return {"crawl4ai": {"error": "HTTP 404"}}
    return {"crawl4ai": {"final_url": url, "html": html, "text": ""}}


def fake_download(url, max_bytes=0):
    if url == "https://data.test/equipements.csv":
        return {"final_url": url, "content_type": "text/csv", "bytes": CENSUS.encode("utf-8")}
    raise mo_providers.ProviderError("HTTP 404")


# ------------------------------------------------------------------ the agents
class Team:
    def __init__(self, project):
        self.p = project
        self.pdir = ws.project_dir(project)
        self.finder_done = threading.Event()
        self.miners_done = threading.Event()
        self.times: dict[str, list] = {"registered": [], "swept": []}
        self.errors: list = []

    def finder(self):
        try:
            for src in ({"source": "federation", "url": "https://fed.test/clubs", "kind": "federation", "row_is": "club"},
                        {"source": "census", "url": "https://data.test/equipements.csv", "kind": "open_data",
                         "row_is": "venue with its operator"},
                        {"source": "portal", "url": "https://portal.test/annuaire", "kind": "directory", "row_is": "club"},
                        {"source": "region", "url": "https://region.test/clubs", "kind": "directory", "row_is": "club"}):
                server.sources(self.p, add=[src], agent="finder-1")
                self.times["registered"].append(time.time())
                time.sleep(0.4)   # still looking while the miners work
        except Exception as exc:  # noqa: BLE001
            self.errors.append(("finder", exc))
        finally:
            self.finder_done.set()

    def mine(self, src):
        name = src["source"]
        if name == "federation":
            out = server.harvest(self.p, name, start=src["url"], follow=r"/club/\d+$", next=r"page=\d+$", agent="m")
            while out["still_queued"]:
                out = server.harvest(self.p, name, agent="m")
            server.pool(self.p, out["file"], into="orgs", source=name,
                        map={"name": "name", "website": "website", "city": "city", "page_url": "page_url"})
        elif name == "census":
            got = server.fetch(self.p, src["url"])
            server.pool(self.p, got["readers"]["download"]["file"], into="venues", source=name,
                        map={"name": "equip_nom", "city": "commune", "operator": "gestionnaire"},
                        group_by="gestionnaire", org_fields={"website": "site_gestionnaire", "city": "commune"})
        else:
            out = server.harvest(self.p, name, start=src["url"], agent="m")
            server.pool(self.p, out["file"], into="orgs", source=name,
                        map={"name": "name", "website": "website", "page_url": "page_url"})

    def miner(self, me):
        try:
            while True:
                got = mo_tasks.claim(self.pdir, me, "source")
                if got.get("task"):
                    self.mine(got["items"][0])
                    mo_tasks.done(self.pdir, got["task"]["task_id"], note="pooled")
                elif self.finder_done.is_set():
                    return
                else:
                    time.sleep(0.05)
        except Exception as exc:  # noqa: BLE001
            self.errors.append((me, exc))

    def researcher(self, me):
        try:
            while True:
                got = mo_tasks.claim(self.pdir, me, "sweep")
                task = got.get("task")
                if not task:
                    if self.miners_done.is_set():
                        got = mo_tasks.claim(self.pdir, me, "sweep")   # the last pooled ones
                        if not got.get("task"):
                            return
                        task = got["task"]
                    else:
                        time.sleep(0.05)
                        continue
                ids = [i for i in task["items"].split("|") if i]
                self.times["swept"].append(time.time())
                mo_run.run_sync(self.pdir, rounds=0, workers=4, job={"id": task["task_id"], "orgs": ids})
                server.grade(self.p, orgs=ids)   # its own package only; no Jev: the rules grade the obvious rows
                # the agent's judgment: read each quote, grade what the rules left
                todo = [c for c in ws.read_contacts(self.pdir) if c.get("org_id") in ids and c.get("kind") == "email"
                        and not c.get("grade")]
                sets = []
                for c in todo:
                    q = c.get("quote", "")
                    person = next((w for w in q.split(" / ") if "Test" in w), "")
                    sets.append({"value": c["value"], "org_id": c["org_id"], "grade": "A" if person else "C",
                                 "person": person.strip(), "role": "Président" if "Président" in q else ""})
                if sets:
                    server.grade(self.p, set=sets)
                have = {c["org_id"] for c in ws.read_contacts(self.pdir) if c.get("grade") in ("A", "B")}
                server.tasks(self.p, action="verdict", agent=me, verdicts=[
                    {"org_id": i, "verdict": "routes" if i in have else "none", "found": "", "where": "site",
                     "why": "simulated"} for i in ids])
                mo_tasks.done(self.pdir, task["task_id"], note="done")
        except Exception as exc:  # noqa: BLE001
            self.errors.append((me, exc))


class WholeJob(unittest.TestCase):
    def test_a_team_builds_every_pool_at_once_and_delivers(self):
        build_web()
        p = "e2e-paris"
        server.project(p, brief={"goal": "people who run sports grounds", "country": "FR", "language": "fr"})
        team = Team(p)
        with mock.patch.object(mo_providers, "read", side_effect=fake_read), \
                mock.patch.object(mo_providers, "read_race", side_effect=fake_read), \
                mock.patch.object(mo_providers, "download", side_effect=fake_download), \
                mock.patch.object(mo_providers, "public_url", side_effect=lambda u: u), \
                mock.patch.object(mo_providers, "search", return_value={"results": [], "routes": {}}):
            finder = threading.Thread(target=team.finder)
            miners = [threading.Thread(target=team.miner, args=(f"miner-{i}",)) for i in (1, 2)]
            researchers = [threading.Thread(target=team.researcher, args=(f"researcher-{i}",)) for i in (1, 2, 3)]
            for t in [finder, *miners, *researchers]:
                t.start()
            finder.join(60)
            for t in miners:
                t.join(120)
            team.miners_done.set()
            for t in researchers:
                t.join(300)
            book = server.export(p)
        self.assertEqual([], team.errors)
        pdir = team.pdir
        fl = mo_flow.flow(pdir)
        pools = fl["pools"]

        # pool 1: several sources, every one mined and pooled
        self.assertEqual(4, pools["sources"]["known"], pools["sources"])
        self.assertEqual(4, pools["sources"]["pooled"], mo_flow.source_pool(pdir))
        # the pipeline overlapped: research started before the last source was even found
        self.assertLess(min(team.times["swept"]), max(team.times["registered"]))
        # venues exist and each has the organisation that runs it
        self.assertEqual(9, pools["venues"]["total"])
        self.assertEqual(0, pools["venues"]["without_organisation"])
        # organisations: the union of the sources, merged across them, and it adds up
        orgs = ws.read_csv(pdir / "orgs.csv")
        self.assertEqual(15, len(orgs), sorted(o["name"] for o in orgs))   # 12 clubs + 3 towns
        self.assertTrue(any("|" in o.get("sources", "") for o in orgs))    # seen by several sources
        self.assertTrue(fl["reconcile"]["adds_up"], fl["reconcile"])
        # every organisation was worked: routes or a verdict
        verdicts = {v["org_id"] for v in ws.read_csv(pdir / "verdicts.csv")}
        self.assertEqual(set(), {o["id"] for o in orgs} - verdicts)
        contacts = ws.read_contacts(pdir)
        named_a = {c["org_id"] for c in contacts if c.get("grade") == "A" and c.get("person")}
        missing = [(o["id"], o["name"], o.get("website"), [(c["value"], c.get("grade"), c.get("person"), c.get("review"))
                    for c in contacts if c["org_id"] == o["id"]],
                    [(r.get("pass"), r.get("status"), r.get("note", "")[:120]) for r in ws.read_csv(pdir / "crawl.csv") if r["org_id"] == o["id"]])
                   for o in orgs if o["id"] not in named_a]
        self.assertEqual(15, len(named_a), missing)                          # every club president, every town head
        self.assertTrue(all(c.get("page_url") and c.get("quote") for c in contacts))
        # nothing left waiting between the pools, nothing for the user about the flow
        self.assertEqual({"sources -> pages": 0, "pages -> pools": 0, "venues -> organisations": 0,
                          "organisations -> websites": 0, "organisations -> contacts": 0, "contacts -> grades": 0},
                         {k: v for k, v in fl["waiting_between_pools"].items()
                                                            if k != "contacts -> delivery"})
        flags = " ".join(server.needs_you(pdir, server.project(p)))
        for bad in ("nobody mines", "never pooled", "no organisation behind", "does not add up"):
            self.assertNotIn(bad, flags)
        self.assertTrue(Path(book["file"]).exists())


class GradingDoesNotOverlap(unittest.TestCase):
    def test_each_agent_grades_its_own_package_once(self):
        import types
        import mo_budget
        import mo_grade
        pdir = ws.project_dir("grade-scope")
        for o in range(6):
            ws.merge_contacts(pdir, [{"kind": "email", "value": f"chef{k}@org{o}.test", "quote": f"Chef {k} chef{k}@org{o}.test",
                                      "page_url": f"https://org{o}.test/", "same_site": "yes", "mailbox": "personal"}
                                     for k in range(3)], f"o{o}", f"Org {o}")
        asked = []
        lock = threading.Lock()

        def fake_jev(state, qs):
            with lock:
                asked.extend(k for k in qs if k.startswith("grade_"))
            time.sleep(0.2)
            return types.SimpleNamespace(answers={k: types.SimpleNamespace(choice="decision_maker", probabilities={"decision_maker": 0.9})
                                                  for k in qs if k.startswith("grade_")})
        with mock.patch.object(mo_budget, "jev_ready", return_value=True), \
                mock.patch.object(mo_grade, "_jev", side_effect=fake_jev), \
                mock.patch.object(mo_budget, "allowed", return_value=(True, "")):
            t0 = time.time()
            agents = [threading.Thread(target=mo_grade.auto, args=(pdir,), kwargs={"orgs": [f"o{i}", f"o{i + 3}"]})
                      for i in range(3)]
            for a in agents:
                a.start()
            for a in agents:
                a.join(30)
            took = time.time() - t0
        self.assertEqual(18, len(asked))          # 6 organisations x 3 addresses, each asked once
        self.assertLess(took, 1.5)                # the three packages graded side by side
        self.assertTrue(all(c.get("grade") for c in ws.read_contacts(pdir)))


class PoliteAndAsync(unittest.TestCase):
    def setUp(self):
        self.home = tempfile.mkdtemp()
        self.env = mock.patch.dict(os.environ, {"MARKETOPS_HOME": self.home})
        self.env.start()

    def tearDown(self):
        self.env.stop()

    def test_one_host_never_gets_more_than_its_slots_and_a_429_pauses_it(self):
        live, peak, lock = [0], [0], threading.Lock()

        def slow_read(url):
            with lock:
                live[0] += 1
                peak[0] = max(peak[0], live[0])
            time.sleep(0.1)
            with lock:
                live[0] -= 1
            return {"final_url": url, "html": "<p>" + "x" * 300 + "</p>", "text": ""}
        with mock.patch.dict(mo_providers._READ, {"http": slow_read}), \
                mock.patch.object(mo_providers, "HOST_INTERVAL", 0.0):
            readers = [threading.Thread(target=mo_providers.read, args=(f"https://portal.test/v/{i}", ["http"]))
                       for i in range(12)]
            t0 = time.time()
            for t in readers:
                t.start()
            for t in readers:
                t.join(30)
        self.assertLessEqual(peak[0], mo_providers.HOST_CAP)        # many organisations, one portal: polite
        self.assertGreater(time.time() - t0, 0.25)                   # 12 reads in rounds of at most 4

        def busy(url):
            raise mo_providers.ProviderError("HTTP 429 retry-after=7")
        with mock.patch.dict(mo_providers._READ, {"http": busy}):
            mo_providers.read("https://busy.test/a", ["http"])
        st = json.loads((Path(self.home) / "rate" / "host_busy_test.json").read_text())
        self.assertGreater(st["until"], time.time() + 4)             # paused for its Retry-After
        self.assertEqual(mo_providers.HOST_CAP - 1, st["cap"])       # and one slot fewer from now on

    def test_waiting_for_a_package_is_one_call(self):
        pdir = ws.project_dir("wait-pkg")
        ws.write_csv(pdir / "orgs.csv", ["id", "name"], [{"id": "w1", "name": "W1"}])

        def finish():
            time.sleep(1)
            ws.append_csv(pdir / "crawl.csv", ws.CRAWL_COLS + ["pass"], {"org_id": "w1", "status": "done", "pass": "final"})
        threading.Thread(target=finish).start()
        t0 = time.time()
        out = server.orgs("wait-pkg", ids=["w1"], wait=30)
        self.assertLess(time.time() - t0, 10)
        self.assertEqual("pipeline done", out["orgs"][0]["state"], out)

    def test_harvest_keeps_reading_in_the_background(self):
        build_web()
        with mock.patch.object(mo_providers, "read", side_effect=fake_read), \
                mock.patch.object(mo_providers, "read_race", side_effect=fake_read), \
                mock.patch.object(mo_providers, "public_url", side_effect=lambda u: u):
            out = server.harvest("bg-harvest", "fed", start="https://fed.test/clubs", follow=r"/club/\d+$",
                                 next=r"page=\d+$", limit=3, background=True)
            self.assertIn("background", out)
            end = time.time() + 20
            while time.time() < end and server.sources("bg-harvest")["sources"][0]["status"] != "harvested":
                time.sleep(0.2)
        rows = ws.read_csv(ws.project_dir("bg-harvest") / out["file"])
        self.assertEqual(10, len(rows))


class KeysSwitchInsideTheCall(unittest.TestCase):
    def setUp(self):
        self.home = tempfile.mkdtemp()
        self.env = mock.patch.dict(os.environ, {"MARKETOPS_HOME": self.home, "SERPER_API_KEY": "key-one-aaaa,key-two-bbbb"})
        self.env.start()

    def tearDown(self):
        self.env.stop()

    def test_a_refused_key_is_replaced_and_the_same_search_succeeds(self):
        class Resp:
            def __init__(self, code, data):
                self.status_code, self._d, self.headers, self.text = code, data, {}, json.dumps(data)

            def json(self):
                return self._d
        used = []

        def post(url, json=None, headers=None, **kw):
            used.append(headers["X-API-KEY"])
            if headers["X-API-KEY"] == "key-one-aaaa":
                return Resp(402, {"message": "Not enough credits"})
            return Resp(200, {"organic": [{"title": "A", "link": "https://a.test/", "snippet": ""}]})
        with mock.patch("httpx.post", side_effect=post):
            out = mo_providers.search("slot switch unique query", ["serper"])
        self.assertEqual(["key-one-aaaa", "key-two-bbbb"], used)          # one search, two keys, no failure
        self.assertEqual(1, len(out["results"]))
        self.assertEqual("key-two-bbbb", mo_config.key("serper"))


if __name__ == "__main__":
    unittest.main()
