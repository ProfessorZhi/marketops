"""The Coordinator's CD-01..CD-06 decisions, as paired executable assertions.

Source: ``docs/wave1-coordination/CONTRACT-DECISION-DEDUP-IDENTITY.md``
(branch ``docs/wave1-coordination-20261017``), whose judgement object is exact
code ``d88b176``.

The Coordinator settled the round-7 dispute as **three separate facts**, and each
one needs its own test or the set stops being able to tell them apart:

* **CD-01** — identity is ``provider + account_ref + capability + canonical
  params``, transport-independent, project-scoped (not run-scoped).
* **CD-02** — the old single test was wrong (it fed two *different* parameter
  sets and demanded a collision). It must be a **pair**: same operation across
  transports is refused, and an unrelated query on the same provider is allowed.
  Only one of the pair would hide a different failure.
* **CD-03** — the product has a real gap: no shared normalization layer, so
  ``IL-2`` is unreachable end-to-end regardless of how the test is written.

CD-04/CD-05 are *pinning* tests: the semantics they cover are already correct and
the point is to fix them so a later "fix the dedup" cannot quietly change them.

Every test uses a temporary SQLite file, a synthetic catalog and no provider.
"""
from __future__ import annotations

import importlib
import importlib.util

import pytest

from conftest import SCRIPTS

LEDGER_MODULES = ("research_usage_ledger",)
PROJECT = "proj"


def _ledger_module():
    for name in LEDGER_MODULES:
        if importlib.util.find_spec(name) is not None:
            return importlib.import_module(name)
    pytest.fail(
        f"no usage ledger ships with this candidate. Searched "
        f"importlib.util.find_spec over sys.path[0]={SCRIPTS}; none of "
        f"{list(LEDGER_MODULES)} resolve.")


def _catalog(module):
    return module.PricingCatalog.from_entries([{
        "provider": "serpapi", "capability": "search", "unit_micros": 0,
        "quota_unit": "searches", "quota_per_call": 1, "source": "qa-gate",
        "version": "v1", "observed_at": "2026-10-17T00:00:00+00:00", "verified": True,
    }, {
        "provider": "serper", "capability": "search", "unit_micros": 0,
        "quota_unit": "credits", "quota_per_call": 1, "source": "qa-gate",
        "version": "v1", "observed_at": "2026-10-17T00:00:00+00:00", "verified": True,
    }])


def _ledger(module, data_root):
    path = data_root / "state" / "dual-search-ledger.sqlite3"
    path.parent.mkdir(parents=True, exist_ok=True)
    ledger = module.UsageLedger(path, pricing=_catalog(module))
    for kind, key in (("run", f"{PROJECT}/runA"), ("run", f"{PROJECT}/runB"),
                      ("project", PROJECT)):
        ledger.set_cap(kind, key, ceiling_calls=1_000, source="qa-gate")
    return ledger


def _request(module, *, operation_id, transport="REST_HTTPS", args=None,
             run_id="runA", provider="serpapi", capability="search"):
    return module.OperationRequest(
        project=PROJECT, run_id=run_id, operation_id=operation_id,
        provider=provider, capability=capability, account_ref=provider,
        transport=transport, intent="intent-1", args=args or {"query": "pool london"})


def _refusal_code(exc):
    code = getattr(exc, "code", None) or getattr(exc, "reason_code", None)
    return code if isinstance(code, str) and code else str(exc).split(":", 1)[0]


# --- CD-01: identity is transport-independent and project-scoped -----------


def test_cd01_the_same_operation_over_two_transports_is_one_route_claim(data_root, clean_env):
    """Transport is how the call travels; it is not what was bought."""
    module = _ledger_module()
    ledger = _ledger(module, data_root)
    ledger.reserve(_request(module, operation_id="rest-1", transport="REST_HTTPS"))
    with pytest.raises(Exception) as refused:  # noqa: BLE001
        ledger.reserve(_request(module, operation_id="mcp-1",
                                transport="EMBEDDED_OFFICIAL_MCP_HTTP"))
    assert _refusal_code(refused.value) == "DUPLICATE_OPERATION_SIGNATURE", refused.value


def test_cd01_the_route_claim_is_project_scoped_not_run_scoped(data_root, clean_env):
    """CD-01: ``route_shape`` answers "has this project bought this?", so a
    *different run* repeating the same operation is the same route claim.

    ``op_signature`` is the run-scoped key and stays separate — two keys, two
    questions. Dropping the route claim's project scope would let a new run start
    with a fresh allowance for work the project already bought.
    """
    module = _ledger_module()
    ledger = _ledger(module, data_root)
    ledger.reserve(_request(module, operation_id="runA-1", run_id="runA",
                            transport="REST_HTTPS"))
    with pytest.raises(Exception) as refused:  # noqa: BLE001
        ledger.reserve(_request(module, operation_id="runB-1", run_id="runB",
                                transport="EMBEDDED_OFFICIAL_MCP_HTTP"))
    assert _refusal_code(refused.value) == "DUPLICATE_OPERATION_SIGNATURE", refused.value


# --- CD-02a / CD-02b: the pair --------------------------------------------


def test_cd02a_the_same_normalized_operation_across_the_two_adapters_is_refused(data_root, clean_env):
    """CD-02a — **expected RED at `d88b176`** because of the CD-03 gap.

    The two real adapters are asked to buy the same normalized SerpApi query over
    their two transports. CD-01 says that is one route claim.

    It is not, today: ``dual_search_mcp`` hands the ledger
    ``{"query": <canonical digest>}`` and ``research_provider_extensions`` hands
    ``{"action": ..., "signature": ...}``. ``route_shape`` digests ``args``, so the
    two can never be equal. That is the CD-03 normalization gap, and this test is
    where it becomes observable through the adapters rather than through a
    hand-built request.

    Do not make this green by weakening CD-01 (provider-only identity): CD-02b
    below is the control that would then fail.
    """
    import search_canonical as sc

    import dual_search_mcp as dsm
    import research_lite_store as store
    import research_provider_extensions as ext

    for key, value in {
        "MARKETOPS_RESEARCH_DATA_ROOT": str(data_root),
        "MARKETOPS_SEARCH_ALLOW_NETWORK": "1",
        "MARKETOPS_SEARCH_MAX_CALLS": "9",
        "MARKETOPS_RESEARCH_ALLOW_NETWORK": "1",
        "MARKETOPS_RESEARCH_MAX_EXTERNAL_CALLS": "9",
        "SERPER_API_KEY": "qa-gate-synthetic-not-a-credential",
        "SERPAPI_API_KEY": "qa-gate-synthetic-not-a-credential",
    }.items():
        clean_env.setenv(key, value)

    store.start_run(data_root, "runA", "pool", "qa gate synthetic", "v-qa")
    canonical = sc.operation_signature(sc.canonical_arguments(
        provider="serpapi", mode="web", query="pool london", country="GB",
        language="en", limit=5))

    dsm._reserve(data_root, run_id="runA", operation_id="rest-1",
                 fingerprint=canonical, provider="serpapi", limit=9)
    try:
        ext._reserve(data_root, run_id="runA", operation_id="mcp-1",
                     action="serpapi_mcp_search", signature=canonical)
    except ValueError:
        return  # the same claim was refused: CD-01 holds end-to-end

    module = _ledger_module()
    rows = module.for_root(data_root).operations(project=None, run_id=None)
    assert len(rows) == 1, (
        f"the same normalized query reserved {len(rows)} rows over two transports "
        f"(transports={[row.transport for row in rows]}) — CD-03 normalization gap")


def test_cd02b_an_unrelated_query_on_the_same_provider_is_allowed(data_root, clean_env):
    """CD-02b — the control that keeps CD-02a from being "solved" by over-blocking.

    A route claim keyed on the provider alone would refuse this. It is the exact
    degeneration CD-01 and the ``route_shape`` docstring forbid.
    """
    module = _ledger_module()
    ledger = _ledger(module, data_root)
    first = ledger.reserve(_request(module, operation_id="op-1",
                                    args={"query": "pool london"}))
    second = ledger.reserve(_request(module, operation_id="op-2",
                                     args={"query": "an unrelated query"}))
    assert first.operation_id != second.operation_id
    assert len(ledger.operations(project=None, run_id=None)) == 2


# --- CD-04: which states hold a route claim -------------------------------


def test_cd04_an_unknown_charge_still_holds_the_route_claim(data_root, clean_env):
    """A dispatched call whose charge is unknown is *exposure*, not a free retry."""
    module = _ledger_module()
    ledger = _ledger(module, data_root)
    first = ledger.reserve(_request(module, operation_id="rest-1"))
    ledger.mark_dispatch_intent(first.operation_id)
    ledger.mark_unknown(first.operation_id, reason="AMBIGUOUS_RESPONSE")

    with pytest.raises(Exception) as refused:  # noqa: BLE001
        ledger.reserve(_request(module, operation_id="mcp-1",
                                transport="EMBEDDED_OFFICIAL_MCP_HTTP"))
    assert _refusal_code(refused.value) == "DUPLICATE_OPERATION_SIGNATURE", refused.value


def test_cd04_a_proven_no_charge_releases_the_route_claim(data_root, clean_env):
    """``NO_CHARGE_PROVEN`` means "provably not billed", so it is not a purchase.

    The second request uses a **different run**. Within one run the *run-scoped*
    ``op_signature`` would refuse it first — a different key answering a different
    question (CD-01: two keys, two questions) — and the route claim under test
    would never be consulted.
    """
    module = _ledger_module()
    ledger = _ledger(module, data_root)
    first = ledger.reserve(_request(module, operation_id="runA-1", run_id="runA"))
    ledger.prove_no_charge(first.operation_id, reason="PRE_DISPATCH_REFUSAL",
                           evidence_ref="qa-evidence-1")

    second = ledger.reserve(_request(module, operation_id="runB-1", run_id="runB",
                                     transport="EMBEDDED_OFFICIAL_MCP_HTTP"))
    assert second.operation_id.endswith("runB-1")


def test_cd04_renaming_the_operation_id_cannot_replay_within_a_run(data_root, clean_env):
    module = _ledger_module()
    ledger = _ledger(module, data_root)
    ledger.reserve(_request(module, operation_id="op-1", transport="REST_HTTPS"))
    with pytest.raises(Exception) as refused:  # noqa: BLE001
        ledger.reserve(_request(module, operation_id="op-2", transport="REST_HTTPS"))
    assert _refusal_code(refused.value) in {"DUPLICATE_OPERATION_SIGNATURE",
                                            "OPERATION_ID_COLLISION"}, refused.value


# --- CD-05: migration compatibility ---------------------------------------


def test_cd05_a_legacy_row_does_not_block_a_new_request(data_root, clean_env):
    """History is not policy: an imported attempt must not refuse today's call.

    The imported row carries no ``route_shape`` (the column default is ``''``),
    and ``''`` matches no real shape. That is the conservative direction — "we
    never recorded this" is not a claim.
    """
    module = _ledger_module()
    ledger = _ledger(module, data_root)
    ledger.import_legacy_record(
        legacy_ref="legacy-attempt-1", project=PROJECT, run_id="runA",
        provider="serpapi", capability="search", transport="rest",
        created_at="2026-10-01T00:00:00+00:00")

    imported = [row for row in ledger.operations(project=None, run_id=None)
                if row.origin == "legacy"]
    assert imported, "the legacy import did not land"
    assert imported[0].route_shape == "", (
        "an imported row carries a real route shape, so it could refuse a new "
        "call: " + repr(imported[0].route_shape))

    new = ledger.reserve(_request(module, operation_id="fresh-1", provider="serpapi"))
    assert new.operation_id.endswith("fresh-1")
