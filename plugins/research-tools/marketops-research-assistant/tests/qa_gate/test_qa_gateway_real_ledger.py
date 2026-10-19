"""The real capability gateway driven against the real usage ledger.

Round 5. Until now this gate exercised ``CapabilityGateway`` with **stub**
collaborators, because no ledger implementation shipped next to it. The
integration candidate `6f0219d` ("the gateway could not complete a single
managed call") supplies the missing seam: ``CapabilityGateway(ledger_request=...)``
translates the contract's ``CapabilityRequest`` into the ledger's own
``OperationRequest``. Before it, the gateway handed the ledger the contract's
object, the ledger raised ``AttributeError: 'CapabilityRequest' object has no
attribute 'project'``, and the gateway reported that as if the *ledger had
refused* — a wiring failure wearing a budget refusal's clothes.

These tests wire the two real modules together over a temporary SQLite file and a
fake provider. Nothing here reaches a network, a credential or a provider; the
adapter is a recorder. The stub is now only the *registry, policy and contract*,
which are still not product modules.

Independence: this file builds its own collaborators and does not import the
Integrator's ``_wave1_contract_fixture``. Where it needs to know a product
vocabulary (the reviewed capability names) it reads it from the product module,
so a rename shows up as a failure rather than as a silently skipped test.
"""
from __future__ import annotations

import importlib
import importlib.util
from types import SimpleNamespace

import pytest

from conftest import SCRIPTS, blocked

GATEWAY_MODULES = ("research_gateway",)
WIRING_MODULES = ("research_wiring",)
LEDGER_MODULES = ("research_usage_ledger",)

CAPABILITY = "serper.search"
PROVIDER = "serper"
REVIEWED = "search"


def _import_first(names, what):
    for name in names:
        if importlib.util.find_spec(name) is not None:
            return importlib.import_module(name)
    pytest.fail(
        f"this candidate ships no {what}. Searched importlib.util.find_spec over "
        f"sys.path[0]={SCRIPTS}; none of {list(names)} resolve.")


@pytest.fixture()
def parts(data_root):
    gateway = _import_first(GATEWAY_MODULES, "capability gateway")
    wiring = _import_first(WIRING_MODULES, "wiring module")
    ledger_module = _import_first(LEDGER_MODULES, "usage ledger")

    if (PROVIDER, CAPABILITY) not in wiring.REVIEWED_CAPABILITY:
        blocked(f"the reviewed-capability table no longer maps "
                f"{PROVIDER}/{CAPABILITY}; this file's fixtures name it directly "
                f"and a rename would make every assertion below meaningless")

    catalog = ledger_module.PricingCatalog.from_entries([{
        "provider": PROVIDER, "capability": REVIEWED, "unit_micros": 0,
        "quota_unit": "credits", "quota_per_call": 1, "source": "qa-gate",
        "version": "credit-only-1", "observed_at": "2026-10-17T00:00:00+00:00",
        "verified": True,
    }])

    class _Request:
        def __init__(self, **kwargs):
            self.__dict__.update(kwargs)

    class _Contract:
        CapabilityRequest = _Request
        OutcomeCode = SimpleNamespace(OK="OK", BLOCKED="BLOCKED", UNKNOWN="UNKNOWN")
        AdapterResult = staticmethod(lambda **kw: SimpleNamespace(**kw))

    class _Policy:
        def __init__(self, verdict="ALLOW", reason_code=None):
            self._decision = SimpleNamespace(verdict=verdict, reason_code=reason_code)
            self.calls = 0

        def evaluate(self, request):
            self.calls += 1
            return self._decision

    class _Registry:
        def __init__(self, entry):
            self._entry = entry

        def get(self, capability_id):
            return self._entry if capability_id == self._entry.capability_id else None

    class _Adapter:
        def __init__(self, result=None, raises=None):
            self._result, self._raises, self.calls = result, raises, 0

        def capabilities(self):
            return [CAPABILITY]

        def execute(self, request):
            self.calls += 1
            if self._raises is not None:
                raise self._raises
            return self._result

    entry = SimpleNamespace(
        capability_id=CAPABILITY, provider=PROVIDER, transport="rest", tier="T2",
        availability="ENABLED", allowed_params=("q",),
        # The wiring bridge now reads the registry's own bridge field. A fixture
        # entry without it produced `WiringIncomplete: missing
        # ledger_capability[serper.search]` — the live entry is the authority, so
        # the fixture has to carry what the real registry carries.
        ledger_capability=REVIEWED)

    def ledger():
        path = data_root / "state" / "dual-search-ledger.sqlite3"
        path.parent.mkdir(parents=True, exist_ok=True)
        instance = ledger_module.UsageLedger(path, pricing=catalog)
        instance.set_cap("run", f"{ledger_module.project_for_root(data_root)}/runA",
                         ceiling_calls=1_000, source="qa-gate")
        instance.set_cap("project", ledger_module.project_for_root(data_root),
                         ceiling_calls=1_000, source="qa-gate")
        return instance

    def gateway_for(adapter, *, policy=None, ledger_request="default", root=None):
        kwargs = {"registry": _Registry(entry), "policy": policy or _Policy(),
                  "ledger": ledger(), "adapters": {PROVIDER: adapter},
                  "contract": _Contract()}
        if ledger_request == "default":
            kwargs["ledger_request"] = wiring.ledger_request_builder(root or data_root)
        elif ledger_request is not None:
            kwargs["ledger_request"] = ledger_request
        return gateway.CapabilityGateway(**kwargs)

    def invoke(instance, **overrides):
        call = dict(capability_id=CAPABILITY, params={"q": "synthetic"},
                    run_id="runA", operation_id="op-1", account_ref="acct-local-1",
                    intent_fingerprint="intent-1")
        call.update(overrides)
        return instance.invoke(**call)

    return SimpleNamespace(gateway=gateway, wiring=wiring, ledger_module=ledger_module,
                           adapter=_Adapter, ledger=ledger, gateway_for=gateway_for,
                           invoke=invoke, contract=_Contract, policy=_Policy)


def _rows(ledger_module, data_root):
    path = data_root / "state" / "dual-search-ledger.sqlite3"
    return ledger_module.UsageLedger(path).operations(project=None, run_id=None)


# --- the call the fix enabled ---------------------------------------------


def test_a_managed_call_completes_through_the_real_ledger(parts, data_root):
    """The headline the candidate claims: one managed call, end to end.

    Real gateway, real ledger, real SQLite file; only the provider is fake. A
    green here means the two shapes actually meet — which is the thing
    `6f0219d` says it fixed.
    """
    adapter = parts.adapter(result=SimpleNamespace(
        outcome="OK", schema_verdict="MATCH", upstream_ref="up-1",
        actual_usd=None, actual_source=None, quota_units_used=1, caveats=()))
    instance = parts.gateway_for(adapter)
    outcome = parts.invoke(instance)

    assert outcome.status == "OK", (outcome.status, outcome.reason_code, outcome.caveats)
    assert adapter.calls == 1, "the adapter was not dispatched exactly once"

    rows = _rows(parts.ledger_module, data_root)
    assert len(rows) == 1, rows
    row = rows[0]
    assert row.operation_id == "op-1"
    assert row.charge_state in {"SETTLED", "FREE_TIER"}, row.charge_state
    assert row.quota_units == 1, row.quota_units


def test_the_call_books_under_the_roots_project_not_a_path(parts, data_root):
    """A project is an identifier. The seam exists so the gateway cannot invent it.

    `ledger_request_builder` is the only place that knows the data root; every
    other candidate was forbidden by the contract. Asserting the booked project
    equals `project_for_root(root)` is what makes "nobody chose a project" a
    detectable statement rather than a claim.
    """
    expected = parts.ledger_module.project_for_root(data_root)
    assert expected and expected != str(data_root), expected

    adapter = parts.adapter(result=SimpleNamespace(
        outcome="OK", schema_verdict="MATCH", upstream_ref="u", actual_usd=None,
        actual_source=None, quota_units_used=1, caveats=()))
    parts.invoke(parts.gateway_for(adapter))

    rows = _rows(parts.ledger_module, data_root)
    assert [row.project for row in rows] == [expected], rows
    assert parts.wiring.ledger_request_builder(data_root).project == expected


def test_the_gateway_and_the_ledger_agree_on_the_operation_id(parts):
    adapter = parts.adapter(result=SimpleNamespace(
        outcome="OK", schema_verdict="MATCH", upstream_ref="u", actual_usd=None,
        actual_source=None, quota_units_used=1, caveats=()))
    outcome = parts.invoke(parts.gateway_for(adapter))
    assert outcome.reservation is not None
    operation_id = getattr(outcome.reservation, "operation_id", None) or \
        getattr(outcome.reservation, "ledger_id", None)
    assert operation_id == "op-1", operation_id


# --- the defect the fix names ---------------------------------------------


def test_an_untranslated_request_is_named_not_read_as_a_refusal(parts):
    """Without the seam the failure must be a *wiring* violation.

    The pre-`6f0219d` behaviour handed the contract's request to the ledger,
    which raised `AttributeError`, which the gateway reported as though the
    ledger had refused. "This budget was refused" and "nothing ever translated
    between the two request shapes" are different facts, and only one of them is
    actionable.
    """
    adapter = parts.adapter(result=SimpleNamespace(
        outcome="OK", schema_verdict="MATCH", upstream_ref="u", actual_usd=None,
        actual_source=None, quota_units_used=1, caveats=()))
    instance = parts.gateway_for(adapter, ledger_request=None)
    with pytest.raises(parts.gateway.ContractViolation) as raised:
        parts.invoke(instance)
    assert "ledger_request" in str(raised.value), raised.value
    assert adapter.calls == 0, "an untranslated request still reached the provider"


def test_an_unreviewed_capability_is_refused_rather_than_guessed(parts, data_root):
    """The vocabulary bridge refuses an unknown id instead of stripping a prefix.

    A fallback that guessed would silently turn an unreviewed capability into a
    reviewed one — the thing the registry exists to prevent.
    """
    adapter = parts.adapter(result=SimpleNamespace(
        outcome="OK", schema_verdict="MATCH", upstream_ref="u", actual_usd=None,
        actual_source=None, quota_units_used=1, caveats=()))
    builder = parts.wiring.ledger_request_builder(data_root)
    entry = SimpleNamespace(capability_id="serper.unreviewed", provider=PROVIDER,
                            transport="rest")
    request = SimpleNamespace(run_id="runA", operation_id="op-1",
                              account_ref="acct-local-1", params={},
                              intent_fingerprint="i", grant_ref="")
    with pytest.raises(parts.gateway.WiringIncomplete):
        builder(entry, request)
    assert adapter.calls == 0


# --- the ledger's own semantics, reached through the gateway --------------


def test_a_policy_denial_never_reaches_the_adapter_or_the_ledger(parts):
    adapter = parts.adapter()
    instance = parts.gateway_for(adapter, policy=parts.policy(
        verdict="DENY", reason_code="POLICY_TIER_NOT_GRANTED"))
    outcome = parts.invoke(instance)
    assert outcome.status == "BLOCKED"
    assert outcome.reason_code == "POLICY_TIER_NOT_GRANTED", outcome.reason_code
    assert adapter.calls == 0, "a denied call reached the provider"


def test_a_dispatched_failure_holds_the_charge_in_the_real_ledger(parts, data_root):
    """An unclassifiable failure keeps its hold — it is not settled as a refusal."""
    adapter = parts.adapter(raises=ConnectionResetError("socket dropped"))
    instance = parts.gateway_for(adapter)
    outcome = parts.invoke(instance)
    assert outcome.status == "UNKNOWN", (outcome.status, outcome.reason_code)

    rows = _rows(parts.ledger_module, data_root)
    assert len(rows) == 1
    assert rows[0].charge_state == "UNKNOWN", rows[0].charge_state
    assert rows[0].charge_state != "SETTLED"


def test_a_proven_local_refusal_settles_without_charging(parts, data_root):
    adapter = parts.adapter(raises=ValueError("EGRESS_REFUSED"))
    instance = parts.gateway_for(adapter)
    outcome = parts.invoke(instance)
    assert outcome.status == "BLOCKED"
    assert outcome.reason_code == "EGRESS_REFUSED", outcome.reason_code

    rows = _rows(parts.ledger_module, data_root)
    assert len(rows) == 1
    assert rows[0].charge_state != "UNKNOWN", rows[0].charge_state


def test_a_ledger_refusal_is_a_governed_outcome_not_an_exception(parts, data_root):
    """IF-5 at the seam: the ledger's refusal must survive as a BLOCKED outcome.

    This is the round-5 finding. The ledger refuses a call it should refuse (its
    run ceiling is spent), and the gateway cannot express that refusal, so
    ``invoke`` raises instead of returning. Two causes, both measurable:

    * ``LedgerRefused`` carries ``.code`` and derives from ``Exception``;
      ``_reason_code_from`` reads ``.reason_code`` and otherwise only splits a
      ``ValueError``'s message, so it gets ``None``.
    * the two vocabularies barely overlap — the ledger raises 34 distinct codes,
      the gateway's frozen closed set has 22, and their intersection is one
      entry (``OPERATION_ID_COLLISION``). A ceiling refusal arrives as
      ``CALL_CAP_EXCEEDED`` / ``MONEY_CAP_EXCEEDED`` and the contract's name for
      that is ``BUDGET_RUN_CEILING``.

    The result is that "the ledger refused to spend" reaches a caller as an
    unhandled ``ContractViolation`` — the same class of confusion the
    ``ledger_request`` seam was added to remove, one layer further down.
    """
    ledger_module = parts.ledger_module
    path = data_root / "state" / "dual-search-ledger.sqlite3"
    path.parent.mkdir(parents=True, exist_ok=True)
    project = ledger_module.project_for_root(data_root)
    instance = parts.gateway_for(parts.adapter(result=SimpleNamespace(
        outcome="OK", schema_verdict="MATCH", upstream_ref="u", actual_usd=None,
        actual_source=None, quota_units_used=1, caveats=())))
    instance.ledger.set_cap("run", f"{project}/runA", ceiling_calls=1, source="qa-gate")
    instance.ledger.set_cap("project", project, ceiling_calls=1, source="qa-gate")

    first = parts.invoke(instance, operation_id="op-1")
    assert first.status == "OK", (first.status, first.reason_code, first.caveats)

    try:
        second = parts.invoke(instance, operation_id="op-2", params={"q": "another"})
    except parts.gateway.ContractViolation as raised:
        pytest.fail(
            "a real ledger refusal escaped invoke() as an exception instead of "
            "returning a governed BLOCKED outcome (IF-5). The ledger refused "
            "because the run's call ceiling was spent; the gateway could not "
            f"name it. {raised}")
    assert second.status == "BLOCKED", (second.status, second.reason_code)
    assert second.reason_code in parts.gateway.REASON_CODES, (
        f"the refusal's code {second.reason_code!r} is outside the frozen closed "
        "set, so a caller cannot branch on it")
