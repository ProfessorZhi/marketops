"""RC3 P0 controls: field-, entity- and relationship-level evidence admission.

The two P0 defects this file exists to keep closed:

1. ``evaluate_admission`` admitted a record whenever *any* quote from a linked
   artifact existed — even when the quote did not contain the name/title being
   shown. A quote is evidence only for a value it actually says.
2. ``_binding_supported`` treated a shared artifact id as ownership. A public
   directory page lists many organisations, so a contact could be attached to
   whichever organisation the caller typed. Binding now needs one local span
   containing both the organisation name and the record's own value, or a named
   relationship quote that does the same.

Every fixture is synthetic (``example.org`` / ``example.net``), offline and
local. No provider, key, browser or network call is involved.
"""
from __future__ import annotations

import csv
import hashlib
import json
import os
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import research_client_view as client_view  # noqa: E402
import research_delivery as delivery  # noqa: E402
import research_lite_store as store  # noqa: E402


FILLER = "".join(f"<p>Unrelated directory entry {index}: public park information.</p>"
                 for index in range(120))
PAGE = (
    "<html><body>"
    "<h1>Example Borough Council</h1>"
    "<p>Example Sports Ground is operated by Example Borough Council.</p>"
    "<p>Alex Sample is the Site manager at Example Borough Council.</p>"
    "<p>Grounds Management at Example Borough Council is the responsible department.</p>"
    "<p>Contact Example Borough Council at grounds@example.org for grounds enquiries.</p>"
    "<p>For general enquiries email grounds@example.org.</p>"
    "<p>Example Borough Council publishes grounds@example.org for grounds enquiries.</p>"
    "<h1>North District Council</h1>"
    "<p>North Depot is operated by North District Council.</p>"
    "<p>Contact North District Council at north@example.org for enquiries.</p>"
    "<h1>Acme Natural Grass Ltd</h1>"
    "<p>Acme Natural Grass Ltd operates the West Field.</p>"
    + FILLER +
    "</body></html>"
).encode("utf-8")

ORG_A = "Example Borough Council"
ORG_B = "North District Council"
EMAIL_A = "grounds@example.org"
EMAIL_B = "north@example.org"


def read_sheet(directory, name):
    with (Path(directory) / f"{name}.csv").open(encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


class P0Case(unittest.TestCase):
    run_id = "p0-run"

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / "data"
        store.start_run(self.root, self.run_id, "GB-synthetic",
                        "RC3 P0 evidence controls", "RC3-RULES")
        store.save_artifact(self.root, self.run_id, "page", "fixture",
                            "https://example.org/directory", PAGE, "rendered_html")

    def put(self, record_id, kind, entity_key, facts, artifacts=("page",)):
        return store.put_candidate(self.root, self.run_id, record_id, "entities",
                                   kind, entity_key, facts, list(artifacts))

    def deliver(self, delivery_id="delivery-p0"):
        return delivery.write_delivery(self.root, self.run_id, delivery_id)

    def decision(self, result, record_id):
        return next(item for item in result["admission"]["decisions"]
                    if item["record_id"] == record_id)

    def org(self, record_id="org-a", entity_key="example-borough", name=ORG_A):
        self.put(record_id, "organization", entity_key,
                 {"name": name,
                  "claims": [{"field": "identity", "artifact_id": "page",
                              "quote": name}]})

    def org_b(self):
        self.put("org-b", "organization", "north-district",
                 {"name": ORG_B,
                  "claims": [{"field": "identity", "artifact_id": "page",
                              "quote": ORG_B}]})


class FieldSupportTests(P0Case):
    def test_organization_name_must_be_in_its_own_identity_quote(self):
        self.put("org-fake", "organization", "invented-council",
                 {"name": "Invented Borough Council",
                  "claims": [{"field": "identity", "artifact_id": "page",
                              "quote": ORG_A}]})
        result = self.deliver()
        decision = self.decision(result, "org-fake")
        self.assertFalse(decision["admitted"])
        self.assertIn("IDENTITY_VALUE_NOT_IN_EXCERPT", decision["reason_codes"])
        self.assertEqual(0, result["counts"]["客户总表"])

    def test_venue_name_must_be_in_its_own_identity_quote(self):
        self.org()
        self.put("venue-fake", "venue", "invented-ground",
                 {"name": "Invented Ground", "organization_id": "example-borough",
                  "claims": [{"field": "identity", "artifact_id": "page",
                              "quote": "Example Sports Ground is operated by Example Borough Council."}]})
        result = self.deliver()
        decision = self.decision(result, "venue-fake")
        self.assertFalse(decision["admitted"])
        self.assertIn("IDENTITY_VALUE_NOT_IN_EXCERPT", decision["reason_codes"])
        self.assertEqual(0, result["counts"]["场地与资产"])

    def test_person_name_needs_its_own_quote_not_just_the_org_page(self):
        self.org()
        self.put("person-fake", "person", "invented-person",
                 {"name": "Invented Person", "department": "Grounds Management",
                  "organization_id": "example-borough",
                  "claims": [{"field": "role_title", "artifact_id": "page",
                              "quote": "Grounds Management at Example Borough Council is the responsible department."}]})
        result = self.deliver()
        decision = self.decision(result, "person-fake")
        self.assertFalse(decision["admitted"])
        self.assertIn("PERSON_NAME_NOT_IN_EXCERPT", decision["reason_codes"])
        self.assertEqual(0, result["counts"]["人员岗位"])

    def test_role_title_cannot_be_invented_from_a_department_quote(self):
        self.org()
        self.put("role-fake", "role", "invented-role",
                 {"department": "Grounds Management", "role_title": "Chief Executive",
                  "organization_id": "example-borough",
                  "claims": [{"field": "department", "artifact_id": "page",
                              "quote": "Grounds Management at Example Borough Council is the responsible department."}]})
        result = self.deliver()
        decision = self.decision(result, "role-fake")
        self.assertFalse(decision["admitted"])
        self.assertIn("ROLE_TITLE_NOT_IN_EXCERPT", decision["reason_codes"])

    def test_a_short_alias_is_not_an_identity(self):
        self.put("org-alias", "organization", "acme-alias",
                 {"name": "Acme",
                  "claims": [{"field": "identity", "artifact_id": "page",
                              "quote": "Acme Natural Grass Ltd operates the West Field."}]})
        result = self.deliver()
        decision = self.decision(result, "org-alias")
        self.assertFalse(decision["admitted"])
        self.assertIn("IDENTITY_VALUE_NOT_IN_EXCERPT", decision["reason_codes"])

    def test_a_partial_name_is_not_the_recorded_entity(self):
        self.put("org-partial", "organization", "acme-partial",
                 {"name": "Acme Natural Grass",
                  "claims": [{"field": "identity", "artifact_id": "page",
                              "quote": "Acme Natural Grass Ltd operates the West Field."}]})
        result = self.deliver()
        decision = self.decision(result, "org-partial")
        self.assertFalse(decision["admitted"])
        self.assertIn("IDENTITY_VALUE_NOT_IN_EXCERPT", decision["reason_codes"])

    def test_an_unrelated_identity_quote_does_not_admit_anything(self):
        self.put("org-unrelated", "organization", "unrelated-council",
                 {"name": "Unrelated Council",
                  "claims": [{"field": "identity", "artifact_id": "page",
                              "quote": "North Depot is operated by North District Council."}]})
        result = self.deliver()
        decision = self.decision(result, "org-unrelated")
        self.assertFalse(decision["admitted"])
        self.assertIn("IDENTITY_VALUE_NOT_IN_EXCERPT", decision["reason_codes"])


class BindingTests(P0Case):
    def test_a_shared_artifact_alone_does_not_bind_a_contact(self):
        self.org()
        self.put("contact-shared", "contact_point", "shared-route",
                 {"organization_id": "example-borough", "type": "email",
                  "value": EMAIL_A,
                  "claims": [{"field": "email", "artifact_id": "page",
                              "quote": "For general enquiries email grounds@example.org."}]})
        result = self.deliver()
        decision = self.decision(result, "contact-shared")
        self.assertFalse(decision["admitted"])
        self.assertIn("ORGANIZATION_PROVENANCE_MISMATCH", decision["reason_codes"])
        self.assertEqual(0, result["counts"]["联系人路线"])

    def test_same_page_two_organisations_keep_their_own_emails(self):
        self.org()
        self.org_b()
        self.put("contact-a", "contact_point", "route-a",
                 {"organization_id": "example-borough", "type": "email",
                  "value": EMAIL_A,
                  "claims": [{"field": "email", "artifact_id": "page",
                              "quote": "Contact Example Borough Council at grounds@example.org for grounds enquiries."}]})
        self.put("contact-b", "contact_point", "route-b",
                 {"organization_id": "north-district", "type": "email",
                  "value": EMAIL_B,
                  "claims": [{"field": "email", "artifact_id": "page",
                              "quote": "Contact North District Council at north@example.org for enquiries."}]})
        self.put("contact-misbound", "contact_point", "route-misbound",
                 {"organization_id": "north-district", "type": "email",
                  "value": EMAIL_A,
                  "claims": [{"field": "email", "artifact_id": "page",
                              "quote": "Contact Example Borough Council at grounds@example.org for grounds enquiries."}]})
        result = self.deliver()
        # Both legitimately bound contacts are admitted, each with its own email;
        # the cross-bound third row is held, so the public channel count is 2 and
        # never 3.
        self.assertEqual(2, result["counts"]["联系人路线"])
        rows = read_sheet(result["directory"], "联系人路线")
        self.assertEqual([EMAIL_A, EMAIL_B], [row["公开字面值"] for row in rows])
        self.assertIn("ORGANIZATION_PROVENANCE_MISMATCH",
                      self.decision(result, "contact-misbound")["reason_codes"])
        self.assertIn("org-a", result["admission"]["admitted_record_ids"])
        self.assertIn("org-b", result["admission"]["admitted_record_ids"])

    def test_a_named_relationship_quote_can_bind_a_contact(self):
        self.org()
        self.put("contact-rel", "contact_point", "route-rel",
                 {"organization_id": "example-borough", "type": "email",
                  "value": EMAIL_A,
                  "claims": [{"field": "email", "artifact_id": "page",
                              "quote": "For general enquiries email grounds@example.org."}]})
        self.put("rel-contact", "relationship", "publishes-contact",
                 {"organization_id": "example-borough",
                  "contact_point_id": "contact-rel",
                  "relationship_type": "publishes",
                  "claims": [{"field": "relationship", "artifact_id": "page",
                              "quote": "Example Borough Council publishes grounds@example.org for grounds enquiries."}]})
        result = self.deliver()
        self.assertEqual(1, result["counts"]["联系人路线"])
        self.assertTrue(self.decision(result, "contact-rel")["admitted"])
        self.assertEqual(["rel-contact"],
                         result["admission"]["admitted"]["relationship"])


class PositiveControls(P0Case):
    def test_a_long_page_still_admits_at_least_three_legitimate_records(self):
        self.org()
        self.put("venue-ok", "venue", "example-sports-ground",
                 {"name": "Example Sports Ground", "organization_id": "example-borough",
                  "claims": [{"field": "identity", "artifact_id": "page",
                              "quote": "Example Sports Ground is operated by Example Borough Council."}]})
        self.put("person-ok", "person", "alex-sample",
                 {"name": "Alex Sample", "role_title": "Site manager",
                  "organization_id": "example-borough",
                  "claims": [{"field": "role_title", "artifact_id": "page",
                              "quote": "Alex Sample is the Site manager at Example Borough Council."}]})
        self.put("contact-ok", "contact_point", "route-ok",
                 {"organization_id": "example-borough", "type": "email",
                  "value": EMAIL_A,
                  "claims": [{"field": "email", "artifact_id": "page",
                              "quote": "Contact Example Borough Council at grounds@example.org for grounds enquiries."}]})
        result = self.deliver()
        self.assertEqual(4, result["admission"]["admitted_record_count"])
        self.assertEqual(0, result["admission"]["rejected_record_count"])
        self.assertEqual(1, result["counts"]["客户总表"])
        self.assertEqual(1, result["counts"]["联系人路线"])
        self.assertEqual(1, result["counts"]["场地与资产"])
        self.assertEqual(1, result["counts"]["人员岗位"])


class StoreCaveatTests(P0Case):
    def test_name_without_identity_warns_at_write_time(self):
        result = store.put_candidate(
            self.root, self.run_id, "org-name-only", "entities", "organization",
            "name-only", {"name": ORG_A,
                          "claims": [{"field": "name", "artifact_id": "page",
                                      "quote": ORG_A}]}, ["page"])
        self.assertTrue(result["caveats"])
        self.assertTrue(result["caveats"][0].startswith("MISSING_REQUIRED_IDENTITY"))
        self.assertIn("field=identity", result["caveats"][0])

    def test_identity_claim_does_not_warn(self):
        result = store.put_candidate(
            self.root, self.run_id, "org-identity", "entities", "organization",
            "identity-ok", {"name": ORG_A,
                            "claims": [{"field": "identity", "artifact_id": "page",
                                        "quote": ORG_A}]}, ["page"])
        self.assertNotIn("caveats", result)

    def test_relationship_without_relationship_field_warns(self):
        result = store.put_candidate(
            self.root, self.run_id, "rel-wrong-field", "entities", "relationship",
            "wrong-field", {"organization_id": "example-borough",
                            "claims": [{"field": "venue_id", "artifact_id": "page",
                                        "quote": "Example Sports Ground is operated by Example Borough Council."}]},
            ["page"])
        self.assertTrue(result["caveats"])
        self.assertTrue(result["caveats"][0].startswith("MISSING_REQUIRED_RELATIONSHIP"))

    def test_batch_reports_the_same_warning_without_rewriting_payload(self):
        facts = {"name": ORG_A,
                 "claims": [{"field": "name", "artifact_id": "page",
                             "quote": ORG_A}]}
        before = json.dumps(facts, sort_keys=True)
        report = store.put_candidates_batch(
            self.root, self.run_id,
            [{"record_id": "batch-org", "stage": "entities", "kind": "organization",
              "entity_key": "batch-org", "facts": facts, "artifact_ids": ["page"]}])
        self.assertTrue(any("batch-org" in caveat and "MISSING_REQUIRED_IDENTITY" in caveat
                            for caveat in report["caveats"]))
        self.assertEqual(before, json.dumps(facts, sort_keys=True),
                         "the writer must not rewrite the caller's evidence")

    def test_a_wrong_field_gets_a_named_repair_instruction(self):
        result = store.put_candidate(
            self.root, self.run_id, "org-wrong-field", "entities", "organization",
            "wrong-field", {"name": ORG_A,
                            "claims": [{"field": "legal_name", "artifact_id": "page",
                                        "quote": ORG_A}]}, ["page"])
        caveat = result["caveats"][0]
        self.assertIn("MISSING_REQUIRED_IDENTITY", caveat)
        self.assertIn("legal_name", caveat)
        self.assertIn("field=identity", caveat)


class AutomaticClientViewTests(P0Case):
    def _seed_single_org(self):
        self.org()
        self.put("venue-ok", "venue", "example-sports-ground",
                 {"name": "Example Sports Ground", "organization_id": "example-borough",
                  "claims": [{"field": "identity", "artifact_id": "page",
                              "quote": "Example Sports Ground is operated by Example Borough Council."}]})
        self.put("contact-ok", "contact_point", "route-ok",
                 {"organization_id": "example-borough", "type": "email",
                  "value": EMAIL_A,
                  "claims": [{"field": "email", "artifact_id": "page",
                              "quote": "Contact Example Borough Council at grounds@example.org for grounds enquiries."}]})

    def test_write_delivery_creates_the_client_view_inside_the_delivery(self):
        self._seed_single_org()
        result = self.deliver()
        view = Path(result["client_view_directory"])
        self.assertTrue((view / "Client_View.xlsx").is_file())
        self.assertTrue((view / "MANIFEST.json").is_file())
        for name in delivery.CLIENT_BUSINESS_SHEETS:
            self.assertTrue((view / f"{name}.csv").is_file(), name)
        client_manifest = json.loads((view / "MANIFEST.json").read_text(encoding="utf-8"))
        self.assertEqual("CANDIDATE_NOT_APPROVED", client_manifest["status"])
        self.assertFalse(client_manifest["send_authorized"])
        self.assertFalse(client_manifest["formal_sop_claimed"])
        self.assertEqual(1, client_manifest["counts"]["客户总表"])

        final = json.loads((Path(result["directory"]) / "MANIFEST.json").read_text(encoding="utf-8"))
        for relative in ("client-view/Client_View.xlsx", "client-view/MANIFEST.json",
                         "client-view/客户总表.csv"):
            self.assertIn(relative, final["files"])
            payload = Path(result["directory"]) / relative
            self.assertEqual(hashlib.sha256(payload.read_bytes()).hexdigest(),
                             final["files"][relative]["sha256"])
        self.assertEqual("client-view", result["client_view"]["directory"])
        self.assertEqual(hashlib.sha256((view / "MANIFEST.json").read_bytes()).hexdigest(),
                         result["client_view"]["manifest_sha256"])
        self.assertTrue(client_view.verify_client_view(view)["files_verified"])
        with zipfile.ZipFile(result["xlsx_path"]) as book:
            self.assertEqual(9, len([name for name in book.namelist()
                                     if name.startswith("xl/worksheets/sheet")
                                     and name.endswith(".xml")]))

    def test_the_handoff_tool_exposes_the_same_client_view(self):
        self._seed_single_org()
        import research_mcp as mcp
        home = Path(self.tmp.name) / "home"
        xdg = Path(self.tmp.name) / "xdg"
        home.mkdir()
        experience = Path(self.tmp.name) / "experience.md"
        with mock.patch.dict(os.environ, {
                "HOME": str(home), "XDG_CONFIG_HOME": str(xdg),
                "MARKETOPS_RESEARCH_DATA_ROOT": str(self.root)}):
            mcp.setup_configure(str(self.root), str(experience))
            result = mcp.handoff(self.run_id, "handoff-p0")
        self.assertEqual("CANDIDATE_HANDOFF_NOT_BUSINESS_VERIFIED", result["status"])
        self.assertTrue(Path(result["client_view_directory"]).is_dir())
        self.assertEqual("CANDIDATE_NOT_APPROVED", result["client_view_status"])
        self.assertFalse(result["client_view_send_authorized"])
        self.assertEqual(64, len(result["client_view_manifest_sha256"]))

    def test_a_failed_client_view_leaves_no_partial_delivery(self):
        self._seed_single_org()
        destination = self.root / "exports" / self.run_id / "delivery-fail"
        with mock.patch.object(client_view, "write_client_view",
                               side_effect=client_view.ClientViewError("SYNTHETIC_FAILURE")):
            with self.assertRaises(client_view.ClientViewError):
                self.deliver("delivery-fail")
        self.assertFalse(destination.exists())
        self.assertFalse(list(destination.parent.glob(".delivery-*")))


if __name__ == "__main__":
    unittest.main()
