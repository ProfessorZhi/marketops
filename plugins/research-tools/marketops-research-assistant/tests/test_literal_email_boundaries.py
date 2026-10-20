"""Literal addresses may end a sentence, but cannot match a longer address."""
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import research_evidence as evidence


class LiteralEmailBoundaryTests(unittest.TestCase):
    def audit(self, suffix="", prefix=""):
        address = "facilities@example.org"
        quote = "Contact the Facilities team at " + prefix + address + suffix
        record = {"record_id":"contact", "kind":"contact_point", "artifact_ids":["source"],
                  "data":{"type":"email", "value":address,
                          "claims":[{"field":"email", "artifact_id":"source", "quote":quote}]}}
        return evidence.audit_records([record], {"source":{"kind":"rendered_html", "text":"<p>" + quote + "</p>"}})

    def test_sentence_punctuation_does_not_change_the_exact_literal_address(self):
        for punctuation in (".", ". Please contact us.", ";", ",", "!", ")", "\n"):
            with self.subTest(punctuation=punctuation):
                result = self.audit(punctuation)
                self.assertNotIn("EMAIL_NOT_IN_PRIMARY_MATERIAL", result["gap_counts"])
                self.assertNotIn("EXCERPT_NOT_FOUND", result["gap_counts"])

    def test_domain_word_plus_and_hyphen_continuations_are_not_the_address(self):
        for suffix in (".evil", ".co.uk", "word", "+tag", "-other", "_other", ".+other"):
            with self.subTest(suffix=suffix):
                self.assertIn("EMAIL_NOT_IN_PRIMARY_MATERIAL", self.audit(suffix)["gap_counts"])
        for prefix in ("other.", "x", "+", "-"):
            with self.subTest(prefix=prefix):
                self.assertIn("EMAIL_NOT_IN_PRIMARY_MATERIAL", self.audit(prefix=prefix)["gap_counts"])


if __name__ == "__main__": unittest.main()
