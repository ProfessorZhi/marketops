"""The campaign floor must bind in the meter the provider is actually billed in.

Owner source: ``MARKETOPS_LIVE_CC_QUALITY_AND_BUDGET_GATE_2026-10-17.md`` §1 —
"honor the 20% floor on verified remaining paid credits/monetary wallet **and
separately on applicable finite period quota**" (§1.5), and "If unsupported,
unknown, stale or unverifiable: do **not** run that paid operation" (§1.6).

Why this file exists
--------------------
The ledger prices every one of the five providers from
``DOCUMENTED_CREDIT_RATES`` with ``unit_micros = 0`` — a credit meter, not a
money meter. ``UsageLedger.reserve`` then guards the campaign floor with::

    if self._campaign and estimated_micros > 0:
        self._check_campaign_floor(...)

and ``estimated_micros`` is ``price.unit_micros * units``, i.e. **always 0** for
those providers. The floor is therefore never consulted in the default
deployment, while ``_check_campaign_floor`` itself carries a correct credit-meter
branch — the guard and the function it guards disagree about what the floor is
for. Measured directly: with a project ceiling of 100 micro-USD, **30
credit-priced reservations were granted and none refused**, ``spent_micros``
stayed 0, and only the quota column moved (``credits: 30``).

That is the "credit-only floor bypass": every USD-denominated gate — the 20%
campaign floor and TinyFish's US$0.20 minimum — is inert for a provider billed in
credits, which is all of them until an operator supplies a priced catalog.

No network, no credential, no provider. Every figure is a synthetic catalog
entry built in the test.
"""
from __future__ import annotations

import importlib
import importlib.util
from decimal import Decimal

import pytest

from conftest import SCRIPTS

LEDGER_MODULES = ("research_usage_ledger",)
USD_MICROS = 1_000_000

CREDIT_PRICED = {
    "provider": "serper", "capability": "search", "unit_micros": 0,
    "quota_unit": "credits", "quota_per_call": 1, "source": "qa-gate",
    "version": "credit-only-1", "observed_at": "2026-10-17T00:00:00+00:00",
    "verified": True,
}
#: A money-priced capability on the *same* provider as the credit case, so the
#: two tests differ only in the meter. Deliberately not a T3 capability: T3 is
#: closed without a grant, which is a different gate and would make this file's
#: floor assertions pass or fail for the wrong reason.
MONEY_PRICED = {
    "provider": "tinyfish", "capability": "search", "unit_micros": 250_000,
    "quota_unit": "searches", "quota_per_call": 1, "source": "qa-gate",
    "version": "usd-1", "observed_at": "2026-10-17T00:00:00+00:00",
    "verified": True,
}


def _ledger_module():
    for name in LEDGER_MODULES:
        if importlib.util.find_spec(name) is not None:
            return importlib.import_module(name)
    pytest.fail(
        f"no usage ledger ships with this candidate. Searched "
        f"importlib.util.find_spec over sys.path[0]={SCRIPTS}; none of "
        f"{list(LEDGER_MODULES)} resolve.")


@pytest.fixture()
def ledger_api(data_root):
    module = _ledger_module()
    catalog = module.PricingCatalog.from_entries([CREDIT_PRICED, MONEY_PRICED])

    def build(campaign=None):
        path = data_root / "state" / "dual-search-ledger.sqlite3"
        path.parent.mkdir(parents=True, exist_ok=True)
        kwargs = {"pricing": catalog}
        if campaign is not None:
            kwargs["campaign_budgets"] = campaign
        return module.UsageLedger(path, **kwargs)

    def request(index, *, provider="serper", capability="search", units=1):
        return module.OperationRequest(
            project="proj", run_id="runA", operation_id=f"op{index}",
            provider=provider, capability=capability, account_ref="acct-local-1",
            transport="rest", intent=f"intent-{index}",
            args={"q": f"query-{index}"}, units=units)

    return module, build, request


def _credit_campaign(module, *, units=500):
    """B0 of 500 credits on a credit meter; floor is 20% = 100 credits."""
    baseline = module.CampaignBaseline(
        provider="serper", account_ref="acct-local-1", meter="credits", units=units,
        observed_at="2026-10-17T00:00:00+00:00",
        source=module.MeterReadingSource.VENDOR_ACCOUNT_READ, campaign_id="qa-campaign")
    return {("serper", "acct-local-1"): module.CampaignBudget(module.CampaignFloor(baseline))}


# --- the bypass ------------------------------------------------------------


def test_a_credit_priced_provider_is_still_bounded_by_its_campaign_floor(ledger_api):
    """§1.5: the floor applies to the finite *period quota* as well.

    B0 is 500 credits and the floor is 100, so at most 400 may be drawn. The
    ledger's own credit branch (`budget.admit(spend_units=quota_units, ...)`)
    computes exactly this — it is simply never reached for a zero-micro price.
    A run that spends past the floor here is spending below the reserve the
    owner set, which is the failure clause §1.2 forbids.
    """
    module, build, request = ledger_api
    ledger = build(_credit_campaign(module))
    ledger.set_cap("run", "proj/runA", ceiling_calls=10_000, source="qa-gate")
    ledger.set_cap("project", "proj", ceiling_calls=10_000, source="qa-gate")

    granted = 0
    refusal = None
    for index in range(600):
        try:
            ledger.reserve(request(index))
            granted += 1
        except Exception as exc:  # noqa: BLE001 - the code is what is under test
            refusal = (type(exc).__name__, str(exc))
            break

    assert refusal is not None, (
        f"a credit-priced provider drew {granted} credits past a 100-credit floor "
        "without a refusal: the campaign floor is not evaluated when the price is "
        "zero micro-USD (§1.2/§1.5 'and separately on applicable finite period "
        "quota')")
    assert granted <= 400, (
        f"the floor is 100 credits of a 500-credit B0, so at most 400 may be "
        f"drawn; {granted} were granted before the refusal")


def test_an_account_with_no_captured_floor_refuses_a_credit_priced_call(ledger_api):
    """§1.6, first of the four fail-closed ways: nothing was verified.

    `_check_campaign_floor` already refuses this case for a money meter
    (``CAMPAIGN_NOT_CAPTURED``). With a credit price the same unverified account
    must not slip through simply because the money leg is zero.
    """
    module, build, request = ledger_api
    # Campaign enforcement is on (the mapping is non-empty) but this account has
    # no captured B0. An account the ledger never checked must not be admitted.
    other = _credit_campaign(module)
    other[("serpapi", "acct-other")] = other.pop(("serper", "acct-local-1"))
    ledger = build(other)
    ledger.set_cap("run", "proj/runA", ceiling_calls=10_000, source="qa-gate")
    ledger.set_cap("project", "proj", ceiling_calls=10_000, source="qa-gate")
    with pytest.raises(Exception) as refused:  # noqa: BLE001
        ledger.reserve(request(0))
    assert "CAMPAIGN" in str(refused.value).upper(), (
        "an account with no captured B0 was admitted for a credit-priced "
        f"chargeable call: {refused.value}")


def test_a_money_ceiling_alone_does_not_bound_a_credit_priced_provider(ledger_api):
    """The observable proof that a USD-denominated gate is inert here.

    This is written as a *characterisation* of the current behaviour rather than
    as a contract, so that the two tests above are not confused with it: a
    micro-USD ceiling genuinely cannot bound a price of zero. The point is that
    nothing else refuses either — so an operator who set only a spend ceiling has
    no bound at all in this deployment.
    """
    module, build, request = ledger_api
    ledger = build(None)
    ledger.set_cap("run", "proj/runA", ceiling_calls=10_000, source="qa-gate")
    ledger.set_cap("project", "proj", ceiling_micros=100, source="qa-gate")

    granted = 0
    for index in range(30):
        try:
            ledger.reserve(request(index))
            granted += 1
        except Exception:  # noqa: BLE001
            break

    report = ledger.report("proj")
    assert report["money"]["reserved_micros"] == 0, report["money"]
    assert report["quota_units"].get("credits") == granted, report["quota_units"]
    assert granted == 30, (
        "expected the micro-USD ceiling to be inert for a zero-micro price; if "
        "this now refuses, the ceiling gained a quota dimension and this "
        "characterisation should be deleted, not relaxed")


# --- the control: the guard is the only thing in the way ------------------


def _credit_campaign_with_priced_capability(module, *, unit_micros, units=500):
    """Identical fixture to the bypass test, differing only in ``unit_micros``.

    ``unit_micros=1`` makes ``estimated_micros`` non-zero, so ``reserve``'s guard
    reaches ``_check_campaign_floor`` — which then takes its *credit* branch,
    because the budget's meter is credits and that branch does not look at money.
    Nothing else about the scenario changes. If the floor binds here and not at
    ``unit_micros=0``, the guard is the sole cause of the bypass, and no other
    part of the fixture can be blamed for it.
    """
    catalog = module.PricingCatalog.from_entries([
        dict(CREDIT_PRICED, unit_micros=unit_micros),
    ])
    baseline = module.CampaignBaseline(
        provider="serper", account_ref="acct-local-1", meter="credits", units=units,
        observed_at="2026-10-17T00:00:00+00:00",
        source=module.MeterReadingSource.VENDOR_ACCOUNT_READ, campaign_id="qa-campaign")
    campaign = {("serper", "acct-local-1"):
                module.CampaignBudget(module.CampaignFloor(baseline))}
    return module, catalog, campaign


def test_the_floor_binds_when_the_same_capability_carries_a_nonzero_micro_price(data_root):
    """Control for the bypass test: same account, same floor, same 600 attempts.

    A pass here (the floor refusing at 400 credits) is what attributes the
    failure above to the ``estimated_micros > 0`` guard rather than to the
    campaign setup, the caps, or the loop.
    """
    module = _ledger_module()
    _module, catalog, campaign = _credit_campaign_with_priced_capability(
        module, unit_micros=1)
    path = data_root / "state" / "dual-search-ledger.sqlite3"
    path.parent.mkdir(parents=True, exist_ok=True)
    ledger = module.UsageLedger(path, pricing=catalog, campaign_budgets=campaign)
    ledger.set_cap("run", "proj/runA", ceiling_calls=10_000, source="qa-gate")
    ledger.set_cap("project", "proj", ceiling_calls=10_000, source="qa-gate")

    granted = 0
    for index in range(600):
        try:
            ledger.reserve(module.OperationRequest(
                project="proj", run_id="runA", operation_id=f"op{index}",
                provider="serper", capability="search", account_ref="acct-local-1",
                transport="rest", intent=f"intent-{index}",
                args={"q": f"query-{index}"}))
            granted += 1
        except Exception:  # noqa: BLE001
            break
    assert granted == 400, (
        f"with a non-zero micro price the floor should have stopped the run at 400 "
        f"credits; it stopped at {granted}. If this is 600 the control is not "
        "controlling anything and the bypass test proves nothing.")


# --- the anchor: the money-metered floor does bind -------------------------


def test_the_tinyfish_minimum_still_binds_on_a_money_meter(ledger_api):
    """§1.4, as the positive anchor: this path works and must keep working.

    A regression that "fixed" the credit case by weakening the money case would
    show up here.
    """
    module, build, request = ledger_api
    baseline = module.CampaignBaseline(
        provider="tinyfish", account_ref="acct-local-1", meter=module.USD_MICROS,
        units=int(Decimal("0.50") * module.MICRO),
        observed_at="2026-10-17T00:00:00+00:00",
        source=module.MeterReadingSource.VENDOR_ACCOUNT_READ, campaign_id="qa-campaign")
    floor = module.CampaignFloor(baseline)
    assert floor.floor_units == 200_000, floor.floor_units      # US$0.20

    ledger = build({("tinyfish", "acct-local-1"): module.CampaignBudget(floor)})
    ledger.set_cap("run", "proj/runA", ceiling_calls=10_000, source="qa-gate")
    ledger.set_cap("project", "proj", ceiling_calls=10_000, source="qa-gate")

    # one $0.25 step is fine; the second would leave $0.00 < floor
    ledger.reserve(request(0, provider="tinyfish", capability="search"))
    with pytest.raises(Exception) as refused:  # noqa: BLE001
        ledger.reserve(request(1, provider="tinyfish", capability="search"))
    assert "FLOOR" in str(refused.value).upper() or "CAMPAIGN" in str(refused.value).upper(), \
        refused.value


# --- fail-closed before the network ----------------------------------------


def test_an_unknown_capability_is_refused_rather_than_priced_at_zero(ledger_api):
    """§1.6: an unpriced capability is not a free one.

    Absent from the catalog means the price is unknown; the operation must not
    be admitted on the assumption that unknown == 0.
    """
    module, build, request = ledger_api
    ledger = build(None)
    ledger.set_cap("run", "proj/runA", ceiling_calls=10_000, source="qa-gate")
    ledger.set_cap("project", "proj", ceiling_calls=10_000, source="qa-gate")
    with pytest.raises(Exception) as refused:  # noqa: BLE001
        ledger.reserve(request(0, provider="serper", capability="not-in-catalog"))
    assert "PRICE" in str(refused.value).upper() or "CAPABILITY" in str(refused.value).upper() \
        or "NOT" in str(refused.value).upper(), refused.value


def test_a_duplicate_does_not_reserve_quota_twice(ledger_api):
    """§1.6/§P1-03: a refused duplicate must not consume a second credit."""
    module, build, request = ledger_api
    ledger = build(None)
    ledger.set_cap("run", "proj/runA", ceiling_calls=10_000, source="qa-gate")
    ledger.set_cap("project", "proj", ceiling_calls=10_000, source="qa-gate")

    ledger.reserve(request(0))
    before = ledger.report("proj")["quota_units"]
    with pytest.raises(Exception):  # noqa: BLE001
        ledger.reserve(request(0))
    after = ledger.report("proj")["quota_units"]
    assert after == before, (before, after)


def test_a_refused_reservation_never_reaches_the_provider(clean_env, monkeypatch):
    """§1.6 + the adapter contract: the refusal happens **before the network**.

    Asserted where it is observable: the search adapter's request function is
    replaced by a recorder, and a reservation the ledger refuses must leave that
    recorder untouched.
    """
    import dual_search_mcp as dsm

    _ledger_module()
    calls = []
    monkeypatch.setattr(dsm, "_request", lambda *a, **k: calls.append(a) or {"organic": []})
    monkeypatch.setenv("MARKETOPS_RESEARCH_DATA_ROOT", str(clean_env and "") or "")
    monkeypatch.setenv("MARKETOPS_SEARCH_ALLOW_NETWORK", "1")
    monkeypatch.setenv("MARKETOPS_SEARCH_MAX_CALLS", "0")   # no approved budget
    monkeypatch.setenv("SERPER_API_KEY", "qa-gate-synthetic-not-a-credential")

    import research_lite_store as store
    import tempfile
    from pathlib import Path

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp) / "data"
        (root / "state").mkdir(parents=True)
        store.start_run(root, "runA", "pool", "qa gate synthetic", "v-qa")
        with pytest.raises(ValueError):
            dsm.search("serper", "synthetic query", "GB", "en",
                       run_id="runA", operation_id="op-1", mode="web", limit=5)
    assert calls == [], "a refused reservation still reached the provider request"
