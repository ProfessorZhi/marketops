"""Validate the real-CC research fixture before anyone spends money on it.

The fixture lives at ``docs/wave1-qa/fixtures/`` and is the *input* to the
owner's Stage 2 real Claude Code run (``docs/acceptance/
MARKETOPS_LIVE_CC_QUALITY_AND_BUDGET_GATE_2026-10-17.md`` §2). A rubric or a
prohibition list that is malformed, incomplete or self-contradictory would make
the whole quality verdict unfalsifiable, so it is checked here, offline, with
no network and no provider.

These tests are about the *fixture*, not about any research result: at this
point no real row exists and none is invented here.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from conftest import REPO_ROOT

FIXTURES = REPO_ROOT / "docs" / "wave1-qa" / "fixtures"
BRIEF = FIXTURES / "research-brief.json"
TARGETS = FIXTURES / "targets.jsonl"
RUBRIC = FIXTURES / "source-quality-rubric.json"
ANTI_FAB = FIXTURES / "anti-fabrication-checks.json"
LINEAGE = FIXTURES / "artifact-lineage.schema.json"
LEDGER = FIXTURES / "cost-ledger.schema.json"

EVIDENCE_GRADES = {"A", "B"}
NON_EVIDENCE_GRADES = {"C", "D", "E", "F"}

# The eight zero-tolerance families the owner document §3 names.
OWNER_ZERO_TOLERANCE = {
    "invented_person_or_email": ("AF-1", "AF-12"),
    "snippet_as_original_source": ("AF-2",),
    "challenge_page_as_validated_evidence": ("AF-3",),
    "forbidden_private_domain_access": ("AF-4",),
    "undeclared_paid_call": ("AF-5",),
    "untracked_charge": ("AF-6",),
    "duplicate_charge_after_timeout": ("AF-7",),
    "unauthorized_side_effect": ("AF-8",),
}


def _load(path: Path):
    if not path.is_file():
        pytest.fail(f"the fixture {path.relative_to(REPO_ROOT)} does not exist; "
                    "the real run has no declared input")
    return json.loads(path.read_text(encoding="utf-8"))


def _targets():
    if not TARGETS.is_file():
        pytest.fail("docs/wave1-qa/fixtures/targets.jsonl is missing")
    rows = []
    for line_no, line in enumerate(TARGETS.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError as exc:
            pytest.fail(f"targets.jsonl line {line_no} is not JSON: {exc}")
    return rows


# --- the brief -------------------------------------------------------------


def test_the_brief_is_bounded_and_names_its_geography():
    brief = _load(BRIEF)["input"]
    count = brief["target_count"]
    assert count["min"] == 10 and count["max"] == 20, (
        "§2 requires 10–20 targets; the brief must carry the bound it was given")
    geography = brief["geography"]
    assert geography["name"] and geography["bounding"], (
        "a bounded geography is required — an unbounded one makes the sample "
        "unreproducible")
    assert geography["excluded"], "the brief must exclude something explicitly"


def test_the_brief_forbids_the_actions_the_owner_forbade():
    prohibited = " ".join(_load(BRIEF)["input"]["hard_prohibitions"]).casefold()
    for term in ("outreach", "contact", "private", "form", "personal data"):
        assert term in prohibited, (
            f"the brief does not forbid {term!r}; §2 forbids private accounts, "
            "outreach, form submission and personal data")


def test_the_brief_does_not_pre_commit_fake_acceptance_thresholds():
    """§3: "Let the first real baseline determine realistic numerical acceptance
    thresholds; do not set fake 100%-everything pass targets."."""
    thresholds = _load(BRIEF)["input"]["acceptance_thresholds"]
    assert thresholds["status"] == "TO_BE_SET_FROM_FIRST_REAL_BASELINE", thresholds
    assert not any(isinstance(v, (int, float)) for k, v in thresholds.items()
                   if not k.startswith("_")), (
        "a numeric threshold was invented before the baseline existed")


def test_the_brief_permits_no_paid_call_in_this_stage():
    budget = _load(BRIEF)["input"]["budget"]
    assert budget["paid_provider_calls"] == "none in this stage", budget


# --- the target seed list --------------------------------------------------


def test_the_seed_list_is_within_the_required_range():
    rows = _targets()
    assert 10 <= len(rows) <= 20, f"the brief asks for 10–20 targets; got {len(rows)}"


def test_every_seed_is_a_public_organisation_and_carries_no_person():
    forbidden_keys = {"email", "phone", "contact_name", "person", "individual",
                      "first_name", "last_name"}
    for row in _targets():
        assert not (forbidden_keys & set(row)), (
            f"seed {row.get('target_id')} carries personal-data fields: "
            f"{sorted(forbidden_keys & set(row))}")
        assert row["kind"] in {"museum", "gallery", "arts_centre", "library",
                               "leisure_centre", "public_venue"}, row["kind"]


def test_no_seed_claims_to_be_verified_yet():
    """The seed list is a work list, not evidence. Every row must say so."""
    for row in _targets():
        assert row["verification"] == "PENDING", row
        for field in ("verified_url", "source_excerpt_sha256", "retrieved_at"):
            assert row[field] is None, (
                f"seed {row['target_id']} pre-fills {field}; a value written "
                "before the run would be fabricated provenance")


def test_seed_identifiers_and_names_are_unique():
    rows = _targets()
    ids = [r["target_id"] for r in rows]
    names = [r["name"].casefold() for r in rows]
    assert len(set(ids)) == len(ids), "duplicate target_id in the seed list"
    assert len(set(names)) == len(names), (
        "the seed list contains the same organisation twice, which would inflate "
        "the denominator of every per-row metric")


# --- the rubric ------------------------------------------------------------


def test_the_rubric_is_total_over_the_grades_it_uses():
    rubric = _load(RUBRIC)
    grades = {g["grade"] for g in rubric["grades"]}
    assert grades == EVIDENCE_GRADES | NON_EVIDENCE_GRADES, grades
    for entry in rubric["grades"]:
        assert entry["definition"].strip(), entry
        assert isinstance(entry["counts_as_evidence"], bool), entry


def test_only_official_grades_are_allowed_to_count_as_evidence():
    rubric = _load(RUBRIC)
    counting = {g["grade"] for g in rubric["grades"] if g["counts_as_evidence"]}
    assert counting == EVIDENCE_GRADES, (
        f"grades counting as evidence are {counting}; an aggregator, a snippet or "
        "a challenge page must never be sufficient on its own")
    acceptance = rubric["field_acceptance"]["rule"]
    assert "A,B" in acceptance.replace(" ", ""), acceptance


def test_the_rubric_publishes_the_measurements_the_owner_asked_for():
    ids = {m["id"] for m in _load(RUBRIC)["measurements"]}
    required = {"official_domain_accuracy", "traceable_fact_ratio",
                "accepted_valid_rows", "duplicates_per_accepted_entity",
                "missed_required_fields", "false_positives",
                "manual_minutes_per_accepted_row", "usd_per_accepted_row",
                "credits_per_accepted_row", "failure_causes"}
    assert required <= ids, f"the rubric omits measurements: {sorted(required - ids)}"


def test_the_rubric_compares_on_cost_per_accepted_result():
    rule = _load(RUBRIC)["comparison_rule"].casefold()
    assert "accepted" in rule and "not on api call count" in rule, rule


def test_the_rubric_declares_a_blind_two_pass_audit():
    blind = _load(RUBRIC)["blind_audit"]
    assert "blind" in json.dumps(blind).casefold() or "without seeing" in blind["definition"]
    assert "pass 2" in blind["two_pass"].casefold(), blind


# --- anti-fabrication coverage --------------------------------------------


def test_every_owner_zero_tolerance_clause_has_an_enforceable_check():
    checks = {c["id"]: c for c in _load(ANTI_FAB)["checks"]}
    for clause, ids in OWNER_ZERO_TOLERANCE.items():
        assert any(i in checks for i in ids), (
            f"the owner's zero-tolerance clause {clause!r} has no check")
    for check in checks.values():
        for field in ("id", "owner_clause", "reads", "fails_when", "severity"):
            assert check[field], check
        assert check["severity"] == "P0", check


def test_an_unreadable_check_blocks_rather_than_passes():
    rule = _load(ANTI_FAB)["evaluation_rule"].casefold()
    assert "blocked" in rule and "prevents a pass" in rule, rule


def test_a_single_p0_failure_fails_the_run():
    rule = _load(ANTI_FAB)["verdict_rule"].casefold()
    assert "fail" in rule and "regardless" in rule, rule


# --- schemas ---------------------------------------------------------------


def test_the_lineage_schema_requires_a_hash_and_a_retrieval_decision():
    schema = _load(LINEAGE)
    required = set(schema["required"])
    assert {"content_sha256", "origin_url", "parent_ids", "derivation"} <= required
    assert schema["properties"]["validated"]["const"] is False, (
        "storage must never be reported as validation")
    assert "challenge_suspected" in schema["properties"], (
        "the schema must be able to mark a challenge page so AF-3 is decidable")


def test_the_ledger_schema_keeps_the_four_money_states_apart():
    schema = _load(LEDGER)
    op = schema["properties"]["operations"]["items"]
    assert {"reserved", "estimated", "derived", "actual"} <= set(op["required"]), (
        "§1 forbids hiding the difference between reservation, estimate, derived "
        "and confirmed actual charge")
    assert "null" in op["properties"]["actual"]["type"], (
        "an unconfirmed charge must be representable as null, never defaulted to zero")


def test_the_ledger_schema_carries_the_twenty_percent_floor_per_provider():
    schema = _load(LEDGER)
    provider = schema["properties"]["providers"]["items"]
    required = set(provider["required"])
    assert {"b0", "floor", "max_cumulative_drawdown", "spendable_ceiling"} <= required
    sources = provider["properties"]["b0_source"]["enum"]
    assert "provider_tool_read" in sources, (
        "the balance must name a real source")
    # §1.1: environment variables, documentation prices, mocked tools and
    # credential-file presence are not evidence of a balance.
    for not_evidence in ("environment", "env", "documentation", "mock", "credential_file"):
        assert not_evidence not in sources, (
            f"{not_evidence!r} must not be an accepted source of a balance")


def test_the_ledger_schema_refuses_zero_cost_per_row_when_nothing_was_accepted():
    totals = _load(LEDGER)["properties"]["totals"]["properties"]
    assert "null" in json.dumps(totals["usd_per_accepted_row"]["type"]), (
        "with no accepted row the cost per accepted row is unknown, not $0.00")
    assert "floor_breached" in totals, (
        "the ledger must be able to state that a floor was breached")


def test_the_fixture_validates_against_the_schemas_it_ships():
    """The two JSON Schemas must actually accept a conforming example."""
    jsonschema = pytest.importorskip("jsonschema")

    lineage = _load(LINEAGE)
    ledger = _load(LEDGER)
    jsonschema.Draft202012Validator.check_schema(lineage)
    jsonschema.Draft202012Validator.check_schema(ledger)

    example_lineage = {
        "artifact_id": "art-1", "run_id": "runA", "provider": "crawl4ai_local",
        "origin_url": "https://example.org/contact", "material_kind": "rendered_html",
        "content_sha256": "0" * 64, "bytes": 10,
        "acquired_at": "2026-10-17T00:00:00Z", "parent_ids": [], "derivation": "retrieved",
        "source_grade": "A", "challenge_suspected": False, "validated": False,
        "retrieval_decision": {"allowed": True, "checked_address": "203.0.113.10",
                               "policy_source": "egress_gate"},
    }
    jsonschema.validate(example_lineage, lineage)
