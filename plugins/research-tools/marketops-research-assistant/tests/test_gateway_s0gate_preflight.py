"""Wave-1 S0->S1 readiness gate: offline tests with SYNTHETIC fake accounts.

No network, no credentials, no vendor SDK, no paid call. Every account value in
this file is invented. The gate must refuse to let "nothing was verified" read
as "authorized", and must never emit a reason code outside the frozen §5.3 set.
"""
from __future__ import annotations

import contextlib
import hashlib
import inspect
import io
import json
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import research_s0_preflight_gate as gate  # noqa: E402

SYNTH_SHA = "1" * 40
AS_OF = "2026-10-18T12:00:00Z"
CAPTURED_AT = "2026-10-18T11:00:00Z"


def snapshot_payload(tool_count: int = 28) -> dict:
    """A synthetic tools/list payload. Invented tool names, no vendor data."""
    return {"tools": [{"name": f"synthetic_tool_{i}",
                       "inputSchema": {"type": "object"}}
                      for i in range(tool_count)]}


def snapshots(provider: str = "tinyfish", *, payload: dict | None = None,
              **over) -> dict:
    """A well-formed, hash-matched capture. Synthetic, and safe to be positive."""
    body = snapshot_payload() if payload is None else payload
    serialized = gate.canonical_snapshot(body)
    digest = gate.snapshot_digest(body)
    entry = {
        "version": 1, "provider": provider,
        "endpoint": "https://mcp.synthetic.invalid/mcp",
        "method": "tools/list", "source": "probe_schema@official_mcp",
        "captured_at": CAPTURED_AT,
        "egress_decision_ref": "egress:synthetic:1",
        "audit_ref": "audit:synthetic:intent|audit:synthetic:result",
        "tool_count": len(body["tools"]),
        "snapshot_serialized": serialized,
        "schema_sha256": digest,
        "registry_schema_sha256": digest,
    }
    entry.update(over)
    return {provider: entry}


def s0_pass(**over) -> dict:
    """Structural gates in the all-passed shape. All values synthetic."""
    ev = {
        "integration": {"sha": SYNTH_SHA, "expected_sha": SYNTH_SHA},
        "independent_qa": {"status": "PASSED", "sha": SYNTH_SHA},
        "managed_mcp": {"configured_count": 1, "effective": "marketops-research",
                        "endpoints": ["https://mcp.synthetic.invalid/mcp"]},
        "direct_vendor_bypass": {"present": False},
        "credential_injection": {"runtime_scoped": True, "source": "process_env"},
        "endpoint_allowlist": {"hosts": ["mcp.synthetic.invalid"]},
        "upstream_schema_verified": True,
        "upstream_schema_snapshots": snapshots(),
        "as_of": AS_OF,
    }
    ev.update(over)
    return ev


def account(**over) -> dict:
    """A metered account whose post-operation balance keeps the floor."""
    acct = {"captured": True, "b0": "2.00", "unit": "USD", "meter": "agent_steps",
            "price_source": "synthetic-finish-receipt", "last_observed_at": "2026-10-17T00:00:00Z",
            "reset_at": "2026-11-01T00:00:00Z", "drawdown_so_far": "0",
            "unknown_exposure": "0", "other_holders_spend": "0",
            "next_op_estimate": "0.10", "auto_topup": False,
            "key_rotation_confirmed": True, "schema_verified": True}
    acct.update(over)
    return acct


class FloorArithmeticTests(unittest.TestCase):
    """The three worked examples pinned by BUDGET-FLOOR.md §3."""

    def test_search_credits_example(self):
        floor = gate.floor_for(Decimal("500"), "credits")
        self.assertEqual(Decimal("100"), floor)
        self.assertEqual(Decimal("400"), gate.max_drawdown_for(Decimal("500")))
        self.assertEqual(Decimal("400"), Decimal("500") - floor)

    def test_small_usd_wallet_absolute_floor_binds(self):
        # proportional floor would be $0.10; the explicit $0.20 minimum raises it
        floor = gate.floor_for(Decimal("0.50"), "USD")
        self.assertEqual(Decimal("0.20"), floor)
        self.assertEqual(Decimal("0.30"), Decimal("0.50") - floor)

    def test_larger_usd_wallet_proportional_floor(self):
        floor = gate.floor_for(Decimal("2.00"), "USD")
        self.assertEqual(Decimal("0.40"), floor)
        self.assertEqual(Decimal("1.60"), Decimal("2.00") - floor)

    def test_non_divisible_credit_floor_rounds_up(self):
        # 0.20 x 7 = 1.4 -> ceil 2, so rounding can never satisfy the floor
        self.assertEqual(Decimal("2"), gate.floor_for(Decimal("7"), "credits"))

    def test_floor_and_drawdown_are_decimal_never_float(self):
        self.assertIsInstance(gate.floor_for(Decimal("2"), "USD"), Decimal)
        self.assertIsInstance(gate.max_drawdown_for(Decimal("2")), Decimal)


class StructuralGateTests(unittest.TestCase):

    def test_all_structural_gates_pass_shape(self):
        res = gate.evaluate(s0_pass(providers={"tinyfish": account()}))
        self.assertTrue(res["s0"]["passed"])
        self.assertEqual([], res["s0"]["failed"])
        self.assertEqual("ALLOW", res["launch"])

    def test_qa_still_running_blocks_the_launch(self):
        """Today's actual state: independent QA is RUNNING, so S0 is not passed."""
        ev = s0_pass(providers={"tinyfish": account()})
        ev["independent_qa"] = {"status": "RUNNING", "sha": SYNTH_SHA}
        res = gate.evaluate(ev)
        self.assertFalse(res["s0"]["passed"])
        self.assertIn("S0_02_INDEPENDENT_QA_PASSED", res["s0"]["failed"])
        self.assertEqual("BLOCKED", res["s1"]["verdict"])
        self.assertEqual("CAPABILITY_NOT_AVAILABLE", res["s1"]["reason_code"])
        self.assertEqual("DENY", res["launch"])
        self.assertEqual(2, gate.exit_code(res))

    def test_qa_passed_for_a_different_sha_does_not_count(self):
        ev = s0_pass(independent_qa={"status": "PASSED", "sha": "2" * 40})
        self.assertFalse(gate.evaluate(ev)["s0"]["passed"])

    def test_unpinned_or_short_sha_is_refused(self):
        for bad in ("", "abc", "b2bcdcd", "z" * 40):
            with self.subTest(sha=bad):
                ev = s0_pass(integration={"sha": bad, "expected_sha": bad})
                self.assertFalse(gate.evaluate(ev)["s0"]["passed"])

    def test_two_managed_mcp_servers_is_not_one_effective_mcp(self):
        ev = s0_pass(managed_mcp={"configured_count": 2, "effective": "marketops-research",
                                  "endpoints": ["https://mcp.synthetic.invalid/mcp"]})
        res = gate.evaluate(ev)
        self.assertIn("S0_03_ONE_EFFECTIVE_MANAGED_MCP", res["s0"]["failed"])

    def test_direct_vendor_bypass_blocks(self):
        ev = s0_pass(direct_vendor_bypass={"present": True})
        self.assertIn("S0_04_NO_DIRECT_VENDOR_BYPASS",
                      gate.evaluate(ev)["s0"]["failed"])

    def test_vendor_direct_entry_in_the_manifest_blocks(self):
        ev = s0_pass(managed_mcp={"configured_count": 1, "effective": "marketops-research",
                                  "endpoints": ["https://mcp.synthetic.invalid/mcp"],
                                  "vendor_direct_entries": ["tinyfish-raw"]})
        self.assertIn("S0_04_NO_DIRECT_VENDOR_BYPASS",
                      gate.evaluate(ev)["s0"]["failed"])

    def test_credential_must_be_injected_at_runtime_scope(self):
        ev = s0_pass(credential_injection={"runtime_scoped": False, "source": "repo_file"})
        self.assertIn("S0_05_RUNTIME_SCOPED_CREDENTIAL_INJECTION",
                      gate.evaluate(ev)["s0"]["failed"])

    def test_endpoint_outside_the_allowlist_blocks(self):
        ev = s0_pass(managed_mcp={"configured_count": 1, "effective": "marketops-research",
                                  "endpoints": ["https://raw.vendor.invalid/mcp"]})
        self.assertIn("S0_06_ENDPOINT_ALLOWLIST", gate.evaluate(ev)["s0"]["failed"])

    def test_empty_allowlist_blocks(self):
        ev = s0_pass(endpoint_allowlist={"hosts": []})
        self.assertIn("S0_06_ENDPOINT_ALLOWLIST", gate.evaluate(ev)["s0"]["failed"])

    def test_unverified_upstream_schema_blocks(self):
        ev = s0_pass(upstream_schema_verified=False)
        res = gate.evaluate(ev)
        self.assertIn("S0_07_UPSTREAM_SCHEMA_VERIFIED", res["s0"]["failed"])

    def test_a_bare_verified_flag_without_a_snapshot_blocks(self):
        ev = s0_pass()
        ev.pop("upstream_schema_snapshots")
        self.assertIn("S0_07_UPSTREAM_SCHEMA_VERIFIED",
                      gate.evaluate(ev)["s0"]["failed"])

    def test_an_empty_snapshot_mapping_blocks(self):
        # "nothing was fetched" must not read as "verified".
        self.assertIn("S0_07_UPSTREAM_SCHEMA_VERIFIED",
                      gate.evaluate(s0_pass(upstream_schema_snapshots={}))["s0"]["failed"])

    def test_a_mismatched_registry_hash_blocks(self):
        bad = snapshots(registry_schema_sha256="b" * 64)
        self.assertIn("S0_07_UPSTREAM_SCHEMA_VERIFIED",
                      gate.evaluate(s0_pass(upstream_schema_snapshots=bad))["s0"]["failed"])

    def test_a_malformed_snapshot_blocks(self):
        for over in ({"captured_at": ""}, {"source": ""}, {"tool_count": 0},
                     {"schema_sha256": "abc"}, {"tool_count": True},
                     {"method": "tools/call"}, {"version": 2}, {"version": True}):
            with self.subTest(**over):
                self.assertIn(
                    "S0_07_UPSTREAM_SCHEMA_VERIFIED",
                    gate.evaluate(s0_pass(upstream_schema_snapshots=snapshots(**over)))["s0"]["failed"])

    def test_a_stale_capture_blocks(self):
        # 8 days old: the upstream surface may have moved since the registry pin.
        stale = snapshots(captured_at="2026-10-10T11:00:00Z")
        self.assertIn("S0_07_UPSTREAM_SCHEMA_VERIFIED",
                      gate.evaluate(s0_pass(upstream_schema_snapshots=stale))["s0"]["failed"])

    def test_a_capture_from_the_future_blocks(self):
        future = snapshots(captured_at="2026-10-18T13:00:00Z")
        self.assertIn("S0_07_UPSTREAM_SCHEMA_VERIFIED",
                      gate.evaluate(s0_pass(upstream_schema_snapshots=future))["s0"]["failed"])

    def test_a_missing_or_invalid_as_of_blocks(self):
        for over in ({"as_of": ""}, {"as_of": "not-a-time"}, {"as_of": None}):
            with self.subTest(**over):
                ev = s0_pass(**over)
                self.assertIn("S0_07_UPSTREAM_SCHEMA_VERIFIED",
                              gate.evaluate(ev)["s0"]["failed"])

    def test_an_untrusted_source_blocks(self):
        bad = snapshots(source="vendor_blog_says_so")
        self.assertIn("S0_07_UPSTREAM_SCHEMA_VERIFIED",
                      gate.evaluate(s0_pass(upstream_schema_snapshots=bad))["s0"]["failed"])

    def test_an_endpoint_outside_the_allowlist_blocks(self):
        bad = snapshots(endpoint="https://raw.vendor.invalid/mcp")
        self.assertIn("S0_07_UPSTREAM_SCHEMA_VERIFIED",
                      gate.evaluate(s0_pass(upstream_schema_snapshots=bad))["s0"]["failed"])

    def test_a_provider_key_mismatch_blocks(self):
        entry = snapshots("tinyfish")["tinyfish"]
        bad = {"serpapi": entry}
        self.assertIn("S0_07_UPSTREAM_SCHEMA_VERIFIED",
                      gate.evaluate(s0_pass(upstream_schema_snapshots=bad))["s0"]["failed"])

    def test_missing_egress_or_audit_refs_block(self):
        for over in ({"egress_decision_ref": ""}, {"audit_ref": ""},
                     {"snapshot_serialized": ""}):
            with self.subTest(**over):
                self.assertIn(
                    "S0_07_UPSTREAM_SCHEMA_VERIFIED",
                    gate.evaluate(s0_pass(
                        upstream_schema_snapshots=snapshots(**over)))["s0"]["failed"])

    def test_a_serialized_payload_that_does_not_match_its_digest_blocks(self):
        body = snapshot_payload()
        tampered = gate.canonical_snapshot({"tools": body["tools"][:-1]})
        bad = snapshots(payload=body)
        bad["tinyfish"]["snapshot_serialized"] = tampered   # digest no longer matches
        self.assertIn("S0_07_UPSTREAM_SCHEMA_VERIFIED",
                      gate.evaluate(s0_pass(upstream_schema_snapshots=bad))["s0"]["failed"])

    def test_a_non_canonical_serialization_blocks(self):
        body = snapshot_payload()
        non_canonical = json.dumps(body, indent=2, sort_keys=True)
        digest = hashlib.sha256(non_canonical.encode("utf-8")).hexdigest()
        bad = snapshots(payload=body)
        bad["tinyfish"].update({"snapshot_serialized": non_canonical,
                                "schema_sha256": digest,
                                "registry_schema_sha256": digest})
        problems = gate.validate_snapshot_entry(
            "tinyfish", bad["tinyfish"],
            hosts={"mcp.synthetic.invalid"},
            as_of=gate._parse_iso(AS_OF))
        self.assertIn("SNAPSHOT_NOT_CANONICAL", problems)

    def test_a_declared_tool_count_that_disagrees_with_the_payload_blocks(self):
        bad = snapshots(tool_count=29)
        problems = gate.validate_snapshot_entry(
            "tinyfish", bad["tinyfish"],
            hosts={"mcp.synthetic.invalid"},
            as_of=gate._parse_iso(AS_OF))
        self.assertIn("SNAPSHOT_TOOL_COUNT_MISMATCH", problems)

    def test_validate_reports_precise_codes(self):
        problems = gate.validate_snapshot_entry(
            "tinyfish", {"version": 1, "provider": "tinyfish"},
            hosts={"mcp.synthetic.invalid"},
            as_of=gate._parse_iso(AS_OF))
        for code in ("SNAPSHOT_ENDPOINT_MISSING", "SNAPSHOT_METHOD_NOT_TOOLS_LIST",
                     "SNAPSHOT_SOURCE_UNTRUSTED", "SNAPSHOT_EGRESS_REF_MISSING",
                     "SNAPSHOT_AUDIT_REF_MISSING", "SNAPSHOT_SERIALIZED_MISSING",
                     "SNAPSHOT_TOOL_COUNT_INVALID"):
            self.assertIn(code, problems)

    def test_a_well_formed_snapshot_passes(self):
        self.assertNotIn("S0_07_UPSTREAM_SCHEMA_VERIFIED",
                         gate.evaluate(s0_pass())["s0"]["failed"])

    def test_the_synthetic_positive_is_scoped_to_s0_07_only(self):
        """A valid snapshot satisfies S0_07 and nothing else."""
        ev = s0_pass(providers={"tinyfish": account()})
        ev["direct_vendor_bypass"] = {"present": True}
        res = gate.evaluate(ev)
        self.assertNotIn("S0_07_UPSTREAM_SCHEMA_VERIFIED", res["s0"]["failed"])
        self.assertIn("S0_04_NO_DIRECT_VENDOR_BYPASS", res["s0"]["failed"])
        self.assertEqual("DENY", res["launch"])

    def test_network_disabled_is_refused_before_anything_else(self):
        ev = s0_pass(providers={"tinyfish": account()}, network_enabled="disabled")
        res = gate.evaluate(ev)
        self.assertEqual("BLOCKED", res["s1"]["verdict"])
        self.assertEqual("RESEARCH_NETWORK_DISABLED", res["s1"]["reason_code"])
        self.assertEqual("DENY", res["launch"])


class AccountVerdictTests(unittest.TestCase):

    def provider(self, **over):
        return gate.evaluate(s0_pass(providers={"tinyfish": account(**over)}))["s1"]["providers"]["tinyfish"]

    def test_healthy_metered_account_authorizes_and_keeps_the_floor(self):
        row = self.provider()
        self.assertEqual("AUTHORIZED", row["verdict"])
        self.assertEqual("0.40", row["floor"])
        self.assertEqual("1.90", row["post_operation_available"])

    def test_absolute_usd_floor_binds_on_a_small_wallet(self):
        ok = self.provider(b0="0.50", next_op_estimate="0.29")
        self.assertEqual("AUTHORIZED", ok["verdict"])
        self.assertEqual("0.21", ok["post_operation_available"])
        bad = self.provider(b0="0.50", next_op_estimate="0.31")
        self.assertEqual("BLOCKED", bad["verdict"])
        self.assertEqual("WALLET_PRECONDITION_FAILED", bad["reason_code"])

    def test_not_captured_when_b0_was_never_read_from_the_vendor(self):
        row = self.provider(captured=False)
        self.assertEqual("NOT_CAPTURED", row["verdict"])
        self.assertEqual("WALLET_PRECONDITION_FAILED", row["reason_code"])

    def test_missing_pricing_source_is_not_captured(self):
        row = self.provider(price_source="")
        self.assertEqual("NOT_CAPTURED", row["verdict"])
        self.assertEqual("PRICING_SOURCE_MISSING", row["reason_code"])

    def test_unknown_operation_cost_fails_closed(self):
        for missing in (None, "", "NaN", "-1"):
            with self.subTest(estimate=missing):
                row = self.provider(next_op_estimate=missing)
                self.assertIn(row["verdict"], {"NOT_CAPTURED", "BLOCKED"})
                self.assertEqual("PRICE_UNKNOWN_FAIL_CLOSED", row["reason_code"])

    def test_unstated_unknown_exposure_is_not_read_as_zero(self):
        row = self.provider(unknown_exposure=None)
        self.assertEqual("BLOCKED", row["verdict"])
        self.assertEqual("UNKNOWN_CHARGE_OUTSTANDING", row["reason_code"])

    def test_auto_topup_must_stay_disabled(self):
        row = self.provider(auto_topup=True)
        self.assertEqual("BLOCKED", row["verdict"])
        self.assertEqual("WALLET_PRECONDITION_FAILED", row["reason_code"])

    def test_unconfirmed_key_rotation_is_not_captured(self):
        """TinyFish's live blocker today: the key must be rotated/revoked first."""
        row = self.provider(key_rotation_confirmed=False)
        self.assertEqual("NOT_CAPTURED", row["verdict"])
        self.assertEqual("WALLET_PRECONDITION_FAILED", row["reason_code"])

    def test_unverified_schema_blocks_the_account_path(self):
        row = self.provider(schema_verified=False)
        self.assertEqual("BLOCKED", row["verdict"])
        self.assertEqual("CAPABILITY_SCHEMA_DRIFT", row["reason_code"])

    def test_non_positive_b0_blocks(self):
        for bad in ("0", "-3.00"):
            with self.subTest(b0=bad):
                self.assertEqual("BLOCKED", self.provider(b0=bad)["verdict"])

    def test_nan_and_infinite_amounts_are_refused(self):
        for bad in ("NaN", "Infinity", "-Infinity", "not-a-number"):
            with self.subTest(b0=bad):
                row = self.provider(b0=bad)
                self.assertEqual("BLOCKED", row["verdict"])
                self.assertEqual("PRICE_UNKNOWN_FAIL_CLOSED", row["reason_code"])

    def test_concurrent_holder_spend_pushes_the_balance_below_the_floor(self):
        """Cross-session concurrency: another session's live reservation counts."""
        row = self.provider(b0="0.50", next_op_estimate="0.10", other_holders_spend="0.25")
        self.assertEqual("BLOCKED", row["verdict"])
        self.assertEqual("WALLET_PRECONDITION_FAILED", row["reason_code"])

    def test_unreconciled_exposure_counts_toward_the_ceiling(self):
        row = self.provider(b0="2.00", unknown_exposure="1.75", next_op_estimate="0.10")
        self.assertEqual("BLOCKED", row["verdict"])
        self.assertIn(row["reason_code"],
                      {"WALLET_PRECONDITION_FAILED", "BUDGET_PROVIDER_CEILING"})

    def test_exceeding_the_point_eight_upper_bound_blocks(self):
        row = self.provider(b0="2.00", drawdown_so_far="1.55", next_op_estimate="0.10")
        self.assertEqual("BLOCKED", row["verdict"])
        self.assertEqual("BUDGET_PROVIDER_CEILING", row["reason_code"])

    def test_local_compute_skips_the_account_gate_but_still_needs_s0(self):
        local = account(billing_mode="local_no_provider_account", captured=False, b0=None,
                        price_source="", next_op_estimate=None)
        row = gate.evaluate(s0_pass(providers={"crawl4ai_local": local}))["s1"]["providers"]["crawl4ai_local"]
        self.assertEqual("AUTHORIZED", row["verdict"])
        blocked = gate.evaluate(s0_pass(upstream_schema_verified=False,
                                        providers={"crawl4ai_local": local}))
        self.assertEqual("DENY", blocked["launch"])

    def test_unknown_billing_mode_blocks(self):
        row = self.provider(billing_mode="mystery")
        self.assertEqual("BLOCKED", row["verdict"])
        self.assertEqual("CAPABILITY_NOT_AVAILABLE", row["reason_code"])


class AggregationTests(unittest.TestCase):

    def test_worst_provider_decides_and_priority_is_blocked_first(self):
        res = gate.evaluate(s0_pass(providers={
            "tinyfish": account(),
            "serpapi": account(b0="5.00"),
            "serper": account(auto_topup=True),
        }))
        self.assertEqual("BLOCKED", res["s1"]["verdict"])
        self.assertEqual("DENY", res["launch"])

    def test_not_captured_beats_authorized(self):
        res = gate.evaluate(s0_pass(providers={
            "tinyfish": account(),
            "jev": account(captured=False),
        }))
        self.assertEqual("NOT_CAPTURED", res["s1"]["verdict"])
        self.assertEqual("DENY", res["launch"])

    def test_empty_evidence_is_never_authorized(self):
        """Regression: a first cut read an empty ledger as AUTHORIZED and exited 0."""
        for ev in ({}, s0_pass(), s0_pass(providers={})):
            with self.subTest(evidence=sorted(ev)):
                res = gate.evaluate(ev)
                self.assertNotEqual("AUTHORIZED", res["s1"]["verdict"])
                self.assertEqual("DENY", res["launch"])
                self.assertEqual(2, gate.exit_code(res))

    def test_reason_code_is_always_in_the_frozen_closed_set(self):
        samples = [
            s0_pass(providers={"a": account(), "b": account(auto_topup=True)}),
            s0_pass(providers={"a": account(price_source="")}),
            s0_pass(providers={"a": account(unknown_exposure=None)}),
            s0_pass(independent_qa={"status": "RUNNING", "sha": SYNTH_SHA}),
            s0_pass(providers={"a": account()}, network_enabled="disabled"),
        ]
        for ev in samples:
            res = gate.evaluate(ev)
            code = res["s1"]["reason_code"]
            self.assertIn(code, gate.REASON_CODES)
            for row in res["s1"]["providers"].values():
                if row.get("reason_code"):
                    self.assertIn(row["reason_code"], gate.REASON_CODES)

    def test_s0_gate_ids_are_not_claimed_to_be_reason_codes(self):
        self.assertFalse(set(gate.S0_GATES) & set(gate.REASON_CODES))


class CredentialHygieneTests(unittest.TestCase):

    def test_secret_bearing_field_names_are_refused_without_echoing_values(self):
        planted = "PLANTED-SENTINEL-VALUE"
        ev = s0_pass(providers={"tinyfish": account()})
        ev["provider_credentials"] = {"tinyfish_api_key": planted}
        res = gate.evaluate(ev)
        self.assertEqual("DENY", res["launch"])
        self.assertEqual("BLOCKED", res["s1"]["verdict"])
        self.assertEqual("GRANT_SIGNATURE_INVALID", res["s1"]["reason_code"])
        blob = json.dumps(res, ensure_ascii=False)
        self.assertNotIn(planted, blob)
        self.assertIn("provider_credentials.tinyfish_api_key", blob)

    def test_nested_secret_material_is_refused(self):
        ev = s0_pass(providers={"tinyfish": account(token="x")})
        self.assertEqual("DENY", gate.evaluate(ev)["launch"])

    def test_presence_booleans_and_sourced_measurements_are_admissible(self):
        ev = s0_pass(providers={"tinyfish": account(key_present=True)})
        self.assertEqual("ALLOW", gate.evaluate(ev)["launch"])


class ZeroCostDisciplineTests(unittest.TestCase):
    """A zero estimate is only free when the price source says so."""

    def provider(self, **over):
        return gate.evaluate(s0_pass(providers={"tinyfish": account(**over)}))["s1"]["providers"]["tinyfish"]

    def test_zero_estimate_without_a_zero_attesting_source_is_not_free(self):
        # An absent source is refused one step earlier as PRICING_SOURCE_MISSING;
        # a present-but-non-attesting source is refused as an unknown cost.
        for src, want in (("", "PRICING_SOURCE_MISSING"),
                          ("doc-page", "PRICE_UNKNOWN_FAIL_CLOSED"),
                          ("synthetic-finish-receipt", "PRICE_UNKNOWN_FAIL_CLOSED")):
            with self.subTest(price_source=src):
                row = self.provider(next_op_estimate="0", price_source=src)
                self.assertEqual("NOT_CAPTURED", row["verdict"])
                self.assertEqual(want, row["reason_code"])

    def test_zero_estimate_with_a_zero_attesting_source_is_allowed(self):
        row = self.provider(next_op_estimate="0", price_source="meter_zero_verified")
        self.assertEqual("AUTHORIZED", row["verdict"])


class _RecordingGateway:
    """Documented-interface double: records and delegates nothing else."""

    def __init__(self, status="OK", reason_code=""):
        self.calls = []
        self._status, self._code = status, reason_code

    def invoke(self, **kwargs):
        self.calls.append(kwargs)

        class _Outcome:
            status = self._status
            reason_code = self._code
        return _Outcome()


def invoke_kwargs():
    return dict(capability_id="tinyfish.search", params={"query": "synthetic"},
                run_id="run-1", operation_id="op-1", account_ref="acct-1",
                intent_fingerprint="fp-1")


class GuardedInvokeTests(unittest.TestCase):
    """The seam: the gateway must not be reached unless the gate says ALLOW."""

    def test_rejected_s0_never_reaches_the_gateway(self):
        gw = _RecordingGateway()
        ev = s0_pass(providers={"tinyfish": account()})
        ev["independent_qa"] = {"status": "RUNNING", "sha": SYNTH_SHA}
        out = gate.guarded_invoke(gw, ev, **invoke_kwargs())
        self.assertFalse(out.gateway_invoked)
        self.assertEqual([], gw.calls)
        self.assertEqual("BLOCKED", out.status)
        self.assertEqual("CAPABILITY_NOT_AVAILABLE", out.reason_code)
        self.assertIn("S0_02_INDEPENDENT_QA_PASSED", out.s0_failed)

    def test_missing_b0_never_reaches_the_gateway(self):
        for over in ({"captured": False}, {"unknown_exposure": None},
                     {"price_source": ""}, {"key_rotation_confirmed": False}):
            with self.subTest(**over):
                gw = _RecordingGateway()
                out = gate.guarded_invoke(gw, s0_pass(providers={"tinyfish": account(**over)}),
                                          **invoke_kwargs())
                self.assertFalse(out.gateway_invoked)
                self.assertEqual([], gw.calls)
                self.assertNotEqual("OK", out.status)

    def test_allowed_gate_reaches_the_gateway_exactly_once(self):
        gw = _RecordingGateway()
        out = gate.guarded_invoke(gw, s0_pass(providers={"tinyfish": account()}),
                                  **invoke_kwargs())
        self.assertTrue(out.gateway_invoked)
        self.assertEqual(1, len(gw.calls))
        self.assertEqual("tinyfish.search", gw.calls[0]["capability_id"])

    def test_negative_control_the_recorder_can_see_a_dispatch(self):
        """Without this, the two 'never reached' tests could pass vacuously."""
        gw = _RecordingGateway()
        gw.invoke(**invoke_kwargs())
        self.assertEqual(1, len(gw.calls))


class AccountEvidenceSchemaTests(unittest.TestCase):
    """Metering/price evidence has a shape, and identity is not part of it."""

    def test_a_well_formed_metered_account_has_no_problems(self):
        self.assertEqual([], gate.validate_account_evidence(account()))

    def test_identity_shaped_fields_are_refused_by_name_only(self):
        for field in ("account_id", "wallet_id", "email", "customer_id"):
            with self.subTest(field=field):
                problems = gate.validate_account_evidence({**account(), field: "SENTINEL"})
                self.assertIn(f"IDENTITY_FIELD_FORBIDDEN:{field}", problems)
                self.assertNotIn("SENTINEL", " ".join(problems))

    def test_an_unrecognised_field_is_a_schema_problem_not_an_ignored_key(self):
        # `balance_usd` is how a real balance would arrive under a name the gate
        # does not understand; ignoring it would silently drop the measurement.
        problems = gate.validate_account_evidence({**account(), "balance_usd": "9.99"})
        self.assertIn("UNKNOWN_FIELD:balance_usd", problems)

    def test_metering_fields_are_required_for_a_metered_account(self):
        for field in gate.REQUIRED_METERING_FIELDS:
            with self.subTest(missing=field):
                payload = dict(account())
                payload.pop(field)
                self.assertIn(f"MISSING_METERING_FIELD:{field}",
                              gate.validate_account_evidence(payload))

    def test_local_compute_needs_no_metering_fields(self):
        payload = {"billing_mode": "local_no_provider_account", "captured": False}
        self.assertEqual([], gate.validate_account_evidence(payload))


class CliTests(unittest.TestCase):

    def test_cli_exit_code_is_zero_only_when_authorized(self):
        with tempfile.TemporaryDirectory() as tmp:
            allow = Path(tmp) / "allow.json"
            allow.write_text(json.dumps(s0_pass(providers={"tinyfish": account()})),
                             encoding="utf-8")
            deny = Path(tmp) / "deny.json"
            deny.write_text(json.dumps(s0_pass(independent_qa={"status": "RUNNING",
                                                               "sha": SYNTH_SHA})),
                            encoding="utf-8")
            sink = io.StringIO()
            with contextlib.redirect_stdout(sink):
                self.assertEqual(0, gate.main(["--evidence", str(allow)]))
                self.assertEqual(2, gate.main(["--evidence", str(deny)]))
            self.assertIn('"launch": "ALLOW"', sink.getvalue())


class BootstrapExceptionTests(unittest.TestCase):
    """The pre-S0 discovery path: read-only, audited, and unable to buy."""

    ENDPOINT = "https://mcp.synthetic.invalid/mcp"
    HOSTS = {"mcp.synthetic.invalid"}
    CLOCK = staticmethod(
        lambda: datetime(2026, 10, 18, 11, 0, tzinfo=timezone.utc))  # noqa: E731

    class Probe:
        def __init__(self, payload):
            self.payload = payload
            self.calls = 0

        def __call__(self):
            self.calls += 1
            return self.payload

    class Audit:
        def __init__(self, ack="audit:synthetic:1"):
            self.events = []
            self.ack = ack

        def __call__(self, event):
            self.events.append(dict(event))
            return self.ack

    class Egress:
        def __init__(self, ref="egress:synthetic:1"):
            self.endpoints = []
            self.ref = ref

        def __call__(self, endpoint):
            self.endpoints.append(endpoint)
            return self.ref

    def capture(self, *, payload=None, **over):
        body = snapshot_payload() if payload is None else payload
        probe = self.Probe(body)
        audit = self.Audit()
        egress = self.Egress()
        kwargs = dict(probe=probe, provider="tinyfish", endpoint=self.ENDPOINT,
                      allowlist=self.HOSTS, egress_decision=egress, audit=audit,
                      registry_schema_sha256=gate.snapshot_digest(body),
                      clock=self.CLOCK)
        kwargs.update(over)
        return gate.bootstrap_probe_schema(**kwargs), probe, audit, egress

    def test_the_method_allowlist_cannot_express_a_tool_call(self):
        for method in ("initialize", "notifications/initialized", "tools/list"):
            self.assertTrue(gate.bootstrap_method_allowed(method), method)
        for method in ("tools/call", "resources/read", "prompts/get", ""):
            self.assertFalse(gate.bootstrap_method_allowed(method), method)

    def test_the_capture_api_has_no_method_and_no_ledger(self):
        params = inspect.signature(gate.bootstrap_probe_schema).parameters
        self.assertNotIn("method", params)
        self.assertNotIn("ledger", params)
        self.assertNotIn("reserve", params)

    def test_a_tools_list_capture_feeds_s0_07(self):
        snapshot, probe, audit, egress = self.capture()
        self.assertEqual(1, probe.calls)
        self.assertEqual([self.ENDPOINT], egress.endpoints)
        self.assertEqual(2, len(audit.events))          # intent, result
        ev = s0_pass(upstream_schema_snapshots=snapshot, as_of=CAPTURED_AT)
        self.assertNotIn("S0_07_UPSTREAM_SCHEMA_VERIFIED",
                         gate.evaluate(ev)["s0"]["failed"])

    def test_a_registry_hash_mismatch_returns_no_snapshot(self):
        with self.assertRaises(gate.BootstrapRefused) as caught:
            self.capture(registry_schema_sha256="b" * 64)
        self.assertEqual("BOOTSTRAP_SCHEMA_MISMATCH", caught.exception.code)

    def test_an_endpoint_outside_the_allowlist_never_probes(self):
        probe = self.Probe(snapshot_payload())
        with self.assertRaises(gate.BootstrapRefused) as caught:
            gate.bootstrap_probe_schema(
                probe=probe, provider="tinyfish",
                endpoint="https://raw.vendor.invalid/mcp",
                allowlist=self.HOSTS, egress_decision=self.Egress(),
                audit=self.Audit(),
                registry_schema_sha256=gate.snapshot_digest(snapshot_payload()),
                clock=self.CLOCK)
        self.assertEqual("BOOTSTRAP_ENDPOINT_NOT_ALLOWED", caught.exception.code)
        self.assertEqual(0, probe.calls)

    def test_auth_required_is_not_a_snapshot(self):
        with self.assertRaises(gate.BootstrapRefused) as caught:
            self.capture(payload={"status": "AUTH_REQUIRED"})
        self.assertEqual("BOOTSTRAP_NO_SNAPSHOT", caught.exception.code)

    def test_an_unacknowledged_audit_never_probes(self):
        probe = self.Probe(snapshot_payload())
        with self.assertRaises(gate.BootstrapRefused) as caught:
            gate.bootstrap_probe_schema(
                probe=probe, provider="tinyfish", endpoint=self.ENDPOINT,
                allowlist=self.HOSTS, egress_decision=self.Egress(),
                audit=self.Audit(ack=""),
                registry_schema_sha256=gate.snapshot_digest(snapshot_payload()),
                clock=self.CLOCK)
        self.assertEqual("BOOTSTRAP_AUDIT_UNACKNOWLEDGED", caught.exception.code)
        self.assertEqual(0, probe.calls)

    def test_an_unacknowledged_egress_decision_never_probes(self):
        probe = self.Probe(snapshot_payload())
        with self.assertRaises(gate.BootstrapRefused) as caught:
            gate.bootstrap_probe_schema(
                probe=probe, provider="tinyfish", endpoint=self.ENDPOINT,
                allowlist=self.HOSTS, egress_decision=self.Egress(ref=""),
                audit=self.Audit(),
                registry_schema_sha256=gate.snapshot_digest(snapshot_payload()),
                clock=self.CLOCK)
        self.assertEqual("BOOTSTRAP_EGRESS_DECISION_UNACKNOWLEDGED",
                         caught.exception.code)
        self.assertEqual(0, probe.calls)

    def test_an_empty_tools_list_is_not_a_snapshot(self):
        with self.assertRaises(gate.BootstrapRefused) as caught:
            self.capture(payload={"tools": []})
        self.assertEqual("BOOTSTRAP_EMPTY_TOOLS", caught.exception.code)

    def test_the_audit_record_carries_no_payload_or_headers(self):
        _, _, audit, _ = self.capture()
        allowed = {"event", "phase", "provider", "endpoint", "host", "method",
                   "version", "egress_decision_ref", "tool_count", "schema_sha256"}
        for event in audit.events:
            self.assertTrue(set(event).issubset(allowed), set(event) - allowed)
            self.assertNotIn("headers", json.dumps(event).lower())
            self.assertNotIn("authorization", json.dumps(event).lower())

    def test_the_returned_snapshot_carries_no_reservation(self):
        snapshot, _, _, _ = self.capture()
        entry = snapshot["tinyfish"]
        for forbidden in ("operation_id", "reserved_micros", "charge_state",
                          "ledger_id", "reservation"):
            self.assertNotIn(forbidden, entry)


class InterfaceContractTests(unittest.TestCase):
    """One gate, one public interface — pinned so two branches cannot diverge.

    The module is carried byte-identically on the preflight branch and on the
    integration branch. This class states the interface those two copies must
    both keep: if one is edited, its copy of this test fails rather than the two
    silently expressing different opinions about the same decision.
    """

    PUBLIC = ("evaluate", "guarded_invoke", "exit_code", "floor_for",
              "max_drawdown_for", "money_str", "validate_account_evidence",
              "canonical_snapshot", "snapshot_digest", "validate_snapshot_entry",
              "bootstrap_method_allowed", "bootstrap_probe_schema")

    def test_the_public_callables_exist_and_are_callable(self):
        for name in self.PUBLIC:
            with self.subTest(name=name):
                self.assertTrue(callable(getattr(gate, name)))

    def test_evaluate_takes_exactly_one_injected_evidence_mapping(self):
        params = list(inspect.signature(gate.evaluate).parameters.values())
        self.assertEqual(["evidence"], [p.name for p in params])
        self.assertEqual(inspect.Parameter.POSITIONAL_OR_KEYWORD, params[0].kind)

    def test_guarded_invoke_keeps_the_gateway_as_its_first_argument(self):
        params = list(inspect.signature(gate.guarded_invoke).parameters.values())
        self.assertEqual("gateway", params[0].name)
        self.assertEqual("evidence", params[1].name)
        keyword_only = {p.name for p in params[2:]}
        self.assertEqual({"capability_id", "params", "run_id", "operation_id",
                          "account_ref", "intent_fingerprint", "grant_ref",
                          "agent_claims"}, keyword_only)

    def test_guarded_outcome_reports_whether_the_gateway_was_touched(self):
        fields = set(gate.GuardedOutcome.__dataclass_fields__)
        self.assertTrue({"status", "reason_code", "capability_id",
                         "gateway_invoked", "s0_failed"}.issubset(fields))

    def test_the_two_closed_sets_do_not_overlap(self):
        # An S0 gate id is not a §5.3 reason code; promoting one needs §8.
        self.assertEqual(set(), set(gate.S0_GATES) & set(gate.REASON_CODES))
        self.assertEqual(23, len(gate.REASON_CODES))

    def test_the_s1_verdict_set_is_the_authoritative_one(self):
        self.assertEqual(("AUTHORIZED", "BLOCKED", "NOT_CAPTURED"),
                         tuple(gate.VERDICTS))

    def test_the_gate_does_not_import_the_gateway_module(self):
        # Duck-typing is what keeps this module from becoming a second opinion
        # about what a gateway is. The import belongs in the composition root.
        source = inspect.getsource(gate)
        self.assertNotIn("import research_gateway", source)
        self.assertNotIn("from research_gateway", source)


if __name__ == "__main__":
    unittest.main()
