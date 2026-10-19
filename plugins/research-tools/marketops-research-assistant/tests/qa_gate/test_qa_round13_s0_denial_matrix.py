"""Round 13 — every host provider entry must be refused *before* the provider.

R12-D1 measured seven MCP tools reaching the provider plane with the S0 gate
never evaluated. `1e7e4f` claims to close that with one check in front of the
two provider-plane calls (`extra.invoke` and `dual_search_mcp.search`).

This module tests the claim from the provider side, not from the return value:
the provider planes, the egress transports and the ledger reservation are all
recorders, and the "refused" case uses recorders that **raise when touched**. A
refusal rendered after the provider call is therefore a failure, not a pass.

Both sides are asserted: the same seven entries, with a synthetic deployment in
which S0 has passed, must reach the provider plane. A gate that refuses
everything would pass the negative half alone.

Every case is offline. No socket, no credential, no vendor.
"""
from __future__ import annotations

import ast
import importlib
import importlib.util
import inspect
import json
import textwrap
from pathlib import Path
from types import SimpleNamespace

import pytest

from conftest import SCRIPTS

EVIDENCE_NAME = "s0-evidence.json"


def _mod(name, what):
    if importlib.util.find_spec(name) is None:
        pytest.fail(f"this candidate ships no {what} ({name}); sys.path[0]={SCRIPTS}")
    return importlib.import_module(name)


# --- the seven host entries, named by the tool a customer can see ----------


def _entries(mcp):
    return {
        "marketops_tinyfish_agent": lambda: mcp.invoke(
            "tinyfish_mcp_agent", "r1", "o1", url="https://example.invalid/",
            question="synthetic goal",
            mcp_params={"max_steps": 1, "max_duration_seconds": 10},
            paid_limit_usd="0.20"),
        "marketops_tinyfish_agent_wait": lambda: mcp.invoke(
            "tinyfish_mcp_wait", "r1", "o1",
            mcp_params={"agent_run_id": "run-x", "origin_operation_id": "o1",
                        "poll_index": 1}),
        "marketops_serpapi_mcp_search": lambda: mcp.invoke(
            "serpapi_mcp_search", "r1", "o1",
            mcp_params={"engine": "google", "q": "synthetic"}),
        "marketops_tinyfish": lambda: mcp.invoke(
            "tinyfish_mcp_search", "r1", "o1", query="synthetic"),
        "marketops_crawl": lambda: mcp.invoke(
            "crawl4ai_local", "r1", "o1", url="https://example.invalid/"),
        "marketops_jev_choice": lambda: mcp.invoke(
            "jev_choice", "r1", "o1", document="synthetic document for the judge",
            question="q", options=["a", "b"], primitive="choice"),
        "marketops_search": lambda: mcp.search(
            "serper", "synthetic", "us", "en", "r1", "o1"),
    }


class _Planes:
    """Provider planes, egress transports and the ledger reservation, recorded.

    `explode=True` makes every touch raise, so a path that reaches a plane fails
    even if it would have returned a governed-looking refusal.
    """

    def __init__(self, explode: bool, stub_extension: bool = True):
        self.explode = explode
        self.stub_extension = stub_extension
        self.reached: list[str] = []
        self.reserve_calls = 0
        self._saved = {}

    def _recorder(self, name):
        def record(*a, **k):
            self.reached.append(name)
            if self.explode:
                raise AssertionError(f"touched the provider plane: {name}")
            return {"status": "SYNTHETIC", "results": []}
        return record

    def __enter__(self):
        self.mcp = _mod("research_mcp", "MCP facade")
        self.extra = _mod("research_provider_extensions", "provider extension")
        self.dual = _mod("dual_search_mcp", "dual search adapter")
        self.official = _mod("research_official_mcp", "official MCP transport")
        self.ledger = _mod("research_usage_ledger", "usage ledger")

        sv = self._saved
        sv["extra.invoke"] = self.extra.invoke
        sv["extra._reserve"] = self.extra._reserve
        sv["dual.search"] = self.dual.search
        sv["official.call"] = self.official.call
        sv["ledger.reserve"] = self.ledger.UsageLedger.reserve
        sv["mcp.brief"] = self.mcp._require_brief_for_network
        sv["store.list_run"] = self.mcp.store.list_run
        sv["store.put_candidate"] = self.mcp.store.put_candidate
        sv["policy.receipt"] = self.mcp.provider_policy.invocation_receipt

        if self.stub_extension:
            self.extra.invoke = self._recorder("provider_extension.invoke")
        self.extra._reserve = lambda *a, **k: (
            self.reached.append("ledger.reserve") or None)
        self.dual.search = self._recorder("dual_search.search")
        self.official.call = self._recorder("official_mcp.call")

        def reserve(self_, *a, **k):
            self.reserve_calls += 1
            self.reached.append("ledger.reserve")
            if self.explode:
                raise AssertionError("touched the ledger reservation")
            return SimpleNamespace(operation_id="op")

        self.ledger.UsageLedger.reserve = reserve
        self.mcp._require_brief_for_network = lambda r, run_id: None
        self.mcp.store.list_run = lambda *a, **k: [{}]
        self.mcp.store.put_candidate = lambda *a, **k: {"record_id": "rec-1"}
        self.mcp.provider_policy.invocation_receipt = lambda *a, **k: {"synthetic": True}
        return self

    def __exit__(self, *exc):
        sv = self._saved
        self.extra.invoke = sv["extra.invoke"]
        self.extra._reserve = sv["extra._reserve"]
        self.dual.search = sv["dual.search"]
        self.official.call = sv["official.call"]
        self.ledger.UsageLedger.reserve = sv["ledger.reserve"]
        self.mcp._require_brief_for_network = sv["mcp.brief"]
        self.mcp.store.list_run = sv["store.list_run"]
        self.mcp.store.put_candidate = sv["store.put_candidate"]
        self.mcp.provider_policy.invocation_receipt = sv["policy.receipt"]
        return False


# --- evidence roots -------------------------------------------------------


def _fixture_module():
    import sys

    tests_dir = Path(__file__).resolve().parents[1]
    if str(tests_dir) not in sys.path:
        sys.path.insert(0, str(tests_dir))
    return importlib.import_module("_s0_evidence_fixture")


def _install(root: Path, evidence: dict | None = None) -> Path:
    path = Path(root) / "state" / EVIDENCE_NAME
    path.parent.mkdir(parents=True, exist_ok=True)
    if evidence is not None:
        path.write_text(json.dumps(evidence, ensure_ascii=False), encoding="utf-8")
    return path


def _passed_evidence() -> dict:
    return _fixture_module().evidence()


def _with_metered(evidence: dict, **changes) -> dict:
    """Change one provider's evidence; every other gate stays satisfied."""
    copy = json.loads(json.dumps(evidence))
    copy["providers"]["serper"].update(changes)
    return copy


@pytest.fixture()
def env_root(monkeypatch, tmp_path):
    root = tmp_path / "data"
    root.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("MARKETOPS_RESEARCH_DATA_ROOT", str(root))
    return root


# --- negative: the denial must precede every plane ------------------------


def test_without_evidence_every_host_entry_refuses_before_the_provider(clean_env, env_root):
    """Seven entries, no S0 evidence, exploding planes.

    The call may raise its governed refusal; it may not touch a provider, the
    egress transport or the ledger reservation. `marketops_tinyfish_agent` is
    reached last in the argument check, so the paid path is held to the same
    rule as the free one.
    """
    mcp = _mod("research_mcp", "MCP facade")
    entries = _entries(mcp)

    with _Planes(explode=True) as planes:
        for label, call in entries.items():
            planes.reached.clear()
            planes.reserve_calls = 0
            with pytest.raises(Exception) as refused:  # noqa: BLE001
                call()
            assert planes.reached == [], (
                f"{label} reached {planes.reached} before S0 refused it")
            assert planes.reserve_calls == 0, label
            code = str(refused.value)
            assert code.startswith(("CAPABILITY_NOT_AVAILABLE",
                                    "REQUIRE_HUMAN_GRANT",
                                    "PRICE_UNKNOWN", "WALLET_PRECONDITION",
                                    "RESEARCH_NETWORK_DISABLED")), (
                f"{label} refused for an unexpected reason: {code}")


def test_each_missing_precondition_denies_with_its_own_reason(clean_env, env_root):
    """One broken premise at a time; each must deny, and say which one.

    A gate that refuses for the same reason every time has not evaluated the
    evidence. `launch` stays DENY and the provider plane stays untouched.
    """
    mcp = _mod("research_mcp", "MCP facade")
    base = _passed_evidence()

    cases = {}
    cases["no evidence file"] = (None, "CAPABILITY_NOT_AVAILABLE")
    boolean_only = json.loads(json.dumps(base))
    boolean_only.pop("upstream_schema_snapshots")
    cases["boolean without a snapshot"] = (boolean_only, "CAPABILITY_NOT_AVAILABLE")

    fake = json.loads(json.dumps(base))
    fake["upstream_schema_snapshots"]["serpapi"]["snapshot_serialized"] = (
        fake["upstream_schema_snapshots"]["serpapi"]["snapshot_serialized"]
        .replace('"wallet"', '"bank"'))
    cases["tampered snapshot payload"] = (fake, "CAPABILITY_NOT_AVAILABLE")

    cases["B0 not captured"] = (_with_metered(base, captured=False),
                                "WALLET_PRECONDITION_FAILED")
    cases["price unknown (null estimate)"] = (
        _with_metered(base, next_op_estimate=None), "PRICE_UNKNOWN_FAIL_CLOSED")
    cases["key rotation unconfirmed"] = (
        _with_metered(base, key_rotation_confirmed=False), "WALLET_PRECONDITION_FAILED")
    cases["unknown zero price"] = (
        _with_metered(base, next_op_estimate="0"), "PRICE_UNKNOWN_FAIL_CLOSED")
    cases["auto top-up enabled"] = (
        _with_metered(base, auto_topup=True), "WALLET_PRECONDITION_FAILED")

    wiring = _mod("research_wiring", "wiring module")
    with _Planes(explode=True) as planes:
        for label, (evidence, expected) in cases.items():
            _install(env_root, evidence)
            planes.reached.clear()
            planes.reserve_calls = 0
            with pytest.raises(Exception) as refused:  # noqa: BLE001
                mcp.invoke("tinyfish_mcp_search", "r1", "o1", query="synthetic")
            assert planes.reached == [], f"{label}: reached {planes.reached}"
            assert planes.reserve_calls == 0, label
            assert str(refused.value).startswith(expected), (
                f"{label}: {refused.value}")

            # The same evidence, read through the entry the product's own
            # evaluator uses; the two must agree on DENY.
            decision = wiring.s0_provider_entry(env_root)
            assert decision[0] is False, (label, decision)
            assert decision[1] == expected, (label, decision)


def test_evaluate_raising_and_a_missing_gate_both_fail_closed(clean_env, env_root):
    """`s0_provider_entry` must never raise and never turn an error into yes."""
    wiring = _mod("research_wiring", "wiring module")
    gate = _mod("research_s0_preflight_gate", "S0 gate")
    _install(env_root, _passed_evidence())

    real_evaluate = gate.evaluate
    gate.evaluate = lambda evidence: (_ for _ in ()).throw(RuntimeError("boom"))
    try:
        allowed, reason, failed = wiring.s0_provider_entry(env_root)
    finally:
        gate.evaluate = real_evaluate
    assert allowed is False, (allowed, reason, failed)
    assert "S0_EVALUATION_FAILED" in failed, failed

    real_import = wiring._import_if_present
    wiring._import_if_present = lambda name: (
        None if name == "research_s0_preflight_gate" else real_import(name))
    try:
        allowed, reason, failed = wiring.s0_provider_entry(env_root)
    finally:
        wiring._import_if_present = real_import
    assert allowed is False, (allowed, reason, failed)
    assert "S0_GATE_ABSENT" in failed, failed


def test_evidence_for_one_data_root_does_not_authorise_another(clean_env, tmp_path, monkeypatch):
    """The gate reads the deployment's own root; it is not ambient permission."""
    mcp = _mod("research_mcp", "MCP facade")
    root_a = tmp_path / "a"
    root_b = tmp_path / "b"
    root_a.mkdir()
    root_b.mkdir()
    _install(root_a, _passed_evidence())

    monkeypatch.setenv("MARKETOPS_RESEARCH_DATA_ROOT", str(root_b))
    with _Planes(explode=True) as planes:
        with pytest.raises(Exception) as refused:  # noqa: BLE001
            mcp.invoke("tinyfish_mcp_search", "r1", "o1", query="synthetic")
    assert planes.reached == [], planes.reached
    assert "CAPABILITY_NOT_AVAILABLE" in str(refused.value)

    # Paired control: the same call against the root that holds the evidence
    # reaches the plane, so the refusal above was about the root, not the call.
    monkeypatch.setenv("MARKETOPS_RESEARCH_DATA_ROOT", str(root_a))
    with _Planes(explode=False) as planes:
        mcp.invoke("tinyfish_mcp_search", "r1", "o1", query="synthetic")
    assert "provider_extension.invoke" in planes.reached, planes.reached


# --- positive: the same seven entries pass when S0 has ---


def test_a_passed_synthetic_deployment_reaches_every_provider_entry(clean_env, env_root):
    """Both sides of the gate: with admissible evidence, all seven proceed.

    Without this half a gate that refuses everything would look perfect.
    """
    mcp = _mod("research_mcp", "MCP facade")
    _install(env_root, _passed_evidence())

    missing = []
    with _Planes(explode=False) as planes:
        for label, call in _entries(mcp).items():
            planes.reached.clear()
            call()
            if not planes.reached:
                missing.append(label)
    assert missing == [], f"entries that never reached a provider despite S0: {missing}"


def test_the_inner_paid_gate_still_holds_after_s0(clean_env, env_root, monkeypatch):
    """S0 is not the paid authorization; the T3 entry needs both.

    Flag off: the call refuses at the paid gate with the provider plane
    untouched. Flag on: the transport is reached, so the flag is the thing that
    changed.
    """
    mcp = _mod("research_mcp", "MCP facade")
    extra = _mod("research_provider_extensions", "provider extension")
    _install(env_root, _passed_evidence())
    monkeypatch.delenv("MARKETOPS_TINYFISH_AGENT_ALLOW_PAID", raising=False)
    monkeypatch.setenv("MARKETOPS_RESEARCH_DATA_ROOT", str(env_root))
    # The domain allowlist is a separate, older guard. It must not be the reason
    # this test passes, so it is satisfied explicitly and the paid gate is the
    # only variable between the two halves.
    monkeypatch.setenv("MARKETOPS_FETCH_ALLOWED_DOMAINS", "example.invalid")

    # DNS resolution is a separate guard (covered by the SSRF module); it must
    # not be the reason either half passes, so it is neutralised for both.
    real_url_guard = extra._public_https_url
    extra._public_https_url = lambda *a, **k: None
    try:
        call = _entries(mcp)["marketops_tinyfish_agent"]
        with _Planes(explode=False, stub_extension=False) as planes:
            with pytest.raises(Exception) as refused:  # noqa: BLE001
                call()
            assert "TINYFISH_AGENT_PAID_NOT_AUTHORIZED" in str(refused.value), refused.value
            assert planes.reached == [], planes.reached

        monkeypatch.setenv("MARKETOPS_TINYFISH_AGENT_ALLOW_PAID", "1")
        real_reserve = extra._reserve
        real_outcome = extra._record_outcome
        extra._reserve = lambda *a, **k: None
        extra._record_outcome = lambda *a, **k: None
        try:
            with _Planes(explode=False, stub_extension=False) as planes:
                # The synthetic transport answers with a shape the real
                # post-processing will reject; that is expected and irrelevant.
                # What is asserted is that the transport was reached at all,
                # which is the paid gate opening.
                try:
                    call()
                except Exception:  # noqa: BLE001
                    pass
            assert "official_mcp.call" in planes.reached, planes.reached
        finally:
            extra._reserve = real_reserve
            extra._record_outcome = real_outcome
    finally:
        extra._public_https_url = real_url_guard


# --- the fixture must not be production, and must not make zero free ------


def test_the_synthetic_fixture_is_test_only(clean_env):
    """A test fixture that production imports would be a deployment bypass."""
    offenders = []
    for path in sorted(SCRIPTS.glob("*.py")):
        text = path.read_text(encoding="utf-8", errors="replace")
        if "_s0_evidence_fixture" in text:
            offenders.append(path.name)
    assert offenders == [], offenders

    fixture = _fixture_module()
    source = inspect.getsource(fixture)
    assert "TEST_FIXTURE_NOT_A_VENDOR_RATE" in source
    assert fixture.SYNTHETIC_SOURCE not in _mod(
        "research_s0_preflight_gate", "S0 gate").ZERO_PRICE_SOURCES


def test_an_unknown_zero_price_is_not_free(clean_env, env_root):
    """A zero estimate with no zero-attesting source must refuse, not read as free."""
    wiring = _mod("research_wiring", "wiring module")
    gate = _mod("research_s0_preflight_gate", "S0 gate")
    base = _passed_evidence()

    zero_unknown = _with_metered(base, next_op_estimate="0")
    _install(env_root, zero_unknown)
    allowed, reason, failed = wiring.s0_provider_entry(env_root)
    assert allowed is False, (allowed, reason, failed)
    assert "PRICE_UNKNOWN_FAIL_CLOSED" in json.dumps(
        gate.evaluate(zero_unknown)["s1"]), gate.evaluate(zero_unknown)["s1"]

    zero_attested = _with_metered(base, next_op_estimate="0",
                                  price_source="local_no_charge")
    _install(env_root, zero_attested)
    allowed, reason, failed = wiring.s0_provider_entry(env_root)
    assert allowed is True, (allowed, reason, failed)


# --- no provider call site may sit outside the gate -----------------------


def test_every_provider_plane_call_site_in_the_facade_is_guarded(clean_env):
    """Static: the two plane calls must be inside functions that gate first.

    The seven entries are not the contract; *any* future function that calls the
    provider plane is. This fails when someone adds an eighth path without the
    gate, which a per-tool test would miss.
    """
    source = (SCRIPTS / "research_mcp.py").read_text(encoding="utf-8")
    tree = ast.parse(source)

    guarded = []
    unguarded = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        attr_calls = {
            (c.func.value.id, c.func.attr)
            for c in ast.walk(node)
            if isinstance(c, ast.Call) and isinstance(c.func, ast.Attribute)
            and isinstance(c.func.value, ast.Name)}
        reaches = (("extra", "invoke") in attr_calls
                   or ("dual_search_mcp", "search") in attr_calls)
        if not reaches:
            continue
        names = {c.func.id for c in ast.walk(node)
                 if isinstance(c, ast.Call) and isinstance(c.func, ast.Name)}
        (guarded if "_require_s0_for_provider" in names else unguarded).append(node.name)

    assert unguarded == [], f"provider-plane call sites without the S0 gate: {unguarded}"
    assert set(guarded) == {"search", "invoke"}, guarded


def test_no_other_host_tool_source_calls_the_provider_plane(clean_env):
    """The 24-tool surface: enumerate who can reach a provider at all.

    This is the "including aliases, no omitted path" check. It reads each tool's
    own source, so a new tool that calls `invoke`/`search` shows up here and must
    be accounted for in `_entries`.
    """
    mcp = _mod("research_mcp", "MCP facade")
    tools = mcp.build_server()._tool_manager._tools
    # 24 at round 13; 25 since `marketops_evidence_workbook` became reachable
    # (wave 9). The count is asserted so a surface change is visible here, and
    # `reachable` below is unchanged: the workbook is not provider-capable.
    assert len(tools) == 25, sorted(tools)

    expected = set(_entries(mcp)) | {"marketops_managed_call"}
    reachable = set()
    for name, tool in tools.items():
        source = textwrap.dedent(inspect.getsource(tool.fn))
        tree = ast.parse(source)
        called = {c.func.id for c in ast.walk(tree)
                  if isinstance(c, ast.Call) and isinstance(c.func, ast.Name)}
        attrs = {(c.func.value.id, c.func.attr) for c in ast.walk(tree)
                 if isinstance(c, ast.Call) and isinstance(c.func, ast.Attribute)
                 and isinstance(c.func.value, ast.Name)}
        if (called & {"invoke", "search", "managed_invoke"}
                or ("research_wiring", "managed_invoke") in attrs):
            reachable.add(name)

    assert reachable == expected, {
        "unexpected provider-capable tools": sorted(reachable - expected),
        "expected but not seen": sorted(expected - reachable),
    }
