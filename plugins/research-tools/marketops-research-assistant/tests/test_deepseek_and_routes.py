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


if __name__ == "__main__":
    unittest.main()
