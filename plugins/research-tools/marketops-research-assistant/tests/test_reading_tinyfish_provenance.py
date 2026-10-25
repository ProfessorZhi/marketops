"""Keep the three evidence tiers apart, per capability.

This exists because "the catalog says 28 tools" is three different statements
wearing one sentence:

* **official_document** — what the vendor's published page lists. Documentation,
  not verification, and it can be stale.
* **local_declaration** — what this adapter decides to enable and how it types
  the arguments. Ours, offline, reviewable.
* **upstream_tools_list** — what the real account actually advertises. **Not
  obtained**: no handshake has been authorized, so every capability reports
  NOT_OBTAINED and no vendor hash exists.

A reader must never be able to take one tier for another. That is what these
tests pin.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import research_adapter_tinyfish as tinyfish
import _tinyfish_egress_fixture as egress_fixture


class ThreeSeparableTiers(unittest.TestCase):
    def test_every_capability_reports_all_three_tiers(self):
        for capability_id in tinyfish.capabilities(include_unreviewed=True):
            record = tinyfish.capability_provenance(capability_id)
            self.assertEqual({"capability_id", "review_status", "official_document",
                              "local_declaration", "upstream_tools_list"},
                             set(record), capability_id)

    def test_the_upstream_tier_is_not_obtained_for_every_capability(self):
        for capability_id in tinyfish.capabilities(include_unreviewed=True):
            upstream = tinyfish.capability_provenance(capability_id)["upstream_tools_list"]
            self.assertEqual("NOT_OBTAINED", upstream["status"], capability_id)
            self.assertIsNone(upstream["sha256"], capability_id)
            self.assertTrue(upstream["blocked_on"], capability_id)

    def test_the_official_tier_is_documentation_and_says_so(self):
        record = tinyfish.capability_provenance("tinyfish.search")
        official = record["official_document"]
        self.assertEqual("DOCUMENTED_NOT_VERIFIED", official["status"])
        self.assertTrue(official["source"])
        self.assertRegex(official["observed_at"], r"^\d{4}-\d{2}-\d{2}$")
        self.assertTrue(official["claim"])

    def test_the_local_tier_describes_only_what_we_chose(self):
        record = tinyfish.capability_provenance("tinyfish.get_wallet")
        local = record["local_declaration"]
        self.assertTrue(local["enabled"])
        self.assertEqual("T0", local["tier"])   # B's tier for wallet reads
        self.assertEqual("OFFLINE_TESTED", local["readiness"])
        self.assertNotIn("sha256", local)

    def test_described_only_capabilities_report_a_disabled_declaration(self):
        record = tinyfish.capability_provenance("tinyfish.create_monitor")
        self.assertFalse(record["local_declaration"]["enabled"])
        self.assertTrue(record["local_declaration"]["deferred_reason"])

    def test_an_unknown_capability_is_a_closed_set_refusal(self):
        with self.assertRaisesRegex(ValueError, "CAPABILITY_UNKNOWN"):
            tinyfish.capability_provenance("tinyfish.purge_account")

    def test_the_full_audit_covers_the_pinned_catalog(self):
        audit = tinyfish.capability_audit()
        self.assertEqual(28, audit["tool_count"])
        self.assertEqual(28, len(audit["capabilities"]))
        self.assertEqual("OFFLINE_TESTED", audit["readiness"])
        self.assertFalse(audit["upstream_tools_list_obtained"])
        for record in audit["capabilities"].values():
            self.assertEqual("NOT_OBTAINED",
                             record["upstream_tools_list"]["status"])


if __name__ == "__main__":
    unittest.main()


class ChargeTaxonomy(unittest.TestCase):
    """NO_CHARGE_PROVEN means "nothing was described to the vendor".

    It is a claim about *dispatch*, not about price. A refusal before dispatch
    earns it; anything that may have gone out does not, however cheap the
    capability is. UNKNOWN is therefore the label for a dispatched call whose
    outcome is unresolved, metered or not.
    """

    def setUp(self):
        import os
        from unittest.mock import patch
        env = patch.dict(os.environ, {
            "MARKETOPS_FETCH_ALLOWED_DOMAINS": "public.example.org",
            "MARKETOPS_RESEARCH_ALLOW_NETWORK": "1",
            "TINYFISH_API_KEY": "synthetic-tinyfish-key-not-for-network",
            "TINYFISH_MCP_ACCESS_TOKEN": "",
            "MARKETOPS_TINYFISH_AGENT_ALLOW_PAID": "0"})
        env.start()
        self.addCleanup(env.stop)
        egress_fixture.install(self)
        egress_fixture.no_real_dns(self)
        self.calls = []

    def runner(self, *, raises=None, raises_on=None):
        async def run(tool, arguments):
            self.calls.append((tool, arguments))
            if raises is not None and (raises_on is None or raises_on == tool):
                raise raises
            return {"results": []}
        return run

    def request(self, capability_id, params, grant_ref=None):
        class _Req:
            pass
        req = _Req()
        req.capability_id = capability_id
        req.params = params
        req.run_id = "run-1"
        req.operation_id = "op-1"
        req.account_ref = "a"
        req.intent_fingerprint = "i"
        req.grant_ref = grant_ref
        return req

    def test_every_pre_dispatch_refusal_is_no_charge_proven_and_not_dispatched(self):
        cases = (
            ("tinyfish.purge_account", {}),
            ("tinyfish.create_monitor", {}),
            ("tinyfish.search", {"query": "x"}),
            # a served capability refused on its arguments, not a disabled one
            ("tinyfish.list_monitors", {"limit": 10}),
        )
        for capability_id, params in cases:
            out = tinyfish.execute(self.request(capability_id, params),
                                   runner=self.runner())
            self.assertEqual("BLOCKED", out["outcome"], capability_id)
            self.assertEqual("NO_CHARGE_PROVEN", out["charge_state"], capability_id)
            self.assertFalse(out["dispatched"], capability_id)
        self.assertEqual([], self.calls)

    def test_a_dispatched_ambiguity_is_unknown_even_when_it_is_free(self):
        out = tinyfish.execute(
            self.request("tinyfish.search", {"query": "public venue list"}),
            runner=self.runner(raises=TimeoutError("slow")))
        self.assertEqual("UNKNOWN", out["outcome"])
        self.assertEqual("UNKNOWN", out["charge_state"])
        self.assertTrue(out["dispatched"])
        self.assertFalse(out["retry_permitted"])

    def test_a_definite_upstream_answer_on_a_free_capability_is_not_unknown(self):
        async def erroring(tool, arguments):
            raise ValueError("UPSTREAM_MCP_TOOL_FAILED")
        out = tinyfish.execute(
            self.request("tinyfish.search", {"query": "public venue list"}),
            runner=erroring)
        self.assertIn(out["outcome"], {"BLOCKED", "FAILED"})
        self.assertNotEqual("UNKNOWN", out["charge_state"])

    def test_a_wallet_preflight_failure_is_proven_no_charge(self):
        out = tinyfish.execute(
            self.request("tinyfish.run_web_automation",
                         {"url": "https://public.example.org/x",
                          "goal": "Read the publicly listed venue names"},
                         grant_ref="grant-1"),
            runner=self.runner(raises=TimeoutError("wallet"),
                               raises_on="get_wallet"))
        self.assertEqual("BLOCKED", out["outcome"])
        self.assertEqual("WALLET_PRECONDITION_FAILED", out["reason_code"])
        self.assertEqual("NO_CHARGE_PROVEN", out["charge_state"])
        self.assertFalse(out["dispatched"])

