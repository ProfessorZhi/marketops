"""W14: `batch_status` is reviewed on the published wire, end to end.

B's registry reviewed `tinyfish.batch_status` with `batch_id` — a parameter no
vendor tool publishes. The capability was T0 and advertised, so a legitimate
adapter argument was refused by Policy as `CAPABILITY_PARAM_NOT_ALLOWED` before
dispatch: reviewed, advertised, and **not routable**. B's `a5527dc` changes the
reviewed surface to the published `run_ids` (1..8 opaque ids, each
``^[A-Za-z0-9_-]{1,128}$``) and teaches the schema builder property-level
schemas so the bound is machine-readable rather than a comment.

This file is the integration side of that change: the registry entry itself, the
**real** adapter with a synthetic runner, and the managed gateway over the real
ledger. No socket, no credential, no provider.

The bound is asserted on both sides of every edge — 1 and 8 accepted, 0 and 9
refused — because a schema that only tests the happy path cannot tell a limit
from a wish.
"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import research_adapter_tinyfish as tinyfish  # noqa: E402
import research_capability_registry as registry_module  # noqa: E402
import research_policy as policy_module  # noqa: E402
import research_usage_ledger as ledger_module  # noqa: E402
import research_wiring as wiring  # noqa: E402

CAPABILITY = "tinyfish.batch_status"


class _NetworkAllowed(unittest.TestCase):
    """The adapter's own network gate is orthogonal to what this file measures.

    `TINYFISH_NETWORK_NOT_AUTHORIZED` is a deployment switch that exists so a
    provider call cannot happen by accident. These tests never dispatch for real —
    the runner is a recorder — so they set the gate the same way every existing
    TinyFish suite does, rather than working around the adapter.
    """

    def setUp(self):
        # A synthetic literal, never a real key: the adapter refuses to run
        # without *a* credential even when a runner is injected, and that refusal
        # exists so a provider call cannot happen by accident.
        env = patch.dict(os.environ, {
            "MARKETOPS_RESEARCH_ALLOW_NETWORK": "1",
            "TINYFISH_API_KEY": "synthetic-tinyfish-key-not-for-network"})
        env.start()
        self.addCleanup(env.stop)


def _run_ids(count: int) -> list[str]:
    return [f"run-{index:03d}" for index in range(count)]


class _Recorder:
    """The synthetic provider. Records every dispatch; never opens a socket."""

    def __init__(self, *, raises: Exception | None = None) -> None:
        self.calls: list[tuple[str, dict]] = []
        self._raises = raises

    async def __call__(self, tool, arguments):
        # The adapter awaits its runner (`plan["runner"] or _live_runner`), so a
        # synchronous recorder would raise inside the dispatch and be booked as an
        # UNKNOWN charge — the exact confusion this file exists to avoid.
        self.calls.append((tool, dict(arguments)))
        if self._raises is not None:
            raise self._raises
        return {"runs": [{"run_id": run_id, "status": "SUCCEEDED"}
                         for run_id in arguments.get("run_ids", [])]}


class RegistrySurfaceTests(_NetworkAllowed):
    def test_the_reviewed_parameter_is_the_published_wire_not_batch_id(self):
        entry = registry_module.DEFAULT_REGISTRY.get(CAPABILITY)
        self.assertIsNotNone(entry)
        self.assertEqual(frozenset({"run_ids"}), entry.allowed_params)
        self.assertNotIn("batch_id", entry.allowed_params)
        self.assertEqual(("run_ids",), tuple(entry.param_schema.get("required", ())))
        self.assertEqual("T0", str(entry.tier))
        self.assertEqual("ENABLED", str(entry.availability))

    def test_the_schema_carries_the_documented_bound_and_pattern(self):
        schema = registry_module.DEFAULT_REGISTRY.get(CAPABILITY).param_schema
        run_ids = schema["properties"]["run_ids"]
        self.assertEqual("array", run_ids["type"])
        self.assertEqual(1, run_ids["minItems"])
        self.assertEqual(8, run_ids["maxItems"])
        self.assertEqual("string", run_ids["items"]["type"])
        self.assertEqual("^[A-Za-z0-9_-]{1,128}$", run_ids["items"]["pattern"])

    def test_the_schema_builder_still_accepts_the_bare_type_form(self):
        """Every pre-existing entry keeps its one-word form."""
        entry = registry_module.DEFAULT_REGISTRY.get("tinyfish.get_run")
        self.assertEqual({"type": "object", "properties": {"id": {"type": "string"}},
                          "additionalProperties": False}, entry.param_schema)


class DirectAdapterTests(_NetworkAllowed):
    def test_one_run_id_is_accepted(self):
        runner = _Recorder()
        receipt = tinyfish.call("batch_status", {"run_ids": _run_ids(1)}, runner=runner)
        self.assertTrue(receipt["ok"], receipt)
        self.assertEqual(1, len(runner.calls))

    def test_eight_run_ids_are_accepted(self):
        runner = _Recorder()
        receipt = tinyfish.call("batch_status", {"run_ids": _run_ids(8)}, runner=runner)
        self.assertTrue(receipt["ok"], receipt)
        self.assertEqual(1, len(runner.calls))

    def test_zero_and_nine_run_ids_are_refused_before_dispatch(self):
        for count in (0, 9):
            with self.subTest(count=count):
                runner = _Recorder()
                with self.assertRaises(ValueError):
                    tinyfish.call("batch_status", {"run_ids": _run_ids(count)},
                                  runner=runner)
                self.assertEqual([], runner.calls, "refused after dispatch")

    def test_a_malformed_id_and_the_forbidden_batch_id_are_refused(self):
        for arguments in ({"run_ids": ["ok", "bad id!"]},
                          {"run_ids": ["x" * 129]},
                          {"batch_id": "b-1"}):
            with self.subTest(arguments=arguments):
                runner = _Recorder()
                with self.assertRaises(ValueError):
                    tinyfish.call("batch_status", arguments, runner=runner)
                self.assertEqual([], runner.calls, "refused after dispatch")

    def test_get_run_stays_strict_on_its_own_handle(self):
        runner = _Recorder()
        tinyfish.call("get_run", {"id": "run-001"}, runner=runner)
        self.assertEqual(("get_run", {"id": "run-001"}), runner.calls[0])
        with self.assertRaises(ValueError):
            tinyfish.call("get_run", {"run_id": "run-001"}, runner=_Recorder())

    def test_a_locked_upstream_is_unknown_and_not_retried(self):
        runner = _Recorder(raises=TimeoutError("locked"))
        receipt = tinyfish.call("batch_status", {"run_ids": _run_ids(1)}, runner=runner)
        self.assertEqual("UNKNOWN", str(receipt["charge_state"]))
        self.assertFalse(receipt["retry_permitted"])
        self.assertEqual(1, len(runner.calls))


class ManagedGatewayTests(_NetworkAllowed):
    def _gateway(self, root: Path, runner: _Recorder):
        ledger = ledger_module.for_root(root)
        project = ledger_module.project_for_root(root)
        ledger.set_cap("run", f"{project}/r1", ceiling_calls=20, source="w14-fixture")
        built = wiring.build_gateway(
            root=root, adapters={"tinyfish": tinyfish.TinyFishAdapter(runner=runner)},
            policy=policy_module.Policy(
                registry=registry_module.DEFAULT_REGISTRY, network_enabled=True))
        return built, ledger

    def test_a_managed_batch_status_settles_at_zero_with_one_dispatch(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            runner = _Recorder()
            built, ledger = self._gateway(root, runner)
            outcome = built.invoke(
                capability_id=CAPABILITY, params={"run_ids": _run_ids(1)},
                run_id="r1", operation_id="r1op1", account_ref="tinyfish-main",
                intent_fingerprint="i1")
            self.assertEqual("OK", outcome.status, (outcome.reason_code, outcome.caveats))
            self.assertEqual(1, len(runner.calls))
            row = ledger.get("r1op1")
            self.assertEqual("SETTLED", row.charge_state)
            self.assertEqual(0, row.actual_micros)
            self.assertEqual("DERIVED", str(row.actual_source))

    def test_the_forbidden_batch_id_is_blocked_before_any_dispatch(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            runner = _Recorder()
            built, ledger = self._gateway(root, runner)
            outcome = built.invoke(
                capability_id=CAPABILITY, params={"batch_id": "b-1"},
                run_id="r1", operation_id="r1op2", account_ref="tinyfish-main",
                intent_fingerprint="i2")
            self.assertEqual("BLOCKED", outcome.status, outcome)
            self.assertEqual("CAPABILITY_PARAM_NOT_ALLOWED", outcome.reason_code)
            self.assertEqual([], runner.calls, "the registry let batch_id through")
            self.assertEqual((), tuple(ledger.operations()))

    def test_the_disabled_cancel_family_is_still_not_routable(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            runner = _Recorder()
            built, _ledger = self._gateway(root, runner)
            for capability_id in ("tinyfish.cancel_run", "tinyfish.batch_cancel"):
                with self.subTest(capability_id=capability_id):
                    # Empty params: the parameter allowlist runs before the
                    # availability gate, so a key that is not on this entry would
                    # report the wrong reason for a disabled capability.
                    outcome = built.invoke(
                        capability_id=capability_id, params={},
                        run_id="r1", operation_id=f"r1-{capability_id}",
                        account_ref="tinyfish-main", intent_fingerprint="i3")
                    self.assertEqual("BLOCKED", outcome.status, outcome)
                    self.assertEqual("CAPABILITY_NOT_AVAILABLE", outcome.reason_code)
            self.assertEqual([], runner.calls)


if __name__ == "__main__":
    unittest.main()
