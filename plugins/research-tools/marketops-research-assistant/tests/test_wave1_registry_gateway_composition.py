"""Composition: the real gateway, the real wiring, these four modules, one call.

This is the test the Wave-1 dispatch asked for and the one an offline suite cannot
fake: after the four W-B modules land, `research_wiring.probe()` must stop listing
them as missing, `build_gateway()` must assemble, and a managed call must run
through Registry -> Policy -> Ledger -> Adapter and settle its row.

The adapter is a **synthetic one in this file**, not a provider: it returns a
canned result and touches nothing. With `build_gateway`'s `adapters=` argument the
real provider adapters are never constructed, so no credential is read and no
network call is attempted.

On a branch that does not carry `research_gateway`/`research_wiring` (W-B's own
branch), every test here is skipped with a BLOCKED marker rather than passing
vacuously.
"""
from __future__ import annotations

import importlib.util
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import research_capability_registry as registry_module
import research_contract as contract
import research_grants as grants
import research_policy as policy_module
import research_usage_ledger as usage

NOW = datetime(2026, 10, 18, 9, 0, tzinfo=timezone.utc)


def _present(name: str) -> bool:
    try:
        return importlib.util.find_spec(name) is not None
    except (ImportError, ValueError):
        return False


WIRING_PRESENT = _present("research_wiring") and _present("research_gateway")
needs_integration = unittest.skipUnless(
    WIRING_PRESENT, "BLOCKED: research_wiring/research_gateway are not on this branch")


class SyntheticSerper:
    """A managed adapter for the composition test. Serves one reviewed capability."""

    provider = "serper"
    transport = "rest"

    def __init__(self):
        self.calls = 0

    def capabilities(self):
        return ("serper.search",)

    def probe_schema(self):
        return {"status": "AUTH_REQUIRED"}

    def execute(self, req):
        self.calls += 1
        return contract.AdapterResult(
            contract.OutcomeCode.OK, payload={"candidates": []},
            upstream_ref="synthetic-1", actual_usd=Decimal("0"),
            actual_source=contract.ActualSource.DERIVED, transport="rest",
            schema_verdict=contract.SchemaVerdict.UNVERIFIED)

    def cancel(self, upstream_ref):
        raise NotImplementedError


class CompositionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def _ledger(self):
        project = usage.project_for_root(self.root)
        ledger = usage.UsageLedger(self.root / "state" / "usage-ledger.sqlite3",
                                   pricing=usage.documented_pricing(),
                                   registry=grants.DEFAULT_REGISTRY, clock=lambda: NOW)
        ledger.set_cap("run", f"{project}/run-01", ceiling_micros=0, ceiling_calls=100,
                       ceiling_seconds=3_600, source="composition-test")
        return ledger

    @needs_integration
    def test_the_four_wb_modules_are_no_longer_reported_missing(self):
        import research_wiring
        report = research_wiring.probe()
        ours = {"research_contract", "research_capability_registry", "research_policy",
                "research_adapter_base"}
        self.assertEqual(sorted(ours & set(report["missing"])), [])
        self.assertIn("research_contract", report["present"])

    @needs_integration
    def test_build_gateway_assembles_with_real_collaborators(self):
        import research_wiring
        gateway = research_wiring.build_gateway(
            contract=contract, registry=registry_module.DEFAULT_REGISTRY,
            policy=policy_module.Policy(registry=registry_module.DEFAULT_REGISTRY,
                                        network_enabled=True, clock=lambda: NOW),
            ledger=self._ledger(), adapters={"serper": SyntheticSerper()},
            root=self.root)
        self.assertTrue(hasattr(gateway, "invoke"))

    @needs_integration
    def test_one_managed_call_runs_end_to_end_and_settles_its_row(self):
        import research_wiring
        ledger = self._ledger()
        adapter = SyntheticSerper()
        gateway = research_wiring.build_gateway(
            contract=contract, registry=registry_module.DEFAULT_REGISTRY,
            policy=policy_module.Policy(registry=registry_module.DEFAULT_REGISTRY,
                                        network_enabled=True, clock=lambda: NOW),
            ledger=ledger, adapters={"serper": adapter}, root=self.root)
        outcome = gateway.invoke(capability_id="serper.search", params={"q": "venue"},
                                 run_id="run-01", operation_id="op-1",
                                 account_ref="serper-main",
                                 intent_fingerprint="intent-1")
        self.assertEqual(outcome.status, "OK", (outcome.reason_code, outcome.caveats))
        self.assertEqual(adapter.calls, 1)
        row = ledger.get("op-1")
        self.assertEqual(row.charge_state, usage.ChargeState.SETTLED.value)
        self.assertEqual(row.actual_micros, 0)          # credit-priced, recorded zero rate
        self.assertEqual(row.actual_source, usage.ActualSource.DERIVED.value)
        self.assertEqual(row.quota_units, 1)

    @needs_integration
    def test_an_unreviewed_capability_is_blocked_before_any_adapter(self):
        import research_wiring
        adapter = SyntheticSerper()
        gateway = research_wiring.build_gateway(
            contract=contract, registry=registry_module.DEFAULT_REGISTRY,
            policy=policy_module.Policy(registry=registry_module.DEFAULT_REGISTRY,
                                        network_enabled=True, clock=lambda: NOW),
            ledger=self._ledger(), adapters={"serper": adapter}, root=self.root)
        outcome = gateway.invoke(capability_id="serper.brand_new", params={"q": "x"},
                                 run_id="run-01", operation_id="op-2",
                                 account_ref="serper-main",
                                 intent_fingerprint="intent-2")
        self.assertEqual(outcome.status, "BLOCKED")
        self.assertEqual(outcome.reason_code, "CAPABILITY_UNKNOWN")
        self.assertEqual(adapter.calls, 0)


if __name__ == "__main__":
    unittest.main()
