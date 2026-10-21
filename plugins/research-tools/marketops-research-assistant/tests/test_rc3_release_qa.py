"""RC3 release QA: a self-generated receipt is not an independent QA pass.

The old gate printed ``ok:true`` while ``SOURCE_MANIFEST.qa_verified`` was
``false``; ZIP/hash checks were being read as formal QA. These tests pin the
opposite: the build stays a preview unless the external receipt carries an
Ed25519 attestation that verifies against a caller-supplied public key.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import research_runtime_setup as runtime  # noqa: E402

try:
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
except ImportError:  # pragma: no cover - the e2e venv ships cryptography
    Ed25519PrivateKey = None


def _payload(block):
    return json.dumps({name: value for name, value in block.items()
                       if name != "signature"},
                      sort_keys=True, separators=(",", ":"),
                      ensure_ascii=True).encode("utf-8")


@unittest.skipUnless(Ed25519PrivateKey is not None, "cryptography unavailable")
class IndependentQATests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.plugin = Path(self.tmp.name) / "plugin"
        (self.plugin / "references").mkdir(parents=True)
        self.key = Ed25519PrivateKey.generate()
        self.public_hex = self.key.public_key().public_bytes(
            Encoding.Raw, PublicFormat.Raw).hex()
        self.receipt = {
            "schema": "marketops.build-qa.v1",
            "source_revision": "a" * 40,
            "test": {"log_sha256": "b" * 64},
        }
        self.block = {
            "schema": "marketops.independent-qa.v1",
            "signed_by": "independent-qa-reviewer",
            "source_revision": "a" * 40,
            "log_sha256": "b" * 64,
            "tests_run": 2187,
        }

    def write(self, block, *, signed=True):
        if signed:
            block = {**block,
                     "signature": self.key.sign(_payload(block)).hex()}
        self.receipt["independent_qa"] = block
        (self.plugin / "references" / "BUILD_QA.json").write_text(
            json.dumps(self.receipt), encoding="utf-8")

    def test_nonexistent_attestation_is_not_verified(self):
        (self.plugin / "references" / "BUILD_QA.json").write_text(
            json.dumps(self.receipt), encoding="utf-8")
        result = runtime.verify_independent_qa(self.plugin)
        self.assertFalse(result["verified"])
        self.assertEqual("INDEPENDENT_QA_ATTESTATION_MISSING", result["reason"])

    def test_a_signature_without_a_public_key_is_not_verified(self):
        self.write(self.block)
        with mock.patch.dict(os.environ, {}, clear=True):
            result = runtime.verify_independent_qa(self.plugin)
        self.assertFalse(result["verified"])
        self.assertEqual("INDEPENDENT_QA_PUBLIC_KEY_MISSING", result["reason"])

    def test_a_valid_signature_is_verified(self):
        self.write(self.block)
        with mock.patch.dict(os.environ, {"MARKETOPS_QA_PUBLIC_KEY": self.public_hex}):
            result = runtime.verify_independent_qa(self.plugin)
        self.assertTrue(result["verified"])
        self.assertEqual("independent-qa-reviewer", result["signed_by"])

    def test_tampering_with_the_signed_block_is_refused(self):
        block = {**self.block, "tests_run": 1}
        self.receipt["independent_qa"] = {
            **block, "signature": self.key.sign(_payload(self.block)).hex()}
        (self.plugin / "references" / "BUILD_QA.json").write_text(
            json.dumps(self.receipt), encoding="utf-8")
        with mock.patch.dict(os.environ, {"MARKETOPS_QA_PUBLIC_KEY": self.public_hex}):
            result = runtime.verify_independent_qa(self.plugin)
        self.assertFalse(result["verified"])
        self.assertTrue(result["reason"].startswith("INDEPENDENT_QA_SIGNATURE_INVALID"))

    def test_a_different_revision_is_refused_before_signature_verification(self):
        self.write({**self.block, "source_revision": "c" * 40})
        with mock.patch.dict(os.environ, {"MARKETOPS_QA_PUBLIC_KEY": self.public_hex}):
            result = runtime.verify_independent_qa(self.plugin)
        self.assertFalse(result["verified"])
        self.assertEqual("INDEPENDENT_QA_REVISION_MISMATCH", result["reason"])


class SelfReceiptIsPreviewTests(unittest.TestCase):
    def test_build_source_with_a_self_generated_receipt_stays_a_preview(self):
        import build_client_bundles
        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp)
            first = build_client_bundles.build_source(work / "first")
            target = Path(first["directory"])
            # A receipt generated from the very same tree: everything matches,
            # but nobody independent has signed it.
            receipt = {
                "schema": "marketops.build-qa.v1",
                "source_revision": "a" * 40,
                "files": runtime.source_manifest(target),
                "test": {"returncode": 0, "tests_run": 2187,
                         "suite": "plugin-unittest-discovery",
                         "log_sha256": "b" * 64},
            }
            receipt_path = work / "BUILD_QA.json"
            receipt_path.write_text(json.dumps(receipt), encoding="utf-8")
            second = build_client_bundles.build_source(work / "second",
                                                       qa_receipt=receipt_path)
            self.assertFalse(second["qa_verified"])
            self.assertEqual("SOURCE_PACKAGE_PREVIEW_BUILT", second["status"])
            self.assertEqual("INDEPENDENT_QA_ATTESTATION_MISSING",
                             second["qa_attestation"]["reason"])
            manifest = json.loads(
                (Path(second["directory"]) / "SOURCE_MANIFEST.json").read_text(
                    encoding="utf-8"))
            self.assertFalse(manifest["qa_verified"])
            self.assertEqual("INDEPENDENT_QA_ATTESTATION_MISSING",
                             manifest["qa_attestation"]["reason"])


if __name__ == "__main__":
    unittest.main()
