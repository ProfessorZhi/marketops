"""Round 17 — the TinyFish account-read decision, tested as a closed contract.

B reviewed three read-only methods as documented T1 account reads; C narrowed the
adapter's *advertisement* to the reviewed subset. The acceptance questions are
about the negatives, not the happy path:

* the registry is the authority: T1 / READ_ONLY / exact reviewed parameters;
* the adapter's default advertisement contains nothing the registry has not
  reviewed, and the unreviewed list carries nothing the registry has reviewed;
* `get_monitor` requires ``monitor_id`` and nothing else; `list_monitors` takes no
  arguments; `list_browser_sessions` is reviewed to ``limit``/``page`` only;
* the billable/state-changing monitor and browser-session verbs stay disabled;
* S0 still DENIES without an official B0, a known price, a full tools/list
  capture and a confirmed key rotation;
* none of the three can produce chargeable egress.

``DOCUMENTED_OFFLINE``: B's review is of the vendor's published catalog, not a
live account capture. No official LIVE verification is claimed.

Offline. No provider, no credential, no paid call.
"""
from __future__ import annotations

import importlib
import importlib.util
from types import SimpleNamespace

import pytest

from conftest import SCRIPTS, SocketSentinel

MONITORS = {
    "tinyfish.get_monitor": frozenset({"monitor_id"}),
    "tinyfish.list_monitors": frozenset(),
    "tinyfish.list_browser_sessions": frozenset({"limit", "page"}),
}

#: The billable / state-changing family that must never be advertised or callable.
T4_FAMILY = (
    "create_monitor", "run_monitor", "pause_monitor", "resume_monitor",
    "cancel_monitor", "create_browser_session", "close_browser_session",
    "create_profile", "list_profiles", "start_profile_setup_session",
    "save_profile_setup_session", "cancel_profile_setup_session",
)


def _mod(name):
    if importlib.util.find_spec(name) is None:
        pytest.fail(f"missing {name}; sys.path[0]={SCRIPTS}")
    return importlib.import_module(name)


def test_registry_reviews_the_three_as_t1_read_only_with_exact_params(clean_env):
    registry = _mod("research_capability_registry")
    for capability_id, expected in MONITORS.items():
        entry = registry.DEFAULT_REGISTRY.get(capability_id)
        assert entry is not None, f"not reviewed: {capability_id}"
        assert str(entry.tier) == "T1", (capability_id, entry.tier)
        assert str(entry.effect_class) == "READ_ONLY", (capability_id, entry.effect_class)
        assert str(entry.availability) == "ENABLED", (capability_id, entry.availability)
        assert set(entry.allowed_params) == set(expected), (
            capability_id, sorted(entry.allowed_params))
        assert not entry.escalates_to, (capability_id, entry.escalates_to)
        assert entry.ledger_capability == "usage_read", (capability_id, entry.ledger_capability)


def test_the_default_advertisement_is_a_subset_of_the_registry(clean_env):
    tiny = _mod("research_adapter_tinyfish")
    registry = _mod("research_capability_registry")
    reviewed = {entry.capability_id for entry in registry.DEFAULT_REGISTRY}
    advertised = set(tiny.capabilities())
    assert advertised <= reviewed, sorted(advertised - reviewed)

    unreviewed = set(tiny.unreviewed_capabilities())
    assert not (advertised & unreviewed), sorted(advertised & unreviewed)


def test_no_id_the_registry_reviews_is_labelled_unreviewed(clean_env):
    """A reviewed capability must not be described as unreviewed by the adapter.

    This is the C-side half of the decision: B's review is authoritative, so the
    adapter's review label and its `unreviewed_capabilities()` list must follow
    it. A mismatch leaves reviewed capabilities non-invocable and mislabelled.
    """
    tiny = _mod("research_adapter_tinyfish")
    registry = _mod("research_capability_registry")
    reviewed = {entry.capability_id for entry in registry.DEFAULT_REGISTRY}
    mislabelled = sorted(set(tiny.unreviewed_capabilities()) & reviewed)
    assert mislabelled == [], (
        "the registry reviews these, the adapter still calls them unreviewed: "
        f"{mislabelled}")


def test_get_monitor_requires_monitor_id_and_nothing_else(clean_env):
    tiny = _mod("research_adapter_tinyfish")
    assert tiny.validate("get_monitor", {"monitor_id": "mon-1"}) == {"monitor_id": "mon-1"}
    for bad in ({}, {"run_id": "mon-1"}, {"monitor_id": "mon-1", "extra": 1},
                {"monitor_id": ""}, {"monitor_id": 7}):
        with pytest.raises(ValueError):
            tiny.validate("get_monitor", bad)


def test_list_monitors_accepts_no_arguments(clean_env):
    tiny = _mod("research_adapter_tinyfish")
    assert tiny.validate("list_monitors", {}) == {}
    for bad in ({"limit": 1}, {"page": 1}, {"monitor_id": "mon-1"}):
        with pytest.raises(ValueError):
            tiny.validate("list_monitors", bad)


def test_list_browser_sessions_is_reviewed_to_limit_and_page_only(clean_env):
    """The gateway enforces the review; the adapter must not accept more.

    Both layers are asserted. The registry is the authority, so the gateway
    blocking a surplus key passes; the adapter's own validator accepting an
    unreviewed key is the failure this test exists to expose.
    """
    tiny = _mod("research_adapter_tinyfish")
    registry = _mod("research_capability_registry")
    entry = registry.DEFAULT_REGISTRY.get("tinyfish.list_browser_sessions")
    assert entry is not None
    assert set(entry.allowed_params) == {"limit", "page"}, sorted(entry.allowed_params)

    assert tiny.validate("list_browser_sessions", {"limit": 10, "page": 1}) == {
        "limit": 10, "page": 1}

    surplus = ({"status": "running"}, {"session_id": "s-1"},
               {"start_after": "2026-01-01"}, {"end_before": "2026-02-01"})
    accepted = []
    for arguments in surplus:
        try:
            tiny.validate("list_browser_sessions", arguments)
        except ValueError:
            continue
        accepted.append(sorted(arguments))
    assert accepted == [], (
        "the adapter accepts parameters the registry did not review: "
        f"{accepted}; the gateway blocks them, but a direct adapter call does not")


def test_the_gateway_blocks_surplus_monitor_arguments_before_dispatch(clean_env, tmp_path):
    wiring = _mod("research_wiring")
    registry = _mod("research_capability_registry")
    policy = _mod("research_policy")
    ledger_module = _mod("research_usage_ledger")

    class Stub:
        provider = "tinyfish"
        transport = "official_mcp"

        def __init__(self):
            self.calls = []

        def capabilities(self):
            return tuple(MONITORS)

        def probe_schema(self):
            return {"status": "AUTH_REQUIRED"}

        def execute(self, request):
            self.calls.append((request.capability_id, dict(request.params)))
            return SimpleNamespace(outcome="OK", actual_usd=None, actual_source=None,
                                   quota_units_used=1, upstream_ref="x",
                                   schema_verdict="UNVERIFIED", caveats=())

        def cancel(self, upstream_ref):
            return {"outcome": "BLOCKED"}

    root = tmp_path / "data"
    root.mkdir()
    ledger = ledger_module.for_root(root)
    project = ledger_module.project_for_root(root)
    ledger.set_cap("run", f"{project}/r1", ceiling_calls=50, source="qa")
    stub = Stub()
    gateway = wiring.build_gateway(
        root=root, adapters={"tinyfish": stub},
        policy=policy.Policy(registry=registry.DEFAULT_REGISTRY, network_enabled=True))

    with SocketSentinel():
        blocked_out = gateway.invoke(
            capability_id="tinyfish.list_browser_sessions",
            params={"status": "running"}, run_id="r1", operation_id="op-bad",
            account_ref="acct", intent_fingerprint="i")
    assert blocked_out.status == "BLOCKED", blocked_out
    assert blocked_out.reason_code == "CAPABILITY_PARAM_NOT_ALLOWED", blocked_out
    assert stub.calls == [], stub.calls


def test_the_billable_and_state_changing_family_stays_disabled(clean_env):
    tiny = _mod("research_adapter_tinyfish")
    advertised = set(tiny.capabilities())
    for tool in T4_FAMILY:
        capability_id = "tinyfish." + tool
        assert capability_id not in advertised, capability_id
        with pytest.raises(ValueError):
            tiny.validate(tool, {})
    catalog = tiny.capability_catalog()["tools"]
    for tool in ("create_monitor", "close_browser_session", "list_profiles"):
        assert catalog[tool]["enabled"] is False, tool
        assert catalog[tool]["billing"]["may_charge"] in (True, False)  # described only
    for tool in ("get_monitor", "list_monitors", "list_browser_sessions"):
        assert catalog[tool]["billing"]["may_charge"] is False, tool
        assert catalog[tool]["requires_explicit_human_authorization"] is False, tool


def _evidence(providers, snapshot=True):
    gate = _mod("research_s0_preflight_gate")
    payload = {"tools": [{"name": "search"}]}
    serialized = gate.canonical_snapshot(payload)
    digest = gate.snapshot_digest(payload)
    entry = {
        "version": gate.SNAPSHOT_VERSION, "provider": "serpapi",
        "endpoint": "https://managed.example/mcp", "method": "tools/list",
        "source": "probe_schema@official_mcp",
        "captured_at": "2026-10-18T00:00:00Z",
        "egress_decision_ref": "egress:qa", "audit_ref": "audit:qa",
        "snapshot_serialized": serialized, "schema_sha256": digest,
        "registry_schema_sha256": digest, "tool_count": 1,
    }
    evidence = {
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
        "providers": providers,
    }
    if snapshot:
        evidence["upstream_schema_snapshots"] = {"serpapi": entry}
    return evidence


def test_s0_denies_without_official_b0_price_capture_or_key_rotation(clean_env):
    gate = _mod("research_s0_preflight_gate")
    metered = {"captured": True, "b0": "1.00", "unit": "USD", "meter": "requests",
               "price_source": "qa_synthetic_rate",
               "last_observed_at": "2026-10-18T00:00:00Z", "unknown_exposure": "0",
               "next_op_estimate": "0.01", "schema_verified": True,
               "key_rotation_confirmed": True, "auto_topup": False}

    cases = {
        "no B0": (dict(metered, captured=False, b0=None), "WALLET_PRECONDITION_FAILED"),
        "unknown price": (dict(metered, next_op_estimate=None),
                          "PRICE_UNKNOWN_FAIL_CLOSED"),
        "key rotation unconfirmed": (dict(metered, key_rotation_confirmed=False),
                                     "WALLET_PRECONDITION_FAILED"),
    }
    for label, (account, expected) in cases.items():
        decision = gate.evaluate(_evidence({"serpapi": account}))
        assert decision["launch"] == "DENY", (label, decision)
        assert decision["s1"]["reason_code"] == expected, (label, decision["s1"])

    no_snapshot = gate.evaluate(_evidence({"serpapi": metered}, snapshot=False))
    assert no_snapshot["launch"] == "DENY", no_snapshot
    assert "S0_07_UPSTREAM_SCHEMA_VERIFIED" in no_snapshot["s0"]["failed"]
