"""Integration: the S0 gate against the REAL composition root and Gateway class.

This file imports and exercises `research_wiring` and `research_gateway` as they
exist on the integrated SHA — no boolean stand-ins for the gateway itself. The
collaborators below are plain classes that implement the documented protocol
(`registry.get`, `policy.evaluate`, `ledger.reserve`/`mark_*`/`settle`,
`adapter.capabilities`/`execute`), so the observed dispatch is the real one.

One thing is injected rather than imported: `CapabilityRequest`. At this SHA
`research_contract` is **absent** (see `CompositionRootTests`), so the contract's
request shape is supplied locally. That absence is the finding, not something
this file works around quietly.

No network, no vendor SDK, no credentials, no paid call.
"""
from __future__ import annotations

import sys
import tempfile
import types
import unittest
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import research_s0_preflight_gate as gate          # noqa: E402
import research_gateway as gateway                 # noqa: E402
import research_wiring as wiring                   # noqa: E402

SYNTH_SHA = "1" * 40
OTHER_SHA = "2" * 40
SYNTH_SCHEMA = "a" * 64


# --- documented-protocol collaborators (plain classes, not flags) -----------

@dataclass(frozen=True)
class CapabilityRequest:
    capability_id: str
    params: Mapping[str, Any]
    run_id: str
    operation_id: str
    account_ref: str
    intent_fingerprint: str
    grant_ref: str | None = None


class _Contract:
    """The contract module's shape, supplied locally because it is absent here."""

    CapabilityRequest = CapabilityRequest


class _Entry:
    provider = "tinyfish"
    transport = "mcp"
    allowed_params = frozenset({"query"})
    availability = "ENABLED"
    tier = "T1"


class _Registry:
    def get(self, capability_id):
        return _Entry() if capability_id == "tinyfish.search" else None


class _Policy:
    #: A verdict outside {ALLOW, DENY, REQUIRE_HUMAN_GRANT} or a reason code
    #: outside the frozen set makes the real gateway raise ContractViolation, so
    #: this double must speak the same closed set the contract does.
    _CODES = {"DENY": "POLICY_TIER_NOT_GRANTED",
              "REQUIRE_HUMAN_GRANT": "POLICY_GRANT_EXPIRED"}

    def __init__(self, verdict="ALLOW"):
        self.verdict = verdict

    def evaluate(self, request):
        return types.SimpleNamespace(verdict=self.verdict,
                                     reason_code=self._CODES.get(self.verdict, ""))


class _Ledger:
    """Records the ledger verbs the gateway calls, in order."""

    def __init__(self):
        self.verbs = []

    def reserve(self, request):
        self.verbs.append("reserve")
        return types.SimpleNamespace(operation_id="ledger-op-1")

    def mark_dispatch_intent(self, operation_id):
        self.verbs.append("intent")

    def prove_no_charge(self, operation_id, reason=""):
        self.verbs.append("prove_no_charge")

    def mark_unknown(self, operation_id, reason=""):
        self.verbs.append("unknown")

    def settle(self, operation_id, **kwargs):
        self.verbs.append("settle")


class _Adapter:
    """A real adapter outcome: it raises a closed-set refusal instead of calling out."""

    def __init__(self, refusal="EGRESS_REFUSED"):
        self.executed = 0
        self.refusal = refusal

    def capabilities(self):
        return ("tinyfish.search",)

    def execute(self, request):
        self.executed += 1
        raise ValueError(self.refusal)


class _CountingGateway(gateway.CapabilityGateway):
    """The real Gateway class, with an observation counter on its one entry point."""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.invoke_calls = 0

    def invoke(self, **kwargs):
        self.invoke_calls += 1
        return super().invoke(**kwargs)


def make_gateway(policy_verdict="ALLOW"):
    adapter = _Adapter()
    ledger = _Ledger()
    gw = _CountingGateway(
        registry=_Registry(), policy=_Policy(policy_verdict), ledger=ledger,
        adapters={"tinyfish": adapter}, contract=_Contract(),
        ledger_request=lambda entry, request: request, refusal_disposition=None)
    return gw, adapter, ledger


def evidence(**over):
    ev = {
        "integration": {"sha": SYNTH_SHA, "expected_sha": SYNTH_SHA},
        "independent_qa": {"status": "PASSED", "sha": SYNTH_SHA},
        "managed_mcp": {"configured_count": 1, "effective": "marketops-research",
                        "endpoints": ["https://mcp.synthetic.invalid/mcp"]},
        "direct_vendor_bypass": {"present": False},
        "credential_injection": {"runtime_scoped": True, "source": "process_env"},
        "endpoint_allowlist": {"hosts": ["mcp.synthetic.invalid"]},
        "upstream_schema_verified": True,
        "upstream_schema_snapshots": snapshot_entry(),
        "as_of": AS_OF,
        "providers": {"tinyfish": {
            "captured": True, "b0": "2.00", "unit": "USD", "meter": "agent_steps",
            "price_source": "synthetic-finish-receipt",
            "last_observed_at": "2026-10-17T00:00:00Z", "reset_at": "2026-11-01T00:00:00Z",
            "drawdown_so_far": "0", "unknown_exposure": "0", "other_holders_spend": "0",
            "next_op_estimate": "0.10", "auto_topup": False,
            "key_rotation_confirmed": True, "schema_verified": True}},
    }
    ev.update(over)
    return ev


AS_OF = "2026-10-18T12:00:00Z"


def snapshot_entry(provider: str = "tinyfish") -> dict:
    """A synthetic, hash-matched capture. Positive only for S0_07."""
    payload = {"tools": [{"name": f"synthetic_tool_{i}",
                          "inputSchema": {"type": "object"}} for i in range(28)]}
    serialized = gate.canonical_snapshot(payload)
    digest = gate.snapshot_digest(payload)
    return {provider: {
        "version": 1, "provider": provider,
        "endpoint": "https://mcp.synthetic.invalid/mcp",
        "method": "tools/list", "source": "probe_schema@official_mcp",
        "captured_at": "2026-10-18T11:00:00Z",
        "egress_decision_ref": "egress:synthetic:1",
        "audit_ref": "audit:synthetic:intent|audit:synthetic:result",
        "tool_count": len(payload["tools"]),
        "snapshot_serialized": serialized,
        "schema_sha256": digest,
        "registry_schema_sha256": digest,
    }}


INVOKE = dict(capability_id="tinyfish.search", params={"query": "synthetic"},
              run_id="run-1", operation_id="op-1", account_ref="acct-1",
              intent_fingerprint="fp-1")


class CompositionRootTests(unittest.TestCase):
    """What the integrated tree can actually assemble."""

    def test_probe_reports_the_missing_collaborators(self):
        report = wiring.probe()
        if not report["missing"]:
            # The pinned candidate `954cdab` lacked the four W-B modules; this
            # branch carries them. The probe's positive reading is what runs now,
            # and the negative one is still asserted on any branch that lacks a
            # module, so neither world can pass this vacuously.
            self.assertEqual("WORKERS_PRESENT", report["state"])
            self.assertTrue(report["worker_plan_complete"])
            return
        self.assertEqual("WAITING_FOR_WORKERS", report["state"])
        self.assertFalse(report["worker_plan_complete"])
        self.assertEqual(
            {"research_adapter_base", "research_capability_registry",
             "research_contract", "research_policy"},
            set(report["missing"]))
        # Present modules are listed, so "missing" is not read as "nothing exists".
        self.assertIn("research_usage_ledger", report["present"])

    def test_build_gateway_refuses_to_assemble_at_this_sha(self):
        if not wiring.probe()["missing"]:
            # Same role change as the probe test: modules landed, so the factory
            # assembles. It is still required to say what it cannot find on a
            # branch where something is absent, and that branch is asserted below.
            with tempfile.TemporaryDirectory() as tmp:
                built = wiring.build_gateway(root=Path(tmp))
            self.assertTrue(hasattr(built, "invoke"))
            return
        with self.assertRaises(gateway.WiringIncomplete) as ctx:
            wiring.build_gateway()
        message = str(ctx.exception)
        for name in ("research_contract", "research_capability_registry",
                     "research_policy", "research_adapter_base"):
            self.assertIn(name, message)

    def test_the_refusal_is_not_a_stub_fallback(self):
        # A gateway assembled from defaults would make a half-built root look
        # wired. Assert the wiring module has no such default.
        import inspect
        source = inspect.getsource(wiring.build_gateway)
        self.assertNotIn("or _Stub", source)
        self.assertIn("WiringIncomplete", source)


class RealGatewayDispatchTests(unittest.TestCase):
    """No paid operation may dispatch while S0 is rejected or B0 is absent."""

    def test_s0_rejected_never_reaches_the_real_gateway(self):
        gw, adapter, ledger = make_gateway()
        ev = evidence(independent_qa={"status": "RUNNING", "sha": SYNTH_SHA})
        out = gate.guarded_invoke(gw, ev, **INVOKE)
        self.assertFalse(out.gateway_invoked)
        self.assertEqual(0, gw.invoke_calls)
        self.assertEqual(0, adapter.executed)
        self.assertEqual([], ledger.verbs)
        self.assertEqual("BLOCKED", out.status)

    def test_no_b0_never_reaches_the_real_gateway(self):
        for over in ({"captured": False}, {"unknown_exposure": None},
                     {"price_source": ""}, {"key_rotation_confirmed": False},
                     {"b0": "0"}):
            with self.subTest(**over):
                gw, adapter, ledger = make_gateway()
                ev = evidence()
                ev["providers"]["tinyfish"].update(over)
                out = gate.guarded_invoke(gw, ev, **INVOKE)
                self.assertFalse(out.gateway_invoked)
                self.assertEqual(0, gw.invoke_calls)
                self.assertEqual(0, adapter.executed)
                self.assertEqual([], ledger.verbs)

    def test_qa_passed_for_a_stale_sha_is_refused(self):
        gw, adapter, _ = make_gateway()
        out = gate.guarded_invoke(
            gw, evidence(independent_qa={"status": "PASSED", "sha": OTHER_SHA}), **INVOKE)
        self.assertFalse(out.gateway_invoked)
        self.assertEqual(0, adapter.executed)

    def test_unverified_upstream_snapshot_is_refused(self):
        gw, adapter, _ = make_gateway()
        out = gate.guarded_invoke(gw, evidence(upstream_schema_verified=False), **INVOKE)
        self.assertFalse(out.gateway_invoked)
        self.assertEqual(0, adapter.executed)

    def test_allowed_gate_reaches_the_real_gateway_once(self):
        gw, adapter, ledger = make_gateway()
        out = gate.guarded_invoke(gw, evidence(), **INVOKE)
        self.assertTrue(out.gateway_invoked)
        self.assertEqual(1, gw.invoke_calls)
        self.assertEqual(1, adapter.executed)
        self.assertIn("reserve", ledger.verbs)   # the gateway really reserved
        self.assertEqual("BLOCKED", out.status)  # adapter's own refusal, not ours
        self.assertEqual("EGRESS_REFUSED", out.reason_code)

    def test_negative_control_the_real_gateway_can_actually_dispatch(self):
        """Without this, the 'never reached' cases above could pass vacuously."""
        gw, adapter, _ = make_gateway()
        outcome = gw.invoke(**INVOKE)
        self.assertEqual(1, adapter.executed)
        self.assertEqual("BLOCKED", outcome.status)
        self.assertEqual("EGRESS_REFUSED", outcome.reason_code)

    def test_policy_denial_still_blocks_after_an_allowing_gate(self):
        """The gate is an addition, not a replacement: the gateway still decides."""
        gw, adapter, ledger = make_gateway(policy_verdict="DENY")
        out = gate.guarded_invoke(gw, evidence(), **INVOKE)
        self.assertTrue(out.gateway_invoked)
        self.assertEqual(0, adapter.executed)     # policy stopped it before dispatch
        self.assertNotIn("reserve", ledger.verbs)
        self.assertEqual("BLOCKED", out.status)


class EvidenceSchemaTests(unittest.TestCase):

    def test_identity_shaped_evidence_is_refused_by_name(self):
        ev = evidence()
        ev["providers"]["tinyfish"]["wallet_id"] = "SENTINEL-VALUE"
        out = gate.guarded_invoke(make_gateway()[0], ev, **INVOKE)
        self.assertFalse(out.gateway_invoked)
        self.assertNotIn("SENTINEL-VALUE", repr(out))

    def test_schema_validator_flags_identity_and_unknown_fields(self):
        problems = gate.validate_account_evidence(
            {**evidence()["providers"]["tinyfish"], "account_id": "x", "balance_usd": "1"})
        self.assertIn("IDENTITY_FIELD_FORBIDDEN:account_id", problems)
        self.assertIn("UNKNOWN_FIELD:balance_usd", problems)


if __name__ == "__main__":
    unittest.main()
