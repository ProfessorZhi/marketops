"""DeepSeek Search as a route of its own, and the multi-route search with its recall log.

No network: DeepSeek answers come from a fake transport.
"""
import io
import json
import os
import sys
import tempfile
import types
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import research_adapter_deepseek as deepseek  # noqa: E402


class Response(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


def request(**params):
    return types.SimpleNamespace(capability_id="deepseek.search", run_id="r", operation_id="o",
                                 account_ref="deepseek", params=params)


class Adapter(unittest.TestCase):
    def adapter(self, answer=None, error=None, key="k"):
        sent = []

        def transport(req, timeout):
            sent.append(json.loads(req.data))
            if error:
                raise error
            return Response(json.dumps(answer).encode())
        return deepseek.DeepSeekAdapter(api_key=key, transport=transport), sent

    def test_results_are_discovery_candidates_with_the_language_operator(self):
        adapter, sent = self.adapter({"results": [
            {"title": "Club", "url": "https://club.example.fr/bureau", "snippet": "Président : J. D.",
             "rerank_score": 1.2}, {"title": "no url"}]})
        result = adapter.execute(request(query="club de badminton Montreuil", language="fr", limit=20))
        self.assertEqual("OK", result.outcome)
        self.assertEqual(["https://club.example.fr/bureau"], [h["url"] for h in result.payload["results"]])
        self.assertEqual("club de badminton Montreuil lang:fr", sent[0]["query"])
        self.assertEqual(20, sent[0]["count"])
        self.assertFalse(result.payload["business_evidence"])

    def test_an_empty_answer_is_not_billed(self):
        adapter, _ = self.adapter({"results": []})
        result = adapter.execute(request(query="nothing here at all"))
        self.assertEqual(0, result.quota_units_used)
        self.assertFalse(result.payload["billable"])

    def test_refusals_and_failures(self):
        adapter, sent = self.adapter({"results": []}, key=None)
        self.assertEqual("BLOCKED", adapter.execute(request(query="anything")).outcome)
        adapter, sent = self.adapter({"results": []})
        self.assertEqual("BLOCKED", adapter.execute(request(query="x", mode="maps")).outcome)
        self.assertEqual("BLOCKED", adapter.execute(request(query="q" * 200)).outcome)
        self.assertEqual([], sent)
        broke = urllib.error.HTTPError(deepseek.ENDPOINT, 402, "Payment Required", {}, None)
        adapter, _ = self.adapter(error=broke)
        result = adapter.execute(request(query="club de judo"))
        self.assertEqual(("FAILED", "DEEPSEEK_NO_BALANCE"), (result.outcome, result.payload["reason"]))
        adapter, _ = self.adapter(error=TimeoutError())
        self.assertEqual("UNKNOWN", adapter.execute(request(query="club de judo")).outcome)

    def test_it_is_a_registered_conformant_adapter(self):
        import research_adapter_base as base
        import research_capability_registry as registry
        adapter = deepseek.DeepSeekAdapter(api_key="k", transport=lambda r, t: None)
        report = base.check_adapter_conformance(adapter, expected_provider="deepseek",
                                                registry=registry.DEFAULT_REGISTRY)
        self.assertTrue(report.conformant, report.problems)


class MultiRoute(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        env = patch.dict(os.environ, {"MARKETOPS_RESEARCH_DATA_ROOT": str(self.root)})
        env.start()
        self.addCleanup(env.stop)

    def test_every_route_runs_and_a_broke_one_drops_out(self):
        import research_mcp as mcp
        import research_search_routes as routes

        def fake(provider, query, country, language, run_id, operation_id, mode="web", limit=10):
            if provider == "serpapi":
                raise ValueError("BUDGET_PROVIDER_CEILING: red line")
            urls = {"serper": ["https://a.fr/", "https://b.fr/"], "deepseek": ["https://b.fr/", "https://c.fr/x"],
                    "tinyfish": []}[provider]
            return {"results": [{"url": u, "title": u} for u in urls]}
        with patch.object(mcp, "search", side_effect=fake), \
                patch.object(mcp, "_search_routes", return_value=("serper", "deepseek", "serpapi", "tinyfish")):
            out = mcp._search_all("clubs", "fr", "fr", "run-1", "op-1", 10)
        self.assertEqual({"https://a.fr/", "https://b.fr/", "https://c.fr/x"}, {h["url"] for h in out["results"]})
        self.assertIn("BUDGET_PROVIDER_CEILING", out["by_provider"]["serpapi"])
        report = routes.report(self.root, "run-1", ab_hosts={"c.fr", "a.fr"})
        self.assertEqual(1, report["routes"]["deepseek"]["unique_pages"])
        self.assertEqual(1, report["routes"]["deepseek"]["unique_ab_sites"])
        self.assertEqual({"NO_MONEY_OR_RED_LINE": 1}, report["routes"]["serpapi"]["calls"])
        self.assertEqual(2, report["ab_sites_found_by_search"])

    def test_routes_follow_the_project_setting(self):
        import research_mcp as mcp
        with patch("research_runtime_setup.config", return_value={
                "providers": ["serper", "deepseek", "crawl4ai"], "search_routes": None}):
            self.assertEqual(("serper", "deepseek"), mcp._search_routes())


class Fetch(unittest.TestCase):
    def test_markdown_is_unescaped_and_a_missing_page_costs_nothing(self):
        sent = []

        def transport(req, timeout):
            sent.append(req.full_url)
            return Response(json.dumps({"title": "Bureau", "content":
                "Président \\: Jean DUPONT \\- jean\\.dupont@club\\.fr"}).encode())
        adapter = deepseek.DeepSeekAdapter(api_key="k", transport=transport)
        req = types.SimpleNamespace(capability_id="deepseek.fetch_page", run_id="r", operation_id="o",
                                    account_ref="deepseek", params={"url": "https://club.fr/bureau"})
        result = adapter.execute(req)
        self.assertEqual("OK", result.outcome)
        self.assertIn("jean.dupont@club.fr", result.payload["markdown"])
        self.assertEqual([deepseek.FETCH_ENDPOINT], sent)

        def missing(req, timeout):
            raise urllib.error.HTTPError(deepseek.FETCH_ENDPOINT, 404, "Not Found", {}, None)
        result = deepseek.DeepSeekAdapter(api_key="k", transport=missing).execute(req)
        self.assertEqual(("FAILED", "DEEPSEEK_FETCH_NO_CONTENT"), (result.outcome, result.payload["reason"]))


class Readers(unittest.TestCase):
    def setUp(self):
        import research_lite_store as store
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.store = store
        store.start_run(self.root, "run-r", "synthetic", "clubs", "rules")
        self.pages = {"crawl4ai.fetch_page": None,  # this machine cannot reach the site
                      "tinyfish.fetch_content": "<p>Contact: info@club.fr</p>",
                      "deepseek.fetch_page": "<p>info@club.fr, president@club.fr</p>"}

    def step(self, url, op, routes):
        reader = routes[0]
        body = self.pages[reader]
        if body is None:
            return {"status": "HOLD", "reason_code": "LOCAL_CRAWL_FAILED", "replayed": False,
                    "attempts": [{"capability_id": reader, "status": "FAILED",
                                  "reason_code": "LOCAL_CRAWL_FAILED"}]}
        artifact = "a-" + op
        self.store.save_artifact(self.root, "run-r", artifact, reader.split(".")[0], url,
                                 body.encode(), "rendered_html")
        return {"status": "SUCCESS_CANDIDATE", "artifact_ids": [artifact],
                "primary_artifact_ids": [artifact], "selected_capability": reader,
                "attempts": [{"capability_id": reader, "status": "OK"}]}

    def test_probe_reads_with_all_and_keeps_the_richest_then_orders_by_yield(self):
        import research_fetch_routes as readers
        receipt = readers.fetch(self.root, "run-r", "https://club.fr/", "p1", self.step)
        self.assertEqual("deepseek.fetch_page", receipt["selected_capability"])
        self.assertEqual(2, receipt["reader_probe"]["deepseek.fetch_page"]["emails"])
        report = readers.report(self.root, "run-r")
        self.assertEqual({"LOCAL_CRAWL_FAILED": 1}, report["readers"]["crawl4ai.fetch_page"]["failed"])
        self.assertEqual(("deepseek.fetch_page", "tinyfish.fetch_content", "crawl4ai.fetch_page"),
                         readers.order(self.root, "run-r"))
        # After the probe window the chain gets the measured order.
        seen = []
        with patch.object(readers, "PROBE_PAGES", 1):
            readers.fetch(self.root, "run-r", "https://other.fr/", "p2",
                          lambda url, op, routes: seen.append(routes) or self.step(url, op, routes))
        self.assertEqual([("deepseek.fetch_page", "tinyfish.fetch_content", "crawl4ai.fetch_page")], seen)


if __name__ == "__main__":
    unittest.main()
