"""The delivery admission gate: fail-closed per record, independent of audit samples.

Every test uses synthetic names and local bytes; no network, no provider, no key.

The gate is one decision over the *whole* active record set. These cases pin the
four failures that used to reach the customer workbook — a fabricated
organisation/email, an email without a quote, a contact bound to the wrong
institution, and a held venue — plus the two process failures: the audit's
100-sample window and a record-id/entity-key comparison that matched the wrong
subject.
"""
from __future__ import annotations

import csv
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import research_delivery as delivery  # noqa: E402
import research_lite_store as store  # noqa: E402


PAGE_A = (b"<html><body><h1>Real Widgets Ltd</h1>"
          b"<p>Real Widgets Ltd operates the Riverside Works.</p>"
          b"<p>Alex Sample is the Site manager at Real Widgets Ltd.</p>"
          b"<p>Contact Real Widgets Ltd at sales@realwidgets.example for enquiries.</p>"
          b"</body></html>")
PAGE_B = (b"<html><body><h1>Other Corp</h1>"
          b"<p>Other Corp operates the Hill Depot.</p>"
          b"<p>Contact hello@othercorp.example for enquiries.</p></body></html>")

ORG_A_QUOTE = "Real Widgets Ltd"
ORG_B_QUOTE = "Other Corp operates the Hill Depot."
VENUE_QUOTE = "Real Widgets Ltd operates the Riverside Works."
EMAIL_QUOTE = "Contact Real Widgets Ltd at sales@realwidgets.example for enquiries."
PERSON_QUOTE = "Alex Sample is the Site manager at Real Widgets Ltd."
REL_QUOTE = "Real Widgets Ltd operates the Riverside Works."


def read_sheet(directory, name):
    with (Path(directory) / f"{name}.csv").open(encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


class GateCase(unittest.TestCase):
    run_id = "gate-run"

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / "data"
        store.start_run(self.root, self.run_id, "GB-widgets", "Find sourced operators", "rules-v1")
        store.save_artifact(self.root, self.run_id, "page-a", "fixture",
                            "https://example.org/a", PAGE_A, "rendered_html")
        store.save_artifact(self.root, self.run_id, "page-b", "fixture",
                            "https://example.org/b", PAGE_B, "rendered_html")

    def put(self, record_id, kind, entity_key, facts, artifact_ids=None):
        return store.put_candidate(self.root, self.run_id, record_id, "entities",
                                   kind, entity_key, facts, artifact_ids or [])

    def org_a(self, record_id="org-a", entity_key="real-widgets"):
        return self.put(record_id, "organization", entity_key,
                        {"name": "Real Widgets Ltd",
                         "claims": [{"field": "identity", "artifact_id": "page-a",
                                     "quote": ORG_A_QUOTE}]}, ["page-a"])

    def venue_ok(self, record_id="venue-ok", entity_key="riverside-works"):
        return self.put(record_id, "venue", entity_key,
                        {"name": "Riverside Works", "organization_id": "real-widgets",
                         "claims": [{"field": "identity", "artifact_id": "page-a",
                                     "quote": VENUE_QUOTE}]}, ["page-a"])

    def deliver(self, delivery_id="delivery-gate"):
        return delivery.write_delivery(self.root, self.run_id, delivery_id)


class FabricatedEntityTests(GateCase):
    def test_fabricated_org_and_email_never_enter_client_tables(self):
        self.put("org-fake", "organization", "totally-fabricated",
                 {"name": "Totally Fabricated Org",
                  "claims": [{"field": "identity", "artifact_id": "page-a",
                              "quote": "Totally Fabricated Org operates here."}]},
                 ["page-a"])
        self.put("contact-fake", "contact_point", "fabricated-ceo",
                 {"organization_id": "totally-fabricated", "type": "email",
                  "value": "ceo@fabricated.example",
                  "claims": [{"field": "email", "artifact_id": "page-a",
                              "quote": "ceo@fabricated.example is the CEO."}]},
                 ["page-a"])
        result = self.deliver()
        self.assertEqual(0, result["counts"]["客户总表"])
        self.assertEqual(0, result["counts"]["联系人路线"])
        self.assertEqual(0, result["counts"]["场地与资产"])
        self.assertEqual({"org-fake", "contact-fake"},
                         set(result["admission"]["rejected_record_ids"]))
        reasons = result["admission"]["reason_counts"]
        self.assertIn("EXCERPT_NOT_FOUND", reasons)
        self.assertEqual(1, reasons.get("ORGANIZATION_NOT_ADMITTED", 0))
        for sheet in delivery.CLIENT_BUSINESS_SHEETS:
            text = repr(read_sheet(result["directory"], sheet))
            self.assertNotIn("Totally Fabricated Org", text, sheet)
            self.assertNotIn("ceo@fabricated.example", text, sheet)
        issues = read_sheet(result["directory"], "排除与待确认")
        self.assertEqual({"org-fake", "contact-fake"},
                         {row["对象ID"] for row in issues
                          if row["类型"] in {"组织准入", "联系渠道准入"}})
        audit = read_sheet(result["directory"], "候选数据审计")
        self.assertIn("org-fake", [row["记录ID"] for row in audit])
        self.assertIn("contact-fake", [row["记录ID"] for row in audit])

    def test_email_on_page_but_without_a_quote_is_excluded(self):
        self.org_a()
        self.put("contact-noquote", "contact_point", "sales-route",
                 {"organization_id": "real-widgets", "type": "email",
                  "value": "sales@realwidgets.example"}, ["page-a"])
        result = self.deliver()
        self.assertEqual(1, result["counts"]["客户总表"])
        self.assertEqual(0, result["counts"]["联系人路线"])
        decision = next(d for d in result["admission"]["decisions"]
                        if d["record_id"] == "contact-noquote")
        self.assertFalse(decision["admitted"])
        self.assertIn("NO_EXCERPT", decision["reason_codes"])
        # The literal is on the page, so this is specifically the missing quote,
        # not a false "email absent" finding.
        self.assertNotIn("EMAIL_NOT_IN_PRIMARY_MATERIAL", decision["reason_codes"])
        self.assertNotIn("sales@realwidgets.example",
                         repr(read_sheet(result["directory"], "联系人路线")))


class AssociationBindingTests(GateCase):
    def test_contact_bound_to_an_unknown_org_is_excluded(self):
        self.org_a()
        self.put("contact-ghost", "contact_point", "ghost-route",
                 {"organization_id": "ghost-org", "type": "email",
                  "value": "sales@realwidgets.example",
                  "claims": [{"field": "email", "artifact_id": "page-a",
                              "quote": EMAIL_QUOTE}]}, ["page-a"])
        result = self.deliver()
        self.assertEqual(0, result["counts"]["联系人路线"])
        decision = next(d for d in result["admission"]["decisions"]
                        if d["record_id"] == "contact-ghost")
        self.assertIn("ORGANIZATION_NOT_ADMITTED", decision["reason_codes"])

    def test_contact_bound_to_a_different_admitted_org_is_excluded(self):
        self.org_a()
        self.put("org-b", "organization", "other-corp",
                 {"name": "Other Corp",
                  "claims": [{"field": "identity", "artifact_id": "page-b",
                              "quote": ORG_B_QUOTE}]}, ["page-b"])
        # Valid quote from page A, but asserted to belong to Other Corp, whose own
        # evidence is page B and whose name is absent from page A.
        self.put("contact-misbound", "contact_point", "sales-route",
                 {"organization_id": "other-corp", "type": "email",
                  "value": "sales@realwidgets.example",
                  "claims": [{"field": "email", "artifact_id": "page-a",
                              "quote": EMAIL_QUOTE}]}, ["page-a"])
        result = self.deliver()
        self.assertEqual(2, result["counts"]["客户总表"])
        self.assertEqual(0, result["counts"]["联系人路线"])
        decision = next(d for d in result["admission"]["decisions"]
                        if d["record_id"] == "contact-misbound")
        self.assertEqual(["ORGANIZATION_PROVENANCE_MISMATCH"], decision["reason_codes"])

    def test_relationship_with_a_wrong_org_binding_is_excluded(self):
        self.org_a()
        self.put("rel-ok", "relationship", "operator-of",
                 {"organization_id": "real-widgets",
                  "claims": [{"field": "relationship", "artifact_id": "page-a",
                              "quote": REL_QUOTE}]}, ["page-a"])
        self.put("rel-bad", "relationship", "operator-of-ghost",
                 {"organization_id": "ghost-org",
                  "claims": [{"field": "relationship", "artifact_id": "page-a",
                              "quote": REL_QUOTE}]}, ["page-a"])
        result = self.deliver()
        self.assertEqual(1, result["admission"]["admitted_by_kind"]["relationship"])
        decision = next(d for d in result["admission"]["decisions"]
                        if d["record_id"] == "rel-bad")
        self.assertIn("ORGANIZATION_NOT_ADMITTED", decision["reason_codes"])
        issues = read_sheet(result["directory"], "排除与待确认")
        self.assertIn("rel-bad", {row["对象ID"] for row in issues
                                  if row["类型"] == "关联身份准入"})


class DispositionAndVersionTests(GateCase):
    def test_held_venue_is_excluded_while_its_org_remains(self):
        self.org_a()
        self.venue_ok()
        self.put("disp-1", "review", "disp-riverside",
                 {"review_type": "target_disposition", "target_kind": "venue",
                  "target_entity_key": "riverside-works", "disposition": "HOLD",
                  "reason": "ownership not established"}, [])
        result = self.deliver()
        self.assertEqual(1, result["counts"]["客户总表"])
        self.assertEqual(0, result["counts"]["场地与资产"])
        self.assertEqual(["venue-ok"], result["admission"]["rejected_record_ids"])
        decision = next(d for d in result["admission"]["decisions"]
                        if d["record_id"] == "venue-ok")
        self.assertIn("TARGET_DISPOSITION_HOLD", decision["reason_codes"])
        issues = read_sheet(result["directory"], "排除与待确认")
        held = next(row for row in issues
                    if row["类型"] == "场地准入" and row["对象ID"] == "venue-ok")
        self.assertIn("TARGET_DISPOSITION_HOLD", held["问题或原因"])
        self.assertIn("venue-ok", [row["记录ID"] for row in
                                   read_sheet(result["directory"], "候选数据审计")])

    def test_a_version_fork_admits_neither_branch(self):
        self.put("venue-left", "venue", "forked-venue",
                 {"name": "Riverside Works",
                  "claims": [{"field": "identity", "artifact_id": "page-a",
                              "quote": VENUE_QUOTE}]}, ["page-a"])
        self.put("venue-right", "venue", "forked-venue",
                 {"name": "Riverside Works (other)",
                  "claims": [{"field": "identity", "artifact_id": "page-a",
                              "quote": VENUE_QUOTE}]}, ["page-a"])
        result = self.deliver()
        self.assertEqual(0, result["counts"]["场地与资产"])
        self.assertEqual(1, result["version_conflict_count"])
        self.assertEqual(2, result["admission"]["version_conflict_record_count"])
        self.assertEqual({"venue-left", "venue-right"},
                         set(result["admission"]["version_conflict_record_ids"]))
        self.assertEqual("HELD_ROWS_EXCLUDED_FROM_CLIENT_TABLES",
                         result["admission_status"])
        issues = read_sheet(result["directory"], "排除与待确认")
        self.assertIn("候选版本冲突", {row["类型"] for row in issues})


class SampleWindowTests(GateCase):
    def test_bad_records_beyond_the_audit_sample_window_are_all_excluded(self):
        # 120 bad records exhaust the audit's 100-sample window, so the last 20 are
        # invisible to a gate built on `samples`. The admission gate still sees all.
        for index in range(120):
            self.put(f"venue-bad-{index}", "venue", f"bad-venue-{index}",
                     {"name": f"Bad Venue {index}",
                      "claims": [{"field": "identity", "artifact_id": "page-a",
                                  "quote": f"Fabricated venue {index} is not on this page."}]},
                     ["page-a"])
        for index in range(10):
            self.put(f"venue-ok-{index}", "venue", f"ok-venue-{index}",
                     {"name": "Riverside Works",
                      "claims": [{"field": "identity", "artifact_id": "page-a",
                                  "quote": VENUE_QUOTE}]}, ["page-a"])
        result = self.deliver()
        self.assertEqual(10, result["counts"]["场地与资产"])
        self.assertEqual(120, result["admission"]["rejected_record_count"])
        self.assertEqual(130, result["admission"]["evaluated_record_count"])
        self.assertEqual(10, result["admission"]["admitted_by_kind"]["venue"])
        self.assertEqual(120, result["admission"]["rejected_by_kind"]["venue"])
        self.assertIn("venue-bad-119", result["admission"]["rejected_record_ids"])
        # The manifest's audit view is still the small review window; admission is
        # not derived from it.
        self.assertEqual(8, result["evidence_assessment"]["sample_limit"])
        audit_issues = [row for row in read_sheet(result["directory"], "排除与待确认")
                        if row["类型"] == "场地准入"]
        self.assertEqual(120, len(audit_issues))
        self.assertIn("venue-bad-119", {row["对象ID"] for row in audit_issues})
        client_rows = read_sheet(result["directory"], "场地与资产")
        self.assertEqual(10, len(client_rows))
        self.assertNotIn("Bad Venue", repr(client_rows))


class CountAndCollisionTests(GateCase):
    def test_quality_sample_fills_tables_and_counts_match_the_files(self):
        self.org_a()
        self.venue_ok()
        self.put("person-1", "person", "alex-sample",
                 {"name": "Alex Sample", "role_title": "Site manager",
                  "organization_id": "real-widgets",
                  "claims": [{"field": "role_title", "artifact_id": "page-a",
                              "quote": PERSON_QUOTE}]},
                 ["page-a"])
        self.put("contact-1", "contact_point", "sales-route",
                 {"organization_id": "real-widgets", "type": "email",
                  "value": "sales@realwidgets.example",
                  "claims": [{"field": "email", "artifact_id": "page-a",
                              "quote": EMAIL_QUOTE}]}, ["page-a"])
        result = self.deliver()
        self.assertEqual(4, result["admission"]["admitted_record_count"])
        for name, count in result["counts"].items():
            self.assertEqual(count, len(read_sheet(result["directory"], name)), name)
        self.assertEqual({"organization": 1, "venue": 1, "person": 1, "contact_point": 1},
                         result["admission"]["admitted_by_kind"])
        self.assertEqual("ALL_CLIENT_ROWS_ADMITTED", result["admission_status"])

    def test_record_id_is_never_compared_against_another_records_entity_key(self):
        # The org's entity key deliberately equals the venue's record id. The old
        # `any(v[1] == key for v in issues)` compared those two namespaces and
        # marked the clean org REVIEW_REQUIRED because a *venue record* failed.
        self.org_a(entity_key="shared-key")
        self.put("shared-key", "venue", "bad-venue",
                 {"name": "Bad Venue", "organization_id": "real-widgets"}, ["page-a"])
        result = self.deliver()
        org_row = next(row for row in read_sheet(result["directory"], "客户总表")
                       if row["组织ID"] == "shared-key")
        self.assertEqual("PENDING_SOP_QUALIFICATION", org_row["未解决事项"])
        self.assertIn("shared-key", result["admission"]["rejected_record_ids"])
        self.assertEqual(1, result["admission"]["admitted_by_kind"]["organization"])


class ProvenanceSpanTests(GateCase):
    def test_a_quote_without_a_recorded_span_is_excluded(self):
        self.org_a()
        import sqlite3
        with sqlite3.connect(self.root / "state" / "research-lite.sqlite3") as db:
            db.execute("DELETE FROM evidence_links WHERE record_id='org-a'")
            db.commit()
        result = self.deliver()
        self.assertEqual(0, result["counts"]["客户总表"])
        decision = next(d for d in result["admission"]["decisions"]
                        if d["record_id"] == "org-a")
        self.assertIn("SPAN_NOT_RECORDED", decision["reason_codes"])


class SourceTypeTests(GateCase):
    def test_derived_material_cannot_anchor_a_business_record(self):
        store.save_artifact(self.root, self.run_id, "derived-a", "fixture",
                            "https://example.org/a", b'{"markdown": "Real Widgets Ltd"}',
                            "provider_extracted")
        self.put("org-derived", "organization", "derived-widgets",
                 {"name": "Real Widgets Ltd",
                  "claims": [{"field": "identity", "artifact_id": "derived-a",
                              "quote": "Real Widgets Ltd"}]}, ["derived-a"])
        result = self.deliver()
        self.assertEqual(0, result["counts"]["客户总表"])
        decision = next(d for d in result["admission"]["decisions"]
                        if d["record_id"] == "org-derived")
        self.assertIn("SOURCE_TYPE_NOT_ADMISSIBLE:provider_extracted",
                      decision["reason_codes"])


if __name__ == "__main__":
    unittest.main()
