"""Wave-6 W-F: the Jev adapter against the composition root's own conformance probe.

`research_wiring` reported `typesafe_jev` NON_CONFORMANT with three findings:
`PROVIDER_MISMATCH jev!=typesafe_jev`, `EXECUTE_SIGNATURE_NOT_CALLABLE_AS_execute(request)`
and `NO_CANCEL_METHOD`. The second one is the dangerous one: the gateway calls
`execute(request)` **after** `ledger.reserve`, so a signature it cannot call is a
`TypeError` it cannot classify — and an unclassifiable exception is booked as an
UNKNOWN charge for a call that never left the process.

So this file holds two things: the real conformance check (skipped where the
composition root is not importable, run where it is), and the property that matters
more than the check — **every pre-dispatch failure is a refusal in the contract's code
space, and no SDK client is ever constructed on those paths.**

Offline only: a fake `typesafe_sdk`, no network, no credential, no provider, no charge.
"""
from __future__ import annotations

import inspect
import sys
import tempfile
import unittest
from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "tests"))

import _wave3_e2e_fixture as fixture  # noqa: E402
import research_adapter_jev as jev  # noqa: E402
import research_lite_store as store  # noqa: E402

PAGE = ("<html><body><h1>Example Arts Centre</h1>"
        "<p>Operated by the Example Borough Council.</p>"
        "<p>Enquiries: enquiries@example.org</p></body></html>")


@dataclass(frozen=True)
class Request:
    """A stand-in for the contract's `CapabilityRequest` (§3.1), same four fields."""

    capability_id: str
    params: Mapping[str, object]
    run_id: str
    operation_id: str
    account_ref: str = "acct-fixture"
    intent_fingerprint: str = "intent-fixture"
    grant_ref: str | None = field(default=None)


class ConformanceCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / "data"
        self.root.mkdir(parents=True)
        store.start_run(self.root, "run-c", "GB", "Conformance", "SOP-PENDING")
        store.save_artifact(self.root, "run-c", "art-own", "fixture",
                            "https://example.org/visit", PAGE.encode(), "raw_response")
        self.sdk = fixture.FakeJev()
        patcher = _patch_sdk(self.sdk.module())
        self.addCleanup(patcher.stop)

    def adapter(self, **kwargs):
        return jev.JevAdapter(api_key=kwargs.pop("api_key", "fictional-key"),
                              root=kwargs.pop("root", self.root), **kwargs)

    def request(self, capability_id="jev.noul", **params):
        base = {"artifact_id": "art-own", "question": "Is this recorded as so?"}
        base.update(params)
        return Request(capability_id=capability_id, params=base,
                       run_id="run-c", operation_id="op-conformance")


def _patch_sdk(module):
    from unittest.mock import patch
    patcher = patch.dict(sys.modules, {"typesafe_sdk": module})
    patcher.start()
    return patcher


class ConformanceProbeTests(ConformanceCase):
    def test_the_composition_root_s_own_check_accepts_this_adapter(self):
        """Skips on a worker branch; runs where the composition root is beside us."""
        try:
            import research_adapter_base as base
            import research_capability_registry as registry_module
        except ImportError:  # pragma: no cover - depends on which branch this is
            self.skipTest("research_adapter_base is not on this branch")
        registry = getattr(registry_module, "DEFAULT_REGISTRY", None) or \
            getattr(registry_module, "REGISTRY", None)
        report = base.check_adapter_conformance(
            self.adapter(), expected_provider="typesafe_jev", registry=registry)
        self.assertTrue(report.conformant, report.problems)

    def test_the_provider_name_is_the_registry_routing_key(self):
        self.assertEqual("typesafe_jev", self.adapter().provider)
        self.assertEqual("jev", jev.LEGACY_PROVIDER)

    def test_the_declared_capabilities_are_the_registry_s_ids(self):
        self.assertEqual(("jev.choice", "jev.noul", "jev.score", "jev.box"),
                         self.adapter().capabilities())

    def test_execute_and_cancel_are_callable_with_one_positional_argument(self):
        """The probe's own rule: one positional parameter, no required keyword-only.

        Stated as the probe states it, because a signature that merely *looks* fine
        still fails the check it has to pass.
        """
        for name in ("execute", "cancel"):
            with self.subTest(method=name):
                # The bound method, which is what the probe inspects: `self` is gone.
                parameters = list(inspect.signature(
                    getattr(self.adapter(), name)).parameters.values())
                positional = [p for p in parameters
                              if p.kind in (p.POSITIONAL_ONLY, p.POSITIONAL_OR_KEYWORD)]
                required_keyword_only = [p for p in parameters
                                         if p.kind is p.KEYWORD_ONLY and p.default is p.empty]
                self.assertEqual(1, len(positional))
                self.assertEqual([], required_keyword_only)
        for name in ("probe_schema", "capabilities"):
            self.assertTrue(callable(getattr(self.adapter(), name)))


class RequestPathTests(ConformanceCase):
    def test_a_noul_request_dispatches_from_the_saved_artifact(self):
        result = self.adapter().execute(self.request())
        self.assertEqual("OK", result.outcome)
        self.assertEqual("noul", result["payload"]["judgments"]["judgment"]["primitive"])
        # The input is the server-extracted span, not anything the caller wrote.
        self.assertIn("Operated by the Example Borough Council",
                      self.sdk.calls[0]["state"]["source_excerpt"])

    def test_a_choice_request_preserves_the_selection(self):
        result = self.adapter().execute(self.request(
            "jev.choice", options=["operator", "tenant"]))
        self.assertEqual("OK", result.outcome)
        self.assertEqual("operator",
                         result["payload"]["judgments"]["judgment"]["choice"])

    def test_a_score_request_preserves_the_rubric(self):
        result = self.adapter().execute(self.request(
            "jev.score", scale=["weak support", "some support", "strong support"]))
        self.assertEqual("OK", result.outcome)
        self.assertEqual(3, result["payload"]["judgments"]["judgment"]["level_count"])

    def test_the_result_is_readable_the_way_the_gateway_reads_it(self):
        result = self.adapter().execute(self.request())
        self.assertEqual("OK", getattr(result, "outcome", None))
        self.assertTrue(hasattr(result, "caveats"))
        self.assertIsNone(getattr(result, "actual_usd", "missing"))
        self.assertEqual("NONE", getattr(result, "actual_source", "missing"))

    def test_the_adapter_names_itself_in_the_stored_provenance(self):
        result = self.adapter().execute(self.request())
        provider = result["payload"]["provider"]
        self.assertEqual("typesafe_jev", provider)


class PreDispatchRefusalTests(ConformanceCase):
    """Every failure before dispatch must be a §5.3 refusal with no SDK call.

    A refusal settles the reservation as BLOCKED/NO_CHARGE. An unclassified exception
    becomes UNKNOWN and keeps the money held — for a call that never happened.
    """

    def refusal_code(self, action) -> str:
        """The §5.3 code a pre-dispatch failure produces, whichever mechanism it uses.

        The contract classifies refusals two ways — a raised `ValueError` and a
        BLOCKED result both settle as NO_CHARGE — so this asserts the *classification*
        rather than the mechanism. What it must never do is return an unclassified
        exception, which the gateway would book as an UNKNOWN charge.
        """
        try:
            result = action()
        except ValueError as exc:
            self.assertTrue(isinstance(exc, jev.JevRefusal), type(exc).__name__)
            self.assertIn(exc.local_code, jev.JEV_REFUSAL_CODES)
            return exc.reason_code
        except Exception as exc:  # noqa: BLE001 - that is the failure being ruled out
            self.fail(f"unclassified {type(exc).__name__} after reserve: {exc}")
        self.assertEqual("BLOCKED", result.outcome, "pre-dispatch failure not refused")
        code = next((c for c in result.caveats if c in jev.CONTRACT_REASON_CODES), None)
        self.assertIsNotNone(code, f"no closed-set code in {result.caveats}")
        return code

    def assert_refusal(self, action, expected_contract_code):
        code = self.refusal_code(action)
        self.assertEqual(expected_contract_code, code)
        self.assertIn(code, jev.CONTRACT_REASON_CODES)
        # Nothing reached the SDK, so nothing can be charged.
        self.assertEqual([], self.sdk.calls)

    def test_an_unknown_capability_id_is_refused_before_dispatch(self):
        self.assert_refusal(
            lambda: self.adapter().execute(self.request("jev.telepathy")),
            "CAPABILITY_UNKNOWN")

    def test_a_parameter_outside_the_reviewed_allowlist_is_refused(self):
        self.assert_refusal(
            lambda: self.adapter().execute(self.request(prompt="free text")),
            "CAPABILITY_PARAM_NOT_ALLOWED")

    def test_an_adapter_without_a_data_root_is_refused(self):
        self.assert_refusal(
            lambda: self.adapter(root=None).execute(self.request()),
            "CAPABILITY_NOT_AVAILABLE")

    def test_a_missing_credential_is_refused_before_dispatch(self):
        self.assert_refusal(
            lambda: self.adapter(api_key="").execute(self.request()),
            "CAPABILITY_NOT_AVAILABLE")

    def test_an_artifact_from_another_run_is_refused(self):
        self.assert_refusal(
            lambda: self.adapter().execute(self.request(artifact_id="art-not-here")),
            "CAPABILITY_PARAM_NOT_ALLOWED")

    def test_a_question_shape_the_builder_rejects_is_refused_not_raised_raw(self):
        self.assert_refusal(
            lambda: self.adapter().execute(self.request("jev.choice", options=[])),
            "CAPABILITY_PARAM_NOT_ALLOWED")

    def test_params_that_are_not_a_mapping_are_refused(self):
        request = Request(capability_id="jev.noul", params=[("artifact_id", "art-own")],
                          run_id="run-c", operation_id="op-x")
        self.assert_refusal(lambda: self.adapter().execute(request),
                            "CAPABILITY_PARAM_NOT_ALLOWED")


class CancelTests(ConformanceCase):
    def test_cancel_reports_that_there_is_nothing_to_cancel(self):
        result = self.adapter().cancel("req-fictional")
        self.assertEqual("BLOCKED", result.outcome)
        self.assertEqual("CAPABILITY_NOT_AVAILABLE", result.caveats[0])
        self.assertIn("adapter_local:JEV_CANCEL_NOT_SUPPORTED", result.caveats)

    def test_cancel_never_touches_the_sdk(self):
        self.adapter().cancel("req-fictional")
        self.assertEqual([], self.sdk.calls)


class ClassificationTests(unittest.TestCase):
    def test_every_refusal_code_maps_into_the_contract_closed_set(self):
        for local in jev.JEV_REFUSAL_CODES:
            with self.subTest(local=local):
                self.assertIn(jev.CONTRACT_CODE_FOR_JE[local], jev.CONTRACT_REASON_CODES)

    def test_the_new_codes_are_present_so_the_probe_can_classify_them(self):
        for code in ("JEV_CAPABILITY_UNKNOWN", "JEV_PARAM_NOT_ALLOWED",
                     "JEV_DATA_ROOT_ABSENT", "JEV_CANCEL_NOT_SUPPORTED"):
            self.assertIn(code, jev.JEV_REFUSAL_CODES)
            self.assertIn(code, jev.CONTRACT_CODE_FOR_JE)


if __name__ == "__main__":
    unittest.main()
