"""A server running older code than the plugin on disk says so."""
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import research_code_drift as drift  # noqa: E402


class CodeDrift(unittest.TestCase):
    def test_quiet_while_the_disk_matches(self):
        with patch.object(drift, "fingerprint", return_value=dict(drift.STARTED)):
            self.assertIsNone(drift.drift())

    def test_an_update_on_disk_asks_for_a_restart(self):
        newer = {"version": "9.9.9", "scripts_changed_ns": drift.STARTED["scripts_changed_ns"] + 1}
        with patch.object(drift, "fingerprint", return_value=newer):
            changed = drift.drift()
        self.assertEqual("PLUGIN_CODE_CHANGED_RESTART_REQUIRED", changed["code"])
        self.assertEqual("9.9.9", changed["disk_version"])
        self.assertIn("restarted", changed["detail"])

    def test_the_fingerprint_reads_the_real_plugin(self):
        self.assertTrue(drift.STARTED["version"])
        self.assertGreater(drift.STARTED["scripts_changed_ns"], 0)


if __name__ == "__main__":
    unittest.main()
