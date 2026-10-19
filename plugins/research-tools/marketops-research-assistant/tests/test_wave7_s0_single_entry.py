"""R12-D1: one S0 gate in front of every provider-capable host entry.

Q's round 12 measured seven MCP tools reaching the provider plane with
`research_s0_preflight_gate.evaluate` never having run. `managed_call` was gated;
`marketops_tinyfish_agent`, `marketops_tinyfish_agent_wait`,
`marketops_serpapi_mcp_search`, `marketops_tinyfish`, `marketops_crawl`,
`marketops_jev_choice` and `marketops_search` were not. An S0 gate that one tool
honours while six others dial out is a preference, not a gate.

The fix is one check in `research_mcp`, above the product's only two provider
planes. These tests are the **paired** half of that claim: for each tier, the
same call is refused without a passing deployment and reaches the plane with one.
A negative-only suite would pass on a gate that refuses everything, which is the
fake broad deny this must not become.

Every provider is a recorder. No socket, no credential, no paid call.
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
sys.path.insert(0, str(ROOT / "tests"))

import _s0_evidence_fixture as s0_fixture  # noqa: E402
import dual_search_mcp as dual  # noqa: E402
import research_mcp as mcp  # noqa: E402
import research_provider_extensions as extra  # noqa: E402
import research_wiring as wiring  # noqa: E402


class _Plane:
    """Both provider planes behind recorders, and the local preconditions relaxed.

    `_require_brief_for_network` and `store.list_run` are local reads; they are
    replaced so a failure here is about the S0 gate and nothing else. A leaked
    recorder would make later tests measure this fixture, so everything is
    restored on exit.
    """

    def __init__(self) -> None:
        self.reached: list[str] = []
        self._saved: dict[str, object] = {}

    def __enter__(self) -> "_Plane":
        self._saved = {
            "extra.invoke": extra.invoke,
            "dual.search": dual.search,
            "mcp.brief": mcp._require_brief_for_network,
            "mcp.list_run": mcp.store.list_run,
            "mcp.root": mcp.root,
        }
        extra.invoke = lambda action, **kw: (
            self.reached.append(action) or {"status": "SYNTHETIC"})
        dual.search = lambda *a, **k: (
            self.reached.append(f"dual_search:{a[0]}") or {"results": []})
        mcp._require_brief_for_network = lambda r, run_id: None
        mcp.store.list_run = lambda *a, **k: [{}]
        return self

    def __exit__(self, *exc) -> bool:
        extra.invoke = self._saved["extra.invoke"]
        dual.search = self._saved["dual.search"]
        mcp._require_brief_for_network = self._saved["mcp.brief"]
        mcp.store.list_run = self._saved["mcp.list_run"]
        mcp.root = self._saved["mcp.root"]
        return False


#: The seven host-visible entries Q measured, by tier, as the call the tool body
#: makes. Each funnels into `research_mcp.invoke` or `research_mcp.search`.
DIRECT = {
    "T0 tinyfish_mcp_wait": lambda: mcp.invoke(
        "tinyfish_mcp_wait", "r1", "o1",
        mcp_params={"agent_run_id": "run-x", "origin_operation_id": "o1",
                    "poll_index": 1}),
    "T1 tinyfish_mcp_search": lambda: mcp.invoke(
        "tinyfish_mcp_search", "r1", "o1", query="synthetic"),
    "T2 serpapi_mcp_search": lambda: mcp.invoke(
        "serpapi_mcp_search", "r1", "o1",
        mcp_params={"engine": "google", "q": "synthetic"}),
    "T2 jev_choice": lambda: mcp.invoke(
        "jev_choice", "r1", "o1", document="synthetic", question="q",
        options=["a", "b"], primitive="choice"),
    "T2 serper search": lambda: mcp.search("serper", "synthetic", "us", "en", "r1", "o1"),
    "T3 tinyfish_mcp_agent": lambda: mcp.invoke(
        "tinyfish_mcp_agent", "r1", "o1", url="https://example.invalid/",
        mcp_params={"max_steps": 1, "max_duration_seconds": 10},
        paid_limit_usd="0.20"),
    "local crawl4ai_local": lambda: mcp.invoke(
        "crawl4ai_local", "r1", "o1", url="https://example.invalid/"),
}


class SingleGateTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / "data"
        env = patch.dict(os.environ, {
            "MARKETOPS_RESEARCH_DATA_ROOT": str(self.root),
            "MARKETOPS_TINYFISH_AGENT_ALLOW_PAID": "1",
        })
        env.start()
        self.addCleanup(env.stop)

    def test_without_a_passing_deployment_every_entry_is_refused_at_the_gate(self):
        with _Plane() as plane:
            for label, call in DIRECT.items():
                with self.subTest(entry=label):
                    plane.reached.clear()
                    with self.assertRaises(ValueError) as caught:
                        call()
                    message = str(caught.exception)
                    self.assertIn("S0 gate refused", message, message)
                    # The refusal names what is missing, so an operator can act.
                    self.assertIn("S0_07_UPSTREAM_SCHEMA_VERIFIED", message, message)
                    self.assertEqual([], plane.reached,
                                     f"{label} reached a provider without S0")

    def test_with_a_passing_deployment_the_same_entries_reach_the_plane(self):
        """The pair. Without this half the test above passes on a blanket deny."""
        s0_fixture.install(self.root)
        with _Plane() as plane:
            for label, call in DIRECT.items():
                with self.subTest(entry=label):
                    plane.reached.clear()
                    call()
                    self.assertEqual(
                        1, len(plane.reached),
                        f"{label} was gated even though S0 passed")

    def test_the_unit_of_the_gate_is_the_deployment_not_the_tier(self):
        """A passing deployment admits every tier; a failing one admits none.

        That is what a pre-S0 gate means: it answers 'may this deployment call a
        vendor at all', and the per-tier ladder runs afterwards, inside the
        gateway or the extension module. A gate that admitted T0 while refusing
        T2 would be a second tier policy, and there is already one authority for
        that.
        """
        with _Plane() as plane:
            for label, call in DIRECT.items():
                plane.reached.clear()
                with self.assertRaises(ValueError):
                    call()
                self.assertEqual([], plane.reached, label)
        s0_fixture.install(self.root)
        with _Plane() as plane:
            for label, call in DIRECT.items():
                plane.reached.clear()
                call()
                self.assertEqual(1, len(plane.reached), label)

    def test_evidence_comes_from_the_data_root_and_not_from_the_caller(self):
        """A caller cannot present its own S0 result.

        `s0_provider_entry` takes a root and nothing else, and the two entry
        points in `research_mcp` pass no evidence through. This is asserted on
        the signatures rather than the docstrings.
        """
        import inspect

        self.assertEqual(["root"], list(
            inspect.signature(wiring.s0_provider_entry).parameters))
        for fn in (mcp.invoke, mcp.search):
            self.assertNotIn("evidence", inspect.signature(fn).parameters, fn)

    def test_evidence_written_elsewhere_is_not_read(self):
        """A second root's evidence must not unlock this deployment."""
        other = Path(self.tmp.name) / "other"
        s0_fixture.install(other)
        with _Plane() as plane:
            with self.assertRaises(ValueError):
                DIRECT["T2 serpapi_mcp_search"]()
            self.assertEqual([], plane.reached)

    def test_an_absent_gate_is_a_refusal_not_permission(self):
        """The gate is the only thing that could authorise a call; no gate is no."""
        real = wiring._import_if_present
        wiring._import_if_present = lambda name: (
            None if name == "research_s0_preflight_gate" else real(name))
        self.addCleanup(setattr, wiring, "_import_if_present", real)
        allowed, code, failed = wiring.s0_provider_entry(self.root)
        self.assertFalse(allowed)
        self.assertEqual("CAPABILITY_NOT_AVAILABLE", code)
        self.assertEqual(("S0_GATE_ABSENT",), failed)

    def test_a_breaking_gate_fails_closed_without_leaking_evidence(self):
        import research_s0_preflight_gate as gate

        def explode(evidence):
            raise RuntimeError("evidence: synthetic")

        real = gate.evaluate
        gate.evaluate = explode
        self.addCleanup(setattr, gate, "evaluate", real)
        allowed, code, failed = wiring.s0_provider_entry(self.root)
        self.assertFalse(allowed)
        self.assertEqual(("S0_EVALUATION_FAILED",), failed)

    def test_the_two_planes_are_the_only_provider_paths_in_the_product(self):
        """The guard is above both planes, so covering them covers the surface.

        Asserted by reading the source rather than by remembering: a third
        `extra.invoke` or `dual_search_mcp.search` call added elsewhere would
        silently be unguarded, and this test is what makes that visible.
        """
        source = (ROOT / "scripts" / "research_mcp.py").read_text(encoding="utf-8")
        self.assertEqual(1, source.count("return extra.invoke("))
        self.assertEqual(1, source.count("result = dual_search_mcp.search("))
        guard = source.index("def _require_s0_for_provider")
        self.assertLess(guard, source.index("return extra.invoke("))
        self.assertLess(guard, source.index("result = dual_search_mcp.search("))


if __name__ == "__main__":
    unittest.main()
