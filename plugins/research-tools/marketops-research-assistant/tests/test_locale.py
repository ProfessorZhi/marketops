"""Country and language names become the codes the search vendors want (Paris F1)."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import research_locale as locale  # noqa: E402
import search_canonical  # noqa: E402


class Codes(unittest.TestCase):
    def test_names_become_codes_and_pass_the_strict_check(self):
        for name, code in (("France", "FR"), ("france", "FR"), ("法国", "FR"), ("fr", "FR"),
                           ("United Kingdom", "GB"), ("USA", "US"), (" Germany ", "DE")):
            self.assertEqual(code, locale.country_code(name))
            search_canonical.search_operation_args(query="q", country=locale.country_code(name))
        self.assertEqual("fr", locale.language_code("French"))
        self.assertEqual("en-GB", locale.language_code("en-GB"))

    def test_an_unknown_name_still_names_the_field(self):
        with self.assertRaisesRegex(ValueError, "IDENTITY_VALUE_INVALID: country"):
            search_canonical.search_operation_args(query="q", country=locale.country_code("Atlantis"))


if __name__ == "__main__":
    unittest.main()
