"""Local event persistence tests: synthetic metadata only, no provider calls."""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts import research_event_journal as journal
from scripts import research_pack as pack
from scripts import experience_pack

class JournalTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / "customer-data"

    def event(self, **kw):
        values = {
            "run_id": "devon-001", "operation_id": "fetch-001",
            "stage": "acquisition", "kind": "tool", "status": "blocked",
            "provider": "crawl4ai", "receipt_ref": "receipt-001",
            "reason_code": "challenge_detected",
        }
        values.update(kw)
        return journal.append_event(self.root, **values)

    def test_append_is_durable_and_idempotent(self):
        first = self.event()
        self.assertEqual("RECORDED", first["result"])
        second = self.event()
        self.assertEqual("IDEMPOTENT", second["result"])
        self.assertEqual(first["sequence"], second["sequence"])
        self.assertEqual(1, len(journal.read_events(self.root, "devon-001")))
        self.assertTrue((self.root / "state" / "research-events.sqlite3").is_file())

    def test_conflicting_replay_is_rejected(self):
        self.event()
        with self.assertRaises(ValueError):
            self.event(receipt_ref="receipt-002")
        self.assertEqual(1, len(journal.read_events(self.root, "devon-001")))

    def test_raw_urls_and_emails_not_accepted_as_event_identifiers(self):
        for bad in ("https://secret.example/path", "person@example.com", "../secrets"):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                self.event(operation_id=bad)

    def test_failed_event_yields_unapproved_incident_draft(self):
        self.event()
        self.event(operation_id="fetch-002", status="succeeded", reason_code="")
        dest = Path(self.tmp.name) / "experience.json"
        result = journal.draft_experience(
            self.root, run_id="devon-001", scope="Synthetic Devon public research",
            rule_version="uk-v1", skill_version="0.2.2", output=dest,
        )
        self.assertEqual("CANDIDATE_DRAFT_NOT_VERIFIED", result["result"])
        self.assertEqual(2, result["events"])
        data = json.loads(dest.read_text(encoding="utf-8"))
        self.assertEqual("candidate_only", data["promotion"])
        self.assertEqual([], data["lessons"])
        self.assertEqual("unknown", data["incidents"][0]["root_cause_status"])
        self.assertEqual("VALID_CANDIDATE_NOT_VERIFIED", experience_pack.validate(data)["status"])
        with self.assertRaises(FileExistsError):
            journal.draft_experience(self.root, run_id="devon-001", scope="scope",
                                     rule_version="v1", skill_version="v1", output=dest)

    def test_empty_run_never_claims_validated_experience(self):
        target = Path(self.tmp.name) / "empty.json"
        journal.draft_experience(self.root, run_id="devon-001", scope="none",
                                 rule_version="v1", skill_version="v1", output=target)
        report = experience_pack.validate(json.loads(target.read_text(encoding="utf-8")))
        self.assertEqual("EMPTY_NOT_EVALUATED", report["status"])

    def test_init_cli_automatically_records_run_creation(self):
        folder = self.root / "runs" / "devon-001"
        rc = pack.main(["init", str(folder), "--request-id", "devon-001",
                        "--country", "GB", "--scene", "gb-natural-grass",
                        "--objective", "Public sports grounds"])
        self.assertEqual(0, rc)
        events = journal.read_events(folder.parent, "devon-001")
        self.assertEqual(1, len(events))
        self.assertEqual("intake", events[0]["stage"])

if __name__ == "__main__":
    unittest.main()
