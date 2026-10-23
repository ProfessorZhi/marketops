"""The curated, lazily-described tool surface, and honest evidence labels.

Two requirements in one place:

1. **Lazy.** A host should be able to learn *which* capabilities exist without
   pulling 28 descriptors -- and the argument contracts, billing notes and
   deferred reasons that come with them -- into the model's context. So there is
   a cheap id digest, a small curated hot set, and a one-at-a-time `describe`.

2. **Separated evidence.** "Coded", "offline tested" and "the real upstream
   agrees" are different facts. Every capability reports the lowest completed
   rung on the contract's ladder (section 1), and nothing may claim
   `UPSTREAM_SCHEMA_VERIFIED` from an offline suite.
"""
from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import research_adapter_tinyfish as tinyfish
import _tinyfish_egress_fixture as egress_fixture

LADDER = ("DOCUMENTED", "CODED", "OFFLINE_TESTED", "UPSTREAM_SCHEMA_VERIFIED",
          "LIVE_VERIFIED", "CLIENT_VERIFIED")


class CuratedHotSet(unittest.TestCase):
    def test_the_curated_set_is_a_small_subset_of_what_is_served(self):
        curated = tinyfish.curated_tools()
        self.assertTrue(curated)
        self.assertLessEqual(len(curated), 5)
        self.assertLess(len(curated), len(tinyfish.capabilities()))
        for capability_id in curated:
            self.assertIn(capability_id, tinyfish.capabilities())

    def test_every_curated_capability_is_free_and_read_only(self):
        tools = tinyfish.capability_catalog()["tools"]
        for capability_id in tinyfish.curated_tools():
            entry = tools[capability_id[len("tinyfish."):]]
            self.assertEqual("READ_ONLY", entry["effect_class"], capability_id)
            self.assertFalse(entry["billing"]["may_charge"], capability_id)
            self.assertIn(entry["tier"], {"T0", "T1"}, capability_id)

    def test_the_paid_capability_is_never_in_the_hot_set(self):
        self.assertNotIn("tinyfish.run_web_automation", tinyfish.curated_tools())


class LazyDescription(unittest.TestCase):
    def test_the_digest_is_cheap_and_carries_no_descriptors(self):
        digest = tinyfish.catalog_digest()
        self.assertNotIn("tools", digest)
        self.assertLess(len(json.dumps(digest)), 1200)
        self.assertEqual(28, digest["tool_count"])
        self.assertRegex(digest["catalog_sha256"], r"^[0-9a-f]{64}$")
        self.assertEqual(sorted(tinyfish.capabilities()), digest["capabilities"])

    def test_one_capability_can_be_described_without_the_rest(self):
        described = tinyfish.describe("tinyfish.get_wallet")
        self.assertEqual("tinyfish.get_wallet", described["capability_id"])
        self.assertEqual("T0", described["tier"])   # B's tier for wallet reads
        self.assertEqual("READ_ONLY", described["effect_class"])
        self.assertIn("published_defaults", described)
        self.assertNotIn("tools", described)

    def test_describing_an_unknown_capability_is_a_closed_set_refusal(self):
        with self.assertRaisesRegex(ValueError, "CAPABILITY_UNKNOWN"):
            tinyfish.describe("tinyfish.purge_account")

    def test_describing_a_disabled_capability_says_it_is_not_available(self):
        with self.assertRaisesRegex(ValueError, "CAPABILITY_NOT_AVAILABLE"):
            tinyfish.describe("tinyfish.create_monitor")

    def test_the_digest_and_the_descriptions_agree_on_the_pin(self):
        digest = tinyfish.catalog_digest()
        self.assertEqual(digest["catalog_sha256"], tinyfish.catalog_sha256())


class EvidenceIsSeparated(unittest.TestCase):
    def test_every_capability_reports_a_ladder_rung_and_none_is_over_claimed(self):
        for entry in tinyfish.capability_catalog()["tools"].values():
            self.assertIn(entry["readiness"], LADDER, entry["capability_id"])
            self.assertLessEqual(LADDER.index(entry["readiness"]),
                                 LADDER.index("OFFLINE_TESTED"),
                                 entry["capability_id"])

    def test_the_evidence_report_states_what_was_not_verified(self):
        report = tinyfish.evidence_report()
        self.assertEqual("OFFLINE_TESTED", report["readiness"])
        self.assertFalse(report["upstream_schema_verified"])
        self.assertFalse(report["live_verified"])
        self.assertFalse(report["client_verified"])
        self.assertTrue(report["blocked_on"])
        self.assertEqual("OFFLINE_RECORDED", report["capture_mode"])
        self.assertFalse(report["live_handshake_performed"])

    def test_the_report_never_claims_an_upstream_hash(self):
        report = tinyfish.evidence_report()
        self.assertIsNone(report["vendor_schema_sha256"])
        self.assertEqual("HOST_SUBSET_NOT_VENDOR_TOOLS_LIST",
                         report["pinned_hash_subject"])

    def test_the_matrix_carries_both_halves_of_the_evidence(self):
        rows = tinyfish.capability_matrix()
        self.assertEqual(28, len(rows))
        for row in rows:
            self.assertEqual("OFFLINE_TESTED", row["offline_tested"])
            self.assertEqual("LIVE-UNVERIFIED", row["live"])


if __name__ == "__main__":
    unittest.main()


class ShippedCallShapes(unittest.TestCase):
    """The shape the shipping caller must send, checked against our contract.

    The shipping path is `.mcp.json` -> `research_mcp.py` ->
    `research_provider_extensions.invoke` -> `research_official_mcp`. I own none
    of those files, so the agreement is pinned here: if this adapter's argument
    contract moves, this test says exactly which shipped call stops matching.
    """

    def setUp(self):
        import os
        egress_fixture.install(self)
        egress_fixture.no_real_dns(self)
        from unittest.mock import patch
        env = patch.dict(os.environ, {
            "MARKETOPS_FETCH_ALLOWED_DOMAINS": "public.example.org"})
        env.start()
        self.addCleanup(env.stop)

    def test_every_shipped_call_shape_matches_our_argument_contract(self):
        self.assertTrue(tinyfish.SHIPPING_CALL_SHAPES)
        for operation, (tool, arguments) in tinyfish.SHIPPING_CALL_SHAPES.items():
            validated = tinyfish.validate(tool, dict(arguments))
            self.assertIsInstance(validated, dict, operation)

    def test_the_shipped_shapes_only_use_capabilities_we_serve(self):
        for tool, _arguments in tinyfish.SHIPPING_CALL_SHAPES.values():
            self.assertIn("tinyfish." + tool, tinyfish.capabilities(), tool)

    def test_the_superseded_wait_shape_is_refused(self):
        # This is what the caller sends today; the patch set changes it to
        # `get_run(id)`. Until then it must stay refused rather than be adapted.
        with self.assertRaisesRegex(ValueError, "TINYFISH_TOOL_NOT_IN_OFFICIAL_CATALOG"):
            tinyfish.validate("wait_for_run", {"run_id": "run-abc"})

    def test_the_fetch_shape_carries_the_published_defaults(self):
        _tool, arguments = tinyfish.SHIPPING_CALL_SHAPES["tinyfish_mcp_fetch"]
        for flag in ("format", "links", "image_links", "page_metadata"):
            self.assertIn(flag, arguments)


if __name__ == "__main__":
    unittest.main()
