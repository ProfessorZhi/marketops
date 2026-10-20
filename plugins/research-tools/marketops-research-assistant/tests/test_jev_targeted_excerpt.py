"""Lane E: a Jev judgment must be able to read a hash-verified span, not only the head.

The production entry (`JevAdapter.execute`) used to call
`bind_evidence(root, run_id, artifact_id)` with no locator. `research_evidence.excerpts`
then returns the first ~180 characters, so a target sentence after a long navigation
prefix was never sent to the model — the judgment silently read the wrong material.

The fix keeps the binding rule intact: the caller supplies a *locator*, never the
text. The span is still extracted server-side from the saved, SHA-checked artifact;
an unknown locator is refused before any SDK call, because a locator that matches
nothing would otherwise be truncated into an empty span or, worse, silently fall
back to the head.

Every test here is offline: the SDK is a local fake and no credential or network
path is touched.
"""
from __future__ import annotations

import dataclasses
import hashlib
import inspect
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
import research_lite_store as store  # noqa: E402

#: A target far beyond the head-only excerpt window. The prefix contains no part of
#: the target phrase, so "the head" and "the located span" are distinguishable.
NAVIGATION = "".join(
    f"Navigation item {i:03d}: section link, legal notice and site index. "
    for i in range(180))
TARGET_QUERY = "Example Borough Council"
TARGET = ("The registered operator of Example Sports Ground is "
          "Example Borough Council, per filing 2026-10-20.")
DOCUMENT = NAVIGATION + " " + TARGET + " " + ("Trailing boilerplate. " * 40)

assert len(NAVIGATION) > 6000, len(NAVIGATION)
assert TARGET_QUERY not in NAVIGATION


# --------------------------------------------------------------------------- fakes
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


class RetryPolicy:
    def __init__(self, **kwargs):
        self.params = kwargs


class FakeSDK:
    """The subset of `typesafe_sdk` this lane reaches, with no vendor call."""

    def __init__(self, answer="operator", request_id="req-targeted-1"):
        self.answer = answer
        self.request_id = request_id
        self.calls: list[dict] = []

    def build(self) -> types.SimpleNamespace:
        sdk = self

        class TypeSafeClient:
            def __init__(self, **options):
                sdk.calls.append({"options": options})

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

            def system_one(self, state, questions, **kwargs):
                sdk.calls[-1]["state"] = state
                sdk.calls[-1]["questions"] = questions
                response = types.SimpleNamespace(
                    model="jev-latest", answers={}, choices={}, nouls={}, scores={},
                    boxes={}, usage=types.SimpleNamespace(input_tokens=1234,
                                                          output_tokens=7))
                response.request_id = sdk.request_id
                for name, question in questions.items():
                    if isinstance(question, Choice):
                        response.choices[name] = types.SimpleNamespace(
                            choice=sdk.answer, confidence=0.83,
                            probabilities={sdk.answer: 0.83})
                    elif isinstance(question, Noul):
                        response.nouls[name] = types.SimpleNamespace(noul=0.25)
                    elif isinstance(question, Score):
                        response.scores[name] = types.SimpleNamespace(
                            score=1.5, confidence=0.6, legend={0: "low", 1: "mid", 2: "high"},
                            probabilities={0: 0.2, 1: 0.6, 2: 0.2})
                return response

        return types.SimpleNamespace(
            Choice=Choice, Noul=Noul, Score=Score, RetryPolicy=RetryPolicy,
            TypeSafeClient=TypeSafeClient, DEFAULT_MODEL="jev-latest",
            DEFAULT_BASE_URL="https://api.example.invalid")


class Request:
    """Duck-typed §3.1 request: four read-only attributes and nothing else."""

    def __init__(self, capability_id, params, *, run_id="run-jev", operation_id="op-jev-1"):
        self.capability_id = capability_id
        self.params = params
        self.run_id = run_id
        self.operation_id = operation_id


class TargetedExcerptCase(unittest.TestCase):
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
        store.start_run(self.root, "run-jev", "GB-sports-grounds",
                        "Judge the operator", "SOP-PENDING")
        store.save_artifact(self.root, "run-jev", "art-register",
                            "fixture-public-html", "https://example.org/register",
                            DOCUMENT.encode("utf-8"), "raw_response")

    def use_sdk(self):
        sdk = FakeSDK()
        patcher = patch.dict(sys.modules, {"typesafe_sdk": sdk.build()})
        patcher.start()
        self.addCleanup(patcher.stop)
        return sdk

    def question(self):
        return jev.choice_question("judgment",
                                   "Who is recorded as the operator of the ground?",
                                   ["operator", "tenant"])

    def execute_request(self, *, evidence_query=None, **extra):
        params = {"artifact_id": "art-register",
                  "question": "Who is recorded as the operator of the ground?",
                  "options": ["operator", "tenant"]}
        if evidence_query is not None:
            params["evidence_query"] = evidence_query
        params.update(extra)
        return jev.JevAdapter(api_key="fictional-test-key", root=self.root).execute(
            Request("jev.choice", params))

    def run_bound(self, binding, *, operation_id="op-module-1"):
        sdk = self.use_sdk()
        result = jev.execute(
            self.root, run_id="run-jev", operation_id=operation_id,
            bindings=[binding], questions=[self.question()], api_key="fictional-test-key")
        return sdk, result


# -------------------------------------------------------------------- extraction
class HeadVersusTargetTests(TargetedExcerptCase):
    def test_a_head_binding_does_not_reach_a_target_after_a_long_prefix(self):
        """The defect, reproduced: no locator means the navigation prefix is read."""
        binding = jev.bind_evidence(self.root, "run-jev", "art-register")
        self.assertEqual("head", binding.locator_kind)
        self.assertNotIn(TARGET_QUERY, binding.span_text)
        self.assertLess(len(binding.span_text), 4000)
        self.assertIn("Navigation item 000", binding.span_text)

    def test_a_legitimate_locator_selects_the_verified_span_at_the_end(self):
        binding = jev.bind_evidence(self.root, "run-jev", "art-register",
                                    query=TARGET_QUERY)
        self.assertEqual("query", binding.locator_kind)
        self.assertEqual(TARGET_QUERY, binding.locator)
        self.assertIn(TARGET, binding.span_text)
        self.assertEqual(store.artifact_sha256(self.root, "run-jev", "art-register"),
                         binding.content_sha256)
        self.assertEqual(hashlib.sha256(binding.span_text.encode("utf-8")).hexdigest(),
                         binding.span_sha256)
        self.assertLessEqual(len(binding.span_text), jev.MAX_SPAN_CHARS)

    def test_the_locator_is_case_insensitive_but_never_content(self):
        binding = jev.bind_evidence(self.root, "run-jev", "art-register",
                                    query="example borough council")
        self.assertIn(TARGET, binding.span_text)
        self.assertEqual("example borough council", binding.locator)


# ------------------------------------------------------------------ execute path
class ExecuteLocatorTests(TargetedExcerptCase):
    def test_execute_sends_the_located_span_not_the_head(self):
        sdk = self.use_sdk()
        result = self.execute_request(evidence_query=TARGET_QUERY)
        self.assertEqual("OK", result["outcome"])
        self.assertEqual(1, len(sdk.calls))
        self.assertIn(TARGET, sdk.calls[0]["state"]["source_excerpt"])
        evidence = result["payload"]["evidence"][0]
        self.assertEqual("query", evidence["locator_kind"])
        self.assertEqual(TARGET_QUERY, evidence["locator"])
        self.assertEqual("art-register", evidence["artifact_id"])

    def test_execute_without_a_locator_stays_backward_compatible(self):
        sdk = self.use_sdk()
        result = self.execute_request()
        self.assertEqual("OK", result["outcome"])
        state = sdk.calls[0]["state"]["source_excerpt"]
        self.assertIn("Navigation item 000", state)
        self.assertNotIn(TARGET_QUERY, state)
        evidence = result["payload"]["evidence"][0]
        self.assertEqual("head", evidence["locator_kind"])
        self.assertEqual("", evidence["locator"])

    def test_the_call_reports_tokens_without_claiming_the_ledger_settled(self):
        self.use_sdk()
        result = self.execute_request(evidence_query=TARGET_QUERY)
        self.assertEqual(1234, result["payload"]["usage"]["input_tokens"])
        # IA-2: the adapter never prices its own call and never claims settlement.
        self.assertIsNone(result["actual_usd"])
        self.assertEqual("NONE", result["actual_source"])
        self.assertIsNone(result["quota_units_used"])
        # A judgment remains a suggestion, not source evidence or an SOP verdict.
        self.assertTrue(result["payload"]["judgment_only"])
        self.assertIn("JUDGMENT_IS_NOT_SOURCE_EVIDENCE", result["caveats"])

    def test_a_free_text_input_key_is_refused_before_dispatch(self):
        """`source_excerpt` / `document` must not become a second input channel."""
        for key in ("source_excerpt", "document", "text"):
            with self.subTest(key=key):
                sdk = self.use_sdk()
                with self.assertRaises(jev.JevRefusal) as ctx:
                    self.execute_request(evidence_query=TARGET_QUERY, **{key: DOCUMENT})
                self.assertEqual("JEV_PARAM_NOT_ALLOWED", ctx.exception.local_code)
                self.assertEqual("CAPABILITY_PARAM_NOT_ALLOWED",
                                 ctx.exception.reason_code)
                self.assertEqual([], sdk.calls)

    def test_execute_has_no_free_text_parameter(self):
        params = set(inspect.signature(jev.execute).parameters)
        self.assertNotIn("document", params)
        self.assertNotIn("source_excerpt", params)
        self.assertNotIn("text", params)

    def test_the_locator_is_not_allowed_on_a_box_capability(self):
        with self.assertRaises(jev.JevRefusal) as ctx:
            jev.JevAdapter(api_key="k", root=self.root).execute(Request(
                "jev.box", {"artifact_id": "art-register", "target": "the contact form",
                            "evidence_query": TARGET_QUERY}))
        self.assertEqual("JEV_PARAM_NOT_ALLOWED", ctx.exception.local_code)


# ------------------------------------------------------------------ fail closed
class LocatorRefusalTests(TargetedExcerptCase):
    BAD_LOCATORS = (
        ("too short", "a"),
        ("too long", "x" * (jev.MAX_LOCATOR_CHARS + 1)),
        ("blank", "   "),
        ("newline", "operator\nof ground"),
        ("tab", "operator\tof ground"),
        ("nul", "operator\x00ground"),
        ("no alphanumeric", "--- ---"),
        ("not a string", 123),
    )

    def test_bad_locators_are_refused_with_the_contract_code(self):
        for label, value in self.BAD_LOCATORS:
            with self.subTest(label=label):
                with self.assertRaises(jev.JevRefusal) as ctx:
                    jev.bind_evidence(self.root, "run-jev", "art-register",
                                      query=value)
                self.assertEqual("JEV_EVIDENCE_LOCATOR_INVALID",
                                 ctx.exception.local_code)
                self.assertEqual("CAPABILITY_PARAM_NOT_ALLOWED",
                                 ctx.exception.reason_code)

    def test_bad_locators_are_refused_before_the_sdk_is_reached(self):
        for label, value in self.BAD_LOCATORS:
            with self.subTest(label=label):
                sdk = self.use_sdk()
                with self.assertRaises(jev.JevRefusal) as ctx:
                    self.execute_request(evidence_query=value)
                self.assertEqual("JEV_EVIDENCE_LOCATOR_INVALID",
                                 ctx.exception.local_code)
                self.assertEqual([], sdk.calls)

    def test_a_locator_that_matches_nothing_fails_closed(self):
        with self.assertRaises(jev.JevRefusal) as ctx:
            jev.bind_evidence(self.root, "run-jev", "art-register",
                              query="phrase absent from saved material")
        self.assertEqual("JEV_EVIDENCE_LOCATOR_NO_MATCH", ctx.exception.local_code)
        self.assertEqual("CAPABILITY_PARAM_NOT_ALLOWED", ctx.exception.reason_code)

    def test_a_non_matching_execute_locator_never_dispatch(self):
        sdk = self.use_sdk()
        with self.assertRaises(jev.JevRefusal) as ctx:
            self.execute_request(evidence_query="phrase absent from saved material")
        self.assertEqual("JEV_EVIDENCE_LOCATOR_NO_MATCH", ctx.exception.local_code)
        self.assertEqual([], sdk.calls)


# ------------------------------------------------------- storage / evidence gate
class EvidenceGateTests(TargetedExcerptCase):
    def test_an_artifact_from_another_run_is_refused(self):
        store.start_run(self.root, "run-other", "GB-other", "Other run", "SOP-PENDING")
        store.save_artifact(self.root, "run-other", "art-other", "fixture-public-html",
                            "https://example.org/other", TARGET.encode("utf-8"),
                            "raw_response")
        with self.assertRaises(jev.JevRefusal) as ctx:
            jev.bind_evidence(self.root, "run-jev", "art-other", query=TARGET_QUERY)
        self.assertEqual("JEV_EVIDENCE_ARTIFACT_NOT_IN_RUN", ctx.exception.local_code)

    def test_a_cross_run_artifact_is_refused_from_execute(self):
        store.start_run(self.root, "run-other", "GB-other", "Other run", "SOP-PENDING")
        store.save_artifact(self.root, "run-other", "art-other", "fixture-public-html",
                            "https://example.org/other", TARGET.encode("utf-8"),
                            "raw_response")
        sdk = self.use_sdk()
        with self.assertRaises(jev.JevRefusal) as ctx:
            jev.JevAdapter(api_key="k", root=self.root).execute(Request(
                "jev.choice", {"artifact_id": "art-other",
                               "question": self.question().instructions,
                               "options": ["operator", "tenant"],
                               "evidence_query": TARGET_QUERY}))
        self.assertEqual("JEV_EVIDENCE_ARTIFACT_NOT_IN_RUN", ctx.exception.local_code)
        self.assertEqual([], sdk.calls)

    def test_a_rewritten_artifact_is_refused_at_verification(self):
        binding = jev.bind_evidence(self.root, "run-jev", "art-register",
                                    query=TARGET_QUERY)
        path = next((self.root / "artifacts" / "run-jev").glob("*.bin"))
        original = path.read_bytes()
        # Same length, different bytes: the check is the hash, not the size or mtime.
        path.write_bytes(b"Z" * len(original))
        sdk, result = self.run_bound(binding)
        self.assertEqual("BLOCKED", result["outcome"])
        self.assertIn("adapter_local:JEV_EVIDENCE_READBACK_MISMATCH",
                      result["caveats"])
        self.assertEqual([], sdk.calls)

    def test_a_locator_rewritten_after_binding_is_refused(self):
        binding = jev.bind_evidence(self.root, "run-jev", "art-register",
                                    query=TARGET_QUERY)
        forged = dataclasses.replace(binding, locator="Trailing boilerplate")
        sdk, result = self.run_bound(forged)
        self.assertEqual("BLOCKED", result["outcome"])
        self.assertIn("adapter_local:JEV_EVIDENCE_SPAN_MISMATCH", result["caveats"])
        self.assertEqual([], sdk.calls)

    def test_a_forged_content_hash_is_refused(self):
        binding = jev.bind_evidence(self.root, "run-jev", "art-register",
                                    query=TARGET_QUERY)
        forged = dataclasses.replace(binding, content_sha256="0" * 64)
        sdk, result = self.run_bound(forged)
        self.assertEqual("BLOCKED", result["outcome"])
        self.assertIn("adapter_local:JEV_EVIDENCE_READBACK_MISMATCH",
                      result["caveats"])
        self.assertEqual([], sdk.calls)

    def test_the_sdk_state_is_the_rederived_span_not_the_binding_text(self):
        """A hand-edited `span_text` changes nothing: the dispatch re-derives from disk."""
        binding = jev.bind_evidence(self.root, "run-jev", "art-register",
                                    query=TARGET_QUERY)
        forged = dataclasses.replace(binding, span_text="Agent supplied text " * 20)
        sdk, result = self.run_bound(forged)
        self.assertEqual("OK", result["outcome"])
        self.assertIn(TARGET, sdk.calls[0]["state"]["source_excerpt"])
        self.assertNotIn("Agent supplied text", sdk.calls[0]["state"]["source_excerpt"])


if __name__ == "__main__":
    unittest.main()
