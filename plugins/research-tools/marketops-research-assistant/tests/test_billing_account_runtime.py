"""Offline managed-path coverage: real gateway/ledger, synthetic Jev SDK only."""
import os
import sys
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from test_billing_account_balances import AccountCase
from test_jev_adapter import FakeSDK
import research_accounts as accounts
import research_balances as balances
import research_provider_health as health
import research_runtime_setup as runtime
import research_lite_store as store
import research_mcp as mcp
import research_usage_ledger as usage
import research_wiring as wiring


class HttpFailure(Exception):
    def __init__(self, status_code, message):
        self.status_code = status_code
        super().__init__(message)


class ManagedAccountTests(AccountCase):
    def setUp(self):
        super().setUp()
        env = patch.dict(os.environ, {"MARKETOPS_RESEARCH_DATA_ROOT": str(self.root),
                                      "MARKETOPS_RESEARCH_ALLOW_NETWORK": "1"})
        env.start(); self.addCleanup(env.stop)
        self.register()
        self.secondary = self.register(account_id="second", allow_failover=True)["account_ref"]
        accounts.configure_key(self.root, provider="typesafe_jev", account_ref=self.secondary,
                               key_id="second", value="second-test-key", confirm=True)
        runtime.configure(self.root, providers=["typesafe_jev"], domains=["example.org"],
                          max_calls=5, provider_limits={"typesafe_jev": 2},
                          task="synthetic named-account test", confirm=True)
        mcp.start("r", "synthetic", "judge saved evidence", "v1",
                  {"record_type": "research_brief", "target_kind": "organization", "goal": "pool",
                   "minimum_targets": None, "require_named_person": False})
        store.save_artifact(self.root, "r", "source", "crawl4ai", "https://example.org/",
                            b"Example Council operates Example Sports Ground and employs grounds staff.",
                            "rendered_html")
        self.sdk = FakeSDK(answer="operator")
        sdk = patch.dict(sys.modules, {"typesafe_sdk": self.sdk.build()})
        sdk.start(); self.addCleanup(sdk.stop)

    def call(self, operation="op-1", question="Which role does Example Council have?"):
        return mcp.managed_call("jev.choice", {"artifact_id": "source", "question": question,
                                "options": ["operator", "other"]}, "r", operation)

    def test_empty_primary_uses_the_named_account_and_records_its_actual_identity(self):
        balances.record_manual(self.root, provider="typesafe_jev", left=0, unit="USD")
        answer = self.call()
        self.assertEqual("OK", answer["status"], answer)
        self.assertEqual(self.secondary, answer["account_ref"])
        self.assertEqual("second-test-key", self.sdk.calls[0]["options"]["api_key"])
        self.assertEqual(self.secondary, usage.for_root(self.root).operations()[0].account_ref)

    def test_changing_account_does_not_reset_the_cumulative_provider_cap(self):
        self.assertEqual("OK", self.call()["status"])
        health.record(self.root, "typesafe_jev", "QUOTA_EXHAUSTED")
        self.assertEqual("OK", self.call("op-2", "Who operates this sports ground?")["status"])
        refused = self.call("op-3", "Which entity manages these grounds?")
        self.assertEqual("BUDGET_PROVIDER_CEILING", refused["reason_code"], refused)
        self.assertEqual(2, len(self.sdk.calls))

    def test_replaying_a_success_cannot_rebill_it_on_another_account(self):
        first = self.call()
        self.assertEqual("OK", first["status"])
        health.record(self.root, "typesafe_jev", "QUOTA_EXHAUSTED")
        replay = self.call("renamed-operation")
        self.assertTrue(replay["reused"])
        self.assertEqual(first["artifact_ids"], replay["artifact_ids"])
        self.assertEqual("typesafe_jev", replay["account_ref"])
        self.assertEqual(1, len(self.sdk.calls))

    def test_timeout_is_not_reissued_or_rotated(self):
        self.sdk.failure = TimeoutError("synthetic timeout")
        first = self.call()
        self.assertEqual("UNKNOWN", first["status"], first)
        again = self.call("new-operation-id")
        self.assertEqual("UNKNOWN", again["status"])
        self.assertTrue(again["reused"])
        self.assertEqual(1, len(self.sdk.calls))

    def test_enabling_a_pool_cannot_rebuy_a_historical_single_account_request(self):
        self.register(account_id="second", enabled=False)
        self.assertEqual("OK", self.call()["status"])
        self.assertEqual([], accounts.query(self.root, "SELECT * FROM intents"))
        self.register(account_id="second", allow_failover=True)
        health.record(self.root, "typesafe_jev", "QUOTA_EXHAUSTED")
        answer = self.call("renamed-after-upgrade")
        self.assertEqual("DUPLICATE_OPERATION", answer["reason_code"], answer)
        self.assertEqual(1, len(self.sdk.calls))

    def test_aggregate_cap_initialization_cannot_replace_existing_money_limits(self):
        ledger = usage.for_root(self.root)
        key = usage.project_for_root(self.root) + "/typesafe_jev/*"
        ledger.set_cap("provider_account", key, ceiling_calls=2,
                       ceiling_micros=500000, usd_enforced=True, source="OPERATOR")
        def initialize(_):
            ledger.set_cap("provider_account", key, ceiling_calls=5,
                           source="STALE_INITIALIZER", if_absent=True)
        with ThreadPoolExecutor(max_workers=4) as pool:
            list(pool.map(initialize, range(8)))
        cap = ledger.remaining("provider_account", key)
        self.assertEqual(500000, cap.ceiling_micros)
        self.assertTrue(cap.usd_enforced)
        self.assertEqual(2, cap.ceiling_calls)

    def test_429_stops_all_accounts_and_positive_balance_does_not_clear_it(self):
        self.sdk.failure = HttpFailure(429, "per-minute quota rate limit")
        first = self.call()
        self.assertEqual("RATE_LIMITED", first["provider_health"]["state"])
        balances.record_manual(self.root, provider="typesafe_jev", left=10, unit="USD")
        self.call("op-2", "Who manages the sports facilities?")
        self.assertEqual(1, len(self.sdk.calls))

    def test_402_marks_the_account_and_next_new_request_uses_the_next_account(self):
        self.sdk.failure = HttpFailure(402, "insufficient credits")
        first = self.call()
        self.assertEqual("FAILED", first["status"])
        self.assertEqual(1, len(self.sdk.calls), "no automatic replay of a sent request")
        self.sdk.failure = None
        second = self.call("op-2", "Who operates this sports ground?")
        self.assertEqual("OK", second["status"], second)
        self.assertEqual(self.secondary, second["account_ref"])

    def test_403_does_not_trigger_quota_failover(self):
        self.sdk.failure = HttpFailure(403, "insufficient permissions")
        self.call()
        self.assertIsNone(health.blocked(self.root, "typesafe_jev"))
        self.sdk.failure = None
        second = self.call("op-2", "Who operates this sports ground?")
        self.assertEqual("typesafe_jev", second["account_ref"])

    def test_local_missing_key_does_not_reserve_money_or_poison_the_intent(self):
        health.record(self.root, "typesafe_jev", "QUOTA_EXHAUSTED")
        self.backend.values.clear()
        blocked = self.call()
        self.assertEqual("BLOCKED", blocked["status"])
        self.assertEqual(0, len(usage.for_root(self.root).operations()))
        accounts.configure_key(self.root, provider="typesafe_jev", account_ref=self.secondary,
                               key_id="second", value="second-test-key", confirm=True)
        self.assertEqual("OK", self.call()["status"])

    def test_disabled_network_does_not_claim_the_intent_or_dispatch(self):
        with patch.dict(os.environ, {"MARKETOPS_RESEARCH_ALLOW_NETWORK": "0"}):
            blocked = self.call()
        self.assertEqual("RESEARCH_NETWORK_DISABLED", blocked["reason_code"])
        self.assertEqual([], accounts.query(self.root, "SELECT * FROM intents"))
        self.assertEqual(0, len(self.sdk.calls))
        self.assertEqual("OK", self.call()["status"])

    def test_credentials_are_injected_per_adapter_without_mutating_process_environment(self):
        def build(secret):
            return wiring._construct_adapter(__import__("research_adapter_jev").JevAdapter,
                                             self.root, api_key=secret)._api_key
        with ThreadPoolExecutor(max_workers=2) as pool:
            actual = list(pool.map(build, ("key-a-test", "key-b-test")))
        self.assertEqual(["key-a-test", "key-b-test"], actual)
        self.assertEqual("legacy-test-key", os.environ["TYPESAFE_API_KEY"])
