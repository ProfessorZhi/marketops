"""A real user brief must be durable before a provider attempt is reserved."""
from __future__ import annotations
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/"scripts"))

import research_mcp as mcp
import research_lite_store as store


BRIEF = {"record_type": "research_brief", "target_kind": "venue",
         "goal": "pool", "minimum_targets": None,
         "require_named_person": False,
         "evidence_requirements": {"venue": ["identity","location"]}}


class BriefStartTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root=Path(tmp.name)/"data"
        env=patch.dict(os.environ,{"MARKETOPS_RESEARCH_DATA_ROOT":str(self.root),
                          "MARKETOPS_SEARCH_ALLOW_NETWORK":"0",
                          "MARKETOPS_RESEARCH_ALLOW_NETWORK":"0"})
        env.start()
        self.addCleanup(env.stop)

    def test_start_persists_valid_brief_in_one_call_and_resumes(self):
        result=mcp.start("run-brief","generic region","venue survey","rules-unknown",BRIEF)
        self.assertEqual("candidate_unverified", store.list_run(self.root,"run-brief")["records"][0]["status"])
        self.assertEqual("MORE_RESEARCH_REQUIRED",result["next_status"])
        repeated=mcp.start("run-brief","generic region","venue survey","rules-unknown",BRIEF)
        self.assertEqual("IDEMPOTENT",repeated["brief"]["status"])
        self.assertEqual(1,store.list_run(self.root,"run-brief")["record_count"])

    def test_start_without_brief_fails_closed_at_network_boundary(self):
        result=mcp.start("run-unready","generic region","venue survey","unknown")
        self.assertEqual("BRIEF_REQUIRED",result["next_status"])
        with patch.object(mcp.extra,"invoke") as mock:
            with self.assertRaisesRegex(ValueError,"RESEARCH_BRIEF_REQUIRED_BEFORE_NETWORK"):
                mcp.invoke("crawl4ai_local","run-unready","op1",
                           url="https://public.example.org/")
            mock.assert_not_called()
        with patch.object(mcp.dual_search_mcp,"search") as mock:
            with self.assertRaisesRegex(ValueError,"RESEARCH_BRIEF_REQUIRED_BEFORE_NETWORK"):
                mcp.search("serper","target company","SG","en","run-unready","op2")
            mock.assert_not_called()

    def test_invalid_brief_prevents_run_creation_and_any_payment(self):
        invalid={**BRIEF,"minimum_targets":10,"minimum_targets_unit":"organization"}
        with self.assertRaisesRegex(ValueError,"RESEARCH_BRIEF_INVALID"):
            mcp.start("invalid-brief","region","goal","rules",invalid)
        self.assertFalse((self.root/"state"/"research-lite.sqlite3").exists())

    def test_invalid_dynamic_scene_field_rejected(self):
        invalid={**BRIEF,"evidence_requirements":{"venue":["bad/not-valid"]}}
        with self.assertRaisesRegex(ValueError,"EVIDENCE_REQUIREMENTS_INVALID"):
            mcp.start("bad-scene","region","goal","rules",invalid)


if __name__ == "__main__":
    unittest.main()
