"""The fixes from the Paris 11e run on 0.7.8 (paris11-078, 0.7.9).

The final report listed seven questions, five of them the lead's own close-out; one registered
source nobody mined kept a researcher claiming for 17 minutes; TinyFish's balance was raised
although only its free search and reading were used; the run had to be archived by hand.

Run: python -m unittest tests.test_079_fixes -v
"""
from __future__ import annotations

import os
import subprocess
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

import mo_export  # noqa: E402
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


class UnminedSources(Base):
    def test_a_source_nobody_mines_is_work_to_take_not_a_reason_to_wait(self):
        p = "u079"
        server.project(p, brief={"goal": "g", "country": "FR", "language": "fr"})
        done = self.org(p, "Club Deja Lu")
        ws.add_verdicts(ws.project_dir(p), [{"org_id": done, "verdict": "none"}], "r0")   # all worked
        server.sources(p, add=[{"source": "ligue universitaire", "url": "https://ligue079.test/"}])
        got = server.tasks(p, action="claim", kind="dig", agent="r1")
        self.assertIsNone(got["task"])
        self.assertNotIn("keep_waiting", got)
        self.assertIn("source", got.get("other_lanes", {}))
        self.assertIn("nobody mines", got["why_empty"])
        server.tasks(p, action="claim", kind="source", agent="m1")   # a miner takes it: now it is coming
        self.assertTrue(server.tasks(p, action="claim", kind="dig", agent="r1").get("keep_waiting"))


class NeedsYou(Base):
    def test_items_say_whose_they_are_and_free_tinyfish_is_not_one(self):
        p, pdir = "n079", ws.project_dir("n079")
        server.project(p, brief={"country": "FR", "language": "fr"})
        items = server.project(p)["needs_you"]
        self.assertTrue(any("[user]" in x and "no goal" in x for x in items))
        status = {"accounts": {"tinyfish": {"balance": 0.26, "red_line": 1.0}}, "capabilities": {"search": ["serper"]}}
        self.assertFalse(any("tinyfish" in x for x in server.needs_you(pdir, status)))
        status["accounts"]["serpapi"] = {"balance": 0, "red_line": 50}
        self.assertFalse(any("serpapi" in x for x in server.needs_you(pdir, status)))   # serper still searches
        self.assertEqual("lead", server.owner("12 A/B rows are marked review"))


class CloseOut(Base):
    def test_the_workbook_is_not_deliverable_until_the_close_out_is_done(self):
        p, pdir = "c079", ws.project_dir("c079")
        a = self.org(p, "MACCABI", city="Paris")
        self.org(p, "SPORTING CLUB MACCABI de PARIS (SCMP)")
        rows = [{"org_id": a, "value": f"p{i}@c079.test", "grade": "A", "checked": "ok"} for i in range(20)]
        rows.append({"org_id": a, "value": "q@c079.test", "grade": "B", "review": "yes"})
        gate = mo_export.delivery_gate(rows, pdir)
        self.assertFalse(gate["ready"])
        self.assertEqual(2, len(gate["todo"]))   # one review, one duplicate group
        server.orgs(p, merge="suggested", keep_apart=[a])   # the lead: two organisations
        self.assertEqual([], mo_tasks.possible_duplicates(pdir))   # remembered, not offered again
        rows[-1]["review"] = ""
        self.assertTrue(mo_export.delivery_gate(rows, pdir)["ready"])


class DeepSeekBase(unittest.TestCase):
    def test_deepseek_goes_through_a_gateway_when_one_is_set(self):
        import httpx

        import mo_budget
        import mo_config
        self.assertEqual("https://api.deepseek.com", mo_config.base_url("deepseek"))
        sent = []

        class R:
            status_code = 200
            headers: dict = {}

            def json(self):
                return {"results": []}

        def post(url, **k):
            sent.append(url)
            return R()
        with mock.patch.dict(os.environ, {"DEEPSEEK_BASE_URL": "http://127.0.0.1:4000/v1/", "DEEPSEEK_API_KEY": "k"}), \
                mock.patch.object(httpx, "post", side_effect=post), \
                mock.patch.object(httpx, "get", side_effect=AssertionError("no balance call through a gateway")):
            self.assertEqual("http://127.0.0.1:4000", mo_config.base_url("deepseek"))
            mo_providers._deepseek("club escrime", "fr", "fr", 5, False)
            self.assertIsNone(mo_budget._read_balance("deepseek"))
        self.assertEqual(["http://127.0.0.1:4000/v1/tools/search"], sent)
        self.assertTrue(mo_config.official("deepseek"))


class Archive(unittest.TestCase):
    def test_a_run_is_archived_redacted_and_refused_if_anything_survives(self):
        tool = ROOT / "tools" / "archive_run.py"
        proj, repo = Path(tempfile.mkdtemp()), Path(tempfile.mkdtemp())
        (proj / "contacts.csv").write_text("org,value,quote\nClub,jean.dupont@gmail.com,Tél 01 43 55 22 10 / "
                                           "marie @ club . fr\n", encoding="utf-8")
        (proj / "RETRO.md").write_text("# retro\n", encoding="utf-8")
        (proj / "pages").mkdir()
        (proj / "pages" / "raw.html").write_text("x@y.fr", encoding="utf-8")
        ok = subprocess.run([sys.executable, str(tool), str(proj), str(repo), "run-079"], capture_output=True, text=True)
        self.assertEqual(0, ok.returncode, ok.stderr)
        text = (repo / "runs" / "run-079" / "contacts.csv").read_text(encoding="utf-8")
        self.assertNotIn("@", text.replace("<email>", ""))
        self.assertIn("<phone>", text)
        self.assertFalse((repo / "runs" / "run-079" / "raw.html").exists())
        (proj / "journal.jsonl").write_text('{"text": "api_key=abcdef0123456789abcdef"}\n', encoding="utf-8")
        bad = subprocess.run([sys.executable, str(tool), str(proj), str(repo), "run-079b"], capture_output=True, text=True)
        self.assertEqual(1, bad.returncode)
        self.assertFalse((repo / "runs" / "run-079b").exists())


if __name__ == "__main__":
    unittest.main()
