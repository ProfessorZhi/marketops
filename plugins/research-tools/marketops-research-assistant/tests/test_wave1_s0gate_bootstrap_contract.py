"""Pre-S0 discovery contract against a synthetic official-MCP wire.

The fixture (`_s0_mcp_fixture.py`) is a 28-tool synthetic catalog behind a fake
guarded egress proxy. It is **not** an official handshake and **not** a live
verification: `server.live` is False and nothing here contacts a vendor.

What this file proves, offline:
  * only `initialize` / `notifications/initialized` / `tools/list` reach the wire;
  * `tools/call` and every other chargeable method are refused before the server;
  * the allowlist, the audit refs, the content sha256, the registry digest and
    the time window are all enforced, not declared;
  * tamper and drift are rejected;
  * QA must be PASSED at the exact accepted product SHA — the docs tip cannot
    stand in for it.
"""
from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import _s0_mcp_fixture as fixture                      # noqa: E402
import research_s0_preflight_gate as gate              # noqa: E402

#: Accepted product code (Q14 OFFLINE_ACCEPTED). Public revision, not a secret.
ACCEPTED_PRODUCT_SHA = "be7f3e89fe015148b91d4066dca9c8f20a95be99"
#: The integration branch tip is docs-only and must never pass as the code SHA.
DOCS_TIP_SHA = "d2366bb1fe81808326a830c011de4f3a1b0a0974"

ENDPOINT = "https://mcp.synthetic.invalid/mcp"
HOSTS = ["mcp.synthetic.invalid"]
CAPTURED_AT = "2026-10-18T11:00:00Z"
AS_OF = "2026-10-18T12:00:00Z"


def account(**over) -> dict:
    """A synthetic metered account. Every value is invented."""
    acct = {
        "captured": True, "b0": "2.00", "unit": "USD", "meter": "steps",
        "price_source": "synthetic-fixture", "last_observed_at": CAPTURED_AT,
        "reset_at": "2026-11-01T00:00:00Z", "drawdown_so_far": "0",
        "unknown_exposure": "0", "other_holders_spend": "0",
        "next_op_estimate": "0.10", "auto_topup": False,
        "key_rotation_confirmed": True, "schema_verified": True,
    }
    acct.update(over)
    return acct


class BootstrapContractTests(unittest.TestCase):

    def setUp(self) -> None:
        self.server = fixture.SyntheticOfficialMCPServer()
        self.audit = fixture.AuditLog()
        self.wire = fixture.FakeGuardedEgressProxy(HOSTS, self.audit)
        self.registry_digest = gate.snapshot_digest({"tools": self.server.catalog})

    def capture(self, **over) -> dict:
        kwargs = dict(
            probe=lambda: self.wire.probe_schema(ENDPOINT, server=self.server),
            provider="tinyfish", endpoint=ENDPOINT, allowlist=HOSTS,
            egress_decision=self.wire.decide, audit=self.audit,
            registry_schema_sha256=self.registry_digest,
            captured_at=CAPTURED_AT)
        kwargs.update(over)
        return gate.bootstrap_probe_schema(**kwargs)

    def evidence(self, snapshot=None, providers=None, **over) -> dict:
        ev = {
            "integration": {"sha": ACCEPTED_PRODUCT_SHA,
                            "expected_sha": ACCEPTED_PRODUCT_SHA},
            "independent_qa": {"status": "PASSED", "sha": ACCEPTED_PRODUCT_SHA},
            "managed_mcp": {"configured_count": 1, "effective": "marketops-research",
                            "endpoints": [ENDPOINT]},
            "direct_vendor_bypass": {"present": False},
            "credential_injection": {"runtime_scoped": True, "source": "process_env"},
            "endpoint_allowlist": {"hosts": HOSTS},
            "upstream_schema_verified": True,
            "upstream_schema_snapshots": snapshot or self.capture(),
            "as_of": AS_OF,
            "providers": {"tinyfish": account()} if providers is None else providers,
        }
        ev.update(over)
        return ev

    # -- the allowed sequence -------------------------------------------------

    def test_the_handshake_is_exactly_initialize_initialized_tools_list(self):
        snapshot = self.capture()
        self.assertEqual(["initialize", "notifications/initialized", "tools/list"],
                         self.server.calls)
        entry = snapshot["tinyfish"]
        self.assertEqual(28, entry["tool_count"])
        self.assertEqual("probe_schema@official_mcp", entry["source"])
        self.assertEqual(gate.snapshot_digest({"tools": self.server.catalog}),
                         entry["schema_sha256"])

    def test_the_fixture_is_synthetic_and_never_claims_live_verification(self):
        self.assertFalse(self.server.live)
        entry = self.capture()["tinyfish"]
        self.assertEqual("probe_schema@official_mcp", entry["source"])
        # The capture path names itself; nothing here is a vendor assertion.
        self.assertNotIn("live", json.dumps(entry).lower())

    # -- refused methods ------------------------------------------------------

    def test_tools_call_is_refused_at_the_wire(self):
        with self.assertRaises(fixture.WireRefused) as caught:
            self.wire.send(ENDPOINT, "tools/call", server=self.server,
                           arguments={"name": "search"})
        self.assertEqual("METHOD_NOT_ALLOWED", caught.exception.code)
        self.assertEqual([], self.server.calls)
        self.assertEqual([], self.wire.frames)

    def test_every_chargeable_or_non_discovery_method_is_refused(self):
        for method in fixture.CHARGEABLE_METHODS:
            with self.subTest(method=method):
                with self.assertRaises(fixture.WireRefused) as caught:
                    self.wire.send(ENDPOINT, method, server=self.server)
                self.assertEqual("METHOD_NOT_ALLOWED", caught.exception.code)
        self.assertEqual([], self.server.calls)

    def test_an_endpoint_outside_the_allowlist_never_reaches_the_server(self):
        with self.assertRaises(fixture.WireRefused) as caught:
            self.wire.send("https://raw.vendor.invalid/mcp", "initialize",
                           server=self.server)
        self.assertEqual("EGRESS_HOST_NOT_ALLOWED", caught.exception.code)
        self.assertEqual([], self.server.calls)

    # -- audit / allowlist / digest ------------------------------------------

    def test_audit_has_intent_and_result_and_carries_no_payload(self):
        self.capture()
        events = self.audit.bootstrap_events()
        self.assertEqual(["intent", "result"], [e["phase"] for e in events])
        allowed = {"event", "phase", "provider", "endpoint", "host", "method",
                   "version", "egress_decision_ref", "tool_count", "schema_sha256"}
        for event in events:
            self.assertTrue(set(event).issubset(allowed), set(event) - allowed)
        text = json.dumps(events).lower()
        for forbidden in ("authorization", "x-api-key", "cookie", "bearer", "headers"):
            self.assertNotIn(forbidden, text)

    def test_a_registry_digest_mismatch_refuses_before_returning_a_snapshot(self):
        with self.assertRaises(gate.BootstrapRefused) as caught:
            self.capture(registry_schema_sha256="b" * 64)
        self.assertEqual("BOOTSTRAP_SCHEMA_MISMATCH", caught.exception.code)

    def test_catalog_drift_refuses(self):
        # The registry pinned the first catalog; the server now advertises another.
        self.server.catalog = self.server.catalog[:-1]
        with self.assertRaises(gate.BootstrapRefused) as caught:
            self.capture()
        self.assertEqual("BOOTSTRAP_SCHEMA_MISMATCH", caught.exception.code)

    def test_the_snapshot_feeds_s0_07(self):
        decision = gate.evaluate(self.evidence())
        self.assertEqual([], decision["s0"]["failed"])
        self.assertEqual("ALLOW", decision["launch"])

    # -- tamper / freshness ---------------------------------------------------

    def test_a_tampered_serialized_payload_is_rejected(self):
        snapshot = self.capture()
        entry = snapshot["tinyfish"]
        payload = json.loads(entry["snapshot_serialized"])
        payload["tools"][0]["name"] = "tampered"
        entry["snapshot_serialized"] = gate.canonical_snapshot(payload)
        problems = gate.validate_snapshot_entry(
            "tinyfish", entry, hosts=HOSTS, as_of=gate._parse_iso(AS_OF))
        self.assertIn("SNAPSHOT_HASH_MISMATCH", problems)
        decision = gate.evaluate(self.evidence(snapshot=snapshot))
        self.assertIn("S0_07_UPSTREAM_SCHEMA_VERIFIED", decision["s0"]["failed"])
        self.assertEqual("DENY", decision["launch"])

    def test_a_stale_capture_is_rejected(self):
        snapshot = self.capture(captured_at="2026-10-01T11:00:00Z")
        decision = gate.evaluate(self.evidence(snapshot=snapshot))
        self.assertIn("S0_07_UPSTREAM_SCHEMA_VERIFIED", decision["s0"]["failed"])

    def test_a_capture_from_the_future_is_rejected(self):
        snapshot = self.capture(captured_at="2026-10-18T13:00:00Z")
        decision = gate.evaluate(self.evidence(snapshot=snapshot))
        self.assertIn("S0_07_UPSTREAM_SCHEMA_VERIFIED", decision["s0"]["failed"])

    def test_an_untrusted_source_is_rejected(self):
        snapshot = self.capture()
        snapshot["tinyfish"]["source"] = "vendor_blog_says_so"
        decision = gate.evaluate(self.evidence(snapshot=snapshot))
        self.assertIn("S0_07_UPSTREAM_SCHEMA_VERIFIED", decision["s0"]["failed"])

    # -- QA must equal the exact accepted product SHA -------------------------

    def test_qa_passed_at_the_accepted_product_sha_counts(self):
        decision = gate.evaluate(self.evidence())
        self.assertNotIn("S0_02_INDEPENDENT_QA_PASSED", decision["s0"]["failed"])

    def test_qa_passed_at_another_sha_does_not_count(self):
        ev = self.evidence(independent_qa={"status": "PASSED",
                                           "sha": "c" * 40})
        self.assertIn("S0_02_INDEPENDENT_QA_PASSED",
                      gate.evaluate(ev)["s0"]["failed"])

    def test_the_docs_tip_cannot_stand_in_for_the_code_sha(self):
        # Pinning the docs-only tip while acceptance expects the product code.
        ev = self.evidence(integration={"sha": DOCS_TIP_SHA,
                                        "expected_sha": ACCEPTED_PRODUCT_SHA},
                           independent_qa={"status": "PASSED", "sha": DOCS_TIP_SHA})
        decision = gate.evaluate(ev)
        self.assertIn("S0_01_INTEGRATION_SHA_PINNED", decision["s0"]["failed"])
        self.assertEqual("DENY", decision["launch"])

    def test_s0_passing_does_not_authorize_anything_without_b0(self):
        # A valid synthetic snapshot satisfies S0_07; it says nothing about money.
        decision = gate.evaluate(self.evidence(providers={}))
        self.assertTrue(decision["s0"]["passed"])
        self.assertEqual("NOT_CAPTURED", decision["s1"]["verdict"])
        self.assertEqual("DENY", decision["launch"])

    # -- no chargeable action can be expressed --------------------------------

    def test_the_bootstrap_api_has_no_method_or_ledger_parameter(self):
        import inspect
        params = inspect.signature(gate.bootstrap_probe_schema).parameters
        self.assertNotIn("method", params)
        self.assertNotIn("ledger", params)
        self.assertNotIn("reserve", params)


if __name__ == "__main__":
    unittest.main()
