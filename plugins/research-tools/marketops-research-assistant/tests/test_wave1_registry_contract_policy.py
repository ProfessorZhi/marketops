"""Contract types, the reviewed registry, and the policy plane — offline.

These are the three W-B modules the gateway was missing. The tests are written
against the *contract* (§2-§5), not against the modules' current shape, and the
negative cases are the ones a host actually needs to be able to trust:

* a capability that is not reviewed does not exist;
* a parameter outside the reviewed set is refused before routing;
* a schema that drifted stops T2+ dispatch;
* a T3/T4 call without a grant is refused, and a same-user attestation is not a
  grant;
* a URL outside the allowlist is refused;
* an unverified campaign balance, and an unreconciled charge, both fail closed at
  the ledger rather than being reported as free.

Private temporary databases, no network, no credential, no provider call.
"""
from __future__ import annotations

import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import research_capability_registry as registry_module
import research_contract as contract
import research_grants as grants
import research_policy as policy_module
import research_usage_ledger as usage
from research_contract import (AdapterResult, CapabilityRequest, EffectClass,
                               PolicyVerdict, SchemaVerdict, Tier)

NOW = datetime(2026, 10, 18, 9, 0, tzinfo=timezone.utc)


def request(capability_id: str, params=None, grant_ref=None, **overrides):
    base = dict(capability_id=capability_id, params=dict(params or {}), run_id="run-01",
                operation_id="op-01", account_ref="acct", intent_fingerprint="intent-1",
                grant_ref=grant_ref)
    base.update(overrides)
    return CapabilityRequest(**base)


def grant(**overrides) -> policy_module.PolicyGrant:
    fields = dict(
        grant_ref="gnt-synthetic-1", project="devon", max_tier=Tier.T3,
        providers=("tinyfish",), capability_ids=(), allowed_domains=("example.com",),
        max_usd=Decimal("5"), period="run", issued_at=NOW - timedelta(minutes=5),
        expires_at=NOW + timedelta(hours=1), issued_by="operator-member-b",
        signature="synthetic-signature", factor="SEPARATELY_AUTHENTICATED_GRANT")
    fields.update(overrides)
    return policy_module.PolicyGrant(**fields)


def registry():
    """The shipped review table.

    No synthetic entries: an ENABLED entry must be one the ledger can price (the
    registry refuses otherwise), and inventing a provider would be exactly the
    "fake capability" this table exists to prevent. "Synthetic" in these tests means
    synthetic *inputs* against real reviewed entries, never a live call.
    """
    return registry_module.DEFAULT_REGISTRY


class ContractTypeTests(unittest.TestCase):
    def test_tier_is_the_contracts_spelling_so_the_gateway_can_rank_it(self):
        self.assertEqual(str(Tier.T2), "T2")
        self.assertEqual(contract.tier_rank(Tier.T0), 0)
        self.assertEqual(contract.tier_rank("T4"), 4)
        with self.assertRaises(contract.ContractError):
            contract.tier_rank("T5")

    def test_an_allow_carries_no_reason_code(self):
        decision = contract.PolicyDecision(PolicyVerdict.ALLOW, "", Tier.T1)
        self.assertIs(decision.verdict, PolicyVerdict.ALLOW)
        with self.assertRaises(contract.ContractError):
            contract.PolicyDecision(PolicyVerdict.ALLOW, "CAPABILITY_UNKNOWN", Tier.T1)

    def test_a_refusal_must_use_a_closed_set_code(self):
        contract.PolicyDecision(PolicyVerdict.DENY, "POLICY_TIER_NOT_GRANTED", Tier.T3)
        for bad in ("TIER_NOT_GRANTED", "denied", ""):
            with self.subTest(bad=bad):
                with self.assertRaises(contract.ContractError):
                    contract.PolicyDecision(PolicyVerdict.DENY, bad, Tier.T3)

    def test_money_is_decimal_never_float(self):
        AdapterResult(contract.OutcomeCode.OK, actual_usd=Decimal("0.10"))
        with self.assertRaises(contract.ContractError):
            AdapterResult(contract.OutcomeCode.OK, actual_usd=0.10)

    def test_a_blocked_result_must_name_a_closed_set_reason(self):
        AdapterResult(contract.OutcomeCode.BLOCKED, caveats=("EGRESS_REFUSED",))
        with self.assertRaises(contract.ContractError):
            AdapterResult(contract.OutcomeCode.BLOCKED, caveats=("it did not work",))

    def test_the_reason_set_matches_the_gateways_copy(self):
        # A cross-check, not a second authority: §5.3 is transcribed in both places
        # and a divergence has to be caught here rather than at a refusal.
        try:
            import research_gateway
        except ModuleNotFoundError:
            self.skipTest("BLOCKED: research_gateway is not on this branch")
        self.assertEqual(contract.REASON_CODES, research_gateway.REASON_CODES)

    def test_an_evidence_envelope_cannot_leave_the_candidate_boundary(self):
        with self.assertRaises(contract.ContractError):
            contract.EvidenceEnvelope(
                evidence_id="ev-1", run_id="run-01", operation_id="op-01",
                ledger_id="op-01", provider="tinyfish", capability_id="tinyfish.search",
                source_url="https://example.com/", retrieved_at=NOW,
                content_sha256="0" * 64, raw_artifact_ref="art-1", candidate_only=False)


class RegistryTests(unittest.TestCase):
    def test_every_entry_is_a_strict_allowlist_entry(self):
        for entry in registry_module.DEFAULT_REGISTRY:
            with self.subTest(entry=entry.capability_id):
                self.assertIn(".", entry.capability_id)
                self.assertLessEqual(entry.allowed_params,
                                     set(entry.param_schema["properties"]))
                if entry.availability is contract.Availability.ENABLED:
                    self.assertIsNotNone(entry.ledger_capability)

    def test_a_capability_that_is_not_reviewed_does_not_exist(self):
        self.assertIsNone(registry_module.DEFAULT_REGISTRY.get("tinyfish.brand_new_tool"))
        with self.assertRaises(registry_module.RegistryError):
            registry_module.DEFAULT_REGISTRY.describe("tinyfish.brand_new_tool")

    def test_no_entry_exposes_the_parameters_that_would_be_authority(self):
        # IR-3: tier/effect/endpoint/tool/host are the caller's to *choose* only
        # through the registry. If any entry allowed them, the gateway's ignore
        # rule would be the only thing standing between an Agent and a bypass.
        forbidden = {"tier", "effect_class", "endpoint", "tool_name", "host", "url_template"}
        for entry in registry_module.DEFAULT_REGISTRY:
            with self.subTest(entry=entry.capability_id):
                self.assertEqual(entry.allowed_params & forbidden, frozenset())

    def test_arbitrary_javascript_is_not_an_exposed_parameter(self):
        for entry in registry_module.DEFAULT_REGISTRY:
            with self.subTest(entry=entry.capability_id):
                self.assertNotIn("js_code", entry.allowed_params)
                self.assertNotIn("javascript", entry.allowed_params)

    def test_list_filters_by_provider_and_tier(self):
        providers = {item.provider for item in registry_module.DEFAULT_REGISTRY.list(
            provider="crawl4ai")}
        self.assertEqual(providers, {"crawl4ai"})
        tier_two = registry_module.DEFAULT_REGISTRY.list(tier=Tier.T2)
        self.assertTrue(tier_two)
        self.assertTrue(all(item.tier is Tier.T2 for item in tier_two))

    def test_a_drifted_schema_downgrades_the_capability(self):
        snapshot = registry_module.param_schema_for({"q": "string"})
        entry = registry_module.CapabilityEntry(
            capability_id="synthetic.search", provider="synthetic", upstream_tool="search",
            transport="rest", effect_class=EffectClass.BILLABLE_READ, tier=Tier.T2,
            auth_scope="synthetic:search", cost_meter="search_query",
            param_schema=snapshot, allowed_params=frozenset({"q"}),
            schema_sha256=registry_module.schema_digest(snapshot),
            availability=contract.Availability.ENABLED, readiness="OFFLINE_TESTED",
            ledger_capability="search")
        table = registry_module.CapabilityRegistry((entry,))
        matching = {"tools": [{"name": "search", "inputSchema": snapshot}]}
        self.assertIs(table.verify_schema("synthetic.search", matching), SchemaVerdict.MATCH)
        self.assertIs(table.get("synthetic.search").availability,
                      contract.Availability.ENABLED)
        drifted = {"tools": [{"name": "search",
                              "inputSchema": {"type": "object", "properties": {}}}]}
        verdict = table.verify_schema("synthetic.search", drifted)
        self.assertIs(verdict, SchemaVerdict.DRIFT)
        # `verify_schema` is pure now: applying a verdict is an explicit derivation.
        # That is what removed the process-wide observation map a probe used to
        # write into, so the base table and `DEFAULT_REGISTRY` cannot be downgraded
        # by one caller for every other caller.
        drifted_table = table.with_schema_verdicts({"synthetic.search": verdict})
        self.assertIs(drifted_table.get("synthetic.search").availability,
                      contract.Availability.SCHEMA_DRIFT)
        self.assertIs(table.get("synthetic.search").availability,
                      contract.Availability.ENABLED)
        absent = {"tools": [{"name": "other"}]}
        self.assertIs(table.verify_schema("synthetic.search", absent), SchemaVerdict.ABSENT)

    def test_without_a_snapshot_the_verdict_is_unverified_not_a_match(self):
        # No live handshake has been authorised, so there is nothing to compare to;
        # claiming MATCH would let a drifted tool run as if it had been reviewed.
        # The shared table is safe to probe: `verify_schema` returns a verdict and
        # writes nothing, so there is no observation to leak into later tests.
        verdict = registry_module.DEFAULT_REGISTRY.verify_schema(
            "tinyfish.search", {"tools": [{"name": "search", "inputSchema": {}}]})
        self.assertIs(verdict, SchemaVerdict.UNVERIFIED)

    def test_every_enabled_entries_ledger_name_is_one_the_ledger_reviews(self):
        for entry in registry_module.DEFAULT_REGISTRY:
            with self.subTest(entry=entry.capability_id):
                if not entry.ledger_capability:
                    continue
                self.assertIsNotNone(
                    grants.DEFAULT_REGISTRY.get(entry.provider, entry.ledger_capability))


class PolicyTests(unittest.TestCase):
    def policy(self, **kwargs):
        kwargs.setdefault("registry", registry())
        kwargs.setdefault("network_enabled", True)
        kwargs.setdefault("clock", lambda: NOW)
        return policy_module.Policy(**kwargs)

    def test_a_valid_synthetic_t0_is_allowed_with_no_grant(self):
        # A real reviewed T0 capability (a read-only wallet snapshot), synthetic
        # inputs, no grant and no live call.
        decision = self.policy(network_enabled=True).evaluate(request("tinyfish.get_wallet"))
        self.assertIs(decision.verdict, PolicyVerdict.ALLOW)
        self.assertEqual(decision.reason_code, "")
        self.assertIs(decision.effective_tier, Tier.T0)
        self.assertIsNone(decision.constraints["max_usd"])

    def test_a_remote_read_with_network_disabled_is_refused(self):
        decision = self.policy(network_enabled=False).evaluate(request("tinyfish.get_wallet"))
        self.assertIs(decision.verdict, PolicyVerdict.DENY)
        self.assertEqual(decision.reason_code, "RESEARCH_NETWORK_DISABLED")

    def test_a_t3_capability_without_a_grant_is_refused(self):
        decision = self.policy().evaluate(request(
            "tinyfish.run_web_automation", {"url": "https://example.com/", "goal": "x"}))
        self.assertIs(decision.verdict, PolicyVerdict.DENY)
        self.assertEqual(decision.reason_code, "POLICY_TIER_NOT_GRANTED")

    def test_a_same_user_attestation_is_not_a_grant(self):
        table = {"gnt-synthetic-1": grant(factor="SAME_USER_CLI_ATTESTATION")}
        decision = self.policy(grants=table).evaluate(request(
            "tinyfish.run_web_automation", {"url": "https://example.com/", "goal": "x"},
            grant_ref="gnt-synthetic-1"))
        self.assertIs(decision.verdict, PolicyVerdict.DENY)
        self.assertEqual(decision.reason_code, "POLICY_TIER_NOT_GRANTED")

    def test_an_expired_grant_is_refused(self):
        table = {"gnt-synthetic-1": grant(expires_at=NOW - timedelta(seconds=1))}
        decision = self.policy(grants=table).evaluate(request(
            "tinyfish.run_web_automation", {"url": "https://example.com/", "goal": "x"},
            grant_ref="gnt-synthetic-1"))
        self.assertEqual(decision.reason_code, "POLICY_GRANT_EXPIRED")

    def test_a_separately_authenticated_t3_grant_is_allowed(self):
        table = {"gnt-synthetic-1": grant()}
        decision = self.policy(grants=table).evaluate(request(
            "tinyfish.run_web_automation", {"url": "https://example.com/", "goal": "x"},
            grant_ref="gnt-synthetic-1"))
        self.assertIs(decision.verdict, PolicyVerdict.ALLOW)
        self.assertIs(decision.effective_tier, Tier.T3)
        self.assertEqual(decision.constraints["max_usd"], "5")
        self.assertTrue(decision.constraints["tier_enforceable"])

    def test_a_login_profile_parameter_raises_the_call_to_t4(self):
        # The tool is T3; using a stored login profile makes the *call* T4, and T4
        # is off until it can be scoped separately.
        table = {"gnt-synthetic-1": grant(max_tier=Tier.T4)}
        decision = self.policy(grants=table).evaluate(request(
            "tinyfish.run_web_automation",
            {"url": "https://example.com/", "goal": "x", "use_profile": True},
            grant_ref="gnt-synthetic-1"))
        self.assertIs(decision.effective_tier, Tier.T4)
        self.assertIs(decision.verdict, PolicyVerdict.DENY)
        self.assertEqual(decision.reason_code, "POLICY_TIER_NOT_GRANTED")

    def test_a_webhook_is_an_outbound_side_effect_and_raises_the_tier(self):
        table = {"gnt-synthetic-1": grant(max_tier=Tier.T4)}
        decision = self.policy(grants=table).evaluate(request(
            "tinyfish.run_web_automation",
            {"url": "https://example.com/", "goal": "x",
             "webhook_url": "https://collector.example.com/hook"},
            grant_ref="gnt-synthetic-1"))
        self.assertIs(decision.effective_tier, Tier.T4)
        self.assertIs(decision.verdict, PolicyVerdict.DENY)

    def test_a_parameter_outside_the_reviewed_set_is_refused(self):
        decision = self.policy().evaluate(request(
            "serper.search", {"q": "venue", "js_code": "fetch('http://x')"}))
        self.assertIs(decision.verdict, PolicyVerdict.DENY)
        self.assertEqual(decision.reason_code, "CAPABILITY_PARAM_NOT_ALLOWED")

    def test_a_url_outside_the_allowlist_is_refused(self):
        decision = self.policy(allowed_domains=("example.com",)).evaluate(request(
            "crawl4ai.fetch_page", {"url": "https://evil-example.com/"}))
        self.assertEqual(decision.reason_code, "POLICY_DOMAIN_NOT_ALLOWED")
        allowed = self.policy(allowed_domains=("example.com",)).evaluate(request(
            "crawl4ai.fetch_page", {"url": "https://docs.example.com/page"}))
        self.assertIs(allowed.verdict, PolicyVerdict.ALLOW)

    def test_no_configured_allowlist_means_no_remote_page(self):
        decision = self.policy().evaluate(request(
            "crawl4ai.fetch_page", {"url": "https://example.com/"}))
        self.assertEqual(decision.reason_code, "POLICY_DOMAIN_NOT_ALLOWED")

    def test_a_disabled_capability_is_refused_before_anything_else(self):
        decision = self.policy().evaluate(request("tinyfish.cancel_run", {"run_id": "r-1"}))
        self.assertEqual(decision.reason_code, "CAPABILITY_NOT_AVAILABLE")

    def test_a_drifted_capability_stops_t2_dispatch(self):
        verdict = registry().verify_schema("serper.search", {"tools": []})
        self.assertIs(verdict, contract.SchemaVerdict.ABSENT)
        registry_with_drift = registry().with_schema_verdicts({"serper.search": verdict})
        decision = self.policy(registry=registry_with_drift).evaluate(
            request("serper.search", {"query": "venue"}))
        self.assertEqual(decision.reason_code, "CAPABILITY_SCHEMA_DRIFT")

    def test_an_unresolvable_grant_reference_is_not_a_grant(self):
        decision = self.policy(grants={}).evaluate(request(
            "tinyfish.run_web_automation", {"url": "https://example.com/", "goal": "x"},
            grant_ref="gnt-missing"))
        self.assertEqual(decision.reason_code, "POLICY_TIER_NOT_GRANTED")


class LedgerFailClosedTests(unittest.TestCase):
    """The two money questions a host asks: is there a budget, and is a charge known?"""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "state" / "usage-ledger.sqlite3"

    def ledger(self, *, budgets=None):
        ledger = usage.UsageLedger(self.path, pricing=usage.documented_pricing(),
                                   registry=grants.DEFAULT_REGISTRY, clock=lambda: NOW,
                                   campaign_budgets=budgets)
        ledger.set_cap("run", "devon/run-01", ceiling_micros=0, ceiling_calls=10,
                       ceiling_seconds=3_600, source="policy-test")
        return ledger

    def operation(self, **overrides):
        base = dict(project="devon", run_id="run-01", operation_id="op-01",
                    provider="tinyfish", capability="search", account_ref="tinyfish-main",
                    transport="official_mcp", intent="find the venue",
                    args={"query": "devon venue"})
        base.update(overrides)
        return usage.OperationRequest(**base)

    def unverified_budget(self):
        return usage.CampaignBudget(usage.CampaignFloor(usage.CampaignBaseline(
            provider="tinyfish", account_ref="tinyfish-main", meter="searches",
            units=1_000_000, observed_at=NOW.isoformat(),
            source=usage.MeterReadingSource.ENVIRONMENT_VARIABLE, campaign_id="c1")))

    def test_an_unverified_balance_is_not_a_budget(self):
        ledger = self.ledger(budgets={("tinyfish", "tinyfish-main"): self.unverified_budget()})
        with self.assertRaises(usage.LedgerRefused) as ctx:
            ledger.reserve(self.operation())
        self.assertEqual(ctx.exception.code, "CAMPAIGN_BASELINE_NOT_EVIDENCE")
        self.assertEqual(ledger.operations(), ())

    def test_an_account_with_no_captured_balance_fails_closed(self):
        ledger = self.ledger(budgets={("serper", "serper-main"): usage.CampaignBudget(
            usage.CampaignFloor(usage.CampaignBaseline(
                provider="serper", account_ref="serper-main", meter="credits", units=10,
                observed_at=NOW.isoformat(),
                source=usage.MeterReadingSource.VENDOR_ACCOUNT_READ, campaign_id="c1")))})
        with self.assertRaises(usage.LedgerRefused) as ctx:
            ledger.reserve(self.operation())
        self.assertEqual(ctx.exception.code, "CAMPAIGN_NOT_CAPTURED")

    def test_an_unknown_charge_holds_its_exposure(self):
        ledger = self.ledger()
        op = ledger.reserve(self.operation())
        ledger.mark_dispatch_intent(op.operation_id)
        ledger.mark_unknown(op.operation_id, reason="PROVIDER_TIMEOUT")
        totals = ledger.account_totals("tinyfish", "tinyfish-main")
        self.assertEqual(totals.quota_units, 1)
        self.assertEqual(totals.unknown_micros, 0)      # credit-priced: the credit is the hold
        self.assertIn(op.operation_id, [row.operation_id for row in ledger.open_operations()])


if __name__ == "__main__":
    unittest.main()
