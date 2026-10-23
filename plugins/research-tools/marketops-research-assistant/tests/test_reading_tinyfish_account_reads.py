"""Safe read-only account/usage coverage, and the schema that makes it usable.

Two things are being pinned here, and both were real coverage losses rather than
missing features:

1. **The published argument defaults were demanded instead of applied.** The
   vendor documents `get_search_usage(limit=100)`, `list_fetch_usage(limit=20)`
   and `list_browser_sessions(limit=100, page=1)` with defaults, so every one of
   those calls is legal with no arguments at all. This adapter required them,
   which turned a legal upstream call into our refusal.

2. **A read-only wallet read was all-or-nothing.** `get_wallet` failed outright
   when the account's payload was not the shape we happened to expect, so the
   safest account method in the catalog was unusable on any account whose
   wallet report is spelled differently. The *strict* parse is still required
   where it protects money (the paid-Agent preflight); for the read itself the
   honest answer is to report what the account said and mark the shape.

No network, credential or provider account is touched.
"""
from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import research_adapter_tinyfish as tinyfish
import _tinyfish_egress_fixture as egress_fixture

CATALOG = sorted(tinyfish.capability_catalog()["tools"])

# The three read-only account/usage capabilities this file is about.
ACCOUNT_READS = ("get_search_usage", "list_fetch_usage", "get_wallet")
# Read-only account inventory: no charge, no credentials, no private sites.
ACCOUNT_INVENTORY = ("list_monitors", "get_monitor", "list_browser_sessions")
# Still T4: reading profiles reads account credentials (INTERFACE-CONTRACT §2).
CREDENTIAL_READS = ("list_profiles",)


class AccountReadBase(unittest.TestCase):
    def setUp(self):
        self.calls = []
        egress_fixture.install(self)
        egress_fixture.no_real_dns(self)
        env = patch.dict(os.environ, {
            "MARKETOPS_FETCH_ALLOWED_DOMAINS": "public.example.org",
            "MARKETOPS_RESEARCH_ALLOW_NETWORK": "1",
            "TINYFISH_API_KEY": "synthetic-tinyfish-key-not-for-network",
            "TINYFISH_MCP_ACCESS_TOKEN": "",
            "MARKETOPS_TINYFISH_AGENT_ALLOW_PAID": "0",
        })
        env.start()
        self.addCleanup(env.stop)

    def runner(self, payloads=None):
        async def run(tool, arguments):
            self.calls.append((tool, arguments))
            return (payloads or {}).get(tool, {})
        return run


class PublishedDefaultsAreAppliedNotDemanded(AccountReadBase):
    def defaults(self, tool):
        return tinyfish.PUBLISHED_DEFAULTS.get(tool, {})

    def test_the_vendor_defaults_are_declared_on_the_capability(self):
        self.assertEqual({"limit": 100, "page": 1}, self.defaults("get_search_usage"))
        self.assertEqual({"limit": 20, "page": 1}, self.defaults("list_fetch_usage"))
        self.assertEqual({"limit": 100, "page": 1}, self.defaults("list_browser_sessions"))

    def test_an_account_read_with_no_arguments_is_legal(self):
        for tool in ("get_search_usage", "list_fetch_usage", "get_wallet"):
            self.assertEqual({}, tinyfish.validate(tool, {}), tool)

    def test_the_documented_defaults_are_sent_explicitly(self):
        for tool, expected in (("get_search_usage", 100), ("list_fetch_usage", 20),
                               ("list_browser_sessions", 100)):
            self.calls.clear()
            tinyfish.call(tool, {}, runner=self.runner())
            sent = dict(self.calls)[tool]
            self.assertEqual(expected, sent["limit"], tool)
            self.assertEqual(1, sent["page"], tool)

    def test_an_explicit_limit_still_wins_and_is_still_bounded(self):
        tinyfish.call("get_search_usage", {"limit": 7}, runner=self.runner())
        self.assertEqual(7, dict(self.calls)["get_search_usage"]["limit"])
        with self.assertRaisesRegex(ValueError, "TINYFISH_USAGE_ARGS_INVALID"):
            tinyfish.validate("get_search_usage", {"limit": 1001})
        with self.assertRaisesRegex(ValueError, "TINYFISH_USAGE_ARGS_INVALID"):
            tinyfish.validate("list_fetch_usage", {"limit": 101})

    def test_an_unpublished_argument_is_still_refused(self):
        with self.assertRaisesRegex(ValueError, "TINYFISH_USAGE_ARGS_INVALID"):
            tinyfish.validate("get_search_usage", {"cursor": "x"})
        with self.assertRaisesRegex(ValueError, "CAPABILITY_PARAM_NOT_ALLOWED"):
            tinyfish.validate("list_browser_sessions", {"engine": "google"})


class AccountInventoryIsReadOnlyAndEnabled(AccountReadBase):
    def test_the_read_only_account_inventory_is_available(self):
        for tool in ACCOUNT_INVENTORY:
            entry = tinyfish.capability_catalog()["tools"][tool]
            self.assertTrue(entry["enabled"], tool)
            self.assertTrue(entry["callable"], tool)
            self.assertEqual("T1", entry["tier"], tool)
            self.assertEqual("READ_ONLY", entry["effect_class"], tool)
            self.assertFalse(entry["billing"]["may_charge"], tool)
            self.assertEqual("ENABLED", entry["availability"], tool)
            self.assertEqual("", entry["deferred_reason"], tool)

    def test_enabling_them_does_not_widen_capabilities_beyond_the_published_verbs(self):
        self.assertEqual(
            {"tinyfish." + n for n in
             set(tinyfish.READ_TOOLS) | set(tinyfish.T3_TOOLS)},
            set(tinyfish.capabilities(include_unreviewed=True)))
        # B's registry reviews these three as T1 READ_ONLY, so they are
        # advertised. Reviewed is not the same as enabled or live.
        for tool in ACCOUNT_INVENTORY:
            self.assertIn("tinyfish." + tool, tinyfish.capabilities())
            self.assertNotIn("tinyfish." + tool, tinyfish.unreviewed_capabilities())
            self.assertNotIn("tinyfish." + tool, tinyfish.disabled_capabilities())

    def test_reading_profiles_stays_tier_four_and_disabled(self):
        # INTERFACE-CONTRACT §2 is explicit: reading profile state reads account
        # credentials, so the effective tier is the maximum, not the average.
        for tool in CREDENTIAL_READS:
            entry = tinyfish.capability_catalog()["tools"][tool]
            self.assertFalse(entry["enabled"], tool)
            self.assertEqual("T4", entry["tier"], tool)
            with self.assertRaisesRegex(ValueError, "TINYFISH_TOOL_DESCRIBED_NOT_ENABLED"):
                tinyfish.validate(tool, {})

    def test_the_billable_and_mutating_monitor_and_session_tools_stay_disabled(self):
        for tool in ("create_monitor", "run_monitor", "resume_monitor", "pause_monitor",
                     "cancel_monitor", "create_browser_session",
                     "close_browser_session", "create_profile",
                     "start_profile_setup_session", "save_profile_setup_session",
                     "cancel_profile_setup_session", "run_web_automation_async"):
            entry = tinyfish.capability_catalog()["tools"][tool]
            self.assertFalse(entry["enabled"], tool)
            self.assertNotIn("tinyfish." + tool, tinyfish.capabilities())

    def test_get_monitor_reaches_only_the_named_monitor(self):
        tinyfish.call("get_monitor", {"monitor_id": "mon-abc_1"}, runner=self.runner())
        self.assertEqual([("get_monitor", {"monitor_id": "mon-abc_1"})], self.calls)
        for bad in ({}, {"monitor_id": ""}, {"monitor_id": "x" * 129},
                    {"monitor_id": "a", "extra": 1}, {"id": "mon-1"}):
            with self.assertRaises(ValueError):
                tinyfish.validate("get_monitor", bad)

    def test_list_monitors_takes_no_arguments(self):
        self.assertEqual({}, tinyfish.validate("list_monitors", {}))
        with self.assertRaisesRegex(ValueError, "TINYFISH_ARGUMENTS_INVALID"):
            tinyfish.validate("list_monitors", {"limit": 10})

    def test_an_account_read_needs_no_grant_but_is_still_tier_one(self):
        result = tinyfish.execute(
            _request("tinyfish.list_monitors", {}), runner=self.runner())
        self.assertEqual("OK", result["outcome"])
        self.assertEqual("FREE_TIER", result["charge_state"])
        self.assertIsNone(result["actual_usd"])


class WalletReadIsTolerantWhileThePreflightStaysStrict(AccountReadBase):
    RECOGNISED = {"available_balance": "25.00", "currency": "USD",
                  "auto_reload": {"enabled": False},
                  "rates": {"meters": [{"label": "TinyFish Agent",
                                        "unit_amount": "0.016"}]}}
    # A plausible upstream re-spelling: same facts, different names.
    RESPELT = {"balance": 25.0, "auto_reload_enabled": False,
               "products": [{"name": "agent", "price": "0.016"}]}

    def test_the_read_reports_a_recognised_wallet_as_recognised(self):
        result = tinyfish.call("get_wallet", {}, runner=self.runner(
            {"get_wallet": self.RECOGNISED}))
        self.assertEqual("25.00", result["wallet"]["balance"])
        self.assertTrue(result["wallet"]["shape_recognized"])

    def test_the_read_still_returns_when_the_shape_is_unrecognised(self):
        # This is the coverage fix: the safest account method in the catalog
        # used to be unusable on any account whose wallet is spelled differently.
        result = tinyfish.call("get_wallet", {}, runner=self.runner(
            {"get_wallet": self.RESPELT}))
        self.assertTrue(result["ok"])
        self.assertEqual("ACCOUNT_PROVIDER_REPORTED_UNVERIFIED",
                         result["wallet"]["evidence"])
        self.assertFalse(result["wallet"]["shape_recognized"])
        self.assertIsNone(result["wallet"]["balance"])
        # It says so in the payload too, so no reader has to guess.
        self.assertIn("25", str(result["tool_result"]["balance"]))

    def test_an_unrecognised_read_never_claims_a_verified_balance(self):
        result = tinyfish.call("get_wallet", {}, runner=self.runner(
            {"get_wallet": self.RESPELT}))
        self.assertFalse(result["provider_charge_verified"])
        self.assertEqual("FREE_TIER", result["charge_state"])
        self.assertIn("WALLET_SHAPE_UNRECOGNIZED", result["caveats"])

    def test_a_non_dict_wallet_payload_is_still_a_read_not_a_crash(self):
        result = tinyfish.call("get_wallet", {}, runner=self.runner({"get_wallet": "n/a"}))
        self.assertTrue(result["ok"])
        self.assertFalse(result["wallet"]["shape_recognized"])

    def test_the_paid_preflight_remains_strict_on_the_very_same_shape(self):
        # Same payload the tolerant read just accepted must still stop the
        # submission, because there the parse is what checks the price.
        with patch.dict(os.environ, {"MARKETOPS_TINYFISH_AGENT_ALLOW_PAID": "1"}):
            with self.assertRaisesRegex(ValueError, "TINYFISH_AGENT_WALLET_NOT_VERIFIED"):
                tinyfish.call(
                    "run_web_automation",
                    {"url": "https://public.example.org/x",
                     "goal": "Read the publicly listed venue names"},
                    max_charge_usd="0.50",
                    authorization={"approved_by": "op@example.test",
                                   "scope": "TINYFISH_PAID_AGENT_RUN",
                                   "accept_uncapped_provider_spend": True},
                    runner=self.runner({"get_wallet": self.RESPELT}))
        self.assertEqual(["get_wallet"], [n for n, _ in self.calls])

    def test_auto_reload_state_is_surfaced_on_both_shapes(self):
        reloading = dict(self.RECOGNISED, auto_reload={"enabled": True})
        result = tinyfish.call("get_wallet", {}, runner=self.runner(
            {"get_wallet": reloading}))
        self.assertTrue(result["wallet"]["auto_reload_enabled"])
        self.assertTrue(result["wallet"]["auto_reload_is_an_operator_risk"])


class TargetGuardSplit(AccountReadBase):
    """The guard is not ours; the *split* between shape and resolution is.

    On the integration tip `research_official_mcp._approved_url` gained
    ``*, resolve: bool = True`` and its docstring fixes the intended order:
    "the shape-only form runs during argument validation", the resolving form
    after the authorization checks. Argument validation must therefore stay
    offline-pure, and the resolving form runs at the pre-dispatch gate.
    """

    def guard(self, calls, *, supports_resolve=True):
        def guard(value, *, resolve=False):
            calls.append((value, resolve))
            if resolve and value.endswith("/blocked"):
                raise ValueError("TARGET_RESOLVES_NON_GLOBAL: loopback IPv4")
            return value
        if supports_resolve:
            return guard

        def shape_only(value):
            calls.append((value, None))
            return value
        return shape_only

    def test_the_seam_declares_one_source_and_no_fallback(self):
        # W-A's guard is the one the egress proxy itself uses, and it is the
        # *only* one. A fallback to a second implementation would let the weaker
        # rule decide without anything reporting the divergence.
        self.assertEqual((("research_egress", "approved_url"),),
                         tuple(tinyfish.URL_GUARD_SOURCES))

    def test_validation_asks_for_the_shape_only_form(self):
        calls = []
        with patch.object(tinyfish, "_url_guard", lambda: self.guard(calls)):
            tinyfish.validate("fetch_content", {"urls": ["https://public.example.org/a"]})
        self.assertEqual([("https://public.example.org/a", False)], calls)

    def test_the_pre_dispatch_gate_asks_for_the_resolving_form(self):
        calls = []
        with patch.object(tinyfish, "_url_guard", lambda: self.guard(calls)):
            tinyfish.call("fetch_content", {"urls": ["https://public.example.org/a"]},
                          runner=self.runner())
        self.assertEqual([("https://public.example.org/a", False),
                          ("https://public.example.org/a", True)], calls)

    def test_the_resolving_form_runs_once_per_target_and_only_once(self):
        calls = []
        with patch.object(tinyfish, "_url_guard", lambda: self.guard(calls)):
            tinyfish.call("fetch_content",
                          {"urls": ["https://public.example.org/a",
                                    "https://public.example.org/b"]},
                          runner=self.runner())
        self.assertEqual(
            [("https://public.example.org/a", True), ("https://public.example.org/b", True)],
            [c for c in calls if c[1] is True])

    def test_a_resolution_refusal_is_a_pre_dispatch_refusal(self):
        # `research_target_guard` refuses a name whose answer is inward. Nothing
        # was dispatched, so this must not be carried as charge exposure.
        with patch.object(tinyfish, "_url_guard", lambda: self.guard([])):
            with self.assertRaisesRegex(ValueError, "TARGET_RESOLVES_NON_GLOBAL"):
                tinyfish.call("fetch_content",
                              {"urls": ["https://public.example.org/blocked"]},
                              runner=self.runner())
        self.assertEqual([], self.calls)

    def test_a_resolution_refusal_reaches_the_gateway_as_a_closed_set_code(self):
        with patch.object(tinyfish, "_url_guard", lambda: self.guard([])):
            result = tinyfish.execute(
                _request("tinyfish.fetch_content",
                         {"urls": ["https://public.example.org/blocked"]}),
                runner=self.runner())
        self.assertEqual("BLOCKED", result["outcome"])
        self.assertEqual("EGRESS_REFUSED", result["reason_code"])
        self.assertEqual("NO_CHARGE_PROVEN", result["charge_state"])
        self.assertEqual([], self.calls)

    def test_a_guard_without_the_keyword_is_called_positionally(self):
        # An older checkout's guard predates the split and is shape-only, so the
        # resolving form cannot be requested there; asking anyway would raise.
        calls = []
        with patch.object(tinyfish, "_url_guard",
                          lambda: self.guard(calls, supports_resolve=False)):
            tinyfish.call("fetch_content", {"urls": ["https://public.example.org/a"]},
                          runner=self.runner())
        self.assertEqual([("https://public.example.org/a", None),
                          ("https://public.example.org/a", None)], calls)


def _request(capability_id, params, grant_ref=None):
    class _Req:
        pass
    req = _Req()
    req.capability_id = capability_id
    req.params = params
    req.run_id = "run-1"
    req.operation_id = "op-1"
    req.account_ref = "acct-1"
    req.intent_fingerprint = "intent-1"
    req.grant_ref = grant_ref
    return req


if __name__ == "__main__":
    unittest.main()
