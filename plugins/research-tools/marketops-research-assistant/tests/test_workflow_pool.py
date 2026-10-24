"""RC6: the shared pool many windows work from, and batch harvesting from it.

Several conversations point the plugin at one data root. These controls pin
what Member A's threads lacked: no two windows claim the same target, a crashed
window's lease expires, every source reconciles listed vs imported, and a batch
harvest records each outcome back into the pool.
"""
from __future__ import annotations

import multiprocessing
from contextlib import closing
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import research_batch as batch  # noqa: E402
import research_lite_store as store  # noqa: E402
import research_pool as pool  # noqa: E402

HALLS = [{"name": f"Hall Number {i} Village Hall", "address": f"{i} High St, Chelmsford CM{i % 9 + 1} 1AA",
          "website": f"https://hall{i}.example/"} for i in range(40)]


def _claim_worker(args):
    root, worker = args
    sys.path.insert(0, str(ROOT / "scripts"))
    import research_pool as p
    keys = []
    while True:
        got = p.claim(root, pool_id="uk", worker_id=worker, count=3)
        if not got["claimed"]:
            return keys
        keys += [t["target_key"] for t in got["targets"]]


class PoolTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name) / "data"

    def load(self, targets=HALLS, listed=None, source="maps-1"):
        return pool.import_targets(self.root, pool_id="uk", source_id=source,
                                   source_url="https://maps.example/q", provider="serpapi",
                                   listed_count=len(targets) if listed is None else listed,
                                   targets=targets, objective="UK halls")

    def test_import_dedupes_on_name_and_postcode_not_domain(self):
        shared = [{"name": "Alpha Hall", "address": "Alpha Rd, Chelmsford CM1 2AB", "website": "https://trust.example/"},
                  {"name": "Beta Hall", "address": "Beta Rd, Chelmsford CM2 9ZZ", "website": "https://trust.example/"},
                  {"name": "The Alpha Hall", "address": "CM1 2AB", "website": ""},
                  {"name": "", "address": "nowhere"}]
        result = self.load(shared)
        self.assertEqual((2, 1, 1), (result["imported"], result["duplicates"], len(result["rejected"])))
        self.assertTrue(result["reconciled"])

    def test_a_pool_target_is_known_to_be_a_target(self):
        self.load(HALLS[:2])
        import sqlite3  # noqa: PLC0415 - only this assertion reads sqlite directly
        with closing(sqlite3.connect(
                (self.root / "state" / "research-pool.sqlite3").as_uri() + "?mode=ro",
                uri=True)) as db:
            key = db.execute("SELECT target_key FROM pool_targets LIMIT 1").fetchone()[0]
        self.assertTrue(pool.is_pool_target(self.root, key))
        self.assertFalse(pool.is_pool_target(self.root, "t-00000000000000000000"))
        # A data root with no pool at all answers False rather than raising.
        self.assertFalse(pool.is_pool_target(Path(tempfile.mkdtemp()) / "empty",
                                             "t-00000000000000000000"))

    def test_unaccounted_rows_are_reported(self):
        result = self.load(HALLS[:5], listed=7)
        self.assertFalse(result["reconciled"])
        self.assertEqual(2, result["unaccounted"])
        self.assertFalse(pool.status(self.root, pool_id="uk")["sources"][0]["reconciled"])

    def test_two_processes_never_claim_the_same_target(self):
        self.load()
        with multiprocessing.get_context("spawn").Pool(2) as workers:
            a, b = workers.map(_claim_worker, [(str(self.root), "win-a"), (str(self.root), "win-b")])
        self.assertEqual(40, len(a) + len(b))
        self.assertEqual(set(), set(a) & set(b))
        self.assertEqual(40, len(set(a) | set(b)))

    def test_expired_lease_is_reclaimable_and_stale_completion_refused(self):
        self.load(HALLS[:2])
        first = pool.claim(self.root, pool_id="uk", worker_id="crashed", count=2, lease_minutes=1)
        with closing(pool._db(self.root)) as db:
            db.execute("UPDATE pool_targets SET lease_until='2000-01-01T00:00:00+00:00'")
            db.commit()
        second = pool.claim(self.root, pool_id="uk", worker_id="rescuer", count=2)
        self.assertEqual(2, second["claimed"])
        key = first["targets"][0]["target_key"]
        late = pool.complete(self.root, pool_id="uk", worker_id="crashed",
                             results=[{"target_key": key, "outcome": "done"}])
        self.assertEqual("NOT_HELD_BY_THIS_WORKER", late["refused"][0]["reason"])

    def test_hold_needs_a_reason_and_status_counts_everything(self):
        self.load(HALLS[:3])
        got = pool.claim(self.root, pool_id="uk", worker_id="w", count=3)
        keys = [t["target_key"] for t in got["targets"]]
        refused = pool.complete(self.root, pool_id="uk", worker_id="w",
                                results=[{"target_key": keys[0], "outcome": "hold"}])
        self.assertTrue(refused["refused"][0]["reason"].startswith("REASON_REQUIRED"))
        pool.complete(self.root, pool_id="uk", worker_id="w", results=[
            {"target_key": keys[0], "outcome": "done", "routes": 2},
            {"target_key": keys[1], "outcome": "hold", "reason": "SITE_UNREACHABLE"}])
        state = pool.status(self.root, pool_id="uk")
        self.assertEqual({"pending": 0, "claimed": 1, "done": 1, "hold": 1},
                         {k: state["by_status"][k] for k in ("pending", "claimed", "done", "hold")})
        self.assertEqual(0.5, state["hit_rate"])  # 1 of 2 researched targets
        self.assertEqual({"w": 1}, state["active_workers"])


class BatchHarvestTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name) / "data"
        store.start_run(self.root, "run-w", "halls", "UK halls", "v1")
        self.targets = [
            {"name": "Alpha Village Hall", "address": "CM1 2AB", "website": "https://alphahall.example/"},
            {"name": "Beta Village Hall", "address": "CM2 3CD", "website": "https://betahall.example/"},
            {"name": "Gamma Village Hall", "address": "CM3 4EF", "website": ""},
            {"name": "Delta Village Hall", "address": "CM4 5GH", "website": "https://blocked.example/"}]
        pool.import_targets(self.root, pool_id="uk", source_id="s1", source_url="https://x.example",
                            provider="serpapi", listed_count=4, targets=self.targets)
        self.web = {
            "https://alphahall.example/": "<p>Alpha Village Hall bookings: hire@alphahall.example or 01245 111222.</p>",
            "https://betahall.example/": "<p>Welcome to Beta Village Hall.</p><a href='/contact'>Contact</a>",
            "https://betahall.example/contact": "<p>Call the Beta Village Hall secretary on 01245 333444.</p>",
        }

    def fetch(self, url, operation_id):
        if url not in self.web:
            return {"status": "HOLD", "reason_code": "SOURCE_ACCESS_BLOCKED"}
        artifact = "art-" + operation_id
        store.save_artifact(self.root, "run-w", artifact, "crawl4ai", url,
                            self.web[url].encode(), "rendered_html")
        return {"status": "SUCCESS_CANDIDATE", "artifact_ids": [artifact],
                "primary_artifact_ids": [artifact]}

    def test_batch_harvest_records_outcomes_in_the_pool(self):
        result = batch.harvest_batch(self.root, pool_id="uk", worker_id="w1", run_id="run-w",
                                     fetch=self.fetch, count=10, concurrency=3)
        self.assertEqual((4, 2, 2), (result["claimed"], result["done"], result["hold"]))
        self.assertEqual(3, result["routes"])
        self.assertEqual({"NO_OFFICIAL_SITE_IN_POOL": 1, "SITE_UNREACHABLE": 1}, result["hold_reasons"])
        state = pool.status(self.root, pool_id="uk")
        self.assertEqual(2, state["by_status"]["done"])
        self.assertEqual(0, state["by_status"]["claimed"])
        again = batch.harvest_batch(self.root, pool_id="uk", worker_id="w1", run_id="run-w",
                                    fetch=self.fetch, count=10)
        self.assertEqual(0, again["claimed"])
        self.assertEqual("POOL_DRAINED_REVIEW_GOAL_AND_COVERAGE", again["next_action"])
        self.assertFalse(again["research_complete"])

    def test_each_saved_organisation_gets_a_disposition_in_the_run(self):
        import research_lite_store as store
        batch.harvest_batch(self.root, pool_id="uk", worker_id="w1", run_id="run-w",
                            fetch=self.fetch, count=10)
        rows = store.list_run(self.root, "run-w", limit=200)
        rows = rows.get("records", rows) if isinstance(rows, dict) else rows
        orgs = {r["entity_key"] for r in rows if r["kind"] == "organization"}
        decided = {r["data"]["target_entity_key"]: r["data"]["disposition"]
                   for r in rows if r["kind"] == "review"}
        self.assertTrue(orgs)
        self.assertEqual(orgs, set(decided))
        self.assertTrue(set(decided.values()) <= {"RESEARCHED_CANDIDATE", "HOLD"})

    def test_a_slow_batch_returns_before_the_host_timeout_and_hands_back_the_rest(self):
        # Live run: Claude Code ends a tool call at 60 s; the batch kept its leases.
        import time

        def slow(url, op):
            time.sleep(0.6)
            return self.fetch(url, op)
        started = time.monotonic()
        result = batch.harvest_batch(self.root, pool_id="uk", worker_id="w1", run_id="run-w",
                                     fetch=slow, count=10, concurrency=1, deadline_seconds=0.3)
        self.assertLess(time.monotonic() - started, 8)
        self.assertGreater(result["returned_unstarted"], 0)
        self.assertGreater(pool.status(self.root, pool_id="uk")["by_status"]["pending"], 0)

    def test_named_roles_tag_each_email_with_a_jev_role_guess(self):
        asked = []

        def grade(route):
            asked.append(route["value"])
            return {"role": "wanted_role_person", "confidence": 0.93}
        result = batch.harvest_batch(self.root, pool_id="uk", worker_id="w1", run_id="run-w",
                                     fetch=self.fetch, count=10, grade=grade)
        lines = [line for target in result["targets"] for line in target["routes"]
                 if line.startswith("email:")]
        self.assertTrue(asked)
        self.assertTrue(all(line.endswith("[wanted_role_person 0.93]") for line in lines))


if __name__ == "__main__":
    unittest.main()
