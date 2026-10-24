"""Offline money/long-run/bulk regressions from the Paris field session."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import research_bulk as bulk
import research_balances as balances
import research_contacts as contacts
import research_evidence as evidence
import research_lite_store as store
import research_pool as pool
import research_runtime_setup as runtime
import research_spending as spending
import research_usage_ledger as usage


class Money(unittest.TestCase):
    def setUp(self):
        environment = patch.dict(os.environ, {"MARKETOPS_USAGE_PRICING": ""})
        environment.start()
        self.addCleanup(environment.stop)
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        runtime.configure(self.root, providers=["crawl4ai", "serper", "serpapi", "tinyfish", "typesafe_jev"],
                          domains=["example.org"], max_calls=1, task="Synthetic long research", confirm=True)
        # The frozen-share model these cases were written for stays available.
        spending.configure(self.root, confirm=True, fraction="0.30", model="ENVELOPE", observations={"serper": {
            "left": 10, "unit": "credit", "observed_at": (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat(),
            "evidence": "SYNTHETIC_TEST_READING"}})
        self.project = usage.project_for_root(self.root)

    def request(self, index, provider="serper", run="run-a"):
        return usage.OperationRequest(project=self.project, run_id=run, operation_id=f"op-{index}",
            provider=provider, capability="render" if provider == "crawl4ai" else "search",
            account_ref=provider, transport="local_python" if provider == "crawl4ai" else "rest_https",
            intent=f"intent-{index}", args={"query": str(index)})

    def ledger(self, run="run-a"):
        # Other legacy suites install a process-wide credit-only test shim;
        # these cases explicitly exercise the reviewed monetary catalogue.
        ledger = usage.for_root(self.root, pricing=usage.PricingCatalog.from_entries(spending.pricing_entries()))
        runtime.state_ledger_caps(self.root, run, ledger=ledger)
        return ledger

    def test_free_pages_outlive_old_request_cap_and_keep_audit_history(self):
        ledger = self.ledger()
        for n in range(20):
            ledger.reserve(self.request(n, "crawl4ai"))
        self.assertEqual(20, len(ledger.operations(project=self.project)))
        self.assertEqual("0.003", spending.status(self.root)["remaining_usd"])
        self.assertIsNone(runtime.cap_plan(runtime.config(self.root))["project_max_calls"])

    def test_concurrent_paid_reservations_cannot_spend_credit_reserve(self):
        ledger = self.ledger()
        def reserve(index):
            try:
                ledger.reserve(self.request(index))
                return True
            except usage.LedgerRefused:
                return False
        with ThreadPoolExecutor(max_workers=8) as executor:
            results = list(executor.map(reserve, range(12)))
        self.assertEqual(3, sum(results))
        self.assertEqual("0", spending.status(self.root)["remaining_usd"])
        # Free reading remains possible after the monetary allowance is gone.
        ledger.reserve(self.request(99, "crawl4ai"))

    def test_restart_and_new_run_do_not_renew_an_envelope(self):
        ledger = self.ledger()
        for n in range(3):
            ledger.reserve(self.request(n))
        original = spending.policy(self.root)
        spending.configure(self.root, confirm=True, fraction="0.30", model="ENVELOPE", observations={})
        self.assertEqual(original, spending.policy(self.root))
        other = self.ledger("run-b")
        with self.assertRaises(usage.LedgerRefused):
            other.reserve(self.request(4, run="run-b"))

    def test_unknown_balance_blocks_paid_judgment_but_not_free_fetch(self):
        ledger = self.ledger()
        request = usage.OperationRequest(project=self.project, run_id="run-a", operation_id="judge",
            provider="typesafe_jev", capability="judge", account_ref="typesafe_jev",
            transport="python_sdk", intent="unknown-balance", args={})
        with self.assertRaises(usage.LedgerRefused):
            ledger.reserve(request)

    def test_an_old_fixed_share_policy_becomes_red_lines_when_the_user_asks(self):
        ledger = self.ledger()
        done = 0
        for n in range(10):
            try:
                ledger.reserve(self.request(n))
                done += 1
            except usage.LedgerRefused:
                break
        self.assertEqual(3, done)  # 30% of 10 credits
        spending.set_limit(self.root, provider="serper", limit="9", user_words="可以多用 serper")
        self.assertEqual("RED_LINE", spending.policy(self.root)["model"])
        more = 0
        for n in range(100, 120):
            try:
                self.ledger().reserve(self.request(n))
                more += 1
            except usage.LedgerRefused:
                break
        self.assertEqual(6, more)  # limit 9 in total; the 10% red line (1 credit) still holds

    def test_agent_can_lower_but_never_increase_or_refill_budget(self):
        ledger = self.ledger()
        ledger.reserve(self.request(1))
        spending.tighten(self.root, amount_usd="0.002", provider="serper")
        with self.assertRaisesRegex(ValueError, "BUDGET_INCREASE"):
            spending.tighten(self.root, amount_usd="0.003", provider="serper")
        with self.assertRaisesRegex(ValueError, "CAP_BELOW_HELD"):
            spending.tighten(self.root, amount_usd="0", provider="serper")

    def test_new_lower_balance_cannot_move_the_reserve_down(self):
        balances.record_manual(self.root, provider="serper", left=6, unit="credit")
        with self.assertRaises(usage.LedgerRefused):
            self.ledger().reserve(self.request(1))

    def test_money_mode_uses_its_current_balance_instead_of_stale_legacy_baseline(self):
        runtime._write(self.root / "state/serper-account.json", {
            "observed_at": (datetime.now(timezone.utc) - timedelta(days=3)).isoformat(), "units": 10})
        with patch.object(runtime, "_credential_present", return_value=True):
            self.assertTrue(runtime.admission(self.root, "serper.search")["allowed"])


class RedLine(Money):
    """Spend down to a red line on the freshest balance; the user can move it."""

    def setUp(self):
        environment = patch.dict(os.environ, {"MARKETOPS_USAGE_PRICING": ""})
        environment.start()
        self.addCleanup(environment.stop)
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        runtime.configure(self.root, providers=["crawl4ai", "serper", "serpapi", "tinyfish", "typesafe_jev"],
                          domains=["example.org"], max_calls=1, task="Synthetic long research", confirm=True)
        now = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()
        spending.configure(self.root, confirm=True, observations={
            "serpapi": {"left": 63, "unit": "search", "observed_at": now, "evidence": "SYNTHETIC"},
            "serper": {"left": 10, "unit": "credit", "observed_at": now, "evidence": "SYNTHETIC"}})
        self.project = usage.project_for_root(self.root)

    def spend(self, provider, count, start=0):
        ledger, done = self.ledger(), 0
        for n in range(start, start + count):
            try:
                ledger.reserve(self.request(n, provider))
                done += 1
            except usage.LedgerRefused:
                break
        return done

    def test_default_keeps_ten_percent_instead_of_allowing_thirty(self):
        # 63 searches: the old 30% share stopped at 19; the red line keeps 10% (6.3).
        self.assertEqual(56, self.spend("serpapi", 70))
        state = spending.status(self.root)["providers"]["serpapi"]
        self.assertEqual("AT_RED_LINE", state["state"])

    def test_the_user_can_move_the_red_line_with_their_words(self):
        self.spend("serpapi", 70)
        with self.assertRaisesRegex(ValueError, "USER_WORDS_REQUIRED"):
            spending.move_red_line(self.root, provider="serpapi", keep="0")
        spending.move_red_line(self.root, provider="serpapi", keep="0",
                               user_words="SerpApi 有余额，允许继续使用")
        self.assertEqual(7, self.spend("serpapi", 10, start=100))
        history = (self.root / "state/money-policy-history.jsonl").read_text(encoding="utf-8")
        self.assertIn("允许继续使用", history)
        # Raising it back needs no words.
        spending.move_red_line(self.root, provider="serpapi", keep="5")

    def test_a_fresh_balance_reading_is_what_counts(self):
        self.spend("serper", 9)
        balances.record_manual(self.root, provider="serper", left=500, unit="credit")
        self.assertGreater(self.spend("serper", 5, start=200), 0)

    def test_a_limit_can_be_set_lowered_and_raised_with_the_users_words(self):
        spending.set_limit(self.root, provider="serpapi", limit="10")
        self.assertEqual(10, self.spend("serpapi", 20))
        with self.assertRaisesRegex(ValueError, "USER_WORDS_REQUIRED"):
            spending.set_limit(self.root, provider="serpapi", limit="30")
        spending.set_limit(self.root, provider="serpapi", limit="30",
                           user_words="本 run 我允许多配置额度，继续完成")
        self.assertEqual(20, self.spend("serpapi", 40, start=100))
        with self.assertRaisesRegex(ValueError, "LIMIT_BELOW_USED"):
            spending.set_limit(self.root, provider="serpapi", limit="5")
        state = spending.status(self.root)["providers"]["serpapi"]
        self.assertEqual("30", state["limit"])

    def test_an_account_with_no_balance_api_runs_under_a_limit(self):
        # Jev reports no balance: without a limit it cannot spend at all.
        def judge(count, start):
            ledger, done = self.ledger(), 0
            for n in range(start, start + count):
                try:
                    ledger.reserve(usage.OperationRequest(
                        project=self.project, run_id="run-a", operation_id=f"jev-{n}",
                        provider="typesafe_jev", capability="judge", account_ref="typesafe_jev",
                        transport="python_sdk", intent=f"judge-{n}", args={"q": str(n)}))
                    done += 1
                except usage.LedgerRefused:
                    break
            return done
        self.assertEqual(0, judge(3, 0))
        with self.assertRaisesRegex(ValueError, "USER_WORDS_REQUIRED"):
            spending.set_limit(self.root, provider="jev", limit="4")
        spending.set_limit(self.root, provider="jev", limit="4", user_words="你自己注意下额度")
        self.assertEqual(4, judge(10, 300))
        state = spending.status(self.root)["providers"]["typesafe_jev"]
        self.assertEqual(("4", "AT_LIMIT"), (state["limit"], state["state"]))

    # The ENVELOPE-only cases of the parent class do not apply to the red line.
    test_free_pages_outlive_old_request_cap_and_keep_audit_history = None
    test_concurrent_paid_reservations_cannot_spend_credit_reserve = None
    test_restart_and_new_run_do_not_renew_an_envelope = None
    test_agent_can_lower_but_never_increase_or_refill_budget = None
    test_an_old_fixed_share_policy_becomes_red_lines_when_the_user_asks = None
    test_new_lower_balance_cannot_move_the_reserve_down = None
    test_money_mode_uses_its_current_balance_instead_of_stale_legacy_baseline = None


class Bulk(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        store.start_run(self.root, "run-a", "Synthetic indoor facilities", "Build a facility pool", "test")
        self.rows = [{"id": n, "name": f"Centre {n}", "nature": "Interior" if n % 3 else "Outdoor",
                      "city": "Synthetic City"} for n in range(300)]
        body = json.dumps({"total_count": 300, "results": self.rows})
        store.save_artifact(self.root, "run-a", "bulk-json", "crawl4ai", "https://example.org/data",
                            ("<html><pre>" + body + "</pre></html>").encode(), "rendered_html")

    def call(self, **overrides):
        args = dict(run_id="run-a", artifact_id="bulk-json", pool_id="facilities", source_id="source-a",
            mapping={"name": "name", "city": "city", "source_key": "id", "indoor": "nature"},
            filters={"nature": ["Interior"]}, limit=100)
        args.update(overrides)
        return bulk.import_saved(self.root, **args)

    def test_bulk_import_filters_and_pages_locally_with_count_reconciliation(self):
        first = self.call()
        self.assertEqual(100, first["next_offset"])
        self.assertEqual(0, first["network_requests"])
        self.assertTrue(first["reconciled"])
        again = self.call()
        self.assertTrue(again["replayed"])
        self.call(offset=100)
        final = self.call(offset=200)
        self.assertIsNone(final["next_offset"])
        status = pool.status(self.root, pool_id="facilities")
        self.assertEqual(200, status["total"])
        self.assertTrue(all(s["reconciled"] for s in status["sources"]))
        with closing(store._connection(self.root)) as db:
            self.assertEqual(200, db.execute("SELECT COUNT(*) FROM records WHERE kind='venue'").fetchone()[0])

    def test_wrong_filter_field_and_tampered_artifact_fail_without_pool(self):
        with self.assertRaisesRegex(ValueError, "BULK_FILTER_FIELD_MISSING"):
            self.call(filters={"not_a_field": ["Interior"]})
        self.assertFalse((self.root / "state/research-pool.sqlite3").exists())

    def test_final_upstream_page_finishes_and_importing_another_pool_is_not_a_replay(self):
        self.call()
        second = self.call(pool_id="another-pool")
        self.assertFalse(second["replayed"])
        self.assertGreater(pool.status(self.root, pool_id="another-pool")["total"], 0)
        store.save_artifact(self.root, "run-a", "last-page", "crawl4ai",
            "https://example.org/data?offset=200&limit=100",
            json.dumps({"total_count": 300, "results": self.rows[200:]}).encode(), "raw_response")
        last = self.call(artifact_id="last-page", preview=True)
        self.assertFalse(last["upstream_pagination_remaining"])
        self.assertIsNone(last["upstream_next_offset"])


class ContactEvidence(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        store.start_run(self.root, "run-a", "Synthetic contacts", "Find distinct decision roles", "test")
        self.quote = "Marie Test, Responsable Partenariats Sample Sports, marie.test@example.org"
        store.save_artifact(self.root, "run-a", "official", "crawl4ai", "https://example.org/team",
                            ("<p>" + self.quote + "</p>").encode(), "rendered_html")
        store.put_candidate(self.root, "run-a", "brief", "qualification", "observation", "brief",
            {"record_type": "research_brief", "target_kind": "organization", "goal": "contacts",
             "minimum_targets": None, "require_named_person": True,
             "role_groups_wanted": ["management", "operations", "commercial_events"],
             "evidence_requirements": {"contact_point": ["name", "role", "email"]}}, [])
        self.items = [
            ("organization", "org", {"name": "Sample Sports"}),
            ("person", "person", {"name": "Marie Test", "organization_id": "org"}),
            ("role", "role", {"person_id": "person", "organization_id": "org", "role_title": "Responsable Partenariats"}),
            ("contact_point", "email", {"person_id": "person", "organization_id": "org",
                "type": "email", "value": "marie.test@example.org"})]
        for kind, key, facts in self.items:
            field = "email" if kind == "contact_point" else "role" if kind == "role" else "identity"
            store.put_candidate(self.root, "run-a", key, "people" if kind != "organization" else "entities",
                kind, key, {**facts, "claims": [{"field": field, "artifact_id": "official", "quote": self.quote}]}, ["official"])

    def test_same_primary_quote_proves_linked_name_role_email(self):
        records = store.list_run(self.root, "run-a", limit=100)["records"]
        artifact = {"official": {"kind": "rendered_html", "text": "<p>" + self.quote + "</p>"}}
        audit = evidence.audit_records(records, artifact)
        self.assertFalse([s for s in audit["samples"] if s["record_id"] == "email"], audit)

    def test_one_contact_does_not_close_uncovered_decision_roles(self):
        result = contacts.coverage(self.root, "run-a")
        self.assertIsNone(result["fixed_people_quota"])
        self.assertEqual(["management", "operations"], result["targets"][0]["missing_role_groups"])
        import research_workflow
        store.put_candidate(self.root, "run-a", "review-org", "review", "review", "review-org",
            {"review_type": "target_disposition", "target_kind": "organization", "target_entity_key": "org",
             "disposition": "RESEARCHED_CANDIDATE", "reason": "One published commercial role saved"}, [])
        progress = research_workflow.next_step(self.root, "run-a")
        self.assertFalse(progress["goal_satisfied"])
        self.assertIn("decision_role_group_management", progress["gap_counts"])

    def test_an_email_only_quote_does_not_invent_name_or_role_evidence(self):
        records = store.list_run(self.root, "run-a", limit=100)["records"]
        row = next(r for r in records if r["record_id"] == "email")
        row["data"]["claims"][0]["quote"] = "marie.test@example.org"
        artifact = {"official": {"kind": "rendered_html", "text": "<p>" + self.quote + "</p>"}}
        audit = evidence.audit_records(records, artifact)
        result = next(s for s in audit["samples"] if s["record_id"] == "email")
        self.assertIn("MISSING_REQUIRED_NAME", result["reason_codes"])
        self.assertIn("MISSING_REQUIRED_ROLE", result["reason_codes"])


class BalanceShape(unittest.TestCase):
    def test_unrecognized_read_does_not_erase_last_valid_wallet_balance(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            balances.record_manual(root, provider="tinyfish", left=1, unit="USD")
            with patch.object(balances, "_enabled", return_value=""), \
                 patch.object(balances, "read_serpapi", return_value={"status": "READ_FAILED"}), \
                 patch.object(balances, "read_tinyfish", return_value={"status": "READ_UNRECOGNISED", "left": None}):
                balances.read_all(root)
            self.assertEqual(1, balances.readings(root)["tinyfish"]["left"])


class AdvisoryBudget(unittest.TestCase):
    """The next-action budget must not report a funded run as exhausted."""

    def test_red_line_policy_keeps_the_managed_budget(self):
        from unittest.mock import patch
        import research_config
        import research_mcp as mcp

        money = {"budget_mode": "MONEY", "model": "RED_LINE", "configured": True,
                 "request_limit_active": False, "total_red_line_usd": "500",
                 "spent_since_policy_usd": "0.113889", "providers": {}}
        snapshot = {"remaining": None, "unknown_charge_operations": 4, "reserved_operations": 0,
                    "ambiguous_outcome_operations": 3}
        with patch.object(research_config, "data_root", return_value=Path("/nonexistent")), \
             patch.object(runtime, "config", return_value={"max_calls": 200,
                                                           "spending": {"mode": "MONEY"}}), \
             patch.object(spending, "status", return_value=money), \
             patch("research_runtime_status.usage", return_value=snapshot):
            budget = mcp._source_selection_budget("run-a")
        self.assertEqual("RUNTIME_CONFIG", budget["source"])
        self.assertEqual("MONEY", budget["budget_mode"])
        self.assertEqual("499.886111", budget["remaining_usd"])
        # A money policy states no request ceiling; that is unknown, not zero.
        self.assertIsNone(budget["search_remaining"])
        self.assertIsNone(budget["provider_remaining"])
        self.assertEqual(3, budget["unknown_held"])
        self.assertEqual(1, budget["unbilled_successes"])

    def test_an_environment_that_states_no_ceiling_is_unknown_not_zero(self):
        from unittest.mock import patch
        import research_config
        import research_mcp as mcp

        with patch.object(research_config, "data_root", side_effect=ValueError("CONFIG_REQUIRED")), \
             patch.dict(os.environ, {}, clear=False):
            os.environ.pop("MARKETOPS_SEARCH_MAX_CALLS", None)
            os.environ.pop("MARKETOPS_RESEARCH_MAX_EXTERNAL_CALLS", None)
            budget = mcp._source_selection_budget("run-a")
        self.assertEqual("ENV", budget["source"])
        self.assertIsNone(budget["search_remaining"])
        self.assertIsNone(budget["provider_remaining"])

    def test_a_stated_environment_ceiling_still_counts_down(self):
        from unittest.mock import patch
        import research_config
        import research_mcp as mcp

        with patch.object(research_config, "data_root", side_effect=ValueError("CONFIG_REQUIRED")), \
             patch.dict(os.environ, {"MARKETOPS_SEARCH_MAX_CALLS": "5",
                                     "MARKETOPS_RESEARCH_MAX_EXTERNAL_CALLS": "9"}), \
             patch.object(mcp, "_ledger_usage", return_value=2):
            budget = mcp._source_selection_budget("run-a")
        self.assertEqual(3, budget["search_remaining"])
        self.assertEqual(7, budget["provider_remaining"])


if __name__ == "__main__":
    unittest.main()
