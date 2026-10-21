"""TinyFish adapter conformance to the Wave-1 frozen interface contract.

The contract is `docs/wave1-coordination/INTERFACE-CONTRACT.md` on branch
`docs/wave1-coordination-20261017` (v1.0, FROZEN), at the same base commit as
this branch. §2 fixes the shared types, §3.4 the `AdapterResult` shape, §6 the
`ProviderAdapter` Protocol and the `IA-*` invariants, §10 the QA table.

This file is the conformance spec, so it deliberately spells the contract's
enum *values* out as literals. Those literals are the contract, not a second
copy of its types: per §2 the types themselves must be imported from
`research_contract.py`, which the Contract/Registry lane (W-B) owns and which
does not exist at this base commit. If W-B lands a different spelling, this
file goes red — which is the intended way to notice.
"""
from __future__ import annotations

import os
import sys
import unittest
from dataclasses import dataclass
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import research_adapter_tinyfish as tinyfish
import _tinyfish_egress_fixture as egress_fixture

# --- contract §2, verbatim value sets -------------------------------------
CONTRACT_TIERS = {"T0", "T1", "T2", "T3", "T4"}
CONTRACT_EFFECTS = {"READ_ONLY", "BILLABLE_READ", "BILLABLE_ACT", "STATE_CHANGING"}
CONTRACT_CHARGE_STATES = {"RESERVED", "SETTLED", "NO_CHARGE_PROVEN", "UNKNOWN",
                          "FREE_TIER"}
CONTRACT_ACTUAL_SOURCES = {"PROVIDER_REPORTED", "DERIVED", "NONE"}
CONTRACT_OUTCOMES = {"OK", "BLOCKED", "FAILED", "UNKNOWN"}
CONTRACT_AVAILABILITY = {"ENABLED", "DISABLED", "UNSUPPORTED", "AUTH_REQUIRED",
                         "SCHEMA_DRIFT"}
CONTRACT_SCHEMA_VERDICTS = {"MATCH", "DRIFT", "ABSENT", "UNVERIFIED"}
# --- contract §5.3, the closed reason-code set, transcribed verbatim ---------
CONTRACT_REASON_CODES = frozenset({
    "POLICY_TIER_NOT_GRANTED", "POLICY_DOMAIN_NOT_ALLOWED", "POLICY_GRANT_EXPIRED",
    "POLICY_GRANT_REVOKED", "BUDGET_RUN_CEILING", "BUDGET_PROVIDER_CEILING",
    "BUDGET_PROJECT_CEILING", "BUDGET_OPERATION_CEILING", "PRICE_UNKNOWN_FAIL_CLOSED",
    "PRICING_SOURCE_MISSING", "DUPLICATE_OPERATION", "OPERATION_ID_COLLISION",
    "HOST_PREVIOUSLY_BLOCKED", "CAPABILITY_UNKNOWN", "CAPABILITY_PARAM_NOT_ALLOWED",
    "CAPABILITY_SCHEMA_DRIFT", "CAPABILITY_NOT_AVAILABLE", "EGRESS_REFUSED",
    "CRAWL4AI_LOCAL_NOT_INSTALLED", "UNKNOWN_CHARGE_OUTSTANDING",
    "RESEARCH_NETWORK_DISABLED", "WALLET_PRECONDITION_FAILED", "GRANT_SIGNATURE_INVALID",
})
# --- contract §6, the fixed transport per provider -------------------------
CONTRACT_TRANSPORT = "official_mcp"
# --- contract §3.4, the AdapterResult fields ---------------------------------
ADAPTER_RESULT_FIELDS = {
    "outcome", "payload", "artifacts", "upstream_ref", "actual_usd",
    "actual_source", "quota_units_used", "transport", "schema_verdict", "caveats",
}
# Extra keys this adapter adds on top of §3.4. Declared here so that adding an
# eleventh one is a deliberate, reviewed act rather than silent drift.
ADAPTER_RESULT_EXTRAS = {
    "capability_id",     # which Registry entry produced this result
    "reason_code",       # §5.3 closed set; None when outcome is OK
    "charge_state",      # §2 ChargeState; §3.4 has no field for it
    "retry_permitted",   # always False after dispatch; §3.4 has no field for it
    "business_evidence_verified",  # always False; owner brief §7
    "dispatched",        # whether the call may have left this process
}

CATALOG = sorted(tinyfish.capability_catalog()["tools"])
WALLET = {"available_balance": "25.00", "currency": "USD",
          "auto_reload": {"enabled": False},
          "rates": {"meters": [{"label": "TinyFish Agent",
                                "unit_amount": "0.016"}]}}


@dataclass(frozen=True)
class Request:
    """Duck-typed `CapabilityRequest` — W-B owns the real type (§2)."""
    capability_id: str
    params: dict
    run_id: str = "run-1"
    operation_id: str = "op-1"
    account_ref: str = "account-1"
    intent_fingerprint: str = "intent-1"
    grant_ref: str | None = None


class AdapterSurface(unittest.TestCase):
    def setUp(self):
        self.calls = []
        egress_fixture.install(self)
        self.dns = egress_fixture.no_real_dns(self)
        env = patch.dict(os.environ, {
            "MARKETOPS_FETCH_ALLOWED_DOMAINS": "public.example.org",
            "MARKETOPS_RESEARCH_ALLOW_NETWORK": "0",
            "TINYFISH_API_KEY": "synthetic-tinyfish-key-not-for-network",
            "TINYFISH_MCP_ACCESS_TOKEN": "",
            "MARKETOPS_TINYFISH_AGENT_ALLOW_PAID": "0",
        })
        env.start()
        self.addCleanup(env.stop)

    def runner(self, *, payloads=None, raises=None, raises_on=None):
        """`raises_on` scopes a failure to one tool.

        A failed wallet preflight and a failed dispatch are different events —
        one provably spent nothing, the other may have — so a test that means
        "the dispatch timed out" must not also break the free read in front of
        it.
        """
        async def run(tool, arguments):
            self.calls.append((tool, arguments))
            if raises is not None and (raises_on is None or raises_on == tool):
                raise raises
            return (payloads or {}).get(tool, {"results": []})
        return run

    # -- identity and capability ids (Registry cross-consistency, §4/§6) ---
    def test_provider_and_transport_match_the_frozen_table(self):
        self.assertEqual("tinyfish", tinyfish.PROVIDER)
        self.assertEqual(CONTRACT_TRANSPORT, tinyfish.TRANSPORT)

    def test_capabilities_uses_the_contracted_id_shape(self):
        ids = tinyfish.capabilities()
        self.assertTrue(ids)
        for capability_id in ids:
            self.assertRegex(capability_id, r"^tinyfish\.[a-z][a-z0-9_]*$")
        self.assertEqual({"tinyfish." + n for n in
                          set(tinyfish.READ_TOOLS) | set(tinyfish.T3_TOOLS)},
                         set(tinyfish.capabilities(include_unreviewed=True)))
        # The advertised set is the reviewed subset of what is served.
        self.assertTrue(set(ids) <= set(tinyfish.capabilities(include_unreviewed=True)))

    def test_capabilities_is_stable_between_calls(self):
        self.assertEqual(tinyfish.capabilities(), tinyfish.capabilities())

    def test_every_catalog_entry_carries_the_registry_fields(self):
        for tool, entry in tinyfish.capability_catalog()["tools"].items():
            self.assertEqual("tinyfish." + tool, entry["capability_id"], tool)
            self.assertEqual("tinyfish", entry["provider"], tool)
            self.assertEqual(tool, entry["upstream_tool"], tool)
            self.assertEqual(tinyfish.TRANSPORT, entry["transport"], tool)
            self.assertIn(entry["tier"], CONTRACT_TIERS, tool)
            self.assertIn(entry["effect_class"], CONTRACT_EFFECTS, tool)
            self.assertTrue(entry["auth_scope"], tool)
            self.assertTrue(entry["cost_meter"], tool)
            self.assertIn(entry["availability"], CONTRACT_AVAILABILITY, tool)

    def test_effect_class_follows_the_contract_definitions(self):
        tools = tinyfish.capability_catalog()["tools"]
        self.assertEqual("READ_ONLY", tools["search"]["effect_class"])
        self.assertEqual("READ_ONLY", tools["fetch_content"]["effect_class"])
        self.assertEqual("READ_ONLY", tools["get_wallet"]["effect_class"])
        self.assertEqual("BILLABLE_ACT", tools["run_web_automation"]["effect_class"])
        self.assertEqual("STATE_CHANGING", tools["cancel_run"]["effect_class"])
        self.assertEqual("BILLABLE_ACT", tools["create_monitor"]["effect_class"])

    def test_availability_uses_the_closed_set(self):
        tools = tinyfish.capability_catalog()["tools"]
        self.assertEqual("ENABLED", tools["search"]["availability"])
        self.assertEqual("DISABLED", tools["create_monitor"]["availability"])
        # guide_next_step is a capability we deliberately will not serve.
        self.assertEqual("UNSUPPORTED", tools["guide_next_step"]["availability"])

    def test_no_duplicate_capability_ids(self):
        ids = [e["capability_id"] for e in
               tinyfish.capability_catalog()["tools"].values()]
        self.assertEqual(len(ids), len(set(ids)))


class ProbeSchema(AdapterSurface):
    def test_probe_without_authorization_reports_auth_required_and_touches_nothing(self):
        async def forbidden(tool, arguments):
            raise AssertionError("probe must not dispatch when unauthorized")

        report = tinyfish.probe_schema(runner=forbidden)
        self.assertEqual("AUTH_REQUIRED", report["status"])
        self.assertEqual("UNVERIFIED", report["schema_verdict"])
        self.assertEqual([], self.calls)

    def test_probe_reports_a_match_with_a_snapshot_hash(self):
        with patch.dict(os.environ, {"MARKETOPS_RESEARCH_ALLOW_NETWORK": "1"}):
            report = tinyfish.probe_schema(runner=self.runner(), advertised=CATALOG)
        self.assertEqual("MATCH", report["schema_verdict"])
        self.assertRegex(report["schema_sha256"], r"^[0-9a-f]{64}$")
        self.assertEqual(28, report["advertised_count"])

    def test_probe_never_fabricates_a_snapshot(self):
        # A probe that could not reach upstream must not look like a MATCH.
        async def failing(tool, arguments):
            raise TimeoutError("no route")

        report = tinyfish.probe_schema(runner=failing)
        self.assertNotEqual("MATCH", report["schema_verdict"])
        self.assertEqual("UNVERIFIED", report["schema_verdict"])
        self.assertNotIn("schema_sha256", report)


class VerifySchema(AdapterSurface):
    def test_verdicts_use_the_contract_closed_set(self):
        verdicts = tinyfish.verify_schema(CATALOG)
        # verify_schema keys what the adapter can serve, including capabilities
        # the review gate keeps out of the advertisement.
        self.assertEqual(set(tinyfish.capabilities(include_unreviewed=True)),
                         set(verdicts))
        for capability_id, verdict in verdicts.items():
            self.assertIn(verdict, CONTRACT_SCHEMA_VERDICTS, capability_id)
        self.assertEqual({"MATCH"}, set(verdicts.values()))

    def test_a_vanished_tool_is_absent_for_its_own_capability_only(self):
        verdicts = tinyfish.verify_schema([n for n in CATALOG if n != "get_run"])
        self.assertEqual("ABSENT", verdicts["tinyfish.get_run"])
        # IR-4: only the drifted capability is affected; T1 reads may continue.
        self.assertEqual("MATCH", verdicts["tinyfish.search"])
        self.assertEqual("MATCH", verdicts["tinyfish.fetch_content"])

    def test_an_unknown_advertised_tool_gets_no_capability_id(self):
        verdicts = tinyfish.verify_schema(CATALOG + ["purge_account"])
        self.assertNotIn("tinyfish.purge_account", verdicts)
        report = tinyfish.schema_drift(CATALOG + ["purge_account"])
        self.assertEqual(["purge_account"], report["unexpected"])
        self.assertIn("purge_account", report["denied"])

    def test_drift_on_a_paid_capability_is_reported_as_drift(self):
        verdicts = tinyfish.verify_schema(CATALOG)
        drifted = tinyfish.verify_schema([n for n in CATALOG
                                         if n != "run_web_automation"])
        self.assertEqual("ABSENT", drifted["tinyfish.run_web_automation"])
        self.assertEqual("MATCH", verdicts["tinyfish.run_web_automation"])


class ExecuteRequiresAnAuthorizedRequest(AdapterSurface):
    """IA-2: the adapter must not decide policy. It must also not be bypassable."""

    def test_execute_requires_a_well_formed_capability_request(self):
        with self.assertRaisesRegex(ValueError, "TINYFISH_CAPABILITY_REQUEST_INVALID"):
            tinyfish.execute({"capability_id": "tinyfish.search"}, runner=self.runner())
        self.assertEqual([], self.calls)

    def test_execute_refuses_an_unknown_capability_id_without_touching_network(self):
        req = Request("tinyfish.purge_account", {})
        result = tinyfish.execute(req, runner=self.runner())
        self.assertEqual("BLOCKED", result["outcome"])
        self.assertEqual("CAPABILITY_UNKNOWN", result["reason_code"])
        self.assertEqual([], self.calls)

    def test_execute_refuses_a_tier_three_capability_without_a_grant(self):
        # The adapter does not re-run the paid policy (IA-2); it requires the
        # caller to show that Policy already ran and produced a grant.
        req = Request("tinyfish.run_web_automation",
                      {"url": "https://public.example.org/x",
                       "goal": "Read the publicly listed venue names"})
        result = tinyfish.execute(req, runner=self.runner())
        self.assertEqual("BLOCKED", result["outcome"])
        self.assertEqual("POLICY_TIER_NOT_GRANTED", result["reason_code"])
        self.assertEqual([], self.calls)

    def test_execute_does_not_consult_the_project_paid_switch(self):
        # MARKETOPS_TINYFISH_AGENT_ALLOW_PAID is "0" in setUp; a granted request
        # still proceeds, because that switch belongs to the Policy lane.
        req = Request("tinyfish.run_web_automation",
                      {"url": "https://public.example.org/x",
                       "goal": "Read the publicly listed venue names"},
                      grant_ref="grant-1")
        result = tinyfish.execute(req, runner=self.runner(
            payloads={"get_wallet": WALLET,
                      "run_web_automation": {"runId": "run-g-1"}}))
        self.assertEqual("OK", result["outcome"])
        self.assertEqual("run-g-1", result["upstream_ref"])

    def test_execute_refuses_an_unauthorized_capability_without_network(self):
        result = tinyfish.execute(Request("tinyfish.create_monitor", {}),
                                  runner=self.runner())
        self.assertEqual("BLOCKED", result["outcome"])
        self.assertEqual("CAPABILITY_NOT_AVAILABLE", result["reason_code"])
        self.assertEqual([], self.calls)

    def test_execute_enforces_param_allowlisting(self):
        result = tinyfish.execute(Request("tinyfish.search", {"query": "ok query",
                                                              "engine": "google"}),
                                  runner=self.runner())
        self.assertEqual("BLOCKED", result["outcome"])
        self.assertEqual("CAPABILITY_PARAM_NOT_ALLOWED", result["reason_code"])
        self.assertEqual([], self.calls)

    def test_execute_reports_schema_drift_locally(self):
        result = tinyfish.execute(Request("tinyfish.get_run", {"id": "run-1"}),
                                  advertised_tools=[n for n in CATALOG
                                                    if n != "get_run"],
                                  runner=self.runner())
        self.assertEqual("BLOCKED", result["outcome"])
        self.assertEqual("CAPABILITY_SCHEMA_DRIFT", result["reason_code"])
        self.assertEqual([], self.calls)


class AdapterResultShape(AdapterSurface):
    def result(self, capability_id, params, **kwargs):
        # grant_ref is a field of the request, not an argument of execute().
        grant_ref = kwargs.pop("grant_ref", None)
        return tinyfish.execute(Request(capability_id, params, grant_ref=grant_ref),
                                runner=kwargs.pop("runner", self.runner(
                                    payloads={"get_wallet": WALLET})), **kwargs)

    def test_result_carries_every_contracted_field_and_no_undeclared_extra(self):
        result = self.result("tinyfish.search", {"query": "public venue list"})
        self.assertEqual(set(), ADAPTER_RESULT_FIELDS - set(result))
        self.assertEqual(ADAPTER_RESULT_EXTRAS, set(result) - ADAPTER_RESULT_FIELDS)
        self.assertIn(result["outcome"], CONTRACT_OUTCOMES)
        self.assertIn(result["actual_source"], CONTRACT_ACTUAL_SOURCES)
        self.assertIn(result["schema_verdict"], CONTRACT_SCHEMA_VERDICTS)
        self.assertIn(result["charge_state"], CONTRACT_CHARGE_STATES)
        self.assertEqual(CONTRACT_TRANSPORT, result["transport"])

    def test_a_successful_read_never_claims_a_settled_charge(self):
        result = self.result("tinyfish.search", {"query": "public venue list"})
        self.assertEqual("OK", result["outcome"])
        self.assertIsNone(result["actual_usd"])
        self.assertEqual("NONE", result["actual_source"])
        self.assertEqual("FREE_TIER", result["charge_state"])
        self.assertEqual((), result["artifacts"])
        self.assertTrue(result["caveats"])

    def test_a_refusal_is_blocked_with_a_closed_set_reason(self):
        result = tinyfish.execute(Request("tinyfish.search",
                                          {"query": "x"}),
                                  runner=self.runner())
        self.assertEqual("BLOCKED", result["outcome"])
        self.assertEqual("NO_CHARGE_PROVEN", result["charge_state"])
        self.assertEqual("CAPABILITY_PARAM_NOT_ALLOWED", result["reason_code"])

    def test_the_payload_is_sanitized_before_it_enters_the_result(self):
        payloads = {"search": {"results": [],
                              "checkout_url": "https://vendor.test/pay?session=live"}}
        result = self.result("tinyfish.search", {"query": "public venue list"},
                             runner=self.runner(payloads=payloads))
        self.assertEqual("[REDACTED]", result["payload"]["checkout_url"])

    def test_no_credential_reaches_the_result(self):
        import json
        result = self.result("tinyfish.search", {"query": "public venue list"})
        self.assertNotIn("synthetic-tinyfish-key", json.dumps(result, default=str))

    def test_a_post_dispatch_timeout_is_unknown_and_not_retried(self):
        # IA-3
        result = self.result("tinyfish.run_web_automation",
                             {"url": "https://public.example.org/x",
                              "goal": "Read the publicly listed venue names"},
                             grant_ref="grant-1",
                             runner=self.runner(payloads={
                                 "get_wallet": WALLET,
                                 "run_web_automation": {"runId": "run-9"}}))
        self.assertEqual("OK", result["outcome"])
        self.calls.clear()
        timed_out = tinyfish.execute(
            Request("tinyfish.run_web_automation",
                    {"url": "https://public.example.org/x",
                     "goal": "Read the publicly listed venue names"},
                    grant_ref="grant-1"),
            runner=self.runner(payloads={"get_wallet": WALLET},
                               raises=TimeoutError("slow"),
                               raises_on="run_web_automation"))
        self.assertEqual("UNKNOWN", timed_out["outcome"])
        self.assertEqual("UNKNOWN", timed_out["charge_state"])
        self.assertFalse(timed_out["retry_permitted"])
        self.assertIsNone(timed_out["actual_usd"])
        # Exactly one dispatch reached the vendor; nothing was re-sent (IA-3).
        self.assertEqual(["get_wallet", "run_web_automation"],
                         [n for n, _ in self.calls])

    def test_a_failed_wallet_preflight_blocks_instead_of_going_unknown(self):
        # Nothing was dispatched, so this must NOT be carried as exposure.
        result = tinyfish.execute(
            Request("tinyfish.run_web_automation",
                    {"url": "https://public.example.org/x",
                     "goal": "Read the publicly listed venue names"},
                    grant_ref="grant-1"),
            runner=self.runner(raises=TimeoutError("wallet"), raises_on="get_wallet"))
        self.assertEqual("BLOCKED", result["outcome"])
        self.assertEqual("WALLET_PRECONDITION_FAILED", result["reason_code"])
        self.assertEqual("NO_CHARGE_PROVEN", result["charge_state"])
        self.assertEqual(["get_wallet"], [n for n, _ in self.calls])

    def test_a_paid_dispatch_is_unknown_until_the_provider_settles(self):
        result = self.result("tinyfish.run_web_automation",
                             {"url": "https://public.example.org/x",
                              "goal": "Read the publicly listed venue names"},
                             grant_ref="grant-1",
                             runner=self.runner(payloads={
                                 "get_wallet": WALLET,
                                 "run_web_automation": {"runId": "run-9"}}))
        self.assertEqual("run-9", result["upstream_ref"])
        self.assertEqual("UNKNOWN", result["charge_state"])
        self.assertIsNone(result["actual_usd"])
        self.assertEqual("NONE", result["actual_source"])


class AdapterObject(AdapterSurface):
    """§6 defines ProviderAdapter as a Protocol on an *object*.

    `research_gateway.py` resolves `adapters[entry.provider]` and then calls
    `.capabilities()` / `.execute(request)` on what it finds, so a module of
    functions is not pluggable however correct its semantics are.
    """

    def test_the_adapter_object_satisfies_the_protocol_surface(self):
        adapter = tinyfish.TinyFishAdapter(runner=self.runner())
        self.assertEqual("tinyfish", adapter.provider)
        for name in ("capabilities", "probe_schema", "execute", "cancel"):
            self.assertTrue(callable(getattr(adapter, name)), name)

    def test_capabilities_agrees_with_the_module(self):
        adapter = tinyfish.TinyFishAdapter(runner=self.runner())
        self.assertEqual(tinyfish.capabilities(), tuple(adapter.capabilities()))

    def test_execute_delegates_and_returns_the_same_shape(self):
        adapter = tinyfish.TinyFishAdapter(runner=self.runner())
        result = adapter.execute(Request("tinyfish.search",
                                         {"query": "public venue list"}))
        self.assertEqual("OK", result["outcome"])
        self.assertEqual(result["outcome"], getattr(result, "outcome", None))
        self.assertEqual(set(ADAPTER_RESULT_FIELDS | ADAPTER_RESULT_EXTRAS),
                         set(result))

    def test_the_injected_runner_is_what_gets_used(self):
        adapter = tinyfish.TinyFishAdapter(runner=self.runner())
        adapter.execute(Request("tinyfish.search", {"query": "public venue list"}))
        self.assertEqual([("search", {"query": "public venue list"})], self.calls)

    def test_probe_and_cancel_delegate_too(self):
        adapter = tinyfish.TinyFishAdapter(runner=self.runner(
            payloads={"cancel_run": {"status": "CANCELLED"}}))
        self.assertNotEqual("MATCH", adapter.probe_schema()["schema_verdict"])
        cancelled = adapter.cancel("run-abc", grant_ref="grant-1")
        self.assertEqual("BLOCKED", cancelled["outcome"])
        self.assertEqual("CAPABILITY_NOT_AVAILABLE", cancelled["reason_code"])

    def test_an_adapter_object_without_an_injected_runner_still_refuses_cleanly(self):
        adapter = tinyfish.TinyFishAdapter()
        result = adapter.execute(Request("tinyfish.purge_account", {}))
        self.assertEqual("BLOCKED", result["outcome"])


class IntegratorJoint(AdapterSurface):
    """The joint with `research_gateway.py` on the integration branch.

    That gateway reaches the result by *attribute* (`getattr(result, "caveats", ())`)
    and, for an `outcome == "BLOCKED"` result, requires a §5.3 closed-set code to
    be one of the `caveats` elements — otherwise it raises `ContractViolation`
    rather than guessing a code. Both are facts about the integrator's code, not
    preferences, so they are pinned here.
    """

    def blocked(self, capability_id, params):
        return tinyfish.execute(Request(capability_id, params), runner=self.runner())

    def test_a_result_is_reachable_by_attribute_not_only_by_key(self):
        result = tinyfish.execute(Request("tinyfish.search",
                                          {"query": "public venue list"}),
                                  runner=self.runner())
        self.assertEqual(result["outcome"], getattr(result, "outcome", None))
        self.assertEqual(result["caveats"], tuple(getattr(result, "caveats", None) or ()))
        self.assertEqual(result["schema_verdict"],
                         getattr(result, "schema_verdict", None))
        self.assertIsNone(getattr(result, "upstream_ref", "missing"))

    def test_every_blocked_result_carries_a_closed_set_code_in_caveats(self):
        cases = (
            ("tinyfish.purge_account", {}),
            ("tinyfish.create_monitor", {}),
            ("tinyfish.search", {"query": "x"}),
            ("tinyfish.run_web_automation", {"url": "https://public.example.org/x",
                                             "goal": "Read the publicly listed venues"}),
        )
        for capability_id, params in cases:
            result = self.blocked(capability_id, params)
            self.assertEqual("BLOCKED", result["outcome"], capability_id)
            self.assertTrue(
                [c for c in result["caveats"] if c in CONTRACT_REASON_CODES],
                "%s: no closed-set code in caveats" % capability_id)
            self.assertIn(result["reason_code"], CONTRACT_REASON_CODES, capability_id)

    def test_a_schema_drift_refusal_also_carries_its_code(self):
        result = tinyfish.execute(Request("tinyfish.get_run", {"id": "run-1"}),
                                  advertised_tools=[n for n in CATALOG if n != "get_run"],
                                  runner=self.runner())
        self.assertEqual("BLOCKED", result["outcome"])
        self.assertIn("CAPABILITY_SCHEMA_DRIFT", result["caveats"])

    def test_the_url_guard_has_exactly_one_source_and_no_fallback(self):
        # One source, deliberately. A second entry would be an SSRF policy
        # bypass, not defence in depth: the weaker of two rules would decide and
        # nothing would report the divergence. A missing source is a governed
        # refusal, never a reason to try another implementation.
        self.assertEqual((("research_egress", "approved_url"),),
                         tuple(tinyfish.URL_GUARD_SOURCES))
        self.assertEqual("SHAPE_DURING_VALIDATION_RESOLVE_BEFORE_DISPATCH",
                         tinyfish.TARGET_GUARD_ORDER)

    def test_the_declared_source_is_the_one_that_is_used(self):
        seen = []

        def guard(value, *, resolve=False, resolver=None):
            seen.append(value)
            return value

        declared = tinyfish.URL_GUARD_SOURCES[0][0]
        with patch.object(tinyfish, "_url_guard", egress_fixture.REAL_URL_GUARD):
            with patch.object(tinyfish, "_import_guard",
                              lambda module, attr: guard if module == declared else None):
                self.assertEqual("https://public.example.org/a",
                                 tinyfish.approved_url("https://public.example.org/a"))
        self.assertEqual(["https://public.example.org/a"], seen)
        # and nothing else resolves: the single source is not "try anything"
        with patch.object(tinyfish, "_url_guard", egress_fixture.REAL_URL_GUARD):
            with patch.object(tinyfish, "_import_guard", lambda module, attr: None):
                with self.assertRaisesRegex(ValueError, "EGRESS_REFUSED"):
                    tinyfish.approved_url("https://public.example.org/a")

    def test_a_missing_url_guard_is_a_closed_set_refusal_not_a_crash(self):
        # Previously this raised ModuleNotFoundError, which the gateway cannot
        # classify: a provable no-charge dispatch became UNKNOWN exposure.
        with patch.object(tinyfish, "_url_guard", egress_fixture.REAL_URL_GUARD):
            with patch.object(tinyfish, "_import_guard", lambda module, attr: None):
                with self.assertRaises(ValueError) as err:
                    tinyfish.approved_url("https://public.example.org/a")
        self.assertEqual("EGRESS_REFUSED", str(err.exception))
        self.assertIn("EGRESS_REFUSED", CONTRACT_REASON_CODES)

    def test_a_missing_primary_guard_is_a_governed_refusal_with_zero_contact(self):
        # No fallback exists, so "the guard module is missing" has exactly one
        # outcome: a governed EGRESS_REFUSED with nothing contacted -- no
        # provider, no adapter dispatch, no egress, no ledger, no DNS.
        with patch.object(tinyfish, "_url_guard", egress_fixture.REAL_URL_GUARD):
            with patch.object(tinyfish, "_import_guard", lambda module, attr: None):
                result = tinyfish.execute(
                    Request("tinyfish.fetch_content",
                            {"urls": ["https://public.example.org/a"]}),
                    runner=self.runner())
        self.assertEqual("BLOCKED", result["outcome"])
        self.assertEqual("EGRESS_REFUSED", result["reason_code"])
        self.assertIn(result["reason_code"], CONTRACT_REASON_CODES)
        self.assertEqual("NO_CHARGE_PROVEN", result["charge_state"])
        self.assertFalse(result["dispatched"])
        self.assertEqual([], self.calls)      # no adapter/provider dispatch
        self.assertEqual([], self.dns.attempts)   # no resolution attempted

    def test_no_reason_code_this_adapter_emits_is_outside_the_closed_set(self):
        # A code the gateway cannot translate is a ContractViolation, so the
        # adapter must not invent one.
        for name in dir(tinyfish):
            if not name.startswith("REASON_"):
                continue
            self.assertIn(getattr(tinyfish, name), CONTRACT_REASON_CODES, name)


class Cancel(AdapterSurface):
    def test_cancel_is_refused_because_the_registry_disables_it(self):
        # B reviews cancel_run at T4 DISABLED. The wrapper still exists for the
        # gateway, but it must refuse before touching anything: a disabled
        # capability is not a per-operation authorization question.
        runner = self.runner(payloads={"cancel_run": {"status": "CANCELLED"}})
        result = tinyfish.cancel("run-abc", grant_ref="grant-1", runner=runner)
        self.assertEqual("BLOCKED", result["outcome"])
        self.assertEqual("CAPABILITY_NOT_AVAILABLE", result["reason_code"])
        self.assertEqual("NO_CHARGE_PROVEN", result["charge_state"])
        self.assertFalse(result["dispatched"])
        self.assertEqual([], self.calls)

    def test_cancel_is_refused_without_a_grant_too(self):
        result = tinyfish.cancel("run-abc", runner=self.runner())
        self.assertEqual("BLOCKED", result["outcome"])
        self.assertEqual("CAPABILITY_NOT_AVAILABLE", result["reason_code"])
        self.assertEqual([], self.calls)


class ContractVocabularyIsOneSourceOfTruth(AdapterSurface):
    def test_the_adapter_does_not_redeclare_the_shared_types(self):
        # §2: shared types come from research_contract.py (W-B). Until it lands
        # the adapter uses the contract's literal values and must not ship a
        # competing definition of Tier/ChargeState/... .
        for name in ("Tier", "EffectClass", "ChargeState", "ActualSource",
                     "OutcomeCode", "Availability", "SchemaVerdict"):
            self.assertFalse(hasattr(tinyfish, name),
                             "adapter must not define shared type %s" % name)

    def test_contract_reference_is_recorded_in_the_module(self):
        self.assertIn("INTERFACE-CONTRACT.md", tinyfish.CONTRACT_REFERENCE)


if __name__ == "__main__":
    unittest.main()
