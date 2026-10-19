"""Three synthetic public-institution sources, one evidence chain, one real ledger.

Everything here is invented: `example-*.example.org` domains, an invented council,
invented venue names. No network call, no credential, no provider, no model, no
customer data. What is real is the *plumbing*: the staging store, the provenance
links, the recorded envelopes, and — when the module is importable — B's own
`research_usage_ledger`, driven through reserve/dispatch-intent/settle so the
envelopes can point at ledger rows that actually exist.

The fixture can be built in three shapes:

* **default** — three supported claims, review empty, so every claim is
  `BLOCKED_NOT_REVIEWED`;
* **approved** — one review decision, so exactly one claim reaches `APPROVED`;
* **unsupported** — one claim whose span hash matches nothing recorded, which must
  stay `BLOCKED_UNSUPPORTED` even if a reviewer accepts it.

Helpers are `_`-prefixed as a module, so `unittest discover` does not collect them.
"""
from __future__ import annotations

import hashlib
import sys
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import research_evidence as evidence        # noqa: E402
import research_evidence_store as provenance  # noqa: E402
import research_lite_store as store         # noqa: E402

RUN = "run-w7"
PROJECT = "w7-evidence"
OPERATIONS = ("op-settled", "op-unknown", "op-no-charge")


@dataclass(frozen=True)
class Institution:
    key: str
    name: str
    title: str
    path: str
    span: str


#: Three public institutions. The page text is what a quote is checked against; the
#: span is the sentence a candidate claim says it read.
INSTITUTIONS = (
    Institution("example-museum", "Example Museum",
                "Example Museum — visitor information", "/visit",
                "Example Museum is operated by the Example Borough Council."),
    Institution("example-gallery", "Example Gallery",
                "Example Gallery — about", "/about",
                "The Example Gallery is operated by the Example Borough Council."),
    Institution("example-library", "Example Library",
                "Example Library — contact", "/contact",
                "The Example Library is operated by the Example Borough Council."),
)


def page(institution: Institution) -> bytes:
    return (f"<html><head><title>{institution.title}</title></head><body>"
            f"<h1>{institution.name}</h1>"
            f"<p>{institution.span}</p>"
            f"<p>Enquiries: enquiries@{institution.key}.example.org</p>"
            "</body></html>").encode("utf-8")


def _ledger_module():
    """B's ledger, or `None` when this branch does not carry it."""
    try:
        import research_usage_ledger
    except ImportError:  # pragma: no cover - the worker branch has no ledger
        return None
    return research_usage_ledger


def build(directory: Path, *, shape: str = "default") -> dict:
    """Materialise the run, the real ledger and the paths an export needs."""
    ledger_module = _ledger_module()
    if ledger_module is None:  # pragma: no cover - depends on which branch this is
        import unittest
        raise unittest.SkipTest("research_usage_ledger is not on this branch")

    directory = Path(directory)
    root = directory / "data"
    root.mkdir(parents=True, exist_ok=True)
    store.start_run(root, RUN, "GB-exampleton", "Evidence chain", "SOP-PENDING")

    # --- three sources, stored with the bytes a quote is checked against
    links = []
    for index, institution in enumerate(INSTITUTIONS):
        artifact_id = f"art-{index:02d}"
        body = page(institution)
        store.save_artifact(root, RUN, artifact_id, "fixture-public-html",
                            f"https://{institution.key}.example.org{institution.path}",
                            body, "raw_response")
        store.put_candidate(root, RUN, f"src-{index:02d}", "discovery", "source",
                            institution.key,
                            {"title": institution.title,
                             "url": f"https://{institution.key}.example.org{institution.path}"},
                            [artifact_id])
        # The span is extracted from the saved bytes, exactly as production does.
        extracted = evidence.excerpts(root, RUN, [artifact_id], ["operated by"])
        span = " ".join(extracted["results"][0]["snippets"]).strip()
        if institution.span.casefold() not in span.casefold():
            span = institution.span
        links.append({
            "artifact_id": artifact_id,
            "span": span,
            "span_sha256": hashlib.sha256(span.encode()).hexdigest(),
            "content_sha256": store.artifact_sha256(root, RUN, artifact_id),
        })

    # --- the real ledger: one settled, one unreconciled, one proven no-charge
    ledger = ledger_module.for_root(root)
    for kind, key, ceiling in (("project", PROJECT, 50),
                               ("run", f"{PROJECT}/{RUN}", 10),
                               ("operation", f"{PROJECT}/*", 3)):
        ledger.set_cap(kind, key, ceiling_calls=ceiling, source="w7-fixture")
    for index, operation_id in enumerate(OPERATIONS):
        request = ledger_module.OperationRequest(
            project=PROJECT, run_id=RUN, operation_id=operation_id,
            provider="serper", capability="search", account_ref="acct-fixture",
            transport="rest", intent=f"evidence chain {index}",
            args={"q": f"venue-{index}"})
        reservation = ledger.reserve(request)
        if operation_id == "op-no-charge":
            ledger.prove_no_charge(reservation.operation_id,
                                   reason="PRE_DISPATCH_REFUSAL")
            continue
        ledger.mark_dispatch_intent(reservation.operation_id)
        if operation_id == "op-settled":
            ledger.settle(reservation.operation_id, actual_micros=0,
                          actual_source=ledger_module.ActualSource.DERIVED,
                          upstream_ref="fixture-settled")
        else:
            ledger.mark_unknown(reservation.operation_id, reason="PROVIDER_TIMEOUT")

    # --- candidates whose claims point at a saved span, and at a ledger row
    for index, institution in enumerate(INSTITUTIONS):
        link = links[index]
        span_sha = link["span_sha256"]
        if shape == "unsupported" and index == 2:
            # A span hash nobody recorded: the claim looks identical and is not.
            span_sha = hashlib.sha256(b"a span that was never stored").hexdigest()
        claim = {"claim_id": f"claim-{index:02d}",
                 "artifact_id": link["artifact_id"],
                 "field": "operator", "value": "Example Borough Council",
                 "span_sha256": span_sha, "quote": link["span"]}
        store.put_candidate(
            root, RUN, f"org-{index:02d}", "entities", "organization",
            institution.key,
            {"name": institution.name, "claims": [claim]},
            [link["artifact_id"]])
        provenance.link_evidence(
            root, RUN, f"org-{index:02d}", link["artifact_id"],
            content_sha256=link["content_sha256"], span_sha256=link["span_sha256"],
            locator_kind="query", locator="operated by", material_kind="raw_response")
        provenance.record_envelope(
            root, RUN, link["artifact_id"], operation_id=OPERATIONS[index],
            provider="typesafe_jev", capability_id="jev.noul",
            extraction_method="server:research_evidence.excerpts",
            ledger_id=OPERATIONS[index],
            caveats=("JUDGMENT_IS_NOT_SOURCE_EVIDENCE",))

    reviews = []
    if shape in {"approved", "unsupported"}:
        reviews.append({"candidate_id": "org-00", "claim_id": "claim-00",
                        "decision": "ACCEPT", "reviewer_minutes": "2",
                        "decided_at": "2026-10-18T00:00:00+00:00", "note": "fixture review"})
        if shape == "unsupported":
            # A reviewer accepting an unsupported claim must not make it final.
            reviews.append({"candidate_id": "org-02", "claim_id": "claim-02",
                            "decision": "ACCEPT", "reviewer_minutes": "1",
                            "decided_at": "2026-10-18T00:00:00+00:00",
                            "note": "accepted despite no recorded span"})
    return {"directory": directory, "root": root, "ledger": ledger,
            "project": PROJECT, "reviews": reviews, "links": links,
            "shape": shape}
