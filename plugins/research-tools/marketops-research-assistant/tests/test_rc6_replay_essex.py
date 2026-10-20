"""RC6 replay: a Member-A-style task runs end to end through the MCP surface.

A small frozen web (one directory page, three village-hall sites, one blocked
site) stands in for the providers. The test drives the same functions an Agent
calls: start, a directory fetch, one site harvest per hall, handoff. It pins the
outcome the Essex trial missed: every hall with a public booking route reaches
the client sheet with its quoted email and phone, and the blocked hall is a HOLD
with its cause rather than a silent drop.

Synthetic, offline material only.
"""
from __future__ import annotations

import csv
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import research_lite_store as store  # noqa: E402
import research_mcp as mcp  # noqa: E402
import research_config as config  # noqa: E402
import research_runtime_setup as runtime  # noqa: E402

RUN = "essex-replay"
DIRECTORY = "https://www.essexhalls.example/directory/halls"
HALLS = {
    "org-alpha": ("Alpha Village Hall Trust", "https://www.alphahall.example/"),
    "org-beta": ("Beta Community Association", "https://betacommunity.example/"),
    "org-gamma": ("Gamma Village Hall Committee", "https://www.gammahall.example/"),
    "org-delta": ("Delta Hall Charity", "https://deltahall.example/"),
}
WEB = {
    DIRECTORY: "<ul>" + "".join(f'<li><a href="{url}">{name}</a></li>'
                               for name, url in HALLS.values()) + "</ul>",
    "https://www.alphahall.example/": (
        "<p>Alpha Village Hall is run by the Alpha Village Hall Trust.</p>"
        '<a href="/hire">Hire the hall</a><a href="/contact">Contact</a>'),
    "https://www.alphahall.example/hire": "<p>Main hall: &pound;18 per hour.</p>",
    "https://www.alphahall.example/contact": (
        "<p>Bookings: hire@alphahall.example or 01245 111222.</p>"),
    "https://betacommunity.example/": (
        "<p>Welcome to the Beta Community Association centre.</p>"
        '<a href="/about-us">About us</a>'),
    "https://betacommunity.example/about-us": (
        "<p>Bookings Secretary: Pat Lee, betacentre.bookings@gmail.com, 01376 333444.</p>"),
    "https://www.gammahall.example/": (
        "<footer>Gamma Village Hall Committee, registered charity 1100100. "
        "Enquiries: gammahall@btinternet.com or call 01621 555666.</footer>"),
}


def managed_call(*, capability_id, params, run_id, operation_id, **_ignored):
    url = params.get("url", "")
    if url not in WEB:
        return {"status": "BLOCKED", "reason_code": "SOURCE_ACCESS_BLOCKED",
                "gateway_invoked": True, "actual_usd": None, "payload": {}}
    artifact = "page-" + operation_id
    store.save_artifact(Path(os.environ["MARKETOPS_RESEARCH_DATA_ROOT"]), run_id, artifact,
                        capability_id.split(".")[0], url,
                        ("<html><body>" + WEB[url] + "</body></html>").encode(), "rendered_html")
    return {"status": "OK", "reason_code": "", "gateway_invoked": True, "actual_usd": None,
            "payload": {"url": url, "html": WEB[url]}, "artifact_ids": [artifact],
            "primary_artifact_ids": [artifact]}


class EssexReplayTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        base = Path(tmp.name)
        self.root = base / "research"
        env = patch.dict(os.environ, {"MARKETOPS_RESEARCH_DATA_ROOT": str(self.root),
                                      "MARKETOPS_RESEARCH_CONFIG": str(base / "config.json")})
        env.start()
        self.addCleanup(env.stop)
        config.configure(str(self.root), str(base / "Research_Experience.md"), confirm=True)
        for target, replacement in ((mcp, "managed_call"), ):
            patcher = patch.object(target, replacement, managed_call)
            patcher.start()
            self.addCleanup(patcher.stop)
        allow = patch.object(runtime, "admission",
                             lambda _root, _capability: {"allowed": True, "reason_code": ""})
        allow.start()
        self.addCleanup(allow.stop)
        self.tools = mcp.build_server()._tool_manager._tools  # noqa: SLF001

    def call(self, name, **arguments):
        return self.tools[name].fn(**arguments)

    def test_three_halls_reach_the_client_sheet_and_the_blocked_one_is_held(self):
        started = self.call("marketops_start", run_id=RUN, scope="Essex village halls",
                            objective="Essex village halls hireable by the hour: email and phone",
                            rule_version="v1", brief={
                                "record_type": "research_brief", "target_kind": "organization",
                                "goal": "contacts", "require_named_person": False,
                                "minimum_targets": 3,
                                "evidence_requirements": {"organization": ["identity"]}})
        self.assertEqual("RUN_CREATED", started["status"])

        listing = self.call("marketops_research_step", run_id=RUN, operation_id="dir-1",
                            task_kind="known_public_url", url=DIRECTORY)
        self.assertEqual("SUCCESS_CANDIDATE", listing["status"])

        results = {}
        for key, (name, url) in HALLS.items():
            results[key] = self.call("marketops_site_harvest", run_id=RUN,
                                     operation_id="harvest-" + key, organization_name=name,
                                     organization_key=key, url=url)
        for key in ("org-alpha", "org-beta", "org-gamma"):
            admitted = [d for d in results[key]["admission"] if d["admitted"]]
            self.assertEqual(3, len(admitted), (key, results[key]["admission"]))
        self.assertTrue(results["org-delta"]["next_action"].startswith("SITE_UNREACHABLE"))

        self.call("marketops_propose_batch", run_id=RUN, items=[{
            "record_id": "review-delta", "stage": "review", "kind": "review",
            "entity_key": "review-org-delta", "artifact_ids": [], "facts": {
                "review_type": "target_disposition", "target_kind": "organization",
                "target_entity_key": "org-delta", "disposition": "HOLD",
                "reason": "Official site blocked (SOURCE_ACCESS_BLOCKED); no other public route."}}])

        delivered = self.call("marketops_handoff", run_id=RUN, operation_id="handoff-1")
        workbook = Path(delivered["workbook_xlsx"])
        self.assertTrue(workbook.is_file())
        with (workbook.parent / "客户总表.csv").open(encoding="utf-8-sig") as handle:
            master = {row[0]: row for row in csv.reader(handle)}
        self.assertEqual({"org-alpha", "org-beta", "org-gamma"},
                         set(master) & set(HALLS))
        with (workbook.parent / "联系人路线.csv").open(encoding="utf-8-sig") as handle:
            values = {row[5] for row in csv.reader(handle)}
        self.assertTrue({"hire@alphahall.example", "01245 111222",
                         "betacentre.bookings@gmail.com", "gammahall@btinternet.com"} <= values)


if __name__ == "__main__":
    unittest.main()
