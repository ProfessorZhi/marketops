"""Align the adapter's review/tier/availability claim with B's registry.

B's registry is the allowlist authority (`research_capability_registry.py`
@ 672447de). The table below is transcribed from that file's TinyFish block --
not from an adapter, and not from the vendor.

Three states, not two:

* REVIEWED + ENABLED   -> advertised in `capabilities()` and routable
* REVIEWED + DISABLED  -> reviewed but T4-disabled (cancels): **not** advertised,
                          **not** routable, and **not** "unreviewed"
* UNREVIEWED           -> absent from the registry: never advertised, never routable

`readiness` is never higher than OFFLINE_TESTED and the vendor schema stays
NOT_CAPTURED, whatever the review state says.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import research_adapter_tinyfish as tinyfish

REGISTRY_SOURCE = ("research_capability_registry.py @ 672447de — the TinyFish "
                   "block, transcribed")

#: capability_id -> (tier, availability). Parameters are checked separately
#: because two of them disagree and that disagreement is a reported blocker.
B_REGISTRY = {
    "tinyfish.search": ("T1", "ENABLED"),
    "tinyfish.fetch_content": ("T1", "ENABLED"),
    "tinyfish.get_run": ("T0", "ENABLED"),
    "tinyfish.list_runs": ("T0", "ENABLED"),
    "tinyfish.batch_status": ("T0", "ENABLED"),
    "tinyfish.get_wallet": ("T0", "ENABLED"),
    "tinyfish.get_search_usage": ("T0", "ENABLED"),
    "tinyfish.list_fetch_usage": ("T0", "ENABLED"),
    "tinyfish.get_monitor": ("T1", "ENABLED"),
    "tinyfish.list_monitors": ("T1", "ENABLED"),
    "tinyfish.list_browser_sessions": ("T1", "ENABLED"),
    "tinyfish.run_web_automation": ("T3", "ENABLED"),
    "tinyfish.cancel_run": ("T4", "DISABLED"),
    "tinyfish.batch_cancel": ("T4", "DISABLED"),
}

ENABLED_REVIEWED = {cid for cid, (_t, a) in B_REGISTRY.items() if a == "ENABLED"}
DISABLED_REVIEWED = {cid for cid, (_t, a) in B_REGISTRY.items() if a == "DISABLED"}


class AdvertisementMatchesTheRegistry(unittest.TestCase):
    def test_the_default_advertisement_is_exactly_the_enabled_reviewed_set(self):
        self.assertEqual(ENABLED_REVIEWED, set(tinyfish.capabilities()))

    def test_the_disabled_reviewed_capabilities_are_not_advertised(self):
        for capability_id in DISABLED_REVIEWED:
            self.assertNotIn(capability_id, tinyfish.capabilities())

    def test_disabled_reviewed_is_not_reported_as_unreviewed(self):
        # A T4-disabled capability was reviewed and deliberately not enabled;
        # calling it "unreviewed" would send a reader to the wrong owner.
        for capability_id in DISABLED_REVIEWED:
            record = tinyfish.capability_provenance(capability_id)
            self.assertEqual("REVIEWED_DISABLED", record["review_status"])
            self.assertNotIn(capability_id, tinyfish.unreviewed_capabilities())
            self.assertIn(capability_id, tinyfish.disabled_capabilities())

    def test_every_advertised_capability_is_reviewed(self):
        for capability_id in tinyfish.capabilities(include_unreviewed=True):
            record = tinyfish.capability_provenance(capability_id)
            self.assertIn(record["review_status"],
                          {"REVIEWED", "REVIEWED_DISABLED", "UNREVIEWED"},
                          capability_id)
            self.assertNotEqual("UNREVIEWED", record["review_status"], capability_id)

    def test_the_adapter_tier_and_availability_match_the_registry(self):
        for capability_id, (tier, availability) in B_REGISTRY.items():
            record = tinyfish.capability_provenance(capability_id)
            self.assertEqual(tier, record["local_declaration"]["tier"], capability_id)
            self.assertEqual(availability,
                             record["local_declaration"]["availability"], capability_id)

    def test_the_registry_is_the_authority_and_is_cited(self):
        self.assertIn("672447de", REGISTRY_SOURCE)
        self.assertEqual(set(B_REGISTRY),
                         set(tinyfish.capabilities(include_unreviewed=True))
                         | set(tinyfish.disabled_capabilities()))


class DisabledToolsStayOffTheWire(unittest.TestCase):
    def test_a_disabled_reviewed_capability_is_refused_before_any_dispatch(self):
        calls = []

        async def runner(tool, arguments):
            calls.append((tool, arguments))
            return {}

        class _Req:
            capability_id = "tinyfish.cancel_run"
            params = {"id": "run-abc"}
            run_id = "run-1"
            operation_id = "op-1"
            account_ref = "a"
            intent_fingerprint = "i"
            grant_ref = "grant-1"

        out = tinyfish.execute(_Req(), runner=runner)
        self.assertEqual("BLOCKED", out["outcome"])
        self.assertEqual("CAPABILITY_NOT_AVAILABLE", out["reason_code"])
        self.assertEqual("NO_CHARGE_PROVEN", out["charge_state"])
        self.assertFalse(out["dispatched"])
        self.assertEqual([], calls)


class BrowserSessionParametersAreTheReviewedOnes(unittest.TestCase):
    """R17-D2: the registry exposes `limit`/`page` and nothing else.

    The vendor documents four more parameters. A parameter the allowlist
    authority has not reviewed is not ours to expose, and the refusal must
    happen before any SDK, client, ledger or egress work -- including when
    `execute()` is called directly rather than through the gateway.
    """

    SURPLUS = ({"status": "running"}, {"session_id": "sess-1"},
               {"start_after": "2026-10-18T00:00:00Z"},
               {"end_before": "2026-10-18T00:00:00Z"})

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
        self.calls = []

    def runner(self):
        async def run(tool, arguments):
            self.calls.append((tool, arguments))
            return {}
        return run

    def request(self, params):
        class _Req:
            capability_id = "tinyfish.list_browser_sessions"
            run_id = "run-1"
            operation_id = "op-1"
            account_ref = "a"
            intent_fingerprint = "i"
            grant_ref = None
        req = _Req()
        req.params = params
        return req

    def test_each_surplus_parameter_is_refused_by_validate(self):
        for extra in self.SURPLUS:
            with self.assertRaisesRegex(ValueError, "CAPABILITY_PARAM_NOT_ALLOWED"):
                tinyfish.validate("list_browser_sessions", dict(extra))

    def test_each_surplus_parameter_is_refused_by_a_direct_execute(self):
        for extra in self.SURPLUS:
            out = tinyfish.execute(self.request(dict(extra)), runner=self.runner())
            self.assertEqual("BLOCKED", out["outcome"], extra)
            self.assertEqual("CAPABILITY_PARAM_NOT_ALLOWED", out["reason_code"], extra)
            self.assertEqual("NO_CHARGE_PROVEN", out["charge_state"], extra)
            self.assertFalse(out["dispatched"], extra)
        self.assertEqual([], self.calls)   # no SDK, client, ledger or egress work

    def test_limit_and_page_are_accepted_and_bounded(self):
        self.assertEqual({"limit": 10, "page": 2},
                         tinyfish.validate("list_browser_sessions",
                                           {"limit": 10, "page": 2}))
        out = tinyfish.execute(self.request({"limit": 10, "page": 2}),
                               runner=self.runner())
        self.assertEqual("OK", out["outcome"])
        self.assertEqual([("list_browser_sessions", {"limit": 10, "page": 2})],
                         self.calls)
        for bad in ({"limit": 0}, {"limit": 1001}, {"page": 0}):
            with self.assertRaisesRegex(ValueError,
                                        "TINYFISH_BROWSER_SESSION_ARGS_INVALID"):
                tinyfish.validate("list_browser_sessions", bad)

    def test_get_monitor_requires_its_handle_and_list_monitors_takes_none(self):
        for bad in ({}, {"monitor_id": ""}, {"monitor_id": "x" * 129}):
            with self.assertRaises(ValueError):
                tinyfish.validate("get_monitor", bad)
        self.assertEqual({"monitor_id": "mon-1"},
                         tinyfish.validate("get_monitor", {"monitor_id": "mon-1"}))
        with self.assertRaisesRegex(ValueError, "TINYFISH_ARGUMENTS_INVALID"):
            tinyfish.validate("list_monitors", {"limit": 5})


class CatalogDistinguishesTheThreeStates(unittest.TestCase):
    def test_the_catalog_labels_each_state(self):
        catalog = tinyfish.capability_catalog()["tools"]
        self.assertEqual("REVIEWED", catalog["get_monitor"]["review_status"])
        self.assertEqual("REVIEWED_DISABLED", catalog["cancel_run"]["review_status"])
        self.assertEqual("DISABLED", catalog["cancel_run"]["availability"])
        self.assertFalse(catalog["cancel_run"]["enabled"])

    def test_the_vendor_schema_state_stays_not_captured_whatever_the_review(self):
        report = tinyfish.evidence_report()
        self.assertIsNone(report["vendor_schema_sha256"])
        self.assertFalse(report["schema_verified"])
        self.assertEqual("NOT_CAPTURED", tinyfish.discover_schema(
            names=sorted(tinyfish.capability_catalog()["tools"]))["promotion"])


if __name__ == "__main__":
    unittest.main()
