"""Independent RC customer-delivery boundary QA for MarketOps PR #9.

Target revision: ``4f87bc3306b90d55bfd0332ef1efc8b97f1dcc44``
(``feat/bruce-plugin-delivery-20261020``).  This file is a QA artifact, not a
product change: it never writes product source and makes no network call.

The fixture is wholly synthetic (``example.org`` / ``example.net``).  It places a
legitimate organisation beside a "Totally Fabricated" organisation and several
held contacts in **one Run**, then reads the workbook ``build_delivery`` returns.

Two audiences are asserted separately and must not be conflated:

* **Customer-facing sheets** (``客户总表`` / ``联系人路线`` / ``场地与资产`` /
  ``人员岗位``) may contain only records that passed admission.  A fabricated
  name, an address that is not a literal in saved material, a missing quote, or
  a contact that is not bound to an admitted organisation must not appear here.
* **Internal audit sheets** (``排除与待确认`` and ``候选数据审计``) must retain
  every candidate.  Dropping a held row from the audit is also a defect: the
  reviewer has to be able to see what was refused and why.

Failure here is reported as a real red, never softened until it turns green.
Run with::

    python -m pytest tests/test_customer_delivery_boundary_qa.py -q

To compare the same assertions against another worktree without writing to it::

    MARKETOPS_QA_PLUGIN_ROOT=/path/to/plugin python -m pytest \
        tests/test_customer_delivery_boundary_qa.py -q
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

_PLUGIN_DEFAULT = (
    Path(__file__).resolve().parents[1]
    / "plugins"
    / "research-tools"
    / "marketops-research-assistant"
)
PLUGIN = Path(os.environ.get("MARKETOPS_QA_PLUGIN_ROOT", _PLUGIN_DEFAULT)).resolve()
sys.path.insert(0, str(PLUGIN / "scripts"))

import research_delivery as delivery  # noqa: E402
import research_lite_store as store  # noqa: E402

RUN_ID = "qa-customer-boundary-run"
ART = "qa-public-material"

LEGIT_ORG_KEY = "example-borough"
FABRICATED_ORG_KEY = "totally-fabricated-org"

CUSTOMER_SHEETS = ("客户总表", "联系人路线", "场地与资产", "人员岗位")
AUDIT_SHEETS = ("排除与待确认", "候选数据审计")

LEGIT_MATERIAL = (
    b"<html><body>"
    b"<h1>Example Borough Council</h1>"
    b"<p>Grounds team contact: grounds@example.org</p>"
    b"<p>Secondary mailbox: secondary@example.org</p>"
    b"<p>Example Borough Council operates Example Sports Ground.</p>"
    b"<p>Example Borough Council staff: Grounds Team.</p>"
    b"</body></html>"
)
UNBOUND_MATERIAL = b"<html><body><p>Unbound mailbox: unbound@example.org</p></body></html>"


def _sheet(sheets, name):
    return next(sheet for sheet in sheets if sheet.name == name)


def _column(sheet, index=0):
    return [row[index] for row in sheet.rows]


class CustomerDeliveryBoundaryQATest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / "data"
        store.start_run(
            self.root,
            RUN_ID,
            "synthetic-customer-boundary-qa",
            "customer delivery boundary, synthetic fixture only",
            "QA-RULES-SYNTHETIC",
        )
        store.save_artifact(
            self.root,
            RUN_ID,
            ART,
            "fixture-public-html",
            "https://example.org/qa-fixture",
            LEGIT_MATERIAL,
            "raw_response",
        )

    # ------------------------------------------------------------------ fixture
    def _propose(self, record_id, stage, kind, entity_key, payload, artifacts):
        return store.put_candidate(
            self.root, RUN_ID, record_id, stage, kind, entity_key, payload, artifacts
        )

    def _seed_mixed_run(self):
        """One legitimate organisation mixed with fabricated / held candidates."""
        self._propose(
            "org-legit",
            "entities",
            "organization",
            LEGIT_ORG_KEY,
            {
                "name": "Example Borough Council",
                "claims": [
                    {"field": "identity", "artifact_id": ART, "quote": "Example Borough Council"}
                ],
            },
            [ART],
        )
        # Fabricated identity: it cites material, but no quote ties the name to it.
        self._propose(
            "org-fabricated",
            "entities",
            "organization",
            FABRICATED_ORG_KEY,
            {"name": "Totally Fabricated Org"},
            [ART],
        )

        self._propose(
            "venue-legit",
            "entities",
            "venue",
            "example-sports-ground",
            {
                "name": "Example Sports Ground",
                "organization_id": LEGIT_ORG_KEY,
                "claims": [
                    {"field": "identity", "artifact_id": ART,
                     "quote": "Example Borough Council operates Example Sports Ground"}
                ],
            },
            [ART],
        )
        self._propose(
            "venue-fabricated",
            "entities",
            "venue",
            "totally-fabricated-arena",
            {"name": "Totally Fabricated Arena", "organization_id": FABRICATED_ORG_KEY},
            [],
        )

        self._propose(
            "person-legit",
            "people",
            "person",
            "grounds-team",
            {
                "name": "Grounds Team",
                "role_title": "Grounds Team",
                "organization_id": LEGIT_ORG_KEY,
                "claims": [
                    {"field": "identity", "artifact_id": ART,
                     "quote": "Example Borough Council staff: Grounds Team"}
                ],
            },
            [ART],
        )
        self._propose(
            "person-fabricated",
            "people",
            "person",
            "totally-fabricated-person",
            {"name": "Totally Fabricated Person", "organization_id": FABRICATED_ORG_KEY},
            [],
        )

        # Admitted: the address is a literal and the quote is recorded.
        self._propose(
            "contact-legit",
            "routes",
            "contact_point",
            "grounds-contact",
            {
                "organization_id": LEGIT_ORG_KEY,
                "type": "email",
                "value": "grounds@example.org",
                "role_function": "Grounds Team",
                "claims": [
                    {
                        "field": "email",
                        "artifact_id": ART,
                        "quote": "Grounds team contact: grounds@example.org",
                    }
                ],
            },
            [ART],
        )
        # Held: the address is not a literal in any saved material.
        self._propose(
            "contact-fabricated-literal-absent",
            "routes",
            "contact_point",
            "fabricated-address",
            {"organization_id": LEGIT_ORG_KEY, "type": "email", "value": "fabricated@example.net"},
            [ART],
        )
        # Held: the address is a literal, but there is no quote / span.
        self._propose(
            "contact-fabricated-no-quote",
            "routes",
            "contact_point",
            "secondary-address",
            {"organization_id": LEGIT_ORG_KEY, "type": "email", "value": "secondary@example.org"},
            [ART],
        )
        # Held: the address looks supported, but it is bound to a rejected org.
        self._propose(
            "contact-cross-org",
            "routes",
            "contact_point",
            "cross-org-address",
            {
                "organization_id": FABRICATED_ORG_KEY,
                "type": "email",
                "value": "grounds@example.org",
                "claims": [
                    {
                        "field": "email",
                        "artifact_id": ART,
                        "quote": "Grounds team contact: grounds@example.org",
                    }
                ],
            },
            [ART],
        )
        # Held: literal + quote, but nothing in its own material names the org.
        store.save_artifact(
            self.root,
            RUN_ID,
            "qa-unbound-material",
            "fixture-public-html",
            "https://example.net/qa-unbound",
            UNBOUND_MATERIAL,
            "raw_response",
        )
        self._propose(
            "contact-unbound",
            "routes",
            "contact_point",
            "unbound-address",
            {
                "organization_id": LEGIT_ORG_KEY,
                "type": "email",
                "value": "unbound@example.org",
                "claims": [
                    {
                        "field": "email",
                        "artifact_id": "qa-unbound-material",
                        "quote": "Unbound mailbox: unbound@example.org",
                    }
                ],
            },
            ["qa-unbound-material"],
        )

        return [
            "org-legit",
            "org-fabricated",
            "venue-legit",
            "venue-fabricated",
            "person-legit",
            "person-fabricated",
            "contact-legit",
            "contact-fabricated-literal-absent",
            "contact-fabricated-no-quote",
            "contact-cross-org",
            "contact-unbound",
        ]

    # ------------------------------------------------------------------- tests
    def test_customer_business_sheets_exclude_fabricated_candidates(self):
        self._seed_mixed_run()
        sheets, report = delivery.build_delivery(self.root, RUN_ID)
        by_name = {sheet.name: sheet for sheet in sheets}

        self.assertEqual(
            ["Example Borough Council"],
            _column(by_name["客户总表"], 1),
            "客户总表 must contain only the admitted organisation",
        )
        self.assertEqual(
            ["example-sports-ground"],
            _column(by_name["场地与资产"], 0),
            "场地与资产 must not carry a fabricated venue",
        )
        self.assertEqual(
            ["person-legit"],
            _column(by_name["人员岗位"], 0),
            "人员岗位 must not carry a fabricated person",
        )
        self.assertEqual(1, report["counts"]["客户总表"])

    def test_contact_sheet_excludes_held_and_cross_bound_channels(self):
        self._seed_mixed_run()
        sheets, _ = delivery.build_delivery(self.root, RUN_ID)
        by_name = {sheet.name: sheet for sheet in sheets}

        self.assertEqual(
            ["contact-legit"],
            _column(by_name["联系人路线"], 0),
            "联系人路线 must contain only admitted contact channels",
        )

    def test_public_channel_count_counts_only_admitted_channels(self):
        self._seed_mixed_run()
        sheets, _ = delivery.build_delivery(self.root, RUN_ID)
        by_name = {sheet.name: sheet for sheet in sheets}

        # 公开联系渠道数 is a customer-visible count: only the admitted channel.
        org_row = next(row for row in by_name["客户总表"].rows if row[0] == LEGIT_ORG_KEY)
        self.assertEqual(1, org_row[5], "公开联系渠道数 must count admitted channels only")
        self.assertEqual(1, org_row[4], "关联场地数 must count admitted venues only")

    def test_refused_candidates_are_listed_with_reasons_in_exclusion_table(self):
        self._seed_mixed_run()
        sheets, _ = delivery.build_delivery(self.root, RUN_ID)
        by_name = {sheet.name: sheet for sheet in sheets}

        reasons = {row[1]: row[2] for row in by_name["排除与待确认"].rows}
        for record_id in (
            "org-fabricated",
            "venue-fabricated",
            "person-fabricated",
            "contact-fabricated-literal-absent",
            "contact-fabricated-no-quote",
            "contact-cross-org",
            "contact-unbound",
        ):
            self.assertIn(
                record_id,
                reasons,
                f"{record_id} must be refused in 排除与待确认, not silently dropped",
            )
            self.assertTrue(reasons[record_id].strip(), "a refused candidate must state its reason")

    def test_internal_audit_retains_every_candidate(self):
        all_record_ids = self._seed_mixed_run()
        sheets, _ = delivery.build_delivery(self.root, RUN_ID)
        by_name = {sheet.name: sheet for sheet in sheets}

        self.assertEqual(
            set(all_record_ids),
            set(_column(by_name["候选数据审计"], 0)),
            "候选数据审计 is the audit trail; it must not drop a candidate",
        )

    def _seed_sample_window_run(self):
        self._propose(
            "org-legit",
            "entities",
            "organization",
            LEGIT_ORG_KEY,
            {
                "name": "Example Borough Council",
                "claims": [
                    {"field": "identity", "artifact_id": ART, "quote": "Example Borough Council"}
                ],
            },
            [ART],
        )
        # Every bulk record cites material and has a linked quote, but lacks the
        # mandatory identity claim. 105 > the audit's result_limit of 100.
        for index in range(105):
            self._propose(
                f"bulk-{index:03d}",
                "entities",
                "organization",
                f"bulk-org-{index:03d}",
                {
                    "name": f"Fabricated Bulk Org {index:03d}",
                    "claims": [
                        {
                            "field": "description",
                            "artifact_id": ART,
                            "quote": "Example Borough Council",
                        }
                    ],
                },
                [ART],
            )

    def test_records_beyond_100_sample_window_stay_out_of_customer_sheet(self):
        """The audit's 100-row sample must not become the admission decision set."""
        self._seed_sample_window_run()
        sheets, report = delivery.build_delivery(self.root, RUN_ID)
        by_name = {sheet.name: sheet for sheet in sheets}

        self.assertEqual(
            ["Example Borough Council"],
            _column(by_name["客户总表"], 1),
            "all 105 unadmitted organisations must stay out of 客户总表",
        )
        self.assertEqual(1, report["counts"]["客户总表"])

    def test_records_beyond_100_sample_window_are_listed_in_exclusion_table(self):
        self._seed_sample_window_run()
        sheets, _ = delivery.build_delivery(self.root, RUN_ID)
        by_name = {sheet.name: sheet for sheet in sheets}

        issue_ids = set(_column(by_name["排除与待确认"], 1))
        for index in range(105):
            self.assertIn(
                f"bulk-{index:03d}",
                issue_ids,
                "every refused record must be listed, not only the first 100",
            )
        # The later records are exactly the ones the audit sample would omit.
        for index in range(100, 105):
            self.assertIn(
                f"bulk-{index:03d}",
                issue_ids,
                "record beyond the 100-sample window was not adjudicated",
            )

    def test_internal_audit_retains_all_records_beyond_sample_window(self):
        self._seed_sample_window_run()
        sheets, _ = delivery.build_delivery(self.root, RUN_ID)
        by_name = {sheet.name: sheet for sheet in sheets}

        audit_ids = set(_column(by_name["候选数据审计"], 0))
        self.assertEqual(
            {"org-legit"} | {f"bulk-{index:03d}" for index in range(105)},
            audit_ids,
            "候选数据审计 must retain all 106 candidates",
        )

    def test_delivery_is_immutable_and_manifest_hashes_match_disk(self):
        """The artifact a customer receives is readable, hashed and never replaced."""
        self._propose(
            "org-legit",
            "entities",
            "organization",
            LEGIT_ORG_KEY,
            {
                "name": "Example Borough Council",
                "claims": [
                    {"field": "identity", "artifact_id": ART, "quote": "Example Borough Council"}
                ],
            },
            [ART],
        )

        result = delivery.write_delivery(self.root, RUN_ID, "qa-delivery-001")
        directory = Path(result["directory"])
        manifest = json.loads((directory / "MANIFEST.json").read_text(encoding="utf-8"))

        self.assertIn("Research_Result.xlsx", manifest["files"])
        for name, metadata in manifest["files"].items():
            path = directory / name
            data = path.read_bytes()
            self.assertEqual(metadata["bytes"], len(data), name)
            self.assertEqual(metadata["sha256"], hashlib.sha256(data).hexdigest(), name)

        with zipfile.ZipFile(result["xlsx_path"]) as archive:
            self.assertIsNone(archive.testzip())
            sheet_count = len(
                [
                    name
                    for name in archive.namelist()
                    if name.startswith("xl/worksheets/sheet") and name.endswith(".xml")
                ]
            )
        self.assertEqual(len(delivery.TABLES), sheet_count)

        before = {
            path.name: hashlib.sha256(path.read_bytes()).hexdigest()
            for path in directory.iterdir()
            if path.is_file()
        }
        with self.assertRaises(FileExistsError):
            delivery.write_delivery(self.root, RUN_ID, "qa-delivery-001")
        after = {
            path.name: hashlib.sha256(path.read_bytes()).hexdigest()
            for path in directory.iterdir()
            if path.is_file()
        }
        self.assertEqual(before, after, "an existing delivery must not be overwritten")


if __name__ == "__main__":
    unittest.main()
