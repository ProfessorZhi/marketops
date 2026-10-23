"""Wave-1 integration: the composed managed choke point (W-INTEGRATOR).

These tests are about the *composition*, not about any single worker's module.
They assert the things nobody can assert alone: that a reservation exists before
any adapter is dispatched, that an unroutable capability never reaches policy,
that a transport never enters the request, that a policy or ledger answering
outside the frozen closed set is a contract violation rather than a prose excuse.

Owned by W-INTEGRATOR. Offline only: no network, no credentials, no provider.
"""
from __future__ import annotations

import dataclasses
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import _wave1_contract_fixture as fixture  # noqa: E402
import research_gateway as gateway  # noqa: E402

# Every reason code the contract's §5.3 closed set pins, transcribed verbatim
# from INTERFACE-CONTRACT.md v1.0 (base b2bcdcd).
FROZEN_REASON_CODES = frozenset({
    "POLICY_TIER_NOT_GRANTED", "POLICY_DOMAIN_NOT_ALLOWED", "POLICY_GRANT_EXPIRED",
    "POLICY_GRANT_REVOKED", "BUDGET_RUN_CEILING", "BUDGET_PROVIDER_CEILING",
    "BUDGET_PROJECT_CEILING", "BUDGET_OPERATION_CEILING", "PRICE_UNKNOWN_FAIL_CLOSED",
    "PRICING_SOURCE_MISSING", "DUPLICATE_OPERATION", "OPERATION_ID_COLLISION",
    "HOST_PREVIOUSLY_BLOCKED", "CAPABILITY_UNKNOWN", "CAPABILITY_PARAM_NOT_ALLOWED",
    "CAPABILITY_SCHEMA_DRIFT", "CAPABILITY_NOT_AVAILABLE", "EGRESS_REFUSED",
    "CRAWL4AI_LOCAL_NOT_INSTALLED", "UNKNOWN_CHARGE_OUTSTANDING",
    "RESEARCH_NETWORK_DISABLED", "WALLET_PRECONDITION_FAILED", "GRANT_SIGNATURE_INVALID",
})

REQUEST_FIELDS = {"capability_id", "params", "run_id", "operation_id",
                  "account_ref", "intent_fingerprint", "grant_ref"}


def build(*, entries=None, adapter=None, contract=fixture, recorder=None,
          ledger=None, policy=None, adapters=None):
    rec = recorder or fixture.Recorder()
    return gateway.CapabilityGateway(
        registry=fixture.FakeRegistry(entries or [fixture.entry()], rec),
        policy=policy or fixture.FakePolicy(recorder=rec),
        ledger=ledger or fixture.FakeLedger(recorder=rec),
        adapters=adapters if adapters is not None else {
            "tinyfish": adapter or fixture.ProbeAdapter(
                "tinyfish", ["tinyfish.search"], recorder=rec)},
        contract=contract,
        contract_module_name="research_contract",
        # The translation between the contract's request and the ledger's own.
        # A real deployment gets this from `research_wiring.ledger_request_builder`
        # (which is the only module that knows the project and the vocabulary
        # bridge); here the fixture ledger accepts the contract request directly,
        # so the translation is the identity.
        ledger_request=lambda entry, request: request,
    )


def call(gw, **over):
    args = dict(capability_id="tinyfish.search", params={"query": "paint"},
                run_id="run-1", operation_id="op-1", account_ref="acct-1",
                intent_fingerprint="intent-1")
    args.update(over)
    return gw.invoke(**args)


class RegistryGateTests(unittest.TestCase):
    def test_unknown_capability_is_blocked_before_policy_ledger_or_adapter(self):
        rec = fixture.Recorder()
        adapter = fixture.ProbeAdapter("tinyfish", ["tinyfish.search"], recorder=rec)
        gw = build(adapter=adapter, recorder=rec)
        out = call(gw, capability_id="tinyfish.does_not_exist")
        self.assertEqual("BLOCKED", out.status)
        self.assertEqual("CAPABILITY_UNKNOWN", out.reason_code)
        self.assertNotIn("policy.evaluate", rec.kinds())
        self.assertNotIn("ledger.reserve", rec.kinds())
        self.assertNotIn("adapter.execute", rec.kinds())

    def test_param_outside_allowed_subset_is_blocked_and_nothing_is_reserved(self):
        rec = fixture.Recorder()
        gw = build(adapter=fixture.ProbeAdapter("tinyfish", ["tinyfish.search"], recorder=rec),
                   recorder=rec)
        out = call(gw, params={"query": "paint", "max_pages": 999})
        self.assertEqual(("BLOCKED", "CAPABILITY_PARAM_NOT_ALLOWED"),
                         (out.status, out.reason_code))
        self.assertNotIn("ledger.reserve", rec.kinds())

    def test_non_mapping_params_are_blocked_not_crashed(self):
        gw = build()
        out = call(gw, params=["query"])
        self.assertEqual(("BLOCKED", "CAPABILITY_PARAM_NOT_ALLOWED"),
                         (out.status, out.reason_code))

    def test_agent_supplied_tier_endpoint_and_tool_name_are_ignored(self):
        rec = fixture.Recorder()
        gw = build(entries=[fixture.entry(tier=fixture.Tier.T0)], recorder=rec)
        out = call(gw, agent_claims={"tier": "T3", "endpoint": "https://evil.example/mcp",
                                     "tool_name": "run_web_automation", "host": "evil.example"})
        self.assertEqual("OK", out.status)
        self.assertEqual(("endpoint", "host", "tier", "tool_name"), out.ignored_agent_claims)

    def test_agent_claims_never_reach_the_request_handed_to_policy(self):
        policy = fixture.FakePolicy()
        gw = build(policy=policy)
        call(gw, agent_claims={"tier": "T4", "effect_class": "STATE_CHANGING"})
        self.assertEqual(REQUEST_FIELDS, {f.name for f in dataclasses.fields(policy.seen[0])})

    def test_schema_drift_blocks_tier_two_and_above(self):
        gw = build(entries=[fixture.entry(tier=fixture.Tier.T2,
                                          availability=fixture.Availability.SCHEMA_DRIFT)])
        out = call(gw)
        self.assertEqual(("BLOCKED", "CAPABILITY_SCHEMA_DRIFT"), (out.status, out.reason_code))

    def test_schema_drift_still_allows_a_tier_one_read(self):
        gw = build(entries=[fixture.entry(tier=fixture.Tier.T1,
                                          availability=fixture.Availability.SCHEMA_DRIFT)])
        self.assertEqual("OK", call(gw).status)

    def test_disabled_capability_is_blocked(self):
        gw = build(entries=[fixture.entry(availability=fixture.Availability.DISABLED)])
        out = call(gw)
        self.assertEqual(("BLOCKED", "CAPABILITY_NOT_AVAILABLE"), (out.status, out.reason_code))

    def test_auth_required_capability_is_blocked_not_attempted(self):
        gw = build(entries=[fixture.entry(availability=fixture.Availability.AUTH_REQUIRED)])
        out = call(gw)
        self.assertEqual(("BLOCKED", "CAPABILITY_NOT_AVAILABLE"), (out.status, out.reason_code))


class RoutingGateTests(unittest.TestCase):
    def test_provider_without_adapter_is_blocked_and_no_budget_is_held(self):
        rec = fixture.Recorder()
        gw = build(adapters={}, recorder=rec)
        out = call(gw)
        self.assertEqual(("BLOCKED", "CAPABILITY_NOT_AVAILABLE"), (out.status, out.reason_code))
        self.assertNotIn("ledger.reserve", rec.kinds())
        self.assertNotIn("policy.evaluate", rec.kinds())

    def test_adapter_not_advertising_the_capability_is_blocked_not_silently_routed(self):
        rec = fixture.Recorder()
        gw = build(adapter=fixture.ProbeAdapter("tinyfish", ["tinyfish.fetch"], recorder=rec),
                   recorder=rec)
        out = call(gw)
        self.assertEqual(("BLOCKED", "CAPABILITY_NOT_AVAILABLE"), (out.status, out.reason_code))
        self.assertNotIn("adapter.execute", rec.kinds())

    def test_a_reservation_exists_before_any_adapter_is_dispatched(self):
        rec = fixture.Recorder()
        gw = build(adapter=fixture.ProbeAdapter("tinyfish", ["tinyfish.search"], recorder=rec),
                   recorder=rec)
        out = call(gw)
        self.assertEqual("OK", out.status)
        kinds = rec.kinds()
        self.assertLess(kinds.index("ledger.reserve"), kinds.index("adapter.execute"))
        self.assertLess(kinds.index("policy.evaluate"), kinds.index("ledger.reserve"))
        self.assertLess(kinds.index("adapter.execute"), kinds.index("ledger.settle"))

    def test_the_orchestrator_never_asks_the_adapter_for_a_policy_decision(self):
        adapter = fixture.AskableAdapter("tinyfish", ["tinyfish.search"])
        gw = build(adapter=adapter)
        self.assertEqual("OK", call(gw).status)
        self.assertEqual(1, len(adapter.executed))


class PolicyGateTests(unittest.TestCase):
    def test_policy_deny_prevents_reservation_and_dispatch(self):
        rec = fixture.Recorder()
        ledger = fixture.FakeLedger(recorder=rec)
        adapter = fixture.ProbeAdapter("tinyfish", ["tinyfish.search"], recorder=rec)
        gw = build(adapters={"tinyfish": adapter}, ledger=ledger,
                   policy=fixture.FakePolicy(fixture.PolicyDecision(
                       verdict="DENY", reason_code="POLICY_TIER_NOT_GRANTED",
                       effective_tier=fixture.Tier.T1), rec), recorder=rec)
        out = call(gw)
        self.assertEqual(("BLOCKED", "POLICY_TIER_NOT_GRANTED"), (out.status, out.reason_code))
        self.assertEqual([], ledger.reserved)
        self.assertEqual([], adapter.executed)

    def test_require_human_grant_is_blocked_not_executed(self):
        gw = build(policy=fixture.FakePolicy(fixture.PolicyDecision(
            verdict="REQUIRE_HUMAN_GRANT", reason_code="POLICY_TIER_NOT_GRANTED",
            effective_tier=fixture.Tier.T4)))
        out = call(gw)
        self.assertEqual("BLOCKED", out.status)

    def test_policy_reason_code_outside_the_closed_set_is_a_contract_violation(self):
        gw = build(policy=fixture.FakePolicy(fixture.PolicyDecision(
            verdict="DENY", reason_code="because I said so",
            effective_tier=fixture.Tier.T1)))
        with self.assertRaises(gateway.ContractViolation):
            call(gw)

    def test_unknown_policy_verdict_is_a_contract_violation(self):
        gw = build(policy=fixture.FakePolicy(fixture.PolicyDecision(
            verdict="MAYBE", reason_code="CAPABILITY_UNKNOWN", effective_tier=fixture.Tier.T1)))
        with self.assertRaises(gateway.ContractViolation):
            call(gw)


class LedgerGateTests(unittest.TestCase):
    def test_ledger_refusal_is_blocked_with_its_closed_set_code_and_no_dispatch(self):
        rec = fixture.Recorder()
        adapter = fixture.ProbeAdapter("tinyfish", ["tinyfish.search"], recorder=rec)
        gw = build(adapters={"tinyfish": adapter},
                   ledger=fixture.FakeLedger(recorder=rec,
                                             refuse=ValueError("BUDGET_RUN_CEILING: exhausted")),
                   recorder=rec)
        out = call(gw)
        self.assertEqual(("BLOCKED", "BUDGET_RUN_CEILING"), (out.status, out.reason_code))
        self.assertEqual([], adapter.executed)

    def test_ledger_refusal_outside_the_closed_set_is_a_contract_violation(self):
        gw = build(ledger=fixture.FakeLedger(refuse=ValueError("disk went sideways")))
        with self.assertRaises(gateway.ContractViolation):
            call(gw)

    def test_reservation_without_a_ledger_id_is_a_contract_violation(self):
        ledger = fixture.FakeLedger()

        def reserve(req, decision):
            return dataclasses.replace(ledger.reserve(req, decision), ledger_id="")

        ledger.reserve = reserve  # type: ignore[assignment]
        gw = build(ledger=ledger)
        with self.assertRaises(gateway.ContractViolation):
            call(gw)

    def test_account_ref_and_intent_fingerprint_reach_the_ledger_verbatim(self):
        ledger = fixture.FakeLedger()
        gw = build(ledger=ledger)
        call(gw, account_ref="acct-42", intent_fingerprint="intent-42")
        req = ledger.reserved[0]
        self.assertEqual(("acct-42", "intent-42"), (req.account_ref, req.intent_fingerprint))


class DispatchOutcomeTests(unittest.TestCase):
    def test_success_settles_exactly_once_and_never_marks_unknown(self):
        rec = fixture.Recorder()
        ledger = fixture.FakeLedger(recorder=rec)
        adapter = fixture.ProbeAdapter("tinyfish", ["tinyfish.search"], recorder=rec)
        gw = build(adapters={"tinyfish": adapter}, ledger=ledger, recorder=rec)
        out = call(gw)
        self.assertEqual("OK", out.status)
        self.assertEqual(1, len(ledger.settled))
        self.assertEqual([], ledger.unknowns)
        self.assertEqual(1, len(adapter.executed))

    def test_returned_unknown_outcome_holds_the_exposure_and_does_not_retry(self):
        rec = fixture.Recorder()
        ledger = fixture.FakeLedger(recorder=rec)
        adapter = fixture.ProbeAdapter("tinyfish", ["tinyfish.search"],
                                       behaviour="unknown", recorder=rec)
        gw = build(adapters={"tinyfish": adapter}, ledger=ledger, recorder=rec)
        out = call(gw)
        self.assertEqual(("UNKNOWN", "UNKNOWN_CHARGE_OUTSTANDING"),
                         (out.status, out.reason_code))
        self.assertEqual(1, len(ledger.unknowns))
        self.assertIn(ledger.unknowns[0][1], {"PROVIDER_TIMEOUT", "AMBIGUOUS_RESPONSE",
                                              "PROVIDER_ERROR_UNVERIFIED"})
        self.assertEqual([], ledger.settled)
        self.assertEqual(1, len(adapter.executed))

    def test_unclassified_failure_after_reserve_holds_the_exposure_and_does_not_retry(self):
        rec = fixture.Recorder()
        ledger = fixture.FakeLedger(recorder=rec)
        adapter = fixture.ProbeAdapter("tinyfish", ["tinyfish.search"],
                                       behaviour="raise_unknown", recorder=rec)
        gw = build(adapters={"tinyfish": adapter}, ledger=ledger, recorder=rec)
        out = call(gw)
        self.assertEqual(("UNKNOWN", "UNKNOWN_CHARGE_OUTSTANDING"),
                         (out.status, out.reason_code))
        self.assertEqual(1, len(ledger.unknowns))
        self.assertIn(ledger.unknowns[0][1], {"PROVIDER_TIMEOUT", "AMBIGUOUS_RESPONSE",
                                              "PROVIDER_ERROR_UNVERIFIED"})
        self.assertEqual([], ledger.settled)
        self.assertEqual(1, len(adapter.executed))

    def test_provable_local_refusal_settles_as_blocked_and_does_not_retry(self):
        rec = fixture.Recorder()
        ledger = fixture.FakeLedger(recorder=rec)
        adapter = fixture.ProbeAdapter("tinyfish", ["tinyfish.search"],
                                       behaviour="raise_blocked_code", recorder=rec)
        gw = build(adapters={"tinyfish": adapter}, ledger=ledger, recorder=rec)
        out = call(gw)
        self.assertEqual(("BLOCKED", "EGRESS_REFUSED"), (out.status, out.reason_code))
        self.assertEqual([], ledger.unknowns)
        # A refusal returned *before* dispatch is the one case the ledger can be
        # told no charge was incurred, so it reaches prove_no_charge rather than
        # settle: booking an actual for a call that never left would be a claim
        # about a vendor that was never contacted.
        self.assertEqual(1, len(ledger.no_charge))
        self.assertEqual("PRE_DISPATCH_REFUSAL", ledger.no_charge[0][1])
        self.assertEqual([], ledger.settled)
        self.assertEqual(1, len(adapter.executed))

    def test_returned_blocked_result_without_a_closed_set_caveat_is_a_contract_violation(self):
        adapter = fixture.ProbeAdapter("tinyfish", ["tinyfish.search"],
                                       behaviour="returned_blocked")
        gw = build(adapters={"tinyfish": adapter})
        with self.assertRaises(gateway.ContractViolation):
            call(gw)

    def test_returned_blocked_result_with_a_closed_set_caveat_reports_that_caveat(self):
        ledger = fixture.FakeLedger()
        adapter = fixture.ProbeAdapter("tinyfish", ["tinyfish.search"],
                                       behaviour="returned_blocked_with_code")
        gw = build(adapters={"tinyfish": adapter}, ledger=ledger)
        out = call(gw)
        self.assertEqual(("BLOCKED", "EGRESS_REFUSED"), (out.status, out.reason_code))
        self.assertEqual(1, len(ledger.no_charge))

    def test_upstream_failure_is_reported_as_failed_and_still_settled(self):
        ledger = fixture.FakeLedger()
        adapter = fixture.ProbeAdapter("tinyfish", ["tinyfish.search"], behaviour="failed")
        gw = build(adapters={"tinyfish": adapter}, ledger=ledger)
        out = call(gw)
        self.assertEqual("FAILED", out.status)
        self.assertEqual(1, len(ledger.settled))

    def test_dispatch_observed_schema_drift_is_surfaced_as_a_caveat(self):
        adapter = fixture.ProbeAdapter("tinyfish", ["tinyfish.search"], behaviour="ok")

        def execute(req):
            return fixture.AdapterResult(outcome=fixture.OutcomeCode.OK,
                                         schema_verdict=fixture.SchemaVerdict.DRIFT)

        adapter.execute = execute  # type: ignore[assignment]
        out = call(build(adapters={"tinyfish": adapter}))
        self.assertIn("SCHEMA_DRIFT_OBSERVED_AT_DISPATCH", out.caveats)


class ClosedSetTests(unittest.TestCase):
    def test_gateway_reason_codes_equal_the_frozen_contract_set(self):
        self.assertEqual(FROZEN_REASON_CODES, set(gateway.REASON_CODES))

    def test_every_blocked_path_emits_a_member_of_the_frozen_set(self):
        cases = [
            build(),
            build(entries=[fixture.entry(tier=fixture.Tier.T2,
                                         availability=fixture.Availability.SCHEMA_DRIFT)]),
            build(entries=[fixture.entry(availability=fixture.Availability.DISABLED)]),
            build(adapters={}),
            build(ledger=fixture.FakeLedger(refuse=ValueError("BUDGET_PROJECT_CEILING"))),
            build(adapters={"tinyfish": fixture.ProbeAdapter(
                "tinyfish", ["tinyfish.search"], behaviour="returned_blocked_with_code")}),
        ]
        args = [dict(capability_id="tinyfish.search", params={"query": "q"},
                     run_id="r", operation_id="o", account_ref="a",
                     intent_fingerprint="i"),
                dict(capability_id="tinyfish.nope", params={}, run_id="r",
                     operation_id="o", account_ref="a", intent_fingerprint="i")]
        seen = set()
        for gw in cases:
            for kw in args:
                out = gw.invoke(**kw)
                if out.reason_code:
                    self.assertIn(out.reason_code, FROZEN_REASON_CODES)
                    seen.add(out.reason_code)
        self.assertEqual({"CAPABILITY_UNKNOWN", "CAPABILITY_SCHEMA_DRIFT",
                          "CAPABILITY_NOT_AVAILABLE", "BUDGET_PROJECT_CEILING",
                          "EGRESS_REFUSED"}, seen)


class SeamTests(unittest.TestCase):
    def test_a_missing_contract_module_reports_wiring_incomplete_not_a_fallback(self):
        gw = gateway.CapabilityGateway(
            registry=fixture.FakeRegistry([fixture.entry()]),
            policy=fixture.FakePolicy(), ledger=fixture.FakeLedger(),
            adapters={"tinyfish": fixture.ProbeAdapter("tinyfish", ["tinyfish.search"])},
            contract=None, contract_module_name="wave1_absent_contract_module")
        with self.assertRaises(gateway.WiringIncomplete) as ctx:
            call(gw)
        self.assertIn("wave1_absent_contract_module", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
