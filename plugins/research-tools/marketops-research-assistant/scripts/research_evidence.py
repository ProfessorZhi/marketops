#!/usr/bin/env python3
"""Compact, scene-neutral evidence audit over already saved material.

A matching excerpt proves only that words occur in a saved representation. It
never verifies meaning, a person's current role, ownership, purchasing authority,
the original HTTP response, or a formal SOP decision. No model/network calls.
"""
from __future__ import annotations

import html
import hashlib
import json
import re
from contextlib import closing
from pathlib import Path
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
    if artifact.get("kind") in {"rendered_html", "raw_response", "provider_html"}:
        # Rendered HTML is NOT the original on-the-wire HTTP response.
        text = re.sub(r"(?is)<(?:script|style)[^>]*>.*?</(?:script|style)>", " ", text)
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
    checked = anchored_rendered = anchored_vendor_html = 0
    normalized_sources = {aid: _text(a) for aid, a in artifacts.items()}
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
            if _norm(quote) not in normalized_sources[aid]:
                codes.add("EXCERPT_NOT_FOUND")
                continue
            matched_fields.add(field)
            if material.get("kind") in {"rendered_html", "raw_response"}:
                anchored_rendered += 1
            elif material.get("kind") == "provider_html":
                anchored_vendor_html += 1
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
                    a in artifacts and artifacts[a].get("kind") in {"rendered_html", "raw_response", "provider_html"}
                    # A sentence-ending dot is punctuation; a dot followed by
                    # another address/domain character is a continuation.
                    # Compare the original bytes/text without trimming either.
                    and re.search(r"(?<![\w.+-])" + re.escape(value) + r"(?![\w+-]|\.[\w+-])",
                                  html.unescape(re.sub(
                                      r"(?is)<(?:script|style)[^>]*>.*?</(?:script|style)>",
                                      " ", artifacts[a].get("text", ""))), re.I)
                    for a in ids
                ):
                    codes.add("EMAIL_NOT_IN_PRIMARY_MATERIAL")
        # Quote availability is distinct from a factual interpretation. Even a
        # record with no codes is still candidate-only, awaiting meaning review.
        for code in sorted(codes):
            reasons[code] += 1
        if codes and len(samples) < result_limit:
            samples.append({"record_id": rec["record_id"], "kind": kind,
                            "reason_codes": sorted(codes)})
    return {"status": "EVIDENCE_GAPS" if reasons or invalid_rules else "QUOTES_PRESENT_NOT_FACT_VERIFIED",
            "record_count_checked": checked, "rendered_quote_matches": anchored_rendered,
            "vendor_html_quote_matches": anchored_vendor_html,
            "vendor_html_boundary": "Vendor-cleaned semantic HTML, not original HTTP, browser DOM or business verification.",
            "gap_counts": dict(sorted(reasons.items())),
            "invalid_brief_evidence_requirements": invalid_rules,
            "sample_limit": result_limit, "samples": samples,
            "facts_verified": False, "formal_sop_approved": False,
            "warning": "Even rendered HTML is not raw HTTP; excerpt matching is not semantic verification."}


def excerpts(root: Path | str, run_id: str, artifact_ids: list[str],
             queries: list[str] | None = None) -> dict:
    """Read only tiny local evidence snippets, no full docs in model context.

    Grounded on SHA-checked saved bytes. Up to 3 artifacts, 3 short queries,
    2 hits per query and at most 1200 visible characters across the response.
    """
    import research_lite_store as store
    from research_event_journal import _root

    store._id(run_id, "run_id")
    if (not isinstance(artifact_ids, list) or not 1 <= len(artifact_ids) <= 3
            or any(not isinstance(a, str) for a in artifact_ids)
            or len(artifact_ids) != len(set(artifact_ids))):
        # The caller is an Agent: the code alone leaves it guessing, and a second
        # guess costs a turn. Say which rule, how many arrived, what is allowed.
        raise ValueError("EXCERPT_ARTIFACT_LIMIT: pass 1-3 distinct artifact IDs; got "
                         + str(len(artifact_ids) if isinstance(artifact_ids, list) else 1))
    queries = queries or []
    if not isinstance(queries, list):
        raise ValueError("EXCERPT_QUERY_LIMIT: queries must be a list of at most 3 strings")
    if len(queries) > 3:
        raise ValueError("EXCERPT_QUERY_LIMIT: at most 3 queries per call; got "
                         + str(len(queries)) + " (split into several calls)")
    for index, query in enumerate(queries, start=1):
        if not isinstance(query, str) or not 2 <= len(query) <= 60:
            raise ValueError(
                "EXCERPT_QUERY_TERM_LENGTH: query " + str(index) + " is "
                + str(len(query) if isinstance(query, str) else type(query).__name__)
                + "; each query must be 2-60 characters")
    base = _root(root)
    outputs, remaining = [], 1200
    with closing(store._connection(base)) as db:
        for aid in artifact_ids:
            store._id(aid, "artifact_id")
            row = db.execute(
                "SELECT material_kind,content_sha256,relative_path FROM artifacts "
                "WHERE run_id=? AND artifact_id=?", (run_id, aid)
            ).fetchone()
            if row is None:
                raise ValueError("EXCERPT_ARTIFACT_NOT_IN_RUN")
            target = (base / row[2]).resolve()
            if not target.is_relative_to((base / "artifacts").resolve()):
                raise ValueError("EXCERPT_PATH_OUTSIDE_ROOT")
            source = target.read_bytes()
            if hashlib.sha256(source).hexdigest() != row[1]:
                raise ValueError("EXCERPT_SOURCE_HASH_MISMATCH")
            text = source.decode("utf-8", errors="replace")
            if row[0] == "provider_extracted":
                try:
                    parsed = json.loads(text)
                    if isinstance(parsed, dict):
                        text = parsed.get("markdown", text)
                except ValueError:
                    pass
            if row[0] in {"rendered_html", "provider_html"}:
                text = html.unescape(re.sub(r"<[^>]+>", " ", text))
            text = " ".join(text.split())
            clips = []
            for query in queries or [""]:
                start = 0
                for _ in range(2 if query else 1):
                    pos = text.casefold().find(query.casefold(), start) if query else 0
                    if pos < 0 or remaining <= 0:
                        break
                    left, right = max(0, pos - 100), min(len(text), pos + max(180, len(query) + 100))
                    clip = text[left:right][:remaining]
                    clips.append(clip)
                    remaining -= len(clip)
                    start = pos + max(1, len(query))
            outputs.append({"artifact_id": aid, "material_kind": row[0],
                            "sha256": row[1], "snippets": clips,
                            "query_hits": len(clips)})
    return {"status": "SAVED_MATERIAL_EXCERPTS_NOT_FACT_VERIFIED",
            "results": outputs, "total_char_limit": 1200,
            "untrusted_source_content": True}
