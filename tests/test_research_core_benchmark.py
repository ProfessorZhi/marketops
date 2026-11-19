"""The Research Core harness contract.

What this file holds fixed:

  * the mock matrix (7 providers x 10 failure modes) never collapses
    ``not_configured`` / ``empty`` / ``failed`` / ``invalid_output`` / ``timeout``
    into one another -- the distinction is the benchmark's whole reason to exist;
  * the runner never *scores* a row that did not run, and never treats an empty
    answer as a failure;
  * three repeats of the same dataset produce byte-identical gold metrics;
  * the fixture server really serves the awkward shapes (redirect chain, cookie
    gate, meta refresh, PDF, JS rows) the fetch gold is written against;
  * every evidence snapshot's SHA256 recomputes from the bytes on disk.
"""

from __future__ import annotations

import hashlib
import io
import json
import pathlib
import tempfile

import httpx
import pytest

REPO = pathlib.Path(__file__).resolve().parents[1]
DATASET = REPO / "benchmark" / "datasets" / "research-core"
SYNTHETIC = DATASET / "synthetic"

from benchmark.research_core import runner  # noqa: E402
from benchmark.research_core.adapters import mock as mock_adapters  # noqa: E402
from benchmark.research_core.adapters.base import (  # noqa: E402
    AdapterInfo, NotConfigured,
)
from benchmark.research_core.cli import REFERENCE  # noqa: E402
from benchmark.research_core.mocks import contract, web  # noqa: E402
from benchmark.research_core.model import load_dataset_dir  # noqa: E402
from benchmark.research_core.report import render  # noqa: E402

STAGES = ("brief", "search", "fetch", "extraction", "qualification",
          "identity", "evidence", "channel", "e2e")


# ---------------------------------------------------------------------------
# Mock provider contract
# ---------------------------------------------------------------------------

def test_mock_contract_matrix_is_complete() -> None:
    rows = contract.run_matrix()
    assert len(rows) == (len(contract.SEARCH_PROVIDERS) + len(contract.FETCH_PROVIDERS)) * 10
    failed = contract.failures(rows)
    assert not failed, json.dumps(failed, indent=2)


def test_failure_modes_never_collapse() -> None:
    rows = contract.run_matrix()
    by_mode: dict[str, set[str]] = {}
    for row in rows:
        by_mode.setdefault(row["mode"], set()).add(row["actual"])
    # malformed JSON is a *failed* request; a well-formed but wrong shape is
    # *invalid_output*; "the provider answered with nothing" is *empty*.
    assert by_mode["invalid_json"] == {"failed"}
    assert by_mode["bad_format"] == {"invalid_output"}
    assert by_mode["missing_field"] == {"invalid_output"}
    assert by_mode["empty"] == {"empty"}
    assert by_mode["timeout"] == {"timeout"}
    assert by_mode["rate_limited"] == {"failed"}
    assert by_mode["server_error"] == {"failed"}
    assert by_mode["normal"] == {"ok"}
    # no error mode may ever be reported as ok
    for mode in ("timeout", "rate_limited", "server_error", "bad_format",
                 "missing_field", "invalid_json"):
        assert "ok" not in by_mode[mode], mode


# ---------------------------------------------------------------------------
# Runner status separation
# ---------------------------------------------------------------------------

class _NotConfigured:
    info = AdapterInfo(name="fake_unconfigured", stage="search", kind="mock")

    def availability(self) -> tuple[bool, str]:
        return False, "no API key for fake provider"

    def run(self, case: dict) -> dict:  # pragma: no cover - must not be called
        raise AssertionError("availability() said no; run() must not be called")


class _Empty:
    info = AdapterInfo(name="fake_empty", stage="search", kind="mock")

    def availability(self) -> tuple[bool, str]:
        return True, ""

    def run(self, case: dict) -> dict:
        return {"results": []}


class _Broken:
    info = AdapterInfo(name="fake_broken", stage="search", kind="mock")

    def availability(self) -> tuple[bool, str]:
        return True, ""

    def run(self, case: dict) -> dict:
        raise NotConfigured("misconfigured mid-run")


def _run_with(adapter, stage: str = "search") -> dict:
    with tempfile.TemporaryDirectory() as tmp:
        result = runner.execute(dataset_root=SYNTHETIC, stage=stage,
                                adapter=adapter, out_dir=pathlib.Path(tmp))
        return result["manifest"]


def test_missing_credential_is_not_configured_and_never_scored() -> None:
    manifest = _run_with(_NotConfigured())
    assert manifest["counts"]["not_configured"] == manifest["counts"]["cases"]
    assert manifest["counts"]["scored"] == 0
    assert manifest["counts"]["failed"] == 0


def test_empty_answer_is_scored_not_failed() -> None:
    manifest = _run_with(_Empty())
    assert manifest["counts"]["empty"] == manifest["counts"]["cases"]
    assert manifest["counts"]["scored"] == manifest["counts"]["cases"]
    assert manifest["counts"]["failed"] == 0


def test_credential_error_mid_run_is_never_ok() -> None:
    manifest = _run_with(_Broken())
    assert manifest["counts"]["not_configured"] == manifest["counts"]["cases"]
    assert manifest["counts"]["ok"] == 0


# ---------------------------------------------------------------------------
# Determinism
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("stage", STAGES)
def test_reference_metrics_are_repeat_identical(stage: str) -> None:
    registry = mock_adapters.registry()
    adapter = registry.get(REFERENCE[stage], stage=stage)
    with tempfile.TemporaryDirectory() as tmp:
        result = runner.execute(dataset_root=SYNTHETIC, stage=stage, adapter=adapter,
                                repeat=3, out_dir=pathlib.Path(tmp))
    assert result["manifest"]["metrics"]["repeat_identical"] is True


# ---------------------------------------------------------------------------
# Fixture server
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def base_url() -> str:
    mock_adapters.ensure_fixture_server()
    return mock_adapters.FIXTURE_BASE


def test_fixture_server_health(base_url: str) -> None:
    assert httpx.get(f"{base_url}/healthz", timeout=5).text.strip() == "ok"


def test_fixture_redirect_chain_ends_on_the_about_page(base_url: str) -> None:
    response = httpx.get(f"{base_url}/dynamic/redirect/1", follow_redirects=True, timeout=5)
    assert response.status_code == 200
    assert response.url.path == "/northwind-marine/about.html"
    assert "Northwind Marine" in response.text


def test_fixture_cookie_gate_needs_the_cookie(base_url: str) -> None:
    blocked = httpx.get(f"{base_url}/dynamic/cookie/check", timeout=5)
    assert blocked.status_code == 403
    client = httpx.Client(timeout=5)
    client.get(f"{base_url}/dynamic/cookie/set")  # stores consent=yes
    allowed = client.get(f"{base_url}/dynamic/cookie/check")
    assert allowed.status_code == 200
    assert "info@northwindmarine.example" in allowed.text


def test_fixture_meta_refresh_points_at_a_real_page(base_url: str) -> None:
    page = httpx.get(f"{base_url}/dynamic/meta-refresh", timeout=5).text
    assert 'http-equiv="refresh"' in page
    target = page.split("url=")[1].split('"')[0]
    assert httpx.get(f"{base_url}{target}", timeout=5).status_code == 200


def test_fixture_pdf_has_a_text_layer(base_url: str) -> None:
    from pypdf import PdfReader

    response = httpx.get(f"{base_url}/dynamic/catalogue.pdf", timeout=5)
    assert response.headers["content-type"] == "application/pdf"
    reader = PdfReader(io.BytesIO(response.content))
    text = " ".join(page.extract_text() or "" for page in reader.pages)
    assert "Braunfeld" in text and "CNC" in text


def test_fixture_binary_route_is_not_text(base_url: str) -> None:
    response = httpx.get(f"{base_url}/dynamic/binary", timeout=5)
    assert response.headers["content-type"] == "image/png"


# ---------------------------------------------------------------------------
# Evidence provenance
# ---------------------------------------------------------------------------

def test_every_present_snapshot_hash_recomputes() -> None:
    cases = load_dataset_dir(SYNTHETIC, "evidence")
    verified = 0
    for case in cases:
        evidence = case["input"]["evidence"]
        snapshot = DATASET / evidence["snapshot_path"]
        if not snapshot.is_file():
            continue
        digest = hashlib.sha256(snapshot.read_bytes()).hexdigest()
        # A deliberately corrupted recorded hash must be reported as a mismatch.
        assert (digest == evidence["sha256"]) is case["gold"]["sha256_matches"], case["case_id"]
        if digest == evidence["sha256"]:
            verified += 1
    assert verified >= 10, f"only {verified} snapshots verified"


def test_fetch_snapshot_hashes_match_the_manifest() -> None:
    manifest = json.loads((DATASET / "corpus" / "MANIFEST.json").read_text(encoding="utf-8"))
    by_path = {path: entry["sha256"] for path, entry in manifest["files"].items()}
    cases = [case for case in load_dataset_dir(SYNTHETIC, "fetch")
             if case["provenance"]["content_sha256"]]
    assert cases
    for case in cases:
        ref = case["provenance"]["source_ref"].removeprefix("corpus/")
        assert case["provenance"]["content_sha256"] == by_path[ref], case["case_id"]


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------

def test_report_renders_every_stage() -> None:
    registry = mock_adapters.registry()
    summaries = {}
    with tempfile.TemporaryDirectory() as tmp:
        for stage in STAGES:
            adapter = registry.get(REFERENCE[stage], stage=stage)
            result = runner.execute(dataset_root=SYNTHETIC, stage=stage, adapter=adapter,
                                    out_dir=pathlib.Path(tmp))
            manifest = result["manifest"]
            summaries[stage] = {
                "provider": manifest["provider"], "run_id": manifest["run_id"],
                "counts": manifest["counts"], "metrics": manifest["metrics"],
                "manifest_path": str(result["manifest_path"]),
                "results_path": str(result["results_path"]),
                "dataset_checksum": manifest["datasets"][0]["checksum"],
                "dataset_version": manifest["datasets"][0]["version"],
            }
    text = render(summaries)
    for stage in STAGES:
        assert stage in text
    assert "no live API was called" in text


# ---------------------------------------------------------------------------
# Public Evidence Benchmark -- frozen real pages, scored offline
# ---------------------------------------------------------------------------

PUBLIC = DATASET / "public-evidence"


@pytest.mark.parametrize("stage", ("fetch", "evidence"))
def test_public_evidence_scores_offline_and_repeats(stage: str) -> None:
    """The public benchmark is scored from committed bytes, not from the live web."""
    adapter = mock_adapters.registry().get(REFERENCE[stage], stage=stage)
    with tempfile.TemporaryDirectory() as tmp:
        result = runner.execute(dataset_root=PUBLIC, stage=stage, adapter=adapter,
                                repeat=3, out_dir=pathlib.Path(tmp))
    counts = result["manifest"]["counts"]
    assert counts["cases"] == 56
    # Only ``ok``/``empty`` are possible: a failed or not_configured case here would
    # mean the offline copy is incomplete, which must be reported, not smoothed.
    assert counts["failed"] == 0
    assert counts["not_configured"] == 0
    assert counts["timeout"] == 0
    assert result["manifest"]["metrics"]["repeat_identical"] is True


def test_public_evidence_app_shell_is_not_a_false_success() -> None:
    """RC-FECH-PUB-034 is an Angular shell whose only visible text is 'SKF'.

    The reference fetcher reports it empty, the gold says ``no_content``, and no
    metric may count it as a page that was successfully read.
    """
    cases = {case["case_id"]: case for case in load_dataset_dir(PUBLIC, "fetch")}
    case = cases["RC-FECH-PUB-034"]
    assert case["input"]["expected_outcome"] == "no_content"
    assert case["gold"]["requires_js"] is True
    assert case["gold"]["expected_text_markers"] == []


def test_public_suite_summary_is_rendered_from_manifests() -> None:
    summary = json.loads((REPO / "benchmark" / "research-core" / "runs" /
                          "suite-summary-public.json").read_text(encoding="utf-8"))
    assert set(summary) == {"fetch", "evidence"}
    text = render(summary)
    assert "Public" in text or "public" in text
    for stage in ("fetch", "evidence"):
        assert summary[stage]["dataset_version"] == "research-core-public-1.0.0"
        assert summary[stage]["counts"]["failed"] == 0
