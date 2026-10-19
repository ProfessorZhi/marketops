"""The Coordinator's frozen invariants, one assertion per invariant.

Source: ``docs/wave1-coordination/INTERFACE-CONTRACT.md`` §10 (v1.0 FROZEN), which
the integration acceptance checklist §B says must each carry "至少一个断言测试".

Where the candidate ships the surface (`research_gateway.CapabilityGateway`) these
tests drive **that real code** with injected stub collaborators — the gateway
takes registry/policy/ledger/adapters/contract by injection, so every invariant
is checkable offline with no network and no provider. Where the surface is absent
the test fails naming the invariant and the exact modules searched, which is the
honest verdict for a Wave whose convergence target names that surface; it is not
a framework error and must not be read as one.

Negative readings carry what was searched, per checklist §G-2.
"""
from __future__ import annotations

import importlib
import importlib.util

import pytest

from conftest import SCRIPTS

SEARCHED = (f"importlib.util.find_spec over sys.path[0]={SCRIPTS}, plus a "
            f"direct file check of that directory")

GATEWAY_MODULES = ("research_gateway",)
WIRING_MODULES = ("research_wiring",)
REGISTRY_MODULES = ("research_registry", "research_capability_registry",
                    "capability_registry", "research_gateway")
LEDGER_MODULES = ("research_ledger", "research_usage_ledger", "usage_ledger")


def _present(names):
    for name in names:
        if importlib.util.find_spec(name) is not None:
            return name, importlib.import_module(name)
    return None, None


def _require(names, invariant: str, what: str):
    name, module = _present(names)
    if module is None:
        pytest.fail(
            f"{invariant}: this candidate exposes no {what}. Searched {SEARCHED}; "
            f"none of {list(names)} resolve. The invariant cannot be asserted, "
            "which is FAIL for the wave, not a pass.")
    return module


# --- stub collaborators ----------------------------------------------------
# Deliberately minimal: they exist to make the gateway's *own* decisions
# observable (which branch it took, what it refused, what it never touched).


class _Entry:
    def __init__(self, capability_id="search.web", provider="serper",
                 transport="rest", tier="T2", availability="ENABLED",
                 allowed_params=("q",)):
        self.capability_id = capability_id
        self.provider = provider
        self.transport = transport
        self.tier = tier
        self.availability = availability
        self.allowed_params = tuple(allowed_params)


class _Registry:
    def __init__(self, entry=None):
        self.entry = entry if entry is not None else _Entry()
        self.calls = []

    def get(self, capability_id):
        self.calls.append(capability_id)
        return self.entry if capability_id == self.entry.capability_id else None


class _Request:
    def __init__(self, **kwargs):
        self.__dict__.update(kwargs)


class _Decision:
    def __init__(self, verdict="ALLOW", reason_code=None):
        self.verdict = verdict
        self.reason_code = reason_code


class _Policy:
    """A stub policy that records whether it was consulted at all."""

    def __init__(self, verdict="ALLOW", reason_code=None):
        self._decision = _Decision(verdict, reason_code)
        self.calls = 0

    def evaluate(self, request):
        self.calls += 1
        return self._decision


class _Reservation:
    """A reservation carrying the row's own operation id, as the ledger returns.

    The gateway reads ``operation_id`` (falling back to ``ledger_id``) to know
    which row to book the terminal state against; a reservation with neither is
    a contract violation there, not something to paper over.
    """

    def __init__(self, operation_id="op-1", reserved_micros=0):
        self.operation_id = operation_id
        self.reserved_micros = reserved_micros
        self.price_source = "qa-gate-stub"


class _Ledger:
    """The gateway's ledger surface, as the corrected contract calls it.

    This stub is deliberately kept to the *method names and signatures the
    gateway actually uses*. An earlier version mirrored the pre-`6f0219d`
    signatures (``reserve(request, decision)``, ``settle(id, result)``); when the
    seam was introduced those calls changed and the stub reported the change as
    five invariant failures. Keeping the stub to the called surface is what makes
    a real signature correction visible as a *product* change rather than as a
    wall of stub errors.
    """

    def __init__(self, raises=None):
        self.reserved, self.settled, self.held, self.proved = [], [], [], []
        self.dispatched = []
        self._raises = raises

    def reserve(self, request):
        if self._raises is not None:
            raise self._raises
        self.reserved.append(request)
        return _Reservation(operation_id=getattr(request, "operation_id", "op-1"))

    def get(self, operation_id):
        return _Reservation(operation_id=operation_id)

    def mark_dispatch_intent(self, operation_id):
        self.dispatched.append(operation_id)

    def mark_unknown(self, operation_id, *, reason=""):
        self.held.append((operation_id, reason))

    def prove_no_charge(self, operation_id, *, reason=""):
        self.proved.append((operation_id, reason))

    def settle(self, operation_id, *, actual_micros, actual_source,
               upstream_ref="", quota_units=None):
        self.settled.append((operation_id, actual_micros, actual_source))


class _Result:
    def __init__(self, outcome="OK", caveats=(), upstream_ref=None):
        self.outcome = outcome
        self.caveats = tuple(caveats)
        self.upstream_ref = upstream_ref
        self.schema_verdict = "MATCH"


class _Adapter:
    def __init__(self, result=None, raises=None):
        self.result = result if result is not None else _Result()
        self._raises = raises
        self.calls = 0

    def capabilities(self):
        return ["search.web"]

    def execute(self, request):
        self.calls += 1
        if self._raises is not None:
            raise self._raises
        return self.result


class _OutcomeCode:
    OK = "OK"
    BLOCKED = "BLOCKED"
    UNKNOWN = "UNKNOWN"


class _AdapterResult:
    def __init__(self, outcome=None, caveats=()):
        self.outcome = outcome
        self.caveats = tuple(caveats)
        self.upstream_ref = None
        self.schema_verdict = "MATCH"


class _Contract:
    CapabilityRequest = _Request
    AdapterResult = _AdapterResult
    OutcomeCode = _OutcomeCode


class _Harness:
    """One assembled gateway plus the fakes, so a test can assert what ran."""

    def __init__(self, gateway, registry, policy, ledger, adapter):
        self.gateway = gateway
        self.registry = registry
        self.policy = policy
        self.ledger = ledger
        self.adapter = adapter

    def invoke(self, **overrides):
        call = dict(capability_id="search.web", params={"q": "synthetic"},
                    run_id="runA", operation_id="op1", account_ref="acct",
                    intent_fingerprint="intent-1")
        call.update(overrides)
        return self.gateway.invoke(**call)


def _harness(entry=None, verdict="ALLOW", reason_code=None, result=None,
             adapter_raises=None, ledger_raises=None, policy=None):
    gateway_module = _require(GATEWAY_MODULES, "IR/IL gateway", "capability gateway")
    registry = _Registry(entry)
    policy = policy if policy is not None else _Policy(verdict, reason_code)
    ledger = _Ledger(ledger_raises)
    adapter = _Adapter(result, adapter_raises)
    gateway = gateway_module.CapabilityGateway(
        registry=registry, policy=policy, ledger=ledger,
        adapters={"serper": adapter}, contract=_Contract())
    return _Harness(gateway, registry, policy, ledger, adapter)


# --- Registry invariants (IR-*) -------------------------------------------


def test_ir_1_an_unknown_capability_id_is_blocked_without_touching_anything():
    """IR-1: local BLOCKED, no adapter, no policy, no ledger, no network."""
    harness = _harness()
    outcome = harness.invoke(capability_id="capability-that-does-not-exist")
    assert outcome.status == "BLOCKED"
    assert outcome.reason_code == "CAPABILITY_UNKNOWN"
    assert harness.adapter.calls == 0, "an unknown capability reached the adapter"
    assert harness.ledger.reserved == [], "an unknown capability made a reservation"
    assert harness.policy.calls == 0, "an unknown capability consulted the policy plane"


def test_ir_2_an_out_of_scope_parameter_is_blocked_and_the_value_is_not_echoed():
    """IR-2: surplus keys are refused by name; the value may itself be the secret."""
    harness = _harness()
    secret = "qa-gate-SYNTHETICKEY-DEADBEEF"
    outcome = harness.invoke(params={"q": "synthetic", "api_key": secret})
    assert outcome.status == "BLOCKED"
    assert outcome.reason_code == "CAPABILITY_PARAM_NOT_ALLOWED"
    assert "PARAMS_OUTSIDE_ALLOWED_SUBSET" in outcome.caveats, outcome.caveats
    assert secret not in repr(outcome), "a refused parameter value was echoed back"
    assert harness.adapter.calls == 0


def test_ir_3_agent_supplied_tier_endpoint_and_tool_name_are_ignored():
    """IR-3: the Registry is the allowlist authority, not the caller."""
    harness = _harness()
    claims = {"tier": "T0", "endpoint": "https://evil.example.org",
              "tool_name": "delete_everything", "budget_usd": "9999"}
    outcome = harness.invoke(agent_claims=claims)
    assert set(outcome.ignored_agent_claims) == set(claims), outcome.ignored_agent_claims
    assert "evil.example.org" not in repr(outcome)
    assert outcome.status != "BLOCKED" or outcome.reason_code != "CAPABILITY_UNKNOWN"


def test_ir_4_schema_drift_disables_the_capability_from_t2_upward():
    """IR-4: DRIFT/ABSENT stops the capability at T2 and above."""
    drifting = _Entry(availability="SCHEMA_DRIFT", tier="T2")
    outcome = _harness(entry=drifting).invoke()
    assert outcome.status == "BLOCKED"
    assert outcome.reason_code == "CAPABILITY_SCHEMA_DRIFT"

    for tier in ("T3", "T4"):
        higher = _harness(entry=_Entry(availability="SCHEMA_DRIFT", tier=tier)).invoke()
        assert higher.reason_code == "CAPABILITY_SCHEMA_DRIFT", (tier, higher.reason_code)


def test_schema_drift_below_t2_is_dispatched_and_the_contract_is_silent_on_it():
    """IR-4 as written gates T2 and above; it says nothing about T0/T1.

    This pins the observable behaviour so the question is visible rather than
    silently decided: at T1 a drifted capability is **not** refused — it goes to
    the policy/adapter path. Whether a free local capability should also stop on
    drift is an owner/contract question, not a QA judgement, so this test records
    the behaviour and does not bless it.
    """
    harness = _harness(entry=_Entry(availability="SCHEMA_DRIFT", tier="T1"))
    outcome = harness.invoke()
    assert outcome.reason_code != "CAPABILITY_SCHEMA_DRIFT", (
        "IR-4 is applied below the schema gate; the contract claims T2+ only")
    assert harness.adapter.calls == 1, (
        "T1 + SCHEMA_DRIFT reached the adapter — recorded here, contract silent")


def test_ir_5_no_upstream_text_is_carried_into_the_outcome():
    """IR-5: an upstream tool description is never a returned instruction.

    The gateway's outcome shape carries no description field, and the allowlist
    comes from the registry entry rather than from anything the adapter says
    about itself.
    """
    from dataclasses import fields

    gateway_module = _require(GATEWAY_MODULES, "IR-5", "capability gateway")
    names = {f.name for f in fields(gateway_module.GatewayOutcome)}
    assert not {n for n in names if "description" in n or "instruction" in n}, names

    # An adapter that claims a capability the registry does not list is refused,
    # not believed.
    harness = _harness()
    harness.adapter.capabilities = lambda: []
    outcome = harness.invoke()
    assert outcome.status == "BLOCKED"
    assert outcome.reason_code == "CAPABILITY_NOT_AVAILABLE", outcome.reason_code


def test_the_gateway_refusal_vocabulary_is_the_frozen_closed_set():
    """IR-5/IF-5: refusals are codes from INTERFACE-CONTRACT §5.3, not prose."""
    gateway_module = _require(GATEWAY_MODULES, "IF-5", "capability gateway")
    for code in ("CAPABILITY_UNKNOWN", "CAPABILITY_PARAM_NOT_ALLOWED",
                 "CAPABILITY_SCHEMA_DRIFT", "CAPABILITY_NOT_AVAILABLE",
                 "UNKNOWN_CHARGE_OUTSTANDING", "EGRESS_REFUSED",
                 "PRICING_SOURCE_MISSING", "DUPLICATE_OPERATION",
                 "BUDGET_RUN_CEILING", "POLICY_TIER_NOT_GRANTED"):
        assert code in gateway_module.REASON_CODES, code


def test_a_policy_refusal_outside_the_closed_set_is_a_contract_violation():
    """IF-5: an unclassifiable refusal is raised, never mapped to a plausible code."""
    gateway_module = _require(GATEWAY_MODULES, "IF-5", "capability gateway")
    harness = _harness(verdict="DENY", reason_code="because I said so")
    with pytest.raises(gateway_module.ContractViolation):
        harness.invoke()


# --- Ledger invariants, asserted at the gateway boundary (IL-*) -----------


def test_il_3_an_unclassifiable_adapter_failure_holds_the_charge_as_unknown():
    """IL-3: a dispatched failure that is not a *provable* local refusal holds
    budget as UNKNOWN_CHARGE_OUTSTANDING — no refund, no automatic retry."""
    harness = _harness(adapter_raises=ConnectionResetError("socket dropped"))
    outcome = harness.invoke()
    assert outcome.status == "UNKNOWN"
    assert outcome.reason_code == "UNKNOWN_CHARGE_OUTSTANDING"
    assert harness.ledger.held and not harness.ledger.settled, (
        "an unclassifiable failure was settled (refunded) instead of held")
    assert harness.ledger.proved == [], (
        "an unclassifiable failure was recorded as a proven no-charge")
    assert harness.adapter.calls == 1, "the adapter was dispatched more than once"


def test_il_4_a_provable_local_refusal_settles_instead_of_holding():
    """IL-4: NO_CHARGE_PROVEN only when the refusal is provable — here a
    ``ValueError`` carrying a closed-set code, the repository's refusal channel."""
    harness = _harness(adapter_raises=ValueError("EGRESS_REFUSED"))
    outcome = harness.invoke()
    assert outcome.status == "BLOCKED"
    assert outcome.reason_code == "EGRESS_REFUSED"
    # Under the corrected contract a refusal made *before* dispatch reaches
    # ``prove_no_charge`` — the one verb that says a charge provably did not
    # happen. It must not reach ``settle``, which books an actual amount, and it
    # must not reach ``mark_unknown``, which holds budget for a call that never
    # left.
    assert harness.ledger.proved, "a provable pre-dispatch refusal did not settle"
    assert not harness.ledger.settled, (
        "a pre-dispatch refusal was booked as an actual charge")
    assert not harness.ledger.held, (
        "a provable local refusal held budget as an unknown charge")


def test_il_6_a_ledger_refusal_outside_the_closed_set_is_a_contract_violation():
    """IL-6/IL-7: the ledger's refusal vocabulary is the same closed set."""
    gateway_module = _require(GATEWAY_MODULES, "IL-6", "capability gateway")
    harness = _harness(ledger_raises=ValueError("out of budget, sorry"))
    with pytest.raises(gateway_module.ContractViolation):
        harness.invoke()


def test_il_7_a_closed_set_budget_refusal_from_the_ledger_is_returned_as_blocked():
    """IL-7: a ceiling reached is a BLOCKED with the budget code — and the
    adapter is never dispatched (the refusal happens *before* the network)."""
    harness = _harness(ledger_raises=ValueError("BUDGET_RUN_CEILING"))
    outcome = harness.invoke()
    assert outcome.status == "BLOCKED"
    assert outcome.reason_code == "BUDGET_RUN_CEILING"
    assert harness.adapter.calls == 0, "a refusal dispatched the adapter anyway"


def test_il_9_a_reservation_without_an_identity_is_a_contract_violation():
    """IL-1/IL-8: an unidentifiable reservation cannot be reconciled later."""
    gateway_module = _require(GATEWAY_MODULES, "IL-1", "capability gateway")

    class _AnonLedger(_Ledger):
        def reserve(self, request, decision):
            return object()  # no ledger_id

    registry = _Registry()
    gateway = gateway_module.CapabilityGateway(
        registry=registry, policy=_Policy(),
        ledger=_AnonLedger(), adapters={"serper": _Adapter()}, contract=_Contract())
    with pytest.raises(gateway_module.ContractViolation):
        gateway.invoke(capability_id="search.web", params={"q": "synthetic"},
                       run_id="runA", operation_id="op1", account_ref="acct",
                       intent_fingerprint="intent-1")


# --- the seam itself ------------------------------------------------------


def test_the_wiring_seam_names_every_absent_collaborator_and_its_owner():
    """The candidate ships a *seam*, and the seam must be readable.

    ``research_wiring.probe()`` is the resume trigger. It must report exactly
    which contract collaborators are absent and who owns each, so a reader never
    has to infer 'nothing is wired' from an ImportError.
    """
    wiring = _require(WIRING_MODULES, "seam", "wiring probe")
    report = wiring.probe()
    for key in ("state", "worker_plan_complete", "present", "missing", "owners",
                "missing_by_owner", "searched"):
        assert key in report, key
    assert report["state"] in {"WAITING_FOR_WORKERS", "WORKERS_PRESENT"}
    assert report["worker_plan_complete"] is (not report["missing"])
    if report["missing"]:
        assert set(report["owners"]) == set(report["missing"])
        assert "directory" in report["searched"]


def test_build_gateway_refuses_precisely_while_collaborators_are_absent(data_root):
    """The integration step must refuse with the *named* gap, not a generic error.

    While the worker modules were absent this exercised the refusal. They have
    landed, so the branch below runs instead: the factory either assembles from
    its defaults or names what it cannot find. Both directions are asserted — a
    factory that quietly returned a half-built gateway would pass a refusal-only
    test.
    """
    wiring = _require(WIRING_MODULES, "seam", "wiring probe")
    gateway_module = _require(GATEWAY_MODULES, "seam", "capability gateway")
    report = wiring.probe()
    if not report["missing"]:
        built = wiring.build_gateway(root=data_root)
        assert hasattr(built, "invoke"), type(built)
        return
    with pytest.raises(gateway_module.WiringIncomplete) as raised:
        wiring.build_gateway()
    assert raised.value.missing, "WiringIncomplete carried no missing set"
    for name in raised.value.missing:
        assert isinstance(name, str) and name, raised.value.missing


def test_the_contract_module_seam_reports_its_own_absence():
    """A missing contract module is a declared seam, not a traceback."""
    gateway_module = _require(GATEWAY_MODULES, "IR-1", "capability gateway")
    registry = _Registry()
    gateway = gateway_module.CapabilityGateway(
        registry=registry, policy=_Policy(),
        ledger=_Ledger(), adapters={"serper": _Adapter()},
        contract_module_name="research_contract_that_does_not_exist")
    with pytest.raises(gateway_module.WiringIncomplete):
        gateway.invoke(capability_id="search.web", params={"q": "x"},
                       run_id="runA", operation_id="op1", account_ref="acct",
                       intent_fingerprint="intent-1")


def test_the_unified_usage_ledger_is_still_absent_as_a_named_module():
    """IL-1..IL-8 cannot be asserted against an implementation that is not here.

    This is the honest negative: the checklist allows the ledger to be attached
    later, but until the module exists the invariants are FAIL, not `BLOCKED`,
    because no external authorization is what is missing — the code is.
    """
    name, module = _present(LEDGER_MODULES)
    if module is None:
        pytest.fail(
            f"IL-1..IL-8 cannot be asserted: searched {SEARCHED}; none of "
            f"{list(LEDGER_MODULES)} resolve. The gateway seam accepts an injected "
            "ledger, but no ledger implementation ships with this candidate.")
    has_reserve = hasattr(module, "reserve") or any(
        isinstance(a, type) and hasattr(a, "reserve") for a in vars(module).values())
    assert has_reserve, (
        "the ledger module exposes no reserve() at module or class level; names "
        f"were {sorted(n for n in dir(module) if not n.startswith('_'))}")
