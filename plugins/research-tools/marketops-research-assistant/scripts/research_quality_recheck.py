#!/usr/bin/env python3
"""Re-check a published scorecard independently of the scorer that produced it.

A scorecard is only worth publishing if someone else can tie it to the artefacts it
came from. This module does that in two passes, and they catch different things:

* **Pass 1 — recompute.** Score the same inputs again and compare. This catches a
  document that was edited after the fact, and names the differing key rather than
  declaring the whole thing wrong. It cannot catch a bug in the scorer, because it
  runs the scorer.
* **Pass 2 — re-derive acceptance.** Work out from the *stored bytes* which rows
  should have been accepted, with its own short implementation that shares no code
  with the scorer's acceptance rule, and compare with the scorecard's flags. This is
  the pass that catches the scorer being wrong, and it deliberately does not import
  the scorer's decision path.

Either pass finding something makes the verdict `DRIFT`. Pass 2 being unable to read
the artefacts at all makes it `BLOCKED`: "could not check" must never read as "agreed".
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import research_quality_scorecard as scorer

STATUSES = ("AGREE", "DRIFT", "BLOCKED")

#: Field names this module uses for its own findings. Deliberately *not* the scorer's
#: vocabulary: two implementations that share a code space stop being independent.
QUOTED_FIELDS = ("organisation_name", "venue_address_public", "official_contact_route")


class RecheckBlocked(RuntimeError):
    """The artefacts needed to re-check were not readable. Not an agreement."""


def rederive_acceptance(inputs: dict) -> dict:
    """Which rows the stored bytes actually support.

    Deliberately simpler than the scorer: this reads each cited file, hashes it,
    looks for the quotation in the raw text and checks that the field's value occurs
    inside that quotation. It does no HTML handling and no domain reasoning, so a bug
    in those parts of the scorer cannot reproduce itself here.
    """
    lineage = {record["artifact_id"]: record for record in inputs.get("lineage") or []}
    artifacts_dir = (inputs.get("paths") or {}).get("artifacts_dir")
    if not artifacts_dir or not Path(artifacts_dir).is_dir():
        raise RecheckBlocked("RECHECK_ARTIFACT_BYTES_UNAVAILABLE: "
                             f"{artifacts_dir or '<not supplied>'}")
    artifacts_dir = Path(artifacts_dir)
    brief = inputs.get("brief") or {}
    required = (brief.get("input", {}).get("required_fields_per_target")
                or ("organisation_name", "venue_address_public", "official_domain",
                    "official_contact_route", "source_url", "source_excerpt_sha256"))

    accepted: list[str] = []
    reason_codes: dict[str, list[str]] = {}
    for row in inputs.get("rows") or []:
        ref = str(row.get("row_id"))
        fields = row.get("fields") or {}
        codes: list[str] = []
        if row.get("source_grade") not in {"A", "B"}:
            codes.append("GRADE_NOT_EVIDENCE")
        for name in required:
            if name not in fields:
                codes.append(f"MISSING_FIELD:{name}")
        covered: set[str] = set()
        for claim in row.get("claims") or []:
            artifact_id = str(claim.get("artifact_id", ""))
            record = lineage.get(artifact_id)
            if record is None:
                codes.append("CLAIM_ARTIFACT_NOT_IN_LINEAGE")
                continue
            path = artifacts_dir / f"{artifact_id}.bin"
            if not path.is_file():
                codes.append("ARTIFACT_BYTES_UNAVAILABLE")
                continue
            raw = path.read_bytes()
            if hashlib.sha256(raw).hexdigest() != record.get("content_sha256"):
                codes.append("ARTIFACT_BYTES_TAMPERED")
                continue
            text = " ".join(raw.decode("utf-8", errors="replace").split())
            if scorer.looks_like_challenge(raw):
                codes.append("CHALLENGE_MARKERS_IN_BYTES")
            quote = " ".join(str(claim.get("quote", "")).split())
            if not quote or quote not in text:
                codes.append("EXCERPT_NOT_IN_STORED_MATERIAL")
                continue
            value = fields.get(claim.get("field"))
            if isinstance(value, str) and value and value not in quote:
                codes.append("EXCERPT_LACKS_VALUE")
                continue
            covered.add(str(claim.get("field")))
        for name in QUOTED_FIELDS:
            if name in fields and name not in covered:
                codes.append(f"UNCOVERED_FIELD:{name}")
        reason_codes[ref] = sorted(set(codes))
        if not codes:
            accepted.append(ref)
    return {"accepted": accepted, "reason_codes": reason_codes}


def compare(scorecard: dict, recomputed: dict) -> list[str]:
    """The differing key paths, not "the documents differ"."""
    differences: list[str] = []
    for key in ("verdict", "verdict_reason"):
        if scorecard.get(key) != recomputed.get(key):
            differences.append(key)
    for key in ("failed", "blocked", "passed"):
        if (scorecard.get("zero_tolerance") or {}).get(key) != \
                (recomputed.get("zero_tolerance") or {}).get(key):
            differences.append(f"zero_tolerance.{key}")
    for name in sorted(set(recomputed.get("measurements") or {})
                       | set(scorecard.get("measurements") or {})):
        if (scorecard.get("measurements") or {}).get(name) != \
                (recomputed.get("measurements") or {}).get(name):
            differences.append(f"measurements.{name}")
    was = {r["row_ref"]: r for r in scorecard.get("rows") or []}
    now = {r["row_ref"]: r for r in recomputed.get("rows") or []}
    for ref in sorted(set(was) | set(now)):
        if was.get(ref) != now.get(ref):
            differences.append(f"rows.{ref}")
    if (scorecard.get("issues") or []) != (recomputed.get("issues") or []):
        differences.append("issues")
    return differences


def recheck(*, scorecard: dict, inputs: dict) -> dict:
    """Pass 1 and pass 2 over one published scorecard."""
    recomputed = scorer.score(**inputs)
    differences = compare(scorecard, recomputed)
    try:
        rederived = rederive_acceptance(inputs)
    except RecheckBlocked as exc:
        return {"status": "BLOCKED", "differences": differences,
                "pass2": {"status": "BLOCKED", "accepted_rederived": [],
                          "disagreements": []},
                "excuse": str(exc)}
    claimed = sorted(r["row_ref"] for r in scorecard.get("rows") or [] if r.get("accepted"))
    rederived_sorted = sorted(rederived["accepted"])
    disagreements = sorted(set(claimed) ^ set(rederived_sorted))
    pass2 = {"status": "PASS" if not disagreements else "FAIL",
             "accepted_rederived": rederived_sorted,
             "disagreements": disagreements}
    status = "DRIFT" if (differences or disagreements) else "AGREE"
    return {"status": status, "differences": differences, "pass2": pass2}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--scorecard", required=True)
    parser.add_argument("--brief", required=True)
    parser.add_argument("--rubric", required=True)
    parser.add_argument("--checks", required=True)
    parser.add_argument("--rows", required=True)
    parser.add_argument("--lineage", required=True)
    parser.add_argument("--artifacts-dir", required=True)
    parser.add_argument("--targets")
    parser.add_argument("--reviews")
    parser.add_argument("--ledger")
    parser.add_argument("--receipts")
    args = parser.parse_args(argv)
    paths = {"brief": args.brief, "rubric": args.rubric, "checks": args.checks,
             "rows": args.rows, "lineage": args.lineage,
             "artifacts_dir": args.artifacts_dir, "targets": args.targets,
             "reviews": args.reviews, "ledger": args.ledger, "receipts": args.receipts}
    scorecard = json.loads(Path(args.scorecard).read_text(encoding="utf-8"))
    verdict = recheck(scorecard=scorecard, inputs=scorer.inputs_from_paths(paths))
    print(json.dumps(verdict, ensure_ascii=False, indent=2, sort_keys=True))
    return {"AGREE": 0, "DRIFT": 1, "BLOCKED": 2}[verdict["status"]]


if __name__ == "__main__":
    raise SystemExit(main())
