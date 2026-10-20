"""RC6: research records its own walls, remembers hosts and reports lessons.

No Agent call is needed: every finished fallback chain writes wall events. A
host where the local crawler failed and TinyFish fetch succeeded starts at
TinyFish next time, from any window on the same data root.
"""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import research_fallback as fallback  # noqa: E402
import research_lite_store as store  # noqa: E402
import research_walls as walls  # noqa: E402


class WallLearningTests(unittest.TestCase):
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
        url = args["params"].get("url") or args["params"]["urls"][0]
        material = "page-" + args["operation_id"]
        store.save_artifact(self.root, "run", material, "tinyfish", url,
                            b"<p>Alpha Hall bookings</p>", "rendered_html")
        return {"status": "OK", "reason_code": "", "gateway_invoked": True, "actual_usd": None,
                "payload": {"html": "<p>Alpha Hall bookings</p>"}, "artifact_ids": [material]}

    def step(self, op, url):
        return fallback.research_step(
            self.root, run_id="run", operation_id=op, task_kind="known_public_url", url=url,
            managed_call=self.managed, admission=lambda _cap: {"allowed": True, "reason_code": ""})

    def test_host_memory_skips_the_failing_local_rung(self):
        self.step("s1", "https://www.alphahall.example/")
        self.assertEqual(["crawl4ai.fetch_page", "tinyfish.fetch_content"], self.calls)
        self.calls.clear()
        second = self.step("s2", "https://alphahall.example/contact")
        self.assertEqual(["tinyfish.fetch_content"], self.calls)
        self.assertEqual("DOMAIN_MEMORY_STARTED_AT_tinyfish.fetch_content", second["route_note"])
        self.calls.clear()
        self.step("s3", "https://otherhall.example/")
        self.assertEqual("crawl4ai.fetch_page", self.calls[0])

    def test_a_local_runtime_that_cannot_start_teaches_nothing_about_the_host(self):
        def unavailable(**args):
            if args["capability_id"] == "crawl4ai.fetch_page":
                self.calls.append(args["capability_id"])
                return {"status": "BLOCKED", "reason_code": "CAPABILITY_NOT_AVAILABLE",
                        "primary_reason_code": "LOCAL_CRAWL_NOT_SAFE_TO_RUN",
                        "gateway_invoked": True, "actual_usd": None, "artifact_ids": []}
            return self.managed(**args)
        for op in ("u1", "u2"):
            fallback.research_step(
                self.root, run_id="run", operation_id=op, task_kind="known_public_url",
                url="https://betahall.example/", managed_call=unavailable,
                admission=lambda _cap: {"allowed": True, "reason_code": ""})
        self.assertEqual(["crawl4ai.fetch_page", "tinyfish.fetch_content"] * 2, self.calls)

    def test_report_exports_walls_and_rescues(self):
        for i in range(6):
            self.step(f"r{i}", f"https://hall{i}.example/")
        result = walls.report(self.root, out_dir=self.root / "lessons")
        self.assertEqual(6, result["rescue"]["crawl4ai.fetch_page"]["rescued"])
        self.assertTrue((self.root / "lessons" / "LESSONS.md").is_file())
        self.assertTrue((self.root / "lessons" / "wall_events_summary.csv").is_file())
        self.assertTrue(any("rescues" in s for s in result["suggestions"]))


if __name__ == "__main__":
    unittest.main()
