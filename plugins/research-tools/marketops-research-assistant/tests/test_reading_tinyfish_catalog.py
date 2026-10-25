"""Pinned TinyFish official tool catalog: tiers, enablement and schema drift.

These tests encode the *reviewed* upstream catalog so a silent upstream change
becomes a test failure or an explicit drift record, never a new callable tool.
No socket, credential or provider account is touched.
"""
from __future__ import annotations

import hashlib
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import research_adapter_tinyfish as tinyfish


OFFICIAL_TOOLS = {
    # Search & fetch
    "search", "fetch_content",
    # Web automation (metered via the account wallet)
    "run_web_automation", "run_web_automation_async", "get_run", "list_runs",
    "cancel_run", "batch_status", "batch_cancel",
    # Browser sessions
    "create_browser_session", "list_browser_sessions", "close_browser_session",
    # Browser Context Profiles (saved login state)
    "create_profile", "list_profiles", "start_profile_setup_session",
    "save_profile_setup_session", "cancel_profile_setup_session",
    # Monitors
    "create_monitor", "get_monitor", "list_monitors", "run_monitor",
    "pause_monitor", "resume_monitor", "cancel_monitor",
    # Account & usage
    "get_wallet", "get_search_usage", "list_fetch_usage", "guide_next_step",
}


class OfficialEndpointAndPins(unittest.TestCase):
    def test_endpoint_is_the_official_streamable_http_mcp_url(self):
        self.assertEqual("https://agent.tinyfish.ai/mcp", tinyfish.ENDPOINT)
        self.assertEqual("tinyfish", tinyfish.PROVIDER)

    def test_catalog_covers_exactly_the_documented_upstream_tools(self):
        catalog = tinyfish.capability_catalog()
        self.assertEqual(OFFICIAL_TOOLS, set(catalog["tools"]))
        self.assertEqual(28, catalog["tool_count"])

    def test_catalog_pin_is_a_stable_sha256_of_the_descriptors(self):
        first = tinyfish.capability_catalog()
        # A pin that moves between two reads in one process is not a pin.
        self.assertEqual(first["catalog_sha256"], tinyfish.catalog_sha256())
        self.assertRegex(first["catalog_sha256"], r"^[0-9a-f]{64}$")
        # Recompute from the descriptors the caller can actually see.
        canonical = json.dumps(first["tools"], sort_keys=True,
                               separators=(",", ":"), ensure_ascii=False,
                               default=str).encode("utf-8")
        self.assertEqual(hashlib.sha256(canonical).hexdigest(),
                         first["catalog_sha256"])

    def test_catalog_records_where_the_pin_came_from(self):
        catalog = tinyfish.capability_catalog()
        self.assertIn("tinyfish-mcp-server", catalog["catalog_source"])
        self.assertRegex(catalog["catalog_observed_at"], r"^\d{4}-\d{2}-\d{2}$")
        # A static doc pin is not an account probe and must say so.
        self.assertEqual("STATIC_UPSTREAM_DOC_PIN_NOT_ACCOUNT_VERIFIED",
                         catalog["pin_evidence"])


class TierAndEnablement(unittest.TestCase):
    def test_only_reviewed_read_and_gated_tools_are_enabled(self):
        catalog = tinyfish.capability_catalog()
        enabled = {n for n, d in catalog["tools"].items() if d["enabled"]}
        self.assertEqual(set(tinyfish.READ_TOOLS) | set(tinyfish.T3_TOOLS), enabled)

    def test_every_enabled_read_tool_is_tier_one_or_above(self):
        catalog = tinyfish.capability_catalog()
        for name in tinyfish.READ_TOOLS:
            # B's registry puts the account/usage reads at T0 and the
            # provider-facing reads at T1; both are read-only and unmetered.
            self.assertIn(catalog["tools"][name]["tier"], {"T0", "T1"}, name)
            self.assertTrue(catalog["tools"][name]["callable"], name)
            self.assertFalse(catalog["tools"][name]["billing"]["may_charge"], name)

    def test_paid_agent_is_a_consented_paid_read_and_never_ungated(self):
        catalog = tinyfish.capability_catalog()
        agent = catalog["tools"]["run_web_automation"]
        self.assertEqual("T2", agent["tier"])
        self.assertTrue(agent["billing"]["may_charge"])
        self.assertNotIn("run_web_automation", tinyfish.ungated_tools())

    def test_the_described_only_tools_are_listed_with_a_reason(self):
        # 14, not 17: the read-only account inventory (list_monitors,
        # get_monitor, list_browser_sessions) was enabled as T1 by
        # test_reading_tinyfish_account_reads. What remains described-only is
        # everything that spends, mutates, or reads credentials.
        catalog = tinyfish.capability_catalog()
        described_only = OFFICIAL_TOOLS - set(tinyfish.READ_TOOLS) - set(tinyfish.T3_TOOLS)
        # 16: the two T4 cancels are reviewed-but-disabled, so they are
        # described rather than served.
        self.assertEqual(16, len(described_only))
        for name in sorted(described_only):
            entry = catalog["tools"][name]
            self.assertFalse(entry["enabled"], name)
            self.assertFalse(entry["callable"], name)
            self.assertTrue(entry["authorization"], name)
            self.assertTrue(entry["deferred_reason"], name)

    def test_the_billable_mutating_and_credential_families_stay_tier_four(self):
        # Reading the account's own session/monitor inventory is T1 and enabled
        # (it spends nothing and reads no credentials). Everything that spends,
        # changes state, or reads credentials stays T4 and disabled.
        catalog = tinyfish.capability_catalog()
        for name in ("run_web_automation_async", "create_browser_session",
                     "close_browser_session", "create_profile", "list_profiles",
                     "start_profile_setup_session", "save_profile_setup_session",
                     "cancel_profile_setup_session", "create_monitor", "run_monitor",
                     "pause_monitor", "resume_monitor", "cancel_monitor"):
            entry = catalog["tools"][name]
            self.assertEqual("T4", entry["tier"], name)
            self.assertFalse(entry["enabled"], name)

    def test_recurring_monitors_carry_their_published_billing_note(self):
        entry = tinyfish.capability_catalog()["tools"]["create_monitor"]
        self.assertIn("0.005", entry["billing"]["note"])
        self.assertEqual("RECURRING_SCHEDULE", entry["effect"])

    def test_unknown_or_nonexistent_upstream_tools_are_not_in_the_catalog(self):
        tools = tinyfish.capability_catalog()["tools"]
        # The fork's snapshot used a run-status verb the official catalog does
        # not publish; it must never be silently allowed as an alias.
        self.assertNotIn("wait_for_run", tools)
        self.assertNotIn("invoke", tools)
        self.assertNotIn("create_session", tools)

    def test_catalog_is_immutable_for_callers(self):
        catalog = tinyfish.capability_catalog()
        before = tinyfish.catalog_sha256()
        catalog["tools"]["search"]["enabled"] = False
        self.assertEqual(before, tinyfish.catalog_sha256())


class SchemaDrift(unittest.TestCase):
    def test_matching_advertised_list_reports_no_drift(self):
        report = tinyfish.schema_drift(sorted(OFFICIAL_TOOLS))
        self.assertFalse(report["drift"])
        self.assertEqual([], report["missing"])
        self.assertEqual([], report["unexpected"])
        self.assertEqual(28, report["advertised_count"])

    def test_removed_upstream_tool_is_reported_as_missing(self):
        report = tinyfish.schema_drift(sorted(OFFICIAL_TOOLS - {"get_run"}))
        self.assertTrue(report["drift"])
        self.assertEqual(["get_run"], report["missing"])

    def test_new_upstream_tool_is_reported_and_denied_by_default(self):
        advertised = sorted(OFFICIAL_TOOLS | {"delete_account"})
        report = tinyfish.schema_drift(advertised)
        self.assertTrue(report["drift"])
        self.assertEqual(["delete_account"], report["unexpected"])
        self.assertIn("delete_account", report["denied"])
        with self.assertRaisesRegex(ValueError, "TINYFISH_TOOL_NOT_IN_OFFICIAL_CATALOG"):
            tinyfish.validate("delete_account", {})

    def test_duplicate_advertised_names_are_drift_not_silent_dedup(self):
        report = tinyfish.schema_drift(sorted(OFFICIAL_TOOLS) + ["search"])
        self.assertTrue(report["drift"])
        self.assertEqual(["search"], report["duplicated"])

    def test_advertised_tool_objects_are_accepted_and_sanitized(self):
        class Tool:
            def __init__(self, name):
                self.name = name

        report = tinyfish.schema_drift([Tool(n) for n in sorted(OFFICIAL_TOOLS)])
        self.assertFalse(report["drift"])

    def test_hostile_advertised_values_cannot_break_the_comparison(self):
        report = tinyfish.schema_drift([None, 7, {"name": "search"}, "search", ""])
        self.assertTrue(report["drift"])
        # The one real name is seen; the four non-names are quarantined.
        self.assertNotIn("search", report["missing"])
        self.assertEqual(27, len(report["missing"]))
        self.assertEqual(4, report["malformed_count"])
        # A quarantined value never becomes a callable capability.
        self.assertEqual([], report["unexpected"])

    def test_oversized_advertisement_is_refused_rather_than_truncated(self):
        with self.assertRaisesRegex(ValueError, "TINYFISH_SCHEMA_ADVERTISEMENT_TOO_LARGE"):
            tinyfish.schema_drift(["t%d" % i for i in range(400)])


class CapabilityMatrixAndIntegrationSurface(unittest.TestCase):
    def test_matrix_labels_every_capability_with_separate_status_fields(self):
        rows = tinyfish.capability_matrix()
        self.assertEqual(28, len(rows))
        for row in rows:
            self.assertTrue(row["coded"])
            self.assertEqual("OFFLINE_TESTED", row["offline_tested"])
            # No real account exists here; advertising LIVE would be a lie.
            self.assertEqual("LIVE-UNVERIFIED", row["live"])
            self.assertEqual("OFFLINE_TESTED", row["offline_tested"])
            # No real account exists here; advertising LIVE would be a lie.
            self.assertEqual("LIVE-UNVERIFIED", row["live"])

    def test_matrix_marks_the_paid_and_profile_families_not_enabled(self):
        by_tool = {r["tool"]: r for r in tinyfish.capability_matrix()}
        self.assertEqual("ENABLED_T1", by_tool["search"]["enablement"])
        self.assertEqual("GATED_T3", by_tool["run_web_automation"]["enablement"])
        self.assertEqual("DESCRIBED_NOT_ENABLED",
                         by_tool["start_profile_setup_session"]["enablement"])
        self.assertEqual("DESCRIBED_NOT_ENABLED", by_tool["create_monitor"]["enablement"])

    def test_integration_surface_lists_only_what_the_router_may_call(self):
        surface = tinyfish.integration_surface()
        self.assertEqual(set(tinyfish.READ_TOOLS) | set(tinyfish.T3_TOOLS),
                         {entry["tool"] for entry in surface["tools"]})
        self.assertEqual("https://agent.tinyfish.ai/mcp", surface["endpoint"])
        for entry in surface["tools"]:
            self.assertTrue(entry["arguments"], entry["tool"])
            self.assertIn("gate", entry)

    def test_matrix_and_catalog_never_disagree_about_enablement(self):
        catalog = tinyfish.capability_catalog()["tools"]
        for row in tinyfish.capability_matrix():
            entry = catalog[row["tool"]]
            self.assertEqual(entry["enabled"], row["enabled"], row["tool"])
            self.assertEqual(entry["tier"], row["tier"], row["tool"])


if __name__ == "__main__":
    unittest.main()
