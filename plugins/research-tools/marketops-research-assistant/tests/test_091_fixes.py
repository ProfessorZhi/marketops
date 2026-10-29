"""The fixes from the all-Paris run on 0.9.0 (0.9.1).

The scale held (project() median 0.59 s on 4,026 organisations, no busy); what it showed: Jev gave
X to clubs' own mailboxes on the city's booking table ("page on another site"), the gate let ungraded
rows through, and a worker kept writing for 15 minutes after the lead exported.

Run: python -m unittest tests.test_091_fixes -v
"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
if "MARKETOPS_PROJECTS" not in os.environ:   # alone; under discover test_core set these already
    TMP = tempfile.mkdtemp()
    os.environ.update(MARKETOPS_HOME=TMP + "/home", MARKETOPS_PROJECTS=TMP + "/projects",
                      MARKETOPS_HEALTH="0", MARKETOPS_KNOWLEDGE="0")
for k in ("SERPER_API_KEY", "SERPAPI_API_KEY", "DEEPSEEK_API_KEY", "TINYFISH_API_KEY", "TYPESAFE_API_KEY"):
    os.environ.pop(k, None)

import mo_export  # noqa: E402
import mo_grade  # noqa: E402
import mo_providers  # noqa: E402
import mo_workspace as ws  # noqa: E402
import server  # noqa: E402

ws._dns_ok[:] = [False]
BRIEF = {"goal": "g", "country": "FR", "language": "fr"}


class ListRows(unittest.TestCase):
    def test_a_clubs_own_row_on_a_list_is_not_another_sites_page(self):
        p, pdir = "row091", ws.project_dir("row091")
        server.project(p, brief=BRIEF)
        with mock.patch.object(mo_providers, "search", return_value={"results": [], "routes": {}}):
            server.orgs(p, add=[{"name": "Club Reservation", "email": "clubresa091@gmail.com"}],
                        source="booking table")
        row = ws.read_contacts(pdir)[0]
        self.assertEqual("row", row["page_on_org_site"])
        self.assertEqual("", mo_grade._rule_grade({**row, "email_site": "clubresa091.fr"}, {})[0])   # never "X"


class Gate(unittest.TestCase):
    def setUp(self):
        self.ab = [{"grade": "A", "checked": "ok", "value": f"a{i}@x.fr"} for i in range(20)]

    def test_an_ungraded_address_keeps_the_workbook_in(self):
        pdir = ws.project_dir("gate091a")
        got = mo_export.delivery_gate(self.ab + [{"grade": "", "value": "n@x.fr"}], pdir)
        self.assertFalse(got["ready"])
        self.assertIn("还没有评级", got["status"])

    def test_a_package_still_out_keeps_the_workbook_in(self):
        pdir = ws.project_dir("gate091b")
        ws.write_csv(pdir / "tasks.csv", ["task_id", "kind", "status", "agent", "items"],
                     [{"task_id": "t1", "kind": "sweep", "status": "claimed", "agent": "w7", "items": "o1"}])
        got = mo_export.delivery_gate(self.ab, pdir)
        self.assertFalse(got["ready"])
        self.assertIn("tasks(action=\"stop\")", got["status"])


class SetFormat(unittest.TestCase):
    def test_a_set_of_plain_strings_says_what_it_takes(self):
        p = "fmt091"
        server.project(p, brief=BRIEF)
        got = server.grade(p, set=["a@x.fr"])
        self.assertIn("a list of objects", got["error"])


if __name__ == "__main__":
    unittest.main()
