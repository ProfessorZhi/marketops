"""#89: an A/B row with no role must be reviewable, not silently confirmed.

Run: python -m unittest tests.test_role_review -v
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import mo_grade  # noqa: E402


def row(grade: str, role: str, *, value: str = "x@example.org", org: str = "org-1",
        review: str = "", why: str = "") -> dict:
    return {"org_id": org, "value": value, "grade": grade, "role": role,
            "person": "A Person" if grade == "A" else "", "review": review, "review_reason": why}


class MarkMissingRoleTest(unittest.TestCase):
    def test_a_row_without_role_goes_to_review(self):
        just = {("org-1", "x@example.org")}
        out = mo_grade.mark_missing_role([row("A", "")], just)
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["review"], "yes")
        self.assertIn("no role recorded", out[0]["review_reason"])

    def test_a_row_with_a_role_is_untouched(self):
        just = {("org-1", "x@example.org")}
        out = mo_grade.mark_missing_role([row("A", "Directeur")], just)
        self.assertEqual(out, [])

    def test_b_row_without_role_also_goes_to_review(self):
        just = {("org-1", "x@example.org")}
        out = mo_grade.mark_missing_role([row("B", "")], just)
        self.assertEqual(len(out), 1)

    def test_row_outside_the_ab_set_is_ignored(self):
        just = {("org-1", "x@example.org")}
        out = mo_grade.mark_missing_role([row("C", "", value="y@example.org")], just)
        self.assertEqual(out, [])

    def test_existing_review_is_not_duplicated(self):
        just = {("org-1", "x@example.org")}
        out = mo_grade.mark_missing_role([row("A", "", review="yes", why="unsure grade")], just)
        self.assertEqual(out, [])

    def test_an_existing_reason_is_kept(self):
        just = {("org-1", "x@example.org")}
        out = mo_grade.mark_missing_role([row("A", "", review="", why="page on another site")], just)
        self.assertEqual(out[0]["review_reason"], "page on another site; no role recorded")


if __name__ == "__main__":
    unittest.main(verbosity=2)
