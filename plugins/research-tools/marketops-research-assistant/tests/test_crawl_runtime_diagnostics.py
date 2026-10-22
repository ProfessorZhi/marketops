"""Retain a safe failure diagnosis without learning a false broken-host route."""
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import research_adapter_crawl4ai as crawl
import research_walls as walls


class RuntimeDiagnostics(unittest.TestCase):
    def test_runtime_conditions_are_distinct_without_exposing_exception_text(self):
        cases = ((RuntimeError("Event loop is closed; private-token"), "RuntimeError:EVENT_LOOP_CLOSED"),
                 (RuntimeError("asyncio.run() cannot be called from a running event loop; private-token"),
                  "RuntimeError:EVENT_LOOP_RUNNING"),
                 (RuntimeError("database is locked: private-token"), "RuntimeError:DATABASE_LOCKED"),
                 (TimeoutError("private-token"), "TimeoutError:RUNTIME_TIMEOUT"),
                 (ValueError("private-token https://example.org"), "ValueError:SDK_EXCEPTION"))
        for exc, expected in cases:
            self.assertEqual(expected, crawl.runtime_failure_detail(exc))
            self.assertNotIn("private-token", crawl.runtime_failure_detail(exc))

    def test_os_diagnosis_retains_codes_without_message_or_filename(self):
        exc = OSError(22, "private-token", "private-filename")
        self.assertEqual("OSError:OS_ERROR_ERRNO_22", crawl.runtime_failure_detail(exc))

    def test_runtime_or_old_cache_failure_does_not_permanently_skip_the_local_reader(self):
        route = (walls.LOCAL_RUNG, walls.CLOUD_RUNG)
        for code in ("LOCAL_CRAWL_RUNTIME_FAILED", "CRAWL_CACHE_RESTART_REQUIRED", "LOCAL_CRAWL_FAILED"):
            with tempfile.TemporaryDirectory() as folder:
                root = Path(folder)
                walls.record_chain(root, run_id="fixture", task_kind="known_public_url",
                    url="https://example.org/page", query="", selected=walls.CLOUD_RUNG,
                    attempts=[{"capability_id": walls.LOCAL_RUNG, "status": "FAILED", "reason_code": code, "ordinal": 1}])
                self.assertEqual(route, walls.preferred_route(root, "https://example.org/other", route))

    def test_confirmed_empty_site_material_can_still_teach_a_different_reader(self):
        route = (walls.LOCAL_RUNG, walls.CLOUD_RUNG)
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            walls.record_chain(root, run_id="fixture", task_kind="known_public_url",
                url="https://example.org/page", query="", selected=walls.CLOUD_RUNG,
                attempts=[{"capability_id": walls.LOCAL_RUNG, "status": "FAILED",
                           "reason_code": "SOURCE_EMPTY_EXTRACT", "ordinal": 1}])
            self.assertEqual((walls.CLOUD_RUNG,), walls.preferred_route(root, "https://example.org/other", route))


if __name__ == "__main__":
    unittest.main()
