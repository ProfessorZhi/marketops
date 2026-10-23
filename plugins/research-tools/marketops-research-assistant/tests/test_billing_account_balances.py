"""Billing identity, once-only balance intake, and conservative usage estimates."""
import json
import os
import sys
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import research_accounts as accounts
import research_balances as balances
import research_costs as costs
import research_provider_health as health
import research_usage_ledger as usage


class Backend:
    def __init__(self):
        self.values = {}

    def set_password(self, service, name, value):
        self.values[service, name] = value

    def get_password(self, service, name):
        return self.values.get((service, name))


class AccountCase(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.backend = Backend()
        backend = patch("research_credentials._backend", return_value=self.backend)
        backend.start(); self.addCleanup(backend.stop)
        env = patch.dict(os.environ, {"TYPESAFE_API_KEY": "legacy-test-key"})
        env.start(); self.addCleanup(env.stop)

    def register(self, provider="typesafe_jev", account_id="primary", **kw):
        return accounts.register(self.root, provider=provider, owner="owner@example.org",
                                 account_id=account_id, confirm=True, **kw)


class IdentityTests(AccountCase):
    def test_status_is_read_only_and_does_not_read_any_secret(self):
        with patch("research_credentials._backend", side_effect=AssertionError("secret read")):
            self.assertEqual(4, len(accounts.status(self.root)["accounts"]))
        self.assertFalse((self.root / "state").exists())

    def test_legacy_refs_preserve_history_and_provider_wallets_stay_separate(self):
        for provider in accounts.PROVIDERS:
            self.assertEqual(provider, self.register(provider)["account_ref"])
        self.assertEqual(4, len(accounts.status(self.root)["accounts"]))
        self.assertEqual({}, self.backend.values)

    def test_serper_cannot_register_a_second_billing_account(self):
        self.register("serper")
        with self.assertRaisesRegex(ValueError, "SERPER_SINGLE_ACCOUNT_ONLY"):
            self.register("serper", "second")

    def test_accounts_require_local_confirmation_and_cannot_change_owner(self):
        with self.assertRaisesRegex(ValueError, "CONFIRMATION"):
            accounts.register(self.root, provider="serper", owner="owner@example.org")
        self.register()
        with self.assertRaisesRegex(ValueError, "OWNER_CHANGE"):
            accounts.register(self.root, provider="typesafe_jev", owner="other@example.org", confirm=True)

    def test_two_keys_share_one_balance_and_activation_does_not_rebase_it(self):
        self.register()
        balances.record_manual(self.root, provider="typesafe_jev", left=20, unit="request")
        before = balances.readings(self.root)
        accounts.configure_key(self.root, provider="typesafe_jev", account_ref="typesafe_jev",
                               key_id="backup", value="backup-test-secret", confirm=True)
        accounts.activate_key(self.root, provider="typesafe_jev", key_id="backup", confirm=True)
        self.assertEqual("backup-test-secret", accounts.credential(self.root, "typesafe_jev", "typesafe_jev"))
        self.assertEqual(before, balances.readings(self.root))
        self.assertNotIn(b"backup-test-secret", accounts.path(self.root).read_bytes())
        self.assertNotIn("backup-test-secret", json.dumps(accounts.status(self.root)))

    def test_same_key_cannot_manufacture_another_account_balance(self):
        self.register()
        ref = self.register(account_id="second", allow_failover=True)["account_ref"]
        with self.assertRaisesRegex(ValueError, "ALREADY_BELONGS"):
            accounts.configure_key(self.root, provider="typesafe_jev", account_ref=ref,
                                   key_id="second", value="legacy-test-key", confirm=True)

    def test_duplicate_named_key_and_key_id_binding_are_rejected(self):
        a = self.register(account_id="second", allow_failover=True)["account_ref"]
        b = self.register(account_id="third", allow_failover=True)["account_ref"]
        accounts.configure_key(self.root, provider="typesafe_jev", account_ref=a,
                               key_id="key-a", value="named-test-secret", confirm=True)
        for key, value in (("key-b", "named-test-secret"), ("key-a", "different-test-secret")):
            with self.assertRaisesRegex(ValueError, "ALREADY_BELONGS"):
                accounts.configure_key(self.root, provider="typesafe_jev", account_ref=b,
                                       key_id=key, value=value, confirm=True)


class IntakeTests(AccountCase):
    def test_only_one_conversation_can_claim_the_initial_question(self):
        self.register()
        with ThreadPoolExecutor(max_workers=6) as pool:
            replies = list(pool.map(lambda _: accounts.claim_balance_question(self.root, "typesafe_jev"),
                                    range(12)))
        self.assertEqual(1, sum(r["ask_now"] for r in replies))
        self.assertFalse(accounts.status(self.root)["accounts"][-1]["ask_balance_once"])

    def test_reported_balance_is_not_asked_again_after_restart(self):
        self.register("serper")
        balances.record_manual(self.root, provider="serper", left=932, unit="credit")
        self.assertFalse(accounts.claim_balance_question(self.root, "serper")["ask_now"])
        self.assertEqual("REPORTED", accounts.status(self.root)["accounts"][0]["balance_state"])

    def test_balances_are_per_account_and_invalid_values_never_clear_health(self):
        self.register()
        ref = self.register(account_id="second", allow_failover=True)["account_ref"]
        balances.record_manual(self.root, provider="typesafe_jev", left=5, unit="USD", account_ref=ref)
        self.assertNotIn("typesafe_jev", balances.readings(self.root))
        for value in (float("nan"), float("inf"), True, -1):
            with self.assertRaises(ValueError):
                balances.record_manual(self.root, provider="typesafe_jev", left=value, unit="USD")

    def test_reporting_a_balance_does_not_lift_a_rate_limit(self):
        health.record(self.root, "typesafe_jev", "RATE_LIMITED")
        balances.record_manual(self.root, provider="typesafe_jev", left=10, unit="USD")
        self.assertEqual("RATE_LIMITED", health.blocked(self.root, "typesafe_jev")["state"])

    def test_quota_uses_the_next_authorised_account_but_rate_limit_does_not(self):
        self.register()
        ref = self.register(account_id="second", allow_failover=True)["account_ref"]
        health.record(self.root, "typesafe_jev", "QUOTA_EXHAUSTED")
        self.assertEqual(ref, accounts.select(self.root, "typesafe_jev")[0]["account_ref"])
        health.record(self.root, "typesafe_jev", "RATE_LIMITED")
        selected, blocked, _ = accounts.select(self.root, "typesafe_jev")
        self.assertEqual("typesafe_jev", selected["account_ref"])
        self.assertEqual("RATE_LIMITED", blocked["state"])

    def test_disabled_or_non_opted_in_accounts_are_never_selected(self):
        self.register()
        self.register(account_id="second", enabled=False, allow_failover=True)
        self.register(account_id="third", allow_failover=False)
        health.record(self.root, "typesafe_jev", "QUOTA_EXHAUSTED")
        self.assertIsNotNone(accounts.select(self.root, "typesafe_jev")[1])


def operation(identifier, *, provider="serper", account_ref=None, state="SETTLED", units=1,
              execution="OK", when="2026-10-21T10:00:00+00:00"):
    return SimpleNamespace(operation_id=identifier, provider=provider, account_ref=account_ref or provider,
        capability="search" if provider == "serper" else "judge", run_id="r", quota_units=units,
        quota_unit="credits" if provider == "serper" else "requests", charge_state=state,
        execution_outcome=execution, counts_against_caps=True, created_at=when,
        settled_at=when if state == "SETTLED" else None, dispatch_intent_at=when,
        actual_micros=None, actual_source=None)


class ConsumptionTests(AccountCase):
    def setUp(self):
        super().setUp()
        usage.for_root(self.root)
        self.rows = []
        fake = SimpleNamespace(operations=lambda **_: tuple(self.rows))
        patched = patch("research_usage_ledger.for_root", return_value=fake)
        patched.start(); self.addCleanup(patched.stop)

    def test_same_second_reading_preserves_old_usage_and_counts_new_operations(self):
        self.rows.append(operation("before", units=3))
        with patch.object(balances, "_now", return_value="2026-10-21T10:00:00+00:00"):
            balances.record_manual(self.root, provider="serper", left=932, unit="credit")
        self.rows.append(operation("after", units=2))
        self.assertEqual(930, costs.balances(self.root, read=False)["serper"]["estimated_left_now"])

    def test_a_new_balance_reading_does_not_release_unknown_exposure(self):
        self.rows.append(operation("unknown", state="UNKNOWN", execution=""))
        balances.record_manual(self.root, provider="serper", left=932, unit="credit")
        entry = costs.balances(self.root, read=False)["serper"]
        self.assertEqual(931, entry["estimated_left_now"])
        self.assertEqual(1, entry["unresolved_charge_operations"])

    def test_other_accounts_and_proven_free_failures_do_not_reduce_this_balance(self):
        balances.record_manual(self.root, provider="serper", left=932, unit="credit")
        self.rows.extend((operation("other", account_ref="other-account"),
                          operation("not-charged", state="NO_CHARGE_PROVEN")))
        self.assertEqual(932, costs.balances(self.root, read=False)["serper"]["estimated_left_now"])

    def test_jev_credit_balance_is_unknown_without_a_credit_conversion(self):
        balances.record_manual(self.root, provider="typesafe_jev", left=5, unit="credit")
        self.rows.append(operation("jev", provider="typesafe_jev"))
        entry = costs.balances(self.root, read=False)["typesafe_jev"]
        self.assertIsNone(entry["estimated_left_now"])
        self.assertEqual(1, entry["consumption_unknown_operations"])

    def test_each_account_can_use_its_own_rate_without_changing_other_wallets(self):
        self.register()
        ref = self.register(account_id="second", allow_failover=True)["account_ref"]
        costs.set_rate(self.root, provider="typesafe_jev", capability="judge",
                       usd_per_unit="0.5", account_ref=ref)
        for account in ("typesafe_jev", ref):
            balances.record_manual(self.root, provider="typesafe_jev", account_ref=account,
                                   left=5, unit="USD")
            self.rows.append(operation(account, provider="typesafe_jev", account_ref=account))
        report = costs.balances(self.root, read=False)
        self.assertEqual("4.500000", report[ref]["estimated_left_now"])
        self.assertEqual("4.999874", report["typesafe_jev"]["estimated_left_now"])

    def test_an_unknown_usd_rate_is_never_subtracted_as_zero(self):
        balances.record_manual(self.root, provider="tinyfish", left=1, unit="USD")
        row = operation("agent", provider="tinyfish")
        row.capability = "agent_run"
        self.rows.append(row)
        self.assertIsNone(costs.balances(self.root, read=False)["tinyfish"]["estimated_left_now"])

    def test_a_cached_legacy_reading_is_preserved(self):
        legacy = {"serper": {"left": 932, "unit": "credit", "observed_at": "2026-10-21T09:00:00+00:00"}}
        balances._path(self.root).write_text(json.dumps(legacy))
        self.rows.append(operation("new", units=2))
        self.assertEqual(930, costs.balances(self.root, read=False)["serper"]["estimated_left_now"])


if __name__ == "__main__":
    unittest.main()
