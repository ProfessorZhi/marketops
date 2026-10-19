"""The gateway against the **real** ledger, not a double.

Why this file exists, stated as the defect it was written after: the gateway's
integration tests passed for a whole wave while the gateway could not complete a
single managed call. The doubles were more forgiving than the dependency they
stood in for — the fixture ledger accepted `reserve(request, decision)` and
`settle(id, result)`, which are the *retired* ledger's signatures, so every
terminal write raised `TypeError` against the ledger that replaced it and the
failure was absorbed into a caveat. A green suite that measures the wrong contract
is worse than a red one, because it is read as evidence.

So this file drives `CapabilityGateway` with the shipped `research_usage_ledger`
and the shipped request builder, and asserts the *ledger's own rows* rather than
what a stand-in was asked to do. It is deliberately small: it is a smoke test for
the seam, not a re-test of the ledger, which has its own suites.
"""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import _wave1_contract_fixture as fixture  # noqa: E402
import research_gateway as gateway  # noqa: E402
import research_usage_ledger as ledger_module  # noqa: E402
import research_wiring as wiring  # noqa: E402


class _Allow:
    def evaluate(self, request):
        return fixture.PolicyDecision(verdict="ALLOW", reason_code="",
                                      effective_tier=fixture.Tier.T1)


def _gateway(root, *, behaviour="ok"):
    """A real ledger, the real request builder, and one synthetic adapter."""
    ledger = ledger_module.for_root(root)
    ledger.set_cap("run", f"{ledger_module.project_for_root(root)}/r1",
                   ceiling_calls=50, source="test-fixture")
    registry = fixture.FakeRegistry([fixture.entry(
        capability_id="serper.search", provider="serper",
        allowed_params=frozenset({"query"}))])
    adapter = fixture.ProbeAdapter("serper", ["serper.search"], behaviour=behaviour)
    return gateway.CapabilityGateway(
        registry=registry, policy=_Allow(), ledger=ledger,
        adapters={"serper": adapter}, contract=fixture,
        contract_module_name="research_contract",
        ledger_request=wiring.ledger_request_builder(root)), ledger


def _call(gateway_object, ledger):
    outcome = gateway_object.invoke(
        capability_id="serper.search", params={"query": "paint"}, run_id="r1",
        operation_id="r1op1", account_ref="serper", intent_fingerprint="i1")
    return outcome, ledger.get("r1op1")


class RealLedgerIntegrationTests(unittest.TestCase):
    def test_a_successful_call_settles_in_the_ledgers_own_row(self):
        with tempfile.TemporaryDirectory() as tmp:
            built = _gateway(Path(tmp))
            outcome, row = _call(*built)
            self.assertEqual("OK", outcome.status)
            self.assertEqual([], list(outcome.caveats))
            self.assertEqual("SETTLED", row.charge_state)
            # The shipped catalog prices credit-metered capabilities at 0 USD with
            # a quota unit, so the settled amount is zero *derived from the
            # recorded rate* — which is a different statement from "the vendor
            # reported zero", and the source column is what keeps them apart.
            self.assertEqual("DERIVED", row.actual_source)

    def test_a_transport_failure_holds_the_exposure_rather_than_refunding_it(self):
        with tempfile.TemporaryDirectory() as tmp:
            built = _gateway(Path(tmp), behaviour="raise_unknown")
            outcome, row = _call(*built)
            self.assertEqual("UNKNOWN", outcome.status)
            self.assertEqual("UNKNOWN", row.charge_state)
            self.assertIsNone(row.actual_micros)

    def test_a_provable_pre_dispatch_refusal_leaves_no_open_exposure(self):
        with tempfile.TemporaryDirectory() as tmp:
            built = _gateway(Path(tmp), behaviour="returned_blocked_with_code")
            outcome, row = _call(*built)
            self.assertEqual("BLOCKED", outcome.status)
            # The property that matters is not which verb ran but that the row did
            # not stay RESERVED: a refused call holding budget is a phantom against
            # the run's ceiling.
            self.assertNotEqual("RESERVED", row.charge_state)

    def test_an_unknown_capability_never_reaches_the_ledger(self):
        with tempfile.TemporaryDirectory() as tmp:
            gateway_object, ledger = _gateway(Path(tmp))
            outcome = gateway_object.invoke(
                capability_id="serper.not_reviewed", params={}, run_id="r1",
                operation_id="r1op2", account_ref="serper", intent_fingerprint="i2")
            self.assertEqual(("BLOCKED", "CAPABILITY_UNKNOWN"),
                             (outcome.status, outcome.reason_code))
            self.assertEqual((), tuple(ledger.operations()))

    def test_the_vocabulary_bridge_refuses_an_unmapped_capability(self):
        """A capability the reviewed registry has no name for is refused, not guessed."""
        with self.assertRaises(gateway.WiringIncomplete) as caught:
            wiring.reviewed_capability("serper", "serper.something_new")
        self.assertIn("REVIEWED_CAPABILITY", str(caught.exception))


if __name__ == "__main__":
    unittest.main()
