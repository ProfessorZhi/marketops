"""The fixes from the WorkBuddy run on Windows (0.8.3, 0.8.5).

Crawl4AI failed on every page (the host's sandbox denied the browser the network:
net::ERR_NETWORK_ACCESS_DENIED) and was still tried first on each one, 31 times; DeepSeek behind a
gateway answered 404 73 times of 75; the reason was cut at 80 characters, before the words that
said it; a lead started one worker for every lane and nothing told it.

Run: python -m unittest tests.test_085_fixes -v
"""
from __future__ import annotations

import io
import json
import os
import sys
import tempfile
import unittest
from datetime import datetime
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
DENIED = ("Unexpected error in _crawl_web at line 779 in _crawl_web (crawl4ai/async_crawler_strategy.py):\n"
          "Error: Failed on navigating ACS-GOTO:\nPage.goto: net::ERR_NETWORK_ACCESS_DENIED at https://example.com/\n"
          "Call log:\n  - navigating to \"https://example.com/\"")


class SetAside(unittest.TestCase):
    def setUp(self):
        mo_providers._STREAK.clear()

    def tearDown(self):
        mo_providers._STREAK.clear()

    def test_a_reader_that_fails_on_site_after_site_is_set_aside_then_skipped(self):
        calls = []

        def broken(url):
            calls.append(url)
            raise mo_providers.ProviderError(DENIED)
        with mock.patch.dict(mo_providers._READ, {"http": broken}), \
                mock.patch.object(mo_providers, "_again", side_effect=lambda f, **k: f()):
            for i in range(mo_providers.BREAK_AFTER):
                mo_providers.read(f"https://site{i % 3}.test/p{i}", ["http"])
            self.assertIn("read:http", mo_providers.paused())
            got = mo_providers.read("https://site9.test/", ["http"])
        self.assertEqual(mo_providers.BREAK_AFTER, len(calls))          # the next page never waited on it
        self.assertIn("set aside", got["http"]["error"])
        self.assertIn("ERR_NETWORK_ACCESS_DENIED", mo_providers.paused()["read:http"]["why"])
        effects = " ".join(mo_providers.capabilities()["effects"])
        self.assertIn("http is set aside", effects)

    def test_one_bad_site_does_not_set_a_reader_aside(self):
        with mock.patch.dict(mo_providers._READ, {"http": mock.Mock(side_effect=mo_providers.ProviderError("HTTP 403"))}), \
                mock.patch.object(mo_providers, "_again", side_effect=lambda f, **k: f()):
            for i in range(8):
                mo_providers.read(f"https://same.test/p{i}", ["http"])
        self.assertEqual({}, mo_providers.paused())

    def test_a_success_starts_the_count_again(self):
        for i in range(mo_providers.BREAK_AFTER - 1):
            mo_providers._streak("read:x", False, f"h{i}", "boom")
        mo_providers._streak("read:x", True)
        mo_providers._streak("read:x", False, "h9", "boom")
        self.assertEqual({}, mo_providers.paused())

    def test_the_browsers_own_reason_is_kept(self):
        self.assertIn("net::ERR_NETWORK_ACCESS_DENIED", mo_providers._last_line(DENIED))


class Check(unittest.TestCase):
    def test_the_check_reads_a_real_page_and_says_when_the_browser_cannot(self):
        with mock.patch.object(mo_providers, "crawl4ai_installed", return_value=True), \
                mock.patch.object(mo_providers.BROWSER, "read", side_effect=mo_providers.ProviderError(DENIED)), \
                mock.patch("sys.stdout", new_callable=io.StringIO) as out:
            server.check()
        self.assertIn("browser reader: cannot read pages here", out.getvalue())
        self.assertIn("ERR_NETWORK_ACCESS_DENIED", out.getvalue())


class Hands(unittest.TestCase):
    def test_one_worker_for_several_lanes_is_named_to_the_lead(self):
        p, pdir = "hands085", ws.project_dir("hands085")
        server.project(p, brief={"goal": "g", "country": "FR", "language": "fr"})
        server.sources(p, add=[{"source": "liste 085", "url": "https://liste085.test/"}])
        server.orgs(p, add=[{"name": f"Club Main {i}"} for i in range(4)], source="autre liste")
        now = datetime.now().isoformat(timespec="seconds")
        (pdir / "calls.jsonl").write_text(json.dumps({"agent": "w1", "tool": "tasks", "time": now}) + "\n",
                                          encoding="utf-8")
        items = " ".join(server.project(p).get("needs_you") or [])
        self.assertIn("Only 1 worker(s) active", items)


if __name__ == "__main__":
    unittest.main()
