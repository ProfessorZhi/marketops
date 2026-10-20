"""RC6: a local crawler failure falls through to TinyFish instead of holding.

The live Essex run found every home page HOLD: the local browser could not
start, the adapter's BLOCKED result lacked a closed-set code, the gateway raised
and the fallback chain read the exception as an unknown charge. Crawl4AI runs on
this machine and bills nobody, so its failure is technical.
"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import research_fallback as fallback  # noqa: E402
import research_lite_store as store  # noqa: E402
import research_runtime_transport as transport  # noqa: E402


class LocalCrawlFallbackTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name) / "data"
        store.start_run(self.root, "run", "halls", "Find halls", "v1")
        self.calls = []

    def managed(self, **args):
        self.calls.append(args["capability_id"])
        if args["capability_id"] == "crawl4ai.fetch_page":
            raise RuntimeError("browser could not start")
        material = "page-" + args["operation_id"]
        url = args["params"].get("url") or args["params"]["urls"][0]
        store.save_artifact(self.root, "run", material, "tinyfish", url,
                            b"<p>Alpha Hall bookings</p>", "rendered_html")
        return {"status": "OK", "reason_code": "", "gateway_invoked": True, "actual_usd": None,
                "payload": {"html": "<p>Alpha Hall bookings</p>"}, "artifact_ids": [material]}

    def test_local_crawl_exception_continues_to_tinyfish(self):
        result = fallback.research_step(
            self.root, run_id="run", operation_id="step-1", task_kind="known_public_url",
            url="https://alphahall.example/", managed_call=self.managed,
            admission=lambda _cap: {"allowed": True, "reason_code": ""})
        self.assertEqual(["crawl4ai.fetch_page", "tinyfish.fetch_content"], self.calls)
        self.assertEqual("SUCCESS_CANDIDATE", result["status"])
        self.assertFalse(result["attempts"][0]["cost_unknown"])

    def test_blocked_browser_carries_a_skippable_closed_set_code(self):
        outcome = transport._blocked_browser(("egress_session_unavailable:OSError",))
        self.assertEqual("CAPABILITY_NOT_AVAILABLE", outcome.caveats[0])
        self.assertIn("CAPABILITY_NOT_AVAILABLE", fallback.SKIPPABLE)


if __name__ == "__main__":
    unittest.main()
