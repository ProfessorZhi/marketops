"""Wave-1 integration seams: what is wired, what is not, and what must never be.

`STATUS-MANIFEST.md` shows every worker role at "未开工", so this branch cannot
carry a real integration candidate. What it *can* carry is a machine-readable
answer to "which worker modules have landed?", so that the resume trigger is one
command rather than a judgement call, plus the frozen invariants that must hold
no matter which implementation eventually arrives.

Three of these tests are permanent architecture gates, not scaffolding:

* the gateway must not import the frozen legacy modules, or the §9 migration
  could never be completed;
* the fixture's field names must track the real contract once it lands;
* the orchestrator's reason codes must equal §5.3 exactly.
"""
from __future__ import annotations

import ast
import dataclasses
import importlib
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import _wave1_contract_fixture as fixture  # noqa: E402
import research_gateway as gateway  # noqa: E402
import research_wiring as wiring  # noqa: E402

# INTERFACE-CONTRACT.md v1.0 §3 "目标文件布局", transcribed with its owners.
PLANNED_MODULES = {
    "research_contract": "W-B",
    "research_capability_registry": "W-B",
    "research_policy": "W-B",
    "research_usage_ledger": "W-B",
    "research_adapter_base": "W-B",
    "research_egress": "W-A",
    "research_adapter_tinyfish": "W-C",
    "research_adapter_serpapi": "W-D",
    "research_adapter_serper": "W-D",
    "research_adapter_crawl4ai": "W-E",
    "research_adapter_jev": "W-F",
    "research_evidence_store": "W-F",
    "research_mcp_facade": "W-G",
}

# W-INTEGRATOR's own additions, pending CONTRACT-CHANGE-REQUEST (see status file).
INTEGRATOR_MODULES = {"research_gateway", "research_wiring"}

# WORKER-DAG-AND-OWNERSHIP.md §3 "冻结（本 Wave 只读）".
FROZEN_LEGACY = ("research_mcp", "dual_search_mcp", "research_provider_extensions",
                 "research_official_mcp", "research_provider_policy",
                 "research_lite_store", "research_delivery", "research_event_journal")


class WiringProbeTests(unittest.TestCase):
    def test_the_probe_names_every_module_in_the_contracts_file_plan(self):
        self.assertEqual(PLANNED_MODULES, wiring.PLANNED_MODULES)

    def test_the_probe_separates_present_from_missing_without_guessing(self):
        report = wiring.probe()
        self.assertEqual(set(wiring.PLANNED_MODULES),
                         set(report["present"]) | set(report["missing"]))
        self.assertEqual(set(wiring.PLANNED_MODULES) & set(report["present"]),
                         set(report["present"]))
        self.assertNotIn("fallback", report)

    def test_readiness_is_gated_on_the_whole_plan_not_on_a_sample(self):
        report = wiring.probe()
        self.assertEqual(not report["missing"], report["worker_plan_complete"])
        if report["missing"]:
            self.assertEqual("WAITING_FOR_WORKERS", report["state"])
        else:
            self.assertEqual("WORKERS_PRESENT", report["state"])

    def test_every_missing_module_is_attributed_to_its_owner(self):
        report = wiring.probe()
        for name in report["missing"]:
            self.assertEqual(PLANNED_MODULES[name], report["owners"][name])

    def test_build_gateway_refuses_and_names_every_absent_collaborator(self):
        # This no longer skips when every worker module has landed. The refusal it
        # exercises is not only about absent modules: `build_gateway()` with no
        # collaborators handed in must name all four of them, which is the state
        # every caller starts from. Skipping on "nothing is missing" retired the
        # only test of that path exactly when the branch became buildable.
        report = wiring.probe()
        if report["missing"]:
            with self.assertRaises(gateway.WiringIncomplete) as ctx:
                wiring.build_gateway()
            absent = set(ctx.exception.missing)
            self.assertTrue(set(report["missing"]) <= absent,
                            "build_gateway dropped a module it had already measured as absent")
            self.assertTrue(set(wiring.REQUIRED_COLLABORATORS) <= absent,
                            "build_gateway did not report the collaborators it was not handed")
        else:
            # Every module landed, so there is no absent collaborator left to
            # name. The half that still has content is the other one: the factory
            # must assemble from its defaults rather than return a half-built
            # gateway. Asserting only the refusal was a test of a world that no
            # longer exists.
            with tempfile.TemporaryDirectory() as tmp:
                built = wiring.build_gateway(root=Path(tmp))
            self.assertTrue(hasattr(built, "invoke"))
        # A landed adapter must be accounted for in exactly one of two ways:
        # routable, or named in `failures`. Silently dropping one is the
        # "standalone module" failure this seam exists to make visible, and
        # silently accepting one the gateway cannot call is worse — see
        # `assemble_adapters`. Asserting "every landed module is routable" was the
        # earlier form of this check and it was measured false: `typesafe_jev`
        # constructs but its `execute` is not callable as `execute(request)`.
        assembled = wiring.assemble_adapters()
        failures = getattr(wiring.assemble_adapters, "failures", {})
        for provider, (module_name, _class_name) in wiring.PROVIDER_ADAPTERS.items():
            if module_name not in report["present"]:
                continue
            with self.subTest(provider=provider):
                self.assertFalse(
                    provider in assembled and provider in failures,
                    f"{provider} is both routable and recorded as a failure")
                self.assertTrue(
                    provider in assembled or provider in failures,
                    f"{module_name} is present but neither routable nor reported")
        for provider, reason in failures.items():
            # An empty reason would let a refusal read as success in a log.
            self.assertTrue(str(reason).strip(), f"{provider} has an empty failure reason")


def _imported_modules(path: Path) -> set[str]:
    """Top-level names this file actually imports — not the words in its prose."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            names.add(node.module.split(".")[0])
    return names


class AdapterConformanceGatingTests(unittest.TestCase):
    """The routing table carries only adapters the gateway can actually call.

    A rule, not a pin on one adapter's defect: a conformant adapter must be
    routable and a non-conformant one must be named instead. When the Jev adapter
    is repaired this test keeps passing and starts asserting the other branch,
    which is the difference between measuring the seam and recording a bug.
    """

    def test_routability_follows_measured_conformance(self):
        try:
            base = importlib.import_module("research_adapter_base")
            jev = importlib.import_module("research_adapter_jev")
        except ModuleNotFoundError as exc:
            self.skipTest(f"BLOCKED: {exc.name} is not on this branch")
        assembled = wiring.assemble_adapters()
        failures = getattr(wiring.assemble_adapters, "failures", {})
        report = base.check_adapter_conformance(
            jev.JevAdapter(), expected_provider="typesafe_jev")
        if report.conformant:
            self.assertIn("typesafe_jev", assembled)
            self.assertNotIn("typesafe_jev", failures)
        else:
            self.assertNotIn(
                "typesafe_jev", assembled,
                "a non-conformant adapter entered the routing table; the gateway "
                "would dispatch it and book the TypeError as an UNKNOWN charge")
            self.assertIn("NON_CONFORMANT", failures.get("typesafe_jev", ""))


class FrozenBoundaryTests(unittest.TestCase):
    def _fresh_process_import(self) -> set[str]:
        """What importing the gateway pulls in, measured in a clean interpreter.

        Reading this process's `sys.modules` would answer a different question:
        by the time the full suite reaches here, other tests have imported the
        frozen modules already, so that reading is about the runner, not the
        module. A fresh interpreter is the only instrument that separates them.
        """
        watched = list(FROZEN_LEGACY) + ["urllib", "http", "socket", "requests",
                                         "httpx", "ssl", "keyring"]
        code = (
            "import sys, json\n"
            f"sys.path.insert(0, {str(ROOT / 'scripts')!r})\n"
            "import research_gateway\n"
            f"print(json.dumps(sorted(n for n in {watched!r} if n in sys.modules)))\n"
        )
        done = subprocess.run([sys.executable, "-c", code], capture_output=True,
                              text=True, cwd=str(ROOT), timeout=120)
        self.assertEqual(0, done.returncode, done.stderr)
        return set(json.loads(done.stdout.strip().splitlines()[-1]))

    def test_importing_the_gateway_pulls_in_no_frozen_or_network_module(self):
        self.assertEqual(set(), self._fresh_process_import())

    def test_the_gateway_reads_no_environment_variable_or_credential_store(self):
        tree = ast.parse((ROOT / "scripts" / "research_gateway.py").read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute) and node.attr in {"environ", "getenv"}:
                self.fail(f"gateway reads the process environment at line {node.lineno}")
        self.assertFalse(_imported_modules(ROOT / "scripts" / "research_gateway.py")
                         & {"os", "keyring"})


class ContractDriftTests(unittest.TestCase):
    """Runs against the real contract once W-B lands; skips loudly until then."""

    def test_fixture_field_names_track_the_real_contract_dataclasses(self):
        try:
            real = importlib.import_module("research_contract")
        except ModuleNotFoundError:
            # A skip, not a pass: nothing here proves the fixture matches reality.
            self.skipTest(
                "searched sys.path for 'research_contract' (scripts/, tests/): absent. "
                "Every gateway test in this branch therefore ran against the fixture; "
                "re-run this file after W-B lands, and treat a field mismatch as a "
                "gateway bug rather than a fixture bug.")
        registry_module = importlib.import_module("research_capability_registry")
        # Each type is compared against the module that actually owns it: §2's
        # request/decision/result live in the contract, §4's entry lives in the
        # registry. Comparing everything against `research_contract` would have
        # been a test of where the types were filed rather than of their shape.
        owners = {
            "CapabilityRequest": real, "PolicyDecision": real,
            "AdapterResult": real, "CapabilityEntry": registry_module,
        }
        for name, module in owners.items():
            fixture_fields = {f.name for f in dataclasses.fields(getattr(fixture, name))}
            real_fields = {f.name for f in dataclasses.fields(getattr(module, name))}
            self.assertEqual(fixture_fields, real_fields,
                             f"{name} drifted between the fixture and {module.__name__}")

        # §3.3 of the frozen contract draws a `Reservation`, and **no
        # implementation module declares one**: the ledger's `reserve()` returns
        # its own `Operation` row and that row is the authority. This is pinned
        # rather than skipped — an absence nobody asserts is how a document and
        # its code drift apart quietly. If a `Reservation` appears here later it
        # is a second shape over the same object and §3.3 needs re-reading first.
        for module in (real, registry_module):
            self.assertFalse(hasattr(module, "Reservation"),
                             f"{module.__name__} now declares Reservation; re-read "
                             "INTERFACE-CONTRACT.md §3.3 before accepting a second shape")
        ledger_module = importlib.import_module("research_usage_ledger")
        self.assertTrue(hasattr(ledger_module, "Operation"),
                        "the ledger's own reservation shape (Operation) disappeared")


if __name__ == "__main__":
    unittest.main()
