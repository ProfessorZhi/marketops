"""RC4 gap-driven source selection: local, deterministic, no search.

The scorer is advisory only. These tests pin the priorities the Essex review
asked for: finish near-complete candidates, prefer official rate/contact pages,
never treat a fetch or an attached artifact as a verified field, never reuse a
blocked domain, and never buy or increase budget.
"""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import research_lite_store as store  # noqa: E402
import research_source_selection as selection  # noqa: E402
import research_workflow as workflow  # noqa: E402

BRIEF = {"target_kind": "venue", "goal": "pool", "require_named_person": False}
ORG_BRIEF = {"target_kind": "organization", "goal": "contacts",
             "require_named_person": False}


def row(record_id, kind, entity_key, data, artifacts=()):
    return {"record_id": record_id, "kind": kind, "entity_key": entity_key,
            "data": data, "artifact_ids": list(artifacts)}


def source(record_id, url, *, organization_id=None, status="success"):
    data = {"url": url, "title": record_id, "provider": "synthetic", "status": status}
    if organization_id:
        data["organization_id"] = organization_id
    return row(record_id, "source", record_id, data)


def venue(record_id="venue-1", entity_key="venue-1", **data):
    base = {"name": "Main Hall", "organization_id": "org-1"}
    base.update(data)
    return row(record_id, "venue", entity_key, base)


def org(record_id="org-1", entity_key="org-1", **data):
    base = {"name": "Example Council", "official_domain": "example.org"}
    base.update(data)
    return row(record_id, "organization", entity_key, base)


def bound_contact(record_id="contact-1", value="hall@example.org"):
    return row(record_id, "contact_point", record_id,
               {"organization_id": "org-1", "type": "email", "value": value,
                "claims": [{"field": "value", "artifact_id": "a1",
                            "quote": "Example Council contact " + value}]})


def select(**overrides):
    values = dict(brief=BRIEF, candidates=[], related_by_org={}, org_by_key={},
                  links_by_record={}, sources=[], budget={}, blocked_domains=[])
    values.update(overrides)
    return selection.select_next_sources(**values)


class GapPriorityTests(unittest.TestCase):
    def test_missing_price_prefers_the_official_rate_page(self):
        candidate = venue(claims=[{"field": "identity", "artifact_id": "a1",
                                   "quote": "Example Council operates Main Hall."}])
        result = select(
            candidates=[candidate],
            related_by_org={"org-1": [bound_contact()]},
            org_by_key={"org-1": org()},
            links_by_record={"org-1": {"identity"}, "venue-1": {"identity"},
                             "contact-1": {"value"}},
            sources=[source("rates", "https://example.org/rates"),
                     source("directory", "https://example.org/directory/halls")],
        )
        action = result["candidates"][0]["next_best_source_action"]
        self.assertEqual("hourly_rate", action["field"])
        self.assertEqual("OFFICIAL_RATE_SHEET_OR_BOOKING_PAGE", action["action"])
        self.assertEqual("https://example.org/rates", action["url"])
        self.assertFalse(action["requires_search"])

    def test_missing_email_prefers_the_official_contact_page(self):
        candidate = org(hourly_rate="GBP 45",
                        claims=[{"field": "hourly_rate", "artifact_id": "a1",
                                 "quote": "Our rate is GBP 45 per hour."}])
        result = select(
            brief=ORG_BRIEF, candidates=[candidate],
            org_by_key={"org-1": candidate},
            links_by_record={"org-1": {"identity", "hourly_rate"}},
            sources=[source("contact", "https://example.org/contact-us")],
        )
        action = result["candidates"][0]["next_best_source_action"]
        self.assertEqual("official_email", action["field"])
        self.assertEqual("OFFICIAL_CONTACT_PAGE", action["action"])
        self.assertEqual("https://example.org/contact-us", action["url"])

    def test_blocked_domain_is_excluded_and_never_retried(self):
        candidate = org(official_domain="blocked.example")
        result = select(
            brief=ORG_BRIEF, candidates=[candidate],
            org_by_key={"org-1": candidate},
            links_by_record={"org-1": {"identity"}},
            sources=[source("rates", "https://blocked.example/rates")],
            blocked_domains=["blocked.example"],
        )
        action = result["candidates"][0]["next_best_source_action"]
        self.assertEqual("BLOCKED_DOMAIN_NO_RETRY", action["action"])
        self.assertIsNone(action["url"])
        self.assertFalse(action["requires_search"])
        self.assertIn("blocked.example", action["blocked_domains"])

    def test_unbound_contact_seeks_a_named_relationship(self):
        candidate = org(hourly_rate="GBP 45 per hour")
        contact = row("contact-1", "contact_point", "contact-1",
                      {"organization_id": "org-1", "type": "email",
                       "value": "hall@example.org",
                       "claims": [{"field": "value", "artifact_id": "a1",
                                   "quote": "Main Hall contact hall@example.org"}]})
        result = select(
            brief=ORG_BRIEF, candidates=[candidate],
            org_by_key={"org-1": candidate},
            related_by_org={"org-1": [contact]},
            links_by_record={"org-1": {"identity"}, "contact-1": {"value"}},
            sources=[source("contact", "https://example.org/contact")],
        )
        action = result["candidates"][0]["next_best_source_action"]
        self.assertEqual("contact_binding", action["field"])
        self.assertEqual("NAMED_CONTACT_TO_ORG_RELATIONSHIP", action["action"])

    def test_venue_without_org_span_asks_for_the_linked_venue_page(self):
        candidate = venue()
        result = select(
            candidates=[candidate], org_by_key={"org-1": org()},
            links_by_record={"org-1": {"identity"}, "venue-1": {"identity"}},
            sources=[source("venues", "https://example.org/venues")],
        )
        action = result["candidates"][0]["next_best_source_action"]
        self.assertEqual("venue_org_relationship", action["field"])
        self.assertEqual("OFFICIAL_VENUE_PAGE_LINKED_FROM_ORG", action["action"])

    def test_scarce_budget_prefers_the_near_complete_candidate(self):
        near = org("org-near", "org-near",
                   hourly_rate="GBP 45", official_domain="near.example")
        far = org("org-far", "org-far", official_domain="far.example")
        contact = bound_contact("contact-near")
        contact["data"]["organization_id"] = "org-near"
        contact["data"]["claims"][0]["quote"] = "Near Council contact hall@example.org"
        near["data"]["name"] = "Near Council"
        far["data"]["name"] = "Far Council"
        common = dict(
            brief=ORG_BRIEF, candidates=[near, far],
            related_by_org={"org-near": [contact]},
            org_by_key={"org-near": near, "org-far": far},
            links_by_record={"org-near": {"identity"}, "org-far": set(),
                             "contact-near": {"value"}},
            sources=[],
        )
        scarce = select(**common, budget={"search_remaining": 1})
        self.assertEqual("org-near", scarce["candidates"][0]["entity_key"])
        self.assertEqual("FINISH_EXISTING_CANDIDATES_FIRST",
                         scarce["discovery_policy"])
        normal = select(**common, budget={"search_remaining": 20})
        self.assertEqual("org-far", normal["candidates"][0]["entity_key"])

    def test_unknown_charge_is_held_and_not_retried(self):
        candidate = org()
        result = select(brief=ORG_BRIEF, candidates=[candidate],
                        org_by_key={"org-1": candidate},
                        links_by_record={"org-1": {"identity"}},
                        budget={"unknown_held": 3})
        self.assertEqual("UNKNOWN_CHARGE_HELD_NO_RETRY", result["budget_note"])
        self.assertFalse(result["next_action"]["retry_unknown_charge"])

    def test_zero_budget_never_proposes_a_search(self):
        candidate = org()
        result = select(brief=ORG_BRIEF, candidates=[candidate],
                        org_by_key={"org-1": candidate},
                        links_by_record={"org-1": {"identity"}},
                        budget={"search_remaining": 0})
        action = result["candidates"][0]["next_best_source_action"]
        self.assertEqual("BUDGET_EXHAUSTED_NO_SEARCH", action["action"])
        self.assertFalse(action["requires_search"])
        self.assertIsNone(action["query"])


class EvidenceBoundaryTests(unittest.TestCase):
    def test_fetch_success_or_artifact_is_not_a_verified_field(self):
        candidate = row("org-1", "organization", "org-1",
                        {"name": "Example Council", "official_domain": "example.org"},
                        artifacts=["a1"])
        result = select(
            brief=ORG_BRIEF, candidates=[candidate],
            org_by_key={"org-1": candidate},
            sources=[source("official", "https://example.org/", status="success")],
            links_by_record={},
        )
        fields = result["candidates"][0]["missing_fields"]
        self.assertIn("organization_identity", fields)
        self.assertIn("official_email", fields)
        self.assertEqual("A successful fetch or attached artifact is not a verified "
                         "field; only a linked claim field counts.", result["warning"])

    def test_a_quote_without_a_recorded_link_is_not_evidence(self):
        candidate = org(claims=[{"field": "identity", "artifact_id": "a1",
                                 "quote": "Example Council"}])
        result = select(brief=ORG_BRIEF, candidates=[candidate],
                        org_by_key={"org-1": candidate}, links_by_record={})
        self.assertIn("organization_identity",
                      result["candidates"][0]["missing_fields"])

    def test_shared_directory_page_is_not_used_for_binding(self):
        candidate = org()
        contact = row("contact-1", "contact_point", "contact-1",
                      {"organization_id": "org-1", "type": "email",
                       "value": "hall@example.org"})
        result = select(
            brief=ORG_BRIEF, candidates=[candidate],
            org_by_key={"org-1": candidate},
            related_by_org={"org-1": [contact]},
            links_by_record={"org-1": {"identity"}, "contact-1": {"value"}},
            sources=[source("directory", "https://example.org/directory/halls")],
        )
        action = result["candidates"][0]["next_best_source_action"]
        self.assertEqual("contact_binding", action["field"])
        self.assertIsNone(action["url"])
        self.assertTrue(action["requires_search"])
        self.assertNotEqual("OFFICIAL_DIRECTORY_UNATTRIBUTED", action["attribution"])

    def test_an_explicit_org_link_attributes_a_third_party_rate_page(self):
        candidate = org()
        result = select(
            brief=ORG_BRIEF, candidates=[candidate],
            org_by_key={"org-1": candidate},
            links_by_record={"org-1": {"identity"}},
            sources=[source("rates", "https://hall.example/rates",
                            organization_id="org-1")],
        )
        action = result["candidates"][0]["next_best_source_action"]
        self.assertEqual("hourly_rate", action["field"])
        self.assertEqual("https://hall.example/rates", action["url"])
        self.assertEqual("EXPLICIT_ORG_LINK", action["attribution"])

    def test_the_same_inputs_produce_the_same_advisory(self):
        candidate = org()
        kwargs = dict(brief=ORG_BRIEF, candidates=[candidate],
                      org_by_key={"org-1": candidate},
                      links_by_record={"org-1": {"identity"}},
                      sources=[source("rates", "https://example.org/rates")],
                      budget={"search_remaining": 2})
        first = select(**kwargs)
        second = select(**kwargs)
        self.assertEqual(first, second)

    def test_unattributed_third_party_page_is_not_attribution(self):
        candidate = org()
        result = select(
            brief=ORG_BRIEF, candidates=[candidate],
            org_by_key={"org-1": candidate},
            links_by_record={"org-1": {"identity"}},
            sources=[source("third-party", "https://unrelated.example/contact")],
        )
        action = result["candidates"][0]["next_best_source_action"]
        self.assertNotEqual("https://unrelated.example/contact", action["url"])


class WorkflowExposureTests(unittest.TestCase):
    def test_next_step_exposes_the_advisory_without_network(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "data"
            store.start_run(root, "rc4-run", "synthetic", "objective", "rules")
            store.put_candidate(root, "rc4-run", "brief-1", "qualification",
                                "observation", "brief-1",
                                {"record_type": "research_brief",
                                 "target_kind": "venue", "goal": "pool",
                                 "require_named_person": False,
                                 "minimum_targets": 1,
                                 "minimum_targets_unit": "venue"})
            store.put_candidate(root, "rc4-run", "venue-1", "entities",
                                "venue", "venue-1", {"name": "Synthetic Hall"})
            result = workflow.next_step(root, "rc4-run")
            self.assertIn("source_selection", result)
            advisory = result["source_selection"]
            self.assertEqual("venue", advisory["target_kind"])
            self.assertFalse(advisory["discovery_allowed"])
            self.assertIsNotNone(advisory["next_action"])
            self.assertEqual("marketops.source-selection/1", advisory["schema"])


if __name__ == "__main__":
    unittest.main()
