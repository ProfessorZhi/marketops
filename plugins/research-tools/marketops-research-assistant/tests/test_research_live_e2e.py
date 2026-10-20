"""Opt-in LIVE real-public-source -> SQLite -> XLSX end-to-end smoke.

No paid provider secrets, no account login, no outreach. This is a publicly
hosted real source read and real business workbook (candidate-only), NOT an
end-to-end Serper/Crawl4AI/TinyFish/Jev billing or formal A/B/C/D test.
"""
from __future__ import annotations

import csv
import os
import sys
import tempfile
import unittest
import urllib.request
import zipfile
from pathlib import Path
from unittest.mock import patch

HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE / "scripts"))
import research_evidence as evidence  # noqa: E402
import research_lite_store as store  # noqa: E402
import research_delivery as delivery  # noqa: E402
import research_mcp as mcp  # noqa: E402


def _local_span(html: bytes, *needles: str, max_chars: int = 600) -> str | None:
    """Return a contiguous, product-normalized span containing every needle.

    The span is taken verbatim from the saved public page. It is not written by
    the test: the product re-reads the same artifact, normalizes it with
    ``research_evidence._text`` and checks the quote is a substring before
    linking it. If the page has no local span that names both sides, this
    returns ``None`` and the caller must record an honest HOLD.
    """
    text = evidence._text({"kind": "raw_response",
                           "text": html.decode("utf-8", errors="replace")})
    folded = text.casefold()
    positions = []
    for needle in needles:
        folded_needle = evidence._norm(needle)
        index = folded.find(folded_needle)
        if index < 0:
            return None
        positions.append((index, index + len(folded_needle)))
    start = min(index for index, _ in positions)
    end = max(stop for _, stop in positions)
    if end - start > max_chars:
        return None
    left, right = start, end
    while left > 0 and text[left - 1] != " " and start - left < 80:
        left -= 1
    while right < len(text) and text[right] != " " and right - end < 120:
        right += 1
    snippet = text[left:right].strip()
    if not 12 <= len(snippet) <= max_chars:
        return None
    if not all(evidence._norm(needle) in snippet.casefold() for needle in needles):
        return None
    return snippet


@unittest.skipUnless(os.environ.get("MARKETOPS_LIVE_PUBLIC_E2E") == "1",
                     "Live public-source testing requires explicit opt-in")
class LivePublicSourceResearchE2E(unittest.TestCase):
    def test_official_council_page_to_excel(self):
        # Verified public authority listing, not a private person's address.
        url = "https://www.lincoln.gov.uk/people-community/community-sport-leisure-facilities/4"
        with urllib.request.urlopen(
            urllib.request.Request(url, headers={
                "User-Agent": "MarketOpsResearchE2ESmoke/0.5 (+public-source-readonly)"
            }), timeout=30
        ) as response:
            self.assertEqual(200, response.status)
            html = response.read(3_000_000)
        literal_email = b"grounds.maintenance@lincoln.gov.uk"
        self.assertIn(literal_email, html.lower(),
                      "Official page changed or blocks retrieval: do not invent an email")
        self.assertIn(b"West Common", html,
                      "Official page changed: do not guess the venue identity")
        with tempfile.TemporaryDirectory() as d:
            root = Path(d) / "marketops-customer-owned"
            env = patch.dict(os.environ, {"MARKETOPS_RESEARCH_DATA_ROOT": str(root),
                             "MARKETOPS_RESEARCH_ALLOW_NETWORK": "0",
                             "MARKETOPS_SEARCH_ALLOW_NETWORK": "0"})
            with env:
                mcp.start("live-lincoln-smoke", "Lincoln-GB-sports",
                          "Research West Common public cricket pitch and grounds maintenance",
                          "no-approved-purchaser-grade")
                store.save_artifact(root, "live-lincoln-smoke", "lincoln-official-page",
                                    "direct_public_https_readonly", url, html, "raw_response")
                mcp.propose("live-lincoln-smoke", "source-official", "discovery", "source",
                            "city-lincoln-community-sport",
                            {"title": "City of Lincoln Council public sports facilities",
                             "url": url, "provider": "official_public_webpage"})
                mcp.propose("live-lincoln-smoke", "city-lincoln", "entities", "organization",
                            "city-lincoln-council",
                            {"name": "City of Lincoln Council",
                             "official_domain": "lincoln.gov.uk",
                             "claims": [{"field": "identity",
                                         "artifact_id": "lincoln-official-page",
                                         "quote": "City of Lincoln Council"}]},
                            ["lincoln-official-page"])
                # A named relationship is only written when the saved page itself
                # contains one contiguous span naming both sides. Nothing is
                # synthesized: the product re-reads these exact bytes.
                org_name = "City of Lincoln Council"
                venue_name = "West Common Cricket Pitch"
                email = "grounds.maintenance@lincoln.gov.uk"
                venue_span = _local_span(html, org_name, venue_name)
                contact_span = _local_span(html, org_name, email)
                if venue_span:
                    mcp.propose("live-lincoln-smoke", "rel-venue", "entities", "relationship",
                                "city-lincoln-operates-west-common",
                                {"organization_id": "city-lincoln-council",
                                 "venue_id": "west-common-cricket",
                                 "relationship_type": "operates",
                                 "claims": [{"field": "relationship",
                                             "artifact_id": "lincoln-official-page",
                                             "quote": venue_span}]},
                                ["lincoln-official-page"])
                if contact_span:
                    mcp.propose("live-lincoln-smoke", "rel-contact", "entities", "relationship",
                                "city-lincoln-publishes-grounds-contact",
                                {"organization_id": "city-lincoln-council",
                                 "contact_point_id": "city-lincoln-grounds-maintenance",
                                 "relationship_type": "publishes",
                                 "claims": [{"field": "relationship",
                                             "artifact_id": "lincoln-official-page",
                                             "quote": contact_span}]},
                                ["lincoln-official-page"])
                mcp.propose("live-lincoln-smoke", "venue-west-common", "entities", "venue",
                            "west-common-cricket",
                            {"name": venue_name,
                             "organization_id": "city-lincoln-council",
                             "claims": [{"field": "identity",
                                         "artifact_id": "lincoln-official-page",
                                         "quote": venue_name},
                                        {"field": "relationship",
                                         "artifact_id": "lincoln-official-page",
                                         "quote": "Currently we only provide a cricket pitch at the West Common."}]},
                            ["lincoln-official-page"])
                mcp.propose("live-lincoln-smoke", "lincoln-grounds-contact", "routes",
                            "contact_point", "city-lincoln-grounds-maintenance",
                            {"organization_id": "city-lincoln-council", "type": "email",
                             "value": email,
                             "department": "Grounds Maintenance",
                             "role_function": "Grounds maintenance",
                             "claims": [{"field": "value",
                                         "artifact_id": "lincoln-official-page",
                                         "quote": email},
                                        {"field": "role_function",
                                         "artifact_id": "lincoln-official-page",
                                         "quote": "Grounds Maintenance Email: " + email}]},
                            ["lincoln-official-page"])
                audit = mcp.evidence_audit("live-lincoln-smoke")
                self.assertEqual({}, audit["gap_counts"])
                self.assertFalse(audit["facts_verified"])
                report = delivery.write_delivery(root, "live-lincoln-smoke", "real-web-e2e")
                self.assertTrue(Path(report["xlsx_path"]).is_file())
                self.assertEqual("ALL_OK", report["evidence_hash_readback"])
                self.assertFalse(report["send_authorized"])
                self.assertEqual(1, report["counts"]["客户总表"])
                with zipfile.ZipFile(report["xlsx_path"]) as f:
                    self.assertIsNone(f.testzip())
                decisions = {item["record_id"]: item
                             for item in report["admission"]["decisions"]}
                client_text = "\n".join(
                    (Path(report["directory"]) / f"{name}.csv").read_text(
                        encoding="utf-8-sig")
                    for name in delivery.CLIENT_BUSINESS_SHEETS)
                if venue_span:
                    self.assertEqual(1, report["counts"]["场地与资产"])
                    self.assertTrue(decisions["venue-west-common"]["admitted"])
                else:
                    # Honest fail-closed negative: the page has no local
                    # org+venue span, so the venue must not be presented as
                    # verified. The organisation itself still passes.
                    self.assertEqual(0, report["counts"]["场地与资产"],
                                     "venue must stay out without a local org+venue span")
                    self.assertIn("ORGANIZATION_PROVENANCE_MISMATCH",
                                  decisions["venue-west-common"]["reason_codes"])
                    self.assertNotIn(venue_name, client_text)
                if contact_span:
                    self.assertEqual(1, report["counts"]["联系人路线"])
                    with (Path(report["directory"]) / "联系人路线.csv").open(
                        encoding="utf-8-sig", newline=""
                    ) as f:
                        contact = next(csv.DictReader(f))
                    self.assertEqual("RENDERED_EMAIL_LITERAL_NOT_BUSINESS_VERIFIED",
                                     contact["来源字面校验"])
                    self.assertEqual("RULES_UNKNOWN", contact["研究评级"])
                else:
                    self.assertEqual(0, report["counts"]["联系人路线"],
                                     "contact must stay out without a local org+email span")
                    self.assertIn("ORGANIZATION_PROVENANCE_MISMATCH",
                                  decisions["lincoln-grounds-contact"]["reason_codes"])
                    self.assertNotIn(email, client_text)


class LocalSpanTests(unittest.TestCase):
    """The live fixture may only write a relationship quote the page really has."""

    def test_a_local_span_contains_every_needle(self):
        html = (b"<html><body><h1>City of Lincoln Council</h1>"
                b"<p>City of Lincoln Council manages the West Common Cricket Pitch "
                b"and publishes grounds.maintenance@lincoln.gov.uk.</p></body></html>")
        span = _local_span(html, "City of Lincoln Council", "West Common Cricket Pitch")
        self.assertIsNotNone(span)
        self.assertIn("city of lincoln council", span)
        self.assertIn("west common cricket pitch", span)
        self.assertTrue(12 <= len(span) <= 600)

    def test_a_missing_value_has_no_span(self):
        html = b"<html><body><h1>City of Lincoln Council</h1><p>No venue here.</p></body></html>"
        self.assertIsNone(_local_span(html, "City of Lincoln Council",
                                      "West Common Cricket Pitch"))

    def test_values_farther_than_the_quote_limit_have_no_span(self):
        filler = "x " * 400
        html = (f"<html><body><h1>City of Lincoln Council</h1><p>{filler}"
                "West Common Cricket Pitch</p></body></html>").encode()
        self.assertIsNone(_local_span(html, "City of Lincoln Council",
                                      "West Common Cricket Pitch"))


if __name__ == "__main__":
    unittest.main()
