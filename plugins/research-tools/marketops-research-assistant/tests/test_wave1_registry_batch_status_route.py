"""`tinyfish.batch_status`: the reviewed route must match the published wire.

C's pinned adapter takes ``batch_status(run_ids)`` — a list of 1..8 opaque run
ids. B's registry said ``batch_id``, a parameter no vendor tool publishes, so a
reviewed and advertised T0 read was refused by Policy as
``CAPABILITY_PARAM_NOT_ALLOWED`` before it could reach the adapter. This file
pins the correction and the boundary around it:

* the allowlist is exactly ``run_ids``, it is required, and the schema carries
  the published cardinality/type/pattern (array, 1..8, bounded opaque strings);
* ``batch_id`` and any other extra key are refused before an adapter is reached;
* a bounded synthetic ``run_ids`` list routes at T0 with no grant;
* with the network gate off the call is refused before egress;
* ``get_wallet`` and ``get_run`` are not widened or weakened;
* ``cancel_run`` stays DISABLED (its ``run_id``/``id`` mismatch is recorded, not
  guessed at), and the vendor schema remains NOT_CAPTURED — S0 stays DENY.

Offline. The adapter counter must remain at zero; nothing here opens a socket.
"""
from __future__ import annotations

import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import research_capability_registry as registry_module
import research_contract as contract
import research_grants as grants
import research_policy as policy_module
import research_usage_ledger as usage

NOW = datetime(2026, 10, 18, 12, 0, tzinfo=timezone.utc)


class CountingAdapter:
    def __init__(self) -> None:
        self.calls = 0

    def execute(self, request):
        self.calls += 1
        raise AssertionError("a refused request reached an adapter")


def request(capability_id: str, params: dict,
            operation_id: str = "op-01") -> contract.CapabilityRequest:
    return contract.CapabilityRequest(
        capability_id=capability_id, params=params, run_id="run-01",
        operation_id=operation_id, account_ref="tinyfish-main",
        intent_fingerprint="intent-1")


def policy(*, network: bool = True) -> policy_module.Policy:
    return policy_module.Policy(registry=registry_module.DEFAULT_REGISTRY,
                                network_enabled=network,
                                allowed_domains=("example.com",), clock=lambda: NOW)


def dispatch_if_allowed(decision, adapter, req) -> None:
    if decision.verdict is contract.PolicyVerdict.ALLOW:
        adapter.execute(req)


class BatchStatusContractTests(unittest.TestCase):
    def test_the_entry_matches_the_published_wire(self):
        entry = registry_module.DEFAULT_REGISTRY.get("tinyfish.batch_status")
        self.assertIsNotNone(entry)
        self.assertEqual(entry.allowed_params, frozenset({"run_ids"}))
        self.assertEqual(entry.param_schema.get("required"), ["run_ids"])
        run_ids = entry.param_schema["properties"]["run_ids"]
        self.assertEqual(run_ids["type"], "array")
        self.assertEqual(run_ids["items"]["type"], "string")
        self.assertEqual(run_ids["items"]["pattern"], "^[A-Za-z0-9_-]{1,128}$")
        self.assertEqual(run_ids["minItems"], 1)
        self.assertEqual(run_ids["maxItems"], 8)
        self.assertIs(entry.tier, contract.Tier.T0)
        self.assertIs(entry.effect_class, contract.EffectClass.READ_ONLY)
        self.assertIs(entry.availability, contract.Availability.ENABLED)
        self.assertEqual(entry.ledger_capability, "usage_read")
        self.assertEqual(entry.cost_meter, "request")

    def test_the_wrong_batch_id_is_refused_before_any_adapter(self):
        adapter = CountingAdapter()
        req = request("tinyfish.batch_status", {"batch_id": "batch-1"})
        decision = policy().evaluate(req)
        self.assertIs(decision.verdict, contract.PolicyVerdict.DENY)
        self.assertEqual(decision.reason_code, "CAPABILITY_PARAM_NOT_ALLOWED")
        dispatch_if_allowed(decision, adapter, req)
        self.assertEqual(adapter.calls, 0)

    def test_unknown_extras_are_refused(self):
        for params in (
                {"run_ids": ["run-1"], "batch_id": "batch-1"},
                {"run_ids": ["run-1"], "id": "run-1"},
                {"run_ids": ["run-1"], "limit": 10}):
            with self.subTest(params=params):
                adapter = CountingAdapter()
                req = request("tinyfish.batch_status", params)
                decision = policy().evaluate(req)
                self.assertIs(decision.verdict, contract.PolicyVerdict.DENY)
                self.assertEqual(decision.reason_code, "CAPABILITY_PARAM_NOT_ALLOWED")
                dispatch_if_allowed(decision, adapter, req)
                self.assertEqual(adapter.calls, 0)

    def test_one_to_eight_run_ids_route_at_t0_without_a_grant(self):
        for count in (1, 8):
            with self.subTest(count=count):
                params = {"run_ids": [f"run-{index}" for index in range(count)]}
                adapter = CountingAdapter()
                decision = policy().evaluate(request("tinyfish.batch_status", params))
                self.assertIs(decision.verdict, contract.PolicyVerdict.ALLOW)
                self.assertEqual(decision.reason_code, "")
                self.assertIs(decision.effective_tier, contract.Tier.T0)
                # Policy only: no adapter, no egress.
                self.assertEqual(adapter.calls, 0)

    def test_network_disabled_refuses_before_egress(self):
        adapter = CountingAdapter()
        req = request("tinyfish.batch_status", {"run_ids": ["run-1"]})
        decision = policy(network=False).evaluate(req)
        self.assertIs(decision.verdict, contract.PolicyVerdict.DENY)
        self.assertEqual(decision.reason_code, "RESEARCH_NETWORK_DISABLED")
        dispatch_if_allowed(decision, adapter, req)
        self.assertEqual(adapter.calls, 0)

    def test_batch_id_is_not_routable(self):
        # The wrong name may survive only on a DISABLED entry (the cancel family,
        # recorded rather than repaired). No ENABLED capability exposes it.
        for entry in registry_module.DEFAULT_REGISTRY:
            if entry.availability is contract.Availability.ENABLED:
                with self.subTest(capability_id=entry.capability_id):
                    self.assertNotIn("batch_id", entry.allowed_params)
        self.assertNotIn(
            "batch_id",
            registry_module.DEFAULT_REGISTRY.get("tinyfish.batch_status").allowed_params)


class NeighbouringCapabilitiesTests(unittest.TestCase):
    def test_wallet_and_get_run_are_not_weakened_or_widened(self):
        reg = registry_module.DEFAULT_REGISTRY
        wallet = reg.get("tinyfish.get_wallet")
        self.assertEqual(wallet.allowed_params, frozenset())
        self.assertIsNone(wallet.param_schema.get("required"))
        get_run = reg.get("tinyfish.get_run")
        self.assertEqual(get_run.allowed_params, frozenset({"id"}))
        self.assertIs(get_run.tier, contract.Tier.T0)
        for capability_id, params in (("tinyfish.get_wallet", {}),
                                      ("tinyfish.get_run", {"id": "run-1"})):
            with self.subTest(capability_id=capability_id):
                decision = policy().evaluate(request(capability_id, params))
                self.assertIs(decision.verdict, contract.PolicyVerdict.ALLOW)

    def test_cancel_run_stays_disabled_with_the_mismatch_recorded(self):
        entry = registry_module.DEFAULT_REGISTRY.get("tinyfish.cancel_run")
        self.assertIs(entry.availability, contract.Availability.DISABLED)
        self.assertIs(entry.tier, contract.Tier.T4)
        # B still reviews the name `run_id`; C's adapter takes `id`. The entry is
        # DISABLED, so neither shape is routable and this is recorded, not guessed.
        self.assertEqual(entry.allowed_params, frozenset({"run_id"}))
        for params in ({"run_id": "run-1"}, {"id": "run-1"}, {}):
            with self.subTest(params=params):
                adapter = CountingAdapter()
                req = request("tinyfish.cancel_run", params)
                decision = policy().evaluate(req)
                self.assertIs(decision.verdict, contract.PolicyVerdict.DENY)
                # Policy checks the reviewed allowlist before availability, so the
                # adapter's `id` shape is a parameter refusal while B's own
                # `run_id` shape reaches the availability refusal. Both are
                # pre-dispatch refusals; the capability is unroutable either way.
                self.assertIn(decision.reason_code,
                              {"CAPABILITY_NOT_AVAILABLE",
                               "CAPABILITY_PARAM_NOT_ALLOWED"})
                dispatch_if_allowed(decision, adapter, req)
                self.assertEqual(adapter.calls, 0)


class LedgerAndCaptureTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.project = usage.project_for_root(self.root)
        self.ledger = usage.UsageLedger(
            self.root / "state" / "usage-ledger.sqlite3",
            pricing=usage.documented_pricing(), registry=grants.DEFAULT_REGISTRY,
            clock=lambda: NOW)
        self.ledger.set_cap("run", f"{self.project}/run-01", ceiling_micros=0,
                            ceiling_calls=10, ceiling_seconds=3_600,
                            source="batch-status-test")

    def test_the_batch_read_routes_through_the_ledger_at_zero(self):
        self.ledger.reserve(usage.OperationRequest(
            project=self.project, run_id="run-01", operation_id="op-batch-1",
            provider="tinyfish", capability="usage_read", account_ref="tinyfish-main",
            transport="official_mcp", intent="batch status read",
            args={"run_ids": ["run-1", "run-2"]}))
        self.ledger.mark_dispatch_intent("op-batch-1")
        row = self.ledger.settle("op-batch-1", actual_micros=0,
                                 actual_source=usage.ActualSource.DERIVED,
                                 upstream_ref="mcp-request-1", quota_units=1)
        self.assertEqual(row.charge_state, usage.ChargeState.SETTLED.value)
        self.assertEqual(row.actual_micros, 0)
        self.assertEqual(row.actual_source, usage.ActualSource.DERIVED.value)
        self.assertEqual(row.quota_units, 1)

    def test_the_vendor_schema_stays_not_captured(self):
        reg = registry_module.DEFAULT_REGISTRY
        self.assertIsNone(reg.vendor_schema_sha256)
        self.assertIs(reg.authoritative_status("tinyfish"),
                      registry_module.VendorSchemaStatus.NOT_CAPTURED)
        self.assertEqual(reg.get("tinyfish.batch_status").readiness, "OFFLINE_TESTED")

    def test_an_observation_still_does_not_mutate_the_default(self):
        default = registry_module.DEFAULT_REGISTRY
        derived = default.with_schema_verdicts(
            {"tinyfish.batch_status": contract.SchemaVerdict.DRIFT})
        self.assertIs(derived.get("tinyfish.batch_status").availability,
                      contract.Availability.SCHEMA_DRIFT)
        self.assertIs(default.get("tinyfish.batch_status").availability,
                      contract.Availability.ENABLED)
        self.assertEqual(default.observed_verdicts(), {})


if __name__ == "__main__":
    unittest.main()
