import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import research_suppression as suppression  # noqa: E402


class SuppressionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_no_list_is_not_clean(self):
        result = suppression.check(self.root, ["a@example.org"])
        self.assertEqual("NO_LIST", result["list_status"])
        self.assertEqual(0, result["entries_checked_against"])

    def test_email_and_domain_entries_match(self):
        added = suppression.add_entries(
            self.root, entries=["Stop@Club.nl", "@other.org", "mailto:x@y.com", "not an entry"],
            reason="complaint", source="client No-Go sheet")
        self.assertEqual(3, added["added"])
        self.assertEqual(1, added["invalid"])
        self.assertTrue(added["reconciled"])
        result = suppression.check(
            self.root, ["stop@club.nl", "info@mail.other.org", "ok@club.nl"])
        self.assertEqual("HITS_EXCLUDED", result["list_status"])
        self.assertEqual({"stop@club.nl", "info@mail.other.org"}, set(result["hits"]))

    def test_checked_clean_when_list_loaded_and_no_hit(self):
        suppression.add_entries(self.root, entries=["a@b.org"], reason="unsubscribe",
                                source="inbox")
        self.assertEqual("CHECKED_CLEAN",
                         suppression.check(self.root, ["c@d.org"])["list_status"])

    def test_reason_and_source_are_required(self):
        with self.assertRaises(ValueError):
            suppression.add_entries(self.root, entries=["a@b.org"], reason="whatever",
                                    source="x")
        with self.assertRaises(ValueError):
            suppression.add_entries(self.root, entries=["a@b.org"], reason="no_go",
                                    source=" ")


if __name__ == "__main__":
    unittest.main()
