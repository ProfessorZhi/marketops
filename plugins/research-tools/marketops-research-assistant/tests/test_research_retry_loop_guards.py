"""Offline regression for provider loop guards and compact research progress.

No actual network, API credentials, model calls or production files.
"""
from __future__ import annotations

import os
import sqlite3
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import dual_search_mcp as search
import research_provider_extensions as ext
import research_event_journal as journal
import research_lite_store as store
import research_workflow as workflow


class ReservationLoopTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name) / "data"
        self.env = patch.dict(os.environ, {
            "MARKETOPS_RESEARCH_ALLOW_NETWORK": "1",
            "MARKETOPS_RESEARCH_MAX_EXTERNAL_CALLS": "20",
            "CRAWL4AI_API_KEY": "unit-only-fake-not-a-secret",
            "TINYFISH_API_KEY": "unit-only-fake-not-a-secret",
        })
        self.env.start()
        self.addCleanup(self.env.stop)

    def reserve(self, *, op="one", sig="signature-one", url="https://www.public.example.org/a",
                run="run-one", action="crawl4ai_cloud"):
        return ext._reserve(self.root, run_id=run, operation_id=op, action=action,
                            signature=sig, host_key=ext._host_key(url))

    def test_renaming_operation_does_not_reissue_the_same_request(self):
        self.reserve()
        with self.assertRaisesRegex(ValueError, "DUPLICATE_RESEARCH_ATTEMPT_SAME_RUN"):
            self.reserve(op="two")
        with sqlite3.connect(self.root / "state" / "provider-reservations.sqlite3") as db:
            self.assertEqual(1, db.execute("SELECT COUNT(*) FROM provider_reservations").fetchone()[0])

    def test_challenge_blocks_different_path_on_same_host(self):
        self.reserve()
        ext._record_outcome(self.root, "run-one", "one", "SOURCE_CHALLENGE_PAGE")
        with self.assertRaisesRegex(ValueError, "HOST_PREVIOUSLY_BLOCKED_SAME_RUN"):
            self.reserve(op="two", sig="different", url="https://public.example.org/other")
        self.reserve(op="three", sig="otherhost", url="https://independent.example.net/page")

    def test_403_also_blocks_a_different_provider_on_same_host(self):
        self.reserve()
        ext._record_outcome(self.root, "run-one", "one", "SOURCE_ACCESS_BLOCKED")
        with self.assertRaisesRegex(ValueError, "HOST_PREVIOUSLY_BLOCKED_SAME_RUN"):
            self.reserve(op="two", sig="diff", url="https://public.example.org/other",
                         action="tinyfish_fetch")

    def test_browser_setup_error_does_not_falsely_block_entire_host(self):
        self.reserve()
        ext._record_outcome(self.root, "run-one", "one", "LOCAL_BROWSER_NOT_INSTALLED")
        self.reserve(op="two", sig="other-source", url="https://public.example.org/other")
        with self.assertRaisesRegex(ValueError, "DUPLICATE_RESEARCH_ATTEMPT_SAME_RUN"):
            self.reserve(op="three")

    def test_other_run_is_not_prohibited_by_a_prior_run_challenge(self):
        self.reserve()
        ext._record_outcome(self.root, "run-one", "one", "SOURCE_CHALLENGE_PAGE")
        self.reserve(op="one", sig="signature-one", run="another-run")

    def test_legacy_reservations_migrate_without_erasing_old_attempts(self):
        self.root.joinpath("state").mkdir(parents=True)
        path = self.root / "state" / "provider-reservations.sqlite3"
        with sqlite3.connect(path) as db:
            db.execute("CREATE TABLE provider_reservations ("
                       "run_id TEXT NOT NULL, operation_id TEXT NOT NULL, action TEXT NOT NULL,"
                       "signature TEXT NOT NULL, created_at TEXT NOT NULL,"
                       "PRIMARY KEY (run_id,operation_id))")
            db.execute("INSERT INTO provider_reservations VALUES (?,?,?,?,?)",
                       ("run-one", "old", "crawl4ai_cloud", "legacy-signature", "2020-01-01"))
        self.reserve(op="new", sig="new-signature")
        with sqlite3.connect(path) as db:
            columns = {row[1] for row in db.execute("PRAGMA table_info(provider_reservations)")}
            self.assertTrue({"host_key", "outcome_code"} <= columns)
            self.assertEqual(2, db.execute("SELECT COUNT(*) FROM provider_reservations").fetchone()[0])
            old = db.execute("SELECT signature FROM provider_reservations WHERE operation_id='old'").fetchone()[0]
            self.assertEqual("legacy-signature", old)

    def test_no_raw_hostname_or_secret_stored_in_attempt_row(self):
        self.reserve()
        with sqlite3.connect(self.root / "state" / "provider-reservations.sqlite3") as db:
            key = db.execute("SELECT host_key FROM provider_reservations").fetchone()[0]
        self.assertEqual(64, len(key))
        self.assertNotIn("public.example.org", key)

    def test_search_rejects_the_same_paid_query_with_new_operation_id(self):
        search._reserve(self.root, run_id="run-one", operation_id="a",
                        fingerprint="search-fingerprint", provider="serper", limit=5)
        with self.assertRaisesRegex(ValueError, "DUPLICATE_SEARCH_SAME_RUN"):
            search._reserve(self.root, run_id="run-one", operation_id="b",
                            fingerprint="search-fingerprint", provider="serper", limit=5)
        with sqlite3.connect(self.root / "state" / "dual-search-ledger.sqlite3") as db:
            self.assertEqual(1, db.execute("SELECT COUNT(*) FROM search_attempts").fetchone()[0])


class CrawlChallengeClassificationTests(unittest.TestCase):
    def test_200_response_challenge_is_detected_even_when_crawl_failed(self):
        async def _enter(self):
            return self
        async def _exit(self, exc_type, exc, tb):
            return None
        async def _arun(self, *, url):
            return types.SimpleNamespace(
                success=False, status_code=200,
                markdown="Checking your browser", html="<title>Just a moment...</title> cf-chl-")
        fake = type("FakeCrawler", (), {
            "__aenter__": _enter, "__aexit__": _exit, "arun": _arun})
        with patch.dict(sys.modules, {"crawl4ai": types.SimpleNamespace(AsyncWebCrawler=fake)}):
            with self.assertRaisesRegex(ValueError, "SOURCE_CHALLENGE_PAGE"):
                ext._crawl4ai_local("https://public.example.org/staff")


    def test_cloudflare_header_detected_without_html_marker(self):
        async def _enter(self):
            return self
        async def _exit(self, exc_type, exc, tb):
            return None
        async def _arun(self, *, url):
            return types.SimpleNamespace(
                success=True, status_code=200,
                response_headers={"CF-Mitigated": "challenge"},
                markdown="normal-looking text", html="<p>ordinary page</p>")
        fake = type("FakeCrawler", (), {
            "__aenter__": _enter, "__aexit__": _exit, "arun": _arun})
        with patch.dict(sys.modules, {"crawl4ai": types.SimpleNamespace(AsyncWebCrawler=fake)}):
            with self.assertRaisesRegex(ValueError, "SOURCE_CHALLENGE_PAGE"):
                ext._crawl4ai_local("https://public.example.org/info")


    def test_embedded_turnstile_on_readable_page_is_not_a_full_challenge(self):
        async def _enter(self):
            return self
        async def _exit(self, exc_type, exc, tb):
            return None
        async def _arun(self, *, url):
            return types.SimpleNamespace(
                success=True, status_code=200, response_headers={},
                markdown="A readable public catalogue of community facilities.",
                html="<h1>Community Facilities</h1><script src='https://"
                     "challenges.cloudflare.com/turnstile/v0/api.js'></script>")
        fake = type("FakeCrawler", (), {
            "__aenter__": _enter, "__aexit__": _exit, "arun": _arun})
        with patch.dict(sys.modules, {"crawl4ai": types.SimpleNamespace(AsyncWebCrawler=fake)}):
            result = ext._crawl4ai_local("https://public.example.org/catalogue")
        self.assertIn("community facilities", result["markdown"].casefold())


class SnapshotAndEventTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name) / "data"
        store.start_run(self.root, "run-one", "generic sector", "target discovery", "unknown")

    def test_only_post_attempt_equal_snapshot_warns_of_a_stall(self):
        a = workflow.next_step(self.root, "run-one", limit=5)
        self.assertEqual("READ_ONLY_OR_INITIAL", a["progress_state"])
        read_again = workflow.next_step(self.root, "run-one",
                                        previous_snapshot_id=a["snapshot_id"])
        self.assertEqual("READ_ONLY_OR_INITIAL", read_again["progress_state"])
        stalled = workflow.next_step(self.root, "run-one",
                                     previous_snapshot_id=a["snapshot_id"],
                                     after_attempt=True)
        self.assertEqual("NO_NEW_PERSISTED_RECORD_AFTER_ATTEMPT", stalled["progress_state"])
        self.assertEqual("STOP_IDENTICAL_ACTION_AND_RECORD_HOLD_OR_SWITCH_TARGET",
                         stalled["repeat_guard"])
        store.put_candidate(self.root, "run-one", "source-1", "discovery", "source",
                            "source-1", {"title": "Example lead"})
        advanced = workflow.next_step(self.root, "run-one",
                                      previous_snapshot_id=a["snapshot_id"],
                                      after_attempt=True)
        self.assertEqual("CHANGED", advanced["progress_state"])

    def test_invalid_snapshot_or_attempt_flag_fails_closed(self):
        with self.assertRaisesRegex(ValueError, "INVALID_PREVIOUS_SNAPSHOT_ID"):
            workflow.next_step(self.root, "run-one", previous_snapshot_id="not-hex")
        with self.assertRaisesRegex(ValueError, "INVALID_ATTEMPT_PROGRESS_CHECK"):
            workflow.next_step(self.root, "run-one", after_attempt=True)

    def test_event_summary_keeps_last_twenty_but_counts_all_blockers(self):
        for i in range(27):
            journal.append_event(self.root, run_id="run-one",
                                 operation_id=f"attempt-{i}", stage="acquisition",
                                 kind="tool", status="blocked" if i in (0, 1, 2) else "succeeded",
                                 provider="crawl4ai_local",
                                 reason_code="source_challenge_page" if i in (0, 1, 2) else "")
        summary = journal.summarize_events(self.root, "run-one")
        self.assertEqual(27, summary["count"])
        self.assertEqual(20, len(summary["events"]))
        self.assertEqual(3, summary["blocker_counts"]["source_challenge_page"])
        self.assertEqual("full_run", summary["summary_scope"])


class BrowserPreflightHintTests(unittest.TestCase):
    def test_missing_then_present_browser_cache_is_a_hint_never_verification(self):
        from research_mcp import _local_browser_cache_hint
        with tempfile.TemporaryDirectory() as tmp:
            cache = Path(tmp) / "playwright-cache"
            cache.mkdir()
            with patch.dict(os.environ, {"PLAYWRIGHT_BROWSERS_PATH": str(cache)}):
                initial = _local_browser_cache_hint()
                self.assertEqual("CHROMIUM_CACHE_MISSING_OR_INCOMPLETE", initial["status"])
                self.assertFalse(initial["verified"])
                (cache / "chromium-1234").mkdir()
                later = _local_browser_cache_hint()
                self.assertEqual("CHROMIUM_CACHE_PRESENT_NOT_LAUNCH_VERIFIED", later["status"])
                self.assertFalse(later["verified"])


if __name__ == "__main__":
    unittest.main()
