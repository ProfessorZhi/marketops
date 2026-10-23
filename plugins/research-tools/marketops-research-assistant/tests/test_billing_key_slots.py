"""Second key slot for Serper/SerpApi: switched on the user's word only."""
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import research_key_slots as slots  # noqa: E402


class KeySlots(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.env = {"SERPER_API_KEY": "first-serper", "SERPER_API_KEY_2": "second-serper",
                    "SERPAPI_API_KEY": "first-serpapi"}

    def test_slot_one_until_the_user_switches_and_back_again(self):
        self.assertEqual({"serper": 1, "serpapi": 1}, slots.apply(self.root, self.env))
        self.assertEqual("first-serper", self.env["SERPER_API_KEY"])
        answer = slots.use(self.root, "serper", 2, self.env)
        self.assertEqual("second-serper", self.env["SERPER_API_KEY"])
        self.assertNotIn("second-serper", repr(answer))
        # A restarted server keeps the user's choice.
        fresh = {k: v for k, v in self.env.items() if not k.startswith("_")}
        fresh["SERPER_API_KEY"] = "first-serper"
        self.assertEqual(2, slots.apply(self.root, fresh)["serper"])
        self.assertEqual("second-serper", fresh["SERPER_API_KEY"])
        slots.use(self.root, "serper", 1, self.env)
        self.assertEqual("first-serper", self.env["SERPER_API_KEY"])

    def test_an_empty_slot_is_refused_and_out_of_credit_is_cleared_on_switch(self):
        import research_provider_health as health
        with self.assertRaises(ValueError):
            slots.use(self.root, "serpapi", 2, self.env)
        health.record(self.root, "serper", "QUOTA_EXHAUSTED")
        self.assertIsNotNone(health.blocked(self.root, "serper"))
        slots.use(self.root, "serper", 2, self.env)
        self.assertIsNone(health.blocked(self.root, "serper"))
        self.assertEqual([1, 2], slots.status(self.root, self.env)["serper"]["slots_with_a_key"])


if __name__ == "__main__":
    unittest.main()
