"""Advertisement is not the same as "everything we can serve".

An advertisement is a permission to invoke: the gateway routes what
`capabilities()` returns. So the default advertisement is exactly the set B's
registry reviews **and enables**, and the two states it does not enable are kept
visible without being routable:

* REVIEWED          -> advertised, routable
* REVIEWED_DISABLED -> not advertised, not routable, and **not** "unreviewed"
* UNREVIEWED        -> never advertised, never routable
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import research_adapter_tinyfish as tinyfish

#: The reviewed-and-enabled set per `research_capability_registry.py` @ 672447de.
REVIEWED = (
    "tinyfish.search", "tinyfish.fetch_content",
    "tinyfish.get_run", "tinyfish.list_runs", "tinyfish.batch_status",
    "tinyfish.get_wallet", "tinyfish.get_search_usage", "tinyfish.list_fetch_usage",
    "tinyfish.get_monitor", "tinyfish.list_monitors", "tinyfish.list_browser_sessions",
    "tinyfish.run_web_automation",
)
DISABLED = ("tinyfish.cancel_run", "tinyfish.batch_cancel")


class DefaultAdvertisement(unittest.TestCase):
    def test_the_default_advertisement_is_the_reviewed_enabled_subset(self):
        self.assertEqual(set(REVIEWED), set(tinyfish.capabilities()))

    def test_the_disabled_pair_is_reviewed_but_not_advertised(self):
        self.assertEqual(set(DISABLED), set(tinyfish.disabled_capabilities()))
        for capability_id in DISABLED:
            self.assertNotIn(capability_id, tinyfish.capabilities())

    def test_nothing_is_currently_unreviewed(self):
        # Every capability this adapter serves is in B's registry. The state
        # still exists for the case where one is added ahead of review.
        self.assertEqual((), tinyfish.unreviewed_capabilities())

    def test_every_served_capability_declares_one_of_the_three_states(self):
        for capability_id in tinyfish.capabilities(include_unreviewed=True):
            status = tinyfish.capability_provenance(capability_id)["review_status"]
            self.assertIn(status, {"REVIEWED", "REVIEWED_DISABLED", "UNREVIEWED"},
                          capability_id)

    def test_review_status_agrees_with_advertisement(self):
        for capability_id in tinyfish.capabilities(include_unreviewed=True):
            status = tinyfish.capability_provenance(capability_id)["review_status"]
            self.assertEqual(status == "REVIEWED",
                             capability_id in tinyfish.capabilities(), capability_id)

    def test_the_reviewed_set_is_declared_not_inferred(self):
        self.assertEqual(set(REVIEWED), set(tinyfish.REVIEWED_CAPABILITIES))
        self.assertEqual(set(DISABLED), set(tinyfish.DISABLED_REVIEWED_CAPABILITIES))


class CatalogKeepsEveryStateVisible(unittest.TestCase):
    def test_a_disabled_capability_is_labelled_not_hidden(self):
        entry = tinyfish.capability_catalog()["tools"]["cancel_run"]
        self.assertEqual("REVIEWED_DISABLED", entry["review_status"])
        self.assertEqual("DISABLED", entry["availability"])
        self.assertFalse(entry["enabled"])
        self.assertTrue(entry["deferred_reason"])

    def test_the_digest_lists_disabled_and_unreviewed_separately(self):
        digest = tinyfish.catalog_digest()
        self.assertEqual(sorted(REVIEWED), digest["capabilities"])
        self.assertEqual(sorted(DISABLED), digest["disabled_capabilities"])
        self.assertEqual([], digest["unreviewed_capabilities"])
        self.assertNotIn("tinyfish.cancel_run", digest["capabilities"])


if __name__ == "__main__":
    unittest.main()
