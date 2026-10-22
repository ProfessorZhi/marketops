"""RC5: rate, phone and contact binding need real linked quotes.

A raw `hourly_rate` field, a phone record with no linked quote, or a generic
`relationship` link is not evidence. These controls keep those values in
`missing_fields` and keep the advisory pointing at an official rate/contact page.

Synthetic, offline material only.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import research_source_selection as selection  # noqa: E402

BRIEF = {"target_kind": "organization", "goal": "contacts",
         "require_named_person": False,
         "evidence_requirements": {"organization": ["identity", "hourly_rate", "phone"]}}


def row(record_id, kind, entity_key, data, artifacts=()):
    return {"record_id": record_id, "kind": kind, "entity_key": entity_key,
            "data": data, "artifact_ids": list(artifacts)}


def org(**data):
    base = {"name": "Example Council", "official_domain": "example.org"}
    base.update(data)
    return row("org-1", "organization", "org-1", base)


def source(record_id, url):
    return row(record_id, "source", record_id,
               {"url": url, "title": record_id, "provider": "synthetic"})


def contact(kind="email", value="hall@example.org", **data):
    base = {"organization_id": "org-1", "type": kind, "value": value}
    base.update(data)
    return row("contact-1", "contact_point", "contact-1", base)


def select(candidate, *, related=None, links=None, sources=None):
    return selection.select_next_sources(
        brief=BRIEF, candidates=[candidate], org_by_key={"org-1": candidate},
        related_by_org={"org-1": related or []},
        links_by_record=links or {"org-1": {"identity"}},
        sources=sources or [source("rates", "https://example.org/rates"),
                            source("contact", "https://example.org/contact")],
        budget={}, blocked_domains=[])


def missing(result):
    return result["candidates"][0]["missing_fields"]


class RateEvidenceTests(unittest.TestCase):
    def test_a_raw_rate_without_a_claim_is_missing(self):
        result = select(org(hourly_rate="GBP 45 per hour"))
        self.assertIn("hourly_rate", missing(result))
        action = result["candidates"][0]["next_best_source_action"]
        self.assertEqual("hourly_rate", action["field"])
        self.assertEqual("OFFICIAL_RATE_SHEET_OR_BOOKING_PAGE", action["action"])

    def test_a_rate_claim_without_an_evidence_link_is_missing(self):
        candidate = org(hourly_rate="GBP 45",
                        claims=[{"field": "hourly_rate", "artifact_id": "a1",
                                 "quote": "Our rate is GBP 45 per hour."}])
        result = select(candidate, links={"org-1": {"identity"}})
        self.assertIn("hourly_rate", missing(result))

    def test_a_linked_rate_quote_without_the_number_is_missing(self):
        candidate = org(hourly_rate="GBP 45",
                        claims=[{"field": "hourly_rate", "artifact_id": "a1",
                                 "quote": "Competitive rates are available."}])
        result = select(candidate, links={"org-1": {"identity", "hourly_rate"}})
        self.assertIn("hourly_rate", missing(result))

    def test_a_linked_rate_quote_with_the_number_is_covered(self):
        candidate = org(hourly_rate="GBP 45",
                        claims=[{"field": "hourly_rate", "artifact_id": "a1",
                                 "quote": "Our rate is GBP 45 per hour."}])
        result = select(candidate, links={"org-1": {"identity", "hourly_rate"}})
        self.assertNotIn("hourly_rate", missing(result))


class PhoneEvidenceTests(unittest.TestCase):
    def test_a_phone_record_without_a_linked_quote_is_missing(self):
        result = select(org(), related=[contact("phone", "01234 567890")])
        self.assertIn("phone", missing(result))

    def test_a_phone_quote_without_org_binding_is_missing(self):
        phone = contact("phone", "01234 567890",
                        claims=[{"field": "value", "artifact_id": "a1",
                                 "quote": "Call 01234 567890"}])
        result = select(org(), related=[phone],
                        links={"org-1": {"identity"}, "contact-1": {"value"}})
        self.assertIn("phone", missing(result))

    def test_a_phone_quote_with_org_binding_is_covered(self):
        phone = contact("phone", "01234 567890",
                        claims=[{"field": "value", "artifact_id": "a1",
                                 "quote": "Example Council call 01234 567890"}])
        result = select(org(), related=[phone],
                        links={"org-1": {"identity"}, "contact-1": {"value"}})
        self.assertNotIn("phone", missing(result))

    def test_a_named_relationship_record_can_bind_a_phone(self):
        phone = contact("phone", "01234 567890",
                        claims=[{"field": "value", "artifact_id": "a1",
                                 "quote": "Call 01234 567890"}])
        relationship = row("rel-1", "relationship", "rel-1", {
            "organization_id": "org-1", "contact_point_id": "contact-1",
            "claims": [{"field": "relationship", "artifact_id": "a1",
                        "quote": "Example Council phone 01234 567890"}]})
        result = select(org(), related=[phone, relationship],
                        links={"org-1": {"identity"},
                               "contact-1": {"value"},
                               "rel-1": {"relationship"}})
        self.assertNotIn("phone", missing(result))


class ContactBindingEvidenceTests(unittest.TestCase):
    def test_a_generic_relationship_link_does_not_bind_a_contact(self):
        email = contact(claims=[{"field": "value", "artifact_id": "a1",
                                 "quote": "Contact hall@example.org"},
                                {"field": "relationship", "artifact_id": "a1",
                                 "quote": "See our contact page"}])
        result = select(org(), related=[email],
                        links={"org-1": {"identity"},
                               "contact-1": {"value", "relationship"}})
        self.assertIn("contact_binding", missing(result))

    def test_a_relationship_quote_naming_both_sides_binds_a_contact(self):
        email = contact(claims=[{"field": "value", "artifact_id": "a1",
                                 "quote": "Contact hall@example.org"},
                                {"field": "relationship", "artifact_id": "a1",
                                 "quote": "Example Council contact hall@example.org"}])
        result = select(org(), related=[email],
                        links={"org-1": {"identity"},
                               "contact-1": {"value", "relationship"}})
        self.assertNotIn("contact_binding", missing(result))


if __name__ == "__main__":
    unittest.main()
