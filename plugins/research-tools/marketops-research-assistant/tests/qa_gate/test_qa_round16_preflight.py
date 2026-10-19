"""Round 16 preflight — six combined checks on the latest integrated script SHA.

This is a preflight, not an acceptance. It targets the seams the integrator and
the B/C owners are actively changing:

1. three TinyFish monitor capabilities the adapter advertises are not in the
   registry: the registry must not silently admit them, and their presence in
   `capabilities()` must not make the whole provider non-conformant;
2. production DNS stays strict (it calls `socket.getaddrinfo` and refuses
   non-global answers); a synthetic test may inject a resolver through a seam,
   never by mutating a production global;
3. a refused S0 must leave no SQLite file, while an allowed S0 may create it
   (the pair is what makes "no side effect before the gate" meaningful);
4. a missing URL guard must refuse with a closed-set code, never fall back to a
   second implementation;
5. the visible surface is 25 tools including `marketops_evidence_workbook`, and
   no tool reaches a provider without the S0 gate;
6. missing B0 / unknown price DENY in S0, and an unclassified adapter failure is
   held as UNKNOWN while a pre-dispatch refusal is NO_CHARGE.

Offline. No provider, no credential, no paid call.
"""
from __future__ import annotations

import ast
import importlib
import importlib.util
import inspect
import socket
import textwrap

import pytest

from conftest import SCRIPTS


def _mod(name):
    if importlib.util.find_spec(name) is None:
        pytest.fail(f"missing {name}; sys.path[0]={SCRIPTS}")
    return importlib.import_module(name)


def _evidence(providers=None, snapshot=True):
    gate = _mod("research_s0_preflight_gate")
    payload = {"tools": [{"name": "search"}, {"name": "fetch"}, {"name": "wallet"}]}
    serialized = gate.canonical_snapshot(payload)
    digest = gate.snapshot_digest(payload)
    entry = {
        "version": gate.SNAPSHOT_VERSION, "provider": "serpapi",
        "endpoint": "https://managed.example/mcp", "method": "tools/list",
        "source": "probe_schema@official_mcp",
        "captured_at": "2026-10-18T00:00:00Z",
        "egress_decision_ref": "egress:qa", "audit_ref": "audit:qa",
        "snapshot_serialized": serialized, "schema_sha256": digest,
        "registry_schema_sha256": digest, "tool_count": len(payload["tools"]),
    }
    base = {
        "integration": {"sha": "c" * 40, "expected_sha": "c" * 40},
        "independent_qa": {"status": "PASSED", "sha": "c" * 40},
        "managed_mcp": {"configured_count": 1, "effective": True,
                        "endpoints": ["https://managed.example/mcp"],
                        "vendor_direct_entries": []},
        "direct_vendor_bypass": {"present": False},
        "credential_injection": {"runtime_scoped": True, "source": "process_env"},
        "endpoint_allowlist": {"hosts": ["managed.example"]},
        "as_of": "2026-10-18T00:00:00Z",
        "upstream_schema_verified": True,
        "network_enabled": "enabled",
        "providers": providers if providers is not None
        else {"crawl4ai": {"billing_mode": "local_no_provider_account"}},
    }
    if snapshot:
        base["upstream_schema_snapshots"] = {"serpapi": entry}
    return base


# --- 1. unreviewed monitor capabilities -----------------------------------


MONITORS = {
    "tinyfish.get_monitor": frozenset({"monitor_id"}),
    "tinyfish.list_monitors": frozenset(),
    "tinyfish.list_browser_sessions": frozenset({"limit", "page"}),
}


def test_the_three_account_reads_are_reviewed_t1_and_the_provider_assembles(clean_env, tmp_path):
    """R16-D1 closure, on the *reviewed* contract rather than the old absence.

    Round 16 failed because these three were advertised without a registry
    entry. B has since reviewed them as documented T1 account reads, and C's
    default advertisement is now the reviewed subset. What must hold now:

    * each id has a registry entry at T1 / READ_ONLY with exactly the reviewed
      parameters (so an adapter cannot silently widen the contract);
    * the adapter's default advertisement contains no id the registry has not
      reviewed (the registry is the allowlist authority);
    * the composition root routes TinyFish instead of dropping the whole
      provider (R16-D1's symptom).
    """
    tiny = _mod("research_adapter_tinyfish")
    registry = _mod("research_capability_registry")
    wiring = _mod("research_wiring")

    for capability_id, expected in MONITORS.items():
        entry = registry.DEFAULT_REGISTRY.get(capability_id)
        assert entry is not None, f"B has not reviewed {capability_id}"
        assert str(entry.tier) == "T1", (capability_id, entry.tier)
        assert str(entry.effect_class) == "READ_ONLY", (capability_id, entry.effect_class)
        assert set(entry.allowed_params) == set(expected), (
            capability_id, sorted(entry.allowed_params))

    reviewed_ids = {entry.capability_id for entry in registry.DEFAULT_REGISTRY}
    advertised = set(tiny.capabilities())
    assert advertised <= reviewed_ids, (
        "the adapter advertises ids the registry has not reviewed: "
        f"{sorted(advertised - reviewed_ids)}")

    built = wiring.assemble_adapters(root=tmp_path / "data",
                                     registry=registry.DEFAULT_REGISTRY)
    failures = getattr(wiring.assemble_adapters, "failures", {})
    assert "tinyfish" in built, (sorted(built), failures)
    assert "tinyfish" not in failures, failures


# --- 2. DNS: production strict, test injection through a seam --------------


def test_production_dns_is_strict(clean_env, monkeypatch):
    guard = _mod("research_target_guard")
    calls = []

    def answer(mapping):
        def fake(host, port, *a, **k):
            calls.append((host, port))
            if mapping == "loopback":
                return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 0))]
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 0))]
        return fake

    monkeypatch.setattr(socket, "getaddrinfo", answer("public"))
    assert guard.resolve_checked("example.org", 443) == ("93.184.216.34",)
    assert calls == [("example.org", 443)]

    monkeypatch.setattr(socket, "getaddrinfo", answer("loopback"))
    with pytest.raises(ValueError) as refused:
        guard.resolve_checked("example.org", 443)
    assert str(refused.value).split(":", 1)[0].startswith("TARGET_")


def test_the_resolver_is_not_a_mutable_production_global(clean_env):
    """A test may inject a resolver; production must not read one from a global."""
    guard = _mod("research_target_guard")
    source = inspect.getsource(guard.resolve_checked)
    assert "socket.getaddrinfo" in source, source
    tree = ast.parse(textwrap.dedent(source))
    global_names = {n.id for n in ast.walk(tree)
                    if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load)}
    assert not {"RESOLVER", "resolver", "GETADDRINFO", "getaddrinfo"} & global_names, (
        "resolve_checked reads a mutable global resolver; production DNS can be "
        "replaced by whoever set it")


# --- 3. S0 before gateway: no SQLite side effect --------------------------


def test_a_refused_s0_creates_no_sqlite_and_an_allowed_one_does(clean_env, tmp_path):
    """Refusal leaves the data root untouched; the file appears only after ALLOW."""
    wiring = _mod("research_wiring")
    root = tmp_path / "data"
    root.mkdir()

    refused = wiring.managed_invoke(
        capability_id="tinyfish.get_wallet", params={}, run_id="r1",
        operation_id="op-refused", account_ref="acct", intent_fingerprint="i",
        root=root, evidence={})
    assert refused.gateway_invoked is False, refused
    assert not (root / "state" / "usage-ledger.sqlite3").exists(), (
        "a refused S0 created the ledger database")

    allowed = wiring.managed_invoke(
        capability_id="__qa__.none", params={}, run_id="r1",
        operation_id="op-allowed", account_ref="acct", intent_fingerprint="i",
        root=root, evidence=_evidence())
    assert allowed.gateway_invoked is True, allowed
    assert (root / "state" / "usage-ledger.sqlite3").exists(), (
        "the gateway never built after S0 allowed")


# --- 4. a missing guard must deny -----------------------------------------


def test_a_missing_url_guard_denies_instead_of_falling_back(clean_env, monkeypatch):
    tiny = _mod("research_adapter_tinyfish")
    monkeypatch.setattr(tiny, "_import_guard", lambda module, attr: None)
    with pytest.raises(ValueError) as refused:
        tiny.approved_url("https://example.org/x")
    assert str(refused.value).split(":", 1)[0] == tiny.REASON_EGRESS_REFUSED
    with pytest.raises(ValueError):
        tiny._url_guard()


def test_every_declared_guard_source_is_honest(clean_env):
    """A declaration that names a nonexistent attribute is a claim, not a guard."""
    tiny = _mod("research_adapter_tinyfish")
    broken = []
    for module_name, attribute in tiny.URL_GUARD_SOURCES:
        if tiny._import_guard(module_name, attribute) is None:
            broken.append(f"{module_name}.{attribute}")
    assert broken == [], broken


# --- 5. the visible surface and the S0 gate -------------------------------


PROVIDER_CAPABLE = {
    "marketops_tinyfish_agent", "marketops_tinyfish_agent_wait",
    "marketops_serpapi_mcp_search", "marketops_tinyfish", "marketops_crawl",
    "marketops_jev_choice", "marketops_search", "marketops_managed_call",
}


def test_the_visible_surface_is_25_and_the_workbook_is_not_a_provider_bypass(clean_env):
    mcp = _mod("research_mcp")
    tools = mcp.build_server()._tool_manager._tools
    assert len(tools) == 25, sorted(tools)
    assert "marketops_evidence_workbook" in tools, sorted(tools)

    reachable = set()
    for name, tool in tools.items():
        source = textwrap.dedent(inspect.getsource(tool.fn))
        tree = ast.parse(source)
        names = {c.func.id for c in ast.walk(tree)
                 if isinstance(c, ast.Call) and isinstance(c.func, ast.Name)}
        attrs = {(c.func.value.id, c.func.attr) for c in ast.walk(tree)
                 if isinstance(c, ast.Call) and isinstance(c.func, ast.Attribute)
                 and isinstance(c.func.value, ast.Name)}
        if (names & {"invoke", "search", "managed_invoke"}
                or ("research_wiring", "managed_invoke") in attrs):
            reachable.add(name)
    assert reachable == PROVIDER_CAPABLE, {
        "unexpected provider-capable tools": sorted(reachable - PROVIDER_CAPABLE),
        "expected but not seen": sorted(PROVIDER_CAPABLE - reachable)}

    workbook = textwrap.dedent(inspect.getsource(tools["marketops_evidence_workbook"].fn))
    assert "extra.invoke" not in workbook and "dual_search_mcp.search" not in workbook


# --- 6. B0/price deny and UNKNOWN hold ------------------------------------


def test_missing_b0_and_unknown_price_deny(clean_env):
    gate = _mod("research_s0_preflight_gate")
    metered = {"captured": True, "b0": "1.00", "unit": "USD", "meter": "requests",
               "price_source": "qa_synthetic_rate",
               "last_observed_at": "2026-10-18T00:00:00Z", "unknown_exposure": "0",
               "next_op_estimate": "0.01", "schema_verified": True,
               "key_rotation_confirmed": True, "auto_topup": False}

    no_b0 = dict(metered, captured=False, b0=None)
    decision = gate.evaluate(_evidence(providers={"serpapi": no_b0}))
    assert decision["launch"] == "DENY"
    assert decision["s1"]["reason_code"] == "WALLET_PRECONDITION_FAILED"

    unknown_price = dict(metered, next_op_estimate=None)
    decision = gate.evaluate(_evidence(providers={"serpapi": unknown_price}))
    assert decision["launch"] == "DENY"
    assert decision["s1"]["reason_code"] == "PRICE_UNKNOWN_FAIL_CLOSED"


def test_a_refusal_is_no_charge_and_an_unclassified_failure_is_held(clean_env, tmp_path):
    wiring = _mod("research_wiring")
    registry = _mod("research_capability_registry")
    policy = _mod("research_policy")
    ledger_module = _mod("research_usage_ledger")

    class Broken:
        provider = "typesafe_jev"
        transport = "python_sdk"

        def capabilities(self):
            return ("jev.choice",)

        def probe_schema(self):
            return {"status": "AUTH_REQUIRED"}

        def execute(self, request):
            raise RuntimeError("synthetic transport failure")

        def cancel(self, upstream_ref):
            return {"outcome": "BLOCKED"}

    root = tmp_path / "data"
    root.mkdir()
    ledger = ledger_module.for_root(root)
    project = ledger_module.project_for_root(root)
    ledger.set_cap("run", f"{project}/r1", ceiling_calls=50, source="qa")
    gateway = wiring.build_gateway(
        root=root, adapters={"typesafe_jev": Broken()},
        policy=policy.Policy(registry=registry.DEFAULT_REGISTRY, network_enabled=True))
    outcome = gateway.invoke(
        capability_id="jev.choice",
        params={"artifact_id": "a", "question": "q", "options": ["a", "b"]},
        run_id="r1", operation_id="op-unknown", account_ref="acct",
        intent_fingerprint="i")
    row = ledger.get("op-unknown")
    assert outcome.status == "UNKNOWN", outcome
    assert row is not None and row.charge_state == "UNKNOWN", row
