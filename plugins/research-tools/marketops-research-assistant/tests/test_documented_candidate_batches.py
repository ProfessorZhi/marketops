"""Execute the published JSON examples through the actual candidate validators."""
import json
import os
from pathlib import Path
import re
import sys
import tempfile
import unittest
from unittest.mock import patch

PLUGIN = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PLUGIN / "scripts"))
import research_config as config
import research_lite_store as store
import research_mcp as mcp


def example(relative, label, language="json"):
    text = (PLUGIN / relative).read_text(encoding="utf-8")
    marker = "tested-source" if language == "html" else "tested-example"
    found = re.search(r"<!-- " + marker + ":" + re.escape(label) + r" -->\s*```" + language + r"\s*\n(.*?)```", text, re.S)
    if found is None:
        raise AssertionError("Missing documented example: " + label)
    return found.group(1).strip() if language == "html" else json.loads(found.group(1))


class DocumentedCandidateBatchTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        base = Path(self.temp.name)
        self.root = base / "research"
        env = patch.dict(os.environ, {"MARKETOPS_RESEARCH_DATA_ROOT": str(self.root),
            "MARKETOPS_RESEARCH_CONFIG": str(base / "config.json"), "MARKETOPS_RESEARCH_ALLOW_NETWORK": "0"})
        env.start(); self.addCleanup(env.stop)
        config.configure(str(self.root), str(base / "Research_Experience.md"), confirm=True)
        mcp.start("run-example", "generic-example", "Find sourced department routes", "unapproved-example", {
            "record_type":"research_brief", "target_kind":"organization", "goal":"contacts",
            "minimum_targets":None, "require_named_person":False,
            "evidence_requirements":{"organization":["identity"], "role":["role"], "contact_point":["email"]}})
        source = example("references/WORKFLOW_CONTRACT.md", "example-material", "html")
        store.save_artifact(self.root, "run-example", "art-example", "synthetic-documentation-fixture",
                            "https://example.org/facilities", source.encode(), "rendered_html")

    def write(self, relative, label):
        args = example(relative, label)
        return mcp.propose_batch(**args)

    def test_complete_contract_batch_is_valid_auditable_and_deliverable(self):
        self.write("references/WORKFLOW_CONTRACT.md", "department-route")
        audit = mcp.evidence_audit("run-example")
        self.assertEqual({}, audit["gap_counts"], audit)
        self.assertEqual("READY_FOR_CANDIDATE_REVIEW", mcp.next_step("run-example")["status"])
        delivered = mcp.handoff("run-example", "documentation-delivery")
        self.assertTrue(Path(delivered["workbook_xlsx"]).is_file())
        self.assertTrue(delivered["unresolved_business_review"])

    def test_stage_batches_preserve_entity_and_contact_joins(self):
        self.write("skills/marketops-accounts/SKILL.md", "venue-organization-relation")
        self.write("skills/marketops-accounts/SKILL.md", "department-role-contact")
        audit = mcp.evidence_audit("run-example")
        self.assertEqual({}, audit["gap_counts"], audit)
        records = store.list_run(self.root, "run-example", limit=20)["records"]
        by_kind = {row["kind"]: row for row in records}
        org = by_kind["organization"]["entity_key"]
        for kind in ("venue", "relationship", "role", "contact_point"):
            self.assertEqual(org, by_kind[kind]["data"]["organization_id"])
        self.assertNotIn("person_id", by_kind["contact_point"]["data"])
        self.write("skills/marketops-delivery/SKILL.md", "target-hold-review")
        self.assertEqual(1, mcp.next_step("run-example")["disposition_counts"]["HOLD"])

    def test_unknown_target_example_remains_a_visible_hold_with_missing_evidence(self):
        self.write("references/WORKFLOW_CONTRACT.md", "unknown-target-hold")
        state = mcp.next_step("run-example")
        self.assertEqual(1, state["target_count"])
        self.assertEqual(1, state["disposition_counts"]["HOLD"])
        self.assertFalse(state["goal_satisfied"])
        audit = mcp.evidence_audit("run-example")
        self.assertGreater(audit["gap_counts"].get("NO_SOURCE", 0), 0)
        self.assertGreater(audit["gap_counts"].get("MISSING_REQUIRED_IDENTITY", 0), 0)


if __name__ == "__main__": unittest.main()
