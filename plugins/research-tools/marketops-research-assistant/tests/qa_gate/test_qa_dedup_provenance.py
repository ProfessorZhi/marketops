"""Deduplication, source provenance and the candidate→formal-core boundary.

Acceptance source: owner brief §2 items 5 and 7 ("trustworthy research", "no
accidental uncontrolled bypass") and §P1-03 Key A/B (logical intent fingerprint
vs. provider execution fingerprint), plus §P1-06 "Evidence/core boundary".

The plugin's staging store is deliberately strong here and most of this file is
green. The red entries are the *converged* requirements the current candidate
does not yet carry: a cross-provider logical-intent fingerprint, and a single
read model over the two spend ledgers.
"""
from __future__ import annotations

import json
import sqlite3

import pytest

from conftest import blocked, module_available

RUN = "runQa"
STAGES = "discovery"


def _start(root, run_id=RUN):
    import research_lite_store as store

    return store.start_run(root, run_id, "pool", "qa gate synthetic run", "v-qa")


def _enable_search(monkeypatch, root):
    """Synthetic operator approval for the *discovery* path, no network implied:
    ``_request`` is replaced by a fake payload in every caller."""
    monkeypatch.setenv("MARKETOPS_RESEARCH_DATA_ROOT", str(root))
    monkeypatch.setenv("MARKETOPS_SEARCH_ALLOW_NETWORK", "1")
    monkeypatch.setenv("MARKETOPS_SEARCH_MAX_CALLS", "5")
    monkeypatch.setenv("SERPER_API_KEY", "qa-gate-synthetic-not-a-credential")


def _fake_provider_payload():
    return {
        "organic": [
            {"title": "Synthetic Pool", "link": "https://allowed.example.org/pool",
             "snippet": "qa gate synthetic result"},
            {"title": "Second", "link": "https://allowed.example.org/two",
             "snippet": "another synthetic result"},
        ]
    }


# --- provenance: a discovered thing is never a verified thing -------------


def test_a_search_receipt_is_content_hashed_and_marked_unverified(data_root, clean_env, monkeypatch):
    """§2.7: "source URLs, raw/derived material distinctions, provenance
    hashes ... verified/unverified state" stay first-class."""
    import dual_search_mcp as dsm

    _start(data_root)
    _enable_search(monkeypatch, data_root)
    monkeypatch.setattr(dsm, "_request", lambda *a, **k: _fake_provider_payload())
    result = dsm.search("serper", "synthetic pool london", "GB", "en",
                        run_id=RUN, operation_id="op-1", mode="web", limit=5)

    assert result["status"] == "DISCOVERED_NOT_VERIFIED"
    assert result["calls_reserved"] == 1
    receipts = list((data_root / "search-receipts" / RUN).glob("*.json"))
    assert len(receipts) == 1
    payload = json.loads(receipts[0].read_text(encoding="utf-8"))
    assert payload["source_status"] == "discovered_not_verified"
    assert payload["business_evidence"] is False
    import hashlib

    assert hashlib.sha256(receipts[0].read_bytes()).hexdigest() == result["receipt_sha256"]
    assert all("verified" not in json.dumps(hit).casefold() for hit in payload["results"])


def test_a_repeated_search_never_rewrites_its_receipt(data_root, clean_env, monkeypatch):
    """§2.7: an immutability guarantee is what makes a provenance hash worth
    anything; a rewritten receipt makes the hash a decoration.

    BEFORE this test pinned the *refusal code* (``RECEIPT_ALREADY_EXISTS``) on a
    path where the receipt table had been emptied by hand. That code stopped
    firing once the authoritative counter moved into the shared usage ledger:
    the adapter's own receipt table is no longer what refuses the replay, so the
    same correct behaviour arrived under a different name and the test reported
    a regression.

    What the contract actually requires is what is asserted here, and it does
    not depend on which layer refuses: the repeat is refused, no second receipt
    is written, and the existing receipt is byte-identical afterwards. Removing
    the refusal would show up as a changed hash or an extra file.
    """
    import hashlib

    import dual_search_mcp as dsm

    _start(data_root)
    _enable_search(monkeypatch, data_root)
    monkeypatch.setattr(dsm, "_request", lambda *a, **k: _fake_provider_payload())
    first = dsm.search("serper", "synthetic pool london", "GB", "en",
                       run_id=RUN, operation_id="op-1", mode="web", limit=5)

    receipt_dir = data_root / "search-receipts" / RUN
    before = {p.name: hashlib.sha256(p.read_bytes()).hexdigest()
              for p in receipt_dir.glob("*.json")}
    assert len(before) == 1
    assert list(before.values())[0] == first["receipt_sha256"]

    with pytest.raises(ValueError) as refused:
        dsm.search("serper", "synthetic pool london", "GB", "en",
                   run_id=RUN, operation_id="op-1", mode="web", limit=5)
    # A refusal, not an unrelated error: the closed-set replay vocabulary.
    assert any(token in str(refused.value)
               for token in ("ALREADY_RESERVED", "DUPLICATE_SEARCH_SAME_RUN",
                             "RECEIPT_ALREADY_EXISTS", "DUPLICATE_OPERATION")), (
        f"unexpected refusal vocabulary: {refused.value!r}")

    after = {p.name: hashlib.sha256(p.read_bytes()).hexdigest()
             for p in receipt_dir.glob("*.json")}
    assert after == before, (
        "the repeated search changed the receipt set: " + repr((before, after)))

    # The refusal is not merely a hash coincidence: the same contract holds when
    # the *receipt* layer is the only thing left to refuse. Emptying the
    # adapter's receipt table does not license a rewrite either.
    with sqlite3.connect(data_root / "state" / "dual-search-ledger.sqlite3") as db:
        db.execute("DELETE FROM search_attempts")
        db.commit()
    with pytest.raises(ValueError):
        dsm.search("serper", "synthetic pool london", "GB", "en",
                   run_id=RUN, operation_id="op-1", mode="web", limit=5)
    final = {p.name: hashlib.sha256(p.read_bytes()).hexdigest()
             for p in receipt_dir.glob("*.json")}
    assert final == before, (
        "an emptied receipt table allowed the receipt to be rewritten: "
        + repr((before, final)))


def test_an_mcp_tool_result_is_never_persisted_as_verified_evidence():
    """§2.3/§2.7: a successful MCP transport is not a verified business fact."""
    from types import SimpleNamespace

    import research_official_mcp as mcp

    extracted = mcp._extract(SimpleNamespace(structuredContent={"status": "completed"}))
    assert extracted["business_evidence_verified"] is False
    assert extracted["mcp_tool_completed"] is True


def test_an_artifact_readback_detects_mutated_bytes(data_root, clean_env):
    """§2.7: "extraction lineage" — the stored hash must be re-checkable."""
    import research_lite_store as store

    _start(data_root)
    store.save_artifact(data_root, RUN, "art-1", "crawl4ai_local",
                        "https://allowed.example.org/a", b"<html>one</html>",
                        "provider_extracted")
    with pytest.raises(ValueError, match="ARTIFACT"):
        store.save_artifact(data_root, RUN, "art-1", "crawl4ai_local",
                            "https://allowed.example.org/a", b"<html>two</html>",
                            "provider_extracted")


def test_an_artifact_from_another_run_cannot_be_cited(data_root, clean_env):
    """§2.7: "run ownership" — evidence is scoped to the run that produced it."""
    import research_lite_store as store

    _start(data_root)
    _start(data_root, run_id="runOther")
    store.save_artifact(data_root, "runOther", "art-other", "serper",
                        "https://allowed.example.org/x", b"material", "provider_extracted")
    with pytest.raises(ValueError, match="ARTIFACT_NOT_IN_RUN"):
        store.put_candidate(data_root, RUN, "rec-1", STAGES, "venue", "entity-x",
                            {"name": "Synthetic"}, ["art-other"])


# --- the candidate → formal boundary --------------------------------------


def test_a_candidate_write_cannot_smuggle_a_promotion_field(data_root, clean_env):
    """§2.7 / §P1-06: "Research candidates must not silently become formally
    approved records or authorized outbound contacts"."""
    import research_lite_store as store

    _start(data_root)
    for field in ("approved", "verified", "ready", "send_authorized", "reviewed_by"):
        with pytest.raises(ValueError, match="Promotion and authorization fields"):
            store.put_candidate(data_root, RUN, f"rec-{field}", STAGES, "venue",
                                "entity-x", {"name": "Synthetic", field: True})


def test_a_candidate_without_material_is_recorded_as_unverified(data_root, clean_env):
    """§2.7: an absent evidence trail is an explicit state, not a silent gap."""
    import research_lite_store as store

    _start(data_root)
    created = store.put_candidate(data_root, RUN, "rec-1", STAGES, "venue",
                                  "entity-x", {"name": "Synthetic"})
    assert created["status"] == "candidate_unverified"
    listing = store.list_run(data_root, RUN)
    assert listing["records"][0]["status"] == "candidate_unverified"
    assert listing["record_count"] == 1


def test_a_csv_export_is_labelled_a_candidate_export(data_root, clean_env):
    """§2.7: "export integrity" — the export must not read as a reviewed result."""
    import research_lite_store as store

    _start(data_root)
    store.put_candidate(data_root, RUN, "rec-1", STAGES, "venue", "entity-x",
                        {"name": "Synthetic"})
    out = data_root / "exports" / "run.csv"
    result = store.export_csv(data_root, RUN, out)
    assert result["status"] == "CANDIDATE_CSV_EXPORTED_NOT_FORMALLY_VERIFIED"
    header = out.read_text(encoding="utf-8-sig").splitlines()[0]
    assert "candidate_data_json" in header
    assert "approved" not in header.casefold()


def test_an_export_outside_the_customer_directory_is_refused(data_root, clean_env, tmp_path):
    """§2.7: material must not be written to an arbitrary caller-chosen path."""
    import research_lite_store as store

    _start(data_root)
    with pytest.raises(ValueError, match="EXPORT_OUTSIDE_CUSTOMER_APPROVED_DIRECTORY"):
        store.export_csv(data_root, RUN, tmp_path / "elsewhere.csv")


# --- converged deduplication: the part the candidate does not yet have ----


def test_a_logical_research_intent_fingerprint_exists_across_providers(data_root, clean_env):
    """§P1-03 Key A: "logical research intent fingerprint (can detect
    cross-provider potentially duplicate operations)".

    Key B (provider execution fingerprint) exists today as ``fingerprint`` in
    ``dual_search_mcp._reserve``. Key A does not: nothing in the plugin can
    recognise that the same research objective was already attempted on a
    different provider, so paid duplication across providers is undetectable.
    """
    import dual_search_mcp as dsm

    fingerprint_source = dsm._reserve.__doc__ or ""
    assert "intent" in fingerprint_source.casefold(), (
        "the reservation ledger documents only a per-provider fingerprint; no "
        "logical-intent key exists to detect the same objective re-run on "
        f"another provider (§P1-03 Key A); doc was {fingerprint_source!r}")

    import research_provider_policy as policy

    assert any("intent" in name.casefold() for name in dir(policy)), (
        "the policy plane exposes no logical-intent key (§P1-03 Key A)")


def test_deduplication_survives_a_restart_of_the_mcp_process(data_root, clean_env):
    """§P1-03: "budget reservations persist in local SQLite across restart".

    Asserted on the *files*, not on a live process, so the guarantee is that the
    constraint is durable rather than that one process remembered it.
    """
    import dual_search_mcp as dsm

    dsm._reserve(data_root, run_id=RUN, operation_id="op-1",
                 fingerprint="fp-1", provider="serper", limit=5)
    with sqlite3.connect(data_root / "state" / "dual-search-ledger.sqlite3") as db:
        row = db.execute("SELECT run_id,operation_id,state FROM search_attempts").fetchone()
    assert row == (RUN, "op-1", "reserved")
    with pytest.raises(ValueError, match="ALREADY_RESERVED|DUPLICATE_SEARCH_SAME_RUN"):
        dsm._reserve(data_root, run_id=RUN, operation_id="op-1",
                     fingerprint="fp-1", provider="serper", limit=5)


def test_cross_provider_escalation_requires_a_new_reason_not_a_silent_retry(data_root, clean_env):
    """§P1-03: "cross-provider escalation must require new reason/budget, not
    always be auto-blocked" — and, symmetrically, must never be automatic."""
    if not module_available("research_provider_policy"):
        blocked("research_provider_policy is not importable in this environment")
    import research_provider_policy as policy

    source = (policy.__file__ and open(policy.__file__, encoding="utf-8").read()) or ""
    assert "escalat" in source.casefold(), (
        "the router expresses no escalation concept, so a provider switch after a "
        "weak result cannot be distinguished from a retry (§P1-03 Key A)")
