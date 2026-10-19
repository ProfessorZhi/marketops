"""The offline benchmark must be able to *fail*, or it is decoration.

A fabricated bundle is constructed that violates one clause per anti-fabrication
check. Every one of those checks must fire. A clean bundle must pass the same
checks. If the clean bundle also failed, the checks would be noise; if the
fabricated one passed, the control would be absent.

No network, no credential, no provider. The bundle's "sources" are local bytes.
"""
from __future__ import annotations

import json

import pytest


import benchmark_offline as bench
import test_qa_research_fixture as fixture


def _targets():
    return fixture._targets()


@pytest.fixture(scope="module")
def clean(tmp_path_factory):
    directory = tmp_path_factory.mktemp("bench-clean")
    return bench.build_clean_bundle(directory, _targets())


@pytest.fixture(scope="module")
def fabricated(tmp_path_factory):
    directory = tmp_path_factory.mktemp("bench-fabricated")
    return bench.build_fabricated_bundle(directory, _targets())


# --- the clean bundle ------------------------------------------------------


def test_a_clean_bundle_passes_every_anti_fabrication_check(clean):
    result = bench.evaluate_anti_fabrication(clean)
    assert result["verdict"] == "PASS", json.dumps(
        {k: v for k, v in result["checks"].items() if v["state"] != "PASS"},
        indent=2, ensure_ascii=False)


def test_a_clean_bundle_produces_the_published_metrics(clean):
    metrics = clean.row_metrics()
    assert metrics["submitted_rows"] == len(_targets())
    assert metrics["traceable_fact_ratio"] == 1.0, metrics
    assert metrics["duplicates_per_accepted_entity"] == 0, metrics


def test_cost_per_accepted_row_is_unknown_not_zero_when_nothing_was_accepted(clean):
    """§3 of the owner document: cost per *human-accepted* row. Before a
    reviewer has run there is no denominator, and 'no rows accepted' must not be
    reported as '$0.00 per row'."""
    metrics = clean.row_metrics()
    assert metrics["accepted_rows"] == 0
    assert metrics["usd_per_accepted_row"] is None
    assert metrics["credits_per_accepted_row"] is None


def test_the_bundle_records_the_brief_it_ran_against(clean):
    """AF-11's precondition: without a pinned brief hash the comparison baseline
    is unnamed and a later brief edit would be undetectable."""
    assert len(clean.brief_sha256) == 64, clean.brief_sha256


# --- the fabricated bundle: every check must fire --------------------------


EXPECTED_TO_FIRE = ("AF-1", "AF-2", "AF-3", "AF-4", "AF-5", "AF-6",
                    "AF-7", "AF-8", "AF-9", "AF-10")


def test_the_fabricated_bundle_fails_the_run(fabricated):
    result = bench.evaluate_anti_fabrication(fabricated)
    assert result["verdict"] == "FAIL", result


@pytest.mark.parametrize("check_id", EXPECTED_TO_FIRE)
def test_each_zero_tolerance_clause_actually_fires(fabricated, check_id):
    """One deliberately planted defect per check. A check that does not fire on
    its own planted defect cannot be trusted to fire on a real one."""
    result = bench.evaluate_anti_fabrication(fabricated)
    entry = result["checks"][check_id]
    assert entry["state"] == "FAIL", (
        f"{check_id} did not fire on its planted defect: {entry}")


def test_the_invented_contact_is_named_in_the_failure_detail(fabricated):
    result = bench.evaluate_anti_fabrication(fabricated)
    assert "tgt-01" in result["checks"]["AF-1"]["detail"], result["checks"]["AF-1"]


def test_a_snippet_grade_source_is_caught_even_though_the_hash_matches(fabricated):
    """AF-2 must be about the *grade*, not the hash: a well-formed excerpt from
    a search snippet is still not evidence."""
    result = bench.evaluate_anti_fabrication(fabricated)
    assert result["checks"]["AF-2"]["state"] == "FAIL"
    assert "tgt-02" in result["checks"]["AF-2"]["detail"]


def test_the_private_address_retrieval_is_caught_by_address_not_by_name(fabricated):
    result = bench.evaluate_anti_fabrication(fabricated)
    assert "169.254.169.254" in result["checks"]["AF-4"]["detail"], \
        result["checks"]["AF-4"]


def test_the_duplicate_charge_is_caught_by_signature(fabricated):
    result = bench.evaluate_anti_fabrication(fabricated)
    assert "serper|search|pool" in result["checks"]["AF-7"]["detail"], \
        result["checks"]["AF-7"]


def test_an_unpinned_brief_blocks_rather_than_passes(clean):
    """AF-11 with no brief hash is BLOCKED, and a BLOCKED check prevents PASS."""
    clean.brief_sha256 = ""
    try:
        result = bench.evaluate_anti_fabrication(clean)
        assert result["checks"]["AF-11"]["state"] == "BLOCKED"
        assert result["verdict"] != "PASS", (
            "a blocked check must prevent a PASS verdict, not be ignored")
    finally:
        clean.brief_sha256 = bench.fixture_brief_sha256()


# --- the benchmark itself --------------------------------------------------


def test_the_benchmark_writes_a_bundle_a_reviewer_can_read(clean, tmp_path):
    out = bench.write_bundle(clean, tmp_path / "out")
    payload = json.loads(out.read_text(encoding="utf-8"))
    assert payload["metrics"]["submitted_rows"] == len(_targets())
    assert "requests" in payload and "operations" in payload
    assert all("source_url" in row for row in payload["rows"])


def test_the_benchmark_makes_no_network_call(clean):
    """Every 'retrieval' is a recorded intention, never a socket."""
    for method, url in clean.requests:
        assert method == "GET", (method, url)
        assert url.startswith("https://"), url
    assert not hasattr(bench, "requests"), (
        "the benchmark must not import an HTTP client")


def test_the_rubric_grades_used_by_the_benchmark_are_the_published_ones(clean, fabricated):
    """A bundle may only claim grades the rubric defines."""
    published = {g["grade"] for g in fixture._load(fixture.RUBRIC)["grades"]}
    for bundle in (clean, fabricated):
        for row in bundle.rows:
            assert row.source_grade in published, row.source_grade
