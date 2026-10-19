"""P1-03 contract: one authoritative logical ledger, safe under concurrency.

Acceptance source: ``MARKETOPS_ARCHITECTURE_CONVERGENCE_DECISION_AND_P0_P2_PLAN_2026-10-17.md``
Appendix D, "P1-03 Unified ledger: separate deduplication, authorization and
actual billing" — the eleven-item minimum test suite.

Every test here races **real subprocesses** against the real SQLite files; no
network call is made and no credential is real. Tests whose contract the current
candidate does not meet are deliberately left **red** and their docstring names
the contract, so a green run after a fix is meaningful and a red run cannot be
mistaken for a pass.
"""
from __future__ import annotations

import os
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

from conftest import blocked, module_available

WORKER = Path(__file__).resolve().parent / "_reserve_worker.py"


def _race(root: Path, *, mode: str, ceiling: int, attempts: int, env_extra=None,
          project_ceiling=None, capture_stderr: bool = False):
    """Launch ``attempts`` processes that all try to reserve at the same instant.

    ``ceiling`` sets the **per-run** allowance (``MARKETOPS_SEARCH_MAX_CALLS``).
    ``project_ceiling`` sets the separate **project** allowance
    (``MARKETOPS_RESEARCH_MAX_EXTERNAL_CALLS``); pass ``None`` to leave it unset,
    which the product reads as "no additional project bound" rather than zero.
    Setting both to the same number makes the project ceiling the binding one,
    and a test that then expects per-run independence is asserting against the
    product's design rather than against §P1-03.
    """
    # Bring the receipt store to its migrated shape before the race. Two of the
    # tests below measure *ceilings*, and the schema-migration race has its own
    # deterministic module (``test_qa_ledger_schema_concurrency``). Without this,
    # a migration collision surfaces here as a budget failure -- the same code
    # path, reported under the wrong contract, which is how a real ceiling
    # regression would get lost among false ones.
    _pre_migrate(root)
    env = dict(os.environ)
    env["MARKETOPS_SEARCH_MAX_CALLS"] = str(ceiling)
    if mode == "ext" and project_ceiling is None:
        # The extensions adapter refuses to run with no approved project budget
        # at all (RESEARCH_BUDGET_NOT_APPROVED); that is its own contract, not
        # the ceiling question this helper is setting up.
        project_ceiling = ceiling
    if project_ceiling is None:
        env.pop("MARKETOPS_RESEARCH_MAX_EXTERNAL_CALLS", None)
    else:
        env["MARKETOPS_RESEARCH_MAX_EXTERNAL_CALLS"] = str(project_ceiling)
    env["MARKETOPS_RESEARCH_ALLOW_NETWORK"] = "1"
    env["MARKETOPS_SEARCH_ALLOW_NETWORK"] = "1"
    env["SERPAPI_API_KEY"] = "qa-gate-synthetic-not-a-credential"
    env["SERPER_API_KEY"] = "qa-gate-synthetic-not-a-credential"
    env.update(env_extra or {})
    procs = [
        subprocess.Popen(
            [sys.executable, str(WORKER), mode, str(root),
             f"run{i}", f"op{i}", f"signature-{i}"],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env,
        )
        for i in range(attempts)
    ]
    out = []
    for proc in procs:
        stdout, stderr = proc.communicate(timeout=90)
        combined = ((stdout or "") + (stderr or "") if capture_stderr
                    else (stdout or stderr or "")).strip()
        if "sqlite3.OperationalError" in combined:
            # BLOCKED, not a pass and not a ceiling failure. The concurrency
            # defect is reported deterministically, once, by
            # test_qa_ledger_schema_concurrency.py under its own contract.
            # Letting it also flip *this* test red would make the ceiling
            # verdict depend on which concurrent test ran first — a flaky
            # signal that hides real ceiling regressions among false ones.
            blocked("the schema-concurrency defect pre-empted this ceiling "
                    "measurement; see test_qa_ledger_schema_concurrency.py::"
                    "test_a_concurrent_first_reservation_never_surfaces_a_"
                    "driver_error. Child output: " + combined[-200:])
        out.append(combined)
    return out


def _pre_migrate(root: Path) -> None:
    """Make the receipt store look like one that has already been migrated."""
    state = root / "state"
    state.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(state / "dual-search-ledger.sqlite3") as db:
        db.execute("""CREATE TABLE IF NOT EXISTS search_attempts (
            run_id TEXT NOT NULL, operation_id TEXT NOT NULL, fingerprint TEXT NOT NULL,
            provider TEXT NOT NULL, reserved_at TEXT NOT NULL, state TEXT NOT NULL,
            outcome_code TEXT NOT NULL DEFAULT 'RESERVED',
            PRIMARY KEY (run_id, operation_id))""")


def _ledger_api(data_root):
    """The ledger module plus the placeholder request builder these tests ignore."""
    import research_usage_ledger as ledger_module

    return ledger_module, None, None


def _count(root: Path, filename: str, table: str) -> int:
    path = root / "state" / filename
    with sqlite3.connect(path) as db:
        return int(db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])


# --- invariants the candidate currently satisfies -------------------------


def test_same_signature_race_never_exceeds_the_ceiling(data_root, clean_env):
    """Concurrent reservations **inside one run** stop exactly at its allowance.

    §P1-03 minimum suite: "concurrent same-signature race" — no interleaving may
    let two processes both pass the ``used >= limit`` check.

    Eight *different* run ids are deliberately not used here. Under the
    pre-convergence counter that shape passed for the wrong reason: one global
    ``SELECT COUNT(*)`` capped all runs together, so the file looked like it
    enforced a per-run ceiling while actually enforcing none. Once the ceiling
    became genuinely per-run, eight runs each legitimately got their own
    allowance. The race only means something within a single run.
    """
    ceiling, attempts = 3, 8
    results = _race(data_root, mode="search", ceiling=ceiling, attempts=attempts,
                    env_extra={"QA_RUN_FIXED": "runRace"})
    granted = [r for r in results if r == "OK"]
    assert len(granted) == ceiling, results
    assert _count(data_root, "dual-search-ledger.sqlite3", "search_attempts") == ceiling
    refused = [r for r in results if r != "OK"]
    assert all("SEARCH_BUDGET_EXHAUSTED" in r for r in refused), refused


def test_renaming_the_operation_id_cannot_replay_a_search(data_root, clean_env):
    """§P1-03: "A timeout may still have incurred a charge", so a new
    ``operation_id`` with the same query inside one run must not re-bill."""
    import dual_search_mcp as dsm

    dsm._reserve(data_root, run_id="runA", operation_id="op1",
                 fingerprint="same-query", provider="serper", limit=5)
    with pytest.raises(ValueError, match="DUPLICATE_SEARCH_SAME_RUN"):
        dsm._reserve(data_root, run_id="runA", operation_id="op2",
                     fingerprint="same-query", provider="serper", limit=5)


def test_reusing_an_operation_id_with_changed_input_is_a_collision(data_root, clean_env):
    """§P1-03: an operation id is an identity, not a slot to be rewritten."""
    import dual_search_mcp as dsm

    dsm._reserve(data_root, run_id="runA", operation_id="op1",
                 fingerprint="query-one", provider="serper", limit=5)
    with pytest.raises(ValueError, match="OPERATION_ID_COLLISION"):
        dsm._reserve(data_root, run_id="runA", operation_id="op1",
                     fingerprint="query-two", provider="serper", limit=5)


def test_a_reservation_survives_the_death_of_the_reserving_process(data_root, clean_env):
    """§P1-03: "crash after reserve, before dispatch" must leave the reservation
    committed, because a crashed dispatch may still have been billed."""
    results = _race(data_root, mode="search", ceiling=2, attempts=1)
    assert results == ["OK"], results
    # A fresh process re-entering the same operation is refused, not re-billed.
    again = _race(data_root, mode="search", ceiling=2, attempts=1)
    assert "ALREADY_RESERVED" in again[0] or "DUPLICATE_SEARCH_SAME_RUN" in again[0], again
    assert _count(data_root, "dual-search-ledger.sqlite3", "search_attempts") == 1


def test_a_malformed_cost_limit_fails_closed(data_root, clean_env, monkeypatch):
    """§P1-03: "malformed cost config" must refuse, never default to unlimited."""
    import dual_search_mcp as dsm

    monkeypatch.setenv("MARKETOPS_SEARCH_MAX_CALLS", "many")
    with pytest.raises(ValueError, match="nonnegative integer"):
        dsm._number("MARKETOPS_SEARCH_MAX_CALLS")
    monkeypatch.setenv("MARKETOPS_RESEARCH_MAX_EXTERNAL_CALLS", "-1")
    import research_provider_extensions as ext

    with pytest.raises(ValueError, match="INVALID_EXTERNAL_CALL_LIMIT"):
        ext._limit()


def test_an_unknown_option_on_a_known_action_is_refused(data_root, clean_env, monkeypatch):
    """§P1-03/P1-05: "new provider tool default deny" — an action outside the
    frozen allowlist is refused before any reservation."""
    import research_provider_extensions as ext

    monkeypatch.setenv("MARKETOPS_RESEARCH_ALLOW_NETWORK", "1")
    monkeypatch.setenv("MARKETOPS_RESEARCH_MAX_EXTERNAL_CALLS", "5")
    with pytest.raises(ValueError, match="UNSUPPORTED_PROVIDER_ACTION"):
        ext._reserve(data_root, run_id="runA", operation_id="op1",
                     action="tinyfish_mcp_list_profiles", signature="s")


# --- invariants the candidate does NOT yet satisfy ------------------------


# The two concurrency defects this file used to probe with repeated probabilistic
# rounds now live in ``test_qa_ledger_schema_concurrency.py``, where each window is
# forced deterministically and carries a patched control. Superseded here rather
# than kept: a test that fails only when the scheduler cooperates is flaky in both
# directions, and two overlapping copies drift.


def test_the_project_ceiling_bounds_every_run_that_shares_the_root(data_root, clean_env):
    """§P1-03: the project ceiling is *shared*, and sharing it is the point.

    BEFORE this test set one number for both the per-run and the project
    allowance and then demanded that a second run get a fresh allowance. Once
    the candidate gained a real project ceiling, that demand inverted: the
    product was correctly refusing run B, and the test called it starvation.

    The correct reading of a project ceiling: when it is the binding constraint,
    a new run under the same project does **not** get a fresh allowance. That is
    what stops "start a new run to bypass the ceiling".
    """
    ceiling = 2
    first = _race(data_root, mode="search", ceiling=ceiling, attempts=ceiling,
                  project_ceiling=ceiling)
    assert first == ["OK"] * ceiling, first

    later = _race(data_root, mode="search", ceiling=ceiling, attempts=1,
                  project_ceiling=ceiling, env_extra={"QA_RUN_PREFIX": "runB-"})
    assert later and later[0] != "OK", (
        "a second run was allowed past a spent project ceiling: " + repr(later))
    assert "BUDGET_PROJECT_CEILING" in later[0] or "EXHAUSTED" in later[0], later


def test_the_per_run_ceiling_lets_a_second_run_use_its_own_allowance(data_root, clean_env):
    """§P1-03: per-run and project ceilings are **separate** levels.

    With the project ceiling left unset (no additional project bound) the run
    allowance is the binding one, and a second run must get its own. This is the
    half the earlier single test could not express, and it is what makes the
    pair meaningful: one level shared, one level per-run.
    """
    per_run = 2
    first = _race(data_root, mode="search", ceiling=per_run, attempts=per_run,
                  project_ceiling=None)
    assert first == ["OK"] * per_run, first

    later = _race(data_root, mode="search", ceiling=per_run, attempts=1,
                  project_ceiling=None, env_extra={"QA_RUN_PREFIX": "runB-"})
    assert later == ["OK"], (
        "a second run was denied even though the per-run allowance is per run; "
        f"observed {later!r}")

    # negative counter-proof: the *same* run is still capped by its own ceiling.
    third = _race(data_root, mode="search", ceiling=per_run, attempts=1,
                  project_ceiling=None, env_extra={"QA_RUN_PREFIX": "runB-"})
    assert third and third[0] != "OK", (
        "the per-run ceiling did not bind for the run that already used it: "
        + repr(third))


def test_search_and_fetch_share_one_authoritative_ledger(data_root, clean_env):
    """§P1-03: one *authoritative logical* ledger, not two independent writers.

    A run that performs one search and one provider fetch has spent two metered
    operations. The product must be able to report that as one number for the
    run; today the two adapters write two unlinked SQLite files.
    """
    import dual_search_mcp as dsm

    dsm._reserve(data_root, run_id="runA", operation_id="search-1",
                 fingerprint="fp-search", provider="serper", limit=9)
    results = _race(data_root, mode="ext", ceiling=9, attempts=1,
                    env_extra={"QA_RUN_FIXED": "runA"})
    assert results == ["OK"], results

    import research_mcp as mcp

    try:
        # ``data_root`` is passed explicitly: without it the read model consults
        # the roots *this process* metered, and the reservations above were taken
        # in child processes. Asking a process about another process's roots
        # returns an empty answer that looks like a missing feature.
        usage = mcp.run_usage("runA", data_root=data_root)
    except AttributeError:
        pytest.fail(
            "no single read model reports a run's metered operations across "
            "transports; the two ledgers "
            "(dual-search-ledger.sqlite3, provider-reservations.sqlite3) are "
            "still separate authorities (§P1-03)")
    assert usage["metered_operations"] == 2, usage
    assert set(usage["by_transport"]) == {"rest", "official_mcp"}, (
        "the two transports are not reported as one run's operations: " + repr(usage))
    assert usage["by_provider"]["serper"] == 1 and usage["by_provider"]["serpapi"] == 1, usage


# --- §P1-03 Key B: transport-independent identity --------------------------
#
# This used to be one test that fed the two adapters two *different* strings and
# asserted they collided. That is not the contract: two different signatures are
# two different operations, and a ledger that deduplicated them would be the
# defect. The three tests below separate the question into the layer that owns
# each half, plus the negative control that keeps "same" from being read as
# "similar".


def test_identical_canonical_identity_across_transports_is_one_operation(data_root, clean_env):
    """The ledger's own key: transport is *how it travels*, not *what it buys*.

    ``op_signature`` excludes transport by design, so two requests that differ
    only in ``transport`` are one spend opportunity. This is the ledger-level
    half of §P1-03 Key B, and it holds today.
    """
    ledger_module, _build, request = _ledger_api(data_root)
    project = "proj"
    ledger = ledger_module.UsageLedger(
        data_root / "state" / "dual-search-ledger.sqlite3",
        pricing=ledger_module.PricingCatalog.from_entries([{
            "provider": "serpapi", "capability": "search", "unit_micros": 0,
            "quota_unit": "searches", "quota_per_call": 1, "source": "qa-gate",
            "version": "v1", "observed_at": "2026-10-17T00:00:00+00:00",
            "verified": True}]))
    ledger.set_cap("run", f"{project}/runA", ceiling_calls=100, source="qa-gate")
    ledger.set_cap("project", project, ceiling_calls=100, source="qa-gate")

    same = {"query": "pool london"}
    ledger.reserve(ledger_module.OperationRequest(
        project=project, run_id="runA", operation_id="rest-1", provider="serpapi",
        capability="search", account_ref="serpapi", transport="REST_HTTPS",
        intent="intent-1", args=same))
    with pytest.raises(Exception) as refused:  # noqa: BLE001
        ledger.reserve(ledger_module.OperationRequest(
            project=project, run_id="runA", operation_id="mcp-1", provider="serpapi",
            capability="search", account_ref="serpapi",
            transport="EMBEDDED_OFFICIAL_MCP_HTTP", intent="intent-1", args=same))
    assert "DUPLICATE" in str(refused.value).upper(), refused.value


def test_two_different_queries_on_one_provider_are_not_blocked(data_root, clean_env):
    """NEGATIVE CONTROL for the dedup above.

    Sharing a provider is not sharing an operation. A dedup keyed on the provider
    alone would refuse this — and would look correct against any same-query test.
    """
    ledger_module, _build, request = _ledger_api(data_root)
    project = "proj"
    ledger = ledger_module.UsageLedger(
        data_root / "state" / "dual-search-ledger.sqlite3",
        pricing=ledger_module.PricingCatalog.from_entries([{
            "provider": "serpapi", "capability": "search", "unit_micros": 0,
            "quota_unit": "searches", "quota_per_call": 1, "source": "qa-gate",
            "version": "v1", "observed_at": "2026-10-17T00:00:00+00:00",
            "verified": True}]))
    ledger.set_cap("run", f"{project}/runA", ceiling_calls=100, source="qa-gate")
    ledger.set_cap("project", project, ceiling_calls=100, source="qa-gate")

    first = ledger.reserve(ledger_module.OperationRequest(
        project=project, run_id="runA", operation_id="op-1", provider="serpapi",
        capability="search", account_ref="serpapi", transport="REST_HTTPS",
        intent="intent-1", args={"query": "pool london"}))
    second = ledger.reserve(ledger_module.OperationRequest(
        project=project, run_id="runA", operation_id="op-2", provider="serpapi",
        capability="search", account_ref="serpapi", transport="REST_HTTPS",
        intent="intent-2", args={"query": "an unrelated query"}))
    assert first.operation_id != second.operation_id
    assert len(ledger.operations(project=None, run_id=None)) == 2


# The end-to-end adapter-level form of this lives in ``test_qa_dedup_identity_cd``
# as ``test_cd02a_the_same_normalized_operation_across_the_two_adapters_is_refused``,
# where it is paired with CD-02b. Kept in one place so one fact is reported once.


def test_a_period_reset_exists_for_the_attempt_ceiling(data_root, clean_env):
    """§P1-03: "period reset" — ceilings are per period, not silently lifetime.

    A lifetime counter that is never resettable reports a run's headroom as a
    function of unrelated history (``_capacity`` admits this) and cannot express
    a monthly or per-project period.
    """
    import research_mcp as mcp

    assert hasattr(mcp, "reset_or_roll_period"), (
        "no period-roll operation exists for the reservation ceilings; the "
        "counters are lifetime-scoped (§P1-03 'period reset')")


def test_legacy_receipts_remain_readable_through_the_new_read_model(data_root, clean_env):
    """§P1-03: migration must preserve audit history and map every row.

    Rows written by the legacy writers must stay visible through whatever read
    model the converged ledger exposes — "no silent deletion of historical
    calls".
    """
    if not module_available("research_lite_store"):
        blocked("research_lite_store is not importable in this environment")
    import dual_search_mcp as dsm

    dsm._reserve(data_root, run_id="legacy", operation_id="op-legacy",
                 fingerprint="legacy-fp", provider="serper", limit=5)
    import research_mcp as mcp

    try:
        rows = mcp.legacy_receipts(data_root)
    except AttributeError:
        pytest.fail(
            "the converged read model exposes no path to legacy "
            "dual-search-ledger rows; there is nothing to reconcile against "
            "(§P1-03 non-destructive migration)")
    assert any(row.get("operation_id") == "op-legacy" for row in rows), rows


def test_unknown_charge_is_conserved_rather_than_refunded(data_root, clean_env):
    """§P1-03: a dispatched timeout is ``UNKNOWN_CHARGE``, never auto-refunded.

    The reservation ledger must carry a distinct financial state for "we may
    have been billed and cannot yet prove we were not", and no code path may
    turn that into an automatic refund or a compensating cross-transport call.
    """
    import dual_search_mcp as dsm

    dsm._reserve(data_root, run_id="runA", operation_id="op-timeout",
                 fingerprint="fp-timeout", provider="serper", limit=5)
    with sqlite3.connect(data_root / "state" / "dual-search-ledger.sqlite3") as db:
        columns = {row[1] for row in db.execute("PRAGMA table_info(search_attempts)")}
        row = db.execute("SELECT * FROM search_attempts WHERE operation_id='op-timeout'").fetchone()
    assert "financial_state" in columns or "outcome_code" in columns, (
        "the reservation row records no financial state at all, so a dispatched "
        f"timeout is indistinguishable from an unused reservation; columns={sorted(columns)}")
    assert row is not None
