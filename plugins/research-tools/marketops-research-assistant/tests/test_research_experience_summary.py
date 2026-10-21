"""One collectable experience file, refreshed from the append-only journal.

No network, no provider, no credentials and no real customer data: the notes and
events here are synthetic.
"""
from __future__ import annotations
import re
import sqlite3
import sys
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import research_config as cfg
import research_event_journal as journal
import research_experience_summary as summary


def paths(tmp: str):
    base = Path(tmp)
    data = base / "research-data"
    exp = base / "Research_Experience.md"
    data.mkdir(parents=True, exist_ok=True)
    return data, exp


def seed_run(data: Path, run_id: str, *, blocked: bool = False):
    journal.append_event(data, run_id=run_id, operation_id="run-" + run_id,
                         stage="intake", kind="run", status="succeeded", provider="local")
    if blocked:
        journal.append_event(data, run_id=run_id, operation_id="src-1",
                             stage="discovery", kind="source", status="blocked",
                             provider="serper", reason_code="source_blocked")


class RefreshTests(unittest.TestCase):
    def test_refresh_creates_the_file_with_the_marker(self):
        with tempfile.TemporaryDirectory() as tmp:
            data, exp = paths(tmp)
            seed_run(data, "run-a")
            result = summary.refresh(data_root=data, experience_file=exp)
            self.assertEqual("REFRESHED", result["status"])
            text = exp.read_text(encoding="utf-8")
            self.assertTrue(text.lstrip().startswith(cfg.EXPERIENCE_MARKER))
            self.assertEqual(str(exp), result["experience_file"])
            self.assertGreater(result["bytes"], 0)

    def test_refresh_aggregates_across_all_runs_and_keeps_history(self):
        with tempfile.TemporaryDirectory() as tmp:
            data, exp = paths(tmp)
            seed_run(data, "run-a", blocked=True)
            seed_run(data, "run-b")
            first = summary.refresh(data_root=data, experience_file=exp)
            seed_run(data, "run-c", blocked=True)
            second = summary.refresh(data_root=data, experience_file=exp)
            text = exp.read_text(encoding="utf-8")
            for run_id in ("run-a", "run-b", "run-c"):
                self.assertIn(run_id, text)
            self.assertLess(first["runs"], second["runs"])
            self.assertEqual(3, second["runs"])

    def test_refresh_lists_the_required_columns_per_incident(self):
        with tempfile.TemporaryDirectory() as tmp:
            data, exp = paths(tmp)
            seed_run(data, "run-a", blocked=True)
            stats = summary.refresh(data_root=data, experience_file=exp)
            text = exp.read_text(encoding="utf-8")
            for heading in ("发生时间", "Run", "工具/原因", "处理尝试", "证据 ref", "经验候选"):
                self.assertIn(heading, text)
            self.assertIn("source_blocked", text)
            self.assertEqual(1, stats["bump_events"])

    def test_no_bumps_is_not_an_all_clear(self):
        with tempfile.TemporaryDirectory() as tmp:
            data, exp = paths(tmp)
            seed_run(data, "run-a")   # success only, no blocked/hold
            stats = summary.refresh(data_root=data, experience_file=exp)
            text = exp.read_text(encoding="utf-8")
            self.assertEqual(0, stats["bump_events"])
            self.assertIn("无异常不代表零卡点", text)

    def test_the_preamble_matches_whether_bumps_were_actually_recorded(self):
        with tempfile.TemporaryDirectory() as tmp:
            data, exp = paths(tmp)
            seed_run(data, "run-a", blocked=True)
            summary.refresh(data_root=data, experience_file=exp)
            text = exp.read_text(encoding="utf-8")
            # The standing caveat stays, but it must not claim "nothing was recorded"
            # directly above a table that lists a recorded bottleneck.
            self.assertIn("无异常不代表零卡点", text)
            self.assertNotIn("这里没有记录到阻断", text)
            self.assertIn("1 项卡点", text)

    def test_summary_is_marked_candidate_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            data, exp = paths(tmp)
            seed_run(data, "run-a", blocked=True)
            summary.refresh(data_root=data, experience_file=exp)
            text = exp.read_text(encoding="utf-8")
            self.assertIn("candidate", text)
            self.assertIn("不修改正式SOP", text)

    def test_refresh_refuses_to_overwrite_a_newer_snapshot(self):
        with tempfile.TemporaryDirectory() as tmp:
            data, exp = paths(tmp)
            seed_run(data, "run-a", blocked=True)
            summary.refresh(data_root=data, experience_file=exp)
            # A different process rendered a snapshot that already knows more.
            lines = exp.read_text(encoding="utf-8").splitlines()
            ahead = re.sub(r"events=(\d+)", lambda m: f"events={int(m.group(1)) + 50}", lines[0])
            exp.write_text("\n".join([ahead] + lines[1:]) + "\n", encoding="utf-8")
            result = summary.refresh(data_root=data, experience_file=exp)
            self.assertEqual("SKIPPED_NOT_NEWER", result["status"])
            self.assertEqual("EXPERIENCE_FILE_IS_NEWER", result["reason_code"])
            self.assertIn(ahead, exp.read_text(encoding="utf-8"))

    def test_a_failed_refresh_leaves_the_previous_file_intact(self):
        with tempfile.TemporaryDirectory() as tmp:
            data, exp = paths(tmp)
            seed_run(data, "run-a", blocked=True)
            summary.refresh(data_root=data, experience_file=exp)
            before = exp.read_text(encoding="utf-8")
            original = summary.render
            summary.render = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom"))
            try:
                result = summary.refresh(data_root=data, experience_file=exp)
            finally:
                summary.render = original
            self.assertEqual("FAILED", result["status"])
            self.assertEqual("boom", result["reason_code"])
            self.assertEqual(before, exp.read_text(encoding="utf-8"))

    def test_refresh_without_a_configured_experience_file_reports_config_required(self):
        with tempfile.TemporaryDirectory() as tmp:
            data, _ = paths(tmp)
            seed_run(data, "run-a")
            iso_env = {"HOME": str(Path(tmp) / "home"), "XDG_CONFIG_HOME": str(Path(tmp) / "xdg")}
            Path(iso_env["HOME"]).mkdir(parents=True, exist_ok=True)
            result = summary.refresh(data_root=data, experience_file=None, env=iso_env)
            self.assertEqual("FAILED", result["status"])
            self.assertEqual("CONFIG_REQUIRED", result["reason_code"])

    def test_an_unreadable_snapshot_header_fails_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            data, exp = paths(tmp)
            seed_run(data, "run-a", blocked=True)
            exp.write_text("<!-- marketops:research-experience garbage -->\n",
                           encoding="utf-8")
            result = summary.refresh(data_root=data, experience_file=exp)
            self.assertEqual("FAILED", result["status"])


class NoteTests(unittest.TestCase):
    def test_a_real_bottleneck_can_be_recorded_with_an_evidence_ref(self):
        with tempfile.TemporaryDirectory() as tmp:
            data, _ = paths(tmp)
            seed_run(data, "run-a")
            result = journal.append_note(
                data, run_id="run-a", note_id="note-1", stage="acquisition",
                category="contact_found_not_stored",
                problem="发现官方页面有部门邮箱，但候选未入库",
                action_taken="改用 propose 逐条写入并附 artifact_id",
                observed_result="第二次入库成功，证据引用可回读",
                hypothesis="抽取成功不等于入库成功，需回读核对",
                evidence_ref="artifact-src-1")
            self.assertEqual("RECORDED", result["result"])
            self.assertIsInstance(result["sequence"], int)

    def test_the_audit_categories_are_recordable(self):
        with tempfile.TemporaryDirectory() as tmp:
            data, _ = paths(tmp)
            seed_run(data, "run-a")
            for category in ("contact_found_not_stored", "field_template_pollution",
                             "artifact_without_delivery"):
                self.assertIn(category, journal.NOTE_CATEGORIES)
                journal.append_note(
                    data, run_id="run-a", note_id="note-" + category,
                    stage="review", category=category, problem="synthetic probe",
                    action_taken="synthetic action", observed_result="synthetic result",
                    evidence_ref="artifact-src-1")
            rows = journal.read_notes(data, "run-a")
            self.assertEqual(3, len(rows))

    def test_notes_are_idempotent_but_a_conflicting_replay_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            data, _ = paths(tmp)
            seed_run(data, "run-a")
            kwargs = dict(run_id="run-a", note_id="note-1", stage="review",
                          category="other", problem="p", action_taken="a",
                          observed_result="o", evidence_ref="artifact-src-1")
            first = journal.append_note(data, **kwargs)
            second = journal.append_note(data, **kwargs)
            self.assertEqual("RECORDED", first["result"])
            self.assertEqual("IDEMPOTENT", second["result"])
            with self.assertRaises(ValueError) as ctx:
                journal.append_note(data, **{**kwargs, "problem": "changed text"})
            self.assertIn("Conflicting replay", str(ctx.exception))
            self.assertEqual(1, len(journal.read_notes(data, "run-a")))

    def test_a_note_rejects_an_email_url_or_key_literal(self):
        with tempfile.TemporaryDirectory() as tmp:
            data, _ = paths(tmp)
            seed_run(data, "run-a")
            bad = [
                ("problem", "contact bob.smith@example.org was on the page"),
                ("problem", "see https://example.org/team for the list"),
                ("action_taken", "used key sk-abcdefghijklmnopqrstuvwxyz012345"),
                ("observed_result", "got cookie session=abc123def456ghi789"),
                ("hypothesis", "token: eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9"),
            ]
            for field, value in bad:
                payload = dict(run_id="run-a", note_id="note-" + field + str(len(value)),
                               stage="review", category="other", problem="p",
                               action_taken="a", observed_result="o",
                               evidence_ref="artifact-src-1")
                payload[field] = value
                with self.assertRaises(ValueError) as ctx:
                    journal.append_note(data, **payload)
                self.assertIn("NOTE_REJECTED", str(ctx.exception))
                self.assertNotIn("example.org", str(ctx.exception))

    def test_a_note_has_nowhere_to_put_hidden_reasoning(self):
        with tempfile.TemporaryDirectory() as tmp:
            data, _ = paths(tmp)
            seed_run(data, "run-a")
            with self.assertRaises(ValueError):
                journal.append_note(data, run_id="run-a", note_id="note-cot",
                                    stage="review", category="other", problem="p",
                                    action_taken="a", observed_result="o",
                                    evidence_ref="artifact-src-1",
                                    chain_of_thought="first I thought ...")
            with closing(sqlite3.connect(data / "state" / "research-events.sqlite3")) as db:
                columns = {row[1] for row in db.execute("PRAGMA table_info(research_notes)")}
            self.assertEqual(set(journal.NOTE_FIELDS), columns)

    def test_a_note_must_carry_a_usable_evidence_witness(self):
        # evidence_ref stays mandatory: every note must point at a stable opaque
        # witness (the delivery-time gap note uses "snapshot-<snapshot>", which is
        # stable across deliveries of the same snapshot). A missing or non-opaque
        # reference is refused, so no note can be filed without a witness.
        with tempfile.TemporaryDirectory() as tmp:
            data, _ = paths(tmp)
            seed_run(data, "run-a")
            for ref in ("", "https://example.org/page"):
                with self.assertRaises(ValueError) as ctx:
                    journal.append_note(data, run_id="run-a", note_id="note-" + str(len(ref)),
                                        stage="review", category="other", problem="p",
                                        action_taken="a", observed_result="o",
                                        evidence_ref=ref)
                self.assertIn("evidence_ref", str(ctx.exception))
            ok = journal.append_note(data, run_id="run-a", note_id="note-ok",
                                     stage="review", category="other", problem="p",
                                     action_taken="a", observed_result="o",
                                     evidence_ref="snapshot-3f3cec48f77f7bc1f80ab5be")
            self.assertEqual("RECORDED", ok["result"])

    def test_notes_appear_in_the_refreshed_summary(self):
        with tempfile.TemporaryDirectory() as tmp:
            data, exp = paths(tmp)
            seed_run(data, "run-a", blocked=True)
            journal.append_note(data, run_id="run-a", note_id="note-1",
                                stage="acquisition", category="field_template_pollution",
                                problem="模板字段把两个部门合并成一行",
                                action_taken="拆成两条候选并各自附来源",
                                observed_result="复核通过",
                                evidence_ref="artifact-src-1")
            stats = summary.refresh(data_root=data, experience_file=exp)
            text = exp.read_text(encoding="utf-8")
            self.assertEqual(1, stats["notes"])
            self.assertIn("field_template_pollution", text)
            self.assertIn("模板字段把两个部门合并成一行", text)


class FormatTests(unittest.TestCase):
    def test_the_marker_header_is_machine_readable(self):
        with tempfile.TemporaryDirectory() as tmp:
            data, exp = paths(tmp)
            seed_run(data, "run-a", blocked=True)
            journal.append_note(data, run_id="run-a", note_id="note-1", stage="review",
                                category="other", problem="p", action_taken="a",
                                observed_result="o", evidence_ref="artifact-src-1")
            summary.refresh(data_root=data, experience_file=exp)
            head = exp.read_text(encoding="utf-8").splitlines()[0]
            fields = dict(re.findall(r"(\w+)=(\d+)", head))
            self.assertEqual("2", fields["events"])   # run + one blocked source event
            self.assertEqual("1", fields["notes"])
            self.assertEqual("1", fields["runs"])
            self.assertEqual("1", fields["bump_events"])
            parsed = summary.read_snapshot_header(exp)
            self.assertEqual(2, parsed["events"])
            self.assertEqual(1, parsed["bump_events"])


if __name__ == "__main__":
    unittest.main()
