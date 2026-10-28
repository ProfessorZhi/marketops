"""The offline diagnostic must never echo an address, name, phone, URL or rule string.

Run: python -m unittest tests.test_diagnose_offline -v
"""
from __future__ import annotations

import csv
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import diagnose_offline  # noqa: E402

SECRET_EMAIL = "jane.doe@club-alpha.example"
SECRET_NAME = "Jane Doe"
SECRET_PHONE = "01 23 45 67 89"
SECRET_URL = "https://portal.example/clubs/alpha"
RULE = "address rule: info -> X"


class NoPiiTest(unittest.TestCase):
    def test_output_carries_counts_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            pdir = Path(tmp)
            with (pdir / "orgs.csv").open("w", newline="", encoding="utf-8") as fh:
                w = csv.DictWriter(fh, ["id", "name", "website", "city", "source"])
                w.writeheader()
                w.writerow({"id": "o1", "name": SECRET_NAME, "website": SECRET_URL,
                            "city": "Paris", "source": "fixture source"})
            with (pdir / "contacts.csv").open("w", newline="", encoding="utf-8") as fh:
                w = csv.DictWriter(fh, ["org_id", "org", "kind", "value", "person", "role", "grade",
                                        "grade_reason", "graded_by", "review", "review_reason",
                                        "page_url", "page_site", "page_on_org_site", "email_on_org",
                                        "found_in", "same_site"])
                w.writeheader()
                w.writerow({"org_id": "o1", "org": SECRET_NAME, "kind": "email", "value": SECRET_EMAIL,
                            "person": SECRET_NAME, "role": "", "grade": "A",
                            "grade_reason": "jev A p 0.9", "graded_by": "jev", "review": "yes",
                            "review_reason": "no role recorded", "page_url": SECRET_URL,
                            "page_site": "portal.example", "page_on_org_site": "no",
                            "email_on_org": "no", "found_in": "deep1", "same_site": "no"})
                w.writerow({"org_id": "o1", "org": SECRET_NAME, "kind": "phone", "value": SECRET_PHONE,
                            "person": "", "role": "", "grade": "X", "grade_reason": RULE,
                            "graded_by": "rule", "review": "", "review_reason": "",
                            "page_url": SECRET_URL, "page_site": "portal.example",
                            "page_on_org_site": "no", "email_on_org": "", "found_in": "deep1",
                            "same_site": ""})
            with (pdir / "crawl.csv").open("w", newline="", encoding="utf-8") as fh:
                w = csv.DictWriter(fh, ["org_id", "org", "pass", "status"])
                w.writeheader()
                w.writerow({"org_id": "o1", "org": SECRET_NAME, "pass": "final", "status": "done"})

            text = json.dumps(diagnose_offline.collect(pdir), ensure_ascii=False)
            for secret in (SECRET_EMAIL, SECRET_NAME, SECRET_PHONE, SECRET_URL, "portal.example",
                           "info -> X", "@"):
                self.assertNotIn(secret, text, f"diagnostic leaked {secret!r}")
            self.assertIn('"ab_rows": 1', text)


if __name__ == "__main__":
    unittest.main(verbosity=2)
