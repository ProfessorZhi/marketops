"""Cross-Lane RC gate: Lane A's admission output feeds Lane B's client view.

This is the integration seam, not a second admission engine.  It proves the
property the RC promises: a record that cannot be tied to a saved excerpt is
kept in the internal audit sheets but never appears in a client-visible sheet,
while a *complete* synthetic record is still admitted (the gate is not a
blanket refusal).

All material is synthetic (``example.org``); no network, provider, key or real
customer data is involved.  If Lane B has not been merged yet the test reports an
explicit skip; ``scripts/rc-20261020/check_release_gate.py`` treats that skip as
BLOCKED rather than success.
"""
from __future__ import annotations

import csv
import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import research_delivery as delivery  # noqa: E402
import research_lite_store as store  # noqa: E402

try:  # Lane B's artefact; absent on the untouched baseline.
    import research_client_view as client_view  # noqa: E402
except ImportError:  # pragma: no cover - exercised before Lane B is merged
    client_view = None

RUN_ID = "rc-integration-run"
ART = "rc-public-material"
STAMP = "2026-10-20T00:00:00+00:00"

PAGE = (
    b"<html><body>"
    b"<h1>Example Borough Council</h1>"
    b"<p>Example Sports Ground is operated by Example Borough Council.</p>"
    b"<p>Grounds Management at Example Borough Council is the responsible department.</p>"
    b"<p>Contact Example Borough Council at grounds@example.org for grounds enquiries.</p>"
    b"</body></html>"
)

LEGIT_ORG_KEY = "example-borough"


def _rows(directory: Path, name: str) -> list[dict]:
    with (directory / f"{name}.csv").open(encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


@unittest.skipUnless(client_view is not None,
                     "LANE_B_NOT_MERGED: research_client_view.py is absent")
class DeliveryToClientViewTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / "data"
        self.out = Path(self.tmp.name) / "out"
        store.start_run(self.root, RUN_ID, "GB-sports-grounds",
                        "RC cross-lane integration fixture", "RC-RULES-SYNTHETIC")
        store.save_artifact(self.root, RUN_ID, ART, "fixture-public-html",
                            "https://example.org/rc-fixture", PAGE, "rendered_html")
        self._seed_mixed_run()

    def _propose(self, record_id, stage, kind, entity_key, payload, artifacts):
        return store.put_candidate(self.root, RUN_ID, record_id, stage, kind,
                                   entity_key, payload, artifacts)

    def _seed_mixed_run(self):
        self._propose(
            "org-legit", "entities", "organization", LEGIT_ORG_KEY,
            {"name": "Example Borough Council",
             "claims": [{"field": "identity", "artifact_id": ART,
                         "quote": "Example Borough Council"}]}, [ART])
        self._propose(
            "venue-legit", "entities", "venue", "example-sports-ground",
            {"name": "Example Sports Ground", "organization_id": LEGIT_ORG_KEY,
             "claims": [{"field": "identity", "artifact_id": ART,
                         "quote": "Example Sports Ground is operated by Example Borough Council."}]},
            [ART])
        self._propose(
            "person-legit", "people", "person", "grounds-management",
            {"department": "Grounds Management", "role_title": "Grounds Management",
             "organization_id": LEGIT_ORG_KEY,
             "claims": [{"field": "role_title", "artifact_id": ART,
                         "quote": "Grounds Management at Example Borough Council is the responsible department."}]},
            [ART])
        self._propose(
            "contact-legit", "routes", "contact_point", "grounds-contact",
            {"organization_id": LEGIT_ORG_KEY, "type": "email",
             "value": "grounds@example.org", "role_function": "Grounds Management",
             "claims": [{"field": "email", "artifact_id": ART,
                         "quote": "Contact Example Borough Council at grounds@example.org for grounds enquiries."}]},
            [ART])
        # P0 negatives: no quote, an address that is not a literal, and an
        # organisation that cites material but never ties its own name to it.
        self._propose(
            "org-fabricated", "entities", "organization", "totally-fabricated-org",
            {"name": "Totally Fabricated Org"}, [ART])
        self._propose(
            "venue-fabricated", "entities", "venue", "totally-fabricated-arena",
            {"name": "Totally Fabricated Arena"}, [])
        self._propose(
            "contact-fabricated", "routes", "contact_point", "fabricated-address",
            {"organization_id": LEGIT_ORG_KEY, "type": "email",
             "value": "fabricated@example.net"}, [ART])
        self._propose(
            "contact-no-quote", "routes", "contact_point", "secondary-address",
            {"organization_id": LEGIT_ORG_KEY, "type": "email",
             "value": "secondary@example.org"}, [ART])

    def _client_view(self):
        sheets, report = delivery.build_delivery(self.root, RUN_ID)
        business = [sheet for sheet in sheets
                    if sheet.name in delivery.CLIENT_BUSINESS_SHEETS]
        self.assertEqual(4, len(business),
                         "delivery must expose exactly the four client business sheets")
        receipt = client_view.write_client_view(
            self.out / "client-view", business,
            label=RUN_ID, generated_at=STAMP)
        return sheets, report, receipt

    # ------------------------------------------------------------------ P0
    def test_fabricated_records_never_reach_client_business_sheets(self):
        sheets, report, receipt = self._client_view()
        view = self.out / "client-view"

        self.assertEqual(["Example Borough Council"],
                         [row["组织名称"] for row in _rows(view, "客户总表")])
        self.assertEqual(["grounds@example.org"],
                         [row["公开字面值"] for row in _rows(view, "联系人路线")])
        # 场地与资产's first column is the venue entity key, not the record id.
        self.assertEqual(["example-sports-ground"],
                         [row["场地ID"] for row in _rows(view, "场地与资产")])
        self.assertEqual(["person-legit"],
                         [row["候选ID"] for row in _rows(view, "人员岗位")])

        rendered = "\n".join(
            (view / f"{name}.csv").read_text(encoding="utf-8-sig")
            for name in delivery.CLIENT_BUSINESS_SHEETS)
        for forbidden in ("Totally Fabricated", "fabricated@example.net",
                          "secondary@example.org"):
            self.assertNotIn(forbidden, rendered)

        # The refusal is visible, not silent: both audiences keep their rows.
        self.assertEqual(4, report["admission"]["admitted_record_count"])
        self.assertEqual(4, report["admission"]["rejected_record_count"])
        self.assertEqual("CANDIDATE_NOT_APPROVED", receipt["status"])
        self.assertFalse(receipt["send_authorized"])
        self.assertFalse(receipt["formal_sop_claimed"])

    def test_qualified_sample_is_admitted_and_counts_match_the_view(self):
        _, report, receipt = self._client_view()
        view = self.out / "client-view"
        for name in delivery.CLIENT_BUSINESS_SHEETS:
            self.assertEqual(report["counts"][name], len(_rows(view, name)),
                             f"{name} row count differs from the delivery manifest")
        self.assertEqual({"客户总表": 1, "联系人路线": 1, "场地与资产": 1, "人员岗位": 1},
                         receipt["counts"])

    def test_client_view_manifest_hashes_match_disk(self):
        _, _, receipt = self._client_view()
        view = self.out / "client-view"
        manifest = json.loads((view / "MANIFEST.json").read_text(encoding="utf-8"))
        self.assertEqual(receipt["manifest_sha256"],
                         hashlib.sha256((view / "MANIFEST.json").read_bytes()).hexdigest())
        for name, meta in manifest["files"].items():
            payload = (view / name).read_bytes()
            self.assertEqual(meta["bytes"], len(payload), name)
            self.assertEqual(meta["sha256"], hashlib.sha256(payload).hexdigest(), name)
        self.assertTrue(client_view.verify_client_view(view)["files_verified"])

    def test_replay_is_byte_identical_and_different_content_is_refused(self):
        _, _, first = self._client_view()
        view = self.out / "client-view"
        before = {path.name: hashlib.sha256(path.read_bytes()).hexdigest()
                  for path in view.iterdir() if path.is_file()}
        second = self._client_view()[2]
        self.assertTrue(second["replayed"])
        after = {path.name: hashlib.sha256(path.read_bytes()).hexdigest()
                 for path in view.iterdir() if path.is_file()}
        self.assertEqual(before, after)
        self.assertEqual(first["manifest_sha256"], second["manifest_sha256"])

        sheets, _ = delivery.build_delivery(self.root, RUN_ID)
        business = [sheet for sheet in sheets
                    if sheet.name in delivery.CLIENT_BUSINESS_SHEETS]
        with self.assertRaises(client_view.ClientViewError):
            client_view.write_client_view(view, business, label="different-label",
                                          generated_at=STAMP)

    def test_full_delivery_keeps_refused_candidates_in_the_audit_sheets(self):
        result = delivery.write_delivery(self.root, RUN_ID, "rc-delivery-001")
        directory = Path(result["directory"])
        self.assertIn("排除与待确认", {path.stem for path in directory.glob("*.csv")})
        refused = {row["对象ID"] for row in _rows(directory, "排除与待确认")}
        self.assertTrue({"org-fabricated", "venue-fabricated", "contact-fabricated",
                         "contact-no-quote"} <= refused,
                        f"refused records missing from audit: {sorted(refused)}")
        audited = {row["记录ID"] for row in _rows(directory, "候选数据审计")}
        self.assertTrue({"org-fabricated", "venue-fabricated", "contact-fabricated",
                         "contact-no-quote"} <= audited)
        manifest = json.loads((directory / "MANIFEST.json").read_text(encoding="utf-8"))
        for name, meta in manifest["files"].items():
            payload = (directory / name).read_bytes()
            self.assertEqual(meta["sha256"], hashlib.sha256(payload).hexdigest(), name)


if __name__ == "__main__":
    unittest.main()
