"""The Research Core dataset contract: schema, counts, determinism, honesty.

These tests are the reason the JSONL is generated rather than hand-written. If a
committed case drifts from the builder, or a gold label claims something the
snapshot bytes do not support, this file fails.

The floor counts are the task's minimums, not a target: a stage may have more
cases, never fewer, and a shortfall is reported rather than padded.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import pathlib
import re
import sys

import pytest

REPO = pathlib.Path(__file__).resolve().parents[1]
DATASET = REPO / "benchmark" / "datasets" / "research-core"
SYNTHETIC = DATASET / "synthetic"
BUILDER = DATASET / "build_datasets.py"

sys.path.insert(0, str(REPO))

from benchmark.research_core.model import load_dataset_dir  # noqa: E402

STAGES = ("brief", "search", "fetch", "extraction", "qualification",
          "identity", "evidence", "channel", "e2e")

FLOOR = {
    "brief": 3,
    "search": 40,
    "fetch": 30,
    "extraction": 30,
    "qualification": 20,
    "identity": 15,
    "evidence": 15,
    "channel": 6,
    "e2e": 3,
}

CASE_ID = re.compile(r"^RC-[A-Z0-9-]+-[0-9]{3}$")


def _builder():
    spec = importlib.util.spec_from_file_location("research_build_datasets", BUILDER)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.path.insert(0, str(DATASET / "corpus"))
    try:
        spec.loader.exec_module(module)
    finally:
        sys.path.remove(str(DATASET / "corpus"))
    return module


def _on_disk(stage: str) -> list[dict]:
    path = SYNTHETIC / stage / "cases.jsonl"
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


@pytest.mark.parametrize("stage", STAGES)
def test_stage_dataset_validates_against_schema(stage: str) -> None:
    cases = load_dataset_dir(SYNTHETIC, stage)
    assert cases, f"{stage} has no cases"
    assert len(cases) >= FLOOR[stage], f"{stage}: {len(cases)} cases < floor {FLOOR[stage]}"
    assert {case["stage"] for case in cases} == {stage}


@pytest.mark.parametrize("stage", STAGES)
def test_case_ids_are_unique_and_canonical(stage: str) -> None:
    cases = load_dataset_dir(SYNTHETIC, stage)
    ids = [case["case_id"] for case in cases]
    assert len(ids) == len(set(ids)), f"{stage} has duplicate case_id"
    for case_id in ids:
        assert CASE_ID.match(case_id), f"{stage}: {case_id!r} is not a canonical case id"


@pytest.mark.parametrize("stage", STAGES)
def test_rebuild_is_byte_identical(stage: str) -> None:
    """The committed JSONL must equal what the builder emits, byte for byte."""
    module = _builder()
    builders = {
        "brief": module.build_briefs, "search": module.build_search,
        "fetch": module.build_fetch, "extraction": module.build_extraction,
        "qualification": module.build_qualification, "identity": module.build_identity,
        "evidence": module.build_evidence, "channel": module.build_channel,
        "e2e": module.build_e2e,
    }
    text = "".join(json.dumps(case, ensure_ascii=False, sort_keys=False) + "\n"
                   for case in builders[stage]())
    assert text == (SYNTHETIC / stage / "cases.jsonl").read_text(encoding="utf-8")


def test_case_counts_are_reported_not_padded() -> None:
    counts = {stage: len(_on_disk(stage)) for stage in STAGES}
    assert sum(counts.values()) == 175, counts
    assert counts["search"] >= 20  # 20 intent categories
    intents = {case["input"]["intent"] for case in _on_disk("search")}
    assert len(intents) >= 20, sorted(intents)


def test_search_covers_required_intents() -> None:
    required = {
        "official_website", "contact_page", "team_page", "executive",
        "youtube_channel", "youtube_creator", "exact_company", "long_tail",
        "multilingual", "country_limited", "parent_subsidiary", "disambiguation",
        "competitor", "identity_link", "official_source_priority",
        "product_catalogue", "pdf_document", "news_article", "job_posting",
        "social_profile", "local_business",
    }
    intents = {case["input"]["intent"] for case in _on_disk("search")}
    assert required <= intents, sorted(required - intents)


def test_fetch_covers_required_page_kinds() -> None:
    kinds = {case["gold"]["page_kind"] for case in _on_disk("fetch")}
    assert len(kinds) >= 25, sorted(kinds)
    assert {"static_html", "redirect_chain", "cookie_consent",
            "pdf_link", "pdf_text", "javascript_rendered", "not_found", "binary_garbage",
            "wrong_language", "duplicate_content", "stale_content"} <= kinds


def test_extraction_has_personal_email_negatives() -> None:
    cases = _on_disk("extraction")
    negatives = [case for case in cases if case["gold"].get("expect_no_personal_email")]
    assert len(negatives) >= 10, len(negatives)
    for case in negatives:
        assert all(email["kind"] != "personal" for email in case["gold"].get("emails", []))


def test_qualification_covers_ten_distinct_statuses() -> None:
    """Ten outcomes, each asking a different question -- not one bucket renamed."""
    statuses = {case["gold"]["qualification_status"] for case in _on_disk("qualification")}
    assert len(statuses) >= 10, sorted(statuses)
    assert {"strong", "partial", "weak", "not_qualified", "excluded",
            "insufficient_evidence", "duplicate", "out_of_scope",
            "unverified_identity", "needs_review"} <= statuses


def test_qualification_evidence_notes_are_quoted_from_the_corpus() -> None:
    """A cited evidence id must have text a person can actually find on a page."""
    corpus = DATASET / "corpus"
    checked = 0
    for case in _on_disk("qualification"):
        notes = case["input"]["prospect"].get("evidence_notes") or {}
        if not notes:
            continue
        slug = case["provenance"]["source_ref"].split("/")[-2]
        pages = [p.read_text(encoding="utf-8")
                 for p in (corpus / "sites" / slug).glob("*.html")]
        for evidence_id, quote in notes.items():
            assert any(quote in page for page in pages), (case["case_id"], evidence_id)
            checked += 1
    assert checked >= 4


def test_edge_type_records_stay_out_of_the_search_index() -> None:
    """They exist to exercise a verdict, not to be discovered by a query.

    Keeping them out is what keeps the frozen search expectations unchanged.
    """
    index = json.loads((DATASET / "corpus" / "search_index.json").read_text(encoding="utf-8"))
    indexed = {row["domain"] for row in index.values()}
    edge = {case["input"]["prospect"]["domain"] for case in _on_disk("qualification")
            if case["input"]["prospect"].get("record_type")
            or case["input"]["prospect"].get("domain_status")}
    assert edge
    assert edge.isdisjoint(indexed), sorted(edge & indexed)


def test_evidence_missing_snapshot_is_not_claimed_verified() -> None:
    for case in _on_disk("evidence"):
        snapshot = DATASET / case["input"]["evidence"]["snapshot_path"]
        if not snapshot.is_file():
            assert case["gold"]["snapshot_exists"] is False
            assert case["gold"]["sha256_matches"] is False
            assert case["gold"]["fields_traceable"] == []


def test_corpus_manifest_checksum_is_the_documented_one() -> None:
    manifest = DATASET / "corpus" / "MANIFEST.json"
    digest = hashlib.sha256(manifest.read_bytes()).hexdigest()
    assert digest.startswith("0ecfa5b03b84d2b723f0bc1308687d28bafde516f3c1649f0e631b8ee858da81")


def test_synthetic_pages_use_reserved_tld() -> None:
    """.example keeps the corpus from ever resolving to a real company."""
    domains = {case["provenance"]["source_ref"] for case in _on_disk("search")}
    assert domains
    for case in _on_disk("qualification"):
        domain = case["input"]["prospect"]["domain"]
        assert domain.endswith(".example"), domain


# ---------------------------------------------------------------------------
# Public Evidence Benchmark -- real pages, frozen bytes, no network at test time
# ---------------------------------------------------------------------------

PUBLIC = DATASET / "public-evidence"
PUBLIC_ORIGIN = "public_real_world"


def _public(stage: str) -> list[dict]:
    return load_dataset_dir(PUBLIC, stage)


def _public_manifest() -> dict:
    return json.loads((PUBLIC / "capture-manifest.json").read_text(encoding="utf-8"))


@pytest.mark.parametrize("stage", ("fetch", "evidence"))
def test_public_dataset_validates_against_schema(stage: str) -> None:
    cases = _public(stage)
    assert cases, f"public {stage} has no cases"
    ids = [case["case_id"] for case in cases]
    assert len(ids) == len(set(ids))
    assert {case["origin"] for case in cases} == {PUBLIC_ORIGIN}
    assert {case["stage"] for case in cases} == {stage}


def test_public_cases_are_served_locally_only() -> None:
    """A test must never reach the internet: every fetch URL is the local server."""
    for case in _public("fetch"):
        url = case["input"]["url"]
        assert url.startswith("http://127.0.0.1:18091/"), url
        assert case["input"]["final_url_expected"] is None


def test_public_snapshot_hashes_recompute() -> None:
    manifest = _public_manifest()
    assert manifest["snapshots"], "no snapshots captured"
    for record in manifest["snapshots"]:
        path = PUBLIC / pathlib.Path(record["snapshot_path"]).relative_to("public-evidence")
        assert path.is_file(), record["snapshot_path"]
        assert hashlib.sha256(path.read_bytes()).hexdigest() == record["snapshot_sha256"]
        # Desensitisation rewrites every address to the reserved .example domain,
        # so a committed snapshot can never carry a real one.
        # Same shape the desensitiser targets: a real address ends in an
        # alphabetic TLD, so npm-style `pkg@0.9.19` tokens are not addresses.
        addresses = set(re.findall(
            r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}",
            path.read_text(encoding="utf-8")))
        assert all(a.endswith(".example") for a in addresses), sorted(addresses)[:5]


def test_public_cases_trace_back_to_a_manifest_snapshot() -> None:
    manifest = _public_manifest()
    recorded = {record["snapshot_path"]: record for record in manifest["snapshots"]}
    fetch_cases = _public("fetch")
    evidence_cases = _public("evidence")
    assert len(fetch_cases) == len(evidence_cases) == len(recorded)
    for case in evidence_cases:
        evidence = case["input"]["evidence"]
        assert evidence["snapshot_path"] in recorded
        assert evidence["sha256"] == recorded[evidence["snapshot_path"]]["snapshot_sha256"]
        assert evidence["source_url"] == recorded[evidence["snapshot_path"]]["source_url"]


def test_public_gold_is_readable_off_the_frozen_bytes() -> None:
    """Every claimed traceable field must be visible in the committed snapshot."""
    import html as _html
    import re as _re

    def words(markup: str) -> str:
        body = _re.sub(r"<(script|style)\b.*?</\1>", " ", markup, flags=_re.S | _re.I)
        text = _html.unescape(_re.sub(r"<[^>]+>", " ", body)).lower()
        return _re.sub(r"[^a-z0-9]+", " ", text)

    for case in _public("evidence"):
        evidence = case["input"]["evidence"]
        snapshot = PUBLIC / pathlib.Path(evidence["snapshot_path"]).relative_to("public-evidence")
        haystack = words(snapshot.read_text(encoding="utf-8"))
        for value in case["gold"]["fields_traceable"]:
            needle = _re.sub(r"[^a-z0-9]+", " ", value.lower()).strip()
            assert needle and needle in haystack, f"{case['case_id']}: {value!r} not in snapshot"


def test_public_gaps_are_recorded_not_padded() -> None:
    """A site that blocked the capture is a gap, never filled with a stand-in."""
    manifest = _public_manifest()
    assert manifest["counts"]["snapshots"] == len(manifest["snapshots"])
    assert manifest["counts"]["gaps"] == len(manifest["gaps"])
    reasons = {gap["reason"] for gap in manifest["gaps"]}
    allowed = {"disallowed_by_robots", "login_required", "forbidden", "rate_limited"}
    for reason in reasons:
        assert reason in allowed or reason.startswith(("http_", "content_type=")) \
            or ":" in reason, reason
    urls = [record["source_url"] for record in manifest["snapshots"]]
    assert len(urls) == len(set(urls)), "a URL was captured twice"
    # A slug may have one page frozen and another blocked; the gap still stands.
    gap_urls = [gap["url"] for gap in manifest["gaps"]]
    assert not set(urls) & set(gap_urls), "a URL both captured and recorded as a gap"


def test_public_spread_across_countries_and_languages() -> None:
    manifest = _public_manifest()
    records = manifest["snapshots"]
    assert len({r["country"] for r in records}) >= 3
    assert len({r["language"] for r in records}) >= 3
    assert len({r["slug"] for r in records if r["platform"] == "website"}) >= 10
    assert len({r["slug"] for r in records if r["platform"] == "youtube"}) >= 5


def test_public_labels_are_not_claimed_human_verified() -> None:
    for stage in ("fetch", "evidence"):
        for case in _public(stage):
            assert case["label_status"] == "draft", case["case_id"]
            assert case["provenance"]["content_sha256"]
