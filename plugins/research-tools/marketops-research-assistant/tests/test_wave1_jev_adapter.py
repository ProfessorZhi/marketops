"""Wave-1 W-F: TypeSafe Jev adapter — artifact-bound input, Choice/Noul/Score/Box,
batching, usage + request_id accounting.

Every provider call is a fake `typesafe_sdk`; this file performs no network call,
holds no credential and spends nothing. The fake mirrors the signatures verified
by reading the installed SDK (typesafe-sdk 0.7.5, `_core/question_types.py`,
`_core/response_types.py`, `_core/client/sync/client.py`).

Audit F-6 is the defect under repair: today `_jev_choice` sends free Agent text as
`state`, so a judgment cannot be traced back to a stored artifact. Here the only way
to reach `execute` is with bindings minted from saved, SHA-checked artifacts.
"""
from __future__ import annotations

import inspect
import json
import os
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import research_adapter_jev as jev  # noqa: E402
import research_evidence_store as provenance  # noqa: E402
import research_lite_store as store  # noqa: E402

#: Long enough to clear the adapter's minimum evidence length, and distinctive so a
#: snippet search either finds exactly this text or finds nothing.
DOCUMENT = (
    "Official register extract. Example Borough Council is recorded as the operator "
    "of Example Sports Ground. The entry lists Grounds Management as the responsible "
    "department. Status: disputed pending a further filing."
)


# --------------------------------------------------------------------------- fakes
# The SDK shape is reproduced from the installed package, not invented: question
# objects take keyword-only `instructions`/`criteria`; the client is a context
# manager whose `system_one(state, questions, **kwargs)` returns one response with
# `model`, `usage`, an `answers` mapping, and per-primitive convenience views.

class _Question:
    def __init__(self, *, instructions=None, criteria=None):
        self.instructions = instructions
        self.criteria = criteria


class Choice(_Question):
    pass


class Noul(_Question):
    pass


class Score(_Question):
    pass


class Box(_Question):
    pass


class Image:
    def __init__(self, data):
        self.data = data


class RetryPolicy:
    def __init__(self, **kwargs):
        self.params = kwargs


class FakeSDK:
    """A parameterisable stand-in for `typesafe_sdk`."""

    def __init__(self, *, answer=None, box=False, usage=True, request_id="req-fake-1",
                 failure=None, model="jev-latest"):
        self.answer = answer
        self.failure = failure
        self.model = model
        self.request_id = request_id
        self.usage = usage
        self.calls: list[dict] = []
        self._box = box

    def build(self) -> types.SimpleNamespace:
        sdk = self

        class TypeSafeClient:
            """A real class, so the multi-question entry point can be introspected."""

            def __init__(self, **options):
                self._inner = _Client(sdk, options)

            def __enter__(self):
                return self._inner

            def __exit__(self, *exc):
                return False

            def system_one(self, state, questions, **kwargs):
                return self._inner.system_one(state, questions, **kwargs)

        module = types.SimpleNamespace(
            Choice=Choice, Noul=Noul, Score=Score, RetryPolicy=RetryPolicy,
            TypeSafeClient=TypeSafeClient, DEFAULT_MODEL="jev-latest",
            DEFAULT_BASE_URL="https://api.typesafe.ai",
        )
        if self._box:
            module.Box = Box
            module.Image = Image
        return module


class _Client:
    def __init__(self, sdk: FakeSDK, options: dict):
        self._sdk = sdk
        sdk.calls.append({"options": options})

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def system_one(self, state, questions, **kwargs):
        self._sdk.calls[-1]["state"] = state
        self._sdk.calls[-1]["questions"] = questions
        if self._sdk.failure is not None:
            raise self._sdk.failure
        response = types.SimpleNamespace(model=self._sdk.model, answers={},
                                         choices={}, nouls={}, scores={}, boxes={})
        if self._sdk.request_id is not None:
            response.request_id = self._sdk.request_id
        if self._sdk.usage:
            response.usage = types.SimpleNamespace(input_tokens=1234, output_tokens=7)
        else:
            response.usage = None
        # The real response groups answers by primitive, so a batch mixing them gets
        # one view per type rather than every answer under the first question's type.
        for name, question in questions.items():
            if isinstance(question, Choice):
                answer = self._sdk.answer or "operator"
                response.choices[name] = types.SimpleNamespace(
                    choice=answer, confidence=0.83, probabilities={answer: 0.83})
            elif isinstance(question, Noul):
                response.nouls[name] = types.SimpleNamespace(noul=0.25)
            elif isinstance(question, Score):
                response.scores[name] = types.SimpleNamespace(
                    score=1.5, confidence=0.6, legend={0: "low", 1: "mid", 2: "high"},
                    probabilities={0: 0.2, 1: 0.6, 2: 0.2})
            else:
                response.boxes[name] = types.SimpleNamespace(
                    box=types.SimpleNamespace(top=0.1, bottom=0.4, left=0.2, right=0.7),
                    confidence=0.55)
        return response


class JevAdapterCase(unittest.TestCase):
    """Shared fixture: one run and one saved text artifact, plus a fake SDK."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / "data"
        env = patch.dict(os.environ, {
            "MARKETOPS_RESEARCH_DATA_ROOT": str(self.root),
            "MARKETOPS_RESEARCH_ALLOW_NETWORK": "0",
            "MARKETOPS_SEARCH_ALLOW_NETWORK": "0",
        })
        env.start()
        self.addCleanup(env.stop)
        store.start_run(self.root, "run-jev", "GB-sports-grounds", "Judge the operator", "SOP-PENDING")
        store.save_artifact(self.root, "run-jev", "art-register", "fixture-public-html",
                            "https://example.org/register", DOCUMENT.encode("utf-8"),
                            "raw_response")
        self.binding = jev.bind_evidence(self.root, "run-jev", "art-register",
                                         query="operator of Example Sports Ground")

    def use_sdk(self, sdk: FakeSDK):
        patcher = patch.dict(sys.modules, {"typesafe_sdk": sdk.build()})
        patcher.start()
        self.addCleanup(patcher.stop)
        return sdk

    def execute(self, sdk, questions, *, bindings=None, key="fictional-test-key",
                operation_id="op-jev-1", **kwargs):
        return jev.execute(
            self.root, run_id="run-jev", operation_id=operation_id,
            bindings=bindings if bindings is not None else [self.binding],
            questions=questions, api_key=key, **kwargs)


# ------------------------------------------------------------------- capability
class CapabilityProbeTests(JevAdapterCase):
    def test_probe_reports_only_what_the_installed_sdk_actually_has(self):
        """A missing primitive is absent from the probe, never assumed present."""
        self.use_sdk(FakeSDK(box=False))
        caps = jev.sdk_capabilities()
        self.assertTrue(caps["installed"])
        self.assertEqual({"choice", "noul", "score"},
                         {k for k, v in caps["primitives"].items() if v})
        self.assertFalse(caps["box"])
        self.assertFalse(caps["images"])

    def test_probe_sees_box_and_images_on_an_sdk_that_ships_them(self):
        self.use_sdk(FakeSDK(box=True))
        caps = jev.sdk_capabilities()
        self.assertTrue(caps["box"])
        self.assertTrue(caps["images"])

    def test_probe_reports_absent_sdk_instead_of_guessing(self):
        patcher = patch.dict(sys.modules, {"typesafe_sdk": None})
        patcher.start()
        self.addCleanup(patcher.stop)
        caps = jev.sdk_capabilities()
        self.assertFalse(caps["installed"])
        self.assertFalse(any(caps["primitives"].values()))
        self.assertFalse(caps["box"])

    def test_probe_schema_refuses_to_fake_a_snapshot_without_a_key(self):
        """§6: an unauthorized handshake is AUTH_REQUIRED, not an invented tools/list."""
        adapter = jev.JevAdapter(api_key="")
        self.assertEqual({"status": "AUTH_REQUIRED"}, adapter.probe_schema())

    def test_capability_ids_have_no_box_entry_when_the_sdk_lacks_box(self):
        self.use_sdk(FakeSDK(box=False))
        self.assertNotIn("jev.box", jev.JevAdapter(api_key="k").capabilities())
        self.use_sdk(FakeSDK(box=True))
        self.assertIn("jev.box", jev.JevAdapter(api_key="k").capabilities())


# --------------------------------------------------------------------- binding
class EvidenceBindingTests(JevAdapterCase):
    def test_binding_records_the_saved_bytes_hash_and_the_extracted_span(self):
        self.assertEqual("art-register", self.binding.artifact_id)
        self.assertEqual("run-jev", self.binding.run_id)
        # The hash is the SAVED file's, read back from disk, not a hash of what the
        # caller said it saved.
        self.assertEqual(store.artifact_sha256(self.root, "run-jev", "art-register"),
                         self.binding.content_sha256)
        self.assertIn("operator of Example Sports Ground", self.binding.span_text)
        self.assertEqual("server:research_evidence.excerpts", self.binding.extracted_by)
        self.assertEqual("query", self.binding.locator_kind)

    def test_binding_rejects_an_artifact_from_another_run(self):
        with self.assertRaises(ValueError) as ctx:
            jev.bind_evidence(self.root, "run-jev", "art-not-here")
        self.assertEqual("CAPABILITY_PARAM_NOT_ALLOWED", str(ctx.exception).split(":", 1)[0])
        self.assertIn("adapter_local=JEV_EVIDENCE_ARTIFACT_NOT_IN_RUN", str(ctx.exception))

    def test_binding_rejects_an_unsaved_artifact_and_free_text(self):
        for bad in ("", "not-an-artifact", "../etc/passwd", DOCUMENT):
            with self.subTest(value=bad[:20]):
                with self.assertRaises(ValueError):
                    jev.bind_evidence(self.root, "run-jev", bad)

    def test_there_is_no_entry_point_that_accepts_free_text_as_the_judgment_input(self):
        """The audit's defect: `_jev_choice(document=<agent text>)`. No such parameter exists."""
        self.use_sdk(FakeSDK())
        params = set(inspect.signature(jev.execute).parameters)
        self.assertNotIn("document", params)
        self.assertNotIn("source_excerpt", params)
        self.assertNotIn("text", params)
        # A raw string is not accepted where a binding is required.
        with self.assertRaises((TypeError, ValueError)):
            jev.execute(self.root, run_id="run-jev", operation_id="op-free",
                        bindings=[DOCUMENT],
                        questions=[jev.choice_question("q", "Which one?", ["a", "b"])],
                        api_key="k")

    def test_a_tampered_artifact_blocks_the_call_before_the_sdk_is_reached(self):
        """The span is re-derived from disk, so stored bytes and sent bytes cannot diverge."""
        sdk = self.use_sdk(FakeSDK())
        path = next((self.root / "artifacts" / "run-jev").glob("*.bin"))
        path.write_bytes(b"Official register extract. Rewritten after binding..........")
        result = self.execute(sdk, [jev.choice_question("judgment", "Which one?", ["a", "b"])])
        self.assertEqual("BLOCKED", result["outcome"])
        self.assertEqual("CAPABILITY_PARAM_NOT_ALLOWED", result["caveats"][0])
        self.assertIn("adapter_local:JEV_EVIDENCE_READBACK_MISMATCH", result["caveats"])
        self.assertEqual([], sdk.calls)

    def test_a_forged_span_hash_is_refused(self):
        sdk = self.use_sdk(FakeSDK())
        forged = self.binding.__class__(**{
            **{f: getattr(self.binding, f) for f in self.binding.__dataclass_fields__},
            "span_sha256": "0" * 64,
        })
        result = self.execute(sdk, [jev.choice_question("judgment", "Which one?", ["a", "b"])],
                              bindings=[forged])
        self.assertEqual("BLOCKED", result["outcome"])
        self.assertEqual("CAPABILITY_PARAM_NOT_ALLOWED", result["caveats"][0])
        self.assertIn("adapter_local:JEV_EVIDENCE_SPAN_MISMATCH", result["caveats"])
        self.assertEqual([], sdk.calls)


# ------------------------------------------------------------------- primitives
class PrimitiveTests(JevAdapterCase):
    def test_choice_preserves_the_selection_with_confidence_and_probabilities(self):
        sdk = self.use_sdk(FakeSDK(answer="operator"))
        result = self.execute(sdk, [jev.choice_question(
            "marketops_judgment", "Who operates the ground?", ["operator", "tenant"])])
        self.assertEqual("OK", result["outcome"])
        judgment = result["payload"]["judgments"]["marketops_judgment"]
        self.assertEqual("choice", judgment["primitive"])
        self.assertEqual("operator", judgment["choice"])
        self.assertEqual(0.83, judgment["confidence"])
        self.assertEqual({"operator": 0.83}, judgment["probabilities"])
        self.assertTrue(judgment["judgment_only"])

    def test_choice_refuses_an_option_the_model_did_not_declare(self):
        sdk = self.use_sdk(FakeSDK(answer="invented-by-the-model"))
        result = self.execute(sdk, [jev.choice_question("q", "Which one?", ["a", "b"])])
        self.assertEqual("FAILED", result["outcome"])
        # An outcome is not a refusal: it keeps its own code first and carries no
        # §5.3 member, because the gateway never reads a refusal off a FAILED.
        self.assertEqual("JEV_RETURNED_UNDECLARED_OPTION", result["caveats"][0])
        self.assertNotIn(result["caveats"][0], jev.CONTRACT_REASON_CODES)

    def test_noul_preserves_the_probability(self):
        sdk = self.use_sdk(FakeSDK())
        result = self.execute(sdk, [jev.noul_question("q", "Is a filing outstanding?")])
        self.assertEqual("OK", result["outcome"])
        self.assertEqual(0.25, result["payload"]["judgments"]["q"]["noul"])

    def test_score_preserves_the_expected_score_and_rubric(self):
        sdk = self.use_sdk(FakeSDK())
        result = self.execute(sdk, [jev.score_question(
            "q", "How strong is the support?", ["low support", "some support", "strong support"])])
        self.assertEqual("OK", result["outcome"])
        judgment = result["payload"]["judgments"]["q"]
        self.assertEqual(1.5, judgment["score"])
        self.assertEqual(3, judgment["level_count"])
        self.assertEqual({0: "low", 1: "mid", 2: "high"}, judgment["legend"])
        self.assertEqual({0: 0.2, 1: 0.6, 2: 0.2}, judgment["probabilities"])

    def test_noul_descriptions_are_passed_through_when_given(self):
        sdk = self.use_sdk(FakeSDK())
        self.execute(sdk, [jev.noul_question("q", "Is the operator known?",
                                             true_description="A named operator is recorded",
                                             false_description="No operator is named")])
        question = sdk.calls[0]["questions"]["q"]
        self.assertEqual({"true": "A named operator is recorded",
                          "false": "No operator is named"}, question.criteria)

    def test_empty_criteria_are_refused_before_any_sdk_call(self):
        sdk = self.use_sdk(FakeSDK())
        with self.assertRaises(ValueError) as ctx:
            jev.choice_question("q", "Which one?", [])
        self.assertEqual("CAPABILITY_PARAM_NOT_ALLOWED", str(ctx.exception).split(":", 1)[0])
        self.assertIn("adapter_local=JEV_OPTIONS_INVALID", str(ctx.exception))
        with self.assertRaises(ValueError):
            jev.score_question("q", "How strong?", [])
        self.assertEqual([], sdk.calls)


# -------------------------------------------------------------------- batching
class BatchingTests(JevAdapterCase):
    def test_several_questions_ride_one_call_and_share_one_usage_record(self):
        sdk = self.use_sdk(FakeSDK())
        questions = [
            jev.choice_question("operates", "Which entity operates it?", ["operator", "tenant"]),
            jev.noul_question("disputed", "Is the status disputed?"),
            jev.score_question("support", "How strong is the support?", ["low", "mid", "high"]),
        ]
        result = self.execute(sdk, questions)
        self.assertEqual("OK", result["outcome"])
        self.assertEqual(1, len(sdk.calls), "one request, not one per question")
        self.assertEqual(3, len(result["payload"]["judgments"]))
        self.assertEqual(3, result["payload"]["usage"]["questions_charged_once"])
        self.assertTrue(result["payload"]["usage"]["input_tokens_shared_across_questions"])
        # The single input is counted once; it is not multiplied across questions.
        self.assertEqual(1234, result["payload"]["usage"]["input_tokens"])

    def test_batching_more_than_the_declared_cap_is_refused_without_an_sdk_call(self):
        sdk = self.use_sdk(FakeSDK())
        questions = [jev.noul_question(f"q{i}", "Is this recorded as so?") for i in range(jev.MAX_QUESTIONS + 1)]
        with self.assertRaises(ValueError) as ctx:
            self.execute(sdk, questions)
        self.assertEqual("CAPABILITY_PARAM_NOT_ALLOWED", str(ctx.exception).split(":", 1)[0])
        self.assertIn("adapter_local=JEV_BATCH_TOO_LARGE", str(ctx.exception))
        self.assertEqual([], sdk.calls)

    def test_a_batch_mixing_box_with_a_text_question_is_refused(self):
        """Box reads an image; one shared state cannot be both text and image."""
        sdk = self.use_sdk(FakeSDK(box=True))
        with self.assertRaises(ValueError) as ctx:
            self.execute(sdk, [jev.noul_question("q", "Is this recorded as so?"),
                               jev.box_question("b", "Locate the contact form",
                                                image_artifact_id="art-x")])
        self.assertEqual("CAPABILITY_PARAM_NOT_ALLOWED", str(ctx.exception).split(":", 1)[0])
        self.assertIn("adapter_local=JEV_BOX_CANNOT_SHARE_A_TEXT_BATCH", str(ctx.exception))
        self.assertEqual([], sdk.calls)


# ------------------------------------------------------------------------- Box
class BoxTests(JevAdapterCase):
    def _save_image(self, artifact_id="art-shot", kind="image"):
        png = (b"\x89PNG\r\n\x1a\n" + b"\x00" * 64)
        store.save_artifact(self.root, "run-jev", artifact_id, "fixture-browser-shot",
                            "https://example.org/register", png, kind)
        return artifact_id

    def test_box_is_unsupported_on_an_sdk_that_does_not_ship_it(self):
        sdk = self.use_sdk(FakeSDK(box=False))
        image_id = self._save_image()
        result = self.execute(sdk, [jev.box_question("form", "The contact form",
                                                     image_artifact_id=image_id)])
        self.assertEqual("BLOCKED", result["outcome"])
        self.assertEqual("CAPABILITY_NOT_AVAILABLE", result["caveats"][0])
        self.assertIn("adapter_local:JEV_BOX_UNSUPPORTED_BY_SDK", result["caveats"])
        self.assertEqual([], sdk.calls)

    def test_box_on_a_text_artifact_is_blocked_rather_than_answered(self):
        sdk = self.use_sdk(FakeSDK(box=True))
        result = self.execute(sdk, [jev.box_question("form", "The contact form",
                                                     image_artifact_id="art-register")])
        self.assertEqual("BLOCKED", result["outcome"])
        self.assertEqual("CAPABILITY_PARAM_NOT_ALLOWED", result["caveats"][0])
        self.assertIn("adapter_local:JEV_BOX_REQUIRES_AN_IMAGE_ARTIFACT", result["caveats"])
        self.assertEqual([], sdk.calls)

    def test_box_on_a_saved_image_returns_coordinates_and_confidence(self):
        sdk = self.use_sdk(FakeSDK(box=True))
        image_id = self._save_image()
        binding = jev.bind_evidence(self.root, "run-jev", image_id)
        result = self.execute(sdk, [jev.box_question("form", "The contact form",
                                                     image_artifact_id=image_id)],
                              bindings=[binding])
        self.assertEqual("OK", result["outcome"])
        box = result["payload"]["judgments"]["form"]["box"]
        self.assertEqual({"top": 0.1, "bottom": 0.4, "left": 0.2, "right": 0.7}, box)
        self.assertEqual(0.55, result["payload"]["judgments"]["form"]["confidence"])
        # The image travels as an asset in `state`; no text was smuggled in beside it.
        self.assertEqual("Image", type(sdk.calls[0]["state"]).__name__)


# ---------------------------------------------------------------------- usage
class UsageAccountingTests(JevAdapterCase):
    def test_usage_and_request_id_are_recorded_when_the_provider_reports_them(self):
        sdk = self.use_sdk(FakeSDK(request_id="req-fake-1"))
        result = self.execute(sdk, [jev.noul_question("q", "Is this recorded as so?")])
        self.assertEqual("req-fake-1", result["upstream_ref"])
        self.assertEqual("req-fake-1", result["payload"]["upstream_ref"])
        self.assertEqual(1234, result["payload"]["usage"]["input_tokens"])
        self.assertEqual(7, result["payload"]["usage"]["output_tokens"])
        self.assertNotIn("REQUEST_ID_UNAVAILABLE", result["caveats"])

    def test_an_absent_request_id_is_recorded_as_absent_and_never_invented(self):
        sdk = self.use_sdk(FakeSDK(request_id=None))
        result = self.execute(sdk, [jev.noul_question("q", "Is this recorded as so?")])
        self.assertEqual("OK", result["outcome"])
        self.assertIsNone(result["upstream_ref"])
        self.assertIn("REQUEST_ID_UNAVAILABLE", result["caveats"])

    def test_missing_usage_is_reported_as_absent_rather_than_zero(self):
        sdk = self.use_sdk(FakeSDK(usage=False))
        result = self.execute(sdk, [jev.noul_question("q", "Is this recorded as so?")])
        self.assertIsNone(result["payload"]["usage"]["input_tokens"])
        self.assertIn("USAGE_UNAVAILABLE", result["caveats"])

    def test_the_adapter_reports_tokens_and_leaves_pricing_to_the_ledger(self):
        """IA-2: the adapter is not the pricing authority, so it does not invent USD."""
        sdk = self.use_sdk(FakeSDK())
        result = self.execute(sdk, [jev.noul_question("q", "Is this recorded as so?")])
        self.assertIsNone(result["actual_usd"])
        self.assertEqual("NONE", result["actual_source"])
        self.assertEqual("python_sdk", result["transport"])

    def test_an_alias_model_is_flagged_rather_than_presented_as_a_pinned_version(self):
        sdk = self.use_sdk(FakeSDK())
        result = self.execute(sdk, [jev.noul_question("q", "Is this recorded as so?")])
        self.assertIn("MODEL_IS_AN_ALIAS_NOT_A_PINNED_VERSION", result["caveats"])
        pinned = self.execute(sdk, [jev.noul_question("q", "Is this recorded as so?")],
                              operation_id="op-jev-pinned", model="jev-2026-01-01")
        self.assertNotIn("MODEL_IS_AN_ALIAS_NOT_A_PINNED_VERSION", pinned["caveats"])
        self.assertEqual("jev-2026-01-01", sdk.calls[-1]["options"]["model"])


# ---------------------------------------------------------- run / storage safety
class StorageAndSafetyTests(JevAdapterCase):
    def test_the_adapter_never_retries_inside_the_call(self):
        """IA-3: one dispatch, one charge. `max_retries=0` is not negotiable."""
        sdk = self.use_sdk(FakeSDK())
        self.execute(sdk, [jev.noul_question("q", "Is this recorded as so?")])
        self.assertEqual({"max_retries": 0}, sdk.calls[0]["options"]["retry"].params)

    def test_a_missing_credential_blocks_before_the_sdk_is_constructed(self):
        sdk = self.use_sdk(FakeSDK())
        result = self.execute(sdk, [jev.noul_question("q", "Is this recorded as so?")], key="")
        self.assertEqual("BLOCKED", result["outcome"])
        self.assertEqual("CAPABILITY_NOT_AVAILABLE", result["caveats"][0])
        self.assertIn("adapter_local:JEV_CREDENTIAL_ABSENT", result["caveats"])
        self.assertEqual([], sdk.calls)

    def test_an_upstream_timeout_is_unknown_and_is_not_retried(self):
        sdk = self.use_sdk(FakeSDK(failure=TimeoutError("read timed out")))
        result = self.execute(sdk, [jev.noul_question("q", "Is this recorded as so?")])
        self.assertEqual("UNKNOWN", result["outcome"])
        self.assertEqual(1, len(sdk.calls))

    def test_the_saved_artifact_is_the_decision_response_and_the_record_stays_candidate(self):
        sdk = self.use_sdk(FakeSDK())
        result = self.execute(sdk, [jev.noul_question("q", "Is this recorded as so?")])
        self.assertEqual(1, len(result["artifacts"]))
        saved = result["artifacts"][0]
        self.assertEqual("decision_response", saved["material_kind"])
        self.assertEqual("SAVED_NOT_BUSINESS_VERIFIED", saved["storage_status"])
        snapshot = store.list_run(self.root, "run-jev", limit=50)
        record = next(r for r in snapshot["records"] if r["record_id"].startswith("rcd-"))
        self.assertEqual("observation", record["kind"])
        self.assertEqual("candidate_with_material", record["status"])
        self.assertIn(saved["artifact_id"], record["artifact_ids"])
        for forbidden in ("verified", "approved", "send_authorized", "ready", "reviewed_by"):
            self.assertNotIn(forbidden, record["data"])

    def test_the_candidate_record_points_at_the_evidence_artifact_it_was_judged_on(self):
        sdk = self.use_sdk(FakeSDK())
        self.execute(sdk, [jev.noul_question("q", "Is this recorded as so?")])
        snapshot = store.list_run(self.root, "run-jev", limit=50)
        record = next(r for r in snapshot["records"] if r["record_id"].startswith("rcd-"))
        self.assertIn("art-register", record["artifact_ids"])
        links = provenance.evidence_links(self.root, "run-jev")
        link = next(row for row in links if row["record_id"] == record["record_id"])
        self.assertEqual("art-register", link["artifact_id"])
        self.assertEqual(self.binding.span_sha256, link["span_sha256"])
        self.assertEqual("query", link["locator_kind"])

    def test_no_credential_value_appears_anywhere_in_the_result(self):
        sdk = self.use_sdk(FakeSDK())
        secret = "typesafe-live-key-should-never-be-echoed"
        result = self.execute(sdk, [jev.noul_question("q", "Is this recorded as so?")], key=secret)
        self.assertNotIn(secret, json.dumps(result, default=str))
        snapshot = store.list_run(self.root, "run-jev", limit=50)
        self.assertNotIn(secret, json.dumps(snapshot, default=str))


# ------------------------------------------------------------------- envelopes
CONTRACT_ENVELOPE_FIELDS = (
    "evidence_id", "run_id", "operation_id", "ledger_id", "provider", "capability_id",
    "source_url", "retrieved_at", "content_sha256", "raw_artifact_ref", "derived_refs",
    "extraction_method", "verified_state", "candidate_only", "egress_decision_ref",
    "caveats",
)


class EvidenceEnvelopeTests(JevAdapterCase):
    def test_the_envelope_carries_exactly_the_frozen_contract_field_set(self):
        sdk = self.use_sdk(FakeSDK())
        result = self.execute(sdk, [jev.noul_question("q", "Is this recorded as so?")])
        envelope = result["envelopes"][0]
        self.assertEqual(set(CONTRACT_ENVELOPE_FIELDS), set(envelope))

    def test_the_envelope_is_candidate_only_and_carries_no_ledger_link_yet(self):
        sdk = self.use_sdk(FakeSDK())
        result = self.execute(sdk, [jev.noul_question("q", "Is this recorded as so?")])
        envelope = result["envelopes"][0]
        self.assertTrue(envelope["candidate_only"])
        self.assertIsNone(envelope["ledger_id"])
        self.assertIn("LEDGER_NOT_WIRED", envelope["caveats"])
        self.assertEqual("UNVERIFIED", envelope["verified_state"])

    def test_the_envelope_names_the_span_the_judgment_actually_read(self):
        sdk = self.use_sdk(FakeSDK())
        result = self.execute(sdk, [jev.noul_question("q", "Is this recorded as so?")])
        envelope = result["envelopes"][0]
        self.assertEqual("art-register", envelope["raw_artifact_ref"])
        self.assertEqual(store.artifact_sha256(self.root, "run-jev", "art-register"),
                         envelope["content_sha256"])
        self.assertEqual("server:research_evidence.excerpts", envelope["extraction_method"])


# ------------------------------------------------------------- box / image store
class InstalledSdkTests(unittest.TestCase):
    """Checks that run against whatever `typesafe_sdk` this interpreter actually has.

    These are the only tests here that do not install a fake. They make no network
    call and hold no credential: constructing a question object and validating it is
    pure local work, so the question shape this adapter emits can be checked against
    the real upstream model instead of against a fake of our own making. Run under
    0.7.1 and 0.7.5 the pair below is also the receipt for the capability check.
    """

    def setUp(self):
        try:
            import typesafe_sdk
        except ImportError:  # pragma: no cover - depends on the interpreter
            self.skipTest("typesafe_sdk is not installed in this interpreter")
        self.sdk = typesafe_sdk

    def test_the_probe_reports_the_installed_package_rather_than_a_version_string(self):
        caps = jev.sdk_capabilities()
        self.assertTrue(caps["installed"])
        for primitive in ("choice", "noul", "score"):
            self.assertTrue(caps["primitives"][primitive], primitive)
        self.assertTrue(caps["batch"], "system_one takes a Mapping of questions")
        self.assertIsInstance(caps["version"], str)
        # Box follows the package: absent in 0.7.1, present in 0.7.5, and the probe
        # tracks the class object rather than a version comparison we wrote.
        self.assertEqual(hasattr(self.sdk, "Box"), caps["box"])
        self.assertEqual(hasattr(self.sdk, "Image"), caps["images"])
        self.assertEqual(hasattr(self.sdk, "Box"),
                         "jev.box" in jev.JevAdapter(api_key="k").capabilities())

    def test_the_installed_sdk_accepts_the_question_objects_this_adapter_builds(self):
        """The upstream model validates our question shape, not our fake of it."""
        asked = [
            jev.choice_question("operates", "Which entity operates the ground?",
                                ["operator", "tenant"],
                                descriptions={"operator": "The operator itself"}),
            jev.noul_question("disputed", "Is the recorded status disputed?",
                              true_description="A filing disputes it",
                              false_description="Nothing disputes it"),
            jev.score_question("support", "How strong is the recorded support?",
                               ["low", "moderate", "strong"]),
        ]
        built = jev.sdk_questions(asked)
        self.assertEqual(set(built), {"operates", "disputed", "support"})
        for name, question in built.items():
            self.assertTrue(hasattr(question, "instructions"), name)
        try:
            from typesafe_sdk._core.questions import normalize_questions
        except ImportError:  # pragma: no cover - a private path that may move
            self.skipTest("normalize_questions is not reachable in this SDK version")
        # Raises TypeSafeError when the SDK rejects the shape we built.
        self.assertEqual(set(normalize_questions(built)), set(built))

    def test_box_objects_are_built_only_where_the_package_has_box(self):
        if not hasattr(self.sdk, "Box"):
            self.skipTest("this SDK version has no Box")
        built = jev.sdk_questions([jev.box_question("form", "Locate the contact form",
                                                    image_artifact_id="art-shot")])
        self.assertEqual("box", getattr(built["form"], "type", None))


class ImageArtifactStoreTests(JevAdapterCase):
    def test_an_image_artifact_is_stored_and_read_back_by_hash(self):
        png = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32
        saved = store.save_artifact(self.root, "run-jev", "art-shot", "fixture-browser-shot",
                                    "https://example.org/register", png, "image")
        self.assertEqual("image", saved["material_kind"])
        self.assertTrue(store.is_image_artifact(self.root, "run-jev", "art-shot"))

    def test_a_non_image_claiming_the_image_kind_is_refused(self):
        with self.assertRaises(ValueError) as ctx:
            store.save_artifact(self.root, "run-jev", "art-not-image", "fixture",
                                "https://example.org/x", b"plain text, not a picture", "image")
        self.assertEqual("IMAGE_ARTIFACT_REQUIRES_IMAGE_BYTES", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
