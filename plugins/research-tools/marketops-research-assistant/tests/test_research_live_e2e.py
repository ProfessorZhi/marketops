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
import research_lite_store as store  # noqa: E402
import research_delivery as delivery  # noqa: E402
import research_mcp as mcp  # noqa: E402


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
                             "official_domain": "lincoln.gov.uk"}, ["lincoln-official-page"])
                mcp.propose("live-lincoln-smoke", "venue-west-common", "entities", "venue",
                            "west-common-cricket",
                            {"name": "West Common Cricket Pitch",
                             "organization_id": "city-lincoln-council"}, ["lincoln-official-page"])
                mcp.propose("live-lincoln-smoke", "lincoln-grounds-contact", "routes",
                            "contact_point", "city-lincoln-grounds-maintenance",
                            {"organization_id": "city-lincoln-council", "type": "email",
                             "value": "grounds.maintenance@lincoln.gov.uk",
                             "department": "Grounds Maintenance",
                             "role_function": "Grounds maintenance"},
                            ["lincoln-official-page"])
                report = delivery.write_delivery(root, "live-lincoln-smoke", "real-web-e2e")
                self.assertTrue(Path(report["xlsx_path"]).is_file())
                self.assertEqual("ALL_OK", report["evidence_hash_readback"])
                self.assertFalse(report["send_authorized"])
                self.assertEqual(1, report["counts"]["客户总表"])
                self.assertEqual(1, report["counts"]["场地与资产"])
                with zipfile.ZipFile(report["xlsx_path"]) as f:
                    self.assertIsNone(f.testzip())
                with (Path(report["directory"]) / "联系人路线.csv").open(
                    encoding="utf-8-sig", newline=""
                ) as f:
                    contact = next(csv.DictReader(f))
                self.assertEqual("LITERAL_IN_SAVED_MATERIAL", contact["来源字面校验"])
                self.assertEqual("RULES_UNKNOWN", contact["研究评级"])


if __name__ == "__main__":
    unittest.main()
