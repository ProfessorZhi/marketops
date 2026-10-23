"""Real queue/concurrency regressions for the premature Paris handoff."""
import sys
import json
import subprocess
import sqlite3
import threading
import time
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import research_batch as batch
import research_pool as pool
import research_execution as execution
import research_lite_store as store
import test_workflow_pool as pool_tests


class Continuation(unittest.TestCase):
    def setUp(self):
        fixture = pool_tests.BatchHarvestTests("test_batch_harvest_records_outcomes_in_the_pool")
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        self.root, self.fetch = fixture.root, fixture.fetch
        self.fixture = fixture
        pool.import_targets(self.root, pool_id="one", source_id="one-source", source_url="https://x.example/",
                            provider="fixture", listed_count=1, targets=fixture.targets[:1])

    def call(self, **overrides):
        args = dict(pool_id="one", worker_id="worker", run_id="run-w", fetch=self.fetch, count=1)
        args.update(overrides)
        return batch.harvest_batch(self.root, **args)

    def test_time_slice_resumes_same_page_receipts_and_supersedes_old_hold(self):
        pool.import_targets(self.root, pool_id="beta", source_id="beta-source", source_url="https://x.example/",
                            provider="fixture", listed_count=1, targets=self.fixture.targets[1:2])
        operations = []
        def observed(url, operation):
            operations.append((url, operation))
            return self.fetch(url, operation)
        with patch.object(batch, "HARVEST_TIME_BUDGET_SECONDS", -1):
            first = self.call(pool_id="beta", fetch=observed)
        self.assertEqual(1, first["requeued_after_time_budget"])
        self.assertTrue(first["continuation_required"])
        self.assertFalse([r for r in store.list_run(self.root, "run-w", limit=100)["records"] if r["kind"] == "review"])
        # Simulate the old release's automatic HOLD, which must not poison the
        # new quoted route after upgrade/continuation.
        target = pool.list_targets(self.root, pool_id="beta")["targets"][0]
        batch._record_dispositions(self.root, "run-w", [{"organization_key": batch._organization_key(target),
            "outcome": "hold", "reason": "NO_PUBLIC_ROUTE_ON_SITE", "lease_attempt": 1}])
        second = self.call(pool_id="beta", fetch=observed)
        self.assertEqual(1, second["done"])
        self.assertEqual(operations[0], operations[1])
        reviews = [r for r in store.list_run(self.root, "run-w", limit=100)["records"] if r["kind"] == "review"]
        self.assertEqual(2, len(reviews))
        newer = next(r for r in reviews if r["data"]["disposition"] == "RESEARCHED_CANDIDATE")
        older = next(r for r in reviews if r["data"]["disposition"] == "HOLD")
        self.assertEqual(older["record_id"], newer["data"]["supersedes"])

    def test_manual_claim_is_resumed_without_incrementing_attempt_or_stranding_targets(self):
        original = pool.claim(self.root, pool_id="one", worker_id="worker", count=1)
        reply = self.call()
        self.assertEqual(1, reply["resumed_owned"])
        self.assertEqual(1, reply["recorded"])
        target = pool.list_targets(self.root, pool_id="one")["targets"][0]
        self.assertEqual(original["targets"][0]["attempts"], target["attempts"])
        self.assertEqual("done", target["status"])

    def test_running_job_is_not_refetched_and_commits_after_the_tool_returns(self):
        entered, release = threading.Event(), threading.Event()
        calls = []
        def slow(url, operation):
            calls.append(url)
            entered.set()
            self.assertTrue(release.wait(5))
            return self.fetch(url, operation)
        try:
            with patch.object(batch, "wait", return_value=None):
                first = self.call(fetch=slow)
                self.assertTrue(entered.wait(2))
                second = self.call(fetch=slow)
            self.assertEqual(1, first["still_loading"])
            self.assertEqual(0, second["claimed"])
            self.assertEqual("WAIT_FOR_ACTIVE_WORKERS", second["next_action"])
            self.assertTrue(second["continuation_required"])
            self.assertEqual(1, len(calls))
        finally:
            release.set()
        for _ in range(150):
            state = pool.status(self.root, pool_id="one")
            if state["queue_complete"]:
                break
            time.sleep(0.01)
        self.assertTrue(state["queue_complete"])
        self.assertEqual(1, state["by_status"]["done"])
        self.assertEqual(0, state["in_flight"])

    def test_saved_result_recovers_after_finalization_failure_without_fetching_again(self):
        with patch.object(batch, "_record_dispositions", side_effect=OSError("synthetic disk failure")):
            first = self.call()
        self.assertEqual(["OSError"], first["finalization_errors"])
        self.assertEqual("FINALIZE_SAVED_RESULTS", first["next_action"])
        with patch.object(batch.site_harvest, "harvest", side_effect=AssertionError("must not refetch")):
            recovered = self.call(worker_id="recovery-worker")
        self.assertEqual(1, recovered["recovered_saved_results"])
        self.assertTrue(recovered["queue_complete"])

    def test_saved_finalization_survives_a_fresh_python_process(self):
        with patch.object(batch, "_record_dispositions", side_effect=OSError("synthetic interrupted finalization")):
            self.call()
        code = '''import sys,json
sys.path.insert(0,sys.argv[1])
import research_batch
def denied(*args): raise AssertionError("recovery must not fetch")
reply=research_batch.harvest_batch(sys.argv[2],pool_id="one",worker_id="new-process",run_id="run-w",fetch=denied,count=1)
print(json.dumps(reply))
'''
        result = subprocess.run([sys.executable, "-c", code,
            str(Path(__file__).resolve().parents[1] / "scripts"), str(self.root)],
            capture_output=True, text=True, timeout=30, check=True)
        reply = json.loads(result.stdout)
        self.assertEqual(1, reply["recovered_saved_results"], (reply, result.stderr))
        self.assertTrue(reply["queue_complete"], reply)

    def test_other_run_can_finalize_saved_work_without_moving_its_evidence(self):
        with patch.object(batch, "_record_dispositions", side_effect=OSError("interrupted finalization")):
            self.call()
        store.start_run(self.root, "other-run", "same pool", "same project", "v1")
        with patch.object(batch.site_harvest, "harvest", side_effect=AssertionError("no refetch")):
            reply = self.call(worker_id="other-window", run_id="other-run")
        self.assertEqual(1, reply["recovered_saved_results"])
        self.assertTrue(reply["queue_complete"])
        self.assertFalse(store.list_run(self.root, "other-run")["records"])
        self.assertTrue([r for r in store.list_run(self.root, "run-w")["records"] if r["kind"] == "review"])

    def test_new_lease_fences_a_late_outcome_even_when_the_worker_name_is_reused(self):
        first = pool.claim_batch(self.root, pool_id="one", worker_id="worker", run_id="run-w", count=1)
        with closing(pool._db(self.root)) as db:
            db.execute("UPDATE pool_targets SET lease_until='2000-01-01T00:00:00+00:00' WHERE pool_id='one'")
            db.commit()
        state = execution.assess(self.root, "run-w", goal={}, budget={"search_remaining": 135})
        self.assertEqual("CONTINUE_OR_RESUME_TARGET_POOL", state["next_action"])
        second = pool.claim_batch(self.root, pool_id="one", worker_id="worker", run_id="run-w", count=1)
        self.assertGreater(second["targets"][0]["attempts"], first["targets"][0]["attempts"])
        late = pool.complete(self.root, pool_id="one", worker_id="worker", results=[{
            "target_key": first["targets"][0]["target_key"], "lease_attempt": first["targets"][0]["attempts"], "outcome": "done"}])
        self.assertEqual(0, late["accepted"])
        self.assertEqual(0, pool.release(self.root, pool_id="one", worker_id="worker",
            target_keys=[first["targets"][0]["target_key"]],
            lease_attempts={first["targets"][0]["target_key"]: first["targets"][0]["attempts"]}))
        self.assertEqual(1, pool.status(self.root, pool_id="one")["in_flight"])

    def test_active_pool_blocks_final_even_if_record_subset_looks_complete(self):
        pool.claim_batch(self.root, pool_id="one", worker_id="worker", run_id="run-w", count=1)
        reply = execution.assess(self.root, "run-w", goal={"goal_satisfied": True}, budget={"search_remaining": 135})
        self.assertFalse(reply["is_final"])
        self.assertEqual("CHECKPOINT_WAIT_FOR_WORKERS", reply["state"])

    def test_reading_an_old_pool_does_not_create_new_job_tables(self):
        path = self.root / "state/research-pool.sqlite3"
        with closing(sqlite3.connect(path)) as db:
            db.execute("UPDATE pool_targets SET run_id='run-w' WHERE pool_id='one'")
            db.execute("DROP TABLE pool_batch_jobs")
            db.commit()
        reply = execution.assess(self.root, "run-w", goal={"goal_satisfied": True}, budget={"search_remaining": 135})
        self.assertFalse(reply["is_final"])
        with closing(sqlite3.connect(path)) as db:
            self.assertIsNone(db.execute("SELECT 1 FROM sqlite_master WHERE name='pool_batch_jobs'").fetchone())

    def test_drained_shortlist_is_not_a_completed_contacts_goal(self):
        self.call()
        reply = execution.assess(self.root, "run-w", goal={"goal_satisfied": False}, budget={"search_remaining": 135})
        self.assertFalse(reply["is_final"])
        self.assertEqual("FOLLOW_MARKETOPS_NEXT_OR_CHANGE_SOURCE_METHOD", reply["next_action"])

    def test_budget_exhaustion_allows_honest_partial_end_but_not_active_writes(self):
        reply = execution.assess(self.root, "run-w", goal={"goal_satisfied": False}, budget={"search_remaining": 0})
        self.assertTrue(reply["is_final"])
        self.assertFalse(reply["goal_satisfied"])
        pool.claim_batch(self.root, pool_id="one", worker_id="worker", run_id="run-w", count=1)
        self.assertFalse(execution.assess(self.root, "run-w", goal={}, budget={"search_remaining": 0})["is_final"])


if __name__ == "__main__":
    unittest.main()
