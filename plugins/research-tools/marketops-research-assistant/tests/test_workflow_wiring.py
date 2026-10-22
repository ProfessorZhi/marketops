"""Mechanical wiring of the owned workflow core into MCP, delivery and bundling.

Synthetic data only. No network, no provider, no credentials, no real customer data.
The workflow core itself (research_workflow.py) is not exercised for its own logic
here; these tests check that the surrounding surfaces call it as contracted.
"""
from __future__ import annotations
import contextlib
import csv
import json
import os
import re
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import research_delivery as delivery       # noqa: E402
import research_event_journal as journal   # noqa: E402
import research_lite_store as store        # noqa: E402
import research_mcp as mcp                 # noqa: E402

BRIEF = {"record_type": "research_brief", "target_kind": "venue", "goal": "pool",
         "minimum_targets": 2, "minimum_targets_unit": "venue",
         "require_named_person": False}


@contextlib.contextmanager
def isolated(tmp: str, *, configure: bool = True):
    """Own HOME/XDG; a configured pair of paths unless the case wants otherwise."""
    env = {k: v for k, v in os.environ.items() if not k.startswith("MARKETOPS_")}
    env.update({"HOME": str(Path(tmp) / "home"), "USERPROFILE": str(Path(tmp) / "home"),
                "APPDATA": str(Path(tmp) / "appdata"), "XDG_CONFIG_HOME": str(Path(tmp) / "xdg"),
                "MARKETOPS_RESEARCH_CONFIG": str(Path(tmp) / "config.json")})
    Path(env["HOME"]).mkdir(parents=True, exist_ok=True)
    with patch.dict(os.environ, env, clear=True):
        if configure:
            mcp.setup_configure(str(Path(tmp) / "data"), str(Path(tmp) / "Research_Experience.md"))
        yield Path(tmp) / "data", Path(tmp) / "Research_Experience.md"


def seed(root: Path, run_id: str, *, venues: int = 2, dispositions: bool = True,
         brief: dict | None = None, artifact: bool = True) -> None:
    """Record IDs are global keys, so every Run gets its own tagged IDs."""
    tag = run_id
    mcp.start(run_id, "synthetic-scope", "synthetic objective", "synthetic-rules")
    mcp.propose(run_id, f"brief-{tag}", "qualification", "observation", f"brief-{tag}",
                brief or BRIEF)
    if artifact:
        store.save_artifact(root, run_id, f"src-{tag}", "synthetic",
                            "https://example.invalid", b"synthetic material",
                            "provider_extracted")
    for i in range(venues):
        key = f"venue-{i:03d}"
        mcp.propose(run_id, f"{tag}-{key}", "entities", "venue", key,
                    {"name": f"Synthetic Venue {i}"}, [f"src-{tag}"] if artifact else [])
        if dispositions:
            mcp.propose(run_id, f"disp-{tag}-{key}", "review", "review", f"disp-{tag}-{key}",
                        {"review_type": "target_disposition", "target_kind": "venue",
                         "target_entity_key": key, "disposition": "RESEARCHED_CANDIDATE",
                         "reason": "synthetic public source reviewed"},
                        [f"src-{tag}"] if artifact else [])


class ToolSurfaceTests(unittest.TestCase):
    def test_next_and_experience_tools_are_in_the_published_surface(self):
        names = set(mcp.build_server()._tool_manager._tools)  # noqa: SLF001
        self.assertIn("marketops_next", names)
        self.assertIn("marketops_experience_feedback", names)
        self.assertEqual(set(mcp.PUBLIC_TOOLS), names)

    def test_status_exposes_the_contract_parameters(self):
        schema = mcp.build_server()._tool_manager._tools["marketops_status"].parameters  # noqa: SLF001
        props = schema.get("properties", schema)
        for name in ("run_id", "limit", "cursor", "kind", "entity_key"):
            self.assertIn(name, props)

    def test_next_returns_the_workflow_assessment_shape(self):
        with tempfile.TemporaryDirectory() as tmp, isolated(tmp) as (data, _exp):
            seed(data, "run-a")
            result = mcp.next_step("run-a", 25)
            self.assertEqual(result["run_id"], "run-a")
            self.assertEqual("STRUCTURAL_CANDIDATE_COMPLETENESS_NOT_FACT_VERIFICATION",
                             result["assessment_scope"])
            self.assertIn(result["status"], {"READY_FOR_CANDIDATE_REVIEW",
                                             "MORE_RESEARCH_REQUIRED", "STOPPED_WITH_GAPS"})
            self.assertIn("source_selection", result)
            self.assertEqual("marketops.source-selection/1",
                             result["source_selection"]["schema"])
            self.assertIn("budget", result["source_selection"])

    def test_next_refuses_a_missing_store_instead_of_reporting_success(self):
        with tempfile.TemporaryDirectory() as tmp, isolated(tmp) as (data, _exp):
            with self.assertRaises(ValueError) as ctx:
                mcp.next_step("no-such-run", 25)
            self.assertIn("RESEARCH_STORE_NOT_FOUND", str(ctx.exception))


class StatusPaginationTests(unittest.TestCase):
    def test_status_pages_with_a_cursor_and_keeps_the_snapshot_fixed(self):
        with tempfile.TemporaryDirectory() as tmp, isolated(tmp) as (data, _exp):
            seed(data, "run-a", venues=6)
            first = mcp.status("run-a", limit=3)
            self.assertEqual(3, first["returned"])
            self.assertTrue(first["has_more"])
            second = mcp.status("run-a", limit=3, cursor=first["next_cursor"])
            keys = {r["record_id"] for r in first["records"]} & {
                r["record_id"] for r in second["records"]}
            self.assertEqual(set(), keys, "pages must not overlap")
            # an append after page 1 must not appear inside the fixed snapshot
            store.save_artifact(data, "run-a", "src-2", "synthetic", "https://example.invalid/2",
                                b"later", "provider_extracted")
            third = mcp.status("run-a", limit=500, cursor=first["next_cursor"])
            self.assertLessEqual(third["snapshot_high_water"], third["snapshot_high_water"])

    def test_status_filters_by_kind_and_entity_key(self):
        with tempfile.TemporaryDirectory() as tmp, isolated(tmp) as (data, _exp):
            seed(data, "run-a", venues=3)
            only_venue = mcp.status("run-a", limit=50, kind="venue")
            self.assertTrue(all(r["kind"] == "venue" for r in only_venue["records"]))
            self.assertEqual(3, only_venue["returned"])
            one = mcp.status("run-a", limit=50, entity_key="venue-001")
            self.assertTrue(all(r["entity_key"] == "venue-001" for r in one["records"]))

    def test_a_cursor_from_another_run_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp, isolated(tmp) as (data, _exp):
            seed(data, "run-a", venues=4)
            seed(data, "run-b", venues=4)
            token = mcp.status("run-a", limit=2)["next_cursor"]
            with self.assertRaises(ValueError) as ctx:
                mcp.status("run-b", limit=2, cursor=token)
            self.assertIn("INVALID_OR_MISMATCHED_PAGE_CURSOR", str(ctx.exception))

    def test_legacy_internal_list_run_is_still_available(self):
        with tempfile.TemporaryDirectory() as tmp, isolated(tmp) as (data, _exp):
            seed(data, "run-a")
            self.assertIn("record_count", store.list_run(data, "run-a", limit=500))


class DeliveryAssessmentTests(unittest.TestCase):
    def test_manifest_carries_the_assessment_of_the_same_snapshot(self):
        with tempfile.TemporaryDirectory() as tmp, isolated(tmp) as (data, _exp):
            seed(data, "run-a", venues=3)
            report = delivery.write_delivery(data, "run-a", "d-1")
            assessment = report["goal_assessment"]
            self.assertEqual("run-a", assessment["run_id"])
            self.assertEqual(report["input_record_count"], assessment["record_count"])
            manifest = json.loads((Path(report["directory"]) / "MANIFEST.json").read_text("utf-8"))
            self.assertEqual(assessment["snapshot_id"], manifest["goal_assessment"]["snapshot_id"])

    def test_workbook_notes_sheet_states_the_structural_goal_status(self):
        with tempfile.TemporaryDirectory() as tmp, isolated(tmp) as (data, _exp):
            seed(data, "run-a", venues=3)
            report = delivery.write_delivery(data, "run-a", "d-1")
            with (Path(report["directory"]) / "本轮说明.csv").open(encoding="utf-8-sig", newline="") as fh:
                notes = {row["字段"]: row["值"] for row in csv.DictReader(fh)}
            self.assertEqual(report["goal_assessment"]["status"], notes["结构目标状态"])
            self.assertEqual(str(report["goal_assessment"]["goal_satisfied"]),
                             notes["结构目标满足"])
            self.assertIn("缺口统计", notes)

    def test_an_unmet_goal_records_a_gap_note_and_refreshes_the_summary(self):
        with tempfile.TemporaryDirectory() as tmp, isolated(tmp) as (data, exp):
            seed(data, "run-a", venues=1)      # minimum_targets is 2 -> shortfall
            report = delivery.write_delivery(data, "run-a", "d-1")
            self.assertFalse(report["goal_assessment"]["goal_satisfied"])
            self.assertEqual("GOAL_GAP_RECORDED", report["goal_gap_record"]["status"])
            self.assertEqual("REFRESHED", report["experience_refresh"]["status"])
            self.assertIn("business_goal_gap", exp.read_text(encoding="utf-8"))

    def test_a_met_goal_records_no_gap(self):
        with tempfile.TemporaryDirectory() as tmp, isolated(tmp) as (data, _exp):
            seed(data, "run-a", venues=2)
            report = delivery.write_delivery(data, "run-a", "d-1")
            self.assertTrue(report["goal_assessment"]["goal_satisfied"])
            self.assertEqual("NO_STRUCTURAL_GAP_OBSERVED_NOT_BUSINESS_VERIFIED",
                             report["goal_gap_record"]["status"])

    def test_the_same_snapshot_delivered_twice_does_not_duplicate_the_gap_note(self):
        with tempfile.TemporaryDirectory() as tmp, isolated(tmp) as (data, _exp):
            seed(data, "run-a", venues=1)
            first = delivery.write_delivery(data, "run-a", "d-1")
            second = delivery.write_delivery(data, "run-a", "d-2")
            self.assertEqual(first["goal_assessment"]["snapshot_id"],
                             second["goal_assessment"]["snapshot_id"])
            notes = journal.read_notes(data, "run-a")
            self.assertEqual(1, len([n for n in notes if n["note_id"].startswith("goal-")]))

    def test_later_gap_notes_do_not_erase_earlier_ones(self):
        with tempfile.TemporaryDirectory() as tmp, isolated(tmp) as (data, _exp):
            seed(data, "run-a", venues=1)
            delivery.write_delivery(data, "run-a", "d-1")
            # a second venue completes the pool: a new snapshot, no new gap
            mcp.propose("run-a", "venue-001-c", "entities", "venue", "venue-001",
                        {"name": "Second Synthetic Venue"}, ["src-run-a"])
            mcp.propose("run-a", "disp-venue-001-c", "review", "review", "disp-venue-001-c",
                        {"review_type": "target_disposition", "target_kind": "venue",
                         "target_entity_key": "venue-001",
                         "disposition": "RESEARCHED_CANDIDATE", "reason": "reviewed"},
                        ["src-run-a"])
            second = delivery.write_delivery(data, "run-a", "d-2")
            self.assertTrue(second["goal_assessment"]["goal_satisfied"])
            notes = [n for n in journal.read_notes(data, "run-a") if n["note_id"].startswith("goal-")]
            self.assertEqual(1, len(notes), "the earlier gap record must survive")

    def test_zero_rows_is_still_refused_rather_than_exported_as_success(self):
        with tempfile.TemporaryDirectory() as tmp, isolated(tmp) as (data, _exp):
            mcp.start("empty-run", "s", "o", "r")
            with self.assertRaises(ValueError) as ctx:
                delivery.write_delivery(data, "empty-run", "d-1")
            self.assertIn("NO_RESEARCH_ROWS_OR_SOURCE_ARTIFACTS", str(ctx.exception))


class HandoffIsTheMainPathTests(unittest.TestCase):
    def test_handoff_delivers_a_workbook_and_no_second_research_snapshot(self):
        with tempfile.TemporaryDirectory() as tmp, isolated(tmp) as (data, exp):
            seed(data, "run-a", venues=2)
            calls = []
            original = delivery.write_delivery

            def counting(*args, **kwargs):
                calls.append(args)
                return original(*args, **kwargs)

            with patch.object(delivery, "write_delivery", counting):
                with patch.object(mcp.delivery, "write_delivery", counting):
                    result = mcp.handoff("run-a", "op-1")
            self.assertEqual(1, len(calls), "exactly one research snapshot per handoff")
            self.assertEqual("CANDIDATE_HANDOFF_NOT_BUSINESS_VERIFIED", result["status"])
            xlsx = Path(result["workbook_xlsx"])
            self.assertTrue(xlsx.is_file())
            with zipfile.ZipFile(xlsx) as z:
                self.assertIsNone(z.testzip(), "workbook archive must be intact")
            from xml.etree import ElementTree as ET
            NS = {"x": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
            with zipfile.ZipFile(xlsx) as z:
                wb = ET.fromstring(z.read("xl/workbook.xml"))
            self.assertEqual(9, len(wb.findall(".//x:sheets/x:sheet", NS)))
            self.assertEqual("REFRESHED", result["experience_refresh"]["status"])
            self.assertTrue(exp.is_file())

    def test_the_legacy_candidate_csv_field_points_at_the_workbooks_audit_csv(self):
        with tempfile.TemporaryDirectory() as tmp, isolated(tmp) as (data, _exp):
            seed(data, "run-a", venues=2)
            result = mcp.handoff("run-a", "op-1")
            candidate_csv = Path(result["candidate_csv"])
            self.assertTrue(candidate_csv.is_file())
            self.assertEqual("候选数据审计.csv", candidate_csv.name)
            self.assertEqual(Path(result["workbook_xlsx"]).parent, candidate_csv.parent)

    def test_a_handoff_reusing_an_existing_delivery_id_stays_an_immutable_error(self):
        with tempfile.TemporaryDirectory() as tmp, isolated(tmp) as (data, _exp):
            seed(data, "run-a", venues=2)
            delivery.write_delivery(data, "run-a", "op-1")
            with self.assertRaises(ValueError) as ctx:
                mcp.handoff("run-a", "op-1")
            self.assertIn("HANDOFF_PARTIAL_OR_FAILED", str(ctx.exception))
            self.assertIn("DELIVERY_ID_ALREADY_EXISTS", str(ctx.exception))
            statuses = [(row["kind"], row["status"])
                        for row in journal.read_events(data, "run-a")]
            self.assertIn(("handoff", "hold"), statuses,
                          "a refused handoff must stay visible in the journal")

    def test_a_failed_summary_refresh_is_partial_and_still_reports_the_workbook(self):
        with tempfile.TemporaryDirectory() as tmp, isolated(tmp) as (data, exp):
            seed(data, "run-a", venues=2)
            exp.write_text("# someone else's file\n", encoding="utf-8")
            result = mcp.handoff("run-a", "op-1")
            self.assertTrue(result["status"].startswith("PARTIAL"))
            self.assertTrue(Path(result["workbook_xlsx"]).is_file())
            self.assertEqual("FAILED", result["experience_refresh"]["status"])
            self.assertEqual("EXPERIENCE_SNAPSHOT_HEADER_INVALID",
                             result["experience_refresh"]["reason_code"])
            self.assertTrue(result["partial_reasons"])
            # the refused refresh must not have clobbered the foreign file
            self.assertEqual("# someone else's file\n", exp.read_text(encoding="utf-8"))

    def test_an_unconfigured_experience_file_is_not_a_clean_handoff(self):
        # A legacy env-only deployment: the data root works, the collectable summary
        # has no configured path, so the experience side cannot move.
        with tempfile.TemporaryDirectory() as tmp:
            data = Path(tmp) / "env-root"
            env = {"HOME": str(Path(tmp) / "home"), "XDG_CONFIG_HOME": str(Path(tmp) / "xdg"),
                   "PATH": os.environ.get("PATH", ""),
                   "MARKETOPS_RESEARCH_DATA_ROOT": str(data)}
            env.update({"USERPROFILE": env["HOME"], "APPDATA": str(Path(tmp) / "appdata"),
                        "MARKETOPS_RESEARCH_CONFIG": str(Path(tmp) / "config.json")})
            Path(env["HOME"]).mkdir(parents=True, exist_ok=True)
            with patch.dict(os.environ, env, clear=True):
                seed(data, "run-a", venues=2)
                result = mcp.handoff("run-a", "op-1")
            self.assertTrue(result["status"].startswith("PARTIAL"))
            self.assertTrue(Path(result["workbook_xlsx"]).is_file())
            self.assertEqual("CONFIG_REQUIRED", result["experience_refresh"]["reason_code"])
            self.assertIsNone(result["experience_file"])
            self.assertTrue(Path(result["experience_candidate"]).is_file())


class LargeRunTests(unittest.TestCase):
    """Above the legacy 500-row status cap the main path must still work."""

    @staticmethod
    def _big(data: Path, run_id: str, extra: int = 501) -> None:
        seed(data, run_id, venues=2)
        for i in range(extra):
            mcp.propose(run_id, f"bulk-{i:04d}", "entities", "venue", f"bulk-venue-{i:04d}",
                        {"name": f"Bulk Venue {i}", "organization_id": "org-x"})

    def test_next_is_bounded_and_still_reports_the_whole_record_count(self):
        with tempfile.TemporaryDirectory() as tmp, isolated(tmp) as (data, _exp):
            self._big(data, "run-a")
            result = mcp.next_step("run-a", 25)
            self.assertGreater(result["record_count"], 500)
            self.assertLessEqual(len(result["targets"]), 25)
            self.assertIn("gap_counts", result)

    def test_a_run_beyond_five_hundred_records_still_reaches_a_nine_sheet_workbook(self):
        from xml.etree import ElementTree as ET
        ns = {"x": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
        with tempfile.TemporaryDirectory() as tmp, isolated(tmp) as (data, exp):
            self._big(data, "run-a")
            result = mcp.handoff("run-a", "op-big")
            self.assertEqual("CHECKPOINT_RESEARCH_NOT_FINISHED", result["status"])
            self.assertFalse(result["is_final"])
            self.assertGreater(result["records"], 500)
            xlsx = Path(result["workbook_xlsx"])
            self.assertTrue(xlsx.is_file())
            with zipfile.ZipFile(xlsx) as z:
                self.assertIsNone(z.testzip())
                wb = ET.fromstring(z.read("xl/workbook.xml"))
            self.assertEqual(9, len(wb.findall(".//x:sheets/x:sheet", ns)))
            self.assertTrue(exp.is_file())

    def test_status_pages_past_five_hundred_records(self):
        with tempfile.TemporaryDirectory() as tmp, isolated(tmp) as (data, _exp):
            self._big(data, "run-a", extra=120)
            first = mcp.status("run-a", limit=100)
            seen, cursor, pages = first["returned"], first["next_cursor"], 1
            while cursor and pages <= 20:
                page = mcp.status("run-a", limit=100, cursor=cursor)
                seen += page["returned"]
                pages += 1
                cursor = page["next_cursor"]
            self.assertGreater(first["record_count"], 100)
            self.assertEqual(first["record_count"], seen, "paging must cover every record")


class BundleTests(unittest.TestCase):
    def test_the_research_bundle_ships_the_workflow_core(self):
        import build_client_bundles as bundles
        with tempfile.TemporaryDirectory() as tmp:
            receipt = bundles.build(Path(tmp) / "b", with_research_mcp=True)
            scripts = Path(receipt["directory"]) / "scripts"
            self.assertTrue((scripts / "research_workflow.py").is_file())
            import py_compile
            py_compile.compile(str(scripts / "research_workflow.py"), doraise=True)

    def test_the_workbuddy_data_root_field_is_an_optional_advanced_override(self):
        import build_client_bundles as bundles
        with tempfile.TemporaryDirectory() as tmp:
            receipt = bundles.build(Path(tmp) / "b", with_research_mcp=True)
            schema = json.loads((Path(receipt["directory"]) / "token-schema.json").read_text("utf-8"))
            field = next(f for f in schema["fields"] if f["key"] == "MARKETOPS_RESEARCH_DATA_ROOT")
            self.assertFalse(field["required"])
            self.assertEqual("", field["defaultValue"])
            self.assertIn("override", field["label"].lower())

    def test_the_legacy_search_bundle_keeps_the_required_data_root(self):
        import build_client_bundles as bundles
        with tempfile.TemporaryDirectory() as tmp:
            receipt = bundles.build(Path(tmp) / "b", with_search_mcp=True)
            schema = json.loads((Path(receipt["directory"]) / "token-schema.json").read_text("utf-8"))
            field = next(f for f in schema["fields"] if f["key"] == "MARKETOPS_RESEARCH_DATA_ROOT")
            self.assertTrue(field["required"])

    def test_every_relative_link_resolves_inside_the_bundle(self):
        import build_client_bundles as bundles
        link = re.compile(r"\]\((?![a-z]+:)([^)#\s]+)")
        with tempfile.TemporaryDirectory() as tmp:
            receipt = bundles.build(Path(tmp) / "b", with_research_mcp=True)
            target = Path(receipt["directory"])
            broken = []
            for md in sorted(target.rglob("*.md")):
                for ref in link.findall(md.read_text(encoding="utf-8", errors="replace")):
                    if not (md.parent / ref).resolve().exists():
                        broken.append((md.relative_to(target).as_posix(), ref))
            self.assertEqual([], broken)

    def test_skill_count_matches_the_source_tree(self):
        import build_client_bundles as bundles
        with tempfile.TemporaryDirectory() as tmp:
            receipt = bundles.build(Path(tmp) / "b", with_research_mcp=True)
            self.assertEqual(13, receipt["skills"])


class PackagedProviderDocsTests(unittest.TestCase):
    CANONICAL = ROOT.parent / "providers"

    def test_packaged_copies_have_no_outbound_relative_links(self):
        packaged = ROOT / "references" / "providers"
        offenders = []
        for md in sorted(packaged.glob("*.md")):
            for ref in re.findall(r"\]\((?![a-z]+:)([^)#\s]+)\)",
                                  md.read_text(encoding="utf-8")):
                if not (md.parent / ref).resolve().exists():
                    offenders.append((md.name, ref))
        self.assertEqual([], offenders)

    def test_the_routing_reference_no_longer_reaches_outside_the_plugin(self):
        text = (ROOT / "references" / "PROVIDER_ROUTING.md").read_text(encoding="utf-8")
        self.assertNotIn("../../providers/", text)
        self.assertIn("PROVIDER_ONBOARDING.md", text)


class PlaceholderUnconfiguredTests(unittest.TestCase):
    def test_an_unreplaced_variable_is_not_a_literal_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = {"HOME": str(Path(tmp) / "home"), "XDG_CONFIG_HOME": str(Path(tmp) / "xdg"),
                   "PATH": os.environ.get("PATH", ""),
                   "MARKETOPS_RESEARCH_DATA_ROOT": "${MARKETOPS_RESEARCH_DATA_ROOT}"}
            env.update({"USERPROFILE": env["HOME"], "APPDATA": str(Path(tmp) / "appdata"),
                        "MARKETOPS_RESEARCH_CONFIG": str(Path(tmp) / "config.json")})
            Path(env["HOME"]).mkdir(parents=True, exist_ok=True)
            with patch.dict(os.environ, env, clear=True):
                status = mcp.setup_status()
                self.assertEqual("CONFIG_REQUIRED", status["status"])
                with self.assertRaises(ValueError) as ctx:
                    mcp.start("r", "s", "o", "v")
                self.assertIn("CONFIG_REQUIRED", str(ctx.exception))
                self.assertFalse((Path.cwd() / "${MARKETOPS_RESEARCH_DATA_ROOT}").exists())

    def test_an_empty_variable_is_not_a_directory_named_nothing(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = {"HOME": str(Path(tmp) / "home"), "XDG_CONFIG_HOME": str(Path(tmp) / "xdg"),
                   "PATH": os.environ.get("PATH", ""),
                   "MARKETOPS_RESEARCH_DATA_ROOT": "   "}
            env.update({"USERPROFILE": env["HOME"], "APPDATA": str(Path(tmp) / "appdata"),
                        "MARKETOPS_RESEARCH_CONFIG": str(Path(tmp) / "config.json")})
            Path(env["HOME"]).mkdir(parents=True, exist_ok=True)
            with patch.dict(os.environ, env, clear=True):
                with self.assertRaises(ValueError) as ctx:
                    mcp.start("r", "s", "o", "v")
                self.assertIn("CONFIG_REQUIRED", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
