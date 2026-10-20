"""RC6: Jev triage returns one compact row per saved page and flags low confidence."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import research_mcp as mcp  # noqa: E402

ANSWERS = {"a1": ("natural_grass", 0.93), "a2": ("artificial", 0.62), "a3": None}


def fake_invoke(action, run_id, operation_id, document="", **_kwargs):
    answer = ANSWERS[document]
    if answer is None:
        raise ValueError("JEV_EVIDENCE_BINDING_REQUIRED: pass a saved artifact ID")
    return {"payload": {"judgments": {"judgment": {"choice": answer[0], "confidence": answer[1]}}}}


class JevTriageTests(unittest.TestCase):
    def test_rows_counts_and_review_flags(self):
        with patch.object(mcp, "invoke", fake_invoke):
            result = mcp.jev_triage("run", "tri", ["a1", "a2", "a3"], "Surface?",
                                    ["natural_grass", "artificial", "unknown"])
        rows = {r["artifact_id"]: r for r in result["rows"]}
        self.assertFalse(rows["a1"]["needs_review"])
        self.assertTrue(rows["a2"]["needs_review"])
        self.assertEqual("JEV_EVIDENCE_BINDING_REQUIRED", rows["a3"]["error"])
        self.assertEqual(2, result["needs_review"])
        self.assertEqual({"natural_grass": 1, "artificial": 1, "error": 1}, result["counts"])

    def test_bounds(self):
        with self.assertRaisesRegex(ValueError, "JEV_TRIAGE_ITEMS_INVALID"):
            mcp.jev_triage("run", "tri", [], "q", ["a", "b"])
        with self.assertRaisesRegex(ValueError, "JEV_TRIAGE_THRESHOLD_INVALID"):
            mcp.jev_triage("run", "tri", ["a1"], "q", ["a", "b"], threshold=1.5)


if __name__ == "__main__":
    unittest.main()
