"""Lane F: an explicit dollar cap, and charge uncertainty that stays uncertain.

These tests pin the three failures the lane exists for:

* a request-count budget was the only default, and ``dollar_limit_enforced`` was
  hard-coded ``False`` even when an operator had stated a money ceiling;
* the wiring could not state a run ceiling and a project ceiling that differ;
* a successful call whose cost could not be established was booked under the
  same ``PROVIDER_ERROR_UNVERIFIED`` code as an execution failure.

Everything here is synthetic and offline. No provider is called, no real key is
read and no price is invented: the money figures are test literals written into a
temporary SQLite file.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import socket
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from unittest import mock

PLUGIN = Path(__file__).resolve().parents[1]
SCRIPTS = PLUGIN / "scripts"
sys.path.insert(0, str(SCRIPTS))

import research_contract as contract  # noqa: E402
import research_gateway as gateway  # noqa: E402
import research_runtime_setup as runtime  # noqa: E402
import research_usage_ledger as usage  # noqa: E402


def _price(unit_micros=1_000, *, free_tier=False, verified=True):
    return usage.Price(
        provider="serper", capability="search", unit_micros=unit_micros,
        quota_unit="credits", quota_per_call=1,
        source="synthetic-test-rate", version="synthetic-1",
        observed_at="2026-10-20T00:00:00+00:00",
        free_tier=free_tier, verified=verified)


def _catalog(**kwargs):
    return usage.PricingCatalog((_price(**kwargs),))


def _request(operation_id="op-1", *, run_id="run-a", query=None, **overrides):
    values = dict(
        project="proj", run_id=run_id, operation_id=operation_id,
        provider="serper", capability="search", account_ref="serper-main",
        transport="rest_https", intent="synthetic dollar-cap contract",
        args={"query": query or operation_id})
    values.update(overrides)
    return usage.OperationRequest(**values)


def _caps(ledger, *, run_calls=10, project_calls=10, run_usd=None,
          project_usd=None, run_id="run-a", project="proj"):
    ledger.set_cap("run", f"{project}/{run_id}", ceiling_calls=run_calls,
                   ceiling_micros=run_usd, usd_enforced=run_usd is not None,
                   source="synthetic-consent")
    ledger.set_cap("project", project, ceiling_calls=project_calls,
                   ceiling_micros=project_usd,
                   usd_enforced=project_usd is not None,
                   source="synthetic-consent")


class CapCase(unittest.TestCase):
    def setUp(self):
        self._temp = tempfile.TemporaryDirectory(prefix="lane-f-cap-")
        self.addCleanup(self._temp.cleanup)
        self.root = Path(self._temp.name)
        self.path = self.root / "state" / "usage-ledger.sqlite3"
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def ledger(self, catalog=None):
        return usage.UsageLedger(self.path, pricing=catalog or _catalog())


class ExplicitDollarCapTests(CapCase):
    def test_an_unknown_price_cannot_satisfy_an_explicit_usd_cap(self):
        ledger = self.ledger(usage.documented_pricing())
        _caps(ledger, run_usd=100_000, project_usd=1_000_000)

        with self.assertRaises(usage.LedgerRefused) as ctx:
            ledger.reserve(_request())

        self.assertEqual("PRICE_UNKNOWN", ctx.exception.code)
        self.assertEqual("money", ctx.exception.dimension)
        self.assertIn("PLACEHOLDER", str(ctx.exception).upper())
        self.assertEqual((), ledger.operations())

    def test_an_unverified_rate_is_not_a_provable_usd_bound(self):
        ledger = self.ledger(_catalog(unit_micros=1_000, verified=False))
        _caps(ledger, run_usd=100_000, project_usd=1_000_000)

        with self.assertRaises(usage.LedgerRefused) as ctx:
            ledger.reserve(_request())

        self.assertEqual("PRICE_UNKNOWN", ctx.exception.code)
        self.assertIn("UNVERIFIED", str(ctx.exception).upper())

    def test_a_verified_rate_reserves_exactly_the_computed_upper_bound(self):
        ledger = self.ledger(_catalog(unit_micros=1_000, verified=True))
        _caps(ledger, run_usd=1_500, project_usd=10_000)

        operation = ledger.reserve(_request())

        self.assertEqual(1_000, operation.reserved_micros)
        self.assertEqual("RESERVED", operation.charge_state)
        # The second call would take the run to 2_000 against a 1_500 ceiling.
        with self.assertRaises(usage.LedgerRefused) as ctx:
            ledger.reserve(_request("op-2"))
        self.assertEqual("BUDGET_RUN_CEILING", ctx.exception.code)
        self.assertEqual("money", ctx.exception.dimension)

    def test_an_evidenced_free_tier_is_admissible_under_a_zero_usd_cap(self):
        ledger = self.ledger(_catalog(unit_micros=0, free_tier=True, verified=True))
        _caps(ledger, run_usd=0, project_usd=0)

        operation = ledger.reserve(_request())

        self.assertEqual(0, operation.reserved_micros)
        self.assertTrue(operation.price_verified)
        ledger.mark_dispatch_intent(operation.operation_id)
        settled = ledger.settle_free_tier(operation.operation_id, quota_units=1,
                                          upstream_ref="free-1")
        self.assertEqual("FREE_TIER", settled.charge_state)

    def test_call_only_caps_remain_an_explicit_non_dollar_mode(self):
        # Compatibility, stated rather than accidental: a deployment that set
        # only request counts has no dollar promise, and must keep working.
        ledger = self.ledger(usage.documented_pricing())
        _caps(ledger, run_calls=10, project_calls=10)

        operation = ledger.reserve(_request())

        self.assertEqual("RESERVED", operation.charge_state)
        self.assertEqual(0, operation.reserved_micros)
        self.assertFalse(ledger.remaining("run", "proj/run-a").usd_enforced)
        self.assertIsNone(ledger.remaining("run", "proj/run-a").ceiling_micros)


class ChargeUncertaintyTests(CapCase):
    def test_a_successful_call_with_unknown_cost_is_not_a_provider_error(self):
        ledger = self.ledger()
        _caps(ledger)
        operation = ledger.reserve(_request())
        ledger.mark_dispatch_intent(operation.operation_id)

        held = ledger.mark_unknown(
            operation.operation_id, reason="CHARGE_UNVERIFIED_AFTER_SUCCESS",
            execution_outcome="OK")

        self.assertEqual("UNKNOWN", held.charge_state)
        self.assertEqual("OK", held.execution_outcome)
        self.assertEqual("CHARGE_UNVERIFIED_AFTER_SUCCESS", held.outcome)
        self.assertEqual(1_000, held.reserved_micros)
        self.assertIsNone(held.actual_micros)

    def test_unknown_hold_survives_reopen_and_cannot_be_zeroed_or_released(self):
        ledger = self.ledger()
        _caps(ledger)
        operation = ledger.reserve(_request())
        ledger.mark_dispatch_intent(operation.operation_id)
        ledger.mark_unknown(operation.operation_id,
                            reason="PROVIDER_ERROR_UNVERIFIED",
                            execution_outcome="FAILED")
        del ledger

        reopened = self.ledger()
        row = reopened.get(operation.operation_id)
        self.assertEqual("UNKNOWN", row.charge_state)
        self.assertEqual("FAILED", row.execution_outcome)
        self.assertEqual(1_000, row.reserved_micros)
        self.assertIsNone(row.actual_micros)
        report = reopened.report("proj")
        self.assertEqual(1_000, report["money"]["unknown_exposure_micros"])
        self.assertEqual({"FAILED": 1}, report["by_execution_outcome"])
        self.assertEqual({"PROVIDER_ERROR_UNVERIFIED": 1},
                         report["by_unknown_reason"])

        with self.assertRaises(usage.LedgerRefused):
            reopened.settle(operation.operation_id, actual_micros=0,
                            actual_source=usage.ActualSource.DERIVED)
        with self.assertRaises(usage.LedgerRefused) as ctx:
            reopened.reconcile_no_charge(operation.operation_id, evidence_ref="")
        self.assertEqual("EVIDENCE_REQUIRED", ctx.exception.code)
        still = reopened.get(operation.operation_id)
        self.assertEqual("UNKNOWN", still.charge_state)
        self.assertEqual(1_000, still.reserved_micros)

    def test_an_unobserved_execution_outcome_is_distinct_from_charge_state(self):
        ledger = self.ledger()
        _caps(ledger)
        operation = ledger.reserve(_request())
        ledger.mark_dispatch_intent(operation.operation_id)

        held = ledger.mark_unknown(operation.operation_id, reason="AMBIGUOUS_RESPONSE")

        self.assertEqual("UNKNOWN", held.charge_state)
        self.assertEqual("", held.execution_outcome)
        with self.assertRaises(usage.LedgerRefused) as ctx:
            ledger.mark_unknown(operation.operation_id, reason="AMBIGUOUS_RESPONSE",
                                execution_outcome="NOT_A_STATE")
        self.assertEqual("INVALID_REQUEST", ctx.exception.code)

    def test_gateway_keeps_execution_outcome_and_charge_uncertainty_apart(self):
        for adapter_outcome, gateway_status, reason, execution_outcome in (
                (contract.OutcomeCode.OK, "OK", "CHARGE_UNVERIFIED_AFTER_SUCCESS", "OK"),
                (contract.OutcomeCode.FAILED, "FAILED", "PROVIDER_ERROR_UNVERIFIED", "FAILED"),
                (contract.OutcomeCode.UNKNOWN, "UNKNOWN", "AMBIGUOUS_RESPONSE", "UNKNOWN")):
            with self.subTest(adapter_outcome=adapter_outcome):
                path = self.root / f"gateway-{adapter_outcome.value}.sqlite3"
                ledger = usage.UsageLedger(path, pricing=usage.documented_pricing())
                _caps(ledger)
                gateway_object = _gateway(ledger, adapter_outcome)

                outcome = gateway_object.invoke(
                    capability_id="serper.search", params={"query": "venue"},
                    run_id="run-a", operation_id=f"gw-{adapter_outcome.value}",
                    account_ref="serper-main", intent_fingerprint="intent-1")

                self.assertEqual(gateway_status, outcome.status)
                row = ledger.get(f"gw-{adapter_outcome.value}")
                self.assertEqual("UNKNOWN", row.charge_state)
                self.assertEqual(reason, row.outcome)
                self.assertEqual(execution_outcome, row.execution_outcome)
                self.assertIsNone(row.actual_micros)


class _Entry:
    provider = "serper"
    transport = "rest_https"
    allowed_params = ("query",)
    availability = "ENABLED"
    tier = "T1"


class _Registry:
    def get(self, capability_id):
        return _Entry() if capability_id == "serper.search" else None


class _Policy:
    def evaluate(self, request):
        return SimpleNamespace(verdict="ALLOW", reason_code="")


class _Adapter:
    def __init__(self, outcome):
        self.outcome = outcome

    def capabilities(self):
        return ("serper.search",)

    def execute(self, request):
        return contract.AdapterResult(outcome=self.outcome, actual_usd=None,
                                      actual_source=contract.ActualSource.NONE,
                                      upstream_ref="")


def _gateway(ledger, outcome):
    def ledger_request(entry, request):
        return usage.OperationRequest(
            project="proj", run_id=request.run_id,
            operation_id=request.operation_id, provider=entry.provider,
            capability="search", account_ref="serper-main",
            transport=entry.transport, intent="gateway charge-state test",
            args=dict(request.params))

    return gateway.CapabilityGateway(
        registry=_Registry(), policy=_Policy(), ledger=ledger,
        adapters={"serper": _Adapter(outcome)}, contract=contract,
        ledger_request=ledger_request,
        refusal_disposition=usage.refusal_disposition)


class ConcurrentCapTests(CapCase):
    def test_concurrent_reservations_stop_at_run_and_project_calls(self):
        ledger = self.ledger()
        ledger.set_cap("run", "proj/run-a", ceiling_calls=3, source="synthetic-consent")
        ledger.set_cap("run", "proj/run-b", ceiling_calls=10, source="synthetic-consent")
        ledger.set_cap("project", "proj", ceiling_calls=5, source="synthetic-consent")

        run_a = _race(ledger, "run-a", 12)
        self.assertEqual(3, len([code for code in run_a if code == "OK"]), run_a)
        self.assertEqual({"OK", "BUDGET_RUN_CEILING"}, set(run_a))
        run_b = _race(ledger, "run-b", 12)
        self.assertEqual(2, len([code for code in run_b if code == "OK"]), run_b)
        self.assertEqual({"OK", "BUDGET_PROJECT_CEILING"}, set(run_b))

        reopened = self.ledger()
        rows = reopened.operations(project="proj")
        self.assertEqual(5, len(rows))
        self.assertEqual({"RESERVED"}, {row.charge_state for row in rows})
        self.assertEqual(5, reopened.remaining("project", "proj").calls_used)

    def test_a_crash_after_dispatch_intent_leaves_the_hold_reserved(self):
        script = f"""
import os, sys
sys.path.insert(0, {str(SCRIPTS)!r})
import research_usage_ledger as usage
price = usage.Price(provider="serper", capability="search", unit_micros=1000,
    quota_unit="credits", quota_per_call=1, source="synthetic-test-rate",
    version="synthetic-1", observed_at="2026-10-20T00:00:00+00:00")
ledger = usage.UsageLedger({str(self.path)!r}, pricing=usage.PricingCatalog((price,)))
ledger.set_cap("run", "proj/run-a", ceiling_calls=10, source="synthetic-consent")
ledger.set_cap("project", "proj", ceiling_calls=10, source="synthetic-consent")
op = ledger.reserve(usage.OperationRequest(project="proj", run_id="run-a",
    operation_id="crash-1", provider="serper", capability="search",
    account_ref="serper-main", transport="rest_https",
    intent="synthetic crash", args={{"query": "crash"}}))
ledger.mark_dispatch_intent(op.operation_id)
os._exit(0)
"""
        run = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True)
        self.assertEqual(0, run.returncode, run.stderr)

        reopened = self.ledger()
        row = reopened.get("crash-1")
        self.assertEqual("RESERVED", row.charge_state)
        self.assertEqual(1_000, row.reserved_micros)
        self.assertIsNotNone(row.dispatch_intent_at)
        with self.assertRaises(usage.LedgerRefused) as ctx:
            reopened.prove_no_charge("crash-1", reason="PRE_DISPATCH_REFUSAL")
        self.assertEqual("CANNOT_PROVE_NO_CHARGE_AFTER_DISPATCH", ctx.exception.code)


def _race(ledger, run_id, count):
    def one(index):
        try:
            ledger.reserve(_request(f"{run_id}-op-{index}", run_id=run_id))
            return "OK"
        except usage.LedgerRefused as exc:
            return exc.code

    with ThreadPoolExecutor(max_workers=8) as pool:
        return list(pool.map(one, range(count)))


class ConsentCapTests(CapCase):
    def configure(self, root, **overrides):
        values = dict(providers=["serper"], domains=[], max_calls=50,
                      task="synthetic dollar cap consent", confirm=True)
        values.update(overrides)
        return runtime.configure(root, **values)

    def test_run_and_project_call_ceilings_are_independent(self):
        settings = self.configure(self.root, run_max_calls=2, project_max_calls=5)
        self.assertEqual(2, settings["run_max_calls"])
        self.assertEqual(5, settings["project_max_calls"])
        self.assertEqual("REQUEST_COUNT", settings["budget_mode"])

        ledger = self.ledger()
        plan = runtime.state_ledger_caps(self.root, "run-a", project="proj",
                                         ledger=ledger)
        self.assertEqual(2, plan["run_max_calls"])
        ledger.set_cap("run", "proj/run-b", ceiling_calls=5, source="synthetic-consent")

        self.assertEqual("OK", _reserve(ledger, "run-a", "a-1"))
        self.assertEqual("OK", _reserve(ledger, "run-a", "a-2"))
        self.assertEqual("BUDGET_RUN_CEILING", _reserve(ledger, "run-a", "a-3"))
        for index in range(3):
            self.assertEqual("OK", _reserve(ledger, "run-b", f"b-{index}"))
        self.assertEqual("BUDGET_PROJECT_CEILING", _reserve(ledger, "run-b", "b-4"))

    def test_separate_calls_require_explicit_consent_and_coherent_bounds(self):
        with self.assertRaises(ValueError) as ctx:
            self.configure(self.root, confirm=False, run_max_calls=2)
        self.assertIn("FINITE_LOCAL_CONSENT_REQUIRED", str(ctx.exception))
        for overrides in ({"run_max_calls": 10, "project_max_calls": 5},
                          {"project_max_calls": 51},
                          {"run_max_calls": 0}):
            with self.subTest(overrides=overrides), self.assertRaises(ValueError):
                self.configure(self.root, **overrides)
        self.assertFalse(runtime.config(self.root))

    def test_usd_caps_are_off_by_default_and_only_explicitly_enabled(self):
        default = self.configure(self.root)
        self.assertEqual("REQUEST_COUNT", default["budget_mode"])
        self.assertIsNone(default["project_max_usd_micros"])
        self.assertIsNone(default["run_max_usd_micros"])
        self.assertFalse(runtime.status(self.root)["dollar_limit_enforced"])

        priced = self.configure(self.root, amend=True, project_max_usd_micros=5_000_000,
                                run_max_usd_micros=1_000_000)
        self.assertEqual("REQUEST_COUNT_AND_USD_CAP", priced["budget_mode"])
        # Pin "no operator catalog" rather than inheriting the suite-wide fixture
        # fallback that ``wave1_pricing_fixture.install()`` leaves in the process.
        with mock.patch("research_usage_ledger._pricing_from_environment",
                        return_value=usage.PricingCatalog(())):
            state = runtime.status(self.root)
            self.assertEqual("FAIL_CLOSED_PRICING_SOURCE_MISSING",
                             state["dollar_limit_enforcement"])
            self.assertFalse(state["dollar_limit_enforced"])
        self.assertEqual(5_000_000, state["usd_caps"]["project_max_usd_micros"])
        self.assertEqual(1_000_000, state["usd_caps"]["run_max_usd_micros"])

    def test_a_configured_pricing_catalog_makes_the_usd_claim_true(self):
        self.configure(self.root, project_max_usd_micros=5_000_000,
                       run_max_usd_micros=1_000_000)
        catalog = self.root / "pricing.json"
        catalog.write_text(json.dumps({"prices": [{
            "provider": "serper", "capability": "search", "unit_micros": 1_000,
            "quota_unit": "credits", "quota_per_call": 1,
            "source": "synthetic-test-rate", "version": "synthetic-1",
            "observed_at": "2026-10-20T00:00:00+00:00", "verified": True}]}),
            encoding="utf-8")
        with mock.patch.dict(os.environ, {"MARKETOPS_USAGE_PRICING": str(catalog)}):
            state = runtime.status(self.root)
            ledger = usage.for_root(self.root)
            plan = runtime.state_ledger_caps(self.root, "run-a", project="proj",
                                             ledger=ledger)
            self.assertEqual(1_000_000, plan["run_max_usd_micros"])
            self.assertTrue(ledger.remaining("run", "proj/run-a").usd_enforced)
            self.assertTrue(ledger.remaining("project", "proj").usd_enforced)
            operation = ledger.reserve(_request())
            self.assertEqual(1_000, operation.reserved_micros)

        self.assertEqual("ENFORCED", state["dollar_limit_enforcement"])
        self.assertTrue(state["dollar_limit_enforced"])
        self.assertEqual("OPERATOR_CATALOG", state["pricing_source"])

    def test_the_contract_is_zero_egress(self):
        with mock.patch.object(socket.socket, "connect",
                               side_effect=AssertionError("network attempted")), \
                mock.patch("socket.create_connection",
                           side_effect=AssertionError("network attempted")), \
                mock.patch("socket.getaddrinfo",
                           side_effect=AssertionError("network attempted")):
            self.configure(self.root, run_max_calls=2, project_max_calls=5,
                           project_max_usd_micros=5_000_000,
                           run_max_usd_micros=1_000_000)
            catalog = self.root / "pricing.json"
            catalog.write_text(json.dumps({"prices": [{
                "provider": "serper", "capability": "search", "unit_micros": 1_000,
                "quota_unit": "credits", "quota_per_call": 1,
                "source": "synthetic-test-rate", "version": "synthetic-1",
                "observed_at": "2026-10-20T00:00:00+00:00", "verified": True}]}),
                encoding="utf-8")
            with mock.patch.dict(os.environ,
                                 {"MARKETOPS_USAGE_PRICING": str(catalog)}):
                ledger = usage.for_root(self.root)
                runtime.state_ledger_caps(self.root, "run-a", project="proj",
                                          ledger=ledger)
                self.assertEqual("RESERVED",
                                 ledger.reserve(_request()).charge_state)


def _reserve(ledger, run_id, operation_id):
    try:
        ledger.reserve(_request(operation_id, run_id=run_id))
        return "OK"
    except usage.LedgerRefused as exc:
        return exc.code


if __name__ == "__main__":
    unittest.main()
