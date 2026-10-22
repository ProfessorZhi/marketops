"""Generic fixes from the Paris Run2 Codex session (0.5.11-rc3, 2 h 39 min).

* Export URLs failed in the browser (an attachment is a download): data URLs are
  read over plain HTTP, and the importer accepts the raw JSON;
* a saved batch result that raised on every finalization blocked delivery;
* 370 stale leases were waited out;
* Serper's balance can be read; budgets are red lines the user can move.
"""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import research_bulk as bulk  # noqa: E402
import research_lite_store as store  # noqa: E402
import research_pool as pool  # noqa: E402


class DataUrls(unittest.TestCase):
    def test_raw_json_saved_as_rendered_html_still_imports(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            store.start_run(root, "run-a", "Any", "Any", "test")
            store.save_artifact(root, "run-a", "export", "crawl4ai",
                                "https://portal.example/api/exports/json",
                                json.dumps([{"id": 1, "name": "Hall A"}]).encode(), "rendered_html")
            out = bulk.import_saved(root, run_id="run-a", artifact_id="export", pool_id="p",
                                    source_id="s", mapping={"name": "name", "source_key": "id"})
            self.assertEqual(1, out["imported"])

    def test_the_crawler_reads_a_data_url_without_the_browser(self):
        import research_adapter_crawl4ai as crawl
        calls = []

        class Response:
            status, url, body, bytes_read = 200, "https://portal.example/api/x.json", '[{"a": 1}]', 10
            headers = {"Content-Type": "application/json"}

        def fake_get(binding, url, **kwargs):
            calls.append((url, kwargs.get("accept", "")))
            return Response()

        adapter = crawl.Crawl4AIAdapter.__new__(crawl.Crawl4AIAdapter)
        adapter._material_writer = lambda run, op, url, body: ("primary-1",)
        adapter._clock = lambda: 0.0
        budget = crawl.CrawlResourceBudget(max_pages=5, max_depth=1, max_domains=1,
                                           max_bytes=8 * 1024 * 1024, max_seconds=30,
                                           max_browser_seconds=30, max_artifacts=5)
        meter = crawl.BudgetMeter(budget=budget)
        artifacts = type("A", (), {"max_bytes_per_artifact": 8 * 1024 * 1024})()
        decision = type("D", (), {"method": "CONNECT", "target": "portal.example", "port": 443})()
        with patch.object(crawl, "auxiliary_http_get", side_effect=fake_get):
            out = adapter._execute_data("crawl4ai.fetch_page", "run-a", "op", Response.url,
                                        object(), budget, meter, artifacts, decision)
        self.assertEqual("OK", out.outcome)
        self.assertEqual(("primary-1",), tuple(out.artifacts))
        self.assertIn("application/json", calls[0][1])


class StuckBatchWork(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        pool.import_targets(self.root, pool_id="p", source_id="s", source_url="", provider="local",
                            listed_count=3, targets=[{"name": f"Org {n}", "address": f"Town {n}"}
                                                     for n in range(3)])

    def test_a_result_that_never_finalizes_becomes_hold(self):
        leased = pool.claim_batch(self.root, pool_id="p", worker_id="w", run_id="run-a", count=1)
        target = leased["targets"][0]
        pool.save_batch_result(self.root, pool_id="p", worker_id="w", run_id="run-a",
                               target_key=target["target_key"], lease_attempt=target["attempts"],
                               result={"outcome": "done"})
        for attempt in range(pool.MAX_SETTLE_FAILURES):
            pool.note_settle_failure(self.root, pool_id="p", target_key=target["target_key"],
                                     worker_id="w", lease_attempt=target["attempts"],
                                     code="ValueError:SUPERSEDES_UNKNOWN_RECORD")
        self.assertEqual([], pool.ready_batch_results(self.root, pool_id="p", worker_id="", run_id=""))
        held = pool.batch_target(self.root, pool_id="p", target_key=target["target_key"])
        self.assertEqual("hold", held["status"])
        self.assertIn("FINALIZATION_FAILED", held["reason"])

    def test_a_workers_leases_are_released_at_once(self):
        pool.claim(self.root, pool_id="p", worker_id="w", count=3)
        out = pool.release(self.root, pool_id="p", worker_id="w")
        self.assertEqual(3, out["released"])
        self.assertEqual(3, pool.status(self.root, pool_id="p")["by_status"]["pending"])


class Balances(unittest.TestCase):
    def test_serper_balance_is_read_from_its_account_api(self):
        import research_balances as balances

        class Answer:
            status_code = 200

            @staticmethod
            def json():
                return {"balance": 612, "rateLimit": 5}

        class Client:
            def __init__(self, **kwargs):
                pass

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

            def get(self, url, headers):
                assert url == balances.SERPER_ACCOUNT and headers["X-API-KEY"] == "k"
                return Answer()

        class Session:
            proxy = type("P", (), {"url": "http://127.0.0.1:1"})()

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

        import httpx2
        import research_egress
        with patch.dict(os.environ, {"SERPER_API_KEY": "k"}), \
                patch.object(httpx2, "Client", Client), \
                patch.object(research_egress, "open_session", return_value=Session()):
            reading = balances.read_serper(Path("."))
        self.assertEqual({"status": "READ", "unit": "credit", "left": 612}, {
            k: reading[k] for k in ("status", "unit", "left")})
        self.assertIn("serper", balances.AUTOMATIC)


if __name__ == "__main__":
    unittest.main()
