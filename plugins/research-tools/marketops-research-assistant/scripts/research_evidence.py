#!/usr/bin/env python3
"""Compact, scene-neutral evidence audit over already saved material.

A matching excerpt proves only that words occur in a saved representation. It
never verifies meaning, a person's current role, ownership, purchasing authority,
the original HTTP response, or a formal SOP decision. No model/network calls.
"""
from __future__ import annotations

import html
import json
import re
from collections import Counter

KEY = re.compile(r"^[a-z][a-z0-9_]{0,39}$")
CHECK_KINDS = frozenset({"venue", "organization", "relationship", "person",
                         "role", "contact_point", "contact_route", "review"})
EMAIL = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")


def _norm(value: str) -> str:
    return " ".join(html.unescape(value).casefold().split())


def _text(artifact: dict) -> str:
    text = artifact.get("text", "")
    if artifact.get("kind") == "provider_extracted":
        try:
            parsed = json.loads(text)
            if isinstance(parsed, dict):
                text = parsed.get("markdown", text)
        except (ValueError, TypeError):
            pass
    if artifact.get("kind") == "rendered_html":
        # Rendered HTML is NOT the original on-the-wire HTTP response.
        text = re.sub(r"<[^>]+>", " ", text)
    return _norm(text if isinstance(text, str) else "")


def _brief_rules(records: list[dict]) -> tuple[dict, bool]:
    """Only explicit user/scene requirements: no country, sport or product rules."""
    briefs = [r for r in records if r["kind"] == "observation"
              and r["data"].get("record_type") == "research_brief"]
    if not briefs:
        return {}, False
    # The workflow itself checks supersedes; no silently selected conflicting brief.
    from research_workflow import _tip
    tip = _tip(briefs)
    if tip is None:
        return {}, False
    rules = tip["data"].get("evidence_requirements", {})
    if not isinstance(rules, dict) or len(rules) > 8:
        return {}, True
    parsed = {}
    for kind, fields in rules.items():
        if (kind not in CHECK_KINDS or not isinstance(fields, list)
                or len(fields) > 8 or len(fields) != len(set(map(str, fields)))
                or any(not isinstance(f, str) or not KEY.fullmatch(f) for f in fields)):
            return {}, True
        parsed[kind] = set(fields)
    return parsed, False


def audit_records(records: list[dict], artifacts: dict[str, dict],
                  *, result_limit: int = 20) -> dict:
    """Return tiny receipts; never surface raw pages, addresses or contact values.

    `claims` are {field, artifact_id, quote}. Quotes must match saved material,
    but matching alone never proves the semantic claim. A target field cannot
    be considered supported merely because any artifact was attached.
    """
    if not 0 <= result_limit <= 100:
        raise ValueError("INVALID_AUDIT_LIMIT")
    required, invalid_rules = _brief_rules(records)
    reasons, samples = Counter(), []
    checked = anchored_rendered = 0
    for rec in records:
        kind = rec["kind"]
        data = rec["data"]
        if kind not in CHECK_KINDS:
            continue
        # Ordinary HOLD can be supported by a factual explanation; a hard
        # EXCLUDED disposition needs a source, irrespective of the market.
        if kind == "review" and data.get("disposition") != "EXCLUDED":
            continue
        checked += 1
        ids = rec.get("artifact_ids", [])
        claims = data.get("claims", [])
        codes, matched_fields = set(), set()
        if not ids:
            codes.add("NO_SOURCE")
        if not isinstance(claims, list) or len(claims) > 12:
            codes.add("CLAIMS_INVALID")
            claims = []
        if not claims:
            codes.add("NO_EXCERPT")
        for claim in claims:
            if not isinstance(claim, dict):
                codes.add("CLAIMS_INVALID")
                continue
            field, aid, quote = (claim.get("field"), claim.get("artifact_id"),
                                  claim.get("quote"))
            if (not isinstance(field, str) or not KEY.fullmatch(field)
                    or not isinstance(aid, str) or aid not in ids or aid not in artifacts
                    or not isinstance(quote, str) or not 12 <= len(quote) <= 600):
                codes.add("CLAIMS_INVALID")
                continue
            material = artifacts[aid]
            if material.get("kind") == "decision_response":
                codes.add("DECISION_NOT_EVIDENCE")
                continue
            if _norm(quote) not in _text(material):
                codes.add("EXCERPT_NOT_FOUND")
                continue
            matched_fields.add(field)
            if material.get("kind") == "rendered_html":
                anchored_rendered += 1
            else:
                codes.add("DERIVED_EXCERPT_ONLY")
        mandatory = set(required.get(kind, set()))
        if kind in {"venue", "organization"}:
            mandatory.add("identity")
        if kind == "relationship":
            mandatory.add("relationship")
        if kind == "review":
            mandatory.add("exclusion")
        for field in mandatory - matched_fields:
            codes.add("MISSING_REQUIRED_" + field.upper())
        if kind == "contact_point":
            value = data.get("value", data.get("email"))
            channel = str(data.get("type", data.get("channel", ""))).casefold()
            if channel == "email":
                if not isinstance(value, str) or not EMAIL.fullmatch(value):
                    codes.add("EMAIL_INVALID")
                elif not any(
                    a in artifacts and artifacts[a].get("kind") == "rendered_html"
                    and re.search(r"(?<![\w.+-])" + re.escape(value) + r"(?![\w.+-])",
                                  html.unescape(artifacts[a].get("text", "")), re.I)
                    for a in ids
                ):
                    codes.add("EMAIL_NOT_IN_RENDERED_HTML")
        # Quote availability is distinct from a factual interpretation. Even a
        # record with no codes is still candidate-only, awaiting meaning review.
        for code in sorted(codes):
            reasons[code] += 1
        if codes and len(samples) < result_limit:
            samples.append({"record_id": rec["record_id"], "kind": kind,
                            "reason_codes": sorted(codes)})
    return {"status": "EVIDENCE_GAPS" if reasons or invalid_rules else "QUOTES_PRESENT_NOT_FACT_VERIFIED",
            "record_count_checked": checked, "rendered_quote_matches": anchored_rendered,
            "gap_counts": dict(sorted(reasons.items())),
            "invalid_brief_evidence_requirements": invalid_rules,
            "sample_limit": result_limit, "samples": samples,
            "facts_verified": False, "formal_sop_approved": False,
            "warning": "Even rendered HTML is not raw HTTP; excerpt matching is not semantic verification."}
