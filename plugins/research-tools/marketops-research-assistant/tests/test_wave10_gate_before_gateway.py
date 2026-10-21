"""I-W10: the S0 gate must run before the gateway is *constructed*, not before it is called.

W-A's post-fix security round carried two strict xfails; the first was this:
`research_wiring.managed_invoke` built the `CapabilityGateway` — and therefore
opened or created the SQLite ledger, ran its schema migration and left a `state/`
directory — **before** evaluating S0. A denied deployment then had a database it
never asked for.

"Refused" and "refused, and the database was initialised anyway" are not the same
outcome. The second one is a side effect that survives the refusal, and it is
observable on disk in a way that "no reservation was written" is not.

The fix is a proxy that constructs on first *dispatch*. `guarded_invoke` is still
the only place that evaluates the gate, so this adds an ordering guarantee without
adding a second opinion about whether the call is allowed.
"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import _s0_evidence_fixture as s0_fixture  # noqa: E402
import research_wiring as wiring  # noqa: E402

CALL = dict(capability_id="tinyfish.get_wallet", params={}, run_id="r1",
            operation_id="r1t0", account_ref="tinyfish-main",
            intent_fingerprint="i1")


def _sqlite_files(root: Path) -> list[Path]:
    return sorted(root.rglob("*.sqlite3")) + sorted(root.rglob("*.sqlite"))


class GateBeforeGatewayTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / "data"
        env = patch.dict(os.environ, {
            "MARKETOPS_RESEARCH_DATA_ROOT": str(self.root)})
        env.start()
        self.addCleanup(env.stop)

    def test_a_denied_gate_creates_no_database_file(self):
        outcome = wiring.managed_invoke(**CALL, root=self.root)
        self.assertFalse(outcome.gateway_invoked)
        self.assertEqual("BLOCKED", outcome.status)
        self.assertEqual([], _sqlite_files(self.root),
                         "a refused deployment still created a ledger file")
        self.assertFalse((self.root / "state").exists(),
                         "a refused deployment still created the ledger directory")

    def test_a_denied_gate_never_reaches_the_constructor(self):
        """The stronger form: the factory itself is not called.

        Asserting on the file is an observation; asserting on the factory is the
        mechanism. Both are here because a future constructor that writes
        somewhere else would pass the file check and fail this one.
        """
        calls = []
        real = wiring.build_gateway

        def counted(**kwargs):
            calls.append(kwargs)
            return real(**kwargs)

        wiring.build_gateway = counted
        self.addCleanup(setattr, wiring, "build_gateway", real)
        outcome = wiring.managed_invoke(**CALL, root=self.root)
        self.assertFalse(outcome.gateway_invoked)
        self.assertEqual([], calls, "the gateway was constructed despite a refusal")

    def test_an_allowed_gate_still_builds_and_invokes(self):
        """The pair. Without this half the test above passes on a broken proxy."""
        s0_fixture.install(self.root)
        calls = []

        class Spy:
            def invoke(self, **kwargs):
                calls.append(kwargs)
                return SimpleNamespace(status="OK", reason_code="")

        outcome = wiring.managed_invoke(**CALL, root=self.root, gateway_object=Spy())
        self.assertTrue(outcome.gateway_invoked)
        self.assertEqual(1, len(calls))
        self.assertEqual("OK", outcome.status)

    def test_an_allowed_gate_builds_the_real_gateway_once(self):
        s0_fixture.install(self.root)
        built = []

        class Spy:
            def invoke(self, **kwargs):
                return SimpleNamespace(status="OK", reason_code="")

        real = wiring.build_gateway

        def counted(**kwargs):
            built.append(kwargs)
            gateway_object = real(**kwargs)
            gateway_object.invoke = Spy().invoke
            return gateway_object

        wiring.build_gateway = counted
        self.addCleanup(setattr, wiring, "build_gateway", real)
        outcome = wiring.managed_invoke(**CALL, root=self.root)
        self.assertTrue(outcome.gateway_invoked)
        self.assertEqual(1, len(built), "the gateway was built more than once")
        self.assertEqual(Path(self.root), Path(built[0]["root"]))

    def test_the_gate_is_evaluated_exactly_once(self):
        """One source of truth: the ordering fix must not add a second evaluate."""
        import research_s0_preflight_gate as gate

        seen = []
        real = gate.evaluate

        def counted(evidence):
            seen.append(evidence)
            return real(evidence)

        gate.evaluate = counted
        self.addCleanup(setattr, gate, "evaluate", real)
        wiring.managed_invoke(**CALL, root=self.root)
        self.assertEqual(1, len(seen), "the gate was evaluated more than once")


if __name__ == "__main__":
    unittest.main()
