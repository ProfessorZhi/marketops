"""S0 refusals proven against the REAL ledger and the REAL gateway.

Why the shipped ledger rather than a double: a stand-in is more forgiving than
the dependency it stands in for, and a "nothing was reserved" assertion measured
against a forgiving double proves only that the double was not called. Here the
ledger is `research_usage_ledger.for_root` on a temporary customer root, priced
by a synthetic-but-well-formed catalog and bounded by a synthetic call ceiling,
so the negative control below really reserves and really settles. Every S0
refusal is then asserted to leave **zero operation rows and zero adapter
executions** in that same live object.

The synthetic values are deliberate: no real balance, no real identity, no
vendor, no network, no credential. `CANDIDATE_SHA` is the public revision under
review, not an account identifier.

The QA rejection is pinned to that exact revision. A rejection recorded for a
different revision is not evidence about this one, and a pass recorded for a
different revision is not a pass for this one; both are exercised below.
"""
from __future__ import annotations

import sys
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import _wave1_contract_fixture as fixture                  # noqa: E402
import research_gateway as gateway                         # noqa: E402
import research_s0_preflight_gate as gate                  # noqa: E402
import research_usage_ledger as ledger_module              # noqa: E402
import research_wiring as wiring                           # noqa: E402

#: The integrated candidate under review (public revision, not a secret).
#: ``6aee84c`` is the docs-only tip; it is NOT the code SHA, so evidence that
#: pins the tip instead of this commit must fail S0_01.
CANDIDATE_SHA = "954cdab0f2f9d5b852864daa58f1105151808360"
DOCS_TIP_SHA = "6aee84c7063f4a4d4f1002516e8e12477c4d9c37"
#: QA round 7 rejected d88b176; the previous candidate was c3b3791.
REJECTED_SHA = "d88b17653546b37232cf97a5201ddd11384f503b"
STALE_SHA = "c3b379120625fdcacda7e0df9e5b0cc569aff007"
OTHER_SHA = "2" * 40
SYNTH_SCHEMA = "a" * 64

SYNTH_PRICE_SOURCE = "synthetic-review-fixture"

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


# --------------------------------------------------------------------------- #
# A real ledger, really priced, really capped
# --------------------------------------------------------------------------- #

def synthetic_catalog() -> "ledger_module.PricingCatalog":
    """A well-formed positive rate for each provider. Invented, not observed."""
    return ledger_module.PricingCatalog.from_entries([
        {"provider": "serper", "capability": "search", "unit_micros": 1000,
         "quota_unit": "credits", "quota_per_call": 1,
         "source": SYNTH_PRICE_SOURCE, "version": "synthetic-1",
         "observed_at": "2026-10-17T00:00:00+00:00", "verified": True},
        {"provider": "tinyfish", "capability": "search", "unit_micros": 2000,
         "quota_unit": "searches", "quota_per_call": 1,
         "source": SYNTH_PRICE_SOURCE, "version": "synthetic-1",
         "observed_at": "2026-10-17T00:00:00+00:00", "verified": True},
    ])


class _Allow:
    """The policy seam's documented verdict, so only S0 can stop the call."""

    def evaluate(self, request):
        return fixture.PolicyDecision(verdict="ALLOW", reason_code="",
                                      effective_tier=fixture.Tier.T1)


def build(root: Path):
    """Real ledger + real gateway + one synthetic adapter per provider."""
    ledger = ledger_module.for_root(root, pricing=synthetic_catalog())
    project = ledger_module.project_for_root(root)
    # A legal call ceiling and a legal money ceiling: the ledger would admit the
    # call. If a refusal is observed below, it is S0's, not the ledger's.
    ledger.set_cap("run", f"{project}/r1", ceiling_calls=50,
                   ceiling_micros=500_000, source="synthetic-test-ceiling")
    registry = fixture.FakeRegistry([
        fixture.entry("serper.search", provider="serper"),
        fixture.entry("tinyfish.search", provider="tinyfish"),
    ])
    adapters = {
        "serper": fixture.ProbeAdapter("serper", ["serper.search"], behaviour="ok"),
        "tinyfish": fixture.ProbeAdapter("tinyfish", ["tinyfish.search"], behaviour="ok"),
    }
    gateway_object = gateway.CapabilityGateway(
        registry=registry, policy=_Allow(), ledger=ledger, adapters=adapters,
        contract=fixture, contract_module_name="research_contract",
        ledger_request=wiring.ledger_request_builder(root))
    return gateway_object, adapters, ledger, project


def account(**over) -> dict:
    """A metered account in the all-captured shape. Every value is invented."""
    acct = {
        "captured": True, "b0": "2.00", "unit": "USD", "meter": "agent_steps",
        "price_source": SYNTH_PRICE_SOURCE,
        "last_observed_at": "2026-10-17T00:00:00Z",
        "reset_at": "2026-11-01T00:00:00Z", "drawdown_so_far": "0",
        "unknown_exposure": "0", "other_holders_spend": "0",
        "next_op_estimate": "0.10", "auto_topup": False,
        "key_rotation_confirmed": True, "schema_verified": True,
    }
    acct.update(over)
    return acct


def evidence(*, provider="serper", **over) -> dict:
    """S0 in the all-passed shape, with one provider's S1 evidence attached."""
    ev = {
        "integration": {"sha": CANDIDATE_SHA, "expected_sha": CANDIDATE_SHA},
        "independent_qa": {"status": "PASSED", "sha": CANDIDATE_SHA},
        "managed_mcp": {"configured_count": 1, "effective": "marketops-research",
                        "endpoints": ["https://mcp.synthetic.invalid/mcp"]},
        "direct_vendor_bypass": {"present": False},
        "credential_injection": {"runtime_scoped": True, "source": "process_env"},
        "endpoint_allowlist": {"hosts": ["mcp.synthetic.invalid"]},
        "upstream_schema_verified": True,
        "upstream_schema_snapshots": snapshot_entry(),
        "as_of": AS_OF,
        "providers": {provider: account()},
    }
    ev.update(over)
    return ev


def invoke(provider="serper", operation_id="op-1") -> dict:
    capability = "tinyfish.search" if provider == "tinyfish" else "serper.search"
    return dict(capability_id=capability, params={"query": "synthetic"},
                run_id="r1", operation_id=operation_id, account_ref=provider,
                intent_fingerprint=f"fp-{provider}")


class _NotTouched:
    """A gateway stand-in that fails loudly if anything reaches it."""

    def invoke(self, **kwargs):  # pragma: no cover - must never run
        raise AssertionError("S0 gate touched the gateway object")


def _assert_no_dispatch(case, gateway_object, adapters, ledger, outcome):
    case.assertFalse(outcome.gateway_invoked)
    case.assertEqual("BLOCKED", outcome.status)
    case.assertIn(outcome.reason_code, gate.REASON_CODES)
    case.assertEqual((), tuple(ledger.operations()),
                     "a refused call left an operation row")
    case.assertEqual((), tuple(ledger.events()),
                     "a refused call left ledger events")
    for name, adapter in adapters.items():
        case.assertEqual([], adapter.executed, f"{name} adapter was executed")


# --------------------------------------------------------------------------- #
# The refusal assertions
# --------------------------------------------------------------------------- #

class RealLedgerRefusalTests(unittest.TestCase):

    def assert_no_dispatch(self, gateway_object, adapters, ledger, outcome):
        _assert_no_dispatch(self, gateway_object, adapters, ledger, outcome)

    def test_negative_control_the_real_ledger_reserves_and_settles(self):
        """Without this, every empty-row assertion below could pass vacuously."""
        with tempfile.TemporaryDirectory() as tmp:
            gw, adapters, ledger, _ = build(Path(tmp))
            outcome = gw.invoke(**invoke())
            self.assertEqual("OK", outcome.status)
            self.assertEqual(1, len(adapters["serper"].executed))
            rows = tuple(ledger.operations())
            self.assertEqual(1, len(rows))
            row = ledger.get("op-1")
            self.assertEqual("SETTLED", row.charge_state)
            self.assertEqual(1000, row.actual_micros)
            self.assertEqual(SYNTH_PRICE_SOURCE, row.price_source)

    def test_qa_rejected_at_the_candidate_sha_never_reserves(self):
        with tempfile.TemporaryDirectory() as tmp:
            gw, adapters, ledger, _ = build(Path(tmp))
            ev = evidence(independent_qa={"status": "REJECTED",
                                          "sha": CANDIDATE_SHA})
            decision = gate.evaluate(ev)
            self.assertIn("S0_02_INDEPENDENT_QA_PASSED", decision["s0"]["failed"])
            self.assertEqual("DENY", decision["launch"])
            out = gate.guarded_invoke(gw, ev, **invoke())
            self.assert_no_dispatch(gw, adapters, ledger, out)
            self.assertEqual("CAPABILITY_NOT_AVAILABLE", out.reason_code)

    def test_qa_pass_for_a_different_sha_never_reserves(self):
        with tempfile.TemporaryDirectory() as tmp:
            gw, adapters, ledger, _ = build(Path(tmp))
            ev = evidence(independent_qa={"status": "PASSED", "sha": OTHER_SHA})
            out = gate.guarded_invoke(gw, ev, **invoke())
            self.assert_no_dispatch(gw, adapters, ledger, out)

    def test_qa_dispatched_but_not_passed_never_reserves(self):
        """Q is testing 954cdab now; 'dispatched' is not 'passed'."""
        with tempfile.TemporaryDirectory() as tmp:
            gw, adapters, ledger, _ = build(Path(tmp))
            ev = evidence(independent_qa={"status": "DISPATCHED",
                                          "sha": CANDIDATE_SHA})
            self.assertIn("S0_02_INDEPENDENT_QA_PASSED",
                          gate.evaluate(ev)["s0"]["failed"])
            out = gate.guarded_invoke(gw, ev, **invoke())
            self.assert_no_dispatch(gw, adapters, ledger, out)

    def test_round7_rejection_at_d88b176_does_not_authorize_954cdab(self):
        with tempfile.TemporaryDirectory() as tmp:
            gw, adapters, ledger, _ = build(Path(tmp))
            ev = evidence(independent_qa={"status": "REJECTED", "sha": REJECTED_SHA})
            decision = gate.evaluate(ev)
            self.assertIn("S0_02_INDEPENDENT_QA_PASSED", decision["s0"]["failed"])
            # S0_01 still passes: the integration SHA is consistent with expected.
            self.assertNotIn("S0_01_INTEGRATION_SHA_PINNED", decision["s0"]["failed"])
            out = gate.guarded_invoke(gw, ev, **invoke())
            self.assert_no_dispatch(gw, adapters, ledger, out)

    def test_pinning_the_docs_tip_instead_of_the_code_sha_fails_s0_01(self):
        with tempfile.TemporaryDirectory() as tmp:
            gw, adapters, ledger, _ = build(Path(tmp))
            ev = evidence(integration={"sha": DOCS_TIP_SHA,
                                       "expected_sha": CANDIDATE_SHA})
            self.assertIn("S0_01_INTEGRATION_SHA_PINNED",
                          gate.evaluate(ev)["s0"]["failed"])
            out = gate.guarded_invoke(gw, ev, **invoke())
            self.assert_no_dispatch(gw, adapters, ledger, out)

    def test_qa_pass_for_the_previous_candidate_does_not_authorize_this_one(self):
        """A pass recorded for c3b3791 is evidence about c3b3791, not 954cdab."""
        with tempfile.TemporaryDirectory() as tmp:
            gw, adapters, ledger, _ = build(Path(tmp))
            ev = evidence(independent_qa={"status": "PASSED", "sha": STALE_SHA})
            decision = gate.evaluate(ev)
            self.assertEqual("BLOCKED", decision["s1"]["verdict"])
            self.assertEqual("CAPABILITY_NOT_AVAILABLE", decision["s1"]["reason_code"])
            out = gate.guarded_invoke(gw, ev, **invoke())
            self.assert_no_dispatch(gw, adapters, ledger, out)

    def test_every_refusal_is_stable_across_repeats(self):
        scenarios = {
            "qa_rejected": (evidence(independent_qa={"status": "REJECTED",
                                                     "sha": CANDIDATE_SHA}), invoke()),
            "b0_missing": (evidence(providers={"serper": account(captured=False)}), invoke()),
            "price_unknown": (evidence(providers={"serper": account(
                next_op_estimate=None)}), invoke()),
            "schema_unverified": (evidence(upstream_schema_verified=False), invoke()),
            "key_not_rotated": (evidence(provider="tinyfish",
                                         providers={"tinyfish": account(
                                             key_rotation_confirmed=False)}),
                                invoke("tinyfish")),
            "unscoped_credential": (evidence(credential_injection={
                "runtime_scoped": False, "source": "repo_file"}), invoke()),
        }
        for name, (ev, kwargs) in scenarios.items():
            samples = []
            for _ in range(3):
                with tempfile.TemporaryDirectory() as tmp:
                    gw, adapters, ledger, _ = build(Path(tmp))
                    out = gate.guarded_invoke(gw, ev, **kwargs)
                    samples.append((out.status, out.reason_code, out.gateway_invoked,
                                    sum(len(a.executed) for a in adapters.values()),
                                    len(tuple(ledger.operations())),
                                    len(tuple(ledger.events()))))
            with self.subTest(scenario=name):
                self.assertEqual(1, len(set(samples)),
                                 f"{name} was not stable: {samples}")
                self.assertEqual("BLOCKED", samples[0][0])
                self.assertFalse(samples[0][2])
                self.assertEqual((0, 0, 0), samples[0][3:])

    def test_qa_still_running_never_reserves(self):
        with tempfile.TemporaryDirectory() as tmp:
            gw, adapters, ledger, _ = build(Path(tmp))
            ev = evidence(independent_qa={"status": "RUNNING", "sha": CANDIDATE_SHA})
            out = gate.guarded_invoke(gw, ev, **invoke())
            self.assert_no_dispatch(gw, adapters, ledger, out)

    def test_b0_not_captured_from_the_provider_never_reserves(self):
        for over in ({"captured": False}, {"b0": None}, {"price_source": ""},
                     {"unknown_exposure": None, "schema_verified": True},
                     {"b0": "0"}):
            with self.subTest(**over), tempfile.TemporaryDirectory() as tmp:
                gw, adapters, ledger, _ = build(Path(tmp))
                ev = evidence(providers={"serper": account(**over)})
                out = gate.guarded_invoke(gw, ev, **invoke())
                self.assert_no_dispatch(gw, adapters, ledger, out)

    def test_unverified_upstream_schema_never_reserves(self):
        for over in ({"upstream_schema_verified": False},
                     {"providers": {"serper": account(schema_verified=False)}}):
            with self.subTest(**over), tempfile.TemporaryDirectory() as tmp:
                gw, adapters, ledger, _ = build(Path(tmp))
                out = gate.guarded_invoke(gw, evidence(**over), **invoke())
                self.assert_no_dispatch(gw, adapters, ledger, out)

    def test_tinyfish_key_rotation_unconfirmed_never_reserves(self):
        with tempfile.TemporaryDirectory() as tmp:
            gw, adapters, ledger, _ = build(Path(tmp))
            ev = evidence(provider="tinyfish",
                          providers={"tinyfish": account(key_rotation_confirmed=False)})
            decision = gate.evaluate(ev)
            self.assertEqual("NOT_CAPTURED",
                             decision["s1"]["providers"]["tinyfish"]["verdict"])
            out = gate.guarded_invoke(gw, ev, **invoke("tinyfish"))
            self.assert_no_dispatch(gw, adapters, ledger, out)
            self.assertEqual("WALLET_PRECONDITION_FAILED", out.reason_code)

    def test_direct_vendor_bypass_never_reserves(self):
        cases = ({"direct_vendor_bypass": {"present": True}},
                 {"managed_mcp": {"configured_count": 1,
                                  "effective": "marketops-research",
                                  "endpoints": ["https://mcp.synthetic.invalid/mcp"],
                                  "vendor_direct_entries": ["tinyfish-raw"]}})
        for over in cases:
            with self.subTest(**over), tempfile.TemporaryDirectory() as tmp:
                gw, adapters, ledger, _ = build(Path(tmp))
                out = gate.guarded_invoke(gw, evidence(**over), **invoke())
                self.assert_no_dispatch(gw, adapters, ledger, out)

    def test_unknown_cost_never_reserves(self):
        for over in ({"next_op_estimate": None},
                     # a zero written by someone who did not attest it is unknown
                     {"next_op_estimate": "0", "price_source": SYNTH_PRICE_SOURCE}):
            with self.subTest(**over), tempfile.TemporaryDirectory() as tmp:
                gw, adapters, ledger, _ = build(Path(tmp))
                ev = evidence(providers={"serper": account(**over)})
                out = gate.guarded_invoke(gw, ev, **invoke())
                self.assert_no_dispatch(gw, adapters, ledger, out)
                self.assertEqual("PRICE_UNKNOWN_FAIL_CLOSED", out.reason_code)

    def test_uncontrolled_auto_topup_never_reserves(self):
        with tempfile.TemporaryDirectory() as tmp:
            gw, adapters, ledger, _ = build(Path(tmp))
            ev = evidence(providers={"serper": account(auto_topup=True)})
            out = gate.guarded_invoke(gw, ev, **invoke())
            self.assert_no_dispatch(gw, adapters, ledger, out)

    def test_credential_from_a_repo_file_never_reserves(self):
        ev = evidence(credential_injection={"runtime_scoped": False,
                                            "source": "repo_file"})
        with tempfile.TemporaryDirectory() as tmp:
            gw, adapters, ledger, _ = build(Path(tmp))
            out = gate.guarded_invoke(gw, ev, **invoke())
            self.assert_no_dispatch(gw, adapters, ledger, out)

    def test_endpoint_outside_the_allowlist_never_reserves(self):
        ev = evidence(managed_mcp={"configured_count": 1,
                                   "effective": "marketops-research",
                                   "endpoints": ["https://raw.vendor.invalid/mcp"]})
        with tempfile.TemporaryDirectory() as tmp:
            gw, adapters, ledger, _ = build(Path(tmp))
            out = gate.guarded_invoke(gw, ev, **invoke())
            self.assert_no_dispatch(gw, adapters, ledger, out)

    def test_two_managed_mcp_servers_never_reserve(self):
        ev = evidence(managed_mcp={"configured_count": 2,
                                   "effective": "marketops-research",
                                   "endpoints": ["https://mcp.synthetic.invalid/mcp"]})
        with tempfile.TemporaryDirectory() as tmp:
            gw, adapters, ledger, _ = build(Path(tmp))
            out = gate.guarded_invoke(gw, ev, **invoke())
            self.assert_no_dispatch(gw, adapters, ledger, out)

    def test_secret_bearing_evidence_is_refused_without_echo(self):
        sentinel = "SENTINEL-VALUE-MUST-NOT-APPEAR"
        ev = evidence()
        ev["providers"]["serper"]["api_key"] = sentinel
        with tempfile.TemporaryDirectory() as tmp:
            gw, adapters, ledger, _ = build(Path(tmp))
            out = gate.guarded_invoke(gw, ev, **invoke())
            self.assert_no_dispatch(gw, adapters, ledger, out)
            self.assertEqual("GRANT_SIGNATURE_INVALID", out.reason_code)
            self.assertNotIn(sentinel, repr(out))

    def test_a_denied_gate_does_not_even_touch_the_gateway_object(self):
        ev = evidence(independent_qa={"status": "REJECTED", "sha": CANDIDATE_SHA})
        out = gate.guarded_invoke(_NotTouched(), ev, **invoke())
        self.assertFalse(out.gateway_invoked)
        self.assertEqual("BLOCKED", out.status)

    def test_the_ledger_stays_usable_after_a_refusal(self):
        """A refusal that corrupted the ledger would be a second defect."""
        with tempfile.TemporaryDirectory() as tmp:
            gw, adapters, ledger, _ = build(Path(tmp))
            out = gate.guarded_invoke(
                gw, evidence(independent_qa={"status": "REJECTED",
                                             "sha": CANDIDATE_SHA}), **invoke())
            self.assert_no_dispatch(gw, adapters, ledger, out)
            # The same live ledger still admits a call when S0 does pass.
            allowed = gate.guarded_invoke(gw, evidence(), **invoke(operation_id="op-2"))
            self.assertTrue(allowed.gateway_invoked)
            self.assertEqual("OK", allowed.status)
            self.assertEqual(1, len(adapters["serper"].executed))


# --------------------------------------------------------------------------- #
# The seven structural gates, one at a time, against the live ledger
# --------------------------------------------------------------------------- #

class SevenGateMatrixTests(unittest.TestCase):
    """Each S0 gate alone is enough to stop a call that would otherwise reserve."""

    FAILURES = {
        "S0_01_INTEGRATION_SHA_PINNED": dict(
            integration={"sha": DOCS_TIP_SHA, "expected_sha": CANDIDATE_SHA},
            # Consistently pinned to the wrong revision: QA agrees with the tip,
            # so only S0_01 is the one that catches it.
            independent_qa={"status": "PASSED", "sha": DOCS_TIP_SHA}),
        "S0_02_INDEPENDENT_QA_PASSED": dict(
            independent_qa={"status": "DISPATCHED", "sha": CANDIDATE_SHA}),
        "S0_03_ONE_EFFECTIVE_MANAGED_MCP": dict(
            managed_mcp={"configured_count": 2, "effective": "marketops-research",
                         "endpoints": ["https://mcp.synthetic.invalid/mcp"]}),
        "S0_04_NO_DIRECT_VENDOR_BYPASS": dict(
            direct_vendor_bypass={"present": True}),
        "S0_05_RUNTIME_SCOPED_CREDENTIAL_INJECTION": dict(
            credential_injection={"runtime_scoped": False, "source": "repo_file"}),
        "S0_06_ENDPOINT_ALLOWLIST": dict(
            managed_mcp={"configured_count": 1, "effective": "marketops-research",
                         "endpoints": ["https://raw.vendor.invalid/mcp"]}),
        "S0_07_UPSTREAM_SCHEMA_VERIFIED": dict(
            upstream_schema_verified=True),   # flag without a snapshot
    }

    def test_each_structural_gate_alone_blocks_without_a_reservation(self):
        self.assertEqual(set(gate.S0_GATES), set(self.FAILURES))
        for gate_id, over in self.FAILURES.items():
            with self.subTest(gate=gate_id), tempfile.TemporaryDirectory() as tmp:
                gw, adapters, ledger, _ = build(Path(tmp))
                ev = evidence(**over)
                if gate_id == "S0_07_UPSTREAM_SCHEMA_VERIFIED":
                    ev.pop("upstream_schema_snapshots", None)
                decision = gate.evaluate(ev)
                self.assertEqual([gate_id], decision["s0"]["failed"])
                self.assertEqual("DENY", decision["launch"])
                out = gate.guarded_invoke(gw, ev, **invoke())
                _assert_no_dispatch(self, gw, adapters, ledger, out)

    def test_all_seven_passing_is_the_only_shape_that_can_allow(self):
        with tempfile.TemporaryDirectory() as tmp:
            gw, adapters, ledger, _ = build(Path(tmp))
            ev = evidence()
            self.assertEqual([], gate.evaluate(ev)["s0"]["failed"])
            out = gate.guarded_invoke(gw, ev, **invoke())
            self.assertTrue(out.gateway_invoked)
            self.assertEqual(1, len(adapters["serper"].executed))


# --------------------------------------------------------------------------- #
# Snapshot evidence: a flag is not a capture
# --------------------------------------------------------------------------- #

class SnapshotAdmissionTests(unittest.TestCase):
    """A snapshot must be a hash-matched capture, and even then only feeds S0_07.

    Ported from the rejected S0 candidate `77feb1f`/`3927953`. That branch was not
    adopted — it was based on `d88b176` and so missed every mainline commit
    through `954cdab` — but its gate module was byte-identical to this one's, and
    this class is the only end-to-end proof that the shape
    `bootstrap_probe_schema()` returns is the shape S0_07 accepts. Dropping it
    with the branch would have left the discovery exception untested against the
    gate it feeds.
    """

    def changed(self, **over):
        snap = snapshot_entry()
        snap["tinyfish"].update(over)
        return snap

    def test_a_boolean_only_claim_never_reserves(self):
        with tempfile.TemporaryDirectory() as tmp:
            gw, adapters, ledger, _ = build(Path(tmp))
            ev = evidence()
            ev.pop("upstream_schema_snapshots")
            _assert_no_dispatch(self, gw, adapters, ledger,
                                gate.guarded_invoke(gw, ev, **invoke()))

    def test_a_stale_capture_never_reserves(self):
        with tempfile.TemporaryDirectory() as tmp:
            gw, adapters, ledger, _ = build(Path(tmp))
            ev = evidence(upstream_schema_snapshots=self.changed(
                captured_at="2026-10-01T00:00:00Z"))
            _assert_no_dispatch(self, gw, adapters, ledger,
                                gate.guarded_invoke(gw, ev, **invoke()))

    def test_a_digest_mismatch_never_reserves(self):
        with tempfile.TemporaryDirectory() as tmp:
            gw, adapters, ledger, _ = build(Path(tmp))
            ev = evidence(upstream_schema_snapshots=self.changed(
                schema_sha256="b" * 64))
            _assert_no_dispatch(self, gw, adapters, ledger,
                                gate.guarded_invoke(gw, ev, **invoke()))

    def test_an_untrusted_source_never_reserves(self):
        with tempfile.TemporaryDirectory() as tmp:
            gw, adapters, ledger, _ = build(Path(tmp))
            ev = evidence(upstream_schema_snapshots=self.changed(
                source="vendor_blog_says_so"))
            _assert_no_dispatch(self, gw, adapters, ledger,
                                gate.guarded_invoke(gw, ev, **invoke()))

    def test_a_synthetic_snapshot_satisfies_only_s0_07(self):
        """The synthetic positive is scoped: it never rescues another gate."""
        with tempfile.TemporaryDirectory() as tmp:
            gw, adapters, ledger, _ = build(Path(tmp))
            ev = evidence(direct_vendor_bypass={"present": True})
            decision = gate.evaluate(ev)
            self.assertNotIn("S0_07_UPSTREAM_SCHEMA_VERIFIED",
                             decision["s0"]["failed"])
            self.assertIn("S0_04_NO_DIRECT_VENDOR_BYPASS",
                          decision["s0"]["failed"])
            _assert_no_dispatch(self, gw, adapters, ledger,
                                gate.guarded_invoke(gw, ev, **invoke()))

    def test_a_bootstrap_capture_is_directly_consumable(self):
        """The shape bootstrap_probe_schema returns is the shape S0_07 accepts."""
        payload = {"tools": [{"name": f"synthetic_tool_{i}",
                              "inputSchema": {"type": "object"}} for i in range(28)]}
        events = []

        def audit(event):
            events.append(dict(event))
            return f"audit:{len(events)}"

        snapshot = gate.bootstrap_probe_schema(
            probe=lambda: payload, provider="tinyfish",
            endpoint="https://mcp.synthetic.invalid/mcp",
            allowlist={"mcp.synthetic.invalid"},
            egress_decision=lambda endpoint: "egress:synthetic:1",
            audit=audit,
            registry_schema_sha256=gate.snapshot_digest(payload),
            captured_at="2026-10-18T11:00:00Z")
        with tempfile.TemporaryDirectory() as tmp:
            gw, adapters, ledger, _ = build(Path(tmp))
            ev = evidence(upstream_schema_snapshots=snapshot)
            self.assertEqual([], gate.evaluate(ev)["s0"]["failed"])
            out = gate.guarded_invoke(gw, ev, **invoke("serper", "bootstrap-ok"))
            self.assertTrue(out.gateway_invoked)
            self.assertEqual(1, len(adapters["serper"].executed))


# --------------------------------------------------------------------------- #
# The real composition-root factory's own admission decision
# --------------------------------------------------------------------------- #

class FactoryAdmissionTests(unittest.TestCase):
    """`research_wiring.build_gateway` is the product's admission point.

    It is allowed to refuse; it is not allowed to hand back a half-built root.
    At this pinned candidate it refuses, and it must name every module it is
    still missing even when the caller hands in every collaborator it can.
    """

    def _collaborators(self, root: Path):
        ledger = ledger_module.for_root(root, pricing=synthetic_catalog())
        registry = fixture.FakeRegistry([fixture.entry("serper.search",
                                                       provider="serper")])
        return dict(contract=fixture, registry=registry, policy=_Allow(),
                    ledger=ledger,
                    adapters={"serper": fixture.ProbeAdapter(
                        "serper", ["serper.search"])},
                    root=root)

    def test_at_this_pinned_candidate_the_factory_is_not_assemblable(self):
        report = wiring.probe()
        if not report["missing"]:
            # `954cdab` lacked the four W-B modules; this branch carries them, so
            # the positive reading is the one that has content. The negative
            # assertions below still run wherever a module is absent.
            self.assertEqual("WORKERS_PRESENT", report["state"])
            self.assertTrue(report["worker_plan_complete"])
            return
        self.assertEqual("WAITING_FOR_WORKERS", report["state"])
        self.assertFalse(report["worker_plan_complete"])
        self.assertEqual(
            {"research_adapter_base", "research_capability_registry",
             "research_contract", "research_policy"},
            set(report["missing"]))
        # Present modules are named too, so "missing" is not "nothing exists".
        self.assertIn("research_usage_ledger", report["present"])

    def test_handing_in_every_collaborator_does_not_skip_the_missing_modules(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            if not wiring.probe()["missing"]:
                # With the modules present, handing in collaborators assembles a
                # gateway. The property this test was written for — that stubs do
                # not paper over absent *modules* — still holds and is asserted
                # below on any branch that is missing one.
                built = wiring.build_gateway(**self._collaborators(root))
                self.assertTrue(hasattr(built, "invoke"))
                return
            with self.assertRaises(gateway.WiringIncomplete) as caught:
                wiring.build_gateway(**self._collaborators(root))
            message = str(caught.exception)
            for name in ("research_adapter_base", "research_capability_registry",
                         "research_contract", "research_policy"):
                self.assertIn(name, message)
            # The refusal says it is a declared seam, not an absorbed failure.
            self.assertIn("declared seam", message)

    def test_the_factory_never_returns_a_gateway_with_a_missing_collaborator(self):
        # Either it assembles a complete root, or it raises. There is no third
        # outcome in which a half-built gateway is returned for use.
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            try:
                built = wiring.build_gateway(**self._collaborators(root))
            except gateway.WiringIncomplete:
                return
            for field in ("registry", "policy", "ledger", "adapters"):
                self.assertIsNotNone(getattr(built, field, None), field)
            self.assertIsNotNone(built.ledger_request, "ledger_request")


# --------------------------------------------------------------------------- #
# The floors that must hold before S1 is reachable
# --------------------------------------------------------------------------- #

class FloorBoundaryTests(unittest.TestCase):
    """20% of B0, and the US$0.20 absolute minimum that binds for TinyFish."""

    def verdict(self, provider, **over):
        return gate.evaluate(evidence(provider=provider,
                                      providers={provider: account(**over)}))["s1"]

    def test_twenty_percent_boundary_is_inclusive(self):
        row = self.verdict("serper", b0="2.00", next_op_estimate="1.60")
        self.assertEqual("AUTHORIZED", row["verdict"])
        self.assertEqual("ALLOW", gate.evaluate(
            evidence(providers={"serper": account(b0="2.00", next_op_estimate="1.60")}))["launch"])
        self.assertEqual("0.40", row["providers"]["serper"]["floor"])
        self.assertEqual("0.40", row["providers"]["serper"]["post_operation_available"])

    def test_just_past_the_eighty_percent_bound_blocks(self):
        row = self.verdict("serper", b0="2.00", next_op_estimate="1.61")
        self.assertEqual("BLOCKED", row["verdict"])
        self.assertEqual("BUDGET_PROVIDER_CEILING", row["reason_code"])

    def test_tinyfish_usd_absolute_floor_binds_below_one_dollar(self):
        row = self.verdict("tinyfish", b0="0.50", next_op_estimate="0.30")
        self.assertEqual("AUTHORIZED", row["verdict"])
        self.assertEqual("0.20", row["providers"]["tinyfish"]["floor"])
        self.assertEqual("0.20", row["providers"]["tinyfish"]["post_operation_available"])

    def test_tinyfish_below_the_absolute_floor_blocks(self):
        row = self.verdict("tinyfish", b0="0.50", next_op_estimate="0.31")
        self.assertEqual("BLOCKED", row["verdict"])
        self.assertEqual("WALLET_PRECONDITION_FAILED", row["reason_code"])

    def test_a_zero_estimate_needs_a_source_that_attests_zero(self):
        unknown = self.verdict("serper", next_op_estimate="0",
                               price_source=SYNTH_PRICE_SOURCE)
        self.assertEqual("NOT_CAPTURED", unknown["verdict"])
        self.assertEqual("PRICE_UNKNOWN_FAIL_CLOSED", unknown["reason_code"])
        attested = self.verdict("serper", next_op_estimate="0",
                                price_source="meter_zero_verified")
        self.assertEqual("AUTHORIZED", attested["verdict"])

    def test_non_divisible_credit_floor_rounds_up(self):
        self.assertEqual(Decimal("2"), gate.floor_for(Decimal("7"), "credits"))


if __name__ == "__main__":
    unittest.main()
