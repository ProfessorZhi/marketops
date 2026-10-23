"""Wave-1 W-F round 2: the Jev adapter's refusal channel must be the contract's.

The integrator's `research_gateway.py` has two hard rules for a provider adapter,
both read straight off its source:

* `_adapter_failure` — a raised `ValueError` is the repository's refusal channel, and
  `_reason_code_from` takes the text before the first colon. If that code is not in
  the §5.3 closed set the gateway raises `ContractViolation`.
* `_result_refusal_code` — a BLOCKED `AdapterResult` must carry a §5.3 member in
  `caveats`, again on pain of `ContractViolation`. It reads the attribute
  (`getattr(result, "caveats", ())`), and `getattr(result, "outcome", ...)` likewise.

This adapter had neither: its local `JEV_*` diagnostics are not contract codes, and it
returned a plain `dict`, which has no `.outcome` or `.caveats` at all — so a BLOCKED
result would have been silently read as FAILED. Both are defects, and this file is the
litmus for them.
"""
from __future__ import annotations

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

#: §5.3 of the frozen interface contract, transcribed. The cross-check below compares
#: this against the integrator's own set when that module is importable, so the two
#: copies cannot drift without a test failing.
CONTRACT_SECTION_5_3 = frozenset({
    "POLICY_TIER_NOT_GRANTED", "POLICY_DOMAIN_NOT_ALLOWED", "POLICY_GRANT_EXPIRED",
    "POLICY_GRANT_REVOKED", "BUDGET_RUN_CEILING", "BUDGET_PROVIDER_CEILING",
    "BUDGET_PROJECT_CEILING", "BUDGET_OPERATION_CEILING", "PRICE_UNKNOWN_FAIL_CLOSED",
    "PRICING_SOURCE_MISSING", "DUPLICATE_OPERATION", "OPERATION_ID_COLLISION",
    "HOST_PREVIOUSLY_BLOCKED", "CAPABILITY_UNKNOWN", "CAPABILITY_PARAM_NOT_ALLOWED",
    "CAPABILITY_SCHEMA_DRIFT", "CAPABILITY_NOT_AVAILABLE", "EGRESS_REFUSED",
    "CRAWL4AI_LOCAL_NOT_INSTALLED", "UNKNOWN_CHARGE_OUTSTANDING",
    "RESEARCH_NETWORK_DISABLED", "WALLET_PRECONDITION_FAILED", "GRANT_SIGNATURE_INVALID",
})

DOCUMENT = ("Official register extract. Example Borough Council is recorded as the "
            "operator of Example Sports Ground. Status: disputed.")


class _Question:
    def __init__(self, *, instructions=None, criteria=None):
        self.instructions, self.criteria = instructions, criteria


def _fake_sdk(*, box=False):
    calls = []

    class TypeSafeClient:
        def __init__(self, **options):
            calls.append(options)

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def system_one(self, state, questions, **kwargs):
            raise AssertionError("the SDK must not be reached on a refused call")

    module = types.SimpleNamespace(
        Choice=_Question, Noul=_Question, Score=_Question, RetryPolicy=_Question,
        TypeSafeClient=TypeSafeClient, DEFAULT_MODEL="jev-latest",
        DEFAULT_BASE_URL="https://api.typesafe.ai")
    if box:
        module.Box = _Question
        module.Image = _Question
    return module, calls


class RefusalChannelCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / "data"
        env = patch.dict(os.environ, {"MARKETOPS_RESEARCH_DATA_ROOT": str(self.root)})
        env.start()
        self.addCleanup(env.stop)
        store.start_run(self.root, "run-r", "GB", "Judge", "SOP")
        store.save_artifact(self.root, "run-r", "art-1", "fixture",
                            "https://example.org/r", DOCUMENT.encode(), "raw_response")

    def use_sdk(self, *, box=False):
        module, calls = _fake_sdk(box=box)
        patcher = patch.dict(sys.modules, {"typesafe_sdk": module})
        patcher.start()
        self.addCleanup(patcher.stop)
        return calls

    def execute(self, questions, *, bindings=None, key="k", operation_id="op-1"):
        return jev.execute(self.root, run_id="run-r", operation_id=operation_id,
                           bindings=bindings if bindings is not None else [self.binding],
                           questions=questions, api_key=key)

    def setUp_binding(self):
        self.binding = jev.bind_evidence(self.root, "run-r", "art-1")


class MappingTableTests(RefusalChannelCase):
    def test_every_local_refusal_code_maps_into_the_contract_closed_set(self):
        self.assertEqual(set(jev.JEV_REFUSAL_CODES), set(jev.CONTRACT_CODE_FOR_JE))
        for local, contract in jev.CONTRACT_CODE_FOR_JE.items():
            with self.subTest(local=local):
                self.assertIn(contract, CONTRACT_SECTION_5_3)

    def test_the_two_contract_sets_agree_when_the_integrator_module_is_importable(self):
        """Skips on a worker branch, runs once the integrator's module is beside us."""
        try:
            import research_gateway
        except ImportError:  # pragma: no cover - depends on which branch this is
            self.skipTest("research_gateway is not on this branch")
        self.assertEqual(set(research_gateway.REASON_CODES), set(CONTRACT_SECTION_5_3))
        self.assertEqual(set(jev.CONTRACT_REASON_CODES), set(research_gateway.REASON_CODES))


class BlockedResultTests(RefusalChannelCase):
    def setUp(self):
        super().setUp()
        self.setUp_binding()

    def test_a_missing_credential_reports_a_contract_code_first(self):
        self.use_sdk()
        result = self.execute([jev.noul_question("q", "Is this recorded as so?")], key="")
        self.assertEqual("BLOCKED", result["outcome"])
        self.assertIn(result["caveats"][0], CONTRACT_SECTION_5_3)
        self.assertIn("adapter_local:JEV_CREDENTIAL_ABSENT", result["caveats"])

    def test_every_blocked_path_puts_a_contract_code_in_caveats(self):
        """The mapping is worthless if one path forgets to use it."""
        cases = {}
        self.setUp_binding()

        self.use_sdk(box=False)
        cases["box on an SDK without Box"] = self.execute(
            [jev.box_question("form", "Locate the contact form", image_artifact_id="art-1")],
            bindings=[jev.bind_evidence(self.root, "run-r", "art-1")], operation_id="op-box")

        store.save_artifact(self.root, "run-r", "art-shot", "fixture-shot",
                            "https://example.org/shot", b"\x89PNG\r\n\x1a\n" + b"\x00" * 32,
                            "image")
        image_binding = jev.bind_evidence(self.root, "run-r", "art-shot")
        cases["text judgment given an image binding"] = self.execute(
            [jev.noul_question("q", "Is this recorded as so?")], bindings=[image_binding],
            operation_id="op-img")

        # Tamper: the re-derived span no longer matches the binding. Artifact files
        # are named by a digest of their id, so directory order says nothing about
        # which artifact a binding points at — take the one this case is bound to.
        bound = DOCUMENT.encode()
        path = next(p for p in (self.root / "artifacts" / "run-r").glob("*.bin")
                    if p.read_bytes() == bound)
        path.write_bytes(b"Official register extract. Rewritten after the binding.")
        cases["tampered artifact"] = self.execute(
            [jev.noul_question("q", "Is this recorded as so?")], operation_id="op-tamper")

        for label, result in cases.items():
            with self.subTest(case=label):
                self.assertEqual("BLOCKED", result["outcome"], label)
                self.assertIn(result["caveats"][0], CONTRACT_SECTION_5_3, label)
                self.assertTrue(
                    any(c.startswith("adapter_local:") for c in result["caveats"]), label)

    def test_a_blocked_result_is_readable_the_way_the_gateway_reads_it(self):
        """`_result_refusal_code` uses getattr, so a plain dict is not enough."""
        self.use_sdk()
        result = self.execute([jev.noul_question("q", "Is this recorded as so?")], key="")
        self.assertEqual("BLOCKED", getattr(result, "outcome", None))
        self.assertTrue(tuple(getattr(result, "caveats", ())))
        self.assertIn(getattr(result, "caveats")[0], CONTRACT_SECTION_5_3)

    def test_the_gateway_refusal_reader_accepts_the_result_shape(self):
        """Skips unless the integrator's gateway is importable beside this module."""
        try:
            import research_gateway
        except ImportError:  # pragma: no cover
            self.skipTest("research_gateway is not on this branch")
        self.use_sdk()
        result = self.execute([jev.noul_question("q", "Is this recorded as so?")], key="")
        code = research_gateway.CapabilityGateway._result_refusal_code(result)
        self.assertEqual("CAPABILITY_NOT_AVAILABLE", code)


class RaisedRefusalTests(RefusalChannelCase):
    def setUp(self):
        super().setUp()
        self.setUp_binding()

    def test_a_raised_refusal_opens_with_a_contract_code(self):
        """`_reason_code_from` splits on the first colon; anything else is a violation."""
        cases = {
            "batch too large": lambda: self.execute(
                [jev.noul_question(f"q{i}", "Is this recorded as so?")
                 for i in range(jev.MAX_QUESTIONS + 1)], operation_id="op-batch"),
            "box mixed into a text batch": lambda: self.execute(
                [jev.noul_question("q", "Is this recorded as so?"),
                 jev.box_question("b", "Locate the contact form", image_artifact_id="art-1")],
                operation_id="op-mix"),
        }
        for label, call in cases.items():
            with self.subTest(case=label):
                with self.assertRaises(ValueError) as ctx:
                    call()
                head = str(ctx.exception).split(":", 1)[0].strip()
                self.assertIn(head, CONTRACT_SECTION_5_3, label)
                self.assertIn("adapter_local=", str(ctx.exception), label)

    def test_question_construction_refusals_also_open_with_a_contract_code(self):
        with self.assertRaises(ValueError) as ctx:
            jev.choice_question("q", "Which entity operates the ground?", [])
        self.assertIn(str(ctx.exception).split(":", 1)[0].strip(), CONTRACT_SECTION_5_3)
        self.assertIn("adapter_local=JEV_OPTIONS_INVALID", str(ctx.exception))

    def test_bind_evidence_refusals_also_open_with_a_contract_code(self):
        with self.assertRaises(ValueError) as ctx:
            jev.bind_evidence(self.root, "run-r", "art-missing")
        self.assertIn(str(ctx.exception).split(":", 1)[0].strip(), CONTRACT_SECTION_5_3)
        self.assertIn("adapter_local=JEV_EVIDENCE_ARTIFACT_NOT_IN_RUN", str(ctx.exception))


class OutcomeCodeTests(RefusalChannelCase):
    """An outcome is not a refusal: these never appear on a BLOCKED result."""

    def setUp(self):
        super().setUp()
        self.setUp_binding()

    def test_outcome_and_refusal_codes_partition_the_local_code_space(self):
        self.assertEqual(set(jev.JEV_REASON_CODES),
                         set(jev.JEV_REFUSAL_CODES) | set(jev.JEV_OUTCOME_CODES))
        self.assertEqual(set(), set(jev.JEV_REFUSAL_CODES) & set(jev.JEV_OUTCOME_CODES))

    def test_an_outcome_code_is_never_presented_as_a_refusal(self):
        for code in jev.JEV_OUTCOME_CODES:
            with self.subTest(code=code):
                self.assertNotIn(code, jev.CONTRACT_CODE_FOR_JE)


if __name__ == "__main__":
    unittest.main()
