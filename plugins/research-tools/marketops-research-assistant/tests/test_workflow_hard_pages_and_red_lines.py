"""Owner decision 2026-10-25: red lines only, and TinyFish Agent for hard pages.

- No call count and no total cap by default; each account keeps its red line.
- TinyFish Agent opens with the owner's one-time consent and runs in the
  background (it can take minutes; a host ends a tool call at 60 s). A run in
  flight during a restart is never resubmitted.
"""
from __future__ import annotations

import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import research_hard_pages as hard  # noqa: E402
import research_lite_store as store  # noqa: E402
import research_runtime_setup as runtime  # noqa: E402
import research_site_harvest as harvest  # noqa: E402
import research_spending as spending  # noqa: E402


def wait_for(predicate, seconds=5.0):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        if predicate():
            return True
        time.sleep(0.02)
    return False


class HardPageQueueTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name) / "data"

    def test_a_hard_page_runs_once_in_the_background_and_reports_its_material(self):
        calls, gate = [], threading.Event()

        def runner(run_id, url, goal, operation_id):
            calls.append(url)
            gate.wait(2)
            return {"status": "OK", "artifact_ids": ["vendor-html-1"]}
        first = hard.enqueue(self.root, "run-a", "https://club.example/contact", runner=runner)
        again = hard.enqueue(self.root, "run-a", "https://club.example/contact", runner=runner)
        self.assertTrue(first["queued"])
        self.assertFalse(again["queued"])  # never submitted twice
        gate.set()
        self.assertTrue(wait_for(lambda: hard.status(self.root, "run-a")["counts"].get("done")))
        page = hard.status(self.root, "run-a")["pages"][0]
        self.assertEqual(["vendor-html-1"], page["artifact_ids"])
        self.assertEqual(["https://club.example/contact"], calls)

    def test_a_refused_or_unknown_run_is_recorded_not_retried(self):
        hard.enqueue(self.root, "run-b", "https://a.example/",
                     runner=lambda *a: {"status": "BLOCKED", "reason_code": "BUDGET_PROVIDER_CEILING"})
        hard.enqueue(self.root, "run-b", "https://b.example/",
                     runner=lambda *a: {"status": "UNKNOWN", "reason_code": "UNKNOWN_CHARGE_OUTSTANDING"})
        self.assertTrue(wait_for(lambda: sum(hard.status(self.root, "run-b")["counts"].get(k, 0)
                                             for k in ("failed", "unknown")) == 2))
        reasons = {p["url"]: (p["status"], p["reason"]) for p in hard.status(self.root, "run-b")["pages"]}
        self.assertEqual(("failed", "BUDGET_PROVIDER_CEILING"), reasons["https://a.example/"])
        self.assertEqual("unknown", reasons["https://b.example/"][0])

    def test_a_run_in_flight_at_restart_is_unknown_and_never_resent(self):
        hard.enqueue(self.root, "run-c", "https://slow.example/")  # no runner: stays queued
        self.assertEqual(1, hard.recover(self.root))
        page = hard.status(self.root, "run-c")["pages"][0]
        self.assertEqual("unknown_after_restart", page["status"])
        self.assertFalse(hard.enqueue(self.root, "run-c", "https://slow.example/")["queued"])

    def test_only_public_https_pages(self):
        with self.assertRaisesRegex(ValueError, "HARD_PAGE_URL_INVALID"):
            hard.enqueue(self.root, "run-d", "http://plain.example/")


class AgentConsentTests(unittest.TestCase):
    def configure(self, root, **kwargs):
        with mock.patch.dict("os.environ", {"TINYFISH_API_KEY": "fixture-key"}):
            runtime.configure(root, providers=["tinyfish", "crawl4ai"], domains=[],
                              max_calls=runtime.MAX_CALLS_CEILING, task="Synthetic scoped task",
                              confirm=True, public_web_fetch=True, **kwargs)

    def test_the_agent_is_off_until_the_owner_says_yes_once(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "data"
            self.configure(root)
            off = runtime.admission(root, "tinyfish.run_web_automation")
            self.assertEqual("POLICY_TIER_NOT_GRANTED", off["reason_code"])
            self.configure(root, amend=True, paid_agent=True)
            with mock.patch.dict("os.environ", {"TINYFISH_API_KEY": "fixture-key"}):
                on = runtime.admission(root, "tinyfish.run_web_automation")
            self.assertNotEqual("POLICY_TIER_NOT_GRANTED", on.get("reason_code"))


class RedLineDefaultTests(unittest.TestCase):
    def test_the_default_policy_has_no_total_cap(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "data"
            runtime.configure(root, providers=["serper", "crawl4ai"], domains=[],
                              max_calls=runtime.MAX_CALLS_CEILING, task="Synthetic scoped task",
                              confirm=True, public_web_fetch=True)
            state = spending.configure(root, confirm=True, model="RED_LINE", observations={})
            self.assertIsNone(state["total_red_line_usd"])
            plan = runtime.state_ledger_caps(root, "run-x")
            self.assertIsNone(plan["run_max_calls"])  # MONEY mode: no call count


class HarvestHandsHardPagesOverTests(unittest.TestCase):
    def test_an_unreadable_site_is_queued_and_a_blocked_one_is_not(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            store.start_run(root, "r", "clubs", "objective", "v1")
            queued = []
            technical = harvest.harvest(
                root, run_id="r", operation_id="h1", organization_name="Rendered Club",
                organization_key="org-a", url="https://rendered.example/", max_pages=1,
                fetch=lambda url, op: {"status": "HOLD", "reason_code": "FALLBACK_UNCLASSIFIED_FAILURE"},
                hard_page=lambda url: queued.append(url) or {"queued": True})
            blocked = harvest.harvest(
                root, run_id="r", operation_id="h2", organization_name="Walled Club",
                organization_key="org-b", url="https://walled.example/", max_pages=1,
                fetch=lambda url, op: {"status": "HOLD",
                                       "reason_code": "FALLBACK_SOURCE_OR_POLICY_REFUSED"},
                hard_page=lambda url: queued.append(url) or {"queued": True})
        self.assertEqual(["https://rendered.example/"], queued)
        self.assertTrue(technical["next_action"].startswith("HARD_PAGE_QUEUED"))
        self.assertTrue(blocked["next_action"].startswith("SITE_UNREACHABLE"))


if __name__ == "__main__":
    unittest.main()
