"""Per-stage scoring.

Every function here answers one question: given the gold the case author wrote
and the output the adapter returned, what did the run get right? Nothing here
calls a provider, reads the clock or looks at latency; that is the runner's
job. Keeping scoring pure is what makes "same dataset, three repeats, identical
gold metrics" possible.

Matching is deliberately conservative:

  * a person counts only when both name and role are present;
  * an email counts only when the exact address is present;
  * an attribute counts when its normalised tokens appear in some output string.

None of these look at a language model's opinion. A plausible-sounding answer
that is not in the gold scores zero, which is the behaviour a research pipeline
needs.
"""

from __future__ import annotations

import math
import re
import statistics
import unicodedata
from typing import Any, Iterable

# ---------------------------------------------------------------------------
# Normalisation helpers
# ---------------------------------------------------------------------------

_WS = re.compile(r"\s+")
_NONWORD = re.compile(r"[^\w@.+-]+", re.UNICODE)


def norm_text(value: str) -> str:
    value = unicodedata.normalize("NFKC", value).lower()
    value = _NONWORD.sub(" ", value)
    return _WS.sub(" ", value).strip()


def norm_url(url: str) -> str:
    from urllib.parse import urlparse

    parsed = urlparse(url.strip().lower())
    host = parsed.netloc
    if host.startswith("www."):
        host = host[4:]
    path = parsed.path.rstrip("/")
    return host + path


def norm_domain(url: str) -> str:
    from urllib.parse import urlparse

    return urlparse(url.strip().lower()).netloc.replace("www.", "", 1)


def _contains(haystack: str, needle: str) -> bool:
    """Normalised substring containment, for markers and attribute phrases."""
    a, b = norm_text(haystack), norm_text(needle)
    return bool(b) and b in a


def _phrase_in(haystack: str, needle: str) -> bool:
    """Whole-word containment: ``en`` must not match inside ``independent``.

    Brief attributes are short (country codes, ``de``) so raw substring
    containment credits a phrase no reader would call covered.
    """
    a, b = norm_text(haystack), norm_text(needle)
    return bool(b) and f" {b} " in f" {a} "


def _ratio(hit: int, total: int) -> float | None:
    return round(hit / total, 4) if total else None


def _mean(values: Iterable[float | None]) -> float | None:
    vals = [v for v in values if v is not None]
    return round(statistics.mean(vals), 4) if vals else None


# ---------------------------------------------------------------------------
# Search
# ---------------------------------------------------------------------------


def search_metrics(case: dict, output: dict) -> dict:
    gold = case["gold"]
    results = output.get("results") or []
    expected_domains = {d.lower().replace("www.", "", 1) for d in gold.get("expected_domains", [])}
    expected_urls = {norm_url(u) for u in gold.get("expected_urls", [])}
    excluded = {d.lower().replace("www.", "", 1) for d in gold.get("excluded_domains", [])}

    domains = [norm_domain(r.get("url", "")) for r in results]
    urls = [norm_url(r.get("url", "")) for r in results]
    relevant = [d in expected_domains or u in expected_urls for d, u in zip(domains, urls)]

    def precision(k: int) -> float | None:
        # An empty result set has no precision to speak of; scoring it 0.0 would
        # drag every provider down on the no-results cases, and scoring it 1.0
        # would reward returning nothing. ``correct_shape`` judges those cases.
        if not results:
            return None
        return round(sum(relevant[:k]) / k, 4)

    def recall(k: int) -> float | None:
        targets = len(expected_domains) + len(expected_urls)
        if not targets:
            return None
        hit_d = {d for d, rel in zip(domains[:k], relevant[:k]) if rel and d in expected_domains}
        hit_u = {u for u, rel in zip(urls[:k], relevant[:k]) if rel and u in expected_urls}
        return round((len(hit_d) + len(hit_u)) / targets, 4)

    first_relevant = next((i + 1 for i, rel in enumerate(relevant) if rel), None)
    kind = case["input"].get("expected_result_kind", "results")

    distinct_domains = set(domains)
    expected_hits = len(distinct_domains & expected_domains)
    excluded_hits = len(set(domains) & excluded)

    out = {
        "results": len(results),
        "precision_at_5": precision(5),
        "precision_at_10": precision(10),
        "recall_at_10": recall(10),
        "mrr": round(1 / first_relevant, 4) if first_relevant else 0.0,
        "domain_hit_rate": _ratio(expected_hits, len(expected_domains)),
        "unique_discovery_rate": _ratio(len(distinct_domains - excluded), len(distinct_domains)),
        "excluded_hits": excluded_hits,
        "duplicate_rate": _ratio(len(domains) - len(distinct_domains), len(domains)),
        "correct_shape": (
            (kind == "results" and bool(results)) or
            (kind == "no_results" and not results) or
            (kind == "irrelevant_only" and expected_hits == 0)
        ),
    }
    return out


# ---------------------------------------------------------------------------
# Fetch
# ---------------------------------------------------------------------------


def fetch_metrics(case: dict, output: dict) -> dict:
    gold = case["gold"]
    text = output.get("text") or ""
    emails = {e.lower() for e in (output.get("emails") or [])}
    people = output.get("people") or []
    haystack = norm_text(text + " " + " ".join(output.get("links") or []))

    expected_emails = [e.lower() for e in gold.get("expected_emails", [])]
    expected_persons = gold.get("expected_persons", [])
    markers = gold.get("expected_text_markers", [])
    forbidden = gold.get("forbidden_strings", [])

    marker_hits = sum(1 for m in markers if _contains(haystack, m))
    email_hits = sum(1 for e in expected_emails if e in emails or _contains(haystack, e))
    person_hits = sum(
        1 for p in expected_persons
        if _contains(haystack, p["name"]) and _contains(haystack, p["role"])
    )
    link_hits = sum(1 for link in gold.get("expected_links", []) if _contains(haystack, link))

    expected_content = case["input"]["expected_outcome"] == "content"
    min_chars = int(gold.get("min_text_chars") or 0)
    has_content = len(text) >= max(min_chars, 1)

    completeness_parts = [
        _ratio(marker_hits, len(markers)),
        _ratio(email_hits, len(expected_emails)),
        _ratio(person_hits, len(expected_persons)),
    ]
    present = [p for p in completeness_parts if p is not None]

    out = {
        "text_length": len(text),
        "marker_recall": _ratio(marker_hits, len(markers)),
        "email_recall": _ratio(email_hits, len(expected_emails)),
        "person_recall": _ratio(person_hits, len(expected_persons)),
        "link_recall": _ratio(link_hits, len(gold.get("expected_links", []))),
        "extraction_completeness": round(statistics.mean(present), 4) if present else None,
        "false_success": (not expected_content) and has_content,
        # Only meaningful where the page is *supposed* to be contentless; a
        # content case would otherwise count as an incorrect "no content".
        "correct_no_content": ((not expected_content) and (not has_content))
        if not expected_content else None,
        "forbidden_hit": any(_contains(haystack, f) for f in forbidden),
        "final_url_ok": (
            (norm_url(output.get("final_url") or "") == norm_url(case["input"].get("final_url_expected") or ""))
            if case["input"].get("final_url_expected") else None
        ),
    }
    return out


# ---------------------------------------------------------------------------
# Extraction
# ---------------------------------------------------------------------------


def extraction_metrics(case: dict, output: dict) -> dict:
    gold = case["gold"]
    people_out = output.get("people") or []
    emails_out = output.get("emails") or []
    companies_out = output.get("companies") or []

    gold_people = gold.get("people", [])
    gold_emails = gold.get("emails", [])
    gold_companies = gold.get("companies", [])

    out_names = [norm_text(p.get("name", "")) for p in people_out]
    gold_names = [norm_text(p["name"]) for p in gold_people]

    person_hits = sum(
        1 for p in gold_people
        if any(
            norm_text(p["name"]) in norm_text(o.get("name", ""))
            and norm_text(p["role"]) in norm_text(o.get("role", ""))
            for o in people_out
        )
    )
    person_hallucinations = sum(
        1 for o in people_out
        if not any(norm_text(o.get("name", "")) in g or g in norm_text(o.get("name", "")) for g in gold_names)
    )

    email_values_out = {e.get("value", "").strip().lower() for e in emails_out}
    email_hits = sum(1 for e in gold_emails if e["value"].lower() in email_values_out)
    kind_hits = sum(
        1 for e in gold_emails
        for o in emails_out
        if o.get("value", "").lower() == e["value"].lower() and o.get("kind") == e["kind"]
    )
    forbidden = {e.lower() for e in gold.get("forbidden_emails", [])}
    forbidden_hits = len(email_values_out & forbidden)

    personal_invented = 0
    if gold.get("expect_no_personal_email"):
        personal_invented = sum(1 for e in emails_out if e.get("kind") == "personal")

    company_hits = sum(
        1 for c in gold_companies
        if any(norm_text(c["name"]) in norm_text(o.get("name", "")) for o in companies_out)
    )

    dm = [p for p in gold_people if p.get("decision_maker")]
    dm_correct = sum(
        1 for p in dm
        if any(
            norm_text(p["name"]) in norm_text(o.get("name", ""))
            and bool(o.get("decision_maker")) == bool(p["decision_maker"])
            for o in people_out
        )
    )

    return {
        "company_recall": _ratio(company_hits, len(gold_companies)),
        "person_recall": _ratio(person_hits, len(gold_people)),
        "person_precision": (
            round((len(people_out) - person_hallucinations) / len(people_out), 4)
            if people_out else None
        ),
        "person_hallucinations": person_hallucinations,
        "email_recall": _ratio(email_hits, len(gold_emails)),
        "email_kind_accuracy": _ratio(kind_hits, len(gold_emails)),
        "forbidden_email_hits": forbidden_hits,
        "invented_personal_emails": personal_invented,
        "decision_maker_accuracy": _ratio(dm_correct, len(dm)),
        "gap_honesty": (
            len(set(gold.get("missing_evidence", [])) & set(output.get("gaps") or [])) /
            len(gold["missing_evidence"])
            if gold.get("missing_evidence") else None
        ),
    }


# ---------------------------------------------------------------------------
# Qualification
# ---------------------------------------------------------------------------


def qualification_metrics(case: dict, output: dict) -> dict:
    gold = case["gold"]
    predicted = output.get("qualification_status")
    evidence_ids = set(case["input"]["prospect"].get("evidence") or [])
    cited = set(output.get("supporting_evidence") or [])
    # Every verdict is an assertion about evidence and must cite a page that
    # exists -- including a negative one ("out of scope", "cannot confirm the
    # company") and one that asks for review. Only "insufficient evidence" may
    # legitimately cite nothing: that is the grounded answer, not an ungrounded
    # one. A missing/None prediction is treated as an assertion so it cannot
    # pass by citing nothing.
    positive = predicted != "insufficient_evidence"
    return {
        "status_correct": predicted == gold["qualification_status"],
        "icp_match_correct": bool(output.get("icp_match")) == bool(gold["icp_match"]),
        "relevance_error": abs(int(output.get("relevance", 0)) - int(gold["relevance"])),
        "grounded": (cited <= evidence_ids) and (bool(cited) or not positive),
        "ungrounded_citations": len(cited - evidence_ids),
        "missing_evidence_recall": _ratio(
            len(set(gold.get("missing_evidence", [])) & set(output.get("missing_evidence") or [])),
            len(gold.get("missing_evidence", [])),
        ) if gold.get("missing_evidence") else None,
    }


# ---------------------------------------------------------------------------
# Identity / dedup
# ---------------------------------------------------------------------------


def _pairs(clusters: list[list[str]]) -> set[frozenset[str]]:
    out: set[frozenset[str]] = set()
    for cluster in clusters:
        members = sorted(set(cluster))
        for i in range(len(members)):
            for j in range(i + 1, len(members)):
                out.add(frozenset((members[i], members[j])))
    return out


def identity_metrics(case: dict, output: dict) -> dict:
    gold = case["gold"]
    predicted = _pairs(output.get("clusters") or [])
    truth = _pairs(gold["clusters"])
    tp = len(predicted & truth)
    fp = len(predicted - truth)
    fn = len(truth - predicted)
    precision = tp / (tp + fp) if (tp + fp) else 1.0
    recall = tp / (tp + fn) if (tp + fn) else 1.0
    f1 = (2 * precision * recall / (precision + recall)) if (precision + recall) else 0.0

    gold_rel = {(r["parent"], r["child"], r["kind"]) for r in gold.get("relations", [])}
    pred_rel = {(r.get("parent"), r.get("child"), r.get("kind")) for r in output.get("relations") or []}
    return {
        "pair_precision": round(precision, 4),
        "pair_recall": round(recall, 4),
        "pair_f1": round(f1, 4),
        "relation_correct": pred_rel == gold_rel,
        "relation_false_positives": len(pred_rel - gold_rel),
        "relation_misses": len(gold_rel - pred_rel),
    }


# ---------------------------------------------------------------------------
# Evidence / lineage
# ---------------------------------------------------------------------------


def evidence_metrics(case: dict, output: dict) -> dict:
    gold = case["gold"]
    checks = output.get("checks") or {}
    return {
        "snapshot_exists_correct": bool(checks.get("snapshot_exists")) == bool(gold["snapshot_exists"]),
        "sha256_correct": bool(checks.get("sha256_matches")) == bool(gold["sha256_matches"]),
        "fields_traceable_recall": _ratio(
            len(set(gold.get("fields_traceable", [])) & set(checks.get("fields_traceable") or [])),
            len(gold.get("fields_traceable", [])),
        ),
        "entity_correct": checks.get("entity_ref") == gold["entity_ref"],
        "stale_detected": (
            bool(checks.get("stale")) == bool(gold.get("stale_after_days"))
            if gold.get("stale_after_days") is not None else None
        ),
        "contradictions_found": len(
            set(gold.get("contradicts", [])) & set(checks.get("contradicts") or [])
        ),
    }


# ---------------------------------------------------------------------------
# Channel
# ---------------------------------------------------------------------------


def channel_metrics(case: dict, output: dict) -> dict:
    gold = case["gold"]
    ranked = list(output.get("ranked_channels") or [])
    acceptable = set(gold["acceptable_set"])
    rejected = set(gold.get("rejected", []))

    gains = [1.0 if c in acceptable else 0.0 for c in ranked]
    dcg = sum(g / math.log2(i + 2) for i, g in enumerate(gains))
    ideal_hits = min(len(acceptable), len(ranked) or 1)
    idcg = sum(1 / math.log2(i + 2) for i in range(ideal_hits))
    return {
        "top1_correct": bool(ranked) and ranked[0] in acceptable,
        "top3_acceptable": _ratio(len([c for c in ranked[:3] if c in acceptable]), min(3, len(ranked))) if ranked else 0.0,
        "ndcg": round(dcg / idcg, 4) if idcg else None,
        "rejected_selected": len(set(ranked) & rejected),
        "order_match": ranked == list(gold["ranked_channels"]),
    }


# ---------------------------------------------------------------------------
# Brief
# ---------------------------------------------------------------------------


def brief_metrics(case: dict, output: dict) -> dict:
    gold = case["gold"]
    fields = ("icp_attributes", "personas", "pain_points", "channels", "queries", "exclusions")
    coverage: dict[str, float | None] = {}
    for field in fields:
        expected = gold.get(field) or []
        produced = output.get(field) or []
        hit = sum(1 for item in expected if any(_phrase_in(p, item) or _phrase_in(item, p) for p in produced))
        coverage[field] = _ratio(hit, len(expected))
    produced_all = " ".join(
        str(p) for field in fields + ("forbidden_claims",) for p in (output.get(field) or [])
    )
    leaked = sum(1 for claim in gold.get("forbidden_claims", []) if _contains(produced_all, claim))
    present = [v for v in coverage.values() if v is not None]
    return {
        "coverage": coverage,
        "overall_coverage": round(statistics.mean(present), 4) if present else None,
        "forbidden_claims_leaked": leaked,
    }


# ---------------------------------------------------------------------------
# E2E
# ---------------------------------------------------------------------------


def e2e_metrics(case: dict, output: dict) -> dict:
    gold = case["gold"]
    prospects = output.get("prospects") or []
    by_company = {norm_text(p.get("company", "")): p for p in prospects}

    expected = gold["expected_prospects"]
    status_correct = 0
    person_correct = 0
    email_correct = 0
    found = 0
    fabricated_claims = 0

    forbidden = [norm_text(c) for c in gold.get("forbidden_claims", [])]
    for want in expected:
        got = by_company.get(norm_text(want["company"]))
        if got is None:
            continue
        found += 1
        status_correct += int(got.get("status") == want["expected_status"])
        person_correct += int(bool(got.get("person")) == bool(want["has_person"]))
        email_correct += int(bool(got.get("email")) == bool(want["has_email"]))
        # A claim is fabricated when the pipeline states a person/email that the
        # synthetic corpus never contains. The corpus is the only authority.
        if got.get("person") and not want["has_person"]:
            fabricated_claims += 1
        if got.get("email") and not want["has_email"]:
            fabricated_claims += 1

    blob = norm_text(" ".join(str(p) for p in prospects))
    forbidden_seen = sum(1 for c in forbidden if c and c in blob)

    evidence_ok = sum(
        1 for p in prospects
        if p.get("evidence") and all(isinstance(e, str) and e for e in p["evidence"])
    )
    gaps = norm_text(" ".join(output.get("gaps") or []))
    gaps_expected = gold.get("expected_gaps", [])

    return {
        "prospects_expected": len(expected),
        "prospects_found": found,
        "prospect_recall": _ratio(found, len(expected)),
        "status_accuracy": _ratio(status_correct, found),
        "person_presence_accuracy": _ratio(person_correct, found),
        "email_presence_accuracy": _ratio(email_correct, found),
        "evidence_coverage": _ratio(evidence_ok, len(prospects)),
        "fabricated_claims": fabricated_claims,
        "forbidden_claims_seen": forbidden_seen,
        "gap_honesty": _ratio(
            sum(1 for g in gaps_expected if _contains(gaps, g)), len(gaps_expected)
        ),
        "dossier_complete": bool(output.get("dossier", {}).get("sections")),
    }


# ---------------------------------------------------------------------------
# Dispatch and aggregation
# ---------------------------------------------------------------------------

SCORERS = {
    "brief": brief_metrics,
    "search": search_metrics,
    "fetch": fetch_metrics,
    "extraction": extraction_metrics,
    "qualification": qualification_metrics,
    "identity": identity_metrics,
    "evidence": evidence_metrics,
    "channel": channel_metrics,
    "e2e": e2e_metrics,
}

#: Metrics that are summed (counts) rather than averaged.
_COUNT_METRICS = {
    "results", "excluded_hits", "person_hallucinations", "forbidden_email_hits",
    "invented_personal_emails", "ungrounded_citations", "relation_false_positives",
    "relation_misses", "contradictions_found", "rejected_selected",
    "forbidden_claims_leaked", "fabricated_claims", "forbidden_claims_seen",
    "prospects_expected", "prospects_found",
}

#: Booleans are reported as a rate over scored rows.
_BOOL_METRICS = {
    "correct_shape", "false_success", "correct_no_content", "forbidden_hit",
    "final_url_ok", "status_correct", "icp_match_correct", "grounded",
    "relation_correct", "snapshot_exists_correct", "sha256_correct",
    "entity_correct", "stale_detected", "top1_correct", "order_match",
    "dossier_complete",
}


def evaluate(stage: str, case: dict, output: dict) -> dict:
    return SCORERS[stage](case, output)


def aggregate(stage: str, evaluations: list[dict]) -> dict:
    """Aggregate scored rows. Nested dicts (brief coverage) are averaged per key."""
    flat: dict[str, list[Any]] = {}
    nested: dict[str, dict[str, list[float]]] = {}

    for ev in evaluations:
        for key, value in ev.items():
            if isinstance(value, dict):
                bucket = nested.setdefault(key, {})
                for sub, subvalue in value.items():
                    if isinstance(subvalue, (int, float)) and not isinstance(subvalue, bool):
                        bucket.setdefault(sub, []).append(float(subvalue))
            elif isinstance(value, bool):
                flat.setdefault(key, []).append(value)
            elif isinstance(value, (int, float)):
                flat.setdefault(key, []).append(value)

    out: dict[str, Any] = {}
    for key, values in flat.items():
        if key in _BOOL_METRICS:
            out[key] = round(sum(1 for v in values if v) / len(values), 4)
        elif key in _COUNT_METRICS:
            out[key] = int(sum(values))
        else:
            out[key] = round(statistics.mean(values), 4)
    for key, buckets in nested.items():
        out[key] = {sub: round(statistics.mean(vals), 4) for sub, vals in sorted(buckets.items())}
    out["scored_cases"] = len(evaluations)
    return out
