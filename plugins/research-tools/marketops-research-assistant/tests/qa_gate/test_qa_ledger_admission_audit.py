"""Independent audit of the ledger's admission decisions and its money honesty.

Round 6. The ledger lane (`0cb1ff9`) changed two things this file exists to pin:

* **Admission.** A dollar-metered campaign floor can only judge a call whose
  dollar rate is known. The shipped catalog prices every provider at zero dollars
  with a real credit unit, so a floor compared against that number never moves —
  a US$1.00 wallet with a US$0.20 floor admitted twelve chargeable calls while
  its committed dollars stayed at zero. The fix refuses such a call
  (``CAMPAIGN_PRICE_UNKNOWN_FAIL_CLOSED``) and must **not** refuse a genuine
  free tier or a priced capability.
* **Reporting.** "Cost per accepted output" used to be
  ``actual_micros // accepted_outputs``, which prints ``0`` in the two ordinary
  cases where the money is not knowable. It is now withheld with a basis code.
  This file checks both directions: withheld when unknowable, present when known.

Every test here builds its own catalog and campaign over a temporary database.
No network, no credential, no provider, no paid call.
"""
from __future__ import annotations

import importlib
import importlib.util

import pytest

from conftest import SCRIPTS

LEDGER_MODULES = ("research_usage_ledger",)
USD = 1_000_000


def _ledger_module():
    for name in LEDGER_MODULES:
        if importlib.util.find_spec(name) is not None:
            return importlib.import_module(name)
    pytest.fail(
        f"no usage ledger ships with this candidate. Searched "
        f"importlib.util.find_spec over sys.path[0]={SCRIPTS}; none of "
        f"{list(LEDGER_MODULES)} resolve.")


@pytest.fixture()
def api(data_root):
    module = _ledger_module()

    def build(prices, *, campaign=None, path_suffix=""):
        path = data_root / "state" / f"ledger{path_suffix}.sqlite3"
        path.parent.mkdir(parents=True, exist_ok=True)
        kwargs = {"pricing": module.PricingCatalog.from_entries(prices)}
        if campaign is not None:
            kwargs["campaign_budgets"] = campaign
        return module.UsageLedger(path, **kwargs)

    def request(index=0, *, provider="serper", capability="search", units=1,
                run_id="runA", project="proj"):
        return module.OperationRequest(
            project=project, run_id=run_id, operation_id=f"op{index}",
            provider=provider, capability=capability, account_ref="acct-local-1",
            transport="rest", intent=f"intent-{index}",
            args={"q": f"query-{index}"}, units=units)

    return module, build, request


def _price(module, *, provider="serper", capability="search", unit_micros=0,
           free_tier=False, quota_unit="credits", verified=True):
    return {
        "provider": provider, "capability": capability, "unit_micros": unit_micros,
        "quota_unit": quota_unit, "quota_per_call": 1, "source": "qa-gate",
        "version": "v1", "observed_at": "2026-10-17T00:00:00+00:00",
        "verified": verified, "free_tier": free_tier,
    }


def _usd_campaign(module, *, units_usd="1.00"):
    baseline = module.CampaignBaseline(
        provider="serper", account_ref="acct-local-1", meter=module.USD_MICROS,
        units=int(float(units_usd) * module.MICRO),
        observed_at="2026-10-17T00:00:00+00:00",
        source=module.MeterReadingSource.VENDOR_ACCOUNT_READ, campaign_id="qa-campaign")
    return {("serper", "acct-local-1"): module.CampaignBudget(module.CampaignFloor(baseline))}


def _caps(ledger, module, *, calls=1_000):
    project = "proj"
    ledger.set_cap("run", f"{project}/runA", ceiling_calls=calls, source="qa-gate")
    ledger.set_cap("project", project, ceiling_calls=calls, source="qa-gate")


def _dispatch_then_settle(ledger, operation, *, micros, source, upstream_ref=""):
    """Book an actual charge the way the ledger requires it to be booked.

    Two refusals are deliberate and the test must not skip them:
    ``NOT_DISPATCHED`` — a settle without a recorded dispatch intent counts a
    charge for a call the ledger cannot see leaving; and ``EVIDENCE_REQUIRED`` —
    a provider-reported amount must name the provider record it came from.
    Recording both is what makes these rows real, rather than the fixture
    asserting against an idealised ledger that does not exist.
    """
    ledger.mark_dispatch_intent(operation.operation_id)
    return ledger.settle(operation.operation_id, actual_micros=micros,
                         actual_source=source, upstream_ref=upstream_ref)


def _code(exc):
    """The refusal's stable code, whichever attribute this ledger version uses."""
    for attribute in ("code", "reason_code"):
        value = getattr(exc, attribute, None)
        if isinstance(value, str) and value:
            return value
    return str(exc).split(":", 1)[0]


# --- admission: an unknown dollar rate is not a free one -------------------


def test_a_usd_floor_refuses_a_zero_dollar_rate_it_cannot_judge(api):
    """§1.6 "unknown ... do not run that paid operation", in the meter it is billed in.

    The shipped catalog's shape is ``unit_micros=0, free_tier=False``: nobody has
    a verified dollar rate. A dollar floor judged against that admits unlimited
    work, so the operation must be refused **before any row is written**.
    """
    module, build, request = api
    ledger = build([_price(module, unit_micros=0, free_tier=False)],
                   campaign=_usd_campaign(module))
    _caps(ledger, module)
    with pytest.raises(Exception) as refused:  # noqa: BLE001
        ledger.reserve(request(0))
    assert _code(refused.value) == "CAMPAIGN_PRICE_UNKNOWN_FAIL_CLOSED", \
        refused.value

    # Fail *closed*, not fail *after writing*: an unknown charge that leaves a row
    # behind is exposure nobody accounted for.
    assert ledger.operations(project=None, run_id=None) == (), (
        "the refused operation still wrote a ledger row")


def test_a_genuine_free_tier_is_not_refused_by_the_unknown_price_gate(api):
    """The fix must not turn "free" into "refused".

    ``free_tier=True`` is a *known* rate of zero, which is different from an
    unknown one. A gate that refuses both would be right for the wrong reason.
    """
    module, build, request = api
    ledger = build([_price(module, unit_micros=0, free_tier=True)],
                   campaign=_usd_campaign(module))
    _caps(ledger, module)
    operation = ledger.reserve(request(0))
    assert operation is not None


def test_a_priced_capability_is_judged_by_the_floor_not_refused(api):
    """A known rate reaches the floor; the unknown-price gate must not catch it."""
    module, build, request = api
    # US$0.25 a call against a US$1.00 wallet with a US$0.20 floor: the first is
    # admitted, the fourth would leave US$0.00.
    ledger = build([_price(module, unit_micros=250_000, quota_unit="searches")],
                   campaign=_usd_campaign(module))
    _caps(ledger, module)
    for index in range(3):
        ledger.reserve(request(index))
    with pytest.raises(Exception) as refused:  # noqa: BLE001
        ledger.reserve(request(3))
    assert "FLOOR" in _code(refused.value) or "CAMPAIGN" in _code(refused.value), \
        refused.value


def test_an_unwired_campaign_is_reported_rather_than_inferred(api):
    """An opt-out nobody can see is indistinguishable from an oversight."""
    module, build, request = api
    ledger = build([_price(module, unit_micros=0, free_tier=False)])
    _caps(ledger, module)
    status = ledger.campaign_status()
    assert _code(status) == "CAMPAIGN_NOT_CONFIGURED", status
    # And the opt-out is a real opt-out: the call runs.
    assert ledger.reserve(request(0)) is not None


# --- reporting: an unknown cost is not zero -------------------------------


def test_report_withholds_cost_per_output_when_nothing_has_settled(api):
    module, build, request = api
    ledger = build([_price(module, unit_micros=0, free_tier=False)])
    _caps(ledger, module)
    ledger.reserve(request(0))
    report = ledger.report("proj", accepted_outputs=5)
    assert report["cost_per_accepted_output_micros"] is None, report
    assert report["cost_per_accepted_output_basis"] == "NO_SETTLED_OPERATIONS", report


def test_report_withholds_cost_per_output_when_a_rate_is_unverified(api):
    module, build, request = api
    ledger = build([_price(module, unit_micros=250_000, quota_unit="searches",
                           verified=False)])
    _caps(ledger, module)
    operation = ledger.reserve(request(0))
    _dispatch_then_settle(ledger, operation, micros=250_000, source="DERIVED")
    report = ledger.report("proj", accepted_outputs=1)
    assert report["cost_per_accepted_output_micros"] is None, report
    assert report["cost_per_accepted_output_basis"] == "PRICE_UNVERIFIED", report


def test_report_withholds_cost_per_output_while_exposure_is_unreconciled(api):
    module, build, request = api
    ledger = build([_price(module, unit_micros=250_000, quota_unit="searches")])
    _caps(ledger, module)
    first = ledger.reserve(request(0))
    _dispatch_then_settle(ledger, first, micros=250_000, source="DERIVED")
    # A dispatched call whose charge nobody could establish. This is the exposure
    # the basis code exists for; a merely RESERVED row is a different state and
    # this test would otherwise be describing it as if it were UNKNOWN.
    second = ledger.reserve(request(1))
    ledger.mark_dispatch_intent(second.operation_id)
    ledger.mark_unknown(second.operation_id, reason="AMBIGUOUS_RESPONSE")
    report = ledger.report("proj", accepted_outputs=1)
    assert second is not None
    assert report["cost_per_accepted_output_micros"] is None, report
    assert report["cost_per_accepted_output_basis"] == "UNKNOWN_COST_OUTSTANDING", report
    assert report["money"]["unknown_exposure_micros"] > 0, report["money"]


def test_report_still_reports_the_number_when_the_money_is_knowable(api):
    """The withholding must be conditional, or it is just a constant."""
    module, build, request = api
    ledger = build([_price(module, unit_micros=250_000, quota_unit="searches")])
    _caps(ledger, module)
    operation = ledger.reserve(request(0))
    _dispatch_then_settle(ledger, operation, micros=250_000,
                          source="PROVIDER_REPORTED", upstream_ref="provider-receipt-1")
    report = ledger.report("proj", accepted_outputs=1)
    assert report["cost_per_accepted_output_micros"] == 250_000, report
    assert report["cost_per_accepted_output_basis"] == "OK", report
    assert report["money"]["basis"] == "VERIFIED_ACTUAL", report["money"]


def test_the_money_basis_code_is_always_present(api):
    """A reader must be able to tell "zero" from "not knowable" without guessing."""
    module, build, request = api
    ledger = build([_price(module, unit_micros=0, free_tier=False)])
    report = ledger.report("proj")
    assert "basis" in report["money"] and "basis_code" in report["money"], report["money"]
    assert report["cost_per_accepted_output_basis"], report
