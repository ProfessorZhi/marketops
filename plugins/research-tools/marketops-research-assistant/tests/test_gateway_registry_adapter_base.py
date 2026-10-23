"""The managed adapter base, and whether the real adapters can actually be called.

The five adapters were written independently, so the claim "the gateway can dispatch
all five" is measured here rather than assumed. On this branch the adapters are not
present, so the conformance test runs against a synthetic adapter and the real
adapters are checked in the integration scratch tree (see the status file).

Offline: no network, no credential, no provider call.
"""
from __future__ import annotations

import sys
import unittest
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import research_adapter_base as base
import research_capability_registry as registry_module
import research_contract as contract


class SyntheticAdapter(base.ManagedAdapter):
    """A real, minimal adapter: it serves one capability and returns a real result."""

    provider = "synthetic"
    capability_ids = ("synthetic.search",)
    transport = "rest"

    def __init__(self):
        self.calls = 0

    def _dispatch(self, req):
        self.calls += 1
        return {"outcome": contract.OutcomeCode.OK, "payload": {"echo": req.params},
                "actual_usd": Decimal("0.001"),
                "actual_source": contract.ActualSource.PROVIDER_REPORTED,
                "upstream_ref": "req-1"}


def request(capability_id="synthetic.search", params=None):
    return contract.CapabilityRequest(
        capability_id=capability_id, params=dict(params or {"q": "venue"}), run_id="run-01",
        operation_id="op-01", account_ref="acct", intent_fingerprint="intent-1")


class ManagedAdapterTests(unittest.TestCase):
    def test_a_conformant_adapter_is_reported_conformant(self):
        report = base.check_adapter_conformance(SyntheticAdapter(),
                                                expected_provider="synthetic")
        self.assertTrue(report.conformant, report.problems)
        self.assertTrue(report.execute_takes_request)
        self.assertEqual(report.capabilities, ("synthetic.search",))

    def test_dispatch_happens_exactly_once(self):
        adapter = SyntheticAdapter()
        result = adapter.execute(request())
        self.assertEqual(adapter.calls, 1)
        self.assertIs(result.outcome, contract.OutcomeCode.OK)
        self.assertEqual(result.upstream_ref, "req-1")

    def test_an_adapter_refuses_a_capability_it_does_not_serve(self):
        with self.assertRaises(base.AdapterRefusal) as ctx:
            SyntheticAdapter().execute(request("synthetic.other"))
        self.assertEqual(ctx.exception.reason_code, "CAPABILITY_NOT_AVAILABLE")

    def test_an_adapter_is_given_the_contract_request_not_a_mapping(self):
        with self.assertRaises(base.AdapterRefusal):
            SyntheticAdapter().execute({"capability_id": "synthetic.search"})

    def test_a_refusal_code_outside_the_closed_set_is_a_bug(self):
        with self.assertRaises(ValueError):
            base.AdapterRefusal("MADE_UP_CODE")

    def test_a_blocked_result_without_a_reason_is_refused_at_the_adapter(self):
        class Blocked(base.ManagedAdapter):
            provider = "synthetic"
            capability_ids = ("synthetic.search",)

            def _dispatch(self, req):
                return {"outcome": contract.OutcomeCode.BLOCKED, "caveats": ("nope",)}

        with self.assertRaises(contract.ContractError):
            Blocked().execute(request())

    def test_a_float_amount_is_refused(self):
        class Floaty(base.ManagedAdapter):
            provider = "synthetic"
            capability_ids = ("synthetic.search",)

            def _dispatch(self, req):
                return {"outcome": contract.OutcomeCode.OK, "actual_usd": 0.001}

        with self.assertRaises(contract.ContractError):
            Floaty().execute(request())

    def test_probe_schema_never_invents_a_snapshot(self):
        self.assertEqual(SyntheticAdapter().probe_schema(), {"status": "AUTH_REQUIRED"})

    def test_cancel_is_not_claimed_when_it_is_not_implemented(self):
        with self.assertRaises(base.AdapterRefusal) as ctx:
            SyntheticAdapter().cancel("ref-1")
        self.assertEqual(ctx.exception.reason_code, "CAPABILITY_NOT_AVAILABLE")


class ConformanceDetectionTests(unittest.TestCase):
    """The check must catch the shapes the real adapters actually have."""

    def test_a_jev_shaped_execute_is_reported_not_crashed(self):
        class RootTakingAdapter:
            provider = "typesafe_jev"
            capabilities = tuple  # a real class attribute in the Jev adapter is a method

            def capabilities(self):  # noqa: F811 - the collision is the point
                return ("jev.choice",)

            def probe_schema(self):
                return {"status": "AUTH_REQUIRED"}

            def execute(self, root, *, run_id, operation_id, question, **kwargs):
                raise AssertionError("the gateway calls execute(request)")

            def cancel(self, upstream_ref):
                return {}

        report = base.check_adapter_conformance(RootTakingAdapter(),
                                                expected_provider="typesafe_jev")
        self.assertFalse(report.conformant)
        self.assertIn("EXECUTE_SIGNATURE_NOT_CALLABLE_AS_execute(request)", report.problems)

    def test_capabilities_outside_the_registry_are_reported(self):
        class UnreviewedAdapter(SyntheticAdapter):
            capability_ids = ("synthetic.search", "synthetic.brand_new")

        table = registry_module.CapabilityRegistry((
            registry_module.build_entry(
                "synthetic.search", "synthetic", "search", "rest",
                contract.EffectClass.BILLABLE_READ, contract.Tier.T2, "synthetic:search",
                "search_query", {"q": "string"}, "search"),))
        report = base.check_adapter_conformance(
            UnreviewedAdapter(), expected_provider="synthetic", registry=table)
        self.assertFalse(report.conformant)
        self.assertIn("CAPABILITIES_NOT_IN_REGISTRY:synthetic.brand_new", report.problems)

    def test_an_adapter_with_no_capabilities_is_not_conformant(self):
        class Empty(SyntheticAdapter):
            capability_ids = ()

        report = base.check_adapter_conformance(Empty())
        self.assertIn("NO_CAPABILITIES_DECLARED", report.problems)

    def test_a_provider_mismatch_is_reported(self):
        report = base.check_adapter_conformance(SyntheticAdapter(),
                                                expected_provider="tinyfish")
        self.assertIn("PROVIDER_MISMATCH:synthetic!=tinyfish", report.problems)


class RealAdapterVocabularyTests(unittest.TestCase):
    """The registry's capability ids must be ones the real adapters advertise.

    The adapters are other lanes' modules and are not on this branch, so this
    compares against the vocabulary recorded from the integration tree rather than
    importing them; when they are present (integration scratch), the same check
    runs against the modules themselves in `test_billing_gateway_real_ledger`-style
    probes owned by the integrator.
    """

    @classmethod
    def advertised(cls) -> dict:
        """What each adapter says it serves, read from the adapters themselves: a
        hand-kept copy here had to be edited for every new capability."""
        return {provider: tuple(getattr(__import__(module_name), class_name)().capabilities())
                for module_name, (class_name, provider) in cls.MODULES.items()}

    def test_the_registry_covers_every_advertised_capability(self):
        for provider, ids in self.advertised().items():
            for capability_id in ids:
                with self.subTest(capability_id=capability_id):
                    entry = registry_module.DEFAULT_REGISTRY.get(capability_id)
                    self.assertIsNotNone(entry, f"{capability_id} is advertised but unreviewed")
                    self.assertEqual(entry.provider, provider)

    def test_the_registry_invents_no_capability_the_adapters_do_not_serve(self):
        # A deferred, disabled entry is reviewed for later and may have no adapter
        # yet; everything that can be called must be served by its adapter.
        advertised = {cap for ids in self.advertised().values() for cap in ids}
        enabled = {entry.capability_id for entry in registry_module.DEFAULT_REGISTRY
                   if entry.availability.value == "ENABLED"}
        self.assertEqual(sorted(enabled - advertised), [])

    MODULES = {
        "research_adapter_tinyfish": ("TinyFishAdapter", "tinyfish"),
        "research_adapter_serpapi": ("SerpApiAdapter", "serpapi"),
        "research_adapter_serper": ("SerperAdapter", "serper"),
        "research_adapter_deepseek": ("DeepSeekAdapter", "deepseek"),
        "research_adapter_crawl4ai": ("Crawl4AIAdapter", "crawl4ai"),
        "research_adapter_jev": ("JevAdapter", "typesafe_jev"),
    }

    def test_every_adapter_conforms_or_matches_its_pinned_defect(self):
        # The shape the gateway uses — adapter.execute(request) — asserted on the
        # modules themselves when they are on sys.path. Absent modules are reported
        # as BLOCKED, never as a pass; a present defect is pinned by name.
        import importlib.util
        for module_name, (class_name, provider) in self.MODULES.items():
            with self.subTest(module=module_name):
                if importlib.util.find_spec(module_name) is None:
                    self.skipTest(f"BLOCKED: {module_name} is not on this branch")
                adapter = getattr(__import__(module_name), class_name)()
                report = base.check_adapter_conformance(
                    adapter, expected_provider=provider,
                    registry=registry_module.DEFAULT_REGISTRY)
                # Two pins died here, and neither was loosened into a pass: the
                # Jev defect pin when W-F fixed the adapter (9cb1ea4), and the
                # TinyFish "three unreviewed capabilities" gap when W-B reviewed
                # them (672447d). All five adapters are held to the same rule now.
                self.assertTrue(report.conformant, report.problems)


if __name__ == "__main__":
    unittest.main()
