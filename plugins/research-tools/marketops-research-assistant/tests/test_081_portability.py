"""The same plugin on every host and every computer (0.8.1).

0.8.0 behaved differently by where it ran: Claude Code loaded the skills, WorkBuddy and Codex
loaded whatever the user copied, and the Windows installer copied only the scripts - its server
handed every worker an empty role guide. A host with one agent had no way to run the pipeline.
Now the server carries its own instructions (the MCP handshake and guide()), one agent can claim
the whole pipeline (kind="any"), and both installers put the whole plugin behind one launcher.

The conformance test at the end speaks MCP over stdio to a real server process, as every host
does; CI runs it on Linux, macOS and Windows.

Run: python -m unittest tests.test_081_portability -v
"""
from __future__ import annotations

import asyncio
import json
import os
import re
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

import mo_providers  # noqa: E402
import mo_workspace as ws  # noqa: E402
import server  # noqa: E402

ws._dns_ok[:] = [False]


class Guide(unittest.TestCase):
    def test_every_skill_and_role_is_readable_through_the_server(self):
        index = server.guide()
        self.assertIn("guide(", index["start"])
        for topic in index["topics"]:
            got = server.guide(topic)
            self.assertNotIn("error", got, topic)
            self.assertGreater(len(got["text"]), 200, topic)
            self.assertFalse(got["text"].startswith("---"), topic)   # no loader front matter
        self.assertIn("role:research", index["topics"])
        self.assertIn("What to do when", server.guide("workflow")["text"])

    def test_an_unknown_topic_lists_the_real_ones(self):
        got = server.guide("everything")
        self.assertIn("error", got)
        self.assertIn("workflow", got["topics"])

    def test_the_handshake_carries_the_rules_that_keep_a_run_good(self):
        for rule in ('guide("workflow")', "minutes", 'kind="any"', "lane_finished", "never guess"):
            self.assertIn(rule, server.INSTRUCTIONS)


class OneAgent(unittest.TestCase):
    def setUp(self):
        self.p = [mock.patch.object(mo_providers, "public_url", side_effect=lambda u: u),
                  mock.patch.object(mo_providers, "search", return_value={"results": [], "routes": {}})]
        for x in self.p:
            x.start()

    def tearDown(self):
        for x in reversed(self.p):
            x.stop()

    def test_one_agent_gets_the_pipeline_in_order(self):
        p = "any081"
        server.project(p, brief={"goal": "g", "country": "FR", "language": "fr"})
        server.orgs(p, add=[{"name": "Club Seul A"}, {"name": "Club Seul B"}], source="premiere liste")
        server.sources(p, add=[{"source": "liste 081", "url": "https://liste081.test/"}])   # nobody mines it yet
        first = server.tasks(p, action="claim", kind="any", agent="solo")
        self.assertEqual("source", first["task"]["kind"])        # what feeds the rest goes first
        self.assertTrue(first.get("role_guide"))
        server.tasks(p, action="done", task_id=first["task"]["task_id"], note="mined")
        second = server.tasks(p, action="claim", kind="any", agent="solo")
        self.assertEqual("sweep", second["task"]["kind"])
        out = server.tasks(p, action="done", task_id=second["task"]["task_id"], note="read")
        self.assertIn("kind='any'", out["next"])

    def test_a_stopped_team_stops_one_agent_too(self):
        p = "stop081"
        server.project(p, brief={"goal": "g", "country": "FR", "language": "fr"})
        server.tasks(p, action="stop", note="done for today")
        self.assertTrue(server.tasks(p, action="claim", kind="any", agent="solo")["stop"])


class Installers(unittest.TestCase):
    PARTS = ("scripts", "skills", "references", "agents", ".claude-plugin")

    def test_both_installers_copy_the_whole_plugin_behind_one_launcher(self):
        sh = (ROOT / "install.sh").read_text(encoding="utf-8")
        ps = (ROOT / "Install.ps1").read_text(encoding="utf-8")
        for part in self.PARTS:
            self.assertIn(part, sh)
            self.assertIn(f'"{part}"', ps)
        self.assertIn("bin/marketops-mcp", sh)
        self.assertIn("marketops-mcp.cmd", ps)
        self.assertIn('dirname -- "$0"', sh)                      # "sh install.sh" works too
        self.assertNotRegex(ps, r"(?m)^claude mcp")               # no Claude Code: not an error

    def test_the_check_names_a_plugin_folder_without_its_skills(self):
        with mock.patch.object(server, "PLUGIN", Path(tempfile.mkdtemp())), \
                mock.patch("sys.stdout", new_callable=lambda: __import__("io").StringIO()) as out:
            self.assertEqual(1, server.check())
        self.assertIn("skills is missing", out.getvalue())


COUNTER = """
import sys
sys.path.insert(0, sys.argv[1])
from pathlib import Path
import mo_workspace as ws
path = Path(sys.argv[2])
for _ in range(150):
    with ws._lock(path):
        n = int(path.read_text() or 0) if path.exists() else 0
        path.write_text(str(n + 1))
"""


class CrossProcessLock(unittest.TestCase):
    def test_two_server_processes_never_lose_a_write(self):
        import subprocess
        d = Path(tempfile.mkdtemp())
        (d / "count.py").write_text(COUNTER, encoding="utf-8")
        target = d / "tasks.csv"
        procs = [subprocess.Popen([sys.executable, str(d / "count.py"), str(ROOT / "scripts"), str(target)])
                 for _ in range(2)]
        for p in procs:
            self.assertEqual(0, p.wait(120))
        self.assertEqual("300", target.read_text())


def _client():
    try:
        from mcp import ClientSession, StdioServerParameters
        from mcp.client.stdio import stdio_client
    except ImportError:
        return None
    return ClientSession, StdioServerParameters, stdio_client


@unittest.skipIf(_client() is None, "the mcp package is not installed")
class Conformance(unittest.TestCase):
    """What every host does: start the server over stdio, read the handshake, list the tools,
    call them. The same answers on every operating system are the plugin's portability."""

    def test_a_host_sees_the_same_server_everywhere(self):
        ClientSession, StdioServerParameters, stdio_client = _client()
        env = {**os.environ, "MARKETOPS_HOME": tempfile.mkdtemp(), "MARKETOPS_PROJECTS": tempfile.mkdtemp(),
               "MARKETOPS_HEALTH": "0", "MARKETOPS_KNOWLEDGE": "0"}
        params = StdioServerParameters(command=sys.executable, args=[str(ROOT / "scripts" / "server.py")], env=env)

        async def talk():
            async with stdio_client(params, errlog=open(os.devnull, "w")) as (read, write):
                async with ClientSession(read, write) as s:
                    init = await s.initialize()
                    tools = {t.name for t in (await s.list_tools()).tools}
                    prompts = {p.name for p in (await s.list_prompts()).prompts}

                    async def call(tool, **args):
                        res = await s.call_tool(tool, args)
                        return json.loads(res.content[0].text)
                    workflow = await call("guide", topic="workflow")
                    role = await call("guide", topic="role:research")
                    proj = await call("project", name="conf081", brief={"goal": "g", "country": "FR",
                                                                        "language": "fr", "minutes": 30})
                    claim = await call("tasks", project="conf081", action="claim", kind="any", agent="solo")
                    return init, tools, prompts, workflow, role, proj, claim
        init, tools, prompts, workflow, role, proj, claim = asyncio.run(asyncio.wait_for(talk(), 120))
        self.assertIn('guide("workflow")', init.instructions or "")
        self.assertTrue({"guide", "project", "tasks", "fetch", "grade", "export"} <= tools)
        self.assertIn("research", prompts)
        self.assertIn("What to do when", workflow["text"])
        self.assertIn("Role: research", role["text"])
        self.assertEqual(server.VERSION, proj["version"])
        self.assertGreater(proj["time_left_min"], 29)
        self.assertIsNone(claim.get("task"))             # nothing pooled yet: an empty claim that says why
        self.assertTrue(claim.get("why_empty"))


class Docs(unittest.TestCase):
    def test_no_doc_gives_a_worker_the_old_start_line(self):
        for f in [ROOT / "HOSTS.md", *ROOT.glob("skills/*/SKILL.md"), *ROOT.glob("references/roles/*.md")]:
            self.assertNotRegex(f.read_text(encoding="utf-8"), re.compile(r"until your queue is empty"), f.name)


if __name__ == "__main__":
    unittest.main()
