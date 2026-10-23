"""TinyFish account-inventory reads: the registry decision, and its boundary.

The adapter at integration SHA ``fba7b27e`` advertises 14 capabilities. Three of
them were absent from the reviewed registry, so the whole adapter failed
``CAPABILITIES_NOT_IN_REGISTRY``. This file records the review and pins the
result:

* ``get_monitor``, ``list_monitors`` and ``list_browser_sessions`` are added as
  **reviewed** T1 read-only entries on the documented upstream catalog
  (`references/providers/tinyfish.md`, pinned 2026-10-17, and the per-tool
  contract), not on a captured ``tools/list``;
* their parameter allowlists are the documented ones only — ``get_monitor``
  requires ``monitor_id``; ``list_monitors`` has no parameters;
  ``list_browser_sessions`` exposes only ``limit``/``page``, the two the
  provider reference documents with defaults. The adapter's other optional
  filters stay unexposed until they are documented and reviewed;
* they are enabled only in the registry sense: the vendor schema is still
  ``NOT_CAPTURED``, readiness stays ``OFFLINE_TESTED``, and no live claim is made;
* the other 25 reviewed capabilities are unchanged, the default registry is
  immutable, and the campaign floor / TinyFish minimum / UNKNOWN hold are not
  touched.

Offline: no network, no credential, no provider call. The only adapter here
counts calls and must stay at zero.
"""
from __future__ import annotations

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

NOW = datetime(2026, 10, 18, 12, 0, tzinfo=timezone.utc)

#: What the TinyFish adapter advertises at integration ``fba7b27e`` (recorded
#: from the module itself; the integration scratch run compares the two sets).
ADAPTER_TINYFISH_CAPABILITIES = (
    "tinyfish.search", "tinyfish.fetch_content", "tinyfish.get_run",
    "tinyfish.list_runs", "tinyfish.batch_status", "tinyfish.list_browser_sessions",
    "tinyfish.get_monitor", "tinyfish.list_monitors", "tinyfish.get_wallet",
    "tinyfish.get_search_usage", "tinyfish.list_fetch_usage",
    "tinyfish.run_web_automation", "tinyfish.cancel_run", "tinyfish.batch_cancel",
)

#: The 25 capabilities that existed before this decision. Their reviewed fields
#: must not move when three entries are added.
BASELINE_25 = {
    "tinyfish.search": ("T1", "ENABLED", "OFFLINE_TESTED", "search"),
    "tinyfish.fetch_content": ("T1", "ENABLED", "OFFLINE_TESTED", "fetch"),
    "tinyfish.get_run": ("T0", "ENABLED", "OFFLINE_TESTED", "usage_read"),
    "tinyfish.list_runs": ("T0", "ENABLED", "OFFLINE_TESTED", "usage_read"),
    "tinyfish.batch_status": ("T0", "ENABLED", "OFFLINE_TESTED", "usage_read"),
    "tinyfish.get_wallet": ("T0", "ENABLED", "OFFLINE_TESTED", "wallet_read"),
    "tinyfish.get_search_usage": ("T0", "ENABLED", "OFFLINE_TESTED", "usage_read"),
    "tinyfish.list_fetch_usage": ("T0", "ENABLED", "OFFLINE_TESTED", "usage_read"),
    "tinyfish.run_web_automation": ("T3", "ENABLED", "CODED", "agent_run"),
    "tinyfish.cancel_run": ("T4", "DISABLED", "DEFERRED", None),
    "tinyfish.batch_cancel": ("T4", "DISABLED", "DEFERRED", None),
    "serpapi.search": ("T2", "ENABLED", "OFFLINE_TESTED", "search"),
    "serpapi.engine_catalog": ("T0", "DISABLED", "DEFERRED", None),
    "serpapi.engine_schema": ("T0", "DISABLED", "DEFERRED", None),
    "serper.search": ("T2", "ENABLED", "OFFLINE_TESTED", "search"),
    "crawl4ai.fetch_page": ("T1", "ENABLED", "OFFLINE_TESTED", "render"),
    "crawl4ai.extract": ("T1", "ENABLED", "OFFLINE_TESTED", "extract"),
    "crawl4ai.deep_crawl": ("T1", "ENABLED", "OFFLINE_TESTED", "render"),
    "crawl4ai.sitemap_seed": ("T1", "ENABLED", "OFFLINE_TESTED", "render"),
    "crawl4ai.screenshot": ("T1", "ENABLED", "OFFLINE_TESTED", "render"),
    "crawl4ai.pdf": ("T1", "ENABLED", "OFFLINE_TESTED", "render"),
    "jev.choice": ("T2", "ENABLED", "OFFLINE_TESTED", "judge"),
    "jev.noul": ("T2", "ENABLED", "OFFLINE_TESTED", "judge"),
    "jev.score": ("T2", "ENABLED", "OFFLINE_TESTED", "judge"),
    "jev.box": ("T2", "ENABLED", "OFFLINE_TESTED", "judge"),
}

NEW_IDS = ("tinyfish.get_monitor", "tinyfish.list_monitors",
           "tinyfish.list_browser_sessions")


class CountingAdapter:
    """An adapter that must stay unreached by anything refused before dispatch."""

    def __init__(self) -> None:
        self.calls = 0

    def execute(self, request):
        self.calls += 1
        raise AssertionError("a refused request reached an adapter")


def request(capability_id: str, params: dict) -> contract.CapabilityRequest:
    return contract.CapabilityRequest(
        capability_id=capability_id, params=params, run_id="run-01",
        operation_id="op-01", account_ref="tinyfish-main",
        intent_fingerprint="intent-1")


def policy(*, network: bool, domains=("example.com",)) -> policy_module.Policy:
    return policy_module.Policy(registry=registry_module.DEFAULT_REGISTRY,
                                network_enabled=network, allowed_domains=domains,
                                clock=lambda: NOW)


def dispatch_if_allowed(decision, adapter, req) -> None:
    """The gateway's precondition, in one line: dispatch only on ALLOW."""
    if decision.verdict is contract.PolicyVerdict.ALLOW:
        adapter.execute(req)


class ReviewedEntryTests(unittest.TestCase):
    def test_the_three_documented_reads_are_reviewed_entries(self):
        reg = registry_module.DEFAULT_REGISTRY
        for capability_id in NEW_IDS:
            with self.subTest(capability_id=capability_id):
                entry = reg.get(capability_id)
                self.assertIsNotNone(entry)
                self.assertEqual(entry.provider, "tinyfish")
                self.assertIs(entry.tier, contract.Tier.T1)
                self.assertIs(entry.effect_class, contract.EffectClass.READ_ONLY)
                self.assertIs(entry.availability, contract.Availability.ENABLED)
                self.assertEqual(entry.readiness, "OFFLINE_TESTED")
                self.assertEqual(entry.ledger_capability, "usage_read")
                self.assertEqual(entry.cost_meter, "request")

    def test_the_documented_parameter_sets_are_the_allowlists(self):
        reg = registry_module.DEFAULT_REGISTRY
        self.assertEqual(reg.get("tinyfish.get_monitor").allowed_params,
                         frozenset({"monitor_id"}))
        self.assertEqual(reg.get("tinyfish.get_monitor").param_schema.get("required"),
                         ["monitor_id"])
        self.assertEqual(reg.get("tinyfish.list_monitors").allowed_params, frozenset())
        self.assertEqual(reg.get("tinyfish.list_browser_sessions").allowed_params,
                         frozenset({"limit", "page"}))
        self.assertIsNone(
            reg.get("tinyfish.list_browser_sessions").param_schema.get("required"))

    def test_the_adapter_is_now_fully_covered_and_not_left_disabled(self):
        reg = registry_module.DEFAULT_REGISTRY
        reviewed = {entry.capability_id for entry in reg
                    if entry.provider == "tinyfish"}
        self.assertEqual(reviewed, set(ADAPTER_TINYFISH_CAPABILITIES))
        enabled = [entry for entry in reg
                   if entry.provider == "tinyfish"
                   and entry.availability is contract.Availability.ENABLED]
        # 12 of 14: only the two T4 state-changing cancels remain off.
        self.assertEqual(len(enabled), 12)
        self.assertEqual({entry.capability_id for entry in reg
                          if entry.provider == "tinyfish"
                          and entry.availability is contract.Availability.DISABLED},
                         {"tinyfish.cancel_run", "tinyfish.batch_cancel"})

    def test_the_original_25_capabilities_are_unchanged(self):
        reg = registry_module.DEFAULT_REGISTRY
        self.assertEqual(len(reg), 30)  # + deepseek.search, deepseek.fetch_page
        for capability_id, expected in BASELINE_25.items():
            with self.subTest(capability_id=capability_id):
                entry = reg.get(capability_id)
                self.assertIsNotNone(entry)
                self.assertEqual(
                    (entry.tier.value, entry.availability.value, entry.readiness,
                     entry.ledger_capability), expected)

    def test_the_vendor_schema_is_still_not_captured(self):
        reg = registry_module.DEFAULT_REGISTRY
        self.assertIsNone(reg.vendor_schema_sha256)
        self.assertIsNone(reg.authoritative_vendor_schema_sha256("tinyfish", as_of=NOW))
        self.assertIs(reg.authoritative_status("tinyfish"),
                      registry_module.VendorSchemaStatus.NOT_CAPTURED)
        self.assertNotIn("READY", " ".join(entry.readiness for entry in reg))
        # The documented basis is recorded in the entry, not turned into a claim.
        for capability_id in NEW_IDS:
            self.assertIn("Documented", reg.get(capability_id).description)


class PolicyBoundaryTests(unittest.TestCase):
    def test_network_disabled_refuses_all_three_before_any_adapter(self):
        for capability_id, params in (
                ("tinyfish.get_monitor", {"monitor_id": "mon-1"}),
                ("tinyfish.list_monitors", {}),
                ("tinyfish.list_browser_sessions", {"limit": 100, "page": 1})):
            with self.subTest(capability_id=capability_id):
                adapter = CountingAdapter()
                req = request(capability_id, params)
                decision = policy(network=False).evaluate(req)
                self.assertIs(decision.verdict, contract.PolicyVerdict.DENY)
                self.assertEqual(decision.reason_code, "RESEARCH_NETWORK_DISABLED")
                dispatch_if_allowed(decision, adapter, req)
                self.assertEqual(adapter.calls, 0)

    def test_a_network_enabled_t1_read_is_allowed_without_a_grant(self):
        for capability_id, params in (
                ("tinyfish.get_monitor", {"monitor_id": "mon-1"}),
                ("tinyfish.list_monitors", {}),
                ("tinyfish.list_browser_sessions", {"limit": 100, "page": 1})):
            with self.subTest(capability_id=capability_id):
                adapter = CountingAdapter()
                req = request(capability_id, params)
                decision = policy(network=True).evaluate(req)
                self.assertIs(decision.verdict, contract.PolicyVerdict.ALLOW)
                self.assertEqual(decision.reason_code, "")
                self.assertIs(decision.effective_tier, contract.Tier.T1)
                # This test evaluates policy only; no adapter and no egress.
                self.assertEqual(adapter.calls, 0)

    def test_parameters_outside_the_documented_set_are_refused(self):
        cases = (
            ("tinyfish.get_monitor", {"monitor_id": "mon-1", "url": "https://x/"}),
            ("tinyfish.list_monitors", {"limit": 10}),
            ("tinyfish.list_browser_sessions", {"status": "active"}),
            ("tinyfish.list_browser_sessions", {"session_id": "s-1"}),
            ("tinyfish.list_browser_sessions", {"start_after": "2026-01-01"}),
        )
        for capability_id, params in cases:
            with self.subTest(capability_id=capability_id, params=params):
                decision = policy(network=True).evaluate(request(capability_id, params))
                self.assertIs(decision.verdict, contract.PolicyVerdict.DENY)
                self.assertEqual(decision.reason_code, "CAPABILITY_PARAM_NOT_ALLOWED")

    def test_the_disabled_monitor_and_session_mutations_stay_disabled(self):
        for capability_id in ("tinyfish.create_monitor", "tinyfish.run_monitor",
                              "tinyfish.create_browser_session",
                              "tinyfish.close_browser_session"):
            with self.subTest(capability_id=capability_id):
                self.assertIsNone(registry_module.DEFAULT_REGISTRY.get(capability_id))


class LedgerRouteTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.project = usage.project_for_root(self.root)
        self.ledger = usage.UsageLedger(
            self.root / "state" / "usage-ledger.sqlite3",
            pricing=usage.documented_pricing(), registry=grants.DEFAULT_REGISTRY,
            clock=lambda: NOW)
        self.ledger.set_cap("run", f"{self.project}/run-01", ceiling_micros=0,
                            ceiling_calls=10, ceiling_seconds=3_600, source="registry-test")

    def operation(self, operation_id: str, capability: str = "usage_read"):
        return usage.OperationRequest(
            project=self.project, run_id="run-01", operation_id=operation_id,
            provider="tinyfish", capability=capability, account_ref="tinyfish-main",
            transport="official_mcp", intent="read account inventory",
            args={"monitor_id": "mon-1"})

    def test_an_audited_read_routes_and_settles_at_zero_with_one_quota_unit(self):
        self.ledger.reserve(self.operation("op-read-1"))
        self.ledger.mark_dispatch_intent("op-read-1")
        row = self.ledger.settle("op-read-1", actual_micros=0,
                                 actual_source=usage.ActualSource.DERIVED,
                                 upstream_ref="mcp-request-1", quota_units=1)
        self.assertEqual(row.charge_state, usage.ChargeState.SETTLED.value)
        self.assertEqual(row.actual_micros, 0)
        self.assertEqual(row.actual_source, usage.ActualSource.DERIVED.value)
        self.assertEqual(row.quota_units, 1)
        self.assertEqual(self.ledger.open_operations(), ())

    def test_the_campaign_floor_and_tinyfish_minimum_are_untouched(self):
        self.assertEqual(usage.CAMPAIGN_FLOOR_RATIO, Decimal("0.20"))
        self.assertEqual(usage.CAMPAIGN_MAX_DRAWDOWN_RATIO, Decimal("0.80"))
        self.assertEqual(usage.TINYFISH_MINIMUM_MICROS, 200_000)

    def test_an_unknown_charge_still_holds_its_exposure(self):
        self.ledger.reserve(self.operation("op-unknown-1"))
        self.ledger.mark_dispatch_intent("op-unknown-1")
        self.ledger.mark_unknown("op-unknown-1", reason="PROVIDER_TIMEOUT")
        totals = self.ledger.account_totals("tinyfish", "tinyfish-main")
        self.assertEqual(totals.quota_units, 1)
        self.assertEqual(totals.unknown_micros, 0)  # credit-priced: the unit is the hold
        self.assertIn("op-unknown-1",
                      [row.operation_id for row in self.ledger.open_operations()])


class ImmutabilityTests(unittest.TestCase):
    def test_a_derived_drift_does_not_change_the_default_registry(self):
        default = registry_module.DEFAULT_REGISTRY
        derived = default.with_schema_verdicts(
            {"tinyfish.get_monitor": contract.SchemaVerdict.DRIFT})
        self.assertIs(derived.get("tinyfish.get_monitor").availability,
                      contract.Availability.SCHEMA_DRIFT)
        self.assertIs(default.get("tinyfish.get_monitor").availability,
                      contract.Availability.ENABLED)
        self.assertEqual(default.observed_verdicts(), {})

    def test_adding_entries_did_not_create_a_vendor_digest(self):
        default = registry_module.DEFAULT_REGISTRY
        self.assertEqual(len(default.vendor_schema_statuses()), 6)
        for provider, status in default.vendor_schema_statuses().items():
            with self.subTest(provider=provider):
                self.assertIs(status, registry_module.VendorSchemaStatus.NOT_CAPTURED)


if __name__ == "__main__":
    unittest.main()
