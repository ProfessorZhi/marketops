"""Wave-6: the real assembly — four collaborators, a real gateway, one guarded entry.

Until this wave the gateway could not be built from the product at all. Four
modules the contract names (`research_contract`, `research_capability_registry`,
`research_policy`, `research_adapter_base`) were absent, `probe()` said
`WAITING_FOR_WORKERS`, and `build_gateway()` refused — correctly, but the refusal
was the whole product. Q's round-11 gate reported that as RED without a single
assertion failing, which is the right shape for "the capability does not exist".

The tests here are the positive half of that gate, and they are deliberately
written against **real** collaborators: the shipped registry table, the shipped
policy plane, the shipped ledger, and the shipped S0 preflight gate. Only the
vendor is synthetic, and it is a local stand-in that never dials — the property
under test is that the managed path completes without a socket, not that any real
vendor was reached.

Two things are pinned because they were measured wrong before:

* the adapter-capability -> ledger-capability bridge is **derived** from the
  registry. The hand-written copy mapped `crawl4ai.fetch_page` to `extract` while
  the registry prices it as `render`, and omitted `tinyfish.fetch_content` and
  most of the Jev set entirely;
* an adapter the gateway cannot call as `execute(request)` is **not routable**.
  `typesafe_jev` constructs but would raise `TypeError` *after* the reservation,
  which the gateway can only book as an UNKNOWN charge — a phantom hold for a
  call that never left.
"""
from __future__ import annotations

import asyncio
import inspect
import json
import os
import socket
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import research_capability_registry as registry_module  # noqa: E402
import research_policy as policy_module  # noqa: E402
import research_usage_ledger as ledger_module  # noqa: E402
import research_wiring as wiring  # noqa: E402


class _LocalTinyFish:
    """A local stand-in for the official MCP transport.

    It declares exactly one T0 capability and never opens a socket. Substituting
    it is the point: the managed path is what is being measured, and a socket
    here would mean the *gateway* dialled rather than the adapter.
    """

    provider = "tinyfish"

    def __init__(self) -> None:
        self.calls = 0

    def capabilities(self):
        return ("tinyfish.get_wallet",)

    def probe_schema(self):
        return {"status": "AUTH_REQUIRED"}

    def execute(self, request):
        self.calls += 1
        return SimpleNamespace(
            outcome="OK", payload={"balance": "synthetic"}, artifacts=(),
            upstream_ref="local:1", actual_usd=None, actual_source=None,
            quota_units_used=1, transport="official_mcp",
            schema_verdict="UNVERIFIED", caveats=())

    def cancel(self, upstream_ref):
        raise ValueError(f"CAPABILITY_NOT_AVAILABLE: {upstream_ref}")


class _Recorder:
    """An adapter that would run if the gateway ever reached it."""

    def __init__(self, provider: str) -> None:
        self.provider = provider
        self.calls = 0

    def capabilities(self):
        return ("serpapi.search", "tinyfish.run_web_automation")

    def probe_schema(self):
        return {"status": "AUTH_REQUIRED"}

    def execute(self, request):
        self.calls += 1
        return SimpleNamespace(outcome="OK", actual_usd=None, actual_source=None,
                               quota_units_used=1, upstream_ref=None,
                               schema_verdict="UNVERIFIED", caveats=())

    def cancel(self, upstream_ref):
        raise ValueError(f"CAPABILITY_NOT_AVAILABLE: {upstream_ref}")


class _Exploding:
    """A gateway that fails loudly if it is touched at all."""

    def invoke(self, **kwargs):
        raise AssertionError("the gateway was invoked despite a refused S0 gate")


def _rooted(tmp: str) -> Path:
    root = Path(tmp)
    ledger = ledger_module.for_root(root)
    project = ledger_module.project_for_root(root)
    ledger.set_cap("run", f"{project}/r1", ceiling_calls=50, source="test-fixture")
    return root


class RealAssemblyTests(unittest.TestCase):
    def test_the_factory_constructs_the_real_stack_with_no_arguments(self):
        """`build_gateway()` is the product entry, so it must work from nothing.

        A factory that only constructs when the caller supplies four objects is
        not reachable from the product: nobody in the product holds them.
        """
        report = wiring.probe()
        self.assertEqual("WORKERS_PRESENT", report["state"])
        self.assertEqual([], report["missing"])
        with tempfile.TemporaryDirectory() as tmp:
            built = wiring.build_gateway(root=_rooted(tmp))
            self.assertTrue(hasattr(built, "invoke"))
            self.assertIs(registry_module.DEFAULT_REGISTRY, built.registry)
            # The default network switch is off: the factory cannot know that S0
            # passed, and a default of on would make the policy gate inert.
            self.assertFalse(built.policy._network_enabled)

    def test_a_t0_invocation_succeeds_and_opens_no_socket(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = _rooted(tmp)
            adapter = _LocalTinyFish()
            local_pricing = ledger_module.PricingCatalog.from_entries([{
                "provider": "tinyfish", "capability": "wallet_read", "unit_micros": 0,
                "quota_unit": "requests", "quota_per_call": 1, "source": "SYNTHETIC_LOCAL_CONTROL",
                "version": "test-1", "observed_at": "2026-10-20T00:00:00+00:00",
                "verified": True, "free_tier": True}])
            built = wiring.build_gateway(
                root=root, adapters={"tinyfish": adapter},
                ledger=ledger_module.for_root(root, pricing=local_pricing),
                policy=policy_module.Policy(
                    registry=registry_module.DEFAULT_REGISTRY, network_enabled=True))

            attempts: list = []
            real_connect = socket.socket.connect

            def refuse(sock, address, *a, **k):
                attempts.append(address)
                raise AssertionError(f"a T0 invocation opened a socket to {address!r}")

            socket.socket.connect = refuse
            try:
                outcome = built.invoke(
                    capability_id="tinyfish.get_wallet", params={}, run_id="r1",
                    operation_id="r1t0", account_ref="tinyfish-main",
                    intent_fingerprint="i-t0")
            finally:
                socket.socket.connect = real_connect

            self.assertEqual([], attempts, "T0 must not dial out")
            self.assertEqual("OK", outcome.status, outcome)
            self.assertEqual("tinyfish", outcome.provider)
            self.assertEqual(1, adapter.calls)
            row = ledger_module.for_root(root).get("r1t0")
            self.assertEqual("SETTLED", row.charge_state)
            self.assertEqual(0, row.actual_micros)

    def test_paid_and_state_changing_tiers_are_refused_and_name_the_provider(self):
        """T2/T3/T4 without authorization fail closed, and say whose call it was.

        All three run against the **default** gateway, whose network switch is
        off. A refusal that did not name the provider would be indistinguishable
        from a typo in the capability id.
        """
        cases = (
            ("serpapi.search", "serpapi", "T2"),
            ("tinyfish.run_web_automation", "tinyfish", "T3"),
            ("tinyfish.cancel_run", "tinyfish", "T4"),
        )
        with tempfile.TemporaryDirectory() as tmp:
            root = _rooted(tmp)
            recorders = {"serpapi": _Recorder("serpapi"),
                         "tinyfish": _Recorder("tinyfish")}
            built = wiring.build_gateway(root=root, adapters=recorders)
            for capability_id, provider, tier in cases:
                with self.subTest(tier=tier, capability_id=capability_id):
                    outcome = built.invoke(
                        capability_id=capability_id, params={}, run_id="r1",
                        operation_id=f"r1-{tier}", account_ref=f"{provider}-main",
                        intent_fingerprint=f"i-{tier}")
                    self.assertEqual("BLOCKED", outcome.status, outcome)
                    self.assertEqual(provider, outcome.provider, outcome)
                    self.assertTrue(outcome.reason_code, outcome)
            self.assertEqual(0, sum(r.calls for r in recorders.values()),
                             "a refused tier still reached an adapter")

    def test_an_undeclared_capability_never_reaches_an_adapter(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = _rooted(tmp)
            recorder = _Recorder("serper")
            built = wiring.build_gateway(root=root, adapters={"serper": recorder})
            outcome = built.invoke(
                capability_id="__qa__.undeclared", params={}, run_id="r1",
                operation_id="r1-unknown", account_ref="serper-main",
                intent_fingerprint="i-unknown")
            self.assertEqual("BLOCKED", outcome.status, outcome)
            self.assertEqual("CAPABILITY_UNKNOWN", outcome.reason_code, outcome)
            self.assertIsNone(outcome.provider)
            self.assertEqual(0, recorder.calls)


class ReviewedVocabularyTests(unittest.TestCase):
    def test_the_bridge_is_derived_from_the_registry_not_hand_written(self):
        expected = {(entry.provider, entry.capability_id): entry.ledger_capability
                    for entry in registry_module.DEFAULT_REGISTRY
                    if entry.ledger_capability}
        self.assertEqual(expected, wiring.REVIEWED_CAPABILITY)
        self.assertGreater(len(wiring.REVIEWED_CAPABILITY), 0)

    def test_the_two_entries_the_hand_written_table_got_wrong(self):
        """Both were measured defects, not hypothetical drift."""
        self.assertEqual(
            "render", wiring.REVIEWED_CAPABILITY[("crawl4ai", "crawl4ai.fetch_page")],
            "the registry prices fetch_page as render; the old table said extract")
        self.assertEqual(
            "fetch", wiring.REVIEWED_CAPABILITY[("tinyfish", "tinyfish.fetch_content")],
            "fetch_content was missing from the hand-written table entirely")

    def test_all_five_adapters_are_routable_once_they_conform(self):
        """The rule still holds in the direction that now matters.

        This test used to assert that `typesafe_jev` was *excluded*, because it
        could not be called as `execute(request)`. W-F fixed that (`9cb1ea4`), so
        the same rule now asserts the other branch: every reviewed provider is in
        the routing table and nothing is recorded as a failure. The exclusion
        branch is still exercised by `test_gateway_integration_seam.py`, which
        branches on the measured conformance rather than on this provider.
        """
        built = wiring.assemble_adapters(registry=registry_module.DEFAULT_REGISTRY)
        failures = wiring.assemble_adapters.failures
        # All five now route. The TinyFish gap this test used to assert was
        # closed on both sides: W-B reviewed the three documented account reads
        # (`672447d`) and W-C narrowed what the adapter advertises by default
        # (`e5f333d`). The rule itself is unchanged — the registry is still the
        # allowlist, and an adapter advertising something it does not carry is
        # still refused.
        self.assertEqual({}, failures)
        for provider in ("tinyfish", "serpapi", "serper", "crawl4ai", "typesafe_jev"):
            self.assertIn(provider, built, failures.get(provider))

    def test_the_adapter_advertises_only_what_the_registry_reviews_by_default(self):
        """W-C's `include_unreviewed` seam must not become a routing bypass.

        After R17-D1 the adapter carries a **three**-state review label aligned to
        B's registry: 12 REVIEWED (advertised), 2 REVIEWED_DISABLED (reviewed but
        T4-disabled, so not advertised and not routable), and 0 UNREVIEWED. The
        default `capabilities()` is what the gateway enumerates, so it is the only
        advertisement that matters.
        """
        import research_adapter_tinyfish as tinyfish

        adapter = tinyfish.TinyFishAdapter()
        default = set(adapter.capabilities())
        reviewed = {entry.capability_id for entry in registry_module.DEFAULT_REGISTRY}
        self.assertTrue(default <= reviewed,
                        f"default advertisement outside the registry: {sorted(default - reviewed)}")
        self.assertEqual(set(), set(tinyfish.unreviewed_capabilities()),
                         "an unreviewed capability is served at all")

        disabled = {"tinyfish.cancel_run", "tinyfish.batch_cancel"}
        self.assertEqual(disabled, set(tinyfish.DISABLED_REVIEWED_CAPABILITIES))
        self.assertFalse(default & disabled,
                         "a T4-disabled capability is advertised")
        # Nothing is left for the wider view to reveal: reviewed-but-disabled is
        # still not servable, and unreviewed is empty.
        self.assertEqual(default, set(tinyfish.capabilities(include_unreviewed=True)))
        tinyfish_registry = {entry.capability_id for entry in registry_module.DEFAULT_REGISTRY
                             if entry.provider == "tinyfish"}
        self.assertEqual(default | disabled, tinyfish_registry)

class JevCompositionTests(unittest.TestCase):
    """The composition half of W-F's fix: the root has to be injected here.

    W-F's adapter reads saved evidence from a data root it deliberately does not
    take from the request — a caller that could name the store could name another
    one. `assemble_adapters()` constructs every adapter with no arguments, which
    passes conformance and leaves Jev refusing `JEV_DATA_ROOT_ABSENT` on every
    call: a routing table entry that can never execute. Injecting the root is the
    composition root's job, so it is asserted here rather than in the adapter's
    own suite.
    """

    def test_the_root_is_injected_into_the_one_adapter_that_accepts_it(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            built = wiring.assemble_adapters(
                registry=registry_module.DEFAULT_REGISTRY, root=root)
            self.assertEqual(Path(root), Path(built["typesafe_jev"]._root))

    def test_every_other_adapter_is_constructed_without_a_root(self):
        """Only the adapter that declares the parameter receives it.

        The other four take no root, and passing one blindly would be a TypeError
        at assembly for every provider — so the construction is signature-driven,
        and this is what proves it stays that way.
        """
        with tempfile.TemporaryDirectory() as tmp:
            built = wiring.assemble_adapters(
                registry=registry_module.DEFAULT_REGISTRY, root=Path(tmp))
            # `typesafe_jev` is the one that takes a root; `tinyfish` is absent
            # from the routing table for the registry gap asserted above.
            for provider in ("serpapi", "serper", "crawl4ai"):
                self.assertFalse(hasattr(built[provider], "_root"),
                                 f"{provider} was handed a root it does not take")

    def test_without_a_root_the_jev_adapter_refuses_before_dispatch(self):
        """No root is a §5.3 refusal, not an exception the gateway must guess at.

        `CAPABILITY_NOT_AVAILABLE` is what the gateway reads, so it can settle the
        reservation as NO_CHARGE instead of booking a phantom UNKNOWN for a call
        that never left the process.
        """
        import research_adapter_jev as jev

        built = wiring.assemble_adapters(registry=registry_module.DEFAULT_REGISTRY)
        request = SimpleNamespace(capability_id="jev.choice",
                                  params={"artifact_id": "art", "question": "q",
                                          "options": ["a", "b"]},
                                  run_id="r1", operation_id="o1")
        with self.assertRaises(jev.JevRefusal) as caught:
            built["typesafe_jev"].execute(request)
        self.assertEqual("JEV_DATA_ROOT_ABSENT", caught.exception.local_code)
        self.assertEqual("CAPABILITY_NOT_AVAILABLE", caught.exception.reason_code)


class GuardedEntryTests(unittest.TestCase):
    """The product entry: S0 first, and the gateway object untouched on refusal."""

    def test_without_s0_evidence_the_entry_never_reaches_the_gateway(self):
        with tempfile.TemporaryDirectory() as tmp:
            outcome = wiring.managed_invoke(
                capability_id="tinyfish.get_wallet", params={}, run_id="r1",
                operation_id="r1t0", account_ref="tinyfish-main",
                intent_fingerprint="i-t0", root=Path(tmp),
                gateway_object=_Exploding())
            self.assertFalse(outcome.gateway_invoked)
            self.assertEqual("BLOCKED", outcome.status)
            self.assertIn("S0_07_UPSTREAM_SCHEMA_VERIFIED", outcome.s0_failed)
            self.assertEqual((), tuple(ledger_module.for_root(Path(tmp)).operations()))

    def test_a_declared_boolean_alone_does_not_unlock_the_entry(self):
        """The exact failure S0_07 exists to refuse."""
        with tempfile.TemporaryDirectory() as tmp:
            outcome = wiring.managed_invoke(
                capability_id="tinyfish.get_wallet", params={}, run_id="r1",
                operation_id="r1t0", account_ref="tinyfish-main",
                intent_fingerprint="i-t0", root=Path(tmp),
                gateway_object=_Exploding(),
                evidence={"upstream_schema_verified": True})
            self.assertFalse(outcome.gateway_invoked)
            self.assertIn("S0_07_UPSTREAM_SCHEMA_VERIFIED", outcome.s0_failed)

    def test_evidence_is_read_from_the_data_root_and_an_env_var_cannot_override_it(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.assertEqual({}, wiring.load_s0_evidence(root))

            path = ledger_module.default_path(root).parent / wiring.S0_EVIDENCE_FILENAME
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps({"network_enabled": "disabled"}), encoding="utf-8")

            os.environ["S0_EVIDENCE"] = json.dumps({"network_enabled": "enabled"})
            os.environ["UPSTREAM_SCHEMA_VERIFIED"] = "true"
            try:
                self.assertEqual({"network_enabled": "disabled"},
                                 wiring.load_s0_evidence(root))
            finally:
                os.environ.pop("S0_EVIDENCE", None)
                os.environ.pop("UPSTREAM_SCHEMA_VERIFIED", None)

    def test_the_customer_facing_tool_cannot_be_handed_its_own_evidence(self):
        """An Agent must not be able to declare that S0 passed."""
        import research_mcp

        server = research_mcp.build_server()
        tools = asyncio.run(server.list_tools())
        for tool in tools:
            properties = (tool.input_schema or {}).get("properties", {})
            self.assertNotIn("evidence", properties, tool.name)
            self.assertNotIn("s0", properties, tool.name)
        parameters = inspect.signature(research_mcp.managed_call).parameters
        self.assertNotIn("evidence", parameters)

    def test_the_gate_module_is_the_one_the_entry_calls(self):
        """No second, differently-behaving gate on the product path."""
        import research_s0_preflight_gate as gate

        source = inspect.getsource(wiring.managed_invoke)
        self.assertIn("research_s0_preflight_gate", source)
        self.assertIn("guarded_invoke", source)
        self.assertTrue(hasattr(gate, "evaluate"))


if __name__ == "__main__":
    unittest.main()
