"""Round 12 — is the S0 gate *the only entry*, or only the new entry?

`bd10203` adds `marketops_managed_call` and calls `managed_invoke` "the product's
only managed path". The claim under test is stronger than that sentence: an S0
gate that one tool honours while six others call the provider layer directly is
not a gate, it is a preference. `S0_04_NO_DIRECT_VENDOR_BYPASS` names exactly
this failure.

The measurement is at the provider plane, not at the tool's return value: the
provider extension and the dual-search adapter are replaced by recorders, and
the S0 gate's own `evaluate` is replaced by a recorder too. A path that reaches
the provider without `evaluate` having run is a bypass, whatever it returned.

Everything here is offline: recorders replace both ends, no socket is opened and
no credential is read.
"""
from __future__ import annotations

import hashlib
import json
from types import SimpleNamespace

import pytest

from conftest import SCRIPTS


def _require(name, what):
    import importlib.util

    if importlib.util.find_spec(name) is None:
        pytest.fail(f"this candidate ships no {what} ({name}); searched sys.path[0]={SCRIPTS}")
    import importlib

    return importlib.import_module(name)


# --- the direct paths: call the same functions the MCP tools call ----------


def _direct_calls(mcp):
    """Every MCP tool body that ends in a provider call, as a callable.

    Named by the tool a customer can see, so a failure points at the surface an
    operator would have to disable, not at an internal function.
    """
    return {
        "marketops_tinyfish_agent (T3, paid agent steps)": lambda: mcp.invoke(
            "tinyfish_mcp_agent", "r1", "o1", url="https://example.invalid/",
            mcp_params={"max_steps": 1, "max_duration_seconds": 10},
            paid_limit_usd="0.20"),
        "marketops_tinyfish_agent_wait (T0 poll of a paid run)": lambda: mcp.invoke(
            "tinyfish_mcp_wait", "r1", "o1",
            mcp_params={"agent_run_id": "run-x", "origin_operation_id": "o1",
                        "poll_index": 1}),
        "marketops_serpapi_mcp_search (T2, billable)": lambda: mcp.invoke(
            "serpapi_mcp_search", "r1", "o1",
            mcp_params={"engine": "google", "q": "synthetic"}),
        "marketops_tinyfish (T1, free-tier vendor call)": lambda: mcp.invoke(
            "tinyfish_mcp_search", "r1", "o1", query="synthetic"),
        "marketops_crawl (local SDK, real egress)": lambda: mcp.invoke(
            "crawl4ai_local", "r1", "o1", url="https://example.invalid/"),
        "marketops_jev_choice (T2, metered)": lambda: mcp.invoke(
            "jev_choice", "r1", "o1", document="synthetic", question="q",
            options=["a", "b"], primitive="choice"),
        "marketops_search (T2, billable REST)": lambda: mcp.search(
            "serper", "synthetic", "us", "en", "r1", "o1"),
    }


class _Planes:
    """Both provider planes, and the S0 gate, behind recorders.

    Restores everything on exit: a leaked recorder would make every later test
    in the file measure the fixture instead of the product.
    """

    def __init__(self, mcp, extra, dual, s0):
        self.mcp, self.extra, self.dual, self.s0 = mcp, extra, dual, s0
        self.reached: list[str] = []
        self.s0_calls = 0
        self._saved = {}

    def __enter__(self):
        m, e, d, s = self.mcp, self.extra, self.dual, self.s0
        self._saved = {
            "extra.invoke": e.invoke,
            "dual.search": d.search,
            "mcp.root": m.root,
            "mcp.brief": m._require_brief_for_network,
            "store.list_run": m.store.list_run,
            "store.put_candidate": m.store.put_candidate,
            "policy.receipt": m.provider_policy.invocation_receipt,
            "s0.evaluate": s.evaluate,
        }
        e.invoke = lambda action, **kw: (
            self.reached.append(action) or {"status": "SYNTHETIC"})
        d.search = lambda *a, **k: (
            self.reached.append(f"dual_search:{a[0]}") or {"results": []})
        m.root = lambda: "/synthetic-root"
        m._require_brief_for_network = lambda r, run_id: None
        m.store.list_run = lambda *a, **k: [{}]
        m.store.put_candidate = lambda *a, **k: {"record_id": "rec-1"}
        m.provider_policy.invocation_receipt = lambda *a, **k: {"synthetic": True}

        real_evaluate = s.evaluate

        def counting_evaluate(evidence):
            self.s0_calls += 1
            return real_evaluate(evidence)

        s.evaluate = counting_evaluate
        return self

    def __exit__(self, *exc):
        sv = self._saved
        self.extra.invoke = sv["extra.invoke"]
        self.dual.search = sv["dual.search"]
        self.mcp.root = sv["mcp.root"]
        self.mcp._require_brief_for_network = sv["mcp.brief"]
        self.mcp.store.list_run = sv["store.list_run"]
        self.mcp.store.put_candidate = sv["store.put_candidate"]
        self.mcp.provider_policy.invocation_receipt = sv["policy.receipt"]
        self.s0.evaluate = sv["s0.evaluate"]
        return False


def test_a_direct_provider_tool_does_not_reach_the_provider_without_s0(clean_env):
    """The gate must be the entry, not a parallel path.

    A tool that reaches a provider with `evaluate` never having run is the exact
    bypass `S0_04_NO_DIRECT_VENDOR_BYPASS` is supposed to refuse. The assertion
    is on the provider plane, so a tool that renders a refusal after the call
    would still fail here — which is the point: a charge with an apology is a
    charge.
    """
    mcp = _require("research_mcp", "MCP facade")
    extra = _require("research_provider_extensions", "provider extension module")
    dual = _require("dual_search_mcp", "dual search adapter")
    s0 = _require("research_s0_preflight_gate", "S0 preflight gate")

    bypassed = []
    with _Planes(mcp, extra, dual, s0) as planes:
        for label, call in _direct_calls(mcp).items():
            planes.reached.clear()
            planes.s0_calls = 0
            try:
                call()
            except Exception as exc:  # noqa: BLE001 - the plane, not the return, is the evidence
                failure = f"{type(exc).__name__}: {exc}"
            else:
                failure = ""
            if planes.reached and planes.s0_calls == 0:
                bypassed.append(f"{label} -> {planes.reached} {failure}".strip())

    assert bypassed == [], (
        "these MCP tools reached a provider without evaluating S0:\n  "
        + "\n  ".join(bypassed))


def test_the_managed_entry_is_the_one_path_that_does_evaluate_s0(clean_env, data_root):
    """Positive control: the managed path evaluates the gate and refuses first.

    Without this half, a recorder that never fires would make the test above
    pass by measuring nothing.
    """
    mcp = _require("research_mcp", "MCP facade")
    extra = _require("research_provider_extensions", "provider extension module")
    dual = _require("dual_search_mcp", "dual search adapter")
    s0 = _require("research_s0_preflight_gate", "S0 preflight gate")
    wiring = _require("research_wiring", "wiring module")

    with _Planes(mcp, extra, dual, s0) as planes:
        outcome = wiring.managed_invoke(
            capability_id="tinyfish.get_wallet", params={}, run_id="r1",
            operation_id="o-managed", account_ref="acct-local-1",
            intent_fingerprint="i1", root=data_root, evidence={})

        assert planes.s0_calls == 1, "the managed entry did not evaluate S0"
        assert planes.reached == [], f"a refused entry still reached a provider: {planes.reached}"
        assert outcome.gateway_invoked is False, outcome
        assert outcome.status == "BLOCKED", outcome


def test_the_managed_call_tool_cannot_be_handed_its_own_evidence(clean_env):
    """Evidence is a deployment fact, not a caller argument.

    If the tool accepted `evidence`, an Agent could declare S0 passed — the
    boolean-only hole this gate exists to close, moved one layer up. Asserted on
    the tool's own schema and on the callable's signature, so a docstring
    promise is not enough.
    """
    mcp = _require("research_mcp", "MCP facade")
    import inspect

    server = mcp.build_server()
    for name, tool in server._tool_manager._tools.items():
        properties = tool.parameters.get("properties", {})
        assert "evidence" not in properties, (name, sorted(properties))
    assert "evidence" not in inspect.signature(mcp.managed_call).parameters


# --- evidence that the gate can actually allow ----------------------------


SHA = hashlib.sha256


def _valid_evidence():
    payload = {"tools": [{"name": "search"}, {"name": "fetch"}, {"name": "wallet"}]}
    serialized = json.dumps(payload, sort_keys=True, separators=(",", ":"),
                            ensure_ascii=False)
    digest = SHA(serialized.encode("utf-8")).hexdigest()
    snapshot = {
        "version": 1,
        "provider": "serpapi",
        "endpoint": "https://managed.example/mcp",
        "method": "tools/list",
        "source": "probe_schema@official_mcp",
        "captured_at": "2026-10-18T00:00:00Z",
        "egress_decision_ref": "egress:synthetic-1",
        "audit_ref": "audit:synthetic-1",
        "snapshot_serialized": serialized,
        "schema_sha256": digest,
        "registry_schema_sha256": digest,
        "tool_count": len(payload["tools"]),
    }
    return {
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
        "upstream_schema_snapshots": {"serpapi": snapshot},
        "network_enabled": "enabled",
        # Local compute spends no vendor account, so S1 has nothing to gate.
        "providers": {"crawl4ai": {"billing_mode": "local_no_provider_account"}},
    }


def test_the_fixture_is_admissible_by_the_products_own_validator(clean_env):
    """The positive control must not be my opinion of a valid snapshot.

    `validate_snapshot_entry` is the gate's own answer; if it returns problems,
    the allow-case below would be testing a fabricated refusal instead of the
    pass path.
    """
    s0 = _require("research_s0_preflight_gate", "S0 preflight gate")
    evidence = _valid_evidence()
    entry = evidence["upstream_schema_snapshots"]["serpapi"]
    problems = s0.validate_snapshot_entry(
        "serpapi", entry, hosts=evidence["endpoint_allowlist"]["hosts"],
        as_of=s0._parse_iso(evidence["as_of"]))
    assert problems == [], problems
    assert s0.evaluate(evidence)["launch"] == "ALLOW", s0.evaluate(evidence)["s0"]


def test_a_fabricated_snapshot_is_refused_for_the_right_reason(clean_env):
    """Every way of dressing up a capture that did not happen must refuse.

    The positive fixture is admissible, so the gate is not a rubber stamp only
    if altering it turns the verdict. Each case changes one field and names the
    problem it must produce, so a refusal for the wrong reason is visible.
    """
    s0 = _require("research_s0_preflight_gate", "S0 preflight gate")
    base = _valid_evidence()
    hosts = base["endpoint_allowlist"]["hosts"]
    as_of = s0._parse_iso(base["as_of"])

    def problems(entry):
        return s0.validate_snapshot_entry("serpapi", entry, hosts=hosts, as_of=as_of)

    good = base["upstream_schema_snapshots"]["serpapi"]
    assert problems(good) == []

    tampered = dict(good)
    tampered["snapshot_serialized"] = good["snapshot_serialized"].replace(
        '"wallet"', '"bank"')
    assert "SNAPSHOT_HASH_MISMATCH" in problems(tampered), problems(tampered)

    count = dict(good)
    count["tool_count"] = 2
    assert "SNAPSHOT_TOOL_COUNT_MISMATCH" in problems(count), problems(count)

    endpoint = dict(good)
    endpoint["endpoint"] = "https://vendor.example/mcp"
    assert "SNAPSHOT_ENDPOINT_NOT_ALLOWED" in problems(endpoint), problems(endpoint)

    stale = dict(good)
    stale["captured_at"] = "2020-01-01T00:00:00Z"
    assert "SNAPSHOT_STALE" in problems(stale), problems(stale)

    future = dict(good)
    future["captured_at"] = "2027-01-01T00:00:00Z"
    assert "SNAPSHOT_CAPTURED_IN_FUTURE" in problems(future), problems(future)


def test_a_valid_snapshot_unlocks_the_gateway_and_a_boolean_does_not(clean_env):
    """Paired: same everything, one variable — the capture.

    Both halves assert on `gateway_invoked`. The paired form is what makes the
    refusal meaningful: a gate that always refuses would pass the negative half
    alone.
    """
    s0 = _require("research_s0_preflight_gate", "S0 preflight gate")
    _require("research_wiring", "wiring module")

    class Gateway:
        def __init__(self):
            self.calls = 0

        def invoke(self, **kwargs):
            self.calls += 1
            return SimpleNamespace(status="OK", reason_code="")

    allowed = Gateway()
    ok = s0.guarded_invoke(
        allowed, _valid_evidence(), capability_id="crawl4ai.fetch_page", params={},
        run_id="r1", operation_id="o1", account_ref="acct-local-1",
        intent_fingerprint="i1")
    assert ok.gateway_invoked is True, ok
    assert allowed.calls == 1

    boolean_only = _valid_evidence()
    boolean_only.pop("upstream_schema_snapshots")
    refused = Gateway()
    bad = s0.guarded_invoke(
        refused, boolean_only, capability_id="crawl4ai.fetch_page", params={},
        run_id="r1", operation_id="o1", account_ref="acct-local-1",
        intent_fingerprint="i1")
    assert bad.gateway_invoked is False, bad
    assert refused.calls == 0, "a refused gate still invoked the gateway"


# --- T0 local: the gateway plane must not dial ----------------------------


def test_a_t0_invocation_is_local_and_settles_at_zero(clean_env, data_root):
    """T0 through the real gateway must not open a socket.

    The registry's T0 entries are read-only *vendor* reads, so the zero-socket
    property is measured with a local stand-in adapter that declares one T0
    capability. What is under test is the gateway/ledger plane around it: no
    reservation bypass, no network, and a settled row with zero actual cost.
    """
    import socket

    wiring = _require("research_wiring", "wiring module")
    registry = _require("research_capability_registry", "capability registry")
    policy = _require("research_policy", "policy module")
    ledger_module = _require("research_usage_ledger", "usage ledger")

    # This adapter is an explicitly local, no-socket test control. Give it an
    # explicit zero-price fixture; the product's unverified credits-only USD
    # placeholder cannot establish a zero charge for an actual vendor read.
    local_pricing = ledger_module.PricingCatalog.from_entries([{
        "provider": "tinyfish", "capability": "wallet_read", "unit_micros": 0,
        "quota_unit": "requests", "quota_per_call": 1,
        "source": "SYNTHETIC_LOCAL_CONTROL_NOT_VENDOR_RATE", "version": "test-1",
        "observed_at": "2026-10-20T00:00:00+00:00", "verified": True, "free_tier": True,
    }])
    ledger = ledger_module.for_root(data_root, pricing=local_pricing)
    project = ledger_module.project_for_root(data_root)
    ledger.set_cap("run", f"{project}/r1", ceiling_calls=50, source="qa-fixture")

    class LocalT0:
        provider = "tinyfish"

        def __init__(self):
            self.calls = 0

        def capabilities(self):
            return ("tinyfish.get_wallet",)

        def probe_schema(self):
            return {"status": "AUTH_REQUIRED"}

        def execute(self, request):
            self.calls += 1
            return SimpleNamespace(outcome="OK", payload={"balance": "synthetic"},
                                   artifacts=(), upstream_ref="local:1",
                                   actual_usd=None, actual_source=None,
                                   quota_units_used=1, transport="official_mcp",
                                   schema_verdict="UNVERIFIED", caveats=())

        def cancel(self, upstream_ref):
            raise ValueError(f"CAPABILITY_NOT_AVAILABLE: {upstream_ref}")

    adapter = LocalT0()
    built = wiring.build_gateway(
        root=data_root, adapters={"tinyfish": adapter}, ledger=ledger,
        policy=policy.Policy(registry=registry.DEFAULT_REGISTRY, network_enabled=True))

    attempts = []
    real_connect = socket.socket.connect

    def refuse(sock, address, *a, **k):
        attempts.append(address)
        raise AssertionError(f"a T0 invocation opened a socket to {address!r}")

    socket.socket.connect = refuse
    try:
        outcome = built.invoke(
            capability_id="tinyfish.get_wallet", params={}, run_id="r1",
            operation_id="r1t0", account_ref="tinyfish-main",
            intent_fingerprint="i-t0")
    finally:
        socket.socket.connect = real_connect

    assert attempts == [], attempts
    assert outcome.status == "OK", outcome
    assert outcome.provider == "tinyfish"
    assert adapter.calls == 1, "the adapter was not the one that ran"
    row = ledger.get("r1t0")
    assert row.charge_state == "SETTLED", row
    assert row.actual_micros == 0, row
