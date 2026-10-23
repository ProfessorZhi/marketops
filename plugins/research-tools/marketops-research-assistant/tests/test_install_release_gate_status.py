"""RC3 release-gate status: a technical pass is not a release pass.

The old gate could print ``ok: true`` while ``SOURCE_MANIFEST.qa_verified`` was
false. These tests pin the unified report layer: ``technical_checks_pass`` and
``release_ready`` are separate fields, and ``ok`` is true only for PASS.
"""
from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path

PLUGIN = Path(__file__).resolve().parents[1]
REPO = PLUGIN.parents[2]
GATE_PATH = REPO / "scripts" / "rc-20261020" / "check_release_gate.py"

spec = importlib.util.spec_from_file_location("rc_release_gate", GATE_PATH)
gate = importlib.util.module_from_spec(spec)
sys.modules["rc_release_gate"] = gate
spec.loader.exec_module(gate)


def _pass(check_id):
    return {"id": check_id, "name": check_id, "status": "PASS", "detail": ""}


def _build(*, qa_verified, manifest_qa, attestation_verified,
           signed_by="synthetic-reviewer"):
    return {
        "id": "G7-release", "name": "release manifest/hash", "status": "PASS",
        "detail": "source + connector manifests verified",
        "evidence": {
            "source": {"qa_verified": qa_verified,
                       "qa_attestation": {"verified": attestation_verified,
                                          "signed_by": signed_by,
                                          "reason": "" if attestation_verified
                                          else "INDEPENDENT_QA_ATTESTATION_MISSING"}},
            "source_manifest": {"qa_verified": manifest_qa},
        },
    }


class AssessReleaseTests(unittest.TestCase):
    def test_no_release_build_is_not_release_ready(self):
        result = gate.assess_release([_pass("G1-A")])
        self.assertFalse(result["release_ready"])
        self.assertEqual("RELEASE_BUILD_NOT_EVALUATED", result["blocked_reason"])
        self.assertEqual("NOT_PERFORMED", result["formal_qa"])

    def test_qa_verified_false_blocks_even_when_hashes_pass(self):
        checks = [_pass("G1-A"), _build(qa_verified=False, manifest_qa=False,
                                        attestation_verified=False)]
        result = gate.assess_release(checks)
        self.assertTrue(result["technical_checks_pass"])
        self.assertFalse(result["release_ready"])
        self.assertEqual("SOURCE_MANIFEST_QA_VERIFIED_FALSE", result["blocked_reason"])

    def test_qa_verified_without_independent_attestation_is_not_ready(self):
        checks = [_pass("G1-A"), _build(qa_verified=True, manifest_qa=True,
                                        attestation_verified=False)]
        result = gate.assess_release(checks)
        self.assertFalse(result["release_ready"])
        self.assertEqual("INDEPENDENT_QA_ATTESTATION_UNVERIFIED",
                         result["blocked_reason"])
        self.assertEqual("NOT_PERFORMED", result["formal_qa"])

    def test_verified_external_attestation_is_release_ready(self):
        checks = [_pass("G1-A"), _build(qa_verified=True, manifest_qa=True,
                                        attestation_verified=True)]
        result = gate.assess_release(checks)
        self.assertTrue(result["release_ready"])
        self.assertEqual("RELEASE_READY", result["release_readiness"])
        self.assertEqual("PASS", result["formal_qa"])
        self.assertEqual("synthetic-reviewer", result["independent_qa_signed_by"])

    def test_technical_failure_keeps_release_unready(self):
        failing = {"id": "G1-A", "name": "A", "status": "FAIL", "detail": "red"}
        checks = [failing, _build(qa_verified=True, manifest_qa=True,
                                  attestation_verified=True)]
        result = gate.assess_release(checks)
        self.assertFalse(result["release_ready"])
        self.assertEqual("TECHNICAL_CHECKS_NOT_GREEN", result["blocked_reason"])


class ReportTests(unittest.TestCase):
    def _blocked_checks(self):
        checks = [_pass("G1-A"), _build(qa_verified=False, manifest_qa=False,
                                        attestation_verified=False)]
        assessment = gate.assess_release(checks)
        checks.append({"id": "G15-release-readiness", "name": "release readiness",
                       "status": "BLOCKED", "detail": "qa_verified=false"})
        return checks, assessment

    def test_ok_is_false_and_status_blocked_without_qa(self):
        checks, assessment = self._blocked_checks()
        report = gate.build_report(checks, assessment, "a" * 40)
        self.assertEqual("BLOCKED", report["status"])
        self.assertFalse(report["ok"])
        self.assertTrue(report["technical_checks_pass"])
        self.assertFalse(report["release_ready"])
        self.assertEqual("NOT_RELEASE_READY", report["release_readiness"])

    def test_ok_is_true_only_when_release_ready(self):
        checks = [_pass("G1-A"), _build(qa_verified=True, manifest_qa=True,
                                        attestation_verified=True)]
        assessment = gate.assess_release(checks)
        checks.append({"id": "G15-release-readiness", "name": "release readiness",
                       "status": "PASS", "detail": ""})
        report = gate.build_report(checks, assessment, "b" * 40)
        self.assertEqual("PASS", report["status"])
        self.assertTrue(report["ok"])
        self.assertTrue(report["release_ready"])

    def test_a_technical_failure_is_fail_not_blocked(self):
        checks = [{"id": "G1-A", "name": "A", "status": "FAIL", "detail": "red"},
                  _build(qa_verified=True, manifest_qa=True,
                         attestation_verified=True)]
        assessment = gate.assess_release(checks)
        checks.append({"id": "G15-release-readiness", "name": "release readiness",
                       "status": "BLOCKED", "detail": ""})
        report = gate.build_report(checks, assessment, "c" * 40)
        self.assertEqual("FAIL", report["status"])
        self.assertFalse(report["ok"])


if __name__ == "__main__":
    unittest.main()
