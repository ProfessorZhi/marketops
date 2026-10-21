"""First-run wiring through the MCP surface: ask once, then never again.

A synthetic Run only: no network, no provider, no credentials, no customer data.
"""
from __future__ import annotations
import contextlib
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import research_config as cfg          # noqa: E402
import research_delivery as delivery   # noqa: E402
import research_event_journal as journal  # noqa: E402
import research_lite_store as store    # noqa: E402
import research_mcp as mcp             # noqa: E402


@contextlib.contextmanager
def isolated_env(tmp: str):
    """A clean process environment: own HOME/XDG, no data-root override."""
    env = {k: v for k, v in os.environ.items()
           if not k.startswith("MARKETOPS_")}
    env.update({"HOME": str(Path(tmp) / "home"), "XDG_CONFIG_HOME": str(Path(tmp) / "xdg")})
    Path(env["HOME"]).mkdir(parents=True, exist_ok=True)
    with patch.dict(os.environ, env, clear=True):
        yield env


def seed_source_run(run_id: str, root: Path):
    mcp.start(run_id, "synthetic-scope", "synthetic objective", "synthetic-rules")
    store.save_artifact(root, run_id, "src-1", "synthetic", "https://example.invalid",
                        b"synthetic material for the first-run test", "provider_extracted")
    mcp.propose(run_id, "src-1", "discovery", "source", "src-1",
                {"title": "synthetic", "url": "https://example.invalid", "provider": "synthetic"},
                ["src-1"])
    mcp.propose(run_id, "org-1", "entities", "organization", "org-1",
                {"name": "Synthetic Org"}, ["src-1"])
    mcp.propose(run_id, "venue-1", "entities", "venue", "venue-1",
                {"name": "Synthetic Venue", "organization_id": "org-1"}, ["src-1"])


class SetupInterviewTests(unittest.TestCase):
    def test_setup_status_is_readable_before_anything_is_configured(self):
        with tempfile.TemporaryDirectory() as tmp, isolated_env(tmp):
            status = mcp.setup_status()
            self.assertEqual("CONFIG_REQUIRED", status["status"])
            self.assertFalse(status["configured"])
            self.assertEqual(2, len(status["questions"]))
            self.assertIn("研究数据", status["questions"][0])
            self.assertIn("经验", status["questions"][1])
            self.assertNotIn("data_root", status)
            # Read-only: asking twice must not have created a config file.
            self.assertFalse(cfg.config_path().exists())

    def test_setup_configure_persists_both_paths_and_stops_asking(self):
        with tempfile.TemporaryDirectory() as tmp, isolated_env(tmp):
            data = Path(tmp) / "research-data"
            exp = Path(tmp) / "Research_Experience.md"
            result = mcp.setup_configure(str(data), str(exp))
            self.assertEqual("CONFIGURED", result["status"])
            after = mcp.setup_status()
            self.assertEqual("CONFIGURED", after["status"])
            self.assertEqual([], after["questions"])
            self.assertEqual(str(data.resolve()), after["data_root"])
            self.assertEqual(str(exp.resolve()), after["experience_file"])

    def test_changing_paths_needs_an_explicit_confirmation(self):
        with tempfile.TemporaryDirectory() as tmp, isolated_env(tmp):
            mcp.setup_configure(str(Path(tmp) / "d1"), str(Path(tmp) / "a.md"))
            with self.assertRaises(ValueError) as ctx:
                mcp.setup_configure(str(Path(tmp) / "d2"), str(Path(tmp) / "b.md"))
            self.assertIn("RECONFIGURE_REQUIRES_CONFIRMATION", str(ctx.exception))
            confirmed = mcp.setup_configure(str(Path(tmp) / "d2"), str(Path(tmp) / "b.md"),
                                            confirm=True)
            self.assertEqual("CONFIGURED", confirmed["status"])

    def test_a_run_before_configuration_reports_config_required_not_a_random_error(self):
        with tempfile.TemporaryDirectory() as tmp, isolated_env(tmp):
            with self.assertRaises(ValueError) as ctx:
                mcp.start("unconfigured-run", "s", "o", "r")
            self.assertIn("CONFIG_REQUIRED", str(ctx.exception))
            self.assertFalse((ROOT / "data").exists())

    def test_the_existing_data_root_env_still_runs_unconfigured(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = {"HOME": str(Path(tmp) / "home"), "XDG_CONFIG_HOME": str(Path(tmp) / "xdg"),
                   "PATH": os.environ.get("PATH", ""),
                   "MARKETOPS_RESEARCH_DATA_ROOT": str(Path(tmp) / "env-root")}
            Path(env["HOME"]).mkdir(parents=True, exist_ok=True)
            with patch.dict(os.environ, env, clear=True):
                result = mcp.start("env-run", "s", "o", "r")
                self.assertEqual("RUN_CREATED", result["status"])


class NoteToolTests(unittest.TestCase):
    def test_a_note_is_recorded_and_the_summary_is_refreshed_immediately(self):
        with tempfile.TemporaryDirectory() as tmp, isolated_env(tmp):
            data = Path(tmp) / "research-data"
            exp = Path(tmp) / "Research_Experience.md"
            mcp.setup_configure(str(data), str(exp))
            seed_source_run("run-a", data)
            result = mcp.note("run-a", "note-1", "acquisition", "contact_found_not_stored",
                              problem="官方页面有部门邮箱但候选未入库",
                              action_taken="改用 propose 逐条写入并附 artifact_id",
                              observed_result="第二次入库成功，证据可回读",
                              hypothesis="抽取成功不等于入库成功",
                              evidence_ref="src-1")
            self.assertEqual("RECORDED", result["note"]["result"])
            self.assertEqual("REFRESHED", result["summary"]["status"])
            text = exp.read_text(encoding="utf-8")
            self.assertIn("contact_found_not_stored", text)
            self.assertIn("官方页面有部门邮箱但候选未入库", text)

    def test_the_note_tool_refuses_unredacted_text(self):
        with tempfile.TemporaryDirectory() as tmp, isolated_env(tmp):
            data, exp = Path(tmp) / "d", Path(tmp) / "e.md"
            mcp.setup_configure(str(data), str(exp))
            seed_source_run("run-a", data)
            with self.assertRaises(ValueError) as ctx:
                mcp.note("run-a", "note-bad", "review", "other",
                         problem="write to bob@example.org",
                         action_taken="a", observed_result="o", evidence_ref="src-1")
            self.assertIn("NOTE_REJECTED", str(ctx.exception))
            self.assertNotIn("bob@example.org", str(ctx.exception))


class AutomaticRefreshTests(unittest.TestCase):
    def test_a_blocked_event_refreshes_the_summary(self):
        with tempfile.TemporaryDirectory() as tmp, isolated_env(tmp):
            data = Path(tmp) / "research-data"
            exp = Path(tmp) / "Research_Experience.md"
            mcp.setup_configure(str(data), str(exp))
            seed_source_run("run-a", data)
            self.assertFalse(exp.exists())
            journal.append_event(data, run_id="run-a", operation_id="op-bad",
                                 stage="discovery", kind="source", status="blocked",
                                 provider="synth", reason_code="source_blocked")
            self.assertTrue(exp.is_file())
            self.assertIn("source_blocked", exp.read_text(encoding="utf-8"))

    def test_a_successful_event_does_not_refresh(self):
        with tempfile.TemporaryDirectory() as tmp, isolated_env(tmp):
            data = Path(tmp) / "research-data"
            exp = Path(tmp) / "Research_Experience.md"
            mcp.setup_configure(str(data), str(exp))
            journal.append_event(data, run_id="run-a", operation_id="run-run-a",
                                 stage="intake", kind="run", status="succeeded")
            self.assertFalse(exp.exists())

    def test_the_export_receipt_reports_the_experience_file_and_refresh_status(self):
        with tempfile.TemporaryDirectory() as tmp, isolated_env(tmp):
            data = Path(tmp) / "research-data"
            exp = Path(tmp) / "Research_Experience.md"
            mcp.setup_configure(str(data), str(exp))
            seed_source_run("run-a", data)
            report = delivery.write_delivery(data, "run-a", "delivery-1")
            self.assertEqual(str(exp.resolve()), report["experience_file"])
            self.assertEqual("REFRESHED", report["experience_refresh"]["status"])
            self.assertTrue(exp.is_file())

    def test_a_failed_refresh_does_not_get_reported_as_success(self):
        with tempfile.TemporaryDirectory() as tmp, isolated_env(tmp):
            data = Path(tmp) / "research-data"
            exp = Path(tmp) / "Research_Experience.md"
            mcp.setup_configure(str(data), str(exp))
            seed_source_run("run-a", data)
            # A foreign file appears where the summary should go: fail-closed.
            exp.write_text("# someone else's notes\n", encoding="utf-8")
            report = delivery.write_delivery(data, "run-a", "delivery-1")
            self.assertTrue(Path(report["xlsx_path"]).is_file())     # the workbook is real
            self.assertEqual("FAILED", report["experience_refresh"]["status"])
            self.assertEqual("EXPERIENCE_SNAPSHOT_HEADER_INVALID",
                             report["experience_refresh"]["reason_code"])
            self.assertEqual("# someone else's notes\n", exp.read_text(encoding="utf-8"))

    def test_the_handoff_also_refreshes_the_summary(self):
        with tempfile.TemporaryDirectory() as tmp, isolated_env(tmp):
            data = Path(tmp) / "research-data"
            exp = Path(tmp) / "Research_Experience.md"
            mcp.setup_configure(str(data), str(exp))
            seed_source_run("run-a", data)
            result = mcp.handoff("run-a", "handoff-1")
            self.assertEqual("CANDIDATE_HANDOFF_NOT_BUSINESS_VERIFIED", result["status"])
            self.assertEqual(str(exp.resolve()), result["experience_file"])
            self.assertEqual("REFRESHED", result["experience_refresh"]["status"])

    def test_unconfigured_export_still_delivers_and_says_the_refresh_was_skipped(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = {"HOME": str(Path(tmp) / "home"), "XDG_CONFIG_HOME": str(Path(tmp) / "xdg"),
                   "PATH": os.environ.get("PATH", ""),
                   "MARKETOPS_RESEARCH_DATA_ROOT": str(Path(tmp) / "env-root")}
            Path(env["HOME"]).mkdir(parents=True, exist_ok=True)
            with patch.dict(os.environ, env, clear=True):
                seed_source_run("run-a", Path(env["MARKETOPS_RESEARCH_DATA_ROOT"]))
                report = delivery.write_delivery(Path(env["MARKETOPS_RESEARCH_DATA_ROOT"]),
                                                 "run-a", "delivery-1")
                self.assertTrue(Path(report["xlsx_path"]).is_file())
                self.assertEqual("FAILED", report["experience_refresh"]["status"])
                self.assertEqual("CONFIG_REQUIRED", report["experience_refresh"]["reason_code"])
                self.assertIsNone(report["experience_file"])


class SurfaceTests(unittest.TestCase):
    def test_the_setup_and_note_tools_are_registered(self):
        names = set(mcp.build_server()._tool_manager._tools)  # noqa: SLF001
        self.assertLessEqual({"marketops_setup_status", "marketops_setup_configure",
                              "marketops_note", "marketops_next",
                              "marketops_propose_batch", "marketops_material_excerpt",
                              "marketops_evidence_audit"}, names)
        self.assertEqual(set(mcp.PUBLIC_TOOLS), names)

    def test_setup_status_is_safe_for_an_agent_to_call_anytime(self):
        with tempfile.TemporaryDirectory() as tmp, isolated_env(tmp):
            first = json.dumps(mcp.setup_status(), ensure_ascii=False, sort_keys=True)
            second = json.dumps(mcp.setup_status(), ensure_ascii=False, sort_keys=True)
            self.assertEqual(first, second)
            self.assertNotIn(str(Path(tmp)), first)   # no local path leaked by default


if __name__ == "__main__":
    unittest.main()
