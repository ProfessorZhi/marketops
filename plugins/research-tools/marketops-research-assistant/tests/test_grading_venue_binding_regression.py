"""Venue ownership suggestions require named, linked evidence (offline)."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import research_source_selection as selection  # noqa: E402


def row(record_id, kind, entity_key, data):
    return {"record_id": record_id, "kind": kind, "entity_key": entity_key,
            "data": data, "artifact_ids": []}


def claim(field, quote):
    return {"field": field, "artifact_id": "artificial-source", "quote": quote}


class VenueOwnershipTests(unittest.TestCase):
    def fields(self, venue_claims, venue_fields, edges=(), edge_fields=()):
        venue = row("venue-id", "venue", "venue-key", {
            "name": "Main Hall", "organization_id": "council",
            "claims": venue_claims})
        council = row("council-id", "organization", "council", {
            "name": "Example Council", "official_domain": "example.org"})
        links = {"venue-id": set(venue_fields), "council-id": {"identity"}}
        for edge in edges:
            links[edge["record_id"]] = set(edge_fields)
        result = selection.select_next_sources(
            brief={"target_kind": "venue", "goal": "pool", "require_named_person": False},
            candidates=[venue], org_by_key={"council": council},
            related_by_org={"council": list(edges)}, links_by_record=links,
            sources=[], budget={"search_remaining": 2})
        return result["candidates"][0]["missing_fields"]

    def test_mismatched_linked_relationship_stays_missing(self):
        f = self.fields([
            claim("identity", "Main Hall is a community venue."),
            claim("relationship", "Beta Council operates Other Hall.")],
            {"identity", "relationship"})
        self.assertIn("venue_org_relationship", f)

    def test_named_but_unlinked_relationship_stays_missing(self):
        f = self.fields([
            claim("identity", "Main Hall is a community venue."),
            claim("relationship", "Example Council operates Main Hall.")],
            {"identity"})
        self.assertIn("venue_org_relationship", f)

    def test_linked_identity_quote_names_both_and_is_valid(self):
        f = self.fields([claim("identity", "Example Council operates Main Hall.")],
                        {"identity"})
        self.assertNotIn("venue_org_relationship", f)

    def test_linked_named_relationship_quote_is_valid(self):
        f = self.fields([
            claim("identity", "Main Hall is a community venue."),
            claim("relationship", "Example Council operates Main Hall.")],
            {"identity", "relationship"})
        self.assertNotIn("venue_org_relationship", f)

    def test_valid_explicit_relationship_record_is_accepted(self):
        edge = row("relation-id", "relationship", "relation", {
            "organization_id": "council", "venue_id": "venue-id",
            "claims": [claim("relationship", "Example Council operates Main Hall.")]})
        f = self.fields([claim("identity", "Main Hall is a community venue.")],
                        {"identity"}, [edge], {"relationship"})
        self.assertNotIn("venue_org_relationship", f)

    def test_wrong_reference_in_relationship_record_is_rejected(self):
        edge = row("relation-id", "relationship", "relation", {
            "organization_id": "council", "venue_id": "other-venue",
            "claims": [claim("relationship", "Example Council operates Main Hall.")]})
        f = self.fields([claim("identity", "Main Hall is a community venue.")],
                        {"identity"}, [edge], {"relationship"})
        self.assertIn("venue_org_relationship", f)

    def test_wrong_organisation_in_relationship_record_is_rejected(self):
        edge = row("relation-id", "relationship", "relation", {
            "organization_id": "council", "venue_id": "venue-id",
            "claims": [claim("relationship", "Beta Council operates Main Hall.")]})
        f = self.fields([claim("identity", "Main Hall is a community venue.")],
                        {"identity"}, [edge], {"relationship"})
        self.assertIn("venue_org_relationship", f)


if __name__ == "__main__":
    unittest.main()
