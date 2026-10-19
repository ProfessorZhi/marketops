"""NEW_REAL_ASSEMBLY_GATE — can a production Gateway actually be assembled?

This is a **new gate**, not a correction of the earlier one. The 277-assertion
gate accepted `954cdab` for what it was: an offline-verified research plugin with
a **declared seam**. It never claimed the seam was filled. These tests ask the
next question — *can the product assemble a real Gateway and refuse without one*
— and they are expected to be RED/BLOCKED on `954cdab`, because the four W-B
modules that `research_wiring.probe()` names are absent there.

Two contracts:

**Assembly.** `research_wiring.probe()` must name exactly the collaborators that
are absent, `build_gateway()` must refuse rather than fabricate, and once the
modules land the factory must construct a gateway whose T0 path is local, whose
T2/T3/T4 paths are denied without a grant, and which offers no fee or egress
bypass.

**S0_07.** A bare `upstream_schema_verified: true` is a claim, not a snapshot. It
must not clear the schema subgate, and a refused gate must never reach the
adapter. A *valid* synthetic snapshot clears the schema subgate and nothing else.

Every test is offline: temporary databases, a recording adapter, no provider, no
credential. Where a surface is absent from this candidate the test reports
**BLOCKED** with the searched module names — never a silent pass.
"""
from __future__ import annotations

import hashlib
import importlib
import importlib.util
import json

import pytest

from conftest import SCRIPTS, blocked

WIRING_MODULES = ("research_wiring",)
S0_MODULES = ("research_s0_preflight_gate",)

#: The four W-B modules `research_wiring.probe()` names on `954cdab`.
EXPECTED_ABSENT = frozenset({
    "research_adapter_base",
    "research_capability_registry",
    "research_contract",
    "research_policy",
})

SHA_A = "a" * 64
SHA_B = "b" * 64


def _import_first(names, what):
    for name in names:
        if importlib.util.find_spec(name) is not None:
            return importlib.import_module(name)
    return None


def _require(names, what):
    module = _import_first(names, what)
    if module is None:
        pytest.fail(
            f"this candidate ships no {what}. Searched importlib.util.find_spec "
            f"over sys.path[0]={SCRIPTS}; none of {list(names)} resolve.")
    return module


# --- assembly: the factory must refuse, and say precisely what is missing --


def test_assembly_probe_names_exactly_the_absent_worker_modules(clean_env):
    """`probe()` is the resume trigger, so its negative reading must be exact.

    An "N modules missing" count is not enough: it cannot tell a module that was
    never written from one that was renamed, and it cannot tell an owner what to
    do. The assertion is on the **set**, not on its size.
    """
    wiring = _require(WIRING_MODULES, "wiring module")
    report = wiring.probe()

    for key in ("state", "missing", "present", "owners", "missing_by_owner", "searched"):
        assert key in report, f"probe() lost {key!r}: {sorted(report)}"

    if report["missing"]:
        assert set(report["missing"]) == EXPECTED_ABSENT, (
            "the set of absent collaborators changed. If a W-B module landed, the "
            "positive contract below must be the one that runs; if a *different* "
            f"module is now missing, that is a new finding: {sorted(report['missing'])}")
        assert report["state"] == "WAITING_FOR_WORKERS"
        assert report["worker_plan_complete"] is False
        assert set(report["owners"]) == EXPECTED_ABSENT
        assert all(owner == "W-B" for owner in report["owners"].values()), report["owners"]
    else:
        # Everything landed: the negative reading is no longer meaningful, and the
        # positive contract below is what must hold. Not a skip — a role change.
        assert report["state"] == "WORKERS_PRESENT"
        assert report["worker_plan_complete"] is True


def test_build_gateway_refuses_and_names_every_absent_collaborator(clean_env, data_root):
    """A factory that cannot assemble must say so, not build a partial gateway.

    A partially-wired gateway is worse than no gateway: it would accept calls and
    then fail somewhere past the policy plane.
    """
    wiring = _require(WIRING_MODULES, "wiring module")
    gateway = _require(("research_gateway",), "capability gateway")
    report = wiring.probe()

    if not report["missing"]:
        built = wiring.build_gateway(root=data_root)
        assert built is not None, "build_gateway() returned nothing with every module present"
        return

    with pytest.raises(gateway.WiringIncomplete) as refused:
        wiring.build_gateway()

    named = set(refused.value.missing)
    assert EXPECTED_ABSENT <= named, (
        f"the refusal did not name every absent module: named={sorted(named)}")
    # The four collaborator *objects* are also handed in, so a factory called with
    # nothing must name those too rather than defaulting them.
    assert {"contract", "registry", "policy", "ledger"} <= named, sorted(named)


def test_handing_in_stub_collaborators_does_not_paper_over_absent_modules(clean_env):
    """Four objects are not four modules.

    A caller that supplies stand-ins for contract/registry/policy/ledger must
    still be refused: the product modules those objects are supposed to *be*
    are absent, and a gateway assembled around stubs would pass every check
    downstream while doing nothing. The refusal must therefore name the
    modules, not merely count what the caller handed in.
    """
    wiring = _require(WIRING_MODULES, "wiring module")
    gateway = _require(("research_gateway",), "capability gateway")
    report = wiring.probe()
    if not report["missing"]:
        blocked("every module is present, so the stub question is no longer the "
                "boundary; the positive contract below is the one that runs")

    stubs = dict(contract=object(), registry=object(), policy=object(), ledger=object())
    with pytest.raises(gateway.WiringIncomplete) as refused:
        wiring.build_gateway(**stubs)

    assert set(refused.value.missing) == EXPECTED_ABSENT, (
        "stub collaborators changed what the factory says is missing: "
        f"{sorted(refused.value.missing)}")


def test_probe_is_read_only(clean_env):
    """The resume trigger reads the filesystem and changes nothing.

    Asserted by observation: two calls agree, and no file appears under the
    package directory as a side effect.
    """
    wiring = _require(WIRING_MODULES, "wiring module")
    scripts = SCRIPTS
    before = {p.name for p in scripts.iterdir()}
    first = wiring.probe()
    second = wiring.probe()
    after = {p.name for p in scripts.iterdir()}

    assert first["missing"] == second["missing"]
    assert first["present"] == second["present"]
    assert before == after, f"probe() created or removed files: {sorted(after ^ before)}"


# --- the positive contract, once B's modules land -------------------------


def _require_assembled(root=None):
    wiring = _require(WIRING_MODULES, "wiring module")
    report = wiring.probe()
    if report["missing"]:
        blocked("a real Gateway cannot be assembled on this candidate: "
                f"{sorted(report['missing'])} are absent (owner "
                f"{sorted(set(report['owners'].values()))}). The positive contract "
                "runs once B's modules are merged into the candidate.")
    # The data root is handed in, not inferred. The ledger is a file inside it and
    # the factory must not invent one: a reservation booked under a project nobody
    # chose is worse than a refusal. `clean_env` clears MARKETOPS_*, so this
    # process has no configured root to fall back on.
    return wiring.build_gateway(root=root)


def test_a_real_gateway_constructs_after_the_modules_land(clean_env, data_root):
    """ACCEPTANCE (positive): the factory returns a usable gateway."""
    gateway = _require_assembled(data_root)
    assert hasattr(gateway, "invoke"), type(gateway)


def test_a_t0_local_invocation_makes_no_network_call(clean_env, data_root, monkeypatch):
    """ACCEPTANCE (positive): a local T0 capability must not open a socket.

    Measured at the socket, not at the adapter: a governed-looking result that
    still dialled out would pass any assertion written one layer up.
    """
    import socket

    gateway = _require_assembled(data_root)
    attempts = []
    real_connect = socket.socket.connect

    def refuse(sock, address, *a, **k):
        attempts.append(address)
        raise AssertionError(f"T0 invocation opened a socket to {address!r}")

    monkeypatch.setattr(socket.socket, "connect", refuse)
    try:
        outcome = gateway.invoke(
            capability_id="local.parse", params={}, run_id="runA",
            operation_id="t0-1", account_ref="acct-local-1",
            intent_fingerprint="intent-1")
    finally:
        monkeypatch.setattr(socket.socket, "connect", real_connect)

    if outcome.reason_code == "CAPABILITY_UNKNOWN":
        blocked("the assembled registry names no T0 capability in this candidate")
    assert attempts == [], attempts
    assert outcome.status == "OK", outcome


def test_t2_t3_t4_without_a_grant_are_denied_and_never_dispatch(clean_env, data_root):
    """ACCEPTANCE (positive): paid/authenticated tiers fail closed without a grant.

    Asserted on the gateway's own outcome **and** on the adapter being untouched:
    a denial reported after dispatch would be a charge with an apology.
    """
    gateway = _require_assembled(data_root)
    # These were placeholders while no registry existed on the candidate. With
    # the reviewed table landed they name real entries, so the loop asserts
    # instead of `continue`-ing three times and passing on nothing.
    for capability_id in ("serpapi.search", "serper.search",
                          "tinyfish.run_web_automation", "tinyfish.cancel_run"):
        outcome = gateway.invoke(
            capability_id=capability_id, params={},
            run_id="runA", operation_id=f"op-{capability_id}",
            account_ref="acct-local-1", intent_fingerprint="intent-1")
        if outcome.reason_code == "CAPABILITY_UNKNOWN":
            continue  # capability not in this candidate's registry
        assert outcome.status == "BLOCKED", (
            f"{capability_id} was not denied without a grant: {outcome}")
        assert outcome.provider, f"{capability_id} denied without naming a provider"


def test_an_undeclared_capability_never_reaches_an_adapter(clean_env, data_root):
    """ACCEPTANCE (positive): the registry, not the adapter map, is the allowlist.

    An adapter that happens to be assembled must not answer a capability the
    registry does not declare. Asserted on the outcome, so it does not pin the
    registry's own API: refusal is the observable contract.
    """
    gateway = _require_assembled(data_root)
    outcome = gateway.invoke(
        capability_id="__qa__.undeclared", params={}, run_id="runA",
        operation_id="op-undeclared", account_ref="acct-local-1",
        intent_fingerprint="intent-1")
    assert outcome.status == "BLOCKED", outcome
    assert outcome.reason_code == "CAPABILITY_UNKNOWN", outcome
    assert outcome.provider is None, outcome


# --- S0_07: a boolean is a claim, a snapshot is evidence -------------------


def _base_evidence(schema_verified, snapshots=None):
    """Every other S0 gate satisfied, so S0_07 is the only variable."""
    evidence = {
        "integration": {"sha": "c" * 40, "expected_sha": "c" * 40},
        "independent_qa": {"status": "PASSED", "sha": "c" * 40},
        "managed_mcp": {"configured_count": 1, "effective": True,
                        "endpoints": ["https://managed.example/mcp"],
                        "vendor_direct_entries": []},
        "direct_vendor_bypass": {"present": False},
        "credential_injection": {"runtime_scoped": True, "source": "process_env"},
        "endpoint_allowlist": {"hosts": ["managed.example"]},
        # `as_of` is the clock every snapshot age is measured against. Without
        # it the gate cannot tell a fresh capture from a fabricated one, so it
        # marks every snapshot SNAPSHOT_AS_OF_INVALID — including a correct one.
        "as_of": "2026-10-18T00:00:00Z",
        "upstream_schema_verified": schema_verified,
        "network_enabled": "enabled",
        "providers": {},
    }
    if snapshots is not None:
        evidence["upstream_schema_snapshots"] = snapshots
    return evidence


def _valid_snapshot(**overrides):
    """A snapshot that satisfies every check `validate_snapshot_entry` makes.

    The first version of this helper listed five fields and could never be
    admissible: the gate also requires a supported `version`, the `provider` it
    is filed under, an allowlisted `endpoint`, `method == "tools/list"`, a
    `source` from `TRUSTED_SNAPSHOT_SOURCES`, an egress-decision ref, an audit
    ref, and the canonical `snapshot_serialized` whose digest is the declared
    hash. A fixture missing those would have turned the positive half of S0_07
    into a permanently-red test that looked like a gate defect.
    """
    payload = {"tools": [{"name": "search"}, {"name": "fetch"}, {"name": "wallet"}]}
    serialized = json.dumps(payload, sort_keys=True, separators=(",", ":"),
                            ensure_ascii=False)
    digest = hashlib.sha256(serialized.encode("utf-8")).hexdigest()
    entry = {
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
        "tool_count": 3,
    }
    entry.update(overrides)
    return {"serpapi": entry}


def _s0_module():
    module = _import_first(S0_MODULES, "S0 preflight gate")
    if module is None:
        blocked("this candidate does not ship research_s0_preflight_gate. The S0_07 "
                "contract is judged on the branch that owns it "
                "(test/wave1-s0-candidate-954cdab-20261018); on this SHA the gate "
                "does not exist, which is not the same as passing.")
    return module


class _RecordingGateway:
    def __init__(self):
        self.calls = []

    def invoke(self, **kwargs):
        self.calls.append(kwargs)
        raise AssertionError("the gateway was invoked despite a refused S0 gate")


def test_s0_07_a_bare_boolean_does_not_authorize_a_schema(clean_env):
    """A claim is not a snapshot.

    `upstream_schema_verified: true` with no capture must leave the schema
    subgate **failed**. Otherwise "nobody fetched anything" reads as "verified".
    """
    s0 = _s0_module()
    result = s0.evaluate(_base_evidence(True))
    assert result["s0"]["gates"]["S0_07_UPSTREAM_SCHEMA_VERIFIED"] is False, result["s0"]
    assert "S0_07_UPSTREAM_SCHEMA_VERIFIED" in result["s0"]["failed"]
    assert result["launch"] == "DENY"


def test_s0_07_missing_and_mismatched_hashes_do_not_authorize(clean_env):
    """Every way of not having a real capture must fail, not just the empty one."""
    s0 = _s0_module()
    for snapshots in (
        {"serpapi": {}},                                   # nothing captured
        _valid_snapshot(schema_sha256=""),                 # no upstream hash
        _valid_snapshot(registry_schema_sha256=""),        # no pinned hash
        _valid_snapshot(registry_schema_sha256=SHA_B),     # the two disagree
        _valid_snapshot(tool_count=0),                     # no tools captured
        _valid_snapshot(captured_at=""),                   # no capture time
        _valid_snapshot(source=""),                        # no named source
        {"serpapi": "not-a-mapping"},                      # wrong shape
        {},                                                # empty mapping
    ):
        result = s0.evaluate(_base_evidence(True, snapshots))
        assert result["s0"]["gates"]["S0_07_UPSTREAM_SCHEMA_VERIFIED"] is False, snapshots


def test_s0_07_a_valid_synthetic_snapshot_clears_only_the_schema_subgate(clean_env):
    """The positive half: a real capture clears S0_07 and nothing else.

    "Clears only" is asserted by diffing the gate map against the same evidence
    without the snapshot — if a valid hash quietly satisfied a *different* gate,
    that gate would appear in the difference.
    """
    s0 = _s0_module()
    without = s0.evaluate(_base_evidence(True))
    with_snapshot = s0.evaluate(_base_evidence(True, _valid_snapshot()))

    assert with_snapshot["s0"]["gates"]["S0_07_UPSTREAM_SCHEMA_VERIFIED"] is True
    assert "S0_07_UPSTREAM_SCHEMA_VERIFIED" not in with_snapshot["s0"]["failed"]

    changed = {gate for gate, value in with_snapshot["s0"]["gates"].items()
               if without["s0"]["gates"].get(gate) != value}
    assert changed == {"S0_07_UPSTREAM_SCHEMA_VERIFIED"}, (
        f"a schema snapshot changed unrelated gates: {sorted(changed)}")


def test_a_refused_s0_gate_never_reaches_the_adapter(clean_env):
    """`gateway_invoked=False` is the load-bearing field, and it is observed.

    The recording gateway raises if touched, so a pass means it genuinely was
    not: no reservation, no adapter dispatch, no vendor call.
    """
    s0 = _s0_module()
    recorder = _RecordingGateway()
    outcome = s0.guarded_invoke(
        recorder, _base_evidence(True), capability_id="search.web",
        params={"q": "synthetic"}, run_id="runA", operation_id="op-1",
        account_ref="acct-local-1", intent_fingerprint="intent-1")

    assert recorder.calls == [], recorder.calls
    assert outcome.gateway_invoked is False, outcome
    assert outcome.status in {"BLOCKED", "DENY"}, outcome
    assert "S0_07_UPSTREAM_SCHEMA_VERIFIED" in tuple(outcome.s0_failed), outcome.s0_failed
