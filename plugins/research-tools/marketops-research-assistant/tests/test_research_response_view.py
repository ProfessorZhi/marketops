"""Lossless storage precedes a bounded model-facing provider response."""
import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import research_lite_store as store
import research_response_view as views


class ProviderViewTests(unittest.TestCase):
    def saved(self, root, payload):
        store.start_run(root, "view-run", "scope", "objective", "rules-v1")
        saved = store.save_artifact(root, "view-run", "provider-material", "tinyfish",
            "https://example.invalid/", json.dumps(payload, ensure_ascii=False).encode(), "provider_extracted")
        return {"status": "PROVIDER_CALL_COMPLETED", "managed_status": "OK", "provider_status": "OK",
                "payload": payload, "result": copy.deepcopy(payload),
                "outcome": {"status": "OK", "reason_code": "", "payload": copy.deepcopy(payload)},
                "artifact": saved, "artifact_ids": ["provider-material"],
                "stored_record_ids": ["source-a"], "actual_usd": None,
                "invocation": {"billing": {"charge_verified": False, "actual_amount": None}},
                "caveats": ["UNKNOWN_COST"], "reason_code": "", "ledger_operation_id": "real-op"}

    def test_preview_is_bounded_but_full_payload_and_financial_information_survive(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            payload = {"results": [{"url": f"https://example.invalid/{i}", "html": "synthetic正文" * 5000} for i in range(12)],
                       "errors": [{"url": "https://example.invalid/fail", "reason": "HTTP_403"}],
                       "usage": {"currency": "USD", "cost": None}}
            original = self.saved(root, payload)
            frozen = copy.deepcopy(original)
            result = views.compact_provider_response(root, "view-run", original)
            self.assertEqual(frozen, original)
            self.assertNotIn("result", result)
            self.assertNotIn("payload", result["outcome"])
            self.assertTrue(result["response_view"]["truncated"])
            self.assertEqual(12, result["response_view"]["row_counts"]["$.results"]["total"])
            self.assertLessEqual(len(json.dumps(result["payload"],ensure_ascii=False).encode()), 8000)
            for key in ("actual_usd", "invocation", "caveats", "ledger_operation_id", "artifact_ids", "stored_record_ids"):
                self.assertEqual(original[key], result[key])
            self.assertEqual(payload["errors"], result["payload_diagnostics"]["$.errors"])
            self.assertEqual(payload["usage"], result["payload_diagnostics"]["$.usage"])
            stored = store.read_artifact(root, "view-run", "provider-material")
            self.assertEqual(payload, json.loads(stored["content"]))

    def test_small_payload_is_returned_once_without_claiming_content_truncation(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            payload = {"results": [{"url": "https://example.invalid/a", "snippet": "short"}]}
            result = views.compact_provider_response(root, "view-run", self.saved(root,payload))
            self.assertEqual(payload, result["payload"])
            self.assertFalse(result["response_view"]["truncated"])
            self.assertEqual(["result", "outcome.payload"], result["response_view"]["removed_duplicate_paths"])

    def test_refuses_to_compact_unpersisted_or_mismatched_payload(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            response = self.saved(root,{"results": []})
            response["payload"]={"results": [{"url":"https://example.invalid/other"}]}
            with self.assertRaisesRegex(ValueError,"SAVED_PAYLOAD"):
                views.compact_provider_response(root,"view-run",response)

    def test_a_payload_holding_a_tuple_is_not_a_storage_mismatch(self):
        # Persist writes with json.dumps, so an in-memory tuple is saved as a
        # list. Serper's `credit_estimate.applied_rules` is a tuple, and the raw
        # comparison refused every Serper search as "saved payload mismatch".
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            payload = {"credit_estimate": {"applied_rules": ("rule-a", "rule-b")},
                       "results": [{"url": "https://example.invalid/a"}]}
            result = views.compact_provider_response(root, "view-run", self.saved(root, payload))
            # The view keeps the live object; what the host receives is its
            # persisted form, which is the list written to the artifact.
            self.assertEqual(["rule-a", "rule-b"],
                             json.loads(json.dumps(result["payload"]["credit_estimate"]["applied_rules"])))

    def test_a_real_difference_is_still_refused_next_to_a_tuple(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            response = self.saved(root, {"credit_estimate": {"applied_rules": ("rule-a",)},
                                         "results": []})
            response["payload"] = {"credit_estimate": {"applied_rules": ("rule-b",)},
                                   "results": []}
            with self.assertRaisesRegex(ValueError, "SAVED_PAYLOAD"):
                views.compact_provider_response(root, "view-run", response)

    def test_failure_response_is_untouched(self):
        response={"status":"FAILED","reason_code":"SOURCE_ASSOCIATION_FAILED","payload":{"error":"retain"},"actual_usd":"0.2"}
        self.assertEqual(response, views.compact_provider_response(Path('/unused'),"r",response))


if __name__ == '__main__':
    unittest.main()
