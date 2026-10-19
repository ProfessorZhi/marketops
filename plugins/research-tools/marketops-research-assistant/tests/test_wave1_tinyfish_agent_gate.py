"""Paid Agent, browser/Profile and Monitor gating for the TinyFish adapter.

The load-bearing claims here are negative ones, so they are asserted by
observing that *nothing was dispatched*: a refusal that still reached the
vendor would be the most expensive kind of green test.

No socket, key, wallet, browser or provider account is touched: the runner
is an injected async coroutine.
"""
from __future__ import annotations

import os
import sys
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import research_adapter_tinyfish as tinyfish
import _tinyfish_egress_fixture as egress_fixture


WALLET_OK = {
    "available_balance": "25.00",
    "currency": "USD",
    "auto_reload": {"enabled": False},
    "rates": {"meters": [
        {"label": "TinyFish Agent", "unit_amount": "0.016", "per": "step"},
        {"label": "TinyFish Search", "unit_amount": "0"},
    ]},
}
WALLET_DRIFTED = {"balance": 25.0, "auto_reload_enabled": False,
                  "products": [{"name": "agent", "price": "0.016"}]}

PAID_ARGS = {"url": "https://public.example.org/venues",
             "goal": "Read the publicly listed venue names on this page"}
AUTHORIZATION = {"approved_by": "operator@example.test",
                 "scope": "TINYFISH_PAID_AGENT_RUN",
                 "accept_uncapped_provider_spend": True}


class GateTest(unittest.TestCase):
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

    def runner(self, *, wallet=WALLET_OK, agent=None, raises=None,
               wallet_raises=None):
        """`raises` models a failure of the *dispatched* tool only.

        A wallet-read failure is a different event — nothing was submitted —
        so it needs its own switch rather than being folded into the same one.
        """
        async def run(tool, arguments):
            self.calls.append((tool, arguments))
            if tool == "get_wallet":
                if wallet_raises is not None:
                    raise wallet_raises
                return wallet
            if raises is not None:
                raise raises
            return agent if agent is not None else {
                "runId": "run-provider-abc", "status": "PENDING"}
        return run

    def paid(self, **overrides):
        return dict(PAID_ARGS, **overrides)

    def assertRefused(self, code, call):
        with self.assertRaises(ValueError) as err:
            call()
        self.assertEqual(code, str(err.exception))

    @property
    def dispatched(self):
        return [name for name, _ in self.calls]


class DefaultDeny(GateTest):
    def test_paid_agent_is_refused_while_the_project_switch_is_off(self):
        self.assertRefused("TINYFISH_AGENT_PAID_NOT_AUTHORIZED", lambda:
            tinyfish.call("run_web_automation", self.paid(),
                          max_charge_usd="0.50", authorization=AUTHORIZATION,
                          runner=self.runner()))
        self.assertEqual([], self.calls)

    def test_every_capability_is_refused_before_any_network_without_the_gate(self):
        with patch.dict(os.environ, {"MARKETOPS_RESEARCH_ALLOW_NETWORK": "0"}):
            self.assertRefused("TINYFISH_NETWORK_NOT_AUTHORIZED", lambda:
                tinyfish.call("search", {"query": "public venue list"},
                              runner=self.runner()))
        self.assertEqual([], self.calls)

    def test_a_missing_credential_is_refused_before_dispatch(self):
        with patch.dict(os.environ, {"TINYFISH_API_KEY": "",
                                     "TINYFISH_MCP_ACCESS_TOKEN": ""}):
            with patch("research_adapter_tinyfish._keyring_tokens_present",
                       return_value=False):
                self.assertRefused("TINYFISH_CREDENTIAL_REQUIRED", lambda:
                    tinyfish.call("search", {"query": "public venue list"},
                                  runner=self.runner()))
        self.assertEqual([], self.calls)

    def test_described_only_tools_cannot_be_called_through_call(self):
        for tool, args in (("create_monitor", {}), ("list_profiles", {}),
                           ("run_web_automation_async", self.paid())):
            self.assertRefused("TINYFISH_TOOL_DESCRIBED_NOT_ENABLED", lambda
                               tool=tool, args=args:
                               tinyfish.call(tool, args, runner=self.runner()))
        self.assertEqual([], self.calls)


class PaidAgentAuthorization(GateTest):
    def setUp(self):
        super().setUp()
        patch.dict(os.environ,
                   {"MARKETOPS_TINYFISH_AGENT_ALLOW_PAID": "1"}).start()

    def test_an_explicit_human_authorization_is_required(self):
        self.assertRefused("TINYFISH_AGENT_HUMAN_AUTHORIZATION_REQUIRED", lambda:
            tinyfish.call("run_web_automation", self.paid(),
                          max_charge_usd="0.50", runner=self.runner()))
        self.assertEqual([], self.calls)

    def test_an_authorization_without_an_approver_is_not_an_authorization(self):
        self.assertRefused("TINYFISH_AGENT_HUMAN_AUTHORIZATION_REQUIRED", lambda:
            tinyfish.call("run_web_automation", self.paid(),
                          max_charge_usd="0.50",
                          authorization={"scope": "TINYFISH_PAID_AGENT_RUN",
                                         "accept_uncapped_provider_spend": True},
                          runner=self.runner()))
        self.assertEqual([], self.calls)

    def test_an_authorization_for_another_scope_is_refused(self):
        self.assertRefused("TINYFISH_AGENT_HUMAN_AUTHORIZATION_SCOPE_INVALID", lambda:
            tinyfish.call("run_web_automation", self.paid(),
                          max_charge_usd="0.50",
                          authorization=dict(AUTHORIZATION, scope="SERPAPI_SEARCH"),
                          runner=self.runner()))
        self.assertEqual([], self.calls)

    def test_uncapped_provider_spend_must_be_acknowledged_in_so_many_words(self):
        # The vendor publishes no per-call step ceiling, so there is no hard
        # provider stop to rely on. Proceeding without acknowledging that would
        # be advertising a guarantee that does not exist.
        self.assertRefused("TINYFISH_AGENT_PROVIDER_HARD_CAP_NOT_PUBLISHED", lambda:
            tinyfish.call("run_web_automation", self.paid(),
                          max_charge_usd="0.50",
                          authorization={"approved_by": "operator@example.test",
                                         "scope": "TINYFISH_PAID_AGENT_RUN"},
                          runner=self.runner()))
        self.assertEqual([], self.calls)

    def test_a_complete_authorization_submits_exactly_one_run(self):
        out = tinyfish.call("run_web_automation", self.paid(),
                            max_charge_usd="0.50", authorization=AUTHORIZATION,
                            runner=self.runner())
        self.assertEqual(["get_wallet", "run_web_automation"], self.dispatched)
        self.assertTrue(out["ok"])
        self.assertEqual("run-provider-abc", out["agent_run_id"])
        self.assertEqual("SUBMITTED_NOT_TERMINAL", out["agent_lifecycle"])

    def test_ceilings_are_required_and_bounded(self):
        for value, code in (("", "TINYFISH_AGENT_MAX_CHARGE_REQUIRED"),
                            ("abc", "TINYFISH_AGENT_MAX_CHARGE_INVALID"),
                            ("0", "TINYFISH_AGENT_MAX_CHARGE_INVALID"),
                            ("-1", "TINYFISH_AGENT_MAX_CHARGE_INVALID"),
                            ("6", "TINYFISH_AGENT_MAX_CHARGE_INVALID")):
            self.assertRefused(code, lambda value=value:
                tinyfish.call("run_web_automation", self.paid(),
                              max_charge_usd=value, authorization=AUTHORIZATION,
                              runner=self.runner()))
        self.assertEqual([], self.calls)

    def test_a_declared_ceiling_is_recorded_as_an_assertion_not_a_provider_cap(self):
        out = tinyfish.call("run_web_automation", self.paid(),
                            max_charge_usd="0.50", authorization=AUTHORIZATION,
                            runner=self.runner())
        self.assertEqual("0.50", out["max_charge_usd_declared"])
        self.assertEqual("CLIENT_ASSERTION_NOT_PROVIDER_ENFORCED",
                         out["ceiling_enforced_by"])
        self.assertEqual("NOT_PUBLISHED", out["provider_hard_cap"])

    def test_the_wallet_step_rate_above_the_ceiling_stops_submission(self):
        self.assertRefused("TINYFISH_AGENT_SPEND_CAP_EXCEEDED", lambda:
            tinyfish.call("run_web_automation", self.paid(),
                          max_charge_usd="0.01", authorization=AUTHORIZATION,
                          runner=self.runner()))
        self.assertEqual(["get_wallet"], self.dispatched)

    def test_cancel_is_reviewed_but_disabled_by_the_registry(self):
        # B reviews cancel_run at T4 with availability DISABLED, so it is not
        # routable at all -- stronger than a per-operation authorization gate.
        self.assertRefused("TINYFISH_TOOL_DESCRIBED_NOT_ENABLED", lambda:
            tinyfish.call("cancel_run", {"id": "run-provider-abc"},
                          runner=self.runner()))
        self.assertEqual([], self.calls)


class SessionAndProfileBoundaries(GateTest):
    def setUp(self):
        super().setUp()
        patch.dict(os.environ,
                   {"MARKETOPS_TINYFISH_AGENT_ALLOW_PAID": "1"}).start()

    def submit(self, **overrides):
        return tinyfish.call("run_web_automation", self.paid(**overrides),
                             max_charge_usd="0.50",
                             authorization=AUTHORIZATION, runner=self.runner())

    def test_a_caller_supplied_session_id_is_refused(self):
        # Reusing a caller's value would let a replay inherit another
        # submission's browser session instead of minting a fresh one.
        self.assertRefused("TINYFISH_AGENT_SESSION_ID_MUST_BE_GENERATED", lambda:
            self.submit(session_id=str(uuid.uuid4())))
        self.assertEqual([], self.calls)

    def test_each_submission_mints_its_own_fresh_uuid_session(self):
        first = self.submit()
        second = self.submit()
        sent = [args["session_id"] for name, args in self.calls
                if name == "run_web_automation"]
        self.assertEqual(2, len(sent))
        self.assertNotEqual(sent[0], sent[1])
        for value in sent:
            parsed = uuid.UUID(value)
            self.assertEqual(4, parsed.version)
        self.assertEqual(sent[0], first["session_id_used"])
        self.assertEqual(sent[1], second["session_id_used"])

    def test_the_session_id_is_never_echoed_as_provider_evidence(self):
        out = self.submit()
        self.assertNotIn("session_id", out["tool_result"])

    def test_unpublished_agent_parameters_are_refused(self):
        for name, value in (("agent_config", {"max_steps": 4}),
                            ("proxy_config", {"enabled": True}),
                            ("webhook_url", "https://example.test/hook"),
                            ("api_integration", {"id": "x"}),
                            ("feature_flags", {"enable_agent_memory": True})):
            self.assertRefused("TINYFISH_AGENT_UNPUBLISHED_PARAMETER", lambda
                               name=name, value=value:
                               self.submit(**{name: value}))
        self.assertEqual([], self.calls)

    def test_the_goal_must_be_a_real_brief(self):
        self.assertRefused("TINYFISH_AGENT_GOAL_INVALID", lambda: self.submit(goal="read it"))
        self.assertRefused("TINYFISH_AGENT_GOAL_INVALID", lambda: self.submit(goal="x" * 1001))
        self.assertRefused("TINYFISH_AGENT_GOAL_INVALID", lambda: self.submit(goal=42))

    def test_the_agent_target_is_still_subject_to_the_domain_allowlist(self):
        self.assertRefused("OFFICIAL_MCP_TARGET_NOT_ALLOWED", lambda:
                           self.submit(url="https://not-approved.example.com/venues"))
        self.assertRefused("OFFICIAL_MCP_TARGET_NOT_ALLOWED", lambda:
                           self.submit(url="http://public.example.org/venues"))
        self.assertEqual([], self.calls)

    def test_profile_and_vault_use_requires_authorization_this_adapter_lacks(self):
        for name, value in (("use_profile", True), ("profile_id", "prof-1"),
                            ("use_vault", True),
                            ("credential_item_ids", ["cred-1"])):
            self.assertRefused(
                "TINYFISH_SITE_PROFILE_REQUIRES_SEPARATE_AUTHORIZATION", lambda
                name=name, value=value: self.submit(**{name: value}))
        self.assertEqual([], self.calls)

    def test_browser_profile_uses_the_published_uppercase_enum(self):
        self.submit(browser_profile="STEALTH")
        sent = [a for n, a in self.calls if n == "run_web_automation"][-1]
        self.assertEqual("STEALTH", sent["browser_profile"])
        # The fork's snapshot compared against a lowercase literal and would
        # have refused the vendor's own uppercase value.
        self.assertRefused("TINYFISH_AGENT_BROWSER_PROFILE_INVALID", lambda:
                           self.submit(browser_profile="lite"))
        self.assertRefused("TINYFISH_AGENT_BROWSER_PROFILE_INVALID", lambda:
                           self.submit(browser_profile="chrome"))

    def test_capture_config_is_bounded_but_output_schema_is_accepted(self):
        out = self.submit(output_schema={"type": "object",
                                         "properties": {"name": {"type": "string"}}},
                          capture_config={"screenshots": False})
        self.assertTrue(out["ok"])
        self.assertRefused("TINYFISH_ARGUMENTS_TOO_LARGE", lambda:
                           self.submit(capture_config={str(i): i for i in range(40)}))


class WalletDiscipline(GateTest):
    def setUp(self):
        super().setUp()
        patch.dict(os.environ,
                   {"MARKETOPS_TINYFISH_AGENT_ALLOW_PAID": "1"}).start()

    def submit(self, runner):
        return tinyfish.call("run_web_automation", PAID_ARGS,
                             max_charge_usd="0.50",
                             authorization=AUTHORIZATION, runner=runner)

    def test_an_unreadable_wallet_blocks_submission(self):
        self.assertRefused("TINYFISH_AGENT_WALLET_NOT_VERIFIED", lambda:
                           self.submit(self.runner(wallet={"unexpected": True})))
        self.assertEqual(["get_wallet"], self.dispatched)

    def test_a_drifted_wallet_shape_is_refused_rather_than_best_effort_parsed(self):
        # An unrecognised shape might hide a rate we cannot read; parsing it
        # loosely would skip the price check while looking successful.
        self.assertRefused("TINYFISH_AGENT_WALLET_NOT_VERIFIED", lambda:
                           self.submit(self.runner(wallet=WALLET_DRIFTED)))
        self.assertEqual(["get_wallet"], self.dispatched)

    def test_an_empty_wallet_blocks_submission(self):
        empty = dict(WALLET_OK, available_balance="0")
        self.assertRefused("TINYFISH_AGENT_WALLET_EMPTY", lambda:
                           self.submit(self.runner(wallet=empty)))
        self.assertEqual(["get_wallet"], self.dispatched)

    def test_the_wallet_is_read_once_and_never_written(self):
        out = self.submit(self.runner())
        self.assertEqual(1, self.dispatched.count("get_wallet"))
        self.assertEqual(["get_wallet", "run_web_automation"], self.dispatched)
        self.assertFalse(out["wallet"]["auto_reload_enabled"])
        self.assertEqual("ACCOUNT_PROVIDER_REPORTED_UNVERIFIED",
                         out["wallet"]["evidence"])
        # A top-up affordance is a spending path; it never enters our receipt.
        self.assertEqual(
            {"balance", "currency", "auto_reload_enabled",
             "auto_reload_is_an_operator_risk", "agent_step_rate", "evidence"},
            set(out["wallet"]))

    def test_no_top_up_or_recharge_capability_exists_to_be_called(self):
        names = " ".join(tinyfish.capability_catalog()["tools"]).casefold()
        for forbidden in ("top_up", "topup", "recharge", "add_credit", "checkout"):
            self.assertNotIn(forbidden, names)

    def test_auto_reload_state_is_surfaced_but_never_enabled_by_us(self):
        reloading = dict(WALLET_OK, auto_reload={"enabled": True})
        out = self.submit(self.runner(wallet=reloading))
        self.assertTrue(out["wallet"]["auto_reload_enabled"])
        self.assertTrue(out["wallet"]["auto_reload_is_an_operator_risk"])


class ChargedFailureIsNeverRetried(GateTest):
    def setUp(self):
        super().setUp()
        patch.dict(os.environ,
                   {"MARKETOPS_TINYFISH_AGENT_ALLOW_PAID": "1"}).start()

    def submit(self, runner):
        return tinyfish.call("run_web_automation", PAID_ARGS,
                             max_charge_usd="0.50",
                             authorization=AUTHORIZATION, runner=runner)

    def test_a_timeout_after_dispatch_reports_unknown_charge_and_does_not_retry(self):
        out = self.submit(self.runner(raises=TimeoutError("slow")))
        self.assertFalse(out["ok"])
        self.assertEqual("TINYFISH_DISPATCH_OUTCOME_UNKNOWN", out["error"])
        self.assertEqual("UNKNOWN", out["charge_state"])
        self.assertFalse(out["retry_permitted"])
        self.assertFalse(out["provider_charge_verified"])
        # Exactly one submission attempt reached the vendor.
        self.assertEqual(1, self.dispatched.count("run_web_automation"))

    def test_an_ambiguous_task_group_failure_is_also_unknown_charge(self):
        group = ExceptionGroup("mcp", [TimeoutError("stream closed")])
        out = self.submit(self.runner(raises=group))
        self.assertEqual("UNKNOWN", out["charge_state"])
        self.assertEqual(1, self.dispatched.count("run_web_automation"))

    def test_a_failed_wallet_read_is_a_pre_dispatch_refusal(self):
        # The agent was never submitted, so there is no exposure to carry: this
        # must be a refusal, not an UNKNOWN_CHARGE receipt.
        self.assertRefused("TINYFISH_AGENT_WALLET_READ_FAILED", lambda:
                           self.submit(self.runner(wallet_raises=TimeoutError("wallet"))))
        self.assertEqual(["get_wallet"], self.dispatched)

    def test_a_submission_without_a_run_handle_is_unknown_charge_not_a_success(self):
        out = self.submit(self.runner(agent={"status": "PENDING"}))
        self.assertFalse(out["ok"])
        self.assertEqual("TINYFISH_AGENT_RUN_ID_MISSING_AFTER_SUBMISSION", out["error"])
        self.assertEqual("UNKNOWN", out["charge_state"])
        self.assertFalse(out["retry_permitted"])
        self.assertEqual(1, self.dispatched.count("run_web_automation"))

    def test_an_upstream_refusal_is_reported_without_a_second_attempt(self):
        out = self.submit(self.runner(agent={"error": "insufficient credit"}))
        self.assertFalse(out["ok"])
        self.assertEqual("UNKNOWN", out["charge_state"])
        self.assertEqual(1, self.dispatched.count("run_web_automation"))

    def test_a_free_read_that_may_have_left_is_unknown_not_no_charge(self):
        # This used to raise, on the reasoning that a failed search spent
        # nothing. That conflates "cost" with "may have gone out": the call was
        # dispatched, so the honest state is UNKNOWN, not NO_CHARGE_PROVEN.
        async def failing(tool, arguments):
            raise TimeoutError("slow")

        out = tinyfish.call("search", {"query": "public venue list"}, runner=failing)
        self.assertFalse(out["ok"])
        self.assertEqual("UNKNOWN", out["charge_state"])
        self.assertTrue(out["dispatched"])
        self.assertFalse(out["retry_permitted"])
        self.assertIsNone(out["actual_charge_usd"])


class ReadPathsStayFreeAndRepeatable(GateTest):
    def test_run_status_does_not_need_paid_authorization(self):
        out = tinyfish.call("get_run", {"id": "run-provider-abc"},
                            runner=self.runner(agent={"status": "RUNNING"}))
        self.assertEqual(["get_run"], self.dispatched)
        self.assertTrue(out["ok"])
        self.assertEqual("RUNNING", out["agent_status"])
        self.assertEqual("FREE_TIER", out["charge_state"])

    def test_run_status_does_not_mint_a_session_or_an_agent(self):
        for _ in range(3):
            tinyfish.call("get_run", {"id": "run-provider-abc"},
                          runner=self.runner(agent={"status": "COMPLETED"}))
        self.assertEqual(["get_run", "get_run", "get_run"], self.dispatched)
        for name, arguments in self.calls:
            self.assertNotIn("session_id", arguments)
            self.assertNotEqual("run_web_automation", name)

    def test_an_unpublished_status_verb_is_refused(self):
        self.assertRefused("TINYFISH_TOOL_NOT_IN_OFFICIAL_CATALOG", lambda:
            tinyfish.call("wait_for_run", {"run_id": "run-provider-abc"},
                          runner=self.runner()))

    def test_the_wallet_can_be_read_without_the_paid_switch(self):
        out = tinyfish.call("get_wallet", {}, runner=self.runner())
        self.assertEqual("25.00", out["wallet"]["balance"])
        self.assertEqual("FREE_TIER", out["charge_state"])

    def test_schema_drift_denies_an_advertised_tool_that_was_not_reviewed(self):
        async def advertising(tool, arguments):
            raise AssertionError("must not dispatch on drift")

        advertised = sorted(tinyfish.capability_catalog()["tools"]) + ["purge"]
        self.assertRefused("TINYFISH_SCHEMA_DRIFT_REFUSED", lambda:
            tinyfish.call("search", {"query": "public venue list"},
                          runner=advertising, advertised_tools=advertised))

    def test_a_matching_advertisement_does_not_block_the_call(self):
        advertised = sorted(tinyfish.capability_catalog()["tools"])
        out = tinyfish.call("search", {"query": "public venue list"},
                            runner=self.runner(agent={"results": []}),
                            advertised_tools=advertised)
        self.assertTrue(out["ok"])


if __name__ == "__main__":
    unittest.main()
