"""A deterministic, offline run for the quality scorecard. No network, no provider.

Twelve targets, walked through the whole grading pipeline on purpose: every rubric
grade A..F appears, every zero-tolerance check that *can* be decided offline is
exercised by at least one row, and the cost ledger carries one confirmed charge, one
unreconciled charge and one blocked attempt so the "unknown is not zero" rule has
something real to bite on.

Names, URLs and addresses are synthetic and use `example.org` / `example.gov.uk`
only, exactly as the repository's other fixtures do. Nothing here stands for a real
organisation and nothing here is evidence about the world.

The builder is a test helper, not a test: the leading underscore keeps
`unittest discover` from collecting it, matching `qa_gate/_reserve_worker.py`.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

#: Two pages, because a grade A and a grade B row have to cite different domains and
#: the "official domain" question is the whole point of the rubric.
OWN_SITE_PAGE = (
    "<html><body><h1>Example Arts Centre</h1>"
    "<p>Operated by the Example Borough Council.</p>"
    "<p>Address: 1 Example Street, Exampleton</p>"
    "<p>Enquiries: enquiries@example.org</p>"
    "<p>Director: A. Person</p></body></html>"
)
PARENT_BODY_PAGE = (
    "<html><body><h1>Example Borough Council cultural venues</h1>"
    "<p>The Council operates the Example Arts Centre and the Example Museum.</p>"
    "</body></html>"
)
DIRECTORY_PAGE = (
    "<html><body><h1>Example Arts Centre - Exampleton Directory</h1>"
    "<p>Listing supplied by Exampleton Directory Ltd.</p></body></html>"
)
CHALLENGE_PAGE = (
    "<html><body><h1>Verify you are human</h1>"
    "<p>Please complete the CAPTCHA to continue.</p></body></html>"
)

PAGES = {
    "art-own-01": ("https://example.org/visit", "raw_response", OWN_SITE_PAGE),
    "art-own-02": ("https://example.gov.uk/venues", "raw_response", PARENT_BODY_PAGE),
    "art-dir-01": ("https://exampleton.example.net/arts-centre", "raw_response",
                   DIRECTORY_PAGE),
    "art-challenge-01": ("https://example.org/protected", "rendered_html", CHALLENGE_PAGE),
}

#: The excerpt each accepted claim quotes. It must occur verbatim in the cited page
#: AND contain the field's value, because that is the rubric's acceptance rule.
CLAIM_NAME = "Example Arts Centre"
CLAIM_ENQUIRIES = "Enquiries: enquiries@example.org"
CLAIM_ADDRESS = "Address: 1 Example Street, Exampleton"
CLAIM_OPERATED = "operates the Example Arts Centre"
CLAIM_DIRECTORY = "Listing supplied by Exampleton Directory Ltd."
CLAIM_CHALLENGE = "Please complete the CAPTCHA to continue."

NAME = "Example Arts Centre"
ADDRESS = "1 Example Street, Exampleton"
ENQUIRIES = "enquiries@example.org"

REQUIRED_FIELDS = ("organisation_name", "venue_address_public", "official_domain",
                   "official_contact_route", "source_url", "source_excerpt_sha256")


def _sha(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _artifact(artifact_id: str) -> dict:
    origin, kind, body = PAGES[artifact_id]
    raw = body.encode("utf-8")
    record = {
        "artifact_id": artifact_id,
        "run_id": "run-q",
        "provider": "fixture",
        "origin_url": origin,
        "material_kind": kind,
        "content_sha256": hashlib.sha256(raw).hexdigest(),
        "bytes": len(raw),
        "acquired_at": "2026-10-17T02:00:00+00:00",
        "parent_ids": [],
        "derivation": "retrieved",
        "validated": False,
    }
    if artifact_id == "art-challenge-01":
        record["challenge_suspected"] = True
        record["retrieval_decision"] = {
            "allowed": True, "checked_address": "example.org",
            "policy_source": "fixture-brief", "refusal_reason": None}
    return record


def _row(row_id: str, target_id: str, *, grade: str, domain: str,
         cited: tuple[str, ...], claims: tuple[tuple[str, str, str], ...],
         person: str | None = None, email: str | None = None,
         missing: tuple[str, ...] = (), parents: tuple[str, ...] = ()) -> dict:
    """One candidate row.

    `claims` are `(field, artifact_id, quote)` triples; the quote is what the row
    asserts the field was read from, and the scorer checks it against the stored
    bytes rather than believing it.
    """
    values = {"organisation_name": NAME, "venue_address_public": ADDRESS,
              "official_contact_route": ENQUIRIES}
    fields = {name: value for name, value in values.items() if name not in missing}
    fields["official_domain"] = domain
    fields["source_url"] = PAGES[cited[0]][0] if cited else "https://search.example.org/s"
    fields["source_excerpt_sha256"] = _sha(claims[0][2]) if claims else None
    return {
        "row_id": row_id, "target_id": target_id, "source_grade": grade,
        "fields": fields,
        "claims": [{"field": f, "artifact_id": a, "quote": q} for f, a, q in claims],
        "cited_artifact_ids": list(cited),
        "same_operator_official_domains": list(parents),
        "contact": {"person_name": person, "email": email},
    }


#: The claims a fully-supported row makes, one per quoted field.
FULL_CLAIMS = (("organisation_name", "art-own-01", CLAIM_NAME),
               ("official_contact_route", "art-own-01", CLAIM_ENQUIRIES),
               ("venue_address_public", "art-own-01", CLAIM_ADDRESS))
PARENT_CLAIMS = (("organisation_name", "art-own-02", CLAIM_OPERATED),)
PARENT_EXTRA = (("official_contact_route", "art-own-01", CLAIM_ENQUIRIES),
                ("venue_address_public", "art-own-01", CLAIM_ADDRESS))
PARENT_DOMAINS = ("example.gov.uk",)


def build_rows() -> list[dict]:
    return [
        # tgt-01..tgt-04: the organisation's own domain -> grade A.
        _row("row-01", "tgt-01", grade="A", domain="example.org",
             cited=("art-own-01",), claims=FULL_CLAIMS),
        _row("row-02", "tgt-02", grade="A", domain="example.org",
             cited=("art-own-01",), claims=FULL_CLAIMS),
        _row("row-03", "tgt-03", grade="A", domain="example.org",
             cited=("art-own-01",), claims=FULL_CLAIMS, person="A. Person"),
        _row("row-04", "tgt-04", grade="A", domain="example.org",
             cited=("art-own-01",), claims=FULL_CLAIMS, email=ENQUIRIES),
        # tgt-05..tgt-07: corroborated on the governing body's domain -> B, still
        # evidence. tgt-06 gets two rows on purpose, so duplicates are countable.
        *[_row(f"row-{n:02d}", target, grade="B", domain="example.org",
               cited=("art-own-02", "art-own-01"), claims=PARENT_CLAIMS + PARENT_EXTRA,
               parents=PARENT_DOMAINS)
          for n, target in ((5, "tgt-05"), (6, "tgt-06"), (7, "tgt-06"))],
        # tgt-07: a commercial directory. Not evidence; the row cannot be accepted.
        _row("row-08", "tgt-07", grade="D", domain="example.org",
             cited=("art-dir-01",), claims=(("organisation_name", "art-dir-01",
                                             CLAIM_DIRECTORY),)),
        # tgt-08: claims A but cites a directory page -> overclaim, misleading source.
        _row("row-09", "tgt-08", grade="A", domain="example.org",
             cited=("art-dir-01",), claims=(("organisation_name", "art-dir-01",
                                             CLAIM_DIRECTORY),)),
        # tgt-09: a snippet. It cites no retrieved page at all -> zero-tolerance AF-2.
        _row("row-10", "tgt-09", grade="E", domain="example.org", cited=(),
             claims=(("organisation_name", "art-none", CLAIM_NAME),)),
        # tgt-10: a stored challenge page cited as evidence -> AF-3.
        _row("row-11", "tgt-10", grade="A", domain="example.org",
             cited=("art-challenge-01",),
             claims=(("organisation_name", "art-challenge-01", CLAIM_CHALLENGE),)),
        # tgt-11: contact invented by the model, on no cited page -> AF-1 and AF-12.
        _row("row-12", "tgt-11", grade="A", domain="example.org",
             cited=("art-own-01",), claims=FULL_CLAIMS,
             person="B. Invented", email="not.on.the.page@example.org"),
        # tgt-12: a required field left absent -> missed_required_fields.
        _row("row-13", "tgt-12", grade="A", domain="example.org",
             cited=("art-own-01",), claims=FULL_CLAIMS,
             missing=("venue_address_public",)),
    ]


def build_reviews() -> list[dict]:
    """Human review over the rows. row-02 is rejected on re-check: a false positive."""
    return [
        {"row_id": "row-01", "decision": "ACCEPT", "reviewer_minutes": "4"},
        {"row_id": "row-02", "decision": "REJECT", "reviewer_minutes": "2",
         "reason": "address belonged to a different venue on the same page"},
        {"row_id": "row-03", "decision": "ACCEPT", "reviewer_minutes": "3"},
        {"row_id": "row-04", "decision": "ACCEPT", "reviewer_minutes": "3"},
        {"row_id": "row-05", "decision": "ACCEPT", "reviewer_minutes": "2"},
        {"row_id": "row-06", "decision": "ACCEPT", "reviewer_minutes": "2"},
        {"row_id": "row-13", "decision": "ACCEPT", "reviewer_minutes": "1"},
    ]


def build_ledger(*, with_unknown_charge: bool = True) -> dict:
    """One settled charge, one unreconciled dispatch, one blocked attempt."""
    operations = [
        {"operation_id": "op-1", "provider": "serper", "capability_id": "serper.search",
         "tier": "T2", "initiating_client": "offline_benchmark", "reserved": "1",
         "estimated": "0.0010", "derived": "0.0010", "actual": "0.0010",
         "state": "SETTLED"},
        {"operation_id": "op-2", "provider": "crawl4ai", "capability_id": "crawl4ai.local",
         "tier": "T1", "initiating_client": "offline_benchmark", "reserved": "0",
         "estimated": "0", "derived": "0", "actual": None, "state": "NO_CHARGE_PROVEN"},
        {"operation_id": "op-3", "provider": "jev", "capability_id": "jev.noul",
         "tier": "T2", "initiating_client": "offline_benchmark", "reserved": "1",
         "estimated": "0.0005", "derived": "0.0005",
         # Reconciled means the provider reported an actual. Leaving it null while
         # marking the row SETTLED would be the exact confusion the ledger forbids.
         "actual": None if with_unknown_charge else "0.0005",
         "state": "UNKNOWN_CHARGE_OUTSTANDING" if with_unknown_charge else "SETTLED"},
        {"operation_id": "op-4", "provider": "serpapi", "capability_id": "serpapi.search",
         "tier": "T2", "initiating_client": "offline_benchmark", "reserved": "0",
         "estimated": "0", "derived": "0", "actual": None, "state": "BLOCKED",
         "reason_code": "POLICY_TIER_NOT_GRANTED"},
    ]
    return {
        "schema": "marketops.qa.cost_ledger.v1", "campaign_id": "fixture-campaign",
        "providers": [{
            "provider": "serper", "account_ref": "acct-local-1", "meter": "credits",
            "b0": "500", "b0_source": "provider_api_read",
            "b0_observed_at": "2026-10-17T01:00:00+00:00", "floor": "100",
            "max_cumulative_drawdown": "400", "spendable_ceiling": "400",
            "current_available": "499", "checked_at": "2026-10-17T02:00:00+00:00",
            "meter_trust": "verified", "in_flight_or_unknown_exposure": "1",
            "external_account_spend": "0",
        }],
        "operations": operations,
        "totals": {
            "attempted": 4, "succeeded": 2, "blocked": 1, "unknown": 1,
            "usd_estimated": "0.0015", "usd_actual_confirmed": "0.0010",
            "usd_unreconciled": "0.0005", "accepted_rows": 0,
            "usd_per_accepted_row": None, "credits_per_accepted_row": None,
            "tokens_in": 0, "tokens_out": 0, "floor_breached": False,
        },
    }


def build_receipts() -> list[dict]:
    return [
        {"operation_id": "op-1", "provider": "serper", "dispatched": True},
        {"operation_id": "op-2", "provider": "crawl4ai", "dispatched": False},
        {"operation_id": "op-3", "provider": "jev", "dispatched": True},
    ]


def build_targets() -> list[dict]:
    return [{"target_id": f"tgt-{i:02d}", "name": f"Example Venue {i}",
             "geography": "Exampleton", "verification": "PENDING"}
            for i in range(1, 13)]


def build_brief() -> dict:
    return {
        "schema": "marketops.qa.research_brief.v1",
        "brief_id": "fixture-brief-1",
        "input": {
            "research_question": "Which organisations operate a public venue here?",
            "geography": {"name": "Exampleton"},
            "target_count": {"min": 10, "max": 20},
            "required_fields_per_target": list(REQUIRED_FIELDS),
            "budget": {"paid_provider_calls": "none in this stage"},
            "hard_prohibitions": ["invented person names, email addresses, phone numbers"],
        },
    }


def build_rubric() -> dict:
    """The rubric shape. The canonical copy belongs to W-QA; this is a local twin so
    these tests do not depend on a file another owner may move."""
    return {
        "schema": "marketops.qa.source_quality_rubric.v1",
        "grades": [
            {"grade": "A", "counts_as_evidence": True},
            {"grade": "B", "counts_as_evidence": True},
            {"grade": "C", "counts_as_evidence": False},
            {"grade": "D", "counts_as_evidence": False},
            {"grade": "E", "counts_as_evidence": False},
            {"grade": "F", "counts_as_evidence": False},
        ],
        "field_acceptance": {
            "rule": "accepted(field) := source.grade in {A,B} AND excerpt_sha256 is 64-hex "
                    "AND the excerpt contains the field value"},
    }


def build_checks() -> dict:
    return {
        "schema": "marketops.qa.anti_fabrication_checks.v1",
        "checks": [
            {"id": "AF-1", "reads": "candidate rows", "severity": "P0"},
            {"id": "AF-2", "reads": "row.source_grade", "severity": "P0"},
            {"id": "AF-3", "reads": "stored artefacts", "severity": "P0"},
            {"id": "AF-5", "reads": "cost ledger + provider receipts", "severity": "P0"},
            {"id": "AF-6", "reads": "cost ledger", "severity": "P0"},
            {"id": "AF-7", "reads": "ledger signatures", "severity": "P0"},
            {"id": "AF-9", "reads": "artifact-lineage records", "severity": "P0"},
            {"id": "AF-10", "reads": "every artefact shipped for review", "severity": "P0"},
            {"id": "AF-12", "reads": "candidate rows", "severity": "P0"},
        ],
        "evaluation_rule": "A check with no readable artefact is BLOCKED with the artefact "
                           "named in the excuse, and a BLOCKED check prevents a PASS verdict.",
        "verdict_rule": "Any P0 failure means the run's quality verdict is FAIL.",
    }


def write_run(directory: Path, *, tamper: str | None = None,
              with_reviews: bool = True, with_ledger: bool = True,
              with_unknown_charge: bool = True) -> dict:
    """Materialise the whole fixture and return the paths a scorer needs.

    `tamper` names one deliberate corruption, so a test can prove the scorer fails on
    it rather than trusting that it would.
    """
    directory = Path(directory)
    (directory / "artifacts").mkdir(parents=True, exist_ok=True)
    for artifact_id, (_origin, _kind, body) in PAGES.items():
        raw = body.encode("utf-8")
        if tamper == f"bytes:{artifact_id}":
            raw = raw + b"<!-- rewritten after the lineage was recorded -->"
        (directory / "artifacts" / f"{artifact_id}.bin").write_bytes(raw)

    lineage = [_artifact(a) for a in PAGES]
    if tamper == "challenge_flag":
        for record in lineage:
            record.pop("challenge_suspected", None)
    _write(directory / "lineage.json", lineage)
    _write(directory / "rows.jsonl", build_rows())
    _write(directory / "targets.jsonl", build_targets())
    _write(directory / "brief.json", build_brief())
    _write(directory / "rubric.json", build_rubric())
    _write(directory / "checks.json", build_checks())
    if with_reviews:
        _write(directory / "reviews.jsonl", build_reviews())
    if with_ledger:
        ledger, receipts = build_ledger(with_unknown_charge=with_unknown_charge), build_receipts()
        if tamper == "untracked_charge":
            # Dispatched, and at the end of the run neither settled nor held unknown.
            ledger["operations"][2]["state"] = "RESERVED"
        elif tamper == "receipt_without_ledger":
            receipts.append({"operation_id": "op-9", "provider": "serper", "dispatched": True})
        _write(directory / "ledger.json", ledger)
        _write(directory / "receipts.jsonl", receipts)
    return {
        "directory": directory,
        "artifacts_dir": directory / "artifacts",
        "lineage": directory / "lineage.json",
        "rows": directory / "rows.jsonl",
        "targets": directory / "targets.jsonl",
        "brief": directory / "brief.json",
        "rubric": directory / "rubric.json",
        "checks": directory / "checks.json",
        "reviews": directory / "reviews.jsonl",
        "ledger": directory / "ledger.json",
        "receipts": directory / "receipts.jsonl",
    }


def _write(path: Path, payload) -> None:
    if isinstance(payload, list) and path.suffix == ".jsonl":
        text = "".join(json.dumps(item, ensure_ascii=False, sort_keys=True) + "\n"
                       for item in payload)
    else:
        text = json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    path.write_text(text, encoding="utf-8")
